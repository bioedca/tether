# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Readers for the kinSoftChallenge simulated datasets (PRD §8 NFR-VALID(c), §9 M8).

The kinSoftChallenge [Götz2022] is a blind community benchmark of smFRET
kinetic-rate analysis tools. Two readers:

* :func:`read_kinsoft_trace` parses one raw challenge trace text file — an ALEX
  table with columns ``%t (s)  Idd  Ida  Iaa  FRET E`` (tab-separated).
* :func:`read_kinsoft_fixture` reads the packed, gated-tier
  ``tests/fixtures/large/kinsoft_sim.hdf5`` (staged by
  ``scripts/make_kinsoft_fixture.py``) — the three simulated levels
  (Fig2/3/4 = level 1/2/3) as zero-padded arrays plus per-trace lengths.

The source data is **CC-BY-4.0** (Götz & Schmid, Zenodo 10.5281/zenodo.5701310),
distinct from the repo's GPL-3.0; the packed fixture carries that license (see
NOTICE / the fixture PROVENANCE). These readers only ingest it — the M8 kinetics
oracle that fits rates and compares to the reported inter-tool spread rides on
top of them.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

# Intensity / FRET columns of every challenge trace file, in on-disk order
# (column 0 is the time axis, handled separately).
_COLUMNS = ("idd", "ida", "iaa", "fret_e")


# eq=False: array fields make the auto __eq__ return an ambiguous array (and the
# frozen __hash__ unhashable); these carry data, not identity, so use object eq.
@dataclass(frozen=True, eq=False)
class KinsoftTrace:
    """One kinSoftChallenge trace: the ALEX intensities and the reported FRET E.

    All arrays are 1-D and the same length. ``idd`` is donor emission under donor
    excitation, ``ida`` acceptor emission under donor excitation, ``iaa`` acceptor
    emission under acceptor excitation (the ALEX channel), and ``fret_e`` the
    apparent FRET efficiency reported in the file. ``frame_time_s`` is the frame
    period (constant within a dataset).
    """

    time: np.ndarray
    idd: np.ndarray
    ida: np.ndarray
    iaa: np.ndarray
    fret_e: np.ndarray
    frame_time_s: float


@dataclass(frozen=True, eq=False)
class KinsoftLevel:
    """One packed challenge level (a difficulty tier / paper figure).

    ``idd``/``ida``/``iaa``/``fret_e`` are ``(n_traces, max_frames)`` arrays,
    zero-padded past each trace's ``length[i]``. Use :meth:`trace` to get an
    unpadded :class:`KinsoftTrace`.
    """

    figure: str
    frame_time_s: float
    length: np.ndarray
    idd: np.ndarray
    ida: np.ndarray
    iaa: np.ndarray
    fret_e: np.ndarray

    @property
    def n_traces(self) -> int:
        """Number of traces packed in this level (the arrays' leading dimension)."""
        return int(self.length.shape[0])

    def trace(self, i: int) -> KinsoftTrace:
        """Return the ``i``-th trace, trimmed to its real length."""
        n = int(self.length[i])
        time = np.arange(n, dtype=np.float64) * self.frame_time_s
        return KinsoftTrace(
            time=time,
            idd=self.idd[i, :n],
            ida=self.ida[i, :n],
            iaa=self.iaa[i, :n],
            fret_e=self.fret_e[i, :n],
            frame_time_s=self.frame_time_s,
        )


def read_kinsoft_trace(path: str | Path) -> KinsoftTrace:
    """Parse one raw kinSoftChallenge trace text file into a :class:`KinsoftTrace`.

    The file is a tab-separated ALEX table whose header line begins with ``%``
    (``%t (s)\tIdd (a.u.)\tIda (a.u.)\tIaa (a.u.)\tFRET E``); values may carry an
    explicit ``+`` sign and scientific notation.
    """
    data = np.loadtxt(path, delimiter="\t", comments="%", dtype=np.float64, ndmin=2)
    if data.shape[1] != 5:
        raise ValueError(f"expected 5 columns (t, Idd, Ida, Iaa, E), got {data.shape[1]}")
    if data.shape[0] < 2:
        raise ValueError("trace has fewer than 2 frames; cannot infer frame time")
    time = data[:, 0]
    frame_time = float(time[1] - time[0])
    return KinsoftTrace(
        time=time,
        idd=data[:, 1],
        ida=data[:, 2],
        iaa=data[:, 3],
        fret_e=data[:, 4],
        frame_time_s=frame_time,
    )


def read_kinsoft_fixture(path: str | Path) -> dict[str, KinsoftLevel]:
    """Read the packed kinSoft fixture into ``{group_name: KinsoftLevel}``.

    ``path`` is the gated-tier ``kinsoft_sim.hdf5``. h5py is imported lazily so
    ``import tether.io`` and :func:`read_kinsoft_trace` work without it.
    """
    import h5py  # noqa: PLC0415  (lazy: keep h5py off the base import path)

    levels: dict[str, KinsoftLevel] = {}
    with h5py.File(path, "r") as h5:
        if h5.attrs.get("format") != "kinsoft-sim":
            raise ValueError(f"{path} is not a kinsoft-sim fixture (format attr mismatch)")
        for name, group in h5.items():
            levels[name] = KinsoftLevel(
                figure=str(group.attrs["figure"]),
                frame_time_s=float(group.attrs["frame_time_s"]),
                length=group["length"][()],
                **{c: group[c][()] for c in _COLUMNS},
            )
    return levels
