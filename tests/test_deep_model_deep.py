# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""CPU train-smoke for the M8 deep trace classifier (ADR-0047; PRD §9 M8, FR-ML).

Marked ``@pytest.mark.deep`` (module-level ``pytestmark``): these tests import torch (inside
:func:`tether.ml.deep.model.train_classifier`), so they run **only** in the isolated, optional
``deep/`` env on the non-required ``deep.yml`` CI leg, and are deselected from the base 3-OS
matrix (``-m "not ... and not deep"``). The file name matches ``deep.yml``'s
``tests/test_*_deep.py`` suffix collection glob — enforced by ``tests/test_marker_contract.py``.

What this file proves, and what it does not (#752, ML-U2). It is a **smoke on synthetic
arrays**: small ``assemble_dataset`` sets of two well-separated classes (no fixtures, no GPU), so
it is fast and hermetic. On them a classifier trains (the loss falls and stays finite), returns
per-sample probabilities in ``[0, 1]`` that separate the classes on the training data, and is
reproducible for a fixed seed on CPU. Two mechanisms are pinned with a known answer: the
per-sample weights steer the loss (a weight-ignoring loss fails), and the padded frames are not
read (a model that reads them fails). One test trains from a real ``.tether`` store through
:func:`~tether.project.deep_dataset.build_deep_dataset`. Not measured here: calibration (no
reliability or Brier check), accuracy on held-out or real data, or agreement with any reference
model; DeepFRET and Deep-LASI are cited for the architecture only. Those checks are #759 and #760.
"""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from tether.ml.deep import assemble_dataset
from tether.ml.deep.model import TrainedDeepClassifier, predict_proba, train_classifier

pytestmark = pytest.mark.deep


def _synthetic_dataset(
    *, n_per_class: int = 16, window_length: int = 64, seed: int = 0, max_length: int | None = None
):
    """Two linearly-separable smFRET-like classes with variable trace lengths.

    Accept (label +1): high acceptor / low donor (high apparent FRET). Reject (label -1): low
    acceptor / high donor. Variable per-trace lengths (< window_length, or < ``max_length``)
    run the zero-pad + validity mask + packed-sequence path; that the padding is not read is
    asserted by ``test_padded_frames_do_not_change_the_prediction``. ``assemble_dataset``
    per-trace-normalizes.
    """
    rng = np.random.default_rng(seed)
    top = window_length if max_length is None else max_length
    ids: list[str] = []
    donors: list[np.ndarray] = []
    acceptors: list[np.ndarray] = []
    labels: list[int] = []
    for cls, label in ((1, +1), (0, -1)):
        for k in range(n_per_class):
            length = int(rng.integers(top // 2, top))
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
    """A short CPU training run reduces a finite loss and separates the two classes.

    Measured on the training data, so this is a smoke: it shows the loop runs and fits a trivial
    set, not that the classifier generalizes or that its probabilities are calibrated.
    """
    ds = _synthetic_dataset()
    assert ds.n_good == 16 and ds.n_bad == 16

    trained = train_classifier(
        ds, epochs=25, batch_size=8, conv_channels=16, lstm_hidden=16, seed=0
    )

    assert isinstance(trained, TrainedDeepClassifier)
    history = np.asarray(trained.history, dtype=float)
    assert history.shape == (25,)
    assert np.all(np.isfinite(history)), "training loss went non-finite"
    # The loss fell over the run (a smoke bound, not a convergence criterion).
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


def test_train_rejects_zero_length_traces() -> None:
    """A trace with no valid frames is rejected, never fabricated into a synthetic frame."""
    donors = [np.array([], dtype=float), 800.0 + np.zeros(20)]
    acceptors = [np.array([], dtype=float), 200.0 + np.zeros(20)]
    y = np.array([1, -1], dtype=np.int8)
    w = np.ones(2, dtype=np.float64)
    ds = assemble_dataset(["empty", "ok"], donors, acceptors, y, w, window_length=32)
    assert ds.lengths[0] == 0  # the substrate honestly records the empty trace
    with pytest.raises(ValueError, match="zero valid frames"):
        train_classifier(ds, epochs=2, batch_size=2)


def test_predict_proba_rejects_mismatched_preprocessing() -> None:
    """Scoring a dataset whose preprocessing differs from training is rejected, not silent-wrong."""
    trained = train_classifier(_synthetic_dataset(n_per_class=6), epochs=3, batch_size=8, seed=0)
    # Same molecules but a different window length => incompatible preprocessing contract.
    other = _synthetic_dataset(n_per_class=6, window_length=48)
    with pytest.raises(ValueError, match="does not match the trained classifier"):
        trained.predict_proba(other)


@pytest.mark.parametrize(
    "change",
    [
        {"normalization": "none"},
        {"intensity_quantity": "raw"},
        {"channels": ("acceptor", "donor")},
    ],
    ids=["normalization", "intensity_quantity", "channel_order"],
)
def test_predict_proba_rejects_a_same_shape_preprocessing_mismatch(change) -> None:
    """A dataset whose tensor shape fits but whose preprocessing differs is rejected (ML-U5).

    The ``window_length`` case above also changes the tensor shape, so the model could fail on
    its own; these three keep the shape and only the contract check stands between them and a
    silently wrong score.
    """
    ds = _synthetic_dataset(n_per_class=6)
    trained = train_classifier(ds, epochs=2, batch_size=8, seed=0)
    other = dataclasses.replace(ds, **change)
    assert other.X.shape == ds.X.shape
    with pytest.raises(ValueError, match="does not match the trained classifier"):
        trained.predict_proba(other)


def test_unidirectional_dropout_model_trains_and_predict_proba_takes_a_device() -> None:
    """``bidirectional=False``, ``dropout > 0`` and ``predict_proba(device=...)`` run (ML-U5)."""
    ds = _synthetic_dataset(n_per_class=8)
    trained = train_classifier(
        ds, epochs=3, batch_size=8, bidirectional=False, dropout=0.25, seed=0
    )
    assert trained.model.head.in_features == trained.hyperparameters["lstm_hidden"]
    proba = predict_proba(trained, ds, device="cpu")
    assert proba.shape == (ds.n_samples,)
    assert np.all((proba >= 0.0) & (proba <= 1.0))
    # Eval mode switches dropout off, so two predictions are identical.
    np.testing.assert_array_equal(proba, predict_proba(trained, ds, device="cpu"))


def _conflicting_weighted_dataset(*, heavy_accepts_high_fret: bool, n_per_class: int = 16):
    """Every trace twice, with opposite labels: one copy at weight 1, one at weight 1e-3.

    The heavy copies label one class accept and the other reject; the light copies say the
    reverse. Only the weights tell the two stories apart: a loss that ignores them sees each trace
    labelled 50/50 and can only predict about 0.5. The light weight is 1e-3, not 0, so no batch is
    all-zero weight (that batch takes the unweighted fallback, which would train the light labels
    at full weight).
    """
    rng = np.random.default_rng(42)
    ids, donors, acceptors, labels, weights = [], [], [], [], []
    for high_fret in (True, False):
        for k in range(n_per_class):
            length = int(rng.integers(32, 64))
            donor = (200.0 if high_fret else 800.0) + rng.normal(0.0, 40.0, size=length)
            acceptor = (800.0 if high_fret else 200.0) + rng.normal(0.0, 40.0, size=length)
            heavy = +1 if high_fret == heavy_accepts_high_fret else -1
            for copy_label, weight in ((heavy, 1.0), (-heavy, 1e-3)):
                ids.append(f"hi{int(high_fret)}-mol{k}-w{weight:g}")
                donors.append(donor)
                acceptors.append(acceptor)
                labels.append(copy_label)
                weights.append(weight)
    return assemble_dataset(
        ids,
        donors,
        acceptors,
        np.array(labels, dtype=np.int8),
        np.array(weights, dtype=np.float64),
        window_length=64,
    )


@pytest.mark.parametrize("heavy_accepts_high_fret", [True, False])
def test_sample_weights_decide_between_conflicting_labels(heavy_accepts_high_fret) -> None:
    """The prediction follows the heavy labels, so the loss is weighted (ML-U3, PRD §9 M8).

    The two parametrizations train on the same traces with the same labels; only which copy is
    heavy changes, and the decision flips with it.
    """
    ds = _conflicting_weighted_dataset(heavy_accepts_high_fret=heavy_accepts_high_fret)
    trained = train_classifier(
        ds, epochs=25, batch_size=8, conv_channels=16, lstm_hidden=16, seed=0
    )
    proba = trained.predict_proba(ds)
    heavy = ds.sample_weight == 1.0
    heavy_label = ds.y[heavy]
    p = proba[heavy]
    assert p[heavy_label == 1].mean() > 0.8, "accept-heavy traces are not predicted accept"
    assert p[heavy_label == 0].mean() < 0.2, "reject-heavy traces are not predicted reject"


def test_padded_frames_do_not_change_the_prediction() -> None:
    """Values in the padded frames do not reach the output (ML-U4, PRD §9 M8 "never data").

    The padding beyond each trace is overwritten with random numbers. The frames that the
    convolutions of the last valid frames read (the receptive-field halo of
    ``num_conv_layers * (kernel_size - 1) // 2`` frames) stay zero: they are the substrate's zero
    padding and feed the valid frames by design. Everything after the halo must be ignored, so
    the probabilities must not change.
    """
    kernel_size, num_conv_layers = 5, 2
    halo = num_conv_layers * (kernel_size - 1) // 2
    ds = _synthetic_dataset(n_per_class=12, window_length=96, max_length=64)
    trained = train_classifier(
        ds,
        epochs=5,
        batch_size=8,
        kernel_size=kernel_size,
        num_conv_layers=num_conv_layers,
        seed=0,
    )
    rng = np.random.default_rng(1)
    x = ds.X.copy()
    for i, length in enumerate(ds.lengths.tolist()):
        assert length + halo < ds.window_length, "fixture bug: no padding left to overwrite"
        x[i, :, length + halo :] = rng.uniform(0.0, 1.0, size=x[i, :, length + halo :].shape)
    scrambled = dataclasses.replace(ds, X=x)
    np.testing.assert_allclose(
        trained.predict_proba(scrambled), trained.predict_proba(ds), rtol=0, atol=1e-6
    )


def test_trains_from_a_store_through_build_deep_dataset(tmp_path) -> None:
    """Store -> ``build_deep_dataset`` -> ``train_classifier`` runs in the deep env (ML-U2).

    The other deep tests start from synthetic ``assemble_dataset`` arrays. This one writes a real
    ``.tether`` with h5py, labels it, computes the features and trains from the store, so the
    path the PRD names ("trains on the shared label store") is run end to end here at least once.
    """
    pytest.importorskip("h5py")
    from _analysis_store import build_store_with_channels  # noqa: PLC0415
    from tether.project.deep_dataset import build_deep_dataset  # noqa: PLC0415
    from tether.project.features import compute_features  # noqa: PLC0415
    from tether.project.labels import accept, reject  # noqa: PLC0415

    rng = np.random.default_rng(0)
    n, t = 16, 40
    high = np.arange(n) % 2 == 0
    e = np.where(high, 0.8, 0.2)[:, None]
    donor = 1000.0 * (1.0 - e) + rng.normal(0.0, 30.0, (n, t))
    acceptor = 1000.0 * e + rng.normal(0.0, 30.0, (n, t))
    proj, keys = build_store_with_channels(tmp_path, donor, acceptor, name="deep-train.tether")
    for key, is_high in zip(keys, high.tolist(), strict=True):
        (accept if is_high else reject)(proj.path, key)
    compute_features(proj)

    ds = build_deep_dataset(proj, window_length=48)
    assert ds.n_good == n // 2 and ds.n_bad == n // 2
    trained = train_classifier(ds, epochs=30, batch_size=4, conv_channels=8, lstm_hidden=8, seed=0)
    proba = trained.predict_proba(ds)
    assert np.all(np.isfinite(trained.history))
    assert proba[ds.y == 1].mean() > proba[ds.y == 0].mean() + 0.2
    assert float(((proba >= 0.5).astype(np.int8) == ds.y).mean()) >= 0.9
