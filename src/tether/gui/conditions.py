# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The condition-validation / confirm-correct / merge dialog (PRD §5.1, §7.6; FR-ANNOTATE).

The GUI surface for the M4 annotation step (PLAN §8 PR-2b). Its headless core landed
earlier: :func:`tether.project.conditions.validate_conditions` (referential-integrity
report), :func:`~tether.project.conditions.sync_conditions` (materialize provisional
rows), and the transactional re-key + human-confirmed merge
(:func:`~tether.project.conditions.preview_rekey` /
:func:`~tether.project.conditions.rekey_condition`). This module renders that as a
per-condition **confirm / correct** prompt:

* **Confirm the provisional ids** — :meth:`ConditionValidationDialog.materialize`
  runs ``sync_conditions`` so every provisional ``condition_id`` a *faithful witness*
  supports becomes a real ``/conditions`` row (a *dangling* reference → *ok*). This is
  the "the filename parse was right" path (PRD §7.6).
* **Correct a mis-parsed id** — :meth:`ConditionValidationDialog.apply_correction`
  takes the corrected :class:`~tether.io.filename.ConditionKey` (built in the
  :class:`ConditionKeyEditor`) and **transactionally re-keys every affected molecule**
  via ``rekey_condition``, which stamps an audit entry.
* **Human-confirmed merge** — when the correction lands on a condition that already has
  members, the re-key would *merge* two conditions; the dialog first previews it
  (:func:`~tether.project.conditions.preview_rekey`) and only proceeds if the injected
  :attr:`~ConditionValidationDialog.confirm_merge` callback returns ``True`` (a
  ``Yes/No`` prompt by default) — never silent on a ~100-video condition (§5.1).

Design notes (mirroring the other :mod:`tether.gui` dialogs, e.g.
:class:`tether.gui.reconcile.ReconcileDialog`):

* **Composition wrapper.** It *holds* a ``QDialog`` rather than subclassing one, and
  imports Qt lazily inside ``__init__`` (via :mod:`pyqtgraph.Qt`), so importing this
  module costs no Qt. A live ``QApplication`` (the shell / ``qtbot``) must exist to
  construct it.
* **Writes go through the :class:`~tether.project.core.Project`** handle, so the §5.4
  single-writer lock is honored (a foreign lock refuses the re-key/sync).
* **The merge confirmation is an injected callback**, not a hard-wired modal, so the
  never-silent-merge contract is driven deterministically in headless tests.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from pyqtgraph.Qt import QtWidgets

    from tether.io.filename import ConditionKey
    from tether.project.conditions import RekeyPreview, RekeyResult
    from tether.project.core import Project

__all__ = ["ConditionKeyEditor", "ConditionValidationDialog"]

#: The eight identity fields of a :class:`~tether.io.filename.ConditionKey`, in the
#: order the editor shows them: ``(attribute, label, is_numeric)``. The numeric fields
#: are optional (empty text ↔ ``None``; the canonical key hashes an absent numeric as
#: JSON ``null``), so a blank cell round-trips to the same id.
_KEY_FIELDS: tuple[tuple[str, str, bool], ...] = (
    ("construct_variant", "Construct / variant", False),
    ("dye", "Dye", False),
    ("ligand", "Ligand", False),
    ("ligand_concentration", "Ligand concentration", True),
    ("ligand_concentration_unit", "Concentration unit", False),
    ("buffer", "Buffer", False),
    ("temperature_c", "Temperature (°C)", True),
    ("laser_power", "Laser power", True),
)

_STATUS_OK = "ok"
_STATUS_DANGLING = "dangling"
_STATUS_INCONSISTENT = "inconsistent"


def _fmt_num(value: float | None) -> str:
    """A numeric key field as editable text (``None`` → ``""``; ``600.0`` → ``"600"``).

    Uses ``repr`` (the shortest string that round-trips a float **exactly**) — not
    ``%g`` — so prefilling the editor from a stored key and pressing OK unchanged
    rebuilds the *same* ``condition_id``; a high-precision numeric (e.g. ``1234567.0``)
    is not silently truncated into a different id. An integral value drops the ``.0``.
    """
    if value is None:
        return ""
    if math.isfinite(value) and value == int(value):
        return str(int(value))
    return repr(value)


def _parse_num(text: str) -> float | None:
    """Parse an editor numeric cell back to ``float | None`` (blank → ``None``).

    Raises :class:`ValueError` on a non-empty, non-numeric value so a typo cannot be
    silently coerced into the id (the never-silent contract).
    """
    stripped = text.strip()
    if not stripped:
        return None
    return float(stripped)


def _key_summary(key: ConditionKey) -> str:
    """A one-line human summary of a condition key for the table (identity fields only)."""
    parts = [key.construct_variant or "—"]
    if key.dye:
        parts.append(key.dye)
    if key.ligand:
        conc = ""
        if key.ligand_concentration is not None:
            conc = f"@{key.ligand_concentration:g}{key.ligand_concentration_unit}"
        parts.append(f"{key.ligand}{conc}")
    if key.buffer:
        parts.append(key.buffer)
    if key.temperature_c is not None:
        parts.append(f"{key.temperature_c:g}°C")
    if key.laser_power is not None:
        parts.append(f"{key.laser_power:g}mW")
    return " · ".join(parts)


class ConditionKeyEditor:
    """A form for a corrected :class:`~tether.io.filename.ConditionKey` (PRD §7.6).

    The eight identity fields as line edits (numerics blank-for-absent). Prefilled from
    the current key when correcting a materialized condition; blank for a dangling one.
    :meth:`key` builds the edited key, raising :class:`ValueError` on a bad numeric.
    """

    def __init__(
        self, initial_key: ConditionKey | None = None, *, parent: QtWidgets.QWidget | None = None
    ) -> None:
        from pyqtgraph.Qt import QtWidgets

        self._dialog = QtWidgets.QDialog(parent)
        self._dialog.setWindowTitle("Correct condition key")
        layout = QtWidgets.QVBoxLayout(self._dialog)
        layout.addWidget(
            QtWidgets.QLabel(
                "Edit the corrected condition identity. Leave a numeric field blank for "
                "«absent». All molecules on the selected condition will be re-keyed."
            )
        )

        form = QtWidgets.QFormLayout()
        self._edits: dict[str, QtWidgets.QLineEdit] = {}
        for attr, label, _is_numeric in _KEY_FIELDS:
            edit = QtWidgets.QLineEdit()
            self._edits[attr] = edit
            form.addRow(label, edit)
        layout.addLayout(form)

        self._error = QtWidgets.QLabel("")
        self._error.setStyleSheet("color: palette(bright-text);")
        self._error.setVisible(False)
        layout.addWidget(self._error)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self._dialog.reject)
        layout.addWidget(buttons)

        if initial_key is not None:
            self.set_key(initial_key)

    @property
    def dialog(self) -> QtWidgets.QDialog:
        """The wrapped "Correct condition key" ``QDialog`` holding the eight-field form."""
        return self._dialog

    def field(self, attr: str) -> QtWidgets.QLineEdit:
        """The line edit for a key attribute (raises ``KeyError`` if unknown)."""
        return self._edits[attr]

    def set_key(self, key: ConditionKey) -> None:
        """Prefill the form from ``key`` (numerics rendered blank-for-``None``)."""
        for attr, _label, is_numeric in _KEY_FIELDS:
            value = getattr(key, attr)
            self._edits[attr].setText(_fmt_num(value) if is_numeric else str(value))

    def key(self) -> ConditionKey:
        """Build the edited :class:`ConditionKey` (raises ``ValueError`` on a bad numeric)."""
        from tether.io.filename import ConditionKey

        values: dict[str, object] = {}
        for attr, label, is_numeric in _KEY_FIELDS:
            text = self._edits[attr].text()
            if is_numeric:
                try:
                    values[attr] = _parse_num(text)
                except ValueError:
                    raise ValueError(f"{label!r} must be a number or blank, got {text!r}") from None
            else:
                values[attr] = text.strip()
        return ConditionKey(**values)  # type: ignore[arg-type]

    def _on_accept(self) -> None:
        """Validate the numerics before accepting; keep the dialog open on a bad value."""
        try:
            self.key()
        except ValueError as exc:
            self._error.setText(str(exc))
            self._error.setVisible(True)
            return
        self._dialog.accept()

    def exec(self) -> ConditionKey | None:
        """Show modally; return the edited key on OK, ``None`` on Cancel."""
        from pyqtgraph.Qt import QtWidgets

        if self._dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            return self.key()
        return None


class ConditionValidationDialog:
    """A per-condition confirm/correct prompt over :func:`validate_conditions` (PRD §5.1)."""

    _COLUMNS = ("Condition", "Molecules", "Status")

    def __init__(
        self,
        project: Project,
        *,
        confirm_merge: Callable[[RekeyPreview], bool] | None = None,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        from pyqtgraph.Qt import QtWidgets

        self._project = project
        #: The human-confirmed-merge gate (§5.1). Injected in tests; a ``Yes/No`` modal
        #: by default. Returning ``False`` aborts the re-key with nothing written.
        self.confirm_merge: Callable[[RekeyPreview], bool] = (
            confirm_merge if confirm_merge is not None else self._default_confirm_merge
        )
        #: ``condition_id`` per table row, in row order (the table shows summaries).
        self._row_ids: list[str] = []

        self._dialog = QtWidgets.QDialog(parent)
        self._dialog.setWindowTitle("Validate conditions")
        layout = QtWidgets.QVBoxLayout(self._dialog)

        self._header = QtWidgets.QLabel("")
        layout.addWidget(self._header)

        self._table = QtWidgets.QTableWidget(0, len(self._COLUMNS))
        self._table.setHorizontalHeaderLabels(list(self._COLUMNS))
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        layout.addWidget(self._table)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Close)
        self._materialize_button = buttons.addButton(
            "Confirm provisional (materialize)", QtWidgets.QDialogButtonBox.ButtonRole.ActionRole
        )
        self._materialize_button.clicked.connect(self._on_materialize)
        self._correct_button = buttons.addButton(
            "Correct…", QtWidgets.QDialogButtonBox.ButtonRole.ActionRole
        )
        self._correct_button.clicked.connect(self._prompt_correction)
        buttons.rejected.connect(self._dialog.reject)
        layout.addWidget(buttons)

        self.refresh()

    # --- accessors (test seams) -------------------------------------------- #

    @property
    def dialog(self) -> QtWidgets.QDialog:
        """The wrapped "Validate conditions" ``QDialog`` holding the header, table and buttons."""
        return self._dialog

    @property
    def table(self) -> QtWidgets.QTableWidget:
        """The single-row-selection ``QTableWidget`` of Condition / Molecules / Status."""
        return self._table

    def row_condition_ids(self) -> list[str]:
        """The ``condition_id`` shown on each table row, in row order."""
        return list(self._row_ids)

    def status_of(self, condition_id: str) -> str:
        """A referenced condition's status: ``ok`` / ``dangling`` / ``inconsistent``."""
        report = self._project.validate_conditions()
        if condition_id in report.dangling:
            return _STATUS_DANGLING
        if condition_id in report.inconsistent:
            return _STATUS_INCONSISTENT
        return _STATUS_OK

    def select_condition(self, condition_id: str) -> None:
        """Select the table row for ``condition_id`` (raises ``KeyError`` if not shown)."""
        try:
            row = self._row_ids.index(condition_id)
        except ValueError:
            raise KeyError(condition_id) from None
        self._table.selectRow(row)

    def selected_condition_id(self) -> str | None:
        """The ``condition_id`` of the selected row, or ``None`` if nothing is selected."""
        row = self._table.currentRow()
        if row < 0 or row >= len(self._row_ids):
            return None
        return self._row_ids[row]

    # --- behaviour --------------------------------------------------------- #

    def _on_materialize(self) -> None:
        """Materialize button slot — guarded so a refused/failed write reports, not escapes.

        A foreign single-writer lock (§5.4) makes ``sync_conditions`` raise
        :class:`~tether.project.lock.LockedError`; catching it here surfaces the cause to
        the curator instead of letting the exception escape the Qt slot (which would crash
        or silently swallow it), matching the shell's write-action convention.
        """
        try:
            self.materialize()
        except Exception as exc:  # noqa: BLE001 - keep the GUI alive, report the cause
            self._report_error(exc)

    def materialize(self) -> None:
        """Confirm the provisional ids: materialize every faithful-witness ``/conditions`` row."""
        self._project.sync_conditions()
        self.refresh()

    def apply_correction(self, from_condition_id: str, to_key: ConditionKey) -> RekeyResult | None:
        """Re-key ``from_condition_id`` → ``to_key``, human-confirming a merge (§5.1).

        Previews first; a no-op correction (``to_key`` already equal) does nothing. A
        *merge* (destination already populated) proceeds only if :attr:`confirm_merge`
        returns ``True`` — otherwise nothing is written and ``None`` is returned. On a
        committed re-key the table is refreshed and the :class:`RekeyResult` returned.
        """
        preview = self._project.preview_rekey(from_condition_id, to_key)
        if preview.to_condition_id == from_condition_id:
            return None  # the edited key equals the current one — nothing to re-key
        if preview.is_merge and not self.confirm_merge(preview):
            return None  # human declined the merge — never silent, never forced
        result = self._project.rekey_condition(
            from_condition_id,
            to_key,
            confirm=preview.is_merge,
            reason="GUI condition validation",
        )
        self.refresh()
        return result

    def refresh(self) -> None:
        """Rebuild the table from the current referential-validation state."""
        from pyqtgraph.Qt import QtWidgets

        report = self._project.validate_conditions()
        members = self._project.molecules_by_condition()
        keys = self._project.read_condition_keys()

        self._row_ids = sorted(members)
        self._table.setRowCount(len(self._row_ids))
        for row, cid in enumerate(self._row_ids):
            if cid in report.dangling:
                status = _STATUS_DANGLING
            elif cid in report.inconsistent:
                status = _STATUS_INCONSISTENT
            else:
                status = _STATUS_OK
            key = keys.get(cid)
            label = _key_summary(key) if key is not None else f"{cid} (no condition row)"
            self._table.setItem(row, 0, QtWidgets.QTableWidgetItem(label))
            self._table.setItem(row, 1, QtWidgets.QTableWidgetItem(str(len(members[cid]))))
            self._table.setItem(row, 2, QtWidgets.QTableWidgetItem(status))
        self._table.resizeColumnsToContents()
        self._header.setText(
            f"{len(self._row_ids)} referenced condition(s) · "
            f"{len(report.dangling)} dangling · {len(report.inconsistent)} inconsistent"
        )

    def _prompt_correction(self) -> RekeyResult | None:
        """Open the key editor for the selected row, then apply the correction."""
        from pyqtgraph.Qt import QtWidgets

        from_id = self.selected_condition_id()
        if from_id is None:
            QtWidgets.QMessageBox.information(
                self._dialog, "Correct condition", "Select a condition row first."
            )
            return None
        current = self._project.read_condition_keys().get(from_id)
        editor = ConditionKeyEditor(current, parent=self._dialog)
        to_key = editor.exec()
        if to_key is None:
            return None
        try:
            return self.apply_correction(from_id, to_key)
        except Exception as exc:  # noqa: BLE001 - keep the GUI alive, report the cause
            # A foreign lock (§5.4) or any headless error is surfaced, not left to
            # escape the Qt slot (mirrors :meth:`_on_materialize`).
            self._report_error(exc)
            return None

    def _report_error(self, exc: Exception) -> None:
        """Surface a failed write to the curator as a modal warning (never a silent crash)."""
        from pyqtgraph.Qt import QtWidgets

        QtWidgets.QMessageBox.warning(self._dialog, "Conditions", f"Operation failed: {exc}")

    def _default_confirm_merge(self, preview: RekeyPreview) -> bool:
        """Default merge gate: a modal ``Yes/No`` warning naming the collateral count."""
        from pyqtgraph.Qt import QtWidgets

        answer = QtWidgets.QMessageBox.warning(
            self._dialog,
            "Confirm condition merge",
            f"Re-keying {preview.n_molecules} molecule(s) into condition "
            f"{preview.to_condition_id} would MERGE them with "
            f"{len(preview.destination_molecule_keys)} molecule(s) already there. "
            "This collapses two conditions into one. Proceed?",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No,
        )
        return answer == QtWidgets.QMessageBox.StandardButton.Yes

    def exec(self) -> None:
        """Show the dialog modally."""
        self._dialog.exec()
