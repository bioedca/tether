# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""After a restart, the last project opens in the section of its next step (#1159).

Each acceptance criterion of #1159 is a test here. A "launch" is
[`create_shell`][tether.gui.app.create_shell] with no project on the command line, over the
preferences file that ``tests/conftest.py`` points at a temporary folder for each test. The
section a launch opens is checked against the next step that ``tether status`` prints for the
same project. The stores come from ``tests/_analysis_store.py``; nothing here is a scientific
oracle.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("h5py")

from tether.gui import preferences  # noqa: E402

_HAS_QT = all(importlib.util.find_spec(m) is not None for m in ("pyqtgraph", "PySide6"))
needs_qt = pytest.mark.skipif(not _HAS_QT, reason="pyqtgraph/PySide6 not installed")


# --- the preference, Qt-free --------------------------------------------------------------


def test_the_preference_is_on_until_it_is_turned_off(tmp_path) -> None:
    prefs = tmp_path / "preferences.json"
    assert preferences.reopen_last_project(prefs) is True
    preferences.set_reopen_last_project(False, prefs)
    assert preferences.reopen_last_project(prefs) is False
    preferences.set_reopen_last_project(True, prefs)
    assert preferences.reopen_last_project(prefs) is True


def test_a_value_that_is_not_a_boolean_reads_as_on(tmp_path) -> None:
    prefs = tmp_path / "preferences.json"
    prefs.write_text(json.dumps({"schema": 1, "reopen_last_project": "no"}), encoding="utf-8")
    assert preferences.reopen_last_project(prefs) is True


# --- the launch ---------------------------------------------------------------------------


@pytest.fixture
def launch(qtbot):
    """Start the application with ``argv``; every shell started is closed at the end."""
    from tether.gui.app import create_shell

    shells = []

    def _launch(argv: list[str] | None = None):
        _app, shell = create_shell(argv or [], splash=False, file_open_events=False)
        qtbot.addWidget(shell.window)
        shells.append(shell)
        return shell

    yield _launch
    for shell in shells:
        shell.close()


def _new_project(tmp_path: Path) -> Path:
    from tether.project import Project

    path = tmp_path / "new.tether"
    Project.create(path)
    return path


def _stale_fit(tmp_path: Path) -> Path:
    from _analysis_store import MEANS, build_store_with_model

    states = np.array([[0, 0, 1, 1, 2], [1, 1, 2, 2, 0], [0, 1, 1, 0, 0], [2, 2, 2, 1, 1]])
    project, _keys = build_store_with_model(
        tmp_path, states, MEANS, stale=[False, False, False, True]
    )
    return Path(project.path)


def _status_section(path: Path, capsys) -> str:
    from tether.cli import main

    capsys.readouterr()
    assert main(["status", "--json", str(path)]) == 0
    return json.loads(capsys.readouterr().out)["next_step"]["section"]


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.gui
@needs_qt
@pytest.mark.parametrize(
    ("make", "section"),
    [(_new_project, "prepare"), (_stale_fit, "analyze")],
    ids=["new-project", "stale-fit"],
)
def test_a_restart_opens_the_last_project_in_the_section_of_its_next_step(
    launch, tmp_path, capsys, make, section
) -> None:
    path = make(tmp_path)
    assert _status_section(path, capsys) == section
    first = launch([str(path)])
    # The section that showed at close is not what the next launch opens.
    other = next(s for s in ("prepare", "curate", "analyze") if s != section)
    first.set_section(other)
    first.close()

    second = launch()
    assert second.project_path == path
    assert second.section == section
    assert second.section_stack.currentWidget() is second.section_page(section)
    assert not second.start_page_visible


@pytest.mark.gui
@needs_qt
def test_the_section_comes_from_the_readiness_and_not_the_preferences_file(
    launch, tmp_path
) -> None:
    path = _stale_fit(tmp_path)
    launch([str(path)]).close()
    data = json.loads(preferences.preferences_path().read_text(encoding="utf-8"))
    # No key of the file names a section; the next launch reads it from the project.
    assert set(data) <= {"schema", "recent_projects", "resume", "layout", "reopen_last_project"}
    assert launch().section == "analyze"


@pytest.mark.gui
@needs_qt
def test_with_the_preference_off_a_restart_shows_the_start_page(launch, tmp_path) -> None:
    path = _stale_fit(tmp_path)
    first = launch([str(path)])
    action = first.reopen_last_action
    assert action.isCheckable() and action.isEnabled() and action.isChecked()
    action.trigger()
    assert not action.isChecked()
    assert first.status_message == "The next start shows the start page"
    first.close()
    assert preferences.reopen_last_project(preferences.preferences_path()) is False

    second = launch()
    assert second.project_path is None
    assert second.molecule_list.count() == 0
    assert second.start_page_visible
    assert not second.reopen_last_action.isChecked()


@pytest.mark.gui
@needs_qt
def test_a_project_on_the_command_line_wins_over_the_last_one(launch, tmp_path) -> None:
    last = _stale_fit(tmp_path)
    named = _new_project(tmp_path)
    launch([str(last)]).close()
    assert launch([str(named)]).project_path == named


@pytest.mark.gui
@needs_qt
def test_a_missing_last_project_shows_the_start_page_with_a_message(launch, tmp_path) -> None:
    gone = tmp_path / "gone.tether"
    preferences.add_recent_project(gone, preferences.preferences_path())
    shell = launch()
    assert shell.project_path is None
    assert shell.start_page_visible
    assert shell.status_message == f"Last project not reopened: {gone} was not found"


@pytest.mark.gui
@needs_qt
def test_a_last_project_that_cannot_open_shows_the_start_page_with_a_message(
    launch, tmp_path
) -> None:
    broken = tmp_path / "broken.tether"
    broken.write_bytes(b"not an HDF5 file")
    preferences.add_recent_project(broken, preferences.preferences_path())
    shell = launch()
    assert shell.project_path is None
    assert shell.start_page_visible
    assert shell.status_message.startswith("Open project failed")


@pytest.mark.gui
@needs_qt
def test_a_readiness_failure_shows_the_start_page_with_a_message(
    launch, tmp_path, monkeypatch
) -> None:
    from tether.services import status

    path = _stale_fit(tmp_path)
    launch([str(path)]).close()

    def _fail(_path):
        raise RuntimeError("unreadable")

    monkeypatch.setattr(status, "project_status", _fail)
    shell = launch()
    assert shell.project_path is None
    assert shell.molecule_list.count() == 0
    assert shell.start_page_visible
    assert shell.status_message == (
        f"Last project not reopened: the next step of {path.name} could not be read"
    )


@pytest.mark.gui
@needs_qt
def test_nothing_is_written_into_the_project_file(launch, tmp_path) -> None:
    path = _stale_fit(tmp_path)
    before = _digest(path)
    first = launch([str(path)])
    first.reopen_last_action.trigger()
    first.reopen_last_action.trigger()
    first.close()
    launch().close()
    assert _digest(path) == before
