# 0036 — Cold-start label weighting: the decay law, what `n_human` counts, and where it lives

- **Status:** accepted
- **Date:** 2026-07-07
- **Deciders:** bioedca
- **PRD anchor:** §7.5, §5.1 `/labels`, §11.2 (FR-ML) — the per-condition ranker trains weighted by each label's `source`; the per-row `weight` is recomputed and rewritten on each retrain
- **Milestone:** M5

## Context and problem statement

The per-condition quality ranker trains on a condition's `/labels`, where a human accept/reject
is ground truth but the two provisional priors (`deeplasi-provisional`, `cross-condition-seed`) are
weaker cold-start signals that should fade as real curation arrives (PRD §7.5). PRD §7.5/§11.2 fix
the shape — `w = w₀/(1+n_human)`, `w₀ ≈ 0.3`, human `= 1.0` — but leave three things for the
implementing PR to pin down: **what exactly `n_human` counts**, **where the decay law lives** so the
layering stays clean, and **when/where the weights are (re)computed and persisted**. Getting
`n_human` wrong (e.g. inflating it by re-curation) would silently over- or under-decay every prior.

## Decision drivers

- The §11.2 tunable is already registered (`w₀ ≈ 0.3`, `w = w₀/(1+n_human)`) — implement it, do not
  redefine it; no new tunable.
- `weight` is a **frozen** `/labels` field (ADR-0005, ADR-0023) declared **mutable** by §7.5 — the
  recompute must be an additive *value* rewrite (schema-guard green), never a structural change.
- Never fabricate / never auto-drop (§7.5): a decayed prior is down-weighted, never deleted; a
  zero-weight prior is still a kept row.
- Layer hygiene: the pure `tether.ml` core must not depend on the store or the `/labels` `source`
  vocabulary (which is a `tether.project` concept).

## Considered options

- **`n_human` = count of human `/labels` event rows in the condition.** Literal reading of "count of
  human labels," but re-curating one molecule (accept → reject → un-reject) inflates the count, so a
  changed mind decays every prior further — not the amount of ground truth.
- **`n_human` = count of molecules in the condition currently carrying a human accept/reject**
  (`/molecules.curation_label != UNCURATED`, grouped by `condition_id`). The trusted-evidence
  reading; equals the condition's ranker training-set size; robust to re-curation.
- **Compute-on-the-fly (do not persist)** vs **recompute-and-rewrite `/labels` on each retrain.**
- **Put the decay law in the store layer** vs **a pure `tether.ml` law + a thin store recompute.**

## Decision outcome

Chosen: **`n_human` = the condition's count of human-curated molecules**
(`/molecules.curation_label != UNCURATED`), a **pure `tether.ml.weighting` decay law** consumed by a
thin **`tether.project.weighting.recompute_label_weights`** that **rewrites the `/labels.weight`
column in place on each retrain**, and an optional **`sample_weight`** seam on
`train_quality_ranker` (plus the persistence retrainers) that the store weights feed.

Reading `n_human` from the authoritative human state (a provisional source never sets
`curation_label`; ADR-0023) makes it exactly the trusted evidence the ranker trains on and immune to
re-curation, so a seed's influence shrinks in lockstep with the ground truth that supersedes it.
The pure/`w = w₀/(1+n_human)` law stays store-free and vocabulary-free (it takes an `is_human` mask
+ per-row `n_human`); the store layer owns the `source→is_human` mapping and the per-condition count.
Persisting the weights means any consumer reads them straight from `/labels` rather than re-deriving.

### Consequences

- Good: schema-guard stays green (value-only rewrite of a frozen, §7.5-mutable field); no new
  tunable; the decay is a permutation-stable, idempotent function of the current label set.
- Good: `sample_weight=None` is behaviourally identical to the unweighted fit, so the seam is inert
  until seeds actually enter training — a clean hand-off to the seeding PR.
- Trade-off: folding provisional/seed `/labels` rows **into** the training set (today the ranker
  trains on the human-only `/molecules.curation_label`) is **deferred to the seeding + multi-curator
  merge PR**, which will build the weighted training set from `/labels` and call the recompute; this
  PR lands the law, the persisted weights, and the fit seam.
- Follow-up: `test_ml_weighting` (the decay + boundary), `test_project_weighting` (the §9 M5
  weight-decay acceptance + per-condition independence + idempotence), and the `train_quality_ranker`
  / persistence `sample_weight` tests.

## More information

- PRD §7.5 (curation & per-condition ML), §5.1 `/labels`, §11.2 "Cold-start seed weight w₀ / decay
  law"; PLAN §9 M5 "label weighting + cold-start decay".
- Homes the deferred item flagged in [ADR-0034](0034-gradient-boosting-quality-ranker.md) and
  [ADR-0035](0035-portable-model-persistence.md) ("per-label `source` weighting + cold-start decay").
- [Nguyen2019] Nguyen et al., "Multi-label classification via incremental clustering on an evolving
  data stream," *Pattern Recognition* 95 (2019) — the weighted incremental-learning mechanism
  (per-sample weights decay so the model favours newer trusted labels) the decay applies to priors.
