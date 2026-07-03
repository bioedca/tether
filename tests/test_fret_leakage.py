# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Post-acceptor-bleach-tail leakage α estimator (M3, FR-CORRECT).

Locks :mod:`tether.fret.leakage` against **synthetic known-α ground truth**
(inject a leakage plateau of a known α into a donor-only tail and recover it) plus
the PRD §11.2 gates: the tail-window minimum, the acceptance ceiling, the
degenerate-donor guard, and the median aggregation with the qualifying-count
withhold. Pure numpy, no store — runs in the base CI matrix.
"""

from __future__ import annotations

import numpy as np
import pytest

from tether.fret.leakage import (
    DEFAULT_MIN_QUALIFYING_TRACES,
    DEFAULT_MIN_WINDOW_FRAMES,
    LEAKAGE_CEILING,
    apply_leakage,
    estimate_leakage_alpha,
    tail_alpha,
    tail_window,
)


def _leaky_trace(
    *,
    n: int,
    acceptor_pb: int,
    donor_pb: int,
    alpha: float,
    donor_level: float = 1000.0,
    fret_level: float = 600.0,
    noise: float = 4.0,
    seed: int = 0,
) -> tuple[np.ndarray, np.ndarray]:
    """A donor/acceptor pair with a known leakage α in its post-acceptor-bleach tail.

    donor:    ``donor_level`` until ``donor_pb``, then ~0.
    acceptor: ``fret_level`` until ``acceptor_pb`` (real FRET), then ``alpha*donor``
              (pure leakage) until ``donor_pb``, then ~0.
    So ``mean(acceptor[tail]) / mean(donor[tail]) ~= alpha`` over ``[acceptor_pb, donor_pb)``.
    """
    rng = np.random.default_rng(seed)
    donor = rng.normal(donor_level, noise, n)
    donor[donor_pb:] = rng.normal(0.0, noise, n - donor_pb)
    acceptor = np.empty(n, dtype=np.float64)
    acceptor[:acceptor_pb] = rng.normal(fret_level, noise, acceptor_pb)
    tail = slice(acceptor_pb, donor_pb)
    acceptor[tail] = alpha * donor[tail] + rng.normal(0.0, noise, donor_pb - acceptor_pb)
    acceptor[donor_pb:] = rng.normal(0.0, noise, n - donor_pb)
    return donor, acceptor


# --- apply_leakage -----------------------------------------------------------


def test_apply_leakage_subtracts_alpha_times_donor() -> None:
    donor = np.array([100.0, 200.0, 50.0])
    acceptor = np.array([30.0, 40.0, 12.0])
    out = apply_leakage(donor, acceptor, 0.1)
    assert np.allclose(out, acceptor - 0.1 * donor)
    assert out.dtype == np.float64


def test_apply_leakage_broadcasts_and_does_not_clip() -> None:
    donor = np.array([100.0, 100.0])
    acceptor = np.array([5.0, 5.0])
    out = apply_leakage(donor, acceptor, 0.1)  # 5 - 10 = -5, kept (no clip)
    assert np.allclose(out, [-5.0, -5.0])


# --- tail_window -------------------------------------------------------------


def test_tail_window_is_between_acceptor_and_donor_bleach() -> None:
    assert tail_window(30, 90, 100) == (30, 90)


def test_tail_window_clamps_donor_end_to_trace() -> None:
    # donor never bleaches (donor_pb == n) -> tail runs to the trace end.
    assert tail_window(30, 120, 100) == (30, 100)


def test_tail_window_empty_when_acceptor_not_before_donor() -> None:
    start, stop = tail_window(90, 30, 100)  # acceptor bleaches after donor
    assert stop <= start


# --- tail_alpha --------------------------------------------------------------


def test_tail_alpha_recovers_known_alpha() -> None:
    donor, acceptor = _leaky_trace(n=120, acceptor_pb=30, donor_pb=110, alpha=0.1)
    res = tail_alpha(donor, acceptor, acceptor_pb=30, donor_pb=110)
    assert res.reason == "ok"
    assert res.alpha is not None
    assert res.alpha == pytest.approx(0.1, abs=0.02)
    assert (res.start, res.stop, res.n_frames) == (30, 110, 80)


def test_tail_alpha_short_tail_rejected() -> None:
    donor, acceptor = _leaky_trace(n=60, acceptor_pb=30, donor_pb=45, alpha=0.1)
    # tail is 15 frames < the default 20 -> rejected.
    res = tail_alpha(donor, acceptor, acceptor_pb=30, donor_pb=45)
    assert res.alpha is None
    assert res.reason == "short-tail"


def test_tail_alpha_no_tail_when_acceptor_bleaches_after_donor() -> None:
    donor, acceptor = _leaky_trace(n=100, acceptor_pb=30, donor_pb=90, alpha=0.1)
    res = tail_alpha(donor, acceptor, acceptor_pb=90, donor_pb=40)
    assert res.alpha is None
    assert res.reason == "no-tail"


def test_tail_alpha_above_ceiling_rejected() -> None:
    donor, acceptor = _leaky_trace(n=120, acceptor_pb=30, donor_pb=110, alpha=0.5)
    res = tail_alpha(donor, acceptor, acceptor_pb=30, donor_pb=110)  # ~0.5 > 0.3 ceiling
    assert res.alpha is None
    assert res.reason == "out-of-range"


def test_tail_alpha_negative_rejected() -> None:
    # A tail whose acceptor channel is net-negative (over-subtracted background)
    # gives a negative ratio -> non-physical leakage, rejected.
    n = 120
    donor = np.full(n, 1000.0)
    acceptor = np.full(n, -50.0)
    res = tail_alpha(donor, acceptor, acceptor_pb=30, donor_pb=110)
    assert res.alpha is None
    assert res.reason == "out-of-range"


def test_tail_alpha_degenerate_donor_rejected() -> None:
    n = 120
    donor = np.zeros(n)  # donor not emitting in the tail
    acceptor = np.full(n, 50.0)
    res = tail_alpha(donor, acceptor, acceptor_pb=30, donor_pb=110)
    assert res.alpha is None
    assert res.reason == "degenerate-donor"


def test_tail_alpha_custom_min_window_accepts_shorter_tail() -> None:
    donor, acceptor = _leaky_trace(n=60, acceptor_pb=30, donor_pb=45, alpha=0.1)
    res = tail_alpha(donor, acceptor, acceptor_pb=30, donor_pb=45, min_window_frames=10)
    assert res.reason == "ok"
    assert res.alpha == pytest.approx(0.1, abs=0.03)


def test_tail_alpha_shape_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="same shape"):
        tail_alpha(np.zeros(10), np.zeros(9), acceptor_pb=2, donor_pb=8)


def test_tail_alpha_non_1d_raises() -> None:
    with pytest.raises(ValueError, match="1-D"):
        tail_alpha(np.zeros((2, 5)), np.zeros((2, 5)), acceptor_pb=1, donor_pb=4)


# --- estimate_leakage_alpha --------------------------------------------------


def _cohort(alphas: list[float], *, n: int = 120, acc: int = 30, don: int = 110):
    donor_traces, acceptor_traces, acc_pbs, don_pbs = [], [], [], []
    for i, a in enumerate(alphas):
        d, ac = _leaky_trace(n=n, acceptor_pb=acc, donor_pb=don, alpha=a, seed=i + 1)
        donor_traces.append(d)
        acceptor_traces.append(ac)
        acc_pbs.append(acc)
        don_pbs.append(don)
    return donor_traces, acceptor_traces, acc_pbs, don_pbs


def test_estimate_is_median_over_qualifying() -> None:
    alphas = [0.08, 0.10, 0.12, 0.09, 0.11, 0.10, 0.13, 0.07, 0.10, 0.11]  # 10 traces
    donor_t, acc_t, acc_pbs, don_pbs = _cohort(alphas)
    est = estimate_leakage_alpha(
        donor_t, acc_t, acc_pbs, don_pbs, min_qualifying_traces=DEFAULT_MIN_QUALIFYING_TRACES
    )
    assert est.n_traces == 10
    assert est.n_qualifying == 10
    assert est.alpha is not None
    assert est.alpha == pytest.approx(float(np.median(alphas)), abs=0.02)


def test_estimate_withholds_below_min_qualifying() -> None:
    # Only 3 traces qualify — fewer than the default 10 -> factor withheld.
    donor_t, acc_t, acc_pbs, don_pbs = _cohort([0.10, 0.11, 0.09])
    est = estimate_leakage_alpha(donor_t, acc_t, acc_pbs, don_pbs)
    assert est.n_qualifying == 3
    assert est.alpha is None


def test_estimate_excludes_out_of_range_from_median() -> None:
    # One trace has an implausible α (0.5, above ceiling): it must not count toward
    # either the qualifying set or the median.
    donor_t, acc_t, acc_pbs, don_pbs = _cohort([0.10, 0.10, 0.5])
    est = estimate_leakage_alpha(donor_t, acc_t, acc_pbs, don_pbs, min_qualifying_traces=2)
    assert est.n_qualifying == 2
    assert est.alpha == pytest.approx(0.10, abs=0.02)


def test_estimate_length_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="same length"):
        estimate_leakage_alpha([np.zeros(5)], [np.zeros(5)], [1, 2], [3])


def test_defaults_match_prd_11_2() -> None:
    assert LEAKAGE_CEILING == 0.3
    assert DEFAULT_MIN_WINDOW_FRAMES == 20
    assert DEFAULT_MIN_QUALIFYING_TRACES == 10
