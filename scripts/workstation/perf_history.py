# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The speed and memory history of the reference workstation (#723).

After each run, ``run_private_checks.py`` appends one record to a history file (JSON lines,
format ``tether-workstation-history/1``): the commit, the date, a hardware summary, the time and
peak memory of each pipeline stage on each acquisition (``tests/test_workstation_perf.py``), the
``.tether`` size, the benchmark suite of #722, and the projection for a condition of 100 movies.

The report rule (maintainer decision Q27): a value larger than 1.2 times the median of the same
value in the last 10 records of ``main`` on the same hardware is a slowdown. The runner adds two
checks to its result file, ``perf-history/acquisitions`` and ``perf-history/benchmarks``, so a
slowdown opens an issue through the rule of #698. A projection that does not fit the overnight
window of ADR-0032 also fails ``perf-history/acquisitions``.

The history holds neutral ids, stage names, benchmark case names and numbers: no file names and
no traces, so a copy can go on an issue. This file uses only the standard library, like the runner.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import subprocess
import sys
from pathlib import Path

HISTORY_SCHEMA = "tether-workstation-history/1"
#: Report rule (Q27): above LIMIT times the median of the last WINDOW_RUNS values.
LIMIT = 1.2
WINDOW_RUNS = 10
#: Fewer earlier values than this give no median to compare with: the value only records.
MIN_BASELINE = 3
#: Peak memory below this is too small for a ratio to mean anything (as in benchmarks/bench.py).
MEMORY_FLOOR_BYTES = 1 << 20
#: ADR-0032 (tether.project.perf): a condition of about 100 movies, overnight is 12 hours.
CONDITION_MOVIES = 100
OVERNIGHT_WINDOW_HOURS = 12.0
#: The stages that must finish overnight (PRD §8 NFR-PERF): extraction, corrections, idealization.
OVERNIGHT_STAGES = ("extract", "correct", "idealize")
PIPELINE_CHECK = "tests/test_workstation_perf.py::test_pipeline_speed_and_memory"
CHECK_ACQUISITIONS = "perf-history/acquisitions"
CHECK_BENCHMARKS = "perf-history/benchmarks"

_NEUTRAL_ID = re.compile(r"[a-z]+-[0-9]{2,}")
_METRIC = re.compile(r"[a-z][a-z0-9_]{0,63}")
_CASE = re.compile(r"[a-z][a-z0-9_]*/[a-z]+")
_HEX40 = re.compile(r"[0-9a-f]{40}")
_STAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")
_TOP = {"schema", "commit", "on_main", "date", "hardware", "acquisitions", "benchmarks"}
_TOP |= {"projection"}
_HARDWARE = {"os", "machine", "cpu", "cpu_count", "memory_gb", "disk", "gpu"}
_PROJECTION = {"per_movie_s", "condition_movies", "condition_hours", "window_hours", "fits_window"}


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


# --- Hardware: disk type and GPU on top of the runner's summary -------------------------------


def _quiet(cmd: list[str]) -> str | None:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    out = proc.stdout.strip()
    return out if proc.returncode == 0 and out else None


def disk_type(path: Path) -> str | None:
    """``ssd`` or ``hdd`` for the disk that holds *path*, or ``None`` when it cannot be read."""
    path = Path(path).resolve()
    if sys.platform == "win32":
        letter = path.drive.rstrip(":")
        script = (
            f"$n = (Get-Partition -DriveLetter {letter} | Get-Disk).Number; "
            "(Get-PhysicalDisk | Where-Object DeviceId -eq $n).MediaType"
        )
        out = _quiet(["powershell", "-NoProfile", "-Command", script]) if letter.isalpha() else None
        return {"ssd": "ssd", "hdd": "hdd"}.get((out or "").strip().lower())
    try:
        dev = path.stat().st_dev
        block = Path(f"/sys/dev/block/{dev >> 8}:{dev & 0xFF}").resolve()
        for folder in (block, block.parent):
            rotational = folder / "queue" / "rotational"
            if rotational.is_file():
                return "hdd" if rotational.read_text().strip() == "1" else "ssd"
    except OSError:
        pass
    return None


def gpu_name() -> str | None:
    out = _quiet(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"])
    if out:
        return out.splitlines()[0].strip()
    if sys.platform == "win32":
        script = "(Get-CimInstance Win32_VideoController | Select-Object -First 1).Name"
        return _quiet(["powershell", "-NoProfile", "-Command", script])
    return None


# --- One record ---------------------------------------------------------------------------------


def projection(acquisitions: dict[str, dict[str, float]]) -> dict | None:
    """The time for a condition of 100 movies, from the median time of one movie."""
    per_movie = [
        sum(values[f"{s}_s"] for s in OVERNIGHT_STAGES)
        for values in acquisitions.values()
        if all(_number(values.get(f"{s}_s")) for s in OVERNIGHT_STAGES)
    ]
    if not per_movie:
        return None
    seconds = statistics.median(per_movie)
    hours = seconds * CONDITION_MOVIES / 3600
    return {
        "per_movie_s": round(seconds, 3),
        "condition_movies": CONDITION_MOVIES,
        "condition_hours": round(hours, 3),
        "window_hours": OVERNIGHT_WINDOW_HOURS,
        "fits_window": hours <= OVERNIGHT_WINDOW_HOURS,
    }


def bench_values(bench: dict | None) -> dict[str, dict[str, float]]:
    """Time and peak memory of each case of a ``benchmarks/bench.py run`` result (one side)."""
    out: dict[str, dict[str, float]] = {}
    for result in bench or []:
        for case, rec in next(iter(result["sides"].values()), {}).items():
            if "error" not in rec and _number(rec.get("time_s")):
                peak = rec.get("peak_child_rss_bytes") or rec.get("peak_py_bytes")
                out[case] = {"time_s": rec["time_s"]}
                if _number(peak):
                    out[case]["peak_bytes"] = peak
    return out


def build_record(
    *, commit: str | None, on_main: bool, date: str, hardware: dict, checks: list, bench: list
) -> dict:
    pipeline = next((c for c in checks if c["check"] == PIPELINE_CHECK), None)
    acquisitions = dict(pipeline["datasets"]) if pipeline else {}
    return {
        "schema": HISTORY_SCHEMA,
        "commit": commit,
        "on_main": on_main,
        "date": date,
        "hardware": {k: v for k, v in hardware.items() if k in _HARDWARE},
        "acquisitions": acquisitions,
        "benchmarks": bench_values(bench),
        "projection": projection(acquisitions),
    }


def validate_record(record: object) -> list[str]:
    """The problems of *record* against ``tether-workstation-history/1``."""
    if not isinstance(record, dict) or set(record) != _TOP:
        return [f"a record must have exactly the keys {sorted(_TOP)}"]
    problems = []
    if record["schema"] != HISTORY_SCHEMA:
        problems.append("schema is not valid")
    if not (record["commit"] is None or _HEX40.fullmatch(str(record["commit"]))):
        problems.append("commit is not valid")
    if not isinstance(record["on_main"], bool) or not _STAMP.fullmatch(str(record["date"])):
        problems.append("on_main or date is not valid")
    hw = record["hardware"]
    if not isinstance(hw, dict) or not set(hw) <= _HARDWARE:
        problems.append("hardware is not valid")
    for name, pattern in (("acquisitions", _NEUTRAL_ID), ("benchmarks", _CASE)):
        group = record[name]
        if not isinstance(group, dict):
            problems.append(f"{name} is not an object")
            continue
        for key, values in group.items():
            if not pattern.fullmatch(key) or not isinstance(values, dict):
                problems.append(f"{name} has a key that is not valid")
            elif not all(_METRIC.fullmatch(k) and _number(v) for k, v in values.items()):
                problems.append(f"{name}.{key} holds a value that is not a number")
    proj = record["projection"]
    if proj is not None and not (
        isinstance(proj, dict)
        and set(proj) == _PROJECTION
        and isinstance(proj["fits_window"], bool)
        and all(_number(proj[k]) for k in _PROJECTION - {"fits_window"})
    ):
        problems.append("projection is not valid")
    return problems


# --- The report rule ----------------------------------------------------------------------------


def series(record: dict) -> dict[str, float]:
    """Every compared value of *record*, by a stable name."""
    out = {}
    for group in ("acquisitions", "benchmarks"):
        for key, values in record[group].items():
            for metric, value in values.items():
                if metric.endswith("bytes") and value < MEMORY_FLOOR_BYTES:
                    continue
                out[f"{group}:{key}:{metric}"] = value
    return out


def _fingerprint(hardware: dict) -> tuple:
    return tuple(hardware.get(k) for k in ("cpu", "memory_gb", "disk", "gpu"))


def slowdowns(history: list[dict], record: dict) -> list[dict]:
    """Each value of *record* above LIMIT times its median over the last WINDOW_RUNS records of
    ``main`` on the same hardware. A value with fewer than MIN_BASELINE earlier values passes."""
    same = [
        r
        for r in history
        if r["on_main"] and _fingerprint(r["hardware"]) == _fingerprint(record["hardware"])
    ]
    earlier = [series(r) for r in same]
    found = []
    for name, value in series(record).items():
        past = [s[name] for s in earlier if name in s][-WINDOW_RUNS:]
        if len(past) < MIN_BASELINE:
            continue
        median = statistics.median(past)
        if median > 0 and value > LIMIT * median:
            found.append(
                {"series": name, "value": value, "median": median, "ratio": value / median}
            )
    return found


def checks_for(record: dict, found: list[dict]) -> list[dict]:
    """The two result-file checks of the history (format ``tether-workstation-result/1``)."""
    datasets: dict[str, dict[str, float]] = {}
    for item in found:
        group, key, metric = item["series"].split(":")
        if group == "acquisitions":
            datasets.setdefault(key, {})[f"{metric}_ratio"] = round(item["ratio"], 3)
    proj = record["projection"]
    acq_ok = bool(record["acquisitions"]) and proj is not None and proj["fits_window"]
    acq_ok = acq_ok and not datasets
    bench_ok = bool(record["benchmarks"]) and not any(
        i["series"].startswith("benchmarks:") for i in found
    )
    return [
        {"check": CHECK_ACQUISITIONS, "outcome": "passed" if acq_ok else "failed",
         "duration_s": 0.0, "datasets": datasets},
        {"check": CHECK_BENCHMARKS, "outcome": "passed" if bench_ok else "failed",
         "duration_s": 0.0, "datasets": {}},
    ]  # fmt: skip


# --- The history file ---------------------------------------------------------------------------


def read_history(path: Path) -> list[dict]:
    if not Path(path).is_file():
        return []
    records = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        try:
            record = json.loads(line)
        except ValueError:
            raise ValueError(f"line {number} of the history is not JSON") from None
        problems = validate_record(record)
        if problems:
            raise ValueError(f"line {number} of the history: {'; '.join(problems)}")
        records.append(record)
    return records


def append(path: Path, record: dict, tokens: frozenset[str] = frozenset()) -> list[dict]:
    """Append *record* to the history at *path*; return its slowdowns against the earlier records.

    Refuses a record that does not match the format or that contains a private name.
    """
    problems = validate_record(record)
    if problems:
        raise ValueError("the history record does not match its format: " + "; ".join(problems))
    text = json.dumps(record, ensure_ascii=False, sort_keys=True)
    if any(token in text for token in tokens):
        raise ValueError("the history record names a private file; it is not written")
    found = slowdowns(read_history(path), record)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(text + "\n")
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("history", help="the history file (JSON lines)")
    args = parser.parse_args(argv)
    records = read_history(Path(args.history))
    if not records:
        print(json.dumps({"records": 0}))
        return 0
    found = slowdowns(records[:-1], records[-1])
    newest = records[-1]
    summary = {"records": len(records), "newest": newest["date"], "commit": newest["commit"]}
    print(json.dumps({**summary, "projection": newest["projection"], "slowdowns": found}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
