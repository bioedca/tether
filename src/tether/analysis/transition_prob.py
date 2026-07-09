# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Transition-probability histogram — M6 B3, FR-ANALYZE (PRD §7.7, Appendix C B3).

tMAVEN's ``tm_hist.py`` histograms, over a population, each trace's **fitted**
transition probability between a chosen ordered state pair: from every trace's own
vbFRET model it reads ``norm_tmatrix[init, fin]`` (the state pair matched to the
composite model by Gaussian nearest-mean) and bins those per-trace probabilities,
optionally overlaying a Gaussian-KDE. That plot reads a per-trace transition matrix
Tether does not persist — Tether fits a **consensus / global** model with one shared
``norm_tmatrix`` (:class:`tether.project.idealize.StoredIdealization`), which gives a
single number per pair, not a distribution.

So, exactly as the B1 real TDP (:mod:`tether.analysis.tdp`) rebuilds a model-plot
from persisted Viterbi paths, Tether's B3 is the **empirical** analogue: each
molecule's transition probability is estimated from its own Viterbi state path as the
maximum-likelihood one-step rate

    P(init → fin) = (# frames where state = init and next state = fin)
                    / (# frames where state = init and a next state is observed),

the per-trace estimate of ``norm_tmatrix[init, fin]``. A molecule that never occupies
``init`` (with an observed successor) has no defined probability and is dropped — the
analogue of tMAVEN's non-finite filter. Frames whose successor is
:data:`~tether.idealize.NO_STATE` (a window edge or interior gap) are excluded from
the denominator: a transition across a gap is unobserved, never assumed. Pooled over
molecules, the probabilities are binned like tMAVEN (``[-0.05, 1.05]``, 25 bins,
density) with an optional Gaussian-KDE curve [McKinney2006][vandeMeent2014].

:func:`empirical_transition_probability` is the per-molecule scalar;
:func:`transition_prob_histogram` is the pure-array core (per-molecule paths → a
:class:`TransitionProbHistogram`); :func:`population_transition_prob_histogram` is the
``.tether`` store entry point (fresh + curation filters, model-range validation).

References
----------
[McKinney2006] McKinney, Joo & Ha. "Analysis of single-molecule FRET trajectories
    using hidden Markov modeling." Biophysical Journal (2006) — HMM inference yields
    per-trajectory state-to-state transition probabilities.
[vandeMeent2014] van de Meent, Bronson, Wiggins & Gonzalez. "Empirical Bayes methods
    enable advanced population-level analyses of single-molecule FRET experiments."
    Biophysical Journal (2014) — per-trace transition parameters vary widely within a
    population, motivating a distribution (histogram/KDE) rather than one number.
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
    "DEFAULT_TPROB_KDE_BANDWIDTH",
    "DEFAULT_TPROB_KDE_POINTS",
    "DEFAULT_TPROB_NBINS",
    "DEFAULT_TPROB_RANGE",
    "TransitionProbHistogram",
    "empirical_transition_probability",
    "population_transition_prob_histogram",
    "transition_prob_histogram",
]

#: tMAVEN ``tm_hist.py`` ``prob_nbins``: bins across the probability axis. A rendering
#: default (Appendix C B3), **not** a PRD §11.2 tunable.
DEFAULT_TPROB_NBINS = 25

#: tMAVEN ``tm_hist.py`` ``[prob_min, prob_max]``: the histogram spans slightly past
#: ``[0, 1]`` so probabilities exactly at the edges are not clipped. A rendering
#: default, **not** a PRD §11.2 tunable.
DEFAULT_TPROB_RANGE: tuple[float, float] = (-0.05, 1.05)

#: tMAVEN ``tm_hist.py`` ``kde_bandwidth``: the Gaussian-KDE bandwidth factor. A
#: rendering default, **not** a PRD §11.2 tunable.
DEFAULT_TPROB_KDE_BANDWIDTH = 0.25

#: tMAVEN ``tm_hist.py`` evaluates the KDE on ``linspace(0, 1, 100)``. A rendering
#: default, **not** a PRD §11.2 tunable.
DEFAULT_TPROB_KDE_POINTS = 100


@dataclass(frozen=True)
class TransitionProbHistogram:
    """A binned population of per-molecule transition probabilities (self-describing).

    ``counts[i]`` is the (density-normalized unless ``not density``) histogram height
    over probability bin ``i`` for the ordered state pair (:attr:`init_state` →
    :attr:`final_state`). :attr:`probabilities` keeps the raw per-molecule values so
    the count and any statistic are reproducible (NFR-REPRO), and
    :attr:`n_molecules` (= ``probabilities.size``) is the number of molecules that
    ever occupy ``init_state`` — tMAVEN's ``N``. :attr:`kde_x` / :attr:`kde_y` carry
    the optional Gaussian-KDE curve, both ``None`` when a KDE was not requested or
    could not be formed (fewer than two probabilities, or all identical — a singular
    covariance); the render then shows the histogram alone, never a fabricated curve.
    """

    counts: np.ndarray  # (n_bins,) float64 — density (or raw counts) over the prob axis
    edges: np.ndarray  # (n_bins + 1,) float64 — probability bin edges
    prob_range: tuple[float, float]
    init_state: int
    final_state: int
    density: bool
    probabilities: np.ndarray  # (n_molecules,) float64 — per-molecule P(init -> fin)
    n_molecules: int  # molecules occupying init_state with an observed successor
    kde_x: np.ndarray | None  # (kde_points,) float64 — KDE abscissa, or None
    kde_y: np.ndarray | None  # (kde_points,) float64 — KDE density, or None

    @property
    def n_bins(self) -> int:
        """Number of probability bins."""
        return int(self.counts.shape[0])

    @property
    def centers(self) -> np.ndarray:
        """Probability bin centres."""
        e = self.edges
        return 0.5 * (e[:-1] + e[1:])


def empirical_transition_probability(
    state_path: np.ndarray, init_state: int, final_state: int
) -> float | None:
    """Per-molecule one-step transition probability ``P(init -> fin)`` from a Viterbi path.

    Counts consecutive frame pairs ``(state[t], state[t + 1])``: the denominator is the
    number of frames in ``init_state`` that have an **observed** successor (successor
    not :data:`~tether.idealize.NO_STATE`), the numerator the subset whose successor is
    ``final_state``. Returns ``None`` when ``init_state`` is never occupied with an
    observed successor (an undefined probability — never ``0/0``). Self-pairs
    (``init_state == final_state``) are counted like any row-normalized transition
    matrix diagonal.
    """
    from tether.idealize import NO_STATE

    v = np.asarray(state_path, dtype=np.int64).ravel()
    if v.size < 2:
        return None
    src = v[:-1]
    dst = v[1:]
    from_init = (src == int(init_state)) & (dst != NO_STATE)
    denom = int(np.count_nonzero(from_init))
    if denom == 0:
        return None
    numer = int(np.count_nonzero(from_init & (dst == int(final_state))))
    return numer / denom


def _kde_curve(
    probabilities: np.ndarray, bandwidth: float, points: int
) -> tuple[np.ndarray, np.ndarray] | None:
    """Gaussian-KDE of the per-molecule probabilities on ``linspace(0, 1, points)``.

    Returns ``None`` (never raises) when the estimate is undefined: fewer than two
    values, or a singular covariance (all probabilities identical) — tMAVEN's
    ``try/except`` around ``gaussian_kde``, made explicit.
    """
    if probabilities.size < 2:
        return None
    try:
        from scipy.stats import gaussian_kde

        gkde = gaussian_kde(probabilities, bw_method=float(bandwidth))
        x = np.linspace(0.0, 1.0, int(points))
        y = np.asarray(gkde.evaluate(x), dtype=np.float64)
    except (np.linalg.LinAlgError, ValueError):
        # Singular covariance (identical values) or a degenerate dataset: no curve,
        # not a crash and not a fabricated line.
        return None
    return x, y


def transition_prob_histogram(
    state_chunks: Iterable[np.ndarray],
    *,
    init_state: int,
    final_state: int,
    prob_bins: int = DEFAULT_TPROB_NBINS,
    prob_range: tuple[float, float] = DEFAULT_TPROB_RANGE,
    density: bool = True,
    kde: bool = True,
    kde_bandwidth: float = DEFAULT_TPROB_KDE_BANDWIDTH,
    kde_points: int = DEFAULT_TPROB_KDE_POINTS,
) -> TransitionProbHistogram:
    """Bin per-molecule ``P(init -> fin)`` into a :class:`TransitionProbHistogram`.

    Each element of ``state_chunks`` is one molecule's integer Viterbi path. Its
    :func:`empirical_transition_probability` is computed; molecules with no defined
    probability (never in ``init_state``) are dropped, and the rest are pooled and
    histogrammed over ``prob_range``. When ``kde`` and at least two probabilities are
    available, a Gaussian-KDE curve is attached (``None`` otherwise).

    Parameters
    ----------
    state_chunks
        Iterable of 1-D per-molecule integer state paths.
    init_state, final_state
        The ordered state pair (``>= 0``); the probability is of a one-step
        ``init_state -> final_state`` transition.
    prob_bins
        Bins across the probability axis (``>= 1``).
    prob_range
        ``(lo, hi)`` probability range for the histogram (``hi > lo``).
    density
        If ``True``, normalize like :func:`numpy.histogram` ``density`` (integral over
        the range is 1); else raw counts.
    kde
        Attach a Gaussian-KDE overlay curve when computable.
    kde_bandwidth
        Gaussian-KDE bandwidth factor (``> 0``).
    kde_points
        Points at which the KDE is evaluated on ``[0, 1]`` (``>= 2``).

    Returns
    -------
    TransitionProbHistogram
    """
    if int(init_state) < 0:
        raise ValueError(f"init_state must be >= 0, got {init_state}")
    if int(final_state) < 0:
        raise ValueError(f"final_state must be >= 0, got {final_state}")
    if int(prob_bins) < 1:
        raise ValueError(f"prob_bins must be >= 1, got {prob_bins}")
    lo, hi = float(prob_range[0]), float(prob_range[1])
    if not hi > lo:
        raise ValueError(f"prob_range must have hi > lo, got {prob_range!r}")
    if not float(kde_bandwidth) > 0:
        raise ValueError(f"kde_bandwidth must be > 0, got {kde_bandwidth}")
    if int(kde_points) < 2:
        raise ValueError(f"kde_points must be >= 2, got {kde_points}")

    prob_bins = int(prob_bins)
    init_state = int(init_state)
    final_state = int(final_state)

    values: list[float] = []
    for chunk in state_chunks:
        if np.ndim(chunk) == 0:
            # A scalar element means a flat 1-D array was passed instead of an iterable
            # of per-molecule paths; iterating it yields single frames. Fail fast on the
            # misuse rather than silently return an empty histogram.
            raise ValueError(
                "state_chunks must be an iterable of 1-D per-molecule state paths, got a "
                "scalar element — wrap a single molecule as [v], not v"
            )
        p = empirical_transition_probability(chunk, init_state, final_state)
        if p is not None:
            values.append(p)

    probabilities = np.asarray(values, dtype=np.float64)
    edges = np.linspace(lo, hi, prob_bins + 1)
    counts = np.histogram(probabilities, bins=prob_bins, range=(lo, hi))[0].astype("float64")
    if density and counts.sum() > 0:
        # numpy's density path divides by the *in-range* count; when no probability
        # falls inside ``prob_range`` (an empty population, or a narrowed range that
        # excludes every value) that is a divide-by-zero -> all-NaN. ``counts`` above is
        # the raw in-range mass, so gate on it (not ``probabilities.size``) — mirroring
        # tdp.py — and keep the all-zeros histogram otherwise (the "never NaN" invariant
        # the render needs).
        counts = np.histogram(probabilities, bins=prob_bins, range=(lo, hi), density=True)[
            0
        ].astype("float64")

    curve = _kde_curve(probabilities, kde_bandwidth, kde_points) if kde else None
    kde_x = curve[0] if curve is not None else None
    kde_y = curve[1] if curve is not None else None

    return TransitionProbHistogram(
        counts=np.ascontiguousarray(counts, dtype="float64"),
        edges=edges,
        prob_range=(lo, hi),
        init_state=init_state,
        final_state=final_state,
        density=bool(density),
        probabilities=probabilities,
        n_molecules=int(probabilities.size),
        kde_x=kde_x,
        kde_y=kde_y,
    )


def population_transition_prob_histogram(
    project: ProjectRef,
    model_name: str,
    init_state: int,
    final_state: int,
    *,
    molecule_keys: list[str] | None = None,
    prob_bins: int = DEFAULT_TPROB_NBINS,
    prob_range: tuple[float, float] = DEFAULT_TPROB_RANGE,
    density: bool = True,
    kde: bool = True,
    kde_bandwidth: float = DEFAULT_TPROB_KDE_BANDWIDTH,
    kde_points: int = DEFAULT_TPROB_KDE_POINTS,
    include_rejected: bool = False,
    include_stale: bool = False,
) -> TransitionProbHistogram:
    """Transition-prob histogram from a ``.tether`` store (§10 B3; Appendix C B3; PRD §7.7).

    Reads each accepted, **fresh** molecule's Viterbi state path from
    ``/idealization/{model_name}`` and feeds them to :func:`transition_prob_histogram`
    for the ordered pair (``init_state`` → ``final_state``). STALE molecules are
    excluded (PRD §5.1) unless ``include_stale``; rejected molecules are excluded
    unless ``include_rejected`` (§7.5) — the B1 TDP selection contract
    (:func:`tether.analysis.tdp.population_transition_density`).

    Parameters
    ----------
    project
        A :class:`~tether.project.core.Project` or a path to a ``.tether`` store.
    model_name
        Which ``/idealization/{model_name}`` supplies the state paths + state count.
    init_state, final_state
        The ordered state pair; each must be a valid state index of the model
        (``0 <= s < nstates``).
    molecule_keys
        Restrict to these ``molecule_key`` values (``None`` = all), intersected with
        the fresh + curation filters.
    prob_bins, prob_range, density, kde, kde_bandwidth, kde_points
        Passed through to :func:`transition_prob_histogram`.
    include_rejected
        Keep rejected molecules (default excludes them, §7.5).
    include_stale
        Keep STALE idealizations (default excludes them, PRD §5.1).

    Returns
    -------
    TransitionProbHistogram

    Raises
    ------
    KeyError
        No ``/idealization/{model_name}`` in the store.
    ValueError
        ``init_state`` or ``final_state`` is not a valid state index of the model.
    """
    from tether.analysis._store import windowed_states
    from tether.project.idealize import live_molecule_keys, read_idealization

    stored = read_idealization(project, model_name)
    nstates = int(stored.nstates)
    for label, state in (("init_state", int(init_state)), ("final_state", int(final_state))):
        if not 0 <= state < nstates:
            raise ValueError(
                f"{label}={state} out of range for model {model_name!r} "
                f"with {nstates} states (valid 0..{nstates - 1})"
            )

    keys = molecule_keys
    if not include_stale:
        live = live_molecule_keys(project, model_name)
        if molecule_keys is None:
            keys = live
        else:
            live_set = set(live)
            keys = [k for k in molecule_keys if k in live_set]

    states = windowed_states(project, model_name, keys, include_rejected)
    return transition_prob_histogram(
        states,
        init_state=int(init_state),
        final_state=int(final_state),
        prob_bins=prob_bins,
        prob_range=prob_range,
        density=density,
        kde=kde,
        kde_bandwidth=kde_bandwidth,
        kde_points=kde_points,
    )
