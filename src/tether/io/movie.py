# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Lazy, memory-mapped reader for the big-endian TIRF movie TIFF (PRD §5.2, App A).

The reference acquisitions are multi-page TIFFs — 512×512, 16-bit **big-endian**
(byteorder ``>``), uint16, ~1700 frames, ≈0.9 GB uncompressed (PRD Appendix A).
[`MovieReader`][] backs frame access with a [`numpy.memmap`][], so a single
frame is an O(1) slice into the OS page cache and the whole movie **never
materializes in RAM** — the ``trace → movie`` leg of the round-trip is a
``memmap`` seek + slice (PRD §5.2).

Big-endian is preserved end to end: ``tifffile.memmap`` maps the file with its
on-disk ``>u2`` dtype, so frame values read correctly with **no eager
byte-swap** (which would defeat the O(1) map). Frames are returned in on-disk
byte order; downstream math that wants native order converts per frame. The
[`MovieReader.byteorder`][] property surfaces the on-disk order so callers can
decide.

Only uncompressed, contiguous movies are memory-mappable (the reference format,
Appendix A); a compressed or tiled TIFF raises a clear [`ValueError`][] rather
than silently loading the stack. A lossy codec (JPEG and its kin) is refused
with [`tether.io.adapters.LossyCodecError`][], which names the codec, and a pixel
that is not one real intensity sample (RGB, a palette index, a complex number)
with [`tether.io.adapters.InvalidPixelError`][]; both are [`ValueError`][]s.

This reader is the first adapter of the movie registry ([`TIFF_ADAPTER`][],
[`tether.io.adapters`][]): it satisfies [`tether.io.adapters.MovieSource`][], and
[`open_movie`][] reaches it through [`tether.io.adapters.select_adapter`][], so a
file that is not a TIFF is refused with the list of containers Tether reads.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import tifffile

from tether.io.adapters import (
    InvalidPixelError,
    LossyCodecError,
    MetadataValue,
    MovieAdapter,
    MovieMetadata,
    UnsupportedContainerError,
    open_movie_source,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from os import PathLike
    from types import TracebackType

__all__ = ["TIFF_ADAPTER", "MovieReader", "open_movie"]

# TIFF compression schemes that discard information. Conversion from them cannot
# recover the lost quantitative values, so the reader refuses them by name rather
# than with the generic "not memory-mappable" message. JPEG 2000 is listed whole:
# the tag does not say whether a given stream is its lossless or its lossy mode.
_LOSSY_COMPRESSION = frozenset(
    {
        "OJPEG",
        "JPEG",
        "JPEG_99",
        "ALT_JPEG",
        "JPEG_LOSSY",
        "JPEGXR",
        "JPEGXR_NDPI",
        "JPEG2000",
        "JPEG_2000_LOSSY",
        "APERIO_JP2000_YCBC",
        "APERIO_JP2000_RGB",
        "JPEGXL",
        "JPEGXL_DNG",
        "WEBP",
        "WEBP_DEPRECATED",
        "LERC",
    }
)

# Pixel kinds that are one real intensity sample: unsigned, signed and float.
_VALID_PIXEL_KINDS = frozenset("uif")

_TIFF_MAGIC = (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")


class MovieReader:
    """Lazy, memory-mapped reader for a multi-page TIRF movie TIFF.

    Open with [`open_movie`][] (or directly) and read frames with
    [`frame`][]; the backing [`numpy.memmap`][] is exposed as [`data`][]
    for advanced lazy slicing. Use as a context manager (or call [`close`][])
    to drop the reader's reference to the map once done; the OS reclaims it when
    no frame views remain (see [`close`][]).

    Frames are returned as ``(height, width)`` views in the movie's on-disk byte
    order ([`byteorder`][]); reads are O(1) and never copy the whole stack.
    """

    def __init__(self, path: str | PathLike[str]) -> None:
        self._path = Path(path)
        # Read geometry / byte order / frame time once from the TIFF directory,
        # then release that handle; lazy frame access uses a separate memmap.
        with tifffile.TiffFile(self._path) as tif:
            _check_pixels(self._path, tif.pages[0])
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
        if series.dtype.kind not in _VALID_PIXEL_KINDS:
            raise InvalidPixelError(
                f"{self._path}: pixel type {series.dtype} is not a real intensity "
                "(a movie pixel must be an unsigned or signed integer, or a float)."
            )
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
    def pixel_type(self) -> str:
        """The on-disk pixel type as a NumPy dtype string (``'>u2'`` for the reference movie)."""
        return self._dtype.str

    @property
    def metadata(self) -> MovieMetadata:
        """The acquisition metadata, each value with its origin.

        The frame time is read from the ImageJ ``finterval`` tag ([`frame_time`][])
        and is ``"unknown"`` without it. This adapter reads no pixel size or channel
        names, so those two are ``"unknown"``.
        """
        ft = self._frame_time
        return MovieMetadata(
            frame_time=MetadataValue.from_file(ft) if ft is not None else MetadataValue.unknown()
        )

    @property
    def byteorder(self) -> str:
        """On-disk byte order: ``'>'`` (big-endian) or ``'<'`` (little-endian)."""
        return self._byteorder

    @property
    def frame_time(self) -> float | None:
        """Seconds per frame if the TIFF declares it (ImageJ ``finterval``), else None.

        The reference movies carry no frame-time tag, and no shipped importer
        supplies one either: the ``.tdat``/``.mat`` decode reads no ``FrameTime``
        ([`tether.io.tdat`][], [`tether.io.deeplasi`][]). This tag is therefore the
        only source ``/movies.frame_time`` has: a store built from those legacy files
        still reads the value off the raw movie TIFF the reconstruction points at, so
        it carries a real interval whenever that TIFF declares a finite, positive
        ``finterval``. Anything else — no tag, or a value that is unparseable,
        non-finite or non-positive — is rejected by `_read_frame_time` and
        collapses to the ``0.0`` "unknown" default.
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
        memory with [`data`][] and stays in the movie's on-disk byte order.
        """
        self._check_open()
        n = self._shape[0]
        if not -n <= index < n:
            raise IndexError(f"frame {index} out of range for a {n}-frame movie")
        return self._data[index]

    def read_frames(self, start: int, stop: int) -> np.ndarray:
        """Return frames ``start`` to ``stop - 1`` as a ``(stop - start, H, W)`` view.

        The result is a slice of [`data`][], so only the touched bytes are read and
        nothing is copied. ``0 <= start <= stop <= n_frames`` is required; there are
        no negative indices here, so an off-by-one cannot wrap to the movie's end.
        """
        self._check_open()
        n = self._shape[0]
        if not 0 <= start <= stop <= n:
            raise IndexError(
                f"frame range [{start}, {stop}) is not inside a {n}-frame movie "
                "(need 0 <= start <= stop <= n_frames)"
            )
        return self._data[start:stop]

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
        underlying ``mmap``: a frame returned by [`frame`][] is a *view* into
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


def _check_pixels(path: Path, page: tifffile.TiffPage) -> None:
    """Refuse a lossy codec, or a pixel that is not one real intensity sample.

    The checks read the first page's tags only; they run before the series is
    built, so a refusal names the cause instead of a later shape mismatch.
    """
    codec = getattr(page.compression, "name", str(page.compression))
    if codec in _LOSSY_COMPRESSION:
        raise LossyCodecError(
            f"{path}: the frames are stored with the lossy {codec} codec. A lossy codec "
            "has already discarded intensity information, and no conversion can recover "
            "it; export the movie again without compression from the original "
            "acquisition."
        )
    if page.photometric == tifffile.PHOTOMETRIC.PALETTE:
        raise InvalidPixelError(
            f"{path}: the pixels are palette (color-map) indices, not intensities."
        )
    if page.samplesperpixel != 1:
        raise InvalidPixelError(
            f"{path}: each pixel has {page.samplesperpixel} samples (for example RGB); "
            "a movie frame must hold one intensity sample per pixel."
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


def _is_tiff(head: bytes) -> bool:
    """Answer whether ``head`` starts with a TIFF or BigTIFF header, in either byte order."""
    return head[:4] in _TIFF_MAGIC


TIFF_ADAPTER = MovieAdapter(
    name="TIFF",
    extensions=(".tif", ".tiff"),
    sniff=_is_tiff,
    open=MovieReader,
)
"""The built-in adapter for uncompressed multi-page TIFF, the first in the registry."""


def open_movie(path: str | PathLike[str]) -> MovieReader:
    """Open ``path`` as a lazy [`MovieReader`][], through the adapter registry.

    The registry chooses the adapter ([`tether.io.adapters.select_adapter`][]), so a
    file that is not a TIFF raises [`tether.io.adapters.UnsupportedContainerError`][]
    with the list of containers Tether reads. The callers of this function use the
    TIFF reader's memory map ([`MovieReader.data`][]); a source from another adapter
    is therefore refused here until those callers read through
    [`tether.io.adapters.MovieSource.read_frames`][].
    """
    source = open_movie_source(path)
    if not isinstance(source, MovieReader):
        source.close()
        raise UnsupportedContainerError(
            f"{path}: this container opens, but extraction reads only TIFF today; "
            "export the movie to uncompressed TIFF."
        )
    return source
