# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Coverage floors and their ratchet (issue #724, ADR-0072).

The first half drives ``scripts/check_coverage_floors.py`` on small in-memory reports: a value
below its floor fails, a value more than one point above it prints a notice, and a package with
no floor (or a floor with no package) is an error. The second half holds the committed files:
``test_floors_never_go_down`` compares ``.github/coverage-floors.toml`` with the copy on
``main``, so a pull request that lowers or removes a floor fails here.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml  # provided by the base conda-lock (a mkdocs dependency)

_REPO = Path(__file__).resolve().parents[1]
SCRIPT = _REPO / "scripts" / "check_coverage_floors.py"
FLOORS_PATH = ".github/coverage-floors.toml"
#: The CI Linux leg sets this to the fetched ``main`` ref, which makes the ratchet mandatory.
BASE_ENV = "TETHER_COVERAGE_BASE_REF"

_spec = importlib.util.spec_from_file_location("check_coverage_floors", SCRIPT)
cf = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = cf
_spec.loader.exec_module(cf)


def _file(lines: int, covered: int, branches: int = 0, covered_branches: int = 0) -> dict:
    return {
        "summary": {
            "num_statements": lines,
            "covered_lines": covered,
            "num_branches": branches,
            "covered_branches": covered_branches,
        }
    }


def _report(**packages: tuple[int, int]) -> dict:
    return {"files": {f"src/tether/{n}/m.py": _file(*v) for n, v in packages.items()}}


def test_package_of_maps_paths_to_subpackages():
    assert cf.package_of("src/tether/io/kinsoft.py") == "io"
    assert cf.package_of("src\\tether\\gui\\panels\\x.py") == "gui"
    # A checkout directory that is itself named "tether" does not confuse it.
    assert cf.package_of("/w/tether/tether/src/tether/fret/e.py") == "fret"
    assert cf.package_of("src/tether/cli.py") is None


def test_a_value_below_its_floor_fails():
    sums = cf.measure(_report(io=(100, 90)))
    _, failures, _, errors = cf.check(sums, {"total": 80, "io": 95})
    assert errors == []
    assert failures == ["io: line coverage 90.00 % is below its floor 95 %"]


def test_the_total_counts_top_level_modules_too():
    report = _report(io=(10, 10))
    report["files"]["src/tether/cli.py"] = _file(10, 0)
    sums = cf.measure(report)
    assert sums["total"]["num_statements"] == 20
    _, failures, _, _ = cf.check(sums, {"total": 60, "io": 90})
    assert failures == ["total: line coverage 50.00 % is below its floor 60 %"]


def test_more_than_one_point_above_a_floor_prints_a_notice():
    sums = cf.measure(_report(io=(1000, 965)))
    _, failures, notices, _ = cf.check(sums, {"total": 96, "io": 95})
    assert failures == []
    # total is 96.5 (0.5 above), io is 96.5 (1.5 above): only io gets the notice.
    assert len(notices) == 1 and notices[0].startswith("io:")
    assert "raised to 96" in notices[0]


def test_a_package_without_a_floor_and_a_floor_without_a_package_are_errors():
    sums = cf.measure(_report(io=(10, 10), newpkg=(10, 10)))
    _, _, _, errors = cf.check(sums, {"total": 0, "io": 0, "gone": 0})
    assert errors == ["floor 'gone' names no measured package", "package 'newpkg' has no floor"]


def test_branch_coverage_is_reported_in_the_table():
    report = {"files": {"src/tether/io/m.py": _file(10, 10, 4, 1)}}
    table, *_ = cf.check(cf.measure(report), {"total": 0, "io": 0})
    assert "| io | 10/10 | 100.00 | 1/4 | 25.00 | 0 | ok |" in table


def test_main_writes_the_summary_and_exits_by_result(tmp_path, monkeypatch, capsys):
    floors = tmp_path / "floors.toml"
    floors.write_text("[floors]\ntotal = 90\nio = 95\n", encoding="utf-8")
    report = tmp_path / "coverage.json"
    summary = tmp_path / "summary.md"
    monkeypatch.setattr(cf, "FLOORS", floors)
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    report.write_text(json.dumps(_report(io=(100, 96))), encoding="utf-8")
    assert cf.main(["x", str(report)]) == 0
    assert "| io | 96/100 |" in summary.read_text(encoding="utf-8")

    report.write_text(json.dumps(_report(io=(100, 94))), encoding="utf-8")
    assert cf.main(["x", str(report)]) == 1
    assert "::error title=Coverage floor::io: line coverage 94.00 %" in capsys.readouterr().out

    report.write_text(json.dumps(_report(io=(1, 1), ml=(1, 1))), encoding="utf-8")
    assert cf.main(["x", str(report)]) == 2


def test_lowered_floors_finds_lowered_and_removed_values():
    base = {"total": 92, "io": 95, "ml": 84}
    assert cf.lowered_floors(base, {"total": 93, "io": 95, "ml": 84, "new": 50}) == []
    assert cf.lowered_floors(base, {"total": 91.5, "io": 95}) == [
        "floor 'ml' (84) was removed",
        "floor 'total' went down from 92 to 91.5",
    ]


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(_REPO), *args], capture_output=True, text=True)


def test_floors_never_go_down():
    """Acceptance criterion 3: a floor in the file may not be lower than the floor on ``main``."""
    ref = os.environ.get(BASE_ENV) or "origin/main"
    if _git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").returncode != 0:
        if os.environ.get(BASE_ENV):
            pytest.fail(f"{BASE_ENV}={ref} names no commit; the CI step that fetches main failed")
        pytest.skip(f"no {ref} in this checkout (CI's Linux leg fetches it and sets {BASE_ENV})")
    shown = _git("show", f"{ref}:{FLOORS_PATH}")
    if shown.returncode != 0:
        if _git("cat-file", "-e", f"{ref}:{FLOORS_PATH}").returncode == 0:
            pytest.fail(f"could not read {FLOORS_PATH} at {ref}: {shown.stderr}")
        return  # main has no floors yet (the pull request that adds them), so none can be lowered
    head = cf.load_floors((_REPO / FLOORS_PATH).read_text(encoding="utf-8"))
    assert cf.lowered_floors(cf.load_floors(shown.stdout), head) == []


def test_every_package_has_a_floor_in_range():
    floors = cf.load_floors((_REPO / FLOORS_PATH).read_text(encoding="utf-8"))
    packages = {p.parent.name for p in (_REPO / "src" / "tether").glob("*/__init__.py")}
    assert set(floors) == packages | {"total"}
    assert all(0 < value <= 100 for value in floors.values())


def test_the_omit_list_names_existing_modules_only():
    """Acceptance criterion 5: exclusions are by name, never a blanket pattern."""
    run = tomllib.loads((_REPO / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["coverage"]
    omit = run["run"]["omit"]
    assert run["run"]["branch"] is True
    assert omit, "the sidecar and deep modules must be named"
    for pattern in omit:
        assert pattern.startswith("*/tether/") and pattern.endswith(".py")
        assert "*" not in pattern[1:] and "?" not in pattern
        assert (_REPO / "src" / pattern[2:]).is_file(), pattern


def test_only_the_linux_leg_measures_coverage():
    """Acceptance criterion 1: the Linux leg measures and uploads; the other legs do not change."""
    ci = yaml.safe_load((_REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    steps = ci["jobs"]["test"]["steps"]
    run = next(s for s in steps if s.get("name") == "Run tests")["run"]
    linux, other = run.split("else", 1)
    assert "coverage run -m pytest" in linux and "coverage" not in other
    assert run.count("pytest -v -m ") == 2  # same command; tests/test_marker_contract.py pins it
    linux_only = [s for s in steps if "coverage" in json.dumps(s).lower() and s is not None]
    linux_only = [s for s in linux_only if s.get("name") != "Run tests"]
    assert linux_only, "the install, report and upload steps are missing"
    assert all(s.get("if", "").startswith("runner.os == 'Linux'") for s in linux_only)
    assert any("check_coverage_floors.py" in s.get("run", "") for s in linux_only)
    assert any(str(s.get("uses", "")).startswith("actions/upload-artifact@") for s in linux_only)
