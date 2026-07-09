# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""State-number bar chart (M6 C1, FR-ANALYZE; PRD §7.7, Appendix C C1).

Tether's C1 is the consensus-model analogue of tMAVEN's ``model_vbstates`` per-trace
vbFRET state count: each molecule's state number is the number of **distinct states
its persisted Viterbi path occupies**. The store path enforces the two Tether
invariants tMAVEN has no analogue for — **fresh idealizations only** (PRD §5.1) and
the §7.5 curation filter — exactly as the B1 TDP does. All headless (no Qt) → base CI
matrix; the store is seeded as post-idealization data under the M0-frozen schema.
"""

from __future__ import annotations

import pytest

pytest.importorskip("numpy")
pytest.importorskip("h5py")

import numpy as np  # noqa: E402

from _analysis_store import MEANS, build_store_with_model  # noqa: E402
from tether.analysis import (  # noqa: E402
    DEFAULT_STATE_NUMBER_LOW,
    StateNumberCounts,
    occupied_state_count,
    population_state_number,
    state_number_counts,
)
from tether.idealize import NO_STATE  # noqa: E402

# --- pure core: occupied_state_count -----------------------------------------


def test_occupied_state_count_distinct_states() -> None:
    assert occupied_state_count(np.array([0, 0, 1, 1, 2, 2])) == 3
    assert occupied_state_count(np.array([1, 1, 1, 1])) == 1
    assert occupied_state_count(np.array([0, 2, 0, 2])) == 2  # revisits don't double-count


def test_occupied_state_count_ignores_no_state_gaps() -> None:
    v = np.array([NO_STATE, 0, 0, NO_STATE, 1, NO_STATE])
    assert occupied_state_count(v) == 2  # only states 0 and 1
    assert occupied_state_count(np.full(5, NO_STATE)) == 0  # all-gap -> zero


# --- pure core: state_number_counts ------------------------------------------


def test_defaults() -> None:
    assert DEFAULT_STATE_NUMBER_LOW == 1


def test_empty_input_is_all_zero() -> None:
    c = state_number_counts([])
    assert isinstance(c, StateNumberCounts)
    assert c.n_molecules == 0
    assert c.n_in_range == 0
    assert c.n_out_of_range == 0
    # states_high defaults down to states_low when there is no data
    assert c.states_low == 1
    assert c.states_high == 1
    np.testing.assert_array_equal(c.states, np.array([1]))
    np.testing.assert_array_equal(c.counts, np.array([0]))


def test_single_molecule_bar() -> None:
    c = state_number_counts([np.array([0, 0, 1, 1, 2])])  # 3 distinct states
    assert c.n_molecules == 1
    assert c.states_high == 3  # derived from data
    np.testing.assert_array_equal(c.states, np.array([1, 2, 3]))
    np.testing.assert_array_equal(c.counts, np.array([0, 0, 1]))
    assert c.n_in_range == 1
    assert c.n_bars == 3


def test_all_gap_molecule_not_counted() -> None:
    # a molecule whose path is entirely NO_STATE contributes nothing at all.
    c = state_number_counts([np.array([0, 0, 1]), np.full(4, NO_STATE)])
    assert c.n_molecules == 1  # only the real one
    np.testing.assert_array_equal(c.states, np.array([1, 2]))
    np.testing.assert_array_equal(c.counts, np.array([0, 1]))


def test_mixed_population_histogram() -> None:
    chunks = [
        np.array([0, 0, 0]),  # 1 state
        np.array([0, 1, 1]),  # 2 states
        np.array([0, 1, 2]),  # 3 states
        np.array([2, 2, 1]),  # 2 states
    ]
    c = state_number_counts(chunks)
    np.testing.assert_array_equal(c.states, np.array([1, 2, 3]))
    np.testing.assert_array_equal(c.counts, np.array([1, 2, 1]))
    assert c.n_molecules == 4
    assert c.n_in_range == 4


def test_states_high_clips_and_reports_out_of_range() -> None:
    # states 0,1,2 occupied -> 3-state molecule; clip axis at high=2 -> it is out of range.
    chunks = [np.array([0, 1]), np.array([0, 1, 2])]  # 2 states, 3 states
    c = state_number_counts(chunks, states_low=1, states_high=2)
    np.testing.assert_array_equal(c.states, np.array([1, 2]))
    np.testing.assert_array_equal(c.counts, np.array([0, 1]))  # only the 2-state molecule
    assert c.n_molecules == 2
    assert c.n_in_range == 1
    assert c.n_out_of_range == 1  # the 3-state molecule, honestly reported (no silent cap)


def test_states_low_above_one_excludes_low_molecules() -> None:
    chunks = [np.array([0, 0]), np.array([0, 1, 2])]  # 1 state, 3 states
    c = state_number_counts(chunks, states_low=2, states_high=3)
    np.testing.assert_array_equal(c.states, np.array([2, 3]))
    np.testing.assert_array_equal(c.counts, np.array([0, 1]))
    assert c.n_out_of_range == 1  # the 1-state molecule below the floor


def test_auto_range_below_floor_does_not_crash() -> None:
    # states_high=None with every molecule below states_low must NOT raise on its own
    # derived bound: the axis clamps up to [low, low] and the molecules count as
    # out-of-range (honest), mirroring the B1 TDP never-crash-on-underfull invariant.
    chunks = [np.array([0, 0, 0]), np.array([1, 1, 1])]  # each occupies 1 state
    c = state_number_counts(chunks, states_low=3, states_high=None)
    np.testing.assert_array_equal(c.states, np.array([3]))
    np.testing.assert_array_equal(c.counts, np.array([0]))
    assert c.n_molecules == 2
    assert c.n_in_range == 0
    assert c.n_out_of_range == 2


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"states_low": 0}, "states_low"),
        ({"states_low": -1}, "states_low"),
        ({"states_low": 3, "states_high": 2}, "states_high"),
    ],
)
def test_validation_errors(kwargs, match) -> None:
    with pytest.raises(ValueError, match=match):
        state_number_counts([], **kwargs)


def test_flat_array_misuse_raises_not_silent() -> None:
    with pytest.raises(ValueError, match="scalar element"):
        state_number_counts(np.array([0, 0, 1, 2]))
    with pytest.raises(ValueError, match="scalar element"):
        state_number_counts([0, 0, 1, 2])
    # a 2-D array is fine: each row is a molecule
    ok = state_number_counts(np.array([[0, 0, 1, 2]]))
    assert ok.n_molecules == 1
    np.testing.assert_array_equal(ok.counts, np.array([0, 0, 1]))


# --- store-level: seed a .tether with molecules + traces + idealization -------


def _states() -> np.ndarray:
    # molecule 0 visits {0,1,2} (3 states); molecule 1 visits {0,2} (2 states)
    return np.array(
        [
            [0, 0, 0, 1, 1, 1, 2, 2, 2, 2],
            [0, 0, 0, 0, 0, 2, 2, 2, 2, 2],
        ],
        dtype="int64",
    )


def test_population_matches_core(tmp_path) -> None:
    s = _states()
    proj, _keys = build_store_with_model(tmp_path, s, MEANS)
    c = population_state_number(proj, "vbconhmm")
    assert isinstance(c, StateNumberCounts)
    assert c.n_molecules == 2
    # molecule 0 -> 3 states, molecule 1 -> 2 states
    np.testing.assert_array_equal(c.states, np.array([1, 2, 3]))
    np.testing.assert_array_equal(c.counts, np.array([0, 1, 1]))
    # equal to feeding the pure core the per-molecule state rows
    ref = state_number_counts([s[0], s[1]])
    np.testing.assert_array_equal(c.counts, ref.counts)


def test_stale_molecule_excluded_by_default(tmp_path) -> None:
    s = _states()
    proj, _keys = build_store_with_model(tmp_path, s, MEANS, stale=[False, True])
    c = population_state_number(proj, "vbconhmm")
    assert c.n_molecules == 1  # only molecule 0 (3 states)
    np.testing.assert_array_equal(c.states, np.array([1, 2, 3]))
    np.testing.assert_array_equal(c.counts, np.array([0, 0, 1]))


def test_include_stale_restores_the_molecule(tmp_path) -> None:
    s = _states()
    proj, _keys = build_store_with_model(tmp_path, s, MEANS, stale=[False, True])
    c = population_state_number(proj, "vbconhmm", include_stale=True)
    assert c.n_molecules == 2


def test_rejected_molecule_excluded_by_default(tmp_path) -> None:
    s = _states()
    proj, _keys = build_store_with_model(tmp_path, s, MEANS, rejected=[False, True])
    c = population_state_number(proj, "vbconhmm")
    assert c.n_molecules == 1


def test_include_rejected_restores_the_molecule(tmp_path) -> None:
    s = _states()
    proj, _keys = build_store_with_model(tmp_path, s, MEANS, rejected=[False, True])
    c = population_state_number(proj, "vbconhmm", include_rejected=True)
    assert c.n_molecules == 2


def test_molecule_keys_selection(tmp_path) -> None:
    s = _states()
    proj, keys = build_store_with_model(tmp_path, s, MEANS)
    c = population_state_number(proj, "vbconhmm", molecule_keys=[keys[1]])
    assert c.n_molecules == 1  # molecule 1 (2 states)
    np.testing.assert_array_equal(c.states, np.array([1, 2]))
    np.testing.assert_array_equal(c.counts, np.array([0, 1]))


def test_molecule_keys_intersect_fresh(tmp_path) -> None:
    # explicitly selecting a STALE key yields nothing: the fresh intersection (not just
    # the molecule_keys selection) gates it. include_stale restores it.
    s = _states()
    proj, keys = build_store_with_model(tmp_path, s, MEANS, stale=[False, True])
    c = population_state_number(proj, "vbconhmm", molecule_keys=[keys[1]])
    assert c.n_molecules == 0
    assert c.n_in_range == 0
    c2 = population_state_number(proj, "vbconhmm", molecule_keys=[keys[1]], include_stale=True)
    assert c2.n_molecules == 1  # the stale 2-state molecule restored
    np.testing.assert_array_equal(c2.states, np.array([1, 2]))
    np.testing.assert_array_equal(c2.counts, np.array([0, 1]))


def test_missing_model_raises(tmp_path) -> None:
    s = _states()
    proj, _keys = build_store_with_model(tmp_path, s, MEANS)
    with pytest.raises(KeyError):
        population_state_number(proj, "no-such-model")
