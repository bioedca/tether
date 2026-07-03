# 0026 — Native single-step photobleach detection, summed-intensity analysis-window default, and the `pacc`/`pdon` oracle reframe

- **Status:** accepted (opens M3)
- **Date:** 2026-07-02
- **Deciders:** bioedca (maintainer)
- **PRD anchor:** §7.2, §11.2 (bleach-detection / analysis-window rows), Appendix B step 6, Appendix E Stage 16, §8 NFR-VALID (g), §9 M3 (FR-CORRECT)
- **Milestone:** M3

## Context and problem statement

M3's first correction step needs the per-molecule photobleach frames (donor,
acceptor) and a per-trace **analysis window** so that histograms, corrections,
and idealization all operate over "the frames both dyes are active." PRD §11.2
specifies a native reimplementation of tMAVEN's Bayesian single-step model
(signal → `N(0)`, priors `a = b = β = 1`, `μ = 1000`), run per channel, with the
analysis-window default set to trace-start → first bleach of the **summed**
donor+acceptor intensity (Appendix B step 6). The §9 M3 acceptance (row 816)
states this is "validated vs `.mat` `pacc`/`pdon` within ±2 frames."

Two questions had to be resolved before implementing: (1) how to store the
outputs without breaking the M0 schema freeze, and (2) whether the committed
Deep-LASI `pacc`/`pdon` field is a usable per-molecule bleach oracle.

## Decision drivers

- **Schema freeze** (ADR-0005): additive data only; `schema-guard` stays green.
- **Faithfulness to tMAVEN** (PLAN §1.3 #7): the native detector must reproduce
  the reference model, not merely approximate it.
- **Never weaken a test to a wrong oracle, never fabricate data** (PLAN §1.3 #8,
  §Data-gaps): if the specified oracle is invalid, reframe honestly rather than
  gate against a value the detector would have to be wrong to match.
- **Manual override wins** (§7.2): the curator's window edits are authoritative.

## Empirical finding — `pacc`/`pdon` is not a per-molecule bleach oracle

The Deep-LASI export `DeepLASI_MAT_export_…010.mat` carries `pacc` and `pdon`
(`uint8`, 250×1). Both are the **constant 60** for **every one of the 250
molecules**. But the molecules' corrected traces (`donc`/`accc`) bleach to
background at wildly different frames — the native detector (faithful to tMAVEN,
see below) finds first-bleach at ≈97, ≈644, ≈1693, … across molecules, each a
clean signal→baseline step (e.g. `donc` pre ≈ 549 → post ≈ 4). A uniform 60 for
molecules that demonstrably bleach at 97 and 1693 cannot be a per-molecule bleach
time; it is a **global acquisition/analysis marker** (the fixed pre-analysis
offset of this export). Additionally, the committed `acceptor_oracle.npz` /
`aperture_oracle.npz` traces are **raw** (a ~2000-count background pedestal that
never decays to `N(0)`), so the single-step model correctly reports "does not
bleach" on them — raw traces are the wrong input for a decay-to-zero model.

Gating "detector within ±2 frames of `pacc`/`pdon`" would therefore force the
detector to return 60 on a molecule that bleaches at 97 — i.e. require it to be
scientifically **wrong**. That is exactly the "weaken the test to match missing
data" trap the working agreement forbids.

## Considered options

- **A — Gate against `pacc`/`pdon` as written.** Rejected: the field is a
  constant marker, not ground truth; passing it would require a wrong detector.
- **B — Fabricate/curate a per-molecule `pdon` fixture to hit ±2.** Rejected:
  no defensible independent per-molecule bleach oracle exists in the available
  data; inventing one is fabrication.
- **C — Validate against genuine ground truth: synthesized known-step traces
  (across position and SNR) for the ±2 acceptance, plus exact reference-formula
  parity, plus real-corrected-trace behavior** (this ADR). Honest, reproducible,
  and stronger than a single uniform oracle value.

## Decision outcome

Chosen option **"C"**, maintainer-approved (2026-07-02).

### Detector (`tether.fret.photobleach`)

1. A **vectorized, Qt-free** reimplementation of tMAVEN's single-step model:
   conjugate Normal-inverse-Gamma evidence per candidate change point, evaluated
   in `O(T)` via prefix sums (the reference is `O(T²)` per trace). `point_pbtime`
   mirrors `get_point_pbtime` (MAP change point; `== T` ⇒ no bleach within the
   trace); `ensemble_pbtime` mirrors `pb_ensemble` (two-pass exponential-lifetime
   prior, first pass truncated to `int64` exactly as the reference does).
   `detect_photobleach` runs it per channel **and** on the summed intensity.
   Priors default to the frozen §11.2 values (`a = b = β = 1`, `μ = 1000`),
   exposed as named constants (`PB_PRIOR_*`), never hardcoded.
2. **Analysis-window default = `(start, sum_pb)`** where `sum_pb` is the first
   bleach of the summed donor+acceptor intensity (Appendix B step 6 / §11.2):
   under donor excitation the sum stays above background until the last-surviving
   dye bleaches, so this places the window end at the loss of usable signal.

### Storage (`tether.project.photobleach.compute_photobleach`) — additive only

3. Writes into the **already-frozen** `/molecules` fields — `bleach_frames`
   (donor, acceptor) and `analysis_window` — via a read-modify-write on the
   traces layer selected by `intensity_quantity` (default `"corrected"`, the
   background-subtracted layer the model requires). **No group or dataset is
   added** (`schema-guard` green; a `no-new-groups` test locks this).
4. **Manual override wins.** The auto window overwrites `analysis_window` only
   where it still equals the extraction default (`== frame_range`); a window a
   curator has already narrowed is preserved. This needs no new "is-manual" flag
   and no change to the three existing window readers (`windowed_channels`,
   `idealize._windows`, `handoff._store_window`), which keep reading
   `analysis_window` unchanged.

### Validation (reframes §9 M3 row 816 for this data)

5. **Reference parity** — the vectorized likelihood / `point_pbtime` /
   `ensemble_pbtime` are asserted equal to a direct in-test transcription of the
   tMAVEN formulas. This is the faithfulness guarantee the "±2 vs a reference"
   clause was reaching for.
6. **Synthetic known-step ground truth** — injected `N(μ,σ)→N(0,σ)` steps across
   positions and SNRs must be recovered within **±2 frames** (the §11.2 bleach
   tolerance, against a *known* answer).
7. The `pacc`/`pdon` ±2 clause (row 816) is **not** gated against this export's
   constant field. If a genuine per-molecule photobleach oracle is later sourced
   (a dataset whose `pacc`/`pdon` vary per molecule and whose corrected traces
   decay to baseline), it can be added as a `@pytest.mark.large` real-data check
   without changing the detector.

### Consequences

- Additive at the data layer — no `.tether` structural change (`schema-guard`
  green), no `conda-lock` change (pure numpy + `scipy.special.gammaln`), no new
  §11.2 tunable (priors/window/tolerance already registered, rows 813/816/819/821).
- The GUI `P`-key trigger and the manual `-`/`=`/`[`/`]` window-nudge handlers
  (bound but no-op since M2) are **not** wired here — this PR is the headless
  detection + storage substrate; the GUI wiring is a follow-up (its own
  computer-use live-smoke + `pytest-qt` coverage).
- M3's leakage-α and γ PRs consume `bleach_frames` (post-acceptor-bleach tail,
  acceptor-bleach step) and the analysis window directly.

## More information

- Reference: `tmaven/tmaven/controllers/photobleaching/photobleaching.py`
  (`get_point_pbtime`, `pb_ensemble`) and `…/photobleaching_controller.py`
  (`sum = True`, prior defaults). Probe scripts + the per-molecule finding are
  recorded in PLAN §15.
- Builds on [ADR-0005](0005-m0-schema-freeze.md) (additive-only),
  [ADR-0008](0008-correction-factor-remap.md) (β→α, δ inert),
  [ADR-0016](0016-extraction-trace-store-layout.md) (`/traces`, `analysis_window`
  = extraction default). Reframe precedent: [ADR-0022](0022-m1-acceptance-reframe-and-close.md).
- Citations:
  - [Verma2024] A. R. Verma et al. (2024), *tMAVEN*, Biophysical Journal — the
    reimplemented single-step model.
  - [Tsekouras2016] K. Tsekouras, T. C. Custer, H. Jashnsaz, R. H. Baker & S.
    Pressé (2016), *A novel method to accurately locate and count large numbers
    of steps by photobleaching*, Mol. Biol. Cell 27:3601 — Bayesian step counting
    recovers ground truth to low SNR.
  - [Garry2020] J. Garry et al. (2020), *Bayesian counting of photobleaching
    steps with physical priors*, J. Chem. Phys. 152:024110 — MAP change-point
    step detection is more precise/less biased than ratiometric or naive
    change-point counting.
  - [Mattamira2025] C. Mattamira et al. (2025), *Bayesian analysis and efficient
    algorithms for single-molecule fluorescence data and step counting*, Biophys.
    J. — validates Bayesian step recovery against synthetic ground truth across
    SNR.
