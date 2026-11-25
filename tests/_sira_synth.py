# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""A writer of synthetic MASH-FRET ``.sira`` movies for the SIRA adapter's tests (#905).

It writes the layout of MASH-FRET's ``writeSiraFile.m``: the header line ending with a
carriage return, the frame time as ``<f8``, the width, height and frame count as ``<f4``,
then each frame column by column as ``<f4`` with its offset after it. As MASH-FRET does,
a frame with negative values is stored plus the offset ``-min``, so that no stored value
is below zero. ``tests/fixtures/sira/mash_offsets.sira`` is the same layout as MASH-FRET's
own writer wrote it.
"""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from pathlib import Path

HEADER = b"MASH-FRET exported binary graphic Version: 1.3.6"


def sira_bytes(
    frames: np.ndarray,
    *,
    frame_time: float = 0.1,
    line: bytes = HEADER,
    end: bytes = b"\r",
    dims: tuple[float, float, float] | None = None,
) -> bytes:
    """Return a SIRA file of ``frames`` (``(n, height, width)``) as bytes.

    ``dims`` replaces the header's ``(width, height, frames)``, to write a damaged header.
    """
    n, h, w = frames.shape
    width, height, count = dims if dims is not None else (w, h, n)
    out = [line, end, struct.pack("<dfff", frame_time, width, height, count)]
    for frame in frames.astype("<f4"):
        offset = np.float32(max(0.0, -float(frame.min())))
        out.append((frame + offset).T.astype("<f4").tobytes())
        out.append(struct.pack("<f", offset))
    return b"".join(out)


def write_sira(path: Path, frames: np.ndarray, **kwargs: object) -> Path:
    """Write ``frames`` to ``path`` as a SIRA file; return ``path``."""
    path.write_bytes(sira_bytes(frames, **kwargs))  # type: ignore[arg-type]
    return path
