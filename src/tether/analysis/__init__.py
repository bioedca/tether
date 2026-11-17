# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""tether.analysis — histograms, transition-density plots, and statistics (PRD §4.2).

FRET-efficiency histograms with confidence intervals, transition-density plots,
the raw FRET cloud, the anticorrelation-event finder, per-condition population
statistics, and the seven tMAVEN plot types (PRD Appendix C).

Landed so far (M2 S8, FR-ANALYZE):

- [`apparent_e_histogram`][tether.analysis.histogram.apparent_e_histogram] /
  [`population_apparent_e_histogram`][tether.analysis.histogram.population_apparent_e_histogram] —
  the 1-D
  population apparent-E histogram (Appendix C plot A1).
- [`cross_correlation`][tether.analysis.crosscorr.cross_correlation] /
  [`population_cross_correlation`][tether.analysis.crosscorr.population_cross_correlation] — the
  Pearson-normalized donor–acceptor cross-correlation with a lag-1 magnitude.

Added at M3 (FR-ANALYZE, PRD §7.7):

- [`bootstrap_histogram_ci`][tether.analysis.histogram.bootstrap_histogram_ci] /
  [`population_apparent_e_histogram_ci`][tether.analysis.histogram.population_apparent_e_histogram_ci]
  — the
  molecule-level bootstrap confidence interval for the histogram (BOBA-FRET
  [König2013]), the FRET histogram's error bars.

Added at M4 (FR-ANNOTATE, PRD §5.1/§7.7):

- [`query_molecules`][tether.analysis.query.query_molecules] — the cross-movie condition
  query/filter (by key fields + tags + category), aggregating a condition's
  molecules across all its movies/files.

Added at M6 (FR-ANALYZE, PRD §7.7, Appendix C plot A1):

-
  [`per_condition_apparent_e_histograms`][tether.analysis.histogram.per_condition_apparent_e_histograms]
  — the
  per-condition overlay: each condition's apparent-E histogram binned on one
  shared axis (density-normalized for cross-condition shape comparison) with its
  molecule-count ``N`` annotation. This is the M6-owned §7.7 "per-condition
  overlays" clause, which only becomes meaningful once the M4 condition model
  exists.
- [`model_gaussian_overlay`][tether.analysis.histogram.model_gaussian_overlay] /
  [`population_model_gaussian_overlay`][tether.analysis.histogram.population_model_gaussian_overlay]
  — the A1
  ``model_on`` overlay: the idealized population model's per-state Gaussians
  (``frac·𝒩(mean, var)``) and their sum, drawn on the histogram axis
  [Gopich2010]. The idealized model's own state emissions, not a fresh GMM fit.
- [`time_signal_histogram2d`][tether.analysis.histogram.time_signal_histogram2d] /
  [`population_time_signal_histogram2d`][tether.analysis.histogram.population_time_signal_histogram2d]
  — the A2
  2-D time-vs-signal occupancy heatmap (tMAVEN ``data_hist2d.py``), raw /
  start-synchronized mode: each accepted molecule's windowed apparent E binned
  into a ``(time, signal)`` grid so the population's FRET evolution over the
  analysis window is visible [Nettels2024].
- [`transition_sync_histogram2d`][tether.analysis.histogram.transition_sync_histogram2d] /
  [`population_transition_sync_histogram2d`][tether.analysis.histogram.population_transition_sync_histogram2d]
  — the
  A2 post-synchronized (transition-aligned) heatmap: align each molecule's Viterbi
  state transitions to a common column and bin the observed apparent E around them,
  so the population's average approach-to and departure-from a state jump is visible
  (a transition-synchronized ensemble average [Blackwell2020]).
- [`transition_density`][tether.analysis.tdp.transition_density] /
  [`population_transition_density`][tether.analysis.tdp.population_transition_density] — the B1 real
  Transition
  Density Plot: the initial-vs-final idealized-FRET density over state-change frames of
  a population's *fresh* idealizations [McKinney2006].
- [`state_dwells`][tether.analysis.dwell.state_dwells] /
  [`survival_curve`][tether.analysis.dwell.survival_curve] /
  [`fit_survival`][tether.analysis.dwell.fit_survival] /
  [`population_dwell_times`][tether.analysis.dwell.population_dwell_times] — the B2 dwell-time
  analysis:
  per-state dwell-length distributions, their empirical survival function, and its
  exponential-fit exit rates with confidence intervals + residuals [Schrangl2024];
  [`mean_dwell_time`][tether.analysis.dwell.mean_dwell_time] gives the amplitude-weighted mean
  dwell time of a sum-of-exponentials fit with its propagated error (#1028).
-
  [`empirical_transition_probability`][tether.analysis.transition_prob.empirical_transition_probability]
  /
  [`transition_prob_histogram`][tether.analysis.transition_prob.transition_prob_histogram] /
  [`population_transition_prob_histogram`][tether.analysis.transition_prob.population_transition_prob_histogram]
  — the
  B3 transition-probability histogram: the population of per-molecule maximum-likelihood
  one-step ``P(init → fin)`` rates estimated from each Viterbi path (the empirical
  analogue of tMAVEN's per-trace ``norm_tmatrix``), binned with an optional Gaussian-KDE
  [McKinney2006].
- [`occupied_state_count`][tether.analysis.state_number.occupied_state_count] /
  [`state_number_counts`][tether.analysis.state_number.state_number_counts] /
  [`population_state_number`][tether.analysis.state_number.population_state_number] — the C1
  state-number
  bar chart: molecule counts by the number of **distinct states each Viterbi path
  occupies** (the consensus-model analogue of tMAVEN's per-trace vbFRET state count)
  [vandeMeent2014].
- [`raw_fret_cloud`][tether.analysis.cloud.raw_fret_cloud] /
  [`population_raw_fret_cloud`][tether.analysis.cloud.population_raw_fret_cloud] — the raw FRET
  cloud QC view
  (PR-5a): the pooled pre-idealization ``(time, apparent-E)`` scatter with a 2-D
  Gaussian-KDE surface and highest-density-region percentile contours (the
  numerical-grid density-quantile method) [Hyndman1996][Haselsteiner2017].
- [`alpha_shape`][tether.analysis.cloud.alpha_shape] /
  [`population_fret_cloud_alpha_shape`][tether.analysis.cloud.population_fret_cloud_alpha_shape] —
  the raw FRET cloud's
  **α-shape support boundary** (PR-5b): the concave hull of the pooled ``(time, E)`` cloud
  from its Delaunay triangulation (circumradius ``<= alpha`` in axis-normalized
  coordinates), tracing where the population's signal lives [Edelsbrunner1983][PateiroLopez2010].
- [`k_rmse_elbow`][tether.analysis.cloud.k_rmse_elbow] /
  [`population_fret_cloud_state_number_elbow`][tether.analysis.cloud.population_fret_cloud_state_number_elbow]
  — the
  **k-vs-RMSE elbow** state-count *hint* (PR-5b): k-means over the pooled apparent-E
  values with the knee of the within-cluster RMSE(k) curve [Satopaa2011][Thorndike1953]
  as a pre-idealization suggestion — a heuristic subordinate to the HMM/BIC state count
  [Schubert2022][McKinney2006].
- [`find_anticorrelation_events`][tether.analysis.anticorrelation.find_anticorrelation_events] /
  [`population_anticorrelation_events`][tether.analysis.anticorrelation.population_anticorrelation_events]
  — the
  **anticorrelation-event finder** (PR-6): a sliding window sweeps each donor/acceptor
  trace and merges windows that are both anti-phase (signed lag-0 Pearson ``< 0``, the
  reliable same-frame direction) and temporally structured (lag-1 magnitude above a
  threshold, which rejects white shot-noise anticorrelation) into time-localized events —
  the model-free lens that says *when* within a trace the donor and acceptor
  anticorrelate, subordinate to the HMM/TDP transition count
  [Felekyan2012][Torres2007][Chung2010][McKinney2006].

Added at M8 (FR-ML / NFR-VALID(c), PRD §8/§9 M8):

- [`fit_gaussian_hmm`][tether.analysis.kinetics.fit_gaussian_hmm] /
  [`viterbi_paths`][tether.analysis.kinetics.viterbi_paths] /
  [`two_state_rate_constants`][tether.analysis.kinetics.two_state_rate_constants] /
  [`evaluate_kinsoft_level`][tether.analysis.kinetics.evaluate_kinsoft_level] — the
  **kinSoftChallenge
  kinetics-validation oracle**: a self-contained base-env Gaussian HMM (no tMAVEN
  sidecar) idealizes FRET trajectories, pooled dwell-time MLE infers the exit rates,
  and the archetypal 2-state level's rates are checked against that dataset's
  reported inter-tool spread (``schema/kinsoft_reference.json``) — an **advisory**
  gate [Götz2022].

Changed at M17 (#369, ADR-0109):

- Every population function takes its selection through one keyword, ``molecule_keys``,
  which also accepts a [`MoleculeSelection`][tether.analysis.selection.MoleculeSelection] — the
  protocol a frozen analysis-set version implements (#934). The six that read
  ``/idealization`` take ``include_stale``; the nine that read none do not.
- [`population_dwell_times`][tether.analysis.dwell.population_dwell_times] always returns the
  mapping, and
  [`population_fret_cloud_alpha_shape`][tether.analysis.cloud.population_fret_cloud_alpha_shape]
  always an
  [`AlphaShape`][tether.analysis.cloud.AlphaShape]
  ([`no_region`][tether.analysis.cloud.AlphaShape.no_region]
  when the cloud has no 2-D region).

Added at M17 (FR-PLOT, PRD §7.7; #373, ADR-0124):

- [`SavedSelection`][tether.analysis.saved_selection.SavedSelection] /
  [`select_molecules`][tether.analysis.saved_selection.select_molecules] /
  [`preset_selections`][tether.analysis.saved_selection.preset_selections] — the Qt-free,
  serializable one-project series of the plot builder: a named selection frozen from a
  [`query_molecules`][tether.analysis.query.query_molecules] result with the filter that made
  it, and the one-click presets (one selection per condition, movie or source file).

Added at M19 (FR-ANALYZE, PRD §7.7; #758, ADR-0092):

- [`dirichlet_rate_bounds`][tether.analysis.rate_bounds.dirichlet_rate_bounds] /
  [`model_rate_bounds`][tether.analysis.rate_bounds.model_rate_bounds] — the rate constants of an
  HMM's un-normalized transition count matrix with their Dirichlet lower and upper
  bounds, the method of the Source Data of [CamposChavez2024], from an array or from a
  stored idealization model (the result carries its provenance stamp).

Added at M17 (FR-ANALYZE, PRD §7.7; #1030, ADR-0120):

- [`fit_gaussians`][tether.analysis.gaussian_fit.fit_gaussians] — a sum of ``n`` Gaussians
  fitted to a 1-D histogram by least squares, with each Gaussian's mean, width, area and
  fraction of the total area, and their errors propagated from the full covariance
  [Gopich2010]; the fit behind Fig. 5c of [CamposChavez2024].

Exposed lazily (FR-EXPORT, PRD §7.9; #372):

- [`plot_export`][tether.analysis.plot_export] and every name in its ``__all__`` —
  [`export_figure`][tether.analysis.plot_export.export_figure], the ``render_*`` helpers,
  [`PlotExportResult`][tether.analysis.plot_export.PlotExportResult] and the export
  constants — resolve on first attribute access through a module ``__getattr__``
  ([PEP 562](https://peps.python.org/pep-0562/)), so
  ``from tether.analysis import export_figure`` works while ``import tether.analysis``
  still does not import Matplotlib. ``dir(tether.analysis)`` lists these names. They
  are deliberately **not** in this package's ``__all__``: a star import then stays
  Matplotlib-free as well.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

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
    mean_dwell_time,
    population_dwell_times,
    state_dwells,
    survival_curve,
)
from tether.analysis.gaussian_fit import (
    AREA_METHODS,
    DEFAULT_AREA_METHOD,
    DEFAULT_INITIAL_WIDTH,
    GaussianFit,
    fit_gaussians,
    gaussian_sum,
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
from tether.analysis.kinetics import (
    DEFAULT_HMM_MAX_ITER,
    DEFAULT_HMM_TOL,
    DEFAULT_HMM_VAR_FLOOR,
    GaussianHMM,
    KinsoftKineticsResult,
    KinsoftReference,
    TwoStateKinetics,
    evaluate_kinsoft_level,
    fit_gaussian_hmm,
    load_kinsoft_reference,
    pooled_exit_rates,
    two_state_rate_constants,
    viterbi_paths,
)
from tether.analysis.query import (
    ConditionQueryResult,
    MoleculeMatch,
    query_molecules,
)
from tether.analysis.rate_bounds import (
    RateBounds,
    dirichlet_rate_bounds,
    model_rate_bounds,
)
from tether.analysis.saved_selection import (
    SavedSelection,
    SelectionFilter,
    preset_selections,
    select_molecules,
)
from tether.analysis.selection import MoleculeSelection, Selection, resolve_selection
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

if TYPE_CHECKING:  # static checkers see the real types; runtime resolves them lazily
    from tether.analysis import plot_export
    from tether.analysis.plot_export import (
        DEFAULT_EXPORT_DPI,
        DEFAULT_FIGSIZE,
        DEFAULT_PLOT_FORMATS,
        DEFAULT_TDP_COLOR_SCALE,
        TDP_COLOR_SCALES,
        PlotExportResult,
        export_figure,
        render_alpha_shape,
        render_cross_correlation,
        render_dwell_survival,
        render_histogram1d,
        render_histogram2d,
        render_per_condition_histograms,
        render_population_anticorrelation,
        render_raw_fret_cloud,
        render_state_number,
        render_state_number_elbow,
        render_transition_density,
        render_transition_prob,
        render_transition_sync_histogram2d,
    )

__all__ = [
    "AREA_METHODS",
    "DEFAULT_ALPHA_FACTOR",
    "DEFAULT_ANTICORR_MIN_MAGNITUDE",
    "DEFAULT_ANTICORR_MIN_WINDOWS",
    "DEFAULT_ANTICORR_STEP",
    "DEFAULT_ANTICORR_WINDOW",
    "DEFAULT_AREA_METHOD",
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
    "DEFAULT_HMM_MAX_ITER",
    "DEFAULT_HMM_TOL",
    "DEFAULT_HMM_VAR_FLOOR",
    "DEFAULT_INITIAL_WIDTH",
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
    "GaussianFit",
    "GaussianHMM",
    "Histogram1D",
    "Histogram2D",
    "HistogramBootstrapCI",
    "KinsoftKineticsResult",
    "KinsoftReference",
    "ModelGaussianOverlay",
    "MoleculeAnticorrelation",
    "MoleculeMatch",
    "MoleculeSelection",
    "PerConditionHistograms",
    "PopulationAnticorrelation",
    "RateBounds",
    "SavedSelection",
    "RawFretCloud",
    "Selection",
    "SelectionFilter",
    "StateDwells",
    "StateNumberCounts",
    "StateNumberElbow",
    "TransitionDensityPlot",
    "TransitionProbHistogram",
    "TransitionSyncHistogram2D",
    "TwoStateKinetics",
    "alpha_shape",
    "apparent_e_histogram",
    "bootstrap_histogram_ci",
    "cross_correlation",
    "dirichlet_rate_bounds",
    "empirical_transition_probability",
    "evaluate_kinsoft_level",
    "find_anticorrelation_events",
    "fit_gaussian_hmm",
    "fit_gaussians",
    "fit_survival",
    "gaussian_sum",
    "k_rmse_elbow",
    "load_kinsoft_reference",
    "mean_dwell_time",
    "model_gaussian_overlay",
    "model_rate_bounds",
    "occupied_state_count",
    "per_condition_apparent_e_histograms",
    "pooled_exit_rates",
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
    "preset_selections",
    "query_molecules",
    "raw_fret_cloud",
    "resolve_selection",
    "select_molecules",
    "state_dwells",
    "state_number_counts",
    "survival_curve",
    "time_signal_histogram2d",
    "transition_density",
    "transition_prob_histogram",
    "transition_sync_histogram2d",
    "two_state_rate_constants",
    "viterbi_paths",
]

#: Names this package resolves on first access (PEP 562) instead of importing at load
#: time: the `tether.analysis.plot_export` module and its ``__all__``. Importing that
#: module imports Matplotlib, so an eager import here would put Matplotlib on every
#: ``import tether.analysis``. ``tests/test_imports.py`` keeps this set equal to
#: ``plot_export.__all__``.
_LAZY_PLOT_EXPORT_NAMES = frozenset(
    {
        "plot_export",
        "DEFAULT_EXPORT_DPI",
        "DEFAULT_FIGSIZE",
        "DEFAULT_PLOT_FORMATS",
        "DEFAULT_TDP_COLOR_SCALE",
        "TDP_COLOR_SCALES",
        "PlotExportResult",
        "export_figure",
        "render_alpha_shape",
        "render_cross_correlation",
        "render_dwell_survival",
        "render_histogram1d",
        "render_histogram2d",
        "render_per_condition_histograms",
        "render_population_anticorrelation",
        "render_raw_fret_cloud",
        "render_state_number",
        "render_state_number_elbow",
        "render_transition_density",
        "render_transition_prob",
        "render_transition_sync_histogram2d",
    }
)


def __getattr__(name: str) -> Any:
    """Resolve a `tether.analysis.plot_export` name on first access (PEP 562)."""
    if name in _LAZY_PLOT_EXPORT_NAMES:
        import importlib

        module = importlib.import_module("tether.analysis.plot_export")
        value = module if name == "plot_export" else getattr(module, name)
        globals()[name] = value  # cache: later lookups skip this hook
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """List the eager names plus the lazily resolved plot-export names."""
    return sorted(set(globals()) | _LAZY_PLOT_EXPORT_NAMES)
