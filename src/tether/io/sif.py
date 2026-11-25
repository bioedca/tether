# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reader for Andor Solis ``.sif`` movies (PRD §7.1 FR-EXTRACT, Appendix A; ADR-0150).

[`SIF_ADAPTER`][] opens a SIF file as a [`tether.io.adapters.MovieSource`][]. Tether
reads the format itself and needs no package: a SIF file is a text header and then
the frames, uncompressed, as little-endian 32-bit floats. A frame-range read seeks to
the frames it returns and reads only those.

**Tether reads a SIF file only when it can read all of it as one movie of counts**,
and refuses every other file with
[`tether.io.adapters.UnsupportedContainerError`][] and a message that names the
reason. A file holds up to five data blocks (signal, reference, background, live,
source); the movie is the *signal* block. Tether reads it when:

* the signal block is present and its data type is ``Counts``: a file whose stored
  frames Solis has already processed (for example ``Counts (Bg Corrected)``) is
  refused, because those are not the values that the camera recorded;
* each frame is one image area (one sub-image), whose size divides by its binning;
* the header declares the frames, and the file holds all of them, followed by the
  next block's marker or the end of the file. A file whose frames are shorter
  than the header says, or whose frames do not end where they must (a compressed or
  spooled acquisition, or a variant Tether has not met), is refused.

**Row order.** A SIF file stores the bottom row of each frame first. Solis shows,
and exports to TIFF, the top row first, and so does Tether: frame row 0 is the top
row of the image as Solis shows it. A movie opened here therefore has the
orientation of the TIFF that Solis exports from it.

The metadata, each value with origin ``"file"`` or ``"unknown"``:

* frame time: the kinetic cycle time of the header (seconds), for a movie of two
  frames or more; ``unknown`` for one frame or a value that is not finite and
  positive;
* pixel size: always ``unknown``. The header gives the camera's pixel pitch on the
  sensor (16 µm for an iXon 897), not the size at the sample, which depends on the
  magnification that the file does not record;
* channels: always ``unknown``; the file names no channel.
"""

from __future__ import annotations

import math
import re
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

__all__ = ["SIF_ADAPTER", "SifMovie"]

_MAGIC = b"Andor Technology Multi-Channel File\n"
_DTYPE = np.dtype("<f4")
# The image-structure record and everything before it lie in the first part of the file;
# the header of a real Solis file is about 3 KiB. 1 MiB bounds the search on a damaged file.
_HEADER_SEARCH = 1 << 20
# The record of the image layout: version, left, top, right, bottom, frames, sub-images,
# total length, frame length. Solis writes version 65538 (older files) or 65541.
_STRUCTURE = re.compile(
    rb"(?:65538|65541) (-?\d+) (-?\d+) (-?\d+) (-?\d+) (\d+) (\d+) (\d+) (\d+)\n"
)
# One sub-image: version 65538, left, top, right, bottom, vertical bin, horizontal bin, and a
# last field that Tether does not read.
_SUBIMAGE = re.compile(rb"65538 (-?\d+) (-?\d+) (-?\d+) (-?\d+) (\d+) (\d+) -?\d+\n")
_NUMBER_LINE = re.compile(rb" *-?\d+\n")
# After the signal frames, each of the other four blocks starts with its presence flag.
_NEXT_BLOCK = (b"0\n", b"1\n")
# The format version decides whether a flag line follows the frame time stamps: none in
# 65559 and before, one from 65566 on (the versions of the public files, ADR-0150). The
# versions between are refused, since Tether has not met them and their layout is a guess.
_NO_FLAG_LINE_UNTIL = 65559
_FLAG_LINE_FROM = 65566
_COUNTS = b"Counts"


def _refuse(path: Path, why: str) -> UnsupportedContainerError:
    return UnsupportedContainerError(
        f"{path}: Tether reads a SIF file only as one movie of counts (one image area in "
        f"each frame, every frame present), and this file {why}. Save the acquisition "
        "again from Andor Solis as a kinetic series with one image area, or export it from "
        "Solis to an unscaled 16-bit or 32-bit TIFF."
    )


@dataclass(frozen=True)
class _Layout:
    """Where the signal frames are and what the header says about them."""

    n_frames: int
    height: int
    width: int
    offset: int
    data_type: bytes
    kinetic_cycle: float | None
    version: int


class _Cursor:
    """A read position in the header bytes, for the space-separated fields of line 3."""

    def __init__(self, data: bytes, pos: int) -> None:
        self.data = data
        self.pos = pos

    def word(self) -> bytes:
        """Return the bytes up to the next space or line end, and move past that separator."""
        end = self.pos
        while end < len(self.data) and self.data[end] not in b" \n":
            end += 1
        if end >= len(self.data):
            raise ValueError("the header ends inside its first record")
        word = self.data[self.pos : end]
        self.pos = end + 1
        return word

    def skip(self, n: int) -> None:
        self.pos += n


def _float(word: bytes) -> float:
    return float(word.decode("ascii"))


def _read_line3(head: bytes, pos: int) -> tuple[int, float | None]:
    """Return the format version and the kinetic cycle time of the header's first record.

    The record is positional: version, three integers, the acquisition time and the
    detector temperature, ten bytes that are not text, then a zero, the exposure time,
    the cycle time, the accumulation cycle time, the number of accumulations, two bytes,
    and the kinetic cycle time.
    """
    c = _Cursor(head, pos)
    version = int(c.word())
    for _ in range(3):
        int(c.word())
    int(c.word())  # acquisition time (Unix seconds)
    _float(c.word())  # detector temperature
    c.skip(10)
    int(c.word())
    for _ in range(3):
        _float(c.word())  # exposure, cycle and accumulation cycle times
    int(c.word())  # accumulations
    c.skip(2)
    kinetic = _float(c.word())
    return version, kinetic if math.isfinite(kinetic) and kinetic > 0 else None


def _axis_strings(head: bytes, end: int) -> tuple[bytes, bytes, bytes] | None:
    """Return the three axis labels written just before ``end``, or None.

    Solis writes the frame axis, the data type and the image axis each as a line that holds
    its length and then the text, with no line end after the text. The labels are read
    backwards from the image-structure record; a decomposition that is not unique is no
    decomposition.
    """

    def back(stop: int) -> list[tuple[int, bytes]]:
        """Every ``(start, text)`` where ``head[start:stop]`` is a length line, then ``text``."""
        found = []
        for length in range(1, 129):
            prefix = b"%d\n" % length
            start = stop - length - len(prefix)
            if start < 0:
                break
            # The digit before a prefix would make it a longer number, so it is no prefix.
            if head[start : stop - length] == prefix and not head[start - 1 : start].isdigit():
                found.append((start, head[stop - length : stop]))
        return found

    results = []
    for s3, image_axis in back(end):
        for s2, data_type in back(s3):
            for _s1, frame_axis in back(s2):
                results.append((frame_axis, data_type, image_axis))
    return results[0] if len(results) == 1 else None


def _find_layout(path: Path, fh: BinaryIO, size: int) -> _Layout:
    """Parse the header of the signal block and locate its frames, or refuse the file."""
    head = fh.read(min(size, _HEADER_SEARCH))
    if not head.startswith(_MAGIC):
        raise _refuse(path, "does not start with the Andor SIF header")
    line2_end = head.find(b"\n", len(_MAGIC))
    fields = head[len(_MAGIC) : line2_end].split()
    if line2_end < 0 or len(fields) != 2 or fields[0] != b"65538":
        raise _refuse(path, "has a damaged header (its second line is not the block list)")
    if fields[1] != b"1":
        raise _refuse(path, "holds no signal frames")
    try:
        version, kinetic = _read_line3(head, line2_end + 1)
    except (ValueError, IndexError) as exc:
        raise _refuse(path, f"has a damaged header ({exc})") from exc

    # The signal block comes first, so its record is the first one that has the three axis
    # labels before it and a sub-image record after it. The other blocks (a background, for
    # example) carry records of their own after the signal frames.
    for m in _STRUCTURE.finditer(head):
        axes = _axis_strings(head, m.start())
        sub = _SUBIMAGE.match(head, m.end())
        if axes is not None and sub is not None:
            break
    else:
        raise _refuse(
            path,
            "has an image layout that Tether cannot find in its header (a Solis version or "
            "an acquisition variant that Tether has not been checked against)",
        )
    _frame_axis, data_type, _image_axis = axes
    n_frames, n_sub, total, frame_len = (int(v) for v in m.groups()[4:])
    if n_sub != 1:
        raise _refuse(
            path,
            f"holds {n_sub} image areas in each frame (for example a multi-track, random-track "
            "or fast-kinetics acquisition)",
        )
    if n_frames < 1:
        raise _refuse(path, "declares no frames")
    if data_type != _COUNTS:
        shown = data_type.decode("latin-1")
        raise _refuse(
            path,
            f"holds frames of the data type '{shown}', which Solis has processed, not the "
            "counts that the camera recorded",
        )
    left, top, right, bottom, ybin, xbin = (int(v) for v in sub.groups())
    span_x, span_y = right - left + 1, top - bottom + 1
    if xbin < 1 or ybin < 1 or span_x < 1 or span_y < 1 or span_x % xbin or span_y % ybin:
        raise _refuse(path, "has an image area whose size does not divide by its binning")
    width, height = span_x // xbin, span_y // ybin
    if frame_len != width * height or total != n_frames * frame_len:
        raise _refuse(path, "has frame lengths that do not match its image area")

    # The frame time stamps, one line for each frame, then (from format version 65566) a flag
    # line, which is 1 when one more line for each frame follows. The frames start after it.
    # The version alone decides whether the flag line is there, so a file cut short by the
    # length of that line is refused rather than read two bytes early.
    if _NO_FLAG_LINE_UNTIL < version < _FLAG_LINE_FROM:
        raise _refuse(
            path,
            f"has the format version {version}, whose layout Tether has not been checked against",
        )
    pos = sub.end()
    need = pos + 64 * n_frames + 4096
    if need > len(head):
        fh.seek(0)
        head = fh.read(min(size, need))
    for _ in range(n_frames):
        stamp = _NUMBER_LINE.match(head, pos)
        if stamp is None:
            raise _refuse(path, "has a damaged list of frame time stamps")
        pos = stamp.end()
    start = pos
    if version >= _FLAG_LINE_FROM:
        flag = head[pos : pos + 2]
        if flag == b"0\n":
            start = pos + 2
        elif flag == b"1\n":
            start = pos + 2
            for _ in range(n_frames):
                line = _NUMBER_LINE.match(head, start)
                if line is None:
                    raise _refuse(path, "has a damaged list of frame time stamps")
                start = line.end()
        else:
            raise _refuse(
                path,
                f"has no flag line after its frame time stamps, where its format version "
                f"({version}) puts one",
            )
    end = start + n_frames * width * height * _DTYPE.itemsize
    if end > size:
        raise _refuse(
            path,
            f"is shorter than the {n_frames} frames that its header declares (an incomplete "
            "copy, or frames that Solis spooled to other files)",
        )
    fh.seek(end)
    if end != size and fh.read(2) not in _NEXT_BLOCK:
        raise _refuse(
            path,
            "has frames that do not end where its header says (compressed frames, or a "
            "variant that Tether has not been checked against)",
        )
    return _Layout(n_frames, height, width, start, data_type, kinetic, version)


class SifMovie:
    """An open SIF movie: the frames of its signal block, top row first.

    Open with [`tether.io.adapters.open_movie_source`][] or directly. The constructor
    reads the header and refuses a file that it cannot read fully (see the module
    docstring); [`read_frames`][] then reads only the frames it returns.
    """

    def __init__(self, path: str | PathLike[str]) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._closed = False
        self._fh = self._path.open("rb")
        try:
            size = self._fh.seek(0, 2)
            self._fh.seek(0)
            self._layout = _find_layout(self._path, self._fh, size)
        except BaseException:
            self._fh.close()
            raise
        layout = self._layout
        frame_time: MetadataValue[float] = MetadataValue.unknown()
        if layout.n_frames > 1 and layout.kinetic_cycle is not None:
            frame_time = MetadataValue.from_file(layout.kinetic_cycle)
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
        """``'<f4'``: Solis stores each count as a little-endian 32-bit float."""
        return _DTYPE.str

    @property
    def metadata(self) -> MovieMetadata:
        """The frame time (from the kinetic cycle time); pixel size and channels are unknown."""
        return self._metadata

    def read_frames(self, start: int, stop: int) -> np.ndarray:
        """Return frames ``start`` to ``stop - 1``; ``0 <= start <= stop <= n_frames``.

        Each frame has its top row first. Only these frames are read. Raise `IndexError`
        when the range is outside that bound.
        """
        if self._closed:
            raise ValueError(f"SifMovie for {self._path} is closed")
        n, h, w = self.shape
        if not 0 <= start <= stop <= n:
            raise IndexError(
                f"frame range [{start}, {stop}) is not inside a {n}-frame movie "
                "(need 0 <= start <= stop <= n_frames)"
            )
        stored = np.empty((stop - start, h, w), dtype=_DTYPE)
        want = stored.nbytes
        with self._lock:
            self._fh.seek(self._layout.offset + start * h * w * _DTYPE.itemsize)
            got = self._fh.readinto(memoryview(stored).cast("B")) if want else 0
        if got != want:
            raise _refuse(self._path, f"ended inside frame {start + got // (h * w * 4)}")
        # SIF stores the bottom row first; return the top row first, as Solis shows it.
        return np.ascontiguousarray(stored[:, ::-1, :])

    def close(self) -> None:
        """Close the file (idempotent)."""
        if not self._closed:
            self._closed = True
            self._fh.close()

    def __enter__(self) -> SifMovie:
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
        """``SifMovie('movie.sif', frames=…, height=…, width=…, dtype='<f4')``."""
        n, h, w = self.shape
        return (
            f"{type(self).__name__}({self._path.name!r}, frames={n}, "
            f"height={h}, width={w}, dtype={_DTYPE.str!r})"
        )


def _is_sif(head: bytes) -> bool:
    """Answer whether ``head`` starts with the Andor SIF header line."""
    return head[:16] == _MAGIC[:16]


SIF_ADAPTER = MovieAdapter(
    name="SIF",
    extensions=(".sif",),
    sniff=_is_sif,
    open=SifMovie,
)
"""The built-in adapter for Andor Solis ``.sif`` movies (ADR-0150)."""
