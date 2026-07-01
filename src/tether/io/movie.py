# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Lazy, memory-mapped reader for the big-endian TIRF movie TIFF (PRD §5.2, App A).

The reference acquisitions are multi-page TIFFs — 512×512, 16-bit **big-endian**
(byteorder ``>``), uint16, ~1700 frames, ≈0.9 GB uncompressed (PRD Appendix A).
:class:`MovieReader` backs frame access with a :class:`numpy.memmap`, so a single
frame is an O(1) slice into the OS page cache and the whole movie **never
materializes in RAM** — the ``trace → movie`` leg of the round-trip is a
``memmap`` seek + slice (PRD §5.2).

Big-endian is preserved end to end: ``tifffile.memmap`` maps the file with its
on-disk ``>u2`` dtype, so frame values read correctly with **no eager
byte-swap** (which would defeat the O(1) map). Frames are returned in on-disk
byte order; downstream math that wants native order converts per frame. The
:attr:`MovieReader.byteorder` property surfaces the on-disk order so callers can
decide.

Only uncompressed, contiguous movies are memory-mappable (the reference format,
Appendix A); a compressed or tiled TIFF raises a clear :class:`ValueError` rather
than silently loading the stack.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import tifffile

if TYPE_CHECKING:
    from collections.abc import Iterator
    from os import PathLike
    from types import TracebackType

__all__ = ["MovieReader", "open_movie"]


class MovieReader:
    """Lazy, memory-mapped reader for a multi-page TIRF movie TIFF.

    Open with :func:`open_movie` (or directly) and read frames with
    :meth:`frame`; the backing :class:`numpy.memmap` is exposed as :attr:`data`
    for advanced lazy slicing. Use as a context manager (or call :meth:`close`)
    to drop the reader's reference to the map once done; the OS reclaims it when
    no frame views remain (see :meth:`close`).

    Frames are returned as ``(height, width)`` views in the movie's on-disk byte
    order (:attr:`byteorder`); reads are O(1) and never copy the whole stack.
    """

    def __init__(self, path: str | PathLike[str]) -> None:
        self._path = Path(path)
        # Read geometry / byte order / frame time once from the TIFF directory,
        # then release that handle; lazy frame access uses a separate memmap.
        with tifffile.TiffFile(self._path) as tif:
            series = tif.series[0]
            shape = tuple(int(x) for x in series.shape)
            if len(shape) != 3:
                raise ValueError(
                    f"{self._path}: expected a 3-D movie (frames, height, width), "
                    f"got shape {shape} (axes {series.axes!r})."
                )
            self._shape: tuple[int, int, int] = shape  # type: ignore[assignment]
            # ``tif.byteorder`` is the authoritative on-disk order ('<' or '>');
            # ``series.dtype`` is reported in *native* order, so it cannot be used
            # for the on-disk dtype.
            self._byteorder = ">" if tif.byteorder == ">" else "<"
            self._frame_time = _read_frame_time(tif)
        try:
            self._data: np.memmap = tifffile.memmap(self._path, mode="r")
        except (ValueError, MemoryError) as exc:
            raise ValueError(
                f"{self._path} is not memory-mappable; the lazy reader needs an "
                "uncompressed, contiguous TIFF (the reference movie format, PRD "
                "Appendix A)."
            ) from exc
        # Defensive parity check: the memmap is laid out from the series geometry,
        # so a disagreement means tifffile mapped a different shape than it
        # reported (e.g. a malformed / non-contiguous file it failed to reject) —
        # serving frames from it would silently return wrong data.
        if self._data.shape != self._shape:
            raise ValueError(
                f"{self._path}: memory-map shape {self._data.shape} does not match "
                f"the TIFF series geometry {self._shape}; refusing to serve frames."
            )
        # Take the dtype from the map itself so it carries the true on-disk byte
        # order ('>u2' for the reference movie) and matches every frame view.
        self._dtype = self._data.dtype
        self._closed = False

    # --- geometry & metadata -------------------------------------------------

    @property
    def path(self) -> Path:
        """The movie's filesystem path."""
        return self._path

    @property
    def shape(self) -> tuple[int, int, int]:
        """``(n_frames, height, width)``."""
        return self._shape

    @property
    def n_frames(self) -> int:
        """Number of frames (pages) in the movie."""
        return self._shape[0]

    @property
    def height(self) -> int:
        """Frame height in pixels."""
        return self._shape[1]

    @property
    def width(self) -> int:
        """Frame width in pixels."""
        return self._shape[2]

    @property
    def dtype(self) -> np.dtype:
        """On-disk pixel dtype (e.g. big-endian ``>u2`` for the reference movie)."""
        return self._dtype

    @property
    def byteorder(self) -> str:
        """On-disk byte order: ``'>'`` (big-endian) or ``'<'`` (little-endian)."""
        return self._byteorder

    @property
    def frame_time(self) -> float | None:
        """Seconds per frame if the TIFF declares it (ImageJ ``finterval``), else None.

        The reference movies carry no frame-time tag; the authoritative value
        comes from the Deep-LASI ``.tdat``/``.mat`` (``FrameTime``) at import.
        """
        return self._frame_time

    @property
    def data(self) -> np.memmap:
        """The lazy ``(n_frames, H, W)`` memory map backing the movie.

        Slicing it (``reader.data[10:20]``) reads only the touched bytes; it
        never copies the full stack into RAM.
        """
        self._check_open()
        return self._data

    # --- frame access --------------------------------------------------------

    def frame(self, index: int) -> np.ndarray:
        """Return frame ``index`` as a ``(height, width)`` view — O(1), no copy.

        Supports negative indices (numpy convention). The returned array shares
        memory with :attr:`data` and stays in the movie's on-disk byte order.
        """
        self._check_open()
        n = self._shape[0]
        if not -n <= index < n:
            raise IndexError(f"frame {index} out of range for a {n}-frame movie")
        return self._data[index]

    def __len__(self) -> int:
        return self._shape[0]

    def __iter__(self) -> Iterator[np.ndarray]:
        """Yield frames lazily, one O(1) memmap view at a time."""
        self._check_open()
        for i in range(self._shape[0]):
            yield self._data[i]

    # --- lifecycle -----------------------------------------------------------

    def close(self) -> None:
        """Release our reference to the memory map (idempotent).

        Drops the reader's strong reference rather than force-closing the
        underlying ``mmap``: a frame returned by :meth:`frame` is a *view* into
        the map, and closing the map out from under a live view is a
        use-after-free (garbage reads or a crash on Windows). The OS map is
        reclaimed once the reader and any outstanding frame views are gone.
        """
        if not self._closed:
            self._closed = True
            self._data = None  # type: ignore[assignment]

    def __enter__(self) -> MovieReader:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def _check_open(self) -> None:
        if self._closed:
            raise ValueError(f"MovieReader for {self._path} is closed")

    def __repr__(self) -> str:
        n, h, w = self._shape
        return (
            f"{type(self).__name__}({self._path.name!r}, frames={n}, "
            f"height={h}, width={w}, dtype={self._dtype.str!r})"
        )


def _read_frame_time(tif: tifffile.TiffFile) -> float | None:
    """Best-effort seconds-per-frame from ImageJ ``finterval``; None if absent/invalid.

    Requires a **finite, positive** interval: a non-finite value (``inf``/``nan``,
    e.g. from a corrupt ``finterval`` such as the literal string ``"inf"``) is
    rejected here at the metadata boundary rather than propagated as a poisoned
    seconds axis to downstream consumers (``float("inf") > 0`` is ``True``, so the
    bare ``> 0`` test alone would let it through).
    """
    meta = tif.imagej_metadata or {}
    val = meta.get("finterval")
    if val is None:
        return None
    try:
        ft = float(val)
    except (TypeError, ValueError):
        return None
    return ft if math.isfinite(ft) and ft > 0 else None


def open_movie(path: str | PathLike[str]) -> MovieReader:
    """Open ``path`` as a lazy :class:`MovieReader`."""
    return MovieReader(path)
