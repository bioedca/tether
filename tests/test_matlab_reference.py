# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The MATLAB reference harness (#700, ADR-0072), tested against a stand-in for ``matlab``.

CI has no MATLAB (#700 item 8). The stand-in ``tests/_matlab_standin.py`` takes the same
``-batch`` command, so these tests exercise everything but the tool's own MATLAB code: the pin
check, the job, the provenance record, and the checksum check that guards every frozen output.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MATLAB_DIR = ROOT / "scripts" / "reference" / "matlab"
STANDIN = str(Path(__file__).with_name("_matlab_standin.py"))
REFERENCE_FIXTURES = ROOT / "tests" / "fixtures" / "reference"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"tether_ref_{name}", MATLAB_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


harness = _load("harness")
DRIVERS = {m.TOOL: m for m in (_load("smfret_analysis2"), _load("mash_fret"))}


def _git(cwd: Path, *args: str) -> str:
    cmd = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args]
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def clone(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "clone"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "tool.m").write_text("function tool\nend\n", encoding="utf-8")
    _git(repo, "add", "tool.m")
    _git(repo, "commit", "-q", "-m", "tool")
    return repo, _git(repo, "rev-parse", "HEAD").strip()


@pytest.fixture
def traces(tmp_path: Path) -> Path:
    folder = tmp_path / "traces"
    folder.mkdir()
    (folder / "1 tr1.dat").write_text("0.03 100 50\n0.06 90 60\n", encoding="utf-8")
    (folder / "1 tr2.dat").write_text("0.03 80 70\n0.06 85 65\n", encoding="utf-8")
    return folder


def _pins(tmp_path: Path, tool: str, commit: str) -> Path:
    path = tmp_path / "pins.toml"
    path.write_text(f'[{tool}]\nkind = "git"\nurl = "u"\ncommit = "{commit}"\n', encoding="utf-8")
    return path


def _run_cli(tool, task, clone_dir, out, pins, inputs=()) -> int:
    argv = ["run", "--clone", str(clone_dir), "--task", task, "--out", str(out)]
    argv += ["--matlab", STANDIN, "--pins", str(pins)]
    for name, path in inputs:
        argv += ["--input", f"{name}={path}"]
    driver = DRIVERS[tool]
    return harness.main(driver.TOOL, driver.TASKS, argv)


CASES = [(tool, task) for tool, d in sorted(DRIVERS.items()) for task in sorted(d.TASKS)]


@pytest.mark.parametrize(("tool", "task"), CASES)
def test_each_driver_task_runs_against_the_standin(tmp_path, clone, traces, tool, task) -> None:
    spec = DRIVERS[tool].TASKS[task]
    assert (MATLAB_DIR / "m" / spec["dir"] / f"{spec['entry']}.m").is_file()
    inputs = [(name, traces) for name in spec["inputs"]]
    out = tmp_path / "out"
    assert _run_cli(tool, task, clone[0], out, _pins(tmp_path, tool, clone[1]), inputs) == 0

    record = harness.verify(out)
    assert record["method"] == "matlab-batch"
    assert record["tool"] == {"name": tool, "url": "u", "commit": clone[1]}
    assert record["matlab"]["release"] == "0000x"
    assert record["outputs"] == {f"{task}.csv": harness.sha256(out / f"{task}.csv")}
    assert (out / f"{task}.csv").read_text() == f"entry,value\n{spec['entry']},1\n"
    for name in spec["inputs"]:
        assert record["inputs"][name] == {
            "1 tr1.dat": harness.sha256(traces / "1 tr1.dat"),
            "1 tr2.dat": harness.sha256(traces / "1 tr2.dat"),
        }
    assert re.fullmatch(r"[0-9a-f]{40}|unknown", record["driver"]["commit"])
    assert {"date", "os", "params", "task"} <= set(record)
    # Inputs of a real run live on the workstation: no absolute path may leak into the record.
    assert str(tmp_path) not in (out / harness.RECORD).read_text(encoding="utf-8")


def _refusal(tmp_path, clone_dir, pins) -> str:
    with pytest.raises(harness.HarnessError) as err:
        harness.run(
            "smfret-analysis2",
            "fret",
            DRIVERS["smfret-analysis2"].TASKS,
            clone=clone_dir,
            inputs={"traces": tmp_path},
            out=tmp_path / "out",
            matlab=STANDIN,
            pins=harness.load_pins(pins),
        )
    assert not (tmp_path / "out").exists()
    return str(err.value)


def test_a_clone_off_the_pin_is_refused(tmp_path, clone) -> None:
    message = _refusal(tmp_path, clone[0], _pins(tmp_path, "smfret-analysis2", "0" * 40))
    assert "the pin is" in message


@pytest.mark.parametrize("change", ["modified", "untracked"])
def test_a_clone_with_local_changes_is_refused(tmp_path, clone, change) -> None:
    name = "tool.m" if change == "modified" else "shadow.m"
    (clone[0] / name).write_text("function tool\ndisp(1)\nend\n", encoding="utf-8")
    message = _refusal(tmp_path, clone[0], _pins(tmp_path, "smfret-analysis2", clone[1]))
    assert "local changes" in message and name in message


def test_a_files_pin_checks_each_script_hash(tmp_path, traces) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "cloud.m").write_text("function cloud\nend\n", encoding="utf-8")
    pins = {"lab": {"kind": "files", "files": {"cloud.m": harness.sha256(scripts / "cloud.m")}}}
    assert harness.verify_clone("lab", scripts, pins) == {
        "name": "lab",
        "files": pins["lab"]["files"],
    }
    (scripts / "cloud.m").write_text("function cloud\ndisp(1)\nend\n", encoding="utf-8")
    with pytest.raises(harness.HarnessError, match="cloud.m has SHA-256"):
        harness.verify_clone("lab", scripts, pins)


def test_a_failed_run_writes_no_record(tmp_path, clone) -> None:
    tasks = {"t": {"dir": "smfret_analysis2", "entry": "e", "params": {"fail": True}}}
    pins = {"x": {"kind": "git", "url": "u", "commit": clone[1]}}
    with pytest.raises(harness.HarnessError, match="matlab exited with 1"):
        harness.run(
            "x",
            "t",
            tasks,
            clone=clone[0],
            inputs={},
            out=tmp_path / "o",
            matlab=STANDIN,
            pins=pins,
        )
    assert not (tmp_path / "o" / harness.RECORD).exists()


def test_a_non_empty_output_directory_is_refused(tmp_path, clone) -> None:
    (tmp_path / "o").mkdir()
    (tmp_path / "o" / "old.csv").write_text("stale", encoding="utf-8")
    pins = {"x": {"kind": "git", "url": "u", "commit": clone[1]}}
    tasks = {"t": {"dir": "smfret_analysis2", "entry": "e"}}
    with pytest.raises(harness.HarnessError, match="not empty"):
        harness.run(
            "x",
            "t",
            tasks,
            clone=clone[0],
            inputs={},
            out=tmp_path / "o",
            matlab=STANDIN,
            pins=pins,
        )


def test_freeze_records_saved_files_and_manual_steps(tmp_path) -> None:
    saved = tmp_path / "total_XC.csv"
    saved.write_text("0,1\n", encoding="utf-8")
    harness.freeze(
        "smfret-analysis2",
        saved=[saved],
        out=tmp_path / "frozen",
        tool_version="121634e",
        matlab_release="unknown (the file does not say)",
        manual_steps=["choose the folder test-data/traces"],
    )
    record = harness.verify(tmp_path / "frozen")
    assert record["method"] == "saved-files"
    assert record["manual_steps"] == ["choose the folder test-data/traces"]
    assert record["outputs"] == {"total_XC.csv": harness.sha256(saved)}


def _frozen(tmp_path: Path, clone) -> Path:
    out = tmp_path / "out"
    pins = {"x": {"kind": "git", "url": "u", "commit": clone[1]}}
    tasks = {"t": {"dir": "smfret_analysis2", "entry": "e"}}
    harness.run("x", "t", tasks, clone=clone[0], inputs={}, out=out, matlab=STANDIN, pins=pins)
    return out


def test_verify_fails_when_an_output_changes(tmp_path, clone) -> None:
    out = _frozen(tmp_path, clone)
    data = bytearray((out / "t.csv").read_bytes())
    data[-2] ^= 1  # one bit of the value "1"
    (out / "t.csv").write_bytes(bytes(data))
    with pytest.raises(harness.HarnessError, match=r"t\.csv: SHA-256 .* the record says"):
        harness.verify(out)


def test_verify_fails_on_a_missing_or_unrecorded_file(tmp_path, clone) -> None:
    out = _frozen(tmp_path, clone)
    (out / "extra.csv").write_text("x", encoding="utf-8")
    with pytest.raises(harness.HarnessError, match="files not in the record: extra.csv"):
        harness.verify(out)
    (out / "extra.csv").unlink()
    (out / "t.csv").unlink()
    with pytest.raises(harness.HarnessError, match="SHA-256 missing"):
        harness.verify(out)


def test_verify_fails_on_a_record_without_a_matlab_release(tmp_path, clone) -> None:
    out = _frozen(tmp_path, clone)
    record = json.loads((out / harness.RECORD).read_text(encoding="utf-8"))
    record["matlab"]["release"] = ""
    (out / harness.RECORD).write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(harness.HarnessError, match="no MATLAB release"):
        harness.verify(out)


COMMITTED = sorted(REFERENCE_FIXTURES.rglob(harness.RECORD))


def test_the_committed_records_include_the_standin_selftest() -> None:
    # Guards the scan below against a vacuous pass: the stand-in record is always committed.
    assert REFERENCE_FIXTURES / "stand-in" / "selftest" / harness.RECORD in COMMITTED


@pytest.mark.parametrize(
    "record", COMMITTED, ids=lambda p: p.parent.relative_to(REFERENCE_FIXTURES).as_posix()
)
def test_every_committed_output_matches_its_record(record: Path) -> None:
    harness.verify(record.parent)


def test_the_pins_are_full_commits_and_cover_every_driver() -> None:
    pins = harness.load_pins()
    for tool in DRIVERS:
        assert pins[tool]["kind"] == "git"
        assert re.fullmatch(r"[0-9a-f]{40}", pins[tool]["commit"]), tool
        assert pins[tool]["url"].startswith("https://github.com/")


def test_the_standin_is_a_python_script() -> None:
    # The harness runs a ``.py`` --matlab with this interpreter; any other value is executed.
    assert harness._matlab_cmd(STANDIN) == [sys.executable, str(Path(STANDIN).resolve())]
    assert harness._matlab_cmd("matlab") == ["matlab"]


def test_a_relative_matlab_path_survives_the_temporary_working_directory(
    tmp_path, clone, monkeypatch
) -> None:
    monkeypatch.chdir(ROOT)
    pins = {"x": {"kind": "git", "url": "u", "commit": clone[1]}}
    tasks = {"t": {"dir": "smfret_analysis2", "entry": "e"}}
    rel = Path(STANDIN).relative_to(ROOT).as_posix()
    harness.run(
        "x", "t", tasks, clone=clone[0], inputs={}, out=tmp_path / "o", matlab=rel, pins=pins
    )
    harness.verify(tmp_path / "o")
