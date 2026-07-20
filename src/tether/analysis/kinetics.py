# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Base-env kinetic-rate inference + the kinSoftChallenge within-spread oracle.

This is the M8 kinetics-validation slice (PRD §8 NFR-VALID(c), §9 M8): fit kinetic
rate constants from FRET trajectories and check them against the reported
inter-tool spread of the **kinSoftChallenge** blind benchmark [Götz2022].

Unlike the rest of Tether's idealization — which routes through the isolated
tMAVEN sidecar (:mod:`tether.idealize.driver`, ``run_vbfret``/``run_vbconhmm``) —
this module carries a **self-contained Gaussian HMM** that runs in the base
environment (numpy only). That keeps the advisory oracle in the gated large-tier
matrix without a sidecar interpreter, and matches the benchmark's own analysis
recipe: an HMM idealizes the state sequence, from which dwell-time distributions
are compiled and rate constants inferred [Götz2022, Rabiner1989, Bilmes1998].

Three pieces:

* :func:`fit_gaussian_hmm` / :func:`viterbi_paths` — a shared-parameter Gaussian
  HMM fit by scaled Baum-Welch (EM) with a deterministic quantile initialization,
  plus the Viterbi most-likely state path. No third-party HMM dependency.
* :func:`pooled_exit_rates` / :func:`two_state_rate_constants` — the per-state
  **exit rate** from the pooled dwell times, ``k = 1/⟨τ⟩`` (the maximum-likelihood
  estimator the benchmark uses [Götz2022]), reusing
  :func:`tether.analysis.dwell.state_dwells` for the finite-window (first/last)
  dwell censoring so the estimator is consistent with the M6 dwell analysis.
* :class:`KinsoftReference` / :func:`load_kinsoft_reference` /
  :func:`evaluate_kinsoft_level` — load the frozen ground-truth + reported
  inter-tool spread (``schema/kinsoft_reference.json``) and check a fitted level
  against the **advisory** band. Only the archetypal 2-state level (Fig. 2) has an
  active oracle: its well-separated FRET states are what a FRET-only idealizer can
  faithfully recover. The 3-state non-equilibrium (Fig. 3) and 4-state
  kinetic-heterogeneity (Fig. 4) levels have overlapping / directional structure
  the benchmark itself only compares via cumulative dwell-time distributions
  [Götz2022]; their ground truth is recorded for a future multi-state extension.

All headless (no Qt, no sidecar) → runs in the base CI matrix; the fixture-backed
oracle is gated behind ``@pytest.mark.large`` (the LFS ``kinsoft_sim.hdf5``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Iterable
    from os import PathLike

    from tether.io.kinsoft import KinsoftLevel

__all__ = [
    "DEFAULT_HMM_MAX_ITER",
    "DEFAULT_HMM_TOL",
    "DEFAULT_HMM_VAR_FLOOR",
    "GaussianHMM",
    "KinsoftKineticsResult",
    "KinsoftReference",
    "TwoStateKinetics",
    "evaluate_kinsoft_level",
    "fit_gaussian_hmm",
    "load_kinsoft_reference",
    "pooled_exit_rates",
    "two_state_rate_constants",
    "viterbi_paths",
]

#: Baum-Welch iteration cap — EM on well-separated smFRET states converges in far
#: fewer; a rendering/analysis parameter, not a PRD §11.2 tunable.
DEFAULT_HMM_MAX_ITER = 200

#: Relative log-likelihood change that stops EM (``|Δℓ| ≤ tol·(1+|ℓ|)``).
DEFAULT_HMM_TOL = 1e-6

#: Lower bound on a state's emission variance (guards a degenerate all-identical
#: state / division by zero in the Gaussian density).
DEFAULT_HMM_VAR_FLOOR = 1e-6

_LOG_TINY = 1e-300  # clip floor before a log, to keep -inf out of the recursions


# --- Gaussian HMM idealization (self-contained, base env) ----------------------


@dataclass(frozen=True)
class GaussianHMM:
    """A fitted shared-parameter Gaussian hidden Markov model.

    States are sorted by **ascending emission mean** (the canonical order Tether
    uses everywhere, so state 0 is the lowest-FRET state). ``tmatrix`` is the
    row-stochastic discrete-time transition matrix; ``start_prob`` the initial
    distribution. ``log_likelihood`` is the final scaled forward log-likelihood
    summed over all fitted traces.
    """

    means: np.ndarray  # (nstates,) ascending
    sigmas: np.ndarray  # (nstates,)
    tmatrix: np.ndarray  # (nstates, nstates) row-stochastic
    start_prob: np.ndarray  # (nstates,)
    log_likelihood: float
    n_iter: int
    converged: bool

    @property
    def nstates(self) -> int:
        """Number of hidden states in the fitted model."""
        return int(self.means.shape[0])


def _emission_matrix(x: np.ndarray, means: np.ndarray, sigmas: np.ndarray) -> np.ndarray:
    """``(T, nstates)`` Gaussian emission densities ``𝒩(x_t; μ_k, σ_k)``."""
    z = (x[:, None] - means[None, :]) / sigmas[None, :]
    return np.exp(-0.5 * z * z) / (sigmas[None, :] * np.sqrt(2.0 * np.pi))


def _as_trace_list(traces: Iterable[np.ndarray]) -> list[np.ndarray]:
    out: list[np.ndarray] = []
    for t in traces:
        a = np.asarray(t, dtype="float64").ravel()
        if a.size >= 1:
            out.append(a)
    return out


def fit_gaussian_hmm(
    traces: Iterable[np.ndarray],
    nstates: int = 2,
    *,
    n_iter: int = DEFAULT_HMM_MAX_ITER,
    tol: float = DEFAULT_HMM_TOL,
    var_floor: float = DEFAULT_HMM_VAR_FLOOR,
) -> GaussianHMM:
    """Fit a Gaussian HMM to a collection of 1-D signal traces (shared parameters).

    Scaled forward-backward Baum-Welch (EM) [Rabiner1989, Bilmes1998] with a
    **deterministic** initialization — state means at evenly spaced quantiles of the
    pooled signal, a diagonal-dominant transition matrix, uniform start — so the fit
    is reproducible across platforms without a seed (no random restarts). The traces
    share one emission/transition parameter set (the ensemble model the benchmark
    tools fit), which is what makes a per-dataset rate estimate meaningful.

    Parameters
    ----------
    traces
        Iterable of 1-D arrays (e.g. per-molecule FRET-efficiency series). Empty
        traces are dropped; a length-1 trace contributes only an emission/start term.
    nstates
        Number of hidden states (``>= 1``).
    n_iter, tol
        EM iteration cap and the relative log-likelihood convergence threshold.
    var_floor
        Lower bound on each state's emission variance.

    Returns
    -------
    GaussianHMM
        States sorted by ascending mean.

    Raises
    ------
    ValueError
        ``nstates < 1`` or no non-empty traces.
    """
    if nstates < 1:
        raise ValueError(f"nstates must be >= 1, got {nstates}")
    data = _as_trace_list(traces)
    if not data:
        raise ValueError("no non-empty traces to fit")

    pooled = np.concatenate(data)
    qs = (np.arange(nstates) + 0.5) / nstates
    means = np.quantile(pooled, qs).astype("float64")
    # Nudge apart any coincident quantiles (near-constant pooled signal) so states
    # start distinguishable.
    for k in range(1, nstates):
        if means[k] <= means[k - 1]:
            means[k] = means[k - 1] + 1e-6
    base_sigma = max(float(pooled.std()) / max(nstates, 1), 1e-3)
    sigmas = np.full(nstates, base_sigma, dtype="float64")
    stay = 0.95 if nstates > 1 else 1.0
    tmatrix = np.full((nstates, nstates), (1.0 - stay) / max(nstates - 1, 1))
    np.fill_diagonal(tmatrix, stay)
    start = np.full(nstates, 1.0 / nstates)

    prev_ll = -np.inf
    converged = False
    n_done = 0
    for iteration in range(1, n_iter + 1):
        n_done = iteration
        start_acc = np.zeros(nstates)
        trans_num = np.zeros((nstates, nstates))
        trans_den = np.zeros(nstates)
        mean_num = np.zeros(nstates)
        sq_num = np.zeros(nstates)
        gamma_sum = np.zeros(nstates)
        total_ll = 0.0

        for x in data:
            T = x.shape[0]
            B = np.clip(_emission_matrix(x, means, sigmas), _LOG_TINY, None)
            # scaled forward
            alpha = np.empty((T, nstates))
            c = np.empty(T)
            alpha[0] = start * B[0]
            c[0] = alpha[0].sum()
            alpha[0] /= c[0]
            for t in range(1, T):
                alpha[t] = (alpha[t - 1] @ tmatrix) * B[t]
                c[t] = alpha[t].sum()
                alpha[t] /= c[t]
            total_ll += float(np.log(c).sum())
            # scaled backward
            beta = np.empty((T, nstates))
            beta[-1] = 1.0
            for t in range(T - 2, -1, -1):
                beta[t] = (tmatrix @ (B[t + 1] * beta[t + 1])) / c[t + 1]
            gamma = alpha * beta
            gamma /= gamma.sum(axis=1, keepdims=True)

            start_acc += gamma[0]
            gamma_sum += gamma.sum(axis=0)
            mean_num += (gamma * x[:, None]).sum(axis=0)
            sq_num += (gamma * (x[:, None] ** 2)).sum(axis=0)
            if T > 1:
                # xi summed over time, vectorized: Σ_t α_t ⊗ (B_{t+1} β_{t+1} / c_{t+1})
                # is the outer-product accumulation ``alpha[:-1].T @ (B[1:]·β[1:]/c[1:])``,
                # scaled elementwise by the transition matrix — equivalent to the per-t loop
                # but without the Python-level iteration over frames.
                bwd = (B[1:] * beta[1:]) / c[1:, None]  # (T-1, nstates)
                trans_num += tmatrix * (alpha[:-1].T @ bwd)
                trans_den += gamma[:-1].sum(axis=0)

        # M-step
        start = start_acc / start_acc.sum()
        means = mean_num / np.maximum(gamma_sum, 1e-12)
        var = sq_num / np.maximum(gamma_sum, 1e-12) - means**2
        sigmas = np.sqrt(np.maximum(var, var_floor))
        if nstates > 1:
            with np.errstate(invalid="ignore", divide="ignore"):
                new_t = trans_num / np.maximum(trans_den[:, None], 1e-12)
            # A row with no observed exits (unvisited state) keeps its prior row.
            rows = trans_den > 0
            tmatrix = np.where(rows[:, None], new_t, tmatrix)
            tmatrix = tmatrix / tmatrix.sum(axis=1, keepdims=True)

        if abs(total_ll - prev_ll) <= tol * (1.0 + abs(prev_ll)):
            converged = True
            prev_ll = total_ll
            break
        prev_ll = total_ll

    order = np.argsort(means)
    return GaussianHMM(
        means=means[order].copy(),
        sigmas=sigmas[order].copy(),
        tmatrix=tmatrix[np.ix_(order, order)].copy(),
        start_prob=start[order].copy(),
        log_likelihood=float(prev_ll),
        n_iter=n_done,
        converged=converged,
    )


def viterbi_paths(traces: Iterable[np.ndarray], hmm: GaussianHMM) -> list[np.ndarray]:
    """Most-likely (Viterbi) integer state path for each trace under ``hmm``.

    Log-space Viterbi with the fitted emission/transition parameters; states are the
    ascending-mean-sorted indices of ``hmm``. Empty traces are dropped (mirroring
    :func:`fit_gaussian_hmm`), so the returned list aligns with the non-empty inputs.
    """
    data = _as_trace_list(traces)
    ns = hmm.nstates
    log_t = np.log(np.clip(hmm.tmatrix, _LOG_TINY, None))
    log_start = np.log(np.clip(hmm.start_prob, _LOG_TINY, None))
    paths: list[np.ndarray] = []
    for x in data:
        T = x.shape[0]
        log_b = np.log(np.clip(_emission_matrix(x, hmm.means, hmm.sigmas), _LOG_TINY, None))
        delta = np.empty((T, ns))
        psi = np.zeros((T, ns), dtype=np.int64)
        delta[0] = log_start + log_b[0]
        for t in range(1, T):
            scores = delta[t - 1][:, None] + log_t  # (prev, cur)
            psi[t] = np.argmax(scores, axis=0)
            delta[t] = scores[psi[t], np.arange(ns)] + log_b[t]
        path = np.empty(T, dtype=np.int64)
        path[-1] = int(np.argmax(delta[-1]))
        for t in range(T - 2, -1, -1):
            path[t] = psi[t + 1, path[t + 1]]
        paths.append(path)
    return paths


# --- kinetic-rate inference ----------------------------------------------------


def pooled_exit_rates(
    paths: Iterable[np.ndarray],
    dt: float,
    *,
    include_first: bool = False,
) -> dict[int, float]:
    """Per-state exit rate ``k = 1/⟨τ⟩`` from the pooled dwell times of state paths.

    The maximum-likelihood exit-rate estimator for a continuous-time Markov process
    the benchmark uses [Götz2022]: pool every fully-observed dwell of a state across
    all molecules and take the reciprocal of the mean dwell **time** (frames × ``dt``).
    Dwell extraction reuses :func:`tether.analysis.dwell.state_dwells` (a sentinel
    ``no_state = -1`` never present in a Viterbi path), so the first/last dwell of
    each molecule is censored exactly as in the M6 dwell analysis — the finite
    observation window rarely coincides with a true state entry/exit.

    Returns ``{state: k}`` (per second when ``dt`` is in seconds). A state with no
    fully-observed interior dwell is absent from the mapping.
    """
    from tether.analysis.dwell import state_dwells

    sd = state_dwells(paths, no_state=-1, include_first=include_first)
    rates: dict[int, float] = {}
    for state, lengths in sd.lengths.items():
        mean_dwell_s = float(np.mean(lengths)) * float(dt)
        rates[state] = 1.0 / mean_dwell_s if mean_dwell_s > 0 else float("nan")
    return rates


@dataclass(frozen=True)
class TwoStateKinetics:
    """Fitted 2-state kinetics of a set of FRET traces.

    ``fret_low``/``fret_high`` are the idealized state means (ascending);
    ``k_low_high`` is the exit rate from the low-FRET state (``k₁₂``) and
    ``k_high_low`` from the high-FRET state (``k₂₁``), in ``1/s`` when ``dt`` is in
    seconds. ``n_dwells_low``/``n_dwells_high`` are the pooled fully-observed dwell
    counts each rate rests on.
    """

    fret_low: float
    fret_high: float
    k_low_high: float
    k_high_low: float
    n_dwells_low: int
    n_dwells_high: int
    hmm: GaussianHMM


def two_state_rate_constants(
    traces: Iterable[np.ndarray],
    dt: float,
    *,
    n_iter: int = DEFAULT_HMM_MAX_ITER,
    tol: float = DEFAULT_HMM_TOL,
) -> TwoStateKinetics:
    """Fit a 2-state Gaussian HMM to ``traces`` and infer both exit rates.

    Pipeline: :func:`fit_gaussian_hmm` (nstates=2) → :func:`viterbi_paths` →
    :func:`pooled_exit_rates`. ``dt`` is seconds per frame (rates come out in
    ``1/s``). Convenience for the archetypal 2-state kinSoft level.
    """
    data = _as_trace_list(traces)
    hmm = fit_gaussian_hmm(data, 2, n_iter=n_iter, tol=tol)
    paths = viterbi_paths(data, hmm)
    rates = pooled_exit_rates(paths, dt)

    from tether.analysis.dwell import state_dwells

    sd = state_dwells(paths, no_state=-1)
    return TwoStateKinetics(
        fret_low=float(hmm.means[0]),
        fret_high=float(hmm.means[1]),
        k_low_high=float(rates.get(0, float("nan"))),
        k_high_low=float(rates.get(1, float("nan"))),
        n_dwells_low=int(sd.lengths.get(0, np.empty(0, dtype=np.int64)).size),
        n_dwells_high=int(sd.lengths.get(1, np.empty(0, dtype=np.int64)).size),
        hmm=hmm,
    )


# --- kinSoftChallenge within-inter-tool-spread oracle --------------------------


@dataclass(frozen=True)
class KinsoftReference:
    """The frozen kinSoftChallenge ground truth + reported inter-tool spread.

    Loaded from ``schema/kinsoft_reference.json`` (single source of truth, mirroring
    ``schema/parity_tolerance.json``). ``band`` is the **advisory** maximum relative
    deviation of a fitted rate from ground truth; ``levels`` maps ``level1``/… to the
    per-dataset record (ground-truth rates, reported spread, whether the oracle is
    active). ``raw`` is the full parsed document for provenance/introspection.
    """

    band_rate_rel_deviation_max: float
    levels: dict[str, dict]
    raw: dict

    def active_levels(self) -> list[str]:
        """Level names whose oracle is ``active`` (fittable by a 2-state idealizer)."""
        return [name for name, lv in self.levels.items() if lv.get("oracle") == "active"]


def load_kinsoft_reference(path: str | PathLike[str]) -> KinsoftReference:
    """Read the frozen kinSoft reference from ``schema/kinsoft_reference.json``."""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    return KinsoftReference(
        band_rate_rel_deviation_max=float(doc["band"]["rate_rel_deviation_max"]),
        levels=doc["levels"],
        raw=doc,
    )


@dataclass(frozen=True)
class KinsoftKineticsResult:
    """Outcome of fitting one kinSoft level's rates against the frozen band.

    ``rates`` and ``ground_truth`` are ``{"k12_low_high", "k21_high_low"}`` in
    ``1/s``; ``rel_deviation`` the per-rate ``|k − GT|/GT``. ``within_band`` is True
    when every rate is within :attr:`band` — an **advisory** check (§9 M8). ``failures``
    names any rate outside the band.
    """

    level: str
    rates: dict[str, float]
    ground_truth: dict[str, float]
    rel_deviation: dict[str, float]
    band: float
    within_band: bool
    failures: list[str]
    kinetics: TwoStateKinetics


def evaluate_kinsoft_level(
    level: KinsoftLevel,
    reference: KinsoftReference,
    level_name: str,
) -> KinsoftKineticsResult:
    """Fit ``level``'s 2-state rates and compare them to the frozen inter-tool band.

    Only defined for a level whose reference record is a 2-state system with an
    ``active`` oracle (currently ``level1``/Fig. 2). Each trace's FRET-efficiency
    series is idealized by the base-env 2-state HMM, the two exit rates inferred by
    pooled dwell-time MLE, and each checked against the ground truth to within the
    reported inter-tool spread (:attr:`KinsoftReference.band`).

    Raises
    ------
    KeyError
        ``level_name`` is not in the reference.
    ValueError
        The referenced level is not an active 2-state oracle.
    """
    record = reference.levels[level_name]
    if record.get("oracle") != "active" or int(record.get("nstates", 0)) != 2:
        raise ValueError(
            f"{level_name!r} has no active 2-state oracle "
            f"(oracle={record.get('oracle')!r}, nstates={record.get('nstates')})"
        )
    gt = record["ground_truth"]["rates_s_inv"]
    gt_rates = {
        "k12_low_high": float(gt["k12_low_high"]),
        "k21_high_low": float(gt["k21_high_low"]),
    }

    traces = [level.trace(i).fret_e for i in range(level.n_traces)]
    kin = two_state_rate_constants(traces, float(level.frame_time_s))
    rates = {"k12_low_high": kin.k_low_high, "k21_high_low": kin.k_high_low}

    band = reference.band_rate_rel_deviation_max
    rel = {k: abs(rates[k] - gt_rates[k]) / gt_rates[k] for k in gt_rates}
    failures = [f"{k}: {rel[k]:.3f} > {band:.3f}" for k in rel if not rel[k] <= band]
    return KinsoftKineticsResult(
        level=level_name,
        rates=rates,
        ground_truth=gt_rates,
        rel_deviation=rel,
        band=band,
        within_band=not failures,
        failures=failures,
        kinetics=kin,
    )
