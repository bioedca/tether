# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Decode tests for the Deep-LASI TIRFdata ``.tdat`` reader (PRD §7.8, App B; M0.5 S6).

Locks the colocalized-coordinate decode and — critically — the Appendix-B
correction-factor remap (Deep-LASI's ``Alpha``/``Beta`` naming is inverted
relative to Tether's), against a real 250-molecule slice of the UCKOPSB ``.tdat``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("h5py")
pytest.importorskip("numpy")

import h5py  # noqa: E402  (guarded by importorskip above)
import numpy as np  # noqa: E402

from tether.io import read_tdat, remap_correction_factors  # noqa: E402
from tether.io.tdat import Tdat  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "tdat_coloc_slice.tdat"


@pytest.fixture(scope="module")
def tdat() -> Tdat:
    return read_tdat(FIXTURE)


def _write_minimal_tdat(path: Path, particles: object) -> None:
    """Write a minimal ``temp`` struct with the given ``ParticlesColocalized``."""
    with h5py.File(path, "w") as f:
        temp = f.create_group("temp")
        if isinstance(particles, np.ndarray) and particles.dtype == h5py.ref_dtype:
            refs = f.create_group("#refs#")
            table = refs.create_dataset("a", data=particles_table())
            pc = temp.create_dataset("ParticlesColocalized", shape=(1, 1), dtype=h5py.ref_dtype)
            pc[0, 0] = table.ref
        else:
            # MATLAB stores an empty [] as a small non-reference uint64 dims marker.
            temp.create_dataset("ParticlesColocalized", data=np.array([0, 0], dtype=np.uint64))
        for name in ("DefaultAlpha", "DefaultBeta", "DefaultGamma"):
            temp.create_dataset(name, data=np.array([[0.0]]))
        temp.create_dataset("ChannelsWithData", data=np.array([[1.0], [2.0]]))
        temp.create_dataset("MappingReferenceChannel", data=np.array([[1.0]]))


def particles_table() -> np.ndarray:
    """A two-molecule (17, 2) findColoc table in MATLAB-transposed orientation."""
    rows = np.zeros((2, 17), dtype=np.float64)
    rows[:, 0:3] = [[10.0, 20.0, 1.0], [30.0, 40.0, 2.0]]  # ch1 X,Y,#
    rows[:, 12] = 1.0  # bCh1
    rows[:, 16] = 1.0  # nFile
    return rows.T


def test_decode_shape_and_channels(tdat: Tdat) -> None:
    coloc = tdat.colocalization
    assert coloc.n_molecules == 250
    assert coloc.channel_present.tolist() == [True, True, False, False]
    assert sorted(coloc.coords) == [0, 1]
    assert tdat.channels_with_data == (0, 1)
    assert tdat.reference_channel == 0
    assert coloc.coords[0].shape == (250, 2)
    assert coloc.coords[1].shape == (250, 2)
    # absent channels carry no coordinate entry at all
    assert 2 not in coloc.coords
    assert 3 not in coloc.coords


def test_first_molecule_coords_are_xy_zero_based(tdat: Tdat) -> None:
    coloc = tdat.colocalization
    # findColoc row 0 (1-based, stored [x, y]): donor X1=485 Y1=15 ; acceptor X2=487 Y2=23.
    # Tether is 0-based [x, y] (PRD §11.1): subtract 1, no flip.
    assert coloc.coords[0][0].tolist() == pytest.approx([484.0, 14.0], abs=1e-6)
    assert coloc.coords[1][0].tolist() == pytest.approx([486.0, 22.0], abs=1e-6)
    # detection indices are source bookkeeping, kept 1-based and integer.
    assert coloc.detection_index[0][:3].tolist() == [9, 13, 17]
    assert coloc.detection_index[1][:3].tolist() == [31, 33, 27]
    assert coloc.detection_index[0].dtype == np.int64
    assert coloc.file_index.tolist() == [1] * 250


def test_coords_within_frame_and_finite(tdat: Tdat) -> None:
    for xy in tdat.colocalization.coords.values():
        assert xy.dtype == np.float64
        assert np.all(np.isfinite(xy))
        assert xy.min() >= 0.0  # 0-based: no negative pixels
        assert xy[:, 0].max() < 512.0  # within a 512-wide reference frame
        assert xy[:, 1].max() < 512.0


def test_donor_acceptor_not_swapped(tdat: Tdat) -> None:
    # Donor (ch1) and acceptor (ch2) share near-equal x and a small signed y split
    # offset in reference-channel coords — a swap or flip would blow these up.
    donor = tdat.colocalization.coords[0]
    acceptor = tdat.colocalization.coords[1]
    assert np.median(np.abs(acceptor[:, 0] - donor[:, 0])) < 5.0
    assert 0.0 < np.median(acceptor[:, 1] - donor[:, 1]) < 20.0


def test_factors_decoded_and_remapped(tdat: Tdat) -> None:
    corr = tdat.corrections
    # This acquisition stores zero default factors.
    assert (corr.deeplasi_alpha, corr.deeplasi_beta, corr.deeplasi_gamma) == (0.0, 0.0, 0.0)
    assert (corr.alpha, corr.delta, corr.gamma) == (0.0, 0.0, 0.0)


def test_appendix_b_remap_is_not_a_naming_passthrough() -> None:
    # The load-bearing correctness check (PRD App B / §7.8): Deep-LASI's Beta is
    # leakage and its Alpha is direct excitation — the OPPOSITE of Tether's naming.
    corr = remap_correction_factors(deeplasi_alpha=0.5, deeplasi_beta=0.1, deeplasi_gamma=1.2)
    assert corr.alpha == 0.1  # Tether leakage = Deep-LASI Beta (NOT Deep-LASI Alpha)
    assert corr.delta == 0.0  # direct excitation inert without ALEX (Deep-LASI Alpha dropped)
    assert corr.gamma == 1.2  # gamma is gamma
    # Deep-LASI originals retained for provenance.
    assert corr.deeplasi_alpha == 0.5
    assert corr.deeplasi_beta == 0.1


def test_empty_colocalization_returns_zero(tmp_path: Path) -> None:
    path = tmp_path / "empty.tdat"
    _write_minimal_tdat(path, particles=None)
    result = read_tdat(path)
    assert result.colocalization.n_molecules == 0
    assert result.colocalization.coords == {}
    assert not result.colocalization.channel_present.any()
    # factors and channel layout still decode from a coordinate-less file.
    assert result.channels_with_data == (0, 1)
    assert result.reference_channel == 0


def test_minimal_referenced_table_decodes(tmp_path: Path) -> None:
    path = tmp_path / "ref.tdat"
    _write_minimal_tdat(path, particles=np.empty((1, 1), dtype=h5py.ref_dtype))
    coloc = read_tdat(path).colocalization
    assert coloc.n_molecules == 2
    assert np.allclose(coloc.coords[0], [[9.0, 19.0], [29.0, 39.0]])
    assert coloc.channel_present.tolist() == [True, False, False, False]


def test_not_a_tdat_raises(tmp_path: Path) -> None:
    bad = tmp_path / "not_a.tdat"
    with h5py.File(bad, "w") as f:
        f.create_dataset("something", data=[1, 2, 3])
    with pytest.raises(ValueError, match="not a Deep-LASI TIRFdata"):
        read_tdat(bad)


def test_corrections_dataclass_is_frozen() -> None:
    corr = remap_correction_factors(0.0, 0.0, 0.0)
    with pytest.raises(AttributeError):
        corr.alpha = 1.0  # type: ignore[misc]
