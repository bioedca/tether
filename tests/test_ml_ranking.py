# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure-core quality-ranking evaluation + the never-auto-drop contract (M5, FR-ML; PRD §7.5).

Locks :mod:`tether.ml.ranking`: precision@k is computed to the textbook definition (and its
reviewable-prefix denominator when ``n < k``); the ranking is a **permutation** — every
molecule kept, ``NaN``-scored ones ranked last, ties broken deterministically on
``molecule_id``; and the file-order-baseline uplift responds correctly to a better ordering.
Pure NumPy -> the base CI matrix.
"""

from __future__ import annotations

import pytest

pytest.importorskip("numpy")

import numpy as np  # noqa: E402

from tether.ml.ranking import (  # noqa: E402
    RankedTraces,
    file_order_ranking,
    precision_at_k,
    precision_at_k_uplift,
    rank_by_score,
)

# --- precision@k -------------------------------------------------------------


def test_precision_at_k_textbook() -> None:
    rel = [True, True, False, True, False]  # 3 good of 5, in ranked order
    assert precision_at_k(rel, 1) == pytest.approx(1.0)
    assert precision_at_k(rel, 2) == pytest.approx(1.0)
    assert precision_at_k(rel, 3) == pytest.approx(2 / 3)
    assert precision_at_k(rel, 4) == pytest.approx(3 / 4)
    assert precision_at_k(rel, 5) == pytest.approx(3 / 5)


def test_precision_at_k_accepts_zero_one_ints() -> None:
    # 0/1 ints are a valid relevance encoding, equivalent to the bool array.
    assert precision_at_k([1, 0, 1, 1], 2) == pytest.approx(0.5)
    assert precision_at_k(np.array([1, 0, 1, 1]), 2) == pytest.approx(0.5)


def test_precision_at_k_denominator_is_reviewable_prefix_when_k_exceeds_n() -> None:
    # Fewer than k labeled traces -> divide by the number actually reviewable (n), not k:
    # a perfect short ranking must be able to reach precision 1.0.
    assert precision_at_k([True, True], 5) == pytest.approx(1.0)
    assert precision_at_k([True, False], 10) == pytest.approx(0.5)


@pytest.mark.parametrize("bad_k", [0, -1, -5])
def test_precision_at_k_rejects_nonpositive_k(bad_k: int) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        precision_at_k([True, False], bad_k)


def test_precision_at_k_rejects_bool_k_not_read_as_one() -> None:
    # bool is an int subclass; k=True must NOT be silently read as k=1.
    with pytest.raises(ValueError, match="positive integer"):
        precision_at_k([True, False], True)  # noqa: FBT003


def test_precision_at_k_rejects_empty_and_malformed() -> None:
    with pytest.raises(ValueError, match="empty"):
        precision_at_k([], 3)
    with pytest.raises(ValueError, match="1-D"):
        precision_at_k([[True, False], [False, True]], 2)
    with pytest.raises(ValueError, match="non-finite"):
        precision_at_k([1.0, np.nan, 0.0], 2)
    with pytest.raises(ValueError, match="0/1"):
        precision_at_k([0.0, 0.5, 1.0], 2)


# --- rank_by_score: the never-auto-drop permutation --------------------------


def test_rank_by_score_orders_descending_with_id_tiebreak() -> None:
    ids = ["m3", "m1", "m2", "m0"]
    scores = [0.2, 0.9, 0.5, 0.9]  # m1 and m0 tie at 0.9
    ranked = rank_by_score(ids, scores)
    # ties (m0, m1 at 0.9) break on the ascending molecule_id -> m0 before m1.
    assert ranked.molecule_ids == ("m0", "m1", "m2", "m3")


def test_rank_by_score_ascending() -> None:
    ranked = rank_by_score(["a", "b", "c"], [3.0, 1.0, 2.0], descending=False)
    assert ranked.molecule_ids == ("b", "c", "a")


def test_rank_by_score_all_equal_is_pure_id_order() -> None:
    ranked = rank_by_score(["z", "a", "m"], [0.5, 0.5, 0.5])
    assert ranked.molecule_ids == ("a", "m", "z")  # deterministic across platforms


def test_nan_scored_molecules_ranked_last_and_kept() -> None:
    ids = ["good", "unscored_b", "mid", "unscored_a"]
    scores = [0.9, np.nan, 0.4, np.nan]
    ranked = rank_by_score(ids, scores)
    # finite scores first (by score), then the NaN group last (by ascending id) -> KEPT.
    assert ranked.molecule_ids == ("good", "mid", "unscored_a", "unscored_b")
    assert set(ranked.molecule_ids) == set(ids)  # never-auto-drop
    assert np.isnan(ranked.scores[-1]) and np.isnan(ranked.scores[-2])


def test_rank_by_score_is_a_permutation_invariant() -> None:
    # The load-bearing §7.5 contract: whatever the scores (including NaN and duplicates),
    # the ranking is always a permutation of the input — no molecule is ever dropped.
    rng = np.random.default_rng(0)
    for _ in range(50):
        n = int(rng.integers(1, 40))
        ids = [f"m{i}" for i in range(n)]
        scores = rng.normal(size=n)
        nan_mask = rng.random(n) < 0.25
        scores[nan_mask] = np.nan
        ranked = rank_by_score(ids, scores)
        assert len(ranked.molecule_ids) == n
        assert sorted(ranked.molecule_ids) == sorted(ids)  # exact multiset, nothing lost


def test_rank_by_score_rejects_infinite_score() -> None:
    with pytest.raises(ValueError, match="finite or NaN"):
        rank_by_score(["a", "b"], [1.0, np.inf])


def test_rank_by_score_rejects_length_mismatch_and_dup_ids() -> None:
    with pytest.raises(ValueError, match="aligned"):
        rank_by_score(["a", "b"], [1.0])
    with pytest.raises(ValueError, match="unique"):
        rank_by_score(["a", "a"], [1.0, 2.0])


def test_rank_by_score_empty_is_valid_empty_ranking() -> None:
    ranked = rank_by_score([], [])
    assert ranked.n == 0
    assert ranked.molecule_ids == ()


# --- RankedTraces container ---------------------------------------------------


def test_ranked_traces_top_and_rank_of() -> None:
    ranked = rank_by_score(["a", "b", "c"], [0.1, 0.9, 0.5])
    assert ranked.molecule_ids == ("b", "c", "a")
    assert ranked.top(2) == ["b", "c"]
    assert ranked.top(10) == ["b", "c", "a"]  # min(k, n)
    assert ranked.rank_of("b") == 1
    assert ranked.rank_of("a") == 3
    with pytest.raises(KeyError, match="not in this ranking"):
        ranked.rank_of("missing")
    with pytest.raises(ValueError, match="positive integer"):
        ranked.top(0)


def test_ranked_traces_rejects_dup_ids_and_length_mismatch() -> None:
    with pytest.raises(ValueError, match="unique"):
        RankedTraces(molecule_ids=("a", "a"), scores=(1.0, 2.0))
    with pytest.raises(ValueError, match="same length"):
        RankedTraces(molecule_ids=("a", "b"), scores=(1.0,))


def test_ranked_relevance_uses_ranking_order_and_skips_unlabeled() -> None:
    ranked = rank_by_score(["a", "b", "c", "d"], [0.9, 0.8, 0.7, 0.6])
    is_good = {"a": True, "c": False, "d": True}  # "b" is unlabeled -> skipped
    rel = ranked.ranked_relevance(is_good)
    assert rel.tolist() == [True, False, True]  # a, c, d in ranked order


# --- file-order baseline + uplift --------------------------------------------


def test_file_order_ranking_preserves_order_not_sorted() -> None:
    ids = ["m5", "m1", "m9", "m2"]  # deliberately unsorted
    baseline = file_order_ranking(ids)
    assert baseline.molecule_ids == tuple(ids)  # NOT re-sorted
    # scores descend so the object is a well-formed RankedTraces in the given order.
    assert list(baseline.scores) == sorted(baseline.scores, reverse=True)


def test_file_order_ranking_rejects_dup_ids() -> None:
    with pytest.raises(ValueError, match="unique"):
        file_order_ranking(["a", "a"])


def test_precision_at_k_uplift_rewards_a_better_ordering() -> None:
    # File order interleaves good/bad; a model ranking that surfaces the good ones first
    # must have a strictly positive precision@k uplift.
    ids = ["t0", "t1", "t2", "t3", "t4", "t5"]
    is_good = {"t0": False, "t1": True, "t2": False, "t3": True, "t4": False, "t5": True}
    baseline = file_order_ranking(ids)
    # Higher score to the good traces -> they rank first.
    scores = [0.1, 0.9, 0.2, 0.8, 0.15, 0.85]
    candidate = rank_by_score(ids, scores)
    uplift = precision_at_k_uplift(candidate, baseline, is_good, k=3)
    assert candidate.molecule_ids[:3] == ("t1", "t5", "t3")
    assert precision_at_k(candidate.ranked_relevance(is_good), 3) == pytest.approx(1.0)
    assert precision_at_k(baseline.ranked_relevance(is_good), 3) == pytest.approx(1 / 3)
    assert uplift == pytest.approx(1.0 - 1 / 3)


def test_precision_at_k_uplift_zero_when_identical() -> None:
    ids = ["a", "b", "c"]
    is_good = {"a": True, "b": False, "c": True}
    baseline = file_order_ranking(ids)
    assert precision_at_k_uplift(baseline, baseline, is_good, k=2) == pytest.approx(0.0)


def test_precision_at_k_uplift_requires_same_labeled_set() -> None:
    baseline = file_order_ranking(["a", "b", "c"])
    candidate = rank_by_score(["a", "b"], [0.5, 0.9])  # missing "c"
    with pytest.raises(ValueError, match="same labeled molecules"):
        precision_at_k_uplift(candidate, baseline, {"a": True, "c": False}, k=2)
