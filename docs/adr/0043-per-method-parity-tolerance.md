<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0043 — Per-method idealization-parity tolerance (ebFRET frozen separately)

- **Status:** accepted
- **Date:** 2026-07-08
- **Deciders:** bioedca
- **PRD anchor:** §7.4 (parity definition), §11.2 (the tolerance row), §12.6 (`sidecar/parity`), §9 M6
- **Milestone:** M6 (PR-1b — the idealization-parity oracle's ebFRET arm)

## Context and problem statement

[ADR-0009](0009-parity-metrics-and-freeze.md) froze the four §11.2 parity numbers
from a cross-seed spread measured **only on the consensus VB-HMM (`vbconhmm`)
path**, and `schema/parity_tolerance.json` recorded the design intent that this is
"ONE tolerance applied to all idealization methods" — vbFRET and ebFRET included.
[ADR-0041](0041-population-model-and-ebfret.md) then added **ebFRET (`ebhmm`)** as a
second global idealizer and explicitly deferred its live parity ratification.

Wiring up that ratification (PR-1b) exposed a problem with the shared-tolerance
assumption. A live sidecar dispatch (run `28960381550`) of an ebFRET cross-seed
oracle on the 281-mol SMD showed ebFRET **reproduces its kinetic model across
seeds** — per-state mean levels, Viterbi framewise agreement, and relative ELBO all
sit within the frozen row — **but its per-trace state-count agreement is ~0.68–0.74**,
far below the frozen `state_count_min_fraction = 0.9` floor. That floor is honest for
vbconhmm (whose measured state-count worst case was **1.0**), but ebFRET's
empirical-Bayes fit legitimately assigns a different *number* of occupied states to
some traces across seeds. So "one tolerance for all methods" is false for ebFRET on
the state-count metric.

## Decision drivers

- **Never loosen the frozen vbconhmm row to accommodate ebFRET.** Widening the shared
  `state_count_min_fraction` to ~0.52 would blind the vbconhmm/vbFRET gate to real
  regressions — the tolerance would no longer mean what it measured.
- **Never fabricate or weaken a test to hide the gap.** The honest fix is to *measure*
  ebFRET's own spread and freeze from it, exactly as M0.5 did for vbconhmm.
- **ebFRET cannot be measured against the committed reference.** The 281-mol reference
  model is a vbconhmm fit; `compare_models` scores `relative_elbo` (`|ΔELBO|/|ELBO|`),
  and an ELBO is a *model-specific* variational bound — an ebFRET fit's is not
  commensurable with a vbconhmm model's, so a cross-method comparison would fail on
  ELBO for reasons unrelated to kinetic agreement.
- State-count agreement is a real parity signal worth keeping, not dropping for
  ebFRET.

## Decision

Freeze **per-method** tolerances. `schema/parity_tolerance.json` keeps its top-level
`tolerance` (the M0.5 vbconhmm freeze, the default) **unchanged**, and gains an
additive:

- `tolerance_by_method` — `{ "ebhmm": { the four bounds } }`;
- `measured_by_method` — the matching provenance + recorded spread each per-method
  block is frozen from.

`tether.idealize.parity.load_frozen_tolerance(path, method=None)` selects: a method
with its own block returns it; a method without one — or `method=None` — falls back
to the default `tolerance` (back-compatible; the vbconhmm/vbFRET tests are untouched).

ebFRET is measured **cross-seed** (two self-reseeded fits agree — the same-method
oracle, no ELBO cross-method problem, no fabricated reference) via a new manual
ratification workflow (`sidecar-measure.yml`, from the `--model-type ebhmm
--cross-seed` measurement harness, PR #106). Run `28963324581` — 20 self-reseeded
ebFRET fits on the 281-mol SMD, 19 cross-seed comparisons, margin 0.5 — frozen:

| metric | ebFRET (measured worst → frozen) | vbconhmm default |
|---|---|---|
| `state_count_min_fraction` (floor) | 0.6833 → **0.5249** | 0.90 |
| `state_mean_abs_delta_max` (ceiling) | 0.0345 → **0.0518** | 0.02 |
| `viterbi_min_agreement` (floor) | 0.9356 → **0.9034** | 0.95 |
| `relative_elbo_max` (ceiling) | 0.00066 → **0.01** | 0.01 |

The live ebFRET arm asserts against `load_frozen_tolerance(..., method="ebhmm")`.
`tests/test_parity.py` gains a per-method counterpart of
`test_frozen_artifact_covers_its_own_measured_evidence`, so every per-method block is
validated against its own recorded evidence in the base CI matrix.

## Consequences

- The ebFRET arm passes against its **own measured floor** — honestly, by measuring
  ebFRET's true spread, not by weakening any shared bound. The vbconhmm/vbFRET gate is
  bit-for-bit unchanged and still tight.
- This **amends** [ADR-0009](0009-parity-metrics-and-freeze.md) / the
  `parity_tolerance.json` coverage note: parity is per-method where a method's
  cross-seed spread is materially different, not one-size-fits-all. The freeze
  *policy* (`freeze()`, margin 0.5) and the four *metric definitions* (`compare_models`)
  are unchanged and remain the single source of truth.
- Adding a future method's tolerance is a repeat of this recipe: dispatch
  `sidecar-measure.yml --model-type <m> --cross-seed`, fold the block in, extend the
  test, ADR it. Methods without their own block keep inheriting the default.
- No schema-guard / conda-lock impact: `parity_tolerance.json` is a config artifact,
  not the HDF5 `.tether` skeleton, and no dependency changed.

## Alternatives considered

- **Loosen the single shared tolerance to ebFRET's spread.** Rejected — it would
  slacken the vbconhmm/vbFRET gate to a 0.52 state-count floor, defeating the point of
  a measured tolerance and hiding real vbconhmm regressions.
- **Drop `state_count` as an ebFRET criterion.** Rejected — state-count agreement is a
  genuine parity signal; measuring ebFRET's honest floor keeps the signal while
  respecting that ebFRET's per-trace state selection is more variable.
- **Commit an ebFRET-specific reference model and compare fresh fits to it.** Rejected
  — no such reference exists, and cross-seed self-consistency is the defensible
  same-method oracle without fabricating a reference.
