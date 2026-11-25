# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The frames of any registered movie reader, for extraction and display (#1388, ADR-0149).

Extraction and the movie panel work on a ``(n_frames, height, width)`` array. The TIFF
reader gives one directly, its memory map ([`tether.io.movie.MovieReader.data`][]).
Another reader gives only [`tether.io.adapters.MovieSource.read_frames`][], so this
module makes the array from that, without a load of the full movie into memory:

* [`open_extraction_movie`][] opens a movie for extraction. A TIFF gives its memory map,
  as before. Any other reader in [`tether.io.movie.EXTRACTED_CONTAINERS`][] is read in
  blocks of [`READ_BLOCK_FRAMES`][] frames into a temporary file, which is then mapped
  read-only the same way. The pipeline after it is the same code for every reader, so an
  ND2 movie and its TIFF export give the same traces. A reader not in that list is
  refused with a message that names the container.
* [`FrameView`][] is a lazy array view for display: indexing it reads only the frames
  that the index touches.

The temporary file is deleted by the operating system when the last view of its map is
gone; nothing names it on disk. Its size is the movie's size, in the system temporary
folder (`tempfile.gettempdir()`).
"""

from __future__ import annotations

import errno
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from tether.io.adapters import MovieSource, UnsupportedContainerError, select_adapter

if TYPE_CHECKING:
    from collections.abc import Iterator
    from os import PathLike
    from types import TracebackType

__all__ = [
    "READ_BLOCK_FRAMES",
    "ExtractionMovie",
    "FrameView",
    "open_extraction_movie",
    "spool_frames",
]

READ_BLOCK_FRAMES = 64
"""Frames read from a non-TIFF movie in one call of ``read_frames``.

It sets only how much of the movie is in memory at one time while it is copied to the
temporary file (64 frames of 512 x 512 uint16 is 32 MiB). It changes no value: the
copy is the same for every block size, which a test checks, so it is not an analysis
parameter of PRD §11.2 (ADR-0149).
"""


def _byteorder(dtype: np.dtype) -> str:
    """``'>'`` or ``'<'``: the byte order of ``dtype``, with native resolved for this host."""
    if dtype.byteorder == ">" or (dtype.byteorder == "=" and sys.byteorder == "big"):
        return ">"
    return "<"


def spool_frames(source: MovieSource, *, block: int = READ_BLOCK_FRAMES) -> np.memmap:
    """Copy every frame of ``source`` to a temporary file and return a read-only map of it.

    The frames are read ``block`` at a time with ``read_frames`` and written with an
    ordinary file write, so at most one block is in memory, and a full disk raises
    `OSError` here (a write into a map of a sparse file could instead stop the process).
    The result has the source's shape and on-disk pixel type. The file has no name on
    disk and is deleted when the map and every view of it are gone.

    Raises
    ------
    ValueError
        If ``block`` is less than 1, or a block has the wrong shape or pixel type.
    OSError
        If the temporary folder has no room for the frames; the message names the folder
        and the size.
    """
    if block < 1:
        raise ValueError(f"block must be >= 1, got {block}")
    n_frames, height, width = (int(v) for v in source.shape)
    dtype = np.dtype(source.pixel_type)
    shape = (n_frames, height, width)
    # A movie of no frames cannot be mapped (a map of zero bytes is refused); an
    # empty array of the right shape and type is the same movie.
    if n_frames == 0 or height == 0 or width == 0:
        return np.zeros(shape, dtype=dtype)  # type: ignore[return-value]
    with tempfile.TemporaryFile() as fh:
        try:
            for start in range(0, n_frames, block):
                stop = min(start + block, n_frames)
                frames = np.asarray(source.read_frames(start, stop))
                if frames.shape != (stop - start, height, width) or frames.dtype != dtype:
                    raise ValueError(
                        f"{source.path}: frames [{start}, {stop}) came back as "
                        f"{frames.shape} {frames.dtype}, not ({stop - start}, {height}, "
                        f"{width}) {dtype}"
                    )
                fh.write(memoryview(np.ascontiguousarray(frames)).cast("B"))
            fh.flush()
        except OSError as exc:
            if exc.errno not in (errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC)):
                raise
            size = n_frames * height * width * dtype.itemsize
            raise OSError(
                exc.errno,
                f"{source.path}: no room in the temporary folder {tempfile.gettempdir()} "
                f"for the movie's frames ({size} bytes); free some space there, or "
                "export the movie to uncompressed TIFF",
            ) from exc
        # The map keeps its own handle, so it stays valid after the file object closes;
        # the operating system deletes the file when the map is gone.
        return np.memmap(fh, dtype=dtype, mode="r", shape=shape)


@dataclass(frozen=True)
class ExtractionMovie:
    """A movie opened for extraction: its frames as one array, and what extraction records.

    ``data`` is the ``(n_frames, height, width)`` array in the on-disk pixel type;
    ``frame_time`` is seconds per frame and ``pixel_size_um`` micrometres per pixel, each
    ``None`` when the file does not give it; ``container`` is the adapter's name.
    """

    path: Path
    container: str
    data: np.ndarray
    byteorder: str
    frame_time: float | None
    pixel_size_um: float | None

    @property
    def shape(self) -> tuple[int, int, int]:
        """``(n_frames, height, width)``."""
        n, h, w = self.data.shape
        return int(n), int(h), int(w)

    @property
    def dtype(self) -> np.dtype:
        """The on-disk pixel type."""
        return self.data.dtype


class _OpenExtractionMovie:
    """The context manager that [`open_extraction_movie`][] returns."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._source: MovieSource | None = None

    def __enter__(self) -> ExtractionMovie:
        from tether.io.movie import EXTRACTED_CONTAINERS, MovieReader  # noqa: PLC0415

        adapter = select_adapter(self._path)
        if adapter.name not in EXTRACTED_CONTAINERS:
            raise UnsupportedContainerError(
                f"{self._path}: this {adapter.name} movie opens, but extraction does not "
                f"read {adapter.name} yet; export the movie to uncompressed TIFF."
            )
        source = adapter.open(self._path)
        try:
            if isinstance(source, MovieReader):
                # The TIFF path is the one it always was: the reader's own memory map and
                # its own metadata properties.
                movie = ExtractionMovie(
                    path=self._path,
                    container=adapter.name,
                    data=source.data,
                    byteorder=source.byteorder,
                    frame_time=source.frame_time,
                    pixel_size_um=source.pixel_size_um,
                )
                self._source = source  # kept open: the map is the reader's
                return movie
            data = spool_frames(source, block=READ_BLOCK_FRAMES)
            metadata = source.metadata
        finally:
            if self._source is None:
                source.close()  # the frames are in the temporary file now, or it failed
        return ExtractionMovie(
            path=self._path,
            container=adapter.name,
            data=data,
            byteorder=_byteorder(data.dtype),
            frame_time=metadata.frame_time.value,
            pixel_size_um=metadata.pixel_size.value,
        )

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._source is not None:
            self._source.close()
            self._source = None


def open_extraction_movie(path: str | PathLike[str]) -> _OpenExtractionMovie:
    """Open ``path`` for extraction; use it as ``with open_extraction_movie(p) as movie:``.

    The adapter is chosen by [`tether.io.adapters.select_adapter`][]. A TIFF gives its
    memory map; a movie of another reader in [`tether.io.movie.EXTRACTED_CONTAINERS`][]
    is copied with [`spool_frames`][] and its reader closed at once. A reader that is not
    in that list raises [`tether.io.adapters.UnsupportedContainerError`][] with the
    container's name, before its frames are read.
    """
    return _OpenExtractionMovie(Path(path))


class FrameView:
    """A lazy, read-only ``(n_frames, height, width)`` view of a [`MovieSource`][].

    It has the array-like protocol that napari needs (``shape``, ``dtype``, ``ndim``,
    ``__getitem__``), as napari accepts dask and zarr arrays. An index reads only the
    frames it touches, with one ``read_frames`` call over their range; the rest of the
    index is then applied to those frames. The source must stay open while the view is
    used, and the caller closes it.
    """

    def __init__(self, source: MovieSource) -> None:
        self._source = source
        self.shape: tuple[int, int, int] = tuple(int(v) for v in source.shape)  # type: ignore[assignment]
        self.ndim = 3
        self.dtype: np.dtype = np.dtype(source.pixel_type)

    @property
    def size(self) -> int:
        """The number of pixels in the movie."""
        return int(np.prod(self.shape))

    def __len__(self) -> int:
        """Return the number of frames."""
        return self.shape[0]

    def __getitem__(self, index: object) -> np.ndarray:
        """Read the frames that ``index`` touches and apply the index to them."""
        rest: tuple[object, ...]
        if isinstance(index, tuple):
            first, rest = (index[0], index[1:]) if index else (slice(None), ())
        else:
            first, rest = index, ()
        n = self.shape[0]
        if isinstance(first, slice):
            start, stop, step = first.indices(n)
            if step > 0 and start < stop:
                frames = self._source.read_frames(start, stop)[::step]
            elif step < 0 and start > stop:
                low = stop + 1 + (start - stop - 1) % -step
                frames = self._source.read_frames(low, start + 1)[::step]
            else:
                frames = np.empty((0, *self.shape[1:]), dtype=self.dtype)
            return np.asarray(frames[(slice(None), *rest)])
        if isinstance(first, (int, np.integer)):
            i = int(first)
            if not -n <= i < n:
                raise IndexError(f"frame {i} out of range for a {n}-frame movie")
            i %= n
            return np.asarray(self._source.read_frames(i, i + 1)[0][rest])
        # Any other index (a list, a mask) reads the whole movie; napari does not use one.
        return np.asarray(self.__array__())[index]  # type: ignore[index]

    def __array__(self, dtype: np.dtype | None = None, copy: bool | None = None) -> np.ndarray:
        """Read the whole movie (a fallback only; display reads one frame at a time)."""
        if copy is False:
            raise ValueError(
                "FrameView reads frames from the file; a no-copy array is not possible"
            )
        out = np.asarray(self._source.read_frames(0, self.shape[0]))
        return out if dtype is None else out.astype(dtype, copy=False)

    def __iter__(self) -> Iterator[np.ndarray]:
        """Yield the frames one at a time."""
        for i in range(self.shape[0]):
            yield self[i]

    def __repr__(self) -> str:
        """Name the file and the shape."""
        n, h, w = self.shape
        return f"FrameView({self._source.path.name!r}, frames={n}, height={h}, width={w})"
