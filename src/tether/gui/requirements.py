# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""A disabled control says which step is missing (#898, PRD §7.13 FR-WORKFLOW-4).

A control declares its *requirement*: one stage of the readiness record of ``tether status``
([`read_project_readiness`][tether.project.readiness.read_project_readiness], #896). One
helper, [`RequirementGate`][], then does the same four things for every section:

* it disables the control while the stage is not done (ready, or not needed);
* it sets the tooltip, and for a widget the accessible description, to the text of
  [`requirement_status`][], which names the missing step and what to do;
* it offers the action **Go to this step**, which opens the section of that stage;
* it gives the control its earlier tooltip and description again once the stage is done.

The words of each requirement are in one table, [`REQUIREMENTS`][], so every section says
the same thing and the documentation cites this table. [`requirement_status`][] is Qt-free;
the gate imports Qt only when it is made, as the rest of ``tether.gui`` does.

A screen reader reads a disabled widget's accessible description. A menu item has no
accessible description in Qt, so for a menu action the gate puts **Go to this step** in the
same menu, directly after the action: its name names the step. A widget gets the same action
on a tool button from [`RequirementGate.make_go_button`][], which the section puts next to
the control.
Both take the keyboard focus, because they are enabled while the control is not.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from tether.project.readiness import STAGES

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "GO_TO_STEP_TEXT",
    "NO_PROJECT_TEXT",
    "REQUIREMENTS",
    "REQUIREMENT_PROPERTY",
    "Requirement",
    "RequirementGate",
    "RequirementStatus",
    "requirement_status",
]

#: The text of the action that opens the section of the missing step.
GO_TO_STEP_TEXT = "Go to this step"

#: The reason of every requirement while no project is open.
NO_PROJECT_TEXT = "Not available: no project is open. Open or import a project first."

#: The Qt dynamic property that a gated control carries: the stage key of its requirement.
#: The ratchet test of #898 reads it to find the disabled controls with no requirement.
REQUIREMENT_PROPERTY = "tetherRequirement"


@dataclass(frozen=True)
class Requirement:
    """One requirement: a stage of the readiness record, its name, and what to do for it."""

    #: The stage key of the readiness record, for example ``"traces"``.
    stage: str
    #: The name of the step, as ``tether status`` prints it.
    step: str
    #: What the user does to complete the step, in the section that has it.
    how: str


_HOW = {
    "sources": "Add a movie to the project in Prepare.",
    "map": "Make or choose a map for each movie in Prepare.",
    "traces": "Extract the traces of a movie in Prepare.",
    "curation": "Accept or reject the molecules in Curate and correct.",
    "corrections": "Complete the corrections in Curate and correct.",
    "analysis_sets": "Make an analysis set in Analyze.",
    "fits": "Fit the molecules in Analyze.",
    "results": "Save a result snapshot in Analyze.",
    "deposit": "Make the deposit package in Deposit.",
}

#: The one table of requirement texts (#898): one entry for each stage of the readiness
#: record, in workflow order, by stage key.
REQUIREMENTS: dict[str, Requirement] = {
    key: Requirement(stage=key, step=name, how=_HOW[key]) for key, name, _section, _issue in STAGES
}


@dataclass(frozen=True)
class RequirementStatus:
    """Whether a requirement is met now, and if not, why and where to go."""

    met: bool
    #: The text of the tooltip and the accessible description; ``""`` when met.
    reason: str
    #: The section that **Go to this step** opens, or ``None`` when no section helps.
    section: str | None


def requirement_status(stage: str, readiness: object) -> RequirementStatus:
    """Return the [`RequirementStatus`][] of the requirement ``stage`` in ``readiness``.

    ``readiness`` is the record of ``tether status``, or ``None`` when no project is open.
    Raise `KeyError` for a ``stage`` that is not in [`REQUIREMENTS`][].
    """
    requirement = REQUIREMENTS[stage]
    if readiness is None:
        return RequirementStatus(False, NO_PROJECT_TEXT, None)
    record = readiness.stage(stage)  # type: ignore[attr-defined]
    if record.state.done:
        return RequirementStatus(True, "", None)
    if record.state.value == "not_available":
        reason = f"Not available: the step “{requirement.step}” is {record.state.text}."
        return RequirementStatus(False, reason, None)
    reason = (
        f"Not available: the step “{requirement.step}” is {record.state.text}. {requirement.how}"
    )
    return RequirementStatus(False, reason, record.section)


class RequirementGate:
    """The one helper that disables a control until its requirement is met (#898).

    Parameters
    ----------
    control
        A ``QWidget`` or a ``QAction``. The gate owns its enabled state from now on.
    stage
        The requirement: a key of [`REQUIREMENTS`][].
    go_to
        Opens a section by id; the shell passes its ``set_section``.
    section_title
        The title of a section the shell shows, or ``None`` for a hidden section.
    """

    def __init__(
        self,
        control: Any,
        stage: str,
        *,
        go_to: Callable[[str], object],
        section_title: Callable[[str], str | None],
    ) -> None:
        from pyqtgraph.Qt import QtGui, QtWidgets

        self._requirement = REQUIREMENTS[stage]
        self._control = control
        self._go_to = go_to
        self._section_title = section_title
        self._is_widget = isinstance(control, QtWidgets.QWidget)
        control.setProperty(REQUIREMENT_PROPERTY, stage)
        self._tooltip = control.toolTip()
        self._status_tip = control.statusTip()
        self._description = control.accessibleDescription() if self._is_widget else ""
        self._target: str | None = None
        self._status = RequirementStatus(False, NO_PROJECT_TEXT, None)
        self._go = QtGui.QAction(GO_TO_STEP_TEXT, control)
        self._go.setObjectName(f"go-to-{stage}")
        self._go.triggered.connect(self.go_to_step)
        self._buttons: list[Any] = []
        if not self._is_widget:
            for menu in control.associatedObjects():
                if isinstance(menu, QtWidgets.QMenu):
                    menu.setToolTipsVisible(True)
                    actions = menu.actions()
                    after = actions.index(control) + 1
                    menu.insertAction(actions[after] if after < len(actions) else None, self._go)

    @property
    def requirement(self) -> Requirement:
        """The [`Requirement`][] of the control."""
        return self._requirement

    @property
    def control(self) -> Any:
        """The gated ``QWidget`` or ``QAction``."""
        return self._control

    @property
    def status(self) -> RequirementStatus:
        """The status of the last [`update`][]."""
        return self._status

    @property
    def go_action(self) -> Any:
        """The ``QAction`` **Go to this step**; enabled and shown only while it can help."""
        return self._go

    def make_go_button(self, parent: Any) -> Any:
        """Make a ``QToolButton`` of [`go_action`][] in ``parent``, shown while it can help.

        A section puts it next to a widget control; a menu action needs none.
        """
        from pyqtgraph.Qt import QtWidgets

        button = QtWidgets.QToolButton(parent)
        button.setDefaultAction(self._go)
        button.setAutoRaise(True)
        self._buttons.append(button)
        self._sync_buttons()
        return button

    def _sync_buttons(self) -> None:
        for button in self._buttons:
            button.setAccessibleName(self._go.text())
            button.setVisible(self._go.isVisible())

    def update(self, readiness: object) -> RequirementStatus:
        """Apply the requirement to the control for ``readiness`` and return its status."""
        status = requirement_status(self._requirement.stage, readiness)
        self._status = status
        control = self._control
        control.setEnabled(status.met)
        control.setToolTip(status.reason or self._tooltip)
        control.setStatusTip(status.reason or self._status_tip)
        if self._is_widget:
            control.setAccessibleDescription(status.reason or self._description)
        title = None if status.section is None else self._section_title(status.section)
        self._target = status.section if title is not None else None
        offered = self._target is not None
        step = self._requirement.step
        self._go.setText(f"{GO_TO_STEP_TEXT}: {step}" if offered else GO_TO_STEP_TEXT)
        self._go.setToolTip(f"Open {title} to complete the step “{step}”" if offered else "")
        self._go.setEnabled(offered)
        self._go.setVisible(offered)
        self._sync_buttons()
        return status

    def go_to_step(self) -> str | None:
        """Open the section of the missing step; return its id, or ``None`` if none helps."""
        if self._target is None:
            return None
        self._go_to(self._target)
        return self._target
