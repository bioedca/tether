# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the ``tether`` CLI entry point (PRD §7.11, NFR-REPRO)."""

from __future__ import annotations

import pytest

from tether import __version__
from tether.cli import build_parser, main


def test_version_flag_reports_git_version(capsys) -> None:
    # ``--version`` exits 0 (argparse) and prints the git-derived app version.
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith("tether ")
    assert __version__ in out


def test_no_args_prints_help_and_succeeds(capsys) -> None:
    rc = main([])
    assert rc == 0
    assert "usage: tether" in capsys.readouterr().out


def test_parser_program_name() -> None:
    assert build_parser().prog == "tether"
