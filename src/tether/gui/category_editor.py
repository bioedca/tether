# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The editor of one condition's category list (PRD §7.6, FR-ANNOTATE; #633).

Each condition has its own ordered list of category names, with no presets (§5.1). The
number keys ``1`` to ``9`` give the first nine names of the selected molecule's
condition, and the overflow picker gives the rest (§7.3). [`CategoryEditor`][] is the
dialog of **Curation → Categories…** and of the **Categories…** button in the browser
panel: it adds, renames, removes and reorders the names of the selected molecule's
condition.

The editor only shows the list and asks. Each change goes to a callable of the shell,
which writes it with the library function under the session's lock
([`TetherShell.add_category`][tether.gui.shell.TetherShell.add_category] and the
functions next to it) and returns a [`CategoryEdit`][]. The editor then shows the list
the project now holds and the result message.

The name field is a text entry, so the bare curation keys type there (§7.3), and the
dialog is outside the scope of the curation event filter anyway.

Removing a category that molecules use first asks, and states the count
([`removal_question`][]). The library's rule applies: those molecules keep the name as
their stored category (it is not cleared without a decision), but it is no longer in the
list, so no number key gives it again.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from pyqtgraph.Qt import QtWidgets

__all__ = ["CategoryEdit", "CategoryEditor", "removal_question"]


@dataclass(frozen=True)
class CategoryEdit:
    """The result of one change to a category list.

    ``names`` is the list the project holds after the change (the old list when the
    change failed), and ``message`` is what the user is told.
    """

    #: Whether the change was written.
    ok: bool
    #: The ordered category names after the change.
    names: tuple[str, ...]
    #: The result, as the status bar and the editor show it.
    message: str


def removal_question(name: str, count: int) -> str:
    """Return the confirmation text for removing ``name``, which ``count`` molecules use."""
    if count == 1:
        who, they = "1 molecule of this condition has", "That molecule keeps"
    else:
        who, they = f"{count} molecules of this condition have", "Those molecules keep"
    return (
        f"{who} the category “{name}”. Remove “{name}” from the list?\n\n"
        f"{they} “{name}” as their category, but it is no longer in the list, so no "
        "number key gives it again. To change it, select the molecule and press 0 to "
        "clear the category, or give it a category from the list. The categories after "
        f"“{name}” move up by one number key."
    )


def _ask(parent: Any, title: str, text: str) -> bool:  # pragma: no cover - modal dialog
    """Ask a yes/no question in a modal message box; ``True`` for Yes."""
    from pyqtgraph.Qt import QtWidgets

    buttons = QtWidgets.QMessageBox.StandardButton
    answer = QtWidgets.QMessageBox.question(
        parent, title, text, buttons.Yes | buttons.No, buttons.No
    )
    return answer == buttons.Yes


class CategoryEditor:
    """Modal editor of one condition's ordered category list (#633).

    ``condition`` is the text that names the condition at the top of the dialog, and
    ``names`` its list now. ``usage`` gives how many molecules of the condition have a
    name. ``add``, ``rename``, ``remove`` and ``move`` write one change each and return a
    [`CategoryEdit`][]; ``move`` takes a name and ``-1`` (up) or ``+1`` (down).
    ``confirm`` asks a yes/no question (title, text); the default is a message box, and a
    test passes its own.
    """

    def __init__(
        self,
        condition: str,
        names: Sequence[str],
        *,
        usage: Callable[[str], int],
        add: Callable[[str], CategoryEdit],
        rename: Callable[[str, str], CategoryEdit],
        remove: Callable[[str], CategoryEdit],
        move: Callable[[str, int], CategoryEdit],
        confirm: Callable[[str, str], bool] | None = None,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        from pyqtgraph.Qt import QtCore, QtWidgets

        self._names: tuple[str, ...] = tuple(names)
        self._usage = usage
        self._add = add
        self._rename = rename
        self._remove = remove
        self._move = move
        self._dialog = QtWidgets.QDialog(parent)
        self._dialog.setWindowTitle("Categories")
        self._confirm = confirm or (lambda title, text: _ask(self._dialog, title, text))

        layout = QtWidgets.QVBoxLayout(self._dialog)
        self._condition_label = QtWidgets.QLabel(f"Categories of the condition {condition}")
        self._condition_label.setWordWrap(True)
        self._condition_label.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        layout.addWidget(self._condition_label)
        help_text = QtWidgets.QLabel(
            "Keys 1 to 9 give the first nine categories to the selected molecule. "
            "Curation → Assign category (overflow)… gives the others. A molecule keeps "
            "its category when you rename it or move it in the list."
        )
        help_text.setWordWrap(True)
        layout.addWidget(help_text)

        self._list = QtWidgets.QListWidget()
        self._list.setAccessibleName("Category list")
        self._list.currentRowChanged.connect(self._on_row_changed)
        layout.addWidget(self._list, stretch=1)

        self._name_field = QtWidgets.QLineEdit()
        self._name_field.setAccessibleName("Category name")
        self._name_field.setPlaceholderText("Type a category name")
        # The bare curation keys type here (PRD §7.3 text-entry exemption).
        self._name_field.setProperty("tetherTextEntry", True)
        self._name_field.textChanged.connect(lambda _text: self._sync_buttons())
        layout.addWidget(self._name_field)

        row = QtWidgets.QHBoxLayout()
        self._buttons: dict[str, QtWidgets.QPushButton] = {}
        for key, text, slot in (
            ("add", "&Add", self.add),
            ("rename", "Re&name", self.rename),
            ("remove", "&Remove…", self.remove),
            ("up", "Move &up", lambda: self.move(-1)),
            ("down", "Move &down", lambda: self.move(1)),
        ):
            button = QtWidgets.QPushButton(text)
            button.setAutoDefault(False)
            button.clicked.connect(lambda _checked=False, s=slot: s())
            row.addWidget(button)
            self._buttons[key] = button
        layout.addLayout(row)

        self._message = QtWidgets.QLabel()
        self._message.setAccessibleName("Category editor message")
        self._message.setWordWrap(True)
        self._message.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        layout.addWidget(self._message)

        close = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self._dialog.reject)
        layout.addWidget(close)
        self._show_names(select=0 if self._names else None)

    # --- what the dialog shows -------------------------------------------------

    @property
    def dialog(self) -> QtWidgets.QDialog:
        """The wrapped modal ``QDialog``."""
        return self._dialog

    @property
    def names(self) -> tuple[str, ...]:
        """The category names the dialog shows, in list order."""
        return self._names

    @property
    def list_widget(self) -> QtWidgets.QListWidget:
        """The list of the categories, one row each, numbered by class."""
        return self._list

    @property
    def name_field(self) -> QtWidgets.QLineEdit:
        """The category name field: a text entry exempt from the curation keys."""
        return self._name_field

    @property
    def message(self) -> str:
        """The result of the last change, as the dialog shows it."""
        return str(self._message.text())

    def button(self, key: str) -> QtWidgets.QPushButton:
        """Return the ``add``, ``rename``, ``remove``, ``up`` or ``down`` button."""
        return self._buttons[key]

    def select(self, name: str) -> None:
        """Select the row of ``name`` (raises `ValueError` if it is not in the list)."""
        self._list.setCurrentRow(self._names.index(name))

    def selected(self) -> str | None:
        """Return the selected category name, or ``None``."""
        row = self._list.currentRow()
        return self._names[row] if 0 <= row < len(self._names) else None

    def exec(self) -> None:  # pragma: no cover - modal event loop
        """Show the dialog modally until the user closes it."""
        self._dialog.exec()

    # --- the changes -----------------------------------------------------------

    def add(self) -> CategoryEdit | None:
        """Add the typed name at the end of the list."""
        name = self._name_field.text().strip()
        if not name:
            return None
        edit = self._add(name)
        self._apply(edit, select=name if edit.ok else self.selected())
        if edit.ok:
            self._name_field.clear()
        return edit

    def rename(self) -> CategoryEdit | None:
        """Rename the selected category to the typed name."""
        old = self.selected()
        new = self._name_field.text().strip()
        if old is None or not new or new == old:
            return None
        edit = self._rename(old, new)
        self._apply(edit, select=new if edit.ok else old)
        return edit

    def remove(self) -> CategoryEdit | None:
        """Remove the selected category; ask first when molecules have it."""
        name = self.selected()
        if name is None:
            return None
        count = self._usage(name)
        if count > 0 and not self._confirm("Remove category", removal_question(name, count)):
            self._message.setText(f"“{name}” was not removed.")
            return None
        row = self._list.currentRow()
        edit = self._remove(name)
        after = edit.names[min(row, len(edit.names) - 1)] if edit.ok and edit.names else None
        self._apply(edit, select=after if edit.ok else name)
        return edit

    def move(self, offset: int) -> CategoryEdit | None:
        """Move the selected category ``offset`` rows (``-1`` up, ``+1`` down)."""
        name = self.selected()
        if name is None:
            return None
        target = self._names.index(name) + offset
        if not 0 <= target < len(self._names):
            return None
        edit = self._move(name, offset)
        self._apply(edit, select=name)
        return edit

    # --- internals ---------------------------------------------------------------

    def _apply(self, edit: CategoryEdit, *, select: str | None) -> None:
        self._names = tuple(edit.names)
        self._message.setText(edit.message)
        self._show_names(select=self._names.index(select) if select in self._names else None)

    def _show_names(self, *, select: int | None) -> None:
        self._list.blockSignals(True)
        self._list.clear()
        for code, name in enumerate(self._names, start=1):
            self._list.addItem(f"{code}. {name}")
        self._list.blockSignals(False)
        if select is not None:
            self._list.setCurrentRow(select)
        else:
            self._on_row_changed(-1)

    def _on_row_changed(self, row: int) -> None:
        if 0 <= row < len(self._names):
            self._name_field.setText(self._names[row])
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        row = self._list.currentRow()
        selected = 0 <= row < len(self._names)
        typed = self._name_field.text().strip()
        self._buttons["add"].setEnabled(bool(typed) and typed not in self._names)
        self._buttons["rename"].setEnabled(selected and bool(typed) and typed not in self._names)
        self._buttons["remove"].setEnabled(selected)
        self._buttons["up"].setEnabled(selected and row > 0)
        self._buttons["down"].setEnabled(selected and row < len(self._names) - 1)
