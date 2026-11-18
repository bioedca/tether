# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The durable record of each lock takeover inside the project (#1099, ADR-0121).

Every successful Recover or Take over leaves one row in ``/settings/lock_takeovers``,
written under the new lock through
[`Project.record_lock_takeover`][tether.project.core.Project.record_lock_takeover]. A
refused takeover, and a record attempted by a writer that does not hold the lock, leave
none. A project written before #1099 has no record and reads as no takeovers.
Headless: real ``.tether`` files, no Qt.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("h5py")

import h5py  # noqa: E402

from tether.io.schema import create_project  # noqa: E402
from tether.project import lock, lock_takeovers  # noqa: E402
from tether.project.core import Project  # noqa: E402
from tether.project.lock import (  # noqa: E402
    DEFAULT_STALENESS_TIMEOUT_S,
    LockedError,
    LockIdentity,
    RecoveryAction,
)

_TIMEOUT = timedelta(seconds=DEFAULT_STALENESS_TIMEOUT_S)


def _dead_pid() -> int:
    """The PID of a child that has exited (and been reaped)."""
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


def _old(seconds_past_stale: float = 60.0) -> datetime:
    return datetime.now(UTC) - _TIMEOUT - timedelta(seconds=seconds_past_stale)


def _str(value: object) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


@pytest.fixture
def me() -> LockIdentity:
    return lock.local_identity()


@pytest.fixture
def project(tmp_path, me) -> Project:
    path = create_project(tmp_path / "exp.tether")
    return Project(path, identity=me)


def _take(project: Project, me: LockIdentity):
    """Judge, take and record, as the application does after a writable reopen."""
    verdict = lock.lock_recovery(project.path, identity=me)
    assert verdict is not None
    taken, ousted = lock.recover_lock(project.path, verdict, identity=me)
    project.record_lock_takeover(verdict.action, taken, ousted)
    return verdict, taken, ousted


def test_recover_appends_one_record(project, me) -> None:
    crashed = lock.acquire(
        project.path, identity=LockIdentity(host=me.host, user=me.user, pid=_dead_pid())
    )

    verdict, taken, _ousted = _take(project, me)

    assert verdict.action is RecoveryAction.RECOVER
    rows = project.read_lock_takeovers()
    assert rows.shape == (1,)
    row = rows[0]
    assert _str(row["action"]) == "recover"
    assert _str(row["timestamp"]) == taken.timestamp
    assert (_str(row["holder_user"]), _str(row["holder_host"])) == (me.user, me.host)
    assert int(row["holder_pid"]) == me.pid
    assert int(row["ousted_corrupt"]) == 0
    assert (_str(row["ousted_user"]), _str(row["ousted_host"])) == (crashed.user, crashed.host)
    assert int(row["ousted_pid"]) == crashed.pid
    assert _str(row["ousted_timestamp"]) == crashed.timestamp
    assert _str(row["app_version"])
    # The timestamp is an offset-aware UTC instant.
    assert datetime.fromisoformat(_str(row["timestamp"])).utcoffset() == timedelta(0)


def test_take_over_appends_one_record_naming_the_foreign_holder(project, me) -> None:
    foreign = LockIdentity(host=f"{me.host}-OTHER", user="alice", pid=4242)
    held = lock.acquire(project.path, identity=foreign, now=_old())

    verdict, _taken, ousted = _take(project, me)

    assert verdict.action is RecoveryAction.TAKE_OVER
    assert ousted == held
    rows = lock_takeovers.read_takeovers(project.path)
    assert rows.shape == (1,)
    assert _str(rows[0]["action"]) == "take_over"
    assert _str(rows[0]["ousted_user"]) == "alice"
    assert _str(rows[0]["ousted_host"]) == f"{me.host}-OTHER"
    assert int(rows[0]["ousted_pid"]) == 4242
    assert _str(rows[0]["ousted_timestamp"]) == held.timestamp


def test_take_over_of_a_corrupt_lock_records_an_unknown_holder(project, me) -> None:
    lp = lock.lock_path(project.path)
    lp.write_text("{not json", encoding="utf-8")
    old = _old().timestamp()
    os.utime(lp, (old, old))

    verdict, _taken, ousted = _take(project, me)

    assert verdict.action is RecoveryAction.TAKE_OVER
    assert ousted is None
    row = project.read_lock_takeovers()[0]
    assert _str(row["action"]) == "take_over"
    assert int(row["ousted_corrupt"]) == 1
    assert (_str(row["ousted_user"]), _str(row["ousted_host"])) == ("", "")
    assert int(row["ousted_pid"]) == -1
    assert _str(row["ousted_timestamp"]) == ""


def test_records_accumulate_in_takeover_order(project, me) -> None:
    lock.acquire(project.path, identity=LockIdentity(host=me.host, user=me.user, pid=_dead_pid()))
    _take(project, me)
    # Our lock goes stale on another computer's clock and is taken from us; then we
    # take it back once that holder is idle past the timeout.
    lock.release(project.path, lock.read_lock(project.path))
    lock.acquire(
        project.path, identity=LockIdentity(host=f"{me.host}-OTHER", user="bob", pid=7), now=_old()
    )
    _take(project, me)

    actions = [_str(a) for a in project.read_lock_takeovers()["action"]]
    assert actions == ["recover", "take_over"]


def test_a_refused_takeover_appends_no_record(project, me) -> None:
    foreign = LockIdentity(host=f"{me.host}-OTHER", user="alice", pid=4242)
    held = lock.acquire(project.path, identity=foreign, now=_old())
    verdict = lock.lock_recovery(project.path, identity=me)
    assert verdict is not None and verdict.action is RecoveryAction.TAKE_OVER
    lock.refresh(project.path, held)  # the holder came back before the click

    with pytest.raises(LockedError):
        lock.recover_lock(project.path, verdict, identity=me)

    assert project.read_lock_takeovers().shape == (0,)
    with h5py.File(project.path, "r") as f:
        assert "lock_takeovers" not in f["settings"]


def test_a_writer_that_does_not_hold_the_lock_cannot_record(project, me) -> None:
    foreign = LockIdentity(host=f"{me.host}-OTHER", user="alice", pid=4242)
    held = lock.acquire(project.path, identity=foreign)

    with pytest.raises(LockedError):
        project.record_lock_takeover(RecoveryAction.TAKE_OVER, held, None)

    assert project.read_lock_takeovers().shape == (0,)


def test_only_a_takeover_action_is_recorded(project, me) -> None:
    taken = lock.acquire(project.path, identity=me)

    with pytest.raises(ValueError, match="not a takeover"):
        project.record_lock_takeover(RecoveryAction.NONE, taken, None)

    with h5py.File(project.path, "r") as f:
        assert "lock_takeovers" not in f["settings"]
    # The string form is accepted too.
    project.record_lock_takeover("recover", taken, None)
    assert project.read_lock_takeovers().shape == (1,)


def test_a_failed_append_leaves_no_blank_row(project, me, monkeypatch) -> None:
    taken = lock.acquire(project.path, identity=me)
    project.record_lock_takeover(RecoveryAction.RECOVER, taken, None)

    def failing_write(self, key, value):
        raise OSError("share went away mid-write")

    monkeypatch.setattr(h5py.Dataset, "__setitem__", failing_write)
    with pytest.raises(OSError, match="share went away"):
        project.record_lock_takeover(RecoveryAction.RECOVER, taken, None)
    monkeypatch.undo()

    rows = project.read_lock_takeovers()
    assert rows.shape == (1,)
    assert _str(rows[0]["action"]) == "recover"


def test_a_project_written_before_the_record_opens_and_reports_none(tmp_path) -> None:
    # A store made the way every release before #1099 made it: no lock_takeovers.
    path = create_project(tmp_path / "old.tether")
    with h5py.File(path, "r") as f:
        assert "lock_takeovers" not in f["settings"]

    opened = Project.open(path)

    rows = opened.read_lock_takeovers()
    assert rows.shape == (0,)
    assert set(rows.dtype.names) >= {"timestamp", "action", "ousted_user", "ousted_corrupt"}
