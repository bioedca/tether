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
| [0006](0006-sidecar-ipc-escalation.md) | Pre-committed sidecar escalation: headless `maven_class`, else bundled IPC | accepted (resolved: headless, IPC not built) | §4.3, §9 M0.5 |
| [0007](0007-parity-is-statistical.md) | Idealization parity is statistical, asserted vs a frozen tolerance | accepted | §7.4, §11.2 |
| [0008](0008-correction-factor-remap.md) | Deep-LASI → Tether correction-factor naming remap (β→α, α→δ, γ→γ) | accepted | Appendix B.1 |

### Homed incrementally by implementing PRs

| ADR | Title | Status | PRD anchor |
|----:|-------|--------|------------|
| [0009](0009-parity-metrics-and-freeze.md) | Idealization-parity metric definitions and the M0.5 freeze | accepted | §7.4, §11.2 |
| [0010](0010-defer-cross-os-gui-handoff.md) | Defer the 2-OS standalone-tMAVEN GUI hand-off check to M9 | accepted | §9 M0.5(a), §9 M9 |
| [0011](0011-home-extraction-recall-at-m1.md) | Home the M0.5 ≥95% extraction-recall acceptance at M1 | accepted | §9 M0.5(b), §9 M1 |
| [0012](0012-registration-pairing-mutual-nn.md) | Registration pairing: mutual NN, fit on original coords; translation prealign first | accepted | App E §7–8, §11.2 |
| [0013](0013-fourier-mellin-similarity-prealign.md) | 4-DOF Fourier-Mellin similarity prealign: log-polar recovery, masked-NCC disambiguation, real-data oracle | accepted | App E §7, §11.2 |
| [0014](0014-registration-map-rms-gate-and-over-gate.md) | Registration map: numeric RMS gate, over-gate flag-don't-drop, unified native/imported calibration | accepted | §7.1, App E §9–10, §11.2 |
| [0015](0015-donor-anchored-colocalization.md) | Donor-anchored colocalization: keep dark/low-FRET acceptors, coordinate-domain apply, both-channel crop box | accepted | §7.1, App E §11–13, §11.2 |
| [0016](0016-extraction-trace-store-layout.md) | Extraction store layout: zero-pad-to-max-T `/traces`, cached patches, the `molecule_key` content hash, the apparent-E substrate | accepted | §5.1, §7.10, App E §14–15, §11.2 |
| [0017](0017-deeplasi-validation-reader.md) | Minimal Deep-LASI `.mat`/`.txt` validation reader: M1-scoped fields, 1-based→0-based coordinates, lazy scipy | accepted | §9 M1, §8 NFR-VALID (a), App A |
| [0018](0018-extract-cli-native-pipeline.md) | `tether extract` CLI: native auto-registration pipeline; imported `.tmap` + Deep-LASI oracle deferred to the S9 follow-up | accepted | §7.11, §9 M1, App E, §11.2 |
| [0019](0019-extract-cli-imported-tmap.md) | `tether extract --tmap`: imported registration path; trust the bead map (residual unknown), refuse a non-identity rotation/flip, defer the apply + oracle | accepted | §7.1, §7.11, §9 M1, App E §6–10 |
| [0020](0020-extraction-oracle-and-deferred-m1-close.md) | The extraction-vs-Deep-LASI acceptance oracle; M1 close deferred (full-scale detection-faithfulness gap surfaced: ~20% recall) | accepted | §9 M1, §8 NFR-VALID (a), §7.11, §2.2, App A |
| [0021](0021-particle-detection-modes.md) | Selectable particle-detection methods (match Deep-LASI's `findPart` modes 1–3) + `.tdat` mode/threshold decode; the deferred PR-C3d framing superseded by 0022 | accepted | §7.1, §9 M1, §11.2, App E |
| [0022](0022-m1-acceptance-reframe-and-close.md) | M1 acceptance reframe (2 px recall, donor-only Pearson ≥0.95, acceptor Pearson diagnostic, faithful per-mode separation, donor-anchored) → **closes M1** (`v0.1.0`) | accepted | §7.1, §9 M1, §8 NFR-VALID (a), §11.2 |
| [0023](0023-curation-label-codec-and-labels-log.md) | Curation-label codec `{UNCURATED 0, ACCEPT +1, REJECT −1}` shared by `curation_label`+`label_value`; append-only `/labels` provenance log with reversible sticky reject + toggleable exclusion; category→`/labels` deferred to M4 | accepted | §5.1, §7.5, §7.3 |
| [0024](0024-idealization-store-layout-staleness-and-nstates.md) | `/idealization/{model}` additive store layout + per-molecule input-provenance hash (staleness) + auto state-count by max-ELBO [Bronson2009]; store-integrated one-click vbFRET (M2 S6 headless core; GUI I-key/overlay is PR-B) | accepted | §5, §7.4, §4.2/§4.3, §11.2 |

_Later decisions (the rest of the PRD §12.7 backfill set) are homed incrementally
by the PRs that implement them._
