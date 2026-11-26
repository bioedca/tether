# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The evidence records of the validation suite and the release set that binds them (#1439).

``scripts/validation_evidence.py`` writes one record per oracle (a)–(g) and one for the deep
add-on run (PRD §9.5). These tests keep the format, the tolerance each record may name and the
release-set rule: every record names one commit and every gate oracle passed, (c) excepted.
A release set exists only for a release, so no set is required here; every set that is
committed under ``docs/validation/release/`` is checked in full (ADR-0155).
"""

from __future__ import annotations

import copy
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validation_evidence.py"
_spec = importlib.util.spec_from_file_location("validation_evidence", SCRIPT)
assert _spec is not None and _spec.loader is not None
ve = importlib.util.module_from_spec(_spec)
sys.modules["validation_evidence"] = ve
_spec.loader.exec_module(ve)

COMMIT = "0123456789abcdef0123456789abcdef01234567"
OTHER = "fedcba9876543210fedcba9876543210fedcba98"


def _record(oracle: str, commit: str = COMMIT, outcome: str = "passed") -> dict:
    measured: dict[str, float | int] = {"metric": 0.97}
    if oracle == "deep":
        measured = {"cuda_tests_executed": 4, "cuda_tests_skipped": 0}
    return {
        "schema": ve.SCHEMA,
        "oracle": oracle,
        "tier": ve.ORACLES[oracle]["tier"],
        "commit": commit,
        "evidence": {"run_url": "https://github.com/bioedca/tether/actions/runs/123456"},
        "measured": measured,
        "tolerance": sorted(ve.ORACLES[oracle]["tolerance"])[:1],
        "outcome": outcome,
    }


def _write_set(directory: Path, records: dict[str, dict]) -> Path:
    directory.mkdir(parents=True)
    for oracle, record in records.items():
        (directory / ve.file_name(oracle)).write_text(json.dumps(record), encoding="utf-8")
    return directory


def _complete() -> dict[str, dict]:
    return {oracle: _record(oracle) for oracle in ve.ORACLES}


def _tunable_rows() -> list[str]:
    text = (ROOT / "docs" / "PRD.md").read_text(encoding="utf-8")
    table = text.split("\n### 11.2 ", 1)[1].split("\n### ", 1)[0]
    rows = [line.split("|")[1].strip() for line in table.splitlines() if line.startswith("| ")]
    return [row.replace("**", "") for row in rows]


def test_the_suite_is_the_seven_oracles_and_the_deep_run() -> None:
    assert set(ve.ORACLES) == {"a", "b", "c", "d", "e", "f", "g", "deep"}
    assert {"c"} == ve.ADVISORY


def test_each_oracle_names_a_tunable_row_or_a_frozen_file() -> None:
    rows = _tunable_rows()
    for oracle, spec in ve.ORACLES.items():
        assert spec["tolerance"], oracle
        for name in spec["tolerance"]:
            if "/" in name:
                assert (ROOT / name).is_file(), f"({oracle}) names a missing file {name}"
            else:
                assert any(row.startswith(name) for row in rows), (
                    f"({oracle}): no §11.2 row {name!r}"
                )


def test_each_tier_is_the_tier_the_validation_page_states() -> None:
    page = (ROOT / "docs" / "validation.md").read_text(encoding="utf-8")
    section = page.split("\n## The validation suite", 1)[1].split("\n## ", 1)[0]
    words = {"workstation": "`workstation`", "sidecar": "`sidecar`", "large": "`large`"}
    words["required"] = "required matrix"
    for oracle, spec in ve.ORACLES.items():
        if oracle == "deep":
            continue
        row = next(line for line in section.splitlines() if line.startswith(f"| ({oracle}) "))
        tier_cell = row.split("|")[2]
        assert tier_cell.strip().startswith(words[str(spec["tier"])]), (oracle, tier_cell)


@pytest.mark.parametrize("oracle", sorted(ve.ORACLES))
def test_a_well_formed_record_has_no_errors(oracle: str) -> None:
    assert ve.record_errors(_record(oracle)) == []


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda r: r.update(tolerance=[]), "tolerance must name"),
        (lambda r: r.update(tolerance=["Ranker success target"]), "does not compare with"),
        (lambda r: r.update(tier="required"), "runs in tier"),
        (lambda r: r.update(commit="abc1234"), "40-character"),
        (lambda r: r.update(outcome="advisory"), "outcome must be"),
        (lambda r: r.update(evidence={"run_url": "https://example.org/run/1"}), "run URL"),
        (lambda r: r.update(evidence={"log_sha256": "0" * 64, "run_url": "x"}), "exactly one"),
        (lambda r: r.update(evidence={"log_path": "C:/Users/lab/run.log"}), "run URL"),
        (lambda r: r.update(measured={"movie": "lab-file.tif"}), "not a number"),
        (lambda r: r.update(measured={"Path C:/x": 1.0}), "snake_case"),
        (lambda r: r.update(measured={"recall": float("nan")}), "not finite"),
        (lambda r: r.update(measured={}), "non-empty"),
        (lambda r: r.update(host="lab-pc"), "keys must be exactly"),
    ],
)
def test_a_record_that_breaks_the_format_is_refused(change, message: str) -> None:
    record = _record("b")
    change(record)
    errors = ve.record_errors(record)
    assert any(message in e for e in errors), errors


def test_the_deep_record_must_show_its_cuda_tests_executed() -> None:
    record = _record("deep")
    record["measured"] = {"cuda_tests_executed": 0, "cuda_tests_skipped": 4}
    assert any("executed, not skipped" in e for e in ve.record_errors(record))
    record["measured"] = {"tests_passed": 40}
    assert any("must give" in e for e in ve.record_errors(record))


def test_a_complete_set_on_one_commit_passes(tmp_path: Path) -> None:
    assert ve.set_errors(_write_set(tmp_path / "v9.9.9", _complete())) == []


def test_a_set_with_one_record_from_another_commit_fails(tmp_path: Path) -> None:
    records = _complete()
    records["e"] = _record("e", commit=OTHER)
    errors = ve.set_errors(_write_set(tmp_path / "v9.9.9", records))
    assert any("more than one commit" in e for e in errors), errors


def test_only_oracle_c_may_be_recorded_advisory(tmp_path: Path) -> None:
    records = _complete()
    records["c"] = _record("c", outcome="failed")
    assert ve.set_errors(_write_set(tmp_path / "c", records)) == []
    for oracle in sorted(set(ve.ORACLES) - {"c"}):
        failed = copy.deepcopy(records)
        failed[oracle] = _record(oracle, outcome="failed")
        errors = ve.set_errors(_write_set(tmp_path / oracle, failed))
        assert any("only (c) may be advisory" in e for e in errors), (oracle, errors)


def test_a_set_missing_a_record_or_naming_the_wrong_oracle_fails(tmp_path: Path) -> None:
    records = _complete()
    del records["g"]
    assert "missing oracle-g.json" in ve.set_errors(_write_set(tmp_path / "missing", records))
    records = _complete()
    records["d"] = _record("e")
    errors = ve.set_errors(_write_set(tmp_path / "swapped", records))
    assert any(e.startswith("oracle-d.json: names oracle") for e in errors), errors


def test_the_oracle_a_record_is_made_from_the_699_record(tmp_path: Path) -> None:
    record_a = json.loads((ROOT / "docs/validation/oracle-a-last-run.json").read_text("utf-8"))
    result = tmp_path / record_a["result_file"]
    result.write_text('{"stand-in": "workstation result file"}', encoding="utf-8")
    made = ve.from_oracle_a(record_a, result)
    assert ve.record_errors(made) == []
    assert made["commit"] == record_a["commit"]
    assert made["evidence"] == {"log_sha256": ve._sha256(result)}
    assert made["measured"] == record_a["check"]["datasets"]["lab-01"]
    with pytest.raises(SystemExit, match="the record names"):
        ve.from_oracle_a(record_a, tmp_path / "other.json")


def test_the_record_command_writes_a_record_that_checks(tmp_path: Path) -> None:
    out = tmp_path / "oracle-g.json"
    argv = ["record", "--oracle", "g", "--commit", COMMIT, "--log", str(SCRIPT)]
    argv += ["--measured", "max_frame_error=1", "--tolerance", "Bleach-frame tolerance"]
    assert ve.main([*argv, "--outcome", "passed", "--out", str(out)]) == 0
    record = json.loads(out.read_text(encoding="utf-8"))
    assert ve.record_errors(record) == []
    assert record["measured"] == {"max_frame_error": 1}


def test_check_release_is_advisory_unless_required(capsys: pytest.CaptureFixture[str]) -> None:
    assert ve.main(["check-release", "--tag", "v0.0.0-none", "--required", "false"]) == 0
    assert ve.main(["check-release", "--tag", "v0.0.0-none", "--required", "true"]) == 1
    assert "::error::" in capsys.readouterr().out


def test_every_committed_release_set_is_complete_and_names_one_commit() -> None:
    base = ROOT / ve.RELEASE_DIR
    sets = sorted(p for p in base.iterdir() if p.is_dir()) if base.is_dir() else []
    for directory in sets:
        assert re.fullmatch(ve.TAG, directory.name), directory.name
        assert ve.set_errors(directory) == [], directory.name


def test_release_yml_runs_the_gate_for_a_stable_publishing_tag() -> None:
    text = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "scripts/validation_evidence.py check-release" in text
    assert (
        "steps.resolve.outputs.prerelease"
        in text.split("check-release", 1)[0].rsplit("- name:", 1)[1]
    )


def test_a_directory_in_place_of_a_record_fails(tmp_path: Path) -> None:
    records = _complete()
    del records["deep"]
    directory = _write_set(tmp_path / "v9.9.9", records)
    (directory / "deep.json").mkdir()
    (directory / "deep.json" / "placeholder").write_text("{}", encoding="utf-8")
    assert "deep.json: not a file" in ve.set_errors(directory)


def _git(repo: Path, *args: str) -> str:
    who = ["-c", "user.name=t", "-c", "user.email=t@example.org", "-c", "commit.gpgsign=false"]
    done = subprocess.run(
        ["git", *who, *args], cwd=repo, check=True, capture_output=True, text=True
    )
    return done.stdout.strip()


def test_the_release_gate_reads_the_set_from_the_tagged_tree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "README").write_text("release commit\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "release commit")
    release = _git(repo, "rev-parse", "HEAD")
    records = {oracle: _record(oracle, commit=release) for oracle in ve.ORACLES}
    # The tag commit carries no set; one written into the checkout afterwards must not count.
    _git(repo, "commit", "-q", "--allow-empty", "-m", "tag commit without its set")
    bare_tag = _git(repo, "rev-parse", "HEAD")
    _write_set(repo / ve.RELEASE_DIR / "v9.9.9", records)
    assert any(
        "no release set in the tag commit" in e for e in ve.binding_errors("v9.9.9", bare_tag, repo)
    )
    # Committed, the same set binds the commit that adds it, and nothing else.
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "the release set")
    assert ve.binding_errors("v9.9.9", _git(repo, "rev-parse", "HEAD"), repo) == []
    (repo / "README").write_text("a later change\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "another change")
    errors = ve.binding_errors("v9.9.9", _git(repo, "rev-parse", "HEAD"), repo)
    assert any("changes more than" in e for e in errors), errors
