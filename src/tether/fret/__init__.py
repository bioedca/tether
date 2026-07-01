# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""tether.fret — photobleaching detection, corrections, and corrected FRET (PRD §4.2).

A native, headless reimplementation of tMAVEN's Bayesian single-step
photobleaching model, run independently per channel (PRD Appendix E Stage 16);
the correction factors (leakage alpha, detection gamma, inert delta = 0 — PRD
Appendix B); corrected FRET over the per-trace analysis window; and a
vectorized FFT donor-acceptor cross-correlation.
"""

from __future__ import annotations

from tether.fret.efficiency import apparent_fret

__all__ = ["apparent_fret"]
