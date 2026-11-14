# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline tests for the scheduled-failure report (#697).

The first half drives ``scripts/report_scheduled_failure.py`` against an in-memory GitHub (the
injected transport), so the acceptance criteria are behaviours here: a failed scheduled run
opens exactly one issue with the run URL and the failed step; a second failure comments on it
and opens no second issue; a pull-request run writes nothing. An autouse fixture makes the real
``urlopen`` raise, so nothing here can reach the network.

The second half reads the workflows and holds every scheduled one to the caller shape that
``.github/workflows/scheduled-failure.yml`` documents, so a new scheduled job from #695 that
forgets the step fails here, and holds the issue-writing job to ``issues: write`` alone.
"""

from __future__ import annotations

import ast
import datetime as dt
import importlib.util
import json
import re
import sys
import urllib.request
from pathlib import Path

import pytest
import yaml  # provided by the base conda-lock (a mkdocs dependency)

_REPO = Path(__file__).resolve().parents[1]
SCRIPT = _REPO / "scripts" / "report_scheduled_failure.py"
WORKFLOWS = _REPO / ".github" / "workflows"
REPORT_WORKFLOW = WORKFLOWS / "scheduled-failure.yml"
REPORT_USES = "./.github/workflows/scheduled-failure.yml"
ORACLE_FORM = _REPO / ".github" / "ISSUE_TEMPLATE" / "validation-oracle-failure.yml"
#: Scheduled workflows the issue leaves out of scope (#697 "Scope and non-goals"): the reaper
#: writes issues itself, and the two audits are a later change.
EXEMPT = {"agent-reaper.yml", "deps-audit.yml", "scorecard.yml"}
#: The four the acceptance criteria name, and the later scheduled jobs of #695.
NAMED = {
    "sidecar.yml",
    "sidecar-upstream.yml",  # #704
    "deep.yml",
    "large-fixtures.yml",
    "docs-liveness.yml",
    "reference-data.yml",  # #696
}
REPORT_PERMISSIONS = {"actions": "read", "contents": "read", "issues": "write"}

_spec = importlib.util.spec_from_file_location("report_scheduled_failure", SCRIPT)
assert _spec is not None and _spec.loader is not None
rsf = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = rsf  # @dataclass resolves its annotations through sys.modules
_spec.loader.exec_module(rsf)

API = "https://api.github.com"
REPO = "bioedca/tether"
SHA = "4d1aaef619e0f754075b4d1dd3b7fd2daed01672"
NOW = dt.datetime(2026, 11, 11, 10, 0, tzinfo=dt.UTC)
FAILED_LOG = "\n".join(
    [f"2026-11-11T06:40:{i % 60:02d}.0000000Z line {i}" for i in range(80)]
    + ["2026-11-11T06:41:00.0000000Z ##[error]Process completed with exit code 1."]
    + [f"2026-11-11T06:41:01.0000000Z cleanup {i}" for i in range(30)]
)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a test reached the network")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)


BOT_USER = {"login": "github-actions[bot]", "type": "Bot"}  # the author on the drill issue #766


class FakeGitHub:
    """The few REST endpoints the report uses, kept in memory."""

    def __init__(self, issues: list[dict] | None = None, log: str | None = FAILED_LOG) -> None:
        self.issues: list[dict] = issues or []
        self.comments: dict[int, list[dict]] = {}
        self.log = log
        self.jobs_status = 200
        self.now = NOW
        self.calls: list[tuple[str, str]] = []
        self.jobs = [
            {"id": 7, "name": "report / issue", "status": "in_progress", "conclusion": None},
            {
                "id": 8,
                "name": "sidecar / parity",
                "status": "completed",
                "conclusion": "failure",
                "steps": [
                    {"name": "Set up job", "conclusion": "success"},
                    {"name": "Assert parity", "conclusion": "failure"},
                    {"name": "Complete job", "conclusion": "success"},
                ],
            },
        ]

    def __call__(self, method: str, url: str, body: bytes | None):
        self.calls.append((method, url))
        path = url.split(f"/repos/{REPO}", 1)[1]
        stamp = self.now.strftime("%Y-%m-%dT%H:%M:%SZ")
        if method == "GET" and path.startswith("/issues?"):
            return 200, json.dumps([i for i in self.issues if i["state"] == "open"]).encode(), {}
        if method == "POST" and path == "/issues":
            new = dict(json.loads(body or b"{}"), number=len(self.issues) + 1, state="open")
            new["user"] = dict(BOT_USER)
            self.issues.append(new)
            return 201, json.dumps(new).encode(), {}
        if found := re.fullmatch(r"/issues/(\d+)/comments(\?.*)?", path):
            thread = self.comments.setdefault(int(found.group(1)), [])
            if method == "POST":
                new_id = 1 + sum(len(c) for c in self.comments.values())
                text = json.loads(body or b"{}")["body"]
                thread.append({"id": new_id, "body": text, "created_at": stamp, "user": BOT_USER})
                return 201, b"{}", {}
            return 200, json.dumps(thread).encode(), {}
        if method == "PATCH" and (found := re.fullmatch(r"/issues/comments/(\d+)", path)):
            (comment,) = [
                c for th in self.comments.values() for c in th if c["id"] == int(found[1])
            ]
            comment["body"] = json.loads(body or b"{}")["body"]
            return 200, b"{}", {}
        if re.fullmatch(r"/actions/runs/\d+/attempts/\d+/jobs\?per_page=100", path):
            return self.jobs_status, json.dumps({"jobs": self.jobs}).encode(), {}
        if path == "/actions/jobs/8/logs":
            return (200, self.log.encode(), {}) if self.log is not None else (404, b"", {})
        raise AssertionError(f"unexpected call {method} {path}")

    def writes(self) -> list[tuple[str, str]]:
        return [call for call in self.calls if call[0] != "GET"]


def make_run(**changes: object):
    fields = {
        "server": "https://github.com",
        "repo": REPO,
        "run_id": "100",
        "attempt": "1",
        "sha": SHA,
        "event": "schedule",
        "ref_name": "main",
        "default_branch": "main",
        "job_name": "sidecar / parity",
        "result": "failure",
    }
    fields.update(changes)
    return rsf.Run(**fields)


def report(gh: FakeGitHub, now: dt.datetime = NOW, **changes: object) -> str:
    gh.now = now
    return rsf.report(make_run(**changes), rsf.Api(API, REPO, gh), now)


# --- behaviour: the acceptance criteria -----------------------------------------------------


def test_a_failed_scheduled_run_opens_one_issue_with_the_run_url_and_the_failed_step() -> None:
    gh = FakeGitHub()
    assert report(gh) == "opened #1"
    (issue,) = gh.issues
    assert issue["title"] == "ci(scheduled): sidecar / parity failed on main"
    assert issue["labels"] == ["type:ci", "status:backlog"]
    body = issue["body"]
    assert "https://github.com/bioedca/tether/actions/runs/100/attempts/1" in body
    assert f"https://github.com/bioedca/tether/commit/{SHA}" in body
    assert "- Failed step: `Assert parity`" in body
    assert "##[error]Process completed with exit code 1." in body
    assert body.startswith("<!-- tether-scheduled-failure:opened run=100/1 -->")


def test_a_second_failure_comments_on_the_same_issue_and_opens_no_second_one() -> None:
    gh = FakeGitHub()
    report(gh)
    assert report(gh, run_id="101") == "commented on #1 (failed)"
    assert len(gh.issues) == 1
    (comment,) = gh.comments[1]
    assert "actions/runs/101/attempts/1" in comment["body"]
    assert "`Assert parity`" in comment["body"]
    excerpt = rsf.fenced(rsf.log_tail(FAILED_LOG))
    assert excerpt in gh.issues[0]["body"]
    assert excerpt in comment["body"], "a repeat failure carries the same excerpt (#852)"


#: The end of a failed ``sidecar-upstream.yml`` job log (#704): its last step names the upstream
#: commit under test and the first failing comparison as the last two error lines.
UPSTREAM_SHA = "10f4230e8d6c1b5a9f2e7d3c4b1a0f9e8d7c6b5a"


def _upstream_log(sha: str, comparison: str) -> str:
    stamp = "2026-11-16T08:40:00.0000000Z"
    return "\n".join(
        [f"{stamp} tests/test_sidecar_parity.py F" for _ in range(10)]
        + [f"{stamp} ##[error]Process completed with exit code 1."]
        + [f'{stamp} ##[group]Run echo "::error::Upstream tMAVEN commit under test: $SHA"']
        + [f"{stamp} ##[error]Upstream tMAVEN commit under test: {sha}"]
        + [f"{stamp} ##[error]First failing comparison: FAILED {comparison}"]
        + [f"{stamp} Post job cleanup." for _ in range(5)]
    )


def test_a_repeat_upstream_failure_names_its_own_commit_and_comparison() -> None:
    """The Tether commit stays the same while the upstream one moves (#852, CodeRabbit on #802)."""
    first = "tests/test_sidecar_parity.py::test_vbconhmm_elbo - relative ELBO 2.78 > 0.01"
    gh = FakeGitHub(log=_upstream_log("a" * 40, first))
    assert report(gh, job_name="sidecar / upstream tMAVEN") == "opened #1"
    second = "tests/test_sidecar_parity.py::test_ebfret_rates - rate 0.42 outside 0.05"
    gh.log = _upstream_log(UPSTREAM_SHA, second)
    when = NOW + dt.timedelta(days=7)
    assert report(gh, when, run_id="101", job_name="sidecar / upstream tMAVEN") == (
        "commented on #1 (failed)"
    )
    (comment,) = gh.comments[1]
    body = comment["body"]
    assert body.startswith(
        "<!-- tether-scheduled-failure:failed run=101/1 as=failed -->\nFailed again."
    )
    assert f"commit/{SHA}" in body, "the Tether commit is the same as in the issue"
    tail = body.rsplit("```text\n", 1)[1].split("\n```", 1)[0].splitlines()
    assert tail[-2:] == [
        f"##[error]Upstream tMAVEN commit under test: {UPSTREAM_SHA}",
        f"##[error]First failing comparison: FAILED {second}",
    ]
    assert "a" * 40 not in body, "the excerpt is this run's log, not the first one"


def test_an_edited_failure_comment_carries_the_excerpt_and_a_pass_comment_none() -> None:
    gh = FakeGitHub()
    report(gh)  # opened
    assert report(gh, run_id="101", result="success") == "commented on #1 (passed)"
    assert "```" not in gh.comments[1][0]["body"], "a pass comment has no log"
    assert report(gh, run_id="102") == "commented on #1 (failed)"
    assert report(gh, run_id="103", result="success") == "updated a comment on #1 (passed)"
    assert "```" not in gh.comments[1][-1]["body"], "an edit to passed drops the log"
    assert report(gh, run_id="104") == "updated a comment on #1 (failed)"
    latest = gh.comments[1][-1]["body"]
    assert "runs/104/attempts/1" in latest
    assert rsf.fenced(rsf.log_tail(FAILED_LOG)) in latest
    assert "This comment was edited to show the latest run" in latest


def test_a_repeat_failure_still_comments_when_its_log_cannot_be_read() -> None:
    gh = FakeGitHub()
    report(gh)
    gh.log = None
    assert report(gh, NOW + dt.timedelta(days=1), run_id="101") == "commented on #1 (failed)"
    assert "The job log could not be read" in gh.comments[1][0]["body"]


def test_failure_comments_are_at_most_one_each_day() -> None:
    gh = FakeGitHub()
    report(gh)
    report(gh, run_id="101")
    assert report(gh, run_id="102").startswith("no report: #1 already has a failure comment")
    assert len(gh.comments[1]) == 1
    assert report(gh, NOW + dt.timedelta(days=1), run_id="103") == "commented on #1 (failed)"
    assert len(gh.comments[1]) == 2


@pytest.mark.parametrize(
    "event", ["pull_request", "pull_request_target", "merge_group", "workflow_run", "issues"]
)
def test_a_pull_request_or_other_event_never_calls_the_api(event: str) -> None:
    gh = FakeGitHub()
    assert report(gh, event=event).startswith("no report:")
    assert gh.calls == []


def test_a_push_reports_only_on_the_default_branch() -> None:
    gh = FakeGitHub()
    assert report(gh, event="push", ref_name="feature").startswith("no report: a push to feature")
    assert gh.calls == []
    assert report(gh, event="push") == "opened #1"


def test_a_manual_run_reports_only_as_a_drill_and_never_on_the_real_issue() -> None:
    gh = FakeGitHub()
    report(gh)  # the real issue
    assert report(gh, event="workflow_dispatch").startswith("no report:")
    assert gh.writes() == [("POST", f"{API}/repos/{REPO}/issues")]
    assert report(gh, event="workflow_dispatch", drill=True, ref_name="test") == "opened #2"
    assert gh.issues[1]["title"] == "ci(drill): sidecar / parity failed on test"
    assert "(a drill, not a real schedule)" in gh.issues[1]["body"]
    assert gh.comments == {}


def test_the_next_pass_comments_once_and_never_closes_the_issue() -> None:
    gh = FakeGitHub()
    report(gh)
    assert report(gh, run_id="101", result="success") == "commented on #1 (passed)"
    assert report(gh, run_id="102", result="success").startswith("no report: #1 already says")
    assert len(gh.comments[1]) == 1
    assert gh.issues[0]["state"] == "open"
    assert not any(method == "PATCH" for method, _ in gh.calls)


def test_a_flapping_job_posts_two_comments_a_day_and_the_last_report_is_always_true() -> None:
    """Failure, pass, failure, pass... in one day: the post-merge sidecar case (Codex, #765)."""
    gh = FakeGitHub()
    report(gh)  # opened
    expected = [
        "commented",
        "commented",
        "updated a comment",
        "updated a comment",
        "updated a comment",
    ]
    for i, verb in enumerate(expected):
        result = "success" if i % 2 == 0 else "failure"
        outcome = report(gh, run_id=str(101 + i), result=result)
        assert outcome.startswith(f"{verb} on #1"), (i, outcome)
        latest = gh.comments[1][-1]["body"]
        assert latest.startswith(
            "<!-- tether-scheduled-failure:" + ("passed" if result == "success" else "failed")
        ), (i, latest)
        assert f"runs/{101 + i}/attempts/1" in latest
    assert len(gh.comments[1]) == 2, "one new failure comment and one new pass comment a day"
    assert report(gh, NOW + dt.timedelta(days=1), run_id="200") == "commented on #1 (failed)"


def test_a_pass_with_no_open_issue_writes_nothing() -> None:
    gh = FakeGitHub()
    assert report(gh, result="success") == "no report: passed, and no open issue"
    assert gh.writes() == []


def test_a_timed_out_job_reports_and_a_skipped_one_does_not() -> None:
    gh = FakeGitHub()
    gh.jobs[1]["conclusion"] = "cancelled"
    gh.jobs[1]["steps"][1]["conclusion"] = "cancelled"
    assert report(gh, result="skipped") == "no report: the watched job was skipped"
    assert gh.calls == []
    assert report(gh, result="cancelled") == "opened #1"
    assert "(result `cancelled`)" in gh.issues[0]["body"]
    assert "- Failed step: `Assert parity`" in gh.issues[0]["body"]


def test_the_issue_still_opens_when_the_jobs_or_the_log_cannot_be_read() -> None:
    gh = FakeGitHub(log=None)
    report(gh)
    assert "The job log could not be read" in gh.issues[0]["body"]
    gh = FakeGitHub()
    gh.jobs_status = 403
    report(gh)
    assert "- Failed step: not known; see the run." in gh.issues[0]["body"]


def test_only_a_leading_marker_counts_as_a_report() -> None:
    """A marker quoted inside a log or by a person does not change the issue's state."""
    gh = FakeGitHub(issues=[{"number": 1, "title": make_run().title, "body": "", "state": "open"}])
    gh.comments[1] = [{"body": "log:\n<!-- tether-scheduled-failure:passed run=9/1 -->"}]
    assert report(gh, result="success") == "commented on #1 (passed)"


def test_a_marker_from_anyone_but_the_reporter_is_not_a_report() -> None:
    """A pasted marker cannot change the state or use up the limit (CodeRabbit, #765)."""
    gh = FakeGitHub()
    report(gh)  # opened
    assert report(gh, run_id="101", result="success") == "commented on #1 (passed)"
    person = {"login": "someone", "type": "User"}
    copied = "<!-- tether-scheduled-failure:failed run=999/1 as=failed -->\nnot a report"
    gh.comments[1].append({"id": 50, "body": copied, "created_at": NOW.isoformat(), "user": person})
    assert report(gh, run_id="102") == "commented on #1 (failed)"
    copied = "<!-- tether-scheduled-failure:passed run=998/1 -->\nnot a report"
    gh.comments[1].append({"id": 51, "body": copied, "created_at": NOW.isoformat(), "user": person})
    assert report(gh, run_id="103", result="success") == "updated a comment on #1 (passed)"
    assert gh.comments[1][-1]["body"] == copied, "never an edit of a person's comment"
    assert "runs/103/attempts/1" in gh.comments[1][-2]["body"]


def test_the_oldest_open_issue_with_the_exact_title_is_the_one() -> None:
    title = make_run().title
    gh = FakeGitHub(
        issues=[
            {"number": 5, "title": title, "body": "", "state": "open"},
            {"number": 3, "title": title, "body": "", "state": "open", "pull_request": {}},
            {"number": 4, "title": title + " (old)", "body": "", "state": "open"},
            {"number": 2, "title": title, "body": "", "state": "closed"},
        ]
    )
    assert report(gh) == "commented on #5 (failed)"


def test_oracle_jobs_open_from_the_validation_oracle_failure_form() -> None:
    gh = FakeGitHub()
    report(gh, oracle="(b) Idealization parity", fixture_tier="gated large")
    issue = gh.issues[0]
    form = yaml.safe_load(ORACLE_FORM.read_text(encoding="utf-8"))
    assert issue["labels"] == form["labels"] == list(rsf.ORACLE_LABELS)
    headings = re.findall(r"^### (.+)$", issue["body"], flags=re.MULTILINE)
    labels = [f["attributes"]["label"] for f in form["body"] if f["type"] != "markdown"]
    assert headings == labels
    assert (
        "### Which oracle failed (PRD §8 NFR-VALID a–g)\n\n(b) Idealization parity"
        in (issue["body"])
    )
    assert "### Fixture tier\n\ngated large" in issue["body"]
    reproduction = issue["body"].split("### Reproduction / harness", 1)[1]
    assert "actions/runs/100/attempts/1" in reproduction.split("###", 1)[0]
    for field in form["body"]:
        if field["type"] == "checkboxes":
            for option in field["attributes"]["options"]:
                # Unticked: the report step cannot attest what only a person can check.
                assert f"- [ ] {option['label']}\n" in issue["body"] + "\n", option["label"]
    assert "- [x]" not in issue["body"]


# --- the log excerpt ----------------------------------------------------------------------


def test_the_log_excerpt_ends_at_the_last_error_without_timestamps() -> None:
    tail = rsf.log_tail("﻿" + FAILED_LOG)
    assert len(tail) == rsf.LOG_LINES
    assert tail[-1] == "##[error]Process completed with exit code 1."
    assert tail[0] == "line 31"
    assert not any("cleanup" in line for line in tail)
    assert rsf.log_tail("2026-11-11T06:40:00Z a\nb") == ["a", "b"], "no error: the plain tail"


def test_the_log_excerpt_cannot_mention_anyone_or_break_its_fence() -> None:
    tail = rsf.log_tail("\x1b[31mping @bioedca\x1b[0m\n```\n" + "x" * 1000)
    assert tail[0] == "ping @⁠bioedca"
    assert len(tail[2]) == rsf.MAX_LINE_CHARS
    fence = rsf.fenced(["a ``` b", "````"])
    assert fence.startswith("`````text\n") and fence.endswith("\n`````")


# --- plumbing -----------------------------------------------------------------------------


def test_pages_follow_the_next_link() -> None:
    def transport(method: str, url: str, body: bytes | None):
        if url.endswith("page=2"):
            return 200, b"[3]", {}
        return 200, b"[1, 2]", {"Link": f'<{API}/repos/{REPO}/issues?page=2>; rel="next"'}

    assert rsf.Api(API, REPO, transport).pages("/issues?state=open") == [1, 2, 3]

    def elsewhere(method: str, url: str, body: bytes | None):
        assert url.startswith(API), f"the token went to {url}"
        return 200, b"[1]", {"Link": '<https://example.org/issues?page=2>; rel="next"'}

    assert rsf.Api(API, REPO, elsewhere).pages("/issues?state=open") == [1]


def test_the_token_is_never_copied_onto_a_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[urllib.request.Request] = []

    class Response:
        status = 200
        headers: dict[str, str] = {}

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def read(self) -> bytes:
            return b"[]"

    monkeypatch.setattr(
        urllib.request, "urlopen", lambda req, timeout: seen.append(req) or Response()
    )
    rsf.urllib_transport("secret")("GET", f"{API}/repos/{REPO}/actions/jobs/8/logs", None)
    (request,) = seen
    assert request.unredirected_hdrs["Authorization"] == "Bearer secret"
    assert "Authorization" not in request.headers, "urllib copies `headers` onto a redirect"


def test_main_reads_the_runner_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"repository": {"default_branch": "main"}}), encoding="utf-8")
    summary = tmp_path / "summary.md"
    gh = FakeGitHub()
    monkeypatch.setattr(rsf, "urllib_transport", lambda token: gh)
    env = {
        "GH_TOKEN": "t",
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_REPOSITORY": REPO,
        "GITHUB_RUN_ID": "100",
        "GITHUB_RUN_ATTEMPT": "2",
        "GITHUB_SHA": SHA,
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_EVENT_PATH": str(event),
        "GITHUB_REF_NAME": "main",
        "GITHUB_STEP_SUMMARY": str(summary),
        "REPORT_JOB_NAME": "deep / smoke",
        "REPORT_RESULT": "failure",
        "REPORT_DRILL": "false",
    }
    assert rsf.main(env) == 0
    assert gh.issues[0]["title"] == "ci(scheduled): deep / smoke failed on main"
    assert "runs/100/attempts/2" in gh.issues[0]["body"]
    assert "opened #1" in summary.read_text(encoding="utf-8")


def test_the_script_imports_only_the_standard_library() -> None:
    imported: set[str] = set()
    for node in ast.walk(ast.parse(SCRIPT.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            imported.add(node.module.partition(".")[0])
    assert {n for n in imported if n not in sys.stdlib_module_names} <= {"__future__"}


# --- the workflows ------------------------------------------------------------------------


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _triggers(workflow: dict) -> dict:
    on = workflow.get("on", workflow.get(True))  # YAML 1.1 reads a bare `on:` key as True
    return on if isinstance(on, dict) else {on: None}


def _report_jobs(workflow: dict) -> dict[str, dict]:
    jobs = workflow.get("jobs") or {}
    return {k: v for k, v in jobs.items() if isinstance(v, dict) and v.get("uses") == REPORT_USES}


def _scheduled() -> dict[str, dict]:
    return {
        path.name: workflow
        for path in sorted(WORKFLOWS.glob("*.yml"))
        if "schedule" in _triggers(workflow := _load(path))
    }


def test_every_scheduled_workflow_calls_the_report_with_the_documented_shape() -> None:
    covered = set()
    for name, workflow in _scheduled().items():
        if name in EXEMPT:
            continue
        reports = _report_jobs(workflow)
        assert len(reports) == 1, f"{name}: a scheduled workflow needs one job using {REPORT_USES}"
        ((report_id, job),) = reports.items()
        watched = [k for k in workflow["jobs"] if k != report_id]
        needs = job.get("needs")
        assert (needs if isinstance(needs, list) else [needs]) == watched, name
        assert job.get("if") == "${{ !cancelled() }}", f"{name}: `!cancelled()`, not always()"
        assert job.get("permissions") == REPORT_PERMISSIONS, name
        if len(watched) == 1:
            (watched_id,) = watched
            assert job["with"]["result"] == f"${{{{ needs.{watched_id}.result }}}}", name
            expected_name = workflow["jobs"][watched_id].get("name", watched_id)
            assert job["with"]["job-name"] == expected_name, name
        covered.add(name)
    assert covered >= NAMED, f"not wired: {sorted(NAMED - covered)}"


def test_the_exemptions_are_real_scheduled_workflows_and_stay_out() -> None:
    scheduled = _scheduled()
    assert set(scheduled) >= EXEMPT, "an exemption that is no longer scheduled is stale"
    assert all(not _report_jobs(scheduled[name]) for name in EXEMPT)


def test_the_report_job_writes_issues_and_nothing_else() -> None:
    workflow = _load(REPORT_WORKFLOW)
    assert set(_triggers(workflow)) == {"workflow_call"}
    assert workflow["permissions"] == {"contents": "read"}
    ((_, job),) = workflow["jobs"].items()
    assert job["permissions"] == REPORT_PERMISSIONS
    for step in job["steps"]:
        if "uses" in step:
            assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", step["uses"]), step["uses"]
    (checkout,) = [s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/checkout")]
    assert checkout["with"]["persist-credentials"] is False
    assert checkout["with"]["sparse-checkout"] == "scripts/report_scheduled_failure.py"
    assert any(s.get("run") == "python3 scripts/report_scheduled_failure.py" for s in job["steps"])


def test_no_other_job_in_a_caller_gains_a_write_permission() -> None:
    for name in NAMED:
        workflow = _load(WORKFLOWS / name)
        assert workflow["permissions"] == {"contents": "read"}, name
        for job_id, job in workflow["jobs"].items():
            if job_id in _report_jobs(workflow):
                continue
            granted = (job.get("permissions") or {}).values()
            assert "write" not in granted, f"{name}:{job_id} must not write"


def test_the_report_job_never_runs_for_a_pull_request() -> None:
    ((_, job),) = _load(REPORT_WORKFLOW)["jobs"].items()
    condition = " ".join(job["if"].split())
    assert condition == (
        "github.event_name == 'schedule' || (github.event_name == 'push' && github.ref == "
        "format('refs/heads/{0}', github.event.repository.default_branch)) || "
        "(github.event_name == 'workflow_dispatch' && inputs.drill)"
    )


def test_oracle_callers_name_options_of_the_form() -> None:
    form = {f.get("id"): f for f in _load(ORACLE_FORM)["body"]}
    oracle_options = form["oracle"]["attributes"]["options"]
    tier_options = form["fixture-tier"]["attributes"]["options"]
    oracles = {}
    for name in NAMED:
        for job in _report_jobs(_load(WORKFLOWS / name)).values():
            given = job["with"].get("oracle")
            if given:
                assert given in oracle_options, f"{name}: {given!r}"
                assert job["with"].get("fixture-tier") in tier_options, name
                oracles[name] = given
    assert set(oracles) == {"sidecar.yml", "large-fixtures.yml"}


def test_docs_liveness_carries_the_drill() -> None:
    workflow = _load(WORKFLOWS / "docs-liveness.yml")
    drill = _triggers(workflow)["workflow_dispatch"]["inputs"]["drill"]
    assert drill["default"] == "none" and set(drill["options"]) == {"none", "fail", "pass"}
    (fail,) = [s for s in workflow["jobs"]["docs-liveness"]["steps"] if "drill" in str(s.get("if"))]
    assert fail["if"] == "inputs.drill == 'fail'" and fail["run"].rstrip().endswith("false")
    (job,) = _report_jobs(workflow).values()
    assert job["with"]["drill"] == "${{ inputs.drill == 'fail' || inputs.drill == 'pass' }}"
