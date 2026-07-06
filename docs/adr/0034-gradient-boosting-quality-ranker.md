<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0034 — Gradient-boosting quality ranker: HistGradientBoosting on `P(good)`, precision@k objective

- **Status:** accepted
- **Date:** 2026-07-06
- **Deciders:** bioedca (maintainer)
- **PRD anchor:** §7.5 (FR-ML quality ranker: precision@k objective, never auto-drop), §11.2 (default parameters), §9 M5
- **Milestone:** M5

## Context and problem statement

PRD §7.5 requires a classical, GPU-free **quality ranker**: a gradient-boosting model
[Chen2016] over the engineered `/features` vectors that **sorts/pre-sorts** traces by quality
and **never auto-drops**, optimizing **precision@k** — the fraction of good (human-accepted)
traces among the first `k` reviewed. The evaluation substrate landed independently: PR #90
built the model-free `precision_at_k` + the never-auto-drop `rank_by_score` / `RankedTraces`
permutation contract, and `tether.project.ranking.ranking_dataset` joins `/features` ⋈
`/labels` into the supervised `(X, accept/reject)` view. PR #92 added scikit-learn + XGBoost
to the base conda-lock. What was unhomed: **which model, trained on what objective, produces
the scores** — and how it upholds the never-fabricate / never-drop invariants.

Two design tensions:

1. The engineered features are **`NaN` where undefined** (a too-short window, a lone
   molecule's `neighbor_distance` — never a fabricated `0`; `tether.ml.features`). A model
   that requires imputation would fabricate exactly the value the feature layer refused to
   invent — a silent bug.
2. "Optimizes precision@k" must be turned into a concrete training objective on a **small,
   growing, binary-labeled** curation set (tens–hundreds of accept/reject labels), and the
   result must be a **reproducible** ranking.

## Decision

**Rank by `P(good)` from a binary gradient-boosting classifier.** The score fed to
`rank_by_score` is the model's predicted probability that a human would accept the trace. By
the **Probability Ranking Principle** [Robertson1977], ordering items by decreasing
probability of relevance is optimal for a precision-based objective — so a well-ordered
`P(good)` *is* the precision@k-optimal score, and framing the ranker as a binary classifier
(accept = good) matches the binary accept/reject ground truth exactly. This is the field
norm for automated smFRET trace selection (AutoSiM [Li2020], DeepFRET [Thomsen2020], Deep-LASI
[Wanninger2023] all sort/select on features of this kind).

**Model = scikit-learn `HistGradientBoostingClassifier`** (not XGBoost, though both are in the
base lock and either satisfies "gradient-boosting ranker"), chosen for three properties this
problem needs:

- **Native `NaN` support** at fit *and* predict — no imputation. Missingness can even be
  learned as predictive. This is the direct complement of the never-fabricate feature
  contract, and it lets the ranker **score and keep** a molecule with undefined features
  (unlike `tether.ml.similarity`, which cannot embed such a point in a metric space and
  reports it unindexed). No molecule is dropped for missing features.
- **Determinism** given `random_state` (which seeds the histogram-binning subsample, itself
  only drawn far above the curation regime; early stopping off) → a reproducible ranking.
- **`sample_weight` in `fit`** — the seam the later per-label weighting / cold-start-decay PR
  (`w = w₀/(1+n_human)`, PRD §7.5) plugs into with no model change.

**Curation-regime hyperparameters (PRD §11.2 "Quality-ranker model" row).**
`learning_rate=0.1, max_iter=100, max_leaf_nodes=15, min_samples_leaf=5,
l2_regularization=1.0, early_stopping=False, random_state=0`. The leaf/regularization
defaults are **smaller than scikit-learn's stock** (`min_samples_leaf` 5 vs 20,
`max_leaf_nodes` 15 vs 31) because curation label sets are small; stock defaults would refuse
to split a modest set and collapse the ranker to a constant. Early stopping is off (a
validation hold-out is wasteful on a small set and would make the fit split-dependent). The
tunables live in `RankerHyperparams` (one source of truth), not scattered literals.

**Degenerate label sets are refused loudly.** A discriminative ranker needs both classes:
training on **one class** (all accept or all reject) or **no labels** raises `ValueError`
rather than silently fitting a constant score — surfaced, never a fabricated ranking.

**Never-auto-drop is a permutation invariant.** `QualityRanker.rank(ids, X)` scores each row
independently (order-independent) and hands the scores to `rank_by_score`, so permuting the
candidate order yields the *identical* ranking (ties broken on `molecule_id`), every molecule
kept exactly once — the oracle-(d) invariant, tested directly.

## Scope and consequences

- **Additive under the M0 freeze; no conda-lock change.** `tether.ml.gbranker` +
  `tether.project.gbranking` are **read-only** over the M0-frozen `/features` + `/molecules`
  (via `ranking_dataset`) — no group/dataset/dtype/field change, `schema-guard` green — and
  add no dependency (scikit-learn was locked in #92).
- **`import tether.ml` stays scikit-learn-free.** The sklearn import is lazy (inside
  `train_quality_ranker`), so the base GUI/import surface is unaffected until a ranker is
  actually trained.
- **Apparent precision@k is *not* the ship gate.** `ranker_precision_at_k` is measured
  **in-sample** (trained and scored on the same labels) — a fit diagnostic, optimistically
  biased. The honest **prequential**, held-out, median-across-videos precision@k **uplift**
  (PRD §7.5; oracle (d)) is its own later PR; the docstrings say so to prevent the in-sample
  number being mistaken for the gate.
- **Explicitly out of scope (each a later M5 PR).** Persistence as a portable
  load/warm-start-retrain/save artifact; per-label `source` weighting + cold-start decay;
  the prequential uplift gate; cross-condition seeding + drift flag + multi-curator merge; the
  active-learning "recommended next" badge.

## Alternatives considered

- **XGBoost `rank:pairwise` / LambdaMART (learning-to-rank).** Rejected for this PR: LTR needs
  query groups and enough pairs per group, which the small early-curation set lacks; with
  binary relevance and a per-video "query," ranking positives above negatives is what
  `P(good)` already delivers, with fewer moving parts and native `NaN`. XGBoost remains
  available in the lock if a future PR wants a pairwise objective.
- **Impute NaN then use any classifier.** Rejected: imputation fabricates the value the
  feature layer deliberately left `NaN`, violating the never-fabricate discipline. Native
  missing-value handling is the reason `HistGradientBoosting` was chosen.
- **Drop molecules with undefined features from the ranking** (the `similarity` exclusion).
  Rejected here: unlike a metric-space embedding, the tree model *can* score a `NaN`-feature
  row natively, so it is scored and kept — closer to never-auto-drop than excluding it.
- **scikit-learn stock hyperparameters.** Rejected: `min_samples_leaf=20` collapses the ranker
  to a constant on realistic curation sizes; the curation-tuned defaults are registered in
  §11.2 with this rationale.
