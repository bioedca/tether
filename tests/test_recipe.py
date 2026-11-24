# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Recipe files, ``tether run`` and ``tether approve`` (#899, ADR-0100 §6, ADR-0102)."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import shlex
import shutil
import sys
from pathlib import Path
from typing import ClassVar

import h5py
import numpy as np
import pytest

from tether.cli import EXIT_APPROVAL_NEEDED, main
from tether.project import approvals
from tether.project.core import Project
from tether.project.jobs import JobState, read_jobs
from tether.project.lock import user_lock_dir
from tether.services import ExtractRequest, IdealizeRequest, registry
from tether.services import recipe as rc
from tether.services.base import Request

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_extract_cli import _make_movie  # noqa: E402, PLC2701 - the synthetic movie
from test_map_file import _write_beads  # noqa: E402, PLC2701 - the synthetic bead image


def _recipe(*steps: dict) -> str:
    return json.dumps({"format": "tether-recipe", "version": 1, "steps": list(steps)})


def _step(command: str = "extract", **fields: object) -> dict:
    if command == "extract" and not fields:
        fields = {"movie": "m.tif", "output": "m.tether"}
    return {"command": command, "version": 1, "fields": fields}


def _write(folder: Path, text: str, name: str = "recipe.json") -> Path:
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


# --- the file ---------------------------------------------------------------------------------


def test_a_relative_path_is_read_from_the_recipe_folder(tmp_path) -> None:
    path = _write(tmp_path, _recipe(_step(), _step("idealize", project="m.tether")))
    recipe = rc.read_recipe(path)
    extract, idealize = (s.request for s in recipe.steps)
    assert extract == ExtractRequest(
        movie=str(tmp_path / "m.tif"), output=str(tmp_path / "m.tether")
    )
    assert idealize == IdealizeRequest(project=str(tmp_path / "m.tether"))
    assert [s.number for s in recipe.steps] == [1, 2]
    assert recipe.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


def test_a_written_recipe_holds_paths_relative_to_its_folder_and_reads_back(tmp_path) -> None:
    requests = [
        ExtractRequest(movie=str(tmp_path / "in" / "m.tif"), output=str(tmp_path / "m.tether"))
    ]
    path = rc.write_recipe(tmp_path / "saved.json", requests)
    fields = json.loads(path.read_text(encoding="utf-8"))["steps"][0]["fields"]
    assert fields["movie"] == "in/m.tif" and fields["output"] == "m.tether"
    assert [s.request for s in rc.read_recipe(path).steps] == requests


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("not json", "not valid JSON"),
        ("[]", "must be a JSON object"),
        ('{"format": "other", "version": 1, "steps": []}', "not a recipe"),
        ('{"format": "tether-recipe", "version": 2, "steps": [{}]}', "version 2 is not supported"),
        ('{"format": "tether-recipe", "version": 1, "steps": []}', "non-empty 'steps'"),
        ('{"format": "tether-recipe", "version": 1, "steps": [{}], "x": 1}', "unknown recipe key"),
        (_recipe(_step("frobnicate")), "step 1: unknown command 'frobnicate'"),
        (_recipe(_step(), _step("extract", movie="m.tif")), "step 2: missing field 'output'"),
        (_recipe(_step("extract", movie=1, output="o")), "step 1: field 'movie' must be str"),
    ],
)
def test_an_invalid_recipe_is_refused_with_the_step_and_the_reason(tmp_path, text, message):
    with pytest.raises(rc.RecipeError, match=re.escape(message)):
        rc.read_recipe(_write(tmp_path, text))


@pytest.mark.parametrize("command", ["publish", "deposit-publish"])
def test_a_recipe_with_a_publish_step_is_refused(tmp_path, capsys, command) -> None:
    path = _write(tmp_path, _recipe(_step(), {"command": command, "version": 1, "fields": {}}))
    assert main(["run", str(path)]) == 2
    err = capsys.readouterr().err
    assert "step 2: a recipe can never publish (card B3)" in err
    assert not (tmp_path / "m.tether").exists()


def test_a_recipe_cannot_approve_for_a_person(tmp_path) -> None:
    step = _step("approve", project="p.tether", step="gamma.approve", subject="c", report="r")
    with pytest.raises(rc.RecipeError, match="cannot approve for a person"):
        rc.read_recipe(_write(tmp_path, _recipe(step)))


def test_a_recipe_holds_no_account_secret(tmp_path) -> None:
    step = _step("extract", movie="m.tif", output="o.tether", api_token="x")
    with pytest.raises(rc.RecipeError, match="no account secret; field 'api_token'"):
        rc.read_recipe(_write(tmp_path, _recipe(step)))
    # No registered request has such a field, so a recipe written by Tether has none.
    for entry in registry.COMMANDS:
        for f in dataclasses.fields(entry.request_type):
            assert not any(word in f.name for word in rc._SECRET_WORDS), f.name


def test_a_policy_for_a_step_the_command_does_not_reach_is_refused(tmp_path) -> None:
    policy = {"step": "fit.select", "rule": "model", "family": "vbconhmm", "n_states": 2}
    step = {**_step("idealize", project="m.tether"), "policy": {**policy, "otherwise": "stop"}}
    with pytest.raises(rc.RecipeError, match="reaches no approval step"):
        rc.read_recipe(_write(tmp_path, _recipe(step)))


def test_the_approval_steps_of_the_recipe_and_of_the_store_are_the_same() -> None:
    assert rc.APPROVAL_STEPS == approvals.APPROVAL_STEPS


# --- policies (ADR-0102 §3) -------------------------------------------------------------------

_EXAMPLES = [
    {
        "step": "map.accept",
        "rule": "rms-gate",
        "gate_px": 0.5,
        "no_residual": "stop",
        "otherwise": "flag",
    },
    {
        "step": "alpha.confirm",
        "rule": "value",
        "alpha": 0.07,
        "source": "donor-only sample of 2026-10-02, donly.tether",
        "otherwise": "stop",
    },
    {
        "step": "gamma.approve",
        "rule": "estimate",
        "min_qualifying_traces": 10,
        "range": [0.5, 2.0],
        "otherwise": "stop",
    },
    {
        "step": "fit.select",
        "rule": "best-score",
        "family": "vbconhmm",
        "score": "elbo",
        "otherwise": "stop",
    },
]


@pytest.mark.parametrize("data", _EXAMPLES, ids=lambda d: d["step"])
def test_the_adr_examples_are_valid_policies(data) -> None:
    policy = rc.check_policy(data)
    assert policy.step == data["step"] and policy.otherwise == data["otherwise"]
    assert json.loads(policy.text) == data


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"gate_px": None}, "missing: gate_px"),
        ({"extra": 1}, "unknown key(s): extra"),
        ({"gate_px": -1}, "'gate_px' must be a number above 0"),
        ({"no_residual": "maybe"}, "'no_residual' must be 'approve' or 'stop'"),
        ({"otherwise": "apparent"}, "'otherwise' must be one of flag, fail, stop"),
        ({"rule": "estimate"}, "has no rule 'estimate'"),
        ({"step": "deposit.publish"}, "unknown approval step"),
    ],
)
def test_a_policy_states_every_value_or_is_refused(change, message) -> None:
    data = {**_EXAMPLES[0], **change}
    data = {k: v for k, v in data.items() if v is not None}
    with pytest.raises(rc.RecipeError, match=re.escape(message)):
        rc.check_policy(data)


def test_best_score_cannot_rank_a_family_that_has_no_score() -> None:
    with pytest.raises(rc.RecipeError, match="no score"):
        rc.check_policy({**_EXAMPLES[3], "family": "kmeans"})


def test_a_gamma_range_is_two_increasing_positive_numbers() -> None:
    for bad in ([2.0, 0.5], [0.5], [0, 1.0], "0.5-2"):
        with pytest.raises(rc.RecipeError, match="'range' must be"):
            rc.check_policy({**_EXAMPLES[2], "range": bad})


# --- running a recipe -------------------------------------------------------------------------


def test_a_dry_run_prints_the_plan_and_changes_nothing(tmp_path, capsys) -> None:
    _make_movie(tmp_path, name="m.tif")
    path = _write(tmp_path, _recipe(_step(), _step("idealize", project="m.tether")))
    before = sorted(p.name for p in tmp_path.iterdir())
    assert main(["run", str(path), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "step 1/2: tether extract" in out and "step 2/2: tether idealize" in out
    assert f"  movie: {tmp_path / 'm.tif'}" in out
    assert f"  job stored in: {tmp_path / 'm.tether'}" in out
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_tether_run_runs_each_step_as_a_stored_job_and_resume_skips_it(tmp_path, capsys):
    _make_movie(tmp_path, name="m.tif")
    path = _write(tmp_path, _recipe(_step()))
    assert main(["run", str(path)]) == 0
    assert "step 1/1 extract: done" in capsys.readouterr().out
    recipe = rc.read_recipe(path)
    (job,) = read_jobs(tmp_path / "m.tether")
    assert job.command == "extract" and job.state is JobState.DONE
    part = json.loads(job.parts[0])
    checkpoint = json.loads(rc._checkpoint_path(recipe).read_text(encoding="utf-8"))
    assert part == {
        "recipe_sha256": recipe.sha256,
        "recipe_version": 1,
        "step": 1,
        "run_id": checkpoint["run_id"],
        "attempt_id": part["attempt_id"],
    }
    assert part["run_id"] and part["attempt_id"]
    assert main(["run", str(path), "--resume"]) == 0
    assert "step 1/1 extract: skipped" in capsys.readouterr().out
    assert len(read_jobs(tmp_path / "m.tether")) == 1


def test_count_only_recipe_step_leaves_an_existing_project_untouched(tmp_path, capsys):
    _make_movie(tmp_path, name="m.tif")
    project = tmp_path / "m.tether"
    Project.create(project)
    original = project.read_bytes()
    step = _step(movie="m.tif", output="m.tether", count_only=True)
    path = _write(tmp_path, _recipe(step))

    assert main(["run", str(path)]) == 0
    assert "step 1/1 extract: done" in capsys.readouterr().out
    assert project.read_bytes() == original
    assert read_jobs(project) == []

    assert main(["run", str(path), "--dry-run", "--resume"]) == 0
    plan = capsys.readouterr().out
    assert "tether extract (done; skipped by --resume)" in plan
    assert "job stored in: (no project; not stored)" in plan


def test_resume_with_no_completed_step_runs_from_the_first_step(tmp_path, capsys) -> None:
    # A changed recipe is a new recipe, and a first step that stores no job leaves nothing
    # to find: --resume then starts again from the first step.
    _make_movie(tmp_path, name="m.tif")
    path = _write(tmp_path, _recipe(_step()))
    assert main(["run", str(path), "--resume"]) == 0
    assert "step 1/1 extract: done" in capsys.readouterr().out


def test_a_failed_step_stops_the_run_with_exit_1(tmp_path, capsys) -> None:
    path = _write(tmp_path, _recipe(_step(), _step("idealize", project="m.tether")))
    assert main(["run", str(path)]) == 1  # there is no movie
    captured = capsys.readouterr()
    assert "step 1/2 extract: failed" in captured.out and "step 2/2" not in captured.out
    assert "--resume" in captured.err


def test_resume_skips_a_completed_map_before_a_project_exists(tmp_path, capsys) -> None:
    _write_beads(tmp_path / "beads.tif")
    map_step = _step("map", source="beads.tif", output="beads.npz")
    path = _write(tmp_path, _recipe(map_step, _step()))
    output = tmp_path / "beads.npz"
    project = tmp_path / "m.tether"

    assert main(["run", str(path)]) == 1  # there is no movie yet
    assert "step 1/2 map: done" in capsys.readouterr().out
    assert output.is_file() and not project.exists()
    written = output.read_bytes()

    assert main(["run", str(path), "--dry-run", "--resume"]) == 0
    plan = capsys.readouterr().out
    assert "tether map (done; skipped by --resume)" in plan
    assert not project.exists()

    _make_movie(tmp_path, name="m.tif")
    assert main(["run", str(path), "--resume"]) == 0
    assert "step 1/2 map: skipped" in capsys.readouterr().out
    assert output.read_bytes() == written
    assert project.is_file()


@pytest.mark.parametrize("destination", ["m.tether", "nested/../m.tether"])
def test_resume_skips_completed_steps_when_a_later_step_replaced_the_project(
    tmp_path, capsys, destination
):
    _make_movie(tmp_path, name="m.tif")
    _make_movie(tmp_path, name="second.tif")
    (tmp_path / "nested").mkdir()
    replace = _step("extract", movie="second.tif", output=destination, overwrite=True)
    path = _write(tmp_path, _recipe(_step(), replace))
    assert main(["run", str(path)]) == 0
    capsys.readouterr()

    (job,) = read_jobs(tmp_path / "m.tether")
    assert json.loads(job.parts[0])["step"] == 2
    assert main(["run", str(path), "--resume"]) == 0
    output = capsys.readouterr().out
    assert "step 1/2 extract: skipped" in output and "step 2/2 extract: skipped" in output


# --- approval steps (a test command that reaches gamma.approve) ---------------------------------


@dataclasses.dataclass(frozen=True, kw_only=True)
class _CalibrateRequest(Request):
    COMMAND: ClassVar[str] = "calibrate"
    PATH_FIELDS: ClassVar[tuple[str, ...]] = ("project", "report")
    PROJECT_FIELD: ClassVar[str | None] = "project"

    project: str
    report: str


@dataclasses.dataclass(frozen=True)
class _CalibrateResult:
    approved: bool


def _calibrate(request: _CalibrateRequest, *, approvals) -> _CalibrateResult:  # noqa: ANN001
    # What a command of #930 does: ask the gate before it writes what needs the approval.
    policy = approvals.decide(
        "gamma.approve", "cond-1", project=request.project, report=request.report
    )
    if policy is not None:
        project = Project.open(request.project)
        project.acquire_lock()
        try:
            approvals.record(project, step="gamma.approve", subject="cond-1", report=request.report)
        finally:
            project.release_lock()
    return _CalibrateResult(approved=True)


@pytest.fixture
def calibrate(monkeypatch, tmp_path):
    entry = registry.ServiceCommand(
        name="calibrate",
        request_type=_CalibrateRequest,
        result_type=_CalibrateResult,
        run=_calibrate,
        summary="a test command that reaches gamma.approve",
        approval_steps=("gamma.approve",),
    )
    monkeypatch.setattr(registry, "COMMANDS", (*registry.COMMANDS, entry))
    Project.create(tmp_path / "exp.tether")
    report = tmp_path / "gamma.json"
    report.write_text('{"gamma": 1.1}', encoding="utf-8")
    (tmp_path / "gamma.png").write_bytes(b"\x89PNG not really")
    return report


def _calibrate_step(**policy: object) -> dict:
    step = _step("calibrate", project="exp.tether", report="gamma.json")
    return {**step, "policy": policy} if policy else step


def test_an_approval_step_with_no_policy_stops_with_exit_4_and_resume_goes_on(
    tmp_path, capsys, calibrate
):
    path = _write(tmp_path, _recipe(_calibrate_step()))
    project = tmp_path / "exp.tether"
    assert main(["run", str(path)]) == EXIT_APPROVAL_NEEDED
    err = capsys.readouterr().err
    assert "stopped at approval step 'gamma.approve' for 'cond-1'" in err
    assert f"  evidence: {calibrate}" in err
    assert f"  approve:  tether approve {project} gamma.approve --subject cond-1" in err
    assert f"  continue: tether run {path} --resume" in err
    (job,) = read_jobs(project)
    assert job.state is JobState.CANCELLED and approvals.read_approvals(project) == []
    # A second run without an approval stops again, on the same job.
    assert main(["run", str(path), "--resume"]) == EXIT_APPROVAL_NEEDED
    assert len(read_jobs(project)) == 1

    approve = ["approve", str(project), "gamma.approve", "--subject", "cond-1"]
    assert main([*approve, "--report", str(calibrate), "--by", "Ana"]) == 0
    (approval,) = approvals.read_approvals(project)
    recipe = rc.read_recipe(path)
    assert (approval.by, approval.actor) == ("person", "Ana")
    assert (approval.recipe_sha256, approval.recipe_version) == (recipe.sha256, 1)
    assert set(approval.report) == {"gamma.json", "gamma.png"}
    assert approval.report["gamma.json"] == hashlib.sha256(calibrate.read_bytes()).hexdigest()

    capsys.readouterr()
    assert main(["run", str(path), "--resume"]) == 0
    assert "step 1/1 calibrate: done" in capsys.readouterr().out
    (job,) = read_jobs(project)
    assert job.state is JobState.DONE


def _split_as_the_shell_does(line: str) -> list[str]:
    """Split a printed command into the words its program gets, on this platform."""
    if os.name != "nt":
        return shlex.split(line)
    import ctypes  # noqa: PLC0415

    count = ctypes.c_int()
    split = ctypes.windll.shell32.CommandLineToArgvW
    split.restype = ctypes.POINTER(ctypes.c_wchar_p)
    words = split(line, ctypes.byref(count))
    try:
        return [words[i] for i in range(count.value)]
    finally:
        ctypes.windll.kernel32.LocalFree(words)


def test_the_printed_commands_run_as_printed_with_spaces_in_a_path_or_subject(
    tmp_path, capsys, calibrate, monkeypatch
):
    # #1310: the approve and continue lines keep a path or a subject with a space as one word.
    folder = tmp_path / "my experiment"
    folder.mkdir()
    Project.create(folder / "exp 1.tether")
    shutil.copy(calibrate, folder / "gamma 1.json")

    def calibrate_condition(request, *, approvals):  # noqa: ANN001, ANN202
        approvals.decide(
            "gamma.approve", "condition 1", project=request.project, report=request.report
        )
        return _CalibrateResult(approved=True)

    _replace_run(monkeypatch, "calibrate", calibrate_condition)
    step = _step("calibrate", project="exp 1.tether", report="gamma 1.json")
    path = _write(folder, _recipe(step), "recipe 1.json")
    assert main(["run", str(path)]) == EXIT_APPROVAL_NEEDED
    lines = {
        line.split(":", 1)[0].strip(): line.split(":", 1)[1].strip()
        for line in capsys.readouterr().err.splitlines()
        if line.startswith(("  approve:", "  continue:"))
    }
    approve = _split_as_the_shell_does(lines["approve"])
    assert approve == [
        "tether", "approve", str(folder / "exp 1.tether"), "gamma.approve",
        "--subject", "condition 1", "--report", str(folder / "gamma 1.json"),
    ]  # fmt: skip
    assert main([*approve[1:], "--by", "Ana"]) == 0
    (approval,) = approvals.read_approvals(folder / "exp 1.tether")
    assert approval.subject == "condition 1"

    resume = _split_as_the_shell_does(lines["continue"])
    assert resume == ["tether", "run", str(path), "--resume"]
    assert main(resume[1:]) == 0


@pytest.mark.parametrize(
    ("words", "windows", "line"),
    [
        (("tether", "run", "recipe.json", "--resume"), False, "tether run recipe.json --resume"),
        (("tether", "run", "recipe.json", "--resume"), True, "tether run recipe.json --resume"),
        (("--subject", "condition 1"), False, "--subject 'condition 1'"),
        (("--subject", "condition 1"), True, '--subject "condition 1"'),
        (("C:\\My runs\\r.json",), True, '"C:\\My runs\\r.json"'),
        (("C:\\runs\\r.json",), True, "C:\\runs\\r.json"),
        (("a&b", "", 'say "hi"', "end\\"), True, '"a&b" "" "say \\"hi\\"" end\\'),
        (("a b\\",), True, '"a b\\\\"'),
        (("it's",), False, "'it'\"'\"'s'"),
    ],
)
def test_a_printed_command_quotes_only_the_words_that_need_it(words, windows, line) -> None:
    from tether.cli import _command_line  # noqa: PLC0415, PLC2701 - the quoting rule

    assert _command_line(*words, windows=windows) == line


def test_with_a_policy_the_store_records_approved_by_policy_and_the_policy_text(
    tmp_path, calibrate
):
    policy = {"step": "gamma.approve", "rule": "value", "gamma": 1.1, "source": "lab note 12"}
    policy["otherwise"] = "stop"
    source = json.dumps(policy, indent=2).replace("\n", "\r\n")
    recipe_text = _recipe(_calibrate_step(**policy)).replace(json.dumps(policy), source)
    path = tmp_path / "recipe.json"
    path.write_bytes(recipe_text.encode("utf-8"))
    assert main(["run", str(path)]) == 0
    (approval,) = approvals.read_approvals(tmp_path / "exp.tether")
    assert approval.by == "policy"
    assert approval.actor == source
    assert approval.recipe_sha256 == rc.read_recipe(path).sha256
    assert (
        approval.recipe_sha256
        == hashlib.sha256(recipe_text.replace("\r\n", "\n").encode()).hexdigest()
    )
    assert approval.step == "gamma.approve" and approval.subject == "cond-1"


@pytest.mark.parametrize(
    ("step", "subject"), [("alpha.confirm", "cond-1"), ("gamma.approve", "other")]
)
def test_ordinary_approval_does_not_inherit_an_unrelated_recipe(tmp_path, calibrate, step, subject):
    path = _write(tmp_path, _recipe(_calibrate_step()))
    project = tmp_path / "exp.tether"
    assert main(["run", str(path)]) == EXIT_APPROVAL_NEEDED
    assert (
        main(["approve", str(project), step, "--subject", subject, "--report", str(calibrate)]) == 0
    )

    (approval,) = approvals.read_approvals(project)
    assert (approval.recipe_sha256, approval.recipe_version) == ("", 0)


def test_tether_approve_refuses_a_bad_report_or_step_before_any_write(tmp_path, capsys):
    project = tmp_path / "exp.tether"
    Project.create(project)
    base = ["approve", str(project), "gamma.approve", "--subject", "c1"]
    assert main([*base, "--report", str(tmp_path / "missing.json")]) == 2
    (tmp_path / "bad.json").write_text("{", encoding="utf-8")
    assert main([*base, "--report", str(tmp_path / "bad.json")]) == 2
    assert "an approval needs its report" in capsys.readouterr().err
    # Valid evidence, so only the blank subject can refuse the request.
    (tmp_path / "good.json").write_text("{}", encoding="utf-8")
    assert main([*base[:-1], " ", "--report", str(tmp_path / "good.json")]) == 2
    assert "an approval needs a subject (--subject)" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["approve", str(project), "publish", "--subject", "c", "--report", "r.json"])
    assert approvals.read_approvals(project) == []


@pytest.mark.parametrize("change", [{"step": []}, {"rule": {}}])
def test_run_refuses_non_string_policy_keys(tmp_path, capsys, calibrate, change) -> None:
    policy = {"step": "gamma.approve", "rule": "value", "otherwise": "stop", **change}
    path = _write(tmp_path, _recipe(_calibrate_step(**policy)))

    assert main(["run", str(path)]) == 2
    assert "step 1:" in capsys.readouterr().err
    assert approvals.read_approvals(tmp_path / "exp.tether") == []


def test_approval_evidence_matches_the_literal_report_stem(tmp_path) -> None:
    report = _write(tmp_path, "{}", "gamma[1].json")
    for name in ("gamma[1].png", "gamma[1]-steps.png", "gamma1.png", "gamma[1]x.png"):
        (tmp_path / name).write_bytes(b"\x89PNG evidence")

    assert set(approvals.report_files(report)) == {
        report.name,
        "gamma[1].png",
        "gamma[1]-steps.png",
    }


def test_tether_approve_refuses_unreadable_png_evidence(tmp_path, capsys, calibrate, monkeypatch):
    sha256 = approvals._sha256

    def hash_readable_files(path: Path) -> str:
        if path.suffix == ".png":
            raise PermissionError("evidence is unreadable")
        return sha256(path)

    monkeypatch.setattr(approvals, "_sha256", hash_readable_files)
    project = tmp_path / "exp.tether"
    assert (
        main(
            [
                "approve",
                str(project),
                "gamma.approve",
                "--subject",
                "c1",
                "--report",
                str(calibrate),
            ]
        )
        == 2
    )
    assert "evidence is unreadable" in capsys.readouterr().err
    assert approvals.read_approvals(project) == []


def test_record_approval_refuses_a_released_retained_lock(tmp_path, calibrate, monkeypatch):
    from tether.project import lock

    project = Project.open(tmp_path / "exp.tether")
    held = project.acquire_lock()
    report_files = approvals.report_files

    def read_report_after_lock_loss(path: Path) -> dict[str, str]:
        files = report_files(path)
        assert lock.release(project.path, held)
        return files

    monkeypatch.setattr(approvals, "report_files", read_report_after_lock_loss)
    try:
        with pytest.raises(lock.LockedError):
            approvals.record_approval(
                project,
                step="gamma.approve",
                subject="c1",
                by="person",
                actor="Ana",
                report=calibrate,
            )
    finally:
        project.release_lock()
    assert approvals.read_approvals(project.path) == []


def test_an_approval_by_no_recipe_run_records_no_recipe(tmp_path) -> None:
    project = tmp_path / "exp.tether"
    Project.create(project)
    report = tmp_path / "map.json"
    report.write_text("{}", encoding="utf-8")
    argv = ["approve", str(project), "map.accept", "--subject", "beads.npz"]
    assert main([*argv, "--report", str(report), "--by", "Ana"]) == 0
    (approval,) = approvals.read_approvals(project)
    assert (approval.recipe_sha256, approval.recipe_version) == ("", 0)
    assert approval.report == {"map.json": hashlib.sha256(b"{}").hexdigest()}


# --- Save as recipe ---------------------------------------------------------------------------

_UUID = re.compile(r"[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}")

#: The parts of a store that two runs on two copies of the inputs may differ in: the job
#: table (ids, times, owner, the full paths of the request) and the movie table (the path
#: and the file time of the copy). Random ids in names and text are read as ``<id>``. #900
#: owns the complete list for the application-and-command-line comparison.
_MAY_DIFFER = ("settings/jobs", "movies/table")

#: The generation table (#921) also records when a generation was written and the path and
#: file time of the copy it was read from; only those fields are set aside, so the rest of
#: each row is still compared.
_GENERATION_TABLE = "generations/table"


def _generation_rows(table: np.ndarray) -> list[object]:
    rows = []
    for row in table.tolist():
        fields = dict(zip(table.dtype.names, row, strict=True))
        fields.pop("created_utc")
        inputs = json.loads(fields["inputs"])
        inputs["source"] = {k: v for k, v in inputs["source"].items() if k not in ("uri", "mtime")}
        fields["inputs"] = inputs
        rows.append(fields)
    return rows


def _datasets(path: Path) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    with h5py.File(path, "r") as f:

        def visit(name: str, obj: object) -> None:
            if isinstance(obj, h5py.Dataset) and name not in _MAY_DIFFER:
                value = obj[()]
                if name == _GENERATION_TABLE:
                    value = _generation_rows(value)
                text = _UUID.sub(
                    "<id>", repr(value.tolist() if isinstance(value, np.ndarray) else value)
                )
                found.append((_UUID.sub("<id>", name), hashlib.sha256(text.encode()).hexdigest()))

        f.visititems(visit)
    return sorted(found)


def test_a_saved_recipe_run_on_a_new_copy_of_the_inputs_gives_the_same_store(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    _make_movie(first, name="m.tif")
    assert main(["run", str(_write(first, _recipe(_step())))]) == 0

    saved = rc.write_recipe(first / "saved.json", rc.recipe_from_project(first / "m.tether"))
    shutil.copy(first / "m.tif", second / "m.tif")
    shutil.copy(saved, second / "saved.json")
    assert main(["run", str(second / "saved.json")]) == 0
    assert _datasets(first / "m.tether") == _datasets(second / "m.tether")
    assert [j.command for j in read_jobs(second / "m.tether")] == ["extract"]


def test_save_as_recipe_needs_a_completed_job(tmp_path) -> None:
    Project.create(tmp_path / "exp.tether")
    with pytest.raises(rc.RecipeError, match="no completed job"):
        rc.recipe_from_project(tmp_path / "exp.tether")


# --- the edges of the checker and the runner ----------------------------------------------------


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('{"format": "tether-recipe", "version": 1, "steps": [1]}', "step 1: a step must be"),
        ('{"format": "tether-recipe", "version": 1, "steps": [{"command": "extract"}]}', "step 1:"),
        (_recipe({**_step(), "policy": {"step": "x"}}), "step 1: a policy names an unknown"),
    ],
)
def test_a_bad_step_or_step_policy_is_refused_with_the_step(tmp_path, text, message) -> None:
    with pytest.raises(rc.RecipeError, match=re.escape(message)):
        rc.read_recipe(_write(tmp_path, text))


def test_a_policy_must_be_an_object_and_a_missing_recipe_cannot_be_read(tmp_path) -> None:
    with pytest.raises(rc.RecipeError, match="a policy must be a JSON object"):
        rc.check_policy(["map.accept"])
    with pytest.raises(rc.RecipeError, match="cannot read the recipe"):
        rc.read_recipe(tmp_path / "missing.json")


def test_a_list_of_paths_is_read_and_written_relative_to_the_recipe_folder(tmp_path) -> None:
    from tether.services import BatchRequest

    path = _write(tmp_path, _recipe(_step("batch", movies=["a.tif", "in/b.tif"], out_dir="out")))
    (step,) = rc.read_recipe(path).steps
    assert step.request.movies == (str(tmp_path / "a.tif"), str(tmp_path / "in" / "b.tif"))
    saved = rc.write_recipe(tmp_path / "saved.json", [step.request])
    fields = json.loads(saved.read_text(encoding="utf-8"))["steps"][0]["fields"]
    assert fields["movies"] == ["a.tif", "in/b.tif"] and fields["out_dir"] == "out"
    assert isinstance(step.request, BatchRequest)


def test_a_batch_recipe_preserves_a_parent_component_after_a_link(tmp_path) -> None:
    from tether.services import BatchRequest
    from tether.services.batch import plan

    target = tmp_path / "archive" / "sub"
    target.mkdir(parents=True)
    work = tmp_path / "work"
    work.mkdir()
    _make_movie(target.parent, name="m.tif")
    _make_movie(work, name="m.tif")
    try:
        (work / "link").symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("this machine cannot make a symbolic link")

    movies = ["work/link/../m.tif", "work/m.tif"]
    direct = BatchRequest(
        movies=tuple(str(tmp_path / movie) for movie in movies),
        out_dir=str(tmp_path / "out"),
        idealize=False,
    )
    text = _recipe(_step("batch", movies=movies, out_dir="out", idealize=False))
    handwritten = _write(tmp_path, text)
    saved = rc.write_recipe(tmp_path / "saved.json", [direct])
    assert json.loads(saved.read_text(encoding="utf-8"))["steps"][0]["fields"]["movies"] == movies
    expected = plan(direct)
    for path in (handwritten, saved):
        (step,) = rc.read_recipe(path).steps
        assert isinstance(step.request, BatchRequest)
        assert plan(step.request) == expected


def test_a_batch_recipe_glob_uses_its_literal_folder(tmp_path, monkeypatch) -> None:
    from tether.project import batch

    folder = tmp_path / "run[1]"
    folder.mkdir()
    sibling = tmp_path / "run1"
    sibling.mkdir()
    movie = _make_movie(folder, name="take-1.tif")
    _make_movie(sibling, name="take-2.tif")
    literal = _make_movie(folder, name="other[1].tif")
    absolute = _make_movie(sibling, name="absolute-1.tif")
    movies = ["take-*.tif", "other[1].tif", str(sibling / "absolute-*.tif")]
    recipe = rc.read_recipe(
        _write(folder, _recipe(_step("batch", movies=movies, out_dir="out", idealize=False)))
    )
    seen = []

    def collect(jobs, **_kwargs):
        seen.extend(job.movie_path for job in jobs)
        return batch.BatchSummary(results=[], policy="warn", idealize_requested=False)

    monkeypatch.setattr(batch, "run_batch", collect)
    assert not rc.run_recipe(recipe).failed
    assert sorted(seen) == sorted([movie, literal, absolute])


def test_a_path_on_another_drive_stays_a_full_path(tmp_path, monkeypatch) -> None:
    def other_drive(path: str, start: object = None) -> str:
        raise ValueError("path is on mount 'D:', start on mount 'C:'")

    monkeypatch.setattr(rc.os.path, "relpath", other_drive)
    movie = str(tmp_path / "m.tif")
    saved = rc.write_recipe(tmp_path / "saved.json", [ExtractRequest(movie=movie, output=movie)])
    assert json.loads(saved.read_text(encoding="utf-8"))["steps"][0]["fields"]["movie"] == movie


def test_a_job_part_that_is_not_a_recipe_part_is_not_a_checkpoint() -> None:
    assert rc._parse_part("{") is None
    assert rc._parse_part('{"step": 1}') is None
    assert rc._parse_part('{"recipe_sha256": "a", "recipe_version": 1, "step": 2}') is not None


def test_the_stopped_recipe_is_read_only_from_a_stopped_recipe_job(tmp_path) -> None:
    from tether.project import jobs

    path = tmp_path / "exp.tether"
    project = Project.create(path)
    request = IdealizeRequest(project=str(path))

    def stop(_part: str, _cancel: object) -> None:
        raise jobs.JobCancelled

    project.acquire_lock()
    try:
        for parts in (["a", "b"], ["not a recipe part"]):
            job = jobs.submit_job(project, request, parts)
            jobs.run_job(project, job.job_id, stop)
    finally:
        project.release_lock()
    assert [j.stored_state for j in read_jobs(path)] == [JobState.CANCELLED] * 2
    assert rc.stopped_recipe(path) is None


def _replace_run(monkeypatch, name: str, run) -> None:  # noqa: ANN001
    commands = tuple(
        dataclasses.replace(e, run=run) if e.name == name else e for e in registry.COMMANDS
    )
    monkeypatch.setattr(registry, "COMMANDS", commands)


def test_a_failed_step_in_a_project_is_stored_failed_and_resume_retries_it(
    tmp_path, capsys, calibrate, monkeypatch
):
    from tether.services.base import CommandFailed

    def fail(request, *, approvals):  # noqa: ANN001, ANN202
        raise CommandFailed("the fit did not converge")

    _replace_run(monkeypatch, "calibrate", fail)
    path = _write(tmp_path, _recipe(_calibrate_step()))
    project = tmp_path / "exp.tether"
    assert main(["run", str(path)]) == 1
    assert "the fit did not converge" in capsys.readouterr().out
    (job,) = read_jobs(project)
    assert job.state is JobState.FAILED

    _replace_run(monkeypatch, "calibrate", lambda request, *, approvals: _CalibrateResult(True))
    assert main(["run", str(path), "--resume"]) == 0
    (job,) = read_jobs(project)
    assert job.state is JobState.DONE


def test_a_step_whose_job_cannot_be_stored_fails_with_the_reason(
    tmp_path, tmp_path_factory, calibrate, monkeypatch
):
    recipe = rc.read_recipe(_write(tmp_path, _recipe(_calibrate_step())))

    def broken(*_args: object) -> None:
        raise OSError("disk full")

    # Undo only this replacement: monkeypatch.undo() would also undo the per-test user-state
    # folder of the autouse fixture, and the next run would write its checkpoint outside it.
    state = user_lock_dir()
    with monkeypatch.context() as scoped:
        scoped.setattr(rc, "_queue", broken)
        run = rc.run_recipe(recipe)  # no progress callback
    assert run.failed and "cannot store the job of the step" in run.steps[0].message
    assert user_lock_dir() == state and state.is_relative_to(tmp_path_factory.getbasetemp())

    monkeypatch.setattr(rc, "_record", broken)
    policy = {"step": "gamma.approve", "rule": "value", "gamma": 1.1, "source": "lab note"}
    recipe = rc.read_recipe(
        _write(tmp_path, _recipe(_calibrate_step(**policy, otherwise="stop")), "p.json")
    )
    run = rc.run_recipe(recipe)
    assert run.failed and run.stop is None
    assert "the step ran, but its job could not be stored" in run.steps[0].message


def test_a_running_job_of_the_step_is_not_taken_over(tmp_path, calibrate) -> None:
    from tether.project import jobs

    path = _write(tmp_path, _recipe(_calibrate_step()))
    project = tmp_path / "exp.tether"
    assert main(["run", str(path)]) == EXIT_APPROVAL_NEEDED
    (job,) = read_jobs(project)
    # As if another run of the recipe holds the job: it is not reused.
    jobs._write_job(project, job.job_id, state=JobState.RUNNING.value)
    assert main(["run", str(path), "--resume"]) == EXIT_APPROVAL_NEEDED
    assert len(read_jobs(project)) == 2


def test_two_steps_in_one_project_each_have_their_job_and_save_keeps_each_in_place(
    tmp_path, capsys, calibrate
):
    policy = {"step": "gamma.approve", "rule": "value", "gamma": 1.1, "source": "lab note"}
    step = _calibrate_step(**policy, otherwise="stop")
    path = _write(tmp_path, _recipe(step, step))
    project = tmp_path / "exp.tether"
    assert main(["run", str(path), "--dry-run"]) == 0
    assert "  approval step gamma.approve: policy {" in capsys.readouterr().out
    assert main(["run", str(path)]) == 0
    assert [j.state for j in read_jobs(project)] == [JobState.DONE] * 2
    # A request that a later job repeats is kept again: a job between the two could have
    # changed what the repeat does.
    assert len(rc.recipe_from_project(project)) == 2


def test_a_dry_run_names_an_approval_step_that_stops_for_a_person(tmp_path, capsys, calibrate):
    path = _write(tmp_path, _recipe(_calibrate_step()))
    assert main(["run", str(path), "--dry-run"]) == 0
    assert (
        "  approval step gamma.approve: stops for a person (no policy)" in capsys.readouterr().out
    )


def test_the_gate_records_only_the_policy_of_its_step(tmp_path, calibrate) -> None:
    recipe = rc.read_recipe(_write(tmp_path, _recipe(_calibrate_step())))
    gate = rc.ApprovalGate(recipe, recipe.steps[0])
    with pytest.raises(ValueError, match="no policy for 'gamma.approve'"):
        gate.record(None, step="gamma.approve", subject="c", report=str(calibrate))


def test_save_as_recipe_refuses_a_job_it_cannot_read(tmp_path, calibrate, monkeypatch) -> None:
    from tether.services.base import RequestError

    policy = {"step": "gamma.approve", "rule": "value", "gamma": 1.1, "source": "lab note"}
    path = _write(tmp_path, _recipe(_calibrate_step(**policy, otherwise="stop")))
    assert main(["run", str(path)]) == 0

    def unreadable(text: str) -> Request:
        raise RequestError("version 9 is not supported")

    monkeypatch.setattr(registry, "request_from_text", unreadable)
    with pytest.raises(rc.RecipeError, match="holds a request this Tether cannot read"):
        rc.recipe_from_project(tmp_path / "exp.tether")


# --- tether approve: the refusals the command line cannot reach -------------------------------


def test_approve_refuses_an_unknown_step_a_blank_name_or_a_missing_project(tmp_path) -> None:
    from tether.services import ApproveRequest
    from tether.services import approve as ap
    from tether.services.base import RequestRefused

    report = tmp_path / "r.json"
    report.write_text("{}", encoding="utf-8")
    project = tmp_path / "exp.tether"
    Project.create(project)
    good = {"project": str(project), "step": "map.accept", "subject": "s", "report": str(report)}
    for change, message in (
        ({"step": "deposit.publish"}, "unknown approval step"),
        ({"by": " "}, "needs the name of the person"),
        ({"project": str(tmp_path / "missing.tether")}, "cannot open"),
    ):
        with pytest.raises(RequestRefused, match=message):
            ap.run(ApproveRequest(**{**good, **change}))
    assert approvals.read_approvals(project) == []


def test_approve_fails_when_the_lock_or_the_write_fails(tmp_path, monkeypatch) -> None:
    from tether.project.lock import LockError
    from tether.services import ApproveRequest
    from tether.services import approve as ap
    from tether.services.base import CommandFailed

    report = tmp_path / "r.json"
    report.write_text("{}", encoding="utf-8")
    project = tmp_path / "exp.tether"
    Project.create(project)
    request = ApproveRequest(
        project=str(project), step="map.accept", subject="s", report=str(report), by="Ana"
    )

    def no_write(*_args: object, **_kwargs: object) -> None:
        raise OSError("read-only file system")

    monkeypatch.setattr(approvals, "record_approval", no_write)
    with pytest.raises(CommandFailed, match="read-only file system"):
        ap.run(request)

    def no_lock(self: Project) -> None:
        raise LockError("held by Ben")

    monkeypatch.setattr(Project, "acquire_lock", no_lock)
    with pytest.raises(CommandFailed, match="cannot take the lock"):
        ap.run(request)


def test_a_step_with_a_deferred_stage_is_not_complete_and_resume_runs_it_again(
    tmp_path, capsys, calibrate, monkeypatch
):
    from types import SimpleNamespace

    ran: list[str] = []

    def batch_like(n_deferred: int):  # noqa: ANN202
        def run(request, *, approvals):  # noqa: ANN001, ANN202
            ran.append(request.report)
            return SimpleNamespace(summary=SimpleNamespace(n_failed=0, n_deferred=n_deferred))

        return run

    (tmp_path / "later.json").write_text("{}", encoding="utf-8")
    later = _step("calibrate", project="exp.tether", report="later.json")
    path = _write(tmp_path, _recipe(_calibrate_step(), later))
    project = tmp_path / "exp.tether"
    _replace_run(monkeypatch, "calibrate", batch_like(1))
    assert main(["run", str(path)]) == 3
    assert "a stage was deferred" in capsys.readouterr().out
    assert [Path(report).name for report in ran] == ["gamma.json"]
    assert [j.stored_state for j in read_jobs(project)] == [JobState.FAILED]
    with pytest.raises(rc.RecipeError, match="no completed job"):
        rc.recipe_from_project(project)

    # Both steps can complete once the first step's deferred stage is available.
    _replace_run(monkeypatch, "calibrate", batch_like(0))
    assert main(["run", str(path), "--resume"]) == 0
    assert [j.stored_state for j in read_jobs(project)] == [JobState.DONE] * 2
    ran.clear()
    assert main(["run", str(path), "--resume"]) == 0
    assert ran == []


@pytest.mark.parametrize(
    "step", [_step("idealize", project="m.tether"), _step("batch", movies=["a.tif"], out_dir="out")]
)
def test_a_recipe_uses_local_sidecar_selection(tmp_path, monkeypatch, capsys, step) -> None:
    from tether.idealize.driver import resolve_sidecar_python
    from tether.services import BatchRequest

    monkeypatch.setenv("TETHER_SIDECAR_PYTHON", sys.executable)
    # Earlier writers included null for an unset interpreter.
    fields = {**step["fields"], "sidecar_python": None}
    (read,) = rc.recipe_from_text(_recipe({**step, "fields": fields}), folder=tmp_path).steps
    assert isinstance(read.request, (IdealizeRequest, BatchRequest))
    assert read.request.sidecar_python is None
    assert resolve_sidecar_python(read.request.sidecar_python) == Path(sys.executable)

    direct = dataclasses.replace(read.request, sidecar_python=sys.executable)
    saved = rc.write_recipe(tmp_path / "saved.json", [direct])
    (saved_step,) = json.loads(saved.read_text(encoding="utf-8"))["steps"]
    assert "sidecar_python" not in saved_step["fields"]
    assert rc.read_recipe(saved).steps[0].request == read.request

    supplied = {**step, "fields": {**fields, "sidecar_python": "local-runtime"}}
    supplied_path = _write(tmp_path, _recipe(supplied))
    assert main(["run", str(supplied_path), "--dry-run"]) == 2
    assert "select the sidecar interpreter locally with TETHER_SIDECAR_PYTHON" in (
        capsys.readouterr().err
    )


def test_resume_uses_existing_project_jobs_when_no_local_checkpoint_exists(
    tmp_path, calibrate
) -> None:
    recipe = rc.read_recipe(_write(tmp_path, _recipe(_calibrate_step())))
    assert "(done; skipped by --resume)" not in "\n".join(rc.plan_recipe(recipe, resume=True))
    project = tmp_path / "exp.tether"
    rc._record(str(project), recipe.steps[0].request, rc.recipe_part(recipe, 1), "done", "")
    assert rc.run_recipe(recipe, resume=True).steps == (rc.StepOutcome(1, "calibrate", "skipped"),)
    project.unlink()
    assert "(done; skipped by --resume)" not in "\n".join(rc.plan_recipe(recipe, resume=True))


def test_resume_without_local_state_cannot_infer_an_earlier_projectless_step(tmp_path, calibrate):
    step = _step("map", source="beads.tif", output="beads.npz")
    recipe = rc.read_recipe(_write(tmp_path, _recipe(step, _calibrate_step())))
    assert "(done; skipped by --resume)" not in "\n".join(rc.plan_recipe(recipe, resume=True))
    project = tmp_path / "exp.tether"
    rc._record(str(project), recipe.steps[1].request, rc.recipe_part(recipe, 2), "done", "")
    with pytest.raises(rc.RecipeError, match="cannot recover step 1 without its local checkpoint"):
        rc.run_recipe(recipe, resume=True)
    assert not rc._checkpoint_path(recipe).exists()


def test_resume_does_not_adopt_done_job_from_an_older_attempt(tmp_path, calibrate):
    recipe = rc.read_recipe(_write(tmp_path, _recipe(_calibrate_step())))
    project = tmp_path / "exp.tether"
    rc._record(
        str(project), recipe.steps[0].request, rc.recipe_part(recipe, 1, "run", "old"), "done", ""
    )
    checkpoint = rc._checkpoint_path(recipe)
    checkpoint.parent.mkdir(parents=True)
    rc._save_checkpoint(checkpoint, rc._Checkpoint(0, "run", "pending"))
    assert "(done; skipped by --resume)" not in "\n".join(rc.plan_recipe(recipe, resume=True))


@pytest.mark.parametrize("checkpoint", ["not JSON", '{"completed": true}'])
def test_resume_refuses_an_invalid_checkpoint(tmp_path, capsys, checkpoint) -> None:
    path = _write(tmp_path, _recipe(_step()))
    checkpoint_path = rc._checkpoint_path(rc.read_recipe(path))
    checkpoint_path.parent.mkdir(parents=True)
    checkpoint_path.write_text(checkpoint, encoding="utf-8")

    assert main(["run", str(path), "--dry-run", "--resume"]) == 2
    assert "the recipe checkpoint is invalid" in capsys.readouterr().err
    assert checkpoint_path.read_text(encoding="utf-8") == checkpoint


def test_resume_reports_an_unreadable_checkpoint(tmp_path, capsys) -> None:
    path = _write(tmp_path, _recipe(_step()))
    rc._checkpoint_path(rc.read_recipe(path)).mkdir(parents=True)
    assert main(["run", str(path), "--dry-run", "--resume"]) == 2
    assert "cannot read the recipe checkpoint" in capsys.readouterr().err


@pytest.mark.parametrize("fail_at", [0, 1])
def test_run_reports_checkpoint_write_failures(tmp_path, capsys, monkeypatch, fail_at) -> None:
    _make_movie(tmp_path, name="m.tif")
    path = _write(tmp_path, _recipe(_step()))
    save = rc._save_checkpoint

    def fail_write(checkpoint: Path, state: rc._Checkpoint) -> None:
        if state.completed == fail_at:
            raise OSError("checkpoint storage is unavailable")
        save(checkpoint, state)

    monkeypatch.setattr(rc, "_save_checkpoint", fail_write)
    assert main(["run", str(path)]) == (2 if fail_at == 0 else 1)
    result = capsys.readouterr()
    if fail_at == 0:
        assert "cannot start a recipe checkpoint" in result.err
    else:
        assert "cannot record recipe progress" in result.out
    assert (tmp_path / "m.tether").exists() is bool(fail_at)
    monkeypatch.setattr(rc, "_save_checkpoint", save)
    assert main(["run", str(path), "--resume"]) == 0
    expected = "skipped" if fail_at else "done"
    assert f"step 1/1 extract: {expected}" in capsys.readouterr().out
    assert len(read_jobs(tmp_path / "m.tether")) == 1


def _fail_on(name: str):  # noqa: ANN202
    """A calibrate run that fails for the report ``name`` and records what it ran."""
    from tether.services.base import CommandFailed

    ran: list[str] = []

    def run(request, *, approvals):  # noqa: ANN001, ANN202
        ran.append(Path(request.report).name)
        if Path(request.report).name == name:
            raise CommandFailed(f"{name} failed")
        return _CalibrateResult(True)

    run.ran = ran  # type: ignore[attr-defined]
    return run


def test_a_step_that_a_later_run_failed_is_not_complete_for_resume(
    tmp_path, calibrate, monkeypatch
):
    path = _write(tmp_path, _recipe(_calibrate_step()))
    _replace_run(monkeypatch, "calibrate", _fail_on("none"))
    assert main(["run", str(path)]) == 0
    _replace_run(monkeypatch, "calibrate", _fail_on("gamma.json"))
    assert main(["run", str(path)]) == 1  # a new run, not --resume: a new job, failed
    run = _fail_on("none")
    _replace_run(monkeypatch, "calibrate", run)
    assert main(["run", str(path), "--resume"]) == 0
    assert run.ran == ["gamma.json"]  # the latest attempt failed, so it runs again


def test_resume_reruns_later_steps_after_retrying_an_earlier_step(
    tmp_path, calibrate, monkeypatch
) -> None:
    later = _step("calibrate", project="exp.tether", report="later.json")
    path = _write(tmp_path, _recipe(_calibrate_step(), later))
    _replace_run(monkeypatch, "calibrate", _fail_on("none"))
    assert main(["run", str(path)]) == 0

    _replace_run(monkeypatch, "calibrate", _fail_on("gamma.json"))
    assert main(["run", str(path)]) == 1

    retried = _fail_on("none")
    _replace_run(monkeypatch, "calibrate", retried)
    assert main(["run", str(path), "--resume"]) == 0
    assert retried.ran == ["gamma.json", "later.json"]


@dataclasses.dataclass(frozen=True, kw_only=True)
class _SurveyRequest(Request):
    COMMAND: ClassVar[str] = "survey"
    PATH_FIELDS: ClassVar[tuple[str, ...]] = ("report",)

    report: str


def test_a_deferred_step_that_stores_no_job_stops_the_run(tmp_path, calibrate, monkeypatch):
    from types import SimpleNamespace

    deferred = {"n": 1}
    ran: list[str] = []

    def survey(request, **_observers):  # noqa: ANN001, ANN003, ANN202
        ran.append("survey")
        return SimpleNamespace(summary=SimpleNamespace(n_failed=0, n_deferred=deferred["n"]))

    entry = registry.ServiceCommand(
        name="survey",
        request_type=_SurveyRequest,
        result_type=object,
        run=survey,
        summary="a test command that stores no job",
    )
    monkeypatch.setattr(registry, "COMMANDS", (*registry.COMMANDS, entry))
    policy = {"step": "gamma.approve", "rule": "value", "gamma": 1.1, "source": "lab note"}
    first = _calibrate_step(**policy, otherwise="stop")
    survey_step = _step("survey", report="gamma.json")
    path = _write(tmp_path, _recipe(first, survey_step, first))
    project = tmp_path / "exp.tether"
    assert main(["run", str(path)]) == 3
    assert [j.stored_state for j in read_jobs(project)] == [JobState.DONE]  # step 3 not run

    deferred["n"] = 0
    ran.clear()
    assert main(["run", str(path), "--resume"]) == 0
    assert ran == ["survey"]
    assert [j.stored_state for j in read_jobs(project)] == [JobState.DONE] * 2

    # As the first step, it leaves nothing stored, and --resume still runs it again.
    deferred["n"] = 1
    alone = _write(tmp_path, _recipe(survey_step), "alone.json")
    assert main(["run", str(alone)]) == 3
    deferred["n"] = 0
    ran.clear()
    assert main(["run", str(alone), "--resume"]) == 0
    assert ran == ["survey"]


def test_save_as_recipe_writes_the_jobs_in_the_order_they_completed(
    tmp_path, calibrate, monkeypatch
):
    (tmp_path / "b.json").write_text("{}", encoding="utf-8")
    first = _write(tmp_path, _recipe(_calibrate_step()), "a.json.recipe")
    second = _write(
        tmp_path, _recipe(_step("calibrate", project="exp.tether", report="b.json")), "b.recipe"
    )
    _replace_run(monkeypatch, "calibrate", _fail_on("gamma.json"))
    assert main(["run", str(first)]) == 1
    assert main(["run", str(second)]) == 0
    _replace_run(monkeypatch, "calibrate", _fail_on("none"))
    assert main(["run", str(first), "--resume"]) == 0
    reports = [Path(r.report).name for r in rc.recipe_from_project(tmp_path / "exp.tether")]
    assert reports == ["b.json", "gamma.json"]
