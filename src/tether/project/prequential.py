# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Store-integrated prequential precision@k uplift gate (PRD §7.5, §9 M5; FR-ML).

Wires the pure prequential harness (:mod:`tether.ml.prequential`) to a ``.tether``: it groups
the project's human-labeled molecules into per-video folds by ``movie_id`` (in curation /
store order) and runs the interleaved test-then-train protocol, training the gradient-boosting
ranker (:func:`tether.ml.gbranker.train_quality_ranker`) on the accumulated prior at each video
boundary. The result is the **honest, held-out** M5 ship metric — the median across videos of
each video's precision@k uplift over file order — the counterpart to the optimistic in-sample
``ranker_precision_at_k`` (:mod:`tether.project.gbranking`).

Read-only over the M0-frozen ``/features`` + ``/molecules`` (the ``/features`` ⋈ ``/labels``
join of :func:`tether.project.ranking.ranking_dataset`, plus each molecule's ``movie_id``): no
group, dataset, dtype or field change, so the ``schema-guard`` freeze holds, and nothing is
persisted or mutated.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from tether.ml.prequential import (
    DEFAULT_SHIP_BAR_PTS,
    VideoFold,
    prequential_uplift,
)
from tether.project.ranking import ranking_dataset

if TYPE_CHECKING:
    from os import PathLike

    from tether.ml.gbranker import RankerHyperparams
    from tether.ml.prequential import PrequentialResult
    from tether.project.core import Project

    ProjectRef = Project | str | PathLike[str]

__all__ = ["prequential_folds", "ranker_prequential_uplift"]


def _project_path(project: ProjectRef) -> Path:
    from tether.project.core import Project as _Project

    return project.path if isinstance(project, _Project) else Path(project)


def _to_str(value: object) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _folds_and_feature_names(
    project: ProjectRef,
) -> tuple[list[VideoFold], tuple[str, ...]]:
    """Group the project's labeled molecules into ordered per-video folds by ``movie_id``.

    Reads the ``/features`` ⋈ ``/labels`` supervised view (:func:`ranking_dataset`) and each
    molecule's ``movie_id`` (``/molecules``), then buckets the **human-labeled** molecules into
    one :class:`~tether.ml.prequential.VideoFold` per movie, preserving store order both across
    movies (first-appearance = curation order) and within a movie (the file-order baseline
    order). Uncurated molecules carry no ground truth and so are not fold members.
    """
    from tether.imaging.extract import read_molecules

    data = ranking_dataset(project)
    is_good = data.is_good  # labeled molecule_id -> accepted?

    molecules = read_molecules(_project_path(project))
    movie_by_id = {
        _to_str(mid): _to_str(movie)
        for mid, movie in zip(molecules["molecule_id"], molecules["movie_id"], strict=True)
    }

    # Bucket labeled rows by movie, preserving the dataset's store order in both the movie
    # sequence (dict insertion order) and each movie's molecule list.
    rows_by_movie: dict[str, list[int]] = {}
    for i, mid in enumerate(data.molecule_ids):
        if mid not in is_good:
            continue
        try:
            movie = movie_by_id[mid]
        except KeyError:  # a featured+labeled molecule with no /molecules row is a broken store
            raise ValueError(
                f"molecule_id {mid!r} has a feature/label row but no /molecules movie_id"
            ) from None
        rows_by_movie.setdefault(movie, []).append(i)

    folds = [
        VideoFold(
            movie_id=movie,
            molecule_ids=tuple(data.molecule_ids[i] for i in idxs),
            X=data.X[idxs],
            is_good=tuple(is_good[data.molecule_ids[i]] for i in idxs),
        )
        for movie, idxs in rows_by_movie.items()
    ]
    return folds, data.feature_names


def prequential_folds(project: ProjectRef) -> list[VideoFold]:
    """The project's human-labeled molecules as ordered per-video folds (PRD §7.5).

    The evaluation units the prequential gate consumes: one
    :class:`~tether.ml.prequential.VideoFold` per ``movie_id`` that has labeled molecules, in
    curation (store) order. Read-only.

    Raises
    ------
    KeyError
        No ``/features/table`` has been written (run ``compute_features`` first).
    """
    return _folds_and_feature_names(project)[0]


def ranker_prequential_uplift(
    project: ProjectRef,
    k: int,
    *,
    ship_bar_pts: float = DEFAULT_SHIP_BAR_PTS,
    hyperparams: RankerHyperparams | None = None,
) -> PrequentialResult:
    """Prequential, held-out precision@k uplift of the ranker across a project's videos (§9 M5).

    Trains the gradient-boosting quality ranker (:func:`tether.ml.gbranker.train_quality_ranker`)
    on the accumulated prior at each video boundary and measures the median-across-videos
    precision@k uplift over file order — the M5 ship gate (PRD §9 M5, §11.2), and the honest
    counterpart to :func:`tether.project.gbranking.ranker_precision_at_k` (which is in-sample).
    Read-only; trains no persisted model.

    Parameters
    ----------
    project:
        A :class:`~tether.project.core.Project` or a path to a ``.tether`` store.
    k:
        The review-budget cutoff for precision@k (a positive integer; PRD §7.5).
    ship_bar_pts:
        The percentage-point median-uplift threshold to ship (default
        :data:`tether.ml.prequential.DEFAULT_SHIP_BAR_PTS`, the PRD §11.2 value).
    hyperparams:
        Override the ranker's :data:`tether.ml.gbranker.DEFAULT_HYPERPARAMS` (PRD §11.2).

    Returns
    -------
    PrequentialResult
        The per-video uplifts, the skipped videos, and the median-vs-ship-bar verdict.

    Raises
    ------
    KeyError
        No ``/features/table`` exists.
    ValueError
        ``k`` is not a positive integer; the project has no labeled molecules; or no video could
        be held-out evaluated (fewer than two videos whose accumulated prior carries both
        classes — the median uplift is undefined, surfaced loudly).
    """
    from tether.ml.gbranker import train_quality_ranker

    folds, feature_names = _folds_and_feature_names(project)

    def train_score_fn(x_prior: np.ndarray, y_prior: np.ndarray):
        return train_quality_ranker(x_prior, y_prior, feature_names, hyperparams=hyperparams).score

    return prequential_uplift(folds, train_score_fn, k=k, ship_bar_pts=ship_bar_pts)
