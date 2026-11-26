# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Calibration windows: the frames of a trace whose bleach step is the γ evidence (#928).

G03 has two different events. *Edit analysis windows or curation* takes ANALYSIS_READY back
to CORRECTED_REVIEW; *calibration evidence changes* makes the calibration stale. Note 03 of
G03 says the calibration windows are distinct from the FRET-analysis windows: the evidence
after the acceptor bleach stays available for the calibration (PRD §7.2, ADR-0156). So a
trace has two windows:

* ``/molecules.analysis_window`` — the frames the analysis reads
  ([`tether.project.molecule_edits`][]).
* Its **calibration window** — the ``[start, stop)`` frames that
  [`compute_gamma`][tether.project.gamma.compute_gamma] reads the acceptor-bleach step
  from. A row with no stored window has the **default**, read from the bleach-step fields
  it already has: ``frame_range[0]`` to the donor's first-bleach frame
  (``bleach_frames[0]``), the pre-step FRET segment and the post-step donor-only segment.
  When the donor bleach is not detected, or is at the first frame, the default is the whole
  ``frame_range``. The default reads the same frames as a γ pass did before #928, so an
  existing store keeps its γ and needs no rewrite.

A window that a person or a recipe policy sets is one row of the append-only
``/calibration_windows/table`` (schema version 4); the latest row of a ``molecule_id``
holds, and the window ``(-1, -1)`` gives the row back its default. A first write to a store
stamped before 4 adds the table and backs the file up first (PRD §5.4, ADR-0136).

An edit of a calibration window is the event *calibration evidence changes*: refused in
CALIBRATION_APPROVED, where the evidence is frozen, and after the application of the
correction it makes the calibration stale, as an exclusion of a γ contributor does
([`tether.project.gamma_contributors`][]). When the project has a γ pass, the same publish
computes it again with its stamped parameters, so the γ evidence table always reads the
stored windows.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from tether.io.schema import CALIBRATION_WINDOWS_DTYPE, TABLE

if TYPE_CHECKING:
    from tether.project.core import Project
    from tether.project.correction_states import Actor
    from tether.project.gamma import GammaSummary

__all__ = [
    "SOURCE_BLEACH_STEP",
    "SOURCE_SET",
    "read_calibration_window_log",
    "read_calibration_windows",
    "reset_calibration_window",
    "resolved_calibration_windows",
    "set_calibration_window",
]

_GROUP = "calibration_windows"
_MOLECULES = "molecules"
#: The stored window that gives a row back its default.
_DEFAULT = (-1, -1)
#: The source of a default window: the row's bleach-step fields.
SOURCE_BLEACH_STEP = "bleach-step"
#: The source of a window that a person or a policy set.
SOURCE_SET = "set"


def _text(value: object) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _default(frame_range: object, bleach_frames: object) -> tuple[int, int]:
    """Return the default window of a row, from ``frame_range`` and the donor's bleach frame."""
    lo, hi = int(frame_range[0]), int(frame_range[1])  # type: ignore[index]
    donor = int(bleach_frames[0])  # type: ignore[index]
    return (lo, donor) if lo < donor < hi else (lo, hi)


def _stored(f: object) -> dict[str, tuple[int, int]]:
    """Return the latest stored window of each ``molecule_id`` in the open file ``f``."""
    group = f.get(_GROUP)  # type: ignore[attr-defined]
    if group is None or TABLE not in group:
        return {}
    out: dict[str, tuple[int, int]] = {}
    for row in group[TABLE][()]:
        out[_text(row["molecule_id"])] = (int(row["window"][0]), int(row["window"][1]))
    return out


def resolved_calibration_windows(f: object, table: np.ndarray) -> tuple[np.ndarray, list[str]]:
    """Return each ``/molecules`` row's calibration window and its source, from open ``f``.

    ``table`` is the ``/molecules`` table read from ``f``. A stored window is clipped into
    the row's ``frame_range``; one with nothing left inside, or ``(-1, -1)``, reads the
    default.
    """
    stored = _stored(f)
    windows = np.zeros((table.shape[0], 2), dtype=np.int64)
    sources: list[str] = []
    for i in range(table.shape[0]):
        fr = table["frame_range"][i]
        lo, hi = int(fr[0]), int(fr[1])
        window = stored.get(_text(table["molecule_id"][i]), _DEFAULT)
        start, stop = max(window[0], lo), min(window[1], hi)
        if window != _DEFAULT and start < stop:
            windows[i] = (start, stop)
            sources.append(SOURCE_SET)
        else:
            windows[i] = _default(fr, table["bleach_frames"][i])
            sources.append(SOURCE_BLEACH_STEP)
    return windows, sources


def read_calibration_windows(path: str | Path) -> tuple[np.ndarray, list[str]]:
    """Return the calibration window and its source of each ``/molecules`` row at ``path``."""
    import h5py  # noqa: PLC0415

    with h5py.File(path, "r") as f:
        return resolved_calibration_windows(f, f[_MOLECULES][TABLE][()])


def read_calibration_window_log(path: str | Path) -> np.ndarray:
    """Return every stored calibration-window row, oldest first (empty before the first)."""
    import h5py  # noqa: PLC0415

    with h5py.File(path, "r") as f:
        group = f.get(_GROUP)
        if group is None or TABLE not in group:
            return np.zeros(0, dtype=CALIBRATION_WINDOWS_DTYPE)
        return group[TABLE][()]


def set_calibration_window(
    project: Project, molecule_key: str, start: int, stop: int, *, actor: Actor, reason: str = ""
) -> GammaSummary | None:
    """Set a molecule's calibration window to ``[start, stop)``, clamped to its frames.

    Each row carrying ``molecule_key`` gets the request clamped into its own
    ``frame_range`` by the rule of
    [`set_analysis_window`][tether.project.molecule_edits.set_analysis_window]. The
    ``/molecules.analysis_window`` is not changed. The correction state follows as the
    module docstring says. Returns the new γ pass, or ``None`` when the project has none or
    nothing changed (then nothing is written).

    Raises
    ------
    KeyError
        If no ``/molecules`` row carries ``molecule_key``.
    ValueError
        If ``start >= stop`` or a bound is not an integer.
    tether.project.correction_states.CorrectionTransitionError
        In CALIBRATION_APPROVED.
    tether.project.lock.LockedError
        If this handle does not hold the project's writer lock.
    """
    import numbers  # noqa: PLC0415

    for value in (start, stop):
        if isinstance(value, bool) or not isinstance(value, numbers.Integral):
            raise ValueError(f"a calibration window bound must be an integer, got {value!r}")
    if int(start) >= int(stop):
        raise ValueError(f"a calibration window needs start < stop, got [{start}, {stop})")
    return _write(project, molecule_key, (int(start), int(stop)), actor=actor, reason=reason)


def reset_calibration_window(
    project: Project, molecule_key: str, *, actor: Actor, reason: str = ""
) -> GammaSummary | None:
    """Give a molecule back the default calibration window, read from its bleach step.

    The same matching, correction-state rule and γ pass as [`set_calibration_window`][].
    """
    return _write(project, molecule_key, None, actor=actor, reason=reason)


def _write(
    project: Project,
    molecule_key: str,
    window: tuple[int, int] | None,
    *,
    actor: Actor,
    reason: str,
) -> GammaSummary | None:
    from tether.project import correction_states as cs  # noqa: PLC0415

    with cs._transition_lock(project.path):
        return _write_held(project, molecule_key, window, actor=actor, reason=reason.strip())


def _targets(
    f: object, table: np.ndarray, idx: list[int], window: tuple[int, int] | None
) -> dict[int, tuple[int, int]]:
    """Return the window each matched row will store, for the rows whose window changes."""
    from tether.project.molecule_edits import _clamped_window, _n_frames  # noqa: PLC0415

    stored = _stored(f)
    n_frames = _n_frames(f)
    out: dict[int, tuple[int, int]] = {}
    for i in idx:
        old = stored.get(_text(table["molecule_id"][i]), _DEFAULT)
        new = (
            _DEFAULT
            if window is None
            else _clamped_window(table["frame_range"][i], window[0], window[1], n_frames)
        )
        if new != old:
            out[i] = new
    return out


def _write_held(
    project: Project,
    molecule_key: str,
    window: tuple[int, int] | None,
    *,
    actor: Actor,
    reason: str,
) -> GammaSummary | None:
    import h5py  # noqa: PLC0415

    from tether.io.schema import upgrade_skeleton  # noqa: PLC0415
    from tether.project import correction_states as cs  # noqa: PLC0415
    from tether.project import lock  # noqa: PLC0415
    from tether.project._guarded_publish import guarded_project_write  # noqa: PLC0415
    from tether.project.gamma import _compute_gamma  # noqa: PLC0415
    from tether.project.gamma_contributors import (  # noqa: PLC0415
        _send_follow_up,
        _stamped_parameters,
    )
    from tether.project.molecule_edits import _matched_rows  # noqa: PLC0415

    epoch = cs._held_nonce(project)

    def guard() -> None:
        if cs._held_nonce(project) != epoch:
            raise lock.LockedError(lock.read_lock(project.path), path=project.lock_path)

    with h5py.File(project.path, "r") as f:
        table = f[_MOLECULES][TABLE][()]
        idx = _matched_rows(table, molecule_key, project.path)
        targets = _targets(f, table, idx, window)
        has_gamma = "gamma" in f["settings"]
    if not targets:
        return None

    word = "reset" if window is None else f"set to {window}"
    note = f"calibration window of {molecule_key} {word}"
    state = cs.read_correction_state(project.path).state
    if state is cs.CorrectionState.CALIBRATION_APPROVED:
        raise cs.CorrectionTransitionError(
            "the calibration evidence is frozen in CALIBRATION_APPROVED; apply the correction "
            "version first, and a change then makes the calibration stale"
        )
    if state is cs.CorrectionState.CORRECTED_REVIEW:
        cs.send_event(project, cs.CorrectionEvent.CALIBRATION_EVIDENCE_CHANGES, actor, note=note)
    elif state is cs.CorrectionState.ANALYSIS_READY:
        cs.send_event(project, cs.CorrectionEvent.FACTOR_EVIDENCE_CHANGES, actor, note=note)

    rows = np.zeros(len(targets), dtype=CALIBRATION_WINDOWS_DTYPE)
    stamp = datetime.now(UTC).isoformat(timespec="microseconds")
    for j, (i, new) in enumerate(sorted(targets.items())):
        rows[j] = (
            _text(table["molecule_id"][i]),
            molecule_key,
            new,
            actor.by,
            actor.name,
            reason,
            stamp,
            cs._app_version(),
        )
    params = _stamped_parameters(project.path) if has_gamma else None
    summary = None
    # One publish: the table (and the version 4 stamp, after a backup), then the γ pass.
    with guarded_project_write(project.path, write_guard=guard, label="calibration") as staged:
        with h5py.File(staged, "r+") as f:
            upgrade_skeleton(f)
            ds = f[_GROUP][TABLE]
            n0 = ds.shape[0]
            ds.resize((n0 + len(rows),))
            ds[n0:] = rows
        if params is not None:
            summary = _compute_gamma(staged, write_guard=guard, **params)
    if summary is not None:
        _send_follow_up(project, actor, note, applied=summary.applied)
    return summary
