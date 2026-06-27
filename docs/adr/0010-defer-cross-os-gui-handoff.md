# 0010 — Defer the 2-OS standalone-tMAVEN GUI hand-off check to M9

- **Status:** accepted
- **Date:** 2026-06-27
- **Deciders:** bioedca
- **PRD anchor:** §4.3 (sidecar), §7.4 (idealization hand-off), §9 M0.5(a), §9 M9
- **Milestone:** M0.5 (decision); the deferred work lands at M9

## Context and problem statement

M0.5 §9(a) acceptance has two legs: (1) a headless vbFRET round-trip drives the
sidecar and the exported SMD is valid; (2) the exported SMD opens in the
**standalone tMAVEN GUI** with coordinate metadata intact and the guided setup
script runs clean on **two OSes** (PLAN M0.5 S2 / issue #13). Leg 1 is done and
the *programmatic* cross-OS reproducibility of headless `maven_class` is already
confirmed (ADR-0006: a green `sidecar/parity` Linux dispatch + the Windows CI
matrix). Leg 2 is a manual, human-in-the-loop GUI interop check that cannot run
in autonomous CI and would otherwise block the M0.5 critical path and milestone
close. Should M0.5 wait on that manual check, or is it safe to defer it?

## Decision drivers

- A human-only manual check must not block autonomous development.
- M0.5's de-risking value — drive the sidecar headlessly and freeze the §11.2
  parity tolerance — is already achieved (ADR-0006, ADR-0009); leg 2 is interop
  confirmation, not a correctness gate for downstream code.
- Cross-OS validation belongs at packaging (M9), where signed cross-platform
  installers are built and exercised across OSes anyway.
- Do not *silently* relax an acceptance gate — the deferral must be explicit,
  with the work kept on a tracking issue, not dropped.

## Considered options

- **A. Defer leg 2 to M9.** Close M0.5 on the headless + Windows evidence; the
  standalone-GUI 2-OS hand-off is explicitly tracked by #13, re-milestoned
  M0.5 → M9, and becomes part of M9 acceptance.
- **B. Block M0.5 close** until a human runs the 2-OS check now (stalls progress
  on a manual step that does not gate code correctness).
- **C. Drop the 2-OS check entirely** (loses interop assurance — rejected; this
  is a deferral, not a deletion).

## Decision outcome

Chosen option: **A**. The 2-OS standalone-tMAVEN GUI hand-off verification is
deferred to M9; M0.5 may close on the headless + Windows evidence with leg 2
tracked by #13 (re-milestoned to M9). Numpy isolation and the parity freeze are
unaffected.

### Consequences

- Good: M0.5 carries no human blocker; autonomous development can proceed into
  M1; the GUI hand-off remains tracked and runs at M9 alongside cross-platform
  packaging and signed installers.
- Trade-off: standalone-tMAVEN GUI interop is not human-verified until M9.
  Mitigated — the `.tether` store is a documented SMD **superset** (ADR-0002)
  and tMAVEN reads standard SMD; the headless path already round-trips that same
  SMD successfully in CI.
- Follow-up: #13 is re-milestoned M0.5 → M9; **M9 acceptance must include**
  "exported SMD opens in standalone tMAVEN GUI with coordinate metadata intact;
  guided setup runs clean on ≥2 OSes (Windows + macOS/Linux)." This ADR is the
  logged-decision the deferral requires.

## More information

PRD §4.3, §7.4, §9 M0.5(a), §9 M9; PLAN M0.5 S2 / M9; issue #13; ADR-0002,
ADR-0004, ADR-0006, ADR-0009.
