# 0002 — The `.tether` store is an SMD superset; round-trip is a data-model property

- **Status:** accepted
- **Date:** 2026-06-24
- **Deciders:** bioedca
- **PRD anchor:** §5.3 (interoperability), §7.3 (round-trip); FR-ROUNDTRIP, FR-IDEALIZE
- **Milestone:** M0 (declared) → M2 (realized)

## Context and problem statement

Tether must interoperate with tMAVEN (SMD-HDF5) and let users hand traces off to
the standalone GUI and bring idealizations back — while never losing the
trace ⇄ movie link. How do we reconcile a standard interchange format with the
extra coordinate/patch provenance the round-trip needs?

## Decision drivers

- tMAVEN reads/writes the SMD `dataset/{data,sources,tMAVEN}` container
  [Greenfeld2015]; a hand-off must open directly in standalone tMAVEN.
- The return leg cannot trust IDs/order (the user may reorder/subset in tMAVEN).
- Round-trip must survive even when coordinates are absent on re-import.

## Considered options

- **A. `.tether` as an SMD superset** — export the exact SMD container with
  Tether's coordinates as *ignored superset metadata*; re-import by **exact
  intensity-trace matching**, ID/order as a hint only.
- **B. A bespoke format** with a converter to/from SMD.
- **C. Store only SMD** and reconstruct coordinates heuristically.

## Decision outcome

Chosen option: **A**. The round-trip is a **property of the data model**: a
molecule is re-identified by matching its raw intensity trace, so a reordered or
subset SMD still re-imports non-destructively as a new `/idealization/{model}`.

### Consequences

- Good: clean tMAVEN interop; non-destructive re-import; resilient to user edits.
- Trade-off: an intensity-matcher must be implemented and tested against
  reordered/subset inputs (M2 S7).
- Follow-up: round-trip integrity is exercised by oracle (f) and the M6
  consensus/ebFRET round-trips.

## More information

PRD §5.3, §7.4, Appendix D.1; ADR-0001.
