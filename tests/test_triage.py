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


def _install(
    monkeypatch: pytest.MonkeyPatch, routes: dict[tuple[str, str], tuple[int, Any]]
) -> Fake:
    fake = Fake(routes)
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
    return {"user": {"login": user}, "commit_id": sha}


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
    }


def _run(routes: Routes, monkeypatch: pytest.MonkeyPatch) -> tuple[Fake, dict[str, Any]]:
    fake = _install(monkeypatch, routes)
    return fake, triage.triage(number=99, branch=None, dry_run=False)


# --------------------------------------------------------------------------- the cap


def test_no_amend_authority_is_issued_once_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    """THE cap. Two rounds spent and CI red - the one state that would otherwise owe an AMEND.

    `agent:needs-amend` is the launcher's authority to start a session, so withholding it makes a
    third round impossible rather than forbidden. #276 reached 9 rounds against a limit of 2
    because a prose rule was the only thing holding it.
    """
    fake, result = _run(
        _routes(reviews=[_review(CODEX, OLDER), _review(RABBIT, HEAD)], suites=RED),
        monkeypatch,
    )
    assert result["rounds"] == 2
    assert result["capped"] is True
    assert result["amend"] == "withheld-at-cap"
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
        _routes(comments=[_review(CODEX, OLDER)], reviews=[_review(RABBIT, HEAD)], suites=GREEN),
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
    """Monotonic on purpose: stepping back from capped would re-authorise a spent round."""
    fake, result = _run(
        _routes(labels=[triage.CAPPED_LABEL], reviews=[_review(CODEX, HEAD)], suites=GREEN),
        monkeypatch,
    )
    assert result["rounds"] == 1
    # round-1 is BELOW the capped label already held, so nothing is written and nothing removed.
    assert fake.added == []
    assert triage.CAPPED_LABEL not in fake.removed


def test_reaching_the_cap_replaces_the_earlier_round_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round labels are mutually exclusive; a PR must not read as round-1 and capped at once."""
    fake, _ = _run(
        _routes(
            labels=["agent:round-1"],
            reviews=[_review(CODEX, OLDER), _review(RABBIT, HEAD)],
            suites=GREEN,
        ),
        monkeypatch,
    )
    assert triage.CAPPED_LABEL in fake.added
    assert "agent:round-1" in fake.removed


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
    _, result = _run(_routes(comments=[_review(CODEX, OLDER)], suites=GREEN), monkeypatch)
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
            reviews=[_review(CODEX, OLDER), _review(RABBIT, HEAD)],
            suites=GREEN,
        ),
        monkeypatch,
    )
    assert result["amend"] == "withheld-at-cap"
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
        reviews=[_review(CODEX, OLDER), _review(RABBIT, HEAD)],
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
            reviews=[_review(CODEX, OLDER)],
            comments=[_carried(CODEX, OLDER, HEAD)],
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
            reviews=[_review(CODEX, OLDER)],
            comments=[_carried(CODEX, HEAD, HEAD)],
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

    _, result = _run(_routes(reviews=[_review(CODEX, HEAD)], suites=RED), monkeypatch)
    assert result["rounds"] == 1


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
    "agent:round-1",
    "type:bug",
    "size:S",
]


def _merged_routes(
    *,
    merged: bool = True,
    labels: list[str] | None = None,
    head_ref: str = "agent/issue-7",
    issue_state: str = "closed",
) -> Routes:
    return {
        ("GET", "/repos/bioedca/tether/pulls/99"): (
            200,
            {"number": 99, "state": "closed", "merged": merged, "head": {"ref": head_ref}},
        ),
        ("GET", "/repos/bioedca/tether/issues/7"): (
            200,
            {
                "state": issue_state,
                "labels": [{"name": n} for n in (labels if labels is not None else MERGE_MIRROR)],
            },
        ),
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


def test_the_workflow_listens_for_the_merge_event() -> None:
    triggers = _workflow()[True]
    assert triggers["pull_request"]["types"] == ["closed"]


def test_the_workflow_passes_merged_only_on_the_pull_request_event() -> None:
    """The flag is set from the event; whether it MERGED is re-checked from the API in triage.py."""
    step = next(
        s
        for s in _workflow()["jobs"]["triage"]["steps"]
        if s.get("name") == "Publish review-round state"
    )
    assert step["env"]["MERGED"] == "${{ github.event_name == 'pull_request' }}"
    assert "--merged" in step["run"]
