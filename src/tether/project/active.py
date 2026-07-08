# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Store-integrated active-learning "recommended next" badge (PRD §7.5; FR-ML).

Wires the pure uncertainty-sampling strategy (:mod:`tether.ml.active`) to a ``.tether``: it
trains the per-condition quality ranker once (:func:`tether.project.gbranking.score_molecules`
— human labels + decayed provisional priors), scores every molecule, and names the single
**uncurated** molecule the active-learning loop recommends curating next — the one whose
``P(good)`` sits nearest the ``0.5`` decision boundary (maximally uncertain, most informative).

The recommendation is returned **alongside the fixed within-video sweep, which it never
reorders** (PRD §7.5: the "most informative next" suggestion is a *non-reordering badge*, not a
live re-queue). :class:`ActiveRecommendation` carries both the sweep (verbatim from
:func:`~tether.project.gbranking.ranker_ranking`) and the badge, making the non-reordering
contract explicit: the sweep object is the same never-auto-drop quality ordering the ranker
produces; the badge is a cue over it.

A molecule counts as *uncurated* when its ``/molecules.curation_label`` is ``UNCURATED`` (no
human accept/reject) — the trusted human-signal reading (:mod:`tether.project.labels`), the
same field the training fold reads (ADR-0038). A molecule carrying only a provisional
Deep-LASI / cross-condition seed prior is still uncurated *by a human* and so remains a valid
recommendation candidate.

Read-only over the M0-frozen ``/features`` + ``/molecules`` + ``/labels``: no group, dataset,
dtype or field change, so the ``schema-guard`` freeze holds. Training requires both an accepted
and a rejected example (human or provisional); a project that cannot train a ranker raises
loudly rather than fabricating a recommendation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from tether.ml.active import NextBadge, recommend_next
from tether.ml.weighting import DEFAULT_SEED_WEIGHT
from tether.project.gbranking import score_molecules

if TYPE_CHECKING:
    from os import PathLike

    from tether.ml.ranking import RankedTraces
    from tether.project.core import Project

    ProjectRef = Project | str | PathLike[str]

__all__ = ["ActiveRecommendation", "next_recommendation"]


# ``eq=False`` -> identity equality/hash (the ScoredMolecules / RankingDataset precedent): the
# ``sweep`` holds a RankedTraces whose numpy-backed scores a generated ``__eq__`` could not compare.
@dataclass(frozen=True, eq=False)
class ActiveRecommendation:
    """The fixed within-video sweep + the active-learning "recommended next" badge (PRD §7.5).

    Attributes
    ----------
    sweep:
        The quality-pre-sorted, never-auto-drop within-video order
        (:class:`~tether.ml.ranking.RankedTraces`) — returned **unchanged** (identical to
        :func:`tether.project.gbranking.ranker_ranking`); the badge is a non-reordering cue over
        it, never a re-queue.
    badge:
        The single uncurated molecule to curate next
        (:class:`~tether.ml.active.NextBadge`), or ``None`` when every molecule is already
        curated (nothing left to recommend — never a fabricated pick).
    """

    sweep: RankedTraces
    badge: NextBadge | None

    @property
    def recommended_id(self) -> str | None:
        """The recommended-next ``molecule_id``, or ``None`` when there is no recommendation."""
        return None if self.badge is None else self.badge.molecule_id


def next_recommendation(
    project: ProjectRef, *, w0: float = DEFAULT_SEED_WEIGHT
) -> ActiveRecommendation:
    """The active-learning "recommended next" badge over a project, with the fixed sweep (PRD §7.5).

    Trains the per-condition ranker on the project's ``/features`` + ``/labels`` (human labels
    plus the decayed provisional priors), scores every molecule, and returns the fixed quality
    sweep together with the single **uncurated** molecule of maximal predictive uncertainty
    (``P(good)`` nearest ``0.5``) as the non-reordering badge
    (:func:`tether.ml.active.recommend_next`). The sweep is never reordered by the badge. Read-only.

    Parameters
    ----------
    project:
        A :class:`~tether.project.core.Project` or a path to a ``.tether`` store.
    w0:
        The cold-start seed weight ``w₀`` provisional priors decay from
        (default :data:`tether.ml.weighting.DEFAULT_SEED_WEIGHT`, the PRD §11.2 tunable).

    Returns
    -------
    ActiveRecommendation
        The fixed sweep and the recommended-next badge (``badge`` is ``None`` when every
        molecule is already curated).

    Raises
    ------
    KeyError
        No ``/features/table`` has been written (run ``compute_features`` first).
    ValueError
        The project has no labeled molecules (human or provisional), or only one class is
        labeled (needs both an accepted and a rejected example — propagated from the model): a
        recommendation is never fabricated over an untrainable project.
    """
    scored = score_molecules(project, w0=w0)
    data = scored.dataset
    # Uncurated = no human accept/reject (curation_label == UNCURATED); labeled_mask is its
    # complement. A provisional-only molecule is uncurated by a human -> still a candidate.
    badge = recommend_next(data.molecule_ids, scored.scores, curated=data.labeled_mask)
    return ActiveRecommendation(sweep=scored.sweep, badge=badge)
