# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless tests for the category editor and the real category names (#633, PRD §7.6).

Each test reads the values back from a real ``.tether`` store after the change.
"""

from __future__ import annotations

import importlib.util

import numpy as np
import pytest

pytest.importorskip("h5py")

_HAS_QT = all(importlib.util.find_spec(m) is not None for m in ("pyqtgraph", "PySide6"))
_needs_qt = pytest.mark.skipif(not _HAS_QT, reason="pyqtgraph/PySide6 not installed")

pytestmark = [pytest.mark.gui, _needs_qt]

N_FRAMES = 40
#: Twelve names, so classes 10 to 12 need the overflow picker.
NAMES_A = [f"a{i}" for i in range(1, 13)]
#: The list of the second condition: different names, so a wrong list shows at once.
NAMES_B = ["bound", "free", "unclear"]


def _text(value: object) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _store(tmp_path, *, two_conditions: bool = False, name: str = "cats.tether"):
    """A real ``.tether`` with three molecules; with ``two_conditions`` the third differs.

    Condition A holds `NAMES_A`. With ``two_conditions`` molecule 2 is moved to a second
    registered condition B that holds `NAMES_B`. Returns the project, the molecule keys and
    the condition ids ``(a, b)`` (``b`` is ``None`` with one condition).
    """
    import h5py

    from _analysis_store import build_store_with_channels
    from tether.io.filename import ConditionKey
    from tether.project.conditions import register_condition

    rng = np.random.default_rng(7)
    donor = rng.normal(600.0, 15.0, size=(3, N_FRAMES))
    acceptor = rng.normal(400.0, 15.0, size=(3, N_FRAMES))
    project, keys = build_store_with_channels(tmp_path, donor, acceptor, name=name)
    project.sync_conditions()
    (cid_a,) = project.molecules_by_condition()
    project.set_category_list(cid_a, NAMES_A)
    cid_b = None
    if two_conditions:
        key_b = ConditionKey(construct_variant="Other", dye="Cy3Cy5", ligand="tRNA")
        register_condition(project.path, key_b)
        cid_b = key_b.condition_id()
        with h5py.File(project.path, "r+") as f:
            table = f["molecules"]["table"]
            row = table[2]
            row["condition_id"] = cid_b
            table[2] = row
        project.set_category_list(cid_b, NAMES_B)
    return project, keys, (cid_a, cid_b)


def _category(project, key) -> str:
    from tether.imaging.extract import read_molecules

    table = read_molecules(project.path)
    keys = [_text(k) for k in table["molecule_key"]]
    return _text(table[keys.index(key)]["category"])


def _names(path, condition_id) -> tuple[str, ...]:
    from tether.project.conditions import read_category_list

    return read_category_list(path, condition_id).categories


@pytest.fixture
def shell(qapp, qtbot):
    from tether.gui.shell import TetherShell

    s = TetherShell(advance_after_curation=False)
    qtbot.addWidget(s.window)
    yield s
    s.close()


def _loaded(shell, tmp_path, **kwargs):
    project, keys, cids = _store(tmp_path, **kwargs)
    assert shell.load_project(project.path) is not None
    shell.molecule_list.setCurrentRow(0)
    return project, keys, cids


def _press(qtbot, shell, key_name: str) -> None:
    from pyqtgraph.Qt import QtCore

    qtbot.keyClick(shell.molecule_list, getattr(QtCore.Qt.Key, key_name))


def _editor(shell, monkeypatch, *, answers=()):
    """Open the editor through the shell with the modal loop replaced; ``answers`` confirm."""
    from tether.gui import category_editor as editor_mod

    monkeypatch.setattr(editor_mod.CategoryEditor, "exec", lambda self: None)
    replies = list(answers)
    asked = []

    def confirm(title, text):
        asked.append(text)
        return replies.pop(0)

    editor = shell.show_category_editor()
    assert editor is not None
    editor._confirm = confirm
    return editor, asked


def _cheatsheet_rows(shell) -> dict[str, str]:
    shell.cheatsheet.refresh()
    return dict(shell.cheatsheet.rows)


# --- the real names ---------------------------------------------------------------


def test_no_placeholder_name_is_shown(shell, tmp_path) -> None:
    # A bare shell has no list at all; it never invents "category 1".
    assert shell._picker_categories() == []
    assert _cheatsheet_rows(shell)["1"] == "Assign category 1"

    _loaded(shell, tmp_path)

    rows = _cheatsheet_rows(shell)
    assert rows["1"] == "Assign category 1 (a1)"
    assert rows["9"] == "Assign category 9 (a9)"
    assert shell._picker_categories() == NAMES_A
    assert not any(text == "category 1" for text in shell._picker_categories())


def test_the_keys_follow_the_selected_molecules_condition(shell, qtbot, tmp_path) -> None:
    project, keys, (_cid_a, _cid_b) = _loaded(shell, tmp_path, two_conditions=True)

    shell.molecule_list.setCurrentRow(2)
    rows = _cheatsheet_rows(shell)
    assert rows["1"] == "Assign category 1 (bound)"
    assert rows["3"] == "Assign category 3 (unclear)"
    assert rows["4"] == "Assign category 4 (not in the list)"
    assert shell._picker_categories() == NAMES_B
    _press(qtbot, shell, "Key_2")
    assert _category(project, keys[2]) == "free"

    shell.molecule_list.setCurrentRow(0)
    assert _cheatsheet_rows(shell)["2"] == "Assign category 2 (a2)"
    _press(qtbot, shell, "Key_2")
    assert _category(project, keys[0]) == "a2"


def test_the_overflow_picker_lists_the_names_after_the_ninth(shell, tmp_path) -> None:
    from pyqtgraph.Qt import QtWidgets

    from tether.gui.shell import OverflowCategoryPicker

    _loaded(shell, tmp_path)
    picker = OverflowCategoryPicker(shell._picker_categories(), parent=shell.window)
    shown = picker.dialog.findChild(QtWidgets.QListWidget)
    assert [shown.item(i).text() for i in range(shown.count())] == ["10: a10", "11: a11", "12: a12"]


# --- the editor ---------------------------------------------------------------------


def test_the_editor_shows_the_selected_molecules_condition(shell, tmp_path, monkeypatch) -> None:
    _loaded(shell, tmp_path, two_conditions=True)
    shell.molecule_list.setCurrentRow(2)

    editor, _ = _editor(shell, monkeypatch)

    assert editor.names == tuple(NAMES_B)
    items = [editor.list_widget.item(i).text() for i in range(editor.list_widget.count())]
    assert items == ["1. bound", "2. free", "3. unclear"]
    assert shell.category_editor is editor


def test_editor_changes_give_the_library_functions_list(shell, tmp_path, monkeypatch) -> None:
    from tether.project import conditions as C

    project, _keys, (cid, _) = _loaded(shell, tmp_path)
    editor, _ = _editor(shell, monkeypatch)

    editor.name_field.setText("docked")
    assert editor.add().ok
    editor.select("a3")
    editor.name_field.setText("open")
    assert editor.rename().ok
    editor.select("a5")
    assert editor.remove().ok  # no molecule has a5, so nothing is asked
    editor.select("docked")
    assert editor.move(-1).ok
    editor.select("a1")
    assert editor.move(1).ok

    # The same four changes through the library functions on a second store.
    (tmp_path / "ref").mkdir()
    reference, _, (ref_cid, _) = _store(tmp_path / "ref", name="ref.tether")
    C.add_category(reference.path, ref_cid, "docked")
    C.rename_category(reference.path, ref_cid, "a3", "open")
    C.remove_category(reference.path, ref_cid, "a5")
    names = list(_names(reference.path, ref_cid))
    i = names.index("docked")
    names[i - 1], names[i] = names[i], names[i - 1]
    names[0], names[1] = names[1], names[0]
    C.set_category_list(reference.path, ref_cid, names)

    assert _names(project.path, cid) == _names(reference.path, ref_cid)
    assert editor.names == _names(project.path, cid)
    assert (
        C.read_class_lookup(project.path, cid).classes
        == C.read_class_lookup(reference.path, ref_cid).classes
    )


def test_a_new_name_is_on_the_next_number_key(shell, qtbot, tmp_path) -> None:
    project, keys, (cid, _) = _store(tmp_path)
    project.set_category_list(cid, ["one", "two"])
    assert shell.load_project(project.path) is not None
    shell.molecule_list.setCurrentRow(0)

    edit = shell.add_category("three")

    assert edit.ok and edit.names == ("one", "two", "three")
    assert shell.status_message == "Category 3 (three) added"
    assert _cheatsheet_rows(shell)["3"] == "Assign category 3 (three)"
    _press(qtbot, shell, "Key_3")
    assert _category(project, keys[0]) == "three"


def test_after_a_rename_every_molecule_keeps_its_category(
    shell, qtbot, tmp_path, monkeypatch
) -> None:
    from tether.project.molecule_edits import read_molecule_audit

    project, keys, (cid, _) = _loaded(shell, tmp_path)
    _press(qtbot, shell, "Key_2")
    shell.molecule_list.setCurrentRow(1)
    _press(qtbot, shell, "Key_2")
    shell.molecule_list.setCurrentRow(2)
    _press(qtbot, shell, "Key_4")
    editor, _ = _editor(shell, monkeypatch)

    editor.select("a2")
    editor.name_field.setText("docked")
    edit = editor.rename()

    assert edit.ok
    assert _names(project.path, cid)[1] == "docked"  # same place, so the same key
    assert [_category(project, k) for k in keys] == ["docked", "docked", "a4"]
    assert "2 molecules keep it" in editor.message
    assert "category docked" in shell.molecule_list.item(0).text()
    assert "category docked" in shell.molecule_list.item(1).text()
    audit = read_molecule_audit(project.path)
    reasons = {_text(r) for r in audit["reason"]}
    assert "category renamed from 'a2' to 'docked'" in reasons
    shell.molecule_list.setCurrentRow(0)
    assert _cheatsheet_rows(shell)["2"] == "Assign category 2 (docked)"


def test_reorder_keeps_each_molecules_category_and_moves_the_keys(
    shell, qtbot, tmp_path, monkeypatch
) -> None:
    project, keys, (cid, _) = _loaded(shell, tmp_path)
    _press(qtbot, shell, "Key_1")
    editor, _ = _editor(shell, monkeypatch)

    editor.select("a1")
    assert editor.move(1).ok
    assert editor.move(-1) is not None  # back up again
    assert editor.move(1).ok

    assert _names(project.path, cid)[:2] == ("a2", "a1")
    assert _category(project, keys[0]) == "a1"
    assert "is now category 2" in editor.message
    assert _cheatsheet_rows(shell)["1"] == "Assign category 1 (a2)"


def test_removing_a_used_category_asks_and_states_the_count(
    shell, qtbot, tmp_path, monkeypatch
) -> None:
    project, keys, (cid, _) = _loaded(shell, tmp_path)
    _press(qtbot, shell, "Key_3")
    shell.molecule_list.setCurrentRow(1)
    _press(qtbot, shell, "Key_3")
    editor, asked = _editor(shell, monkeypatch, answers=[False, True])

    editor.select("a3")
    assert editor.remove() is None  # declined: nothing written
    assert "a3" in _names(project.path, cid)
    assert asked[0].startswith("2 molecules of this condition have the category “a3”.")
    assert "keep “a3” as their category" in asked[0]
    assert "move up by one number key" in asked[0]

    edit = editor.remove()

    assert edit.ok
    assert "a3" not in _names(project.path, cid)
    # The library's rule: the molecules keep the name; it is no longer in the list.
    assert _category(project, keys[0]) == "a3"
    assert _category(project, keys[1]) == "a3"
    assert "2 molecules keep it as a name that is not in the list" in editor.message
    assert _cheatsheet_rows(shell)["3"] == "Assign category 3 (a4)"


def test_the_tmaven_class_mapping_is_unaffected(shell, tmp_path, monkeypatch) -> None:
    from tether.project.conditions import read_class_lookup

    project, _keys, (cid, _) = _loaded(shell, tmp_path)
    editor, _ = _editor(shell, monkeypatch)
    editor.select("a1")
    editor.name_field.setText("first")
    editor.rename()
    editor.select("a2")
    editor.remove()

    lookup = read_class_lookup(project.path, cid)
    assert lookup.category_of(0) == ""
    assert lookup.class_of("") == 0
    assert lookup.classes[1] == "first"
    assert lookup.classes[2] == "a3"
    assert 0 not in lookup.classes


def test_a_duplicate_name_is_refused_and_reported(shell, tmp_path, monkeypatch) -> None:
    project, _keys, (cid, _) = _loaded(shell, tmp_path)
    editor, _ = _editor(shell, monkeypatch)

    editor.name_field.setText("a4")
    assert not editor.button("add").isEnabled()
    edit = shell.add_category("a4")

    assert not edit.ok
    assert "already present" in edit.message
    assert _names(project.path, cid) == tuple(NAMES_A)


def test_without_a_writable_project_the_changes_are_unavailable(shell, tmp_path) -> None:
    edit = shell.add_category("x")
    assert not edit.ok
    assert edit.names == ()
    assert shell.show_category_editor() is None
    assert "select a molecule" in shell.status_message


def test_the_editor_keys_type_text(shell, tmp_path, monkeypatch) -> None:
    from pyqtgraph.Qt import QtCore, QtGui

    _loaded(shell, tmp_path)
    editor, _ = _editor(shell, monkeypatch)
    field = editor.name_field
    press = QtGui.QKeyEvent(
        QtCore.QEvent.Type.KeyPress, int(QtCore.Qt.Key.Key_1), QtCore.Qt.KeyboardModifier(0), "1"
    )
    before = list(shell.controller.history)

    assert shell.event_filter.filter_event(field, press, focus_widget=field) is False
    assert shell.controller.history == before


def test_the_button_and_the_menu_open_the_editor(shell, tmp_path, monkeypatch) -> None:
    from pyqtgraph.Qt import QtWidgets

    from tether.gui import category_editor as editor_mod

    opened = []
    monkeypatch.setattr(editor_mod.CategoryEditor, "exec", lambda self: opened.append(self))
    _loaded(shell, tmp_path)

    assert shell.categories_button.isEnabled()
    shell.categories_button.click()
    (menu,) = [m for m in shell.window.findChildren(QtWidgets.QMenu) if m.title() == "&Curation"]
    {a.text(): a for a in menu.actions()}["&Categories…"].trigger()

    assert len(opened) == 2
    assert all(editor.names == tuple(NAMES_A) for editor in opened)


def test_the_editor_stays_on_its_condition_when_the_selection_moves(
    shell, tmp_path, monkeypatch
) -> None:
    # Greptile P1 on #1420: an edit made after the selection moved to a molecule of
    # another condition (a rename that the filter hides, a click) must still change the
    # condition the editor was opened on.
    project, _keys, (cid_a, cid_b) = _loaded(shell, tmp_path, two_conditions=True)
    shell.molecule_list.setCurrentRow(2)
    editor, _ = _editor(shell, monkeypatch)
    shell.molecule_list.setCurrentRow(0)  # now a molecule of condition A is selected

    editor.name_field.setText("late")
    assert editor.add().ok
    editor.select("free")
    editor.name_field.setText("loose")
    assert editor.rename().ok
    editor.select("bound")
    assert editor.move(1).ok
    editor.select("unclear")
    assert editor.remove().ok

    assert _names(project.path, cid_b) == ("loose", "bound", "late")
    assert _names(project.path, cid_a) == tuple(NAMES_A)
    assert editor.names == ("loose", "bound", "late")
