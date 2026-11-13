# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Compare the skips and the test count of one CI job with the committed budget (#726).

A test that starts to skip leaves CI green: a fixture file that is absent, a Git LFS pointer
that is not materialized, a tool that is no longer on the runner, an optional package that
was renamed. pytest exits 0 when tests skip, so nothing failed. Each CI job that runs pytest
therefore writes a JUnit report (``--junitxml``), and this script compares that report with
the job's part of ``tests/skip_budget.toml``. The job fails when

* a test skips and no ``[[skip]]`` entry for the job matches its node id and its reason —
  the message names the test and the reason;
* more tests skip than the job's ``max_skipped``;
* the report holds fewer tests than the job's ``min_collected`` — a path filter, a marker
  expression or a module-level skip that takes a whole file out of the run.

An entry matches a skip when its ``test`` equals the node id or is an ``fnmatch`` pattern that
matches it, and its ``reason`` equals the skip reason exactly. Each entry names the jobs where
the skip is correct and says why (``why``), so every skip on a job is a recorded decision.

Exit status: ``0`` in budget, ``1`` out of budget, ``2`` the report or the budget cannot be
read (absent, not XML or TOML, a job the budget does not name). The interpreter is whichever
environment the job restored (base, sidecar or deep), so this module imports the standard
library only.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import os
import sys
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_BUDGET = REPO / "tests" / "skip_budget.toml"

#: The reason pytest gives in a JUnit report when a whole module skips at collection
#: (``pytest.importorskip`` or ``pytest.skip(allow_module_level=True)``). The real reason is
#: then in the element text, as the repr of ``(path, line, "Skipped: <reason>")``.
_COLLECTION_SKIPPED = "collection skipped"
_SKIPPED_PREFIX = "Skipped: "


class BudgetError(Exception):
    """The report or the budget cannot be read; the check cannot give a verdict."""


@dataclass(frozen=True)
class Skip:
    test: str
    reason: str


@dataclass(frozen=True)
class Report:
    tests: tuple[str, ...]
    skips: tuple[Skip, ...]


@dataclass(frozen=True)
class Expected:
    test: str
    reason: str
    why: str

    def matches(self, skip: Skip) -> bool:
        if skip.reason != self.reason:
            return False
        # An exact node id first: a parametrized id holds "[...]", which fnmatch reads as a
        # character class.
        return skip.test == self.test or fnmatch.fnmatchcase(skip.test, self.test)


@dataclass(frozen=True)
class Budget:
    job: str
    min_collected: int
    max_skipped: int
    expected: tuple[Expected, ...]


def _is_module(part: str) -> bool:
    """pytest's default ``python_files`` (``test_*.py``, ``*_test.py``); classes are ``Test*``."""
    return part.startswith("test_") or part.endswith("_test")


def node_id(classname: str, name: str) -> str:
    """The pytest node id of one JUnit ``testcase``.

    pytest writes ``classname`` as the module path with ``/`` as ``.`` and no ``.py``,
    followed by the class names, and ``name`` as the function name with its parameter id. A
    module that skips at collection has an empty ``classname`` and its dotted path in ``name``.
    """
    if not classname:
        return name.replace(".", "/") + ".py"
    parts = classname.split(".")
    modules = [i for i, part in enumerate(parts) if _is_module(part)]
    if not modules:
        return f"{classname}::{name}"
    cut = modules[-1] + 1
    return "::".join(["/".join(parts[:cut]) + ".py", *parts[cut:], name])


def _reason(skipped: ET.Element) -> str:
    message = skipped.get("message", "")
    if message == _COLLECTION_SKIPPED:
        try:
            detail = ast.literal_eval((skipped.text or "").strip())
            message = str(detail[2])
        except (ValueError, SyntaxError, TypeError, IndexError):
            message = (skipped.text or "").strip()
    if message.startswith(_SKIPPED_PREFIX):
        message = message[len(_SKIPPED_PREFIX) :]
    return message.strip()


def read_report(path: Path) -> Report:
    """The tests and the skips of one pytest JUnit report (``--junitxml``)."""
    try:
        root = ET.parse(path).getroot()
    except FileNotFoundError as exc:
        raise BudgetError(f"no JUnit report at {path}: did the pytest step run?") from exc
    except ET.ParseError as exc:
        raise BudgetError(f"{path} is not a JUnit XML report: {exc}") from exc
    tests: list[str] = []
    skips: list[Skip] = []
    seen: set[str] = set()
    for case in root.iter("testcase"):
        test = node_id(case.get("classname", ""), case.get("name", ""))
        if test in seen:
            continue
        seen.add(test)
        tests.append(test)
        skipped = case.find("skipped")
        # An xfail is reported as <skipped type="pytest.xfail">; pytest counts it apart.
        if skipped is not None and skipped.get("type") != "pytest.xfail":
            skips.append(Skip(test, _reason(skipped)))
    return Report(tuple(tests), tuple(skips))


def _text(entry: dict, key: str, where: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        raise BudgetError(f"{where}: `{key}` must be a non-empty string")
    return value


def _count(entry: dict, key: str, where: str) -> int:
    value = entry.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BudgetError(f"{where}: `{key}` must be an integer of 0 or more")
    return value


def load_budget(path: Path, job: str) -> Budget:
    """The budget of *job*, after the whole file is validated.

    The whole file, not only *job*'s part: a ``[[skip]]`` entry that names a job with a typo
    would otherwise allow nothing anywhere and no run would say so.
    """
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BudgetError(f"no skip budget at {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise BudgetError(f"{path} is not valid TOML: {exc}") from exc
    jobs = data.get("jobs")
    if not isinstance(jobs, dict) or not jobs:
        raise BudgetError(f'{path}: no [jobs."<check name>"] tables')
    limits = {
        name: (
            _count(table, "min_collected", f'[jobs."{name}"]'),
            _count(table, "max_skipped", f'[jobs."{name}"]'),
        )
        for name, table in jobs.items()
    }
    expected: list[Expected] = []
    for number, entry in enumerate(data.get("skip", []), start=1):
        where = f"[[skip]] entry {number}"
        names = entry.get("jobs")
        if not isinstance(names, list) or not names:
            raise BudgetError(f"{where}: `jobs` must be a non-empty list of job names")
        unknown = [n for n in names if n not in limits]
        if unknown:
            raise BudgetError(f"{where}: `jobs` names no [jobs] table: {unknown}")
        item = Expected(
            _text(entry, "test", where), _text(entry, "reason", where), _text(entry, "why", where)
        )
        if job in names:
            expected.append(item)
    if job not in limits:
        raise BudgetError(f'{path} has no [jobs."{job}"] table; known jobs: {sorted(limits)}')
    min_collected, max_skipped = limits[job]
    return Budget(job, min_collected, max_skipped, tuple(expected))


def check(report: Report, budget: Budget) -> tuple[list[str], list[str]]:
    """(errors, notices) for *report* against *budget*; any error fails the job."""
    errors: list[str] = []
    notices: list[str] = []
    for skip in report.skips:
        if not any(entry.matches(skip) for entry in budget.expected):
            errors.append(
                f"unexpected skip: {skip.test} -- reason: {skip.reason!r}. Make the test run "
                f'on this job, or add a [[skip]] entry for "{budget.job}" to '
                "tests/skip_budget.toml that says why the skip is correct here."
            )
    if len(report.skips) > budget.max_skipped:
        errors.append(
            f"{len(report.skips)} tests skipped; the budget of {budget.job} allows "
            f"max_skipped = {budget.max_skipped}."
        )
    if len(report.tests) < budget.min_collected:
        errors.append(
            f"{len(report.tests)} tests ran; the budget of {budget.job} needs "
            f"min_collected = {budget.min_collected}. A path filter, a marker expression or a "
            "module-level skip took tests out of the run. If the pull request removes tests "
            "on purpose, lower min_collected in tests/skip_budget.toml in the same pull request."
        )
    for entry in budget.expected:
        if not any(entry.matches(skip) for skip in report.skips):
            notices.append(
                f"expected skip did not occur: {entry.test} -- reason: {entry.reason!r}. "
                "Remove the entry when the test now runs on this job."
            )
    # The limits only protect while they are tight, so say when they are loose.
    if len(report.skips) < budget.max_skipped:
        notices.append(
            f"{len(report.skips)} tests skipped, fewer than max_skipped = {budget.max_skipped}: "
            "lower it in tests/skip_budget.toml when the difference is permanent."
        )
    if len(report.tests) > budget.min_collected:
        notices.append(
            f"{len(report.tests)} tests ran, more than min_collected = {budget.min_collected}: "
            "raise it in tests/skip_budget.toml when the difference is permanent."
        )
    return errors, notices


def _escape(message: str) -> str:
    """A workflow-command message is one line: escape what GitHub reads as a delimiter."""
    return message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _emit(kind: str, message: str) -> None:
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::{kind} title=skip budget::{_escape(message)}")
    else:
        print(f"{kind}: {message}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--job", required=True, help="the check name, e.g. 'sidecar / parity'")
    parser.add_argument("--report", required=True, type=Path, help="pytest --junitxml file")
    parser.add_argument("--budget", type=Path, default=DEFAULT_BUDGET, help="the budget TOML")
    args = parser.parse_args(argv)
    try:
        budget = load_budget(args.budget, args.job)
        report = read_report(args.report)
    except BudgetError as exc:
        _emit("error", str(exc))
        return 2
    print(
        f"skip budget of {budget.job}: {len(report.tests)} tests "
        f"(min_collected {budget.min_collected}), {len(report.skips)} skipped "
        f"(max_skipped {budget.max_skipped})"
    )
    for skip in report.skips:
        print(f"  skipped: {skip.test} -- {skip.reason!r}")
    errors, notices = check(report, budget)
    for message in notices:
        _emit("notice", message)
    for message in errors:
        _emit("error", message)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
