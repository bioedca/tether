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
"""

from __future__ import annotations

from tether.analysis.crosscorr import (
    CrossCorrelation,
    cross_correlation,
    population_cross_correlation,
)
from tether.analysis.histogram import (
    DEFAULT_NBINS,
    DEFAULT_RANGE,
    Histogram1D,
    apparent_e_histogram,
    population_apparent_e_histogram,
)

__all__ = [
    "DEFAULT_NBINS",
    "DEFAULT_RANGE",
    "CrossCorrelation",
    "Histogram1D",
    "apparent_e_histogram",
    "cross_correlation",
    "population_apparent_e_histogram",
    "population_cross_correlation",
]
