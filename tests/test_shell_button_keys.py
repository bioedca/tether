# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""A focused push button keeps Space and Enter (#832).

The application-wide :class:`~tether.gui.curation.CurationEventFilter` consumes the
bare curation keys on every non-text surface of the shell window (PRD §7.3). Before
#832 that included a focused ``QPushButton``: Space dispatched ACCEPT and Enter
dispatched JUMP, so the start-page buttons and **Un-reject selected** never
activated from the keyboard. These tests press the keys with real key events (not
``.click()``) and check that the button activates and that no curation command is
dispatched, and that the molecule list, the movie switcher and the trace dock
still curate on the same keys.

All ``@pytest.mark.gui`` and headless, on a real :class:`TetherShell`.
"""

from __future__ import annotations

import importlib.util

import numpy as np
import pytest

pytest.importorskip("h5py")

_HAS_QT = all(importlib.util.find_spec(m) is not None for m in ("pyqtgraph", "PySide6"))
_needs_qt = pytest.mark.skipif(not _HAS_QT, reason="pyqtgraph/PySide6 not installed")

pytestmark = [pytest.mark.gui, _needs_qt]

_KEYS = ("Key_Space", "Key_Return", "Key_Enter")


@pytest.fixture
def shell(qapp, qtbot):
    from tether.gui.shell import TetherShell

    s = TetherShell()
    qtbot.addWidget(s.window)
    yield s
    s.close()


def _store(tmp_path, *, n=3, t=12):
    from _analysis_store import build_store_with_channels

    rng = np.random.default_rng(1)
    donor = rng.uniform(400.0, 800.0, size=(n, t))
    acceptor = rng.uniform(200.0, 600.0, size=(n, t))
    project, keys = build_store_with_channels(tmp_path, donor, acceptor, name="keys.tether")
    return project, keys


def _key(name):
    from pyqtgraph.Qt import QtCore

    return getattr(QtCore.Qt.Key, name)


def _press(qtbot, widget, name):
    """Give ``widget`` focus and press + release ``name`` with real key events."""
    widget.setFocus()
    qtbot.keyClick(widget, _key(name))


@pytest.mark.parametrize("key", _KEYS)
@pytest.mark.parametrize("button", ["open_button", "tutorial_button"])
def test_a_focused_start_page_button_activates_on_its_key(
    shell, qtbot, monkeypatch, button, key
) -> None:
    from tether.gui.curation import CurationController

    pressed: list[str] = []
    widget = getattr(shell.start_page, button)
    widget.clicked.connect(lambda: pressed.append(button))
    # The real slots open a file dialog or a browser; neither may leave the test.
    shell._open_project_dialog = lambda: None
    monkeypatch.setattr("tether.gui.about.open_external", lambda url: True)
    dispatched: list[object] = []
    monkeypatch.setattr(
        CurationController, "dispatch", lambda self, command: dispatched.append(command)
    )
    assert shell.start_page_visible

    _press(qtbot, widget, key)

    assert pressed == [button]
    assert dispatched == []


@pytest.mark.parametrize("key", _KEYS)
def test_a_focused_unreject_button_unrejects_instead_of_curating(
    shell, qtbot, tmp_path, key
) -> None:
    from tether.gui.curation import Command, CurationAction
    from tether.project.labels import read_labels

    project, keys = _store(tmp_path)
    assert shell.load_project(project.path) is not None
    shell.molecule_list.setCurrentRow(1)
    _press(qtbot, shell.molecule_list, "Key_Backspace")
    assert project.curation_label(keys[1]) == -1
    assert shell.unreject_button.isEnabled()

    _press(qtbot, shell.unreject_button, key)

    # The button's own action ran: the reversal is persisted, and the last command
    # is UNREJECT (from the button), not ACCEPT (Space) or JUMP (Enter).
    assert shell.controller.last == Command(CurationAction.UNREJECT)
    assert project.curation_label(keys[1]) == 0
    assert [int(v) for v in read_labels(project.path)["label_value"]] == [-1, 0]
    assert "Un-rejected" in shell.status_message


@pytest.mark.parametrize(
    ("key", "action"),
    [
        ("Key_Space", "ACCEPT"),
        ("Key_Backspace", "REJECT"),
        ("Key_Delete", "REJECT"),
        ("Key_Return", "JUMP"),
        ("Key_Enter", "JUMP"),
    ],
)
@pytest.mark.parametrize("surface", ["molecule_list", "movie_switcher", "trace_dock"])
def test_the_curation_surfaces_still_curate_on_the_same_keys(
    shell, qtbot, tmp_path, monkeypatch, surface, key, action
) -> None:
    from tether.gui.curation import CurationAction, CurationController

    project, _keys = _store(tmp_path)
    assert shell.load_project(project.path) is not None
    shell.molecule_list.setCurrentRow(1)
    dispatched: list[object] = []
    monkeypatch.setattr(
        CurationController, "dispatch", lambda self, command: dispatched.append(command)
    )
    widget = shell.trace_dock.widget if surface == "trace_dock" else getattr(shell, surface)

    _press(qtbot, widget, key)

    assert [c.action for c in dispatched] == [CurationAction[action]]


def test_other_curation_keys_still_curate_with_a_button_focused(
    shell, qtbot, tmp_path, monkeypatch
) -> None:
    """Only the keys a push button owns natively are left to it."""
    from tether.gui.curation import CurationAction, CurationController

    project, _keys = _store(tmp_path)
    assert shell.load_project(project.path) is not None
    shell.molecule_list.setCurrentRow(1)
    dispatched: list[object] = []
    monkeypatch.setattr(
        CurationController, "dispatch", lambda self, command: dispatched.append(command)
    )

    _press(qtbot, shell.unreject_button, "Key_Right")

    assert [c.action for c in dispatched] == [CurationAction.NEXT]


@pytest.mark.parametrize("key", ["Key_Return", "Key_Enter"])
def test_an_auto_default_button_is_clicked_once_on_enter(shell, qtbot, monkeypatch, key) -> None:
    """Qt clicks an auto-default button itself; the filter must not click it again."""
    from tether.gui.curation import CurationController

    pressed: list[bool] = []
    widget = shell.start_page.open_button
    widget.setAutoDefault(True)
    widget.clicked.connect(lambda: pressed.append(True))
    shell._open_project_dialog = lambda: None
    dispatched: list[object] = []
    monkeypatch.setattr(
        CurationController, "dispatch", lambda self, command: dispatched.append(command)
    )

    _press(qtbot, widget, key)

    assert pressed == [True]
    assert dispatched == []
