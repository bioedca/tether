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
from pathlib import Path
from typing import Any

import pytest

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
    yaml = pytest.importorskip("yaml")
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
    concurrency = _workflow()["concurrency"]
    assert concurrency["cancel-in-progress"] is False
    # Per pull request: a global group would serialise every PR behind one.
    assert "github.event.pull_request.number" in concurrency["group"]


def test_the_workflow_listens_for_the_three_state_changing_events() -> None:
    # `on` is parsed by PyYAML's 1.1 rules as the boolean True, not the string "on".
    triggers = _workflow()[True]
    assert triggers["check_suite"]["types"] == ["completed"]
    assert set(triggers) >= {"check_suite", "pull_request_review", "pull_request_review_comment"}


def test_the_workflow_records_the_bot_trigger_probe_answer() -> None:
    """The probe decides how strong this control is, so its state is written down, not implied.

    It cannot be run from the branch that introduces the workflow - `check_suite` and
    `workflow_dispatch` both need the file on the default branch - so the honest record is that it
    is unanswered and that the workflow therefore posts no triggers.
    """
    header = WORKFLOW.read_text(encoding="utf-8")
    assert "BOT-TRIGGER PROBE" in header
    assert "NOT ANSWERED" in header
    assert "@codex review" in header


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
