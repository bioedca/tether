# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""GPU train-smoke for the M8 deep trace classifier (ADR-0047; PRD §9 M8, FR-ML).

The CUDA counterpart of the CPU :mod:`tests.test_deep_model_deep` smoke. It is
``@pytest.mark.deep`` (so the base 3-OS matrix deselects it, ``-m "not ... and not
deep"``) *and* self-skips unless a CUDA device is present. Consequences:

* base env (no torch): ``importorskip`` skips the module at import — never an error;
* CPU ``deep.yml`` leg (torch, no GPU): the ``skipif`` skips both tests cleanly;
* non-required ``deep-gpu.yml`` leg on a real CUDA box: it exercises the
  ``device="cuda"`` training + inference path end to end.

The file name matches ``deep.yml`` / ``deep-gpu.yml``'s ``tests/test_*_deep.py``
suffix collection glob — enforced by :mod:`tests.test_marker_contract`.
"""

from __future__ import annotations

import numpy as np
import pytest

from tether.ml.deep import assemble_dataset
from tether.ml.deep.model import TrainedDeepClassifier, train_classifier

# Imported here (not at top) so the module *skips* rather than *errors* during
# base-matrix collection, where the isolated deep/ torch stack is absent.
torch = pytest.importorskip("torch")

pytestmark = [
    pytest.mark.deep,
    pytest.mark.skipif(not torch.cuda.is_available(), reason="requires a CUDA-capable GPU"),
]


def _synthetic_dataset(*, n_per_class: int = 16, window_length: int = 64, seed: int = 0):
    """Two linearly-separable smFRET-like classes with variable trace lengths.

    Accept (label +1): bright acceptor / dim donor (high apparent FRET). Reject
    (label -1): the reverse. Mirrors the CPU smoke's fixture so the GPU path is
    checked on the same separable signal.
    """
    rng = np.random.default_rng(seed)
    ids: list[str] = []
    donors: list[np.ndarray] = []
    acceptors: list[np.ndarray] = []
    labels: list[int] = []
    for cls, label in ((1, +1), (0, -1)):
        for k in range(n_per_class):
            length = int(rng.integers(window_length // 2, window_length))
            noise_d = rng.normal(0.0, 40.0, size=length)
            noise_a = rng.normal(0.0, 40.0, size=length)
            donor = (200.0 if cls == 1 else 800.0) + noise_d
            acceptor = (800.0 if cls == 1 else 200.0) + noise_a
            ids.append(f"cls{cls}-mol{k}")
            donors.append(donor)
            acceptors.append(acceptor)
            labels.append(label)
    y = np.array(labels, dtype=np.int8)
    sample_weight = np.ones(len(ids), dtype=np.float64)
    return assemble_dataset(ids, donors, acceptors, y, sample_weight, window_length=window_length)


def test_trains_on_cuda_and_returns_host_predictions() -> None:
    """Training on ``device="cuda"`` lands the model on the GPU and predicts on the host."""
    ds = _synthetic_dataset()
    trained = train_classifier(
        ds, epochs=25, batch_size=8, conv_channels=16, lstm_hidden=16, seed=0, device="cuda"
    )
    assert isinstance(trained, TrainedDeepClassifier)
    # The trained parameters actually live on the GPU, and provenance records it.
    assert next(trained.model.parameters()).device.type == "cuda"
    assert trained.hyperparameters["device"] == "cuda"

    history = np.asarray(trained.history, dtype=float)
    assert history.shape == (25,)
    assert np.all(np.isfinite(history)), "training loss went non-finite on cuda"
    assert history[-1] < history[0], "cuda training did not reduce the loss"

    # predict_proba resolves the training device (cuda) yet returns host float64
    # in [0, 1] — the .detach().cpu().numpy() contract must hold on the GPU path.
    proba = trained.predict_proba(ds)
    assert isinstance(proba, np.ndarray)
    assert proba.dtype == np.float64
    assert proba.shape == (ds.n_samples,)
    assert np.all((proba >= 0.0) & (proba <= 1.0))
    # Separable classes are separated.
    assert proba[ds.y == 1].mean() > proba[ds.y == 0].mean() + 0.2


def test_cuda_and_cpu_training_agree_on_the_decision() -> None:
    """A GPU-trained and a CPU-trained model (same seed) both separate the classes.

    Cross-device kernels are not bit-identical (cuDNN vs CPU BLAS), so this asserts
    the *decision* agrees — both split accept from reject — not exact probability
    equality (which only the CPU-vs-CPU reproducibility test in the sibling smoke
    is entitled to).
    """
    ds = _synthetic_dataset(n_per_class=12, seed=3)
    on_gpu = train_classifier(ds, epochs=15, batch_size=8, seed=3, device="cuda").predict_proba(ds)
    on_cpu = train_classifier(ds, epochs=15, batch_size=8, seed=3, device="cpu").predict_proba(ds)
    for proba in (on_gpu, on_cpu):
        assert proba[ds.y == 1].mean() > proba[ds.y == 0].mean() + 0.2
