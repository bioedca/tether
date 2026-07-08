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
    run_ebhmm,
    run_vbconhmm,
    run_vbfret,
    states_from_idealized,
)
from tether.idealize.matcher import MatchResult, match_return_leg
from tether.idealize.parity import (
    PROVISIONAL,
    ParityMetrics,
    SpreadSummary,
    canonical_state_path,
    compare_models,
    freeze,
    load_frozen_tolerance,
    measure_spread,
    relative_elbo,
    state_count_fraction,
    state_mean_abs_delta,
    viterbi_agreement,
    within_tolerance,
)
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
    "PROVISIONAL",
    "SIDECAR_ENV_VAR",
    "SMD_FORMAT",
    "Dwell",
    "IdealizationResult",
    "MatchResult",
    "ParityMetrics",
    "SMDData",
    "SidecarError",
    "SpreadSummary",
    "StateModel",
    "canonical_state_path",
    "compare_models",
    "dwells_from_states",
    "freeze",
    "load_frozen_tolerance",
    "match_return_leg",
    "measure_spread",
    "read_model",
    "read_smd",
    "relative_elbo",
    "resolve_sidecar_python",
    "run_ebhmm",
    "run_vbconhmm",
    "run_vbfret",
    "state_count_fraction",
    "state_mean_abs_delta",
    "states_from_idealized",
    "viterbi_agreement",
    "within_tolerance",
    "write_smd",
]
