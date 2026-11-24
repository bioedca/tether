# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reader for Zeiss ZEN ``.czi`` movies (PRD §7.1 FR-EXTRACT, Appendix A; ADR-0144).

[`CZI_ADAPTER`][] opens a CZI file as a [`tether.io.adapters.MovieSource`][]. The
pixels are read with **czifile** (BSD-3-Clause), one image subblock per frame, so a
frame-range read decodes only the frames it returns. czifile is imported inside
[`CziMovie`][]'s constructor and nowhere else (ADR-0113 rule 6), so importing this
module or ``tether.io`` does not import it.

**Tether reads a CZI file only when it can read all of it as one movie**, and it
refuses every other file with a message that names the reason. A movie here is one
scene, one channel, one Z plane and one tile per frame, at full resolution, with one
frame for each time point from the first to the last. A file with two channels, two
scenes, a Z stack, mosaic tiles, an image pyramid, a gap in its time points, or a part
of a multi-file acquisition is refused with
[`tether.io.adapters.UnsupportedContainerError`][]. A codec that can be lossy (JPEG,
JPEG XR) is refused with [`tether.io.adapters.LossyCodecError`][], and a pixel that is
not one real sample (BGR colour, complex) with
[`tether.io.adapters.InvalidPixelError`][]. The lossless codecs that Tether reads are
none, Zstd and Zstd with the byte shuffle.

The metadata comes from the file's XML document, each value with origin ``"file"``
or, when the file does not hold it, ``"unknown"``:

* frame time: ``Information/Image/Dimensions/T/Positions/Interval/Increment``
  (seconds per frame);
* pixel size: ``Scaling/Items/Distance`` ``X`` and ``Y`` (metres), in micrometres, only
  when the two are equal;
* channels: the ``Name`` of the one ``Channel`` of ``Information/Image/Dimensions``.
"""

from __future__ import annotations

import math
import struct
import threading
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from tether.io.adapters import (
    InvalidPixelError,
    LossyCodecError,
    MetadataValue,
    MovieAdapter,
    MovieMetadata,
    UnsupportedContainerError,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from os import PathLike
    from types import TracebackType
    from xml.etree.ElementTree import Element

__all__ = ["CZI_ADAPTER", "CziMovie"]

# A CZI file starts with the 16-byte segment id of its file header (ZISRAW).
_CZI_MAGIC = b"ZISRAWFILE" + b"\x00" * 6

# czifile's CziCompressionType names. Only the lossless codecs that czifile decodes and
# that the test files exercise are read; the two lossy ones are refused by name.
_READ_CODECS = frozenset({"UNCOMPRESSED", "ZSTD", "ZSTDHDR"})
_LOSSY_CODECS = {"JPEG": "JPEG", "JPEGXR": "JPEG XR"}

# The dimensions a movie frame is indexed by; every other one must hold a single value.
_FRAME_DIMS = frozenset({"T", "Y", "X", "S"})
_DIM_WORDS = {
    "C": "channels",
    "Z": "Z planes",
    "H": "phases",
    "I": "illumination directions",
    "R": "rotations",
    "B": "acquisition blocks",
    "V": "views",
}


def _refuse(path: Path, why: str) -> UnsupportedContainerError:
    return UnsupportedContainerError(
        f"{path}: Tether reads a CZI file only as one movie (one scene, one channel, one "
        f"Z plane, one tile for each frame), and this file {why}. Export the movie from "
        "ZEN to uncompressed TIFF, one channel and one position for each file."
    )


class CziMovie:
    """An open CZI movie, read one image subblock per frame.

    Open with [`tether.io.adapters.open_movie_source`][] or directly. The constructor
    reads the subblock directory and the XML metadata and refuses a file that it
    cannot read fully (see the module docstring); [`read_frames`][] then reads and
    decodes only the subblocks of the frames it returns.
    """

    def __init__(self, path: str | PathLike[str]) -> None:
        import czifile

        self._path = Path(path)
        self._lock = threading.Lock()
        self._closed = False
        # A damaged file fails inside czifile's parser as a ValueError (its CziFileError
        # among them) or a struct.error; both mean "cannot read this file", never a frame.
        damaged = (ValueError, struct.error)
        try:
            self._czi = czifile.CziFile(self._path)
        except damaged as exc:
            raise _refuse(self._path, f"cannot be parsed ({exc})") from exc
        try:
            try:
                header = self._czi.header
                entries = self._czi.subblock_directory
                root = self._czi.xml_element
            except damaged as exc:
                raise _refuse(self._path, f"has a damaged directory or metadata ({exc})") from exc
            self._entries = self._check_structure(header, entries)
            self._metadata = _read_metadata(root)
        except BaseException:
            self._czi.close()
            raise
        self._segment_type = czifile.CziSubBlockSegmentData

    def _check_structure(self, header: Any, entries: Sequence[Any]) -> tuple[Any, ...]:
        """Return the directory entries in frame order, or refuse the file."""
        path = self._path
        if header.file_part != 0 or header.primary_file_guid != header.file_guid:
            raise _refuse(path, "is one part of a multi-file acquisition")
        if not entries:
            raise _refuse(path, "holds no image subblock")
        if any(e.pyramid_type != 0 or e.is_pyramid for e in entries):
            raise _refuse(path, "holds an image pyramid or planes stored at another resolution")
        scenes = {e.scene_index for e in entries}
        if len(scenes) > 1:
            raise _refuse(path, f"holds {len(scenes)} scenes")
        tiles = {e.mosaic_index for e in entries}
        if len(tiles) > 1:
            raise _refuse(path, f"holds {len(tiles)} mosaic tiles")
        for e in entries:
            codec = e.compression.name
            if codec in _LOSSY_CODECS:
                raise LossyCodecError(
                    f"{path}: the frames use {_LOSSY_CODECS[codec]} compression, which can be "
                    "lossy, and the file does not say whether this stream is lossless. Tether "
                    "refuses it rather than guess. Export the movie from ZEN without "
                    "compression or with Zstd."
                )
            if codec not in _READ_CODECS:
                raise _refuse(path, f"uses the codec {codec}, which Tether does not read")
        pixel_types = {e.pixel_type for e in entries}
        if len(pixel_types) > 1:
            raise _refuse(path, "mixes pixel types")
        (pixel,) = pixel_types
        dtype = np.dtype(pixel.dtype)
        if pixel.name == "UNKNOWN" or pixel.samples != 1 or dtype.kind not in "uif":
            raise InvalidPixelError(
                f"{path}: the pixel type {pixel.name} is not one real intensity sample "
                "(a movie pixel must be an unsigned or signed integer, or a float)."
            )
        if len({e.dims for e in entries}) > 1:
            raise _refuse(path, "has subblocks with different dimensions")
        dims = entries[0].dims
        for d in dims:
            if d in _FRAME_DIMS:
                continue
            i = dims.index(d)
            word = _DIM_WORDS.get(d, f"values of the dimension {d}")
            if any(e.shape[i] != 1 for e in entries):
                raise _refuse(path, f"stores more than one of its {word} in a subblock")
            values = {e.start[i] for e in entries}
            if len(values) > 1:
                raise _refuse(path, f"holds {len(values)} {word}")
        y, x = dims.index("Y"), dims.index("X")
        if len({(e.start[y], e.start[x], e.shape[y], e.shape[x]) for e in entries}) > 1:
            raise _refuse(path, "has frames of different size or position")
        if "T" in dims:
            t = dims.index("T")
            if any(e.shape[t] != 1 for e in entries):
                raise _refuse(path, "stores more than one time point in a subblock")
            ordered = tuple(sorted(entries, key=lambda e: e.start[t]))
            first = ordered[0].start[t]
            if [e.start[t] for e in ordered] != list(range(first, first + len(ordered))):
                raise _refuse(path, "has time points that are missing or repeated")
        elif len(entries) > 1:
            raise _refuse(path, "has no time dimension but more than one subblock")
        else:
            ordered = tuple(entries)
        self._shape = (len(ordered), int(entries[0].shape[y]), int(entries[0].shape[x]))
        self._dtype = dtype
        return ordered

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
        """The number of frames."""
        return self._shape[0]

    @property
    def pixel_type(self) -> str:
        """The on-disk pixel type, for example ``'<u2'`` (CZI pixels are little-endian)."""
        return self._dtype.str

    @property
    def metadata(self) -> MovieMetadata:
        """The frame time, pixel size and channel name, each with its origin."""
        return self._metadata

    def read_frames(self, start: int, stop: int) -> np.ndarray:
        """Return frames ``start`` to ``stop - 1``; ``0 <= start <= stop <= n_frames``.

        Only the subblocks of these frames are read and decoded. Raise `IndexError`
        when the range is outside that bound.
        """
        if self._closed:
            raise ValueError(f"CziMovie for {self._path} is closed")
        n, h, w = self._shape
        if not 0 <= start <= stop <= n:
            raise IndexError(
                f"frame range [{start}, {stop}) is not inside a {n}-frame movie "
                "(need 0 <= start <= stop <= n_frames)"
            )
        out = np.empty((stop - start, h, w), dtype=self._dtype)
        with self._lock:
            for i, entry in enumerate(self._entries[start:stop]):
                segment = entry.read_segment_data(self._czi)
                if not isinstance(segment, self._segment_type):
                    raise ValueError(f"{self._path}: frame {start + i} is a deleted subblock")
                tile = segment.data()
                if tile.size != h * w:
                    raise ValueError(
                        f"{self._path}: frame {start + i} decodes to {tile.size} pixels, "
                        f"not {h} x {w}; refusing to serve it."
                    )
                out[i] = tile.reshape(h, w)
        return out

    def close(self) -> None:
        """Close the file (idempotent)."""
        if not self._closed:
            self._closed = True
            self._czi.close()

    def __enter__(self) -> CziMovie:
        """Enter the context; return the source."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the source."""
        self.close()

    def __repr__(self) -> str:
        """``CziMovie('movie.czi', frames=…, height=…, width=…, dtype='<u2')``."""
        n, h, w = self._shape
        return (
            f"{type(self).__name__}({self._path.name!r}, frames={n}, "
            f"height={h}, width={w}, dtype={self._dtype.str!r})"
        )


def _positive(text: str | None) -> float | None:
    """Return the number in ``text`` when it is finite and positive, else None."""
    try:
        value = float(text) if text is not None else math.nan
    except ValueError:
        return None
    return value if math.isfinite(value) and value > 0 else None


def _read_metadata(root: Element | None) -> MovieMetadata:
    """Read the frame time, pixel size and channel name from the CZI XML document."""
    if root is None:
        return MovieMetadata()
    image = "Metadata/Information/Image/Dimensions"
    increment = _positive(root.findtext(f"{image}/T/Positions/Interval/Increment"))
    frame_time = (
        MetadataValue.from_file(increment) if increment is not None else MetadataValue.unknown()
    )
    scale = {
        d.get("Id"): _positive(d.findtext("Value"))
        for d in root.iterfind("Metadata/Scaling/Items/Distance")
    }
    sx, sy = scale.get("X"), scale.get("Y")
    pixel_size: MetadataValue[float] = MetadataValue.unknown()
    if sx is not None and sy is not None and math.isclose(sx, sy, rel_tol=1e-6):
        # Metres to micrometres on the decimal text, so "1E-07" gives 0.1 and not
        # 0.09999999999999999 (a float multiplication by 1e6 would).
        pixel_size = MetadataValue.from_file(float(Decimal(repr(sx)).scaleb(6)))
    names = [c.get("Name") for c in root.iterfind(f"{image}/Channels/Channel")]
    channels: MetadataValue[tuple[str, ...]] = MetadataValue.unknown()
    if len(names) == 1 and names[0]:
        channels = MetadataValue.from_file((names[0],))
    return MovieMetadata(frame_time=frame_time, pixel_size=pixel_size, channels=channels)


def _is_czi(head: bytes) -> bool:
    """Answer whether ``head`` starts with the ZISRAW file-header segment id."""
    return head[:16] == _CZI_MAGIC


CZI_ADAPTER = MovieAdapter(
    name="CZI",
    extensions=(".czi",),
    sniff=_is_czi,
    open=CziMovie,
)
"""The built-in adapter for Zeiss ZEN ``.czi`` movies (ADR-0144)."""
