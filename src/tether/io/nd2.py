# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tether's own reader for Nikon NIS-Elements ``.nd2`` movies (#902, ADR-0148).

An ND2 file of format version 3 is a sequence of *chunks*. Each chunk starts with a
16-byte header (the magic ``0x0ABECEDA``, the length of its name and the length of
its data), then its name (for example ``ImageDataSeq|7!``) and its data. The last 40
bytes of the file give the place of the *chunk map*, which names every chunk and its
place. The metadata chunks hold a binary tree of named, typed values; the frame
chunk ``ImageDataSeq|i!`` holds the acquisition time of frame ``i`` in milliseconds
(one float64) and then its pixels, row by row, little-endian.

[`Nd2Movie`][] reads only that layout, and only the files that it can read fully:
one camera channel of one sample per pixel, frames that are not compressed, rows
with no padding, and no loop other than time. A file outside that set is refused
with a message that names the cause; it is never read in part. The registry
entry is [`ND2_ADAPTER`][].

No reader package is imported: the `nd2` package that the conda-forge channel
gives pulls the full Dask, Distributed and PyArrow stack into the base lock, far
over the size limit of ADR-0113. The layout here was checked against the `nd2`
package 0.12.0 (BSD-3-Clause) and Bio-Formats 7.3.1, which read the public test
files to the same values (``tests/fixtures/PROVENANCE.md``).
"""

from __future__ import annotations

import math
import struct
import threading
import zlib
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
    from os import PathLike
    from types import TracebackType

__all__ = ["ND2_ADAPTER", "Nd2Movie"]

_CHUNK_MAGIC = 0x0ABECEDA
_HEADER = struct.Struct("<IIQ")  # magic, name length, data length
_FILE_SIGNATURE = b"ND2 FILE SIGNATURE CHUNK NAME01!"
_CHUNKMAP_SIGNATURE = b"ND2 CHUNK MAP SIGNATURE 0000001!"
_FILEMAP_SIGNATURE = b"ND2 FILEMAP SIGNATURE NAME 0001!"
# The first ND2 format (version 1) is a JPEG 2000 file; it starts with this box.
_JP2_SIGNATURE = b"\x00\x00\x00\x0cjP  "
_TIMESTAMP = struct.Struct("<d")  # the first 8 data bytes of a frame chunk, in ms

# Loop types of the experiment tree (SLxExperiment.eType) that are not time.
_OTHER_LOOPS = {2: "XY positions", 4: "Z planes", 6: "spectral planes", 7: "custom loop"}
_TIME_LOOPS = frozenset({1, 8})  # TimeLoop, NETimeLoop

# The pixel type follows the bits in memory, as both reference readers do.
_DTYPES = {8: "<u1", 16: "<u2", 32: "<f4"}


class _CorruptError(UnsupportedContainerError):
    """The ND2 structure is not what this reader expects (corrupt or unknown variant)."""


# --- the binary metadata tree ("LV" variant) -------------------------------------

_SCALARS: dict[int, struct.Struct] = {
    1: struct.Struct("<B"),  # bool
    2: struct.Struct("<i"),  # int32
    3: struct.Struct("<I"),  # uint32
    4: struct.Struct("<q"),  # int64
    5: struct.Struct("<Q"),  # uint64
    6: struct.Struct("<d"),  # double
    7: struct.Struct("<Q"),  # void pointer
}
_LEVEL = struct.Struct("<IQ")  # item count, length from the start of the record
_U64 = struct.Struct("<Q")


def decode_metadata(data: bytes) -> dict[str, Any]:
    """Decode one binary metadata chunk into nested dicts.

    A record is a type byte, a name length in UTF-16 code units and the name, then
    the value. Type 11 is a level: an item count, a length, the items, and one
    8-byte offset for each item. Items with an empty name are list items, so a
    level whose only key is ``""`` decodes to a list. A whole chunk can be
    deflated (type 76). A type this decoder does not know raises an
    [`UnsupportedContainerError`][tether.io.adapters.UnsupportedContainerError].
    """
    if data[:1] == b"L":  # type 76: two header bytes, ten more, then a zlib stream
        data = zlib.decompress(data[12:])
    out, _ = _records(data, 0, len(data), None)
    return out


def _records(buf: bytes, pos: int, end: int, count: int | None) -> tuple[dict[str, Any], int]:
    out: dict[str, Any] = {}
    n = 0
    while pos < end and (count is None or n < count):
        name, value, pos = _record(buf, pos, end)
        if name == "":  # a list item
            out.setdefault("", []).append(value)
        else:
            out[name] = value
        n += 1
    return out, pos


def _record(buf: bytes, pos: int, end: int) -> tuple[str, Any, int]:
    start = pos
    if pos + 2 > end:
        raise _CorruptError("a metadata record is cut short")
    kind, name_len = buf[pos], buf[pos + 1]
    pos += 2
    name = buf[pos : pos + 2 * name_len].decode("utf-16-le", errors="replace")[:-1]
    pos += 2 * name_len
    if kind in _SCALARS:
        fmt = _SCALARS[kind]
        (value,) = fmt.unpack_from(buf, pos)
        return name, bool(value) if kind == 1 else value, pos + fmt.size
    if kind == 8:  # UTF-16 text that ends at the first two-byte zero
        stop = pos
        while stop + 1 < end and buf[stop : stop + 2] != b"\x00\x00":
            stop += 2
        return name, buf[pos:stop].decode("utf-16-le", errors="replace"), stop + 2
    if kind == 9:  # byte array
        (size,) = _U64.unpack_from(buf, pos)
        pos += _U64.size
        return name, bytes(buf[pos : pos + size]), pos + size
    if kind == 11:
        n_items, length = _LEVEL.unpack_from(buf, pos)
        pos += _LEVEL.size
        child_end = start + length
        if child_end > end or child_end < pos:
            raise _CorruptError("a metadata level runs past its chunk")
        items, _ = _records(buf, pos, child_end, n_items)
        value: Any = items[""] if list(items) == [""] else items
        return name, value, child_end + 8 * n_items
    raise _CorruptError(f"metadata value type {kind} is not one this reader decodes")


# --- the reader --------------------------------------------------------------------


def _read_chunk(fh: Any, offset: int, path: Path) -> tuple[bytes, int, int]:
    """Return ``(name, data offset, data length)`` of the chunk whose header is at ``offset``."""
    fh.seek(offset)
    header = fh.read(_HEADER.size)
    if len(header) < _HEADER.size:
        raise _CorruptError(f"{path}: a chunk at byte {offset} is past the end of the file")
    magic, name_len, data_len = _HEADER.unpack(header)
    if magic != _CHUNK_MAGIC:
        raise _CorruptError(f"{path}: no ND2 chunk at byte {offset}; the file may be corrupt")
    name = fh.read(name_len).split(b"\x00", 1)[0]
    return name, offset + _HEADER.size + name_len, data_len


def _chunk_map(fh: Any, path: Path) -> dict[bytes, int]:
    """Read the chunk map at the end of the file: chunk name to header offset."""
    size = fh.seek(0, 2)
    if size < 40:
        raise _CorruptError(f"{path}: the file is too short to be an ND2 movie")
    fh.seek(size - 40)
    sig, location = struct.unpack("<32sQ", fh.read(40))
    if sig != _CHUNKMAP_SIGNATURE:
        raise _CorruptError(
            f"{path}: the ND2 chunk map is missing at the end of the file; the file may be "
            "an incomplete copy or an acquisition that did not finish"
        )
    name, data_at, data_len = _read_chunk(fh, location, path)
    if name != _FILEMAP_SIGNATURE:
        raise _CorruptError(f"{path}: the ND2 chunk map does not start where the file says")
    fh.seek(data_at)
    data = fh.read(data_len)
    chunks: dict[bytes, int] = {}
    pos = 0
    while True:
        bang = data.find(b"!", pos)
        if bang < 0:
            raise _CorruptError(f"{path}: the ND2 chunk map has no end signature")
        key = data[pos : bang + 1]
        if key == _CHUNKMAP_SIGNATURE:
            return chunks
        (chunks[key],) = _U64.unpack_from(data, bang + 1)  # then the 8-byte size
        pos = bang + 17


def _loops(node: Any) -> list[tuple[int, int]]:
    """Return ``(loop type, count)`` for every loop of an ``SLxExperiment`` tree."""
    if not isinstance(node, dict):
        return []
    kind = int(node.get("eType", 0))
    params = node.get("uLoopPars") or {}
    if isinstance(params, list):  # a level with one unnamed item
        params = params[0] if params else {}
    found: list[tuple[int, int]] = []
    if isinstance(params, dict) and params:
        valid = node.get("pItemValid")
        if kind == 2 and isinstance(valid, (bytes, list)):
            count = sum(1 for v in valid if v)
        else:
            count = int(params.get("uiCount", 0))
        found.append((kind, count))
    children = node.get("ppNextLevelEx") or []
    if isinstance(children, dict):
        children = list(children.values())
    for child in children:
        found.extend(_loops(child))
    return found


class Nd2Movie:
    """An ND2 movie as a [`MovieSource`][tether.io.adapters.MovieSource].

    Opening reads the chunk map, the image attributes, the experiment loops, the
    picture metadata and the header of every frame chunk, so every refusal comes at
    open and a frame read cannot fail half-way through the movie. Frames are read
    from the file on request; ``read_frames`` returns a copy in the on-disk
    little-endian type.
    """

    def __init__(self, path: str | PathLike[str]) -> None:
        self._path = Path(path)
        self._closed = False
        self._lock = threading.Lock()
        fh = self._path.open("rb")
        try:
            self._open(fh)
        except BaseException:
            fh.close()
            raise
        self._fh = fh

    def _open(self, fh: Any) -> None:
        p = self._path
        head = fh.read(_HEADER.size + 32 + 64)
        if head[:8] == _JP2_SIGNATURE:
            raise LossyCodecError(
                f"{p}: this is a JPEG 2000 file, which is how the first ND2 format stores "
                "its frames. JPEG 2000 can be lossy and the file does not show that it was "
                "not, so Tether refuses it. Export the movie to uncompressed TIFF from "
                "NIS-Elements, or save it again as a current ND2 file without compression."
            )
        if len(head) < 112 or head[16:48] != _FILE_SIGNATURE:
            raise _CorruptError(f"{p}: the ND2 file header is not complete")
        version = head[48:112].split(b"\x00", 1)[0].decode("ascii", errors="replace")
        if not version.startswith("Ver3."):
            raise UnsupportedContainerError(
                f"{p}: this ND2 file is format {version or 'unknown'}; Tether reads ND2 "
                "format version 3 (NIS-Elements 4.0 and later). Save it again from a "
                "current NIS-Elements, or export it to uncompressed TIFF."
            )
        chunks = _chunk_map(fh, p)

        def meta(key: bytes) -> dict[str, Any] | None:
            if key not in chunks:
                return None
            _, at, length = _read_chunk(fh, chunks[key], p)
            fh.seek(at)
            return decode_metadata(fh.read(length))

        attributes = meta(b"ImageAttributesLV!")
        if attributes is None:
            raise _CorruptError(f"{p}: the ND2 file has no image attributes")
        attrs = attributes.get("SLxImageAttributes", attributes)
        experiment = meta(b"ImageMetadataLV!") or {}
        picture = meta(b"ImageMetadataSeqLV|0!") or {}
        picture = picture.get("SLxPictureMetadata", picture)

        self._check(attrs, _loops(experiment.get("SLxExperiment", experiment)))
        self._height = int(attrs["uiHeight"])
        self._width = int(attrs["uiWidth"])
        n = int(attrs["uiSequenceCount"])
        frame_bytes = self._height * self._width * self._dtype.itemsize
        self._offsets: list[int] = []
        times: list[float] = []
        for i in range(n):
            key = b"ImageDataSeq|%d!" % i
            if key not in chunks:
                raise _CorruptError(
                    f"{p}: frame {i} of {n} is missing from the file; Tether does not "
                    "read an ND2 movie in part"
                )
            name, at, length = _read_chunk(fh, chunks[key], p)
            if name != key or length < _TIMESTAMP.size + frame_bytes:
                raise _CorruptError(f"{p}: the chunk of frame {i} is not a complete frame")
            if i in (0, n - 1):
                fh.seek(at)
                times.append(_TIMESTAMP.unpack(fh.read(_TIMESTAMP.size))[0])
            self._offsets.append(at + _TIMESTAMP.size)
        self._metadata = MovieMetadata(
            frame_time=_frame_time(times, n),
            pixel_size=_pixel_size(picture),
            channels=_channels(picture),
        )

    def _check(self, attrs: dict[str, Any], loops: list[tuple[int, int]]) -> None:
        """Refuse what this reader cannot read fully; set the pixel type."""
        p = self._path
        compression = int(attrs.get("eCompression", 2))
        if compression == 1:
            raise LossyCodecError(
                f"{p}: the ND2 frames are stored with lossy compression; no conversion "
                "can recover the discarded intensity values. Acquire without compression."
            )
        if compression != 2:
            raise UnsupportedContainerError(
                f"{p}: the ND2 frames are compressed (lossless). Tether does not read "
                "compressed ND2 frames yet; export the movie to uncompressed TIFF."
            )
        width, height = int(attrs["uiWidth"]), int(attrs["uiHeight"])
        tile_w, tile_h = int(attrs.get("uiTileWidth", 0)), int(attrs.get("uiTileHeight", 0))
        if (0 < tile_w != width) or (0 < tile_h != height):
            raise UnsupportedContainerError(f"{p}: the ND2 frames are stored in tiles")
        other = sorted({_OTHER_LOOPS[k] for k, c in loops if k in _OTHER_LOOPS and c > 1})
        if other or any(k not in _OTHER_LOOPS and k not in _TIME_LOOPS for k, c in loops if c > 1):
            raise UnsupportedContainerError(
                f"{p}: the ND2 file holds more than a time series "
                f"({', '.join(other) or 'an unknown loop'}). Tether reads one time series "
                "of one field of view; export each one to its own TIFF."
            )
        components = int(attrs["uiComp"])
        if components != 1:
            raise InvalidPixelError(
                f"{p}: each pixel has {components} components (camera channels or RGB "
                "samples); a movie frame must hold one intensity sample per pixel. "
                "Export each channel to its own TIFF."
            )
        bits = int(attrs["uiBpcInMemory"])
        if bits not in _DTYPES:
            raise InvalidPixelError(f"{p}: {bits}-bit ND2 pixels are not a type Tether reads")
        self._dtype = np.dtype(_DTYPES[bits])
        row_bytes = int(attrs["uiWidthBytes"])
        if row_bytes != width * self._dtype.itemsize:
            # Two reference readers disagree on padded rows (the nd2 package skips the
            # padding, Bio-Formats 7.3.1 reads it as pixels), so refuse, not guess.
            raise UnsupportedContainerError(
                f"{p}: each ND2 row holds {row_bytes} bytes for {width} pixels of "
                f"{self._dtype.itemsize} bytes; Tether does not read padded ND2 rows. "
                "Export the movie to uncompressed TIFF."
            )
        if int(attrs["uiSequenceCount"]) < 1:
            raise _CorruptError(f"{p}: the ND2 file holds no frame")

    # --- the MovieSource interface ---------------------------------------------

    @property
    def path(self) -> Path:
        """The movie's filesystem path."""
        return self._path

    @property
    def shape(self) -> tuple[int, int, int]:
        """``(n_frames, height, width)``."""
        return (len(self._offsets), self._height, self._width)

    @property
    def n_frames(self) -> int:
        """The number of frames."""
        return len(self._offsets)

    @property
    def pixel_type(self) -> str:
        """The on-disk pixel type, for example ``'<u2'`` (ND2 is little-endian)."""
        return self._dtype.str

    @property
    def metadata(self) -> MovieMetadata:
        """Frame time, pixel size and channel names, each with its origin."""
        return self._metadata

    def read_frames(self, start: int, stop: int) -> np.ndarray:
        """Return frames ``start`` to ``stop - 1`` as a ``(stop - start, H, W)`` copy.

        ``0 <= start <= stop <= n_frames`` is required, else `IndexError`. Only those
        frames are read from the file.
        """
        if self._closed:
            raise ValueError(f"Nd2Movie for {self._path} is closed")
        n = len(self._offsets)
        if not 0 <= start <= stop <= n:
            raise IndexError(
                f"frame range [{start}, {stop}) is not inside a {n}-frame movie "
                "(need 0 <= start <= stop <= n_frames)"
            )
        out = np.empty((stop - start, self._height, self._width), dtype=self._dtype)
        with self._lock:
            for k, i in enumerate(range(start, stop)):
                self._fh.seek(self._offsets[i])
                if self._fh.readinto(memoryview(out[k]).cast("B")) != out[k].nbytes:
                    raise _CorruptError(f"{self._path}: frame {i} is cut short")
        return out

    def close(self) -> None:
        """Close the file (idempotent)."""
        if not self._closed:
            self._closed = True
            self._fh.close()

    def __enter__(self) -> Nd2Movie:
        """Return the movie, to be closed when the block ends."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the file."""
        self.close()

    def __repr__(self) -> str:
        """Name the file and the shape."""
        n, h, w = self.shape
        return f"Nd2Movie({self._path.name!r}, frames={n}, height={h}, width={w})"


def _frame_time(times: list[float], n: int) -> MetadataValue[float]:
    """Seconds per frame from the first and the last frame's acquisition time."""
    if n < 2 or len(times) != 2:
        return MetadataValue.unknown()
    seconds = (times[1] - times[0]) / (n - 1) / 1000.0
    if math.isfinite(seconds) and seconds > 0:
        return MetadataValue.from_file(seconds)
    return MetadataValue.unknown()


def _pixel_size(picture: dict[str, Any]) -> MetadataValue[float]:
    """Micrometres per pixel when the file says the calibration is set."""
    size = picture.get("dCalibration")
    calibrated = picture.get("bCalibrated") and isinstance(size, float)
    if calibrated and math.isfinite(size) and size > 0:
        return MetadataValue.from_file(size)
    return MetadataValue.unknown()


def _channels(picture: dict[str, Any]) -> MetadataValue[tuple[str, ...]]:
    """Read the channel names (``sDescription``) of the picture planes, in file order."""
    planes = picture.get("sPicturePlanes")
    if not isinstance(planes, dict):
        return MetadataValue.unknown()
    plane_list = planes.get("sPlaneNew") or planes.get("sPlane")
    if not isinstance(plane_list, dict):
        return MetadataValue.unknown()
    names = tuple(
        str(v.get("sDescription", "")) for v in plane_list.values() if isinstance(v, dict)
    )
    return MetadataValue.from_file(names) if names else MetadataValue.unknown()


def _is_nd2(head: bytes) -> bool:
    """Answer whether ``head`` starts an ND2 file (the current format or the first one)."""
    if head[:8] == _JP2_SIGNATURE:
        return True
    if len(head) < _HEADER.size:
        return False
    magic, name_len, data_len = _HEADER.unpack_from(head)
    return magic == _CHUNK_MAGIC and name_len == 32 and data_len == 64


ND2_ADAPTER = MovieAdapter(name="ND2", extensions=(".nd2",), sniff=_is_nd2, open=Nd2Movie)
"""The built-in adapter for Nikon NIS-Elements ND2 movies (format version 3)."""
