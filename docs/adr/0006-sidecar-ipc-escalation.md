# 0006 — Pre-committed sidecar escalation: headless `maven_class`, else bundled IPC

- **Status:** accepted
- **Date:** 2026-06-24
- **Deciders:** bioedca
- **PRD anchor:** §4.3 (sidecar), §7.11, §9 M0.5, §10
- **Milestone:** M0.5

## Context and problem statement

Idealization (vbFRET / consensus VB-HMM / ebFRET) runs in the isolated tMAVEN
sidecar (ADR-0004). tMAVEN's computational core, `tmaven.maven.maven_class`
(`tmaven/tmaven/maven.py:15`), imports no Qt and is in principle callable
headlessly — but this may not be reproducible across Windows/macOS/Linux. What is
the mechanism, and what is the fallback if it isn't?

## Decision drivers

- Numpy isolation must be preserved (no in-process embed of `numpy<2`).
- A hand-off-only MVP is insufficient — one-click idealize and M9 bundling need
  programmatic driving.
- The decision must be pre-committed so M0.5 doesn't stall on discovery.

## Considered options

- **A. Drive headless `maven_class` in the sidecar env**; if it proves
  non-reproducible cross-OS, **escalate to a prebuilt bundled sidecar over a
  stable IPC** (stdio-JSON / local socket) with a supervisor (per-call timeout +
  liveness, bounded auto-restart, idealization-deferred mode).
- **B. In-process embed** (breaks numpy isolation).
- **C. Hand-off only** (no one-click; insufficient for M9).

## Decision outcome

Chosen option: **A**. M0.5 S1–S2 attempt headless `maven_class`; S3 implements the
IPC-bundled fallback **only if** cross-OS reproducibility fails, and is **skipped
with a logged decision** otherwise. Either way numpy isolation holds.

### Consequences

- Good: a working mechanism is guaranteed without blocking M0.5; isolation
  preserved; offline-bundleable at M9.
- Trade-off: a possible second implementation path (IPC) kept on the shelf.
- Follow-up: the chosen mechanism is recorded in the §15 Session log + this ADR
  is updated to name it.

## More information

PRD §4.3, §9 M0.5, §10; PLAN M0.5 S1–S3; ADR-0004.
