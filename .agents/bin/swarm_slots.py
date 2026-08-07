#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 The Tether Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fill worker slots, and be the only thing that ever issues a review round.

``AGENTS.md`` §Review gate: *"every AMEND is a fresh short-lived session whose task text the
launcher injects with an explicit ``ROUND = N of 2``; past the cap it injects none, so no worker
ever holds authority for a third."* This is that launcher. Without it the sentence describes
nothing.

**Why this is correctness, not throughput.** On #276 the review loop ran inside one long agent
session that kept deciding to ask again — 9 rounds against a cap of 2. Under the peer model a worker
is short-lived: it claims, works, pushes, arms auto-merge and exits, so every subsequent AMEND is a
new session whose entire task text somebody else writes. That is the whole reason the cap can bind:

    The cap binds because the launcher is the only issuer of AMEND turns, and the launcher counts.

``triage.py`` withholds ``agent:needs-amend`` at the cap, so no authority is *published*. This
refuses to issue one past the cap, so no authority is *acted on* even when a label is wrong — and
the labels can be wrong in the fail-open direction, because that counter only sees head-bound review
evidence and can undercount.

**The second refusal has to count something triage does not, or it is not a second refusal.** The
first version of this file read ``_rounds_spent`` from triage's labels and called that independent;
it was not — it was the same undercountable number read twice, so an undercount would have passed
both checks and issued a third session. Both reviewers caught it on #287.

So the launcher counts **its own issuances**, which is the thing ``AGENTS.md`` actually says it
counts. Each AMEND is claimed by creating ``refs/amend-rounds/<issue>-<generation>-<n>``: ``201`` to
the first writer, ``422`` to every other. That is monotonic (refs here are never deleted), atomic
(so two launchers cannot both issue round *n*), independent of every label, and keyed to the claim
generation so a reaped-and-reclaimed issue starts fresh. The refusal fires on ``max(label count,
issued refs)``, which can only ever be *stricter* than the contract's cap, never looser.

**Eligibility is never reimplemented here.** ``_dispatch_build`` calls ``claim._cmd_claim`` itself
rather than repeating its steps, so the eligibility gate, the 422 race, the generation read and the
label mirror are literally the same code an agent would run by hand. A second copy would be a second
thing to keep in step and the first divergence would be silent — the argument that put
``_scope_hash`` in exactly one place in #280.

**Printing is the default.** ``--print`` emits ready-to-run launch commands with the claim already
taken. *"GitHub is the coordinator; there is no coordinator agent"* is satisfied by a launcher that
prints, and printing keeps two genuine unknowns — headless ``claude -p`` auth, and ``/mnt/c``
performance under WSL — off the critical path instead of blocking on them. ``--spawn`` exists and is
interlocked behind ``TETHER_SPAWN_OK``; see ``_spawn``.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

BIN = Path(__file__).resolve().parent


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(f"tether_{name}", BIN / f"{name}.py")
    if spec is None or spec.loader is None:  # pragma: no cover - packaging accident
        raise SystemExit(f"error: {name}.py is missing next to swarm_slots.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# reaper is imported for `_claim_refs`, and `claim` is taken THROUGH it rather than loaded again.
# Loading claim.py twice would create two module objects, so a test patching one transport would
# leave the other live - the kind of half-faked test that passes for the wrong reason.
reaper = _load("reaper")
claim = reaper.claim
# triage supplies the label vocabulary, the cap, and - since #391 - the phase predicate the draft
# exemption keys on. Sharing the constants means the two halves of the cap cannot disagree about
# which labels they are talking about, and sharing the predicate means they cannot disagree about
# which phase a pull request is in.
#
# `_load` builds a fresh module, so triage arrives holding a SECOND `claim` object. That was
# harmless while triage was only read; it is not now that `_in_draft_phase` calls into it, because a
# second transport is a second thing to authenticate and a second thing a test must patch - and a
# half-patched test is the failure this file's own reaper/claim comment above exists to prevent. So
# the one transport is pushed in, and a test pins that all three modules share it.
triage = _load("triage")
triage.claim = claim

REPO = claim.REPO
CAP = triage.CAP
AMEND_LABEL = triage.AMEND_LABEL
ADVANCE_LABEL = triage.ADVANCE_LABEL
CAPPED_LABEL = triage.CAPPED_LABEL
#: The terminal state, and since #399 the only published label that withholds AMEND authority here.
#: `agent:review-capped` bounds metered REVIEWS; an AMEND is the session that answers one and is not
#: itself a review. See `_plan`.
GATE_BLOCKED_LABEL = triage.GATE_BLOCKED_LABEL
ROUND_LABELS = triage.ROUND_LABELS

# `agent:human` is reserved for the maintainer; `needs:split` means the issue is over its diff
# budget and must be broken up before anyone works it. Both are refusals, not deferrals.
EXCLUDING_LABELS = frozenset({"agent:human", "needs:split"})
BLOCKED_PREFIX = "blocked-by:"
PRIORITY_ORDER = ("priority:P0", "priority:P1", "priority:P2")

WSL_REPO = "/mnt/c/Users/bioed/Documents/smfret-references/Tether"
CODEX_CLI = r"%APPDATA%\npm\codex.ps1"
GATE = r".agents\bin\gate.ps1"

# The interpreter each lane can actually resolve, because `_inner_command` puts the lanes in
# different shells: `claude` inside WSL bash, where Ubuntu ships `python3` and no `python` at all,
# and every other lane in native PowerShell, where the python.org installer registers `python`. The
# templates take this as `{{PYTHON}}` rather than naming an interpreter themselves - one template
# reaches both shells, so the name has to come from whoever knows the lane, which is this file
# (#382). There is deliberately no fallback: see :func:`_lane_python`.
LANE_PYTHON = {"claude": "python3", "codex": "python", "copilot": "python"}
# `gh` resolves from `PATH` in both shells, so this is one value rather than a table. It is injected
# anyway so that no template ever spells a tool path, which is the half of #382 that stranded #327
# and #334: a worker that cannot run `gh` cannot arm auto-merge.
LANE_GH = "gh"

# Deliberately NOT under refs/tags/: hatch-vcs derives the package version from tags, so a
# non-version tag makes `pip install -e .` fail and turns main red - the trap ADR-0057 records for
# the ADR reservations. A custom namespace is the same compare-and-swap and invisible to every tag
# consumer.
AMEND_NAMESPACE = "amend-rounds"

# A SEPARATE namespace, and that is what makes advancing cost no round (#394's third criterion).
# The mechanism is the AMEND ledger's - `POST /git/refs` is the mutex, 201 to the winner and 422 to
# everyone after, keyed to the claim generation - but a lane advance is not a review round, and
# putting it in `amend-rounds` would spend one to move a PR from one phase to the next.
ADVANCE_NAMESPACE = "lane-advances"

#: How many times one lane STEP may be attempted at one head before the launcher stops serving it.
#: A runaway stop, not a cap - see `ADVANCE_ATTEMPTS`'s use in `_authorise_advance`. A step ends by
#: producing the evidence that moves `advance_step_token`; a step whose provider is throttled or
#: silently suppressing produces none, so without a bound the launcher would either relaunch forever
#: or (keyed on the step alone) refuse forever. Three covers an ordinary throttle-and-wait and is
#: small enough that a genuinely stuck gate reaches a person quickly.
ADVANCE_ATTEMPTS = 3

# The draft phase's refs live under the same namespace with a `draft-` ordinal prefix, so both
# ledgers are enumerated by one `matching-refs` call and neither can be mistaken for the other
# (#391). ADR-0062 makes the draft phase uncapped - Codex iterates freely until nothing blocking
# remains - and `triage.py` counts accordingly, but this launcher keeps a SECOND, independent cap in
# permanent generation refs, deliberately, so that either counter can bind. It had no notion of
# draft state, so two draft iterations exhausted it while triage correctly reported zero rounds, and
# the advertised unlimited loop stalled here with no label saying why.
#
# The phase is written INTO THE REF NAME rather than re-derived when the ledger is read. A ref is
# immutable once created, so `<issue>-<gen>-draft-2` records what was true when that session was
# issued; reading `draft` at audit time would report today's answer about yesterday's decision, and
# a PR that has since gone ready would make its own draft history retroactively count.
#
# A ref written BEFORE this prefix existed is therefore read as counted-phase, and that is correct
# rather than a missing migration: it was created under the old semantics, where every AMEND counted
# against the cap whatever the pull request's state. Verified before this merged - the repository
# holds exactly one `refs/amend-rounds/*` ref, `252-38550159308-1`, belonging to an issue that is
# closed and merged, and **zero** live `agent/issue-*` claims. A generation is a server-assigned,
# strictly increasing activity id, so a reclaim can never reuse that key either. Nothing live can
# misread, and a future change to this naming needs the same check.
DRAFT_ORDINAL_PREFIX = "draft-"

#: A runaway stop, NOT the review cap - the two are different things and conflating them would undo
#: this change. `CAP` bounds how much metered review a PR may buy; this bounds how many times the
#: launcher will relaunch the same free session before concluding that nothing is progressing.
#: Deliberately far above any real draft phase (#385, the largest, took eleven Codex rounds), so it
#: binds only when `agent:needs-amend` is stuck - which the reaper's stale-PR path can do on a draft
#: with nothing to fix. Reaching it is reported, never silent.
DRAFT_CEILING = 20

EXIT_NO_WORK = 3


class SlotError(RuntimeError):
    """A launcher precondition failed. Safe to print; carries no local path."""


def _labels(issue: dict[str, Any]) -> set[str]:
    return {label["name"] for label in issue.get("labels", []) if isinstance(label, dict)}


def _rounds_spent(labels: set[str]) -> int:
    """How many review rounds this issue has already spent, from the published round labels.

    Read from the labels rather than recounted: ``triage.py`` owns the count, and a launcher that
    re-derived it could disagree with the state the contract publishes. ``agent:review-capped``
    is the terminal value, so it reports ``CAP`` and the caller refuses.
    """
    if CAPPED_LABEL in labels:
        return CAP
    spent = [i + 1 for i, name in enumerate(ROUND_LABELS) if name in labels]
    return max(spent, default=0)


def _issued_amends(number: int, generation: int) -> int:
    """How many AMEND sessions have already been issued for this exact claim.

    The launcher's **own** count, and the only part of the cap that does not depend on a label.
    Keyed to the generation so a reaped-and-reclaimed issue starts fresh rather than inheriting a
    spent cap.

    Fails closed. Reading a failure as zero would hand out a fresh session at the cap, which is the
    one outcome the whole mechanism exists to prevent. ``404`` from ``matching-refs`` is the
    ordinary "nothing matches" answer and genuinely means zero.

    """
    return len(_amend_ordinals(number, generation, draft=False))


def _amend_ordinals(number: int, generation: int, *, draft: bool) -> list[str]:
    """The AMEND refs already taken for this claim, in one phase or the other (#391).

    One ``matching-refs`` call covers both ledgers; the ``draft-`` prefix on the ordinal separates
    them. Splitting here rather than at the call site keeps the two counts derived from one read, so
    they cannot disagree about what exists.

    Fails closed for the same reason :func:`_issued_amends` does: reading a failure as zero would
    hand out a fresh session at the cap.
    """
    prefix = f"{AMEND_NAMESPACE}/{number}-{generation}-"
    status, refs = claim._request("GET", f"/repos/{REPO}/git/matching-refs/{prefix}")
    if status == 404:
        return []
    if status != 200 or not isinstance(refs, list):
        raise SlotError(
            f"#{number} issued-AMEND count could not be read (HTTP {status}); refusing to guess it"
        )
    ordinals = []
    for entry in refs:
        # A malformed entry is a FAILED READ, not an absent round. Skipping one would make a bad
        # ledger response look like fewer issued rounds, and the only thing that count protects
        # against is issuing one too many - so it fails closed exactly as a non-list response does.
        name = entry.get("ref") if isinstance(entry, dict) else None
        _, marker, ordinal = (
            name.partition(f"refs/{prefix}") if isinstance(name, str) else ("", "", "")
        )
        if not marker or not ordinal:
            raise SlotError(
                f"#{number} has an AMEND ref this launcher cannot parse ({name!r}); refusing to "
                "count a ledger it does not fully understand"
            )
        if ordinal.startswith(DRAFT_ORDINAL_PREFIX) is draft:
            ordinals.append(ordinal)
    return ordinals


def _in_draft_phase(branch: str) -> bool:
    """Whether this claim's pull request is still in the **uncapped** draft phase (#391).

    Derived from ``triage._counted_from``, not from a second reading of ``draft``, and that is the
    point rather than an economy. It is the same predicate the round labels come from, so
    ``agent:review-capped`` and this launcher's refusal cannot disagree about which phase a pull
    request is in - and it keys on the *first* ``ready_for_review`` event, so entering the counted
    phase is permanent and a worker cannot refund a spent round by toggling back to draft.

    **Fails closed, twice over.** No pull request yet, or one this cannot read, is treated as the
    counted phase, so the cap binds. The uncapped phase has to be positively established: a wrong
    answer in that direction is an unbounded issuance budget, which is the one outcome the second
    counter exists to prevent.

    **"Cannot read" includes MALFORMED, not just unreachable**, and the first version of this
    caught only the two transport errors. A payload that arrives and is the wrong *shape* raises
    `KeyError`/`TypeError`/`ValueError`/`AttributeError` out of `_pull_request`'s selection or
    `_counted_from`'s timeline walk - which escaped this function entirely, so the docstring above
    described a fail-closed guard that the code did not implement for the case most likely to
    produce one (CodeRabbit on #406). Both arms now return the same answer, kept separate because
    they mean different things: one is *GitHub did not answer*, the other is *GitHub answered
    something this does not understand*.
    """
    try:
        pr = triage._pull_request(number=None, branch=branch)
        if not isinstance(pr, dict):
            return False
        return triage._counted_from(pr) is triage._COUNT_NOTHING
    except (triage.TriageError, claim.ClaimError):
        return False
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def _take_amend_round(number: int, generation: int, ordinal: str, sha: str) -> bool:
    """Claim the right to issue AMEND round ``ordinal``. ``True`` if this launcher won it.

    ``POST /git/refs`` is the mutex, exactly as it is for the issue claim itself: ``201`` to the
    first writer and ``422 Reference already exists`` to every other. Without it two launchers
    inspecting the same ``agent:needs-amend`` issue would both reuse the existing claim and start
    two workers on one branch, generation and worktree.

    The target ``sha`` is provenance rather than meaning - it records which head the round was
    issued against. The ref *name* is the record.

    """
    ref = f"refs/{AMEND_NAMESPACE}/{number}-{generation}-{ordinal}"
    status, _ = claim._request("POST", f"/repos/{REPO}/git/refs", {"ref": ref, "sha": sha})
    if status == 201:
        return True
    if status == 422:
        return False
    raise SlotError(f"AMEND round {ordinal} for #{number} could not be claimed (HTTP {status})")


def _priority(labels: set[str]) -> int:
    for rank, name in enumerate(PRIORITY_ORDER):
        if name in labels:
            return rank
    return len(PRIORITY_ORDER)


def _open_ready() -> list[dict[str, Any]]:
    """Every open issue the queue could draw from, before eligibility.

    Paginated. Reading one page would make the queue a function of issue age rather than of
    priority, and on a backlog this size the oldest page is least likely to hold ready work.
    """
    items = claim._paginate(f"/repos/{REPO}/issues?state=open&labels=status:ready", "ready queue")
    return [item for item in items if isinstance(item, dict) and not item.get("pull_request")]


def _issue_now(number: int) -> dict[str, Any]:
    """Re-read one issue at the moment of an authoritative write. Raises rather than guessing."""
    status, issue = claim._request("GET", f"/repos/{REPO}/issues/{number}")
    if status != 200 or not isinstance(issue, dict):
        raise SlotError(f"#{number} could not be re-read (HTTP {status}); refusing to guess")
    return issue


def _amend_candidates(claimed: set[int]) -> list[dict[str, Any]]:
    """Issues whose claim is live and whose PR owes an AMEND session.

    These come from the CLAIM REFS rather than from ``status:ready``: a claimed issue has had
    ``status:ready`` removed by ``claim.py``'s label mirror, so a ready-only sweep would never
    see the work already in flight - which is exactly the work an AMEND continues.
    """
    out = []
    for number in sorted(claimed):
        status, issue = claim._request("GET", f"/repos/{REPO}/issues/{number}")
        if status != 200 or not isinstance(issue, dict):
            raise SlotError(f"#{number} could not be read (HTTP {status}); refusing to guess")
        if issue.get("state") != "open":
            continue
        issue["number"] = number
        out.append(issue)
    return out


def _terminal_refusal(number: int, spent: int, priority: int) -> dict[str, Any]:
    """The refusal `agent:gate-blocked` produces, in the one shape both callers report.

    The FAST one, from the published labels - not the authoritative one, which is the launcher's
    own issuance count in `_authorise_amend`. This exists so a terminal issue costs no further API
    calls, and it is reported rather than skipped: a launcher passing over it quietly would look
    just like one with no work.

    IT KEYS ON `agent:gate-blocked`, NOT ON THE CAP, and that is #399 (CodeRabbit on #408).
    Refusing at `spent >= CAP` rebuilt the deadlock this PR exists to remove, one layer up:
    `triage.py` publishes `agent:needs-amend` AT the cap so round-2's findings can be answered - a
    round is a metered REVIEW, and an AMEND is the session that answers one - and the launcher then
    refused to issue it. The lane could never reach the *everything answered, everything pushed*
    state the convergence check requires, so the change that un-deadlocks the gate deadlocked it one
    step earlier. Past the cap the convergence check found something too, and then there is
    genuinely nothing left to authorise; that is what this label means and it is the only thing that
    stops it.
    """
    return {
        "issue": number,
        "mode": "refuse",
        "label_rounds": spent,
        "reason": (
            f"{GATE_BLOCKED_LABEL} is published for this claim: the convergence review ADR-0062 "
            f"allows past the {CAP}-round cap found blocking work too, so no AMEND authority may "
            "be issued. Safety-class findings escalate to the maintainer; the rest become "
            "follow-ups."
        ),
        "priority": priority,
    }


def _plan(*, slots: int, vendor: str) -> list[dict[str, Any]]:
    """Decide what to launch, refusing rather than skipping where the contract says refuse.

    AMEND before BUILD, deliberately: finishing an open pull request is worth more than starting a
    new one, and a PR left mid-review is what the reaper eventually has to clean up.
    """
    claimed = set(reaper._claim_refs())
    plan: list[dict[str, Any]] = []

    for issue in _amend_candidates(claimed):
        labels = _labels(issue)
        number = int(issue["number"])
        spent = _rounds_spent(labels)
        # ADVANCE IS TESTED BEFORE THE CAP, and the order is the fix rather than a preference. The
        # cap bounds ROUNDS, and the steps this label authorises at the cap are not rounds: a PR
        # whose gate has passed with both rounds spent still needs one session to arm the merge,
        # and `triage._advance_state` deliberately keeps the label alive for it. With the refusal
        # first, the launcher answered "at the cap" and started nothing, stranding a gated, green,
        # mergeable PR with nobody authorised to finish it (Codex P1 on #407). Triage is the thing
        # that decides eligibility; a launcher that re-decides it from a different rule is how the
        # two came to disagree.
        # THE TERMINAL LABEL IS TESTED FIRST, ahead of both authorities. `_advance_state` withholds
        # the advance on `gate_blocked` and clears a stale label, but that happens on a triage run,
        # and between the review that blocked the gate and the run that clears it both labels are
        # published at once. Reading ADVANCE first launched a session to walk a lane that has
        # stopped terminating (CodeRabbit on #408). Nothing is authorised past this point.
        if GATE_BLOCKED_LABEL in labels:
            plan.append(_terminal_refusal(number, spent, _priority(labels)))
            continue
        if ADVANCE_LABEL in labels and AMEND_LABEL not in labels:
            # A clean review on an unfinished draft (#394). Not an AMEND: there are no blocking
            # findings to fix, and a session told to fix them would either invent work or stop.
            # AMEND wins if both are somehow present - answering a finding always precedes moving
            # the lane on, and triage does not publish both, so this is a belt on a state that
            # should not occur.
            plan.append(
                {
                    "issue": number,
                    "mode": "advance",
                    "label_rounds": spent,
                    "reason": (
                        "agent:needs-advance is published for this claim: its draft is green, "
                        "owes nothing, and a review has come back clean, so the lane has a next "
                        "phase and nobody is walking it."
                    ),
                    "priority": _priority(labels),
                }
            )
            continue
        if AMEND_LABEL not in labels:
            continue
        # The ROUND is not decided here. `_authorise_amend` reads the launcher's own issuance count
        # and takes the round by compare-and-swap, because a plan built from labels alone would both
        # trust triage's undercountable number and let two launchers issue the same round.
        plan.append(
            {
                "issue": number,
                "mode": "amend",
                "label_rounds": spent,
                # Carried so `_authorise_amend` can apply the label-side bound without re-reading
                # the issue. False here by construction - the branch above returns on it - and
                # named rather than assumed, because the refusal message reads it.
                "gate_blocked": False,
                "reason": (
                    "agent:needs-amend is published for this claim: a check suite is not "
                    "green, or a review at the current head is unanswered."
                ),
                "priority": _priority(labels),
            }
        )

    for issue in _open_ready():
        labels = _labels(issue)
        number = int(issue["number"])
        if number in claimed:
            continue
        blocking = (labels & EXCLUDING_LABELS) | {n for n in labels if n.startswith(BLOCKED_PREFIX)}
        if blocking:
            continue
        plan.append({"issue": number, "mode": "build", "priority": _priority(labels)})

    # AMEND and refusals first, then priority, then issue number. The number is the tie-break rather
    # than API order so two launchers on the same queue agree on what they are competing for.
    order = {"refuse": 0, "lost": 0, "amend": 1, "advance": 2, "build": 3}
    plan.sort(key=lambda item: (order[item["mode"]], item["priority"], item["issue"]))

    # Refusals are reported however many there are; only launches consume a slot.
    launches = [item for item in plan if item["mode"] != "refuse"][:slots]
    refusals = [item for item in plan if item["mode"] == "refuse"]
    for item in launches:
        item["vendor"] = vendor
    return refusals + launches


def _dispatch_build(number: int, vendor: str, owner: str) -> dict[str, Any] | None:
    """Take the claim by calling ``claim._cmd_claim`` itself. Returns its record, or ``None``.

    Calling the command rather than repeating its body is the point: eligibility, the 422 race, the
    server-recorded generation and the label mirror stay in one implementation. It prints JSON and
    raises ``SystemExit`` with a named code, so stdout is captured and the exit is caught here.
    """
    args = argparse.Namespace(issue=number, vendor=vendor, owner=owner, base=None)
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            claim._cmd_claim(args)
    except SystemExit as exc:
        # 3 ineligible, 4 lost the race. Both mean no launch command and neither is an error:
        # losing a claim costs one wasted API call, which is the design's stated trade.
        if exc.code in (claim.EXIT_INELIGIBLE, claim.EXIT_LOST):
            return None
        raise
    return json.loads(out.getvalue())


def _existing_claim(number: int, owner: str) -> dict[str, Any]:
    """The record an AMEND needs for a claim that is already held."""
    generation = claim._generation(number)
    if generation is None:
        raise SlotError(f"#{number} has no live claim generation; refusing to resume it")
    return {
        "issue": number,
        "branch": f"{claim.BRANCH_PREFIX}{number}",
        "generation": generation,
        "base_sha": None,
        "owner": owner,
    }


def _body(task: Path) -> str:
    """The template minus its leading comment block, validated - **before any state is consumed**.

    The comment is stripped, not substituted. It carries the SPDX header and the maintainer's
    rationale, including the token syntax itself, and this text becomes resident context for the
    worker's whole session, so notes to whoever edits the template do not belong in it.

    Two ways a template renders to nothing, and the second is not the first with a different cause:

    * ``str.partition`` answers ``("<!-- ...", "", "")`` when the separator is absent, so tolerating
      a missing ``-->`` yields an empty remainder;
    * a *well-formed* comment that happens to be the whole file leaves nothing behind either.

    Both then pass the unsubstituted-placeholder guard, there being nothing left to be
    unsubstituted, and reach the worker as its entire task text.

    This is a separate function from :func:`_render` because *when* it runs is the point. It needs
    no record, so :func:`run` calls it on every template it may use before taking a claim or
    authorising a round - a malformed BUILD template would otherwise strand its claim until the
    reaper, and a malformed AMEND template would irrevocably consume one of the two rounds without
    ever launching a worker.
    """
    text = task.read_text(encoding="utf-8")
    if text.lstrip().startswith("<!--"):
        _, closed, remainder = text.partition("-->")
        if not closed:
            raise SlotError(f"{task.name} opens a comment block that is never closed")
        text = remainder
    text = text.lstrip("\n")
    if not text.strip():
        raise SlotError(f"{task.name} renders to nothing; refusing to inject an empty task")
    return text


def _lane_python(vendor: str) -> str:
    """The interpreter for this lane, or a refusal. **Never a guess.**

    A default here is worse than the ``KeyError`` it replaced (#387). Falling back to ``python3``
    hands a native lane the one name Windows may expose only as an unconfigured Store stub, and
    falling back to ``python`` hands WSL a name Ubuntu does not provide at all - so whichever
    spelling is chosen, an unknown vendor gets a task template whose commands do not run. That is
    #382 returning through the door built to close it, and it fails at the worker rather than here.

    ``SlotError`` because that is what every other refusal in this module raises and what
    :func:`main` catches to report exit 2; a bare ``KeyError`` escapes that handler mid-render.
    Argparse ``choices=claim.VENDORS`` makes this unreachable from the CLI, so it guards the
    in-process callers - ``_dispatch_build`` and the tests that build an ``item`` by hand.
    """
    try:
        return LANE_PYTHON[vendor]
    except KeyError:
        raise SlotError(
            f"no interpreter is registered for lane {vendor!r}; "
            f"LANE_PYTHON covers {sorted(LANE_PYTHON)}. Add the lane's interpreter rather than "
            "letting it inherit another shell's spelling."
        ) from None


def _render(task: Path, record: dict[str, Any], item: dict[str, Any]) -> str:
    """Substitute the task template. Every token must be consumed, or the worker reads a literal."""
    values = {
        "ISSUE": str(record["issue"]),
        "BRANCH": record["branch"],
        "BASE_SHA": str(record.get("base_sha") or "the claim ref's current tip"),
        "GENERATION": str(record["generation"]),
        "VENDOR": item["vendor"],
        "CAP": str(CAP),
        "ROUND": str(item.get("round", 0)),
        "REMAINING": str(item.get("remaining", CAP)),
        "REASON": item.get("reason", "a fresh build"),
        "PYTHON": _lane_python(item["vendor"]),
        "GH": LANE_GH,
    }
    text = _body(task)
    for key, value in values.items():
        text = text.replace(f"{{{{{key}}}}}", value)
    if "{{" in text:
        raise SlotError(f"{task.name} has an unsubstituted placeholder; refusing to inject it")
    if not text.strip():
        # Substitution can empty a template the body check passed - a file that is nothing but
        # tokens. The placeholder guard cannot see it either, because nothing is left to be
        # unsubstituted.
        raise SlotError(f"{task.name} renders to nothing; refusing to inject an empty task")
    return text


def _inner_command(record: dict[str, Any], item: dict[str, Any], task_file: Path) -> str:
    """The worker invocation for this lane, before the admission gate wraps it.

    The two lanes are genuinely different environments, which is a constraint rather than a detail:
    ``claude`` exists only inside WSL on this machine, and ``codex`` is a native PowerShell shim.
    WSL is already the established bridge here - the CodeRabbit CLI is WSL-only too.
    """
    worktree = f".claude/worktrees/{record['branch'].replace('/', '-')}"
    if item["vendor"] == "claude":
        inner = f'cd {WSL_REPO}/{worktree} && claude -p "$(cat {task_file.as_posix()})"'
        return f"wsl -e bash -lc {shlex.quote(inner)}"
    return f'& "{CODEX_CLI}" exec --cd "{worktree}" --task-file "{task_file}"'


def _wrapper(record: dict[str, Any], item: dict[str, Any], task_file: Path, target: Path) -> str:
    """Write the per-worker launch script and return the command that runs it.

    **The gate has to wrap the WORKER, not the launcher.** The launcher exits immediately, so it
    cannot hold a slot on a process it does not own - and the first version of this file built
    ``gate.ps1`` and then never invoked it, so the admission control protected nothing (#287).

    A generated script rather than a one-liner because the alternative is nesting a ``wsl -e bash
    -lc '...'`` inside a PowerShell ``-Command "..."``, and that quoting is exactly the kind of
    thing that is wrong in a way nobody notices until a worker silently fails to start. This is
    readable, and its contents are asserted by a test.

    The gate runs **natively** in both lanes: it guards this machine's RAM and slot count, which is
    a Windows-side resource even when the worker itself lives inside WSL.

    """
    inner = _inner_command(record, item, task_file)
    script = f"""# Generated by .agents/bin/swarm_slots.py - do not edit; regenerate instead.
# Holds one admission slot for the LIFETIME OF THE WORKER. Exit 4 means every slot is taken, 5 means
# the machine is below the free-RAM floor; in both cases nothing was started, so retry later.
$ErrorActionPreference = 'Stop'
& "{GATE}" -Acquire | Out-Null
if ($LASTEXITCODE -ne 0) {{ exit $LASTEXITCODE }}
try {{
    {inner}
}}
finally {{
    & "{GATE}" -Release
}}
"""
    target.write_text(script, encoding="utf-8")
    return f'powershell -NoProfile -ExecutionPolicy Bypass -File "{target}"'


def _spawn(command: str) -> None:
    """Interlocked, not disabled - and interlocked on purpose rather than shipped unproven.

    Two things about spawning are unmeasured on this machine: whether headless ``claude -p`` has
    usable auth in a non-interactive WSL shell, and whether a worktree under ``/mnt/c`` is fast
    enough to be worth it. Shipping a spawn that silently fails auth would produce empty sessions
    that look like workers, which is worse than printing. ``TETHER_SPAWN_OK=1`` is the single
    place to flip once both are answered.
    """
    if os.environ.get("TETHER_SPAWN_OK") != "1":
        raise SlotError(
            "--spawn is interlocked: set TETHER_SPAWN_OK=1 only after probing headless "
            "`claude -p` auth and /mnt/c worktree performance. Until then use --print, which "
            "takes the claim and emits the same command for a human or a shell to run."
        )
    subprocess.Popen(command, shell=True)  # noqa: S602 - command is built here, not user-supplied


def _authorise_advance(item: dict[str, Any], owner: str) -> dict[str, Any] | None:
    """Take the authority to advance this claim's lane by one phase, or return ``None`` (#394).

    **One session per attempt, and attempts are bounded — not "exactly one session per step".**
    This docstring asserted the stronger claim until Greptile read the two halves of the function
    together on #407, and the difference is load-bearing. A label is a *state*: it stays published
    until triage recomputes and withdraws it, so every launcher run in between would start another
    session against the same phase - several workers all spending the Greptile credit, or all
    marking the PR ready. So the label publishes the authority and this consumes it, by the same
    compare-and-swap the AMEND ledger uses: ``201`` to the winner, ``422`` to everyone after, keyed
    to the claim generation so a reclaim starts fresh.

    An attempt ORDINAL settled only the simultaneous race, and Greptile's ``P1`` on #407 is that
    gap: a launcher running *after* another's ref exists counts it, takes the next ordinal, and
    collides with nothing, so two same-generation workers run one step and can spend a metered
    credit twice. The ref is therefore keyed on :func:`_lane_state_digest` rather than a counter -
    *the lane state the previous session recorded* - which is the one signal here that distinguishes
    **reported back** from **still running**, and needs no lease, TTL or heartbeat (#412).

    **In its own namespace**, which is what makes advancing cost no round. Reusing
    ``amend-rounds`` would have spent one of the two metered rounds to move a pull request from one
    phase to the next - and #391 has just finished making that ledger mean one thing.

    Refs accumulate rather than stopping at one: a lane has several phases, and the worker that
    spends the Greptile credit is not the worker that marks the PR ready. **The review cap does not
    bind here** - none of these steps is a round - so what bounds a single step is
    ``ADVANCE_ATTEMPTS``, a runaway ceiling on refs sharing one prefix, and what bounds the lane is
    triage withdrawing the label the moment it has nowhere left to go. This paragraph said *"there
    is no cap here"* until CodeRabbit read it beside the ceiling three screens below.
    """
    number = item["issue"]
    record = _existing_claim(number, owner)
    generation = int(record["generation"])
    sha = reaper._read_ref(number)
    if sha is None:
        raise SlotError(f"#{number} claim ref vanished before its lane advance could be issued")
    # Keyed to the concrete lane STEP, from the same evidence triage used to publish the label.
    # Head-plus-phase was too coarse: several one-step sessions share a head - spend the Greptile
    # credit, then mark ready; ask CodeRabbit, then arm the merge - so the second of any pair
    # collided on `422` and stranded the lane (Codex P2 on #407).
    pr = triage._pull_request(number=None, branch=record["branch"])
    if not isinstance(pr, dict):
        raise SlotError(f"#{number} has no open pull request to advance")
    try:
        phase = triage.advance_step_token(pr)
    except (triage.TriageError, claim.ClaimError):
        # A step token this launcher cannot compute is not one it may guess at: a coarser fallback
        # keys the ref differently from the launcher that read successfully, and two names for one
        # state is two workers (CodeRabbit on #407).
        #
        # Reported as a REFUSAL rather than raised, because the failure is transient and local to
        # this claim. Raising would abort the whole run, so one flaky read on one pull request would
        # strand every other claim in the plan - and this costs nothing to retry, since no ref has
        # been taken and the next event recomputes it.
        item["mode"] = "refuse"
        item["reason"] = (
            f"#{number}'s lane step could not be determined - the review list at this head was "
            "unreadable - so no advance ref can be keyed to it. Nothing was consumed; the next "
            "triage event reissues the authority."
        )
        return None
    # AND TO AN ATTEMPT, because a step can fail to produce the evidence that ends it. The step
    # token only moves when a provider submission appears at this head, so the session that ASKS
    # CodeRabbit consumes `ready-0` before there is anything to see - and if that request is
    # throttled, refused, or silently suppressed, no review ever arrives, the token stays put, and
    # every later launcher collides on `422`. The mandatory gate would then be unretryable by
    # construction (Codex P1 on #407), which is not hypothetical: this repository's own lane sat in
    # exactly that state under CodeRabbit's adaptive limit.
    #
    # AND TO THE LANE STATE THE PREVIOUS ATTEMPT LEFT BEHIND, which is what makes the retry safe.
    # An ordinal derived from the refs that merely EXIST settles only the SIMULTANEOUS race: two
    # launchers at once compute the same number and the compare-and-swap gives the session to one of
    # them, but a launcher arriving LATER counts the winner's ref, takes the next ordinal, and
    # collides with nothing - so two same-generation workers run one step and can duplicate a
    # metered request (Greptile `P1` on #407).
    #
    # Keying the ref on a digest of the PR BODY closes it without a lease, a TTL or a heartbeat,
    # none of which `AGENTS.md` allows. `.agents/tasks/advance.md` requires every session to write
    # the lane state into that body before it exits - including the stop-and-record path - so the
    # digest moving IS the previous attempt reporting that it is over. While a worker is still
    # running the digest is unchanged, the ref name is identical, and the second launcher takes
    # `422` exactly as the simultaneous case does.
    #
    # It fails in the safe direction on the case it cannot see: a worker that dies WITHOUT recording
    # leaves the digest unmoved, so no retry is issued and the lane stalls visibly, wanting a
    # person. That is the direction every other control here takes, and it is the opposite of
    # spending a credit twice in silence.
    prefix = f"{ADVANCE_NAMESPACE}/{number}-{generation}-{sha[:12]}-{phase}-"
    taken = _advance_attempts(prefix)
    if taken >= ADVANCE_ATTEMPTS:
        item["mode"] = "refuse"
        item["reason"] = (
            f"#{number}'s {phase} lane step has been attempted {taken} times at {sha[:7]} without "
            f"producing the evidence that ends it, which is the {ADVANCE_ATTEMPTS}-attempt runaway "
            "ceiling rather than the review cap. A provider is refusing or silently suppressing "
            "the request; a maintainer decides, and no further session helps."
        )
        return None
    ref = f"refs/{prefix}{_lane_state_digest(pr)}"
    status, _ = claim._request("POST", f"/repos/{REPO}/git/refs", {"ref": ref, "sha": sha})
    if status == 422:
        item["mode"] = "lost"
        item["reason"] = (
            f"#{number}'s {phase} lane step is already held at {sha[:7]} - either another launcher "
            "took it just now, or a session is still working it and has not yet recorded a lane "
            "state to retry from"
        )
        return None
    if status != 201:
        raise SlotError(
            f"the {phase}-phase lane advance for #{number} could not be claimed (HTTP {status})"
        )
    # `round` is what the ledger has ALREADY spent, not a round being taken - an advance takes
    # none. Zero was the honest reading of "this session spends nothing" and a false reading of the
    # row it renders: a capped pull request was handed *"metered rounds spent 0 of 2"* directly
    # above *"rounds still available 0"*, two rows of one table contradicting each other, and the
    # first of them telling the worker the cap was untouched (CodeRabbit on #407). The template
    # says what this session spends in its own row, where it needs no number.
    item["round"] = item["label_rounds"]
    item["remaining"] = CAP - item["label_rounds"]
    return record


def _lane_state_digest(pr: dict[str, Any]) -> str:
    """A short digest of the pull request body — *the lane state the last session left* (#394).

    The advance ref is keyed on this so a retry becomes issuable only once something has reported
    back. `.agents/tasks/advance.md` makes writing the lane state the last thing every session does,
    stop-and-record paths included, so a moved digest means the previous attempt ended and an
    unmoved one means it has not.

    **Digest of the whole body, not of a parsed lane block.** Parsing would need the body to keep a
    fixed shape, and the body is prose a human also edits; a parser that silently found nothing
    would return one constant for every state and re-open the overlap this closes. Hashing
    everything means an unrelated edit merely permits one extra attempt - bounded by
    ``ADVANCE_ATTEMPTS`` - which is the harmless direction.

    Hex-truncated because it lands in a ref name: 12 characters over a body-sized input is far past
    the collision risk that matters here, where the only cost of a collision is one refused retry.
    """
    body = pr.get("body")
    return hashlib.sha256((body if isinstance(body, str) else "").encode("utf-8")).hexdigest()[:12]


def _advance_attempts(prefix: str) -> int:
    """How many sessions have already been issued for this exact lane step.

    Fails closed, like every other ledger read here: an unreadable answer reported as zero would
    mint an attempt the ceiling should have refused. ``404`` from ``matching-refs`` is the ordinary
    "nothing matches" answer and genuinely means none.
    """
    status, payload = claim._request("GET", f"/repos/{REPO}/git/matching-refs/{prefix}", None)
    if status == 404:
        return 0
    if status != 200 or not isinstance(payload, list):
        raise SlotError(
            f"the lane-advance ledger for {prefix} could not be read (HTTP {status}); refusing to "
            "treat an unreadable ledger as an empty one"
        )
    return len(payload)


def _authorise_amend(item: dict[str, Any], owner: str) -> dict[str, Any] | None:
    """Take AMEND authority for this issue, or return ``None`` having recorded why not.

    Three outcomes, and they are deliberately distinct:

    * **refuse** - the launcher's own issuance count has reached the cap. This is the independent
      refusal: it counts refs this launcher created, not a label another workflow wrote.
    * **lost** - another launcher claimed the same round first. Not an error and not a refusal.
    * authorised - ``item`` gains ``round``/``remaining`` and the caller proceeds.

    **The cap applies to the counted phase only** (#391). ADR-0062 makes the draft phase uncapped,
    and this counter had no notion of one: two draft iterations exhausted it while ``triage.py``
    correctly reported zero rounds, so the free Codex loop the lane advertises stalled here with no
    label saying why. A draft session still takes a ref - that ref is the mutex preventing two
    launchers from starting the same session, which is a separate job from counting - but under the
    ``draft-`` ordinal, which :func:`_issued_amends` does not count.

    ``item["phase"]`` records which ledger was used, so the report says so rather than leaving a
    reader to infer it from a round number of ``0``.
    """
    number = item["issue"]
    record = _existing_claim(number, owner)
    generation = int(record["generation"])
    draft = _in_draft_phase(record["branch"])
    item["phase"] = "draft" if draft else "counted"

    if draft:
        # ADR-0062 makes the draft phase uncapped: Codex is the unmetered lane and iterates until
        # nothing blocking remains. So the ref is still taken - it is the mutex that stops two
        # launchers issuing the same session, which is orthogonal to counting - but under the
        # `draft-` ordinal, where `_issued_amends` does not see it.
        spent = len(_amend_ordinals(number, generation, draft=True))
        # UNCAPPED IS NOT UNBOUNDED, and the difference is a live failure mode rather than caution.
        # `agent:needs-amend` has two writers: triage publishes it for an owed review, and
        # `reaper.sweep` publishes it for a STALE open pull request (reaper.py:466-471). A stale
        # draft carries it with nothing for a worker to fix, so each launcher run would mint another
        # `draft-N` ref, forever, and the label nobody clears would relaunch the same dead session
        # indefinitely (Codex P2 on #406). This ceiling never binds on real iteration - #385 took
        # eleven Codex rounds, the most any PR here has needed - so reaching it means the label is
        # stuck, which is the thing to report rather than to keep serving.
        if spent >= DRAFT_CEILING:
            item["mode"] = "refuse"
            item["reason"] = (
                f"{spent} draft AMEND sessions already issued for generation {generation}, which "
                f"is the {DRAFT_CEILING}-session runaway ceiling rather than the review cap. The "
                "draft phase is uncapped by design, so reaching this means `agent:needs-amend` is "
                "stuck - most likely the reaper's stale-PR marker on a draft with nothing to fix. "
                "A maintainer clears the label or closes the claim; no further session helps."
            )
            return None
        ordinal = f"{DRAFT_ORDINAL_PREFIX}{spent + 1}"
        round_number, remaining = 0, CAP
    else:
        issued = _issued_amends(number, generation)
        # `max` of the two counters, so EITHER can cap it. That makes the launcher's bound at worst
        # as strict as the contract's and never looser - the property the first version failed to
        # have, and one the draft split above must not weaken: it changes which refs are counted,
        # never how the count is compared.
        # `max` of the round labels and this count is what the first version compared, and since
        # #399 that conflates two different things. `label_rounds` counts metered REVIEWS; this
        # counts the SESSIONS THAT ANSWER THEM, and at the cap exactly one of the latter is still
        # due - the one that fixes round-2's findings so the convergence check has something clean
        # to verify. Comparing against the review count refused it, which is the deadlock #399
        # removes from `triage.py` rebuilt here (CodeRabbit on #408).
        #
        # The label-side bound is still real, it is just the right label: `agent:gate-blocked` is
        # the state where nothing further is authorised, and it is published from a recount rather
        # than carried forward, so it cannot be cleared by tidying labels.
        # RE-READ, not the plan's snapshot. `item["gate_blocked"]` was decided when the plan was
        # built, and triage can publish the terminal label in the seconds between - so a stale False
        # let the reservation create an AMEND ref for a lane that had stopped terminating
        # (CodeRabbit on #408). This is the same rule `AGENTS.md` states for a claim: revalidate
        # immediately before the authoritative write, never once for all of them. A read failure
        # raises rather than defaulting, because defaulting here defaults toward issuing.
        item["gate_blocked"] = GATE_BLOCKED_LABEL in _labels(_issue_now(number))
        if issued >= CAP or item["gate_blocked"]:
            item["mode"] = "refuse"
            blocked = f", and {GATE_BLOCKED_LABEL} is published" if item.get("gate_blocked") else ""
            item["reason"] = (
                f"no further AMEND authority may be issued ({issued} session(s) already issued for "
                f"generation {generation}; labels report {item['label_rounds']} round(s)"
                f"{blocked}). Safety-class findings escalate to the maintainer; the rest become "
                "follow-ups."
            )
            return None
        ordinal = str(issued + 1)
        round_number, remaining = issued + 1, CAP - (issued + 1)

    sha = reaper._read_ref(number)
    if sha is None:
        raise SlotError(f"#{number} claim ref vanished before its AMEND could be issued")
    if not _take_amend_round(number, generation, ordinal, sha):
        item["mode"] = "lost"
        item["reason"] = f"another launcher issued AMEND round {ordinal} for #{number} first"
        return None

    item["round"] = round_number
    item["remaining"] = remaining
    return record


def run(*, slots: int, vendor: str, owner: str, spawn: bool, tasks: Path) -> dict[str, Any]:
    # The lane's interpreter is resolved FIRST, before the plan and before any mutation. It is a
    # property of the argument alone - no I/O, nothing to read - so discovering it late buys nothing
    # and costs everything: `_render` runs after `_dispatch_build` has created the claim ref or
    # `_authorise_amend` has burned a permanent round ref, so a refusal there strands a claim until
    # the reaper, or spends one of the two rounds irrevocably, without launching a worker. Same
    # argument as the template validation below, and the same one `AGENTS.md` makes about releasing
    # a claim rather than abandoning it.
    _lane_python(vendor)
    plan = _plan(slots=slots, vendor=vendor)
    # Validate every template this run may use BEFORE any of it is consumed. A template defect is a
    # property of the file alone, so there is no reason to discover it after taking a claim - which
    # would strand the ref until the reaper - or after authorising a round, which spends one of the
    # two irrevocably and launches nothing with it.
    for mode in {item["mode"] for item in plan} & {"amend", "advance", "build"}:
        _body(tasks / f"{mode}.md")
    results: list[dict[str, Any]] = []
    for item in plan:
        if item["mode"] == "refuse":
            results.append({**item, "launched": False})
            continue
        if item["mode"] in ("amend", "advance"):
            authorise = _authorise_amend if item["mode"] == "amend" else _authorise_advance
            record = authorise(item, owner)
            if record is None:
                results.append({**item, "launched": False})
                continue
            task_file = tasks / f"{item['mode']}.md"
        else:
            taken = _dispatch_build(item["issue"], vendor, owner)
            if taken is None:
                results.append({**item, "launched": False, "reason": "claim not taken"})
                continue
            record = taken
            task_file = tasks / "build.md"

        rendered = tasks / f"_task-issue-{item['issue']}.md"
        rendered.write_text(_render(task_file, record, item), encoding="utf-8")
        wrapper = tasks / f"_task-issue-{item['issue']}.ps1"
        command = _wrapper(record, item, rendered, wrapper)
        if spawn:
            _spawn(command)
        results.append(
            {
                **item,
                "launched": True,
                "branch": record["branch"],
                "generation": record["generation"],
                "task_file": rendered.name,
                "wrapper": wrapper.name,
                "command": command,
                "spawned": spawn,
            }
        )
    return {"version": 1, "cap": CAP, "slots": slots, "results": results}


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Fill worker slots; issue review rounds.")
    parser.add_argument("--slots", type=int, default=2, help="how many workers to launch")
    parser.add_argument("--vendor", choices=claim.VENDORS, default="claude")
    parser.add_argument("--owner", default="bioedca", help="login whose approval counts")
    parser.add_argument(
        "--spawn",
        action="store_true",
        help="start the workers instead of printing their commands (interlocked)",
    )
    parser.add_argument(
        "--tasks",
        default=str(BIN.parent / "tasks"),
        help="directory holding build.md and amend.md",
    )
    args = parser.parse_args()
    try:
        report = run(
            slots=args.slots,
            vendor=args.vendor,
            owner=args.owner,
            spawn=args.spawn,
            tasks=Path(args.tasks),
        )
    except (SlotError, claim.ClaimError, reaper.ReaperError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (ValueError, OverflowError, RecursionError, AttributeError, KeyError, TypeError):
        print("error: input exceeds safe processing limits", file=sys.stderr)
        return 2
    except OSError:
        print("error: operating-system I/O failure", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    # An empty queue is a distinct, non-error outcome: a caller loop must be able to tell
    # "nothing to do" from "something went wrong" without parsing prose.
    return EXIT_NO_WORK if not any(r["launched"] for r in report["results"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
