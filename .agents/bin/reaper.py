#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 The Tether Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reclaim dead agent claims, so a stalled worker cannot strand an issue until a human wakes up.

This is the regression fix for the terminal failure of the 2026-07-26 run: leases expired at 08:03Z,
only the coordinator could renew them, and its own contract forbade renewing an expired lease
without a typed human approval. Nobody was awake, and the run never resumed. A scheduled sweep
answers that by removing the human from the recovery path entirely.

**Staleness is judged from server-recorded time only.** A commit's ``committedDate`` is written by
the client - ``GIT_COMMITTER_DATE`` sets it to any value, so a worker could pin a claim open
forever or backdate one to steal it. The repository activity API stamps ``timestamp`` itself and no
request parameter sets it. The original design sketch for this workflow used ``committedDate``; it
must not come back.

**Discard, don't hand off.** The diff budget bounds an issue to <=400 lines and workers push after
every green gate, so reclaiming loses one increment. The handoff ceremony the retired model used
cost more than the work it protected.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_spec = importlib.util.spec_from_file_location("tether_claim", Path(__file__).with_name("claim.py"))
if _spec is None or _spec.loader is None:  # pragma: no cover - packaging accident
    raise SystemExit("error: claim.py is missing next to reaper.py")
claim = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(claim)

REPO = claim.REPO
BRANCH_PREFIX = claim.BRANCH_PREFIX
NO_PR_MINUTES = 90
STALE_PR_HOURS = 6


class ReaperError(RuntimeError):
    """A sweep precondition failed. Safe to print; carries no path."""


def _now() -> datetime:
    return datetime.now(UTC)


def _parse(stamp: str) -> datetime:
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(UTC)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ReaperError("a server timestamp was not valid ISO-8601") from exc


def _claim_refs() -> list[int]:
    """Every issue number with a live claim ref."""
    status, refs = claim._request("GET", f"/repos/{REPO}/git/matching-refs/heads/{BRANCH_PREFIX}")
    if status == 404:
        return []
    if status != 200 or not isinstance(refs, list):
        raise ReaperError("claim refs could not be listed")
    numbers = []
    for ref in refs:
        name = ref.get("ref", "")
        tail = name.removeprefix(f"refs/heads/{BRANCH_PREFIX}")
        if name.startswith(f"refs/heads/{BRANCH_PREFIX}") and tail.isdigit():
            numbers.append(int(tail))
    return sorted(numbers)


def _last_activity(number: int) -> datetime | None:
    """Newest SERVER-recorded activity timestamp for this claim ref.

    Never a commit date. See the module docstring - client-written dates make a reaper unsound in
    both directions.
    """
    ref = f"refs/heads/{BRANCH_PREFIX}{number}"
    entries = claim._paginate(f"/repos/{REPO}/activity?ref={ref}", "claim activity")
    stamps = [_parse(e["timestamp"]) for e in entries if e.get("timestamp")]
    return max(stamps) if stamps else None


def _open_pr(number: int) -> dict[str, Any] | None:
    """The PR for this claim, or ``None`` only when the API says there genuinely is none.

    Fails closed. A 403/429/500 or a malformed body previously collapsed to ``None``, which the
    sweep reads as "no PR" and therefore as grounds to reclaim - so one transient API error could
    destroy a healthy claim. Not-knowing and knowing-there-is-none must never be the same value.
    """
    branch = f"{BRANCH_PREFIX}{number}"
    status, prs = claim._request("GET", f"/repos/{REPO}/pulls?head=bioedca:{branch}&state=all")
    if status != 200 or not isinstance(prs, list):
        raise ReaperError(f"#{number} pull-request state could not be read (HTTP {status})")
    if not prs:
        return None
    open_prs = [p for p in prs if p.get("state") == "open"]
    return open_prs[0] if open_prs else prs[0]


def _checks_running(sha: str) -> bool:
    """Whether CI is live on this head. Fails closed for the same reason as ``_open_pr``.

    Returning ``False`` on an error would let the sweep close a PR whose checks are still running.
    This needs ``checks: read`` in the workflow - with an explicit ``permissions:`` block every
    unlisted scope is ``none``, so omitting it yields a 403 that used to read as "no checks".
    """
    status, suites = claim._request("GET", f"/repos/{REPO}/commits/{sha}/check-suites")
    if status != 200 or not isinstance(suites, dict):
        raise ReaperError(f"check-suite state could not be read (HTTP {status})")
    entries = suites.get("check_suites")
    if not isinstance(entries, list):
        raise ReaperError("check-suite response was malformed")
    return any(s.get("status") in {"queued", "in_progress"} for s in entries)


def _requeue(number: int, *, dry_run: bool) -> None:
    """Delete the claim ref and put the issue back on the queue. Idempotent."""
    if dry_run:
        return
    claim._request("DELETE", f"/repos/{REPO}/git/refs/heads/{BRANCH_PREFIX}{number}")
    for vendor in claim.VENDORS:
        claim._request("DELETE", f"/repos/{REPO}/issues/{number}/labels/agent:{vendor}", None)
    claim._request("DELETE", f"/repos/{REPO}/issues/{number}/labels/status:in-progress", None)
    # Only re-open the issue's queue slot if it is still open; a merged issue must stay done.
    status, issue = claim._request("GET", f"/repos/{REPO}/issues/{number}")
    if status == 200 and isinstance(issue, dict) and issue.get("state") == "open":
        claim._request(
            "POST", f"/repos/{REPO}/issues/{number}/labels", {"labels": [claim.REQUIRED_LABEL]}
        )


def sweep(*, dry_run: bool) -> list[dict[str, Any]]:
    now = _now()
    actions: list[dict[str, Any]] = []
    for number in _claim_refs():
        pr = _open_pr(number)
        last = _last_activity(number)
        age_min = (now - last).total_seconds() / 60 if last else None

        if pr is None or pr.get("state") != "open":
            if age_min is not None and age_min < NO_PR_MINUTES:
                actions.append({"issue": number, "action": "keep", "reason": "recent-activity"})
                continue
            _requeue(number, dry_run=dry_run)
            actions.append({"issue": number, "action": "requeue", "reason": "no-open-pr"})
            continue

        if pr.get("mergeable_state") == "dirty" or pr.get("mergeStateStatus") == "DIRTY":
            if not dry_run:
                claim._request(
                    "POST",
                    f"/repos/{REPO}/issues/{number}/labels",
                    {"labels": ["agent:conflicted"]},
                )
            actions.append({"issue": number, "action": "flag-conflicted", "reason": "dirty"})
            continue

        pr_age_h = (now - _parse(pr["updated_at"])).total_seconds() / 3600
        if pr_age_h >= STALE_PR_HOURS and not _checks_running(pr["head"]["sha"]):
            if not dry_run:
                claim._request("PATCH", f"/repos/{REPO}/pulls/{pr['number']}", {"state": "closed"})
            _requeue(number, dry_run=dry_run)
            actions.append(
                {"issue": number, "action": "requeue", "reason": "stale-pr", "pr": pr["number"]}
            )
            continue

        actions.append({"issue": number, "action": "keep", "reason": "pr-active"})
    return actions


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Reclaim dead agent claims.")
    parser.add_argument("--dry-run", action="store_true", help="report without mutating anything")
    args = parser.parse_args()
    try:
        actions = sweep(dry_run=args.dry_run)
    except (ReaperError, claim.ClaimError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (ValueError, OverflowError, RecursionError, AttributeError, KeyError, TypeError):
        print("error: input exceeds safe processing limits", file=sys.stderr)
        return 2
    except OSError:
        print("error: operating-system I/O failure", file=sys.stderr)
        return 2
    # Nothing to do is success, not failure: this runs on a schedule and must not page anyone.
    print(json.dumps({"version": 1, "dry_run": args.dry_run, "actions": actions}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
