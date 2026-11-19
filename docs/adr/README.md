---
description: "The architecture decision records of Tether: the reasoning, the options and the result of each consequential design choice."
---

# Architecture Decision Records (ADRs)

**Who this page is for.** Anyone who wants the reasoning behind a consequential Tether design
choice; reading needs no repository access. A record is added or changed only in the pull
request that implements its decision, which a maintainer with write access to `bioedca/tether`
merges.

This directory records Tether's architecture decisions in
[MADR](https://adr.github.io/madr/) format. Each ADR captures one decision, its
context, the options weighed, and the consequences, so the *rationale* survives
prose harmonization of the PRD (PRD §12.7).

## How ADRs are created

- New file: `NNNN-kebab-title.md`, copied from [`0000-template.md`](0000-template.md),
  `NNNN` = the next zero-padded integer.
- **Incremental rule.** A PR that *implements* a still-unhomed resolved PRD
  decision lands that decision's ADR **in the same PR** (`AGENTS.md` §Agile
  execution).
- **M9 gate.** The M9 docs PR fails unless this index is complete (no placeholder
  gaps) — closing PRD §12.7's "home the resolved decisions" deliverable.

## A note on `PLAN §N` references in historical records

Some records below cite `PLAN §N` or `PLAN M<n> S<n>`. Those refer to a private
development-planning document retired on 2026-08-02. It was never tracked in this
repository and is not recoverable from it. The durable content it carried — the
load-bearing invariants and the fixture and validation-oracle map — lives in
[the PRD](https://github.com/bioedca/tether/blob/main/docs/PRD.md) §8 and §9.
(The PRD is kept in the repository rather than published to this site, so that
link goes to GitHub.)

Those citations are left **exactly as written**. An accepted record states the
reasoning that was actually used at the time, and rewriting it to point somewhere
else would make it a worse historical record in exchange for a working link.

## Index

<!-- gen:adr-index start (scripts/gen_adr_index.py --write) -->

### Foundational, cross-cutting (M0 seed)

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
| [0013](0013-fourier-mellin-similarity-prealign.md) | 4-DOF Fourier-Mellin similarity prealign: log-polar recovery, masked-NCC disambiguation, real-data oracle | accepted; amended by [ADR-0089](0089-the-similarity-prealign-samples-a-square-padded-spectrum.md) on the spectrum sampling, the band-pass default and the regime | App E §7, §11.2 |
| [0014](0014-registration-map-rms-gate-and-over-gate.md) | Registration map: numeric RMS gate, over-gate flag-don't-drop, and a unified native/imported calibration | accepted | §7.1, App E §9–10, §11.2 |
| [0015](0015-donor-anchored-colocalization.md) | Donor-anchored colocalization: keep dark/low-FRET acceptors, apply the map in the coordinate domain | accepted; molecule anchor superseded by [ADR-0127](0127-colocalization-policy-partner-in-both-by-default-or-donor-anchored.md) | §7.1, App E §11–13, §11.2 |
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
| [0050](0050-release-pipeline-and-code-signing.md) | Release pipeline + code-signing (tag-driven, SignPath for Windows, gated Apple) | accepted; code-signing half superseded by [0059](0059-ship-v1-unsigned-with-provenance-as-the-integrity-anchor.md) | §9 M9, §12.7, §4.1 |
| [0051](0051-installed-app-launch-surface.md) | The installed app's launch surface: a real GUI entry point, prefix shims, and a menu shortcut | accepted; amended by [ADR-0094](0094-the-launchers-get-a-folder-of-their-own.md) on the folder that holds the launchers | §7.8, §4.1, §9 M9 |
| [0052](0052-concurrent-agent-swarm-coordination.md) | Concurrent issue-swarm coordination | superseded by 0057 (fully retired) | §12.2–§12.5 |
| [0053](0053-structured-backlog-intake-gates-swarm-admission.md) | Structured backlog intake gates swarm admission | superseded by 0057 (admitting authority) | §12.5 |
| [0055](0055-gui-session-writer-ownership.md) | GUI session writer ownership | accepted; amended by [ADR-0096](0096-the-lock-guard-is-shared-with-directory-writers.md) on the lifecycle guard's permissions | §5.4, §7.3, §7.5, §7.10 |
| [0056](0056-policy-fail-rejected-extraction-checkpoint.md) | Preserve policy-fail rejection across batch resume | accepted | §6, §7.11, §11.2 |
| [0057](0057-github-native-swarm-coordination.md) | GitHub-native swarm coordination | accepted in part; supersedes [ADR-0052](0052-concurrent-agent-swarm-coordination.md) in full and [ADR-0053](0053-structured-backlog-intake-gates-swarm-admission.md) only in its admitting mechanism — ADR-0053’s intake gates remain in force as the claim precondition; its review gate, round cap, slot launcher and advisory scope guard superseded by [ADR-0064](0064-the-agent-layer-coordinates-writers-not-reviews.md) | §12.2–§12.5 |
| [0059](0059-ship-v1-unsigned-with-provenance-as-the-integrity-anchor.md) | Ship v1.0.0 unsigned, with SHA-256 manifests + build provenance as the integrity anchor | accepted; supersedes the code-signing half of [0050](0050-release-pipeline-and-code-signing.md) | §9 M9, §12.7, §4.1, NFR-PKG |
| [0060](0060-in-app-update-mechanism-and-integrity-model.md) | In-app update: download, verify against build provenance, hand off to the OS installer | accepted; decision 2's mechanism amended to bundled `gh attestation verify --bundle`; store location delegated to [#330](https://github.com/bioedca/tether/issues/330) | §4.1, §4.3, §8 NFR-PKG, §7.12 FR-UPDATE |
| [0061](0061-transport-failures-and-the-strict-x509-opt-out.md) | A transport failure is not a scope verdict, and strict X.509 conformance is opt-out | accepted; reverses a non-goal of [#315](https://github.com/bioedca/tether/issues/315) | §12.2–§12.5 |
| [0062](0062-draft-first-review-lane-with-metered-providers.md) | Draft-first review lane: spend the free provider before the metered ones | superseded by [ADR-0064](0064-the-agent-layer-coordinates-writers-not-reviews.md) | §12 |
| [0063](0063-review-evidence-is-read-not-inferred.md) | Review evidence is read from the payload, not inferred from its shape | superseded by [ADR-0064](0064-the-agent-layer-coordinates-writers-not-reviews.md); its measurements stand as evidence | §12 |
| [0064](0064-the-agent-layer-coordinates-writers-not-reviews.md) | The agent layer coordinates writers, not reviews | accepted; supersedes [ADR-0062](0062-draft-first-review-lane-with-metered-providers.md) and [ADR-0063](0063-review-evidence-is-read-not-inferred.md), and the review gate, round cap, slot launcher and advisory scope guard of [ADR-0057](0057-github-native-swarm-coordination.md); its review-gate terminus is in turn superseded by [ADR-0065](0065-a-spent-metered-cap-closes-on-the-unmetered-provider.md), the rest governing unchanged | §12 (development & version-control protocol) |
| [0065](0065-a-spent-metered-cap-closes-on-the-unmetered-provider.md) | A spent metered cap closes on the unmetered provider, not on a maintainer | accepted; supersedes the review-gate terminus of [ADR-0064](0064-the-agent-layer-coordinates-writers-not-reviews.md), whose other decisions govern unchanged; its two-review trigger and third shutting condition are in turn superseded by [ADR-0069](0069-one-metered-review-then-the-codex-close.md) | §12 (development & version-control protocol) |
| [0066](0066-read-issue-markdown-with-a-real-parser.md) | Read issue Markdown with a real parser, as a dev-only dependency | accepted | §4.1 (pin-and-hold), §12.5–§12.6 |
| [0067](0067-github-releases-are-the-only-change-record.md) | GitHub Releases are the only change record | accepted; supersedes the committed-`CHANGELOG.md` wording of PRD §12.7 and its single "Internal" changelog bucket | §12.7 (Releases & versioning), §12.2 / §12.6 (Conventional Commits) |
| [0068](0068-a-project-holds-one-experiment.md) | A project holds one experiment, and a condition can span projects | accepted | §5.1 (project, experiment, condition), §3.1 UC3, §5.4, §7.5, §7.6, §7.7, §7.11 |
| [0069](0069-one-metered-review-then-the-codex-close.md) | One metered review, then the Codex close; Greptile stands in for a long CodeRabbit refusal | accepted; supersedes the two-review trigger and the third shutting condition of [ADR-0065](0065-a-spent-metered-cap-closes-on-the-unmetered-provider.md), whose other decisions govern unchanged | §12 (development & version-control protocol) |
| [0070](0070-advisory-check-for-swapped-donor-and-acceptor-halves.md) | An advisory check for swapped donor and acceptor halves | accepted | §7.1 (FR-EXTRACT), §7.11 (FR-BATCH), §11.2 (donor-side check rows) |
| [0071](0071-private-data-checks-run-from-a-workstation-task.md) | Private-data checks run from a scheduled task on the maintainer's workstation | accepted | §9.5 (the validation suite), §12.6 (continuous integration) |
| [0072](0072-coverage-floors-that-only-go-up.md) | Coverage floors that only go up, measured on the Linux leg | accepted | §12.6 (CI merge gate); NFR-REPRO |
| [0073](0073-one-manifest-for-reference-data-sets.md) | One manifest and four storage tiers for reference data sets | accepted | §8 (NFR-FIXTURES, NFR-VALID), §12.1 (SPDX / REUSE), §12.6 (CI) |
| [0074](0074-matlab-reference-outputs-come-from-pinned-runs.md) | MATLAB reference outputs come from pinned runs with a provenance record | accepted | §1 (reference tools), §8 NFR-VALID, §8 NFR-FIXTURES |
| [0075](0075-seeded-ebfret-parity-gate.md) | The ebFRET parity gate runs on two fixed seeds | accepted; amends [ADR-0007](0007-parity-is-statistical.md) on pinning the seed | §7.4 (parity definition), §11.2 (the ebFRET tolerance row), §12.6 (`sidecar/parity`), §9 M6 |
| [0076](0076-the-gui-activates-its-own-dll-path.md) | The GUI puts its env's DLL folders first on PATH, so the Windows shortcut needs no console | accepted; changes the Windows shortcut part of [ADR-0051](0051-installed-app-launch-surface.md), whose other decisions govern unchanged | §12.7 (installers), §7.8 |
| [0077](0077-a-condition-retrain-reads-its-source-projects.md) | A condition's retrain reads the labels from its source projects | accepted | §7.5 (FR-ML), §5.1 (`/labels`, `/models`, the project definition), §5.4, UC3 |
| [0078](0078-benchmarks-compare-a-pull-request-with-its-merge-base.md) | Benchmarks compare a pull request with its merge base on one runner | accepted | §8 (NFR-PERF), §12.6 (continuous integration) |
| [0079](0079-rf2-arfa-authors-data-oracle.md) | The tMAVEN authors' RF2-ArfA data set is a reference oracle | accepted | §7.4 (parity definition), §8 NFR-VALID and NFR-FIXTURES, §11.2 (the frozen tolerance), §12.6 (`sidecar/parity`) |
| [0080](0080-self-drift-golden-numbers.md) | A self-drift suite freezes end-to-end numbers in golden files | accepted | §9.5 (the validation suite), §12.6 (continuous integration) |
| [0081](0081-kinsoft-levels-two-and-three-are-reported.md) | kinSoft levels 2 and 3 are reported against the simulator settings, never band-checked | accepted; amends [ADR-0048](0048-kinsoft-kinetics-oracle.md) on the deferred levels | §8 NFR-VALID(c), §9 M8, §11.2 (the kinSoftChallenge parity band row), N2 |
| [0082](0082-measured-kinsoft-sets-are-placed-not-scored.md) | The measured kinSoft sets are placed among the published results, not scored | accepted | §8 NFR-VALID(c), §9 M8, §11.2 |
| [0083](0083-unchanged-copies-of-cc-by-nc-nd-reference-data.md) | Unchanged copies of CC BY-NC-ND 4.0 reference data in a GPL-3.0 repository | accepted | §8 (NFR-FIXTURES, NFR-VALID), §12.1 (SPDX / REUSE) |
| [0084](0084-smfret-analysis2-is-a-reference-at-a-pinned-commit.md) | smfret-analysis2 at a pinned commit is the reference for the histogram and the cross-correlation | accepted | §1 (the two model-free lenses), §7.7 FR-ANALYZE, §8 NFR-VALID, N4 |
| [0085](0085-a-trailing-dwell-keeps-the-last-window-frame.md) | A trailing dwell keeps the last frame of its analysis window | accepted (the maintainer accepted the re-frozen tMAVEN-app limit on 2026-11-12) | §7.7 FR-ANALYZE, Appendix C A2 (post-synchronized heatmap) |
| [0086](0086-ha-laboratory-snapshot-histogram-is-a-reference.md) | The Ha-laboratory SAM/SAH snapshot histogram is a reference, compared by its peaks | accepted | §1 (the apparent-E histogram), §2.2 (N4), §8 NFR-VALID, §8 NFR-FIXTURES |
| [0087](0087-mash-fret-is-a-reference-for-corrected-fret.md) | MASH-FRET's published project files are a reference for leakage and corrected FRET | accepted | §7.2 (corrections), §7.4 (FRET efficiency), §2.2 (what the tests can use), §8 NFR-VALID |
| [0088](0088-extraction-test-on-mash-fret-simulated-videos.md) | An extraction test on videos with known truth from the MASH-FRET simulator | accepted | §9.5 (the validation suite), §8 NFR-VALID, §8 NFR-FIXTURES |
| [0089](0089-the-similarity-prealign-samples-a-square-padded-spectrum.md) | The similarity prealign samples a square, padded, emphasised spectrum | accepted; amends [ADR-0013](0013-fourier-mellin-similarity-prealign.md) on the spectrum sampling, the band-pass default and the stated regime | App E §7, §11.2 |
| [0090](0090-measured-vbfret-paths-are-a-dwell-reference.md) | Measured traces with a stored vbFRET path are a dwell and transition reference | accepted | §2.2 (rule 2), §8 (NFR-VALID (b), NFR-FIXTURES), §7.7 (dwell times and the TDP) |
| [0091](0091-macos-launcher-bundle.md) | The macOS installer adds a Tether.app launcher bundle | accepted | §4.1 NFR-PKG, goal G6, §9.6 (M18 clean-machine acceptance) |
| [0092](0092-dirichlet-rate-bounds-from-the-count-matrix.md) | Rate constants with Dirichlet bounds come from the count matrix, as in Campos-Chavez et al. 2024 | accepted | §7.7 FR-ANALYZE, §8 NFR-VALID, NFR-REPRO |
| [0093](0093-numpy-docstrings-and-mkdocstrings-cross-references.md) | NumPy docstrings, with cross-references in the form of mkdocstrings | accepted | §12.4 (definition of done), §12.6 (the `lint` gate), §12.9 (pre-commit) |
| [0094](0094-the-launchers-get-a-folder-of-their-own.md) | The two launchers get a folder of their own: `<prefix>/launchers` | accepted; amends [ADR-0051](0051-installed-app-launch-surface.md) on the folder that holds the launchers | §4.1 (installers), §9 M9 (packaging & docs) |
| [0095](0095-application-settings-live-outside-the-install-folder.md) | Application settings live outside the install folder | accepted; amended by [ADR-0123](0123-the-settings-store-also-keeps-the-trace-colors.md) on what the settings file stores | §7.12 FR-UPDATE (consent, and the requirement that the answer survives an update); §4.1 (application shell) |
| [0096](0096-the-lock-guard-is-shared-with-directory-writers.md) | The lock lifecycle guard is shared with everyone who can write the project directory | accepted; amends [ADR-0055](0055-gui-session-writer-ownership.md) on the guard's permissions | §5.4 (single writer, steal-lock), §7.10 FR-CONCURRENCY |
| [0097](0097-the-2024-t-box-source-data-is-an-advisory-analysis-reference.md) | The Source Data of the 2024 T-box paper is an advisory reference for the analysis functions | accepted | §8 (NFR-VALID), §7.4 (apparent E), §7.7 (histograms, dwell times, the TDP) |
| [0098](0098-the-pysmacks-labels-are-the-deep-classifier-reference.md) | The PySMACKS tutorial labels are the reference of the deep classifier | accepted | §7.5 (quality ranking), §9 M8 (the deep classifier), §9.5 (the validation suite) |
| [0099](0099-load-bearing-pull-requests-take-one-greptile-review.md) | Load-bearing pull requests take one Greptile review, not CodeRabbit | accepted; amends [ADR-0069](0069-one-metered-review-then-the-codex-close.md), whose other decisions govern unchanged | §12 (development & version-control protocol) |
| [0100](0100-service-commands-are-the-one-way-to-run-an-action.md) | Service commands are the one way to run an action | accepted | §7.11 (FR-BATCH, the parity rule), §7.13 (FR-WORKFLOW-7), §4.2 |
| [0101](0101-mash-fret-analyses-are-a-histogram-and-state-reference.md) | MASH-FRET's stored analyses are an exact histogram reference and a measured state reference | accepted | §7.7 FR-ANALYZE, §7.4 (idealization), §8 NFR-VALID, NFR-FIXTURES, §11.2 |
| [0102](0102-a-batch-run-stops-at-an-approval-step-or-applies-a-recipe-policy.md) | A batch run stops at a step that needs a person, unless the recipe names a policy | accepted | §7.11 (FR-BATCH, the parity rule), §7.1 (low-confidence registration), §7.2 (correction states) |
| [0103](0103-the-project-dependency-graph-and-the-stale-rules.md) | The dependency graph of a project and the rules that make a result stale | accepted | §7.13 (FR-WORKFLOW-6), §5 (data model), §9.1 (load-bearing invariants) |
| [0104](0104-a-reject-reason-log-under-labels.md) | The reason of a reject is an append-only log under `/labels` | accepted | §5.1 (`/labels`), §7.2 ("a reject has a reason"), §7.5 (reject semantics) |
| [0106](0106-campos-chavez-tmaven-fits-are-an-advisory-reference.md) | The tMAVEN fits of Campos-Chavez et al. 2024 are an advisory reference for the sidecar fits | accepted | §7.4 (idealization), §8 NFR-VALID, NFR-FIXTURES, §11.2 |
| [0107](0107-the-mash-fret-extraction-test-counts-duplicate-molecules.md) | The MASH-FRET extraction test counts duplicate molecules | accepted; amends [ADR-0088](0088-extraction-test-on-mash-fret-simulated-videos.md) on the frozen limits | §9.5 (the validation suite), §8 NFR-VALID |
| [0108](0108-mash-fret-s-stored-bootstrap-is-the-reference-for-tether-s-bootstrap.md) | MASH-FRET's stored bootstrap is the reference for Tether's histogram bootstrap | accepted | §7.7 FR-ANALYZE (FRET histogram with bootstrap CI), §8 NFR-VALID, §11.2 |
| [0109](0109-one-selection-argument-and-one-return-shape-on-the-population-functions.md) | One selection argument and one return shape on the population functions | accepted | §7.7 FR-ANALYZE (population views), §5.1 (stale idealizations), §7.5 (the curation filter) |
| [0110](0110-idealization-parity-gaps-per-trace-transition-matrix-refreeze-and-bound-lock.md) | Idealization parity: per-trace vbFRET, the transition-matrix term, the re-freeze on the pinned build, and a lock on the frozen bounds | accepted | §7.4, §9.2 (the idealization-parity gate), §9.6, §11.2, §8 NFR-VALID (b) |
| [0112](0112-every-publishing-writer-holds-its-destination-lock.md) | Every publishing writer holds its destination lock, and one helper names a file | accepted; amends [ADR-0055](0055-gui-session-writer-ownership.md) on the GUI write freeze and the file identity | §5.4 (single writer), §7.10 FR-CONCURRENCY |
| [0113](0113-one-movie-reader-interface-an-adapter-registry-and-a-rule-for-reader-packages.md) | One movie reader interface, an adapter registry, and a rule for reader packages | accepted | §7.1 FR-EXTRACT, Appendix A (input formats) |
| [0114](0114-leave-one-video-out-is-the-second-held-out-ranker-protocol.md) | Leave-one-video-out is the second held-out ranker protocol | accepted | §8 NFR-VALID (d), §7.5 (FR-ML precision@k), §9 M5, §9.6, §11.2 (ranker |
| [0115](0115-deep-lasi-acquisitions-in-oracle-a-and-how-a-failure-is-handled.md) | Deep-LASI acquisitions in oracle (a), and how a failure on one is handled | accepted | §9.5 (the validation suite, oracle (a)), §8 NFR-VALID (a), §8 NFR-FIXTURES |
| [0116](0116-the-shell-hosts-its-sections-in-one-stack.md) | The shell hosts its sections in one stack, and each dock belongs to one section | accepted | §4.2 (`tether.gui`), §7.3 (the curation shell), §7.10 FR-CONCURRENCY, FR-WORKFLOW |
| [0117](0117-a-movie-is-appended-to-a-project-once-under-its-writer-lock.md) | A movie is appended to a project once, under its writer lock, and keeps its own settings | accepted | §5.1 (data model), §5.4 (single writer), §7.11 FR-EXTRACT / FR-BATCH, §7.8 (the |
| [0118](0118-the-tether-file-type-is-registered-by-the-installer.md) | The installer registers the .tether file type on each system | accepted | §4.1 NFR-PKG, goal G6 |
| [0119](0119-the-condition-model-has-its-own-lock-and-a-project-reference.md) | The condition model has its own single-writer lock, and a project records it under `/models` | accepted; completes the store integration that [ADR-0035](0035-portable-model-persistence.md) deferred | §5.1 (`/models`), §5.4 (single writer, steal-lock), §7.5 (FR-ML), §7.10 FR-CONCURRENCY |
| [0120](0120-a-sum-of-gaussians-fit-of-a-fret-histogram-and-its-two-area-definitions.md) | A sum-of-Gaussians fit of a FRET histogram, its two area definitions and its errors | proposed | §7.7 FR-ANALYZE (FRET histograms), §8 NFR-VALID |
| [0121](0121-each-lock-takeover-leaves-a-record-in-the-project.md) | Each lock takeover leaves a record in the project | accepted | §7.10 FR-CONCURRENCY, §5.4 (single writer), §5 (schema) |
| [0122](0122-the-session-is-remembered-in-a-preferences-file-beside-the-settings-file.md) | The session is remembered in a preferences file beside the settings file | accepted | §7.3 (the curation shell); §5.1 `/labels` (the `labeler`); goal G6 |
| [0123](0123-the-settings-store-also-keeps-the-trace-colors.md) | The settings store also keeps the trace colors | accepted; amends [ADR-0095](0095-application-settings-live-outside-the-install-folder.md) on what the settings file stores | §7.3 (familiar conventions); §4.1 (application shell) |
| [0124](0124-the-plot-builder-s-composition-unit-renderer-and-staleness-and-frame-time-policies.md) | The plot builder's composition unit, its one renderer, and its staleness and frame-time policies | accepted | §7.7 FR-PLOT (composed plots), §7.9 FR-EXPORT, §4.1 (no PyQt in the base stack) |
| [0126](0126-jobs-and-stale-marks-are-stored-in-the-project.md) | Jobs, their checkpoints and the stale marks are stored in the project | accepted | §7.13 (FR-WORKFLOW), §7.10 (FR-CONCURRENCY), §5 (data model) |
| [0127](0127-colocalization-policy-partner-in-both-by-default-or-donor-anchored.md) | The colocalization policy: a partner in both channels by default, or donor-anchored | accepted | §7.1, App E §11, §11.2, §5 |
| [0128](0128-the-map-file-carries-a-format-version-and-a-reader-refuses-a-newer-one.md) | The map file carries a format version, and a reader refuses a newer one | accepted | §7.1 FR-EXTRACT (registration), Appendix E Stage 10 (map persistence) |
| [0129](0129-an-extraction-profile-is-a-file-in-the-request-text-form.md) | An extraction profile is a file in the request text form, and `--tdat` sets two fields | accepted | §7.11 (FR-EXTRACT, FR-BATCH), §11.2, §8 NFR-REPRO |
| [0130](0130-the-gamma-levels-are-averaged-over-20-frames-each-side.md) | The γ levels are averaged over 20 frames each side of the acceptor-bleach step | accepted; amends [ADR-0028](0028-gamma-acceptor-bleach-step-estimator.md) on the frames each γ level is averaged over, whose other decisions govern unchanged | §7.2 (γ), §11.2 (the correction tolerance window row), Appendix B.2 step 4 |
| [0131](0131-the-correction-states-are-a-transition-log-in-the-project.md) | The correction states of G03 are a transition log stored in the project | accepted | §7.2 (FR-CORRECT, the correction states), Appendix B, §7.13 (FR-WORKFLOW), §5 (data model) |
| [0132](0132-several-series-on-one-figure-one-color-cycle-small-multiples-and-one-grid.md) | Several series on one figure: one color cycle, small multiples where an overlay has no meaning, and one grid | accepted | §7.7 FR-PLOT (a plot is a layer list), §7.9 FR-EXPORT (per-series provenance) |
| [0133](0133-the-leakage-band-runs-on-the-donor-only-sample-and-the-gamma-leg-stays-withheld.md) | The leakage-α band runs on the donor-only sample; the γ-vs-Deep-LASI leg stays withheld | accepted | §7.2, §9.1 item 8, §9.3 (Corrections), §9.5 oracle (e), §9.6, §11.2 (leakage-α validation band, γ-agreement tolerance) |

<!-- gen:adr-index end -->

*Later decisions (the rest of the PRD §12.7 backfill set) are homed incrementally
by the PRs that implement them.*
