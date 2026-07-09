# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""State-number bar chart — M6 C1, FR-ANALYZE (PRD §7.7, Appendix C C1).

tMAVEN's ``model_vbstates.py`` bars *how many trajectories were modeled with N
states* after independent per-trace vbFRET model selection: for each trace's own
fit it reads ``hmm.mu.size`` and histograms those per-trace state counts. That plot
reads a data structure Tether does not persist — Tether always fits a **consensus /
global** model (one shared state count, one ``/idealization/{model}`` for the whole
population) and stores each molecule's **Viterbi state path**, not an independent
per-trace HMM (:class:`tether.project.idealize.StoredIdealization`).

So, exactly as the B1 real TDP (:mod:`tether.analysis.tdp`) rebuilds a model-plot
from persisted Viterbi paths rather than the fitted transition matrix, Tether's C1
is the **empirical** analogue: each molecule's state number is the count of
**distinct states its Viterbi path actually occupies**. A trace that only ever sits
in one level shows 1 state even though the consensus model has three; a trace that
visits 0↔1↔2 shows 3. This is the only per-trace state-count heterogeneity a
consensus model produces, and it answers the same scientific question C1 exists for
— how many conformational states does each molecule visit — from Tether's data model
[McKinney2006][vandeMeent2014].

:func:`state_number_counts` is the pure-array core (an iterable of per-molecule
integer Viterbi paths → a :class:`StateNumberCounts` bar-chart table);
:func:`population_state_number` is the ``.tether`` store entry point that pulls each
accepted, **fresh** molecule's state path and applies the §7.5 curation filter.

References
----------
[McKinney2006] McKinney, Joo & Ha. "Analysis of single-molecule FRET trajectories
    using hidden Markov modeling." Biophysical Journal (2006) — HMM inference yields,
    per trajectory, the most likely underlying state sequence and the number of
    states present.
[vandeMeent2014] van de Meent, Bronson, Wiggins & Gonzalez. "Empirical Bayes methods
    enable advanced population-level analyses of single-molecule FRET experiments."
    Biophysical Journal (2014) — per-molecule inferred parameters (including occupied
    state counts) vary widely within a population, motivating a distribution view.
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
    "DEFAULT_STATE_NUMBER_LOW",
    "StateNumberCounts",
    "occupied_state_count",
    "population_state_number",
    "state_number_counts",
]

#: Lowest state number on the bar-chart x-axis (Appendix C C1). tMAVEN's
#: ``model_vbstates`` ``states_low``; a molecule with a valid idealization occupies at
#: least one state, so ``1`` is the natural floor. A rendering default, **not** a PRD
#: §11.2 tunable.
DEFAULT_STATE_NUMBER_LOW = 1


@dataclass(frozen=True)
class StateNumberCounts:
    """Molecule counts by number of occupied Viterbi states (self-describing).

    ``counts[i]`` is the number of molecules whose Viterbi path occupies exactly
    ``states[i]`` distinct states — the height of bar ``states[i]`` in the C1 chart.
    ``states`` spans ``[states_low, states_high]``. :attr:`n_molecules` counts every
    molecule with at least one occupied state; :attr:`n_in_range` (= ``counts.sum()``)
    counts only those whose state number fell inside the bars, and
    :attr:`n_out_of_range` records the rest, so a clipped x-axis never silently hides
    molecules.
    """

    states: np.ndarray  # (n_bars,) int64 — the state-number x-axis [low, high]
    counts: np.ndarray  # (n_bars,) int64 — molecules occupying exactly that many states
    states_low: int
    states_high: int
    n_molecules: int  # molecules with >= 1 occupied state (tMAVEN's per-trace N)
    n_in_range: int  # molecules whose state number is in [low, high] (== counts.sum())
    n_out_of_range: int  # molecules whose state number falls outside the bars

    @property
    def n_bars(self) -> int:
        """Number of bars (state-number values on the x-axis)."""
        return int(self.states.shape[0])


def occupied_state_count(state_path: np.ndarray) -> int:
    """Number of **distinct** non-:data:`~tether.idealize.NO_STATE` states in a path.

    ``state_path`` is one molecule's integer Viterbi path (``NO_STATE`` outside the
    window / at interior gaps). Returns how many different states it actually visits —
    ``0`` for an all-gap path.
    """
    from tether.idealize import NO_STATE

    v = np.asarray(state_path, dtype=np.int64).ravel()
    occupied = v[v != NO_STATE]
    return int(np.unique(occupied).size)


def state_number_counts(
    state_chunks: Iterable[np.ndarray],
    *,
    states_low: int = DEFAULT_STATE_NUMBER_LOW,
    states_high: int | None = None,
) -> StateNumberCounts:
    """Bar-chart per-molecule occupied-state counts into a :class:`StateNumberCounts`.

    Each element of ``state_chunks`` is one molecule's integer Viterbi path
    (:data:`~tether.idealize.NO_STATE` outside its window). Molecules with **zero**
    occupied states (an all-gap path) contribute nothing — the analogue of tMAVEN
    counting only traces that carry a model. The distinct-state count of every
    remaining molecule is tallied into the bars ``[states_low, states_high]``.

    Parameters
    ----------
    state_chunks
        Iterable of 1-D per-molecule integer state paths.
    states_low
        Lowest state number on the x-axis (``>= 1``).
    states_high
        Highest state number on the x-axis (``>= states_low``). ``None`` derives it
        from the data (the maximum occupied-state count observed, or ``states_low``
        when there is no molecule), so the axis spans exactly what the population shows.

    Returns
    -------
    StateNumberCounts
    """
    if int(states_low) < 1:
        raise ValueError(f"states_low must be >= 1, got {states_low}")
    states_low = int(states_low)

    per_molecule: list[int] = []
    for chunk in state_chunks:
        if np.ndim(chunk) == 0:
            # A scalar element means a flat 1-D array was passed instead of an iterable
            # of per-molecule paths (``state_number_counts(v)`` vs ``[v]``); iterating it
            # would tally single frames as molecules. Fail fast on this public-API misuse.
            raise ValueError(
                "state_chunks must be an iterable of 1-D per-molecule state paths, got a "
                "scalar element — wrap a single molecule as [v], not v"
            )
        count = occupied_state_count(chunk)
        if count >= 1:
            per_molecule.append(count)

    per = np.asarray(per_molecule, dtype=np.int64)
    n_molecules = int(per.size)

    if states_high is None:
        # Auto-range spans what the population shows, but never below the caller's
        # floor: with every molecule below ``states_low`` the max is still clamped up
        # so the axis stays valid ([low, low]) and the below-floor molecules land in
        # ``n_out_of_range`` — never a ValueError on its own derived bound.
        states_high = max(int(per.max()), states_low) if n_molecules else states_low
    states_high = int(states_high)
    if states_high < states_low:
        raise ValueError(f"states_high ({states_high}) must be >= states_low ({states_low})")

    states = np.arange(states_low, states_high + 1, dtype=np.int64)
    counts = np.array([int(np.count_nonzero(per == k)) for k in states.tolist()], dtype=np.int64)
    n_in_range = int(counts.sum())
    return StateNumberCounts(
        states=states,
        counts=counts,
        states_low=states_low,
        states_high=states_high,
        n_molecules=n_molecules,
        n_in_range=n_in_range,
        n_out_of_range=n_molecules - n_in_range,
    )


def population_state_number(
    project: ProjectRef,
    model_name: str,
    *,
    molecule_keys: list[str] | None = None,
    states_low: int = DEFAULT_STATE_NUMBER_LOW,
    states_high: int | None = None,
    include_rejected: bool = False,
    include_stale: bool = False,
) -> StateNumberCounts:
    """State-number bar chart from a ``.tether`` store (§10 C1; Appendix C C1; PRD §7.7).

    Reads each accepted, **fresh** molecule's Viterbi state path from
    ``/idealization/{model_name}`` and feeds them to :func:`state_number_counts`.
    STALE molecules are excluded (PRD §5.1) unless ``include_stale``; rejected
    molecules are excluded unless ``include_rejected`` (§7.5) — the same selection
    contract as the B1 TDP (:func:`tether.analysis.tdp.population_transition_density`).

    Parameters
    ----------
    project
        A :class:`~tether.project.core.Project` or a path to a ``.tether`` store.
    model_name
        Which ``/idealization/{model_name}`` supplies the state paths.
    molecule_keys
        Restrict to these ``molecule_key`` values (``None`` = all), intersected with
        the fresh + curation filters.
    states_low, states_high
        Passed through to :func:`state_number_counts`.
    include_rejected
        Keep rejected molecules (default excludes them, §7.5).
    include_stale
        Keep STALE idealizations (default excludes them, PRD §5.1).

    Returns
    -------
    StateNumberCounts

    Raises
    ------
    KeyError
        No ``/idealization/{model_name}`` in the store.
    """
    from tether.analysis._store import windowed_states
    from tether.project.idealize import live_molecule_keys

    keys = molecule_keys
    if not include_stale:
        live = live_molecule_keys(project, model_name)
        if molecule_keys is None:
            keys = live
        else:
            live_set = set(live)
            keys = [k for k in molecule_keys if k in live_set]

    states = windowed_states(project, model_name, keys, include_rejected)
    return state_number_counts(states, states_low=states_low, states_high=states_high)
