<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0131 — The correction states of G03 are a transition log stored in the project

- **Status:** accepted
- **Date:** 2026-11-18
- **Deciders:** bioedca (issue #925 approved; cards C2 option B and A1 option B of 2026-11-14, plan item F1 of #890)
- **PRD anchor:** §7.2 (FR-CORRECT, the correction states), Appendix B, §7.13 (FR-WORKFLOW), §5 (data model)
- **Milestone:** M16

## Context and problem statement

Diagram G03 makes the readiness of the corrections a state machine with eight states, from
APPARENT_REVIEW to ANALYSIS_READY and CALIBRATION_STALE (PRD §7.2). Today the correction pass is
one function that runs or does not run. The store does not record that a person approved a
factor, and nothing prevents a corrected analysis on a correction that is out of date.

Card C2 (option B) keeps apparent-E analysis possible as a separate named mode in every state, so
the machine controls corrected analysis only. ADR-0102 says who or what may approve
(`alpha.confirm`, `gamma.approve`: a person, or a recipe policy) and what an approval records.

Where is the state stored, what does a transition record, what state does a project that
already holds factors open in, and how does a corrected analysis learn that it may not run?

## Decision drivers

- **One state for the window, the command line and a recipe** (§7.2), so the machine is Qt-free.
- **No silent approval** (ADR-0102): the store shows who or what moved the state, when, and on
  which values.
- **Additive only** (ADR-0005, §9.1): no frozen field changes, and `schema_version` stays 1.
- **An existing project never opens as approved** (issue #925): factors stored before this change
  were never approved by anyone.
- **The table and nothing else**: a test checks each edge of G03 and that no other is possible.

## Considered options

- **A. An append-only transition log** under `/settings`, one row per transition; the state of a
  scope is the target of its last row.
- **B. One state attribute per scope** on a `/settings/correction_state` group, changed in place,
  with a separate audit log for the history.
- **C. No stored state**: derive the state on each read from the factor, correction and review
  records and their stale marks.

## Decision outcome

Chosen option: **A**, implemented in `src/tether/project/correction_states.py`.

B stores the same fact twice, and a crash between the two writes leaves the attribute and the log
disagreeing. C cannot hold an approval: the approval *is* the person's act, not a property of the
data, and the records it would derive from (#926, #927, #929) do not exist yet. A log answers both
“what is the state” and “who moved it” from one write, and follows the `/settings/condition_audit`
and `/settings/lock_takeovers` idiom.

1. **States, events, guards.** The eight states and thirteen edges of the table in issue #925 and
   PRD §7.2, and no others, are `TRANSITIONS`. The two ways out of CALIBRATION_STALE are one event,
   *calculate again*, whose guard on the bleedthrough factor α chooses the target: GAMMA_REVIEW
   when α is valid, BLEEDTHROUGH_REQUIRED when it is missing or not valid. An event with no edge
   out of the present state is refused, and so is an event whose guard does not hold; neither
   writes a row.
2. **The guards read values that the caller passes**, and every value is stored with the row. α is
   valid when it is finite and γ when it is finite and above 0, the rules that
   `compute_corrected_fret` already applies to a manual factor; this ADR adds no new validity rule
   and no tunable. *Has a source* means a source text that is not blank. *Approve* names the factor
   version it freezes and *apply correction version* the correction version it applies; *finish
   review [all dependencies current]* holds while neither of those two instances has a stale mark
   (ADR-0103, ADR-0126). The machine computes no factor: the action of an edge (*estimate gamma*)
   is the caller's next step.
3. **The store.** `/settings/correction_transitions`, a lazily created, resizable compound dataset
   of UTF-8 strings: `scope`, `from_state`, `event`, `to_state`, `by`, `actor`, `detail` (JSON),
   `timestamp` (UTC) and `app_version`. Rows are append-only. A transition needs the writer lock
   to be present and this writer's, so an unlocked project is refused (the `reconcile_jobs` rule of
   ADR-0126). Under the open file the lock epoch and the scope's rows are checked again before the
   row is written, so a steal or a transition that landed in between refuses the write. *Finish
   review* also reads the stale marks again there, since a mark changes neither. Within one
   process the transitions of a project file run one at a time, from the read of the state to
   the write of the row, since the writer lock does not exclude threads that share it.
4. **The scope** is the project now (`scope = "project"`). When #624 gives each condition its own
   factors, each condition becomes a scope of its own in the same column, an additive change; until
   then any other scope is refused.
5. **Who or what.** `by` is `person` or `policy`. For a person, `actor` is the name, read as the
   `labeler` of a curation label is; for a policy, the policy text exactly as the recipe gives it
   (ADR-0102 §4). The fuller approval record of ADR-0102 §4 (the recipe identity, the report and its
   checksums) is written by the approval routes (#899, #930) and is not part of this log. A person's
   name is personal data and gets the alias of ADR-0102 §6 in a copy for sharing (#620).
6. **The start state.** A scope with no row is in APPARENT_REVIEW, except in a project that holds
   `/settings/leakage`, `/settings/gamma` or `/settings/correction`: that project opens in
   GAMMA_REVIEW, with the stored α and γ as the proposal (a manual override of the correction pass
   wins over the estimate). A read writes nothing; the first transition stores the proposal it
   started from. Thus no project, old or new, reaches CALIBRATION_APPROVED without a row that says
   who approved.
7. **Corrected analysis.** `require_analysis_mode` refuses a corrected analysis in every state but
   ANALYSIS_READY, CALIBRATION_STALE among them, with a message that names the state. An apparent-E
   analysis is never refused (card C2). Every corrected analysis calls it before it reads data.
8. **Idempotence.** An event that has no edge out of the present state but is the event of the last
   row changes nothing and writes nothing, so an event sent two times gives the state of one.

**Schema version: no change.** The dataset is absent from a fresh project, so
`schema/schema_frozen.json` does not change and `scripts/dump_schema.py --check` passes.

### Consequences

- Good: the window, the command line and a recipe read one state, and each move has a row that
  says who or what made it and on which values.
- Good: a project from before this change, or one that `tether batch` corrected, stays in review
  until someone approves; nothing old reads as approved.
- Trade-off: a guard trusts the values the caller passes (α, γ, the source). The row keeps them, so
  a later reader sees what the guard read. The version records of #926 and #929 make those values
  checkable against the store.
- Trade-off: an event sent in a state the table does not list is an error, also where the caller
  might expect it to be harmless (an evidence change in GAMMA_REVIEW). The caller decides; the
  machine does not invent an edge.
- Follow-up: #896 shows the state by name in `tether status`; #926, #927 and #929 send the evidence
  events; #930 and #931 are the pages; #899 and `tether approve` are the policy and command-line
  routes; #624 adds one scope per condition; #933 labels each apparent-E analysis.

## More information

- Issue #925; plan #890; diagram `docs/design/03-correction-state-machine.dot`. ADR-0102 (approval
  steps and policies), ADR-0103 (the graph), ADR-0126 (stale marks), ADR-0029 (the effective
  factors), ADR-0005 (schema freeze). Tests: `tests/test_correction_states.py`. Store layout:
  [`docs/reference/tether-format.md`](../reference/tether-format.md).
