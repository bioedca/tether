# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The durable record of each lock takeover, kept inside the project (#1099, ADR-0121).

[`recover_lock`][tether.project.lock.recover_lock] takes a lock that a crashed session
left behind (**Recover**) or that a stale foreign writer holds (**Take over**), and logs
it at WARNING on ``tether.project.lock`` (#626). A log line ends with the session, so
this module also keeps one row per successful takeover in ``/settings/lock_takeovers``:
a later reader of the ``.tether`` can see that a takeover happened, when, and whose
unsaved work it may have cost.

``/settings/lock_takeovers`` is a lazily created, resizable compound dataset under the
frozen ``/settings`` container, the ``/settings/molecule_audit`` idiom. That makes it
additive **data**, not structure: the ``schema-guard`` freeze (ADR-0005) is untouched and
``SCHEMA_VERSION`` stays where it is, so a project written before this change opens as it
always did and [`read_takeovers`][] reports zero rows for it.

The row is written by the caller **after** it reopens the project writable under the
new lock, through the ordinary single-writer path
([`Project.record_lock_takeover`][tether.project.core.Project.record_lock_takeover]).
A takeover that was refused, failed, or whose reopen was not writable therefore writes
no row. As for every module-level ``r+`` writer in this package, the single-writer
``.lock`` is the caller's responsibility; the [`Project`][tether.project.core.Project]
method checks it before it calls in.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

    from tether.project.lock import LockInfo, RecoveryAction

__all__ = ["TAKEOVER_ACTIONS", "read_takeovers", "record_takeover"]

_SETTINGS = "settings"
#: The append-only takeover log, created lazily under ``/settings``.
_DATASET = "lock_takeovers"

#: The closed ``action`` vocabulary: the two values of
#: [`RecoveryAction`][tether.project.lock.RecoveryAction] that take a lock.
TAKEOVER_ACTIONS = frozenset({"recover", "take_over"})

#: The ``ousted_pid`` of a row whose ousted lock was corrupt (its holder is unknown).
_UNKNOWN_PID = -1


def _dtype() -> np.dtype:
    """Return the ``/settings/lock_takeovers`` compound dtype (a ``/settings`` artifact)."""
    import h5py  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    s = h5py.string_dtype(encoding="utf-8")
    return np.dtype(
        [
            ("timestamp", s),  # the new lock's UTC acquisition instant
            ("action", s),  # one of TAKEOVER_ACTIONS
            ("holder_user", s),
            ("holder_host", s),
            ("holder_pid", "<i8"),
            ("ousted_corrupt", "u1"),  # 1 when the ousted lock could not be parsed
            ("ousted_user", s),
            ("ousted_host", s),
            ("ousted_pid", "<i8"),
            ("ousted_timestamp", s),  # the ousted lock's last acquire or refresh
            ("app_version", s),
        ]
    )


def _app_version() -> str:
    """Best-effort Tether version for the record's provenance stamp (NFR-REPRO)."""
    try:
        from tether import __version__  # noqa: PLC0415

        return str(__version__)
    except Exception:  # pragma: no cover - defensive; version is normally present
        return "0.0.0+unknown"


def _row(action: str, taken: LockInfo, ousted: LockInfo | None) -> np.ndarray:
    """Build the one-row array for a takeover, before any file I/O."""
    import numpy as np  # noqa: PLC0415

    out = np.zeros(1, dtype=_dtype())
    out["timestamp"] = taken.timestamp
    out["action"] = action
    out["holder_user"] = taken.user
    out["holder_host"] = taken.host
    out["holder_pid"] = taken.pid
    out["ousted_corrupt"] = 1 if ousted is None else 0
    out["ousted_user"] = "" if ousted is None else ousted.user
    out["ousted_host"] = "" if ousted is None else ousted.host
    out["ousted_pid"] = _UNKNOWN_PID if ousted is None else ousted.pid
    out["ousted_timestamp"] = "" if ousted is None else ousted.timestamp
    out["app_version"] = _app_version()
    return out


def record_takeover(
    path: str | Path,
    action: RecoveryAction | str,
    taken: LockInfo,
    ousted: LockInfo | None,
) -> np.ndarray:
    """Append one row for a successful takeover to ``/settings/lock_takeovers``.

    Parameters
    ----------
    path:
        The project the lock was taken on.
    action:
        ``"recover"`` or ``"take_over"`` (a [`RecoveryAction`][tether.project.lock.RecoveryAction]
        is accepted); ``"none"`` takes no lock and is refused.
    taken, ousted:
        The ``(new, ousted)`` pair [`recover_lock`][tether.project.lock.recover_lock]
        returned. ``ousted`` is ``None`` for a corrupt lock, whose holder is unknown.

    Returns
    -------
    numpy.ndarray
        The appended row.

    Raises
    ------
    ValueError
        If ``action`` is not one of [`TAKEOVER_ACTIONS`][]. Nothing is written.
    """
    import h5py  # noqa: PLC0415

    value = str(getattr(action, "value", action))
    if value not in TAKEOVER_ACTIONS:
        raise ValueError(
            f"action {value!r} is not a takeover; expected one of {sorted(TAKEOVER_ACTIONS)}"
        )
    row = _row(value, taken, ousted)
    with h5py.File(Path(path), "r+") as f:
        settings = f[_SETTINGS]
        if _DATASET not in settings:
            settings.create_dataset(_DATASET, shape=(0,), maxshape=(None,), dtype=row.dtype)
        ds = settings[_DATASET]
        n0 = ds.shape[0]
        ds.resize((n0 + 1,))
        try:
            ds[n0:] = row
        except BaseException:
            # Never leave a blank row behind: shrink back to what was there.
            ds.resize((n0,))
            raise
    return row


def read_takeovers(path: str | Path) -> np.ndarray:
    """Read ``/settings/lock_takeovers`` in append order; zero rows when never written.

    A project written before #1099, or one whose lock was never taken over, has no
    such dataset and reads as no takeovers.
    """
    import h5py  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    with h5py.File(Path(path), "r") as f:
        settings = f[_SETTINGS]
        if _DATASET not in settings:
            return np.zeros(0, dtype=_dtype())
        return settings[_DATASET][:]
