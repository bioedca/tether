# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Store-integrated ranking dataset + baseline precision@k (PRD §7.5; FR-ML).

Read-only assembly of the per-condition quality ranker's *supervised* view from a
``.tether``: it joins the engineered ``/features`` matrix
([`tether.project.features.feature_matrix`][]) with each molecule's authoritative
accept/reject ``curation_label`` ([`tether.project.labels`][] — the ``/molecules`` state
field) to produce the (feature-matrix, good/bad ground-truth) pairing a ranker trains on
and is evaluated with ([`tether.ml.ranking`][]).

Deliberately **no model and no new dependency**: the gradient-boosting model that consumes
this dataset (and adds scikit-learn/XGBoost to the base lock) is a later PR. What lands here
is the substrate — the feature/label join, and the **file-order-baseline precision@k**
([`baseline_precision_at_k`][]) the model's uplift (PRD §7.5, oracle (d)) will be measured
against. Read-only over the M0-frozen ``/features`` + ``/molecules``: no group, dataset,
dtype or field change, so the ``schema-guard`` freeze holds.

Join key. Features are keyed by the unique per-row ``molecule_id``; ``curation_label`` lives
on ``molecule_key`` (a key can name several ids — §7.10 quantized-coordinate collisions), so
each feature row takes the label of its ``molecule_key``. Only human accept/reject labels are
ground truth: an uncurated molecule is a ranking *candidate* without a training label
([`RankingDataset.is_good`][] omits it), and is never dropped from the candidate set.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from tether.io.generations import pending_label_ids_in
from tether.io.schema import TABLE
from tether.project.labels import CurationLabel, _to_str

if TYPE_CHECKING:
    from os import PathLike

    from h5py import File

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
# semantics keep the object safely comparable/hashable (the `tether.ml.similarity`
# precedent); tests compare the array fields directly.
@dataclass(frozen=True, eq=False)
class RankingDataset:
    """The ranker's supervised view of a project: features + accept/reject truth.

    ``X`` is the ``(n_molecules, n_features)`` ``float64`` feature matrix (row ``i`` =
    ``molecule_ids[i]`` / ``molecule_keys[i]``); ``curation_label`` is the aligned signed
    [`CurationLabel`][tether.project.labels.CurationLabel] code (``+1`` accept, ``-1`` reject, ``0``
    uncurated). [`labeled_mask`][] marks the human-labeled rows (the ground truth) and
    [`is_good`][] is the ``molecule_id -> accepted?`` map over just those rows, ready for
    [`tether.ml.ranking`][]. Carried labels awaiting confirmation appear uncurated in
    this supervised view. ``pending_label_ids`` also identifies carry-overs with no
    feature row, so training and label weighting apply the same eligibility rule.
    """

    molecule_ids: list[str]
    molecule_keys: list[str]
    feature_names: tuple[str, ...]
    X: np.ndarray
    curation_label: np.ndarray
    pending_label_ids: frozenset[str] = frozenset()

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

    Reads features, active molecules and pending reviews from one file handle.
    Features join to active molecule IDs with matching keys. A carried label becomes
    supervised evidence after a human confirms it on the active extraction. Read-only.

    Raises
    ------
    KeyError
        No ``/features/table`` has been written (run ``compute_features`` first).
    ValueError
        A feature row does not match an active molecule (run ``compute_features`` again).
    """
    import h5py

    path = _project_path(project)
    with h5py.File(path, "r") as f:
        return _ranking_data_in(f, path)[0]


def _training_snapshot(project: ProjectRef) -> tuple[RankingDataset, np.ndarray, np.ndarray]:
    """Read the ranking dataset, molecule rows and label history from one project."""
    import h5py

    path = _project_path(project)
    with h5py.File(path, "r") as f:
        data, molecules = _ranking_data_in(f, path)
        return data, molecules, f["labels"][TABLE][:]


def _ranking_data_in(f: File, path: Path) -> tuple[RankingDataset, np.ndarray]:
    """Join cached features to active molecules in an open project snapshot."""
    from tether.project.features import _feature_matrix_in

    stored = _feature_matrix_in(f, path)
    molecules = f["molecules"][TABLE][:]
    by_id = {_to_str(row["molecule_id"]): row for row in molecules}
    pending = pending_label_ids_in(f)
    curation = []
    for mid, key in zip(stored.molecule_ids, stored.molecule_keys, strict=True):
        row = by_id.get(mid)
        if row is None or _to_str(row["molecule_key"]) != key:
            raise ValueError(
                f"{path}: feature row {mid!r} does not match an active molecule. "
                "Run compute_features again with its default arguments, then retry."
            )
        curation.append(_UNCURATED if mid in pending else int(row["curation_label"]))

    data = RankingDataset(
        molecule_ids=list(stored.molecule_ids),
        molecule_keys=list(stored.molecule_keys),
        feature_names=stored.feature_names,
        X=np.asarray(stored.matrix, dtype=np.float64),
        curation_label=np.asarray(curation, dtype=np.int64),
        pending_label_ids=pending,
    )
    return data, molecules


def baseline_precision_at_k(project: ProjectRef, k: int) -> float:
    """File-order-baseline precision@k over a project's labeled molecules (PRD §7.5, oracle (d)).

    The reference the quality ranker's uplift is measured against: the precision@k that plain
    file-/extraction-order curation achieves on the molecules a human has actually
    accepted/rejected ([`tether.ml.ranking.file_order_ranking`][] +
    [`tether.ml.ranking.precision_at_k`][]). Read-only.

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
