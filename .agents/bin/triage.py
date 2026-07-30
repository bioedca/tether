#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 The Tether Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Publish the review-round state the two-round cap depends on.

``AGENTS.md`` §Review gate says the cap binds because the launcher is the only issuer of AMEND turns
and the launcher counts. This is what it counts. Without it the cap is prose, which is exactly what
failed on #276: **9 external rounds against a limit of 2**, every request author-posted.

The mechanism is one label and one counter:

* ``agent:needs-amend`` on the linked issue means *this PR owes one AMEND session*. It is the
  launcher's authority to start one.
* ``agent:round-1`` → ``agent:round-2`` → ``agent:review-capped``. **At the cap this module stops
  emitting ``agent:needs-amend``**, so no worker is ever handed the authority for a third round.
  Nothing needs to refuse a request that was never authorised.

**State is recomputed, not accumulated.** Every run reads the PR's current review and check state
and writes the labels that state implies, so a missed webhook, a re-run and a manual dispatch all
converge on the same answer. Reacting to event payloads instead would make the counter a function
of delivery luck.

**A round is a distinct head SHA at which an external provider reported.** Not a review count: a
``high`` PR routes to *two* providers answering as *one* round (``AGENTS.md`` §Review gate), so
counting submissions would cap every high-risk PR after its first pass. Grouping by head SHA gives
both providers one round and gives a second pass its own.

**What this deliberately cannot see, stated rather than implied.** Only *head-bound* evidence
counts — review submissions and inline review comments, both of which carry ``commit_id``. A
provider answering in a plain issue comment carries no head binding and is not counted; that
happened on #282, where CodeRabbit's substantive review arrived as an issue comment while Codex's
arrived as a review. So this counter can **undercount** and can never overcount, and undercounting
is the fail-open direction. Two compensating controls exist and neither is optional: the launcher
refuses to inject an AMEND block past the cap whatever the labels say, and a post-merge audit reads
the real round count. Do not promote this to a required check on the counter alone.

Two writers share ``agent:needs-amend``: this module and ``reaper.py``'s stale-PR path. They agree
on its meaning, and the split of authority between them is deliberate:

* At the cap this module **does not add** the label. It also does **not remove** one that is already
  there. Removing it would be the tempting extra belt, and it is wrong: the reaper applies the same
  label to a *stale* PR for reasons that have nothing to do with rounds, so removing it here would
  make two workflows fight over one label and erase the only signal that a claim needs a person.
  The launcher reads ``agent:review-capped`` first and refuses regardless.
* The label is removed only when a check suite has just completed green at the current head with no
  outstanding findings. That is an activity signal as much as a state one, which is why it does not
  step on the reaper's stale marker: a stale PR produces no check events at all.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

_spec = importlib.util.spec_from_file_location("tether_claim", Path(__file__).with_name("claim.py"))
if _spec is None or _spec.loader is None:  # pragma: no cover - packaging accident
    raise SystemExit("error: claim.py is missing next to triage.py")
claim = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(claim)

REPO = claim.REPO
BRANCH_PREFIX = claim.BRANCH_PREFIX
BRANCH_RE = re.compile(r"^" + re.escape(BRANCH_PREFIX) + r"(\d+)$")

# Copilot is deliberately absent: AGENTS.md makes it optional and says its absence or quota never
# blocks, so a Copilot pass must not consume a round the contract did not grant.
EXTERNAL_PROVIDERS = frozenset({"chatgpt-codex-connector[bot]", "coderabbitai[bot]"})

CAP = 2
ROUND_LABELS = ("agent:round-1", "agent:round-2")
CAPPED_LABEL = "agent:review-capped"
AMEND_LABEL = "agent:needs-amend"
ALL_ROUND_LABELS = (*ROUND_LABELS, CAPPED_LABEL)

FAILED_CONCLUSIONS = frozenset({"failure", "timed_out", "action_required", "startup_failure"})
LIVE_STATUSES = frozenset({"queued", "in_progress", "pending", "requested", "waiting"})


class TriageError(RuntimeError):
    """A triage precondition failed. Safe to print; carries no path."""


def _pull_request(*, number: int | None, branch: str | None) -> dict[str, Any] | None:
    """The open PR this event concerns, or ``None`` when there is none to triage.

    Fails closed on an unreadable answer, for the same reason ``reaper._open_pr`` does: "I could not
    read the PR" and "there is no PR" must never be the same value, or one transient 502 silently
    publishes a wrong round count.
    """
    if number is not None:
        status, pr = claim._request("GET", f"/repos/{REPO}/pulls/{number}")
        if status == 404:
            return None
        if status != 200 or not isinstance(pr, dict):
            raise TriageError(f"PR #{number} could not be read (HTTP {status})")
        return pr

    if not branch:
        raise TriageError("one of --pr or --branch is required")
    try:
        prs = claim._paginate(f"/repos/{REPO}/pulls?head=bioedca:{branch}&state=open", "PR list")
    except claim.ClaimError as exc:
        raise TriageError("pull-request state could not be read") from exc
    if not prs:
        return None
    # A branch has at most one open PR; take the lowest number for determinism if that ever changes.
    summary = min(prs, key=lambda p: int(p["number"]))
    status, full = claim._request("GET", f"/repos/{REPO}/pulls/{summary['number']}")
    if status != 200 or not isinstance(full, dict):
        raise TriageError(f"PR #{summary['number']} could not be read (HTTP {status})")
    return full


def _linked_issue(pr: dict[str, Any]) -> int | None:
    """The issue number, taken from the head branch rather than from ``Closes #N`` prose.

    The claim ref *is* ``agent/issue-<N>``, so the branch name is the link, and the one piece of
    metadata that cannot drift from the mutex that created it. ``reaper.py`` reads the same mapping
    in the other direction. A head that does not match is not agent-claimed work: no labels.
    """
    match = BRANCH_RE.match(str((pr.get("head") or {}).get("ref") or ""))
    return int(match.group(1)) if match else None


def _issue_labels(number: int) -> set[str] | None:
    """Current labels, or ``None`` when the issue is not open. Fails closed on a read error."""
    status, issue = claim._request("GET", f"/repos/{REPO}/issues/{number}")
    if status != 200 or not isinstance(issue, dict):
        raise TriageError(f"#{number} could not be read (HTTP {status})")
    if issue.get("state") != "open":
        return None
    return {label["name"] for label in issue.get("labels", []) if isinstance(label, dict)}


def _reviewed_heads(pr_number: int) -> set[str]:
    """Every head SHA at which an external provider left head-bound review evidence.

    Both sources carry ``commit_id``: submitted reviews and inline review comments. Inline comments
    are included because a provider can post findings with no submission wrapper, and missing one
    of those would undercount a round that really happened.
    """
    heads: set[str] = set()
    for path, what in (
        (f"/repos/{REPO}/pulls/{pr_number}/reviews", "review list"),
        (f"/repos/{REPO}/pulls/{pr_number}/comments", "review-comment list"),
    ):
        try:
            entries = claim._paginate(path, what)
        except claim.ClaimError as exc:
            raise TriageError(f"PR #{pr_number} review state could not be read") from exc
        for entry in entries:
            login = ((entry.get("user") or {}).get("login")) or ""
            sha = entry.get("commit_id")
            if login in EXTERNAL_PROVIDERS and isinstance(sha, str) and sha:
                heads.add(sha)
    return heads


def _suite_state(sha: str) -> tuple[bool, bool]:
    """``(running, failed)`` for the check suites on this head. Fails closed on an unreadable read.

    Mirrors ``reaper._checks_running`` including its truncation guard: this endpoint wraps its list
    in an object so ``_paginate`` does not apply, and a silently short read would report a failing
    head as clean.
    """
    path = f"/repos/{REPO}/commits/{sha}/check-suites?per_page=100"
    status, payload = claim._request("GET", path)
    if status != 200 or not isinstance(payload, dict):
        raise TriageError(f"check-suite state could not be read (HTTP {status})")
    suites = payload.get("check_suites")
    if not isinstance(suites, list):
        raise TriageError("check-suite response was malformed")
    total = payload.get("total_count")
    if isinstance(total, int) and total > len(suites):
        raise TriageError(
            f"check-suite list truncated ({len(suites)} of {total}); refusing to judge CI state"
        )
    # A suite with no checks reports conclusion `null` forever; that is neither running nor failed.
    running = any(s.get("status") in LIVE_STATUSES for s in suites)
    failed = any(s.get("conclusion") in FAILED_CONCLUSIONS for s in suites)
    return running, failed


def _round_label(rounds: int) -> str | None:
    if rounds <= 0:
        return None
    return CAPPED_LABEL if rounds >= CAP else ROUND_LABELS[rounds - 1]


def _apply(number: int, add: list[str], remove: list[str], *, dry_run: bool) -> None:
    """Write the label delta. Adds are fatal on failure; removals are best-effort.

    An add that silently fails publishes a state that is not true — and for ``agent:review-capped``
    that means a third round looks authorised. A removal that fails leaves a stale marker, which is
    cosmetic against the same standard, and the next event recomputes it anyway.
    """
    if dry_run:
        return
    for label in remove:
        claim._request("DELETE", f"/repos/{REPO}/issues/{number}/labels/{label}", None)
    if add:
        status, _ = claim._request("POST", f"/repos/{REPO}/issues/{number}/labels", {"labels": add})
        if status != 200:
            raise TriageError(
                f"#{number} needs {', '.join(add)} but the write failed (HTTP {status}); "
                "not reporting state that was never published"
            )


def triage(*, number: int | None, branch: str | None, dry_run: bool) -> dict[str, Any]:
    pr = _pull_request(number=number, branch=branch)
    if pr is None:
        return {"action": "skip", "reason": "no-open-pull-request"}
    if pr.get("state") != "open":
        return {"action": "skip", "reason": "pull-request-not-open", "pr": pr["number"]}

    issue = _linked_issue(pr)
    if issue is None:
        # Not agent-claimed work. Maintainer-authored branches keep type/issue-N-slug names and must
        # be left completely alone: labelling them would move a human's issue through an agent state
        # machine that nothing is driving.
        return {"action": "skip", "reason": "not-agent-claimed", "pr": pr["number"]}

    labels = _issue_labels(issue)
    if labels is None:
        return {"action": "skip", "reason": "issue-not-open", "pr": pr["number"], "issue": issue}

    head = str((pr.get("head") or {}).get("sha") or "")
    if not head:
        raise TriageError(f"PR #{pr['number']} has no head sha")

    rounds = len(_reviewed_heads(pr["number"]))
    running, failed = _suite_state(head)
    capped = rounds >= CAP

    add: list[str] = []
    remove: list[str] = []

    # Round labels are mutually exclusive and only ever escalate. Monotonic on purpose: heads cannot
    # be rewritten here (the ruleset forbids force-push and non-fast-forward), so a count that fell
    # would mean a read failed - and stepping a PR back from capped to round-1 would re-authorise a
    # round the contract already spent.
    target = _round_label(rounds)
    highest_held = max(
        (ALL_ROUND_LABELS.index(name) for name in ALL_ROUND_LABELS if name in labels), default=-1
    )
    if target is not None and ALL_ROUND_LABELS.index(target) > highest_held:
        add.append(target)
        remove += [n for n in ALL_ROUND_LABELS if n in labels and n != target]

    # The whole mechanism: past the cap no AMEND authority is issued, so no third round can start.
    # An existing marker is left alone - see the module docstring on the two writers.
    if capped:
        amend = "withheld-at-cap"
    elif failed and AMEND_LABEL not in labels:
        add.append(AMEND_LABEL)
        amend = "added"
    elif not failed and not running and AMEND_LABEL in labels:
        remove.append(AMEND_LABEL)
        amend = "cleared"
    else:
        amend = "unchanged"

    _apply(issue, add, remove, dry_run=dry_run)
    return {
        "action": "triage",
        "pr": pr["number"],
        "issue": issue,
        "head": head,
        "rounds": rounds,
        "capped": capped,
        "checks": "running" if running else ("failed" if failed else "green"),
        "amend": amend,
        "added": add,
        "removed": remove,
    }


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Publish review-round state for the cap.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--pr", type=int, help="pull-request number")
    source.add_argument("--branch", help="head branch name, e.g. agent/issue-42")
    parser.add_argument("--dry-run", action="store_true", help="report without mutating anything")
    args = parser.parse_args()
    try:
        result = triage(number=args.pr, branch=args.branch, dry_run=args.dry_run)
    except (TriageError, claim.ClaimError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (ValueError, OverflowError, RecursionError, AttributeError, KeyError, TypeError):
        print("error: input exceeds safe processing limits", file=sys.stderr)
        return 2
    except OSError:
        print("error: operating-system I/O failure", file=sys.stderr)
        return 2
    print(json.dumps({"version": 1, "dry_run": args.dry_run, **result}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
