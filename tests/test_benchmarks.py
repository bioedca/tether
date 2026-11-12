# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The benchmark suite and its comparison job (#722, ADR-0078).

These tests check the parts that decide a verdict -- the ratio, the limit, the gate, the
noise spread -- on recorded numbers, so they are exact and fast; they do not time anything.
One smoke test runs every default case once at the small size, so a case that no longer
matches the API fails here and not first in the comparison job.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "benchmarks"))

import bench  # noqa: E402
from cases import CASES, SIZES  # noqa: E402

WORKFLOW = ROOT / ".github" / "workflows" / "benchmarks.yml"


def _rec(time_s: float, peak: int = 4 << 20, **extra) -> dict:
    return {"time_s": time_s, "first_call_s": time_s, "peak_py_bytes": peak, **extra}


def _result(base: dict, head: dict) -> dict:
    return {"group": "default", "sides": {"base": base, "head": head}}


def test_every_operation_of_the_issue_has_a_case_at_two_sizes() -> None:
    assert set(CASES) == {
        "spot_finding", "extraction", "background", "photobleach", "leakage_alpha", "gamma",
        "histogram", "cross_correlation", "cloud", "transition_density", "dwell_times",
        "project_open", "project_save", "project_export", "trace_display", "idealize_sidecar",
    }  # fmt: skip
    assert len(SIZES) == 2
    assert {c.group for c in CASES.values()} == {"default", "gui", "sidecar"}
    assert CASES["trace_display"].group == "gui"
    assert CASES["idealize_sidecar"].group == "sidecar"


def test_a_slower_gated_case_fails_and_names_the_operation() -> None:
    lines, failures = bench.compare(
        _result({"photobleach/small": _rec(0.010)}, {"photobleach/small": _rec(0.016)}),
        "base", "head", 1.5,
    )  # fmt: skip
    assert failures == ["photobleach/small: time 1.60x base (limit 1.5x)"]
    assert "FAIL: slower" in lines[-1]


def test_a_case_inside_the_limit_passes() -> None:
    _, failures = bench.compare(
        _result({"histogram/large": _rec(0.010)}, {"histogram/large": _rec(0.0149)}),
        "base", "head", 1.5,
    )  # fmt: skip
    assert failures == []


def test_more_peak_memory_fails_and_a_tiny_peak_only_reports() -> None:
    base = {"cloud/small": _rec(0.1, 4 << 20), "gamma/small": _rec(0.1, 10_000)}
    head = {"cloud/small": _rec(0.1, 7 << 20), "gamma/small": _rec(0.1, 100_000)}
    _, failures = bench.compare(_result(base, head), "base", "head", 1.5)
    assert failures == ["cloud/small: peak memory 1.75x base (limit 1.5x)"]


def test_an_ungated_case_reports_and_never_fails(monkeypatch) -> None:
    from dataclasses import replace

    monkeypatch.setitem(CASES, "cloud", replace(CASES["cloud"], gate=False))
    lines, failures = bench.compare(
        _result({"cloud/small": _rec(0.1)}, {"cloud/small": _rec(1.0)}), "base", "head", 1.5
    )
    assert failures == []
    assert "reports only" in lines[-1]


def test_a_head_error_fails_and_a_base_error_is_not_compared() -> None:
    base = {"gamma/small": {"error": "TypeError: x"}, "dwell_times/small": _rec(0.1)}
    head = {"gamma/small": _rec(0.1), "dwell_times/small": {"error": "KeyError: 0"}}
    lines, failures = bench.compare(_result(base, head), "base", "head", 1.5)
    assert failures == ["dwell_times/small: head failed to run (KeyError: 0)"]
    assert any("gamma/small" in line and "not compared" in line for line in lines)


def test_compare_exits_1_on_a_regression_and_writes_the_table(tmp_path: Path) -> None:
    result = tmp_path / "r.json"
    result.write_text(
        json.dumps(_result({"cloud/small": _rec(0.1)}, {"cloud/small": _rec(0.2)})), "utf-8"
    )
    summary = tmp_path / "summary.md"
    assert bench.main(["compare", str(result), "--summary", str(summary)]) == 1
    text = summary.read_text("utf-8")
    assert "| cloud/small | 100.000 ms | 200.000 ms | 2.00 |" in text


def test_noise_spread_and_the_cases_too_noisy_for_the_gate() -> None:
    sides = {
        f"run{i}": {"histogram/small": _rec(0.010 + 0.0001 * i), "cloud/small": _rec(0.1 * i)}
        for i in range(1, 11)
    }
    lines, noisy = bench.noise({"group": "default", "sides": sides})
    assert noisy == ["cloud/small"]
    assert any(line.startswith("| histogram/small | 10 |") and "| yes |" in line for line in lines)


def test_merge_takes_the_minimum_over_rounds_and_keeps_errors_apart() -> None:
    merged = bench._merge([_rec(0.3), {"error": "x"}, _rec(0.2)])
    assert merged["time_s"] == 0.2
    assert merged["rounds_time_s"] == [0.3, 0.2]
    assert bench._merge([{"error": "a"}, {"error": "b"}]) == {"error": "b"}


def test_sides_and_the_noise_copies() -> None:
    assert bench.expand_sides(["base=a/src", "head=b/src"]) == {"base": "a/src", "head": "b/src"}
    assert bench.expand_sides(["run=s"], copies=3) == {"run1": "s", "run2": "s", "run3": "s"}


def test_one_refuses_a_tree_that_tether_was_not_imported_from(tmp_path: Path) -> None:
    # An empty tree: tether then comes from the installed copy, and comparing that copy
    # with itself would pass every time.
    proc = subprocess.run(
        [sys.executable, str(ROOT / "benchmarks" / "bench.py"), "one", "--src", str(tmp_path),
         "--case", "histogram", "--size", "small"],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert proc.returncode == 1
    assert "not " + str(tmp_path.resolve()) in json.loads(proc.stdout)["error"]


@pytest.mark.parametrize("name", [n for n, c in CASES.items() if c.group == "default"])
def test_each_default_case_runs_at_the_small_size(name: str) -> None:
    record = bench.measure(name, "small", repeats=1)
    assert record["time_s"] > 0.0
    assert record["peak_py_bytes"] > 0
    if name == "extraction":
        assert record["tether_bytes"] > 0


@pytest.mark.gui
def test_the_gui_case_runs_offscreen(qapp) -> None:
    pytest.importorskip("pyqtgraph")
    assert bench.measure("trace_display", "small", repeats=1)["time_s"] > 0.0


def test_the_job_runs_only_for_source_changes_and_compares_base_with_head() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text("utf-8"))
    on = workflow[True]  # PyYAML reads the bare `on:` key as True
    assert set(on) == {"pull_request", "workflow_dispatch"}
    assert on["pull_request"] == {"paths": ["src/tether/**"]}
    assert workflow["permissions"] == {"contents": "read"}
    text = WORKFLOW.read_text("utf-8")
    assert 'git merge-base "$BASE_SHA" HEAD' in text
    assert '--side base="$RUNNER_TEMP/base/src" --side head="$head"' in text
    assert 'head="$GITHUB_WORKSPACE/src"' in text
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            uses = step.get("uses", "")
            if uses and not uses.startswith("./"):
                assert len(uses.split("@")[1].split()[0]) == 40, uses


def test_the_job_is_not_a_required_context() -> None:
    release = yaml.safe_load((ROOT / ".github" / "workflows" / "release.yml").read_text("utf-8"))
    assert "benchmarks" not in release["env"]["REQUIRED_CONTEXTS"]
