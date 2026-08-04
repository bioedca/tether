# SPDX-FileCopyrightText: 2026 The Tether Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Contract tests for the slot launcher.

Every GitHub call goes through a fake transport. The load-bearing test is
`test_the_launcher_refuses_to_issue_a_third_round`: that refusal is the second of the two
controls the review cap rests on, and `AGENTS.md` requires it be proven by a test, not prose.

The first control is `triage.py` withholding `agent:needs-amend` at the cap. This one has to
hold even when that label is WRONG, because the triage counter can undercount - so the capped
tests deliberately present `agent:needs-amend` alongside `agent:review-capped`, which is
exactly the inconsistent state an undercount would produce.
"""

from __future__ import annotations

import importlib.util
import inspect
import json
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / ".agents" / "bin"
SCRIPT = BIN / "swarm_slots.py"
GATE = BIN / "gate.ps1"
TASKS = ROOT / ".agents" / "tasks"

_spec = importlib.util.spec_from_file_location("tether_slots", SCRIPT)
assert _spec is not None and _spec.loader is not None
slots = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(slots)

HEAD = "f" * 40
DIGEST = "a" * 64
MARKER = '<!-- tether-agent-ready {"version":1,"criteria_sha256":"' + DIGEST + '"} -->'


class Fake:
    """Answers by (method, path-prefix), longest prefix first, and records every request."""

    DEFAULTS = {"DELETE": (204, None), "POST": (200, None), "PATCH": (200, None)}

    def __init__(self, routes: dict[tuple[str, str], tuple[int, Any]]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str]] = []
        # The ref NAME lives in the request body, and the name is the record: which ledger a round
        # was taken in is written into it (#391), so a test that could only see the path could not
        # tell a draft session from a counted one.
        self.created_refs: list[str] = []

    def __call__(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        self.calls.append((method, path))
        if method == "POST" and path.endswith("/git/refs") and isinstance(body, dict):
            self.created_refs.append(str(body.get("ref", "")))
        best: tuple[int, tuple[int, Any]] | None = None
        for (m, prefix), response in self.routes.items():
            if m == method and path.startswith(prefix) and (best is None or len(prefix) > best[0]):
                best = (len(prefix), response)
        return best[1] if best is not None else self.DEFAULTS.get(method, (200, None))

    def posted_refs(self) -> list[str]:
        return [p for m, p in self.calls if m == "POST" and p.endswith("/git/refs")]

    def amend_refs(self) -> list[str]:
        return [r for r in self.created_refs if f"refs/{slots.AMEND_NAMESPACE}/" in r]


def _issue(number: int, *labels: str, state: str = "open") -> dict[str, Any]:
    return {
        "number": number,
        "state": state,
        "title": f"feat(io): thing {number}",
        "body": "Acceptance criteria\n",
        "labels": [{"name": name} for name in labels],
        "assignees": [],
    }


def _install(
    monkeypatch: pytest.MonkeyPatch,
    *,
    ready: list[dict[str, Any]] | None = None,
    claimed: list[int] | None = None,
    issues: dict[int, dict[str, Any]] | None = None,
    ref_status: int = 201,
    issued_refs: list[dict[str, Any]] | None = None,
    advance_refs: list[dict[str, Any]] | None = None,
) -> Fake:
    """Wire the fake transport and the claim-ref list.

    `slots.claim` IS `slots.reaper.claim` - the module is loaded once and shared on purpose - so
    this single patch covers every call either half makes. Loading claim.py twice would leave
    one transport live and make these tests pass for the wrong reason.
    """
    assert slots.claim is slots.reaper.claim, "one claim module, one patch point"
    assert slots.claim is slots.triage.claim, "triage too, since #391 calls into it"

    routes: dict[tuple[str, str], tuple[int, Any]] = {
        ("GET", "/repos/bioedca/tether/issues?state=open"): (200, ready or []),
        ("GET", "/repos/bioedca/tether/git/ref/heads/main"): (200, {"object": {"sha": HEAD}}),
        ("POST", "/repos/bioedca/tether/git/refs"): (ref_status, {}),
        ("GET", "/repos/bioedca/tether/activity"): (
            200,
            [{"id": 77, "activity_type": "branch_creation"}],
        ),
        ("GET", "/repos/bioedca/tether/git/ref/heads/agent/issue-"): (
            200,
            {"object": {"sha": HEAD}},
        ),
        ("GET", f"/repos/bioedca/tether/git/matching-refs/{slots.AMEND_NAMESPACE}/"): (
            200,
            issued_refs or [],
        ),
        ("GET", f"/repos/bioedca/tether/git/matching-refs/{slots.ADVANCE_NAMESPACE}/"): (
            200,
            advance_refs or [],
        ),
    }
    for number, issue in (issues or {}).items():
        routes[("GET", f"/repos/bioedca/tether/issues/{number}/comments")] = (
            200,
            [{"user": {"login": "bioedca"}, "body": f"Approved.\n\n{MARKER}"}],
        )
        routes[("GET", f"/repos/bioedca/tether/issues/{number}")] = (200, issue)

    fake = Fake(routes)
    monkeypatch.setattr(slots.claim, "_request", fake)
    monkeypatch.setattr(slots.claim, "_scope_hash", lambda title, body: DIGEST)

    def paginate(path: str, what: str) -> list[Any]:
        status, payload = fake("GET", path)
        if status != 200 or not isinstance(payload, list):
            raise slots.claim.ClaimError(f"{what} could not be read")
        return payload

    monkeypatch.setattr(slots.claim, "_paginate", paginate)
    monkeypatch.setattr(slots.reaper, "_claim_refs", lambda: list(claimed or []))
    return fake


def _run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **kw: Any) -> dict[str, Any]:
    # Copy the real templates so rendering is exercised against the committed text, while the
    # generated per-issue task files land in tmp_path instead of the working tree.
    for name in ("build.md", "amend.md"):
        (tmp_path / name).write_text((TASKS / name).read_text(encoding="utf-8"), encoding="utf-8")
    return slots.run(
        slots=kw.pop("slot_count", 2),
        vendor=kw.pop("vendor", "claude"),
        owner="bioedca",
        spawn=kw.pop("spawn", False),
        tasks=tmp_path,
    )


def _by_issue(report: dict[str, Any], number: int) -> dict[str, Any]:
    return next(r for r in report["results"] if r["issue"] == number)


# ------------------------------------------------------------------------------ the cap


def test_the_launcher_refuses_to_issue_a_third_round(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """THE acceptance criterion of this change, and it must hold against a WRONG label.

    `agent:needs-amend` is presented alongside `agent:review-capped` on purpose: the triage counter
    can undercount, so the launcher must refuse on the cap rather than trust the amend flag. Two
    independent refusals is the design; this is the second one.
    """
    issue = _issue(7, slots.CAPPED_LABEL, slots.AMEND_LABEL, "priority:P0")
    _install(monkeypatch, claimed=[7], issues={7: issue})
    report = _run(monkeypatch, tmp_path)
    entry = _by_issue(report, 7)
    assert entry["mode"] == "refuse"
    assert entry["launched"] is False
    assert "cap" in entry["reason"] and "maintainer" in entry["reason"]
    assert "command" not in entry, "a refusal must not produce something runnable"
    assert not list(tmp_path.glob("_task-issue-*.md")), "no AMEND text may be written at the cap"


def test_a_refusal_is_reported_never_silently_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A launcher that quietly passed over a capped PR looks identical to one with no work."""
    _install(monkeypatch, claimed=[7], issues={7: _issue(7, slots.CAPPED_LABEL, slots.AMEND_LABEL)})
    report = _run(monkeypatch, tmp_path)
    assert [r["mode"] for r in report["results"]] == ["refuse"]
    assert report["cap"] == slots.CAP


def _ref(number: int, generation: int, ordinal: int) -> dict[str, Any]:
    return {"ref": f"refs/{slots.AMEND_NAMESPACE}/{number}-{generation}-{ordinal}"}


@pytest.mark.parametrize(
    ("already", "expect_round", "expect_remaining"),
    [(0, 1, 1), (1, 2, 0)],
    ids=["first-session", "second-session"],
)
def test_the_injected_round_comes_from_the_launchers_own_issuance_count(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    already: int,
    expect_round: int,
    expect_remaining: int,
) -> None:
    """The round is the launcher's OWN count, not triage's label.

    The first version read `_rounds_spent` from the labels and called that an independent refusal;
    it was the same undercountable number read twice, so an undercount passed both checks. The count
    now comes from `refs/amend-rounds/<issue>-<generation>-<n>`, which the launcher creates itself.

    Note the label here says ZERO rounds while the refs say otherwise - deliberately, so the test
    fails if the implementation drifts back to trusting the label.

    """
    refs = [_ref(7, 77, n + 1) for n in range(already)]
    _install(monkeypatch, claimed=[7], issues={7: _issue(7, slots.AMEND_LABEL)}, issued_refs=refs)
    entry = _by_issue(_run(monkeypatch, tmp_path), 7)
    assert entry["mode"] == "amend"
    assert (entry["round"], entry["remaining"]) == (expect_round, expect_remaining)
    text = (tmp_path / entry["task_file"]).read_text(encoding="utf-8")
    assert f"round {expect_round} of {slots.CAP}" in text
    assert "{{" not in text, "an unsubstituted placeholder would be read as literal instruction"
    assert "SPDX" not in text, "the maintainer comment block must not reach the worker's context"


def test_the_launcher_refuses_on_its_own_count_even_when_the_label_says_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The independent refusal, and the whole point of the ref counter.

    `agent:needs-amend` present, NO round label at all, and two AMEND sessions already issued. If
    the launcher trusted the labels it would happily start a third. This is the exact undercount
    Codex identified on #287.

    """
    refs = [_ref(7, 77, 1), _ref(7, 77, 2)]
    _install(monkeypatch, claimed=[7], issues={7: _issue(7, slots.AMEND_LABEL)}, issued_refs=refs)
    entry = _by_issue(_run(monkeypatch, tmp_path), 7)
    assert entry["mode"] == "refuse"
    assert "already issued" in entry["reason"]
    assert not list(tmp_path.glob("_task-issue-*"))


# --------------------------------------------------- the draft phase is uncapped here too (#391)
#
# ADR-0062 makes the draft phase uncapped: Codex is the unmetered lane and iterates until nothing
# blocking remains. `triage.py` counts accordingly. This launcher keeps a SECOND, independent cap in
# permanent generation refs - deliberately, so either counter can bind - and it had no notion of
# draft state, so two draft iterations exhausted it while triage correctly reported zero rounds and
# the advertised unlimited loop stalled here with no label saying why.


def _draft_ref(number: int, generation: int, ordinal: int) -> dict[str, Any]:
    return {
        "ref": f"refs/{slots.AMEND_NAMESPACE}/{number}-{generation}-"
        f"{slots.DRAFT_ORDINAL_PREFIX}{ordinal}"
    }


def _as_draft(fake: Fake, *, draft: bool) -> None:
    """Point the claim's branch at a pull request in one phase or the other.

    `_in_draft_phase` goes through `triage._counted_from`, which reads the TIMELINE and not the
    `draft` flag alone - so a draft here means no `ready_for_review` has ever happened, which is
    what makes entering the counted phase permanent.
    """
    fake.routes[("GET", "/repos/bioedca/tether/pulls?head=bioedca:agent/issue-7")] = (
        200,
        [{"number": 99}],
    )
    fake.routes[("GET", "/repos/bioedca/tether/pulls/99")] = (
        200,
        {"number": 99, "draft": draft, "head": {"sha": HEAD}},
    )
    fake.routes[("GET", "/repos/bioedca/tether/issues/99/timeline")] = (
        200,
        [] if draft else [{"event": "ready_for_review", "created_at": "2026-08-01T12:00:00Z"}],
    )
    # The advance ref is keyed to the lane STEP, which triage derives from the provider evidence at
    # the head, so the reviews list has to be answerable or the token falls back to the phase alone.
    fake.routes[("GET", "/repos/bioedca/tether/pulls/99/reviews")] = (
        200,
        [{"user": {"login": "chatgpt-codex-connector[bot]"}, "commit_id": HEAD}],
    )


def test_a_draft_claim_can_be_issued_more_amends_than_the_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """#391's first criterion, and the one the old counter made impossible.

    `CAP` draft sessions are already on record. Under the old ledger that is the cap and the
    launcher refuses; the uncapped Codex loop the lane advertises could not complete, because the
    BUILD worker exits after requesting its review and `amend.md` forbids a worker requesting the
    next one.
    """
    refs = [_draft_ref(7, 77, n + 1) for n in range(slots.CAP)]
    fake = _install(
        monkeypatch, claimed=[7], issues={7: _issue(7, slots.AMEND_LABEL)}, issued_refs=refs
    )
    _as_draft(fake, draft=True)

    entry = _by_issue(_run(monkeypatch, tmp_path), 7)
    assert entry["mode"] == "amend", entry.get("reason")
    assert entry["phase"] == "draft"
    assert fake.amend_refs() == [
        f"refs/{slots.AMEND_NAMESPACE}/7-77-{slots.DRAFT_ORDINAL_PREFIX}{slots.CAP + 1}"
    ]


def test_the_counted_phase_still_refuses_at_the_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """#391's second criterion. The exemption is the phase, not the mechanism.

    Same claim, same generation, `CAP` refs — but taken after `ready_for_review`, so they are in the
    counted ledger and the refusal is exactly as it was.
    """
    refs = [_ref(7, 77, n + 1) for n in range(slots.CAP)]
    fake = _install(
        monkeypatch, claimed=[7], issues={7: _issue(7, slots.AMEND_LABEL)}, issued_refs=refs
    )
    _as_draft(fake, draft=False)

    entry = _by_issue(_run(monkeypatch, tmp_path), 7)
    assert entry["mode"] == "refuse"
    assert "already issued" in entry["reason"]


def test_draft_sessions_do_not_discount_the_counted_ledger(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The two ledgers are separate in BOTH directions.

    Five draft sessions and one counted one: the counted count must read 1, so the next session is
    round 2 of 2 — not round 6, and not refused. A single ordinal sequence shared between the phases
    would let free draft iteration consume the metered budget, which is the defect inverted.
    """
    refs = [_draft_ref(7, 77, n + 1) for n in range(5)] + [_ref(7, 77, 1)]
    fake = _install(
        monkeypatch, claimed=[7], issues={7: _issue(7, slots.AMEND_LABEL)}, issued_refs=refs
    )
    _as_draft(fake, draft=False)

    entry = _by_issue(_run(monkeypatch, tmp_path), 7)
    assert entry["mode"] == "amend"
    assert (entry["round"], entry["remaining"]) == (2, 0)


def test_an_unreadable_pull_request_charges_the_counted_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """#391 changes a safety control, so the unknown case must fall on the strict side.

    The uncapped phase has to be positively established. Reading "I cannot tell" as draft would
    hand out an unbounded issuance budget on any transient failure — the one outcome this second
    counter exists to prevent.
    """
    refs = [_ref(7, 77, n + 1) for n in range(slots.CAP)]
    fake = _install(
        monkeypatch, claimed=[7], issues={7: _issue(7, slots.AMEND_LABEL)}, issued_refs=refs
    )
    fake.routes[("GET", "/repos/bioedca/tether/pulls?head=bioedca:agent/issue-7")] = (502, None)

    entry = _by_issue(_run(monkeypatch, tmp_path), 7)
    assert entry["mode"] == "refuse"


def test_the_launcher_shares_one_transport_with_everything_it_calls() -> None:
    """Two `claim` module objects is two things to authenticate and two things to patch.

    `_load` builds a fresh module, so `triage` arrives holding its own. That was harmless while
    triage was only *read* for constants; #391 calls `_in_draft_phase` into it, and a second,
    unpatched transport would make these tests reach the network — or, worse, pass while only half
    of the calls were faked. The same reasoning the file already applies to `reaper`.
    """
    assert slots.claim is slots.reaper.claim is slots.triage.claim


def test_the_phase_comes_from_the_same_predicate_the_round_labels_do() -> None:
    """#391's last criterion: `agent:review-capped` and this refusal cannot disagree about phase.

    Asserted as identity, not as behaviour. Two implementations that happen to agree today are
    exactly what the criterion forbids, and `_counted_from` keys on the FIRST `ready_for_review`,
    so a worker cannot refund a spent round by toggling back to draft.
    """
    source = inspect.getsource(slots._in_draft_phase)
    assert "triage._counted_from" in source and "triage._COUNT_NOTHING" in source, (
        "the launcher must read triage's phase predicate rather than re-deriving `draft`"
    )


def test_the_draft_ledger_is_still_keyed_to_the_claim_generation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A reaped-and-reclaimed issue starts fresh in both ledgers, as it always did.

    The draft refs of generation 76 must not be visible to generation 77, or a reclaim would inherit
    a phase history that belongs to a worker the reaper already removed.
    """
    refs = [_draft_ref(7, 76, n + 1) for n in range(3)]
    fake = _install(
        monkeypatch, claimed=[7], issues={7: _issue(7, slots.AMEND_LABEL)}, issued_refs=refs
    )
    _as_draft(fake, draft=True)

    _by_issue(_run(monkeypatch, tmp_path), 7)
    assert fake.amend_refs() == [
        f"refs/{slots.AMEND_NAMESPACE}/7-77-{slots.DRAFT_ORDINAL_PREFIX}1"
    ], "generation 77's draft ledger must start at 1 regardless of generation 76's"


# ------------------------------------------- the lane advance is not a review round (#394)


def test_an_advance_label_launches_the_advance_task_not_the_amend_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """#394's second criterion: the session must be told which phase it is in.

    Handing this claim `amend.md` would tell a worker to fix the blocking findings — and there are
    none, which is the precondition of the whole state. It would then either invent work or stop.
    """
    fake = _install(monkeypatch, claimed=[7], issues={7: _issue(7, slots.ADVANCE_LABEL)})
    _as_draft(fake, draft=True)
    for name in ("build.md", "amend.md", "advance.md"):
        (tmp_path / name).write_text((TASKS / name).read_text(encoding="utf-8"), encoding="utf-8")

    report = slots.run(slots=2, vendor="claude", owner="bioedca", spawn=False, tasks=tmp_path)
    entry = _by_issue(report, 7)
    assert entry["mode"] == "advance"
    text = (tmp_path / entry["task_file"]).read_text(encoding="utf-8")
    assert "ADVANCE" in text and "AMEND —" not in text
    assert "{{" not in text


def test_an_advance_takes_a_ref_outside_the_round_ledger(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """#394's third criterion: advancing consumes no review round.

    The ref is still taken — it is what makes the authority exactly one session — but in
    `refs/lane-advances/`. In `refs/amend-rounds/` it would spend one of the two metered rounds to
    move a pull request from one phase to the next.
    """
    fake = _install(monkeypatch, claimed=[7], issues={7: _issue(7, slots.ADVANCE_LABEL)})
    _as_draft(fake, draft=True)
    for name in ("build.md", "amend.md", "advance.md"):
        (tmp_path / name).write_text((TASKS / name).read_text(encoding="utf-8"), encoding="utf-8")

    slots.run(slots=2, vendor="claude", owner="bioedca", spawn=False, tasks=tmp_path)
    assert fake.amend_refs() == [], "an advance must not touch the round ledger"
    # Keyed to the head and the phase, not to a running ordinal: an ordinal only deduplicates
    # launchers racing on the same COUNT, so one that had already taken `-1` while the label was
    # still published would create `-2` and start a second session against the same reviewed head.
    assert fake.created_refs == [f"refs/{slots.ADVANCE_NAMESPACE}/7-77-{HEAD[:12]}-draft-1"]


def test_losing_the_advance_race_launches_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`exactly one session` is the criterion, and a label alone cannot meet it.

    A label is state: it stays published until triage recomputes, so every launcher run in between
    would start another session against the same phase — several workers all spending the Greptile
    credit, or all marking the PR ready. `422` is how the loser finds out.
    """
    fake = _install(
        monkeypatch,
        claimed=[7],
        issues={7: _issue(7, slots.ADVANCE_LABEL)},
        ref_status=422,
    )
    _as_draft(fake, draft=True)
    for name in ("build.md", "amend.md", "advance.md"):
        (tmp_path / name).write_text((TASKS / name).read_text(encoding="utf-8"), encoding="utf-8")

    report = slots.run(slots=2, vendor="claude", owner="bioedca", spawn=False, tasks=tmp_path)
    entry = _by_issue(report, 7)
    assert entry["mode"] == "lost"
    assert entry["launched"] is False
    assert "already advanced" in entry["reason"]
    assert not list(tmp_path.glob("_task-issue-*"))


def test_an_amend_wins_over_an_advance_when_both_are_somehow_published(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Answering a finding always precedes moving the lane on.

    `triage.py` does not publish both, so this is a belt on a state that should not occur — but if
    it does, the safe reading is the one that fixes something rather than the one that advances past
    it.
    """
    _install(
        monkeypatch,
        claimed=[7],
        issues={7: _issue(7, slots.ADVANCE_LABEL, slots.AMEND_LABEL)},
    )
    for name in ("build.md", "amend.md", "advance.md"):
        (tmp_path / name).write_text((TASKS / name).read_text(encoding="utf-8"), encoding="utf-8")

    entry = _by_issue(
        slots.run(slots=2, vendor="claude", owner="bioedca", spawn=False, tasks=tmp_path), 7
    )
    assert entry["mode"] == "amend"


def test_the_advance_template_tells_a_worker_it_is_not_an_amend() -> None:
    """The stop-list #394 asks for, asserted on the shipped text rather than on the launcher.

    Advancing more than one phase is the failure this template exists to prevent: each step begins
    only when the previous has nothing blocking left, and a worker cannot know that about a review
    it has just requested.
    """
    # Whitespace-normalised: these are prose files, so an assertion on an exact string breaks the
    # first time a sentence rewraps, which teaches the next author to rewrite the test rather than
    # to keep the promise.
    text = " ".join((TASKS / "advance.md").read_text(encoding="utf-8").split())
    assert "NOT AN AMEND" in text.upper()
    assert "Do not walk more than one phase" in text
    assert "Exhaustion never blocks" in text, "a Greptile balance of zero must not stall the lane"


def test_losing_the_amend_round_race_launches_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Two launchers on one `agent:needs-amend` issue must not both start a worker.

    Without the compare-and-swap both would reuse the existing claim and run two sessions on one
    branch, generation and worktree. 422 means the other launcher took this round.
    """
    _install(
        monkeypatch,
        claimed=[7],
        issues={7: _issue(7, slots.AMEND_LABEL)},
        ref_status=422,
    )
    entry = _by_issue(_run(monkeypatch, tmp_path), 7)
    assert entry["mode"] == "lost"
    assert entry["launched"] is False
    assert not list(tmp_path.glob("_task-issue-*"))


def test_the_amend_ref_is_keyed_to_the_claim_generation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A reaped-and-reclaimed issue must start fresh rather than inherit a spent cap.

    The generation changes on every reclaim, so keying the refs to it means the count belongs to
    *this* claim. It also means the refs need never be deleted, which is what keeps the counter
    monotonic.

    """
    fake = _install(monkeypatch, claimed=[7], issues={7: _issue(7, slots.AMEND_LABEL)})
    _run(monkeypatch, tmp_path)
    reads = [p for m, p in fake.calls if m == "GET" and slots.AMEND_NAMESPACE in p]
    assert reads, "the issuance count must actually be read"
    assert all("7-77-" in p for p in reads), "keyed to issue AND generation"


def test_an_unreadable_issuance_count_never_authorises_an_amend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Reading a failure as zero would hand out a session at the cap - the one thing to avoid."""
    fake = _install(monkeypatch, claimed=[7], issues={7: _issue(7, slots.AMEND_LABEL)})
    prefix = f"/repos/bioedca/tether/git/matching-refs/{slots.AMEND_NAMESPACE}/"
    fake.routes[("GET", prefix)] = (502, None)
    with pytest.raises(slots.SlotError, match="refusing to guess"):
        _run(monkeypatch, tmp_path)


def test_a_hand_applied_round_2_label_is_treated_as_the_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`agent:round-2` means both rounds are spent, which IS the cap - so it refuses.

    Recording the asymmetry that this test exposed, because it is easy to misread: under `CAP = 2`
    `triage._round_label` publishes `agent:round-1` at one round and `agent:review-capped` at
    two, so **`agent:round-2` is never published by code**. It is provisioned on the repository
    and reachable only by hand.

    `_rounds_spent` still honours it rather than ignoring it, because a maintainer applying it
    by hand plainly means "two rounds are gone" - and reading that as zero would hand out a
    fresh session at the cap. Whether the label should exist at all under a two-round cap is
    tracked separately; the safe reading is pinned here either way.
    """
    _install(monkeypatch, claimed=[7], issues={7: _issue(7, "agent:round-2", slots.AMEND_LABEL)})
    entry = _by_issue(_run(monkeypatch, tmp_path), 7)
    assert entry["mode"] == "refuse"
    assert entry["launched"] is False
    assert not list(tmp_path.glob("_task-issue-*.md"))


def test_a_live_claim_without_the_amend_label_is_left_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No published authority, no session. A worker may simply still be running."""
    _install(monkeypatch, claimed=[7], issues={7: _issue(7, "agent:round-1")})
    report = _run(monkeypatch, tmp_path)
    assert report["results"] == []


def test_rounds_spent_reads_the_cap_from_the_capped_label() -> None:
    assert slots._rounds_spent({slots.CAPPED_LABEL}) == slots.CAP
    assert slots._rounds_spent({"agent:round-1"}) == 1
    assert slots._rounds_spent(set()) == 0


def test_the_launcher_and_the_triage_share_one_vocabulary() -> None:
    """Two halves of one cap must not disagree about which labels or what limit they mean."""
    assert slots.CAP == slots.triage.CAP
    assert slots.AMEND_LABEL == slots.triage.AMEND_LABEL
    assert slots.CAPPED_LABEL == slots.triage.CAPPED_LABEL
    assert slots.ROUND_LABELS == slots.triage.ROUND_LABELS


# ------------------------------------------------------------------------------- claiming


def test_a_build_takes_the_claim_through_claim_py_itself(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Eligibility is not reimplemented: the launcher calls the same command an agent would run."""
    fake = _install(
        monkeypatch, ready=[_issue(9, "status:ready")], issues={9: _issue(9, "status:ready")}
    )
    report = _run(monkeypatch, tmp_path)
    entry = _by_issue(report, 9)
    assert entry["mode"] == "build" and entry["launched"] is True
    assert entry["branch"] == "agent/issue-9"
    assert entry["generation"] == 77, "the server-recorded generation, not a local guess"
    assert fake.posted_refs(), "the claim ref must actually be created"


def test_losing_the_claim_race_produces_no_launch_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """422 means another agent holds it. Losing costs one API call; it is not an error."""
    _install(
        monkeypatch,
        ready=[_issue(9, "status:ready")],
        issues={9: _issue(9, "status:ready")},
        ref_status=422,
    )
    report = _run(monkeypatch, tmp_path)
    entry = _by_issue(report, 9)
    assert entry["launched"] is False
    assert "command" not in entry
    assert not list(tmp_path.glob("_task-issue-*.md"))


def test_an_unapproved_issue_is_never_launched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Eligibility is a PRECONDITION of the claim (ADR-0057): a missing approval stops it."""
    fake = _install(
        monkeypatch, ready=[_issue(9, "status:ready")], issues={9: _issue(9, "status:ready")}
    )
    fake.routes[("GET", "/repos/bioedca/tether/issues/9/comments")] = (200, [])
    report = _run(monkeypatch, tmp_path)
    assert _by_issue(report, 9)["launched"] is False
    assert not fake.posted_refs(), "no ref may be created for unapproved scope"


@pytest.mark.parametrize(
    "label",
    ["agent:human", "needs:split", "blocked-by:maintainer", "blocked-by:external"],
    ids=["human", "split", "blocked-maintainer", "blocked-external"],
)
def test_an_excluded_label_keeps_an_issue_out_of_the_queue(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, label: str
) -> None:
    """`agent:human` and `needs:split` are refusals, and `blocked-by:*` says why it is blocked."""
    ready = [_issue(9, "status:ready", label)]
    _install(monkeypatch, ready=ready, issues={9: ready[0]})
    assert _run(monkeypatch, tmp_path)["results"] == []


def test_an_already_claimed_issue_is_not_built_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One work item, one owner, one branch, one PR. A second build would void that."""
    ready = [_issue(7, "status:ready")]
    _install(monkeypatch, ready=ready, claimed=[7], issues={7: _issue(7, "status:ready")})
    report = _run(monkeypatch, tmp_path)
    assert [r["mode"] for r in report["results"]] == [], "claimed and no amend owed: nothing to do"


# ------------------------------------------------------------------------------- ordering


def test_amend_comes_before_build_and_refusals_before_both(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Finishing an open PR beats starting a new one; a stalled PR is what the reaper cleans up."""
    ready = [_issue(9, "status:ready", "priority:P0")]
    issues = {
        7: _issue(7, slots.AMEND_LABEL, "agent:round-1", "priority:P2"),
        8: _issue(8, slots.CAPPED_LABEL, "priority:P2"),
        9: ready[0],
    }
    _install(monkeypatch, ready=ready, claimed=[7, 8], issues=issues)
    report = _run(monkeypatch, tmp_path, slot_count=2)
    assert [r["mode"] for r in report["results"]] == ["refuse", "amend", "build"]


def test_priority_orders_the_build_queue(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ready = [
        _issue(11, "status:ready", "priority:P2"),
        _issue(12, "status:ready", "priority:P0"),
        _issue(13, "status:ready", "priority:P1"),
    ]
    _install(monkeypatch, ready=ready, issues={i["number"]: i for i in ready})
    report = _run(monkeypatch, tmp_path, slot_count=3)
    assert [r["issue"] for r in report["results"]] == [12, 13, 11]


def test_only_launches_consume_a_slot(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A capped PR must not eat the slot a workable issue could have used."""
    ready = [_issue(11, "status:ready"), _issue(12, "status:ready")]
    issues = {8: _issue(8, slots.CAPPED_LABEL), 11: ready[0], 12: ready[1]}
    _install(monkeypatch, ready=ready, claimed=[8], issues=issues)
    report = _run(monkeypatch, tmp_path, slot_count=1)
    modes = [r["mode"] for r in report["results"]]
    assert modes == ["refuse", "build"], "the refusal is reported AND the single slot is still used"


# ---------------------------------------------------------------------- launch mechanics


def test_the_claude_lane_goes_through_wsl(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`claude` exists only inside WSL on this machine; it is not on the native Windows PATH."""
    ready = [_issue(9, "status:ready")]
    _install(monkeypatch, ready=ready, issues={9: ready[0]})
    entry = _by_issue(_run(monkeypatch, tmp_path, vendor="claude"), 9)
    script = (tmp_path / entry["wrapper"]).read_text(encoding="utf-8")
    assert "wsl -e bash -lc " in script
    assert "/mnt/c/" in script
    assert "claude -p" in script


def test_the_codex_lane_runs_the_native_shim(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`codex` is a native PowerShell shim, so it must not be routed through WSL."""
    ready = [_issue(9, "status:ready")]
    _install(monkeypatch, ready=ready, issues={9: ready[0]})
    entry = _by_issue(_run(monkeypatch, tmp_path, vendor="codex"), 9)
    script = (tmp_path / entry["wrapper"]).read_text(encoding="utf-8")
    assert "codex.ps1" in script
    assert "wsl" not in script


def test_every_worker_is_wrapped_in_the_admission_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The gate must wrap the WORKER, not the launcher, or it protects nothing.

    The first version built `gate.ps1` and never invoked it - a repo-wide search for `-Acquire`
    found only docs and tests, so every worker bypassed both the holder ceiling and the RAM floor
    (Codex,
    #287). The launcher cannot hold a slot itself: it exits before the worker starts.

    """
    ready = [_issue(9, "status:ready")]
    _install(monkeypatch, ready=ready, issues={9: ready[0]})
    entry = _by_issue(_run(monkeypatch, tmp_path), 9)
    script = (tmp_path / entry["wrapper"]).read_text(encoding="utf-8")
    assert "-Acquire" in script
    assert "-Release" in script
    # The release must be in a `finally`, or a crashed worker leaks its slot until PID liveness
    # reclaims it - which works, but much later than it needs to.
    assert "finally {" in script
    # The acquire failure must abort rather than run the worker anyway.
    assert "if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }" in script
    assert entry["command"].startswith("powershell -NoProfile")


def test_the_gate_wrapper_aborts_before_starting_a_worker_it_cannot_admit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The exit codes have to survive into the wrapper, not just exist in the gate.

    Exit 4 (no slot) and 5 (below the RAM floor) must reach the caller unchanged so a retry loop can
    tell "wait" from "this machine is too small right now".
    """
    ready = [_issue(9, "status:ready")]
    _install(monkeypatch, ready=ready, issues={9: ready[0]})
    entry = _by_issue(_run(monkeypatch, tmp_path), 9)
    script = (tmp_path / entry["wrapper"]).read_text(encoding="utf-8")
    acquire = script.index("-Acquire")
    guard = script.index("if ($LASTEXITCODE -ne 0)")
    worker = script.index("try {")
    assert acquire < guard < worker, "the guard must sit between admission and the worker"


def test_spawn_is_interlocked_until_the_unknowns_are_probed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Shipping an unproven spawn would produce empty sessions that look like workers.

    Two things are unmeasured: headless `claude -p` auth in a non-interactive WSL shell, and /mnt/c
    worktree performance. Printing keeps both off the critical path.
    """
    monkeypatch.delenv("TETHER_SPAWN_OK", raising=False)
    ready = [_issue(9, "status:ready")]
    _install(monkeypatch, ready=ready, issues={9: ready[0]})
    with pytest.raises(slots.SlotError, match="TETHER_SPAWN_OK"):
        _run(monkeypatch, tmp_path, spawn=True)


def test_printing_is_the_default_and_starts_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    started: list[str] = []
    monkeypatch.setattr(slots.subprocess, "Popen", lambda *a, **k: started.append(str(a)))
    ready = [_issue(9, "status:ready")]
    _install(monkeypatch, ready=ready, issues={9: ready[0]})
    report = _run(monkeypatch, tmp_path)
    assert _by_issue(report, 9)["spawned"] is False
    assert started == []


# --------------------------------------------------------------------------- task templates


@pytest.mark.parametrize("name", ["build.md", "amend.md"])
def test_every_template_placeholder_is_substituted(name: str) -> None:
    """An unsubstituted `{{TOKEN}}` reaches the worker as a literal instruction."""
    record = {"issue": 7, "branch": "agent/issue-7", "generation": 5, "base_sha": "abc"}
    item = {"vendor": "claude", "round": 1, "remaining": 1, "reason": "because"}
    text = slots._render(TASKS / name, record, item)
    assert "{{" not in text and "}}" not in text


def test_an_unregistered_lane_is_refused_rather_than_given_someone_elses_interpreter() -> None:
    """The lookup that decides `{{PYTHON}}` must not answer for a lane it does not know (#387).

    Reachable only in-process — argparse pins `--vendor` to `claim.VENDORS` — which is exactly why
    it is asserted here: `_dispatch_build` and these tests build an `item` by hand, and the two
    shapes this guard has already worn were both silent-ish. `LANE_PYTHON[vendor]` raised a bare
    `KeyError` that escapes `main`'s `SlotError` handler mid-render; `LANE_PYTHON.get(vendor,
    DEFAULT_PYTHON)` replaced it with something worse, a task rendered against an interpreter the
    lane's own shell may not resolve, discovered only when the worker ran it.
    """
    record = {"issue": 7, "branch": "agent/issue-7", "generation": 5, "base_sha": "abc"}
    item = {"vendor": "gemini", "round": 1, "remaining": 1, "reason": "because"}
    with pytest.raises(slots.SlotError) as raised:
        slots._render(TASKS / "amend.md", record, item)
    message = str(raised.value)
    assert "gemini" in message, "the refusal must name the lane it could not resolve"
    assert all(vendor in message for vendor in slots.claim.VENDORS), (
        f"the refusal must name the accepted set; got {message!r}"
    )


def test_an_unknown_placeholder_is_refused_not_shipped(tmp_path: Path) -> None:
    """Fail loudly rather than inject a template the launcher does not fully understand."""
    bad = tmp_path / "bad.md"
    bad.write_text("Do the thing on {{MYSTERY}}.\n", encoding="utf-8")
    record = {"issue": 7, "branch": "b", "generation": 1, "base_sha": None}
    with pytest.raises(slots.SlotError, match="unsubstituted placeholder"):
        slots._render(bad, record, {"vendor": "claude"})


def test_a_malformed_template_is_caught_before_any_state_is_consumed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A template defect is a property of the file, so nothing may be spent discovering it.

    Raising from `_render` was too late by construction: `run` has already called `_dispatch_build`,
    creating the claim ref, or `_authorise_amend`, creating the permanent round ref. The first
    strands a claim until the reaper; the second irrevocably consumes one of the claim generation's
    two rounds and launches no worker with it. Neither unwinds.

    So `run` validates every template it may use up front, and the assertion here is about what did
    NOT happen: no claim ref created, no round issued.
    """
    ready = [_issue(9, "status:ready")]
    fake = _install(monkeypatch, ready=ready, issues={9: ready[0]})
    for name in ("build.md", "amend.md"):
        (tmp_path / name).write_text("<!-- never closed\nDo the thing.\n", encoding="utf-8")

    with pytest.raises(slots.SlotError, match="never closed"):
        slots.run(slots=2, vendor="claude", owner="bioedca", spawn=False, tasks=tmp_path)

    mutations = [c for c in fake.calls if c[0] in {"POST", "PATCH", "DELETE"}]
    assert mutations == [], (
        f"nothing may be consumed before the template is known good: {mutations}"
    )


def test_an_unregistered_lane_is_caught_before_any_state_is_consumed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The interpreter refusal must be side-effect free, for the template argument's exact reason.

    Codex's P2 on the first draft of #387: raising from `_render` is too late by construction.
    `run` has already called `_dispatch_build`, creating the claim ref, or `_authorise_amend`,
    burning a permanent round ref — so the refusal strands a claim until the reaper, or spends one
    of the two rounds irrevocably, and launches nothing with it. Neither unwinds, and `AGENTS.md`
    says to release a claim rather than abandon it.

    The lane's interpreter is a property of the argument alone, with nothing to read, so `run`
    resolves it before it plans. What this asserts is what did NOT happen.
    """
    ready = [_issue(9, "status:ready")]
    fake = _install(monkeypatch, ready=ready, issues={9: ready[0]})
    for name in ("build.md", "amend.md"):
        (tmp_path / name).write_text("Do the thing on {{ISSUE}}.\n", encoding="utf-8")

    with pytest.raises(slots.SlotError, match="gemini"):
        slots.run(slots=2, vendor="gemini", owner="bioedca", spawn=False, tasks=tmp_path)

    mutations = [c for c in fake.calls if c[0] in {"POST", "PATCH", "DELETE"}]
    assert mutations == [], (
        f"nothing may be consumed before the lane is known dispatchable: {mutations}"
    )


def test_an_unclosed_comment_block_is_refused_not_silently_emptied(tmp_path: Path) -> None:
    """`str.partition` answers ("<!-- ...", "", "") when the separator is absent.

    So tolerating a missing `-->` handed the worker an EMPTY task, which then passed the
    unsubstituted-placeholder guard because nothing was left to be unsubstituted. Silent, and not
    free: the claim is already taken by this point, and for an AMEND a round has already been spent
    on the compare-and-swap ref. A worker would start with no instructions and its issue would have
    one fewer round available.
    """
    bad = tmp_path / "bad.md"
    bad.write_text("<!-- a maintainer note that never closes\nDo the thing.\n", encoding="utf-8")
    record = {"issue": 7, "branch": "b", "generation": 1, "base_sha": None}
    with pytest.raises(slots.SlotError, match="never closed"):
        slots._render(bad, record, {"vendor": "claude"})


def test_a_template_that_renders_to_nothing_is_refused(tmp_path: Path) -> None:
    """The placeholder guard cannot catch this: an empty task has no placeholder left in it.

    A well-formed comment that happens to be the whole file reaches the same end state as the
    unclosed one above by a different route, so the emptiness is checked directly rather than only
    the delimiter that usually causes it.
    """
    empty = tmp_path / "empty.md"
    empty.write_text("<!-- only a note -->\n\n", encoding="utf-8")
    record = {"issue": 7, "branch": "b", "generation": 1, "base_sha": None}
    with pytest.raises(slots.SlotError, match="renders to nothing"):
        slots._render(empty, record, {"vendor": "claude"})

    # A third route, which `_body` cannot see because it happens during substitution: a template
    # that is nothing but tokens, all of which resolve to empty. The placeholder guard misses it
    # too, there being no placeholder left.
    tokens = tmp_path / "tokens.md"
    tokens.write_text("{{REASON}}\n", encoding="utf-8")
    with pytest.raises(slots.SlotError, match="renders to nothing"):
        slots._render(tokens, record, {"vendor": "claude", "reason": ""})


def test_the_amend_template_states_that_the_launcher_refuses_past_the_cap() -> None:
    """The template must not grow its own "if capped" branch.

    Its existence in a session already means the round was authorised, so a conditional there would
    be a second, weaker copy of the control - and the kind that drifts.
    """
    text = (TASKS / "amend.md").read_text(encoding="utf-8")
    assert "REFUSES TO INJECT THIS FILE" in text
    assert "Do not request another review round" in text


def test_both_templates_forbid_self_issued_rounds() -> None:
    """The stop-list line has to reach the worker, not just live in AGENTS.md."""
    # Split so `reuse lint` does not read this assertion as a SECOND license declaration for THIS
    # file - it scans for the literal tag anywhere and reported `GPL-3.0-or-later" in text` as an
    # invalid expression.
    tag = "SPDX-License-" + "Identifier: GPL-3.0-or-later"
    for name in ("build.md", "amend.md"):
        text = (TASKS / name).read_text(encoding="utf-8")
        assert tag in text
        assert "review round" in text


# ----------------------------------------------------------------------------------- gate


def test_stale_slot_reclamation_never_deletes_the_file() -> None:
    """The race both reviewers found: delete-then-recreate can admit two holders to one slot.

    Two acquirers can each observe the SAME dead pid; the loser then deletes the winner's freshly
    written slot and recreates it for itself, so both report success. Reproduced against the pre-fix
    code - one trial admitted **three** holders to a one-slot gate - and the fix is an exclusive
    `FileShare.None` handle held across the read-and-overwrite, so the file is never removed and
    there is nothing for a successor to lose.

    Asserted structurally because the live interleaving proof needs concurrent Windows processes,
    which CI cannot run; the measurements are recorded on the pull request.

    """
    text = GATE.read_text(encoding="utf-8")
    after_catch = text.split("catch [System.IO.IOException]", 1)[1]
    reclaim = after_catch.split("Write-Output $slot.Path", 1)[0]
    assert "Remove-Item" not in reclaim, "reclamation must not delete a slot a successor may own"
    assert "[System.IO.FileMode]::Open" in reclaim
    assert "[System.IO.FileShare]::None" in reclaim
    assert "SetLength(0)" in reclaim, "overwrite in place, under the exclusive handle"


def test_the_gate_is_not_a_semaphore_and_says_why() -> None:
    """A named Semaphore leaks its count when a holder is OOM-killed, then deadlocks forever.

    That is the fail-closed-with-only-human-escapes trap ADR-0057 exists to remove, so the reasoning
    is recorded in the script rather than left to be rediscovered.
    """
    text = GATE.read_text(encoding="utf-8")
    assert "NOT a named Semaphore" in text
    assert "PID-liveness" in text or "PID liveness" in text
    assert "$PID" in text


def test_the_gate_documents_its_exit_codes_and_the_ram_floor() -> None:
    """The codes are the interface: "wait and retry" must be distinguishable from "too small"."""
    text = GATE.read_text(encoding="utf-8")
    assert "exit 4" in text and "exit 5" in text
    assert "MinFreeMb" in text
    assert "[Console]::Error.WriteLine" in text
    # `Write-Error` THROWS under $ErrorActionPreference = 'Stop', so an `exit 5` after one never
    # runs and the caller sees a generic terminating error instead of the documented code.
    # Verified live: the first version of this script did exactly that.
    #
    # Statements only - comments are stripped first. The header explains the rule and would
    # otherwise match the text it warns against, a mistake this suite has already made once.
    statements = [
        line.split("#", 1)[0]
        for line in text.splitlines()
        if not line.strip().startswith("#") and not line.strip().startswith(".")
    ]
    assert not [s for s in statements if "Write-Error" in s]


def test_the_generated_state_is_gitignored() -> None:
    """Both new writers put files under TRACKED directories, so this is not incidental.

    `.claude/` stopped being a blanket ignore in #280, which is exactly why these need naming: the
    gate's slot files are `.pid` (so the pre-existing `*.lock` rule misses them), and the launcher
    renders per-issue task text into `.agents/tasks/`, beside the committed templates.
    """
    patterns = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".claude/gate-slots/" in patterns
    assert ".agents/tasks/_task-issue-*" in patterns


def test_the_gate_records_that_its_floor_is_provisional() -> None:
    """No measured peak for a Tether GUI suite exists, so the floor must not claim to be one."""
    text = GATE.read_text(encoding="utf-8")
    assert "PROVISIONAL" in text
    assert "16,073 MB total" in text, "the measurement it does rest on, stated"


# ------------------------------------------------------------------------------------ cli


def test_the_cli_reports_json_and_a_named_empty_queue_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """A caller loop must tell "nothing to do" from "something broke" without parsing prose."""
    _install(monkeypatch)
    monkeypatch.setattr("sys.argv", ["swarm_slots.py", "--tasks", str(tmp_path)])
    assert slots.main() == slots.EXIT_NO_WORK
    assert json.loads(capsys.readouterr().out)["results"] == []


def test_an_unreadable_queue_is_an_error_not_an_empty_queue(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Not-knowing and knowing-there-is-nothing must never be the same value."""
    fake = _install(monkeypatch)
    fake.routes[("GET", "/repos/bioedca/tether/issues?state=open")] = (502, None)
    monkeypatch.setattr("sys.argv", ["swarm_slots.py", "--tasks", str(tmp_path)])
    assert slots.main() == 2
    assert "error:" in capsys.readouterr().err
