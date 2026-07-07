# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Prequential precision@k uplift — the M5 ranker ship gate (PRD §7.5, §9 M5; oracle (d)).

The **honest, held-out** evaluation of the per-condition quality ranker (PRD §7.5), as
opposed to the optimistic in-sample "apparent" precision@k
(:func:`tether.project.gbranking.ranker_precision_at_k`). It answers the M5 question the
apparent number cannot: *does the ranker, reloaded onto a video it has never seen, actually
surface good traces earlier than plain file order?*

Protocol — prequential (interleaved test-then-train). The videos of a condition are processed
in curation order. For each video in turn the model **trained only on the videos already
curated** scores that video's traces (the *test*), then the video's own labels fold into the
training set (the *train*) before the next video — exactly the reloaded-model, video-by-video
warm-start of PRD §7.5 / UC3. Each video therefore contributes a precision@k **uplift** — the
ranker's precision@k on that video minus the file-/extraction-order baseline's
(:func:`tether.ml.ranking.file_order_ranking`) — measured on data the model had not yet seen.
The **first** curated video has no prior model and so cannot be a held-out test; it is
*reported as skipped*, never scored against nothing (never a fabricated ``0`` uplift).

Ship-bar. The M5 gate (PRD §9 M5, §11.2) is the **median** per-video uplift across the
held-out videos: ship when it clears :data:`DEFAULT_SHIP_BAR_PTS` percentage points. The
median (not the mean) is the robust aggregate PRD §11.2 specifies — one pathological video
cannot sink or inflate the verdict.

This is the standard evaluation methodology for an incrementally-updated / online-learning
predictor, where the honest question is predictive-sequential (each new instance is predicted
before it is learned from) rather than a single in-sample fit [Vinagre2021][GonzalezHidalgo2019].
A per-condition ranker sees no concept drift *within* a condition (the experimental condition
is fixed), so the prior is the **expanding window** of all previously-curated videos (the
"Basic Window" prequential variant [GonzalezHidalgo2019]) — mirroring the warm-start model that
only ever accumulates labels.

The harness is **model-free**: it takes an ordered list of :class:`VideoFold` and a
``train_score_fn`` callback that fits a scorer on a prior's ``(X, y)`` and returns a
``X -> scores`` callable, so the metric and the interleaving never import scikit-learn. The
store-integrated entry point that wires the gradient-boosting ranker is
:func:`tether.project.prequential.ranker_prequential_uplift`.

Never-auto-drop (PRD §7.5). Every per-video ranking is a
:func:`tether.ml.ranking.rank_by_score` permutation — no molecule is dropped; precision@k is
read off the labeled molecules' positions, exactly as the file-order baseline is.

References
----------
[Vinagre2021] Vinagre J., Jorge A.M., Rocha C. & Gama J. "Statistically Robust Evaluation of
    Stream-Based Recommender Systems." IEEE TKDE (2021) — prequential evaluation as the
    standard, statistically-grounded protocol for incrementally-updated models.
[GonzalezHidalgo2019] González Hidalgo J.I., et al. "Experimenting with prequential variations
    for data stream learning evaluation." Computational Intelligence 35(4) (2019) — the
    prequential (predictive-sequential) methodology and its Basic/Sliding-Window variants.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from tether.ml.ranking import file_order_ranking, precision_at_k, rank_by_score

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

__all__ = [
    "DEFAULT_SHIP_BAR_PTS",
    "PrequentialResult",
    "VideoFold",
    "VideoUplift",
    "prequential_uplift",
]

#: The default M5 ship-bar: a ≥ 10-percentage-point median precision@k uplift over file order
#: (PRD §11.2 "Ranker success target (M5)"; §7.5, §9 M5). A tunable, surfaced here as the
#: single source of the default — not a new tunable (the §11.2 row is authoritative).
DEFAULT_SHIP_BAR_PTS = 10.0

#: A scorer fitted on a prior's ``(X, y)``: maps an ``(n, n_features)`` matrix to ``n`` scores
#: (higher = better quality). The seam that keeps this module free of the model's dependency.
if TYPE_CHECKING:
    TrainScoreFn = Callable[[np.ndarray, np.ndarray], Callable[[np.ndarray], np.ndarray]]


def _is_positive_int(k: object) -> bool:
    """Whether ``k`` is a positive integer and not a ``bool``.

    The positive-cutoff rule of :mod:`tether.ml.ranking`, re-checked so a bad ``k`` fails fast.
    """
    return isinstance(k, (int, np.integer)) and not isinstance(k, bool) and int(k) > 0


# ``eq=False`` -> identity equality/hash: this value object holds a numpy ``X`` whose dataclass
# ``==`` would be elementwise (ambiguous truth) and whose ``__hash__`` would hash an unhashable
# array (the :mod:`tether.project.ranking` precedent). Tests compare the fields directly.
@dataclass(frozen=True, eq=False)
class VideoFold:
    """One video's held-out evaluation unit: its **labeled** molecules, features, and truth.

    ``molecule_ids`` is the video's labeled molecules in **file/extraction (store) order** — the
    order the file-order baseline curates them in — and ``X`` (``(n_labeled, n_features)``,
    ``float64``) / ``is_good`` (accept = ``True``) are aligned to it row-for-row. A fold holds
    only molecules a human has labeled, because a held-out precision@k needs ground truth;
    uncurated molecules of the same video carry no truth and so are not evaluation units (they
    remain ranking candidates elsewhere — never dropped, just not scored here).
    """

    movie_id: str
    molecule_ids: tuple[str, ...]
    X: np.ndarray
    is_good: tuple[bool, ...]

    @property
    def n(self) -> int:
        """The number of labeled molecules in this fold."""
        return len(self.molecule_ids)


@dataclass(frozen=True)
class VideoUplift:
    """The held-out precision@k uplift a single video contributed (a fraction, not points).

    ``ranker`` and ``baseline`` are the trained ranker's and the file-order baseline's
    precision@k on this video's labeled molecules (evaluated at ``min(k, n)``); ``uplift`` is
    their difference (``ranker - baseline``), the video's contribution to the median ship
    metric. Multiply a fraction by 100 for the PRD §11.2 percentage-point scale.
    """

    movie_id: str
    n_labeled: int
    ranker: float
    baseline: float
    uplift: float


@dataclass(frozen=True)
class PrequentialResult:
    """The prequential ship-gate verdict: per-video uplifts, their median, and pass/fail.

    ``per_video`` are the uplifts of the videos that *could* be held-out tested (a prior model
    existed and was trainable); ``skipped_movie_ids`` are the videos that could not be — the
    first curated video, and any whose only prior labels were a single class — reported so the
    coverage is never silently narrowed. The gate ships when :attr:`median_uplift_pts` clears
    :attr:`ship_bar_pts`.
    """

    k: int
    ship_bar_pts: float
    per_video: tuple[VideoUplift, ...]
    skipped_movie_ids: tuple[str, ...]

    @property
    def n_evaluated(self) -> int:
        """The number of held-out videos that contributed an uplift."""
        return len(self.per_video)

    @property
    def median_uplift_pts(self) -> float:
        """The median per-video precision@k uplift, in **percentage points** (the ship metric)."""
        return float(np.median([v.uplift for v in self.per_video]) * 100.0)

    @property
    def shipped(self) -> bool:
        """Whether the median uplift clears the ship-bar (PRD §9 M5, §11.2)."""
        return self.median_uplift_pts >= self.ship_bar_pts


def prequential_uplift(
    folds: Sequence[VideoFold],
    train_score_fn: TrainScoreFn,
    *,
    k: int,
    ship_bar_pts: float = DEFAULT_SHIP_BAR_PTS,
) -> PrequentialResult:
    """Prequential (interleaved test-then-train) precision@k uplift over ordered video folds.

    Walks ``folds`` in curation order. For each video, if the videos already seen supply a
    trainable prior (a non-empty ``(X, y)`` with **both** accepted and rejected labels), fits a
    scorer on that prior via ``train_score_fn``, scores the current video, and records the
    precision@k uplift of the ranker's ordering over the file-order baseline
    (:func:`tether.ml.ranking.file_order_ranking`) — a *held-out* measurement, taken before the
    video's labels join the prior. A video with no trainable prior is reported in
    ``skipped_movie_ids`` (never fabricated as a ``0`` uplift). See the module docstring.

    Parameters
    ----------
    folds:
        The condition's videos in curation order (:func:`VideoFold`), each carrying its labeled
        molecules, features, and accept/reject truth.
    train_score_fn:
        ``(X_prior, y_prior) -> (X -> scores)``: fits a quality scorer on the accumulated prior
        and returns a callable that scores a feature matrix (higher = better). Called only when
        the prior has both classes.
    k:
        The review-budget cutoff for precision@k (a positive integer; PRD §7.5 ``k`` ≈ 20–50).
    ship_bar_pts:
        The percentage-point median-uplift threshold to ship (default
        :data:`DEFAULT_SHIP_BAR_PTS`, the PRD §11.2 value).

    Returns
    -------
    PrequentialResult
        The per-video uplifts, the skipped videos, and the median-vs-ship-bar verdict.

    Raises
    ------
    ValueError
        ``k`` is not a positive integer; ``folds`` is empty; a fitted scorer returns a
        misshapen score vector; or **no** video could be held-out evaluated (the median uplift
        is undefined — surfaced loudly, never a fabricated ``0``/``NaN``).
    """
    if not _is_positive_int(k):
        raise ValueError(f"k must be a positive integer, got {k!r}")
    folds = list(folds)
    if not folds:
        raise ValueError("no video folds to evaluate (a prequential gate needs >= 1 video)")

    prior_x_blocks: list[np.ndarray] = []
    prior_y_blocks: list[np.ndarray] = []
    per_video: list[VideoUplift] = []
    skipped: list[str] = []

    for fold in folds:
        prior_y = np.concatenate(prior_y_blocks) if prior_y_blocks else np.empty(0, dtype=bool)
        # A held-out test needs a prior model, which needs both classes to be discriminative.
        if prior_y.size and int(np.unique(prior_y).size) >= 2:
            prior_x = np.concatenate(prior_x_blocks, axis=0)
            scorer = train_score_fn(prior_x, prior_y)
            scores = np.asarray(scorer(fold.X), dtype=np.float64)
            if scores.shape != (fold.n,):
                raise ValueError(
                    f"train_score_fn scorer returned {scores.shape} scores for video "
                    f"{fold.movie_id!r} with {fold.n} molecules; they must align"
                )
            is_good = dict(zip(fold.molecule_ids, fold.is_good, strict=True))
            candidate = rank_by_score(fold.molecule_ids, scores)
            baseline = file_order_ranking(fold.molecule_ids)
            ranker_p = precision_at_k(candidate.ranked_relevance(is_good), k)
            baseline_p = precision_at_k(baseline.ranked_relevance(is_good), k)
            per_video.append(
                VideoUplift(
                    movie_id=fold.movie_id,
                    n_labeled=fold.n,
                    ranker=ranker_p,
                    baseline=baseline_p,
                    uplift=ranker_p - baseline_p,
                )
            )
        else:
            skipped.append(fold.movie_id)
        # Prequential: the video's own labels fold into the prior only AFTER it was scored.
        prior_x_blocks.append(np.asarray(fold.X, dtype=np.float64))
        prior_y_blocks.append(np.asarray(fold.is_good, dtype=bool))

    if not per_video:
        raise ValueError(
            "no held-out video had a trainable prior (need >= 2 videos whose accumulated prior "
            "carries both accepted and rejected labels); prequential uplift is undefined"
        )
    return PrequentialResult(
        k=int(k),
        ship_bar_pts=float(ship_bar_pts),
        per_video=tuple(per_video),
        skipped_movie_ids=tuple(skipped),
    )
