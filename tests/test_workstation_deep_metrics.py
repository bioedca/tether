# SPDX-FileCopyrightText: 2026 The Tether Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Small public cases for the workstation deep-check reporting mechanics."""

import os
from functools import partial
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from _deep_device import compare_device_scores
from _deep_lasi_reference import split_reference
from tether.ml.deep import assemble_dataset


class _SyntheticModel:
    def __init__(self, held_out_ids, device):
        self.held_out_ids = held_out_ids
        self.device = device
        self.train_pid = os.getpid()

    def predict_proba(self, dataset):
        assert dataset.molecule_ids == self.held_out_ids
        assert (os.getpid() != self.train_pid) == (self.device == "cpu")
        return dataset.y / 4


def _train_stub(data, *, training_ids, held_out_ids, seed, device):
    assert data.molecule_ids == training_ids
    assert seed == 7 and device in ("cpu", "cuda")
    return _SyntheticModel(held_out_ids, device)


def test_workload_uses_disjoint_ordered_traces_and_reports_four_phases(monkeypatch) -> None:
    monkeypatch.syspath_prepend(
        str(Path(__file__).resolve().parents[1] / "scripts" / "workstation")
    )
    import deep_check

    dataset = assemble_dataset(
        [str(i) for i in range(10)], np.ones((10, 3)), np.ones((10, 3)),
        np.arange(10) % 2, np.ones(10),
    )  # fmt: skip
    train, held = split_reference(dataset)
    train_stub = partial(
        _train_stub,
        training_ids=[dataset.molecule_ids[i] for i in train],
        held_out_ids=[dataset.molecule_ids[i] for i in held],
    )
    monkeypatch.setattr(deep_check, "train_classifier", train_stub)
    cuda = SimpleNamespace(
        synchronize=lambda: None, reset_peak_memory_stats=lambda: None,
        max_memory_allocated=lambda: 123,
    )  # fmt: skip
    numbers = deep_check.run_workload(dataset, cuda=cuda, training_seed=7)
    assert numbers["training_seed"] == 7
    assert numbers["cpu_roc_auc"] == numbers["cuda_roc_auc"] == 1.0
    assert numbers["agreement"] == 1.0
    assert numbers["max_score_difference"] == numbers["n_disagreements"] == 0
    for device in ("cpu", "cuda"):
        for phase in ("train", "predict"):
            assert numbers[f"{device}_{phase}_s"] >= 0
            assert numbers[f"{device}_{phase}_peak_bytes"] > 0


def test_device_agreement_compares_each_molecule_at_the_decision_boundary() -> None:
    cpu = np.array([0.25, 0.50, 0.99, 0.50])
    cuda = np.array([0.49, 0.49, 0.51, 0.50])
    comparison = compare_device_scores(cpu, cuda, n_samples=4)

    assert comparison.n_disagreements == 1
    assert comparison.agreement == 0.75
    assert comparison.max_score_difference == pytest.approx(0.48)


@pytest.mark.parametrize(
    ("cuda", "message"),
    [
        (np.array([0.5]), "one score per molecule"),
        (np.array([[0.5], [0.5]]), "one score per molecule"),
        (np.array([np.nan, 0.5]), "finite probabilities"),
        (np.array([np.inf, 0.5]), "finite probabilities"),
        (np.array([0.5, 1.1]), "finite probabilities"),
    ],
)
def test_device_agreement_rejects_invalid_scores(cuda: np.ndarray, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        compare_device_scores(np.array([0.5, 0.5]), cuda, n_samples=2)
