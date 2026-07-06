# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Store-integrated gradient-boosting quality ranker (PRD §7.5; FR-ML).

Wires the trained scorer (:mod:`tether.ml.gbranker`) to a ``.tether``: it trains on the
project's human-labeled molecules and ranks **every** molecule (labeled and uncurated alike)
by predicted quality, reusing the read-only supervised view
(:func:`tether.project.ranking.ranking_dataset` — the ``/features`` ⋈ ``/labels`` join).
Read-only over the M0-frozen ``/features`` + ``/molecules``: no group, dataset, dtype or field
change, so the ``schema-guard`` freeze holds.

Scope (this PR). Train + rank + **apparent** precision@k. The ranker is trained on all the
project's labeled molecules and the precision@k :func:`ranker_precision_at_k` reports is
measured over *those same* labels — an **in-sample (apparent)** fit diagnostic, deliberately
**not** the ship gate. The honest evaluation — a **prequential**, held-out, median-across-videos
precision@k uplift (PRD §7.5; oracle (d)) — and the model's persistence as a portable
warm-start artifact are each their own later PR. Nothing here persists a model or mutates the
project.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from tether.project.labels import CurationLabel
from tether.project.ranking import ranking_dataset

if TYPE_CHECKING:
    from os import PathLike

    from tether.ml.gbranker import QualityRanker
    from tether.ml.ranking import RankedTraces
    from tether.project.core import Project
    from tether.project.ranking import RankingDataset

    ProjectRef = Project | str | PathLike[str]

__all__ = ["ranker_precision_at_k", "ranker_ranking", "train_ranker"]

_ACCEPT = int(CurationLabel.ACCEPT)


def _project_name(project: ProjectRef) -> str:
    from tether.project.core import Project as _Project

    path = project.path if isinstance(project, _Project) else Path(project)
    return path.name


def _train(data: RankingDataset, name: str) -> QualityRanker:
    """Fit the ranker on a dataset's human-labeled rows (accept = good)."""
    from tether.ml.gbranker import train_quality_ranker

    mask = data.labeled_mask
    if not bool(mask.any()):
        raise ValueError(f"{name} has no human-labeled molecules; cannot train a quality ranker")
    y = data.curation_label[mask] == _ACCEPT
    return train_quality_ranker(data.X[mask], y, data.feature_names)


def train_ranker(project: ProjectRef) -> QualityRanker:
    """Train the gradient-boosting quality ranker on a project's ``/features`` + ``/labels``.

    Fits :func:`tether.ml.gbranker.train_quality_ranker` on the molecules a human has
    accepted/rejected (accept = good). Read-only.

    Raises
    ------
    KeyError
        No ``/features/table`` has been written (run ``compute_features`` first).
    ValueError
        The project has no human-labeled molecules, or only one class is labeled (needs both
        accepted and rejected examples — propagated from the model).
    """
    return _train(ranking_dataset(project), _project_name(project))


def ranker_ranking(project: ProjectRef) -> RankedTraces:
    """Rank **all** the project's molecules by predicted quality — never auto-drop (PRD §7.5).

    Trains on the labeled molecules, then scores and ranks every molecule (labeled *and*
    uncurated), so the result is a permutation of the full candidate set — no molecule is
    dropped, including uncurated ones and any with undefined (``NaN``) features (scored
    natively). Read-only.

    Raises
    ------
    KeyError
        No ``/features/table`` exists.
    ValueError
        No human-labeled molecules, or only one class is labeled.
    """
    data = ranking_dataset(project)
    ranker = _train(data, _project_name(project))
    return ranker.rank(data.molecule_ids, data.X)


def ranker_precision_at_k(project: ProjectRef, k: int) -> float:
    """**Apparent** precision@k of the trained ranker over the project's labeled molecules.

    An **in-sample** fit diagnostic (PRD §7.5): the ranker is trained on the project's labels
    and precision@k is measured over the ranking of *those same* labeled molecules, so it is
    optimistically biased and is **not** the M5 ship gate — the honest held-out **prequential**
    median-across-videos uplift lands in its own later PR (oracle (d)). Compare it against
    :func:`tether.project.ranking.baseline_precision_at_k` for a same-project,
    same-``k`` before/after read. Read-only.

    Raises
    ------
    KeyError
        No ``/features/table`` exists.
    ValueError
        No human-labeled molecules (precision@k is undefined), only one class is labeled, or
        ``k`` is not a positive integer (propagated from :func:`tether.ml.ranking.precision_at_k`).
    """
    from tether.ml.ranking import precision_at_k

    data = ranking_dataset(project)
    ranker = _train(data, _project_name(project))
    ranking = ranker.rank(data.molecule_ids, data.X)
    return precision_at_k(ranking.ranked_relevance(data.is_good), k)
