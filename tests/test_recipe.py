# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Recipe files, ``tether run`` and ``tether approve`` (#899, ADR-0100 §6, ADR-0102)."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
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
from tether.services import ExtractRequest, IdealizeRequest, registry
from tether.services import recipe as rc
from tether.services.base import Request

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_extract_cli import _make_movie  # noqa: E402, PLC2701 - the synthetic movie


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
    assert job.parts == (rc.recipe_part(recipe, 1),)
    assert json.loads(job.parts[0]) == {
        "recipe_sha256": recipe.sha256,
        "recipe_version": 1,
        "step": 1,
    }
    assert main(["run", str(path), "--resume"]) == 0
    assert "step 1/1 extract: skipped" in capsys.readouterr().out
    assert len(read_jobs(tmp_path / "m.tether")) == 1


def test_resume_with_no_run_of_the_recipe_is_refused(tmp_path, capsys) -> None:
    path = _write(tmp_path, _recipe(_step()))
    assert main(["run", str(path), "--resume"]) == 2
    assert "no run of this recipe to resume" in capsys.readouterr().err


def test_a_failed_step_stops_the_run_with_exit_1(tmp_path, capsys) -> None:
    path = _write(tmp_path, _recipe(_step(), _step("idealize", project="m.tether")))
    assert main(["run", str(path)]) == 1  # there is no movie
    captured = capsys.readouterr()
    assert "step 1/2 extract: failed" in captured.out and "step 2/2" not in captured.out
    assert "--resume" in captured.err


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


def test_with_a_policy_the_store_records_approved_by_policy_and_the_policy_text(
    tmp_path, calibrate
):
    policy = {"step": "gamma.approve", "rule": "value", "gamma": 1.1, "source": "lab note 12"}
    policy["otherwise"] = "stop"
    path = _write(tmp_path, _recipe(_calibrate_step(**policy)))
    assert main(["run", str(path)]) == 0
    (approval,) = approvals.read_approvals(tmp_path / "exp.tether")
    assert approval.by == "policy"
    assert json.loads(approval.actor) == policy
    assert approval.recipe_sha256 == rc.read_recipe(path).sha256
    assert approval.step == "gamma.approve" and approval.subject == "cond-1"


def test_tether_approve_refuses_a_bad_report_or_step_before_any_write(tmp_path, capsys):
    project = tmp_path / "exp.tether"
    Project.create(project)
    base = ["approve", str(project), "gamma.approve", "--subject", "c1"]
    assert main([*base, "--report", str(tmp_path / "missing.json")]) == 2
    (tmp_path / "bad.json").write_text("{", encoding="utf-8")
    assert main([*base, "--report", str(tmp_path / "bad.json")]) == 2
    assert main([*base[:-1], " ", "--report", str(tmp_path / "bad.json")]) == 2
    with pytest.raises(SystemExit):
        main(["approve", str(project), "publish", "--subject", "c", "--report", "r.json"])
    assert approvals.read_approvals(project) == []
    assert "an approval needs its report" in capsys.readouterr().err


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


def _datasets(path: Path) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    with h5py.File(path, "r") as f:

        def visit(name: str, obj: object) -> None:
            if isinstance(obj, h5py.Dataset) and name not in _MAY_DIFFER:
                value = obj[()]
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


def test_a_step_whose_job_cannot_be_stored_fails_with_the_reason(tmp_path, calibrate, monkeypatch):
    recipe = rc.read_recipe(_write(tmp_path, _recipe(_calibrate_step())))

    def broken(*_args: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(rc, "_queue", broken)
    run = rc.run_recipe(recipe)  # no progress callback
    assert run.failed and "cannot store the job of the step" in run.steps[0].message

    monkeypatch.undo()
    monkeypatch.setattr(registry, "COMMANDS", (*registry.COMMANDS, _calibrate_entry()))
    monkeypatch.setattr(rc, "_record", broken)
    policy = {"step": "gamma.approve", "rule": "value", "gamma": 1.1, "source": "lab note"}
    recipe = rc.read_recipe(
        _write(tmp_path, _recipe(_calibrate_step(**policy, otherwise="stop")), "p.json")
    )
    run = rc.run_recipe(recipe)
    assert run.failed and run.stop is None
    assert "the step ran, but its job could not be stored" in run.steps[0].message


def _calibrate_entry() -> registry.ServiceCommand:
    return registry.ServiceCommand(
        name="calibrate",
        request_type=_CalibrateRequest,
        result_type=_CalibrateResult,
        run=_calibrate,
        summary="a test command that reaches gamma.approve",
        approval_steps=("gamma.approve",),
    )


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
    assert [j.stored_state for j in read_jobs(project)] == [JobState.FAILED] * 2
    with pytest.raises(rc.RecipeError, match="no completed job"):
        rc.recipe_from_project(project)

    # Only the first step deferred this time: the second is complete and is skipped later.
    _replace_run(monkeypatch, "calibrate", batch_like(0))
    assert main(["run", str(path), "--resume"]) == 0
    assert [j.stored_state for j in read_jobs(project)] == [JobState.DONE] * 2
    ran.clear()
    assert main(["run", str(path), "--resume"]) == 0
    assert ran == []


def test_a_relative_sidecar_interpreter_is_read_from_the_recipe_folder(tmp_path) -> None:
    from tether.services import BatchRequest

    idealize = _step("idealize", project="m.tether", sidecar_python="env/bin/python")
    batch = _step("batch", movies=["a.tif"], sidecar_python="env/bin/python")
    steps = rc.read_recipe(_write(tmp_path, _recipe(idealize, batch))).steps
    for step in steps:
        assert step.request.sidecar_python == str(tmp_path / "env" / "bin" / "python")
    assert isinstance(steps[1].request, BatchRequest)
    saved = rc.write_recipe(tmp_path / "saved.json", [s.request for s in steps])
    for step in json.loads(saved.read_text(encoding="utf-8"))["steps"]:
        assert step["fields"]["sidecar_python"] == "env/bin/python"


def test_resume_skips_a_step_that_stores_no_job_up_to_the_checkpoint(tmp_path) -> None:
    step = _step("map", source="beads.tif", output="beads.npz")
    recipe = rc.read_recipe(_write(tmp_path, _recipe(step)))
    assert rc._skipped(recipe, recipe.steps[0], 1)
    assert not rc._skipped(recipe, recipe.steps[0], 0)
