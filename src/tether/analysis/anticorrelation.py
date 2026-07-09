# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Anticorrelation-event finder — M6 PR-6, FR-ANALYZE (PRD §7.7).

A **model-free lens that localizes anticorrelation events in time**: a sliding
window is swept along one molecule's donor/acceptor intensity trace, the
donor–acceptor cross-correlation is measured in each window, and contiguous runs
of windows that are genuinely anti-phase are merged into events. During FRET a
structural fluctuation raises one channel as it lowers the other — donor and
acceptor signals are *anticorrelated* [Felekyan2012][Torres2007] — so a genuine
conformational/kinetic event shows up as a window where the two channels move in
anti-phase. Where the population
:func:`~tether.analysis.crosscorr.cross_correlation` scores a *whole* trace with a
single lag-1 magnitude, this finder answers the complementary question **"and
*when* within the trace does the anticorrelation happen?"** — the pre-idealization
counterpart to the HMM's transition list, available before any model is committed.

Two statistics of each window's cross-correlation gate an event, each carrying the
part of the signal it is reliable for:

* **Direction — the lag-0 (same-frame) sign.** :attr:`CrossCorrelation.lag0` is the
  genuine Pearson coefficient of the two channels (:mod:`tether.analysis.crosscorr`),
  so its sign is the physically meaningful same-frame relationship: ``lag0 < 0`` is
  anti-phase, ``> 0`` in-phase. This is the *only* reliable anti-phase indicator. The
  signed lag-1 value is **not** used for direction: the cross-correlation is
  *biased*-normalized (every lag divided by the shared lag-0 factor ``N·σ_d·σ_a``,
  crosscorr §"Implementation"), so ``r[+1]`` is not the lag-1 Pearson coefficient and
  its sign couples to the donor autocorrelation sign — a fast *in-phase* oscillation
  (period ≲ 4 frames) has a strongly **negative** ``r[+1]`` and would be misflagged if
  the sign of ``r[+1]`` drove detection.
* **Strength — the lag-1 magnitude.** :attr:`CrossCorrelation.lag1_magnitude`
  (``|r[+1]|``, the quantity the cross-correlation exposes, PRD §7.7) measures the
  *temporal structure* of the fluctuation. A genuine slow anti-phase change persists
  frame-to-frame, so its lag-1 magnitude is large; same-frame **shot-noise**
  anticorrelation (photon partitioning on a near-constant total gives ``lag0 ≈ -1``
  with no real dynamics) is white, so its lag-1 magnitude decays to ~0. Requiring a
  minimum lag-1 magnitude therefore keeps genuine conformational dynamics and rejects
  shot noise [Chung2010] — the cross-correlation *function*, not the single lag-0
  value, is what distinguishes dynamics from photophysics.

A window is *flagged* when it is both anti-phase and temporally structured —
``lag0 < 0`` **and** ``lag1_magnitude >= min_magnitude`` (a constant window is an
undefined correlation → ``NaN`` → never flagged, never fabricated as 0). Maximal runs
of consecutive flagged windows form one :class:`AnticorrelationEvent` spanning every
frame those windows touch, with the run's most anti-phase (most-negative ``lag0``)
window as its peak. The thresholds (``window``, ``step``, ``min_magnitude``,
``min_windows``) are **QC-rendering defaults**, not PRD §11.2 science tunables: the
events are a visualization/curation aid — no downstream corrected-E, idealization, or
exported factor depends on them (the same classification as the raw FRET cloud,
:mod:`tether.analysis.cloud`). The trustworthy state/transition count of record remains
the HMM/vbFRET model view (the real TDP) [McKinney2006]; this finder orients the eye
first.

:func:`find_anticorrelation_events` is the pure-array core (one trace ->
:class:`AnticorrelationScan`); :func:`population_anticorrelation_events` is the
``.tether`` store entry point (§7.5 curation filter, chosen intensity channels,
analysis window) returning one scan per accepted molecule.

References
----------
[Felekyan2012] Felekyan S, Kalinin S, Sanabria H, Valeri A, Seidel CAM. "Analyzing
    Förster resonance energy transfer with fluctuation algorithms." Methods in
    Enzymology 519:39-85 (2012) — structural fluctuations produce *anticorrelated*
    donor and acceptor signals, analyzed via their correlation functions to
    characterize the underlying conformational dynamics.
[Torres2007] Torres T, Levitus M. "Measuring conformational dynamics: a new
    FCS-FRET approach." J. Phys. Chem. B 111(25):7392-7400 (2007) — the auto- and
    cross-correlation of donor/acceptor intensities carries the conformational
    kinetics, independent of the diffusion contribution.
[Chung2010] Chung HS, Louis JM, Eaton WA. "Distinguishing between protein dynamics
    and dye photophysics in single-molecule FRET experiments." Biophys. J.
    98(4):696-706 (2010) — the donor–acceptor cross-correlation *function* separates
    genuine conformational dynamics from same-frame dye photophysics/shot noise, the
    basis for gating on the temporally-structured (lag-1) magnitude rather than the
    instantaneous value alone.
[McKinney2006] McKinney SA, Joo C, Ha T. "Analysis of single-molecule FRET
    trajectories using hidden Markov modeling." Biophys. J. 91(5):1941-1951 (2006) —
    the rigorous state/transition count is an HMM + transition-density-plot property;
    the model-free anticorrelation events precede, not replace, it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from tether.analysis.crosscorr import cross_correlation

if TYPE_CHECKING:  # pragma: no cover - typing only
    from os import PathLike

    from tether.project.core import Project

    ProjectRef = Project | str | PathLike[str]

__all__ = [
    "DEFAULT_ANTICORR_MIN_MAGNITUDE",
    "DEFAULT_ANTICORR_MIN_WINDOWS",
    "DEFAULT_ANTICORR_STEP",
    "DEFAULT_ANTICORR_WINDOW",
    "AnticorrelationEvent",
    "AnticorrelationScan",
    "MoleculeAnticorrelation",
    "PopulationAnticorrelation",
    "find_anticorrelation_events",
    "population_anticorrelation_events",
]

#: Sliding-window length (frames) over which each cross-correlation is measured. Long
#: enough for a stable Pearson estimate of anti-phase dynamics, short enough to localize
#: an event. A QC-rendering default (like the cloud's grid resolution), **not** a PRD
#: §11.2 science tunable — no downstream result depends on the events.
DEFAULT_ANTICORR_WINDOW = 15

#: Stride (frames) between successive windows. ``1`` scans densely (best localization);
#: a larger step trades resolution for speed. A QC-rendering default, **not** §11.2.
DEFAULT_ANTICORR_STEP = 1

#: Minimum lag-1 correlation **magnitude** (temporal-structure strength) for a window to
#: flag, once it is anti-phase (``lag0 < 0``). ``0.5`` is the classical "clear
#: correlation" cut; it rejects white same-frame shot-noise anticorrelation (lag-1
#: magnitude ~0). A QC-rendering default, **not** §11.2.
DEFAULT_ANTICORR_MIN_MAGNITUDE = 0.5

#: Minimum number of consecutive flagged windows for a run to count as an event
#: (``1`` reports every flagged run; raise it to suppress isolated single-window
#: flags). A QC-rendering default, **not** §11.2.
DEFAULT_ANTICORR_MIN_WINDOWS = 1


@dataclass(frozen=True)
class AnticorrelationEvent:
    """One localized donor–acceptor anticorrelation event within a trace.

    :attr:`start` (inclusive) and :attr:`stop` (exclusive) are the frame span the
    event's flagged windows collectively cover, in the trace's own (window-relative)
    frame coordinates. :attr:`peak_frame` is the centre frame of the run's most
    anti-phase window (most-negative same-frame ``lag0``); :attr:`peak_lag0` is that
    window's signed lag-0 Pearson correlation (``< 0``) and :attr:`peak_lag1_magnitude`
    its lag-1 magnitude (the temporal-structure strength there). :attr:`mean_lag0`
    averages the signed lag-0 over the run's :attr:`n_windows` flagged windows.
    """

    start: int  # first frame of the event (inclusive)
    stop: int  # last frame of the event + 1 (exclusive)
    peak_frame: int  # window-centre frame of the most anti-phase (most-negative lag0) window
    peak_lag0: float  # signed lag-0 Pearson at the peak window (negative ⇒ anti-phase)
    peak_lag1_magnitude: float  # |lag-1| at the peak window (temporal-structure strength)
    mean_lag0: float  # mean signed lag-0 over the event's flagged windows
    n_windows: int  # consecutive flagged windows merged into this event

    @property
    def n_frames(self) -> int:
        """Number of frames the event spans (``stop - start``)."""
        return self.stop - self.start

    @property
    def peak_magnitude(self) -> float:
        """Strength of the peak same-frame anticorrelation, ``|peak_lag0|``."""
        return abs(self.peak_lag0)


@dataclass(frozen=True)
class AnticorrelationScan:
    """The per-window cross-correlation profile of one trace and its events.

    :attr:`lag0` ``[w]`` is the signed lag-0 (same-frame) Pearson correlation and
    :attr:`lag1_magnitude` ``[w]`` the lag-1 magnitude measured in the window centred at
    :attr:`centers` ``[w]`` (both ``NaN`` where that window is constant — an undefined
    correlation, never a fabricated 0). :attr:`events` are the maximal runs of
    consecutive windows with ``lag0 < 0`` **and** ``lag1_magnitude >= min_magnitude``
    that survive the :attr:`min_windows` length filter, in ascending frame order. The
    scan is empty (no windows, no events) when the trace is shorter than :attr:`window`.
    """

    window: int  # window length (frames) used
    step: int  # stride between windows (frames)
    min_magnitude: float  # lag-1 magnitude flag threshold
    min_windows: int  # minimum consecutive flagged windows per event
    centers: np.ndarray  # (W,) int64 window-centre frame index
    lag0: np.ndarray  # (W,) float64 signed lag-0 Pearson per window (NaN if undefined)
    lag1_magnitude: np.ndarray  # (W,) float64 |lag-1| per window (NaN if undefined)
    events: tuple[AnticorrelationEvent, ...]
    n_frames: int  # trace length scanned

    @property
    def n_windows(self) -> int:
        """Number of windows swept over the trace."""
        return int(self.centers.shape[0])

    @property
    def n_events(self) -> int:
        """Number of anticorrelation events found."""
        return len(self.events)


def _merge_events(
    starts: np.ndarray,
    centers: np.ndarray,
    lag0: np.ndarray,
    lag1_magnitude: np.ndarray,
    flagged: np.ndarray,
    window: int,
    min_windows: int,
) -> tuple[AnticorrelationEvent, ...]:
    """Merge maximal runs of consecutive flagged windows into events.

    A run of flagged window indices ``[i0, i1]`` becomes one event spanning frames
    ``[starts[i0], starts[i1] + window)`` (every frame its windows touch), with the
    run's most anti-phase (most-negative ``lag0``) window as the peak. Runs shorter than
    ``min_windows`` are dropped.
    """
    if not flagged.any():
        return ()
    # Boundaries of maximal True runs via the transitions of the boolean mask.
    padded = np.concatenate(([False], flagged, [False]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    starts_idx = edges[0::2]  # first flagged index of each run
    stops_idx = edges[1::2]  # one past the last flagged index of each run

    events: list[AnticorrelationEvent] = []
    for i0, i_end in zip(starts_idx, stops_idx, strict=True):
        i1 = i_end - 1  # inclusive last window index of the run
        n_win = int(i_end - i0)
        if n_win < min_windows:
            continue
        run_lag0 = lag0[i0:i_end]
        peak_widx = int(i0) + int(np.argmin(run_lag0))  # most negative lag0 = most anti-phase
        events.append(
            AnticorrelationEvent(
                start=int(starts[i0]),
                stop=int(starts[i1]) + int(window),
                peak_frame=int(centers[peak_widx]),
                peak_lag0=float(lag0[peak_widx]),
                peak_lag1_magnitude=float(lag1_magnitude[peak_widx]),
                mean_lag0=float(np.mean(run_lag0)),
                n_windows=n_win,
            )
        )
    return tuple(events)


def find_anticorrelation_events(
    donor: np.ndarray,
    acceptor: np.ndarray,
    *,
    window: int = DEFAULT_ANTICORR_WINDOW,
    step: int = DEFAULT_ANTICORR_STEP,
    min_magnitude: float = DEFAULT_ANTICORR_MIN_MAGNITUDE,
    min_windows: int = DEFAULT_ANTICORR_MIN_WINDOWS,
) -> AnticorrelationScan:
    """Localize donor–acceptor anticorrelation events in one trace (the pure core).

    Sweeps a ``window``-frame window (stride ``step``) along the trace, measures each
    window's donor–acceptor cross-correlation with
    :func:`~tether.analysis.crosscorr.cross_correlation` (``max_lag=1``), and merges
    maximal runs of windows that are **anti-phase** (signed lag-0 ``< 0``) **and**
    **temporally structured** (lag-1 magnitude ``>= min_magnitude``) into
    :class:`AnticorrelationEvent`\\ s (runs shorter than ``min_windows`` are dropped).

    The lag-0 sign supplies the reliable anti-phase direction (it is the Pearson
    coefficient); the lag-1 magnitude supplies the dynamics strength and rejects white
    same-frame shot-noise anticorrelation. The signed lag-1 value is deliberately *not*
    used for direction — the cross-correlation's biased normalization decouples its sign
    from the same-frame relationship (see the module docstring).

    Parameters
    ----------
    donor, acceptor
        1-D per-frame intensity time-series of equal length. Must be finite (a
        cross-correlation is undefined across ``NaN``/``inf`` gaps).
    window
        Sliding-window length in frames (``>= 2``). A trace shorter than ``window``
        yields an empty scan (no events — not an error).
    step
        Stride between successive windows in frames (``>= 1``).
    min_magnitude
        Minimum lag-1 correlation magnitude for an anti-phase window to flag
        (``0 <= min_magnitude <= 1``).
    min_windows
        Minimum number of consecutive flagged windows for a run to count (``>= 1``).

    Returns
    -------
    AnticorrelationScan

    Raises
    ------
    ValueError
        Mismatched, non-finite, or out-of-range arguments (see the parameter bounds
        above).
    """
    d = np.asarray(donor, dtype=np.float64).ravel()
    a = np.asarray(acceptor, dtype=np.float64).ravel()
    if d.shape != a.shape:
        raise ValueError(f"donor and acceptor must be the same length, got {d.shape} vs {a.shape}")
    if not (np.isfinite(d).all() and np.isfinite(a).all()):
        raise ValueError("donor and acceptor must be finite (no NaN/inf gaps)")
    window = int(window)
    if window < 2:
        raise ValueError(f"window must be >= 2, got {window}")
    step = int(step)
    if step < 1:
        raise ValueError(f"step must be >= 1, got {step}")
    min_magnitude = float(min_magnitude)
    if not 0.0 <= min_magnitude <= 1.0:
        raise ValueError(f"min_magnitude must be in [0, 1], got {min_magnitude}")
    min_windows = int(min_windows)
    if min_windows < 1:
        raise ValueError(f"min_windows must be >= 1, got {min_windows}")

    n = int(d.shape[0])
    if n < window:  # no full window fits -> nothing to scan (not an error)
        return AnticorrelationScan(
            window=window,
            step=step,
            min_magnitude=min_magnitude,
            min_windows=min_windows,
            centers=np.empty(0, dtype=np.int64),
            lag0=np.empty(0, dtype=np.float64),
            lag1_magnitude=np.empty(0, dtype=np.float64),
            events=(),
            n_frames=n,
        )

    starts = np.arange(0, n - window + 1, step, dtype=np.int64)
    centers = starts + window // 2
    lag0 = np.full(starts.shape[0], np.nan, dtype=np.float64)
    lag1_magnitude = np.full(starts.shape[0], np.nan, dtype=np.float64)
    for i, s in enumerate(starts):
        s = int(s)
        win_d = d[s : s + window]
        win_a = a[s : s + window]
        # A constant window has an undefined correlation -> leave NaN (never flagged).
        # cross_correlation itself returns NaN there, but skipping the FFT is cheaper on
        # long bleached/flat stretches and defers to that same "undefined" contract.
        if win_d.max() == win_d.min() or win_a.max() == win_a.min():
            continue
        cc = cross_correlation(win_d, win_a, max_lag=1)
        lag0[i] = cc.lag0
        lag1_magnitude[i] = cc.lag1_magnitude

    # anti-phase (reliable lag-0 sign) AND temporally structured (lag-1 magnitude);
    # NaN comparisons are False, so an undefined window never flags.
    flagged = (lag0 < 0.0) & (lag1_magnitude >= min_magnitude)
    events = _merge_events(starts, centers, lag0, lag1_magnitude, flagged, window, min_windows)
    return AnticorrelationScan(
        window=window,
        step=step,
        min_magnitude=min_magnitude,
        min_windows=min_windows,
        centers=centers,
        lag0=lag0,
        lag1_magnitude=lag1_magnitude,
        events=events,
        n_frames=n,
    )


@dataclass(frozen=True)
class MoleculeAnticorrelation:
    """One accepted molecule's anticorrelation :class:`AnticorrelationScan`, keyed."""

    molecule_key: str
    scan: AnticorrelationScan


@dataclass(frozen=True)
class PopulationAnticorrelation:
    """Per-molecule anticorrelation-event scans over a ``.tether`` population (§7.7).

    :attr:`molecules` holds one :class:`MoleculeAnticorrelation` per accepted molecule
    long enough to scan (``>= window`` frames), in store order; molecules too short are
    skipped. :attr:`n_molecules` is how many were scanned and :attr:`n_events` the total
    events across them. The window/threshold fields echo the parameters used.
    """

    molecules: tuple[MoleculeAnticorrelation, ...]
    window: int
    step: int
    min_magnitude: float
    min_windows: int
    n_molecules: int  # molecules scanned (>= window frames)
    n_events: int  # total anticorrelation events across all scanned molecules


def population_anticorrelation_events(
    project: ProjectRef,
    *,
    molecule_keys: list[str] | None = None,
    intensity_quantity: str = "corrected",
    window: int = DEFAULT_ANTICORR_WINDOW,
    step: int = DEFAULT_ANTICORR_STEP,
    min_magnitude: float = DEFAULT_ANTICORR_MIN_MAGNITUDE,
    min_windows: int = DEFAULT_ANTICORR_MIN_WINDOWS,
    include_rejected: bool = False,
) -> PopulationAnticorrelation:
    """Anticorrelation-event scans over a ``.tether`` store (§10 PR-6; PRD §7.7).

    Runs :func:`find_anticorrelation_events` on each accepted molecule's windowed
    donor/acceptor intensities (the ``intensity_quantity`` channels sliced to the
    ``analysis_window``; rejected molecules excluded unless ``include_rejected``, §7.5).
    Only molecules with at least ``window`` frames are scanned; the rest are skipped
    (they cannot carry a full window, not an error).

    Parameters
    ----------
    project
        A :class:`~tether.project.core.Project` or a path to a ``.tether`` store.
    molecule_keys
        Restrict to these ``molecule_key`` values (``None`` = all), intersected with
        the §7.5 curation filter.
    intensity_quantity
        Which ``/traces`` layers supply the channels (``"corrected"`` default, or
        ``"raw"``; see :func:`tether.analysis._store.resolve_quantity`).
    window, step, min_magnitude, min_windows
        Passed through to :func:`find_anticorrelation_events`.
    include_rejected
        Keep rejected molecules (default excludes them, §7.5).

    Returns
    -------
    PopulationAnticorrelation

    Raises
    ------
    ValueError
        The store lacks the requested trace layer, or a
        :func:`find_anticorrelation_events` parameter is invalid.
    """
    from tether.analysis._store import windowed_channels_with_keys

    keyed = windowed_channels_with_keys(
        project, molecule_keys, intensity_quantity, include_rejected
    )
    molecules: list[MoleculeAnticorrelation] = []
    n_events = 0
    for key, donor, acceptor in keyed:
        if donor.shape[0] < int(window):  # too short to carry a full window -> skip
            continue
        scan = find_anticorrelation_events(
            donor,
            acceptor,
            window=window,
            step=step,
            min_magnitude=min_magnitude,
            min_windows=min_windows,
        )
        molecules.append(MoleculeAnticorrelation(molecule_key=key, scan=scan))
        n_events += scan.n_events

    return PopulationAnticorrelation(
        molecules=tuple(molecules),
        window=int(window),
        step=int(step),
        min_magnitude=float(min_magnitude),
        min_windows=int(min_windows),
        n_molecules=len(molecules),
        n_events=n_events,
    )
