# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Store-integrated gradient-boosting quality ranker (PRD §7.5; FR-ML).

Wires the trained scorer (:mod:`tether.ml.gbranker`) to a ``.tether``: it trains on the project's
human accept/reject labels **plus** the down-weighted provisional ``/labels`` priors (the §7.5
cold-start seeding seam), and ranks **every** molecule (labeled and uncurated alike) by predicted
quality. It reuses the read-only human-only supervised view
(:func:`tether.project.ranking.ranking_dataset`) for ranking + evaluation and adds the weighted
training view (:func:`weighted_training_set`) for the fit. Read-only over the M0-frozen
``/features`` + ``/molecules`` + ``/labels``: no group, dataset, dtype or field change, so the
``schema-guard`` freeze holds.

Scope. Train + rank + **apparent** precision@k. The ranker trains on the project's human
accept/reject labels **plus** any provisional ``/labels`` priors (Deep-LASI / cross-condition seeds)
folded in at their §7.5 cold-start-decayed ``sample_weight`` — ``w = w₀/(1 + n_human)``,
:mod:`tether.ml.weighting` — with a human label superseding a provisional prior on the same
molecule; the **apparent** precision@k :func:`ranker_precision_at_k` reports is still measured over
the **human** labels only (the seeds train the model but are never scored as ground truth) — an
in-sample diagnostic, deliberately **not** the ship gate. The honest held-out **prequential**
median-across-videos uplift (PRD §7.5; oracle (d)) and the model's persistence as a portable
warm-start artifact are each their own PR. Read-only: nothing here persists a model or mutates the
project.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from tether.ml.weighting import DEFAULT_SEED_WEIGHT, seed_weight
from tether.project.labels import HUMAN_WEIGHT, LABEL_SOURCE_HUMAN, CurationLabel
from tether.project.ranking import ranking_dataset
from tether.project.weighting import human_counts_by_condition

if TYPE_CHECKING:
    from os import PathLike

    from tether.ml.gbranker import QualityRanker
    from tether.ml.ranking import RankedTraces
    from tether.project.core import Project
    from tether.project.ranking import RankingDataset

    ProjectRef = Project | str | PathLike[str]

__all__ = [
    "WeightedTrainingSet",
    "ranker_precision_at_k",
    "ranker_ranking",
    "train_ranker",
    "weighted_training_set",
]

_ACCEPT = int(CurationLabel.ACCEPT)
_UNCURATED = int(CurationLabel.UNCURATED)


def _project_path(project: ProjectRef) -> Path:
    from tether.project.core import Project as _Project  # noqa: PLC0415

    return project.path if isinstance(project, _Project) else Path(project)


def _project_name(project: ProjectRef) -> str:
    return _project_path(project).name


def _to_str(value: object) -> str:
    """Decode an h5py variable-length string field (``bytes`` or ``str``)."""
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _latest_provisional_labels(labels_rows: np.ndarray) -> dict[str, int]:
    """``molecule_key -> latest provisional accept/reject`` over the ``/labels`` log (PRD §7.5).

    Scans the append-only ``/labels`` history in order and, for the **non-human** sources
    (``deeplasi-provisional`` / ``cross-condition-seed``), keeps each ``molecule_key``'s *most
    recent* ``label_value`` — a later provisional event supersedes an earlier one (a re-seed, or a
    provisional clear to ``UNCURATED``). Keys whose latest provisional state is ``UNCURATED`` are
    dropped, so the result is exactly the keys currently carrying a provisional accept/reject prior.
    Human rows are ignored here: a human decision lives on ``/molecules.curation_label`` and takes
    priority in the training-fold (:func:`weighted_training_set`), so it never reaches this overlay.
    """
    state: dict[str, int] = {}
    for row in labels_rows:
        if _to_str(row["source"]) == LABEL_SOURCE_HUMAN:
            continue
        state[_to_str(row["molecule_key"])] = int(row["label_value"])
    return {key: value for key, value in state.items() if value != _UNCURATED}


# ``eq=False`` -> identity equality/hash (the RankingDataset / QualityRanker precedent): this value
# object holds numpy arrays a dataclass-generated ``__eq__``/``__hash__`` could not compare/hash.
@dataclass(frozen=True, eq=False)
class WeightedTrainingSet:
    """The ranker's **training** view: human + down-weighted provisional labels (PRD §7.5).

    The supervised set the gradient-boosting ranker fits on — a superset of the human labels that
    also folds in the two provisional ``/labels`` priors (Deep-LASI / cross-condition seeds), each
    at its cold-start-decayed weight (:mod:`tether.ml.weighting`). Distinct from the human-only
    :class:`tether.project.ranking.RankingDataset`, which stays the **evaluation** ground truth:
    apparent/prequential precision@k score against the human labels alone, never the provisional
    priors (the seeds train the model but are never counted as truth).

    Attributes
    ----------
    molecule_ids:
        The ``molecule_id`` of each training row (a labeled molecule). An uncurated molecule with no
        prior is not a training row, though it is still *ranked* (never dropped, PRD §7.5).
    feature_names:
        The feature-column order (:data:`tether.ml.features.FEATURE_NAMES`).
    X:
        ``(n_train, n_features)`` ``float64`` feature matrix of the training rows (``NaN`` allowed).
    y:
        ``(n_train,)`` boolean accept (``True`` = good) / reject label.
    sample_weight:
        ``(n_train,)`` ``float64`` per-row training weight — ``1.0`` for a human label, the decayed
        ``w₀/(1 + n_human)`` for a provisional prior (:mod:`tether.ml.weighting`).
    """

    molecule_ids: list[str]
    feature_names: tuple[str, ...]
    X: np.ndarray
    y: np.ndarray
    sample_weight: np.ndarray

    @property
    def n_train(self) -> int:
        return len(self.molecule_ids)

    @property
    def n_good(self) -> int:
        return int(np.count_nonzero(self.y))


def _prepare(project: ProjectRef, w0: float) -> tuple[WeightedTrainingSet, RankingDataset]:
    """Build the weighted training set + the human-only ranking dataset from one project read.

    Returns both so a caller that also ranks/evaluates (:func:`_train_and_rank`) reuses the single
    ``/features`` read rather than assembling it twice. Read-only.
    """
    from tether.imaging.extract import read_molecules  # noqa: PLC0415
    from tether.project.labels import read_labels  # noqa: PLC0415

    data = ranking_dataset(project)
    path = _project_path(project)
    molecules = read_molecules(path)
    n_human_by_condition = human_counts_by_condition(
        molecules["condition_id"], molecules["curation_label"]
    )
    condition_by_key = {
        _to_str(key): _to_str(cond)
        for key, cond in zip(molecules["molecule_key"], molecules["condition_id"], strict=True)
    }
    provisional_by_key = _latest_provisional_labels(read_labels(path))

    idx: list[int] = []
    labels: list[bool] = []
    weights: list[float] = []
    for i, key in enumerate(data.molecule_keys):
        human = int(data.curation_label[i])
        if human != _UNCURATED:
            # A human accept/reject is authoritative and supersedes any provisional prior on the
            # same molecule (a seed is only a cold-start guess): full weight, never double-counted.
            labels.append(human == _ACCEPT)
            weights.append(HUMAN_WEIGHT)
        elif key in provisional_by_key:
            labels.append(provisional_by_key[key] == _ACCEPT)
            n_human = n_human_by_condition.get(condition_by_key.get(key, ""), 0)
            weights.append(seed_weight(n_human, w0=w0))
        else:
            continue  # uncurated candidate, no prior -> ranked but not a training row
        idx.append(i)

    training = WeightedTrainingSet(
        molecule_ids=[data.molecule_ids[i] for i in idx],
        feature_names=data.feature_names,
        X=data.X[idx],
        y=np.asarray(labels, dtype=bool),
        sample_weight=np.asarray(weights, dtype=np.float64),
    )
    return training, data


def weighted_training_set(
    project: ProjectRef, *, w0: float = DEFAULT_SEED_WEIGHT
) -> WeightedTrainingSet:
    """Assemble the ranker's weighted training set from a project's ``/features`` + ``/labels``.

    Every molecule carrying a human accept/reject (full weight) **or** a provisional ``/labels``
    prior (Deep-LASI / cross-condition seed, at its ``w₀/(1 + n_human)`` decayed weight) becomes a
    training row; a human label supersedes a provisional prior on the same molecule. Uncurated
    molecules with no prior are omitted from *training* (they are still ranked, never dropped —
    PRD §7.5). Read-only over the M0-frozen ``/features`` + ``/molecules`` + ``/labels``.

    Parameters
    ----------
    project:
        A :class:`~tether.project.core.Project` or a path to a ``.tether`` store.
    w0:
        The cold-start seed weight ``w₀`` (default :data:`tether.ml.weighting.DEFAULT_SEED_WEIGHT`,
        the PRD §11.2 tunable) provisional priors decay from.

    Raises
    ------
    KeyError
        No ``/features/table`` has been written (run ``compute_features`` first).
    """
    return _prepare(project, w0)[0]


def _fit(training: WeightedTrainingSet, name: str) -> QualityRanker:
    """Fit the ranker on a weighted training set (accept = good), refusing an empty set loudly."""
    from tether.ml.gbranker import train_quality_ranker  # noqa: PLC0415

    if training.n_train == 0:
        raise ValueError(
            f"{name} has no labeled molecules (human or provisional seed); cannot train a "
            "quality ranker"
        )
    # An all-unit-weight set (the common human-only project with no priors) fits identically to the
    # unweighted model — pass sample_weight=None so behaviour is unchanged when nothing is seeded.
    weights = (
        None if bool(np.all(training.sample_weight == HUMAN_WEIGHT)) else training.sample_weight
    )
    return train_quality_ranker(
        training.X, training.y, training.feature_names, sample_weight=weights
    )


def _train_and_rank(
    project: ProjectRef, w0: float = DEFAULT_SEED_WEIGHT
) -> tuple[RankedTraces, RankingDataset]:
    """Train the ranker once and rank every molecule — shared by the ranking entry points.

    Builds the weighted training set (human + decayed provisional priors), fits the model **once**,
    and ranks all molecules, returning both the ranking and the human-only dataset so a caller that
    also needs the evaluation labels (:func:`ranker_precision_at_k`) reuses this single fit.
    """
    training, data = _prepare(project, w0)
    ranker = _fit(training, _project_name(project))
    return ranker.rank(data.molecule_ids, data.X), data


def train_ranker(project: ProjectRef, *, w0: float = DEFAULT_SEED_WEIGHT) -> QualityRanker:
    """Train the gradient-boosting quality ranker on a project's ``/features`` + ``/labels``.

    Fits :func:`tether.ml.gbranker.train_quality_ranker` on the molecules a human has
    accepted/rejected (full weight, accept = good) **plus** any provisional ``/labels`` priors
    (Deep-LASI / cross-condition seeds) folded in at their §7.5 cold-start-decayed weight
    (``w = w₀/(1 + n_human)``); a human label supersedes a provisional prior on the same molecule.
    Read-only.

    Parameters
    ----------
    project:
        A :class:`~tether.project.core.Project` or a path to a ``.tether`` store.
    w0:
        The seed weight ``w₀`` provisional priors decay from (PRD §11.2).

    Raises
    ------
    KeyError
        No ``/features/table`` has been written (run ``compute_features`` first).
    ValueError
        The project has no labeled molecules (human or provisional), or only one class is labeled
        (needs both accepted and rejected examples — propagated from the model).
    """
    return _fit(weighted_training_set(project, w0=w0), _project_name(project))


def ranker_ranking(project: ProjectRef, *, w0: float = DEFAULT_SEED_WEIGHT) -> RankedTraces:
    """Rank **all** the project's molecules by predicted quality — never auto-drop (PRD §7.5).

    Trains on the labeled molecules (human + decayed provisional priors), then scores and ranks
    every molecule (labeled *and* uncurated), so the result is a permutation of the full candidate
    set — no molecule is dropped, including uncurated ones and any with undefined (``NaN``) features
    (scored natively). Read-only.

    Raises
    ------
    KeyError
        No ``/features/table`` exists.
    ValueError
        No labeled molecules (human or provisional), or only one class is labeled.
    """
    ranking, _ = _train_and_rank(project, w0)
    return ranking


def ranker_precision_at_k(project: ProjectRef, k: int, *, w0: float = DEFAULT_SEED_WEIGHT) -> float:
    """**Apparent** precision@k of the ranker over the project's **human**-labeled molecules.

    An **in-sample** fit diagnostic (PRD §7.5): the ranker is trained on the project's labels (human
    **plus** decayed provisional priors) and precision@k is measured over the ranking of the
    **human**-labeled molecules only — the provisional seeds train the model but are never scored as
    ground truth. Optimistically biased, so **not** the M5 ship gate — the honest held-out
    **prequential** median-across-videos uplift lands in its own later PR (oracle (d)). Compare it
    against :func:`tether.project.ranking.baseline_precision_at_k` for a same-project, same-``k``
    before/after read. Read-only.

    Raises
    ------
    KeyError
        No ``/features/table`` exists.
    ValueError
        No human-labeled molecules (precision@k is undefined), no labeled molecules to train on,
        only one class is labeled, or ``k`` is not a positive integer (propagated from
        :func:`tether.ml.ranking.precision_at_k`).
    """
    from tether.ml.ranking import precision_at_k  # noqa: PLC0415

    ranking, data = _train_and_rank(project, w0)
    if not data.is_good:
        # The model may have trained on provisional-only priors, but apparent precision@k is
        # measured against human ground truth — undefined here, surfaced loudly (never a fake 0).
        raise ValueError(
            f"{_project_name(project)} has no human-labeled molecules; apparent precision@k "
            "is undefined"
        )
    return precision_at_k(ranking.ranked_relevance(data.is_good), k)
