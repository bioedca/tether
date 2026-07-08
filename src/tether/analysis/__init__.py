# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""tether.analysis — histograms, transition-density plots, and statistics (PRD §4.2).

FRET-efficiency histograms with confidence intervals, transition-density plots,
the raw FRET cloud, the anticorrelation-event finder, per-condition population
statistics, and the seven tMAVEN plot types (PRD Appendix C).

Landed so far (M2 S8, FR-ANALYZE):

- :func:`~tether.analysis.histogram.apparent_e_histogram` /
  :func:`~tether.analysis.histogram.population_apparent_e_histogram` — the 1-D
  population apparent-E histogram (Appendix C plot A1).
- :func:`~tether.analysis.crosscorr.cross_correlation` /
  :func:`~tether.analysis.crosscorr.population_cross_correlation` — the
  Pearson-normalized donor–acceptor cross-correlation with a lag-1 magnitude.

Added at M3 (FR-ANALYZE, PRD §7.7):

- :func:`~tether.analysis.histogram.bootstrap_histogram_ci` /
  :func:`~tether.analysis.histogram.population_apparent_e_histogram_ci` — the
  molecule-level bootstrap confidence interval for the histogram (BOBA-FRET
  [König2013]), the FRET histogram's error bars.

Added at M4 (FR-ANNOTATE, PRD §5.1/§7.7):

- :func:`~tether.analysis.query.query_molecules` — the cross-movie condition
  query/filter (by key fields + tags + category), aggregating a condition's
  molecules across all its movies/files.

Added at M6 (FR-ANALYZE, PRD §7.7, Appendix C plot A1):

- :func:`~tether.analysis.histogram.per_condition_apparent_e_histograms` — the
  per-condition overlay: each condition's apparent-E histogram binned on one
  shared axis (density-normalized for cross-condition shape comparison) with its
  molecule-count ``N`` annotation. This is the M6-owned §7.7 "per-condition
  overlays" clause, which only becomes meaningful once the M4 condition model
  exists.
- :func:`~tether.analysis.histogram.model_gaussian_overlay` /
  :func:`~tether.analysis.histogram.population_model_gaussian_overlay` — the A1
  ``model_on`` overlay: the idealized population model's per-state Gaussians
  (``frac·𝒩(mean, var)``) and their sum, drawn on the histogram axis
  [Gopich2010]. The idealized model's own state emissions, not a fresh GMM fit.
- :func:`~tether.analysis.histogram.time_signal_histogram2d` /
  :func:`~tether.analysis.histogram.population_time_signal_histogram2d` — the A2
  2-D time-vs-signal occupancy heatmap (tMAVEN ``data_hist2d.py``), raw /
  start-synchronized mode: each accepted molecule's windowed apparent E binned
  into a ``(time, signal)`` grid so the population's FRET evolution over the
  analysis window is visible [Nettels2024]. The post-synchronized (transition-
  aligned) mode is a follow-up.
"""

from __future__ import annotations

from tether.analysis.crosscorr import (
    CrossCorrelation,
    cross_correlation,
    population_cross_correlation,
)
from tether.analysis.histogram import (
    DEFAULT_BOOTSTRAP_RESAMPLES,
    DEFAULT_CI_LEVEL,
    DEFAULT_NBINS,
    DEFAULT_OVERLAY_POINTS,
    DEFAULT_RANGE,
    DEFAULT_SEED,
    DEFAULT_SIGNAL_BINS,
    DEFAULT_SIGNAL_RANGE,
    DEFAULT_TIME_BINS,
    DEFAULT_TIME_DT,
    ConditionHistogram,
    Histogram1D,
    Histogram2D,
    HistogramBootstrapCI,
    ModelGaussianOverlay,
    PerConditionHistograms,
    apparent_e_histogram,
    bootstrap_histogram_ci,
    model_gaussian_overlay,
    per_condition_apparent_e_histograms,
    population_apparent_e_histogram,
    population_apparent_e_histogram_ci,
    population_model_gaussian_overlay,
    population_time_signal_histogram2d,
    time_signal_histogram2d,
)
from tether.analysis.query import (
    ConditionQueryResult,
    MoleculeMatch,
    query_molecules,
)

__all__ = [
    "DEFAULT_BOOTSTRAP_RESAMPLES",
    "DEFAULT_CI_LEVEL",
    "DEFAULT_NBINS",
    "DEFAULT_OVERLAY_POINTS",
    "DEFAULT_RANGE",
    "DEFAULT_SEED",
    "DEFAULT_SIGNAL_BINS",
    "DEFAULT_SIGNAL_RANGE",
    "DEFAULT_TIME_BINS",
    "DEFAULT_TIME_DT",
    "ConditionHistogram",
    "ConditionQueryResult",
    "CrossCorrelation",
    "Histogram1D",
    "Histogram2D",
    "HistogramBootstrapCI",
    "ModelGaussianOverlay",
    "MoleculeMatch",
    "PerConditionHistograms",
    "apparent_e_histogram",
    "bootstrap_histogram_ci",
    "cross_correlation",
    "model_gaussian_overlay",
    "per_condition_apparent_e_histograms",
    "population_apparent_e_histogram",
    "population_apparent_e_histogram_ci",
    "population_cross_correlation",
    "population_model_gaussian_overlay",
    "population_time_signal_histogram2d",
    "query_molecules",
    "time_signal_histogram2d",
]
