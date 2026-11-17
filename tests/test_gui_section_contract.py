# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The contract every registered shell section passes, headless (#344).

Each test runs once per section in ``_gui_harness.SECTION_IDS`` on a shell the ``section_shell``
fixture builds over a writable temporary ``.tether``. For every section: it constructs, it
switches in and out, its docks attach to the window and leave with the section, the lazily built
ones detach when the project closes, and teardown leaves ``_GUI_PROJECT_OWNERS`` empty. A section
that builds a napari ``Viewer`` is listed in ``VIEWER_SECTIONS`` and skipped on macOS offscreen
only. No check reads a pixel: the offscreen canvas is 0×0.

The last tests check the harness itself: the section list matches the shell's registry, so a new
section cannot miss the contract, and each leak check fails when its leak is real.
"""

from __future__ import annotations

import pytest

from _gui_harness import SECTION_IDS, VIEWER_SECTIONS, SectionShells, needs_qt, section_params

pytest.importorskip("h5py")

pytestmark = [pytest.mark.gui, needs_qt]


def _dock_name(section_id: str, title: str) -> str:
    """The object name the shell gives a dock of ``section_id`` titled ``title``."""
    return f"{section_id}-{title.lower().replace(' ', '-')}"


def _assert_showing(shell, section_id: str) -> None:
    assert shell.section == section_id
    assert shell.section_stack.currentWidget() is shell.section_page(section_id)
    assert shell.section_actions[section_id].isChecked()
    for dock in shell.section_docks(section_id):
        assert not dock.isHidden(), dock.objectName()
        assert dock.toggleViewAction().isVisible(), dock.objectName()


def _assert_away(shell, section_id: str) -> None:
    assert shell.section != section_id
    assert shell.section_stack.currentWidget() is not shell.section_page(section_id)
    assert not shell.section_actions[section_id].isChecked()
    for dock in shell.section_docks(section_id):
        assert dock.isHidden(), dock.objectName()
        assert not dock.toggleViewAction().isVisible(), dock.objectName()


@pytest.mark.parametrize("section_id", section_params())
def test_the_section_constructs_on_a_project(section_shell, section_id) -> None:
    from pyqtgraph.Qt import QtWidgets

    shell = section_shell(section_id)
    spec = shell.sections.get(section_id)
    page = shell.section_page(section_id)
    assert isinstance(page, QtWidgets.QWidget)
    assert shell.section_stack.indexOf(page) == shell.sections.ids.index(section_id)
    assert shell.section_actions[section_id].text() == spec.title
    assert shell.curation_project is not None
    _assert_showing(shell, section_id)


@pytest.mark.parametrize("section_id", section_params())
def test_the_section_switches_in_and_out(section_shell, section_id) -> None:
    shell = section_shell(section_id)
    for other in SECTION_IDS:
        if other == section_id:
            continue
        shell.set_section(other)
        _assert_showing(shell, other)
        _assert_away(shell, section_id)
        shell.set_section(section_id)
        _assert_showing(shell, section_id)
        _assert_away(shell, other)


@pytest.mark.parametrize("section_id", section_params())
def test_the_sections_docks_attach_and_detach(section_shell, section_id) -> None:
    from pyqtgraph.Qt import QtCore

    shell = section_shell(section_id)
    window = shell.window
    spec = shell.sections.get(section_id)
    docks = shell.section_docks(section_id)
    by_name = {dock.objectName(): dock for dock in docks}

    # Every declared dock is in the window, in the area its spec names.
    for dock_spec in spec.docks:
        dock = by_name[_dock_name(section_id, dock_spec.title)]
        area = getattr(QtCore.Qt.DockWidgetArea, dock_spec.area.qt_name)
        assert window.dockWidgetArea(dock) == area, dock_spec.id
    # Every dock of the section, the lazily built ones too, is in the window and no other
    # section's list.
    for dock in docks:
        assert window.dockWidgetArea(dock) != QtCore.Qt.DockWidgetArea.NoDockWidgetArea
        for other in SECTION_IDS:
            if other != section_id:
                assert dock not in shell.section_docks(other)

    # Closing the project detaches the lazily built docks and keeps the declared ones.
    declared = {_dock_name(section_id, d.title) for d in spec.docks}
    lazy = [dock for dock in docks if dock.objectName() not in declared]
    assert shell.close_project()
    remaining = shell.section_docks(section_id)
    assert sorted(d.objectName() for d in remaining) == sorted(declared)
    for dock in lazy:
        assert window.dockWidgetArea(dock) == QtCore.Qt.DockWidgetArea.NoDockWidgetArea
    shell.set_section(section_id)
    _assert_showing(shell, section_id)


@pytest.mark.parametrize("section_id", section_params())
def test_the_section_leaves_no_project_owner_on_teardown(section_shell, section_id) -> None:
    from tether.gui import shell as shell_mod

    shell = section_shell(section_id)
    assert shell.holds_project_claim
    assert shell_mod._GUI_PROJECT_OWNERS
    section_shell.close(shell)
    assert not shell_mod._GUI_PROJECT_OWNERS


# --- the harness itself ------------------------------------------------------


def test_the_harness_lists_every_registered_section(section_shell) -> None:
    shell = section_shell(project=False)
    assert shell.sections.ids == SECTION_IDS
    assert set(SECTION_IDS) >= VIEWER_SECTIONS


def test_a_project_is_optional(section_shell) -> None:
    shell = section_shell("prepare", project=False)
    assert shell.section == "prepare"
    assert shell.curation_project is None
    assert shell.project_path is None


def test_a_second_close_ignores_a_lock_another_shell_took_since(qtbot, tmp_path) -> None:
    shells = SectionShells(qtbot, tmp_path, lock_timeout_ms=50)
    first = shells("curate")
    path = first.project_path
    shells.close(first)
    second = shells(project=path)
    assert second.holds_project_claim
    shells.close(first)  # idempotent: the lock on ``path`` now belongs to ``second``
    shells.close_all()


def test_the_lock_check_fails_while_the_lock_is_held(qtbot, tmp_path) -> None:
    shells = SectionShells(qtbot, tmp_path, lock_timeout_ms=50)
    shell = shells("curate")
    try:
        with pytest.raises(AssertionError, match="session lock outlived close"):
            shells._check_released(shell, [shell.project_path])
    finally:
        shells.close_all()


def test_the_event_filter_check_fails_when_the_filter_is_left_installed(qtbot, tmp_path) -> None:
    shells = SectionShells(qtbot, tmp_path)
    shell = shells(project=False)
    shell.close()
    shell.event_filter.install()
    try:
        with pytest.raises(AssertionError, match="curation event filter outlived close"):
            shells.close(shell)
    finally:
        shell.event_filter.remove()
    shells.close_all()


def test_the_owner_check_fails_while_another_shell_holds_a_project(qtbot, tmp_path) -> None:
    shells = SectionShells(qtbot, tmp_path)
    kept = shells("analyze")
    other = SectionShells(qtbot, tmp_path)
    other(project=False)
    try:
        with pytest.raises(AssertionError, match="_GUI_PROJECT_OWNERS is not empty"):
            other.close_all()
    finally:
        shells.close(kept)
