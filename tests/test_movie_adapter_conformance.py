# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The conformance tests every movie adapter passes (#901, ADR-0113).

One module, parametrized over ``CASES``. **A new adapter joins with one line**: a
``pytest.param(Case(...), id=...)`` entry whose ``make`` writes (or copies) a movie
into a temporary folder and returns its path together with the frames it holds.
Every test below then runs against it — the registry chooses it by content, it
gives the right geometry and values, a frame-range read does not load the full
movie, every metadata value has an origin, and the source closes cleanly.
"""

from __future__ import annotations

import shutil
import tracemalloc
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

pytest.importorskip("tifffile")
pytest.importorskip("numpy")

import numpy as np  # noqa: E402
import tifffile  # noqa: E402

from _nd2_synth import write_nd2  # noqa: E402
from tether.io.adapters import (  # noqa: E402
    ORIGINS,
    MetadataValue,
    MovieSource,
    open_movie_source,
    select_adapter,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

# 64 frames of 64 x 64 uint16 is 512 KiB: large enough that a two-frame read
# (16 KiB) and a full load differ by a factor the memory bound can see.
_N, _H, _W = 64, 64, 64


def _frames(dtype: str) -> np.ndarray:
    """Frames that differ from each other, so a wrong-frame read gives other numbers."""
    rng = np.random.default_rng(901)
    return rng.integers(0, 4000, size=(_N, _H, _W)).astype(dtype)


def _tiff_big_endian(tmp: Path) -> tuple[Path, np.ndarray]:
    frames = _frames(">u2")
    path = tmp / "movie.tif"
    tifffile.imwrite(path, frames)
    return path, frames


def _tiff_little_endian(tmp: Path) -> tuple[Path, np.ndarray]:
    frames = _frames("<u2")
    path = tmp / "movie.tiff"
    tifffile.imwrite(path, frames)
    return path, frames


def _nd2_time_series(tmp: Path) -> tuple[Path, np.ndarray]:
    """A 64-frame ND2 time series (the public ND2 files are one frame, or not a series)."""
    frames = _frames("<u2")
    return write_nd2(tmp / "movie.nd2", frames), frames


@dataclass(frozen=True)
class Case:
    """One adapter under test: its registry name and a factory for a movie it reads."""

    adapter: str
    make: Callable[[Path], tuple[Path, np.ndarray]]


CASES = [
    pytest.param(Case("TIFF", _tiff_big_endian), id="tiff-big-endian"),
    pytest.param(Case("TIFF", _tiff_little_endian), id="tiff-little-endian"),
    pytest.param(Case("ND2", _nd2_time_series), id="nd2-time-series"),
]


@pytest.fixture(params=CASES)
def case(request: pytest.FixtureRequest, tmp_path: Path) -> tuple[Case, Path, np.ndarray]:
    c: Case = request.param
    path, frames = c.make(tmp_path)
    return c, path, frames


def test_registry_selects_the_adapter(case: tuple[Case, Path, np.ndarray]) -> None:
    c, path, _ = case
    assert select_adapter(path).name == c.adapter


def test_content_decides_not_the_extension(case: tuple[Case, Path, np.ndarray]) -> None:
    c, path, frames = case
    renamed = path.with_name("renamed.dat")
    shutil.copyfile(path, renamed)
    assert select_adapter(renamed).name == c.adapter
    with open_movie_source(renamed) as source:
        np.testing.assert_array_equal(np.asarray(source.read_frames(0, 1)), frames[:1])


def test_source_satisfies_the_interface(case: tuple[Case, Path, np.ndarray]) -> None:
    _, path, frames = case
    with open_movie_source(path) as source:
        assert isinstance(source, MovieSource)
        assert source.path == path
        assert source.shape == frames.shape
        assert source.n_frames == frames.shape[0]
        pixel = np.dtype(source.pixel_type)
        assert pixel == frames.dtype, "the pixel type is the on-disk type, byte order included"


@pytest.mark.parametrize(("start", "stop"), [(0, 1), (17, 20), (_N - 1, _N), (0, _N), (5, 5)])
def test_read_frames_values(case: tuple[Case, Path, np.ndarray], start: int, stop: int) -> None:
    _, path, frames = case
    with open_movie_source(path) as source:
        got = np.asarray(source.read_frames(start, stop))
    assert got.shape == (stop - start, _H, _W)
    np.testing.assert_array_equal(got, frames[start:stop])


@pytest.mark.parametrize(("start", "stop"), [(-1, 2), (3, 2), (0, _N + 1), (_N, _N + 1)])
def test_read_frames_refuses_a_range_outside(
    case: tuple[Case, Path, np.ndarray], start: int, stop: int
) -> None:
    _, path, _ = case
    with open_movie_source(path) as source, pytest.raises(IndexError):
        source.read_frames(start, stop)


def test_frame_range_read_does_not_load_the_movie(case: tuple[Case, Path, np.ndarray]) -> None:
    """A two-frame read allocates far less than the movie (a memory limit, by tracemalloc)."""
    _, path, frames = case
    with open_movie_source(path) as source:
        tracemalloc.start()
        try:
            got = source.read_frames(10, 12)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert got.shape[0] == 2
    assert peak < frames.nbytes // 4, f"read peak {peak} B for a {frames.nbytes} B movie"


def test_every_metadata_value_has_an_origin(case: tuple[Case, Path, np.ndarray]) -> None:
    _, path, _ = case
    with open_movie_source(path) as source:
        meta = source.metadata
    for name in ("frame_time", "pixel_size", "channels"):
        value = getattr(meta, name)
        assert isinstance(value, MetadataValue), name
        assert value.origin in ORIGINS, name
        assert (value.value is None) == (value.origin == "unknown"), name


def test_close_is_idempotent_and_final(case: tuple[Case, Path, np.ndarray]) -> None:
    _, path, _ = case
    source = open_movie_source(path)
    with source:
        source.read_frames(0, 1)
    source.close()
    with pytest.raises(ValueError, match="closed"):
        source.read_frames(0, 1)


def test_every_registered_adapter_has_a_case() -> None:
    """An adapter cannot enter the registry without joining ``CASES``."""
    from tether.io.adapters import registered_adapters

    covered = {p.values[0].adapter for p in CASES}
    assert {a.name for a in registered_adapters()} <= covered
