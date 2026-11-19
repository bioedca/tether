# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real Transition Density Plot (TDP) — M6 B1, FR-ANALYZE (PRD §7.7, Appendix C B1).

A TDP [McKinney2006] is a 2-D histogram of the **initial vs final idealized FRET
level** over every state-change frame of a population, the standard way to read off
which conformational transitions a system makes and how often. Tether builds the
*real* TDP from a persisted ``/idealization/{model}`` (not raw signal): each state
change contributes one ``(initial_E, final_E)`` point taken ``nskip`` frames apart,
and the pooled points are binned into a square ``E × E`` grid the GUI and the plot
export ([`tether.analysis.plot_export.render_transition_density`][]) render
log-normalized [McKinney2006][Hadzic2018].

Two invariants distinguish it from a naive 2-D histogram:

* **Fresh idealizations only.** Molecules whose inputs changed since the fit are
  STALE and are excluded from the TDP (PRD §5.1) via
  [`tether.project.idealize.live_molecule_keys`][], so a state path is never mixed
  with signal it was not fit on. Pass ``include_stale=True`` to override.
* **State-change frames only.** A point is emitted only where the idealized level
  changes between adjacent frames (``|v[t+1] - v[t]| > 0``) — the neighbour-pair,
  jump-restricted extraction of tMAVEN's ``data_tdp.py`` (``nskip = 2``), ported here
  so the plot reproduces its tMAVEN counterpart (the §9 M6 parity clause).

[`transition_density`][] is the pure-array core (an iterable of per-molecule
idealized-level arrays → a [`TransitionDensityPlot`][]); [`population_transition_density`][]
is the ``.tether`` store entry point that reconstructs those levels from a model's
Viterbi state paths and the fresh/curation filters.

[`smooth_transition_density`][] is the array a published TDP figure shows: the counts
Gaussian-filtered, spline-interpolated onto a fine grid and normalized to their maximum,
the three steps of the ``checkTDP.py`` that Campos-Chavez et al. 2024 published with their
Source Data (doi:10.1038/s41467-024-50885-x) and of tMAVEN's ``data_tdp.py``
``gen_histogram``. It is a **view** of a [`TransitionDensityPlot`][]: the counts stay
the stored result (NFR-REPRO), and no reported number is read off the smoothed array.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Iterable
    from os import PathLike

    from tether.analysis.selection import Selection
    from tether.project.core import Project

    ProjectRef = Project | str | PathLike[str]

__all__ = [
    "DEFAULT_TDP_INTERP_RES",
    "DEFAULT_TDP_NORMALIZE",
    "DEFAULT_TDP_NSKIP",
    "DEFAULT_TDP_SIGNAL_BINS",
    "DEFAULT_TDP_SIGNAL_RANGE",
    "DEFAULT_TDP_SMOOTH",
    "SmoothedTransitionDensity",
    "TransitionDensityPlot",
    "population_transition_density",
    "smooth_transition_density",
    "transition_density",
]

#: tMAVEN ``data_tdp.py`` neighbour-pair gap: the initial/final points of a transition
#: are read ``nskip`` frames apart so the transition frame itself is skipped. A
#: rendering default (Appendix C B1), **not** a PRD §11.2 tunable.
DEFAULT_TDP_NSKIP = 2

#: tMAVEN ``data_tdp.py`` ``signal_nbins``: the square grid is ``signal_bins`` bins on
#: each axis. A rendering default, **not** a PRD §11.2 tunable.
DEFAULT_TDP_SIGNAL_BINS = 101

#: tMAVEN ``data_tdp.py`` ``[signal_min, signal_max]`` for smFRET. A rendering default,
#: **not** a PRD §11.2 tunable.
DEFAULT_TDP_SIGNAL_RANGE: tuple[float, float] = (-0.25, 1.25)

#: Gaussian-filter widths, in bins, along the initial and final axes of the smoothed TDP:
#: ``checkTDP.py``'s ``hist_smoothx`` / ``hist_smoothy`` (tMAVEN ``data_tdp.py`` uses the
#: same values). ``0`` on an axis leaves that axis unfiltered. A rendering default, **not**
#: a PRD §11.2 tunable: it changes only the drawn view, never the counts.
DEFAULT_TDP_SMOOTH: tuple[float, float] = (1.0, 1.0)

#: Points per axis of the bicubic-spline grid of the smoothed TDP: ``checkTDP.py``'s
#: ``hist_interp_res``. ``0`` skips the interpolation and keeps the bin-centre grid
#: (tMAVEN ``data_tdp.py``'s own default). A rendering default, **not** a PRD §11.2 tunable.
DEFAULT_TDP_INTERP_RES = 800

#: Whether the smoothed TDP is divided by its maximum, so its peak is 1: ``checkTDP.py``'s
#: and tMAVEN ``data_tdp.py``'s ``hist_normalize``. A rendering default, **not** a PRD
#: §11.2 tunable.
DEFAULT_TDP_NORMALIZE = True


@dataclass(frozen=True)
class TransitionDensityPlot:
    """A binned initial-vs-final idealized-FRET transition density (self-describing).

    ``counts[i, j]`` is the number of state changes whose **initial** level fell in
    signal bin ``i`` and **final** level in bin ``j`` — tMAVEN's ``data_tdp.py`` ``z``
    (``histogram2d(d1, d2)``), unsmoothed and in raw counts unless ``density``. Both
    axes share [`signal_edges`][] (a square ``E × E`` grid), so the diagonal is the
    no-net-change locus and off-diagonal mass marks the transitions. The GUI renders
    it log-normalized [McKinney2006]; smoothing/log are display concerns, so the
    stored array stays the exact histogram (NFR-REPRO).
    """

    counts: np.ndarray  # (signal_bins, signal_bins) float64 — [initial, final] density
    signal_edges: np.ndarray  # (signal_bins + 1,) float64 — shared by both axes
    signal_range: tuple[float, float]
    nskip: int  # neighbour-pair gap frames (transition read initial @ t, final @ t + nskip)
    density: bool  # numpy histogram2d density (integrates to 1) vs raw counts
    n_transitions: int  # finite (initial, final) points pooled — tMAVEN's ``n = d1.size``
    n_molecules: int  # molecules contributing >= 1 transition (not tMAVEN's all-traces N)

    @property
    def signal_centers(self) -> np.ndarray:
        """Bin-centre signal values shared by the initial and final axes."""
        e = self.signal_edges
        return 0.5 * (e[:-1] + e[1:])

    @property
    def initial_centers(self) -> np.ndarray:
        """Bin centres of the initial-E (row) axis."""
        return self.signal_centers

    @property
    def final_centers(self) -> np.ndarray:
        """Bin centres of the final-E (column) axis."""
        return self.signal_centers

    @property
    def signal_bins(self) -> int:
        """Number of bins on each axis."""
        return int(self.counts.shape[1])


def transition_density(
    idealized_chunks: Iterable[np.ndarray],
    *,
    nskip: int = DEFAULT_TDP_NSKIP,
    signal_bins: int = DEFAULT_TDP_SIGNAL_BINS,
    signal_range: tuple[float, float] = DEFAULT_TDP_SIGNAL_RANGE,
    density: bool = False,
) -> TransitionDensityPlot:
    """Bin per-molecule idealized-level traces into an initial-vs-final TDP.

    Each element of ``idealized_chunks`` is one molecule's per-frame **idealized FRET
    level** (``NaN`` outside its window / at any interior gap). For each molecule the
    neighbour pairs ``(v[t], v[t + nskip])`` are formed and kept only where the level
    changes between adjacent frames (``|v[t+1] - v[t]| > 0``) — tMAVEN ``data_tdp.py``
    ``get_neighbor_data``'s jump restriction — then pooled and 2-D-histogrammed over
    ``signal_range`` on both axes. Non-finite pairs are dropped (a ``NaN`` gap is never
    a transition). Out-of-``signal_range`` transitions still count toward
    ``n_transitions`` / ``n_molecules`` but fall outside the histogram (tMAVEN
    semantics).

    Parameters
    ----------
    idealized_chunks
        Iterable of 1-D per-molecule idealized-level arrays.
    nskip
        Frames between the initial and final point of a transition (>= 1).
    signal_bins
        Bins per axis (>= 1).
    signal_range
        ``(lo, hi)`` E range for both axes (``hi > lo``).
    density
        If ``True``, normalize like [`numpy.histogram2d`][] ``density`` (the 2-D
        integral is 1); else raw counts.

    Returns
    -------
    TransitionDensityPlot
    """
    if int(nskip) < 1:
        raise ValueError(f"nskip must be >= 1, got {nskip}")
    if int(signal_bins) < 1:
        raise ValueError(f"signal_bins must be >= 1, got {signal_bins}")
    lo, hi = float(signal_range[0]), float(signal_range[1])
    if not hi > lo:
        raise ValueError(f"signal_range must have hi > lo, got {signal_range!r}")

    nskip = int(nskip)
    signal_bins = int(signal_bins)
    edges = np.linspace(lo, hi, signal_bins + 1)

    initials: list[np.ndarray] = []
    finals: list[np.ndarray] = []
    n_molecules = 0
    for chunk in idealized_chunks:
        if np.ndim(chunk) == 0:
            # A scalar element means a flat 1-D array was passed instead of an iterable
            # of per-molecule arrays (``transition_density(v)`` vs ``[v]``) — iterating
            # it would silently yield an all-zero TDP. Fail fast on this public-API misuse.
            raise ValueError(
                "idealized_chunks must be an iterable of 1-D per-molecule arrays, got a "
                "scalar element — wrap a single molecule as [v], not v"
            )
        v = np.asarray(chunk, dtype="float64").ravel()
        if v.size <= nskip:
            continue
        d1 = v[:-nskip]
        d2 = v[nskip:]
        jump = np.abs(v[1:] - v[:-1]) > 0.0  # state-change frames (NaN -> False)
        if nskip > 1:
            jump = jump[: -(nskip - 1)]  # align the (T-1) jump mask to the (T-nskip) pairs
        d1 = d1[jump]
        d2 = d2[jump]
        keep = np.isfinite(d1) & np.isfinite(d2)
        d1 = d1[keep]
        d2 = d2[keep]
        if d1.size:
            n_molecules += 1
            initials.append(d1)
            finals.append(d2)

    if initials:
        cat_initial = np.concatenate(initials)
        cat_final = np.concatenate(finals)
    else:
        cat_initial = np.empty(0, dtype="float64")
        cat_final = np.empty(0, dtype="float64")

    counts, _, _ = np.histogram2d(
        cat_initial,
        cat_final,
        bins=[signal_bins, signal_bins],
        range=[[lo, hi], [lo, hi]],
    )
    if density and counts.sum() > 0:
        # numpy's density path divides by the in-range count; when no transition falls
        # in ``signal_range`` (every molecule stale, or all transitions out of range)
        # that is a divide-by-zero -> all-NaN. Normalize only when there is mass, so the
        # empty case stays all-zeros (the "never NaN" invariant this plot's render needs).
        counts, _, _ = np.histogram2d(
            cat_initial,
            cat_final,
            bins=[signal_bins, signal_bins],
            range=[[lo, hi], [lo, hi]],
            density=True,
        )
    return TransitionDensityPlot(
        counts=np.ascontiguousarray(counts, dtype="float64"),
        signal_edges=edges,
        signal_range=(lo, hi),
        nskip=nskip,
        density=bool(density),
        n_transitions=int(cat_initial.size),
        n_molecules=int(n_molecules),
    )


@dataclass(frozen=True)
class SmoothedTransitionDensity:
    """The drawn view of a [`TransitionDensityPlot`][]: smoothed, interpolated, normalized.

    ``values[i, j]`` is the view at initial level ``initial[i]`` and final level
    ``final[j]``, the same ``[initial, final]`` orientation as
    [`TransitionDensityPlot.counts`][tether.analysis.tdp.TransitionDensityPlot]. Unlike
    the counts it is sampled at **points**, not bins: the interpolation grid spans
    ``signal_range`` end to end, as ``checkTDP.py``'s ``x_interp`` does. ``source`` is the
    unchanged counts the view was drawn from.
    """

    values: np.ndarray  # (n, n) float64 — [initial, final], >= 0; peak 1 when normalized
    grid: np.ndarray  # (n,) float64 — shared by both axes
    smooth: tuple[float, float]  # Gaussian-filter sigma (bins) along initial, final
    interp_res: int  # spline grid points per axis; 0 = bin centres, not interpolated
    normalized: bool  # values divided by their maximum (when that maximum is > 0)
    source: TransitionDensityPlot  # the stored counts this is a view of

    @property
    def initial(self) -> np.ndarray:
        """Grid points of the initial-E (row) axis."""
        return self.grid

    @property
    def final(self) -> np.ndarray:
        """Grid points of the final-E (column) axis."""
        return self.grid

    @property
    def signal_range(self) -> tuple[float, float]:
        """The ``(lo, hi)`` E range of both axes, from the source counts."""
        return self.source.signal_range


def smooth_transition_density(
    tdp: TransitionDensityPlot,
    *,
    smooth: tuple[float, float] = DEFAULT_TDP_SMOOTH,
    interp_res: int = DEFAULT_TDP_INTERP_RES,
    normalize: bool = DEFAULT_TDP_NORMALIZE,
) -> SmoothedTransitionDensity:
    """Compute the array a published TDP figure draws, before its colour map.

    Three steps on ``tdp.counts``, in the order and with the calls of the ``checkTDP.py``
    published with Campos-Chavez et al. 2024 (doi:10.1038/s41467-024-50885-x):

    1. [`scipy.ndimage.gaussian_filter`][] with ``sigma = smooth`` (in bins, its default
       ``reflect`` boundary);
    2. a [`scipy.interpolate.RectBivariateSpline`][] (bicubic, interpolating) through the
       bin centres, evaluated on ``interp_res`` evenly spaced points from ``signal_range[0]``
       to ``signal_range[1]`` on each axis, with negative values set to 0 — skipped when
       ``interp_res`` is 0, which keeps the bin-centre grid;
    3. with ``normalize``, a division by the maximum, so the peak is 1. An all-zero view
       (no transition in range) is left at zero rather than divided into ``NaN``.

    The spline knots are the **bin centres**, as in ``checkTDP.py``. tMAVEN's
    ``data_tdp.py`` ``gen_histogram`` runs the same three steps, but it places its knots on
    ``linspace(signal_min, signal_max, signal_nbins)`` (one point per bin, from edge to
    edge) and does not interpolate by default (``hist_interp_res = 0``), so its view of the
    same counts is offset by up to half a bin. Tether follows the published script because
    its grid is the one Tether's counts are binned on.

    The result is a view: ``tdp`` is not changed, and the counts remain the stored result
    (NFR-REPRO). Nothing Tether reports is read off this array.

    Parameters
    ----------
    tdp
        The binned transition density ([`transition_density`][] or
        [`population_transition_density`][]).
    smooth
        Gaussian-filter widths in bins along the initial and final axes (each >= 0).
    interp_res
        Spline grid points per axis (0, or >= 2).
    normalize
        Divide by the maximum.

    Returns
    -------
    SmoothedTransitionDensity
    """
    from scipy.interpolate import RectBivariateSpline
    from scipy.ndimage import gaussian_filter

    sx, sy = float(smooth[0]), float(smooth[1])
    if not (np.isfinite(sx) and np.isfinite(sy)) or sx < 0 or sy < 0:
        raise ValueError(f"smooth must be two finite widths >= 0, got {smooth!r}")
    interp_res = int(interp_res)
    if interp_res < 0 or interp_res == 1:
        raise ValueError(f"interp_res must be 0 or >= 2, got {interp_res}")
    centers = tdp.signal_centers
    if interp_res and centers.size < 4:
        # A bicubic spline needs at least four knots per axis (kx = ky = 3).
        raise ValueError(
            f"a bicubic spline needs signal_bins >= 4, got {centers.size}; pass interp_res=0"
        )

    z = gaussian_filter(np.asarray(tdp.counts, dtype="float64"), (sx, sy))
    grid = centers
    if interp_res:
        lo, hi = tdp.signal_range
        spline = RectBivariateSpline(centers, centers, z)
        grid = np.linspace(lo, hi, interp_res)
        z = spline(grid, grid)
        z[z < 0] = 0.0
    if normalize:
        peak = float(z.max()) if z.size else 0.0
        if peak > 0:
            z = z / peak
    return SmoothedTransitionDensity(
        values=np.ascontiguousarray(z, dtype="float64"),
        grid=np.asarray(grid, dtype="float64"),
        smooth=(sx, sy),
        interp_res=interp_res,
        normalized=bool(normalize),
        source=tdp,
    )


def _levels_from_states(state_path: np.ndarray, means: np.ndarray, no_state: int) -> np.ndarray:
    """Reconstruct a molecule's per-frame idealized level from its Viterbi state path.

    ``v[t] = means[state_path[t]]`` where the frame carries a state, ``NaN`` at
    [`NO_STATE`][tether.idealize.driver.NO_STATE] frames — the analogue of tMAVEN's idealized data
    row. Reconstructing from ``means`` (never a possibly-absent ``/idealization``
    ``idealized`` dataset) keeps a single, always-available source of truth for the
    level.
    """
    state = np.asarray(state_path, dtype=np.int64)
    off = state == no_state
    safe = np.where(off, 0, state)
    levels = means[safe]
    return np.where(off, np.nan, levels)


def population_transition_density(
    project: ProjectRef,
    model_name: str,
    *,
    molecule_keys: Selection = None,
    nskip: int = DEFAULT_TDP_NSKIP,
    signal_bins: int = DEFAULT_TDP_SIGNAL_BINS,
    signal_range: tuple[float, float] = DEFAULT_TDP_SIGNAL_RANGE,
    density: bool = False,
    include_rejected: bool = False,
    include_stale: bool = False,
) -> TransitionDensityPlot:
    """Real TDP from a ``.tether`` store (§10 B1; Appendix C B1; PRD §7.7).

    Builds each accepted, **fresh** molecule's idealized-level trace from the
    ``/idealization/{model_name}`` Viterbi state paths (levels =
    [`means`][tether.project.idealize.StoredIdealization.means]) and feeds them to
    [`transition_density`][]. STALE molecules are excluded (PRD §5.1) unless
    ``include_stale``; rejected molecules are excluded unless ``include_rejected``
    (§7.5).

    Parameters
    ----------
    project
        A [`Project`][tether.project.core.Project] or a path to a ``.tether`` store.
    model_name
        Which ``/idealization/{model_name}`` supplies the state paths + means.
    molecule_keys
        Restrict to these ``molecule_key`` values (``None`` = all), intersected with
        the fresh + curation filters.
    nskip, signal_bins, signal_range, density
        Passed through to [`transition_density`][].
    include_rejected
        Keep rejected molecules (default excludes them, §7.5).
    include_stale
        Keep STALE idealizations (default excludes them, PRD §5.1).

    Returns
    -------
    TransitionDensityPlot

    Raises
    ------
    KeyError
        No ``/idealization/{model_name}`` in the store.
    """
    from tether.analysis._store import fresh_selection, windowed_states
    from tether.idealize import NO_STATE
    from tether.project.idealize import read_idealization

    stored = read_idealization(project, model_name)
    means = np.asarray(stored.means, dtype="float64")

    keys = fresh_selection(project, model_name, molecule_keys, include_stale)

    states = windowed_states(project, model_name, keys, include_rejected)
    idealized_chunks = [_levels_from_states(state, means, NO_STATE) for state in states]
    return transition_density(
        idealized_chunks,
        nskip=nskip,
        signal_bins=signal_bins,
        signal_range=signal_range,
        density=density,
    )
