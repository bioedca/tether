<!--
SPDX-FileCopyrightText: 2026 The Tether Authors
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0057 — GitHub-native swarm coordination

- **Status:** accepted (supersedes [ADR-0052](0052-concurrent-agent-swarm-coordination.md) and
  [ADR-0053](0053-structured-backlog-intake-gates-swarm-admission.md); adopted in phases — see
  [Adoption status](#adoption-status))
- **Date:** 2026-07-28
- **Deciders:** bioedca
- **PRD anchor:** §12.2–§12.5 (GitHub Flow, reviews, pull requests, issue planning)
- **Milestone:** Cross-cutting repository governance

## Context and problem statement

ADR-0052 specified a coordinator-led swarm: marker-comment leases with four-hour TTLs and hourly
renewal, a lowest-comment-ID election, and a hash-chained run-lineage state machine, implemented in a
1,504-line helper with a 1,090-line test.

It ran 2026-07-26 → 07-28 and was measured from the session logs. **~3.48 billion input tokens, 83
points of a weekly quota, 44 hours, two merged PRs**, four abandoned worktrees holding uncommitted
work, and three freezes. The protocol was not merely expensive; several of its central mechanisms were
the direct cause of the failures.

- **The review rule livelocked.** "Any head change invalidates final-head review evidence", combined
  with a reviewer that fired automatically on every push, meant every fix invalidated the approval that
  had asked for it. PR #238 reached 16 heads in 12h45m and never merged.
- **The protocol was the bill.** ~132k tokens of resident contract on every model call: 4,885 calls ×
  132k ≈ 645M for the coordinator alone.
- **Approval-judging subagents cost 619M tokens (17.8% of the run) and produced no output.**
- **Polling replaced eventing**: 977 `wait_*` calls, one unbroken run of 162.
- **Fail-closed with only human escapes.** Leases expired at 08:03Z; only the coordinator could renew;
  its own contract forbade renewing an expired lease without a typed human approval. Nobody was awake,
  and the run never resumed.
- **A single coordinator serialized everything** — sole writer, sole review router, sole merger — in
  one 24.6-hour turn with 26 context compactions, so four workers ran as a one-deep queue.

## Decision drivers

- `main` must stay releasable, and no step may deadlock waiting on a human who is asleep.
- Coordination state must survive a restart; the in-memory ledger did not.
- Contract text is resident context, so its size is a running cost, not a one-off.
- Two vendors (Codex and Claude) must share one backlog without a vendor-specific protocol.

## Considered options

1. **Patch ADR-0052 in place** — keep leases and the coordinator, fix the review rule and the deadlock.
   Rejected: the resident-context cost and the freeze-on-any-ambiguity posture are properties of the
   design, not of its bugs.
2. **GitHub-native rebuild** — replace bespoke coordination with primitives GitHub already provides.
   **Chosen.**
3. **Clean slate** — discard §12 governance and start over. Rejected: the parts that worked (one issue
   ↔ one PR, approval before work, never merge red) had no defects to answer for.

## Decision outcome

**GitHub is the coordinator; there is no coordinator agent.** Every agent is a peer that claims an
issue, does the work, opens a PR, arms auto-merge, and exits.

- **Eligibility is a precondition of the claim, not a consequence of it.** The mutex decides *who*
  works an issue; it never decides *whether* the issue may be worked. Before creating the ref an agent
  must confirm the ADR-0053 intake gates on the authenticated issue: open, `status:ready`, no competing
  assignee, and a maintainer approval bound to the exact scope snapshot it is about to act on. A claim
  taken on unapproved or since-edited work is invalid regardless of who won the race, and the ref must
  be released rather than worked.
- **Claim by atomic ref creation.** `POST /git/refs` returns `201` to the first writer and `422
  Reference already exists` to every other — verified live against this repository. That is a genuine
  compare-and-swap: one call, no election, no singleton writer, identical for both vendors. The branch
  is `agent/issue-<N>` with **no title slug**, because a slug derived from the title is not
  deterministic across agents; two agents could otherwise create two different refs for one issue and
  both succeed, silently voiding the mutex.
- **No lease, no TTL, no heartbeat — but liveness must be server-observed, and the claim must be
  fenced.** Commit metadata such as `committedDate` is written by the client and cannot carry this:
  an agent may stamp any value, so a reaper keying on it can reclaim a live claim or preserve a dead
  one. Staleness must therefore be judged from a timestamp GitHub itself records, and the implementing
  change must name that source and demonstrate it is not client-settable.
  Reclamation alone is also not sufficient. Deleting a ref does not stop the worker that held it, so
  once a successor recreates `agent/issue-<N>` two workers can believe they own it. Each claim
  therefore carries a **monotonically increasing generation**, revalidated immediately before every
  authoritative write, so a superseded worker's late write is refused rather than silently applied.
  This is the same discipline the project store already uses for long writes, where
  `_assert_held_lock(expected_nonce=…)` binds each persistence point to the ownership epoch validated
  before the work began.
- A scheduled CI reaper reclaims dead claims — it runs while every human and agent is asleep, which is
  precisely what the terminal freeze needed and what a coordinator-only renewal monopoly could not
  provide.
- **No waiting.** Arm auto-merge and exit; an event-driven workflow turns check and review events into
  a single label the launcher reads in one call.
- **Review turns on materiality, not head identity**, with a severity floor and a two-round cap.
- **Merge queue is not available** and is not part of this design: it requires an organization-owned
  repository, and `bioedca/tether` is user-owned. `main` therefore carries no strict up-to-date rule,
  and merges bind with an expected-head guard instead.

### Consequences

- Coordination state becomes queryable GitHub state rather than agent memory, so a restart reconstructs
  it instead of rebuilding a ledger.
- Losing a claim costs one wasted API call rather than an election.
- Dropping the strict up-to-date rule means a semantic conflict can land green; post-merge `ci`,
  `schema-guard` and `sidecar / parity` runs on `main` are the compensating detection, which is why
  `sidecar.yml` gained a `push` trigger.
- Gaps in ADR numbering are legal and expected: numbers are reserved atomically as `refs/tags/adr/NNNN`
  and never reused, so a collision can never force the renumbering that previously invalidated reviews
  across three PRs at once.
- Two vendors sharing one backlog need no vendor-specific protocol, but they also gain no mutual
  review guarantee from this record; cross-vendor peer review is deliberately left to the change that
  introduces a second lane, rather than written as policy for something that cannot yet happen.

## Adoption status

This record documents one decision; it lands in phases, and the parts of ADR-0052/0053 that are not yet
replaced **remain operative until their replacement merges**. Superseded therefore means *decided*, not
*already switched off*.

**In force now:** the review gate (material-change rule, severity floor, two-round cap,
capability-vs-quota) in `AGENTS.md`; `main` without the strict up-to-date rule; `sidecar / parity`
reporting post-merge; the prose-drift guard retired.

**Not yet implemented, and ADR-0052/0053 still govern in the meantime:** the claim mutex and
`agent/issue-<N>` branches; removal of the coordinator, leases and run records; the reaper and
event-driven triage; the slot launcher; the `agent:*` label model; Projects/Discussions as coordination
surfaces.

## More information

- ADR-0052 keeps its history. Its review-evidence clause carries a supersession note in place; the
  worker/coordinator separation and guarded merge it specified stand until the machinery above lands.
- ADR-0053's intake gates (issue forms, `status:ready`, maintainer approval of a scope hash) remain in
  force; this decision changes who may admit work, not that admission is gated.
- The measurements quoted above come from the 2026-07-26 → 07-28 session logs.
