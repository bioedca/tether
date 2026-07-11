# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reconstruct a round-trip-ready ``.tether`` from Deep-LASI data (M7, PRD §7.8 / §9 M7).

Drives :func:`tether.project.reconstruct.reconstruct_project` on the committed UCKOPSB
``…010`` slices — the same acquisition exercised by ``test_recover`` — proving the three
§9 M7 acceptance sub-clauses (PRD line 730):

1. a full Deep-LASI acquisition reconstructs into a round-trip-ready project **from the
   ``.tdat`` *or* the ``.mat`` coordinates** (movie-linked coords + raw/corrected/
   background traces);
2. the **curated subset** (the SMD selection, written as ``deeplasi-provisional``) **and
   the categories** (the editable category list) **survive**;
3. the **SMD intensity-match cross-check** identifies the curated molecule (the one of
   ``smd_4mol``'s four that lies in the committed first-4 slice) and no other.

The real ``…010`` movie is not committed (it is the ~0.9 GB acquisition), so a synthetic
:class:`~tether.imaging.extract.MovieMetadata` stands in — reconstruction imports the
already-integrated legacy traces and never re-opens the movie, so only the movie's
provenance (id + ``sha256`` + ``n_frames``) is needed to link every molecule.
"""

from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np
import pytest

from tether.idealize import read_smd
from tether.imaging.extract import MovieMetadata, molecule_key
from tether.io.deeplasi import read_deeplasi_mat, read_deeplasi_txt
from tether.io.recover import (
    RecoveredCoordinates,
    match_smd_to_coordinates,
    recover_coordinates,
)
from tether.io.tdat import TdatCorrections, read_tdat
from tether.project import conditions, labels
from tether.project.correct import METHOD_APPARENT_UNAVAILABLE, METHOD_MANUAL
from tether.project.labels import LABEL_SOURCE_DEEPLASI, CurationLabel
from tether.project.reconstruct import ReconstructionSummary, reconstruct_project

_FIXTURES = Path(__file__).parent / "fixtures"
_MAT = _FIXTURES / "deeplasi_export_slice.mat"
_TXT = _FIXTURES / "deeplasi_traces_slice.txt"
_TDAT = _FIXTURES / "tdat_coloc_slice.tdat"
_SMD4 = _FIXTURES / "smd_4mol.hdf5"

_SHA = "a1b2c3d4" * 8  # a fixed 64-hex stand-in movie content hash
_CATEGORIES = ("dynamic", "static", "noise")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _movie_meta(n_frames: int, *, sha256: str = _SHA, movie_id: str = "mov-recon") -> MovieMetadata:
    """A stand-in ``/movies`` provenance row for the (uncommitted) ``…010`` movie."""
    return MovieMetadata(
        movie_id=movie_id,
        sha256=sha256,
        n_frames=n_frames,
        height=512,
        width=256,
        uri="Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif",
        pixel_dtype="uint16",
        byteorder=">",
    )


def _curated_match(export):
    """The SMD intensity cross-check against the ``.mat`` (the §9 M7 sub-clause 3)."""
    smd = read_smd(_SMD4)
    txt = read_deeplasi_txt(_TXT)  # the SMD stores the corrected -donc-accc-w series
    rec = recover_coordinates(mat=export)
    reference = np.stack([txt.donor_corrected, txt.acceptor_corrected], axis=-1)
    smd_raw = smd.raw[:, : txt.n_frames, :]
    return match_smd_to_coordinates(smd_raw, reference, rec)


def _read_molecules(path: Path) -> np.ndarray:
    with h5py.File(path, "r") as f:
        return f["molecules"]["table"][:]


def _read_trace(path: Path, name: str) -> np.ndarray:
    with h5py.File(path, "r") as f:
        return f["traces"][name][:]


def _decode(value) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


# --------------------------------------------------------------------------- #
# §9 M7 acceptance — the round-trip reconstruction
# --------------------------------------------------------------------------- #


def test_reconstruct_from_mat_is_round_trip_ready(tmp_path: Path) -> None:
    """Sub-clauses 1-3 from the ``.mat`` coordinate source, in one reconstruction."""
    export = read_deeplasi_mat(_MAT)
    out = tmp_path / "recon.tether"
    match = _curated_match(export)

    summary = reconstruct_project(
        out,
        export=export,
        movie=_movie_meta(export.n_frames),
        categories=_CATEGORIES,
        curated_match=match,
    )

    assert isinstance(summary, ReconstructionSummary)
    assert summary.coordinate_source == "mat"
    assert summary.n_molecules == export.n_molecules == 4
    assert summary.n_curated == 1  # only smd_4mol[0] lies in the committed first-4 slice
    assert summary.n_categories == 3

    mols = _read_molecules(out)
    assert mols.shape[0] == 4
    # every molecule links to the single movie, movie-linked by movie_id + sub-pixel xy
    assert {_decode(v) for v in mols["movie_id"]} == {"mov-recon"}
    np.testing.assert_array_equal(mols["donor_xy"], export.donor_xy)
    np.testing.assert_array_equal(mols["acceptor_xy"], export.acceptor_xy)
    # molecule_key = movie sha256 + quantized donor_xy (the §7.10 cross-file join key)
    expected_keys = [molecule_key(_SHA, export.donor_xy[i]) for i in range(4)]
    assert [_decode(v) for v in mols["molecule_key"]] == expected_keys

    # raw + corrected + background traces round-trip (stored float32)
    np.testing.assert_allclose(_read_trace(out, "donor_raw"), export.donor_raw, rtol=0, atol=0.5)
    np.testing.assert_allclose(
        _read_trace(out, "donor_corrected"), export.donor_corrected, rtol=1e-3, atol=0.1
    )
    np.testing.assert_allclose(
        _read_trace(out, "acceptor_background"), export.acceptor_background, rtol=1e-3, atol=0.1
    )


def test_curated_subset_and_categories_survive(tmp_path: Path) -> None:
    """§9 M7 sub-clause 2: the curated selection + editable category list persist."""
    export = read_deeplasi_mat(_MAT)
    out = tmp_path / "recon.tether"
    match = _curated_match(export)
    reconstruct_project(
        out,
        export=export,
        movie=_movie_meta(export.n_frames),
        categories=_CATEGORIES,
        curated_match=match,
    )

    # the curated molecule is a deeplasi-provisional ACCEPT in /labels (not human)
    rows = labels.read_labels(out)
    assert rows.shape[0] == 1
    row = rows[0]
    assert _decode(row["source"]) == LABEL_SOURCE_DEEPLASI == "deeplasi-provisional"
    assert int(row["label_value"]) == int(CurationLabel.ACCEPT)
    assert _decode(row["molecule_key"]) == molecule_key(_SHA, export.donor_xy[1])
    # a provisional prior never sets curation_label (§5.1); it is a /labels-only prior
    mols = _read_molecules(out)
    assert set(mols["curation_label"].tolist()) == {int(CurationLabel.UNCURATED)}
    # its weight is the decaying cold-start prior w₀/(1+n_human), n_human=0 -> w₀≈0.3
    assert float(row["weight"]) == pytest.approx(0.3)

    # the editable category vocabulary survives verbatim
    condition_id = _decode(mols["condition_id"][0])
    survived = conditions.read_category_list(out, condition_id)
    assert survived.categories == _CATEGORIES


def test_reconstruct_from_tdat_coordinates(tmp_path: Path) -> None:
    """§9 M7 sub-clause 1, alternate source: reconstruct from the ``.tdat`` coordinates.

    The committed ``.tdat`` holds the full 250-molecule detection; its first four
    molecules are the committed ``.mat`` slice (locked equal in ``test_recover``), so a
    ``.tdat``-sourced coordinate set aligned to the four traced molecules reconstructs
    to the same positions, tagged ``source="tdat"``.
    """
    export = read_deeplasi_mat(_MAT)
    tdat = read_tdat(_TDAT)
    tdat_rec = recover_coordinates(tdat=tdat)  # 250 molecules
    coords = RecoveredCoordinates(
        donor_xy=np.ascontiguousarray(tdat_rec.donor_xy[:4]),
        acceptor_xy=np.ascontiguousarray(tdat_rec.acceptor_xy[:4]),
        source="tdat",
    )
    out = tmp_path / "recon.tether"

    summary = reconstruct_project(
        out,
        export=export,
        movie=_movie_meta(export.n_frames),
        coordinates=coords,
        corrections=tdat.corrections,
    )

    assert summary.coordinate_source == "tdat"
    assert summary.n_molecules == 4
    mols = _read_molecules(out)
    np.testing.assert_array_equal(mols["donor_xy"], tdat_rec.donor_xy[:4])
    # this fixture carries DefaultGamma=0 -> no usable γ -> apparent-E substrate
    assert summary.corrections_applied is False
    assert {_decode(v) for v in mols["correction_method"]} == {METHOD_APPARENT_UNAVAILABLE}
    assert np.isnan(mols["gamma"]).all()


# --------------------------------------------------------------------------- #
# correction-factor remap
# --------------------------------------------------------------------------- #


def test_corrections_injected_when_gamma_valid(tmp_path: Path) -> None:
    """A remapped, usable γ (>0) is injected as METHOD_MANUAL α/γ on every molecule."""
    export = read_deeplasi_mat(_MAT)
    # remapped factors (Appendix B): Deep-LASI β=0.05 -> Tether α; γ=1.2 -> Tether γ
    corrections = TdatCorrections(0.0, 0.05, 1.2, 0.05, 0.0, 1.2)
    out = tmp_path / "recon.tether"

    summary = reconstruct_project(
        out, export=export, movie=_movie_meta(export.n_frames), corrections=corrections
    )

    assert summary.corrections_applied is True
    mols = _read_molecules(out)
    np.testing.assert_allclose(mols["alpha"], 0.05)
    np.testing.assert_allclose(mols["gamma"], 1.2)
    assert {_decode(v) for v in mols["correction_method"]} == {METHOD_MANUAL}


def test_apparent_e_substrate_from_real_fixture(tmp_path: Path) -> None:
    """The committed ``.tdat`` (DefaultGamma=0) reconstructs as an apparent-E substrate."""
    export = read_deeplasi_mat(_MAT)
    tdat = read_tdat(_TDAT)
    assert tdat.corrections.gamma == 0.0  # guards the premise of this test
    out = tmp_path / "recon.tether"

    summary = reconstruct_project(
        out, export=export, movie=_movie_meta(export.n_frames), corrections=tdat.corrections
    )

    assert summary.corrections_applied is False
    mols = _read_molecules(out)
    assert np.isnan(mols["alpha"]).all()
    assert np.isnan(mols["gamma"]).all()
    assert {_decode(v) for v in mols["correction_method"]} == {METHOD_APPARENT_UNAVAILABLE}


# --------------------------------------------------------------------------- #
# bleach detection + patches + guardrails
# --------------------------------------------------------------------------- #


def test_photobleach_writes_frozen_fields(tmp_path: Path) -> None:
    """``detect_photobleach`` runs the M3 detector into bleach_frames + analysis_window."""
    export = read_deeplasi_mat(_MAT)
    out_on = tmp_path / "on.tether"
    reconstruct_project(
        out_on, export=export, movie=_movie_meta(export.n_frames), detect_photobleach=True
    )
    mols_on = _read_molecules(out_on)
    assert mols_on["bleach_frames"].shape == (4, 2)  # written per molecule

    out_off = tmp_path / "off.tether"
    summary_off = reconstruct_project(
        out_off, export=export, movie=_movie_meta(export.n_frames), detect_photobleach=False
    )
    assert summary_off.n_donor_bleached == 0
    mols_off = _read_molecules(out_off)
    # skipped -> the write_extraction "not detected" sentinel (-1, -1) survives
    assert (mols_off["bleach_frames"] == -1).all()


def test_supplied_patches_are_stored(tmp_path: Path) -> None:
    export = read_deeplasi_mat(_MAT)
    donor_patches = np.full((4, 21, 21), 7.0, dtype=np.float32)
    out = tmp_path / "recon.tether"
    reconstruct_project(
        out,
        export=export,
        movie=_movie_meta(export.n_frames),
        donor_patches=donor_patches,
        acceptor_patches=np.zeros((4, 21, 21), dtype=np.float32),
    )
    with h5py.File(out, "r") as f:
        np.testing.assert_array_equal(f["patches"]["donor"][:], donor_patches)
        assert not f["patches"]["acceptor"][:].any()  # zero-filled default


def test_reconstruct_rejects_misaligned_coordinates(tmp_path: Path) -> None:
    export = read_deeplasi_mat(_MAT)
    bad = RecoveredCoordinates(
        donor_xy=np.zeros((3, 2)), acceptor_xy=np.zeros((3, 2)), source="mat"
    )
    with pytest.raises(ValueError, match="positional join"):
        reconstruct_project(
            tmp_path / "x.tether",
            export=export,
            movie=_movie_meta(export.n_frames),
            coordinates=bad,
        )


def test_reconstruct_rejects_frame_mismatch(tmp_path: Path) -> None:
    export = read_deeplasi_mat(_MAT)
    with pytest.raises(ValueError, match="same movie"):
        reconstruct_project(
            tmp_path / "x.tether", export=export, movie=_movie_meta(export.n_frames + 1)
        )


def test_reconstruct_refuses_to_clobber(tmp_path: Path) -> None:
    export = read_deeplasi_mat(_MAT)
    out = tmp_path / "recon.tether"
    reconstruct_project(out, export=export, movie=_movie_meta(export.n_frames))
    with pytest.raises(FileExistsError):
        reconstruct_project(out, export=export, movie=_movie_meta(export.n_frames))
    # overwrite=True replaces it
    summary = reconstruct_project(
        out, export=export, movie=_movie_meta(export.n_frames), overwrite=True
    )
    assert summary.n_molecules == 4


def test_overwrite_refuses_a_foreign_locked_project(tmp_path: Path) -> None:
    """``overwrite=True`` must not clobber a project another writer holds open (§5.4)."""
    from tether.project import lock

    export = read_deeplasi_mat(_MAT)
    out = tmp_path / "recon.tether"
    reconstruct_project(out, export=export, movie=_movie_meta(export.n_frames))
    # a foreign writer (another host/user/pid) holds the single-writer lock
    lock.acquire(out, identity=lock.LockIdentity(host="OTHER", user="bob", pid=999))
    with pytest.raises(lock.LockedError):
        reconstruct_project(out, export=export, movie=_movie_meta(export.n_frames), overwrite=True)
