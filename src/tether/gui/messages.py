# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The message log of the window (#627): the Messages panel, sticky warnings and the log file.

Every message the window gives goes through one [`MessageCenter`][]. It

* keeps the session's messages, each with its time and its [`Level`][], and shows them in the
  **Messages** panel ([`MessagesPanel`][]), where **Copy** puts them on the clipboard as text;
* shows the latest message in the status bar, except that a warning or an error stays there
  until the user presses **Dismiss** or the next warning or error arrives;
* writes the same lines to a rotating log file, ``tether.log`` in [`log_folder_path`][].
  The log holds the text of each message as the status bar shows it and nothing else: no
  trace data and no file contents. A path is in it only where a message shows that path.

A caller that names no level gets the one [`classify`][] reads from the text, so the window's
existing messages need no change: the message conventions of the shell (``"<action> failed:
<reason>"``, ``"<action> unavailable: <reason>"``) carry the level already.

Qt is imported only when a [`MessageCenter`][] or a [`MessagesPanel`][] is built, so importing
this module costs no Qt, matching the rest of [`tether.gui`][].
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import re
import weakref
from dataclasses import dataclass
from enum import StrEnum
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

__all__ = [
    "LOG_DIR_ENV",
    "LOG_FILE_NAME",
    "Level",
    "Message",
    "MessageCenter",
    "MessagesPanel",
    "classify",
    "log_folder_path",
    "post",
]

#: An environment variable that moves the log folder (the tests point it at a temporary one).
LOG_DIR_ENV = "TETHER_LOG_DIR"
#: The name of the log file in the log folder; its rotated copies add ``.1`` to ``.3``.
LOG_FILE_NAME = "tether.log"
_LOG_MAX_BYTES = 1_000_000
_LOG_BACKUPS = 3
# The panel keeps the most recent messages of the session; the log file keeps them all.
_HISTORY_LIMIT = 2000

# Words of the part before the first ": " that give a level (see classify).
_ERROR_MARKS = re.compile(r"\b(failed|could not)\b")
_WARNING_MARKS = re.compile(r"\b(unavailable|refused|not|became read-only|lost)\b")


class Level(StrEnum):
    """How serious a message is."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"

    @property
    def label(self) -> str:
        """The name the panel shows: ``Information``, ``Warning`` or ``Error``."""
        return {"info": "Information", "warning": "Warning", "error": "Error"}[self.value]


@dataclass(frozen=True)
class Message:
    """One message of the session.

    Attributes
    ----------
    time : datetime.datetime
        When it was given, in local time with its UTC offset.
    level : Level
        How serious it is.
    text : str
        The text, as the status bar shows it.
    """

    time: dt.datetime
    level: Level
    text: str

    def as_text(self) -> str:
        """One line for the clipboard and the log file: time, level, text."""
        stamp = self.time.isoformat(timespec="seconds")
        return f"{stamp} {self.level.value.upper()} {self.text}"


def classify(text: str) -> Level:
    """Return the level of a message that names none, read from the text before its reason.

    That part, up to the first ``": "``, names the action and what happened to it; the reason
    after it may quote any text (an exception, a file name), so it is not read. ``failed``
    and ``could not`` give [`Level.ERROR`][]; ``unavailable``, ``refused``, ``not`` (as in
    ``"Channel map not made"``), ``became read-only`` and ``lost`` give [`Level.WARNING`][],
    each as a whole word; anything else is
    [`Level.INFO`][]. A cancel the user asked for is information.

    Examples
    --------
    >>> from tether.gui.messages import classify
    >>> [classify(t).value for t in ("Open project failed: no such file", "Closed a.tether")]
    ['error', 'info']
    >>> classify("Idealize unavailable: wait for it to finish (it failed before)").value
    'warning'
    """
    head = text.split(": ", 1)[0].casefold()
    if _ERROR_MARKS.search(head):
        return Level.ERROR
    if _WARNING_MARKS.search(head):
        return Level.WARNING
    return Level.INFO


def log_folder_path() -> Path | None:
    """Return the folder of the log file: the per-user application data folder plus ``logs``.

    ``$TETHER_LOG_DIR`` replaces it when set. ``None`` when Qt names no such folder. The
    folder may not exist yet; the first message creates it.
    """
    override = os.environ.get(LOG_DIR_ENV)
    if override:
        return Path(override)
    from pyqtgraph.Qt import QtCore

    location = QtCore.QStandardPaths.writableLocation(
        QtCore.QStandardPaths.StandardLocation.AppLocalDataLocation
    )
    return Path(location) / "logs" if location else None


class _LogFile(RotatingFileHandler):
    """The rotating log file; a write that fails turns it off instead of printing to stderr."""

    failed = False

    def handleError(self, record: logging.LogRecord) -> None:  # noqa: N802 - logging override
        self.failed = True


# Each window's center, so code that holds only the window (the update notice) can post.
_CENTERS: weakref.WeakKeyDictionary[Any, MessageCenter] = weakref.WeakKeyDictionary()


def post(window: Any, text: str, level: Level | None = None) -> None:
    """Give ``text`` through the [`MessageCenter`][] of ``window``, else its status bar."""
    center = _CENTERS.get(window)
    if center is None:
        window.statusBar().showMessage(text)
    else:
        center.post(text, level)


class MessageCenter:
    """The messages of one window: history, status bar, sticky warnings and the log file.

    Build it on the main thread, after the ``QApplication``. It adds a **Dismiss** button to
    the status bar, shown while a warning or an error is held there.

    Parameters
    ----------
    window : QMainWindow
        The window whose status bar shows the messages.
    log_folder : pathlib.Path or None, optional
        The folder of the log file. The default is [`log_folder_path`][]; ``None`` there
        writes no log.
    """

    _DEFAULT = object()

    def __init__(self, window: Any, *, log_folder: Any = _DEFAULT) -> None:
        from pyqtgraph.Qt import QtWidgets

        self._window = window
        self._status_bar = window.statusBar()
        self._history: list[Message] = []
        self._sticky: Message | None = None
        self._listeners: list[Any] = []
        folder = log_folder_path() if log_folder is MessageCenter._DEFAULT else log_folder
        self._log_folder: Path | None = folder
        self._log: _LogFile | None = None
        self._dismiss = QtWidgets.QToolButton()
        self._dismiss.setText("Dismiss")
        self._dismiss.setAccessibleName("Dismiss the warning")
        self._dismiss.setToolTip("Dismiss the warning or error in the status bar")
        self._dismiss.clicked.connect(self.acknowledge)
        self._dismiss.setVisible(False)
        self._status_bar.addPermanentWidget(self._dismiss)
        # A menu's status tip clears the status bar when the pointer leaves an item; a held
        # warning comes back then, so opening a menu does not acknowledge it.
        self._status_bar.messageChanged.connect(self._on_message_changed)
        _CENTERS[window] = self

    # --- reading -------------------------------------------------------------

    @property
    def history(self) -> list[Message]:
        """The session's messages, oldest first (at most the latest 2000)."""
        return list(self._history)

    @property
    def latest(self) -> Message | None:
        """The latest message, else ``None``."""
        return self._history[-1] if self._history else None

    @property
    def sticky(self) -> Message | None:
        """The warning or error the status bar holds until it is dismissed, else ``None``."""
        return self._sticky

    @property
    def dismiss_button(self) -> Any:
        """The status-bar **Dismiss** button."""
        return self._dismiss

    @property
    def log_file(self) -> Path | None:
        """The log file, or ``None`` when no log is written."""
        return None if self._log_folder is None else self._log_folder / LOG_FILE_NAME

    def subscribe(self, listener: Any) -> None:
        """Call ``listener(message)`` for each new message (the panel does)."""
        self._listeners.append(listener)

    # --- writing -------------------------------------------------------------

    def post(self, text: str, level: Level | None = None) -> Message:
        """Keep, show and log one message.

        Parameters
        ----------
        text : str
            The message, as the status bar shows it.
        level : Level, optional
            Its level. The default is ``None``, the level [`classify`][] reads from ``text``.

        Returns
        -------
        Message
            The message as kept.
        """
        message = Message(dt.datetime.now().astimezone(), level or classify(text), text)
        self._history.append(message)
        del self._history[:-_HISTORY_LIMIT]
        if message.level is not Level.INFO:
            self._sticky = message
            self._dismiss.setVisible(True)
        self._status_bar.showMessage((self._sticky or message).text)
        self._write(message)
        for listener in list(self._listeners):
            listener(message)
        return message

    def acknowledge(self) -> None:
        """Release a held warning or error; the status bar shows the latest message again."""
        if self._sticky is None:
            return
        self._sticky = None
        self._dismiss.setVisible(False)
        latest = self.latest
        self._status_bar.showMessage("" if latest is None else latest.text)

    def close(self) -> None:
        """Close the log file. Messages posted later are still shown and kept."""
        _CENTERS.pop(self._window, None)
        if self._log is not None:
            self._log.close()
            self._log = None
        self._log_folder = None

    def _on_message_changed(self, text: str) -> None:
        if not text and self._sticky is not None:
            from pyqtgraph.Qt import QtCore

            QtCore.QTimer.singleShot(0, self._status_bar, self._show_sticky)

    def _show_sticky(self) -> None:
        if self._sticky is not None and not self._status_bar.currentMessage():
            self._status_bar.showMessage(self._sticky.text)

    def _write(self, message: Message) -> None:
        if self._log_folder is None:
            return
        if self._log is None:
            try:
                self._log_folder.mkdir(parents=True, exist_ok=True)
                self._log = _LogFile(
                    self._log_folder / LOG_FILE_NAME,
                    maxBytes=_LOG_MAX_BYTES,
                    backupCount=_LOG_BACKUPS,
                    encoding="utf-8",
                )
            except OSError:
                self._log_folder = None
                return
        record = logging.makeLogRecord({"msg": message.as_text(), "levelno": logging.INFO})
        self._log.handle(record)
        if self._log.failed:
            # A log that cannot be written (a full disk, another copy of Tether rotating the
            # same file) is turned off for the session; the panel still keeps every message.
            self.close()


class MessagesPanel:
    """The **Messages** panel: the session's messages with time and level, and **Copy**.

    **Copy** puts the selected messages on the clipboard, or every message when none is
    selected, one [`Message.as_text`][tether.gui.messages.Message.as_text] line each.

    Parameters
    ----------
    center : MessageCenter
        The messages to show; the panel follows the new ones.
    """

    def __init__(self, center: MessageCenter) -> None:
        from pyqtgraph.Qt import QtWidgets

        self._center = center
        self._messages: list[Message] = []
        self.widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(self.widget)
        layout.setContentsMargins(4, 4, 4, 4)
        self._table = QtWidgets.QTreeWidget()
        self._table.setAccessibleName("Messages")
        self._table.setRootIsDecorated(False)
        self._table.setUniformRowHeights(True)
        self._table.setHeaderLabels(["Time", "Level", "Message"])
        self._table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self._copy = QtWidgets.QPushButton("Copy")
        self._copy.setToolTip("Copy the selected messages, or all of them, as text")
        self._copy.clicked.connect(self.copy)
        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self._copy)
        layout.addWidget(self._table)
        layout.addLayout(buttons)
        for message in center.history:
            self._add(message)
        center.subscribe(self._add)

    @property
    def table(self) -> Any:
        """The ``QTreeWidget`` of messages, one row each: time, level, text."""
        return self._table

    @property
    def copy_button(self) -> Any:
        """The **Copy** button."""
        return self._copy

    def copied_text(self) -> str:
        """Return the text **Copy** puts on the clipboard now."""
        rows = sorted(self._table.indexOfTopLevelItem(i) for i in self._table.selectedItems())
        chosen = [self._messages[r] for r in rows] if rows else self._messages
        return "\n".join(message.as_text() for message in chosen)

    def copy(self) -> str:
        """Put [`copied_text`][tether.gui.messages.MessagesPanel.copied_text] on the clipboard."""
        from pyqtgraph.Qt import QtWidgets

        text = self.copied_text()
        QtWidgets.QApplication.clipboard().setText(text)
        return text

    def _add(self, message: Message) -> None:
        from pyqtgraph.Qt import QtWidgets

        # QTreeWidgetItem text is plain text, so a message that quotes user data (a molecule
        # key, a file name) is never read as markup.
        item = QtWidgets.QTreeWidgetItem(
            [message.time.strftime("%H:%M:%S"), message.level.label, message.text]
        )
        item.setToolTip(2, message.text)
        self._table.addTopLevelItem(item)
        self._messages.append(message)
        while len(self._messages) > _HISTORY_LIMIT:
            self._table.takeTopLevelItem(0)
            self._messages.pop(0)
        self._table.scrollToItem(item)
