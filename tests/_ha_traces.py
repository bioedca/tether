# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Readers for laboratory ``.traces`` and output files, for the tests only (#796, #798, PRD N4).

Two variants of the binary ``.traces`` format are read: the Ha-laboratory one with no time row
(#796) and the Weninger-laboratory one whose every frame starts with a frame index that the
header does not count (#798).

The product has no importer for the ``.traces`` format and gets none: PRD N4 keeps old laboratory
formats out of Tether, and the "What the tests can use" rules (#705) let a test read one with a
small reader under ``tests/``. Each reader refuses a file whose shape is not the shape it expects,
so a wrong file fails here and not as a numeric difference later.

It is a plain helper module (not a ``test_*`` file, so pytest does not collect it), importable as
``from _ha_traces import ...`` because ``tests/`` is on ``sys.path`` under pytest's default
``prepend`` import mode.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np

__all__ = [
    "ChannelOrder",
    "CountsRecord",
    "Traces",
    "read_column_file",
    "read_counts",
    "read_traces",
]

#: Bytes before the intensities: an int32 frame count and an int16 channel count.
HEADER_BYTES = 6


#: Which channel of each molecule's pair comes first in the file.
ChannelOrder = Literal["donor-first", "acceptor-first"]
_ORDERS = ("donor-first", "acceptor-first")


@dataclass(frozen=True)
class Traces:
    """One ``.traces`` file: ``donor`` and ``acceptor`` are ``(molecules, frames)`` float64.

    ``frame_index`` is the frame-index value that starts each frame in the variant that has one
    (int64, one per frame), and ``None`` in the variant that has none.
    """

    frames: int
    donor: np.ndarray
    acceptor: np.ndarray
    frame_index: np.ndarray | None = None

    @property
    def molecules(self) -> int:
        return int(self.donor.shape[0])


def read_traces(path: Path, *, order: ChannelOrder) -> Traces:
    """Read a binary ``.traces`` file of either variant, with the channel order stated.

    Both variants start with a little-endian int32 number of frames and an int16 number of
    channels, then hold int16 values frame by frame, every value of one frame before the next
    frame. The channels come in pairs, one pair per molecule.

    * **No frame index** (Ha laboratory, #796): each frame holds the channels only, as
      ``p_nxgn1_ap.pro`` writes them (Zenodo 10.5281/zenodo.4925617), donor first.
    * **Frame index** (Weninger laboratory, #798): each frame starts with one more value, the
      frame index, that the header does not count; the laboratory's MATLAB reader (Zenodo
      10.5281/zenodo.7853531) takes it as row 1 and then the pairs, acceptor first.

    The variant is found from the file size: ``6 + 2 * frames * channels`` bytes has no frame
    index and ``6 + 2 * frames * (channels + 1)`` has one; any other size is refused, so a
    truncated file fails here. The channel order cannot be found from the file and is therefore
    a required parameter: ``order="donor-first"`` or ``order="acceptor-first"``.
    """
    if order not in _ORDERS:
        raise ValueError(f"order must be one of {_ORDERS}, got {order!r}")
    raw = path.read_bytes()
    if len(raw) < HEADER_BYTES:
        raise ValueError(f"{path.name}: {len(raw)} B is shorter than the {HEADER_BYTES} B header")
    frames = int(np.frombuffer(raw, dtype="<i4", count=1, offset=0)[0])
    channels = int(np.frombuffer(raw, dtype="<i2", count=1, offset=4)[0])
    if frames < 1 or channels < 2 or channels % 2:
        raise ValueError(
            f"{path.name}: header says {frames} frames and {channels} channels; expected at "
            "least one frame and a positive even number of channels"
        )
    plain = HEADER_BYTES + 2 * frames * channels
    indexed = HEADER_BYTES + 2 * frames * (channels + 1)
    if len(raw) == plain:
        width = channels
    elif len(raw) == indexed:
        width = channels + 1
    else:
        raise ValueError(
            f"{path.name}: {len(raw)} B, but {frames} frames x {channels} channels of int16 "
            f"need {plain} B, or {indexed} B with a frame index in each frame"
        )
    values = np.frombuffer(raw, dtype="<i2", offset=HEADER_BYTES).reshape(frames, width)
    frame_index = values[:, 0].astype(np.int64) if width > channels else None
    per_channel = values[:, width - channels :].T.astype(np.float64)
    first, second = per_channel[0::2].copy(), per_channel[1::2].copy()
    donor, acceptor = (first, second) if order == "donor-first" else (second, first)
    return Traces(frames=frames, donor=donor, acceptor=acceptor, frame_index=frame_index)


def read_column_file(path: Path, columns: int) -> np.ndarray:
    """Read ``frets.dat`` (1 column) or ``test3.dat`` (2 columns), as MATLAB's ``save -ascii``
    writes them, and refuse a file with another number of columns."""
    data = np.loadtxt(path, dtype=np.float64, ndmin=2)
    if data.shape[1] != columns:
        raise ValueError(f"{path.name}: {data.shape[1]} columns, expected {columns}")
    return data[:, 0] if columns == 1 else data


@dataclass(frozen=True)
class CountsRecord:
    """The parameters and the per-file counts that the laboratory's script wrote to
    ``counts2.txt`` when it made ``frets.dat`` and ``test3.dat``."""

    parameters: dict[str, float]
    green_frames: tuple[int, int]
    red_frames: tuple[int, int]
    #: For each ``helN.traces``: (good spots, molecules, high FRET, low FRET, both dyes).
    files: dict[str, tuple[int, int, int, int, int]]


_PARAMETER = re.compile(r"^(\w+) =\n\s*\n\s*([-+0-9.eE]+)\s*$", re.MULTILINE)
_FRAMES = re.compile(r"^(Red|Green) ex\. frames: (\d+) to (\d+)$", re.MULTILINE)
_FILE = re.compile(
    r"^(hel\d+\.traces)\nTotal good spots: (\d+) / (\d+)\nHigh fret: (\d+), low fret: (\d+)\n"
    r"red only: \d+, green only: \d+, both: (\d+)$",
    re.MULTILINE,
)


def read_counts(path: Path) -> CountsRecord:
    """Read ``counts2.txt``: the parameters, the excitation frames and the counts of each file."""
    text = path.read_text(encoding="ascii").replace("\r\n", "\n")
    parameters = {name: float(value) for name, value in _PARAMETER.findall(text)}
    frames = {colour: (int(first), int(last)) for colour, first, last in _FRAMES.findall(text)}
    files = {
        name: (int(spots), int(molecules), int(high), int(low), int(both))
        for name, spots, molecules, high, low, both in _FILE.findall(text)
    }
    if set(frames) != {"Red", "Green"} or not files or "leakage" not in parameters:
        raise ValueError(f"{path.name}: not the counts file of the snapshot histogram script")
    return CountsRecord(parameters, frames["Green"], frames["Red"], files)
