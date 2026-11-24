# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The reconciliation page of a new trace generation (#923, ADR-0136).

When a movie is extracted again, the molecules that did not move keep their key, and their
labels, category and window move to the new rows with the mark *carried*.
[`GenerationReconcilePage`][] shows what happened, movie by movie, in three groups:

* **Carried**: the labels moved. Each row says who labelled the molecule and when (the
  original curator, kept), and whether a person has confirmed it on this generation.
* **New**: a molecule with no partner in the previous generation, so no labels.
* **Lost**: a molecule of the previous generation with no partner in the new one. It and
  its labels stay readable in the previous generation; it is not in the trace view.

The user can show only the carried molecules, confirm them one by one or as a group, and
open a carried or new molecule in the trace view. A confirmation removes the mark and
records who confirmed and when; the shell makes the write through
[`Project.confirm_carried`][tether.project.core.Project.confirm_carried] under its writer
lock. The page itself writes nothing: it calls the callables it is given, so a test drives
it without a shell.

Like the other ``tether.gui`` dialogs it *holds* a ``QDialog`` rather than subclassing one,
and imports Qt lazily inside ``__init__``. The dialog is not modal, so the trace view stays
usable while it shows.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from pyqtgraph.Qt import QtWidgets

    from tether.project.generation_reconcile import (
        ConfirmResult,
        GenerationReconciliation,
        ReconciledMolecule,
    )

__all__ = ["GenerationReconcilePage"]

_LABEL_TEXT = {1: "accepted", -1: "rejected", 0: "uncurated"}


def _label_text(code: int) -> str:
    return _LABEL_TEXT.get(int(code), str(code))


def _when(timestamp: str) -> str:
    """Return the date and minute of an ISO time, for a narrow column."""
    return timestamp[:16].replace("T", " ") if timestamp else ""


def _mark_text(molecule: ReconciledMolecule, previous: int) -> str:
    if molecule.group == "lost":
        return f"lost; readable in generation {previous}"
    if molecule.group == "new":
        return "new; no labels"
    if molecule.waiting:
        return "carried; waiting for confirmation"
    return f"confirmed by {molecule.confirmed_by}, {_when(molecule.confirmed_utc)}"


class GenerationReconcilePage:
    """The carried, new and lost molecules of each movie extracted again.

    Parameters
    ----------
    reconciliations:
        One reconciliation
        ([`GenerationReconciliation`][tether.project.generation_reconcile.GenerationReconciliation])
        per movie that has more than one generation.
    confirm:
        Confirms the given keys and returns the
        [`ConfirmResult`][tether.project.generation_reconcile.ConfirmResult], or ``None``
        when the write was refused (the caller reports why).
    open_molecule:
        Selects a molecule by key in the trace view; returns whether it was found.
    reload:
        Reads the reconciliations again, after a confirmation. ``None`` keeps the lists.
    parent:
        The parent widget of the dialog.
    """

    #: The columns of the tree.
    COLUMNS = ("Molecule", "Label", "Category", "Labelled by", "Mark")

    def __init__(
        self,
        reconciliations: Sequence[GenerationReconciliation],
        *,
        confirm: Callable[[list[str]], ConfirmResult | None],
        open_molecule: Callable[[str], bool],
        reload: Callable[[], Sequence[GenerationReconciliation]] | None = None,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        from pyqtgraph.Qt import QtWidgets

        self._confirm = confirm
        self._open_molecule = open_molecule
        self._reload = reload
        self._reconciliations: tuple[GenerationReconciliation, ...] = ()

        self._dialog = QtWidgets.QDialog(parent)
        self._dialog.setWindowTitle("Reconcile trace generations")
        self._dialog.setModal(False)
        layout = QtWidgets.QVBoxLayout(self._dialog)
        self._summary = QtWidgets.QLabel()
        self._summary.setWordWrap(True)
        layout.addWidget(self._summary)

        self._carried_only = QtWidgets.QCheckBox("Show only the carried molecules")
        self._carried_only.toggled.connect(self._apply_filter)
        layout.addWidget(self._carried_only)

        self._tree = QtWidgets.QTreeWidget()
        self._tree.setColumnCount(len(self.COLUMNS))
        self._tree.setHeaderLabels(list(self.COLUMNS))
        self._tree.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self._tree.itemDoubleClicked.connect(lambda item, _column: self._open_item(item))
        self._tree.itemSelectionChanged.connect(self._update_buttons)
        layout.addWidget(self._tree)

        buttons = QtWidgets.QHBoxLayout()
        self._confirm_selected_button = QtWidgets.QPushButton("Confirm selected")
        self._confirm_selected_button.clicked.connect(self.confirm_selected)
        self._confirm_all_button = QtWidgets.QPushButton("Confirm all carried")
        self._confirm_all_button.clicked.connect(self.confirm_all)
        self._open_button = QtWidgets.QPushButton("Open in trace view")
        self._open_button.clicked.connect(self.open_selected)
        close = QtWidgets.QPushButton("Close")
        close.clicked.connect(self._dialog.close)
        for button in (
            self._confirm_selected_button,
            self._confirm_all_button,
            self._open_button,
        ):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self._status = QtWidgets.QLabel()
        self._status.setWordWrap(True)
        layout.addWidget(self._status)
        self._dialog.resize(760, 480)

        self.set_reconciliations(reconciliations)

    # --- contents ------------------------------------------------------------ #

    def set_reconciliations(self, reconciliations: Sequence[GenerationReconciliation]) -> None:
        """Show ``reconciliations``: one movie item, with its three groups, per movie."""
        from pyqtgraph.Qt import QtCore, QtWidgets

        role = QtCore.Qt.ItemDataRole.UserRole
        self._reconciliations = tuple(reconciliations)
        self._tree.clear()
        n_carried = n_waiting = n_new = n_lost = 0
        for rec in self._reconciliations:
            movie = QtWidgets.QTreeWidgetItem(
                [f"{rec.movie_id}: generation {rec.generation} against {rec.previous}"]
            )
            self._tree.addTopLevelItem(movie)
            carried = f"Carried ({len(rec.carried)}, {len(rec.waiting)} waiting)"
            groups = (
                ("carried", carried, rec.carried),
                ("new", f"New ({len(rec.new)})", rec.new),
                ("lost", f"Lost ({len(rec.lost)})", rec.lost),
            )
            for group, title, molecules in groups:
                group_item = QtWidgets.QTreeWidgetItem([title])
                group_item.setData(0, role, ("group", group))
                group_item.setFlags(group_item.flags() & ~QtCore.Qt.ItemFlag.ItemIsSelectable)
                movie.addChild(group_item)
                for molecule in molecules:
                    labeller = molecule.labeler
                    if molecule.labelled_utc:
                        labeller = f"{labeller}, {_when(molecule.labelled_utc)}"
                    item = QtWidgets.QTreeWidgetItem(
                        [
                            molecule.molecule_key[:12],
                            _label_text(molecule.curation_label),
                            molecule.category,
                            labeller,
                            _mark_text(molecule, rec.previous),
                        ]
                    )
                    item.setToolTip(0, molecule.molecule_key)
                    item.setData(0, role, (group, molecule.molecule_key, molecule.waiting))
                    group_item.addChild(item)
            movie.setExpanded(True)
            for i in range(movie.childCount()):
                movie.child(i).setExpanded(True)
            n_carried += len(rec.carried)
            n_waiting += len(rec.waiting)
            n_new += len(rec.new)
            n_lost += len(rec.lost)
        for column in range(len(self.COLUMNS)):
            self._tree.resizeColumnToContents(column)
        if self._reconciliations:
            self._summary.setText(
                f"{len(self._reconciliations)} movie(s) extracted again: {n_carried} carried "
                f"({n_waiting} waiting for confirmation), {n_new} new, {n_lost} lost. A carried "
                "label does not train the ranker until you confirm it, or accept or reject "
                "the molecule again. Every fit and correction of these movies is stale."
            )
        else:
            self._summary.setText("No movie of this project was extracted again.")
        self._apply_filter()
        self._update_buttons()

    def _items(self) -> list[QtWidgets.QTreeWidgetItem]:
        """Every molecule item, in tree order."""
        out = []
        for m in range(self._tree.topLevelItemCount()):
            movie = self._tree.topLevelItem(m)
            for g in range(movie.childCount()):
                group = movie.child(g)
                out.extend(group.child(i) for i in range(group.childCount()))
        return out

    def _data(self, item: QtWidgets.QTreeWidgetItem) -> tuple:
        from pyqtgraph.Qt import QtCore

        return item.data(0, QtCore.Qt.ItemDataRole.UserRole) or ()

    def _apply_filter(self, *_args: object) -> None:
        """Hide the new and lost groups while *carried only* is checked."""
        carried_only = self._carried_only.isChecked()
        for m in range(self._tree.topLevelItemCount()):
            movie = self._tree.topLevelItem(m)
            for g in range(movie.childCount()):
                group = movie.child(g)
                kind = self._data(group)[1:2]
                group.setHidden(carried_only and kind != ("carried",))
        self._update_buttons()

    def _update_buttons(self) -> None:
        selected = self.selected_items()
        self._confirm_selected_button.setEnabled(any(self._data(i)[2] for i in selected))
        self._confirm_all_button.setEnabled(bool(self.waiting_keys()))
        self._open_button.setEnabled(len(selected) == 1 and self._data(selected[0])[0] != "lost")

    # --- accessors ------------------------------------------------------------ #

    @property
    def dialog(self) -> QtWidgets.QDialog:
        """The wrapped, non-modal ``QDialog``."""
        return self._dialog

    @property
    def tree(self) -> QtWidgets.QTreeWidget:
        """The tree: one item per movie, its three groups, and a row per molecule."""
        return self._tree

    @property
    def carried_only(self) -> QtWidgets.QCheckBox:
        """The filter that shows only the carried molecules."""
        return self._carried_only

    @property
    def status_text(self) -> str:
        """The last line the page wrote under its buttons."""
        return self._status.text()

    @property
    def reconciliations(self) -> tuple[GenerationReconciliation, ...]:
        """The reconciliations the page shows."""
        return self._reconciliations

    def shown_keys(self, group: str | None = None) -> list[str]:
        """Return the keys of the rows the filter shows, of ``group`` or of all groups."""
        out = []
        for item in self._items():
            data = self._data(item)
            if item.parent().isHidden() or (group is not None and data[0] != group):
                continue
            out.append(data[1])
        return out

    def waiting_keys(self) -> list[str]:
        """Return the keys of the carried molecules that wait for confirmation."""
        return [self._data(i)[1] for i in self._items() if self._data(i)[2]]

    def selected_items(self) -> list[QtWidgets.QTreeWidgetItem]:
        """Return the selected molecule rows that the filter shows."""
        return [
            i for i in self._tree.selectedItems() if self._data(i) and not i.parent().isHidden()
        ]

    def select_keys(self, keys: Sequence[str]) -> None:
        """Select the rows of ``keys`` (and only them)."""
        wanted = set(keys)
        self._tree.clearSelection()
        for item in self._items():
            if self._data(item)[1] in wanted:
                item.setSelected(True)

    # --- actions -------------------------------------------------------------- #

    def show(self) -> None:
        """Show the page and bring it to the front."""
        self._dialog.show()
        self._dialog.raise_()

    def confirm_selected(self) -> ConfirmResult | None:
        """Confirm the selected carried molecules that wait; the others are left."""
        keys = [self._data(i)[1] for i in self.selected_items() if self._data(i)[2]]
        return self._confirm_keys(keys)

    def confirm_all(self) -> ConfirmResult | None:
        """Confirm every carried molecule that waits, in every movie shown."""
        return self._confirm_keys(self.waiting_keys())

    def _confirm_keys(self, keys: list[str]) -> ConfirmResult | None:
        if not keys:
            self._status.setText("Select a carried molecule that waits for confirmation.")
            return None
        result = self._confirm(keys)
        if result is None:
            self._status.setText("Nothing was confirmed; the status bar says why.")
            return None
        self._status.setText(
            f"Confirmed {len(result.confirmed)} carried molecule(s) as {result.labeler}."
        )
        if self._reload is not None:
            self.set_reconciliations(self._reload())
        return result

    def open_selected(self) -> bool:
        """Open the one selected carried or new molecule in the trace view."""
        selected = self.selected_items()
        if len(selected) != 1:
            self._status.setText("Select one molecule to open.")
            return False
        return self._open_item(selected[0])

    def _open_item(self, item: QtWidgets.QTreeWidgetItem) -> bool:
        data = self._data(item)
        if not data or data[0] == "group":
            return False
        if data[0] == "lost":
            self._status.setText(
                "A lost molecule is not in the active generation, so the trace view does not "
                "show it; it stays readable in the previous generation."
            )
            return False
        found = self._open_molecule(data[1])
        if not found:
            self._status.setText(f"Molecule {data[1][:12]} is not in the trace view.")
        return found
