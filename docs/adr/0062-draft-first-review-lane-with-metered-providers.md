<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0062 — Draft-first review lane: spend the free provider before the metered ones

- **Status:** accepted; supersedes the §Review gate and §Round cap of [ADR-0057](0057-github-native-swarm-coordination.md)
  ADR-0057 records the review gate, the round counter and the launcher as one architectural control.
  This record changes two of those three; the launcher half, and the rest of ADR-0057, stand
  unchanged.
- **Date:** 2026-08-03
- **Deciders:** bioedca
- **PRD anchor:** §12 (development & version-control protocol)
- **Milestone:** M11 - Agent-swarm infrastructure

## Context and problem statement

The review gate routed by *risk*: `low`/`standard` to Codex, `high` to Codex and CodeRabbit
together. That was written when every provider was effectively free, and it stopped being true.

**Greptile is metered per seat.** Greptile Pro includes 50 credits per seat per month; billing
counts *completed reviews, not PRs*, one credit each, charged to the PR author, and a TREX review
costs 3. This account has **one paid seat**, shared across `tether`, `Yeliztli` and `tbox-finder`,
so all three draw on the same 50. Left on automatic Greptile reviewed every PR the moment it opened:
on 2026-08-03 it spent two credits in one day across two repositories, neither requested. Milestones
M14–M17 alone carry ~40 issues; one auto-review each would consume the seat's whole month on this
repository twice over. Copilot draws on the same seat, and CodeRabbit is subject to fair use.

So provider choice is now a *budget* decision as much as a quality one, and risk-based routing
cannot express it: a `high` PR and a `low` PR cost the same credit.

There is a second, older problem the same change fixes. CodeRabbit's adaptive rate limit can
suppress a review **silently while its status check still goes green** — a failure that reads as
success. Nothing in the routing distinguished "reviewed and found nothing" from "never reviewed".

## Decision

**Providers are spent cheapest-first along one fixed lane, not routed by risk.** Each step begins
only when the previous has nothing blocking left:

1. **Open as a draft.** Every required check runs on a draft, so the diff reaches green before
   anything metered is asked. Greptile skips drafts by default.
2. **Codex on the draft, uncapped.**
3. **Optionally one Greptile credit** (`@greptileai review this draft`), if the seat has budget.
4. **Mark ready for review.** The two-round cap starts here.
5. **CodeRabbit with no actionable comments** — the last gate before merge.

**The round cap counts metered providers only, and only after the PR first goes ready.** Codex never
consumes a round, draft or not — counting the free lane would let it eat the rounds reserved for the
mandatory CodeRabbit stage. Greptile does count, because a spent credit is a real round. Entering the
counted phase is **permanent**: a PR converted back to draft keeps every round it has spent, or the
cap would be opt-out by toggling draft.

Owed-an-answer is a **separate axis** from rounds. Any external provider's finding is owed an answer
at the head it was written against — including Greptile's, whose review was paid for, and including
findings raised on a draft.

**Exhaustion and incapacity are different.** A provider with nothing to say has reviewed; a provider
with no budget has not. Greptile out of credits is skippable and never blocks. **CodeRabbit
unavailable freezes the PR**, because it is the gate. Copilot is advisory only: it never satisfies a
leg, and a quota refusal from it is recorded as *did not review*.

## Consequences

- **Good.** The expensive provider is asked once, against a diff that is already green and already
  survived free iteration — which is also when its findings are most useful. Uncapping the free lane
  removes a throttle that bought nothing but slower convergence. And the CodeRabbit-freezes rule
  turns its silent-suppression failure from a false pass into a stop.
- **Bad / trade-off.** Every PR now needs CodeRabbit, where `low` work previously needed only Codex,
  so the cheapest PRs get slower and lean on a fair-use budget. The lane is also a fixed sequence
  rather than a judgement, which will occasionally spend a Greptile credit on something that did not
  need it — accepted, because the alternative is a per-PR decision nobody can audit.
- **The cap change is a real widening.** Unbounded draft iteration means a worker can loop on Codex.
  The bound is now the session budget and the maintainer's attention rather than a counter. Accepted
  because the counter was never protecting a cost — it was protecting a provider that is free.
- **Implementation.** `triage.py` counted every provider-reviewed head with no notion of draft state,
  so the documented loop would have consumed the cap it is exempt from and stranded the PR at
  `agent:review-capped` *before* the mandatory CodeRabbit stage. It now counts only evidence
  submitted after the **first** `ready_for_review` event — the first, not the last, because taking
  the last let a worker refund a spent round by toggling back to draft, and a material push is
  granted no extra round; draft findings still **owe an answer**, they
  just do not cost a round. An unreadable timeline counts everything — a safety control fails toward
  counting.
- **Budget visibility, not budget enforcement.** Greptile publishes no usage API, so
  `.agents/bin/greptile_usage.py` counts completed `greptile-apps[bot]` reviews per calendar month
  across all three repositories on the seat. It is a proxy: a TREX review costs 3 and is counted as
  1, so it can only read **low**, and it fails closed — an unreadable repository makes the total
  *unknown* rather than silently small. Nothing prevents an over-spend except not asking.
- **Not covered.** `.greptile/config.json` is read from the PR's *source branch*, so branches cut
  before it landed still auto-fire. There is no auto-trigger off switch; the account-level
  file-change limit is the stop-gap, and its comparison is *exceeding*, so a one-file PR still
  auto-fires.
- **The launcher keeps a second cap, and it is not yet phase-aware.** `swarm_slots.py` records every
  AMEND in a permanent generation ref and refuses another past `CAP`, independently of the labels —
  deliberately, so either counter can bind. It has no notion of draft state, so two draft iterations
  exhaust it while `triage.py` correctly reports zero rounds, and the uncapped loop stalls at the
  launcher with nothing saying why. Tracked as **#391**, and it blocks dispatching this lane to a
  worker. It does not affect a hand-driven PR, which is how this record's own PR was worked.

## Alternatives considered

- **Keep risk routing and swap CodeRabbit for Greptile on `high`.** Rejected: 16 credits a month
  cannot cover `high` work at this repository's rate, and it leaves the silent-CodeRabbit failure in
  place on everything else.
- **Budget by milestone allowance.** Rejected as premature — it needs a policy for what happens when
  one bucket empties while another has slack, and the seat is shared with two repositories whose
  spend this repository cannot see.
- **A workflow that arms auto-merge and reports green-unarmed PRs.** Deferred: arming is a worker
  action, and a workflow that posts its findings as a comment trips `test_no_workflow_can_post_a_review_trigger`.
  Detection via a label, using the `issues: write` the reaper already holds, is the shape that fits.
