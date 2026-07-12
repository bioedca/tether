# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Store-integrated deep-classifier training-dataset builder (PRD §7.5/§9 M8; FR-ML).

Reads a ``.tether`` and assembles the framework-agnostic
:class:`tether.ml.deep.dataset.DeepTraceDataset` for the M8 deep trace classifier (ADR-0047):
it reuses the M5 ranker's **exact** labeled set + cold-start weights
(:func:`tether.project.gbranking.weighted_training_set`) and the same per-molecule window
slicing the engineered features use, so a deep sample's window is identical to that molecule's
feature window. Read-only over the M0-frozen store (no group/dataset/dtype/field change) — the
``schema-guard`` freeze holds — and adds **no** dependency (pure NumPy; PyTorch is the isolated,
optional PR-1b ``deep/`` stack, ADR-0047).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from tether.ml.deep.dataset import (
    DEFAULT_DEEP_CHANNELS,
    DEFAULT_NORMALIZATION,
    DEFAULT_WINDOW_LENGTH,
    assemble_dataset,
)
from tether.ml.weighting import DEFAULT_SEED_WEIGHT

if TYPE_CHECKING:
    from collections.abc import Sequence
    from os import PathLike

    from tether.ml.deep.dataset import DeepTraceDataset
    from tether.project.core import Project

    ProjectRef = Project | str | PathLike[str]

__all__ = ["build_deep_dataset"]


def _project_path(project: ProjectRef) -> Path:
    from tether.project.core import Project as _Project  # noqa: PLC0415

    return project.path if isinstance(project, _Project) else Path(project)


def build_deep_dataset(
    project: ProjectRef,
    *,
    intensity_quantity: str = "corrected",
    channels: Sequence[str] = DEFAULT_DEEP_CHANNELS,
    window_length: int = DEFAULT_WINDOW_LENGTH,
    normalization: str = DEFAULT_NORMALIZATION,
    w0: float = DEFAULT_SEED_WEIGHT,
) -> DeepTraceDataset:
    """Assemble the deep-classifier training dataset from a project's labels + traces (FR-ML).

    Reuses :func:`tether.project.gbranking.weighted_training_set` for the labeled set (every
    human accept/reject at full weight **plus** provisional ``/labels`` priors at their decayed
    ``w₀/(1 + n_human)`` weight; a human label supersedes a provisional prior), joins each row by
    its unique ``molecule_id`` to that molecule's analysis-window-sliced donor/acceptor trace
    (:func:`tether.project.features.windowed_traces` — the shared windowing primitive, so a deep
    window equals the engineered-feature window), and hands both to
    :func:`tether.ml.deep.dataset.assemble_dataset`. Read-only.

    The labeled set is read through the ranker's feature/label join, so ``/features/table`` must
    exist (run :func:`tether.project.features.compute_features` first). ``intensity_quantity``
    selects which ``/traces`` layer feeds the deep tensors (``"corrected"`` default, or
    ``"raw"``) independently of the layer the engineered features were computed on — the labels
    and weights are per-molecule and layer-independent.

    Parameters
    ----------
    project:
        A :class:`~tether.project.core.Project` or a path to a ``.tether`` store.
    intensity_quantity, channels, window_length, normalization:
        The deep-dataset preprocessing parameters (PRD §11.2; see
        :mod:`tether.ml.deep.dataset`).
    w0:
        The cold-start seed weight ``w₀`` provisional priors decay from (PRD §11.2).

    Raises
    ------
    KeyError
        No ``/features/table`` has been written (run ``compute_features`` first).
    ValueError
        The project has no labeled molecules (human or provisional), the requested trace layer is
        absent, or a labeled molecule has no matching trace window.
    """
    from tether.project.features import windowed_traces  # noqa: PLC0415
    from tether.project.gbranking import weighted_training_set  # noqa: PLC0415

    training = weighted_training_set(project, w0=w0)
    path = _project_path(project)
    if training.n_train == 0:
        raise ValueError(
            f"{path.name} has no labeled molecules (human or provisional seed); cannot build a "
            "deep-classifier dataset"
        )

    # include_rejected=True: a reject is a training label (y=0), so its trace must be kept.
    rows = windowed_traces(project, intensity_quantity=intensity_quantity, include_rejected=True)
    windows_by_id = {mol_id: (donor, acceptor) for mol_id, _key, donor, acceptor in rows}

    donors = []
    acceptors = []
    for mol_id in training.molecule_ids:
        pair = windows_by_id.get(mol_id)
        if pair is None:
            raise ValueError(
                f"labeled molecule_id {mol_id!r} has no trace window in {path.name} "
                f"(intensity_quantity={intensity_quantity!r}); the /features and /traces views "
                "disagree — recompute features"
            )
        donors.append(pair[0])
        acceptors.append(pair[1])

    return assemble_dataset(
        training.molecule_ids,
        donors,
        acceptors,
        training.y,
        training.sample_weight,
        window_length=window_length,
        normalization=normalization,
        channels=channels,
        intensity_quantity=intensity_quantity,
    )
