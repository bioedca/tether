# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The ``tether`` command-line entry point (PRD §7.11, NFR-REPRO).

A thin headless front door over :mod:`tether.project`. M0 ships the version stub
only — ``tether --version`` reports the git-derived app version (NFR-REPRO:
"the app version is derived from git"). Real subcommands (e.g. ``extract``) land
at M1; the parser is structured so they slot in as ``add_subparsers`` entries.

The CLI deliberately uses the standard-library :mod:`argparse` rather than a
third-party framework: a version stub needs no dependency, and adding one would
force a base ``conda-lock`` regeneration (the pin-and-hold invariant). Revisit
click/typer at M1 when real subcommands justify it.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from tether import __version__


def build_parser() -> argparse.ArgumentParser:
    """Construct the top-level ``tether`` argument parser."""
    parser = argparse.ArgumentParser(
        prog="tether",
        description="Tether - a single-molecule FRET desktop suite (headless core).",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"tether {__version__}",
        help="show the git-derived Tether version and exit",
    )
    # Subcommands land at M1 (extract / correct / idealize); the empty dispatcher
    # keeps the M0 stub forward-compatible.
    parser.add_subparsers(dest="command", metavar="<command>")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI. Returns a process exit code (``0`` on success).

    ``--version`` is handled by argparse (which exits ``0``). With no subcommand
    this prints help and returns ``0`` — a no-op success, not an error.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    # No dispatchable subcommands exist yet (M1); unreachable until they land.
    parser.error(f"unknown command: {args.command}")  # pragma: no cover
    return 2  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
