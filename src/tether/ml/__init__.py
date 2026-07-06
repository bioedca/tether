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
"""

from __future__ import annotations

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
from tether.ml.ranking import (
    RankedTraces,
    file_order_ranking,
    precision_at_k,
    precision_at_k_uplift,
    rank_by_score,
)
from tether.ml.similarity import Neighbor, SimilarityIndex, build_similarity_index

__all__ = [
    "FEATURE_NAMES",
    "MODEL_FORMAT_VERSION",
    "SPATIAL_FEATURE_NAMES",
    "TRACE_FEATURE_NAMES",
    "CorruptModelError",
    "Neighbor",
    "PortableModelError",
    "PortableRankerModel",
    "QualityRanker",
    "RankedTraces",
    "RankerHyperparams",
    "SimilarityIndex",
    "SpatialFeatures",
    "TraceFeatures",
    "UnsupportedModelFormatError",
    "build_similarity_index",
    "compute_spatial_features",
    "compute_trace_features",
    "file_order_ranking",
    "load_model",
    "precision_at_k",
    "precision_at_k_uplift",
    "rank_by_score",
    "save_model",
    "train_portable_model",
    "train_quality_ranker",
    "warm_start_retrain",
]
