# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for native Bayesian single-step photobleach detection (PRD §7.2, §11.2).

Two independent kinds of check:

* **Reference parity** — the vectorized :mod:`tether.fret.photobleach` output
  must match a direct, line-for-line transcription of tMAVEN's reference
  formulas (``normal_ln_evidence`` / ``normal_mu_ln_evidence`` / ``ln_likelihood``
  / ``get_point_pbtime`` / ``pb_ensemble`` in
  ``tmaven/controllers/photobleaching/photobleaching.py``). This proves the O(T)
  prefix-sum evaluation is faithful to the O(T^2) reference it replaces.
* **Synthetic ground truth** — traces with an injected ``N(mu, sigma) -> N(0,
  sigma)`` step at a *known* frame; the detected first-bleach must land within
  the §11.2 ±2-frame tolerance across a range of step positions and SNRs. This
  is the real ground-truth check for the ±2 acceptance (the committed
  Deep-LASI ``.mat`` ``pacc``/``pdon`` are a constant acquisition marker, not a
  per-molecule bleach oracle — see ADR-0026 — so genuine ground truth comes from
  synthesis, not from that field).
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy.special import gammaln

from tether.fret.photobleach import (
    PB_PRIOR_A,
    PB_PRIOR_B,
    PB_PRIOR_BETA,
    PB_PRIOR_MU,
    PhotobleachResult,
    active_mask,
    detect_photobleach,
    ensemble_pbtime,
    point_pbtime,
)
from tether.fret.photobleach import _ln_likelihood as _fast_ln_likelihood

_LN_2PI = float(np.log(2.0 * np.pi))


# --- Direct transcription of tMAVEN's reference formulas (the oracle) ---------
def _ref_normal_ln_evidence(x: np.ndarray, a0: float, b0: float, k0: float, m0: float) -> float:
    """N(unknown mu, unknown sigma^2) evidence — tMAVEN ``normal_ln_evidence``."""
    n = x.size
    xbar = float(np.mean(x))
    s2 = float(np.sum((x - xbar) ** 2))
    an = a0 + n / 2.0
    kn = k0 + n
    bn = b0 + 0.5 * s2 + k0 * n * (xbar - m0) ** 2 / (2.0 * (k0 + n))
    return (
        gammaln(an)
        - gammaln(a0)
        + a0 * np.log(b0)
        - an * np.log(bn)
        + 0.5 * np.log(k0)
        - 0.5 * np.log(kn)
        - n / 2.0 * _LN_2PI
    )


def _ref_normal_mu_ln_evidence(x: np.ndarray, mu: float, a0: float, b0: float) -> float:
    """N(known mu, unknown sigma^2) evidence — tMAVEN ``normal_mu_ln_evidence``."""
    n = x.size
    s2 = float(np.sum((x - mu) ** 2))
    an = a0 + n / 2.0
    bn = b0 + 0.5 * s2
    return gammaln(an) - gammaln(a0) + a0 * np.log(b0) - an * np.log(bn) - n / 2.0 * _LN_2PI


def _ref_ln_likelihood(d: np.ndarray, a0: float, b0: float, k0: float, m0: float) -> np.ndarray:
    """Per-change-point log likelihood — tMAVEN ``ln_likelihood``."""
    d = np.asarray(d, dtype=np.float64)
    t = d.shape[0]
    lnl = np.zeros(t)
    lnl[0] = _ref_normal_mu_ln_evidence(d, 0.0, a0, b0)
    for i in range(1, t - 1):
        lnl[i] = _ref_normal_ln_evidence(d[:i], a0, b0, k0, m0) + _ref_normal_mu_ln_evidence(
            d[i:], 0.0, a0, b0
        )
    lnl[-1] = _ref_normal_ln_evidence(d, a0, b0, k0, m0)
    return lnl


def _ref_get_point_pbtime(d: np.ndarray, a0: float, b0: float, k0: float, m0: float) -> int:
    lnl = _ref_ln_likelihood(d, a0, b0, k0, m0)
    lnl = np.where(np.isnan(lnl), -np.inf, lnl)
    pbt = int(np.argmax(lnl))
    if pbt == d.shape[0] - 1:
        pbt = d.shape[0]
    return pbt


def _ref_expectation_pbtime(d: np.ndarray, a0: float, b0: float, k0: float, m0: float) -> float:
    lnl = _ref_ln_likelihood(d, a0, b0, k0, m0)
    t = np.arange(lnl.size, dtype=np.float64)
    p = np.exp(lnl - np.max(lnl))
    return float(np.sum(p * t) / np.sum(p))


def _ref_pb_ensemble(d2: np.ndarray, a0: float, b0: float, k0: float, m0: float):
    n, t = d2.shape
    pbt = np.zeros(n, dtype=np.int64)
    for i in range(n):
        pbt[i] = _ref_expectation_pbtime(d2[i], a0, b0, k0, m0)  # int cast (truncates)
    e_k = (1.0 + pbt.size) / (1.0 + np.sum(pbt))
    out = np.zeros(n, dtype=np.int64)
    for i in range(n):
        lnp = _ref_ln_likelihood(d2[i], a0, b0, k0, m0) + np.log(e_k) - e_k * np.arange(t)
        p = int(np.argmax(lnp))
        out[i] = t if p == t - 1 else p
    return e_k, out


# --- Synthetic step generator (deterministic) --------------------------------
def _step_trace(rng: np.random.Generator, n: int, k: int, level: float, noise: float) -> np.ndarray:
    """N(level, noise) for frames [0, k), N(0, noise) for [k, n) — bleach at k."""
    d = rng.normal(0.0, noise, size=n)
    d[:k] += level
    return d


_PRIORS = (PB_PRIOR_A, PB_PRIOR_B, PB_PRIOR_BETA, PB_PRIOR_MU)


def test_priors_match_registered_defaults() -> None:
    # Guards against silent drift from the frozen PRD §11.2 bleach-detection row
    # (a = b = beta = 1, mu = 1000).
    assert (PB_PRIOR_A, PB_PRIOR_B, PB_PRIOR_BETA, PB_PRIOR_MU) == (1.0, 1.0, 1.0, 1000.0)


def test_ln_likelihood_matches_reference_formulas() -> None:
    rng = np.random.default_rng(0)
    for _ in range(8):
        n = int(rng.integers(6, 130))
        k = int(rng.integers(2, n - 1))
        d = _step_trace(rng, n, k, level=800.0, noise=60.0)
        fast = _fast_ln_likelihood(d, *_PRIORS)
        ref = _ref_ln_likelihood(d, *_PRIORS)
        np.testing.assert_allclose(fast, ref, rtol=1e-6, atol=1e-6)


def test_point_pbtime_matches_reference() -> None:
    rng = np.random.default_rng(1)
    for _ in range(12):
        n = int(rng.integers(6, 130))
        k = int(rng.integers(2, n - 1))
        d = _step_trace(rng, n, k, level=700.0, noise=50.0)
        assert point_pbtime(d) == _ref_get_point_pbtime(d, *_PRIORS)


def test_point_pbtime_recovers_known_step_within_two_frames() -> None:
    # The §11.2 ±2-frame acceptance, against genuine injected ground truth,
    # across step positions and a range of signal-to-noise ratios.
    rng = np.random.default_rng(2)
    n = 200
    for k in (30, 60, 90, 120, 160):
        for level, noise in ((1000.0, 40.0), (600.0, 80.0), (1500.0, 120.0)):
            d = _step_trace(rng, n, k, level=level, noise=noise)
            pbt = point_pbtime(d)
            assert abs(pbt - k) <= 2, f"k={k} level={level} noise={noise} -> pbt={pbt}"


def test_never_bleaches_returns_length_and_all_zero_returns_zero() -> None:
    rng = np.random.default_rng(3)
    n = 100
    signal = rng.normal(1000.0, 40.0, size=n)  # no step at all
    assert point_pbtime(signal) == n  # does not bleach within the trace
    zeros = rng.normal(0.0, 30.0, size=n)  # bleached from the first frame
    assert point_pbtime(zeros) == 0


def test_short_traces_do_not_raise() -> None:
    assert point_pbtime(np.array([])) == 0
    assert point_pbtime(np.array([5.0])) == 1
    # Two frames: either bleached-from-start or never-bleaches, never an error.
    assert point_pbtime(np.array([1000.0, 0.0])) in (0, 1, 2)


def test_ensemble_matches_reference() -> None:
    rng = np.random.default_rng(4)
    n_frames = 120
    traces = np.stack(
        [_step_trace(rng, n_frames, int(rng.integers(10, 110)), 800.0, 60.0) for _ in range(9)]
    )
    e_k, pbt = ensemble_pbtime(traces)
    ref_ek, ref_pbt = _ref_pb_ensemble(traces, *_PRIORS)
    assert e_k == pytest.approx(ref_ek, rel=1e-9)
    np.testing.assert_array_equal(pbt, ref_pbt)


def test_ensemble_requires_2d() -> None:
    with pytest.raises(ValueError):
        ensemble_pbtime(np.zeros(10))


def test_active_mask() -> None:
    np.testing.assert_array_equal(active_mask(5, 3), [True, True, True, False, False])
    np.testing.assert_array_equal(active_mask(3, 0), [False, False, False])
    np.testing.assert_array_equal(active_mask(3, 3), [True, True, True])


def test_detect_photobleach_window_and_masks() -> None:
    rng = np.random.default_rng(5)
    n = 200
    ka, kd = 70, 130  # acceptor bleaches first, donor later
    donor = _step_trace(rng, n, kd, level=1200.0, noise=60.0)
    acceptor = _step_trace(rng, n, ka, level=900.0, noise=60.0)
    res = detect_photobleach(donor, acceptor)
    assert isinstance(res, PhotobleachResult)
    assert res.n_frames == n
    assert abs(res.donor_pb - kd) <= 2
    assert abs(res.acceptor_pb - ka) <= 2
    # The window ends at the first bleach of the *summed* intensity (PRD §11.2):
    # the sum stays above background until the last-surviving dye (donor) goes.
    assert res.window == (0, res.sum_pb)
    assert abs(res.sum_pb - kd) <= 3
    # Active masks reflect the per-channel bleach frames.
    assert int(res.donor_active().sum()) == res.donor_pb
    assert int(res.acceptor_active().sum()) == res.acceptor_pb


def test_detect_photobleach_shape_guards() -> None:
    with pytest.raises(ValueError):
        detect_photobleach(np.zeros(10), np.zeros(9))
    with pytest.raises(ValueError):
        detect_photobleach(np.zeros((2, 5)), np.zeros((2, 5)))


def test_summed_window_spans_whole_trace_when_no_bleach() -> None:
    rng = np.random.default_rng(6)
    n = 80
    donor = rng.normal(1000.0, 40.0, size=n)  # neither channel bleaches
    acceptor = rng.normal(800.0, 40.0, size=n)
    res = detect_photobleach(donor, acceptor)
    assert res.window == (0, n)
    assert res.sum_pb == n
