# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""tether.idealize — tMAVEN sidecar driver and dwell analysis (PRD §4.2, §4.3).

Drives the isolated tMAVEN sidecar (export SMD, run vbFRET / consensus VB-HMM /
ebFRET headless via ``tmaven.maven.maven_class``, import the result); the
one-click hand-off to the standalone tMAVEN GUI with non-destructive re-import;
idealization staleness tracking; and dwell/rate analysis.
"""

from __future__ import annotations

from tether.idealize.driver import (
    MODEL_GROUP,
    NO_STATE,
    SIDECAR_ENV_VAR,
    Dwell,
    IdealizationResult,
    SidecarError,
    StateModel,
    dwells_from_states,
    read_model,
    resolve_sidecar_python,
    run_vbfret,
    states_from_idealized,
)
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
    "MODEL_GROUP",
    "NO_STATE",
    "SIDECAR_ENV_VAR",
    "SMD_FORMAT",
    "Dwell",
    "IdealizationResult",
    "MatchResult",
    "SMDData",
    "SidecarError",
    "StateModel",
    "dwells_from_states",
    "match_return_leg",
    "read_model",
    "read_smd",
    "resolve_sidecar_python",
    "run_vbfret",
    "states_from_idealized",
    "write_smd",
]
