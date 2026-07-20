# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The ``tether-gui`` desktop entry point (PRD §7.8, §9 M9).

The front door a shortcut or a shell command opens. It builds the
:class:`~tether.gui.shell.TetherShell` against a **real** store — either the
``.tether`` named on the command line, or none, in which case the shell opens empty
and the curator picks one through ``&File -> &Open project...``.

This is deliberately *not* :func:`tether.gui.shell.launch`, which fabricates six
synthetic traces and a demo project as a hand-driven live-smoke helper. Wiring a
desktop shortcut to that entry would ship a demo rather than the application.

The Qt import is deferred into :func:`create_shell` so that ``--version`` and
``--help`` answer without paying for PySide6/pyqtgraph, matching the lazy-import
discipline :mod:`tether.cli` uses for the imaging stack.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from tether import __version__

if TYPE_CHECKING:  # pragma: no cover - typing only
    from tether.gui.shell import TetherShell

__all__ = ["build_parser", "create_shell", "main"]

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


def create_shell(argv: Sequence[str] | None = None) -> tuple[Any, TetherShell]:
    """Build the ``QApplication`` and shell **without** entering the event loop.

    Split out from :func:`main` so the startup path is exercisable headlessly: a test
    can assert that a named project is opened, or that a bad path leaves a usable empty
    shell, without blocking on ``app.exec()``.

    Reuses an existing ``QApplication`` when one is already running (``qtbot`` provides
    one under test), since Qt permits only a single instance per process.

    A project that fails to open is reported on the status bar by
    :meth:`~tether.gui.shell.TetherShell.load_project` and leaves the shell empty
    rather than raising — starting the application must not be fatal just because the
    path on the command line was wrong.
    """
    from pyqtgraph.Qt import QtWidgets

    from tether.gui.shell import TetherShell

    args = build_parser().parse_args(argv)

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setApplicationName("Tether")
    app.setApplicationDisplayName("Tether")
    app.setOrganizationName(_ORGANIZATION)
    app.setApplicationVersion(__version__)

    shell = TetherShell()
    if args.project is not None:
        shell.load_project(args.project)
    return app, shell


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - Qt event loop
    """Open the Tether shell and run until the window closes.

    Returns the Qt exit status, so ``tether-gui`` propagates a non-zero code the way a
    console entry point is expected to.
    """
    app, shell = create_shell(argv)
    shell.show()
    return int(app.exec())


if __name__ == "__main__":  # pragma: no cover - module execution entry
    raise SystemExit(main())
