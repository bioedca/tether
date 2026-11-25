# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The "Idealize project…" dialog and the whole-project fit it starts (#625, PRD §7.4).

**Idealize → Idealize project…** in the Curate and correct section, and the **I** key, open
[`IdealizeProjectDialog`][]. The fit it starts is the ``idealize`` service command
([`tether.services.idealize.run_held`][]), the same command as ``tether idealize``, so the
same choices store the same model as
[`idealize_molecules`][tether.project.idealize.idealize_molecules] with the same arguments.
The window runs it on its background-task service (#343), with progress and **Cancel**.

* [`ProjectFit`][] — what the dialog chose: the method, the state count, the molecules and
  whether to refit only the stale model.
* [`read_fit_facts`][tether.services.idealize.read_fit_facts] — what the dialog shows before
  a fit: the conditions, the accepted molecules, the models and their stale rows, and
  whether the sidecar can be found (a read of the service, re-exported here).
* [`fit_refusal`][] — why a choice cannot start, or ``None``.
* [`fit_project`][] — the fit itself, on the worker thread.
* [`outcome_message`][] — the status line after the fit.

The molecules of a fit are the **accepted** molecules (``curation_label`` > 0), or those of
one condition when the project holds several. The model is stored under the name of its
method (``vbconhmm`` or ``ebhmm``) and replaces a model of that name. "Refit stale only" calls
[`reidealize`][tether.project.idealize.reidealize] for the model of the chosen method when a
molecule of it is stale, with the settings the model stored. Like the other ``tether.gui``
dialogs, the dialog holds a ``QDialog`` and imports Qt in ``__init__``, so everything else
here needs no Qt.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from tether.services.idealize import METHODS as IDEALIZE_METHODS
from tether.services.idealize import FitFacts, accepted_keys, read_fit_facts

if TYPE_CHECKING:
    from pyqtgraph.Qt import QtWidgets

    from tether.gui.tasks import CancelToken, ProgressReport
    from tether.project.core import Project
    from tether.project.idealize import StoredIdealization
    from tether.services.idealize import IdealizeResult

__all__ = [
    "METHOD_LABELS",
    "SIDECAR_REMEDY",
    "IdealizeProjectDialog",
    "ProjectFit",
    "fit_project",
    "fit_refusal",
    "outcome_message",
    "read_fit_facts",
    "stored_model",
]

#: The name the dialog and the status line give each method of ``IDEALIZE_METHODS``.
METHOD_LABELS: dict[str, str] = {"vbconhmm": "consensus VB-HMM", "ebhmm": "ebFRET"}

#: What to do when the sidecar cannot be found: the remedy of the troubleshooting entry
#: "Idealization cannot find the sidecar interpreter".
SIDECAR_REMEDY = (
    "Set TETHER_SIDECAR_PYTHON to the sidecar's Python (from a source checkout, "
    "scripts/setup_sidecar.py prints it), or check that the envs/sidecar folder is in the "
    "install folder. See Troubleshooting: Idealization cannot find the sidecar interpreter."
)

#: The largest fixed state count the dialog offers: a bound of the entry field only.
_MAX_FIXED_NSTATES = 20


@dataclass(frozen=True)
class ProjectFit:
    """The choices of one whole-project fit.

    ``nstates=None`` selects the state count by maximum ELBO over the default grid.
    ``condition=None`` fits the accepted molecules of every condition. With
    ``stale_only`` the fit refits the stored model of ``method`` with the settings it
    stored, so ``nstates`` and ``condition`` are not used.
    """

    method: str = IDEALIZE_METHODS[0]
    nstates: int | None = None
    condition: str | None = None
    stale_only: bool = False

    def __post_init__(self) -> None:
        """Refuse a method the command does not offer, and a state count below 1."""
        if self.method not in IDEALIZE_METHODS:
            raise ValueError(
                f"method must be one of {', '.join(IDEALIZE_METHODS)}, got {self.method!r}"
            )
        if self.nstates is not None and self.nstates < 1:
            raise ValueError(f"nstates must be 1 or more, got {self.nstates}")


def fit_refusal(fit: ProjectFit, facts: FitFacts) -> str | None:
    """Why *fit* cannot start on a project with *facts*, or ``None`` when it can."""
    if facts.sidecar_problem is not None:
        return f"The sidecar is not available: {facts.sidecar_problem}. {SIDECAR_REMEDY}"
    if fit.stale_only:
        model = facts.models.get(fit.method)
        if model is None:
            return f"The project has no {fit.method} model to refit. Fit the project first."
        if model.imported:
            return (
                f"The {fit.method} model was imported from standalone tMAVEN, so it cannot be "
                "refitted here. Hand its molecules to tMAVEN again."
            )
        if model.n_stale == 0:
            return f"No molecule of the {fit.method} model is stale. Nothing to refit."
        return None
    if facts.accepted.get(fit.condition, 0) == 0:
        where = "" if fit.condition is None else f" in condition {fit.condition}"
        return f"No molecule is accepted{where}. Accept molecules first."
    return None


def fit_project(
    project: Project,
    fit: ProjectFit,
    *,
    cancel: CancelToken | None = None,
    progress: ProgressReport | None = None,
    _runner: Any = None,
) -> IdealizeResult:
    """Run *fit* on *project*, whose handle holds the project's session lock.

    It runs on the worker thread of the window. A cancel stops it before the next sidecar
    fit, or at the latest just before the model is written: the project is then unchanged
    and [`TaskCancelled`][tether.gui.tasks.TaskCancelled] is raised. A cancel that comes
    after the write began is too late, and the fit ends as done.
    """
    from tether.services import idealize as service

    def checkpoint() -> None:
        if cancel is not None:
            cancel.raise_if_cancelled()

    def on_fit(done: int, total: int) -> None:
        checkpoint()
        if progress is not None:
            progress.report(done, total)

    if fit.stale_only:
        request = service.IdealizeRequest(
            project=str(project.path), stale_only=True, model_name=fit.method
        )
        keys = None
    else:
        request = service.IdealizeRequest(
            project=str(project.path), method=fit.method, nstates=fit.nstates, overwrite=True
        )
        keys = accepted_keys(project, fit.condition)
        if not keys:
            where = "" if fit.condition is None else f" in condition {fit.condition}"
            raise ValueError(f"no molecule is accepted{where}; accept molecules first")
    checkpoint()
    result = service.run_held(
        request,
        project,
        molecule_keys=keys,
        write_guard=checkpoint,
        on_fit=on_fit,
        _runner=_runner,
    )
    if progress is not None:
        progress.report(1, 1)
    return result


def stored_model(result: IdealizeResult) -> StoredIdealization | None:
    """Return the model a fit wrote: the fitted one, or the refitted one; else ``None``."""
    if result.fitted is not None:
        return result.fitted
    return next((r.refit for r in result.refits if r.refit is not None), None)


def outcome_message(fit: ProjectFit, result: IdealizeResult) -> str:
    """Return the status line after a fit: method, state count and molecules fitted."""
    label = METHOD_LABELS.get(fit.method, fit.method)
    stored = stored_model(result)
    if fit.stale_only:
        refit = next((r for r in result.refits if r.model_name == fit.method), None)
        if stored is None or refit is None:
            return f"Idealize project: no molecule of the {fit.method} model is stale"
        return (
            f"Refitted the {fit.method} model ({label}): {stored.nstates} states, "
            f"{stored.n_molecules} molecules fitted, {refit.n_stale} of them stale"
        )
    if stored is None:
        return "Idealize project: no model was written"
    return (
        f"Idealized the project with {label}: {stored.nstates} states, "
        f"{stored.n_molecules} molecules fitted (model {stored.model_name})"
    )


class IdealizeProjectDialog:
    """The "Idealize project…" dialog: the choices of a fit, and why one cannot start.

    It shows the method, the state count (automatic or fixed), the molecules (the accepted
    ones, by condition when the project holds several) and "Refit stale only". When the
    choice cannot start ([`fit_refusal`][]), the reason shows and **Idealize** is disabled;
    so a missing sidecar is named, with its remedy, before anything starts.
    """

    def __init__(self, facts: FitFacts, *, parent: QtWidgets.QWidget | None = None) -> None:
        from pyqtgraph.Qt import QtWidgets

        self._facts = facts
        dialog = QtWidgets.QDialog(parent)
        dialog.setWindowTitle("Idealize project")
        self._dialog = dialog
        form = QtWidgets.QFormLayout()

        self._method = QtWidgets.QComboBox()
        for method in IDEALIZE_METHODS:
            self._method.addItem(f"{METHOD_LABELS.get(method, method)} ({method})", method)
        form.addRow("Method", self._method)

        self._auto = QtWidgets.QRadioButton("Automatic (largest ELBO)")
        self._fixed = QtWidgets.QRadioButton("Fixed")
        self._auto.setChecked(True)
        self._nstates = QtWidgets.QSpinBox()
        self._nstates.setRange(1, _MAX_FIXED_NSTATES)
        self._nstates.setValue(2)
        row = QtWidgets.QHBoxLayout()
        for widget in (self._auto, self._fixed, self._nstates):
            row.addWidget(widget)
        form.addRow("State count", row)

        self._molecules = QtWidgets.QComboBox()
        self._molecules.addItem(f"Accepted molecules ({facts.accepted.get(None, 0)})", None)
        for condition in facts.conditions:
            count = facts.accepted.get(condition, 0)
            self._molecules.addItem(f"Accepted in condition {condition} ({count})", condition)
        form.addRow("Molecules", self._molecules)

        self._stale_only = QtWidgets.QCheckBox("Refit stale only")
        self._stale_only.setToolTip(
            "Refit the stored model of this method with the settings it stored, when a "
            "molecule of it is stale."
        )
        form.addRow("", self._stale_only)

        self._replaces = QtWidgets.QLabel()
        self._replaces.setWordWrap(True)
        self._reason = QtWidgets.QLabel()
        self._reason.setWordWrap(True)
        self._reason.setObjectName("idealize-project-reason")
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        self._ok = buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok)
        self._ok.setText("Idealize")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)

        layout = QtWidgets.QVBoxLayout(dialog)
        layout.addLayout(form)
        layout.addWidget(self._replaces)
        layout.addWidget(self._reason)
        layout.addWidget(buttons)

        for signal in (
            self._method.currentIndexChanged,
            self._molecules.currentIndexChanged,
            self._auto.toggled,
            self._stale_only.toggled,
            self._nstates.valueChanged,
        ):
            signal.connect(self._sync)
        self._sync()

    @property
    def dialog(self) -> QtWidgets.QDialog:
        """The ``QDialog`` itself."""
        return self._dialog

    @property
    def ok_button(self) -> QtWidgets.QPushButton:
        """The **Idealize** button; disabled while the choice cannot start."""
        return self._ok

    @property
    def reason(self) -> str:
        """Why the choice cannot start, or ``""`` when it can."""
        return self._reason.text()

    @property
    def replaces(self) -> str:
        """The note that the fit replaces a stored model, or ``""``."""
        return self._replaces.text()

    def set_choice(self, fit: ProjectFit) -> None:
        """Show *fit* in the dialog (a test and a caller use it to preset the choice)."""
        self._method.setCurrentIndex(self._method.findData(fit.method))
        if fit.nstates is None:
            self._auto.setChecked(True)
        else:
            self._fixed.setChecked(True)
            self._nstates.setValue(fit.nstates)
        index = self._molecules.findData(fit.condition)
        self._molecules.setCurrentIndex(max(index, 0))
        self._stale_only.setChecked(fit.stale_only)
        self._sync()

    def choice(self) -> ProjectFit:
        """Return the fit the dialog shows now."""
        stale_only = self._stale_only.isChecked()
        return ProjectFit(
            method=self._method.currentData(),
            nstates=None if stale_only or self._auto.isChecked() else self._nstates.value(),
            condition=None if stale_only else self._molecules.currentData(),
            stale_only=stale_only,
        )

    def _sync(self, *_args: object) -> None:
        stale_only = self._stale_only.isChecked()
        for widget in (self._auto, self._fixed, self._molecules):
            widget.setEnabled(not stale_only)
        self._nstates.setEnabled(not stale_only and self._fixed.isChecked())
        fit = self.choice()
        model = self._facts.models.get(fit.method)
        if model is not None and not stale_only:
            self._replaces.setText(f"The fit replaces the stored {fit.method} model.")
        else:
            self._replaces.setText("")
        refusal = fit_refusal(fit, self._facts)
        self._reason.setText(refusal or "")
        self._ok.setEnabled(refusal is None)
