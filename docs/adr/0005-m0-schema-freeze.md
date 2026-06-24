# 0005 — Freeze the HDF5 schema skeleton at M0 (additive-only thereafter)

- **Status:** accepted
- **Date:** 2026-06-24
- **Deciders:** bioedca
- **PRD anchor:** §5 (data model), §9 M0, §12.6 (`schema-guard`)
- **Milestone:** M0

## Context and problem statement

The provenance store (ADR-0001) is read and written across every milestone M0–M9.
If its structure drifts ad hoc, older `.tether` files stop opening and provenance
guarantees erode. How do we let the schema grow with new analyses without
breaking compatibility?

## Decision drivers

- Provenance and round-trip depend on a stable set of identity/coordinate/label
  fields being present from the start.
- Structural churn across milestones would be unreviewable and migration-prone.
- A machine-checkable gate is needed — prose rules are not enough.

## Considered options

- **A. Forward-declare the entire §5 group skeleton at M0, version-stamp it, and
  allow only additive *data* thereafter**, enforced by a `schema-guard` CI gate;
  any structural change requires an ADR + an explicit schema-version bump.
- **B. Evolve the schema freely**, fixing readers as needed.
- **C. Version every group independently.**

## Decision outcome

Chosen option: **A**. M0 S6 declares the full skeleton (`/movies`,
`/molecules` with `molecule_id`/`molecule_key`, `/labels` provenance,
`/idealization`, `/conditions`, …) with a monotonic schema-version. `schema-guard`
diffs declared-vs-golden: additions pass; removals/renames/dtype/identity changes
fail. This is the keystone invariant.

### Consequences

- Good: every later milestone adds data, not structure; old files keep opening.
- Trade-off: the M0 schema must be designed comprehensively up front (a 2-session
  keystone PR).
- Follow-up: a legitimate structural change is the one sanctioned `!`
  breaking-change, carrying an ADR + version bump.

## More information

PRD §5.1–§5.4, Appendix D; PLAN M0 S6; ADR-0001.
