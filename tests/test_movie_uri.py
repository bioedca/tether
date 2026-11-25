# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The ``/movies`` ``uri`` is recorded relative to the project's folder (#1282, PRD §5.1).

Each writer of a ``/movies`` row (a new project, an append, ``tether batch`` with and without
``--into``, a new trace generation) records the movie's path relative to the folder of the
project file, with POSIX separators, so the movie panel finds the movie from any working
folder. Where no relative path exists (another Windows drive) the path is absolute. The
Deep-LASI reconstruction's writer is covered in ``tests/test_deeplasi_executor.py``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

for _module in ("h5py", "numpy", "scipy", "skimage", "tifffile"):
    pytest.importorskip(_module)

import h5py  # noqa: E402

import test_extract_cli as helpers  # noqa: E402
from tether.cli import main  # noqa: E402
from tether.project import lock  # noqa: E402
from tether.project.extract import (  # noqa: E402
    ExtractOptions,
    add_movie_to_project,
    extract_movie,
    reextract_movie,
)
from tether.project.sources import project_movie_uri  # noqa: E402
from tether.services.movies import read_project_movies  # noqa: E402

_OPTIONS = ExtractOptions(window=helpers._WINDOW)


def _uris(project: Path) -> list[str]:
    with h5py.File(project, "r") as f:
        return sorted(
            v.decode() if isinstance(v, bytes) else str(v) for v in f["movies/table"]["uri"]
        )


def _raw_movie(folder: Path, name: str = "m.tif", amp: float = 300.0) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    return helpers._make_movie(folder, name=name, acceptor_amp=amp)


def test_a_relative_movie_path_is_recorded_relative_to_the_project(tmp_path, monkeypatch) -> None:
    _raw_movie(tmp_path / "raw")
    (tmp_path / "out").mkdir()
    monkeypatch.chdir(tmp_path)

    extract_movie(Path("raw/m.tif"), Path("out/m.tether"), options=_OPTIONS)

    assert _uris(tmp_path / "out" / "m.tether") == ["../raw/m.tif"]
    # The movie panel finds it from another working folder (the issue's reproduction).
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    (movie,) = read_project_movies(tmp_path / "out" / "m.tether").movies
    assert movie.available, movie.unavailable
    assert movie.path is not None
    assert movie.path.resolve() == (tmp_path / "raw" / "m.tif").resolve()


def test_an_absolute_movie_path_is_recorded_relative_to_the_project(tmp_path) -> None:
    movie = _raw_movie(tmp_path / "raw")
    (tmp_path / "out").mkdir()
    project = tmp_path / "out" / "m.tether"

    extract_movie(movie, project, options=_OPTIONS)

    assert _uris(project) == ["../raw/m.tif"]


def test_an_append_and_a_new_generation_record_it_relative_too(tmp_path, monkeypatch) -> None:
    _raw_movie(tmp_path / "raw")
    _raw_movie(tmp_path / "raw" / "day2", name="m2.tif", amp=380.0)
    (tmp_path / "out").mkdir()
    monkeypatch.chdir(tmp_path)
    project = Path("out/m.tether")
    extract_movie(Path("raw/m.tif"), project, options=_OPTIONS)

    with lock.publish_lock(project) as guard:
        add_movie_to_project(
            project, Path("raw/day2/m2.tif"), options=_OPTIONS, publish_guard=guard
        )
    assert _uris(project) == ["../raw/day2/m2.tif", "../raw/m.tif"]

    with lock.publish_lock(project) as guard:
        reextract_movie(project, Path("raw/m.tif"), options=_OPTIONS, publish_guard=guard)
    assert _uris(project) == ["../raw/day2/m2.tif", "../raw/m.tif"]


@pytest.mark.parametrize("into", [True, False], ids=["into", "out-dir"])
def test_tether_batch_records_it_relative_to_each_project(tmp_path, monkeypatch, into) -> None:
    _raw_movie(tmp_path / "raw", name="movie_000.tif")
    monkeypatch.chdir(tmp_path)
    target = ["--into", "out/P.tether"] if into else ["--out-dir", "out"]

    rc = main(["batch", "raw", *target, "--no-idealize", "--quiet"])

    assert rc == 0
    project = tmp_path / "out" / ("P.tether" if into else "movie_000.tether")
    assert _uris(project) == ["../raw/movie_000.tif"]


def test_a_movie_on_another_drive_is_recorded_absolute(tmp_path, monkeypatch) -> None:
    movie = tmp_path / "raw" / "m.tif"

    def no_relative_path(*_args, **_kwargs):
        raise ValueError("path is on mount 'D:', start on mount 'C:'")

    monkeypatch.setattr(os.path, "relpath", no_relative_path)

    assert project_movie_uri(tmp_path / "out" / "m.tether", movie) == movie.as_posix()


def test_a_relative_path_past_a_linked_folder_is_recorded_absolute(tmp_path) -> None:
    movie = _raw_movie(tmp_path / "data" / "raw")
    (tmp_path / "elsewhere" / "out").mkdir(parents=True)
    try:
        (tmp_path / "data" / "out").symlink_to(tmp_path / "elsewhere" / "out")
    except OSError:  # Windows without the right to make a link
        pytest.skip("cannot make a folder link here")
    project = tmp_path / "data" / "out" / "m.tether"

    uri = project_movie_uri(project, movie)
    # Whatever is recorded, read against the project's folder, is the movie.
    assert os.path.samefile(project.parent / uri, movie)
    if os.name != "nt":
        # POSIX reads ``..`` from where the link points (elsewhere/), where no movie is;
        # Windows drops ``..`` from the name first, so ``../raw/m.tif`` is right there.
        assert uri == movie.as_posix()
    assert project_movie_uri(tmp_path / "data" / "m.tether", movie) == "raw/m.tif"
