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
    DEFAULT_RANGE,
    DEFAULT_SEED,
    Histogram1D,
    HistogramBootstrapCI,
    apparent_e_histogram,
    bootstrap_histogram_ci,
    population_apparent_e_histogram,
    population_apparent_e_histogram_ci,
)

__all__ = [
    "DEFAULT_BOOTSTRAP_RESAMPLES",
    "DEFAULT_CI_LEVEL",
    "DEFAULT_NBINS",
    "DEFAULT_RANGE",
    "DEFAULT_SEED",
    "CrossCorrelation",
    "Histogram1D",
    "HistogramBootstrapCI",
    "apparent_e_histogram",
    "bootstrap_histogram_ci",
    "cross_correlation",
    "population_apparent_e_histogram",
    "population_apparent_e_histogram_ci",
    "population_cross_correlation",
]
