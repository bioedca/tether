# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Readers for the smfret-analysis2 test data, for the tests only (#709, PRD N4).

The product has no importer for these files and gets none: PRD N4 keeps old laboratory formats
out of Tether, and the maintainer chose readers that live only in the tests (#709, Q18 A). Each
reader refuses a file whose shape is not the shape that ``create_histogram.m`` reads or writes, so
a wrong file fails here and not as a numeric difference later.

It is a plain helper module (not a ``test_*`` file, so pytest does not collect it), importable as
``from _sa2_traces import ...`` because ``tests/`` is on ``sys.path`` under pytest's default
``prepend`` import mode.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

__all__ = ["OUTPUT_HEADERS", "read_output", "read_trace"]

#: The header line of each output, as ``create_histogram.m`` writes it with ``writetable``.
OUTPUT_HEADERS = {
    "total_FRET.csv": ("fret", "raw", "smoothed", "filtered-fret", "filtered-d.a", "gamma"),
    "total_XC.csv": ("delta-time", "cross-correlation"),
}


def read_trace(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(donor, acceptor)`` of one ``*tr*.dat`` trace file.

    The file holds whitespace-separated numbers, one row for each frame. The script reads column 1
    as time, column 2 as donor and column 3 as acceptor (``create_histogram.m``,
    ``trace_data(:,2)`` and ``trace_data(:,3)``); the other columns are not used by the histogram or
    the cross-correlation.
    """
    data = np.loadtxt(path, dtype=np.float64, ndmin=2)
    if data.shape[1] < 3 or data.shape[0] < 2:
        raise ValueError(
            f"{path.name}: expected at least 2 rows of at least 3 columns, got {data.shape}"
        )
    return data[:, 1].copy(), data[:, 2].copy()


def read_output(path: Path) -> np.ndarray:
    """Return the numbers of ``total_FRET.csv`` or ``total_XC.csv`` (one row for each line).

    The first line must be the header that the script writes for that file; the numbers are
    comma-separated, as ``writetable`` writes them.
    """
    expected = OUTPUT_HEADERS.get(path.name)
    if expected is None:
        raise ValueError(f"{path.name}: not an output of create_histogram.m")
    with path.open(encoding="ascii", newline="") as handle:
        header = tuple(handle.readline().rstrip("\r\n").split(","))
    if header != expected:
        raise ValueError(f"{path.name}: header {header} is not {expected}")
    data = np.loadtxt(path, dtype=np.float64, delimiter=",", skiprows=1, ndmin=2)
    if data.shape[1] != len(expected):
        raise ValueError(f"{path.name}: {data.shape[1]} columns, the header names {len(expected)}")
    return data
