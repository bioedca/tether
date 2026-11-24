# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""A disabled control says which step is missing (#898, FR-WORKFLOW-4).

The acceptance criteria of #898, one test or more each: one helper sets the disabled state,
the tooltip, the accessible description and **Go to this step** from a requirement; the text
of each requirement is in one table that the documentation cites; each registered
requirement is checked for its text and its action; a ratchet lists the section controls
that are disabled with no declared requirement; and the helper works with the keyboard and a
screen reader. The Qt-free core tests run without a display; the rest are headless
``pytest-qt``. The readiness records are made here; nothing here is a scientific oracle.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from tether.gui.requirements import (
    GO_TO_STEP_TEXT,
    NO_PROJECT_TEXT,
    REQUIREMENT_PROPERTY,
    REQUIREMENTS,
    requirement_status,
)
from tether.project.readiness import (
    STAGES,
    NextStep,
    ProjectReadiness,
    StageReadiness,
    StageState,
)

_HAS_QT = all(importlib.util.find_spec(m) is not None for m in ("pyqtgraph", "PySide6"))
needs_qt = pytest.mark.skipif(not _HAS_QT, reason="pyqtgraph/PySide6 not installed")

_DOC = Path(__file__).resolve().parents[1] / "docs" / "how-to" / "curate-traces.md"
_SECTION_OF = {key: section for key, _name, section, _issue in STAGES}
_TITLES = {
    "prepare": "Prepare",
    "curate": "Curate and correct",
    "analyze": "Analyze",
    "deposit": "Deposit",
}

#: The ratchet (#898): the section controls that are disabled with no declared requirement,
#: as ``(state, section, control)``. A section issue that declares a requirement for one
#: removes its line. The list can get shorter, never longer: a new disabled control fails
#: until it declares its requirement, and a line that no longer applies fails until it goes.
_UNDECLARED: frozenset[tuple[str, str, str]] = frozenset(
    {
        # The movie switcher is empty and off while no project is open (#1031).
        ("no project", "curate", "widget:Movie"),
        # The seconds axis of the trace dock is on only for a trace with a frame interval.
        ("no project", "curate", "widget:Time axis in seconds"),
        ("project open", "curate", "widget:Time axis in seconds"),
    }
)


def _readiness(**states: StageState) -> ProjectReadiness:
    """A record with every stage ready except those named, for example ``fits=STALE``."""
    stages = tuple(
        StageReadiness(key, name, section, states.get(key, StageState.READY), "", "", issue)
        for key, name, section, issue in STAGES
    )
    return ProjectReadiness("p.tether", stages, "ANALYSIS_READY", (), (), NextStep(None, None, ""))


# --- the Qt-free core ------------------------------------------------------------------------


def test_the_table_has_one_requirement_for_each_stage_in_workflow_order() -> None:
    assert list(REQUIREMENTS) == [key for key, *_ in STAGES]
    for key, name, section, _issue in STAGES:
        requirement = REQUIREMENTS[key]
        assert (requirement.stage, requirement.step) == (key, name)
        # The text says where the step is done.
        assert requirement.how.endswith(f" in {_TITLES[section]}."), key


def test_the_documentation_cites_the_table() -> None:
    page = _DOC.read_text(encoding="utf-8")
    for requirement in REQUIREMENTS.values():
        assert f"| {requirement.step} | {requirement.how} |" in page
    assert NO_PROJECT_TEXT.split(": ", 1)[0] == "Not available"
    assert f'"{NO_PROJECT_TEXT.rsplit(". ", 1)[1]}"' in " ".join(page.split())
    assert "tether.gui.requirements" in page


@pytest.mark.parametrize("stage", list(REQUIREMENTS))
def test_each_requirement_names_its_missing_step(stage) -> None:
    step = REQUIREMENTS[stage].step
    assert requirement_status(stage, None).reason == NO_PROJECT_TEXT
    assert requirement_status(stage, None).section is None
    for done in (StageState.READY, StageState.NOT_NEEDED):
        status = requirement_status(stage, _readiness(**{stage: done}))
        assert (status.met, status.reason, status.section) == (True, "", None)
    for state in (
        StageState.NOT_STARTED,
        StageState.IN_PROGRESS,
        StageState.STALE,
        StageState.BLOCKED,
        StageState.RUNNING,
    ):
        status = requirement_status(stage, _readiness(**{stage: state}))
        assert not status.met
        assert status.reason == (
            f"Not available: the step “{step}” is {state.text}. {REQUIREMENTS[stage].how}"
        )
        assert status.section == _SECTION_OF[stage]
    # A stage that a later issue adds has no step to go to yet.
    status = requirement_status(stage, _readiness(**{stage: StageState.NOT_AVAILABLE}))
    assert status.reason == f"Not available: the step “{step}” is not available in this version."
    assert (status.met, status.section) == (False, None)


def test_an_unknown_requirement_is_refused() -> None:
    with pytest.raises(KeyError):
        requirement_status("nothing", None)


# --- the helper in the shell -----------------------------------------------------------------


@pytest.fixture
def readiness_of(monkeypatch):
    """Make ``refresh_readiness`` read the record that the test sets."""
    from tether.services import status as status_mod

    holder: dict[str, object] = {"record": None}
    monkeypatch.setattr(status_mod, "project_status", lambda _path: holder["record"])

    def set_record(shell, record):
        holder["record"] = record
        shell.refresh_readiness()

    return set_record


def _page_button(shell, section: str, text: str):
    from pyqtgraph.Qt import QtWidgets

    box = QtWidgets.QWidget(shell.section_page(section))
    row = QtWidgets.QHBoxLayout(box)
    button = QtWidgets.QPushButton(text, box)
    button.setToolTip("Fit the selected molecules")
    row.addWidget(button)
    return button, row


def _section_action(shell, section: str, text: str):
    menu = shell.section_menus(section)[0]
    action = menu.addAction(text)
    action.setToolTip("before")
    return menu, action


@pytest.mark.gui
@needs_qt
@pytest.mark.parametrize("stage", list(REQUIREMENTS))
def test_the_helper_applies_each_registered_requirement(section_shell, readiness_of, stage):
    shell = section_shell()
    section = _SECTION_OF[stage]
    visible = section in shell.sections.visible_ids
    host = "analyze" if section != "analyze" else "prepare"
    button, row = _page_button(shell, host, "Fit")
    gate = shell.require(button, stage)
    go_button = gate.make_go_button(button.parentWidget())
    row.addWidget(go_button)
    menu, action = _section_action(shell, host, "Fit all")
    menu_gate = shell.require(action, stage)
    assert button.property(REQUIREMENT_PROPERTY) == stage
    assert action.property(REQUIREMENT_PROPERTY) == stage
    assert shell.requirement_gates[-2:] == (gate, menu_gate)

    readiness_of(shell, _readiness(**{stage: StageState.NOT_STARTED}))
    reason = requirement_status(stage, _readiness(**{stage: StageState.NOT_STARTED})).reason
    assert not button.isEnabled()
    assert not action.isEnabled()
    assert button.toolTip() == reason == button.accessibleDescription() == button.statusTip()
    assert action.toolTip() == reason == action.statusTip()
    # Go to this step follows the action directly in its menu, and opens the section.
    actions = menu.actions()
    assert actions[actions.index(action) + 1] is menu_gate.go_action
    for go in (gate.go_action, menu_gate.go_action):
        assert go.isEnabled() is visible
        assert go.isVisible() is visible
        if visible:
            assert go.text() == f"{GO_TO_STEP_TEXT}: {REQUIREMENTS[stage].step}"
            shell.set_section(host)
            go.trigger()
            assert shell.section == section
        else:
            # Deposit is hidden until #956: there is no section to open.
            assert go.text() == GO_TO_STEP_TEXT
            assert menu_gate.go_to_step() is None
    assert go_button.accessibleName() == gate.go_action.text()

    readiness_of(shell, _readiness())
    assert button.isEnabled() and action.isEnabled()
    assert button.toolTip() == "Fit the selected molecules"
    assert button.accessibleDescription() == ""
    assert action.toolTip() == "before"
    assert not gate.go_action.isVisible() and not menu_gate.go_action.isVisible()
    assert gate.go_to_step() is None


@pytest.mark.gui
@needs_qt
def test_with_no_project_a_gated_control_asks_for_one(section_shell) -> None:
    shell = section_shell(project=False)
    button, _row = _page_button(shell, "analyze", "Fit")
    gate = shell.require(button, "fits")
    assert not button.isEnabled()
    assert button.toolTip() == NO_PROJECT_TEXT == button.accessibleDescription()
    assert not gate.go_action.isEnabled()
    assert gate.status.reason == NO_PROJECT_TEXT


@pytest.mark.gui
@needs_qt
def test_a_real_project_gates_on_its_own_readiness(section_shell, tmp_path) -> None:
    from tether.project import Project

    shell = section_shell(project=False)
    button, _row = _page_button(shell, "analyze", "Fit")
    gate = shell.require(button, "traces")
    path = tmp_path / "new.tether"
    Project.create(path)
    assert shell.load_project(path) is not None
    # A new project has no molecules: ``tether status`` says the trace generation is not
    # started, and the gate says the same.
    assert gate.status.section == "prepare"
    assert "“trace generation” is not started" in button.toolTip()
    assert shell.close_project()
    assert button.toolTip() == NO_PROJECT_TEXT


@pytest.mark.gui
@needs_qt
def test_the_keyboard_and_a_screen_reader_reach_the_missing_step(
    section_shell, readiness_of, qtbot
) -> None:
    from pyqtgraph.Qt import QtCore, QtGui

    shell = section_shell()
    shell.set_section("analyze")
    button, row = _page_button(shell, "analyze", "Fit")
    button.setAccessibleName("Fit the selected molecules")
    gate = shell.require(button, "traces")
    go_button = gate.make_go_button(button.parentWidget())
    row.addWidget(go_button)
    menu, action = _section_action(shell, "analyze", "Fit all")
    menu_gate = shell.require(action, "traces")
    readiness_of(shell, _readiness(traces=StageState.NOT_STARTED))
    reason = gate.status.reason
    shell.show()
    qtbot.waitExposed(shell.window)

    # A screen reader: the disabled control keeps its name and reads the reason.
    accessible = QtGui.QAccessible.queryAccessibleInterface(button)
    assert accessible.text(QtGui.QAccessible.Text.Name) == "Fit the selected molecules"
    assert accessible.text(QtGui.QAccessible.Text.Description) == reason
    assert accessible.state().disabled
    go = QtGui.QAccessible.queryAccessibleInterface(go_button)
    assert go.text(QtGui.QAccessible.Text.Name) == "Go to this step: trace generation"
    assert not go.state().disabled
    # In a menu the next item names the step, because a menu item has no description.
    items = QtGui.QAccessible.queryAccessibleInterface(menu)
    names = [items.child(i).text(QtGui.QAccessible.Text.Name) for i in range(items.childCount())]
    assert names[names.index("Fit all") + 1] == "Go to this step: trace generation"

    # The keyboard: the go button takes the focus, and Space opens the section.
    assert go_button.isVisible()
    assert go_button.focusPolicy() & QtCore.Qt.FocusPolicy.TabFocus
    go_button.setFocus()
    qtbot.waitUntil(go_button.hasFocus)
    qtbot.keyClick(go_button, QtCore.Qt.Key.Key_Space)
    assert shell.section == "prepare"
    assert not menu_gate.control.isEnabled()


# --- the ratchet -----------------------------------------------------------------------------


def _label(obj) -> str:
    for text in (obj.objectName(), obj.accessibleName() if hasattr(obj, "accessibleName") else ""):
        if text:
            return text
    text = obj.text().replace("&", "") if hasattr(obj, "text") else ""
    return text or type(obj).__name__


def _menu_actions(menu):
    for action in menu.actions():
        if action.isSeparator():
            continue
        if action.menu() is not None:
            yield from _menu_actions(action.menu())
        else:
            yield action


def _disabled_undeclared(shell, state: str) -> set[tuple[str, str, str]]:
    from pyqtgraph.Qt import QtWidgets

    kinds = (
        QtWidgets.QAbstractButton,
        QtWidgets.QComboBox,
        QtWidgets.QLineEdit,
        QtWidgets.QAbstractSpinBox,
        QtWidgets.QAbstractSlider,
    )
    found: set[tuple[str, str, str]] = set()
    for section in shell.sections.visible_ids:
        roots = [shell.section_page(section), *shell.section_docks(section)]
        for root in roots:
            for widget in root.findChildren(QtWidgets.QWidget):
                if not isinstance(widget, kinds) or widget.isEnabled():
                    continue
                if widget.property(REQUIREMENT_PROPERTY) is None:
                    found.add((state, section, f"widget:{_label(widget)}"))
        for menu in shell.section_menus(section):
            for action in _menu_actions(menu):
                if action.isEnabled() or action.property(REQUIREMENT_PROPERTY) is not None:
                    continue
                found.add((state, section, f"menu:{_label(action)}"))
    return found


@pytest.mark.gui
@needs_qt
def test_the_ratchet_of_disabled_controls_with_no_requirement(section_shell) -> None:
    found = _disabled_undeclared(section_shell(project=False), "no project")
    found |= _disabled_undeclared(section_shell(), "project open")
    new = sorted(found - _UNDECLARED)
    gone = sorted(_UNDECLARED - found)
    assert not new, f"declare a requirement (TetherShell.require) for: {new}"
    assert not gone, f"remove from _UNDECLARED, they no longer apply: {gone}"
