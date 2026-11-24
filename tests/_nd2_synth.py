# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Write small ND2 (format version 3) files for the ND2 reader tests (#902, ADR-0144).

The public ND2 files of the fixture record are one frame and one Z/C stack, so a
time series, a frame time, and each variant that the reader refuses come from this
writer. It writes the layout that ``tether.io.nd2`` documents: a start chunk, the
metadata chunks as a binary tree, one ``ImageDataSeq|i!`` chunk per frame (the
acquisition time in ms, then the rows), and the chunk map at the end. Two other
readers, the ``nd2`` package 0.12.0 and Bio-Formats 7.3.1, read a file from
``write_nd2`` to the same frames, frame times and pixel size
(``tests/fixtures/PROVENANCE.md``), so the tests do not check the reader only
against its own idea of the format.
"""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from pathlib import Path

_MAGIC = 0x0ABECEDA
_FILE_SIGNATURE = b"ND2 FILE SIGNATURE CHUNK NAME01!"
_CHUNKMAP_SIGNATURE = b"ND2 CHUNK MAP SIGNATURE 0000001!"
_FILEMAP_SIGNATURE = b"ND2 FILEMAP SIGNATURE NAME 0001!"


def _name(name: str) -> bytes:
    return (name + "\x00").encode("utf-16-le")


def encode(name: str, value: Any) -> bytes:
    """Encode one named value as a metadata record (see ``tether.io.nd2.decode_metadata``)."""
    n = len(name) + 1
    if isinstance(value, bool):
        return bytes([1, n]) + _name(name) + struct.pack("<B", value)
    if isinstance(value, int):
        kind, fmt = (3, "<I") if 0 <= value < 2**32 else (4, "<q")
        return bytes([kind, n]) + _name(name) + struct.pack(fmt, value)
    if isinstance(value, float):
        return bytes([6, n]) + _name(name) + struct.pack("<d", value)
    if isinstance(value, str):
        return bytes([8, n]) + _name(name) + (value + "\x00").encode("utf-16-le")
    if isinstance(value, bytes):
        return bytes([9, n]) + _name(name) + struct.pack("<Q", len(value)) + value
    items = [("", v) for v in value] if isinstance(value, list) else list(value.items())
    head = bytes([11, n]) + _name(name)
    body = b""
    offsets = []
    for key, item in items:
        offsets.append(len(head) + 12 + len(body))
        body += encode(key, item)
    length = len(head) + 12 + len(body)
    return (
        head
        + struct.pack("<IQ", len(items), length)
        + body
        + struct.pack(f"<{len(items)}Q", *offsets)
    )


def _chunk(name: bytes, data: bytes, *, name_len: int | None = None) -> bytes:
    name_len = max(len(name), 20) if name_len is None else name_len
    return struct.pack("<IIQ", _MAGIC, name_len, len(data)) + name.ljust(name_len, b"\x00") + data


def _experiment(loops: list[tuple[int, int]], period_ms: float) -> dict[str, Any] | None:
    node: dict[str, Any] | None = None
    for kind, count in reversed(loops):
        here: dict[str, Any] = {
            "eType": kind,
            "uLoopPars": {
                "uiCount": count,
                "dStart": 0.0,
                "dPeriod": period_ms,
                "dDuration": 0.0,
            },
        }
        if node is not None:
            here["ppNextLevelEx"] = [node]
        node = here
    return node


def write_nd2(
    path: Path,
    frames: np.ndarray,
    *,
    frame_ms: float = 100.0,
    pixel_um: float | None = 0.16,
    channels: tuple[str, ...] = ("Cy3",),
    components: int = 1,
    compression: int = 2,
    loops: list[tuple[int, int]] | None = None,
    row_padding: int = 0,
    version: bytes = b"Ver3.0",
    drop_frame: int | None = None,
    bits: int | None = None,
) -> Path:
    """Write ``frames`` (``(n, h, w)``, one sample per pixel) as a version-3 ND2 file.

    The keyword arguments make the variants that the reader must refuse: more than
    one component per pixel, a compression code other than 2 (none), a loop that is
    not time, a version other than 3, or a missing frame chunk. ``row_padding``
    adds bytes at the end of each row, as a camera with an aligned stride does.
    """
    n, h, w = frames.shape
    itemsize = frames.dtype.itemsize
    bits = 8 * itemsize if bits is None else bits
    row_bytes = w * itemsize * components + row_padding
    loops = [(1, n)] if loops is None else loops
    attrs = {
        "SLxImageAttributes": {
            "uiWidth": w,
            "uiWidthBytes": row_bytes,
            "uiHeight": h,
            "uiComp": components,
            "uiBpcInMemory": bits,
            "uiBpcSignificant": bits,
            "uiSequenceCount": n,
            "uiTileWidth": w,
            "uiTileHeight": h,
            "eCompression": compression,
            "dCompressionParam": 0.0,
            "ePixelType": 1,
            "uiVirtualComponents": components,
        }
    }
    probe = {"m_ExcitationSpectrum": {"uiCount": 0}, "m_EmissionSpectrum": {"uiCount": 0}}
    planes = {
        f"a{i}": {"sDescription": c, "uiCompCount": 1, "pFluorescentProbe": probe}
        for i, c in enumerate(channels)
    }
    picture = {
        "SLxPictureMetadata": {
            "bCalibrated": pixel_um is not None,
            "dCalibration": pixel_um if pixel_um is not None else 1.0,
            "sPicturePlanes": {
                "uiCount": len(channels),
                "uiCompCount": components,
                "sPlaneNew": planes,
            },
        }
    }
    chunks: list[tuple[bytes, bytes, int | None]] = [
        (b"ImageAttributesLV!", _top(attrs), None),
        (b"ImageMetadataSeqLV|0!", _top(picture), None),
    ]
    experiment = _experiment(loops, frame_ms)
    if experiment is not None:
        chunks.append((b"ImageMetadataLV!", _top({"SLxExperiment": experiment}), None))
    le = frames.astype(frames.dtype.newbyteorder("<"), copy=False)
    for i in range(n):
        if i == drop_frame:
            continue
        rows = np.repeat(le[i], components, axis=1).reshape(h, w * components)
        raw = rows.tobytes()
        if row_padding:
            raw = b"".join(
                raw[r * w * itemsize * components : (r + 1) * w * itemsize * components]
                + b"\x00" * row_padding
                for r in range(h)
            )
        data = struct.pack("<d", 1000.0 + i * frame_ms) + raw  # the time in ms
        chunks.append((b"ImageDataSeq|%d!" % i, data, 4072))

    times = [1000.0 + i * frame_ms for i in range(n)]
    chunks.append((b"CustomData|AcqTimesCache!", struct.pack(f"<{n}d", *times), None))
    out = _chunk(_FILE_SIGNATURE, version.ljust(64, b"\x00"), name_len=32)
    places: list[tuple[bytes, int, int]] = []
    for name, data, name_len in chunks:
        out += b"\x00" * (-len(out) % 4096)
        places.append((name, len(out), len(data)))
        out += _chunk(name, data, name_len=name_len)
    out += b"\x00" * (-len(out) % 4096)
    location = len(out)
    table = b"".join(name + struct.pack("<QQ", at, size) for name, at, size in places)
    table += _CHUNKMAP_SIGNATURE + struct.pack("<Q", location)
    out += _chunk(_FILEMAP_SIGNATURE, table, name_len=32)
    path.write_bytes(out)
    return path


def _top(tree: dict[str, Any]) -> bytes:
    """Encode the single top-level record of a metadata chunk (``{name: level}``)."""
    ((name, value),) = tree.items()
    return encode(name, value)
