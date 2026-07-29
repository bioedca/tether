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
        ("GET", "/repos/bioedca/tether/activity"): (200, [{"id": 1, "timestamp": _ago(minutes=5)}]),
        ("GET", "/repos/bioedca/tether/pulls?head"): (200, [] if pr is None else [pr]),
        ("GET", "/repos/bioedca/tether/issues/7"): (200, {"state": "open"}),
        # Read by _retire_ref, which archives the tip to refs/reaped/ before deleting it.
        ("GET", "/repos/bioedca/tether/git/ref/heads/agent/issue-7"): (
            200,
            {"object": {"sha": "abc12345" + "0" * 32}},
        ),
        # Ref creation returns 201, not the generic 200 the POST default gives.
        ("POST", "/repos/bioedca/tether/git/refs"): (201, {}),
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
    routes[("GET", "/repos/bioedca/tether/activity")] = (
        200,
        [{"id": 1, "timestamp": _ago(minutes=91)}],
    )
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
    routes[("GET", "/repos/bioedca/tether/activity")] = (
        200,
        [{"id": 1, "timestamp": _ago(minutes=91)}],
    )
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
    routes[("GET", "/repos/bioedca/tether/activity")] = (
        200,
        [{"id": 1, "timestamp": _ago(minutes=91)}],
    )
    routes[("GET", "/repos/bioedca/tether/pulls?head")] = (200, [])
    fake = _install(monkeypatch, routes)
    assert reaper.sweep(dry_run=False)[0]["action"] == "requeue"
    assert fake.did("DELETE", "git/refs/heads/agent/issue-7")


def test_the_workflow_grants_checks_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """`statuses: read` does not cover /check-suites, and an unlisted scope is `none`."""
    workflow = (ROOT / ".github" / "workflows" / "agent-reaper.yml").read_text(encoding="utf-8")
    permissions = workflow.split("permissions:", 1)[1].split("concurrency:", 1)[0]
    assert "checks: read" in permissions


# ------------------------------------------------- decide-then-mutate (TOCTOU) properties


class Changing(Fake):
    """A fake whose activity feed changes once, after the Nth read.

    Models the only thing that matters here: a worker becoming active between the sweep's decision
    and its destructive call. `concurrency` serializes reaper runs against each other, not against
    workers, and a push opens no PR - so nothing else in the sweep would notice.
    """

    def __init__(self, routes: dict[tuple[str, str], tuple[int, Any]], flip_after: int) -> None:
        super().__init__(routes)
        self.flip_after = flip_after
        self.activity_reads = 0

    def __call__(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        if method == "GET" and "/activity" in path:
            self.activity_reads += 1
            if self.activity_reads > self.flip_after:
                self.calls.append((method, path))
                return 200, [{"id": 999, "timestamp": _ago(minutes=0)}]
        return super().__call__(method, path, body)


def test_a_push_between_decision_and_delete_saves_the_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The worker was silent when we decided, then pushed. Its claim must survive."""
    routes = _routes()
    routes[("GET", "/repos/bioedca/tether/activity")] = (
        200,
        [{"id": 1, "timestamp": _ago(minutes=91)}],
    )
    fake = Changing(routes, flip_after=2)
    monkeypatch.setattr(reaper.claim, "_request", fake)
    monkeypatch.setattr(reaper.claim, "_paginate", lambda path, what: fake("GET", path)[1] or [])
    monkeypatch.setattr(reaper, "_now", lambda: NOW)
    with pytest.raises(reaper.ReaperError, match="new activity mid-sweep"):
        reaper.sweep(dry_run=False)
    assert not fake.did("DELETE", "git/refs")


def test_the_claim_ref_is_deleted_last_so_a_failed_requeue_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Order is the recovery mechanism.

    Sweeps discover work only through claim refs, so deleting the ref before restoring
    `status:ready` made any later failure permanent - raising made it loud, not recoverable. With
    the ref deleted last it is the commit point and the next sweep simply retries.
    """
    routes = _routes()
    routes[("GET", "/repos/bioedca/tether/activity")] = (
        200,
        [{"id": 1, "timestamp": _ago(minutes=91)}],
    )
    routes[("POST", "/repos/bioedca/tether/issues/7/labels")] = (502, None)
    fake = _install(monkeypatch, routes)
    with pytest.raises(reaper.ReaperError, match="left in place so the next sweep retries"):
        reaper.sweep(dry_run=False)
    assert not fake.did("DELETE", "git/refs"), "the ref must survive so the sweep can retry"


def test_ref_deletion_happens_after_the_queue_label_is_restored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routes = _routes()
    routes[("GET", "/repos/bioedca/tether/activity")] = (
        200,
        [{"id": 1, "timestamp": _ago(minutes=91)}],
    )
    fake = _install(monkeypatch, routes)
    reaper.sweep(dry_run=False)
    order = [f"{m} {p}" for m, p in fake.calls]
    label = next(i for i, c in enumerate(order) if c.startswith("POST") and "labels" in c)
    delete = next(i for i, c in enumerate(order) if c.startswith("DELETE") and "git/refs" in c)
    assert label < delete, "status:ready must be restored before the ref is deleted"


def test_a_pr_that_changed_since_the_decision_is_not_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A worker that pushed a new head since the staleness reading is alive, not abandoned."""
    stale = _pr(updated_at=_ago(hours=7))
    routes = _routes(stale)
    routes[("GET", "/repos/bioedca/tether/commits/")] = (200, {"check_suites": []})
    fake = _install(monkeypatch, routes)

    original = fake.__call__
    seen = {"n": 0}

    def moving(method: str, path: str, body: Any = None) -> tuple[int, Any]:
        # After the decision read, the PR reports a fresh head - the worker pushed.
        if method == "GET" and path.endswith("/pulls/99"):
            seen["n"] += 1
            if seen["n"] > 1:
                return 200, _pr(updated_at=_ago(minutes=1), head={"sha": "e" * 40})
        return original(method, path, body)

    monkeypatch.setattr(reaper.claim, "_request", moving)
    assert reaper.sweep(dry_run=False)[0] == {
        "issue": 7,
        "action": "keep",
        "reason": "pr-changed",
    }
    assert not fake.did("PATCH", "/pulls/99")


def test_a_missing_activity_record_is_unknown_not_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    """The feed lags a just-created ref, so no record must never mean "silent for 90 minutes".

    claim.py has an explicit path for `201 but no activity record appeared`, so this is a real
    state - and with no record the fingerprint is None too, disabling the later recheck.
    """
    routes = _routes()
    routes[("GET", "/repos/bioedca/tether/activity")] = (200, [])
    fake = _install(monkeypatch, routes)
    assert reaper.sweep(dry_run=False) == [
        {"issue": 7, "action": "keep", "reason": "activity-unknown"}
    ]
    assert not [c for c in fake.calls if c[0] in {"DELETE", "PATCH", "POST"}]


def test_the_stale_pr_path_is_fenced_like_the_no_pr_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """A push after the head revalidation must still save the claim on the stale-PR route."""
    routes = _routes(_pr(updated_at=_ago(hours=7)))
    routes[("GET", "/repos/bioedca/tether/activity")] = (
        200,
        [{"id": 1, "timestamp": _ago(hours=7)}],
    )
    routes[("GET", "/repos/bioedca/tether/commits/")] = (200, {"check_suites": []})
    # Activity reads in this path: _fingerprint (1), _last_activity (2), then _requeue's
    # revalidating _fingerprint (3). Flipping after 2 puts the push exactly in the window between
    # the decision and the destructive call.
    fake = Changing(routes, flip_after=2)
    monkeypatch.setattr(reaper.claim, "_request", fake)
    monkeypatch.setattr(reaper.claim, "_paginate", lambda path, what: fake("GET", path)[1] or [])
    monkeypatch.setattr(reaper, "_now", lambda: NOW)
    with pytest.raises(reaper.ReaperError, match="new activity mid-sweep"):
        reaper.sweep(dry_run=False)
    assert not fake.did("DELETE", "git/refs")


def test_a_failed_conflict_label_is_not_reported_as_flagged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The label is the only persistent conflict marker; claiming it was set when it was not
    publishes state that is false."""
    routes = _routes(_pr(mergeable_state="dirty", mergeable=False))
    routes[("POST", "/repos/bioedca/tether/issues/7/labels")] = (403, None)
    _install(monkeypatch, routes)
    with pytest.raises(reaper.ReaperError, match="agent:conflicted could not be applied"):
        reaper.sweep(dry_run=False)


def test_the_claim_ref_is_archived_before_it_is_deleted(monkeypatch: pytest.MonkeyPatch) -> None:
    """The residual race cannot be closed - DELETE /git/refs takes no expected-SHA - so losing it
    must be harmless. The branch tip is copied to refs/reaped/ first, keeping the commits
    reachable if the reaper ever deletes a branch a worker had just revived."""
    routes = _routes()
    routes[("GET", "/repos/bioedca/tether/activity")] = (
        200,
        [{"id": 1, "timestamp": _ago(minutes=91)}],
    )
    routes[("GET", "/repos/bioedca/tether/git/ref/heads/agent/issue-7")] = (
        200,
        {"object": {"sha": "abc12345" + "0" * 32}},
    )
    fake = _install(monkeypatch, routes)
    reaper.sweep(dry_run=False)
    order = [f"{m} {p}" for m, p in fake.calls]
    archive = next(i for i, c in enumerate(order) if c.startswith("POST") and "git/refs" in c)
    delete = next(i for i, c in enumerate(order) if c.startswith("DELETE") and "git/refs" in c)
    assert archive < delete, "the tip must be archived before the ref is deleted"


def test_a_failed_archive_prevents_the_deletion(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the commits cannot be preserved, the branch must not be destroyed."""
    routes = _routes()
    routes[("GET", "/repos/bioedca/tether/activity")] = (
        200,
        [{"id": 1, "timestamp": _ago(minutes=91)}],
    )
    routes[("GET", "/repos/bioedca/tether/git/ref/heads/agent/issue-7")] = (
        200,
        {"object": {"sha": "abc12345" + "0" * 32}},
    )
    routes[("POST", "/repos/bioedca/tether/git/refs")] = (500, None)
    fake = _install(monkeypatch, routes)
    with pytest.raises(reaper.ReaperError, match="could not be archived"):
        reaper.sweep(dry_run=False)
    assert not fake.did("DELETE", "git/refs")


def test_a_tip_that_moves_while_being_archived_is_not_deleted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the tip we actually archived may be deleted.

    Archiving A and then deleting whatever the ref points at destroys a push that landed in
    between - unarchived. The window cannot be closed (no expected-SHA on DELETE), but deleting an
    unarchived tip can be refused.
    """
    routes = _routes()
    routes[("GET", "/repos/bioedca/tether/activity")] = (
        200,
        [{"id": 1, "timestamp": _ago(minutes=91)}],
    )
    fake = _install(monkeypatch, routes)
    original = fake.__call__
    reads = {"n": 0}

    def moving(method: str, path: str, body: Any = None) -> tuple[int, Any]:
        if method == "GET" and "git/ref/heads/agent/issue-7" in path:
            reads["n"] += 1
            if reads["n"] > 1:  # the worker pushed while we were archiving
                return 200, {"object": {"sha": "f" * 40}}
        return original(method, path, body)

    monkeypatch.setattr(reaper.claim, "_request", moving)
    with pytest.raises(reaper.ReaperError, match="moved while being archived"):
        reaper.sweep(dry_run=False)
    assert not fake.did("DELETE", "git/refs")


@pytest.mark.parametrize(
    "status_label",
    ["status:blocked", "status:backlog", "status:done", "status:in-review"],
)
def test_a_maintainer_status_change_is_never_overwritten(
    monkeypatch: pytest.MonkeyPatch, status_label: str
) -> None:
    """The reaper undoes its own bookkeeping; it does not overrule a person.

    `claim._check_eligible` only requires that `status:ready` be *present*, so adding it on top of
    a maintainer's `status:blocked` would let a successor immediately take work a human stopped.
    """
    routes = _routes()
    routes[("GET", "/repos/bioedca/tether/activity")] = (
        200,
        [{"id": 1, "timestamp": _ago(minutes=91)}],
    )
    routes[("GET", "/repos/bioedca/tether/issues/7")] = (
        200,
        {"state": "open", "labels": [{"name": status_label}]},
    )
    fake = _install(monkeypatch, routes)
    reaper.sweep(dry_run=False)
    assert fake.did("DELETE", "git/refs/heads/agent/issue-7"), "the claim is still released"
    assert not fake.did("POST", "/issues/7/labels"), f"{status_label} must not be overwritten"


def test_a_block_landing_mid_sweep_still_prevents_the_requeue_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first label read happens several round-trips before the write.

    A maintainer blocking the issue in between must not end up with both blocked and ready, since
    claim._check_eligible only requires ready to be present.
    """
    routes = _routes()
    routes[("GET", "/repos/bioedca/tether/activity")] = (
        200,
        [{"id": 1, "timestamp": _ago(minutes=91)}],
    )
    fake = _install(monkeypatch, routes)
    original = fake.__call__
    reads = {"n": 0}

    def blocking(method: str, path: str, body: Any = None) -> tuple[int, Any]:
        if method == "GET" and path.endswith("/issues/7"):
            reads["n"] += 1
            if reads["n"] > 1:  # the maintainer blocked it after our first read
                return 200, {"state": "open", "labels": [{"name": "status:blocked"}]}
        return original(method, path, body)

    monkeypatch.setattr(reaper.claim, "_request", blocking)
    reaper.sweep(dry_run=False)
    assert not fake.did("POST", "/issues/7/labels"), "a late block must still win"
    assert fake.did("DELETE", "git/refs/heads/agent/issue-7"), "the claim is still released"


# ------------------------------------------------------------------ safety properties


def test_dry_run_mutates_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    routes = _routes()
    routes[("GET", "/repos/bioedca/tether/activity")] = (
        200,
        [{"id": 1, "timestamp": _ago(minutes=91)}],
    )
    fake = _install(monkeypatch, routes)
    assert reaper.sweep(dry_run=True)[0]["action"] == "requeue"
    assert not [c for c in fake.calls if c[0] in {"DELETE", "PATCH", "POST"}]


def test_a_closed_issue_is_not_reopened_onto_the_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reclaiming a merged issue's leftover ref must not put it back in Ready."""
    routes = _routes()
    routes[("GET", "/repos/bioedca/tether/activity")] = (
        200,
        [{"id": 1, "timestamp": _ago(minutes=91)}],
    )
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
    routes[("GET", "/repos/bioedca/tether/activity")] = (
        200,
        [{"id": 1, "timestamp": _ago(minutes=91)}],
    )
    fake = _install(monkeypatch, routes)
    reaper.sweep(dry_run=False)
    paths = [p for _, p in fake.calls]
    assert any("/activity?ref=refs/heads/agent/issue-7" in p for p in paths)
    assert not [p for p in paths if "/commits?" in p or "graphql" in p]


def test_a_malformed_server_timestamp_is_refused_not_guessed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routes = _routes()
    routes[("GET", "/repos/bioedca/tether/activity")] = (
        200,
        [{"id": 1, "timestamp": "not-a-date"}],
    )
    _install(monkeypatch, routes)
    with pytest.raises(reaper.ReaperError, match="ISO-8601"):
        reaper.sweep(dry_run=False)


def test_json_output_is_machine_readable(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _routes())
    payload = {"version": 1, "dry_run": False, "actions": reaper.sweep(dry_run=False)}
    assert json.loads(json.dumps(payload))["actions"][0]["issue"] == 7
