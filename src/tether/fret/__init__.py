# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""tether.fret — photobleaching detection, corrections, and corrected FRET (PRD §4.2).

A native, headless reimplementation of tMAVEN's Bayesian single-step
photobleaching model, run independently per channel (PRD Appendix E Stage 16);
the correction factors (leakage alpha, detection gamma, inert delta = 0 — PRD
Appendix B); and corrected FRET over the per-trace analysis window. The
donor-acceptor cross-correlation is not here: it is
[`tether.analysis.crosscorr`][].
"""

from __future__ import annotations

from tether.fret.efficiency import apparent_fret, corrected_fret
from tether.fret.photobleach import (
    PhotobleachResult,
    active_mask,
    detect_photobleach,
    ensemble_pbtime,
    point_pbtime,
)

__all__ = [
    "PhotobleachResult",
    "active_mask",
    "apparent_fret",
    "corrected_fret",
    "detect_photobleach",
    "ensemble_pbtime",
    "point_pbtime",
]
