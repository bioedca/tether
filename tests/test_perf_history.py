# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The speed history of the workstation (#723), on a synthetic history: its format, the report
rule (1.2 times the median of the last 10 runs), the projection and the runner's two checks."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "scripts" / "workstation" / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ph = _load("perf_history")
runner = _load("run_private_checks")
STAGES = ("extract", "correct", "idealize", "analysis", "save", "export")
HARDWARE = {"os": "Windows-11", "machine": "AMD64", "cpu": "cpu", "cpu_count": 16,
            "memory_gb": 31.7, "disk": "ssd", "gpu": "gpu"}  # fmt: skip


def _acquisition(scale: float = 1.0) -> dict:
    values = {}
    for i, stage in enumerate(STAGES, 1):
        values[f"{stage}_s"] = 10.0 * i * scale
        values[f"{stage}_peak_bytes"] = (100 + i) << 20
    return {**values, "tether_bytes": 50 << 20}


def _checks(scale: float = 1.0) -> list[dict]:
    datasets = {nid: _acquisition(scale) for nid in ("lab-11", "lab-12", "lab-13")}
    return [{"check": ph.PIPELINE_CHECK, "outcome": "passed", "duration_s": 1.0,
             "datasets": datasets}]  # fmt: skip


def _bench(scale: float = 1.0) -> list[dict]:
    side = {"extraction/small": {"time_s": 0.5 * scale, "peak_py_bytes": 8 << 20},
            "histogram/large": {"time_s": 0.01, "peak_py_bytes": 1000}}  # fmt: skip
    return [{"group": "default", "sides": {"main": side}}]


def _record(i: int = 0, *, scale: float = 1.0, bench_scale: float = 1.0, **kw) -> dict:
    args = {"commit": f"{i:040x}", "on_main": True, "date": f"2026-11-{i + 1:02d}T03:00:00Z",
            "hardware": HARDWARE, "checks": _checks(scale),
            "bench": _bench(bench_scale)}  # fmt: skip
    return ph.build_record(**{**args, **kw})


def _history(tmp_path: Path, n: int = 10) -> Path:
    path = tmp_path / "perf-history.jsonl"
    for i in range(n):
        ph.append(path, _record(i, scale=1.0 + 0.01 * (i % 3)))
    return path


def test_a_record_matches_the_format_and_holds_every_field_the_issue_names(tmp_path) -> None:
    record = _record()
    assert ph.validate_record(record) == []
    assert set(record["hardware"]) == {
        "os",
        "machine",
        "cpu",
        "cpu_count",
        "memory_gb",
        "disk",
        "gpu",
    }
    assert set(record["acquisitions"]["lab-11"]) == {
        *(f"{s}_s" for s in STAGES),
        *(f"{s}_peak_bytes" for s in STAGES),
        "tether_bytes",
    }
    assert record["benchmarks"]["extraction/small"] == {"time_s": 0.5, "peak_bytes": 8 << 20}
    path = _history(tmp_path, 3)
    assert [json.loads(line)["schema"] for line in path.read_text().splitlines()] == [
        ph.HISTORY_SCHEMA
    ] * 3


@pytest.mark.parametrize(
    "change",
    [
        lambda r: r.pop("projection"),
        lambda r: r.update(extra=1),
        lambda r: r.update(commit="main"),
        lambda r: r.update(date="yesterday"),
        lambda r: r["hardware"].update(host="name"),
        lambda r: r["acquisitions"].update({"movie.tif": {"extract_s": 1.0}}),
        lambda r: r["acquisitions"]["lab-11"].update(extract_s="slow"),
        lambda r: r["acquisitions"]["lab-11"].update(trace=[1.0, 2.0]),
        lambda r: r["benchmarks"].update({"Extraction": {"time_s": 1.0}}),
        lambda r: r["projection"].update(fits_window="yes"),
    ],
)
def test_the_format_check_rejects_what_a_record_may_not_hold(change, tmp_path) -> None:
    record = json.loads(json.dumps(_record()))
    change(record)
    assert ph.validate_record(record)
    with pytest.raises(ValueError, match="format"):
        ph.append(tmp_path / "h.jsonl", record)


def test_a_record_that_names_a_private_file_is_not_written(tmp_path) -> None:
    record = _record()
    record["hardware"]["cpu"] = "acquisition-7"
    with pytest.raises(ValueError, match="private"):
        ph.append(tmp_path / "h.jsonl", record, frozenset({"acquisition-7"}))
    assert not (tmp_path / "h.jsonl").exists()


def test_the_projection_is_100_movies_of_the_median_movie_against_the_window() -> None:
    proj = _record()["projection"]
    assert proj["per_movie_s"] == pytest.approx(10 + 20 + 30)  # extract + correct + idealize
    assert proj["condition_hours"] == pytest.approx(60 * 100 / 3600, abs=1e-3)
    assert (proj["window_hours"], proj["fits_window"]) == (12.0, True)
    assert _record(scale=10.0)["projection"]["fits_window"] is False  # 16.7 h


def test_the_constants_are_those_of_adr_0032() -> None:
    from tether.project import perf

    assert ph.CONDITION_MOVIES == perf.REFERENCE_CONDITION_MOVIES
    assert ph.OVERNIGHT_WINDOW_HOURS == perf.OVERNIGHT_WINDOW_HOURS


def test_a_value_above_1_2_times_the_median_of_the_last_10_is_reported(tmp_path) -> None:
    path = _history(tmp_path, 12)
    assert ph.slowdowns(ph.read_history(path), _record(12, scale=1.15)) == []
    found = ph.slowdowns(ph.read_history(path), _record(12, scale=1.3))
    names = {f["series"] for f in found}
    assert "acquisitions:lab-11:extract_s" in names
    assert not any(n.endswith("peak_bytes") for n in names)  # memory did not change
    assert all(f["ratio"] > ph.LIMIT for f in found)
    bench = ph.slowdowns(ph.read_history(path), _record(12, bench_scale=1.5))
    assert [f["series"] for f in bench] == ["benchmarks:extraction/small:time_s"]


def test_only_the_last_10_runs_of_main_on_the_same_hardware_are_the_baseline(tmp_path) -> None:
    path = tmp_path / "h.jsonl"
    for i in range(5):
        ph.append(path, _record(i, scale=3.0))  # old and slow: outside the last 10
    for i in range(5, 15):
        ph.append(path, _record(i))
    ph.append(path, _record(15, scale=5.0, on_main=False))  # a dry run never counts
    history = ph.read_history(path)
    assert ph.slowdowns(history, _record(16, scale=1.3))
    other = {**HARDWARE, "disk": "hdd"}
    assert ph.slowdowns(history, _record(16, scale=1.3, hardware=other)) == []


def test_too_few_earlier_values_and_tiny_memory_only_record(tmp_path) -> None:
    path = _history(tmp_path, ph.MIN_BASELINE - 1)
    assert ph.slowdowns(ph.read_history(path), _record(9, scale=5.0)) == []
    assert not any(":histogram/large:peak_bytes" in k for k in ph.series(_record()))


def _same_computer(monkeypatch) -> None:
    base = {k: v for k, v in HARDWARE.items() if k not in ("disk", "gpu")}
    monkeypatch.setattr(runner, "_perf_history", lambda: ph)
    monkeypatch.setattr(runner, "hardware", lambda: dict(base))
    monkeypatch.setattr(ph, "disk_type", lambda path: HARDWARE["disk"])
    monkeypatch.setattr(ph, "gpu_name", lambda: HARDWARE["gpu"])


def test_the_runner_gets_two_checks_that_match_the_result_format(tmp_path, monkeypatch) -> None:
    _same_computer(monkeypatch)
    history = _history(tmp_path, 10)
    checks = _checks(scale=1.3)
    added = runner.history_checks(
        history, commit="a" * 40, on_main=True, finished=0.0, checks=checks,
        bench=_bench(), data=tmp_path, tokens=frozenset(),
    )  # fmt: skip
    by_name = {c["check"]: c for c in added}
    assert by_name[ph.CHECK_ACQUISITIONS]["outcome"] == "failed"
    assert by_name[ph.CHECK_ACQUISITIONS]["datasets"]["lab-12"]["extract_s_ratio"] > ph.LIMIT
    assert by_name[ph.CHECK_BENCHMARKS]["outcome"] == "passed"
    assert len(ph.read_history(history)) == 11
    result = {"schema": runner.RESULT_SCHEMA, "mode": "scheduled", "status": "failed",
              "commit": "a" * 40, "on_main": True, "started": "2026-11-13T03:00:00Z",
              "finished": "2026-11-13T05:00:00Z", "duration_s": 1.0, "lock_sha256": None,
              "tools": {}, "hardware": runner.hardware(), "checks": checks + added}  # fmt: skip
    assert runner.validate_result(result) == []
    titles = [i["title"] for i in runner.latest(_write_result(tmp_path, result))["open_or_update"]]
    assert titles == [runner.issue_title(ph.CHECK_ACQUISITIONS)]


@pytest.mark.skipif(sys.platform == "win32", reason="Windows keeps the inherited ACL (#787)")
def test_the_runner_writes_a_new_history_owner_only_whatever_the_umask(tmp_path) -> None:
    """#787: files of the state folder are owner-only on POSIX; the history is one of them."""
    import os
    import stat

    history = tmp_path / "perf-history.jsonl"
    old = os.umask(0)
    try:
        runner.history_checks(
            history, commit="a" * 40, on_main=True, finished=0.0, checks=_checks(),
            bench=_bench(), data=tmp_path, tokens=frozenset(),
        )  # fmt: skip
    finally:
        os.umask(old)
    assert stat.S_IMODE(history.stat().st_mode) & runner.GROUP_OTHER == 0
    assert len(ph.read_history(history)) == 1


def _write_result(tmp_path: Path, result: dict) -> Path:
    results = tmp_path / "results"
    results.mkdir()
    (results / "20261113T050000Z-aaaaaaaaaaaa.json").write_text(json.dumps(result))
    return results


def test_no_acquisitions_or_no_benchmarks_fail_their_check(tmp_path) -> None:
    record = _record(checks=[], bench=[])
    assert record["projection"] is None
    assert [c["outcome"] for c in ph.checks_for(record, [])] == ["failed", "failed"]


def test_a_broken_history_is_an_error_check_and_is_not_overwritten(tmp_path) -> None:
    history = tmp_path / "h.jsonl"
    history.write_text("not json\n")
    added = runner.history_checks(
        history, commit=None, on_main=False, finished=0.0, checks=_checks(), bench=_bench(),
        data=tmp_path, tokens=frozenset(),
    )  # fmt: skip
    assert {c["outcome"] for c in added} == {"error"}
    assert history.read_text() == "not json\n"


def test_speed_in_a_dry_run_needs_a_history_copy() -> None:
    with pytest.raises(SystemExit):
        runner.main(["run", "--dry-run", "--speed", "--data", "x", "--state", "y"])


def test_the_plot_script_draws_a_synthetic_history(tmp_path) -> None:
    pytest.importorskip("matplotlib")
    plot = _load("plot_perf_history")
    out = plot.plot(_history(tmp_path, 4), tmp_path / "history.png")
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_the_hardware_probes_answer_or_say_nothing(tmp_path) -> None:
    assert ph.disk_type(tmp_path) in (None, "ssd", "hdd")
    gpu = ph.gpu_name()
    assert gpu is None or (isinstance(gpu, str) and gpu)


def test_a_benchmark_case_or_group_that_cannot_run_fails_the_benchmark_check() -> None:
    bench = _bench()
    bench[0]["sides"]["main"]["gamma/small"] = {"error": "ValueError: boom"}
    bench.append({"group": "gui", "error": "exit 1"})
    record = _record(bench=bench)
    assert record["benchmark_errors"] == ["gamma/small", "gui/run"]
    assert ph.validate_record(record) == []
    by_name = {c["check"]: c["outcome"] for c in ph.checks_for(record, [])}
    assert by_name[ph.CHECK_BENCHMARKS] == "failed"
    assert by_name[ph.CHECK_ACQUISITIONS] == "passed"
    record["benchmark_errors"] = ["movie.tif"]
    assert ph.validate_record(record)


def test_the_baseline_is_the_last_10_records_not_the_last_10_values(tmp_path) -> None:
    """Codex on #878: three old values of an id must not stay its baseline for ever."""
    path = tmp_path / "h.jsonl"
    for i in range(3):
        ph.append(path, _record(i))
    for i in range(3, 13):  # ten records in which lab-11 has no values
        record = _record(i)
        del record["acquisitions"]["lab-11"]
        ph.append(path, record)
    found = ph.slowdowns(ph.read_history(path), _record(13, scale=1.5))
    assert not any(":lab-11:" in f["series"] for f in found)
    assert any(":lab-12:" in f["series"] for f in found)


def test_a_stage_imports_before_its_timer_starts() -> None:
    """Codex on #878: the timed call holds the operation only, not the cold imports."""
    import argparse

    stage = _load("perf_stage")
    args = argparse.Namespace(store="none.tether", out="none.csv", movie=None, tmap=None, tdat=None)
    call = stage._stage("export", args)  # imports Tether; runs nothing yet
    assert callable(call)
    assert "tether.project.export" in sys.modules
