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
    DEFAULT_SEED,
    apparent_e_histogram,
    bootstrap_histogram_ci,
    population_apparent_e_histogram,
    population_apparent_e_histogram_ci,
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


# --- bootstrap CI: pure core (bootstrap_histogram_ci) ------------------------


def _const_molecule(e: float, m: int) -> np.ndarray:
    """One molecule's apparent-E array: constant ``e`` over ``m`` frames."""
    return np.full(m, e, dtype="float64")


def test_bootstrap_ci_identical_molecules_have_zero_spread() -> None:
    # Five identical molecules: every resample pools the same distribution, so the
    # band collapses onto the point estimate (no cross-sample variability).
    mols = [_const_molecule(0.5, 8) for _ in range(5)]
    ci = bootstrap_histogram_ci(mols, n_resamples=128, density=False)
    assert ci.n_resamples == 128
    assert ci.ci_level == 0.95
    assert ci.seed == DEFAULT_SEED
    assert np.all(ci.std == 0.0)
    np.testing.assert_allclose(ci.ci_low, ci.counts)
    np.testing.assert_allclose(ci.ci_high, ci.counts)
    assert np.all(np.isfinite(ci.counts))


def test_bootstrap_ci_two_populations_have_spread() -> None:
    mols = [_const_molecule(0.25, 8) for _ in range(4)]
    mols += [_const_molecule(0.75, 8) for _ in range(4)]
    ci = bootstrap_histogram_ci(mols, n_resamples=256, density=False)
    edges = ci.bin_edges
    b25 = int(np.searchsorted(edges, 0.25, side="right") - 1)
    b75 = int(np.searchsorted(edges, 0.75, side="right") - 1)
    b_empty = int(np.searchsorted(edges, 0.0, side="right") - 1)  # no molecule at E=0.0
    assert ci.std[b25] > 0.0
    assert ci.std[b75] > 0.0
    assert ci.std[b_empty] == 0.0
    assert np.all(ci.ci_low <= ci.ci_high)
    # observed count within [min, max] of the resamples -> error bars non-negative
    assert np.all(ci.yerr_low >= 0.0)
    assert np.all(ci.yerr_high >= 0.0)


def test_bootstrap_ci_reproducible_seeded() -> None:
    mols = [_const_molecule(e, 6) for e in (0.2, 0.4, 0.6, 0.8)]
    a = bootstrap_histogram_ci(mols, n_resamples=200, seed=7)
    b = bootstrap_histogram_ci(mols, n_resamples=200, seed=7)
    np.testing.assert_array_equal(a.ci_low, b.ci_low)
    np.testing.assert_array_equal(a.ci_high, b.ci_high)
    np.testing.assert_array_equal(a.std, b.std)
    # a different seed moves the band on a multi-population sample (deterministic).
    c = bootstrap_histogram_ci(mols, n_resamples=200, seed=8)
    assert not np.array_equal(a.std, c.std)


def test_bootstrap_ci_empty_is_zeros_not_nan() -> None:
    ci = bootstrap_histogram_ci([], n_resamples=50)
    assert ci.n_molecules is None  # pure core: no molecule count attached
    assert ci.nbins == DEFAULT_NBINS
    assert np.all(ci.counts == 0.0)
    assert np.all(ci.ci_low == 0.0)
    assert np.all(ci.ci_high == 0.0)
    assert np.all(ci.std == 0.0)
    assert not np.any(np.isnan(ci.counts))
    assert ci.n_resamples == 50


def test_bootstrap_ci_single_resample_zero_std() -> None:
    mols = [_const_molecule(0.3, 5), _const_molecule(0.7, 5)]
    ci = bootstrap_histogram_ci(mols, n_resamples=1)
    assert np.all(ci.std == 0.0)  # ddof=1 undefined for one replicate -> zeros by contract


def test_bootstrap_ci_validates_params() -> None:
    mols = [_const_molecule(0.5, 4)]
    with pytest.raises(ValueError, match="ci_level"):
        bootstrap_histogram_ci(mols, ci_level=0.0)
    with pytest.raises(ValueError, match="ci_level"):
        bootstrap_histogram_ci(mols, ci_level=1.0)
    with pytest.raises(ValueError, match="n_resamples"):
        bootstrap_histogram_ci(mols, n_resamples=0)
    with pytest.raises(ValueError, match="length"):
        bootstrap_histogram_ci(mols, per_molecule_weights=[])
    with pytest.raises(ValueError, match="shape"):
        bootstrap_histogram_ci(mols, per_molecule_weights=[np.ones(3)])


def test_bootstrap_ci_equal_weight_totals_one_per_molecule() -> None:
    # One molecule, equal-weight 1/m per frame: each replicate draws it once, so
    # its total weight is exactly 1 (density-free), and a lone molecule has no
    # cross-sample spread.
    m = 10
    mols = [_const_molecule(0.5, m)]
    weights = [np.full(m, 1.0 / m)]
    ci = bootstrap_histogram_ci(mols, per_molecule_weights=weights, density=False, n_resamples=32)
    assert float(ci.counts.sum()) == pytest.approx(1.0)
    assert np.all(ci.std == 0.0)


# --- bootstrap CI: store level (population_apparent_e_histogram_ci) ----------


def test_population_ci_point_estimate_matches_histogram(tmp_path) -> None:
    t = 10
    donor, acceptor = _constant_e_traces([0.25, 0.5, 0.75], t)
    proj, _ = _build_store(tmp_path / "h.tether", donor, acceptor)

    point = population_apparent_e_histogram(proj)
    ci = population_apparent_e_histogram_ci(proj, n_resamples=100)
    assert ci.n_molecules == 3
    assert ci.histogram.n_molecules == 3
    np.testing.assert_allclose(ci.counts, point.counts)
    np.testing.assert_allclose(ci.bin_edges, point.bin_edges)
    assert np.all(ci.ci_low <= ci.ci_high)
    assert not np.any(np.isnan(ci.ci_low))


def test_population_ci_excludes_rejected_by_default(tmp_path) -> None:
    t = 10
    donor, acceptor = _constant_e_traces([0.25, 0.75], t)
    proj, keys = _build_store(tmp_path / "h.tether", donor, acceptor)
    proj.reject(keys[0], labeler="tester")
    ci = population_apparent_e_histogram_ci(proj, n_resamples=64)
    assert ci.n_molecules == 1
    with_rej = population_apparent_e_histogram_ci(proj, include_rejected=True, n_resamples=64)
    assert with_rej.n_molecules == 2


def test_population_ci_reproducible_seeded(tmp_path) -> None:
    t = 12
    donor, acceptor = _constant_e_traces([0.2, 0.5, 0.8], t)
    proj, _ = _build_store(tmp_path / "h.tether", donor, acceptor)
    a = population_apparent_e_histogram_ci(proj, n_resamples=150, seed=3)
    b = population_apparent_e_histogram_ci(proj, n_resamples=150, seed=3)
    np.testing.assert_array_equal(a.ci_low, b.ci_low)
    np.testing.assert_array_equal(a.ci_high, b.ci_high)
    np.testing.assert_array_equal(a.std, b.std)


def test_population_ci_all_rejected_is_zeros(tmp_path) -> None:
    t = 8
    donor, acceptor = _constant_e_traces([0.5], t)
    proj, keys = _build_store(tmp_path / "h.tether", donor, acceptor)
    proj.reject(keys[0], labeler="tester")
    ci = population_apparent_e_histogram_ci(proj, n_resamples=40)
    assert ci.n_molecules == 0
    assert float(ci.counts.sum()) == 0.0
    assert np.all(ci.ci_low == 0.0)
    assert np.all(ci.ci_high == 0.0)
    assert not np.any(np.isnan(ci.counts))


def test_population_ci_equal_weight_changes_point(tmp_path) -> None:
    t = 20
    donor, acceptor = _constant_e_traces([0.25, 0.75], t)
    path = tmp_path / "h.tether"
    proj, _ = _build_store(path, donor, acceptor)
    _set_analysis_window(path, 0, 0, 20)  # long molecule (E=0.25): 20 frames
    _set_analysis_window(path, 1, 0, 4)  # short molecule (E=0.75): 4 frames

    frame_w = population_apparent_e_histogram_ci(path, density=False, n_resamples=32)
    per_mol = population_apparent_e_histogram_ci(
        path, density=False, per_molecule_equal_weight=True, n_resamples=32
    )
    edges = frame_w.bin_edges
    b25 = int(np.searchsorted(edges, 0.25, side="right") - 1)
    b75 = int(np.searchsorted(edges, 0.75, side="right") - 1)
    assert frame_w.counts[b25] == 20.0
    assert frame_w.counts[b75] == 4.0
    assert per_mol.counts[b25] == pytest.approx(1.0)
    assert per_mol.counts[b75] == pytest.approx(1.0)
