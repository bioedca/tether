# 0008 — Deep-LASI → Tether correction-factor naming remap (β→α, α→δ, γ→γ)

- **Status:** accepted
- **Date:** 2026-06-24
- **Deciders:** bioedca
- **PRD anchor:** Appendix B.1 (naming map), §7.2; FR-CORRECT, FR-LEGACY
- **Milestone:** M0.5 (decode) → M3 (apply)

## Context and problem statement

Tether adopts the field-standard α/δ/γ convention [Hellenkamp2018], [Lee2005]:
**α** = donor→acceptor leakage (additive), **δ** = direct excitation (= 0,
single-laser), **γ** = detection/QY ratio (multiplicative). Deep-LASI's internal
MATLAB naming is **inverted**: its stored `Beta` holds leakage and its stored
`Alpha` holds direct excitation. When importing Deep-LASI `.tdat`/`.mat` factors,
how do we map them without silently corrupting E?

## Decision drivers

- Misattributing Deep-LASI's `Beta` (leakage) drops a real leakage correction and
  shifts every imported E.
- Folding leakage into γ would double-count and is physically wrong.
- The remap must be explicit, unit-tested, and documented next to the importer.

## Considered options

- **A. Explicit remap on import** — Deep-LASI **β → Tether α** (apply,
  additive); Deep-LASI **α → Tether δ** (inert / 0, ALEX-only); **γ → γ**. A
  unit test asserts β is never folded into γ and Deep-LASI α is never treated as
  Tether α.
- **B. Pass factors through by name** (wrong — inverts leakage vs direct
  excitation).
- **C. Re-estimate all factors natively**, ignoring imported ones (loses legacy
  calibration).

## Decision outcome

Chosen option: **A**. The `tether.io` importer applies the remap (PRD Appendix
B.1); δ is carried as inert 0 in the single-laser 2-color scheme; the load-bearing
correction order is background → α → δ(0) → γ (Appendix B.2).

### Consequences

- Good: imported Deep-LASI results match Tether's native convention; no silent E
  shift.
- Trade-off: importer must track source provenance and the remap explicitly.
- Follow-up: a remap unit test (β never folded into γ) lands with the M0.5 `.tdat`
  decode and is re-exercised at M3/M7.

## More information

PRD Appendix B.1/B.2; `deeplasi/functions/deeplearning/deep_autocorrect_2color.m`;
`deeplasi/functions/traces/manualCorrectionFactors.m`; PLAN M0.5 S6, M3.
