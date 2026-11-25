# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The shell hosts its sections in one QStackedWidget (#341, ADR-0116).

These ``@pytest.mark.gui`` tests run headless on the pytest-qt ``QApplication``. They pin the
acceptance criteria of #341: the one ``QMainWindow`` has a ``QStackedWidget`` with one page per
registered section; ``section`` / ``set_section`` switch it; each dock belongs to one section;
and a section switch leaves the session lifecycle of ADR-0055 as it was — the process-local
owner registry, the lock (no acquire, refresh or release on a switch), the write freeze while
idealization runs, and both teardown routes.
"""

from __future__ import annotations

import importlib.util
import threading

import numpy as np
import pytest

pytest.importorskip("h5py")

_HAS_QT = all(importlib.util.find_spec(m) is not None for m in ("pyqtgraph", "PySide6"))
pytestmark = [
    pytest.mark.gui,
    pytest.mark.skipif(not _HAS_QT, reason="pyqtgraph/PySide6 not installed"),
]

_SECTIONS = ("prepare", "curate", "analyze")


@pytest.fixture
def shell(section_shell):
    """A bare shell from the offscreen harness (#344), checked for leaks when the test ends."""
    return section_shell(project=False)


def _project_fit(**choices):
    """A whole-project fit choice (#625), the default unless *choices* say otherwise."""
    from tether.gui.idealize_project import ProjectFit

    return ProjectFit(**choices)


def _no_model():
    """What a fake whole-project fit returns: a result that wrote no model."""
    from pathlib import Path as _Path

    from tether.services.idealize import IdealizeResult

    return IdealizeResult(project=_Path("fake.tether"))


def _store(tmp_path, *, n=3, t=12, name="sections.tether"):
    from _analysis_store import build_store_with_channels

    rng = np.random.default_rng(3)
    donor = rng.uniform(400.0, 800.0, size=(n, t))
    acceptor = rng.uniform(200.0, 600.0, size=(n, t))
    project, _keys = build_store_with_channels(tmp_path, donor, acceptor, name=name)
    return project


def _write_controls(shell) -> dict[str, bool]:
    return {
        "unreject": shell.unreject_button.isEnabled(),
        "import_return_leg": shell._act_import.isEnabled(),
        "validate_conditions": shell._act_validate_conditions.isEnabled(),
        "import_deeplasi": shell._act_import_deeplasi.isEnabled(),
    }


# --- the stack -------------------------------------------------------------


def test_central_widget_is_one_stack_with_a_page_per_registered_section(shell) -> None:
    from pyqtgraph.Qt import QtWidgets

    assert isinstance(shell.window, QtWidgets.QMainWindow)
    stack = shell.window.centralWidget()
    assert isinstance(stack, QtWidgets.QStackedWidget)
    assert stack is shell.section_stack
    assert shell.sections.ids == (*_SECTIONS, "deposit")
    assert shell.sections.visible_ids == _SECTIONS  # Deposit is hidden until #956 (#897)
    assert stack.count() == len(_SECTIONS)
    for index, section_id in enumerate(shell.sections.visible_ids):
        assert stack.widget(index) is shell.section_page(section_id)
    # The page of Curate and correct holds the start page and the trace dock.
    assert shell.trace_dock.widget.parent() is not None
    curate = shell.section_page("curate")
    assert curate.isAncestorOf(shell.trace_dock.widget)
    assert curate.isAncestorOf(shell.start_page.widget)


def test_the_shell_opens_on_curate_and_correct_with_the_start_page(shell) -> None:
    assert shell.section == "curate"
    assert shell.section_stack.currentWidget() is shell.section_page("curate")
    assert shell.start_page_visible
    assert shell.section_actions["curate"].isChecked()


def test_set_section_switches_the_page_and_the_view_menu(shell) -> None:
    for section_id in ("analyze", "prepare", "curate"):
        shell.set_section(section_id)
        assert shell.section == section_id
        assert shell.section_stack.currentWidget() is shell.section_page(section_id)
        assert shell.section_actions[section_id].isChecked()
        assert shell.start_page_visible is (section_id == "curate")


def test_the_view_menu_switches_the_section(shell) -> None:
    shell.section_actions["prepare"].trigger()
    assert shell.section == "prepare"
    assert shell.section_stack.currentWidget() is shell.section_page("prepare")
    titles = [action.text() for action in shell.section_actions.values()]
    assert titles == [spec.title for spec in shell.sections.visible_sections()]


def test_an_unknown_or_hidden_section_raises_and_changes_nothing(shell) -> None:
    shell.set_section("analyze")
    with pytest.raises(KeyError, match="deposit"):
        shell.set_section("deposit")
    assert shell.section == "analyze"
    assert shell.section_stack.currentWidget() is shell.section_page("analyze")
    with pytest.raises(KeyError):
        shell.section_page("deposit")


# --- section-scoped docks --------------------------------------------------


def test_the_browser_dock_belongs_to_curate_and_shows_only_there(shell) -> None:
    assert shell.section_docks("curate") == [shell.browser_dock]
    assert not shell.browser_dock.isHidden()
    shell.set_section("analyze")
    assert shell.browser_dock.isHidden()
    shell.set_section("prepare")
    assert shell.browser_dock.isHidden()
    shell.set_section("curate")
    assert not shell.browser_dock.isHidden()


def _dock_menu_titles(shell) -> list[str]:
    """The section dock items the window's own dock menu (a right click on the menu bar) offers.

    The Messages dock (#627) belongs to the whole window, not to a section, so it is offered in
    every section and left out here.
    """
    menu = shell.window.createPopupMenu()
    try:
        return [
            a.text()
            for a in menu.actions()
            if a.isVisible() and not a.isSeparator() and a.text() != "&Messages"
        ]
    finally:
        menu.deleteLater()


def test_the_dock_menu_offers_only_the_current_sections_docks(shell, tmp_path) -> None:
    project = _store(tmp_path)
    assert shell.load_project(project.path) is not None
    shell.set_section("analyze")
    # In Analyze, the dock menu cannot bring the Browser or the Overlap dock over the page.
    # Analyze has no dock of its own: its plots are its page (#374).
    assert _dock_menu_titles(shell) == []
    assert not shell.browser_dock.toggleViewAction().isVisible()
    shell.set_section("curate")
    assert sorted(_dock_menu_titles(shell)) == ["Browser", "Overlap"]
    shell.set_section("prepare")
    assert _dock_menu_titles(shell) == []
    assert shell.browser_dock.isHidden()


def test_a_dock_the_user_closed_stays_closed_across_a_switch(shell) -> None:
    shell.browser_dock.close()
    assert shell.browser_dock.isHidden()
    shell.set_section("analyze")
    shell.set_section("curate")
    assert shell.browser_dock.isHidden()
    shell.browser_dock.show()
    shell.set_section("analyze")
    shell.set_section("curate")
    assert not shell.browser_dock.isHidden()


def test_lazy_docks_attach_to_their_section(shell, tmp_path) -> None:
    project = _store(tmp_path)
    assert shell.load_project(project.path) is not None
    overlap = shell._overlap_dock_widget
    assert overlap is not None
    assert overlap in shell.section_docks("curate")
    assert not overlap.isHidden()

    # Analyze holds no dock (its plots are its page, #374), so the Curate docks hide there.
    shell.set_section("analyze")
    assert shell.section_docks("analyze") == []
    assert overlap.isHidden()
    assert shell.browser_dock.isHidden()

    shell.set_section("curate")
    assert not overlap.isHidden()

    # Closing the project discards the lazy dock and shows the start page.
    shell.set_section("analyze")
    assert shell.close_project()
    assert shell.section == "curate"
    assert shell.start_page_visible
    assert shell.section_docks("analyze") == []
    assert shell.section_docks("curate") == [shell.browser_dock]


def test_a_lazy_dock_built_while_its_section_is_hidden_stays_hidden(shell, tmp_path) -> None:
    project = _store(tmp_path)
    shell.set_section("prepare")
    assert shell.load_project(project.path) is not None
    overlap = shell._overlap_dock_widget
    assert overlap is not None
    assert overlap.isHidden()
    shell.set_section("curate")
    assert not overlap.isHidden()


# --- the session lifecycle of ADR-0055 across a switch ---------------------


def test_switching_sections_neither_acquires_refreshes_nor_releases_the_lock(
    qapp, qtbot, tmp_path, monkeypatch
) -> None:
    from tether.gui.shell import TetherShell
    from tether.project.core import Project

    project = _store(tmp_path)
    first = TetherShell()
    second = TetherShell()
    qtbot.addWidget(first.window)
    qtbot.addWidget(second.window)
    try:
        held = first.load_project(project.path)
        assert held is not None and first.curation_project is held

        calls: list[str] = []
        for name in ("acquire_lock", "refresh_lock", "release_lock"):
            original = getattr(Project, name)

            def spy(self, *args, _name=name, _original=original, **kwargs):
                calls.append(_name)
                return _original(self, *args, **kwargs)

            monkeypatch.setattr(Project, name, spy)

        owner_before = project.lock_owner()
        for section_id in (*_SECTIONS, *reversed(_SECTIONS)):
            first.set_section(section_id)
            second.set_section(section_id)
        assert calls == []
        assert project.lock_owner() == owner_before
        assert first.curation_project is held
        assert first.holds_project_claim

        # The process-local owner registry still refuses a second writable shell.
        second.set_section("analyze")
        assert second.load_project(project.path) is not None
        assert second.curation_project is None
        assert "another Tether window" in (second.read_only_reason or "")
        assert not second.holds_project_claim
    finally:
        first.close()
        second.close()


def test_every_write_seam_stays_disabled_while_idealizing_across_a_switch(
    shell, qtbot, tmp_path
) -> None:
    from pyqtgraph.Qt import QtCore

    from tether.project.labels import read_labels

    project = _store(tmp_path)
    assert shell.load_project(project.path) is not None
    assert all(_write_controls(shell).values())
    started = threading.Event()
    gate = threading.Event()

    def blocking_idealizer(_fit, **_kw):
        started.set()
        gate.wait(timeout=5.0)
        return _no_model()

    shell._idealizer = blocking_idealizer
    try:
        assert shell.idealize_project(_project_fit())
        qtbot.waitUntil(started.is_set, timeout=2000)
        assert shell.is_idealizing
        for section_id in (*_SECTIONS, "curate"):
            shell.set_section(section_id)
            assert not any(_write_controls(shell).values()), section_id
            assert shell._idealization_blocks_write("Accept")
        qtbot.keyClick(shell.molecule_list, QtCore.Qt.Key.Key_Space)
        assert read_labels(project.path).shape == (0,)
        assert "Idealize in progress" in shell.status_message
    finally:
        gate.set()
        qtbot.waitUntil(lambda: not shell.is_idealizing, timeout=5000)
    assert all(_write_controls(shell).values())


@pytest.mark.parametrize("route", ["window-close", "about-to-quit"])
def test_both_teardown_routes_release_the_lock_after_a_switch(qapp, qtbot, tmp_path, route) -> None:
    from pyqtgraph.Qt import QtCore, QtWidgets

    from tether.gui.shell import TetherShell

    project = _store(tmp_path)
    shell = TetherShell()
    qtbot.addWidget(shell.window)
    try:
        assert shell.load_project(project.path) is not None
        shell.set_section("analyze")
        shell.set_section("prepare")
        assert project.lock_owner() is not None
        if route == "window-close":
            QtWidgets.QApplication.sendEvent(shell.window, QtCore.QEvent(QtCore.QEvent.Type.Close))
        else:
            qapp.aboutToQuit.emit()
        assert shell.session_project is None
        assert not shell.holds_project_claim
        assert not shell.lock_release_pending
        assert project.lock_owner() is None
    finally:
        shell.close()
