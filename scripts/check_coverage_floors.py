# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Hold measured test coverage to the committed floors (issue #724, ADR-0072).

The Linux leg of the required ``test`` matrix runs the tests under ``coverage`` with branch
measurement and writes ``coverage json``. This script reads that report and
``.github/coverage-floors.toml``, then:

* writes one table of line and branch coverage, for the total and for each package of
  ``src/tether``, to stdout and to ``$GITHUB_STEP_SUMMARY`` when it is set;
* fails (exit 1) when a measured **line** coverage is below its floor;
* prints a ``::notice::`` when a measured value is more than one point above its floor, so the
  floor can be raised;
* fails (exit 2) when a package has no floor or a floor names no package, so a new or renamed
  package can never leave the gate by accident;
* fails (exit 2) when the report was made without branch measurement (its
  ``meta.branch_coverage`` flag is absent or false), because such a report carries no branch
  counters and the table would show every branch value as 100 % (issue #848).

The floors can only go up. ``lowered_floors`` compares two floor tables, and
``tests/test_coverage_floors.py`` holds the committed table to the one on ``main`` with it.

Standard library only, so it runs on any interpreter that has the report.
"""

from __future__ import annotations

import json
import math
import os
import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path, PurePosixPath

FLOORS = Path(__file__).resolve().parents[1] / ".github" / "coverage-floors.toml"
TOTAL = "total"
#: How far a measured value may sit above its floor before the notice asks for a raise.
SLACK = 1.0


def load_floors(text: str) -> dict[str, float]:
    """Return the ``[floors]`` table of a floors file as ``{name: percent}``."""
    table = tomllib.loads(text).get("floors")
    if not isinstance(table, dict) or TOTAL not in table:
        raise ValueError(f"the floors file needs a [floors] table with a '{TOTAL}' key")
    return {str(k): float(v) for k, v in table.items()}


def package_of(path: str) -> str | None:
    """Name the ``tether`` subpackage that holds *path*, or ``None`` for a top-level module."""
    parts = PurePosixPath(path.replace("\\", "/")).parts
    idx = max((i for i, part in enumerate(parts) if part == "tether"), default=None)
    if idx is None or len(parts) < idx + 3:
        return None
    return parts[idx + 1]


def measure(report: Mapping) -> dict[str, dict[str, int]]:
    """Sum lines and branches of a ``coverage json`` report for the total and each package."""
    keys = ("covered_lines", "num_statements", "covered_branches", "num_branches")
    sums: dict[str, dict[str, int]] = {}
    for path, data in report["files"].items():
        for name in (TOTAL, package_of(path)):
            if name is None:
                continue
            row = sums.setdefault(name, dict.fromkeys(keys, 0))
            for key in keys:
                row[key] += int(data["summary"].get(key, 0))
    return sums


def branch_measured(report: Mapping) -> bool:
    """Tell whether *report* was made with branch measurement (``meta.branch_coverage``)."""
    meta = report.get("meta")
    return isinstance(meta, Mapping) and meta.get("branch_coverage") is True


def _pct(covered: int, total: int) -> float:
    return 100.0 * covered / total if total else 100.0


def check(sums: Mapping, floors: Mapping) -> tuple[list[str], list[str], list[str], list[str]]:
    """Return ``(table, failures, notices, errors)`` for measured *sums* against *floors*."""
    table = [
        "| Package | Lines | Line % | Branches | Branch % | Floor | Result |",
        "|---|---|---|---|---|---|---|",
    ]
    failures: list[str] = []
    notices: list[str] = []
    errors = [f"floor '{n}' names no measured package" for n in sorted(set(floors) - set(sums))]
    errors += [f"package '{n}' has no floor" for n in sorted(set(sums) - set(floors))]
    for name in [TOTAL, *sorted(n for n in sums if n != TOTAL)]:
        row = sums[name]
        line = _pct(row["covered_lines"], row["num_statements"])
        branch = _pct(row["covered_branches"], row["num_branches"])
        floor = floors.get(name)
        result = "no floor"
        if floor is not None:
            result = "below floor" if line < floor else "ok"
            if line < floor:
                failures.append(
                    f"{name}: line coverage {line:.2f} % is below its floor {floor:g} %"
                )
            elif line - floor > SLACK:
                notices.append(
                    f"{name}: line coverage {line:.2f} % is more than {SLACK:g} point above its "
                    f"floor {floor:g} %; the floor can be raised to {math.floor(line)}"
                )
        table.append(
            f"| {name} | {row['covered_lines']}/{row['num_statements']} | {line:.2f} | "
            f"{row['covered_branches']}/{row['num_branches']} | {branch:.2f} | "
            f"{'' if floor is None else f'{floor:g}'} | {result} |"
        )
    return table, failures, notices, errors


def lowered_floors(base: Mapping, head: Mapping) -> list[str]:
    """List every floor of *base* that *head* removes or lowers; an empty list is a pass."""
    problems = []
    for name, value in sorted(base.items()):
        if name not in head:
            problems.append(f"floor '{name}' ({value:g}) was removed")
        elif head[name] < value:
            problems.append(f"floor '{name}' went down from {value:g} to {head[name]:g}")
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: check_coverage_floors.py COVERAGE_JSON", file=sys.stderr)
        return 2
    report = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    if not branch_measured(report):
        print(
            "::error title=Coverage floor::the report has no branch data: its "
            "meta.branch_coverage flag is absent or false; make it with branch measurement "
            "(coverage run --branch, or [tool.coverage.run] branch = true)"
        )
        return 2
    sums = measure(report)
    table, failures, notices, errors = check(sums, load_floors(FLOORS.read_text(encoding="utf-8")))
    text = "## Test coverage (Linux leg)\n\nFloors hold line coverage.\n\n" + "\n".join(table)
    print(text)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")
    for message in notices:
        print(f"::notice title=Coverage floor can be raised::{message}")
    for message in failures + errors:
        print(f"::error title=Coverage floor::{message}")
    return 2 if errors else 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
