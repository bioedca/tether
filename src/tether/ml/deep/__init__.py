# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Deep trace-classifier subpackage (PRD §4.1/§9 M8; FR-ML) — the optional GPU add-on.

The framework-agnostic, **dependency-free** substrate for Tether's M8 deep classifier lives
here (:mod:`tether.ml.deep.dataset`): it turns the shared curation-label store into
normalized, fixed-length NumPy tensors ready for a 1-D CNN/LSTM — **without** importing any
deep-learning framework, so this module stays in the base env and is covered by the default
3-OS test matrix.

Per ADR-0047 (Option A), PyTorch and the model/training loop are **not** here: they live in
an isolated, optional ``deep/`` conda stack (mirroring the tMAVEN sidecar) behind a
lazy/guarded import and a non-required GPU CI leg, so the CPU base app is unaffected. That is
the follow-up PR-1b; this PR-1a is the torch-free dataset substrate only.
"""

from __future__ import annotations

from tether.ml.deep.dataset import (
    DEFAULT_DEEP_CHANNELS,
    DEFAULT_NORMALIZATION,
    DEFAULT_SPLIT_SEED,
    DEFAULT_VAL_FRACTION,
    DEFAULT_WINDOW_LENGTH,
    NORMALIZATIONS,
    SUPPORTED_CHANNELS,
    DeepTraceDataset,
    assemble_dataset,
    normalize_pair,
    train_val_split,
)

__all__ = [
    "DEFAULT_DEEP_CHANNELS",
    "DEFAULT_NORMALIZATION",
    "DEFAULT_SPLIT_SEED",
    "DEFAULT_VAL_FRACTION",
    "DEFAULT_WINDOW_LENGTH",
    "NORMALIZATIONS",
    "SUPPORTED_CHANNELS",
    "DeepTraceDataset",
    "assemble_dataset",
    "normalize_pair",
    "train_val_split",
]
