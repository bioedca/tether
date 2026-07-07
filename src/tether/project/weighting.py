# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Recompute + rewrite each ``/labels`` row's effective training weight (PRD §7.5; FR-ML).

The per-condition quality ranker trains **weighted by each label's ``source``**, and the per-row
``weight`` is **mutable — recomputed and rewritten on every retrain** (PRD §5.1 ``/labels``, §7.5).
This module is that store operation: it reads a ``.tether``'s ``/labels`` history plus the
authoritative human accept/reject state on ``/molecules``, applies the pure cold-start decay law
(:mod:`tether.ml.weighting`), and writes the fresh weights back into ``/labels`` — the thing a
retrain calls **before** it (re)fits, so the model always trains on current weights.

The decay (:mod:`tether.ml.weighting`)::

    weight = 1.0                    for a human label   (source == "human")
    weight = w₀ / (1 + n_human)     for a provisional/seed prior

``n_human`` is **the number of molecules in the label's condition that currently carry a human
accept/reject** — i.e. ``/molecules.curation_label != UNCURATED`` grouped by ``condition_id``. That
count is exactly the trusted evidence the ranker trains on for the condition (a provisional source
never sets ``curation_label``; :mod:`tether.project.labels`), so tying the decay to it means a
seed's influence shrinks in lockstep with the real ground truth that supersedes it. Reading the
count from ``/molecules`` (not by tallying ``/labels`` rows) makes it insensitive to a molecule
being re-curated multiple times — one molecule's changing mind is one molecule's worth of evidence,
not several — which is the amount-of-ground-truth reading of "the count of human labels" (PRD §7.5).

Additive under the M0 freeze. ``weight`` is a **frozen** ``/labels`` field (ADR-0005, ADR-0023);
rewriting its *values* changes no group, dataset, dtype or field, and §7.5 declares the field
mutable — so ``schema-guard`` holds. The ``.tether`` is opened ``r+`` for the in-place column
rewrite; the single-writer ``.lock`` that serializes concurrent curation (§5.4) is the caller's
concern, exactly as for :func:`tether.project.labels.set_curation_label`.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from tether.io.schema import TABLE
from tether.ml.weighting import DEFAULT_SEED_WEIGHT, effective_weights
from tether.project.labels import HUMAN_WEIGHT, LABEL_SOURCE_HUMAN, CurationLabel

if TYPE_CHECKING:
    from os import PathLike

    from tether.project.core import Project

    ProjectRef = Project | str | PathLike[str]

__all__ = ["recompute_label_weights"]

_MOLECULES = "molecules"
_LABELS = "labels"
_UNCURATED = int(CurationLabel.UNCURATED)


def _project_path(project: ProjectRef) -> Path:
    from tether.project.core import Project as _Project

    return project.path if isinstance(project, _Project) else Path(project)


def _to_str(value: object) -> str:
    """Decode an h5py variable-length string field (``bytes`` or ``str``)."""
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def recompute_label_weights(project: ProjectRef, *, w0: float = DEFAULT_SEED_WEIGHT) -> int:
    """Recompute every ``/labels`` row's ``weight`` from the current label set and rewrite it.

    Applies the cold-start decay (:mod:`tether.ml.weighting`): a human label becomes full weight
    (:data:`tether.project.labels.HUMAN_WEIGHT`); a provisional/seed prior becomes
    ``w₀ / (1 + n_human)`` where ``n_human`` is its condition's count of human-curated molecules.
    The whole ``/labels/table`` ``weight`` column is rewritten in place (``r+``); no other field is
    touched. Idempotent — human weights resolve to the same ``1.0`` and a fixed label set yields the
    same weights on every call. Call it at the start of each retrain so the fit sees current
    weights.

    Parameters
    ----------
    project:
        The ``.tether`` project (a :class:`~tether.project.core.Project` or a path), opened ``r+``.
    w0:
        The seed weight ``w₀`` (default :data:`tether.ml.weighting.DEFAULT_SEED_WEIGHT`, the PRD
        §11.2 tunable). Provisional/seed rows decay from this.

    Returns
    -------
    int
        The number of ``/labels`` rows whose ``weight`` was rewritten (``0`` if the log is empty).

    Raises
    ------
    KeyError
        The project has no ``/labels`` or ``/molecules`` group (not an extracted ``.tether``).
    ValueError
        ``w0`` is not finite and positive (propagated from :mod:`tether.ml.weighting`).
    """
    import h5py  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    path = _project_path(project)
    with h5py.File(path, "r+") as f:
        try:
            labels_table = f[_LABELS][TABLE]
            mol_table = f[_MOLECULES][TABLE]
        except KeyError as exc:
            raise KeyError(f"{path.name} has no /labels or /molecules group") from exc

        n = int(labels_table.shape[0])
        if n == 0:
            return 0

        # n_human per condition = molecules currently carrying a human accept/reject
        # (curation_label != UNCURATED) grouped by condition_id — the authoritative human state
        # (provisional sources never touch curation_label), robust to a molecule being re-curated.
        mol_conditions = mol_table["condition_id"][:]
        mol_labels = mol_table["curation_label"][:]
        n_human_by_condition: dict[str, int] = {}
        for cond, label in zip(mol_conditions, mol_labels, strict=True):
            key = _to_str(cond)
            n_human_by_condition[key] = n_human_by_condition.get(key, 0) + (
                1 if int(label) != _UNCURATED else 0
            )

        rows = labels_table[:]
        is_human = np.array([_to_str(s) == LABEL_SOURCE_HUMAN for s in rows["source"]], dtype=bool)
        n_human_per_row = np.array(
            [n_human_by_condition.get(_to_str(c), 0) for c in rows["condition_id"]],
            dtype=np.int64,
        )
        rows["weight"] = effective_weights(
            is_human, n_human_per_row, w0=w0, human_weight=HUMAN_WEIGHT
        )
        labels_table[:] = rows
        return n
