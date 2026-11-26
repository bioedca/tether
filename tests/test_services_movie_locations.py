# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Finding a project's movie again without changing the project (#638, PRD §5.4).

The rules of ``tether.services.movie_locations``: a file is the project's movie only by its
checksum, a cloud placeholder is never read unless asked, and nothing writes the project.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import numpy as np
import pytest

h5py = pytest.importorskip("h5py")
tifffile = pytest.importorskip("tifffile")


def _tiff(path: Path, seed: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.random.default_rng(seed).integers(0, 4000, size=(4, 16, 16), dtype=np.uint16)
    tifffile.imwrite(path, data, photometric="minisblack")
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _project(tmp_path: Path, movies: list[Path]) -> Path:
    """A ``.tether`` whose ``/movies`` rows record ``movies`` with their real signatures."""
    from _movie_locate_store import project_with_movies

    return project_with_movies(tmp_path, movies)


def test_the_expected_checksum_is_the_one_movies_recorded(tmp_path) -> None:
    from tether.services.movie_locations import expected_movie_sha256

    movie = _tiff(tmp_path / "raw" / "a.tif", 1)
    path = _project(tmp_path, [movie])

    assert expected_movie_sha256(path, "mov-1") == _sha256(movie)
    assert expected_movie_sha256(path, "no-such-movie") == ""


def test_a_moved_file_with_the_same_checksum_is_accepted(tmp_path) -> None:
    from tether.services.movie_locations import check_relocated

    movie = _tiff(tmp_path / "a.tif", 1)
    moved = tmp_path / "elsewhere" / "renamed.tif"
    moved.parent.mkdir()
    shutil.copy(movie, moved)

    where = check_relocated(moved, _sha256(movie).upper())

    assert where.path == moved.absolute()
    assert where.file_size == moved.stat().st_size
    assert where.mtime == moved.stat().st_mtime


def test_a_wrong_file_or_no_recorded_checksum_is_refused(tmp_path) -> None:
    from tether.project.sources import RelinkRefused
    from tether.services.movie_locations import check_relocated

    movie = _tiff(tmp_path / "a.tif", 1)
    other = _tiff(tmp_path / "b.tif", 2)

    with pytest.raises(RelinkRefused, match="checksum is different"):
        check_relocated(other, _sha256(movie))
    with pytest.raises(RelinkRefused, match="no checksum"):
        check_relocated(movie, "")


def test_the_checksum_rule_is_the_callers(tmp_path) -> None:
    # The deposit package check and the registry call the same step with their own rule.
    from tether.services.movie_locations import check_relocated

    movie = _tiff(tmp_path / "a.tif", 1)
    seen: list[tuple[str, str]] = []

    def rule(expected: str, actual: str) -> bool:
        seen.append((expected, actual))
        return True

    check_relocated(movie, "f" * 64, rule=rule)

    assert seen == [("f" * 64, _sha256(movie))]


def test_a_folder_search_finds_several_movies_and_refuses_a_namesake(tmp_path) -> None:
    from tether.services.movie_locations import locate_in_folder
    from tether.services.movies import read_project_movies

    first = _tiff(tmp_path / "raw" / "first.tif", 1)
    second = _tiff(tmp_path / "raw" / "second.tif", 2)
    third = _tiff(tmp_path / "raw" / "third.tif", 3)
    path = _project(tmp_path, [first, second, third])
    folder = tmp_path / "copy"
    (folder / "day1").mkdir(parents=True)
    shutil.move(first, folder / "day1" / "FIRST.TIF")
    shutil.move(second, folder / "second.tif")
    _tiff(folder / "third.tif", 99)  # the right name, another file
    third.unlink()
    movies = read_project_movies(path).movies
    progress: list[tuple[int, int]] = []

    search = locate_in_folder(path, movies, folder, progress=lambda d, t: progress.append((d, t)))

    assert set(search.found) == {"mov-1", "mov-2"}
    assert search.found["mov-1"].path == (folder / "day1" / "FIRST.TIF").absolute()
    assert "checksum is different" in search.refused["mov-3"]
    assert search.not_found == ()
    assert progress[-1][0] == progress[-1][1]  # every byte read is counted


def test_a_folder_search_never_reads_a_placeholder(tmp_path, monkeypatch) -> None:
    from tether.services import movie_locations
    from tether.services.movies import read_project_movies

    movie = _tiff(tmp_path / "raw" / "a.tif", 1)
    path = _project(tmp_path, [movie])
    folder = tmp_path / "cloud"
    folder.mkdir()
    shutil.move(movie, folder / "a.tif")
    monkeypatch.setattr(movie_locations, "is_placeholder", lambda p: Path(p).parent == folder)

    def _no_read(*_args, **_kwargs):
        raise AssertionError("a placeholder was read")

    monkeypatch.setattr(movie_locations, "check_relocated", _no_read)

    search = movie_locations.locate_in_folder(path, read_project_movies(path).movies, folder)

    assert search.found == {}
    assert search.placeholders == {"mov-1": folder / "a.tif"}


def test_a_remembered_location_replaces_only_a_movie_that_cannot_be_shown(tmp_path) -> None:
    from tether.services.movie_locations import apply_locations, check_relocated
    from tether.services.movies import read_project_movies

    shown = _tiff(tmp_path / "raw" / "shown.tif", 1)
    gone = _tiff(tmp_path / "raw" / "gone.tif", 2)
    path = _project(tmp_path, [shown, gone])
    moved = tmp_path / "moved" / "gone.tif"
    moved.parent.mkdir()
    shutil.move(gone, moved)
    movies = read_project_movies(path)
    assert [m.unavailable for m in movies.movies] == [None, "file missing"]
    where = check_relocated(moved, _sha256(moved))
    elsewhere = check_relocated(shown, _sha256(shown))

    moved_movies = apply_locations(movies, {"mov-2": where, "mov-1": elsewhere})

    first, second = moved_movies.movies
    assert first == movies.movies[0]
    assert second.path == moved.absolute()
    assert second.available


def test_a_remembered_location_that_changed_since_is_flagged(tmp_path) -> None:
    from tether.services.movie_locations import MovieLocation, apply_locations
    from tether.services.movies import read_project_movies

    gone = _tiff(tmp_path / "raw" / "gone.tif", 2)
    path = _project(tmp_path, [gone])
    moved = _tiff(tmp_path / "moved" / "gone.tif", 5)
    gone.unlink()
    stale = MovieLocation(moved.absolute(), moved.stat().st_size + 1, moved.stat().st_mtime)

    (movie,) = apply_locations(read_project_movies(path), {"mov-1": stale}).movies

    assert movie.unavailable == "file changed since extraction"


def test_download_reads_every_byte(tmp_path) -> None:
    from tether.services.movie_locations import download_movie

    movie = _tiff(tmp_path / "a.tif", 1)

    assert download_movie(movie) == movie.stat().st_size
