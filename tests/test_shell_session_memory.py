# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The desktop application remembers the session on this computer (#628, ADR-0122).

These ``@pytest.mark.gui`` tests run headless on the pytest-qt ``QApplication``, with the
preferences file in a temporary folder. Each acceptance criterion of #628 is a test here:
File → Open recent, the molecule selected at close, the layout and **View → Reset layout**,
the curator name in new labels, and nothing written into the project file. A "next launch" is
a second shell over the same preferences file.
"""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("h5py")

_HAS_QT = all(importlib.util.find_spec(m) is not None for m in ("pyqtgraph", "PySide6"))
pytestmark = [
    pytest.mark.gui,
    pytest.mark.skipif(not _HAS_QT, reason="pyqtgraph/PySide6 not installed"),
]


@pytest.fixture
def prefs(tmp_path: Path) -> Path:
    return tmp_path / "user" / "preferences.json"


@pytest.fixture
def launch(qapp, qtbot, prefs):
    """Start a shell over ``prefs``; every shell started is closed at the end."""
    from tether.gui.shell import TetherShell

    shells: list[TetherShell] = []

    def _launch(preferences_file: Path | None = prefs) -> TetherShell:
        shell = TetherShell(preferences_file=preferences_file)
        qtbot.addWidget(shell.window)
        shells.append(shell)
        return shell

    yield _launch
    for shell in shells:
        shell.close()


def _store(tmp_path: Path, *, n: int = 4, name: str = "session.tether") -> Path:
    from _analysis_store import build_store_with_channels

    rng = np.random.default_rng(7)
    donor = rng.uniform(400.0, 800.0, size=(n, 10))
    acceptor = rng.uniform(200.0, 600.0, size=(n, 10))
    project, _keys = build_store_with_channels(tmp_path, donor, acceptor, name=name)
    return Path(project.path)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _recent_entries(shell) -> list[tuple[str, bool, str]]:
    shell.recent_menu.aboutToShow.emit()
    return [
        (action.text(), action.isEnabled(), action.data() or "")
        for action in shell.recent_menu.actions()
    ]


# --- File -> Open recent ------------------------------------------------------------------


def test_a_project_opened_once_is_in_open_recent_on_the_next_launch(launch, tmp_path) -> None:
    path = _store(tmp_path)
    first = launch()
    assert first.load_project(path) is not None
    first.close()

    second = launch()
    entries = _recent_entries(second)
    assert entries[0] == (path.name, True, str(path))
    # Choosing it opens it.
    second.recent_menu.actions()[0].trigger()
    assert second.project_path == path
    assert second.molecule_list.count() == 4


def test_open_recent_sits_between_open_and_close_in_the_file_menu(launch) -> None:
    shell = launch()
    actions = shell.file_menu.actions()
    menus = [a.menu() for a in actions]
    index = menus.index(shell.recent_menu)
    assert actions[index - 1].text() == "&Open project…"
    assert actions[index + 1].text() == "&Close project"


def test_a_project_whose_file_is_missing_shows_disabled_with_its_path(
    launch, prefs, tmp_path
) -> None:
    from tether.gui import preferences

    gone = tmp_path / "gone.tether"
    preferences.add_recent_project(gone, prefs)
    shell = launch()
    assert _recent_entries(shell) == [(f"{gone} (missing)", False, str(gone))]


def test_an_empty_list_says_so(launch) -> None:
    shell = launch()
    assert _recent_entries(shell) == [("No recent projects", False, "")]


# --- resume ---------------------------------------------------------------------------------


def test_reopening_a_project_selects_the_molecule_selected_at_close(launch, tmp_path) -> None:
    path = _store(tmp_path)
    first = launch()
    first.load_project(path)
    first.molecule_list.setCurrentRow(2)
    first.close()

    second = launch()
    second.load_project(path)
    assert second.molecule_list.currentRow() == 2
    # Close project and open it again in the same window: the same molecule.
    second.molecule_list.setCurrentRow(3)
    assert second.close_project()
    second.load_project(path)
    assert second.molecule_list.currentRow() == 3


def test_opening_another_project_keeps_the_position_of_the_one_replaced(launch, tmp_path) -> None:
    a = _store(tmp_path, name="a.tether")
    b = _store(tmp_path, name="b.tether")
    shell = launch()
    shell.load_project(a)
    shell.molecule_list.setCurrentRow(1)
    shell.load_project(b)
    assert shell.molecule_list.currentRow() == 0
    shell.load_project(a)
    assert shell.molecule_list.currentRow() == 1


def test_a_project_never_closed_here_opens_at_its_first_molecule(launch, tmp_path) -> None:
    shell = launch()
    shell.load_project(_store(tmp_path))
    assert shell.molecule_list.currentRow() == 0


# --- layout ---------------------------------------------------------------------------------


def test_the_layout_is_restored_and_reset_layout_returns_the_default(
    launch, prefs, tmp_path
) -> None:
    from pyqtgraph.Qt import QtCore

    from tether.gui import preferences

    left = QtCore.Qt.DockWidgetArea.LeftDockWidgetArea
    right = QtCore.Qt.DockWidgetArea.RightDockWidgetArea

    first = launch()
    default_size = first.window.size()
    assert first.window.dockWidgetArea(first.browser_dock) == left
    first.window.addDockWidget(right, first.browser_dock)
    first.window.resize(default_size.width() + 120, default_size.height() + 80)
    wanted = first.window.size()
    first.close()

    second = launch()
    assert second.window.dockWidgetArea(second.browser_dock) == right
    assert second.window.size() == wanted
    assert not second.browser_dock.isHidden()

    second.reset_layout_action.trigger()
    assert second.window.dockWidgetArea(second.browser_dock) == left
    assert second.window.size() == default_size
    assert not second.browser_dock.isHidden()
    assert preferences.stored_layout(prefs) is None


def test_a_dock_hidden_only_by_its_section_shows_on_the_next_launch(launch) -> None:
    from tether.gui.shell import ANALYZE_SECTION, CURATE_SECTION

    first = launch()
    first.set_section(ANALYZE_SECTION)
    assert first.browser_dock.isHidden()
    first.close()

    second = launch()
    assert second.section == CURATE_SECTION
    assert not second.browser_dock.isHidden()


def test_a_dock_the_user_closed_stays_closed_on_the_next_launch(launch) -> None:
    from tether.gui.shell import ANALYZE_SECTION, CURATE_SECTION

    first = launch()
    first.browser_dock.close()
    first.close()

    second = launch()
    assert second.browser_dock.isHidden()
    # And the section rule still applies on top of the restored state.
    second.set_section(ANALYZE_SECTION)
    second.set_section(CURATE_SECTION)
    assert second.browser_dock.isHidden()


# --- curator name -----------------------------------------------------------------------------


def _labelers(path: Path) -> list[str]:
    from tether.project import labels as L
    from tether.project.core import Project

    rows = Project.open(path).read_labels()
    return [L._to_str(value) for value in rows["labeler"]]


def test_a_label_written_after_the_name_changes_carries_the_new_name(launch, tmp_path) -> None:
    from tether.project.labels import default_labeler

    path = _store(tmp_path)
    shell = launch()
    shell.load_project(path)
    assert shell.curator_name == default_labeler()
    assert shell.curator_label.text() == f"Curator: {default_labeler()}"

    from tether.gui.curation import Command, CurationAction

    shell.molecule_list.setCurrentRow(0)
    shell.controller.dispatch(Command(CurationAction.ACCEPT))
    assert shell.set_curator_name("Ana López")
    assert shell.curator_label.text() == "Curator: Ana López"
    shell.molecule_list.setCurrentRow(1)
    shell.controller.dispatch(Command(CurationAction.REJECT))

    assert _labelers(path)[-2:] == [default_labeler(), "Ana López"]


def test_the_curator_name_is_kept_for_the_next_launch_and_blank_returns_the_login(
    launch,
) -> None:
    from tether.project.labels import default_labeler

    first = launch()
    first.set_curator_name("Ana")
    first.close()

    second = launch()
    assert second.curator_name == "Ana"
    assert second.set_curator_name("  ")
    assert second.curator_name == default_labeler()
    assert launch().curator_name == default_labeler()


def test_a_bad_curator_name_changes_nothing_and_says_why(launch) -> None:
    shell = launch()
    before = shell.curator_name
    assert not shell.set_curator_name("a\x00b")
    assert shell.curator_name == before
    assert shell.status_message.startswith("Curator name not changed:")


def test_the_condition_dialog_writes_its_audit_entry_as_the_curator(launch, monkeypatch) -> None:
    from tether.gui import conditions as gui_conditions

    seen: dict[str, object] = {}

    class _Dialog:
        def __init__(self, project, **kwargs) -> None:
            seen.update(kwargs)

        def exec(self) -> None:
            return None

    shell = launch()
    shell.set_curator_name("Ana")
    monkeypatch.setattr(gui_conditions, "ConditionValidationDialog", _Dialog)
    shell._conditions = object()
    shell._validate_conditions_dialog()
    assert seen["labeler"] == "Ana"


# --- nothing in the project file, and nothing without a preferences file --------------------


def test_nothing_is_written_into_the_project_file(launch, tmp_path) -> None:
    path = _store(tmp_path)
    before = _digest(path)
    first = launch()
    first.load_project(path)
    first.molecule_list.setCurrentRow(3)
    first.set_curator_name("Ana")
    first.close()
    second = launch()
    second.load_project(path)
    assert second.molecule_list.currentRow() == 3
    second.reset_layout()
    second.close()
    assert _digest(path) == before


def test_a_shell_without_a_preferences_file_remembers_nothing(launch, prefs, tmp_path) -> None:
    path = _store(tmp_path)
    shell = launch(None)
    shell.load_project(path)
    shell.molecule_list.setCurrentRow(2)
    shell.set_curator_name("Ana")
    shell.close()
    assert not prefs.exists()
    assert shell.recent_projects() == []
    assert not shell.recent_menu.isEnabled()


def test_a_failed_preferences_write_is_a_status_message_not_an_error(
    launch, tmp_path, monkeypatch
) -> None:
    from tether.gui import preferences

    def boom(*_args, **_kwargs):
        raise OSError("read-only home")

    path = _store(tmp_path)
    shell = launch()
    monkeypatch.setattr(preferences, "_write", boom)
    assert shell.load_project(path) is not None
    assert shell.project_path == path
    assert shell.set_curator_name("Ana")
    assert shell.curator_name == "Ana"
    shell.close()  # the layout and position saves fail quietly too


def test_tether_gui_remembers_in_the_per_user_file(qtbot, monkeypatch, tmp_path) -> None:
    from tether.gui.app import create_shell
    from tether.gui.preferences import PREFERENCES_ENV

    monkeypatch.setenv(PREFERENCES_ENV, str(tmp_path / "p.json"))
    _app, shell = create_shell([], splash=False)
    qtbot.addWidget(shell.window)
    try:
        assert shell.preferences_file == tmp_path / "p.json"
    finally:
        shell.close()
