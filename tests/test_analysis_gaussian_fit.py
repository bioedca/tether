# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The sum-of-Gaussians fit of a 1-D histogram (#1030, ADR-0120).

The published numbers it reproduces are in ``test_campos_chavez_2024.py`` (weekly); this module
holds the method to what can be derived here: exact recovery of a noise-free model, the two area
definitions, the propagated errors against a numerical derivative and against a Monte Carlo of the
fit, the order of the Gaussians, and the failures.
"""

from __future__ import annotations

import numpy as np
import pytest

from tether.analysis.gaussian_fit import (
    AREA_METHODS,
    DEFAULT_INITIAL_WIDTH,
    fit_gaussians,
    gaussian_sum,
)
from tether.analysis.histogram import Histogram1D, apparent_e_histogram

#: Two states, about the shape of Campos-Chavez et al. 2024 Fig. 5b: (A, mu, sigma) each.
TRUTH = np.array([3.9, 0.046, 0.065, 1.6, 0.324, 0.070])
EDGES = np.linspace(-0.2, 1.2, 200)


def _histogram(counts: np.ndarray, edges: np.ndarray = EDGES) -> Histogram1D:
    lo, hi = float(edges[0]), float(edges[-1])
    return Histogram1D(
        counts=np.asarray(counts, dtype=np.float64),
        bin_edges=edges,
        density=True,
        value_range=(lo, hi),
        n_samples=0,
        n_molecules=None,
        per_molecule_equal_weight=False,
    )


def _centers(edges: np.ndarray = EDGES) -> np.ndarray:
    return 0.5 * (edges[:-1] + edges[1:])


def _noisy(seed: int, scale: float = 0.05) -> Histogram1D:
    rng = np.random.default_rng(seed)
    x = _centers()
    return _histogram(gaussian_sum(x, *TRUTH) + rng.normal(0.0, scale, x.size))


def test_a_noise_free_sum_of_gaussians_is_recovered() -> None:
    fit = fit_gaussians(_histogram(gaussian_sum(_centers(), *TRUTH)), (0.0, 0.33))
    assert fit.success
    np.testing.assert_allclose(fit.params, TRUTH, rtol=1e-7)
    np.testing.assert_allclose(fit.model, fit.observed, atol=1e-10)
    assert fit.r_squared == pytest.approx(1.0)
    area = TRUTH[0::3] * np.sqrt(2 * np.pi) * TRUTH[2::3]
    np.testing.assert_allclose(fit.areas, area, rtol=1e-7)
    np.testing.assert_allclose(fit.fractions, area / area.sum(), rtol=1e-7)


def test_the_two_area_definitions() -> None:
    hist = _noisy(1)
    integral = fit_gaussians(hist, (0.0, 0.33), area="integral")
    bin_sum = fit_gaussians(hist, (0.0, 0.33), area="bin_sum")
    np.testing.assert_array_equal(integral.params, bin_sum.params)  # one fit, two areas
    a, s = integral.amplitudes, integral.widths
    np.testing.assert_allclose(integral.areas, a * np.sqrt(2 * np.pi) * s, rtol=1e-14)
    dx = np.diff(EDGES)
    for i in range(2):
        one = gaussian_sum(_centers(), *bin_sum.params[3 * i : 3 * i + 3])
        assert bin_sum.areas[i] == pytest.approx(float(np.sum(one * dx)), rel=1e-12)
    # The low Gaussian reaches past -0.2, so its area inside the range is smaller.
    assert bin_sum.areas[0] < integral.areas[0]
    np.testing.assert_allclose(bin_sum.fractions.sum(), 1.0)


@pytest.mark.parametrize("area", AREA_METHODS)
def test_propagated_errors_equal_a_numerical_derivative_with_the_covariance(area: str) -> None:
    fit = fit_gaussians(_noisy(2), (0.0, 0.33), area=area)
    p = fit.params

    def derived(q: np.ndarray) -> np.ndarray:
        if area == "integral":
            areas = q[0::3] * np.sqrt(2 * np.pi) * q[2::3]
        else:
            dx = np.diff(EDGES)
            areas = np.array(
                [np.sum(gaussian_sum(_centers(), *q[3 * i : 3 * i + 3]) * dx) for i in range(2)]
            )
        return np.r_[areas, areas / areas.sum()]

    jac = np.empty((4, p.size))
    for j in range(p.size):
        h = 1e-6 * max(abs(p[j]), 1e-3)
        up, down = p.copy(), p.copy()
        up[j] += h
        down[j] -= h
        jac[:, j] = (derived(up) - derived(down)) / (2 * h)
    expected = np.sqrt(np.diag(jac @ fit.covariance @ jac.T))
    np.testing.assert_allclose(np.r_[fit.area_stderr, fit.fraction_stderr], expected, rtol=1e-5)
    np.testing.assert_allclose(fit.amplitude_stderr, np.sqrt(np.diag(fit.covariance))[0::3])
    # The two fractions sum to one, so their errors are equal.
    assert fit.fraction_stderr[0] == pytest.approx(fit.fraction_stderr[1], rel=1e-9)


def test_the_fraction_error_matches_the_spread_of_repeated_fits() -> None:
    # Tellinghuisen 2001: the propagated error predicts the spread of a non-linear LS estimate.
    fractions = []
    predicted = []
    for seed in range(300):
        fit = fit_gaussians(_noisy(1000 + seed), (0.0, 0.33))
        assert fit.success
        fractions.append(fit.fractions[1])
        predicted.append(fit.fraction_stderr[1])
    spread = float(np.std(fractions, ddof=1))
    assert float(np.median(predicted)) == pytest.approx(spread, rel=0.15)


def test_gaussians_come_back_in_order_of_mean() -> None:
    hist = _histogram(gaussian_sum(_centers(), *TRUTH))
    ordered = fit_gaussians(hist, (0.0, 0.33))
    swapped = fit_gaussians(hist, (0.33, 0.0))
    np.testing.assert_allclose(swapped.params, ordered.params, rtol=1e-7)
    np.testing.assert_allclose(swapped.covariance, ordered.covariance, rtol=1e-4, atol=1e-12)


def test_a_negative_width_from_the_optimizer_is_reported_as_its_absolute_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scipy.optimize

    hist = _histogram(gaussian_sum(_centers(), *TRUTH))
    plain = fit_gaussians(hist, (0.0, 0.33))
    real = scipy.optimize.curve_fit
    flip = np.ones(6)
    flip[2] = -1.0  # the model depends on sigma**2, so -sigma is the same optimum

    calls = []

    def negated(*args, **kwargs):
        popt, pcov = real(*args, **kwargs)
        calls.append(popt[2])
        return popt * flip, pcov * np.outer(flip, flip)

    monkeypatch.setattr(scipy.optimize, "curve_fit", negated)
    neg = fit_gaussians(hist, (0.0, 0.33))
    assert len(calls) == 1 and calls[0] > 0  # the optimizer's width was handed back negated
    assert np.all(neg.widths > 0)
    np.testing.assert_allclose(neg.params, plain.params, rtol=1e-12)
    np.testing.assert_allclose(neg.covariance, plain.covariance, rtol=1e-12)
    np.testing.assert_allclose(neg.fraction_stderr, plain.fraction_stderr, rtol=1e-12)


def test_one_and_three_gaussians() -> None:
    one = fit_gaussians(_histogram(gaussian_sum(_centers(), 2.0, 0.5, 0.1)), (0.4,))
    assert one.success and one.n_components == 1
    np.testing.assert_allclose(one.params, [2.0, 0.5, 0.1], rtol=1e-7)
    assert one.fractions[0] == pytest.approx(1.0)
    assert one.fraction_stderr[0] == pytest.approx(0.0, abs=1e-12)
    truth = np.array([1.0, 0.1, 0.05, 2.0, 0.5, 0.06, 1.5, 0.85, 0.05])
    three = fit_gaussians(_histogram(gaussian_sum(_centers(), *truth)), (0.15, 0.45, 0.8))
    assert three.success and three.n_components == 3
    np.testing.assert_allclose(three.params, truth, rtol=1e-6)


def test_a_count_histogram_gives_the_same_fractions_as_its_density() -> None:
    rng = np.random.default_rng(5)
    values = np.r_[rng.normal(0.05, 0.07, 30_000), rng.normal(0.33, 0.07, 15_000)]
    dens = fit_gaussians(apparent_e_histogram(values, bins=199, value_range=(-0.2, 1.2)), (0, 0.33))
    counts = apparent_e_histogram(values, bins=199, value_range=(-0.2, 1.2), density=False)
    raw = fit_gaussians(counts, (0, 0.33))
    np.testing.assert_allclose(raw.fractions, dens.fractions, rtol=1e-6)
    np.testing.assert_allclose(raw.fraction_stderr, dens.fraction_stderr, rtol=1e-4)
    assert dens.fractions[1] == pytest.approx(1 / 3, abs=0.01)


def test_a_fit_that_cannot_be_made_fails_with_nan() -> None:
    empty = fit_gaussians(_histogram(np.zeros(EDGES.size - 1)), (0.0, 0.33))
    assert not empty.success
    assert np.all(np.isnan(empty.fractions)) and np.all(np.isnan(empty.covariance))
    few = np.linspace(0.0, 1.0, 6)
    short = fit_gaussians(_histogram(np.ones(5), few), (0.0, 0.33))
    assert not short.success and short.n_points == 5
    bad = _histogram(np.r_[np.nan, np.ones(EDGES.size - 2)])
    assert not fit_gaussians(bad, (0.0, 0.33)).success


@pytest.mark.parametrize(
    ("means", "kwargs", "match"),
    [
        ((), {}, "at least one"),
        ((0.0, np.nan), {}, "finite"),
        ((0.0,), {"initial_width": 0.0}, "positive"),
        ((0.0,), {"initial_width": np.inf}, "positive"),
        ((0.0,), {"area": "trapezoid"}, "area must be"),
    ],
)
def test_invalid_arguments_raise(means: tuple, kwargs: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        fit_gaussians(_noisy(3), means, **kwargs)


def test_the_result_keeps_the_counts_it_was_fitted_to() -> None:
    hist = _noisy(6)
    fit = fit_gaussians(hist, (0.0, 0.33))
    before = fit.observed.copy()
    hist.counts[:] = 0.0
    np.testing.assert_array_equal(fit.observed, before)
    np.testing.assert_allclose(fit.residuals, fit.observed - fit.model)


def test_the_starting_point_is_recorded() -> None:
    fit = fit_gaussians(_noisy(4), [0.0, 0.33])
    assert fit.initial_means == (0.0, 0.33)
    assert fit.initial_width == DEFAULT_INITIAL_WIDTH
    np.testing.assert_allclose(fit.evaluate(fit.bin_centers), fit.model)
