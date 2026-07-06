# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Gradient-boosting quality ranker — the precision@k objective (PRD §7.5; FR-ML).

The trained scorer the M5 quality ranker is built on (PRD §7.5, PLAN §9 M5): a
gradient-boosting model [Chen2016] fitted on a condition's human accept/reject
``/labels`` that turns each molecule's engineered :mod:`tether.ml.features` vector into a
scalar **quality score**, which :func:`tether.ml.ranking.rank_by_score` orders into a
never-auto-drop ranking. It plugs its scores into the model-free evaluation substrate
(:mod:`tether.ml.ranking` — ``rank_by_score`` / ``precision_at_k``) landed independently in
the prior PR, so the metric and the ordering contract never import the model.

Why rank by ``P(good)``. The ranker optimizes **precision@k** — the fraction of good traces
among the first ``k`` reviewed (PRD §7.5). By the **Probability Ranking Principle**
[Robertson1977], ordering items by decreasing probability of relevance is optimal for a
precision-based objective; here relevance is "a human would accept this trace," so a
calibrated ``P(good)`` from a binary classifier *is* the precision@k-optimal score.
Engineered-feature quality classification is the field-standard route to automated smFRET
trace selection (AutoSiM [Li2020], DeepFRET [Thomsen2020], Deep-LASI [Wanninger2023] all
sort/select traces on features of exactly this kind), and PRD §7.5 names a gradient-boosting
model specifically.

Model choice — ``HistGradientBoostingClassifier``. Both scikit-learn's histogram gradient
boosting and XGBoost [Chen2016] are in the base lock (added in #92) and either satisfies the
"gradient-boosting ranker" spec; this module uses scikit-learn's
:class:`~sklearn.ensemble.HistGradientBoostingClassifier` for three properties this problem
needs (ADR-0034):

* **Native ``NaN`` support** at both fit and predict. The engineered features are ``NaN``
  where undefined (a window too short, a lone molecule's ``neighbor_distance`` — never a
  fabricated ``0``; :mod:`tether.ml.features`), so a model that ingests ``NaN`` directly and
  *learns* a split direction for missingness is the exact complement of the never-fabricate
  discipline — no imputation (which would fabricate the very value the feature layer refused
  to). This is why a molecule with undefined features can still be **scored and ranked** here,
  unlike :mod:`tether.ml.similarity`, which cannot embed it in a metric space and so reports
  it as unindexed.
* **Determinism** given ``random_state`` (which seeds the histogram-binning subsample — a
  draw that only occurs far above the curation regime; early stopping off), so a ranking is
  reproducible.
* **``sample_weight``** in ``fit`` — the seam the later label-weighting / cold-start-decay PR
  (PRD §7.5 ``w = w₀/(1+n_human)``) needs, with no model change.

Scope (this PR — PLAN §9 M5 "gradient-boosting ranker"). The **trained scorer + the
never-auto-drop ranking + apparent precision@k** only. Persistence as a portable artifact
(load / warm-start-retrain / save), per-label weighting, the **prequential**
median-across-videos ship gate, cross-condition seeding, and the active-learning badge are
each their own later PR. In particular the precision@k this module can report over the
training labels is **apparent (in-sample)** and is *not* the ship gate — the honest held-out
prequential evaluation lands in the prequential PR (PRD §7.5; oracle (d)).

Never-auto-drop. :meth:`QualityRanker.rank` is a permutation of the molecule set — every
molecule is scored and kept, ordering is invariant to the input order (a pure function of the
fitted model and each row's features, tie-broken on ``molecule_id`` by
:func:`~tether.ml.ranking.rank_by_score`), and a molecule that cannot be scored is ranked
last, never removed (PRD §7.5 "sort/rank only, never auto-drop").

References
----------
[Chen2016] Chen T. & Guestrin C. "XGBoost: A Scalable Tree Boosting System." KDD (2016) —
    the gradient-boosting family PRD §7.5 names for the quality ranker.
[Robertson1977] Robertson S.E. "The probability ranking principle in IR." Journal of
    Documentation 33(4):294–304 (1977) — ranking by decreasing probability of relevance is
    optimal for precision-based retrieval, the justification for ranking on ``P(good)``.
[Li2020] Li, Zhang, Johnson-Buck & Walter. "Automatic classification and segmentation of
    single-molecule fluorescence time traces with deep learning." Nature Communications
    (2020) — AutoSiM smFRET trace selection.
[Thomsen2020] Thomsen et al. "DeepFRET, a software for rapid and automated single-molecule
    FRET data classification using deep learning." eLife (2020).
[Wanninger2023] Wanninger et al. "Deep-LASI: deep-learning assisted, single-molecule imaging
    analysis of multi-color DNA origami structures." Nature Communications (2023).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from tether.ml.ranking import RankedTraces, rank_by_score

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["RankerHyperparams", "QualityRanker", "train_quality_ranker"]


@dataclass(frozen=True)
class RankerHyperparams:
    """The gradient-boosting quality-ranker hyperparameters (registered in PRD §11.2).

    Defaults tuned for the **curation regime** — a condition accumulates tens to a few
    hundred human-labeled traces, not the tens of thousands scikit-learn's stock defaults
    assume — so the leaf/regularization defaults are smaller (``min_samples_leaf`` 5 vs 20,
    ``max_leaf_nodes`` 15 vs 31) to let the model split on modest label sets while some
    ``l2_regularization`` guards against overfitting a small sample. Early stopping is **off**:
    a validation hold-out is wasteful on a small curation set and would make the fit depend on
    the split, so every ``max_iter`` tree is fit deterministically. ``random_state`` fixes the
    binning so a ranking is reproducible.
    """

    learning_rate: float = 0.1
    max_iter: int = 100
    max_leaf_nodes: int = 15
    min_samples_leaf: int = 5
    l2_regularization: float = 1.0
    early_stopping: bool = False
    random_state: int = 0


#: The default hyperparameters (the PRD §11.2 "Quality-ranker model" row).
DEFAULT_HYPERPARAMS = RankerHyperparams()


def _as_labels(y: object) -> np.ndarray:
    """Coerce ``y`` to a 1-D boolean accept/reject array (``True`` = good/accepted).

    Accept/reject is a *known* human label, never an undefined feature, so a non-finite or
    non-0/1 entry is a caller error surfaced loudly (mirrors
    :func:`tether.ml.ranking._as_relevance`). Coercing to ``bool`` fixes the class order so
    the fitted model's ``classes_`` is ``[False, True]`` and the "good" probability column is
    unambiguous.
    """
    arr = np.asarray(y)
    if arr.ndim != 1:
        raise ValueError(f"labels must be 1-D, got shape {arr.shape}")
    if arr.dtype == bool:
        return arr
    values = arr.astype(np.float64)
    if not bool(np.isfinite(values).all()):
        raise ValueError("labels have non-finite entries; expected a boolean 0/1 array")
    if not bool(np.isin(values, (0.0, 1.0)).all()):
        raise ValueError("labels must be a boolean 0/1 array (True = accepted/good)")
    return values.astype(bool)


# ``eq=False`` -> identity equality/hash. This is a handle-like object wrapping a fitted
# scikit-learn estimator and a feature-name tuple; a dataclass-generated ``__eq__`` would try
# to compare the estimator (no meaningful ``==``) and ``__hash__`` would be built from it.
# Identity semantics are the right contract (the :mod:`tether.ml.similarity` precedent).
@dataclass(frozen=True, eq=False)
class QualityRanker:
    """A fitted gradient-boosting quality model that scores traces by ``P(good)``.

    Built by :func:`train_quality_ranker`. :meth:`score` returns each row's probability of
    being a good (human-accepted) trace; :meth:`rank` orders molecules by that score into a
    never-auto-drop :class:`~tether.ml.ranking.RankedTraces`.

    Attributes
    ----------
    feature_names:
        The feature-column order the model was trained on; ``score``/``rank`` inputs must
        present columns in this order (the :data:`tether.ml.features.FEATURE_NAMES` layout).
    n_train, n_good:
        The number of labeled training molecules and how many were accepted (good) — the
        class balance the fit saw, surfaced for provenance/diagnostics.
    """

    feature_names: tuple[str, ...]
    n_train: int
    n_good: int
    _model: object = field(repr=False)
    _good_col: int = field(repr=False)

    def _as_matrix(self, X: object) -> np.ndarray:
        matrix = np.asarray(X, dtype=np.float64)
        if matrix.ndim != 2 or matrix.shape[1] != len(self.feature_names):
            raise ValueError(
                f"feature matrix must be (n, {len(self.feature_names)}) to match the trained "
                f"feature_names, got shape {matrix.shape}"
            )
        return matrix

    def score(self, X: object) -> np.ndarray:
        """``P(good)`` per row (higher = better quality) — a ``float64`` vector.

        ``X`` is an ``(n, n_features)`` matrix in :attr:`feature_names` order. ``NaN`` feature
        entries are handled natively by the model (never imputed), so every row — including a
        molecule with undefined features — gets a real, learned score.
        """
        matrix = self._as_matrix(X)
        proba = np.asarray(self._model.predict_proba(matrix), dtype=np.float64)
        return proba[:, self._good_col]

    def rank(self, molecule_ids: Sequence[str], X: object) -> RankedTraces:
        """Rank molecules by ``P(good)`` — a never-auto-drop permutation (PRD §7.5).

        Scores every molecule (row-independent, so the ranking is invariant to the input
        order) and hands the scores to :func:`~tether.ml.ranking.rank_by_score`: highest
        quality first, ties broken on ``molecule_id``, every molecule kept. A model score is
        always a finite probability, so no molecule is ranked ``NaN``-last here — but the
        contract still holds by construction (nothing is dropped).

        Raises
        ------
        ValueError
            ``molecule_ids`` and ``X`` disagree in length, ``X`` has the wrong feature count,
            or ``molecule_ids`` are not unique (from :func:`rank_by_score`).
        """
        ids = [str(m) for m in molecule_ids]
        matrix = self._as_matrix(X)
        if matrix.shape[0] != len(ids):
            raise ValueError(
                f"molecule_ids ({len(ids)}) and feature rows ({matrix.shape[0]}) must align"
            )
        return rank_by_score(ids, self.score(matrix), descending=True)


def train_quality_ranker(
    X: object,
    y: object,
    feature_names: Sequence[str],
    *,
    hyperparams: RankerHyperparams | None = None,
) -> QualityRanker:
    """Fit the gradient-boosting quality ranker on human accept/reject labels (PRD §7.5).

    Parameters
    ----------
    X:
        ``(n_labeled, n_features)`` feature matrix of the **labeled** molecules only, in
        ``feature_names`` order. ``NaN`` entries are allowed (handled natively).
    y:
        ``(n_labeled,)`` boolean/0-1 accept (``True`` = good) / reject (``False`` = bad)
        labels aligned to ``X``.
    feature_names:
        The feature-column order (stored on the returned model so ``score``/``rank`` can
        validate their inputs).
    hyperparams:
        Override the :data:`DEFAULT_HYPERPARAMS` (PRD §11.2).

    Returns
    -------
    QualityRanker
        The fitted scorer.

    Raises
    ------
    ValueError
        ``X`` is not 2-D with ``len(feature_names)`` columns; ``X``/``y`` lengths disagree;
        there are no labeled molecules; or only one class is present (a discriminative ranker
        needs both accepted **and** rejected examples — surfaced loudly, not silently fit to a
        constant).
    """
    # Lazy import: keeps ``import tether.ml`` free of the scikit-learn (base-lock) dependency
    # until a model is actually trained.
    from sklearn.ensemble import HistGradientBoostingClassifier

    names = tuple(str(n) for n in feature_names)
    matrix = np.asarray(X, dtype=np.float64)
    if matrix.ndim != 2 or matrix.shape[1] != len(names):
        raise ValueError(
            f"X must be (n_labeled, {len(names)}) to match feature_names, got shape {matrix.shape}"
        )
    labels = _as_labels(y)
    if labels.shape[0] != matrix.shape[0]:
        raise ValueError(
            f"X has {matrix.shape[0]} rows but y has {labels.shape[0]} labels; they must align"
        )
    if labels.shape[0] == 0:
        raise ValueError("cannot train a quality ranker with no labeled molecules")
    if int(np.unique(labels).shape[0]) < 2:
        only = "accepted" if bool(labels[0]) else "rejected"
        raise ValueError(
            "quality ranker needs both accepted and rejected examples to learn a ranking; "
            f"got only {only} labels"
        )

    hp = hyperparams if hyperparams is not None else DEFAULT_HYPERPARAMS
    model = HistGradientBoostingClassifier(
        learning_rate=hp.learning_rate,
        max_iter=hp.max_iter,
        max_leaf_nodes=hp.max_leaf_nodes,
        min_samples_leaf=hp.min_samples_leaf,
        l2_regularization=hp.l2_regularization,
        early_stopping=hp.early_stopping,
        random_state=hp.random_state,
    )
    model.fit(matrix, labels)
    # classes_ is [False, True] (labels coerced to bool); the good-probability column is
    # whichever position True lands in — read it explicitly rather than assuming column 1.
    good_col = int(np.flatnonzero(np.asarray(model.classes_) == True)[0])  # noqa: E712
    return QualityRanker(
        feature_names=names,
        n_train=int(labels.shape[0]),
        n_good=int(np.count_nonzero(labels)),
        _model=model,
        _good_col=good_col,
    )
