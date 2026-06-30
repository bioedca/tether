# 0021 — Selectable particle-detection methods (match Deep-LASI's `findPart` modes)

- **Status:** accepted (partial: modes 2 & 3 + the CLI/pipeline selector landed; `.tdat` config-decode + re-measurement follow)
- **Date:** 2026-06-30
- **Deciders:** bioedca
- **PRD anchor:** §7.1, §9 M1, §11.2, Appendix E Stage 3 (FR-EXTRACT)
- **Milestone:** M1

## Context and problem statement

The M1 extraction-vs-Deep-LASI oracle (ADR-0020) revealed native extraction
recalls only ~20 % @1px of Deep-LASI's 250 colocalized `fret_pairs` on the real
UCKOPSB pair, traced to the **detector**, not the oracle or coordinate frames.
The only detector Tether had ported is Deep-LASI mode 1 (à trous wavelet,
`Wave_Partfind.m`), whose detection mask is the `AND` of wavelet scales 1 & 4
(`P=cumprod(w,3)` makes scale-4 significance mandatory). The UCKOPSB molecules
are **tight PSFs with almost no scale-4 energy** (per-scale truth coverage
scale4 = 76/250), so faithful mode 1 structurally caps at ~52/250 @1px at *any*
threshold. But Deep-LASI exposes **three** detection methods behind its GUI
`Rbg_Detection` radio group (`findPart.m` `method`: 1 wavelet, 2
intensity-threshold, 3 bandpass), and its 250 pairs are
detected → bidirectionally colocalized → **human-curated** (`select`/`tags`).
So: which faithful detector reproduces Deep-LASI's spots, and how do we match the
method/threshold a given movie was *actually* detected with — rather than
assuming mode 1?

## Decision drivers

- **Faithfulness over gate-gaming** (PLAN §1.3 #7–#8): port Deep-LASI's real
  methods; never ship a high-false-positive detector that games recall, nor
  weaken a frozen acceptance gate to hide the gap.
- **The frozen §9 M1 gate** (recall ≥ 95 % @1px, Pearson r ≥ 0.99, RMS ≤ 0.5 px)
  is the substrate M2+ trusts; closing M1 must be earned by a real detector.
- **Schema freeze** (ADR-0005): keep this PR additive at the imaging layer (no
  `.tether`/`/settings` change) so `schema-guard` stays trivially green.
- **Session/PR budget** (PLAN §0.1): one reviewable concern per PR.

## Considered options

- **A — Relax the M1 recall match-tolerance 1px → 2px** (maintainer-approved
  2026-06-30) + land a principled detector. Justified by localization precision
  (two valid sub-pixel localizers legitimately differ ~0.3–0.5 px), but demoted
  by the maintainer to a **last-resort fallback** after the framing corrections
  below.
- **B — Implement & match Deep-LASI's three `findPart` methods** (selectable),
  decode the mode/threshold the data was detected with from the `.tdat` MCOS
  blob, validate **per-channel** vs DL `donor_xy`/`acceptor_xy`, then re-measure
  @1px on the faithfully-matched pipeline. The 2px relaxation is used only if
  faithful matching still can't hit 1px.
- **C — Port only mode 3 (bandpass)** as the single replacement detector.
- **D — Re-derive the oracle ground truth from DL's raw per-channel detections**
  (pre-coloc, pre-curation) instead of the curated 250 pairs.

## Decision outcome

Chosen option: **"B"**, implemented incrementally. This PR (M1 S9 **PR-C3a**)
lands the **selector** (`ParticleDetectionMode`) and a **faithful mode-2
intensity-threshold detector** (`detect_spots_intensity`): threshold at
`t·max` → Crocker-Grier band-pass (`bpass.m`; [Crocker1996]) → 3 %-of-max
binarize → 3×3 erode (`bwmorph 'erode'`) → 8-connected centroids
(`regionprops 'Centroid'`), then the **shared Stage-4 tail**
(`_finalize_candidates`: snap → border → min-separation NMS → `[x, y]`),
mirroring `findPart.m`'s post-switch block (lines 63–103). Mode-1 was refactored
onto the same tail with no behavioural change (existing tests pin it).

Centroid + intensity-weighted localization is the standard sub-pixel approach
([Lelek2021], [Cnossen2019]); the Crocker-Grier band-pass localizes
sub-diffraction spheres "to within 10 nm in the focal plane" ([Crocker1996]).

**PR-C3b (landed, this PR)** — the faithful **mode-3 band-pass detector**
(`detect_spots_bandpass`, port of `find_part_bpass_sort.m`) + `BANDPASS` enum
member, wired into `detect_spots_by_mode`: threshold at `t·max` → Crocker-Grier
band-pass (`bpass(I, 1, 9)`; `lobject = 9`, vs mode 2's 7) → keep the top `1 − t`
band-pass values (percentile sort) → regional maxima
(`skimage.morphology.local_maxima` = `imregionalmax`, 8-conn) → 8-connected
centroids, then the shared Stage-4 tail. The `t` is **dual-use** (intensity floor
+ percentile cut), faithful to the reference; default `t = 0.98` (the standalone
`.m` default). Mode 3 localizes with the centroid, not `radialcenter` (the
`findPart.m:30` comment naming `radialcenter` is aspirational; the actual `.m`
uses `regionprops 'Centroid'`). Still additive at the imaging layer
(`schema-guard` green; no lock change).

**PR-C3c (landed, this PR) — the CLI/pipeline selector.** `ExtractOptions` gains
`detection_mode ∈ {wavelet, intensity, bandpass}` + an optional
`detection_threshold` (`[0, 1)`, a fraction of the detection-image max); the
native pipeline routes both halves through `detect_spots_by_mode`, and the choice
is recorded verbatim into `/settings/extraction` (NFR-REPRO). The default
`wavelet` + `None` reproduces the prior à trous detection exactly. `tether extract`
exposes `--detection-mode` / `--detection-threshold`. **Additive at `/settings`
(an empty container group), so `schema-guard` stays green with no version bump**;
no lock change. This resolves the "not reachable from the CLI" trade-off below.

**Deferred to follow-up PRs (the further split):**
- **PR-C3c-decode** — decode the per-movie detection config from the `.tdat`
  (extends `read_tdat`) so an import can auto-apply the method/threshold a movie
  was *actually* detected with. **Investigation finding (probed the real UCKOPSB
  `.tdat`):** `ParticleDetectionMode` is a **plain `temp/` leaf** (`val = 2.0`
  = intensity on this acquisition) — trivially readable, no MCOS needed; but
  `DetectionThreshold` is a **per-channel `TIRFdata` property** reached only
  through the `#subsystem#/MCOS FileWrapper__` blob (`temp/Channel[i]` →
  `0xDD000000` object-reference markers, class_id 4), so it requires a genuine
  MCOS decoder. The committed `tdat_coloc_slice.tdat` fixture **dropped the MCOS
  blob** (6 leaves only), so this PR must also regenerate a fixture that retains
  the MCOS bytes + the relevant `#refs#` property datasets. Kept separate from the
  pipeline wiring (it is an IO-reader concern) per the atomic-PR rule (PLAN §0.2).
- **PR-C3d** — per-channel detect + bidirectional colocalization, oracle
  re-framed to evaluate the **colocalized** set apples-to-apples (USER
  CORRECTION #1), re-measure @1px; **only then** invoke the maintainer-approved
  1px → 2px relaxation if faithful matching still falls short → close M1 / tag
  `v0.1.0`.

### Consequences

- Good: Tether now has a selectable, faithful detector surface; the structural
  mode-1 limitation is no longer the only option; the path to a legitimate M1
  close is unblocked without gaming or weakening the gate.
- Good: additive at the imaging layer — `schema-guard` green, no lock change.
- Resolved (PR-C3c): the selector is now reachable from `ExtractOptions` and the
  `tether extract` CLI, recorded into `/settings/extraction`; still additive
  (`schema-guard` green, no lock change) because `/settings` is a container group.
- **Risk (carried to PR-C3d): detector precision.** The §9 gate checks recall +
  Pearson + RMS, *not* precision; intensity/bandpass detectors emit many spurious
  local maxima on textured backgrounds. Control it with native bidirectional
  colocalization (donor must pair with an independently-detected acceptor), not a
  recall-gaming flood — review will (correctly) flag a detector that finds 5–8×
  too many molecules even when the gate passes.
- Follow-up: per-channel synthetic-truth tests lock mode 2 now; the full-scale
  per-channel + colocalized @1px re-measurement is the gated `large-fixtures`
  leg added in PR-C3d.

## More information

- Reference: `deeplasi/functions/mapping/findPart.m:1,18-62` (`method` dispatch),
  `:21-28,107-115` (mode 2), `external/bpass.m` (Crocker-Grier band-pass),
  `mapping/find_part_bpass_sort.m` (mode 3).
- PRD §11.2 rows "Particle detection mode" / "Detection threshold (intensity
  mode)"; supersedes the implicit mode-1-only assumption in ADR-0020.
- Related: [ADR-0020](0020-extraction-oracle-and-deferred-m1-close.md) (the gap),
  [ADR-0011](0011-home-extraction-recall-at-m1.md) (the M1 recall acceptance),
  [ADR-0015](0015-donor-anchored-colocalization.md) (dark-acceptor read).
- Citations:
  - [Crocker1996] J. C. Crocker & D. G. Grier (1996), *Methods of Digital Video
    Microscopy for Colloidal Studies*, J. Colloid Interface Sci. 179:298.
  - [Lelek2021] M. Lelek et al. (2021), *Single-molecule localization
    microscopy*, Nat. Rev. Methods Primers.
  - [Cnossen2019] J. Cnossen et al. (2019), *Localization microscopy at doubled
    precision with patterned illumination*, Nat. Methods.
