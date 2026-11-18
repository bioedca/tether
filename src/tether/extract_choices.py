# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The allowed values of the choice options of ``tether extract`` (#654, #920).

[`ExtractOptions`][tether.project.extract.ExtractOptions] validates ``donor_side``,
``detection_mode``, ``prealign`` and ``colocalization`` against these tuples, and [`tether.cli`][]
gives the same tuples to argparse as ``choices``. One source for both means the
parser and the library cannot accept different values.

This module imports nothing. [`tether.cli`][] reads it when it builds the parser,
and that also happens for ``tether --version``, which must not load the imaging or
HDF5 stack (ADR-0018).
"""

from __future__ import annotations

__all__ = [
    "COLOCALIZATION_POLICIES",
    "DEFAULT_COLOCALIZATION_POLICY",
    "DETECTION_MODES",
    "DONOR_SIDES",
    "DONOR_ANCHORED",
    "PARTNER_IN_BOTH",
    "PREALIGN_MODES",
]

DONOR_SIDES: tuple[str, ...] = ("left", "right")
"""The horizontal half of the frame that carries the donor channel."""

DETECTION_MODES: tuple[str, ...] = ("wavelet", "intensity", "bandpass")
"""The spot detectors, in Deep-LASI ``findPart`` mode order.

These are the values of
[`ParticleDetectionMode`][tether.imaging.detect.ParticleDetectionMode]; a test keeps the two
equal.
"""

PREALIGN_MODES: tuple[str, ...] = ("translation", "similarity")
"""The degrees of freedom of the registration prealign."""

PARTNER_IN_BOTH = "partner-in-both"
"""Keep a molecule only when a spot was detected in both channels (Deep-LASI's rule, ADR-0127)."""

DONOR_ANCHORED = "donor-anchored"
"""Keep every in-frame donor spot, detected acceptor or not (the rule of ADR-0015)."""

COLOCALIZATION_POLICIES: tuple[str, ...] = (PARTNER_IN_BOTH, DONOR_ANCHORED)
"""The colocalization rules a new extraction can use, the default first (ADR-0127)."""

DEFAULT_COLOCALIZATION_POLICY = PARTNER_IN_BOTH
"""The rule of a new extraction when nothing selects another (maintainer decision C1, #920)."""
