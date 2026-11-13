# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The torch-free argument and preprocessing checks of the M8 deep classifier (#752, ML-U5).

:func:`tether.ml.deep.model.train_classifier` and :func:`~tether.ml.deep.model.fine_tune`
validate their arguments, and ``fine_tune`` its preprocessing contract, **before** the lazy torch
import. These tests run that code in the required base matrix, where torch is absent. Each test
also blocks the torch module, so a check that moved after the import would raise ``ImportError``
here instead of ``ValueError`` and fail. The torch-dependent cases stay in the deep tier
(``tests/test_deep_model_deep.py``, ``tests/test_deep_finetune_deep.py``).
"""

from __future__ import annotations

import sys

import numpy as np
import pytest

import tether.ml.deep as deep_pkg
from tether.ml.deep import assemble_dataset
from tether.ml.deep.model import TrainedDeepClassifier, fine_tune, train_classifier

_TORCH_MODULE = "tether.ml.deep._torch_model"


@pytest.fixture(autouse=True)
def _no_torch(monkeypatch):
    """Make the lazy ``from tether.ml.deep import _torch_model`` raise ``ImportError``."""
    monkeypatch.delattr(deep_pkg, "_torch_model", raising=False)
    monkeypatch.setitem(sys.modules, _TORCH_MODULE, None)


def _dataset(**kwargs):
    rng = np.random.default_rng(0)
    donors = [200.0 + rng.normal(0.0, 10.0, 20), 800.0 + rng.normal(0.0, 10.0, 20)]
    acceptors = [800.0 + rng.normal(0.0, 10.0, 20), 200.0 + rng.normal(0.0, 10.0, 20)]
    y = np.array([1, -1], dtype=np.int8)
    w = np.ones(2, dtype=np.float64)
    return assemble_dataset(["a", "b"], donors, acceptors, y, w, window_length=32, **kwargs)


def _base(dataset) -> TrainedDeepClassifier:
    """A trained-classifier record with no model: ``fine_tune`` must refuse before it needs one."""
    return TrainedDeepClassifier(
        model=None,
        channels=dataset.channels,
        n_channels=dataset.n_channels,
        window_length=dataset.window_length,
        normalization=dataset.normalization,
        intensity_quantity=dataset.intensity_quantity,
        history=(),
        hyperparameters={"device": "cpu"},
    )


def test_the_torch_block_is_in_force() -> None:
    """Anchor: valid arguments reach the lazy import, which the fixture makes fail."""
    ds = _dataset()
    with pytest.raises(ImportError):
        train_classifier(ds, epochs=1)
    with pytest.raises(ImportError):
        fine_tune(_base(ds), ds, epochs=1)


@pytest.mark.parametrize(
    ("kwargs", "name"),
    [
        ({"epochs": 0}, "epochs"),
        ({"batch_size": 0}, "batch_size"),
        ({"learning_rate": 0.0}, "learning_rate"),
        ({"learning_rate": -1e-3}, "learning_rate"),
        ({"conv_channels": 0}, "conv_channels"),
        ({"num_conv_layers": 0}, "num_conv_layers"),
        ({"lstm_hidden": 0}, "lstm_hidden"),
        ({"kernel_size": 4}, "kernel_size"),
        ({"kernel_size": -1}, "kernel_size"),
    ],
)
def test_train_classifier_rejects_a_bad_argument_without_torch(kwargs, name) -> None:
    with pytest.raises(ValueError, match=name):
        train_classifier(_dataset(), **kwargs)


@pytest.mark.parametrize(
    ("kwargs", "name"),
    [
        ({"epochs": 0}, "epochs"),
        ({"batch_size": 0}, "batch_size"),
        ({"learning_rate": 0.0}, "learning_rate"),
    ],
)
def test_fine_tune_rejects_a_bad_argument_without_torch(kwargs, name) -> None:
    ds = _dataset()
    with pytest.raises(ValueError, match=name):
        fine_tune(_base(ds), ds, **kwargs)


@pytest.mark.parametrize(
    "target_kwargs",
    [
        {"normalization": "none"},
        {"intensity_quantity": "raw"},
        {"channels": ("acceptor", "donor")},
        {"channels": ("donor",)},
    ],
    ids=["normalization", "intensity_quantity", "channel_order", "channel_count"],
)
def test_fine_tune_rejects_a_preprocessing_mismatch_without_torch(target_kwargs) -> None:
    """A target built with other preprocessing is refused, also when the tensor shape fits."""
    base = _base(_dataset())
    target = _dataset(**target_kwargs)
    with pytest.raises(ValueError, match="does not match the trained classifier"):
        fine_tune(base, target)
