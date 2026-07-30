<!--
SPDX-FileCopyrightText: 2026 The Tether Authors
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0053 — Structured backlog intake gates swarm admission

- **Status:** accepted, **superseded by [ADR-0057](0057-github-native-swarm-coordination.md)** on who
  may admit work. The intake gates themselves — issue forms, `status:ready`, maintainer approval of a
  scope hash — **remain in force**; ADR-0057 changes the admitting authority, not that admission is gated.
- **Date:** 2026-07-26
- **Deciders:** bioedca
- **PRD anchor:** §12.5 (issue tracking and project planning)
- **Milestone:** M10

## Context and problem statement

[ADR-0052](0052-concurrent-agent-swarm-coordination.md) defines how an accepted
`status:ready` issue is claimed and isolated, but it deliberately begins after
maintainer approval. The public issue forms previously collected useful
report-specific detail without consistently collecting the acceptance,
dependency, autonomy, overlap, and scope information needed to make that
approval safe. Blank issues were disabled, yet no form covered maintenance or
governance work.

How should public intake produce groomable backlog items without allowing a
filer or a form to bypass maintainer approval and enter the autonomous swarm?

## Decision drivers

- A new issue must not grant itself `status:ready` or swarm eligibility.
- Maintainers need the same routing evidence on every work-producing form.
- Questions must remain answerable without becoming executable work by accident.
- Security reports, secrets, and private or unlicensed data must stay out of
  public intake.
- The form taxonomy and routing labels need an order-independent executable
  contract so later template edits cannot silently weaken admission.
- Maintenance, CI, test, refactor, and governance work need structured intake
  while blank issues remain disabled.

## Considered options

1. **Keep report-specific forms and groom missing information in comments.**
   This preserves the existing templates, but every issue begins with a
   different admission surface and the maintainer cannot tell whether omitted
   routing evidence is absent or merely unstated.
2. **Re-enable blank issues or add one free-form engineering template.** This
   admits every work type, but restores unstructured intake and cannot enforce
   the safety or routing fields required before autonomous execution.
3. **Have forms apply `status:ready` when all required fields are present.**
   This is mechanically convenient, but confuses completeness with authenticated
   maintainer approval and lets a filer authorize swarm work.
4. **Use shared structured backlog controls, followed by maintainer grooming and
   exact-snapshot approval** (chosen).

## Decision outcome

Chosen option: **"Use shared structured backlog controls, followed by
maintainer grooming and exact-snapshot approval"**, because it creates a uniform
admission boundary without changing the ownership, lease, review, or merge
semantics in ADR-0052.

Every public issue form applies exactly one `status:backlog` label and no form
applies `status:ready`. Each work-producing form requires testable acceptance
criteria, dependencies or the literal `none`, one of the registered execution
autonomy choices, related-work or file overlap or the literal `none`, and a
one-PR scope/non-goals statement. Every form requires the shared duplicate,
private-security, secrets, and private/raw/unlicensed/user/lab-data
attestations.

`maintenance.yml` is the intake surface for chore, CI, test, refactor, and
governance work. It starts as `type:chore`; maintainers may correct the type
during grooming. Validation-oracle failures alone retain the automatic
`priority:P0` label. Questions are explicitly non-worker intake and require a
separate work issue before they can become ready.

The lifecycle is:

`new → status:backlog → groomed → exact-body maintainer approval → status:ready`

Required fields make an issue groomable; they never make it approved. ADR-0052
starts at the next boundary: only the authenticated, approved `status:ready`
snapshot can receive a lease. Its one-issue/branch/worktree/PR rule and its
separate review and merge-authority controls are unchanged.

`tests/test_issue_forms.py` parses the complete `.yml`/`.yaml` form set without
depending on field order. It pins the form filenames, exact `type:` and
`status:` routing labels, shared required fields and attestations, priority
exception, milestone coverage, question exclusion, and disabled blank issues.

### Consequences

- **Good.** New work arrives with enough evidence for deterministic grooming,
  dependency routing, overlap checks, and a bounded one-PR increment.
- **Good.** Completeness and authority remain separate; neither a filer nor a
  template can self-promote work into the swarm.
- **Good.** Maintenance work has a public form without reopening blank issues,
  and questions cannot accidentally consume a worker slot.
- **Trade-off.** Filing work takes longer and repeats shared fields across YAML
  files; the contract test intentionally fails when a new form omits them.
- **Trade-off.** Static `type:chore` is deliberately coarse for maintenance
  intake and may require maintainer correction during grooming.
- **Follow-up.** Changes to the admission boundary, registered autonomy choices,
  or question eligibility require an ADR amendment or superseding decision and
  matching contract-test updates.

## Amendment, 2026-07-30 — `size`, `risk` and `adr_needed` at intake

This record's own Follow-up requires an amendment for a change to the registered
field set, so this is it rather than a new decision.

**What changed.** Each of the five work forms gains three required `dropdown`
controls — `size`, `risk`, `adr_needed` — added to `ROUTING_FIELD_IDS` with frozen
option sets asserted the same way `AUTONOMY_OPTIONS` is. `question.yml` is
unchanged and remains non-worker intake.

**What did not change: the admission boundary.** Admission is still
`status:ready` plus an authenticated maintainer approval of the scope hash, and
neither a filer nor a template can self-promote work. These three answers are
*evidence for grooming*, not authority — a reporter selecting `risk: low` does not
make the work low-risk, exactly as an `autonomy` answer never granted autonomy.

**Why they are required rather than optional.** Both are consumed by machinery
that did not exist when this record was written. `risk:*` selects the reviewer
under [ADR-0057](0057-github-native-swarm-coordination.md)'s review gate, and
`size:*` carries the diff budget `.agents/bin/scope_guard.py` measures against.
An unanswered field leaves an issue `status:ready` with no lane able to take it,
which is the state #240, #242, #243 and #244 are in.

**Why `dropdown` and not `checkboxes`.** `_is_required` requires a literal
`validations: {required: true}`; GitHub puts a per-option `required:` inside
`checkboxes` instead, so a checkbox control would satisfy "the field exists" while
collecting nothing.

**Deliberately still out.** No `preauth` control — `preauth` records a
*maintainer's* standing authorization, and a form field would let an untrusted
reporter assert their own. No `non_goals` field — `scope` is already titled
"Scope and non-goals". No automatic labelling: a form's `labels:` list is static
and no `on: issues:` workflow exists, so applying these answers as labels is a
separate change with its own risk class.

## More information

- [ADR-0052](0052-concurrent-agent-swarm-coordination.md) owns coordination from
  authenticated `status:ready` approval onward.
- `docs/PRD.md` §12.5 records the issue lifecycle and public form taxonomy.
- `CONTRIBUTING.md` records the contributor-facing grooming lifecycle.
- `.github/ISSUE_TEMPLATE/` and `tests/test_issue_forms.py` are the executable
  intake contract.
