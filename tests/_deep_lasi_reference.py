# SPDX-FileCopyrightText: 2026 The Tether Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Decode the public two-colour Deep-LASI trace-classifier reference for #760."""

from hashlib import file_digest
from pathlib import Path
from zipfile import ZipFile

import numpy as np

from tether.ml.deep import DeepTraceDataset, assemble_dataset

REFERENCE_COUNTS = (8678, 11402)


def molecule_labels(frames: np.ndarray) -> np.ndarray:
    """Map one molecule's frame categories to one Tether accept or reject label.

    The publisher's simulator assigns 0–2 to bleaching, 3–5 to aggregate, randomized
    and noisy, and 6–7 to static and dynamic. Reject traces that have only bleach labels.
    """
    if frames.ndim != 2 or not frames.size or not np.issubdtype(frames.dtype, np.integer):
        raise ValueError("expected a non-empty matrix of integer frame labels")
    if np.any((frames < 0) | (frames > 7)):
        raise ValueError("unknown Deep-LASI frame label")
    category = frames.max(axis=1)
    if np.any((frames >= 3) & (frames != category[:, None])):
        raise ValueError("mixed non-bleach categories in one molecule")
    return (category >= 6).astype(np.int8)


def _read_array(archive: ZipFile, name: str) -> np.ndarray:
    with archive.open(name) as member, np.load(member, allow_pickle=False) as arrays:
        return arrays["arr_0"]


def load_reference(path: Path, *, sha256: str) -> DeepTraceDataset:
    """Verify the archive before reading its named NPZ members and assembling trace rows."""
    with path.open("rb") as source:
        if file_digest(source, "sha256").hexdigest() != sha256:
            raise ValueError("Deep-LASI archive SHA-256 does not match")
        with ZipFile(source) as archive:
            frames = _read_array(archive, "y_sim_2color_test.npz")
            if frames.shape != (sum(REFERENCE_COUNTS), 500, 1):
                raise ValueError("unexpected Deep-LASI label shape")
            labels = molecule_labels(frames[:, :, 0])
            accepted = int(labels.sum())
            if (accepted, labels.size - accepted) != REFERENCE_COUNTS:
                raise ValueError("unexpected Deep-LASI accept and reject counts")
            traces = _read_array(archive, "X_sim_2color_test.npz")
    if (
        traces.shape != (*frames.shape[:2], 3)
        or not np.issubdtype(traces.dtype, np.floating)
        or not np.isfinite(traces[:, :, :2]).all()
    ):
        raise ValueError("expected finite Deep-LASI DD and DA traces")
    return assemble_dataset(
        [str(i) for i in range(len(labels))],
        traces[:, :, 0], traces[:, :, 1], labels, np.ones(len(labels)),
        intensity_quantity="raw",
    )  # fmt: skip
