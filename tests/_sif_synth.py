# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""A writer of small synthetic Andor SIF files for the SIF adapter's tests (#904, ADR-0150).

The header follows the layout of a Solis 4 file of format version 65567, the newest that
the public test files hold: the block list, the first record (acquisition times), the
detector, the original file name, a 2048-byte user text, the records that Tether does not
read, the three axis labels, the image-structure record, one sub-image record, one time
stamp line for each frame, a flag line with one more line for each frame, and then the
frames, bottom row first, as little-endian 32-bit floats. After them come the presence
flags of the four other blocks, all ``0``.

The records that Tether does not read are copied from the layout of the CC0 file
``c739xMagFRET_1.sif`` of Zenodo 4904806 (an Andor Zyla image saved by Solis); they are
format structure, not data. On 2026-11-24 sif_parser 0.3.6 read the files of this writer
to the written frames (in stored order) and kinetic cycle time (tests/fixtures/PROVENANCE.md).

Each keyword makes one variant that the reader must refuse or read.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from pathlib import Path

MAGIC = b"Andor Technology Multi-Channel File\n"

# The records between the user text and the axis labels, as Solis 4 writes them for an
# image of a camera with no spectrograph (format version 65567); W and H are the sensor size.
_MIDDLE = (
    b"\n65538 \x01 \x02 \x03 \x00 0 0\n65540 0 0 500 0 0 1200 1200\n0 10\n"
    b"0 0 0 0 0  0 0 0 0 0 0 10\n0 SR303i\n0 10\n0 10\n0 0 \n65537 1 500 200\n"
    b"17 Spectrograph_None999 999\n\n65539 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0\n"
    b"0 0 0 0\n0 0 0 0\n0 0 0 0\n0 0 0\n\n0\n65538 0 W H W H 1 1 1 1\n"
    b"65540 \x01 \x00 \x01 \x00 \x01 \x00\n0 1 0 0\n0 1 0 0\n0 1 0 0\n422\n16\n16\n1\n"
)


def _label(text: bytes) -> bytes:
    return b"%d\n%s" % (len(text), text)


def sif_bytes(
    frames: np.ndarray,
    *,
    kinetic_cycle: float = 0.05,
    data_type: bytes = b"Counts",
    signal: int = 1,
    sub_images: int = 1,
    xbin: int = 1,
    ybin: int = 1,
    structure_version: int = 65541,
    stamp_flag: bytes = b"1",
    after: bytes = b"0\n0\n0\n0\n",
    truncate: int = 0,
    user_text: bytes = b"",
) -> bytes:
    """Return the bytes of a SIF file that holds ``frames`` (``(n, h, w)``, top row first)."""
    frames = np.asarray(frames, dtype="<f4")
    n, h, w = frames.shape
    detector = b"DU897_BV"
    name = b"C:\\data\\synthetic.sif"
    exposure = kinetic_cycle * 0.9
    line3 = (
        b"65567 0 0 1 1700000000 -70 \x00 \x00 \x00 \x05 \x00 "
        + b"0 %r %r %r 1 \x00 %r " % (exposure, kinetic_cycle, kinetic_cycle, kinetic_cycle)
        + b"1e-07 0 1 300 0 0 1e-08 0 0 \x04 \x00 1 0 4 1 1 0 0 0 0 0 0 0 1.7e-06 0 5.1 "
        + b"4077 0 1 -999 1 0 1 0 0 -1 4 21 30007 0 0 1 100 0 0 0 550 0 0 0 0 0 0 0 0 0 1 1 "
        + b"%d\n" % len(detector)
    )
    top, right = h * ybin, w * xbin
    frame_len = h * w
    header = (
        MAGIC
        + b"65538 %d\n" % signal
        + line3
        + detector
        + b" \n"
        + b" %d %d %d\n" % (right, top, len(name))
        + name
        + b" \n65538 2048\n"
        + user_text.ljust(2048, b"\x00")[:2048]
        + _MIDDLE.replace(b"W", b"%d" % right).replace(b"H", b"%d" % top)
        + _label(b"Pixel number")
        + _label(data_type)
        + _label(b"Pixel number")
        + b"%d 1 %d %d 1 %d %d %d %d\n"
        % (structure_version, top, right, n, sub_images, n * frame_len * sub_images, frame_len)
    )
    for _ in range(sub_images):
        header += b"65538 1 %d %d 1 %d %d 0\n" % (top, right, ybin, xbin)
    step = kinetic_cycle * 1e6 if math.isfinite(kinetic_cycle) else 0.0
    stamps = [round(i * step) for i in range(n)]
    header += b"".join(b"%10d\n" % s for s in stamps)
    if stamp_flag == b"1":
        header += b"1\n" + b"".join(b"%20d\n" % 0 for _ in range(n))
    elif stamp_flag:
        header += stamp_flag + b"\n"
    data = np.ascontiguousarray(frames[:, ::-1, :]).tobytes()
    if truncate:
        data = data[:-truncate]
        after = b""
    return header + data + after


def write_sif(path: Path, frames: np.ndarray, **variant: object) -> Path:
    """Write :func:`sif_bytes` to ``path`` and return it."""
    path.write_bytes(sif_bytes(frames, **variant))  # type: ignore[arg-type]
    return path
