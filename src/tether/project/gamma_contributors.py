# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The γ evidence table: each contributor, its step window, its source and the reason (#927).

G03 has the state GAMMA_REVIEW, "inspect curated bleach-step contributors", and
CALIBRATION_APPROVED freezes the factors with that evidence (PRD §7.2). This module keeps
the evidence:

* ``/settings/gamma_contributors`` — one row for each molecule that
  [`compute_gamma`][tether.project.gamma.compute_gamma] examined: its ``molecule_key``,
  the acceptor-bleach ``step`` and its ``step_source``, the level window
  (``level_window``, ``half_window`` and the absolute ``pre_window`` and
  ``post_window``), the four levels and the two changes, the raw ``ratio``
  ``ΔI_A / ΔI_D`` with its sign, the trace ``gamma``, the estimator's ``gate``, and
  ``included`` with the ``reason``, ``by`` and ``actor`` of an exclusion. With these a
  row can be computed again from ``/traces`` and ``/molecules``. It is recomputable:
  each γ pass writes it again, in the same write as ``/molecules.gamma``.
* ``/settings/gamma_contributor_decisions`` — the append-only log of each exclusion and
  inclusion by a person or a recipe policy, written by [`set_gamma_contributor`][]. The
  latest decision for a ``molecule_key`` holds, and each γ pass reads it.

An excluded row keeps its ratio, also when it is negative, zero or above the ceiling,
because that number is the evidence a person uses to judge the step. The ratio is empty
(``NaN``) only when no step was measured, or when ``ΔI_D`` is zero; then the reason is
``zero-donor-step``. The estimator does not change (ADR-0028): the population γ is the
median of the included rows and needs ``min_qualifying_traces`` of them, so with no
person exclusion the γ is the one before this table existed.

Today the bleach-step detector is the one source of a step (``step_source = "detector"``);
a window that a person sets comes with the calibration windows of #928.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from tether.fret.gamma import GammaEstimate
    from tether.project.core import Project
    from tether.project.correction_states import Actor
    from tether.project.gamma import GammaSummary

__all__ = [
    "CONTRIBUTORS_DTYPE",
    "DECISIONS_DTYPE",
    "STEP_SOURCE_DETECTOR",
    "read_gamma_contributor_decisions",
    "read_gamma_contributors",
    "set_gamma_contributor",
]

_SETTINGS = "settings"
_CONTRIBUTORS = "gamma_contributors"
_DECISIONS = "gamma_contributor_decisions"

#: ``step_source`` of a step that the photobleach detector found.
STEP_SOURCE_DETECTOR = "detector"

#: ``by`` of an exclusion that the estimator's gates made.
_BY_ESTIMATOR = "estimator"

#: ``reason`` of a measured step whose donor did not change: the ratio is empty.
_ZERO_DONOR_STEP = "zero-donor-step"


def _str() -> object:
    import h5py  # noqa: PLC0415

    return h5py.string_dtype(encoding="utf-8")


#: ``/settings/gamma_contributors``: one row for each examined molecule.
CONTRIBUTORS_DTYPE = np.dtype(
    [
        ("molecule_key", _str()),
        ("step", "<i4"),
        ("step_source", _str()),
        ("level_window", _str()),
        ("half_window", "<i4"),
        ("pre_window", "<i4", (2,)),
        ("post_window", "<i4", (2,)),
        ("donor_pre", "<f8"),
        ("donor_post", "<f8"),
        ("acceptor_pre", "<f8"),
        ("acceptor_post", "<f8"),
        ("delta_donor", "<f8"),
        ("delta_acceptor", "<f8"),
        ("ratio", "<f8"),
        ("gamma", "<f8"),
        ("gate", _str()),
        ("included", "?"),
        ("reason", _str()),
        ("by", _str()),
        ("actor", _str()),
    ]
)

#: ``/settings/gamma_contributor_decisions``: one row for each exclusion or inclusion.
DECISIONS_DTYPE = np.dtype(
    [
        ("molecule_key", _str()),
        ("included", "?"),
        ("reason", _str()),
        ("by", _str()),
        ("actor", _str()),
        ("timestamp", _str()),
        ("app_version", _str()),
    ]
)


def _text(value: object) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _read(f: object, name: str, dtype: np.dtype) -> np.ndarray:
    settings = f.get(_SETTINGS)  # type: ignore[attr-defined]
    if settings is None or name not in settings:
        return np.zeros(0, dtype=dtype)
    rows = settings[name][()]
    for field in rows.dtype.names or ():
        if rows.dtype[field].kind == "O":  # h5py reads a UTF-8 string as bytes
            rows[field] = [_text(v) for v in rows[field]]
    return rows


def latest_decisions(f: object) -> dict[str, tuple[bool, str, str, str]]:
    """Return the latest ``(included, reason, by, actor)`` per ``molecule_key`` in ``f``."""
    out: dict[str, tuple[bool, str, str, str]] = {}
    for row in _read(f, _DECISIONS, DECISIONS_DTYPE):
        out[_text(row["molecule_key"])] = (
            bool(row["included"]),
            _text(row["reason"]),
            _text(row["by"]),
            _text(row["actor"]),
        )
    return out


def excluded_indices(keys: list[str], decisions: Mapping[str, tuple[bool, str, str, str]]) -> set:
    """Return the indices of ``keys`` whose latest decision excludes them."""
    return {i for i, key in enumerate(keys) if key in decisions and not decisions[key][0]}


def contributor_rows(
    estimate: GammaEstimate,
    keys: list[str],
    starts: list[int],
    decisions: Mapping[str, tuple[bool, str, str, str]],
    *,
    level_window: str,
    half_window: int,
) -> np.ndarray:
    """Build the table rows of a γ pass; ``starts`` turn local frames into absolute ones."""
    rows = np.zeros(len(keys), dtype=CONTRIBUTORS_DTYPE)
    for name in ("reason", "by", "actor"):
        rows[name] = ""  # an included row has no exclusion; the string fields start as 0
    for i, (t, key, start) in enumerate(zip(estimate.per_trace, keys, starts, strict=True)):
        row = rows[i]
        row["molecule_key"] = key
        row["step"] = t.step + start
        row["step_source"] = STEP_SOURCE_DETECTOR
        row["level_window"] = level_window
        row["half_window"] = half_window
        row["pre_window"] = (t.pre_window[0] + start, t.pre_window[1] + start)
        row["post_window"] = (t.post_window[0] + start, t.post_window[1] + start)
        for name in ("donor_pre", "donor_post", "acceptor_pre", "acceptor_post"):
            row[name] = getattr(t, name)
        row["delta_donor"] = t.delta_donor
        row["delta_acceptor"] = t.delta_acceptor
        row["ratio"] = t.ratio
        row["gamma"] = t.gamma if t.gamma is not None else np.nan
        row["gate"] = t.reason
        decision = decisions.get(key)
        if decision is not None and not decision[0]:
            _, row["reason"], row["by"], row["actor"] = decision
            row["included"] = False
        elif t.gamma is None:
            zero = t.step_measured and t.delta_donor == 0.0
            row["reason"] = _ZERO_DONOR_STEP if zero else t.reason
            row["by"] = _BY_ESTIMATOR
            row["included"] = False
        else:
            row["included"] = True
    return rows


def write_contributors(f: object, rows: np.ndarray) -> None:
    """Replace ``/settings/gamma_contributors`` in the open file ``f`` with ``rows``."""
    settings = f[_SETTINGS]  # type: ignore[index]
    if _CONTRIBUTORS in settings:
        del settings[_CONTRIBUTORS]
    settings.create_dataset(_CONTRIBUTORS, data=rows, dtype=CONTRIBUTORS_DTYPE)


def drop_contributors(f: object) -> None:
    """Remove ``/settings/gamma_contributors``, as a withheld leakage pass removes γ."""
    settings = f[_SETTINGS]  # type: ignore[index]
    if _CONTRIBUTORS in settings:
        del settings[_CONTRIBUTORS]


def append_decision(f: object, decision: Mapping[str, object]) -> None:
    """Append one row to ``/settings/gamma_contributor_decisions``, created on first use."""
    block = np.zeros(1, dtype=DECISIONS_DTYPE)
    for name in DECISIONS_DTYPE.names or ():
        block[name][0] = decision[name]
    settings = f[_SETTINGS]  # type: ignore[index]
    if _DECISIONS not in settings:
        settings.create_dataset(_DECISIONS, shape=(0,), maxshape=(None,), dtype=DECISIONS_DTYPE)
    ds = settings[_DECISIONS]
    n0 = ds.shape[0]
    ds.resize((n0 + 1,))
    try:
        ds[n0:] = block
    except BaseException:
        ds.resize((n0,))
        raise


def read_gamma_contributors(path: str | Path) -> np.ndarray:
    """Return the γ evidence table of the project at ``path`` (empty before a γ pass)."""
    import h5py  # noqa: PLC0415

    with h5py.File(path, "r") as f:
        return _read(f, _CONTRIBUTORS, CONTRIBUTORS_DTYPE)


def read_gamma_contributor_decisions(path: str | Path) -> np.ndarray:
    """Return every stored exclusion and inclusion, oldest first."""
    import h5py  # noqa: PLC0415

    with h5py.File(path, "r") as f:
        return _read(f, _DECISIONS, DECISIONS_DTYPE)


def _stamped_parameters(path: Path) -> dict[str, object]:
    """Return the parameters of the last γ pass, so a decision computes γ the same way."""
    import h5py  # noqa: PLC0415

    with h5py.File(path, "r") as f:
        grp = f.get(f"{_SETTINGS}/gamma")
        if grp is None:
            raise ValueError("the project has no γ pass; run compute_gamma() first")
        a = grp.attrs
        params: dict[str, object] = {
            "intensity_quantity": _text(a["intensity_quantity"]),
            "half_window": None
            if _text(a["level_window"]) == "whole-segment"
            else int(a["half_window"]),
            "min_window_frames": int(a["min_window_frames"]),
            "ceiling": float(a["ceiling"]),
            "min_qualifying_traces": int(a["min_qualifying_traces"]),
        }
        if "min_clean_step_fraction" in a:
            params["min_clean_step_fraction"] = float(a["min_clean_step_fraction"])
    return params


def set_gamma_contributor(
    project: Project,
    molecule_key: str,
    *,
    include: bool,
    actor: Actor,
    reason: str = "",
) -> GammaSummary | None:
    """Exclude a γ contributor with a reason, or include it again, and compute γ again.

    ``actor`` is a person or a recipe policy
    ([`Actor`][tether.project.correction_states.Actor]). An exclusion needs a ``reason``.
    An inclusion withdraws an exclusion by a person or a policy; a row that the
    estimator's gates exclude stays excluded with the gate's reason. The decision and the
    new γ pass, with the parameters stamped in ``/settings/gamma``, are written in one
    guarded write. Returns the new [`GammaSummary`][tether.project.gamma.GammaSummary], or
    ``None`` when the decision changes nothing (and no decision or γ pass is written; the
    follow-up event below is still sent if an earlier send of it failed).

    The correction state (#925) follows the change. In CALIBRATION_APPROVED the evidence is
    frozen and the change is refused. After approval it is the event *calibration evidence
    changes* (CORRECTED_REVIEW) or *factor or contributor evidence changes*
    (ANALYSIS_READY), sent before the write, so the state is CALIBRATION_STALE. In
    GAMMA_REVIEW a γ withheld below ``min_qualifying_traces`` included rows sends
    *insufficient evidence* (CALIBRATION_BLOCKED); in CALIBRATION_BLOCKED a γ that is valid
    again sends *new evidence* (GAMMA_REVIEW).

    Raises
    ------
    ValueError
        If an exclusion has no reason, or the project has no γ pass.
    KeyError
        If ``molecule_key`` is not a row of the γ evidence table.
    tether.project.correction_states.CorrectionTransitionError
        In CALIBRATION_APPROVED.
    tether.project.lock.LockedError
        If this handle does not hold the project's writer lock, before anything is written,
        or if the lock changes hands during the write.
    """
    from tether.project import correction_states as cs  # noqa: PLC0415

    reason = reason.strip()
    if not include and not reason:
        raise ValueError("an exclusion of a γ contributor needs a reason")
    # The transitions of this project wait while the state is read, the γ pass runs and the
    # events are sent, so an approval cannot land in between (it is reentrant for send_event).
    with cs._transition_lock(project.path):
        return _set_held(project, molecule_key, include=include, actor=actor, reason=reason)


def _set_held(
    project: Project, molecule_key: str, *, include: bool, actor: Actor, reason: str
) -> GammaSummary | None:
    """Run [`set_gamma_contributor`][] under the transition lock of its project."""
    import h5py  # noqa: PLC0415

    from tether.project import correction_states as cs  # noqa: PLC0415
    from tether.project import lock  # noqa: PLC0415
    from tether.project.gamma import _compute_gamma  # noqa: PLC0415

    # The writer lock must be held, and be this writer's, before anything is written: the
    # γ pass is bound to its nonce, as a transition is.
    epoch = cs._held_nonce(project)

    def guard() -> None:
        if cs._held_nonce(project) != epoch:
            raise lock.LockedError(lock.read_lock(project.path), path=project.lock_path)

    params = _stamped_parameters(project.path)
    keys = {_text(k) for k in read_gamma_contributors(project.path)["molecule_key"]}
    if molecule_key not in keys:
        raise KeyError(f"{molecule_key!r} is not a row of the γ evidence table")
    with h5py.File(project.path, "r") as f:
        current = latest_decisions(f).get(molecule_key)
    now_included = current is None or current[0]
    word = "included" if include else f"excluded: {reason}"
    note = f"gamma contributor {molecule_key} {word}"
    if (include and now_included) or (
        not include and current is not None and current[:4] == (False, reason, actor.by, actor.name)
    ):
        # Nothing to write. A retry after a failed follow-up event still sends that event,
        # because the stored γ pass, not this call, decides whether the state must move.
        _send_follow_up(project, actor, note, applied=not _stored_withheld(project.path))
        return None

    state = cs.read_correction_state(project.path).state
    if state is cs.CorrectionState.CALIBRATION_APPROVED:
        raise cs.CorrectionTransitionError(
            "the γ contributor evidence is frozen in CALIBRATION_APPROVED; apply the correction "
            "version first, and a change then makes the calibration stale"
        )
    if state is cs.CorrectionState.CORRECTED_REVIEW:
        cs.send_event(project, cs.CorrectionEvent.CALIBRATION_EVIDENCE_CHANGES, actor, note=note)
    elif state is cs.CorrectionState.ANALYSIS_READY:
        cs.send_event(project, cs.CorrectionEvent.FACTOR_EVIDENCE_CHANGES, actor, note=note)

    decision = {
        "molecule_key": molecule_key,
        "included": bool(include),
        "reason": "" if include else reason,
        "by": actor.by,
        "actor": actor.name,
        "timestamp": datetime.now(UTC).isoformat(timespec="microseconds"),
        "app_version": cs._app_version(),
    }
    summary = _compute_gamma(project.path, write_guard=guard, decision=decision, **params)
    _send_follow_up(project, actor, note, applied=summary.applied)
    return summary


def _stored_withheld(path: Path) -> bool:
    """Return whether the stored γ pass withheld the factor."""
    import h5py  # noqa: PLC0415

    with h5py.File(path, "r") as f:
        return bool(f[f"{_SETTINGS}/gamma"].attrs["withheld"])


def _send_follow_up(project: Project, actor: Actor, note: str, *, applied: bool) -> None:
    """Move GAMMA_REVIEW or CALIBRATION_BLOCKED to agree with the stored γ pass."""
    from tether.project import correction_states as cs  # noqa: PLC0415

    state = cs.read_correction_state(project.path).state
    if state is cs.CorrectionState.GAMMA_REVIEW and not applied:
        cs.send_event(project, cs.CorrectionEvent.INSUFFICIENT_EVIDENCE, actor, note=note)
    elif state is cs.CorrectionState.CALIBRATION_BLOCKED and applied:
        cs.send_event(project, cs.CorrectionEvent.NEW_EVIDENCE, actor, note=note)
