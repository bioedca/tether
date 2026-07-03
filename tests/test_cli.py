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


def test_batch_requires_out_dir(tmp_path) -> None:
    # ``-d/--out-dir`` is required; argparse exits 2 on a missing required option.
    with pytest.raises(SystemExit) as exc:
        main(["batch", str(tmp_path / "m.tif")])
    assert exc.value.code == 2


def test_batch_isolates_failure_and_exits_nonzero(tmp_path, capsys) -> None:
    # A non-TIFF "movie" makes the extract stage fail; the batch must isolate it
    # (continue-on-error), print an end-of-run summary naming the failure, write a
    # JSONL log, and exit 1 because a stage failed.
    bad = tmp_path / "not_a_movie_010.tif"
    bad.write_bytes(b"not a tiff at all")
    out = tmp_path / "out"

    rc = main(["batch", str(bad), "--out-dir", str(out), "--no-idealize"])

    assert rc == 1
    report = capsys.readouterr().out
    assert "1 movie(s), 0 ok, 1 failed" in report
    assert "not_a_movie_010.tif" in report
    assert (out / "batch-log.jsonl").exists()
