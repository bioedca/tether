# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure-core prequential precision@k uplift gate (M5, FR-ML; PRD §7.5, §9 M5).

Locks :mod:`tether.ml.prequential`: the interleaved test-then-train protocol scores each video
with the model trained on the videos already seen (never on itself), reports the first video
and single-class-prior videos as *skipped* rather than fabricating a ``0`` uplift, aggregates
the held-out uplifts by their **median** into a ship verdict, and refuses (loudly) to invent a
number when no video can be held-out evaluated. Model-free (a fake scorer) -> pure NumPy on the
base CI matrix.
"""

from __future__ import annotations

import pytest

pytest.importorskip("numpy")

import numpy as np  # noqa: E402

from tether.ml.prequential import (  # noqa: E402
    DEFAULT_SHIP_BAR_PTS,
    PrequentialResult,
    VideoFold,
    VideoUplift,
    prequential_uplift,
)

# --- fake scorers (the train_score_fn seam) ----------------------------------


def _perfect(_x_prior: np.ndarray, _y_prior: np.ndarray):
    """Ignores training: scores each row by its first feature column (higher = better)."""
    return lambda x: np.asarray(x, dtype=np.float64)[:, 0]


def _identity_order(_x_prior: np.ndarray, _y_prior: np.ndarray):
    """Scores rows in strictly-descending file order -> ranker == file-order baseline."""
    return lambda x: -np.arange(np.asarray(x).shape[0], dtype=np.float64)


def _fold(movie_id: str, quals: list[float], goods: list[bool]) -> VideoFold:
    """A fold whose single feature column is ``quals`` and truth is ``goods`` (store order)."""
    ids = tuple(f"{movie_id}-{i}" for i in range(len(goods)))
    x = np.asarray([[q] for q in quals], dtype=np.float64)
    return VideoFold(movie_id=movie_id, molecule_ids=ids, X=x, is_good=tuple(goods))


# goods LAST in file order -> a poor file-order baseline a perfect ranker beats.
def _goods_last(movie_id: str) -> VideoFold:
    return _fold(movie_id, quals=[0.0, 0.0, 1.0, 1.0], goods=[False, False, True, True])


# --- PrequentialResult aggregation (median / shipped) ------------------------


def test_default_ship_bar_is_ten_points() -> None:
    assert DEFAULT_SHIP_BAR_PTS == 10.0  # the PRD §11.2 default, exact


def test_median_odd_count() -> None:
    r = PrequentialResult(
        k=5,
        ship_bar_pts=10.0,
        per_video=(
            VideoUplift("a", 4, 1.0, 0.5, 0.5),
            VideoUplift("b", 4, 0.8, 0.7, 0.1),
            VideoUplift("c", 4, 0.6, 0.6, 0.0),
        ),
        skipped_movie_ids=("z",),
    )
    assert r.n_evaluated == 3
    assert r.median_uplift_pts == pytest.approx(10.0)  # median(0.5, 0.1, 0.0) = 0.1 -> 10 pts
    assert r.shipped is True  # >= the 10-pt bar


def test_median_even_count() -> None:
    r = PrequentialResult(
        k=5,
        ship_bar_pts=10.0,
        per_video=(VideoUplift("a", 4, 1.0, 1.0, 0.0), VideoUplift("b", 4, 1.0, 0.6, 0.4)),
        skipped_movie_ids=(),
    )
    assert r.median_uplift_pts == pytest.approx(20.0)  # mean of the two middles: (0.0+0.4)/2
    assert r.shipped is True


def test_ship_bar_boundary_is_inclusive() -> None:
    at_bar = PrequentialResult(5, 10.0, (VideoUplift("a", 4, 0.6, 0.5, 0.10),), ())
    just_under = PrequentialResult(5, 10.0, (VideoUplift("a", 4, 0.6, 0.5, 0.099),), ())
    assert at_bar.shipped is True  # exactly 10 pts ships
    assert just_under.shipped is False


def test_custom_ship_bar() -> None:
    r = PrequentialResult(5, 50.0, (VideoUplift("a", 4, 0.8, 0.4, 0.4),), ())
    assert r.median_uplift_pts == pytest.approx(40.0)
    assert r.shipped is False  # 40 < a stricter 50-pt bar


# --- the interleaved test-then-train harness ---------------------------------


def test_first_video_skipped_not_scored_against_nothing() -> None:
    # v1 has both classes (so v2/v3 have a trainable prior) but is itself never a held-out test.
    folds = [
        _fold("v1", [0.0, 1.0], [False, True]),
        _goods_last("v2"),
        _goods_last("v3"),
    ]
    result = prequential_uplift(folds, _perfect, k=2)
    assert [v.movie_id for v in result.per_video] == ["v2", "v3"]  # v1 never evaluated...
    assert result.skipped_movie_ids == ("v1",)  # ...reported skipped, not a fabricated 0
    assert result.n_evaluated == 2


def test_perfect_ranker_beats_file_order() -> None:
    folds = [_fold("v1", [0.0, 1.0], [False, True]), _goods_last("v2"), _goods_last("v3")]
    result = prequential_uplift(folds, _perfect, k=2)
    for v in result.per_video:
        assert v.ranker == pytest.approx(1.0)  # goods-first
        assert v.baseline == pytest.approx(0.0)  # goods-last in file order
        assert v.uplift == pytest.approx(1.0)
        assert v.n_labeled == 4  # every labeled molecule used
    assert result.median_uplift_pts == pytest.approx(100.0)
    assert result.shipped is True


def test_ranker_equal_to_file_order_is_zero_uplift_not_shipped() -> None:
    folds = [_fold("v1", [0.0, 1.0], [False, True]), _goods_last("v2"), _goods_last("v3")]
    result = prequential_uplift(folds, _identity_order, k=2)
    assert all(v.uplift == pytest.approx(0.0) for v in result.per_video)
    assert result.median_uplift_pts == pytest.approx(0.0)
    assert result.shipped is False  # no uplift -> does not clear the bar


def test_single_class_prior_video_is_skipped() -> None:
    # v1 all-accepted (single class) -> v2's prior cannot train; v2 both classes -> v3's prior can.
    folds = [
        _fold("v1", [1.0, 1.0], [True, True]),  # single-class -> not a usable prior
        _goods_last("v2"),  # prior = v1 only (single class) -> skipped
        _goods_last("v3"),  # prior = v1 + v2 (both classes now) -> evaluated
    ]
    result = prequential_uplift(folds, _perfect, k=2)
    assert set(result.skipped_movie_ids) == {"v1", "v2"}
    assert [v.movie_id for v in result.per_video] == ["v3"]


def test_no_trainable_prior_raises_never_fabricates() -> None:
    # A single video can never be held-out tested against a prior.
    with pytest.raises(ValueError, match="no held-out video had a trainable prior"):
        prequential_uplift([_goods_last("only")], _perfect, k=2)
    # Every video single-class -> no prior ever trains.
    all_good = [_fold(f"v{i}", [1.0, 1.0], [True, True]) for i in range(3)]
    with pytest.raises(ValueError, match="no held-out video had a trainable prior"):
        prequential_uplift(all_good, _perfect, k=2)


def test_empty_folds_raises() -> None:
    with pytest.raises(ValueError, match="no video folds"):
        prequential_uplift([], _perfect, k=2)


@pytest.mark.parametrize("bad_k", [0, -1, 2.0, True])
def test_k_must_be_positive_int(bad_k) -> None:
    folds = [_fold("v1", [0.0, 1.0], [False, True]), _goods_last("v2")]
    with pytest.raises(ValueError, match="positive integer"):
        prequential_uplift(folds, _perfect, k=bad_k)


def test_scorer_misshapen_scores_raises() -> None:
    def _wrong_length(_xp, _yp):
        return lambda x: np.zeros(np.asarray(x).shape[0] + 1)  # one score too many

    folds = [_fold("v1", [0.0, 1.0], [False, True]), _goods_last("v2")]
    with pytest.raises(ValueError, match="must align"):
        prequential_uplift(folds, _wrong_length, k=2)


def test_deterministic_across_runs() -> None:
    folds = [_fold("v1", [0.0, 1.0], [False, True]), _goods_last("v2"), _goods_last("v3")]
    a = prequential_uplift(folds, _perfect, k=2)
    b = prequential_uplift(folds, _perfect, k=2)
    assert a == b  # frozen dataclasses with float/str/tuple fields compare by value


def test_result_round_trips_k_and_ship_bar() -> None:
    folds = [_fold("v1", [0.0, 1.0], [False, True]), _goods_last("v2")]
    result = prequential_uplift(folds, _perfect, k=3, ship_bar_pts=25.0)
    assert result.k == 3
    assert result.ship_bar_pts == pytest.approx(25.0)


def test_prior_expands_across_all_prior_videos() -> None:
    # The prior handed to train_score_fn must be the *accumulated* labels of every prior video
    # (the expanding window), not just the immediately-previous one.
    seen_prior_sizes: list[int] = []

    def _recording(x_prior: np.ndarray, _y_prior: np.ndarray):
        seen_prior_sizes.append(int(np.asarray(x_prior).shape[0]))
        return lambda x: np.asarray(x, dtype=np.float64)[:, 0]

    folds = [
        _fold("v1", [0.0, 1.0], [False, True]),  # 2 rows
        _fold("v2", [0.0, 0.0, 1.0], [False, False, True]),  # 3 rows
        _goods_last("v3"),
    ]
    prequential_uplift(folds, _recording, k=2)
    # v2 trained on v1 (2), v3 trained on v1+v2 (2+3=5): expanding, not sliding.
    assert seen_prior_sizes == [2, 5]
