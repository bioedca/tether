# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reads behind the curation view's category and analysis-window keys (#359, PRD §7.3).

The keys ``1``–``9``, ``0``, ``-``/``=``, ``[``/``]``, ``R`` and ``P`` write through the
[`Project`][tether.project.core.Project] setters of #358. Before a write the application
needs three facts about the selected molecule, and these functions read them. They read and
never write, so, like [`tether.services.notices`][], they are plain functions here rather
than registered service commands, and they import the core only when they run (ADR-0100).

- [`stored_molecule`][] — the molecule's row, condition, ``frame_range`` and absolute
  ``analysis_window``, resolved by the rules every windowed reader applies.
- [`summed_bleach_frame`][] — the first frame of the summed-intensity photobleach, by the
  detector and priors that
  [`compute_photobleach`][tether.project.photobleach.compute_photobleach] runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "StoredMolecule",
    "resolved_frame_range",
    "resolved_window",
    "stored_molecule",
    "summed_bleach_frame",
]


@dataclass(frozen=True)
class StoredMolecule:
    """The ``/molecules`` facts a curation category or window write starts from.

    Attributes
    ----------
    row
        The row index in ``/molecules`` (and so in ``/traces``) of the first row that
        carries the molecule key.
    condition_id
        The molecule's condition, whose category list the number keys index.
    frame_range
        ``(start, stop)`` native frames, resolved by [`resolved_frame_range`][].
    window
        The absolute ``(start, stop)`` analysis window, resolved by [`resolved_window`][].
    """

    row: int
    condition_id: str
    frame_range: tuple[int, int]
    window: tuple[int, int]


def resolved_frame_range(frame_range: Any, n_frames: int) -> tuple[int, int]:
    """Return a row's ``frame_range`` as ints; unset ``[0, 0]`` is the whole row.

    An unset range reads as ``(0, n_frames)``.

    The rule the curation view's trace reader applies (``traces_from_store``), so a window
    read here lines up with the trace the view shows.
    """
    lo, hi = int(frame_range[0]), int(frame_range[1])
    return (0, n_frames) if hi <= lo else (lo, hi)


def resolved_window(analysis_window: Any, frame_range: tuple[int, int]) -> tuple[int, int]:
    """Return a row's absolute ``analysis_window``; unset (``stop <= start``) is ``frame_range``.

    The same fallback every windowed reader applies (§7.2).
    """
    start, stop = int(analysis_window[0]), int(analysis_window[1])
    return frame_range if stop <= start else (start, stop)


def _text(value: object) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def stored_molecule(project_path: str | Path, molecule_key: str) -> StoredMolecule:
    """Read the first ``/molecules`` row that carries ``molecule_key`` (read-only).

    Raises [`KeyError`][] when no row carries the key.
    """
    import h5py  # noqa: PLC0415

    from tether.io.schema import TABLE  # noqa: PLC0415

    path = Path(project_path)
    with h5py.File(path, "r") as f:
        table = f["molecules"][TABLE]
        keys = [_text(k) for k in table.fields("molecule_key")[:]]
        if molecule_key not in keys:
            raise KeyError(f"no molecule with molecule_key {molecule_key!r} in {path.name}")
        row = keys.index(molecule_key)
        record = table[row]
        n_frames = next(
            (int(layer.shape[1]) for layer in f["traces"].values() if layer.ndim == 2), 0
        )
    frame_range = resolved_frame_range(record["frame_range"], n_frames)
    return StoredMolecule(
        row=row,
        condition_id=_text(record["condition_id"]),
        frame_range=frame_range,
        window=resolved_window(record["analysis_window"], frame_range),
    )


def summed_bleach_frame(project_path: str | Path, molecule: StoredMolecule) -> int:
    """Return the absolute first frame of ``molecule``'s summed-intensity photobleach.

    Reads the row's background-subtracted (``corrected``) donor and acceptor over its
    ``frame_range`` and runs [`detect_photobleach`][tether.fret.photobleach.detect_photobleach]
    with its frozen §11.2 priors, as
    [`compute_photobleach`][tether.project.photobleach.compute_photobleach] does: raw traces
    keep a background pedestal and never look bleached. ``frame_range[1]`` means the trace
    does not bleach. Raises [`KeyError`][] when the corrected layers are missing.
    """
    import h5py  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    from tether.fret.photobleach import detect_photobleach  # noqa: PLC0415
    from tether.project.trace_layers import INTENSITY_QUANTITY_LAYERS  # noqa: PLC0415

    lo, hi = molecule.frame_range
    donor_layer, acceptor_layer = INTENSITY_QUANTITY_LAYERS["corrected"]
    with h5py.File(Path(project_path), "r") as f:
        traces = f["traces"]
        for layer in (donor_layer, acceptor_layer):
            if layer not in traces:
                raise KeyError(f"project has no /traces/{layer}; run extraction first")
        donor = np.asarray(traces[donor_layer][molecule.row, lo:hi], dtype=np.float64)
        acceptor = np.asarray(traces[acceptor_layer][molecule.row, lo:hi], dtype=np.float64)
    return lo + int(detect_photobleach(donor, acceptor).sum_pb)
