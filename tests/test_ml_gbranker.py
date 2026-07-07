# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Gradient-boosting quality ranker — the trained scorer (M5, FR-ML; PRD §7.5).

Locks :mod:`tether.ml.gbranker`: the model turns engineered features into a ``P(good)``
score whose ranking is a **never-auto-drop permutation** — every molecule kept, invariant to
input order (oracle (d)) — and, on cleanly separable data, that ranking achieves
**precision@k = 1** and beats the file-order baseline (the precision@k objective, PRD §7.5).
``NaN`` features are scored natively (never imputed / dropped), and a degenerate label set
(one class, or none) is refused loudly. Needs scikit-learn (base lock, #92) -> base CI matrix.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

pytest.importorskip("numpy")
pytest.importorskip("sklearn")

import numpy as np  # noqa: E402

from tether.ml.gbranker import RankerHyperparams, train_quality_ranker  # noqa: E402
from tether.ml.ranking import file_order_ranking, precision_at_k  # noqa: E402

FEATURES = ("f0", "f1", "f2")


def _separable(n_per_class: int = 30, seed: int = 0) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """A cleanly separable 3-feature set: ``n_per_class`` good rows, then that many bad."""
    rng = np.random.default_rng(seed)
    good = rng.normal(6.0, 0.5, size=(n_per_class, 3))
    bad = rng.normal(0.0, 0.5, size=(n_per_class, 3))
    X = np.vstack([good, bad]).astype(np.float64)
    y = np.array([True] * n_per_class + [False] * n_per_class)
    ids = [f"m{i:03d}" for i in range(2 * n_per_class)]
    return X, y, ids


# --- never-auto-drop permutation invariant (oracle (d)) ----------------------


def test_rank_is_a_never_drop_permutation() -> None:
    X, y, ids = _separable()
    ranker = train_quality_ranker(X, y, FEATURES)
    ranked = ranker.rank(ids, X)
    # Every molecule kept exactly once — a permutation, never a filter.
    assert ranked.n == len(ids)
    assert set(ranked.molecule_ids) == set(ids)
    assert len(set(ranked.molecule_ids)) == len(ids)


def test_ranking_is_invariant_to_candidate_order() -> None:
    # The never-auto-drop permutation invariant: permuting the candidate order yields the
    # identical ranking (scoring is row-independent; ties break on molecule_id).
    X, y, ids = _separable()
    ranker = train_quality_ranker(X, y, FEATURES)
    ranked_a = ranker.rank(ids, X)

    perm = np.random.default_rng(7).permutation(len(ids))
    ranked_b = ranker.rank([ids[i] for i in perm], X[perm])

    assert ranked_b.molecule_ids == ranked_a.molecule_ids
    assert ranked_b.scores == pytest.approx(ranked_a.scores)


def test_same_inputs_train_reproducibly() -> None:
    # Identical training data + fixed random_state -> a bit-identical fit.
    X, y, _ = _separable()
    s1 = train_quality_ranker(X, y, FEATURES).score(X)
    s2 = train_quality_ranker(X, y, FEATURES).score(X)
    assert np.array_equal(s1, s2)


def test_training_order_does_not_change_the_ranking() -> None:
    # Permuting the *training* rows must not change which molecules the model ranks best on a
    # cleanly separable set (asserted on the ranking, robust to sub-ULP score differences).
    X, y, ids = _separable()
    ranker_a = train_quality_ranker(X, y, FEATURES)
    perm = np.random.default_rng(3).permutation(len(ids))
    ranker_b = train_quality_ranker(X[perm], y[perm], FEATURES)

    n_good = int(np.count_nonzero(y))
    assert set(ranker_a.rank(ids, X).top(n_good)) == set(ranker_b.rank(ids, X).top(n_good))


# --- precision@k objective (PRD §7.5) ----------------------------------------


def test_precision_at_k_is_one_and_beats_file_order_on_separable_data() -> None:
    X, y, ids = _separable(30, seed=1)
    n = len(ids)
    # Interleave good/bad so plain file order is a deliberately poor ranking (~0.5).
    idx = np.empty(n, dtype=int)
    idx[0::2] = np.arange(30)
    idx[1::2] = np.arange(30, 60)
    Xi, yi, idsi = X[idx], y[idx], [ids[i] for i in idx]
    is_good = {mid: bool(g) for mid, g in zip(idsi, yi.tolist(), strict=True)}
    n_good = int(np.count_nonzero(yi))

    ranker = train_quality_ranker(Xi, yi, FEATURES)
    ranked = ranker.rank(idsi, Xi)
    p_ranker = precision_at_k(ranked.ranked_relevance(is_good), n_good)

    baseline = file_order_ranking(idsi)
    p_base = precision_at_k(baseline.ranked_relevance(is_good), n_good)

    assert p_ranker == pytest.approx(1.0)  # separable -> all good ranked first
    assert p_ranker > p_base  # uplift over the file-order baseline
    assert p_base == pytest.approx(0.5, abs=0.1)  # interleaving really is a poor order


def test_ranker_records_training_balance() -> None:
    X, y, _ = _separable(30)
    ranker = train_quality_ranker(X, y, FEATURES)
    assert ranker.n_train == 60
    assert ranker.n_good == 30
    assert ranker.feature_names == FEATURES


def test_custom_hyperparams_are_honored() -> None:
    X, y, ids = _separable()
    ranker = train_quality_ranker(
        X, y, FEATURES, hyperparams=RankerHyperparams(max_iter=20, random_state=5)
    )
    assert ranker.rank(ids, X).n == len(ids)


# --- NaN features: scored natively, never imputed or dropped -----------------


def test_nan_feature_candidate_is_scored_and_kept() -> None:
    X, y, ids = _separable()
    ranker = train_quality_ranker(X, y, FEATURES)
    # A candidate whose features are undefined (NaN, never a fabricated 0) must still be
    # scored (native missing-value handling) and kept in the ranking, not dropped.
    cand_ids = [*ids, "nan-mol"]
    cand_X = np.vstack([X, np.full((1, 3), np.nan)])
    ranked = ranker.rank(cand_ids, cand_X)

    assert ranked.n == len(cand_ids)
    assert "nan-mol" in ranked.molecule_ids
    assert np.isfinite(ranker.score(cand_X)[-1])


def test_training_tolerates_nan_features() -> None:
    X, y, _ = _separable()
    X = X.copy()
    X[0, 1] = np.nan  # one undefined feature in the training set — must not raise
    ranker = train_quality_ranker(X, y, FEATURES)
    assert np.isfinite(ranker.score(X)).all()


# --- degenerate label sets refused loudly ------------------------------------


def test_single_class_is_refused() -> None:
    X, y, _ = _separable()
    with pytest.raises(ValueError, match="both accepted and rejected"):
        train_quality_ranker(X, np.ones(len(y), dtype=bool), FEATURES)
    with pytest.raises(ValueError, match="both accepted and rejected"):
        train_quality_ranker(X, np.zeros(len(y), dtype=bool), FEATURES)


def test_no_labels_is_refused() -> None:
    with pytest.raises(ValueError, match="no labeled molecules"):
        train_quality_ranker(np.empty((0, 3)), np.empty(0, dtype=bool), FEATURES)


def test_binary_labels_required() -> None:
    X, y, _ = _separable()
    with pytest.raises(ValueError, match="0/1"):
        train_quality_ranker(X, np.full(len(y), 2.0), FEATURES)


# --- shape validation --------------------------------------------------------


def test_mismatched_lengths_raise() -> None:
    X, y, _ = _separable()
    with pytest.raises(ValueError, match="align"):
        train_quality_ranker(X, y[:-1], FEATURES)


def test_wrong_feature_count_raises() -> None:
    X, y, _ = _separable()
    with pytest.raises(ValueError, match="feature_names"):
        train_quality_ranker(X, y, ("f0", "f1"))


def test_score_validates_feature_count() -> None:
    X, y, _ = _separable()
    ranker = train_quality_ranker(X, y, FEATURES)
    with pytest.raises(ValueError, match="feature matrix"):
        ranker.score(X[:, :2])


def test_rank_requires_ids_aligned_to_rows() -> None:
    X, y, ids = _separable()
    ranker = train_quality_ranker(X, y, FEATURES)
    with pytest.raises(ValueError, match="align"):
        ranker.rank(ids[:-1], X)


# --- sample_weight seam (cold-start label weighting, PRD §7.5) ---------------


def test_sample_weight_none_matches_unweighted() -> None:
    # The default (None) fits every labeled molecule at unit weight — behaviourally identical to
    # not passing the kwarg at all.
    X, y, _ = _separable()
    s_default = train_quality_ranker(X, y, FEATURES).score(X)
    s_none = train_quality_ranker(X, y, FEATURES, sample_weight=None).score(X)
    assert np.array_equal(s_default, s_none)


def test_uniform_sample_weight_matches_unweighted() -> None:
    # A uniform weight vector is the same fit as the unweighted default (deterministic HGB).
    X, y, _ = _separable()
    s_unweighted = train_quality_ranker(X, y, FEATURES).score(X)
    s_uniform = train_quality_ranker(X, y, FEATURES, sample_weight=np.ones(len(y))).score(X)
    assert np.array_equal(s_unweighted, s_uniform)


def test_nonuniform_sample_weight_changes_the_fit() -> None:
    # Non-uniform weights actually reach the model: strongly re-weighting the classes changes the
    # learned scores relative to the uniform fit (the seam is live, not ignored).
    X, y, _ = _separable(40, seed=2)
    uniform = train_quality_ranker(X, y, FEATURES, sample_weight=np.ones(len(y))).score(X)
    w = np.where(y, 100.0, 0.01)  # up-weight good, down-weight bad
    weighted = train_quality_ranker(X, y, FEATURES, sample_weight=w).score(X)
    assert not np.allclose(uniform, weighted)


def test_zero_weights_are_allowed_and_train() -> None:
    # Zero is a valid weight (a fully-decayed prior); a mix of zero and positive weights, with both
    # classes still positively weighted, trains a valid never-drop ranking.
    X, y, ids = _separable()
    w = np.ones(len(y))
    w[1] = 0.0  # ignore one good and one bad row
    w[-2] = 0.0
    ranker = train_quality_ranker(X, y, FEATURES, sample_weight=w)
    assert ranker.rank(ids, X).n == len(ids)


def test_sample_weight_wrong_length_raises() -> None:
    X, y, _ = _separable()
    with pytest.raises(ValueError, match="sample_weight must be a 1-D array of length"):
        train_quality_ranker(X, y, FEATURES, sample_weight=np.ones(len(y) - 1))


def test_sample_weight_negative_raises() -> None:
    X, y, _ = _separable()
    w = np.ones(len(y))
    w[0] = -1.0
    with pytest.raises(ValueError, match="non-negative"):
        train_quality_ranker(X, y, FEATURES, sample_weight=w)


@pytest.mark.parametrize("bad", [np.nan, np.inf])
def test_sample_weight_non_finite_raises(bad: float) -> None:
    X, y, _ = _separable()
    w = np.ones(len(y))
    w[0] = bad
    with pytest.raises(ValueError, match="non-finite"):
        train_quality_ranker(X, y, FEATURES, sample_weight=w)


# --- lazy dependency: importing tether.ml stays scikit-learn-free ------------


def test_importing_tether_ml_does_not_load_sklearn() -> None:
    # The sklearn import is lazy (inside train_quality_ranker), so the base import surface
    # stays scikit-learn-free until a ranker is actually trained. Checked in a fresh
    # interpreter because this test module has already imported sklearn.
    code = (
        "import sys, tether.ml; "
        "loaded = sorted(m for m in sys.modules if m == 'sklearn' or m.startswith('sklearn.')); "
        "assert not loaded, loaded"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
