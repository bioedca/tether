# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""A batch extraction checkpoint counts only for the movie it was made from (#1538).

The output of a batch movie is named from its stem, so ``x.tif`` and a later, different
``x.sif`` (or ``x.czi`` after ``x.nd2``) land on the same ``.tether``. A checkpoint
probe that only looked for ``/settings/extraction`` skipped the second movie's
extraction and continued the later stages on the first movie's data. These tests drive
:func:`~tether.project.batch.run_batch` with an extract stage that writes the real
``/movies/table`` row (URI and content checksum), which is the identity the checkpoint
is checked against.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("numpy")
pytest.importorskip("h5py")

import h5py  # noqa: E402
import numpy as np  # noqa: E402

from tether.io.schema import MOVIES_DTYPE, TABLE, create_project  # noqa: E402
from tether.project.batch import (  # noqa: E402
    STAGE_CORRECT,
    STAGE_EXTRACT,
    STATUS_BLOCKED,
    STATUS_FAILED,
    STATUS_SKIPPED,
    MovieJob,
    run_batch,
)


def _extract_with_movie_row(movie_path, output_path, **_kwargs):
    """Write the checkpoint group plus the movie's ``/movies/table`` row, as extract does."""
    movie = Path(movie_path)
    create_project(output_path, overwrite=True)
    row = np.zeros(1, dtype=MOVIES_DTYPE)
    for name in MOVIES_DTYPE.names:  # the variable-length strings start as None
        if MOVIES_DTYPE[name].kind == "O":
            row[name] = ""
    row["movie_id"] = "m0"
    row["uri"] = movie.name
    row["sha256"] = hashlib.sha256(movie.read_bytes()).hexdigest()
    row["file_size"] = movie.stat().st_size
    with h5py.File(output_path, "r+") as f:
        table = f["movies"][TABLE]
        table.resize((1,))
        table[0] = row[0]
        group = f.require_group("settings/extraction")
        group.attrs["profile_json"] = json.dumps({"registration_rms_px": 0.25, "rms_gate": 0.5})
    return SimpleNamespace(n_molecules=3, low_confidence_registration=False)


def _never_extract(movie_path, output_path, **_kwargs):
    raise AssertionError(f"extract runner was invoked for {output_path!r} (expected skip)")


def _correct(output_path, **_kwargs):
    with h5py.File(output_path, "r+") as f:
        f.require_group("settings/correction")
    return "corrected"


def _run(jobs, *, extract):
    return run_batch(jobs, idealize=False, _extract=extract, _correct=_correct)


def _movie(folder: Path, name: str, payload: bytes) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(payload)
    return path


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize(("first", "second"), [("x.tif", "x.sif"), ("x.nd2", "x.czi")])
def test_a_different_same_stem_movie_is_refused_not_skipped(
    tmp_path: Path, first: str, second: str
) -> None:
    out = tmp_path / "out" / "x.tether"
    out.parent.mkdir()
    one = _movie(tmp_path / "dir1", first, b"first movie bytes")
    two = _movie(tmp_path / "dir2", second, b"a different movie")
    assert _run([MovieJob(one, out)], extract=_extract_with_movie_row).n_failed == 0
    before = _digest(out)

    summary = _run([MovieJob(two, out)], extract=_never_extract)

    stages = summary.results[0].stages
    assert stages[STAGE_EXTRACT].status == STATUS_FAILED
    error = stages[STAGE_EXTRACT].error or ""
    assert first in error  # the movie the checkpoint was made from
    assert str(two) in error  # the movie selected now
    assert stages[STAGE_CORRECT].status == STATUS_BLOCKED
    assert _digest(out) == before  # the existing project is untouched


def test_a_rerun_on_the_same_movie_still_resumes(tmp_path: Path) -> None:
    out = tmp_path / "x.tether"
    movie = _movie(tmp_path / "dir1", "x.tif", b"first movie bytes")
    assert _run([MovieJob(movie, out)], extract=_extract_with_movie_row).n_failed == 0

    summary = _run([MovieJob(movie, out)], extract=_never_extract)

    stages = summary.results[0].stages
    assert stages[STAGE_EXTRACT].status == STATUS_SKIPPED
    assert stages[STAGE_EXTRACT].detail == "already extracted"
    assert stages[STAGE_CORRECT].status == STATUS_SKIPPED


def test_the_same_movie_moved_to_another_folder_still_resumes(tmp_path: Path) -> None:
    # The identity is the content checksum, not the path: a moved movie is the same movie.
    out = tmp_path / "x.tether"
    movie = _movie(tmp_path / "dir1", "x.tif", b"first movie bytes")
    assert _run([MovieJob(movie, out)], extract=_extract_with_movie_row).n_failed == 0
    moved = _movie(tmp_path / "dir2", "x.tif", movie.read_bytes())

    summary = _run([MovieJob(moved, out)], extract=_never_extract)

    assert summary.results[0].stages[STAGE_EXTRACT].status == STATUS_SKIPPED


def test_an_unreadable_movie_identity_is_not_a_checkpoint_hit(tmp_path: Path) -> None:
    out = tmp_path / "x.tether"
    movie = _movie(tmp_path / "dir1", "x.tif", b"first movie bytes")
    assert _run([MovieJob(movie, out)], extract=_extract_with_movie_row).n_failed == 0
    missing = tmp_path / "dir2" / "x.tif"  # selected movie cannot be read for its checksum

    summary = _run([MovieJob(missing, out)], extract=_never_extract)

    stages = summary.results[0].stages
    assert stages[STAGE_EXTRACT].status == STATUS_FAILED
    assert "cannot confirm which movie" in (stages[STAGE_EXTRACT].error or "")
