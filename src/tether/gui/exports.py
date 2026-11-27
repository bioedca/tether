# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""**File → Export**: the three project exports from the window (#621, PRD §7.9, G6).

Each action of the menu opens [`ExportDialog`][] for one [`ExportKind`][]. The dialog collects
the arguments of one existing export function of [`tether.project.export`][] and nothing
else; this module adds no export logic:

* **Molecule table (CSV)…** calls
  [`export_molecule_table_csv`][tether.project.export.export_molecule_table_csv];
* **Traces (Deep-LASI text)…** calls
  [`export_deeplasi_txt`][tether.project.export.export_deeplasi_txt];
* **Subset project…** calls [`export_subset_tether`][tether.project.export.export_subset_tether].

[`run_export`][] is the work, on the window's background-task worker. It writes the export and
its provenance file under a temporary name in the destination's folder, then moves both into
place. So a **Cancel** asked while the function runs writes nothing, and the files are the ones
the function writes for the same arguments. [`export_message`][] is the status line of a
finished export, and [`error_text`][] the message of a failed one, unchanged from the function.

Like the other ``tether.gui`` dialogs, the dialog holds a ``QDialog`` and imports Qt in
``__init__``, so everything else here needs no Qt.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyqtgraph.Qt import QtWidgets

    from tether.gui.tasks import CancelToken
    from tether.project.export import ExportResult

__all__ = [
    "ExportDialog",
    "ExportKind",
    "ExportRequest",
    "default_destination",
    "error_text",
    "export_message",
    "run_export",
]


class ExportKind(StrEnum):
    """The three project exports of the **File → Export** menu."""

    TABLE = "table"
    TRACES = "traces"
    SUBSET = "subset"


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

_SUFFIX: dict[ExportKind, str] = {
    ExportKind.TABLE: ".csv",
    ExportKind.TRACES: ".txt",
    ExportKind.SUBSET: ".tether",
}

_FILTER: dict[ExportKind, str] = {
    ExportKind.TABLE: "CSV table (*.csv)",
    ExportKind.TRACES: "Deep-LASI text (*.txt)",
    ExportKind.SUBSET: "Tether project (*.tether)",
}

#: The ``include_rejected`` default of each function: the table is a full inventory.
_INCLUDE_REJECTED: dict[ExportKind, bool] = {
    ExportKind.TABLE: True,
    ExportKind.TRACES: False,
    ExportKind.SUBSET: False,
}

#: The default prefix of "Replace curator names", as in the how-to guide (#620).
DEFAULT_CURATOR_ALIAS = "curator"


@dataclass(frozen=True)
class ExportRequest:
    """The arguments of one export, as the dialog chose them.

    ``movie_of`` is a ``molecule_key``: when set, the Deep-LASI export takes the molecules of
    that molecule's movie, as ``molecule_keys``. ``curator_alias`` is the subset's
    ``curator_alias`` (``None`` keeps the names).
    """

    kind: ExportKind
    out_path: Path
    include_rejected: bool
    include_raw: bool = False
    curator_alias: str | None = None
    movie_of: str | None = None


def default_destination(kind: ExportKind, project_path: str | os.PathLike[str]) -> Path:
    """Return the destination the dialog proposes: beside the project, named after it.

    The trace file takes the ``-donc-accc-w.txt`` ending that Deep-LASI reads. The path is
    absolute, so the dialog shows the folder it writes to.
    """
    project = Path(project_path).absolute()
    names = {
        ExportKind.TABLE: f"{project.stem}.csv",
        ExportKind.TRACES: f"{project.stem}-donc-accc-w.txt",
        ExportKind.SUBSET: f"{project.stem}-subset.tether",
    }
    return project.with_name(names[kind])


def _with_suffix(kind: ExportKind, text: str) -> Path:
    path = Path(text)
    suffix = _SUFFIX[kind]
    return path if path.name.lower().endswith(suffix) else path.with_name(path.name + suffix)


def _text(value: object) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _movie_keys(project_path: Path, molecule_key: str) -> list[str]:
    """Return the ``molecule_key`` of each molecule of the movie of ``molecule_key``."""
    from tether.imaging.extract import read_molecules

    molecules = read_molecules(project_path)
    keys = [_text(k) for k in molecules["molecule_key"]]
    movies = [_text(m) for m in molecules["movie_id"]]
    if molecule_key not in keys:
        raise KeyError(f"no molecule with molecule_key(s) {[molecule_key]} in the store")
    movie = movies[keys.index(molecule_key)]
    return [k for k, m in zip(keys, movies, strict=True) if m == movie]


def _call(request: ExportRequest, project_path: Path, out_path: Path) -> ExportResult:
    from tether.project.export import (
        export_deeplasi_txt,
        export_molecule_table_csv,
        export_subset_tether,
    )

    if request.kind is ExportKind.TABLE:
        return export_molecule_table_csv(
            project_path, out_path, include_rejected=request.include_rejected
        )
    if request.kind is ExportKind.TRACES:
        keys = None if request.movie_of is None else _movie_keys(project_path, request.movie_of)
        return export_deeplasi_txt(
            project_path,
            out_path,
            molecule_keys=keys,
            include_rejected=request.include_rejected,
        )
    return export_subset_tether(
        project_path,
        out_path,
        include_rejected=request.include_rejected,
        include_raw=request.include_raw,
        overwrite=True,  # the staged name exists; the destination is replaced only at the end
        curator_alias=request.curator_alias,
    )


def run_export(
    project_path: str | os.PathLike[str],
    request: ExportRequest,
    *,
    cancel: CancelToken | None = None,
) -> ExportResult:
    """Write the export ``request`` of ``project_path``; the work of the background task.

    The function writes to a temporary name in the destination's folder, so a relative path
    the subset stores still names the same file. When ``cancel`` was asked for by the time
    the function returns, both files are removed and
    [`TaskCancelled`][tether.gui.tasks.TaskCancelled] is raised: a cancelled export writes
    nothing. Otherwise the export and its provenance file replace the destination's; a
    cancel asked after that is too late, and the export finishes as usual.

    Raises
    ------
    ValueError, KeyError, FileExistsError, OSError
        As the export function raises them, and ``ValueError`` when the destination is the
        project itself
        ([`refuse_source_destination`][tether.project.export.refuse_source_destination]).
    TaskCancelled
        When ``cancel`` was asked for.
    """
    from tether.project.export import ExportResult, refuse_source_destination

    project = Path(project_path)
    out_path = Path(request.out_path)
    what = "subset out_path" if request.kind is ExportKind.SUBSET else "export destination"
    refuse_source_destination(project, out_path, what=what)
    if cancel is not None:
        cancel.raise_if_cancelled()
    fd, name = tempfile.mkstemp(
        dir=out_path.parent, prefix=f".{out_path.name}.", suffix=f".export{out_path.suffix}"
    )
    os.close(fd)
    staged = Path(name)
    staged_provenance = staged.with_name(staged.name + ".provenance.json")
    try:
        result = _call(request, project, staged)
        if cancel is not None:
            cancel.raise_if_cancelled()
        provenance = out_path.with_name(out_path.name + ".provenance.json")
        os.replace(staged, out_path)
        os.replace(staged_provenance, provenance)
    except BaseException:
        for leftover in (staged, staged_provenance):
            with contextlib.suppress(FileNotFoundError):
                leftover.unlink()
        raise
    return ExportResult(path=out_path, provenance_path=provenance, n_molecules=result.n_molecules)


def export_message(result: ExportResult) -> str:
    """Return the status line of a finished export, in the form ``tether export`` prints."""
    return f"Exported {result.n_molecules} molecule(s) -> {result.path.name}"


def error_text(error: BaseException | None) -> str:
    """Return the message of a failed export, as the function raised it.

    ``str()`` of a ``KeyError`` quotes its message; its argument is the message itself.
    """
    if isinstance(error, KeyError) and len(error.args) == 1:
        return str(error.args[0])
    return str(error)


class ExportDialog:
    """The dialog of one export: the destination and the options its function takes.

    *molecule_key* is the molecule selected in the window, or ``None``. For the Deep-LASI
    export, "Only the movie of the selected molecule" then scopes the file to that movie, as
    a project of movies of different lengths needs. **Export** is disabled while the
    destination is empty.
    """

    def __init__(
        self,
        kind: ExportKind,
        project_path: str | os.PathLike[str],
        *,
        molecule_key: str | None = None,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        from pyqtgraph.Qt import QtWidgets

        self._kind = kind
        self._molecule_key = molecule_key
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
        self._include_rejected.setChecked(_INCLUDE_REJECTED[kind])
        form.addRow("", self._include_rejected)

        self._movie_only = QtWidgets.QCheckBox("Only the movie of the selected molecule")
        self._movie_only.setToolTip(
            "A trace file has one frame axis. A project of movies of different lengths is "
            "exported one movie at a time."
        )
        self._movie_only.setEnabled(molecule_key is not None)
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
            out_path=_with_suffix(self._kind, self._destination.text().strip()),
            include_rejected=self._include_rejected.isChecked(),
            include_raw=subset and self._include_raw.isChecked(),
            curator_alias=(
                self._alias.text() if subset and self._replace_names.isChecked() else None
            ),
            movie_of=(
                self._molecule_key
                if self._kind is ExportKind.TRACES and self._movie_only.isChecked()
                else None
            ),
        )

    def _sync(self) -> None:
        text = self._destination.text().strip()
        self._ok.setEnabled(bool(text))
        target = _with_suffix(self._kind, text) if text else None
        exists = target is not None and target.exists()
        self._replaces.setText(f"{target.name} exists; Export replaces it." if exists else "")

    def _browse(self) -> None:
        from pyqtgraph.Qt import QtWidgets

        chosen, _ = QtWidgets.QFileDialog.getSaveFileName(
            self._dialog, TITLES[self._kind], self._destination.text(), _FILTER[self._kind]
        )
        if chosen:
            self._destination.setText(chosen)
