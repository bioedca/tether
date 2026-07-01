# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the curation keymap + controller + focus contract (M2 S2, PRD §7.3).

Two layers, mirroring ``test_trace_dock`` / ``test_movie_panel``:

* **Pure** checks of the Qt-free core — the :class:`CurationAction`/:class:`Command`
  vocabulary, the :class:`CurationController` routing, and the :class:`Keymap`
  bind/rebind/lookup/persistence logic (built from raw key ints, no Qt) — run in
  the default matrix because ``tether.gui.curation`` imports Qt lazily.
* **``@pytest.mark.gui``** smokes that install a real
  :class:`CurationEventFilter` on the pytest-qt ``QApplication`` and assert the
  §7.3 focus contract at the **controller level** (no pixels): the four bare keys
  fire from each child-widget focus, a text-entry is exempt, ``0`` clears to
  *uncategorized* (distinct from an accepted-but-uncategorized ``Space``), ``↑``/
  ``↓`` alias ``←``/``→``, the overflow picker assigns a category beyond the first
  nine, a rebinding fires the new key while the former default no-ops, and the
  cheat-sheet reflects the current bindings.
"""

from __future__ import annotations

import importlib.util
from types import SimpleNamespace

import pytest

from tether.gui.curation import (
    CATEGORY_SLOTS,
    UNCATEGORIZED_CLASS,
    Command,
    CurationAction,
    CurationController,
    CurationHandlers,
    Keymap,
    action_description,
    is_text_entry,
)

_HAS_QT = all(importlib.util.find_spec(m) is not None for m in ("pyqtgraph", "PySide6"))
_needs_qt = pytest.mark.skipif(not _HAS_QT, reason="pyqtgraph/PySide6 not installed")

A = CurationAction


# --- pure core (no Qt) -------------------------------------------------------


def test_command_defaults_and_equality() -> None:
    assert Command(A.ACCEPT).arg is None
    assert Command(A.ASSIGN_CATEGORY, 3).arg == 3
    assert Command(A.ACCEPT) == Command(A.ACCEPT)
    assert Command(A.ASSIGN_CATEGORY, 1) != Command(A.ASSIGN_CATEGORY, 2)


def test_command_enforces_category_arg_contract() -> None:
    # ASSIGN_CATEGORY requires an integer class >= 1 (0 is reserved for the
    # uncategorized/CLEAR_CATEGORY null state); every other action carries no arg.
    assert Command(A.ASSIGN_CATEGORY, 1).arg == 1
    assert Command(A.ASSIGN_CATEGORY, 12).arg == 12  # overflow class
    for bad in (None, 0, -1, 1.0, True):
        with pytest.raises(ValueError, match="ASSIGN_CATEGORY"):
            Command(A.ASSIGN_CATEGORY, bad)
    with pytest.raises(ValueError, match="takes no arg"):
        Command(A.ACCEPT, 3)
    with pytest.raises(ValueError, match="takes no arg"):
        Command(A.CLEAR_CATEGORY, 0)


def test_controller_routes_every_action_to_its_handler() -> None:
    calls: list[tuple] = []
    handlers = CurationHandlers(
        accept=lambda: calls.append(("accept",)),
        reject=lambda: calls.append(("reject",)),
        jump=lambda: calls.append(("jump",)),
        idealize=lambda: calls.append(("idealize",)),
        next=lambda: calls.append(("next",)),
        prev=lambda: calls.append(("prev",)),
        assign_category=lambda c: calls.append(("cat", c)),
        clear_category=lambda: calls.append(("clear",)),
        window_start=lambda d: calls.append(("wstart", d)),
        window_end=lambda d: calls.append(("wend", d)),
        reset_window=lambda: calls.append(("reset",)),
        photobleach=lambda: calls.append(("pb",)),
        grid=lambda: calls.append(("grid",)),
    )
    controller = CurationController(handlers)
    commands = [
        Command(A.ACCEPT),
        Command(A.REJECT),
        Command(A.JUMP),
        Command(A.IDEALIZE),
        Command(A.NEXT),
        Command(A.PREV),
        Command(A.ASSIGN_CATEGORY, 3),
        Command(A.CLEAR_CATEGORY),
        Command(A.WINDOW_START_DEC),
        Command(A.WINDOW_START_INC),
        Command(A.WINDOW_END_DEC),
        Command(A.WINDOW_END_INC),
        Command(A.RESET_WINDOW),
        Command(A.PHOTOBLEACH),
        Command(A.GRID),
        Command(A.RESERVED),
    ]
    for command in commands:
        controller.dispatch(command)

    assert ("cat", 3) in calls
    # window nudges carry the signed ±1 delta from the distinct dec/inc actions.
    assert ("wstart", -1) in calls and ("wstart", 1) in calls
    assert ("wend", -1) in calls and ("wend", 1) in calls
    # Every command is recorded; RESERVED (C/V) is recorded but fires no handler.
    assert controller.history == commands
    assert controller.last == Command(A.RESERVED)
    assert len(calls) == len(commands) - 1  # RESERVED has no handler


def test_controller_missing_handlers_are_silent_noops() -> None:
    controller = CurationController()  # no handlers wired
    controller.dispatch(Command(A.ACCEPT))
    controller.dispatch(Command(A.ASSIGN_CATEGORY, 5))
    assert controller.last == Command(A.ASSIGN_CATEGORY, 5)
    assert len(controller.history) == 2


def test_keymap_bind_lookup_and_chords_for() -> None:
    km = Keymap()  # empty, Qt-free
    km.bind(32, Command(A.ACCEPT))  # 32 == Key_Space
    km.bind(65, Command(A.ACCEPT))  # a second chord for the same action
    assert km.command_for(32) == Command(A.ACCEPT)
    assert km.command_for(999) is None
    assert set(km.chords_for(Command(A.ACCEPT))) == {(32, 0), (65, 0)}


def test_keymap_rebind_unbinds_former_chord() -> None:
    km = Keymap()
    km.bind(32, Command(A.ACCEPT))
    km.rebind(Command(A.ACCEPT), 65)  # move accept from 32 to 65
    assert km.command_for(65) == Command(A.ACCEPT)
    assert km.command_for(32) is None  # former default is now a no-op


def test_keymap_save_load_roundtrip(tmp_path) -> None:
    ctrl = 0x04000000  # Qt.ControlModifier's int value (kept literal to stay Qt-free)
    km = Keymap()
    km.bind(32, Command(A.ACCEPT))
    km.bind(49, Command(A.ASSIGN_CATEGORY, 1))
    km.bind(83, Command(A.IDEALIZE), modifiers=ctrl)  # a modified chord
    path = tmp_path / "sub" / "keymap.json"
    km.save(path)
    loaded = Keymap.load(path)
    assert loaded.bindings == km.bindings
    # The modifiers column survives persistence, not just the key code.
    assert loaded.bindings[(83, ctrl)] == Command(A.IDEALIZE)
    assert loaded.command_for(83, ctrl) == Command(A.IDEALIZE)
    assert loaded.command_for(83) is None  # bare 'S' is not the modified chord


def test_action_description_includes_category_number() -> None:
    assert action_description(Command(A.ASSIGN_CATEGORY, 7)) == "Assign category 7"
    assert action_description(Command(A.ACCEPT)) == "Accept trace"
    assert "uncategorized" in action_description(Command(A.CLEAR_CATEGORY)).lower()


def test_uncategorized_class_is_zero_and_nine_slots() -> None:
    assert UNCATEGORIZED_CLASS == 0
    assert CATEGORY_SLOTS == 9


def test_is_text_entry_false_for_none_and_plain_object() -> None:
    # The exemption predicate is Qt-free for the non-widget path (no Qt import).
    assert is_text_entry(None) is False
    assert is_text_entry(object()) is False


# --- GUI focus-contract smokes (@pytest.mark.gui) ----------------------------


def _key_event(key, modifiers=None, *, press=True, text=""):
    from pyqtgraph.Qt import QtCore, QtGui

    qt = QtCore.Qt
    mods = modifiers if modifiers is not None else qt.KeyboardModifier.NoModifier
    etype = QtCore.QEvent.Type.KeyPress if press else QtCore.QEvent.Type.KeyRelease
    return QtGui.QKeyEvent(etype, int(key), mods, text)


@pytest.fixture
def curation(qapp, qtbot):
    """An installed CurationEventFilter with a recording controller (removed on teardown)."""
    from tether.gui.curation import CurationController, CurationEventFilter

    controller = CurationController()
    keymap = Keymap.default()
    filt = CurationEventFilter(controller, keymap)
    filt.install(qapp)
    yield SimpleNamespace(controller=controller, keymap=keymap, filter=filt, qtbot=qtbot)
    filt.remove()


@pytest.mark.gui
@_needs_qt
def test_default_keymap_covers_inherited_and_tether_bindings() -> None:
    from pyqtgraph.Qt import QtCore

    k = QtCore.Qt.Key
    km = Keymap.default()
    # Tether-only bare keys.
    assert km.command_for(k.Key_Space) == Command(A.ACCEPT)
    assert km.command_for(k.Key_Backspace) == Command(A.REJECT)
    assert km.command_for(k.Key_Delete) == Command(A.REJECT)
    assert km.command_for(k.Key_Return) == Command(A.JUMP)
    assert km.command_for(k.Key_Enter) == Command(A.JUMP)  # numpad Enter
    assert km.command_for(k.Key_I) == Command(A.IDEALIZE)
    # Inherited categories: 1-9 -> classes 1-9; 0 -> clear/uncategorized.
    assert km.command_for(k.Key_1) == Command(A.ASSIGN_CATEGORY, 1)
    assert km.command_for(k.Key_9) == Command(A.ASSIGN_CATEGORY, 9)
    assert km.command_for(k.Key_0) == Command(A.CLEAR_CATEGORY)
    # Inherited window nudges (distinct start/end bounds).
    assert km.command_for(k.Key_Minus) == Command(A.WINDOW_START_DEC)
    assert km.command_for(k.Key_Equal) == Command(A.WINDOW_START_INC)
    assert km.command_for(k.Key_BracketLeft) == Command(A.WINDOW_END_DEC)
    assert km.command_for(k.Key_BracketRight) == Command(A.WINDOW_END_INC)
    # Inherited single keys + reserved no-ops.
    assert km.command_for(k.Key_R) == Command(A.RESET_WINDOW)
    assert km.command_for(k.Key_P) == Command(A.PHOTOBLEACH)
    assert km.command_for(k.Key_G) == Command(A.GRID)
    assert km.command_for(k.Key_C) == Command(A.RESERVED)
    assert km.command_for(k.Key_V) == Command(A.RESERVED)


@pytest.mark.gui
@_needs_qt
def test_arrow_up_down_alias_left_right(curation) -> None:
    from pyqtgraph.Qt import QtCore

    k = QtCore.Qt.Key
    km = curation.keymap
    assert km.command_for(k.Key_Up) == km.command_for(k.Key_Left) == Command(A.PREV)
    assert km.command_for(k.Key_Down) == km.command_for(k.Key_Right) == Command(A.NEXT)


@pytest.mark.gui
@_needs_qt
def test_four_bare_keys_fire_from_each_child_focus(curation) -> None:
    from pyqtgraph.Qt import QtCore, QtWidgets

    k = QtCore.Qt.Key
    # A molecule list, a movie switcher, and a napari-canvas surrogate: none is a
    # text entry, so the four bare curation keys must reach the controller from
    # each, regardless of which holds focus.
    surfaces = [QtWidgets.QListWidget(), QtWidgets.QComboBox(), QtWidgets.QWidget()]
    expected = [
        (k.Key_Space, Command(A.ACCEPT)),
        (k.Key_Backspace, Command(A.REJECT)),
        (k.Key_Delete, Command(A.REJECT)),
        (k.Key_Return, Command(A.JUMP)),
    ]
    for surface in surfaces:
        curation.qtbot.addWidget(surface)
        for key, command in expected:
            before = len(curation.controller.history)
            consumed = curation.filter.filter_event(surface, _key_event(key), focus_widget=surface)
            assert consumed is True  # native binding suppressed on this surface
            assert len(curation.controller.history) == before + 1
            assert curation.controller.last == command


@pytest.mark.gui
@_needs_qt
def test_text_entry_is_exempt_from_the_keymap(curation) -> None:
    from pyqtgraph.Qt import QtCore, QtWidgets

    k = QtCore.Qt.Key
    field = QtWidgets.QLineEdit()
    curation.qtbot.addWidget(field)
    assert is_text_entry(field) is True
    # Space on the focused text field is NOT consumed and NOT dispatched: it keeps
    # its text semantics (PRD §7.3 exemption for the editable category field).
    consumed = curation.filter.filter_event(field, _key_event(k.Key_Space))
    assert consumed is False
    assert curation.controller.history == []


@pytest.mark.gui
@_needs_qt
def test_property_marked_widget_is_exempt(curation) -> None:
    from pyqtgraph.Qt import QtCore, QtWidgets

    k = QtCore.Qt.Key
    custom = QtWidgets.QWidget()
    custom.setProperty("tetherTextEntry", True)
    curation.qtbot.addWidget(custom)
    assert is_text_entry(custom) is True
    assert curation.filter.filter_event(custom, _key_event(k.Key_Space)) is False
    assert curation.controller.history == []


@pytest.mark.gui
@_needs_qt
def test_focused_text_field_exempts_even_when_event_propagates(curation) -> None:
    from pyqtgraph.Qt import QtCore, QtWidgets

    k = QtCore.Qt.Key
    field = QtWidgets.QLineEdit()
    parent = QtWidgets.QWidget()  # a non-text ancestor
    curation.qtbot.addWidget(field)
    curation.qtbot.addWidget(parent)
    # A key the focused field ignores (Enter) propagates up to a non-text parent,
    # so the event's target is non-text while focus stays on the field. Keyed on
    # the focused widget, the filter still exempts it — editing text never jumps.
    consumed = curation.filter.filter_event(parent, _key_event(k.Key_Return), focus_widget=field)
    assert consumed is False
    assert curation.controller.history == []
    # But with a non-text widget focused, the same key on the parent dispatches.
    consumed = curation.filter.filter_event(parent, _key_event(k.Key_Return), focus_widget=parent)
    assert consumed is True
    assert curation.controller.last == Command(A.JUMP)


@pytest.mark.gui
@_needs_qt
def test_zero_clears_to_uncategorized_distinct_from_space_accept(curation) -> None:
    from pyqtgraph.Qt import QtCore, QtWidgets

    k = QtCore.Qt.Key
    surface = QtWidgets.QListWidget()
    curation.qtbot.addWidget(surface)
    # Space accepts but assigns NO category — the trace stays accepted-but-
    # uncategorized (class 0), never an ASSIGN_CATEGORY (a named class >= 1).
    curation.filter.filter_event(surface, _key_event(k.Key_Space), focus_widget=surface)
    assert curation.controller.last == Command(A.ACCEPT)
    assert curation.controller.last.action is not A.ASSIGN_CATEGORY
    # 0 clears the category back to the uncategorized null state — again distinct
    # from assigning any named category, and distinct from the Space accept.
    curation.filter.filter_event(surface, _key_event(k.Key_0), focus_widget=surface)
    assert curation.controller.last == Command(A.CLEAR_CATEGORY)
    assert curation.controller.last.action is not A.ASSIGN_CATEGORY
    assert Command(A.CLEAR_CATEGORY) != Command(A.ACCEPT)
    # class 0 is unreachable via ASSIGN_CATEGORY (contract-enforced); CLEAR/ACCEPT
    # are distinct from assigning any named category (>= 1).
    assert Command(A.CLEAR_CATEGORY) != Command(A.ASSIGN_CATEGORY, 1)
    assert Command(A.ACCEPT) != Command(A.ASSIGN_CATEGORY, 1)


@pytest.mark.gui
@_needs_qt
def test_unmapped_key_passes_through(curation) -> None:
    from pyqtgraph.Qt import QtCore, QtWidgets

    k = QtCore.Qt.Key
    surface = QtWidgets.QListWidget()
    curation.qtbot.addWidget(surface)
    # A key with no binding (Tab) is not consumed and not dispatched.
    assert (
        curation.filter.filter_event(surface, _key_event(k.Key_Tab), focus_widget=surface) is False
    )
    # A curation key with a stray modifier (Shift+Space) also passes through.
    shift = QtCore.Qt.KeyboardModifier.ShiftModifier
    stray = _key_event(k.Key_Space, shift)
    assert curation.filter.filter_event(surface, stray, focus_widget=surface) is False
    assert curation.controller.history == []


@pytest.mark.gui
@_needs_qt
def test_jump_consumes_and_refocuses_the_dock(qapp, qtbot) -> None:
    from unittest.mock import Mock

    from pyqtgraph.Qt import QtCore, QtWidgets

    from tether.gui.curation import CurationController, CurationEventFilter

    k = QtCore.Qt.Key
    dock = Mock()  # a refocus target: the filter only calls .setFocus() on it
    surface = QtWidgets.QListWidget()
    qtbot.addWidget(surface)
    controller = CurationController()
    filt = CurationEventFilter(controller, Keymap.default(), focus_dock=dock)
    filt.install(qapp)
    try:
        # A JUMP press dispatches AND refocuses the dock (the round-trip contract).
        assert filt.filter_event(surface, _key_event(k.Key_Return), focus_widget=surface) is True
        dock.setFocus.assert_called_once()
        # The paired release is consumed but neither dispatches nor refocuses again.
        release = _key_event(k.Key_Return, press=False)
        assert filt.filter_event(surface, release, focus_widget=surface) is True
        assert controller.history == [Command(A.JUMP)]  # release added nothing
        dock.setFocus.assert_called_once()  # still just the one refocus
        # Numpad Enter (Key_Enter) is a JUMP alias: dispatches and refocuses too.
        assert filt.filter_event(surface, _key_event(k.Key_Enter), focus_widget=surface) is True
        assert controller.history == [Command(A.JUMP), Command(A.JUMP)]
        assert dock.setFocus.call_count == 2
        # A non-JUMP key (Space = accept) does not refocus the dock.
        dock.reset_mock()
        assert filt.filter_event(surface, _key_event(k.Key_Space), focus_widget=surface) is True
        dock.setFocus.assert_not_called()
    finally:
        filt.remove()


@pytest.mark.gui
@_needs_qt
def test_rebinding_fires_new_key_and_former_default_noops(curation) -> None:
    from pyqtgraph.Qt import QtCore, QtWidgets

    k = QtCore.Qt.Key
    surface = QtWidgets.QListWidget()
    curation.qtbot.addWidget(surface)
    # Rebind ACCEPT from Space to 'A'.
    curation.keymap.rebind(Command(A.ACCEPT), int(k.Key_A))
    # The new key fires ACCEPT...
    assert curation.filter.filter_event(surface, _key_event(k.Key_A), focus_widget=surface) is True
    assert curation.controller.last == Command(A.ACCEPT)
    # ...and the former default (Space) no longer dispatches ACCEPT.
    before = len(curation.controller.history)
    assert (
        curation.filter.filter_event(surface, _key_event(k.Key_Space), focus_widget=surface)
        is False
    )
    assert len(curation.controller.history) == before


@pytest.mark.gui
@_needs_qt
def test_cheatsheet_reflects_current_bindings() -> None:
    from pyqtgraph.Qt import QtCore

    k = QtCore.Qt.Key
    km = Keymap.default()
    default_rows = dict(km.cheatsheet())
    # "Space" and letter keys render identically on every platform; Backspace /
    # Delete render as native symbols (Del/⌫/⌦), so assert Reject by action value
    # rather than a platform-specific key label.
    assert default_rows.get("Space") == "Accept trace"
    assert "Reject trace" in default_rows.values()

    km.rebind(Command(A.ACCEPT), int(k.Key_A))
    rebound_rows = dict(km.cheatsheet())
    assert rebound_rows.get("A") == "Accept trace"
    assert "Space" not in rebound_rows  # the former accept chord is gone


@pytest.mark.gui
@_needs_qt
def test_default_keymap_save_load_roundtrip(tmp_path) -> None:
    km = Keymap.default()
    path = tmp_path / "keymap.json"
    km.save(path)
    assert Keymap.load(path).bindings == km.bindings


@pytest.mark.gui
@_needs_qt
def test_restore_defaults_reverts_a_rebinding() -> None:
    from pyqtgraph.Qt import QtCore

    k = QtCore.Qt.Key
    km = Keymap.default()
    km.rebind(Command(A.ACCEPT), int(k.Key_A))
    assert km.command_for(k.Key_Space) is None  # moved off Space
    km.restore_defaults()
    assert km.command_for(k.Key_Space) == Command(A.ACCEPT)  # back on Space
    assert km.command_for(k.Key_A) is None  # the rebinding is gone
    assert km.bindings == Keymap.default().bindings


@pytest.mark.gui
@_needs_qt
def test_key_release_consumed_only_when_its_press_was(curation) -> None:
    from pyqtgraph.Qt import QtCore, QtWidgets

    k = QtCore.Qt.Key
    surface = QtWidgets.QListWidget()
    field = QtWidgets.QLineEdit()
    curation.qtbot.addWidget(surface)
    curation.qtbot.addWidget(field)

    def prs(w):
        return curation.filter.filter_event(w, _key_event(k.Key_Space), focus_widget=w)

    def rel(w):
        return curation.filter.filter_event(w, _key_event(k.Key_Space, press=False), focus_widget=w)

    # A release with no consumed press passes through.
    assert rel(surface) is False
    # A release after an *exempt* (text-field) press is not consumed either.
    assert prs(field) is False
    assert rel(field) is False
    # But a release paired with a consumed press IS consumed (symmetry).
    assert prs(surface) is True
    assert rel(surface) is True
    # ...and only once — a second release is no longer paired.
    assert rel(surface) is False


@pytest.mark.gui
@_needs_qt
def test_default_keymap_path_is_under_a_tether_config_dir() -> None:
    from tether.gui.curation import default_keymap_path

    path = default_keymap_path()
    assert path.name == "keymap.json"
    assert "tether" in str(path).lower()
