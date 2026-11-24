# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The safety rules of the merge-queue workflow and its candidate branches (#1267, ADR-0135).

The mode step runs here as the runner runs it. ``tests/test_merge_queue.py`` tests the script.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml  # provided by the base conda-lock (a mkdocs dependency)

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
QUEUE = WORKFLOWS / "merge-queue.yml"
REQUIRED = ("ci.yml", "sidecar.yml", "schema-guard.yml")
KEY = "MERGE_QUEUE_APP_PRIVATE_KEY"
#: Refs and branches, and read pull requests. No status, issue, label or merge (part 2, #1268).
TOKEN = {"permission-contents": "write", "permission-workflows": "write"}
TOKEN |= {"permission-pull-requests": "read"}


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _on(workflow: dict) -> dict:
    return workflow.get("on", workflow.get(True))  # YAML 1.1 reads a bare `on:` key as True


def _pushes(workflow: dict, branch: str) -> bool:
    """Whether a push to ``branch`` runs ``workflow``. ``**`` matches ``/`` and ``*`` does not."""
    if "push" not in _on(workflow):
        return False
    push = _on(workflow)["push"] or {}  # a bare `push:` runs on every branch

    def hit(patterns: list[str]) -> bool:
        regexes = (re.escape(p).replace(r"\*\*", ".*").replace(r"\*", "[^/]*") for p in patterns)
        return any(re.fullmatch(r, branch) for r in regexes)

    if "branches" in push:
        return hit(push["branches"])
    if "branches-ignore" in push:
        return not hit(push["branches-ignore"])
    return "tags" not in push


QUEUE_DOC = _load(QUEUE)
JOBS = QUEUE_DOC["jobs"]


def test_the_required_workflows_and_no_advisory_one_run_on_a_candidate() -> None:
    assert not _pushes({"on": {"push": {"branches": ["mq/*"]}}}, "mq/a/b")
    for path in sorted(WORKFLOWS.glob("*.yml")):
        workflow = _load(path)
        assert _pushes(workflow, "mq/pr-1267") == (path.name in REQUIRED), path.name
        if path.name in REQUIRED:
            assert _pushes(workflow, "main") and "pull_request" in _on(workflow), path.name


def test_the_queue_has_its_four_triggers_and_no_pull_request_one() -> None:
    on = _on(QUEUE_DOC)
    assert set(on) == {"workflow_dispatch", "workflow_run", "push", "schedule"}
    assert set(on["workflow_dispatch"]["inputs"]) == {"action", "pr", "sha", "message"}
    assert on["push"] == {"branches": ["main"]} and on["schedule"]
    run = on["workflow_run"]
    assert sorted(run["workflows"]) == sorted(_load(WORKFLOWS / n)["name"] for n in REQUIRED)
    assert (run["types"], run["branches"]) == (["completed"], ["mq/**"])


def test_every_action_is_pinned_to_a_commit_with_its_version() -> None:
    uses = re.findall(r"^\s*(?:- )?uses: (.*)$", QUEUE.read_text(encoding="utf-8"), re.M)
    assert len(uses) == 4
    for use in uses:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40} # v\d+(\.\d+)*", use), use


def test_the_workflow_token_is_read_only_and_the_key_stays_in_its_environment() -> None:
    for scope in [QUEUE_DOC, *JOBS.values()]:
        assert set(scope.get("permissions", {}).values()) <= {"read"}, "GITHUB_TOKEN stays read"
    readers = [job_id for job_id, job in JOBS.items() if KEY in yaml.safe_dump(job)]
    assert readers == ["record", "reconcile"]
    assert all(JOBS[job_id]["environment"] == "merge-queue" for job_id in readers)
    text = QUEUE.read_text(encoding="utf-8")
    assert set(re.findall(r"secrets\.(\w+)", text)) == {KEY}
    for path in WORKFLOWS.glob("*.yml"):
        assert path == QUEUE or KEY not in path.read_text(encoding="utf-8"), path.name


def test_the_app_token_writes_refs_and_nothing_else() -> None:
    for job in (JOBS["record"], JOBS["reconcile"]):
        (step,) = [s for s in job["steps"] if "create-github-app-token" in s.get("uses", "")]
        given = {k: v for k, v in step["with"].items() if k not in ("client-id", "private-key")}
        assert given == TOKEN


def test_reconcile_has_one_group_and_is_never_cancelled() -> None:
    assert "concurrency" not in QUEUE_DOC, "a workflow group would make a join wait"
    reconcile, record = JOBS["reconcile"]["concurrency"], JOBS["record"]["concurrency"]
    assert reconcile == {"group": "merge-queue-reconcile", "cancel-in-progress": False}
    assert record == {"group": "merge-queue-pr-${{ inputs.pr }}", "cancel-in-progress": False}


def test_every_job_that_can_write_waits_for_the_observe_mode() -> None:
    mode = JOBS["mode"]
    assert mode["permissions"] == {} and "environment" not in mode
    assert not any("uses" in step for step in mode["steps"]), "mode makes no token"
    for job_id, job in JOBS.items():
        if job_id != "mode":
            condition = " ".join(job["if"].split())
            assert "mode" in job["needs"], job_id
            assert "needs.mode.outputs.mode == 'observe'" in condition, job_id
            assert "always()" not in condition, job_id
    assert "needs.mode.result == 'success'" in JOBS["reconcile"]["if"]


def test_the_steps_run_no_pull_request_code_and_no_interpolated_input() -> None:
    commands = []
    for job_id, job in JOBS.items():
        for step in job["steps"]:
            assert "${{" not in step.get("run", ""), f"{job_id}: pass values through env:"
            commands += re.findall(r"merge_queue\.py --repo \S+ (\w+)", step.get("run", ""))
            if step.get("uses", "").startswith("actions/checkout@"):
                assert step["with"]["persist-credentials"] is False, job_id
                assert not {"ref", "repository", "token"} & set(step["with"]), job_id
    assert sorted(commands) == ["build", "join", "leave"]


def _mode(tmp_path: Path, **env: str) -> tuple[int, str, str]:
    (step,) = [s for s in JOBS["mode"]["steps"] if s.get("id") == "mode"]
    output, summary = tmp_path / "output", tmp_path / "summary"
    output.write_text("", encoding="utf-8")
    env = {"REF": "refs/heads/main", **env}
    env |= {"GITHUB_OUTPUT": str(output), "GITHUB_STEP_SUMMARY": str(summary)}
    done = subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-c", step["run"]], env=env, capture_output=True, check=False
    )
    read = (p.read_text(encoding="utf-8") for p in (output, summary))
    return done.returncode, *read


@pytest.mark.parametrize(
    ("env", "status", "said"),
    [  # Off wins: a run that writes nothing may come from any ref.
        ({"MODE": "", "REF": "refs/heads/x"}, 0, "mode off, so this run writes nothing"),
        ({"MODE": " off "}, 0, "mode off"),
        ({"MODE": "observe"}, 0, "mode observe"),
        ({"MODE": "on"}, 1, "#1268"),
        ({"MODE": "Observe"}, 1, "use off, observe or on"),
        ({"MODE": "observe", "REF": "refs/heads/mq/pr-1"}, 1, "only from main"),
    ],
)
def test_the_mode_job_lets_only_observe_on_main_reach_a_write(
    tmp_path: Path, env: dict[str, str], status: int, said: str
) -> None:
    got, output, summary = _mode(tmp_path, **env)
    mode = (env["MODE"].strip() or "off") if status == 0 else ""
    assert (got, output) == (status, mode and f"mode={mode}\n"), "a failed job skips the rest"
    assert said in summary
