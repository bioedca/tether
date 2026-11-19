# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The movies of a project as a front end shows them, for the shell's movie panel (#356).

``tether.services.movies`` is Qt-free, so these run on every operating system without a
display: where a ``/movies`` row's file is, whether it can be shown, where its molecules'
spots fall in the whole camera frame, and the radius they are drawn at.
"""

from __future__ import annotations

import itertools
import stat
from pathlib import Path

import numpy as np
import pytest

from tether.imaging.split import process_image
from tether.services.movies import open_project_movie, read_project_movies, subimage_to_frame

h5py = pytest.importorskip("h5py")

_FRAME = (7, 10)


@pytest.mark.parametrize(
    ("rotation", "flip"),
    list(itertools.product((0, 90, 180, 270), ((0, 0), (1, 0), (0, 1), (1, 1)))),
)
def test_subimage_to_frame_undoes_process_image(rotation, flip) -> None:
    # Each pixel of the frame holds its own index, so the sub-image says which frame pixel
    # each of its pixels came from; the mapping must land on exactly that pixel.
    height, width = _FRAME
    frame = np.arange(height * width).reshape(_FRAME)
    rot_h, rot_w = (width, height) if rotation in (90, 270) else (height, width)
    crop = (2, 3, rot_h - 1, rot_w - 1)
    sub = process_image(frame, rotation_deg=rotation, flip=flip, crop=crop)
    rows, cols = np.meshgrid(np.arange(sub.shape[0]), np.arange(sub.shape[1]), indexing="ij")
    xy = np.column_stack([cols.ravel(), rows.ravel()]).astype(np.float64)

    full = subimage_to_frame(xy, frame_shape=_FRAME, crop=crop, rotation_deg=rotation, flip=flip)

    np.testing.assert_array_equal(full, np.round(full))
    picked = frame[full[:, 1].astype(int), full[:, 0].astype(int)]
    np.testing.assert_array_equal(picked, sub.ravel())


def test_subimage_to_frame_keeps_sub_pixel_offsets_and_its_input() -> None:
    xy = np.array([[0.25, 1.5]])
    original = xy.copy()
    full = subimage_to_frame(xy, frame_shape=(8, 16), crop=(1, 9, 8, 16))
    np.testing.assert_allclose(full, [[8.25, 1.5]])
    np.testing.assert_array_equal(xy, original)
    # An all-zero crop is "no crop", the stored convention.
    np.testing.assert_allclose(subimage_to_frame(xy, frame_shape=(8, 16), crop=(0, 0, 0, 0)), xy)
    with pytest.raises(ValueError, match="multiple of 90"):
        subimage_to_frame(xy, frame_shape=(8, 16), crop=None, rotation_deg=45)


def _store(tmp_path, *, uri: str, name: str = "movies.tether"):
    from _analysis_store import build_store_with_channels

    rng = np.random.default_rng(356)
    donor = rng.uniform(400.0, 800.0, size=(3, 12))
    acceptor = rng.uniform(200.0, 600.0, size=(3, 12))
    project, _keys = build_store_with_channels(tmp_path, donor, acceptor, name=name, uri=uri)
    return project.path


def _molecule_xy(path) -> tuple[np.ndarray, np.ndarray]:
    from tether.imaging.extract import read_molecules

    mols = read_molecules(path)
    return np.asarray(mols["donor_xy"]), np.asarray(mols["acceptor_xy"])


def test_a_movie_row_carries_its_file_and_its_spots_in_frame_pixels(tmp_path) -> None:
    movie_file = tmp_path / "Bla_UCKOPSB_movie_010.tif"
    movie_file.write_bytes(b"not read here")
    path = _store(tmp_path, uri=str(movie_file))

    (movie,) = read_project_movies(path).movies

    assert movie.movie_id == "mov-1"
    assert movie.name == "Bla_UCKOPSB_movie_010.tif"
    assert movie.path == movie_file
    assert movie.available
    assert movie.unavailable is None
    donor, acceptor = _molecule_xy(path)
    # The donor crop starts at column 1 (no offset); the acceptor crop at column 65, so its
    # spots move 64 px right, into the acceptor half of the frame.
    np.testing.assert_allclose(movie.donor_xy, donor)
    np.testing.assert_allclose(movie.acceptor_xy, acceptor + [64.0, 0.0])


def test_a_relative_uri_is_read_against_the_project_folder(tmp_path) -> None:
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / "m.tif").write_bytes(b"x")
    path = _store(tmp_path, uri="raw/m.tif")

    (movie,) = read_project_movies(path).movies

    assert movie.path == tmp_path / "raw" / "m.tif"
    assert movie.available


def _set_movie_row(path, **fields) -> None:
    with h5py.File(path, "r+") as handle:
        table = handle["movies"]["table"][:]
        for name, value in fields.items():
            table[name][0] = value
        handle["movies"]["table"][:] = table


def test_a_relative_uri_from_the_extraction_folder_is_found_there_too(
    tmp_path, monkeypatch
) -> None:
    # `tether extract raw/m.tif -o out/m.tether` records "raw/m.tif", relative to the folder
    # the command ran in rather than to the project (#356 review).
    movie_file = tmp_path / "raw" / "m.tif"
    movie_file.parent.mkdir()
    movie_file.write_bytes(b"four")
    (tmp_path / "out").mkdir()
    path = _store(tmp_path / "out", uri="raw/m.tif")
    monkeypatch.chdir(tmp_path)
    st = movie_file.stat()

    # A row with no fast signature cannot vouch for a file found elsewhere.
    (movie,) = read_project_movies(path).movies
    assert movie.path == tmp_path / "out" / "raw" / "m.tif"
    assert movie.unavailable == "file missing"

    # The whole recorded signature (size and modification time) must match.
    _set_movie_row(path, file_size=st.st_size, mtime=st.st_mtime)
    (movie,) = read_project_movies(path).movies
    assert movie.path == movie_file
    assert movie.available
    _set_movie_row(path, mtime=st.st_mtime + 60.0)
    (movie,) = read_project_movies(path).movies
    assert movie.path == tmp_path / "out" / "raw" / "m.tif"
    assert movie.unavailable == "file missing"


def test_a_movie_whose_signature_cannot_be_read_is_unreadable(tmp_path, monkeypatch) -> None:
    # A stat that fails after the file was found (a permission or network error) reads as
    # unreadable, and a file in the current folder whose stat fails is not taken.
    from tether.services import movies as movies_mod

    movie_file = tmp_path / "m.tif"
    movie_file.write_bytes(b"four")
    path = _store(tmp_path, uri=str(movie_file))
    st = movie_file.stat()
    _set_movie_row(path, file_size=st.st_size, mtime=st.st_mtime)

    def _refused(*_args) -> bool:
        raise PermissionError("stat refused")

    monkeypatch.setattr(movies_mod, "_signature_matches", _refused)
    (movie,) = read_project_movies(path).movies
    assert movie.unavailable == "file unreadable"

    (tmp_path / "out").mkdir()
    elsewhere = _store(tmp_path / "out", uri="m.tif")
    _set_movie_row(elsewhere, file_size=st.st_size, mtime=st.st_mtime)
    monkeypatch.chdir(tmp_path)
    (movie,) = read_project_movies(elsewhere).movies
    assert movie.path == tmp_path / "out" / "m.tif"
    assert movie.unavailable == "file missing"


@pytest.mark.parametrize("changed", ["file_size", "mtime"])
def test_a_movie_whose_fast_signature_differs_is_flagged(tmp_path, changed) -> None:
    # PRD §5.4: a routine open checks size + mtime (no byte read); a mismatch is flagged
    # and never silently trusted (#356 review).
    movie_file = tmp_path / "m.tif"
    movie_file.write_bytes(b"four")
    path = _store(tmp_path, uri=str(movie_file))
    st = movie_file.stat()
    _set_movie_row(path, file_size=st.st_size, mtime=st.st_mtime)
    (movie,) = read_project_movies(path).movies
    assert movie.available

    _set_movie_row(path, **{changed: {"file_size": 5, "mtime": st.st_mtime - 1.0}[changed]})
    (movie,) = read_project_movies(path).movies

    assert not movie.available
    assert movie.unavailable == "file changed since extraction"
    with pytest.raises(ValueError, match="file changed since extraction"):
        open_project_movie(movie)


def test_a_movie_with_no_channel_geometry_has_no_spots(tmp_path) -> None:
    # A Deep-LASI reconstruction records no channel geometry: the stored coordinates are
    # sub-image pixels with no record of where the sub-images sat (#356 review).
    movie_file = tmp_path / "m.tif"
    movie_file.write_bytes(b"x")
    path = _store(tmp_path, uri=str(movie_file))
    zero = np.zeros(4, dtype=np.int32)
    _set_movie_row(path, donor_crop=zero, acceptor_crop=zero)

    (movie,) = read_project_movies(path).movies

    assert movie.available
    assert movie.no_spots == "spot positions not recorded"
    assert movie.donor_xy.shape == movie.acceptor_xy.shape == (0, 2)


def test_one_full_frame_channel_keeps_its_spots(tmp_path) -> None:
    # One all-zero channel is a full-frame channel, not missing geometry.
    movie_file = tmp_path / "m.tif"
    movie_file.write_bytes(b"x")
    path = _store(tmp_path, uri=str(movie_file))
    _set_movie_row(path, donor_crop=np.zeros(4, dtype=np.int32))

    (movie,) = read_project_movies(path).movies

    assert movie.no_spots is None
    donor, acceptor = _molecule_xy(path)
    np.testing.assert_allclose(movie.donor_xy, donor)
    np.testing.assert_allclose(movie.acceptor_xy, acceptor + [64.0, 0.0])


@pytest.mark.parametrize(
    ("uri", "reason"),
    [("", "no movie file recorded"), ("gone.tif", "file missing")],
)
def test_a_movie_that_cannot_be_shown_says_why(tmp_path, uri, reason) -> None:
    path = _store(tmp_path, uri=uri)

    (movie,) = read_project_movies(path).movies

    assert not movie.available
    assert movie.unavailable == reason
    assert movie.name == ("mov-1" if not uri else "gone.tif")
    assert len(movie.donor_xy) == 3


def test_a_cloud_placeholder_is_not_on_this_disk(tmp_path, monkeypatch) -> None:
    # PRD §5.4: a routine open reads attributes only and never hydrates a placeholder.
    movie_file = tmp_path / "m.tif"
    movie_file.write_bytes(b"x")
    path = _store(tmp_path, uri=str(movie_file))
    from tether.project import condition_model

    monkeypatch.setattr(condition_model, "_file_attributes", lambda _p: stat.FILE_ATTRIBUTE_OFFLINE)

    (movie,) = read_project_movies(path).movies

    assert movie.unavailable == "file not on this disk"


def test_a_project_without_movies_has_none(tmp_path) -> None:
    from tether.io.schema import create_project

    path = tmp_path / "empty.tether"
    create_project(path)
    assert read_project_movies(path).movies == ()
    with h5py.File(path, "r+") as handle:
        del handle["movies"]
    empty = read_project_movies(path)
    assert empty.movies == ()
    # No extraction settings either: the documented default radius.
    assert empty.aperture_radius == 3.0


def test_molecules_of_another_movie_or_without_coordinates_are_left_out(tmp_path) -> None:
    movie_file = tmp_path / "m.tif"
    movie_file.write_bytes(b"x")
    path = _store(tmp_path, uri=str(movie_file))
    with h5py.File(path, "r+") as handle:
        table = handle["molecules"]["table"][:]
        table["movie_id"][0] = "mov-elsewhere"  # a subset export's dangling movie_id
        table["donor_xy"][1] = [np.nan, np.nan]
        handle["molecules"]["table"][:] = table

    (movie,) = read_project_movies(path).movies

    donor, _acceptor = _molecule_xy(path)
    np.testing.assert_allclose(movie.donor_xy, donor[2:])
    assert movie.acceptor_xy.shape == (1, 2)


def test_the_radius_is_the_stored_extraction_disk_radius(tmp_path) -> None:
    path = _store(tmp_path, uri="")
    assert read_project_movies(path).aperture_radius == 3.0
    with h5py.File(path, "r+") as handle:
        handle["/settings/extraction"].attrs["disk_radius"] = 2.5
    assert read_project_movies(path).aperture_radius == 2.5


def test_opening_a_movie_that_cannot_be_shown_touches_no_file(tmp_path) -> None:
    path = _store(tmp_path, uri="gone.tif")
    (movie,) = read_project_movies(path).movies
    with pytest.raises(ValueError, match="gone.tif cannot be opened: file missing"):
        open_project_movie(movie)


def test_opening_an_available_movie_gives_its_lazy_reader(tmp_path) -> None:
    fixture = Path(__file__).resolve().parent / "fixtures" / "movie_be_64x64x50.tif"
    path = _store(tmp_path, uri=str(fixture))
    (movie,) = read_project_movies(path).movies
    with open_project_movie(movie) as reader:
        assert reader.shape == (50, 64, 64)
