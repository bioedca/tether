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
- [`curation_snapshot`][] — the accept/reject state of one molecule as an undo restores
  it (#632): its ``curation_label``, whether a carried label waits for review, and the
  reason of a reject.
- [`molecule_states`][] — what the molecule list shows for every row (#630): the curation
  state, the category, and whether an idealization exists and is stale.
- [`category_names`][], [`category_classes`][], [`category_members`][] and
  [`condition_key`][] — what the category editor and the number keys show for one
  condition (#633): its ordered list, the class each key gives, the molecules that have a
  name, and the key that names the condition.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

    import numpy as np

    from tether.io.filename import ConditionKey

__all__ = [
    "ACCEPTED",
    "CURATION_STATES",
    "REJECTED",
    "UNDECIDED",
    "CurationSnapshot",
    "MoleculeState",
    "StoredMolecule",
    "category_classes",
    "category_members",
    "category_names",
    "condition_key",
    "curation_snapshot",
    "curation_state",
    "calibration_windows",
    "molecule_states",
    "resolved_frame_range",
    "resolved_window",
    "stored_molecule",
    "stored_molecules",
    "stored_rows",
    "summed_bleach_frame",
]

#: The curation state of a molecule that is accepted (``curation_label`` ``+1``).
ACCEPTED = "accepted"
#: The curation state of a molecule that is rejected (``curation_label`` ``-1``).
REJECTED = "rejected"
#: The curation state of a molecule with no decision (``curation_label`` ``0``).
UNDECIDED = "undecided"
#: The three curation states, in the order the molecule list counts them (#630).
CURATION_STATES = (ACCEPTED, REJECTED, UNDECIDED)


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
    category
        The row's category name; ``""`` when it has none.
    molecule_id
        The row's unique ``molecule_id``.
    """

    row: int
    molecule_key: str
    condition_id: str
    frame_range: tuple[int, int]
    window: tuple[int, int]
    category: str = ""
    molecule_id: str = ""


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


def calibration_windows(path: Any) -> np.ndarray:
    """Return the absolute calibration window of each ``/molecules`` row, in row order (#928).

    The windows γ reads its acceptor-bleach step from, apart from the analysis window
    ([`tether.project.calibration_windows`][]); a row with none stored has its default.
    """
    from tether.project.calibration_windows import read_calibration_windows  # noqa: PLC0415

    windows, _ = read_calibration_windows(path)
    return windows


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
                category=_text(record["category"]),
                molecule_id=_text(record["molecule_id"]),
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


@dataclass(frozen=True)
class MoleculeState:
    """What the molecule list shows for one ``/molecules`` row (#630, PRD §7.3).

    Attributes
    ----------
    row
        The row index in ``/molecules`` (and so in the list).
    molecule_key
        The row's ``molecule_key``.
    movie_id
        The movie the row was extracted from.
    curation
        [`ACCEPTED`][], [`REJECTED`][] or [`UNDECIDED`][] (see [`curation_state`][]).
    category
        The row's category name; ``""`` when it has none.
    idealized
        Whether a model under ``/idealization`` holds a fit of this row.
    stale
        Whether one of those fits is stale: the row's inputs changed after the fit.
    """

    row: int
    molecule_key: str
    movie_id: str
    curation: str
    category: str = ""
    idealized: bool = False
    stale: bool = False


def curation_state(label: int) -> str:
    """Map a ``curation_label`` to its state: ``+1`` accepted, ``-1`` rejected, else undecided."""
    if label > 0:
        return ACCEPTED
    if label < 0:
        return REJECTED
    return UNDECIDED


@dataclass(frozen=True)
class CurationSnapshot:
    """One molecule's human accept/reject state, as an undo writes it back (#632).

    Attributes
    ----------
    label
        The ``curation_label``: ``+1`` accepted, ``-1`` rejected, ``0`` uncurated.
    pending
        Whether a row of the molecule carries a label of an earlier trace generation that
        waits for review (ADR-0136).
    reason, note, value
        The reason row of the current reject
        ([`reject_record_of`][tether.project.labels.reject_record_of]): its reason code,
        note and threshold value. ``"unspecified"``, ``""`` and ``None`` when the molecule
        is not rejected or its reject has no reason row.
    """

    label: int
    pending: bool = False
    reason: str = "unspecified"
    note: str = ""
    value: float | None = None


def curation_snapshot(project_path: str | Path, molecule_key: str) -> CurationSnapshot:
    """Read the [`CurationSnapshot`][] of ``molecule_key`` (read-only).

    Raises ``KeyError`` when no row carries the key.
    """
    import math  # noqa: PLC0415

    import h5py  # noqa: PLC0415

    from tether.io.generations import pending_label_ids_in  # noqa: PLC0415
    from tether.io.schema import TABLE  # noqa: PLC0415
    from tether.project.labels import CurationLabel, reject_record_of  # noqa: PLC0415

    path = Path(project_path)
    with h5py.File(path, "r") as f:
        table = f["molecules"][TABLE]
        keys = [_text(k) for k in table.fields("molecule_key")[:]]
        rows = [i for i, key in enumerate(keys) if key == molecule_key]
        if not rows:
            raise KeyError(f"no molecule with molecule_key {molecule_key!r} in {path.name}")
        ids = table.fields("molecule_id")[:]
        label = int(table.fields("curation_label")[rows[0]])
        waiting = pending_label_ids_in(f)
        pending = any(_text(ids[i]) in waiting for i in rows)
    if label != CurationLabel.REJECT:
        return CurationSnapshot(label=label, pending=pending)
    record = reject_record_of(path, molecule_key)
    if record is None:
        return CurationSnapshot(label=label, pending=pending)
    value = float(record["value"])
    return CurationSnapshot(
        label=label,
        pending=pending,
        reason=_text(record["reason"]),
        note=_text(record["note"]),
        value=None if math.isnan(value) else value,
    )


def molecule_states(project_path: str | Path) -> tuple[MoleculeState, ...]:
    """Read the list state of every ``/molecules`` row, in row order (read-only, #630).

    One read of ``/molecules`` gives the curation state and the category. For the
    idealization marks each model's staleness stamp is read and checked against the
    current traces, by the rule of
    [`stale_row_ids`][tether.project.idealize.stale_row_ids], from one read of the traces
    for all models (#1410); the join is on the unique
    ``molecule_id``, so two rows that share a ``molecule_key`` keep their own marks.
    """
    import h5py  # noqa: PLC0415

    from tether.io.schema import TABLE  # noqa: PLC0415
    from tether.project.idealize import (  # noqa: PLC0415
        list_idealizations,
        read_staleness_stamp,
        stale_row_ids_each,
    )

    path = Path(project_path)
    with h5py.File(path, "r") as f:
        table = f["molecules"][TABLE]
        if table.shape[0] == 0:
            return ()
        keys = [_text(k) for k in table.fields("molecule_key")[:]]
        ids = [_text(k) for k in table.fields("molecule_id")[:]]
        movies = [_text(k) for k in table.fields("movie_id")[:]]
        categories = [_text(k) for k in table.fields("category")[:]]
        labels = [int(v) for v in table.fields("curation_label")[:]]
    stamps = [read_staleness_stamp(path, model) for model in list_idealizations(path)]
    fitted: set[str] = set()
    stale: set[str] = set()
    for stamp, stale_ids in zip(stamps, stale_row_ids_each(path, stamps), strict=True):
        fitted.update(stamp.molecule_ids)
        stale.update(stale_ids)
    return tuple(
        MoleculeState(
            row=row,
            molecule_key=keys[row],
            movie_id=movies[row],
            curation=curation_state(labels[row]),
            category=categories[row],
            idealized=ids[row] in fitted,
            stale=ids[row] in stale,
        )
        for row in range(len(keys))
    )


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


# --- one condition's category list, for the editor and the keys (#633) ------------


def category_names(project_path: str | Path, condition_id: str) -> tuple[str, ...]:
    """Read the ordered category list of ``condition_id`` (``()`` when it has none).

    Read-only, through
    [`read_category_list`][tether.project.conditions.read_category_list].
    """
    from tether.project.conditions import read_category_list  # noqa: PLC0415

    return read_category_list(project_path, condition_id).categories


def category_classes(project_path: str | Path, condition_id: str) -> dict[int, str]:
    """Read the class each number key gives in ``condition_id``, as ``{class: name}``.

    The classes are ``>= 1``; class 0 is always *uncategorized* and is not listed.
    Read-only, through the persisted lookup of
    [`read_class_lookup`][tether.project.conditions.read_class_lookup] (ADR-0125).
    Raises `ValueError` when that lookup disagrees with the list beside it.
    """
    from tether.project.conditions import read_class_lookup  # noqa: PLC0415

    return dict(read_class_lookup(project_path, condition_id).classes)


def category_members(project_path: str | Path, condition_id: str, category: str) -> tuple[str, ...]:
    """Read the keys of the molecules of ``condition_id`` whose stored category is ``category``.

    In row order, each key once (two rows can share a key). Read-only.
    """
    import h5py  # noqa: PLC0415

    from tether.io.schema import TABLE  # noqa: PLC0415

    with h5py.File(Path(project_path), "r") as f:
        table = f["molecules"][TABLE]
        keys = [_text(k) for k in table.fields("molecule_key")[:]]
        conditions = [_text(k) for k in table.fields("condition_id")[:]]
        categories = [_text(k) for k in table.fields("category")[:]]
    members = (
        key
        for key, cid, name in zip(keys, conditions, categories, strict=True)
        if cid == condition_id and name == category
    )
    return tuple(dict.fromkeys(members))


def condition_key(project_path: str | Path, condition_id: str) -> ConditionKey | None:
    """Read the ``/conditions`` key of ``condition_id``, or ``None`` when it has no row.

    Read-only, through
    [`read_condition_keys`][tether.project.conditions.read_condition_keys].
    """
    from tether.project.conditions import read_condition_keys  # noqa: PLC0415

    return read_condition_keys(project_path).get(condition_id)
