# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""A labelled movie that a test extracts again, for the reconciliation tests (#923).

One small synthetic movie with two bright spots and one dim one. A detection threshold
of ``HIGH`` finds the two bright spots and ``LOW`` all three. [`first_generation`][]
extracts it at ``HIGH``, moves the stored position of the second spot by 2 px (as if that
extraction had found it elsewhere), and labels both spots, so a later extraction at ``LOW``
has a spot that stayed, a spot that moved more than the limit and a new spot.

A plain helper module (not a ``test_*`` file, so pytest never collects it), imported as a
top-level module like ``_analysis_store``. Its users gate on ``pytest.importorskip`` first.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import h5py
import numpy as np
import tifffile

from tether.imaging.extract import molecule_key
from tether.io.generations import read_generation
from tether.project import labels, lock
from tether.project.extract import ExtractOptions, extract_movie, reextract_movie

SHAPE = (64, 96)
N_FRAMES = 12
SHIFT = np.array([49.0, 0.0])
#: Two bright spots and one dim one: a threshold of 0.5 finds two, 0.2 finds three.
DONOR = np.array([[12.0, 12.0], [24.0, 40.0], [16.0, 52.0]])
AMP = np.array([400.0, 400.0, 120.0])
HIGH, LOW = 0.5, 0.2
T0 = "2026-01-01T00:00:00+00:00"
T1 = "2026-02-01T00:00:00+00:00"


def text(value: object) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def movie_file(tmp_path: Path) -> Path:
    path = tmp_path / "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif"
    centers = np.vstack([DONOR, DONOR + SHIFT])
    amps = np.concatenate([AMP, AMP])
    rows, cols = np.mgrid[0 : SHAPE[0], 0 : SHAPE[1]]
    spots = np.zeros(SHAPE)
    for (x, y), amp in zip(centers, amps, strict=True):
        spots += amp * np.exp(-((rows - y) ** 2 + (cols - x) ** 2) / (2.0 * 1.5**2))
    ramp = 1.0 + 0.02 * np.arange(N_FRAMES)
    stack = 80.0 + ramp[:, None, None] * spots[None]
    tifffile.imwrite(
        path,
        np.ascontiguousarray(stack, dtype=">u2"),
        byteorder=">",
        photometric="minisblack",
    )
    return path


def options(threshold: float) -> ExtractOptions:
    return ExtractOptions(window=21, detection_mode="intensity", detection_threshold=threshold)


def reextract(project: Path, movie: Path, threshold: float):
    with lock.publish_lock(project) as guard:
        return reextract_movie(project, movie, options=options(threshold), publish_guard=guard)


def key_at(project: Path, x: float, y: float) -> str:
    rows = read_generation(project, movie_id(project)).molecules
    for row in rows:
        if np.allclose(row["donor_xy"], [x, y], atol=0.3):
            return text(row["molecule_key"])
    raise AssertionError(f"no molecule at ({x}, {y})")


def movie_id(project: Path) -> str:
    with h5py.File(project, "r") as f:
        return text(f["movies"]["table"]["movie_id"][0])


def first_generation(tmp_path: Path) -> tuple[Path, Path, str, str]:
    """Generation 1, labelled; return the project, the movie and the two keys."""
    movie = movie_file(tmp_path)
    project = tmp_path / "experiment.tether"
    extract_movie(movie, project, options=options(HIGH))
    # As if generation 1 had found the second spot 2 px from where it is: it moved.
    sha = hashlib.sha256(movie.read_bytes()).hexdigest()
    with h5py.File(project, "r+") as f:
        table = f["molecules"]["table"]
        for i in range(table.shape[0]):
            row = table[i]
            if np.allclose(row["donor_xy"], DONOR[1], atol=0.3):
                row["donor_xy"] = np.asarray(row["donor_xy"]) + [2.0, 0.0]
                row["molecule_key"] = molecule_key(sha, row["donor_xy"])
                table[i] = row
            elif np.allclose(row["donor_xy"], DONOR[0], atol=0.3):
                row["category"] = "folded"
                row["analysis_window"] = [2, 9]
                table[i] = row
    stayed = key_at(project, *DONOR[0])
    moved = key_at(project, *(DONOR[1] + [2.0, 0.0]))
    labels.accept(project, stayed, labeler="original", timestamp=T0)
    labels.reject(project, moved, labeler="original", timestamp=T0)
    return project, movie, stayed, moved
