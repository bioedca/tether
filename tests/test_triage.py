# SPDX-FileCopyrightText: 2026 The Tether Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Contract tests for the review-round triage.

Every GitHub call goes through a fake transport. The state that matters here - a PR that has
already spent both of its review rounds - cannot be produced on demand against a live repository,
and CI must not depend on the network.

The load-bearing test is `test_no_amend_authority_is_issued_once_capped`. That is the whole cap:
the launcher's authority to start an AMEND session is the `agent:needs-amend` label, so withholding
it is what makes a third round impossible rather than merely forbidden.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from typing import Any

import pytest

# Imported, not `importorskip`ed. pyyaml is already a hard test dependency
# (`tests/test_issue_forms.py` imports it at module scope), and skipping on its absence would let
# the workflow's `permissions` and `concurrency` assertions silently disappear - the two checks
# that stop an over-grant or a de-serialised label write from landing.
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".agents" / "bin" / "triage.py"
WORKFLOW = ROOT / ".github" / "workflows" / "agent-triage.yml"

_spec = importlib.util.spec_from_file_location("tether_triage", SCRIPT)
assert _spec is not None and _spec.loader is not None
triage = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(triage)

HEAD = "d" * 40
OLDER = "e" * 40
CODEX = "chatgpt-codex-connector[bot]"
RABBIT = "coderabbitai[bot]"
COPILOT = "copilot-pull-request-reviewer[bot]"
GREPTILE = "greptile-apps[bot]"


class Fake:
    """Answers by (method, path-prefix), longest prefix first, and records every request."""

    DEFAULTS = {"DELETE": (204, None), "POST": (200, None), "PATCH": (200, None)}

    def __init__(self, routes: dict[tuple[str, str], tuple[int, Any]]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str]] = []
        # Adds and removes are tracked separately. Both directions touch `/labels` paths, so
        # without the split a test asserting "withheld" could not tell a missing add from a
        # successful one.
        self.added: list[str] = []
        self.removed: list[str] = []

    def __call__(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        self.calls.append((method, path))
        if method == "POST" and path.endswith("/labels") and isinstance(body, dict):
            self.added.extend(body.get("labels", []))
        if method == "DELETE" and "/labels/" in path:
            self.removed.append(path.split("/labels/", 1)[1])

        best: tuple[int, tuple[int, Any]] | None = None
        for (m, prefix), response in self.routes.items():
            if m == method and path.startswith(prefix) and (best is None or len(prefix) > best[0]):
                best = (len(prefix), response)
        return best[1] if best is not None else self.DEFAULTS.get(method, (200, None))


class Paging(Fake):
    """A `Fake` whose `/graphql` answer depends on the cursor the query asked for (#410).

    The base class answers by `(method, path)` alone, so every request in a cursor-following walk
    gets the same page. That is enough to prove the walk REFUSES a read it cannot finish, and
    structurally unable to prove it ever COMPLETES one - so the paging code was covered only in the
    direction where it declines to run.

    The cursor lives in the request body, which the base class deliberately does not record:
    `self.calls` is unpacked as 2-tuples elsewhere in this file, so widening it would be a change
    to every other test. Recorded here instead, and only for this route.

    `pages` maps the cursor a request carried - `None` on the first, since that is what
    `_resolved_comment_ids` seeds and what GraphQL reads as `after: null` - to the payload it gets
    back. An unlisted cursor raises `KeyError` rather than defaulting, because a walk that asked
    for a page the test did not describe is the failure, not a case to paper over.
    """

    def __init__(self, routes: Routes, pages: dict[str | None, Any]) -> None:
        super().__init__(routes)
        self.pages = pages
        #: The cursor each `/graphql` request carried, in order. `[None]` is a single-page walk.
        self.cursors: list[str | None] = []

    def __call__(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        if path != "/graphql":
            return super().__call__(method, path, body)
        cursor = ((body or {}).get("variables") or {}).get("cursor")
        self.cursors.append(cursor)
        self.calls.append((method, path))
        return 200, self.pages[cursor]


def _install(
    monkeypatch: pytest.MonkeyPatch,
    routes: dict[tuple[str, str], tuple[int, Any]],
    *,
    pages: dict[str | None, Any] | None = None,
) -> Fake:
    fake = Fake(routes) if pages is None else Paging(routes, pages)
    # Patch the module OBJECTS, not dotted strings - these are file-loaded, not importable packages.
    monkeypatch.setattr(triage.claim, "_request", fake)

    def paginate(path: str, what: str) -> list[Any]:
        # Mirror the real _paginate contract: it RAISES on a non-200 or non-list body. Returning []
        # instead would turn every fail-closed test into a "genuinely empty list" test.
        status, payload = fake("GET", path)
        if status != 200 or not isinstance(payload, list):
            raise triage.claim.ClaimError(f"{what} could not be read")
        return payload

    monkeypatch.setattr(triage.claim, "_paginate", paginate)
    return fake


def _pr(**over: Any) -> dict[str, Any]:
    pr: dict[str, Any] = {
        "number": 99,
        "state": "open",
        "head": {"ref": "agent/issue-7", "sha": HEAD},
    }
    pr.update(over)
    return pr


def _review(user: str, sha: str) -> dict[str, Any]:
    """A review that FOUND something, which is what "a round happened" means since #399.

    The state is explicit because a round is now a metered review with blocking output, not merely
    a metered review: a clean one is the lane terminating and costs nothing. Every test written
    before that meant *a round was spent*, so the default carries a verdict rather than leaving the
    payload silent. `_clean_review` is the other half, and the pair is what stops either rule from
    being asserted vacuously.
    """
    return {"user": {"login": user}, "commit_id": sha, "state": "CHANGES_REQUESTED"}


def _suites(*entries: dict[str, Any], total: int | None = None) -> dict[str, Any]:
    return {
        "total_count": total if total is not None else len(entries),
        "check_suites": list(entries),
    }


GREEN = _suites({"status": "completed", "conclusion": "success"})
RED = _suites({"status": "completed", "conclusion": "failure"})
RUNNING = _suites({"status": "in_progress", "conclusion": None})

Routes = dict[tuple[str, str], tuple[int, Any]]


def _routes(
    *,
    pr: dict[str, Any] | None = None,
    labels: list[str] | None = None,
    reviews: list[dict[str, Any]] | None = None,
    comments: list[dict[str, Any]] | None = None,
    suites: dict[str, Any] | None = None,
    issue_state: str = "open",
    timeline: list[dict[str, Any]] | None = None,
    threads: list[dict[str, Any]] | None = None,
) -> Routes:
    return {
        ("GET", "/repos/bioedca/tether/pulls/99/reviews"): (200, reviews or []),
        ("GET", "/repos/bioedca/tether/pulls/99/comments"): (200, comments or []),
        ("GET", "/repos/bioedca/tether/pulls/99"): (200, pr if pr is not None else _pr()),
        ("GET", "/repos/bioedca/tether/issues/7"): (
            200,
            {"state": issue_state, "labels": [{"name": n} for n in (labels or [])]},
        ),
        ("GET", "/repos/bioedca/tether/commits"): (200, suites if suites is not None else GREEN),
        ("GET", "/repos/bioedca/tether/issues/99/timeline"): (200, timeline or []),
        ("POST", "/graphql"): (200, _threads(threads or [])),
    }


#: The contract's deferral reply, spelled as `docs/agents/review.md` and `.agents/tasks/amend.md`
#: prescribe it. #409 is the real follow-up this stack filed, so the fixture is not inventing one.
DEFERRAL = "Deferred: non-blocking, severity is unread here. Tracked in #409."

#: **GraphQL omits the `[bot]` suffix REST carries.** `author.login` on a Bot is `coderabbitai`,
#: while `user.login` on the same account over REST is `coderabbitai[bot]` - measured on this repo.
#: The fixture uses the GraphQL spelling because that is what the parser under test receives, and
#: writing the REST one here would let a provider-authored deferral pass unnoticed.
RABBIT_GQL = "coderabbitai"


def _thread(
    *comment_ids: int,
    resolved: bool = False,
    truncated: bool = False,
    answer: str | None = DEFERRAL,
    answered_by: str = "bioedca",
) -> dict[str, Any]:
    """One review thread. Resolution is a property of the THREAD, so it covers all its comments.

    Shaped the way GitHub returns one: the first comment is the provider's finding, anything after
    it is a reply. `answer` is what those replies say - `None` for a thread somebody closed with no
    answer at all - and `answered_by` is who wrote them, so a *provider* writing the deferral (which
    must not count) is expressible.
    """
    nodes: list[dict[str, Any]] = []
    for position, comment_id in enumerate(comment_ids):
        finding = position == 0
        nodes.append(
            {
                # `fullDatabaseId` is a GraphQL BigInt and arrives as a STRING, which is the shape
                # the parser must normalise - `databaseId` is deprecated for dropping 64-bit ids.
                "fullDatabaseId": str(comment_id),
                "body": "A finding." if finding else (answer or "Acknowledged."),
                "author": {"login": RABBIT_GQL if finding else answered_by},
            }
        )
    return {
        "isResolved": resolved,
        "comments": {"pageInfo": {"hasNextPage": truncated}, "nodes": nodes},
    }


def _threads(nodes: list[dict[str, Any]], *, cursor: str | None = None) -> dict[str, Any]:
    return {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": cursor is not None, "endCursor": cursor},
                        "nodes": nodes,
                    }
                }
            }
        }
    }


def _run(
    routes: Routes,
    monkeypatch: pytest.MonkeyPatch,
    *,
    pages: dict[str | None, Any] | None = None,
) -> tuple[Fake, dict[str, Any]]:
    fake = _install(monkeypatch, routes, pages=pages)
    return fake, triage.triage(number=99, branch=None, dry_run=False)


# --------------------------------------------------------------------------- the cap


def test_no_amend_authority_is_issued_once_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    """THE cap. Authority stops once the convergence check has failed too, and not before.

    `agent:needs-amend` is the launcher's authority to start a session, so withholding it makes a
    further round impossible rather than forbidden. #276 reached 9 rounds against a limit of 2
    because a prose rule was the only thing holding it.

    THREE blocking rounds here, not two, and that is the correction rather than a stronger case:
    withholding at exactly `CAP` also withheld the session that answers round 2, so the convergence
    check could never happen (CodeRabbit on #408). Past the cap that check has itself come back
    blocking, so there is genuinely nothing left to authorise — CI red as well, which is the state
    that would otherwise owe an AMEND on its own.
    """
    fake, result = _run(
        _routes(
            reviews=[_review(RABBIT, OLDER), _review(RABBIT, ROUND_2), _review(RABBIT, HEAD)],
            suites=RED,
        ),
        monkeypatch,
    )
    assert result["rounds"] == 3
    assert result["capped"] is True
    assert result["gate"] == "blocked"
    assert result["amend"] == "gate-blocked"
    assert triage.AMEND_LABEL not in fake.added
    assert triage.CAPPED_LABEL in fake.added


def test_two_providers_at_one_head_are_one_round(monkeypatch: pytest.MonkeyPatch) -> None:
    """A `high` PR routes to BOTH providers answering as one round (AGENTS.md §Review gate).

    Counting review submissions instead of heads would cap every high-risk PR after its first
    pass, which would make the routing rule and the cap contradict each other.
    """
    fake, result = _run(
        _routes(reviews=[_review(CODEX, HEAD), _review(RABBIT, HEAD)], suites=RED),
        monkeypatch,
    )
    assert result["rounds"] == 1
    assert result["capped"] is False
    assert "agent:round-1" in fake.added
    assert triage.AMEND_LABEL in fake.added


def test_inline_review_comments_count_even_without_a_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider can post findings with no submission wrapper; missing those undercounts."""
    _, result = _run(
        _routes(comments=[_review(RABBIT, OLDER)], reviews=[_review(RABBIT, HEAD)], suites=GREEN),
        monkeypatch,
    )
    assert result["rounds"] == 2
    assert result["capped"] is True


def test_copilot_never_consumes_a_round(monkeypatch: pytest.MonkeyPatch) -> None:
    """AGENTS.md: Copilot is optional and its absence or quota never blocks.

    So a Copilot pass must not spend a round the contract did not grant - which, at two Copilot
    reviews, would cap a PR that no selected provider had even looked at.
    """
    _, result = _run(
        _routes(reviews=[_review(COPILOT, OLDER), _review(COPILOT, HEAD)], suites=GREEN),
        monkeypatch,
    )
    assert result["rounds"] == 0
    assert result["capped"] is False


def test_the_author_never_consumes_a_round(monkeypatch: pytest.MonkeyPatch) -> None:
    """#276 carried 44 bioedca review submissions. Self-review is not evidence and not a round."""
    _, result = _run(
        _routes(reviews=[_review("bioedca", OLDER), _review("bioedca", HEAD)], suites=GREEN),
        monkeypatch,
    )
    assert result["rounds"] == 0


def test_round_labels_only_escalate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Monotonic on purpose: stepping back from capped would re-authorise a spent round.

    The round is at `OLDER`, not `HEAD`: it was spent, answered, and the fix pushed. Putting a
    blocking review at the current head would also owe an AMEND, which is a different axis and
    would make the label assertions below about something other than monotonicity.
    """
    fake, result = _run(
        _routes(labels=[triage.CAPPED_LABEL], reviews=[_review(RABBIT, OLDER)], suites=GREEN),
        monkeypatch,
    )
    assert result["rounds"] == 1
    # round-1 is BELOW the capped label already held, so nothing is written and nothing removed.
    # Filtered to the ROUND labels: this test is about their monotonicity, and #394 gave the same
    # run a second label to publish, so a blanket "added nothing" would be asserting two things.
    assert [n for n in fake.added if n in triage.ALL_ROUND_LABELS] == []
    assert triage.CAPPED_LABEL not in fake.removed


def test_reaching_the_cap_replaces_the_earlier_round_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round labels are mutually exclusive; a PR must not read as round-1 and capped at once."""
    fake, _ = _run(
        _routes(
            labels=["agent:round-1"],
            reviews=[_review(RABBIT, OLDER), _review(RABBIT, HEAD)],
            suites=GREEN,
        ),
        monkeypatch,
    )
    assert triage.CAPPED_LABEL in fake.added
    assert "agent:round-1" in fake.removed


def test_a_failed_amend_label_removal_is_reported_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CodeRabbit's finding on #385, and it falsified a claim this PR had just introduced.

    `_apply`'s docstring said "every removal this function makes is paired with an add". Clearing
    `agent:needs-amend` is not — it is the whole change, and it *retracts authority*. Swallowed, the
    PR owes nothing but still advertises that it owes an AMEND, and the launcher keeps starting
    sessions against a permanent cap. Superseded round labels stay best-effort, since an add already
    published the truth beside them.
    """
    routes = _routes(labels=[triage.AMEND_LABEL], reviews=[], suites=GREEN, timeline=[])
    routes[("DELETE", f"/repos/bioedca/tether/issues/7/labels/{triage.AMEND_LABEL}")] = (500, None)
    fake = _install(monkeypatch, routes)
    # Matched on the LABEL, not on the word "AMEND": `CHECKED_REMOVALS` holds two labels now, so the
    # message is label-neutral and naming one of them in the assertion would pin prose that has to
    # describe both (CodeRabbit on #407).
    with pytest.raises(triage.TriageError, match=triage.AMEND_LABEL):
        triage.triage(number=99, branch=None, dry_run=False)
    assert triage.AMEND_LABEL in fake.removed, "the delete was attempted before it was reported"


def test_an_amend_label_already_absent_is_not_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """404 is the end state asked for, reached by another route — not an error to escalate."""
    routes = _routes(labels=[triage.AMEND_LABEL], reviews=[], suites=GREEN, timeline=[])
    routes[("DELETE", f"/repos/bioedca/tether/issues/7/labels/{triage.AMEND_LABEL}")] = (404, None)
    _install(monkeypatch, routes)
    assert triage.triage(number=99, branch=None, dry_run=False)["amend"] == "cleared"


def test_a_zero_recount_never_clears_a_round_label(monkeypatch: pytest.MonkeyPatch) -> None:
    """No automatic migration for labels written under the pre-ADR-0062 semantics, on purpose.

    Three versions of one were tried on #385 and each was worse than the last, because a recount of
    zero is ambiguous in a way no predicate here can resolve. It means *never spent* for a stale
    label — and *the evidence was deleted* for a real round a metered provider left as wrapper-less
    inline comments, which `_review_state` supports. Clearing on zero refunds the second case, which
    is fail-OPEN on the cap: the one thing this module exists to hold.

    So a stale label is removed by hand — a command someone runs, where a wrong automatic clear is
    silent. Verified empty before ADR-0062 merged.
    """
    fake, result = _run(
        _routes(labels=[triage.CAPPED_LABEL], reviews=[], suites=GREEN, timeline=[]),
        monkeypatch,
    )
    assert result["rounds"] == 0
    assert triage.CAPPED_LABEL not in fake.removed
    round_labels = [n for n in fake.added if n in triage.ALL_ROUND_LABELS]
    assert round_labels == [], "a zero recount writes no round label either"


def test_a_draft_excursion_after_ready_still_keeps_its_spent_rounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A PR that HAS been ready and returned to draft keeps the round it spent.

    Its `counted_from` is the *first* ready instant, so evidence from before the excursion still
    counts and the label stands. This is the toggle-to-refund loophole staying closed.
    """
    fake, result = _run(
        _routes(
            pr=_pr(draft=True),
            labels=["agent:round-1"],
            reviews=[dict(_review(RABBIT, HEAD), submitted_at="2026-08-02T12:00:00Z")],
            suites=GREEN,
            timeline=[
                {"event": "ready_for_review", "created_at": "2026-08-02T00:00:00Z"},
                {"event": "convert_to_draft", "created_at": "2026-08-02T18:00:00Z"},
            ],
        ),
        monkeypatch,
    )
    assert result["rounds"] == 1
    assert "agent:round-1" not in fake.removed


# --------------------------------------------------------------------- the amend label


def test_a_red_head_owes_one_amend_and_only_one(monkeypatch: pytest.MonkeyPatch) -> None:
    fake, result = _run(_routes(labels=[triage.AMEND_LABEL], suites=RED), monkeypatch)
    assert result["amend"] == "unchanged"
    assert fake.added == [], "the label is already present; re-POSTing it is noise"


def test_a_green_head_clears_the_amend_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    fake, result = _run(_routes(labels=[triage.AMEND_LABEL], suites=GREEN), monkeypatch)
    assert result["amend"] == "cleared"
    assert triage.AMEND_LABEL in fake.removed


def test_a_running_suite_neither_owes_nor_clears(monkeypatch: pytest.MonkeyPatch) -> None:
    """CI mid-flight is not a verdict. Clearing here would drop a real marker on a transient."""
    fake, result = _run(_routes(labels=[triage.AMEND_LABEL], suites=RUNNING), monkeypatch)
    assert result["amend"] == "unchanged"
    assert fake.removed == []


@pytest.mark.parametrize("conclusion", ["cancelled", "stale"], ids=["cancelled", "stale"])
def test_a_non_green_completed_suite_never_clears_the_marker(
    monkeypatch: pytest.MonkeyPatch, conclusion: str
) -> None:
    """`cancelled` is readable, completed, and NOT a pass.

    Treating it as green cleared a real `agent:needs-amend` - the fail-open direction on the one
    label that carries AMEND authority. `skipped` and `neutral` are genuinely non-failing and are
    deliberately not in this list.
    """
    suites = _suites({"status": "completed", "conclusion": conclusion})
    fake, result = _run(_routes(labels=[triage.AMEND_LABEL], suites=suites), monkeypatch)
    assert result["checks"] == "failed"
    assert result["amend"] == "unchanged"
    assert fake.removed == []


@pytest.mark.parametrize("conclusion", ["skipped", "neutral"], ids=["skipped", "neutral"])
def test_a_genuinely_non_failing_conclusion_still_clears(
    monkeypatch: pytest.MonkeyPatch, conclusion: str
) -> None:
    """The other direction: over-broadening the failure set would strand every PR as owing."""
    suites = _suites({"status": "completed", "conclusion": conclusion})
    _, result = _run(_routes(labels=[triage.AMEND_LABEL], suites=suites), monkeypatch)
    assert result["checks"] == "green"
    assert result["amend"] == "cleared"


# ------------------------------------------------------------- an unanswered review


def test_a_blocking_review_at_a_green_head_still_owes_an_amend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The hole this workflow existed to fill, and originally did not.

    Keying AMEND authority on a failed check suite alone meant a provider requesting changes at a
    GREEN head produced no authority at all, so the launcher never started the session that answers
    an ordinary blocking review.
    """
    fake, result = _run(
        _routes(comments=[_review(CODEX, HEAD)], suites=GREEN),
        monkeypatch,
    )
    assert result["checks"] == "green"
    assert result["review_owed"] is True
    assert result["amend"] == "added"
    assert triage.AMEND_LABEL in fake.added


def test_changes_requested_at_the_head_owes_an_amend(monkeypatch: pytest.MonkeyPatch) -> None:
    """A submission with no inline comments still asks for changes explicitly."""
    review = {**_review(RABBIT, HEAD), "state": "CHANGES_REQUESTED"}
    _, result = _run(_routes(reviews=[review], suites=GREEN), monkeypatch)
    assert result["review_owed"] is True
    assert result["amend"] == "added"


def test_a_clean_pass_at_the_head_owes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A provider that looked and had nothing to say must not owe an AMEND.

    Otherwise every PR would owe one forever the moment it was reviewed, and the launcher would
    respawn a worker to answer a review with no findings.
    """
    review = {**_review(CODEX, HEAD), "state": "APPROVED"}
    fake, result = _run(_routes(labels=[triage.AMEND_LABEL], reviews=[review]), monkeypatch)
    assert result["review_owed"] is False
    assert result["amend"] == "cleared"
    assert triage.AMEND_LABEL in fake.removed


def test_a_review_on_an_older_head_does_not_owe_at_the_current_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pushing the fix moves the head, which is exactly how an answered round stops owing."""
    _, result = _run(_routes(comments=[_review(RABBIT, OLDER)], suites=GREEN), monkeypatch)
    assert result["rounds"] == 1
    assert result["review_owed"] is False
    assert result["amend"] == "unchanged"


def test_a_capped_pr_keeps_a_marker_the_reaper_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two writers share this label and must not fight over it.

    The reaper applies `agent:needs-amend` to a STALE PR for reasons unrelated to rounds. Removing
    it here - the tempting extra belt - would erase the only signal that a claim needs a person.
    The launcher refuses on `agent:review-capped` instead.
    """
    fake, result = _run(
        _routes(
            labels=[triage.AMEND_LABEL],
            reviews=[_review(RABBIT, OLDER), _review(RABBIT, ROUND_2), _review(RABBIT, HEAD)],
            suites=GREEN,
        ),
        monkeypatch,
    )
    assert result["amend"] == "gate-blocked"
    assert triage.AMEND_LABEL not in fake.removed


# ---------------------------------------------------------------------------- scoping


def test_a_non_claim_branch_is_left_completely_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """Maintainer branches keep `type/issue-N-slug` names and must not enter an agent machine."""
    fake, result = _run(
        _routes(pr=_pr(head={"ref": "docs/issue-281-review-cap", "sha": HEAD})), monkeypatch
    )
    assert result == {"action": "skip", "reason": "not-agent-claimed", "pr": 99}
    assert fake.added == [] and fake.removed == []


@pytest.mark.parametrize(
    "ref",
    ["agent/issue-", "agent/issue-7-slug", "agent/issue-x", "agent/issue-7/extra"],
    ids=["no-number", "slugged", "non-numeric", "nested"],
)
def test_only_an_exact_claim_ref_is_recognised(monkeypatch: pytest.MonkeyPatch, ref: str) -> None:
    """The claim ref has NO slug by design (ADR-0057).

    A slug is not deterministic across agents, so two refs could exist for one issue. A near-miss
    must therefore not be treated as that issue's claim.
    """
    _, result = _run(_routes(pr=_pr(head={"ref": ref, "sha": HEAD})), monkeypatch)
    assert result["reason"] == "not-agent-claimed"


def test_a_closed_issue_is_not_labelled(monkeypatch: pytest.MonkeyPatch) -> None:
    fake, result = _run(_routes(issue_state="closed"), monkeypatch)
    assert result["reason"] == "issue-not-open"
    assert fake.added == []


def test_a_closed_pull_request_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    fake, result = _run(_routes(pr=_pr(state="closed")), monkeypatch)
    assert result["reason"] == "pull-request-not-open"
    assert fake.added == []


def test_dry_run_writes_nothing_but_still_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _install(monkeypatch, _routes(suites=RED))
    result = triage.triage(number=99, branch=None, dry_run=True)
    assert result["amend"] == "added"
    assert fake.added == [] and fake.removed == []


# ------------------------------------------------------------------------ fail closed


@pytest.mark.parametrize("status", [403, 429, 500, 502], ids=["forbidden", "rate", "500", "502"])
def test_an_unreadable_check_state_never_publishes_a_round(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    """Not-knowing and knowing-it-is-green must never be the same value.

    A permissions omission returns 403 under an explicit `permissions:` block, and reading that as
    green would clear a real `agent:needs-amend`.
    """
    routes = _routes(labels=[triage.AMEND_LABEL])
    routes[("GET", "/repos/bioedca/tether/commits")] = (status, None)
    fake = _install(monkeypatch, routes)
    with pytest.raises(triage.TriageError, match="check-suite state"):
        triage.triage(number=99, branch=None, dry_run=False)
    assert fake.added == [] and fake.removed == []


def test_a_truncated_check_suite_list_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """This endpoint wraps its list in an object, so _paginate does not apply.

    A silently short read would report a failing head as clean.
    """
    routes = _routes(suites=_suites({"status": "completed", "conclusion": "success"}, total=9))
    _install(monkeypatch, routes)
    with pytest.raises(triage.TriageError, match="truncated"):
        triage.triage(number=99, branch=None, dry_run=False)


def test_an_unreadable_review_list_never_publishes_a_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty review list and an unreadable one differ by exactly one spent round."""
    routes = _routes()
    routes[("GET", "/repos/bioedca/tether/pulls/99/reviews")] = (502, None)
    fake = _install(monkeypatch, routes)
    with pytest.raises(triage.TriageError, match="review state"):
        triage.triage(number=99, branch=None, dry_run=False)
    assert fake.added == []


def test_a_failed_capped_label_write_is_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reporting `capped` while the write failed leaves a third round looking authorised."""
    routes = _routes(reviews=[_review(CODEX, OLDER), _review(RABBIT, HEAD)], suites=GREEN)
    routes[("POST", "/repos/bioedca/tether/issues/7/labels")] = (403, None)
    _install(monkeypatch, routes)
    with pytest.raises(triage.TriageError, match="never published"):
        triage.triage(number=99, branch=None, dry_run=False)


def test_a_failed_cap_write_leaves_the_previous_round_label_in_place(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Add BEFORE remove, so a failed cap write cannot leave the PR with no round label at all.

    Removing first opened a window with neither `agent:round-1` nor `agent:review-capped` present,
    in which the launcher reads an uncapped PR still carrying AMEND authority and issues a third
    round - while this run exits non-zero and looks like it changed nothing.
    """
    routes = _routes(
        labels=["agent:round-1", triage.AMEND_LABEL],
        reviews=[_review(RABBIT, OLDER), _review(RABBIT, HEAD)],
        suites=GREEN,
    )
    routes[("POST", "/repos/bioedca/tether/issues/7/labels")] = (403, None)
    fake = _install(monkeypatch, routes)
    with pytest.raises(triage.TriageError, match="never published"):
        triage.triage(number=99, branch=None, dry_run=False)
    assert fake.removed == [], "nothing may be deleted until the replacement is published"
    assert "agent:round-1" not in fake.removed


def test_an_absent_pull_request_is_a_skip_not_a_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    routes = _routes()
    routes[("GET", "/repos/bioedca/tether/pulls/99")] = (404, None)
    _install(monkeypatch, routes)
    result = triage.triage(number=99, branch=None, dry_run=False)
    assert result["reason"] == "no-open-pull-request"


def test_the_branch_lookup_path_resolves_a_pull_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """check_suite events carry a head branch, not a PR number - that path must work too."""
    routes = _routes(suites=RED)
    routes[("GET", "/repos/bioedca/tether/pulls?head=bioedca:agent/issue-7")] = (
        200,
        [{"number": 99}],
    )
    fake = _install(monkeypatch, routes)
    result = triage.triage(number=None, branch="agent/issue-7", dry_run=False)
    assert result["pr"] == 99
    assert triage.AMEND_LABEL in fake.added


# --------------------------------------------------------------------------- workflow


def _workflow() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_the_workflow_permissions_are_exactly_what_triage_needs() -> None:
    """Assert the whole mapping, parsed, not a substring of a line slice.

    `pull-requests: write` creeping back would be standing authority for a mutation this workflow
    cannot perform - the class of over-grant that #277 removed from the reaper.
    """
    assert _workflow()["permissions"] == {
        "contents": "read",
        "issues": "write",
        "pull-requests": "read",
        "checks": "read",
    }


def test_the_workflow_never_cancels_a_run_in_progress() -> None:
    """A cancelled run leaves labels describing an older head, which reads as authority."""
    assert _workflow()["concurrency"]["cancel-in-progress"] is False


def test_every_real_event_shares_one_concurrency_key_for_a_pull_request() -> None:
    """Review events and check_suite events must serialise against EACH OTHER, not just themselves.

    Keying reviews on the PR number and check suites on the branch gave one PR two groups, so a
    delayed check run could snapshot pre-cap state and apply its stale delta after the review run
    published the cap. The head branch is the normaliser rather than the number because it is
    present on both payloads - `check_suite.pull_requests` can be empty - and branch and PR are 1:1
    here, the branch being the claim ref itself.
    """
    group = _workflow()["concurrency"]["group"]
    assert "github.event.pull_request.head.ref" in group
    assert "github.event.check_suite.head_branch" in group
    # The number must NOT key a real event: that is precisely the split that de-serialised them.
    assert "github.event.pull_request.number" not in group


def test_the_workflow_checks_out_the_default_branch_not_the_event_ref() -> None:
    """`pull_request_review` is a pull-request-family event, so GITHUB_REF is the PR's MERGE ref.

    The default checkout would run `triage.py` and the `claim.py` it imports from the unmerged
    branch under review, so any PR editing either file would execute unreviewed Python with
    `GH_TOKEN` and `issues: write` - on the workflow whose whole job is publishing review state.
    """
    steps = _workflow()["jobs"]["triage"]["steps"]
    checkout = next(s for s in steps if str(s.get("uses", "")).startswith("actions/checkout@"))
    assert checkout["with"]["ref"] == "${{ github.event.repository.default_branch }}"
    assert checkout["with"]["persist-credentials"] is False


def test_the_workflow_listens_for_the_three_state_changing_events() -> None:
    # `on` is parsed by PyYAML's 1.1 rules as the boolean True, not the string "on".
    triggers = _workflow()[True]
    assert triggers["check_suite"]["types"] == ["completed"]
    assert set(triggers) >= {"check_suite", "pull_request_review", "pull_request_review_comment"}


def test_the_workflow_records_the_bot_trigger_probe_answer() -> None:
    """The probe decides how strong this control is, so its answer is written down, not implied.

    Run on #299 on 2026-07-30. CodeRabbit starts a round from a `github-actions[bot]` comment and
    names the author when it does; Codex both refused the mention and, two minutes later, asserted
    the opposite, so its leg is read conservatively as no.

    What this test protects is the *conclusion*, not the prose: the trigger stays out of the
    workflow body for **both** providers. The first draft of that note justified it with a
    round-counting argument that is false - `_review_state` groups by head SHA, so two providers at
    one head are one round by design - so this pins the behaviour rather than any one sentence of
    the reasoning.
    """
    header = WORKFLOW.read_text(encoding="utf-8")
    assert "BOT-TRIGGER PROBE" in header
    assert "ANSWERED" in header and "NOT ANSWERED" not in header
    assert "@codex review" in header


# A route that CREATES a pull-request or issue comment. This is the check that binds, and it is
# deliberately not a search for the mention text: a workflow cannot post a trigger it cannot post a
# comment with, however the mention is spelled. Fragmented string construction defeats a text search
# and defeats nothing here.
_COMMENT_ROUTES = (
    re.compile(r"/(?:issues|pulls)/[^/\s\"']+/comments"),  # REST, via gh api or curl
    re.compile(r"\bgh\s+(?:pr|issue)\s+comment\b"),
    re.compile(r"\bcreateComment\b"),  # actions/github-script
    re.compile(r"\bpeter-evans/create-or-update-comment\b"),
)
_PROVIDER_MENTION = re.compile(r"@(?:codex|coderabbitai)\b")


def _posting_routes(text: str) -> list[str]:
    """Which comment-creating routes appear in this text, if any."""
    return [rx.pattern for rx in _COMMENT_ROUTES if rx.search(text)]


def _workflow_sources() -> dict[str, str]:
    """Every workflow and composite action, as text, keyed by path.

    Repository scope rather than this one file, per CodeRabbit's finding on #299: asserting that two
    literals are absent from `agent-triage.yml` is satisfied by moving the same capability into any
    other workflow, a composite action, or a reusable workflow.
    """
    root = WORKFLOW.parents[1]
    paths = [
        *sorted((root / "workflows").glob("*.yml")),
        *sorted(root.glob("actions/*/action.yml")),
    ]
    return {str(p.relative_to(root.parent)): p.read_text(encoding="utf-8") for p in paths}


def test_no_workflow_can_post_a_review_trigger() -> None:
    """The probe's conclusion, enforced where it binds rather than where it was written.

    The earlier version rejected two literal strings after the first `permissions:` token in one
    file. CodeRabbit's round-1 finding on #299: that stays green if a workflow builds the mention
    from fragments, decodes it, delegates to a checked-in script, or calls a reusable workflow - so
    the assertion was weaker than the conclusion it claimed to protect.

    Two nets, and the first is the one that holds. **A workflow cannot post a trigger it cannot post
    a comment with**, so the comment-creating ROUTES are what is banned - and a route survives
    fragmentation, because `printf`-ing the mention in pieces still has to reach
    `/issues/{n}/comments` to deliver it. The mention text is banned too, as a cheap second net that
    catches the naive case with a clearer failure message.

    What this still cannot see is stated rather than implied: a workflow that runs a checked-in
    *script* that posts a comment, where the route lives in the script and not in the YAML. That is
    why `.agents/bin/*.py` carry their own `permissions`-justified contracts, and why nothing here
    grants `pull-requests: write` to a step that runs one.
    """
    offenders = {name: _posting_routes(text) for name, text in _workflow_sources().items()}
    posting = {name: hits for name, hits in offenders.items() if hits}
    assert not posting, (
        "these workflows can create a PR/issue comment, which is the capability the #299 probe "
        f"concluded must not exist on the default branch: {posting}"
    )

    mentions = {
        name: _PROVIDER_MENTION.findall(text)
        for name, text in _workflow_sources().items()
        # The recorded ANSWER quotes the mention; only non-comment lines are candidates for a real
        # trigger, so comment lines are excluded rather than the file being exempted wholesale.
        if any(
            _PROVIDER_MENTION.search(ln)
            for ln in text.splitlines()
            if not ln.strip().startswith("#")
        )
    }
    assert not mentions, f"a provider mention appears outside a comment: {mentions}"


def test_the_bootstrap_guard_distinguishes_missing_from_broken() -> None:
    """A missing `triage.py` is either "not merged yet" or "someone broke it" - never both.

    Waving both through would make a real removal silently green on the workflow that publishes
    review state; failing both would make this PR permanently red. The guard keys on whether the
    workflow itself is present on the default branch, so the tolerant branch stops being reachable
    the moment this lands.
    """
    body = WORKFLOW.read_text(encoding="utf-8")
    assert "BOOTSTRAP GUARD" in body
    assert "::error::agent-triage.yml is on the default branch but triage.py is missing" in body
    assert "not on the default branch yet" in body
    # An explicit zero exit is reported as a FAILURE in a `bash -el {0}` step (#138), so there must
    # be no `exit 0` STATEMENT. Comments are stripped first: the header explains the rule and would
    # otherwise match the very text it warns against.
    statements = [
        line.split("#", 1)[0].strip()
        for line in body.splitlines()
        if not line.strip().startswith("#")
    ]
    assert not [s for s in statements if s == "exit 0" or s.endswith(" exit 0")]


def test_the_module_records_that_the_counter_can_undercount() -> None:
    """The fail-open direction must be stated, not implied.

    It is why this is not a required check and why the launcher's own refusal is not optional.
    """
    assert "undercount" in SCRIPT.read_text(encoding="utf-8")


def test_the_cli_reports_json_and_a_named_exit_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install(monkeypatch, _routes(suites=RED))
    monkeypatch.setattr("sys.argv", ["triage.py", "--pr", "99", "--dry-run"])
    assert triage.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["version"] == 1
    assert payload["dry_run"] is True
    assert payload["issue"] == 7


def test_the_cli_refuses_both_sources_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """--pr and --branch are mutually exclusive: two sources could name two different PRs."""
    monkeypatch.setattr("sys.argv", ["triage.py", "--pr", "99", "--branch", "agent/issue-7"])
    with pytest.raises(SystemExit) as exc:
        triage.main()
    assert exc.value.code == 2


@pytest.mark.parametrize(
    ("sample", "caught"),
    [
        ('gh api "repos/${R}/issues/${N}/comments" --input /tmp/x.json', True),
        ("gh pr comment 299 --body 'hello'", True),
        ("gh issue comment 299 --body-file x.md", True),
        ("github.rest.issues.createComment({issue_number: n})", True),
        ("uses: peter-evans/create-or-update-comment@v4", True),
        # The case the literal search could not see: the mention spelled in fragments. It is caught
        # because it still has to reach a comment route to be delivered anywhere.
        (
            "printf '@cod' > /tmp/c; printf 'ex review' >> /tmp/c;"
            ' gh api "repos/o/r/issues/1/comments" -F body=@/tmp/c',
            True,
        ),
        ("gh api repos/o/r/issues/1/labels -f labels[]=agent:claude", False),
        ("echo 'no comment posted here'", False),
        ("gh pr checks 299 --watch", False),
    ],
    ids=[
        "gh-api-rest",
        "gh-pr-comment",
        "gh-issue-comment",
        "github-script",
        "third-party-action",
        "fragmented-mention-still-needs-a-route",
        "labels-are-not-comments",
        "plain-echo",
        "reading-checks",
    ],
)
def test_the_posting_route_detector_catches_indirection(sample: str, caught: bool) -> None:
    """Negative fixtures, because an absence-assertion is worthless unless presence is proven.

    CodeRabbit's round-1 finding on #299 was that a two-literal search is satisfied by fragmented
    construction or indirection. The answer is to key on the ROUTE rather than the text - so these
    pin that the route detector fires on every realistic way of posting a comment, including one
    where the mention itself is unfindable, and stays silent on the label writes this repository
    legitimately makes.
    """
    assert bool(_posting_routes(sample)) is caught


# --------------------------------------------- #307: GitHub rewrites commit_id


def _carried(user: str, wrote_at: str, now_points_at: str) -> dict[str, Any]:
    """An inline comment GitHub has carried forward onto a later head.

    The exact payload shape observed on #304: `original_commit_id` is the head Codex read,
    `commit_id` is the commit that ANSWERED it, and `updated_at` never changed — so nothing in
    the payload's own metadata reveals the rewrite.
    """
    return {
        "user": {"login": user},
        "original_commit_id": wrote_at,
        "commit_id": now_points_at,
    }


def test_answering_a_round_does_not_itself_count_as_a_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#307, and it capped a real pull request: one Codex review, two heads, `review-capped`.

    Reading `commit_id` meant the fix commit — the answer — carried the provider's own comment onto
    itself and scored as a second round. Every PR was therefore capped the moment it responded once,
    and because `agent:review-capped` withholding an AMEND looks exactly like the cap working, the
    failure was silent.
    """
    fake, result = _run(
        _routes(
            reviews=[_review(RABBIT, OLDER)],
            comments=[_carried(RABBIT, OLDER, HEAD)],
            suites=RED,
        ),
        monkeypatch,
    )
    assert result["rounds"] == 1, "one review at one head is one round, wherever it now points"
    assert result["capped"] is False
    assert triage.CAPPED_LABEL not in fake.added
    assert triage.AMEND_LABEL in fake.added, "the second round it is owed must still be issued"


def test_a_genuine_second_round_still_counts_two(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fix must not become an undercount in the other direction.

    A provider reporting at a NEW head after an answer is a real second round, and the difference
    from the case above is exactly `original_commit_id`: written at the new head rather than
    carried onto it.
    """
    _, result = _run(
        _routes(
            reviews=[_review(RABBIT, OLDER)],
            comments=[_carried(RABBIT, HEAD, HEAD)],
            suites=RED,
        ),
        monkeypatch,
    )
    assert result["rounds"] == 2
    assert result["capped"] is True


def test_a_submitted_review_has_no_original_and_still_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`/pulls/{n}/reviews` entries carry only `commit_id`; the fallback IS the answer for them."""
    assert triage._reviewed_head({"commit_id": HEAD}) == HEAD
    assert triage._reviewed_head({"original_commit_id": OLDER, "commit_id": HEAD}) == OLDER
    assert triage._reviewed_head({"original_commit_id": "", "commit_id": HEAD}) == HEAD
    assert triage._reviewed_head({}) is None

    _, result = _run(_routes(reviews=[_review(RABBIT, HEAD)], suites=RED), monkeypatch)
    assert result["rounds"] == 1


# ------------------------------------------------------- the cap starts at ready-for-review (#384)
#
# The review lane spends the unmetered provider first, on a draft, iterating until nothing blocking
# is left, and only then marks the PR ready and starts spending metered reviews. Counting that draft
# iteration would strand the PR at `agent:review-capped` before it ever reached the mandatory
# CodeRabbit gate — the documented loop consuming the cap it is explicitly exempt from.


def _ready(at: str) -> dict[str, Any]:
    return {"event": "ready_for_review", "created_at": at}


DRAFT_TIME = "2026-08-01T10:00:00Z"
READY_TIME = "2026-08-01T12:00:00Z"
AFTER_READY = "2026-08-01T13:00:00Z"


def test_draft_phase_reviews_do_not_consume_a_round(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two METERED reviews on a draft, at two heads, and the PR is still on round zero.

    Before #384 this counted 2 and capped the PR before it reached the CodeRabbit gate.

    The providers here must be metered, which is CodeRabbit's finding on #385: an earlier version
    used `CODEX` twice, and Codex consumes no round in *any* phase, so the assertion held for the
    wrong reason and the draft exemption itself went untested. Every other draft-phase test had the
    same shape, so the load-bearing behaviour of this whole change had no coverage at all.
    `test_codex_never_consumes_a_round_even_after_ready` covers the Codex axis separately.
    """
    _, result = _run(
        _routes(
            reviews=[
                dict(_review(RABBIT, OLDER), submitted_at=DRAFT_TIME),
                dict(_review(GREPTILE, HEAD), submitted_at=DRAFT_TIME),
            ],
            timeline=[_ready(READY_TIME)],
            suites=RED,
        ),
        monkeypatch,
    )
    assert result["rounds"] == 0
    assert result["capped"] is False


def test_a_review_after_ready_for_review_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    """The exemption is the draft phase, not the provider — the cap must still bind after ready."""
    _, result = _run(
        _routes(
            reviews=[dict(_review(RABBIT, HEAD), submitted_at=AFTER_READY)],
            timeline=[_ready(READY_TIME)],
            suites=RED,
        ),
        monkeypatch,
    )
    assert result["rounds"] == 1


def test_a_pull_request_still_in_draft_has_taken_no_counted_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No `ready_for_review` has happened yet, so nothing can have been counted against the cap.

    The review is a **metered** one on purpose. Codex consumes no round in any phase, so asserting
    zero against it would hold whether or not the draft exemption existed.
    """
    _, result = _run(
        _routes(
            pr=_pr(draft=True),
            reviews=[dict(_review(RABBIT, HEAD), submitted_at=DRAFT_TIME)],
            suites=RED,
        ),
        monkeypatch,
    )
    assert result["rounds"] == 0


def test_a_pr_opened_ready_counts_every_round_it_ever_had(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No `ready_for_review` event and not a draft means it was never a draft.

    The absence of the event must not be read as "the cap never started" — that would exempt every
    PR opened the ordinary way, which is most of them.
    """
    _, result = _run(_routes(reviews=[_review(RABBIT, HEAD)], timeline=[], suites=RED), monkeypatch)
    assert result["rounds"] == 1


def test_an_unreadable_timeline_counts_everything_rather_than_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cap is a safety control, so an API failure must fail toward counting.

    Capping one round early asks the maintainer a question; the other direction hands out an
    unbounded review budget, and metered providers make that a bill as well as a risk.
    """
    routes = _routes(reviews=[_review(RABBIT, HEAD)], suites=RED)
    del routes[("GET", "/repos/bioedca/tether/issues/99/timeline")]
    _, result = _run(routes, monkeypatch)
    assert result["rounds"] == 1


def test_a_draft_cannot_be_capped_by_reviews_the_payload_left_undated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#423: the malformed-data escape used to answer before the phase did, so a draft could cap.

    `_counts_as_round` opened on *"no usable timestamp, so count it"*, which is the right answer
    when the phase is unknown and the wrong one when the phase is **draft** — ADR-0062 says the
    draft phase spends no rounds at all, and `_COUNT_NOTHING` exists to say so. The sentinel was
    the last clause, so it almost never got asked: measured over the shapes `when` can take out of
    a REST payload, nine of ten reached the counting answer on a draft.

    Two metered reviews at different heads with no `submitted_at` is the reachable form. Before the
    reorder this reported `rounds == 2` and `capped` on a pull request that has never been ready —
    the state `_advance_state` carried a branch for, which is why #419 exists and why that branch
    could be deleted rather than tested.
    """
    _, result = _run(
        _routes(
            pr=_pr(draft=True),
            reviews=[_review(RABBIT, HEAD), _review(GREPTILE, OLDER)],
            timeline=[],
            suites=GREEN,
        ),
        monkeypatch,
    )
    assert result["rounds"] == 0, "the draft phase spends no round, whatever the payload looks like"
    assert result["capped"] is False
    assert triage.CAPPED_LABEL not in result["added"]


def test_the_reorder_changes_nothing_outside_the_draft_phase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The control for #423, and the property that makes the reorder safe rather than merely small.

    Fixing a fail-open by moving a clause is only correct if the clause it moved in front of still
    answers everything else the same way. Both other values of `counted_from` are asserted here at
    the unit, over every shape the payload can produce — a well-formed instant, an empty string,
    `None`, and the non-string types a truncated or oddly-rendered response can yield.

    `None` (timeline unreadable, so the phase is unknown) must still count everything: that is the
    fail-closed direction #276 was filed for at nine rounds against a limit of two. A real instant
    must still count the undated entry for the same reason.
    """
    shapes: list[object] = ["2026-08-06T00:00:00Z", "", None, 0, 123, True, [], {}]
    for when in shapes:
        assert triage._counts_as_round(when, None) is True, (
            f"an unreadable phase must count {when!r} - that is the fail-closed direction"
        )
    for when in shapes:
        if when == "2026-08-06T00:00:00Z":
            continue  # a dated entry is compared, and this one predates the instant below
        assert triage._counts_as_round(when, READY_TIME) is True, (
            f"past the ready transition an undated entry still counts: {when!r}"
        )
    # And the one case that changed, at the unit rather than only through `triage()`.
    for when in shapes:
        assert triage._counts_as_round(when, triage._COUNT_NOTHING) is False, (
            f"nothing counts on a draft, including {when!r}"
        )


# ------------------------------------------- #396: GitHub wraps a reply in a review submission
#
# Answering a review thread produces a review submission of its own - empty body, state COMMENTED,
# carrying the reply as its only comment - so counting every submission made ANSWERING a review
# consume the round needed to answer the next one, and a 2-round cap behaved like a 1-round cap.
#
# The fixtures below are the measured shape of PR #385, not an invention:
#
#   4849387696  23:45:12  e5533f8  body 5667  5 comments   <- the one real review
#   4849532819  00:18:21  13c1390  body    0  1 comment    <- five wrappers, one per reply the bot
#   4849532989  00:18:24  13c1390  body    0  1 comment       sent after the author answered a
#   4849533034  00:18:24  13c1390  body    0  1 comment       thread, each comment carrying
#   4849533088  00:18:25  13c1390  body    0  1 comment       in_reply_to_id -> 3708430108
#   4849535136  00:18:58  13c1390  body    0  1 comment

REAL_REVIEW_ID = 4849387696
WRAPPER_IDS = (4849532819, 4849532989, 4849533034, 4849533088, 4849535136)
FINDING_ID = 3708430108


def _submission(user: str, sha: str, review_id: int, *, body: str = "", at: str = AFTER_READY):
    return {
        "user": {"login": user},
        "commit_id": sha,
        "id": review_id,
        "state": "COMMENTED",
        "body": body,
        "submitted_at": at,
    }


def _finding(user: str, sha: str, review_id: int, comment_id: int, *, at: str = AFTER_READY):
    return {
        "user": {"login": user},
        "commit_id": sha,
        "original_commit_id": sha,
        "id": comment_id,
        "pull_request_review_id": review_id,
        "created_at": at,
    }


def _reply(user: str, sha: str, review_id: int, comment_id: int, *, at: str = AFTER_READY):
    return dict(_finding(user, sha, review_id, comment_id, at=at), in_reply_to_id=FINDING_ID)


def test_answering_a_review_does_not_spend_the_round_that_answers_the_next(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE #385 fixture: one real review plus five reply wrappers must count as one round.

    Against the old implementation this is 2 - `{e5533f8, 13c1390}` - because every submission from
    a metered provider added its head. So the PR reached `agent:review-capped` after a single
    review, before the mandatory CodeRabbit gate it cannot merge without.
    """
    reviews = [_submission(RABBIT, OLDER, REAL_REVIEW_ID, body="x" * 5667)]
    comments = [_finding(RABBIT, OLDER, REAL_REVIEW_ID, FINDING_ID)]
    for offset, wrapper_id in enumerate(WRAPPER_IDS):
        reviews.append(_submission(RABBIT, HEAD, wrapper_id))
        comments.append(_reply(RABBIT, HEAD, wrapper_id, 3708500000 + offset))

    _, result = _run(
        _routes(reviews=reviews, comments=comments, timeline=[_ready(READY_TIME)], suites=GREEN),
        monkeypatch,
    )
    assert result["rounds"] == 1, "five replies to one review are still one review"
    assert result["capped"] is False


def test_a_providers_reply_costs_no_round_but_is_still_owed_an_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Where the reply predicate stops, and why it stops there.

    The wrapper costs no round — that is #396. What it must *not* do is clear the owed axis as well.
    A provider answering inside a thread writes an acknowledgement and *"that only half fixes it"*
    in exactly the same shape, so treating every threaded reply as handled would leave a green head
    owing nothing while real feedback sat unanswered. Over-counting a round costs a metered review;
    under-owing merges past a finding. The signal that really separates the two is thread
    resolution, which these REST payloads do not carry and #393 adds.
    """
    fake, result = _run(
        _routes(
            reviews=[_submission(RABBIT, HEAD, WRAPPER_IDS[0])],
            comments=[_reply(RABBIT, HEAD, WRAPPER_IDS[0], 3708500099)],
            timeline=[_ready(READY_TIME)],
            suites=GREEN,
        ),
        monkeypatch,
    )
    assert result["rounds"] == 0, "the wrapper is not a review"
    assert result["review_owed"] is True, "but the reply may still carry a finding"
    assert triage.AMEND_LABEL in fake.added


def test_a_real_finding_in_the_same_shape_still_counts_and_still_owes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The control. Identical payload but for `in_reply_to_id`, and every conclusion flips.

    Without this the two tests above would pass just as happily against an implementation that had
    stopped counting metered reviews altogether.
    """
    fake, result = _run(
        _routes(
            reviews=[_submission(RABBIT, HEAD, WRAPPER_IDS[0])],
            comments=[_finding(RABBIT, HEAD, WRAPPER_IDS[0], 3708500099)],
            timeline=[_ready(READY_TIME)],
            suites=GREEN,
        ),
        monkeypatch,
    )
    assert result["rounds"] == 1
    assert result["review_owed"] is True
    assert triage.AMEND_LABEL in fake.added


def test_an_empty_bodied_review_carrying_real_findings_counts_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#396's second criterion: a bodyless review is not automatically a wrapper.

    A provider can post findings with no summary at all, and `_review_state` has always supported
    that - dropping it would undercount a round that really happened, which is the fail-OPEN
    direction on the cap.
    """
    _, result = _run(
        _routes(
            reviews=[_submission(RABBIT, HEAD, REAL_REVIEW_ID)],
            comments=[
                _finding(RABBIT, HEAD, REAL_REVIEW_ID, 3708500001),
                _reply(RABBIT, HEAD, REAL_REVIEW_ID, 3708500002),
            ],
            timeline=[_ready(READY_TIME)],
            suites=GREEN,
        ),
        monkeypatch,
    )
    assert result["rounds"] == 1, "one non-reply comment makes the submission a review"


def test_the_wrapper_filter_and_the_rewritten_commit_id_are_independent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#396's fourth criterion: reconciled with `_reviewed_head`'s note, not layered on it.

    Both defects make one review look like two, and neither subsumes the other. `_reviewed_head`
    fixes the *head* — GitHub carries a real finding forward onto the commit that answered it —
    while this fixes the *count*, where the answer itself became evidence. Here they occur together:
    a real finding written at `OLDER` and since re-pointed at `HEAD`, plus a wrapper genuinely at
    `HEAD`. One round, at `OLDER`. Fix either defect alone and this reads 2.
    """
    _, result = _run(
        _routes(
            reviews=[
                _submission(RABBIT, OLDER, REAL_REVIEW_ID, body="findings"),
                _submission(RABBIT, HEAD, WRAPPER_IDS[0]),
            ],
            comments=[
                dict(_finding(RABBIT, HEAD, REAL_REVIEW_ID, FINDING_ID), original_commit_id=OLDER),
                _reply(RABBIT, HEAD, WRAPPER_IDS[0], 3708500098),
            ],
            timeline=[_ready(READY_TIME)],
            suites=GREEN,
        ),
        monkeypatch,
    )
    assert result["rounds"] == 1


# ----------------------------------------- #400: a review nobody submitted is not a review round
#
# GitHub's schema for the reviews endpoint says a review "created in the PENDING state" is "not
# submitted and therefore does not include the `submitted_at` property". `_counts_as_round` counts a
# timestamp-less entry on purpose, so an unsubmitted draft spent a round no review had taken.
#
# Same family as #396 - an artefact that is not a review being counted as one - and a distinct
# cause: #396's wrapper is a SUBMITTED review with nothing in it, this is an UNSUBMITTED one.
#
# THE FIRST THREE OF THESE ARE NOW PARTLY VACUOUS, and are kept as documentation of the round axis
# rather than as its coverage. #399 landed `_is_blocking`, which spends a round only on
# `CHANGES_REQUESTED` or an inline finding, so `PENDING` stops counting there whether or not the
# guard exists. Measured across the merge, not inferred: neutralising `UNSUBMITTED_REVIEW_STATE`
# failed two of the three before it and none of them after.
#
# The guard did not become redundant, it MOVED - to the gate, where a `PENDING` submission carrying
# a body reaches `_says_something` and would prove the mandatory gate from a review its author has
# not sent. `test_a_review_still_being_drafted_proves_no_gate` is what binds it now, and it fails
# when the constant is neutralised. Keep that one; the three below may be deleted with the guard.
#
# This is the shape to watch for whenever two branches touch one predicate: a test can keep passing
# because a SIBLING change started answering the same question, and green then means nothing.


def test_a_review_still_being_drafted_is_not_a_round(monkeypatch: pytest.MonkeyPatch) -> None:
    """#400: a `PENDING` review has no `submitted_at`, and the counter counted it for that.

    One submitted review at `OLDER`, one still in someone's editor at `HEAD`. Only the first is a
    review that happened. Before the fix the draft's missing timestamp took the
    fail-toward-counting branch of `_counts_as_round` and this read **2** - a pull request capped by
    a review nobody had sent, and one nothing could un-send.
    """
    _, result = _run(
        _routes(
            reviews=[
                dict(_review(RABBIT, OLDER), submitted_at=AFTER_READY),
                dict(_review(RABBIT, HEAD), state="PENDING"),
            ],
            timeline=[_ready(READY_TIME)],
            suites=GREEN,
        ),
        monkeypatch,
    )
    assert result["rounds"] == 1, "the unsubmitted draft is not a round"
    assert result["capped"] is False


def test_a_review_still_being_drafted_owes_nothing_either(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other axis. Nothing is owed an answer to a finding its author has not sent yet.

    `owed` deliberately fails toward owing everywhere else - a threaded reply counts (#404), a draft
    finding counts (#384) - because those are all things a provider has actually published. An
    unsubmitted review is the one case where there is nothing on the pull request to answer, so the
    skip covers both axes rather than only the round one.
    """
    fake, result = _run(
        _routes(
            reviews=[dict(_review(RABBIT, HEAD), state="PENDING", body="half-written")],
            timeline=[_ready(READY_TIME)],
            suites=GREEN,
        ),
        monkeypatch,
    )
    assert result["rounds"] == 0
    assert result["review_owed"] is False
    assert triage.AMEND_LABEL not in fake.added


def test_a_timestampless_review_that_is_not_pending_still_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The control, and the half of #400 that is a REFUSAL to change something.

    The fix must not arrive by teaching `_counts_as_round` to distrust a missing timestamp. That
    function fails toward counting for malformed data by design (ADR-0062), and narrowing it would
    trade a visible over-count for the fail-open direction #276 reached at 9 rounds against a limit
    of 2. So a review with no timestamp and no state at all - the shape an oddly-rendered payload
    would take - still spends its round, and only the explicitly `PENDING` one does not.

    `counted_from` is set here on purpose: with no ready event `_counts_as_round` short-circuits on
    `counted_from is None` and this would hold without exercising the branch under test.
    """
    _, result = _run(
        _routes(
            reviews=[_review(RABBIT, HEAD)],
            timeline=[_ready(READY_TIME)],
            suites=GREEN,
        ),
        monkeypatch,
    )
    assert result["rounds"] == 1, "a malformed entry is still counted; only PENDING is exempt"


def test_a_review_still_being_drafted_is_not_a_look_at_this_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The axis the `PENDING` guard is load-bearing on, and it has now moved twice.

    **The three tests above went partly vacuous when #399 landed.** `_is_blocking` spends a round
    only on `CHANGES_REQUESTED` or an inline finding, so `PENDING` stopped counting on the round
    axis whether or not the guard existed. #414 replaced them with an assertion on `converged`,
    because on the gate axis a half-written review WITH A BODY was the fail-open case.

    **That replacement went vacuous in turn when #415 landed, in this same pull request.** The
    proving half is now an allowlist and `PENDING` is not in it, so `converged` is false with or
    without the guard. Measured the same way both times, by neutralising
    `UNSUBMITTED_REVIEW_STATE` and rerunning: two of three failed before #399, none after it, and
    the `converged` replacement failed before #415 and not after.

    What is left is `read_head` — *a provider has looked at this exact head* — which is the
    authority `_advance_state` requires before it will walk the lane on. It is set before either
    other axis is consulted, so nothing downstream masks it: without the guard, a review sitting
    unsent in its author's editor would be a look, and the lane would advance out of a head no
    provider had reported on. That is the last thing the guard uniquely does, so it is what this
    test now asserts.

    The comment above this block, warning that a sibling change can silently answer the same
    question, was written by #414 about #399. It predicted this instance.
    """
    _run(
        _routes(
            reviews=[
                dict(_review(RABBIT, HEAD), state="PENDING", body="half-written, not sent"),
            ],
            timeline=[_ready(READY_TIME)],
            suites=GREEN,
        ),
        monkeypatch,
    )
    _, _, read_head, converged = triage._review_state(99, HEAD, READY_TIME)
    assert read_head is False, (
        "an unsent review is not a provider having looked, and `_advance_state` reads this as the "
        "authority to move the lane on"
    )
    # Still true, and still worth stating - but no longer what binds the guard, since the allowlist
    # in `GATE_PROVING_STATES` refuses `PENDING` on its own.
    assert converged is False


# ------------------------------------ #393: only a push used to clear `review_owed`
#
# For a NON-BLOCKING finding the contract forbids a push: defer it to one follow-up issue and
# resolve the thread with the link. So the answer the contract prescribes cleared nothing,
# `agent:needs-amend` survived a completed answer, and the launcher kept issuing AMEND sessions
# until it hit its cap. Resolution is the signal because it is what that instruction produces and
# the only part of it a machine can read - the follow-up LINK is contract, not payload.


def test_a_deferred_finding_stops_owing_once_its_thread_is_resolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE #393 case: reply, resolve, link a follow-up, do not push. No AMEND owed.

    Against the old implementation `review_owed` is True here and stays True forever, because its
    only exit was `pushing a fix moves the head` — exactly what the contract forbids for a
    non-blocking finding.
    """
    fake, result = _run(
        _routes(
            comments=[_finding(RABBIT, HEAD, REAL_REVIEW_ID, 4242)],
            threads=[_thread(4242, 4243, resolved=True)],
            timeline=[_ready(READY_TIME)],
            suites=GREEN,
        ),
        monkeypatch,
    )
    assert result["review_owed"] is False
    assert triage.AMEND_LABEL not in fake.added


def test_a_thread_closed_without_a_reply_has_answered_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex P2: `isResolved` alone is not the contract's deferral.

    That is reply + resolve + a follow-up link, and the reply is the part a machine can see.
    Somebody clicking resolve on an unanswered finding must not retract the AMEND it owes.
    """
    fake, result = _run(
        _routes(
            comments=[_finding(RABBIT, HEAD, REAL_REVIEW_ID, 4242)],
            threads=[_thread(4242, resolved=True)],
            timeline=[_ready(READY_TIME)],
            suites=GREEN,
        ),
        monkeypatch,
    )
    assert result["review_owed"] is True
    assert triage.AMEND_LABEL in fake.added


def test_a_reply_that_is_not_a_deferral_does_not_license_the_resolve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex P1: resolution may only mean the one thing the severity floor permits it to mean.

    Resolve-without-push is allowed for a **non-blocking** finding, deferred to a follow-up issue.
    A blocking finding must be fixed, which moves the head. So `isResolved` plus *any* reply was too
    weak: resolving a blocking CodeRabbit thread under a "will look at this" retracted the AMEND
    authority that existed to address it. The `Deferred: … Tracked in #N` reply is the evidence the
    contract itself prescribes, and it is what this now requires.

    Severity is still unread — a worker that writes the deferral over a blocking finding defeats
    this — and that is #409. What is closed here is the path where nothing was written at all.
    """
    fake, result = _run(
        _routes(
            comments=[_finding(RABBIT, HEAD, REAL_REVIEW_ID, 4242)],
            threads=[_thread(4242, 4243, resolved=True, answer="Ack, will look at this.")],
            timeline=[_ready(READY_TIME)],
            suites=GREEN,
        ),
        monkeypatch,
    )
    assert result["review_owed"] is True
    assert triage.AMEND_LABEL in fake.added


def test_a_deferral_written_by_the_provider_is_not_the_authors_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The `[bot]` suffix, which GraphQL drops and REST keeps.

    `EXTERNAL_PROVIDERS` is built from REST logins (`coderabbitai[bot]`), so testing the raw GraphQL
    login (`coderabbitai`) against it matches nothing — no error, no empty result, just a provider
    quoting the deferral wording in its own finding and passing as the author's answer. Both
    spellings are tried, and this is what fails if only one is.
    """
    fake, result = _run(
        _routes(
            comments=[_finding(RABBIT, HEAD, REAL_REVIEW_ID, 4242)],
            threads=[_thread(4242, 4243, resolved=True, answered_by=RABBIT_GQL)],
            timeline=[_ready(READY_TIME)],
            suites=GREEN,
        ),
        monkeypatch,
    )
    assert result["review_owed"] is True
    assert triage.AMEND_LABEL in fake.added


def test_a_sixty_four_bit_comment_id_still_matches_its_resolved_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex P2: `databaseId` is deprecated for exactly this, and the ids here are already that big.

    A modern comment would return no usable id, the resolved set would never contain the REST `id`,
    and the finding would stay owed forever — this function's own bug, reintroduced through the
    field it reads. `fullDatabaseId` is a BigInt and arrives as a string, so it is normalised.
    """
    big = 3708430108000
    _, result = _run(
        _routes(
            comments=[_finding(RABBIT, HEAD, REAL_REVIEW_ID, big)],
            threads=[_thread(big, big + 1, resolved=True)],
            timeline=[_ready(READY_TIME)],
            suites=GREEN,
        ),
        monkeypatch,
    )
    assert result["review_owed"] is False


def test_a_finding_on_an_unresolved_thread_still_owes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The control, and #393's second criterion. Identical but for `isResolved`."""
    fake, result = _run(
        _routes(
            comments=[_finding(RABBIT, HEAD, REAL_REVIEW_ID, 4242)],
            threads=[_thread(4242, 4243, resolved=False)],
            timeline=[_ready(READY_TIME)],
            suites=GREEN,
        ),
        monkeypatch,
    )
    assert result["review_owed"] is True
    assert triage.AMEND_LABEL in fake.added


def test_resolving_threads_does_not_clear_an_outstanding_changes_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#393's third criterion: the two are separate signals.

    `CHANGES_REQUESTED` is a verdict on the pull request, not a comment on a line. It is withdrawn
    by the provider reviewing again, never by the author tidying the threads underneath it — so an
    author who could clear it by resolving would be dismissing a review on their own authority.
    """
    fake, result = _run(
        _routes(
            reviews=[
                dict(
                    _submission(RABBIT, HEAD, REAL_REVIEW_ID, body="please fix"),
                    state="CHANGES_REQUESTED",
                )
            ],
            comments=[_finding(RABBIT, HEAD, REAL_REVIEW_ID, 4242)],
            threads=[_thread(4242, 4243, resolved=True)],
            timeline=[_ready(READY_TIME)],
            suites=GREEN,
        ),
        monkeypatch,
    )
    assert result["review_owed"] is True, "the submission outlives its threads"
    assert triage.AMEND_LABEL in fake.added


def test_thread_resolution_is_not_read_when_nothing_could_be_cleared_by_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The query is asked only when it can change an answer.

    `clear_mirror` and the round-label paths run on every triage event, and a pull request with no
    external finding at the current head cannot have an answered one.
    """
    fake, _ = _run(_routes(reviews=[_review(RABBIT, HEAD)], suites=GREEN), monkeypatch)
    assert not [c for c in fake.calls if c[1] == "/graphql"]


def test_an_unreadable_thread_read_fails_the_job_rather_than_answering_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fails closed by RAISING, and the distinction is #388's.

    Returning "nothing resolved" would also keep every finding owed, and would restore this exact
    bug invisibly and forever the first time the query broke. A transport failure must not become a
    verdict about work nobody read — so this is the same treatment `_paginate_or_raise` gives an
    unreadable review list one call above.
    """
    routes = _routes(
        comments=[_finding(RABBIT, HEAD, REAL_REVIEW_ID, 4242)],
        timeline=[_ready(READY_TIME)],
        suites=GREEN,
    )
    routes[("POST", "/graphql")] = (200, {"errors": [{"message": "Something went wrong"}]})
    with pytest.raises(triage.TriageError):
        _run(routes, monkeypatch)


class _Answer:
    """A ``urlopen`` result that goes wrong where ``_request``'s handlers cannot see it."""

    status = 200

    def __enter__(self) -> _Answer:
        """``urlopen`` is used as a context manager, so this stands in for one."""
        return self

    def __exit__(self, *_exc: object) -> bool:
        """Never suppress: a test that swallowed its own failure would pass for nothing."""
        return False

    def read(self) -> bytes:
        """What a TLS-inspecting proxy answers with — a 200 whose body is not JSON at all."""
        return b"<html>502 Bad Gateway</html>"


def test_an_unreadable_comment_list_withholds_the_verdict_instead_of_ending_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_verdict_at_head` promises to fail SOFT, and only `ClaimError` was ever soft.

    Deliberately end to end through the real `_paginate` and `_request` rather than a stubbed
    `ClaimError`, because a stub proves only that the handler present catches what it names. The
    defect was the handler being narrower than the ways the read fails: a proxy answering with an
    HTML error page raised `JSONDecodeError` straight through `except claim.ClaimError`, and one
    unreadable list ended the triage run for every issue in the repository (CodeRabbit on #407).

    Soft is the right direction *here* and nowhere near a general licence — no verdict seen
    withholds an authority. The thread-read tests above fail closed by raising, for the same
    reason read the other way.
    """
    monkeypatch.setattr(triage.claim, "_token", lambda: "t")
    monkeypatch.setattr(triage.claim.urllib.request, "urlopen", lambda *_a, **_k: _Answer())
    assert triage._verdict_at_head(99, HEAD) is False


def test_a_partial_thread_read_is_refused_rather_than_treated_as_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unread threads would report answered findings as unanswered — and #385 reached 54 threads.

    A short read is indistinguishable from a short list, so the cursor is followed and a walk that
    does not terminate is an error rather than a partial answer.
    """
    routes = _routes(
        comments=[_finding(RABBIT, HEAD, REAL_REVIEW_ID, 4242)],
        timeline=[_ready(READY_TIME)],
        suites=GREEN,
    )
    # Always another page, and never a usable cursor: the walk cannot complete.
    routes[("POST", "/graphql")] = (200, _threads([_thread(4242, 4243, resolved=True)], cursor=""))
    with pytest.raises(triage.TriageError, match="partial read"):
        _run(routes, monkeypatch)


def test_a_malformed_thread_payload_is_refused_rather_than_read_as_nothing_resolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex P2: `or {}` down the GraphQL chain turns a null level into a silent verdict.

    A null `repository`, `pullRequest` or `reviewThreads` would become an empty page and then
    "nothing is resolved" — which SUCCEEDS, keeps every finding owed, and so restores this bug
    permanently and invisibly. Every level is required.
    """
    routes = _routes(
        comments=[_finding(RABBIT, HEAD, REAL_REVIEW_ID, 4242)],
        timeline=[_ready(READY_TIME)],
        suites=GREEN,
    )
    routes[("POST", "/graphql")] = (200, {"data": {"repository": None}})
    with pytest.raises(triage.TriageError, match="malformed"):
        _run(routes, monkeypatch)


def test_a_thread_with_more_comments_than_one_page_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex P3: a finding past the first 100 comments would stay owed after its thread resolved.

    That is this bug one level down, so the nested connection reports `hasNextPage` and a truncated
    thread fails the job rather than being half-read.
    """
    routes = _routes(
        comments=[_finding(RABBIT, HEAD, REAL_REVIEW_ID, 4242)],
        threads=[_thread(4242, 4243, resolved=True, truncated=True)],
        timeline=[_ready(READY_TIME)],
        suites=GREEN,
    )
    with pytest.raises(triage.TriageError, match="part of a thread"):
        _run(routes, monkeypatch)


def test_a_thread_whose_paging_is_unreadable_is_refused_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same refusal, reached through the check meant to enforce it (CodeRabbit on #405).

    `(comments.get("pageInfo") or {}).get("hasNextPage")` read an ABSENT connection as `False`, so
    a payload that could not be paged was judged from whatever came back — the half-read the test
    above refuses, arriving through its own guard. The query always selects `pageInfo`, so absent
    means unreadable rather than short.
    """
    thread = _thread(4242, 4243, resolved=True)
    del thread["comments"]["pageInfo"]
    routes = _routes(
        comments=[_finding(RABBIT, HEAD, REAL_REVIEW_ID, 4242)],
        threads=[thread],
        timeline=[_ready(READY_TIME)],
        suites=GREEN,
    )
    with pytest.raises(triage.TriageError, match="paging could not be read"):
        _run(routes, monkeypatch)


def test_the_thread_walk_follows_the_cursor_and_joins_every_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SUCCESS direction of the page walk, which nothing could express until now (#410).

    `Fake` answers by `(method, path)`, so every `/graphql` call in one test got the same page back
    and the paging code was covered only where it refuses to run. A finding whose resolved thread
    sits on page two would have stayed owed forever, and neither refusal test above can see it.
    """
    routes = _routes(
        comments=[
            _finding(RABBIT, HEAD, REAL_REVIEW_ID, 4242),
            _finding(RABBIT, HEAD, REAL_REVIEW_ID, 5353),
        ],
        timeline=[_ready(READY_TIME)],
        suites=GREEN,
    )
    fake, result = _run(
        routes,
        monkeypatch,
        pages={
            None: _threads([_thread(4242, 4243, resolved=True)], cursor="CUR1"),
            "CUR1": _threads([_thread(5353, 5354, resolved=True)]),
        },
    )
    # Page two is requested `after` page one's `endCursor`, and there is no third request.
    assert isinstance(fake, Paging)
    assert fake.cursors == [None, "CUR1"]
    # Both findings are cleared, so the resolved set is the UNION of the pages rather than
    # whichever page happened to come back last.
    assert result["review_owed"] is False
    assert triage.AMEND_LABEL not in fake.added


def test_a_walk_longer_than_the_page_budget_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """`claim.MAX_PAGES` bounds the walk, and the arm that enforces it was never exercised.

    `test_a_partial_thread_read_is_refused_rather_than_treated_as_complete` reaches the same
    `raise` through the *unusable cursor* break, so the budget itself — a walk where every page
    hands back a perfectly good cursor — had no coverage on any ref. Both arms share one message,
    which is exactly how an untested one hides behind a tested one.

    The map describes exactly `MAX_PAGES` responses, so it also pins WHERE the walk stops (#420).
    Described one page longer, a walk that requested one page too many would still be answered,
    still fall out of the loop, and still raise this same error — leaving the budget asserted and
    its boundary not. `Paging` refuses an unlisted cursor with `KeyError` precisely so that an
    overrun is the failure rather than a case to paper over, and this test now relies on it.
    """
    routes = _routes(
        comments=[_finding(RABBIT, HEAD, REAL_REVIEW_ID, 4242)],
        timeline=[_ready(READY_TIME)],
        suites=GREEN,
    )
    # Every page is readable and every page has another after it, so only the budget stops this.
    # The final page still hands back `CUR{MAX_PAGES}` — a perfectly good cursor that this map
    # deliberately does not answer, so asking for it is a `KeyError` and not a longer walk.
    pages = {
        (None if n == 0 else f"CUR{n}"): _threads([], cursor=f"CUR{n + 1}")
        for n in range(triage.claim.MAX_PAGES)
    }
    with pytest.raises(triage.TriageError, match="more review threads than this query will walk"):
        _run(routes, monkeypatch, pages=pages)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (4242, 4242),
        (3708430108123456789, 3708430108123456789),  # 64-bit, the case #405 already normalises
        (0, None),
        (-1, None),
        (True, None),  # a bool IS an int in Python, and would compare equal to comment id 1
        ("4242", None),  # the GraphQL node id is a string; truthy, and not this field
        (None, None),
    ],
)
def test_only_a_usable_comment_id_can_ever_clear_a_finding(
    value: Any, expected: int | None
) -> None:
    """One field, one reading — `answerable` and the owed axis now share this predicate (#410)."""
    assert triage._clearable_comment_id({"id": value}) == expected
    assert triage._clearable_comment_id({}) is None


def test_an_unusable_comment_id_owes_even_when_a_resolved_thread_names_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The direction guard, and the reason `answerable` was NOT widened to match (#410).

    Aligning the two readings the other way — dropping `answerable`'s truthiness test so it agrees
    with a bare membership check — reads as the tidier fix and is fail-OPEN. Here it would make
    `answerable` true, populate `resolved` with `{0, 1}`, match the finding's `0` against it, and
    withhold `agent:needs-amend` on a head whose external finding nothing has answered.

    Unreachable against today's REST payload, which always sends a positive `id`. This pins the
    direction rather than regressing a live defect, and it fails against that tidier fix.
    """
    fake, result = _run(
        _routes(
            comments=[dict(_finding(RABBIT, HEAD, REAL_REVIEW_ID, 4242), id=0)],
            threads=[_thread(0, 1, resolved=True)],
            timeline=[_ready(READY_TIME)],
            suites=GREEN,
        ),
        monkeypatch,
    )
    assert result["review_owed"] is True
    # And the query that could only ever have cleared it is not spent asking.
    assert not [c for c in fake.calls if c[1] == "/graphql"]


def test_a_boolean_graphql_id_cannot_clear_the_comment_it_compares_equal_to(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The BUILDER side of the same rule, and the half #410 left open (CodeRabbit `Major` on #413).

    `_clearable_comment_id` refused a `bool` on the READER side, which is the axis that asks *can a
    resolved thread ever clear this comment*. Nothing refused one on the side that BUILDS
    `resolved`, and the two are halves of a single membership test — so a boolean `fullDatabaseId`
    entered the set, `True == 1`, and `1 in {True}` cleared the REST comment numbered `1`.

    That is fail-OPEN on the axis that decides AMEND authority: a real, unanswered external finding
    stops owing, `agent:needs-amend` is withheld, and no session is ever issued to answer it. The
    comment carrying it here is an ordinary CodeRabbit finding — the only unusual thing in the
    payload is one boolean, on a different comment, in a different endpoint's response.

    Reachable only from a malformed GraphQL payload, like its reader-side sibling above. Pinned for
    the same reason: the two sides must agree about what a usable id is, and they now share one
    predicate rather than two expressions that happen to match.
    """
    thread = _thread(1, 2, resolved=True)
    # The finding's own node, with the one value that compares equal to REST comment id 1.
    thread["comments"]["nodes"][0]["fullDatabaseId"] = True
    fake, result = _run(
        _routes(
            comments=[_finding(RABBIT, HEAD, REAL_REVIEW_ID, 1)],
            threads=[thread],
            timeline=[_ready(READY_TIME)],
            suites=GREEN,
        ),
        monkeypatch,
    )
    assert result["review_owed"] is True, "a boolean id must not clear comment 1"
    assert triage.AMEND_LABEL in fake.added


@pytest.mark.parametrize(
    ("raw", "usable"),
    [
        (4242, True),
        (3708430108123456789, True),  # 64-bit, which is why `fullDatabaseId` replaced `databaseId`
        (True, False),  # a bool IS an int, and equals the id of comment 1
        (False, False),
        (0, False),
        (-1, False),
        ("4242", False),  # the caller normalises a BigInt string BEFORE asking
        (None, False),
    ],
)
def test_both_sides_of_the_resolved_set_share_one_usability_rule(raw: object, usable: bool) -> None:
    """One predicate, asked by the reader and the builder alike — that is the property here."""
    assert triage._is_a_usable_comment_id(raw) is usable


def test_a_head_carrying_only_replies_still_reads_the_threads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The `answerable` predicate tracks the loop, and the loop moved.

    An earlier revision gave this predicate the loop's `in_reply_to_id` filter, on the ground that a
    head carrying only replies had nothing resolution could clear. #396 removed that filter from the
    owed axis — a threaded reply can carry a finding — so the ground is gone and keeping the filter
    would be worse than redundant: such a head would owe, skip the one read that could clear it, and
    owe forever.

    The saving it was written for is still had, one case over: a head with no external comment at
    all makes no query. That is `test_a_head_with_no_external_comment_never_asks_about_threads`.
    """
    fake, result = _run(
        _routes(
            reviews=[_submission(RABBIT, HEAD, WRAPPER_IDS[0])],
            comments=[_reply(RABBIT, HEAD, WRAPPER_IDS[0], 3708500099)],
            threads=[_thread(3708500099, 3708500100, resolved=True)],
            timeline=[_ready(READY_TIME)],
            suites=GREEN,
        ),
        monkeypatch,
    )
    assert [c for c in fake.calls if c[1] == "/graphql"], "the reply owes, so it must be readable"
    assert result["review_owed"] is False, "and its resolved thread is what clears it"


def test_a_head_with_no_external_comment_never_asks_about_threads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The query is still only paid for when something could be cleared by it.

    Nothing external at this head means nothing owed and nothing to resolve, so a rate limit or a
    transient GraphQL error must not fail the job — `clear_mirror` and the round-label paths run on
    every triage event, including the ones with no review activity at all.
    """
    fake, _ = _run(
        _routes(
            comments=[_finding(RABBIT, OLDER, REAL_REVIEW_ID, 4242)],
            timeline=[_ready(READY_TIME)],
            suites=GREEN,
        ),
        monkeypatch,
    )
    assert not [c for c in fake.calls if c[1] == "/graphql"]


def test_resolution_covers_every_comment_on_the_thread_not_just_the_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`isResolved` is a property of the thread, so a finding anywhere in it is answered.

    Keying on the thread's first comment alone would leave a multi-comment thread owing after it was
    resolved — the same "answered but still owed" state, one level down.
    """
    _, result = _run(
        _routes(
            comments=[_finding(RABBIT, HEAD, REAL_REVIEW_ID, 4243)],
            threads=[_thread(4242, 4243, 4244, resolved=True)],
            timeline=[_ready(READY_TIME)],
            suites=GREEN,
        ),
        monkeypatch,
    )
    assert result["review_owed"] is False


def test_draft_findings_still_owe_an_answer_even_though_they_cost_no_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A finding does not stop mattering because it arrived on a draft.

    The two are separate axes: `rounds` is the budget, `owed` is whether the CURRENT head has an
    unanswered blocking finding. Conflating them would let a worker discard draft findings by
    marking the PR ready.

    The finding is a **metered** provider's so that both halves are load-bearing: Codex is owed an
    answer but never costs a round, which would leave the `rounds` assertion true for a reason that
    has nothing to do with drafts.
    """
    fake, result = _run(
        _routes(
            comments=[dict(_carried(RABBIT, HEAD, HEAD), created_at=DRAFT_TIME)],
            timeline=[_ready(READY_TIME)],
            suites=GREEN,
        ),
        monkeypatch,
    )
    assert result["rounds"] == 0, "a draft finding is not a round"
    assert triage.AMEND_LABEL in fake.added, "but it is still owed an answer"


def test_codex_never_consumes_a_round_even_after_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """Codex is the unmetered lane; the cap exists to ration the providers that cost something.

    Counting it would let free iteration eat the rounds reserved for the mandatory CodeRabbit
    stage — the same strand-before-the-gate failure as counting draft rounds, one step later.
    """
    fake, result = _run(
        _routes(
            reviews=[
                dict(_review(CODEX, OLDER), submitted_at=AFTER_READY),
                dict(_review(CODEX, HEAD), submitted_at=AFTER_READY),
            ],
            timeline=[_ready(READY_TIME)],
            suites=RED,
        ),
        monkeypatch,
    )
    assert result["rounds"] == 0, "Codex is not metered, so it cannot consume a round"
    assert triage.AMEND_LABEL in fake.added, "its findings are still owed an answer"


def test_a_greptile_review_owes_an_answer_like_any_other_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A PAID review that nothing answers is the worst outcome of the lane.

    `EXTERNAL_PROVIDERS` held only Codex and CodeRabbit, so a Greptile review arriving after the
    short-lived worker exited published no AMEND authority at all and the PR could advance without
    answering the credit it had just spent.
    """
    fake, result = _run(
        _routes(
            reviews=[dict(_review(GREPTILE, HEAD), submitted_at=AFTER_READY, state="COMMENTED")],
            comments=[dict(_carried(GREPTILE, HEAD, HEAD), created_at=AFTER_READY)],
            timeline=[_ready(READY_TIME)],
            suites=GREEN,
        ),
        monkeypatch,
    )
    assert triage.AMEND_LABEL in fake.added, "a paid review must be answered"
    assert result["rounds"] == 1, "and a spent credit is a real round"


def test_toggling_back_to_draft_does_not_refund_a_spent_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Entering the counted phase is permanent — otherwise the cap is opt-out.

    Two earlier versions of `_counted_from` each leaked here from a different direction: taking the
    LAST `ready_for_review`, and short-circuiting on the current `draft` flag. Both let a worker buy
    unlimited metered rounds by toggling draft, and a material push is granted no extra round.
    """
    _, result = _run(
        _routes(
            pr=_pr(draft=True),
            reviews=[
                dict(_review(RABBIT, OLDER), submitted_at=AFTER_READY),
                dict(_review(RABBIT, HEAD), submitted_at="2026-08-01T20:00:00Z"),
            ],
            timeline=[_ready(READY_TIME), _ready("2026-08-01T19:00:00Z")],
            suites=RED,
        ),
        monkeypatch,
    )
    assert result["rounds"] == 2, "rounds spent before a draft excursion still count"
    assert result["capped"] is True


# --------------------------------------------------------------------------- the merge path (#308)
#
# The happy path used to clean up nothing. `claim.py release` clears the mirror and the reaper's
# `_requeue` clears it, but a PR that merges clears neither: the squash deletes the branch, so the
# mutex releases correctly, and `Closes: #N` closes the issue — while `status:in-progress` and
# `agent:needs-amend` survive on finished work, and that second label is the launcher's authority to
# start an AMEND session.


MERGE_MIRROR = [
    "status:in-progress",
    "agent:claude",
    triage.AMEND_LABEL,
    triage.CONFLICTED_LABEL,
    "agent:round-1",
    "type:bug",
    "size:S",
]

# The head the merged PR carried. The claim ref is fenced against it, so these two SHAs are what
# distinguishes "my own branch, not yet deleted" from "a successor recreated the claim".
MERGED_HEAD = "f" * 40
SUCCESSOR_HEAD = "1" * 40

CLAIM_REF = "/repos/bioedca/tether/git/ref/heads/agent/issue-7"


def _merged_routes(
    *,
    merged: bool = True,
    labels: list[str] | None = None,
    head_ref: str = "agent/issue-7",
    issue_state: str = "closed",
    claim_ref: tuple[int, Any] = (404, None),
) -> Routes:
    """Routes for the merge path.

    ``claim_ref`` defaults to 404 because that is the ordinary post-merge state: the squash deleted
    the branch. A test that wants the successor race passes the ref back in.
    """
    return {
        ("GET", "/repos/bioedca/tether/pulls/99"): (
            200,
            {
                "number": 99,
                "state": "closed",
                "merged": merged,
                "head": {"ref": head_ref, "sha": MERGED_HEAD},
            },
        ),
        ("GET", "/repos/bioedca/tether/issues/7"): (
            200,
            {
                "state": issue_state,
                "labels": [{"name": n} for n in (labels if labels is not None else MERGE_MIRROR)],
            },
        ),
        ("GET", CLAIM_REF): claim_ref,
    }


def test_a_merged_pull_request_clears_the_claim_mirror(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _install(monkeypatch, _merged_routes())
    result = triage.clear_mirror(number=99, dry_run=False)

    assert result["action"] == "clear-mirror"
    assert result["issue"] == 7
    assert set(result["removed"]) == {
        "status:in-progress",
        "agent:claude",
        triage.AMEND_LABEL,
        triage.CONFLICTED_LABEL,
        "agent:round-1",
    }
    assert set(fake.removed) == set(result["removed"])


def test_the_merge_path_leaves_unrelated_labels_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the claim mirror is the merge path's business.

    `type:bug` and `size:S` describe the work itself and outlive it; stripping the grooming labels
    off every merged issue would destroy the record the backlog is built from.
    """
    fake = _install(monkeypatch, _merged_routes())
    triage.clear_mirror(number=99, dry_run=False)
    assert "type:bug" not in fake.removed
    assert "size:S" not in fake.removed


def test_a_merged_pull_request_never_requeues_the_issue(monkeypatch: pytest.MonkeyPatch) -> None:
    """`status:ready` must not come back. Merged work is done, not queued.

    This is the one place the merge path must differ from `reaper._requeue`, which restores the
    label on purpose. Getting it backwards would hand finished work to the next claimant, and the
    claim would succeed — the issue would be open, labelled ready, and already implemented.
    """
    fake = _install(monkeypatch, _merged_routes())
    triage.clear_mirror(number=99, dry_run=False)

    ready = triage.claim.REQUIRED_LABEL
    assert fake.added == []
    assert ready not in fake.added
    assert ready not in triage.MIRROR_LABELS


def test_a_pull_request_closed_without_merging_clears_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unmerged close may leave a live claim, so its mirror is still true.

    The ref can still exist and the worker can still be holding it; only the reaper decides that a
    claim is dead. Clearing here would erase a live claim's state from the board.
    """
    fake = _install(monkeypatch, _merged_routes(merged=False))
    result = triage.clear_mirror(number=99, dry_run=False)

    assert result == {"action": "skip", "reason": "closed-without-merging", "pr": 99}
    assert fake.removed == []
    assert fake.added == []


def test_the_merge_path_reads_a_closed_issue(monkeypatch: pytest.MonkeyPatch) -> None:
    """By the time the PR has merged, `Closes: #N` has already closed the issue.

    `_issue_labels` returns None for a non-open issue, which is correct for the round counter and
    would make this path a no-op on every real merge — the defect, not the fix.
    """
    fake = _install(monkeypatch, _merged_routes(issue_state="closed"))
    result = triage.clear_mirror(number=99, dry_run=False)
    assert result["removed"]
    assert fake.removed


def test_the_merge_path_ignores_a_branch_that_is_not_a_claim_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A maintainer branch is not agent-claimed work and has no mirror to clear."""
    fake = _install(monkeypatch, _merged_routes(head_ref="docs/issue-189-github-wiki-index"))
    result = triage.clear_mirror(number=99, dry_run=False)

    assert result == {"action": "skip", "reason": "not-agent-claimed", "pr": 99}
    assert fake.removed == []


def test_the_merge_path_writes_nothing_when_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _install(monkeypatch, _merged_routes())
    result = triage.clear_mirror(number=99, dry_run=True)

    assert result["removed"]  # still reports what it WOULD remove
    assert fake.removed == []


def test_the_merge_path_is_a_no_op_on_an_already_clean_issue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No DELETE is issued for a label that was never there."""
    fake = _install(monkeypatch, _merged_routes(labels=["type:bug"]))
    result = triage.clear_mirror(number=99, dry_run=False)

    assert result["removed"] == []
    assert fake.removed == []


def test_the_merge_path_fails_closed_on_an_unreadable_pull_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 502 must not be read as "not merged" — that would silently skip the cleanup forever."""
    _install(monkeypatch, {("GET", "/repos/bioedca/tether/pulls/99"): (502, None)})
    with pytest.raises(triage.TriageError):
        triage.clear_mirror(number=99, dry_run=False)


def test_the_merge_path_fails_closed_on_an_unreadable_issue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routes = _merged_routes()
    routes[("GET", "/repos/bioedca/tether/issues/7")] = (502, None)
    _install(monkeypatch, routes)
    with pytest.raises(triage.TriageError):
        triage.clear_mirror(number=99, dry_run=False)


def test_every_vendor_marker_is_in_the_mirror() -> None:
    """The mirror is derived from `claim.VENDORS`, so a new vendor cannot be forgotten here."""
    for vendor in triage.claim.VENDORS:
        assert f"agent:{vendor}" in triage._mirror_present({f"agent:{vendor}"})


# ------------------------------------------------------------------- the successor fence (#334 R1)
#
# `merged` is a fact about a pull request that is over. Whether the labels on the issue still belong
# to THAT claim is a fact about now, and the two diverge the moment an issue is reopened, re-marked
# `status:ready` and claimed again. This run can arrive late, and - since a failed cleanup now goes
# red on purpose - it can be re-run from the Actions UI arbitrarily long afterwards.


def test_a_successor_claim_is_never_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    """A live ref at a DIFFERENT head means someone else owns this issue now. Touch nothing.

    Deleting here would strip `status:in-progress`, the vendor marker and the round state off a
    live claim while leaving its mutex ref intact: a worker still holding the claim, invisible on
    the board, and a successor whose AMEND authority silently vanished.
    """
    fake = _install(
        monkeypatch,
        _merged_routes(claim_ref=(200, {"object": {"sha": SUCCESSOR_HEAD}})),
    )
    result = triage.clear_mirror(number=99, dry_run=False)

    assert result == {"action": "skip", "reason": "successor-claim", "pr": 99, "issue": 7}
    assert fake.removed == []
    assert fake.added == []


def test_the_claim_ref_is_read_after_the_labels_and_before_any_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Revalidation is worth nothing if it happens early. The fence must be the LAST read.

    A fence read before the issue's labels would leave a window in which the successor appears
    between the two reads and its labels are deleted anyway.
    """
    fake = _install(monkeypatch, _merged_routes())
    triage.clear_mirror(number=99, dry_run=False)

    order = [path for _, path in fake.calls]
    fence = order.index(CLAIM_REF)
    issue_read = order.index("/repos/bioedca/tether/issues/7")
    first_delete = next(i for i, (m, _) in enumerate(fake.calls) if m == "DELETE")
    assert issue_read < fence < first_delete


def test_a_ref_still_at_the_merged_head_is_this_claims_own_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fencing on mere EXISTENCE would break the ordinary path.

    GitHub deletes the branch asynchronously, so the ref can outlive the merge event by the time
    this runs. A ref still pointing at the merged head is this PR's own branch mid-deletion, and
    skipping on it would mean the cleanup silently never happens at all — the very defect #308
    exists to fix, moved to a new place.
    """
    fake = _install(monkeypatch, _merged_routes(claim_ref=(200, {"object": {"sha": MERGED_HEAD}})))
    result = triage.clear_mirror(number=99, dry_run=False)

    assert result["action"] == "clear-mirror"
    assert "status:in-progress" in fake.removed


def test_the_fence_fails_closed_on_an_unreadable_claim_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ "I cannot see whether a successor holds this" must never mean "no successor holds it"."""
    fake = _install(monkeypatch, _merged_routes(claim_ref=(502, None)))
    with pytest.raises(triage.TriageError):
        triage.clear_mirror(number=99, dry_run=False)
    assert fake.removed == []


# ------------------------------------------------------ the window the fence cannot close (#381)
#
# The fence above is one snapshot taken before several authoritative writes, and there is no
# transaction across label writes to make it more than that: the label endpoints take no
# precondition, so "delete only if no successor has claimed since" is not expressible. A successor's
# `claim._cmd_claim` creates the ref FIRST and publishes its mirror SECOND, so one that interleaves
# has its freshly written labels deleted by a loop that has already decided to run.
#
# Nothing prevents this - the cleanup cannot hold a mutex the merge already released, and re-reading
# before each DELETE only narrows the window. So it is DETECTED instead, which converts a silent
# corruption into a red job. No repair path: re-adding would give `clear_mirror` an add path, and
# its not having one is #308's load-bearing property.


def _sequenced_ref(monkeypatch: pytest.MonkeyPatch, *shas: str | None) -> list[int]:
    """Make `_claim_ref_sha` answer differently per call, and count the calls.

    Patched at the function rather than through `Fake`, which answers per route and so cannot tell
    the pre-loop read from the post-loop one — and *that those two reads see different things* is
    the entire subject here.
    """
    seen = [0]

    def answer(issue: int) -> str | None:
        index = min(seen[0], len(shas) - 1)
        seen[0] += 1
        return shas[index]

    monkeypatch.setattr(triage, "_claim_ref_sha", answer)
    return seen


def test_a_successor_that_arrives_mid_cleanup_is_reported_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The residual #334 left behind, made loud (#381).

    Absent when the fence reads it, present at a foreign head immediately after the deletes: that
    is a successor whose claim was created and whose mirror was published *while this loop ran*.
    The labels are already gone; what this asserts is that the job says so.
    """
    fake = _install(monkeypatch, _merged_routes())
    _sequenced_ref(monkeypatch, None, SUCCESSOR_HEAD)

    with pytest.raises(triage.TriageError) as raised:
        triage.clear_mirror(number=99, dry_run=False)

    message = str(raised.value)
    assert "successor" in message
    assert SUCCESSOR_HEAD[:7] in message and MERGED_HEAD[:7] in message
    # The labels it names must be the ones actually deleted, or the repair instruction is a guess.
    assert all(label in message for label in fake.removed)
    # `agent:needs-amend` is the launcher's authority to start an AMEND session, so a successor
    # losing it is the consequence a human most needs pointed at.
    assert triage.AMEND_LABEL in message


def test_the_window_audit_never_re_adds_what_it_reports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`clear_mirror` having no add path is load-bearing (#308), and a repair would destroy it.

    Re-adding `status:ready` to an issue a successor is actively working is precisely the failure
    #308 was written to prevent, so the audit reports and stops. Visible residue over silent
    destruction — the precedence #334 already encodes.
    """
    fake = _install(monkeypatch, _merged_routes())
    _sequenced_ref(monkeypatch, None, SUCCESSOR_HEAD)

    with pytest.raises(triage.TriageError):
        triage.clear_mirror(number=99, dry_run=False)
    assert fake.added == []


def test_the_ordinary_merge_still_reads_the_ref_twice_and_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The audit must cost the ordinary path a `GET` and nothing else.

    Also pins that the post-loop read happens at all: with one read the count is 1 and this test
    fails, which is what makes the two above more than a monkeypatch exercise.
    """
    fake = _install(monkeypatch, _merged_routes())
    seen = _sequenced_ref(monkeypatch, None, None)

    result = triage.clear_mirror(number=99, dry_run=False)
    assert result["action"] == "clear-mirror"
    assert seen[0] == 2, "the claim ref must be read before the deletes and again after them"
    assert "status:in-progress" in fake.removed


def test_a_branch_awaiting_deletion_is_not_mistaken_for_a_successor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The post-loop read compares heads for the same reason the fence does.

    GitHub deletes the merged branch asynchronously, so the ref can still be there when the loop
    finishes. Auditing on mere existence would turn every ordinary merge red.
    """
    _install(monkeypatch, _merged_routes())
    _sequenced_ref(monkeypatch, None, MERGED_HEAD)

    result = triage.clear_mirror(number=99, dry_run=False)
    assert result["action"] == "clear-mirror"


def test_a_label_that_was_already_gone_is_not_reported_as_damage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex P2 on this PR: `DELETE_DONE` includes 404, and 404 removed nothing.

    A re-run is the ordinary way to reach this — the first run removed the labels, the second finds
    them absent and gets 404 for every one. Counting those as damage would report a successor
    losing labels this cleanup never touched, and the repair instruction would say to re-add them:
    #308's failure arriving through the report rather than through the code.

    So nothing was removed, nothing is audited, and the second read is not even spent.
    """
    routes = _merged_routes()
    routes[("DELETE", "/repos/bioedca/tether/issues/7/labels/")] = (404, None)
    _install(monkeypatch, routes)
    seen = _sequenced_ref(monkeypatch, None, SUCCESSOR_HEAD)

    result = triage.clear_mirror(number=99, dry_run=False)
    assert result["action"] == "clear-mirror"
    assert seen[0] == 1, "nothing was removed, so there is no damage to audit"


def test_a_404_is_still_a_successful_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    """The control for the change above: 404 must not become a *failure* either.

    It is success — the label is gone, which is what the call was for. Only its status as *damage*
    changes, and moving it from one bucket to the other would turn every re-run red.
    """
    routes = _merged_routes()
    routes[("DELETE", "/repos/bioedca/tether/issues/7/labels/")] = (404, None)
    _install(monkeypatch, routes)
    _sequenced_ref(monkeypatch, None, None)

    assert triage.clear_mirror(number=99, dry_run=False)["action"] == "clear-mirror"


def test_a_partially_removed_mirror_reports_only_what_it_removed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mixed case, which is what a re-run interrupted by a successor actually looks like.

    One label still present and removed, the rest already absent. The report must name the one, so
    a human re-adds what was really lost and nothing else.
    """
    removed_now = "status:in-progress"
    routes = _merged_routes()
    routes[("DELETE", "/repos/bioedca/tether/issues/7/labels/")] = (404, None)
    routes[("DELETE", f"/repos/bioedca/tether/issues/7/labels/{removed_now}")] = (204, None)
    _install(monkeypatch, routes)
    _sequenced_ref(monkeypatch, None, SUCCESSOR_HEAD)

    with pytest.raises(triage.TriageError) as raised:
        triage.clear_mirror(number=99, dry_run=False)

    message = str(raised.value)
    assert removed_now in message
    assert triage.CONFLICTED_LABEL not in message, "a label that was already gone is not damage"


def test_a_dry_run_writes_nothing_so_it_audits_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing was deleted, so no live claim can have been damaged and nothing needs reporting."""
    _install(monkeypatch, _merged_routes())
    seen = _sequenced_ref(monkeypatch, None, SUCCESSOR_HEAD)

    result = triage.clear_mirror(number=99, dry_run=True)
    assert result["action"] == "clear-mirror"
    assert seen[0] == 1, "a dry run must not spend a second read on damage it could not have done"


def test_the_fence_fails_closed_on_a_pull_request_with_no_head_sha(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a head to compare against there is no fence, so there is no delete either."""
    routes = _merged_routes()
    routes[("GET", "/repos/bioedca/tether/pulls/99")] = (
        200,
        {"number": 99, "state": "closed", "merged": True, "head": {"ref": "agent/issue-7"}},
    )
    fake = _install(monkeypatch, routes)
    with pytest.raises(triage.TriageError):
        triage.clear_mirror(number=99, dry_run=False)
    assert fake.removed == []


def test_a_dry_run_still_reports_the_successor_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fence is a decision, not a write, so `--dry-run` must reach the same one."""
    _install(monkeypatch, _merged_routes(claim_ref=(200, {"object": {"sha": SUCCESSOR_HEAD}})))
    result = triage.clear_mirror(number=99, dry_run=True)
    assert result["reason"] == "successor-claim"


# ------------------------------------------------------------ deletions are not best-effort (#334)
#
# `_apply` may shrug off a failed removal because its state is recomputed on the next event. This
# path has no next event: `closed` fires once, and the merge already deleted the ref that
# `reaper.sweep` would have had to enumerate to retry. A swallowed 429 leaves exactly the stale
# mirror this function exists to remove, and reports that it removed it.


def test_a_failed_label_delete_is_not_reported_as_removed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routes = _merged_routes()
    routes[("DELETE", "/repos/bioedca/tether/issues/7/labels/status:in-progress")] = (429, None)
    _install(monkeypatch, routes)

    with pytest.raises(triage.TriageError) as excinfo:
        triage.clear_mirror(number=99, dry_run=False)
    assert "status:in-progress" in str(excinfo.value)
    assert "429" in str(excinfo.value)


def test_every_label_is_attempted_before_the_failure_is_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Partial progress beats none, and one message naming every failure beats finding them
    one re-run at a time."""
    routes = _merged_routes()
    routes[("DELETE", "/repos/bioedca/tether/issues/7/labels/agent:claude")] = (500, None)
    fake = _install(monkeypatch, routes)

    with pytest.raises(triage.TriageError):
        triage.clear_mirror(number=99, dry_run=False)
    assert "status:in-progress" in fake.removed
    assert triage.AMEND_LABEL in fake.removed


def test_a_label_that_is_already_gone_is_not_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """404 on a DELETE is the desired end state reached by another route, not an error."""
    routes = _merged_routes()
    routes[("DELETE", "/repos/bioedca/tether/issues/7/labels/agent:round-1")] = (404, None)
    _install(monkeypatch, routes)

    result = triage.clear_mirror(number=99, dry_run=False)
    assert result["action"] == "clear-mirror"


# -------------------------------------------- the reaper cannot reach a merged claim (#334 R1)


def test_the_conflict_marker_is_cleared_on_merge(monkeypatch: pytest.MonkeyPatch) -> None:
    """`agent:conflicted` has no other cleanup path once the claim ref is gone.

    `reaper._clear_conflicted` runs only inside `reaper.sweep`'s loop over `_claim_refs()`, i.e.
    over refs that still EXIST. A squash-merge deletes `agent/issue-N`, so a conflict that was
    resolved and then auto-merged before the reaper's next scheduled sweep would keep a marker
    saying it still needs a person — forever. That is the #276 failure exactly.
    """
    fake = _install(monkeypatch, _merged_routes())
    triage.clear_mirror(number=99, dry_run=False)
    assert triage.CONFLICTED_LABEL in fake.removed
    assert triage.CONFLICTED_LABEL in triage.MIRROR_LABELS


def test_the_workflow_does_not_carry_a_trigger_actions_will_not_run() -> None:
    """`pull_request_review_thread` is a webhook Actions does not implement (Codex P2 on #405).

    Resolving a thread can CLEAR `agent:needs-amend`, and no trigger reports it — so this file grew
    one that looks exactly right and never fires. The parser accepts an unknown event, which is what
    makes it dangerous: an inert control reads as a live one, and the missing-trigger bug was
    recorded as fixed while nothing had changed.

    Asserted as an ABSENCE because the mistake is re-addable in one line and looks like a fix. The
    real path is the dispatch in `.agents/tasks/amend.md`, pinned below.

    **Over the whole file, not just the `on:` mapping.** Checking the mapping alone let the same
    dead event straight back in through the job's `if:` pre-filter — in this very pull request,
    three lines below the comment explaining why it cannot fire (CodeRabbit on #405). A name that
    can never equal `github.event_name` is inert wherever it is written, and reading a branch for it
    as evidence the resolve path is handled is precisely the failure being guarded against.
    """
    source = WORKFLOW.read_text(encoding="utf-8")
    offenders = [
        f"{i}: {line.strip()}"
        for i, line in enumerate(source.splitlines(), 1)
        if "pull_request_review_thread" in line and not line.lstrip().startswith("#")
    ]
    assert not offenders, (
        "`pull_request_review_thread` is a webhook Actions does not implement, so it never fires "
        f"and never matches `github.event_name` — inert wherever it appears: {offenders}"
    )


def test_the_deferral_procedure_dispatches_the_triage_that_reads_it() -> None:
    """The resolve path's only working trigger, so the contract has to carry it.

    Without this, `_resolved_comment_ids` is real code that nothing ever runs at the moment it
    matters: the worker replies, resolves, exits, and the label it just answered survives.

    **The ORDER is the property, not the presence.** Asserting only that the command appears let it
    sit in step 2 while the reply it must read was instructed in step 4 — so a worker following the
    numbered order dispatched first, triage read a resolved thread with no
    `Deferred: … Tracked in #N` in it, and correctly kept owing. The run happened and cleared
    nothing, which is indistinguishable from the bug it was added to fix (CodeRabbit on #405).

    **The exit is part of that order.** Moving the dispatch after the reply fixed one half and left
    the other: step 4 still ended `and **exit**` a paragraph ABOVE the dispatch, so a worker reading
    it in order left before running it and no run happened at all — a stronger version of the same
    defect, and one an ordering assertion between only those two tokens cannot see.
    """
    amend = (ROOT / ".agents" / "tasks" / "amend.md").read_text(encoding="utf-8")
    assert "-f pr=" in amend and "-f dry_run=false" in amend
    dispatch = amend.find("workflow run agent-triage.yml")
    reply = amend.find("reply to every thread you answered")
    assert dispatch != -1, "the deferral procedure must dispatch triage"
    assert reply != -1, "the procedure must instruct the reply triage reads"
    assert dispatch > reply, (
        "the dispatch must come AFTER the reply it exists to make readable; dispatching first "
        "gives triage a resolved thread with no deferral in it, and the label survives the answer"
    )
    procedure = amend.find("## Do\n")
    exits = [m.start() for m in re.finditer(r"\bexit\b", amend) if m.start() > procedure]
    assert exits, "the procedure must tell the worker to exit"
    assert min(exits) > dispatch, (
        "every exit instruction must come AFTER the dispatch; an exit written above it tells the "
        "worker to leave before the run, so nothing recomputes the labels the reply just answered"
    )


def test_the_workflow_listens_for_the_merge_event() -> None:
    triggers = _workflow()[True]
    assert "closed" in triggers["pull_request"]["types"]


def test_the_workflow_listens_for_the_lanes_own_phase_change() -> None:
    """`ready_for_review` is a lane step, and nothing else re-triages the claim after it (#394).

    An ADVANCE worker marks the PR ready and exits. No check suite completes and no review is
    submitted by that alone, so without this trigger the lane strands at the step it was just moved
    to — with no authority published to ask for the mandatory CodeRabbit review.
    """
    assert "ready_for_review" in _workflow()[True]["pull_request"]["types"]


def test_the_workflow_passes_merged_only_on_a_closed_pull_request() -> None:
    """The flag is set from the event; whether it MERGED is re-checked from the API in triage.py.

    The action matters, not just the event name. `pull_request` now also carries
    `ready_for_review`, and while `MERGED` was true for every event of that name a ready transition
    ran the merge-cleanup path — where `clear_mirror` sees `merged == false` and skips. The phase
    change then published nothing, stranding the lane at the step the trigger was added to unstick
    (Codex P2 on #407).
    """
    step = next(
        s
        for s in _workflow()["jobs"]["triage"]["steps"]
        if s.get("name") == "Publish review-round state"
    )
    merged = step["env"]["MERGED"]
    assert "github.event_name == 'pull_request'" in merged
    assert "github.event.action == 'closed'" in merged, (
        "a ready_for_review transition must not take the merge-cleanup path"
    )
    assert "--merged" in step["run"]


# ---------------------------------------- #394: a clean review authorises the next lane phase
#
# The lane is a SEQUENCE, and the swarm had one resumption signal for it: `agent:needs-amend`,
# published for a failed check or an owed finding. A clean review owes nothing, so it published
# nothing, `swarm_slots` resumes claimed work only on that label, and the draft sat forever before
# the CodeRabbit gate it cannot merge without.


def _drafted(**over: Any) -> dict[str, Any]:
    """A pull request still in the draft phase: `draft` true and no `ready_for_review` ever."""
    return _pr(draft=True, **over)


def test_a_clean_review_on_an_unfinished_draft_publishes_advance_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE #394 state, and against today's triage nothing at all is published for it.

    A draft, green, nothing owed, and Codex has looked at this exact head and found nothing
    blocking. That is the end of a draft round and the start of the next phase — and it was the one
    state the machine could not represent.
    """
    fake, result = _run(
        _routes(
            pr=_drafted(),
            reviews=[dict(_clean_review(CODEX, HEAD), submitted_at=DRAFT_TIME)],
            suites=GREEN,
        ),
        monkeypatch,
    )
    assert result["advance"] == "added"
    assert triage.ADVANCE_LABEL in fake.added
    assert triage.AMEND_LABEL not in fake.added, "an advance is not an amend"


def test_an_owed_finding_is_still_an_amend_and_never_an_advance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#394's fourth criterion. Publishing both would hand one claim two authorities.

    Whichever resumption arrived first would decide what the session did, which is worse than
    either alone.
    """
    fake, result = _run(
        _routes(
            pr=_drafted(),
            comments=[dict(_carried(CODEX, HEAD, HEAD), created_at=DRAFT_TIME)],
            suites=GREEN,
        ),
        monkeypatch,
    )
    assert triage.AMEND_LABEL in fake.added
    assert triage.ADVANCE_LABEL not in fake.added
    assert result["advance"] != "added"


def test_a_stale_amend_label_withholds_the_advance_it_would_contradict() -> None:
    """The same invariant as above, against the label rather than the finding (CodeRabbit on #407).

    Asserted on `_advance_state` DIRECTLY, and the reason is #399, which landed between that finding
    and this merge. The original test drove it through `triage()` with a capped pull request,
    because the branch that withholds it returned before the one that clears a stale
    `agent:needs-amend` and so stranded the label. #399 moved that branch from *at* the cap to
    *past* it, which is what makes its convergence check reachable — and in doing so it removed the
    only route by which a stale label survives a green, unowed run.

    Every remaining route is gate-blocked, where `_advance_state` withholds for that reason instead.
    Driving this through `triage()` would therefore assert nothing about the AMEND guard: it would
    pass with the guard deleted. So the guard is asserted where it lives.

    Both labels on one claim is the state `swarm_slots` assumes cannot happen. Precisely: its belt
    is `ADVANCE_LABEL in labels and AMEND_LABEL not in labels`, so the outcome is *deterministic* —
    AMEND wins — and the failure is not a race. It is that the belt's stated justification,
    **"triage does not publish both"**, was false, and what it falls back to is an AMEND session
    dispatched against a claim with nothing owed and no findings to fix.
    """
    add: list[str] = []
    remove: list[str] = []
    state = triage._advance_state(
        labels={triage.AMEND_LABEL},
        read_head=True,
        armed=False,
        counted_from=triage._COUNT_NOTHING,
        owed=False,
        running=False,
        gate_blocked=False,
        add=add,
        remove=remove,
    )
    assert state == "not-eligible", "a published AMEND that still stands owns the claim"
    assert triage.ADVANCE_LABEL not in add, "one claim, one authority"


def test_a_gate_blocked_pull_request_is_never_advanced(monkeypatch: pytest.MonkeyPatch) -> None:
    """#399 meeting #394, and neither branch could have caught it alone.

    `agent:gate-blocked` means the convergence verification came back blocking too, so no automatic
    state remains and a maintainer decides. An advance is automatic state. The window is real rather
    than theoretical: `owed` holds while the findings sit there and stops holding the moment the
    author answers them and resolves the threads (#393) — which is exactly when a session would be
    dispatched to walk a lane that has stopped terminating, toward a review it has no round left to
    buy.

    Three blocking rounds put it past the cap; the clean read at the head satisfies every OTHER
    precondition, so what withholds the advance here can only be the gate-blocked one.
    """
    _, result = _run(
        _routes(
            reviews=[
                dict(_review(RABBIT, OLDER), submitted_at=AFTER_READY),
                dict(_review(RABBIT, ROUND_2), submitted_at=AFTER_READY),
                dict(_review(RABBIT, ROUND_3), submitted_at=AFTER_READY),
                dict(_clean_review(RABBIT, HEAD), submitted_at=AFTER_READY),
            ],
            timeline=[_ready(READY_TIME)],
            suites=GREEN,
        ),
        monkeypatch,
    )
    assert result["rounds"] > triage.CAP, "the premise: the convergence check found something too"
    assert result["advance"] == "gate-blocked"


def test_the_run_that_retires_the_amend_is_the_run_that_publishes_the_advance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half of the guard above, and without it that guard rebuilds #394.

    This is the ordinary end of an AMEND: the finding was answered, the fix pushed, the suite came
    back green at the new head, and the free provider has looked at it. `agent:needs-amend` is
    retired on this run — and `labels` is the snapshot read before that, so it still contains the
    label being removed.

    Withholding on the snapshot alone therefore withheld the advance on exactly the run that made
    it due, and nothing fires afterwards to reconsider: clearing a label is not an event. The draft
    would sit before the gate it cannot merge without, which is the stranding #394 exists to end.
    """
    fake, result = _run(
        _routes(
            pr=_drafted(),
            labels=[triage.AMEND_LABEL],
            reviews=[dict(_clean_review(CODEX, HEAD), submitted_at=DRAFT_TIME)],
            suites=GREEN,
        ),
        monkeypatch,
    )
    assert result["amend"] == "cleared", "the answered finding retires its authority"
    assert triage.AMEND_LABEL in fake.removed
    assert result["advance"] == "added", "and the same run hands over the next phase"
    assert triage.ADVANCE_LABEL in fake.added


def test_a_draft_nobody_has_reviewed_yet_is_not_authorised_to_advance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Green and owing nothing is not the same as reviewed and clean.

    Without this, a draft would be authorised to leave the draft phase the moment its checks went
    green — before the free provider had ever looked at it, which is the lane's whole point skipped.
    """
    _, result = _run(_routes(pr=_drafted(), suites=GREEN), monkeypatch)
    assert result["advance"] == "no-review-yet"


def test_a_providers_reply_at_the_new_head_is_not_a_review_of_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`read_head` is *a provider looked at this head*, and a reply is not looking.

    This is the shape that makes it reachable rather than theoretical: a submitted review carries no
    `original_commit_id`, so the wrapper GitHub builds around a provider's reply (#396) binds to
    whatever the head is **now** — which, after the worker has answered and pushed, is a head no
    provider has reviewed. Counted, it authorises the lane to advance out of exactly that state.

    Asserted on `_review_state` directly, because `owed` masks it here: the reply is owed an answer
    (#404), and `_advance_state` refuses on `owed` before ever reading this value. #393 unmasks it
    by letting a resolved thread stop owing, which is why it is fixed now rather than then.
    """
    _install(
        monkeypatch,
        _routes(
            pr=_drafted(),
            reviews=[_submission(CODEX, HEAD, WRAPPER_IDS[0], at=DRAFT_TIME)],
            comments=[_reply(CODEX, HEAD, WRAPPER_IDS[0], 3708500095)],
            suites=GREEN,
        ),
    )
    _, _, read_head, _ = triage._review_state(99, HEAD, None)
    assert read_head is False, (
        "the only provider evidence at this head is a wrapper around a reply; nobody has reviewed "
        "the fix that produced it"
    )


def test_a_ready_pull_request_with_the_merge_armed_is_the_lane_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#394's fourth criterion: nothing published once the lane is complete.

    Complete means the merge is **armed**, not merely that the PR went ready — Codex's P1 on this
    PR. Reading `ready for review` as complete stranded the lane one step further along: an ADVANCE
    worker marks the PR ready and exits, and asking CodeRabbit and then arming are exactly the
    phases auto-merge cannot perform for itself.
    """
    _, result = _run(
        _routes(
            pr=_pr(auto_merge={"enabled_by": {"login": "bioedca"}}),
            reviews=[dict(_clean_review(RABBIT, HEAD), submitted_at=AFTER_READY)],
            timeline=[_ready(READY_TIME)],
            suites=GREEN,
        ),
        monkeypatch,
    )
    assert result["advance"] == "lane-complete"


def test_a_ready_pull_request_awaiting_its_gate_is_still_advanced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The P1 itself: green, ready, owing nothing, and nobody has asked CodeRabbit.

    This is the state an ADVANCE worker leaves behind when it marks the PR ready and exits, and it
    is the one the mandatory gate is waiting on. Before this it published nothing at all.
    """
    _, result = _run(
        _routes(
            reviews=[dict(_clean_review(CODEX, HEAD), submitted_at=DRAFT_TIME)],
            timeline=[_ready(READY_TIME)],
            suites=GREEN,
        ),
        monkeypatch,
    )
    assert result["advance"] == "added"


def test_an_unreadable_timeline_never_publishes_advance_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fail-open the composed audit found, and it was live.

    `_counted_from` answers `None` BOTH for a PR opened ready and for a timeline it could not read.
    That `None` is the fail-toward-COUNTING answer on the round axis, and reusing it here with the
    opposite polarity meant an API failure took the past-the-draft branch, skipped the
    review-happened requirement, and published authority for a pull request nobody had reviewed.

    Requiring the review in every phase is what closes it, so this asserts the composite: unreadable
    timeline plus no review at head publishes nothing.
    """
    routes = _routes(suites=GREEN)
    del routes[("GET", "/repos/bioedca/tether/issues/99/timeline")]
    _, result = _run(routes, monkeypatch)
    assert result["advance"] == "no-review-yet"


def test_the_cap_does_not_withhold_the_counted_phases_remaining_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cap bounds ROUNDS, and neither remaining step is one (Codex P2 on #407).

    A PR that has answered round CAP still needs to request the convergence check ADR-0062 permits,
    and one whose gate has passed still needs a session to arm the merge. Withholding for both left
    a green, gated pull request with nobody authorised to finish it. The DRAFT phase stays subject
    to the cap, where a round really would be spent.
    """
    _, result = _run(
        _routes(
            labels=[triage.CAPPED_LABEL],
            reviews=[
                dict(_review(RABBIT, OLDER), submitted_at=AFTER_READY),
                dict(_review(RABBIT, ROUND_2), submitted_at=AFTER_READY),
                dict(_clean_review(RABBIT, HEAD), submitted_at=AFTER_READY),
            ],
            timeline=[_ready(READY_TIME)],
            suites=GREEN,
        ),
        monkeypatch,
    )
    assert result["capped"] is True, "both rounds spent, or the test asserts nothing about the cap"
    assert result["advance"] == "added"


def test_a_provider_verdict_naming_the_head_counts_as_a_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex's CLEAN pass produces no review object at all — Codex's own P1 on this PR.

    It submits a review when it has findings and posts a plain issue comment when it does not, so
    the draft that most deserves to advance is exactly the one `/pulls/{n}/reviews` cannot show.
    The comment names the head it read, and that string is its only binding.
    """
    routes = _routes(pr=_pr(draft=True), suites=GREEN)
    routes[("GET", "/repos/bioedca/tether/issues/99/comments")] = (
        200,
        [
            {
                "user": {"login": CODEX},
                "body": f"Codex Review: nothing.\n\n**Reviewed commit:** `{HEAD[:10]}`",
            }
        ],
    )
    _, result = _run(routes, monkeypatch)
    assert result["advance"] == "added"


def test_a_verdict_naming_an_older_head_does_not_authorise_an_advance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The control: a verdict is head-bound, and a stale one must not advance a moved diff."""
    routes = _routes(pr=_pr(draft=True), suites=GREEN)
    routes[("GET", "/repos/bioedca/tether/issues/99/comments")] = (
        200,
        [{"user": {"login": CODEX}, "body": f"**Reviewed commit:** `{OLDER[:10]}`"}],
    )
    _, result = _run(routes, monkeypatch)
    assert result["advance"] == "no-review-yet"


def test_a_running_check_suite_holds_the_advance_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """Advancing spends a metered credit or asks the mandatory gate. Not against a moving diff."""
    _, result = _run(
        _routes(
            pr=_drafted(),
            reviews=[dict(_review(CODEX, HEAD), submitted_at=DRAFT_TIME)],
            suites=RUNNING,
        ),
        monkeypatch,
    )
    assert result["advance"] != "added"


def test_the_advance_authority_is_withdrawn_when_it_stops_being_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale advance would authorise a session to walk a lane that has moved on.

    Safe to withdraw here, unlike `agent:needs-amend`: this label has exactly one writer, so
    removing it cannot fight the reaper over a signal that means something else.
    """
    fake, result = _run(
        _routes(
            pr=_drafted(),
            labels=[triage.ADVANCE_LABEL],
            comments=[dict(_carried(CODEX, HEAD, HEAD), created_at=DRAFT_TIME)],
            suites=GREEN,
        ),
        monkeypatch,
    )
    assert result["advance"].startswith("cleared")
    assert triage.ADVANCE_LABEL in fake.removed


def test_the_advance_label_is_part_of_the_merged_claims_mirror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It describes work in flight, so a merge must clear it like every other mirror label (#308).

    Left out, a merged issue would keep an authority to advance a lane whose PR no longer exists.
    """
    assert triage.ADVANCE_LABEL in triage.MIRROR_LABELS
    fake = _install(monkeypatch, _merged_routes(labels=[triage.ADVANCE_LABEL]))
    triage.clear_mirror(number=99, dry_run=False)
    assert triage.ADVANCE_LABEL in fake.removed


def test_the_workflow_passes_merged_only_on_the_pull_request_event() -> None:
    """The flag is set from the event; whether it MERGED is re-checked from the API in triage.py."""
    step = next(
        s
        for s in _workflow()["jobs"]["triage"]["steps"]
        if s.get("name") == "Publish review-round state"
    )
    assert step["env"]["MERGED"] == (
        "${{ github.event_name == 'pull_request' && github.event.action == 'closed' }}"
    )
    assert "--merged" in step["run"]


def _clean_review(user: str, sha: str, *, body: str = "No actionable comments.") -> dict[str, Any]:
    """CodeRabbit's clean form: a body, state `COMMENTED`, and no inline findings.

    Measured on #385 — every CodeRabbit submission there was `COMMENTED`, the dirty ones included,
    so the state does not separate them. Only the absence of findings does.
    """
    return {"user": {"login": user}, "commit_id": sha, "state": "COMMENTED", "body": body}


def test_a_submission_that_proves_nothing_either_way_is_still_read_as_a_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The safety direction of `_reply_wrapper_ids`, and where #399 takes over from #396.

    A submission with no body, no verdict and no comments is *ambiguous* about whether it is a
    review at all. #396's set is written as `owns comments and all of them are replies` rather than
    `owns a non-reply comment` precisely so the ambiguous case is treated as a review — a wrapper
    has to be proven, because undercounting is the fail-open direction on the cap.

    It then spends no round, and that is #399 rather than a hole in #396: it found nothing, so it
    is a convergence verification and free. The two rules compose in the order they are asked —
    *is this a review*, then *did it find anything* — and this pins both halves, because a naive
    implementation of either could produce this same zero for the wrong reason.
    """
    _, result = _run(
        _routes(
            reviews=[_submission(RABBIT, HEAD, REAL_REVIEW_ID)],
            timeline=[_ready(READY_TIME)],
            suites=GREEN,
        ),
        monkeypatch,
    )
    assert result["rounds"] == 0, "it found nothing, so it converged rather than spending a round"
    assert result["review_owed"] is False, (
        "and nothing is owed, so it is not a wrapper being hidden"
    )
    # AND IT DOES NOT SATISFY THE GATE, which is the opposite answer from the same payload
    # (CodeRabbit on #408). Counting the ambiguous case is fail-CLOSED on the cap and fail-OPEN on
    # the gate: `converged` would be true with nothing showing that CodeRabbit reported anything at
    # all, and that is the axis merges are decided on. So the round axis asks *is this a review*
    # and the gate axis asks *did it say something*, and this submission answers yes and no.
    #
    # Asserted on `converged` rather than on `result["gate"]`, because that summary is gated on
    # `capped` and reports `open` for an uncapped PR whichever way the evidence went - the first
    # version of this assertion passed for that reason rather than for the rule.
    _, _, _, converged = triage._review_state(99, HEAD, READY_TIME)
    assert converged is False, "an empty submission is not evidence the gate was met"

    # The control: the same submission carrying one real finding is a round.
    _, blocking = _run(
        _routes(
            reviews=[_submission(RABBIT, HEAD, REAL_REVIEW_ID)],
            comments=[_finding(RABBIT, HEAD, REAL_REVIEW_ID, 4242)],
            timeline=[_ready(READY_TIME)],
            suites=GREEN,
        ),
        monkeypatch,
    )
    assert blocking["rounds"] == 1


# ---------------------------------------------- #399: the gate and the cap could deadlock a PR
#
# ADR-0062 requires "at least one CodeRabbit review with no actionable comments" AND allows two
# rounds. If the round-2 review posts actionable comments, answering them moves the head, and the
# gate then requires a review at THAT head - which would be round 3, which the cap forbids. #385
# reached exactly that state: green on all 16 checks, 54 threads resolved, mergeStateStatus CLEAN,
# and unmergeable by the lane's own text, with nothing anywhere saying why.
#
# The resolution: a round is a metered review that FOUND something. A clean one is the lane
# terminating, not a round, so a capped PR may always ask once more to verify convergence.

ROUND_1 = "1" * 40
ROUND_2 = "2" * 40
#: A third spent round, which only exists PAST the cap - the convergence check that found
#: something too. Nothing but `agent:gate-blocked` follows from it.
ROUND_3 = "3" * 40


def _blocking_round(sha: str, comment_id: int) -> dict[str, list[dict[str, Any]]]:
    """One metered round that found something, in the shape #385's actually had."""
    return {
        "reviews": [_submission(RABBIT, sha, comment_id * 10, body="findings")],
        "comments": [_finding(RABBIT, sha, comment_id * 10, comment_id)],
    }


def test_two_blocking_rounds_then_a_clean_verification_leaves_the_lane_able_to_merge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE #385 deadlock, resolved. The third review is the one that terminates the lane.

    Two rounds found things; both were answered and the fix pushed. The verification at the current
    head comes back clean — which is exactly what the gate asks for — so it costs nothing and the
    count stays at the cap rather than passing it.
    """
    first, second = _blocking_round(ROUND_1, 11), _blocking_round(ROUND_2, 22)
    _, result = _run(
        _routes(
            reviews=[*first["reviews"], *second["reviews"], _clean_review(RABBIT, HEAD)],
            comments=[*first["comments"], *second["comments"]],
            timeline=[_ready(READY_TIME)],
            suites=GREEN,
        ),
        monkeypatch,
    )
    assert result["rounds"] == 2, "the verification found nothing, so it spent nothing"
    assert result["capped"] is True
    assert result["review_owed"] is False
    assert result["gate"] == "satisfied", (
        "clean evidence is free, so `rounds` and `capped` read the same either side of the "
        "convergence check - and reporting `open` here told an operator that another check was "
        "still permitted on a PR whose gate had just been met (CodeRabbit on #408)"
    )


def test_the_gate_is_not_satisfied_by_the_rounds_that_preceded_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The convergence check must have actually happened. Stale round evidence is not it.

    Two blocking rounds, both answered and pushed, and the verification never requested. Everything
    a capped PR reports looks identical to the converged case — `rounds == 2`, nothing owed, checks
    green — because the only thing that differs is a review that does not exist.

    Reading the gate off the round set said `satisfied` here, since answering a finding moves the
    head and leaves those SHAs behind as stale non-empty evidence. That is the mandatory gate
    reporting itself met with no clean review anywhere (CodeRabbit on #408).
    """
    first, second = _blocking_round(ROUND_1, 11), _blocking_round(ROUND_2, 22)
    _, result = _run(
        _routes(
            reviews=[*first["reviews"], *second["reviews"]],
            comments=[*first["comments"], *second["comments"]],
            timeline=[_ready(READY_TIME)],
            suites=GREEN,
        ),
        monkeypatch,
    )
    assert result["rounds"] == 2, "the two rounds still happened"
    assert result["capped"] is True
    assert result["review_owed"] is False, "both were answered and the fix pushed"
    assert result["gate"] == "open", (
        "no CodeRabbit review exists at this head, so the gate ADR-0062 makes mandatory has not "
        "been met - and `open` is what tells the worker to go and buy it"
    )


def test_a_first_review_that_comes_back_clean_satisfies_the_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#411: the cap is a CEILING, not a quota, and the gate used to insist the ceiling was reached.

    `_gate_state` claimed `satisfied` only when `capped` held. That excluded the draft phase, which
    is what it was reaching for, and it excluded this too — a ready pull request whose very first
    CodeRabbit review found nothing. ADR-0062's gate is *a CodeRabbit review with no actionable
    comments*; it says nothing about how many rounds preceded it. Reporting `open` here told an
    operator the lane was unfinished when it was finished, which invites spending a metered credit
    on a review nobody needs.

    Zero rounds, not one: a clean review is free (#399), so `rounds` reads 0 and the old predicate
    could not have been further from firing.
    """
    _, result = _run(
        _routes(
            reviews=[_clean_review(RABBIT, HEAD)],
            timeline=[_ready(READY_TIME)],
            suites=GREEN,
        ),
        monkeypatch,
    )
    assert result["rounds"] == 0, "a clean review spends nothing"
    assert result["capped"] is False
    assert result["gate"] == "satisfied", (
        "the mandatory review happened at this head and found nothing - which is the whole of what "
        "ADR-0062 asks for"
    )


def test_a_clean_review_on_a_draft_does_not_satisfy_the_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The half of the old proxy that was right, kept and now asserted directly (#411).

    A clean review on a draft ends nothing: the optional Greptile credit and the ready transition
    are still ahead, and `.agents/tasks/amend.md` warns about arming a merge on an unfinished lane.
    `capped` excluded this case only as a side effect — draft rounds do not count, so a draft is
    never capped — and a side effect is not a property. Now the phase is asked directly.

    The payload is otherwise identical to the test above, so the phase is the only difference
    between `satisfied` and `open`.
    """
    _, result = _run(
        _routes(
            pr=_pr(draft=True),
            reviews=[_clean_review(RABBIT, HEAD)],
            timeline=[],
            suites=GREEN,
        ),
        monkeypatch,
    )
    assert result["gate"] == "open", (
        "the Greptile step and the ready transition are lane, and the gate is not met until they "
        "are behind the PR"
    )


def test_a_timeline_that_cannot_be_read_reports_the_gate_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fail direction, which is the property — not a side effect of which helper was called.

    `_counted_from` answers `None` for a PR **opened ready** and for a timeline it **could not
    read**, and those want opposite treatment. On the round axis `None` means *count everything*,
    which is fail-closed for a safety control. Read here with the opposite polarity it would mean
    *past the draft* — so one transient 502 on the timeline endpoint would let a clean review report
    the mandatory gate satisfied, which is CodeRabbit's fail-open on #407 in a new place.

    So the caller catches the failure where the two are still distinguishable and hands the gate a
    plain `False`. The evidence is otherwise a gate-satisfying payload, so nothing but the
    unreadable timeline can explain `open`.
    """
    # A clean review at the head to satisfy the gate, AND a blocking one at an older head so the
    # round assertion below can fail. With only the clean review, `rounds == 0` holds whatever
    # `counted_from` is - a clean review never spends a round - so the assertion would have passed
    # even if the round axis had stopped counting entirely (CodeRabbit on #424).
    routes = _routes(
        reviews=[_clean_review(RABBIT, HEAD), _review(RABBIT, OLDER)],
        suites=GREEN,
    )
    routes[("GET", "/repos/bioedca/tether/issues/99/timeline")] = (502, {"message": "upstream"})
    _, result = _run(routes, monkeypatch)
    assert result["gate"] == "open", (
        "an unreadable phase is not evidence the draft is behind this PR, and the gate is the one "
        "axis merges are decided on"
    )
    assert result["rounds"] == 1, (
        "and the ROUND axis keeps its own fail direction from the same failure - `None` there "
        "still means count everything, which is why the two cannot share one answer"
    )


def test_a_greptile_round_does_not_satisfy_the_coderabbit_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gate names one provider, and the round set is provider-blind.

    Greptile is metered, so its blocking review is a real round — but ADR-0062's gate is *CodeRabbit
    with no actionable comments*, and Greptile is the optional leg whose exhaustion never blocks. A
    clean Greptile pass at the head must therefore leave the gate open, or a PR could merge having
    never satisfied the one review the lane requires.
    """
    first, second = _blocking_round(ROUND_1, 11), _blocking_round(ROUND_2, 22)
    _, result = _run(
        _routes(
            reviews=[
                *first["reviews"],
                *second["reviews"],
                _clean_review(GREPTILE, HEAD),
            ],
            comments=[*first["comments"], *second["comments"]],
            timeline=[_ready(READY_TIME)],
            suites=GREEN,
        ),
        monkeypatch,
    )
    assert result["rounds"] == 2, "a clean review is free whoever wrote it"
    assert result["gate"] == "open", "Greptile cannot stand in for the mandatory CodeRabbit gate"


def test_a_reply_wrapper_at_the_head_is_not_the_convergence_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#396's wrapper, seen from the gate axis.

    Answering the round-two threads produces an empty `COMMENTED` submission at the new head that
    is byte-identical to a clean review. Counting it would let a PR satisfy its own gate by
    replying to itself, without any provider having looked at the fix.

    Today two independent things stop this, and only one of them is the gate signal: the reply also
    leaves the head *owing*, because that axis deliberately does not filter replies (#396). So this
    passes even against the pre-fix code. It is kept as a forward guard rather than a binding
    regression, because #393 removes the other one — once a resolved thread stops owing, the reply
    filter on this axis is all that is left.
    """
    first, second = _blocking_round(ROUND_1, 11), _blocking_round(ROUND_2, 22)
    _, result = _run(
        _routes(
            reviews=[
                *first["reviews"],
                *second["reviews"],
                _submission(RABBIT, HEAD, WRAPPER_IDS[0]),
            ],
            comments=[
                *first["comments"],
                *second["comments"],
                _reply(RABBIT, HEAD, WRAPPER_IDS[0], 3708500099),
            ],
            timeline=[_ready(READY_TIME)],
            suites=GREEN,
        ),
        monkeypatch,
    )
    assert result["gate"] == "open", "the PR answered itself; nobody verified the fix"


def test_an_acknowledgement_does_not_retract_a_gate_that_was_already_satisfied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The deadlock this fix nearly rebuilt, caught in self-review before it shipped.

    Two blocking rounds, the verification comes back clean at `HEAD` — the gate is satisfied — and
    then CodeRabbit replies inside one of its own threads, as it routinely does after an answer.
    Voiding the gate on *any* threaded comment made that acknowledgement retract a satisfied gate,
    and nothing could restore it: the signal is head-bound, and a converged pull request has no
    material change left to push. Green, gated, and unmergeable — #399's exact shape, arriving
    through #399's own fix.

    A reply is not an actionable comment (#396), so it is not asked here. The case it was guarding
    against — a reply that really does carry a finding — is `owed`'s, which counts replies for that
    reason (#404) and which `_gate_state` already requires to be false.

    Asserted on `_review_state`'s convergence value rather than on `gate`, because on this branch
    the reply also leaves the head **owed**, and `owed` alone would report `open` whichever way the
    convergence value went. The deadlock becomes reachable when #393 lands and a resolved thread
    stops owing, which is precisely when this assertion starts carrying the whole weight.
    """
    first, second = _blocking_round(ROUND_1, 11), _blocking_round(ROUND_2, 22)
    _install(
        monkeypatch,
        _routes(
            reviews=[
                *first["reviews"],
                *second["reviews"],
                _clean_review(RABBIT, HEAD),
                _submission(RABBIT, HEAD, WRAPPER_IDS[0]),
            ],
            comments=[
                *first["comments"],
                *second["comments"],
                _reply(RABBIT, HEAD, WRAPPER_IDS[0], 3708500097),
            ],
            timeline=[_ready(READY_TIME)],
            suites=GREEN,
        ),
    )
    heads, owed, _, converged = triage._review_state(99, HEAD, READY_TIME)
    assert len(heads) == 2, "neither the clean review nor the reply is a round"
    assert owed is True, "the reply is still owed an answer - that is #404's axis, and it stands"
    assert converged is True, (
        "the verification happened and found nothing; an acknowledgement afterwards is not a "
        "finding and must not take the gate back"
    )


def test_a_dismissed_submission_is_not_evidence_the_gate_was_met(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A withdrawn verdict is an administrative act, not a review (CodeRabbit on #408).

    `_says_something` read `VERDICT_REVIEW_STATES`, which carries `DISMISSED` because on the ROUND
    axis a dismissal is still a submission that happened - counting it there is fail-CLOSED. On the
    gate axis the same membership is fail-OPEN: an empty `DISMISSED` submission at the head made
    `converged` true with nothing showing CodeRabbit had ever looked, on the one axis merges are
    decided on.

    The control is the identical payload under `APPROVED`, so what separates the two cases is the
    state alone - not the provider, the head, or the empty body they share.
    """
    for state, expected in (("DISMISSED", False), ("APPROVED", True)):
        _run(
            _routes(
                reviews=[
                    {"user": {"login": RABBIT}, "commit_id": HEAD, "state": state, "body": ""}
                ],
                timeline=[_ready(READY_TIME)],
                suites=GREEN,
            ),
            monkeypatch,
        )
        _, _, _, converged = triage._review_state(99, HEAD, READY_TIME)
        assert converged is expected, f"{state} must {'' if expected else 'not '}satisfy the gate"
    assert "DISMISSED" in triage.VERDICT_REVIEW_STATES, "still counted on the ROUND axis"
    assert "DISMISSED" not in triage.GATE_PROVING_STATES, "and never on the gate axis"


def test_dismissing_a_changes_requested_review_does_not_convert_it_into_a_clean_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#415's dismissal sequence, walked end to end - and it survived #408's fix.

    That fix took `DISMISSED` out of the set `_says_something` consults, which closed the case where
    the withdrawn submission was EMPTY. It could not close this one, because a real
    `CHANGES_REQUESTED` review has a body and dismissal does not remove it: the body outlives the
    verdict, `_says_something` sees it, and the proving half was reached by *exclusion* - anything
    that is not `CHANGES_REQUESTED`. So the one payload a worker can actually produce here still
    proved the gate.

    The sequence needs no special access beyond the author's own: submit, then
    `PUT /pulls/{n}/reviews/{id}/dismissals`. That is why the allowlist is the fix rather than
    adding `DISMISSED` to another denylist - the next state GitHub invents is admitted by default.

    Three payloads, identical but for `state`, so nothing but the state can explain the difference.
    """
    body = "**Actionable comments posted: 1**\n\nThe head is not bound to the evidence."
    for state, expected in (
        ("CHANGES_REQUESTED", False),  # the verdict itself: blocked, obviously not satisfied
        ("DISMISSED", False),  # withdrawn - and the body it left behind is not a clean review
        ("COMMENTED", True),  # the control: the same body, in CodeRabbit's clean form
    ):
        _run(
            _routes(
                reviews=[
                    {"user": {"login": RABBIT}, "commit_id": HEAD, "state": state, "body": body}
                ],
                timeline=[_ready(READY_TIME)],
                suites=GREEN,
            ),
            monkeypatch,
        )
        _, _, _, converged = triage._review_state(99, HEAD, READY_TIME)
        assert converged is expected, (
            f"a {state} submission carrying a body must "
            f"{'' if expected else 'not '}satisfy the mandatory gate"
        )


@pytest.mark.parametrize(
    "state",
    ["PENDING", "SOMETHING_GITHUB_ADDS_LATER", None],
    ids=["pending", "unknown", "absent"],
)
def test_a_state_the_gate_cannot_read_proves_nothing_and_voids_nothing(
    state: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The allowlist's other half: it must not become a *voiding* list by accident (#415).

    An unreadable state is not evidence a review happened - that is what the allowlist enforces -
    but neither is it evidence one found something. Were it to void, a single odd submission would
    retract a gate a real clean review had already satisfied, and the lane would strand with nothing
    a worker could push to clear it.

    So each case is asserted twice: alone it does not converge, and ALONGSIDE a genuine clean review
    convergence survives. The second assertion is the one that fails if the fix is written as a
    denylist that happens to include these states.

    Asserted on `converged` rather than on `result["gate"]` because that is the predicate #415 is
    about. The reported gate additionally requires `past_the_draft`, which is a separate question
    (#411, answered in this same change) and would make a green here mean two things at once.
    """
    odd: dict[str, Any] = {"user": {"login": RABBIT}, "commit_id": HEAD, "body": "..."}
    if state is not None:
        odd["state"] = state

    _run(_routes(reviews=[odd], timeline=[_ready(READY_TIME)], suites=GREEN), monkeypatch)
    _, _, _, alone = triage._review_state(99, HEAD, READY_TIME)
    assert alone is False, "an unreadable state is not evidence a review happened"

    _run(
        _routes(
            reviews=[odd, _clean_review(RABBIT, HEAD)],
            timeline=[_ready(READY_TIME)],
            suites=GREEN,
        ),
        monkeypatch,
    )
    _, _, _, beside = triage._review_state(99, HEAD, READY_TIME)
    assert beside is True, "and it must not take back a gate a real review met"


def test_a_bodiless_submission_from_the_gate_provider_does_not_satisfy_the_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fail-open half of `_is_a_review`'s deliberate fail-closed direction (CodeRabbit on #408).

    `_reply_wrapper_ids` is written so a WRAPPER must be proven, which makes an unproven submission
    count as a review - the safe direction on the cap, where undercounting is what fails open. The
    gate needs the opposite: a submission with no body, no verdict and no comment of its own is a
    submission the payload does not describe, and accepting it as the mandatory review would satisfy
    the gate with no evidence that CodeRabbit reported anything.

    The control below is the same payload with a body, which is what every real CodeRabbit pass
    carries - so what separates the two cases is exactly the evidence, not the provider or the head.
    """
    empty = {"user": {"login": RABBIT}, "commit_id": HEAD, "state": "COMMENTED", "body": ""}
    _run(_routes(reviews=[empty], timeline=[_ready(READY_TIME)], suites=GREEN), monkeypatch)
    _, _, _, converged = triage._review_state(99, HEAD, READY_TIME)
    assert converged is False, "no body, no verdict, no comment - so no evidence"

    _run(
        _routes(
            reviews=[_clean_review(RABBIT, HEAD)],
            timeline=[_ready(READY_TIME)],
            suites=GREEN,
        ),
        monkeypatch,
    )
    _, _, _, with_body = triage._review_state(99, HEAD, READY_TIME)
    assert with_body is True, "and the same review WITH a body is the gate met"


def test_a_submission_owning_one_real_comment_is_evidence_even_with_no_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The third way a submission can prove it reported, and the reason it is not body-only.

    A provider that writes only inline comments has plainly reviewed. Requiring a body would read
    that as no evidence and strand a pull request whose gate really was met - the failure mode of
    over-tightening, which is why `_says_something` takes three signals rather than one.

    Here the comment is a FINDING, so the gate is blocked rather than satisfied; what the assertion
    pins is that the submission registered at all, which `result["gate"] == "open"` would deny.
    """
    _, result = _run(
        _routes(
            reviews=[_submission(RABBIT, HEAD, REAL_REVIEW_ID)],
            comments=[_finding(RABBIT, HEAD, REAL_REVIEW_ID, 4242)],
            timeline=[_ready(READY_TIME)],
            suites=GREEN,
        ),
        monkeypatch,
    )
    assert result["review_owed"] is True, (
        "the submission is evidence; its comment is what makes the evidence bad news"
    )
    _, _, _, converged = triage._review_state(99, HEAD, READY_TIME)
    assert converged is False, "a finding at this head is the gate blocked, not merely unproven"


def test_a_verification_that_finds_something_reports_why_the_pr_cannot_proceed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#399's third criterion: a PR in this state must say so, not sit green and mergeable.

    Three blocking metered rounds. There is no state left that merges: the gate wants a clean
    review, the cap forbids buying one, and nothing automatic can resolve it. That is a
    maintainer's, and `agent:gate-blocked` is how they find out without hand-counting review ids as
    #385 required.
    """
    first, second = _blocking_round(ROUND_1, 11), _blocking_round(ROUND_2, 22)
    third = _blocking_round(HEAD, 33)
    fake, result = _run(
        _routes(
            reviews=[*first["reviews"], *second["reviews"], *third["reviews"]],
            comments=[*first["comments"], *second["comments"], *third["comments"]],
            timeline=[_ready(READY_TIME)],
            suites=GREEN,
        ),
        monkeypatch,
    )
    assert result["rounds"] == 3
    assert result["gate"] == "blocked"
    assert triage.GATE_BLOCKED_LABEL in fake.added


def test_the_cap_still_binds_on_rounds_that_found_something(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The control. "A converged round is free" must not become "no round is ever counted".

    Two blocking rounds still reach the cap — the property the whole counter exists for, and the one
    a too-generous reading of #399 would destroy. Both were answered at heads that have since moved,
    so nothing is owed at the current head and no session is needed; what the cap denies is a THIRD
    round, and none is being asked for.
    """
    first, second = _blocking_round(ROUND_1, 11), _blocking_round(ROUND_2, 22)
    fake, result = _run(
        _routes(
            reviews=[*first["reviews"], *second["reviews"]],
            comments=[*first["comments"], *second["comments"]],
            timeline=[_ready(READY_TIME)],
            suites=GREEN,
        ),
        monkeypatch,
    )
    assert (result["rounds"], result["capped"]) == (2, True)
    assert triage.CAPPED_LABEL in fake.added
    assert triage.AMEND_LABEL not in fake.added, "nothing is owed, so nothing is authorised"


def test_a_blocking_review_at_the_cap_still_gets_the_session_that_answers_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CodeRabbit on #408: withholding AMEND *at* the cap made the convergence check unreachable.

    An AMEND is not a round. A round is a metered REVIEW; this label authorises the session that
    ANSWERS one. Refusing it at `rounds == CAP` meant the round-2 findings could never be fixed, so
    the pull request could never reach the *everything answered, everything pushed* state the
    convergence check requires — the change written to un-deadlock the gate deadlocking it one step
    earlier, and in the one place the deadlock is hardest to see.

    Distinguished from the test above by WHERE the second round landed: there both were answered and
    the head moved on, here the second review is at the current head and still owes.
    """
    first = _blocking_round(ROUND_1, 11)
    second = _blocking_round(HEAD, 22)
    fake, result = _run(
        _routes(
            reviews=[*first["reviews"], *second["reviews"]],
            comments=[*first["comments"], *second["comments"]],
            timeline=[_ready(READY_TIME)],
            suites=GREEN,
        ),
        monkeypatch,
    )
    assert (result["rounds"], result["capped"]) == (2, True)
    assert result["review_owed"] is True
    assert result["gate"] == "open", "the gate is not satisfied while a finding is outstanding"
    assert result["amend"] == "added"
    assert triage.AMEND_LABEL in fake.added


def test_a_clean_metered_review_alone_spends_no_round(monkeypatch: pytest.MonkeyPatch) -> None:
    """The narrow statement of the rule, so it is not only observable through the deadlock.

    CodeRabbit's clean form is state `COMMENTED` with a body and no inline findings — measured on
    #385, where the dirty reviews were `COMMENTED` too, so the state does not separate them and
    only the absence of findings does.
    """
    _, result = _run(
        _routes(
            reviews=[_clean_review(RABBIT, HEAD)],
            timeline=[_ready(READY_TIME)],
            suites=GREEN,
        ),
        monkeypatch,
    )
    assert result["rounds"] == 0


def test_a_changes_requested_submission_is_blocking_without_any_inline_finding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A verdict is actionable output whether or not it is spelled out line by line."""
    _, result = _run(
        _routes(
            reviews=[
                dict(_clean_review(RABBIT, HEAD, body="please fix"), state="CHANGES_REQUESTED")
            ],
            timeline=[_ready(READY_TIME)],
            suites=GREEN,
        ),
        monkeypatch,
    )
    assert result["rounds"] == 1


def test_the_gate_blocked_label_is_part_of_the_merged_claims_mirror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It describes work in flight, so a merge clears it like every other mirror label (#308)."""
    assert triage.GATE_BLOCKED_LABEL in triage.MIRROR_LABELS
    fake = _install(monkeypatch, _merged_routes(labels=[triage.GATE_BLOCKED_LABEL]))
    triage.clear_mirror(number=99, dry_run=False)
    assert triage.GATE_BLOCKED_LABEL in fake.removed


# ------------------------------------------- #409: severity read from the provider's own badge
#
# There was no test here at all when this landed. It was validated by driving the real predicate
# over 154 findings from thirteen live pull requests, which proved the regexes parse and proved
# nothing about the FLOOR - and the floor is where it was wrong. Greptile found it on #424: the
# blocking severities were enumerated rather than derived from an ordering, so `P0` - the level
# ABOVE the `P1` floor - was read as below it and dropped from the count. Live replay could not see
# that, because no `P0` finding has ever been posted to this repository.


def _badged(user: str, badge: str, **over: Any) -> dict[str, Any]:
    """One inline finding carrying a provider's real severity markup."""
    body = (
        f"_📐 Maintainability & Code Quality_ | _{badge}_ | _⚡ Quick win_\n\n**Fix the thing.**"
        if user == RABBIT
        else f'<a href="#"><img alt="{badge}" src="https://x/badges/p.svg?v=9" align="top"></a> '
        "**Fix the thing.**"
    )
    return dict(_finding(user, HEAD, REAL_REVIEW_ID, 4242), body=body, **over)


@pytest.mark.parametrize(
    ("user", "badge", "blocking"),
    [
        (RABBIT, "🔴 Critical", True),
        (RABBIT, "🟠 Major", True),
        (RABBIT, "🟡 Minor", False),
        (RABBIT, "🔵 Trivial", False),
        # `P0` is the regression. It is ABOVE the floor `docs/agents/review.md` names, and the first
        # version of this code - a `frozenset({"P1"})` membership test - called it non-blocking.
        (GREPTILE, "P0", True),
        (GREPTILE, "P1", True),
        (GREPTILE, "P2", False),
        (GREPTILE, "P3", False),
    ],
)
def test_the_floor_blocks_at_and_above_it_rather_than_at_it(
    user: str, badge: str, blocking: bool
) -> None:
    """*Floor* means at or above, and enumerating the members only looked like saying so.

    Enumerating was right for CodeRabbit by accident — `Critical` and `Major` happen to be the whole
    of the top of that scale — and wrong for Greptile, whose `P0` sits above the named floor. That
    is the kind of accident a derived value cannot have, so the ordering is written down once and
    the answer is an index comparison.
    """
    assert triage._finding_is_blocking(_badged(user, badge)) is blocking


@pytest.mark.parametrize(
    ("entry", "why"),
    [
        ({"user": {"login": RABBIT}, "body": "no header at all"}, "unparseable markup"),
        ({"user": {"login": GREPTILE}, "body": "no badge at all"}, "unparseable markup"),
        ({"user": {"login": RABBIT}, "body": None}, "a body that is not a string"),
        ({"user": {"login": "someone-new[bot]"}, "body": "_x_ | _🔵 Trivial_ | _y_"}, "a login"),
        ({"user": {}, "body": "_x_ | _🔵 Trivial_ | _y_"}, "no login"),
        # A P-level the scale does not place. It LOOKS less severe than `P3` and might be, but
        # guessing an order for a level nobody wrote down is exactly how `P0` went wrong.
        ({"user": {"login": GREPTILE}, "body": '<img alt="P7" src="x">'}, "an unplaced level"),
    ],
)
def test_evidence_the_scale_cannot_place_still_counts(entry: dict[str, Any], why: str) -> None:
    """The fail direction, which is the property — narrowing applies only to a STATED severity.

    Over-counting caps a pull request early, which is visible and recoverable. Under-counting hands
    out an unbounded metered budget, which is the failure the cap exists to prevent. So every case
    the scale cannot place keeps the answer the counter gave before #409.
    """
    assert triage._finding_is_blocking(entry) is True, f"{why} must still count"


def test_the_domain_label_cannot_promote_a_finding() -> None:
    """The anchoring, and the reason it is the second field rather than a search of the body.

    `docs/agents/review.md` says in as many words that CodeRabbit's *domain* label and its
    `cr-indicator-types:` marker are **not** severities and never promote a finding. A finding whose
    domain field reads `Major Bug Risk` is a `Minor` finding in a domain with `Major` in its name,
    and grepping the body — or reading field one — would spend a round on it.
    """
    entry = dict(
        _finding(RABBIT, HEAD, REAL_REVIEW_ID, 4242),
        body="_🛠️ Major Bug Risk_ | _🟡 Minor_ | _⚡ Quick win_\n\n**Fix the thing.**",
    )
    assert triage._finding_is_blocking(entry) is False, (
        "the domain field carries the word Major; the severity field says Minor, and that is the "
        "one this reads"
    )


def test_a_minor_finding_spends_no_round_but_still_blocks_the_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two axes stay separate, which is what makes narrowing the cap safe (#409).

    The severity floor governs whether a finding must be *fixed*; the gate asks whether there is an
    actionable comment at this head at all. `docs/agents/review.md` states both, and a `Minor`
    inline comment is non-blocking on the first and actionable on the second. Narrowing the round
    counter without checking this would have let a PR merge past an unanswered finding.

    This is #414's own shape: one `Minor`, which under the old rule spent that PR's third round and
    put it at `agent:gate-blocked`.
    """
    _, result = _run(
        _routes(
            reviews=[_submission(RABBIT, HEAD, REAL_REVIEW_ID)],
            comments=[_badged(RABBIT, "🟡 Minor")],
            timeline=[_ready(READY_TIME)],
            suites=GREEN,
        ),
        monkeypatch,
    )
    assert result["rounds"] == 0, "a finding the contract says to DEFER must not spend a round"
    assert result["review_owed"] is True, "it is owed an answer, which is the deferral reply"
    # `result["gate"]` reads `open` here for THREE independent reasons - the finding voids it,
    # something is owed, and the cap is not reached - so asserting on it proves none of them.
    # Measured: mutating the voiding half so an inline finding stops setting `gate_finding_here`
    # left `result["gate"]` at `open` and this test green. `converged` is the half that answers
    # *is there an actionable comment at this head*, so that is what the claim is asserted on.
    _, _, _, converged = triage._review_state(99, HEAD, READY_TIME)
    assert converged is False, (
        "a Minor is non-blocking on the SEVERITY axis and actionable on the GATE axis - "
        "docs/agents/review.md keeps those separate, and so must this"
    )
    assert result["gate"] == "open"
