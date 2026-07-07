# 0037 — Cross-condition drift advisory: the drift statistic, how per-feature tests combine, and its semantics

- **Status:** accepted
- **Date:** 2026-07-07
- **Deciders:** bioedca
- **PRD anchor:** §7.5, §11.2 (FR-ML) — a condition's ranker may be seeded from another condition, and "cross-condition use raises an advisory (overridable) flag driven by a simple feature-distribution / FRET-range / SNR drift signal between the source and target conditions"
- **Milestone:** M5

## Context and problem statement

The M5 ranker can be **seeded** from another condition (or from Deep-LASI categories), but that transfer only helps if the two conditions are distributionally similar. PRD §7.5 requires an **advisory (overridable) flag** on cross-condition use, "driven by a simple feature-distribution / FRET-range / SNR drift signal." The PRD fixes the *intent* but leaves the implementing PR three things to pin down:

1. **What statistic** measures "drift" between two conditions' feature distributions.
2. **How the per-feature tests combine** into one flag without a false-alarm blow-up.
3. **What the flag's semantics are** — advisory vs blocking — and where the layering lives.

Getting (2) wrong is the subtle trap: naively OR-ing an independent test per feature at α inflates the false-alarm rate on genuinely-matched conditions to `1 − (1 − α)^F` (≈ 37% at α = 0.05, F = 9 features), so a matched seed would spuriously flag a third of the time — the opposite of a useful advisory.

## Decision drivers

- **Simple, label-free, defensible** (PRD §7.5 says "simple"): drift must be measurable from the raw `/features` distributions with no labels and no trained model.
- **The three named axes** (feature-distribution / FRET-range / SNR) must all be covered.
- **Never fabricate / never over-claim** (§7.5): an untestable feature must be reported untested, not a fabricated "no drift"; a wholly-untestable comparison must raise, not return "not drifted."
- **Advisory, overridable** — the flag informs the seeding decision; it never blocks it.
- **Layer hygiene** — the pure `tether.ml` core must not depend on the store (the `condition_id` grouping is a `tether.project` concept), mirroring [ADR-0036].
- **Deterministic across the 3-OS matrix** — the verdict must be a reproducible function of the two feature matrices.

## Considered options

- **Drift statistic:** two-sample **Kolmogorov–Smirnov** test per feature vs. population stability index (PSI) vs. a standardized mean-shift (z). KS is the standard non-parametric, label-free two-sample test and the field-standard covariate-drift detector [Porwik2022, Cardoso2023]; PSI needs binning choices; a mean-shift misses variance/shape drift.
- **Combining the per-feature tests:** naive OR at α vs. **Bonferroni** family-wise correction (α / n_tested) vs. Holm's step-down vs. requiring a fraction of features to drift. Bonferroni is the classic, conservative, dependency-agnostic control of the family-wise error and needs no ordering or extra tunable.
- **KS p-value method:** `exact` vs. **`asymp`** (asymptotic). Exact is more accurate at tiny N but emits ties/precision warnings and switches method by sample size; asymp is sample-size-consistent, tie-robust, and deterministic — ample for an advisory Bonferroni gate.
- **Semantics:** advisory (overridable) — mandated by §7.5 — vs. a blocking gate (rejected).
- **Which features:** all engineered features (the FRET-range = `fret_mean`/`fret_var` and SNR = `snr` columns are already members) vs. a hand-picked subset. Monitoring all covers "feature-distribution" in full and subsumes the two named sub-axes.

## Decision outcome

Chosen: a **per-feature two-sample Kolmogorov–Smirnov test** (`method="asymp"`) over the engineered `/features`, combined with a **Bonferroni family-wise correction** — each feature drifts iff its p-value `< α / n_tested`, and the overall **advisory** fires iff any feature drifts. The pure `tether.ml.drift.condition_drift` takes two feature matrices + names (store-free); the thin `tether.project.drift.cross_condition_drift` groups `/features` by `condition_id` and calls it. A single per-feature sweep covers all three PRD axes at once (whole sweep = feature-distribution; `fret_mean`/`fret_var` = FRET-range; `snr` = SNR), and `DriftReport.drifted_features` names which moved.

The **default overall α = 0.05** is registered as the one new §11.2 tunable ("Cross-condition drift advisory"); the Bonferroni denominator is the number of features actually testable, so the *combined* false-alarm rate on matched conditions stays ≈ α rather than ≈ 37%.

### Consequences

- Good: `schema-guard` stays green — read-only over the frozen `/features` + `/molecules`, nothing persisted. No conda-lock change (SciPy `ks_2samp` is already in the base lock; no sklearn — drift is model-free).
- Good: matched conditions robustly do **not** flag (family-wise ≈ α), while real drift (a vanishing KS p-value) clears the corrected bar easily — the §9 M5 "mismatched raises, matched does not" acceptance holds at both the pure and store layers.
- Good: never fabricates — a feature with `< 2` finite values in either condition is reported `tested=False` (statistic/p-value NaN), excluded from the Bonferroni denominator and the flag; a wholly-untestable comparison raises.
- Trade-off: Bonferroni is conservative (it can under-flag a broad, shallow drift spread thinly across many features). Acceptable for an *advisory* — a curator may seed regardless — and a Holm/FDR refinement is a later option if the advisory proves too quiet.
- Trade-off: comparing conditions across **different** `.tether` files (seeding from another experiment) is left to the seeding PR, which composes the same primitives (`condition_feature_matrices` per file → the file-agnostic `condition_drift`).
- Follow-up: `test_ml_drift` (matched/mismatched, Bonferroni wiring, NaN handling, all-untestable raise, determinism) and `test_project_drift` (the §9 M5 store acceptance on shifted-FRET and shifted-SNR conditions).

## More information

- PRD §7.5 (curation & per-condition ML), §11.2 "Cross-condition drift advisory"; PLAN §9 M5 "seeding + drift flag + multi-curator split-merge" (the **drift-flag** concern; seeding and the multi-curator split-merge are separate follow-up PRs).
- Consumes the guard the **seeding** path (a later M5 PR) raises before folding cross-condition seed labels into the weighted training set ([ADR-0036]'s `sample_weight` seam + `recompute_label_weights`).
- [Porwik2022] Porwik, Doroz & Wrobel, "Detection of data drift in a two-dimensional stream using the Kolmogorov–Smirnov test," *Procedia Computer Science* (2022).
- [Cardoso2023] Cardoso et al., "Online evaluation of the Kolmogorov–Smirnov test on arbitrarily large samples," *Journal of Computational Science* (2023).
