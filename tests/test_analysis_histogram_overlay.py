# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""A1 model-Gaussian histogram overlay (M6, FR-ANALYZE; Appendix C plot A1).

Locks the headless A1 ``model_on`` overlay: the pure-array core
``model_gaussian_overlay`` (each state's ``frac·𝒩(mean, var)`` and their sum,
faithful to tMAVEN ``data_hist1d.py`` [Gopich2010]) and the store-level
``population_model_gaussian_overlay`` that reads a persisted ``/idealization``
population model and builds that overlay. The overlay is the idealized model's own
state emissions — not a fresh GMM fit — and a model without per-state spread is
**withheld, never fabricated**. All headless -> runs in the base CI matrix.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("h5py")

import numpy as np  # noqa: E402
from scipy.stats import norm  # noqa: E402

from tether.analysis import (  # noqa: E402
    DEFAULT_OVERLAY_POINTS,
    DEFAULT_RANGE,
    ModelGaussianOverlay,
    apparent_e_histogram,
    model_gaussian_overlay,
    population_model_gaussian_overlay,
)
from tether.io.schema import create_project  # noqa: E402
from tether.project.core import Project  # noqa: E402
from tether.project.idealize import write_idealization_model  # noqa: E402

# --- pure core: faithfulness + shape -----------------------------------------


def _tmaven_reference(means, variances, frac, lo, hi, n):
    """The tMAVEN ``data_hist1d.py`` ``model_on`` loop, verbatim, as the oracle."""
    means = np.asarray(means, dtype="float64")
    variances = np.asarray(variances, dtype="float64")
    frac = np.asarray(frac, dtype="float64")
    x = np.linspace(lo, hi, n)
    y = np.zeros_like(x)
    comps = []
    for i in range(means.size):
        yi = (
            frac[i]
            * 1.0
            / np.sqrt(2.0 * np.pi * variances[i])
            * np.exp(-0.5 / variances[i] * (x - means[i]) ** 2.0)
        )
        comps.append(yi)
        y += yi
    return x, np.array(comps), y


def test_overlay_matches_tmaven_formula_exactly() -> None:
    """The overlay reproduces tMAVEN's per-state Gaussian loop bit-for-bit."""
    means = np.array([0.2, 0.5, 0.85])
    variances = np.array([0.01, 0.02, 0.005])
    frac = np.array([0.5, 0.3, 0.2])
    ov = model_gaussian_overlay(means, variances, frac)

    x_ref, comps_ref, total_ref = _tmaven_reference(
        means, variances, frac, DEFAULT_RANGE[0], DEFAULT_RANGE[1], DEFAULT_OVERLAY_POINTS
    )
    np.testing.assert_allclose(ov.x, x_ref)
    np.testing.assert_allclose(ov.components, comps_ref)
    np.testing.assert_allclose(ov.total, total_ref)


def test_overlay_component_is_scaled_gaussian() -> None:
    """Each component equals ``frac·Normal(mean, sqrt(var))`` (scipy reference)."""
    means = np.array([0.3, 0.7])
    variances = np.array([0.02, 0.015])
    frac = np.array([0.6, 0.4])
    ov = model_gaussian_overlay(means, variances, frac, n_points=257)

    for i in range(2):
        expected = frac[i] * norm.pdf(ov.x, loc=means[i], scale=np.sqrt(variances[i]))
        np.testing.assert_allclose(ov.components[i], expected, rtol=1e-10, atol=1e-12)
    np.testing.assert_allclose(ov.total, ov.components.sum(axis=0))


def test_overlay_shapes_and_grid() -> None:
    ov = model_gaussian_overlay([0.4], [0.01], [1.0], value_range=(0.0, 1.0), n_points=64)
    assert ov.nstates == 1
    assert ov.n_points == 64
    assert ov.x.shape == (64,)
    assert ov.components.shape == (1, 64)
    assert ov.total.shape == (64,)
    assert ov.value_range == (0.0, 1.0)
    assert ov.x[0] == 0.0 and ov.x[-1] == 1.0  # linspace spans the range inclusively
    assert ov.model_name is None


def test_overlay_defaults_align_with_a1_histogram() -> None:
    """Default grid spans the A1 histogram range at the tMAVEN 1001-point fidelity."""
    ov = model_gaussian_overlay([0.5], [0.01], [1.0])
    assert ov.value_range == DEFAULT_RANGE
    assert ov.n_points == DEFAULT_OVERLAY_POINTS == 1001
    assert ov.x[0] == DEFAULT_RANGE[0]
    assert ov.x[-1] == DEFAULT_RANGE[1]


def test_single_unit_gaussian_integrates_to_one() -> None:
    """A unit-population Gaussian well inside a wide range integrates to ~1 (density)."""
    ov = model_gaussian_overlay([0.5], [0.001], [1.0], value_range=(-2.0, 3.0), n_points=20001)
    area = np.trapezoid(ov.total, ov.x)
    assert area == pytest.approx(1.0, abs=1e-3)


def test_mixture_area_equals_frac_sum() -> None:
    """The mixture integrates to ``sum(frac)`` over a wide range (each Gaussian -> 1)."""
    means = np.array([-0.5, 0.5, 1.5])
    variances = np.array([0.004, 0.004, 0.004])
    frac = np.array([0.2, 0.5, 0.3])  # sums to 1
    ov = model_gaussian_overlay(means, variances, frac, value_range=(-3.0, 4.0), n_points=40001)
    assert np.trapezoid(ov.total, ov.x) == pytest.approx(frac.sum(), abs=2e-3)


def test_no_renormalization_to_finite_range() -> None:
    """A state whose mass spills past an edge leaves total < 1 there (honest, not rescaled)."""
    # Mean at the right edge -> only the left half of its Gaussian is inside the range.
    ov = model_gaussian_overlay([1.25], [0.01], [1.0], value_range=(-0.25, 1.25), n_points=5001)
    area = np.trapezoid(ov.total, ov.x)
    assert area == pytest.approx(0.5, abs=1e-2)  # half the mass captured, not renormalized to 1


def test_total_peaks_near_state_means() -> None:
    means = np.array([0.25, 0.8])
    ov = model_gaussian_overlay(means, [0.002, 0.002], [0.5, 0.5], n_points=1501)
    peak_x = ov.x[int(np.argmax(ov.total))]
    assert min(abs(peak_x - means[0]), abs(peak_x - means[1])) < 0.02


def test_overlay_is_deterministic() -> None:
    args = ([0.3, 0.7], [0.01, 0.02], [0.55, 0.45])
    a = model_gaussian_overlay(*args)
    b = model_gaussian_overlay(*args)
    np.testing.assert_array_equal(a.total, b.total)
    np.testing.assert_array_equal(a.components, b.components)


def test_model_name_propagates() -> None:
    ov = model_gaussian_overlay([0.5], [0.01], [1.0], model_name="vbconhmm")
    assert ov.model_name == "vbconhmm"


def test_zero_population_state_contributes_nothing() -> None:
    """A frac=0 state is allowed (a real population can be empty) and adds a zero curve."""
    ov = model_gaussian_overlay([0.3, 0.7], [0.01, 0.01], [1.0, 0.0])
    np.testing.assert_array_equal(ov.components[1], np.zeros_like(ov.x))
    np.testing.assert_allclose(ov.total, ov.components[0])


# --- pure core: validation (withhold, never fabricate) -----------------------


def test_mismatched_lengths_raise() -> None:
    with pytest.raises(ValueError, match="same length"):
        model_gaussian_overlay([0.3, 0.7], [0.01], [0.5, 0.5])
    with pytest.raises(ValueError, match="same length"):
        model_gaussian_overlay([0.3, 0.7], [0.01, 0.02], [1.0])


def test_empty_model_raises() -> None:
    with pytest.raises(ValueError, match="at least one state"):
        model_gaussian_overlay([], [], [])


@pytest.mark.parametrize("bad", [0.0, -0.01, np.nan, np.inf])
def test_nonpositive_or_nonfinite_variance_raises(bad) -> None:
    with pytest.raises(ValueError, match="variance must be finite and > 0"):
        model_gaussian_overlay([0.3, 0.7], [0.01, bad], [0.5, 0.5])


@pytest.mark.parametrize("bad", [-0.1, np.nan, np.inf])
def test_negative_or_nonfinite_frac_raises(bad) -> None:
    with pytest.raises(ValueError, match="frac' must be finite and >= 0"):
        model_gaussian_overlay([0.3, 0.7], [0.01, 0.01], [bad, 0.5])


def test_degenerate_value_range_raises() -> None:
    with pytest.raises(ValueError, match="high > low"):
        model_gaussian_overlay([0.5], [0.01], [1.0], value_range=(1.0, 1.0))
    with pytest.raises(ValueError, match="high > low"):
        model_gaussian_overlay([0.5], [0.01], [1.0], value_range=(1.0, 0.0))


def test_too_few_points_raises() -> None:
    with pytest.raises(ValueError, match="n_points must be >= 2"):
        model_gaussian_overlay([0.5], [0.01], [1.0], n_points=1)


# --- overlay vs an actual sampled histogram (integration) --------------------


def test_overlay_tracks_a_sampled_histogram() -> None:
    """A histogram of samples drawn from the mixture matches the overlay density."""
    means = np.array([0.25, 0.75])
    variances = np.array([0.0025, 0.0025])
    frac = np.array([0.6, 0.4])
    rng = np.random.default_rng(0)  # PCG64 -> identical samples across the 3-OS matrix
    n = 400_000
    which = rng.random(n) < frac[0]
    samples = np.where(
        which,
        rng.normal(means[0], np.sqrt(variances[0]), n),
        rng.normal(means[1], np.sqrt(variances[1]), n),
    )
    hist = apparent_e_histogram(samples, bins=60, value_range=DEFAULT_RANGE, density=True)
    ov = model_gaussian_overlay(means, variances, frac)
    at_centers = np.interp(hist.bin_centers, ov.x, ov.total)
    # Density scale: the model curve tracks the empirical histogram within sampling noise.
    assert np.max(np.abs(hist.counts - at_centers)) < 0.25


# --- store-level: persisted /idealization population model -------------------


def _write_model(
    path: Path,
    *,
    model_type: str,
    means,
    variances,
    frac,
) -> None:
    """Persist a minimal ``/idealization/{model_type}`` with the given members."""
    create_project(path, overwrite=True)
    means_arr = np.asarray(means, dtype="float64")
    n_states = int(means_arr.shape[0])
    write_idealization_model(
        path,
        model_name=model_type,
        model_type=model_type,
        nstates=n_states,
        dtype="FRET",
        means=means_arr,
        variances=None if variances is None else np.asarray(variances, dtype="float64"),
        tmatrix=None,
        norm_tmatrix=None,
        elbo=1.0,
        idealized=np.array([[means_arr[0], means_arr[-1]]]),
        state_paths=np.array([[0, n_states - 1]]),
        molecule_keys=["m0"],
        molecule_ids=["m0#0"],
        input_hashes=["h0"],
        intensity_quantity="corrected",
        selected_by="fixed",
        elbo_by_nstates=None,
        app_version="test",
        created_utc="2026-01-01T00:00:00Z",
        overwrite=True,
        frac=None if frac is None else np.asarray(frac, dtype="float64"),
    )


def test_population_overlay_reads_persisted_model(tmp_path) -> None:
    path = tmp_path / "pop.tether"
    means = [0.3, 0.7]
    variances = [0.01, 0.02]
    frac = [0.55, 0.45]
    _write_model(path, model_type="vbconhmm", means=means, variances=variances, frac=frac)

    ov = population_model_gaussian_overlay(path, "vbconhmm")
    assert isinstance(ov, ModelGaussianOverlay)
    assert ov.model_name == "vbconhmm"
    assert ov.nstates == 2
    # Identical to calling the pure core with the model's stored members.
    ref = model_gaussian_overlay(means, variances, frac)
    np.testing.assert_allclose(ov.total, ref.total)
    np.testing.assert_array_equal(ov.means, np.asarray(means))
    np.testing.assert_array_equal(ov.variances, np.asarray(variances))
    np.testing.assert_array_equal(ov.frac, np.asarray(frac))


def test_population_overlay_accepts_a_project_object(tmp_path) -> None:
    path = tmp_path / "obj.tether"
    _write_model(
        path, model_type="ebhmm", means=[0.2, 0.8], variances=[0.01, 0.01], frac=[0.5, 0.5]
    )
    proj = Project.open(path)
    ov = population_model_gaussian_overlay(proj, "ebhmm", n_points=201)
    assert ov.n_points == 201
    assert ov.model_name == "ebhmm"


def test_population_overlay_forwards_range_and_points(tmp_path) -> None:
    path = tmp_path / "fwd.tether"
    _write_model(path, model_type="vbconhmm", means=[0.5], variances=[0.02], frac=[1.0])
    ov = population_model_gaussian_overlay(path, "vbconhmm", value_range=(0.0, 1.0), n_points=128)
    assert ov.value_range == (0.0, 1.0)
    assert ov.n_points == 128


def test_model_without_population_members_is_withheld(tmp_path) -> None:
    """A threshold/k-means model (no variances/frac) raises, never a fabricated overlay."""
    path = tmp_path / "nopop.tether"
    _write_model(path, model_type="threshold", means=[0.3, 0.7], variances=None, frac=None)
    with pytest.raises(ValueError, match="needs a population model"):
        population_model_gaussian_overlay(path, "threshold")


def test_model_missing_only_frac_is_withheld(tmp_path) -> None:
    path = tmp_path / "novar.tether"
    _write_model(path, model_type="vbconhmm", means=[0.3, 0.7], variances=[0.01, 0.02], frac=None)
    with pytest.raises(ValueError, match="frac"):
        population_model_gaussian_overlay(path, "vbconhmm")


def test_unknown_model_name_raises_keyerror(tmp_path) -> None:
    path = tmp_path / "missing.tether"
    _write_model(path, model_type="vbconhmm", means=[0.5], variances=[0.01], frac=[1.0])
    with pytest.raises(KeyError):
        population_model_gaussian_overlay(path, "does-not-exist")
