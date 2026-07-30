<!--
SPDX-FileCopyrightText: 2026 The Tether Authors
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0052 — Concurrent issue-swarm coordination

- **Status:** **superseded by [ADR-0057](0057-github-native-swarm-coordination.md)** and **fully
  switched off since 2026-07-30**. Nothing below governs any longer: the claim mutex, the reaper and
  the peer model replaced the coordinator, the leases, the run records and the guarded merge, and the
  tooling this record called its executable protocol has been deleted. Kept for its history and for
  the measurements that justified replacing it.
- **Date:** 2026-07-21
- **Deciders:** bioedca
- **PRD anchor:** §12.2–§12.5 (GitHub Flow, reviews, pull requests, and issue planning)
- **Milestone:** Cross-cutting repository governance

> **Historical record only (2026-07-30).** Every clause below has been replaced. Two are worth naming
> because they were the specific mechanisms that failed. First, the review-evidence rule: *any* head
> change invalidated final-head review evidence, and combined with a reviewer that fired on every push
> it produced a livelock — PR #238 reached 16 heads in 12h45m without merging, because each fix
> invalidated the approval that asked for it. `AGENTS.md` §Review gate now governs; evidence survives a
> **non-material** push, a material push grants no extra round, and a PR gets at most two. Second, the
> worker/coordinator separation and guarded merge: a peer now claims by atomic ref, arms auto-merge and
> exits, so no agent is a bottleneck for another. Read this record for why, never for what to do.

## Context and problem statement

Independent agent sessions share one GitHub identity, while process memory and host-local locks do
not survive crashes or reboots. Simultaneous claims, worktrees, and merges can therefore collide.
How can agents make ownership visible and recoverable without allowing a claim to imply issue
acceptance or merge authority?

## Decision drivers

- Preserve one issue, owner, branch, writable worktree, and PR.
- Make ownership durable and visible while keeping local task IDs and paths private.
- Recover deterministically after interruption and fail closed on edited, forked, or ambiguous state.
- Separate coordination from acceptance, review, and merge authority.

## Considered options

- Assignees and labels alone: visible, but they do not bind scope, base SHA, branch, or expiry.
- An in-memory or host-local ledger: fast, but unavailable to other sessions and lost on restart.
- One editable state comment: compact, but concurrent edits and lost history make recovery ambiguous.
- Coordinator-serialized public leases, isolated workers, and append-only run state (chosen).

## Decision outcome

One coordinator owns shared GitHub and worktree lifecycle mutations. Accepted scope is bound to the
maintainer-approved title/body hash. Each worker receives one dedicated task, worktree, branch, and
PR; workers stop at PR-ready and never merge.

Visible leases last exactly four hours, identify an automated persona, and bind the run, issue,
approved scope, base SHA, and branch. The lowest-ID validated unexpired claim wins. A lease coordinates
ownership only: it grants neither issue acceptance nor merge authority.

Each run has an immutable start record and predecessor-hashed, monotonic transition comments. The
coordinator validates raw GitHub comment IDs, target, author, body, and immutable server timestamps;
malformed markers, edits, forks, or uncertain writes freeze the run. Merge authority is a separate,
explicit, earlier run-bound record. A merge-policy run is actionable only when the complete fetched
run and authority envelopes resolve as one lineage. Only the coordinator may perform an exact-head,
exact-base guarded merge after the required review path is complete.

A standard-library swarm helper and its tests were the executable protocol. That tooling was deleted in
#269 and #279; the only part of it that survives is the frozen approval-scope digest, now in
`.agents/bin/claim.py`, because markers published on live issues bind to that exact normalization.

## Amendment — exact-head independent review (2026-07-22)

Copilot is optional, best-effort feedback and its availability or quota never blocks a worker slot or
merge. Every low, standard, and high lane instead requires a substantive PR diff review or walkthrough
bound to the final head SHA from Codex GitHub Code Review or CodeRabbit; low and standard may select
either, while high/load-bearing changes require CodeRabbit after the stable diff is green. Qualified
human/domain review is required when scientific, security, or release judgment is material. Author-side or local review, a status/check alone,
denial, provider unavailability, or a summary without a diff walkthrough is not independent review
evidence. Any head change invalidates final-head review evidence; a material change requires every affected
review layer again. Every conversation and every actionable finding is resolved. CodeRabbit quota
occupies the slot only when CodeRabbit is required or selected.

Workers remain PR-ready producers and never merge. Under explicit run-scoped `merge` authority, only the coordinator
may verify the exact head/base, perform the guarded squash merge, complete the owned lease, and refill
the worker slot. An explicit `PR-ready` run remains a non-merging terminal path.

### Consequences

- Good: claims are visible, duplicate work is reduced, and recovery does not depend on one process.
- Good: least authority is explicit; a forged or stale local record cannot authorize a merge.
- Trade-off: public comment/API traffic increases, and ambiguity deliberately freezes progress.
- Trade-off: CI and required/selected-review waits keep a worker slot occupied until its safe terminal state.

## More information

- [ADR-0057](0057-github-native-swarm-coordination.md) — what replaced this, and the measured reasons.
- `AGENTS.md`: Concurrent GitHub Flow, Review gate, and Handoff and cleanup.
- `.agents/bin/claim.py`, `.agents/bin/reaper.py`, `.agents/skills/tether-worker/SKILL.md`. The files
  this record used to cite — `run-issue-swarm/`, `solve-issue-goal/`, `swarm_lease.py` and
  `tests/test_swarm_lease.py` — no longer exist.
