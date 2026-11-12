# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The shell's desktop basics (#629): shortcuts, Close project and Quit, the Help menu
with its About box, opening a dropped ``.tether`` file, the start page, and the
``Import`` menu that used to be named ``Legacy``.

All ``@pytest.mark.gui`` and headless: they build a real :class:`TetherShell` on the
pytest-qt ``QApplication``. Opening a browser or a file manager goes through
:func:`tether.gui.about.open_external`, and quitting through the shell module's
``_quit_application``; the tests replace both, so nothing leaves the test process.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

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
def opened(monkeypatch):
    """Record every URL the shell would open, instead of opening it."""
    from tether.gui import about

    urls: list[str] = []
    monkeypatch.setattr(about, "open_external", lambda url: urls.append(url) or True)
    return urls


def _store(tmp_path, *, name="basics.tether", n=2):
    from _analysis_store import build_store_with_channels

    rng = np.random.default_rng(0)
    donor = rng.uniform(400.0, 800.0, size=(n, 10))
    acceptor = rng.uniform(200.0, 600.0, size=(n, 10))
    project, _keys = build_store_with_channels(tmp_path, donor, acceptor, name=name)
    return project


def _action(menu, text):
    return next(a for a in menu.actions() if a.text() == text)


def _menu_actions(window):
    # Walk the menus as children of the window: calling QAction.menu() on a menu-bar
    # action makes PySide6 drop the Python owner of the menu's actions.
    from pyqtgraph.Qt import QtWidgets

    for menu in window.findChildren(QtWidgets.QMenu):
        yield from menu.actions()


def _mime(paths):
    from pyqtgraph.Qt import QtCore

    mime = QtCore.QMimeData()
    mime.setUrls([QtCore.QUrl.fromLocalFile(str(p)) for p in paths])
    return mime


def _drag_and_drop(qapp, window, paths):
    """Send a drag-enter then a drop, as a real drag does.

    Qt delivers a drop to the widget that accepted the drag-enter, so a bare drop
    event reaches nothing.
    """
    from pyqtgraph.Qt import QtCore, QtGui

    mime = _mime(paths)
    args = (
        QtCore.Qt.DropAction.CopyAction,
        mime,
        QtCore.Qt.MouseButton.NoButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
    )
    enter = QtGui.QDragEnterEvent(QtCore.QPoint(10, 10), *args)
    enter.ignore()
    qapp.sendEvent(window, enter)
    drop = QtGui.QDropEvent(QtCore.QPointF(10, 10), *args)
    drop.ignore()
    qapp.sendEvent(window, drop)
    return enter, drop


# --- shortcuts ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "standard", "fallback"),
    [
        ("&Open project…", "Open", "Ctrl+O"),
        ("&Close project", "Close", "Ctrl+W"),
        ("&Quit", "Quit", "Ctrl+Q"),
    ],
)
def test_file_actions_carry_the_platform_standard_keys(shell, text, standard, fallback) -> None:
    from pyqtgraph.Qt import QtGui

    std = QtGui.QKeySequence.keyBindings(getattr(QtGui.QKeySequence.StandardKey, standard))
    shortcuts = [s.toString() for s in _action(shell.file_menu, text).shortcuts()]
    # The platform's own keys first, then the common chord when the table lacks it
    # (Quit: none on Windows, only the Exit key on a generic Linux desktop).
    assert shortcuts[: len(std)] == [s.toString() for s in std]
    assert QtGui.QKeySequence(fallback).toString() in shortcuts
    assert len(shortcuts) == len(set(shortcuts))


def test_documentation_has_the_platform_help_key(shell) -> None:
    from pyqtgraph.Qt import QtGui

    std = QtGui.QKeySequence.keyBindings(QtGui.QKeySequence.StandardKey.HelpContents)
    shortcuts = [s.toString() for s in _action(shell.help_menu, "&Documentation").shortcuts()]
    assert shortcuts[: len(std)] == [s.toString() for s in std]
    assert "F1" in shortcuts


def test_no_menu_shortcut_shadows_a_curation_or_reserved_key(shell) -> None:
    # Every chord of every menu action, against the whole default keymap — which
    # includes the reserved tMAVEN C/V no-ops (PRD §7.3).
    bound = set(shell.keymap.bindings)
    checked = 0
    for action in _menu_actions(shell.window):
        for sequence in action.shortcuts():
            for i in range(sequence.count()):
                combo = sequence[i]
                chord = (int(combo.key().value), int(combo.keyboardModifiers().value))
                assert chord not in bound, (action.text(), sequence.toString())
                checked += 1
    assert checked >= 4


def _activate(shell, qtbot):
    from pyqtgraph.Qt import QtWidgets

    shell.show()
    qtbot.waitExposed(shell.window)
    shell.window.activateWindow()
    try:
        qtbot.waitUntil(lambda: QtWidgets.QApplication.activeWindow() is shell.window, timeout=2000)
    except AssertionError:
        pytest.skip("the platform plugin did not activate the window")


def _press(qtbot, widget, action):
    combo = action.shortcuts()[0][0]
    qtbot.keyClick(widget, combo.key(), combo.keyboardModifiers())


def test_the_help_key_works_with_focus_in_the_molecule_list(shell, qtbot, opened) -> None:
    from tether.gui import about

    _activate(shell, qtbot)
    shell.molecule_list.setFocus()
    _press(qtbot, shell.molecule_list, _action(shell.help_menu, "&Documentation"))
    assert opened == [about.docs_url()]


@pytest.mark.parametrize("surface", ["molecule_list", "movie_switcher"])
def test_the_close_key_works_with_focus_outside_a_text_field(
    shell, qtbot, tmp_path, surface
) -> None:
    project = _store(tmp_path)
    shell.load_project(project.path)
    _activate(shell, qtbot)
    widget = getattr(shell, surface)
    widget.setFocus()
    _press(qtbot, widget, _action(shell.file_menu, "&Close project"))
    assert shell.project_path is None
    assert project.lock_owner() is None


# --- Close project and Quit ----------------------------------------------------


def test_close_project_releases_the_lock_and_shows_the_start_page(shell, tmp_path) -> None:
    project = _store(tmp_path)
    assert shell.load_project(project.path) is not None
    close = _action(shell.file_menu, "&Close project")
    assert close.isEnabled()
    assert project.lock_owner() is not None
    assert not shell.start_page_visible

    close.trigger()

    assert shell.project_path is None
    assert project.lock_owner() is None
    assert shell.molecule_list.count() == 0
    assert shell.start_page_visible
    assert not close.isEnabled()
    assert "Closed basics.tether" in shell.status_message


def test_a_closed_project_opens_writable_in_another_shell(qapp, qtbot, shell, tmp_path) -> None:
    from tether.gui.shell import TetherShell

    project = _store(tmp_path)
    shell.load_project(project.path)
    shell.close_project()
    other = TetherShell()
    qtbot.addWidget(other.window)
    try:
        assert other.load_project(project.path) is not None
        assert "read-only" not in other.status_message
    finally:
        other.close()


def test_close_project_without_a_project_reports_it(shell) -> None:
    assert shell.close_project() is False
    assert "no project is open" in shell.status_message


def test_quit_releases_the_lock_and_quits(shell, tmp_path, monkeypatch) -> None:
    from tether.gui import shell as shell_module

    quits: list[bool] = []
    monkeypatch.setattr(shell_module, "_quit_application", lambda: quits.append(True))
    project = _store(tmp_path)
    shell.load_project(project.path)

    _action(shell.file_menu, "&Quit").trigger()

    assert quits == [True]
    assert project.lock_owner() is None


# --- Help menu and About -------------------------------------------------------


def test_help_menu_lists_its_actions(shell) -> None:
    labels = [a.text() for a in shell.help_menu.actions() if not a.isSeparator()]
    assert labels == [
        "&Documentation",
        "Keyboard &shortcuts…",
        "Open &log folder",
        "&About Tether",
    ]


def test_documentation_opens_the_installed_versions_docs(shell, opened) -> None:
    from tether.gui import about

    assert shell.open_documentation() is True
    assert opened == [about.docs_url()]


def test_keyboard_shortcuts_opens_the_cheat_sheet(shell) -> None:
    _action(shell.help_menu, "Keyboard &shortcuts…").trigger()
    assert shell.cheatsheet.dialog.isVisible()
    shell.cheatsheet.dialog.close()


def test_log_folder_shows_only_when_the_folder_exists(shell, tmp_path, monkeypatch, opened) -> None:
    from tether.gui import about

    action = _action(shell.help_menu, "Open &log folder")
    monkeypatch.setattr(about, "log_folder", lambda: None)
    shell.help_menu.aboutToShow.emit()
    assert not action.isVisible()
    assert shell.open_log_folder() is False

    monkeypatch.setattr(about, "log_folder", lambda: tmp_path)
    shell.help_menu.aboutToShow.emit()
    assert action.isVisible()
    assert shell.open_log_folder() is True
    assert opened and opened[0].startswith("file:")


def test_about_shows_the_version_and_copies_it(shell) -> None:
    from pyqtgraph.Qt import QtWidgets

    from tether import __version__

    about_box = shell.show_about()
    rows = dict(about_box.rows)
    assert rows["Version"] == __version__
    assert {"Commit", "Base stack", "Sidecar stack", "Deep stack"} <= set(rows)
    about_box.copy_button.click()
    copied = QtWidgets.QApplication.clipboard().text()
    assert f"Version: {__version__}" in copied
    assert copied == about_box.text()
    about_box.dialog.close()


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("1.0.0", "https://bioedca.github.io/tether/1.0/"),
        ("1.2.3", "https://bioedca.github.io/tether/1.2/"),
        ("1.0.0rc4", "https://bioedca.github.io/tether/latest/"),
        ("1.0.0rc4.dev19+g4d1aaef61", "https://bioedca.github.io/tether/latest/"),
        ("0.0.0+unknown", "https://bioedca.github.io/tether/latest/"),
    ],
)
def test_docs_url_follows_the_published_labels(version, expected) -> None:
    from tether.gui.about import docs_url

    assert docs_url(version) == expected
    assert docs_url(version, "tutorial/") == f"{expected}tutorial/"


def test_build_commit_reads_the_recorded_or_local_commit() -> None:
    from tether.gui.about import build_commit

    assert build_commit("1.0.0", "abc1234def") == "abc1234def"
    assert build_commit("1.0.0rc4.dev19+g4d1aaef61") == "4d1aaef61"
    assert build_commit("1.0.0rc4.dev19+g4d1aaef61.d20261112") == "4d1aaef61"
    assert build_commit("1.0.0") is None


def test_sidecar_line_reads_the_sidecar_env_without_a_path(tmp_path, monkeypatch) -> None:
    from tether.gui import about

    prefix = tmp_path / "sidecar"
    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin" / "python").write_text("")
    meta = prefix / "conda-meta"
    meta.mkdir()
    for record in ("python-3.11.9-h_0_cpython", "numpy-1.26.4-py311_0", "pyqt-5.15.11-py311_2"):
        (meta / f"{record}.json").write_text("{}")
    (prefix / "lib" / "python3.11" / "site-packages" / "tmaven-0.3.0.dist-info").mkdir(parents=True)
    monkeypatch.setenv("TETHER_SIDECAR_PYTHON", str(prefix / "bin" / "python"))

    line = dict(about.about_rows())["Sidecar stack"]

    assert line == "Python 3.11.9, NumPy 1.26.4, PyQt5 5.15.11, tMAVEN 0.3.0"
    assert str(tmp_path) not in about.about_text()


def test_sidecar_line_says_when_no_sidecar_is_found(tmp_path, monkeypatch) -> None:
    from tether.gui import about
    from tether.idealize import driver

    monkeypatch.setenv("TETHER_SIDECAR_PYTHON", str(tmp_path / "missing" / "python"))
    assert dict(about.about_rows())["Sidecar stack"].startswith("not found")
    monkeypatch.delenv("TETHER_SIDECAR_PYTHON")
    monkeypatch.setattr(driver, "bundled_sidecar_python", lambda: None)
    assert dict(about.about_rows())["Sidecar stack"].startswith("not found")


# --- drop to open ----------------------------------------------------------------


def test_a_dropped_tether_file_opens(shell, tmp_path, qapp) -> None:
    project = _store(tmp_path)
    enter, drop = _drag_and_drop(qapp, shell.window, [project.path])
    assert enter.isAccepted()
    assert drop.isAccepted()
    assert shell.project_path == Path(project.path)
    assert shell.molecule_list.count() == 2


def test_a_dropped_file_of_another_type_is_refused(shell, tmp_path, qapp) -> None:
    other = tmp_path / "movie.tif"
    other.write_bytes(b"")
    _drag_and_drop(qapp, shell.window, [other])
    assert shell.project_path is None
    assert shell.status_message == "Drop refused: movie.tif is not a .tether project file"


def test_dropping_two_projects_opens_neither(shell, tmp_path) -> None:
    first = _store(tmp_path, name="a.tether")
    (tmp_path / "b").mkdir()
    second = _store(tmp_path / "b", name="b.tether")
    assert shell.open_dropped([Path(first.path), Path(second.path)]) is None
    assert shell.project_path is None
    assert "one .tether project file at a time" in shell.status_message


# --- start page ----------------------------------------------------------------


def test_the_start_page_shows_until_something_is_loaded(shell) -> None:
    from tether.gui.trace_dock import TraceView

    assert shell.start_page_visible
    trace = TraceView(donor=np.ones(10), acceptor=np.ones(10), frame_time=0.1, name="m")
    shell.set_molecules([trace])
    assert not shell.start_page_visible
    shell.set_molecules([])
    assert shell.start_page_visible


def test_the_start_page_opens_a_project_and_the_tutorial(shell, opened) -> None:
    from tether.gui import about

    calls: list[bool] = []
    shell._open_project_dialog = lambda: calls.append(True)
    shell.start_page.open_button.click()
    assert calls == [True]
    shell.start_page.tutorial_button.click()
    assert opened == [about.docs_url(page="tutorial/")]


# --- the Import menu ---------------------------------------------------------------


def test_no_menu_is_named_legacy(shell) -> None:
    titles = [a.text().replace("&", "") for a in shell.window.menuBar().actions()]
    assert "Legacy" not in titles
    assert "Import" in titles
    assert shell.legacy_menu.title() == "&Import"
