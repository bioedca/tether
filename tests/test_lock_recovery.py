# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the headless lock-recovery verdict and its guarded steal (#626, PRD §7.10).

[`lock_recovery`][tether.project.lock.lock_recovery] judges a lock this process does
not hold: a crashed session of this user on this computer is recoverable at once, a
running process on this computer is never taken, and every other holder is taken only
once stale by the wall clock (PRD §5.4, §11.2). [`recover_lock`][tether.project.lock.recover_lock]
re-judges under the lifecycle guard and refuses when the lock changed since the user
looked. No Qt and no HDF5: the lock lifecycle needs only a path.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
from datetime import UTC, datetime, timedelta

import pytest

from tether.project import lock
from tether.project.lock import (
    DEFAULT_STALENESS_TIMEOUT_S,
    LockedError,
    LockIdentity,
    RecoveryAction,
)

_TIMEOUT = timedelta(seconds=DEFAULT_STALENESS_TIMEOUT_S)


@pytest.fixture
def project_path(tmp_path):
    path = tmp_path / "exp.tether"
    path.write_bytes(b"")
    return path


@pytest.fixture
def me() -> LockIdentity:
    return lock.local_identity()


def _dead_pid() -> int:
    """The PID of a child that has exited (and been reaped)."""
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


@pytest.fixture
def running_pid():
    """The PID of a child that runs until the test ends."""
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        yield child.pid
    finally:
        child.kill()
        child.wait()


def _old(seconds_past_stale: float = 60.0) -> datetime:
    return datetime.now(UTC) - _TIMEOUT - timedelta(seconds=seconds_past_stale)


# --- the process probe --------------------------------------------------------


def test_pid_probe_sees_this_process_and_misses_an_exited_child() -> None:
    assert lock._pid_alive(os.getpid()) is True
    assert lock._pid_alive(_dead_pid()) is False


def test_pid_probe_sees_a_running_child(running_pid) -> None:
    assert lock._pid_alive(running_pid) is True


@pytest.mark.parametrize("pid", [0, -1])
def test_pid_probe_fails_closed_for_group_addresses(pid) -> None:
    # On POSIX these address process groups; never report them as a gone holder.
    assert lock._pid_alive(pid) is True


# --- the verdict --------------------------------------------------------------


def test_no_verdict_when_unlocked_or_ours(project_path, me) -> None:
    assert lock.lock_recovery(project_path, identity=me) is None
    held = lock.acquire(project_path, identity=me)
    try:
        assert lock.lock_recovery(project_path, identity=me) is None
    finally:
        assert lock.release(project_path, held)


def test_crashed_session_of_this_user_here_is_recoverable_at_once(project_path, me) -> None:
    crashed = LockIdentity(host=me.host, user=me.user, pid=_dead_pid())
    held = lock.acquire(project_path, identity=crashed)  # fresh, not stale

    verdict = lock.lock_recovery(project_path, identity=me)

    assert verdict is not None
    assert verdict.action is RecoveryAction.RECOVER
    assert verdict.owner == held
    assert verdict.same_host is True
    assert verdict.holder_running is False
    assert verdict.available_at is None
    assert verdict.corrupt is False


def test_running_process_here_is_never_taken_even_when_stale(project_path, me, running_pid) -> None:
    live = LockIdentity(host=me.host, user=me.user, pid=running_pid)
    lock.acquire(project_path, identity=live, now=_old(3600))

    verdict = lock.lock_recovery(project_path, identity=me)

    assert verdict is not None
    assert verdict.action is RecoveryAction.NONE
    assert verdict.holder_running is True
    assert verdict.available_at is None


def test_other_user_here_with_gone_process_waits_for_the_wall_clock(
    project_path, me, monkeypatch
) -> None:
    monkeypatch.setattr(lock, "_pid_alive", lambda pid: False)
    other = LockIdentity(host=me.host, user=f"{me.user}-other", pid=424242)
    fresh = lock.acquire(project_path, identity=other)

    waiting = lock.lock_recovery(project_path, identity=me)
    assert waiting is not None
    assert waiting.action is RecoveryAction.NONE
    assert waiting.available_at == fresh.acquired_at() + _TIMEOUT

    later = fresh.acquired_at() + _TIMEOUT + timedelta(seconds=1)
    stale = lock.lock_recovery(project_path, identity=me, now=later)
    assert stale is not None
    assert stale.action is RecoveryAction.TAKE_OVER
    assert stale.same_host is True
    assert stale.holder_running is False


def test_another_computer_is_judged_by_wall_clock_only(project_path, me, monkeypatch) -> None:
    def _no_probe(pid: int) -> bool:
        raise AssertionError("a remote PID must never be probed")

    monkeypatch.setattr(lock, "_pid_alive", _no_probe)
    remote = LockIdentity(host=f"{me.host}-OTHER", user="alice", pid=os.getpid())
    fresh = lock.acquire(project_path, identity=remote)

    waiting = lock.lock_recovery(project_path, identity=me)
    assert waiting is not None
    assert waiting.action is RecoveryAction.NONE
    assert waiting.same_host is False
    assert waiting.holder_running is None
    assert waiting.available_at == fresh.acquired_at() + _TIMEOUT

    stale = lock.lock_recovery(project_path, identity=me, now=waiting.available_at + _TIMEOUT)
    assert stale is not None
    assert stale.action is RecoveryAction.TAKE_OVER
    assert stale.available_at is None


def test_corrupt_lock_is_judged_by_its_file_age(project_path, me) -> None:
    lp = lock.lock_path(project_path)
    lp.write_text("{not json", encoding="utf-8")

    fresh = lock.lock_recovery(project_path, identity=me)
    assert fresh is not None
    assert fresh.corrupt is True
    assert fresh.action is RecoveryAction.NONE
    assert fresh.available_at is not None

    old = _old().timestamp()
    os.utime(lp, (old, old))
    stale = lock.lock_recovery(project_path, identity=me)
    assert stale is not None
    assert stale.corrupt is True
    assert stale.action is RecoveryAction.TAKE_OVER


# --- the guarded steal --------------------------------------------------------


def test_recover_takes_the_lock_and_logs_the_takeover(project_path, me, caplog) -> None:
    crashed = LockIdentity(host=me.host, user=me.user, pid=_dead_pid())
    prior = lock.acquire(project_path, identity=crashed)
    verdict = lock.lock_recovery(project_path, identity=me)
    assert verdict is not None

    with caplog.at_level(logging.WARNING, logger="tether.project.lock"):
        taken, ousted = lock.recover_lock(project_path, verdict, identity=me)

    assert ousted == prior
    assert taken.identity == me
    assert lock.read_lock(project_path) == taken
    record = next(r for r in caplog.records if r.name == "tether.project.lock")
    assert record.levelno == logging.WARNING
    assert "recover" in record.getMessage()
    assert f"pid {crashed.pid}" in record.getMessage()
    assert lock.release(project_path, taken)


def test_take_over_of_a_stale_foreign_lock(project_path, me, caplog) -> None:
    remote = LockIdentity(host=f"{me.host}-OTHER", user="alice", pid=1234)
    prior = lock.acquire(project_path, identity=remote, now=_old())
    verdict = lock.lock_recovery(project_path, identity=me)
    assert verdict is not None
    assert verdict.action is RecoveryAction.TAKE_OVER

    with caplog.at_level(logging.WARNING, logger="tether.project.lock"):
        taken, ousted = lock.recover_lock(project_path, verdict, identity=me)

    assert ousted == prior
    assert lock.read_lock(project_path) == taken
    assert any("take_over" in r.getMessage() and "alice@" in r.getMessage() for r in caplog.records)


def test_take_over_of_a_stale_corrupt_lock(project_path, me) -> None:
    lp = lock.lock_path(project_path)
    lp.write_text("{not json", encoding="utf-8")
    old = _old().timestamp()
    os.utime(lp, (old, old))
    verdict = lock.lock_recovery(project_path, identity=me)
    assert verdict is not None

    taken, ousted = lock.recover_lock(project_path, verdict, identity=me)

    assert ousted is None
    assert lock.read_lock(project_path) == taken


def test_recover_refuses_a_verdict_that_offers_nothing(project_path, me) -> None:
    remote = LockIdentity(host=f"{me.host}-OTHER", user="alice", pid=1234)
    held = lock.acquire(project_path, identity=remote)
    verdict = lock.lock_recovery(project_path, identity=me)
    assert verdict is not None
    assert verdict.action is RecoveryAction.NONE

    with pytest.raises(LockedError):
        lock.recover_lock(project_path, verdict, identity=me)
    assert lock.read_lock(project_path) == held


def test_take_over_refuses_when_the_holder_refreshed_since(project_path, me) -> None:
    remote = LockIdentity(host=f"{me.host}-OTHER", user="alice", pid=1234)
    held = lock.acquire(project_path, identity=remote, now=_old())
    verdict = lock.lock_recovery(project_path, identity=me)
    assert verdict is not None
    assert verdict.action is RecoveryAction.TAKE_OVER

    refreshed = lock.refresh(project_path, held)  # the holder came back

    with pytest.raises(LockedError):
        lock.recover_lock(project_path, verdict, identity=me)
    assert lock.read_lock(project_path) == refreshed


def test_take_over_refuses_when_another_writer_took_it_first(project_path, me) -> None:
    remote = LockIdentity(host=f"{me.host}-OTHER", user="alice", pid=1234)
    lock.acquire(project_path, identity=remote, now=_old())
    verdict = lock.lock_recovery(project_path, identity=me)
    assert verdict is not None

    rival = LockIdentity(host=f"{me.host}-THIRD", user="bob", pid=99)
    winner, _ = lock.steal_lock(project_path, identity=rival, now=_old())

    with pytest.raises(LockedError):
        lock.recover_lock(project_path, verdict, identity=me)
    assert lock.read_lock(project_path) == winner


def test_recover_refuses_when_the_process_reappears(project_path, me, monkeypatch) -> None:
    crashed = LockIdentity(host=me.host, user=me.user, pid=424242)
    held = lock.acquire(project_path, identity=crashed)
    monkeypatch.setattr(lock, "_pid_alive", lambda pid: False)
    verdict = lock.lock_recovery(project_path, identity=me)
    assert verdict is not None
    assert verdict.action is RecoveryAction.RECOVER

    monkeypatch.setattr(lock, "_pid_alive", lambda pid: True)  # e.g. a reused PID

    with pytest.raises(LockedError):
        lock.recover_lock(project_path, verdict, identity=me)
    assert lock.read_lock(project_path) == held


def test_recover_never_crosses_a_process_reservation(project_path, me) -> None:
    crashed = LockIdentity(host=me.host, user=me.user, pid=_dead_pid())
    held = lock.acquire(project_path, identity=crashed)
    verdict = lock.lock_recovery(project_path, identity=me)
    assert verdict is not None

    token = lock.acquire_process_reservation(project_path)
    try:
        with pytest.raises(LockedError):
            lock.recover_lock(project_path, verdict, identity=me)
    finally:
        lock.release_process_reservation(project_path, token)
    assert json.loads(lock.lock_path(project_path).read_text(encoding="utf-8"))["nonce"] == (
        held.nonce
    )


# --- hard-link aliases (ADR-0112) ---------------------------------------------


def _hard_link_pair(tmp_path) -> tuple:
    first = tmp_path / "a" / "first.tether"
    first.parent.mkdir()
    first.write_bytes(b"")
    second = tmp_path / "b" / "second.tether"
    second.parent.mkdir()
    try:
        os.link(first, second)
    except OSError as exc:  # pragma: no cover - filesystem without hard links
        pytest.skip(f"no hard links here: {exc}")
    if lock.file_identity(first) is None:  # pragma: no cover - no inode numbers
        pytest.skip("this filesystem reports no file identity")
    return first, second


def test_recovered_lock_keeps_a_hard_link_alias_out(tmp_path, me) -> None:
    first, second = _hard_link_pair(tmp_path)
    crashed = LockIdentity(host=me.host, user=me.user, pid=_dead_pid())
    lock.acquire(first, identity=crashed)
    verdict = lock.lock_recovery(first, identity=me)
    assert verdict is not None
    assert verdict.action is RecoveryAction.RECOVER

    taken, _ousted = lock.recover_lock(first, verdict, identity=me)

    rival = LockIdentity(host=f"{me.host}-OTHER", user="bob", pid=99)
    with pytest.raises(LockedError):
        lock.acquire(second, identity=rival)
    assert lock.read_lock(second) == taken
    assert lock.release(first, taken)
    claimed = lock.acquire(second, identity=rival)
    assert lock.release(second, claimed)


def test_recover_refuses_while_another_name_holds_the_file(tmp_path, me, monkeypatch) -> None:
    first, second = _hard_link_pair(tmp_path)
    crashed = LockIdentity(host=me.host, user=me.user, pid=424242)
    monkeypatch.setattr(lock, "_pid_alive", lambda pid: False)
    stale = lock.acquire(first, identity=crashed)
    verdict = lock.lock_recovery(first, identity=me)
    assert verdict is not None
    assert verdict.action is RecoveryAction.RECOVER

    # Another writer takes the file through the other name before the click.
    rival = LockIdentity(host=f"{me.host}-OTHER", user="bob", pid=99)
    lock.release(first, stale)
    winner = lock.acquire(second, identity=rival)
    lock._atomic_write(lock.lock_path(first), stale)  # the crashed record reappears

    with pytest.raises(LockedError):
        lock.recover_lock(first, verdict, identity=me)
    assert lock.read_lock(second) == winner
    assert lock.release(second, winner)


# --- recovery through a hard-link alias (#1124) -------------------------------


def _nonce_on_disk(path) -> str:
    return json.loads(lock.lock_path(path).read_text(encoding="utf-8"))["nonce"]


def test_recover_through_an_alias_judges_the_blocking_name(tmp_path, me) -> None:
    first, second = _hard_link_pair(tmp_path)
    crashed = LockIdentity(host=me.host, user=me.user, pid=_dead_pid())
    prior = lock.acquire(first, identity=crashed)

    verdict = lock.lock_recovery(second, identity=me)

    assert verdict is not None
    assert verdict.action is RecoveryAction.RECOVER
    assert verdict.owner == prior

    taken, ousted = lock.recover_lock(second, verdict, identity=me)

    assert ousted == prior
    assert taken.identity == me
    assert _nonce_on_disk(first) == taken.nonce
    assert _nonce_on_disk(second) == taken.nonce
    rival = LockIdentity(host=f"{me.host}-OTHER", user="bob", pid=99)
    for name in (first, second):
        with pytest.raises(LockedError):
            lock.acquire(name, identity=rival)
    assert lock.refresh(second, taken).nonce == taken.nonce
    assert lock.release(second, taken)
    assert not lock.lock_path(first).exists()
    assert not lock.lock_path(second).exists()
    claimed = lock.acquire(first, identity=rival)
    assert lock.release(first, claimed)


def test_take_over_through_an_alias(tmp_path, me, caplog) -> None:
    first, second = _hard_link_pair(tmp_path)
    remote = LockIdentity(host=f"{me.host}-OTHER", user="alice", pid=1234)
    prior = lock.acquire(first, identity=remote, now=_old())

    verdict = lock.lock_recovery(second, identity=me)

    assert verdict is not None
    assert verdict.action is RecoveryAction.TAKE_OVER
    assert verdict.owner == prior

    with caplog.at_level(logging.WARNING, logger="tether.project.lock"):
        taken, ousted = lock.recover_lock(second, verdict, identity=me)

    assert ousted == prior
    assert _nonce_on_disk(first) == taken.nonce
    assert _nonce_on_disk(second) == taken.nonce
    assert any("take_over" in r.getMessage() and "alice@" in r.getMessage() for r in caplog.records)
    assert lock.release(second, taken)
    assert not lock.lock_path(first).exists()


def test_recover_through_an_alias_refuses_when_the_blocking_lock_changed(tmp_path, me) -> None:
    first, second = _hard_link_pair(tmp_path)
    remote = LockIdentity(host=f"{me.host}-OTHER", user="alice", pid=1234)
    held = lock.acquire(first, identity=remote, now=_old())
    verdict = lock.lock_recovery(second, identity=me)
    assert verdict is not None
    assert verdict.action is RecoveryAction.TAKE_OVER

    refreshed = lock.refresh(first, held)  # the holder came back through its own name

    with pytest.raises(LockedError):
        lock.recover_lock(second, verdict, identity=me)
    assert lock.read_lock(first) == refreshed
    assert not lock.lock_path(second).exists()
    assert lock.release(first, refreshed)


def test_recover_through_an_alias_holds_the_alias_guard_while_it_judges(
    tmp_path, me, monkeypatch
) -> None:
    # Another OS user has a separate identity guard, so only the alias's own
    # lifecycle guard keeps their claim of the alias out between check and write.
    first, second = _hard_link_pair(tmp_path)
    crashed = LockIdentity(host=me.host, user=me.user, pid=_dead_pid())
    lock.acquire(first, identity=crashed)
    verdict = lock.lock_recovery(second, identity=me)
    assert verdict is not None

    monkeypatch.setattr(lock, "_GUARD_ACQUIRE_TIMEOUT_S", 0.3)
    probes: list[bool] = []
    judge = lock._classify_recovery

    def probe_the_alias_guard() -> None:
        try:
            with lock._lifecycle_guard(lock.lock_path(second)):
                probes.append(True)
        except TimeoutError:
            probes.append(False)

    def judging(*args, **kwargs):
        rival = threading.Thread(target=probe_the_alias_guard)
        rival.start()
        rival.join()
        return judge(*args, **kwargs)

    monkeypatch.setattr(lock, "_classify_recovery", judging)
    taken, _ousted = lock.recover_lock(second, verdict, identity=me)

    assert probes == [False]
    assert _nonce_on_disk(first) == taken.nonce
    assert _nonce_on_disk(second) == taken.nonce
    assert lock.release(second, taken)
