# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""**Install update** on the update notice (#1379), headless under pytest-qt.

What these pin, in the order the issue lists them:

* with consent and a newer stable release, the notice offers Install; the download, the
  verification and the hand-off run in that order and only in that order, the first two off
  the GUI thread and the hand-off on it;
* no consent, the administrator override, or an install that ``check_install`` refuses: no
  Install button, and no download request even when ``install`` is called — a guard on
  ``socket`` fails the test if one is tried;
* a refused verification shows its reason and hands nothing off; an unavailable one is
  silent and leaves the link;
* the result of the last update is shown once at the next start, and the file is removed;
* the progress bar follows the download, and **Cancel** stops it and drops its file.
"""

from __future__ import annotations

import importlib.util
import socket
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

_HAS_QT = all(importlib.util.find_spec(m) is not None for m in ("pyqtgraph", "PySide6"))

pytestmark = [
    pytest.mark.gui,
    pytest.mark.skipif(not _HAS_QT, reason="pyqtgraph/PySide6 not installed"),
]


class _NoNetwork:
    """Fails loudly — and records — any attempt to resolve a name or open a connection."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.attempts: list[str] = []

        def refuse(name: str):
            def _refuse(*_args: object, **_kwargs: object) -> None:
                self.attempts.append(name)
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
    return tmp_path / "settings" / "settings.json", tmp_path / "machine" / "disable-update-check"


@pytest.fixture
def window(qtbot):
    from pyqtgraph.Qt import QtWidgets

    win = QtWidgets.QMainWindow()
    menu = win.menuBar().addMenu("&Help")
    menu.addSeparator()
    menu.addAction("&About Tether")
    qtbot.addWidget(win)
    # Wide enough that the bar's buttons are not folded into its overflow menu.
    win.resize(1400, 600)
    win.show()
    return win


def _allow(store) -> None:
    from tether.gui import app_settings

    app_settings.record_update_consent(True, settings_file=store[0])


def _version(text: str):
    from tether.gui.update_check import parse_version

    return parse_version(text)


class _Steps:
    """Stand-ins for the download, the verifier and the hand-off that record what ran where."""

    def __init__(self, tmp_path: Path, *, verify: str = "verified", reason: str = "") -> None:
        self.tmp_path = tmp_path
        self.log: list[tuple[str, str]] = []
        self.verdict = verify
        self.reason = reason
        self.quits: list[bool] = []
        self.handoff_args: dict = {}
        self.installer = object()
        self.folder: Path | None = None
        self.gate: threading.Event | None = None
        self.ignore_cancel = False
        self.verify_gate: threading.Event | None = None
        self.verify_cancelled = None

    def download(self, version, *, progress, cancelled):
        from tether.gui.update_download import DownloadOutcome, DownloadResult

        self.log.append(("download", threading.current_thread().name))
        progress(0, 4)
        if self.gate is not None:
            self.gate.wait(5)
        if cancelled() and not self.ignore_cancel:
            return DownloadResult(DownloadOutcome.CANCELLED)
        self.folder = self.tmp_path / "download"
        self.folder.mkdir()
        path = self.folder / f"Tether-{version}-Linux-x86_64.sh"
        path.write_bytes(b"abcd")
        progress(4, 4)
        return DownloadResult(DownloadOutcome.DOWNLOADED, path=path)

    def verify(self, path, version, *, settings_file, override_file, gh, cancelled):
        from tether.gui.update_verify import Outcome

        self.log.append(("verify", threading.current_thread().name))
        self.verify_cancelled = cancelled
        if self.verify_gate is not None:
            self.verify_gate.wait(5)
            # As on Windows: the file was open here, so the first removal left it behind.
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(b"abcd")
            return SimpleNamespace(
                outcome=Outcome("unavailable" if cancelled() else self.verdict),
                reason=self.reason,
                installer=None,
            )
        assert path.read_bytes() == b"abcd"
        installer = self.installer if self.verdict == "verified" else None
        return SimpleNamespace(
            outcome=Outcome(self.verdict), reason=self.reason, installer=installer
        )

    def hand_off(self, installer, *, prefix, quit_app, settings_file, override_file):
        from tether.gui.update_handoff import HandoffOutcome, HandoffResult

        self.log.append(("hand_off", threading.current_thread().name))
        assert installer is self.installer
        self.handoff_args = {"prefix": prefix}
        quit_app()
        return HandoffResult(HandoffOutcome.HANDED_OFF, "Tether will close, install 99.0.0.")


def _notice(window, store, steps: _Steps | None = None, **kwargs):
    from tether.gui.update_notice import UpdateNotice

    settings, override = store
    if steps is not None:
        kwargs.setdefault("download", steps.download)
        kwargs.setdefault("verify", steps.verify)
        kwargs.setdefault("hand_off", steps.hand_off)
        kwargs.setdefault("quit_app", lambda: steps.quits.append(True))
    kwargs.setdefault("can_install", lambda version: True)
    kwargs.setdefault("check", lambda: _version("v99.0.0"))
    kwargs.setdefault("result_file", settings.parent / "update-result.txt")
    return UpdateNotice(
        window,
        settings_file=settings,
        override_file=override,
        install_prefix=Path("/opt/tether-install"),
        gh=Path("/opt/tether-install/verifier/gh/gh"),
        **kwargs,
    )


def _shown(qtbot, notice) -> None:
    notice.start()
    if notice.check_thread is not None:
        notice.check_thread.join(5)
    qtbot.waitUntil(lambda: notice.notice_shown, timeout=2000)


def _settle_job(qtbot, notice) -> None:
    thread = notice.install_thread
    if thread is not None:
        thread.join(5)
        assert not thread.is_alive()
    qtbot.wait(20)


def _click(qtbot, window, name: str) -> None:
    from pyqtgraph.Qt import QtCore, QtWidgets

    button = window.findChild(QtWidgets.QPushButton, name)
    assert button is not None, name
    # The bar lays out its widgets from the event loop, so a button just made visible
    # shows on the next pass.
    qtbot.waitUntil(button.isVisible, timeout=2000)
    qtbot.mouseClick(button, QtCore.Qt.MouseButton.LeftButton)


# --- the happy path, in order --------------------------------------------------------


def test_install_downloads_verifies_and_hands_off_in_that_order(
    qtbot, window, store, tmp_path, no_network
) -> None:
    _allow(store)
    steps = _Steps(tmp_path)
    notice = _notice(window, store, steps)
    _shown(qtbot, notice)
    assert notice.install_offered
    assert "Install it now" in notice.label_text

    _click(qtbot, window, "tetherUpdateInstall")
    qtbot.waitUntil(lambda: notice.mode == "confirm", timeout=3000)
    assert [step for step, _ in steps.log] == ["download", "verify"]
    gui = threading.main_thread().name
    assert all(thread != gui for _, thread in steps.log)
    assert "Tether will close, install 99.0.0 and start again." in notice.label_text
    assert steps.quits == []  # nothing is handed off before the user confirms

    _click(qtbot, window, "tetherUpdateConfirm")
    assert [step for step, _ in steps.log] == ["download", "verify", "hand_off"]
    assert steps.log[-1][1] == gui  # the hand-off runs on the GUI thread
    assert steps.quits == [True]
    assert steps.handoff_args["prefix"] == Path("/opt/tether-install")
    assert notice.mode == "closing"
    assert steps.folder is not None and not steps.folder.exists()  # the download is removed
    assert no_network.attempts == []


def test_the_progress_bar_follows_the_download(qtbot, window, store, tmp_path) -> None:
    from pyqtgraph.Qt import QtWidgets

    _allow(store)
    steps = _Steps(tmp_path)
    steps.gate = threading.Event()
    notice = _notice(window, store, steps)
    _shown(qtbot, notice)
    notice.install()
    bar = window.findChild(QtWidgets.QProgressBar, "tetherUpdateProgress")
    assert notice.mode == "downloading"
    assert "Downloading Tether 99.0.0" in notice.label_text
    qtbot.waitUntil(bar.isVisible, timeout=2000)
    qtbot.waitUntil(lambda: bar.maximum() == 1000, timeout=2000)  # (0, 4) arrived
    assert bar.value() == 0
    steps.gate.set()
    qtbot.waitUntil(lambda: notice.mode == "confirm", timeout=3000)


# --- no request without consent or a copy that can update -----------------------------


@pytest.mark.parametrize("case", ["declined", "administrator"])
def test_consent_gone_before_install_makes_no_download_request(
    qtbot, window, store, tmp_path, no_network, case: str
) -> None:
    """The real download behind the socket guard: once consent is gone, nothing is sent."""
    from tether.gui import app_settings

    _allow(store)
    notice = _notice(window, store, download=None, verify=None, hand_off=None)
    _shown(qtbot, notice)
    assert notice.install_offered
    if case == "declined":
        app_settings.record_update_consent(False, settings_file=store[0])
    else:
        store[1].parent.mkdir(parents=True)
        store[1].touch()
    notice.install()
    _settle_job(qtbot, notice)
    assert notice.install_thread is None
    assert notice.mode == "notice"
    assert no_network.attempts == []


def test_without_consent_there_is_no_notice_and_no_download(
    qtbot, window, store, tmp_path, no_network
) -> None:
    calls: list[int] = []
    notice = _notice(window, store, download=lambda *a, **k: calls.append(1))
    notice.start()  # no answer: the question, no check
    notice.install()
    assert notice.asking
    assert calls == [] and no_network.attempts == []


def test_an_install_that_cannot_update_itself_keeps_the_link_only(
    qtbot, window, store, tmp_path, no_network
) -> None:
    calls: list[int] = []
    _allow(store)
    notice = _notice(
        window, store, can_install=lambda version: False, download=lambda *a, **k: calls.append(1)
    )
    _shown(qtbot, notice)
    assert not notice.install_offered
    assert "Open the Releases page</a> to download it." in notice.label_text
    notice.install()
    assert calls == [] and notice.install_thread is None
    assert no_network.attempts == []


def test_check_install_decides_the_default_offer(
    qtbot, window, store, tmp_path, monkeypatch: pytest.MonkeyPatch, no_network
) -> None:
    from tether.gui import update_handoff, update_notice

    monkeypatch.setattr(update_handoff, "handoff_platform", lambda system=None: "linux")
    _allow(store)
    prefix = tmp_path / "apps" / "tether"
    win = window

    def build(**kwargs):
        notice = update_notice.UpdateNotice(
            win,
            settings_file=store[0],
            override_file=store[1],
            check=lambda: _version("v99.0.0"),
            result_file=tmp_path / "none.txt",
            **kwargs,
        )
        _shown(qtbot, notice)
        return notice

    # No verifier (a development checkout): no offer.
    assert not build(install_prefix=prefix, gh=None).install_offered
    # A folder that holds no installed Tether: check_install refuses.
    gh = tmp_path / "gh"
    gh.touch()
    assert not build(install_prefix=prefix, gh=gh).install_offered
    # An installed layout: offered.
    (prefix / "envs" / "tether" / "conda-meta").mkdir(parents=True)
    (prefix / "launchers").mkdir()
    assert build(install_prefix=prefix, gh=gh).install_offered
    # A leftover backup from an earlier update: refused again.
    prefix.with_name("tether" + update_handoff.BACKUP_SUFFIX).mkdir()
    assert not build(install_prefix=prefix, gh=gh).install_offered
    assert no_network.attempts == []


def test_the_real_shell_passes_this_copys_folder_and_verifier(qtbot, no_network) -> None:
    from pyqtgraph.Qt import QtCore

    from tether import _verifier
    from tether.gui.app import create_shell
    from tether.gui.update_notice import NOTICE_OBJECT_NAME

    _app, shell = create_shell([], splash=False)
    qtbot.addWidget(shell.window)
    try:
        notice = shell.window.findChild(QtCore.QObject, NOTICE_OBJECT_NAME)
        assert notice._install_prefix == _verifier.install_prefix()
        assert notice._gh == _verifier.bundled_gh()
        assert notice._quit_app == shell.quit
    finally:
        shell.close()
    assert no_network.attempts == []


# --- refused and unavailable -------------------------------------------------------------


def test_a_refused_verification_shows_its_reason_and_hands_nothing_off(
    qtbot, window, store, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tether.gui import about
    from tether.gui.update_check import RELEASES_PAGE

    opened: list[str] = []
    monkeypatch.setattr(about, "open_external", lambda url: opened.append(url) or True)
    _allow(store)
    steps = _Steps(tmp_path, verify="refused", reason="The attestation is for <another> file.")
    notice = _notice(window, store, steps)
    _shown(qtbot, notice)
    notice.install()
    qtbot.waitUntil(lambda: notice.mode == "refused", timeout=3000)
    assert "The attestation is for &lt;another&gt; file." in notice.label_text
    assert f'href="{RELEASES_PAGE}"' in notice.label_text
    assert [step for step, _ in steps.log] == ["download", "verify"]
    assert steps.quits == []
    assert steps.folder is not None and not steps.folder.exists()
    notice._label.linkActivated.emit("https://example.invalid/")
    assert opened == [RELEASES_PAGE]
    _click(qtbot, window, "tetherUpdateDismiss")
    assert not notice.bar.isVisible()


def test_an_unavailable_verification_is_silent_and_keeps_the_link(
    qtbot, window, store, tmp_path
) -> None:
    _allow(store)
    steps = _Steps(tmp_path, verify="unavailable", reason="no network")
    notice = _notice(window, store, steps)
    _shown(qtbot, notice)
    notice.install()
    qtbot.waitUntil(lambda: [s for s, _ in steps.log] == ["download", "verify"], timeout=3000)
    qtbot.waitUntil(lambda: notice.mode == "notice", timeout=3000)
    assert not notice.install_offered  # for the rest of the session
    assert "no network" not in notice.label_text
    assert window.statusBar().currentMessage() == ""
    assert steps.quits == []
    assert steps.folder is not None and not steps.folder.exists()


def test_a_refused_hand_off_shows_its_reason(qtbot, window, store, tmp_path) -> None:
    from tether.gui.update_handoff import HandoffOutcome, HandoffResult

    _allow(store)
    steps = _Steps(tmp_path)
    notice = _notice(
        window,
        store,
        steps,
        hand_off=lambda installer, **kw: HandoffResult(
            HandoffOutcome.REFUSED, "Tether could not start the update."
        ),
    )
    _shown(qtbot, notice)
    notice.install()
    qtbot.waitUntil(lambda: notice.mode == "confirm", timeout=3000)
    notice.confirm()
    assert notice.mode == "refused"
    assert "Tether could not start the update." in notice.label_text
    assert steps.quits == []


def test_a_download_that_does_not_finish_says_so_and_keeps_the_notice(
    qtbot, window, store, tmp_path
) -> None:
    from tether.gui.update_download import DownloadOutcome, DownloadResult
    from tether.gui.update_notice import DOWNLOAD_FAILED_TEXT

    _allow(store)
    verified: list[int] = []
    notice = _notice(
        window,
        store,
        download=lambda version, **kw: DownloadResult(DownloadOutcome.FAILED, reason="offline"),
        verify=lambda *a, **k: verified.append(1),
    )
    _shown(qtbot, notice)
    notice.install()
    _settle_job(qtbot, notice)
    qtbot.waitUntil(lambda: notice.mode == "notice", timeout=2000)
    assert window.statusBar().currentMessage() == DOWNLOAD_FAILED_TEXT
    assert verified == []
    assert notice.install_offered  # the user may try again


# --- cancel ----------------------------------------------------------------------------


def test_cancel_stops_the_download_and_drops_a_late_result(qtbot, window, store, tmp_path) -> None:
    _allow(store)
    steps = _Steps(tmp_path)
    steps.gate = threading.Event()
    notice = _notice(window, store, steps)
    _shown(qtbot, notice)
    notice.install()
    assert notice.mode == "downloading"
    _click(qtbot, window, "tetherUpdateCancel")
    assert notice.mode == "notice"
    steps.gate.set()
    _settle_job(qtbot, notice)
    assert notice.mode == "notice"
    assert [step for step, _ in steps.log] == ["download"]


def test_cancel_at_the_confirmation_removes_the_download(qtbot, window, store, tmp_path) -> None:
    _allow(store)
    steps = _Steps(tmp_path)
    notice = _notice(window, store, steps)
    _shown(qtbot, notice)
    notice.install()
    qtbot.waitUntil(lambda: notice.mode == "confirm", timeout=3000)
    _click(qtbot, window, "tetherUpdateCancel")
    assert notice.mode == "notice"
    assert steps.folder is not None and not steps.folder.exists()
    notice.confirm()  # nothing left to hand off
    assert steps.quits == []


# --- quitting -------------------------------------------------------------------------


def test_quitting_at_the_confirmation_removes_the_download(qtbot, window, store, tmp_path) -> None:
    _allow(store)
    steps = _Steps(tmp_path)
    notice = _notice(window, store, steps)
    _shown(qtbot, notice)
    notice.install()
    qtbot.waitUntil(lambda: notice.mode == "confirm", timeout=3000)
    assert steps.folder is not None and steps.folder.exists()
    notice.shutdown()
    assert not steps.folder.exists()
    notice.confirm()  # nothing left to hand off
    assert steps.quits == []


def test_a_download_that_finishes_after_quitting_removes_its_own_folder(
    qtbot, window, store, tmp_path
) -> None:
    _allow(store)
    steps = _Steps(tmp_path)
    steps.gate = threading.Event()
    steps.ignore_cancel = True  # the worst case: the download does not stop when asked
    notice = _notice(window, store, steps)
    _shown(qtbot, notice)
    notice.install()
    thread = notice.install_thread
    assert thread is not None
    threading.Timer(0.2, steps.gate.set).start()
    notice.shutdown()  # waits for the thread, which finishes the download after the quit
    assert not thread.is_alive()
    assert steps.folder is not None and not steps.folder.exists()
    qtbot.wait(20)
    assert [step for step, _ in steps.log] == ["download"]  # nothing verified after the quit


def test_quitting_during_the_download_stops_it(qtbot, window, store, tmp_path) -> None:
    _allow(store)
    steps = _Steps(tmp_path)
    steps.gate = threading.Event()
    notice = _notice(window, store, steps)
    _shown(qtbot, notice)
    notice.install()
    thread = notice.install_thread
    threading.Timer(0.2, steps.gate.set).start()
    notice.shutdown()
    assert thread is not None and not thread.is_alive()
    assert steps.folder is None  # the download saw the cancel and wrote nothing


def test_the_notice_shuts_down_when_the_application_quits(qtbot, window, store) -> None:
    from pyqtgraph.Qt import QtWidgets

    notice = _notice(window, store)
    app = QtWidgets.QApplication.instance()
    # Disconnecting succeeds only for a connected slot; it also keeps this test's notice
    # out of any later quit.
    assert app.aboutToQuit.disconnect(notice.shutdown)


def test_cancel_during_verification_stops_it_and_removes_the_file(
    qtbot, window, store, tmp_path
) -> None:
    _allow(store)
    steps = _Steps(tmp_path)
    steps.verify_gate = threading.Event()
    notice = _notice(window, store, steps)
    _shown(qtbot, notice)
    notice.install()
    qtbot.waitUntil(lambda: notice.mode == "verifying", timeout=3000)
    qtbot.waitUntil(lambda: steps.verify_cancelled is not None, timeout=3000)
    assert steps.verify_cancelled() is False
    _click(qtbot, window, "tetherUpdateCancel")
    assert steps.verify_cancelled() is True  # the verifier sends nothing more
    steps.verify_gate.set()
    _settle_job(qtbot, notice)
    assert notice.mode == "notice"
    assert steps.folder is not None and not steps.folder.exists()  # removed again, by the job


def test_turning_the_check_off_during_verification_stops_it(qtbot, window, store, tmp_path) -> None:
    _allow(store)
    steps = _Steps(tmp_path)
    steps.verify_gate = threading.Event()
    notice = _notice(window, store, steps)
    _shown(qtbot, notice)
    notice.install()
    qtbot.waitUntil(lambda: steps.verify_cancelled is not None, timeout=3000)
    notice.action.trigger()  # untick Help -> Check for new releases at startup
    assert steps.verify_cancelled() is True
    steps.verify_gate.set()
    _settle_job(qtbot, notice)
    assert [step for step, _ in steps.log] == ["download", "verify"]
    assert steps.quits == []
    assert steps.folder is not None and not steps.folder.exists()


def test_turning_the_check_off_during_the_download_stops_the_update(
    qtbot, window, store, tmp_path
) -> None:
    _allow(store)
    steps = _Steps(tmp_path)
    steps.gate = threading.Event()
    notice = _notice(window, store, steps)
    _shown(qtbot, notice)
    notice.install()
    notice.action.trigger()  # untick Help -> Check for new releases at startup
    steps.gate.set()
    _settle_job(qtbot, notice)
    assert [step for step, _ in steps.log] == ["download"]
    assert notice.mode == "notice"
    assert not notice.install_offered


# --- the result of the last update --------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("updated 1.0.1", "Tether was updated to 1.0.1."),
        (
            "restored 1.0.1 the installer exited 2",
            "did not install, so Tether put the previous copy back (the installer exited 2).",
        ),
        ("unchanged 1.0.1 Tether did not close", "did not start, and nothing was changed"),
        (
            "failed 1.0.1 the check failed; the previous copy is in /x/tether.tether-update-backup",
            "the previous copy is in /x/tether.tether-update-backup",
        ),
    ],
)
def test_the_last_updates_result_is_shown_once_and_removed(
    qtbot, window, store, tmp_path, line: str, expected: str
) -> None:
    result = store[0].parent / "update-result.txt"
    result.parent.mkdir(parents=True)
    result.write_text(line + "\n", encoding="utf-8")
    _allow(store)
    notice = _notice(window, store, check=lambda: None)
    notice.start()
    assert notice.mode == "report"
    assert expected in notice.label_text
    assert notice.report is not None and expected in notice.report
    assert not result.exists()
    _click(qtbot, window, "tetherUpdateDismiss")
    assert not notice.bar.isVisible()

    again = _notice(window, store, check=lambda: None)
    again.start()
    assert again.report is None
    assert again.mode is None


def test_the_question_waits_for_the_report(qtbot, window, store, tmp_path) -> None:
    result = store[0].parent / "update-result.txt"
    result.parent.mkdir(parents=True)
    result.write_text("updated 1.0.1\n", encoding="utf-8")
    notice = _notice(window, store)  # no answer stored: the question is pending
    notice.start()
    assert notice.mode == "report"
    _click(qtbot, window, "tetherUpdateDismiss")
    assert notice.asking


def test_a_question_answered_from_help_during_the_report_is_not_asked_again(
    qtbot, window, store, tmp_path
) -> None:
    result = store[0].parent / "update-result.txt"
    result.parent.mkdir(parents=True)
    result.write_text("updated 1.0.1\n", encoding="utf-8")
    notice = _notice(window, store, check=lambda: None)  # no newer release
    notice.start()
    assert notice.mode == "report"
    notice.action.trigger()  # tick Help -> Check for new releases at startup
    _click(qtbot, window, "tetherUpdateDismiss")
    assert not notice.asking
    assert notice.mode is None


def test_a_malformed_result_is_removed_and_shows_nothing(qtbot, window, store, tmp_path) -> None:
    result = store[0].parent / "update-result.txt"
    result.parent.mkdir(parents=True)
    result.write_text("<b>nonsense</b>\n", encoding="utf-8")
    _allow(store)
    notice = _notice(window, store, check=lambda: None)
    notice.start()
    assert notice.mode is None and notice.report is None
    assert not result.exists()
