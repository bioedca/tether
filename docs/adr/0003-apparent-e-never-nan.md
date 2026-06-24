# 0003 — Total-correction-failure falls back to apparent-E, never NaN

- **Status:** accepted
- **Date:** 2026-06-24
- **Deciders:** bioedca
- **PRD anchor:** §7.2 (corrections), §10; FR-CORRECT
- **Milestone:** M0 (principle) → M3 (realized)

## Context and problem statement

Correction factors (leakage α, detection γ) can fail to estimate — e.g. no
donor-only sample, or fewer than the minimum qualifying traces. A naive pipeline
divides by an empty-set median and writes `NaN`, silently poisoning every
downstream histogram, TDP, and export. What should Tether emit when correction
cannot be computed?

## Decision drivers

- A `NaN` factor is a silent, propagating bug that CI and review will not catch.
- Users still need a usable, clearly-labeled result when correction is
  impossible.
- The min-qualifying-traces gate must be applied **before** the median, not
  after.

## Considered options

- **A. Fall back to apparent-E + a non-blocking banner**, stamp `method`, offer
  recovery actions (load donor-only / manual factor entry); never write a NaN
  factor.
- **B. Emit NaN** and let downstream filter it.
- **C. Block analysis** until correction succeeds.

## Decision outcome

Chosen option: **A**. The qualifying gate is applied before any median, so an
empty set never produces a NaN; on total failure Tether retains **apparent E**
(UI-labeled), shows a banner, and stamps the method. Manual override of every
factor is always available.

### Consequences

- Good: results are never silently corrupted; the failure is visible and
  recoverable.
- Trade-off: apparent-E vs corrected-E must be tracked and labeled everywhere.
- Follow-up: enforced by oracle (e) — "a dataset with no donor-only and
  < min_qualifying_traces falls to apparent-E + banner and never writes a NaN".

## More information

PRD §7.2, Appendix B.2 step 5; the §1.3 PLAN invariant "apparent-E never NaN".
