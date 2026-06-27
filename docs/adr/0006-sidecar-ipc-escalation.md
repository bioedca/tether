# 0006 — Pre-committed sidecar escalation: headless `maven_class`, else bundled IPC

- **Status:** accepted — *resolved 2026-06-27 (M0.5 S3): headless `maven_class`
  is the mechanism; the IPC escalation was not built (see Resolution below).*
- **Date:** 2026-06-24 (resolved 2026-06-27)
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
- Follow-up (done 2026-06-27, M0.5 S3): the chosen mechanism is recorded in the
  §15 Session log and named in the Resolution below; the IPC escalation
  (option A's fallback) is **not** built.

## Resolution (2026-06-27, M0.5 S3)

**Headless `maven_class` is the sidecar mechanism; the IPC-bundled fallback is
not built.** Option A escalates to IPC *only if* headless `maven_class` proves
non-reproducible cross-OS — that condition did **not** occur:

- **Windows:** the headless driver (subprocess → runner → sidecar-Python →
  JSON-stat) was developed and run on the Windows dev machine in M0.5 S1
  (PRs #19, #20); the vbFRET fit-hang was diagnosed and resolved there.
- **Linux:** the `sidecar/parity` CI job drives the same headless `maven_class`
  vbFRET round-trip in the isolated sidecar env and asserts the result against
  the frozen §11.2 idealization-parity tolerance (ADR-0007, ADR-0009). It is
  confirmed **green** on `main` — Actions run `28276519587`
  (`workflow_dispatch` on `3765a49`, conclusion `success`), landed by PR #29.

Headless `maven_class` therefore drives idealization reproducibly across the
OSes exercised, with numpy isolation preserved (the sidecar env stays
`numpy<2`/PyQt5, never merged into the base stack — ADR-0004). The pre-committed
IPC escalation (a bundled sidecar over stdio-JSON / a local socket, with a
supervisor) is **kept on the shelf, not implemented**; it remains the documented
fallback should a future OS or tMAVEN version break headless driving. PLAN
M0.5 S3 and issue #14 are closed by this decision; no escalation code ships.

## More information

PRD §4.3, §9 M0.5, §10; PLAN M0.5 S1–S3 (§15 Session log, 2026-06-27);
issue #14; CI evidence PR #29 / `sidecar/parity` run `28276519587`;
ADR-0004, ADR-0007, ADR-0009.
