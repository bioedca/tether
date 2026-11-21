# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The job and dependency coordinator: stored jobs, checkpoints, cancel, retry, stale marks.

A long step of the workflow (an extraction of many movies, a set of model fits, a deposit)
runs as a **job** (#895, ADR-0126). The job is stored in the project, so the command line
and a restarted application see the same run as the window that started it:

* A **job record** is one row of ``/settings/jobs``: its id, the service request that it
  runs (the text form of ADR-0100), its [`JobState`][], its checkpoint (the parts that are
  complete), the identities of its inputs, its owner, its times and its error text.
* [`run_job`][] does the parts that are not yet complete, one at a time, and stores each
  completed part before it starts the next. **Cancel** stops between parts or inside a part
  that raises [`JobCancelled`][]; the store stays valid and the completed parts stay.
  [`retry_job`][] continues a failed, cancelled or interrupted job from its checkpoint.
* A job in state *running* whose owner is gone (the writer session that ran it ended, or its
  process died) is **interrupted**. [`read_jobs`][] reports it so on every read, and the
  next writer stores it so ([`reconcile_jobs`][]).

The stale marks of ADR-0103 are here too. [`record_inputs`][] stores the identity of each
upstream instance that a record is made from; [`set_stale_marks`][] is the one function
that sets marks after an edit, and [`read_stale_marks`][] the one that reads them. The owner
of each downstream record judges the guard of a guarded edge for that record, so two records
made from one upstream instance can differ on it (#1170). A mark is a cache: the comparison
of recorded and current identities is the authority (ADR-0103, decision 2).

Every write needs the single-writer lock (PRD §7.10). A job binds to the lock epoch that it
started under and checks it again before each write, so a steal stops it. The file is open
only for each short write, never during the work of a part, so a reader never waits on a job.
The window shows a job through a Qt adapter (#343); this module imports no Qt module.
"""

from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import uuid4

from tether.project import dependency_graph as graph

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
    from pathlib import Path

    from tether.project.core import Project
    from tether.services.base import Request

__all__ = [
    "CancelToken",
    "JobCancelled",
    "JobError",
    "JobRecord",
    "JobState",
    "RecordedInput",
    "StaleMark",
    "read_job",
    "read_jobs",
    "read_recorded_inputs",
    "read_stale_marks",
    "reconcile_jobs",
    "record_inputs",
    "retry_job",
    "run_job",
    "set_stale_marks",
    "submit_job",
]

_SETTINGS = "settings"
_JOBS = "jobs"
_INPUTS = "recorded_inputs"
_MARKS = "stale_marks"


class JobState(StrEnum):
    """The state of a job record."""

    #: Stored and not started.
    QUEUED = "queued"
    #: A writer session is doing its parts now.
    RUNNING = "running"
    #: Every part is complete.
    DONE = "done"
    #: A part raised an error; the error text says which and why.
    FAILED = "failed"
    #: Cancelled by the user; the parts that were complete are kept.
    CANCELLED = "cancelled"
    #: The job was running when its owner stopped (a crash, a closed session).
    INTERRUPTED = "interrupted"


#: The states from which [`retry_job`][] continues a job.
RETRYABLE = frozenset({JobState.FAILED, JobState.CANCELLED, JobState.INTERRUPTED})


class JobError(RuntimeError):
    """A job operation is not allowed in the job's present state."""


class JobCancelled(Exception):  # noqa: N818 - a signal, not an error
    """Raised by the work of a part to stop the job as cancelled.

    The part is not recorded as complete, so the work must leave the store as it was before
    the part started.
    """


class CancelToken:
    """A thread-safe flag that asks a running job to stop.

    The job checks it before each part; a long part can call [`raise_if_cancelled`][] too.
    """

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        """Ask the job to stop."""
        self._event.set()

    @property
    def cancelled(self) -> bool:
        """Whether a cancel was asked for."""
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        """Raise [`JobCancelled`][] when a cancel was asked for."""
        if self._event.is_set():
            raise JobCancelled


@dataclass(frozen=True)
class JobRecord:
    """One stored job, as [`read_jobs`][] returns it.

    Attributes
    ----------
    job_id
        A random id (32 hex characters), never reused.
    command
        The service command of the request (``"extract"``, ``"batch"``).
    request
        The text form of the service request (ADR-0100).
    state
        The state as a reader sees it: a stored *running* job with no live owner reads as
        [`JobState.INTERRUPTED`][].
    stored_state
        The state that is written in the store.
    parts
        Every part of the job, in the order the job does them.
    completed
        The checkpoint: the parts that are complete, in the order they completed.
    inputs
        The identity of each input, keyed ``"<node key>:<instance id>"`` (ADR-0103).
    owner
        The writer that last ran the job: ``host``, ``user``, ``pid`` and the lock ``nonce``.
    created_utc, started_utc, ended_utc
        ISO-8601 UTC times; ``""`` when not yet reached.
    error
        The error text of a failed job, else ``""``.
    """

    job_id: str
    command: str
    request: str
    state: JobState
    stored_state: JobState
    parts: tuple[str, ...]
    completed: tuple[str, ...]
    inputs: Mapping[str, str]
    owner: Mapping[str, object]
    created_utc: str
    started_utc: str
    ended_utc: str
    error: str

    @property
    def remaining(self) -> tuple[str, ...]:
        """The parts that are not complete, in job order."""
        done = set(self.completed)
        return tuple(p for p in self.parts if p not in done)


@dataclass(frozen=True)
class RecordedInput:
    """The identity of one upstream instance that a record was made from (ADR-0103)."""

    node: str
    instance: str
    upstream_node: str
    upstream_instance: str
    identity: str


@dataclass(frozen=True)
class StaleMark:
    """One stale mark: the record ``(node, instance)`` is stale, and the reason.

    ``edge`` is the number of the edge of ADR-0103 that the mark crossed, and
    ``(upstream_node, upstream_instance)`` the record it came from. ``edited_node`` and
    ``edited_instance`` name the edit that started it.
    """

    node: str
    instance: str
    edge: int
    upstream_node: str
    upstream_instance: str
    edited_node: str
    edited_instance: str
    event: str
    timestamp: str


# --- the store tables -------------------------------------------------------------------------

#: The process-local set of job ids that a [`run_job`][] in this process is doing now.
_ACTIVE: set[str] = set()
_ACTIVE_LOCK = threading.Lock()


def _str_dtype():  # noqa: ANN202 - numpy dtype
    import h5py  # noqa: PLC0415

    return h5py.string_dtype(encoding="utf-8")


def _dtype(names: Sequence[str]):  # noqa: ANN202 - numpy dtype
    import numpy as np  # noqa: PLC0415

    return np.dtype([(n, _str_dtype()) for n in names])


_JOB_FIELDS = (
    "job_id",
    "command",
    "request",
    "state",
    "parts",
    "completed",
    "inputs",
    "owner",
    "created_utc",
    "started_utc",
    "ended_utc",
    "error",
    "app_version",
)
_INPUT_FIELDS = ("node", "instance", "upstream_node", "upstream_instance", "identity")
_MARK_FIELDS = (
    "node",
    "instance",
    "edge",
    "upstream_node",
    "upstream_instance",
    "edited_node",
    "edited_instance",
    "event",
    "timestamp",
    "app_version",
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _app_version() -> str:
    from tether.io.schema import _app_version as version  # noqa: PLC0415

    return version()


def _text(value: object) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _append(
    path: Path, name: str, fields: Sequence[str], rows: Sequence[Mapping[str, str]]
) -> None:
    """Append ``rows`` to ``/settings/<name>``, creating it on first use (additive data).

    Every row is built before the dataset grows, and a failed write shrinks it back, so a
    failed append leaves no empty row.
    """
    import h5py  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    if not rows:
        return
    dtype = _dtype(fields)
    block = np.zeros(len(rows), dtype=dtype)
    for i, row in enumerate(rows):
        for f in fields:
            block[f][i] = row[f]
    with h5py.File(path, "r+") as f:
        settings = f[_SETTINGS]
        if name not in settings:
            settings.create_dataset(name, shape=(0,), maxshape=(None,), dtype=dtype)
        ds = settings[name]
        n0 = ds.shape[0]
        ds.resize((n0 + len(rows),))
        try:
            ds[n0:] = block
        except BaseException:
            ds.resize((n0,))
            raise


def _read_rows(path: str | Path, name: str) -> list[dict[str, str]]:
    import h5py  # noqa: PLC0415

    with h5py.File(path, "r") as f:
        settings = f.get(_SETTINGS)
        if settings is None or name not in settings:
            return []
        data = settings[name][()]
    names = data.dtype.names or ()
    return [{n: _text(row[n]) for n in names} for row in data]


_OPEN_ATTEMPTS = 20
_OPEN_DELAY_S = 0.025


def _open_for_job_write(path: Path):  # noqa: ANN202 - h5py.File
    """Open ``path`` for a job write, waiting out a reader that has the file open.

    HDF5 refuses a writable open while a reader holds the file. A reader holds it only for
    its own short read, so the open is tried again, at most ``_OPEN_ATTEMPTS`` times over
    about five seconds, before the error is raised.
    """
    import time  # noqa: PLC0415

    import h5py  # noqa: PLC0415

    for attempt in range(_OPEN_ATTEMPTS):
        try:
            return h5py.File(path, "r+")
        except OSError:
            if attempt == _OPEN_ATTEMPTS - 1:
                raise
            time.sleep(_OPEN_DELAY_S * (attempt + 1))
    raise AssertionError("unreachable")  # pragma: no cover


def _write_job(
    path: Path, job_id: str, check: Callable[[], object] | None = None, **fields: str
) -> None:
    """Change fields of the stored row of ``job_id`` in place.

    ``check`` runs once the file is open and before the row changes, so a lock check made
    there still holds when the open had to wait for a reader.
    """
    with _open_for_job_write(path) as f:
        if check is not None:
            check()
        ds = f[_SETTINGS][_JOBS]
        ids = [_text(v) for v in ds["job_id"]]
        try:
            i = ids.index(job_id)
        except ValueError:
            raise KeyError(f"no job {job_id!r} in {path.name}") from None
        row = ds[i]
        for key, value in fields.items():
            row[key] = value
        ds[i] = row


# --- jobs -------------------------------------------------------------------------------------


def _owner_live(path: str | Path, job_id: str, owner: Mapping[str, object]) -> bool:
    """Whether the writer that runs ``job_id`` is still there.

    The owner is the lock epoch the job started under. It is gone when the lock no longer
    carries that nonce, when it is this process and no [`run_job`][] here is doing the job,
    or when it is another process of this computer that is no longer running. A lock that
    cannot be read counts as live, so a reader never reports a job interrupted by mistake.
    """
    from tether.project import lock  # noqa: PLC0415

    try:
        current = lock.read_lock(path)
    except lock.CorruptLockError:
        return True
    if current is None or current.nonce != owner.get("nonce"):
        return False
    me = lock.local_identity()
    if current.host != me.host:
        return True
    if current.pid == os.getpid():
        with _ACTIVE_LOCK:
            return job_id in _ACTIVE
    return lock._pid_alive(current.pid)


def _record(row: Mapping[str, str], path: str | Path) -> JobRecord:
    stored = JobState(row["state"])
    owner = json.loads(row["owner"]) if row["owner"] else {}
    state = stored
    if stored is JobState.RUNNING and not _owner_live(path, row["job_id"], owner):
        state = JobState.INTERRUPTED
    return JobRecord(
        job_id=row["job_id"],
        command=row["command"],
        request=row["request"],
        state=state,
        stored_state=stored,
        parts=tuple(json.loads(row["parts"])),
        completed=tuple(json.loads(row["completed"])),
        inputs=json.loads(row["inputs"]),
        owner=owner,
        created_utc=row["created_utc"],
        started_utc=row["started_utc"],
        ended_utc=row["ended_utc"],
        error=row["error"],
    )


def read_jobs(path: str | Path) -> list[JobRecord]:
    """Return every job of the project at ``path``, in the order they were submitted.

    This is a read: it needs no lock, never waits on a running job and writes nothing. A
    stored *running* job whose owner is gone is returned as *interrupted*
    ([`JobRecord.stored_state`][] keeps what is written). A read that meets one of the short
    writes of a job raises the ``OSError`` of HDF5; a caller that polls tries again.
    """
    return [_record(row, path) for row in _read_rows(path, _JOBS)]


def read_job(path: str | Path, job_id: str) -> JobRecord:
    """Return the job ``job_id``; raise ``KeyError`` when the project has no such job."""
    for job in read_jobs(path):
        if job.job_id == job_id:
            return job
    raise KeyError(f"no job {job_id!r} in the project")


def reconcile_jobs(project: Project) -> list[str]:
    """Store *interrupted* for each running job with no live owner; return their ids.

    Needs the writer lock. [`submit_job`][], [`run_job`][] and [`retry_job`][] call it first,
    so the first writer after a crash stores what every reader already reports. A lock must
    be present and this writer's (a handle that retains one, its own): an absent one refuses
    with ``LockedError``. Each write binds to the lock epoch this call started under, so a
    steal stops it even when the thief has released the lock since.
    """
    from tether.project import lock  # noqa: PLC0415

    def held_nonce() -> str:
        project._assert_writable()
        if project._held_lock is not None:  # a retained lock binds to its own nonce
            return project._assert_held_lock()
        current = lock.read_lock(project.path)
        if current is None:
            raise lock.LockedError(None, path=project.lock_path)
        return current.nonce

    epoch = held_nonce()

    def check() -> None:
        if held_nonce() != epoch:
            raise lock.LockedError(lock.read_lock(project.path), path=project.lock_path)

    seen = [
        j
        for j in read_jobs(project.path)
        if j.stored_state is JobState.RUNNING and j.state is JobState.INTERRUPTED
    ]
    gone: list[str] = []
    for job in seen:
        # A run may have started since the read; the claim keeps one from starting now, and
        # a fresh row with the same start is the dead run that was read.
        try:
            with _claimed(job.job_id):
                now = read_job(project.path, job.job_id)
                if (now.stored_state, now.started_utc, now.owner) != (
                    JobState.RUNNING,
                    job.started_utc,
                    job.owner,
                ):
                    continue
                _write_job(
                    project.path,
                    job.job_id,
                    check,
                    state=JobState.INTERRUPTED.value,
                    ended_utc=_now(),
                )
        except JobError:
            continue
        gone.append(job.job_id)
    return gone


def _input_key(node_key: str, instance: str) -> str:
    graph.node(node_key)
    if not instance:
        raise ValueError(f"an input of node {node_key!r} needs an instance id")
    return f"{node_key}:{instance}"


def submit_job(
    project: Project,
    request: Request,
    parts: Sequence[str],
    *,
    inputs: Mapping[tuple[str, str], str] | None = None,
) -> JobRecord:
    """Store a new *queued* job that runs ``request`` in ``parts``; return its record.

    ``parts`` names each unit of work that can complete on its own (a movie, a model), with
    no repeat. ``inputs`` maps ``(node key, instance id)`` of the dependency graph
    (ADR-0103) to the identity of that input when the job was made.
    """
    from tether.services.base import request_to_text  # noqa: PLC0415

    project._assert_writable()
    reconcile_jobs(project)
    parts = tuple(str(p) for p in parts)
    if not parts or len(set(parts)) != len(parts) or any(not p for p in parts):
        raise ValueError("a job needs at least one part, each named once and not empty")
    recorded = {_input_key(n, i): str(v) for (n, i), v in (inputs or {}).items()}
    job_id = uuid4().hex
    row = {
        "job_id": job_id,
        "command": request.COMMAND,
        "request": request_to_text(request),
        "state": JobState.QUEUED.value,
        "parts": json.dumps(list(parts)),
        "completed": "[]",
        "inputs": json.dumps(recorded, sort_keys=True),
        "owner": "",
        "created_utc": _now(),
        "started_utc": "",
        "ended_utc": "",
        "error": "",
        "app_version": _app_version(),
    }
    _append(project.path, _JOBS, _JOB_FIELDS, [row])
    return read_job(project.path, job_id)


def run_job(
    project: Project,
    job_id: str,
    work: Callable[[str, CancelToken], None],
    *,
    cancel: CancelToken | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> JobRecord:
    """Do the parts of a queued job that are not complete; return the final record.

    ``work(part, cancel)`` does one part and writes what it makes; the coordinator then
    stores the part in the checkpoint before it starts the next one. The project file is
    closed while ``work`` runs. ``progress(done, total)`` is called after each part.

    Needs the writer lock retained by ``project`` (``project.acquire_lock()``). The context
    manager ``project.write_lock()`` does not retain the lock on the handle, so a job refuses
    to start under it. A steal during the job stops it before its next write.
    The job ends *done*, *cancelled* (the token was set, or ``work`` raised
    [`JobCancelled`][]) or *failed* (``work`` raised; the error text is stored). An
    interrupt such as ``KeyboardInterrupt`` stores *interrupted* and is raised again.
    """
    nonce = project._assert_held_lock()
    reconcile_jobs(project)
    with _claimed(job_id):
        job = read_job(project.path, job_id)
        if job.stored_state is not JobState.QUEUED:
            raise JobError(f"job {job_id} is {job.state.value}; only a queued job starts")
        return _drive(project, job, nonce, work, cancel or CancelToken(), progress)


def retry_job(
    project: Project,
    job_id: str,
    work: Callable[[str, CancelToken], None],
    *,
    cancel: CancelToken | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> JobRecord:
    """Continue a failed, cancelled or interrupted job from its checkpoint.

    The complete parts are kept and not done again; the other parts run as in
    [`run_job`][], and the error text of the last attempt is cleared.
    """
    nonce = project._assert_held_lock()
    reconcile_jobs(project)
    with _claimed(job_id):
        job = read_job(project.path, job_id)
        if job.stored_state not in RETRYABLE:
            raise JobError(
                f"job {job_id} is {job.state.value}; only a failed, cancelled or interrupted "
                "job can be retried"
            )
        return _drive(project, job, nonce, work, cancel or CancelToken(), progress)


@contextmanager
def _claimed(job_id: str) -> Iterator[None]:
    """Hold ``job_id`` for one run in this process; refuse it when a run already holds it.

    The claim is taken before the stored state is read, so two starts of one job never both
    pass the state check. It is released on every way out.
    """
    with _ACTIVE_LOCK:
        if job_id in _ACTIVE:
            raise JobError(f"job {job_id} is already running in this process")
        _ACTIVE.add(job_id)
    try:
        yield
    finally:
        with _ACTIVE_LOCK:
            _ACTIVE.discard(job_id)


def _drive(
    project: Project,
    job: JobRecord,
    nonce: str,
    work: Callable[[str, CancelToken], None],
    cancel: CancelToken,
    progress: Callable[[int, int], None] | None,
) -> JobRecord:
    path = project.path
    held = project._held_lock
    owner = {"host": held.host, "user": held.user, "pid": held.pid, "nonce": nonce}

    def check() -> None:
        project._assert_held_lock(expected_nonce=nonce)

    def write(**fields: str) -> None:
        check()
        _write_job(path, job.job_id, check, **fields)

    completed = list(job.completed)
    try:
        write(
            state=JobState.RUNNING.value,
            owner=json.dumps(owner, sort_keys=True),
            started_utc=_now(),
            ended_utc="",
            error="",
        )
        for part in job.remaining:
            if cancel.cancelled:
                raise JobCancelled
            work(part, cancel)
            completed.append(part)
            write(completed=json.dumps(completed))
            if progress is not None:
                progress(len(completed), len(job.parts))
    except JobCancelled:
        write(state=JobState.CANCELLED.value, ended_utc=_now())
    except Exception as exc:
        write(state=JobState.FAILED.value, ended_utc=_now(), error=f"{type(exc).__name__}: {exc}")
    except BaseException:
        with suppress(Exception):
            write(state=JobState.INTERRUPTED.value, ended_utc=_now())
        raise
    else:
        write(state=JobState.DONE.value, ended_utc=_now())
    return read_job(path, job.job_id)


# --- recorded inputs and stale marks (ADR-0103) -----------------------------------------------


def read_recorded_inputs(path: str | Path) -> list[RecordedInput]:
    """Return every recorded input identity of the project at ``path`` (a read)."""
    return [RecordedInput(**{k: r[k] for k in _INPUT_FIELDS}) for r in _read_rows(path, _INPUTS)]


def record_inputs(
    project: Project,
    node_key: str,
    instance: str,
    inputs: Mapping[tuple[str, str], str],
) -> None:
    """Store the identity of each direct upstream instance that a new record is made from.

    The owner of a record calls this once, when it makes the record ``(node_key,
    instance)``. ``inputs`` maps ``(upstream node key, upstream instance id)`` to the
    identity of that instance now. Each upstream node must have an edge of ADR-0103 into
    ``node_key``. A record's inputs are recorded once: a changed record is a new instance.
    """
    project._assert_writable()
    _input_key(node_key, instance)
    if any(
        r.node == node_key and r.instance == instance for r in read_recorded_inputs(project.path)
    ):
        raise ValueError(f"the inputs of {node_key}:{instance} are already recorded")
    upstream = {e.upstream for e in graph.EDGES if e.downstream == node_key}
    rows = []
    for (up_node, up_instance), identity in inputs.items():
        _input_key(up_node, up_instance)
        if up_node not in upstream:
            raise ValueError(f"the dependency graph has no edge from {up_node!r} to {node_key!r}")
        rows.append(
            {
                "node": node_key,
                "instance": instance,
                "upstream_node": up_node,
                "upstream_instance": up_instance,
                "identity": str(identity),
            }
        )
    _append(project.path, _INPUTS, _INPUT_FIELDS, rows)


def read_stale_marks(path: str | Path) -> list[StaleMark]:
    """Return every stale mark of the project at ``path`` (a read).

    A record with a mark is stale: it stays readable and never supplies a current result
    (ADR-0103, decision 3). Marks are never removed.
    """
    marks = []
    for r in _read_rows(path, _MARKS):
        fields = {k: r[k] for k in _MARK_FIELDS if k != "app_version"}
        fields["edge"] = int(fields["edge"])
        marks.append(StaleMark(**fields))
    return marks


def set_stale_marks(
    project: Project,
    node_key: str,
    instance: str,
    identity: str,
    *,
    guards_hold: Iterable[int] | Callable[[int, str, str], bool] = (),
) -> list[StaleMark]:
    """Mark stale every record that an edit of ``(node_key, instance)`` makes stale.

    ``identity`` is the identity of the edited instance after the edit. A record that
    recorded the instance with another identity is marked through the edge of ADR-0103
    between them, and the mark then reaches each record made from a marked record, as
    [`marked_by`][tether.project.dependency_graph.marked_by] says: never into a node of
    ``CONTENT_COMPARED`` except by an edge from the edited node. Only the instances that
    recorded a marked instance are reached, never every instance of a node.

    A guarded edge is crossed only when the owner of the downstream record says that its
    guard holds **for that record**. ``guards_hold`` says it in one of two forms:

    * a callable ``(edge number, downstream node key, downstream instance id) -> bool``,
      asked once for each record that a guarded edge would mark, so two records made from
      one upstream instance can be judged apart (two factor versions of one trace
      generation, of which changed intensities touch a contributor of only one: edge 7);
    * the edge numbers whose guard holds for every record they reach (the form before
      #1170, which still works).

    A record already marked is not marked again, and its guard is not asked. The writer
    check is made again before the marks are stored, and a lock taken or released while
    the guards were asked refuses the write with ``LockedError``. Returns the new marks.
    """
    from tether.project import lock  # noqa: PLC0415

    project._assert_writable()
    _input_key(node_key, instance)

    def lock_nonce() -> str | None:
        current = lock.read_lock(project.path)
        return None if current is None else current.nonce

    epoch = lock_nonce()
    if callable(guards_hold):
        judge = guards_hold

        def holds(edge: int, node: str, inst: str) -> bool:
            return bool(judge(edge, node, inst))

    else:
        numbers = frozenset(int(g) for g in guards_hold)

        def holds(edge: int, node: str, inst: str) -> bool:  # noqa: ARG001 - one answer
            return edge in numbers

    recorded = read_recorded_inputs(project.path)
    already = {(m.node, m.instance) for m in read_stale_marks(project.path)}
    made_from: dict[tuple[str, str], list[RecordedInput]] = {}
    for r in recorded:
        made_from.setdefault((r.upstream_node, r.upstream_instance), []).append(r)
    stamp = _now()
    new: list[StaleMark] = []
    queue = [(node_key, instance)]
    while queue:
        up_node, up_instance = queue.pop(0)
        edited = (up_node, up_instance) == (node_key, instance)
        for r in made_from.get((up_node, up_instance), []):
            edge = next(e for e in graph.EDGES if e.upstream == up_node and e.downstream == r.node)
            if edited and r.identity == identity:
                continue
            if not edited and r.node in graph.CONTENT_COMPARED:
                continue
            if (r.node, r.instance) in already:
                continue
            if edge.guarded and not holds(edge.number, r.node, r.instance):
                continue
            already.add((r.node, r.instance))
            new.append(
                StaleMark(
                    node=r.node,
                    instance=r.instance,
                    edge=edge.number,
                    upstream_node=up_node,
                    upstream_instance=up_instance,
                    edited_node=node_key,
                    edited_instance=instance,
                    event=edge.event,
                    timestamp=stamp,
                )
            )
            queue.append((r.node, r.instance))
    rows = [
        {
            **{k: str(getattr(m, k)) for k in _MARK_FIELDS if k != "app_version"},
            "app_version": _app_version(),
        }
        for m in new
    ]
    if rows:  # the guards are the owners' code: the lock may have moved while they ran
        project._assert_writable()
        if lock_nonce() != epoch:
            raise lock.LockedError(lock.read_lock(project.path), path=project.lock_path)
    _append(project.path, _MARKS, _MARK_FIELDS, rows)
    return new
