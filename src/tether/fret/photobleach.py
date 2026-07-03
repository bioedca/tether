# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Native Bayesian single-step photobleaching detection (PRD Appendix E Stage 16).

A headless, Qt-free reimplementation of tMAVEN's single-step photobleaching
model (``tmaven/controllers/photobleaching/photobleaching.py``), used to place
each trace's *analysis window* at trace-start → first photobleach.

Model
-----
A fluorophore trace is modelled as a single change point ``t``: for frames
``[0, t)`` the signal is Normal with an unknown mean and variance,
``N(mu, sigma^2)``; from frame ``t`` onward the fluorophore has bleached and the
signal is Normal about **zero**, ``N(0, sigma^2)``. With conjugate
Normal-inverse-Gamma priors (``a = b = beta = 1``, ``mu = 1000`` — the tMAVEN
defaults, registered in PRD §11.2), the marginal likelihood of each candidate
change point has a closed form, and the maximum-a-posteriori (MAP) change point
is the detected first-bleach frame. This Bayesian MAP change-point approach to
photobleaching-step detection is a validated, kinetic-model-independent method
that recovers ground-truth steps across a wide signal-to-noise range
[Tsekouras2016; Garry2020; Mattamira2025].

The implementation is a closed-form **vectorized** evaluation of the same
per-change-point evidence tMAVEN computes trace-by-trace: prefix sums make each
candidate's segment evidence O(1), so the whole likelihood curve is O(T) rather
than O(T^2). It matches tMAVEN's ``get_point_pbtime`` / ``pb_ensemble`` outputs
frame-for-frame (see ``tests/test_fret_photobleach.py``, which cross-checks
against a direct transcription of the reference formulas).

Per-channel vs summed
---------------------
tMAVEN's default runs the detector on the summed colors (``photobleach.sum``).
Tether additionally runs it **independently per channel** (donor, acceptor) to
recover the per-molecule donor/acceptor first-bleach frames used by the leakage
and gamma corrections (M3), while the **analysis-window default** is derived
from the first bleach of the *summed* donor+acceptor intensity (PRD §7.2 /
Appendix B step 6).

References
----------
[Tsekouras2016] Tsekouras, Custer, Jashnsaz, Baker & Presse. "A novel method to
    accurately locate and count large numbers of steps by photobleaching."
    Molecular Biology of the Cell (2016).
[Garry2020] Garry, Li, Shatoff, Zhang, Comfort & Rassolov. "Bayesian counting of
    photobleaching steps with physical priors." J. Chem. Phys. (2020).
[Mattamira2025] Mattamira et al. "Bayesian analysis and efficient algorithms for
    single-molecule fluorescence data and step counting." Biophysical Journal
    (2025).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.special import gammaln

__all__ = [
    "PB_PRIOR_A",
    "PB_PRIOR_B",
    "PB_PRIOR_BETA",
    "PB_PRIOR_MU",
    "PhotobleachResult",
    "active_mask",
    "detect_photobleach",
    "ensemble_pbtime",
    "point_pbtime",
]

# --- Default Normal-inverse-Gamma priors (tMAVEN defaults; PRD §11.2) ---------
# Named, not magic: the corrections pipeline and any UI override read these.
PB_PRIOR_A: float = 1.0  # alpha of the inverse-Gamma over the noise variance
PB_PRIOR_B: float = 1.0  # beta of the inverse-Gamma over the noise variance
PB_PRIOR_BETA: float = 1.0  # precision (kappa) of the Normal over the signal mean
PB_PRIOR_MU: float = 1000.0  # prior mean of the pre-bleach signal level

_LN_2PI = float(np.log(2.0 * np.pi))


def _ln_likelihood(d: np.ndarray, a: float, b: float, beta: float, mu: float) -> np.ndarray:
    """Log marginal likelihood of each single-step change point ``t`` in ``d``.

    Element ``t`` is the log evidence for "frames ``[0, t)`` are signal
    ``N(mu, sigma^2)``, frames ``[t, T)`` are bleached ``N(0, sigma^2)``".
    Boundary conventions match tMAVEN exactly: index ``0`` is the fully-bleached
    (all-zero) hypothesis and index ``T-1`` is the never-bleaches (all-signal)
    hypothesis. Returned array has length ``T = d.size``.
    """
    d = np.asarray(d, dtype=np.float64)
    t = d.size
    if t == 0:
        return np.zeros(0, dtype=np.float64)

    # Prefix sums: cs[k] = sum(d[:k]), cs2[k] = sum(d[:k]**2), for k in 0..T.
    cs = np.concatenate(([0.0], np.cumsum(d)))
    cs2 = np.concatenate(([0.0], np.cumsum(d * d)))
    total_sq = cs2[t]

    # --- Left (signal) segment evidence for left size n_L = i, i = 1..T --------
    # normal_ln_evidence(d[:i]) with unknown mean and variance (NIG prior).
    i = np.arange(1, t + 1, dtype=np.float64)
    sum_l = cs[1:]  # sum d[:i]
    sq_l = cs2[1:]  # sum d[:i]**2
    xbar = sum_l / i
    s2 = np.maximum(sq_l - sum_l * sum_l / i, 0.0)  # sum (d[:i]-xbar)^2, clamped
    an_l = a + i / 2.0
    kn_l = beta + i
    bn_l = b + 0.5 * s2 + beta * i * (xbar - mu) ** 2 / (2.0 * (beta + i))
    ev_l = (
        gammaln(an_l)
        - gammaln(a)
        + a * np.log(b)
        - an_l * np.log(bn_l)
        + 0.5 * np.log(beta)
        - 0.5 * np.log(kn_l)
        - i / 2.0 * _LN_2PI
    )

    # --- Right (bleached, mean-zero) segment evidence for d[i:], i = 0..T-1 -----
    # normal_mu_ln_evidence(d[i:], mu=0) with known mean 0, unknown variance.
    j = np.arange(0, t, dtype=np.float64)
    n_r = t - j
    sq_r = np.maximum(total_sq - cs2[:t], 0.0)  # sum d[i:]**2
    an_r = a + n_r / 2.0
    bn_r = b + 0.5 * sq_r
    ev_r = gammaln(an_r) - gammaln(a) + a * np.log(b) - an_r * np.log(bn_r) - n_r / 2.0 * _LN_2PI

    lnl = np.empty(t, dtype=np.float64)
    lnl[0] = ev_r[0]  # all frames bleached (index 0)
    if t >= 3:
        mid = np.arange(1, t - 1)
        lnl[mid] = ev_l[mid - 1] + ev_r[mid]  # split: signal[:i] + bleached[i:]
    lnl[t - 1] = ev_l[t - 1]  # all frames signal (never bleaches)
    return lnl


def point_pbtime(
    trace: np.ndarray,
    *,
    a: float = PB_PRIOR_A,
    b: float = PB_PRIOR_B,
    beta: float = PB_PRIOR_BETA,
    mu: float = PB_PRIOR_MU,
) -> int:
    """First-bleach frame of a single trace (MAP single-step change point).

    Faithful to tMAVEN's ``get_point_pbtime``: returns the MAP change point in
    ``0..T``. A return of ``t`` frames means frames ``[0, t)`` are pre-bleach
    signal and ``[t, T)`` are bleached; ``t == T`` (``= trace.size``) means the
    fluorophore does **not** bleach within the trace. Traces shorter than two
    frames cannot carry a step and return ``trace.size``.
    """
    trace = np.asarray(trace, dtype=np.float64)
    t = trace.size
    if t < 2:
        return int(t)
    lnl = _ln_likelihood(trace, a, b, beta, mu)
    lnl = np.where(np.isnan(lnl), -np.inf, lnl)
    pbt = int(np.argmax(lnl))
    if pbt == t - 1:  # all-signal hypothesis → does not bleach within the trace
        pbt = t
    return pbt


def _posterior(
    d: np.ndarray, rate: float, a: float, b: float, beta: float, mu: float
) -> np.ndarray:
    """Log posterior over the change point with an exponential lifetime prior."""
    lnl = _ln_likelihood(d, a, b, beta, mu)
    t = np.arange(d.size, dtype=np.float64)
    return lnl + np.log(rate) - rate * t


def _expectation_pbtime(d: np.ndarray, a: float, b: float, beta: float, mu: float) -> float:
    """Posterior-mean change point (first pass of the ensemble estimate)."""
    lnl = _ln_likelihood(d, a, b, beta, mu)
    t = np.arange(lnl.size, dtype=np.float64)
    p = np.exp(lnl - np.max(lnl))
    return float(np.sum(p * t) / np.sum(p))


def ensemble_pbtime(
    traces: np.ndarray,
    *,
    a: float = PB_PRIOR_A,
    b: float = PB_PRIOR_B,
    beta: float = PB_PRIOR_BETA,
    mu: float = PB_PRIOR_MU,
) -> tuple[float, np.ndarray]:
    """Ensemble first-bleach frames for a cohort of equal-length traces.

    Faithful to tMAVEN's ``pb_ensemble``: a two-pass estimate that first takes
    each trace's posterior-mean change point, derives a shared exponential
    lifetime rate ``e_k = (1 + N) / (1 + sum(pbt))``, then re-estimates each
    trace's MAP change point under that lifetime prior. Returns ``(e_k, pbt)``
    with ``pbt`` an integer array of shape ``(N,)``; ``pbt[i] == T`` means trace
    ``i`` does not bleach within the trace.
    """
    traces = np.asarray(traces, dtype=np.float64)
    if traces.ndim != 2:
        raise ValueError("ensemble_pbtime expects a 2-D (n_traces, n_frames) array")
    n, t = traces.shape
    if t < 2:
        return 1.0, np.full(n, t, dtype=np.int64)

    # tMAVEN stores the first-pass posterior-mean change points into an int64
    # array (truncating toward zero) before deriving the shared rate, so match
    # that exactly rather than summing the float expectations.
    first = np.array(
        [int(_expectation_pbtime(traces[i], a, b, beta, mu)) for i in range(n)],
        dtype=np.int64,
    )
    e_k = (1.0 + first.size) / (1.0 + float(np.sum(first)))

    pbt = np.empty(n, dtype=np.int64)
    for i in range(n):
        lnp = _posterior(traces[i], e_k, a, b, beta, mu)
        p = int(np.argmax(lnp))
        pbt[i] = t if p == t - 1 else p
    return e_k, pbt


def active_mask(n_frames: int, pb_frame: int) -> np.ndarray:
    """Per-frame active (pre-bleach) mask: ``True`` for frames ``[0, pb_frame)``."""
    idx = np.arange(int(n_frames))
    return idx < int(pb_frame)


@dataclass(frozen=True)
class PhotobleachResult:
    """Per-molecule photobleaching detection over donor/acceptor + their sum.

    Attributes
    ----------
    n_frames
        Trace length in frames.
    donor_pb, acceptor_pb
        Per-channel first-bleach frames (``0..n_frames``; ``== n_frames`` when a
        channel does not bleach within the trace).
    sum_pb
        First-bleach frame of the summed donor+acceptor intensity — the end of
        the auto analysis window.
    window
        Auto analysis-window default ``(start, stop)`` = ``(0, sum_pb)``, a
        half-open frame range (PRD §7.2 / Appendix B step 6). Manual ``-``/``=``/
        ``[``/``]`` bounds, when set, override this default.
    """

    n_frames: int
    donor_pb: int
    acceptor_pb: int
    sum_pb: int
    window: tuple[int, int]

    def donor_active(self) -> np.ndarray:
        """Per-frame donor active mask."""
        return active_mask(self.n_frames, self.donor_pb)

    def acceptor_active(self) -> np.ndarray:
        """Per-frame acceptor active mask."""
        return active_mask(self.n_frames, self.acceptor_pb)


def detect_photobleach(
    donor: np.ndarray,
    acceptor: np.ndarray,
    *,
    a: float = PB_PRIOR_A,
    b: float = PB_PRIOR_B,
    beta: float = PB_PRIOR_BETA,
    mu: float = PB_PRIOR_MU,
) -> PhotobleachResult:
    """Detect per-channel first-bleach and the summed-intensity analysis window.

    Runs the single-step detector independently on the donor channel, the
    acceptor channel, and their per-frame sum. The auto analysis window is
    ``(0, sum_pb)`` where ``sum_pb`` is the first bleach of the summed intensity;
    when the summed signal does not bleach, the window spans the whole trace.
    """
    donor = np.asarray(donor, dtype=np.float64)
    acceptor = np.asarray(acceptor, dtype=np.float64)
    if donor.shape != acceptor.shape:
        raise ValueError("donor and acceptor traces must have the same shape")
    if donor.ndim != 1:
        raise ValueError("detect_photobleach expects 1-D donor/acceptor traces")

    n = int(donor.size)
    donor_pb = point_pbtime(donor, a=a, b=b, beta=beta, mu=mu)
    acceptor_pb = point_pbtime(acceptor, a=a, b=b, beta=beta, mu=mu)
    sum_pb = point_pbtime(donor + acceptor, a=a, b=b, beta=beta, mu=mu)
    return PhotobleachResult(
        n_frames=n,
        donor_pb=donor_pb,
        acceptor_pb=acceptor_pb,
        sum_pb=sum_pb,
        window=(0, sum_pb),
    )
