# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The opt-in update notice in the running shell (#248), headless under pytest-qt.

What these pin, in the order the issue lists them:

* off by default: with no answer, a declined answer or the administrator override, no
  outbound connection is even attempted — a guard on ``socket`` fails the test if one is;
* allowed: a newer release gives exactly one dismissible notice, an equal or older one none;
* the check never runs on the GUI thread, and building and starting the shell does not
  wait for it;
* a failing check is silent — no bar, no status message.

The no-network tests replace the low-level ``socket`` entry points, so they hold whatever
library a future change might use to make the request.
"""

from __future__ import annotations

import importlib.util
import json
import socket
import threading
import time
from pathlib import Path

import pytest

_HAS_QT = all(importlib.util.find_spec(m) is not None for m in ("pyqtgraph", "PySide6"))

pytestmark = [
    pytest.mark.gui,
    pytest.mark.skipif(not _HAS_QT, reason="pyqtgraph/PySide6 not installed"),
]


class _NoNetwork:
    """Fails loudly — and records — any attempt to resolve a name or open a connection."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.attempts: list[tuple[str, object]] = []

        def refuse(name: str):
            def _refuse(*args: object, **_kwargs: object) -> None:
                self.attempts.append((name, args[1:] if name == "connect" else args))
                raise OSError(f"test forbids the network: {name}")

            return _refuse

        monkeypatch.setattr(socket, "getaddrinfo", refuse("getaddrinfo"))
        monkeypatch.setattr(socket, "create_connection", refuse("create_connection"))
        monkeypatch.setattr(socket.socket, "connect", refuse("connect"))
        monkeypatch.setattr(socket.socket, "connect_ex", refuse("connect_ex"))


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> _NoNetwork:
    return _NoNetwork(monkeypatch)


@pytest.fixture
def store(tmp_path: Path) -> tuple[Path, Path]:
    """A private settings file and an absent override."""
    return tmp_path / "settings" / "settings.json", tmp_path / "machine" / "disable-update-check"


@pytest.fixture
def window(qtbot):
    from pyqtgraph.Qt import QtWidgets

    win = QtWidgets.QMainWindow()
    menu = win.menuBar().addMenu("&Help")
    menu.addAction("&Documentation")
    menu.addSeparator()
    menu.addAction("&About Tether")
    qtbot.addWidget(win)
    win.show()
    return win, menu


def _notice(window, store, **kwargs):
    from tether.gui.update_notice import UpdateNotice

    win, menu = window
    settings, override = store
    return UpdateNotice(
        win, help_menu=menu, settings_file=settings, override_file=override, **kwargs
    )


def _version(text: str):
    from tether.gui.update_check import parse_version

    return parse_version(text)


def _settle(qtbot, notice) -> None:
    """Let a started check finish and its queued result arrive."""
    thread = notice.check_thread
    if thread is not None:
        thread.join(5)
        assert not thread.is_alive()
    qtbot.wait(20)


# --- off by default ----------------------------------------------------------------


def test_with_no_answer_it_asks_and_makes_no_request(qtbot, window, store, no_network) -> None:
    from tether.gui.update_notice import QUESTION_TEXT

    notice = _notice(window, store)  # the real check, behind the socket guard
    notice.start()
    _settle(qtbot, notice)
    assert notice.asking
    assert notice.label_text == QUESTION_TEXT
    assert not notice.notice_shown
    assert notice.check_thread is None
    assert not notice.action.isChecked()
    assert no_network.attempts == []
    assert not store[0].exists()  # asking writes nothing


def test_declining_stores_the_answer_and_still_makes_no_request(
    qtbot, window, store, no_network
) -> None:
    from pyqtgraph.Qt import QtCore, QtWidgets

    from tether.gui import app_settings

    notice = _notice(window, store)
    notice.start()
    decline = window[0].findChild(QtWidgets.QPushButton, "tetherUpdateDecline")
    qtbot.mouseClick(decline, QtCore.Qt.MouseButton.LeftButton)
    _settle(qtbot, notice)
    assert app_settings.update_check_state(*store) is app_settings.UpdateCheckState.DECLINED
    assert not notice.bar.isVisible()
    assert notice.check_thread is None
    assert no_network.attempts == []


@pytest.mark.parametrize("answer", ["declined", "administrator"])
def test_a_declined_or_disabled_check_neither_asks_nor_requests(
    qtbot, window, store, no_network, answer: str
) -> None:
    from tether.gui import app_settings

    settings, override = store
    if answer == "declined":
        app_settings.record_update_consent(False, settings_file=settings)
    else:
        # The override wins even over a user who said yes.
        app_settings.record_update_consent(True, settings_file=settings)
        override.parent.mkdir(parents=True)
        override.touch()
    notice = _notice(window, store)
    notice.start()
    _settle(qtbot, notice)
    assert not notice.bar.isVisible()
    assert notice.check_thread is None
    assert no_network.attempts == []
    assert notice.action.isEnabled() is (answer == "declined")
    assert not notice.action.isChecked()


def test_the_real_shell_makes_no_request_until_asked(qtbot, no_network) -> None:
    """End to end through ``create_shell``: a fresh user gets the question and no request."""
    from pyqtgraph.Qt import QtCore

    from tether.gui.app import create_shell
    from tether.gui.update_notice import NOTICE_OBJECT_NAME

    _app, shell = create_shell([], splash=False)
    qtbot.addWidget(shell.window)
    try:
        shell.window.show()
        notice = shell.window.findChild(QtCore.QObject, NOTICE_OBJECT_NAME)
        assert notice is not None
        qtbot.waitUntil(lambda: notice.asking, timeout=2000)
        assert notice.action in shell.help_menu.actions()
        # About stays the last item of the Help menu.
        assert shell.help_menu.actions()[-1].text() == "&About Tether"
        _settle(qtbot, notice)
        assert no_network.attempts == []
    finally:
        shell.close()


# --- allowed -----------------------------------------------------------------------


def _allow(store) -> None:
    from tether.gui import app_settings

    app_settings.record_update_consent(True, settings_file=store[0])


def test_a_newer_release_gives_exactly_one_dismissible_notice(qtbot, window, store) -> None:
    from pyqtgraph.Qt import QtCore, QtWidgets

    from tether.gui.update_check import RELEASES_PAGE

    _allow(store)
    calls: list[str] = []

    def check():
        calls.append(threading.current_thread().name)
        return _version("v99.0.0")

    notice = _notice(window, store, check=check)
    notice.start()
    notice.start()  # a second start in the same run asks nothing more
    _settle(qtbot, notice)
    qtbot.waitUntil(lambda: notice.notice_shown, timeout=2000)
    assert len(calls) == 1
    assert calls[0] != threading.main_thread().name
    assert notice.shown_version == _version("99.0.0")
    assert "Tether 99.0.0 is available" in notice.label_text
    assert f'href="{RELEASES_PAGE}"' in notice.label_text
    assert notice.action.isChecked()
    bars = window[0].findChildren(QtWidgets.QToolBar, "tetherUpdateBar")
    assert len(bars) == 1

    # A late duplicate result does not stack a second notice.
    notice._result.emit(_version("v100.0.0"))
    qtbot.wait(20)
    assert notice.shown_version == _version("99.0.0")

    dismiss = window[0].findChild(QtWidgets.QPushButton, "tetherUpdateDismiss")
    qtbot.mouseClick(dismiss, QtCore.Qt.MouseButton.LeftButton)
    assert not notice.bar.isVisible()
    assert not notice.notice_shown


def test_the_link_opens_only_the_fixed_releases_page(
    qtbot, window, store, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tether.gui import about
    from tether.gui.update_check import RELEASES_PAGE

    opened: list[str] = []
    monkeypatch.setattr(about, "open_external", lambda url: opened.append(url) or True)
    _allow(store)
    notice = _notice(window, store, check=lambda: _version("v99.0.0"))
    notice.start()
    _settle(qtbot, notice)
    qtbot.waitUntil(lambda: notice.notice_shown, timeout=2000)
    notice._label.linkActivated.emit("https://example.invalid/not-this")
    assert opened == [RELEASES_PAGE]


@pytest.mark.parametrize("tag", ["v1.0.0", "v0.8.0"])
def test_an_equal_or_older_release_gives_no_notice(qtbot, window, store, tag: str) -> None:
    from tether.gui.update_check import check_for_update

    _allow(store)
    payload = json.dumps({"tag_name": tag}).encode()
    notice = _notice(window, store, check=lambda: check_for_update("1.0.0", fetch=lambda: payload))
    notice.start()
    _settle(qtbot, notice)
    assert notice.check_thread is not None
    assert not notice.bar.isVisible()


def test_a_failing_check_is_silent(qtbot, window, store) -> None:
    _allow(store)

    def boom():
        raise RuntimeError("anything at all")

    notice = _notice(window, store, check=boom)
    notice.start()
    _settle(qtbot, notice)
    assert not notice.bar.isVisible()
    assert window[0].statusBar().currentMessage() == ""


def test_the_real_check_with_no_network_is_silent(qtbot, window, store, no_network) -> None:
    """The real request, refused at the socket: it was tried, and the user sees nothing."""
    _allow(store)
    notice = _notice(window, store)
    notice.start()
    _settle(qtbot, notice)
    assert no_network.attempts  # the allowed check did try to connect
    assert not notice.bar.isVisible()
    assert window[0].statusBar().currentMessage() == ""


def test_a_result_after_the_answer_changed_is_not_shown(qtbot, window, store) -> None:
    from tether.gui import app_settings

    _allow(store)
    release = threading.Event()

    def slow():
        release.wait(5)
        return _version("v99.0.0")

    notice = _notice(window, store, check=slow)
    notice.start()
    app_settings.record_update_consent(False, settings_file=store[0])
    release.set()
    _settle(qtbot, notice)
    assert not notice.bar.isVisible()


# --- never in the way --------------------------------------------------------------


def test_a_slow_check_delays_neither_the_shell_nor_the_start(
    qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the check allowed and the network hanging, startup takes no longer."""
    from pyqtgraph.Qt import QtCore

    from tether.gui import app_settings, update_notice
    from tether.gui.app import create_shell

    app_settings.record_update_consent(True)  # the conftest's private store
    release = threading.Event()
    ran_on: list[str] = []

    def hanging():
        ran_on.append(threading.current_thread().name)
        release.wait(10)

    monkeypatch.setattr(update_notice, "check_for_update", hanging)
    try:
        _app, shell = create_shell([], splash=False)
        qtbot.addWidget(shell.window)
        notice = shell.window.findChild(QtCore.QObject, update_notice.NOTICE_OBJECT_NAME)
        # Building the shell did not start the check: it is queued for the event loop.
        assert notice.check_thread is None
        qtbot.waitUntil(lambda: notice.check_thread is not None, timeout=2000)
        # The queued start has returned to the event loop while the check still hangs,
        # and the check is not on the GUI thread.
        qtbot.waitUntil(lambda: bool(ran_on), timeout=2000)
        assert ran_on[0] != threading.main_thread().name
        assert notice.check_thread.daemon  # a hung check cannot keep the app alive
        assert notice.check_thread.is_alive()
        assert not notice.bar.isVisible()
    finally:
        release.set()
        shell.close()


def test_start_returns_at_once_while_the_check_hangs(qtbot, window, store) -> None:
    _allow(store)
    release = threading.Event()
    notice = _notice(window, store, check=lambda: release.wait(10))
    try:
        started = time.monotonic()
        notice.start()
        assert time.monotonic() - started < 0.5
        assert notice.check_thread.is_alive()
    finally:
        release.set()
        _settle(qtbot, notice)


# --- the Help menu toggle ----------------------------------------------------------


def test_the_help_menu_changes_the_answer(qtbot, window, store) -> None:
    from tether.gui import app_settings

    calls: list[int] = []
    notice = _notice(window, store, check=lambda: calls.append(1))
    notice.start()
    assert notice.asking

    notice.action.trigger()  # tick: allowed, and the check runs now
    _settle(qtbot, notice)
    assert app_settings.update_check_state(*store) is app_settings.UpdateCheckState.ALLOWED
    assert not notice.asking
    assert calls == [1]

    notice.action.trigger()  # untick: declined
    assert app_settings.update_check_state(*store) is app_settings.UpdateCheckState.DECLINED
    assert not notice.action.isChecked()
    assert calls == [1]


def test_the_menu_item_sits_before_about(window, store) -> None:
    notice = _notice(window, store)
    actions = window[1].actions()
    assert actions.index(notice.action) == len(actions) - 3  # item, separator, About
    assert notice.action.text() == "Check for new releases at startup"


def test_a_failed_save_says_so_and_the_session_still_follows_the_answer(
    qtbot, window, store, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tether.gui import app_settings

    def no_disk(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("read-only home")

    monkeypatch.setattr(app_settings, "record_update_consent", no_disk)
    notice = _notice(window, store, check=lambda: None)
    notice.start()
    notice.answer(False)
    assert "Could not save the answer" in window[0].statusBar().currentMessage()
    assert not notice.bar.isVisible()
