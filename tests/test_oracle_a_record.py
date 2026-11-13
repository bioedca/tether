# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The newest recorded run of oracle (a) and the line of ``docs/validation.md`` that states it.

Oracle (a) runs on the maintainer's workstation and on no CI runner (ADR-0071, #699), so a CI
run cannot say when it last ran or what it measured. The committed record
``docs/validation/oracle-a-last-run.json`` says it: one check entry of a workstation result file
(``scripts/workstation/run_private_checks.py``), with the commit and the time of its run. This
module keeps the record well formed, keeps its outcome in step with its own numbers and the
frozen ADR-0022 gate, and keeps the page's *Newest recorded run* paragraph in step with it. The
record holds only the neutral id and summary numbers, never a file name.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from _oracle_a import ORACLE_A
from tether.project.oracle import PEARSON_THRESHOLD, RECALL_THRESHOLD

ROOT = Path(__file__).resolve().parents[1]
RECORD_REL = "validation/oracle-a-last-run.json"
RECORD = ROOT / "docs" / RECORD_REL
PAGE = ROOT / "docs" / "validation.md"
CHECK = "tests/test_oracle.py::test_extraction_meets_m1_acceptance_on_uckopsb"
MODES = {"scheduled": "a scheduled run", "by-hand": "a run by hand"}
KEYS = {"schema", "mode", "commit", "on_main", "finished", "result_file", "check"}
METRICS = {
    "matched_fraction",
    "position_rms_px",
    "donor_pearson_median",
    "acceptor_pearson_median",
    "precision",
    "n_ground_truth",
    "n_extracted",
    "n_matched",
}
LEAD = "**Newest recorded run.**"


def _record() -> dict:
    return json.loads(RECORD.read_text(encoding="utf-8"))


def _paragraphs_of_a() -> list[str]:
    text = PAGE.read_text(encoding="utf-8")
    section = text.split("\n## (a) ", 1)[1].split("\n## ", 1)[0]
    return [" ".join(p.split()) for p in section.split("\n\n")]


def test_the_record_is_one_check_entry_of_a_workstation_result() -> None:
    record = _record()
    assert set(record) == KEYS
    assert record["schema"] == "tether-oracle-a-record/1"
    assert record["mode"] in MODES
    assert re.fullmatch(r"[0-9a-f]{40}", record["commit"])
    # Only the runner verifies that a commit is on main, and it runs nothing else.
    assert record["on_main"] is (record["mode"] == "scheduled")
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", record["finished"])
    if record["mode"] == "scheduled":  # the runner's own file name: <UTC stamp>-<commit>.json
        assert re.fullmatch(
            rf"\d{{8}}T\d{{6}}Z-{record['commit'][:12]}\.json", record["result_file"]
        )
    else:
        assert record["result_file"] is None
    check = record["check"]
    assert set(check) == {"check", "outcome", "duration_s", "datasets"}
    assert check["check"] == CHECK
    assert check["outcome"] in {"passed", "failed", "skipped", "error"}
    assert list(check["datasets"]) == [ORACLE_A], "the record names the neutral id only"
    assert set(check["datasets"][ORACLE_A]) <= METRICS


def test_the_recorded_check_is_the_workstation_oracle_test() -> None:
    path, name = CHECK.split("::")
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    tests = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    assert name in tests, f"{CHECK} no longer exists"
    marks = {ast.unparse(d) for d in tests[name].decorator_list}
    assert marks == {"pytest.mark.workstation"}, marks


def test_the_outcome_agrees_with_the_numbers_and_the_frozen_gate() -> None:
    check = _record()["check"]
    numbers = check["datasets"][ORACLE_A]
    if check["outcome"] != "passed":
        pytest.skip("the newest recorded run did not pass; its numbers may be incomplete")
    assert set(numbers) | {"position_rms_px", "precision"} >= METRICS
    assert numbers["matched_fraction"] >= RECALL_THRESHOLD
    assert numbers["donor_pearson_median"] >= PEARSON_THRESHOLD
    assert numbers["n_matched"] <= min(numbers["n_ground_truth"], numbers["n_extracted"])
    assert numbers["matched_fraction"] == pytest.approx(
        numbers["n_matched"] / numbers["n_ground_truth"]
    )


def test_the_page_states_the_newest_recorded_run() -> None:
    record = _record()
    paragraphs = [p for p in _paragraphs_of_a() if p.startswith(LEAD)]
    assert len(paragraphs) == 1, f"oracle (a) needs exactly one paragraph that opens {LEAD}"
    line = paragraphs[0]
    commit = record["commit"]
    stated = re.findall(r"`([0-9a-f]{7,40})`", line)
    assert len(stated) == 1 and commit.startswith(stated[0]), (
        f"the paragraph must name the commit {commit[:7]} once, in backticks: {stated}"
    )
    assert f"on {record['finished'][:10]}" in line
    assert MODES[record["mode"]] in line
    assert f"**{record['check']['outcome']}**" in line
    assert f"]({RECORD_REL})" in line, "the paragraph must link the record"
    assert "on the maintainer's workstation" in line.lower()
