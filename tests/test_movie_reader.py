# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Lazy big-endian TIFF movie reader (PRD §5.2, Appendix A; §9 M0 S7).

Locks the M0 acceptance clause "a big-endian TIFF opens with correct geometry
and O(1) frame access": geometry/dtype/byte order are read correctly from the
committed big-endian fixture, and a single-frame read is a zero-copy memmap view
that never materializes the whole stack.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("tifffile")
pytest.importorskip("numpy")

import numpy as np  # noqa: E402
import tifffile  # noqa: E402

from tether.io.movie import MovieReader, open_movie  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "movie_be_64x64x50.tif"
SHAPE = (50, 64, 64)


def test_opens_with_correct_geometry() -> None:
    with open_movie(FIXTURE) as movie:
        assert movie.shape == SHAPE
        assert movie.n_frames == 50
        assert movie.height == 64
        assert movie.width == 64
        assert len(movie) == 50
        assert movie.path == FIXTURE


def test_preserves_big_endian_dtype() -> None:
    with MovieReader(FIXTURE) as movie:
        assert movie.byteorder == ">", "big-endian on-disk order must be preserved"
        assert movie.dtype == np.dtype(">u2")
        assert movie.dtype.byteorder == ">"


def test_frame_read_is_lazy_memmap_view() -> None:
    """A single-frame read shares memory with the on-disk map — no stack copy."""
    with MovieReader(FIXTURE) as movie:
        assert isinstance(movie.data, np.memmap), "whole movie must stay memory-mapped"
        frame = movie.frame(0)
        assert frame.shape == (64, 64)
        assert isinstance(frame, np.memmap)
        # Zero-copy: the frame is a view into `data`, not a materialized array.
        assert np.shares_memory(frame, movie.data)


def test_frame_values_match_independent_read() -> None:
    """Big-endian values are read correctly, not silently byte-swapped."""
    expected = tifffile.imread(FIXTURE, key=0)
    with MovieReader(FIXTURE) as movie:
        got = np.asarray(movie.frame(0))
    assert got.dtype == np.dtype(">u2")
    np.testing.assert_array_equal(got, expected)


def test_last_frame_and_negative_index() -> None:
    with MovieReader(FIXTURE) as movie:
        last = np.asarray(movie.frame(49))
        neg = np.asarray(movie.frame(-1))
        np.testing.assert_array_equal(last, neg)


def test_iter_yields_every_frame_lazily() -> None:
    with MovieReader(FIXTURE) as movie:
        frames = list(movie)
        assert len(frames) == 50
        assert all(isinstance(f, np.memmap) for f in frames)
        assert all(f.shape == (64, 64) for f in frames)


def test_out_of_range_frame_raises() -> None:
    with MovieReader(FIXTURE) as movie:
        with pytest.raises(IndexError):
            movie.frame(50)
        with pytest.raises(IndexError):
            movie.frame(-51)


def test_frame_time_absent_on_raw_movie() -> None:
    # The reference TIFF carries no frame-time tag; it arrives from the .tdat/.mat.
    with MovieReader(FIXTURE) as movie:
        assert movie.frame_time is None


def test_use_after_close_raises() -> None:
    movie = MovieReader(FIXTURE)
    movie.close()
    movie.close()  # idempotent
    with pytest.raises(ValueError, match="closed"):
        movie.frame(0)


def test_repr_is_informative() -> None:
    with MovieReader(FIXTURE) as movie:
        text = repr(movie)
    assert "MovieReader" in text
    assert "frames=50" in text
    assert FIXTURE.name in text
