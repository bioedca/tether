# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Donor and acceptor in two files: two movies as one acquisition (#909).

Locks the issue's acceptance criteria:

* two movies are paired as one acquisition with the roles donor and acceptor;
* a pair whose frame count, frame time or pixel type differ is refused with a message;
* the public tutorial movie (dual view, ``docs/tutorial/data/tutorial_movie.tif``) split
  into two files and paired gives traces equal to those of the one-file extraction;
* the store records both sources and their roles;
* a map between the two cameras comes from a bead file pair through the present
  registration stages, equal to the map of the same beads in one frame.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("skimage")
h5py = pytest.importorskip("h5py")
tifffile = pytest.importorskip("tifffile")

import numpy as np  # noqa: E402

from tether.imaging.beadmap import BeadMapError, bead_map, bead_map_pair_from_files  # noqa: E402
from tether.imaging.calibrate import OverGateRegistrationWarning, save_map  # noqa: E402
from tether.io.pair import pair_sha256  # noqa: E402
from tether.project.extract import (  # noqa: E402
    ExtractionError,
    ExtractOptions,
    add_movie_to_project,
    extract_movie,
    reextract_movie,
)
from tether.project.sources import file_sha256, read_sources  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TUTORIAL = ROOT / "docs" / "tutorial" / "data" / "tutorial_movie.tif"
BEADS = Path(__file__).resolve().parent / "fixtures" / "bead_prealign_oracle.npz"
TRACES = (
    "donor_raw",
    "donor_background",
    "donor_corrected",
    "acceptor_raw",
    "acceptor_background",
    "acceptor_corrected",
)


def _write(path: Path, frames: np.ndarray, **kwargs: object) -> Path:
    tifffile.imwrite(path, frames, **kwargs)
    return path


@pytest.fixture
def pair(tmp_path: Path) -> tuple[Path, Path]:
    """The tutorial movie cut at its midline into a donor file and an acceptor file."""
    movie = tifffile.imread(TUTORIAL)
    half = movie.shape[2] // 2
    donor = _write(tmp_path / "cam1_donor.tif", np.ascontiguousarray(movie[:, :, :half]))
    acceptor = _write(tmp_path / "cam2_acceptor.tif", np.ascontiguousarray(movie[:, :, half:]))
    return donor, acceptor


def _traces(project: Path) -> dict[str, np.ndarray]:
    with h5py.File(project, "r") as f:
        return {name: f["traces"][name][()] for name in TRACES}


def _movie_rows(project: Path) -> np.ndarray:
    with h5py.File(project, "r") as f:
        return f["movies"]["table"][()]


def _text(value: object) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


# --- the pair gives the one-file traces ----------------------------------------


def test_a_split_public_movie_paired_gives_the_one_file_traces(
    pair: tuple[Path, Path], tmp_path: Path
) -> None:
    donor, acceptor = pair
    one = extract_movie(TUTORIAL, tmp_path / "one.tether")
    two = extract_movie(donor, tmp_path / "pair.tether", acceptor_movie=acceptor)

    assert two.n_molecules == one.n_molecules == 3
    assert two.n_control_points == one.n_control_points
    expected, got = _traces(tmp_path / "one.tether"), _traces(tmp_path / "pair.tether")
    for name in TRACES:
        assert np.array_equal(got[name], expected[name]), name
    # The molecules are the same spots, in channel pixels.
    with h5py.File(tmp_path / "one.tether") as f1, h5py.File(tmp_path / "pair.tether") as f2:
        for column in ("donor_xy", "acceptor_xy"):
            assert np.array_equal(
                f2["molecules"]["table"][column], f1["molecules"]["table"][column]
            ), column


def test_the_store_records_both_sources_and_their_roles(
    pair: tuple[Path, Path], tmp_path: Path
) -> None:
    donor, acceptor = pair
    project = tmp_path / "pair.tether"
    summary = extract_movie(donor, project, acceptor_movie=acceptor)

    (row,) = _movie_rows(project)
    sha_donor, sha_acceptor = file_sha256(donor), file_sha256(acceptor)
    assert _text(row["sha256"]) == pair_sha256(sha_donor, sha_acceptor) == summary.movie_sha256
    # Each file is one whole channel of the acquisition.
    assert list(row["donor_crop"]) == list(row["acceptor_crop"]) == [1, 1, 40, 40]

    records = [r for r in read_sources(project) if r.movie_id == summary.movie_id]
    assert [(r.role, r.acquisition, r.original_name, r.sha256, r.stored) for r in records] == [
        ("movie", {"pair_role": "donor"}, donor.name, sha_donor, True),
        ("movie", {"pair_role": "acceptor"}, acceptor.name, sha_acceptor, True),
    ]
    assert [r.resolve(project) for r in records] == [donor, acceptor]
    assert {r.file_size for r in records} == {donor.stat().st_size}

    from tether.imaging.extract import read_movie_settings

    profile = read_movie_settings(project)[summary.movie_id]
    assert profile["movie_pair"] == {"donor": donor.name, "acceptor": acceptor.name}


def test_a_pair_appends_once_and_extracts_again_as_a_new_generation(
    pair: tuple[Path, Path], tmp_path: Path
) -> None:
    from tether.project.lock import publish_lock

    donor, acceptor = pair
    project = tmp_path / "project.tether"
    first = extract_movie(TUTORIAL, project)
    with publish_lock(project) as guard:
        added = add_movie_to_project(project, donor, acceptor_movie=acceptor, publish_guard=guard)
        with pytest.raises(ExtractionError, match="already in"):
            add_movie_to_project(project, donor, acceptor_movie=acceptor, publish_guard=guard)
        again = reextract_movie(project, donor, acceptor_movie=acceptor, publish_guard=guard)
    assert added.movie_id != first.movie_id
    assert again.extraction.movie_id == added.movie_id
    assert len(_movie_rows(project)) == 2
    roles = [r.acquisition for r in read_sources(project) if r.movie_id == added.movie_id]
    assert roles == [{"pair_role": "donor"}, {"pair_role": "acceptor"}]


# --- refusals --------------------------------------------------------------------


def _movie(n_frames: int = 6, *, dtype: str = ">u2") -> np.ndarray:
    rng = np.random.default_rng(909)
    return rng.integers(100, 200, size=(n_frames, 16, 16)).astype(dtype)


_TIMED = {"imagej": True, "metadata": {"axes": "TYX", "finterval": 0.1}}
_SLOWER = {"imagej": True, "metadata": {"axes": "TYX", "finterval": 0.2}}


@pytest.mark.parametrize(
    ("donor_kwargs", "acceptor_frames", "acceptor_kwargs", "match"),
    [
        ({}, _movie(5), {}, r"frame count 6 and 5"),
        (_TIMED, _movie(), _SLOWER, r"frame time 0.1 s and 0.2 s"),
        ({}, _movie(dtype=">f4"), {}, r"pixel type uint16 and float32"),
    ],
)
def test_a_pair_that_differs_is_refused_with_a_message(
    tmp_path: Path,
    donor_kwargs: dict,
    acceptor_frames: np.ndarray,
    acceptor_kwargs: dict,
    match: str,
) -> None:
    donor = _write(tmp_path / "d.tif", _movie(), **donor_kwargs)
    acceptor = _write(tmp_path / "a.tif", acceptor_frames, **acceptor_kwargs)
    with pytest.raises(ExtractionError, match=match) as info:
        extract_movie(donor, tmp_path / "out.tether", acceptor_movie=acceptor)
    assert "cannot be one acquisition" in str(info.value)
    assert not (tmp_path / "out.tether").exists()


def test_a_known_frame_time_against_an_unknown_one_is_refused(tmp_path: Path) -> None:
    donor = _write(tmp_path / "d.tif", _movie(), **_TIMED)
    acceptor = _write(tmp_path / "a.tif", _movie())
    with pytest.raises(ExtractionError, match=r"frame time 0.1 s and unknown"):
        extract_movie(donor, tmp_path / "out.tether", acceptor_movie=acceptor)


def test_a_pair_of_different_frame_sizes_is_refused(tmp_path: Path) -> None:
    donor = _write(tmp_path / "d.tif", _movie())
    acceptor = _write(tmp_path / "a.tif", _movie()[:, :, :12])
    with pytest.raises(ExtractionError, match=r"frame size 16 x 16 and 16 x 12 px"):
        extract_movie(donor, tmp_path / "out.tether", acceptor_movie=acceptor)


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"options": ExtractOptions(donor_side="right")}, "each file is one whole channel"),
        ({"tmap": "x.tmap"}, "a .tmap cannot be applied to a movie pair"),
    ],
)
def test_a_pair_takes_no_split_option(
    pair: tuple[Path, Path], tmp_path: Path, kwargs: dict, match: str
) -> None:
    donor, acceptor = pair
    with pytest.raises(ExtractionError, match=match):
        extract_movie(donor, tmp_path / "out.tether", acceptor_movie=acceptor, **kwargs)


def test_one_file_or_a_missing_acceptor_is_not_a_pair(
    pair: tuple[Path, Path], tmp_path: Path
) -> None:
    donor, _ = pair
    with pytest.raises(ExtractionError, match="are one file"):
        extract_movie(donor, tmp_path / "out.tether", acceptor_movie=donor)
    with pytest.raises(ExtractionError, match="acceptor movie not found"):
        extract_movie(donor, tmp_path / "out.tether", acceptor_movie=tmp_path / "none.tif")


# --- a map between the two cameras from a bead file pair --------------------------


@pytest.fixture(scope="module")
def beads() -> tuple[np.ndarray, np.ndarray]:
    data = np.load(BEADS)
    return data["donor"], data["acceptor"]


def _quiet(call, *args, **kwargs):
    # The committed bead crops are over the 0.5 px gate; the warning is tested elsewhere.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", OverGateRegistrationWarning)
        return call(*args, **kwargs)


def test_a_bead_file_pair_gives_the_map_of_the_same_beads_in_one_frame(
    beads: tuple[np.ndarray, np.ndarray], tmp_path: Path
) -> None:
    donor, acceptor = beads
    one = _quiet(bead_map, np.hstack([donor, acceptor]), source_id="one-frame")
    d = _write(tmp_path / "beads_donor.tif", donor)
    a = _write(tmp_path / "beads_acceptor.tif", acceptor)
    two = _quiet(bead_map_pair_from_files, d, a)

    for name in ("ref_to_moving", "moving_to_ref"):
        for field in ("a", "b", "norm_xy", "norm_uv"):
            assert np.array_equal(
                getattr(getattr(two.registration_map, name), field),
                getattr(getattr(one.registration_map, name), field),
            ), f"{name}.{field}"
    assert two.quality == one.quality
    whole = two.registration_map.reference_geometry
    assert whole == two.registration_map.moving_geometry
    assert whole is not None and whole.crop == (1, 1, *donor.shape)
    provenance = two.registration_map.provenance
    assert provenance["bead_source"]["source_id"] == f"sha256:{file_sha256(d)}"
    assert provenance["acceptor_bead_source"]["source_id"] == f"sha256:{file_sha256(a)}"
    assert provenance["bead_settings"]["two_files"] is True
    assert two.acceptor_source is not None and two.acceptor_source.kind == "image"


def test_a_bead_pair_map_applies_to_a_movie_pair_only(
    beads: tuple[np.ndarray, np.ndarray], tmp_path: Path
) -> None:
    donor, acceptor = beads
    pair_map = _quiet(
        bead_map_pair_from_files,
        _write(tmp_path / "bd.tif", donor),
        _write(tmp_path / "ba.tif", acceptor),
    ).registration_map
    save_map(pair_map, tmp_path / "pair.npz")
    one_map = _quiet(bead_map, np.hstack([donor, acceptor]), source_id="one").registration_map
    save_map(one_map, tmp_path / "one.npz")

    d = _write(tmp_path / "d.tif", np.stack([donor, donor]).astype(">u2"))
    a = _write(tmp_path / "a.tif", np.stack([acceptor, acceptor]).astype(">u2"))
    summary = extract_movie(
        d, tmp_path / "p.tether", acceptor_movie=a, map_path=tmp_path / "pair.npz"
    )
    assert summary.registration_source == "map-file"
    assert summary.n_control_points == pair_map.n_control_points

    with pytest.raises(ExtractionError, match="made for a movie pair"):
        extract_movie(
            _write(tmp_path / "one.tif", np.stack([np.hstack([donor, acceptor])] * 2)),
            tmp_path / "o.tether",
            map_path=tmp_path / "pair.npz",
        )
    with pytest.raises(ExtractionError, match="make the map from a bead file pair"):
        extract_movie(d, tmp_path / "q.tether", acceptor_movie=a, map_path=tmp_path / "one.npz")


def test_a_bead_file_pair_is_refused_when_it_cannot_be_one_acquisition(
    beads: tuple[np.ndarray, np.ndarray], tmp_path: Path
) -> None:
    donor, acceptor = beads
    d = _write(tmp_path / "bd.tif", donor)
    with pytest.raises(BeadMapError, match="are one file"):
        bead_map_pair_from_files(d, d)
    with pytest.raises(BeadMapError, match="differ in shape"):
        bead_map_pair_from_files(d, _write(tmp_path / "small.tif", acceptor[:, :200]))
    with pytest.raises(BeadMapError, match="each file is one whole channel"):
        bead_map(
            donor, source_id="d", acceptor_data=acceptor, acceptor_source_id="a", donor_side="right"
        )
    with pytest.raises(BeadMapError, match="needs acceptor_source_id"):
        bead_map(donor, source_id="d", acceptor_data=acceptor)
