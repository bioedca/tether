# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reader for MASH-FRET ``.sira`` movies (PRD §7.1 FR-EXTRACT, Appendix A; ADR-0152).

[`SIRA_ADAPTER`][] opens a SIRA file as a [`tether.io.adapters.MovieSource`][]. SIRA is
the movie format of MASH-FRET: its Simulation module exports simulated movies to it, and
its Video processing module exports edited movies and average images. Tether reads the
format itself and needs no package. A frame-range read seeks to the frames it returns
and reads only those.

The layout, from MASH-FRET's description of the format and its ``writeSiraFile.m``:

* one line of text, ``MASH-FRET exported binary graphic Version: <version>``, that ends
  with a carriage return;
* the frame time as a 64-bit float, then the width, the height and the number of frames,
  each as a 32-bit float;
* each frame as its pixels, column by column, then one more value, the frame's *offset*.
  All are little-endian 32-bit floats. MASH-FRET adds the offset to each pixel of a frame
  that has negative values, so that it stores no value below zero. The pixel is the
  stored value minus the offset.

**Tether reads a SIRA file only when it can read all of it**, and refuses every other
file with [`tether.io.adapters.UnsupportedContainerError`][] and a message that names
the reason. It refuses:

* an older header line (``SIRA exported binary graphic`` or ``MASH smFRET exported
  binary graphic``), or MASH-FRET version 1.003 before 1.003.41. These files can store
  16-bit integers and no offsets, and Tether has not been checked against them;
* a header whose dimensions are not whole numbers of 1 or more;
* a file shorter or longer than its header and its frames; an incomplete copy is shorter;
* an offset that is not finite and zero or more.

**Row order.** Frame row 0 is the first row of MASH-FRET's image matrix, which MASH-FRET
shows at the top, and which its TIFF export writes first.

The metadata, each value with origin ``"file"`` or ``"unknown"``:

* frame time: the header's frame time (seconds), for a movie of two frames or more;
  ``unknown`` for one frame (an average image) or a value that is not finite and
  positive;
* pixel size: always ``unknown``; the file does not record it;
* channels: always ``unknown``. A movie of two or more channels has them side by side
  in each frame, and the file names none of them.
"""

from __future__ import annotations

import math
import re
import struct
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO

import numpy as np

from tether.io.adapters import (
    MetadataValue,
    MovieAdapter,
    MovieMetadata,
    UnsupportedContainerError,
)

if TYPE_CHECKING:
    from os import PathLike
    from types import TracebackType

__all__ = ["SIRA_ADAPTER", "SiraMovie"]

_PREFIX = b"MASH-FRET exported binary graphic Version: "
# The header lines of the older layouts, which MASH-FRET still reads (getSiraDat.m).
_OLDER = (b"SIRA exported binary graphic", b"MASH smFRET exported binary graphic")
# MASH-FRET 1.003 before 1.003.41 wrote 16-bit pixels, and before 1.003.39 no offsets.
_OLD_VERSION = re.compile(r"1\.003(?:\.(\d+))?(?!\d)")
_FIRST_SINGLE_LAYOUT = 41
_DTYPE = np.dtype("<f4")
_HEADER = struct.Struct("<dfff")
# The version text is short; a header line longer than this is no SIRA header.
_LINE_LIMIT = 4096
# A float32 holds every whole number up to 2**24 exactly.
_DIMENSION_LIMIT = 1 << 24


def _refuse(path: Path, why: str) -> UnsupportedContainerError:
    return UnsupportedContainerError(
        f"{path}: Tether reads a SIRA file only when it can read all of it (a header of "
        f"MASH-FRET 1.003.41 or later, and every frame present), and this file {why}. "
        "Copy the file again if the copy is incomplete, or open the movie in a current "
        "MASH-FRET and export it to .sira again."
    )


@dataclass(frozen=True)
class _Layout:
    """Where the frames are and what the header says about them."""

    n_frames: int
    height: int
    width: int
    offset: int
    frame_time: float | None
    version: str


def _dimension(value: float) -> int | None:
    """Return ``value`` as a whole number from 1 to 2**24, or None."""
    if not math.isfinite(value) or value != int(value) or not 1 <= value <= _DIMENSION_LIMIT:
        return None
    return int(value)


def _find_layout(path: Path, fh: BinaryIO, size: int) -> _Layout:
    """Parse the header, check every frame's place and offset, or refuse the file."""
    head = fh.read(min(size, _LINE_LIMIT))
    # MASH-FRET ends the line at the first carriage return or line feed (fgetl_MASH.m).
    ends = [i for i in (head.find(b"\r"), head.find(b"\n")) if i >= 0]
    line = head[: min(ends)] if ends else head
    if line.startswith(_OLDER):
        raise _refuse(
            path,
            f"has the older header '{line.decode('latin-1')}', whose layout can hold 16-bit "
            "pixels and no offsets",
        )
    if not line.startswith(_PREFIX):
        raise _refuse(path, "does not start with the MASH-FRET header line")
    if not ends:
        raise _refuse(path, "has a header line with no end")
    version = line[len(_PREFIX) :].decode("latin-1").strip()
    old = _OLD_VERSION.match(version)
    if old is not None and (old.group(1) is None or int(old.group(1)) < _FIRST_SINGLE_LAYOUT):
        raise _refuse(
            path,
            f"has the MASH-FRET version {version}, whose layout can hold 16-bit pixels and "
            "no offsets",
        )
    start = len(line) + 1 + _HEADER.size
    if size < start:
        raise _refuse(path, "has a header cut short")
    fh.seek(len(line) + 1)
    frame_time, *dims = _HEADER.unpack(fh.read(_HEADER.size))
    width, height, n_frames = (_dimension(v) for v in dims)
    if width is None or height is None or n_frames is None:
        shown = ", ".join(f"{v:g}" for v in dims)
        raise _refuse(
            path,
            f"has a damaged header (width, height and frames {shown}, not whole numbers of 1 "
            "or more)",
        )
    frame_bytes = (width * height + 1) * _DTYPE.itemsize
    end = start + n_frames * frame_bytes
    if size < end:
        raise _refuse(
            path,
            f"is shorter than the {n_frames} frames that its header declares (an incomplete copy)",
        )
    if size > end:
        raise _refuse(path, f"has {size - end} bytes after its last frame")
    for k in range(n_frames):
        fh.seek(start + k * frame_bytes + frame_bytes - _DTYPE.itemsize)
        (offset,) = struct.unpack("<f", fh.read(_DTYPE.itemsize))
        if not (math.isfinite(offset) and offset >= 0):
            raise _refuse(path, f"has the offset {offset} in frame {k}, not finite and 0 or more")
    usable = math.isfinite(frame_time) and frame_time > 0
    return _Layout(n_frames, height, width, start, frame_time if usable else None, version)


class SiraMovie:
    """An open SIRA movie, its frames as MASH-FRET shows them.

    Open with [`tether.io.adapters.open_movie_source`][] or directly. The constructor
    reads the header, checks the frames' places and offsets, and refuses a file that it
    cannot read fully (see the module docstring); [`read_frames`][] then reads only the
    frames it returns.
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
            self._fh.seek(0)
            self._layout = _find_layout(self._path, self._fh, size)
        except BaseException:
            self._fh.close()
            raise
        layout = self._layout
        frame_time: MetadataValue[float] = MetadataValue.unknown()
        if layout.n_frames > 1 and layout.frame_time is not None:
            frame_time = MetadataValue.from_file(layout.frame_time)
        self._metadata = MovieMetadata(frame_time=frame_time)

    @property
    def path(self) -> Path:
        """The movie's filesystem path."""
        return self._path

    @property
    def shape(self) -> tuple[int, int, int]:
        """``(n_frames, height, width)``."""
        return (self._layout.n_frames, self._layout.height, self._layout.width)

    @property
    def n_frames(self) -> int:
        """The number of frames."""
        return self._layout.n_frames

    @property
    def pixel_type(self) -> str:
        """``'<f4'``: MASH-FRET stores each pixel as a little-endian 32-bit float."""
        return _DTYPE.str

    @property
    def version(self) -> str:
        """The MASH-FRET version that the header line names."""
        return self._layout.version

    @property
    def metadata(self) -> MovieMetadata:
        """The frame time from the header; pixel size and channels are unknown."""
        return self._metadata

    def read_frames(self, start: int, stop: int) -> np.ndarray:
        """Return frames ``start`` to ``stop - 1``; ``0 <= start <= stop <= n_frames``.

        Each pixel is its stored value minus its frame's offset. Only these frames are
        read. Raise `IndexError` when the range is outside that bound.
        """
        if self._closed:
            raise ValueError(f"SiraMovie for {self._path} is closed")
        n, h, w = self.shape
        if not 0 <= start <= stop <= n:
            raise IndexError(
                f"frame range [{start}, {stop}) is not inside a {n}-frame movie "
                "(need 0 <= start <= stop <= n_frames)"
            )
        stored = np.empty((stop - start, h * w + 1), dtype=_DTYPE)
        want = stored.nbytes
        with self._lock:
            self._fh.seek(self._layout.offset + start * stored.shape[1] * _DTYPE.itemsize)
            got = _read_into(self._fh, memoryview(stored).cast("B")) if want else 0
        if got != want:
            raise _refuse(self._path, f"ended inside frame {start + got // stored[0].nbytes}")
        # Each frame is stored column by column (MATLAB order), so a (width, height) array
        # transposed is the frame, its first row the top row that MASH-FRET shows.
        pixels = stored[:, :-1].reshape(-1, w, h).transpose(0, 2, 1)
        return np.ascontiguousarray(pixels - stored[:, -1, None, None])

    def close(self) -> None:
        """Close the file (idempotent)."""
        if not self._closed:
            self._closed = True
            self._fh.close()

    def __enter__(self) -> SiraMovie:
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
        """``SiraMovie('movie.sira', frames=…, height=…, width=…, dtype='<f4')``."""
        n, h, w = self.shape
        return (
            f"{type(self).__name__}({self._path.name!r}, frames={n}, "
            f"height={h}, width={w}, dtype={_DTYPE.str!r})"
        )


def _read_into(fh: BinaryIO, view: memoryview) -> int:
    """Fill ``view`` from ``fh``; return the bytes read, fewer only at the end of the file."""
    got = 0
    while got < len(view):
        n = fh.readinto(view[got:])
        if not n:
            break
        got += n
    return got


def _is_sira(head: bytes) -> bool:
    """Answer whether ``head`` starts like a SIRA header line, of any layout."""
    return any(head[:16] == prefix[:16] for prefix in (_PREFIX, *_OLDER))


SIRA_ADAPTER = MovieAdapter(
    name="SIRA",
    extensions=(".sira",),
    sniff=_is_sira,
    open=SiraMovie,
)
"""The built-in adapter for MASH-FRET ``.sira`` movies (ADR-0152)."""
