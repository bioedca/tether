# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Store-integrated cross-condition drift advisory (PRD §7.5, §9 M5; FR-ML).

Wires the pure drift signal (:mod:`tether.ml.drift`) to a ``.tether``: it reads the engineered
``/features`` matrix, groups its rows by each molecule's ``condition_id`` (``/molecules``), and
compares two conditions' feature distributions — the advisory the seeding path raises when a model
is seeded from a dissimilar condition (PRD §7.5). Features are grouped over **every** featured
molecule of a condition (labeled or not): drift is a property of the raw feature distribution, not
of the curated subset.

Read-only over the M0-frozen ``/features`` + ``/molecules``: no group, dataset, dtype or field
change (``schema-guard`` holds), and nothing is persisted or mutated.

Cross-file seeding. Comparing conditions that live in **different** ``.tether`` files (seeding from
another experiment) is the seeding PR's job; it composes the same primitives — call
:func:`condition_feature_matrices` on each file and pass the two matrices to
:func:`tether.ml.drift.condition_drift` (which is store- and file-agnostic).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from tether.ml.drift import DEFAULT_DRIFT_ALPHA, condition_drift

if TYPE_CHECKING:
    from os import PathLike

    from tether.ml.drift import DriftReport
    from tether.project.core import Project
    from tether.project.features import StoredFeatures

    ProjectRef = Project | str | PathLike[str]

__all__ = ["condition_feature_matrices", "cross_condition_drift"]


def _project_path(project: ProjectRef) -> Path:
    from tether.project.core import Project as _Project  # noqa: PLC0415

    return project.path if isinstance(project, _Project) else Path(project)


def _to_str(value: object) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _condition_by_molecule_id(path: Path) -> dict[str, str]:
    """``molecule_id -> condition_id`` over the ``/molecules`` table (the unique per-row key)."""
    from tether.imaging.extract import read_molecules  # noqa: PLC0415

    molecules = read_molecules(path)
    ids = [_to_str(x) for x in molecules["molecule_id"]]
    # molecule_id is the unique per-row join key (§7.10); a duplicate would silently collapse two
    # molecules' condition assignment (strict=True checks only length), mirroring the guard in
    # tether.project.features._spatial_features_by_id.
    if len(set(ids)) != len(ids):
        raise ValueError(f"{path.name}/molecules has duplicate molecule_id values")
    conditions = [_to_str(c) for c in molecules["condition_id"]]
    return dict(zip(ids, conditions, strict=True))


def _grouped(project: ProjectRef) -> tuple[StoredFeatures, dict[str, list[int]]]:
    """The stored features + a ``condition_id -> feature-row indices`` grouping (store order)."""
    from tether.project.features import feature_matrix  # noqa: PLC0415

    stored = feature_matrix(project)
    cond_by_id = _condition_by_molecule_id(_project_path(project))
    groups: dict[str, list[int]] = {}
    for i, mid in enumerate(stored.molecule_ids):
        try:
            cond = cond_by_id[mid]
        except KeyError:  # a featured molecule with no /molecules row is a broken store
            raise ValueError(
                f"molecule_id {mid!r} has a /features row but no /molecules condition_id"
            ) from None
        groups.setdefault(cond, []).append(i)
    return stored, groups


def condition_feature_matrices(project: ProjectRef) -> dict[str, np.ndarray]:
    """``condition_id -> (n_molecules, n_features)`` feature matrix for every condition (PRD §7.5).

    Reads ``/features`` (:func:`tether.project.features.feature_matrix`) and groups its rows by each
    molecule's ``condition_id`` (``/molecules``), including uncurated molecules — drift is a
    property of the full feature distribution. Column order is the stored feature order
    (:data:`tether.ml.features.FEATURE_NAMES`). Read-only.

    Cost / reuse. Each call performs **one** store read (``/features`` + ``/molecules``). A caller
    that needs both this grouping and a drift comparison — or that compares many condition pairs —
    should read once here and pass two of the returned matrices straight to the store-free
    :func:`tether.ml.drift.condition_drift` (its column contract is this same
    :data:`~tether.ml.features.FEATURE_NAMES` order), rather than also calling
    :func:`cross_condition_drift`, which re-reads the store.

    Raises
    ------
    KeyError
        No ``/features/table`` has been written (run ``compute_features`` first).
    ValueError
        The store is inconsistent (a ``/features`` row with no ``/molecules`` row, or a duplicate
        ``molecule_id``).
    """
    stored, groups = _grouped(project)
    return {cond: stored.matrix[idxs] for cond, idxs in groups.items()}


def cross_condition_drift(
    project: ProjectRef,
    source_condition: str,
    target_condition: str,
    *,
    alpha: float = DEFAULT_DRIFT_ALPHA,
) -> DriftReport:
    """Advisory feature-distribution drift between two of a project's conditions (§9 M5).

    The store-integrated cross-condition drift flag PRD §7.5 requires before seeding a condition's
    ranker from another: compares the two conditions' ``/features`` distributions
    (:func:`tether.ml.drift.condition_drift`) and returns the per-feature verdicts plus the overall
    **advisory, overridable** flag. Read-only, one store read per call — a caller comparing many
    pairs should instead read once via :func:`condition_feature_matrices` and call
    :func:`tether.ml.drift.condition_drift` per pair (see that function's "Cost / reuse" note).

    Parameters
    ----------
    project:
        A :class:`~tether.project.core.Project` or a path to a ``.tether`` store.
    source_condition, target_condition:
        The two ``condition_id`` values to compare (as on ``/molecules``). The KS statistic is
        symmetric, so the order only labels the report.
    alpha:
        The overall family-wise significance level of the advisory (default
        :data:`tether.ml.drift.DEFAULT_DRIFT_ALPHA`, the PRD §11.2 tunable).

    Returns
    -------
    DriftReport
        The per-feature drift verdicts and the overall advisory flag.

    Raises
    ------
    KeyError
        No ``/features/table`` exists, or a named condition has no featured molecules.
    ValueError
        The store is inconsistent, or drift is undefined (no testable feature — propagated from
        :func:`tether.ml.drift.condition_drift`).
    """
    stored, groups = _grouped(project)
    for cond in (source_condition, target_condition):
        if cond not in groups:
            raise KeyError(
                f"condition {cond!r} has no featured molecules in "
                f"{_project_path(project).name}; known conditions: {sorted(groups)}"
            )
    src = stored.matrix[groups[source_condition]]
    tgt = stored.matrix[groups[target_condition]]
    return condition_drift(src, tgt, stored.feature_names, alpha=alpha)
