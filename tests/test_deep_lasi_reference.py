# SPDX-FileCopyrightText: 2026 The Tether Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise the reference reader without downloading the Deep-LASI archive."""

from hashlib import file_digest
from io import BytesIO
from zipfile import ZipFile

import numpy as np
import pytest

import _deep_lasi_reference as reference


def test_all_two_colour_labels_and_the_three_bleach_only_cases():
    frames = np.array([[0, i, 1, 2] for i in range(8)])
    assert reference.molecule_labels(frames).tolist() == [0, 0, 0, 0, 0, 0, 1, 1]


@pytest.mark.parametrize(
    ("frames", "message"),
    [([[3, 6]], "mixed non-bleach"), ([[8, 1]], "unknown"), ([[6.0, 1.0]], "integer")],
)
def test_bad_label_rows_raise(frames, message):
    with pytest.raises(ValueError, match=message):
        reference.molecule_labels(np.asarray(frames))


def test_archive_keeps_molecules_and_uses_donor_excitation_channels(tmp_path, monkeypatch):
    path = tmp_path / "reference.zip"
    traces = np.broadcast_to([1.0, 3.0, 100.0], (2, 500, 3))
    labels = np.zeros((2, 500, 1), dtype=np.int8)
    labels[0, 0, 0] = 6
    with ZipFile(path, "w") as archive:
        for name, array in (("X", traces), ("y", labels)):
            member = BytesIO()
            np.savez_compressed(member, array)
            archive.writestr(f"{name}_sim_2color_test.npz", member.getvalue())
    with path.open("rb") as source:
        digest = file_digest(source, "sha256").hexdigest()
    monkeypatch.setattr(reference, "REFERENCE_COUNTS", (1, 1))
    with pytest.raises(ValueError, match="SHA-256"):
        reference.load_reference(path, sha256="0" * 64)
    dataset = reference.load_reference(path, sha256=digest)
    assert dataset.y.tolist() == [1, 0]
    assert dataset.mask.all()
    assert dataset.intensity_quantity == "raw"
    assert dataset.X[:, :, 0].tolist() == [[0.25, 0.75], [0.25, 0.75]]
    monkeypatch.setattr(reference, "REFERENCE_COUNTS", (2, 0))
    with pytest.raises(ValueError, match="accept and reject counts"):
        reference.load_reference(path, sha256=digest)
