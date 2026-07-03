<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0028 — γ from the acceptor-bleach step (bare-`I_D` convention); Deep-LASI-median oracle deferred

- **Status:** accepted
- **Date:** 2026-07-02
- **Deciders:** bioedca (maintainer)
- **PRD anchor:** §7.2, §7.4, §11.2 (γ half-width row 814 / γ-agreement row 815 / γ ceiling row 818 / `min_window_frames` row 819 / `min_qualifying_traces` row 820), Appendix B.2 step 4, Appendix E Stages 17–18, §9 M3 (FR-CORRECT)
- **Milestone:** M3

## Context and problem statement

M3's last correction step is the detection-correction factor **γ**, which turns the
leakage-corrected proximity ratio into an absolute efficiency
`E = I_A,corr / (I_A,corr + γ·I_D,corr)` (PRD §7.4 / Appendix B.2). γ is estimated
per molecule across the **acceptor-bleach step**: when the acceptor photobleaches
(before the donor), the acceptor intensity drops and the donor rises (dequenching),
and `γ = ΔI_A / ΔI_D` across that step [McCann2010].

Three things had to be settled: (1) the exact estimator — window, gates, aggregation;
(2) the donor correction convention (Tether's additive scheme vs Deep-LASI's
`(1+α)`-scaled donor); and (3) whether the committed/available Deep-LASI data can
supply the §9 M3 acceptance oracle ("γ within ±10 % of the Deep-LASI median on a
shared frame set derived from Deep-LASI's own per-frame classification").

## Decision — the estimator (`tether.fret.gamma`)

Per trace, on the leakage-corrected acceptor `I_A,corr = I_A − α·I_D` and the bare
background-subtracted donor `I_D`:

- **γ = (I_A,spFRET − I_A,after) / (I_D,after − I_D,spFRET)** (PRD Appendix B.2 step 4;
  `deeplasi/functions/deeplearning/deep_autocorrect_2color.m:118-130`), with the ALEX
  `de·(da+dd)` direct-excitation term dropped (δ = 0, no ALEX).
- **Levels** each side of the step are the mean over a **3-frame half-window**
  (§11.2 row 814): pre over `[step − 3, step)`, post over `[step, step + 3)`.
- **Segment gate:** both the pre-step FRET segment `[0, step)` and the post-step
  donor-only segment `[step, donor_bleach)` must be **strictly longer** than
  `min_window_frames` (default 20, §11.2 row 819) — Deep-LASI's
  `length(spFRET_frames) > min_frames && length(da_acc_bleached) > min_frames`
  (`:129`). The same §11.2 quantity the leakage tail uses (one named parameter).
- **Ceiling:** a per-trace γ outside `(0, GAMMA_CEILING]` (= 5, §11.2 row 818) is
  rejected; a non-positive donor jump `ΔI_D ≤ 0` is rejected as `degenerate-donor`.
- **Aggregation:** the dataset factor is the **median** of qualifying per-trace γ,
  **withheld** (`None`) below `min_qualifying_traces` (default 10, §11.2 row 820).
- **Per-molecule value + median fallback:** unlike leakage α (one factor for the
  whole condition), γ is per-molecule — a qualifying molecule keeps its own γ; a
  molecule that fails the gates takes the dataset median (Deep-LASI
  `isnan_corr(gamma(i), median(gamma))`, `:144`). This split is what the later
  staleness scope re-stales on a γ-median shift (PRD §5.1, §7.2).

Stored additively: the per-molecule value into the already-frozen `/molecules.gamma`
(NaN default = "no factor computed"), plus a `/settings/gamma` provenance group
(like `/settings/leakage`). No structural schema change (schema-guard green); no new
§11.2 tunable (all five rows above pre-exist).

## Decision — donor convention: bare `I_D`, not Deep-LASI's `I_D·(1+α)`

Deep-LASI scales the donor by `(1 + ct)` to add the leaked photons back to the donor
budget (`dd·(1+ct)`, `:118`). Tether's additive scheme (PRD Appendix B.2) instead
uses the **bare** corrected donor `I_D,corr = I_D`: the leakage subtraction only
removes donor-leaked photons *from the acceptor*, it does not add them back to the
donor. For `E` to be correct, γ must be defined consistently with that `I_D,corr`, so
γ divides by the **bare** `ΔI_D` — PRD line 1364 writes `I_D` with no `(1+α)` factor.
Each convention is internally self-consistent (the `(1+α)` cancels between a group's
γ definition and its `E`), so **Tether's γ is systematically ≈ `(1 + α)` times
Deep-LASI's on the same step** (α ≈ 0.09 ⇒ ~9 %). This is a principled convention
choice, not an error — but it means a Deep-LASI-median comparison must control for the
convention, not just the frame selection (below).

## Empirical finding — the available Deep-LASI export cannot supply the *strict* γ oracle

The §9 M3 γ acceptance oracle requires a **shared frame set derived from Deep-LASI's
own per-frame classification** (estimator-isolated). Direct inspection of the source
export `example-data/bla-uckopsb-tbox-video10/DeepLASI_MAT_export_…010.mat`
(250 molecules × 1700 frames, the `TRacer_v1` trace export) shows:

- **`g` (Deep-LASI γ):** present, 250 non-zero, **median ≈ 0.569** (min 0.189, max
  4.090) — but only **9 distinct values**, i.e. dominated by the population-median
  fallback (`deep_autocorrect_2color.m:144` replaces every gate-failing molecule's γ
  with `median(gamma)`), so it is a population summary, not 250 independent per-trace
  γ.
- **`b` (Deep-LASI leakage):** present, median ≈ **0.090** (0.021–0.252) — consistent
  with the empirical Cy3→Cy5 leakage and Tether's tail-α (ADR-0027).
- **No per-frame classification:** the `fret` field is **all zeros** (no spFRET /
  acceptor-bleached labels), and there is **no DeepLASI session/`.dlss` file** in
  `example-data/` carrying `NeuralNetwork.Probabilities`. So the spFRET vs
  acceptor-bleached frame partition Deep-LASI used **cannot be reconstructed**.
- **`pacc`/`pdon` are a uniform constant 60** (per ADR-0026), so they do not localise
  the per-molecule acceptor-bleach step either.

Therefore the *strict* estimator-isolated oracle (Tether's γ vs Deep-LASI's γ on
Deep-LASI's own classified frames) is **not computable** from the available data —
the same capability boundary hit in ADR-0026 (bleach oracle) and ADR-0027 (donor-only
α), not a fabrication license (§Data-gaps). The `median(g) = 0.569` reference is
recorded here for the eventual comparison, which must additionally divide out the
`(1+α)` donor-convention difference above (Tether's bare-`I_D` γ ≈ Deep-LASI's × `(1+α)`).

## Considered options

- **A — Block M3 PR3 on the Deep-LASI-median oracle.** Rejected: the estimator is
  fully specifiable and testable now against synthetic ground truth; blocking would
  strand real γ correction on a classification-file dependency that does not exist in
  the vendored data.
- **B — Reconstruct "Deep-LASI's classification" from Tether's own bleach detector
  and call it the estimator-isolated oracle.** Rejected: that substitutes Tether's
  frame selection for Deep-LASI's, so it is no longer *estimator-isolated* — it would
  silently pass on a shared-detector artefact, defeating the oracle.
- **C — Fabricate a classification / reference γ to satisfy the ±10 % test.**
  Rejected: the `fret` field is empty and `g` is fallback-dominated; a stubbed
  classification silently biases the validation — the exact §Data-gaps trap.
- **D (chosen) — Ship the estimator now, validated against synthetic known-γ recovery
  and a reference-formula parity check (Deep-LASI's `ΔI_A/ΔI_D`, δ = 0 simplification,
  on a shared synthetic frame set = estimator-isolated on data we control); record the
  `median(g)` reference and defer the Deep-LASI-median cross-check to a follow-up gated
  on a per-frame classification source (a DeepLASI session file, or the full pipeline
  re-run with the `(1+α)` convention reconciled).**

## Consequences

- **Positive:** real, spec-faithful γ lands with durable CI coverage (synthetic
  recovery + reference-formula parity + every gate + store integration); the
  bare-`I_D` convention is fixed and documented so the corrected-E PR (PR4) and the
  later staleness PR key off a single definition; the `median(g) = 0.569` reference
  and the `(1+α)` caveat are recorded so the deferred comparison is unambiguous.
- **Negative / follow-up:** the §9 M3 strict γ-agreement oracle is **not** exercised
  on real Deep-LASI data this PR. Re-home it when a per-frame classification source is
  available, comparing Tether's bare-`I_D` γ against Deep-LASI's `g` with the `(1+α)`
  convention divided out, on a shared classified frame set.
- **Neutral:** the estimator reads `/molecules.alpha` (PR #75) and `bleach_frames`
  (PR #74); it raises a clear prerequisite error if either is absent, making the
  `background → α → γ` order explicit rather than silently correcting on undefined
  inputs.
