<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0102 — A batch run stops at a step that needs a person, unless the recipe names a policy

- **Status:** accepted
- **Date:** 2026-11-14
- **Deciders:** bioedca (the rules are his decisions of 2026-11-14 on the cards of plan #890: B1 option C,
  B2 option B, B3 option A, B4 option A; issue #894)
- **PRD anchor:** §7.11 (FR-BATCH, the parity rule), §7.1 (low-confidence registration), §7.2 (correction states)
- **Milestone:** M14

## Context and problem statement

The parity rule of §7.11 says that each action of the application has a command-line route and a recipe step.
Some steps of the workflow need a person: in the application, a person looks at the evidence and approves. A batch
run has no person. Today `tether batch` has one rule of this type: a map above the RMS gate is kept with a flag
(`--policy warn`, the default) or the movie fails (`--policy fail`) (ADR-0014, ADR-0031). The correction pass of
`tether batch` uses the estimated factors with no approval at all.

Which steps need a person, what does a batch run do at each of them, and what does the store record? Issues
#899 (the recipe runner), #918 (map commands), #925 (correction states), #939 (active fit) and #958 (deposit)
implement this contract.

## Decision drivers

- **Full batch compatibility** (§7.11): a run in the application can be done again with no window.
- **No silent approval.** The store must show who or what approved a step, and on which evidence.
- **No change for today's users.** `tether batch` keeps its flags, its exit codes and its default behaviour.
- **Publication cannot be undone** (§7.14, card B3), so no unattended route may publish.

## Considered options

- **A — A batch run always stops** at a step that needs a person. Safe, but a recipe can never run overnight.
- **B — A batch run always applies a built-in default.** This is the present `tether batch`, and it approves with
  no record that a person or a rule did it.
- **C — Stop by default; the recipe can name a policy for the step**, and the store records the policy.

## Decision outcome

Chosen option: **"C"** (card B1), because it keeps a run unattended only where the user wrote down the rule, and
the record says so.

### 1. The approval steps

There are four approval steps. Each step has an identifier, which the recipe, the store and the messages use, and
a subject.

| Step | What a person does in the application | Subject | Where it is built |
|---|---|---|---|
| `map.accept` | Accept a map with its quality record (FR-WORKFLOW-9, §7.1) | the map file and the movie | #918, #351 |
| `alpha.confirm` | Confirm the bleedthrough factor α of a condition (G03, BLEEDTHROUGH_REQUIRED) | the condition | #925, #930 |
| `gamma.approve` | Approve γ of a condition (G03, GAMMA_REVIEW) | the condition | #925, #930 |
| `fit.select` | Select the active fit in one score context (G04) | the analysis set and the score context | #939 |

(G03 calls α the bleedthrough factor; PRD Appendix B.1 gives the naming map.) A new approval step needs a change of
this record.

**Curation is not an approval step** (card B2). A recipe names the molecule filter as a recipe value. The default
is `not-rejected`: all molecules that do not have the label *rejected*, which includes molecules with no label. Each
result records the filter text. A recipe never applies ranker proposals as labels (§7.5).

**Publication is not an approval step and not a recipe step** (card B3). No policy can publish a deposit. The
recipe check refuses a recipe with a publish step, and there is no step identifier for publication.
`tether deposit publish` is a separate command that needs the deposit id (#958).

### 2. The default: stop with exit code 4

A run that comes to an approval step and has no policy for it **stops** before the step. It exits with code
**`4`**, and it writes one message to stderr that names the step, the subject, the report, the command that
approves and the command that continues. For example:

```text
tether run: stopped at approval step 'gamma.approve' for condition 'KJG_231027': the recipe has no policy for it.
  evidence: runs/gamma-KJG_231027.json (and .png)
  approve:  tether approve exp.tether gamma.approve --subject KJG_231027 --report runs/gamma-KJG_231027.json
  continue: tether run recipe.toml --resume
```

The work done before the stop stays in the project as checkpoints of stored jobs (#895). A later run with
`--resume` continues from the checkpoint and does not do that work again. `tether approve` is the command-line
route of the approval service command (#892); #899 adds it, because the runner is the command that must name it.
A step-specific route, such as `tether map create --accept` (#918), sends the same request.

Code `4` has this meaning in **every** `tether` command, and no command may use `4` for another meaning. Codes
`0`, `1` and `2` keep their present meanings. A run that stops at an approval step and also had a failed item
exits `1`, because a failure is the more urgent message; the stop is in the summary.

### 3. The form of a policy

A policy is a recipe value: `step`, `rule`, the values of the rule, and `otherwise`, which says what happens when
the rule does not approve. The text form follows the request format of the ADR of #892; the examples here use JSON.
**A policy in a recipe states every value**: the recipe check refuses a policy with a missing value. Thus a policy
has no hidden default and adds no tunable to §11.2.

| Step | Rules | `otherwise` |
|---|---|---|
| `map.accept` | `rms-gate`: approve when the RMS residual is at most `gate_px`; `no_residual` (`approve` or `stop`) says what to do with a map that has no measured residual, such as an imported `.tmap` without control points | `flag`, `fail` or `stop` |
| `alpha.confirm` | `estimate`: approve the estimated α when at least `min_qualifying_traces` molecules qualify; `value`: approve a given α with its `source` | `apparent`, `fail` or `stop` |
| `gamma.approve` | `estimate`: approve the estimated γ when at least `min_qualifying_traces` molecules qualify and γ is in `range`; `value`: approve a given γ with its `source` | `apparent`, `fail` or `stop` |
| `fit.select` | `best-score`: select the best score in the context of `family` and `score`; `model`: select the fit of `family` with `n_states` | `fail` or `stop` |

The meanings of `otherwise`: `stop` stops as in section 2 (exit `4`); `fail` fails the item (the movie or the
condition) and the run continues with the others (continue-on-error, exit `1`); `flag` approves the map with the
`low-confidence-registration` tag on each molecule (§7.1); `apparent` approves nothing, and the condition stays in
apparent E (G03 BLEEDTHROUGH_REQUIRED or CALIBRATION_BLOCKED, §7.2).

One example for each step:

```json
{"step": "map.accept", "rule": "rms-gate", "gate_px": 0.5, "no_residual": "stop", "otherwise": "flag"}
{"step": "alpha.confirm", "rule": "value", "alpha": 0.07, "source": "donor-only sample of 2026-10-02, donly.tether", "otherwise": "stop"}
{"step": "gamma.approve", "rule": "estimate", "min_qualifying_traces": 10, "range": [0.5, 2.0], "otherwise": "stop"}
{"step": "fit.select", "rule": "best-score", "family": "vbconhmm", "score": "elbo", "otherwise": "stop"}
```

`best-score` ranks only inside one score context (#939). The recipe check refuses `best-score` for a family that
has no score (K-means).

### 4. What the store records

The store records each approval, by a person or by a policy:

- `step` and `subject`;
- `by`: `person` or `policy`;
- for a person, the name, read in the same way as the `labeler` of a curation label (`default_labeler()`, or
  `--by` on `tether approve`); for a policy, the policy text exactly as the recipe gives it;
- the identity of the recipe: the SHA-256 of its text and its schema version, or none for an approval in the
  application or by `tether approve`;
- the time, in UTC, ISO 8601;
- the report that was the evidence (card B4): its JSON and its PNG images, each with its SHA-256. An approval with
  no report is refused. For a policy, the run makes the report with the same code as the review page.

A policy that does not approve (`apparent`, `fail`, `stop`) records no approval. The run log records the result of
the rule. The layout of the record is additive (§5) and the first implementing pull request fixes it, with
`scripts/dump_schema.py --check`.

### 5. The present rule of `tether batch` is a policy

`tether batch` never stops at an approval step and never exits `4`. It runs with an implicit policy that gives
its present behaviour:

| Step | `--policy warn` (default) | `--policy fail` |
|---|---|---|
| `map.accept` | `rms-gate`, `gate_px` = the §11.2 RMS gate, `no_residual: approve`, `otherwise: flag` | the same, `otherwise: fail` |
| `alpha.confirm`, `gamma.approve` | `estimate` with the §11.2 `min_qualifying_traces` and no `range`, `otherwise: apparent` | the same |
| `fit.select` | not reached: `tether batch` makes one fit for each movie | the same |

`no_residual: approve` keeps today's rule for an imported `.tmap` with no control points: its residual is not a
number, so it is not over the gate, and it gets no flag (ADR-0014). The default behaviour of `tether batch` does not
change: the same flags, outputs, tags, exit codes and
`/settings/batch` stamp. When the record of section 4 exists, `tether batch` also writes it, with the implicit
policy as the policy text, so that its approvals have the same record as the approvals of a recipe.

### 6. The name of a person in a copy for sharing

The name of a person who approved is personal data of the same type as the `labeler` of a label
(`docs/privacy.md`). The export of a copy for sharing (#620) replaces it with the same stable alias that it gives
to the labeler: one person has one alias in the labels and in the approvals of the copy. A policy approval has no
person name, and the copy keeps its policy text.

### Consequences

- Good: a recipe can run with no person, and only where the user wrote the rule down. Each approval says who or
  what approved, and on which evidence.
- Good: one exit code, `4`, says "a person must act" for every command, so a script can tell it from a failure.
- Bad / trade-off: a recipe with no policies stops at each approval step. That is the safe default (card B1).
- Bad / trade-off: a policy makes the run depend on values that the user writes, and a bad value approves bad
  evidence. The report and the record keep the evidence, so a person can check the approval later.
- Follow-up: #899 adds `tether run`, `--resume`, `tether approve`, the recipe check and the tests of exit `4`;
  #918, #925 and #939 add the steps; #958 makes publication a separate command; #620 adds the alias.

## More information

- PRD §7.11 (the parity rule and cards B1 to B4), §7.1, §7.2, §7.13, §7.14; plan #890.
- ADR-0014 (the RMS gate and the over-gate branch), ADR-0031 (batch supervision), ADR-0068 (one project holds one
  experiment).
- The exit codes are in [the command-line reference](../cli.md#exit-codes).
