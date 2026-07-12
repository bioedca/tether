# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Base-importable public API for the M8 deep trace classifier (ADR-0047; PRD §9 M8, FR-ML).

Torch is imported **lazily, inside the functions** here (never at module top), so importing
this module in the base env is safe and pulls **no** deep-learning framework — the load-bearing
"deep classifier is optional; CPU base app unaffected" invariant (§9 M8). The torch
``nn.Module`` + ``Dataset`` live in the sibling private module :mod:`tether.ml.deep._torch_model`,
imported only from within these functions.

Model (DeepFRET/Deep-LASI-style [Thomsen2020][Wanninger2023]): a 1-D CNN over the
``(donor, acceptor)`` intensity channels feeds a (bi)LSTM whose final valid-frame hidden state
(padded frames excluded via packed sequences — never treated as data) is mapped to one
accept/reject logit. Training uses a **per-sample-weighted** BCE so the M5 cold-start
provisional weights carry through (§7.5). LSTM trace idealization/classification for smFRET
follows Kin-SiM [Zhang2025]; CNN trace selection follows AutoSiM [Li2020].

The framework-agnostic input tensors come from :func:`tether.ml.deep.dataset.assemble_dataset`
(or the store-backed :func:`tether.project.deep_dataset.build_deep_dataset`); this module is the
torch consumer of that substrate. Every tunable default below is registered in **PRD §11.2**
("Deep-classifier model + training"), the single source of truth (NFR-REPRO).

References
----------
[Thomsen2020] Thomsen et al. "DeepFRET, a software for rapid and automated single-molecule
    FRET data classification using deep learning." eLife (2020) — CNN non-ALEX DD+DA input.
[Wanninger2023] Wanninger et al. "Deep-LASI: deep-learning assisted, single-molecule imaging
    analysis of multi-color DNA origami structures." Nature Communications (2023).
[Zhang2025] Zhang et al. "Pre-trained Deep Neural Network Kin-SiM for Single-Molecule FRET
    Trace Idealization." The Journal of Physical Chemistry B (2025) — LSTM trace idealization.
[Li2020] Li, Zhang, Johnson-Buck & Walter. "Automatic classification and segmentation of
    single-molecule fluorescence time traces with deep learning." Nature Communications (2020)
    — AutoSiM CNN trace selection.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from tether.ml.deep.dataset import DEFAULT_SPLIT_SEED  # dependency-free import

if TYPE_CHECKING:
    from tether.ml.deep.dataset import DeepTraceDataset

# --- Architecture defaults (PRD §11.2 "Deep-classifier model + training") ---
#: Convolutional feature channels per 1-D conv layer.
DEFAULT_CONV_CHANNELS = 32
#: Convolution kernel width (frames). MUST be odd so symmetric padding keeps the "same" length,
#: which keeps the conv-feature length aligned with the per-sample valid lengths for packing.
DEFAULT_KERNEL_SIZE = 5
#: Number of stacked (Conv1d + ReLU) layers.
DEFAULT_NUM_CONV_LAYERS = 2
#: LSTM hidden size (per direction).
DEFAULT_LSTM_HIDDEN = 32
#: Bidirectional LSTM (summarizes the trace from both ends).
DEFAULT_BIDIRECTIONAL = True
#: Dropout on the pooled trace summary before the classification head.
DEFAULT_DROPOUT = 0.0

# --- Training defaults (PRD §11.2) ---
DEFAULT_EPOCHS = 20
DEFAULT_BATCH_SIZE = 32
DEFAULT_LEARNING_RATE = 1e-3
#: CPU is the base/default device; the optional CUDA build selects "cuda" on a GPU box.
DEFAULT_DEVICE = "cpu"

__all__ = [
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_BIDIRECTIONAL",
    "DEFAULT_CONV_CHANNELS",
    "DEFAULT_DEVICE",
    "DEFAULT_DROPOUT",
    "DEFAULT_EPOCHS",
    "DEFAULT_KERNEL_SIZE",
    "DEFAULT_LEARNING_RATE",
    "DEFAULT_LSTM_HIDDEN",
    "DEFAULT_NUM_CONV_LAYERS",
    "TrainedDeepClassifier",
    "predict_proba",
    "train_classifier",
]


@dataclass(frozen=True)
class TrainedDeepClassifier:
    """A trained deep trace classifier plus its self-describing build provenance (NFR-REPRO).

    ``model`` is the trained ``torch.nn.Module``; it is typed ``Any`` so this container stays
    importable in the torch-free base env (a caller only touches it via :meth:`predict_proba`,
    which lazily loads torch). ``history`` is the per-epoch weighted training loss;
    ``hyperparameters`` records every §11.2 tunable used, so a stored result is reproducible.
    """

    model: Any
    channels: tuple[str, ...]
    n_channels: int
    window_length: int
    normalization: str
    history: tuple[float, ...]
    hyperparameters: dict[str, Any]

    def predict_proba(
        self, dataset: DeepTraceDataset, *, batch_size: int = DEFAULT_BATCH_SIZE
    ) -> Any:
        """Per-sample accept probability for ``dataset`` — see module :func:`predict_proba`."""
        return predict_proba(self, dataset, batch_size=batch_size)


def train_classifier(
    dataset: DeepTraceDataset,
    *,
    epochs: int = DEFAULT_EPOCHS,
    batch_size: int = DEFAULT_BATCH_SIZE,
    learning_rate: float = DEFAULT_LEARNING_RATE,
    conv_channels: int = DEFAULT_CONV_CHANNELS,
    kernel_size: int = DEFAULT_KERNEL_SIZE,
    num_conv_layers: int = DEFAULT_NUM_CONV_LAYERS,
    lstm_hidden: int = DEFAULT_LSTM_HIDDEN,
    bidirectional: bool = DEFAULT_BIDIRECTIONAL,
    dropout: float = DEFAULT_DROPOUT,
    seed: int = DEFAULT_SPLIT_SEED,
    device: str = DEFAULT_DEVICE,
) -> TrainedDeepClassifier:
    """Train a 1-D CNN/LSTM accept/reject classifier on a :class:`DeepTraceDataset`.

    Lazily imports torch (:mod:`tether.ml.deep._torch_model`), so calling this needs the optional
    ``deep/`` env; merely importing this module does not. Determinism: fixed ``seed`` reproduces
    weight init + shuffle order on CPU. Returns a :class:`TrainedDeepClassifier` carrying the
    trained model, per-epoch loss ``history``, and the full §11.2 hyperparameter record.

    Raises
    ------
    ValueError
        Empty dataset, non-positive ``epochs`` / ``batch_size`` / ``learning_rate`` /
        ``conv_channels`` / ``num_conv_layers`` / ``lstm_hidden``, or an even ``kernel_size``
        (an even kernel cannot be symmetric-padded to a "same" length, desyncing packing).
    """
    if dataset.n_samples == 0:
        raise ValueError("cannot train a deep classifier on an empty dataset")
    if epochs <= 0:
        raise ValueError(f"epochs must be positive, got {epochs}")
    if batch_size <= 0:
        raise ValueError(f"batch_size must be positive, got {batch_size}")
    if learning_rate <= 0.0:
        raise ValueError(f"learning_rate must be positive, got {learning_rate}")
    if conv_channels <= 0:
        raise ValueError(f"conv_channels must be positive, got {conv_channels}")
    if num_conv_layers <= 0:
        raise ValueError(f"num_conv_layers must be positive, got {num_conv_layers}")
    if lstm_hidden <= 0:
        raise ValueError(f"lstm_hidden must be positive, got {lstm_hidden}")
    if kernel_size <= 0 or kernel_size % 2 == 0:
        raise ValueError(f"kernel_size must be a positive odd integer, got {kernel_size}")

    hyperparameters: dict[str, Any] = {
        "epochs": epochs,
        "batch_size": batch_size,
        "learning_rate": learning_rate,
        "conv_channels": conv_channels,
        "kernel_size": kernel_size,
        "num_conv_layers": num_conv_layers,
        "lstm_hidden": lstm_hidden,
        "bidirectional": bidirectional,
        "dropout": dropout,
        "seed": seed,
        "device": device,
    }

    from tether.ml.deep import _torch_model  # lazy: torch only loads here

    model, history = _torch_model.train(
        dataset,
        epochs=epochs,
        batch_size=batch_size,
        learning_rate=learning_rate,
        conv_channels=conv_channels,
        kernel_size=kernel_size,
        num_conv_layers=num_conv_layers,
        lstm_hidden=lstm_hidden,
        bidirectional=bidirectional,
        dropout=dropout,
        seed=seed,
        device=device,
    )
    return TrainedDeepClassifier(
        model=model,
        channels=dataset.channels,
        n_channels=dataset.n_channels,
        window_length=dataset.window_length,
        normalization=dataset.normalization,
        history=tuple(history),
        hyperparameters=hyperparameters,
    )


def predict_proba(
    trained: TrainedDeepClassifier | Any,
    dataset: DeepTraceDataset,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    device: str | None = None,
) -> Any:
    """Per-sample accept probability ``sigmoid(logit)`` as a ``float64`` ``(n_samples,)`` array.

    Accepts either a :class:`TrainedDeepClassifier` or a bare trained ``nn.Module``. Rows align
    with ``dataset.molecule_ids`` (no shuffle). ``device`` defaults to the training device when a
    :class:`TrainedDeepClassifier` is passed, else ``"cpu"``. Lazily imports torch.
    """
    from tether.ml.deep import _torch_model  # lazy: torch only loads here

    if isinstance(trained, TrainedDeepClassifier):
        model = trained.model
        resolved_device = device or trained.hyperparameters.get("device", DEFAULT_DEVICE)
    else:
        model = trained
        resolved_device = device or DEFAULT_DEVICE
    return _torch_model.predict_proba(model, dataset, batch_size=batch_size, device=resolved_device)
