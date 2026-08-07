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
arrived as a review. So this counter can **undercount**, and undercounting
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
EXTERNAL_PROVIDERS = frozenset(
    {"chatgpt-codex-connector[bot]", "coderabbitai[bot]", "greptile-apps[bot]"}
)

#: The providers whose rounds the cap counts. **Codex is deliberately absent**: it is the unmetered
#: lane the review lane iterates on freely, and counting it would let free iteration consume the
#: rounds reserved for the mandatory CodeRabbit stage (ADR-0062). Greptile IS counted - a spent
#: credit is a real round - but its findings reach `owed` through EXTERNAL_PROVIDERS either way,
#: because a paid review that nothing answers is the worst of both.
METERED_PROVIDERS = frozenset({"coderabbitai[bot]", "greptile-apps[bot]"})

#: The one provider that can satisfy the mandatory gate. ADR-0062 names CodeRabbit specifically -
#: Greptile is optional and its exhaustion never blocks - so the gate signal is keyed on this login
#: alone. Reading it off ``METERED_PROVIDERS`` instead would let a spent Greptile credit report a
#: CodeRabbit gate as met, which is the one substitution the lane does not allow.
GATE_PROVIDER = "coderabbitai[bot]"

CAP = 2
ROUND_LABELS = ("agent:round-1", "agent:round-2")
CAPPED_LABEL = "agent:review-capped"
AMEND_LABEL = "agent:needs-amend"

#: *This pull request cannot satisfy its own gate, and no automatic state remains.* Published when
#: the convergence verification a capped PR is allowed (#399) itself came back blocking, so the
#: count has passed ``CAP``. Without it such a PR sits green, mergeable and forbidden to merge, with
#: nothing anywhere saying why - which is how #385 was found, by hand.
#:
#: Distinct from ``agent:review-capped``, which means *the budget is spent and the lane may still
#: terminate*. This one means *the lane has stopped terminating*, and it is a maintainer's.
GATE_BLOCKED_LABEL = "agent:gate-blocked"
#: *This draft's lane has somewhere to go, and nobody is going there.* The lane (ADR-0062) is a
#: multi-phase sequence - iterate Codex on the draft, optionally spend a Greptile credit, mark
#: ready, ask CodeRabbit - but the only resumption signal the swarm had was ``agent:needs-amend``,
#: published when a check failed or a finding was owed. A **clean** review owes nothing, so it
#: published nothing, no worker resumed, and the draft sat forever before the gate it cannot merge
#: without (#394).
#:
#: Deliberately a second label rather than a reuse of ``agent:needs-amend``. They authorise
#: different sessions: AMEND says *fix the blocking findings*, and a session handed that with none
#: to find would either invent work or stop. #394's second criterion is that the session be told
#: which phase it is in, and one label cannot say two things.
ADVANCE_LABEL = "agent:needs-advance"
CONFLICTED_LABEL = "agent:conflicted"
ALL_ROUND_LABELS = (*ROUND_LABELS, CAPPED_LABEL)

# The claim's label MIRROR: the labels that describe work in flight, cleared when its PR merges
# (#308). `status:ready` is deliberately NOT in this set and is never written by the merge path —
# see `clear_mirror`.
#
# `agent:conflicted` IS in the set, and the first draft of this change left it out on the grounds
# that the reaper owns it (#276) and clears it on the first clean sweep. That reasoning does not
# survive the merged path, and checkably so: `reaper.sweep` iterates `_claim_refs()` - the refs that
# still EXIST - and a squash-merge deletes `agent/issue-N`. The reaper therefore never visits a
# merged claim's issue again, so a conflict that was resolved and then merged would keep a marker
# saying it still needs a person, forever, which is precisely the failure #276 named.
#
# The usual two-writers hazard does not apply here, and for two reasons that are properties of the
# code rather than promises about it. Stated precisely, because the version of this comment that
# said "clear_mirror refuses to write when a live ref owns the issue" was FALSE - the fence
# deliberately proceeds when the ref survives at the merged head - and a false justification is the
# defect this whole change is about:
#
# 1. GitHub will not merge a PR with conflicts. So a marker that survives to a merge is provably
#    STALE, and the merge path can only ever delete a false one. This is the load-bearing reason.
# 2. Both of the reaper's conflicted paths - `_mark` at reaper.py:443-445 and `_clear_conflicted` at
#    reaper.py:464 - sit past the `pr is None or state != "open"` guard, so they need a live ref AND
#    an OPEN pull request. A merged PR is closed, so in the one window where `clear_mirror` writes
#    beside a surviving ref, `sweep` takes the no-PR branch and reaches neither. Pinned by
#    `test_a_closed_pull_request_is_never_marked_conflicted` in tests/test_reaper.py, so the claim
#    this set's safety rests on is a gate and not this paragraph.
MIRROR_LABELS = (
    "status:in-progress",
    AMEND_LABEL,
    GATE_BLOCKED_LABEL,
    ADVANCE_LABEL,
    CONFLICTED_LABEL,
    *ALL_ROUND_LABELS,
)

# A label DELETE that reached the desired end state. 404 counts: the label is already gone, which
# is what the call was for. Anything else is a failure the merge path must not swallow - see
# `clear_mirror`.
DELETE_DONE = frozenset({200, 204, 404})

# A DELETE that actually TOOK a label off, which is a narrower question than whether the call
# succeeded. `404` is success - the label is already gone, which is what the call was for - but it
# removed nothing, so counting it as damage would have `_successor_arrived_mid_cleanup` tell a human
# a live claim lost a label this run never touched, and invite re-adding it. That is #308's failure
# arriving through the report rather than through the code (#381).
DELETE_REMOVED = frozenset({200, 204})

# `cancelled` and `stale` are here deliberately. Neither is a live status nor a pass, so leaving
# them out made a readable-but-not-green head report as green and CLEAR a real
# `agent:needs-amend` - the
# fail-open direction, on the one label that carries AMEND authority. `skipped` and `neutral` are
# genuinely non-failing and stay out.
FAILED_CONCLUSIONS = frozenset(
    {"failure", "timed_out", "action_required", "startup_failure", "cancelled", "stale"}
)
LIVE_STATUSES = frozenset({"queued", "in_progress", "pending", "requested", "waiting"})
# A review at the current head that is still waiting for an answer. CHANGES_REQUESTED is explicit;
# inline comments are the form both providers actually use (both answered #285 as COMMENTED with
# inline findings), so keying only on CHANGES_REQUESTED would see no outstanding review at all.
BLOCKING_REVIEW_STATES = frozenset({"CHANGES_REQUESTED"})

# States that are a verdict in themselves, so the submission is a real review even with no body.
# `COMMENTED` is deliberately absent: it is what GitHub stamps on the wrapper it puts around a
# reply to a review thread, and treating it as a verdict is exactly the over-count of #396.
VERDICT_REVIEW_STATES = frozenset({"CHANGES_REQUESTED", "APPROVED", "DISMISSED"})

# The GATE's proving half, as an ALLOWLIST (#415). These are the submission states that can mean
# *a review happened at this head and found nothing*; everything else proves nothing.
#
# The predicate used to be spelled by EXCLUSION - anything that was not `CHANGES_REQUESTED` proved
# the gate - which is allow-by-default on the one axis merges are decided on. `DISMISSED` is the
# state that made it a live defect: a worker who cannot converge can submit a review and then
# `PUT /pulls/{n}/reviews/{id}/dismissals`, and the withdrawn verdict's BODY outlives it, so the
# submission still looked like a provider reporting. #408 closed the empty case by taking
# `DISMISSED` out of the set consulted here; it could not close the case with a body, because a
# real `CHANGES_REQUESTED` review always has one.
#
# An allowlist also answers the states nobody has thought of yet. `PENDING`, an absent `state` and
# whatever GitHub adds next all prove nothing - and, just as importantly, VOID nothing: retracting a
# gate a genuine clean review already met would strand the lane with nothing to push. Voiding stays
# the separate, fail-closed job of `BLOCKING_REVIEW_STATES`.
GATE_PROVING_STATES = frozenset({"COMMENTED", "APPROVED"})
#: The member of the set above that is a verdict in itself, so it proves the gate with no body.
#: `COMMENTED` is not one: GitHub stamps it on reply wrappers too (#396), so it has to show that it
#: reported something. `DISMISSED` is a verdict being *withdrawn* and is in neither place.
GATE_VERDICT_STATE = "APPROVED"

# A review someone has STARTED DRAFTING and not submitted (#400). GitHub's own schema for the
# reviews endpoint says it outright: a review "created in the PENDING state" is "not submitted and
# therefore does not include the `submitted_at` property". `_counts_as_round` counts a
# timestamp-less entry deliberately, so without this an unsubmitted draft spends a round that no
# review has taken - a cap consumed by work nobody has done.
#
# Skipped here rather than by teaching `_counts_as_round` about it. That function must keep failing
# TOWARD counting for a missing or malformed timestamp, which is the direction ADR-0062 requires of
# a safety control and the property #396 rests on; narrowing it to fix this would trade a visible
# over-count for the fail-open one.
#
# **Not reachable through the identity this workflow runs as**, and the docstring says so rather
# than claiming a defect nobody can trigger: GitHub keeps a pending review's comments "only visible
# to you", so a metered provider's draft never appears in a payload this reads. What the guard buys
# is that the count stays correct whichever identity reads it - a deployment property, not one this
# module can assert about itself.
UNSUBMITTED_REVIEW_STATE = "PENDING"


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


def _mirror_present(labels: set[str]) -> list[str]:
    """The mirror labels actually on the issue, vendor markers included.

    Returned rather than deleted blindly so the run reports what it changed, and so a DELETE is
    not issued for a label that was never there.
    """
    mirror = set(MIRROR_LABELS) | {f"agent:{vendor}" for vendor in claim.VENDORS}
    return sorted(labels & mirror)


def _claim_ref_sha(issue: int) -> str | None:
    """The SHA ``agent/issue-<N>`` points at *now*, or ``None`` when the ref is gone.

    Fails closed on an unreadable answer, and this is the one read where that matters most: "I
    cannot see whether a successor holds this claim" must never resolve to "no successor holds it",
    because this is the read that authorises a delete.
    """
    status, ref = claim._request("GET", f"/repos/{REPO}/git/ref/heads/{BRANCH_PREFIX}{issue}")
    if status == 404:
        return None
    if status != 200 or not isinstance(ref, dict):
        raise TriageError(f"the claim ref for #{issue} could not be read (HTTP {status})")
    sha = (ref.get("object") or {}).get("sha")
    if not isinstance(sha, str) or not sha:
        raise TriageError(f"the claim ref for #{issue} carries no object SHA")
    return sha


def _successor_arrived_mid_cleanup(issue: int, merged_head: str, removed: list[str]) -> None:
    """Raise if a successor claimed ``issue`` while the delete loop was running (#381).

    **The residual #334 could not close, converted from silence into a red job.** The fence above
    reads the claim ref once and then issues one DELETE per label, and there is no transaction
    across those writes: ``POST``/``DELETE /issues/{n}/labels`` take no precondition — no ETag, no
    expected-state header — so "delete only if no successor has claimed since" is not expressible.
    Meanwhile ``claim._cmd_claim`` creates the ref *first* and publishes its mirror *second*, so a
    successor that interleaves has its freshly written labels removed by this loop. Blast radius is
    ``agent:<vendor>`` and ``status:in-progress`` ordinarily, and ``agent:needs-amend`` if the
    successor has progressed — the last of which is the launcher's authority to start an AMEND
    session, so losing it makes a live claim silently unauthorized.

    **Nothing prevents it, so this detects it.** Three things that look like fixes are not: the
    cleanup cannot hold the mutex, because the claim ref *is* the mutex and the merge already
    released it by deleting the branch — re-acquiring would make this a second writer for the very
    mutex the design gives to workers. ``concurrency`` in ``agent-triage.yml`` serialises triage
    runs against each other, never against workers (the same limitation #278 records for the
    reaper). And re-reading the ref before *each* DELETE only narrows the window; it does not
    remove it.

    ``removed`` is what the run **actually took off**, never what it attempted. ``DELETE_DONE``
    includes ``404`` because an already-absent label is a successful delete, but it removed nothing,
    and a report that counted it would name labels for re-adding that this cleanup never touched -
    #308's failure arriving through the report instead of through the code. The caller therefore
    skips this entirely when nothing was removed: no damage is possible, so there is nothing to
    audit and no reason to spend the read.

    **This has no repair path, deliberately.** Re-adding what was deleted would give
    :func:`clear_mirror` an add path, and its not having one is load-bearing (#308): re-adding
    ``status:ready`` is precisely the failure #308 was written to prevent. So this reports and
    stops. That is the precedence #334 already encodes — *visible residue over silent destruction*
    — and the sibling of #278's irreducible window on the reaper's ref deletion.
    """
    live = _claim_ref_sha(issue)
    if live is None or live == merged_head:
        return
    raise TriageError(
        f"#{issue} was claimed by a successor while its merged mirror was being cleared, and "
        f"{', '.join(removed) or 'no label'} may have been removed from that LIVE claim. The "
        f"claim ref is at {live[:7]}, not the merged head {merged_head[:7]}. Re-add by hand only "
        "what the successor is owed - this job has no add path on purpose (#308) - and check "
        f"whether it lost {AMEND_LABEL}, the launcher's authority to start an AMEND session."
    )


def clear_mirror(*, number: int, dry_run: bool) -> dict[str, Any]:
    """Clear a merged claim's label mirror from its linked issue (#308).

    The happy path is the one path with no cleanup. ``claim.py release`` clears the mirror and the
    reaper's ``_requeue`` clears it, but a PR that simply *merges* clears nothing: the squash
    deletes the branch (so the mutex releases correctly) and ``Closes: #N`` closes the issue, while
    the worker exited long before, exactly as ``arm auto-merge and exit`` intends. The labels then
    describe live work on a finished item — and `#276` already settled that a marker nothing removes
    is indistinguishable from one nothing checks.

    Four properties, each of which is a way this could go wrong:

    * **Merged, not merely closed.** A PR closed *without* merging leaves the claim live and the ref
      in place, so its mirror is still true. Only ``merged`` clears.
    * **Fenced against a successor, and audited after the fact.** ``merged`` is a fact about a
      finished pull request and says nothing about who owns the issue *now*. The ref is re-read
      immediately before the deletes and compared to the merged head, so a reopened-and-reclaimed
      issue is left entirely alone — and re-read *again* afterwards, because that fence is one
      snapshot before several authoritative writes. See :func:`_successor_arrived_mid_cleanup`.
    * **Never re-queues.** ``status:ready`` is not in :data:`MIRROR_LABELS` and this function has no
      add path at all — that is the structural guarantee, not a rule to remember. Merged work is
      *done*, and returning it to the queue would hand a finished item to the next worker. This is
      the one place the merge path must differ from ``reaper._requeue``, which requeues on purpose.
    * **Reads the issue whatever its state.** ``_issue_labels`` gates on ``open`` and returns
      ``None`` otherwise, which is right for the round counter and fatal here: by the time a PR has
      merged, ``Closes: #N`` has already closed the issue. Reading it separately is the point of
      this function rather than a flag threaded through :func:`triage`.

    Unlike :func:`_apply`, **deletions here are not best-effort.** ``_apply`` may shrug off a failed
    removal because its state is recomputed on the next event; this path has no next event. The
    ``closed`` event fires once, and the reaper cannot pick up the slack because the merge already
    deleted the ref ``reaper.sweep`` would have had to enumerate. A swallowed 429 or 502 would
    therefore leave exactly the stale mirror this function exists to remove, while reporting that it
    removed it. So every DELETE is checked, and a failure raises: the job goes red and re-running it
    replays the same ``closed`` payload, which is the retry — *while no successor has claimed the
    issue*. Once one has, the fence below correctly refuses and the residue is a maintainer's. That
    is the right precedence: never strip a live claim to tidy a dead one.
    """
    status, pr = claim._request("GET", f"/repos/{REPO}/pulls/{number}")
    if status == 404:
        return {"action": "skip", "reason": "no-pull-request", "pr": number}
    if status != 200 or not isinstance(pr, dict):
        raise TriageError(f"PR #{number} could not be read (HTTP {status})")
    if not pr.get("merged"):
        # Fails closed on the safe side: an unmerged close means the claim may still be live.
        return {"action": "skip", "reason": "closed-without-merging", "pr": number}

    issue = _linked_issue(pr)
    if issue is None:
        return {"action": "skip", "reason": "not-agent-claimed", "pr": number}

    merged_head = (pr.get("head") or {}).get("sha")
    if not isinstance(merged_head, str) or not merged_head:
        raise TriageError(f"PR #{number} reports no head SHA to fence the cleanup against")

    status, payload = claim._request("GET", f"/repos/{REPO}/issues/{issue}")
    if status != 200 or not isinstance(payload, dict):
        raise TriageError(f"#{issue} could not be read (HTTP {status})")
    labels = {label["name"] for label in payload.get("labels", []) if isinstance(label, dict)}
    remove = _mirror_present(labels)

    # THE SUCCESSOR FENCE, re-read immediately before the deletes rather than inferred from the
    # merge, because those are two different questions. ``merged`` is a fact about a pull request
    # that is over; whether the labels on the issue still describe *that* claim is a fact about now.
    # The merge deletes `agent/issue-N`, but the issue can be reopened, re-marked `status:ready` and
    # claimed again - and this run can arrive late, or be re-run from the Actions UI long after.
    # Deleting then would strip `status:in-progress`, the vendor marker and the round state off a
    # LIVE claim while leaving its mutex ref intact: a worker still holding the ref, invisible on
    # the board, and a successor whose AMEND authority silently disappeared.
    #
    # Compared against the merged head, NOT mere existence. "Ref exists -> skip" would also skip the
    # ordinary path whenever GitHub's branch deletion has not landed by the time the event is
    # handled, and the cleanup would silently never happen at all - the same defect in a new place.
    # A surviving ref at the merged head is this claim's own branch - normally mid-deletion, and if
    # it persists (GitHub declines to auto-delete a branch that is another open PR's base) the
    # reaper still enumerates it, so nothing is stranded either way.
    live = _claim_ref_sha(issue)
    if live is not None and live != merged_head:
        return {"action": "skip", "reason": "successor-claim", "pr": number, "issue": issue}

    failed: list[str] = []
    deleted: list[str] = []
    if not dry_run:
        for label in remove:
            status, _ = claim._request(
                "DELETE", f"/repos/{REPO}/issues/{issue}/labels/{label}", None
            )
            if status in DELETE_REMOVED:
                deleted.append(label)
            elif status not in DELETE_DONE:
                failed.append(f"{label} (HTTP {status})")
        # THE WINDOW AUDIT, and two things about when it runs are deliberate.
        #
        # It runs BEFORE the failure report. A failed DELETE wants a re-run; a successor arriving
        # mid-loop means a live claim lost labels and wants a human - and re-running this job while
        # a successor holds the issue is exactly what must not happen. Reporting the re-runnable
        # condition first would invite precisely that.
        #
        # It runs only when something was actually removed. `deleted` is keyed on DELETE_REMOVED
        # rather than DELETE_DONE, so a 404 - the label was already gone, which on a re-run is the
        # ordinary case - is not damage. Auditing anyway would spend a read to report a successor
        # that lost nothing, and name labels for re-adding that this cleanup never touched.
        if deleted:
            _successor_arrived_mid_cleanup(issue, merged_head, deleted)
    if failed:
        # Every label is attempted before raising: partial progress is strictly better than none,
        # and one message naming all of them beats discovering them one re-run at a time.
        raise TriageError(
            f"#{issue} still carries {', '.join(failed)} after its pull request merged; "
            "this cleanup has no second chance of its own - the `closed` event fires once and the "
            "reaper cannot reach a claim whose ref the merge has already deleted. Re-run this job."
        )
    return {"action": "clear-mirror", "pr": number, "issue": issue, "removed": remove}


def _reviewed_head(entry: dict[str, Any]) -> str | None:
    """The head a provider actually read, which is not always ``commit_id``.

    GitHub carries an inline review comment forward onto later commits and rewrites its
    ``commit_id`` to match, leaving ``original_commit_id`` as the head it was written against.
    ``updated_at`` does not change, so the rewrite is invisible in the payload's own metadata.

    Counting ``commit_id`` made an answer to a round score as a round of its own: push the fix, the
    provider's comment moves to the fix commit, and the counter sees two heads for one review. Every
    pull request was therefore capped as soon as it answered once - and `agent:review-capped` is
    what makes the launcher refuse an AMEND, so the failure was silent and looked exactly like the
    cap working correctly (#307).

    Submitted reviews carry only ``commit_id``; for them the fallback IS the answer.
    """
    original = entry.get("original_commit_id")
    if isinstance(original, str) and original:
        return original
    commit = entry.get("commit_id")
    return commit if isinstance(commit, str) and commit else None


#: Returned by :func:`_counted_from` for a pull request that is STILL a draft. A sentinel rather
#: than ``None``, because ``None`` already means the opposite - count everything - and a bare
#: timestamp cannot express "nothing counts yet". It sorts above every ISO-8601 instant, but
#: :func:`_counts_as_round` tests it by identity rather than relying on that: the comparison is the
#: LAST clause there, and the malformed-data clauses in front of it used to answer first (#423).
_COUNT_NOTHING = "9999-12-31T23:59:59Z"


def _counts_as_round(when: object, counted_from: str | None) -> bool:
    """Whether one piece of review evidence consumes a round.

    ISO-8601 UTC instants from the GitHub API are fixed-width and zero-padded, so lexicographic
    comparison is chronological; no parsing, and nothing to get wrong about time zones.

    **The phase is asked first, and that ordering is the fix for #423.** This used to open on the
    malformed-data clauses, which meant a metered review with no usable ``submitted_at`` counted
    even against ``_COUNT_NOTHING`` - so a draft could be capped, which is the one thing ADR-0062
    says the draft phase cannot do, and the state ``_advance_state`` used to carry a branch for.
    Measured over every shape ``when`` can take out of the payload, nine of ten reached the counting
    answer on a draft; only a well-formed instant did not.

    Evidence with no timestamp still counts everywhere else, deliberately. Dropping it would let a
    provider response the API rendered oddly silently buy an extra round, and the cap is a safety
    control: it fails toward counting. That direction is unchanged for both other values of
    ``counted_from``, and that is measured rather than argued - see
    ``test_the_reorder_changes_nothing_outside_the_draft_phase``.
    """
    if counted_from is _COUNT_NOTHING:
        return False  # ADR-0062: the draft phase spends no rounds, whatever the payload looks like.
    if counted_from is None:
        return True
    return not isinstance(when, str) or not when or when >= counted_from


def _counted_from(pr: dict[str, Any], *, strict: bool = False) -> str | None:
    """When the round cap starts counting for this pull request, as an ISO-8601 instant.

    ``None`` means *count everything*: the pull request was opened ready for review, so every
    provider round it has ever had is a real round.

    The review lane spends the free provider first, on a draft, iterating until nothing blocking is
    left — and only then marks the PR ready and starts spending metered reviews. Counting the draft
    iteration would strand a PR at ``agent:review-capped`` before it ever reached the mandatory
    CodeRabbit gate: the documented loop would consume the cap it is explicitly exempt from.

    **Entering the counted phase is permanent.** The instant is the *first* ``ready_for_review``,
    and a PR converted back to draft keeps every round it has already spent. Two earlier drafts of
    this function each had the same loophole from a different direction — taking the *last* event,
    and short-circuiting on the current ``draft`` flag — and both let a worker buy unlimited metered
    rounds by toggling draft. A material push is granted **no** extra round; a draft excursion is
    not a way around that.

    So a PR that has never been ready has taken no counted round, and once it has been ready once,
    the clock started then and never restarts.
    """
    try:
        events = claim._paginate(f"/repos/{REPO}/issues/{pr['number']}/timeline", "PR timeline")
    except (claim.ClaimError, TriageError):
        # An unreadable timeline must not decide the cap by accident. Counting everything is the
        # safe direction for a safety control: at worst a PR is capped one round early and the
        # maintainer is asked, where the other direction hands out an unbounded review budget.
        #
        # `strict` exists because that answer is safe for the CAP and unsafe for a mutex KEY.
        # `None` here is indistinguishable from `None` meaning "opened ready", so a caller building
        # a ref name from the phase would write `ready-*` on a transient failure and `draft-*` on
        # success — two names for one state, and therefore two workers where the ref promises one
        # (Codex P2 on #407). Such a caller asks for the refusal instead.
        if strict:
            raise
        return None
    ready = [
        stamp
        for event in events
        if event.get("event") == "ready_for_review"
        and isinstance(stamp := event.get("created_at"), str)
        and stamp
    ]
    drafted = [
        stamp
        for event in events
        if event.get("event") == "convert_to_draft"
        and isinstance(stamp := event.get("created_at"), str)
        and stamp
    ]
    # Opened READY: a PR created ready emits no `ready_for_review`, so a later draft excursion and
    # return would make that return look like the first entry into the counted phase - discarding
    # every round spent before it. A `convert_to_draft` earlier than any `ready_for_review` is the
    # signal, and it means the clock started at creation.
    if drafted and (not ready or min(drafted) < min(ready)):
        return None
    if ready:
        return min(ready)
    # Never ready and never drafted. A draft has taken no counted round; anything else was opened
    # ready, so every round it has ever had is real.
    return _COUNT_NOTHING if pr.get("draft") else None


#: The head a provider names in a plain issue comment. Codex writes ``**Reviewed commit:**
#: `9785d61352``` in every verdict it posts that way, and that string is the only head binding such
#: a comment has. Read for the *did a review happen* axis only — never for the round counter, which
#: stays strictly on `commit_id`-carrying evidence and its documented undercount.
_VERDICT_HEAD = re.compile(r"Reviewed commit:\**\s*`?([0-9a-f]{7,40})`?")


def _verdict_at_head(pr_number: int, head: str) -> bool:
    """Whether an external provider has posted a verdict naming the current head (#394, #407).

    A provider's **clean** pass is the one that produces no review object. Codex submits a review
    when it has findings and posts a plain issue comment - or a 👍 reaction - when it does not, and
    neither of those carries ``commit_id``. So the state that most needs the lane advanced is
    precisely the one ``/pulls/{n}/reviews`` cannot show, and reading only that channel left every
    cleanly-reviewed draft stranded.

    Prefix-matched because the comment abbreviates the SHA. Fails **soft**, not closed: an
    unreadable comment list means *no verdict seen*, which withholds an authority rather than
    granting one, and the review and comment channels are still read by the caller.

    That is true of *every* way the read can fail only because ``claim._request`` now converts a
    malformed or truncated answer into ``ClaimError`` as well. It used to let those out raw, so the
    soft failure this docstring promises was a hard crash of the whole triage run for anything but
    a transport error (CodeRabbit on #407).
    """
    try:
        comments = claim._paginate(f"/repos/{REPO}/issues/{pr_number}/comments", "PR comment list")
    except claim.ClaimError:
        return False
    for entry in comments:
        if ((entry.get("user") or {}).get("login")) not in EXTERNAL_PROVIDERS:
            continue
        for named in _VERDICT_HEAD.findall(str(entry.get("body") or "")):
            if head.startswith(named):
                return True
    return False


#: Review threads and their resolution, which the REST payloads do not carry at all. Paged because
#: a long-running PR approaches 100 threads - #385 reached 54 - and this set only ever REMOVES
#: owing: a comment absent from it is owed. So a short read drops resolved threads and reports
#: ANSWERED findings as unanswered, which re-issues AMEND sessions against work already deferred.
#: That is the fail-closed direction, and stating it the other way round is how a later reader
#: comes to relax the wrong control.
_RESOLVED_THREADS = """
query($owner:String!, $name:String!, $number:Int!, $cursor:String) {
  repository(owner:$owner, name:$name) {
    pullRequest(number:$number) {
      reviewThreads(first:100, after:$cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          isResolved
          comments(first:100) {
            pageInfo { hasNextPage }
            nodes { fullDatabaseId body author { login } }
          }
        }
      }
    }
  }
}
"""


#: The contract's deferral reply, which `docs/agents/review.md` and `.agents/tasks/amend.md` both
#: spell `Deferred: … Tracked in #N`. Anchored per line so it matches the reply's opening whether or
#: not the worker wrapped it in emphasis, and paired with an issue reference because half the
#: sentence is not the sentence.
_DEFERRAL_RE = re.compile(r"^\s*[*_>\s]*deferred\s*:", re.IGNORECASE | re.MULTILINE)
_TRACKED_RE = re.compile(r"#\d+")


def _carries_a_deferral(comments: list[Any]) -> bool:
    """Whether a resolved thread records the one answer that resolution alone is allowed to mean.

    The severity floor permits resolve-without-push for a **non-blocking** finding only: defer it to
    one follow-up issue, reply ``Deferred: … Tracked in #N``, resolve. A blocking finding must be
    fixed, which moves the head and clears the owing that way. So ``isResolved`` on its own says
    *somebody closed this*, not *this was answered the way the contract allows* — and reading the
    first as the second lets a resolved blocking thread retract the AMEND authority that exists to
    address it (Codex P1 on #405).

    The reply is the part of that procedure the payload can show. It must come from outside
    ``EXTERNAL_PROVIDERS``: a provider writing the word in its own finding is not the author
    deferring it. The follow-up issue is required to be *referenced*, not to exist — nothing here
    can tell ``#4711`` from a real number, and ``amend.md`` already forbids pointing at one that is
    not there.

    **GraphQL and REST spell a bot's login differently**, and the first version of this check was
    wrong because of it: ``author.login`` on a ``Bot`` is ``coderabbitai``, while the REST ``user.
    login`` these provider sets were built from is ``coderabbitai[bot]``. Comparing the raw GraphQL
    login against ``EXTERNAL_PROVIDERS`` therefore matches nothing — no exception, no empty result,
    just a provider-authored comment silently passing as the author's own deferral. Both spellings
    are tried.

    Severity itself is still unread, and this does not pretend otherwise: a worker that writes the
    deferral over a blocking finding defeats it. That needs the provider severity parsing tracked in
    #409. What this removes is the *silent* path, where no answer at all cleared the label.
    """
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        login = ((comment.get("author") or {}).get("login")) or ""
        if login in EXTERNAL_PROVIDERS or f"{login}[bot]" in EXTERNAL_PROVIDERS:
            continue
        body = comment.get("body")
        if not isinstance(body, str):
            continue
        if _DEFERRAL_RE.search(body) and _TRACKED_RE.search(body):
            return True
    return False


def _resolved_comment_ids(pr_number: int) -> set[int]:
    """Every review-comment id sitting on a **resolved** thread (#393).

    ``review_owed`` could previously be cleared only by moving the head, and for a *non-blocking*
    finding the contract forbids exactly that: ``docs/agents/review.md`` and ``.agents/tasks/
    amend.md`` say to defer it to one follow-up issue and resolve the thread with the link — **no
    push**. So the response the contract prescribes cleared nothing, ``agent:needs-amend`` survived
    a completed answer, and the launcher kept issuing AMEND sessions until it hit its cap.

    Resolution is the signal because it is the one the contract's own instruction produces and the
    only one a machine can read. The follow-up *link* is contract, not payload — nothing here can
    tell a link to a real issue from a plausible one — so this keys on ``isResolved`` and says so.

    **Fails closed by raising, not by answering "nothing resolved".** Both would keep every finding
    owed, and the difference is #388's: a swallowed transport error becomes a *verdict* about work
    nobody read. Silently returning an empty set restores the exact bug this fixes, invisibly and
    forever, while a raise is the same treatment ``_paginate_or_raise`` already gives an unreadable
    review list one call above — an unreadable review state fails the job rather than being guessed
    at. A partial page is refused for the same reason: unread threads would report answered findings
    as unanswered, and a short read is indistinguishable from a short list.
    """
    owner, _, name = REPO.partition("/")
    resolved: set[int] = set()
    cursor: str | None = None
    for _page in range(claim.MAX_PAGES):
        try:
            data = claim._graphql(
                _RESOLVED_THREADS,
                {"owner": owner, "name": name, "number": pr_number, "cursor": cursor},
                f"PR #{pr_number} review threads",
            )
        except claim.ClaimError as exc:
            # Wrapped for the same reason `_paginate_or_raise` wraps: `main` catches `TriageError`
            # and reports it as a triage failure, so a bare `ClaimError` would escape as a
            # traceback rather than as the module's own error.
            raise TriageError(f"PR #{pr_number} review-thread state could not be read") from exc
        # Every level is REQUIRED, not defaulted. `or {}` down this chain would turn a null
        # `repository`, `pullRequest` or `reviewThreads` into an empty page and then into "nothing
        # is resolved" - which succeeds, keeps every finding owed, and so restores the bug this
        # function exists to fix, silently and permanently. That is the fail-open shape #388 names:
        # an unreadable answer becoming a verdict about work nobody read.
        threads = ((data.get("repository") or {}).get("pullRequest") or {}).get("reviewThreads")
        nodes = threads.get("nodes") if isinstance(threads, dict) else None
        page = threads.get("pageInfo") if isinstance(threads, dict) else None
        if not isinstance(nodes, list) or not isinstance(page, dict):
            raise TriageError(
                f"PR #{pr_number} review-thread state came back malformed; refusing to read it as "
                "'nothing is resolved', which would keep answered findings owed forever"
            )
        for node in nodes:
            if not isinstance(node, dict) or not node.get("isResolved"):
                continue
            comments = node.get("comments")
            inner = comments.get("nodes") if isinstance(comments, dict) else None
            if not isinstance(inner, list):
                raise TriageError(
                    f"PR #{pr_number} has a resolved review thread whose comments could not be "
                    "read; refusing to decide what it answered"
                )
            # A thread with more comments than one page is refused rather than half-read: a finding
            # past the slice would stay owed after its thread was resolved, which is this bug one
            # level down. Threads that long are rare enough that failing loudly costs little.
            #
            # The paging level is REQUIRED, like every other. `(… or {}).get("hasNextPage")` read an
            # absent `pageInfo` as `False` and judged the thread from whatever came back — which is
            # the half-read this refuses, arriving through the check meant to prevent it. The query
            # always selects the connection, so an absent one means an unreadable payload rather
            # than a short thread (CodeRabbit on #405).
            inner_page = comments.get("pageInfo")
            if not isinstance(inner_page, dict):
                raise TriageError(
                    f"PR #{pr_number} has a resolved review thread whose comment paging could not "
                    "be read; refusing to judge resolution from a thread that may be truncated"
                )
            if inner_page.get("hasNextPage"):
                raise TriageError(
                    f"PR #{pr_number} has a resolved review thread with more comments than this "
                    "query reads; refusing to judge resolution from part of a thread"
                )
            if not _carries_a_deferral(inner):
                continue
            for comment in inner:
                if not isinstance(comment, dict):
                    continue
                # `fullDatabaseId` is a GraphQL BigInt and arrives as a STRING, where the REST `id`
                # compared against it is an int. Normalising here rather than at the comparison
                # keeps the set one type.
                raw = comment.get("fullDatabaseId")
                if isinstance(raw, str) and raw.isdigit():
                    resolved.add(int(raw))
                elif isinstance(raw, int):
                    resolved.add(raw)
        if not page.get("hasNextPage"):
            return resolved
        cursor = page.get("endCursor")
        if not isinstance(cursor, str) or not cursor:
            break
    raise TriageError(
        f"PR #{pr_number} has more review threads than this query will walk; refusing to judge "
        "resolution from a partial read, which would report ANSWERED findings as unanswered and "
        "re-issue AMEND sessions against deferrals that are already complete"
    )


def _paginate_or_raise(pr_number: int, path: str, what: str) -> list[dict[str, Any]]:
    try:
        return claim._paginate(path, what)
    except claim.ClaimError as exc:
        raise TriageError(f"PR #{pr_number} review state could not be read") from exc


def _reply_wrapper_ids(comments: list[dict[str, Any]]) -> set[Any]:
    """Review ids whose every comment is a reply — the wrappers, positively identified (#396).

    Built as *"owns comments, and all of them are replies"* rather than *"owns a non-reply
    comment"*, and the difference is the whole safety argument. The second spelling asks a
    submission to **prove** it is a review, so anything the payload does not describe fully — a
    review that carried no inline comments at all — would drop out of the count. This spelling asks
    a submission to prove it is a **wrapper**, so the unproven case is counted.

    That is the direction ADR-0062 requires: an unreadable timeline counts everything, because a
    safety control fails toward counting. Undercounting rounds is the fail-open direction on the
    cap; overcounting merely strands a pull request, which is visible.
    """
    replies: dict[Any, bool] = {}
    for entry in comments:
        review_id = entry.get("pull_request_review_id")
        if review_id is None:
            continue
        replies[review_id] = replies.get(review_id, True) and bool(entry.get("in_reply_to_id"))
    return {review_id for review_id, only_replies in replies.items() if only_replies}


def _is_a_review(entry: dict[str, Any], wrappers: set[Any]) -> bool:
    """Whether a review submission is a review, or the wrapper GitHub puts around a reply (#396).

    Answering a review thread produces a submission of its own: empty body, state ``COMMENTED``,
    and one comment carrying ``in_reply_to_id``. Nothing on the reviews payload alone distinguishes
    it from a real review, which is why the caller joins the comment list.

    A body or a verdict settles it without the join. A body is what every substantive review writes
    — #385's real review was 5667 bytes and all five of its wrappers were 0 — and
    ``CHANGES_REQUESTED`` says something whether or not it is spelled out, while an empty-bodied
    ``APPROVED`` is still a pass. Only when both are absent does the join decide, and then only
    against submissions proven to be wrappers.
    """
    if (entry.get("body") or "").strip():
        return True
    if entry.get("state") in VERDICT_REVIEW_STATES:
        return True
    return entry.get("id") not in wrappers


def _substantive_review_ids(comments: list[dict[str, Any]]) -> set[Any]:
    """Review ids owning at least one comment of their own - the POSITIVE spelling.

    Deliberately not the complement of :func:`_reply_wrapper_ids`, and a separate function so the
    two directions stay legible side by side. That one asks a submission to prove it is a
    **wrapper**, so the unproven case counts as a review - the fail-closed direction on the cap.
    This one asks a submission to prove it **said something**, so the unproven case is not evidence
    - the fail-closed direction on the gate. One payload, two axes, opposite safe answers.
    """
    return {
        entry["pull_request_review_id"]
        for entry in comments
        if entry.get("pull_request_review_id") is not None and not entry.get("in_reply_to_id")
    }


def _proves_the_gate(entry: dict[str, Any], spoke: set[Any]) -> bool:
    """Whether this submission is the gate provider reporting cleanly at this head.

    Two questions, in this order, and the order is the fix for #415.

    **Is the state one that can mean a review happened?** An allowlist - see
    ``GATE_PROVING_STATES``. Asking the complement instead ("not ``CHANGES_REQUESTED``") admitted
    ``DISMISSED``, an absent state, and every state GitHub has yet to invent.

    **Did the payload show it actually reported?** A body is what every substantive review writes -
    #385's real review was 5667 bytes and all five of its wrappers were 0 - and a submission owning
    a non-reply comment has plainly reported. ``APPROVED`` skips this: it is a verdict in itself.
    Anything else is a submission the payload does not describe, and the gate does not accept those.

    The two must both hold. Either alone is a defect this function has already had: state-only
    admitted a dismissal, evidence-only admitted the body a dismissal leaves behind.
    """
    if entry.get("state") not in GATE_PROVING_STATES:
        return False
    if entry.get("state") == GATE_VERDICT_STATE:
        return True
    return bool((entry.get("body") or "").strip() or entry.get("id") in spoke)


def _gate_state(
    *,
    gate_blocked: bool,
    past_the_draft: bool,
    owed: bool,
    running: bool,
    converged: bool,
) -> str:
    """``blocked`` | ``satisfied`` | ``open`` — what the mandatory gate is doing (#399).

    Reported rather than acted on: nothing here writes a label for ``satisfied``, because the lane
    ending is the absence of work rather than a state to publish. What it changes is the run
    summary, which an operator reads to decide the next step.

    ``satisfied`` is deliberately narrow, and it is read off ``converged`` — *a clean CodeRabbit
    review at this exact head* — rather than off the round evidence. **The two are not
    interchangeable, and using the rounds was a real defect.** ``reviewed`` holds the heads where a
    metered provider found something *blocking*; answering a finding moves the head, so by the time
    the lane is capped every SHA in that set is stale by construction. It is also provider-blind. A
    capped PR whose two rounds were fixed but which never asked for the verification would therefore
    have reported its mandatory gate satisfied with no clean review in existence, and a blocking
    *Greptile* round would have satisfied a *CodeRabbit* gate (CodeRabbit on #408).

    So the gate asks for the thing ADR-0062 actually requires and nothing adjacent to it: that
    provider, that head, no findings.

    **``past_the_draft`` asks the question directly, where ``capped`` used to stand in for it
    (#411).** What the gate needs to exclude is the DRAFT phase: a clean review there ends nothing,
    because the Greptile step and the ready transition are still to come, and reporting the gate
    satisfied would invite arming a merge on an unfinished lane — the failure
    ``.agents/tasks/amend.md`` warns about, one step earlier.

    ``capped`` did exclude it, because draft rounds do not count and a draft is therefore never
    capped. It also excluded a PR whose FIRST CodeRabbit review came back clean — which has met
    ADR-0062's gate, since the cap is a ceiling and not a quota — and reported ``open`` for it. The
    run summary then told an operator the lane was unfinished when it was finished, which is an
    invitation to spend a metered credit nobody needed.

    The caller resolves the phase from ``_counted_from(strict=True)`` and hands the answer down as a
    plain bool, so an unreadable timeline arrives here as ``False`` and reports ``open``. That
    matters more than which helper is called: ``_counted_from`` answers ``None`` both for *opened
    ready* and for *could not read*, and reusing that ``None`` here with the opposite polarity is a
    live fail-open CodeRabbit found on #407.
    """
    if gate_blocked:
        return "blocked"
    if past_the_draft and not owed and not running and converged:
        return "satisfied"
    return "open"


#: CodeRabbit renders a fixed three-field italic header on every inline finding:
#: ``_<domain>_ | _<severity>_ | _<effort>_``. The capture is anchored on the **second** field, and
#: that is the whole point rather than an implementation detail: `docs/agents/review.md` says in as
#: many words that the *domain* label and the ``cr-indicator-types:`` marker are **not** severities
#: and never promote a finding - ``potential_issue`` sits on ``Minor`` and ``Major`` alike. Reading
#: field one, or grepping the body, would do exactly what that rule forbids.
_CODERABBIT_SEVERITY = re.compile(
    r"\A_[^_\n]*_\s*\|\s*_[^_|\n]*?(Critical|Major|Minor|Trivial)[^_|\n]*?_\s*\|"
)

#: Greptile renders a P-scale badge as an image whose ``alt`` text is the severity.
#: `docs/agents/review.md`: *"its badges use the same P-scale as Codex, so they map straight
#: across"*.
_GREPTILE_SEVERITY = re.compile(r'<img\s+alt="(P\d)"')

#: Each metered provider's severity scale, **most severe first**, paired with the floor
#: `docs/agents/review.md` names for it. Codex is deliberately absent: it is the unmetered lane and
#: can never spend a round, so parsing its badge would be dead code. Only these two logins reach
#: :func:`_finding_is_blocking`.
#:
#: **The ordering is stated so the floor can be derived, and that is the fix for a real fail-open**
#: (Greptile ``P1`` on #424). A floor means *at or above*, and the first version of this enumerated
#: the blocking severities instead - which read Greptile's ``P0``, the level ABOVE the floor, as
#: below it, and dropped the provider's most severe finding from the round count. Enumerating
#: happened to be right for CodeRabbit and wrong for Greptile, which is exactly the kind of accident
#: a derived value cannot have.
SEVERITY_SCALES: dict[str, tuple[tuple[str, ...], str]] = {
    "coderabbitai[bot]": (("Critical", "Major", "Minor", "Trivial"), "Major"),
    "greptile-apps[bot]": (("P0", "P1", "P2", "P3"), "P1"),
}


def _finding_is_blocking(entry: dict[str, Any]) -> bool:
    """Whether an inline finding is at or above the severity floor, per the provider's own badge.

    **Unreadable markup counts.** A provider that changes its rendering, a body this cannot parse, a
    login not in the table - all take the counting answer, which is the direction the cap has failed
    in since it was written. The cost of over-counting is a pull request capped early, which is
    visible and recoverable; the cost of under-counting is an unbounded metered review budget, and
    that is the expensive direction: #408 accumulated **nine** heads carrying findings against a
    cap of two, and every one of those nine carried a genuinely blocking finding.

    So this narrows the count **only where a provider has stated a severity the scale places below
    the floor** and leaves every ambiguous case exactly where it was. A level the scale does not
    describe is ambiguous in that sense: ``P4`` looks less severe than ``P3`` and might be, but
    guessing an order for a level nobody wrote down is how ``P0`` came to be read as *below*
    ``P1`` in the first place.
    """
    login = ((entry.get("user") or {}).get("login")) or ""
    known = SEVERITY_SCALES.get(login)
    if known is None:
        return True
    scale, floor = known
    body = entry.get("body")
    if not isinstance(body, str):
        return True
    pattern = _CODERABBIT_SEVERITY if login == GATE_PROVIDER else _GREPTILE_SEVERITY
    found = pattern.search(body)
    if found is None:
        return True  # rendering changed, or this is not a badged finding - count it
    severity = found.group(1)
    if severity not in scale:
        return True  # a level this scale does not place - count it rather than rank it
    return scale.index(severity) <= scale.index(floor)


def _is_blocking(entry: dict[str, Any], is_reviews: bool) -> bool:
    """Whether this piece of metered evidence found something, and so spends a round (#399).

    **A converged round is free.** ADR-0062's gate requires *"at least one CodeRabbit review with no
    actionable comments"*, and its cap allows two rounds — and those two rules could contradict each
    other. If the round-2 review posts actionable comments, answering them moves the head, and the
    gate then requires a review at *that* head, which would be round 3. The cap forbids it. #385
    reached exactly that state: green on all 16 checks, 54 threads resolved, ``mergeStateStatus:
    CLEAN``, and unmergeable by the lane's own text.

    So the cap counts what it was always for - *how many times a provider has found something that
    had to be fixed* - and a review that finds nothing is not a round but the lane **terminating**.
    After two blocking rounds a worker may therefore always ask once more to verify convergence:
    clean satisfies the gate at no cost, and blocking again pushes the count past ``CAP``, which
    publishes ``agent:gate-blocked`` rather than leaving the PR green and stuck. The lane is bounded
    at three metered reviews and every path out of it is a state something can act on.

    Blocking is *actionable output at the head reviewed*: a ``CHANGES_REQUESTED`` submission, or an
    inline finding **the provider itself badged at or above the severity floor**. A submission with
    a body and no findings is CodeRabbit's clean form and is exactly what the gate asks for. A reply
    is neither (#396), and is filtered before this is asked.

    **The severity is read, not inferred from the comment's shape (#409).** Every inline finding
    used to count, which is an approximation in the over-counting direction - safe on the cap, and
    expensive: measured across this repository, 58% of CodeRabbit's inline findings are ``Minor``,
    which `docs/agents/review.md` says to **defer** rather than fix. Two pull requests reached
    ``agent:review-capped`` having been told nothing they were obliged to act on (#405 spent 4 such
    rounds, #402 spent 2). Reading the badge is not a heuristic here: both metered providers emit a
    fixed machine-readable severity, and 154 of 154 non-reply findings across thirteen pull requests
    parsed.
    """
    if not is_reviews:
        return _finding_is_blocking(entry)
    return entry.get("state") in BLOCKING_REVIEW_STATES


def _review_state(
    pr_number: int, head: str, counted_from: str | None = None
) -> tuple[set[str], bool, bool, bool]:
    """``(heads that spent a round, whether the CURRENT head owes an answer, read, converged)``.

    The last two both look like *"a review happened"* and are not interchangeable — with each other
    or with ``heads``. #394 and #399 landed one each, and this is where they meet.

    ``read`` is *any* external provider having looked at the current head, counted phase or not. It
    is not a third counter: it answers a question neither of the others can, which is whether a
    **clean** review has happened at all (#394). ``heads`` counts only metered,
    post-``ready_for_review`` evidence, so on a draft it is empty whatever Codex has done — and
    ``owed`` is false both when a review came back clean and when none was ever asked for. Those two
    states authorise opposite things, so telling them apart needs its own value.

    ``converged`` is the mandatory gate itself: **a clean CodeRabbit review at this head** (#399).
    Tracked apart from ``heads`` because the two answer opposite questions — the round set is *where
    a provider found something*, the gate is *where one found nothing* — and apart from ``read``
    because that one is satisfied by any provider at all. **A Codex thumbs-up is a ``read`` and is
    not a gate**, which is why ``converged`` keys on ``GATE_PROVIDER`` and ``read`` deliberately
    does not: substituting a free provider for the metered gate is exactly what ADR-0062 forbids.
    See :func:`_gate_state` for what conflating a round with the gate did.

    ``counted_from`` is the instant the cap starts (see :func:`_counted_from`). Evidence older than
    it still decides whether the current head **owes an answer** — a finding does not stop mattering
    because it arrived on a draft — but it does not consume a round.

    Both sources carry ``commit_id``: submitted reviews and inline review comments. Inline comments
    are included because a provider can post findings with no submission wrapper, and missing one
    of those would undercount a round that really happened.

    **The head is ``original_commit_id`` when there is one.** GitHub *mutates* ``commit_id`` on an
    inline review comment as the pull request advances: the comment is carried forward onto the new
    head and re-pointed at it, with ``updated_at`` unchanged, so nothing looks edited. Reading
    ``commit_id`` therefore counted one review twice — once at the head the provider read, once at
    the head that ANSWERED it — which capped a pull request the moment it responded to its first
    round (#307, seen live on #304). Submitted reviews have no ``original_commit_id`` and are
    unaffected.

    The second value is what makes an *ordinary* blocking review issue AMEND authority. Keying only
    on a failed check suite left a real hole: a provider requesting changes at a **green** head
    produced no authority at all, so the launcher never started the session that answers the review
    — on the workflow whose whole job is to publish that authority. A head owes an answer when an
    external provider left **unresolved** inline findings on it, or submitted ``CHANGES_REQUESTED``
    for it. A clean pass leaves nothing owed, and pushing a fix moves the head, so the next head
    owes nothing until a provider looks at it.

    **Resolution clears a finding; a push is not the only exit** (#393). Pushing used to be the only
    one, and for a *non-blocking* finding the contract forbids exactly that — defer it to one
    follow-up issue and resolve the thread with the link, **no push** — so the prescribed answer
    cleared nothing and the launcher kept issuing AMEND sessions until it hit its cap. See
    :func:`_resolved_comment_ids`, including why an unreadable answer still owes.

    **A reply is not a review** (#396). GitHub wraps a bot's reply to a review thread in a *review
    submission* — empty body, state ``COMMENTED``, carrying the reply as its only comment — so
    counting every submission made answering a review consume the round needed to answer the next
    one, and a 2-round cap behaved like a 1-round cap. Measured on #385: one real review, five
    wrappers. The reviews endpoint carries no comments, so the two payloads are joined on
    ``pull_request_review_id`` and a submission counts only when it has a body, a verdict state, or
    a comment of its own that is not an ``in_reply_to_id``.

    **The reply filter stops at the round axis, and that asymmetry is the point.** It is tempting to
    reuse it on the owed axis, where a provider's acknowledgement reads as a fresh finding at the
    head that answered it — but a threaded reply is not *reliably* an acknowledgement. A provider
    answering "that only half fixes it" writes it in the same shape, and dropping it would leave the
    head owing nothing while real feedback sat unanswered. Over-counting a round costs a metered
    review; under-owing merges past a finding. So this axis fails toward owing, matching the rest of
    the module. The signal that actually separates *handled* from *unhandled* is whether the thread
    was resolved, which is absent from these REST payloads and arrives with #393.

    This is the *sibling* of the ``commit_id`` rewrite :func:`_reviewed_head` fixes, not a second
    layer on it. Both make one review look like two, and they are independent: that one moves a
    real finding onto the head that answered it (wrong *head*), this one turns the answer itself
    into evidence (wrong *count*). Neither subsumes the other, so both filters run — the head is
    normalised first, then the entry is asked whether it is a review at all.
    """
    reviews = _paginate_or_raise(
        pr_number, f"/repos/{REPO}/pulls/{pr_number}/reviews", "review list"
    )
    comments = _paginate_or_raise(
        pr_number, f"/repos/{REPO}/pulls/{pr_number}/comments", "review-comment list"
    )

    wrappers = _reply_wrapper_ids(comments)
    spoke = _substantive_review_ids(comments)
    # Only asked when something might actually be cleared by it. A pull request with no external
    # inline FINDING at the current head cannot have an answered one, so the extra query buys
    # nothing there - and `clear_mirror` and the round-label paths run on every triage event.
    #
    # Deliberately NOT filtered by `in_reply_to_id`, and an earlier revision of this predicate was.
    # That filter was correct only while the main loop skipped replies on the owed axis too; #396
    # removed that, because a threaded reply can carry a finding. Keeping it here would be worse
    # than redundant - a head whose only external comment is a reply would owe, skip the read that
    # could clear it, and owe forever.
    answerable = any(
        entry.get("id")
        for entry in comments
        if ((entry.get("user") or {}).get("login")) in EXTERNAL_PROVIDERS
        and _reviewed_head(entry) == head
    )
    resolved = _resolved_comment_ids(pr_number) if answerable else set()

    heads: set[str] = set()
    owed = False
    # The gate, tracked as two halves that must both hold: the provider submitted at THIS head, and
    # it left no finding here. A `COMMENTED` submission is CodeRabbit's clean form, so the
    # submission alone proves nothing — the absence of findings beside it is the actual signal.
    gate_review_here = False
    gate_finding_here = False
    # Seeded from the issue-comment channel, because that is where Codex's CLEAN verdict lands.
    # A dirty Codex pass submits a review; a clean one posts *"Codex Review: Didn't find any major
    # issues"* as a plain issue comment, or reacts 👍 — neither of which carries `commit_id`, so the
    # loop below can never see it (Codex P1 on #407). The draft that most deserves to advance is
    # exactly the one that produced no review object at all.
    #
    # Deliberately NOT seeding the gate the same way: that channel is where the FREE provider
    # answers, and a gate satisfiable by Codex is no gate at all.
    read_head = _verdict_at_head(pr_number, head)
    for entries, is_reviews in ((reviews, True), (comments, False)):
        for entry in entries:
            login = ((entry.get("user") or {}).get("login")) or ""
            sha = _reviewed_head(entry)
            if login not in EXTERNAL_PROVIDERS or not isinstance(sha, str) or not sha:
                continue
            # An unsubmitted draft is not a review on ANY axis - not a round, and nothing is owed an
            # answer to a finding its author has not sent yet. See `UNSUBMITTED_REVIEW_STATE`.
            if is_reviews and entry.get("state") == UNSUBMITTED_REVIEW_STATE:
                continue
            # A reply is not a *review*. On the comment axis `in_reply_to_id` says so outright; on
            # the review axis it takes the join, because the wrapper GitHub builds around a reply
            # looks exactly like a review from the reviews payload alone.
            if is_reviews:
                is_reply = not _is_a_review(entry, wrappers)
            else:
                is_reply = bool(entry.get("in_reply_to_id"))
            # A REPLY IS NOT A LOOK, and set before this filter it said one had happened. Answering
            # is exactly what lands on a *new* head: a submitted review carries no
            # `original_commit_id`, so the wrapper GitHub builds around a provider's reply binds to
            # whatever the head is now (#396). The lane would advance out of a state nobody had
            # reviewed — the requirement in `_advance_state` inverted by the evidence meant to
            # satisfy it.
            #
            # Masked today by `owed`, which counts replies deliberately (#404) and which
            # `_advance_state` refuses on. Unmasked the moment #393 lands and a resolved thread
            # stops owing, so it is fixed here rather than left for that PR to inherit.
            if sha == head and not is_reply:
                read_head = True
            # Two independent axes, and conflating them is how this went wrong twice.
            #
            # ROUNDS: only a metered provider, only after the PR went ready, and never a reply.
            # Draft-phase evidence is free by design, and Codex never counts at all.
            #
            # OWED: any external provider, at any time, *including* a reply. A finding does not stop
            # mattering because it arrived on a draft, nor because the provider wrote it inside an
            # existing thread; a paid Greptile review that nothing answers is the worst case. This
            # is where the reply filter deliberately stops — see the docstring.
            #
            # A round is a metered review that found something BLOCKING (#399). A clean one is a
            # convergence verification: it satisfies the gate, so charging it a round would make
            # the gate and the cap contradict each other - see `_is_blocking`.
            #
            # All three conditions narrow the ROUND axis and none of them touches OWED. They compose
            # rather than overlap: a reply is not a review at all, a draft round is free, and a
            # clean round is the lane terminating.
            when = entry.get("submitted_at") if is_reviews else entry.get("created_at")
            if (
                not is_reply
                and login in METERED_PROVIDERS
                and _counts_as_round(when, counted_from)
                and _is_blocking(entry, is_reviews)
            ):
                heads.add(sha)
            if sha != head:
                continue
            # The gate is asked at the current head only, and the two halves fail in opposite
            # directions on purpose. A *reply wrapper* is not the verification (#396), so it cannot
            # prove the gate, and it does not void it either — **both halves skip replies, and the
            # asymmetric version of this was a defect.** Voiding on any threaded comment looked like
            # the safe direction (#404: a provider writing "that only half fixes it" inside its own
            # thread is a finding wearing an acknowledgement's shape), but it made an ordinary
            # CodeRabbit acknowledgement at the current head retract a gate that was already
            # satisfied — permanently, since nothing clears it but moving the head, and a converged
            # PR has nothing left to push. That is #399's own deadlock, rebuilt by the fix for it.
            #
            # The reply question belongs to `owed`, which counts replies deliberately and for
            # exactly that reason, and which `_gate_state` already requires to be false. So this
            # axis asks only what ADR-0062 asks — *actionable comments* — and a reply is not one.
            #
            # **The gate is RESOLUTION-BLIND where `owed` below is resolution-aware, deliberately.**
            # #393 made a resolved finding stop owing, because a deferral answered it. It does not
            # make the review that carried it clean: ADR-0062's gate is *a CodeRabbit review with no
            # actionable comments*, and tidying the thread afterwards does not retroactively remove
            # the comment from that review. So a deferred-and-resolved finding still needs a fresh
            # review at this head before the gate is met — one more free review under #399's rule,
            # never a deadlock, because a non-blocking round costs nothing to ask again.
            #
            # The asymmetry is the fail-closed direction on the axis that decides merges: reading
            # `resolved` here would let the author satisfy the mandatory gate by resolving threads,
            # which is precisely the substitution the gate exists to refuse.
            if login == GATE_PROVIDER and not is_reply:
                if not is_reviews or entry.get("state") in BLOCKING_REVIEW_STATES:
                    gate_finding_here = True
                elif _proves_the_gate(entry, spoke):
                    # POSITIVE EVIDENCE, where the round axis above deliberately takes the absence
                    # of it. `_is_a_review` counts a submission with no body, no verdict and no
                    # comments, because it asks a submission to prove it is a *wrapper* - and on the
                    # cap, counting the unproven case is fail-closed. On the gate the identical
                    # submission would set `converged` with nothing showing that CodeRabbit reported
                    # anything at all, which is fail-OPEN on the axis that decides merges
                    # (CodeRabbit on #408). Same payload, opposite safe directions, so the two axes
                    # ask opposite questions of it.
                    gate_review_here = True
            if is_reviews:
                # A CHANGES_REQUESTED submission is a SEPARATE signal, and resolving threads does
                # not clear it (#393's third criterion). It is a verdict on the pull request rather
                # than a comment on a line, so it is withdrawn by the provider re-reviewing, not by
                # the author tidying the threads underneath it.
                if entry.get("state") in BLOCKING_REVIEW_STATES:
                    owed = True
            elif entry.get("id") not in resolved:
                owed = True
    return heads, owed, read_head, gate_review_here and not gate_finding_here


def advance_step_token(pr: dict[str, Any]) -> str:
    """A token that changes when the lane's NEXT STEP changes, and not otherwise (#394).

    The launcher keys its one-session ref on this. Keying on the head and a coarse draft/ready
    phase was too blunt: the lane needs several one-step sessions at one head - a Codex-clean draft
    spends a Greptile credit, the clean Greptile result then marks the PR ready, a ready PR asks
    CodeRabbit and a later session arms the merge once that comes back clean. All four can share a
    head, so the second of any pair collided on `422` and the lane stranded (Codex P2 on #407).

    What separates them is the EVIDENCE: each step is unlocked by a provider verdict that was not
    there before. Counting distinct provider submissions at this head therefore advances exactly
    when the next step does, and stays put when two launchers race on the same state - which is the
    duplicate-session case the ref exists to refuse.

    **Raises rather than guessing the phase.** ``_counted_from`` answers ``None`` both for a PR
    opened ready and for a timeline it could not read, which is the right answer for the cap and
    the wrong one for a key: a transient failure would write ``ready-*`` where a successful read
    writes ``draft-*``, giving one state two ref names and the ref would stop being a mutex. The
    caller refuses instead, which costs a launcher cycle and never a duplicate session.
    """
    number = int(pr["number"])
    head = str((pr.get("head") or {}).get("sha") or "")
    phase = "draft" if _counted_from(pr, strict=True) is _COUNT_NOTHING else "ready"
    # NO FALLBACK on an unreadable review list, and an earlier revision had one: it returned the
    # coarse `phase` on `ClaimError`, reasoning that a collision stalls one step visibly rather than
    # duplicating a session. That reasoning was wrong, and checkably so. The coarse token `draft`
    # keys the prefix `…-draft-`, which `…-draft-1-1` STARTS WITH - so a launcher with an unreadable
    # endpoint counts the successful launcher's ref, takes `…-draft-2`, and creates a ref that
    # collides with nothing. Two names, one lane state, two workers, and a Greptile credit possibly
    # spent twice (CodeRabbit on #407).
    #
    # It also made the caller's own guard dead code: `_authorise_advance` wraps this call to convert
    # a read failure into a refusal, and the swallow meant that `except` could never fire for the
    # one failure it was written for. Propagating is what makes the pair coherent.
    #
    # The reply filter runs here for the same reason it runs in `_review_state`, and leaving it out
    # was a live defect: answering a thread makes GitHub wrap the reply in a `COMMENTED` submission
    # of its own (#396). The reply carries `original_commit_id` of the head it was written against
    # while the wrapper carries the CURRENT `commit_id`, so an answer to an OLD finding increments
    # this token — the lane's next step is unchanged, but the ref prefix moves, `_authorise_advance`
    # finds nothing at the new name, and launches the step a second time. One reply, one duplicate
    # session, and a Greptile credit possibly spent twice (CodeRabbit on #407).
    wrappers = _reply_wrapper_ids(
        claim._paginate(f"/repos/{REPO}/pulls/{number}/comments", "review-comment list")
    )
    seen = 0
    for entry in claim._paginate(f"/repos/{REPO}/pulls/{number}/reviews", "review list"):
        login = ((entry.get("user") or {}).get("login")) or ""
        if (
            login in EXTERNAL_PROVIDERS
            and _reviewed_head(entry) == head
            and _is_a_review(entry, wrappers)
        ):
            seen += 1
    return f"{phase}-{seen}"


def _advance_state(
    *,
    labels: set[str],
    read_head: bool,
    armed: bool,
    counted_from: str | None,
    owed: bool,
    running: bool,
    capped: bool,
    gate_blocked: bool,
    add: list[str],
    remove: list[str],
) -> str:
    """Publish, hold or clear the authority to advance an unfinished lane (#394).

    **Unfinished, not merely draft.** The stranded draft is the incident that found this, but the
    conditions below turn on what step is left rather than on the draft flag: a ready pull request
    whose gate has not been asked for is stranded in the same way, and only *ready and armed* is
    the lane actually complete.

    The lane is a **sequence**, and until now the swarm had one resumption signal for it:
    ``agent:needs-amend``, published when a check failed or a finding was owed. A clean review owes
    nothing, so it published nothing; ``swarm_slots`` resumes claimed work only on that label; and
    the draft sat forever, before the CodeRabbit gate it cannot merge without. The pre-ADR-0062
    contract had no such gap because it was single-shot - open ready, request, arm auto-merge, exit
    - and auto-merge did the waiting. The lane replaced that with phases auto-merge cannot perform.

    Every condition below is a way the authority would otherwise be wrong, and each is #394's:

    * **The lane must be unfinished**, and only *ready and armed* is finished. A counted-phase pull
      request whose gate has not been asked for, or has passed with the merge not yet armed, still
      has a step left and is authorised. ``_counted_from`` reporting the draft sentinel is exactly
      *"no ``ready_for_review`` has ever happened"*, and what that answer decides is which rules
      apply — the cap binds in the draft phase, where a round really is spent — not whether the
      lane is over.
    * **Nothing may be owed, and no AMEND may still be published.** An owed finding is still an
      AMEND. Publishing both would hand one claim two authorities and let the resumption that
      arrives first decide which. ``owed`` alone does not cover it: the label is a *published*
      state that outlives the finding, because the capped branch above returns before the one that
      clears a stale one, and the module docstring forbids this function clearing it instead — that
      label has a second writer. So the presence of the label is checked directly, and the advance
      waits for whoever owns the amend to finish (CodeRabbit on #407).
    * **The checks must be green and settled.** Advancing means spending a metered credit or asking
      the mandatory gate, and neither is worth doing against a diff that is still moving.
    * **A review must actually have happened at this head.** Otherwise a freshly opened draft would
      be authorised to leave the draft phase before the free provider had ever looked at it, which
      is the lane's whole point skipped.
    * **Not past the cap.** Nothing is authorised past it, by any route.
    * **Not gate-blocked**, which is #399 meeting #394 and belongs to neither branch alone. That
      label means *the convergence verification came back blocking too, so no automatic state
      remains* — and an advance is automatic state. ``owed`` covers the moment the finding lands
      and stops covering it the instant the author answers and resolves the thread (#393), which is
      exactly when a session would be dispatched to walk a lane that has stopped terminating.
      Checked on the recomputed ``rounds > CAP``, not on the label, so it cannot be cleared by
      anyone tidying labels.

    The label is *published*, not consumed. ``swarm_slots`` takes the ref that makes it exactly one
    session; the label alone re-triggering would be an unbounded supply of them.
    """
    # `not in remove` is load-bearing, and leaving it out re-created the very bug this function
    # exists to fix. `labels` is the snapshot read at the start of the run, so on the run that
    # RETIRES the amend — the finding was answered, the fix pushed, the suite came back green — the
    # label is in `remove` and still in `labels`. Withholding on the snapshot alone therefore
    # withheld the advance on exactly the run that made it due, and nothing fires afterwards to
    # publish it: clearing a label is not an event. The draft strands before the gate, which is
    # #394. So the question is whether the amend STANDS, not whether it was there when we looked.
    if owed or running or (AMEND_LABEL in labels and AMEND_LABEL not in remove):
        return _withdraw_advance(labels, remove, "not-eligible")
    # #399's terminal state, which #394's branch could not see and #399's had nothing to withhold
    # from. `gate_blocked` says a maintainer decides from here, and publishing advance authority
    # beside it would dispatch a session to walk a lane that has stopped terminating - past the cap,
    # so with no round to spend on the review that step would ask for.
    if gate_blocked:
        return _withdraw_advance(labels, remove, "gate-blocked")
    # The cap bounds ROUNDS, and the counted phase's remaining steps are not rounds. A PR whose
    # gate has passed with both rounds spent still needs a session to arm the merge, and one that
    # has answered round CAP still needs to request the convergence check ADR-0062 permits - a
    # review that finds nothing is the lane terminating, not a third round. Withholding here left
    # such a PR green, gated and unmergeable with nobody authorised to finish it (Codex P2 on
    # #407).
    #
    # A `capped and counted_from is _COUNT_NOTHING` guard used to follow, for the capped-DRAFT case.
    # It is gone, and #419 - which asked for a test of it - is closed by removing it rather than by
    # pinning it, under that issue's own third criterion: *if the state turns out to be unreachable
    # in practice, the branch is what changes.* It was reachable only because `_counts_as_round`
    # asked about malformed data before it asked about the phase, so a timestamp-less metered review
    # capped a draft (#423). With the phase asked first that cannot happen: `_counted_from` returns
    # `_COUNT_NOTHING` only for a PR that is currently a draft and has NEVER been ready - a
    # ready->draft excursion returns the ready instant, so rounds persist - and against that
    # sentinel nothing counts, so `rounds` is 0 and `capped` is False. The two conditions are now
    # mutually exclusive by construction.
    # A REVIEW MUST HAVE HAPPENED AT THIS HEAD, in every phase. The lane only ever advances out of
    # a state a provider has looked at: on a draft that is Codex coming back clean, and past it the
    # head has not moved since - marking a PR ready pushes nothing - so the same evidence is still
    # there. Requiring it unconditionally is also what closes a fail-open, which is why it is not
    # phase-specific: `_counted_from` answers `None` BOTH for a PR that was opened ready and for a
    # timeline it could not read, so a phase-specific test read an API failure as "past the draft"
    # and published authority for a pull request nobody had reviewed. `None` is deliberately the
    # fail-toward-counting answer on the ROUND axis; it is not evidence of anything on this one.
    if not read_head:
        return _withdraw_advance(labels, remove, "no-review-yet")
    if counted_from is not _COUNT_NOTHING and armed:
        # PAST THE DRAFT AND ARMED is the one state needing no further session. Reading merely
        # `ready for review` as "the lane is complete" stranded it one step further along than
        # before (Codex P1 on #407): an ADVANCE worker marks the PR ready and exits, and the
        # remaining steps - ask CodeRabbit, then arm once it comes back clean - are exactly the
        # ones auto-merge cannot perform for itself.
        return _withdraw_advance(labels, remove, "lane-complete")
    if ADVANCE_LABEL in labels:
        return "unchanged"
    add.append(ADVANCE_LABEL)
    return "added"


def _withdraw_advance(labels: set[str], remove: list[str], reason: str) -> str:
    """Take the advance authority back when its preconditions stop holding.

    Unlike ``agent:needs-amend`` this label has exactly one writer, so removing it here cannot
    fight the reaper over a signal that means something else. It is safe to withdraw and it must
    be: a stale one would authorise a session to advance a lane that has moved on.
    """
    if ADVANCE_LABEL in labels:
        remove.append(ADVANCE_LABEL)
        return f"cleared-{reason}"
    return reason


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


#: Removals whose failure must be reported rather than swallowed, because they are the whole change
#: rather than the tail of an add. Clearing `agent:needs-amend` is the only one: it retracts the
#: launcher's authority to start a session, so a silently failed DELETE leaves a PR that owes
#: nothing still advertising that it owes an AMEND, and the launcher keeps spending sessions on it.
# Both are launcher AUTHORITY, so a removal that silently fails leaves a session authorised against
# state that no longer justifies it. `agent:needs-advance` joins for exactly the reason
# `agent:needs-amend` is here: `_withdraw_advance` fires when a finding is owed, checks are running,
# the cap is reached or the lane completed, and in every one of those a surviving label starts an
# ADVANCE against a PR that is no longer eligible for one (Codex P2 on #407).
CHECKED_REMOVALS = frozenset({AMEND_LABEL, ADVANCE_LABEL})


def _apply(number: int, add: list[str], remove: list[str], *, dry_run: bool) -> None:
    """Write the label delta. Adds are fatal on failure; most removals are best-effort.

    **ORDER IS THE SAFETY PROPERTY: add first, remove second.** Removing first meant a failed
    ``agent:review-capped`` POST left the PR with *neither* the old round label nor the cap — a
    window in which the launcher reads an uncapped PR carrying AMEND authority and issues a third,
    while this run exits non-zero and looks like it changed nothing. With additions first, the same
    failure leaves the previous round label in place and the next event simply retries.

    An add that silently fails publishes state that is not true, so it raises. A *superseded* round
    label — replaced by the higher one just added — leaves a stale marker if its removal fails,
    which is cosmetic against that standard and is recomputed next event.

    ``CHECKED_REMOVALS`` is the exception, and an earlier revision of this docstring got it wrong:
    it claimed "every removal this function makes is paired with an add". Clearing
    ``agent:needs-amend`` is not — it is the whole change, and it *retracts authority*. Swallowed,
    it leaves a pull request that owes nothing still advertising that it owes an AMEND, and the
    launcher keeps starting sessions against a permanent cap. Reported rather than assumed. Success
    is ``DELETE_DONE``, the set the merge path already uses, ``404`` included: a label that is not
    there is the end state asked for.
    """
    if dry_run:
        return
    if add:
        status, _ = claim._request("POST", f"/repos/{REPO}/issues/{number}/labels", {"labels": add})
        if status != 200:
            raise TriageError(
                f"#{number} needs {', '.join(add)} but the write failed (HTTP {status}); "
                "not reporting state that was never published"
            )
    for label in remove:
        status, _ = claim._request("DELETE", f"/repos/{REPO}/issues/{number}/labels/{label}", None)
        if label in CHECKED_REMOVALS and status not in DELETE_DONE:
            # Label-neutral: `CHECKED_REMOVALS` holds two labels now, and naming only the AMEND one
            # made the failure misdescribe the state whenever `agent:needs-advance` was the label
            # that would not clear — on the message an operator reads to decide what to repair
            # (CodeRabbit on #407).
            raise TriageError(
                f"#{number} no longer holds the authority {label} grants, but it could not be "
                f"removed (HTTP {status}); leaving it would keep issuing the session this run "
                "just retracted"
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

    # ONE timeline read, TWO polarities, and they are separated here rather than inferred from the
    # answer (#411). `_counted_from` returns `None` both for a PR opened ready and for a timeline it
    # could not read; that ambiguity is safe for the CAP, which wants "count everything" either way,
    # and fail-OPEN for the gate, which would read the unreadable case as *past the draft* (Codex P2
    # on #407). So the failure is caught once, where it is still distinguishable, and each axis is
    # given the answer that fails closed for it.
    try:
        counted_from = _counted_from(pr, strict=True)
        phase_read = True
    except (claim.ClaimError, TriageError):
        counted_from, phase_read = None, False
    past_the_draft = phase_read and counted_from is not _COUNT_NOTHING
    reviewed, review_owed, read_head, converged = _review_state(pr["number"], head, counted_from)
    rounds = len(reviewed)
    running, failed = _suite_state(head)
    capped = rounds >= CAP
    # PAST the cap, not at it. At the cap the lane can still terminate: one convergence
    # verification is allowed and a clean one satisfies the gate at no cost (#399). Past it,
    # that verification came back blocking too, so no automatic state remains.
    gate_blocked = rounds > CAP
    # Either reason owes the same single AMEND session: CI to fix, or a review to answer.
    owed = failed or review_owed

    add: list[str] = []
    remove: list[str] = []

    # Round labels are mutually exclusive and only ever escalate. Monotonic on purpose: heads cannot
    # be rewritten here (the ruleset forbids force-push and non-fast-forward), so a count that fell
    # would ordinarily mean a read failed - and stepping a PR back from capped to round-1 would
    # re-authorise a round the contract already spent.
    #
    # ADR-0062 changed what these labels MEAN, and there is deliberately no automatic migration for
    # labels written under the old semantics. Three versions of one were tried and each was worse
    # than the last, because a recount of zero is ambiguous in a way no predicate here can resolve:
    # it means "never spent" for a stale label, and "the evidence was deleted" for a real round that
    # a metered provider left as wrapper-less inline comments. Clearing on zero refunds the second
    # case - fail-OPEN on the cap, which is the one thing this module exists to hold. The migration
    # is therefore an operational step, run once against a repository where it was verified empty;
    # see ADR-0062. A stale label is removed by hand, which is a command, where a wrong automatic
    # clear is silent.
    target = _round_label(rounds)
    held = [name for name in ALL_ROUND_LABELS if name in labels]
    highest_held = max((ALL_ROUND_LABELS.index(name) for name in held), default=-1)
    if target is not None and ALL_ROUND_LABELS.index(target) > highest_held:
        add.append(target)
        remove += [n for n in held if n != target]

    # The whole mechanism: past the cap no AMEND authority is issued, so no third round can start.
    # An existing marker is left alone - see the module docstring on the two writers.
    #
    # PAST the cap, not AT it, and the difference is what makes #399's convergence check reachable.
    # An AMEND is not a round: a round is a metered REVIEW, and this authorises the session that
    # ANSWERS one. Withholding at `rounds == CAP` meant the round-2 review's own findings could
    # never be fixed - so the lane could not reach the "everything answered, everything pushed"
    # state the convergence check requires, and the change that exists to un-deadlock the gate
    # deadlocked it one step earlier (CodeRabbit on #408). Past `CAP` the convergence check itself
    # came back blocking, and then there is genuinely nothing left to authorise.
    if gate_blocked:
        amend = "gate-blocked"
    elif owed and AMEND_LABEL not in labels:
        add.append(AMEND_LABEL)
        amend = "added"
    elif not owed and not running and AMEND_LABEL in labels:
        remove.append(AMEND_LABEL)
        amend = "cleared"
    else:
        amend = "unchanged"

    if gate_blocked and GATE_BLOCKED_LABEL not in labels:
        add.append(GATE_BLOCKED_LABEL)

    advance = _advance_state(
        labels=labels,
        read_head=read_head,
        armed=bool(pr.get("auto_merge")),
        counted_from=counted_from,
        owed=owed,
        running=running,
        capped=capped,
        gate_blocked=gate_blocked,
        add=add,
        remove=remove,
    )

    _apply(issue, add, remove, dry_run=dry_run)
    return {
        "action": "triage",
        "pr": pr["number"],
        "issue": issue,
        "head": head,
        "rounds": rounds,
        "capped": capped,
        # Three states, not two. `open` covers "the gate is still ahead of this PR"; `blocked` is
        # the convergence check having come back blocking. `satisfied` is the third, and leaving it
        # out made the run summary tell an operator that "one convergence check is still permitted"
        # on a PR whose gate had just been met - an invitation to spend a second one, which is a
        # stop-list violation (CodeRabbit on #408). Clean evidence is free, so `rounds` and `capped`
        # look identical either side of it; the distinguisher is `converged` - nothing owed at a
        # head **`GATE_PROVIDER`** has reviewed cleanly. Not *a metered provider*, which is what
        # this comment said until CodeRabbit caught the same conflation it had already caught once
        # in this file: Greptile is metered and cannot satisfy this gate, and
        # `test_a_greptile_round_does_not_satisfy_the_coderabbit_gate` is what holds that.
        "gate": _gate_state(
            gate_blocked=gate_blocked,
            past_the_draft=past_the_draft,
            owed=owed,
            running=running,
            converged=converged,
        ),
        "checks": "running" if running else ("failed" if failed else "green"),
        "review_owed": review_owed,
        "amend": amend,
        "advance": advance,
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
    parser.add_argument(
        "--merged",
        action="store_true",
        help="clear a merged claim's label mirror instead of publishing round state",
    )
    args = parser.parse_args()
    if args.merged and args.pr is None:
        parser.error("--merged needs --pr: a merged PR is identified by number, not by branch")
    try:
        result = (
            clear_mirror(number=args.pr, dry_run=args.dry_run)
            if args.merged
            else triage(number=args.pr, branch=args.branch, dry_run=args.dry_run)
        )
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
