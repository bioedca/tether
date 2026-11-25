# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The opt-in update notice in the shell: the first-run question and the "newer release" bar (#248).

**Off until the user says yes.** At startup, ``start`` on the notice that
[`UpdateNotice`][] builds reads the consent answer
([`tether.gui.app_settings.update_check_state`][], ADR-0095). Only *allowed* starts a check.

============================  ===============================================================
State                         What the shell does
============================  ===============================================================
no answer yet (*pending*)     Shows the question in a bar above the window's content, with
                              **Check for new releases** and **Don't check**. No request.
allowed                       Starts one check on a background thread. A newer release shows
                              one dismissible bar with a link to the Releases page.
declined                      Nothing. No request and no question.
disabled by administrator     Nothing, and the Help menu item is greyed out. No request.
============================  ===============================================================

The answer can be changed at any time with **Help → Check for new releases at startup**.
Ticking it there, or pressing **Check for new releases** on the question, stores *allowed* and
runs the check at once; unticking it stores *declined*.

**Never on the GUI thread, never in the way.** ``start`` on the notice that
[`UpdateNotice`][] builds reads one small file
and, when allowed, starts a daemon thread; it returns at once. The thread runs
[`tether.gui.update_check.check_for_update`][] and hands the result back to the GUI thread
through a queued Qt signal. Whatever goes wrong there, the user sees nothing: no dialog, no
status message, no bar. A slow or dead network therefore cannot delay the window, and a
thread still waiting when the application quits does not keep it alive.

**One notice per run.** A newer release produces exactly one bar; dismissing it hides it for
the rest of the session. It is not remembered: the store holds only the consent answer
and the trace colours (ADR-0095, ADR-0123), so the next start asks GitHub again and shows
the bar again while the installed copy is still older.

**What the link opens.** Always [`tether.gui.update_check.RELEASES_PAGE`][], never a URL from
GitHub's answer; the only text of the answer the bar shows is the version, re-spelled from
its parsed form.

**Install update** (#1379, ADR-0060 decision 1). The notice offers it only while the check is
*allowed* and this copy can update itself: an installed copy with its bundled verifier, in a
folder [`tether.gui.update_handoff.check_install`][] accepts, on a platform that has an
installer for the offered stable release. Otherwise the notice keeps only the link. Pressing it
runs three steps, in this order and only in this order, each only when the step before it
succeeded:

1. [`tether.gui.update_download.download_installer`][] on a background thread, with a
   progress bar and **Cancel**;
2. [`tether.gui.update_verify.verify_update`][] on a background thread. *Refused* shows its
   reason with the link, and nothing is installed. *Unavailable* says nothing: the bar goes
   back to the notice with the link only;
3. on *verified*, a confirmation (*Tether will close, install X and start again*); **Install
   and restart** calls [`tether.gui.update_handoff.hand_off`][] on the GUI thread, which starts
   the helper and then quits Tether.

The consent is read again before each step, so turning the check off part way stops the
update. A download that does not finish says so in one line and keeps the link; the user
asked for it, so the user is told. The downloaded file is removed whatever the outcome: the
hand-off works on its own private copy.

**The result of the last update.** At the next start the notice reads the helper's report
([`tether.gui.update_handoff.read_update_result`][]), shows it once in the bar (*updated*,
*restored*, *unchanged* or *failed*, the last with the folder of the previous copy), writes it
to the message log, and removes the file. This happens whatever the consent answer is, since
it sends nothing.

Importing this module loads none of the download, the verifier or the hand-off; each is
imported when the notice first needs it, after the window is shown.
"""

from __future__ import annotations

import contextlib
import html
import logging
import shutil
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tether.gui import app_settings, messages
from tether.gui.update_check import RELEASES_PAGE, Version, check_for_update

__all__ = [
    "ACTION_OBJECT_NAME",
    "BAR_OBJECT_NAME",
    "NOTICE_OBJECT_NAME",
    "UpdateNotice",
    "install_update_notice",
    "report_text",
]

_LOG = logging.getLogger(__name__)

NOTICE_OBJECT_NAME = "tetherUpdateNotice"
"""The ``objectName`` of the [`UpdateNotice`][] object, a child of the main window."""

BAR_OBJECT_NAME = "tetherUpdateBar"
"""The ``objectName`` of the bar that carries the question or the notice."""

ACTION_OBJECT_NAME = "tetherUpdateCheckAction"
"""The ``objectName`` of **Help → Check for new releases at startup**."""

QUESTION_TEXT = (
    "May Tether check GitHub for a newer release each time it starts? It sends no data "
    "about you or your work; GitHub sees this computer's IP address."
)
"""The first-run question shown in the bar."""

_ACTION_TEXT = "Check for new releases at startup"
_ADMIN_TIP = "Turned off for every user of this computer by its administrator."

DOWNLOAD_FAILED_TEXT = (
    "The update could not be downloaded. Try again later, or download it from the Releases page."
)
"""The one line a download that did not finish gives."""

_REPORT_TEXT = {
    "updated": "Tether was updated to {version}.",
    "restored": (
        "The update to {version} did not install, so Tether put the previous copy back ({detail})."
    ),
    "unchanged": "The update to {version} did not start, and nothing was changed ({detail}).",
    "failed": (
        "The update to {version} failed, and Tether could not put the previous copy back "
        "({detail}). Install Tether again from the Releases page."
    ),
}


def report_text(status: str, version: str, detail: str) -> str:
    """Return the sentence for the helper's report of the last update (plain text).

    ``status`` is ``updated``, ``restored``, ``unchanged`` or ``failed``
    ([`tether.gui.update_handoff.UpdateReport`][]); ``detail`` is the helper's few words, which
    for *failed* name the folder that holds the previous copy.
    """
    template = _REPORT_TEXT.get(status, _REPORT_TEXT["unchanged"])
    return template.format(version=version, detail=detail or "no reason was recorded")


_notice_class: type | None = None


def _make_notice_class() -> type:
    """Build the QObject subclass lazily, so importing this module costs no Qt import."""
    from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

    from tether.gui import about

    class _UpdateNotice(QtCore.QObject):
        # Carries a Version or None from the check thread to the GUI thread (queued:
        # the receiver lives in the GUI thread, the emitter does not).
        _result = QtCore.Signal(object)
        # (job, received, total) from the download thread.
        _progress = QtCore.Signal(object, object, object)
        # (job, DownloadResult) and (job, VerificationResult), from their threads.
        _downloaded = QtCore.Signal(object, object)
        _verified = QtCore.Signal(object, object)

        def __init__(
            self,
            window: Any,
            *,
            help_menu: Any | None = None,
            check: Callable[[], Version | None] | None = None,
            settings_file: Path | None = None,
            override_file: Path | None = None,
            install_prefix: Path | None = None,
            gh: Path | None = None,
            quit_app: Callable[[], None] | None = None,
            can_install: Callable[[Version], bool] | None = None,
            download: Callable[..., Any] | None = None,
            verify: Callable[..., Any] | None = None,
            hand_off: Callable[..., Any] | None = None,
            result_file: Path | None = None,
        ) -> None:
            super().__init__(window)
            self.setObjectName(NOTICE_OBJECT_NAME)
            self._window = window
            self._check = check if check is not None else check_for_update
            self._settings_file = settings_file
            self._override_file = override_file
            self._install_prefix = install_prefix
            self._gh = gh
            self._quit_app = quit_app
            self._can_install = can_install
            self._download = download
            self._verify = verify
            self._hand_off = hand_off
            self._result_file = result_file
            self._started = False
            # What the bar carries: None, "question", "notice", "report", "downloading",
            # "verifying", "confirm", "starting", "closing" or "refused".
            self._mode: str | None = None
            # The answer given in this session, which holds even when it could not be
            # saved. The administrator override still wins over it.
            self._session_answer: bool | None = None
            self._shown_version: Version | None = None
            self._thread: threading.Thread | None = None
            # The install job: a number that only grows, so a result from a cancelled job
            # is recognised and dropped; the folder of its download; the verified record.
            self._job = 0
            self._job_thread: threading.Thread | None = None
            self._cancel = threading.Event()
            self._download_folder: Path | None = None
            self._installer: Any | None = None
            # Set for the rest of the session when verification could not run, so the
            # notice keeps only the link.
            self._install_off = False
            # What to show once the report of the last update is dismissed.
            self._after_report: Callable[[], None] | None = None
            self._report: str | None = None
            self._result.connect(self._on_result)
            self._progress.connect(self._on_progress)
            self._downloaded.connect(self._on_downloaded)
            self._verified.connect(self._on_verified)

            self._bar = QtWidgets.QToolBar("Update", window)
            self._bar.setObjectName(BAR_OBJECT_NAME)
            self._bar.setMovable(False)
            self._bar.setFloatable(False)
            # Not listed in the window's toolbar context menu: the bar shows only for a
            # question or a notice, and hides by its own buttons.
            self._bar.toggleViewAction().setVisible(False)
            self._label = QtWidgets.QLabel()
            self._label.setWordWrap(True)
            self._label.setTextFormat(QtCore.Qt.TextFormat.RichText)
            self._label.setOpenExternalLinks(False)
            self._label.linkActivated.connect(lambda _href: self.open_releases_page())
            self._label.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Preferred
            )
            self._bar.addWidget(self._label)
            self._progress_bar = QtWidgets.QProgressBar()
            self._progress_bar.setObjectName("tetherUpdateProgress")
            self._progress_bar.setAccessibleName("Update progress")
            self._progress_bar.setMinimumWidth(160)
            self._progress_action = self._bar.addWidget(self._progress_bar)

            def button(text: str, name: str, slot: Callable[[], None]) -> tuple[Any, Any]:
                widget = QtWidgets.QPushButton(text)
                widget.setObjectName(name)
                widget.clicked.connect(lambda: slot())
                return widget, self._bar.addWidget(widget)

            self._allow_button, self._allow_action = button(
                "Check for new releases", "tetherUpdateAllow", lambda: self.answer(True)
            )
            self._decline_button, self._decline_action = button(
                "Don't check", "tetherUpdateDecline", lambda: self.answer(False)
            )
            self._install_button, self._install_action = button(
                "Install update", "tetherUpdateInstall", self.install
            )
            self._confirm_button, self._confirm_action = button(
                "Install and restart", "tetherUpdateConfirm", self.confirm
            )
            self._cancel_button, self._cancel_action = button(
                "Cancel", "tetherUpdateCancel", self.cancel
            )
            self._dismiss_button, self._dismiss_action = button(
                "Dismiss", "tetherUpdateDismiss", self.dismiss
            )
            self._dismiss_button.setAccessibleName("Dismiss the newer-release notice")
            window.addToolBar(QtCore.Qt.ToolBarArea.TopToolBarArea, self._bar)
            self._bar.hide()

            self._action = QtGui.QAction(_ACTION_TEXT, self)
            self._action.setObjectName(ACTION_OBJECT_NAME)
            self._action.setCheckable(True)
            self._action.toggled.connect(self._on_toggled)
            if help_menu is not None:
                actions = help_menu.actions()
                # Before the separator that precedes About, so About stays last.
                anchor = next((a for a in reversed(actions) if a.isSeparator()), None)
                if anchor is not None:
                    help_menu.insertAction(anchor, self._action)
                else:
                    help_menu.addAction(self._action)
            self._sync_action()

        # --- state -------------------------------------------------------------

        def state(self) -> app_settings.UpdateCheckState:
            """Return the effective consent: the store, then this session's answer.

            The store is read now. The administrator override wins over everything; an
            answer given in this session wins over the stored one, so it holds even when
            saving it failed.
            """
            stored = app_settings.update_check_state(self._settings_file, self._override_file)
            if (
                self._session_answer is None
                or stored is app_settings.UpdateCheckState.DISABLED_BY_ADMINISTRATOR
            ):
                return stored
            if self._session_answer:
                return app_settings.UpdateCheckState.ALLOWED
            return app_settings.UpdateCheckState.DECLINED

        @property
        def bar(self) -> Any:
            """The bar that carries the question or the notice."""
            return self._bar

        @property
        def action(self) -> Any:
            """**Help → Check for new releases at startup**."""
            return self._action

        @property
        def label_text(self) -> str:
            """The bar's current text (rich text)."""
            return str(self._label.text())

        @property
        def mode(self) -> str | None:
            """What the bar carries now, or ``None`` when it is hidden.

            ``question``, ``notice``, ``report``, ``downloading``, ``verifying``,
            ``confirm``, ``starting`` (the hand-off runs), ``closing`` (Tether quits) or
            ``refused``.
            """
            return None if self._bar.isHidden() else self._mode

        @property
        def asking(self) -> bool:
            """Whether the bar is showing the first-run question."""
            return self._mode == "question" and not self._bar.isHidden()

        @property
        def notice_shown(self) -> bool:
            """Whether the bar is showing a newer-release notice."""
            return self._mode == "notice" and not self._bar.isHidden()

        @property
        def install_offered(self) -> bool:
            """Whether the notice shows **Install update**."""
            return self.notice_shown and self._install_action.isVisible()

        @property
        def shown_version(self) -> Version | None:
            """The newer version this run's notice named, or ``None``."""
            return self._shown_version

        @property
        def check_thread(self) -> threading.Thread | None:
            """The background thread of this run's check, once one was started."""
            return self._thread

        @property
        def install_thread(self) -> threading.Thread | None:
            """The background thread of the latest download or verification, if any."""
            return self._job_thread

        @property
        def report(self) -> str | None:
            """The sentence this run showed for the last update's report, or ``None``."""
            return self._report

        # --- actions -----------------------------------------------------------

        def start(self) -> None:
            """Show the last update's report, then ask, check or do nothing, by the answer."""
            self._show_report()
            state = self.state()
            self._sync_action(state)
            if state.prompt_pending:
                self._when_free(self._show_question)
            elif state.network_allowed:
                self._start_check()

        def answer(self, allowed: bool) -> None:
            """Store the user's answer to the question and act on it for this session."""
            self._session_answer = bool(allowed)
            self._store(self._session_answer)
            if self._mode == "question":
                self._mode = None
                self._bar.hide()
            state = self.state()
            self._sync_action(state)
            if state.network_allowed:
                self._start_check()
            elif self._mode in {"downloading", "verifying", "confirm"}:
                # Turning the check off part way stops the update.
                self.cancel()

        def dismiss(self) -> None:
            """Hide the bar for the rest of this session (its notice, refusal or report)."""
            self._bar.hide()
            if self._mode == "report":
                self._mode = None
                then, self._after_report = self._after_report, None
                if then is not None:
                    then()

        def open_releases_page(self) -> bool:
            """Open the fixed Releases page in the web browser."""
            return about.open_external(RELEASES_PAGE)

        def can_install(self) -> bool:
            """Whether **Install update** may be offered for the version the notice names."""
            version = self._shown_version
            if version is None or self._install_off or not self.state().network_allowed:
                return False
            if self._can_install is not None:
                return bool(self._can_install(version))
            return self._default_can_install(version)

        def install(self) -> None:
            """Download the offered installer on a background thread (**Install update**)."""
            if self._mode != "notice" or self._job_thread_alive() or not self.can_install():
                return
            version = self._shown_version
            assert version is not None  # can_install() checked it
            download = self._download
            if download is None:
                from tether.gui.update_download import download_installer as download
            self._job += 1
            job = self._job
            self._cancel = threading.Event()
            cancel = self._cancel
            self._show_progress("downloading", f"Downloading Tether {version}…")
            emit_progress, emit_done = self._progress.emit, self._downloaded.emit

            def run() -> None:
                def progress(received: int, total: int | None) -> None:
                    with contextlib.suppress(RuntimeError):
                        emit_progress(job, received, total)

                try:
                    result = download(version, progress=progress, cancelled=cancel.is_set)
                except Exception:  # noqa: BLE001 - reported as a failed download
                    _LOG.debug("update download failed", exc_info=True)
                    result = None
                with contextlib.suppress(RuntimeError):
                    emit_done(job, result)

            self._run_job(run, "tether-update-download")

        def confirm(self) -> None:
            """Hand the verified installer to the OS installer (**Install and restart**).

            Runs [`tether.gui.update_handoff.hand_off`][] on the GUI thread, as it must: it
            starts the helper, waits for it to report, and then quits Tether.
            """
            if self._mode != "confirm" or self._installer is None:
                return
            installer, self._installer = self._installer, None
            if not self.state().network_allowed:
                self._discard_download()
                self._show_notice()
                return
            hand_off = self._hand_off
            if hand_off is None:
                from tether.gui.update_handoff import hand_off
            self._show_progress("starting", "Starting the update…")
            self._cancel_action.setVisible(False)
            QtWidgets.QApplication.processEvents()
            try:
                result = hand_off(
                    installer,
                    prefix=self._install_prefix,
                    quit_app=self._quit,
                    settings_file=self._settings_file,
                    override_file=self._override_file,
                )
            finally:
                self._discard_download()
            outcome = getattr(getattr(result, "outcome", None), "value", None)
            reason = str(getattr(result, "reason", ""))
            if outcome == "handed-off":
                self._show_message("closing", reason, link=False, dismiss=False)
                return
            self._show_message("refused", reason, link=True)

        def cancel(self) -> None:
            """Stop the download, the verification or the confirmation; back to the notice."""
            if self._mode not in {"downloading", "verifying", "confirm"}:
                return
            self._cancel.set()
            self._job += 1  # a result still on its way belongs to no job now
            self._installer = None
            self._discard_download()
            self._show_notice()

        # --- internals ---------------------------------------------------------

        def _default_can_install(self, version: Version) -> bool:
            if self._install_prefix is None or self._gh is None:
                return False
            from tether.gui import update_handoff
            from tether.gui.update_download import download_url

            platform = update_handoff.handoff_platform()
            if platform is None or download_url(version) is None:
                return False
            try:
                update_handoff.check_install(Path(self._install_prefix), platform)
            except update_handoff.HandoffUnavailable as why:
                _LOG.debug("this copy cannot update itself: %s", why)
                return False
            except OSError:
                _LOG.debug("could not check the install folder", exc_info=True)
                return False
            return True

        def _quit(self) -> None:
            if self._quit_app is not None:
                self._quit_app()
                return
            self._window.close()
            app = QtWidgets.QApplication.instance()
            if app is not None:
                app.quit()

        def _store(self, allowed: bool) -> None:
            try:
                app_settings.record_update_consent(allowed, self._settings_file)
            except OSError as exc:
                # The session still follows the answer (self._session_answer); only
                # remembering it failed.
                _LOG.info("could not store the update-check answer: %s", exc)
                messages.post(
                    self._window, "Could not save the answer. It applies until Tether closes."
                )

        def _sync_action(self, state: app_settings.UpdateCheckState | None = None) -> None:
            state = state if state is not None else self.state()
            admin = state is app_settings.UpdateCheckState.DISABLED_BY_ADMINISTRATOR
            blocked = self._action.blockSignals(True)
            try:
                self._action.setChecked(state.network_allowed)
                self._action.setEnabled(not admin)
                self._action.setToolTip(_ADMIN_TIP if admin else "")
            finally:
                self._action.blockSignals(blocked)

        def _on_toggled(self, checked: bool) -> None:
            self.answer(bool(checked))

        def _when_free(self, show: Callable[[], None]) -> None:
            """Show now, or after the report of the last update is dismissed."""
            if self._mode == "report" and not self._bar.isHidden():
                self._after_report = show
            else:
                show()

        def _buttons(self, *visible: Any) -> None:
            for action in (
                self._allow_action,
                self._decline_action,
                self._install_action,
                self._confirm_action,
                self._cancel_action,
                self._dismiss_action,
                self._progress_action,
            ):
                action.setVisible(action in visible)

        def _show_question(self) -> None:
            self._mode = "question"
            self._label.setText(QUESTION_TEXT)
            self._buttons(self._allow_action, self._decline_action)
            self._bar.show()

        def _show_report(self) -> None:
            from tether.gui import update_handoff

            path = (
                self._result_file
                if self._result_file is not None
                else update_handoff.result_path(self._settings_file)
            )
            if not path.exists():
                return
            report = update_handoff.read_update_result(path)
            # Shown once: the file goes whether or not it could be read.
            with contextlib.suppress(OSError):
                path.unlink()
            if report is None:
                return
            text = report_text(report.status, report.version, report.detail)
            self._report = text
            level = messages.Level.INFO if report.status == "updated" else messages.Level.WARNING
            if report.status == "failed":
                level = messages.Level.ERROR
            messages.post(self._window, text, level)
            self._show_message("report", text, link=report.status == "failed")

        def _show_message(self, mode: str, text: str, *, link: bool, dismiss: bool = True) -> None:
            self._mode = mode
            body = html.escape(text)
            if link:
                body += f' <a href="{RELEASES_PAGE}">Open the Releases page</a>.'
            self._label.setText(body)
            self._buttons(*([self._dismiss_action] if dismiss else []))
            self._bar.show()

        def _show_progress(self, mode: str, text: str) -> None:
            self._mode = mode
            self._label.setText(html.escape(text))
            # A busy indicator until the first byte count arrives.
            self._progress_bar.setRange(0, 0)
            self._buttons(self._progress_action, self._cancel_action)
            self._bar.show()

        def _show_notice(self) -> None:
            version = self._shown_version
            if version is None:
                return
            self._mode = "notice"
            from tether import __version__

            if self.can_install():
                self._label.setText(
                    f"Tether {version} is available (you have {__version__}). Install it "
                    f'now, or <a href="{RELEASES_PAGE}">open the Releases page</a>.'
                )
                self._buttons(self._install_action, self._dismiss_action)
            else:
                self._label.setText(
                    f"Tether {version} is available (you have {__version__}). "
                    f'<a href="{RELEASES_PAGE}">Open the Releases page</a> to download it.'
                )
                self._buttons(self._dismiss_action)
            self._bar.show()

        def _start_check(self) -> None:
            if self._started:
                return
            self._started = True
            check, emit = self._check, self._result.emit

            def run() -> None:
                try:
                    result = check()
                except Exception:  # noqa: BLE001 - every failure is silent (ADR-0060)
                    _LOG.debug("update check failed", exc_info=True)
                    result = None
                # RuntimeError: the window closed while the check ran.
                with contextlib.suppress(RuntimeError):
                    emit(result)

            self._thread = threading.Thread(target=run, name="tether-update-check", daemon=True)
            self._thread.start()

        def _on_result(self, version: object) -> None:
            if not isinstance(version, Version) or self._shown_version is not None:
                return
            # The answer may have changed while the request ran: only an answer that
            # still allows the check may show its result.
            if not self.state().network_allowed:
                return
            self._shown_version = version
            self._when_free(self._show_notice)

        # --- the install job ---------------------------------------------------

        def _job_thread_alive(self) -> bool:
            return self._job_thread is not None and self._job_thread.is_alive()

        def _run_job(self, run: Callable[[], None], name: str) -> None:
            self._job_thread = threading.Thread(target=run, name=name, daemon=True)
            self._job_thread.start()

        def _discard_download(self) -> None:
            folder, self._download_folder = self._download_folder, None
            if folder is not None:
                shutil.rmtree(folder, ignore_errors=True)

        def _on_progress(self, job: object, received: object, total: object) -> None:
            if job != self._job or self._mode != "downloading":
                return
            if isinstance(total, int) and total > 0 and isinstance(received, int):
                self._progress_bar.setRange(0, 1000)
                self._progress_bar.setValue(min(1000, received * 1000 // total))

        def _on_downloaded(self, job: object, result: object) -> None:
            path = getattr(result, "path", None)
            if job != self._job or self._mode != "downloading":
                # Cancelled: the job's file is not wanted.
                if isinstance(path, Path):
                    shutil.rmtree(path.parent, ignore_errors=True)
                return
            outcome = getattr(getattr(result, "outcome", None), "value", None)
            if outcome != "downloaded" or not isinstance(path, Path):
                if outcome != "cancelled":
                    _LOG.debug("update download did not finish: %s", getattr(result, "reason", ""))
                    messages.post(self._window, DOWNLOAD_FAILED_TEXT, messages.Level.WARNING)
                self._show_notice()
                return
            self._download_folder = path.parent
            if not self.state().network_allowed:
                self._discard_download()
                self._show_notice()
                return
            self._start_verify(path)

        def _start_verify(self, path: Path) -> None:
            version = self._shown_version
            verify = self._verify
            if verify is None:
                from tether.gui.update_verify import verify_update as verify
            self._show_progress("verifying", f"Checking the download of Tether {version}…")
            job = self._job
            emit = self._verified.emit
            settings, override, gh = self._settings_file, self._override_file, self._gh

            def run() -> None:
                try:
                    result = verify(
                        path, version, settings_file=settings, override_file=override, gh=gh
                    )
                except Exception:  # noqa: BLE001 - verify_update never raises; refuse if so
                    _LOG.debug("update verification raised", exc_info=True)
                    result = None
                with contextlib.suppress(RuntimeError):
                    emit(job, result)

            self._run_job(run, "tether-update-verify")

        def _on_verified(self, job: object, result: object) -> None:
            if job != self._job or self._mode != "verifying":
                # Cancelled while the file was being verified: remove its folder again, in
                # case the first attempt met a file the verifier still held open.
                path = getattr(getattr(result, "installer", None), "path", None)
                if isinstance(path, Path):
                    shutil.rmtree(path.parent, ignore_errors=True)
                return
            outcome = getattr(getattr(result, "outcome", None), "value", None)
            if outcome == "verified" and self.state().network_allowed:
                self._installer = getattr(result, "installer", None)
                self._mode = "confirm"
                version = self._shown_version
                self._label.setText(
                    html.escape(
                        f"Tether will close, install {version} and start again. Save your "
                        "work first."
                    )
                )
                self._buttons(self._confirm_action, self._cancel_action)
                self._bar.show()
                return
            self._discard_download()
            if outcome == "unavailable" or outcome == "verified":
                # Could not run (or consent went away): say nothing, keep only the link.
                self._install_off = outcome == "unavailable"
                self._show_notice()
                return
            reason = str(getattr(result, "reason", "")) or "The update could not be verified."
            self._show_message("refused", reason, link=True)

    return _UpdateNotice


def UpdateNotice(window: Any, **kwargs: Any) -> Any:  # noqa: N802 - a lazy class constructor
    """Build the update notice for ``window`` (a ``QMainWindow``) without starting it.

    Keyword arguments: ``help_menu`` (where **Check for new releases at startup** goes),
    ``check`` (a callable returning a newer [`Version`][tether.gui.update_check.Version] or
    ``None``; defaults to [`check_for_update`][tether.gui.update_check.check_for_update]),
    and ``settings_file`` / ``override_file`` for the consent store (tests pass their own).

    For **Install update**: ``install_prefix`` (the install folder,
    `tether._verifier.install_prefix`) and ``gh`` (the bundled verifier,
    `tether._verifier.bundled_gh`), which the caller looks up because this GUI module does not
    import the core; without both, the notice offers only the link. ``quit_app`` is what the
    hand-off calls to quit (default: close the window and quit the application). For tests:
    ``can_install`` (a callable taking the offered version), ``download``, ``verify`` and
    ``hand_off`` (stand-ins for
    [`download_installer`][tether.gui.update_download.download_installer],
    [`verify_update`][tether.gui.update_verify.verify_update] and
    [`hand_off`][tether.gui.update_handoff.hand_off]), and ``result_file`` (the helper's
    report, by default [`result_path`][tether.gui.update_handoff.result_path]).
    The object is a child of ``window`` and lives as long as it.
    """
    global _notice_class
    if _notice_class is None:
        _notice_class = _make_notice_class()
    return _notice_class(window, **kwargs)


def install_update_notice(window: Any, *, help_menu: Any | None = None, **kwargs: Any) -> Any:
    """Build the notice and start it once the event loop runs; return the notice.

    The start is queued with a zero-delay timer, so building the window and showing it are
    never held up by the consent read, let alone by the network. The timer is bound to the
    notice, so a window deleted before the event loop runs takes the queued start with it.
    """
    from pyqtgraph.Qt import QtCore

    notice = UpdateNotice(window, help_menu=help_menu, **kwargs)
    QtCore.QTimer.singleShot(0, notice, notice.start)
    return notice
