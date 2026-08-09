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


# A claimable issue is a GROOMED one, and since #336 that includes an Execution-autonomy
# declaration the body carries itself. The default fixture therefore declares it; a test that wants
# an ungroomed body passes `body=` explicitly.
GROOMED_BODY = "Acceptance criteria\n\n## Execution autonomy\n\nagent-can-do-alone\n"


def _issue(**overrides: Any) -> dict[str, Any]:
    issue = {
        "state": "open",
        "title": "feat(io): a thing",
        "body": GROOMED_BODY,
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


# ------------------------------------------------------- what the issue says about itself (#336)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("## Execution autonomy\n\nexternal/human action required\n", "human action"),
        ("## Execution autonomy\n\nmaintainer decision required\n", "maintainer decision"),
        ("Acceptance criteria\n", "declares no Execution autonomy"),
        (
            "## Execution autonomy\n\n`needs-human-action` - a desktop installer\n",
            "needs-human-action",
        ),
        (
            "## Execution autonomy\n\n`agent-can-do-alone` for the drafting; the rest is a "
            "maintainer decision\n",
            "maintainer decision",
        ),
        # Greptile on #428: a separator must not decide a safety verdict. This opens with an
        # admitting prefix and names its restriction with a hyphen, so a literal-token match saw
        # nothing and ADMITTED it - a fail-open in the one gate whose purpose is failing closed.
        (
            "## Execution autonomy\n\nagent-can-do-alone; maintainer-decision required\n",
            "maintainer decision",
        ),
        (
            "## Execution autonomy\n\nagent-can-do-alone, needs_human_action for the upload\n",
            "human action",
        ),
    ],
    ids=[
        "external-human",
        "maintainer-decision",
        "absent",
        "legacy-spelling",
        "split-declaration",
        "hyphenated-restriction",
        "underscored-restriction",
    ],
)
def test_an_issue_whose_body_says_no_agent_can_do_it_is_not_claimable(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    body: str,
    expected: str,
) -> None:
    """The regression for #336, and it must fail against the pre-#336 `_check_eligible`.

    Every issue here is open, `status:ready`, unassigned and carries a marker that binds - so all
    four checks that existed before this passed, and the mutex was issued. #246 is why that matters:
    its body records that *"a wrong first upload cannot be replaced (PyPI forbids re-uploading a
    version)"*, so an agent reaching that step produces a permanent public artifact with wrong
    metadata. Most admission mistakes waste a worker; that one cannot be undone.
    """
    routes = _routes({("GET", "/repos/bioedca/tether/issues/7"): (200, _issue(body=body))})
    fake = _install(monkeypatch, Fake(routes))
    with pytest.raises(SystemExit) as exit_info:
        claim._cmd_claim(_args(issue=7))
    assert exit_info.value.code == claim.EXIT_INELIGIBLE
    assert expected in capsys.readouterr().err
    # Eligibility is a precondition of the claim: the ref must never have been created.
    assert not [c for c in fake.calls if c[0] == "POST" and "git/refs" in c[1]]


@pytest.mark.parametrize(
    "body",
    [
        "## Execution autonomy\n\nagent-can-do-alone\n",
        "### Execution autonomy\n\n`agent-can-do-alone`.\n",
        "## Autonomy\n\nagent-can-do-alone\n",
        "- **Autonomy:** agent-can-do-alone\n",
        "- **Autonomy:** agent can complete alone\n",
        "## Execution autonomy\n\n`agent-can-do-alone`, unless the sizing note says otherwise.\n",
        "<!-- tether-grooming-v1 -->\n\n- **Autonomy after unblock:** agent-can-do-alone\n",
    ],
    ids=[
        "heading",
        "backticked",
        "short-heading",
        "bullet",
        "legacy-prose",
        "qualified",
        "after-unblock",
    ],
)
def test_every_spelling_of_agent_can_do_alone_in_the_live_corpus_admits(
    monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    """The spellings the live corpus uses, plus the qualified and after-unblock forms.

    `after-unblock` admits deliberately: it answers *what kind of work is this*, and whether the
    issue is still blocked is the `status:` label's question. #214's grooming block reads
    `**Status:** unblocked` two lines above `**Autonomy after unblock:**`, so refusing on the
    qualifier would bar work that is ready.
    """
    routes = _routes({("GET", "/repos/bioedca/tether/issues/7"): (200, _issue(body=body))})
    fake = _install(monkeypatch, Fake(routes))
    claim._cmd_claim(_args(issue=7))
    assert [c for c in fake.calls if c[0] == "POST" and "git/refs" in c[1]]


def test_no_refusal_token_depends_on_the_separator_it_is_written_with() -> None:
    """Re-spelling an `AUTONOMY_REFUSES` entry must not change any verdict.

    The flattener's whole job is that a separator never decides a safety verdict, and the
    table is the one place a future edit can quietly undo that. `external/human` is the only
    entry carrying a `/`, and until this test existed the flattener widened `[\\s_-]+` but not
    `/`: writing that entry as `external - human` instead left it unable to match the body it
    was there to refuse, re-opening the exact fail-open Greptile found on #428 - silently, in a
    table edit that reads as a formatting change.

    So this asserts the property over **every** entry rather than the one that broke. Each is
    re-spelled with each separator and must still refuse a body that opens with an admitting
    prefix, which is the case a literal-token match gets wrong.
    """
    separators = (" ", "-", "_", "/")
    for token in claim.AUTONOMY_REFUSES:
        canonical = claim._flatten_autonomy(token)
        body = f"## Execution autonomy\n\nagent-can-do-alone; {token} applies here\n"
        for separator in separators:
            respelled = canonical.replace(" ", separator)
            assert claim._flatten_autonomy(respelled) == canonical, (
                f"{token!r} re-spelled as {respelled!r} flattens differently"
            )
            patched = tuple(
                respelled if entry == token else entry for entry in claim.AUTONOMY_REFUSES
            )
            original = claim.AUTONOMY_REFUSES
            try:
                claim.AUTONOMY_REFUSES = patched
                assert claim._autonomy_refusal(body) is not None, (
                    f"{token!r} written as {respelled!r} stopped refusing - fail-open"
                )
            finally:
                claim.AUTONOMY_REFUSES = original


def test_a_restrictive_declaration_governs_wherever_it_sits_in_the_source(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two declarations in one source: the restrictive one governs, whichever is found first.

    `_declared_autonomy` used to return the **first** match and stop, and it looked for bullets
    before headings. So a body whose `## Execution autonomy` section said `maintainer decision
    required` was admitted outright if any bullet anywhere else in it read `agent-can-do-alone` -
    the heading was never read. That is a fail-open in the gate whose entire purpose is to fail
    closed, reachable by an ordinary issue body that declares the same thing twice.

    The precedence that *is* deliberate is between sources: a grooming block supersedes the body
    above it, which the neighbouring test covers. Within one source there is no such argument -
    two disagreeing declarations mean the issue is not clearly groomed, and the restrictive half
    governs exactly as it already does when one value names both.

    Asserted in both orders, because the defect was an ordering artefact and a fix that only
    reversed the search order would pass one and fail the other.
    """
    bodies = {
        "heading first": (
            "## Execution autonomy\n\nmaintainer decision required\n\n"
            "- **Autonomy:** agent-can-do-alone\n"
        ),
        "bullet first": (
            "- **Autonomy:** agent-can-do-alone\n\n"
            "## Execution autonomy\n\nmaintainer decision required\n"
        ),
        # Two headings, which the first fix still got wrong: it collected every bullet but took
        # only `_AUTONOMY_HEADING.search()`, so a second `## Execution autonomy` restricting the
        # issue was invisible behind an admitting first one. Same defect, one level down.
        "two headings, restrictive second": (
            "## Execution autonomy\n\nagent-can-do-alone\n\n"
            "## Execution autonomy\n\nmaintainer decision required\n"
        ),
        "two headings, restrictive first": (
            "## Execution autonomy\n\nmaintainer decision required\n\n"
            "## Execution autonomy\n\nagent-can-do-alone\n"
        ),
        # And two bullets, for the same reason in the other direction.
        "two bullets, restrictive second": (
            "- **Autonomy:** agent-can-do-alone\n- **Autonomy:** maintainer decision required\n"
        ),
    }
    for where, body in bodies.items():
        assert claim._autonomy_refusal(body) is not None, (
            f"{where}: a restrictive declaration was ignored - fail-open"
        )
        routes = _routes({("GET", "/repos/bioedca/tether/issues/7"): (200, _issue(body=body))})
        fake = _install(monkeypatch, Fake(routes))
        with pytest.raises(SystemExit) as exit_info:
            claim._cmd_claim(_args(issue=7))
        assert exit_info.value.code == 3, where
        assert not [c for c in fake.calls if c[0] == "POST" and "git/refs" in c[1]], (
            f"{where}: a claim ref was created for an issue a maintainer must decide"
        )
        assert "maintainer" in capsys.readouterr().err


def test_a_grooming_block_supersedes_a_stale_body_declaration(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The grooming block wins, and it is the restrictive one here.

    Those blocks exist to restate readiness after the body above them went stale, so reading the
    body first would admit on a value a grooming pass had already replaced.
    """
    body = (
        "## Execution autonomy\n\nagent-can-do-alone\n\n"
        "<!-- tether-grooming-v1 -->\n\n"
        "- **Status:** blocked.\n"
        "- **Autonomy:** needs maintainer input (the dataset decision)\n"
    )
    routes = _routes({("GET", "/repos/bioedca/tether/issues/7"): (200, _issue(body=body))})
    fake = _install(monkeypatch, Fake(routes))
    with pytest.raises(SystemExit) as exit_info:
        claim._cmd_claim(_args(issue=7))
    assert exit_info.value.code == claim.EXIT_INELIGIBLE
    assert "maintainer input" in capsys.readouterr().err
    assert not [c for c in fake.calls if c[0] == "POST" and "git/refs" in c[1]]


def test_the_refusal_names_the_declared_value_so_a_worker_knows_not_to_retry(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    body = "## Execution autonomy\n\nexternal/human action required\n"
    routes = _routes({("GET", "/repos/bioedca/tether/issues/7"): (200, _issue(body=body))})
    _install(monkeypatch, Fake(routes))
    with pytest.raises(SystemExit):
        claim._cmd_claim(_args(issue=7))
    err = capsys.readouterr().err
    assert "external/human action required" in err
    assert "agent-can-do-alone" in err


def test_autonomy_is_read_before_the_comment_page_is_fetched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A body that bars agent work is decided from the issue alone - no comment pagination.

    Not a micro-optimisation: `_paginate` walks up to twenty pages, and an issue no agent may work
    should cost one GET to refuse.
    """
    body = "## Execution autonomy\n\nexternal/human action required\n"
    routes = _routes({("GET", "/repos/bioedca/tether/issues/7"): (200, _issue(body=body))})
    fake = _install(monkeypatch, Fake(routes))
    with pytest.raises(SystemExit):
        claim._cmd_claim(_args(issue=7))
    assert not [c for c in fake.calls if "comments" in c[1]]


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


class _Answer:
    """The little of a ``urlopen`` result ``_request`` actually touches, and no more."""

    def __init__(self, payload: bytes | BaseException, status: int = 200) -> None:
        """Carry either the bytes to hand back or the exception to raise instead of them."""
        self._payload = payload
        self.status = status

    def __enter__(self) -> _Answer:
        """``urlopen`` is used as a context manager, so this stands in for one."""
        return self

    def __exit__(self, *_exc: object) -> bool:
        """Never suppress: a test that swallowed its own failure would pass for nothing."""
        return False

    def read(self) -> bytes:
        """Hand back the body, or fail the way a real response fails part-way through one."""
        if isinstance(self._payload, BaseException):
            raise self._payload
        return self._payload  # type: ignore[return-value]


@pytest.mark.parametrize(
    ("payload", "named"),
    [
        pytest.param(b"<html>502 Bad Gateway</html>", "JSONDecodeError", id="not-json"),
        pytest.param(claim.http.client.IncompleteRead(b"{"), "IncompleteRead", id="cut-short"),
        pytest.param(TimeoutError("timed out"), "TimeoutError", id="stalled-mid-read"),
    ],
)
def test_an_answer_that_cannot_be_read_leaves_request_as_a_claim_error(
    monkeypatch: pytest.MonkeyPatch, payload: bytes | BaseException, named: str
) -> None:
    """``ClaimError`` is the promise every caller holds; only the transport half of it was kept.

    ``_request`` converted a socket failure and returned an HTTP status, so both of those arrive as
    something a caller can handle. The success path did not: ``response.read()`` and ``json.loads``
    ran *inside* the ``try`` but past every ``except``, so a proxy's HTML error page or a connection
    dropped mid-body came out as a raw ``ValueError`` or ``IncompleteRead``.

    That is not a tidiness point. ``triage._verdict_at_head`` documents itself as failing **soft** —
    an unreadable comment list means *no verdict seen*, withholding an authority rather than
    granting one — and implements it as ``except claim.ClaimError``. For anything but a transport
    error the documented soft failure was a hard crash of the whole triage run (CodeRabbit on #407).
    """
    monkeypatch.setattr(claim, "_token", lambda: "t")
    monkeypatch.setattr(claim.urllib.request, "urlopen", lambda *_a, **_k: _Answer(payload))
    with pytest.raises(claim.ClaimError) as caught:
        claim._request("GET", "/repos/o/r/issues/1/comments")
    assert named in str(caught.value)
    assert "could not be read" in str(caught.value)


class _UnreadableBody:
    """An ``HTTPError`` file object that fails where ``error.read()`` reads it."""

    def read(self, *_a: object) -> bytes:
        """The failure itself: the body ends before the length the headers promised."""
        raise claim.http.client.IncompleteRead(b"{")

    def close(self) -> None:
        """``HTTPError`` closes its file object on the way out, and that must not fail too."""
        return None


def test_a_status_survives_a_body_that_cannot_be_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """The same defect one branch over, and Python's scoping is why (CodeRabbit's `Major` on #407).

    ``error.read()`` runs INSIDE the ``HTTPError`` handler, and an exception raised inside a handler
    is not offered to its siblings — so the guard added for the success path could never have caught
    this one.

    Degraded rather than raised, deliberately. The status line arrived intact, so the answer is
    known even though the body is not, and it is the same loss the unparseable-body branch beside it
    already accepts. ``_request`` promises HTTP errors are *returned* — 422 is an answer — and
    raising here would falsify that for a 404 whose body happened to be truncated.
    """
    monkeypatch.setattr(claim, "_token", lambda: "t")
    monkeypatch.setattr(
        claim.urllib.request,
        "urlopen",
        _raises(
            claim.urllib.error.HTTPError("https://api", 404, "Not Found", {}, _UnreadableBody())  # type: ignore[arg-type]
        ),
    )
    assert claim._request("GET", "/repos/o/r/issues/1") == (404, None)


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
    if not claim._strict_is_the_default():
        # Below 3.13 the flag is off, so strict conformance genuinely is not the cause and the
        # remedy must not be offered. Saying otherwise is the confidently-wrong message again.
        assert "not enabled on this interpreter" in message
        assert claim.STRICT_OPT_OUT not in message
        return
    assert "VERIFY_X509_STRICT" in message, "the message must name the cause it observed"
    assert claim.STRICT_OPT_OUT in message, "and the one supported remedy"
    assert "host is reachable" in message, "it must not read as a network outage"


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (claim.ClaimError("no GitHub token: set GH_TOKEN or run gh auth login"), "no GitHub token"),
        (claim.ClaimError("#7 comments could not be read"), "could not be read"),
        (claim.TransportError("the GitHub API could not be reached (gaierror)"), "reached"),
    ],
    ids=["no-token", "http-error", "transport"],
)
def test_only_a_decided_answer_reaches_exit_three(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: Exception,
    expected: str,
) -> None:
    """Codex P1 on #388: subtyping the *failures* left the guarantee false.

    `_token` raises a plain `ClaimError` when there is no GitHub token, and `_issue`/`_paginate`
    raise one on any 401, 403 or 5xx. None is a verdict about the issue, and all of them slipped
    past an `except TransportError` arm into the blanket `except ClaimError` below it. Enumerating
    what *is* a verdict is the fix, and this is the test that would have caught the first attempt.
    """
    monkeypatch.setattr(claim, "_check_eligible", _raises(failure))
    with pytest.raises(claim.ClaimError) as info:
        claim._cmd_claim(_args(issue=7))
    assert not isinstance(info.value, claim.IneligibleError)
    assert expected in str(info.value)
    assert "ineligible" not in capsys.readouterr().err


def test_the_decided_answers_are_enumerated_and_stay_enumerated() -> None:
    """Bind the enumeration itself, since its whole value is that it is closed.

    Writing this test found the fifth: `_issue` refuses a pull-request number, which *is* a verdict
    — the server told us what the number is — while the `status != 200` beside it is not. A later
    edit that reaches for `IneligibleError` somewhere new shows up here rather than as a worker
    silently skipping approved work.

    The sixth arrived with #336: what the issue *body* declares about the autonomy the work needs.
    It belongs here on the same reasoning as the other five — it is a decided answer about the
    issue, read from the issue, and not a failure to ask.
    """
    source = (ROOT / ".agents" / "bin" / "claim.py").read_text(encoding="utf-8")
    eligible = source.partition("def _check_eligible")[2].partition("\ndef ")[0]
    fetch = source.partition("def _issue")[2].partition("\ndef ")[0]
    assert source.count("raise IneligibleError") == 6, "the verdicts are exactly six"
    assert eligible.count("raise IneligibleError") == 5
    assert fetch.count("raise IneligibleError") == 1
    assert "could not be read" in fetch, "a failed read sits beside it and must NOT be a verdict"
    assert fetch.count("raise ClaimError") == 1


def test_a_certificate_failure_with_the_opt_out_already_set_does_not_suggest_it_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The remedy branch must not fire when the remedy is already applied.

    If the opt-out is on and the certificate *still* fails, the strict-conformance story is no
    longer the explanation - the chain itself is untrusted. Telling the reader to set a variable
    they have already set would send them in a circle, and worse, imply another notch of loosening
    exists. There is not one.
    """
    monkeypatch.setenv(claim.STRICT_OPT_OUT, "1")
    monkeypatch.setattr(claim, "_ANNOUNCED", True)
    _transport(monkeypatch, _cert_error("some OpenSSL wording nobody here has seen"))
    with pytest.raises(claim.TransportError) as info:
        claim._request("GET", "/repos/bioedca/tether/issues/7")
    message = str(info.value)
    assert "already set" in message
    assert f"{claim.STRICT_OPT_OUT}=1" not in message, "do not re-suggest what is already applied"
    assert "Do not relax verification further" in message


@pytest.mark.parametrize(
    ("cause", "expected"),
    [
        ("certificate has expired", "invalid however X.509 conformance is configured"),
        ("Hostname mismatch, certificate is not valid for 'x'", "however X.509 conformance"),
        ("unable to get local issuer certificate", "SSL_CERT_FILE"),
    ],
)
def test_the_opt_out_being_set_does_not_relabel_an_unrelated_failure(
    monkeypatch: pytest.MonkeyPatch, cause: str, expected: str
) -> None:
    """Codex P1 on #388: the opt-out branch used to short-circuit ahead of classification.

    It announced "the chain itself is not trusted" for whatever came through, which is wrong for an
    expired certificate or a hostname mismatch — neither is a chain-trust failure, and both survive
    the relaxation precisely because it leaves chain and hostname verification on. What was observed
    is classified first now; the opt-out's state qualifies only the branches about conformance.
    """
    monkeypatch.setenv(claim.STRICT_OPT_OUT, "1")
    monkeypatch.setattr(claim, "_ANNOUNCED", True)
    _transport(monkeypatch, _cert_error(cause))
    with pytest.raises(claim.TransportError) as info:
        claim._request("GET", "/repos/bioedca/tether/issues/7")
    message = str(info.value)
    assert cause in message
    assert expected in message
    assert "chain itself is not trusted" not in message, "that is not what happened"


def test_a_missing_issuer_gets_the_remedy_that_actually_applies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex P2 on #388: the strict story was asserted for every certificate failure.

    For a genuinely missing issuer, `SSL_CERT_FILE` *is* the remedy — the first version told the
    reader the opposite, confidently. That is the failure mode #315 exists to remove, reintroduced
    one branch over.
    """
    monkeypatch.delenv(claim.STRICT_OPT_OUT, raising=False)
    _transport(monkeypatch, _cert_error("unable to get local issuer certificate"))
    with pytest.raises(claim.TransportError) as info:
        claim._request("GET", "/repos/bioedca/tether/issues/7")
    message = str(info.value)
    assert "SSL_CERT_FILE" in message
    assert "point SSL_CERT_FILE at that CA bundle" in message
    assert f"{claim.STRICT_OPT_OUT} cannot address it" in message
    assert "was found" not in message, "it was not found; that is the whole point"


def test_an_unrelated_certificate_defect_is_not_blamed_on_conformance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An expired certificate is not a Basic Constraints quibble, and must not read as one."""
    monkeypatch.delenv(claim.STRICT_OPT_OUT, raising=False)
    _transport(monkeypatch, _cert_error("certificate has expired"))
    with pytest.raises(claim.TransportError) as info:
        claim._request("GET", "/repos/bioedca/tether/issues/7")
    message = str(info.value)
    assert "certificate has expired" in message
    assert "Basic Constraints" not in message
    assert "the certificate was found" not in message
    # On *either* interpreter, and keyed on the arming form: naming the variable in order to say it
    # cannot help is a refusal, not advice. Codex's P1 at `0f14fa1` was that the 3.13+ fallback
    # offered the opt-out to anything without a conformance signature, so the gate the ADR promised
    # was not the gate the code applied.
    assert f"{claim.STRICT_OPT_OUT}=1" not in message, "no remedy for a certificate that is expired"
    # Interpreter-independent, unlike the unknown-signature case: expiry is checked the same way
    # under strict and non-strict verification, so the answer does not depend on which is running.
    assert "should not be accepted" in message
    assert "not a conformance defect" in message


@pytest.mark.parametrize(
    ("cause", "certainty"),
    [
        ("Basic Constraints of CA cert not marked critical", "conformance"),
        ("invalid CA certificate", "conformance"),
        ("Missing Authority Key Identifier", "conformance"),
        ("CA cert does not include key usage extension", "conformance"),
        ("certificate has expired", "not-conformance"),
        ("certificate is not yet valid", "not-conformance"),
        ("Hostname mismatch, certificate is not valid for 'api.github.com'", "not-conformance"),
        ("unable to get local issuer certificate", "missing-issuer"),
        ("self-signed certificate in certificate chain", "missing-issuer"),
        ("some OpenSSL wording nobody here has seen", "unknown"),
    ],
)
def test_the_certificate_message_claims_only_what_it_can_know(
    monkeypatch: pytest.MonkeyPatch, cause: str, certainty: str
) -> None:
    """Three certainty classes, because two of them were review findings pointing opposite ways.

    Codex first showed that *offering* `TETHER_ALLOW_NONSTRICT_X509=1` for anything unrecognized
    pointed expired certificates at a TLS switch that cannot help them. Then it showed that
    *denying* the remedy for anything unrecognized is equally unfounded — `_STRICT_MARKERS` cannot
    be exhaustive, since OpenSSL gates a family of checks behind `X509_V_FLAG_X509_STRICT` and words
    them per build, so a message that misses the list is genuinely **unknown**.

    Both are the same defect: asserting something the tool does not know. So known-conformance gets
    the remedy, known-not-conformance gets a definite refusal, and unknown gets neither — it names
    both possibilities and the experiment that separates them.
    """
    monkeypatch.delenv(claim.STRICT_OPT_OUT, raising=False)
    _transport(monkeypatch, _cert_error(cause))
    with pytest.raises(claim.TransportError) as info:
        claim._request("GET", "/repos/bioedca/tether/issues/7")
    message = str(info.value)
    assert cause in message, "the observed reason travels with every verdict"

    if not claim._strict_is_the_default() and certainty in ("conformance", "unknown"):
        # Below 3.13 the flag is off, so conformance cannot be the cause whatever the wording.
        assert "not enabled on this interpreter" in message
        assert f"{claim.STRICT_OPT_OUT}=1" not in message
        return

    if certainty == "conformance":
        assert f"{claim.STRICT_OPT_OUT}=1" in message, "the remedy applies and must be offered"
        assert "cannot tell" not in message
    elif certainty == "not-conformance":
        assert f"{claim.STRICT_OPT_OUT}=1" not in message, "the remedy cannot help; do not offer it"
        assert "should not be accepted" in message
    elif certainty == "missing-issuer":
        assert "SSL_CERT_FILE" in message
        assert f"{claim.STRICT_OPT_OUT}=1" not in message
    else:
        assert "cannot tell" in message, "an unknown signature must not be asserted either way"
        assert "must not be forced" in message
        # The experiment has to isolate the flag. "Re-run under an older interpreter" was the first
        # suggestion and does not: a different interpreter brings a different OpenSSL build, CA path
        # and - on this machine - a different environment, so success there proves nothing about
        # encoding. Toggling one variable in one process does.
        assert "same interpreter" in message
        assert "older than 3.13" not in message


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

    Whitespace is covered by `test_only_the_exact_string_one_arms_it`, which rejects it. An earlier
    version of this file said whitespace was tolerated; that was true of an earlier implementation
    and stopped being true when the comparison became exact.
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


def test_only_the_exact_string_one_arms_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """ "Literal" means literal — no `strip`, no truthiness.

    An earlier version stripped whitespace, arguing that `"1 "` from a `.env` line is unambiguous
    intent and that refusing it would make a set variable a silent no-op. Both reviewers flagged it,
    and the argument does not survive: the contract says *literal*, and the no-op is not silent —
    a value that does not arm produces the ordinary strict failure, which prints the cause and this
    variable as the remedy. A malformed setting failing loudly is the safer direction to err.
    """
    stock = claim.ssl.create_default_context()
    monkeypatch.setattr(claim, "_ANNOUNCED", False)
    monkeypatch.setenv(claim.STRICT_OPT_OUT, "1")
    # Asserted on the predicate too, not only on the flags: below 3.13 the flag is already clear, so
    # a flags-only check would pass for every value and prove nothing.
    assert claim._nonstrict_x509_allowed()
    assert claim._ssl_context().verify_flags == stock.verify_flags & ~claim.ssl.VERIFY_X509_STRICT

    for value in (" 1", "1 ", " 1 ", "1\n", "01", "1.0"):
        monkeypatch.setenv(claim.STRICT_OPT_OUT, value)
        assert not claim._nonstrict_x509_allowed(), f"{value!r} armed the opt-out"
        assert claim._ssl_context().verify_flags == stock.verify_flags


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


def test_a_failed_announcement_does_not_latch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Codex P2 on #388: latching before the write can relax a context in total silence.

    If stderr is closed or its reader has exited, the print raises and no notice arrived. Latching
    first would let a caller that catches that error and retries get a relaxed context with nothing
    on the record — which is precisely what the interlock exists to prevent.
    """
    monkeypatch.setenv(claim.STRICT_OPT_OUT, "1")
    monkeypatch.setattr(claim, "_ANNOUNCED", False)
    monkeypatch.setattr(claim, "print", _raises(BrokenPipeError("stderr is gone")), raising=False)
    with pytest.raises(BrokenPipeError):
        claim._ssl_context()
    assert claim._ANNOUNCED is False, "a notice that never arrived must not count as delivered"


def test_the_default_says_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The announcement marks the exception, so it must not fire on the ordinary path."""
    monkeypatch.delenv(claim.STRICT_OPT_OUT, raising=False)
    monkeypatch.setattr(claim, "_ANNOUNCED", False)
    claim._ssl_context()
    assert capsys.readouterr().err == ""


@pytest.mark.skipif(sys.version_info < (3, 13), reason="VERIFY_X509_STRICT is off before 3.13")
def test_the_default_really_is_strict_on_the_interpreters_that_have_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half: `untouched` only means `strict` where CPython makes it so.

    3.13 is where `create_default_context()` turned the flag on, and 3.13/3.14 are supported
    interpreters here - so on those, the shipped default must be the strict one.

    The `delenv` is load-bearing rather than tidy: the contract tells operators on the affected
    machines to set `TETHER_ALLOW_NONSTRICT_X509=1`, so in the very shell this fix exists to serve,
    reading the ambient environment would fail this test for an environment reason.
    """
    monkeypatch.delenv(claim.STRICT_OPT_OUT, raising=False)
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
