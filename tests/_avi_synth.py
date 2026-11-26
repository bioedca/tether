# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""A writer of synthetic AVI movies for the AVI adapter's tests (#906, ADR-0154).

It writes the RIFF layout of the AVI 1.0 format: ``RIFF AVI`` with a header list
(``hdrl``: the main header ``avih`` and one ``strl`` list per stream, each with its stream
header ``strh`` and format ``strf``), then the frame list ``movi`` with one ``00db`` chunk a
frame, then the index ``idx1``. With ``per_riff`` it splits the frames over OpenDML
``RIFF AVIX`` extensions, as a writer does for a movie larger than 1 GB. The frames are
8-bit; a DIB stores them bottom-up (positive height) or top-down, each row padded to 4
bytes, and the gray FourCCs store them top-down and unpadded. The keywords write each
variant the adapter refuses.

The committed files of ``tests/fixtures/avi/`` were written by FFmpeg, not by this module
(``scripts/make_avi_fixtures.py``), so the reader is checked on another writer as well.
"""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from pathlib import Path


def _chunk(cid: bytes, data: bytes) -> bytes:
    return cid + struct.pack("<I", len(data)) + data + (b"\x00" if len(data) & 1 else b"")


def _list(kind: bytes, form: bytes, *parts: bytes) -> bytes:
    return _chunk(kind, form + b"".join(parts))


GREY_TABLE = bytes(b for i in range(256) for b in (i, i, i, 0))


def _strl(
    kind: bytes,
    handler: bytes,
    length: int,
    width: int,
    height: int,
    bits: int,
    compression: int,
    table: bytes,
) -> bytes:
    strh = struct.pack(
        "<4s4sIHHIIIIIIIIhhhh",
        kind,
        handler,
        0,
        0,
        0,
        0,
        1,
        10,
        0,
        length,
        0,
        0xFFFFFFFF,
        0,
        0,
        0,
        width,
        abs(height),
    )
    used = len(table) // 4
    bih = struct.pack("<IiiHHIIiiII", 40, width, height, 1, bits, compression, 0, 0, 0, used, 0)
    return _list(b"LIST", b"strl", _chunk(b"strh", strh), _chunk(b"strf", bih + table))


def avi_bytes(
    frames: np.ndarray,
    *,
    fourcc: bytes | None = None,
    bottom_up: bool = True,
    bits: int = 8,
    compression: int | bytes | None = None,
    table: bytes | None = None,
    length: int | None = None,
    sizes: dict[int, int] | None = None,
    per_riff: int | None = None,
    rec: bool = False,
    extra: tuple[bytes, ...] = (),
    streams: tuple[bytes, ...] = (),
    chunk_id: bytes = b"00db",
    flags: int = 0x10,
) -> bytes:
    """Return an AVI file of ``frames`` (``(n, height, width)``, ``uint8``) as bytes.

    ``fourcc`` writes a gray FourCC (top-down, unpadded) in place of an 8-bit DIB;
    ``compression``, ``bits`` and ``table`` replace the format header's fields; ``length``
    the declared frame count; ``sizes`` cuts frame *k* to that many bytes; ``streams`` adds
    a stream of each kind (``b"vids"``, ``b"auds"``) after the first; ``extra`` adds chunks
    after the frames in ``movi``; ``flags`` is the main header's ``dwFlags``; ``per_riff``
    puts that many frames in each RIFF.
    """
    n, h, w = frames.shape
    if fourcc is not None:
        stride, rows, height = w, frames, h
        comp = struct.unpack("<I", fourcc)[0]
        tab = b""
    else:
        stride = (w + 3) // 4 * 4
        rows = frames[:, ::-1] if bottom_up else frames
        height = h if bottom_up else -h
        comp = 0
        tab = GREY_TABLE
    if compression is not None:
        comp = compression if isinstance(compression, int) else struct.unpack("<I", compression)[0]
    if table is not None:
        tab = table
    padded = np.zeros((n, h, stride), dtype="u1")
    padded[:, :, :w] = rows
    data = [padded[k].tobytes() for k in range(n)]
    for k, size in (sizes or {}).items():
        data[k] = data[k][:size]
    chunks = [_chunk(chunk_id, d) for d in data]
    declared = n if length is None else length
    strls = [_strl(b"vids", fourcc or b"\x00" * 4, declared, w, height, bits, comp, tab)]
    for kind in streams:
        strls.append(_strl(kind, b"\x00" * 4, declared, w, height, 8, 0, GREY_TABLE))
    avih = struct.pack("<14I", 100000, 0, 0, flags, n, 0, 1 + len(streams), 0, w, h, 0, 0, 0, 0)
    hdrl = _list(b"LIST", b"hdrl", _chunk(b"avih", avih), *strls)
    groups = (
        [chunks]
        if per_riff is None
        else [chunks[i : i + per_riff] for i in range(0, len(chunks), per_riff)]
    )
    out = []
    for g, group in enumerate(groups):
        body = [_list(b"LIST", b"rec ", c) for c in group] if rec else list(group)
        if g == len(groups) - 1:
            body.extend(extra)
        movi = _list(b"LIST", b"movi", *body)
        if g == 0:
            entries = b"".join(struct.pack("<4sIII", chunk_id, 0x10, 0, 0) for _ in group)
            idx1 = _chunk(b"idx1", entries)
            out.append(_list(b"RIFF", b"AVI ", hdrl, movi, idx1))
        else:
            out.append(_list(b"RIFF", b"AVIX", movi))
    return b"".join(out)


def write_avi(path: Path, frames: np.ndarray, **kwargs: object) -> Path:
    """Write ``frames`` to ``path`` as an AVI file; return ``path``."""
    path.write_bytes(avi_bytes(frames, **kwargs))  # type: ignore[arg-type]
    return path
