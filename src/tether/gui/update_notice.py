# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The opt-in update notice in the shell: the first-run question and the "newer release" bar (#248).

**Off until the user says yes.** At startup `UpdateNotice.start` reads the consent answer
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

**Never on the GUI thread, never in the way.** `UpdateNotice.start` reads one small file
and, when allowed, starts a daemon thread; it returns at once. The thread runs
[`tether.gui.update_check.check_for_update`][] and hands the result back to the GUI thread
through a queued Qt signal. Whatever goes wrong there, the user sees nothing: no dialog, no
status message, no bar. A slow or dead network therefore cannot delay the window, and a
thread still waiting when the application quits does not keep it alive.

**One notice per run.** A newer release produces exactly one bar; dismissing it hides it for
the rest of the session. It is not remembered: the store holds only the consent answer
(ADR-0095), so the next start asks GitHub again and shows the bar again while the installed
copy is still older.

**What the link opens.** Always [`tether.gui.update_check.RELEASES_PAGE`][], never a URL from
GitHub's answer; the only text of the answer the bar shows is the version, re-spelled from
its parsed form.
"""

from __future__ import annotations

import contextlib
import logging
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

_notice_class: type | None = None


def _make_notice_class() -> type:
    """Build the QObject subclass lazily, so importing this module costs no Qt import."""
    from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

    from tether.gui import about

    class _UpdateNotice(QtCore.QObject):
        # Carries a Version or None from the check thread to the GUI thread (queued:
        # the receiver lives in the GUI thread, the emitter does not).
        _result = QtCore.Signal(object)

        def __init__(
            self,
            window: Any,
            *,
            help_menu: Any | None = None,
            check: Callable[[], Version | None] | None = None,
            settings_file: Path | None = None,
            override_file: Path | None = None,
        ) -> None:
            super().__init__(window)
            self.setObjectName(NOTICE_OBJECT_NAME)
            self._window = window
            self._check = check if check is not None else check_for_update
            self._settings_file = settings_file
            self._override_file = override_file
            self._started = False
            # What the bar carries: None, "question" or "notice".
            self._mode: str | None = None
            # The answer given in this session, which holds even when it could not be
            # saved. The administrator override still wins over it.
            self._session_answer: bool | None = None
            self._shown_version: Version | None = None
            self._thread: threading.Thread | None = None
            self._result.connect(self._on_result)

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
            self._allow_button = QtWidgets.QPushButton("Check for new releases")
            self._allow_button.setObjectName("tetherUpdateAllow")
            self._allow_button.clicked.connect(lambda: self.answer(True))
            self._decline_button = QtWidgets.QPushButton("Don't check")
            self._decline_button.setObjectName("tetherUpdateDecline")
            self._decline_button.clicked.connect(lambda: self.answer(False))
            self._dismiss_button = QtWidgets.QPushButton("Dismiss")
            self._dismiss_button.setObjectName("tetherUpdateDismiss")
            self._dismiss_button.setAccessibleName("Dismiss the newer-release notice")
            self._dismiss_button.clicked.connect(self.dismiss)
            self._allow_action = self._bar.addWidget(self._allow_button)
            self._decline_action = self._bar.addWidget(self._decline_button)
            self._dismiss_action = self._bar.addWidget(self._dismiss_button)
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
        def asking(self) -> bool:
            """Whether the bar is showing the first-run question."""
            return self._mode == "question" and not self._bar.isHidden()

        @property
        def notice_shown(self) -> bool:
            """Whether the bar is showing a newer-release notice."""
            return self._mode == "notice" and not self._bar.isHidden()

        @property
        def shown_version(self) -> Version | None:
            """The newer version this run's notice named, or ``None``."""
            return self._shown_version

        @property
        def check_thread(self) -> threading.Thread | None:
            """The background thread of this run's check, once one was started."""
            return self._thread

        # --- actions -----------------------------------------------------------

        def start(self) -> None:
            """Ask the question, start the check, or do nothing, by the stored answer."""
            state = self.state()
            self._sync_action(state)
            if state.prompt_pending:
                self._show_question()
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

        def dismiss(self) -> None:
            """Hide the notice for the rest of this session."""
            self._bar.hide()

        def open_releases_page(self) -> bool:
            """Open the fixed Releases page in the web browser."""
            return about.open_external(RELEASES_PAGE)

        # --- internals ---------------------------------------------------------

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

        def _show_question(self) -> None:
            self._mode = "question"
            self._label.setText(QUESTION_TEXT)
            self._allow_action.setVisible(True)
            self._decline_action.setVisible(True)
            self._dismiss_action.setVisible(False)
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
            self._mode = "notice"
            from tether import __version__

            self._label.setText(
                f"Tether {version} is available (you have {__version__}). "
                f'<a href="{RELEASES_PAGE}">Open the Releases page</a> to download it.'
            )
            self._allow_action.setVisible(False)
            self._decline_action.setVisible(False)
            self._dismiss_action.setVisible(True)
            self._bar.show()

    return _UpdateNotice


def UpdateNotice(window: Any, **kwargs: Any) -> Any:  # noqa: N802 - a lazy class constructor
    """Build the update notice for ``window`` (a ``QMainWindow``) without starting it.

    Keyword arguments: ``help_menu`` (where **Check for new releases at startup** goes),
    ``check`` (a callable returning a newer [`Version`][tether.gui.update_check.Version] or
    ``None``; defaults to [`check_for_update`][tether.gui.update_check.check_for_update]),
    and ``settings_file`` / ``override_file`` for the consent store (tests pass their own).
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
