# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-molecule coordinate recovery + SMD intensity cross-check (M7, PRD §7.8).

Exercises :mod:`tether.io.recover` on the committed UCKOPSB slices — all three
artifacts of the *same* acquisition (``…010``): ``tdat_coloc_slice.tdat`` (250-mol
``ParticlesColocalized``), ``deeplasi_export_slice.mat`` (first 4 mol coords +
traces), ``deeplasi_traces_slice.txt`` (matching corrected traces), and
``smd_4mol.hdf5`` (the verbatim ``video10.hdf5`` tMAVEN selection). The ``.tdat``
donor/acceptor coordinates are locked equal to the ``.mat`` on real data, and the
``smd_4mol`` corrected traces cross-check back to their acquisition molecule (only
one of its four curated molecules lies in the committed first-4 slice — the other
three must be reported unmatched, never guessed).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from tether.idealize import read_smd
from tether.io.deeplasi import read_deeplasi_mat, read_deeplasi_txt
from tether.io.recover import (
    RecoveredCoordinates,
    match_smd_to_coordinates,
    recover_coordinates,
)
from tether.io.tdat import (
    Tdat,
    TdatColocalization,
    TdatCorrections,
    TdatDetectionSettings,
    read_tdat,
)

_FIXTURES = Path(__file__).parent / "fixtures"
_TDAT = _FIXTURES / "tdat_coloc_slice.tdat"
_MAT = _FIXTURES / "deeplasi_export_slice.mat"
_TXT = _FIXTURES / "deeplasi_traces_slice.txt"
_SMD4 = _FIXTURES / "smd_4mol.hdf5"


def _reference_traces(donor: np.ndarray, acceptor: np.ndarray) -> np.ndarray:
    """Stack donor/acceptor ``(N, T)`` into the ``(N, T, 2)`` matcher layout."""
    return np.stack([donor, acceptor], axis=-1)


def _synthetic_tdat(channels_with_data: tuple[int, ...], reference_channel: int) -> Tdat:
    """A minimal :class:`Tdat` with the given channel layout (coords are throwaway)."""
    present = np.zeros(4, dtype=bool)
    for ch in channels_with_data:
        present[ch] = True
    coords = {ch: np.zeros((2, 2), dtype=np.float64) for ch in channels_with_data}
    detection_index = {ch: np.zeros(2, dtype=np.int64) for ch in channels_with_data}
    coloc = TdatColocalization(
        coords=coords,
        detection_index=detection_index,
        channel_present=present,
        file_index=np.ones(2, dtype=np.int64),
        n_molecules=2,
    )
    return Tdat(
        colocalization=coloc,
        corrections=TdatCorrections(0.0, 0.0, 1.0, 0.0, 0.0, 1.0),
        detection=TdatDetectionSettings(mode="intensity", threshold=None),
        channels_with_data=channels_with_data,
        reference_channel=reference_channel,
    )


# --------------------------------------------------------------------------- #
# recover_coordinates
# --------------------------------------------------------------------------- #


def test_recover_from_tdat_two_colour_matches_mat() -> None:
    """The ``.tdat`` reference channel is the donor; both channels equal the ``.mat``."""
    tdat = read_tdat(_TDAT)
    mat = read_deeplasi_mat(_MAT)
    rec = recover_coordinates(tdat=tdat)

    assert rec.source == "tdat"
    assert rec.n_molecules == tdat.colocalization.n_molecules == 250
    # The first four .tdat molecules are the committed .mat slice — locked equal.
    np.testing.assert_array_equal(rec.donor_xy[:4], mat.donor_xy)
    np.testing.assert_array_equal(rec.acceptor_xy[:4], mat.acceptor_xy)


def test_recover_from_mat_uses_pair_columns() -> None:
    """Recovery from the ``.mat`` returns its donor/acceptor ``fret_pairs`` split."""
    mat = read_deeplasi_mat(_MAT)
    rec = recover_coordinates(mat=mat)

    assert rec.source == "mat"
    assert rec.n_molecules == 4
    np.testing.assert_array_equal(rec.donor_xy, mat.donor_xy)
    np.testing.assert_array_equal(rec.acceptor_xy, mat.acceptor_xy)


def test_recover_prefers_tdat_when_both_present() -> None:
    """``prefer`` selects the source when both are given (default the authoritative .tdat)."""
    tdat = read_tdat(_TDAT)
    mat = read_deeplasi_mat(_MAT)

    both_default = recover_coordinates(tdat=tdat, mat=mat)
    assert both_default.source == "tdat"
    assert both_default.n_molecules == 250

    prefer_mat = recover_coordinates(tdat=tdat, mat=mat, prefer="mat")
    assert prefer_mat.source == "mat"
    assert prefer_mat.n_molecules == 4


def test_recover_falls_back_to_the_available_source() -> None:
    """``prefer`` is honoured only when present; otherwise the given source is used."""
    tdat = read_tdat(_TDAT)
    mat = read_deeplasi_mat(_MAT)

    # prefer tdat but only mat available -> mat.
    assert recover_coordinates(mat=mat, prefer="tdat").source == "mat"
    # prefer mat but only tdat available -> tdat.
    assert recover_coordinates(tdat=tdat, prefer="mat").source == "tdat"


def test_recover_requires_a_coordinate_source() -> None:
    with pytest.raises(ValueError, match="needs a .tdat or a .mat"):
        recover_coordinates()


def test_recover_rejects_unknown_prefer() -> None:
    mat = read_deeplasi_mat(_MAT)
    with pytest.raises(ValueError, match="prefer must be"):
        recover_coordinates(mat=mat, prefer="txt")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("channels", "reference"),
    [
        ((0,), 0),  # single channel — no acceptor
        ((0, 1, 2), 0),  # three-colour — outside M7 two-colour scope
        ((1, 2), 0),  # reference channel not colocalized
    ],
)
def test_recover_rejects_non_two_colour_tdat(channels: tuple[int, ...], reference: int) -> None:
    tdat = _synthetic_tdat(channels, reference)
    with pytest.raises(ValueError, match="two-colour donor/acceptor"):
        recover_coordinates(tdat=tdat)


# --------------------------------------------------------------------------- #
# match_smd_to_coordinates
# --------------------------------------------------------------------------- #


def test_cross_check_passes_on_reordered_subset() -> None:
    """A reordered subset of the acquisition's traces re-matches with right coords."""
    mat = read_deeplasi_mat(_MAT)
    rec = recover_coordinates(mat=mat)
    reference = _reference_traces(mat.donor_corrected, mat.acceptor_corrected)

    order = [2, 0, 3]  # tMAVEN may subset + reorder by the GUI selection mask
    smd_subset = reference[order]
    result = match_smd_to_coordinates(smd_subset, reference, rec)

    assert result.all_matched
    assert result.mapping.tolist() == order
    np.testing.assert_array_equal(result.donor_xy, rec.donor_xy[order])
    np.testing.assert_array_equal(result.acceptor_xy, rec.acceptor_xy[order])


def test_cross_check_real_smd_anchor_and_reports_unmatched() -> None:
    """The real ``smd_4mol`` corrected traces cross-check back to the acquisition.

    Only ``smd_4mol`` molecule 0 lies in the committed first-4 slice (it is the
    acquisition's molecule 1); its coordinates are recovered, and the three curated
    molecules outside the slice are reported unmatched, never guessed.
    """
    smd = read_smd(_SMD4)
    txt = read_deeplasi_txt(_TXT)  # the SMD stores the corrected -donc-accc-w series
    mat = read_deeplasi_mat(_MAT)
    rec = recover_coordinates(mat=mat)

    reference = _reference_traces(txt.donor_corrected, txt.acceptor_corrected)
    # The committed .txt/.mat are truncated to 80 frames; compare on that shared span.
    smd_raw = smd.raw[:, : txt.n_frames, :]
    result = match_smd_to_coordinates(smd_raw, reference, rec)

    assert result.mapping.tolist() == [1, -1, -1, -1]
    assert result.matched == [(0, 1)]
    assert result.unmatched == [1, 2, 3]
    assert result.n_matched == 1
    np.testing.assert_array_equal(result.donor_xy[0], rec.donor_xy[1])
    np.testing.assert_array_equal(result.acceptor_xy[0], rec.acceptor_xy[1])
    # Unmatched rows carry NaN coordinates, not a guess.
    assert np.isnan(result.donor_xy[1:]).all()
    assert np.isnan(result.acceptor_xy[1:]).all()


def test_cross_check_honours_id_hint_passthrough() -> None:
    """A correct id hint is honoured; the intensity evidence still decides."""
    mat = read_deeplasi_mat(_MAT)
    rec = recover_coordinates(mat=mat)
    reference = _reference_traces(mat.donor_corrected, mat.acceptor_corrected)

    smd_subset = reference[[3, 1]]
    result = match_smd_to_coordinates(smd_subset, reference, rec, id_hint=[3, 1])
    assert result.mapping.tolist() == [3, 1]
    np.testing.assert_array_equal(result.donor_xy, rec.donor_xy[[3, 1]])


def test_cross_check_requires_aligned_reference() -> None:
    """The reference trace count must equal the recovered molecule count."""
    mat = read_deeplasi_mat(_MAT)
    rec = recover_coordinates(mat=mat)  # 4 molecules
    reference = _reference_traces(mat.donor_corrected, mat.acceptor_corrected)[:3]

    with pytest.raises(ValueError, match="aligned with"):
        match_smd_to_coordinates(reference, reference, rec)


def test_recovered_coordinates_dtype_and_shape() -> None:
    """Recovered coordinates are contiguous float64 ``(N, 2)`` regardless of source."""
    for rec in (
        recover_coordinates(tdat=read_tdat(_TDAT)),
        recover_coordinates(mat=read_deeplasi_mat(_MAT)),
    ):
        assert isinstance(rec, RecoveredCoordinates)
        for arr in (rec.donor_xy, rec.acceptor_xy):
            assert arr.dtype == np.float64
            assert arr.ndim == 2 and arr.shape[1] == 2
            assert arr.flags["C_CONTIGUOUS"]
