# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The per-trace tMAVEN return-leg reconcile prompt (PRD §7.4/§5.3, FR-IDEALIZE).

:class:`ReconcileDialog` is the GUI surface for the return leg of the standalone
tMAVEN hand-off (M2 S7 PR-B). Its headless core landed in
:mod:`tether.project.handoff` (PR-A): :func:`~tether.project.handoff.read_return_leg`
intensity-matches a returning SMD to the store and produces a
:class:`~tether.project.handoff.ReconcileReport` — the per-trace diff of
analysis-window edits and tMAVEN integer-class changes plus the unmatched report —
and :func:`~tether.project.handoff.apply_reconcile` commits the accepted subset
non-destructively. This dialog renders that report as a per-trace **accept/reject**
prompt and hands back a :class:`ReconcileDecision` the shell feeds straight to
``apply_reconcile``.

Design notes:

* **Accept per facet.** Each matched trace with a change gets an *Accept window* and
  an *Accept class* checkbox, enabled only for the facet that actually changed. A
  non-zero tMAVEN class has no free-text category mapping until the M4 editable list
  (§7.6), so its ``class_change.applicable`` is ``False`` — the row is **surfaced**
  (so the curator sees it) but its Accept box stays disabled (it cannot be committed
  at M2, matching the headless layer).
* **Idealization import** is a single project-level checkbox, enabled only when the
  preview carried a tMAVEN model (``report.has_idealization``).
* **Unmatched traces** are shown read-only (reported, never guessed — §5.3).
* **Composition wrapper.** Like the other ``tether.gui`` dialogs it *holds* a
  ``QDialog`` rather than subclassing one, and imports Qt lazily inside ``__init__``
  so importing this module (and the Qt-free :class:`ReconcileDecision`) costs no Qt.
  Constructing a dialog needs a live ``QApplication`` (the shell / ``qtbot`` provide
  one).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyqtgraph.Qt import QtWidgets

    from tether.project.handoff import ReconcileReport, TraceReconcile

__all__ = ["ReconcileDecision", "ReconcileDialog"]


@dataclass(frozen=True)
class ReconcileDecision:
    """The accepted subset of a return-leg reconcile, keyed by ``molecule_id``.

    Maps one-to-one onto :func:`tether.project.handoff.apply_reconcile`'s
    ``accept_windows`` / ``accept_classes`` / ``import_idealization`` arguments (the
    empty tuple is a valid "accept none" spec).
    """

    accept_windows: tuple[str, ...] = ()
    accept_classes: tuple[str, ...] = ()
    import_idealization: bool = False

    @property
    def is_empty(self) -> bool:
        """Whether nothing was accepted (no commit would change the store)."""
        return not (self.accept_windows or self.accept_classes or self.import_idealization)


def _window_text(trace: TraceReconcile) -> str:
    """``lo–hi → lo–hi`` for an analysis-window edit, or ``—`` when unchanged."""
    wc = trace.window_change
    if wc is None:
        return "—"
    return f"{wc.old[0]}–{wc.old[1]} → {wc.new[0]}–{wc.new[1]}"


def _class_text(trace: TraceReconcile) -> str:
    """Human text for a class change: the applicable proposal or the M4-deferred note."""
    cc = trace.class_change
    if cc is None:
        return "—"
    if cc.applicable:
        target = cc.proposed_category or "uncategorized"
        return f"class {cc.returned_class} → «{target}»"
    store = cc.store_category or "uncategorized"
    return f"class {cc.returned_class} vs «{store}» (M4-deferred)"


class ReconcileDialog:
    """A per-trace accept/reject prompt for a return-leg :class:`ReconcileReport`."""

    #: Table column headers (the two "Accept" columns are the per-facet toggles).
    _COLUMNS = ("Molecule", "Analysis-window change", "Accept", "tMAVEN class change", "Accept")

    def __init__(self, report: ReconcileReport, *, parent: QtWidgets.QWidget | None = None) -> None:
        from pyqtgraph.Qt import QtWidgets

        self._report = report
        # Only rows that actually changed are offered; an unchanged matched trace has
        # nothing to reconcile. A deferred (non-applicable) class still counts as a
        # change so it is surfaced, even though its Accept box stays disabled.
        self._rows: list[TraceReconcile] = [t for t in report.matched if t.has_changes]
        self._window_boxes: dict[str, QtWidgets.QCheckBox] = {}
        self._class_boxes: dict[str, QtWidgets.QCheckBox] = {}

        self._dialog = QtWidgets.QDialog(parent)
        self._dialog.setWindowTitle("Reconcile tMAVEN return leg")
        layout = QtWidgets.QVBoxLayout(self._dialog)
        layout.addWidget(QtWidgets.QLabel(self._header_text()))

        self._table = QtWidgets.QTableWidget(len(self._rows), len(self._COLUMNS))
        self._table.setHorizontalHeaderLabels(list(self._COLUMNS))
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        for row, trace in enumerate(self._rows):
            self._table.setItem(row, 0, QtWidgets.QTableWidgetItem(trace.molecule_key))
            self._table.setItem(row, 1, QtWidgets.QTableWidgetItem(_window_text(trace)))
            win_box = QtWidgets.QCheckBox()
            win_box.setEnabled(trace.window_change is not None)
            self._table.setCellWidget(row, 2, win_box)
            self._window_boxes[trace.molecule_id] = win_box
            self._table.setItem(row, 3, QtWidgets.QTableWidgetItem(_class_text(trace)))
            cls_box = QtWidgets.QCheckBox()
            cls_box.setEnabled(trace.class_change is not None and trace.class_change.applicable)
            self._table.setCellWidget(row, 4, cls_box)
            self._class_boxes[trace.molecule_id] = cls_box
        self._table.resizeColumnsToContents()
        layout.addWidget(self._table)
        if not self._rows:
            layout.addWidget(
                QtWidgets.QLabel("No analysis-window or category changes to reconcile.")
            )

        # Project-level idealization import (enabled only when a model was previewed).
        self._import_box = QtWidgets.QCheckBox(self._import_text())
        self._import_box.setEnabled(report.has_idealization)
        layout.addWidget(self._import_box)

        if report.unmatched_returned:
            rows_txt = ", ".join(str(i) for i in report.unmatched_returned)
            layout.addWidget(
                QtWidgets.QLabel(
                    f"{report.n_unmatched} returning trace(s) matched no store molecule "
                    f"(rows: {rows_txt}) — reported, not imported."
                )
            )

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        self._select_all_button = buttons.addButton(
            "Select all applicable", QtWidgets.QDialogButtonBox.ButtonRole.ActionRole
        )
        self._select_all_button.clicked.connect(self.select_all)
        buttons.accepted.connect(self._dialog.accept)
        buttons.rejected.connect(self._dialog.reject)
        layout.addWidget(buttons)

    # --- text --------------------------------------------------------------- #

    def _header_text(self) -> str:
        r = self._report
        return (
            f"Matched {r.n_matched} · unmatched {r.n_unmatched} · "
            f"intensity «{r.intensity_quantity}»"
        )

    def _import_text(self) -> str:
        r = self._report
        if not r.has_idealization:
            return "Import idealization — no tMAVEN model provided"
        summary = ""
        if r.imported_model_type is not None:
            summary = f" ({r.imported_model_type}, {r.imported_nstates} states)"
        return f"Import idealization → /idealization/{r.model_name}{summary}"

    # --- accessors ---------------------------------------------------------- #

    @property
    def dialog(self) -> QtWidgets.QDialog:
        return self._dialog

    @property
    def report(self) -> ReconcileReport:
        return self._report

    @property
    def import_checkbox(self) -> QtWidgets.QCheckBox:
        return self._import_box

    def change_rows(self) -> list[TraceReconcile]:
        """The matched traces shown as reconcilable rows (those with a change)."""
        return list(self._rows)

    def window_checkbox(self, molecule_id: str) -> QtWidgets.QCheckBox:
        """The *Accept window* checkbox for a row (raises ``KeyError`` if absent)."""
        return self._window_boxes[molecule_id]

    def class_checkbox(self, molecule_id: str) -> QtWidgets.QCheckBox:
        """The *Accept class* checkbox for a row (raises ``KeyError`` if absent)."""
        return self._class_boxes[molecule_id]

    # --- behaviour ---------------------------------------------------------- #

    def select_all(self) -> None:
        """Check every *committable* accept box (enabled only) + the import box."""
        for box in (*self._window_boxes.values(), *self._class_boxes.values()):
            if box.isEnabled():
                box.setChecked(True)
        if self._import_box.isEnabled():
            self._import_box.setChecked(True)

    def decision(self) -> ReconcileDecision:
        """The accepted subset. Only *enabled* checked boxes count (a disabled box —
        an unchanged facet or an M4-deferred class — is never committed)."""
        accept_windows = tuple(
            t.molecule_id
            for t in self._rows
            if self._window_boxes[t.molecule_id].isEnabled()
            and self._window_boxes[t.molecule_id].isChecked()
        )
        accept_classes = tuple(
            t.molecule_id
            for t in self._rows
            if self._class_boxes[t.molecule_id].isEnabled()
            and self._class_boxes[t.molecule_id].isChecked()
        )
        return ReconcileDecision(
            accept_windows=accept_windows,
            accept_classes=accept_classes,
            import_idealization=self._import_box.isEnabled() and self._import_box.isChecked(),
        )

    def exec(self) -> ReconcileDecision | None:
        """Show modally; return the :class:`ReconcileDecision` on OK, ``None`` on Cancel."""
        from pyqtgraph.Qt import QtWidgets

        accepted = self._dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted
        return self.decision() if accepted else None
