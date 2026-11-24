# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Four sections, a guided mode and direct navigation in the shell (#897).

The acceptance criteria of #897, one test or more each: the registry lists Prepare, Curate
and correct, Analyze and Deposit in that order; the guided mode shows the next step that
``tether status`` gives for the same project (a new project, a stale fit and an import of
traces); a project made from imported traces starts in Curate and correct; each visible
section opens at each time with the mouse and with the keyboard; and one registry flag keeps
Deposit hidden. The Qt-free core tests run without a display; the rest are headless
``pytest-qt``. The stores come from ``tests/_analysis_store.py``; nothing here is a
scientific oracle.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("h5py")

from tether.gui.sections import (  # noqa: E402
    GuidedStep,
    SectionRegistry,
    SectionSpec,
    entry_section,
    guided_step,
)

_HAS_QT = all(importlib.util.find_spec(m) is not None for m in ("pyqtgraph", "PySide6"))
needs_qt = pytest.mark.skipif(not _HAS_QT, reason="pyqtgraph/PySide6 not installed")

_FIXTURES = Path(__file__).parent / "fixtures"
_ORDER = ("prepare", "curate", "analyze", "deposit")
_VISIBLE = ("prepare", "curate", "analyze")


# --- the Qt-free core ------------------------------------------------------------------------


def _spec(section_id: str, order: int, *, visible: bool = True) -> SectionSpec:
    return SectionSpec(
        id=section_id,
        title=section_id.title(),
        order=order,
        central=lambda _host: None,
        writes_project=False,
        visible=visible,
    )


def _readiness(section: str | None, action: str = "do it", sources: str | None = None):
    stages = (
        ()
        if sources is None
        else (
            SimpleNamespace(key="sources", state=SimpleNamespace(value=sources)),
            SimpleNamespace(key="traces", state=SimpleNamespace(value="ready")),
        )
    )
    return SimpleNamespace(next_step=SimpleNamespace(section=section, action=action), stages=stages)


def test_one_flag_hides_a_section_and_keeps_it_in_the_registry() -> None:
    registry = SectionRegistry([_spec("prepare", 1), _spec("deposit", 2, visible=False)])
    assert registry.ids == ("prepare", "deposit")
    assert registry.visible_ids == ("prepare",)
    with pytest.raises(ValueError, match="visible must be a bool"):
        SectionSpec(id="x", title="X", order=1, central=len, writes_project=False, visible=1)  # type: ignore[arg-type]


def test_the_guided_step_names_the_visible_section_of_the_next_step() -> None:
    registry = SectionRegistry([_spec("curate", 1), _spec("deposit", 2, visible=False)])
    assert guided_step(None, registry) is None
    assert guided_step(_readiness("curate", "curate"), registry) == GuidedStep(
        "curate", "curate", "Curate"
    )
    # A step in a hidden section, or with no section, has nothing to open.
    assert guided_step(_readiness("deposit"), registry) == GuidedStep("do it", None, None)
    assert guided_step(_readiness(None), registry) == GuidedStep("do it", None, None)


def test_only_a_project_of_imported_traces_enters_at_curation() -> None:
    assert entry_section(None) is None
    assert entry_section(_readiness("curate", sources="not_needed")) == "curate"
    assert entry_section(_readiness("prepare", sources="not_started")) is None
    assert entry_section(_readiness("prepare", sources="ready")) is None


# --- the shell -------------------------------------------------------------------------------


@pytest.fixture
def shell(section_shell):
    return section_shell(project=False)


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
    return project.path


def _imported(tmp_path: Path) -> Path:
    from tether.idealize import read_smd
    from tether.project.analysis_import import import_analysis_only_project

    out = tmp_path / "ao.tether"
    smd = read_smd(_FIXTURES / "smd_4mol.hdf5")
    import_analysis_only_project(out, source=smd, source_name="smd_4mol.hdf5")
    return out


def _status_next_step(path: Path, capsys) -> dict:
    from tether.cli import main

    capsys.readouterr()
    assert main(["status", "--json", str(path)]) == 0
    return json.loads(capsys.readouterr().out)["next_step"]


@pytest.mark.gui
@needs_qt
@pytest.mark.parametrize(
    ("make", "section"),
    [(_new_project, "prepare"), (_stale_fit, "analyze"), (_imported, "curate")],
    ids=["new-project", "stale-fit", "imported-traces"],
)
def test_the_guided_mode_shows_the_next_step_of_tether_status(
    shell, tmp_path, capsys, make, section
) -> None:
    path = make(tmp_path)
    expected = _status_next_step(path, capsys)
    assert expected["section"] == section
    assert shell.load_project(path) is not None
    assert shell.next_step_text == f"Next step: {expected['action']}"
    action = shell.next_step_action
    assert action.isEnabled()
    assert action.text() == f"Go to {shell.sections.get(section).title}"
    other = next(s for s in _VISIBLE if s != section)
    shell.set_section(other)
    action.trigger()
    assert shell.section == section
    assert shell.section_stack.currentWidget() is shell.section_page(section)
    assert shell.section_actions[section].isChecked()


@pytest.mark.gui
@needs_qt
def test_the_registry_lists_four_sections_in_the_order_of_page_1(shell) -> None:
    assert shell.sections.ids == _ORDER
    assert [s.title for s in shell.sections] == [
        "Prepare",
        "Curate and correct",
        "Analyze",
        "Deposit",
    ]
    assert [s.order for s in shell.sections] == sorted(s.order for s in shell.sections)


@pytest.mark.gui
@needs_qt
def test_deposit_is_hidden_until_its_content_exists(shell) -> None:
    assert shell.sections.get("deposit").visible is False
    assert "deposit" not in shell.section_actions
    assert shell.section_stack.count() == len(_VISIBLE)
    titles = [a.text() for a in shell.section_bar.actions()]
    assert "Deposit" not in titles
    with pytest.raises(KeyError, match="not shown"):
        shell.set_section("deposit")


@pytest.mark.gui
@needs_qt
def test_the_one_flag_shows_deposit(section_shell, monkeypatch) -> None:
    from tether.gui import shell as shell_mod

    monkeypatch.setattr(shell_mod, "DEPOSIT_VISIBLE", True)
    shell = section_shell(project=False)
    assert shell.sections.visible_ids == _ORDER
    shell.section_actions["deposit"].trigger()
    assert shell.section == "deposit"


@pytest.mark.gui
@needs_qt
def test_an_imported_project_starts_in_curate_and_correct(shell, tmp_path) -> None:
    shell.set_section("prepare")
    assert shell.load_project(_imported(tmp_path)) is not None
    assert shell.section == "curate"
    # A project with a movie keeps the section that shows.
    shell.set_section("prepare")
    (tmp_path / "fit").mkdir()
    assert shell.load_project(_stale_fit(tmp_path / "fit")) is not None
    assert shell.section == "prepare"


@pytest.mark.gui
@needs_qt
def test_with_no_project_the_guided_mode_has_nothing_to_open(shell, tmp_path) -> None:
    assert not shell.next_step_action.isEnabled()
    assert shell.go_to_next_step() is None
    assert shell.load_project(_new_project(tmp_path)) is not None
    assert shell.next_step_action.isEnabled()
    assert shell.close_project()
    assert not shell.next_step_action.isEnabled()
    assert shell.next_step_text == "Next step: open or import a project"


@pytest.mark.gui
@needs_qt
def test_each_section_opens_from_each_section_with_the_mouse(shell, qtbot) -> None:
    from pyqtgraph.Qt import QtCore

    shell.show()
    qtbot.waitExposed(shell.window)
    for start in _VISIBLE:
        for target in _VISIBLE:
            shell.set_section(start)
            button = shell.section_bar.widgetForAction(shell.section_actions[target])
            assert button is not None and button.isVisible()
            qtbot.mouseClick(button, QtCore.Qt.MouseButton.LeftButton)
            assert shell.section == target
            assert shell.section_stack.currentWidget() is shell.section_page(target)


@pytest.mark.gui
@needs_qt
def test_each_section_opens_from_each_section_with_the_keyboard(section_shell, qtbot) -> None:
    from test_shell_basics import _activate

    # A project is open, so in Curate and correct the curation keys act: the section keys
    # still reach the window there, with focus in the molecule list.
    shell = section_shell()
    _activate(shell, qtbot)
    for number, target in enumerate(_VISIBLE, start=1):
        assert shell.section_actions[target].shortcut().toString() == f"Ctrl+{number}"
    for start in _VISIBLE:
        for target in _VISIBLE:
            shell.set_section(start)
            focus = shell.molecule_list if start == "curate" else shell.window
            focus.setFocus()
            combo = shell.section_actions[target].shortcut()[0]
            qtbot.keyClick(focus, combo.key(), combo.keyboardModifiers())
            assert shell.section == target, (start, target)
    assert shell.curation_project is not None  # a switch changes no write state (ADR-0055)


@pytest.mark.gui
@needs_qt
def test_the_next_step_follows_a_curation_decision(shell, tmp_path, qtbot) -> None:
    # Codex review of #1330: the bar must not keep a step the last write finished.
    from tether.gui.curation import Command, CurationAction
    from tether.project import readiness as rd

    path = _imported(tmp_path)
    assert shell.load_project(path) is not None
    before = rd.read_project_readiness(path).next_step
    assert before.stage == "curation"
    assert shell.next_step_text == f"Next step: {before.action}"
    for row in range(shell.molecule_list.count()):
        shell.molecule_list.setCurrentRow(row)
        shell.controller.dispatch(Command(CurationAction.ACCEPT))
    after = rd.read_project_readiness(path).next_step
    assert after.stage != "curation"
    qtbot.waitUntil(lambda: shell.next_step_text == f"Next step: {after.action}", timeout=3000)


def _accept_all(shell) -> None:
    from tether.gui.curation import Command, CurationAction

    for row in range(shell.molecule_list.count()):
        shell.molecule_list.setCurrentRow(row)
        shell.controller.dispatch(Command(CurationAction.ACCEPT))


@pytest.mark.gui
@needs_qt
def test_the_read_after_a_write_runs_off_the_main_thread(shell, tmp_path, qtbot, monkeypatch):
    # A local CodeRabbit check of #1330: the stale check reads every trace, so the read
    # after a write must not stall the window.
    import threading

    from tether.services import status

    threads: list[str] = []
    real = status.project_status
    monkeypatch.setattr(
        status,
        "project_status",
        lambda p: threads.append(threading.current_thread().name) or real(p),
    )
    assert shell.load_project(_imported(tmp_path)) is not None
    assert threads == ["MainThread"]  # the open reads at once, to choose the first section
    _accept_all(shell)
    qtbot.waitUntil(lambda: len(threads) > 1, timeout=3000)
    assert all(name.startswith("tether-readiness") for name in threads[1:])


@pytest.mark.gui
@needs_qt
def test_a_read_for_a_closed_project_is_dropped(shell, tmp_path, qtbot, monkeypatch):
    import threading

    from tether.services import status

    started, release = threading.Event(), threading.Event()
    real = status.project_status

    def slow(path):
        if threading.current_thread() is not threading.main_thread():
            started.set()
            release.wait(5)
        return real(path)

    monkeypatch.setattr(status, "project_status", slow)
    assert shell.load_project(_imported(tmp_path)) is not None
    _accept_all(shell)
    qtbot.waitUntil(started.is_set, timeout=3000)
    assert shell.close_project()
    release.set()
    qtbot.wait(200)
    assert shell.readiness is None
    assert shell.next_step_text == "Next step: open or import a project"
