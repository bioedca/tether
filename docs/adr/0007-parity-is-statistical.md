# 0007 — Idealization parity is statistical, asserted against a frozen tolerance

- **Status:** accepted
- **Date:** 2026-06-24
- **Deciders:** bioedca
- **PRD anchor:** §7.4 (idealization parity), §11.2, §8 NFR-VALID(b)
- **Milestone:** M0.5 (freeze) → M2, M6 (inherit)

## Context and problem statement

Tether's one-click vbFRET (and later consensus/ebFRET) must agree with standalone
tMAVEN. But tMAVEN's variational inference **self-reseeds**, so runs are not
bit-identical even on the same input. How do we define and enforce "parity"
without chasing nondeterminism?

## Decision drivers

- Variational Bayes HMM results vary run-to-run; exact equality is impossible.
- A trustworthy, non-flaky gate must compare against a *measured* spread, not a
  guessed threshold.
- M2 (one-click) and M6 (consensus/ebFRET) both depend on this gate.

## Considered options

- **A. Statistical parity vs a frozen tolerance** — measure the cross-seed spread
  from ≥20 standalone-tMAVEN runs on committed SMDs, freeze four numbers (state
  count agreement, per-state mean ΔE, Viterbi per-frame agreement, relative
  ΔELBO) into §11.2 + `schema/parity_tolerance.json`, and assert against them.
- **B. Bit-exact comparison** (impossible — self-reseeding).
- **C. Pin tMAVEN's RNG seed** (brittle; not how the standalone runs).

## Decision outcome

Chosen option: **A**. M0.5 S4 ratifies and **freezes** the tolerance; the
`sidecar/parity` CI job asserts *against* the frozen numbers and never recomputes
them. M2 and M6 inherit the tolerance by reference and cannot sign off until it
is frozen (a hard gate).

### Consequences

- Good: a stable, non-flaky parity gate grounded in measured variance.
- Trade-off: the freeze is a prerequisite milestone gate (M0.5 S4) blocking M2/M6.
- Follow-up: provisional defaults (≥90% / ≤0.02 / ≥95% / ≤0.01) are replaced by
  measured values at M0.5 S4 — **done** (the measured cross-seed spread was
  negligible, confirming the defaults; see [ADR-0009](0009-parity-metrics-and-freeze.md)).

## More information

PRD §7.4, §11.2, §8 NFR-VALID(b); PLAN §1.1 hard gates, M0.5 S4. The metric
definitions, freeze policy, and measured result are recorded in
[ADR-0009](0009-parity-metrics-and-freeze.md).
