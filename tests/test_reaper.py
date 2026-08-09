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
import yaml

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
        # Which labels were actually POSTed. `calls` records only (method, path), and both markers
        # go to the same `/labels` path through one helper, so without this a test asserting
        # "flag-needs-amend" could not tell `agent:needs-amend` from `agent:conflicted`.
        self.labels: list[str] = []

    def __call__(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        self.calls.append((method, path))
        if method == "POST" and path.endswith("/labels") and isinstance(body, dict):
            self.labels.extend(body.get("labels", []))

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

    def paginate(path: str, what: str) -> list[Any]:
        # Mirror the real _paginate contract: it RAISES on a non-200 or a non-list body. The
        # previous stub returned [] instead, which silently turned every fail-closed test into a
        # "genuinely empty list" test - the fake was hiding the behaviour the tests exist to pin.
        status, payload = fake("GET", path)
        if status != 200 or not isinstance(payload, list):
            raise reaper.claim.ClaimError(f"{what} could not be read")
        return payload

    monkeypatch.setattr(reaper.claim, "_paginate", paginate)
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


@pytest.mark.parametrize(
    ("func", "path_fragment"),
    [
        ("_claim_refs", "git/matching-refs/heads/agent/issue-"),
        ("_fingerprint", "/activity?ref="),
    ],
    ids=["claim-refs", "activity"],
)
def test_every_list_read_goes_through_the_paginating_helper(
    monkeypatch: pytest.MonkeyPatch, func: str, path_fragment: str
) -> None:
    """A single-page read means claims beyond page one are NEVER reaped.

    Every scheduled run would receive the same first page, so the workflow silently stops working
    on a busy repo. This is the third unpaginated-read defect in this change, so it is pinned
    structurally rather than trusted.
    """
    fake = _install(monkeypatch, _routes())
    seen: list[str] = []

    def spy(path: str, what: str) -> list[Any]:
        seen.append(path)
        status, payload = fake("GET", path)
        return payload if isinstance(payload, list) else []

    monkeypatch.setattr(reaper.claim, "_paginate", spy)
    getattr(reaper, func)(*([] if func == "_claim_refs" else [7]))
    assert any(path_fragment in p for p in seen), f"{func} must read through _paginate; saw {seen}"


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
    # A reclaim now carries its tombstone: which sha was archived at the moment of deletion, so a
    # loss inside the residual window is attributable rather than silent (#278, ADR-0064).
    assert actions == [
        {
            "issue": 7,
            "action": "requeue",
            "reason": "no-open-pr",
            "archived": "abc12345" + "0" * 32,
            "archive_error": None,
        }
    ]
    assert fake.did("DELETE", "git/refs/heads/agent/issue-7")
    assert fake.did("POST", "/issues/7/labels")


def test_a_recently_active_claim_with_no_pr_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    """A worker that just claimed has not had time to open a PR."""
    fake = _install(monkeypatch, _routes())
    actions = reaper.sweep(dry_run=False)
    assert actions == [{"issue": 7, "action": "keep", "reason": "recent-activity"}]
    assert not fake.did("DELETE", "git/refs")


# ------------------------------------------------------------------ rule 2: stale PR


def test_a_stale_pr_is_flagged_and_left_completely_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#277: the stale-PR case labels the issue and touches nothing else.

    An abandoned PR is visible in the pull-request list; an abandoned claim ref is not. The sweep
    therefore spends its unattended authority on the invisible case only, and hands the visible one
    to the launcher's AMEND path - which can continue the *existing* PR precisely because the claim
    survives. This asserts the absence of all three mutations the closing version performed.
    """
    routes = _routes(_pr(updated_at=_ago(hours=7)))
    routes[("GET", "/repos/bioedca/tether/commits/")] = (200, {"check_suites": []})
    fake = _install(monkeypatch, routes)
    actions = reaper.sweep(dry_run=False)
    assert actions[0]["action"] == "flag-needs-amend"
    assert actions[0]["reason"] == "stale-pr"
    assert actions[0]["pr"] == 99
    assert fake.labels == ["agent:needs-amend"]
    assert not fake.did("PATCH", "/pulls/99"), "the PR must not be closed"
    assert not fake.did("DELETE", "git/refs"), "the claim ref must survive"
    assert not fake.did("DELETE", "labels/status:in-progress"), "the labels must not move"


def test_a_stale_pr_with_checks_still_running_is_not_even_flagged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CI can legitimately outlast the window, so a mid-run PR is active, not abandoned."""
    routes = _routes(_pr(updated_at=_ago(hours=7)))
    routes[("GET", "/repos/bioedca/tether/commits/")] = (
        200,
        {"check_suites": [{"status": "in_progress"}]},
    )
    fake = _install(monkeypatch, routes)
    assert reaper.sweep(dry_run=False)[0]["action"] == "keep"
    assert fake.labels == []
    assert not fake.did("PATCH", "/pulls/99")
    assert not fake.did("DELETE", "git/refs")


# ------------------------------------------------------------------ rule 3: conflicted


def test_a_dirty_pr_is_flagged_and_its_claim_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """A conflict needs a human or a rebase, not a reclaim - the work is still good."""
    routes = _routes(_pr(mergeable_state="dirty", mergeable=False))
    fake = _install(monkeypatch, routes)
    assert reaper.sweep(dry_run=False)[0]["action"] == "flag-conflicted"
    assert fake.labels == ["agent:conflicted"]
    assert not fake.did("DELETE", "git/refs")
    assert not fake.did("PATCH", "/pulls/99")


def test_a_resolved_conflict_loses_the_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    """Once the PR is no longer dirty the marker is stale, and nothing else ever removed it.

    The label is meant to be the only persistent signal that a claim needs a person, so an issue
    left falsely marked degrades the very signal it exists to provide - and a marker that is never
    cleared is indistinguishable from one that is never checked. 200 from the label DELETE is the
    real API's "a label was actually removed"; 404 is the ordinary no-marker case, covered below.
    """
    routes = _routes(_pr())
    routes[("DELETE", "/repos/bioedca/tether/issues/7/labels/agent:conflicted")] = (200, [])
    fake = _install(monkeypatch, routes)
    assert reaper.sweep(dry_run=False) == [
        {"issue": 7, "action": "clear-conflicted", "reason": "conflict-resolved"}
    ]
    assert fake.did("DELETE", "labels/agent:conflicted")
    # Clearing a cosmetic marker must not escalate into touching the claim.
    assert not fake.did("DELETE", "git/refs")
    assert fake.labels == []


def test_a_closed_pull_request_is_never_marked_conflicted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both conflicted paths need an OPEN pull request, and `triage.MIRROR_LABELS` relies on it.

    `agent:conflicted` is cleared by the merge path in `triage.clear_mirror` (#308). That is only
    free of the two-writers hazard because `_mark` and `_clear_conflicted` both sit past the
    `pr is None or state != "open"` guard, so a merged - therefore closed - pull request routes to
    the no-PR branch and reaches neither. Loosening that guard would silently re-open the window
    with every other test still green, which is why the invariant is a gate rather than a comment.
    """
    routes = _routes(_pr(state="closed", mergeable_state="dirty", mergeable=False))
    fake = _install(monkeypatch, routes)

    assert reaper.sweep(dry_run=False) == [
        {"issue": 7, "action": "keep", "reason": "recent-activity"}
    ]
    assert fake.labels == []
    assert not fake.did("DELETE", "labels/agent:conflicted")


def test_a_clean_pr_without_a_marker_reports_nothing_to_do(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The common case: the DELETE 404s, which is not an error and not a state change."""
    routes = _routes(_pr())
    routes[("DELETE", "/repos/bioedca/tether/issues/7/labels/agent:conflicted")] = (404, None)
    fake = _install(monkeypatch, routes)
    assert reaper.sweep(dry_run=False) == [{"issue": 7, "action": "keep", "reason": "pr-active"}]
    assert not fake.did("DELETE", "git/refs")


def test_a_failed_marker_clear_does_not_abort_the_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deliberately best-effort, unlike `_mark`.

    Aborting a whole unattended sweep over a label nobody reads would trade a real reclamation for a
    tidy one, and the next sweep retries anyway. This pins that asymmetry so it cannot be "fixed"
    into a fail-closed check by someone sweeping for the class.
    """
    routes = _routes(_pr())
    routes[("DELETE", "/repos/bioedca/tether/issues/7/labels/agent:conflicted")] = (500, None)
    _install(monkeypatch, routes)
    assert reaper.sweep(dry_run=False) == [{"issue": 7, "action": "keep", "reason": "pr-active"}]


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
def test_unreadable_check_state_mutates_nothing_at_all(
    monkeypatch: pytest.MonkeyPatch, response: tuple[int, Any]
) -> None:
    """A 403 here is the realistic case: with an explicit permissions block, omitting
    `checks: read` makes every check-suite read a 403. Reading that as "no checks running" would
    flag a PR whose CI is merely mid-flight.

    The assertion covers every write verb rather than one path. Excluding only `/pulls/99` passed
    while a regression that mutated some *other* PR went unnoticed, and the sweep has no business
    issuing any write once it has admitted it cannot read the state it is deciding from.
    """
    routes = _routes(_pr(updated_at=_ago(hours=7)))
    routes[("GET", "/repos/bioedca/tether/commits/")] = response
    fake = _install(monkeypatch, routes)
    with pytest.raises(reaper.ReaperError, match="check-suite"):
        reaper.sweep(dry_run=False)
    assert not [c for c in fake.calls if c[0] in {"DELETE", "PATCH", "POST"}]


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


def test_the_workflow_grants_checks_read() -> None:
    """`statuses: read` does not cover /check-suites, and an unlisted scope is `none`.

    Parsed rather than sliced (#338). The previous form asserted `"checks: read" in permissions`,
    where `permissions` was `workflow.split("permissions:", 1)[1].split("concurrency:", 1)[0]` - a
    substring delimited by two *unrelated* keys. That assertion changed meaning for reasons that had
    nothing to do with the permission it claims to check: reorder the block, rename `concurrency:`,
    or add a second `permissions:` mapping and it silently starts measuring something else. It had
    already moved once, when #277 narrowed the pull-request scope in that same block.

    `tests/test_triage.py` already parses its own workflow this way, so the pattern is established
    in-repo rather than introduced here.
    """
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "agent-reaper.yml").read_text(encoding="utf-8")
    )
    assert workflow["permissions"]["checks"] == "read"


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


@pytest.mark.parametrize(
    ("pr", "label"),
    [
        (_pr(mergeable_state="dirty", mergeable=False), "agent:conflicted"),
        (_pr(updated_at=_ago(hours=7)), "agent:needs-amend"),
    ],
    ids=["conflicted", "needs-amend"],
)
def test_a_failed_marker_is_never_reported_as_applied(
    monkeypatch: pytest.MonkeyPatch, pr: dict[str, Any], label: str
) -> None:
    """Since #277 the marker is the ONLY thing either PR path does.

    It is no longer a hint beside a close and a reclaim - it *is* the action - so a silently failed
    write would report a flag that was never set and leave the PR unattended, which is the
    silent-state failure this whole workflow exists to end. Both markers go through one helper, so
    both are pinned here.
    """
    routes = _routes(pr)
    routes[("GET", "/repos/bioedca/tether/commits/")] = (200, {"check_suites": []})
    routes[("POST", "/repos/bioedca/tether/issues/7/labels")] = (403, None)
    fake = _install(monkeypatch, routes)
    with pytest.raises(reaper.ReaperError, match=rf"needs {label} but it could not be applied"):
        reaper.sweep(dry_run=False)
    assert not fake.did("DELETE", "git/refs")


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


def test_the_archive_ref_name_carries_the_full_sha(monkeypatch: pytest.MonkeyPatch) -> None:
    """An 8-character prefix makes a 422 ambiguous.

    It could mean "this exact archive exists" or "a different commit sharing that prefix was
    archived earlier" - and accepting the second deletes a tip that was never preserved. The full
    sha makes the ref name identify the commit, so 422 can only mean identical.
    """
    sha = "abc12345" + "0" * 32
    routes = _routes()
    routes[("GET", "/repos/bioedca/tether/activity")] = (
        200,
        [{"id": 1, "timestamp": _ago(minutes=91)}],
    )
    routes[("GET", "/repos/bioedca/tether/git/ref/heads/agent/issue-7")] = (
        200,
        {"object": {"sha": sha}},
    )
    fake = _install(monkeypatch, routes)
    bodies: list[Any] = []
    original = fake.__call__

    def record(method: str, path: str, body: Any = None) -> tuple[int, Any]:
        if method == "POST" and path.endswith("/git/refs"):
            bodies.append(body)
        return original(method, path, body)

    monkeypatch.setattr(reaper.claim, "_request", record)
    reaper.sweep(dry_run=False)
    assert bodies, "no archive ref was created"
    assert bodies[0]["ref"] == f"refs/reaped/issue-7-{sha}"
    assert sha[:8] != sha and not bodies[0]["ref"].endswith(sha[:8]), "must not be a prefix"


def test_an_aborted_retirement_leaves_the_labels_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every abortable step must run before any state change.

    When the archive/verify ran *after* the label transition, an abort left a live claim ref beside
    a falsely `status:ready` issue: claimers lost the 422 race forever, and later sweeps saw the
    fresh activity, kept the claim, and never repaired the labels.
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
    assert not fake.did("DELETE", "git/refs"), "the ref must survive"
    assert not fake.did("POST", "/issues/7/labels"), "status:ready must not have been added"
    assert not fake.did("DELETE", "/issues/7/labels/status:in-progress"), (
        "the active-claim labels must be intact so the next sweep re-decides cleanly"
    )


def test_a_422_archive_is_verified_rather_than_assumed(monkeypatch: pytest.MonkeyPatch) -> None:
    """422 from POST /git/refs is not only "already exists".

    It also covers validation failures such as an unknown sha or a malformed name. Assuming the
    benign meaning would let the delete proceed against an archive that was never created.
    """
    sha = "abc12345" + "0" * 32
    routes = _routes()
    routes[("GET", "/repos/bioedca/tether/activity")] = (
        200,
        [{"id": 1, "timestamp": _ago(minutes=91)}],
    )
    routes[("GET", "/repos/bioedca/tether/git/ref/heads/agent/issue-7")] = (
        200,
        {"object": {"sha": sha}},
    )
    routes[("POST", "/repos/bioedca/tether/git/refs")] = (422, None)
    # The archive it claims to already have does not exist.
    routes[("GET", f"/repos/bioedca/tether/git/ref/reaped/issue-7-{sha}")] = (404, None)
    fake = _install(monkeypatch, routes)
    with pytest.raises(reaper.ReaperError, match="absent or points elsewhere"):
        reaper.sweep(dry_run=False)
    assert not fake.did("DELETE", "git/refs")


def test_a_422_archive_pointing_elsewhere_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    sha = "abc12345" + "0" * 32
    routes = _routes()
    routes[("GET", "/repos/bioedca/tether/activity")] = (
        200,
        [{"id": 1, "timestamp": _ago(minutes=91)}],
    )
    routes[("GET", "/repos/bioedca/tether/git/ref/heads/agent/issue-7")] = (
        200,
        {"object": {"sha": sha}},
    )
    routes[("POST", "/repos/bioedca/tether/git/refs")] = (422, None)
    routes[("GET", f"/repos/bioedca/tether/git/ref/reaped/issue-7-{sha}")] = (
        200,
        {"object": {"sha": "9" * 40}},
    )
    fake = _install(monkeypatch, routes)
    with pytest.raises(reaper.ReaperError, match="absent or points elsewhere"):
        reaper.sweep(dry_run=False)
    assert not fake.did("DELETE", "git/refs")


def test_an_issue_closed_mid_sweep_does_not_get_the_ready_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The open/closed check in the caller came from an earlier read.

    A maintainer closing the issue in between must not leave status:ready on a closed issue.
    """
    routes = _routes()
    routes[("GET", "/repos/bioedca/tether/activity")] = (
        200,
        [{"id": 1, "timestamp": _ago(minutes=91)}],
    )
    fake = _install(monkeypatch, routes)
    original = fake.__call__
    reads = {"n": 0}

    def closing(method: str, path: str, body: Any = None) -> tuple[int, Any]:
        if method == "GET" and path.endswith("/issues/7"):
            reads["n"] += 1
            if reads["n"] > 1:  # closed after our first read
                return 200, {"state": "closed", "labels": []}
        return original(method, path, body)

    monkeypatch.setattr(reaper.claim, "_request", closing)
    reaper.sweep(dry_run=False)
    assert not fake.did("POST", "/issues/7/labels"), "a closed issue must not be marked ready"
    assert fake.did("DELETE", "git/refs/heads/agent/issue-7"), "the claim is still released"


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


# ------------------------------------------------- the residual deletion window (#278, ADR-0064)


def _archived_shas(posts: list[tuple[str, Any]]) -> set[str]:
    """Every sha copied to ``refs/reaped/`` during a sweep."""
    return {
        body["ref"].rsplit("-", 1)[1]
        for path, body in posts
        if path.endswith("/git/refs") and isinstance(body, dict) and "refs/reaped/" in body["ref"]
    }


def _reclaimable(monkeypatch: pytest.MonkeyPatch) -> Fake:
    """A claim with no PR, silent for 91 minutes: the one path that still deletes a ref."""
    routes = _routes()
    routes[("GET", "/repos/bioedca/tether/activity")] = (
        200,
        [{"id": 1, "timestamp": _ago(minutes=91)}],
    )
    return _install(monkeypatch, routes)


def test_the_tip_deleted_is_the_tip_archived_even_when_a_push_lands_late(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A push after `_prepare_retire`'s final read is still archived before the DELETE.

    This is the bound ADR-0064 decided on for #278, and it **fails against the implementation
    before it**: the only archive was of the tip read at the *start* of `_prepare_retire`, so a
    push landing after its verification read was deleted unarchived. `_retire_ref` now re-reads and
    re-archives immediately before the delete, so that push is preserved too.

    The window is narrowed, not closed. `DELETE /git/refs` accepts no expected-SHA, so a push
    inside the final round-trip is irreducible — that residual is what ADR-0064 accepts rather than
    pretends to have fixed.

    Exercised through the ordinary fake transport, as #278 requires: the ref simply answers a
    different sha on its third read, which is what a live push looks like from here.
    """
    late = "e" * 40
    fake = _reclaimable(monkeypatch)
    original = fake.__call__
    posts: list[tuple[str, Any]] = []
    reads = {"n": 0}

    def pushing_late(method: str, path: str, body: Any = None) -> tuple[int, Any]:
        if method == "POST":
            posts.append((path, body))
        if method == "GET" and "git/ref/heads/agent/issue-7" in path:
            reads["n"] += 1
            # Reads 1 and 2 are `_prepare_retire`'s archive-then-verify pair, so the reap proceeds.
            # Read 3 is `_retire_ref`'s, and by then the worker has pushed.
            if reads["n"] >= 3:
                return 200, {"object": {"sha": late}}
        return original(method, path, body)

    monkeypatch.setattr(reaper.claim, "_request", pushing_late)
    actions = reaper.sweep(dry_run=False)

    assert late in _archived_shas(posts), "the late push was deleted without being archived"
    assert fake.did("DELETE", "git/refs/heads/agent/issue-7")
    # The tombstone names it, so a loss inside the residual window is attributable, not silent.
    assert [a for a in actions if a.get("archived") == late]


def test_a_failed_late_archive_does_not_strand_the_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The second archive is best-effort on purpose, and this pins that it stays so.

    `_retire_ref` runs *after* the labels have flipped. Raising there is the exact defect the
    prepare/retire split was made to fix — a live claim ref beside a falsely `status:ready` issue,
    which every later sweep then kept and never repaired. The prepare-time archive is still the
    floor, so proceeding is no worse than the behaviour before #278, whereas stranding the claim to
    protect a best-effort improvement would trade a bounded risk for an unbounded one.
    """
    fake = _reclaimable(monkeypatch)
    original = fake.__call__
    seen = {"archives": 0}

    def failing_second_archive(method: str, path: str, body: Any = None) -> tuple[int, Any]:
        if method == "POST" and path.endswith("/git/refs"):
            seen["archives"] += 1
            if seen["archives"] >= 2:
                return 500, None
        return original(method, path, body)

    monkeypatch.setattr(reaper.claim, "_request", failing_second_archive)
    actions = reaper.sweep(dry_run=False)

    assert fake.did("DELETE", "git/refs/heads/agent/issue-7"), "the claim must not be stranded"
    assert [a for a in actions if a.get("archive_error")], "and the failure is reported, not hidden"
