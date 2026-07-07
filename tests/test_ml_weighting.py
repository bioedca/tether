# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Cold-start label-weight decay law (M5, FR-ML; PRD §7.5, §11.2).

Locks :mod:`tether.ml.weighting`: the pure decay ``w = w₀/(1+n_human)`` — full seed weight at the
cold start, a hyperbolic decay toward zero as human labels accrue, the §9 M5 weight-decay boundary
(a provisional label's effective weight drops below ``0.2·w₀`` once a handful of human labels
exist), and the vectorized recompute + its loud validation. Pure NumPy — no store, no sklearn.
"""

from __future__ import annotations

import math

import pytest

pytest.importorskip("numpy")

import numpy as np  # noqa: E402

from tether.ml.weighting import (  # noqa: E402
    DEFAULT_SEED_WEIGHT,
    HUMAN_WEIGHT,
    effective_weights,
    seed_weight,
)


def test_default_seed_weight_is_the_prd_tunable() -> None:
    # PRD §11.2 "Cold-start seed weight w₀": w₀ ≈ 0.3, human = 1.0.
    assert pytest.approx(0.3) == DEFAULT_SEED_WEIGHT
    assert HUMAN_WEIGHT == 1.0


def test_seed_weight_is_full_at_the_cold_start() -> None:
    # n_human == 0: a lone seed bootstrapping an empty condition carries the full seed weight w₀.
    assert seed_weight(0) == pytest.approx(DEFAULT_SEED_WEIGHT)


def test_seed_weight_decays_monotonically_toward_zero() -> None:
    weights = [seed_weight(n) for n in range(0, 50)]
    pairs = list(zip(weights, weights[1:], strict=False))
    assert all(b < a for a, b in pairs)  # strictly decreasing
    assert weights[-1] < weights[0]
    assert seed_weight(10_000) < 1e-3  # ->0 as human labels dominate


def test_weight_decay_boundary_below_one_fifth_after_five_human_labels() -> None:
    # PLAN §9 M5 weight-decay acceptance: a provisional label's effective weight < 0.2·w₀ once a
    # handful of human labels exist. w = w₀/(1+n) < 0.2·w₀  <=>  n >= 5.
    w0 = DEFAULT_SEED_WEIGHT
    assert seed_weight(4, w0=w0) == pytest.approx(0.2 * w0)  # exactly the threshold at n=4
    assert seed_weight(5, w0=w0) < 0.2 * w0  # crosses below it at n=5
    assert seed_weight(5, w0=w0) == pytest.approx(w0 / 6.0)


def test_seed_weight_honours_a_custom_w0() -> None:
    assert seed_weight(3, w0=0.8) == pytest.approx(0.8 / 4.0)


@pytest.mark.parametrize("bad_n", [-1, -5])
def test_seed_weight_rejects_negative_n_human(bad_n: int) -> None:
    with pytest.raises(ValueError, match="n_human"):
        seed_weight(bad_n)


@pytest.mark.parametrize("bad_n", [True, 1.5, "3", None])
def test_seed_weight_rejects_non_integer_n_human(bad_n: object) -> None:
    with pytest.raises(ValueError, match="n_human"):
        seed_weight(bad_n)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad_w0", [0.0, -0.1, math.inf, math.nan])
def test_seed_weight_rejects_non_positive_or_non_finite_w0(bad_w0: float) -> None:
    with pytest.raises(ValueError, match="w0"):
        seed_weight(2, w0=bad_w0)


def test_effective_weights_splits_human_from_seed() -> None:
    is_human = np.array([True, False, True, False])
    n_human = np.array([7, 7, 7, 7])
    w = effective_weights(is_human, n_human, w0=0.3)
    assert w.dtype == np.float64
    expected_seed = 0.3 / (1.0 + 7)
    np.testing.assert_allclose(w, [1.0, expected_seed, 1.0, expected_seed])


def test_effective_weights_human_weight_ignores_n_human() -> None:
    # A human row is full weight regardless of how large its condition's n_human is.
    w = effective_weights(np.array([True]), np.array([10_000]))
    assert w[0] == 1.0


def test_effective_weights_broadcasts_a_scalar_n_human() -> None:
    is_human = np.array([False, False, False])
    w = effective_weights(is_human, 2, w0=0.3)  # scalar count applies to every row
    np.testing.assert_allclose(w, np.full(3, 0.3 / 3.0))


def test_effective_weights_per_row_counts() -> None:
    # Different conditions -> different n_human per row.
    is_human = np.array([False, False])
    w = effective_weights(is_human, np.array([0, 9]), w0=0.3)
    np.testing.assert_allclose(w, [0.3, 0.3 / 10.0])


def test_effective_weights_honours_custom_human_weight() -> None:
    w = effective_weights(np.array([True, False]), np.array([1, 1]), w0=0.4, human_weight=2.0)
    np.testing.assert_allclose(w, [2.0, 0.4 / 2.0])


def test_effective_weights_rejects_negative_counts() -> None:
    with pytest.raises(ValueError, match="n_human"):
        effective_weights(np.array([False, False]), np.array([1, -1]))


def test_effective_weights_rejects_float_counts() -> None:
    with pytest.raises(ValueError, match="integer"):
        effective_weights(np.array([False]), np.array([1.5]))


def test_effective_weights_rejects_non_broadcastable_shapes() -> None:
    with pytest.raises(ValueError, match="broadcast"):
        effective_weights(np.array([True, False, True]), np.array([1, 2]))


@pytest.mark.parametrize("bad_w0", [0.0, -1.0, math.inf, math.nan])
def test_effective_weights_rejects_bad_w0(bad_w0: float) -> None:
    with pytest.raises(ValueError, match="w0"):
        effective_weights(np.array([False]), np.array([1]), w0=bad_w0)


@pytest.mark.parametrize("bad_hw", [-0.1, math.inf, math.nan])
def test_effective_weights_rejects_bad_human_weight(bad_hw: float) -> None:
    with pytest.raises(ValueError, match="human_weight"):
        effective_weights(np.array([True]), np.array([1]), human_weight=bad_hw)
