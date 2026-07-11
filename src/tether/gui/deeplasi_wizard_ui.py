# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Deep-LASI re-analysis wizard UI (PRD §7.8, FR-LEGACY, M7 PR #5).

:class:`DeepLasiWizardDialog` is the GUI surface for the legacy re-analysis
workflow: point it at a folder of Deep-LASI acquisition files, review and adjust
the proposed per-acquisition plan, then run the import — writing one ``.tether``
per acquisition **without re-extraction**. It is a thin renderer + navigation
shell over two already-frozen, fully-tested headless layers:

* the **controller** :class:`~tether.gui.deeplasi_wizard.DeepLasiWizard` (M7 PR
  #5a, ``#127``) — the editable plan state machine. Every plan edit routes through
  its validated mutators (``set_mode`` / ``set_coordinate_source`` /
  ``set_output_name``), so the controller stays the single source of truth for
  what an acquisition's files can support; an unsupported edit raises
  :class:`~tether.gui.deeplasi_wizard.WizardError` and this dialog reverts the
  widget and surfaces the reason (rather than duplicating the capability logic).
* the **executor** :func:`~tether.gui.deeplasi_executor.execute_plan` (M7 PR #5b,
  ``#128``) — runs a finalized :class:`~tether.gui.deeplasi_wizard.WizardPlan`.
  It is injected (``runner=``) so tests drive the UI wiring without re-running the
  importers (whose correctness the executor's own suite already covers).

Three steps, held in a :class:`~qtpy.QtWidgets.QStackedWidget`:

1. **Intake** — choose a folder (+ optional recursive scan) →
   :meth:`~tether.gui.deeplasi_wizard.DeepLasiWizard.from_directory` builds the
   default plan. *Next* is enabled once a scan discovers ≥1 acquisition.
2. **Confirm** — a per-acquisition table (mode / coordinate source / output name)
   plus the run destination. A live status line mirrors
   :meth:`~tether.gui.deeplasi_wizard.DeepLasiWizard.summary` (mode counts,
   advisories, and the reasons a run is blocked). *Run* is enabled only when the
   plan :attr:`~tether.gui.deeplasi_wizard.DeepLasiWizard.is_ready` and a
   destination is set.
3. **Report** — the per-acquisition :class:`~tether.gui.deeplasi_executor.ExecutionReport`
   (imported / failed, with any non-fatal warnings). The produced ``.tether``
   paths are exposed via :meth:`produced_projects` for the shell to open live.

Like the other ``tether.gui`` dialogs this **holds** a ``QDialog`` rather than
subclassing one (so importing this module costs no Qt), and imports Qt lazily
inside ``__init__``. Constructing a dialog needs a live ``QApplication`` (the
shell / ``qtbot`` provide one).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from tether.gui.deeplasi_wizard import DeepLasiWizard, WizardError, WizardMode

if TYPE_CHECKING:
    from collections.abc import Callable
    from os import PathLike

    from pyqtgraph.Qt import QtWidgets

    from tether.gui.deeplasi_executor import ExecutionReport
    from tether.gui.deeplasi_wizard import PlannedAcquisition

    #: A callable with :func:`~tether.gui.deeplasi_executor.execute_plan`'s shape.
    ExecuteRunner = Callable[..., ExecutionReport]

__all__ = ["DeepLasiWizardDialog"]

#: The stack index of each step (also the value of :attr:`DeepLasiWizardDialog.step`).
_STEP_INTAKE = 0
_STEP_CONFIRM = 1
_STEP_REPORT = 2

#: Human labels for the three modes, in the mode combo's fixed order.
_MODE_LABELS: tuple[tuple[WizardMode, str], ...] = (
    (WizardMode.RECONSTRUCT, "Reconstruct (round-trip)"),
    (WizardMode.ANALYSIS_ONLY, "Analysis-only"),
    (WizardMode.SKIP, "Skip"),
)

#: Confirm-table column order.
_COL_KEY = 0
_COL_MODE = 1
_COL_COORDS = 2
_COL_OUTPUT = 3
_COL_NOTES = 4
_CONFIRM_COLUMNS = ("Acquisition", "Mode", "Coordinates", "Output name", "Notes")

#: Report-table column order.
_REPORT_COLUMNS = ("Acquisition", "Mode", "Result", "Notes")


class DeepLasiWizardDialog:
    """A three-step folder → plan → import wizard for Deep-LASI re-analysis (§7.8)."""

    def __init__(
        self,
        *,
        wizard: DeepLasiWizard | None = None,
        runner: ExecuteRunner | None = None,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        """Build the dialog.

        Parameters
        ----------
        wizard
            A pre-built :class:`~tether.gui.deeplasi_wizard.DeepLasiWizard` (e.g. the
            shell scanned the folder itself). When given the dialog opens on the
            **Confirm** step; when ``None`` it opens on **Intake** and builds the
            controller from the chosen folder.
        runner
            The executor to run the finalized plan; defaults (lazily) to
            :func:`~tether.gui.deeplasi_executor.execute_plan`. Injected so a test can
            drive the confirm→report transition without re-running the importers.
        parent
            The Qt parent widget (the shell window), or ``None``.
        """
        from pyqtgraph.Qt import QtWidgets

        self._runner = runner
        self._wizard = wizard
        self._report: ExecutionReport | None = None
        self._produced: tuple[Path, ...] = ()
        # Per-acquisition edit widgets, keyed by the plan's stable grouping key, and
        # the last-committed mode (so an edit the controller rejects can be reverted
        # without re-firing the change handler).
        self._mode_combos: dict[str, QtWidgets.QComboBox] = {}
        self._coord_combos: dict[str, QtWidgets.QComboBox] = {}
        self._output_edits: dict[str, QtWidgets.QLineEdit] = {}
        self._last_mode: dict[str, WizardMode] = {}

        self._dialog = QtWidgets.QDialog(parent)
        self._dialog.setWindowTitle("Deep-LASI re-analysis")
        self._dialog.resize(760, 520)
        outer = QtWidgets.QVBoxLayout(self._dialog)

        self._stack = QtWidgets.QStackedWidget()
        self._stack.addWidget(self._build_intake_page())
        self._stack.addWidget(self._build_confirm_page())
        self._stack.addWidget(self._build_report_page())
        outer.addWidget(self._stack, stretch=1)

        self._status = QtWidgets.QLabel("")
        self._status.setWordWrap(True)
        outer.addWidget(self._status)

        outer.addLayout(self._build_nav())

        if wizard is not None:
            self._populate_confirm_table()
            self._go_to(_STEP_CONFIRM)
        else:
            self._go_to(_STEP_INTAKE)

    # ------------------------------------------------------------------ #
    # page construction
    # ------------------------------------------------------------------ #

    def _build_intake_page(self) -> QtWidgets.QWidget:
        from pyqtgraph.Qt import QtWidgets

        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.addWidget(
            QtWidgets.QLabel(
                "Choose a folder of Deep-LASI acquisition files "
                "(.tif movie, .tdat, .mat, .txt, or SMD)."
            )
        )
        row = QtWidgets.QHBoxLayout()
        self._dir_edit = QtWidgets.QLineEdit()
        self._dir_edit.setPlaceholderText("Acquisition folder…")
        # Editing the folder (or the recursive toggle) after a scan invalidates the
        # cached plan, so a stale one can never be advanced without re-scanning.
        self._dir_edit.textChanged.connect(self._invalidate_scan)
        row.addWidget(self._dir_edit, stretch=1)
        self._browse_btn = QtWidgets.QPushButton("Browse…")
        self._browse_btn.clicked.connect(self._choose_directory)
        row.addWidget(self._browse_btn)
        layout.addLayout(row)

        self._recursive_box = QtWidgets.QCheckBox("Search sub-folders")
        self._recursive_box.toggled.connect(self._invalidate_scan)
        layout.addWidget(self._recursive_box)

        self._scan_btn = QtWidgets.QPushButton("Scan folder")
        self._scan_btn.clicked.connect(self.scan)
        layout.addWidget(self._scan_btn)

        self._intake_status = QtWidgets.QLabel("")
        self._intake_status.setWordWrap(True)
        layout.addWidget(self._intake_status)
        layout.addStretch(1)
        return page

    def _build_confirm_page(self) -> QtWidgets.QWidget:
        from pyqtgraph.Qt import QtWidgets

        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.addWidget(
            QtWidgets.QLabel("Review each acquisition's planned import, then choose a destination.")
        )
        self._table = QtWidgets.QTableWidget(0, len(_CONFIRM_COLUMNS))
        self._table.setHorizontalHeaderLabels(list(_CONFIRM_COLUMNS))
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
        layout.addWidget(self._table, stretch=1)

        dest = QtWidgets.QHBoxLayout()
        dest.addWidget(QtWidgets.QLabel("Write projects to:"))
        self._outdir_edit = QtWidgets.QLineEdit()
        self._outdir_edit.setPlaceholderText("Destination folder…")
        self._outdir_edit.textChanged.connect(self._refresh_confirm)
        dest.addWidget(self._outdir_edit, stretch=1)
        self._outdir_btn = QtWidgets.QPushButton("Browse…")
        self._outdir_btn.clicked.connect(self._choose_output_dir)
        dest.addWidget(self._outdir_btn)
        layout.addLayout(dest)

        self._detect_bleach_box = QtWidgets.QCheckBox("Detect photobleaching on import")
        self._detect_bleach_box.setChecked(True)
        layout.addWidget(self._detect_bleach_box)

        self._summary_label = QtWidgets.QLabel("")
        self._summary_label.setWordWrap(True)
        layout.addWidget(self._summary_label)
        return page

    def _build_report_page(self) -> QtWidgets.QWidget:
        from pyqtgraph.Qt import QtWidgets

        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        self._report_heading = QtWidgets.QLabel("")
        self._report_heading.setWordWrap(True)
        layout.addWidget(self._report_heading)
        self._report_table = QtWidgets.QTableWidget(0, len(_REPORT_COLUMNS))
        self._report_table.setHorizontalHeaderLabels(list(_REPORT_COLUMNS))
        self._report_table.verticalHeader().setVisible(False)
        self._report_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self._report_table, stretch=1)
        return page

    def _build_nav(self) -> QtWidgets.QHBoxLayout:
        from pyqtgraph.Qt import QtWidgets

        nav = QtWidgets.QHBoxLayout()
        self._back_btn = QtWidgets.QPushButton("Back")
        self._back_btn.clicked.connect(self._go_back)
        nav.addWidget(self._back_btn)
        nav.addStretch(1)
        self._next_btn = QtWidgets.QPushButton("Next")
        self._next_btn.clicked.connect(self._advance)
        nav.addWidget(self._next_btn)
        self._close_btn = QtWidgets.QPushButton("Close")
        self._close_btn.clicked.connect(self._dialog.reject)
        nav.addWidget(self._close_btn)
        return nav

    # ------------------------------------------------------------------ #
    # intake step
    # ------------------------------------------------------------------ #

    def _choose_directory(self) -> None:
        """Open a native folder picker and copy the choice into the folder field."""
        from pyqtgraph.Qt import QtWidgets

        chosen = QtWidgets.QFileDialog.getExistingDirectory(
            self._dialog, "Choose a Deep-LASI acquisition folder", self._dir_edit.text()
        )
        if chosen:
            self._dir_edit.setText(chosen)

    def set_directory(self, directory: str | PathLike[str]) -> None:
        """Set the intake folder text (the test/shell seam that bypasses the picker)."""
        self._dir_edit.setText(str(directory))

    def _invalidate_scan(self) -> None:
        """Drop a stale plan when the intake inputs change, forcing a fresh scan.

        Once a folder is scanned, editing the folder text or the recursive toggle must
        not leave the previous folder's plan runnable — otherwise *Next* could advance
        (or a shell caller could read via :meth:`produced_projects`) acquisitions from
        the folder the user just navigated away from. Clears the controller + confirm
        table and disables *Next* until :meth:`scan` succeeds again. A no-op when no
        plan is cached yet (so typing the first folder path stays quiet).
        """
        if self._wizard is None:
            return
        self._wizard = None
        self._table.setRowCount(0)
        self._mode_combos.clear()
        self._coord_combos.clear()
        self._output_edits.clear()
        self._last_mode.clear()
        self._intake_status.setText("Folder changed — scan again to refresh the plan.")
        self._update_nav()

    def scan(self) -> None:
        """Scan the chosen folder into a controller and populate the confirm table.

        Filename-only discovery (no file contents are read), so an unreadable folder
        is the only failure mode — surfaced on the intake status line rather than
        raised. A successful scan enables *Next*.
        """
        directory = self._dir_edit.text().strip()
        if not directory:
            self._intake_status.setText("Choose a folder to scan first.")
            return
        try:
            wizard = DeepLasiWizard.from_directory(
                directory, recursive=self._recursive_box.isChecked()
            )
        except (OSError, ValueError) as exc:  # unreadable / missing folder
            self._wizard = None
            self._intake_status.setText(f"Could not scan the folder: {exc}")
            self._update_nav()
            return
        self._wizard = wizard
        n = len(wizard.plans)
        if n == 0:
            self._intake_status.setText(
                "No Deep-LASI acquisitions found in that folder. Try another, or enable "
                "sub-folder search."
            )
        else:
            self._intake_status.setText(
                f"Discovered {n} acquisition(s). Continue to review the import plan."
            )
        self._populate_confirm_table()
        self._update_nav()

    # ------------------------------------------------------------------ #
    # confirm step — render the plan + wire the edit widgets to the controller
    # ------------------------------------------------------------------ #

    def _populate_confirm_table(self) -> None:
        from pyqtgraph.Qt import QtWidgets

        self._mode_combos.clear()
        self._coord_combos.clear()
        self._output_edits.clear()
        self._last_mode.clear()
        plans = self._wizard.plans if self._wizard is not None else ()
        self._table.setRowCount(len(plans))
        for row, plan in enumerate(plans):
            key = plan.key
            self._last_mode[key] = plan.mode

            item = QtWidgets.QTableWidgetItem(key)
            item.setToolTip(key)
            self._table.setItem(row, _COL_KEY, item)

            mode_combo = QtWidgets.QComboBox()
            for mode, label in _MODE_LABELS:
                mode_combo.addItem(label, mode.value)
            mode_combo.setCurrentIndex(self._mode_index(plan.mode))
            mode_combo.currentIndexChanged.connect(lambda _idx, k=key: self._on_mode_changed(k))
            self._table.setCellWidget(row, _COL_MODE, mode_combo)
            self._mode_combos[key] = mode_combo

            coord_combo = QtWidgets.QComboBox()
            for source in self._coordinate_sources(plan):
                coord_combo.addItem(source, source)
            self._select_coord(coord_combo, plan.coordinate_source)
            coord_combo.setEnabled(plan.mode is WizardMode.RECONSTRUCT and coord_combo.count() > 0)
            coord_combo.currentIndexChanged.connect(lambda _idx, k=key: self._on_coord_changed(k))
            self._table.setCellWidget(row, _COL_COORDS, coord_combo)
            self._coord_combos[key] = coord_combo

            output_edit = QtWidgets.QLineEdit(plan.output_name)
            output_edit.editingFinished.connect(lambda k=key: self._on_output_changed(k))
            self._table.setCellWidget(row, _COL_OUTPUT, output_edit)
            self._output_edits[key] = output_edit

            notes = QtWidgets.QTableWidgetItem(self._notes_text(plan))
            notes.setToolTip("\n".join((plan.rationale, *plan.warnings)))
            self._table.setItem(row, _COL_NOTES, notes)

        self._table.resizeColumnsToContents()
        self._refresh_confirm()

    def _on_mode_changed(self, key: str) -> None:
        """Commit a mode edit to the controller, reverting on an unsupported choice."""
        combo = self._mode_combos[key]
        mode = WizardMode(combo.currentData())
        try:
            self._wizard.set_mode(key, mode)
        except WizardError as exc:
            self._status.setText(str(exc))
            self._set_combo_mode(combo, self._last_mode[key])
            return
        self._last_mode[key] = mode
        self._status.setText("")
        # The controller repairs the coordinate source when re-entering reconstruct;
        # mirror its state and re-gate the coordinate combo.
        self._sync_coord_combo(key)
        self._refresh_confirm()

    def _on_coord_changed(self, key: str) -> None:
        combo = self._coord_combos[key]
        source = combo.currentData()
        if source is None:  # empty combo
            return
        try:
            self._wizard.set_coordinate_source(key, source)
        except WizardError as exc:
            self._status.setText(str(exc))
            self._sync_coord_combo(key)
            return
        self._status.setText("")
        self._refresh_confirm()

    def _on_output_changed(self, key: str) -> None:
        edit = self._output_edits[key]
        name = edit.text().strip()
        try:
            plan = self._wizard.set_output_name(key, name)
        except WizardError as exc:
            self._status.setText(str(exc))
            # Restore the controller's current (unchanged) name.
            _, current = self._locate_plan(key)
            edit.setText(current.output_name)
            return
        # Reflect any normalization the controller applied (e.g. trimming).
        if edit.text() != plan.output_name:
            edit.setText(plan.output_name)
        self._status.setText("")
        self._refresh_confirm()

    def _refresh_confirm(self) -> None:
        """Recompute the summary line and the *Run* button's enabled state."""
        if self._wizard is None:
            self._summary_label.setText("")
            self._update_nav()
            return
        summary = self._wizard.summary()
        parts = [
            f"Reconstruct {summary.n_reconstruct} · analysis-only "
            f"{summary.n_analysis_only} · skip {summary.n_skipped}"
        ]
        parts.extend(summary.advisories)
        if summary.blocking:
            parts.append("Cannot run yet: " + "; ".join(summary.blocking))
        elif not self._outdir_edit.text().strip():
            parts.append("Choose a destination folder to run.")
        self._summary_label.setText("\n".join(parts))
        self._update_nav()

    # ------------------------------------------------------------------ #
    # run + report step
    # ------------------------------------------------------------------ #

    def _choose_output_dir(self) -> None:
        from pyqtgraph.Qt import QtWidgets

        chosen = QtWidgets.QFileDialog.getExistingDirectory(
            self._dialog, "Choose where to write the projects", self._outdir_edit.text()
        )
        if chosen:
            self._outdir_edit.setText(chosen)

    def set_output_dir(self, directory: str | PathLike[str]) -> None:
        """Set the destination folder text (the test/shell seam that bypasses the picker)."""
        self._outdir_edit.setText(str(directory))

    def run(self) -> ExecutionReport | None:
        """Finalize the plan and run the executor, advancing to the report step.

        Returns the :class:`~tether.gui.deeplasi_executor.ExecutionReport`, or ``None``
        when the plan is not ready / has no destination (the reason is shown inline).
        The executor is fail-soft, so a per-acquisition failure still returns a report;
        only an unexpected error (e.g. the destination cannot be created) is surfaced.
        """
        from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

        if self._wizard is None or not self._wizard.is_ready:
            self._status.setText("The plan is not ready to run yet.")
            return None
        output_dir = self._outdir_edit.text().strip()
        if not output_dir:
            self._status.setText("Choose a destination folder before running.")
            return None

        runner = self._runner
        if runner is None:
            from tether.gui.deeplasi_executor import execute_plan

            runner = execute_plan

        plan = self._wizard.finalize()
        QtWidgets.QApplication.setOverrideCursor(QtGui.QCursor(QtCore.Qt.CursorShape.WaitCursor))
        try:
            report = runner(
                plan, output_dir, detect_photobleach=self._detect_bleach_box.isChecked()
            )
        except (OSError, ValueError) as exc:
            self._status.setText(f"Import failed to start: {exc}")
            return None
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

        self._report = report
        self._produced = tuple(e.output_path for e in report.succeeded)
        self._render_report(report)
        self._status.setText("")
        self._go_to(_STEP_REPORT)
        return report

    def _render_report(self, report: ExecutionReport) -> None:
        from pyqtgraph.Qt import QtWidgets

        self._report_heading.setText(
            f"Imported {report.n_ok} of {len(report.executed)} acquisition(s)"
            + (f" · {report.n_failed} failed" if report.n_failed else "")
            + f" → {report.output_dir}"
        )
        self._report_table.setRowCount(len(report.executed))
        for row, executed in enumerate(report.executed):
            self._report_table.setItem(row, 0, QtWidgets.QTableWidgetItem(executed.key))
            self._report_table.setItem(row, 1, QtWidgets.QTableWidgetItem(str(executed.mode.value)))
            if executed.ok:
                result = "Imported"
                if executed.coordinate_source:
                    result += f" · coords={executed.coordinate_source}"
            else:
                result = f"Failed · {executed.error}"
            self._report_table.setItem(row, 2, QtWidgets.QTableWidgetItem(result))
            notes = QtWidgets.QTableWidgetItem("; ".join(executed.warnings))
            notes.setToolTip("\n".join(executed.warnings))
            self._report_table.setItem(row, 3, notes)
        self._report_table.resizeColumnsToContents()

    # ------------------------------------------------------------------ #
    # navigation
    # ------------------------------------------------------------------ #

    def _go_to(self, step: int) -> None:
        self._stack.setCurrentIndex(step)
        self._update_nav()

    def _go_back(self) -> None:
        if self.step == _STEP_CONFIRM:
            self._go_to(_STEP_INTAKE)

    def _advance(self) -> None:
        if self.step == _STEP_INTAKE:
            self._go_to(_STEP_CONFIRM)
        elif self.step == _STEP_CONFIRM:
            self.run()

    def _update_nav(self) -> None:
        step = self.step
        self._back_btn.setVisible(step == _STEP_CONFIRM)
        if step == _STEP_INTAKE:
            self._next_btn.setVisible(True)
            self._next_btn.setText("Next")
            self._next_btn.setEnabled(self._wizard is not None and len(self._wizard.plans) > 0)
        elif step == _STEP_CONFIRM:
            self._next_btn.setVisible(True)
            self._next_btn.setText("Run")
            ready = self._wizard is not None and self._wizard.is_ready
            self._next_btn.setEnabled(ready and bool(self._outdir_edit.text().strip()))
        else:  # report
            self._next_btn.setVisible(False)

    # ------------------------------------------------------------------ #
    # small helpers
    # ------------------------------------------------------------------ #

    def _locate_plan(self, key: str) -> tuple[int, PlannedAcquisition]:
        for i, plan in enumerate(self._wizard.plans):
            if plan.key == key:
                return i, plan
        raise KeyError(key)

    @staticmethod
    def _mode_index(mode: WizardMode) -> int:
        for i, (candidate, _label) in enumerate(_MODE_LABELS):
            if candidate is mode:
                return i
        return 0

    def _set_combo_mode(self, combo: QtWidgets.QComboBox, mode: WizardMode) -> None:
        """Set a mode combo without re-firing its change handler."""
        blocked = combo.blockSignals(True)
        combo.setCurrentIndex(self._mode_index(mode))
        combo.blockSignals(blocked)

    @staticmethod
    def _coordinate_sources(plan: PlannedAcquisition) -> tuple[str, ...]:
        """The coordinate sources the acquisition's files offer (``tdat`` before ``mat``)."""
        fs = plan.fileset
        sources = []
        if fs.tdat is not None:
            sources.append("tdat")
        if fs.mat is not None:
            sources.append("mat")
        return tuple(sources)

    @staticmethod
    def _select_coord(combo: QtWidgets.QComboBox, source: str) -> None:
        idx = combo.findData(source)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def _sync_coord_combo(self, key: str) -> None:
        """Mirror the controller's coordinate source + reconstruct-only enablement."""
        combo = self._coord_combos[key]
        _, plan = self._locate_plan(key)
        blocked = combo.blockSignals(True)
        self._select_coord(combo, plan.coordinate_source)
        combo.blockSignals(blocked)
        combo.setEnabled(plan.mode is WizardMode.RECONSTRUCT and combo.count() > 0)

    @staticmethod
    def _notes_text(plan: PlannedAcquisition) -> str:
        if plan.warnings:
            return f"{plan.rationale} — {plan.warnings[0]}"
        return plan.rationale

    # ------------------------------------------------------------------ #
    # public surface (accessors + modal entry)
    # ------------------------------------------------------------------ #

    @property
    def dialog(self) -> QtWidgets.QDialog:
        """The held ``QDialog``."""
        return self._dialog

    @property
    def wizard(self) -> DeepLasiWizard | None:
        """The controller behind the current plan (``None`` before a scan)."""
        return self._wizard

    @property
    def report(self) -> ExecutionReport | None:
        """The last run's report, or ``None`` before a run."""
        return self._report

    @property
    def step(self) -> int:
        """The active step index (``0`` intake, ``1`` confirm, ``2`` report)."""
        return self._stack.currentIndex()

    def produced_projects(self) -> tuple[Path, ...]:
        """The ``.tether`` paths written by the last run (for the shell to open live)."""
        return self._produced

    def mode_combo(self, key: str) -> QtWidgets.QComboBox:
        """The mode combo for an acquisition row (raises ``KeyError`` if absent)."""
        return self._mode_combos[key]

    def coordinate_combo(self, key: str) -> QtWidgets.QComboBox:
        """The coordinate-source combo for an acquisition row."""
        return self._coord_combos[key]

    def output_edit(self, key: str) -> QtWidgets.QLineEdit:
        """The output-name line edit for an acquisition row."""
        return self._output_edits[key]

    def summary_text(self) -> str:
        """The confirm-step status line (mode counts + advisories + blocking reasons)."""
        return self._summary_label.text()

    def exec(self) -> tuple[Path, ...]:
        """Show the dialog modally; return the produced ``.tether`` paths (empty if none)."""
        self._dialog.exec()
        return self._produced
