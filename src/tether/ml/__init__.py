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
  total intensity). The store-integrated writer is
  :func:`tether.project.features.compute_features`.
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
"""

from __future__ import annotations

from tether.ml.features import FEATURE_NAMES, TraceFeatures, compute_trace_features
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
    "Neighbor",
    "RankedTraces",
    "SimilarityIndex",
    "TraceFeatures",
    "build_similarity_index",
    "compute_trace_features",
    "file_order_ranking",
    "precision_at_k",
    "precision_at_k_uplift",
    "rank_by_score",
]
