# 0027 — Leakage α from the post-acceptor-bleach tail; donor-only-sample cross-check deferred

- **Status:** accepted
- **Date:** 2026-07-02
- **Deciders:** bioedca (maintainer)
- **PRD anchor:** §7.2, §7.4, §11.2 (leakage ceiling / `min_window_frames` / `min_qualifying_traces` / validation band), Appendix B.2 step 2, Appendix E Stages 17–18, §9 M3 (FR-CORRECT, oracle e)
- **Milestone:** M3

## Context and problem statement

M3's second correction step is the donor→acceptor **leakage factor α**, applied
additively `I_A,corr = I_A* − α · I_D*` (PRD §7.4 / Appendix B.2). α must be
estimated from a *donor-only* condition — frames where the donor emits but the
acceptor does not, so acceptor-channel signal is pure leakage. PRD §7.2 names two
estimators and makes their **agreement** the validation oracle (§9 M3 row e, the
§11.2 conjunctive band): (i) a **global α** from a dedicated Cy3-only donor-only
sample, and (ii) the **per-trace post-acceptor-bleach tail** α from the FRET data
itself.

Two questions had to be resolved: (1) is the committed donor-only sample usable to
compute a global `median(I_DA/I_DD)`, and (2) how are α and its provenance stored
under the M0 schema freeze.

## Empirical finding — the committed donor-only `.tdat` yields no computable global α (yet)

`example-data/cy3-donor-only-calibration/` holds a real Cy3-only acquisition
(`DeepLASI_DATA_Cy3_only_WCBN_2ndreplicate_15pM_001…tdat`, 18.6 MB) + two `.tmap`s,
**but no movie and no `.txt`/`.mat` trace export**. Direct inspection shows:

- `temp/DefaultAlpha = DefaultBeta = DefaultGamma = 0` — Deep-LASI stored **no**
  leakage value, so there is no reference α to read (`read_tdat(...).corrections.alpha`
  is `0.0`).
- The per-frame donor/acceptor **traces exist only inside the undecoded MCOS
  `#subsystem#/FileWrapper__` blob** (`nTraces`, 2 channels, 91 colocalized
  molecules). `tether.io.tdat` decodes coordinates + correction factors + detection
  mode, **not** traces (trace/HMMdata MCOS decode is explicitly out of scope —
  legacy-importer / M7 territory).

So computing `median(I_DA/I_DD)` from this sample needs either an MCOS **trace**
decoder or the donor-only movie (to re-extract) — neither available this PR. This is
a *capability* boundary, not a fabrication license: per §Data-gaps, we withhold the
donor-only path rather than stub a distribution.

Crucially, the tail estimator is **not** a lesser substitute. After a molecule's
acceptor photobleaches (but before its donor bleaches), the trace *is* a
per-molecule donor-only measurement, and leakage α is a property of the donor dye +
emission filters (shared across constructs), so the tail α measures the same
quantity a bulk Cy3-only sample would — it is the standard, always-available
estimator [Hellenkamp2018][Lee2005].

## Considered options

- **A — Block M3 PR2 on the donor-only global α.** Rejected: the tail estimator is
  fully computable now and is the scientifically primary, calibration-free method;
  blocking would strand real leakage correction on an MCOS-decode dependency.
- **B — Fabricate/stub a donor-only α (or read the zero `DefaultBeta`) to satisfy
  the conjunctive band.** Rejected: `DefaultBeta = 0` is not a measurement, and a
  stubbed distribution silently biases every corrected E — the exact §Data-gaps trap.
- **C — Ship the tail estimator as the primary α now; defer the donor-only-sample
  cross-check (and its band test) to a follow-up gated on an MCOS trace decoder**
  (this ADR). Honest, delivers real correction, fabricates nothing.

## Decision outcome

Chosen option **"C"**.

### Estimator (`tether.fret.leakage`) — pure numpy, Qt-free

1. **Per-trace α** over the tail `[acceptor_pb, min(donor_pb, T))` follows
   Deep-LASI's crosstalk definition `ct = mean(I_DA) / mean(I_DD)` (PRD §11.1;
   `deeplasi …/manualCorrectionFactors.m`) — a ratio of window means (not a mean of
   per-frame ratios), robust to a near-zero donor frame. Rides directly on PR #74's
   per-channel `bleach_frames`.
2. **Gates (PRD §11.2, all named, none hardcoded):** the tail must be ≥
   `min_window_frames` (default 20); a per-trace α outside `[0, LEAKAGE_CEILING]`
   (≈ 0.3; Cy3→Cy5 leakage is typically 0.05–0.2, median ≈ 0.09) is dropped as
   non-physical / implausible; the **dataset α = median** of the qualifying per-trace
   values (a single per-condition instrument/dye factor), **withheld** below
   `min_qualifying_traces` (≈ 10) rather than emitted from too little data (PRD §7.2
   total-failure path). `min_window_frames` (per-trace) and `min_qualifying_traces`
   (per-dataset) are kept as distinct parameters (§11.2 L831).
3. `apply_leakage(donor, acceptor, α) → I_A − α·I_D` — the additive correction, no
   clipping (a slightly negative noisy frame is a real fluctuation).

### Storage (`tether.project.leakage.compute_leakage_alpha`) — additive only

4. Writes the applied factor into the **already-frozen** `/molecules.alpha` for
   every processed molecule (α is per-condition, applied cohort-wide). The
   `alpha = NaN` extraction default is the "no factor computed" sentinel PR4's
   apparent-E fallback consumes; the withhold path leaves it NaN.
5. Stamps a `/settings/leakage` provenance group (source estimator, effective gates,
   `n_qualifying`, `app_version`, timestamp) — **additive data** under the frozen
   `/settings` container, mirroring `/settings/extraction`; recomputable
   (overwritten each pass). `schema-guard` stays green (a `only-new-group-is-
   /settings/leakage` test locks this). The **per-condition** α + its donor-only
   provenance move to the frozen `/conditions.leakage_alpha` / `leakage_alpha_source`
   when M4 introduces condition rows.

### Deferred (own follow-up PR)

6. The **global α from the Cy3-only donor-only sample** + the **conjunctive §11.2
   band cross-check** (relative-median diff ≤ 20% **and** both medians ∈ 0.05–0.2)
   are deferred, gated on an **MCOS trace decoder** (or the donor-only movie) to
   recover the sample's per-frame traces. Until then the tail α is the applied
   factor; the band test is not gated against a fabricated donor-only value.

### Consequences

- Additive at the data layer — no `.tether` structural change (`schema-guard`
  green), no `conda-lock` change (pure numpy), no new §11.2 tunable (ceiling /
  `min_window_frames` / `min_qualifying_traces` / band already registered, rows
  817/819/820/825).
- α provenance is currently dataset-level (`/settings/leakage`); per-condition
  storage lands with M4 conditions.
- The γ PR (next) consumes the acceptor-bleach step on **leakage-corrected**
  intensities; PR4 (corrected-E) consumes `/molecules.alpha` (NaN → apparent-E).

## More information

- Empirical probe (donor-only `.tdat` structure, `DefaultBeta = 0`, traces in the
  MCOS blob) is recorded in PLAN §15.
- Builds on [ADR-0005](0005-m0-schema-freeze.md) (additive-only),
  [ADR-0003](0003-apparent-e-never-nan.md) (total-failure → apparent-E),
  [ADR-0008](0008-correction-factor-remap.md) (β→α, δ inert),
  [ADR-0026](0026-photobleach-detection-and-window-default.md) (`bleach_frames`
  this consumes). Data-gap-honesty precedent: [ADR-0022](0022-m1-acceptance-reframe-and-close.md).
- Citations:
  - [Hellenkamp2018] B. Hellenkamp et al. (2018), *Precision and accuracy of
    single-molecule FRET measurements — a multi-laboratory benchmark study*, Nat.
    Methods 15:669 — the unified leakage/γ correction procedure and the empirical
    leakage magnitude (median ≈ 0.09).
  - [Lee2005] N. K. Lee et al. (2005), *Accurate FRET measurements within single
    diffusing biomolecules using alternating-laser excitation*, Biophys. J. 88:2939 —
    the additive leakage correction `I_A,corr = I_A − α·I_D`.
