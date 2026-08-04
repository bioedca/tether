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
CAPPED_LABEL = triage.CAPPED_LABEL
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
DRAFT_ORDINAL_PREFIX = "draft-"

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
        name = entry.get("ref", "") if isinstance(entry, dict) else ""
        _, marker, ordinal = name.partition(f"refs/{prefix}")
        if not marker:
            continue
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
    """
    try:
        pr = triage._pull_request(number=None, branch=branch)
    except (triage.TriageError, claim.ClaimError):
        return False
    if not isinstance(pr, dict):
        return False
    return triage._counted_from(pr) is triage._COUNT_NOTHING


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
        if spent >= CAP:
            # The FAST refusal, from the published labels. Not the authoritative one - that is the
            # launcher's own issuance count in `_authorise_amend`, which does not depend on any
            # label. This one exists so a capped issue costs no further API calls, and it is
            # reported rather than skipped: a launcher passing over it quietly would look just like
            # one with no work.
            plan.append(
                {
                    "issue": number,
                    "mode": "refuse",
                    "label_rounds": spent,
                    "reason": (
                        f"at the {CAP}-round cap ({CAPPED_LABEL} present or {spent} rounds spent); "
                        "no AMEND authority may be issued. Safety-class findings escalate to the "
                        "maintainer; the rest become follow-ups."
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
    order = {"refuse": 0, "lost": 0, "amend": 1, "build": 2}
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
        ordinal = (
            f"{DRAFT_ORDINAL_PREFIX}{len(_amend_ordinals(number, generation, draft=True)) + 1}"
        )
        round_number, remaining = 0, CAP
    else:
        issued = _issued_amends(number, generation)
        # `max` of the two counters, so EITHER can cap it. That makes the launcher's bound at worst
        # as strict as the contract's and never looser - the property the first version failed to
        # have, and one the draft split above must not weaken: it changes which refs are counted,
        # never how the count is compared.
        if max(issued, item["label_rounds"]) >= CAP:
            item["mode"] = "refuse"
            item["reason"] = (
                f"at the {CAP}-round cap ({issued} AMEND session(s) already issued for generation "
                f"{generation}; labels report {item['label_rounds']}). No further AMEND authority "
                "may be issued. Safety-class findings escalate to the maintainer; the rest become "
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
    for mode in {item["mode"] for item in plan} & {"amend", "build"}:
        _body(tasks / f"{mode}.md")
    results: list[dict[str, Any]] = []
    for item in plan:
        if item["mode"] == "refuse":
            results.append({**item, "launched": False})
            continue
        if item["mode"] == "amend":
            record = _authorise_amend(item, owner)
            if record is None:
                results.append({**item, "launched": False})
                continue
            task_file = tasks / "amend.md"
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
