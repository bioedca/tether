# Standalone-tMAVEN cross-OS result record

**Who this page is for.** Tether maintainers with write access to `bioedca/tether`, who run the
manual GUI leg on a physical desktop and record its result here; users do not need this page.
The procedure that produces each row is the
[manual GUI leg](../idealize/standalone-tmaven-handoff.md#manual-gui-leg) on the user-facing
[Standalone-tMAVEN hand-off](../idealize/standalone-tmaven-handoff.md) page.

## Cross-OS result record

The M9 acceptance (ADR-0010) is: the exported SMD opens in standalone tMAVEN and the setup
script runs clean on **≥2 OSes** (Windows + macOS/Linux). The scripted half is verified on
Windows (developer) and Linux (CI); the manual GUI leg is recorded here as it is completed.

| OS | tMAVEN commit | Setup script | Scripted open-check | Manual GUI open | Date | Notes |
|---|---|---|---|---|---|---|
| Linux (CI) | `10f4230` | clean | pass | n/a (headless CI) | ongoing | `sidecar` workflow: setup script + `test_handoff_sidecar.py` on every live run. |
| Windows 11 | `10f4230` | clean | pass | pending | 2026-07-19 | Developer box; setup-script probe + `test_handoff_sidecar.py` (3/3) green. GUI leg pending. |
| macOS / Linux (desktop) | — | — | — | pending | — | Second desktop OS for the manual GUI leg. |

The scripted open-check and the guided setup script are green on two operating systems
(Windows developer + Linux CI). The manual GUI legs are tracked here for a maintainer to
complete on a physical desktop of each OS, which is the human-in-the-loop step ADR-0010
deferred to packaging time.
