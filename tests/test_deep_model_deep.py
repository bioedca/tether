# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""CPU train-smoke for the M8 deep trace classifier (ADR-0047; PRD §9 M8, FR-ML).

Marked ``@pytest.mark.deep`` (module-level ``pytestmark``): these tests import torch (inside
:func:`tether.ml.deep.model.train_classifier`), so they run **only** in the isolated, optional
``deep/`` env on the non-required ``deep.yml`` CI leg, and are deselected from the base 3-OS
matrix (``-m "not ... and not deep"``). The file name matches ``deep.yml``'s
``tests/test_*deep*.py`` collection glob — enforced by ``tests/test_marker_contract.py``.

The smoke asserts the §9 M8 acceptance clause end to end on the shared label-store substrate:
a deep classifier *trains* on labeled traces (loss decreases, stays finite), *predicts*
calibrated per-sample probabilities that separate the two classes, and is *reproducible* for a
fixed seed on CPU. It uses small synthetic ``assemble_dataset`` arrays (no fixtures, no GPU), so
it is fast and hermetic.
"""

from __future__ import annotations

import numpy as np
import pytest

from tether.ml.deep import assemble_dataset
from tether.ml.deep.model import TrainedDeepClassifier, predict_proba, train_classifier

pytestmark = pytest.mark.deep


def _synthetic_dataset(*, n_per_class: int = 16, window_length: int = 64, seed: int = 0):
    """Two linearly-separable smFRET-like classes with variable trace lengths.

    Accept (label +1): high acceptor / low donor (high apparent FRET). Reject (label -1): low
    acceptor / high donor. Variable per-trace lengths (< window_length) exercise the zero-pad +
    validity mask + packed-sequence masking path. ``assemble_dataset`` per-trace-normalizes.
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
            if cls == 1:  # accept: bright acceptor, dim donor
                donor = 200.0 + noise_d
                acceptor = 800.0 + noise_a
            else:  # reject: bright donor, dim acceptor
                donor = 800.0 + noise_d
                acceptor = 200.0 + noise_a
            ids.append(f"cls{cls}-mol{k}")
            donors.append(donor)
            acceptors.append(acceptor)
            labels.append(label)
    y = np.array(labels, dtype=np.int8)
    sample_weight = np.ones(len(ids), dtype=np.float64)
    return assemble_dataset(ids, donors, acceptors, y, sample_weight, window_length=window_length)


def test_train_smoke_learns_and_predicts() -> None:
    """A short CPU training run reduces a finite loss and separates the two classes."""
    ds = _synthetic_dataset()
    assert ds.n_good == 16 and ds.n_bad == 16

    trained = train_classifier(
        ds, epochs=25, batch_size=8, conv_channels=16, lstm_hidden=16, seed=0
    )

    assert isinstance(trained, TrainedDeepClassifier)
    history = np.asarray(trained.history, dtype=float)
    assert history.shape == (25,)
    assert np.all(np.isfinite(history)), "training loss went non-finite"
    # It learned: final epoch loss is well below the first.
    assert history[-1] < history[0]

    proba = trained.predict_proba(ds)
    assert proba.shape == (ds.n_samples,)
    assert proba.dtype == np.float64
    assert np.all((proba >= 0.0) & (proba <= 1.0))

    # Accept-class mean probability clearly exceeds reject-class on separable data.
    accept_mean = proba[ds.y == 1].mean()
    reject_mean = proba[ds.y == 0].mean()
    assert accept_mean > reject_mean + 0.2
    # Thresholded train accuracy is high on separable classes.
    predicted = (proba >= 0.5).astype(np.int8)
    accuracy = float((predicted == ds.y).mean())
    assert accuracy >= 0.8, f"train accuracy too low: {accuracy}"


def test_training_is_reproducible_for_a_fixed_seed() -> None:
    """Same seed → identical CPU predictions (weight init + shuffle order are both seeded)."""
    ds = _synthetic_dataset()
    first = train_classifier(ds, epochs=10, batch_size=8, seed=7).predict_proba(ds)
    second = train_classifier(ds, epochs=10, batch_size=8, seed=7).predict_proba(ds)
    np.testing.assert_allclose(first, second, rtol=0, atol=1e-6)


def test_module_level_predict_proba_accepts_bare_model() -> None:
    """The module-level ``predict_proba`` also accepts the bare trained ``nn.Module``."""
    ds = _synthetic_dataset(n_per_class=8)
    trained = train_classifier(ds, epochs=5, batch_size=8, seed=0)
    from_result = predict_proba(trained, ds)
    from_bare = predict_proba(trained.model, ds)
    np.testing.assert_allclose(from_result, from_bare, rtol=0, atol=1e-6)


def test_train_classifier_rejects_bad_arguments() -> None:
    ds = _synthetic_dataset(n_per_class=4)
    with pytest.raises(ValueError, match="epochs"):
        train_classifier(ds, epochs=0)
    with pytest.raises(ValueError, match="kernel_size"):
        train_classifier(ds, kernel_size=4)  # even kernel desyncs same-length packing
