# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Active-learning "recommended next" **non-reordering** badge (PRD §7.5; FR-ML).

The active-learning query strategy the curation loop surfaces as a *cue*, not a re-queue.
Within a single video's pass the trace order is **fixed once the model pre-sorts on load**
(retrain + re-sort happen only at the video boundary — PRD §7.5); the active-learning loop's
"most informative next" suggestion is shown as a **non-reordering badge** over that fixed
sweep, never a live re-rank (PRD §7.5: "surfaces its 'most informative next' suggestion as a
non-reordering badge (a 'recommended next' cue), not a live re-queue").

The strategy is **uncertainty sampling** [Huellermeier2021]: query the label of the
still-uncurated molecule for which the ranker's prediction is *maximally uncertain* — the one
whose ``P(good)`` sits nearest the ``0.5`` decision boundary, i.e. the one the model is least
sure about and so whose human label is most informative [Cho2024]. For a binary quality
classifier the least-confidence, margin, and Shannon-entropy uncertainty measures are all
monotone in ``|P(good) - 0.5|`` and so induce the **same** "most informative" ordering
[Guochen2021][Hein2022]; this module uses the *margin* form :func:`informativeness`
(``1 - |2p - 1|`` — bounded ``[0, 1]``, no logarithm), maximal at the boundary.

This module is deliberately **model-free and store-free** (pure NumPy): the gradient-boosting
ranker (:mod:`tether.ml.gbranker`) produces the ``P(good)`` scores and the ``.tether`` glue
(:func:`tether.project.active.next_recommendation`) supplies which molecules a human has
already curated; the strategy here just reads scores + a curated mask and names the single
molecule to curate next. It is structurally **non-reordering**: it takes an aligned
``(molecule_ids, scores)`` view and returns a :class:`NextBadge` *annotation* — it never
returns or mutates an order, so the fixed sweep it annotates is untouched by construction.

Never fabricate. Only *uncurated* molecules with a real (finite) score are candidates; a
molecule the model could not score (a ``NaN`` — never a fabricated ``0``, the
:mod:`tether.ml.ranking` discipline) is excluded from the recommendation but never dropped,
and when nothing remains to recommend the badge is :data:`None` rather than an invented pick.

References
----------
[Huellermeier2021] Hüllermeier E. "How to measure uncertainty in uncertainty sampling for
    active learning." Machine Learning (2021) — uncertainty sampling queries the instance whose
    current prediction is maximally uncertain.
[Cho2024] Cho S., et al. "Querying Easily Flip-flopped Samples for Deep Active Learning."
    ArXiv (2024) — an instance's distance to the decision boundary is a natural measure of its
    predictive uncertainty (informativeness).
[Guochen2021] Zhang G. "Four Uncertain Sampling Methods are Superior to Random Sampling Method
    in Classification." ICAIE (2021) — least-confidence / margin / ratio / entropy uncertainty
    sampling all beat random selection.
[Hein2022] Hein A., et al. "A Comparison of Uncertainty Quantification Methods for Active
    Learning in Image Classification." IJCNN (2022) — least-confidence, margin and entropy
    sampling consistently outperform random sampling.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["BOUNDARY", "NextBadge", "informativeness", "recommend_next"]

#: A binary ``P(good)`` classifier's decision boundary — the point of maximal uncertainty.
BOUNDARY = 0.5


def informativeness(scores: object) -> np.ndarray:
    """Uncertainty-sampling informativeness of each ``P(good)`` score: ``1 - |2p - 1|``.

    The *margin* uncertainty measure for a binary classifier: ``1.0`` at the ``p = 0.5``
    decision boundary (maximally uncertain, so a human label there is most informative) and
    falling to ``0.0`` at a confident ``p ∈ {0, 1}``. For a binary label it is monotone in the
    least-confidence and Shannon-entropy measures, so it induces the same "most informative
    next" ordering [Guochen2021][Hein2022]; the margin form is used because it is bounded to
    ``[0, 1]`` and logarithm-free.

    Parameters
    ----------
    scores:
        A 1-D array of ``P(good)`` quality probabilities
        (:meth:`tether.ml.gbranker.QualityRanker.score`), each in ``[0, 1]``. ``NaN`` is allowed
        (an unscored molecule — never a fabricated ``0``)
        and propagates to a ``NaN`` informativeness, so such a molecule is not comparable and is
        never chosen; ``±inf`` and out-of-``[0, 1]`` values are rejected (a probability cannot be
        infinite or outside the unit interval).

    Returns
    -------
    numpy.ndarray
        The ``float64`` informativeness of each score, aligned to ``scores``.

    Raises
    ------
    ValueError
        ``scores`` is not 1-D, or a finite score is infinite or outside ``[0, 1]``.
    """
    p = np.asarray(scores, dtype=np.float64)
    if p.ndim != 1:
        raise ValueError(f"scores must be 1-D, got shape {p.shape}")
    if bool(np.isinf(p).any()):
        raise ValueError("P(good) scores must be finite probabilities in [0, 1] (or NaN)")
    finite = ~np.isnan(p)
    if bool(((p < 0.0) | (p > 1.0))[finite].any()):
        raise ValueError("P(good) scores must be probabilities in [0, 1] (or NaN)")
    return 1.0 - np.abs(2.0 * p - 1.0)  # NaN propagates through np.abs


@dataclass(frozen=True)
class NextBadge:
    """The active-learning "recommended next" cue — a single molecule, not an ordering (PRD §7.5).

    A lightweight annotation naming the one uncurated molecule the loop recommends curating
    next and why. It references a molecule by id, so attaching it to the fixed within-video
    sweep leaves that sweep's order untouched (the **non-reordering** contract).

    Attributes
    ----------
    molecule_id:
        The recommended molecule's ``molecule_id``.
    informativeness:
        Its :func:`informativeness` (``1`` = at the decision boundary, maximally uncertain).
    score:
        Its ``P(good)`` quality score, carried for display alongside the cue.
    """

    molecule_id: str
    informativeness: float
    score: float


def recommend_next(
    molecule_ids: Sequence[str], scores: object, *, curated: object
) -> NextBadge | None:
    """The single most-informative **uncurated** molecule to curate next (PRD §7.5), or ``None``.

    Uncertainty sampling [Huellermeier2021]: among the molecules a human has **not** yet
    curated (``curated[i] is False``) and that carry a real score, return the one whose
    :func:`informativeness` is highest — the ``P(good)`` nearest the ``0.5`` boundary, the
    model's least-confident and so most-informative candidate. Ties (equal informativeness)
    break on the ascending ``molecule_id`` so the recommendation is deterministic across
    platforms (the :mod:`tether.ml.ranking` precedent).

    This is a pure read: it neither returns nor mutates any ordering, so the fixed within-video
    sweep the badge annotates is untouched — the badge is a **non-reordering** cue, not a
    re-queue.

    Parameters
    ----------
    molecule_ids:
        The candidate molecules (must be unique — a recommendation is over a molecule *set*).
    scores:
        Their ``P(good)`` quality scores, aligned to ``molecule_ids`` (:func:`informativeness`
        validates them; ``NaN`` marks an unscored molecule, excluded from the pick but kept).
    curated:
        A 1-D boolean mask aligned to ``molecule_ids``: ``True`` where a human has already
        accepted/rejected the molecule (so it is not a candidate to recommend next).

    Returns
    -------
    NextBadge | None
        The recommended-next molecule, or ``None`` when every molecule is already curated or
        none of the uncurated ones is scoreable — never a fabricated recommendation.

    Raises
    ------
    ValueError
        ``molecule_ids``/``scores``/``curated`` are not aligned 1-D, ``molecule_ids`` are not
        unique, ``curated`` is not boolean, or a score is infinite or outside ``[0, 1]``.
    """
    ids = [str(m) for m in molecule_ids]
    p = np.asarray(scores, dtype=np.float64)
    u = informativeness(p)  # validates 1-D + probabilities/NaN
    if len(ids) != p.shape[0]:
        raise ValueError(f"molecule_ids ({len(ids)}) and scores ({p.shape[0]}) must align")
    if len(set(ids)) != len(ids):
        raise ValueError("molecule_ids must be unique (a recommendation is over a molecule set)")
    mask = np.asarray(curated)
    if mask.dtype != bool or mask.ndim != 1 or mask.shape[0] != len(ids):
        raise ValueError(f"curated must be a 1-D boolean mask aligned to molecule_ids ({len(ids)})")

    candidate = (~mask) & np.isfinite(u)
    if not bool(candidate.any()):
        return None
    # Most informative first (highest u, nearest the boundary); ties break on the ascending id.
    best = min(
        (i for i in range(len(ids)) if bool(candidate[i])),
        key=lambda i: (-float(u[i]), ids[i]),
    )
    return NextBadge(molecule_id=ids[best], informativeness=float(u[best]), score=float(p[best]))
