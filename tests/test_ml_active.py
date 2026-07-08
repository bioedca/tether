# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure-core active-learning "recommended next" non-reordering badge (M5, FR-ML; PRD §7.5).

Locks :mod:`tether.ml.active`: the uncertainty-sampling informativeness peaks at the ``p=0.5``
decision boundary and is symmetric about it; :func:`recommend_next` names the single
**uncurated** molecule of maximal uncertainty, breaks ties deterministically on ``molecule_id``,
never picks a curated molecule or an unscored (``NaN``) one, and returns ``None`` rather than a
fabricated pick when nothing remains — all as a pure read that never mutates its inputs (the
non-reordering guarantee). Pure NumPy -> the base CI matrix.
"""

from __future__ import annotations

import pytest

pytest.importorskip("numpy")

import numpy as np  # noqa: E402

from tether.ml.active import BOUNDARY, NextBadge, informativeness, recommend_next  # noqa: E402

# --- informativeness (uncertainty sampling: 1 - |2p - 1|) ---------------------


def test_informativeness_peaks_at_the_decision_boundary() -> None:
    assert BOUNDARY == 0.5
    # Maximal (1.0) at p=0.5, zero at a confident p in {0, 1}.
    assert informativeness([0.5])[0] == pytest.approx(1.0)
    assert informativeness([0.0])[0] == pytest.approx(0.0)
    assert informativeness([1.0])[0] == pytest.approx(0.0)


def test_informativeness_is_symmetric_and_monotone_in_distance_to_boundary() -> None:
    u = informativeness([0.5, 0.7, 0.9, 0.3, 0.1])
    # symmetric about 0.5: p and 1-p are equally informative.
    assert u[1] == pytest.approx(u[3])  # 0.7 vs 0.3
    assert u[2] == pytest.approx(u[4])  # 0.9 vs 0.1
    # monotone decreasing as the score moves away from the boundary.
    assert u[0] > u[1] > u[2]
    assert u.tolist() == pytest.approx([1.0, 0.6, 0.2, 0.6, 0.2])


def test_informativeness_nan_propagates_not_fabricated() -> None:
    # An unscored molecule (NaN, never a fabricated 0) yields NaN informativeness, so it is not
    # comparable and can never be selected — but is never coerced to a finite value.
    u = informativeness([0.5, np.nan, 0.9])
    assert u[0] == pytest.approx(1.0)
    assert np.isnan(u[1])
    assert u[2] == pytest.approx(0.2)


def test_informativeness_rejects_out_of_range_and_infinite_and_non_1d() -> None:
    with pytest.raises(ValueError, match="probabilities in .0, 1."):
        informativeness([0.5, 1.5])
    with pytest.raises(ValueError, match="probabilities in .0, 1."):
        informativeness([-0.1, 0.5])
    with pytest.raises(ValueError, match="finite"):
        informativeness([0.5, np.inf])
    with pytest.raises(ValueError, match="1-D"):
        informativeness([[0.5, 0.5], [0.5, 0.5]])


# --- recommend_next: the uncurated, most-uncertain pick -----------------------


def test_recommend_next_picks_the_most_uncertain_uncurated() -> None:
    ids = ["a", "b", "c", "d"]
    scores = [0.95, 0.55, 0.05, 0.60]  # b (0.55) is nearest the 0.5 boundary
    curated = np.zeros(4, dtype=bool)
    badge = recommend_next(ids, scores, curated=curated)
    assert isinstance(badge, NextBadge)
    assert badge.molecule_id == "b"
    assert badge.score == pytest.approx(0.55)
    assert badge.informativeness == pytest.approx(0.9)


def test_recommend_next_breaks_ties_on_ascending_molecule_id() -> None:
    # Two equally-uncertain candidates (both at 0.5) -> the ascending molecule_id wins,
    # deterministic across platforms (the rank_by_score precedent).
    badge = recommend_next(["m2", "m1"], [0.5, 0.5], curated=np.zeros(2, dtype=bool))
    assert badge is not None
    assert badge.molecule_id == "m1"


def test_recommend_next_never_picks_a_curated_molecule() -> None:
    # "a" is nearest the boundary but already curated -> the still-uncurated "b" is returned.
    curated = np.array([True, False])
    badge = recommend_next(["a", "b"], [0.5, 0.9], curated=curated)
    assert badge is not None
    assert badge.molecule_id == "b"


def test_recommend_next_returns_none_when_all_curated() -> None:
    badge = recommend_next(["a", "b"], [0.5, 0.9], curated=np.array([True, True]))
    assert badge is None


def test_recommend_next_excludes_unscored_and_returns_none_if_no_finite_candidate() -> None:
    # A NaN (unscored) uncurated molecule is not a candidate (can't claim it's most informative),
    # but is never dropped from the set; with no finite-scored uncurated molecule -> None.
    assert recommend_next(["a"], [np.nan], curated=np.array([False])) is None
    # Mixed: the finite-scored uncurated molecule is chosen, the NaN one skipped.
    badge = recommend_next(["a", "b"], [np.nan, 0.6], curated=np.array([False, False]))
    assert badge is not None
    assert badge.molecule_id == "b"


def test_recommend_next_is_a_pure_read_that_does_not_mutate_inputs() -> None:
    # The non-reordering guarantee at the pure layer: recommend_next returns an annotation and
    # touches no ordering — the inputs it was handed come back unchanged.
    ids = ["a", "b", "c"]
    scores = np.array([0.9, 0.5, 0.1])
    curated = np.array([False, False, True])
    scores_before = scores.copy()
    curated_before = curated.copy()
    badge = recommend_next(ids, scores, curated=curated)
    assert badge is not None and badge.molecule_id == "b"
    assert ids == ["a", "b", "c"]
    assert np.array_equal(scores, scores_before)
    assert np.array_equal(curated, curated_before)


def test_recommend_next_validates_alignment_uniqueness_and_mask() -> None:
    curated2 = np.zeros(2, dtype=bool)
    with pytest.raises(ValueError, match="align"):
        recommend_next(["a", "b", "c"], [0.5, 0.5], curated=curated2)
    with pytest.raises(ValueError, match="unique"):
        recommend_next(["a", "a"], [0.5, 0.5], curated=curated2)
    with pytest.raises(ValueError, match="boolean mask"):
        recommend_next(["a", "b"], [0.5, 0.5], curated=np.array([0, 1]))
    with pytest.raises(ValueError, match="boolean mask"):
        recommend_next(["a", "b"], [0.5, 0.5], curated=np.zeros((2, 1), dtype=bool))
    with pytest.raises(ValueError, match="probabilities in .0, 1."):
        recommend_next(["a", "b"], [0.5, 1.7], curated=curated2)
