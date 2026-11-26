# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The safety rules of the merge-queue workflow and its candidate branches (#1267, #1268).

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
#: Observe, and every join or leave: refs and branches, and read pull requests. Nothing else.
TOKEN = {"permission-contents": "write", "permission-workflows": "write"}
TOKEN |= {"permission-pull-requests": "read"}
#: Mode on (#1268): also the label and comment, the merge-queue status and the check runs. The
#: docs page lists the same permissions with the call that needs each.
TOKEN_ON = TOKEN | {"permission-pull-requests": "write", "permission-statuses": "write"}
TOKEN_ON |= {"permission-checks": "read"}
DOCS = WORKFLOWS.parents[1] / "docs" / "maintainers" / "merge-queue.md"


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
    assert len(uses) == 5
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


def _tokens(job: dict) -> list[tuple[str | None, dict]]:
    steps = [s for s in job["steps"] if "create-github-app-token" in s.get("uses", "")]
    drop = ("client-id", "private-key")
    return [(s.get("if"), {k: v for k, v in s["with"].items() if k not in drop}) for s in steps]


def test_the_app_token_writes_refs_only_and_more_only_in_mode_on() -> None:
    assert _tokens(JOBS["record"]) == [(None, TOKEN)], "join and leave write only the request"
    assert _tokens(JOBS["reconcile"]) == [
        ("${{ needs.mode.outputs.mode == 'observe' }}", TOKEN),
        ("${{ needs.mode.outputs.mode == 'on' }}", TOKEN_ON),
    ]
    (step,) = [s for s in JOBS["reconcile"]["steps"] if "merge_queue.py" in s.get("run", "")]
    assert step["env"]["GH_TOKEN"] == "${{ steps.app.outputs.token || steps.app_on.outputs.token }}"
    assert step["env"]["MODE"] == "${{ needs.mode.outputs.mode }}"
    assert 'build --mode "$MODE"' in step["run"]


def test_the_docs_page_lists_the_permissions_mode_on_asks_for() -> None:
    text = DOCS.read_text(encoding="utf-8")
    section = text.split("## App permissions", 1)[1].split("\n## ", 1)[0]
    rows = dict(re.findall(r"^\| (\w[\w ]*?) \| (read|write) \|", section, re.M))
    names = {"contents": "Contents", "workflows": "Workflows", "pull-requests": "Pull requests"}
    names |= {"statuses": "Commit statuses", "checks": "Checks"}
    expected = {names[k.removeprefix("permission-")]: v for k, v in TOKEN_ON.items()}
    assert rows == expected | {"Metadata": "read"}


def test_reconcile_has_one_group_and_is_never_cancelled() -> None:
    assert "concurrency" not in QUEUE_DOC, "a workflow group would make a join wait"
    reconcile, record = JOBS["reconcile"]["concurrency"], JOBS["record"]["concurrency"]
    assert reconcile == {"group": "merge-queue-reconcile", "cancel-in-progress": False}
    # A waiting join or leave is never replaced by a later one for the same pull request.
    assert record == {**reconcile, "group": "merge-queue-pr-${{ inputs.pr }}", "queue": "max"}


def test_every_job_that_can_write_waits_for_observe_or_on() -> None:
    mode = JOBS["mode"]
    assert mode["permissions"] == {} and "environment" not in mode
    assert not any("uses" in step for step in mode["steps"]), "mode makes no token"
    for job_id, job in JOBS.items():
        if job_id != "mode":
            condition = " ".join(job["if"].split())
            assert "mode" in job["needs"], job_id
            assert "needs.mode.outputs.mode == 'observe'" in condition, job_id
            assert "needs.mode.outputs.mode == 'on'" in condition, job_id
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
        ({"MODE": "on"}, 0, "mode on"),
        ({"MODE": "Observe"}, 1, "use off, observe or on"),
        ({"MODE": "observe", "REF": "refs/heads/mq/pr-1"}, 1, "only from main"),
    ],
)
def test_the_mode_job_lets_only_observe_or_on_on_main_reach_a_write(
    tmp_path: Path, env: dict[str, str], status: int, said: str
) -> None:
    got, output, summary = _mode(tmp_path, **env)
    mode = (env["MODE"].strip() or "off") if status == 0 else ""
    assert (got, output) == (status, mode and f"mode={mode}\n"), "a failed job skips the rest"
    assert said in summary


def _called(workflow: dict) -> list[dict]:
    """``workflow`` and each local reusable workflow its jobs call."""
    found = [workflow]
    for job in workflow["jobs"].values():
        if str(job.get("uses", "")).startswith("./.github/workflows/"):
            found.append(_load(WORKFLOWS.parents[1] / job["uses"]))
    return found


def test_a_candidate_run_gets_no_secret_or_grant_its_pull_request_run_lacks() -> None:
    """Criterion of #1268: a push to `mq/**` gets nothing a same-repository `pull_request` lacks.

    It holds because these workflows name no secret but `GITHUB_TOKEN`, use no environment (whose
    secrets a push could reach), and declare every permission as a literal, so it cannot differ
    by event; and the one job that writes (the scheduled-failure report) acts on a push only
    when the ref is the default branch, never on a candidate.
    """
    for name in REQUIRED:
        for workflow in _called(_load(WORKFLOWS / name)):
            text = yaml.safe_dump(workflow)
            assert set(re.findall(r"secrets\.(\w+)", text)) <= {"GITHUB_TOKEN"}, name
            for job_id, job in workflow["jobs"].items():
                assert "environment" not in job, (name, job_id)
                scopes = [workflow.get("permissions", {}), job.get("permissions", {})]
                for scope in scopes:
                    values = scope.values() if isinstance(scope, dict) else [scope]
                    assert all(v in ("read", "write", "none") for v in values), (name, job_id)
                # A caller job passes its grant on; the called job, checked too, holds the gate.
                if "write" in (job.get("permissions") or {}).values() and "uses" not in job:
                    condition = " ".join(str(job.get("if", "")).split())
                    branch = "github.ref == format('refs/heads/{0}', github.event.repository"
                    assert "github.event_name == 'push' && " + branch in condition, job_id
