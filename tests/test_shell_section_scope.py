# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The menu bar and the curation keymap are scoped to the current section (#342).

These ``@pytest.mark.gui`` tests run headless on the pytest-qt ``QApplication``. They pin the
acceptance criteria of #342: the menus of the sections are built from the
[`SectionRegistry`][tether.gui.sections.SectionRegistry] and show only while their section is
current; a curation key pressed while Prepare or Analyze is current never reaches the
`CurationController`, and keeps its native meaning there; and the cheat-sheet lists only the
bindings that act in the current section, asserted against the live `Keymap`.
"""

from __future__ import annotations

import importlib.util

import pytest

_HAS_QT = all(importlib.util.find_spec(m) is not None for m in ("pyqtgraph", "PySide6"))
pytestmark = [
    pytest.mark.gui,
    pytest.mark.skipif(not _HAS_QT, reason="pyqtgraph/PySide6 not installed"),
]

#: The window's own menus: they show in every section.
_WINDOW_MENUS_BEFORE = ["File", "Edit"]  # Edit: undo and redo of the curation actions (#632)
_WINDOW_MENUS_AFTER = ["View", "Help"]

#: The sections whose central page has no curation keys.
_KEYLESS_SECTIONS = ("prepare", "analyze")


@pytest.fixture
def shell(qapp, qtbot):
    from tether.gui.shell import TetherShell

    s = TetherShell()
    qtbot.addWidget(s.window)
    yield s
    s.close()


def _plain(text: str) -> str:
    return text.replace("&", "")


def _bar_titles(shell, *, visible_only: bool) -> list[str]:
    actions = shell.window.menuBar().actions()
    return [_plain(a.text()) for a in actions if a.isVisible() or not visible_only]


def _key_event(key, *, press=True, auto_repeat=False):
    from pyqtgraph.Qt import QtCore, QtGui

    etype = QtCore.QEvent.Type.KeyPress if press else QtCore.QEvent.Type.KeyRelease
    no_mod = QtCore.Qt.KeyboardModifier.NoModifier
    return QtGui.QKeyEvent(etype, int(key), no_mod, "", auto_repeat, 1)


def _cheatsheet_cells(overlay):
    from pyqtgraph.Qt import QtWidgets

    table = overlay.dialog.findChild(QtWidgets.QTableWidget)
    return [(table.item(r, 0).text(), table.item(r, 1).text()) for r in range(table.rowCount())]


# --- the menus are built from the registry ---------------------------------


def test_each_section_has_the_menus_its_specs_declare(shell) -> None:
    for spec in shell.sections.visible_sections():
        menus = shell.section_menus(spec.id)
        assert [m.title() for m in menus] == [m.title for m in spec.menus]
        assert [m.objectName() for m in menus] == [f"{spec.id}-{m.id}-menu" for m in spec.menus]
    assert [_plain(m.title()) for m in shell.section_menus("prepare")] == [
        "Import",
        "Calibration",
    ]
    assert [_plain(m.title()) for m in shell.section_menus("curate")] == [
        "Curation",
        "Hand-off",
        "Conditions",
        "Corrections",
        "Idealize",
    ]
    # The Analyze section's plots live on its page, so it has no menu of its own (#374).
    assert shell.section_menus("analyze") == []
    with pytest.raises(KeyError):
        shell.section_menus("deposit")


def test_the_menu_bar_is_file_the_section_menus_in_registry_order_then_view_and_help(
    shell,
) -> None:
    section_titles = [_plain(m.title) for spec in shell.sections for m in spec.menus]
    assert _bar_titles(shell, visible_only=False) == (
        _WINDOW_MENUS_BEFORE + section_titles + _WINDOW_MENUS_AFTER
    )


def test_a_menu_added_to_the_registry_appears_on_the_menu_bar(qapp, qtbot, monkeypatch) -> None:
    from dataclasses import replace

    from tether.gui.sections import MenuSpec, SectionRegistry
    from tether.gui.shell import TetherShell

    built = TetherShell._build_sections
    populated: list[object] = []

    def with_extra_menu(self):
        def extra(spec):
            if spec.id != "analyze":
                return spec
            menu = MenuSpec(
                id="extra",
                title="E&xtra",
                populate=lambda host, m: populated.append((host, m.addAction("Probe"))),
            )
            return replace(spec, menus=(*spec.menus, menu))

        return SectionRegistry(extra(spec) for spec in built(self))

    monkeypatch.setattr(TetherShell, "_build_sections", with_extra_menu)
    s = TetherShell()
    qtbot.addWidget(s.window)
    try:
        assert [_plain(m.title()) for m in s.section_menus("analyze")] == ["Extra"]
        (host, action), *rest = populated
        assert not rest
        assert host is s
        assert [a.text() for a in s.section_menus("analyze")[0].actions()] == [action.text()]
        assert "Extra" not in _bar_titles(s, visible_only=True)
        s.set_section("analyze")
        assert "Extra" in _bar_titles(s, visible_only=True)
    finally:
        s.close()


def test_only_the_current_sections_menus_show(shell) -> None:
    for section_id in ("curate", "prepare", "analyze", "curate"):
        shell.set_section(section_id)
        own = [_plain(m.title()) for m in shell.section_menus(section_id)]
        assert _bar_titles(shell, visible_only=True) == (
            _WINDOW_MENUS_BEFORE + own + _WINDOW_MENUS_AFTER
        )


def test_the_shell_opens_with_the_curate_menus(shell) -> None:
    assert shell.section == "curate"
    assert _bar_titles(shell, visible_only=True) == [
        "File",
        "Edit",
        "Curation",
        "Hand-off",
        "Conditions",
        "Corrections",
        "Idealize",
        "View",
        "Help",
    ]


def test_no_section_menu_action_has_a_shortcut(shell) -> None:
    # A hidden menu's action must not be reachable from the keyboard either, and a bare
    # shortcut would shadow a curation key (§7.3).
    for spec in shell.sections.visible_sections():
        for menu in shell.section_menus(spec.id):
            for action in menu.actions():
                assert action.shortcut().isEmpty(), action.text()


def test_the_help_menu_keeps_the_shortcuts_item_in_every_section(shell) -> None:
    for section_id in shell.sections.visible_ids:
        shell.set_section(section_id)
        help_items = [_plain(a.text()) for a in shell.help_menu.actions() if a.isVisible()]
        assert "Keyboard shortcuts…" in help_items


# --- the curation keymap is scoped to Curate and correct -------------------


def test_the_keymap_is_active_only_in_curate_and_correct(shell) -> None:
    from tether.gui.curation import CURATION_SCOPE

    for spec in shell.sections.visible_sections():
        shell.set_section(spec.id)
        assert shell.keymap_active is (spec.keymap_scope == CURATION_SCOPE)
        assert shell.event_filter.active is shell.keymap_active
    assert [s.id for s in shell.sections if s.keymap_scope == CURATION_SCOPE] == ["curate"]


@pytest.mark.parametrize("section_id", _KEYLESS_SECTIONS)
def test_no_bound_chord_reaches_the_controller_outside_curate(shell, section_id) -> None:
    shell.set_section(section_id)
    page = shell.section_page(section_id)
    bindings = shell.keymap.bindings
    assert bindings  # the full default keymap, every chord
    for key, _modifiers in bindings:
        consumed = shell.event_filter.filter_event(page, _key_event(key), focus_widget=page)
        # Not consumed: the key keeps its native meaning in this section.
        assert consumed is False
        released = shell.event_filter.filter_event(
            page, _key_event(key, press=False), focus_widget=page
        )
        assert released is False
    assert shell.controller.history == []


@pytest.mark.parametrize("section_id", _KEYLESS_SECTIONS)
def test_a_real_keystroke_outside_curate_does_not_curate(shell, qtbot, section_id) -> None:
    from pyqtgraph.Qt import QtCore

    k = QtCore.Qt.Key
    shell.set_section(section_id)
    for key in (k.Key_Space, k.Key_Backspace, k.Key_Delete, k.Key_Right, k.Key_I, k.Key_1):
        qtbot.keyClick(shell.section_page(section_id), key)
    assert shell.controller.history == []


def test_the_same_keystroke_curates_after_returning_to_curate(shell, qtbot) -> None:
    from pyqtgraph.Qt import QtCore

    from tether.gui.curation import Command, CurationAction

    shell.set_section("analyze")
    qtbot.keyClick(shell.molecule_list, QtCore.Qt.Key.Key_Space)
    assert shell.controller.history == []
    shell.set_section("curate")
    qtbot.keyClick(shell.molecule_list, QtCore.Qt.Key.Key_Space)
    assert shell.controller.history == [Command(CurationAction.ACCEPT)]


def test_a_release_after_a_switch_is_consumed_with_its_press(shell) -> None:
    from pyqtgraph.Qt import QtCore

    space = QtCore.Qt.Key.Key_Space
    surface = shell.molecule_list
    assert shell.event_filter.filter_event(surface, _key_event(space), focus_widget=surface)
    # The curator switches section while the key is held: the release still goes with
    # its press, so no widget of the new section sees half a chord.
    shell.set_section("analyze")
    assert shell.event_filter.filter_event(surface, _key_event(space, press=False)) is True


def test_the_text_entry_exemption_is_unchanged_in_curate(shell) -> None:
    from pyqtgraph.Qt import QtCore

    from tether.gui.category_editor import CategoryEditor

    field = CategoryEditor(
        "c", [], usage=len, add=None, rename=None, remove=None, move=None, parent=shell.window
    ).name_field
    assert shell.section == "curate"
    consumed = shell.event_filter.filter_event(
        field, _key_event(QtCore.Qt.Key.Key_Space), focus_widget=field
    )
    assert consumed is False
    assert shell.controller.history == []


def test_the_event_filter_predicate_gates_dispatch(qapp) -> None:
    from pyqtgraph.Qt import QtCore, QtWidgets

    from tether.gui.curation import (
        Command,
        CurationAction,
        CurationController,
        CurationEventFilter,
    )

    active = [False]
    controller = CurationController()
    filt = CurationEventFilter(controller, scope_active=lambda: active[0])
    surface = QtWidgets.QListWidget()
    space = QtCore.Qt.Key.Key_Space
    assert filt.active is False
    assert filt.filter_event(surface, _key_event(space), focus_widget=surface) is False
    assert controller.history == []
    active[0] = True
    assert filt.active is True
    assert filt.filter_event(surface, _key_event(space), focus_widget=surface) is True
    assert controller.history == [Command(CurationAction.ACCEPT)]
    # Without a predicate the keymap is always active, as before #342.
    assert CurationEventFilter(CurationController()).active is True


# --- the cheat-sheet lists the keys that act in the current section ---------


def test_the_cheatsheet_lists_the_live_keymap_in_curate(shell) -> None:
    from tether.gui.shell import DEFERRED_ACTIONS

    overlay = shell.show_cheatsheet()
    assert overlay.live is True
    assert overlay.rows == shell.keymap.cheatsheet(unavailable=DEFERRED_ACTIONS)
    assert _cheatsheet_cells(overlay) == overlay.rows
    assert overlay.rows  # every binding of the curation keymap
    assert overlay.inactive_label.isHidden()


@pytest.mark.parametrize("section_id", _KEYLESS_SECTIONS)
def test_the_cheatsheet_lists_no_key_where_none_acts(shell, section_id) -> None:
    shell.set_section(section_id)
    overlay = shell.show_cheatsheet()
    assert overlay.live is False
    assert overlay.rows == []
    assert _cheatsheet_cells(overlay) == []
    assert not overlay.inactive_label.isHidden()
    assert "Curate and correct" in overlay.inactive_label.text()


def test_an_open_cheatsheet_follows_the_section(shell) -> None:
    from pyqtgraph.Qt import QtCore

    from tether.gui.curation import Command, CurationAction
    from tether.gui.shell import DEFERRED_ACTIONS

    overlay = shell.show_cheatsheet()
    assert overlay.rows
    shell.set_section("analyze")
    assert _cheatsheet_cells(overlay) == []
    # A rebinding made meanwhile shows once the curation keys act again: the rows are
    # read from the live keymap, not from a copy taken when the sheet was built.
    shell.keymap.rebind(Command(CurationAction.ACCEPT), int(QtCore.Qt.Key.Key_A))
    shell.set_section("curate")
    assert _cheatsheet_cells(overlay) == shell.keymap.cheatsheet(unavailable=DEFERRED_ACTIONS)
    assert dict(_cheatsheet_cells(overlay)).get("A") == "Accept trace"


def test_the_help_shortcuts_item_outside_curate_shows_no_key(shell) -> None:
    shell.set_section("prepare")
    shell._act_shortcuts.trigger()
    assert _cheatsheet_cells(shell.cheatsheet) == []
    assert not shell.cheatsheet.inactive_label.isHidden()
