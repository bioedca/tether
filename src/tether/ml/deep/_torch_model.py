# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""PyTorch consumer for the M8 deep trace classifier (ADR-0047 "Option A"; PRD §9 M8, FR-ML).

This module imports **torch at module top**, so it belongs to the isolated, optional ``deep/``
conda stack and **must never be imported by anything that runs in the base env**. The
base-importable public surface is :mod:`tether.ml.deep.model`, which imports this module
**lazily, inside its functions**. The base-matrix guard
``tests/test_torch_optional_import.py`` asserts (in a fresh interpreter) that importing
:mod:`tether.ml.deep.model` never pulls torch, so the "CPU base app unaffected" invariant
(§9 M8) cannot silently regress.

Architecture (DeepFRET/Deep-LASI-style [Thomsen2020][Wanninger2023]): a 1-D CNN feature
extractor over the ``(donor, acceptor)`` intensity channels feeds a (bi)LSTM. Padded frames
are excluded from the recurrent summary via a packed sequence — a masked pooling, never
treating zero-padding as observed data (the never-fabricate discipline the substrate already
enforces with its validity ``mask``). The final valid-frame hidden state is linearly mapped to
one accept/reject logit. LSTM-based trace idealization/classification is established for smFRET
[Zhang2025]; CNN-based trace selection likewise [Li2020].

References
----------
[Thomsen2020] Thomsen et al. "DeepFRET, a software for rapid and automated single-molecule
    FRET data classification using deep learning." eLife (2020).
[Wanninger2023] Wanninger et al. "Deep-LASI: deep-learning assisted, single-molecule imaging
    analysis of multi-color DNA origami structures." Nature Communications (2023).
[Zhang2025] Zhang et al. "Pre-trained Deep Neural Network Kin-SiM for Single-Molecule FRET
    Trace Idealization." The Journal of Physical Chemistry B (2025) — LSTM trace idealization.
[Li2020] Li, Zhang, Johnson-Buck & Walter. "Automatic classification and segmentation of
    single-molecule fluorescence time traces with deep learning." Nature Communications (2020)
    — AutoSiM CNN trace selection.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import torch
from torch import nn
from torch.nn.utils.rnn import pack_padded_sequence
from torch.utils.data import DataLoader, Dataset

if TYPE_CHECKING:
    from tether.ml.deep.dataset import DeepTraceDataset


class DeepTraceTorchDataset(Dataset):
    """A ``torch`` :class:`~torch.utils.data.Dataset` view over a :class:`DeepTraceDataset`.

    Holds the substrate's already-normalized, fixed-length tensors as torch tensors and yields
    one ``(X, length, y, weight)`` sample per row (row ``i`` = ``molecule_ids[i]``). No copy of
    the trace data beyond the numpy→torch bridge; labels/weights are cast to ``float32`` for the
    weighted BCE loss.
    """

    def __init__(self, dataset: DeepTraceDataset) -> None:
        # Reject zero-valid-frame traces up front (the choke point both train and predict pass
        # through). A length-0 trace has no observed data; feeding it to the model would force a
        # fabricated frame (the never-fabricate discipline), so fail fast naming the offenders
        # rather than silently classifying padding.
        lengths_arr = np.ascontiguousarray(dataset.lengths)
        zero_rows = np.nonzero(lengths_arr < 1)[0]
        if zero_rows.size:
            offenders = [dataset.molecule_ids[i] for i in zero_rows[:10].tolist()]
            raise ValueError(
                f"{zero_rows.size} trace(s) have zero valid frames and cannot be classified "
                "(a zero-length trace has no observed data); filter them before training or "
                f"inference. First offending molecule_ids: {offenders}"
            )
        # from_numpy shares memory; ascontiguousarray guarantees a C-contiguous source. X is
        # already float32 (n, n_channels, window_length); lengths int64; y int8 -> float32 for
        # BCEWithLogitsLoss; sample_weight float64 -> float32.
        self._x = torch.from_numpy(np.ascontiguousarray(dataset.X))
        self._lengths = torch.from_numpy(lengths_arr)
        self._y = torch.from_numpy(np.ascontiguousarray(dataset.y).astype(np.float32))
        self._w = torch.from_numpy(np.ascontiguousarray(dataset.sample_weight).astype(np.float32))

    def __len__(self) -> int:
        return int(self._y.shape[0])

    def __getitem__(
        self, index: int
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        return self._x[index], self._lengths[index], self._y[index], self._w[index]


class TraceClassifier(nn.Module):
    """1-D CNN + (bi)LSTM binary trace classifier (accept/reject logit).

    ``forward(x, lengths)`` takes ``x`` of shape ``(N, n_channels, window_length)`` and the
    per-sample valid-frame ``lengths`` ``(N,)``. The convolutional stack uses odd-kernel
    ``padding = (kernel_size - 1) // 2`` ("same" length) so the feature length stays equal to
    ``window_length`` and the LSTM can be packed against ``lengths`` — padded frames are dropped
    from the recurrent summary. Returns raw logits ``(N,)`` (apply ``sigmoid`` for probabilities).
    """

    def __init__(
        self,
        *,
        n_channels: int,
        conv_channels: int,
        kernel_size: int,
        num_conv_layers: int,
        lstm_hidden: int,
        bidirectional: bool,
        dropout: float,
    ) -> None:
        super().__init__()
        if kernel_size % 2 == 0:
            # Even kernels cannot be zero-padded to an exact "same" length with symmetric
            # padding, which would desync the conv-feature length from `lengths` and corrupt the
            # packed sequence. Enforce odd (the model.py validator raises earlier, too).
            raise ValueError(f"kernel_size must be odd for same-length conv, got {kernel_size}")
        padding = (kernel_size - 1) // 2
        layers: list[nn.Module] = []
        in_channels = n_channels
        for _ in range(num_conv_layers):
            layers.append(nn.Conv1d(in_channels, conv_channels, kernel_size, padding=padding))
            layers.append(nn.ReLU())
            in_channels = conv_channels
        self.conv = nn.Sequential(*layers)
        self.lstm = nn.LSTM(
            conv_channels, lstm_hidden, batch_first=True, bidirectional=bidirectional
        )
        self.dropout = nn.Dropout(dropout)
        self._bidirectional = bidirectional
        head_in = lstm_hidden * (2 if bidirectional else 1)
        self.head = nn.Linear(head_in, 1)

    def forward(self, x: torch.Tensor, lengths: torch.Tensor) -> torch.Tensor:
        features = self.conv(x)  # (N, conv_channels, L)  — odd-kernel "same" keeps L
        features = features.transpose(1, 2)  # (N, L, conv_channels) for batch_first LSTM
        # pack_padded_sequence needs int64 lengths on the CPU, each >= 1. Zero-length traces are
        # rejected up front in DeepTraceTorchDataset.__init__ (never fabricated into a frame), so
        # every length here is already >= 1.
        lengths_cpu = lengths.detach().to("cpu").to(torch.int64)
        packed = pack_padded_sequence(features, lengths_cpu, batch_first=True, enforce_sorted=False)
        _, (h_n, _) = self.lstm(packed)  # h_n: (num_directions, N, lstm_hidden)
        # Bidirectional: concat the forward (h_n[-2]) and backward (h_n[-1]) final hidden states
        # -> (N, 2 * lstm_hidden); unidirectional: the single final hidden state (N, lstm_hidden).
        summary = torch.cat([h_n[-2], h_n[-1]], dim=1) if self._bidirectional else h_n[-1]
        return self.head(self.dropout(summary)).squeeze(1)  # (N,)


def train(
    dataset: DeepTraceDataset,
    *,
    epochs: int,
    batch_size: int,
    learning_rate: float,
    conv_channels: int,
    kernel_size: int,
    num_conv_layers: int,
    lstm_hidden: int,
    bidirectional: bool,
    dropout: float,
    seed: int,
    device: str,
) -> tuple[nn.Module, list[float]]:
    """Train a :class:`TraceClassifier` on ``dataset``; return ``(model, per_epoch_loss)``.

    Deterministic for a given ``seed`` on CPU: the global RNG (seeded via ``torch.manual_seed``)
    fixes weight initialization, and a separately-seeded :class:`torch.Generator` fixes the
    ``DataLoader`` shuffle order (single-process, ``num_workers = 0``). The loss is a per-sample
    **weighted** ``BCEWithLogitsLoss`` (``reduction="none"`` then ``Σ wᵢ·ℓᵢ / Σ wᵢ``), so the M5
    cold-start provisional weights (``w₀ / (1 + n_human)``) carry into the deep model (§7.5).
    """
    torch.manual_seed(seed)
    device_t = torch.device(device)
    model = TraceClassifier(
        n_channels=dataset.n_channels,
        conv_channels=conv_channels,
        kernel_size=kernel_size,
        num_conv_layers=num_conv_layers,
        lstm_hidden=lstm_hidden,
        bidirectional=bidirectional,
        dropout=dropout,
    ).to(device_t)

    torch_ds = DeepTraceTorchDataset(dataset)
    generator = torch.Generator()
    generator.manual_seed(seed)
    loader = DataLoader(torch_ds, batch_size=batch_size, shuffle=True, generator=generator)
    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
    loss_fn = nn.BCEWithLogitsLoss(reduction="none")

    history: list[float] = []
    model.train()
    for _ in range(epochs):
        epoch_loss = 0.0
        epoch_weight = 0.0
        for x, lengths, y, w in loader:
            x = x.to(device_t)
            lengths = lengths.to(device_t)
            y = y.to(device_t)
            w = w.to(device_t)
            optimizer.zero_grad()
            logits = model(x, lengths)
            per_sample = loss_fn(logits, y)  # (batch,)
            weight_sum = w.sum()
            weighted_sum = (per_sample * w).sum()
            loss = weighted_sum / weight_sum if float(weight_sum) > 0.0 else per_sample.mean()
            loss.backward()
            optimizer.step()
            # Accumulate detached scalars (the running values are graph-free reporting only).
            epoch_loss += float(weighted_sum.detach())
            epoch_weight += float(weight_sum.detach())
        history.append(epoch_loss / epoch_weight if epoch_weight > 0.0 else float("nan"))

    model.eval()
    return model, history


def predict_proba(
    model: nn.Module,
    dataset: DeepTraceDataset,
    *,
    batch_size: int,
    device: str,
) -> np.ndarray:
    """Per-sample accept probability ``sigmoid(logit)`` as a ``float64`` ``(n_samples,)`` array.

    Rows align with ``dataset.molecule_ids`` (the loader does not shuffle). Runs under
    ``torch.no_grad()`` in eval mode.
    """
    device_t = torch.device(device)
    model = model.to(device_t)
    model.eval()
    torch_ds = DeepTraceTorchDataset(dataset)
    loader = DataLoader(torch_ds, batch_size=batch_size, shuffle=False)
    parts: list[np.ndarray] = []
    with torch.no_grad():
        for x, lengths, _y, _w in loader:
            logits = model(x.to(device_t), lengths.to(device_t))
            parts.append(torch.sigmoid(logits).detach().cpu().numpy())
    if not parts:
        return np.empty(0, dtype=np.float64)
    return np.concatenate(parts).astype(np.float64)
