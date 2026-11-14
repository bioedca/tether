# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Train and retrain a condition's portable ranker on the labels of every project that holds the
condition (PRD §7.5, UC3; ADR-0035, ADR-0068, ADR-0077).

One project holds one experiment, and a condition can span several projects (ADR-0068). The
portable model of :mod:`tether.ml.persistence` refits on the accumulated label set at each video
boundary (ADR-0035), so this module collects that set from all the condition's projects:

1. The model records the project files it was trained on (:class:`tether.ml.SourceProject`).
2. A retrain reads ``/features`` ⋈ ``/labels`` for the model's condition from each recorded
   project, **read-only and without their locks**, and adds the rows of the open project.
3. ``n_human`` for the cold-start decay ``w = w₀ / (1 + n_human)`` (:mod:`tether.ml.weighting`)
   counts the human labels of the condition in **all** those projects.
4. A recorded project that is missing, or offline (a cloud placeholder whose data is not on the
   disk), stops the retrain with :class:`SourceProjectUnavailableError`, which names the file.
   Every project is checked before any is read, and a placeholder is never opened, so nothing is
   hydrated automatically (PRD §5.4) and a partial label set is never used.

The labels are read from the projects at every retrain and are not kept in the model file, so a
label changed in an older project reaches the next retrain: ``/labels`` stays the label store of
record (PRD §5.1). The maintainer chose this over keeping the rows in the model (ADR-0077).

The UC3 loop with this module is::

    model = train_condition_model(day1, condition_id=cid, video_id=v1)   # first video
    save_model(model, path)
    #   ... the next day, in another project ...
    model = retrain_condition_model(load_model(path), day2, video_id=v2)  # reads day1 again
    save_model(model, path)

Out of scope here: the project's ``/models`` reference and the owner-curator lock (#561), and the
application controls (#640).
"""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from tether.ml.weighting import DEFAULT_SEED_WEIGHT, seed_weight
from tether.project.labels import HUMAN_WEIGHT, CurationLabel

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from os import PathLike

    from tether.ml.gbranker import RankerHyperparams
    from tether.ml.persistence import PortableRankerModel, SourceProject
    from tether.project.core import Project

    ProjectRef = Project | str | PathLike[str]

__all__ = [
    "ConditionTrainingSet",
    "SourceProjectUnavailableError",
    "condition_training_set",
    "retrain_condition_model",
    "train_condition_model",
]

_ACCEPT = int(CurationLabel.ACCEPT)
_UNCURATED = int(CurationLabel.UNCURATED)

#: ``FILE_ATTRIBUTE_OFFLINE`` (``0x1000``): "the data of a file is not immediately available"
#: (Win32 file attribute constants, read through Context7 2026-11-12). Taken from the standard
#: library so the value is not typed by hand. ``os.stat`` reads attributes only and never hydrates.
_FILE_ATTRIBUTE_OFFLINE = stat.FILE_ATTRIBUTE_OFFLINE
#: The cloud-file recall bits, which the standard library does not carry: opening the file
#: (``RECALL_ON_OPEN``) or reading its data (``RECALL_ON_DATA_ACCESS``) fetches it from remote
#: storage. Values from ``winnt.h`` of mingw-w64-headers (``master``, retrieved 2026-11-13,
#: SHA-256 ``bc318d60…4321``); the Microsoft page could not be fetched from that session.
_FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x00040000
_FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x00400000
#: A project whose attributes carry any of these is not on the disk: it is never opened (§5.4).
_OFFLINE_ATTRIBUTES = (
    _FILE_ATTRIBUTE_OFFLINE | _FILE_ATTRIBUTE_RECALL_ON_OPEN | _FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
)


class SourceProjectUnavailableError(RuntimeError):
    """A project file the retrain needs is missing or offline; the retrain did not run.

    Attributes
    ----------
    path:
        The project file that could not be read.
    reason:
        ``"missing"``, ``"offline"`` or ``"unreadable"``.
    """

    def __init__(self, path: Path, reason: str, detail: str = "") -> None:
        self.path = Path(path)
        self.reason = reason
        text = {
            "missing": "does not exist (moved, renamed or deleted)",
            "offline": "is offline: its data is not on this disk, and Tether does not download it",
            "unreadable": "cannot be read",
        }[reason]
        message = f"source project {self.path} {text}; the retrain did not run"
        if detail:
            message += f" ({detail})"
        message += ". Make the file available on this computer, then retrain again."
        super().__init__(message)


# ``eq=False`` -> identity equality/hash (the WeightedTrainingSet precedent): numpy fields.
@dataclass(frozen=True, eq=False)
class ConditionTrainingSet:
    """The training rows of one condition, collected from several projects (PRD §7.5).

    Row order is project order (the order of :attr:`sources`), then the stored feature-row order
    inside each project, so the same projects give the same set every time.

    Attributes
    ----------
    condition_id:
        The condition the rows belong to.
    project_paths:
        The project file of each row (absolute path).
    molecule_ids:
        The ``molecule_id`` of each row, unique inside its project only.
    feature_names:
        The feature-column order (:data:`tether.ml.features.FEATURE_NAMES`).
    X:
        ``(n_train, n_features)`` ``float64`` feature matrix (``NaN`` allowed).
    y:
        ``(n_train,)`` boolean accept (``True`` = good) / reject label.
    sample_weight:
        ``(n_train,)`` ``float64`` weight: ``1.0`` for a human label, ``w₀/(1 + n_human)`` for a
        provisional prior, with ``n_human`` counted over every project.
    n_human:
        The human labels of the condition in all the projects (the decay's ``n_human``).
    sources:
        One :class:`tether.ml.SourceProject` for each project read, in row order.
    """

    condition_id: str
    project_paths: tuple[str, ...]
    molecule_ids: list[str]
    feature_names: tuple[str, ...]
    X: np.ndarray
    y: np.ndarray
    sample_weight: np.ndarray
    n_human: int
    sources: tuple[SourceProject, ...]

    @property
    def n_train(self) -> int:
        """Number of training rows, human plus provisional, over all projects."""
        return len(self.molecule_ids)

    @property
    def n_good(self) -> int:
        """Number of training rows labeled accept (good)."""
        return int(np.count_nonzero(self.y))


@dataclass(frozen=True, eq=False)
class _ProjectRows:
    """The condition's rows from one project, before the cross-project weights are known."""

    path: Path
    molecule_ids: list[str]
    molecule_keys: list[str]
    feature_names: tuple[str, ...]
    X: np.ndarray
    y: np.ndarray
    is_human: np.ndarray
    n_human: int
    n_molecules: int


def _project_path(project: ProjectRef) -> Path:
    from tether.project.core import Project as _Project  # noqa: PLC0415

    path = project.path if isinstance(project, _Project) else Path(project)
    return Path(os.path.abspath(path))


def _identity(path: Path) -> str:
    """The comparison key of a project path: symlinks and junctions resolved, then case-folded
    where the platform folds case, so one file reached by two paths is one project."""
    return os.path.normcase(os.path.realpath(path))


def _to_str(value: object) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _file_attributes(path: Path) -> int:
    """The Windows file attributes of ``path`` (``0`` elsewhere). Metadata only: no byte read.

    Raises :class:`FileNotFoundError` when the file does not exist.
    """
    return int(getattr(os.stat(path), "st_file_attributes", 0))


def _check_available(path: Path) -> None:
    """Refuse a missing or offline project before anything opens it (PRD §5.4)."""
    try:
        attributes = _file_attributes(path)
    except FileNotFoundError as exc:
        raise SourceProjectUnavailableError(path, "missing") from exc
    except OSError as exc:
        raise SourceProjectUnavailableError(path, "unreadable", str(exc)) from exc
    if not path.is_file():
        raise SourceProjectUnavailableError(path, "missing", "not a file")
    if attributes & _OFFLINE_ATTRIBUTES:
        raise SourceProjectUnavailableError(path, "offline")


def _read_rows(path: Path, condition_id: str) -> _ProjectRows:
    """Read one project's labelled rows of ``condition_id``. Read-only; takes no lock.

    Opens the file only through the path-based readers, which open it with ``h5py`` mode ``"r"``.
    A human accept/reject supersedes a provisional prior on the same molecule (the
    :mod:`tether.project.gbranking` rule).
    """
    from tether.imaging.extract import read_molecules  # noqa: PLC0415
    from tether.project.gbranking import (  # noqa: PLC0415
        _latest_provisional_labels,
        _require_featured_human_labels,
    )
    from tether.project.labels import read_labels  # noqa: PLC0415
    from tether.project.ranking import ranking_dataset  # noqa: PLC0415
    from tether.project.weighting import human_counts_by_condition  # noqa: PLC0415

    try:
        data = ranking_dataset(path)
        molecules = read_molecules(path)
        provisional = _latest_provisional_labels(read_labels(path))
    except OSError as exc:
        raise SourceProjectUnavailableError(path, "unreadable", str(exc)) from exc

    condition_by_key = {
        _to_str(key): _to_str(cond)
        for key, cond in zip(molecules["molecule_key"], molecules["condition_id"], strict=True)
    }
    _require_featured_human_labels(path, molecules, data.molecule_keys, condition_id)
    n_molecules = sum(1 for cond in condition_by_key.values() if cond == condition_id)
    n_human = human_counts_by_condition(molecules["condition_id"], molecules["curation_label"]).get(
        condition_id, 0
    )

    idx: list[int] = []
    labels: list[bool] = []
    human: list[bool] = []
    for i, key in enumerate(data.molecule_keys):
        if condition_by_key.get(key) != condition_id:
            continue
        code = int(data.curation_label[i])
        if code != _UNCURATED:
            labels.append(code == _ACCEPT)
            human.append(True)
        elif key in provisional:
            labels.append(provisional[key] == _ACCEPT)
            human.append(False)
        else:
            continue  # uncurated and no prior: ranked, never a training row
        idx.append(i)

    return _ProjectRows(
        path=path,
        molecule_ids=[data.molecule_ids[i] for i in idx],
        molecule_keys=[data.molecule_keys[i] for i in idx],
        feature_names=data.feature_names,
        X=np.asarray(data.X[idx], dtype=np.float64),
        y=np.asarray(labels, dtype=bool),
        is_human=np.asarray(human, dtype=bool),
        n_human=int(n_human),
        n_molecules=n_molecules,
    )


def _rows_sha256(rows: _ProjectRows) -> str:
    """SHA-256 of the rows a fit took from one project: keys, labels, label sources, features."""
    digest = hashlib.sha256()
    for key, good, is_human, features in zip(
        rows.molecule_keys, rows.y.tolist(), rows.is_human.tolist(), rows.X, strict=True
    ):
        digest.update(key.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(bytes([int(good), int(is_human)]))
        digest.update(np.ascontiguousarray(features, dtype="<f8").tobytes())
    return digest.hexdigest()


def condition_training_set(
    projects: Sequence[ProjectRef],
    condition_id: str,
    *,
    w0: float = DEFAULT_SEED_WEIGHT,
) -> ConditionTrainingSet:
    """Collect the labelled rows of ``condition_id`` from every project in ``projects``.

    Each project is checked first (it exists and is not offline), and only then read, read-only and
    without its lock. Human labels train at weight ``1.0``; a provisional prior trains at
    ``w₀ / (1 + n_human)``, where ``n_human`` counts the condition's human labels in **all**
    ``projects`` (PRD §7.5).

    Parameters
    ----------
    projects:
        The project files (or open :class:`~tether.project.core.Project` handles) to read, in the
        row order wanted. A file named twice is a caller error.
    condition_id:
        The condition whose rows are collected; other conditions' molecules are left out.
    w0:
        The cold-start seed weight ``w₀`` (PRD §11.2).

    Raises
    ------
    SourceProjectUnavailableError
        A project is missing, offline or unreadable. No project has been read when this is raised
        for the availability check.
    KeyError
        A project has no ``/features/table`` (run ``compute_features`` first).
    tether.project.gbranking.FeatureCacheIncompleteError
        A human label of the condition has no feature row in a project (a partial cache).
    ValueError
        ``projects`` is empty or names one file twice, or two projects store different feature
        columns.
    """
    from tether.ml.persistence import SourceProject  # noqa: PLC0415

    paths = [_project_path(p) for p in projects]
    if not paths:
        raise ValueError("condition_training_set needs at least one project")
    keys = [_identity(p) for p in paths]
    if len(set(keys)) != len(keys):
        raise ValueError(f"a project file is named more than once: {[str(p) for p in paths]}")
    for path in paths:  # every file is checked before any is read: never a partial set
        _check_available(path)

    cid = str(condition_id)
    per_project = [_read_rows(path, cid) for path in paths]
    names = per_project[0].feature_names
    for rows in per_project[1:]:
        if rows.feature_names != names:
            raise ValueError(
                f"{rows.path} stores the feature columns {rows.feature_names}, but "
                f"{per_project[0].path} stores {names}; recompute the features of both"
            )

    n_human = sum(rows.n_human for rows in per_project)
    seed_w = seed_weight(n_human, w0=w0)
    weights = [
        np.where(rows.is_human, HUMAN_WEIGHT, seed_w).astype(np.float64) for rows in per_project
    ]
    n_features = len(names)
    return ConditionTrainingSet(
        condition_id=cid,
        project_paths=tuple(str(rows.path) for rows in per_project for _ in rows.molecule_ids),
        molecule_ids=[mid for rows in per_project for mid in rows.molecule_ids],
        feature_names=names,
        X=np.vstack([rows.X.reshape(-1, n_features) for rows in per_project]),
        y=np.concatenate([rows.y for rows in per_project]),
        sample_weight=np.concatenate(weights),
        n_human=n_human,
        sources=tuple(
            SourceProject(
                path=str(rows.path),
                rows_sha256=_rows_sha256(rows),
                n_rows=len(rows.molecule_ids),
                n_human=rows.n_human,
            )
            for rows in per_project
        ),
    )


def _fit_weights(training: ConditionTrainingSet) -> np.ndarray | None:
    """``None`` when every row is at unit weight, so a human-only set fits the unweighted model
    (the :func:`tether.project.gbranking.train_ranker` rule)."""
    if training.n_train == 0:
        raise ValueError(
            f"condition {training.condition_id!r} has no labeled molecules (human or provisional "
            "seed) in the projects read; cannot train a quality ranker"
        )
    if bool(np.all(training.sample_weight == HUMAN_WEIGHT)):
        return None
    return training.sample_weight


def _require_condition(training: ConditionTrainingSet, path: Path) -> None:
    """The open project must hold the condition; otherwise the call names the wrong project."""
    from tether.imaging.extract import read_molecules  # noqa: PLC0415

    molecules = read_molecules(path)
    if not any(_to_str(c) == training.condition_id for c in molecules["condition_id"]):
        raise ValueError(f"{path} holds no molecule of condition {training.condition_id!r}")


def train_condition_model(
    project: ProjectRef,
    *,
    condition_id: str,
    w0: float = DEFAULT_SEED_WEIGHT,
    hyperparams: RankerHyperparams | None = None,
    video_id: str | None = None,
    now: datetime | None = None,
) -> PortableRankerModel:
    """Fit a condition's first portable model from one project, and record the project.

    The start of the UC3 loop (PRD §7.5): the rows of ``condition_id`` in ``project``
    (:func:`condition_training_set`) train a fresh model
    (:func:`tether.ml.persistence.train_portable_model`) whose ``source_projects`` names the
    project, so a later :func:`retrain_condition_model` from another project reads it again.

    Raises
    ------
    SourceProjectUnavailableError, KeyError, FeatureCacheIncompleteError
        As :func:`condition_training_set`.
    ValueError
        ``project`` holds no molecule of the condition, no labeled molecule, or only one class.
    """
    from tether.ml.persistence import train_portable_model  # noqa: PLC0415

    path = _project_path(project)
    training = condition_training_set([path], condition_id, w0=w0)
    _require_condition(training, path)
    return train_portable_model(
        training.X,
        training.y,
        training.feature_names,
        condition_id=training.condition_id,
        hyperparams=hyperparams,
        sample_weight=_fit_weights(training),
        video_id=video_id,
        source_projects=training.sources,
        now=now,
    )


def retrain_condition_model(
    model: PortableRankerModel,
    project: ProjectRef,
    *,
    w0: float = DEFAULT_SEED_WEIGHT,
    hyperparams: RankerHyperparams | None = None,
    video_id: str | None = None,
    now: datetime | None = None,
) -> PortableRankerModel:
    """Retrain the model on its condition's labels from every recorded project plus ``project``.

    Reads ``model.source_projects`` in their recorded order, then ``project`` if the model has not
    recorded it yet, all read-only and without locks (:func:`condition_training_set`), and refits
    with :func:`tether.ml.persistence.warm_start_retrain`. The new model records every project it
    read, with fresh row digests. The input model is not changed, and nothing is saved: a refused
    retrain therefore leaves a saved model as it was.

    Parameters
    ----------
    model:
        The condition's model, as :func:`tether.ml.persistence.load_model` returns it.
    project:
        The open project (the one being curated). It must hold the model's condition.
    w0, hyperparams, video_id, now:
        As :func:`tether.ml.persistence.warm_start_retrain`; ``w0`` is the seed weight.

    Raises
    ------
    SourceProjectUnavailableError
        A recorded project is missing, offline or unreadable. Raised before any project is read.
    KeyError
        A project has no ``/features/table``.
    FeatureCacheIncompleteError
        As :func:`condition_training_set`; the input model is not changed.
    ValueError
        The model records no source projects (an artifact saved before ADR-0077, or one fitted on
        caller-supplied arrays): a retrain from one project would forget the rest. Or ``project``
        holds no molecule of the condition, the projects store different feature columns, or the
        rows have no labels or one class only.
    """
    from tether.ml.persistence import warm_start_retrain  # noqa: PLC0415

    if not model.source_projects:
        # A model with no project record (saved before ADR-0077, or fitted on caller arrays) would
        # refit on the open project alone and silently forget every earlier project's labels.
        raise ValueError(
            f"the model of condition {model.condition_id!r} records no source projects, so a "
            "retrain would forget the labels it was trained on. Fit it again from its projects: "
            "condition_training_set([...]) then warm_start_retrain(..., "
            "source_projects=training.sources), or train_condition_model for one project"
        )
    open_path = _project_path(project)
    paths = [Path(src.path) for src in model.source_projects]
    if _identity(open_path) not in {_identity(p) for p in paths}:
        paths.append(open_path)
    training = condition_training_set(paths, model.condition_id, w0=w0)
    _require_condition(training, open_path)
    return warm_start_retrain(
        model,
        training.X,
        training.y,
        training.feature_names,
        video_id=video_id,
        hyperparams=hyperparams,
        sample_weight=_fit_weights(training),
        source_projects=training.sources,
        now=now,
    )
