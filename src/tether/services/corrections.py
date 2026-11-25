# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The correction pass of an open project, as the application runs it (#360, PRD §7.2).

[`run_correction_pass`][] runs
[`compute_corrected_fret`][tether.project.correct.compute_corrected_fret] over the whole
project: it stamps each molecule's ``correction_method`` and ``correction_confidence`` from
the applied ``/molecules.alpha`` and ``/molecules.gamma`` and writes the
``/settings/correction`` provenance. It adds no override: a manual α or γ with its source
is the *sourced override* of the review pages (#930).

**The correction states (G03, #925).** The pass is not an event of the state machine, and
it sends none: the event it belongs to, *apply correction version*, applies a frozen
correction version, and those versions are #929. So the pass is allowed only where it
cannot change the state or what was reviewed ([`PASS_STATES`][]), and
[`correction_pass_refusal`][] says why in every other state:

* APPARENT_REVIEW is the start state of a project that holds no factor. The provenance
  group the pass writes is a stored factor record, so after the pass the project would
  read as GAMMA_REVIEW with no transition that says who moved it (ADR-0131 §6).
* CALIBRATION_APPROVED is where *apply correction version* runs (#929).
* In CORRECTED_REVIEW and ANALYSIS_READY the corrected data are under review or in a
  review snapshot; a pass would change them behind the review (#931).

In the four states that remain the project already holds a stored state, or a stored factor
record, so the provenance the pass rewrites leaves the state as it was. The pass re-reads
the state before it writes, so a state that changed since the window read it refuses the
pass too.

**Manual factors.** A pass with no override stamps a valid molecule ``corrected``, never
``manual``, and writes ``NaN`` for the two overrides of ``/settings/correction``. On a project
whose factors a person supplied, that would relabel the supplied factors as estimated and
drop the manual proposal of GAMMA_REVIEW. A project re-corrected per movie (a Deep-LASI
acquisition appended with factors of its own, ADR-0117) cannot take one project-wide
override back either. So the pass is refused while any molecule is stamped ``manual`` or
``/settings/correction`` holds a finite override ([`manual_factor_refusal`][]).

Like [`tether.services.sources`][], this is not a registered service command: it has no
command-line subcommand (``tether batch`` runs the same pass as its correct stage). It
imports the core only when it runs (ADR-0100).
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from tether.services.base import RequestRefused

if TYPE_CHECKING:
    from collections.abc import Callable
    from os import PathLike

    from tether.project.correct import CorrectionSummary

__all__ = [
    "PASS_STATES",
    "CorrectionPassResult",
    "correction_pass_refusal",
    "correction_state_name",
    "manual_factor_refusal",
    "pass_refusal",
    "run_correction_pass",
]

#: The correction states (G03) in which the pass may run, by name.
PASS_STATES: frozenset[str] = frozenset(
    {
        "BLEEDTHROUGH_REQUIRED",
        "GAMMA_REVIEW",
        "CALIBRATION_BLOCKED",
        "CALIBRATION_STALE",
    }
)

#: Why the pass is refused in each other state; the text completes "… because …".
_REFUSALS: dict[str, str] = {
    "APPARENT_REVIEW": (
        "the project holds no correction factor yet, and a pass here would move it to "
        "GAMMA_REVIEW with no recorded transition; finish the initial curation first"
    ),
    "CALIBRATION_APPROVED": (
        "the approved factors are applied by the step apply correction version (#929), "
        "not by this pass"
    ),
    "CORRECTED_REVIEW": (
        "the corrected traces are under review, and a pass would change them behind the review"
    ),
    "ANALYSIS_READY": (
        "the corrected traces are in a finished review, and a pass would change them "
        "behind it; edit the analysis windows or the curation to reopen the review"
    ),
}


@dataclasses.dataclass(frozen=True)
class CorrectionPassResult:
    """The outcome of one [`run_correction_pass`][].

    ``summary`` is the [`CorrectionSummary`][tether.project.correct.CorrectionSummary] the
    pass returned, and ``state`` the name of the correction state after it.
    """

    summary: CorrectionSummary
    state: str


def correction_state_name(path: str | PathLike[str]) -> str:
    """Return the name of the project's correction state (G03), read with no lock."""
    from tether.project.correction_states import read_correction_state  # noqa: PLC0415

    return read_correction_state(path).state.value


def correction_pass_refusal(state: str) -> str | None:
    """Return why the pass may not run in the correction state ``state``, else ``None``."""
    if state in PASS_STATES:
        return None
    why = _REFUSALS.get(state, "the pass is not defined for this state")
    return f"the correction state is {state}, and {why}"


def manual_factor_refusal(path: str | PathLike[str]) -> str | None:
    """Return why the pass would lose manual-factor provenance, else ``None`` (a read).

    The pass is refused while a molecule is stamped
    [`METHOD_MANUAL`][tether.project.correct.METHOD_MANUAL] or ``/settings/correction``
    records a finite ``alpha_override`` or ``gamma_override``.
    """
    import h5py  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    from tether.io.schema import TABLE  # noqa: PLC0415
    from tether.project.correct import METHOD_MANUAL  # noqa: PLC0415

    with h5py.File(path, "r") as f:
        methods = f["molecules"][TABLE]["correction_method"]
        n_manual = sum(
            1
            for m in methods
            if (m.decode("utf-8") if isinstance(m, bytes) else str(m)) == METHOD_MANUAL
        )
        stamp = f["settings"].get("correction")
        overrides = (
            []
            if stamp is None
            else [
                name
                for name in ("alpha_override", "gamma_override")
                if name in stamp.attrs and np.isfinite(float(stamp.attrs[name]))
            ]
        )
    if not n_manual and not overrides:
        return None
    if n_manual:
        held = f"{n_manual} molecule{'s' if n_manual != 1 else ''} carry a manual α or γ"
    else:
        held = "the last correction pass recorded a manual α or γ"
    return (
        f"{held}, and this pass would relabel the supplied factors as estimated; run the "
        "pass with the same overrides instead (how-to Apply corrections, step 3)"
    )


def pass_refusal(path: str | PathLike[str]) -> str | None:
    """Return why the pass may not run on the project at ``path`` now, else ``None``.

    It is [`correction_pass_refusal`][] of the present state, then
    [`manual_factor_refusal`][]; both are reads with no lock.
    """
    return correction_pass_refusal(correction_state_name(path)) or manual_factor_refusal(path)


def run_correction_pass(
    path: str | PathLike[str],
    *,
    apparent_e_only: bool = False,
    write_guard: Callable[[], None] | None = None,
) -> CorrectionPassResult:
    """Run the correction pass over the project at ``path`` and return its outcome.

    ``apparent_e_only`` keeps every molecule in apparent E by choice
    ([`METHOD_APPARENT_TOGGLE`][tether.project.correct.METHOD_APPARENT_TOGGLE]).
    ``write_guard`` is the caller's check of its writer lock; with it, the pass writes a
    sibling copy and publishes it in one guarded atomic replace. The caller holds the
    single-writer lock. Raises [`RequestRefused`][tether.services.base.RequestRefused],
    and writes nothing, when [`pass_refusal`][] read now refuses the pass.
    """
    from tether.project.correct import compute_corrected_fret  # noqa: PLC0415

    refusal = pass_refusal(path)
    if refusal is not None:
        raise RequestRefused(f"the correction pass is not available: {refusal}")
    summary = compute_corrected_fret(path, apparent_e_only=apparent_e_only, write_guard=write_guard)
    return CorrectionPassResult(summary=summary, state=correction_state_name(path))
