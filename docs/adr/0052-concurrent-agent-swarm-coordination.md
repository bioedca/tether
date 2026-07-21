<!--
SPDX-FileCopyrightText: 2026 The Tether Authors
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0052 — Concurrent issue-swarm coordination

- **Status:** accepted
- **Date:** 2026-07-21
- **Deciders:** bioedca
- **PRD anchor:** §12.2–§12.5 (GitHub Flow, reviews, pull requests, and issue planning)
- **Milestone:** Cross-cutting repository governance

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

The standard-library swarm helper and its tests are the executable protocol. A material change to marker
schemas, canonical election, transition edges, or authority separation requires a superseding ADR;
compatible clarifications require an explicitly labeled amendment. Update the executable tests with either.

### Consequences

- Good: claims are visible, duplicate work is reduced, and recovery does not depend on one process.
- Good: least authority is explicit; a forged or stale local record cannot authorize a merge.
- Trade-off: public comment/API traffic increases, and ambiguity deliberately freezes progress.
- Trade-off: CI, review, and quota waits keep a worker slot occupied until its safe terminal state.

## More information

- `AGENTS.md`: Concurrent GitHub Flow, Mandatory review path, and Handoff and cleanup.
- `.agents/skills/run-issue-swarm/SKILL.md` and `.agents/skills/solve-issue-goal/SKILL.md`.
- `.agents/skills/run-issue-swarm/scripts/swarm_lease.py` and `tests/test_swarm_lease.py`.
