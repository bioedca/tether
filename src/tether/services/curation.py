# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reads behind the curation view's category and analysis-window keys (#359, PRD §7.3).

The keys ``1``–``9``, ``0``, ``-``/``=``, ``[``/``]``, ``R`` and ``P`` write through the
[`Project`][tether.project.core.Project] setters of #358. Before a write the application
needs three facts about the selected molecule, and these functions read them. They read and
never write, so, like [`tether.services.notices`][], they are plain functions here rather
than registered service commands, and they import the core only when they run (ADR-0100).

- [`stored_molecule`][] — the selected row's condition, ``frame_range`` and absolute
  ``analysis_window``, resolved by the rules every windowed reader applies, and
  [`stored_molecules`][] for every row that shares its key, and [`stored_rows`][] for
  the rows of many keys in one read.
- [`summed_bleach_frame`][] — the first frame of the summed-intensity photobleach, by the
  detector and priors that
  [`compute_photobleach`][tether.project.photobleach.compute_photobleach] runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = [
    "StoredMolecule",
    "resolved_frame_range",
    "resolved_window",
    "stored_molecule",
    "stored_molecules",
    "stored_rows",
    "summed_bleach_frame",
]


@dataclass(frozen=True)
class StoredMolecule:
    """The ``/molecules`` facts a curation category or window write starts from.

    Attributes
    ----------
    row
        The row index in ``/molecules`` (and so in ``/traces``).
    molecule_key
        The row's ``molecule_key``.
    condition_id
        The molecule's condition, whose category list the number keys index.
    frame_range
        ``(start, stop)`` native frames, resolved by [`resolved_frame_range`][].
    window
        The absolute ``(start, stop)`` analysis window, resolved by [`resolved_window`][].
    """

    row: int
    molecule_key: str
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


def stored_rows(
    project_path: str | Path, molecule_keys: Iterable[str]
) -> tuple[StoredMolecule, ...]:
    """Read every ``/molecules`` row whose key is in ``molecule_keys``, in row order (read-only).

    One open of the file serves any number of keys, so a caller that refreshes many rows
    after one write (a return-leg import) reads once. Keys no row carries are skipped;
    an empty result is not an error.
    """
    import h5py  # noqa: PLC0415

    from tether.io.schema import TABLE  # noqa: PLC0415

    wanted = set(molecule_keys)
    with h5py.File(Path(project_path), "r") as f:
        table = f["molecules"][TABLE]
        keys = [_text(k) for k in table.fields("molecule_key")[:]]
        rows = [i for i, key in enumerate(keys) if key in wanted]
        records = [table[i] for i in rows]
        n_frames = next(
            (int(layer.shape[1]) for layer in f["traces"].values() if layer.ndim == 2), 0
        )
    out = []
    for row, record in zip(rows, records, strict=True):
        frame_range = resolved_frame_range(record["frame_range"], n_frames)
        out.append(
            StoredMolecule(
                row=row,
                molecule_key=keys[row],
                condition_id=_text(record["condition_id"]),
                frame_range=frame_range,
                window=resolved_window(record["analysis_window"], frame_range),
            )
        )
    return tuple(out)


def stored_molecules(project_path: str | Path, molecule_key: str) -> tuple[StoredMolecule, ...]:
    """Read every ``/molecules`` row that carries ``molecule_key``, in row order (read-only).

    A key-based setter writes all of them, each clamped to its own ``frame_range``, so a
    caller that shows rows refreshes each one from here after a write. Raises
    ``KeyError`` when no row carries the key.
    """
    out = stored_rows(project_path, (molecule_key,))
    if not out:
        raise KeyError(
            f"no molecule with molecule_key {molecule_key!r} in {Path(project_path).name}"
        )
    return out


def stored_molecule(
    project_path: str | Path, molecule_key: str, *, row: int | None = None
) -> StoredMolecule:
    """Read row ``row`` of ``/molecules`` if it carries ``molecule_key``, else the first that does.

    Pass the row the curator selected: two rows can share a key (the same file extracted
    twice) with different frame ranges and windows, and an edit starts from the selected
    one. Read-only; raises ``KeyError`` when no row carries the key.
    """
    matches = stored_molecules(project_path, molecule_key)
    return next((m for m in matches if m.row == row), matches[0])


def summed_bleach_frame(project_path: str | Path, molecule: StoredMolecule) -> int:
    """Return the absolute first frame of ``molecule``'s summed-intensity photobleach.

    Runs the same summed-intensity detector and frozen §11.2 priors as
    [`compute_photobleach`][tether.project.photobleach.compute_photobleach] on the
    row's background-subtracted donor and acceptor over its ``frame_range``.
    Returns ``frame_range[1]`` when no bleach is detected and raises ``KeyError``
    when the corrected layers are missing.
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
