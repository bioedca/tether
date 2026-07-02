# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Single-writer ``<file>.lock`` lifecycle + shared-storage hygiene (PRD §5.4, §7.10).

FR-CONCURRENCY. A ``.tether`` project commonly lives on OneDrive or a network
share where several lab members can open the same file. HDF5 is not multi-writer
safe, so Tether enforces a **one-owner-at-a-time** posture (PRD §5.4): a sidecar
``<file>.lock`` marks the current writer, non-owners are refused canonical writes
(the GUI shows a read-only banner, M2 S9 PR-B), and a **steal-lock** hands the
file over with a typed confirmation. This module is the Qt-free, headless core of
that lifecycle (PRD §7.11 — the GUI is a thin layer over the scriptable core).

Why wall-clock staleness, not PID-liveness (PRD §5.4)
-----------------------------------------------------
OneDrive is eventually-consistent and a **remote** PID cannot be probed across
machines, so liveness is judged by a **wall-clock staleness timeout** (default
≈ 30 min, PRD §11.2 "Lock staleness timeout") rather than by checking whether the
recorded PID is alive. A lock older than the timeout is *stale* — reclaimable, but
still only via an explicit steal (the timeout enables the steal; it does not
silently auto-acquire). Ownership is judged by the full ``(host, user, pid)``
identity: a different machine, process, or login is a foreign writer refused until
it steals. Comparing the login too keeps the guard safe against PID reuse — a
recycled PID owned by a *different* user is treated as foreign, never silently
granted write access.

The ``.lock`` sidecar is a JSON file (``<file>.tether.lock``) carrying
``host`` / ``user`` / ``pid`` / ``timestamp`` (PRD §5.1) plus a per-acquisition
``nonce`` so a holder can prove ownership for release even after PID reuse. It is
**not** part of the M0-frozen HDF5 schema (the schema module says so explicitly),
so nothing here touches ``schema-guard``. "Last-write-wins with version stamping"
(§5.4) is the HDF5 ``r+`` semantics plus the file's monotonic ``schema_version``;
a steal *surfaces* the prior owner (:func:`steal_lock` returns them) so the GUI can
warn the stealer — the prior owner's unsaved work is never silently merged back.

Concurrent curation without blocking (PRD §7.10)
------------------------------------------------
Curation is the central daily workflow and must never be blocked while a file is
locked, so a locked-out non-owner can still **browse the canonical file read-only
and curate into their own split/subset ``.tether``** — writing provenance-tagged
``/labels`` keyed by the stable ``molecule_key`` (§5.1/§7.10) via
:func:`create_split_curation`. That split is the *producer* side of the M5
owner-pull merge (the owner's retrain later joins every split's ``/labels`` on
``molecule_key``); the merge itself is M5, not here.

OneDrive is also **detected, not prevented** (§5.4): :func:`conflict_copies`
surfaces the sync-conflict duplicates OneDrive/SharePoint leave beside the
project so the user can reconcile them.
"""

from __future__ import annotations

import getpass
import glob as _glob
import json
import os
import socket
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from tether.project.core import Project

__all__ = [
    "DEFAULT_STALENESS_TIMEOUT_S",
    "LOCK_SUFFIX",
    "CorruptLockError",
    "LockError",
    "LockIdentity",
    "LockInfo",
    "LockedError",
    "acquire",
    "assert_writable",
    "conflict_copies",
    "create_split_curation",
    "held_lock",
    "local_identity",
    "lock_path",
    "read_lock",
    "release",
    "steal_lock",
]

#: Default wall-clock staleness timeout in seconds (PRD §11.2: "Lock staleness
#: timeout ≈ 30 min (wall-clock), then steal-confirm"). Configurable per call.
DEFAULT_STALENESS_TIMEOUT_S: float = 30.0 * 60.0

#: Suffix appended to the project file name to form the sidecar lock path
#: (``exp.tether`` -> ``exp.tether.lock``), so it never collides with a sibling
#: project sharing the stem but a different extension.
LOCK_SUFFIX = ".lock"


# --- errors ------------------------------------------------------------------


class LockError(RuntimeError):
    """Base class for single-writer lock failures."""


class CorruptLockError(LockError):
    """A ``.lock`` sidecar exists but is not readable/parseable JSON.

    Surfaced rather than silently ignored: an unparseable lock has an unknown
    owner, so a write is refused (via :class:`LockedError`) until the lock is
    explicitly stolen (which overwrites it).
    """


class LockedError(LockError):
    """Raised when a canonical write/acquire is refused by a foreign lock.

    Attributes
    ----------
    owner:
        The current lock holder, or ``None`` when the lock is present but corrupt.
    stale:
        Whether the held lock is older than the staleness timeout (PRD §5.4) — the
        GUI uses this to offer a one-click steal ("owner idle > 30 min").
    corrupt:
        Whether the refusal is due to an unparseable lock rather than a live owner.
    path:
        The ``.lock`` sidecar path, for the message/diagnostics.
    """

    def __init__(
        self,
        owner: LockInfo | None,
        *,
        stale: bool = False,
        corrupt: bool = False,
        path: Path | None = None,
    ) -> None:
        self.owner = owner
        self.stale = stale
        self.corrupt = corrupt
        self.path = path
        if corrupt:
            detail = "the lock file is corrupt (unknown owner)"
        elif owner is not None:
            detail = (
                f"held by {owner.user}@{owner.host} (pid {owner.pid}, "
                f"acquired {owner.timestamp}{'; stale' if stale else ''})"
            )
        else:
            detail = "held by another writer"
        super().__init__(f"{path if path is not None else 'project'} is locked: {detail}")


# --- identity + lock record --------------------------------------------------


@dataclass(frozen=True)
class LockIdentity:
    """The writer identity that owns (or contends for) a lock: ``(host, user, pid)``.

    Ownership is the **full** ``(host, user, pid)`` identity (this dataclass's value
    equality). A single live process is one owner; a different machine, process, or
    login is a foreign writer refused until it steals (PRD §5.4). Comparing ``user``
    too is deliberate — it keeps the guard safe against PID reuse by a *different*
    login (a recycled PID is never silently granted write access).
    """

    host: str
    user: str
    pid: int


@dataclass(frozen=True)
class LockInfo:
    """A parsed ``<file>.lock`` record (PRD §5.1: host/user/PID/timestamp + nonce)."""

    host: str
    user: str
    pid: int
    #: Offset-aware ISO-8601 acquisition instant (UTC).
    timestamp: str
    #: Per-acquisition token proving ownership for release across PID reuse.
    nonce: str

    @property
    def identity(self) -> LockIdentity:
        """The ``(host, user, pid)`` writer identity of this lock."""
        return LockIdentity(self.host, self.user, self.pid)

    def acquired_at(self) -> datetime:
        """Parse :attr:`timestamp` into an offset-aware :class:`datetime`."""
        parsed = datetime.fromisoformat(self.timestamp)
        if parsed.tzinfo is None:  # defensive: we only ever write offset-aware stamps
            parsed = parsed.replace(tzinfo=UTC)
        return parsed

    def age_seconds(self, *, now: datetime | None = None) -> float:
        """Wall-clock age of the lock in seconds (may be negative under clock skew)."""
        ref = now if now is not None else _utc_now()
        return (ref - self.acquired_at()).total_seconds()

    def is_stale(
        self, *, timeout_s: float = DEFAULT_STALENESS_TIMEOUT_S, now: datetime | None = None
    ) -> bool:
        """Whether the lock is older than ``timeout_s`` (the §5.4 liveness judgment)."""
        return self.age_seconds(now=now) > timeout_s

    def to_dict(self) -> dict[str, object]:
        """A JSON-serializable mapping of the record."""
        return {
            "host": self.host,
            "user": self.user,
            "pid": self.pid,
            "timestamp": self.timestamp,
            "nonce": self.nonce,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> LockInfo:
        """Reconstruct from a parsed JSON mapping.

        Raises :class:`KeyError` / ``TypeError`` on a missing/ill-typed field and
        ``ValueError`` on an unparseable ``timestamp`` — callers map all of these to
        :class:`CorruptLockError`, so a malformed record is treated as a corrupt lock
        up front rather than crashing a later staleness check with a raw ``ValueError``.
        """
        info = cls(
            host=str(data["host"]),
            user=str(data["user"]),
            pid=int(data["pid"]),  # type: ignore[arg-type]
            timestamp=str(data["timestamp"]),
            nonce=str(data["nonce"]),
        )
        info.acquired_at()  # validate the timestamp is offset-parseable now, not later
        return info


# --- helpers -----------------------------------------------------------------


def _utc_now() -> datetime:
    """Offset-aware current UTC instant."""
    return datetime.now(UTC)


def _decode(value: object) -> str:
    """Decode an h5py variable-length string field (``bytes`` or ``str``)."""
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def local_identity() -> LockIdentity:
    """The identity of the current process: ``(hostname, login, pid)``.

    Best-effort for host/user (falls back to ``"unknown"``), so a lock can always
    be written even on a stripped-down environment.
    """
    try:
        host = socket.gethostname() or "unknown"
    except Exception:  # pragma: no cover - env without a resolvable hostname
        host = "unknown"
    try:
        user = getpass.getuser() or "unknown"
    except Exception:  # pragma: no cover - env without a resolvable login
        user = "unknown"
    return LockIdentity(host=host, user=user, pid=os.getpid())


def lock_path(project_path: str | Path) -> Path:
    """The sidecar ``<file>.lock`` path for a project (``exp.tether`` -> ``exp.tether.lock``)."""
    p = Path(project_path)
    return p.with_name(p.name + LOCK_SUFFIX)


def _new_info(identity: LockIdentity, *, now: datetime | None = None) -> LockInfo:
    """Build a fresh lock record for ``identity`` stamped at ``now`` (default: now)."""
    ts = (now if now is not None else _utc_now()).isoformat()
    return LockInfo(
        host=identity.host, user=identity.user, pid=identity.pid, timestamp=ts, nonce=uuid4().hex
    )


def _atomic_write(lp: Path, info: LockInfo) -> None:
    """Write the lock JSON via a temp file + atomic ``os.replace`` (crash/torn-write safe)."""
    tmp = lp.with_name(f"{lp.name}.tmp-{info.nonce}")
    tmp.write_text(json.dumps(info.to_dict(), indent=2), encoding="utf-8")
    os.replace(tmp, lp)  # atomic on both POSIX and Windows for same-directory paths


def _exclusive_create(lp: Path, info: LockInfo) -> bool:
    """Atomically claim an *unlocked* path via ``O_CREAT | O_EXCL``.

    Returns ``True`` if we created the lock (won the claim), ``False`` if the file
    already existed (a concurrent local writer beat us). ``O_EXCL`` closes the
    first-writer TOCTOU on a single host, where two Tether processes could otherwise
    both observe the path unlocked and both write. Cross-machine on an
    eventually-consistent share this is not atomic — that is exactly why liveness is
    judged by the wall-clock staleness timeout, not by racing the file (PRD §5.4).
    """
    try:
        fd = os.open(lp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(info.to_dict(), indent=2))
    return True


def _read_tolerant(lp: Path) -> tuple[LockInfo | None, bool]:
    """Read a lock file, returning ``(info | None, corrupt)`` without raising.

    ``(None, False)`` — no lock present; ``(info, False)`` — a valid record;
    ``(None, True)`` — present but unparseable.
    """
    try:
        text = lp.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, False
    except OSError:
        return None, True
    try:
        return LockInfo.from_dict(json.loads(text)), False
    except (ValueError, TypeError, KeyError):
        return None, True


# --- public lifecycle --------------------------------------------------------


def read_lock(project_path: str | Path) -> LockInfo | None:
    """Read the current lock holder, or ``None`` if unlocked.

    Raises
    ------
    CorruptLockError
        If the ``.lock`` sidecar exists but cannot be parsed (unknown owner).
    """
    info, corrupt = _read_tolerant(lock_path(project_path))
    if corrupt:
        raise CorruptLockError(f"unparseable lock file: {lock_path(project_path)}")
    return info


def assert_writable(
    project_path: str | Path,
    *,
    identity: LockIdentity | None = None,
    timeout_s: float = DEFAULT_STALENESS_TIMEOUT_S,
    now: datetime | None = None,
) -> None:
    """Refuse a canonical write if a **foreign** lock is held (single-writer guard).

    Passes silently when the file is unlocked or the lock is ours (a full
    ``(host, user, pid)`` identity match). A foreign lock — live *or* stale — raises
    :class:`LockedError` (carrying
    ``stale`` so the GUI can offer a steal); a corrupt lock raises too (unknown
    owner). This is the boundary the :class:`~tether.project.core.Project` writers
    consult before mutating the canonical file.
    """
    identity = identity if identity is not None else local_identity()
    lp = lock_path(project_path)
    info, corrupt = _read_tolerant(lp)
    if corrupt:
        raise LockedError(None, corrupt=True, path=lp)
    if info is None or info.identity == identity:
        return
    raise LockedError(info, stale=info.is_stale(timeout_s=timeout_s, now=now), path=lp)


def acquire(
    project_path: str | Path,
    *,
    identity: LockIdentity | None = None,
    timeout_s: float = DEFAULT_STALENESS_TIMEOUT_S,
    steal: bool = False,
    now: datetime | None = None,
) -> LockInfo:
    """Acquire the single-writer lock, returning the written :class:`LockInfo`.

    With ``steal=False`` (default) a foreign lock — live or stale — raises
    :class:`LockedError` (a stale lock still requires an explicit steal, PRD §5.4);
    an unlocked file, or one already held by this same ``(host, user, pid)``
    identity, is (re)written and refreshed. With ``steal=True`` any existing lock
    is overwritten
    unconditionally (prefer :func:`steal_lock`, which also returns the ousted
    owner so the caller can warn).

    A fresh claim on an unlocked path uses an atomic ``O_CREAT | O_EXCL`` create
    (:func:`_exclusive_create`) so two concurrent local writers cannot both claim it;
    if the race is lost, the winner's lock is re-evaluated as a foreign lock.
    """
    identity = identity if identity is not None else local_identity()
    lp = lock_path(project_path)
    info = _new_info(identity, now=now)
    if steal:
        _atomic_write(lp, info)
        return info

    existing, corrupt = _read_tolerant(lp)
    if corrupt:
        raise LockedError(None, corrupt=True, path=lp)
    if existing is None:
        # Atomically claim the unlocked path; if a local writer beats us, fall
        # through to re-evaluate their now-present lock.
        if _exclusive_create(lp, info):
            return info
        existing, corrupt = _read_tolerant(lp)
        if corrupt:
            raise LockedError(None, corrupt=True, path=lp)
    if existing is not None and existing.identity != identity:
        raise LockedError(existing, stale=existing.is_stale(timeout_s=timeout_s, now=now), path=lp)
    # Unlocked again (raced away) or already ours: (re)write and refresh.
    _atomic_write(lp, info)
    return info


def steal_lock(
    project_path: str | Path,
    *,
    identity: LockIdentity | None = None,
    now: datetime | None = None,
) -> tuple[LockInfo, LockInfo | None]:
    """Force-acquire the lock, returning ``(new_lock, ousted_owner_or_None)``.

    The typed-confirmation UX is a GUI concern (M2 S9 PR-B); this is its headless
    engine. The returned prior owner is what the GUI surfaces to *warn the stealer*
    (PRD §5.4): last-write-wins, and the prior owner's unsaved work is not merged.
    A corrupt prior lock is reported as ``None`` (unknown owner) and overwritten.
    """
    identity = identity if identity is not None else local_identity()
    prior, _corrupt = _read_tolerant(lock_path(project_path))
    info = acquire(project_path, identity=identity, steal=True, now=now)
    return info, prior


def release(project_path: str | Path, info: LockInfo) -> bool:
    """Release a lock we hold; a no-op (returns ``False``) if we no longer own it.

    Only removes the sidecar when the on-disk ``nonce`` still matches ``info`` — so
    a holder never deletes a lock that was stolen from them in the meantime, and a
    double release is harmless.

    A narrow read-then-``unlink`` window remains (a steal landing between the nonce
    check and the ``unlink`` would delete the successor's lock). Closing it fully
    needs OS advisory locking, which is deliberately out of scope: PRD §5.4 adopts a
    one-owner-at-a-time, wall-clock-staleness model precisely because atomic locking
    is impossible on an eventually-consistent share, so a sub-millisecond same-host
    race is not the threat this guard defends against.
    """
    lp = lock_path(project_path)
    current, corrupt = _read_tolerant(lp)
    if corrupt or current is None or current.nonce != info.nonce:
        return False
    try:
        lp.unlink()
    except FileNotFoundError:  # pragma: no cover - raced away between read and unlink
        return False
    return True


@contextmanager
def held_lock(
    project_path: str | Path,
    *,
    identity: LockIdentity | None = None,
    timeout_s: float = DEFAULT_STALENESS_TIMEOUT_S,
    steal: bool = False,
    now: datetime | None = None,
) -> Iterator[LockInfo]:
    """Context manager that acquires the lock on entry and releases it on exit.

    Raises :class:`LockedError` on entry if a foreign lock blocks acquisition
    (unless ``steal=True``). Release is nonce-checked, so a lock stolen mid-session
    is not clobbered on exit.
    """
    info = acquire(project_path, identity=identity, timeout_s=timeout_s, steal=steal, now=now)
    try:
        yield info
    finally:
        release(project_path, info)


# --- OneDrive / SharePoint sync-conflict detection (PRD §5.4 detect-and-surface)


def conflict_copies(project_path: str | Path) -> list[Path]:
    """Sibling files that look like sync-conflict duplicates of ``project_path``.

    OneDrive/SharePoint **detect-and-surface** policy (PRD §5.4): rather than
    prevent conflict copies, Tether finds and reports them so the user reconciles.
    Two documented naming conventions are matched in the project's directory:

    * ``<stem>-<COMPUTERNAME><suffix>`` — the modern OneDrive form, where a second
      device that saved a conflicting change appends ``-COMPUTERNAME`` (e.g.
      ``exp.tether`` -> ``exp-DESKTOP-AB12.tether``).
    * ``<stem> (<...>)<suffix>`` — the parenthetical form used for
      ``(conflicted copy ...)`` and numbered ``(1)`` duplicates.

    This is a **heuristic surface**, not a guarantee: a legitimately-named sibling
    (``exp-donoronly.tether``) can match, so the caller/GUI presents the results as
    *candidates* to review. The canonical file and its ``.lock`` sidecar are never
    reported. Returns a sorted, de-duplicated list of existing paths.
    """
    p = Path(project_path)
    parent = p.parent
    stem, suffix = _glob.escape(p.stem), _glob.escape(p.suffix)
    patterns = (f"{stem}-*{suffix}", f"{stem} (*){suffix}")
    matches: set[Path] = set()
    for pattern in patterns:
        for candidate in parent.glob(pattern):
            # Defensive invariant: never report the project itself or its own lock.
            # The separator-bearing patterns above ("-*" / " (*)") structurally
            # cannot match "<stem><suffix>" or the ".lock" sidecar today, so this is
            # a belt-and-braces guard that keeps holding if a future, separatorless
            # pattern is ever added.
            if candidate.name == p.name or candidate.name == lock_path(p).name:
                continue
            if candidate.is_file():
                matches.add(candidate)
    return sorted(matches)


# --- split-file curation (PRD §7.10 producer side; M5 owner-pull consumer) ----


def create_split_curation(
    canonical_path: str | Path,
    split_path: str | Path,
    molecule_keys: Iterable[str] | None = None,
    *,
    overwrite: bool = False,
    identity: LockIdentity | None = None,
) -> Project:
    """Open a locked-out non-owner's own split ``.tether`` for curation (PRD §7.10).

    Curation must never be blocked while a file is locked (§7.10), so a non-owner
    **browses the canonical file read-only** (this reads its ``/molecules`` without
    ever writing it) and gets back a fresh, fully-owned ``.tether`` holding just the
    curated subset's molecule rows — enough for ``molecule_key`` to resolve — into
    which they log their own provenance-tagged ``/labels`` (:func:`Project.accept`
    / :func:`Project.reject`). That split is the **producer** side of the M5
    owner-pull merge, which later joins every split's ``/labels`` on the stable
    ``molecule_key`` (§5.1/§7.10); the merge itself is M5, not here.

    Parameters
    ----------
    canonical_path:
        The locked canonical project to browse read-only.
    split_path:
        Destination for the new split ``.tether`` (this non-owner's own file).
    molecule_keys:
        Restrict the copied ``/molecules`` rows to these keys; ``None`` copies all.
    overwrite:
        Passed to :func:`~tether.io.schema.create_project` for ``split_path``; an
        overwrite is refused (``LockedError``) if the split is locked by another
        writer.
    identity:
        The split's writer identity (defaults to the local process); the returned
        handle owns the split, so its writes are never lock-guarded against the
        canonical.

    Returns
    -------
    tether.project.core.Project
        A handle on the split, ready to curate.
    """
    import h5py  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    from tether.io.schema import TABLE, create_project  # noqa: PLC0415
    from tether.project.core import Project  # noqa: PLC0415

    canonical_path = Path(canonical_path)
    split_path = Path(split_path)

    # Respect the destination split's own single-writer lock before a destructive
    # overwrite: never clobber a split another writer holds (§5.4). A fresh path has
    # no lock, so this is a no-op for the common case.
    if overwrite:
        assert_writable(split_path, identity=identity)

    # Browse the canonical /molecules read-only (never opened for write).
    with h5py.File(canonical_path, "r") as f:
        rows = f["molecules"][TABLE][:]
    if molecule_keys is not None:
        wanted = {str(k) for k in molecule_keys}
        keep = np.array([_decode(k) in wanted for k in rows["molecule_key"]], dtype=bool)
        rows = rows[keep]

    create_project(split_path, overwrite=overwrite)
    with h5py.File(split_path, "r+") as f:
        table = f["molecules"][TABLE]
        table.resize((rows.shape[0],))
        if rows.shape[0]:
            table[:] = rows
    return Project(split_path, identity=identity)
