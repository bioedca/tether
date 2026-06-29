# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the minimal Deep-LASI ``.mat`` / ``.txt`` validation reader (M1 S9).

Two layers:

* **Committed-slice + synthetic** (always run): the reader on the tiny
  ``deeplasi_export_slice.mat`` / ``deeplasi_traces_slice.txt`` fixtures (a
  4-molecule × 80-frame slice of the real UCKOPSB export, ``scripts/
  make_deeplasi_fixture.py``), plus in-test ``savemat`` round-trips that prove
  the 1-based→0-based coordinate conversion and the input guards without any
  external data.
* **Data-present** (skipped when ``example-data/`` is absent — e.g. the default
  CI checkout): the reader on the full 250-molecule × 1700-frame export, locking
  the coordinate convention and the ``.txt`` ≡ ``.mat`` ``donc`` / ``accc``
  identity on the real files.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")

import numpy as np  # noqa: E402
import scipy.io as sio  # noqa: E402

from tether.io.deeplasi import (  # noqa: E402
    DeepLasiExport,
    DeepLasiTraces,
    read_deeplasi_mat,
    read_deeplasi_txt,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SLICE_MAT = FIXTURES / "deeplasi_export_slice.mat"
SLICE_TXT = FIXTURES / "deeplasi_traces_slice.txt"
TDAT_V73 = FIXTURES / "tdat_coloc_slice.tdat"  # a real MATLAB v7.3 file

# Ground truth captured from the committed 4x80 slice (molecule 0, frame 0).
N_MOL, N_FRAMES = 4, 80
DONOR_XY0 = (14.0, 484.0)  # fret_pairs[0, 0:2] = [15, 485] (1-based) - 1
ACCEPTOR_XY0 = (22.0, 486.0)  # fret_pairs[0, 2:4] = [23, 487] - 1
DONOR_RAW_00, ACCEPTOR_RAW_00 = 2745.6, 1769.6
DONC_00, ACCC_00 = 1226.965625, 35.940625
BDON_00, BACC_00 = 1518.634375, 1733.659375
EXPORTED_BY = "TRacer_v1"
MOVIE_BASENAME = "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif"  # real ``movie_name`` filename
REDACTED_MOVIE_PATH = "<redacted-directory>"  # fixture redaction of the real directory
TXT_DONC_00 = 1226.96562  # the .txt's 5-decimal rounding of DONC_00

# .txt 5-decimal rounding vs the .mat's full-precision donc/accc.
TXT_ROUNDING_ATOL = 1e-4


def _valid_mat_fields(n_mol: int = 2, n_frames: int = 5) -> dict[str, object]:
    """A complete, minimal set of Deep-LASI ``.mat`` fields for ``savemat``."""
    rng = np.arange(n_mol * n_frames, dtype=np.float64).reshape(n_mol, n_frames)
    return {
        "fret_pairs": np.array([[1.0, 1.0, 3.0, 3.0], [10.0, 20.0, 12.0, 22.0]])[:n_mol],
        "don": rng,
        "acc": rng + 1.0,
        "donc": rng + 2.0,
        "accc": rng + 3.0,
        "bdon": rng + 4.0,
        "bacc": rng + 5.0,
        "movie_name": "synthetic.tif",
        "movie_path": "/synthetic/dir/",
        "exportedby": "pytest",
    }


def _write_mat(path: Path, fields: dict[str, object]) -> Path:
    sio.savemat(str(path), fields, format="5", do_compression=True)
    return path


# --- committed-slice .mat reader --------------------------------------------


def test_read_mat_returns_export() -> None:
    export = read_deeplasi_mat(SLICE_MAT)
    assert isinstance(export, DeepLasiExport)
    assert export.n_molecules == N_MOL
    assert export.n_frames == N_FRAMES


def test_read_mat_array_shapes_and_dtype() -> None:
    e = read_deeplasi_mat(SLICE_MAT)
    for arr in (e.donor_xy, e.acceptor_xy):
        assert arr.shape == (N_MOL, 2)
        assert arr.dtype == np.float64
        assert arr.flags["C_CONTIGUOUS"]
    for arr in (
        e.donor_raw,
        e.acceptor_raw,
        e.donor_corrected,
        e.acceptor_corrected,
        e.donor_background,
        e.acceptor_background,
    ):
        assert arr.shape == (N_MOL, N_FRAMES)
        assert arr.dtype == np.float64
        assert arr.flags["C_CONTIGUOUS"]


def test_read_mat_coordinates_are_zero_based() -> None:
    e = read_deeplasi_mat(SLICE_MAT)
    np.testing.assert_allclose(e.donor_xy[0], DONOR_XY0, atol=1e-6)
    np.testing.assert_allclose(e.acceptor_xy[0], ACCEPTOR_XY0, atol=1e-6)


def test_read_mat_trace_values() -> None:
    e = read_deeplasi_mat(SLICE_MAT)
    assert e.donor_raw[0, 0] == pytest.approx(DONOR_RAW_00)
    assert e.acceptor_raw[0, 0] == pytest.approx(ACCEPTOR_RAW_00)
    assert e.donor_corrected[0, 0] == pytest.approx(DONC_00)
    assert e.acceptor_corrected[0, 0] == pytest.approx(ACCC_00)
    assert e.donor_background[0, 0] == pytest.approx(BDON_00)
    assert e.acceptor_background[0, 0] == pytest.approx(BACC_00)


def test_read_mat_provenance() -> None:
    # movie_name is the real filename; movie_path is the redacted directory.
    e = read_deeplasi_mat(SLICE_MAT)
    assert e.movie_name == MOVIE_BASENAME
    assert e.movie_path == REDACTED_MOVIE_PATH
    assert e.exported_by == EXPORTED_BY


# --- committed-slice .txt reader --------------------------------------------


def test_read_txt_returns_traces() -> None:
    t = read_deeplasi_txt(SLICE_TXT)
    assert isinstance(t, DeepLasiTraces)
    assert t.n_molecules == N_MOL
    assert t.n_frames == N_FRAMES
    assert t.donor_corrected.shape == (N_MOL, N_FRAMES)
    assert t.acceptor_corrected.shape == (N_MOL, N_FRAMES)
    assert t.donor_corrected.dtype == np.float64


def test_read_txt_deinterleaves_donor_first() -> None:
    # Column 0 is molecule-0 donor, column 1 is molecule-0 acceptor.
    t = read_deeplasi_txt(SLICE_TXT)
    assert t.donor_corrected[0, 0] == pytest.approx(TXT_DONC_00)
    assert t.acceptor_corrected[0, 0] == pytest.approx(ACCC_00, abs=1e-4)


def test_txt_matches_mat_corrected_traces() -> None:
    # The .txt is the donor-first-interleaved donc/accc, rounded to 5 decimals.
    e = read_deeplasi_mat(SLICE_MAT)
    t = read_deeplasi_txt(SLICE_TXT)
    np.testing.assert_allclose(t.donor_corrected, e.donor_corrected, atol=TXT_ROUNDING_ATOL)
    np.testing.assert_allclose(t.acceptor_corrected, e.acceptor_corrected, atol=TXT_ROUNDING_ATOL)


# --- synthetic round-trips: coordinate conversion + guards -------------------


def test_mat_coordinate_conversion_is_minus_one(tmp_path: Path) -> None:
    e = read_deeplasi_mat(_write_mat(tmp_path / "ok.mat", _valid_mat_fields()))
    # fret_pairs = [[1,1,3,3],[10,20,12,22]] (1-based) -> 0-based.
    np.testing.assert_array_equal(e.donor_xy, [[0.0, 0.0], [9.0, 19.0]])
    np.testing.assert_array_equal(e.acceptor_xy, [[2.0, 2.0], [11.0, 21.0]])


def test_mat_missing_field_raises(tmp_path: Path) -> None:
    fields = _valid_mat_fields()
    del fields["accc"]
    with pytest.raises(ValueError, match="accc"):
        read_deeplasi_mat(_write_mat(tmp_path / "missing.mat", fields))


def test_mat_inconsistent_frame_count_raises(tmp_path: Path) -> None:
    fields = _valid_mat_fields(n_mol=2, n_frames=5)
    fields["acc"] = np.zeros((2, 4), dtype=np.float64)  # T mismatch
    with pytest.raises(ValueError, match="frame count"):
        read_deeplasi_mat(_write_mat(tmp_path / "ragged.mat", fields))


def test_mat_bad_fret_pairs_shape_raises(tmp_path: Path) -> None:
    fields = _valid_mat_fields()
    fields["fret_pairs"] = np.zeros((2, 3), dtype=np.float64)  # not N x 4
    with pytest.raises(ValueError, match="fret_pairs"):
        read_deeplasi_mat(_write_mat(tmp_path / "badcoords.mat", fields))


def test_mat_trace_molecule_mismatch_raises(tmp_path: Path) -> None:
    fields = _valid_mat_fields(n_mol=2, n_frames=5)
    fields["don"] = np.zeros((3, 5), dtype=np.float64)  # N mismatch vs fret_pairs
    with pytest.raises(ValueError, match="don"):
        read_deeplasi_mat(_write_mat(tmp_path / "nmismatch.mat", fields))


def test_read_mat_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_deeplasi_mat(tmp_path / "nope.mat")


def test_read_mat_rejects_non_v5_hdf5() -> None:
    # The .tdat fixture is a bare HDF5 file (no MATLAB header) — not a MAT file;
    # the reader rejects it cleanly rather than leaking a raw scipy error.
    if not TDAT_V73.is_file():
        pytest.skip("HDF5 .tdat fixture absent")
    with pytest.raises(ValueError, match="MATLAB v5"):
        read_deeplasi_mat(TDAT_V73)


def test_read_mat_rejects_garbage_text(tmp_path: Path) -> None:
    # A .txt mistakenly renamed to .mat (a realistic wrong-file mistake): scipy's
    # matfile_version raises IndexError on such short/garbage input — the reader
    # must still surface the documented clean ValueError, not leak it.
    path = tmp_path / "renamed.mat"
    path.write_bytes(b"This was a .txt renamed to .mat\n1 2 3 4\n")
    with pytest.raises(ValueError, match="MATLAB v5"):
        read_deeplasi_mat(path)


def test_read_mat_rejects_v73(tmp_path: Path) -> None:
    # Craft a minimal file with the MATLAB v7.3 header signature (an HDF5 file
    # whose 512-byte userblock carries version 0x0200 + "IM" at bytes 124-128),
    # so scipy.io.matlab.matfile_version reports major version 2.
    h5py = pytest.importorskip("h5py")
    path = tmp_path / "v73.mat"
    with h5py.File(path, "w", userblock_size=512) as fh:
        fh["x"] = np.array([1.0])
    header = bytearray(512)
    header[: len("MATLAB 7.3 MAT-file")] = b"MATLAB 7.3 MAT-file"
    header[124:128] = bytes([0x00, 0x02, ord("I"), ord("M")])
    with path.open("r+b") as fh:
        fh.write(header)
    with pytest.raises(NotImplementedError, match="v7.3"):
        read_deeplasi_mat(path)


# --- .txt guards -------------------------------------------------------------


def test_read_txt_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_deeplasi_txt(tmp_path / "nope.txt")


def test_read_txt_odd_columns_raises(tmp_path: Path) -> None:
    path = tmp_path / "odd.txt"
    path.write_text("1.0 2.0 3.0\n4.0 5.0 6.0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="even"):
        read_deeplasi_txt(path)


def test_read_txt_ragged_rows_raises(tmp_path: Path) -> None:
    # Rows of differing width -> a reader-level message, not numpy's `usecols` hint.
    path = tmp_path / "ragged.txt"
    path.write_text("1.0 2.0 3.0 4.0\n5.0 6.0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="rectangular numeric"):
        read_deeplasi_txt(path)


def test_read_txt_single_frame(tmp_path: Path) -> None:
    # One frame -> one row; ndmin=2 keeps it (1, 2N) so de-interleave still works.
    path = tmp_path / "onerow.txt"
    path.write_text("11.0 22.0 33.0 44.0\n", encoding="utf-8")
    t = read_deeplasi_txt(path)
    assert t.n_molecules == 2
    assert t.n_frames == 1
    np.testing.assert_array_equal(t.donor_corrected[:, 0], [11.0, 33.0])
    np.testing.assert_array_equal(t.acceptor_corrected[:, 0], [22.0, 44.0])


# --- data-present: the full real export -------------------------------------


def _find_full_export() -> tuple[Path, Path] | None:
    for parent in Path(__file__).resolve().parents:
        src = parent / "example-data" / "bla-uckopsb-tbox-video10"
        mat = src / "DeepLASI_MAT_export_Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.mat"
        txt = src / "DeepLASI_MAT_export_Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010-donc-accc-w.txt"
        if mat.is_file() and txt.is_file():
            return mat, txt
    return None


def test_full_export_matches_when_present() -> None:
    found = _find_full_export()
    if found is None:
        pytest.skip("external example-data Deep-LASI export absent")
    mat_path, txt_path = found
    e = read_deeplasi_mat(mat_path)
    t = read_deeplasi_txt(txt_path)
    assert (e.n_molecules, e.n_frames) == (250, 1700)
    assert (t.n_molecules, t.n_frames) == (250, 1700)
    # Coordinate convention holds on real data (molecule 0).
    np.testing.assert_allclose(e.donor_xy[0], DONOR_XY0, atol=1e-6)
    # Provenance: movie_name IS the filename on real data; movie_path is the
    # (machine-specific) directory — assert only that it is a non-empty string.
    assert e.movie_name == MOVIE_BASENAME
    assert isinstance(e.movie_path, str) and e.movie_path
    # The .txt is donc/accc to the text rounding, across ALL molecules.
    np.testing.assert_allclose(t.donor_corrected, e.donor_corrected, atol=TXT_ROUNDING_ATOL)
    np.testing.assert_allclose(t.acceptor_corrected, e.acceptor_corrected, atol=TXT_ROUNDING_ATOL)
