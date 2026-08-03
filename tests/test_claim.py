# SPDX-FileCopyrightText: 2026 The Tether Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Contract tests for the atomic issue-claim helper.

Every GitHub call goes through a fake transport: CI must not depend on the network, and the
interesting cases (losing a 422 race, a reclaimed generation) cannot be produced on demand against
a live repository anyway.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".agents" / "bin" / "claim.py"

_spec = importlib.util.spec_from_file_location("tether_claim", SCRIPT)
assert _spec is not None and _spec.loader is not None
claim = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(claim)

DIGEST = "a" * 64
OTHER = "b" * 64
MARKER = '<!-- tether-agent-ready {"version":1,"criteria_sha256":"' + DIGEST + '"} -->'
HEAD = "c" * 40


def _issue(**overrides: Any) -> dict[str, Any]:
    issue = {
        "state": "open",
        "title": "feat(io): a thing",
        "body": "Acceptance criteria\n",
        "labels": [{"name": "status:ready"}],
        "assignees": [],
    }
    issue.update(overrides)
    return issue


class Fake:
    """Records every request and answers from a routing table keyed by (method, path-prefix)."""

    def __init__(self, routes: dict[tuple[str, str], tuple[int, Any]]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str]] = []

    def __call__(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        self.calls.append((method, path))
        for (route_method, prefix), response in self.routes.items():
            if method == route_method and path.startswith(prefix):
                return response
        return 200, None


def _install(monkeypatch: pytest.MonkeyPatch, fake: Fake) -> Fake:
    # Patch the module OBJECT, not a dotted string: a dotted patch of a non-package module
    # resolves differently under CI's import layout.
    monkeypatch.setattr(claim, "_request", fake)
    monkeypatch.setattr(claim, "_scope_hash", lambda title, body: DIGEST)
    return fake


Routes = dict[tuple[str, str], tuple[int, Any]]


def _routes(over: Routes | None = None) -> Routes:
    routes: dict[tuple[str, str], tuple[int, Any]] = {
        ("GET", "/repos/bioedca/tether/issues/7/comments"): (
            200,
            [{"user": {"login": "bioedca"}, "body": f"Approved.\n\n{MARKER}"}],
        ),
        ("GET", "/repos/bioedca/tether/issues/7"): (200, _issue()),
        ("GET", "/repos/bioedca/tether/git/ref/heads/main"): (200, {"object": {"sha": HEAD}}),
        ("POST", "/repos/bioedca/tether/git/refs"): (201, {}),
        ("GET", "/repos/bioedca/tether/activity"): (
            200,
            [{"id": 42, "activity_type": "branch_creation"}],
        ),
    }
    routes.update(over or {})
    return routes


# --------------------------------------------------------------------- eligibility


@pytest.mark.parametrize(
    ("issue", "message"),
    [
        (_issue(state="closed"), "not open"),
        (_issue(labels=[{"name": "status:blocked"}]), "not status:ready"),
        (_issue(assignees=[{"login": "someone-else"}]), "assigned to someone else"),
        (_issue(pull_request={"url": "x"}), "pull request"),
    ],
    ids=["closed", "not-ready", "other-assignee", "is-a-pr"],
)
def test_claim_refuses_ineligible_work_before_creating_any_ref(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    issue: dict[str, Any],
    message: str,
) -> None:
    routes = _routes({("GET", "/repos/bioedca/tether/issues/7"): (200, issue)})
    fake = _install(monkeypatch, Fake(routes))
    with pytest.raises(SystemExit) as exit_info:
        claim._cmd_claim(_args(issue=7))
    assert exit_info.value.code == claim.EXIT_INELIGIBLE
    assert message in capsys.readouterr().err
    # The mutex must never be taken for work that may not be worked.
    assert not [c for c in fake.calls if c[0] == "POST" and "git/refs" in c[1]]


def test_claim_refuses_when_the_approval_no_longer_binds_the_body(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The issue was edited after approval: the marker's digest no longer matches the snapshot."""
    fake = _install(monkeypatch, Fake(_routes()))
    monkeypatch.setattr(claim, "_scope_hash", lambda title, body: OTHER)
    with pytest.raises(SystemExit) as exit_info:
        claim._cmd_claim(_args(issue=7))
    assert exit_info.value.code == claim.EXIT_INELIGIBLE
    assert "edited after approval" in capsys.readouterr().err
    assert not [c for c in fake.calls if c[0] == "POST" and "git/refs" in c[1]]


def test_claim_ignores_an_approval_from_a_non_maintainer(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    routes = _routes(
        {
            ("GET", "/repos/bioedca/tether/issues/7/comments"): (
                200,
                [{"user": {"login": "a-stranger"}, "body": MARKER}],
            )
        }
    )
    _install(monkeypatch, Fake(routes))
    with pytest.raises(SystemExit) as exit_info:
        claim._cmd_claim(_args(issue=7))
    assert exit_info.value.code == claim.EXIT_INELIGIBLE
    assert "no maintainer approval" in capsys.readouterr().err


# --------------------------------------------------------------------- the mutex


def test_claim_wins_and_reports_the_server_assigned_generation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install(monkeypatch, Fake(_routes()))
    claim._cmd_claim(_args(issue=7))
    record = json.loads(capsys.readouterr().out)
    assert record["branch"] == "agent/issue-7"
    assert record["generation"] == 42
    assert record["base_sha"] == HEAD


def test_losing_the_race_is_an_ordinary_exit_not_a_crash(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    routes = _routes(
        {("POST", "/repos/bioedca/tether/git/refs"): (422, {"message": "Reference already exists"})}
    )
    _install(monkeypatch, Fake(routes))
    with pytest.raises(SystemExit) as exit_info:
        claim._cmd_claim(_args(issue=7))
    assert exit_info.value.code == claim.EXIT_LOST
    captured = capsys.readouterr().err
    assert "already exists" in captured
    assert "Traceback" not in captured
    assert str(ROOT) not in captured


def test_a_failed_label_write_does_not_void_the_claim(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The label is a mirror, never the lock: the next agent still gets 422 either way."""
    routes = _routes({("POST", "/repos/bioedca/tether/issues/7/labels"): (403, None)})
    _install(monkeypatch, Fake(routes))
    claim._cmd_claim(_args(issue=7))
    record = json.loads(capsys.readouterr().out)
    assert record["generation"] == 42
    assert record["label_mirror"] is False


# --------------------------------------------------------------------- fencing


def test_check_passes_only_for_the_current_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, Fake(_routes()))
    claim._cmd_check(_args(issue=7, generation=42))


@pytest.mark.parametrize(
    ("entries", "message"),
    [
        ([{"id": 99, "activity_type": "branch_creation"}], "was reclaimed"),
        ([], "no claim ref"),
        ([{"id": 99, "activity_type": "push"}], "no claim ref"),
        # The activity API keeps the creation entry after the ref is deleted, so reading only
        # creations would tell a reaped worker it still holds the claim. Verified live.
        (
            [
                {"id": 42, "activity_type": "branch_creation"},
                {"id": 77, "activity_type": "branch_deletion"},
            ],
            "no claim ref",
        ),
    ],
    ids=["reclaimed", "no-ref", "pushes-are-not-creations", "reaped-then-stale-creation-remains"],
)
def test_a_superseded_worker_is_refused(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    entries: list[dict[str, Any]],
    message: str,
) -> None:
    routes = _routes({("GET", "/repos/bioedca/tether/activity"): (200, entries)})
    _install(monkeypatch, Fake(routes))
    with pytest.raises(SystemExit) as exit_info:
        claim._cmd_check(_args(issue=7, generation=42))
    assert exit_info.value.code == claim.EXIT_SUPERSEDED
    assert message in capsys.readouterr().err


def test_release_refuses_to_delete_a_successors_claim(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    routes = _routes(
        {
            ("GET", "/repos/bioedca/tether/activity"): (
                200,
                [{"id": 99, "activity_type": "branch_creation"}],
            )
        }
    )
    fake = _install(monkeypatch, Fake(routes))
    with pytest.raises(SystemExit) as exit_info:
        claim._cmd_release(_args(issue=7, generation=42, vendor="claude"))
    assert exit_info.value.code == claim.EXIT_SUPERSEDED
    assert "releasing would delete a successor" in capsys.readouterr().err
    assert not [c for c in fake.calls if c[0] == "DELETE" and "git/refs" in c[1]]


def test_a_recreated_ref_supersedes_the_deleted_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reclaim is delete-then-recreate: the successor's creation must win over the deletion."""
    _install(
        monkeypatch,
        Fake(
            _routes(
                {
                    ("GET", "/repos/bioedca/tether/activity"): (
                        200,
                        [
                            {"id": 42, "activity_type": "branch_creation"},
                            {"id": 77, "activity_type": "branch_deletion"},
                            {"id": 91, "activity_type": "branch_creation"},
                        ],
                    ),
                }
            )
        ),
    )
    assert claim._generation(7) == 91


def test_release_refuses_when_the_ref_exists_but_its_generation_is_unreadable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The destructive path must not be the permissive one.

    `check` reads an unreadable generation as fail-closed. `release` used to read the same fact as
    authorization and delete, so a stale worker could destroy a live successor's mutex ref and
    requeue an issue someone was mid-way through. The activity feed can lag the ref - `claim`
    itself handles "201 but no activity record yet" - so this is reachable in the reclaim window.
    """
    routes = _routes(
        {
            ("GET", "/repos/bioedca/tether/git/ref/heads/agent/issue-7"): (200, {"object": {}}),
            ("GET", "/repos/bioedca/tether/activity"): (200, []),
        }
    )
    fake = _install(monkeypatch, Fake(routes))
    with pytest.raises(SystemExit) as exit_info:
        claim._cmd_release(_args(issue=7, generation=42, vendor="claude"))
    assert exit_info.value.code == claim.EXIT_SUPERSEDED
    assert "unreadable" in capsys.readouterr().err
    assert not [c for c in fake.calls if c[0] == "DELETE" and "git/refs" in c[1]]


def test_release_of_an_absent_ref_cleans_labels_without_deleting(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A legitimately reaped worker must still be able to reset the labels."""
    routes = _routes(
        {
            ("GET", "/repos/bioedca/tether/git/ref/heads/agent/issue-7"): (404, None),
            ("GET", "/repos/bioedca/tether/activity"): (200, []),
        }
    )
    fake = _install(monkeypatch, Fake(routes))
    claim._cmd_release(_args(issue=7, generation=42, vendor="claude"))
    assert json.loads(capsys.readouterr().out)["ref"] == "absent"
    assert not [c for c in fake.calls if c[0] == "DELETE" and "git/refs" in c[1]]
    assert [c for c in fake.calls if c[0] == "POST" and "labels" in c[1]]


def test_the_fence_filters_by_activity_type_so_pushes_cannot_evict_the_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unfiltered feed is newest-first and mixes in pushes, which can hide the creation."""
    fake = _install(monkeypatch, Fake(_routes()))
    claim._generation(7)
    activity = [p for _, p in fake.calls if "/activity" in p]
    assert activity, "no activity call made"
    assert all(
        "activity_type=branch_creation" in p or "activity_type=branch_deletion" in p
        for p in activity
    )


def test_approval_discovery_pages_past_the_first_hundred_comments(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A re-approval past comment 100 must be found, not reported as an edited-after-approval."""
    page1 = [{"user": {"login": "bioedca"}, "body": "chatter"} for _ in range(100)]
    page2 = [{"user": {"login": "bioedca"}, "body": f"Re-approved.\n\n{MARKER}"}]

    def transport(method: str, path: str, body: Any = None) -> tuple[int, Any]:
        if "/issues/7/comments" in path:
            return 200, page2 if "page=2" in path else page1
        if path.endswith("/issues/7"):
            return 200, _issue()
        if "git/ref/heads/main" in path:
            return 200, {"object": {"sha": HEAD}}
        if method == "POST" and path.endswith("/git/refs"):
            return 201, {}
        if "/activity" in path:
            return 200, [{"id": 42, "activity_type": "branch_creation"}]
        return 200, None

    monkeypatch.setattr(claim, "_request", transport)
    monkeypatch.setattr(claim, "_scope_hash", lambda title, body: DIGEST)
    claim._cmd_claim(_args(issue=7))
    assert json.loads(capsys.readouterr().out)["generation"] == 42


@pytest.mark.parametrize("status", [403, 404, 500, 502], ids=["forbidden", "missing", "500", "502"])
def test_reserve_adr_refuses_to_guess_when_discovery_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], status: int
) -> None:
    """Dropping a failed read meant "no ADRs exist" -> 0001, which collides with a real ADR.

    The reservation namespace is legitimately empty today, so the contents read is the only source
    of used numbers: one 403 was enough, and the CAS cannot catch it because no *ref* holds 0001.
    """
    posted: list[str] = []

    def transport(method: str, path: str, body: Any = None) -> tuple[int, Any]:
        if method == "GET" and path.endswith("/git/matching-refs/adr-reservations"):
            return 200, []
        if method == "GET" and path.endswith("/contents/docs/adr"):
            return status, None
        if method == "GET" and "git/ref/heads/main" in path:
            return 200, {"object": {"sha": HEAD}}
        if method == "POST" and path.endswith("/git/refs"):
            posted.append(body["ref"])
            return 201, {}
        return 200, None

    monkeypatch.setattr(claim, "_request", transport)
    assert claim.main.__module__  # module import sanity
    with pytest.raises(claim.ClaimError, match="refusing to guess"):
        claim._cmd_reserve_adr(_args(attempts=8))
    assert posted == [], "no reservation may be created when the number is a guess"


def test_generation_never_comes_from_commit_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """committedDate is client-settable, so the generation must come from the activity API alone."""
    fake = _install(monkeypatch, Fake(_routes()))
    claim._generation(7)
    paths = [path for _, path in fake.calls]
    assert any("/activity?ref=refs/heads/agent/issue-7" in p for p in paths)
    assert not [p for p in paths if "/commits" in p or "graphql" in p]


# --------------------------------------------------------------------- ADR reservation


def test_reserve_adr_skips_taken_numbers_and_never_uses_a_tag(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A non-version tag breaks hatch-vcs version derivation and turns main red."""
    posted: list[str] = []

    def transport(method: str, path: str, body: Any = None) -> tuple[int, Any]:
        if method == "GET" and path.endswith("/git/matching-refs/adr-reservations"):
            return 200, [{"ref": "refs/adr-reservations/0058"}]
        if method == "GET" and path.endswith("/contents/docs/adr"):
            return 200, [{"name": "0057-a.md"}, {"name": "0052-b.md"}]
        if method == "GET" and "git/ref/heads/main" in path:
            return 200, {"object": {"sha": HEAD}}
        if method == "POST" and path.endswith("/git/refs"):
            posted.append(body["ref"])
            return (422, None) if body["ref"].endswith("0059") else (201, {})
        return 200, None

    monkeypatch.setattr(claim, "_request", transport)
    claim._cmd_reserve_adr(_args(attempts=8))
    assert json.loads(capsys.readouterr().out)["adr"] == "0060"
    assert posted == ["refs/adr-reservations/0059", "refs/adr-reservations/0060"]
    assert not [ref for ref in posted if ref.startswith("refs/tags/")]


# ------------------------------------------------------------ the frozen approval digest

# A frozen snapshot and the digest it must always produce. This normalization is a **published
# contract**: the markers on #188, #189, #216 and #218 were computed with it, and all four were
# re-verified against this re-homed implementation and reproduced their published digests
# byte-for-byte on 2026-07-30. Those checks need the GitHub API, so this pinned pair is what CI can
# enforce offline. It moved here with the function it pins, from the withdrawn lease helper.
PIN_TITLE = "build(packaging): pin the wheel"
PIN_BODY = "Acceptance criteria\n\n- [ ] one source of truth\n"
PIN_DIGEST = "9906a25c28495a649934b2e809e2b70c136b724b25b025db6907f1797100e0dc"
NON_ASCII_DIGEST = "329909edc2df9090ff2861ea36485b53039d0bd28f79d91eeac2bb2a7b9cb8c8"


def test_the_scope_digest_is_pinned_for_a_known_snapshot() -> None:
    assert claim._scope_hash(PIN_TITLE, PIN_BODY) == PIN_DIGEST


@pytest.mark.parametrize(
    "body",
    [
        "Acceptance criteria\r\n\r\n- [ ] one source of truth\r\n",
        "Acceptance criteria\r\r- [ ] one source of truth\r",
        "Acceptance criteria\n\n- [ ] one source of truth\n\n\n\n",
    ],
    ids=["crlf", "cr", "extra-trailing-newlines"],
)
def test_the_scope_digest_normalizes_line_endings_and_trailing_newlines(body: str) -> None:
    assert claim._scope_hash(PIN_TITLE, body) == PIN_DIGEST


def test_the_scope_digest_is_pinned_for_a_non_ascii_snapshot() -> None:
    """Pins ensure_ascii=False: \\uXXXX-escaping before hashing changes every non-ASCII issue."""
    body = "Rationale: the γ correction factor — see Hellenkamp 2018.\n"
    assert claim._scope_hash("fix(fret): γ factor", body) == NON_ASCII_DIGEST


def test_the_scope_digest_separates_title_from_body() -> None:
    """Moving text across the title/body boundary must change the digest."""
    assert claim._scope_hash("ab", "c") != claim._scope_hash("a", "bc")


def test_scope_hash_reads_the_snapshot_from_the_api_and_renders_a_binding_marker(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """One snapshot source for both sides of the approval.

    The maintainer renders a marker and an agent later recomputes the digest; if those read the
    snapshot differently the approval silently stops binding. Reading a body from a file the caller
    prepared is how a prepended BOM could change the digest of an unchanged issue - there is no file
    in this path at all - so the two cannot diverge that way.
    """
    routes = _routes(
        {("GET", "/repos/bioedca/tether/issues/7"): (200, _issue(title=PIN_TITLE, body=PIN_BODY))}
    )
    monkeypatch.setattr(claim, "_request", Fake(routes))
    claim._cmd_scope_hash(_args(issue=7))
    printed = json.loads(capsys.readouterr().out)
    assert printed["criteria_sha256"] == PIN_DIGEST
    marker = '<!-- tether-agent-ready {"version":1,"criteria_sha256":"' + PIN_DIGEST + '"} -->'
    assert printed["marker"] == marker
    # Deliberately NOT via _install: the real _scope_hash must run on both sides, so this asserts
    # the rendered marker is the exact form _approval_binds accepts, not merely a similar one.
    assert claim._approval_binds(
        {"title": PIN_TITLE, "body": PIN_BODY},
        [{"user": {"login": "bioedca"}, "body": f"Approved as written.\n\n{marker}\n"}],
        "bioedca",
    )


def test_scope_hash_refuses_a_pull_request_number(monkeypatch: pytest.MonkeyPatch) -> None:
    """A PR's title and body are not an approvable scope, so digesting one is a wrong answer.

    `/issues/{n}` answers for pull requests too, so the refusal has to be explicit. It lives in
    `_issue`, shared with the eligibility path, rather than being repeated in each caller.
    """
    routes = {("GET", "/repos/bioedca/tether/issues/7"): (200, {"pull_request": {"url": "x"}})}
    monkeypatch.setattr(claim, "_request", Fake(routes))
    with pytest.raises(claim.ClaimError, match="not an issue"):
        claim._cmd_scope_hash(_args(issue=7))


def _args(**values: Any) -> Any:
    defaults = {"vendor": "claude", "owner": "bioedca", "base": None, "attempts": 16}
    defaults.update(values)
    return type("Args", (), defaults)()


# ------------------------------------------------- the activity index's read-after-write lag


class LaggingFake(Fake):
    """Answers the activity endpoint with nothing until the ``n``-th read.

    The shape measured live on this repository's first claim: `POST /git/refs` returned 201 and the
    `branch_creation` entry was not yet readable. It appeared moments later.
    """

    def __init__(self, routes: Routes, *, appears_on: int) -> None:
        super().__init__(routes)
        self.appears_on = appears_on
        self.activity_reads = 0

    def __call__(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        if method == "GET" and "/activity" in path:
            self.activity_reads += 1
            if self.activity_reads < self.appears_on:
                self.calls.append((method, path))
                return 200, []
        return super().__call__(method, path, body)


def test_a_late_activity_record_is_waited_for_not_treated_as_absent(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The defect the pilot's very first live claim hit, and it failed in the worst available way.

    The ref existed, so the mutex was taken and every other agent got 422 - while the caller raised
    before the label mirror, leaving the issue reading `status:ready` with no `agent:*` label. The
    board said the work was free and the mutex said it was taken, and the caller was told to stop.
    """
    monkeypatch.setattr(claim.time, "sleep", lambda _seconds: None)
    fake = _install(monkeypatch, LaggingFake(_routes(), appears_on=3))
    claim._cmd_claim(_args(issue=7))

    assert fake.activity_reads >= 3, "it must actually re-read rather than sleep once and give up"
    assert json.loads(capsys.readouterr().out)["generation"] == 42
    # The mirror runs exactly once on the success path, so the board and the mutex agree.
    adds = [c for c in fake.calls if c[0] == "POST" and c[1].endswith("/labels")]
    assert len(adds) == 2, f"agent:<vendor> and status:in-progress, once each: {adds}"


def test_a_record_that_never_appears_leaves_the_ref_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It must NOT delete, and the reason is a TOCTOU both reviewers refused independently.

    An earlier version deleted the ref after checking its tip still equalled the SHA this call
    created it at. `GET`-compare-`DELETE` is not atomic, `DELETE /git/refs` takes no expected-SHA
    precondition, and the base SHA is **not a claim identity**: a successor claiming the same issue
    while the default branch has not moved creates the ref at exactly the same SHA, so the guard
    passes on a ref that is no longer ours.

    Leaking a claim costs one reaper cycle. Deleting a successor's claim puts two workers on one
    issue - the single failure the mutex exists to prevent. This asserts the trade by asserting on
    what does not happen.
    """
    monkeypatch.setattr(claim.time, "sleep", lambda _seconds: None)
    fake = _install(monkeypatch, LaggingFake(_routes(), appears_on=10_000))

    with pytest.raises(claim.ClaimError, match="NOT deleted"):
        claim._cmd_claim(_args(issue=7))

    assert not [c for c in fake.calls if c[0] == "DELETE"], (
        f"no delete may be issued on this path at all: {fake.calls}"
    )


def test_the_same_base_sha_interleaving_cannot_reach_a_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CodeRabbit's exact interleaving, pinned so a future 'safe' guard cannot reintroduce it.

    The reaper deletes the ref and a successor recreates it at the SAME unchanged default-branch
    SHA. Every tip comparison a claimant could make still passes, because the SHA is not an
    identity. The only defence is not having a delete on this path.
    """
    monkeypatch.setattr(claim.time, "sleep", lambda _seconds: None)
    routes = _routes(
        {
            # A successor's ref, indistinguishable from ours: identical SHA.
            ("GET", "/repos/bioedca/tether/git/ref/heads/agent/issue-7"): (
                200,
                {"object": {"sha": HEAD}},
            )
        }
    )
    fake = _install(monkeypatch, LaggingFake(routes, appears_on=10_000))

    with pytest.raises(claim.ClaimError, match="NOT deleted"):
        claim._cmd_claim(_args(issue=7))

    assert not [c for c in fake.calls if c[0] == "DELETE"]


def test_the_unfenced_message_tells_the_caller_not_to_reclaim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The caller's next move differs from every other failure, so the message must say it.

    Exit 4 means *lost, stand down*. This is *held but unusable*, and re-claiming would get a 422
    and read as lost - so the message says do not re-claim, names the reaper as the resolver, and
    points at #303 for the case where the record never lands at all.
    """
    monkeypatch.setattr(claim.time, "sleep", lambda _seconds: None)
    _install(monkeypatch, LaggingFake(_routes(), appears_on=10_000))
    with pytest.raises(claim.ClaimError) as info:
        claim._cmd_claim(_args(issue=7))
    message = str(info.value)
    assert "Do not re-claim" in message
    assert "reaper" in message
    assert "#303" in message, "the residual is a filed issue, not a docstring note"


def test_the_wait_is_bounded_and_does_not_poll_indefinitely() -> None:
    """A read-after-write wait, not the coordination polling ADR-0057 retired.

    That was 977 `wait_*` calls waiting on other agents. This waits on one server's own index for a
    write it has already acknowledged, and it is bounded by a constant rather than by an outcome.
    """
    assert claim.GENERATION_ATTEMPTS[0] == 0.0, "the first read happens immediately"
    assert sum(claim.GENERATION_ATTEMPTS) <= 20.0, "a claim must not hang on a lagging index"


# ----------------------------------------- transport is not a verdict, and TLS is not the network


def _cert_error(message: str) -> Any:
    """An ``SSLCertVerificationError`` shaped like the one OpenSSL actually raises.

    ``verify_message`` is the path-free field the fix reads, and it is not settable through the
    constructor.
    """
    error = claim.ssl.SSLCertVerificationError(1, f"[SSL: CERTIFICATE_VERIFY_FAILED] {message}")
    error.verify_message = message
    return error


def _raises(exc: BaseException) -> Any:
    def call(*_args: Any, **_kwargs: Any) -> Any:
        raise exc

    return call


def _transport(monkeypatch: pytest.MonkeyPatch, reason: BaseException) -> None:
    """Make every real HTTP call fail at the socket, the way a proxy or an outage does."""
    monkeypatch.setattr(claim, "_token", lambda: "t")
    monkeypatch.setattr(
        claim.urllib.request, "urlopen", _raises(claim.urllib.error.URLError(reason))
    )


def test_a_transport_failure_on_the_eligibility_read_is_an_error_not_a_verdict(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The defect: an approved issue reported to an agent as unapproved.

    Exit 3 is ``EXIT_INELIGIBLE``, which ``AGENTS.md`` defines as *do not work it*, and a compliant
    agent obeys it. So the blanket ``except ClaimError`` around ``_check_eligible`` turned every
    network or TLS failure into a scope verdict about work nobody managed to read.
    """
    monkeypatch.setattr(claim, "_check_eligible", _raises(claim.TransportError("no answer")))
    with pytest.raises(claim.TransportError):
        claim._cmd_claim(_args(issue=7))
    assert "ineligible" not in capsys.readouterr().err


def test_that_transport_failure_reaches_the_shell_as_exit_two(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """End to end through ``main``, which is what a worker's shell actually sees."""
    monkeypatch.setattr(claim, "_check_eligible", _raises(claim.TransportError("no answer")))
    monkeypatch.setattr(
        claim.sys, "argv", ["claim.py", "claim", "--issue", "7", "--vendor", "claude"]
    )
    assert claim.main() == 2
    err = capsys.readouterr().err
    assert err.startswith("error:"), err
    assert "ineligible" not in err


def test_a_genuine_ineligibility_still_exits_three(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The two must not collapse into one code again, in either direction.

    An unapproved issue is a *decided answer* and stays exit 3; the case above is the absence of an
    answer and is exit 2.
    """
    routes = _routes({("GET", "/repos/bioedca/tether/issues/7/comments"): (200, [])})
    fake = _install(monkeypatch, Fake(routes))
    with pytest.raises(SystemExit) as exit_info:
        claim._cmd_claim(_args(issue=7))
    assert exit_info.value.code == claim.EXIT_INELIGIBLE
    assert "ineligible" in capsys.readouterr().err
    assert not [c for c in fake.calls if c[0] == "POST" and "git/refs" in c[1]]


def test_a_certificate_failure_names_the_certificate_and_the_one_remedy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ "Unreachable" was the wrong cause, and it sent readers hunting a nonexistent outage."""
    monkeypatch.delenv(claim.STRICT_OPT_OUT, raising=False)
    _transport(monkeypatch, _cert_error("Basic Constraints of CA cert not marked critical"))
    with pytest.raises(claim.TransportError) as info:
        claim._request("GET", "/repos/bioedca/tether/issues/7")
    message = str(info.value)
    assert "certificate failed verification" in message
    assert "Basic Constraints of CA cert not marked critical" in message
    assert "VERIFY_X509_STRICT" in message, "the message must name the cause it observed"
    assert claim.STRICT_OPT_OUT in message, "and the one supported remedy"
    assert "host is reachable" in message, "it must not read as a network outage"


def test_an_unreachable_host_is_still_reported_as_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other side of the distinction: a genuine outage must not mention certificates."""
    _transport(monkeypatch, OSError(11001, "getaddrinfo failed"))
    with pytest.raises(claim.TransportError) as info:
        claim._request("GET", "/repos/bioedca/tether/issues/7")
    message = str(info.value)
    assert "could not be reached" in message
    assert "getaddrinfo failed" in message
    assert "certificate" not in message


def test_a_transport_message_never_carries_a_filesystem_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ClaimError`` promises its message carries no path, and ``OSError`` renders its filename.

    A misdirected ``SSL_CERT_FILE`` is exactly how a private path would otherwise reach a log.
    """
    _transport(monkeypatch, FileNotFoundError(2, "No such file or directory", "/home/me/ca.pem"))
    with pytest.raises(claim.TransportError) as info:
        claim._request("GET", "/repos/bioedca/tether/issues/7")
    assert "/home/me/ca.pem" not in str(info.value)


def test_the_opt_out_relaxes_conformance_only_and_never_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole security argument of ADR-0061, asserted rather than described.

    Clearing ``VERIFY_X509_STRICT`` restores pre-3.13 *conformance* checking. It must not touch
    ``verify_mode`` or ``check_hostname``: this tool sends a GitHub token, and a context that
    skipped either would hand it to whoever answered.
    """
    monkeypatch.setenv(claim.STRICT_OPT_OUT, "1")
    monkeypatch.setattr(claim, "_ANNOUNCED", False)
    relaxed = claim._ssl_context()
    stock = claim.ssl.create_default_context()
    # Exact, and independent of the interpreter: whatever the default context sets, the opt-out
    # differs from it by that single flag and nothing else. Asserting `not flags & STRICT` alone
    # would pass vacuously below 3.13, where the flag is off to begin with - which is the whole
    # reason this defect is version-dependent.
    assert relaxed.verify_flags == stock.verify_flags & ~claim.ssl.VERIFY_X509_STRICT
    assert relaxed.verify_mode == claim.ssl.CERT_REQUIRED
    assert relaxed.check_hostname is True


def test_only_the_literal_one_arms_the_opt_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """ "An interlock that fires on anything truthy is not an interlock" (#315, maintainer).

    `true`, `yes` and `TRUE` are the spellings a shell profile picks up by habit, and each would
    silently relax a TLS check on a path that carries a GitHub token. Only `1` counts.

    Surrounding whitespace is tolerated and asserted separately below: a trailing space picked up
    from a `.env` line is not an alternative truthy spelling, and refusing it would turn a set
    variable into a silent no-op — the one outcome the announcement exists to prevent.
    """
    stock = claim.ssl.create_default_context()
    for value in ("", "0", "false", "no", "true", "yes", "TRUE", "on", "2", "-1", None):
        monkeypatch.setattr(claim, "_ANNOUNCED", False)
        if value is None:
            monkeypatch.delenv(claim.STRICT_OPT_OUT, raising=False)
        else:
            monkeypatch.setenv(claim.STRICT_OPT_OUT, value)
        assert not claim._nonstrict_x509_allowed(), f"{value!r} was read as an opt-in"
        context = claim._ssl_context()
        assert context.verify_flags == stock.verify_flags
        assert context.verify_mode == claim.ssl.CERT_REQUIRED
        assert context.check_hostname is True


def test_stray_whitespace_around_the_one_still_arms_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """The deliberate exception to "literal `1`", and the reason it is not a loophole.

    `1` padded by whitespace is unambiguously the same intent; no *other* value becomes acceptable.
    """
    stock = claim.ssl.create_default_context()
    for value in ("1", " 1", "1 ", " 1 "):
        monkeypatch.setenv(claim.STRICT_OPT_OUT, value)
        monkeypatch.setattr(claim, "_ANNOUNCED", False)
        # Asserted on the predicate, not only on the flags: below 3.13 the flag is already clear,
        # so a flags-only check would pass for every value and prove nothing.
        assert claim._nonstrict_x509_allowed(), f"{value!r} did not arm the opt-out"
        relaxed = claim._ssl_context().verify_flags
        assert relaxed == stock.verify_flags & ~claim.ssl.VERIFY_X509_STRICT


def test_the_relaxation_announces_itself_once_per_process(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A process that quietly stopped enforcing a check reads like one that never needed to.

    Once per process rather than per request: `_paginate` can make twenty calls, and a warning
    repeated twenty times is one nobody reads.
    """
    monkeypatch.setenv(claim.STRICT_OPT_OUT, "1")
    monkeypatch.setattr(claim, "_ANNOUNCED", False)
    for _ in range(3):
        claim._ssl_context()
    err = capsys.readouterr().err
    assert err.count("notice:") == 1, err
    assert claim.STRICT_OPT_OUT in err
    assert "hostname verification remain enabled" in err


def test_the_default_says_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The announcement marks the exception, so it must not fire on the ordinary path."""
    monkeypatch.delenv(claim.STRICT_OPT_OUT, raising=False)
    monkeypatch.setattr(claim, "_ANNOUNCED", False)
    claim._ssl_context()
    assert capsys.readouterr().err == ""


@pytest.mark.skipif(sys.version_info < (3, 13), reason="VERIFY_X509_STRICT is off before 3.13")
def test_the_default_really_is_strict_on_the_interpreters_that_have_it() -> None:
    """The other half: `untouched` only means `strict` where CPython makes it so.

    3.13 is where `create_default_context()` turned the flag on, and 3.13/3.14 are supported
    interpreters here - so on those, the shipped default must be the strict one.
    """
    assert claim._ssl_context().verify_flags & claim.ssl.VERIFY_X509_STRICT


def test_the_claim_tool_never_reaches_for_a_blunter_instrument() -> None:
    """#315's non-goal, bound to the source rather than left as a promise in a docstring.

    Docstrings are blanked before the check, because this file's own prose names these mechanisms
    in order to rule them out - a plain substring search over the source flags that as a violation.
    `ast` drops comments outright, so unparsing covers those too.
    """
    tree = ast.parse((ROOT / ".agents" / "bin" / "claim.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            first.value.value = ""
    code = ast.unparse(tree)
    for forbidden in ("_create_unverified_context", "PYTHONHTTPSVERIFY", "CERT_NONE"):
        assert forbidden not in code, f"claim.py must never use {forbidden}"
    assert "check_hostname" not in code, "the default (on) must never be assigned away"


def test_no_other_subcommand_turns_a_transport_failure_into_a_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The audit #315 asks for: ``check``, ``release`` and ``reserve-adr`` must not share it.

    They never caught ``ClaimError`` at all, so each already reached ``main``'s exit 2 - but "shown
    not to have it" is worth binding, since the tempting fix for any of them is the same blanket
    ``except`` that caused this.
    """
    boom = _raises(claim.TransportError("no answer"))
    monkeypatch.setattr(claim, "_generation", boom)
    monkeypatch.setattr(claim, "_ref_exists", boom)
    monkeypatch.setattr(claim, "_default_sha", boom)
    for call in (
        lambda: claim._cmd_check(_args(issue=7, generation=42)),
        lambda: claim._cmd_release(_args(issue=7, generation=42, vendor="claude")),
        lambda: claim._cmd_reserve_adr(_args(attempts=8)),
    ):
        with pytest.raises(claim.TransportError):
            call()
