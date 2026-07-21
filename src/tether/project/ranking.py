# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Store-integrated ranking dataset + baseline precision@k (PRD §7.5; FR-ML).

Read-only assembly of the per-condition quality ranker's *supervised* view from a
``.tether``: it joins the engineered ``/features`` matrix
(:func:`tether.project.features.feature_matrix`) with each molecule's authoritative
accept/reject ``curation_label`` (:mod:`tether.project.labels` — the ``/molecules`` state
field) to produce the (feature-matrix, good/bad ground-truth) pairing a ranker trains on
and is evaluated with (:mod:`tether.ml.ranking`).

Deliberately **no model and no new dependency**: the gradient-boosting model that consumes
this dataset (and adds scikit-learn/XGBoost to the base lock) is a later PR. What lands here
is the substrate — the feature/label join, and the **file-order-baseline precision@k**
(:func:`baseline_precision_at_k`) the model's uplift (PRD §7.5, oracle (d)) will be measured
against. Read-only over the M0-frozen ``/features`` + ``/molecules``: no group, dataset,
dtype or field change, so the ``schema-guard`` freeze holds.

Join key. Features are keyed by the unique per-row ``molecule_id``; ``curation_label`` lives
on ``molecule_key`` (a key can name several ids — §7.10 quantized-coordinate collisions), so
each feature row takes the label of its ``molecule_key``. Only human accept/reject labels are
ground truth: an uncurated molecule is a ranking *candidate* without a training label
(:attr:`RankingDataset.is_good` omits it), and is never dropped from the candidate set.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from tether.project.labels import CurationLabel

if TYPE_CHECKING:
    from os import PathLike

    from tether.project.core import Project

    ProjectRef = Project | str | PathLike[str]

__all__ = ["RankingDataset", "baseline_precision_at_k", "ranking_dataset"]

_UNCURATED = int(CurationLabel.UNCURATED)
_ACCEPT = int(CurationLabel.ACCEPT)


def _project_path(project: ProjectRef) -> Path:
    from tether.project.core import Project as _Project

    return project.path if isinstance(project, _Project) else Path(project)


# ``eq=False`` -> identity equality/hash. This value object holds numpy arrays (``X``,
# ``curation_label``); a dataclass-generated ``__eq__`` would compare them with ``==`` (an
# ndarray ``==`` is elementwise -> ambiguous truth value) and ``__hash__`` would hash an
# unhashable ndarray, so ``dataset_a == dataset_b`` / ``hash(dataset)`` would raise. Identity
# semantics keep the object safely comparable/hashable (the :mod:`tether.ml.similarity`
# precedent); tests compare the array fields directly.
@dataclass(frozen=True, eq=False)
class RankingDataset:
    """The ranker's supervised view of a project: features + accept/reject truth.

    ``X`` is the ``(n_molecules, n_features)`` ``float64`` feature matrix (row ``i`` =
    ``molecule_ids[i]`` / ``molecule_keys[i]``); ``curation_label`` is the aligned signed
    :class:`~tether.project.labels.CurationLabel` code (``+1`` accept, ``-1`` reject, ``0``
    uncurated). :attr:`labeled_mask` marks the human-labeled rows (the ground truth) and
    :attr:`is_good` is the ``molecule_id -> accepted?`` map over just those rows, ready for
    :mod:`tether.ml.ranking`.
    """

    molecule_ids: list[str]
    molecule_keys: list[str]
    feature_names: tuple[str, ...]
    X: np.ndarray
    curation_label: np.ndarray

    @property
    def n_molecules(self) -> int:
        """Number of candidate molecules — rows of ``X``, labeled and uncurated alike."""
        return len(self.molecule_ids)

    @property
    def labeled_mask(self) -> np.ndarray:
        """A boolean mask of the rows carrying a human accept/reject label."""
        return self.curation_label != _UNCURATED

    @property
    def n_labeled(self) -> int:
        """Number of rows carrying a human accept/reject label (the ground-truth count)."""
        return int(np.count_nonzero(self.labeled_mask))

    @property
    def is_good(self) -> dict[str, bool]:
        """``molecule_id -> accepted`` over just the human-labeled rows (eval ground truth)."""
        return {
            mid: bool(label == _ACCEPT)
            for mid, label in zip(self.molecule_ids, self.curation_label.tolist(), strict=True)
            if label != _UNCURATED
        }


def ranking_dataset(project: ProjectRef) -> RankingDataset:
    """Assemble the supervised ranking dataset from a project's ``/features`` + ``/labels``.

    Reads the stored feature matrix (:func:`tether.project.features.feature_matrix`) and each
    molecule's ``curation_label`` (:func:`tether.project.labels.curation_labels`), joining on
    ``molecule_key``. Read-only.

    Raises
    ------
    KeyError
        No ``/features/table`` has been written (run ``compute_features`` first).
    """
    from tether.project.features import feature_matrix
    from tether.project.labels import curation_labels

    stored = feature_matrix(project)
    labels_by_key = curation_labels(_project_path(project))
    curation = np.array(
        [int(labels_by_key.get(key, _UNCURATED)) for key in stored.molecule_keys],
        dtype=np.int64,
    )
    return RankingDataset(
        molecule_ids=list(stored.molecule_ids),
        molecule_keys=list(stored.molecule_keys),
        feature_names=stored.feature_names,
        X=np.asarray(stored.matrix, dtype=np.float64),
        curation_label=curation,
    )


def baseline_precision_at_k(project: ProjectRef, k: int) -> float:
    """File-order-baseline precision@k over a project's labeled molecules (PRD §7.5, oracle (d)).

    The reference the quality ranker's uplift is measured against: the precision@k that plain
    file-/extraction-order curation achieves on the molecules a human has actually
    accepted/rejected (:func:`tether.ml.ranking.file_order_ranking` +
    :func:`tether.ml.ranking.precision_at_k`). Read-only.

    Raises
    ------
    KeyError
        No ``/features/table`` exists.
    ValueError
        The project has no human-labeled molecules (precision@k is undefined — surfaced
        loudly), or ``k`` is not a positive integer.
    """
    from tether.ml.ranking import file_order_ranking, precision_at_k

    data = ranking_dataset(project)
    is_good = data.is_good
    if not is_good:
        raise ValueError(
            f"{_project_path(project).name} has no human-labeled molecules; precision@k "
            "is undefined"
        )
    ranking = file_order_ranking(data.molecule_ids)
    return precision_at_k(ranking.ranked_relevance(is_good), k)
