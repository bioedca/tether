# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Flat tabular exports — Deep-LASI ``.txt`` + per-molecule CSV (FR-EXPORT, PRD §7.9).

Headless (no Qt): builds an in-memory ``.tether`` via the shared
``_analysis_store`` builder, exports to ``tmp_path``, and reads the result back.
Runs in the default 3-OS ``test`` matrix (gated on ``h5py``).
"""

from __future__ import annotations

import csv
import json

import pytest

pytest.importorskip("numpy")
h5py = pytest.importorskip("h5py")

import numpy as np  # noqa: E402

from _analysis_store import build_store_with_channels  # noqa: E402
from tether.imaging.extract import read_traces  # noqa: E402
from tether.io.deeplasi import read_deeplasi_txt, write_deeplasi_txt  # noqa: E402
from tether.project.export import (  # noqa: E402
    MOLECULE_TABLE_COLUMNS,
    export_deeplasi_txt,
    export_molecule_table_csv,
    write_provenance_sidecar,
)

_TXT_ATOL = 1e-4  # the Deep-LASI .txt is written to 5 decimals (matches test_deeplasi)


def _asym_channels(n: int = 3, n_frames: int = 10) -> tuple[np.ndarray, np.ndarray]:
    """Per-molecule-asymmetric, per-frame-varying donor/acceptor channels.

    Asymmetric across molecules (donor scales with the row) and varying across
    frames so the interleave/transpose is actually exercised and no two cells tie
    (avoids the symmetric-fixture FP-tie trap)."""
    frames = np.arange(n_frames, dtype="float64")
    donor = np.array([(i + 1) * 100.0 + frames for i in range(n)])
    acceptor = np.array([50.0 + 3.0 * (i + 1) + 2.0 * frames for i in range(n)])
    return donor, acceptor


def _set_frame_range(project_path, ranges):
    """Overwrite each molecule's ``frame_range`` (its valid native extent).

    Lets a single-movie fixture emulate a store whose ``/traces`` padding extends
    past a molecule's real end (``frame_range[hi] < max_T``) or whose molecules span
    differing native extents — without building a genuine multi-movie store."""
    with h5py.File(project_path, "r+") as handle:
        table = handle["molecules"]["table"][:]
        for i, (lo, hi) in enumerate(ranges):
            table["frame_range"][i] = (lo, hi)
        handle["molecules"]["table"][:] = table


# --------------------------------------------------------------------------- #
# write_deeplasi_txt — the pure serializer (write-side mirror of the reader)   #
# --------------------------------------------------------------------------- #


def test_write_deeplasi_txt_roundtrips_donor_first(tmp_path):
    donor = np.array([[1.0, 2.0, 3.0], [10.0, 20.0, 30.0]])  # (2 molecules, 3 frames)
    acceptor = np.array([[4.0, 5.0, 6.0], [40.0, 50.0, 60.0]])
    out = write_deeplasi_txt(tmp_path / "t.txt", donor, acceptor)

    back = read_deeplasi_txt(out)
    assert back.donor_corrected.shape == (2, 3)
    np.testing.assert_allclose(back.donor_corrected, donor, atol=_TXT_ATOL)
    np.testing.assert_allclose(back.acceptor_corrected, acceptor, atol=_TXT_ATOL)
    # donor-first interleave: molecule-0 frame-0 donor is 1.0, not the acceptor 4.0.
    assert back.donor_corrected[0, 0] == pytest.approx(1.0)
    assert back.acceptor_corrected[0, 0] == pytest.approx(4.0)


def test_write_deeplasi_txt_rejects_bad_shapes(tmp_path):
    with pytest.raises(ValueError, match="matching"):
        write_deeplasi_txt(tmp_path / "a.txt", np.zeros((2, 3)), np.zeros((2, 4)))
    with pytest.raises(ValueError, match="empty"):
        write_deeplasi_txt(tmp_path / "b.txt", np.zeros((0, 3)), np.zeros((0, 3)))
    with pytest.raises(ValueError, match="matching"):
        write_deeplasi_txt(tmp_path / "c.txt", np.zeros(3), np.zeros(3))  # 1-D


# --------------------------------------------------------------------------- #
# export_deeplasi_txt — store -> .txt                                          #
# --------------------------------------------------------------------------- #


def test_export_deeplasi_txt_matches_store_traces(tmp_path):
    donor, acceptor = _asym_channels()
    project, _ = build_store_with_channels(tmp_path, donor, acceptor)

    result = export_deeplasi_txt(project, tmp_path / "traces.txt")
    assert result.n_molecules == 3

    back = read_deeplasi_txt(result.path)
    store = read_traces(project.path)
    np.testing.assert_allclose(back.donor_corrected, store["donor_corrected"], atol=_TXT_ATOL)
    np.testing.assert_allclose(back.acceptor_corrected, store["acceptor_corrected"], atol=_TXT_ATOL)


def test_export_deeplasi_txt_stamps_provenance(tmp_path):
    donor, acceptor = _asym_channels()
    project, _ = build_store_with_channels(tmp_path, donor, acceptor)

    result = export_deeplasi_txt(project, tmp_path / "traces.txt")

    assert result.provenance_path == result.path.with_name("traces.txt.provenance.json")
    assert result.provenance_path.is_file()
    stamp = json.loads(result.provenance_path.read_text(encoding="utf-8"))
    assert stamp["tether_export"] == "deeplasi-txt"
    assert stamp["app_version"]  # non-empty; NOT an exact string (git-derived)
    assert "T" in stamp["created_utc"]  # ISO-8601 datetime
    assert stamp["source_project"] == project.path.name
    assert stamp["parameters"]["n_molecules"] == 3
    assert stamp["parameters"]["intensity_quantity"] == "corrected"


def test_export_deeplasi_txt_excludes_rejected(tmp_path):
    donor, acceptor = _asym_channels()
    project, _ = build_store_with_channels(tmp_path, donor, acceptor, rejected=[False, True, False])

    result = export_deeplasi_txt(project, tmp_path / "traces.txt")  # include_rejected=False

    assert result.n_molecules == 2
    back = read_deeplasi_txt(result.path)
    store = read_traces(project.path)
    # only the two accepted rows (0 and 2) survive, in store order
    np.testing.assert_allclose(
        back.donor_corrected, store["donor_corrected"][[0, 2]], atol=_TXT_ATOL
    )


def test_export_deeplasi_txt_molecule_keys_subselect(tmp_path):
    donor, acceptor = _asym_channels()
    project, keys = build_store_with_channels(tmp_path, donor, acceptor)

    result = export_deeplasi_txt(project, tmp_path / "one.txt", molecule_keys=[keys[1]])

    assert result.n_molecules == 1
    back = read_deeplasi_txt(result.path)
    store = read_traces(project.path)
    np.testing.assert_allclose(back.donor_corrected, store["donor_corrected"][[1]], atol=_TXT_ATOL)


def test_export_deeplasi_txt_empty_selection_raises(tmp_path):
    donor, acceptor = _asym_channels()
    project, _ = build_store_with_channels(tmp_path, donor, acceptor, rejected=[True, True, True])
    with pytest.raises(ValueError, match="no molecules"):
        export_deeplasi_txt(project, tmp_path / "traces.txt")


def test_export_deeplasi_txt_unknown_quantity_raises(tmp_path):
    donor, acceptor = _asym_channels()
    project, _ = build_store_with_channels(tmp_path, donor, acceptor)
    with pytest.raises(ValueError, match="intensity_quantity"):
        export_deeplasi_txt(project, tmp_path / "x.txt", intensity_quantity="bogus")


def test_export_deeplasi_txt_unknown_key_raises(tmp_path):
    # A caller typo must fail loudly, not silently under-export (cf. handoff sibling).
    donor, acceptor = _asym_channels()
    project, keys = build_store_with_channels(tmp_path, donor, acceptor)
    with pytest.raises(KeyError, match="molecule_key"):
        export_deeplasi_txt(project, tmp_path / "x.txt", molecule_keys=[keys[0], "typo-key"])


def test_export_deeplasi_txt_trims_to_frame_range(tmp_path):
    # frame_range shorter than the trace length (emulates a store padded past a
    # molecule's real end): the .txt must stop at the native extent, not write pad.
    n_frames = 10
    donor, acceptor = _asym_channels(n=3, n_frames=n_frames)
    project, _ = build_store_with_channels(tmp_path, donor, acceptor)
    _set_frame_range(project.path, [(0, 7)] * 3)

    result = export_deeplasi_txt(project, tmp_path / "traces.txt")

    back = read_deeplasi_txt(result.path)
    assert back.n_frames == 7  # trimmed to frame_range, not the full 10-frame array
    store = read_traces(project.path)
    np.testing.assert_allclose(
        back.donor_corrected, store["donor_corrected"][:, :7], atol=_TXT_ATOL
    )
    stamp = json.loads(result.provenance_path.read_text(encoding="utf-8"))
    assert stamp["parameters"]["n_frames"] == 7


def test_export_deeplasi_txt_mixed_extents_raises(tmp_path):
    # Molecules of differing native extent have no single shared frame axis.
    donor, acceptor = _asym_channels(n=3, n_frames=10)
    project, _ = build_store_with_channels(tmp_path, donor, acceptor)
    _set_frame_range(project.path, [(0, 10), (0, 6), (0, 10)])
    with pytest.raises(ValueError, match="frame ranges"):
        export_deeplasi_txt(project, tmp_path / "traces.txt")


# --------------------------------------------------------------------------- #
# export_molecule_table_csv — store -> per-molecule CSV                        #
# --------------------------------------------------------------------------- #


def _constant_e_channels(n: int = 3, n_frames: int = 8) -> tuple[np.ndarray, np.ndarray]:
    """Molecule ``i`` has a constant apparent E of ``1 / (i + 2)``.

    donor_i = (i+1)*100, acceptor_i = 100 -> E = 100 / ((i+2)*100) = 1/(i+2):
    0.5, 1/3, 0.25 for i = 0, 1, 2 (distinct, no ties)."""
    donor = np.array([[(i + 1) * 100.0] * n_frames for i in range(n)])
    acceptor = np.full((n, n_frames), 100.0)
    return donor, acceptor


def _read_csv(path):
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_export_molecule_table_csv_rows_and_apparent_e(tmp_path):
    n_frames = 8
    donor, acceptor = _constant_e_channels(n=3, n_frames=n_frames)
    project, keys = build_store_with_channels(
        tmp_path, donor, acceptor, windows=[(0, n_frames)] * 3
    )

    result = export_molecule_table_csv(project, tmp_path / "mol.csv")
    assert result.n_molecules == 3

    rows = _read_csv(result.path)
    assert len(rows) == 3
    assert set(rows[0].keys()) == set(MOLECULE_TABLE_COLUMNS)
    for i, row in enumerate(rows):
        assert row["molecule_key"] == keys[i]
        assert row["curation_label"] == "uncurated"
        assert int(row["n_finite_frames"]) == n_frames
        assert float(row["mean_apparent_e"]) == pytest.approx(1.0 / (i + 2))
        assert float(row["median_apparent_e"]) == pytest.approx(1.0 / (i + 2))
        # window fields round-tripped
        assert int(row["window_start"]) == 0
        assert int(row["window_end"]) == n_frames


def test_export_molecule_table_csv_stamps_provenance(tmp_path):
    donor, acceptor = _constant_e_channels()
    project, _ = build_store_with_channels(tmp_path, donor, acceptor)

    result = export_molecule_table_csv(project, tmp_path / "mol.csv")

    assert result.provenance_path.is_file()
    stamp = json.loads(result.provenance_path.read_text(encoding="utf-8"))
    assert stamp["tether_export"] == "molecule-table-csv"
    assert stamp["app_version"]
    assert stamp["parameters"]["include_rejected"] is True


def test_export_molecule_table_csv_rejected_filter(tmp_path):
    donor, acceptor = _constant_e_channels()
    project, _ = build_store_with_channels(tmp_path, donor, acceptor, rejected=[False, True, False])

    full = export_molecule_table_csv(project, tmp_path / "full.csv")  # include_rejected=True
    full_rows = _read_csv(full.path)
    assert full.n_molecules == 3
    assert [r["curation_label"] for r in full_rows] == ["uncurated", "reject", "uncurated"]

    trimmed = export_molecule_table_csv(project, tmp_path / "trimmed.csv", include_rejected=False)
    assert trimmed.n_molecules == 2
    assert all(r["curation_label"] != "reject" for r in _read_csv(trimmed.path))


def test_export_molecule_table_csv_unknown_quantity_raises(tmp_path):
    donor, acceptor = _constant_e_channels()
    project, _ = build_store_with_channels(tmp_path, donor, acceptor)
    with pytest.raises(ValueError, match="intensity_quantity"):
        export_molecule_table_csv(project, tmp_path / "x.csv", intensity_quantity="bogus")


# --------------------------------------------------------------------------- #
# write_provenance_sidecar — the shared stamp                                  #
# --------------------------------------------------------------------------- #


def test_write_provenance_sidecar_shape(tmp_path):
    data = tmp_path / "export.csv"
    data.write_text("a,b\n1,2\n", encoding="utf-8")

    sidecar = write_provenance_sidecar(
        data, tether_export="unit-test", source="proj.tether", parameters={"k": 1}
    )

    assert sidecar == tmp_path / "export.csv.provenance.json"
    stamp = json.loads(sidecar.read_text(encoding="utf-8"))
    assert set(stamp) == {
        "tether_export",
        "app_version",
        "created_utc",
        "source_project",
        "parameters",
    }
    assert stamp["parameters"] == {"k": 1}
    assert stamp["app_version"]
