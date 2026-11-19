# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""File -> Save as recipe writes the open project's actions as a recipe (#899), headless."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

pytest.importorskip("h5py")

_HAS_QT = all(importlib.util.find_spec(m) is not None for m in ("pyqtgraph", "PySide6"))
pytestmark = [
    pytest.mark.gui,
    pytest.mark.skipif(not _HAS_QT, reason="pyqtgraph/PySide6 not installed"),
]

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_extract_cli import _make_movie  # noqa: E402, PLC2701 - the synthetic movie


@pytest.fixture
def shell(qapp, qtbot, tmp_path):
    from tether.gui.shell import TetherShell

    shell = TetherShell(preferences_file=tmp_path / "user" / "preferences.json")
    qtbot.addWidget(shell.window)
    yield shell
    shell.close()


def _action(shell, text: str):
    return next(a for a in shell.file_menu.actions() if a.text() == text)


def test_save_as_recipe_writes_a_recipe_that_tether_run_runs_on_a_new_copy(shell, tmp_path):
    from tether.cli import main
    from tether.project.jobs import read_jobs

    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    _make_movie(first, name="m.tif")
    recipe = {
        "format": "tether-recipe",
        "version": 1,
        "steps": [
            {"command": "extract", "version": 1, "fields": {"movie": "m.tif", "output": "m.tether"}}
        ],
    }
    (first / "recipe.json").write_text(json.dumps(recipe), encoding="utf-8")
    assert main(["run", str(first / "recipe.json")]) == 0

    action = _action(shell, "Save as &recipe…")
    assert not action.isEnabled()
    assert shell.load_project(first / "m.tether") is not None
    assert action.isEnabled()
    saved = shell.save_recipe(first / "saved.json")
    assert saved == first / "saved.json"
    assert "Saved the recipe saved.json (1 step(s))" in shell.window.statusBar().currentMessage()
    step = json.loads(saved.read_text(encoding="utf-8"))["steps"][0]
    assert step["command"] == "extract"
    assert (step["fields"]["movie"], step["fields"]["output"]) == ("m.tif", "m.tether")

    shutil.copy(first / "m.tif", second / "m.tif")
    shutil.copy(saved, second / "saved.json")
    assert main(["run", str(second / "saved.json")]) == 0
    assert [j.command for j in read_jobs(second / "m.tether")] == ["extract"]

    shell.close_project()
    assert not action.isEnabled()


def test_save_as_recipe_says_so_when_the_project_has_no_completed_job(shell, tmp_path):
    from tether.cli import main

    # `tether extract` itself stores no job: only a recipe run (and, later, a job of the
    # window) does, so there is nothing to save.
    _make_movie(tmp_path, name="m.tif")
    assert main(["extract", str(tmp_path / "m.tif"), "-o", str(tmp_path / "m.tether")]) == 0
    assert shell.load_project(tmp_path / "m.tether") is not None
    assert shell.save_recipe(tmp_path / "saved.json") is None
    assert "no completed job" in shell.window.statusBar().currentMessage()
    assert not (tmp_path / "saved.json").exists()
