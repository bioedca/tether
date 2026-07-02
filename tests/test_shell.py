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


# --- one-click idealize (I key), M2 S6 PR-B ----------------------------------


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


def _fake_idealizer(traces):
    """A molecule_key -> two-level step path sized to the matching trace."""
    keyed = {t.molecule_key: t for t in traces}

    def _idealize(molecule_key):
        trace = keyed[molecule_key]
        path = np.full(trace.n_frames, 0.3)
        path[trace.n_frames // 2 :] = 0.7
        return path

    return _idealize


def test_i_key_idealizes_current_and_draws_overlay(qapp, qtbot) -> None:
    from pyqtgraph.Qt import QtCore

    from tether.gui.shell import TetherShell

    k = QtCore.Qt.Key
    traces = _keyed_traces(3)
    s = TetherShell(idealizer=_fake_idealizer(traces))
    qtbot.addWidget(s.window)
    try:
        s.set_molecules(traces)  # selects row 0
        consumed = s.event_filter.filter_event(s.molecule_list, _key_event(k.Key_I))
        assert consumed is True  # the Tether-only I key is handled, not passed through
        assert s.trace_dock.idealization_curve.isVisible()
        np.testing.assert_allclose(s.trace_dock.idealized_path, [0.3] * 10 + [0.7] * 10)
        assert "Idealized" in s.status_message and "m0" in s.status_message
    finally:
        s.close()


def test_i_key_without_idealizer_reports_load_project(qapp, qtbot) -> None:
    from pyqtgraph.Qt import QtCore

    from tether.gui.shell import TetherShell

    k = QtCore.Qt.Key
    # A shell with molecules but no wired idealizer (the synthetic/no-project state).
    s = TetherShell()
    qtbot.addWidget(s.window)
    try:
        s.set_molecules(_keyed_traces(2))
        s.event_filter.filter_event(s.molecule_list, _key_event(k.Key_I))
        assert "load a project" in s.status_message
        assert s.trace_dock.idealized_path is None
    finally:
        s.close()


def test_i_key_with_no_selection_reports(qapp, qtbot) -> None:
    from pyqtgraph.Qt import QtCore

    from tether.gui.shell import TetherShell

    k = QtCore.Qt.Key
    traces = _keyed_traces(1)
    s = TetherShell(idealizer=_fake_idealizer(traces))
    qtbot.addWidget(s.window)
    try:
        s.set_molecules([])  # nothing selected
        s.event_filter.filter_event(s.molecule_list, _key_event(k.Key_I))
        assert "select a molecule" in s.status_message
    finally:
        s.close()


def test_i_key_surfaces_idealizer_failure(qapp, qtbot) -> None:
    from pyqtgraph.Qt import QtCore

    from tether.gui.shell import TetherShell

    k = QtCore.Qt.Key
    traces = _keyed_traces(1)

    def _boom(_key):
        raise RuntimeError("sidecar exploded")

    s = TetherShell(idealizer=_boom)
    qtbot.addWidget(s.window)
    try:
        s.set_molecules(traces)
        # A failing fit must surface as a status message, not crash the shell.
        s.event_filter.filter_event(s.molecule_list, _key_event(k.Key_I))
        assert "Idealize failed" in s.status_message and "sidecar exploded" in s.status_message
        assert s.trace_dock.idealized_path is None
    finally:
        s.close()


def test_i_key_reports_when_no_idealization_produced(qapp, qtbot) -> None:
    from pyqtgraph.Qt import QtCore

    from tether.gui.shell import TetherShell

    k = QtCore.Qt.Key
    s = TetherShell(idealizer=lambda _key: None)
    qtbot.addWidget(s.window)
    try:
        s.set_molecules(_keyed_traces(1))
        s.event_filter.filter_event(s.molecule_list, _key_event(k.Key_I))
        assert "no idealization produced" in s.status_message
        assert s.trace_dock.idealized_path is None
    finally:
        s.close()


def test_i_key_surfaces_length_mismatch_without_crashing(qapp, qtbot) -> None:
    from pyqtgraph.Qt import QtCore

    from tether.gui.shell import TetherShell

    k = QtCore.Qt.Key
    # A misbehaving idealizer returns a wrong-length array (traces are 20 frames);
    # set_idealization rejects it and the shell must report, not crash (the draw is
    # inside the guarded block, so its ValueError surfaces as a status message).
    s = TetherShell(idealizer=lambda _key: np.zeros(5))
    qtbot.addWidget(s.window)
    try:
        s.set_molecules(_keyed_traces(1))
        s.event_filter.filter_event(s.molecule_list, _key_event(k.Key_I))
        assert "Idealize failed" in s.status_message
        assert s.trace_dock.idealized_path is None
    finally:
        s.close()


# --- make_store_idealizer (the store-backed production seam) ------------------


def _stub_stored(idealized, keys):
    from tether.project.idealize import StoredIdealization

    n = len(keys)
    width = idealized.shape[1] if idealized is not None else 0
    return StoredIdealization(
        model_name="vbfret",
        model_type="vbconhmm",
        nstates=2,
        means=np.array([0.3, 0.7]),
        variances=None,
        tmatrix=None,
        norm_tmatrix=None,
        elbo=1.0,
        idealized=idealized,
        state_paths=np.zeros((n, width), dtype="int64"),
        molecule_keys=list(keys),
        molecule_ids=[f"id-{i}" for i in range(n)],
        input_hashes=["h"] * n,
        intensity_quantity="corrected",
        nstates_selected_by="max-elbo",
        elbo_by_nstates={2: 1.0},
        app_version="0",
        created_utc="2026-07-01T00:00:00+00:00",
    )


def test_make_store_idealizer_runs_pipeline_and_returns_row(monkeypatch) -> None:
    from tether.gui.shell import make_store_idealizer

    calls = {}

    def fake_idealize(project, keys, *, model_name, overwrite, **kw):
        calls["project"] = project
        calls["keys"] = list(keys)
        calls["model_name"] = model_name
        calls["overwrite"] = overwrite
        calls["kw"] = kw
        idealized = np.array([[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]])
        return _stub_stored(idealized, ["k0", "k1"])

    monkeypatch.setattr("tether.project.idealize.idealize_molecules", fake_idealize)
    # Extra kwargs (nstates, intensity_quantity, …) must reach idealize_molecules.
    idealizer = make_store_idealizer("proj.tether", model_name="vbfret", nstates=3)
    row = idealizer("k1")
    np.testing.assert_allclose(row, [0.4, 0.5, 0.6])  # the selected key's row
    assert calls["keys"] == ["k1"]  # only the requested molecule is fitted
    assert calls["model_name"] == "vbfret"
    assert calls["overwrite"] is True  # re-pressing I re-idealizes into the model
    assert calls["kw"] == {"nstates": 3}  # **kwargs passthrough


def test_make_store_idealizer_returns_none_when_key_absent(monkeypatch) -> None:
    from tether.gui.shell import make_store_idealizer

    monkeypatch.setattr(
        "tether.project.idealize.idealize_molecules",
        lambda *a, **k: _stub_stored(np.array([[0.1, 0.2]]), ["other"]),
    )
    assert make_store_idealizer("proj.tether")("missing") is None


def test_make_store_idealizer_returns_none_when_idealized_missing(monkeypatch) -> None:
    from tether.gui.shell import make_store_idealizer

    monkeypatch.setattr(
        "tether.project.idealize.idealize_molecules",
        lambda *a, **k: _stub_stored(None, ["k0"]),
    )
    assert make_store_idealizer("proj.tether")("k0") is None
