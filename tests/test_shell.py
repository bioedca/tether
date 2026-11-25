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
from pathlib import Path

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
    # The shell hosts the non-text surfaces + the category editor's entry point (#633),
    # and the curation event filter is installed on the application.
    assert shell.molecule_list is not None
    assert shell.movie_switcher is not None
    assert shell.categories_button.text() == "Categories…"
    assert shell.event_filter.qobject is not None


def test_bare_accept_key_without_project_reports_nonwriting(shell) -> None:
    from pyqtgraph.Qt import QtCore

    from tether.gui.curation import Command, CurationAction

    k = QtCore.Qt.Key
    shell.set_molecules(_traces(1))
    consumed = shell.event_filter.filter_event(shell.molecule_list, _key_event(k.Key_Space))
    assert consumed is True
    assert shell.controller.last == Command(CurationAction.ACCEPT)
    assert "load a writable project" in shell.status_message
    assert "Accepted" not in shell.status_message


def test_deferred_store_commands_report_unavailable(shell, qtbot) -> None:
    from pyqtgraph.Qt import QtCore

    shell.set_molecules(_traces(1))

    qtbot.keyClick(shell.molecule_list, QtCore.Qt.Key.Key_G)

    assert "unavailable" in shell.status_message.lower()


@pytest.mark.parametrize("key_name", ["Key_Return", "Key_Enter"])
def test_enter_with_no_movie_shown_says_why(shell, qtbot, key_name) -> None:
    # #357: Enter jumps to the movie spot. A shell with no movie panel (no project, or
    # a project with no movie file here) has nowhere to jump, and says so.
    from pyqtgraph.Qt import QtCore

    shell.set_molecules(_traces(1))
    assert shell.roundtrip is None

    qtbot.keyClick(shell.molecule_list, getattr(QtCore.Qt.Key, key_name))

    assert shell.status_message == "Jump to movie spot unavailable: no movie is shown"


def test_category_field_keeps_text_semantics(shell) -> None:
    from pyqtgraph.Qt import QtCore

    from tether.gui.category_editor import CategoryEditor

    # The category name field lives in the category editor (#633); it stays a text entry.
    field = CategoryEditor(
        "c", [], usage=len, add=None, rename=None, remove=None, move=None, parent=shell.window
    ).name_field
    assert bool(field.property("tetherTextEntry")) is True
    k = QtCore.Qt.Key
    consumed = shell.event_filter.filter_event(field, _key_event(k.Key_Space), focus_widget=field)
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


def _cheatsheet_cells(overlay):
    """The ``(Key, Action)`` text of every row of the table the cheat-sheet shows."""
    from pyqtgraph.Qt import QtWidgets

    table = overlay.dialog.findChild(QtWidgets.QTableWidget)
    headers = [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())]
    assert headers == ["Key", "Action"]
    return [(table.item(r, 0).text(), table.item(r, 1).text()) for r in range(table.rowCount())]


def test_overflow_picker_lists_and_assigns_beyond_nine(shell) -> None:
    from pyqtgraph.Qt import QtWidgets

    from tether.gui.shell import OverflowCategoryPicker

    categories = [f"cat {i}" for i in range(1, 13)]  # twelve categories
    picker = OverflowCategoryPicker(categories, parent=shell.window)
    # Only classes beyond the 1-9 hotkeys are offered (10, 11, 12).
    assert picker.overflow_classes == [10, 11, 12]
    # The list the curator reads pairs each class number with its own category name
    # (class N is categories[N - 1]); an off-by-one here assigns the wrong category.
    shown = picker.dialog.findChild(QtWidgets.QListWidget)
    assert [shown.item(i).text() for i in range(shown.count())] == [
        "10: cat 10",
        "11: cat 11",
        "12: cat 12",
    ]
    # ... and that pairing is the persisted tMAVEN class lookup's, class for class (ADR-0125).
    from tether.project.conditions import ClassLookup

    lookup = ClassLookup.from_categories("", categories)
    assert [shown.item(i).text() for i in range(shown.count())] == [
        f"{cls}: {lookup.category_of(cls)}" for cls in picker.overflow_classes
    ]
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
    # The table the user sees holds exactly those rows, Key column then Action column.
    assert _cheatsheet_cells(overlay) == shell.keymap.cheatsheet()
    assert dict(_cheatsheet_cells(overlay)).get("Space") == "Accept trace"

    # After a rebinding, refresh() re-reads the live keymap into the table.
    k = QtCore.Qt.Key
    shell.keymap.rebind(Command(CurationAction.ACCEPT), int(k.Key_A))
    overlay.refresh()
    rows = dict(overlay.rows)
    assert rows.get("A") == "Accept trace"
    assert "Space" not in rows
    shown = _cheatsheet_cells(overlay)
    assert shown == shell.keymap.cheatsheet()
    assert dict(shown).get("A") == "Accept trace"
    assert "Space" not in dict(shown)


def test_status_bar_and_cheatsheet_do_not_advertise_deferred_keys(shell) -> None:
    # #345: every deferred command is marked unavailable on the shipped cheat sheet, the
    # working ones are not. Jump works since #357, so the ready line names its key again
    # and the sheet offers it plainly; the grid is still deferred.
    from tether.gui.curation import CurationAction
    from tether.gui.shell import DEFERRED_ACTIONS

    assert CurationAction.JUMP not in DEFERRED_ACTIONS
    assert shell.status_message.endswith(" jump")
    assert "grid" not in shell.status_message.lower()
    shown = _cheatsheet_cells(shell.cheatsheet)
    assert shown == shell.keymap.cheatsheet(unavailable=DEFERRED_ACTIONS)
    rows = dict(shown)
    assert rows.get("Space") == "Accept trace"
    assert rows.get("I") == "Idealize project…"
    assert rows.get("G") == "Toggle grid (unavailable)"
    # The category keys write the project since #359, so the sheet offers them plainly.
    assert rows.get("1") == "Assign category 1"
    assert "Jump to movie spot" in rows.values()
    assert "Jump to movie spot (unavailable)" not in rows.values()


def test_every_deferred_action_reports_unavailable(shell) -> None:
    # The set the cheat sheet marks is the set the handlers route to _unavailable,
    # so the two cannot drift apart (#357 / #364 / #359 shrink both together).
    from tether.gui.curation import Command, CurationAction
    from tether.gui.shell import DEFERRED_ACTIONS

    shell.set_molecules(_traces(1))
    for action in sorted(DEFERRED_ACTIONS):
        arg = 1 if action is CurationAction.ASSIGN_CATEGORY else None
        shell.controller.dispatch(Command(action, arg))
        assert "unavailable" in shell.status_message, action


def test_show_overflow_picker_dispatches_selected_class(shell, monkeypatch) -> None:
    from tether.gui import shell as shell_mod
    from tether.gui.curation import Command, CurationAction

    # Drive the production menu path (TetherShell.show_overflow_picker) without a
    # real modal loop by stubbing the picker's exec() to return a chosen class.
    monkeypatch.setattr(shell_mod.OverflowCategoryPicker, "exec", lambda self: 11)
    shell.show_overflow_picker()
    assert shell.controller.last == Command(CurationAction.ASSIGN_CATEGORY, 11)


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
    shown = _cheatsheet_cells(overlay)
    assert len(shown) == len(shell.keymap.cheatsheet()) > 0
    assert dict(shown).get("Space") == "Accept trace"


def _menu_action(shell, menu_title, action_text):
    """The action ``action_text`` of the menu-bar menu ``menu_title`` (as the user sees it)."""
    from pyqtgraph.Qt import QtWidgets

    # findChildren, not QAction.menu(): reading the menus through QAction.menu() left
    # the Hand-off menu's C++ object deleted later in the test ("Internal C++ object
    # already deleted"), so the menu is found by its title among the window's children.
    (menu,) = [m for m in shell.window.findChildren(QtWidgets.QMenu) if m.title() == menu_title]
    actions = {a.text(): a for a in menu.actions()}
    return actions[action_text]


def test_curation_menu_shortcuts_action_shows_the_cheatsheet(shell) -> None:
    # Trigger the menu action itself (not show_cheatsheet()), so a disconnected
    # action fails: the overlay stays hidden and its table stays as built.
    overlay = shell.cheatsheet
    assert not overlay.dialog.isVisible()
    _menu_action(shell, "&Curation", "Keyboard &shortcuts…").trigger()
    assert overlay.dialog.isVisible()
    assert dict(_cheatsheet_cells(overlay)).get("Space") == "Accept trace"
    overlay.dialog.hide()


def test_curation_menu_overflow_action_dispatches_the_chosen_class(shell, monkeypatch) -> None:
    from tether.gui import shell as shell_mod
    from tether.gui.curation import Command, CurationAction

    # Only the modal loop is replaced; the trigger runs the real menu handler.
    opened = []

    def _exec(picker):
        opened.append(picker)
        return 11

    monkeypatch.setattr(shell_mod.OverflowCategoryPicker, "exec", _exec)
    _menu_action(shell, "&Curation", "Assign category (&overflow)…").trigger()
    assert len(opened) == 1
    assert shell.controller.last == Command(CurationAction.ASSIGN_CATEGORY, 11)


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


# --- the I key opens "Idealize project…" (#625) ------------------------------
# The fit itself, its dialog, progress, cancel and refit: tests/test_shell_idealize_project.py.


def _keyed_traces(n=3):
    from tether.gui.trace_dock import TraceView

    out = []
    for i in range(n):
        donor = np.linspace(100.0, 50.0, 20) + i
        acceptor = np.linspace(0.0, 50.0, 20) + i
        out.append(
            TraceView(
                donor=donor,
                acceptor=acceptor,
                frame_time=0.1,
                name=f"mol-{i}",
                molecule_key=f"m{i}",
            )
        )
    return out


def test_i_key_without_a_project_fits_nothing_and_says_why(qapp, qtbot) -> None:
    # No code path fits a single molecule from a key (#625): with molecules shown but no
    # project open, I opens no dialog and starts no task, and the status line says why.
    from pyqtgraph.Qt import QtCore

    from tether.gui.shell import TetherShell

    k = QtCore.Qt.Key
    calls: list = []
    s = TetherShell(idealizer=lambda *a, **kw: calls.append((a, kw)))
    qtbot.addWidget(s.window)
    try:
        s.set_molecules(_keyed_traces(2))
        consumed = s.event_filter.filter_event(s.molecule_list, _key_event(k.Key_I))
        assert consumed is True  # the Tether-only I key is handled, not passed through
        assert s.idealize_dialog is None
        assert not s.is_idealizing
        assert calls == []
        assert s.status_message == (
            "Idealize project unavailable: load a project with extracted molecules"
        )
        assert s.trace_dock.idealized_path is None
    finally:
        s.close()


# --- hand-off round trip (export + return-leg import), M2 S7 PR-B -------------


class _FakeHandoff:
    """A recording :class:`HandoffSeam` stand-in so the menu wiring needs no store."""

    def __init__(self, *, report=None, manifest=None, applied=None, fail=None):
        self.report = report
        self.manifest = manifest
        self.applied = applied
        self._fail = fail or {}
        self.hand_off_calls: list = []
        self.preview_calls: list = []
        self.apply_calls: list = []

    def hand_off(self, molecule_keys, out_path):
        self.hand_off_calls.append((molecule_keys, out_path))
        if "hand_off" in self._fail:
            raise self._fail["hand_off"]
        return self.manifest

    def preview(self, smd_path, *, model_path=None):
        self.preview_calls.append((smd_path, model_path))
        if "preview" in self._fail:
            raise self._fail["preview"]
        return self.report

    def apply(self, smd_path, decision, *, model_path=None):
        self.apply_calls.append((smd_path, decision, model_path))
        if "apply" in self._fail:
            raise self._fail["apply"]
        return self.applied


def _manifest(n=2):
    from tether.project.handoff import HandoffManifest

    return HandoffManifest(
        path=Path("out.hdf5"),
        intensity_quantity="corrected",
        molecule_keys=[f"k{i}" for i in range(n)],
        molecule_ids=[f"id{i}" for i in range(n)],
    )


def _applied(**kwargs):
    from tether.project.handoff import AppliedReconcile

    return AppliedReconcile(**kwargs)


def _shell_report():
    from tether.project.handoff import ReconcileReport, TraceReconcile, WindowChange

    trace = TraceReconcile(
        returned_index=0,
        store_row=0,
        molecule_key="a",
        molecule_id="id-a",
        window_change=WindowChange(old=(0, 100), new=(0, 50)),
        class_change=None,
    )
    return ReconcileReport(
        matched=[trace],
        unmatched_returned=[],
        intensity_quantity="corrected",
        smd_path=Path("ret.hdf5"),
        model_path=None,
        model_name="tmaven-import",
        imported_model_type=None,
        imported_nstates=None,
    )


@pytest.fixture
def make_shell(qapp, qtbot):
    """Factory that builds shells (optionally with a handoff seam) and closes them."""
    from tether.gui.shell import TetherShell

    created: list = []

    def _make(**kwargs):
        s = TetherShell(**kwargs)
        qtbot.addWidget(s.window)
        created.append(s)
        return s

    yield _make
    for s in created:
        s.close()


def test_handoff_menu_exposes_export_and_import(make_shell) -> None:
    labels = [a.text() for a in make_shell().handoff_menu.actions()]
    assert any("tMAVEN" in t for t in labels)
    assert any("Import" in t for t in labels)


def test_handoff_menu_import_action_runs_the_return_leg(make_shell, monkeypatch) -> None:
    from pyqtgraph.Qt import QtWidgets

    shell = make_shell(handoff=_FakeHandoff(report=_shell_report()))
    # The two file pickers answer "ret.hdf5" and then Cancel (no model); the trigger
    # runs the real menu handler, which must hand both answers to the return leg.
    answers = iter([("ret.hdf5", ""), ("", "")])
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: next(answers))
    )
    calls = []
    shell.import_return_leg = lambda smd, *, model_path=None: calls.append((smd, model_path))
    _menu_action(shell, "&Hand-off", "&Import return leg…").trigger()
    assert calls == [("ret.hdf5", None)]


def test_handoff_menu_import_action_without_project_reports(make_shell) -> None:
    shell = make_shell()  # no handoff seam: the handler reports before any picker opens
    _menu_action(shell, "&Hand-off", "&Import return leg…").trigger()
    assert "load a project" in shell.status_message


def test_hand_off_without_project_reports(make_shell) -> None:
    shell = make_shell()  # no handoff seam wired
    assert shell.hand_off_to_tmaven("out.hdf5") is None
    assert "load a project" in shell.status_message


def test_hand_off_invokes_seam_and_reports_count(make_shell) -> None:
    fake = _FakeHandoff(manifest=_manifest(2))
    shell = make_shell(handoff=fake)
    manifest = shell.hand_off_to_tmaven("out.hdf5")
    assert manifest is fake.manifest
    assert fake.hand_off_calls == [(None, "out.hdf5")]  # every extracted molecule
    assert "Handed off 2 molecule" in shell.status_message


def test_hand_off_failure_is_reported(make_shell) -> None:
    fake = _FakeHandoff(fail={"hand_off": RuntimeError("disk full")})
    shell = make_shell(handoff=fake)
    assert shell.hand_off_to_tmaven("out.hdf5") is None
    assert "Hand-off failed: disk full" in shell.status_message


class _WritingHandoff(_FakeHandoff):
    """A recording seam that also creates the file, so the test can read the disk."""

    def hand_off(self, molecule_keys, out_path):
        Path(out_path).write_bytes(b"")
        return super().hand_off(molecule_keys, out_path)


@pytest.mark.parametrize(
    ("typed", "written"),
    [
        ("handoff", "handoff.hdf5"),  # #568: the Linux Qt dialog returns the bare name
        ("handoff.hdf5", "handoff.hdf5"),
        ("handoff.smd", "handoff.smd"),
        ("HANDOFF.HDF5", "HANDOFF.HDF5"),
        ("run.v2", "run.v2.hdf5"),  # a dot in the name is not an SMD suffix
    ],
)
def test_hand_off_dialog_defaults_the_smd_suffix(
    make_shell, monkeypatch, tmp_path, typed, written
) -> None:
    from pyqtgraph.Qt import QtWidgets

    chosen = str(tmp_path / typed)
    monkeypatch.setattr(
        QtWidgets.QFileDialog,
        "getSaveFileName",
        staticmethod(lambda *a, **k: (chosen, "tMAVEN SMD (*.hdf5 *.smd)")),
    )
    fake = _WritingHandoff(manifest=_manifest(2))
    shell = make_shell(handoff=fake)
    action = next(a for a in shell.handoff_menu.actions() if "tMAVEN" in a.text())
    action.trigger()
    assert fake.hand_off_calls == [(None, str(tmp_path / written))]
    assert sorted(p.name for p in tmp_path.iterdir()) == [written]


@pytest.mark.parametrize("replace", [True, False])
def test_hand_off_dialog_asks_before_replacing_the_suffixed_file(
    make_shell, monkeypatch, tmp_path, replace
) -> None:
    from pyqtgraph.Qt import QtWidgets

    buttons = QtWidgets.QMessageBox.StandardButton
    existing = tmp_path / "handoff.hdf5"
    existing.write_bytes(b"earlier export")
    monkeypatch.setattr(
        QtWidgets.QFileDialog,
        "getSaveFileName",
        staticmethod(lambda *a, **k: (str(tmp_path / "handoff"), "")),
    )
    asked: list = []

    def fake_question(parent, title, text, *args):
        asked.append(text)
        return buttons.Yes if replace else buttons.No

    monkeypatch.setattr(QtWidgets.QMessageBox, "question", staticmethod(fake_question))
    fake = _WritingHandoff(manifest=_manifest(2))
    shell = make_shell(handoff=fake)
    next(a for a in shell.handoff_menu.actions() if "tMAVEN" in a.text()).trigger()
    assert len(asked) == 1 and "handoff.hdf5" in asked[0]
    if replace:
        assert fake.hand_off_calls == [(None, str(existing))]
    else:
        assert fake.hand_off_calls == []
        assert existing.read_bytes() == b"earlier export"
        assert "Hand-off cancelled" in shell.status_message


def test_hand_off_dialog_does_not_ask_when_the_typed_path_is_kept(
    make_shell, monkeypatch, tmp_path
) -> None:
    from pyqtgraph.Qt import QtWidgets

    existing = tmp_path / "handoff.hdf5"
    existing.write_bytes(b"earlier export")  # the dialog itself confirmed this name
    monkeypatch.setattr(
        QtWidgets.QFileDialog,
        "getSaveFileName",
        staticmethod(lambda *a, **k: (str(existing), "")),
    )

    def no_question(*a, **k):
        raise AssertionError("the dialog already confirmed this name")

    monkeypatch.setattr(QtWidgets.QMessageBox, "question", staticmethod(no_question))
    fake = _WritingHandoff(manifest=_manifest(2))
    shell = make_shell(handoff=fake)
    next(a for a in shell.handoff_menu.actions() if "tMAVEN" in a.text()).trigger()
    assert fake.hand_off_calls == [(None, str(existing))]


def test_hand_off_dialog_cancel_hands_off_nothing(make_shell, monkeypatch) -> None:
    from pyqtgraph.Qt import QtWidgets

    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: ("", ""))
    )
    fake = _FakeHandoff(manifest=_manifest(2))
    shell = make_shell(handoff=fake)
    next(a for a in shell.handoff_menu.actions() if "tMAVEN" in a.text()).trigger()
    assert fake.hand_off_calls == []
    assert "Hand-off cancelled" in shell.status_message


def test_import_return_leg_applies_decision(make_shell, monkeypatch) -> None:
    from tether.gui import reconcile as reconcile_mod
    from tether.gui.reconcile import ReconcileDecision

    decision = ReconcileDecision(accept_windows=("id-a",))
    monkeypatch.setattr(reconcile_mod.ReconcileDialog, "exec", lambda self: decision)
    fake = _FakeHandoff(report=_shell_report(), applied=_applied(windows_applied=["id-a"]))
    shell = make_shell(handoff=fake)
    applied = shell.import_return_leg("ret.hdf5")
    assert applied is fake.applied
    assert fake.preview_calls == [("ret.hdf5", None)]
    assert len(fake.apply_calls) == 1
    smd, got_decision, model_path = fake.apply_calls[0]
    assert smd == "ret.hdf5"
    assert got_decision == decision  # the dialog's decision is passed straight through
    assert model_path is None
    assert "1 window" in shell.status_message


def test_import_return_leg_cancel_does_not_apply(make_shell, monkeypatch) -> None:
    from tether.gui import reconcile as reconcile_mod

    monkeypatch.setattr(reconcile_mod.ReconcileDialog, "exec", lambda self: None)
    fake = _FakeHandoff(report=_shell_report())
    shell = make_shell(handoff=fake)
    assert shell.import_return_leg("ret.hdf5") is None
    assert fake.apply_calls == []  # nothing is committed on cancel
    assert "cancelled" in shell.status_message


def test_import_return_leg_without_project_reports(make_shell) -> None:
    shell = make_shell()
    assert shell.import_return_leg("ret.hdf5") is None
    assert "load a project" in shell.status_message


def test_import_return_leg_preview_failure_reported(make_shell) -> None:
    fake = _FakeHandoff(fail={"preview": FileNotFoundError("no smd")})
    shell = make_shell(handoff=fake)
    assert shell.import_return_leg("ret.hdf5") is None
    assert "preview failed" in shell.status_message
    assert fake.apply_calls == []


def test_make_store_handoff_delegates_to_project_handoff(monkeypatch) -> None:
    from tether.gui.reconcile import ReconcileDecision
    from tether.gui.shell import make_store_handoff

    calls: dict = {}

    def fake_hand_off(project, keys, *, out_path, intensity_quantity):
        calls["hand_off"] = (project, keys, out_path, intensity_quantity)
        return "manifest"

    def fake_read(project, smd, *, model_path, intensity_quantity, model_name):
        calls["preview"] = (project, smd, model_path, intensity_quantity, model_name)
        return "report"

    def fake_apply(
        project,
        smd,
        *,
        model_path,
        intensity_quantity,
        model_name,
        accept_windows,
        accept_classes,
        import_idealization,
        overwrite,
    ):
        calls["apply"] = {
            "project": project,
            "smd": smd,
            "model_path": model_path,
            "accept_windows": accept_windows,
            "accept_classes": accept_classes,
            "import_idealization": import_idealization,
            "intensity_quantity": intensity_quantity,
            "model_name": model_name,
            "overwrite": overwrite,
        }
        return "applied"

    monkeypatch.setattr("tether.project.handoff.hand_off_to_tmaven", fake_hand_off)
    monkeypatch.setattr("tether.project.handoff.read_return_leg", fake_read)
    monkeypatch.setattr("tether.project.handoff.apply_reconcile", fake_apply)

    seam = make_store_handoff("proj.tether", intensity_quantity="raw", model_name="mymodel")
    assert seam.hand_off(None, "out.hdf5") == "manifest"
    assert calls["hand_off"] == ("proj.tether", None, "out.hdf5", "raw")
    assert seam.preview("ret.hdf5", model_path="m.hdf5") == "report"
    assert calls["preview"] == ("proj.tether", "ret.hdf5", "m.hdf5", "raw", "mymodel")

    decision = ReconcileDecision(
        accept_windows=("id-a",), accept_classes=("id-b",), import_idealization=True
    )
    assert seam.apply("ret.hdf5", decision, model_path="m.hdf5") == "applied"
    applied_call = calls["apply"]
    assert applied_call["project"] == "proj.tether"
    assert applied_call["smd"] == "ret.hdf5"
    assert applied_call["model_path"] == "m.hdf5"
    assert applied_call["accept_windows"] == ("id-a",)
    assert applied_call["accept_classes"] == ("id-b",)
    assert applied_call["import_idealization"] is True
    assert applied_call["intensity_quantity"] == "raw"
    assert applied_call["model_name"] == "mymodel"
    assert applied_call["overwrite"] is False  # non-destructive by default


def test_store_handoff_apply_opts_into_the_retained_session_guard(tmp_path, monkeypatch) -> None:
    """A live store goes through the Project facade with ``require_held_lock=True``.

    The test above drives the seam with a *string* project, which takes the
    module-level branch; this one covers the ``isinstance(..., Project)`` branch the
    running GUI actually uses. Without the opt-in the return leg would fall back to
    the identity-only writer check, which cannot see a lock stolen and released
    while ``_reconcile`` runs.
    """
    from tether.gui.reconcile import ReconcileDecision
    from tether.gui.shell import make_store_handoff
    from tether.io.schema import create_project
    from tether.project.core import Project

    project = Project(create_project(tmp_path / "p.tether"))
    seen: dict = {}

    def recording_apply_reconcile(self, smd_path, **kwargs):
        seen["self"] = self
        seen["kwargs"] = kwargs
        return "applied"

    monkeypatch.setattr(Project, "apply_reconcile", recording_apply_reconcile)

    seam = make_store_handoff(project)
    assert seam.apply("ret.hdf5", ReconcileDecision(accept_windows=("id-a",))) == "applied"

    assert seen["self"] is project
    assert seen["kwargs"]["require_held_lock"] is True


def test_import_return_leg_apply_failure_is_reported(make_shell, monkeypatch) -> None:
    from tether.gui import reconcile as reconcile_mod
    from tether.gui.reconcile import ReconcileDecision

    monkeypatch.setattr(
        reconcile_mod.ReconcileDialog,
        "exec",
        lambda self: ReconcileDecision(accept_windows=("id-a",)),
    )
    fake = _FakeHandoff(report=_shell_report(), fail={"apply": RuntimeError("clobber")})
    shell = make_shell(handoff=fake)
    assert shell.import_return_leg("ret.hdf5") is None
    assert len(fake.apply_calls) == 1  # apply was attempted…
    assert "import failed: clobber" in shell.status_message  # …and its failure surfaced


def test_import_return_leg_threads_model_path_through_both_legs(make_shell, monkeypatch) -> None:
    from tether.gui import reconcile as reconcile_mod
    from tether.gui.reconcile import ReconcileDecision

    monkeypatch.setattr(
        reconcile_mod.ReconcileDialog,
        "exec",
        lambda self: ReconcileDecision(import_idealization=True),
    )
    fake = _FakeHandoff(
        report=_shell_report(), applied=_applied(idealization_written="tmaven-import")
    )
    shell = make_shell(handoff=fake)
    shell.import_return_leg("ret.hdf5", model_path="m.hdf5")
    # The same model_path threads through BOTH legs (preview gates the checkbox, apply imports).
    assert fake.preview_calls == [("ret.hdf5", "m.hdf5")]
    assert fake.apply_calls[0][2] == "m.hdf5"


def test_applied_summary_covers_every_branch() -> None:
    from tether.gui.shell import _applied_summary

    assert "nothing to apply" in _applied_summary(_applied())
    assert "/idealization/tmaven-import" in _applied_summary(
        _applied(idealization_written="tmaven-import")
    )
    assert "1 window" in _applied_summary(_applied(windows_applied=["a"]))
    assert "2 class(es)" in _applied_summary(_applied(classes_applied=["a", "b"]))
    assert "deferred to M4" in _applied_summary(_applied(classes_deferred=["a"]))
    assert "unfit trace(s) dropped" in _applied_summary(_applied(import_unfit_dropped=["a"]))
    assert "re-staled" in _applied_summary(_applied(stale_after=["a"]))
    # A rich commit chains multiple clauses in one line.
    combo = _applied_summary(
        _applied(windows_applied=["a"], classes_deferred=["b"], stale_after=["a"])
    )
    assert "1 window" in combo and "deferred to M4" in combo and "re-staled" in combo


# --- the Analyze section's plot builder (#374, §7.7) --------------------------
# It replaced the one-action &Analysis menu and its pyqtgraph population-histogram dock;
# the A1 histogram is now an entry of the plot registry (tests/test_histogram_dock.py).


def test_bare_shell_analysis_section_says_a_project_is_needed(shell) -> None:
    section = shell.analysis_section
    assert section.project is None
    assert section.empty_text == "Open a project to draw a plot."
    assert section.figure is None and section.canvas is None
    assert not section.add_button.isEnabled()  # nothing to add without a project


def test_analyze_has_no_analysis_menu_and_shows_the_plot_builder(shell) -> None:
    # The one-action &Analysis menu is replaced by the section (#374): Analyze declares no
    # menu, and its page is the plot builder.
    assert shell.section_menus("analyze") == []
    menu_titles = [a.text() for a in shell.window.menuBar().actions()]
    assert "&Analysis" not in menu_titles
    assert not hasattr(shell, "show_histogram")
    shell.set_section("analyze")
    assert shell.section_page("analyze") is shell.analysis_section.widget


def test_close_closes_the_analysis_section(qapp, qtbot) -> None:
    from tether.gui.shell import TetherShell

    s = TetherShell()
    qtbot.addWidget(s.window)
    section = s.analysis_section
    closed = []
    original_close = section.close

    def _spy_close() -> None:
        closed.append(True)
        original_close()

    section.close = _spy_close
    s.close()
    assert closed == [True]


# --- &Import menu (FR-LEGACY: M7 Deep-LASI re-analysis bundle import, §7.8) ----


class _StubWizardDialog:
    """A stand-in for ``DeepLasiWizardDialog`` returning preset produced paths.

    Lets the ``&Import`` (FR-LEGACY) entry point be driven headlessly (no modal event loop):
    :meth:`exec` records its call count and hands back the paths the shell reports.
    """

    def __init__(self, produced) -> None:
        self._produced = tuple(produced)
        self.exec_calls = 0

    def exec(self):
        self.exec_calls += 1
        return self._produced


def test_legacy_menu_exposes_import_action(shell) -> None:
    assert shell.legacy_menu.title() == "&Import"
    labels = [a.text() for a in shell.legacy_menu.actions()]
    assert labels == ["&Deep-LASI bundle…"]


def test_import_deeplasi_bundle_reports_produced_projects(shell) -> None:
    produced = (Path("out/recon_010.tether"), Path("out/analysis_025.tether"))
    stub = _StubWizardDialog(produced)
    result = shell.import_deeplasi_bundle(dialog_factory=lambda: stub)
    assert result == produced
    assert stub.exec_calls == 1
    assert "wrote 2 project(s)" in shell.status_message
    assert "recon_010.tether" in shell.status_message
    assert "analysis_025.tether" in shell.status_message


def test_import_deeplasi_bundle_reports_nothing_when_empty(shell) -> None:
    result = shell.import_deeplasi_bundle(dialog_factory=lambda: _StubWizardDialog(()))
    assert result == ()
    assert "no projects written" in shell.status_message


class _RaisingWizardDialog:
    """A stand-in whose modal run raises — the realistic failure path (the wizard
    decodes and imports inside ``exec``), so the guard is pinned where it fires."""

    def exec(self):
        raise RuntimeError("bundle decode failed")


def test_import_deeplasi_bundle_surfaces_dialog_failure(shell) -> None:
    # Construction succeeds; the modal run raises (where real decode/import happens).
    result = shell.import_deeplasi_bundle(dialog_factory=_RaisingWizardDialog)
    assert result == ()
    assert "Deep-LASI import failed" in shell.status_message
    assert "bundle decode failed" in shell.status_message


def test_legacy_menu_action_triggers_import(shell) -> None:
    # The menu action routes through import_deeplasi_bundle (which, unpatched, opens
    # the real modal dialog); patch the bound method so the trigger is observable
    # headlessly and assert the wiring fires.
    calls = []
    shell.import_deeplasi_bundle = lambda: calls.append(True) or ()
    shell.legacy_menu.actions()[0].trigger()
    assert calls == [True]


def test_import_deeplasi_bundle_builds_the_real_wizard_dialog(shell, monkeypatch) -> None:
    # No dialog_factory: the shell's own factory must build the real wizard dialog,
    # parented to the shell window (#746 GUI-F14). Only the modal loop is replaced.
    from tether.gui.deeplasi_wizard_ui import DeepLasiWizardDialog

    shown = []

    def _exec(dialog):
        shown.append(dialog)
        return ()

    monkeypatch.setattr(DeepLasiWizardDialog, "exec", _exec)
    assert shell.import_deeplasi_bundle() == ()
    assert len(shown) == 1
    assert isinstance(shown[0], DeepLasiWizardDialog)
    assert shown[0].dialog.parent() is shell.window
    assert "no projects written" in shell.status_message
