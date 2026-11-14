# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the ``tether`` CLI entry point (PRD §7.11, NFR-REPRO)."""

from __future__ import annotations

import argparse
import json
from types import SimpleNamespace

import pytest

from tether import __version__
from tether.cli import build_parser, main
from tether.extract_choices import DETECTION_MODES, DONOR_SIDES, PREALIGN_MODES


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


def test_batch_rejects_duplicate_movie_basenames(tmp_path, capsys) -> None:
    # Two movies with the same basename in different folders would map to the same
    # <out-dir>/<stem>.tether and silently collide on the checkpoint; the CLI must
    # reject them (exit 2) rather than process one as the other.
    (tmp_path / "condA").mkdir()
    (tmp_path / "condB").mkdir()
    m1 = tmp_path / "condA" / "movie_010.tif"
    m2 = tmp_path / "condB" / "movie_010.tif"
    m1.write_bytes(b"x")
    m2.write_bytes(b"y")
    out = tmp_path / "out"

    rc = main(["batch", str(m1), str(m2), "--out-dir", str(out), "--no-idealize"])

    assert rc == 2
    err = capsys.readouterr().err
    assert "movie_010.tether" in err
    assert "rename one" in err


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


# --- Sidecar supervision wiring (PR7-B) --------------------------------------


def _capture_run_batch(monkeypatch):
    """Replace batch.run_batch with a capture stub; return the captured jobs and kwargs.

    The stub also writes one record through the ``log`` it is given, so a test can find
    the file that the batch log writes to.
    """
    import tether.project.batch as batch_mod

    captured: dict[str, object] = {}

    def _fake(jobs, **kwargs):
        captured["jobs"] = list(jobs)
        captured["kwargs"] = kwargs
        kwargs["log"].event(movie="capture", stage="batch", status="done")
        return SimpleNamespace(format_report=lambda: "(report)", n_failed=0)

    monkeypatch.setattr(batch_mod, "run_batch", _fake)
    return captured


def test_batch_builds_supervision_from_flags(tmp_path, monkeypatch) -> None:
    captured = _capture_run_batch(monkeypatch)
    m = tmp_path / "movie_001.tif"
    m.write_bytes(b"x")
    rc = main(
        [
            "batch",
            str(m),
            "--out-dir",
            str(tmp_path / "out"),
            "--max-restarts",
            "5",
            "--sidecar-timeout",
            "60",
            "--sidecar-python",
            "/x/py",
            "--no-defer",
        ]
    )
    assert rc == 0
    sup = captured["kwargs"]["supervision"]
    assert sup.max_restarts == 5
    assert sup.timeout == 60.0
    assert str(sup.sidecar_python) == "/x/py"
    assert sup.defer_if_unavailable is False


def test_batch_supervision_defaults(tmp_path, monkeypatch) -> None:
    captured = _capture_run_batch(monkeypatch)
    m = tmp_path / "movie_001.tif"
    m.write_bytes(b"x")
    rc = main(["batch", str(m), "--out-dir", str(tmp_path / "out")])
    assert rc == 0
    sup = captured["kwargs"]["supervision"]
    assert sup.max_restarts == 3  # §11.2 default
    assert sup.defer_if_unavailable is True


def test_batch_no_idealize_disables_supervision(tmp_path, monkeypatch) -> None:
    captured = _capture_run_batch(monkeypatch)
    m = tmp_path / "movie_001.tif"
    m.write_bytes(b"x")
    rc = main(["batch", str(m), "--out-dir", str(tmp_path / "out"), "--no-idealize"])
    assert rc == 0
    assert captured["kwargs"]["supervision"] is None
    assert captured["kwargs"]["idealize"] is False


# --- Flags that reach the batch run (#730) ----------------------------------


def _log_movies(path) -> list[str]:
    return [json.loads(line)["movie"] for line in path.read_text(encoding="utf-8").splitlines()]


def test_batch_forwards_tmap_tdat_policy_overwrite_and_log(tmp_path, monkeypatch, capsys) -> None:
    # A batch that dropped --tmap/--tdat would fit a native registration for each
    # movie instead of the imported map, with no message; --log, --overwrite and
    # --policy must reach the run too.
    captured = _capture_run_batch(monkeypatch)
    movies = [tmp_path / "movie_001.tif", tmp_path / "movie_002.tif"]
    for movie in movies:
        movie.write_bytes(b"x")
    tmap, tdat = tmp_path / "beads.tmap", tmp_path / "movie.tdat"
    out, log = tmp_path / "out", tmp_path / "logs" / "night.jsonl"

    rc = main(
        [
            "batch",
            *map(str, movies),
            "--out-dir",
            str(out),
            "--tmap",
            str(tmap),
            "--tdat",
            str(tdat),
            "--policy",
            "fail",
            "--overwrite",
            "--log",
            str(log),
            "--no-idealize",
        ]
    )

    assert rc == 0
    assert [(j.movie_path, j.output_path, j.tmap, j.tdat) for j in captured["jobs"]] == [
        (movies[0], out / "movie_001.tether", tmap, tdat),
        (movies[1], out / "movie_002.tether", tmap, tdat),
    ]
    kwargs = captured["kwargs"]
    assert kwargs["policy"] == "fail"
    assert kwargs["overwrite"] is True
    assert _log_movies(log) == ["capture"]  # the run's log writes to --log ...
    assert not (out / "batch-log.jsonl").exists()  # ... and not to the default path
    assert f"structured log: {log}" in capsys.readouterr().err


def test_batch_defaults_reach_run_batch(tmp_path, monkeypatch) -> None:
    captured = _capture_run_batch(monkeypatch)
    m = tmp_path / "movie_001.tif"
    m.write_bytes(b"x")
    out = tmp_path / "out"

    rc = main(["batch", str(m), "--out-dir", str(out), "--no-idealize"])

    assert rc == 0
    assert [(j.tmap, j.tdat) for j in captured["jobs"]] == [(None, None)]
    assert captured["kwargs"]["policy"] == "warn"
    assert captured["kwargs"]["overwrite"] is False
    assert _log_movies(out / "batch-log.jsonl") == ["capture"]


def test_batch_real_run_applies_tmap_and_tdat_and_writes_log(tmp_path, monkeypatch, capsys) -> None:
    # `tether batch` on a readable movie, with the real run_batch, extract and correct
    # stages: the profile records the imported map and the detection file.
    for module in ("h5py", "scipy", "skimage", "tifffile"):
        pytest.importorskip(module)
    import h5py  # noqa: PLC0415

    import test_extract_cli as helpers  # noqa: PLC0415
    import tether.project.extract as extract_module  # noqa: PLC0415

    monkeypatch.setattr(
        extract_module, "read_tmap", lambda _path: helpers._synthetic_tmap_channels()
    )
    movie = helpers._make_movie(tmp_path, name="movie_010.tif")
    tmap = tmp_path / "map.tmap"
    tmap.write_bytes(b"stub .tmap; read_tmap is monkeypatched")
    tdat = helpers._write_tdat(tmp_path / "data.tdat", mode_code=2.0)
    out, log = tmp_path / "out", tmp_path / "night.jsonl"

    rc = main(
        [
            "batch",
            str(movie),
            "--out-dir",
            str(out),
            "--tmap",
            str(tmap),
            "--tdat",
            str(tdat),
            "--log",
            str(log),
            "--no-idealize",
        ]
    )

    assert rc == 0, capsys.readouterr()
    with h5py.File(out / "movie_010.tether", "r") as f:
        profile = json.loads(f["/settings/extraction"].attrs["profile_json"])
    assert profile["registration_source"] == "imported"
    assert profile["tmap_source"] == "map.tmap"
    assert profile["tdat_source"] == "data.tdat"
    records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert ("movie_010", "extract", "done") in [
        (r["movie"], r["stage"], r["status"]) for r in records
    ]


def test_batch_rejects_negative_max_restarts(tmp_path, capsys) -> None:
    m = tmp_path / "movie_001.tif"
    m.write_bytes(b"x")
    rc = main(["batch", str(m), "--out-dir", str(tmp_path / "out"), "--max-restarts", "-1"])
    assert rc == 2
    assert "max_restarts must be >= 0" in capsys.readouterr().err


# --- choice options of ``tether extract`` (#654) -------------------------------------

_CHOICE_FLAGS = [
    ("--donor-side", "donor_side", DONOR_SIDES),
    ("--detection-mode", "detection_mode", DETECTION_MODES),
    ("--prealign", "prealign", PREALIGN_MODES),
]


def _extract_action(dest: str) -> argparse.Action:
    parser = build_parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))  # noqa: SLF001
    extract = sub.choices["extract"]
    return next(a for a in extract._actions if a.dest == dest)  # noqa: SLF001


@pytest.mark.parametrize(("flag", "dest", "allowed"), _CHOICE_FLAGS)
def test_extract_choice_flag_uses_the_library_constants(flag, dest, allowed) -> None:
    # The parser's choices are the very tuples ExtractOptions validates against.
    assert _extract_action(dest).choices is allowed


@pytest.mark.parametrize(("flag", "dest", "allowed"), _CHOICE_FLAGS)
def test_extract_rejects_a_wrong_choice_at_parse_time(
    tmp_path, monkeypatch, capsys, flag, dest, allowed
) -> None:
    # A wrong value is a usage error: exit 2 and the usage line, before the extract
    # handler (and with it any file access) runs.
    import tether.cli as cli  # noqa: PLC0415

    def _no_extract(args):  # pragma: no cover - reached only if parsing let it through
        raise AssertionError("the extract handler ran for a wrong choice value")

    monkeypatch.setattr(cli, "_run_extract", _no_extract)
    out = tmp_path / "out.tether"
    with pytest.raises(SystemExit) as exc:
        main(["extract", str(tmp_path / "movie.tif"), "-o", str(out), flag, "sideways"])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert err.startswith("usage: tether extract")
    assert f"argument {flag}: invalid choice: 'sideways'" in err
    for value in allowed:
        assert value in err
    assert not out.exists()


def test_extract_detection_mode_default_still_means_not_given() -> None:
    # None tells "no flag" apart from an explicit --detection-mode for the --tdat check.
    args = build_parser().parse_args(["extract", "m.tif", "-o", "o.tether"])
    assert args.detection_mode is None
    assert args.donor_side == "left"
    assert args.prealign == "translation"


def test_extract_help_lists_the_choices(capsys) -> None:
    with pytest.raises(SystemExit):
        main(["extract", "--help"])
    out = capsys.readouterr().out
    for allowed in (DONOR_SIDES, DETECTION_MODES, PREALIGN_MODES):
        assert "{" + ",".join(allowed) + "}" in out
