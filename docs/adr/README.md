# Architecture Decision Records (ADRs)

This directory records Tether's architecture decisions in
[MADR](https://adr.github.io/madr/) format. Each ADR captures one decision, its
context, the options weighed, and the consequences, so the *rationale* survives
prose harmonization of the PRD (PRD §12.7).

## How ADRs are created

- New file: `NNNN-kebab-title.md`, copied from [`0000-template.md`](0000-template.md),
  `NNNN` = the next zero-padded integer.
- **Incremental rule (PLAN §0.4 DoD).** A PR that *implements* a still-unhomed
  resolved PRD decision lands that decision's ADR **in the same PR**.
- **M9 gate.** The M9 docs PR fails unless this index is complete (no placeholder
  gaps) — closing PRD §12.7's "home the resolved decisions" deliverable.

## Index

### Foundational, cross-cutting (M0 seed — PLAN M0 S1)

| ADR | Title | Status | PRD anchor |
|----:|-------|--------|------------|
| [0001](0001-provenance-first-data-model.md) | Provenance-first project store | accepted | §3, §5 |
| [0002](0002-smd-superset-round-trip.md) | The `.tether` store is an SMD superset; round-trip is a data-model property | accepted | §5.3, §7.3 |
| [0003](0003-apparent-e-never-nan.md) | Total-correction-failure falls back to apparent-E, never NaN | accepted | §7.2 |
| [0004](0004-pin-and-hold-dual-lock-isolation.md) | Pin-and-hold conda-lock with an isolated tMAVEN sidecar lock | accepted | §4.1, §4.3 |
| [0005](0005-m0-schema-freeze.md) | Freeze the HDF5 schema skeleton at M0 (additive-only thereafter) | accepted | §5, §9 M0 |
| [0006](0006-sidecar-ipc-escalation.md) | Pre-committed sidecar escalation: headless `maven_class`, else bundled IPC | accepted | §4.3, §9 M0.5 |
| [0007](0007-parity-is-statistical.md) | Idealization parity is statistical, asserted vs a frozen tolerance | accepted | §7.4, §11.2 |
| [0008](0008-correction-factor-remap.md) | Deep-LASI → Tether correction-factor naming remap (β→α, α→δ, γ→γ) | accepted | Appendix B.1 |

_Later decisions (the rest of the PRD §12.7 backfill set) are homed incrementally
by the PRs that implement them._
