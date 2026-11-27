# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Frame geometry steps before the channel split (#1435).

A movie's *frame steps* turn, flip, move or crop the whole of every frame before the split
of a map or a layout. These tests pin:

* the direction: ``rotate 90`` is ImageJ's "Rotate 90 Degrees Right", ``numpy.rot90(k=3)``;
* that every list of steps is a one-to-one map of the plane (a point to the view and back
  is the same point) and never resamples a pixel value;
* that a movie stored turned, flipped or moved against its map or layout, extracted with
  the steps that undo it, gives the same molecules and traces as the original movie -- with
  a layout, a saved Tether map and an imported ``.tmap``;
* where the steps come from (the map's default, or the movie's own, ``none`` included),
  how they are recorded, and that a re-extraction uses them again;
* the refusal of steps that leave the frame too small, naming both sizes;
* the ``--frame-steps`` options of ``tether extract``, ``reextract``, ``batch`` and
  ``map create``.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("skimage")
pytest.importorskip("h5py")
pytest.importorskip("tifffile")

import h5py  # noqa: E402
import numpy as np  # noqa: E402
import tifffile  # noqa: E402

import tether.project.extract as ext  # noqa: E402
from tether.imaging.calibrate import read_calibration  # noqa: E402
from tether.imaging.extract import read_molecules, read_movie_settings, read_traces  # noqa: E402
from tether.imaging.layout import ChannelLayout  # noqa: E402
from tether.imaging.map_asset import (  # noqa: E402
    MapAsset,
    load_map_asset,
    save_map_asset,
)
from tether.imaging.recipe import (  # noqa: E402
    ChannelRecipe,
    ChannelRecipeError,
    frame_view_shape,
    through_frame_steps,
)
from tether.imaging.split import ChannelGeometry  # noqa: E402
from tether.project.extract import (  # noqa: E402
    ExtractionError,
    ExtractOptions,
    extract_movie,
    reextract_movie,
)
from tether.project.lock import publish_lock  # noqa: E402

_REPO = Path(__file__).resolve().parents[1]
_TUTORIAL_MOVIE = _REPO / "docs" / "tutorial" / "data" / "tutorial_movie.tif"

#: A combination of every kind of step: the stored movie is the original moved 3 px right
#: (with a filler border), mirrored left-right and turned 90 degrees to the left.
_COMBINED = "translate 0 -3, crop 1 1 80 40, flip h, rotate 90"


def _steps(text: str) -> ChannelRecipe:
    return ChannelRecipe.parse(text)


def _write_be(path: Path, stack: np.ndarray) -> Path:
    tifffile.imwrite(
        path, np.ascontiguousarray(stack, dtype=">u2"), photometric="minisblack", byteorder=">"
    )
    return path


def _original() -> np.ndarray:
    return tifffile.imread(_TUTORIAL_MOVIE)


def _stored_against(original: np.ndarray, text: str) -> np.ndarray:
    """The movie as stored against ``original``: the frames ``text`` turns back into it."""
    turned = {
        "rotate 90": lambda m: np.rot90(m, 1, axes=(1, 2)),  # stored turned left
        "rotate 180": lambda m: np.rot90(m, 2, axes=(1, 2)),
        "rotate 270": lambda m: np.rot90(m, 3, axes=(1, 2)),
        "flip h": lambda m: m[:, :, ::-1],
        "flip v": lambda m: m[:, ::-1, :],
    }
    if text in turned:
        stored = turned[text](original)
    elif text == "translate 2 0, crop 3 1 42 80":  # stored 2 px higher, filler below
        stored = np.full((original.shape[0], 42, 80), 7, dtype=original.dtype)
        stored[:, :40] = original
    elif text == _COMBINED:
        left = np.rot90(original, 1, axes=(1, 2))[:, :, ::-1]  # 80 x 40
        stored = np.full((original.shape[0], 80, 43), 7, dtype=original.dtype)
        stored[:, :, 3:] = left
    else:  # pragma: no cover - the test parameters are closed
        raise AssertionError(text)
    # The test's own premise: the steps turn the stored movie back into the original.
    np.testing.assert_array_equal(_steps(text).apply(stored), original)
    return stored


_CASES = (
    "rotate 90",
    "rotate 180",
    "rotate 270",
    "flip h",
    "flip v",
    "translate 2 0, crop 3 1 42 80",
    _COMBINED,
)


def _assert_same_extraction(expected: Path, actual: Path) -> None:
    exp_traces, act_traces = read_traces(expected), read_traces(actual)
    assert set(act_traces) == set(exp_traces)
    for name in exp_traces:
        np.testing.assert_array_equal(act_traces[name], exp_traces[name], err_msg=name)
    exp_mol, act_mol = read_molecules(expected), read_molecules(actual)
    assert len(exp_mol["donor_xy"]) > 0  # a vacuous equality would prove nothing
    for name in ("donor_xy", "acceptor_xy"):
        np.testing.assert_array_equal(act_mol[name], exp_mol[name], err_msg=name)


def _profile(project: Path) -> dict:
    (profile,) = read_movie_settings(project).values()
    return profile


def _movie_row(project: Path) -> np.ndarray:
    with h5py.File(project, "r") as f:
        return f["/movies/table"][0]


@pytest.fixture(scope="module")
def baseline(tmp_path_factory) -> dict[str, Path]:
    """The original movie extracted natively, and its map saved as a map file."""
    root = tmp_path_factory.mktemp("baseline")
    native = root / "native.tether"
    summary = extract_movie(_TUTORIAL_MOVIE, native)
    reg_map = read_calibration(native, summary.calibration_id)
    map_file = save_map_asset(MapAsset.from_registration_map(reg_map), root / "map.npz")
    with_map = root / "with_map.tether"
    extract_movie(_TUTORIAL_MOVIE, with_map, map_path=map_file)
    return {"native": native, "map_file": map_file, "with_map": with_map, "root": root}


# --- the steps themselves ------------------------------------------------------------


def test_rotate_90_is_imagej_rotate_90_degrees_right() -> None:
    frame = np.arange(12).reshape(3, 4)
    turned = _steps("rotate 90").apply(frame)
    np.testing.assert_array_equal(turned, np.rot90(frame, k=3))
    # Clockwise: the top-left pixel goes to the top-right corner.
    assert turned[0, -1] == frame[0, 0]
    assert turned.shape == (4, 3)


def _random_steps(rng: np.random.Generator) -> ChannelRecipe:
    """A random list of rotate, flip, move and crop steps that a 30 x 40 frame can take."""
    steps: list[tuple[str, object]] = []
    h, w = 30, 40
    for _ in range(int(rng.integers(1, 6))):
        kind = rng.choice(["rotate", "flip", "move", "crop"])
        if kind == "rotate":
            deg = int(rng.choice([90, 180, 270]))
            steps.append(("rotate", deg))
            if deg != 180:
                h, w = w, h
        elif kind == "flip":
            steps.append(("flip", (int(rng.integers(2)), int(rng.integers(2)))))
        elif kind == "move" and h > 4 and w > 4:
            dy, dx = int(rng.integers(0, 3)), int(rng.integers(0, 3))
            steps.append(("translate", (dy, dx)))  # down/right, then crop the empty part
            steps.append(("crop", (dy + 1, dx + 1, h, w)))
            h, w = h - dy, w - dx
        elif kind == "crop" and h > 4 and w > 4:
            y1, x1 = int(rng.integers(1, 3)), int(rng.integers(1, 3))
            steps.append(("crop", (y1, x1, h - 1, w - 1)))
            h, w = h - y1, w - x1
    return ChannelRecipe(tuple(steps))


def test_every_list_of_steps_is_inverted_and_never_resamples() -> None:
    rng = np.random.default_rng(1435)
    frame = rng.integers(0, 2**16, size=(30, 40)).astype(np.uint16)
    for _ in range(200):
        steps = _random_steps(rng)
        view = steps.apply(frame)
        # Every output pixel is one input pixel: the values are a sub-multiset, and a
        # list without a crop keeps every pixel.
        assert set(np.unique(view)) <= set(np.unique(frame))
        if not any(name == "crop" for name, _ in steps.steps):
            np.testing.assert_array_equal(np.sort(view, axis=None), np.sort(frame, axis=None))
        # A point that goes to the view and back is the same point.
        points = rng.uniform(-5.0, 45.0, size=(16, 2))
        back = steps.to_original(steps.to_view(points, frame.shape), frame.shape)
        np.testing.assert_allclose(back, points, atol=1e-9)
        # The pixel at a view position is the one at its original position.
        vh, vw = view.shape
        r, c = int(rng.integers(vh)), int(rng.integers(vw))
        x, y = steps.to_original(np.array([[c, r]], dtype=float), frame.shape)[0]
        assert view[r, c] == frame[int(round(y)), int(round(x))]


def test_through_frame_steps_folds_the_steps_into_one_geometry() -> None:
    rng = np.random.default_rng(14350)
    frame = rng.integers(0, 1000, size=(30, 40))
    for _ in range(100):
        steps = _random_steps(rng)
        h, w = frame_view_shape(steps, 30, 40)
        cut = ChannelGeometry(crop=(1, 1, h, max(1, w // 2)), rotation_deg=0)
        folded = through_frame_steps(steps, cut, frame.shape)
        np.testing.assert_array_equal(folded.apply(frame), cut.apply(steps.apply(frame)))
    plain = ChannelGeometry(crop=(1, 1, 10, 10))
    assert through_frame_steps(ChannelRecipe(), plain, (30, 40)) is plain


def test_steps_that_do_not_fit_the_frame_name_both() -> None:
    with pytest.raises(ChannelRecipeError, match=r"crop 1 1 50 50.*30 x 40 px"):
        frame_view_shape(_steps("crop 1 1 50 50"), 30, 40)


# --- extraction: the inverse steps give the original's molecules and traces ----------------


@pytest.mark.parametrize("text", _CASES)
def test_a_movie_against_its_layout_with_the_inverse_steps_gives_the_same_extraction(
    tmp_path, baseline, text
) -> None:
    movie = _write_be(tmp_path / "stored.tif", _stored_against(_original(), text))
    out = tmp_path / "stepped.tether"
    summary = extract_movie(movie, out, frame_steps=_steps(text))
    _assert_same_extraction(baseline["native"], out)
    assert (str(summary.frame_steps), summary.frame_steps_source) == (str(_steps(text)), "movie")
    # The stored geometry reads the channels straight from the stored frames (#1435).
    row = _movie_row(out)
    stored = tifffile.imread(movie)
    donor = ChannelGeometry(
        crop=tuple(int(v) for v in row["donor_crop"]),
        rotation_deg=int(row["donor_rotation_deg"]),
        flip=tuple(int(v) for v in row["donor_flip"]),
    )
    np.testing.assert_array_equal(donor.apply(stored), _original()[:, :, :40])
    assert (int(row["height"]), int(row["width"])) == stored.shape[1:]


@pytest.mark.parametrize("text", ("rotate 90", _COMBINED))
def test_a_movie_against_its_map_file_with_the_inverse_steps_gives_the_same_extraction(
    tmp_path, baseline, text
) -> None:
    movie = _write_be(tmp_path / "stored.tif", _stored_against(_original(), text))
    out = tmp_path / "stepped.tether"
    extract_movie(movie, out, map_path=baseline["map_file"], frame_steps=_steps(text))
    _assert_same_extraction(baseline["with_map"], out)


def _stub_tmap(tmp_path: Path, baseline: dict[str, Path], monkeypatch) -> Path:
    """A ``.tmap`` whose decode gives the baseline's map (the MCOS format is not authored)."""
    saved = read_calibration(baseline["with_map"], _calibration_id(baseline["with_map"]))
    imported = dataclasses.replace(saved, source="imported", provenance={})
    monkeypatch.setattr(ext, "read_tmap", lambda _path: None)
    monkeypatch.setattr(ext, "registration_map_from_tmap", lambda *_a, **_k: imported)
    path = tmp_path / "map.tmap"
    path.write_bytes(b"stub .tmap; decode is monkeypatched at the extract seam")
    return path


def _calibration_id(project: Path) -> str:
    return _movie_row(project)["calibration_id"].decode("utf-8")


def test_a_tmap_default_is_used_and_kept_with_the_map_record(
    tmp_path, baseline, monkeypatch
) -> None:
    tmap = _stub_tmap(tmp_path, baseline, monkeypatch)
    movie = _write_be(tmp_path / "stored.tif", _stored_against(_original(), "rotate 90"))
    out = tmp_path / "tmap.tether"
    summary = extract_movie(movie, out, tmap=tmap, map_frame_steps=_steps("rotate 90"))
    _assert_same_extraction(baseline["with_map"], out)
    assert summary.frame_steps_source == "map"
    assert _profile(out)["frame_steps"] == {"steps": "rotate 90", "source": "map"}
    # The .tmap's default is stored with its record in the project's /calibration.
    assert read_calibration(out, summary.calibration_id).provenance["map_frame_steps"] == (
        "rotate 90"
    )


def test_a_tmap_default_without_a_tmap_is_refused(tmp_path, baseline) -> None:
    with pytest.raises(ExtractionError, match="default frame steps of a .tmap"):
        extract_movie(
            _TUTORIAL_MOVIE,
            tmp_path / "x.tether",
            map_path=baseline["map_file"],
            map_frame_steps=_steps("rotate 90"),
        )


# --- the map's default and the movie's override ----------------------------------------


def test_a_map_file_stores_default_steps_and_an_older_file_has_none(tmp_path, baseline) -> None:
    plain = load_map_asset(baseline["map_file"])
    assert plain.frame_steps is None
    with np.load(baseline["map_file"]) as data:
        assert int(data["format_version"]) == 3  # ADR-0128: an added array is a new version
        assert json.loads(str(data["frame_steps_json"])) is None
    turned = save_map_asset(
        MapAsset.from_registration_map(plain.registration_map, frame_steps=_steps("rotate 90")),
        tmp_path / "turned.npz",
    )
    asset = load_map_asset(turned)
    assert asset.frame_steps == _steps("rotate 90")
    assert asset.record()["frame_steps"] == "rotate 90"
    with pytest.raises(TypeError, match="frame_steps"):
        MapAsset.from_registration_map(plain.registration_map, frame_steps="rotate 90")  # type: ignore[arg-type]


def test_a_version_2_map_file_reads_with_no_default_and_version_3_needs_the_array(
    tmp_path, baseline
) -> None:
    from tether.imaging.calibrate import MapFileError

    with np.load(baseline["map_file"]) as data:
        arrays = {k: data[k] for k in data.files if k != "frame_steps_json"}
    np.savez(tmp_path / "v3-without.npz", **arrays)
    with pytest.raises(MapFileError, match=r"version-3 map file that lacks \['frame_steps_json'\]"):
        load_map_asset(tmp_path / "v3-without.npz")
    arrays["format_version"] = np.array(2, dtype=np.int64)
    np.savez(tmp_path / "v2.npz", **arrays)
    old = load_map_asset(tmp_path / "v2.npz")
    assert (old.format_version, old.frame_steps) == (2, None)


@pytest.fixture
def turned_map(tmp_path, baseline) -> Path:
    """The baseline map with the default ``rotate 90``: made on turned frames."""
    reg_map = load_map_asset(baseline["map_file"]).registration_map
    asset = MapAsset.from_registration_map(reg_map, frame_steps=_steps("rotate 90"))
    return save_map_asset(asset, tmp_path / "turned_map.npz")


def test_the_map_default_applies_and_a_movie_override_replaces_it(
    tmp_path, baseline, turned_map
) -> None:
    # Map default only: the movie stored turned left is turned back by the map's default.
    movie = _write_be(tmp_path / "stored.tif", _stored_against(_original(), "rotate 90"))
    default = tmp_path / "default.tether"
    summary = extract_movie(movie, default, map_path=turned_map)
    _assert_same_extraction(baseline["with_map"], default)
    assert summary.frame_steps_source == "map"
    assert _profile(default)["frame_steps"] == {"steps": "rotate 90", "source": "map"}

    # Override with "none": a movie already turned (as an ImageJ TIFF is) reads as stored.
    already = tmp_path / "already.tether"
    summary = extract_movie(
        _TUTORIAL_MOVIE, already, map_path=turned_map, frame_steps=_steps("none")
    )
    _assert_same_extraction(baseline["with_map"], already)
    assert _profile(already)["frame_steps"] == {"steps": "none", "source": "movie"}

    # Override with other steps: the movie's own replace the map's.
    flipped = _write_be(tmp_path / "flipped.tif", _stored_against(_original(), "flip h"))
    own = tmp_path / "own.tether"
    extract_movie(flipped, own, map_path=turned_map, frame_steps=_steps("flip h"))
    _assert_same_extraction(baseline["with_map"], own)
    assert _profile(own)["frame_steps"] == {"steps": "flip h", "source": "movie"}


def test_no_steps_anywhere_records_nothing_and_changes_nothing(baseline) -> None:
    assert "frame_steps" not in _profile(baseline["native"])
    assert "frame_steps" not in _profile(baseline["with_map"])
    row = _movie_row(baseline["native"])
    assert row["donor_crop"].tolist() == [1, 1, 40, 40]
    assert row["acceptor_crop"].tolist() == [1, 41, 40, 80]


def test_a_reextraction_uses_the_stored_steps(tmp_path, baseline, turned_map) -> None:
    movie = _write_be(tmp_path / "stored.tif", _stored_against(_original(), "rotate 90"))
    out = tmp_path / "stepped.tether"
    extract_movie(movie, out, frame_steps=_steps("rotate 90"))
    with publish_lock(out) as guard:
        result = reextract_movie(out, movie, publish_guard=guard)
    assert (str(result.extraction.frame_steps), result.extraction.frame_steps_source) == (
        "rotate 90",
        "movie",
    )
    _assert_same_extraction(baseline["native"], out)
    assert _profile(out)["frame_steps"] == {"steps": "rotate 90", "source": "movie"}

    # A map's default is used again when the map used now has none of its own.
    mapped = tmp_path / "mapped.tether"
    extract_movie(movie, mapped, map_path=turned_map)
    with publish_lock(mapped) as guard:
        result = reextract_movie(mapped, movie, map_path=baseline["map_file"], publish_guard=guard)
    assert result.extraction.frame_steps_source == "map"
    _assert_same_extraction(baseline["with_map"], mapped)


# --- too small a frame ---------------------------------------------------------------------


def test_steps_that_leave_the_frame_too_small_for_the_layout_are_refused(tmp_path) -> None:
    layout = ChannelLayout(
        kind="rectangles", donor_rect=(1, 1, 30, 30), acceptor_rect=(1, 31, 30, 60)
    )
    with pytest.raises(ExtractionError) as info:
        extract_movie(
            _TUTORIAL_MOVIE,
            tmp_path / "small.tether",
            options=ExtractOptions(channel_layout=layout),
            frame_steps=_steps("crop 1 1 30 50"),
        )
    message = str(info.value)
    assert "40 x 80 px" in message and "30 x 50 px after them" in message
    assert not (tmp_path / "small.tether").exists()


def test_steps_that_leave_the_frame_too_small_for_the_map_are_refused(tmp_path, baseline) -> None:
    with pytest.raises(ExtractionError) as info:
        extract_movie(
            _TUTORIAL_MOVIE,
            tmp_path / "small.tether",
            map_path=baseline["map_file"],
            frame_steps=_steps("crop 1 1 40 60"),
        )
    message = str(info.value)
    assert "40 x 60 px" in message and "40 x 80 px" in message
    with pytest.raises(ExtractionError, match=r"crop 1 1 50 80.*40 x 80 px"):
        extract_movie(_TUTORIAL_MOVIE, tmp_path / "x.tether", frame_steps=_steps("crop 1 1 50 80"))


# --- the command line ------------------------------------------------------------------------


def test_the_extract_command_takes_frame_steps(tmp_path, baseline, capsys) -> None:
    from tether.cli import main

    movie = _write_be(tmp_path / "stored.tif", _stored_against(_original(), "rotate 90"))
    out = tmp_path / "cli.tether"
    assert main(["extract", str(movie), "-o", str(out), "--frame-steps", "rotate 90"]) == 0
    assert "frame steps: rotate 90 (from the movie)" in capsys.readouterr().out
    _assert_same_extraction(baseline["native"], out)
    assert (
        main(["extract", str(movie), "-o", str(tmp_path / "b.tether"), "--frame-steps", "spin 3"])
        == 1
    )
    assert "frame_steps 'spin 3'" in capsys.readouterr().err


@pytest.mark.parametrize("command", ("extract", "reextract", "batch"))
def test_every_extraction_command_documents_the_options(command, capsys) -> None:
    from tether.cli import main

    with pytest.raises(SystemExit):
        main([command, "--help"])
    text = " ".join(capsys.readouterr().out.split())
    assert "--frame-steps" in text and "--map-frame-steps" in text
    assert "Rotate 90 Degrees Right" in text


def test_map_create_stores_the_default(tmp_path) -> None:
    from tether.services.maps import MapCreateRequest

    request = MapCreateRequest(source="x.tif", output="m.npz", frame_steps="rotate 90")
    assert request.frame_steps == "rotate 90"


def test_a_batch_gives_one_value_to_every_movie(tmp_path) -> None:
    from tether.project.batch import MovieJob
    from tether.services.base import RequestRefused
    from tether.services.batch import BatchRequest, plan

    job = MovieJob(movie_path="a.tif", output_path="a.tether", frame_steps="rotate 90")
    assert job.steps_kwargs() == {"frame_steps": _steps("rotate 90")}
    assert MovieJob(movie_path="a.tif", output_path="a.tether").steps_kwargs() == {}

    _write_be(tmp_path / "a.tif", _original())
    request = BatchRequest(
        movies=(str(tmp_path / "a.tif"),), out_dir=str(tmp_path / "out"), frame_steps="flip h"
    )
    assert "frame-steps=flip h (every movie)" in plan(request).settings
    bad = BatchRequest(movies=(str(tmp_path / "a.tif"),), out_dir=str(tmp_path), frame_steps="x")
    with pytest.raises(RequestRefused, match="frame_steps 'x'"):
        plan(bad)
    lone = BatchRequest(
        movies=(str(tmp_path / "a.tif"),), out_dir=str(tmp_path), map_frame_steps="rotate 90"
    )
    with pytest.raises(RequestRefused, match="--map-frame-steps"):
        plan(lone)


def test_a_batch_extracts_with_the_steps(tmp_path, baseline) -> None:
    from tether.services.batch import BatchRequest, run

    (tmp_path / "in").mkdir()
    _write_be(tmp_path / "in" / "stored.tif", _stored_against(_original(), "rotate 90"))
    request = BatchRequest(
        movies=(str(tmp_path / "in" / "stored.tif"),),
        out_dir=str(tmp_path / "out"),
        frame_steps="rotate 90",
        idealize=False,
    )
    run(request)
    out = tmp_path / "out" / "stored.tether"
    _assert_same_extraction(baseline["native"], out)
    assert _profile(out)["frame_steps"] == {"steps": "rotate 90", "source": "movie"}
    assert json.loads(json.dumps(_profile(out)))["frame_steps"]["source"] == "movie"


def test_a_map_file_without_a_default_ignores_one_in_its_provenance(tmp_path, baseline) -> None:
    """The file's ``frame_steps`` is the default; an inherited provenance key is not (Codex P2)."""
    reg_map = load_map_asset(baseline["map_file"]).registration_map
    stale = dataclasses.replace(
        reg_map, provenance={**reg_map.provenance, "map_frame_steps": "rotate 90"}
    )
    map_file = save_map_asset(MapAsset.from_registration_map(stale), tmp_path / "stale.npz")
    assert load_map_asset(map_file).frame_steps is None
    out = tmp_path / "plain.tether"
    summary = extract_movie(_TUTORIAL_MOVIE, out, map_path=map_file)
    assert summary.frame_steps_source is None
    _assert_same_extraction(baseline["with_map"], out)
    assert "map_frame_steps" not in read_calibration(out, summary.calibration_id).provenance


def test_saving_a_project_map_keeps_its_default_steps(tmp_path, baseline, monkeypatch) -> None:
    """A ``.tmap``'s default recorded in /calibration goes into the saved map file (Codex P2)."""
    from tether.imaging.map_asset import save_project_map

    tmap = _stub_tmap(tmp_path, baseline, monkeypatch)
    movie = _write_be(tmp_path / "stored.tif", _stored_against(_original(), "rotate 90"))
    project = tmp_path / "tmap.tether"
    summary = extract_movie(movie, project, tmap=tmap, map_frame_steps=_steps("rotate 90"))
    saved = save_project_map(project, summary.calibration_id, tmp_path / "saved.npz")
    assert saved.frame_steps == _steps("rotate 90")
    assert load_map_asset(tmp_path / "saved.npz").frame_steps == _steps("rotate 90")
    # Reused on the same movie, the saved map turns it again.
    again = tmp_path / "again.tether"
    assert extract_movie(movie, again, map_path=tmp_path / "saved.npz").frame_steps_source == "map"
    _assert_same_extraction(baseline["with_map"], again)
    # The call can still clear it.
    none = save_project_map(
        project, summary.calibration_id, tmp_path / "none.npz", frame_steps=None
    )
    assert none.frame_steps is None
