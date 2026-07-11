# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Analysis-only import of a coordinate-less SMD / ``.txt`` source (M7, PRD §7.8 / §9 M7).

Drives :func:`tether.project.analysis_import.import_analysis_only_project` on the
committed ``smd_4mol.hdf5`` (a raw ``.txt``-sourced tMAVEN SMD: ``has_superset`` False,
so no coordinates) and the bare ``.txt`` slice, proving the §9 M7 analysis-only clause
(PRD line ~730): an SMD/``.txt`` source with no coordinate source imports as a degraded
**analysis-only** project — the trace↔movie round-trip is disabled and every molecule is
tagged ``round-trip-unavailable``, yet the analysis substrate (FRET histogram / the
idealization inputs) still works.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("h5py")
pytest.importorskip("numpy")

import h5py
import numpy as np

from tether.analysis import population_apparent_e_histogram
from tether.idealize import read_smd
from tether.io.deeplasi import read_deeplasi_txt
from tether.io.schema import create_project
from tether.project import conditions, lock
from tether.project.analysis_import import (
    ANALYSIS_ONLY_BANNER,
    ANALYSIS_ONLY_TAG,
    AnalysisOnlyImportSummary,
    import_analysis_only_project,
    read_analysis_only_marker,
)
from tether.project.correct import METHOD_APPARENT_UNAVAILABLE
from tether.project.labels import CurationLabel

_FIXTURES = Path(__file__).parent / "fixtures"
_SMD4 = _FIXTURES / "smd_4mol.hdf5"
_TXT = _FIXTURES / "deeplasi_traces_slice.txt"
_SMD281 = _FIXTURES / "large" / "smd_281mol.hdf5"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _read_molecules(path: Path) -> np.ndarray:
    with h5py.File(path, "r") as f:
        return f["molecules"]["table"][:]


def _decode(value: object) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


# --------------------------------------------------------------------------- #
# §9 M7 acceptance — the analysis-only import
# --------------------------------------------------------------------------- #


def test_smd_import_is_analysis_only(tmp_path: Path) -> None:
    """An SMD with no coordinate source imports movie-less, round-trip-disabled (§7.8)."""
    smd = read_smd(_SMD4)
    assert smd.has_superset is False  # guards the premise: a coordinate-less SMD
    out = tmp_path / "ao.tether"

    summary = import_analysis_only_project(out, source=smd, source_name="smd_4mol.hdf5")

    assert isinstance(summary, AnalysisOnlyImportSummary)
    assert summary.n_molecules == smd.n_molecules == 4
    assert summary.n_frames == smd.n_frames == 1700
    assert summary.source_kind == "smd"
    assert summary.banner == ANALYSIS_ONLY_BANNER

    mols = _read_molecules(out)
    assert mols.shape[0] == 4
    # movie-less: no /movies row, every molecule's movie_id is empty
    assert {_decode(v) for v in mols["movie_id"]} == {""}
    with h5py.File(out, "r") as f:
        assert f["movies"]["table"].shape[0] == 0  # movie-less store (§5.4)
        assert list(f["patches"].keys()) == []  # patches absent
        # only the corrected (apparent-E) trace layers are written — no raw/background
        assert set(f["traces"].keys()) == {"donor_corrected", "acceptor_corrected"}
        assert f["traces"]["donor_corrected"].shape == (4, 1700)
        # the layers hold the SMD's donor/acceptor intensities (right channel + values,
        # not just shape — a zero-fill or channel-swap would fail this). float32 store.
        np.testing.assert_array_equal(
            f["traces"]["donor_corrected"][:], smd.raw[:, :, 0].astype(np.float32)
        )
        np.testing.assert_array_equal(
            f["traces"]["acceptor_corrected"][:], smd.raw[:, :, 1].astype(np.float32)
        )
    # coordinates are genuinely absent (a NaN sentinel, never a fabricated [0, 0])
    assert np.isnan(mols["donor_xy"]).all()
    assert np.isnan(mols["acceptor_xy"]).all()
    # every molecule is tagged round-trip-unavailable (§7.8 provenance)
    assert {_decode(v) for v in mols["tags"]} == {ANALYSIS_ONLY_TAG}
    # the SMD is the curated subset (no accept/reject mask) → nothing is curated-in/out
    assert set(mols["curation_label"].tolist()) == {int(CurationLabel.UNCURATED)}
    # a distinct molecule_key per molecule (the coord-less collision trap avoided)
    keys = [_decode(v) for v in mols["molecule_key"]]
    assert len(set(keys)) == 4


def test_analysis_only_marker_and_disabled_round_trip(tmp_path: Path) -> None:
    """The project-level marker records round-trip-disabled + the one-time banner (§7.8)."""
    smd = read_smd(_SMD4)
    out = tmp_path / "ao.tether"
    import_analysis_only_project(out, source=smd, source_name="smd_4mol.hdf5")

    marker = read_analysis_only_marker(out)
    assert marker is not None
    assert marker.round_trip_available is False
    assert marker.banner == ANALYSIS_ONLY_BANNER
    assert marker.source == "smd_4mol.hdf5"
    assert marker.n_molecules == 4


def test_marker_absent_on_a_normal_project(tmp_path: Path) -> None:
    """A store without the marker reads back ``None`` (a round-trip-capable project)."""
    plain = tmp_path / "plain.tether"
    create_project(plain)
    assert read_analysis_only_marker(plain) is None


def test_marker_reader_fails_safe_without_the_attr(tmp_path: Path) -> None:
    """A marker group present but missing the attr reads as round-trip UNAVAILABLE (fail-safe).

    The group's mere presence marks an analysis-only project, so a partial write must
    never re-enable the round-trip browser over a coordinate-less store.
    """
    plain = tmp_path / "p.tether"
    create_project(plain)
    with h5py.File(plain, "r+") as f:
        f["settings"].create_group("analysis_only")  # group present, attr absent

    marker = read_analysis_only_marker(plain)
    assert marker is not None
    assert marker.round_trip_available is False


def test_analysis_runs_on_the_imported_project(tmp_path: Path) -> None:
    """§9 M7: analysis still works — the FRET histogram computes over the imported traces."""
    smd = read_smd(_SMD4)
    out = tmp_path / "ao.tether"
    import_analysis_only_project(out, source=smd, source_name="smd_4mol.hdf5")

    hist = population_apparent_e_histogram(out)
    assert hist.n_samples > 0  # apparent-E pooled over the imported corrected traces
    assert np.sum(hist.counts) > 0


def test_correction_method_is_apparent_unavailable(tmp_path: Path) -> None:
    """No movie ⇒ no correction factors ⇒ the explicit apparent-E substrate (ADR-0003)."""
    smd = read_smd(_SMD4)
    out = tmp_path / "ao.tether"
    import_analysis_only_project(out, source=smd, source_name="smd_4mol.hdf5")

    mols = _read_molecules(out)
    assert np.isnan(mols["alpha"]).all()
    assert np.isnan(mols["gamma"]).all()
    assert {_decode(v) for v in mols["correction_method"]} == {METHOD_APPARENT_UNAVAILABLE}


def test_analysis_window_from_smd_tmaven_windows(tmp_path: Path) -> None:
    """The SMD's tMAVEN ``pre_list``/``post_list`` become the per-molecule analysis window."""
    smd = read_smd(_SMD4)
    assert smd.has_tmaven  # this fixture carries tMAVEN windows
    out = tmp_path / "ao.tether"
    import_analysis_only_project(out, source=smd, source_name="smd_4mol.hdf5")

    mols = _read_molecules(out)
    expected = np.stack([smd.pre_list, smd.post_list], axis=1).astype(np.int64)
    np.testing.assert_array_equal(mols["analysis_window"], expected)
    # frame_range is always the full native extent
    np.testing.assert_array_equal(mols["frame_range"], np.tile([0, 1700], (4, 1)))


def test_categories_seeded_survive_reload(tmp_path: Path) -> None:
    """A seeded editable-category vocabulary is materialized and survives a reload."""
    smd = read_smd(_SMD4)
    out = tmp_path / "ao.tether"
    categories = ("dynamic", "static", "noise")
    summary = import_analysis_only_project(
        out, source=smd, source_name="smd_4mol.hdf5", categories=categories
    )
    assert summary.n_categories == 3

    mols = _read_molecules(out)
    condition_id = _decode(mols["condition_id"][0])
    survived = conditions.read_category_list(out, condition_id)
    assert survived.categories == categories


def test_reimport_is_deterministic(tmp_path: Path) -> None:
    """Re-importing the same source yields identical molecule_keys (stable identity, §7.8)."""
    smd = read_smd(_SMD4)
    a = tmp_path / "a.tether"
    b = tmp_path / "b.tether"
    import_analysis_only_project(a, source=smd, source_name="smd_4mol.hdf5")
    import_analysis_only_project(b, source=smd, source_name="smd_4mol.hdf5")

    keys_a = [_decode(v) for v in _read_molecules(a)["molecule_key"]]
    keys_b = [_decode(v) for v in _read_molecules(b)["molecule_key"]]
    assert keys_a == keys_b  # deterministic across re-import (a stable content identity)


def test_identical_traces_get_distinct_molecule_keys(tmp_path: Path) -> None:
    """molecule_key stays unique even for byte-identical traces (the /labels-join guard).

    The critical invariant (§7.10): ``molecule_key`` is the ``/labels`` join key, so a
    collision would cross-contaminate two molecules' curation labels. Uniqueness is
    anchored by the row index, so two molecules with identical donor/acceptor traces
    must still get distinct keys — the guard against a future "hash only the trace bytes"
    simplification (which the distinct-trace fixtures would not catch).
    """
    from tether.idealize.smd import SMDData

    raw = np.zeros((3, 20, 2), dtype=np.float64)
    raw[0] = 5.0
    raw[1] = 5.0  # byte-identical to row 0
    raw[2] = 7.0
    smd = SMDData(raw=raw, source_names=["s"], source_index=np.zeros(3, dtype="int64"))

    out = tmp_path / "id.tether"
    import_analysis_only_project(out, source=smd, source_name="idtest.hdf5")
    keys = [_decode(v) for v in _read_molecules(out)["molecule_key"]]
    assert len(set(keys)) == 3  # index-anchored: identical traces → distinct keys

    out2 = tmp_path / "id2.tether"
    import_analysis_only_project(out2, source=smd, source_name="idtest.hdf5")
    keys2 = [_decode(v) for v in _read_molecules(out2)["molecule_key"]]
    assert keys == keys2  # and stable across a re-import


def test_analysis_window_clamps_and_falls_back(tmp_path: Path) -> None:
    """Out-of-range SMD windows clamp to ``[0, n_frames]``; a degenerate one → full native."""
    from tether.idealize.smd import SMDData

    n_frames = 20
    raw = np.arange(3 * n_frames * 2, dtype=np.float64).reshape(3, n_frames, 2)
    pre = np.array([2, 5, 1], dtype="int64")
    post = np.array([n_frames + 10, 3, 18], dtype="int64")  # [clamp-hi, degenerate, valid-partial]
    smd = SMDData(
        raw=raw,
        source_names=["s"],
        source_index=np.zeros(3, dtype="int64"),
        classes=np.zeros(3, dtype="int64"),
        pre_list=pre,
        post_list=post,
    )
    out = tmp_path / "w.tether"
    import_analysis_only_project(out, source=smd, source_name="w.hdf5")

    aw = _read_molecules(out)["analysis_window"]
    assert aw[0].tolist() == [2, n_frames]  # post clamped to n_frames, pre kept
    assert aw[1].tolist() == [0, n_frames]  # post(3) <= pre(5) → degenerate → full native
    assert aw[2].tolist() == [1, 18]  # a valid partial window is preserved verbatim


def test_import_from_bare_txt_source(tmp_path: Path) -> None:
    """A bare Deep-LASI ``.txt`` (no HDF5 wrapper) also imports as analysis-only."""
    txt = read_deeplasi_txt(_TXT)
    out = tmp_path / "txt.tether"
    summary = import_analysis_only_project(out, source=txt, source_name="deeplasi_traces_slice.txt")

    assert summary.source_kind == "txt"
    assert summary.n_molecules == txt.n_molecules
    mols = _read_molecules(out)
    assert {_decode(v) for v in mols["tags"]} == {ANALYSIS_ONLY_TAG}
    assert set(mols["curation_label"].tolist()) == {int(CurationLabel.UNCURATED)}
    # no tMAVEN windows on a bare .txt → the full native window; frame_range == stored width
    full = np.tile([0, txt.n_frames], (txt.n_molecules, 1))
    np.testing.assert_array_equal(mols["analysis_window"], full)
    np.testing.assert_array_equal(mols["frame_range"], full)
    with h5py.File(out, "r") as f:
        assert set(f["traces"].keys()) == {"donor_corrected", "acceptor_corrected"}
        assert f["traces"]["donor_corrected"].shape == (txt.n_molecules, txt.n_frames)
        # the .txt donor/acceptor columns land in the right layers, value-for-value
        np.testing.assert_array_equal(
            f["traces"]["donor_corrected"][:], txt.donor_corrected.astype(np.float32)
        )
        np.testing.assert_array_equal(
            f["traces"]["acceptor_corrected"][:], txt.acceptor_corrected.astype(np.float32)
        )
        assert f["movies"]["table"].shape[0] == 0  # movie-less
    assert read_analysis_only_marker(out).round_trip_available is False


# --------------------------------------------------------------------------- #
# guardrails
# --------------------------------------------------------------------------- #


def test_refuses_to_clobber_then_overwrites_with_a_new_source(tmp_path: Path) -> None:
    """Default refuses to clobber; ``overwrite=True`` genuinely replaces the persisted data."""
    smd4 = read_smd(_SMD4)
    smd2 = read_smd(_FIXTURES / "smd_2mol.hdf5")  # a different source (2 molecules)
    out = tmp_path / "ao.tether"
    import_analysis_only_project(out, source=smd4, source_name="smd_4mol.hdf5")
    with pytest.raises(FileExistsError):
        import_analysis_only_project(out, source=smd4, source_name="smd_4mol.hdf5")

    # overwrite=True replaces it with the *different* source — the persisted store changes
    summary = import_analysis_only_project(
        out, source=smd2, source_name="smd_2mol.hdf5", overwrite=True
    )
    assert summary.n_molecules == smd2.n_molecules == 2
    assert _read_molecules(out).shape[0] == 2  # the store on disk actually changed
    assert read_analysis_only_marker(out).source == "smd_2mol.hdf5"


def test_overwrite_failure_leaves_the_original_intact(tmp_path: Path, monkeypatch) -> None:
    """A failure mid-publish rolls back: the original project + no temp artifact survive (§5.4)."""
    import tether.project.analysis_import as ai_module

    smd4 = read_smd(_SMD4)
    smd2 = read_smd(_FIXTURES / "smd_2mol.hdf5")
    out = tmp_path / "ao.tether"
    import_analysis_only_project(out, source=smd4, source_name="smd_4mol.hdf5")

    def _boom(*_a, **_k):
        raise RuntimeError("injected mid-publish failure")

    # fail after the temp store is built but before os.replace publishes it
    monkeypatch.setattr(ai_module, "compute_corrected_fret", _boom)
    with pytest.raises(RuntimeError, match="injected"):
        import_analysis_only_project(out, source=smd2, source_name="smd_2mol.hdf5", overwrite=True)

    # the original 4-molecule project is untouched, and no .tmp sibling was left behind
    assert _read_molecules(out).shape[0] == 4
    assert list(tmp_path.glob("*.tmp")) == []


def test_overwrite_refuses_a_foreign_locked_project(tmp_path: Path) -> None:
    """``overwrite=True`` must not clobber a project another writer holds open (§5.4)."""
    smd = read_smd(_SMD4)
    out = tmp_path / "ao.tether"
    import_analysis_only_project(out, source=smd, source_name="smd_4mol.hdf5")
    lock.acquire(out, identity=lock.LockIdentity(host="OTHER", user="bob", pid=999))
    with pytest.raises(lock.LockedError):
        import_analysis_only_project(out, source=smd, source_name="smd_4mol.hdf5", overwrite=True)


def test_rejects_empty_source(tmp_path: Path) -> None:
    from tether.idealize.smd import SMDData

    empty = SMDData(
        raw=np.zeros((0, 10, 2)), source_names=["s"], source_index=np.zeros(0, dtype="int64")
    )
    with pytest.raises(ValueError, match="no molecules"):
        import_analysis_only_project(tmp_path / "e.tether", source=empty)


def test_rejects_zero_frame_traces(tmp_path: Path) -> None:
    """A source with zero-length traces is rejected before any output is published."""
    from tether.idealize.smd import SMDData

    zero_frames = SMDData(
        raw=np.zeros((2, 0, 2)), source_names=["s"], source_index=np.zeros(2, dtype="int64")
    )
    out = tmp_path / "z.tether"
    with pytest.raises(ValueError, match="zero frames"):
        import_analysis_only_project(out, source=zero_frames)
    assert not out.exists()  # nothing published (rejected before the atomic build)


def test_rejects_unknown_source_type(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="SMDData"):
        import_analysis_only_project(tmp_path / "b.tether", source=object())


# --------------------------------------------------------------------------- #
# gated large tier — the PRD's canonical analysis-only example
# --------------------------------------------------------------------------- #


@pytest.mark.large
@pytest.mark.skipif(not _SMD281.exists(), reason="281-mol SMD large fixture not present")
def test_import_281mol_parity_fixture(tmp_path: Path) -> None:
    """The M6 281-molecule parity SMD (PRD §7.8's canonical case) imports analysis-only."""
    smd = read_smd(_SMD281)
    out = tmp_path / "parity.tether"
    summary = import_analysis_only_project(out, source=smd, source_name="smd_281mol.hdf5")

    assert summary.n_molecules == smd.n_molecules >= 50  # a ≥50-molecule population
    mols = _read_molecules(out)
    assert {_decode(v) for v in mols["tags"]} == {ANALYSIS_ONLY_TAG}
    assert read_analysis_only_marker(out).round_trip_available is False
    # analysis still works on the full population
    hist = population_apparent_e_histogram(out)
    assert hist.n_samples > 0
