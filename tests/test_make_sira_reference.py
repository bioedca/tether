# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Octave quoting in ``scripts/make_sira_reference.py`` (#1425); no Octave is needed."""

from __future__ import annotations

import importlib.util
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "make_sira_reference.py"


@pytest.fixture(scope="module")
def script():  # noqa: ANN201 - a module loaded by path.
    spec = importlib.util.spec_from_file_location("make_sira_reference", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_apostrophe_is_doubled(script) -> None:
    assert script._octave_str(PurePosixPath("/home/o'brien/movie.sira")) == (
        "'/home/o''brien/movie.sira'"
    )


def test_plain_path_is_only_quoted(script) -> None:
    assert script._octave_str(PurePosixPath("/data/movie.sira")) == "'/data/movie.sira'"


def _unquoted_apostrophes(call: str) -> int:
    """Count apostrophes that end a string early: inside a literal, only ``''`` may appear."""
    stray, inside, i = 0, False, 0
    while i < len(call):
        if call[i] == "'":
            if inside and call[i + 1 : i + 2] == "'":
                i += 2
                continue
            inside = not inside
        elif not inside and call[i].isalpha() and call[i - 1 : i] == "'":
            stray += 1
        i += 1
    return stray


def test_every_path_in_the_octave_calls_is_escaped(script, tmp_path, monkeypatch) -> None:
    root = tmp_path / "o'brien"
    root.mkdir()
    calls: list[str] = []

    def fake_octave(work: Path, call: str) -> str:
        calls.append(f"addpath({script._octave_str(work)}); {call}")
        return "3 4 5"

    monkeypatch.setattr(script, "_octave", fake_octave)
    monkeypatch.setattr(script.tempfile, "TemporaryDirectory", lambda: _Dir(root))
    np = pytest.importorskip("numpy")
    monkeypatch.setattr(np, "fromfile", lambda *_a, **_k: np.zeros(60, dtype="<f4"))

    script.write_offsets(root / "out.sira", root / "mash")
    script.reference_frames(root / "in.sira", root / "mash")

    assert len(calls) == 2
    for call in calls:
        assert "o''brien" in call
        assert _unquoted_apostrophes(call) == 0
        assert call.count("'") % 2 == 0


def test_the_real_octave_escapes_its_addpath(script, tmp_path, monkeypatch) -> None:
    """The real ``_octave`` escapes the work folder in its ``addpath`` literal (#1456)."""
    work = tmp_path / "o'brien"
    work.mkdir()
    argv: list[list[str]] = []

    def fake_run(args: list[str], **_kwargs: object) -> SimpleNamespace:
        argv.append(list(args))
        return SimpleNamespace(stdout="3 4 5\n")

    monkeypatch.setattr(script.subprocess, "run", fake_run)

    assert script._octave(work, "disp(1)") == "3 4 5\n"

    assert len(argv) == 1
    assert argv[0][:4] == ["octave-cli", "--no-gui", "--quiet", "--eval"]
    folder = work.as_posix()
    assert folder.count("'") == 1
    expected = "addpath('" + folder.replace("o'brien", "o''brien") + "'); disp(1)"
    assert argv[0][4] == expected
    assert _unquoted_apostrophes(argv[0][4]) == 0


class _Dir:
    def __init__(self, path: Path) -> None:
        self.path = path

    def __enter__(self) -> str:
        return str(self.path)

    def __exit__(self, *_exc: object) -> None:
        return None
