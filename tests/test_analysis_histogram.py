# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""1-D population apparent-E histogram (M2 S8, FR-ANALYZE; Appendix C plot A1).

Locks the headless histogram: the pure-array binning core and the store-level
``population_apparent_e_histogram`` that reproduces the MVP histogram from the API
(PRD §9 M2), with the §7.5 rejected-exclusion filter and the §7.7 per-molecule
equal-weight toggle. All headless -> runs in the base CI matrix.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("h5py")

import h5py  # noqa: E402
import numpy as np  # noqa: E402

from tether.analysis import (  # noqa: E402
    DEFAULT_NBINS,
    DEFAULT_RANGE,
    apparent_e_histogram,
    population_apparent_e_histogram,
)
from tether.fret.efficiency import apparent_fret  # noqa: E402
from tether.imaging.aperture import IntegratedTraces  # noqa: E402
from tether.imaging.calibrate import RegistrationMap  # noqa: E402
from tether.imaging.coloc import ColocalizedMolecules  # noqa: E402
from tether.imaging.extract import (  # noqa: E402
    MoleculeTraces,
    MovieMetadata,
    read_molecules,
    write_extraction,
)
from tether.imaging.register import PolyTransform2D  # noqa: E402
from tether.imaging.split import ChannelGeometry  # noqa: E402
from tether.io.filename import parse_filename  # noqa: E402
from tether.io.schema import create_project  # noqa: E402
from tether.project.core import Project  # noqa: E402

_PARSED = parse_filename("Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif")
_WINDOW = 21


def _distinct_coords(n: int) -> np.ndarray:
    return np.array([[12.0 + 1.7 * i, 14.0 + 2.3 * (i % 7)] for i in range(n)], dtype="float64")


def _reg_map() -> RegistrationMap:
    poly = PolyTransform2D(
        a=np.array([0.0, 1.0, 0.0, 0.0, 0.0, 0.0]),
        b=np.array([0.0, 0.0, 1.0, 0.0, 0.0, 0.0]),
        norm_xy=np.eye(3),
        norm_uv=np.eye(3),
    )
    return RegistrationMap(
        reference_channel=1,
        moving_channel=2,
        ref_to_moving=poly,
        moving_to_ref=poly,
        rms_residual=0.1,
        n_control_points=100,
    )


def _integrated(intensity: np.ndarray) -> IntegratedTraces:
    intensity = np.asarray(intensity, dtype="float64")
    n = intensity.shape[0]
    background = np.full_like(intensity, 100.0)
    return IntegratedTraces(
        intensity=intensity,
        total=intensity + background,
        background=background,
        valid=np.ones(n, dtype=bool),
    )


def _build_store(path: Path, donor: np.ndarray, acceptor: np.ndarray) -> tuple[Project, list[str]]:
    """Write a ``.tether`` with controlled donor/acceptor *corrected* traces."""
    donor = np.asarray(donor, dtype="float64")
    acceptor = np.asarray(acceptor, dtype="float64")
    n, t = donor.shape
    coords = _distinct_coords(n)
    mols = ColocalizedMolecules(
        donor_xy=coords,
        acceptor_xy=coords,
        acceptor_detected=np.zeros(n, dtype=bool),
        donor_index=np.arange(n, dtype=np.intp),
        acceptor_index=np.full(n, -1, dtype=np.intp),
    )
    traces = MoleculeTraces(
        donor=_integrated(donor),
        acceptor=_integrated(acceptor),
        donor_patches=np.zeros((n, _WINDOW, _WINDOW), dtype="float32"),
        acceptor_patches=np.zeros((n, _WINDOW, _WINDOW), dtype="float32"),
        window=_WINDOW,
        disk_radius=3.0,
        ring_inner=6.0,
        ring_outer=8.0,
        bg_window=10,
    )
    movie = MovieMetadata(
        movie_id="mov-1",
        sha256="a" * 64,
        n_frames=t,
        height=64,
        width=64,
        donor_geometry=ChannelGeometry(crop=(1, 1, 64, 64)),
        acceptor_geometry=ChannelGeometry(crop=(1, 65, 64, 128)),
    )
    create_project(path, overwrite=True)
    write_extraction(
        path,
        movie=movie,
        molecules=mols,
        traces=traces,
        parsed=_PARSED,
        registration_map=_reg_map(),
    )
    proj = Project.open(path)
    keys = [
        k.decode() if isinstance(k, bytes) else str(k) for k in read_molecules(path)["molecule_key"]
    ]
    return proj, keys


def _set_analysis_window(path: Path, row: int, lo: int, hi: int) -> None:
    with h5py.File(path, "r+") as f:
        table = f["molecules"]["table"][:]
        table["analysis_window"][row] = (lo, hi)
        f["molecules"]["table"][:] = table


def _constant_e_traces(e_values: list[float], t: int) -> tuple[np.ndarray, np.ndarray]:
    """(n, t) donor/acceptor whose apparent E is ``e_values[i]`` on every frame.

    With donor = 1 - E and acceptor = E (scaled), A / (D + A) == E exactly.
    """
    n = len(e_values)
    donor = np.empty((n, t), dtype="float64")
    acceptor = np.empty((n, t), dtype="float64")
    for i, e in enumerate(e_values):
        donor[i, :] = (1.0 - e) * 1000.0
        acceptor[i, :] = e * 1000.0
    return donor, acceptor


# --- pure-array core ---------------------------------------------------------


def test_default_binning_matches_tmaven_a1() -> None:
    values = np.array([0.0, 0.25, 0.5, 0.75, 1.0])
    hist = apparent_e_histogram(values)
    assert hist.nbins == DEFAULT_NBINS == 151
    assert hist.bin_edges.shape == (152,)
    assert hist.value_range == DEFAULT_RANGE == (-0.25, 1.25)
    assert hist.bin_edges[0] == pytest.approx(-0.25)
    assert hist.bin_edges[-1] == pytest.approx(1.25)
    # density integrates to 1 over the range (all samples in range here)
    width = np.diff(hist.bin_edges)
    assert float(np.sum(hist.counts * width)) == pytest.approx(1.0)
    assert hist.n_samples == 5


def test_counts_mode_sums_to_finite_in_range() -> None:
    values = np.array([0.1, 0.2, 0.9, 2.0])  # 2.0 is outside (-0.25, 1.25)
    hist = apparent_e_histogram(values, density=False)
    assert hist.n_samples == 4  # all finite fed to the histogram
    assert float(hist.counts.sum()) == 3.0  # but the out-of-range 2.0 lands in no bin


def test_non_finite_dropped() -> None:
    values = np.array([0.3, np.nan, 0.7, np.inf, -np.inf])
    hist = apparent_e_histogram(values, density=False)
    assert hist.n_samples == 2
    assert float(hist.counts.sum()) == 2.0


def test_weights_align_and_validate() -> None:
    values = np.array([0.25, 0.75])
    weights = np.array([3.0, 1.0])
    hist = apparent_e_histogram(values, density=False, weights=weights)
    assert float(hist.counts.sum()) == pytest.approx(4.0)
    with pytest.raises(ValueError, match="weights shape"):
        apparent_e_histogram(values, weights=np.array([1.0, 2.0, 3.0]))


def test_core_validates_range_and_bins() -> None:
    with pytest.raises(ValueError, match="high > low"):
        apparent_e_histogram(np.array([0.5]), value_range=(1.0, 1.0))
    with pytest.raises(ValueError, match="positive integer"):
        apparent_e_histogram(np.array([0.5]), bins=0)


# --- store-level: reproduce the MVP histogram (PRD §9 M2) --------------------


def test_population_reproduces_mvp_histogram(tmp_path) -> None:
    t = 10
    donor, acceptor = _constant_e_traces([0.25, 0.5, 0.75], t)
    proj, _ = _build_store(tmp_path / "h.tether", donor, acceptor)

    # Analytic oracle: 10 frames each at E = 0.25 / 0.5 / 0.75.
    expected_vals = np.concatenate([np.full(t, e) for e in (0.25, 0.5, 0.75)])
    exp_counts, exp_edges = np.histogram(
        expected_vals, bins=DEFAULT_NBINS, range=DEFAULT_RANGE, density=True
    )

    hist = population_apparent_e_histogram(proj)
    assert hist.n_molecules == 3
    assert hist.n_samples == 3 * t
    np.testing.assert_allclose(hist.bin_edges, exp_edges)
    np.testing.assert_allclose(hist.counts, exp_counts)


def test_population_excludes_rejected_by_default(tmp_path) -> None:
    t = 10
    donor, acceptor = _constant_e_traces([0.25, 0.75], t)
    proj, keys = _build_store(tmp_path / "h.tether", donor, acceptor)
    proj.reject(keys[0], labeler="tester")  # the E = 0.25 molecule

    default = population_apparent_e_histogram(proj)
    assert default.n_molecules == 1
    assert default.n_samples == t

    with_rejected = population_apparent_e_histogram(proj, include_rejected=True)
    assert with_rejected.n_molecules == 2
    assert with_rejected.n_samples == 2 * t


def test_population_respects_analysis_window(tmp_path) -> None:
    t = 20
    donor, acceptor = _constant_e_traces([0.5], t)
    path = tmp_path / "h.tether"
    proj, _ = _build_store(path, donor, acceptor)
    _set_analysis_window(path, 0, 4, 9)  # 5 frames

    hist = population_apparent_e_histogram(proj)
    assert hist.n_molecules == 1
    assert hist.n_samples == 5


def test_population_per_molecule_equal_weight(tmp_path) -> None:
    # A long molecule (E = 0.25) and a short one (E = 0.75); frame-weighting lets
    # the long trace dominate, per-molecule weighting equalizes them.
    t = 20
    donor, acceptor = _constant_e_traces([0.25, 0.75], t)
    path = tmp_path / "h.tether"
    proj, _ = _build_store(path, donor, acceptor)
    _set_analysis_window(path, 0, 0, 20)  # E=0.25 molecule: 20 frames
    _set_analysis_window(path, 1, 0, 4)  # E=0.75 molecule: 4 frames

    def _bin_of(hist, e):
        return int(np.searchsorted(hist.bin_edges, e, side="right") - 1)

    frame_w = population_apparent_e_histogram(path, density=False)
    b25, b75 = _bin_of(frame_w, 0.25), _bin_of(frame_w, 0.75)
    assert frame_w.counts[b25] == 20.0
    assert frame_w.counts[b75] == 4.0

    per_mol = population_apparent_e_histogram(path, density=False, per_molecule_equal_weight=True)
    assert per_mol.per_molecule_equal_weight is True
    assert per_mol.counts[b25] == pytest.approx(1.0)
    assert per_mol.counts[b75] == pytest.approx(1.0)


def test_population_all_rejected_is_empty(tmp_path) -> None:
    t = 8
    donor, acceptor = _constant_e_traces([0.5], t)
    proj, keys = _build_store(tmp_path / "h.tether", donor, acceptor)
    proj.reject(keys[0], labeler="tester")
    hist = population_apparent_e_histogram(proj)
    assert hist.n_molecules == 0
    assert hist.n_samples == 0
    assert float(hist.counts.sum()) == 0.0


def test_population_matches_manual_pool_with_zero_total_gap(tmp_path) -> None:
    # A frame with D + A == 0 -> apparent_fret NaN -> dropped, never counted.
    t = 6
    donor, acceptor = _constant_e_traces([0.5], t)
    donor[0, 2] = 0.0
    acceptor[0, 2] = 0.0
    path = tmp_path / "h.tether"
    proj, _ = _build_store(path, donor, acceptor)

    e = apparent_fret(donor[0], acceptor[0])
    expected = e[np.isfinite(e)]
    exp_counts, _ = np.histogram(expected, bins=DEFAULT_NBINS, range=DEFAULT_RANGE, density=False)

    hist = population_apparent_e_histogram(proj, density=False)
    assert hist.n_samples == t - 1  # the zero-total frame is dropped
    np.testing.assert_allclose(hist.counts, exp_counts)
