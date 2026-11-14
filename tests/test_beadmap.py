# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""A map from a bead video or from one image (#915, plan D1 of #890).

Locks :mod:`tether.imaging.beadmap` against the issue's acceptance criteria:

* a bead video (frame range + projection) or one image gives a map;
* on the image the present tests use (the committed ``bead_prealign_oracle.npz``
  bead crops, joined back into one frame) the map and its numbers are **equal**
  to those of the present stages -- the native ``tether extract`` path and the
  primitive chain it runs -- so no frozen number changes;
* the map file and the project store record the source id, the frame range and
  the projection;
* the quality record has the RMS residual, the number of pairs, the residual of
  each pair and a coverage value; the new limit is a PRD §11.2 tunable;
* a source with too few bead spots is refused with the count and the limit.
"""

from __future__ import annotations

import hashlib
import warnings
from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("skimage")
tifffile = pytest.importorskip("tifffile")

import numpy as np  # noqa: E402

from tether.imaging import (  # noqa: E402
    DEFAULT_MIN_BEAD_SPOTS,
    BeadMapError,
    BeadMapSource,
    OverGateRegistrationWarning,
    bead_map,
    bead_map_from_file,
    detect_spots_by_mode,
    detection_image,
    estimate_translation_prealign,
    fit_registration_map,
    load_map,
    map_quality,
    pair_control_points,
    project_frames,
    read_calibration,
    save_map,
    split_channels,
    write_calibration,
)
from tether.imaging.beadmap import _half_split  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "bead_prealign_oracle.npz"
PRD = Path(__file__).resolve().parents[1] / "docs" / "PRD.md"


@pytest.fixture(scope="module")
def bead_image() -> np.ndarray:
    """The present tests' real bead field: donor crop | acceptor crop, one frame."""
    data = np.load(FIXTURE)
    return np.hstack([data["donor"], data["acceptor"]])


def _quiet_bead_map(*args, **kwargs):
    # The real crop is a contrast-stretched display export; its residual is over
    # the 0.5 px gate, which warns. The warning is tested on its own below.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", OverGateRegistrationWarning)
        return bead_map(*args, **kwargs)


def _assert_same_map(a, b) -> None:
    for name in ("ref_to_moving", "moving_to_ref"):
        ta, tb = getattr(a, name), getattr(b, name)
        for field in ("a", "b", "norm_xy", "norm_uv"):
            assert np.array_equal(getattr(ta, field), getattr(tb, field)), f"{name}.{field}"
    assert a.rms_residual == b.rms_residual
    assert a.n_control_points == b.n_control_points
    assert a.degree == b.degree
    assert a.reference_geometry == b.reference_geometry
    assert a.moving_geometry == b.moving_geometry


# --- the present numbers do not change ---------------------------------------


def test_image_map_equals_the_present_stage_chain(bead_image: np.ndarray) -> None:
    """One image runs stages 6-9 unchanged: same coefficients, RMS and pair count
    as the primitive chain ``tether extract`` composes (split, detect, prealign,
    pair, fit)."""
    result = _quiet_bead_map(bead_image, source_id="fixture")

    h, w = bead_image.shape
    donor_geom, acceptor_geom = _half_split(w, h, "left")
    donor, acceptor = split_channels(bead_image[np.newaxis], donor_geom, acceptor_geom)
    donor_det = detection_image(donor, block=1)
    acceptor_det = detection_image(acceptor, block=1)
    donor_spots = detect_spots_by_mode(donor_det)
    acceptor_spots = detect_spots_by_mode(acceptor_det)
    prealign = estimate_translation_prealign(donor_det, acceptor_det)
    paired = pair_control_points(donor_spots, acceptor_spots, tol=2.0, prealign=prealign)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", OverGateRegistrationWarning)
        present = fit_registration_map(
            paired.reference,
            paired.moving,
            reference_channel=0,
            moving_channel=1,
            reference_geometry=donor_geom,
            moving_geometry=acceptor_geom,
        )
    _assert_same_map(result.registration_map, present)
    # Non-trivial: a real degree-2 fit on many real bead pairs.
    assert result.registration_map.degree == 2
    assert result.quality.n_pairs >= 10


def test_image_map_equals_the_native_extract_calibration(
    bead_image: np.ndarray, tmp_path: Path
) -> None:
    """End to end against the present product path: ``extract_movie`` on a movie of
    that frame stores the same calibration the bead map makes."""
    from tether.project.extract import extract_movie

    movie = tmp_path / "beads.tif"
    tifffile.imwrite(movie, np.stack([bead_image, bead_image]), photometric="minisblack")
    summary = extract_movie(movie, tmp_path / "beads.tether")
    stored = read_calibration(tmp_path / "beads.tether", summary.calibration_id)

    result = _quiet_bead_map(bead_image, source_id="fixture")
    _assert_same_map(result.registration_map, stored)
    assert summary.n_control_points == result.quality.n_pairs
    assert summary.rms_residual == result.quality.rms_residual


@pytest.mark.parametrize("projection", ["max", "mean"])
def test_video_of_one_frame_repeated_gives_the_image_map(
    bead_image: np.ndarray, projection: str
) -> None:
    video = np.stack([bead_image] * 5)
    from_image = _quiet_bead_map(bead_image, source_id="fixture")
    from_video = _quiet_bead_map(video, source_id="fixture", frames=(1, 4), projection=projection)
    _assert_same_map(from_video.registration_map, from_image.registration_map)
    assert from_video.source == BeadMapSource(
        source_id="fixture",
        kind="video",
        frame_start=1,
        frame_stop=4,
        projection=projection,  # type: ignore[arg-type]
        n_frames=5,
    )


def test_frames_outside_the_range_are_not_used(bead_image: np.ndarray) -> None:
    """Only the selected frames enter the projection: blank frames outside the
    range change nothing, and the same blank frames inside it would."""
    blank = np.zeros_like(bead_image)
    video = np.stack([blank, bead_image, bead_image, blank])
    inside = _quiet_bead_map(video, source_id="v", frames=(1, 3), projection="mean")
    reference = _quiet_bead_map(bead_image, source_id="v")
    _assert_same_map(inside.registration_map, reference.registration_map)
    assert np.array_equal(inside.image, bead_image.astype(np.float64))
    # The mean over all four frames is half the image -> a different projection.
    assert np.allclose(project_frames(video, projection="mean"), bead_image / 2.0)


def test_project_frames_max_and_mean() -> None:
    video = np.array([[[1, 5]], [[3, 2]], [[8, 0]]], dtype=np.uint16)  # (3, 1, 2)
    assert project_frames(video).tolist() == [[8.0, 5.0]]
    assert project_frames(video, frames=(0, 2), projection="max").tolist() == [[3.0, 5.0]]
    assert project_frames(video, frames=(0, 2), projection="mean").tolist() == [[2.0, 3.5]]
    assert project_frames(video).dtype == np.float64


# --- the source record is stored ---------------------------------------------


def test_source_is_recorded_in_the_map_file_and_the_store(
    bead_image: np.ndarray, tmp_path: Path
) -> None:
    from tether.project import Project

    video = np.stack([bead_image] * 3)
    result = _quiet_bead_map(video, source_id="sha256:abc", frames=(0, 2), projection="max")
    expected = {
        "source_id": "sha256:abc",
        "kind": "video",
        "frame_start": 0,
        "frame_stop": 2,
        "projection": "max",
        "n_frames": 3,
    }
    assert result.registration_map.provenance["bead_source"] == expected

    loaded = load_map(save_map(result.registration_map, tmp_path / "beads.npz"))
    assert loaded.provenance["bead_source"] == expected
    assert loaded.provenance["map_quality"] == result.quality.to_dict()
    _assert_same_map(loaded, result.registration_map)

    store = tmp_path / "p.tether"
    Project.create(store)
    write_calibration(store, result.registration_map, calibration_id="cal-beads")
    read_back = read_calibration(store, "cal-beads")
    assert read_back.provenance["bead_source"] == expected
    assert read_back.provenance["map_quality"] == result.quality.to_dict()
    assert read_back.provenance["bead_settings"]["min_bead_spots"] == DEFAULT_MIN_BEAD_SPOTS


def test_image_source_records_no_frames_or_projection(bead_image: np.ndarray) -> None:
    result = _quiet_bead_map(bead_image, source_id="img-1")
    assert result.source.kind == "image"
    assert result.registration_map.provenance["bead_source"] == {
        "source_id": "img-1",
        "kind": "image",
        "frame_start": None,
        "frame_stop": None,
        "projection": None,
        "n_frames": None,
    }


def test_from_file_video_and_image(bead_image: np.ndarray, tmp_path: Path) -> None:
    video_path = tmp_path / "beads-video.tif"
    tifffile.imwrite(video_path, np.stack([bead_image] * 4), photometric="minisblack")
    image_path = tmp_path / "beads-image.tif"
    tifffile.imwrite(image_path, bead_image)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", OverGateRegistrationWarning)
        from_video = bead_map_from_file(video_path, frames=(2, 4), projection="mean")
        from_image = bead_map_from_file(image_path)

    digest = hashlib.sha256(video_path.read_bytes()).hexdigest()
    assert from_video.source.source_id == f"sha256:{digest}"
    assert from_video.source.kind == "video"
    assert (from_video.source.frame_start, from_video.source.frame_stop) == (2, 4)
    assert from_video.registration_map.provenance["bead_file"] == "beads-video.tif"
    assert from_image.source.kind == "image"
    assert (
        from_image.source.source_id
        == f"sha256:{hashlib.sha256(image_path.read_bytes()).hexdigest()}"
    )
    _assert_same_map(from_video.registration_map, from_image.registration_map)

    with pytest.raises(BeadMapError, match="one page"):
        bead_map_from_file(image_path, frames=(0, 1))
    with pytest.raises(BeadMapError, match="not found"):
        bead_map_from_file(tmp_path / "missing.tif")


@pytest.mark.parametrize("axes", ["CYX", "ZYX"])
def test_from_file_refuses_channels_or_focus_planes_as_frames(
    bead_image: np.ndarray, tmp_path: Path, axes: str
) -> None:
    """A multichannel (or focus) stack is not a video: its planes must never be
    max-projected together as if they were frames."""
    path = tmp_path / f"stack-{axes}.tif"
    tifffile.imwrite(
        path, np.stack([bead_image] * 3).astype(np.uint16), imagej=True, metadata={"axes": axes}
    )
    with pytest.raises(BeadMapError, match=f"axes '{axes}'"):
        bead_map_from_file(path)


def test_from_file_accepts_an_imagej_time_series_and_a_compressed_video(
    bead_image: np.ndarray, tmp_path: Path
) -> None:
    timed = tmp_path / "beads-time.tif"
    tifffile.imwrite(
        timed, np.stack([bead_image] * 3).astype(np.uint16), imagej=True, metadata={"axes": "TYX"}
    )
    packed = tmp_path / "beads-zlib.tif"
    tifffile.imwrite(
        packed, np.stack([bead_image] * 3), photometric="minisblack", compression="zlib"
    )
    reference = _quiet_bead_map(bead_image, source_id="r")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", OverGateRegistrationWarning)
        for path in (timed, packed):
            result = bead_map_from_file(path, frames=(0, 2))
            assert result.source.kind == "video"
            assert result.source.n_frames == 3
            _assert_same_map(result.registration_map, reference.registration_map)


def test_projection_in_blocks_equals_the_whole_stack_reduction(tmp_path: Path) -> None:
    """The projection reduces a long video block by block; the result is the same
    per-pixel max and mean as reducing the whole selection at once, also on a memmap."""
    rng = np.random.default_rng(3)
    video = rng.integers(0, 4096, size=(77, 6, 10), dtype=np.uint16)
    selected = video[5:71].astype(np.float64)
    assert np.array_equal(project_frames(video, frames=(5, 71)), selected.max(axis=0))
    assert np.allclose(
        project_frames(video, frames=(5, 71), projection="mean"), selected.mean(axis=0)
    )
    mapped = np.lib.format.open_memmap(
        tmp_path / "video.npy", mode="w+", dtype=video.dtype, shape=video.shape
    )
    mapped[:] = video
    assert np.array_equal(project_frames(mapped, frames=(5, 71)), selected.max(axis=0))


def test_from_file_refuses_an_rgb_image(tmp_path: Path) -> None:
    rgb = tmp_path / "rgb.tif"
    tifffile.imwrite(rgb, np.zeros((32, 64, 3), dtype=np.uint8), photometric="rgb")
    with pytest.raises(BeadMapError, match="axes"):
        bead_map_from_file(rgb)


# --- the quality record --------------------------------------------------------


def test_quality_record_matches_the_map(bead_image: np.ndarray) -> None:
    result = _quiet_bead_map(bead_image, source_id="q")
    quality, reg_map = result.quality, result.registration_map
    assert quality.n_pairs == reg_map.n_control_points
    assert len(quality.pair_residuals) == quality.n_pairs
    residuals = np.asarray(quality.pair_residuals)
    assert np.all(residuals >= 0)
    # The RMS residual is the RMS of the per-pair residuals, and the gate's value.
    assert quality.rms_residual == pytest.approx(float(np.sqrt(np.mean(residuals**2))), abs=1e-12)
    assert quality.rms_residual == reg_map.rms_residual  # the same computation, exactly
    assert 0.0 < quality.coverage <= 1.0
    assert reg_map.provenance["map_quality"] == quality.to_dict()


def test_coverage_is_the_hull_fraction_of_the_field() -> None:
    """A 50x50 square of pairs on a 100x100 field covers 0.25 of it; pairs on one
    line, or fewer than three pairs, enclose nothing."""
    corners = np.array([[10, 10], [60, 10], [60, 60], [10, 60]], dtype=np.float64)
    inside = np.random.default_rng(0).uniform(15, 55, size=(6, 2))
    square = np.vstack([corners, inside])
    reg_map = fit_registration_map(square, square + 1.0, reference_channel=0, moving_channel=1)
    quality = map_quality(reg_map, square, square + 1.0, field_shape=(100, 100))
    assert quality.coverage == pytest.approx(0.25)
    assert quality.rms_residual == pytest.approx(0.0, abs=1e-9)
    assert quality.n_pairs == 10

    line = np.column_stack([np.linspace(0, 50, 6), np.linspace(0, 50, 6)])
    assert (
        map_quality(
            reg_map, line, reg_map.apply_reference_to_moving(line), field_shape=(100, 100)
        ).coverage
        == 0.0
    )
    two = square[:2]
    assert map_quality(reg_map, two, two + 1.0, field_shape=(100, 100)).coverage == 0.0


def test_min_bead_spots_is_a_registered_tunable() -> None:
    """The new limit is in the PRD §11.2 table with its default."""
    section = PRD.read_text(encoding="utf-8").split("### 11.2", 1)[1].split("### 11.3", 1)[0]
    rows = [line for line in section.splitlines() if "min_bead_spots" in line]
    assert rows, "PRD §11.2 has no min_bead_spots row"
    assert f"**{DEFAULT_MIN_BEAD_SPOTS}**" in rows[0]


# --- refusals --------------------------------------------------------------------


def test_too_few_bead_spots_is_refused_with_count_and_limit(bead_image: np.ndarray) -> None:
    blank = np.zeros((4, *bead_image.shape), dtype=np.uint8)
    with pytest.raises(BeadMapError) as excinfo:
        bead_map(blank, source_id="empty")
    message = str(excinfo.value)
    assert "too few bead spots" in message
    assert "0 in the donor half and 0 in the acceptor half" in message
    assert f"the limit is {DEFAULT_MIN_BEAD_SPOTS} in each half" in message


def test_min_bead_spots_limit_is_applied(bead_image: np.ndarray) -> None:
    result = _quiet_bead_map(bead_image, source_id="n")
    n_donor = result.registration_map.provenance["bead_settings"]["n_donor_spots"]
    n_acceptor = result.registration_map.provenance["bead_settings"]["n_acceptor_spots"]
    limit = min(n_donor, n_acceptor) + 1
    with pytest.raises(BeadMapError, match=f"the limit is {limit} in each half"):
        bead_map(bead_image, source_id="n", min_bead_spots=limit)
    # At the smaller count exactly, the source is accepted.
    _quiet_bead_map(bead_image, source_id="n", min_bead_spots=limit - 1)


def test_over_gate_map_warns_and_is_kept(bead_image: np.ndarray) -> None:
    with pytest.warns(OverGateRegistrationWarning):
        result = bead_map(bead_image, source_id="g", gate_px=0.1)
    assert result.registration_map.low_confidence


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"frames": (0, 9)}, "not inside"),
        ({"frames": (2, 2)}, "not inside"),
        ({"frames": (-1, 2)}, "not inside"),
        ({"frames": "ab"}, "pair of integers"),
        ({"projection": "median"}, "projection must be one of"),
        ({"donor_side": "top"}, "donor_side"),
        ({"prealign": "affine"}, "prealign"),
        ({"min_bead_spots": 0}, "min_bead_spots"),
    ],
)
def test_bad_video_arguments_are_refused(bead_image: np.ndarray, kwargs: dict, match: str) -> None:
    video = np.stack([bead_image] * 3)
    with pytest.raises(BeadMapError, match=match):
        bead_map(video, source_id="bad", **kwargs)


def test_image_with_frames_or_projection_is_refused(bead_image: np.ndarray) -> None:
    with pytest.raises(BeadMapError, match="not to one image"):
        bead_map(bead_image, source_id="i", frames=(0, 1))
    with pytest.raises(BeadMapError, match="not to one image"):
        bead_map(bead_image, source_id="i", projection="mean")


def test_bad_shapes_and_ids_are_refused(bead_image: np.ndarray) -> None:
    with pytest.raises(BeadMapError, match="one image"):
        bead_map(bead_image.ravel(), source_id="s")
    with pytest.raises(BeadMapError, match="non-empty"):
        bead_map(bead_image, source_id=" ")
    nan_image = bead_image.astype(np.float64)
    nan_image[0, 0] = np.nan
    with pytest.raises(BeadMapError, match="non-finite"):
        bead_map(nan_image, source_id="s")


def test_source_record_validates_itself() -> None:
    with pytest.raises(BeadMapError, match="no frame range"):
        BeadMapSource(source_id="x", kind="image", projection="max")
    with pytest.raises(BeadMapError, match="needs frame_start"):
        BeadMapSource(source_id="x", kind="video", projection="max")
    with pytest.raises(BeadMapError, match="kind"):
        BeadMapSource(source_id="x", kind="stack")  # type: ignore[arg-type]
