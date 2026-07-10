# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for Deep-LASI acquisition intake (PRD §7.8, §9 M7).

Exercises the headless multi-file discovery + movie-pairing core on synthetic
folders that mirror the real ``example-data/`` Deep-LASI naming (PRD Appendix A):
the four core roles of one acquisition (``.tif`` / ``.tdat`` / ``.mat`` / ``.txt``)
share a filename stem, the ``.tmap`` is session/day-scoped, and the SMD pairs by
video index. A ``large``-marked test runs the same discovery over the actual
``example-data`` bundle when it is present.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import tether.io.intake as intake_module
from tether.io import (
    AcquisitionFileSet,
    MovieReference,
    classify_file,
    discover_acquisitions,
    read_mat_movie_reference,
    verify_movie_reference,
)

# Real filenames from example-data/ (PRD Appendix A) — the one UCKOPSB acquisition.
MOVIE = "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif"
TDAT = "DeepLASI_DATA_Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif2025-07-21_00-00.tdat"
MAT = "DeepLASI_MAT_export_Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.mat"
TXT = "DeepLASI_MAT_export_Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010-donc-accc-w.txt"
TMAP = "DeepLASI_MAP_Bla_UCKOPSB_T-box_35pM_tRNA_600nM_20250718_2025-07-18_13-40.tmap"
SMD = "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.hdf5"

# A second acquisition (video 011) in the same condition — shares the .tmap.
MOVIE_011 = "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_011.tif"
TDAT_011 = "DeepLASI_DATA_Bla_UCKOPSB_T-box_35pM_tRNA_600nM_011.tif2025-07-21_01-00.tdat"

# A *different condition* (300 nM tRNA) sharing the SAME video index 010 — its key
# differs, so its condition_id and stem differ while its video index collides.
MOVIE_300 = "Bla_UCKOPSB_T-box_35pM_tRNA_300nM_010.tif"
TDAT_300 = "DeepLASI_DATA_Bla_UCKOPSB_T-box_35pM_tRNA_300nM_010.tif2025-07-21_00-00.tdat"
TMAP_300 = "DeepLASI_MAP_Bla_UCKOPSB_T-box_35pM_tRNA_300nM_20250718_2025-07-18_13-40.tmap"
# A second same-condition (600 nM) map from a different day — same condition_id.
TMAP_D2 = "DeepLASI_MAP_Bla_UCKOPSB_T-box_35pM_tRNA_600nM_20250719_2025-07-19_14-00.tmap"

ACQ_STEM = "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010"
ACQ_STEM_300 = "Bla_UCKOPSB_T-box_35pM_tRNA_300nM_010"


def _touch(directory: Path, *names: str) -> None:
    """Create empty files with the given names (intake reads names, not contents)."""
    for name in names:
        (directory / name).touch()


# --- classify_file -----------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "role"),
    [
        (MOVIE, "movie"),
        ("movie.TIFF", "movie"),
        (TDAT, "tdat"),  # the mid-name '.tif' must not win over the final '.tdat'
        (TMAP, "tmap"),
        (MAT, "mat"),
        (TXT, "txt"),
        (SMD, "smd"),
        ("trace.h5", "smd"),
        ("notes.log", "unknown"),
        ("README.md", "unknown"),
    ],
)
def test_classify_file(name: str, role: str) -> None:
    assert classify_file(name) == role


# --- discovery + grouping ----------------------------------------------------


def test_single_acquisition_pairs_core_roles(tmp_path: Path) -> None:
    _touch(tmp_path, MOVIE, TDAT, MAT, TXT, TMAP)
    result = discover_acquisitions(tmp_path)

    assert len(result.acquisitions) == 1
    acq = result.acquisitions[0]
    assert acq.key == ACQ_STEM
    assert acq.video_index == "010"
    assert acq.movie == tmp_path / MOVIE
    assert acq.tdat == tmp_path / TDAT
    assert acq.mat == tmp_path / MAT
    assert acq.txt == tmp_path / TXT
    assert acq.round_trip_available
    assert not acq.analysis_only
    assert acq.warnings == ()
    # The .tmap is session-scoped: surfaced at the result level *and* offered to the
    # same-condition acquisition, but never grouped as a per-acquisition role.
    assert result.shared_maps == (tmp_path / TMAP,)
    assert acq.shared_maps == (tmp_path / TMAP,)


def test_smd_pairs_by_video_index(tmp_path: Path) -> None:
    _touch(tmp_path, MOVIE, TDAT, SMD)
    result = discover_acquisitions(tmp_path)

    assert len(result.acquisitions) == 1
    assert result.acquisitions[0].smd == tmp_path / SMD
    assert result.unpaired == ()


def test_smd_unpaired_when_name_carries_no_video_index(tmp_path: Path) -> None:
    # example-data ships the SMD as 'video10.hdf5' — no confident filename link to
    # acquisition '010', so it is left unpaired (SMD↔movie pairing is by molecule
    # index + intensity match, a later M7 concern), never silently misattached.
    _touch(tmp_path, MOVIE, TDAT, "video10.hdf5")
    result = discover_acquisitions(tmp_path)

    assert result.acquisitions[0].smd is None
    assert result.unpaired == (tmp_path / "video10.hdf5",)


def test_multiple_acquisitions_grouped_separately(tmp_path: Path) -> None:
    _touch(tmp_path, MOVIE, TDAT, MOVIE_011, TDAT_011, TMAP)
    result = discover_acquisitions(tmp_path)

    assert [a.video_index for a in result.acquisitions] == ["010", "011"]  # sorted by key
    for acq in result.acquisitions:
        assert acq.round_trip_available
        assert acq.shared_maps == (tmp_path / TMAP,)  # one condition → shared map


def test_movie_absent_disables_round_trip_and_warns(tmp_path: Path) -> None:
    _touch(tmp_path, TDAT, MAT)  # coordinates present, but no movie to link them
    (acq,) = discover_acquisitions(tmp_path).acquisitions
    assert acq.has_coordinate_source
    assert not acq.has_movie
    assert not acq.round_trip_available
    assert any("no raw movie" in w for w in acq.warnings)


def test_txt_only_is_analysis_only(tmp_path: Path) -> None:
    _touch(tmp_path, TXT)  # intensities only, no coordinates
    (acq,) = discover_acquisitions(tmp_path).acquisitions
    assert acq.analysis_only
    assert not acq.round_trip_available
    assert not acq.has_coordinate_source


def test_movie_without_coordinate_source_warns(tmp_path: Path) -> None:
    _touch(tmp_path, MOVIE)  # a bare movie: cannot recover coordinates
    (acq,) = discover_acquisitions(tmp_path).acquisitions
    assert acq.has_movie
    assert acq.analysis_only
    assert any("analysis-only" in w for w in acq.warnings)


def test_unknown_files_are_ignored_not_dropped(tmp_path: Path) -> None:
    _touch(tmp_path, MOVIE, TDAT, "lab_notes.log", "thumbnail.png")
    result = discover_acquisitions(tmp_path)
    assert len(result.acquisitions) == 1
    assert set(result.ignored) == {tmp_path / "lab_notes.log", tmp_path / "thumbnail.png"}


def test_duplicate_role_warns_and_picks_first(tmp_path: Path) -> None:
    dup = "DeepLASI_DATA_Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif2025-07-22_09-00.tdat"
    _touch(tmp_path, MOVIE, TDAT, dup)
    (acq,) = discover_acquisitions(tmp_path).acquisitions
    assert acq.tdat == tmp_path / min(TDAT, dup)  # first by sorted name
    assert any("multiple tdat" in w for w in acq.warnings)


def test_recursive_scan(tmp_path: Path) -> None:
    sub = tmp_path / "acq010"
    sub.mkdir()
    _touch(sub, MOVIE, TDAT)
    assert discover_acquisitions(tmp_path).acquisitions == ()  # non-recursive by default
    result = discover_acquisitions(tmp_path, recursive=True)
    assert len(result.acquisitions) == 1
    assert result.acquisitions[0].movie == sub / MOVIE


def test_missing_directory_raises(tmp_path: Path) -> None:
    with pytest.raises(NotADirectoryError):
        discover_acquisitions(tmp_path / "does-not-exist")


def test_smd_pairs_by_exact_stem_over_colliding_video_index(tmp_path: Path) -> None:
    # Two acquisitions share video index '010' but have distinct stems/conditions.
    # An SMD whose stem exactly matches the 600 nM set must attach there — NOT to the
    # 300 nM set that would win a video-index-only tie-break (it sorts first).
    _touch(tmp_path, MOVIE, TDAT, MOVIE_300, TDAT_300, SMD)
    acqs = discover_acquisitions(tmp_path).acquisitions
    assert [a.key for a in acqs] == [ACQ_STEM_300, ACQ_STEM]  # 300 sorts before 600
    assert acqs[0].smd is None  # the 300 set does not steal the 600's SMD
    assert acqs[1].smd == tmp_path / SMD  # exact stem match wins


def test_second_smd_for_same_acquisition_is_unpaired(tmp_path: Path) -> None:
    # Two SMDs both resolve (by video index) to the one acquisition '010'; exactly one
    # attaches and the other is left unpaired — never silently overwritten.
    _touch(tmp_path, MOVIE, TDAT, "aaa_010.hdf5", "bbb_010.hdf5")
    result = discover_acquisitions(tmp_path)
    assert result.acquisitions[0].smd == tmp_path / "aaa_010.hdf5"  # first by sorted name
    assert result.unpaired == (tmp_path / "bbb_010.hdf5",)


def test_smd_with_ambiguous_video_index_is_unpaired(tmp_path: Path) -> None:
    # Two acquisitions (600 nM and 300 nM) share video index '010'. An SMD whose stem
    # matches neither and carries only that colliding index is *ambiguous*: it must be
    # left unpaired, never bolted onto an arbitrary one of the two candidates.
    _touch(tmp_path, MOVIE, TDAT, MOVIE_300, TDAT_300, "unrelated_010.hdf5")
    result = discover_acquisitions(tmp_path)
    assert [a.key for a in result.acquisitions] == [ACQ_STEM_300, ACQ_STEM]
    assert all(a.smd is None for a in result.acquisitions)  # neither acquisition claims it
    assert result.unpaired == (tmp_path / "unrelated_010.hdf5",)


def test_shared_map_of_other_condition_not_attached(tmp_path: Path) -> None:
    # A .tmap for the 300 nM condition in a 600 nM-only folder is surfaced at the
    # result level but NOT attached to the mismatched acquisition.
    _touch(tmp_path, MOVIE, TDAT, TMAP_300)
    result = discover_acquisitions(tmp_path)
    (acq,) = result.acquisitions
    assert result.shared_maps == (tmp_path / TMAP_300,)
    assert acq.shared_maps == ()  # condition mismatch → not offered to this acquisition


def test_multiple_same_condition_maps_all_attached_sorted(tmp_path: Path) -> None:
    _touch(tmp_path, MOVIE, TDAT, TMAP, TMAP_D2)
    (acq,) = discover_acquisitions(tmp_path).acquisitions
    expected = tuple(sorted((tmp_path / TMAP, tmp_path / TMAP_D2), key=lambda p: p.as_posix()))
    assert acq.shared_maps == expected
    assert len(acq.shared_maps) == 2


def test_round_trip_ready_filters_to_reconstructable(tmp_path: Path) -> None:
    # One round-trip-ready acquisition (010: movie + tdat) plus one movie-absent
    # acquisition (011: tdat only) → only the ready one is round_trip_ready.
    _touch(tmp_path, MOVIE, TDAT, TDAT_011)
    result = discover_acquisitions(tmp_path)
    assert len(result.acquisitions) == 2
    assert [a.key for a in result.round_trip_ready] == [ACQ_STEM]


def test_files_lists_roles_then_shared_maps_in_order(tmp_path: Path) -> None:
    m, t, mp = Path("/d") / MOVIE, Path("/d") / TDAT, Path("/d") / TMAP
    fs = _fileset(movie=m, tdat=t, shared_maps=(mp,))
    assert fs.files() == (m, t, mp)  # movie, tdat (mat/txt/smd None skipped), then maps
    assert _fileset(txt=Path("/d") / TXT).files() == (Path("/d") / TXT,)


# --- movie-reference cross-check ---------------------------------------------


def _fileset(**kw: object) -> AcquisitionFileSet:
    base: dict[str, object] = {"key": ACQ_STEM, "condition_id": "cond-x", "video_index": "010"}
    base.update(kw)
    return AcquisitionFileSet(**base)  # type: ignore[arg-type]


def test_verify_movie_reference_confirmed() -> None:
    fs = _fileset(movie=Path("/data") / MOVIE)
    ref = MovieReference(name=MOVIE, path="/exporter/machine", source="mat")
    check = verify_movie_reference(fs, ref)
    assert check.status == "confirmed"
    assert check.found == MOVIE and check.expected == MOVIE


def test_verify_movie_reference_compares_basename_only() -> None:
    # A Deep-LASI .mat records a Windows path on the exporter's machine; only the
    # basename matters, and it must split on '\' even on a POSIX CI runner.
    fs = _fileset(movie=Path("/my/local/copy") / MOVIE)
    ref = MovieReference(name=f"D:\\rig\\{MOVIE}", path="D:\\rig", source="mat")
    assert verify_movie_reference(fs, ref).status == "confirmed"


def test_verify_movie_reference_case_insensitive() -> None:
    # Windows/macOS filesystems are case-insensitive, so a reference recorded as
    # '..._010.TIF' is the same file as the grouped '..._010.tif'.
    fs = _fileset(movie=Path("/data") / MOVIE)
    ref = MovieReference(name=MOVIE.replace(".tif", ".TIF"), path="", source="mat")
    assert verify_movie_reference(fs, ref).status == "confirmed"


def test_verify_movie_reference_mismatch() -> None:
    fs = _fileset(movie=Path("/data") / MOVIE)
    ref = MovieReference(name="Some_Other_Movie_099.tif", path="", source="mat")
    check = verify_movie_reference(fs, ref)
    assert check.status == "mismatch"
    assert "099" in check.message


def test_verify_movie_reference_movie_absent() -> None:
    fs = _fileset(movie=None, tdat=Path("/data") / TDAT)
    ref = MovieReference(name=MOVIE, path="", source="tdat")
    check = verify_movie_reference(fs, ref)
    assert check.status == "movie_absent"
    assert check.expected == MOVIE and check.found == ""


def test_verify_movie_reference_none() -> None:
    fs = _fileset(movie=Path("/data") / MOVIE)
    assert verify_movie_reference(fs, None).status == "no_reference"
    # An empty embedded name is treated the same as no reference.
    assert verify_movie_reference(fs, MovieReference("", "", "mat")).status == "no_reference"


# --- read_mat_movie_reference (thin wrapper over read_deeplasi_mat) -----------


def test_read_mat_movie_reference_none_without_mat() -> None:
    assert read_mat_movie_reference(_fileset(movie=Path("/data") / MOVIE)) is None


def test_read_mat_movie_reference_reads_export(monkeypatch: pytest.MonkeyPatch) -> None:
    # Patch where the reader is used (intake imports it at module scope), passing the
    # module object directly so the stub intercepts the call regardless of how the
    # package is installed / imported.
    fake = SimpleNamespace(movie_name=MOVIE, movie_path="/exporter/rig")
    monkeypatch.setattr(intake_module, "read_deeplasi_mat", lambda _p: fake)
    ref = read_mat_movie_reference(_fileset(mat=Path("/data") / MAT))
    assert ref == MovieReference(name=MOVIE, path="/exporter/rig", source="mat")


def test_read_mat_movie_reference_none_when_name_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = SimpleNamespace(movie_name="", movie_path="")
    monkeypatch.setattr(intake_module, "read_deeplasi_mat", lambda _p: fake)
    assert read_mat_movie_reference(_fileset(mat=Path("/data") / MAT)) is None


# --- real example-data integration (gated; not in the required matrix) --------

# ``example-data/`` is a read-only sibling of the repo under ``smfret-references/``
# (never committed into Tether), so it resolves from the repo's *parent*, not the
# repo root: tests/ → repo root (parents[1]) → smfret-references/ (parents[2]).
_EXAMPLE_DATA = Path(__file__).resolve().parents[2] / "example-data" / "bla-uckopsb-tbox-video10"


@pytest.mark.large
@pytest.mark.skipif(not _EXAMPLE_DATA.is_dir(), reason="example-data bundle not present")
def test_discovers_real_example_data_bundle() -> None:
    result = discover_acquisitions(_EXAMPLE_DATA)
    assert len(result.acquisitions) == 1
    acq = result.acquisitions[0]
    assert acq.movie is not None and acq.movie.suffix == ".tif"
    assert acq.tdat is not None and acq.mat is not None and acq.txt is not None
    assert acq.round_trip_available
    assert len(result.shared_maps) == 1  # the DeepLASI_MAP_*.tmap
    # The embedded .mat movie reference confirms the filename-stem pairing.
    ref = read_mat_movie_reference(acq)
    assert ref is not None
    assert verify_movie_reference(acq, ref).status == "confirmed"
