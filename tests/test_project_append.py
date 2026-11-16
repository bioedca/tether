# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Append a movie into an existing ``.tether`` project (#347, ADR-0117).

Each test is one acceptance criterion of #347:

* a second movie grows ``/movies``, ``/molecules``, all six ``/traces`` arrays and both
  ``/patches`` arrays, and the **positional** trace↔molecule join holds for every row;
* the same movie appended again is refused, and ``write_extraction``'s write-once rule
  per ``movie_id`` still holds on an appended project;
* the append runs under the #346 destination lock, and a lock lost mid-append leaves
  the project byte-identical;
* a movie **longer** than every stored one widens every ``/traces`` array and the
  existing rows gain zero-padded columns;
* ``reconstruct_project``'s append path (``add_reconstruction_to_project``) builds one
  project from several Deep-LASI acquisitions, each pass scoped to its own molecules;
* ``/settings/extraction/movies`` records each movie's settings and the first
  extraction's stamp does not change.
"""

from __future__ import annotations

import json
import subprocess
import sys
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

from tether.imaging.extract import (  # noqa: E402
    MovieMetadata,
    molecule_key,
    read_movie_settings,
    write_extraction,
)
from tether.io.deeplasi import read_deeplasi_mat  # noqa: E402
from tether.io.tdat import TdatCorrections  # noqa: E402
from tether.project import conditions, lock  # noqa: E402
from tether.project.correct import METHOD_MANUAL  # noqa: E402
from tether.project.extract import (  # noqa: E402
    ExtractionError,
    ExtractOptions,
    add_movie_to_project,
    extract_movie,
)
from tether.project.reconstruct import (  # noqa: E402
    add_reconstruction_to_project,
    reconstruct_project,
)

_BG = 80.0
_AMP = 400.0
_SIGMA = 1.5
_SHAPE = (64, 96)  # 48-px-wide halves: room for the default 21-px aperture
_WINDOW = 21
_SHORT = 12
_LONG = 20

# Movie A and movie B place their spots differently, so their molecules differ.
_DONOR_A = np.array([[12.0, 12.0], [24.0, 40.0], [16.0, 52.0]])
_DONOR_B = np.array([[14.0, 20.0], [30.0, 30.0], [20.0, 48.0], [34.0, 14.0]])
_SHIFT = np.array([49.0, 0.0])  # acceptor copy: +1 px in x inside its own half

_TRACE_NAMES = tuple(
    f"{channel}_{quantity}"
    for channel in ("donor", "acceptor")
    for quantity in ("corrected", "raw", "background")
)
_FIXTURES = Path(__file__).parent / "fixtures"
_MAT = _FIXTURES / "deeplasi_export_slice.mat"


def _decode(value: object) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _write_movie(path: Path, donor: np.ndarray, n_frames: int, *, amp: float = _AMP) -> Path:
    """Write a dual-channel big-endian uint16 movie whose spots brighten frame by frame.

    The ramp makes every frame different, so a trace column can be traced back to the
    frame it came from.
    """
    centers = np.vstack([donor, donor + _SHIFT])
    rows, cols = np.mgrid[0 : _SHAPE[0], 0 : _SHAPE[1]]
    spots = np.zeros(_SHAPE)
    for x, y in centers:
        spots += np.exp(-((rows - y) ** 2 + (cols - x) ** 2) / (2.0 * _SIGMA**2))
    ramp = amp * (1.0 + 0.02 * np.arange(n_frames))
    stack = _BG + ramp[:, None, None] * spots[None]
    tifffile.imwrite(path, np.ascontiguousarray(stack, dtype=">u2"), byteorder=">")
    return path


def _movie_a(tmp_path: Path) -> Path:
    return _write_movie(tmp_path / "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif", _DONOR_A, _SHORT)


def _movie_b(tmp_path: Path, n_frames: int = _LONG) -> Path:
    return _write_movie(tmp_path / "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_011.tif", _DONOR_B, n_frames)


def _options(**overrides) -> ExtractOptions:
    return ExtractOptions(window=_WINDOW, **overrides)


def _project_with_a(tmp_path: Path) -> Path:
    out = tmp_path / "experiment.tether"
    extract_movie(_movie_a(tmp_path), out, options=_options())
    return out


def _append(project: Path, movie: Path, **kwargs):
    with lock.publish_lock(project) as guard:
        return add_movie_to_project(project, movie, publish_guard=guard, **kwargs)


def _read(project: Path) -> dict[str, np.ndarray]:
    with h5py.File(project, "r") as f:
        out = {"movies": f["movies/table"][:], "molecules": f["molecules/table"][:]}
        for name in _TRACE_NAMES:
            out[name] = f["traces"][name][:]
        out["patch_donor"] = f["patches/donor"][:]
        out["patch_acceptor"] = f["patches/acceptor"][:]
    return out


def _no_stage_left(project: Path) -> None:
    leftovers = sorted(p.name for p in project.parent.iterdir() if p.name.endswith(".tmp"))
    assert leftovers == []


# --- AC 1 + AC 4: the tables grow and the positional join holds ----------------


def test_a_second_movie_grows_every_table_and_keeps_the_join(tmp_path: Path) -> None:
    project = _project_with_a(tmp_path)
    before = _read(project)
    alone = tmp_path / "b-alone.tether"
    extract_movie(_movie_b(tmp_path), alone, options=_options())
    b_alone = _read(alone)

    summary = _append(project, _movie_b(tmp_path), options=_options())

    after = _read(project)
    n_a, n_b = before["molecules"].shape[0], b_alone["molecules"].shape[0]
    assert (n_a, n_b) == (3, 4)
    assert summary.output_path == project
    assert summary.n_molecules == n_b
    assert after["movies"].shape[0] == 2
    assert _decode(after["movies"]["movie_id"][1]) == summary.movie_id
    assert after["molecules"].shape[0] == n_a + n_b
    for name in (*_TRACE_NAMES, "patch_donor", "patch_acceptor"):
        assert after[name].shape[0] == n_a + n_b, name

    # Row i is one molecule everywhere: the first rows are movie A's, unchanged; the
    # new rows are movie B's, in the order an extraction of B alone writes them.
    mols = after["molecules"]
    assert {_decode(v) for v in mols["movie_id"][n_a:]} == {summary.movie_id}
    np.testing.assert_array_equal(mols["molecule_key"][:n_a], before["molecules"]["molecule_key"])
    np.testing.assert_array_equal(mols["molecule_key"][n_a:], b_alone["molecules"]["molecule_key"])
    np.testing.assert_array_equal(mols["donor_xy"][n_a:], b_alone["molecules"]["donor_xy"])
    for name in _TRACE_NAMES:
        np.testing.assert_array_equal(after[name][n_a:], b_alone[name], err_msg=name)
    for name in ("patch_donor", "patch_acceptor"):
        np.testing.assert_array_equal(after[name][:n_a], before[name], err_msg=name)
        np.testing.assert_array_equal(after[name][n_a:], b_alone[name], err_msg=name)
    sha_b = _decode(after["movies"]["sha256"][1])
    for i in range(n_a, n_a + n_b):
        assert _decode(mols["molecule_key"][i]) == molecule_key(sha_b, mols["donor_xy"][i])
    # The movie has its own calibration.
    with h5py.File(project, "r") as f:
        assert summary.calibration_id in f["calibration"]
        assert len(f["calibration"]) == 2


def test_a_longer_movie_zero_pads_every_existing_row(tmp_path: Path) -> None:
    project = _project_with_a(tmp_path)
    before = _read(project)
    assert before["donor_corrected"].shape[1] == _SHORT

    _append(project, _movie_b(tmp_path, _LONG), options=_options())

    after = _read(project)
    n_a = before["molecules"].shape[0]
    for name in _TRACE_NAMES:
        assert after[name].shape[1] == _LONG, name
        np.testing.assert_array_equal(after[name][:n_a, :_SHORT], before[name], err_msg=name)
        assert not after[name][:n_a, _SHORT:].any(), name  # the zero-pad
        assert after[name][n_a:, :].any(), name
    frame_range = after["molecules"]["frame_range"]
    assert (frame_range[:n_a] == [0, _SHORT]).all()  # the valid extent is unchanged
    assert (frame_range[n_a:] == [0, _LONG]).all()


def test_a_shorter_movie_is_zero_padded_to_the_project_width(tmp_path: Path) -> None:
    project = tmp_path / "long-first.tether"
    extract_movie(_movie_b(tmp_path, _LONG), project, options=_options())

    _append(project, _movie_a(tmp_path), options=_options())

    after = _read(project)
    n_b = 4
    for name in _TRACE_NAMES:
        assert after[name].shape[1] == _LONG, name
        assert not after[name][n_b:, _SHORT:].any(), name
    assert (after["molecules"]["frame_range"][n_b:] == [0, _SHORT]).all()


# --- AC 2: write-once ------------------------------------------------------------


def test_the_same_movie_twice_is_refused_and_changes_nothing(tmp_path: Path) -> None:
    project = _project_with_a(tmp_path)
    original = project.read_bytes()

    with pytest.raises(ExtractionError, match="already in"):
        _append(project, _movie_a(tmp_path), options=_options())

    assert project.read_bytes() == original
    _no_stage_left(project)


def test_write_extraction_stays_write_once_per_movie_id(tmp_path: Path) -> None:
    """The append goes through ``write_extraction``; its own rule is not bypassed."""
    project = _project_with_a(tmp_path)
    summary = _append(project, _movie_b(tmp_path), options=_options())
    with h5py.File(project, "r") as f:
        row = f["movies/table"][1]
    movie = MovieMetadata(
        movie_id=summary.movie_id,
        sha256="f" * 64,
        n_frames=int(row["n_frames"]),
        height=int(row["height"]),
        width=int(row["width"]),
    )
    from tether.imaging.coloc import ColocalizedMolecules
    from tether.imaging.extract import extract_molecules
    from tether.io.filename import parse_filename

    empty = ColocalizedMolecules(
        donor_xy=np.zeros((0, 2)),
        acceptor_xy=np.zeros((0, 2)),
        acceptor_detected=np.zeros(0, dtype=bool),
        donor_index=np.zeros(0, dtype=np.int64),
        acceptor_index=np.zeros(0, dtype=np.int64),
    )
    stack = np.zeros((int(row["n_frames"]), 64, 48))
    traces = extract_molecules(stack, stack, empty, window=_WINDOW)
    with pytest.raises(ValueError, match="write-once"):
        write_extraction(
            project, movie=movie, molecules=empty, traces=traces, parsed=parse_filename("x.tif")
        )


# --- AC 3: the destination lock ------------------------------------------------------


def test_an_append_without_the_writer_lock_is_refused(tmp_path: Path) -> None:
    project = _project_with_a(tmp_path)
    original = project.read_bytes()

    with pytest.raises(ExtractionError, match="writer lock"):
        add_movie_to_project(project, _movie_b(tmp_path), options=_options(), publish_guard=None)

    assert project.read_bytes() == original


def test_a_project_another_writer_holds_cannot_be_appended(tmp_path: Path) -> None:
    project = _project_with_a(tmp_path)
    original = project.read_bytes()
    lock.acquire(project, identity=lock.LockIdentity(host="OTHER", user="bob", pid=999))

    with pytest.raises(lock.LockedError):
        _append(project, _movie_b(tmp_path), options=_options())

    assert project.read_bytes() == original


def test_a_lock_lost_mid_append_leaves_the_project_byte_identical(tmp_path: Path) -> None:
    """Another machine steals the lock while the append writes its copy (#346)."""
    project = _project_with_a(tmp_path)
    original = project.read_bytes()
    steal = (
        "import sys\n"
        "from tether.project import lock\n"
        "other = lock.LockIdentity(host='OTHER', user='bob', pid=9)\n"
        "lock.steal_lock(sys.argv[1], identity=other)\n"
    )
    calls: list[str] = []

    with lock.publish_lock(project) as guard:

        def guard_then_lose_the_lock() -> None:
            calls.append("guard")
            if len(calls) == 2:  # after the copy is made: the write is under way
                subprocess.run([sys.executable, "-c", steal, str(project)], check=True)
            guard()

        with pytest.raises(ExtractionError, match="failed to append"):
            add_movie_to_project(
                project,
                _movie_b(tmp_path),
                options=_options(),
                publish_guard=guard_then_lose_the_lock,
            )

    assert len(calls) >= 2
    assert project.read_bytes() == original
    _no_stage_left(project)


def test_aperture_parameters_must_match_the_project(tmp_path: Path) -> None:
    project = _project_with_a(tmp_path)
    original = project.read_bytes()

    with pytest.raises(ExtractionError, match="extraction parameters differ"):
        _append(project, _movie_b(tmp_path), options=_options(bg_window=5))

    assert project.read_bytes() == original
    _no_stage_left(project)


def test_a_missing_project_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ExtractionError, match="project not found"):
        add_movie_to_project(
            tmp_path / "absent.tether", _movie_b(tmp_path), publish_guard=lambda: None
        )


# --- AC 6: per-movie settings ------------------------------------------------------


def test_each_movie_records_its_settings_and_the_first_stamp_stays(tmp_path: Path) -> None:
    project = _project_with_a(tmp_path)
    with h5py.File(project, "r") as f:
        stamp_before = dict(f["settings/extraction"].attrs)
    first = read_movie_settings(project)
    assert len(first) == 1

    summary = _append(project, _movie_b(tmp_path), options=_options(pair_tol=2.5))

    with h5py.File(project, "r") as f:
        stamp_after = dict(f["settings/extraction"].attrs)
    assert stamp_after.keys() == stamp_before.keys()
    for key, value in stamp_before.items():
        assert stamp_after[key] == value, key
    assert json.loads(_decode(stamp_after["profile_json"]))["pair_tol"] == 2.0

    per_movie = read_movie_settings(project)
    assert set(per_movie) == {*first, summary.movie_id}
    assert per_movie[summary.movie_id]["pair_tol"] == 2.5
    (first_id,) = first
    assert per_movie[first_id] == first[first_id]
    assert per_movie[first_id]["pair_tol"] == 2.0


def test_a_project_from_before_per_movie_records_reads_as_empty(tmp_path: Path) -> None:
    project = _project_with_a(tmp_path)
    with h5py.File(project, "r+") as f:
        del f["settings/extraction/movies"]
    assert read_movie_settings(project) == {}


# --- AC 5: the reconstruction append path -------------------------------------------


def _recon_movie(movie_id: str, sha256: str, n_frames: int) -> MovieMetadata:
    return MovieMetadata(
        movie_id=movie_id,
        sha256=sha256,
        n_frames=n_frames,
        height=512,
        width=256,
        uri=f"{movie_id}.tif",
        pixel_dtype="uint16",
        byteorder=">",
    )


def test_deeplasi_acquisitions_append_into_one_project(tmp_path: Path) -> None:
    export = read_deeplasi_mat(_MAT)
    project = tmp_path / "reanalysis.tether"
    reconstruct_project(
        project,
        export=export,
        movie=_recon_movie("mov-a", "a" * 64, export.n_frames),
        corrections=TdatCorrections(0.0, 0.05, 1.2, 0.05, 0.0, 1.2),
        categories=("dynamic", "static", "noise"),
    )
    with h5py.File(project, "r+") as f:  # a curator's edit the append must keep
        table = f["molecules/table"][:]
        table["bleach_frames"] = (3, 4)
        f["molecules/table"][:] = table
    before = _read(project)

    with lock.publish_lock(project) as guard:
        summary = add_reconstruction_to_project(
            project,
            export=export,
            movie=_recon_movie("mov-b", "b" * 64, export.n_frames),
            corrections=TdatCorrections(0.0, 0.10, 0.8, 0.10, 0.0, 0.8),
            categories=("static", "stuck"),
            publish_guard=guard,
        )

    after = _read(project)
    n = export.n_molecules
    assert summary.output_path == project
    assert summary.movie_id == "mov-b"
    assert after["movies"].shape[0] == 2
    mols = after["molecules"]
    assert mols.shape[0] == 2 * n
    assert [_decode(v) for v in mols["movie_id"]] == ["mov-a"] * n + ["mov-b"] * n
    for name in _TRACE_NAMES:
        np.testing.assert_array_equal(after[name][:n], before[name], err_msg=name)
        np.testing.assert_array_equal(after[name][n:], before[name], err_msg=name)
    # Each acquisition keeps its own factors; the first movie's rows are untouched.
    for column in ("alpha", "gamma", "correction_method", "bleach_frames", "analysis_window"):
        np.testing.assert_array_equal(mols[column][:n], before["molecules"][column], column)
    np.testing.assert_allclose(mols["alpha"][n:], 0.10)
    np.testing.assert_allclose(mols["gamma"][n:], 0.8)
    assert {_decode(v) for v in mols["correction_method"][n:]} == {METHOD_MANUAL}
    assert not (mols["bleach_frames"][n:] == (3, 4)).all()
    # Category codes the first movie uses keep their place; new names go after them.
    condition_id = _decode(mols["condition_id"][0])
    assert conditions.read_category_list(project, condition_id).categories == (
        "dynamic",
        "static",
        "noise",
        "stuck",
    )
    assert set(read_movie_settings(project)) == {"mov-a", "mov-b"}


def test_a_reconstruction_append_refuses_a_duplicate_movie(tmp_path: Path) -> None:
    export = read_deeplasi_mat(_MAT)
    project = tmp_path / "reanalysis.tether"
    reconstruct_project(
        project, export=export, movie=_recon_movie("mov-a", "a" * 64, export.n_frames)
    )
    original = project.read_bytes()

    for movie in (
        _recon_movie("mov-a", "c" * 64, export.n_frames),  # same id
        _recon_movie("mov-c", "A" * 64, export.n_frames),  # same content
    ):
        with lock.publish_lock(project) as guard, pytest.raises(ValueError, match="already in"):
            add_reconstruction_to_project(project, export=export, movie=movie, publish_guard=guard)
    assert project.read_bytes() == original


def test_a_reconstruction_append_needs_the_writer_lock(tmp_path: Path) -> None:
    export = read_deeplasi_mat(_MAT)
    project = tmp_path / "reanalysis.tether"
    reconstruct_project(
        project, export=export, movie=_recon_movie("mov-a", "a" * 64, export.n_frames)
    )
    original = project.read_bytes()

    with pytest.raises(lock.PublishGuardRequiredError):
        add_reconstruction_to_project(
            project,
            export=export,
            movie=_recon_movie("mov-b", "b" * 64, export.n_frames),
            publish_guard=None,
        )
    assert project.read_bytes() == original

    def lost() -> None:
        raise lock.LockError("the writer lock was stolen")

    with pytest.raises(lock.LockError, match="stolen"):
        add_reconstruction_to_project(
            project,
            export=export,
            movie=_recon_movie("mov-b", "b" * 64, export.n_frames),
            publish_guard=lost,
        )
    assert project.read_bytes() == original
    _no_stage_left(project)


# --- targets whose arrays do not cover every molecule (Greptile P1 on #1119) ----------


def _analysis_only_project(tmp_path: Path) -> Path:
    from tether.idealize import read_smd
    from tether.project.analysis_import import import_analysis_only_project

    out = tmp_path / "analysis-only.tether"
    import_analysis_only_project(out, source=read_smd(_FIXTURES / "smd_4mol.hdf5"))
    return out


def _subset_without_raw(tmp_path: Path) -> Path:
    from tether.project.export import export_subset_tether

    source = _project_with_a(tmp_path)
    out = tmp_path / "subset.tether"
    export_subset_tether(source, out, include_rejected=True)  # include_raw=False
    with h5py.File(out, "r") as f:
        assert "donor_raw" not in f["traces"]  # the premise: raw layers left out
    return out


@pytest.mark.parametrize("make_target", [_analysis_only_project, _subset_without_raw])
def test_a_store_missing_row_aligned_arrays_cannot_take_a_movie(tmp_path, make_target) -> None:
    project = make_target(tmp_path)
    original = project.read_bytes()

    with pytest.raises(ExtractionError, match="could not stay aligned"):
        _append(project, _movie_b(tmp_path), options=_options())
    with (
        lock.publish_lock(project) as guard,
        pytest.raises(ValueError, match="could not stay aligned"),
    ):
        export = read_deeplasi_mat(_MAT)
        add_reconstruction_to_project(
            project,
            export=export,
            movie=_recon_movie("mov-b", "b" * 64, export.n_frames),
            publish_guard=guard,
        )

    assert project.read_bytes() == original
    _no_stage_left(project)


def test_write_extraction_refuses_a_store_with_a_misaligned_array(tmp_path: Path) -> None:
    """The writer itself checks, so no caller can misalign the join (reject-before-mutate)."""
    from tether.imaging.extract import assert_appendable

    project = _project_with_a(tmp_path)
    with h5py.File(project, "r+") as f:
        del f["traces/donor_background"]
    original = project.read_bytes()

    with pytest.raises(ValueError, match="could not stay aligned"):
        assert_appendable(project)
    with pytest.raises(ValueError, match="could not stay aligned"):
        _append_raw_extraction(project, tmp_path)
    assert project.read_bytes() == original


def _append_raw_extraction(project: Path, tmp_path: Path) -> None:
    """Call ``write_extraction`` directly with one real molecule of movie B."""
    from tether.project.extract import _resolve_sources, _run_pipeline

    movie_path = _movie_b(tmp_path)
    options, imported = _resolve_sources(_options(), None, None)
    extracted = _run_pipeline(movie_path, options, imported)
    movie = MovieMetadata(
        movie_id="mov-direct",
        sha256=extracted.sha256,
        n_frames=extracted.n_frames,
        height=extracted.height,
        width=extracted.width,
    )
    from tether.io.filename import parse_filename

    write_extraction(
        project,
        movie=movie,
        molecules=extracted.molecules,
        traces=extracted.traces,
        parsed=parse_filename(movie_path.name),
    )


# --- category seeds that differ only by whitespace (Greptile P2 on #1119) ------------


def test_a_seed_that_differs_only_by_whitespace_is_the_same_category(tmp_path: Path) -> None:
    export = read_deeplasi_mat(_MAT)
    project = tmp_path / "reanalysis.tether"
    reconstruct_project(
        project,
        export=export,
        movie=_recon_movie("mov-a", "a" * 64, export.n_frames),
        categories=("dynamic", "static"),
    )

    with lock.publish_lock(project) as guard:
        add_reconstruction_to_project(
            project,
            export=export,
            movie=_recon_movie("mov-b", "b" * 64, export.n_frames),
            categories=("static ", " stuck"),
            publish_guard=guard,
        )

    condition_id = _decode(_read(project)["molecules"]["condition_id"][0])
    assert conditions.read_category_list(project, condition_id).categories == (
        "dynamic",
        "static",
        "stuck",
    )
