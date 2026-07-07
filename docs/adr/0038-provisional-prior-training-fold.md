# 0038 — Fold weighted provisional `/labels` priors into the ranker's training set (human-supersedes, eval-on-truth)

- **Status:** accepted
- **Date:** 2026-07-07
- **Deciders:** bioedca
- **PRD anchor:** §7.5, §11.2 (FR-ML) — "human labels are full weight; `deeplasi-provisional` and `cross-condition-seed` labels are down-weighted cold-start priors whose weight decays toward zero as human labels in the condition accrue"; §9 M5 "seed from Deep-LASI categories / other conditions … Reads `/labels` since M2"
- **Milestone:** M5

## Context and problem statement

[ADR-0036] landed the cold-start decay law (`w = w₀/(1 + n_human)`) and gave `train_quality_ranker` an optional `sample_weight` seam, but it left that seam **inert**: `tether.project.gbranking` still trained the ranker on the **human** `curation_label` only, so the two provisional `/labels` sources (`deeplasi-provisional`, `cross-condition-seed`) — which the codec ([ADR-0023]) and `set_curation_label(source=…, weight=…)` already write to `/labels` — did nothing. Seeding a condition's model (the next M5 concern) is pointless until those provisional rows actually influence the fit. Making the seam live forces three decisions the PRD implies but does not spell out:

1. **What supersedes what** when a molecule carries both a human accept/reject and a provisional prior.
2. **Where the decayed weight comes from** at fit time (the persisted `/labels.weight` column vs. an on-the-fly recompute), given the training path is read-only.
3. **Whether provisional priors count as evaluation ground truth** (apparent / prequential precision@k).

Getting (3) wrong is the subtle trap: if a provisional seed were scored as ground truth, apparent precision@k would measure the model against its own priors — a circular, inflated diagnostic that hides a bad seed.

## Decision drivers

- **PRD §7.5 fidelity** — human labels are full-weight ground truth; provisional priors are down-weighted cold-start guesses that decay as human evidence accrues.
- **Never-auto-drop, never-fabricate** — an uncurated molecule with no prior is still ranked (never dropped); apparent precision@k with no human labels is *undefined*, surfaced loudly, never a fabricated `0`.
- **Read-only / schema-guard green** — training reads `/features` + `/molecules` + `/labels`; it must not mutate the store.
- **One definition of `n_human`** — the training fold and `recompute_label_weights` ([ADR-0036]) must not drift apart.
- **Backward compatibility** — a project with no provisional rows must fit **identically** to the pre-fold model.
- **Standard methodology** — training on down-weighted pseudo-labeled/seed priors while evaluating only on ground truth is established semi-supervised practice [Wang2022, Liu2024].

## Considered options

- **Precedence:** human-supersedes-provisional (a human label wins, weight 1.0, counted once) vs. keep both rows (double-counting the same molecule) vs. provisional-wins. Human-supersedes is the only reading consistent with "human labels are ground truth" and with the decay (a well-curated molecule's stale seed would have a near-zero weight anyway).
- **Weight source at fit time:** read the persisted `/labels.weight` (requires the caller to `recompute_label_weights` first — a footgun if forgotten, and a mutation in the training path) vs. **compute weights on the fly** from the current label set via the same pure `tether.ml.weighting` primitives. On-the-fly is read-only and always-current; because it calls the identical `seed_weight`/`human_counts_by_condition` law that `recompute_label_weights` persists, the trainer's weights and the persisted column agree by construction.
- **Multiple provisional events on one key:** latest-event-wins (append order) vs. first vs. majority. Latest-wins honors the append-only `/labels` event semantics — a re-seed or a provisional *clear* (`label_value = UNCURATED`) supersedes the earlier prior.
- **Evaluation set:** human-only ground truth (rejected the alternative of scoring seeds as truth — it is circular).

## Decision outcome

Chosen: `tether.project.gbranking` gains a **weighted training view** (`weighted_training_set` → `WeightedTrainingSet`) assembled read-only from `/features` ⋈ `/molecules` ⋈ `/labels`:

- Every molecule with a **human** accept/reject is a training row at **weight 1.0** (accept = good); a human label **supersedes** any provisional prior on the same molecule (counted once).
- Every remaining molecule carrying a **provisional** `/labels` accept/reject (the *latest* such event per `molecule_key`; a later `UNCURATED` provisional event clears it) is a training row at its decayed **`seed_weight(n_human, w₀)`**.
- Uncurated molecules with no prior are **omitted from training** but still **ranked** (never dropped).

`train_ranker` / `ranker_ranking` / `ranker_precision_at_k` fit on this set with `sample_weight`, and gain a `w0` knob (default the §11.2 `DEFAULT_SEED_WEIGHT`). An all-unit-weight set (the common human-only project) passes `sample_weight=None`, so the fit is **byte-for-byte the pre-fold model**. Evaluation stays **human-only**: `ranker_precision_at_k` scores against `RankingDataset.is_good` (human labels), and raises when a project has no human labels (apparent precision@k undefined). The per-condition `n_human` computation is extracted from `recompute_label_weights` into a shared `tether.project.weighting.human_counts_by_condition`, so the fold and the persisted-weight recompute share one definition.

### Consequences

- Good: `schema-guard` stays green — read-only over the frozen `/features` + `/molecules` + `/labels`, nothing persisted. No conda-lock change (numpy + the already-locked sklearn `sample_weight` seam). No new §11.2 tunable (`w₀` is the existing "Cold-start seed weight" row).
- Good: seeds can now **bootstrap a condition a human has not curated at all** (both classes among the priors) — the fit and ranking succeed where the human-only path refused — while apparent precision@k there is correctly reported *undefined* rather than fabricated.
- Good: the human-only baseline is provably unchanged (`sample_weight=None` guard + the backward-compat test), so no prior M5 behavior regresses.
- Trade-off: a provisional seed on a molecule with **no** `/features` row cannot train (no feature vector) — it is silently skipped, consistent with "features are required to train"; this is an edge a real seeding pass avoids by seeding featured molecules.
- Follow-up: this is the training-fold half of the M5 "seeding + multi-curator" PR. The **seeding operation** that *generates* the cross-condition provisional rows (drift-guarded by [ADR-0037], k-NN/model-score label transfer) and the multi-curator split-merge are the next M5 PRs; both now have a live training seam to write into.

## More information

- PRD §7.5 (curation & per-condition ML), §11.2 "Cold-start seed weight w₀ / decay law"; PLAN §9 M5.
- Builds directly on [ADR-0036] (the decay law + the inert `sample_weight` seam this PR activates), [ADR-0023] (the `/labels` codec + provisional sources), and [ADR-0034] (the gradient-boosting ranker). Guarded, on the seeding-*operation* PR, by [ADR-0037]'s cross-condition drift advisory.
- [Wang2022] Wang, Wu, Weng, Alsdurf, Wang & Yu, "Debiased Learning from Naturally Imbalanced Pseudo-Labels," *CVPR* (2022) — pseudo-labels adapt a model but are distinguished from ground-truth training labels, which remain the evaluation basis.
- [Liu2024] Liu et al., "Enhanced Semi-Supervised Medical Image Classification Based on Dynamic Sample Reweighting and Pseudo-Label Guided Contrastive Learning," *Mathematics* (2024) — reweighting less-reliable pseudo-labeled samples below trusted labels during training.
