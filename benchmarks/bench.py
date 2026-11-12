# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run the benchmark suite on two source trees and compare them (#722, ADR-0078).

Subcommands:

* ``one`` -- measure one case at one size against one ``src/`` tree; prints one JSON line.
  The other subcommands start it as a child process, so each measurement has its own
  process (its own peak RSS, its own first call).
* ``run`` -- measure every case of a group on each ``--side NAME=SRC``, interleaved: for each
  case and size the sides run one after the other, and the order turns round each round.
  Writes the results as JSON.
* ``compare`` -- read a ``run`` result, write the table (operation, base, head, ratio) and
  exit 1 when a gated case is slower or takes more peak memory than ``--limit`` times base.
* ``noise`` -- read a ``run`` result whose sides are all the same commit and report the
  spread of each case (the noise report of #722).

Time is the minimum over repeats (as ``tether.project.perf.min_runtime`` does); a repeat
runs the operation enough times to last at least ``_MIN_REPEAT_S``, so a fast operation is
not timed at the clock's resolution. Memory is the ``tracemalloc`` peak of one extra call
(Python and NumPy allocations) and the process peak RSS; a case whose work runs in a
child process (the sidecar fit) gates on that child's peak RSS instead (``Case.memory``).
The first call is timed apart and reported as ``first_call_s``: it holds the one-time costs
(imports, any Numba compilation), so it never enters the timed part.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

HERE = Path(__file__).resolve().parent
TESTS = HERE.parent / "tests"

#: A gated case fails when head/base exceeds this (the start value of #722, Q27).
DEFAULT_LIMIT = 1.5
#: Peak memory below this is too small for a ratio to mean anything; it only reports.
MEMORY_FLOOR_BYTES = 1 << 20
#: The noise report keeps the 1.5 gate on a case only when max/min over its runs is at most this.
NOISE_CEILING = 1.2
_MIN_REPEAT_S = 0.05


def _peak_rss_bytes(children: bool = False) -> int | None:
    try:
        import resource
    except ImportError:  # Windows
        return None
    who = resource.RUSAGE_CHILDREN if children else resource.RUSAGE_SELF
    peak = resource.getrusage(who).ru_maxrss
    return int(peak if sys.platform == "darwin" else peak * 1024)  # Linux reports KiB


def measure(case_name: str, size: str, repeats: int) -> dict:
    """Measure one case in this process; the caller has put the tree under test on sys.path."""
    from cases import CASES

    case = CASES[case_name]
    with tempfile.TemporaryDirectory(prefix="tether-bench-") as tmp:
        fn, extra = case.setup(size, Path(tmp))
        start = time.perf_counter()
        fn()
        first = time.perf_counter() - start
        loops = max(1, math.ceil(_MIN_REPEAT_S / max(first, 1e-9)))
        best = math.inf
        for _ in range(repeats):
            start = time.perf_counter()
            for _ in range(loops):
                fn()
            best = min(best, (time.perf_counter() - start) / loops)
        tracemalloc.start()
        fn()
        _, peak_py = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    rss = _peak_rss_bytes()
    children = _peak_rss_bytes(children=True)  # the sidecar fit runs in a child process
    return {
        "time_s": best,
        "first_call_s": first,
        "loops": loops,
        "peak_py_bytes": peak_py,
        "peak_rss_bytes": max(rss or 0, children or 0) or None,
        "peak_child_rss_bytes": children or None,
        **extra,
    }


def _cmd_one(args: argparse.Namespace) -> int:
    src = str(Path(args.src).resolve())
    sys.path[:0] = [src, str(TESTS), str(HERE)]
    import tether

    if not Path(tether.__file__).resolve().is_relative_to(src):
        # A stale install would otherwise compare a tree with itself and pass every time.
        print(json.dumps({"error": f"tether imported from {tether.__file__}, not {src}"}))
        return 1
    try:
        record = measure(args.case, args.size, args.repeats)
    except Exception as exc:  # reported, then judged by compare
        record = {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(record))
    return 0


def _child(src: str, case: str, size: str, repeats: int) -> dict:
    cmd = [sys.executable, "-I", str(Path(__file__).resolve()), "one", "--src", src]
    cmd += ["--case", case, "--size", size, "--repeats", str(repeats)]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    lines = proc.stdout.strip().splitlines()
    try:
        return json.loads(lines[-1])
    except (IndexError, json.JSONDecodeError):
        return {"error": f"exit {proc.returncode}: {proc.stderr.strip()[-400:]}"}


def expand_sides(specs: list[str], copies: int = 1) -> dict[str, str]:
    """``NAME=SRC`` specs as a dict; with ``copies > 1`` (the noise run) each is N sides."""
    sides = dict(spec.split("=", 1) for spec in specs)
    if copies > 1:
        sides = {f"{n}{i}": p for n, p in sides.items() for i in range(1, copies + 1)}
    return sides


def _merge(records: list[dict]) -> dict:
    good = [r for r in records if "error" not in r]
    if not good:
        return {"error": records[-1]["error"]}
    out = dict(good[0])
    for key in (
        "time_s",
        "first_call_s",
        "peak_py_bytes",
        "peak_rss_bytes",
        "peak_child_rss_bytes",
    ):
        values = [r[key] for r in good if r.get(key) is not None]
        out[key] = min(values) if values else None
    out["rounds_time_s"] = [r["time_s"] for r in good]
    return out


def _cmd_run(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(HERE))
    from cases import CASES, SIZES

    sides = expand_sides(args.side, args.copies)
    names = [c.name for c in CASES.values() if c.group == args.group]
    if args.cases:
        names = [n for n in names if n in args.cases.split(",")]
    sizes = args.sizes.split(",") if args.sizes else list(SIZES)
    raw: dict[str, dict[str, list[dict]]] = {side: {} for side in sides}
    for r in range(args.rounds):
        order = list(sides) if r % 2 == 0 else list(reversed(sides))
        for name in names:
            for size in sizes:
                for side in order:
                    rec = _child(sides[side], name, size, args.repeats)
                    raw[side].setdefault(f"{name}/{size}", []).append(rec)
                    print(f"round {r + 1} {side:>6} {name}/{size}: {rec}", flush=True)
    result = {
        "group": args.group,
        "sides": {side: {k: _merge(v) for k, v in raw[side].items()} for side in sides},
    }
    Path(args.out).write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    return 0


def _fmt_s(value: float | None) -> str:
    return "-" if value is None else f"{value * 1e3:.3f} ms"


def _fmt_b(value: float | None) -> str:
    return "-" if value is None else f"{value / 2**20:.2f} MiB"


def compare(result: dict, base: str, head: str, limit: float) -> tuple[list[str], list[str]]:
    """Return (table lines, failures) for ``head`` against ``base`` in a ``run`` result."""
    from cases import CASES

    b_side, h_side = result["sides"][base], result["sides"][head]
    lines = [
        "| operation | base time | head time | ratio | base peak | head peak | ratio "
        "| head RSS | .tether | result |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    failures: list[str] = []
    for key in sorted(set(b_side) | set(h_side)):
        b, h = b_side.get(key, {"error": "missing"}), h_side.get(key, {"error": "missing"})
        case = CASES[key.split("/")[0]]
        gated, mem = case.gate, case.memory
        if "error" in h:
            failures.append(f"{key}: head failed to run ({h['error']})")
            lines.append(f"| {key} | | | | | | | | | FAIL: head error |")
            continue
        if "error" in b:
            lines.append(
                f"| {key} | | {_fmt_s(h['time_s'])} | | | | | | | base error, not compared |"
            )
            continue
        rt = h["time_s"] / b["time_s"] if b["time_s"] > 0 else math.inf
        big = (b.get(mem) or 0) >= MEMORY_FLOOR_BYTES
        rm = h[mem] / b[mem] if big and h.get(mem) is not None else None
        verdict = "ok" if gated else "reports only"
        if gated and rt > limit:
            failures.append(f"{key}: time {rt:.2f}x base (limit {limit}x)")
            verdict = "FAIL: slower"
        if gated and rm is not None and rm > limit:
            failures.append(f"{key}: peak memory {rm:.2f}x base (limit {limit}x)")
            verdict = "FAIL: memory" if verdict == "ok" else verdict + ", memory"
        size = h.get("tether_bytes")
        lines.append(
            f"| {key} | {_fmt_s(b['time_s'])} | {_fmt_s(h['time_s'])} | {rt:.2f} "
            f"| {_fmt_b(b.get(mem))} | {_fmt_b(h.get(mem))} "
            f"| {'-' if rm is None else f'{rm:.2f}'} | {_fmt_b(h.get('peak_rss_bytes'))} "
            f"| {'-' if size is None else f'{size:,} B'} | {verdict} |"
        )
    return lines, failures


def noise(result: dict) -> tuple[list[str], list[str]]:
    """Return (table lines, cases too noisy for the gate) for a run of one commit N times."""
    from cases import CASES

    sides = result["sides"]
    lines = [
        "| operation | runs | min time | max time | time spread | memory spread | gate at 1.5 |",
        "|---|---|---|---|---|---|---|",
    ]
    noisy: list[str] = []
    for key in sorted(next(iter(sides.values()))):
        recs = [s[key] for s in sides.values() if "error" not in s.get(key, {"error": 1})]
        if len(recs) < 2:
            lines.append(f"| {key} | {len(recs)} | | | | | no data |")
            noisy.append(key)
            continue
        times = [r["time_s"] for r in recs]
        mems = [r.get(CASES[key.split("/")[0]].memory) or 0 for r in recs]
        st, sm = max(times) / min(times), max(mems) / max(min(mems), 1)
        ok = st <= NOISE_CEILING and (max(mems) < MEMORY_FLOOR_BYTES or sm <= NOISE_CEILING)
        if not ok:
            noisy.append(key)
        lines.append(
            f"| {key} | {len(recs)} | {_fmt_s(min(times))} | {_fmt_s(max(times))} "
            f"| {st:.3f} | {sm:.3f} | {'yes' if ok else 'NO'} |"
        )
    return lines, noisy


def _write(lines: list[str], summary: str | None) -> None:
    text = "\n".join(lines) + "\n"
    print(text)
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(text)


def _cmd_compare(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(HERE))
    result = json.loads(Path(args.result).read_text(encoding="utf-8"))
    lines, failures = compare(result, args.base, args.head, args.limit)
    title = f"### Benchmarks ({result['group']}): head against base, limit {args.limit}x"
    _write([title, "", *lines, "", *(f"- **{f}**" for f in failures)], args.summary)
    for failure in failures:
        print(f"::error title=benchmark regression::{failure}")
    return 1 if failures else 0


def _cmd_noise(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(HERE))
    result = json.loads(Path(args.result).read_text(encoding="utf-8"))
    lines, noisy = noise(result)
    title = f"### Benchmark noise ({result['group']}): {len(result['sides'])} runs of one commit"
    footer = [
        f"Too noisy for the 1.5 limit (max/min above {NOISE_CEILING}): {', '.join(noisy) or 'none'}"
    ]
    _write([title, "", *lines, "", *footer], args.summary)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    one = sub.add_parser("one")
    one.add_argument("--src", required=True)
    one.add_argument("--case", required=True)
    one.add_argument("--size", required=True)
    one.add_argument("--repeats", type=int, default=5)
    run = sub.add_parser("run")
    run.add_argument("--side", action="append", required=True, help="NAME=PATH/TO/src")
    run.add_argument("--group", default="default", choices=["default", "gui", "sidecar"])
    run.add_argument("--rounds", type=int, default=3)
    run.add_argument("--repeats", type=int, default=5)
    run.add_argument("--copies", type=int, default=1, help="measure each side N times")
    run.add_argument("--sizes", default="")
    run.add_argument("--cases", default="")
    run.add_argument("--out", required=True)
    cmp_ = sub.add_parser("compare")
    cmp_.add_argument("result")
    cmp_.add_argument("--base", default="base")
    cmp_.add_argument("--head", default="head")
    cmp_.add_argument("--limit", type=float, default=DEFAULT_LIMIT)
    cmp_.add_argument("--summary", default=os.environ.get("GITHUB_STEP_SUMMARY"))
    nse = sub.add_parser("noise")
    nse.add_argument("result")
    nse.add_argument("--summary", default=os.environ.get("GITHUB_STEP_SUMMARY"))
    args = parser.parse_args(argv)
    handler = {"one": _cmd_one, "run": _cmd_run, "compare": _cmd_compare, "noise": _cmd_noise}
    return handler[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
