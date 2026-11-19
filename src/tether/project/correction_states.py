# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The correction states of diagram G03: states, events and guards, stored in the project.

The readiness of the corrections is a state machine (PRD §7.2, #925, ADR-0131). The
states are scientific readiness states, not screens, and this module is Qt-free, so the
application, the command line and a recipe see one state:

* [`CorrectionState`][] holds the eight states of G03 and [`TRANSITIONS`][] its thirteen
  edges, each with its event, its guard and its action. No other transition is possible:
  [`send_event`][] refuses an event that has no edge out of the present state, and an
  event whose guard does not hold.
* Each transition is one row of ``/settings/correction_transitions``, an append-only
  dataset that names the calibration scope, the two states, the event, who or what caused
  it ([`Actor`][]: a person, or a recipe policy as ADR-0102 says), the time and the values
  the guard read. The present state of a scope is the target of its last row.
* A scope with no row is in its start state. That is APPARENT_REVIEW, except in a project
  that already holds factors (a leakage, γ or correction pass from before this change, or
  a ``tether batch`` run): it starts in GAMMA_REVIEW, with the stored factors as the
  proposal ([`CorrectionStatus.proposal`][]). A project never opens as approved.
* The machine controls **corrected** analysis only (card C2, option B).
  [`require_analysis_mode`][] refuses a corrected analysis in every state but
  ANALYSIS_READY, with a message that names the state, and never refuses apparent E.
* An event sent a second time leaves the state as the first one did: when an event has no
  edge out of the present state but is the event that made it, it changes nothing.

The machine does not compute a factor; an edge's action (``estimate gamma``) is the
caller's next step. The scope is the project; one scope per condition follows #624. The
single-writer lock must be held through the [`Project`][tether.project.core.Project] handle
for each write, and an unlocked project is refused; a read takes no lock.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from pathlib import Path

    from tether.project.core import Project

__all__ = [
    "PROJECT_SCOPE",
    "TRANSITIONS",
    "Actor",
    "AnalysisMode",
    "CorrectedAnalysisRefused",
    "CorrectionEvent",
    "CorrectionGuardError",
    "CorrectionState",
    "CorrectionStatus",
    "CorrectionTransitionError",
    "Transition",
    "TransitionRecord",
    "read_correction_state",
    "read_correction_transitions",
    "require_analysis_mode",
    "send_event",
]

_SETTINGS = "settings"
_TRANSITIONS = "correction_transitions"
_FIELDS = (
    "scope",
    "from_state",
    "event",
    "to_state",
    "by",
    "actor",
    "detail",
    "timestamp",
    "app_version",
)

#: The one calibration scope until #624 gives each condition its own.
PROJECT_SCOPE = "project"


class CorrectionState(StrEnum):
    """The eight states of G03."""

    #: Manual curation and confirmed ML labels, in apparent E.
    APPARENT_REVIEW = "APPARENT_REVIEW"
    #: Source and confirm the bleedthrough (leakage) factor α.
    BLEEDTHROUGH_REQUIRED = "BLEEDTHROUGH_REQUIRED"
    #: Inspect the curated bleach-step contributors of γ.
    GAMMA_REVIEW = "GAMMA_REVIEW"
    #: No usable steps, or a factor that is not valid.
    CALIBRATION_BLOCKED = "CALIBRATION_BLOCKED"
    #: The factors and the contributor evidence are frozen.
    CALIBRATION_APPROVED = "CALIBRATION_APPROVED"
    #: Review the bleach boundaries, the analysis windows and the traces in corrected E.
    CORRECTED_REVIEW = "CORRECTED_REVIEW"
    #: An approved correction and a review snapshot: corrected analysis is allowed.
    ANALYSIS_READY = "ANALYSIS_READY"
    #: The calibration evidence changed; corrected analysis is disabled.
    CALIBRATION_STALE = "CALIBRATION_STALE"


#: The state of a scope that has no stored transition and no stored factor (G03 “start”).
INITIAL_STATE = CorrectionState.APPARENT_REVIEW


class CorrectionEvent(StrEnum):
    """The events of G03. The value is the text stored in a transition row."""

    FINISH_INITIAL_CURATION = "finish initial curation"
    CONFIRM_BLEEDTHROUGH = "confirm bleedthrough factor"
    INSUFFICIENT_EVIDENCE = "insufficient evidence"
    NEW_EVIDENCE = "new evidence"
    OVERRIDE = "sourced override"
    APPROVE = "approve"
    APPLY_CORRECTION = "apply correction version"
    FINISH_REVIEW = "finish review"
    CALIBRATION_EVIDENCE_CHANGES = "calibration evidence changes"
    FACTOR_EVIDENCE_CHANGES = "factor or contributor evidence changes"
    EDIT_WINDOWS_OR_CURATION = "edit analysis windows or curation"
    #: Leave CALIBRATION_STALE; the bleedthrough guard picks the target.
    RECALCULATE = "calculate again"


class AnalysisMode(StrEnum):
    """The two named analysis modes of PRD §7.2 (card C2)."""

    APPARENT = "apparent"
    CORRECTED = "corrected"


# --- guards -----------------------------------------------------------------------------------

_G_ALPHA_SOURCED = "bleedthrough factor valid and has a source"
_G_OVERRIDE = "override valid and has a source"
_G_GAMMA = "gamma valid"
_G_CURRENT = "all dependencies current"
_G_ALPHA_INVALID = "bleedthrough factor missing or not valid"
_G_ALPHA_VALID = "bleedthrough factor valid"


def _alpha_valid(alpha: float | None) -> bool:
    # The validity that compute_corrected_fret applies to a manual α (finite).
    return alpha is not None and math.isfinite(alpha)


def _gamma_valid(gamma: float | None) -> bool:
    # The validity that compute_corrected_fret applies to γ (finite and above 0).
    return gamma is not None and math.isfinite(gamma) and gamma > 0.0


def _sourced(source: str) -> bool:
    return bool(source.strip())


@dataclass(frozen=True)
class Transition:
    """One edge of G03: ``source --event [guard] / action--> target``."""

    source: CorrectionState
    event: CorrectionEvent
    target: CorrectionState
    guard: str = ""
    action: str = ""


_S = CorrectionState
_E = CorrectionEvent

#: Every edge of G03, and no other. ``tests/test_correction_states.py`` checks each one.
TRANSITIONS: tuple[Transition, ...] = (
    Transition(_S.APPARENT_REVIEW, _E.FINISH_INITIAL_CURATION, _S.BLEEDTHROUGH_REQUIRED),
    Transition(
        _S.BLEEDTHROUGH_REQUIRED,
        _E.CONFIRM_BLEEDTHROUGH,
        _S.GAMMA_REVIEW,
        _G_ALPHA_SOURCED,
        "estimate gamma",
    ),
    Transition(_S.GAMMA_REVIEW, _E.INSUFFICIENT_EVIDENCE, _S.CALIBRATION_BLOCKED),
    Transition(_S.CALIBRATION_BLOCKED, _E.NEW_EVIDENCE, _S.GAMMA_REVIEW),
    Transition(_S.CALIBRATION_BLOCKED, _E.OVERRIDE, _S.GAMMA_REVIEW, _G_OVERRIDE),
    Transition(_S.GAMMA_REVIEW, _E.APPROVE, _S.CALIBRATION_APPROVED, _G_GAMMA),
    Transition(_S.CALIBRATION_APPROVED, _E.APPLY_CORRECTION, _S.CORRECTED_REVIEW),
    Transition(_S.CORRECTED_REVIEW, _E.FINISH_REVIEW, _S.ANALYSIS_READY, _G_CURRENT),
    Transition(_S.CORRECTED_REVIEW, _E.CALIBRATION_EVIDENCE_CHANGES, _S.CALIBRATION_STALE),
    Transition(_S.ANALYSIS_READY, _E.FACTOR_EVIDENCE_CHANGES, _S.CALIBRATION_STALE),
    Transition(_S.ANALYSIS_READY, _E.EDIT_WINDOWS_OR_CURATION, _S.CORRECTED_REVIEW),
    Transition(_S.CALIBRATION_STALE, _E.RECALCULATE, _S.BLEEDTHROUGH_REQUIRED, _G_ALPHA_INVALID),
    Transition(
        _S.CALIBRATION_STALE, _E.RECALCULATE, _S.GAMMA_REVIEW, _G_ALPHA_VALID, "calculate again"
    ),
)


class CorrectionTransitionError(ValueError):
    """The event has no edge of G03 out of the present state."""


class CorrectionGuardError(ValueError):
    """The event has an edge out of the present state, and its guard does not hold."""


class CorrectedAnalysisRefused(RuntimeError):  # noqa: N818 - a refusal the user reads
    """A corrected analysis was asked for in a state before ANALYSIS_READY."""

    def __init__(self, state: CorrectionState, scope: str) -> None:
        self.state = state
        self.scope = scope
        super().__init__(
            f"a corrected analysis needs the correction state {CorrectionState.ANALYSIS_READY}; "
            f"the {scope} is in {state}. An apparent-E analysis stays possible."
        )


@dataclass(frozen=True)
class Actor:
    """Who or what caused a transition: ``by`` is ``person`` or ``policy`` (ADR-0102 §4).

    For a person, ``name`` is the name, read as the ``labeler`` of a curation label is;
    for a policy, it is the policy text exactly as the recipe gives it.
    """

    by: str
    name: str

    def __post_init__(self) -> None:
        """Refuse an actor that is not a person or a policy, or that has no name."""
        if self.by not in ("person", "policy"):
            raise ValueError(f"an actor is a 'person' or a 'policy', not {self.by!r}")
        if not self.name.strip():
            raise ValueError(f"a {self.by} actor needs a name or a policy text")

    @classmethod
    def person(cls, name: str | None = None) -> Actor:
        """Return a person; the name defaults to the curation labeler.

        The default is [`default_labeler`][tether.project.labels.default_labeler].
        """
        if name is None:
            from tether.project.labels import default_labeler  # noqa: PLC0415

            name = default_labeler()
        return cls("person", name)

    @classmethod
    def policy(cls, text: str) -> Actor:
        """Return a recipe policy, given by its text."""
        return cls("policy", text)


@dataclass(frozen=True)
class TransitionRecord:
    """One stored transition of a scope."""

    scope: str
    from_state: CorrectionState
    event: CorrectionEvent
    to_state: CorrectionState
    actor: Actor
    detail: Mapping[str, object]
    timestamp: str
    app_version: str


@dataclass(frozen=True)
class CorrectionStatus:
    """The present correction state of one scope, as [`read_correction_state`][] reads it.

    ``stored`` is false when the scope has no transition yet and ``state`` is its start
    state. ``proposal`` holds the factors already stored in the project (``alpha`` and
    ``gamma``, ``None`` when absent), the proposal a person reviews in GAMMA_REVIEW.
    ``last`` is the last stored transition, or ``None``.
    """

    scope: str
    state: CorrectionState
    stored: bool
    proposal: Mapping[str, float | None] = field(default_factory=dict)
    last: TransitionRecord | None = None


# --- the store --------------------------------------------------------------------------------


def _check_scope(scope: str) -> None:
    if scope != PROJECT_SCOPE:
        raise ValueError(
            f"the calibration scope is {PROJECT_SCOPE!r}; one scope per condition follows #624, "
            f"not {scope!r}"
        )


def _text(value: object) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _finite_attr(group: object, name: str) -> float | None:
    if group is None:
        return None
    value = group.attrs.get(name)  # type: ignore[attr-defined]
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _stored_factors(f: object) -> dict[str, float | None] | None:
    """Return the factors a leakage, γ or correction pass stored, or ``None`` if none ran."""
    settings = f.get(_SETTINGS)  # type: ignore[attr-defined]
    if settings is None:
        return None
    leakage = settings.get("leakage")
    gamma = settings.get("gamma")
    correction = settings.get("correction")
    if leakage is None and gamma is None and correction is None:
        return None
    alpha = _finite_attr(correction, "alpha_override")
    if alpha is None:
        alpha = _finite_attr(leakage, "alpha")
    g = _finite_attr(correction, "gamma_override")
    if g is None:
        g = _finite_attr(gamma, "gamma")
    return {"alpha": alpha, "gamma": g}


def _rows(f: object) -> list[dict[str, str]]:
    settings = f.get(_SETTINGS)  # type: ignore[attr-defined]
    if settings is None or _TRANSITIONS not in settings:
        return []
    data = settings[_TRANSITIONS][()]
    return [{n: _text(row[n]) for n in _FIELDS} for row in data]


def _record(row: Mapping[str, str]) -> TransitionRecord:
    return TransitionRecord(
        scope=row["scope"],
        from_state=CorrectionState(row["from_state"]),
        event=CorrectionEvent(row["event"]),
        to_state=CorrectionState(row["to_state"]),
        actor=Actor(row["by"], row["actor"]),
        detail=json.loads(row["detail"]) if row["detail"] else {},
        timestamp=row["timestamp"],
        app_version=row["app_version"],
    )


def read_correction_transitions(
    path: str | Path, scope: str | None = None
) -> list[TransitionRecord]:
    """Return the stored transitions, oldest first, of ``scope`` or of every scope (a read)."""
    import h5py  # noqa: PLC0415

    with h5py.File(path, "r") as f:
        rows = _rows(f)
    records = [_record(r) for r in rows]
    return records if scope is None else [r for r in records if r.scope == scope]


def read_correction_state(path: str | Path, scope: str = PROJECT_SCOPE) -> CorrectionStatus:
    """Return the present correction state of ``scope`` in the project at ``path`` (a read).

    With no stored transition the scope is in its start state: GAMMA_REVIEW when the
    project holds factors from an earlier pass, otherwise APPARENT_REVIEW.
    """
    _check_scope(scope)
    return _snapshot(path, scope)[0]


def _snapshot(path: str | Path, scope: str) -> tuple[CorrectionStatus, list[dict[str, str]]]:
    """Return the status of ``scope`` and its rows, both read under one open file."""
    import h5py  # noqa: PLC0415

    with h5py.File(path, "r") as f:
        rows = [r for r in _rows(f) if r["scope"] == scope]
        factors = _stored_factors(f)
    proposal = factors or {"alpha": None, "gamma": None}
    if rows:
        last = _record(rows[-1])
        return CorrectionStatus(scope, last.to_state, True, proposal, last), rows
    start = CorrectionState.GAMMA_REVIEW if factors is not None else INITIAL_STATE
    return CorrectionStatus(scope, start, False, proposal, None), rows


def require_analysis_mode(
    path: str | Path, mode: AnalysisMode | str, scope: str = PROJECT_SCOPE
) -> CorrectionStatus:
    """Allow an analysis in ``mode``, or raise [`CorrectedAnalysisRefused`][].

    An apparent-E analysis is allowed in every state. A corrected analysis is allowed only
    in ANALYSIS_READY; in every other state, CALIBRATION_STALE among them, it is refused
    with a message that names the state. Returns the status that was read.
    """
    mode = AnalysisMode(mode)
    status = read_correction_state(path, scope)
    if mode is AnalysisMode.CORRECTED and status.state is not CorrectionState.ANALYSIS_READY:
        raise CorrectedAnalysisRefused(status.state, scope)
    return status


def _dependencies_current(path: Path, rows: Sequence[Mapping[str, str]]) -> tuple[bool, str]:
    """Whether the factor and correction versions the scope approved and applied are current.

    They are the ``factor_version`` of the last approval and the ``correction_version`` of
    the last application; each is current while it has no stale mark (ADR-0103, ADR-0126).
    """
    from tether.project.jobs import read_stale_marks  # noqa: PLC0415

    wanted: dict[str, str] = {}
    for row in rows:
        detail = json.loads(row["detail"]) if row["detail"] else {}
        if row["event"] == CorrectionEvent.APPROVE:
            wanted["factor_version"] = str(detail.get("factor_version", ""))
        elif row["event"] == CorrectionEvent.APPLY_CORRECTION:
            wanted["correction_version"] = str(detail.get("correction_version", ""))
    for mark in read_stale_marks(path):
        if wanted.get(mark.node) == mark.instance:
            return False, f"{mark.node} {mark.instance!r} is stale ({mark.event})"
    return True, ""


def send_event(
    project: Project,
    event: CorrectionEvent | str,
    actor: Actor,
    *,
    scope: str = PROJECT_SCOPE,
    alpha: float | None = None,
    gamma: float | None = None,
    source: str = "",
    factor_version: str = "",
    correction_version: str = "",
    note: str = "",
) -> CorrectionStatus:
    """Send ``event`` to the correction state of ``scope`` and store the transition.

    The values are what the guards read: ``alpha`` and ``source`` for *confirm bleedthrough
    factor* (valid and sourced) and for *calculate again* (valid or not), ``gamma`` and
    ``source`` for a *sourced override* (and ``alpha`` when it is given) and ``gamma`` for
    *approve*. *approve* records the ``factor_version`` it freezes and *apply correction
    version* the ``correction_version`` it applies, and *finish review* holds only while
    neither has a stale mark. Every value given is stored with the transition.

    Raises [`CorrectionTransitionError`][] when the event has no edge out of the present
    state, unless it is the event that made the present state: then nothing changes and
    the present status is returned. Raises [`CorrectionGuardError`][] when the edge's guard
    does not hold. The single-writer lock is required: it must be present and this writer's,
    and under the open file it is checked again, with the scope's transitions, before the row
    is written, so a steal or a concurrent transition refuses the write.
    """
    event = CorrectionEvent(event)
    _check_scope(scope)
    epoch = _held_nonce(project)
    # The state and the row count come from one read, so the count checked before the
    # append is the count the state was derived from.
    status, rows = _snapshot(project.path, scope)
    seen = len(rows)
    edges = [t for t in TRANSITIONS if t.source is status.state and t.event is event]
    if not edges:
        if status.last is not None and status.last.event is event:
            return status
        raise CorrectionTransitionError(
            f"the event {event.value!r} is not possible in the state {status.state}"
        )
    if event is CorrectionEvent.APPROVE and not factor_version.strip():
        raise ValueError("an approval names the factor_version that it freezes")
    if event is CorrectionEvent.APPLY_CORRECTION and not correction_version.strip():
        raise ValueError("apply correction version names the correction_version it applies")
    chosen, reason = _choose(
        edges,
        project.path,
        rows,
        alpha=alpha,
        gamma=gamma,
        source=source,
    )
    if chosen is None:
        raise CorrectionGuardError(
            f"the event {event.value!r} in the state {status.state} needs "
            f"[{edges[0].guard}]: {reason}"
        )
    detail: dict[str, object] = {}
    for key, value in (
        ("alpha", alpha),
        ("gamma", gamma),
        ("source", source),
        ("factor_version", factor_version),
        ("correction_version", correction_version),
        ("note", note),
    ):
        if isinstance(value, float) and not math.isfinite(value):
            detail[key] = str(value)  # JSON has no NaN; the text keeps what the guard read
        elif value not in (None, ""):
            detail[key] = value
    if chosen.guard:
        detail["guard"] = chosen.guard
    if chosen.action:
        detail["action"] = chosen.action
    if not status.stored and status.state is CorrectionState.GAMMA_REVIEW:
        detail["start_proposal"] = dict(status.proposal)
    row = {
        "scope": scope,
        "from_state": status.state.value,
        "event": event.value,
        "to_state": chosen.target.value,
        "by": actor.by,
        "actor": actor.name,
        "detail": json.dumps(detail, sort_keys=True, allow_nan=False),
        "timestamp": datetime.now(UTC).isoformat(timespec="microseconds"),
        "app_version": _app_version(),
    }

    def check(f: object) -> None:
        # Under the open file: the lock is still the one this transition started under, and
        # no transition of the scope landed since the state was read.
        if _held_nonce(project) != epoch:
            from tether.project import lock  # noqa: PLC0415

            raise lock.LockedError(lock.read_lock(project.path), path=project.lock_path)
        if len([r for r in _rows(f) if r["scope"] == scope]) != seen:
            raise CorrectionTransitionError(
                f"the correction state of the {scope} changed while {event.value!r} was checked"
            )

    _append(project.path, row, check)
    return read_correction_state(project.path, scope)


def _choose(
    edges: Sequence[Transition],
    path: Path,
    rows: Sequence[Mapping[str, str]],
    *,
    alpha: float | None,
    gamma: float | None,
    source: str,
) -> tuple[Transition | None, str]:
    """Return the edge among ``edges`` whose guard holds, or ``None`` and the reason."""
    reason = ""
    for edge in edges:
        if edge.guard == "":
            ok, reason = True, ""
        elif edge.guard == _G_ALPHA_SOURCED:
            ok = _alpha_valid(alpha) and _sourced(source)
            reason = "a finite bleedthrough factor and a source are needed"
        elif edge.guard == _G_OVERRIDE:
            ok = _gamma_valid(gamma) and _sourced(source) and (alpha is None or _alpha_valid(alpha))
            reason = "a gamma above 0, a finite bleedthrough factor if one is given, and a source"
        elif edge.guard == _G_GAMMA:
            ok = _gamma_valid(gamma)
            reason = "a finite gamma above 0 is needed"
        elif edge.guard == _G_CURRENT:
            ok, reason = _dependencies_current(path, rows)
        elif edge.guard == _G_ALPHA_INVALID:
            ok = not _alpha_valid(alpha)
        elif edge.guard == _G_ALPHA_VALID:
            ok = _alpha_valid(alpha)
        else:  # pragma: no cover - TRANSITIONS names only the guards above
            raise AssertionError(edge.guard)
        if ok:
            return edge, ""
    return None, reason


def _app_version() -> str:
    from tether.io.schema import _app_version as version  # noqa: PLC0415

    return version()


def _held_nonce(project: Project) -> str:
    """Return the nonce of the writer lock this handle holds, or raise ``LockedError``.

    A transition needs the lock to be present and this writer's: an unlocked project is
    refused, unlike the identity check alone (the ``reconcile_jobs`` rule of ADR-0126).
    """
    from tether.project import lock  # noqa: PLC0415

    project._assert_writable()
    if project._held_lock is not None:  # a retained lock binds to its own nonce
        return project._assert_held_lock()
    current = lock.read_lock(project.path)
    if current is None:
        raise lock.LockedError(None, path=project.lock_path)
    return current.nonce


def _append(path: Path, row: Mapping[str, str], check: Callable[[object], None]) -> None:
    """Append one row to ``/settings/correction_transitions``, created on first use.

    The row is built before the dataset grows, and a failed write shrinks it back, so a
    failed append leaves no empty row (the ``/settings/jobs`` idiom).
    """
    import h5py  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    dtype = np.dtype([(n, h5py.string_dtype(encoding="utf-8")) for n in _FIELDS])
    block = np.zeros(1, dtype=dtype)
    for name in _FIELDS:
        block[name][0] = row[name]
    with h5py.File(path, "r+") as f:
        check(f)
        settings = f[_SETTINGS]
        if _TRANSITIONS not in settings:
            settings.create_dataset(_TRANSITIONS, shape=(0,), maxshape=(None,), dtype=dtype)
        ds = settings[_TRANSITIONS]
        n0 = ds.shape[0]
        ds.resize((n0 + 1,))
        try:
            ds[n0:] = block
        except BaseException:
            ds.resize((n0,))
            raise
