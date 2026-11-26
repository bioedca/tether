# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""A ``.tether`` whose ``/movies`` rows record real movie files (the #638 tests).

Each row records its file's absolute path, SHA-256, size and modification time, as an
extraction does, so a test can move, replace or "un-download" a file and watch the project
find it again.
"""

from __future__ import annotations

import dataclasses
import hashlib
from pathlib import Path

import h5py
import numpy as np


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _channels(seed: int, n: int = 3, t: int = 12) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    return rng.uniform(400.0, 800.0, size=(n, t)), rng.uniform(200.0, 600.0, size=(n, t))


def project_with_movies(tmp_path: Path, movies: list[Path], name: str = "m.tether") -> Path:
    """Write ``name`` in ``tmp_path`` with one ``/movies`` row (``mov-1``, ``mov-2``…) per file."""
    from _analysis_store import PARSED, _mols_traces_movie, build_store_with_channels, reg_map
    from tether.imaging.extract import write_extraction

    project, _keys = build_store_with_channels(
        tmp_path, *_channels(638), name=name, uri=str(movies[0])
    )
    path = Path(project.path)
    for i, movie in enumerate(movies[1:], start=2):
        mols, traces, meta = _mols_traces_movie(*_channels(638 + i), uri=str(movie))
        write_extraction(
            path,
            # The molecule keys hash the movie's checksum, so each movie needs its own.
            movie=dataclasses.replace(meta, movie_id=f"mov-{i}", sha256=_sha256(movie)),
            molecules=mols,
            traces=traces,
            parsed=PARSED,
            registration_map=reg_map(),
        )
    with h5py.File(path, "r+") as handle:
        table = handle["movies"]["table"][:]
        for row, movie in enumerate(movies):
            st = movie.stat()
            table["sha256"][row] = _sha256(movie)
            table["file_size"][row] = st.st_size
            table["mtime"][row] = st.st_mtime
        handle["movies"]["table"][:] = table
    return path
