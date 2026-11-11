# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Open or update one issue when a scheduled job fails on the default branch (issue #697).

``.github/workflows/scheduled-failure.yml`` runs this after the watched job of each scheduled
workflow. Before it, a scheduled run that broke because of an upstream change, a runner image or
an expired cache had no pull request to show it, and it stayed red on the Actions page only.

What it does, by the watched job's result:

* **failure** -- it looks for an open issue with the workflow's fixed title. If there is none, it
  opens one with the run URL, the commit, the failed step and the last log lines: an oracle job
  gets the fields of the ``validation-oracle-failure`` form, the others get ``type:ci``. If the
  issue is open, it adds one comment with the new run URL.
* **success** -- if the issue is open and its last report is a failure, it adds one comment that
  says so. It never closes the issue: the person or agent who finds the cause closes it.
* **the limit** -- one new failure comment and one new pass comment each day (UTC), so a gate
  that flaps (#311) cannot flood the issue. Past the limit, a run that changes the state edits
  the latest report in place instead, so the issue never says "passed" while the job fails.
* **cancelled** -- the workflow calls this only while the run itself is not cancelled
  (``!cancelled()``), so a cancelled job here is a job that hit its own ``timeout-minutes``, and
  that is a failure. A run that a newer run replaced never gets this far.
* **skipped** -- nothing.

It writes nothing for any event except ``schedule``, a ``push`` to the default branch, or a
``workflow_dispatch`` drill. The workflow's ``if:`` already keeps pull requests out; this is the
second lock. A drill uses its own title prefix, so it can never touch the real issue.

The token goes to the API origin only. It is an *unredirected* header, so the job-log download's
redirect to blob storage does not carry it. Standard library only (the job runs on the bare
runner interpreter). The API is behind an injected transport, so every path is unit-tested
offline in ``tests/test_scheduled_failure_report.py``.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

LOG_LINES = 50
MAX_LINE_CHARS = 300
MAX_PAGES = 30
TIMEOUT_SECONDS = 30
CI_LABELS = ("type:ci", "status:backlog")
#: The labels of ``.github/ISSUE_TEMPLATE/validation-oracle-failure.yml`` (a test keeps them equal).
ORACLE_LABELS = ("type:validation-oracle-failure", "priority:P0", "status:backlog")
REPORTABLE_EVENTS = ("schedule", "push", "workflow_dispatch")
#: Job and step conclusions that count as "this is where it failed".
FAILED = ("failure", "timed_out", "cancelled")
#: ``state`` is what the report says now; ``as`` is what its comment was posted as, which an edit
#: keeps, so the one-a-day limit still counts the comment once.
_MARKER = "<!-- tether-scheduled-failure:{state} run={run}{kind} -->"
_MARKER_RE = re.compile(
    r"<!-- tether-scheduled-failure:(opened|failed|passed) run=\S+?(?: as=(failed|passed))? -->"
)
#: The author of every issue and comment the job token writes (as on the drill issue #766). Only
#: its markers count: anyone can comment on a public issue, and a copied marker must not change
#: the state or use up the daily limit.
REPORTER = "github-actions[bot]"
_STAMP_RE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z ")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_NOT_KNOWN = "Not known yet: the log of the failed step tells it."

#: (heading, value) for each field of the validation-oracle-failure form, in form order.
#: ``{oracle}``, ``{fixture_tier}`` and ``{facts}`` are filled per run. A test reads the form and
#: keeps the headings equal to its labels.
ORACLE_FIELDS = (
    ("Which oracle failed (PRD §8 NFR-VALID a–g)", "{oracle}"),
    ("§9 milestone gate", _NOT_KNOWN),
    ("Measured vs expected (PRD §11.2)", _NOT_KNOWN),
    (
        "Acceptance criteria",
        "- [ ] The failing harness reproduces the regression.\n"
        "- [ ] The frozen tolerance passes with provenance-locked evidence.",
    ),
    (
        "Scope and non-goals",
        "In scope: find and fix the cause of this failure. Out of scope: changing the frozen "
        "tolerance.",
    ),
    ("Size estimate", "unsure — leave it to grooming"),
    ("Risk level", "unsure — leave it to grooming"),
    ("Does this need an architecture decision record?", "unsure — leave it to grooming"),
    ("Dependencies / blockers", "none"),
    ("Execution autonomy", "maintainer decision required"),
    ("Related work / file overlap", "#697 (the report step that opened this issue)"),
    ("Fixture tier", "{fixture_tier}"),
    ("Fixture identifier", _NOT_KNOWN),
    ("Suspected module (PRD §4.2)", "unknown"),
    ("Reproduction / harness", "{facts}"),
    (
        "Required safety attestations",
        "The report step ticks none of these: it looked only for an open issue with the same "
        "title, and no person read the log excerpt. Tick each one when you have checked it.\n\n"
        "- [ ] I searched existing issues and this is not a duplicate.\n"
        "- [ ] I am not reporting a vulnerability publicly; security reports use the private "
        "advisory flow.\n"
        "- [ ] I included no secrets or private, raw, unlicensed, user, or lab data.",
    ),
    (
        "Remediation checks (CLAUDE.md data-gaps / invariants)",
        "- [ ] I will not weaken the oracle or its §11.2 tolerance to make it pass.\n"
        "- [ ] Any new reference data will be defensible (sourced, versioned, provenance + "
        "locking test).\n"
        "- [ ] Parity is statistical — I compared against the frozen tolerance, not a bit-exact "
        "value.",
    ),
)

#: ``(method, url, json body or None) -> (status, body bytes, headers)``
Transport = Callable[[str, str, bytes | None], tuple[int, bytes, Mapping[str, str]]]


class ApiError(RuntimeError):
    """A GitHub API call returned a status the caller did not accept."""


def urllib_transport(token: str) -> Transport:
    def send(method: str, url: str, body: bytes | None) -> tuple[int, bytes, Mapping[str, str]]:
        request = urllib.request.Request(url, data=body, method=method)
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        request.add_header("User-Agent", "tether-scheduled-failure")
        if body is not None:
            request.add_header("Content-Type", "application/json")
        # urllib copies `headers` onto a redirected request but never `unredirected_hdrs`, so
        # the job-log redirect to blob storage goes without the token.
        request.add_unredirected_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                return response.status, response.read(), dict(response.headers)
        except urllib.error.HTTPError as error:
            return error.code, error.read(), dict(error.headers or {})

    return send


class Api:
    def __init__(self, api_url: str, repo: str, transport: Transport) -> None:
        self.origin = api_url.rstrip("/") + "/"
        self.base = f"{self.origin}repos/{repo}"
        self.transport = transport

    def call(self, method: str, path: str, payload: Any = None) -> Any:
        url = self.base + path
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        status, data, _ = self.transport(method, url, body)
        if status not in (200, 201):
            raise ApiError(f"{method} {url} returned HTTP {status}")
        return json.loads(data or b"null")

    def pages(self, path: str) -> list[Any]:
        """Every item of a paginated list, following ``Link: rel="next"``."""
        items: list[Any] = []
        url: str | None = self.base + path
        for _ in range(MAX_PAGES):
            if url is None:
                break
            status, data, headers = self.transport("GET", url, None)
            if status != 200:
                raise ApiError(f"GET {url} returned HTTP {status}")
            page = json.loads(data)
            items.extend(page["jobs"] if isinstance(page, dict) else page)
            link = {k.lower(): v for k, v in headers.items()}.get("link", "")
            found = re.search(r'<([^>]+)>;\s*rel="next"', link)
            # The token follows a next link only on the API origin.
            url = found.group(1) if found and found.group(1).startswith(self.origin) else None
        return items

    def text(self, path: str) -> str | None:
        """A text body, or ``None`` when it cannot be read (a log is a convenience, not a gate)."""
        try:
            status, data, _ = self.transport("GET", self.base + path, None)
        except (OSError, ValueError):
            return None
        return data.decode("utf-8", "replace") if status == 200 else None


@dataclass(frozen=True)
class Run:
    server: str
    repo: str
    run_id: str
    attempt: str
    sha: str
    event: str
    ref_name: str
    default_branch: str
    job_name: str
    result: str
    oracle: str = ""
    fixture_tier: str = ""
    drill: bool = False

    @property
    def url(self) -> str:
        return f"{self.server}/{self.repo}/actions/runs/{self.run_id}/attempts/{self.attempt}"

    @property
    def title(self) -> str:
        prefix = "ci(drill)" if self.drill else "ci(scheduled)"
        return f"{prefix}: {self.job_name} failed on {self.ref_name}"

    def marker(self, state: str, kind: str = "") -> str:
        as_kind = f" as={kind}" if kind else ""
        return _MARKER.format(state=state, run=f"{self.run_id}/{self.attempt}", kind=as_kind)


def run_from_env(env: Mapping[str, str]) -> Run:
    default_branch = ""
    event_path = env.get("GITHUB_EVENT_PATH", "")
    if event_path and os.path.isfile(event_path):
        with open(event_path, encoding="utf-8") as handle:
            repository = json.load(handle).get("repository") or {}
        default_branch = str(repository.get("default_branch") or "")
    return Run(
        server=env["GITHUB_SERVER_URL"],
        repo=env["GITHUB_REPOSITORY"],
        run_id=env["GITHUB_RUN_ID"],
        attempt=env.get("GITHUB_RUN_ATTEMPT", "1"),
        sha=env["GITHUB_SHA"],
        event=env["GITHUB_EVENT_NAME"],
        ref_name=env["GITHUB_REF_NAME"],
        default_branch=default_branch,
        job_name=env["REPORT_JOB_NAME"],
        result=env["REPORT_RESULT"],
        oracle=env.get("REPORT_ORACLE", ""),
        fixture_tier=env.get("REPORT_FIXTURE_TIER", ""),
        drill=env.get("REPORT_DRILL", "false") == "true",
    )


def refusal(run: Run) -> str | None:
    """Why this run must not write, or ``None`` when it may."""
    if run.event not in REPORTABLE_EVENTS:
        return f"a {run.event} run never reports"
    if run.event == "push" and run.ref_name != run.default_branch:
        return f"a push to {run.ref_name} is not a push to the default branch"
    if run.event == "workflow_dispatch" and not run.drill:
        return "a manual run reports only as a drill"
    return None


def log_tail(text: str) -> list[str]:
    """The last :data:`LOG_LINES` lines up to the last ``##[error]`` line, safe to quote.

    A job log ends with the post-job cleanup, so its plain tail hides the error. Timestamps and
    colour codes go; an ``@`` gets a word joiner, so a log line cannot mention a person or a bot.
    """
    rows = [_ANSI_RE.sub("", _STAMP_RE.sub("", ln)) for ln in text.lstrip("\ufeff").splitlines()]
    errors = [i for i, row in enumerate(rows) if row.startswith("##[error]")]
    end = errors[-1] + 1 if errors else len(rows)
    return [
        row[:MAX_LINE_CHARS].replace("@", "@\u2060") for row in rows[max(0, end - LOG_LINES) : end]
    ]


def fenced(lines: list[str]) -> str:
    text = "\n".join(lines)
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}text\n{text}\n{fence}"


def _failed(items: list[dict]) -> dict | None:
    return next((i for i in items if i.get("conclusion") in FAILED), None)


def facts(run: Run, api: Api, *, with_log: bool) -> str:
    """The run URL, event, commit, failed job and step, and (for a new issue) the log tail."""
    try:
        jobs = api.pages(f"/actions/runs/{run.run_id}/attempts/{run.attempt}/jobs?per_page=100")
    except (ApiError, OSError, ValueError):
        jobs = []  # the report still goes out; the run page has the rest
    # This job is still running, so only completed jobs can be the failed one.
    job = _failed([j for j in jobs if j.get("status") == "completed"])
    step = _failed((job or {}).get("steps") or [])
    lines = [
        f"- Run: {run.url}",
        f"- Event: `{run.event}`" + (" (a drill, not a real schedule)" if run.drill else ""),
        f"- Commit: {run.server}/{run.repo}/commit/{run.sha}",
        f"- Failed job: `{job['name'] if job else run.job_name}` (result `{run.result}`)",
        f"- Failed step: `{step['name']}`" if step else "- Failed step: not known; see the run.",
    ]
    if not with_log:
        return "\n".join(lines)
    log = api.text(f"/actions/jobs/{job['id']}/logs") if job else None
    if log:
        lines += ["", f"The last {LOG_LINES} log lines of the failed job, up to its last error:"]
        lines += ["", fenced(log_tail(log))]
    else:
        lines += ["", "The job log could not be read. The run page has it."]
    return "\n".join(lines)


def issue_body(run: Run, facts_text: str) -> str:
    head = (
        f"{run.marker('opened')}\nThe scheduled job **{run.job_name}** failed on "
        f"`{run.ref_name}`. `.github/workflows/scheduled-failure.yml` opened this issue (#697)."
    )
    tail = (
        "A later failure adds at most one comment each day, and the next passing run adds one "
        "comment. This issue does not close itself: close it with the cause."
    )
    if not run.oracle:
        return f"{head}\n\n{facts_text}\n\n{tail}\n"
    fields = {"oracle": run.oracle, "fixture_tier": run.fixture_tier, "facts": facts_text}
    sections = [f"### {heading}\n\n{value.format(**fields)}" for heading, value in ORACLE_FIELDS]
    return "\n\n".join([head, *sections, tail]) + "\n"


def find_issue(run: Run, api: Api) -> dict | None:
    issues = [
        i
        for i in api.pages("/issues?state=open&per_page=100")
        if "pull_request" not in i and i.get("title") == run.title
    ]
    return min(issues, key=lambda i: i["number"]) if issues else None


def report(run: Run, api: Api, now: dt.datetime) -> str:
    """Do the one write this run calls for, and say what it was."""
    why = refusal(run)
    if why:
        return f"no report: {why} (#697)"
    if run.result == "success":
        outcome = "passed"
    elif run.result in ("failure", "cancelled"):
        outcome = "failed"
    else:
        return f"no report: the watched job was {run.result}"
    issue = find_issue(run, api)
    if issue is None:
        if outcome == "passed":
            return "no report: passed, and no open issue"
        labels = list(ORACLE_LABELS if run.oracle else CI_LABELS)
        body = issue_body(run, facts(run, api, with_log=True))
        payload = {"title": run.title, "body": body, "labels": labels}
        created = api.call("POST", "/issues", payload)
        return f"opened #{created['number']}"
    number = issue["number"]
    comments = api.pages(f"/issues/{number}/comments?per_page=100")
    # (state now, posted as, item) for each report by the reporter, oldest first; the issue body
    # says "opened". A marker from anyone else is not a report.
    reports = [
        (m.group(1), m.group(2) or m.group(1), item)
        for item in [{"body": issue.get("body") or "", "user": issue.get("user")}, *comments]
        if (item.get("user") or {}).get("login") == REPORTER
        and (m := _MARKER_RE.match(item.get("body") or ""))
    ]
    failing = not reports or reports[-1][0] != "passed"  # an issue a person opened says "failed"
    if outcome == "passed" and not failing:
        return f"no report: #{number} already says the job passed"
    today = now.astimezone(dt.UTC).date().isoformat()
    posted_today = any(
        kind == outcome and str(item.get("created_at", "")).startswith(today)
        for _, kind, item in reports
    )
    if posted_today and outcome == "failed" and failing:
        return f"no report: #{number} already has a failure comment from {today}"
    if posted_today and "id" in reports[-1][2]:
        # Over the limit but the state changed: edit the latest report in place, so the issue
        # never says "passed" while the job fails (or the reverse), and nobody is notified.
        _, kind, latest = reports[-1]
        body = _comment_body(run, api, outcome, kind, updated=True)
        api.call("PATCH", f"/issues/comments/{latest['id']}", {"body": body})
        return f"updated a comment on #{number} ({outcome})"
    api.call(
        "POST", f"/issues/{number}/comments", {"body": _comment_body(run, api, outcome, outcome)}
    )
    return f"commented on #{number} ({outcome})"


def _comment_body(run: Run, api: Api, state: str, kind: str, *, updated: bool = False) -> str:
    if state == "failed":
        text = f"Failed again.\n\n{facts(run, api, with_log=False)}"
    else:
        text = (
            f"The next run passed: {run.url} on commit {run.server}/{run.repo}/commit/{run.sha}. "
            "Close this issue with the cause."
        )
    if updated:
        text += (
            "\n\nThis comment was edited to show the latest run: the limit is one new failure "
            "comment and one new pass comment each day. The Actions page lists every run."
        )
    return f"{run.marker(state, kind)}\n{text}\n"


def main(env: Mapping[str, str] = os.environ) -> int:
    run = run_from_env(env)
    api_url = env.get("GITHUB_API_URL", "https://api.github.com")
    api = Api(api_url, run.repo, urllib_transport(env["GH_TOKEN"]))
    outcome = report(run, api, dt.datetime.now(dt.UTC))
    print(f"{run.title}: {outcome}")
    summary = env.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(f"## Scheduled-failure report\n\n`{run.title}`: {outcome}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
