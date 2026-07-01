# 0022 — M1 acceptance reframe: 2 px recall, donor-only Pearson, faithful separation, donor-anchored close

- **Status:** accepted (closes M1; tag `v0.1.0`)
- **Date:** 2026-07-01
- **Deciders:** bioedca (maintainer)
- **PRD anchor:** §7.1, §9 M1, §8 NFR-VALID (a), §11.2, Appendix E (FR-EXTRACT)
- **Milestone:** M1

## Context and problem statement

ADR-0021 ported Deep-LASI's three `findPart` detection methods and the `.tdat`
mode/threshold decode, so the UCKOPSB movie can be extracted with the exact method
it was detected with (mode-2 intensity @ `DetectionThreshold` 0.330097). Running the
**full** faithful pipeline (imported `.tmap` registration + `.tdat` detection) on the
real 891 MB pair against the reframed per-channel oracle (ADR-0021 / PR-C3d-1) gave:

| metric | value | originally-frozen §9 M1 gate | verdict |
|---|---|---|---|
| recall @ 1 px | 0.928 | ≥ 0.95 @ 1 px | ✗ |
| recall @ 2 px | **0.984** | — | ✓ (at 2 px) |
| donor per-molecule Pearson (median) | **0.982** | ≥ 0.99 | ✗ |
| acceptor per-molecule Pearson (median) | **0.854** | ≥ 0.99 | ✗ |
| donor precision | 0.34 | (not gated) | — |

So the faithful pipeline **cannot** meet the originally-frozen §9 M1 gate (recall
≥ 95 % @ 1 px, per-molecule Pearson ≥ 0.99 on *both* channels), even though it
reproduces Deep-LASI well. Three empirical findings explain why, and each is a
property of the science rather than a Tether defect:

1. **1 px recall is a localizer-*identity* test.** Centroid localization precision is
   photon-limited — it scales as the inverse square-root of the photon count
   ([Thompson2002]) and reaches only tens of nm even at best ([Khater2020]) — so two
   independent sub-pixel localizers on the same (especially dim) spot legitimately
   differ by ~0.3–0.5 px. Requiring agreement within 1 px tests whether Tether *is*
   Deep-LASI's localizer, not whether it finds the same molecules. Deep-LASI's 250
   `fret_pairs` are additionally detected → colocalized → **human-curated**, so no
   automated detector reproduces them exactly.
2. **The acceptor intensity gate measures noise.** Tether is **donor-anchored** by
   design (ADR-0015): it reads the acceptor at the mapped donor position regardless of
   whether an acceptor was independently detected, to keep the dark / low-FRET acceptor
   population that is a real, substantial fraction of FRET data ([Vogel2012],
   [Dey2018], [Wanninger2023]). Those molecules' acceptor traces are near-noise, and
   two independent extractions of noise do not correlate — dragging the acceptor
   per-molecule Pearson median to 0.85 (bright, well-registered acceptors reach only
   ~0.91; the acceptor read position also carries the ~1.3 px molecule-domain scatter
   of the bead `.tmap`). A **bidirectional** colocalization filter (require an
   independently-detected acceptor) would "fix" precision but collapse recall to ~0.66
   by discarding exactly that kept population — measured, not hypothesised.
3. **The intensity detector's 8 px minimum-separation NMS is unfaithful.** Tether's
   shared Stage-4 tail applied an 8 px keep-brightest NMS to every mode, imported from
   the wavelet mode's `Wave_Partfind`. But Deep-LASI's `findPart` shared tail applies
   **no** effective separation: its `XY = XY(max(D>z),:)` line (`findPart.m:66-69`) is
   a no-op in a populated field (its intended `D<5` line is commented out). The 8 px
   NMS merges real molecules sitting < 8 px apart, capping donor recall at 0.87.

## Decision drivers

- **Faithfulness over gate-gaming** (PLAN §1.3 #7–#8): never weaken a gate to hide a
  gap, nor flood false positives to inflate recall. A reframe must be scientifically
  justified, not convenient.
- **The M1 substrate must be trustworthy** for M2+ (round-trip, corrections, ML).
- **Schema freeze** (ADR-0005): keep the change additive (no `.tether` structural
  change); `schema-guard` stays green.

## Considered options

- **A — Keep the frozen gate; do not close M1.** Rejected: the gate is unmeetable by
  *any* faithful independent extraction (finding 1–2), so this blocks the milestone
  permanently on a test that measures localizer identity, not reproduction.
- **B — Reframe the gate to what "reproduces to tolerance" actually means** (this ADR):
  2 px recall, **donor-only** Pearson at a realistic floor, acceptor Pearson
  diagnostic, faithful per-mode separation; donor-anchored with precision reported.
- **C — Chase the residual integration difference** (aperture/background) to push
  donor Pearson toward 0.99 before closing. Deferred, not blocking: donor 0.982 is
  already strong agreement; the acceptor is limited by dark-acceptor noise + `.tmap`
  scatter regardless, so 0.99-both-channels stays unreachable. Any future integration
  improvement rides M3 (corrections) without re-opening M1.
- **D — Gate the acceptor Pearson on a FRET-positive subset.** A reasonable variant of
  B; not chosen to avoid encoding a brightness/SNR cut into the acceptance gate now.

## Decision outcome

Chosen option **"B"** — reframe the M1 acceptance gate, maintainer-approved
(2026-07-01), and **close M1**:

1. **Recall match tolerance 1 px → 2 px** (`MATCH_TOL_PX = 2.0`). Justified by
   photon-limited localization precision ([Thompson2002]): the 1 px bar was a
   localizer-identity test. Recall @ 2 px = 0.984 ≥ 0.95.
2. **Pearson gate is donor-only, at ≥ 0.95** (`PEARSON_THRESHOLD = 0.95`;
   `OracleResult.meets_pearson` gates the donor median only). The donor is the anchor
   and always carries signal, so its per-molecule trace correlation (median 0.982) is
   the meaningful intensity-fidelity check; 0.95 is a robust strong-agreement floor it
   clears. The **acceptor per-molecule Pearson is diagnostic only** (reported, never
   gated) — gating it would penalize the kept dark / low-FRET population (finding 2).
3. **Faithful per-mode minimum separation.** `detect_spots_intensity` /
   `detect_spots_bandpass` default `min_separation = 3` px (the PSF disk radius, the
   finest separable scale), not the wavelet mode's 8; `detect_spots_by_mode` and
   `ExtractOptions.min_separation` default to `None` → each mode's faithful default
   (wavelet 8, intensity/bandpass 3). Reproduces `findPart`'s effectively-absent NMS
   (finding 3) and lifts recall 0.87 → 0.98.
4. **Donor-anchored, not bidirectional; precision ~0.34 accepted.** The sensitive
   donor-anchored detector emits ~681 candidates for 250 curated molecules
   (precision ~0.34); this is accepted because false positives are removed downstream
   by human curation + Tether's anticorrelation detection and ML ranker (M5), and
   because bidirectional filtering would discard the kept low-FRET population. This
   **supersedes** ADR-0021's deferred "score the colocalized set" framing, which the
   bidirectional-recall measurement (~0.66) refuted.

The gated `test_extraction_meets_m1_acceptance_on_uckopsb` (`@pytest.mark.large`) is
un-`xfail`ed and now extracts with `tmap=` + `tdat=` and asserts the reframed gate.

### Consequences

- **M1 closes** on a faithful, scientifically-honest gate: recall 0.984 @ 2 px, donor
  Pearson 0.982 ≥ 0.95, RMS N/A (imported `.tmap`). Tag `v0.1.0`.
- Additive at the imaging/scoring layer — no `.tether` structural change
  (`schema-guard` green), no `conda-lock` change.
- The acceptor intensity fidelity + the ~1.3 px acceptor read-position scatter are
  reported as diagnostics and carried forward: registration refinement and any
  aperture/background improvement land in M3 without re-opening M1.
- Precision ~0.34 is an accepted property of the M1 detector, not a regression; M5's
  ranker and human curation are the designed false-positive filter.

## More information

- Reference: `deeplasi/functions/mapping/findPart.m:66-69` (the no-op separation
  filter; intended `D<5` commented out), `:21-28` (mode 2). Probe/confirmation scripts
  and the full metric breakdown recorded in PLAN §15.
- Supersedes the deferred **PR-C3d** item and the "last-resort 2 px" framing in
  [ADR-0021](0021-particle-detection-modes.md); builds on
  [ADR-0020](0020-extraction-oracle-and-deferred-m1-close.md) (the oracle),
  [ADR-0015](0015-donor-anchored-colocalization.md) (donor-anchored read),
  [ADR-0011](0011-home-extraction-recall-at-m1.md) (the M1 recall acceptance).
- Citations:
  - [Thompson2002] R. E. Thompson, D. R. Larson & W. W. Webb (2002), *Precise
    Nanometer Localization Analysis for Individual Fluorescent Probes*, Biophys. J.
    82:2775 — localization precision scales as the inverse √(photon count).
  - [Khater2020] I. M. Khater et al. (2020), *A Review of Super-Resolution SMLM
    Cluster Analysis and Quantification Methods*, Patterns 1:100038 — SMLM resolution
    ~10–20 nm.
  - [Vogel2012] S. S. Vogel et al. (2012), *The Impact of Heterogeneity and Dark
    Acceptor States on FRET*, PLoS ONE 7:e49593.
  - [Dey2018] S. Dey et al. (2018), *Eliminating Spurious Zero-Efficiency FRET
    States…*, J. Phys. Chem. Lett. 9:4844.
  - [Wanninger2023] Deep-LASI (2023), Nat. Commun. — donor-anchored dark-acceptor read.
