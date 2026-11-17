# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The offscreen GUI harness: a shell parked on a named section over a temporary ``.tether`` (#344).

The ``section_shell`` fixture in ``tests/conftest.py`` is the way in; this module holds what it
uses, so a test module can also import the guards directly. A test asks for a shell like this::

    def test_something(section_shell):
        shell = section_shell("analyze")  # a writable temporary project, Analyze showing
        bare = section_shell(project=False)  # no project, the default section

The harness closes every shell it built when the test ends and then checks that nothing leaked:
the session lock file of each project it opened is gone, the curation event filter no longer
reaches the controller, the window's close filter is removed, and the process-local owner
registry ``_GUI_PROJECT_OWNERS`` is empty. A leak is an error in the teardown of that test.

**Pixels are never asserted.** The offscreen platform gives a canvas of 0×0 pixels, so every
check reads widget state (``isHidden``, the current page, the dock area), never a rendering.

**The GL guard lives here.** A napari ``Viewer`` needs an OpenGL context for its vispy canvas,
and the macOS offscreen platform has none: it segfaults. [`needs_gl`][] skips exactly that
combination, and [`section_marks`][] applies it to every section listed in
[`VIEWER_SECTIONS`][]. Sections without a Viewer run on all three operating systems. The movie
panel and round-trip tests import the same guard, so there is one condition, not one per module.

Like ``_analysis_store``, this is a plain helper module (no ``test_`` prefix, so pytest does not
collect it), importable as ``_gui_harness`` under the default ``prepend`` import mode. It imports
only the standard library and pytest at module level, so a module that needs only the GL guard
does not pull in Qt, h5py or the project layer; those load when a shell is built.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from tether.gui.shell import TetherShell
    from tether.project import Project

__all__ = [
    "HAS_QT",
    "NO_HEADLESS_GL",
    "SECTION_IDS",
    "VIEWER_SECTIONS",
    "SectionShells",
    "build_project",
    "needs_gl",
    "needs_qt",
    "section_marks",
    "section_params",
]

#: Whether the Qt stack of the shell is importable. ``find_spec`` only locates the packages.
HAS_QT = all(importlib.util.find_spec(m) is not None for m in ("pyqtgraph", "PySide6"))
needs_qt = pytest.mark.skipif(not HAS_QT, reason="pyqtgraph/PySide6 not installed")

# Instantiating a real napari Viewer also needs an OpenGL context for its vispy canvas. macOS CI
# provides none under ``QT_QPA_PLATFORM=offscreen`` (the cocoa GL backend segfaults), so the
# Viewer-instantiating tests are skipped on that exact combination. They still run headless on
# Linux (xvfb) and Windows, and a macOS developer with a real display is unaffected.
NO_HEADLESS_GL = sys.platform == "darwin" and os.environ.get("QT_QPA_PLATFORM") == "offscreen"
needs_gl = pytest.mark.skipif(
    NO_HEADLESS_GL,
    reason="napari Viewer needs a GL context; macOS offscreen Qt has none (segfaults)",
)

#: The id of every section the shell registers, in order. ``tests/test_gui_section_contract.py``
#: checks this against ``TetherShell().sections.ids``, so a new section cannot skip the contract.
SECTION_IDS: tuple[str, ...] = ("prepare", "curate", "analyze")

#: The sections whose page or docks build a napari ``Viewer``. None does today; the movie panel
#: of #356 adds its section here, and the contract then skips it on macOS offscreen only.
VIEWER_SECTIONS: frozenset[str] = frozenset()


def section_marks(section_id: str) -> list[pytest.MarkDecorator]:
    """The marks a test of ``section_id`` carries: [`needs_gl`][] for a Viewer section."""
    return [needs_gl] if section_id in VIEWER_SECTIONS else []


def section_params(section_ids: tuple[str, ...] = SECTION_IDS) -> list[Any]:
    """One ``pytest.param`` per section, with its id and its [`section_marks`][]."""
    return [pytest.param(s, id=s, marks=section_marks(s)) for s in section_ids]


def build_project(
    tmp_path: Path, *, name: str = "harness.tether", n: int = 3, t: int = 12
) -> Project:
    """A small extracted ``.tether`` of ``n`` molecules by ``t`` frames under ``tmp_path``."""
    import numpy as np

    from _analysis_store import build_store_with_channels

    rng = np.random.default_rng(344)
    donor = rng.uniform(400.0, 800.0, size=(n, t))
    acceptor = rng.uniform(200.0, 600.0, size=(n, t))
    project, _keys = build_store_with_channels(tmp_path, donor, acceptor, name=name)
    return project


class SectionShells:
    """Builds shells parked on a section, and closes and checks them all at the end.

    One instance serves one test; ``tests/conftest.py`` makes it the ``section_shell`` fixture.
    """

    def __init__(self, qtbot: Any, tmp_path: Path, *, lock_timeout_ms: int = 5000) -> None:
        self._qtbot = qtbot
        self._tmp_path = tmp_path
        self._lock_timeout_ms = lock_timeout_ms
        self._built: list[tuple[TetherShell, list[Path]]] = []

    def __call__(
        self,
        section: str | None = None,
        *,
        project: bool | str | os.PathLike[str] = True,
        **shell_kwargs: Any,
    ) -> TetherShell:
        """A new shell showing ``section`` (``None``: the default section).

        ``project=True`` builds a temporary ``.tether`` and opens it writable; a path opens that
        file instead; ``False`` leaves the shell bare. With a project the lazily built docks are
        built as well (the overlap dock on opening, the histogram dock by showing it), so every
        dock a section can hold is attached before the shell is parked. ``shell_kwargs`` go to
        ``TetherShell``.
        """
        from tether.gui.shell import TetherShell

        shell = TetherShell(**shell_kwargs)
        self._qtbot.addWidget(shell.window)
        opened: list[Path] = []
        self._built.append((shell, opened))
        if project is not False:
            if project is True:
                index = len(self._built)
                path = build_project(self._tmp_path, name=f"harness-{index}.tether").path
            else:
                path = Path(project)
            loaded = shell.load_project(path)
            assert loaded is not None, f"the harness could not open {path}: {shell.status_message}"
            opened.append(Path(loaded.path))
            assert shell.show_histogram() is not None, shell.status_message
        if section is not None:
            shell.set_section(section)
        return shell

    def close(self, shell: TetherShell) -> None:
        """Close ``shell`` now and check that it leaked nothing (idempotent)."""
        for built, opened in self._built:
            if built is shell:
                shell.close()
                self._check_released(built, opened)
                return
        raise ValueError("this shell was not built by the harness")

    def close_all(self) -> None:
        """Close every shell this harness built, then check each one and the owner registry."""
        from tether.gui import shell as shell_mod

        for shell, _opened in self._built:
            shell.close()
        for shell, opened in self._built:
            self._check_released(shell, opened)
        assert not shell_mod._GUI_PROJECT_OWNERS, (
            f"_GUI_PROJECT_OWNERS is not empty: {shell_mod._GUI_PROJECT_OWNERS!r}"
        )

    def _check_released(self, shell: TetherShell, opened: list[Path]) -> None:
        from pyqtgraph.Qt import QtCore, QtGui
        from pytestqt.exceptions import TimeoutError as WaitTimeout

        from tether.gui import shell as shell_mod
        from tether.project.lock import lock_path

        # The session lock: the release runs at once with no writer in flight, and a timer
        # retries a transient failure, so wait briefly rather than read it once.
        for path in opened:
            lock_file = lock_path(path)
            try:
                self._qtbot.waitUntil(
                    lambda f=lock_file: not f.exists(), timeout=self._lock_timeout_ms
                )
            except WaitTimeout:
                raise AssertionError(f"the session lock outlived close: {lock_file}") from None
        # Released: forget the paths, so a later close of this shell does not mistake the
        # lock of another shell that has since opened the same file for a leak of this one.
        opened.clear()
        assert not shell.holds_project_claim, "the shell still holds its project claim"
        assert shell._project_owner_token not in shell_mod._GUI_PROJECT_OWNERS.values(), (
            "the shell is still an owner in _GUI_PROJECT_OWNERS"
        )

        # The curation event filter: a bare curation key routed through the application no
        # longer reaches the controller once the shell is closed.
        before = len(shell.controller.history)
        key = QtGui.QKeyEvent(
            QtCore.QEvent.Type.KeyPress,
            QtCore.Qt.Key.Key_Space,
            QtCore.Qt.KeyboardModifier.NoModifier,
        )
        QtCore.QCoreApplication.sendEvent(shell.molecule_list, key)
        assert len(shell.controller.history) == before, "the curation event filter outlived close"
        assert shell._window_close_filter is None, "the window close filter outlived close"
