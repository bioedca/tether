# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the Tether shell host + curation dialogs (M2 S2, PRD §7.3).

All ``@pytest.mark.gui``: they construct a real :class:`TetherShell` on the
pytest-qt ``QApplication`` and assert the shell wires the focus contract (the
event filter is installed and reaches the controller from a child surface), the
editable category field is exempt, molecule navigation walks the list, and the
overflow picker + cheat-sheet dialogs behave. Pixel rendering is left to the live
computer-use smoke; these assert wiring/behaviour only.
"""

from __future__ import annotations

import importlib.util

import numpy as np
import pytest

_HAS_QT = all(importlib.util.find_spec(m) is not None for m in ("pyqtgraph", "PySide6"))
_needs_qt = pytest.mark.skipif(not _HAS_QT, reason="pyqtgraph/PySide6 not installed")

pytestmark = [pytest.mark.gui, _needs_qt]


def _key_event(key, *, press=True):
    from pyqtgraph.Qt import QtCore, QtGui

    etype = QtCore.QEvent.Type.KeyPress if press else QtCore.QEvent.Type.KeyRelease
    return QtGui.QKeyEvent(etype, int(key), QtCore.Qt.KeyboardModifier.NoModifier, "")


def _traces(n=4):
    from tether.gui.trace_dock import TraceView

    out = []
    for i in range(n):
        donor = np.linspace(100.0, 50.0, 20) + i
        acceptor = np.linspace(0.0, 50.0, 20) + i
        out.append(TraceView(donor=donor, acceptor=acceptor, frame_time=0.1, name=f"mol-{i}"))
    return out


@pytest.fixture
def shell(qapp, qtbot):
    from tether.gui.shell import TetherShell

    s = TetherShell()
    qtbot.addWidget(s.window)
    yield s
    s.close()


def test_shell_hosts_surfaces_and_installs_filter(shell) -> None:
    # The shell hosts the three non-text surfaces + the exempt category field,
    # and the curation event filter is installed on the application.
    assert shell.molecule_list is not None
    assert shell.movie_switcher is not None
    assert bool(shell.category_field.property("tetherTextEntry")) is True
    assert shell.event_filter.qobject is not None


def test_bare_key_from_list_dispatches_and_updates_status(shell) -> None:
    from pyqtgraph.Qt import QtCore

    from tether.gui.curation import Command, CurationAction

    k = QtCore.Qt.Key
    consumed = shell.event_filter.filter_event(shell.molecule_list, _key_event(k.Key_Space))
    assert consumed is True
    assert shell.controller.last == Command(CurationAction.ACCEPT)
    assert "Accepted" in shell.status_message


def test_category_field_keeps_text_semantics(shell) -> None:
    from pyqtgraph.Qt import QtCore

    k = QtCore.Qt.Key
    consumed = shell.event_filter.filter_event(shell.category_field, _key_event(k.Key_Space))
    assert consumed is False
    assert shell.controller.history == []


def test_set_molecules_populates_list_and_dock(shell) -> None:
    traces = _traces(4)
    shell.set_molecules(traces)
    assert shell.molecule_list.count() == 4
    assert shell.molecule_list.currentRow() == 0
    assert shell.trace_dock.trace is traces[0]


def test_next_prev_navigate_the_molecule_list(shell) -> None:
    from pyqtgraph.Qt import QtCore

    k = QtCore.Qt.Key
    traces = _traces(3)
    shell.set_molecules(traces)
    shell.event_filter.filter_event(shell.molecule_list, _key_event(k.Key_Right))  # next
    assert shell.molecule_list.currentRow() == 1
    assert shell.trace_dock.trace is traces[1]
    shell.event_filter.filter_event(shell.molecule_list, _key_event(k.Key_Down))  # next alias
    assert shell.molecule_list.currentRow() == 2
    shell.event_filter.filter_event(shell.molecule_list, _key_event(k.Key_Left))  # prev
    assert shell.molecule_list.currentRow() == 1
    # Navigation clamps at the ends (does not wrap past the last molecule).
    for _ in range(5):
        shell.event_filter.filter_event(shell.molecule_list, _key_event(k.Key_Right))
    assert shell.molecule_list.currentRow() == 2


def test_overflow_picker_lists_and_assigns_beyond_nine(shell) -> None:
    from tether.gui.shell import OverflowCategoryPicker

    categories = [f"cat {i}" for i in range(1, 13)]  # twelve categories
    picker = OverflowCategoryPicker(categories, parent=shell.window)
    # Only classes beyond the 1-9 hotkeys are offered (10, 11, 12).
    assert picker.overflow_classes == [10, 11, 12]
    picker.choose(12)
    assert picker.selected_class() == 12
    from tether.gui.curation import Command, CurationAction

    assert picker.command() == Command(CurationAction.ASSIGN_CATEGORY, 12)


def test_cheatsheet_overlay_reflects_the_keymap(shell) -> None:
    from pyqtgraph.Qt import QtCore

    from tether.gui.curation import Command, CurationAction
    from tether.gui.shell import CheatSheetOverlay

    overlay = CheatSheetOverlay(shell.keymap, parent=shell.window)
    assert dict(overlay.rows).get("Space") == "Accept trace"
    assert overlay.dialog is not None

    # After a rebinding, refresh() re-reads the live keymap into the table.
    k = QtCore.Qt.Key
    shell.keymap.rebind(Command(CurationAction.ACCEPT), int(k.Key_A))
    overlay.refresh()
    rows = dict(overlay.rows)
    assert rows.get("A") == "Accept trace"
    assert "Space" not in rows


def test_show_overflow_picker_dispatches_selected_class(shell, monkeypatch) -> None:
    from tether.gui import shell as shell_mod
    from tether.gui.curation import Command, CurationAction

    # Drive the production menu path (TetherShell.show_overflow_picker) without a
    # real modal loop by stubbing the picker's exec() to return a chosen class.
    monkeypatch.setattr(shell_mod.OverflowCategoryPicker, "exec", lambda self: 11)
    shell.show_overflow_picker()
    assert shell.controller.last == Command(CurationAction.ASSIGN_CATEGORY, 11)
    assert "Category 11" in shell.status_message


def test_show_overflow_picker_cancel_dispatches_nothing(shell, monkeypatch) -> None:
    from tether.gui import shell as shell_mod

    monkeypatch.setattr(shell_mod.OverflowCategoryPicker, "exec", lambda self: None)
    before = len(shell.controller.history)
    shell.show_overflow_picker()
    assert len(shell.controller.history) == before  # cancel -> no dispatch


def test_show_cheatsheet_populates_the_overlay(shell) -> None:
    overlay = shell.show_cheatsheet()
    assert shell.cheatsheet is overlay
    assert dict(overlay.rows).get("Space") == "Accept trace"


def test_close_removes_the_event_filter(qapp, qtbot) -> None:
    from pyqtgraph.Qt import QtCore

    from tether.gui.shell import TetherShell

    k = QtCore.Qt.Key
    s = TetherShell()
    qtbot.addWidget(s.window)
    # Before close: a key routed through the application reaches the controller
    # (this exercises the real installEventFilter path, not just filter_event).
    QtCore.QCoreApplication.sendEvent(s.molecule_list, _key_event(k.Key_Space))
    assert len(s.controller.history) == 1
    s.close()
    # After close: the filter is uninstalled, so app-routed keys no longer reach it.
    QtCore.QCoreApplication.sendEvent(s.molecule_list, _key_event(k.Key_Space))
    assert len(s.controller.history) == 1  # unchanged
