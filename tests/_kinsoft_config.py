# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The kinSoftChallenge simulator settings as the truth of oracle (c), for tests only (#717).

The kinSoftChallenge record (Zenodo 10.5281/zenodo.5701310) ships ``MATLAB_simulator.zip``.
That zip holds one settings file for each simulated level,
``Config_simulation_round_1_level_N.cfg``, which the simulator read to make the published
traces. Tether commits the three ``.cfg`` files verbatim under
``tests/fixtures/kinsoft_simulator/`` and never the simulator itself (PRD N2). This module:

* :func:`read_simulator_config` parses one file into a :class:`SimulatorConfig`: the true
  rate matrix, the frame rate, the bleach rate, the blinking rates, the map of states to
  degenerate (observable) states and the mean intensities, each with the 1-based lines of
  the file it came from.
* :func:`pooled_rate_matrix` turns Viterbi state paths into the rate from each state to each
  other state. It counts the dwells :func:`tether.analysis.kinetics.pooled_exit_rates`
  counts, so each row sums to that function's exit rate; it is the measurement of the
  level-2 leg, kept here because it is part of the check and not a product estimator.
* :func:`stationary_distribution`, :func:`entry_distribution`, :func:`dwell_cdf` and
  :func:`mean_dwell` give what a true rate matrix predicts for the dwell times of one
  observable level when several states share it (an aggregated Markov process; Colquhoun &
  Hawkes 1981, doi:10.1098/rspb.1981.0003, and 1982, doi:10.1098/rstb.1982.0156). The
  dwell in class ``A`` starts in ``A``'s states with the probabilities ``φ_A ∝ π_B Q_BA``
  of an entry from the other states ``B`` at steady state, and lasts longer than ``t`` with
  probability ``φ_A exp(Q_AA t) 1``.

The parser reads the format the simulator's own ``importSettings.m`` reads: a header line
that starts with ``%``, then numeric rows separated by tabs, then a blank or comment line.
A ``%`` after the numbers on a row starts a comment. It is not a product reader: the module
lives under ``tests/`` and is imported as the top-level module ``_kinsoft_config``, the
pattern of ``_synth``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.linalg import expm

__all__ = [
    "CONFIG_DIR",
    "SimulatorConfig",
    "config_path",
    "dwell_cdf",
    "ks_distance",
    "entry_distribution",
    "mean_dwell",
    "pooled_rate_matrix",
    "read_simulator_config",
    "stationary_distribution",
]

#: Where the three committed settings files are.
CONFIG_DIR = Path(__file__).resolve().parent / "fixtures" / "kinsoft_simulator"

# The header of each block this module reads, as written in the files.
_RATES = "%transition rate matrix"
_FRAME_RATE = "%frameRate"
_BLEACH = "%bleachRate"
_MIN_LEN = "%minimum trace length"
_MAX_LEN = "%maximum trace length"
_COLLAPSE = "%lookupCollapse"
_BLINKING = "%include blinking"
_DONOR_BLINK = "%donor blinking kinetics"
_ACCEPTOR_BLINK = "%acceptor blinking kinetics"
_MEAN = "%fluorescence intensity, mean"
_N_TRACES = "%number of traces"


def config_path(level: int) -> Path:
    """The committed settings file of challenge level ``level`` (1, 2 or 3)."""
    return CONFIG_DIR / f"Config_simulation_round_1_level_{level}.cfg"


@dataclass(frozen=True, eq=False)
class SimulatorConfig:
    """The settings of one simulated kinSoft level, as the simulator read them.

    ``rate_matrix[i, j]`` is the rate from state ``i + 1`` to state ``j + 1`` in 1/s (the
    file counts states from 1); its rows sum to zero. ``collapse[i]`` is the 1-based
    degenerate state of state ``i + 1``. ``intensity_mean`` has one row for each degenerate
    state and then the donor-dark and acceptor-dark rows, with the columns Idd, Ida and Iaa.
    ``lines`` maps each block header to the first and last 1-based line of its numbers.
    """

    path: Path
    rate_matrix: np.ndarray
    frame_rate_hz: float
    bleach_rate_s_inv: float
    min_trace_s: float
    max_trace_s: float
    collapse: tuple[int, ...]
    include_blinking: bool
    donor_blinking: np.ndarray
    acceptor_blinking: np.ndarray
    intensity_mean: np.ndarray
    n_traces: int
    lines: dict[str, tuple[int, int]]

    @property
    def nstates(self) -> int:
        """Number of kinetic states."""
        return int(self.rate_matrix.shape[0])

    @property
    def frame_time_s(self) -> float:
        """Seconds per frame."""
        return 1.0 / self.frame_rate_hz

    def degenerate_fret(self) -> np.ndarray:
        """FRET efficiency ``Ida / (Idd + Ida)`` of each degenerate state, from the means."""
        bright = self.intensity_mean[: max(self.collapse)]
        return bright[:, 1] / (bright[:, 0] + bright[:, 1])

    def members(self, degenerate: int) -> np.ndarray:
        """0-based states that the 1-based ``degenerate`` state collects."""
        return np.flatnonzero(np.asarray(self.collapse) == degenerate)


def _block(lines: list[str], header: str) -> tuple[np.ndarray, int, int]:
    """The numeric rows after ``header`` and their first and last 1-based line numbers."""
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == header)
    except StopIteration:
        raise ValueError(f"no {header!r} block") from None
    rows: list[list[float]] = []
    first = last = 0
    for i in range(start + 1, len(lines)):
        text = lines[i].split("%", 1)[0].strip()
        if not text:
            if rows:
                break
            if lines[i].strip():
                break  # a comment line before any number: the block is empty
            continue
        rows.append([float(v) for v in text.split()])
        first = first or i + 1
        last = i + 1
    if not rows:
        raise ValueError(f"the {header!r} block has no numbers")
    width = {len(r) for r in rows}
    if len(width) != 1:
        raise ValueError(f"the {header!r} block has rows of different lengths")
    return np.array(rows, dtype=np.float64), first, last


def read_simulator_config(path: str | Path) -> SimulatorConfig:
    """Parse one ``Config_simulation_round_1_level_N.cfg`` into a :class:`SimulatorConfig`.

    Raises
    ------
    ValueError
        A block is missing or empty, its rows differ in length, the rate matrix is not
        square or a row does not sum to zero, or the map of states does not have one entry
        for each state.
    """
    path = Path(path)
    lines = path.read_text(encoding="ascii").splitlines()
    found: dict[str, tuple[int, int]] = {}

    def read(header: str) -> np.ndarray:
        values, first, last = _block(lines, header)
        found[header.lstrip("%")] = (first, last)
        return values

    def scalar(header: str) -> float:
        values = read(header)
        if values.shape != (1, 1):
            raise ValueError(f"the {header!r} block must hold one number, not {values.shape}")
        return float(values[0, 0])

    q = read(_RATES)
    if q.shape[0] != q.shape[1]:
        raise ValueError(f"the rate matrix is {q.shape[0]}x{q.shape[1]}, not square")
    if not np.allclose(q.sum(axis=1), 0.0, atol=1e-9):
        raise ValueError("a row of the rate matrix does not sum to zero")
    collapse_rows = read(_COLLAPSE)
    if collapse_rows.shape != (1, q.shape[0]):
        raise ValueError(f"lookupCollapse must hold {q.shape[0]} entries in one row")
    collapse = tuple(int(v) for v in collapse_rows[0])
    return SimulatorConfig(
        path=path,
        rate_matrix=q,
        frame_rate_hz=scalar(_FRAME_RATE),
        bleach_rate_s_inv=scalar(_BLEACH),
        min_trace_s=scalar(_MIN_LEN),
        max_trace_s=scalar(_MAX_LEN),
        collapse=collapse,
        include_blinking=bool(scalar(_BLINKING)),
        donor_blinking=read(_DONOR_BLINK),
        acceptor_blinking=read(_ACCEPTOR_BLINK),
        intensity_mean=read(_MEAN),
        n_traces=int(scalar(_N_TRACES)),
        lines=found,
    )


# --- what a true rate matrix predicts for an observable level --------------------------


def stationary_distribution(q: np.ndarray) -> np.ndarray:
    """The stationary distribution ``π`` of rate matrix ``q`` (``π q = 0``, ``Σπ = 1``)."""
    n = q.shape[0]
    a = np.vstack([q.T, np.ones(n)])
    b = np.zeros(n + 1)
    b[-1] = 1.0
    pi, *_ = np.linalg.lstsq(a, b, rcond=None)
    return pi


def entry_distribution(q: np.ndarray, members: np.ndarray) -> np.ndarray:
    """Probabilities of the states of class ``members`` at an entry into it, at steady state."""
    others = np.setdiff1d(np.arange(q.shape[0]), members)
    flow = stationary_distribution(q)[others] @ q[np.ix_(others, members)]
    return flow / flow.sum()


def dwell_cdf(q: np.ndarray, members: np.ndarray, t: np.ndarray) -> np.ndarray:
    """``P(dwell ≤ t)`` for one dwell in the class ``members`` of rate matrix ``q``."""
    phi = entry_distribution(q, members)
    q_aa = q[np.ix_(members, members)]
    ones = np.ones(members.size)
    return np.array([1.0 - float(phi @ expm(q_aa * float(x)) @ ones) for x in np.ravel(t)])


def ks_distance(samples: np.ndarray, cdf: Callable[[np.ndarray], np.ndarray]) -> float:
    """Kolmogorov-Smirnov distance ``sup_t |F_n(t) − F(t)|`` of ``samples`` from ``cdf``.

    The empirical CDF ``F_n`` jumps at each distinct sample value, from the fraction below
    it to the fraction at or below it; dwell times are whole frames, so many values repeat,
    and both sides of every jump are compared with the continuous ``F`` there.
    """
    values, counts = np.unique(np.asarray(samples, dtype=np.float64), return_counts=True)
    above = np.cumsum(counts) / counts.sum()
    below = above - counts / counts.sum()
    model = np.asarray(cdf(values), dtype=np.float64)
    return float(max(np.max(np.abs(above - model)), np.max(np.abs(model - below))))


def mean_dwell(q: np.ndarray, members: np.ndarray) -> float:
    """Mean dwell time in the class ``members``: ``φ (−Q_AA)⁻¹ 1``."""
    phi = entry_distribution(q, members)
    q_aa = q[np.ix_(members, members)]
    return float(phi @ np.linalg.solve(-q_aa, np.ones(members.size)))


# --- the measurement of the level-2 leg -------------------------------------------------


def pooled_rate_matrix(
    paths: list[np.ndarray], dt: float, nstates: int
) -> tuple[np.ndarray, np.ndarray]:
    """Rate from each state to each other state, ``k_ij = n_ij / T_i``, from state paths.

    The dwells that count are the ones :func:`tether.analysis.dwell.state_dwells` keeps by
    default: neither the first dwell of a molecule (its start was not observed) nor the last
    (its end was not). ``n_ij`` is the number of counted dwells in ``i`` that end in ``j`` and
    ``T_i`` the total time of the counted dwells in ``i``. So ``Σ_j k_ij = 1/⟨τ_i⟩``, the
    exit rate of :func:`tether.analysis.kinetics.pooled_exit_rates`, and ``k_ij`` is that
    exit rate times the fraction of exits to ``j``: the maximum-likelihood rate of a
    continuous-time Markov chain whose every transition is seen.

    Returns ``(rates, counts)``, both ``(nstates, nstates)``: ``rates`` in 1/s when ``dt``
    is in seconds, zero on the diagonal and NaN in the row of a state with no counted
    dwell; ``counts[i, j] = n_ij``.
    """
    counts = np.zeros((nstates, nstates), dtype=np.int64)
    frames = np.zeros(nstates, dtype=np.int64)
    for path in paths:
        p = np.asarray(path, dtype=np.int64).ravel()
        if p.size and (p.min() < 0 or p.max() >= nstates):
            raise ValueError(f"a path holds a state outside 0..{nstates - 1}")
        starts = np.concatenate(([0], np.flatnonzero(np.diff(p)) + 1)) if p.size else p
        ends = np.concatenate((starts[1:], [p.size])) if p.size else p
        # Runs 1 .. n-2: run 0 has no observed start, run n-1 no observed end.
        for r in range(1, starts.size - 1):
            src = p[starts[r]]
            counts[src, p[ends[r]]] += 1
            frames[src] += ends[r] - starts[r]
    rates = np.full((nstates, nstates), np.nan)
    seen = frames > 0
    rates[seen] = counts[seen] / (frames[seen, None] * float(dt))
    rates[np.diag_indices(nstates)] = np.where(seen, 0.0, np.nan)
    return rates, counts
