# 0040 — Active-learning "recommended next" non-reordering badge (uncertainty sampling)

- **Status:** accepted
- **Date:** 2026-07-07
- **Deciders:** bioedca
- **PRD anchor:** §7.5 ("the active-learning loop surfaces its 'most informative next' suggestion as a **non-reordering badge** (a 'recommended next' cue), not a live re-queue; live unseen-tail re-ranking is a deferred opt-in"; "An **active-learning** loop **shall** propose the most informative next traces (surfaced as the non-reordering badge above)"), §4.2 (FR-ML "active learning"), §9 M5 (the M5 acceptance)
- **Milestone:** M5 — the **last** M5 PR before the `v0.5.0` exit ([ADR-0039] closer)

## Context and problem statement

M5's quality ranker pre-sorts a video's traces by predicted quality **once on load** and holds that order fixed for the pass (retrain + re-sort happen only at the video boundary, preserving a predictable sweep — PRD §7.5, [ADR-0034]). Separately, the PRD requires an **active-learning loop** that proposes "the most informative next" trace to curate. The tension the decision must resolve: active learning naturally wants to *reorder* the queue to put its most-informative pick first, but the PRD explicitly forbids that within a video — the suggestion is a **non-reordering badge** ("recommended next" cue), **not** a live re-queue. So the questions are:

1. **What is "most informative"** for Tether's binary (good/bad) quality ranker, computed from what the model already produces?
2. **Which molecules are candidates** to recommend next?
3. **How is the suggestion surfaced** so it cannot reorder the fixed sweep?
4. **What happens when there is nothing to recommend** (or the model can't be trained)?

Getting (3) wrong — letting the active-learning pick bubble to the top of the list — breaks the "fixed once the model pre-sorts on load … preserving a predictable sweep" invariant and disorients the curator mid-pass.

## Decision drivers

- **PRD §7.5 fidelity** — a non-reordering cue, not a live re-queue; the fixed within-video sweep is untouched; live unseen-tail re-ranking stays a deferred opt-in.
- **Reuse what the ranker already emits** — no second model, no new dependency; the cue is a thin read over the existing `P(good)` scores.
- **Established practice** — uncertainty sampling (query the most-uncertain unlabeled instance) is the canonical, well-validated active-learning query strategy [Huellermeier2021][Guochen2021][Hein2022].
- **Never fabricate; never drop** — an unscored molecule is excluded from the pick but kept; when nothing remains the badge is absent, not invented; an untrainable project raises rather than emitting a guess (the [ADR-0003] "withhold, never fabricate" discipline).
- **Schema-guard green** — read-only over the M0-frozen store; no group/dataset/dtype/field change.

## Considered options

- **Informativeness measure:** distance-to-boundary / least-confidence / **margin** / entropy. For a *binary* classifier these are all monotone in `|P(good) − 0.5|` and so induce the **same** "most informative" ordering [Guochen2021][Hein2022]; the **margin** form `1 − |2p − 1|` is chosen because it is bounded to `[0, 1]` and logarithm-free (entropy needs a log and a `0·log 0` guard; raw distance-to-boundary is unbounded in logit space). All name the same molecule: the uncurated one whose `P(good)` sits nearest `0.5`.
- **Candidate pool:** all molecules vs **uncurated only**. Recommending a molecule the human has *already* accepted/rejected wastes the click, so candidates are the **uncurated** molecules (`/molecules.curation_label == UNCURATED` — the trusted human-signal reading the training fold also uses, [ADR-0038]). A molecule carrying only a provisional Deep-LASI / cross-condition seed prior is still uncurated *by a human* and remains a candidate.
- **Surfacing:** live re-queue (reorder the sweep so the pick is first) vs. **non-reordering badge**. The PRD mandates the badge. Modeled as a separate `NextBadge` *annotation* that references a molecule by id; it structurally cannot reorder the sweep it is shown over. `top-k` re-queue and live unseen-tail re-ranking are the PRD's **deferred opt-in** and are not built here.
- **Empty / untrainable:** fabricate a pick vs. **absent badge / raise**. All-curated (nothing left) → `badge = None`; no uncurated molecule is scoreable → `None`; the ranker can't train (no labels, or a single class) → the same loud `ValueError` `ranker_ranking` raises — a recommendation is never fabricated over an untrainable project.

## Decision outcome

Two thin, model-free/read-only layers plus a shared scoring seam:

1. **Pure `tether.ml.active`** — `informativeness(scores) = 1 − |2p − 1|` (uncertainty sampling, maximal at the `0.5` boundary; `NaN` propagates, `±inf`/out-of-`[0,1]` rejected) and `recommend_next(molecule_ids, scores, *, curated) → NextBadge | None`: the single uncurated, finite-scored molecule of maximal informativeness, ties broken on the ascending `molecule_id` (the [ADR-0034]/`rank_by_score` determinism precedent). It takes an aligned scores view and returns an annotation — it never returns or mutates an order, so it is **non-reordering by construction**.
2. **Store `tether.project.active.next_recommendation(project) → ActiveRecommendation`** — trains + scores once via the new shared seam and returns the **fixed sweep unchanged** alongside the badge. `ActiveRecommendation.sweep` is identical to `ranker_ranking`; `.badge` is the cue over it. Read-only.
3. **Shared seam `tether.project.gbranking.score_molecules(project) → ScoredMolecules`** — one project read + one fit producing the per-molecule `P(good)` scores, the fixed quality sweep, and the human-only dataset; `ranker_ranking` (via `_train_and_rank`) now delegates to it, so the badge path adds **no** extra read or fit. Because `QualityRanker.rank(ids, X) == rank_by_score(ids, score(X))`, the delegation is behaviour-preserving and scores only once (previously `rank` re-scored).

### Consequences

- Good: `schema-guard` stays green — read-only over the frozen `/features` + `/molecules` + `/labels`; no group/dataset/dtype/field change. **No conda-lock change** (numpy + the existing sklearn ranker stack) and **no new §11.2 tunable** — the strategy is the single canonical uncertainty-sampling measure and the `0.5` boundary is a binary classifier's intrinsic decision point, not a knob.
- Good: the fixed within-video sweep is provably untouched — the badge is a separate annotation, asserted byte-identical to `ranker_ranking` in the §9 M5 store test.
- Good: never fabricates — unscored molecules excluded (not dropped), absent badge when nothing remains, loud refusal when untrainable.
- Trade-off: a **single** recommended-next molecule (not a ranked short-list) and no live unseen-tail re-ranking — both are the PRD's explicitly **deferred opt-in**; a top-k queue would edge toward the forbidden re-queue.
- Follow-up: the **GUI** that renders the badge in the curation dock (highlighting the recommended row without moving it) is deferred to the M5 GUI PR (computer-use gate), joining the deferred feature/similarity/ranker/drift/merge-reconcile displays; the headless core surfaces the cue it will show. With this, the M5 curation + ML-v1 slice is functionally complete → **M5 exit, tag `v0.5.0`**.

## More information

- PRD §7.5 (curation order + the active-learning non-reordering badge), §4.2 (FR-ML active learning), §9 M5.
- Builds on [ADR-0034] (the gradient-boosting `P(good)` ranker + `rank_by_score` never-auto-drop permutation), [ADR-0036]/[ADR-0038] (the human-signal reading `curation_label` that defines "uncurated"), and closes the M5 sequence opened by [ADR-0037]/[ADR-0038]/[ADR-0039].
- [Huellermeier2021] Hüllermeier, E. "How to measure uncertainty in uncertainty sampling for active learning," *Machine Learning* (2021) — uncertainty sampling queries the instance whose current prediction is maximally uncertain.
- [Cho2024] Cho, S., et al. "Querying Easily Flip-flopped Samples for Deep Active Learning," ArXiv (2024) — an instance's distance to the decision boundary is a natural measure of its predictive uncertainty / informativeness.
- [Guochen2021] Zhang, G. "Four Uncertain Sampling Methods are Superior to Random Sampling Method in Classification," ICAIE (2021) — least-confidence / margin / ratio / entropy uncertainty sampling all beat random selection.
- [Hein2022] Hein, A., et al. "A Comparison of Uncertainty Quantification Methods for Active Learning in Image Classification," IJCNN (2022) — least-confidence, smallest-margin and entropy sampling consistently outperform random sampling.
