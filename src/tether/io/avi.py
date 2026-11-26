# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reader for uncompressed ``.avi`` movies (PRD §7.1 FR-EXTRACT, Appendix A; ADR-0154).

[`AVI_ADAPTER`][] opens an AVI file as a [`tether.io.adapters.MovieSource`][]. Tether
reads the RIFF structure itself and needs no package. The open reads the headers and
the 8-byte header of each chunk, not the frames; a frame-range read seeks to the frames
it returns and reads only those.

**Tether reads an AVI file only when its frames are stored without compression and
each pixel is one 8-bit gray level**, and it refuses every other file with a message
that names the reason. It reads:

* uncompressed DIB frames (``BI_RGB``) of 8 bits a pixel whose color table is the gray
  ramp, entry *i* being red = green = blue = *i*, so the stored index is the gray level;
  rows bottom-up for a positive height and top-down for a negative one, each row padded
  to a multiple of 4 bytes, as the DIB format stores them;
* the 8-bit gray FourCCs ``Y800``, ``Y8`` and ``GREY``: rows top-down and not padded.

It refuses, with [`tether.io.adapters.LossyCodecError`][], a codec that can be lossy
(Motion JPEG, MPEG-4, H.264 and the like); with [`tether.io.adapters.InvalidPixelError`][],
color pixels (16-, 24- or 32-bit DIBs) and a color table that is not the gray ramp;
and with [`tether.io.adapters.UnsupportedContainerError`][], a lossless codec that Tether
does not decode (FFV1, HuffYUV, Lagarith, PNG, run-length DIBs), a codec it does not
know, two or more video streams, a file whose main header sets ``AVIF_MUSTUSEINDEX``
(its index, not the file order, orders the frames), a color-table change inside the
movie, a frame of the wrong size or of no bytes (a dropped frame), and a file that holds
fewer or more frames than its stream header declares or that ends inside a chunk (an
incomplete copy). A movie larger than 1 GB in the OpenDML layout (``RIFF AVIX``
extensions) is read whole.

The metadata, each value with origin ``"unknown"`` (ADR-0154):

* frame time: an AVI records the rate at which a player shows the frames, which the
  writing program chooses, often by default, and not the acquisition interval;
* pixel size and channels: an AVI does not record them.
"""

from __future__ import annotations

import io
import struct
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO

import numpy as np

from tether.io.adapters import (
    InvalidPixelError,
    LossyCodecError,
    MovieAdapter,
    MovieMetadata,
    UnsupportedContainerError,
)

if TYPE_CHECKING:
    from os import PathLike
    from types import TracebackType

__all__ = ["AVI_ADAPTER", "AviMovie"]

_DTYPE = np.dtype("u1")
_CHUNK = struct.Struct("<4sI")
# AVISTREAMHEADER up to dwLength, and BITMAPINFOHEADER.
_STRH = struct.Struct("<4s4sIHHIIIII")
_BIH = struct.Struct("<IiiHHIIiiII")
_BI_RGB, _BI_RLE8, _BI_RLE4, _BI_BITFIELDS = 0, 1, 2, 3
# AVIMAINHEADER.dwFlags: the index, not the file order, gives the order of the frames.
_AVIF_MUSTUSEINDEX = 0x20
# The 8-bit gray FourCCs, rows top-down and not padded.
_GREY = frozenset({b"Y800", b"Y8  ", b"GREY"})
# FourCCs of codecs that can be lossy, upper-cased; the name is for the message.
_LOSSY = {
    b"MJPG": "Motion JPEG",
    b"JPEG": "JPEG",
    b"AVRN": "Motion JPEG",
    b"DIVX": "MPEG-4 (DivX)",
    b"DX50": "MPEG-4 (DivX)",
    b"XVID": "MPEG-4 (Xvid)",
    b"FMP4": "MPEG-4",
    b"MP4V": "MPEG-4",
    b"MP42": "MPEG-4 v2",
    b"MP43": "MPEG-4 v3",
    b"H264": "H.264",
    b"X264": "H.264",
    b"AVC1": "H.264",
    b"HEVC": "H.265",
    b"H265": "H.265",
    b"HVC1": "H.265",
    b"WMV1": "Windows Media Video",
    b"WMV2": "Windows Media Video",
    b"WMV3": "Windows Media Video",
    b"WVC1": "VC-1",
    b"VP80": "VP8",
    b"VP90": "VP9",
    b"AV01": "AV1",
    b"CVID": "Cinepak",
    b"IV32": "Indeo",
    b"IV41": "Indeo",
    b"IV50": "Indeo",
    b"MPG1": "MPEG-1",
    b"MPG2": "MPEG-2",
    b"MSVC": "Microsoft Video 1",
    b"CRAM": "Microsoft Video 1",
    b"DVSD": "DV",
}
# FourCCs of lossless codecs that Tether does not decode.
_LOSSLESS = {
    b"FFV1": "FFV1",
    b"HFYU": "HuffYUV",
    b"FFVH": "HuffYUV",
    b"LAGS": "Lagarith",
    b"MPNG": "PNG",
    b"PNG1": "PNG",
    b"PNG ": "PNG",
    b"ZLIB": "zlib",
    b"MSZH": "MSZH",
    b"MAGY": "MagicYUV",
    b"ULY0": "Ut Video",
    b"ULY2": "Ut Video",
    b"ULRG": "Ut Video",
    b"ULRA": "Ut Video",
    b"ULY4": "Ut Video",
    b"ULH0": "Ut Video",
    b"ULH2": "Ut Video",
    b"LJPG": "lossless JPEG",
}
# A width or height beyond this is a damaged header, not a camera frame.
_SIDE_LIMIT = 1 << 16


def _refuse(path: Path, why: str) -> UnsupportedContainerError:
    return UnsupportedContainerError(
        f"{path}: Tether reads an AVI file only when it can read all of it (one video "
        f"stream of uncompressed 8-bit gray frames, every frame present), and this file "
        f"{why}. Export the movie again as an uncompressed 8-bit grayscale AVI, or as an "
        "uncompressed TIFF."
    )


def _fourcc(raw: bytes) -> str:
    """``raw`` for a message: printable ASCII as text, anything else as hex."""
    text = raw.decode("latin-1")
    return text.strip() if raw.isascii() and text.isprintable() else raw.hex()


def _named(name: str, code: bytes) -> str:
    """``'Motion JPEG (MJPG)'``, or only ``'FFV1'`` when the name is the FourCC."""
    shown = _fourcc(code)
    return name if shown.upper() == name.upper() else f"{name} ({shown})"


@dataclass(frozen=True)
class _Layout:
    """Where the frames are and how one frame is stored."""

    offsets: np.ndarray
    height: int
    width: int
    stride: int
    bottom_up: bool


@dataclass(frozen=True)
class _Stream:
    """The one video stream: its chunk tag, declared length and frame format."""

    tag: bytes
    length: int
    width: int
    height: int
    stride: int
    bottom_up: bool


def _read_exact(fh: BinaryIO, at: int, n: int) -> bytes:
    fh.seek(at)
    data = fh.read(n)
    return data if len(data) == n else b""


def _check_format(path: Path, bih: bytes) -> tuple[int, int, int, bool]:
    """Check the BITMAPINFOHEADER; return ``(width, height, stride, bottom_up)`` or refuse."""
    if len(bih) < _BIH.size:
        raise _refuse(path, "has a video format header cut short")
    size, width, height, _, bits, compression, _, _, _, used, _ = _BIH.unpack_from(bih)
    if compression == _BI_RGB:
        if bits in (16, 24, 32):
            raise InvalidPixelError(
                f"{path}: each pixel is a {bits}-bit color value, not one gray level. "
                "Export the movie as 8-bit grayscale."
            )
        if bits != 8:
            raise _refuse(path, f"stores {bits}-bit pixels")
        # The color table follows the header; with biClrUsed 0 it has all 256 entries.
        count = used or 256
        table = bih[size : size + 4 * count] if size >= _BIH.size else b""
        ramp = np.repeat(np.arange(256, dtype="u1"), 3).reshape(256, 3)
        # A short or odd-sized table is refused here, before it is read as 4-byte entries.
        whole = count == 256 and len(table) == 4 * count
        entries = np.frombuffer(table, dtype="u1").reshape(-1, 4)[:, :3] if whole else None
        if entries is None or not np.array_equal(entries, ramp):
            raise InvalidPixelError(
                f"{path}: the pixels are indices into a color table that is not the gray "
                "ramp (entry i = gray level i), so they are not the stored intensities. "
                "Export the movie as 8-bit grayscale with a linear gray table."
            )
        stride, bottom_up = (abs(width) + 3) // 4 * 4, height > 0
    elif compression in (_BI_RLE8, _BI_RLE4):
        raise _refuse(path, "uses run-length encoding, a lossless codec that Tether does not read")
    elif compression == _BI_BITFIELDS:
        raise InvalidPixelError(
            f"{path}: each pixel is a {bits}-bit color value, not one gray level. Export the "
            "movie as 8-bit grayscale."
        )
    else:
        code = struct.pack("<I", compression)
        upper = code.upper()
        if upper in _GREY and bits == 8:
            # FourCC (YUV-family) frames are top-down whatever the sign of the height.
            stride, bottom_up = abs(width), False
        elif upper in _GREY:
            raise _refuse(path, f"labels {bits}-bit pixels as {_fourcc(code)}")
        elif upper in _LOSSY:
            raise LossyCodecError(
                f"{path}: the frames use {_named(_LOSSY[upper], code)}, which can be lossy, "
                "so the pixel values may not be the measured ones. Tether refuses it rather "
                "than guess. Export the movie again without compression."
            )
        elif upper in _LOSSLESS:
            raise _refuse(
                path,
                f"uses {_named(_LOSSLESS[upper], code)}, a lossless codec that Tether does "
                "not decode",
            )
        else:
            raise _refuse(
                path,
                f"uses the codec {_fourcc(code)}, which Tether does not know, so it cannot "
                "tell whether the codec is lossless",
            )
    width, height = abs(width), abs(height)
    if not (1 <= width <= _SIDE_LIMIT and 1 <= height <= _SIDE_LIMIT):
        raise _refuse(path, f"has a damaged frame size ({width} x {height})")
    return width, height, stride, bottom_up


def _children(path: Path, fh: BinaryIO, start: int, end: int) -> list[tuple[bytes, int, int]]:
    """Return ``(id, data offset, size)`` of each chunk from ``start`` to ``end``, or refuse."""
    out = []
    at = start
    while at < end:
        if end - at < _CHUNK.size:
            raise _refuse(path, "ends inside a chunk header (an incomplete copy)")
        cid, size = _CHUNK.unpack(_read_exact(fh, at, _CHUNK.size))
        if at + _CHUNK.size + size > end:
            raise _refuse(path, f"ends inside its chunk {_fourcc(cid)} (an incomplete copy)")
        out.append((cid, at + _CHUNK.size, size))
        # A chunk of odd size is followed by one pad byte.
        at += _CHUNK.size + size + (size & 1)
    return out


def _list_type(fh: BinaryIO, offset: int, size: int) -> bytes:
    return _read_exact(fh, offset, 4) if size >= 4 else b""


def _find_stream(path: Path, fh: BinaryIO, hdrl: tuple[int, int]) -> _Stream:
    """Parse ``hdrl``: exactly one video stream, its format checked, or refuse."""
    video = []
    index = 0
    for cid, offset, size in _children(path, fh, hdrl[0] + 4, hdrl[0] + hdrl[1]):
        if cid == b"avih" and size >= 16:
            (flags,) = struct.unpack("<I", _read_exact(fh, offset + 12, 4))
            if flags & _AVIF_MUSTUSEINDEX:
                raise _refuse(
                    path,
                    "sets AVIF_MUSTUSEINDEX, so its index and not the order in the file gives "
                    "the order of its frames, and Tether reads frames in file order",
                )
            continue
        if cid != b"LIST" or _list_type(fh, offset, size) != b"strl":
            continue
        parts = {c: (o, s) for c, o, s in reversed(_children(path, fh, offset + 4, offset + size))}
        tag = b"%02d" % index
        index += 1
        if b"strh" not in parts or parts[b"strh"][1] < _STRH.size:
            raise _refuse(path, f"has a stream {tag.decode()} with no stream header")
        strh = _STRH.unpack(_read_exact(fh, parts[b"strh"][0], _STRH.size))
        if strh[0] != b"vids":
            continue
        if b"strf" not in parts:
            raise _refuse(path, f"has a video stream {tag.decode()} with no format header")
        o, s = parts[b"strf"]
        width, height, stride, bottom_up = _check_format(path, _read_exact(fh, o, s))
        video.append(_Stream(tag, strh[-1], width, height, stride, bottom_up))
    if not video:
        raise _refuse(path, "has no video stream")
    if len(video) > 1:
        raise _refuse(path, f"has {len(video)} video streams")
    return video[0]


def _collect_frames(
    path: Path,
    fh: BinaryIO,
    stream: _Stream,
    start: int,
    end: int,
    offsets: list[int],
    *,
    in_rec: bool = False,
) -> None:
    """Append the data offset of each frame chunk from ``start`` to ``end``, in file order.

    A ``rec`` list groups chunks directly under ``movi``; one inside another is not AVI and
    is refused, so a crafted file cannot nest them without bound.
    """
    frame_bytes = stream.stride * stream.height
    for cid, o, s in _children(path, fh, start, end):
        if cid == b"LIST":
            if _list_type(fh, o, s) == b"rec ":
                if in_rec:
                    raise _refuse(path, "has a rec list inside another rec list")
                _collect_frames(path, fh, stream, o + 4, o + s, offsets, in_rec=True)
            continue
        if cid[:2] != stream.tag:
            continue  # another stream, an OpenDML index (ix##) or JUNK
        kind = cid[2:]
        if kind == b"pc":
            raise _refuse(path, "changes its color table inside the movie")
        if kind not in (b"db", b"dc"):
            continue
        if s == 0:
            raise _refuse(
                path,
                f"has no bytes for frame {len(offsets)} (a dropped frame, which a player "
                "shows as a repeat of the one before)",
            )
        if s != frame_bytes:
            raise _refuse(
                path,
                f"stores frame {len(offsets)} in {s} bytes, not the {frame_bytes} of a "
                f"{stream.width} x {stream.height} frame",
            )
        offsets.append(o)


def _find_layout(path: Path, fh: BinaryIO, size: int) -> _Layout:
    """Walk the RIFF chunks, check the stream and every frame chunk, or refuse the file."""
    riffs = _children(path, fh, 0, size)
    if not riffs:
        raise _refuse(path, "is too short to hold an AVI header")
    movis: list[tuple[int, int]] = []  # (offset, size) of each movi list
    hdrl = None
    for k, (cid, offset, length) in enumerate(riffs):
        form = _list_type(fh, offset, length)
        if cid != b"RIFF" or form != (b"AVI " if k == 0 else b"AVIX"):
            raise _refuse(path, f"has the chunk {_fourcc(cid)} {_fourcc(form)} at the top level")
        for sub, o, s in _children(path, fh, offset + 4, offset + length):
            if sub != b"LIST":
                continue
            kind = _list_type(fh, o, s)
            if kind == b"hdrl" and k == 0 and hdrl is None:
                hdrl = (o, s)
            elif kind == b"movi":
                movis.append((o, s))
    if hdrl is None:
        raise _refuse(path, "has no header list (hdrl)")
    stream = _find_stream(path, fh, hdrl)
    if not movis:
        raise _refuse(path, "has no frame list (movi)")
    offsets: list[int] = []
    for offset, length in movis:
        _collect_frames(path, fh, stream, offset + 4, offset + length, offsets)
    if len(offsets) != stream.length:
        raise _refuse(
            path,
            f"holds {len(offsets)} frames and its stream header declares {stream.length}",
        )
    if not offsets:
        raise _refuse(path, "holds no frame")
    return _Layout(
        np.asarray(offsets, dtype=np.int64),
        stream.height,
        stream.width,
        stream.stride,
        stream.bottom_up,
    )


class AviMovie:
    """An open uncompressed 8-bit gray AVI movie, row 0 at the top of the picture.

    Open with [`tether.io.adapters.open_movie_source`][] or directly. The constructor
    reads the headers and the chunk headers and refuses a file that it cannot read fully
    (see the module docstring); [`read_frames`][] then reads only the frames it returns.
    """

    def __init__(self, path: str | PathLike[str]) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._closed = False
        # Unbuffered: a buffer filled by an earlier read could otherwise give back bytes
        # that a file cut short after the open no longer holds.
        self._fh = self._path.open("rb", buffering=0)
        try:
            size = self._fh.seek(0, 2)
            self._layout = _find_layout(self._path, self._fh, size)
        except BaseException:
            self._fh.close()
            raise
        self._metadata = MovieMetadata()

    @property
    def path(self) -> Path:
        """The movie's filesystem path."""
        return self._path

    @property
    def shape(self) -> tuple[int, int, int]:
        """``(n_frames, height, width)``."""
        return (len(self._layout.offsets), self._layout.height, self._layout.width)

    @property
    def n_frames(self) -> int:
        """The number of frames."""
        return len(self._layout.offsets)

    @property
    def pixel_type(self) -> str:
        """``'|u1'``: one 8-bit gray level a pixel."""
        return _DTYPE.str

    @property
    def metadata(self) -> MovieMetadata:
        """Frame time, pixel size and channels, all unknown (ADR-0154)."""
        return self._metadata

    def read_frames(self, start: int, stop: int) -> np.ndarray:
        """Return frames ``start`` to ``stop - 1``; ``0 <= start <= stop <= n_frames``.

        Only these frames are read. Raise `IndexError` when the range is outside that
        bound, and `UnsupportedContainerError` when the file has been cut short since it
        was opened.
        """
        if self._closed:
            raise ValueError(f"AviMovie for {self._path} is closed")
        n, h, w = self.shape
        if not 0 <= start <= stop <= n:
            raise IndexError(
                f"frame range [{start}, {stop}) is not inside a {n}-frame movie "
                "(need 0 <= start <= stop <= n_frames)"
            )
        layout = self._layout
        out = np.empty((stop - start, h, w), dtype=_DTYPE)
        row = np.empty((h, layout.stride), dtype=_DTYPE)
        with self._lock:
            for i, offset in enumerate(layout.offsets[start:stop]):
                self._fh.seek(int(offset))
                if _read_into(self._fh, memoryview(row).cast("B")) != row.nbytes:
                    raise _refuse(self._path, f"ended inside frame {start + i}")
                out[i] = row[::-1, :w] if layout.bottom_up else row[:, :w]
        return out

    def close(self) -> None:
        """Close the file (idempotent)."""
        if not self._closed:
            self._closed = True
            self._fh.close()

    def __enter__(self) -> AviMovie:
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
        """``AviMovie('movie.avi', frames=…, height=…, width=…, dtype='|u1')``."""
        n, h, w = self.shape
        return (
            f"{type(self).__name__}({self._path.name!r}, frames={n}, "
            f"height={h}, width={w}, dtype={_DTYPE.str!r})"
        )


def _read_into(fh: io.RawIOBase, view: memoryview) -> int:
    """Fill ``view`` from ``fh``; return the bytes read, fewer only at the end of the file."""
    got = 0
    while got < len(view):
        n = fh.readinto(view[got:])
        if not n:
            break
        got += n
    return got


def _is_avi(head: bytes) -> bool:
    """Answer whether ``head`` starts with a RIFF header of the form ``AVI``."""
    return head[:4] == b"RIFF" and head[8:12] == b"AVI "


AVI_ADAPTER = MovieAdapter(
    name="AVI",
    extensions=(".avi",),
    sniff=_is_avi,
    open=AviMovie,
)
"""The built-in adapter for uncompressed 8-bit gray ``.avi`` movies (ADR-0154)."""
