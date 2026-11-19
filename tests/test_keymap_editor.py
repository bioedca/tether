# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The saved keymap at start and the keyboard-shortcut editor (#635, PRD §7.3).

The first block is Qt-free: the rules a saved keymap file must keep ([`Keymap.from_dict`][]),
the defaults a file from an older version gains, and restoring one command. The rest are
``@pytest.mark.gui`` tests on the pytest-qt ``QApplication``, headless. They pin the acceptance
criteria of #635: a binding changed in the editor acts at once and after a restart, two
commands cannot share one key, ``C`` and ``V`` cannot be bound, an invalid keymap file starts
the shell with the defaults and one message and is left as it is, and the cheat-sheet shows
the current bindings.
"""

from __future__ import annotations

import importlib.util
import json

import pytest

from tether.gui.curation import Command, Keymap, KeymapError
from tether.gui.curation import CurationAction as A

_HAS_QT = all(importlib.util.find_spec(m) is not None for m in ("pyqtgraph", "PySide6"))
_needs_qt = pytest.mark.skipif(not _HAS_QT, reason="pyqtgraph/PySide6 not installed")

# Qt key codes, literal so the Qt-free tests need no Qt (a letter's code is its ASCII code).
_SPACE, _KEY_A, _KEY_C, _KEY_I, _KEY_V = 0x20, 0x41, 0x43, 0x49, 0x56
_SHIFT = 0x02000000


def _row(key, action, arg=None, modifiers=0):
    return {"key": key, "modifiers": modifiers, "action": action, "arg": arg}


# --- the rules a saved file keeps (Qt-free) ---------------------------------


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        ([], "list of bindings"),
        ({"bindings": {}}, "list of bindings"),
        ({"bindings": ["accept"]}, "not an object"),
        ({"bindings": [_row(True, "accept")]}, "key code"),
        ({"bindings": [_row(-5, "accept")]}, "key code"),
        ({"bindings": [_row(2**40, "accept")]}, "key code"),  # QKeySequence would overflow
        ({"bindings": [_row(_SPACE, "accept", modifiers=0x01)]}, "key code"),  # not a modifier
        ({"bindings": [_row(_SPACE, "no-such-action")]}, "binding 1"),
        ({"bindings": [_row(_SPACE, "assign_category", 0)]}, "binding 1"),
        ({"bindings": [_row(_SPACE, "accept"), _row(_SPACE, "reject")]}, "already uses"),
        ({"bindings": [_row(_KEY_C, "accept")]}, "C and V"),
        ({"bindings": [_row(_KEY_V, "next", modifiers=_SHIFT)]}, "C and V"),
        ({"bindings": [_row(_SPACE, "reserved")]}, "C and V"),
    ],
)
def test_a_saved_keymap_breaking_a_rule_is_refused_whole(data, reason) -> None:
    with pytest.raises(KeymapError, match=reason):
        Keymap.from_dict(data)


def test_load_refuses_text_that_is_not_json_and_a_file_it_cannot_read(tmp_path) -> None:
    broken = tmp_path / "keymap.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(KeymapError, match="not valid JSON at line 1"):
        Keymap.load(broken)
    with pytest.raises(KeymapError, match="cannot be read"):
        Keymap.load(tmp_path)  # a folder: unreadable as a file on every OS


def test_a_file_from_an_older_version_gets_the_default_of_a_newer_command(tmp_path) -> None:
    defaults = Keymap()
    defaults.bind(_SPACE, Command(A.ACCEPT))
    defaults.bind(_KEY_I, Command(A.IDEALIZE))
    defaults.bind(_KEY_C, Command(A.RESERVED))
    # Saved before IDEALIZE existed: it has only accept, moved to A.
    path = tmp_path / "keymap.json"
    path.write_text(json.dumps({"bindings": [_row(_KEY_A, "accept")]}), encoding="utf-8")
    loaded = Keymap.load(path, defaults=defaults)
    assert loaded.command_for(_KEY_A) == Command(A.ACCEPT)
    assert loaded.command_for(_SPACE) is None  # the user's move is kept
    assert loaded.command_for(_KEY_I) == Command(A.IDEALIZE)
    assert loaded.command_for(_KEY_C) == Command(A.RESERVED)


def test_a_newer_commands_default_key_already_in_use_is_left_to_the_user() -> None:
    defaults = Keymap()
    defaults.bind(_KEY_I, Command(A.IDEALIZE))
    saved = Keymap()
    saved.bind(_KEY_I, Command(A.ACCEPT))
    assert saved.add_missing(defaults) == []
    assert saved.command_for(_KEY_I) == Command(A.ACCEPT)
    assert saved.chords_for(Command(A.IDEALIZE)) == []


def test_restore_default_names_the_command_in_the_way() -> None:
    defaults = Keymap()
    defaults.bind(_SPACE, Command(A.ACCEPT))
    km = Keymap()
    km.bind(_KEY_A, Command(A.ACCEPT))
    km.bind(_SPACE, Command(A.NEXT))
    assert km.restore_default(Command(A.ACCEPT), defaults) == Command(A.NEXT)
    assert km.command_for(_KEY_A) == Command(A.ACCEPT)  # nothing changed
    km.rebind(Command(A.NEXT), _KEY_V + 1)
    assert km.restore_default(Command(A.ACCEPT), defaults) is None
    assert km.chords_for(Command(A.ACCEPT)) == [(_SPACE, 0)]


def test_save_leaves_no_temporary_file(tmp_path) -> None:
    km = Keymap()
    km.bind(_SPACE, Command(A.ACCEPT))
    km.save(tmp_path / "keymap.json")
    km.save(tmp_path / "keymap.json")  # over an existing file too
    assert [p.name for p in tmp_path.iterdir()] == ["keymap.json"]
    assert Keymap.load(tmp_path / "keymap.json").bindings == km.bindings


# --- the shell and the editor (Qt) -------------------------------------------


@pytest.fixture
def launch(qapp, qtbot):
    from tether.gui.shell import TetherShell

    shells = []

    def _launch(keymap_file=None):
        shell = TetherShell(keymap_file=keymap_file)
        qtbot.addWidget(shell.window)
        shells.append(shell)
        return shell

    yield _launch
    for shell in shells:
        shell.close()


def _qt():
    from pyqtgraph.Qt import QtCore

    return QtCore.Qt.Key, QtCore.Qt.KeyboardModifier


def _label(key) -> str:
    from tether.gui.keymap_editor import chord_label

    return chord_label(int(key))


def _cheatsheet(shell) -> dict[str, str]:
    shell.cheatsheet.refresh()
    return dict(shell.cheatsheet.rows)


def _menu_action(shell, menu_title, action_text):
    from pyqtgraph.Qt import QtWidgets

    (menu,) = [m for m in shell.window.findChildren(QtWidgets.QMenu) if m.title() == menu_title]
    return {a.text(): a for a in menu.actions()}[action_text]


@pytest.mark.gui
@_needs_qt
def test_a_binding_changed_in_the_editor_acts_at_once_and_after_a_restart(
    launch, qtbot, tmp_path
) -> None:
    key, _ = _qt()
    path = tmp_path / "config" / "keymap.json"
    shell = launch(path)
    # The menu action itself, so a disconnected action fails.
    _menu_action(shell, "&Curation", "&Edit keyboard shortcuts…").trigger()
    editor = shell._keymap_editor
    assert editor is not None and editor.dialog.isVisible()

    editor.select(Command(A.ACCEPT))
    qtbot.keyClick(editor.capture_field, key.Key_A)
    assert editor.pending_chord == (int(key.Key_A), 0)
    assert editor.assign_button.isEnabled()
    editor.assign_button.click()
    assert dict(editor.rows())["Accept trace"] == "A"
    # Nothing is in effect before Save.
    assert shell.keymap.command_for(key.Key_A) is None
    editor.save()
    assert path.exists()
    assert shell.status_message == f"Keyboard shortcuts saved to {path}"

    # In effect at once: A accepts, Space no longer does, and the cheat-sheet says so.
    qtbot.keyClick(shell.molecule_list, key.Key_A)
    assert shell.controller.history == [Command(A.ACCEPT)]
    qtbot.keyClick(shell.molecule_list, key.Key_Space)
    assert shell.controller.history == [Command(A.ACCEPT)]
    rows = _cheatsheet(shell)
    assert rows.get("A") == "Accept trace"
    assert _label(key.Key_Space) not in rows
    shell.close()

    # And after a restart.
    restarted = launch(path)
    assert restarted.keymap_error is None
    assert restarted.status_message == f"Ready — A accept · {_label(key.Key_Backspace)} reject"
    assert _cheatsheet(restarted).get("A") == "Accept trace"
    qtbot.keyClick(restarted.molecule_list, key.Key_A)
    assert restarted.controller.history == [Command(A.ACCEPT)]


@pytest.mark.gui
@_needs_qt
def test_two_commands_cannot_share_one_key(launch) -> None:
    key, _ = _qt()
    shell = launch()
    editor = shell.show_keymap_editor()
    editor.select(Command(A.ACCEPT))
    problem = editor.propose(key.Key_Right)
    # The conflict shows before anything is accepted, and Assign stays off.
    assert problem is not None and "Next trace" in problem
    assert editor.message == problem
    assert not editor.assign_button.isEnabled()
    assert editor.assign() is False
    assert editor.keymap.command_for(key.Key_Right) == Command(A.NEXT)
    assert editor.keymap.command_for(key.Key_Space) == Command(A.ACCEPT)
    # A command may keep the key it already has.
    assert editor.propose(key.Key_Space) is None


@pytest.mark.gui
@_needs_qt
@pytest.mark.parametrize("letter", ["Key_C", "Key_V"])
@pytest.mark.parametrize("shift", [False, True])
def test_c_and_v_cannot_be_bound(launch, qtbot, letter, shift) -> None:
    key, mod = _qt()
    shell = launch()
    editor = shell.show_keymap_editor()
    editor.select(Command(A.GRID))
    modifier = mod.ShiftModifier if shift else mod.NoModifier
    qtbot.keyClick(editor.capture_field, getattr(key, letter), modifier)
    assert editor.message == "C and V are reserved and cannot be bound."
    assert not editor.assign_button.isEnabled()
    assert editor.assign() is False
    # They stay the reserved no-ops, and are not listed as editable actions.
    assert editor.keymap.command_for(getattr(key, letter)) == Command(A.RESERVED)
    assert Command(A.RESERVED) not in editor.commands


@pytest.mark.gui
@_needs_qt
def test_a_key_a_menu_command_owns_is_refused(launch, qtbot) -> None:
    key, mod = _qt()
    shell = launch()
    editor = shell.show_keymap_editor()
    editor.select(Command(A.GRID))
    qtbot.keyClick(editor.capture_field, key.Key_O, mod.ControlModifier)
    assert "Open project" in editor.message
    assert not editor.assign_button.isEnabled()


@pytest.mark.gui
@_needs_qt
def test_a_menu_titles_mnemonic_is_refused(launch) -> None:
    from pyqtgraph.Qt import QtGui

    from tether.gui.curation import _as_int

    shell = launch()
    editor = shell.show_keymap_editor()
    editor.select(Command(A.ACCEPT))
    mnemonic = QtGui.QKeySequence.mnemonic("&File")
    if mnemonic.count() == 0:
        pytest.skip("this platform has no menu mnemonics (macOS)")
    chord = mnemonic[0]
    problem = editor.propose(_as_int(chord.key()), _as_int(chord.keyboardModifiers()))
    assert problem is not None and "File menu" in problem


@pytest.mark.gui
@_needs_qt
def test_tab_escape_and_a_bare_modifier_are_not_captured(launch, qtbot) -> None:
    key, _ = _qt()
    shell = launch()
    editor = shell.show_keymap_editor()
    for k in (key.Key_Tab, key.Key_Escape, key.Key_Shift):
        qtbot.keyClick(editor.capture_field, k)
        assert editor.pending_chord is None
    assert "cannot be a shortcut" in (editor.problem(Command(A.GRID), key.Key_Tab) or "")


@pytest.mark.gui
@_needs_qt
def test_restore_one_command_and_all(launch) -> None:
    key, _ = _qt()
    shell = launch()
    editor = shell.show_keymap_editor()
    editor.select(Command(A.REJECT))
    assert editor.propose(key.Key_X) is None and editor.assign()
    assert dict(editor.rows())["Reject trace"] == "X"
    assert editor.restore_selected()
    assert set(editor.keymap.chords_for(Command(A.REJECT))) == {
        (int(key.Key_Backspace), 0),
        (int(key.Key_Delete), 0),
    }

    # Move PREV off Left and Up, give Left to ACCEPT: PREV's default is now in the way.
    editor.select(Command(A.PREV))
    assert editor.propose(key.Key_U) is None and editor.assign()
    editor.select(Command(A.ACCEPT))
    assert editor.propose(key.Key_Left) is None and editor.assign()
    editor.select(Command(A.PREV))
    assert editor.restore_selected() is False
    assert "Accept trace" in editor.message
    assert editor.keymap.chords_for(Command(A.PREV)) == [(int(key.Key_U), 0)]

    editor.restore_all()
    assert editor.keymap.bindings == Keymap.default().bindings


@pytest.mark.gui
@_needs_qt
def test_cancel_drops_the_edits(launch, tmp_path) -> None:
    key, _ = _qt()
    path = tmp_path / "keymap.json"
    shell = launch(path)
    editor = shell.show_keymap_editor()
    editor.select(Command(A.ACCEPT))
    assert editor.propose(key.Key_A) is None and editor.assign()
    editor.dialog.reject()
    assert shell.keymap.command_for(key.Key_Space) == Command(A.ACCEPT)
    assert shell.keymap.command_for(key.Key_A) is None
    assert not path.exists()


@pytest.mark.gui
@_needs_qt
def test_a_rebound_bare_key_still_types_in_the_category_field(launch) -> None:
    from pyqtgraph.Qt import QtCore, QtGui

    key, mod = _qt()
    shell = launch()
    editor = shell.show_keymap_editor()
    editor.select(Command(A.ACCEPT))
    assert editor.propose(key.Key_A) is None and editor.assign()
    editor.save()
    field = shell.category_field
    press = QtGui.QKeyEvent(QtCore.QEvent.Type.KeyPress, int(key.Key_A), mod.NoModifier, "a")
    # The text field keeps the key: the filter passes it through and nothing curates.
    assert shell.event_filter.filter_event(field, press, focus_widget=field) is False
    assert shell.controller.history == []
    surface = shell.molecule_list
    assert shell.event_filter.filter_event(surface, press, focus_widget=surface) is True
    assert shell.controller.history == [Command(A.ACCEPT)]


@pytest.mark.gui
@_needs_qt
@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        json.dumps({"bindings": [_row(0x43, "accept")]}),
        json.dumps({"bindings": [_row(0x20, "accept"), _row(0x20, "next")]}),
    ],
)
def test_an_invalid_keymap_file_starts_with_the_defaults_and_one_message(
    launch, tmp_path, content
) -> None:
    path = tmp_path / "keymap.json"
    path.write_text(content, encoding="utf-8")
    before = path.read_bytes()
    shell = launch(path)
    assert shell.keymap.bindings == Keymap.default().bindings
    assert shell.keymap_error is not None
    assert str(path) in shell.keymap_error
    assert "default keys" in shell.keymap_error
    # The one message is the start-up status line.
    assert shell.status_message == shell.keymap_error
    shell.close()
    assert path.read_bytes() == before  # left as it is


@pytest.mark.gui
@_needs_qt
def test_no_keymap_file_starts_with_the_defaults_silently(launch, tmp_path) -> None:
    shell = launch(tmp_path / "absent.json")
    assert shell.keymap_error is None
    key, _ = _qt()
    backspace = _label(key.Key_Backspace)  # the native text: "⌫" on macOS
    assert shell.status_message == f"Ready — {_label(key.Key_Space)} accept · {backspace} reject"
    assert not (tmp_path / "absent.json").exists()


@pytest.mark.gui
@_needs_qt
def test_a_failed_save_keeps_the_keys_for_the_session(launch, tmp_path) -> None:
    key, _ = _qt()
    blocker = tmp_path / "a-file"
    blocker.write_text("", encoding="utf-8")
    shell = launch(blocker / "keymap.json")  # its folder is a file: the write fails
    editor = shell.show_keymap_editor()
    editor.select(Command(A.ACCEPT))
    assert editor.propose(key.Key_A) is None and editor.assign()
    editor.save()
    assert shell.keymap.command_for(key.Key_A) == Command(A.ACCEPT)
    assert "not saved" in shell.status_message
