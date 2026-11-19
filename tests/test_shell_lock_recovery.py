# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Recover or take over a locked project from the shell's read-only banner (#626, PRD §7.10).

The crash case (a session of this user on this computer that no longer runs) gets a
one-click **Recover**; a stale lock of another computer or user gets **Take over…**
behind a typed confirmation; a running process on this computer, or a foreign lock that
is not yet stale, gets neither. Each takeover reopens the project through
[`load_project`][tether.gui.shell.TetherShell.load_project], so every write seam is
rebound, and is logged on ``tether.project.lock``.

All ``@pytest.mark.gui``, headless (``QT_QPA_PLATFORM=offscreen``), over a real
round-trip ``.tether``.
"""

from __future__ import annotations

import importlib.util
import logging
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

pytest.importorskip("h5py")

_HAS_QT = all(importlib.util.find_spec(m) is not None for m in ("pyqtgraph", "PySide6"))
_needs_qt = pytest.mark.skipif(not _HAS_QT, reason="pyqtgraph/PySide6 not installed")

pytestmark = [pytest.mark.gui, _needs_qt]


@pytest.fixture
def shell(qapp, qtbot):
    from tether.gui.shell import TetherShell

    s = TetherShell()
    qtbot.addWidget(s.window)
    yield s
    s.close()


@pytest.fixture
def running_pid():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        yield child.pid
    finally:
        child.kill()
        child.wait()


def _store(tmp_path):
    from _analysis_store import build_store_with_channels

    rng = np.random.default_rng(7)
    donor = rng.uniform(400.0, 800.0, size=(3, 12))
    acceptor = rng.uniform(200.0, 600.0, size=(3, 12))
    project, _keys = build_store_with_channels(tmp_path, donor, acceptor, name="rt.tether")
    return project


def _dead_pid() -> int:
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


def _stale_now(extra_s: float = 60.0) -> datetime:
    from tether.project.lock import DEFAULT_STALENESS_TIMEOUT_S

    return datetime.now(UTC) - timedelta(seconds=DEFAULT_STALENESS_TIMEOUT_S + extra_s)


def _foreign():
    from tether.project.lock import LockIdentity, local_identity

    return LockIdentity(host=f"{local_identity().host}-OTHER", user="alice", pid=4242)


def _text(value: object) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _assert_write_seams_work(shell, qtbot, project) -> None:
    """Accept the selected molecule and see the row land in ``/labels``."""
    from pyqtgraph.Qt import QtCore

    from tether.project.labels import read_labels

    before = read_labels(project.path).shape[0]
    shell.molecule_list.setCurrentRow(0)
    qtbot.keyClick(shell.molecule_list, QtCore.Qt.Key.Key_Space)
    assert read_labels(project.path).shape[0] == before + 1
    assert shell.unreject_button.isEnabled()


def test_crash_case_recovers_with_one_click(shell, qtbot, tmp_path, caplog) -> None:
    from pyqtgraph.Qt import QtCore

    from tether.project import lock
    from tether.project.lock import LockIdentity, RecoveryAction, local_identity

    project = _store(tmp_path)
    me = local_identity()
    crashed = LockIdentity(host=me.host, user=me.user, pid=_dead_pid())
    lock.acquire(project.path, identity=crashed)  # fresh: a crash just happened

    assert shell.load_project(project.path) is not None
    assert shell.curation_project is None
    assert shell.lock_recovery is not None
    assert shell.lock_recovery.action is RecoveryAction.RECOVER
    assert not shell.recover_button.isHidden()
    assert shell.take_over_button.isHidden()
    assert "Recover" in shell.read_only_banner.text()

    with caplog.at_level(logging.WARNING, logger="tether.project.lock"):
        qtbot.mouseClick(shell.recover_button, QtCore.Qt.MouseButton.LeftButton)

    assert shell.curation_project is not None
    assert shell.session_project is shell.curation_project
    assert shell.read_only_reason is None
    assert shell.read_only_banner.isHidden()
    assert shell.recover_button.isHidden()
    assert shell.lock_recovery is None
    assert shell.lock_refresh_active
    assert lock.read_lock(project.path).identity == me
    assert "Recover: took the lock" in shell.status_message
    assert any("recover" in r.getMessage() for r in caplog.records)
    # The durable record went in under the new lock (#1099).
    rows = shell.curation_project.read_lock_takeovers()
    assert [_text(a) for a in rows["action"]] == ["recover"]
    assert int(rows[0]["ousted_pid"]) == crashed.pid
    _assert_write_seams_work(shell, qtbot, project)


def test_running_process_on_this_computer_offers_nothing(shell, tmp_path, running_pid) -> None:
    from tether.project import lock
    from tether.project.lock import LockIdentity, RecoveryAction, local_identity

    project = _store(tmp_path)
    me = local_identity()
    live = LockIdentity(host=me.host, user=me.user, pid=running_pid)
    held = lock.acquire(project.path, identity=live, now=_stale_now(3600))

    assert shell.load_project(project.path) is not None
    assert shell.lock_recovery.action is RecoveryAction.NONE
    assert shell.recover_button.isHidden()
    assert shell.take_over_button.isHidden()
    assert "running Tether process on this computer" in shell.read_only_banner.text()

    assert shell.recover_project_lock() is None
    assert shell.take_over_project_lock(confirmation=project.path.name) is None
    assert "unavailable" in shell.status_message
    assert lock.read_lock(project.path) == held
    assert shell.curation_project is None


def test_foreign_lock_that_is_not_stale_cannot_be_taken(shell, tmp_path) -> None:
    from tether.project import lock
    from tether.project.lock import RecoveryAction

    project = _store(tmp_path)
    held = lock.acquire(project.path, identity=_foreign())

    assert shell.load_project(project.path) is not None
    recovery = shell.lock_recovery
    assert recovery.action is RecoveryAction.NONE
    assert recovery.available_at == held.acquired_at() + timedelta(
        seconds=lock.DEFAULT_STALENESS_TIMEOUT_S
    )
    assert shell.take_over_button.isHidden()
    assert shell.recover_button.isHidden()
    assert "takeover becomes possible at" in shell.read_only_banner.text()
    assert "locked" in shell.read_only_banner.text()

    assert shell.take_over_project_lock(confirmation=project.path.name) is None
    assert lock.read_lock(project.path) == held
    assert shell.curation_project is None


def test_stale_foreign_lock_needs_the_typed_confirmation(shell, qtbot, tmp_path) -> None:
    from tether.project import lock
    from tether.project.lock import RecoveryAction

    project = _store(tmp_path)
    held = lock.acquire(project.path, identity=_foreign(), now=_stale_now())

    assert shell.load_project(project.path) is not None
    assert shell.lock_recovery.action is RecoveryAction.TAKE_OVER
    assert not shell.take_over_button.isHidden()
    assert shell.recover_button.isHidden()

    assert shell.take_over_project_lock(confirmation="wrong.tether") is None
    assert "does not match rt.tether" in shell.status_message
    assert lock.read_lock(project.path) == held
    assert shell.curation_project is None
    assert shell.recover_project_lock() is None  # Recover is not the offered action
    assert lock.read_lock(project.path) == held

    opened = shell.take_over_project_lock(confirmation="rt.tether")

    assert opened is not None
    assert shell.curation_project is opened
    assert lock.read_lock(project.path).identity == lock.local_identity()
    assert "alice@" in shell.status_message
    assert "unsaved work is not merged" in shell.status_message
    rows = opened.read_lock_takeovers()
    assert rows.shape == (1,)  # the refused attempts above recorded nothing
    assert _text(rows[0]["action"]) == "take_over"
    assert _text(rows[0]["ousted_user"]) == "alice"
    assert _text(rows[0]["ousted_timestamp"]) == held.timestamp
    _assert_write_seams_work(shell, qtbot, project)


def test_take_over_button_asks_for_the_project_name(shell, qtbot, tmp_path, monkeypatch) -> None:
    from pyqtgraph.Qt import QtCore, QtWidgets

    from tether.project import lock

    project = _store(tmp_path)
    lock.acquire(project.path, identity=_foreign(), now=_stale_now())
    assert shell.load_project(project.path) is not None
    prompts: list[str] = []

    def cancel(_parent, _title, label, *args, **kwargs):
        prompts.append(label)
        return "", False

    monkeypatch.setattr(QtWidgets.QInputDialog, "getText", cancel)
    qtbot.mouseClick(shell.take_over_button, QtCore.Qt.MouseButton.LeftButton)
    assert shell.status_message == "Take over cancelled"
    assert shell.curation_project is None
    assert "alice@" in prompts[0]
    assert "lost" in prompts[0]
    assert "rt.tether" in prompts[0]

    monkeypatch.setattr(QtWidgets.QInputDialog, "getText", lambda *_a, **_k: (" rt.tether ", True))
    qtbot.mouseClick(shell.take_over_button, QtCore.Qt.MouseButton.LeftButton)
    assert shell.curation_project is not None


def test_take_over_refused_when_the_holder_came_back(shell, tmp_path) -> None:
    from tether.project import lock

    project = _store(tmp_path)
    held = lock.acquire(project.path, identity=_foreign(), now=_stale_now())
    assert shell.load_project(project.path) is not None
    assert not shell.take_over_button.isHidden()

    refreshed = lock.refresh(project.path, held)  # the holder resumed before the click

    assert shell.take_over_project_lock(confirmation="rt.tether") is None
    assert "refused" in shell.status_message
    assert lock.read_lock(project.path) == refreshed
    assert shell.curation_project is None
    # The banner was judged again: the refreshed lock is no longer stale.
    assert shell.take_over_button.isHidden()
    assert "takeover becomes possible at" in shell.read_only_banner.text()
    assert project.read_lock_takeovers().shape == (0,)  # a refusal records nothing


def test_banner_offers_take_over_once_the_lock_turns_stale(shell, qtbot, tmp_path) -> None:
    from tether.project import lock

    project = _store(tmp_path)
    held = lock.acquire(project.path, identity=_foreign(), now=_stale_now(-2.0))
    try:
        assert shell.load_project(project.path) is not None
        assert shell.take_over_button.isHidden()

        qtbot.waitUntil(lambda: not shell.take_over_button.isHidden(), timeout=15_000)

        assert "Take over" in shell.read_only_banner.text()
    finally:
        lock.release(project.path, held)


def test_closing_the_project_clears_the_lock_actions(shell, tmp_path) -> None:
    from tether.project import lock

    project = _store(tmp_path)
    held = lock.acquire(project.path, identity=_foreign(), now=_stale_now())
    try:
        assert shell.load_project(project.path) is not None
        assert not shell.take_over_button.isHidden()

        assert shell.close_project()

        assert shell.lock_recovery is None
        assert shell.take_over_button.isHidden()
        assert shell.recover_button.isHidden()
        assert shell.take_over_project_lock(confirmation="rt.tether") is None
        assert lock.read_lock(project.path) == held
    finally:
        lock.release(project.path, held)


def test_failed_reopen_keeps_retrying_the_release_of_the_taken_lock(
    shell, tmp_path, monkeypatch
) -> None:
    from tether.project import lock
    from tether.project.lock import LockIdentity, local_identity

    project = _store(tmp_path)
    me = local_identity()
    lock.acquire(project.path, identity=LockIdentity(host=me.host, user=me.user, pid=_dead_pid()))
    assert shell.load_project(project.path) is not None
    assert not shell.recover_button.isHidden()

    real_release = lock.release
    failures = {"left": 1}

    def flaky_release(path, info):
        if failures["left"]:
            failures["left"] -= 1
            raise OSError("share went away")
        return real_release(path, info)

    monkeypatch.setattr(lock, "release", flaky_release)
    monkeypatch.setattr(shell, "load_project", lambda *_a, **_k: None)

    assert shell.recover_project_lock() is None
    assert "Recover failed" in shell.status_message
    taken = lock.read_lock(project.path)
    assert taken is not None and taken.identity == me
    assert shell.lock_release_pending  # the timer owns the taken lock now
    assert project.read_lock_takeovers().shape == (0,)  # a failed reopen records nothing

    # Opening another project would stop that timer, so it waits for the release.
    monkeypatch.undo()
    (tmp_path / "other").mkdir()
    other = _store(tmp_path / "other")
    assert shell.load_project(other.path) is None
    assert "waiting to release a recovered project lock" in shell.status_message
    assert shell.lock_release_pending

    shell._retry_session_release()  # one timer tick

    assert lock.read_lock(project.path) is None
    assert not shell.lock_release_pending


def test_unconfirmed_release_keeps_the_taken_lock_for_the_retry(
    shell, tmp_path, monkeypatch
) -> None:
    """A release that returns ``False`` on an unreadable lock is not a release."""
    from tether.project import lock
    from tether.project.lock import CorruptLockError, LockIdentity, local_identity

    project = _store(tmp_path)
    me = local_identity()
    lock.acquire(project.path, identity=LockIdentity(host=me.host, user=me.user, pid=_dead_pid()))
    assert shell.load_project(project.path) is not None

    def unreadable(_path):
        raise CorruptLockError("unreadable for a moment")

    monkeypatch.setattr(lock, "release", lambda *_a, **_k: False)
    monkeypatch.setattr(lock, "read_lock", unreadable)
    monkeypatch.setattr(shell, "load_project", lambda *_a, **_k: None)

    assert shell.recover_project_lock() is None
    assert shell.lock_release_pending

    monkeypatch.undo()
    shell._retry_session_release()

    assert lock.read_lock(project.path) is None
    assert not shell.lock_release_pending


def test_read_only_reopen_is_not_reported_as_writable(shell, tmp_path, monkeypatch) -> None:
    """A reopen that keeps a session but cannot write reports the failure."""
    from tether.gui import shell as shell_module
    from tether.project import lock
    from tether.project.core import Project
    from tether.project.lock import LockIdentity, local_identity

    project = _store(tmp_path)
    me = local_identity()
    lock.acquire(project.path, identity=LockIdentity(host=me.host, user=me.user, pid=_dead_pid()))
    assert shell.load_project(project.path) is not None

    def not_writable(_path):
        raise OSError("read-only share")

    def release_fails(_self):
        raise OSError("share went away")

    monkeypatch.setattr(shell_module, "_probe_project_writable", not_writable)
    monkeypatch.setattr(Project, "release_lock", release_fails)

    assert shell.recover_project_lock() is None

    assert shell.curation_project is None
    assert "Recover failed" in shell.status_message
    assert "writable" not in shell.status_message.replace("not writable", "")
    monkeypatch.undo()
    assert project.read_lock_takeovers().shape == (0,)  # a read-only reopen records nothing


def test_recheck_describes_the_current_holder(shell, tmp_path) -> None:
    """The banner names the holder of the lock now, not the one seen at open."""
    from tether.project import lock
    from tether.project.lock import LockIdentity

    project = _store(tmp_path)
    lock.acquire(project.path, identity=_foreign())
    assert shell.load_project(project.path) is not None
    assert "alice@" in shell.read_only_banner.text()

    bob = LockIdentity(host=f"{lock.local_identity().host}-THIRD", user="bob", pid=99)
    winner, _ = lock.steal_lock(project.path, identity=bob)
    try:
        shell._recheck_lock_recovery()

        assert "bob@" in shell.read_only_banner.text()
        assert "alice@" not in shell.read_only_banner.text()
        assert shell.lock_recovery.owner == winner
    finally:
        lock.release(project.path, winner)


def test_takeover_stands_when_its_record_cannot_be_written(shell, tmp_path, monkeypatch) -> None:
    """A record that fails to write is reported; the lock taken is kept (#1099)."""
    from tether.project import lock
    from tether.project.core import Project
    from tether.project.lock import LockIdentity, local_identity

    project = _store(tmp_path)
    me = local_identity()
    lock.acquire(project.path, identity=LockIdentity(host=me.host, user=me.user, pid=_dead_pid()))
    assert shell.load_project(project.path) is not None

    def record_fails(_self, *_a):
        raise OSError("disk full")

    monkeypatch.setattr(Project, "record_lock_takeover", record_fails)

    opened = shell.recover_project_lock()

    assert opened is not None
    assert shell.curation_project is opened
    assert lock.read_lock(project.path).identity == me
    assert "is writable" in shell.status_message
    assert "the takeover record was not written (disk full)" in shell.status_message
    monkeypatch.undo()
    assert project.read_lock_takeovers().shape == (0,)
