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
mandatory CodeRabbit stage. Greptile counts **when it reviews after the PR is ready** — there, a spent
credit is a real round. The step-2 credit does not, because step 2 is on the draft: the money is gone
either way and the findings are owed either way, but no round is charged. Those are two ledgers and
conflating them would let a worker reach `agent:review-capped` on the draft phase the lane sends them
to, before the mandatory gate. Entering the counted phase is **permanent**: a PR converted back to draft keeps every round it has spent, or the
cap would be opt-out by toggling draft.

Owed-an-answer is a **separate axis** from rounds. Any external provider's finding is owed an answer
at the head it was written against — including Greptile's, whose review was paid for, and including
findings raised on a draft.

**Exhaustion and incapacity are different.** A provider with nothing to say has reviewed; a provider
with no budget has not. Greptile out of credits is skippable and never blocks. **CodeRabbit
unavailable freezes the PR**, because it is the gate — but *throttled* is a third thing again: a fair-use refusal that
names a retry time is a **wait**, and a request that produced no review has not spent the one-per-round allowance.
That retry carries a precondition, because the same status check reads two ways: green with no review body is the
silent suppression above, and `pending` is a review **running now** that a second request destroys (measured on #385).
So the check is read before every ask — elapsed time alone is not a licence, and a retry that lands on a live review
converts a wait into the freeze it was not.
Copilot is advisory only: it never satisfies a
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
- **The round labels change meaning, so they need a migration.** `agent:round-N` and
  `agent:review-capped` are persisted state written under the old semantics, where draft-phase
  rounds counted. They are deliberately monotonic — a count that falls normally means a read failed,
  and stepping back from capped would re-authorise a spent round — so a pull request open at the
  changeover would keep a label the new counter contradicts, and `swarm_slots` trusts that label and
  refuses work past the cap. The PR could never reach the CodeRabbit stage this record makes
  mandatory: unrecoverable, not merely wrong.

  **The migration is an operational step, not code, and that is the decision rather than an
  omission.** Three automatic versions were written and each was worse than the last: clearing for a
  PR that had never been ready left the label stuck the moment it went ready; clearing on a zero
  recount fixed that and introduced something worse. A recount of zero is *ambiguous*. It means
  "never spent" for a stale label — and "the evidence was deleted" for a real round that a metered
  provider left as wrapper-less inline comments, which `_review_state` explicitly supports. Clearing
  on zero refunds the second case, which is **fail-open on the cap**: the single property the counter
  exists to hold. No predicate available at that point can separate the two.

  So a stale label is removed by hand, which is a command somebody runs, where a wrong automatic
  clear is silent. **Remove every round label, not just the capped one** — `swarm_slots.py` refuses
  on `agent:review-capped` *or* on `agent:round-2` by itself (`:137` and `:139`), so clearing only
  the first leaves the item exactly as capped while looking cleaned:

  ```sh
  gh issue edit <N> --remove-label agent:review-capped \
                    --remove-label agent:round-2 --remove-label agent:round-1
  ``` Verified empty before
  this merged: no issue in the repository carried `agent:round-*` or `agent:review-capped`. A future
  change to what these labels mean needs the same check, and should reach for the same answer.
- **Budget visibility, not budget enforcement.** Greptile publishes no usage API, so
  `.agents/bin/greptile_usage.py` counts completed `greptile-apps[bot]` reviews per calendar month
  across all three repositories on the seat. It is a proxy: a TREX review costs 3 and is counted as
  1, so it can only read **low**, and it fails closed — an unreadable repository makes the total
  *unknown* rather than silently small. Nothing prevents an over-spend except not asking.
- **Not covered.** `.greptile/config.json` is read from the PR's *source branch*, so branches cut
  before it landed still auto-fire. There is no auto-trigger off switch; the account-level
  file-change limit is the stop-gap, and its comparison is *exceeding*, so a one-file PR still
  auto-fires.
- **The lane is hand-driven until the worker state machine models phases.** This is the record's
  largest known limitation and it is one problem wearing three faces, all in how a claim is resumed.
  A worker opens a draft and exits; only `agent:needs-amend` reopens the claim; and that label is
  published only for a failed check or an owed finding. So a **clean** review authorises nobody, and
  the draft is stranded before the CodeRabbit gate it cannot merge without
  ([#394](https://github.com/bioedca/tether/issues/394)) — the opposite failure to
  [#391](https://github.com/bioedca/tether/issues/391), where the launcher's permanent refs cap
  resumptions without knowing draft phases are free, and next to
  [#393](https://github.com/bioedca/tether/issues/393), where a deferred finding owes an AMEND
  forever because only a push clears it. The single-shot model this replaces had no such gap:
  auto-merge did the waiting. Patching the three separately would be three guesses at one missing
  concept, so `.agents/tasks/build.md` now opens the lane and hands off rather than instructing a
  session to walk phases it cannot reach, and dispatching the lane to a worker waits for **both**
  #394 and #391 — they gate it independently, one by publishing no resumption and the other by
  refusing the resumptions that do get published.

  **#391 is resolved** (see the launcher entry below), and **#393** with it. #394 remains, so the
  lane is still hand-driven past the point a clean review lands: nothing publishes the authority to
  advance it.
- **The launcher's second cap is phase-aware, in its own ledger** (amended by
  [#391](https://github.com/bioedca/tether/issues/391)). `swarm_slots.py` records every AMEND in a
  permanent generation ref and refuses another past `CAP`, independently of the labels —
  deliberately, so either counter can bind. As first written it had no notion of draft state, so two
  draft iterations exhausted it while `triage.py` correctly reported zero rounds, and the uncapped
  loop stalled at the launcher with nothing saying why.

  A draft-phase session now takes its ref under a `draft-` ordinal
  (`refs/amend-rounds/<issue>-<generation>-draft-<n>`), which the cap does not count. It still takes
  a ref, because that ref is the mutex stopping two launchers from starting the same session — a
  separate job from counting, and one the draft phase needs just as much.

  Three properties are load-bearing and each is pinned by a test. **The phase is written into the
  ref name**, not re-derived when the ledger is read: a ref is immutable, so it records what was true
  when the session was issued, where reading `draft` at audit time would let a PR that has since
  gone ready make its own draft history retroactively count. **The phase comes from
  `triage._counted_from`**, the same predicate the round labels come from, so `agent:review-capped`
  and the launcher's refusal cannot disagree about which phase a PR is in — and because that keys on
  the *first* `ready_for_review`, entering the counted phase stays permanent here too. **It fails
  toward the cap**: no pull request, or one that cannot be read, is the counted phase, because the
  uncapped phase has to be positively established.

  `max(issued, label_rounds)` is unchanged for the counted phase. This changes which refs are
  counted, never how the count is compared, so the launcher's bound remains at worst as strict as
  the contract's.

## Alternatives considered

- **Keep risk routing and swap CodeRabbit for Greptile on `high`.** Rejected: the seat's **50**
  credits are one pool shared across three repositories, and a TREX review costs 3 — so at most 16
  full reviews a month, for all three. That cannot cover `high` work at this repository's rate, and
  it leaves the silent-CodeRabbit failure in place on everything else.
- **Budget by milestone allowance.** Rejected as premature — it needs a policy for what happens when
  one bucket empties while another has slack, and the seat is shared with two repositories whose
  spend this repository cannot see.
- **A workflow that arms auto-merge and reports green-unarmed PRs.** Deferred: arming is a worker
  action, and a workflow that posts its findings as a comment trips `test_no_workflow_can_post_a_review_trigger`.
  Detection via a label, using the `issues: write` the reaper already holds, is the shape that fits.
