# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fine-tuning / transfer + classical-ranker reconciliation for the M8 deep classifier.

Marked ``@pytest.mark.deep`` (module-level ``pytestmark``): these tests import torch (inside
:func:`tether.ml.deep.model.fine_tune` / :func:`~tether.ml.deep.model.rank_by_deep_model`), so
they run **only** in the isolated, optional ``deep/`` env on the non-required ``deep.yml`` CI leg,
and are deselected from the base 3-OS matrix (``-m "not ... and not deep"``). The file name matches
``deep.yml``'s ``tests/test_*_deep.py`` suffix collection glob — enforced by
``tests/test_marker_contract.py``.

These cover the §9 M8 PR-4 clause "**fine-tuning improves a held-out metric without regressing the
CPU path**" (ADR-0047; PRD §7.5, FR-ML) end to end on small synthetic ``assemble_dataset`` arrays
(no fixtures, no GPU): fine-tuning a model pretrained on a **noisy** source onto clean target labels
raises the held-out accuracy; the base model is never mutated; the preprocessing contract is
enforced; ``freeze_conv`` freezes the CNN feature extractor; fine-tuning is reproducible; and the
deep model's ``P(accept)`` reconciles with the M5 classical ranker by feeding the *same*
never-auto-drop ``rank_by_score`` / ``precision_at_k`` substrate.

The synthetic arrays exercise the transfer *mechanism* — they are not reference/oracle data (the
real-data kinetics oracle is the separate kinSoftChallenge suite). Margins are deliberately
generous so the assertions survive the CPU-BLAS differences across the ``deep/`` env's ``cpu_mkl``
(linux/win) and ``cpu_generic`` (osx) PyTorch builds.
"""

from __future__ import annotations

import numpy as np
import pytest

from tether.ml.deep import assemble_dataset
from tether.ml.deep.model import fine_tune, rank_by_deep_model, train_classifier
from tether.ml.ranking import file_order_ranking, precision_at_k

pytestmark = pytest.mark.deep


def _dataset(
    *,
    n_per_class: int = 24,
    window_length: int = 64,
    seed: int = 0,
    label_noise: float = 0.0,
    sep: float = 0.8,
    sigma: float = 40.0,
    shuffle: bool = False,
):
    """Two smFRET-like classes at apparent-FRET ``sep`` / ``1 - sep``; optionally mislabel some.

    Accept (label +1): bright acceptor / dim donor (mean total-fraction ``sep``, so a high apparent
    FRET E = A/(D+A) ≈ ``sep``). Reject (label -1): the mirror image. ``sigma`` is the per-frame
    intensity noise: the default ``sep=0.8, sigma=40`` is an easy, well-separated task; a smaller
    ``sep`` with a larger ``sigma`` is a harder, overlapping one. ``label_noise`` flips that
    fraction of labels (a noisy provisional / classical label) — on the hard task a high-noise
    source drives the base model to chance, which fine-tuning on clean labels then improves upon.
    By default the accept class is emitted before the reject class; ``shuffle`` deterministically
    interleaves them so the file/extraction order is a *non-trivial* ranking baseline (not already
    sorted good-first). ``seed`` also namespaces the molecule ids so independently-drawn sets never
    collide. ``assemble_dataset`` per-trace-normalizes.
    """
    rng = np.random.default_rng(seed)
    ids: list[str] = []
    donors: list[np.ndarray] = []
    acceptors: list[np.ndarray] = []
    labels: list[int] = []
    hi, lo = 1000.0 * sep, 1000.0 * (1.0 - sep)
    for cls, label in ((1, +1), (0, -1)):
        for k in range(n_per_class):
            length = int(rng.integers(window_length // 2, window_length))
            noise_d = rng.normal(0.0, sigma, size=length)
            noise_a = rng.normal(0.0, sigma, size=length)
            if cls == 1:  # accept: bright acceptor, dim donor
                donor = lo + noise_d
                acceptor = hi + noise_a
            else:  # reject: bright donor, dim acceptor
                donor = hi + noise_d
                acceptor = lo + noise_a
            assigned = label
            if label_noise > 0.0 and rng.random() < label_noise:
                assigned = -label  # a mislabeled (noisy) example
            ids.append(f"s{seed}-cls{cls}-mol{k}")
            donors.append(donor)
            acceptors.append(acceptor)
            labels.append(assigned)
    if shuffle:
        perm = rng.permutation(len(ids))
        ids = [ids[i] for i in perm]
        donors = [donors[i] for i in perm]
        acceptors = [acceptors[i] for i in perm]
        labels = [labels[i] for i in perm]
    y = np.array(labels, dtype=np.int8)
    sample_weight = np.ones(len(ids), dtype=np.float64)
    return assemble_dataset(ids, donors, acceptors, y, sample_weight, window_length=window_length)


def _accuracy(trained, dataset) -> float:
    """Thresholded accuracy of ``trained`` on ``dataset`` (predict_proba >= 0.5 vs the label)."""
    proba = trained.predict_proba(dataset)
    predicted = (proba >= 0.5).astype(np.int8)
    return float((predicted == dataset.y).mean())


def test_fine_tune_improves_held_out_metric() -> None:
    """Fine-tuning a noisy-source model on clean target labels raises held-out accuracy (§9 M8).

    A **hard** (overlapping, ``sep=0.60``/``sigma=80``) task with a 40%-mislabeled source drives
    the pretrained base to chance on the clean held-out draw; fine-tuning on the clean target
    labels then lifts it to near-perfect. Calibrated to a comfortable ~0.48 gap (worst observed
    over 9 data × training seeds), so the margins survive the ``deep/`` env's cpu_mkl (linux/win)
    vs cpu_generic (osx) BLAS differences.
    """
    # Pretrain on a high-noise "provisional/classical" source -> a chance-level base on the target.
    source = _dataset(n_per_class=28, seed=1, label_noise=0.40, sep=0.60, sigma=80.0)
    target_train = _dataset(n_per_class=28, seed=2, label_noise=0.0, sep=0.60, sigma=80.0)
    target_val = _dataset(n_per_class=28, seed=3, label_noise=0.0, sep=0.60, sigma=80.0)  # held-out

    base = train_classifier(
        source, epochs=12, batch_size=8, conv_channels=16, lstm_hidden=16, seed=0
    )
    base_acc = _accuracy(base, target_val)

    tuned = fine_tune(base, target_train, epochs=25, batch_size=8, learning_rate=1e-3, seed=0)
    tuned_acc = _accuracy(tuned, target_val)

    assert tuned_acc >= base_acc + 0.1, (
        f"fine-tuning did not improve held-out accuracy: base={base_acc}, tuned={tuned_acc}"
    )
    assert tuned_acc >= 0.85, f"fine-tuned held-out accuracy too low: {tuned_acc}"
    # `history` is this fine-tune run's per-epoch loss, finite and length == epochs.
    history = np.asarray(tuned.history, dtype=float)
    assert history.shape == (25,) and np.all(np.isfinite(history))
    assert tuned.hyperparameters["fine_tuned"] is True
    assert tuned.hyperparameters["base_hyperparameters"]["epochs"] == 12


def test_fine_tune_does_not_mutate_the_base_model() -> None:
    """Fine-tuning deep-copies the base, so the original's predictions are unchanged."""
    ds = _dataset(n_per_class=16, seed=4)
    other = _dataset(n_per_class=16, seed=5)
    base = train_classifier(ds, epochs=8, batch_size=8, seed=0)

    before = base.predict_proba(ds)
    fine_tune(base, other, epochs=10, batch_size=8, learning_rate=1e-2, seed=0)
    after = base.predict_proba(ds)

    np.testing.assert_allclose(before, after, rtol=0, atol=1e-6)


def test_fine_tune_is_reproducible_for_a_fixed_seed() -> None:
    """Same seed -> identical fine-tuned CPU predictions."""
    base = train_classifier(_dataset(n_per_class=16, seed=6), epochs=6, batch_size=8, seed=0)
    target = _dataset(n_per_class=16, seed=7)
    first = fine_tune(base, target, epochs=8, batch_size=8, seed=3).predict_proba(target)
    second = fine_tune(base, target, epochs=8, batch_size=8, seed=3).predict_proba(target)
    np.testing.assert_allclose(first, second, rtol=0, atol=1e-6)


def test_fine_tune_freeze_conv_freezes_the_feature_extractor() -> None:
    """``freeze_conv`` leaves the CNN weights untouched while the LSTM head still adapts."""
    ds = _dataset(n_per_class=16, seed=8)
    target = _dataset(n_per_class=16, seed=9)
    base = train_classifier(ds, epochs=8, batch_size=8, conv_channels=16, lstm_hidden=16, seed=0)

    tuned = fine_tune(
        base, target, epochs=10, batch_size=8, learning_rate=1e-2, freeze_conv=True, seed=0
    )

    base_conv = [p.detach().cpu().numpy() for p in base.model.conv.parameters()]
    tuned_conv = [p.detach().cpu().numpy() for p in tuned.model.conv.parameters()]
    assert len(base_conv) == len(tuned_conv) and base_conv, "expected conv parameters to compare"
    for before, after in zip(base_conv, tuned_conv, strict=False):
        np.testing.assert_allclose(before, after, rtol=0, atol=0)  # frozen: bit-identical

    # The classification head is not frozen, so at least one head weight moved.
    base_head = base.model.head.weight.detach().cpu().numpy()
    tuned_head = tuned.model.head.weight.detach().cpu().numpy()
    assert not np.allclose(base_head, tuned_head), "the trainable head did not update"


def test_fine_tune_unfreezes_a_previously_frozen_base() -> None:
    """``freeze_conv=False`` trains every weight even if the base came from a frozen fine-tune.

    A model produced with ``freeze_conv=True`` carries ``requires_grad=False`` on its conv layers;
    deep-copying it must not silently keep them frozen when the next fine-tune asks to train
    everything (fine_tune resets to a fully-trainable copy before optionally re-freezing).
    """
    ds = _dataset(n_per_class=16, seed=16)
    target = _dataset(n_per_class=16, seed=17)
    base = train_classifier(ds, epochs=6, batch_size=8, conv_channels=16, lstm_hidden=16, seed=0)
    frozen = fine_tune(base, target, epochs=6, batch_size=8, learning_rate=1e-2, freeze_conv=True)

    thawed = fine_tune(
        frozen, target, epochs=8, batch_size=8, learning_rate=1e-2, freeze_conv=False
    )

    frozen_conv = [p.detach().cpu().numpy() for p in frozen.model.conv.parameters()]
    thawed_conv = [p.detach().cpu().numpy() for p in thawed.model.conv.parameters()]
    assert any(
        not np.allclose(before, after)
        for before, after in zip(frozen_conv, thawed_conv, strict=True)
    ), "freeze_conv=False did not re-enable training of the previously-frozen conv layers"


def test_fine_tune_rejects_mismatched_preprocessing() -> None:
    """Fine-tuning on a differently-preprocessed dataset is rejected, not silently corrupting."""
    base = train_classifier(_dataset(window_length=64, seed=10), epochs=3, batch_size=8, seed=0)
    other = _dataset(window_length=48, seed=11)  # a different window => incompatible contract
    with pytest.raises(ValueError, match="does not match the trained classifier"):
        fine_tune(base, other)


def test_fine_tune_rejects_bad_arguments() -> None:
    base = train_classifier(_dataset(n_per_class=8, seed=12), epochs=3, batch_size=8, seed=0)
    target = _dataset(n_per_class=8, seed=13)
    with pytest.raises(ValueError, match="epochs"):
        fine_tune(base, target, epochs=0)
    with pytest.raises(ValueError, match="learning_rate"):
        fine_tune(base, target, learning_rate=0.0)


def test_rank_by_deep_model_is_a_never_auto_drop_permutation() -> None:
    """The deep ranking keeps every molecule (a permutation) and orders best (highest P) first."""
    ds = _dataset(n_per_class=16, seed=14)
    trained = train_classifier(
        ds, epochs=20, batch_size=8, conv_channels=16, lstm_hidden=16, seed=0
    )

    ranked = rank_by_deep_model(trained, ds)

    # Never-auto-drop: exactly the input molecules, each once.
    assert set(ranked.molecule_ids) == set(ds.molecule_ids)
    assert len(ranked.molecule_ids) == ds.n_samples
    # Best-first: the top-ranked molecule is the highest-probability one.
    proba = trained.predict_proba(ds)
    top_idx = ds.molecule_ids.index(ranked.molecule_ids[0])
    assert proba[top_idx] == pytest.approx(float(proba.max()))


def test_deep_and_classical_rankers_share_the_precision_at_k_substrate() -> None:
    """The deep ranking is scored by the *same* precision@k as the M5 classical ranker.

    Reconciliation: the deep model's P(accept) feeds the model-agnostic ``rank_by_score`` and is
    measured by ``precision_at_k`` — the very substrate the gradient-boosting ``QualityRanker``
    plugs into (PRD §7.5), so the two rankers' outputs are directly comparable on one objective.
    The fixture is ``shuffle``d so the file/extraction order is a *non-trivial* baseline (not
    already sorted good-first); on separable data the deep ranking then shows a genuine precision@k
    **uplift** over it — the M5 success signal, demonstrated through the deep scorer.
    """
    ds = _dataset(n_per_class=16, seed=15, shuffle=True)
    trained = train_classifier(
        ds, epochs=20, batch_size=8, conv_channels=16, lstm_hidden=16, seed=0
    )
    is_good = {
        mid: bool(label == 1) for mid, label in zip(ds.molecule_ids, ds.y.tolist(), strict=True)
    }
    k = ds.n_good  # the review budget = the number of good traces

    ranked = rank_by_deep_model(trained, ds)
    deep_relevance = [is_good[mid] for mid in ranked.molecule_ids]
    deep_p = precision_at_k(deep_relevance, k)

    baseline = file_order_ranking(ds.molecule_ids)
    baseline_relevance = [is_good[mid] for mid in baseline.molecule_ids]
    baseline_p = precision_at_k(baseline_relevance, k)

    # The shuffled file order is genuinely non-trivial (not already perfect), so "deep >= baseline"
    # is a real uplift, not a hidden "deep must be perfect" bar.
    assert baseline_p < 1.0, "fixture bug: the shuffled file-order baseline should not be perfect"
    assert deep_p > baseline_p  # a real precision@k uplift on the shared objective
    assert deep_p >= 0.8  # and on separable data the deep ranking surfaces good traces well
