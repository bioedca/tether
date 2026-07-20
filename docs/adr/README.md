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
| [0007](0007-parity-is-statistical.md) | Idealization parity is statistical, asserted against a frozen tolerance | accepted | §7.4, §11.2 |
| [0008](0008-correction-factor-remap.md) | Deep-LASI → Tether correction-factor naming remap (β→α, α→δ, γ→γ) | accepted | Appendix B.1 |

### Homed incrementally by implementing PRs

| ADR | Title | Status | PRD anchor |
|----:|-------|--------|------------|
| [0009](0009-parity-metrics-and-freeze.md) | Idealization-parity metric definitions and the M0.5 freeze | accepted | §7.4, §11.2 |
| [0010](0010-defer-cross-os-gui-handoff.md) | Defer the 2-OS standalone-tMAVEN GUI hand-off check to M9 | accepted | §9 M0.5(a), §9 M9 |
| [0011](0011-home-extraction-recall-at-m1.md) | Home the M0.5 ≥95% extraction-recall acceptance at M1 | accepted | §9 M0.5(b), §9 M1 |
| [0012](0012-registration-pairing-mutual-nn.md) | Registration pairing: mutual NN, fit on original coords; translation prealign first | accepted | App E §7–8, §11.2 |
| [0013](0013-fourier-mellin-similarity-prealign.md) | 4-DOF Fourier-Mellin similarity prealign: log-polar recovery, masked-NCC disambiguation, real-data oracle | accepted | App E §7, §11.2 |
| [0014](0014-registration-map-rms-gate-and-over-gate.md) | Registration map: numeric RMS gate, over-gate flag-don't-drop, and a unified native/imported calibration | accepted | §7.1, App E §9–10, §11.2 |
| [0015](0015-donor-anchored-colocalization.md) | Donor-anchored colocalization: keep dark/low-FRET acceptors, apply the map in the coordinate domain | accepted | §7.1, App E §11–13, §11.2 |
| [0016](0016-extraction-trace-store-layout.md) | Extraction trace-store layout: zero-pad-to-max-T traces, cached patches, and the molecule_key content hash | accepted | §5.1, §7.10, App E §14–15, §11.2 |
| [0017](0017-deeplasi-validation-reader.md) | Minimal Deep-LASI `.mat` / `.txt` validation reader: M1-scoped fields, coordinate convention, and lazy scipy | accepted | §9 M1, §8 NFR-VALID (a), App A |
| [0018](0018-extract-cli-native-pipeline.md) | `tether extract` CLI: a native auto-registration pipeline; imported `.tmap` + the Deep-LASI oracle deferred to the S9 follow-up | accepted | §7.11, §9 M1, App E, §11.2 |
| [0019](0019-extract-cli-imported-tmap.md) | `tether extract --tmap`: the imported registration path; trust the bead map (residual unknown), refuse a non-identity rotation/flip, defer the apply + the oracle | accepted | §7.1, §7.11, §9 M1, App E §6–10 |
| [0020](0020-extraction-oracle-and-deferred-m1-close.md) | The extraction-vs-Deep-LASI acceptance oracle; M1 close deferred (detection-faithfulness gap surfaced) | accepted | §9 M1, §8 NFR-VALID (a), §7.11, §2.2, App A |
| [0021](0021-particle-detection-modes.md) | Selectable particle-detection methods (match Deep-LASI's `findPart` modes) | accepted | §7.1, §9 M1, §11.2, App E |
| [0022](0022-m1-acceptance-reframe-and-close.md) | M1 acceptance reframe: 2 px recall, donor-only Pearson, faithful separation, donor-anchored close | accepted | §7.1, §9 M1, §8 NFR-VALID (a), §11.2 |
| [0023](0023-curation-label-codec-and-labels-log.md) | Curation-label codec + append-only `/labels` provenance log; category logging deferred to M4 | accepted | §5.1, §7.5, §7.3 |
| [0024](0024-idealization-store-layout-staleness-and-nstates.md) | `/idealization/{model}` store layout, per-molecule input-provenance hash (staleness), and auto state-count by max-ELBO | accepted | §5, §7.4, §4.2/§4.3, §11.2 |
| [0025](0025-tmaven-handoff-and-return-leg-reconcile.md) | Bidirectional tMAVEN hand-off + non-destructive return-leg re-import with a per-trace reconcile | accepted | §7.4, §5.3, App D.1 |
| [0026](0026-photobleach-detection-and-window-default.md) | Native single-step photobleach detection, summed-intensity analysis-window default, and the `pacc`/`pdon` oracle reframe | accepted | §7.2, §11.2, App B step 6, App E Stage 16, §9 M3 |
| [0027](0027-leakage-alpha-tail-estimator.md) | Leakage α from the post-acceptor-bleach tail; donor-only-sample cross-check deferred | accepted | §7.2, §7.4, §11.2, App B.2 step 2, App E Stages 17–18, §9 M3 |
| [0028](0028-gamma-acceptor-bleach-step-estimator.md) | γ from the acceptor-bleach step (bare-`I_D` convention); Deep-LASI-median oracle deferred | accepted | §7.2, §7.4, §11.2, App B.2 step 4, App E Stages 17–18, §9 M3 |
| [0029](0029-idealization-correction-provenance-hash-and-per-factor-staleness.md) | Composite corrected-FRET provenance hash + per-factor idealization staleness scope | accepted | §5.1, §7.2, §7.4, §9 M3 |
| [0030](0030-headless-batch-runner-isolation-and-checkpoint.md) | Headless batch runner: per-movie isolation + provenance-derived per-stage checkpoint | accepted | §6, §7.11, §7.2, §11.2, §9 M3 |
| [0031](0031-batch-sidecar-supervision.md) | Batch sidecar supervision: liveness-deferred startup + transient auto-restart | accepted | §7.11, §11.2, §4.3, §9 M3 |
| [0032](0032-nfr-perf-budget-verification.md) | NFR-PERF budget verification: a light M3 gate over slice-scaled envelopes | accepted | §8 NFR-PERF, §11.2, §12.10, §9 M3 |
| [0033](0033-condition-identity-and-rekey.md) | Condition identity (content-hash, keep-separate) + transactional re-key with human-confirmed merge | accepted | §5.1, §7.6, §9 M4 |
| [0034](0034-gradient-boosting-quality-ranker.md) | Gradient-boosting quality ranker: HistGradientBoosting on `P(good)`, precision@k objective | accepted | §7.5, §11.2, §9 M5 |
| [0035](0035-portable-model-persistence.md) | Portable per-condition model artifact: zip + manifest + pickle, format-versioned, warm-start = refit on accumulated labels | accepted | §7.5, §5.1, §9 M5 |
| [0036](0036-label-weighting-cold-start-decay.md) | Cold-start label weighting: the decay law, what `n_human` counts, and where it lives | accepted | §7.5, §5.1, §11.2, §9 M5 |
| [0037](0037-cross-condition-drift-advisory-flag.md) | Cross-condition drift advisory: the drift statistic, how per-feature tests combine, and its semantics | accepted | §7.5, §11.2, §9 M5 |
| [0038](0038-provisional-prior-training-fold.md) | Fold weighted provisional `/labels` priors into the ranker's training set (human-supersedes, eval-on-truth) | accepted | §7.5, §11.2, §9 M5 |
| [0039](0039-multi-curator-split-file-merge-back.md) | Multi-curator split-file label merge-back (append-only owner-pull, adopt-or-surface) | accepted | §5.1, §7.5, §7.10, §7.4, §9 M5 |
| [0040](0040-active-learning-non-reordering-badge.md) | Active-learning "recommended next" non-reordering badge (uncertainty sampling) | accepted | §7.5, §4.2, §9 M5 |
| [0041](0041-population-model-and-ebfret.md) | Persist the full Appendix-D.2 population model; add ebFRET as a second global idealizer | accepted | §4.2, §10, App D.2, §9 M6 |
| [0042](0042-a1-model-gaussian-overlay.md) | A1 histogram model overlay: the idealized model's per-state Gaussians, not a fresh GMM fit | accepted | §7.7, §10, App C A1, §9 M6 |
| [0043](0043-per-method-parity-tolerance.md) | Per-method idealization-parity tolerance (ebFRET frozen separately) | accepted | §7.4, §11.2, §12.6, §9 M6 |
| [0044](0044-matplotlib-base-plot-export-backend.md) | matplotlib-base as the static vector plot-export backend | accepted | §7.9, §4.1, §9 M6 |
| [0045](0045-deeplasi-round-trip-reconstruction.md) | Reconstruct a round-trip-ready `.tether` from Deep-LASI legacy data | accepted | §7.8, §5.3, §5.1, §9 M7 |
| [0046](0046-analysis-only-smd-import.md) | Analysis-only import of a coordinate-less SMD / `.txt` source | accepted | §7.8, §5.3, §9 M7 |
| [0047](0047-deep-model-optional-stack-and-dataset.md) | Deep-model optional stack (Option A) + torch-free training-dataset substrate | accepted | §4.1, §7.5, §9 M8 |
| [0048](0048-kinsoft-kinetics-oracle.md) | kinSoftChallenge kinetics oracle: base-env HMM, 2-state scope, within-spread band | accepted | §8, §9 M8, §11.2 |
| [0049](0049-m9-packaging-constructor-architecture.md) | M9 packaging: constructor installer architecture (offline base env + isolated sidecar) | accepted | §4.1, §9 M9, §12.7 |
| [0050](0050-release-pipeline-and-code-signing.md) | Release pipeline + code-signing (tag-driven, SignPath for Windows, gated Apple) | accepted | §9 M9, §12.7, §4.1 |
| [0051](0051-installed-app-launch-surface.md) | The installed app's launch surface: a real GUI entry point, prefix shims, and a menu shortcut | accepted | §7.8, §4.1, §9 M9 |

_Later decisions (the rest of the PRD §12.7 backfill set) are homed incrementally
by the PRs that implement them._
