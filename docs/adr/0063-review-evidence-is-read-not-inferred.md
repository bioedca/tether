<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0063 — Review evidence is read from the payload, not inferred from its shape

- **Status:** accepted; refines the §Round cap of [ADR-0062](0062-draft-first-review-lane-with-metered-providers.md)
  and supersedes the no-shared-code rationale ADR-0057 gave the scope guard's round counter.
  ADR-0062's lane, cap and gate are unchanged as *policy*; what changes is how the payload is read
  to decide each one.
- **Date:** 2026-08-07
- **Deciders:** bioedca
- **PRD anchor:** §12 (development & version-control protocol)
- **Milestone:** M11 - Agent-swarm infrastructure

## Context and problem statement

`.agents/bin/triage.py` reconstructs the lane's state from GitHub REST payloads. It grew from 344 to
1,316 lines in eight days, and six of the nine issues open against the agent layer on 2026-08-07
share one root cause: **the module inferred lane state from the shape of a payload rather than from
what the payload states.**

Each inference was a separate inline predicate with its own fail direction, and the defects lived in
the disagreements between them.

| issue | the inference | what it stood in for |
|---|---|---|
| [#409](https://github.com/bioedca/tether/issues/409) | *an inline comment exists* | the severity of the finding |
| [#411](https://github.com/bioedca/tether/issues/411) | *the PR is capped* | the PR is past the draft phase |
| [#415](https://github.com/bioedca/tether/issues/415) | *the state is not `CHANGES_REQUESTED`* | a review happened and found nothing |
| [#423](https://github.com/bioedca/tether/issues/423) | *the timestamp is unreadable* | the round should count |
| [#419](https://github.com/bioedca/tether/issues/419) | — | a branch reachable only because of #423 |

Three of these were measured rather than argued, against this repository's own history:

- **58% of CodeRabbit's inline findings here are `Minor`**, which `docs/agents/review.md` instructs
  workers to *defer* rather than fix — yet each spent a round. #405 spent 4 rounds that way and #402
  spent 2. Severity markup parsed on **154 of 154** non-reply findings across thirteen pull
  requests, so it is machine-readable, not a heuristic.
- Driving `_counts_as_round` over every shape a REST payload can give `submitted_at`, **nine of ten
  spent a round on a draft** — the one thing ADR-0062 says the draft phase cannot do.
- The dismissal sequence in #415 is reachable with the author's own write access: submit a review,
  then `PUT /pulls/{n}/reviews/{id}/dismissals`. The body outlives the withdrawn verdict, so the
  submission still read as a provider reporting cleanly.

Separately, `.agents/bin/scope_guard.py` carried a ~145-line **reimplementation** of the round
counter. ADR-0057's rationale was independence — a guard that audits a counter should not import it.
In practice it produced a second implementation that drifted from the first three times
([#307](https://github.com/bioedca/tether/issues/307), #396, #400), each found and fixed twice, with
a window between in which the "second opinion" disagreed with `triage` for reasons that had nothing
to do with the pull request being measured.

## Decision

**1. Every lane predicate reads the field that states the answer, and each is asked exactly once.**

- **Severity comes from the provider's own badge.** CodeRabbit renders `_<domain>_ | _<severity>_ |
  _<effort>_`; Greptile renders `<img alt="P1">`. The capture is anchored on the **second** pipe
  field, because `docs/agents/review.md` says in as many words that the domain label is not a
  severity and never promotes a finding. Blocking is CodeRabbit `{Critical, Major}` and Greptile
  `{P1}` — the floor that page already defines, now read by the code that enforces it.
- **The gate's proving half is an allowlist**, `{COMMENTED, APPROVED}`, not "anything but
  `CHANGES_REQUESTED`". `DISMISSED`, `PENDING`, an absent state and every state GitHub has yet to
  invent prove nothing — and void nothing, which stays the separate job of the voiding half.
- **The gate keys on the phase, not on the cap.** `capped` stood in for *past the draft*; it also
  excluded a PR whose first CodeRabbit review came back clean, which has met ADR-0062's gate,
  because the cap is a ceiling and not a quota.
- **The draft sentinel is tested before the malformed-data escape**, so ADR-0062's "the draft phase
  spends no rounds" holds against any payload rather than only against a well-formed one.

**2. Ambiguity is resolved where it is still resolvable, not where it is convenient.** The timeline
read now happens once, and its failure is caught at the point the two meanings of `None` — *opened
ready* and *could not be read* — are still distinguishable. The round axis keeps *count everything*;
the gate gets `False`. Neither infers the other's answer from a shared value.

**3. Unreadable evidence still counts, everywhere.** Narrowing applies only where a provider has
**stated** something below the floor. An unparseable body, an unknown login, a non-string payload and
a login not in the severity table all take the counting answer. Over-counting caps a PR early, which
is visible and recoverable; under-counting hands out an unbounded metered budget.

**4. The scope guard delegates to the counter it reports beside.** `_review_rounds` calls
`triage._review_state`; the guard's copies of `_counted_from`, `_COUNT_NOTHING`, `_is_draft` and the
counting loop are deleted, with the nine tests that pinned the copy against the original. The two
modules share one transport, as `swarm_slots.py` already does.

An unreadable count reports **unknown**, not zero. Advisory means *never fail the job*; it does not
mean answer a question nobody could read.

## Consequences

**A clean first review now ends the lane.** A PR whose first CodeRabbit review finds nothing reports
`gate: satisfied` instead of `open`, so the summary stops implying a metered credit is still owed.

**Non-blocking findings stop spending the budget.** Replayed against seven real pull requests, #405
drops from 4 rounds to 0 and #402 from 2 to 0, while **#408 holds at 9** — every one of its heads
carried a genuinely blocking finding. That last figure is the acceptance criterion, not a footnote:
a change that moved #408 would have widened the rule rather than sharpened it.

**The severity floor now has one definition instead of two.** `docs/agents/review.md` told workers
to defer a `Minor`; the counter charged them a round for it. A worker following the contract exactly
could reach `agent:review-capped` having been told nothing they were obliged to act on — which
[#414](https://github.com/bioedca/tether/pull/414) then did, live, at `agent:gate-blocked`.

**The guard can no longer disagree with the counter for its own reasons.** It also can no longer
detect a defect *in* the counter, which is the independence being traded away. That trade is
acceptable because the number was never load-bearing — it is rendered into an advisory table and
nothing branches on it — and because after decision 1 a mirror would have been actively harmful:
severity is not recoverable from comment shape, so a copy would over-report on every PR carrying a
non-blocking finding, manufacturing the "evidence is ahead of the labels" alarm it existed to raise.

**A risk is created and named.** A provider that changes its severity rendering silently reverts the
count to the old behaviour rather than failing. That is the deliberate fail direction, and it is
visible: the over-count is what the layer did before this record, so the regression is a return to a
known-safe state rather than a new failure mode. `_finding_is_blocking`'s unparseable arm is tested.

**Dead code is removed rather than pinned.** `_advance_state`'s capped-draft branch is deleted under
[#419](https://github.com/bioedca/tether/issues/419)'s own third criterion — with the ordering fixed
it is unreachable by construction. Nothing in the suite failed when it was removed, which is the
complaint #419 was filed about.

## Alternatives considered

**Keep counting every finding and raise the cap.** Rejected: it treats the symptom. The cap exists
to bound a *metered budget*, and a limit tuned around findings nobody is obliged to act on measures
the wrong thing at any value.

**Parse severity for Codex too.** Rejected as dead code. Codex is the unmetered lane under ADR-0062
and can never spend a round, so a badge parser for it would never run.

**Add `DISMISSED` to the denylist and stop there.** Rejected. It closes the one state that has
already been exploited and leaves the next one open; an allowlist answers states nobody has thought
of yet. This is also what #408 tried — it removed `DISMISSED` from the set consulted, which closed
only the empty-payload case and left the case a worker can actually produce.

**Keep the scope guard's mirror and teach it severity too.** Rejected: it is the drift that has
already happened three times, and decision 1 makes an accurate mirror impossible without copying the
regexes as well — at which point the two modules share the defect they were supposed to
cross-check.
