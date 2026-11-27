# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""**File → Export**: the dialogs of the three project exports (#621, PRD §7.9, G6).

Each action of the menu opens [`ExportDialog`][] for one
[`ExportKind`][tether.services.exports.ExportKind]. The dialog collects the arguments of one
existing export function of [`tether.project.export`][] and nothing else, as an
[`ExportRequest`][tether.services.exports.ExportRequest]. The window runs it with
[`run_export`][tether.services.exports.run_export] on its background-task worker, so the
action has one implementation outside the GUI (ADR-0100).

Like the other ``tether.gui`` dialogs, the dialog holds a ``QDialog`` and imports Qt in
``__init__``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tether.services.exports import (
    DEFAULT_CURATOR_ALIAS,
    ExportKind,
    ExportRequest,
    default_destination,
    include_rejected_default,
    with_suffix,
)

if TYPE_CHECKING:
    import os

    from pyqtgraph.Qt import QtWidgets

__all__ = ["MENU_TEXT", "TITLES", "ExportDialog"]


#: The menu text of each export.
MENU_TEXT: dict[ExportKind, str] = {
    ExportKind.TABLE: "Molecule &table (CSV)…",
    ExportKind.TRACES: "T&races (Deep-LASI text)…",
    ExportKind.SUBSET: "&Subset project…",
}

#: The dialog title of each export.
TITLES: dict[ExportKind, str] = {
    ExportKind.TABLE: "Export the molecule table",
    ExportKind.TRACES: "Export the traces for Deep-LASI",
    ExportKind.SUBSET: "Export a subset project",
}

_FILTER: dict[ExportKind, str] = {
    ExportKind.TABLE: "CSV table (*.csv)",
    ExportKind.TRACES: "Deep-LASI text (*.txt)",
    ExportKind.SUBSET: "Tether project (*.tether)",
}


class ExportDialog:
    """The dialog of one export: the destination and the options its function takes.

    *molecule* is the molecule selected in the window as ``(row, molecule_key)``, its
    ``/molecules`` row and key, or ``None``. For the Deep-LASI export, "Only the movie of the
    selected molecule" then scopes the file to that movie, as a project of movies of
    different lengths needs. **Export** is disabled while the destination is empty.
    """

    def __init__(
        self,
        kind: ExportKind,
        project_path: str | os.PathLike[str],
        *,
        molecule: tuple[int, str] | None = None,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        from pyqtgraph.Qt import QtWidgets

        self._kind = kind
        self._molecule = molecule
        dialog = QtWidgets.QDialog(parent)
        dialog.setWindowTitle(TITLES[kind])
        self._dialog = dialog
        form = QtWidgets.QFormLayout()

        self._destination = QtWidgets.QLineEdit(str(default_destination(kind, project_path)))
        self._destination.setAccessibleName("Destination")
        browse = QtWidgets.QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        self._browse_button = browse
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self._destination)
        row.addWidget(browse)
        form.addRow("Destination", row)

        self._include_rejected = QtWidgets.QCheckBox("Include rejected molecules")
        self._include_rejected.setChecked(include_rejected_default(kind))
        form.addRow("", self._include_rejected)

        self._movie_only = QtWidgets.QCheckBox("Only the movie of the selected molecule")
        self._movie_only.setToolTip(
            "A trace file has one frame axis. A project of movies of different lengths is "
            "exported one movie at a time."
        )
        self._movie_only.setEnabled(molecule is not None)
        self._include_raw = QtWidgets.QCheckBox("Include raw traces")
        self._replace_names = QtWidgets.QCheckBox("Replace curator names")
        self._alias = QtWidgets.QLineEdit(DEFAULT_CURATOR_ALIAS)
        self._alias.setAccessibleName("Curator name prefix")
        self._alias.setEnabled(False)
        self._replace_names.toggled.connect(self._alias.setEnabled)
        if kind is ExportKind.TRACES:
            form.addRow("", self._movie_only)
        if kind is ExportKind.SUBSET:
            form.addRow("", self._include_raw)
            alias_row = QtWidgets.QHBoxLayout()
            alias_row.addWidget(self._replace_names)
            alias_row.addWidget(self._alias)
            form.addRow("", alias_row)

        self._replaces = QtWidgets.QLabel()
        self._replaces.setWordWrap(True)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        self._ok = buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok)
        self._ok.setText("Export")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)

        layout = QtWidgets.QVBoxLayout(dialog)
        layout.addLayout(form)
        layout.addWidget(self._replaces)
        layout.addWidget(buttons)
        self._destination.textChanged.connect(self._sync)
        self._sync()

    @property
    def dialog(self) -> QtWidgets.QDialog:
        """The ``QDialog``."""
        return self._dialog

    @property
    def ok_button(self) -> QtWidgets.QPushButton:
        """The **Export** button."""
        return self._ok

    @property
    def destination(self) -> QtWidgets.QLineEdit:
        """The destination field."""
        return self._destination

    @property
    def include_rejected(self) -> QtWidgets.QCheckBox:
        """The "Include rejected molecules" box."""
        return self._include_rejected

    @property
    def movie_only(self) -> QtWidgets.QCheckBox:
        """The "Only the movie of the selected molecule" box (the Deep-LASI export)."""
        return self._movie_only

    @property
    def include_raw(self) -> QtWidgets.QCheckBox:
        """The "Include raw traces" box (the subset)."""
        return self._include_raw

    @property
    def replace_names(self) -> QtWidgets.QCheckBox:
        """The "Replace curator names" box (the subset)."""
        return self._replace_names

    @property
    def alias(self) -> QtWidgets.QLineEdit:
        """The prefix of the replaced curator names (the subset)."""
        return self._alias

    def request(self) -> ExportRequest:
        """Return the export the dialog shows now; the destination takes the kind's suffix."""
        subset = self._kind is ExportKind.SUBSET
        return ExportRequest(
            kind=self._kind,
            out_path=with_suffix(self._kind, self._destination.text().strip()),
            include_rejected=self._include_rejected.isChecked(),
            include_raw=subset and self._include_raw.isChecked(),
            curator_alias=(
                self._alias.text() if subset and self._replace_names.isChecked() else None
            ),
            movie_of=(
                self._molecule
                if self._kind is ExportKind.TRACES and self._movie_only.isChecked()
                else None
            ),
        )

    def _sync(self) -> None:
        text = self._destination.text().strip()
        self._ok.setEnabled(bool(text))
        target = with_suffix(self._kind, text) if text else None
        exists = target is not None and target.exists()
        self._replaces.setText(f"{target.name} exists; Export replaces it." if exists else "")

    def _browse(self) -> None:
        from pyqtgraph.Qt import QtWidgets

        chosen, _ = QtWidgets.QFileDialog.getSaveFileName(
            self._dialog, TITLES[self._kind], self._destination.text(), _FILTER[self._kind]
        )
        if chosen:
            self._destination.setText(chosen)
