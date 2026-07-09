# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Dwell-time distributions + survival fits — M6 B2, FR-ANALYZE (PRD §7.7, Appendix C B2).

A dwell-time analysis reads the *kinetics* off an idealized population: for each
state, how long the system stays there before leaving. From a persisted
``/idealization/{model}`` Viterbi state path the runs of constant state are the
dwells; their **empirical survival function** ``S(τ) = P(dwell > τ)`` is fit to a
sum of exponentials whose rate constants ``k`` are the state's exit rates
[Schrangl2024][Qin2004]. This is a faithful port of tMAVEN's dwell pipeline
(``controllers/modeler/dwells.py`` + ``controllers/analysis_plots/survival_dwell.py``)
so the plot reproduces its tMAVEN counterpart (the §9 M6 parity clause), with the two
Tether invariants tMAVEN has no analogue for layered on at the store path: **fresh
idealizations only** (STALE molecules excluded, PRD §5.1) and the §7.5 curation filter.

Three pieces, mirroring :mod:`tether.analysis.tdp`:

* :func:`state_dwells` — the pure extraction core (iterable of per-molecule state
  paths → per-state dwell-length samples). Faithful to tMAVEN ``generate_dwells``:
  the **first** and **last** dwell of every molecule are censored (the analysis
  window rarely coincides with a true state entry/exit), so only fully-observed
  interior dwells contribute — the last is always dropped, the first unless
  ``include_first`` (tMAVEN's ``first_flag``). Right-censoring this way is the
  standard survival-analysis handling of finite observation windows [Schrangl2024].
* :func:`survival_curve` / :func:`fit_survival` — the empirical survival function
  (tMAVEN ``survival``) and its non-linear-least-squares exponential fit (tMAVEN
  ``optimize_*_surv``), with covariance-derived standard errors + Student-``t``
  confidence intervals + fit residuals.
* :func:`population_dwell_times` — the ``.tether`` store entry point that assembles a
  :class:`DwellTimeAnalysis` per state from a model's fresh, accepted Viterbi paths.

All headless (no Qt) → runs in the base CI matrix. ``dt`` (seconds per frame),
``nbins``, the fit model, and the CI level are analysis/render parameters, **not**
PRD §11.2 tunables (the B1 TDP precedent for tMAVEN rendering defaults): rates are
reported in ``1 / (frames · dt)``; with the default ``dt = 1`` they are per-frame.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Iterable
    from os import PathLike

    from tether.project.core import Project

    ProjectRef = Project | str | PathLike[str]

__all__ = [
    "DEFAULT_DWELL_CI_LEVEL",
    "DEFAULT_DWELL_DT",
    "DEFAULT_DWELL_NBINS",
    "DWELL_MODEL_NPARAMS",
    "DwellFit",
    "DwellTimeAnalysis",
    "StateDwells",
    "double_exp_survival",
    "fit_survival",
    "population_dwell_times",
    "single_exp_survival",
    "state_dwells",
    "stretched_exp_survival",
    "survival_curve",
    "triple_exp_survival",
]

#: Seconds per frame — converts a dwell measured in frames to physical time so a fitted
#: rate ``k`` is in ``1/second``. An acquisition property (ideally the movie frame
#: interval), passed in per analysis; the default 1.0 leaves everything per-frame. A
#: rendering/analysis parameter, **not** a PRD §11.2 tunable (the B1 TDP precedent).
DEFAULT_DWELL_DT = 1.0

#: tMAVEN ``survival_dwell.py`` ``dwell_nbins``: bins for the dwell-time *distribution*
#: histogram view. A rendering default, **not** a PRD §11.2 tunable.
DEFAULT_DWELL_NBINS = 51

#: Confidence level for the fitted-parameter intervals (two-sided Student-``t``).
DEFAULT_DWELL_CI_LEVEL = 0.95

#: Free-parameter count of each survival model (used for the fit's residual degrees of
#: freedom and the "too few points to fit" guard).
DWELL_MODEL_NPARAMS = {"single": 2, "double": 4, "triple": 6, "stretched": 3}


# --- exponential survival forms (tMAVEN modeler/fxns/exponentials.py) ----------


def single_exp_survival(tau: np.ndarray, k: float, a: float) -> np.ndarray:
    """Single-exponential survival ``A·exp(-k·τ)`` (tMAVEN ``single_exp_surv``)."""
    return a * np.exp(-k * tau)


def double_exp_survival(tau: np.ndarray, k1: float, k2: float, a: float, b: float) -> np.ndarray:
    """Bi-exponential survival ``A·exp(-k1·τ) + B·exp(-k2·τ)`` (tMAVEN ``double_exp_surv``)."""
    return a * np.exp(-k1 * tau) + b * np.exp(-k2 * tau)


def triple_exp_survival(
    tau: np.ndarray, k1: float, k2: float, k3: float, a: float, b: float, c: float
) -> np.ndarray:
    """Tri-exponential survival (tMAVEN ``triple_exp_surv``)."""
    return a * np.exp(-k1 * tau) + b * np.exp(-k2 * tau) + c * np.exp(-k3 * tau)


def stretched_exp_survival(tau: np.ndarray, k: float, beta: float, a: float) -> np.ndarray:
    """Stretched-exponential survival ``A·exp(-(k·τ)^β)`` (tMAVEN ``stretched_exp_surv``)."""
    return a * np.exp(-((k * tau) ** beta))


# --- dwell extraction (tMAVEN modeler/dwells.py generate_dwells) ---------------


@dataclass(frozen=True)
class StateDwells:
    """Per-state dwell-length samples extracted from a population's state paths.

    ``lengths[state]`` is the int64 array of that state's fully-observed dwell
    durations **in frames** (each an interior run of the constant state index);
    ``n_molecules[state]`` is how many molecules contributed at least one such dwell.
    Only states that occur as an interior dwell appear as keys.
    """

    lengths: dict[int, np.ndarray]  # state index -> int64 dwell lengths (frames)
    n_molecules: dict[int, int]  # state index -> molecules contributing >= 1 dwell
    include_first: bool  # whether each molecule's first (left-censored) dwell was kept


def state_dwells(
    state_chunks: Iterable[np.ndarray],
    *,
    no_state: int | None = None,
    include_first: bool = False,
) -> StateDwells:
    """Extract per-state dwell lengths from per-molecule Viterbi state paths.

    Each element of ``state_chunks`` is one molecule's integer state path (``no_state``
    marks frames outside the idealized window / interior gaps). For each molecule the
    ``no_state`` frames are dropped and the remainder is split into runs of constant
    state (tMAVEN ``np.split`` on ``np.diff != 0``); each run is one dwell. The
    **last** run is always discarded — the trace ends before the molecule is observed
    to leave that state, so its true duration is unknown (right-censored). The
    **first** run is discarded too unless ``include_first`` — the window rarely begins
    exactly when the molecule entered the state, so the first dwell is left-censored
    (tMAVEN's ``first_flag``). Every remaining interior run's ``(state, length)`` is
    recorded. Restricting to fully-observed interior dwells is the standard survival
    handling of a finite observation window [Schrangl2024].

    A molecule with no transition (a single run) contributes nothing — its lone dwell
    is censored on both ends.

    Parameters
    ----------
    state_chunks
        Iterable of 1-D per-molecule integer state-path arrays.
    no_state
        Sentinel for out-of-window / gap frames. ``None`` uses
        :data:`tether.idealize.NO_STATE`.
    include_first
        Keep each molecule's first (left-censored) dwell (default drops it).

    Returns
    -------
    StateDwells
    """
    if no_state is None:
        from tether.idealize import NO_STATE

        no_state = NO_STATE

    lengths: dict[int, list[int]] = {}
    n_molecules: dict[int, int] = {}
    for chunk in state_chunks:
        if np.ndim(chunk) == 0:
            # A scalar element means a flat 1-D array was passed instead of an iterable
            # of per-molecule arrays (``state_dwells(path)`` vs ``[path]``) — iterating
            # it would silently record nothing. Fail fast on this public-API misuse.
            raise ValueError(
                "state_chunks must be an iterable of 1-D per-molecule state paths, got a "
                "scalar element — wrap a single molecule as [path], not path"
            )
        s = np.asarray(chunk).ravel()
        s = s[s != no_state]  # drop out-of-window / interior-gap frames (tMAVEN NaN strip)
        if s.size == 0:
            continue
        # runs of constant state: split where the value changes (tMAVEN gen split)
        runs = np.split(s, np.flatnonzero(np.diff(s) != 0) + 1)
        if len(runs) <= 1:  # no transition -> the sole dwell is censored both ends
            continue
        start = 0 if include_first else 1
        interior = runs[start:-1]  # drop last (right-censored); drop first unless include_first
        contributed: set[int] = set()
        for run in interior:
            st = int(run[0])
            lengths.setdefault(st, []).append(int(run.size))
            contributed.add(st)
        for st in contributed:
            n_molecules[st] = n_molecules.get(st, 0) + 1

    return StateDwells(
        lengths={st: np.asarray(v, dtype=np.int64) for st, v in lengths.items()},
        n_molecules=n_molecules,
        include_first=include_first,
    )


# --- empirical survival (tMAVEN modeler/dwells.py survival) --------------------


def survival_curve(dwell_lengths: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Empirical survival function of a set of dwell lengths (tMAVEN ``survival``).

    Returns ``(tau, survival)`` where ``tau = arange(n)`` in **frames**
    (``n = max(dwell_lengths)``) and ``survival[i]`` is the fraction of dwells that
    last strictly longer than ``i`` frames, normalized so ``survival[0] = 1`` (every
    dwell is ≥ 1 frame). This is the complementary CDF the exponential fit targets. An
    empty input returns tMAVEN's degenerate ``(array([0]), array([0.0]))``.

    Dwell lengths are frame counts and must be ``>= 1``; a non-positive value (which
    would make ``np.bincount`` raise or ``raw[0]`` index out of bounds) is rejected
    with a clear :class:`ValueError` rather than crashing — this is a re-exported
    public helper, not only reached via :func:`state_dwells`.
    """
    d = np.asarray(dwell_lengths, dtype=np.int64).ravel()
    if d.size == 0:
        return np.array([0]), np.array([0.0])
    if int(d.min()) < 1:
        raise ValueError("dwell_lengths must be positive frame counts (>= 1)")
    n = int(d.max())
    m = d.size
    # raw[i] = #{dwell > i} = m - #{dwell <= i}; cumulative bincount is #{dwell <= i}.
    le = np.cumsum(np.bincount(d, minlength=n + 1))
    raw = (m - le[:n]).astype(np.float64)
    norm = np.zeros_like(raw) if raw[0] == 0 else raw / raw[0]
    return np.arange(n), norm


# --- exponential survival fit (tMAVEN modeler/dwells.py optimize_*_surv) -------


@dataclass(frozen=True)
class DwellFit:
    """An exponential fit to an empirical dwell survival curve.

    ``rates`` are the exit-rate constants ``k`` (ascending), in ``1 / time`` where
    ``time`` is whatever unit ``tau`` was fit in (seconds when the caller scaled by
    ``dt``, else per-frame); ``amplitudes`` are their weights, aligned to the sorted
    rates. ``rate_stderr`` / ``amplitude_stderr`` are the 1-σ standard errors from the
    fit covariance (tMAVEN's ``error``); ``rate_ci`` / ``amplitude_ci`` are two-sided
    ``ci_level`` half-widths (Student-``t`` on the residual degrees of freedom).
    ``model_survival`` is the fitted curve sampled at :attr:`tau` and ``residuals`` is
    ``survival - model_survival`` (the residual subplot). ``success`` is ``False`` when
    the fit did not converge or the covariance was not estimable — the parameters are
    then ``NaN`` and the plot should fall back to the bare survival curve.

    These intervals are the asymptotic covariance intervals of the **survival-curve
    least-squares fit** (tMAVEN reports the same ``sqrt(diag(pcov))`` errors), *not* a
    maximum-likelihood interval on the underlying dwell samples. Because the survival
    points are strongly correlated (a monotone cumulative), the fit residuals are small
    and the covariance can understate the true rate uncertainty for large samples — read
    the interval as the fit's parameter precision, not a rigorous coverage statement.
    """

    model: str  # "single" | "double" | "triple" | "stretched"
    rates: np.ndarray  # (n_exp,) float64, ascending
    amplitudes: np.ndarray  # (n_exp,) float64, aligned to rates
    beta: float | None  # stretch exponent (stretched model only)
    rate_stderr: np.ndarray  # (n_exp,) 1-sigma SE of each rate
    amplitude_stderr: np.ndarray  # (n_exp,) 1-sigma SE of each amplitude
    beta_stderr: float | None
    ci_level: float
    rate_ci: np.ndarray  # (n_exp,) two-sided CI half-width
    amplitude_ci: np.ndarray
    beta_ci: float | None
    r_squared: float
    tau: np.ndarray  # fit abscissa (time units)
    survival: np.ndarray  # observed survival at tau
    model_survival: np.ndarray  # fitted survival at tau
    residuals: np.ndarray  # survival - model_survival
    n_points: int  # survival samples used
    success: bool

    @property
    def annotation(self) -> str:
        """A tMAVEN-style textbox string (``k`` and ``A``, ``β`` when stretched)."""
        k = np.array2string(np.around(self.rates, 3), separator=", ")
        a = np.array2string(np.around(self.amplitudes, 3), separator=", ")
        lines = [f"k = {k}", f"A = {a}"]
        if self.beta is not None:
            lines.append(f"β = {round(self.beta, 3)}")
        if np.isfinite(self.r_squared):
            lines.append(f"R² = {round(self.r_squared, 3)}")
        return "\n".join(lines)


def _t_multiplier(ci_level: float, dof: int) -> float:
    """Two-sided Student-``t`` multiplier for ``ci_level`` on ``dof`` d.o.f. (NaN if dof<=0)."""
    if dof <= 0:
        return float("nan")
    from scipy import stats

    return float(stats.t.ppf(0.5 + ci_level / 2.0, dof))


def _r_squared(surv: np.ndarray, model: np.ndarray) -> float:
    ss_res = float(np.sum((surv - model) ** 2))
    ss_tot = float(np.sum((surv - np.mean(surv)) ** 2))
    if ss_tot == 0.0:  # flat survival (e.g. a single dwell) — R^2 undefined
        return float("nan")
    return 1.0 - ss_res / ss_tot


# Each model: its survival callable, free-parameter count, and how curve_fit's popt maps
# to (rates, amplitudes, beta). The amplitude/rate split lets us report and sort them.
def _fit_config(model: str):
    if model == "single":
        return single_exp_survival, ([0.0, 0.0], [np.inf, np.inf]), 2
    if model == "double":
        return double_exp_survival, ([0.0, 0.0, 0.0, 0.0], [np.inf, np.inf, 1.0, 1.0]), 4
    if model == "triple":
        bounds = ([0.0] * 6, [np.inf, np.inf, np.inf, 1.0, 1.0, np.inf])
        return triple_exp_survival, bounds, 6
    if model == "stretched":
        return stretched_exp_survival, ([0.0, 0.0, 0.0], [np.inf, 1.0, np.inf]), 3
    raise ValueError(f"model must be one of {sorted(DWELL_MODEL_NPARAMS)}, got {model!r}")


def _split_params(model: str, popt: np.ndarray, perr: np.ndarray):
    """Split popt/perr into (rates, rate_err, amps, amp_err, beta, beta_err), sorted by k."""
    if model == "single":
        return (popt[:1], perr[:1], popt[1:2], perr[1:2], None, None)
    if model == "stretched":
        # popt = (k, beta, A)
        return (popt[:1], perr[:1], popt[2:3], perr[2:3], float(popt[1]), float(perr[1]))
    n = 2 if model == "double" else 3
    ks, amps = popt[:n], popt[n:]
    ke, ae = perr[:n], perr[n:]
    order = np.argsort(ks)
    return (ks[order], ke[order], amps[order], ae[order], None, None)


def fit_survival(
    tau: np.ndarray,
    survival: np.ndarray,
    *,
    model: str = "single",
    ci_level: float = DEFAULT_DWELL_CI_LEVEL,
) -> DwellFit:
    """Fit an exponential survival model to a dwell survival curve (tMAVEN ``optimize_*_surv``).

    Non-linear least squares (``scipy.optimize.curve_fit``, bounded, ``trf``) of the
    ``model`` survival function to ``(tau, survival)``. ``tau`` is taken in whatever
    time unit the caller chose (scale by ``dt`` for seconds, giving ``k`` in
    ``1/second``). Standard errors are ``sqrt(diag(pcov))`` and confidence intervals
    use the two-sided Student-``t`` multiplier on ``n_points - n_params`` degrees of
    freedom. A non-converging fit or an unestimable covariance yields a
    :class:`DwellFit` with ``success = False`` and ``NaN`` parameters rather than
    raising — the analysis view then shows the bare survival curve.

    Parameters
    ----------
    tau, survival
        The empirical survival curve from :func:`survival_curve` (``tau`` optionally
        scaled to physical time).
    model
        ``"single"``, ``"double"``, ``"triple"``, or ``"stretched"``.
    ci_level
        Two-sided confidence level for the interval half-widths.

    Returns
    -------
    DwellFit
    """
    from scipy.optimize import curve_fit

    fn, bounds, nparams = _fit_config(model)
    tau = np.asarray(tau, dtype="float64")
    survival = np.asarray(survival, dtype="float64")
    n_points = int(tau.size)

    def _failed() -> DwellFit:
        n_exp = 1 if model in ("single", "stretched") else (2 if model == "double" else 3)
        nan_exp = np.full(n_exp, np.nan)
        return DwellFit(
            model=model,
            rates=nan_exp.copy(),
            amplitudes=nan_exp.copy(),
            beta=(float("nan") if model == "stretched" else None),
            rate_stderr=nan_exp.copy(),
            amplitude_stderr=nan_exp.copy(),
            beta_stderr=(float("nan") if model == "stretched" else None),
            ci_level=ci_level,
            rate_ci=nan_exp.copy(),
            amplitude_ci=nan_exp.copy(),
            beta_ci=(float("nan") if model == "stretched" else None),
            r_squared=float("nan"),
            tau=tau,
            survival=survival,
            model_survival=np.full_like(tau, np.nan),
            residuals=np.full_like(tau, np.nan),
            n_points=n_points,
            success=False,
        )

    if n_points <= nparams or not np.all(np.isfinite(survival)):
        return _failed()

    try:
        popt, pcov = curve_fit(fn, tau, survival, bounds=bounds, method="trf", maxfev=10000)
    except (RuntimeError, ValueError):
        return _failed()

    perr = np.sqrt(np.diag(pcov))
    if not np.all(np.isfinite(perr)):  # rank-deficient Jacobian -> inf covariance
        return _failed()

    model_surv = fn(tau, *popt)
    residuals = survival - model_surv
    rates, rate_err, amps, amp_err, beta, beta_err = _split_params(model, popt, perr)
    dof = n_points - nparams
    t = _t_multiplier(ci_level, dof)

    return DwellFit(
        model=model,
        rates=np.asarray(rates, dtype="float64"),
        amplitudes=np.asarray(amps, dtype="float64"),
        beta=beta,
        rate_stderr=np.asarray(rate_err, dtype="float64"),
        amplitude_stderr=np.asarray(amp_err, dtype="float64"),
        beta_stderr=beta_err,
        ci_level=ci_level,
        rate_ci=np.asarray(rate_err, dtype="float64") * t,
        amplitude_ci=np.asarray(amp_err, dtype="float64") * t,
        beta_ci=(beta_err * t if beta_err is not None else None),
        r_squared=_r_squared(survival, model_surv),
        tau=tau,
        survival=survival,
        model_survival=model_surv,
        residuals=residuals,
        n_points=n_points,
        success=True,
    )


# --- per-state analysis + store entry point -----------------------------------


@dataclass(frozen=True)
class DwellTimeAnalysis:
    """A single state's dwell-time distribution, survival curve, and exponential fit.

    ``dwell_lengths`` are the state's fully-observed interior dwells **in frames**;
    :attr:`tau` and the fit work in physical time (``frames · dt``). ``fit`` is
    ``None`` only when the state had no dwell at all; otherwise it is a
    :class:`DwellFit` (possibly ``success = False``).
    """

    state: int
    level: float  # idealized FRET level of the state (means[state])
    dt: float  # seconds per frame used for tau / the fit
    dwell_lengths: np.ndarray  # int64 (frames)
    n_dwells: int
    n_molecules: int  # molecules contributing >= 1 dwell to this state
    tau: np.ndarray  # time units (arange(n) * dt)
    survival: np.ndarray  # empirical survival at tau
    fit: DwellFit | None

    def histogram(self, nbins: int = DEFAULT_DWELL_NBINS) -> tuple[np.ndarray, np.ndarray]:
        """Dwell-time distribution as ``(bin_centres, density)`` over the dwell times.

        The probability-density histogram of the dwell **times** (``dwell_lengths ·
        dt``) tMAVEN draws for the distribution view. Returns empty arrays when there
        are no dwells.
        """
        if self.dwell_lengths.size == 0:
            return np.empty(0), np.empty(0)
        times = self.dwell_lengths.astype("float64") * self.dt
        lo, hi = float(times.min()), float(times.max())
        if hi <= lo:  # all dwells identical -> a single degenerate bin
            hi = lo + 1.0
        density, edges = np.histogram(times, bins=int(nbins), range=(lo, hi), density=True)
        centres = 0.5 * (edges[:-1] + edges[1:])
        return centres, density.astype("float64")


def population_dwell_times(
    project: ProjectRef,
    model_name: str,
    *,
    state: int | None = None,
    dt: float = DEFAULT_DWELL_DT,
    molecule_keys: list[str] | None = None,
    include_first: bool = False,
    model: str = "single",
    ci_level: float = DEFAULT_DWELL_CI_LEVEL,
    include_rejected: bool = False,
    include_stale: bool = False,
) -> dict[int, DwellTimeAnalysis] | DwellTimeAnalysis:
    """Dwell-time analyses from a ``.tether`` store (§10 B2; Appendix C B2; PRD §7.7).

    Extracts each accepted, **fresh** molecule's dwells from the
    ``/idealization/{model_name}`` Viterbi state paths (:func:`state_dwells`), builds
    each state's empirical survival curve (:func:`survival_curve`), and fits it
    (:func:`fit_survival`). STALE molecules are excluded (PRD §5.1) unless
    ``include_stale``; rejected molecules are excluded unless ``include_rejected``
    (§7.5) — the same fresh + curation contract as :func:`population_transition_density`.

    Parameters
    ----------
    project
        A :class:`~tether.project.core.Project` or a path to a ``.tether`` store.
    model_name
        Which ``/idealization/{model_name}`` supplies the state paths + means.
    state
        A single state index to analyse; ``None`` returns every state that has dwells.
    dt
        Seconds per frame (rates come out in ``1/second``); default per-frame.
    molecule_keys
        Restrict to these ``molecule_key`` values (``None`` = all), intersected with
        the fresh + curation filters.
    include_first
        Keep each molecule's first (left-censored) dwell (default drops it).
    model
        Survival fit model (``"single"`` .. ``"stretched"``).
    ci_level
        Confidence level for the fitted-parameter intervals.
    include_rejected, include_stale
        Curation / freshness overrides (default excludes both).

    Returns
    -------
    dict[int, DwellTimeAnalysis] or DwellTimeAnalysis
        A mapping ``state -> analysis`` (ascending states), or the single
        :class:`DwellTimeAnalysis` when ``state`` is given (an empty analysis — no
        dwells, ``fit = None`` — if that state never dwells).

    Raises
    ------
    KeyError
        No ``/idealization/{model_name}`` in the store.
    """
    from tether.analysis._store import windowed_states
    from tether.idealize import NO_STATE
    from tether.project.idealize import live_molecule_keys, read_idealization

    stored = read_idealization(project, model_name)
    means = np.asarray(stored.means, dtype="float64")

    keys = molecule_keys
    if not include_stale:
        live = live_molecule_keys(project, model_name)
        if molecule_keys is None:
            keys = live
        else:
            live_set = set(live)
            keys = [k for k in molecule_keys if k in live_set]

    states = windowed_states(project, model_name, keys, include_rejected)
    dwells = state_dwells(states, no_state=NO_STATE, include_first=include_first)

    def _analysis(st: int) -> DwellTimeAnalysis:
        lengths = dwells.lengths.get(st, np.empty(0, dtype=np.int64))
        tau_frames, surv = survival_curve(lengths)
        tau_time = tau_frames.astype("float64") * dt
        fit = None
        if lengths.size:
            fit = fit_survival(tau_time, surv, model=model, ci_level=ci_level)
        level = float(means[st]) if 0 <= st < means.size else float("nan")
        return DwellTimeAnalysis(
            state=st,
            level=level,
            dt=dt,
            dwell_lengths=lengths,
            n_dwells=int(lengths.size),
            n_molecules=dwells.n_molecules.get(st, 0),
            tau=tau_time,
            survival=surv,
            fit=fit,
        )

    if state is not None:
        return _analysis(int(state))
    return {st: _analysis(st) for st in sorted(dwells.lengths)}
