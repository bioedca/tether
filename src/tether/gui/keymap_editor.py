# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The keyboard-shortcut editor of the curation keymap (#635, PRD §7.3).

PRD §7.3 says every curation binding is rebindable. [`KeymapEditor`][] lists each command
of the [`Keymap`][tether.gui.curation.Keymap] with its key. The user selects a command,
clicks the key field and presses the new key; the editor names a conflict *before* the
**Assign** button accepts it. It refuses:

* a key another curation command already uses (the keymap is one section, #342, so two
  commands never share a key);
* ``C`` and ``V``, which stay reserved no-ops
  ([`RESERVED_KEYS`][tether.gui.curation.RESERVED_KEYS]);
* a key a menu command of the window already owns, which would never reach the keymap;
* ``Tab``, ``Shift+Tab`` and ``Esc``, which move the focus and close the dialog, and a
  modifier key pressed alone.

A bare key needs no refusal for text fields: the application-level filter exempts every
key while a text field has the focus ([`is_text_entry`][tether.gui.curation.is_text_entry]),
so a letter bound here still types that letter into the category field.

**Restore default** gives the selected command its default key(s) again, and **Restore all
defaults** the whole table. The edits apply to a working copy; **Save** hands it to
``on_save`` (the shell puts it in effect and writes the keymap file), **Cancel** drops it.

Like the other ``tether.gui`` dialogs, the editor *holds* a ``QDialog`` and imports Qt
lazily.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tether.gui.curation import (
    _ACTION_ORDER,
    RESERVED_KEYS,
    Command,
    CurationAction,
    KeyChord,
    Keymap,
    _as_int,
    action_description,
)

if TYPE_CHECKING:
    from pyqtgraph.Qt import QtWidgets

__all__ = ["KeymapEditor", "chord_label", "editable_commands"]


def chord_label(key: int, modifiers: int = 0) -> str:
    """Return the native text of a chord, as the cheat-sheet shows it (``Space``, ``Shift+N``)."""
    from pyqtgraph.Qt import QtGui

    native = QtGui.QKeySequence.SequenceFormat.NativeText
    return QtGui.QKeySequence(_as_int(key) | _as_int(modifiers)).toString(native)


def editable_commands(defaults: Keymap, *saved: Keymap) -> list[Command]:
    """List default and saved commands in cheat-sheet order, except the reserved no-op."""
    commands = {cmd for keymap in (defaults, *saved) for cmd in keymap.bindings.values()}
    commands.discard(Command(CurationAction.RESERVED))
    return sorted(
        commands,
        key=lambda cmd: (_ACTION_ORDER.get(cmd.action, len(_ACTION_ORDER)), cmd.arg or 0),
    )


class KeymapEditor:
    """The **Keyboard shortcuts** editor: a command table, a key field and the checks.

    ``keymap`` is the live keymap; the editor works on a copy of it. ``taken`` maps a chord
    the window's menus already own to that menu command's name. ``on_save`` receives the
    edited keymap when the user presses **Save**. ``file_path``, when given, is named under
    the table so the user knows where the keymap is kept.
    """

    def __init__(
        self,
        keymap: Keymap,
        *,
        parent: QtWidgets.QWidget | None = None,
        on_save: Callable[[Keymap], Any] | None = None,
        taken: Mapping[KeyChord, str] | None = None,
        unavailable: frozenset[CurationAction] = frozenset(),
        file_path: Path | None = None,
    ) -> None:
        from pyqtgraph.Qt import QtCore, QtWidgets

        self._working = Keymap(dict(keymap.bindings))
        self._defaults = Keymap.default()
        self._commands = editable_commands(self._defaults, self._working)
        self._taken = dict(taken or {})
        self._unavailable = frozenset(unavailable)
        self._on_save = on_save
        self._pending: KeyChord | None = None

        key = QtCore.Qt.Key
        self._passthrough = frozenset(
            _as_int(k) for k in (key.Key_Tab, key.Key_Backtab, key.Key_Escape)
        )
        self._modifier_keys = frozenset(
            _as_int(k)
            for k in (
                key.Key_Shift,
                key.Key_Control,
                key.Key_Meta,
                key.Key_Alt,
                key.Key_AltGr,
                key.Key_CapsLock,
                key.Key_NumLock,
                key.Key_ScrollLock,
                key.Key_unknown,
            )
        )
        mod = QtCore.Qt.KeyboardModifier
        # The same modifiers the curation filter compares (Shift, Ctrl, Alt, Meta), so the
        # chord captured here is the chord the filter will look up.
        self._significant_mask = (
            _as_int(mod.ShiftModifier)
            | _as_int(mod.ControlModifier)
            | _as_int(mod.AltModifier)
            | _as_int(mod.MetaModifier)
        )

        self._dialog = QtWidgets.QDialog(parent)
        self._dialog.setWindowTitle("Edit keyboard shortcuts")
        layout = QtWidgets.QVBoxLayout(self._dialog)
        self._table = QtWidgets.QTableWidget(len(self._commands), 2)
        self._table.setHorizontalHeaderLabels(["Action", "Key"])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self._table.currentCellChanged.connect(lambda *_: self._clear_pending())
        layout.addWidget(self._table)

        form = QtWidgets.QHBoxLayout()
        form.addWidget(QtWidgets.QLabel("New key:"))
        self._capture = QtWidgets.QLineEdit()
        self._capture.setReadOnly(True)
        self._capture.setPlaceholderText("Click here, then press a key")
        self._capture.setAccessibleName("New key")
        form.addWidget(self._capture, stretch=1)
        self._assign_button = QtWidgets.QPushButton("&Assign")
        self._assign_button.setAutoDefault(False)
        self._assign_button.setEnabled(False)
        self._assign_button.clicked.connect(lambda: self.assign())
        form.addWidget(self._assign_button)
        layout.addLayout(form)

        self._message = QtWidgets.QLabel()
        self._message.setWordWrap(True)
        layout.addWidget(self._message)

        restore = QtWidgets.QHBoxLayout()
        self._restore_button = QtWidgets.QPushButton("Restore &default")
        self._restore_button.setAutoDefault(False)
        self._restore_button.clicked.connect(lambda: self.restore_selected())
        restore.addWidget(self._restore_button)
        self._restore_all_button = QtWidgets.QPushButton("Restore a&ll defaults")
        self._restore_all_button.setAutoDefault(False)
        self._restore_all_button.clicked.connect(lambda: self.restore_all())
        restore.addWidget(self._restore_all_button)
        restore.addStretch(1)
        layout.addLayout(restore)

        if file_path is not None:
            where = QtWidgets.QLabel(f"Saved to {file_path}")
            where.setWordWrap(True)
            where.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(where)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Save
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self._dialog.reject)
        layout.addWidget(buttons)
        self._buttons = buttons

        outer = self

        class _CaptureFilter(QtCore.QObject):
            def eventFilter(self, watched: Any, event: Any) -> bool:  # noqa: N802 (Qt override)
                return outer._capture_event(event)

        self._capture_filter = _CaptureFilter(self._capture)
        self._capture.installEventFilter(self._capture_filter)
        self._shortcut_override = QtCore.QEvent.Type.ShortcutOverride
        self._key_press = QtCore.QEvent.Type.KeyPress

        self._refresh_rows()
        if self._commands:
            self._table.setCurrentCell(0, 0)

    # --- accessors -----------------------------------------------------------

    @property
    def dialog(self) -> QtWidgets.QDialog:
        """The wrapped ``QDialog``."""
        return self._dialog

    @property
    def keymap(self) -> Keymap:
        """The working copy the edits apply to (the live keymap changes only on Save)."""
        return self._working

    @property
    def commands(self) -> list[Command]:
        """The listed commands, in table order."""
        return list(self._commands)

    @property
    def capture_field(self) -> QtWidgets.QLineEdit:
        """The field that records the next key pressed while it has the focus."""
        return self._capture

    @property
    def assign_button(self) -> QtWidgets.QPushButton:
        """**Assign**: enabled only while the pending key has no conflict."""
        return self._assign_button

    @property
    def message(self) -> str:
        """The conflict, refusal or confirmation shown under the key field."""
        return self._message.text()

    @property
    def pending_chord(self) -> KeyChord | None:
        """The key pressed in the key field and not yet assigned, or ``None``."""
        return self._pending

    def rows(self) -> list[tuple[str, str]]:
        """Return the ``(action, key)`` rows of the table, in order."""
        return [
            (self._table.item(row, 0).text(), self._table.item(row, 1).text())
            for row in range(self._table.rowCount())
        ]

    # --- editing -------------------------------------------------------------

    def select(self, command: Command) -> None:
        """Select ``command``'s row (``ValueError`` if it is not listed)."""
        self._table.setCurrentCell(self._commands.index(command), 0)

    def selected_command(self) -> Command | None:
        """Return the command of the selected row, or ``None``."""
        row = self._table.currentRow()
        return self._commands[row] if 0 <= row < len(self._commands) else None

    def problem(self, command: Command, key: int, modifiers: int = 0) -> str | None:
        """Why ``(key, modifiers)`` cannot be ``command``'s key, or ``None`` if it can."""
        key, modifiers = _as_int(key), _as_int(modifiers)
        label = chord_label(key, modifiers)
        if key in self._passthrough or key in self._modifier_keys:
            return f"{label or 'That key'} cannot be a shortcut."
        if key in RESERVED_KEYS:
            return "C and V are reserved and cannot be bound."
        menu_command = self._taken.get((key, modifiers))
        if menu_command is not None:
            return f"{label} is already used by the menu command “{menu_command}”."
        other = self._working.conflict(command, key, modifiers)
        if other is not None:
            return (
                f"{label} is already used by “{action_description(other)}”. "
                "Give that action another key first."
            )
        return None

    def propose(self, key: int, modifiers: int = 0) -> str | None:
        """Take ``(key, modifiers)`` as the new key of the selected command, pending Assign.

        Shows the key and, before anything is accepted, the conflict or refusal; **Assign**
        is enabled only when there is none. Returns the problem, or ``None``.
        """
        command = self.selected_command()
        key, modifiers = _as_int(key), _as_int(modifiers) & self._significant_mask
        self._pending = (key, modifiers)
        self._capture.setText(chord_label(key, modifiers))
        problem = (
            "Select an action first." if command is None else self.problem(command, key, modifiers)
        )
        if problem is None and command is not None:
            self._message.setText(
                f"Press Assign to use {chord_label(key, modifiers)} for "
                f"“{action_description(command)}”."
            )
        else:
            self._message.setText(problem)
        self._assign_button.setEnabled(problem is None)
        return problem

    def assign(self) -> bool:
        """Bind the pending key to the selected command, replacing its key(s)."""
        command = self.selected_command()
        if command is None or self._pending is None:
            return False
        if self.problem(command, *self._pending) is not None:
            return False
        key, modifiers = self._pending
        self._working.rebind(command, key, modifiers)
        self._refresh_rows()
        self._clear_pending()
        self._message.setText(
            f"“{action_description(command)}” is now {chord_label(key, modifiers)}."
        )
        return True

    def restore_selected(self) -> bool:
        """Give the selected command its default key(s); refuse if one is now taken."""
        command = self.selected_command()
        if command is None:
            return False
        self._clear_pending()
        wanted = self._defaults.chords_for(command)
        if not wanted:
            self._message.setText(f"“{action_description(command)}” has no default key.")
            return False
        blocker = self._working.restore_default(command, self._defaults)
        if blocker is not None:
            keys = ", ".join(chord_label(*c) for c in sorted(wanted))
            self._message.setText(
                f"The default key {keys} is now used by “{action_description(blocker)}”. "
                "Give that action another key first, or restore all defaults."
            )
            return False
        self._refresh_rows()
        self._message.setText(f"“{action_description(command)}” has its default key again.")
        return True

    def restore_all(self) -> None:
        """Give every command its default key(s)."""
        self._clear_pending()
        self._working = Keymap(dict(self._defaults.bindings))
        self._refresh_rows()
        self._message.setText("Every action has its default key again.")

    def save(self) -> None:
        """Hand the edited keymap to ``on_save`` and close the dialog."""
        if self._on_save is not None:
            self._on_save(Keymap(dict(self._working.bindings)))
        self._dialog.accept()

    def show(self) -> None:
        """Show the editor (not modal, like the cheat-sheet)."""
        self._dialog.show()

    # --- internals -----------------------------------------------------------

    def _capture_event(self, event: Any) -> bool:
        """Record a key pressed in the key field; Tab and Esc keep their meaning."""
        event_type = event.type()
        if event_type not in (self._key_press, self._shortcut_override):
            return False
        key = _as_int(event.key())
        if key in self._passthrough:
            return False
        if event_type == self._shortcut_override:
            # Claim the key so a menu shortcut (Ctrl+O …) does not fire while capturing.
            event.accept()
            return True
        if key not in self._modifier_keys and not event.isAutoRepeat():
            self.propose(key, _as_int(event.modifiers()))
        return True

    def _clear_pending(self) -> None:
        self._pending = None
        self._capture.clear()
        self._assign_button.setEnabled(False)

    def _refresh_rows(self) -> None:
        from pyqtgraph.Qt import QtWidgets

        for row, command in enumerate(self._commands):
            description = action_description(command)
            if command.action in self._unavailable:
                description += " (unavailable)"
            chords = sorted(self._working.chords_for(command), key=lambda c: (c[1], c[0]))
            keys = ", ".join(chord_label(*chord) for chord in chords) or "(none)"
            self._table.setItem(row, 0, QtWidgets.QTableWidgetItem(description))
            self._table.setItem(row, 1, QtWidgets.QTableWidgetItem(keys))
        self._table.resizeColumnsToContents()
