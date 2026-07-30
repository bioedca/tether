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

**A claim with an open pull request is never reclaimed here** (issue #277). Only the no-PR case is
reclaimed; a stale PR is labelled ``agent:needs-amend`` and otherwise left exactly as it is - PR
open, claim ref intact, labels untouched.

The reason is an asymmetry, not squeamishness. An abandoned **PR is visible** in the pull-request
list; an abandoned **claim ref is silent**, and silent state is what caused the freeze this workflow
exists to prevent. Unattended recovery is worth its complexity for the invisible case and is not for
the visible one. Closing a PR additionally required three mutations with no transaction - close,
move labels, delete the ref - and ``PATCH /pulls/{n}`` accepts no expected-head, ``DELETE
/git/refs`` no expected-SHA, and label writes no precondition, so a push landing between the final
revalidation and the close closed a PR that had just come back to life with nothing to reverse it.
Seven review rounds narrowed that window and none closed it; the fix was to stop needing it.

Nothing is stranded by that choice: the claim survives, so the launcher's AMEND path continues the
existing PR on the existing claim rather than discarding an increment.
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
    # Paginated: reading one page means abandoned claims beyond it are NEVER reaped, because every
    # scheduled run would receive the same first page. That silently defeats the whole workflow on a
    # busy repo, and it is the third unpaginated-read defect in this change - so every list read
    # here is now either paginated or truncation-checked.
    try:
        refs = claim._paginate(
            f"/repos/{REPO}/git/matching-refs/heads/{BRANCH_PREFIX}", "claim refs"
        )
    except claim.ClaimError as exc:
        # matching-refs 404s when nothing matches, which is an empty sweep rather than a failure.
        status, _ = claim._request("GET", f"/repos/{REPO}/git/matching-refs/heads/{BRANCH_PREFIX}")
        if status == 404:
            return []
        raise ReaperError("claim refs could not be listed") from exc
    numbers = []
    for ref in refs:
        name = ref.get("ref", "")
        tail = name.removeprefix(f"refs/heads/{BRANCH_PREFIX}")
        if name.startswith(f"refs/heads/{BRANCH_PREFIX}") and tail.isdigit():
            numbers.append(int(tail))
    return sorted(numbers)


def _fingerprint(number: int) -> tuple[int, str] | None:
    """A server-recorded snapshot of this claim ref's activity: (newest id, newest timestamp).

    The decision to reclaim is made from a snapshot and acted on later. `concurrency` serializes
    reaper runs against each other but not against workers, so anything can change in between - and
    a push does not open a PR, so checking only for a PR misses it. Re-reading this immediately
    before a destructive call and requiring it to be identical closes that window generally, rather
    than patching each place it shows up.
    """
    ref = f"refs/heads/{BRANCH_PREFIX}{number}"
    entries = claim._paginate(f"/repos/{REPO}/activity?ref={ref}", "claim activity")
    ids = [(int(e["id"]), str(e.get("timestamp", ""))) for e in entries if e.get("id") is not None]
    return max(ids) if ids else None


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
    try:
        prs = claim._paginate(f"/repos/{REPO}/pulls?head=bioedca:{branch}&state=all", "PR list")
    except claim.ClaimError as exc:
        raise ReaperError(f"#{number} pull-request state could not be read") from exc
    if not prs:
        return None
    open_prs = [p for p in prs if p.get("state") == "open"]
    summary = open_prs[0] if open_prs else prs[0]

    # The LIST representation omits mergeable/mergeable_state entirely - verified against the API,
    # not assumed. Deciding "not conflicted" from it makes that test ALWAYS false, so a conflicted
    # PR would fall through to the stale path and be closed and requeued instead of flagged. Only
    # /pulls/{number} populates them.
    status, full = claim._request("GET", f"/repos/{REPO}/pulls/{summary['number']}")
    if status != 200 or not isinstance(full, dict):
        raise ReaperError(f"PR #{summary['number']} state could not be read (HTTP {status})")
    return full


def _checks_running(sha: str) -> bool:
    """Whether CI is live on this head. Fails closed for the same reason as ``_open_pr``.

    Returning ``False`` on an error would let the sweep close a PR whose checks are still running.
    This needs ``checks: read`` in the workflow - with an explicit ``permissions:`` block every
    unlisted scope is ``none``, so omitting it yields a 403 that used to read as "no checks".
    """
    status, suites = claim._request("GET", f"/repos/{REPO}/commits/{sha}/check-suites?per_page=100")
    if status != 200 or not isinstance(suites, dict):
        raise ReaperError(f"check-suite state could not be read (HTTP {status})")
    entries = suites.get("check_suites")
    if not isinstance(entries, list):
        raise ReaperError("check-suite response was malformed")
    # This endpoint wraps its list in an object, so `_paginate` does not apply. Detect truncation
    # instead of silently under-reading: a missed in_progress suite would let the sweep close a PR
    # whose CI is live, which is the same failure the fail-closed guard above exists to prevent.
    total = suites.get("total_count")
    if isinstance(total, int) and total > len(entries):
        raise ReaperError(
            f"check-suite list truncated ({len(entries)} of {total}); refusing to judge CI state"
        )
    return any(s.get("status") in {"queued", "in_progress"} for s in entries)


def _only_in_progress(number: int) -> bool:
    """Re-read the labels immediately before granting claimability.

    The earlier read happens before several network round-trips, and a maintainer can block an issue
    in between. Adding ``status:ready`` on top of ``status:blocked`` would let a successor take work
    a person deliberately stopped, so the decision is re-taken against fresh state here.

    This narrows the window; it does not close it. Label writes are not conditional either, so a
    block landing in the final round-trip still races. The durable fix belongs on the consumer
    side: ``claim._check_eligible`` should require ``status:ready`` to be the *only* status label
    rather than merely present, which makes a stray ready harmless instead of authoritative. That
    touches ``claim.py`` and is tracked separately rather than widened into this change.
    """
    status, issue = claim._request("GET", f"/repos/{REPO}/issues/{number}")
    if status != 200 or not isinstance(issue, dict):
        raise ReaperError(f"#{number} labels could not be re-read (HTTP {status})")
    if issue.get("state") != "open":
        # The open/closed check in the caller came from an earlier read; a maintainer closing the
        # issue in between must not end up with status:ready on a closed issue.
        return False
    names = {label["name"] for label in issue.get("labels", []) if isinstance(label, dict)}
    return not ({n for n in names if n.startswith("status:")} - {"status:in-progress"})


def _mark(number: int, label: str, *, dry_run: bool) -> None:
    """Apply a persistent marker, treating a failed write as fatal.

    On both pull-request paths this label is the ONLY thing the sweep does: since #277 the reaper
    neither closes a PR nor reclaims its ref, so the marker is not a hint beside the real action, it
    *is* the action. Reporting a flag in the run summary while the write silently failed would
    publish state that is not true and leave the PR unattended - the same silent-state failure this
    workflow exists to end. Raising instead leaves the claim untouched for the next sweep to retry.
    """
    if dry_run:
        return
    status, _ = claim._request("POST", f"/repos/{REPO}/issues/{number}/labels", {"labels": [label]})
    if status != 200:
        raise ReaperError(
            f"#{number} needs {label} but it could not be applied (HTTP {status}); "
            "not reporting a flag that was never set"
        )


def _clear_conflicted(number: int, *, dry_run: bool) -> bool:
    """Drop a stale ``agent:conflicted`` marker once the pull request is no longer dirty.

    Nothing removed it before, so an issue stayed marked as needing human conflict resolution long
    after a worker had rebased successfully. Because that label is meant to be the only persistent
    signal that a claim needs a person, a stale one degrades the very signal it exists to provide: a
    marker that is never cleared is indistinguishable from one that is never checked.

    Clearing on the next clean sweep is self-healing, and it is deliberately preferred over clearing
    inside ``_requeue``: a human usually resolves the conflict *without* the claim ever being
    reclaimed, and that is the case ``_requeue`` would never see.

    The outcome is read from the response rather than from a prior GET - 404 is the ordinary case
    (no marker) and 200 means one was really cleared - so the common path costs one call and no
    read. Unlike ``_mark`` this is best-effort: aborting a whole sweep over a cosmetic label would
    trade a real reclamation for a tidy one, and the next sweep retries regardless.
    """
    if dry_run:
        return False
    status, _ = claim._request(
        "DELETE", f"/repos/{REPO}/issues/{number}/labels/agent:conflicted", None
    )
    return status == 200


def _requeue(number: int, *, dry_run: bool, expect: tuple[int, str] | None = None) -> None:
    """Delete the claim ref and put the issue back on the queue.

    Order matters. Everything that can fail is checked BEFORE anything destructive happens:

    * The issue is read first. Reading it afterwards meant a transient non-200 left the ref
      already deleted and the labels already stripped, while ``status:ready`` was silently never
      restored. Sweeps discover work only through claim refs, so that issue would be stranded
      forever - the precise failure this whole workflow exists to repair.
    * The PR state is re-read immediately before the delete. ``concurrency`` serializes reaper runs
      against each other, not against workers, and opening a PR does not refresh branch activity -
      so a delayed worker could open its PR after the earlier check and still lose its claim.
    * Restoring the queue label is treated as an error, not best-effort: an issue left without
      ``status:ready`` is invisible to every future sweep.
    """
    if dry_run:
        return

    status, issue = claim._request("GET", f"/repos/{REPO}/issues/{number}")
    if status != 200 or not isinstance(issue, dict):
        raise ReaperError(f"#{number} could not be read (HTTP {status}); refusing to reclaim")

    # Restore the queue slot ONLY if the issue still looks like the in-progress claim we are
    # reaping. A maintainer may have moved it to blocked, backlog or done while the claim sat
    # abandoned; adding status:ready on top would leave both labels, and claim._check_eligible only
    # requires that ready be *present* - so a successor could immediately take work a human had
    # deliberately stopped. The reaper undoes its own bookkeeping; it does not overrule a person.
    labels = {label["name"] for label in issue.get("labels", []) if isinstance(label, dict)}
    other_status = {name for name in labels if name.startswith("status:")} - {"status:in-progress"}
    requeue_label = issue.get("state") == "open" and not other_status

    # Since #277 the ONLY caller is the no-PR path, so an open pull request here means the premise
    # the decision rested on is gone - a worker opened one after the earlier read. This used to be
    # softened because the stale path closed a PR immediately before calling in, which made "abort
    # on any PR" refuse the reclaim it had just authorized; with that path gone the guard is
    # unconditionally correct rather than a compromise. A *closed* PR is still fine: it is exactly
    # what the caller already saw.
    fresh = _open_pr(number)
    if fresh is not None and fresh.get("state") == "open":
        raise ReaperError(f"#{number} gained an open pull request mid-sweep; refusing to reclaim")

    # A push does not open a PR, so the check above cannot see one. Require the server-recorded
    # activity to be exactly what the decision was made from.
    if expect is not None and _fingerprint(number) != expect:
        raise ReaperError(f"#{number} saw new activity mid-sweep; refusing to reclaim")

    # Archive and verify the tip FIRST. This is the last step that can abort, and it must run before
    # any label change: previously it ran after, so an abort left a live claim ref beside a falsely
    # `status:ready` issue - claimers lost the 422 race forever, and later sweeps saw the fresh
    # activity, kept the claim, and never repaired the labels. Creating the archive ref is additive
    # and harmless if the reap is then abandoned.
    if _prepare_retire(number) is None:
        return

    # ORDER IS THE RECOVERY MECHANISM. Labels next, ref deletion last.
    #
    # Deleting the ref first made every later failure permanent: sweeps discover work only through
    # claim refs, so an issue whose ref was gone and whose status:ready never got restored was
    # invisible to every future sweep. Raising did not help - it made the stranding loud, not
    # recoverable. With the ref deleted last it is the commit point: any failure before it leaves
    # the ref in place, so the next sweep simply re-decides and retries. Restoring status:ready
    # while the ref still exists is harmless, because a claimer would lose the ref race with 422.
    # These two are deliberately best-effort, unlike every other mutation here: removing a label
    # that is not present returns 404, which is the common case (only one vendor's label exists,
    # and status:in-progress may already be gone). A stale label is cosmetic; the queue label and
    # the ref below are what correctness depends on, and both are checked.
    for vendor in claim.VENDORS:
        claim._request("DELETE", f"/repos/{REPO}/issues/{number}/labels/agent:{vendor}", None)
    claim._request("DELETE", f"/repos/{REPO}/issues/{number}/labels/status:in-progress", None)

    if requeue_label and _only_in_progress(number):
        status, _ = claim._request(
            "POST", f"/repos/{REPO}/issues/{number}/labels", {"labels": [claim.REQUIRED_LABEL]}
        )
        if status != 200:
            raise ReaperError(
                f"#{number} could not be returned to {claim.REQUIRED_LABEL} (HTTP {status}); "
                "its claim ref is deliberately left in place so the next sweep retries"
            )

    _retire_ref(number)


def _read_ref(number: int) -> str | None:
    status, current = claim._request("GET", f"/repos/{REPO}/git/ref/heads/{BRANCH_PREFIX}{number}")
    if status == 404:
        return None
    if status != 200 or not isinstance(current, dict):
        raise ReaperError(f"#{number} claim ref could not be read (HTTP {status})")
    return str(current["object"]["sha"])


def _prepare_retire(number: int) -> str | None:
    """Archive the tip and confirm it has not moved. The last step that can abort.

    Separated from the delete so that **every abortable step runs before any state change**. It
    used to run after the labels had already flipped, so an abort left a live claim ref beside a
    falsely `status:ready` issue: claimers lost the 422 race forever, and later sweeps saw the
    fresh activity, kept the claim, and never repaired the labels. Creating the archive ref is
    additive, so it is harmless if the reap is then abandoned.

    **What this does and does not guarantee.** `DELETE /git/refs` takes no expected-SHA, so GitHub
    offers no compare-and-swap for ref deletion, and `concurrency` serializes reaper runs against
    each other rather than against workers. A residual window therefore **cannot be eliminated**,
    only bounded - an earlier version of this docstring claimed the archive made losing the race
    harmless, which was wrong: archiving tip A and then deleting whatever the ref points at destroys
    a push that landed in between, unarchived.

    So: read the tip, copy it to ``refs/reaped/issue-<N>-<sha>``, read it again, and let the caller
    delete only if it is unchanged. A push during those steps aborts the reap. A push between the
    final read and the caller's DELETE is the irreducible remainder: one round-trip wide. Three
    things keep even that from being a loss - the tip is archived, the worker still holds the
    commits locally, and its next `claim check` fails on the generation fence.

    Returns the archived sha, or ``None`` when there is no ref left to retire.
    """
    before = _read_ref(number)
    if before is None:
        return None

    # The FULL sha, not a prefix. With an 8-character prefix a 422 was ambiguous - it could mean
    # "this exact archive exists" or "a different commit sharing that prefix was archived earlier",
    # and accepting the second would delete a tip that was never preserved. With the full sha the
    # ref name identifies the commit exactly, so 422 can only mean the identical archive is already
    # there. The ambiguity is removed rather than checked for.
    status, _ = claim._request(
        "POST",
        f"/repos/{REPO}/git/refs",
        {"ref": f"refs/reaped/issue-{number}-{before}", "sha": before},
    )
    archive = f"refs/reaped/issue-{number}-{before}"
    if status not in (201, 422):
        raise ReaperError(
            f"#{number} claim ref could not be archived (HTTP {status}); refusing to delete it"
        )
    if status == 422:
        # 422 from POST /git/refs is not only "already exists" - it also covers validation failures
        # such as an unknown sha or a malformed name. Assuming the benign meaning would let the
        # delete proceed against an archive that was never created, so confirm it exists and points
        # where we think it does.
        status, existing = claim._request(
            "GET", f"/repos/{REPO}/git/ref/{archive.removeprefix('refs/')}"
        )
        target = existing.get("object", {}).get("sha") if isinstance(existing, dict) else None
        if status != 200 or target != before:
            raise ReaperError(
                f"#{number} archive {archive} is absent or points elsewhere; refusing to delete"
            )

    # Only let the caller delete the tip we actually archived. If it moved while we were archiving,
    # the worker is alive and this reap is abandoned - before any label has changed.
    after = _read_ref(number)
    if after != before:
        raise ReaperError(
            f"#{number} claim ref moved while being archived; refusing to delete an unarchived tip"
        )
    return before


def _retire_ref(number: int) -> None:
    """Commit the retirement: delete the ref whose tip ``_prepare_retire`` already archived.

    This is the commit point. Everything that can abort has already run, and every failure after it
    leaves the ref in place for the next sweep to re-decide and retry.
    """
    status, _ = claim._request("DELETE", f"/repos/{REPO}/git/refs/heads/{BRANCH_PREFIX}{number}")
    if status not in (204, 404):
        raise ReaperError(f"#{number} claim ref could not be deleted (HTTP {status})")


def sweep(*, dry_run: bool) -> list[dict[str, Any]]:
    now = _now()
    actions: list[dict[str, Any]] = []
    for number in _claim_refs():
        # The snapshot every decision below is made from. Re-checked immediately before any
        # destructive call, so a worker that becomes active mid-sweep keeps its claim.
        expect = _fingerprint(number)
        pr = _open_pr(number)
        last = _last_activity(number)

        # No activity record is UNKNOWN, not stale. The feed can lag a ref that was just created -
        # claim.py's own `201 but no activity record appeared` path exists for exactly that - so
        # reclaiming here would reap brand-new claims, and `expect` would be None so the later
        # fingerprint recheck could not catch it either.
        if last is None or expect is None:
            actions.append({"issue": number, "action": "keep", "reason": "activity-unknown"})
            continue
        age_min = (now - last).total_seconds() / 60

        if pr is None or pr.get("state") != "open":
            if age_min < NO_PR_MINUTES:
                actions.append({"issue": number, "action": "keep", "reason": "recent-activity"})
                continue
            _requeue(number, dry_run=dry_run, expect=expect)
            actions.append({"issue": number, "action": "requeue", "reason": "no-open-pr"})
            continue

        if pr.get("mergeable_state") == "dirty":
            _mark(number, "agent:conflicted", dry_run=dry_run)
            actions.append({"issue": number, "action": "flag-conflicted", "reason": "dirty"})
            continue

        # GitHub computes mergeability asynchronously: `mergeable: null` means "not known yet",
        # which is not the same as "not conflicted". Deciding to close on an unknown would be the
        # fail-open this PR already had to fix once.
        if pr.get("mergeable") is None:
            actions.append({"issue": number, "action": "keep", "reason": "mergeability-unknown"})
            continue

        pr_age_h = (now - _parse(pr["updated_at"])).total_seconds() / 3600
        stale = pr_age_h >= STALE_PR_HOURS and not _checks_running(pr["head"]["sha"])

        # ONLY NOW is it safe to write: `_checks_running` above is the last read that can fail, and
        # it fails closed. Clearing before it meant an unreadable check state deleted a label and
        # *then* raised - a sweep that has just admitted it cannot read the state it is deciding
        # from must not already have mutated. The PR is open and demonstrably not dirty at this
        # point, so any conflict marker it still carries is false whether or not it has also gone
        # quiet; leaving it would pair `agent:conflicted` with `agent:needs-amend` on one issue.
        cleared = _clear_conflicted(number, dry_run=dry_run)

        if stale:
            # #277: label only. No close, no label move, no ref deletion - so there is no ordering
            # to get wrong, nothing to revalidate against, and no need to fence with `expect`. A
            # worker that comes back finds its PR and its claim exactly where it left them, and the
            # launcher's AMEND path continues the PR instead of a successor starting over.
            _mark(number, "agent:needs-amend", dry_run=dry_run)
            actions.append(
                {
                    "issue": number,
                    "action": "flag-needs-amend",
                    "reason": "stale-pr",
                    "pr": pr["number"],
                }
            )
            continue

        if cleared:
            actions.append(
                {"issue": number, "action": "clear-conflicted", "reason": "conflict-resolved"}
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
