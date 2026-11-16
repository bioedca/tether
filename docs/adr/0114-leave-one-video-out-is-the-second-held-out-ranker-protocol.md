<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0114 — Leave-one-video-out is the second held-out ranker protocol

- **Status:** accepted
- **Date:** 2026-11-16
- **Deciders:** bioedca (item 2 of #548: "build it", comment of 2026-11-14; item 1: "none exist
  yet: withhold", 2026-11-16; the issue approved to start on 2026-11-16)
- **PRD anchor:** §8 NFR-VALID (d), §7.5 (FR-ML precision@k), §9 M5, §9.6, §11.2 (ranker
  success target)
- **Milestone:** M19
- **Issue:** [#548](https://github.com/bioedca/tether/issues/548)

## Context and problem statement

PRD §8 NFR-VALID (d) validates the per-condition ranker by "held-out cross-validation
(prequential / leave-one-video-out, the precision@k protocol of §7.5)". Only the prequential
protocol existed (`tether.ml.prequential.prequential_uplift`). `docs/validation.md` stated the
gap, and PRD §9.6 listed it as not met. On 2026-11-14 the maintainer chose to build the second
protocol and to keep it in the PRD.

Item 1 of #548 asked for the uplift on real labelled videos: curated laboratory projects on the
workstation (#698). On 2026-11-16 the maintainer stated that no curated multi-video Tether
project with accept/reject labels exists yet. No public set can stand in: the four public sets
of #721 carry no curation labels, and the PySMACKS labels of #759 are 52 traces with no videos.

The prequential walk scores each video with a model trained on the videos curated **before**
it. That is the reloaded-model situation of PRD §7.5 and UC3, but it has two limits as a
validation estimate: the first video is never scored, and the result depends on the curation
order. Leave-one-video-out removes both. The question this record answers is what the second
protocol computes, what it shares with the first, and which of the two the M5 ship gate reads.

## Decision drivers

- **The held-out unit is the video.** Traces of one video share a movie, a field of view, a
  laser power and a registration. A split that puts traces of one video on both sides of the fit
  measures memory of the video, not generalization. Cross-validation on grouped or dependent
  data must hold out whole groups, or it under-estimates the prediction error [Roberts2017]; a
  leave-one-participant-out design removed the same leakage in a repeated-measures study, where
  a trial-level split inflated the accuracy from 0.66 to 0.91 [Karbalaie2026].
- **The ship-bar does not become lower** (acceptance criterion of #548). `DEFAULT_SHIP_BAR_PTS`
  stays 10.0, and no gate reads a weaker number than before.
- **Never fabricate.** A video that cannot be tested is reported as skipped, never as a `0`.
- **No change to the ranker or its defaults** (scope of #548).

## Considered options

- **A. Leave-one-video-out as a second function beside the prequential one**, with the same
  folds, the same measurement and the same result type, and the prequential protocol kept as the
  M5 gate.
- **B. Replace the prequential gate with leave-one-video-out.** It scores more videos, but it
  trains on later videos, which a user never has when the model is reloaded on a new video. The
  M5 question of PRD §9 M5 is that reloaded-model one.
- **C. Gate on both** (ship only when both medians clear the bar). Stricter, but it changes the
  M5 ship rule, which is a product decision that #548 does not carry.
- **D. Leave-one-molecule-out or k-fold over molecules.** Rejected by the first driver: it
  leaks the held-out video into its own fit.

## Decision outcome

Chosen: **A**.

- `tether.ml.prequential.leave_one_video_out_uplift(folds, train_score_fn, *, k, ship_bar_pts)`
  scores each video with a scorer fitted on the labeled molecules of **every other** video,
  earlier and later, and never on itself. The prior is the other videos' rows in fold order.
- The measurement is the one the prequential protocol makes, now one shared helper: the
  precision@k of the scorer's ranking minus that of the file-order baseline, per video, with the
  same misshapen-score and non-finite-score refusals.
- A video whose other videos do not carry both accepted and rejected labels is skipped. A set
  where no video can be tested (one video, or no two videos with both classes) raises. Two folds
  with one `movie_id` raise, because that video would train its own scorer.
- Each video's prior holds the same rows whatever the order of the folds; only their order in
  the prior follows the folds. The per-video uplifts are therefore order-independent when the
  trainer does not depend on row order, and the function does not promise more than that.
- Both protocols return `PrequentialResult`. A new field `protocol` names the protocol
  (`"prequential"`, the default, or `"leave-one-video-out"`). The median and the ship-bar are
  the same.
- `tether.project.prequential.ranker_leave_one_video_out_uplift(project, k, ...)` runs it on a
  `.tether` over the same per-video folds and the same gradient-boosting ranker as
  `ranker_prequential_uplift`, for **one condition**: it takes a `condition_id`, and it refuses
  a project whose labelled molecules span several conditions when none is given, because the
  ranker is per condition (PRD §7.5) and pooling would train a video's scorer on other
  conditions.
- **The M5 ship gate stays the prequential median.** Leave-one-video-out is reported beside it
  as the second held-out estimate of oracle (d), at the same bar.
- **The real-data leg is withheld** under the data-gaps rule (PRD §9.1 item 8). No labelled
  multi-video set exists to measure it on, and a synthetic store is not accepted in its place.
  It stays a §9.6 row, tracked by [#1100](https://github.com/bioedca/tether/issues/1100), which
  measures both protocols on the workstation once the maintainer names a curated set.

### Consequences

- Good: oracle (d) now has both protocols the PRD names, and the first curated video is no
  longer left out of every held-out number.
- Good: the prequential path is unchanged in behavior; its tests pass as before. The refactor
  moved its per-video scoring into the helper that both protocols use.
- Trade-off: leave-one-video-out fits one ranker per video, so it costs as many fits as there are
  videos; the prequential walk costs the same. Neither persists a model.
- Trade-off: a caller that builds `PrequentialResult` by position is unchanged; `protocol` is a
  new last field with a default.
- Trade-off: oracle (d) is still evidenced only on synthetic stores. The withheld leg is named in
  PRD §9.6 and `docs/validation.md` and is not counted as met.

## More information

- Code: `src/tether/ml/prequential.py`, `src/tether/project/prequential.py`.
- Tests: `tests/test_ml_prequential.py` (pure core, fake scorers) and
  `tests/test_project_prequential.py` (store-integrated). Both are in the required matrix.
- Related: [ADR-0034](0034-gradient-boosting-quality-ranker.md) (apparent precision@k is not the
  gate), [ADR-0038](0038-provisional-prior-training-fold.md) (evaluation reads human labels
  only), #742, #851, #1100.

### References

- [Roberts2017] Roberts D.R., Bahn V., Ciuti S., et al. "Cross-validation strategies for data
  with temporal, spatial, hierarchical, or phylogenetic structure." *Ecography* 40(8), 913–929
  (2017). [doi:10.1111/ecog.02881](https://doi.org/10.1111/ecog.02881). Found with Consensus,
  query "leave-one-group-out cross-validation for grouped or hierarchical data to avoid
  optimistic bias from correlated samples", 2026-11-16; Scite verify 2026-11-16: no editorial
  notice.
- [Karbalaie2026] Karbalaie A., Abtahi F. & Häger C.K. "Participant-Aware Model Validation for
  Repeated-Measures Data: Comparative Cross-Validation Study." *JMIR AI* (2026).
  [doi:10.2196/87728](https://doi.org/10.2196/87728). Same query and date; Scite verify
  2026-11-16: no editorial notice.
