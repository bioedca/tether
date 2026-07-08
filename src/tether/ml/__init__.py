# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""tether.ml — per-condition incrementally-retrained quality ranker (PRD §4.2).

Per-condition, persistent, incrementally-retrained feature extraction and
classifier that sorts/ranks trace quality (and never auto-drops); similarity
search; the active-learning loop; and model load / warm-start-retrain / save as
a portable artifact. Deep (GPU) models arrive later (PRD M8).

Landed so far (M5, FR-ML):

- :func:`~tether.ml.features.compute_trace_features` /
  :class:`~tether.ml.features.TraceFeatures` — the engineered per-trace quality
  features (the trace-derived block: SNR, FRET mean/variance, anticorrelation,
  total intensity), plus :func:`~tether.ml.features.compute_spatial_features` /
  :class:`~tether.ml.features.SpatialFeatures` — the spatial crowding block
  (nearest-neighbour distance + the second-molecule-in-aperture flag). The
  store-integrated writer of both is :func:`tether.project.features.compute_features`.
- :func:`~tether.ml.similarity.build_similarity_index` /
  :class:`~tether.ml.similarity.SimilarityIndex` — feature-space nearest-neighbour
  retrieval ("find traces like these") over the standardized ``/features`` vectors;
  rank/sort only, never auto-drop. The store-integrated entry point is
  :func:`tether.project.features.similar_molecules`.
- :func:`~tether.ml.ranking.precision_at_k` / :func:`~tether.ml.ranking.rank_by_score` /
  :class:`~tether.ml.ranking.RankedTraces` — the quality-ranker evaluation metric
  (precision@k, PRD §7.5) and the never-auto-drop ranking contract (a permutation, never a
  filter), both independent of which model produces the scores. The store-integrated entry
  point is :func:`tether.project.ranking.baseline_precision_at_k`.
- :func:`~tether.ml.gbranker.train_quality_ranker` / :class:`~tether.ml.gbranker.QualityRanker`
  — the gradient-boosting scorer that turns engineered features into a ``P(good)`` quality
  score and plugs it into ``rank_by_score`` (precision@k objective, never auto-drop). The
  store-integrated entry point is :func:`tether.project.gbranking.ranker_ranking`. (Importing
  :mod:`tether.ml` stays free of scikit-learn; the dependency loads only when a ranker is
  trained.)
- :func:`~tether.ml.persistence.train_portable_model` /
  :func:`~tether.ml.persistence.warm_start_retrain` / :func:`~tether.ml.persistence.save_model` /
  :func:`~tether.ml.persistence.load_model` / :class:`~tether.ml.persistence.PortableRankerModel`
  — the persistent, portable per-condition model artifact (PRD §7.5, UC3): a standalone versioned
  file that carries a fitted ranker + provenance across experiment files and is
  warm-start-retrained video-by-video. Store-integration (the ``/models`` reference + its own
  owner-curator single-writer lock) lands in a later PR.
- :func:`~tether.ml.prequential.prequential_uplift` /
  :class:`~tether.ml.prequential.PrequentialResult` / :class:`~tether.ml.prequential.VideoFold`
  — the prequential (interleaved test-then-train) precision@k **uplift ship gate** (PRD §7.5,
  §9 M5; oracle (d)): the honest, held-out, median-across-videos evaluation the ranker must
  clear to ship, as opposed to the optimistic in-sample apparent precision@k. Model-free (takes
  a train/score callback); the store-integrated entry point is
  :func:`tether.project.prequential.ranker_prequential_uplift`.
- :func:`~tether.ml.drift.condition_drift` / :class:`~tether.ml.drift.DriftReport` — the
  cross-condition **feature-distribution drift advisory** (PRD §7.5, §9 M5): a two-sample
  Kolmogorov–Smirnov sweep with a Bonferroni family-wise correction that raises an advisory
  (overridable) flag when a condition's model is seeded from a distributionally dissimilar one.
  Model-free; the store entry point is :func:`tether.project.drift.cross_condition_drift`.
- :func:`~tether.ml.active.recommend_next` / :func:`~tether.ml.active.informativeness` /
  :class:`~tether.ml.active.NextBadge` — the active-learning **"recommended next"
  non-reordering badge** (PRD §7.5, §9 M5): uncertainty sampling names the single uncurated
  molecule of maximal predictive uncertainty (``P(good)`` nearest the ``0.5`` boundary) as a
  cue over the fixed within-video sweep, never reordering it. Model-free; the store entry point
  is :func:`tether.project.active.next_recommendation`.
"""

from __future__ import annotations

from tether.ml.active import BOUNDARY, NextBadge, informativeness, recommend_next
from tether.ml.drift import (
    DEFAULT_DRIFT_ALPHA,
    DriftReport,
    FeatureDrift,
    condition_drift,
)
from tether.ml.features import (
    FEATURE_NAMES,
    SPATIAL_FEATURE_NAMES,
    TRACE_FEATURE_NAMES,
    SpatialFeatures,
    TraceFeatures,
    compute_spatial_features,
    compute_trace_features,
)
from tether.ml.gbranker import QualityRanker, RankerHyperparams, train_quality_ranker
from tether.ml.persistence import (
    MODEL_FORMAT_VERSION,
    CorruptModelError,
    PortableModelError,
    PortableRankerModel,
    UnsupportedModelFormatError,
    load_model,
    save_model,
    train_portable_model,
    warm_start_retrain,
)
from tether.ml.prequential import (
    DEFAULT_SHIP_BAR_PTS,
    PrequentialResult,
    VideoFold,
    VideoUplift,
    prequential_uplift,
)
from tether.ml.ranking import (
    RankedTraces,
    file_order_ranking,
    precision_at_k,
    precision_at_k_uplift,
    rank_by_score,
)
from tether.ml.similarity import Neighbor, SimilarityIndex, build_similarity_index

__all__ = [
    "BOUNDARY",
    "DEFAULT_DRIFT_ALPHA",
    "DEFAULT_SHIP_BAR_PTS",
    "FEATURE_NAMES",
    "MODEL_FORMAT_VERSION",
    "SPATIAL_FEATURE_NAMES",
    "TRACE_FEATURE_NAMES",
    "CorruptModelError",
    "DriftReport",
    "FeatureDrift",
    "Neighbor",
    "NextBadge",
    "PortableModelError",
    "PortableRankerModel",
    "PrequentialResult",
    "QualityRanker",
    "RankedTraces",
    "RankerHyperparams",
    "SimilarityIndex",
    "SpatialFeatures",
    "TraceFeatures",
    "UnsupportedModelFormatError",
    "VideoFold",
    "VideoUplift",
    "build_similarity_index",
    "compute_spatial_features",
    "compute_trace_features",
    "condition_drift",
    "file_order_ranking",
    "informativeness",
    "load_model",
    "precision_at_k",
    "precision_at_k_uplift",
    "prequential_uplift",
    "rank_by_score",
    "recommend_next",
    "save_model",
    "train_portable_model",
    "train_quality_ranker",
    "warm_start_retrain",
]
