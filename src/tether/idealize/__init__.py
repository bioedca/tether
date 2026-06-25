# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""tether.idealize — tMAVEN sidecar driver and dwell analysis (PRD §4.2, §4.3).

Drives the isolated tMAVEN sidecar (export SMD, run vbFRET / consensus VB-HMM /
ebFRET headless via ``tmaven.maven.maven_class``, import the result); the
one-click hand-off to the standalone tMAVEN GUI with non-destructive re-import;
idealization staleness tracking; and dwell/rate analysis.
"""

from __future__ import annotations

from tether.idealize.matcher import MatchResult, match_return_leg
from tether.idealize.smd import (
    DEFAULT_GROUP,
    SMD_FORMAT,
    SMDData,
    read_smd,
    write_smd,
)

__all__ = [
    "DEFAULT_GROUP",
    "SMD_FORMAT",
    "MatchResult",
    "SMDData",
    "match_return_leg",
    "read_smd",
    "write_smd",
]
