# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The message log of the window (#627): panel, sticky warnings, log file, task progress."""

from __future__ import annotations

import importlib.util
import threading
from pathlib import Path

import pytest

from tether.gui import messages
from tether.gui.messages import Level, classify

pytest.importorskip("h5py")

_HAS_QT = all(importlib.util.find_spec(m) is not None for m in ("pyqtgraph", "PySide6"))
pytestmark = [
    pytest.mark.gui,
    pytest.mark.skipif(not _HAS_QT, reason="pyqtgraph/PySide6 not installed"),
]

# --- the level of a message -------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "level"),
    [
        ("Open project failed: [Errno 2] No such file", Level.ERROR),
        ("Could not open the log folder", Level.ERROR),
        ("Idealize unavailable: wait for idealization to finish", Level.WARNING),
        ("Drop refused: x.txt is not a .tether project file", Level.WARNING),
        ("Project became read-only: session lock lost", Level.WARNING),
        ("Curator name not changed: empty", Level.WARNING),
        ("Channel map not made: stopped", Level.WARNING),
        ("Notes saved", Level.INFO),  # a whole word only
        ("Opened a.tether — 3 molecule(s)", Level.INFO),
        ("Open project cancelled", Level.INFO),
        # Only the part before the reason gives the level: a reason may quote anything.
        ("Idealize: select a molecule first (the last one failed)", Level.INFO),
        ("Hand-off unavailable: the export failed", Level.WARNING),
        # A line with no reason names things whose names may hold any word.
        ("Opened failed.tether — 1 molecule(s)", Level.INFO),
        ("Molecule not-yet (2/3)", Level.INFO),
        ("Closed lost data.tether", Level.INFO),
        ("Could not open https://example.org/a", Level.ERROR),
    ],
)
def test_the_level_comes_from_the_action_not_the_reason(text, level) -> None:
    assert classify(text) is level


def test_the_log_folder_follows_the_override(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(messages.LOG_DIR_ENV, str(tmp_path / "here"))
    assert messages.log_folder_path() == tmp_path / "here"


# --- the shell ---------------------------------------------------------------------------


@pytest.fixture
def shell(qapp, qtbot):
    from tether.gui.shell import TetherShell

    s = TetherShell()
    qtbot.addWidget(s.window)
    yield s
    s.close()


def _rows(shell) -> list[tuple[str, str]]:
    table = shell.messages_panel.table
    return [
        (table.topLevelItem(i).text(1), table.topLevelItem(i).text(2))
        for i in range(table.topLevelItemCount())
    ]


def test_every_status_message_is_in_the_panel_with_time_and_level(shell) -> None:
    startup = shell.window.statusBar().currentMessage()
    shell._status("Closed a.tether")
    shell._status("Open project failed: no such file")
    rows = _rows(shell)
    assert rows[-2:] == [
        ("Information", "Closed a.tether"),
        ("Error", "Open project failed: no such file"),
    ]
    table = shell.messages_panel.table
    stamp = table.topLevelItem(table.topLevelItemCount() - 1).text(0)
    assert len(stamp) == 8 and stamp[2] == stamp[5] == ":"  # HH:MM:SS
    assert rows[0] == ("Information", startup)


def test_the_messages_dock_is_hidden_by_default_and_in_the_view_menu(shell) -> None:
    assert shell.messages_dock.isHidden()
    toggles = [action for action in shell.view_menu.actions() if action.text() == "&Messages"]
    assert toggles == [shell.messages_dock.toggleViewAction()]
    toggles[0].trigger()
    assert not shell.messages_dock.isHidden()
    # It belongs to the whole window: every section offers it and keeps it shown.
    for section in ("prepare", "curate", "analyze"):
        shell.set_section(section)
        assert not shell.messages_dock.isHidden()
        menu = shell.window.createPopupMenu()
        assert "&Messages" in [a.text() for a in menu.actions() if a.isVisible()]
        menu.deleteLater()
    shell.reset_layout()
    assert shell.messages_dock.isHidden()


def test_an_error_can_be_copied_as_text(shell, qapp) -> None:
    shell._status("Hand-off failed: disk full")
    table = shell.messages_panel.table
    table.topLevelItem(table.topLevelItemCount() - 1).setSelected(True)
    shell.messages_panel.copy_button.click()
    copied = qapp.clipboard().text()
    assert copied.endswith(" ERROR Hand-off failed: disk full")
    assert "\n" not in copied  # only the selected row
    table.clearSelection()
    assert len(shell.messages_panel.copy().splitlines()) == table.topLevelItemCount()


def test_markup_in_a_message_is_shown_as_typed(shell) -> None:
    shell._status("Closed <b>a</b>.tether")
    assert _rows(shell)[-1][1] == "Closed <b>a</b>.tether"


def test_a_warning_survives_a_later_information_line(shell) -> None:
    bar = shell.window.statusBar()
    shell._status("Idealize unavailable: wait for idealization to finish")
    shell._status("Molecule mol-1 (2/3)")
    assert bar.currentMessage() == "Idealize unavailable: wait for idealization to finish"
    # The latest message is still the information line, and the panel has both.
    assert shell.status_message == "Molecule mol-1 (2/3)"
    assert _rows(shell)[-1] == ("Information", "Molecule mol-1 (2/3)")
    assert not shell.messages.dismiss_button.isHidden()
    # The next warning replaces it.
    shell._status("Open project failed: no such file")
    shell._status("Molecule mol-2 (3/3)")
    assert bar.currentMessage() == "Open project failed: no such file"
    # Dismiss releases it, and the bar shows the latest line again.
    shell.messages.dismiss_button.click()
    assert bar.currentMessage() == "Molecule mol-2 (3/3)"
    assert shell.messages.dismiss_button.isHidden()
    shell._status("Molecule mol-1 (2/3)")
    assert bar.currentMessage() == "Molecule mol-1 (2/3)"


def test_space_on_the_focused_dismiss_button_dismisses_and_does_not_curate(shell, qtbot) -> None:
    # The curation keys bind Space to accept; a focused push button keeps it (#832).
    from pyqtgraph.Qt import QtCore

    shell.set_section("curate")
    shell._status("Drop refused: drop one .tether project file at a time")
    button = shell.messages.dismiss_button
    qtbot.keyClick(button, QtCore.Qt.Key.Key_Space)
    assert shell.messages.sticky is None and button.isHidden()


def test_a_cleared_status_bar_shows_the_held_warning_again(shell, qtbot) -> None:
    # A menu's status tip clears the bar when the pointer leaves an item.
    bar = shell.window.statusBar()
    shell._status("Drop refused: drop one .tether project file at a time")
    bar.clearMessage()
    qtbot.waitUntil(lambda: bar.currentMessage() != "", timeout=2000)
    assert bar.currentMessage() == "Drop refused: drop one .tether project file at a time"


def test_an_explicit_level_wins_over_the_text(shell) -> None:
    shell._status("Something odd", Level.WARNING)
    assert shell.messages.sticky.text == "Something odd"


def test_the_messages_go_to_a_rotating_log_file(shell, monkeypatch) -> None:
    log = shell.messages.log_file
    assert log is not None and log.parent == messages.log_folder_path()
    shell._status("Opened <C:\\data\\a.tether> — 3 molecule(s)")
    shell._status("Idealize failed for m0: boom")
    lines = log.read_text(encoding="utf-8").splitlines()
    assert lines[-2].endswith(" INFO Opened <C:\\data\\a.tether> — 3 molecule(s)")
    assert lines[-1].endswith(" ERROR Idealize failed for m0: boom")
    # Help -> Open log folder shows now that the folder exists.
    from tether.gui import about

    assert about.log_folder() == log.parent
    shell.help_menu.aboutToShow.emit()
    assert shell._act_log_folder.isVisible()
    # The handler rotates: past its size the file moves to tether.log.1.
    monkeypatch.setattr(shell.messages._log, "maxBytes", 200)
    for index in range(10):
        shell._status(f"Molecule mol-{index} (1/10)")
    assert (log.parent / f"{messages.LOG_FILE_NAME}.1").is_file()
    assert log.stat().st_size <= 400


def test_a_log_that_cannot_be_written_does_not_stop_the_messages(qapp, qtbot, tmp_path) -> None:
    from pyqtgraph.Qt import QtWidgets

    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x", encoding="utf-8")
    window = QtWidgets.QMainWindow()
    qtbot.addWidget(window)
    center = messages.MessageCenter(window, log_folder=blocker / "logs")
    center.post("Closed a.tether")
    assert center.log_file is None
    assert [m.text for m in center.history] == ["Closed a.tether"]
    messages.post(window, "Could not save the answer.")
    assert center.sticky is not None and center.sticky.level is Level.ERROR
    center.close()
    # After close the window's status bar still takes a message.
    messages.post(window, "Closed b.tether")
    assert window.statusBar().currentMessage() == "Closed b.tether"


# --- progress and cancel of a background task ---------------------------------------------


def test_a_running_task_shows_its_name_progress_and_cancel(shell, qtbot) -> None:
    from tether.gui.tasks import TaskCancelled, TaskState

    busy = shell.busy_indicator
    reported = threading.Event()
    outcomes = []

    def work(*, cancel, progress):
        progress.report(1, 4)
        reported.set()
        while not cancel.cancelled:
            threading.Event().wait(0.01)
        raise TaskCancelled

    task = shell._tasks.start(
        work,
        label="Exporting 4 molecules",
        on_finished=outcomes.append,
        cancellable=True,
        reports_progress=True,
    )
    assert task is not None
    try:
        assert busy.label.text() == "Exporting 4 molecules"
        assert not busy.cancel_button.isHidden()
        reported.wait(timeout=5)
        qtbot.waitUntil(lambda: not busy.progress_row.isHidden(), timeout=3000)
        assert busy.progress_row.value() == 25 and busy.mark.isHidden()
        busy.cancel_button.click()
        assert not busy.cancel_button.isEnabled()
        qtbot.waitUntil(lambda: bool(outcomes), timeout=5000)
    finally:
        task.cancel()  # a failed assertion above must not leave the worker looping
    assert outcomes[0].state is TaskState.CANCELLED
    assert not busy.is_running() and busy.cancel_button.isHidden()
    assert busy.progress_row.isHidden() and not busy.mark.isHidden()


def test_a_task_that_cannot_be_cancelled_offers_no_cancel(shell, qtbot) -> None:
    gate = threading.Event()
    outcomes = []
    shell._tasks.start(gate.wait, 5, label="Idealizing mol-0", on_finished=outcomes.append)
    assert shell.busy_indicator.is_running()
    assert shell.busy_indicator.cancel_button.isHidden()
    assert shell._tasks.cancel() is False
    gate.set()
    qtbot.waitUntil(lambda: bool(outcomes), timeout=5000)


def test_a_log_that_fails_later_keeps_the_window_routed(qapp, qtbot, tmp_path, monkeypatch) -> None:
    # Codex review on PR #1274: a write that fails after the log opened (a full disk) turns
    # the log off, and code that holds only the window still reaches the panel and the
    # sticky status line.
    from pyqtgraph.Qt import QtWidgets

    window = QtWidgets.QMainWindow()
    qtbot.addWidget(window)
    center = messages.MessageCenter(window, log_folder=tmp_path / "logs")
    center.post("Closed a.tether")
    assert center.log_file is not None

    def disk_full(_record):
        raise OSError("No space left on device")

    monkeypatch.setattr(center._log, "shouldRollover", disk_full)
    center.post("Closed b.tether")
    assert center.log_file is None
    messages.post(window, "Could not save the answer.")
    assert center.latest.text == "Could not save the answer."
    assert center.sticky is not None and center.sticky.level is Level.ERROR
    center.close()


def test_a_log_whose_close_fails_too_still_delivers_the_message(qapp, qtbot, tmp_path) -> None:
    # Codex review on PR #1274: on a full disk the flush inside close() fails again; that
    # must not escape post() or keep the message from the panel.
    from pyqtgraph.Qt import QtWidgets

    class FullDisk:
        def write(self, _text):
            return None

        def flush(self):
            raise OSError("No space left on device")

        def close(self):
            self.flush()

    window = QtWidgets.QMainWindow()
    qtbot.addWidget(window)
    center = messages.MessageCenter(window, log_folder=tmp_path / "logs")
    center.post("Closed a.tether")
    center._log.stream = FullDisk()
    received = []
    center.subscribe(received.append)
    center.post("Closed b.tether")
    assert center.log_file is None
    assert [m.text for m in received] == ["Closed b.tether"]
    center.close()
