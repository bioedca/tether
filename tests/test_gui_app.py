# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the ``tether-gui`` desktop entry point (PRD §7.8, §9 M9).

The startup path an installed user's shortcut invokes. :func:`create_shell` is split
out of :func:`main` precisely so it can be asserted here without blocking on
``app.exec()``.

Two contracts matter beyond "it starts":

* the entry point opens a **real** store, never the synthetic-trace live-smoke helper
  ``tether.gui.shell.launch`` — wiring a shortcut to that would ship a demo; and
* a bad path on the command line leaves a usable empty shell rather than killing the
  application before the curator can use ``File -> Open project...``.

The parser tests carry no ``gui`` marker and no Qt import: ``--version``/``--help``
must answer without PySide6, which is what keeps the entry point cheap to probe from
an installer smoke test.
"""

from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path

import pytest

_HAS_QT = all(importlib.util.find_spec(m) is not None for m in ("pyqtgraph", "PySide6"))
_needs_qt = pytest.mark.skipif(not _HAS_QT, reason="pyqtgraph/PySide6 not installed")

_REPO = Path(__file__).resolve().parents[1]


# --- parser surface (no Qt) ---------------------------------------------------


def test_version_exits_zero_without_qt() -> None:
    """``--version`` answers from argparse without importing PySide6.

    The installer smoke test probes the launcher this way, so it must not depend on
    the GUI stack being importable in that context.
    """
    from tether.gui.app import build_parser

    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["--version"])
    assert exc.value.code == 0


def test_project_argument_is_optional() -> None:
    """Starting with no argument is legal — the shell opens empty."""
    from tether.gui.app import build_parser

    assert build_parser().parse_args([]).project is None
    assert build_parser().parse_args(["x.tether"]).project == "x.tether"


# --- packaging contract -------------------------------------------------------


def test_gui_entry_point_is_declared_and_not_the_smoke_helper() -> None:
    """``pyproject.toml`` declares ``tether-gui`` under ``gui-scripts``.

    ``gui-scripts`` (not ``scripts``) is what gives Windows a console-less launcher, so
    a desktop shortcut does not flash a terminal window. The target must not be
    ``tether.gui.shell:launch``, which fabricates synthetic traces.
    """
    cfg = tomllib.loads((_REPO / "pyproject.toml").read_text(encoding="utf-8"))
    gui_scripts = cfg["project"].get("gui-scripts", {})
    assert gui_scripts.get("tether-gui") == "tether.gui.app:main", (
        f"expected tether-gui -> tether.gui.app:main under [project.gui-scripts]; got {gui_scripts}"
    )
    assert "launch" not in gui_scripts.get("tether-gui", ""), (
        "the GUI entry point must not be the synthetic-trace live-smoke helper"
    )


# --- startup path (Qt) --------------------------------------------------------


def _assert_no_project_is_open(shell) -> None:
    """The shell holds no project: no molecules, and the project menus say so."""
    assert shell.molecule_list.count() == 0
    assert shell.show_histogram() is None  # no histogram seam without a project
    assert "load a project" in shell.status_message
    shell.conditions_menu.actions()[0].trigger()
    assert shell.status_message == "Conditions: load a project first"


@pytest.mark.gui
@_needs_qt
def test_starts_empty_with_no_project(qtbot) -> None:
    """No argument builds a shell with no molecules and no project-backed seams."""
    from tether.gui.app import create_shell

    _app, shell = create_shell([])
    qtbot.addWidget(shell.window)
    _assert_no_project_is_open(shell)
    shell.close()


@pytest.mark.gui
@_needs_qt
def test_bad_project_path_leaves_a_usable_shell(qtbot, tmp_path) -> None:
    """A nonexistent ``.tether`` reports on the status bar and does not raise.

    Starting the application must not be fatal because the path was wrong — the
    curator still needs the window in order to reach ``File -> Open project...``.
    """
    from tether.gui.app import create_shell

    missing = tmp_path / "nope.tether"
    _app, shell = create_shell([str(missing)])
    qtbot.addWidget(shell.window)
    # The shell tried the path it was given, and says which one failed.
    assert "Open project failed" in shell.status_message
    assert str(missing) in shell.status_message
    _assert_no_project_is_open(shell)
    shell.close()


@pytest.mark.gui
@_needs_qt
def test_valid_project_argument_opens_the_store(qtbot, tmp_path) -> None:
    """A real ``.tether`` on the command line is opened: its molecules are listed."""
    import numpy as np

    from _analysis_store import build_store_with_channels
    from tether.gui.app import create_shell

    rng = np.random.default_rng(746)
    donor = rng.uniform(400.0, 800.0, size=(3, 12))
    acceptor = rng.uniform(200.0, 600.0, size=(3, 12))
    project, _keys = build_store_with_channels(tmp_path, donor, acceptor, name="start.tether")

    _app, shell = create_shell([str(project.path)])
    qtbot.addWidget(shell.window)
    assert shell.molecule_list.count() == 3
    assert "3 molecule(s)" in shell.status_message
    shell.close()


def test_main_puts_the_env_folders_on_path_before_anything_loads(monkeypatch) -> None:
    """``main`` takes the PATH step first, then builds the shell (issue #579, ADR-0076).

    The Start Menu shortcut runs ``tether-gui.exe`` directly, with no ``.bat`` to put the
    env's DLL folders first on PATH (issue #553). ``main`` does it, and it must do it
    before :func:`create_shell` imports PySide6 and the shell imports NumPy.
    """
    from tether.gui import app as gui_app

    calls: list[str] = []

    class _App:
        def exec(self) -> int:
            calls.append("exec")
            return 3

    class _Shell:
        def show(self) -> None:
            calls.append("show")

    def _create_shell(argv):
        calls.append(f"create_shell {argv}")
        return _App(), _Shell()

    monkeypatch.setattr(gui_app, "put_env_folders_first", lambda: calls.append("path") or True)
    monkeypatch.setattr(gui_app, "create_shell", _create_shell)
    assert gui_app.main(["x.tether"]) == 3
    assert calls == ["path", "create_shell ['x.tether']", "show", "exec"]
