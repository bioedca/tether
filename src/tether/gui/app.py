# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The ``tether-gui`` desktop entry point (PRD §7.8, §9 M9).

The front door a shortcut or a shell command opens. It builds the
[`TetherShell`][tether.gui.shell.TetherShell] against a **real** store — either the
``.tether`` named on the command line, or none, in which case the shell opens empty
and the curator picks one through ``&File -> &Open project...``.

This is deliberately *not* [`tether.gui.shell.launch`][], which fabricates six
synthetic traces and a demo project as a hand-driven live-smoke helper. Wiring a
desktop shortcut to that entry would ship a demo rather than the application.

The Qt import is deferred into [`create_shell`][], after the arguments are parsed,
so that ``--version`` and ``--help`` answer without paying for PySide6/pyqtgraph,
matching the lazy-import discipline [`tether.cli`][] uses for the imaging stack.
The first Qt import is PySide6 itself, not ``pyqtgraph.Qt``: importing pyqtgraph loads
NumPy, and the splash must be on the screen before that. pyqtgraph then picks the
binding that is already loaded.

Between the ``QApplication`` and the slow import of the shell, [`create_shell`][]
shows the startup splash ([`tether.gui.splash`][], #676) on a platform with a screen.

A double-click on a ``.tether`` project (issue #664, ADR-0118) reaches this module in two
ways. On Windows and Linux the file type starts ``tether-gui PROJECT.tether``, so the
project is the command-line argument. On macOS the system starts ``Tether.app`` without
arguments and sends the file as a file-open event, or sends the event to the Tether that is
already running; [`create_shell`][] gives each such event to
[`load_project`][tether.gui.shell.TetherShell.load_project], the path of
``&File -> &Open project...``. An event that arrives before the shell exists waits for it.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from tether import __version__
from tether.gui.env_path import put_env_folders_first

if TYPE_CHECKING:  # pragma: no cover - typing only
    from tether.gui.shell import TetherShell

__all__ = ["build_parser", "create_shell", "install_file_open_handler", "main"]

_ORGANIZATION = "MondragonLab"


def build_parser() -> argparse.ArgumentParser:
    """Construct the ``tether-gui`` argument parser."""
    parser = argparse.ArgumentParser(
        prog="tether-gui",
        description="Tether - a single-molecule FRET desktop suite (graphical shell).",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"tether {__version__}",
        help="show the git-derived Tether version and exit",
    )
    parser.add_argument(
        "project",
        nargs="?",
        default=None,
        metavar="PROJECT.tether",
        help=(
            "optional .tether project to open on startup; omit to start empty and "
            "use File -> Open project..."
        ),
    )
    return parser


def install_file_open_handler(app: Any) -> Any:
    """Catch the file-open events of *app*; return the object that holds them.

    macOS sends a file-open event (``QEvent.FileOpen``) when the user opens a ``.tether``
    file that ``Tether.app`` declares, in place of a command-line argument. The returned
    object filters the events of *app* and keeps the path of each one until
    ``attach(open_project)`` is called. Then the newest waiting path goes to *open_project*
    (each open replaces the open project, so opening the older ones first would only open
    and close them), and from then on each new path goes to it at once. When the object is
    destroyed (it is given the shell window as its parent), the filter goes with it.
    """
    from PySide6 import QtCore

    class _FileOpenHandler(QtCore.QObject):
        def __init__(self) -> None:
            super().__init__()
            self.pending: list[str] = []
            self._open: Any = None

        def attach(self, open_project: Any) -> None:
            self._open = open_project
            pending, self.pending = self.pending, []
            if pending:
                open_project(pending[-1])

        def eventFilter(self, watched: Any, event: Any) -> bool:  # noqa: N802 - Qt API
            if event.type() != QtCore.QEvent.Type.FileOpen:
                return False
            path = event.file() or event.url().toLocalFile()
            if not path:
                return False
            if self._open is None:
                self.pending.append(path)
            else:
                self._open(path)
            return True

    handler = _FileOpenHandler()
    app.installEventFilter(handler)
    return handler


def create_shell(
    argv: Sequence[str] | None = None, *, splash: bool | None = None
) -> tuple[Any, TetherShell]:
    """Build the ``QApplication`` and shell **without** entering the event loop.

    Split out from [`main`][] so the startup path is exercisable headlessly: a test
    can assert that a named project is opened, or that a bad path leaves a usable empty
    shell, without blocking on ``app.exec()``.

    Reuses an existing ``QApplication`` when one is already running (``qtbot`` provides
    one under test), since Qt permits only a single instance per process.

    A project that fails to open is reported on the status bar by
    [`load_project`][tether.gui.shell.TetherShell.load_project] and leaves the shell empty
    rather than raising — starting the application must not be fatal just because the
    path on the command line was wrong.

    The arguments are parsed first, so ``--version`` and ``--help`` exit before any Qt
    import and before any window. Then the application identity — the Tether icon, the
    Windows AppUserModelID and the Linux desktop file name (issue #673,
    [`tether.gui.identity`][]) — is set, before the splash: Windows reads the identity
    only before the first window, and the splash is a window. Then the startup splash
    shows, before the import of [`tether.gui.shell`][], which is the slow part; it
    closes when the shell window is shown, or before the error propagates when the start
    fails. ``splash=None`` shows it unless the Qt platform is headless; ``True`` or
    ``False`` decides it explicitly.

    A file-open event (macOS, [`install_file_open_handler`][]) is caught from the moment the
    ``QApplication`` exists. It opens its project after the shell is built, and after the
    project on the command line, so the project the user opened last is the one shown.
    """
    args = build_parser().parse_args(argv)

    # PySide6 directly, not pyqtgraph.Qt: importing pyqtgraph loads NumPy, and the
    # splash must be on the screen before that. pyqtgraph then picks the same binding.
    from PySide6 import QtWidgets

    from tether.gui import splash as startup
    from tether.gui.identity import APPLICATION_NAME, apply_application_identity

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setApplicationName(APPLICATION_NAME)
    app.setApplicationDisplayName(APPLICATION_NAME)
    app.setOrganizationName(_ORGANIZATION)
    app.setApplicationVersion(__version__)
    apply_application_identity(app)
    file_open = install_file_open_handler(app)

    wanted = not startup.is_headless(app) if splash is None else splash
    screen = startup.open_splash(app) if wanted else None
    try:
        from tether.gui.shell import TetherShell

        shell = TetherShell()
        if args.project is not None:
            shell.load_project(args.project)
    except BaseException:
        app.removeEventFilter(file_open)
        if screen is not None:
            screen.abort()
        raise
    file_open.setParent(shell.window)
    file_open.attach(shell.load_project)
    if screen is not None:
        screen.close_when_shown(shell.window)
    return app, shell


def main(argv: Sequence[str] | None = None) -> int:
    """Open the Tether shell and run until the window closes.

    First, on Windows, it puts the env's own DLL folders at the front of ``PATH``
    ([`tether.gui.env_path.put_env_folders_first`][], issue #579), before PySide6 or
    NumPy loads. That is the step ``launchers\\tether-gui.bat`` used to be needed for (issue
    #553), so the Start Menu shortcut can start the console-less ``tether-gui.exe``
    directly and no console window flashes (ADR-0076).

    Returns the Qt exit status, so ``tether-gui`` propagates a non-zero code the way a
    console entry point is expected to.
    """
    put_env_folders_first()
    app, shell = create_shell(argv)
    shell.show()
    return int(app.exec())


if __name__ == "__main__":  # pragma: no cover - module execution entry
    raise SystemExit(main())
