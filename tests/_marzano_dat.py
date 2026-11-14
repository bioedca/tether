# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Test-only reader of the vbFRET ``.dat`` traces of #797 (PRD §2.2, "What the tests can use").

The product reads no ``.dat`` file (PRD N4); this module lives under ``tests/`` and only the
tests import it. A fixture is a ``.tar.xz`` whose members are the original ``.dat`` files,
byte for byte, of one condition folder of a Marzano record (``scripts/make_marzano_fixture.py``
makes it, ``tests/fixtures/PROVENANCE.md`` names the source). Each member is vbFRET's
"Idealized Traces" export of one molecule: whitespace-separated rows of five columns,

    frame   donor   acceptor   FRET (= A / (D + A))   idealized FRET

with frames numbered from 1. The idealized column is the Viterbi path of that trace's own
vbFRET_nov12 model, written as the state mean of each frame.

This file is a plain helper module (not a ``test_*`` file), importable as
``from _marzano_dat import ...`` under pytest's default ``prepend`` import mode. It uses only
the standard library and NumPy, so the base and the sidecar environment can both import it.
"""

from __future__ import annotations

import io
import tarfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

FIXTURES = Path(__file__).parent / "fixtures"
#: set id -> (committed fixture, reference-data manifest id, condition folder name)
SETS = {
    "ppr": (
        FIXTURES / "marzano_ppr_rnaanticognate.tar.xz",
        "marzano-ppr-rnaanticognate",
        "RNAanticognate",
    ),
    "chaperone": (
        FIXTURES / "marzano_chaperone_kjg_231027.tar.xz",
        "marzano-chaperone-kjg-231027",
        "KJG_231027",
    ),
}

#: smFRET_module's ``remove_outliers(df, "hist")`` keeps a frame only if its raw FRET lies in
#: this open interval (``remove_outliers_tdp`` keeps a transition whose levels lie in the closed
#: one).
RAW_FRET_OPEN = (-0.5, 1.5)


@dataclass(frozen=True)
class DatTrace:
    """One ``.dat`` member: its file name and its five columns."""

    name: str
    frame: np.ndarray  # int64, numbered from 1
    donor: np.ndarray
    acceptor: np.ndarray
    fret: np.ndarray
    idealized: np.ndarray

    @property
    def molecule(self) -> str:
        """smFRET_module's molecule number: the file-name prefix before the first ``_``."""
        return self.name.split("_")[0]

    @property
    def kept(self) -> np.ndarray:
        """The frames smFRET_module keeps: raw FRET strictly inside :data:`RAW_FRET_OPEN`."""
        lo, hi = RAW_FRET_OPEN
        return (self.fret > lo) & (self.fret < hi)


def parse_dat(name: str, data: bytes) -> DatTrace:
    """Parse the bytes of one ``.dat`` member. Five numeric columns, nothing else, or an error."""
    table = np.loadtxt(io.BytesIO(data), dtype=np.float64, ndmin=2)
    if table.shape[1] != 5:
        raise ValueError(f"{name}: expected 5 columns, got {table.shape[1]}")
    frame = table[:, 0]
    if not np.array_equal(frame, np.arange(1, frame.size + 1)):
        raise ValueError(f"{name}: frames are not numbered 1..{frame.size}")
    return DatTrace(
        name=name,
        frame=frame.astype(np.int64),
        donor=table[:, 1],
        acceptor=table[:, 2],
        fret=table[:, 3],
        idealized=table[:, 4],
    )


def read_set(path: Path) -> list[DatTrace]:
    """Every ``.dat`` member of a fixture archive, in file-name order."""
    traces = []
    with tarfile.open(path, "r:xz") as tf:
        for info in sorted(tf.getmembers(), key=lambda m: m.name):
            if not (info.isfile() and info.name.endswith(".dat")):
                raise ValueError(f"{path.name}: unexpected member {info.name!r}")
            handle = tf.extractfile(info)
            assert handle is not None
            traces.append(parse_dat(info.name, handle.read()))
    return traces


def global_levels(traces: list[DatTrace]) -> np.ndarray:
    """The sorted distinct idealized levels of a set: one Tether state per level."""
    return np.unique(np.concatenate([t.idealized for t in traces]))


def state_path(trace: DatTrace, levels: np.ndarray, *, no_state: int) -> np.ndarray:
    """The trace's idealized path as indices into ``levels``, ``no_state`` at a dropped frame.

    smFRET_module drops each frame whose raw FRET is out of range and then reads the runs of what
    is left. Tether's :func:`tether.analysis.dwell.state_dwells` drops ``NO_STATE`` frames and
    reads the runs of what is left, so the dropped frames become ``NO_STATE``.
    """
    index = np.searchsorted(levels, trace.idealized)
    if not np.array_equal(levels[index], trace.idealized):
        raise ValueError(f"{trace.name}: a level is missing from the level table")
    return np.where(trace.kept, index, no_state).astype(np.int64)
