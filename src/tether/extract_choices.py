# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The allowed values of the three choice options of ``tether extract`` (#654).

:class:`tether.project.extract.ExtractOptions` validates ``donor_side``,
``detection_mode`` and ``prealign`` against these tuples, and :mod:`tether.cli`
gives the same tuples to argparse as ``choices``. One source for both means the
parser and the library cannot accept different values.

This module imports nothing. :mod:`tether.cli` reads it when it builds the parser,
and that also happens for ``tether --version``, which must not load the imaging or
HDF5 stack (ADR-0018).
"""

from __future__ import annotations

__all__ = ["DETECTION_MODES", "DONOR_SIDES", "PREALIGN_MODES"]

DONOR_SIDES: tuple[str, ...] = ("left", "right")
"""The horizontal half of the frame that carries the donor channel."""

DETECTION_MODES: tuple[str, ...] = ("wavelet", "intensity", "bandpass")
"""The spot detectors, in Deep-LASI ``findPart`` mode order.

These are the values of :class:`tether.imaging.detect.ParticleDetectionMode`; a test
keeps the two equal.
"""

PREALIGN_MODES: tuple[str, ...] = ("translation", "similarity")
"""The degrees of freedom of the registration prealign."""
