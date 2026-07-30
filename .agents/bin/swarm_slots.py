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
# triage is read for the label vocabulary and the cap ONLY, never called, so its own separate
# `claim` object is never used. Sharing the constants means the two halves of the cap cannot
# disagree about which labels they are talking about.
triage = _load("triage")

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

# Deliberately NOT under refs/tags/: hatch-vcs derives the package version from tags, so a
# non-version tag makes `pip install -e .` fail and turns main red - the trap ADR-0057 records for
# the ADR reservations. A custom namespace is the same compare-and-swap and invisible to every tag
# consumer.
AMEND_NAMESPACE = "amend-rounds"

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
    prefix = f"{AMEND_NAMESPACE}/{number}-{generation}-"
    status, refs = claim._request("GET", f"/repos/{REPO}/git/matching-refs/{prefix}")
    if status == 404:
        return 0
    if status != 200 or not isinstance(refs, list):
        raise SlotError(
            f"#{number} issued-AMEND count could not be read (HTTP {status}); refusing to guess it"
        )
    return len(refs)


def _take_amend_round(number: int, generation: int, ordinal: int, sha: str) -> bool:
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


def _render(task: Path, record: dict[str, Any], item: dict[str, Any]) -> str:
    """Substitute the task template. Every token must be consumed, or the worker reads a literal.

    The leading HTML comment block is **stripped, not substituted**. It carries the SPDX header and
    the maintainer's rationale - including the token syntax itself, which is why the
    unsubstituted-placeholder guard below tripped on the templates when the block was kept. Removing
    it is the right fix rather than rewording the comment: this text becomes resident context for
    the worker's whole session, so notes to whoever edits the template do not belong in it.
    """
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
    }
    text = task.read_text(encoding="utf-8")
    if text.lstrip().startswith("<!--"):
        # `str.partition` returns ("<!-- ...", "", "") when the separator is absent, so tolerating a
        # missing "-->" hands the worker an EMPTY task - which then passes the placeholder guard
        # below, there being nothing left to be unsubstituted. That failure is silent and it is not
        # free: the claim has already been taken, and for an AMEND a round has already been spent on
        # the compare-and-swap. Require the delimiter instead.
        _, closed, remainder = text.partition("-->")
        if not closed:
            raise SlotError(f"{task.name} opens a comment block that is never closed")
        text = remainder
    text = text.lstrip("\n")
    for key, value in values.items():
        text = text.replace(f"{{{{{key}}}}}", value)
    if "{{" in text:
        raise SlotError(f"{task.name} has an unsubstituted placeholder; refusing to inject it")
    if not text.strip():
        # The placeholder guard cannot see this one: an empty task has no placeholder left in it.
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
    """
    number = item["issue"]
    record = _existing_claim(number, owner)
    generation = int(record["generation"])
    issued = _issued_amends(number, generation)

    # `max` of the two counters, so EITHER can cap it. That makes the launcher's bound at worst as
    # strict as the contract's and never looser - the property the first version failed to have.
    if max(issued, item["label_rounds"]) >= CAP:
        item["mode"] = "refuse"
        item["reason"] = (
            f"at the {CAP}-round cap ({issued} AMEND session(s) already issued for generation "
            f"{generation}; labels report {item['label_rounds']}). No further AMEND authority "
            "may be issued. Safety-class findings escalate to the maintainer; the rest become "
            "follow-ups."
        )
        return None

    ordinal = issued + 1
    sha = reaper._read_ref(number)
    if sha is None:
        raise SlotError(f"#{number} claim ref vanished before its AMEND could be issued")
    if not _take_amend_round(number, generation, ordinal, sha):
        item["mode"] = "lost"
        item["reason"] = f"another launcher issued AMEND round {ordinal} for #{number} first"
        return None

    item["round"] = ordinal
    item["remaining"] = CAP - ordinal
    return record


def run(*, slots: int, vendor: str, owner: str, spawn: bool, tasks: Path) -> dict[str, Any]:
    plan = _plan(slots=slots, vendor=vendor)
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
