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
  analysis window is visible [Nettels2024].
- :func:`~tether.analysis.histogram.transition_sync_histogram2d` /
  :func:`~tether.analysis.histogram.population_transition_sync_histogram2d` — the
  A2 post-synchronized (transition-aligned) heatmap: align each molecule's Viterbi
  state transitions to a common column and bin the observed apparent E around them,
  so the population's average approach-to and departure-from a state jump is visible
  (a transition-synchronized ensemble average [Blackwell2020]).
- :func:`~tether.analysis.tdp.transition_density` /
  :func:`~tether.analysis.tdp.population_transition_density` — the B1 real Transition
  Density Plot: the initial-vs-final idealized-FRET density over state-change frames of
  a population's *fresh* idealizations [McKinney2006].
- :func:`~tether.analysis.dwell.state_dwells` /
  :func:`~tether.analysis.dwell.survival_curve` /
  :func:`~tether.analysis.dwell.fit_survival` /
  :func:`~tether.analysis.dwell.population_dwell_times` — the B2 dwell-time analysis:
  per-state dwell-length distributions, their empirical survival function, and its
  exponential-fit exit rates with confidence intervals + residuals [Schrangl2024].
- :func:`~tether.analysis.transition_prob.empirical_transition_probability` /
  :func:`~tether.analysis.transition_prob.transition_prob_histogram` /
  :func:`~tether.analysis.transition_prob.population_transition_prob_histogram` — the
  B3 transition-probability histogram: the population of per-molecule maximum-likelihood
  one-step ``P(init → fin)`` rates estimated from each Viterbi path (the empirical
  analogue of tMAVEN's per-trace ``norm_tmatrix``), binned with an optional Gaussian-KDE
  [McKinney2006].
- :func:`~tether.analysis.state_number.occupied_state_count` /
  :func:`~tether.analysis.state_number.state_number_counts` /
  :func:`~tether.analysis.state_number.population_state_number` — the C1 state-number
  bar chart: molecule counts by the number of **distinct states each Viterbi path
  occupies** (the consensus-model analogue of tMAVEN's per-trace vbFRET state count)
  [vandeMeent2014].
- :func:`~tether.analysis.cloud.raw_fret_cloud` /
  :func:`~tether.analysis.cloud.population_raw_fret_cloud` — the raw FRET cloud QC view
  (PR-5a): the pooled pre-idealization ``(time, apparent-E)`` scatter with a 2-D
  Gaussian-KDE surface and highest-density-region percentile contours (the
  numerical-grid density-quantile method) [Hyndman1996][Haselsteiner2017].
- :func:`~tether.analysis.cloud.alpha_shape` /
  :func:`~tether.analysis.cloud.population_fret_cloud_alpha_shape` — the raw FRET cloud's
  **α-shape support boundary** (PR-5b): the concave hull of the pooled ``(time, E)`` cloud
  from its Delaunay triangulation (circumradius ``<= alpha`` in axis-normalized
  coordinates), tracing where the population's signal lives [Edelsbrunner1983][PateiroLopez2010].
- :func:`~tether.analysis.cloud.k_rmse_elbow` /
  :func:`~tether.analysis.cloud.population_fret_cloud_state_number_elbow` — the
  **k-vs-RMSE elbow** state-count *hint* (PR-5b): k-means over the pooled apparent-E
  values with the knee of the within-cluster RMSE(k) curve [Satopaa2011][Thorndike1953]
  as a pre-idealization suggestion — a heuristic subordinate to the HMM/BIC state count
  [Schubert2022][McKinney2006].
- :func:`~tether.analysis.anticorrelation.find_anticorrelation_events` /
  :func:`~tether.analysis.anticorrelation.population_anticorrelation_events` — the
  **anticorrelation-event finder** (PR-6): a sliding window sweeps each donor/acceptor
  trace and merges windows that are both anti-phase (signed lag-0 Pearson ``< 0``, the
  reliable same-frame direction) and temporally structured (lag-1 magnitude above a
  threshold, which rejects white shot-noise anticorrelation) into time-localized events —
  the model-free lens that says *when* within a trace the donor and acceptor
  anticorrelate, subordinate to the HMM/TDP transition count
  [Felekyan2012][Torres2007][Chung2010][McKinney2006].
"""

from __future__ import annotations

from tether.analysis.anticorrelation import (
    DEFAULT_ANTICORR_MIN_MAGNITUDE,
    DEFAULT_ANTICORR_MIN_WINDOWS,
    DEFAULT_ANTICORR_STEP,
    DEFAULT_ANTICORR_WINDOW,
    AnticorrelationEvent,
    AnticorrelationScan,
    MoleculeAnticorrelation,
    PopulationAnticorrelation,
    find_anticorrelation_events,
    population_anticorrelation_events,
)
from tether.analysis.cloud import (
    DEFAULT_ALPHA_FACTOR,
    DEFAULT_CLOUD_BW_METHOD,
    DEFAULT_CLOUD_HDR_COVERAGES,
    DEFAULT_CLOUD_SIGNAL_BINS,
    DEFAULT_CLOUD_SIGNAL_RANGE,
    DEFAULT_CLOUD_TIME_BINS,
    DEFAULT_CLOUD_TIME_DT,
    DEFAULT_ELBOW_K_MAX,
    DEFAULT_ELBOW_RESTARTS,
    DEFAULT_ELBOW_SEED,
    AlphaShape,
    RawFretCloud,
    StateNumberElbow,
    alpha_shape,
    k_rmse_elbow,
    population_fret_cloud_alpha_shape,
    population_fret_cloud_state_number_elbow,
    population_raw_fret_cloud,
    raw_fret_cloud,
)
from tether.analysis.crosscorr import (
    CrossCorrelation,
    cross_correlation,
    population_cross_correlation,
)
from tether.analysis.dwell import (
    DEFAULT_DWELL_CI_LEVEL,
    DEFAULT_DWELL_DT,
    DEFAULT_DWELL_NBINS,
    DwellFit,
    DwellTimeAnalysis,
    StateDwells,
    fit_survival,
    population_dwell_times,
    state_dwells,
    survival_curve,
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
    DEFAULT_SYNC_PREFRAME,
    DEFAULT_TIME_BINS,
    DEFAULT_TIME_DT,
    ConditionHistogram,
    Histogram1D,
    Histogram2D,
    HistogramBootstrapCI,
    ModelGaussianOverlay,
    PerConditionHistograms,
    TransitionSyncHistogram2D,
    apparent_e_histogram,
    bootstrap_histogram_ci,
    model_gaussian_overlay,
    per_condition_apparent_e_histograms,
    population_apparent_e_histogram,
    population_apparent_e_histogram_ci,
    population_model_gaussian_overlay,
    population_time_signal_histogram2d,
    population_transition_sync_histogram2d,
    time_signal_histogram2d,
    transition_sync_histogram2d,
)
from tether.analysis.query import (
    ConditionQueryResult,
    MoleculeMatch,
    query_molecules,
)
from tether.analysis.state_number import (
    DEFAULT_STATE_NUMBER_LOW,
    StateNumberCounts,
    occupied_state_count,
    population_state_number,
    state_number_counts,
)
from tether.analysis.tdp import (
    DEFAULT_TDP_NSKIP,
    DEFAULT_TDP_SIGNAL_BINS,
    DEFAULT_TDP_SIGNAL_RANGE,
    TransitionDensityPlot,
    population_transition_density,
    transition_density,
)
from tether.analysis.transition_prob import (
    DEFAULT_TPROB_KDE_BANDWIDTH,
    DEFAULT_TPROB_KDE_POINTS,
    DEFAULT_TPROB_NBINS,
    DEFAULT_TPROB_RANGE,
    TransitionProbHistogram,
    empirical_transition_probability,
    population_transition_prob_histogram,
    transition_prob_histogram,
)

__all__ = [
    "DEFAULT_ALPHA_FACTOR",
    "DEFAULT_ANTICORR_MIN_MAGNITUDE",
    "DEFAULT_ANTICORR_MIN_WINDOWS",
    "DEFAULT_ANTICORR_STEP",
    "DEFAULT_ANTICORR_WINDOW",
    "DEFAULT_BOOTSTRAP_RESAMPLES",
    "DEFAULT_CI_LEVEL",
    "DEFAULT_CLOUD_BW_METHOD",
    "DEFAULT_CLOUD_HDR_COVERAGES",
    "DEFAULT_CLOUD_SIGNAL_BINS",
    "DEFAULT_CLOUD_SIGNAL_RANGE",
    "DEFAULT_CLOUD_TIME_BINS",
    "DEFAULT_CLOUD_TIME_DT",
    "DEFAULT_DWELL_CI_LEVEL",
    "DEFAULT_DWELL_DT",
    "DEFAULT_DWELL_NBINS",
    "DEFAULT_ELBOW_K_MAX",
    "DEFAULT_ELBOW_RESTARTS",
    "DEFAULT_ELBOW_SEED",
    "DEFAULT_NBINS",
    "DEFAULT_OVERLAY_POINTS",
    "DEFAULT_RANGE",
    "DEFAULT_SEED",
    "DEFAULT_SIGNAL_BINS",
    "DEFAULT_SIGNAL_RANGE",
    "DEFAULT_STATE_NUMBER_LOW",
    "DEFAULT_SYNC_PREFRAME",
    "DEFAULT_TDP_NSKIP",
    "DEFAULT_TDP_SIGNAL_BINS",
    "DEFAULT_TDP_SIGNAL_RANGE",
    "DEFAULT_TIME_BINS",
    "DEFAULT_TIME_DT",
    "DEFAULT_TPROB_KDE_BANDWIDTH",
    "DEFAULT_TPROB_KDE_POINTS",
    "DEFAULT_TPROB_NBINS",
    "DEFAULT_TPROB_RANGE",
    "AlphaShape",
    "AnticorrelationEvent",
    "AnticorrelationScan",
    "ConditionHistogram",
    "ConditionQueryResult",
    "CrossCorrelation",
    "DwellFit",
    "DwellTimeAnalysis",
    "Histogram1D",
    "Histogram2D",
    "HistogramBootstrapCI",
    "ModelGaussianOverlay",
    "MoleculeAnticorrelation",
    "MoleculeMatch",
    "PerConditionHistograms",
    "PopulationAnticorrelation",
    "RawFretCloud",
    "StateDwells",
    "StateNumberCounts",
    "StateNumberElbow",
    "TransitionDensityPlot",
    "TransitionProbHistogram",
    "TransitionSyncHistogram2D",
    "alpha_shape",
    "apparent_e_histogram",
    "bootstrap_histogram_ci",
    "cross_correlation",
    "empirical_transition_probability",
    "find_anticorrelation_events",
    "fit_survival",
    "k_rmse_elbow",
    "model_gaussian_overlay",
    "occupied_state_count",
    "per_condition_apparent_e_histograms",
    "population_anticorrelation_events",
    "population_apparent_e_histogram",
    "population_apparent_e_histogram_ci",
    "population_cross_correlation",
    "population_dwell_times",
    "population_fret_cloud_alpha_shape",
    "population_fret_cloud_state_number_elbow",
    "population_model_gaussian_overlay",
    "population_raw_fret_cloud",
    "population_state_number",
    "population_time_signal_histogram2d",
    "population_transition_density",
    "population_transition_prob_histogram",
    "population_transition_sync_histogram2d",
    "query_molecules",
    "raw_fret_cloud",
    "state_dwells",
    "state_number_counts",
    "survival_curve",
    "time_signal_histogram2d",
    "transition_density",
    "transition_prob_histogram",
    "transition_sync_histogram2d",
]
