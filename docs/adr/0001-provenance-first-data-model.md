# 0001 — Provenance-first project store

- **Status:** accepted
- **Date:** 2026-06-24
- **Deciders:** bioedca
- **PRD anchor:** §3 (vision), §5 (data model); NFR-REPRO
- **Milestone:** M0

## Context and problem statement

smFRET pipelines routinely sever the link between a processed trace and the
pixels, coordinates, corrections, and software version that produced it. Once
that link is gone, results cannot be re-derived, audited, or trusted. How should
Tether structure its data so that provenance is never lost?

## Decision drivers

- Reproducibility is a non-functional requirement (NFR-REPRO): a result must
  resolve back to a specific verified commit + frozen `conda-lock`.
- The trace ⇄ movie round-trip (ADR-0002) requires coordinates and patches to
  travel with every molecule.
- Single self-describing artifact beats a scatter of sidecar files for a
  desktop, lab-shared workflow.

## Considered options

- **A. Provenance-first single HDF5 store** — coordinates, correction factors,
  parameters, labels, and app-version stamped into one `.tether` file.
- **B. Processed traces only** (CSV/`.dat`), provenance in ad-hoc notes.
- **C. Relational/DB-backed store.**

## Decision outcome

Chosen option: **A**. Every datum carries its origin: `/molecules` hold stable
identity + coordinates, corrections store their factors and method, every
analysis stamps parameters + app-version (`git describe`). Provenance is a
property of the store, not a convention.

### Consequences

- Good: results are auditable and re-derivable; round-trip and staleness
  tracking become possible.
- Trade-off: a richer schema that must be designed up front — hence the M0
  schema freeze (ADR-0005).
- Follow-up: enforced by the `schema-guard` gate and the §0.4 DoD "provenance
  stamped" item.

## More information

PRD §5.1 group skeleton; ADR-0002, ADR-0005.
