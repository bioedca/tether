<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0063 — Review evidence is read from the payload, not inferred from its shape

- **Status:** superseded by [ADR-0064](0064-the-agent-layer-coordinates-writers-not-reviews.md); its measurements stand as evidence, and its mechanisms still run and still govern until the subtractive pull request removes them
- **Date:** 2026-08-07
- **Deciders:** bioedca
- **PRD anchor:** §12 (development & version-control protocol)
- **Milestone:** M11 - Agent-swarm infrastructure

## Context and problem statement

> **Superseded 2026-08-07 by [ADR-0064](0064-the-agent-layer-coordinates-writers-not-reviews.md),
> which removes `triage.py` rather than correcting it again — the growth measured below is the
> evidence for that decision.** **Deciding is not doing.** ADR-0064 records the decision to retire
> `triage.py`; it does not delete the module, and neither does the pull request that adds it. The
> module still runs and still governs until the separate subtractive pull request that deletes it
> lands, so the fixes recorded here remain the live behaviour until then. The measurements stand as
> evidence either way.

`.agents/bin/triage.py` reconstructs the lane's state from GitHub REST payloads. It grew from 344 to
1,316 lines in eight days, and the **five** issues below share one root cause: **the module inferred
lane state from the shape of a payload rather than from what the payload states.**

Two more of the same shape were found in the same sweep and fixed before this record was written —
one field read two ways in [#410](https://github.com/bioedca/tether/issues/410), and an unsubmitted
review counted as a round in [#400](https://github.com/bioedca/tether/issues/400) — so the pattern
accounts for seven defects in one week, not five. They are named here rather than tabled because
neither is decided by this record.

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
  severity and never promotes a finding.

  **The floor is a threshold on a stated ordering, not a list of members.** Each provider's scale is
  written down most-severe-first — CodeRabbit `Critical > Major > Minor > Trivial`, Greptile
  `P0 > P1 > P2 > P3` — and blocking is *at or above* the floor that page names. Enumerating the
  members instead was the first version of this and it was wrong: it happened to be right for
  CodeRabbit, whose two blocking levels are the whole top of its scale, and it read Greptile's `P0`
  as **below** the `P1` floor, dropping the provider's most severe finding from the count. Greptile
  found that on the pull request implementing this record, which is the kind of accident a derived
  value cannot have and an enumerated one can.
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
stated a severity **the scale places below the floor**. An unparseable body, an unknown login, a
non-string payload, a login not in the table, and a level the scale does not describe all take the
counting answer. That last case is what makes decision 1's ordering safe rather than merely correct:
a scale that is missing a level ranks nothing by guesswork, so `P0` counts even against a scale that
has forgotten it exists. Over-counting caps a PR early, which is visible and recoverable;
under-counting hands out an unbounded metered budget.

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
