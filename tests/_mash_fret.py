# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Readers for the frozen MASH-FRET simulation outputs of #714 (ADR-0088).

MASH-FRET v1.3.6 writes, for one simulation called ``<name>``:

* ``<name>.sira`` — its own video format (``writeSiraFile.m``): a text line ending in ``\\r``,
  then the exposure time (float64) and the width, height and frame count (float32), then for
  each frame the pixels in column-major order as float32, followed by one float32 that is the
  offset the writer added to make the frame non-negative. The run compresses it to
  ``<name>.sira.gz`` (gzip, lossless) so that it fits the git tier.
* ``coordinates/<name>.coord`` — one row of ``x_donor y_donor x_acceptor y_acceptor`` for each
  molecule, in MASH-FRET's full-frame coordinates, where pixel ``i`` (1-based) spans
  ``(i - 1, i]``.
* ``traces_ASCII/<name>_mol<n>of<N>.txt`` — the traces of molecule ``n``, with the ideal
  (noise-free) donor and acceptor intensities in photon counts.

Tether's coordinates are 0-based with a pixel's centre on the integer, so a MASH-FRET coordinate
``c`` is ``c - 0.5`` in Tether, and an acceptor ``x`` is also reduced by the channel width.
"""

from __future__ import annotations

import gzip
import re
import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np

_HEADER_END = b"\r"
_TRACE_NAME = re.compile(r"_mol(\d+)of(\d+)\.txt$")


@dataclass(frozen=True)
class SiraVideo:
    """A decoded ``.sira`` video: ``frames`` is ``(T, H, W)`` float32."""

    version: str
    exposure_s: float
    frames: np.ndarray


def read_sira(path: Path) -> SiraVideo:
    """Read a ``.sira`` or ``.sira.gz`` file as MASH-FRET's ``readSira.m`` reads it."""
    raw = path.read_bytes()
    if path.suffix == ".gz":
        raw = gzip.decompress(raw)
    end = raw.index(_HEADER_END)
    version = raw[:end].decode("ascii").split("Version:", 1)[-1].strip()
    pos = end + 1
    exposure, width, height, length = struct.unpack_from("<dfff", raw, pos)
    pos += struct.calcsize("<dfff")
    width, height, length = int(width), int(height), int(length)
    n_pix = width * height
    body = np.frombuffer(raw, dtype="<f4", offset=pos)
    if body.size != length * (n_pix + 1):
        raise ValueError(f"{path.name}: {body.size} values, expected {length * (n_pix + 1)}")
    body = body.reshape(length, n_pix + 1)
    # The trailing value of each frame is the offset writeSiraFile added; take it off again.
    pixels = body[:, :n_pix] - body[:, n_pix:]
    # Column-major (MATLAB) order: each frame is width columns of height pixels.
    frames = pixels.reshape(length, width, height).transpose(0, 2, 1)
    return SiraVideo(version, float(exposure), np.ascontiguousarray(frames))


def write_movie_tiff(video: SiraVideo, path: Path) -> Path:
    """Write the video as the uncompressed big-endian uint16 TIFF that Tether reads.

    The conversion must be exact: every pixel of the simulation is an integer image count
    (MASH-FRET's ``P`` camera model with ``K = 1``), so a value that is not an integer in
    ``[0, 65535]`` is an error, never a rounding.
    """
    import tifffile

    frames = video.frames.astype(np.float64)
    as_int = np.rint(frames)
    if not np.array_equal(as_int, frames) or frames.min() < 0 or frames.max() > 65535:
        raise ValueError(f"{path.name}: the video is not integer image counts in uint16 range")
    tifffile.imwrite(path, as_int.astype(">u2"), byteorder=">", photometric="minisblack")
    return path


@dataclass(frozen=True)
class Truth:
    """The simulated truth, in Tether's conventions.

    ``donor_xy`` and ``acceptor_xy`` are ``(N, 2)`` ``[x, y]`` in each channel's own 0-based
    pixels; ``donor_ideal`` and ``acceptor_ideal`` are ``(N, T)`` noise-free intensities in
    photon counts (frames that MASH-FRET did not export, if any, are NaN).
    """

    donor_xy: np.ndarray
    acceptor_xy: np.ndarray
    donor_ideal: np.ndarray
    acceptor_ideal: np.ndarray


def read_truth(run: Path, name: str, *, channel_width: int, n_frames: int) -> Truth:
    """Read the coordinates and the ideal traces of the simulation ``name`` in ``run``."""
    coord = np.loadtxt(run / "coordinates" / f"{name}.coord", ndmin=2)
    n_mol = coord.shape[0]
    donor_ideal = np.full((n_mol, n_frames), np.nan)
    acceptor_ideal = np.full((n_mol, n_frames), np.nan)
    files = sorted((run / "traces_ASCII").glob(f"{name}_mol*of*.txt"))
    if len(files) != n_mol:
        raise ValueError(f"{name}: {len(files)} trace files for {n_mol} coordinates")
    for path in files:
        index, total = (int(g) for g in _TRACE_NAME.search(path.name).groups())
        if total != n_mol:
            raise ValueError(f"{path.name}: names {total} molecules, the coordinates {n_mol}")
        lines = path.read_text(encoding="ascii").splitlines()
        given = np.array(lines[0].split()[1:], dtype=float)
        if not np.allclose(given, coord[index - 1], atol=0.005):
            raise ValueError(f"{path.name}: its coordinates differ from the .coord file")
        rows = np.atleast_2d(np.loadtxt(lines[2:]))
        frame = rows[:, 1].astype(int) - 1
        donor_ideal[index - 1, frame] = rows[:, 4]
        acceptor_ideal[index - 1, frame] = rows[:, 5]
    donor_xy = coord[:, 0:2] - 0.5
    acceptor_xy = coord[:, 2:4] - 0.5
    acceptor_xy[:, 0] -= channel_width
    return Truth(donor_xy, acceptor_xy, donor_ideal, acceptor_ideal)
