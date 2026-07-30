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

    def __call__(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        self.calls.append((method, path))
        best: tuple[int, tuple[int, Any]] | None = None
        for (m, prefix), response in self.routes.items():
            if m == method and path.startswith(prefix) and (best is None or len(prefix) > best[0]):
                best = (len(prefix), response)
        return best[1] if best is not None else self.DEFAULTS.get(method, (200, None))

    def posted_refs(self) -> list[str]:
        return [p for m, p in self.calls if m == "POST" and p.endswith("/git/refs")]


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
) -> Fake:
    """Wire the fake transport and the claim-ref list.

    `slots.claim` IS `slots.reaper.claim` - the module is loaded once and shared on purpose - so
    this single patch covers every call either half makes. Loading claim.py twice would leave
    one transport live and make these tests pass for the wrong reason.
    """
    assert slots.claim is slots.reaper.claim, "one claim module, one patch point"

    routes: dict[tuple[str, str], tuple[int, Any]] = {
        ("GET", "/repos/bioedca/tether/issues?state=open"): (200, ready or []),
        ("GET", "/repos/bioedca/tether/git/ref/heads/main"): (200, {"object": {"sha": HEAD}}),
        ("POST", "/repos/bioedca/tether/git/refs"): (ref_status, {}),
        ("GET", "/repos/bioedca/tether/activity"): (
            200,
            [{"id": 77, "activity_type": "branch_creation"}],
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


@pytest.mark.parametrize(
    ("label", "spent", "remaining"),
    [(None, 0, 2), ("agent:round-1", 1, 1)],
    ids=["ci-only", "round-1"],
)
def test_the_injected_round_matches_the_published_label(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    label: str | None,
    spent: int,
    remaining: int,
) -> None:
    """The round is READ from triage's labels, never recounted here.

    A launcher that re-derived the count could disagree with the state the contract publishes, and
    then the two halves of one cap would be arguing. The `ci-only` case is a real state: a red check
    suite owes an AMEND before any external review has been spent at all.
    """
    names = [slots.AMEND_LABEL] + ([label] if label else [])
    _install(monkeypatch, claimed=[7], issues={7: _issue(7, *names)})
    report = _run(monkeypatch, tmp_path)
    entry = _by_issue(report, 7)
    assert entry["mode"] == "amend"
    assert (entry["round"], entry["remaining"]) == (spent, remaining)
    text = (tmp_path / entry["task_file"]).read_text(encoding="utf-8")
    assert f"round {spent} of {slots.CAP}" in text
    assert "{{" not in text, "an unsubstituted placeholder would be read as literal instruction"
    assert "SPDX" not in text, "the maintainer comment block must not reach the worker's context"


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
    command = _by_issue(_run(monkeypatch, tmp_path, vendor="claude"), 9)["command"]
    assert command.startswith("wsl -e bash -lc ")
    assert "/mnt/c/" in command
    assert "claude -p" in command


def test_the_codex_lane_starts_the_native_shim(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`codex` is a native PowerShell shim, so it must not be routed through WSL."""
    ready = [_issue(9, "status:ready")]
    _install(monkeypatch, ready=ready, issues={9: ready[0]})
    command = _by_issue(_run(monkeypatch, tmp_path, vendor="codex"), 9)["command"]
    assert "Start-Process" in command
    assert "codex.ps1" in command
    assert "wsl" not in command


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


def test_an_unknown_placeholder_is_refused_not_shipped(tmp_path: Path) -> None:
    """Fail loudly rather than inject a template the launcher does not fully understand."""
    bad = tmp_path / "bad.md"
    bad.write_text("Do the thing on {{MYSTERY}}.\n", encoding="utf-8")
    record = {"issue": 7, "branch": "b", "generation": 1, "base_sha": None}
    with pytest.raises(slots.SlotError, match="unsubstituted placeholder"):
        slots._render(bad, record, {"vendor": "claude"})


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
    assert ".agents/tasks/_task-issue-*.md" in patterns


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
