# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The smallest accepted value of each limit of the histogram, TDP and transition-probability cores.

The tests of these cores check the *rejected* side of each validation limit
(``signal_bins=0`` raises) and the usual cases, but not the smallest value that a
limit accepts, nor the degenerate inputs where a guard decides the result (a density
over a total weight of exactly 1). The audit of #695 found a surviving mutant at each
of these lines; #739 (findings ANB-F7 and ANB-F10) asks for one assertion each. The
dwell-time rows of ANB-F10 are in ``test_analysis_dwell.py``, next to their cores.

All headless (no Qt, no store) -> base CI matrix.
"""

from __future__ import annotations

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")

import numpy as np  # noqa: E402

from tether.analysis import (  # noqa: E402
    apparent_e_histogram,
    bootstrap_histogram_ci,
    transition_density,
    transition_prob_histogram,
)

# --- transition probability: final_state = 0 (ANB-F7) -------------------------


def test_transition_prob_histogram_into_state_zero() -> None:
    # final_state may be any state >= 0, so 0 is valid. P(1 -> 0) of each molecule:
    #   [1, 0]        successors of 1: 1->0           -> 1/1
    #   [0, 1, 0]     successors of 1: 1->0           -> 1/1
    #   [1, 1, 0]     1->1, 1->0                      -> 1/2
    #   [1, 1, 1, 0]  1->1, 1->1, 1->0                -> 1/3
    #   [1, 1, 1, 1]  1->1 three times                -> 0/3
    #   [0, 0, 0]     never in state 1                -> dropped
    # Bins of (0, 1) in four: [0, .25) [.25, .5) [.5, .75) [.75, 1].
    chunks = [
        np.array([1, 0]),
        np.array([0, 1, 0]),
        np.array([1, 1, 0]),
        np.array([1, 1, 1, 0]),
        np.array([1, 1, 1, 1]),
        np.array([0, 0, 0]),
    ]
    h = transition_prob_histogram(
        chunks,
        init_state=1,
        final_state=0,
        prob_bins=4,
        prob_range=(0.0, 1.0),
        density=False,
        kde=False,
    )
    assert h.init_state == 1
    assert h.final_state == 0
    assert h.n_molecules == 5
    np.testing.assert_allclose(np.sort(h.probabilities), [0.0, 1 / 3, 0.5, 1.0, 1.0])
    np.testing.assert_array_equal(h.counts, [1.0, 1.0, 1.0, 2.0])


# --- transition probability: smallest accepted prob_bins and kde_points ---------


def test_transition_prob_histogram_accepts_one_bin() -> None:
    # "prob_bins must be >= 1": one bin is valid and holds every in-range molecule.
    chunks = [np.array([0, 1]), np.array([0, 0, 1])]  # P(0->1) = 1 and 1/2
    h = transition_prob_histogram(
        chunks, init_state=0, final_state=1, prob_bins=1, density=False, kde=False
    )
    assert h.n_bins == 1
    np.testing.assert_array_equal(h.counts, [2.0])
    np.testing.assert_allclose(h.edges, [-0.05, 1.05])


def test_transition_prob_histogram_accepts_two_kde_points() -> None:
    # "kde_points must be >= 2": two points are valid, at both ends of [0, 1].
    chunks = [np.array([0, 1]), np.array([0, 0, 1])]  # two distinct probabilities
    h = transition_prob_histogram(chunks, init_state=0, final_state=1, kde_points=2)
    assert h.kde_x is not None and h.kde_y is not None
    np.testing.assert_array_equal(h.kde_x, [0.0, 1.0])
    assert h.kde_y.shape == (2,)
    assert np.all(np.isfinite(h.kde_y)) and np.all(h.kde_y > 0.0)


# --- TDP: smallest accepted signal_bins, density of a single transition ---------


def test_transition_density_accepts_one_signal_bin() -> None:
    # "signal_bins must be >= 1": one bin per axis is valid, a 1 x 1 TDP.
    empty = transition_density([], signal_bins=1)
    assert empty.counts.shape == (1, 1)
    assert empty.signal_bins == 1
    one = transition_density([np.array([0.2, 0.8])], nskip=1, signal_bins=1, signal_range=(0, 1))
    np.testing.assert_array_equal(one.counts, [[1.0]])
    assert one.n_transitions == 1


def test_transition_density_of_one_transition_integrates_to_one() -> None:
    # density=True with exactly one in-range transition: all the mass is in one cell,
    # and the 2-D integral is 1 (the cell area is 0.25 * 0.25).
    h = transition_density(
        [np.array([0.2, 0.8])], nskip=1, signal_bins=4, signal_range=(0.0, 1.0), density=True
    )
    assert h.n_transitions == 1
    want = np.zeros((4, 4))
    want[0, 3] = 16.0  # initial E 0.2 -> row 0, final E 0.8 -> column 3
    np.testing.assert_allclose(h.counts, want, rtol=1e-12)
    cell = np.diff(h.signal_edges)[0] ** 2
    assert float(h.counts.sum() * cell) == pytest.approx(1.0, rel=1e-12)


# --- 1-D histogram: density on a total weight of exactly 1 ----------------------


def test_density_of_one_frame_integrates_to_one() -> None:
    # One frame is a total weight of 1. The density must still be normalized: the
    # one occupied bin of width 0.1 has height 10.
    h = apparent_e_histogram(np.array([0.55]), bins=10, value_range=(0.0, 1.0), density=True)
    want = np.zeros(10)
    want[5] = 10.0
    np.testing.assert_allclose(h.counts, want, rtol=1e-12)
    assert float(np.sum(h.counts * np.diff(h.bin_edges))) == pytest.approx(1.0, rel=1e-12)


def test_density_of_one_equal_weight_molecule_integrates_to_one() -> None:
    # The per-molecule equal-weight path gives one molecule a total weight of 1
    # (here four frames of weight 1/4); density=True still normalizes it.
    values = np.array([0.15, 0.15, 0.55, 0.85])
    h = apparent_e_histogram(
        values, bins=10, value_range=(0.0, 1.0), density=True, weights=np.full(4, 0.25)
    )
    want = np.zeros(10)
    want[1], want[5], want[8] = 5.0, 2.5, 2.5
    np.testing.assert_allclose(h.counts, want, rtol=1e-12)
    assert float(np.sum(h.counts * np.diff(h.bin_edges))) == pytest.approx(1.0, rel=1e-12)


def test_bootstrap_density_of_one_frame_is_normalized() -> None:
    # The same total weight of 1 in each bootstrap replicate: one molecule of one
    # frame. Every replicate is that molecule, so the band is the normalized point
    # estimate, not the raw count.
    ci = bootstrap_histogram_ci(
        [np.array([0.55])], bins=10, value_range=(0.0, 1.0), density=True, n_resamples=20
    )
    want = np.zeros(10)
    want[5] = 10.0
    np.testing.assert_allclose(ci.counts, want, rtol=1e-12)
    np.testing.assert_allclose(ci.ci_low, want, rtol=1e-12)
    np.testing.assert_allclose(ci.ci_high, want, rtol=1e-12)
    # identical replicates: the std is zero up to rounding (measured 1.8e-15)
    np.testing.assert_allclose(ci.std, np.zeros(10), rtol=0.0, atol=1e-12)


# --- bootstrap: two resamples give a standard deviation ---------------------------


def test_bootstrap_two_resamples_give_a_spread() -> None:
    # n_resamples = 2 is the smallest count with a sample standard deviation (ddof=1).
    # With two replicates a <= b in a bin, the linear percentiles give
    # ci_high - ci_low = (1 - 2 alpha) (b - a) and the std is (b - a) / sqrt(2), so
    # std = (ci_high - ci_low) / ((1 - 2 alpha) sqrt(2)) whatever the draws are.
    mols = [np.full(5, 0.3), np.full(5, 0.7)]
    ci = bootstrap_histogram_ci(mols, bins=10, value_range=(0.0, 1.0), density=False, n_resamples=2)
    assert ci.n_resamples == 2
    width = ci.ci_high - ci.ci_low
    assert np.any(width > 0.0)  # the two replicates differ for this seed
    np.testing.assert_allclose(ci.std, width / (0.95 * np.sqrt(2.0)), rtol=1e-12, atol=0.0)
    assert np.any(ci.std > 0.0)
