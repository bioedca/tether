# SPDX-FileCopyrightText: 2026 The Tether Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Contract tests for the claim reaper.

Every GitHub call goes through a fake transport. The interesting states - a claim that has been
silent for 90 minutes, a PR abandoned for six hours - cannot be produced on demand against a live
repository, and CI must not depend on the network.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".agents" / "bin" / "reaper.py"

_spec = importlib.util.spec_from_file_location("tether_reaper", SCRIPT)
assert _spec is not None and _spec.loader is not None
reaper = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(reaper)

NOW = datetime(2026, 7, 29, 12, 0, 0, tzinfo=UTC)


def _ago(**kw: float) -> str:
    return (NOW - timedelta(**kw)).strftime("%Y-%m-%dT%H:%M:%SZ")


class Fake:
    """Answers by (method, path-prefix) and records every request.

    Defaults mirror the real API's success codes, because the reaper now checks them: a DELETE that
    "succeeds" with 200 instead of 204 would look like a failure and mask a real regression.
    """

    DEFAULTS = {"DELETE": (204, None), "POST": (200, None), "PATCH": (200, None)}

    def __init__(self, routes: dict[tuple[str, str], tuple[int, Any]]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str]] = []

    def __call__(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        self.calls.append((method, path))

        # Model the one side effect the reaper depends on: closing a PR makes subsequent reads
        # return state="closed". Without this the requeue-after-close path would look like
        # "a PR appeared mid-sweep" and be refused - which is the guard working, not a bug.
        if method == "PATCH" and "/pulls/" in path and isinstance(body, dict):
            new_state = body.get("state")
            for _, payload in self.routes.values():
                items = payload if isinstance(payload, list) else [payload]
                for item in items:
                    if isinstance(item, dict) and item.get("number") is not None:
                        item["state"] = new_state or item.get("state")

        best: tuple[int, tuple[int, Any]] | None = None
        for (m, prefix), response in self.routes.items():
            # Longest prefix wins, so "/pulls/99" is not shadowed by "/pulls?head".
            if m == method and path.startswith(prefix) and (best is None or len(prefix) > best[0]):
                best = (len(prefix), response)
        if best is not None:
            return best[1]
        return self.DEFAULTS.get(method, (200, None))

    def did(self, method: str, fragment: str) -> bool:
        return any(m == method and fragment in p for m, p in self.calls)


def _install(
    monkeypatch: pytest.MonkeyPatch, routes: dict[tuple[str, str], tuple[int, Any]]
) -> Fake:
    fake = Fake(routes)
    # Patch the module OBJECTS, not dotted strings - these are file-loaded, not importable packages.
    monkeypatch.setattr(reaper.claim, "_request", fake)
    monkeypatch.setattr(reaper.claim, "_paginate", lambda path, what: fake("GET", path)[1] or [])
    monkeypatch.setattr(reaper, "_now", lambda: NOW)
    return fake


def _pr(**over: Any) -> dict[str, Any]:
    """A full PR object as `/pulls/{number}` returns it - mergeable* only exist there."""
    pr = {
        "number": 99,
        "state": "open",
        "updated_at": _ago(minutes=5),
        "mergeable": True,
        "mergeable_state": "clean",
        "head": {"sha": "d" * 40},
    }
    pr.update(over)
    return pr


def _routes(pr: dict[str, Any] | None = None) -> dict[tuple[str, str], tuple[int, Any]]:
    """A live, healthy claim: ref present, active 5 minutes ago, no PR yet, issue open.

    Pass `pr` to give the claim a pull request; both the list and single-PR reads are wired, since
    `_open_pr` follows the list with `/pulls/{number}` to obtain mergeability.
    """
    routes: dict[tuple[str, str], tuple[int, Any]] = {
        ("GET", "/repos/bioedca/tether/git/matching-refs/heads/agent/issue-"): (
            200,
            [{"ref": "refs/heads/agent/issue-7"}],
        ),
        ("GET", "/repos/bioedca/tether/activity"): (200, [{"timestamp": _ago(minutes=5)}]),
        ("GET", "/repos/bioedca/tether/pulls?head"): (200, [] if pr is None else [pr]),
        ("GET", "/repos/bioedca/tether/issues/7"): (200, {"state": "open"}),
    }
    if pr is not None:
        routes[("GET", f"/repos/bioedca/tether/pulls/{pr['number']}")] = (200, pr)
    return routes


# ------------------------------------------------------------------ ref discovery


def test_only_well_formed_claim_refs_are_swept(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ref that is not agent/issue-<digits> is not a claim and must be left alone."""
    routes = _routes()
    routes[("GET", "/repos/bioedca/tether/git/matching-refs/heads/agent/issue-")] = (
        200,
        [
            {"ref": "refs/heads/agent/issue-7"},
            {"ref": "refs/heads/agent/issue-notanumber"},
            {"ref": "refs/heads/agent/issue-12-with-slug"},
            {"ref": "refs/heads/feat/issue-9-something"},
        ],
    )
    _install(monkeypatch, routes)
    assert reaper._claim_refs() == [7]


def test_no_claim_refs_is_success_not_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """This runs on a schedule; an empty sweep must never page anyone."""
    routes = _routes()
    routes[("GET", "/repos/bioedca/tether/git/matching-refs/heads/agent/issue-")] = (404, None)
    _install(monkeypatch, routes)
    assert reaper.sweep(dry_run=False) == []


# ------------------------------------------------------------------ rule 1: no PR


def test_a_silent_claim_with_no_pr_is_reclaimed(monkeypatch: pytest.MonkeyPatch) -> None:
    routes = _routes()
    routes[("GET", "/repos/bioedca/tether/activity")] = (200, [{"timestamp": _ago(minutes=91)}])
    fake = _install(monkeypatch, routes)
    actions = reaper.sweep(dry_run=False)
    assert actions == [{"issue": 7, "action": "requeue", "reason": "no-open-pr"}]
    assert fake.did("DELETE", "git/refs/heads/agent/issue-7")
    assert fake.did("POST", "/issues/7/labels")


def test_a_recently_active_claim_with_no_pr_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    """A worker that just claimed has not had time to open a PR."""
    fake = _install(monkeypatch, _routes())
    actions = reaper.sweep(dry_run=False)
    assert actions == [{"issue": 7, "action": "keep", "reason": "recent-activity"}]
    assert not fake.did("DELETE", "git/refs")


# ------------------------------------------------------------------ rule 2: stale PR


def test_a_pr_untouched_for_six_hours_with_no_checks_is_closed_and_requeued(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routes = _routes(_pr(updated_at=_ago(hours=7)))
    routes[("GET", "/repos/bioedca/tether/commits/")] = (200, {"check_suites": []})
    fake = _install(monkeypatch, routes)
    actions = reaper.sweep(dry_run=False)
    assert actions[0]["action"] == "requeue" and actions[0]["reason"] == "stale-pr"
    assert fake.did("PATCH", "/pulls/99")
    assert fake.did("DELETE", "git/refs/heads/agent/issue-7")


def test_a_stale_pr_with_checks_still_running_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    """CI can legitimately outlast the window; killing a PR mid-run loses real work."""
    routes = _routes(_pr(updated_at=_ago(hours=7)))
    routes[("GET", "/repos/bioedca/tether/commits/")] = (
        200,
        {"check_suites": [{"status": "in_progress"}]},
    )
    fake = _install(monkeypatch, routes)
    assert reaper.sweep(dry_run=False)[0]["action"] == "keep"
    assert not fake.did("PATCH", "/pulls/99")
    assert not fake.did("DELETE", "git/refs")


# ------------------------------------------------------------------ rule 3: conflicted


def test_a_dirty_pr_is_flagged_and_its_claim_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """A conflict needs a human or a rebase, not a reclaim - the work is still good."""
    routes = _routes(_pr(mergeable_state="dirty", mergeable=False))
    fake = _install(monkeypatch, routes)
    assert reaper.sweep(dry_run=False)[0]["action"] == "flag-conflicted"
    assert not fake.did("DELETE", "git/refs")
    assert not fake.did("PATCH", "/pulls/99")


# ------------------------------------------------------------------ fail closed on API errors


@pytest.mark.parametrize(
    "response",
    [(403, None), (429, None), (500, None), (502, None), (200, {"unexpected": "shape"})],
    ids=["forbidden", "rate-limited", "500", "502", "malformed"],
)
def test_an_unreadable_pr_state_stops_the_sweep_instead_of_reclaiming(
    monkeypatch: pytest.MonkeyPatch, response: tuple[int, Any]
) -> None:
    """Not-knowing must never equal knowing-there-is-no-PR.

    Collapsing an API error to "no PR" lets one transient 403 destroy a healthy claim - and this
    job runs unattended every 30 minutes, so nobody would see it happen.
    """
    routes = _routes()
    routes[("GET", "/repos/bioedca/tether/activity")] = (200, [{"timestamp": _ago(minutes=91)}])
    routes[("GET", "/repos/bioedca/tether/pulls?head")] = response
    fake = _install(monkeypatch, routes)
    with pytest.raises(reaper.ReaperError, match="pull-request state could not be read"):
        reaper.sweep(dry_run=False)
    assert not [c for c in fake.calls if c[0] in {"DELETE", "PATCH", "POST"}]


@pytest.mark.parametrize(
    "response",
    [(403, None), (500, None), (200, {"check_suites": "not-a-list"})],
    ids=["forbidden-missing-checks-read", "500", "malformed"],
)
def test_unreadable_check_state_never_closes_a_pr(
    monkeypatch: pytest.MonkeyPatch, response: tuple[int, Any]
) -> None:
    """A 403 here is the realistic case: with an explicit permissions block, omitting
    `checks: read` makes every check-suite read a 403. Reading that as "no checks running" would
    close a PR whose CI is mid-flight."""
    routes = _routes(_pr(updated_at=_ago(hours=7)))
    routes[("GET", "/repos/bioedca/tether/commits/")] = response
    fake = _install(monkeypatch, routes)
    with pytest.raises(reaper.ReaperError, match="check-suite"):
        reaper.sweep(dry_run=False)
    assert not fake.did("PATCH", "/pulls/99")
    assert not [c for c in fake.calls if c[0] in {"DELETE", "POST"}]


def test_a_genuinely_empty_pr_list_still_permits_reclamation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fail-closed guard must not block the ordinary case: 200 with [] means no PR exists."""
    routes = _routes()
    routes[("GET", "/repos/bioedca/tether/activity")] = (200, [{"timestamp": _ago(minutes=91)}])
    routes[("GET", "/repos/bioedca/tether/pulls?head")] = (200, [])
    fake = _install(monkeypatch, routes)
    assert reaper.sweep(dry_run=False)[0]["action"] == "requeue"
    assert fake.did("DELETE", "git/refs/heads/agent/issue-7")


def test_the_workflow_grants_checks_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """`statuses: read` does not cover /check-suites, and an unlisted scope is `none`."""
    workflow = (ROOT / ".github" / "workflows" / "agent-reaper.yml").read_text(encoding="utf-8")
    permissions = workflow.split("permissions:", 1)[1].split("concurrency:", 1)[0]
    assert "checks: read" in permissions


# ------------------------------------------------------------------ safety properties


def test_dry_run_mutates_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    routes = _routes()
    routes[("GET", "/repos/bioedca/tether/activity")] = (200, [{"timestamp": _ago(minutes=91)}])
    fake = _install(monkeypatch, routes)
    assert reaper.sweep(dry_run=True)[0]["action"] == "requeue"
    assert not [c for c in fake.calls if c[0] in {"DELETE", "PATCH", "POST"}]


def test_a_closed_issue_is_not_reopened_onto_the_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reclaiming a merged issue's leftover ref must not put it back in Ready."""
    routes = _routes()
    routes[("GET", "/repos/bioedca/tether/activity")] = (200, [{"timestamp": _ago(minutes=91)}])
    routes[("GET", "/repos/bioedca/tether/issues/7")] = (200, {"state": "closed"})
    fake = _install(monkeypatch, routes)
    reaper.sweep(dry_run=False)
    assert fake.did("DELETE", "git/refs/heads/agent/issue-7")
    assert not fake.did("POST", "/issues/7/labels")


def test_the_sweep_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second pass over an already-reclaimed issue finds no ref and changes nothing."""
    routes = _routes()
    routes[("GET", "/repos/bioedca/tether/git/matching-refs/heads/agent/issue-")] = (200, [])
    fake = _install(monkeypatch, routes)
    assert reaper.sweep(dry_run=False) == []
    assert not [c for c in fake.calls if c[0] in {"DELETE", "PATCH", "POST"}]


def test_staleness_never_reads_commit_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """committedDate is client-written: keying on it lets a claim be pinned open or stolen."""
    routes = _routes()
    routes[("GET", "/repos/bioedca/tether/activity")] = (200, [{"timestamp": _ago(minutes=91)}])
    fake = _install(monkeypatch, routes)
    reaper.sweep(dry_run=False)
    paths = [p for _, p in fake.calls]
    assert any("/activity?ref=refs/heads/agent/issue-7" in p for p in paths)
    assert not [p for p in paths if "/commits?" in p or "graphql" in p]


def test_a_malformed_server_timestamp_is_refused_not_guessed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routes = _routes()
    routes[("GET", "/repos/bioedca/tether/activity")] = (200, [{"timestamp": "not-a-date"}])
    _install(monkeypatch, routes)
    with pytest.raises(reaper.ReaperError, match="ISO-8601"):
        reaper.sweep(dry_run=False)


def test_json_output_is_machine_readable(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _routes())
    payload = {"version": 1, "dry_run": False, "actions": reaper.sweep(dry_run=False)}
    assert json.loads(json.dumps(payload))["actions"][0]["issue"] == 7
