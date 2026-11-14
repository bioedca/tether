# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""A test-only reader for OpenFRET JSON trace sets (#720; PRD §2.2 "What the tests can use").

OpenFRET (https://pypi.org/project/openfret) is the JSON trace format that PySMACKS writes. One
file holds a ``traces`` list; each trace has a ``channels`` list, whose entries carry a
``channel_type`` and the per-frame ``data``, and a ``metadata`` dictionary. PySMACKS names the
channels ``DD``, ``DA``, ``AA`` (donor and acceptor emission under donor excitation, acceptor
emission under acceptor excitation) and ``back_DD``, ``back_DA``, ``back_AA`` (the local
background of each), and writes a label as ``metadata[<label>] = {"indmin": i, "indmax": j}``,
the frames ``[i, j)`` of the trace that carry that label (``FRET``, ``DO``, ``AO``).

**Rule 1 of PRD §2.2: ALEX data at trace level only.** The reader returns the two donor-
excitation channels and their backgrounds, and nothing from the acceptor-excitation frames: no
``AA``, no ``back_AA``. Tether gets no ALEX support from this module.

**Rule 2: old formats through test-only readers.** The module lives under ``tests/`` and is
imported as the top-level module ``_openfret`` (the pattern of ``_synth``). The product has no
OpenFRET importer, and a test reads the original bytes of the published file: :func:`read_openfret`
opens the downloaded ``.json.zip`` itself, not a converted copy.
"""

from __future__ import annotations

import json
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

__all__ = [
    "DONOR_EXCITATION_CHANNELS",
    "LABELS",
    "OpenFretSet",
    "OpenFretTrace",
    "read_openfret",
]

#: The channels the reader keeps: donor-excitation intensities and their local backgrounds.
DONOR_EXCITATION_CHANNELS = ("DD", "DA", "back_DD", "back_DA")
#: The trace labels PySMACKS writes, each a ``{"indmin", "indmax"}`` frame range.
LABELS = ("FRET", "DO", "AO")


@dataclass(frozen=True)
class OpenFretTrace:
    """One trace: the donor-excitation channels as ``float64`` and the labelled frame ranges."""

    molecule_id: str
    uuid: str
    donor: np.ndarray
    acceptor: np.ndarray
    donor_background: np.ndarray
    acceptor_background: np.ndarray
    labels: Mapping[str, tuple[int, int]]
    background_correction: str | None

    def segment(self, label: str) -> tuple[np.ndarray, np.ndarray]:
        """Background-subtracted donor and acceptor over the frames ``[indmin, indmax)`` of *label*.

        The background is subtracted frame by frame, which is what PySMACKS 1.0.0b4 does for a
        trace whose ``background_correction`` is ``"Median"``: its ``extract_labelled_traces``
        has no case for that value, so the stored ``back_DD`` and ``back_DA`` are used as they are.
        """
        start, stop = self.labels[label]
        donor = self.donor[start:stop] - self.donor_background[start:stop]
        acceptor = self.acceptor[start:stop] - self.acceptor_background[start:stop]
        return donor, acceptor


@dataclass(frozen=True)
class OpenFretSet:
    """A whole OpenFRET file: its traces and the top-level ``metadata`` dictionary."""

    traces: tuple[OpenFretTrace, ...]
    metadata: Mapping[str, object] = field(default_factory=dict)

    def labelled(self, label: str) -> tuple[OpenFretTrace, ...]:
        """The traces that carry *label*, in file order."""
        return tuple(t for t in self.traces if label in t.labels)


def _labels(
    metadata: Mapping[str, object], n_frames: int, where: str
) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    for label in LABELS:
        if label not in metadata:
            continue
        span = metadata[label]
        if not isinstance(span, Mapping) or set(span) != {"indmin", "indmax"}:
            raise ValueError(f"{where}: label {label} is not an {{indmin, indmax}} range: {span!r}")
        start, stop = span["indmin"], span["indmax"]
        if not (isinstance(start, int) and isinstance(stop, int) and 0 <= start < stop <= n_frames):
            raise ValueError(f"{where}: label {label} range [{start}, {stop}) is outside the trace")
        out[label] = (start, stop)
    return out


def _trace(raw: Mapping[str, object], index: int) -> OpenFretTrace:
    where = f"trace {index}"
    by_type: dict[str, np.ndarray] = {}
    for channel in raw["channels"]:
        name = channel["channel_type"]
        if name in by_type:
            raise ValueError(f"{where}: two channels of type {name}")
        if name in DONOR_EXCITATION_CHANNELS:
            by_type[name] = np.asarray(channel["data"], dtype=np.float64)
    missing = [c for c in DONOR_EXCITATION_CHANNELS if c not in by_type]
    if missing:
        raise ValueError(f"{where}: no channel {missing}")
    lengths = {c: by_type[c].shape for c in DONOR_EXCITATION_CHANNELS}
    if len(set(lengths.values())) != 1 or by_type["DD"].ndim != 1:
        raise ValueError(f"{where}: the channels differ in length: {lengths}")
    metadata = raw["metadata"]
    correction = metadata.get("background_correction")
    return OpenFretTrace(
        molecule_id=str(metadata["molecule_id"]),
        uuid=str(metadata["UUID_v7"]),
        donor=by_type["DD"],
        acceptor=by_type["DA"],
        donor_background=by_type["back_DD"],
        acceptor_background=by_type["back_DA"],
        labels=_labels(metadata, by_type["DD"].shape[0], where),
        background_correction=None if correction is None else str(correction),
    )


def read_openfret(path: str | Path, member: str | None = None) -> OpenFretSet:
    """Read an OpenFRET ``.json`` file, or the one JSON member of a ``.zip`` that holds it.

    A zip with more than one JSON member needs *member*. A trace without one of the four
    donor-excitation channels, with channels of unequal length or with a label outside the
    trace is refused with a ``ValueError`` that names the trace, never read in part.
    """
    path = Path(path)
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            if member is None:
                names = [n for n in archive.namelist() if n.endswith(".json")]
                if len(names) != 1:
                    raise ValueError(f"{path.name}: name the JSON member; found {names}")
                member = names[0]
            raw = json.loads(archive.read(member))
    else:
        raw = json.loads(path.read_bytes())
    traces = tuple(_trace(t, i) for i, t in enumerate(raw["traces"]))
    return OpenFretSet(traces=traces, metadata=dict(raw.get("metadata", {})))
