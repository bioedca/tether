# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Return-leg intensity-trace matching (PRD §7.4, §5.3)."""

from __future__ import annotations

import numpy as np
import pytest

from tether.idealize import match_return_leg


def _store(n: int, t: int, seed: int) -> np.ndarray:
    return np.random.default_rng(seed).random((n, t, 2)) * 1000.0


def test_recovers_shuffled_subset() -> None:
    """A reordered subset of the store is mapped back to the right molecules."""
    store = _store(6, 50, seed=7)
    order = [4, 1, 5]  # tMAVEN may subset + reorder by the GUI selection mask
    returned = store[order]
    result = match_return_leg(returned, store)
    assert result.all_matched
    assert result.mapping.tolist() == order


def test_matches_tail_zero_padded_traces() -> None:
    """tMAVEN pads to a common maxt at the tail; the leading region still matches."""
    store = _store(4, 40, seed=3)
    returned = np.zeros((2, 60, 2))
    returned[0, :40] = store[2]
    returned[1, :40] = store[0]
    result = match_return_leg(returned, store)
    assert result.mapping.tolist() == [2, 0]


def test_reports_unmatched_returning_trace() -> None:
    """A returning trace absent from the store is reported, never guessed."""
    store = _store(3, 30, seed=11)
    alien = np.random.default_rng(99).random((1, 30, 2)) * 1000.0
    returned = np.concatenate([store[[1]], alien], axis=0)
    result = match_return_leg(returned, store)
    assert result.mapping[0] == 1
    assert result.mapping[1] == -1
    assert result.unmatched == [1]
    assert result.n_matched == 1


def test_match_is_one_to_one() -> None:
    """Two returning copies of an identical store trace claim distinct rows."""
    store = _store(3, 20, seed=5)
    store[2] = store[0]  # a genuine duplicate in the store
    returned = np.stack([store[0], store[0]])
    result = match_return_leg(returned, store)
    assert sorted(int(x) for x in result.mapping) == [0, 2]
    assert len(set(result.mapping.tolist())) == 2


def test_hint_honoured_only_when_intensity_agrees() -> None:
    """A wrong id hint is overridden by the intensity evidence."""
    store = _store(5, 25, seed=13)
    returned = store[[3]]
    # Hint points at the wrong molecule; intensity must still win.
    wrong = match_return_leg(returned, store, id_hint=[0])
    assert wrong.mapping.tolist() == [3]
    # A correct hint is used.
    right = match_return_leg(returned, store, id_hint=[3])
    assert right.mapping.tolist() == [3]


def test_out_of_range_hint_falls_back_to_search() -> None:
    store = _store(4, 18, seed=2)
    returned = store[[2]]
    result = match_return_leg(returned, store, id_hint=[-1])
    assert result.mapping.tolist() == [2]


def test_tolerance_rejects_near_miss() -> None:
    """A trace perturbed beyond atol does not match (no false identity)."""
    store = _store(3, 20, seed=8)
    returned = store[[1]].copy()
    returned[0, 0, 0] += 1.0  # well beyond the default atol
    result = match_return_leg(returned, store, atol=1e-6)
    assert result.unmatched == [0]


def test_validates_shapes() -> None:
    with pytest.raises(ValueError, match="returned_raw"):
        match_return_leg(np.zeros((2, 10)), np.zeros((2, 10, 2)))
    with pytest.raises(ValueError, match="store_raw"):
        match_return_leg(np.zeros((2, 10, 2)), np.zeros((2, 10, 3)))
