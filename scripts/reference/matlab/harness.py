# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run a MATLAB reference tool at its pinned commit and freeze its outputs (#700, ADR-0074).

The drivers beside this file (``smfret_analysis2.py``, ``mash_fret.py``) name a tool and its
tasks; this module does the rest:

* ``run`` verifies the tool's clone (pinned commit, no local change; or the SHA-256 of each
  script for a ``kind = "files"`` pin), runs one task with ``matlab -batch`` on named inputs, and
  writes ``provenance.json`` beside the outputs.
* ``freeze`` makes the same record for files that the tool already saved (exports, project
  files), for a tool that cannot run without its window. The record says so, and lists any manual
  step that made a result.
* ``verify`` checks a frozen directory against its record. The required matrix runs it on every
  committed record (``tests/test_matlab_reference.py``).

No path outside the output directory goes into a record: inputs are named by file name only,
because the inputs of a real run live on the maintainer's workstation.

CI has no MATLAB. ``--matlab`` takes any executable; a ``.py`` file is run with this
interpreter, which is how the tests use the stand-in ``tests/_matlab_standin.py``.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
PINS = HERE / "pins.toml"
M_DIR = HERE / "m"
RECORD = "provenance.json"
RECORD_VERSION = 1
# What `version('-release')` returns ("2023b"), optionally with the "R" of the product name.
RELEASE = re.compile(r"R?\d{4}[a-z]")
# An absolute path (POSIX, home, Windows drive or UNC share) that does not continue a word or a
# relative path: any delimiter before it counts, so `/x`, [/x], <C:\x> and "~/x" are all found.
PRIVATE_PATH = re.compile(r"(?:^|(?<=[^\w./\\~-]))(?:/|~[/\\]|[A-Za-z]:[\\/]|\\\\)")


class HarnessError(RuntimeError):
    """A refusal: the clone, the inputs or the run is not fit to make a reference."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _files(path: Path) -> dict[str, str]:
    """SHA-256 of a file, or of every file under a directory, keyed by relative POSIX name."""
    if path.is_file():
        return {path.name: sha256(path)}
    if not path.is_dir():
        raise HarnessError(f"input does not exist: {path.name}")
    found = {p.relative_to(path).as_posix(): sha256(p) for p in path.rglob("*") if p.is_file()}
    if not found:
        raise HarnessError(f"input directory is empty: {path.name}")
    return dict(sorted(found.items()))


def _git(cwd: Path, *args: str) -> str:
    out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if out.returncode != 0:
        raise HarnessError(f"git {' '.join(args)} failed in {cwd.name}: {out.stderr.strip()}")
    return out.stdout


def load_pins(path: Path = PINS) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def verify_clone(tool: str, clone: Path, pins: dict) -> dict:
    """Refuse a clone that is not the pinned one; return the tool part of the record."""
    if tool not in pins:
        raise HarnessError(f"no pin for {tool!r} in pins.toml")
    pin = pins[tool]
    if pin["kind"] == "git":
        head = _git(clone, "rev-parse", "HEAD").strip()
        if head != pin["commit"]:
            raise HarnessError(f"{tool} clone is at {head}, the pin is {pin['commit']}")
        dirty = _git(clone, "status", "--porcelain", "--untracked-files=all").strip()
        if dirty:
            raise HarnessError(f"{tool} clone has local changes:\n{dirty}")
        return {"name": tool, "url": pin["url"], "commit": head}
    if pin["kind"] == "files":
        for name, want in pin["files"].items():
            path = clone / name
            got = sha256(path) if path.is_file() else "missing"
            if got != want:
                raise HarnessError(f"{tool} script {name} has SHA-256 {got}, the pin is {want}")
        return {"name": tool, "files": dict(pin["files"])}
    raise HarnessError(f"unknown pin kind {pin['kind']!r} for {tool}")


def _driver() -> dict:
    root = HERE.parents[2]
    try:
        commit = _git(root, "rev-parse", "HEAD").strip()
        clean = not _git(root, "status", "--porcelain", "--", str(HERE)).strip()
    except (HarnessError, FileNotFoundError):
        commit, clean = "unknown", False
    return {"commit": commit, "clean": clean}


def _fresh_out(out: Path) -> None:
    if out.exists() and any(out.iterdir()):
        raise HarnessError(f"output directory is not empty: {out}")
    out.mkdir(parents=True, exist_ok=True)


def _outputs(out: Path) -> dict[str, str]:
    found = {k: v for k, v in _files(out).items() if k != RECORD} if any(out.iterdir()) else {}
    if not found:
        raise HarnessError("the run wrote no output")
    return found


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _no_private_paths(*values) -> None:
    for text in (t for v in values for t in _strings(v)):
        if PRIVATE_PATH.search(text):
            raise HarnessError(f"a record may not hold an absolute path: {text!r}")


def _check_release(release: str) -> None:
    if not RELEASE.fullmatch(release or ""):
        raise HarnessError(f"not a MATLAB release (like 2023b): {release!r}")


def _write_record(out: Path, record: dict) -> Path:
    _check_release(record["matlab"]["release"])
    record = {"record_version": RECORD_VERSION, **record}
    record["date"] = _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds")
    record["os"] = platform.platform()
    record["driver"] = _driver()
    record["outputs"] = _outputs(out)
    path = out / RECORD
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _matlab_cmd(matlab: str) -> list[str]:
    # The run's working directory is a temporary one, so a relative path is resolved here.
    exe = str(Path(matlab).resolve()) if Path(matlab).is_file() else matlab
    return [sys.executable, exe] if exe.endswith(".py") else [exe]


def _quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def run(
    tool: str,
    task: str,
    tasks: dict,
    *,
    clone: Path,
    inputs: dict[str, Path],
    out: Path,
    params: dict | None = None,
    matlab: str = "matlab",
    pins: dict | None = None,
) -> Path:
    """Run one task of ``tool`` and return the path of the record it wrote."""
    if task not in tasks:
        raise HarnessError(f"{tool} has no task {task!r}; tasks: {', '.join(sorted(tasks))}")
    spec = tasks[task]
    missing = set(spec.get("inputs", ())) - set(inputs)
    if missing:
        raise HarnessError(f"task {task} needs the inputs: {', '.join(sorted(missing))}")
    params = {**spec.get("params", {}), **(params or {})}
    _no_private_paths(params)
    tool_part = verify_clone(tool, clone, load_pins() if pins is None else pins)
    input_part = {name: _files(path) for name, path in sorted(inputs.items())}
    _fresh_out(out)
    with tempfile.TemporaryDirectory() as tmp:
        job = {
            "tool": tool,
            "task": task,
            "entry": spec["entry"],
            "tool_dir": str(clone.resolve()),
            "task_dir": str((M_DIR / spec["dir"]).resolve()),
            "inputs": {name: str(path.resolve()) for name, path in inputs.items()},
            "output_dir": str(out.resolve()),
            "params": params,
            "matlab_info": str(Path(tmp, "matlab.json")),
        }
        job_file = Path(tmp, "job.json")
        job_file.write_text(json.dumps(job), encoding="utf-8")
        command = f"addpath({_quote(str(M_DIR))}); tether_run_job({_quote(str(job_file))})"
        done = subprocess.run([*_matlab_cmd(matlab), "-batch", command], cwd=tmp)
        if done.returncode != 0:
            raise HarnessError(f"matlab exited with {done.returncode}; no record written")
        try:
            info = json.loads(Path(job["matlab_info"]).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HarnessError("the run did not report its MATLAB release") from exc
    # The record must name the bytes MATLAB read, so an input changed during the run is refused.
    if {name: _files(path) for name, path in sorted(inputs.items())} != input_part:
        raise HarnessError("an input changed while MATLAB ran; no record written")
    return _write_record(
        out,
        {
            "method": "matlab-batch",
            "tool": tool_part,
            "task": task,
            "params": params,
            "matlab": {"release": info["release"], "version": info["version"]},
            "inputs": input_part,
            "manual_steps": [],
        },
    )


def freeze(
    tool: str,
    *,
    saved: list[Path],
    out: Path,
    tool_version: str,
    matlab_release: str,
    inputs: dict[str, Path] | None = None,
    manual_steps: list[str] | None = None,
) -> Path:
    """Freeze files that the tool already saved; the record says that no harness run made them.

    ``tool_version`` is the version that saved the files, as stated (the record keeps it apart
    from a pinned ``commit``); ``inputs`` are the files the tool read to make them, if known.
    """
    _check_release(matlab_release)
    _no_private_paths(tool_version, manual_steps or [])
    input_part = {name: _files(path) for name, path in sorted((inputs or {}).items())}
    _fresh_out(out)
    for path in saved:
        if not path.is_file():
            raise HarnessError(f"saved file does not exist: {path.name}")
        if (out / path.name).exists():
            raise HarnessError(f"two saved files share the name {path.name}")
        shutil.copy2(path, out / path.name)
    return _write_record(
        out,
        {
            "method": "saved-files",
            "tool": {"name": tool, "version": tool_version},
            "matlab": {"release": matlab_release},
            "inputs": input_part,
            "manual_steps": list(manual_steps or []),
        },
    )


def verify(out: Path) -> dict:
    """Check every file of a frozen directory against its record; return the record."""
    record = json.loads((out / RECORD).read_text(encoding="utf-8"))
    if not RELEASE.fullmatch(record.get("matlab", {}).get("release") or ""):
        raise HarnessError(f"{out.name}: the record states no MATLAB release")
    actual = {k: v for k, v in _files(out).items() if k != RECORD}
    for name, want in record["outputs"].items():
        got = actual.pop(name, "missing")
        if got != want:
            raise HarnessError(f"{out.name}/{name}: SHA-256 {got}, the record says {want}")
    if actual:
        raise HarnessError(f"{out.name}: files not in the record: {', '.join(sorted(actual))}")
    return record


def _pairs(items: list[str], what: str) -> dict[str, str]:
    pairs = {}
    for item in items:
        name, sep, value = item.partition("=")
        if not sep or not name:
            raise HarnessError(f"{what} must read NAME=VALUE, not {item!r}")
        pairs[name] = value
    return pairs


def main(tool: str, tasks: dict, argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"MATLAB reference driver for {tool} (#700)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run", help="run one task at the pinned commit")
    p_run.add_argument("--clone", type=Path, required=True)
    p_run.add_argument("--task", required=True, choices=sorted(tasks))
    p_run.add_argument("--input", action="append", default=[], metavar="NAME=PATH")
    p_run.add_argument("--param", action="append", default=[], metavar="NAME=JSON")
    p_run.add_argument("--out", type=Path, required=True)
    p_run.add_argument("--matlab", default="matlab")
    p_run.add_argument("--pins", type=Path, default=PINS, help=argparse.SUPPRESS)
    p_frz = sub.add_parser("freeze", help="freeze files that the tool already saved")
    p_frz.add_argument("saved", type=Path, nargs="+")
    p_frz.add_argument("--out", type=Path, required=True)
    p_frz.add_argument("--tool-version", required=True)
    p_frz.add_argument("--matlab-release", required=True)
    p_frz.add_argument("--input", action="append", default=[], metavar="NAME=PATH")
    p_frz.add_argument("--manual-step", action="append", default=[])
    p_ver = sub.add_parser("verify", help="check a frozen directory against its record")
    p_ver.add_argument("out", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.cmd == "run":
            inputs = {k: Path(v) for k, v in _pairs(args.input, "--input").items()}
            params = {k: json.loads(v) for k, v in _pairs(args.param, "--param").items()}
            path = run(
                tool,
                args.task,
                tasks,
                clone=args.clone,
                inputs=inputs,
                out=args.out,
                params=params,
                matlab=args.matlab,
                pins=load_pins(args.pins),
            )
        elif args.cmd == "freeze":
            path = freeze(
                tool,
                saved=args.saved,
                out=args.out,
                tool_version=args.tool_version,
                matlab_release=args.matlab_release,
                inputs={k: Path(v) for k, v in _pairs(args.input, "--input").items()},
                manual_steps=args.manual_step,
            )
        else:
            verify(args.out)
            path = args.out / RECORD
    except HarnessError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 3
    print(path)
    return 0
