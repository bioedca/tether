# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The reconciliation of a new trace generation with the one before it (#923, ADR-0136).

When a movie is extracted again ([`reextract_movie`][tether.project.extract.reextract_movie]),
the molecules that did not move keep their ``molecule_key``, and their curation label,
category and analysis window move to the new rows with the mark *carried* (ADR-0136
item 5; card C5, option A). This module tells the user what happened, molecule by
molecule, and records the confirmations:

* [`reconcile_generation`][] compares the active generation of one movie with the
  generation before it and gives three lists: **carried** (the key is in both, and the
  labels moved), **new** (a molecule of the active generation with no partner, so no
  labels) and **lost** (a molecule of the previous generation that the active one does not
  have). A lost molecule and its labels stay readable in the previous generation
  ([`read_generation`][tether.io.generations.read_generation]) and in ``/labels``, which is
  keyed by ``molecule_key``.
* [`reconcile_project`][] does it for each movie with more than one generation, and
  [`reconciliation_counts`][] adds the counts up for ``tether status``.
* [`confirm_carried`][] removes the mark *carried* from molecules the user confirms. Each
  confirmation is one human row in ``/labels/table`` with the molecule's current label, the
  user and the time, linked to the molecule's current ``molecule_id`` in
  ``/generations/label_reviews`` (ADR-0136 item 7). An explicit accept or reject of a
  carried molecule confirms it in the same way. The earlier rows of ``/labels`` are not
  changed, so the original curator and time stay readable.

A carried molecule that waits for confirmation does not train the ranker (ADR-0136 item 7);
this module only reads that state and adds confirmations. Every read opens the project
read-only and takes no lock. Nothing here imports Qt.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable
    from os import PathLike

    import numpy as np

__all__ = [
    "CARRIED",
    "LOST",
    "NEW",
    "ConfirmResult",
    "GenerationReconciliation",
    "ReconciledMolecule",
    "ReconciliationCounts",
    "confirm_carried",
    "reconcile_generation",
    "reconcile_project",
    "reconciliation_counts",
]

#: The group of a molecule whose labels moved from the previous generation.
CARRIED = "carried"
#: The group of a molecule of the active generation with no partner in the previous one.
NEW = "new"
#: The group of a molecule of the previous generation that the active one does not have.
LOST = "lost"


def _text(value: object) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


@dataclass(frozen=True)
class ReconciledMolecule:
    """One molecule of a reconciliation.

    Attributes
    ----------
    group:
        [`CARRIED`][], [`NEW`][] or [`LOST`][].
    molecule_key:
        The stable key. A carried molecule has the same key in both generations.
    molecule_id:
        The ``molecule_id`` in the active generation, or, for a lost molecule, in the
        previous generation.
    donor_xy:
        The donor position in pixels of the donor sub-image of its generation.
    curation_label:
        The [`CurationLabel`][tether.project.labels.CurationLabel] code: ``+1`` accept,
        ``-1`` reject, ``0`` uncurated.
    category:
        The category, ``""`` when none.
    analysis_window:
        The stored ``(start, stop)`` analysis window; ``(0, 0)`` is unset.
    labeler, labelled_utc:
        Who set the label and when: the latest human row of ``/labels`` for the key before
        the confirmation, or ``""`` when no person labelled the molecule. For a carried
        molecule this is the original curator, kept from the earlier generation.
    confirmed_by, confirmed_utc:
        For a carried molecule that a person confirmed, who and when; else ``""``.
    confirmed:
        Whether the carried molecule is linked to a review event in
        ``/generations/label_reviews``. This, not the name, decides if it waits.
    """

    group: str
    molecule_key: str
    molecule_id: str
    donor_xy: tuple[float, float]
    curation_label: int
    category: str
    analysis_window: tuple[int, int]
    labeler: str = ""
    labelled_utc: str = ""
    confirmed_by: str = ""
    confirmed_utc: str = ""
    confirmed: bool = False

    @property
    def waiting(self) -> bool:
        """Whether this is a carried molecule that waits for confirmation."""
        return self.group == CARRIED and not self.confirmed


@dataclass(frozen=True)
class ReconciliationCounts:
    """How many molecules are carried, new and lost, and how many carried ones wait.

    ``movies`` is the number of movies with more than one generation that the counts
    cover. Counts add with ``+``.
    """

    carried: int = 0
    new: int = 0
    lost: int = 0
    waiting: int = 0
    movies: int = 0

    def __add__(self, other: ReconciliationCounts) -> ReconciliationCounts:
        """Return the sum of two counts."""
        return ReconciliationCounts(
            self.carried + other.carried,
            self.new + other.new,
            self.lost + other.lost,
            self.waiting + other.waiting,
            self.movies + other.movies,
        )

    def to_dict(self) -> dict[str, int]:
        """Return the JSON form (``docs/cli.md``, the ``reconciliation`` key of status)."""
        return {
            "movies": self.movies,
            "carried": self.carried,
            "new": self.new,
            "lost": self.lost,
            "waiting": self.waiting,
        }


@dataclass(frozen=True)
class GenerationReconciliation:
    """The three lists of one movie: its active generation against the one before it."""

    movie_id: str
    #: The number of the active generation.
    generation: int
    #: The number of the generation it is compared with (``generation - 1``).
    previous: int
    carried: tuple[ReconciledMolecule, ...] = ()
    new: tuple[ReconciledMolecule, ...] = ()
    lost: tuple[ReconciledMolecule, ...] = ()

    @property
    def waiting(self) -> tuple[ReconciledMolecule, ...]:
        """The carried molecules that wait for confirmation."""
        return tuple(m for m in self.carried if m.waiting)

    @property
    def counts(self) -> ReconciliationCounts:
        """The counts of the three lists."""
        return ReconciliationCounts(
            len(self.carried), len(self.new), len(self.lost), len(self.waiting), 1
        )


@dataclass(frozen=True)
class ConfirmResult:
    """What [`confirm_carried`][] did: the keys it confirmed and the keys confirmed before."""

    confirmed: tuple[str, ...] = ()
    already: tuple[str, ...] = ()
    labeler: str = ""
    timestamp: str = ""


def _human_rows(labels: np.ndarray) -> dict[str, list[int]]:
    """Map each key to the indices of its human rows in ``/labels/table``, in order."""
    from tether.project.labels import LABEL_SOURCE_HUMAN  # noqa: PLC0415

    out: dict[str, list[int]] = {}
    for i, row in enumerate(labels):
        if _text(row["source"]) == LABEL_SOURCE_HUMAN:
            out.setdefault(_text(row["molecule_key"]), []).append(i)
    return out


def _labelled(
    labels: np.ndarray, rows: list[int], before: int | None, reviews: frozenset[int]
) -> tuple[str, str]:
    """``(labeler, timestamp)`` of who set the label, from ``rows`` before index ``before``.

    A review event (an index in ``reviews``) that keeps the label it found is a
    confirmation of an earlier generation, not a curation, so it is passed over and the
    curator before it is given. A review that changed the label is a curation.
    """
    from tether.project.labels import CurationLabel  # noqa: PLC0415

    earlier = [i for i in rows if before is None or i < before]
    while earlier and earlier[-1] in reviews:
        found = (
            int(labels[earlier[-2]]["label_value"])
            if len(earlier) > 1
            else int(CurationLabel.UNCURATED)
        )
        if int(labels[earlier[-1]]["label_value"]) != found:
            break
        earlier.pop()
    if not earlier:
        return "", ""
    row = labels[earlier[-1]]
    return _text(row["labeler"]), _text(row["timestamp"])


def _molecule(group: str, row: Any, **provenance: str) -> ReconciledMolecule:
    window = row["analysis_window"]
    return ReconciledMolecule(
        group=group,
        molecule_key=_text(row["molecule_key"]),
        molecule_id=_text(row["molecule_id"]),
        donor_xy=(float(row["donor_xy"][0]), float(row["donor_xy"][1])),
        curation_label=int(row["curation_label"]),
        category=_text(row["category"]),
        analysis_window=(int(window[0]), int(window[1])),
        **provenance,
    )


def _reconcile_in(f: Any, movie_id: str) -> GenerationReconciliation | None:
    """Reconcile ``movie_id`` in an open store; ``None`` for a movie with one generation."""
    from tether.io import generations  # noqa: PLC0415
    from tether.io.schema import TABLE  # noqa: PLC0415

    records = generations.generations_in(f, movie_id)
    active = next(g for g in records if g.active)
    previous = next((g for g in records if g.generation == active.generation - 1), None)
    if previous is None:
        return None
    archived = generations.archived_generation(f, movie_id, previous.generation)
    old_rows = archived["molecules"][()] if archived is not None else ()
    rows = f["molecules"][TABLE][generations.molecule_rows(f, movie_id)]
    labels = f["labels"][TABLE][()]
    human = _human_rows(labels)
    reviews = generations.label_reviews_in(f)
    review_events = frozenset(e for e in reviews.values() if 0 <= e < len(labels))
    carried_keys = set(active.carried_keys)

    carried: list[ReconciledMolecule] = []
    new: list[ReconciledMolecule] = []
    active_keys: set[str] = set()
    for row in rows:
        key, mid = _text(row["molecule_key"]), _text(row["molecule_id"])
        active_keys.add(key)
        if key not in carried_keys:
            labeler, when = _labelled(labels, human.get(key, []), None, review_events)
            new.append(_molecule(NEW, row, labeler=labeler, labelled_utc=when))
            continue
        event = reviews.get(mid, generations.PENDING_LABEL_REVIEW)
        confirmed = 0 <= event < len(labels)
        labeler, when = _labelled(
            labels, human.get(key, []), event if confirmed else None, review_events
        )
        by, at = (
            (_text(labels[event]["labeler"]), _text(labels[event]["timestamp"]))
            if confirmed
            else ("", "")
        )
        carried.append(
            _molecule(
                CARRIED,
                row,
                labeler=labeler,
                labelled_utc=when,
                confirmed_by=by,
                confirmed_utc=at,
                confirmed=confirmed,
            )
        )
    lost = []
    for row in old_rows:
        key = _text(row["molecule_key"])
        if key in active_keys:
            continue
        labeler, when = _labelled(labels, human.get(key, []), None, review_events)
        lost.append(_molecule(LOST, row, labeler=labeler, labelled_utc=when))
    return GenerationReconciliation(
        movie_id=movie_id,
        generation=active.generation,
        previous=previous.generation,
        carried=tuple(carried),
        new=tuple(new),
        lost=tuple(lost),
    )


def reconcile_generation(
    project_path: str | PathLike[str], movie_id: str
) -> GenerationReconciliation | None:
    """Compare the active generation of ``movie_id`` with the one before it (a read).

    Returns ``None`` for a movie that has one generation only.

    Raises
    ------
    KeyError
        If ``movie_id`` is not a movie of the project.
    """
    import h5py  # noqa: PLC0415

    with h5py.File(Path(project_path), "r") as f:
        return _reconcile_in(f, movie_id)


def _reconcile_all_in(f: Any) -> tuple[GenerationReconciliation, ...]:
    from tether.io.schema import TABLE  # noqa: PLC0415

    movies = [_text(v) for v in f["movies"][TABLE]["movie_id"][()]]
    found = (_reconcile_in(f, movie_id) for movie_id in movies)
    return tuple(r for r in found if r is not None)


def reconcile_project(project_path: str | PathLike[str]) -> tuple[GenerationReconciliation, ...]:
    """Reconcile every movie of the project that has more than one generation (a read).

    One [`GenerationReconciliation`][] per such movie, in ``/movies`` order, all read from
    one open of the file, so the lists agree with each other.
    """
    import h5py  # noqa: PLC0415

    with h5py.File(Path(project_path), "r") as f:
        return _reconcile_all_in(f)


def reconciliation_counts(project_path: str | PathLike[str]) -> ReconciliationCounts:
    """Return the counts of [`reconcile_project`][] added up over the movies (a read)."""
    total = ReconciliationCounts()
    for reconciliation in reconcile_project(project_path):
        total = total + reconciliation.counts
    return total


def _check_timestamp(timestamp: str) -> None:
    try:
        parsed = datetime.fromisoformat(timestamp)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"timestamp must be an ISO-8601 string, got {timestamp!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp must include an explicit UTC offset, got {timestamp!r}")


def _reject_reasons(f: Any, labels: np.ndarray, keys: list[str]) -> dict[str, Any]:
    """Return the reason row of the current reject of each of ``keys`` rejected now.

    The join is the one of [`reject_record_of`][tether.project.labels.reject_record_of]:
    the latest human row of the key is a reject, and one ``"reject"`` reason row has its
    key, labeler and timestamp. A key whose join is missing or names more than one row
    has no entry, so no reason is attributed to it.
    """
    from tether.project.labels import (  # noqa: PLC0415
        LABEL_SOURCE_HUMAN,
        REJECT_EVENT,
        REJECT_LOG,
        CurationLabel,
    )

    wanted = set(keys)
    log = f["labels"].get(REJECT_LOG)
    if not wanted or log is None:
        return {}
    joins: dict[tuple[str, str, str], int] = {}
    current: dict[str, tuple[str, str, str]] = {}
    for row in labels:
        key = _text(row["molecule_key"])
        if key not in wanted or _text(row["source"]) != LABEL_SOURCE_HUMAN:
            continue
        join = (key, _text(row["labeler"]), _text(row["timestamp"]))
        if int(row["label_value"]) == CurationLabel.REJECT:
            joins[join] = joins.get(join, 0) + 1
            current[key] = join
        else:
            current.pop(key, None)
    found: dict[tuple[str, str, str], list[Any]] = {}
    for row in log[()]:
        join = (_text(row["molecule_key"]), _text(row["labeler"]), _text(row["timestamp"]))
        if join in joins and _text(row["event"]) == REJECT_EVENT:
            found.setdefault(join, []).append(row)
    return {
        key: found[join][0]
        for key, join in current.items()
        if joins[join] == 1 and len(found.get(join, ())) == 1
    }


def _append_reasons(f: Any, rows: np.ndarray, reasons: dict[str, Any]) -> None:
    """Give each confirmed reject in ``rows`` the reason of the reject it confirms.

    The copy carries the reason, note and value of ``reasons`` under the labeler and
    timestamp of the confirmation row, so the reason stays joined to the current reject.
    """
    from tether.project.labels import REJECT_LOG  # noqa: PLC0415

    copies = []
    for row in rows:
        reason = reasons.get(_text(row["molecule_key"]))
        if reason is None:
            continue
        copy = reason.copy()
        copy["labeler"] = row["labeler"]
        copy["timestamp"] = row["timestamp"]
        copy["condition_id"] = row["condition_id"]
        copies.append(copy)
    if not copies:
        return
    log = f["labels"][REJECT_LOG]
    start = log.shape[0]
    log.resize((start + len(copies),))
    log[start:] = copies


def confirm_carried(
    project_path: str | PathLike[str],
    molecule_keys: Iterable[str],
    *,
    labeler: str | None = None,
    timestamp: str | None = None,
    source_file: str | None = None,
) -> ConfirmResult:
    """Confirm carried molecules: remove the mark *carried*, and record who and when.

    For each key that is a carried molecule waiting for confirmation, one row is added to
    ``/labels/table``: the key, ``labeler``, ``timestamp``, ``source_file``, source
    ``"human"``, the full human weight and the molecule's **current** label, so the label
    does not change. The row is linked to the molecule's current ``molecule_id`` in
    ``/generations/label_reviews``. A confirmed reject with a reason also gets a copy of
    that reason in ``/labels/reject_log``, under the new row's labeler and timestamp, so
    the reason stays readable. The earlier rows are not changed. A key confirmed
    before (by a confirmation, or by an accept or reject on this generation) is reported in
    ``already`` and gets no row. All the rows are written in one open of the file.

    The caller holds the right to write the project, as for
    [`set_curation_label`][tether.project.labels.set_curation_label];
    [`Project.confirm_carried`][tether.project.core.Project.confirm_carried] checks it.

    Parameters
    ----------
    project_path:
        The ``.tether`` project.
    molecule_keys:
        The keys to confirm, from the carried list of [`reconcile_generation`][].
    labeler:
        Who confirms; the OS login by default
        ([`default_labeler`][tether.project.labels.default_labeler]).
    timestamp:
        When, as an ISO-8601 time with a UTC offset; now by default.
    source_file:
        The ``source_file`` of the rows; the project file name by default.

    Raises
    ------
    KeyError
        If a key is not a carried molecule of an active generation. Nothing is written.
    ValueError
        If ``timestamp`` is not an ISO-8601 time with an offset. Nothing is written.
    """
    import h5py  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    from tether.io import generations  # noqa: PLC0415
    from tether.io.schema import LABELS_DTYPE, TABLE  # noqa: PLC0415
    from tether.project.labels import (  # noqa: PLC0415
        HUMAN_WEIGHT,
        LABEL_SOURCE_HUMAN,
        default_labeler,
    )

    path = Path(project_path)
    wanted = list(dict.fromkeys(molecule_keys))
    labeler = default_labeler() if labeler is None else labeler
    timestamp = datetime.now(UTC).isoformat() if timestamp is None else timestamp
    _check_timestamp(timestamp)
    source_file = path.name if source_file is None else source_file

    with h5py.File(path, "r+") as f:
        carried = {m.molecule_key: m for r in _reconcile_all_in(f) for m in r.carried}
        unknown = [key for key in wanted if key not in carried]
        if unknown:
            raise KeyError(f"not a carried molecule of {path.name}: {', '.join(unknown)}")
        todo = [carried[key] for key in wanted if carried[key].waiting]
        already = tuple(key for key in wanted if not carried[key].waiting)
        if not todo:
            return ConfirmResult((), already, labeler, timestamp)
        molecules = f["molecules"][TABLE]
        conditions = {
            _text(mid): _text(cond)
            for mid, cond in zip(
                molecules["molecule_id"][()], molecules["condition_id"][()], strict=True
            )
        }
        rows = np.zeros(len(todo), dtype=LABELS_DTYPE)
        for row, molecule in zip(rows, todo, strict=True):
            row["molecule_key"] = molecule.molecule_key
            row["labeler"] = labeler
            row["timestamp"] = timestamp
            row["source_file"] = source_file
            row["source"] = LABEL_SOURCE_HUMAN
            row["weight"] = HUMAN_WEIGHT
            row["label_value"] = molecule.curation_label
            row["condition_id"] = conditions[molecule.molecule_id]
        table = f["labels"][TABLE]
        reasons = _reject_reasons(f, table[()], [m.molecule_key for m in todo])
        start = table.shape[0]
        table.resize((start + len(rows),))
        table[start:] = rows
        _append_reasons(f, rows, reasons)
        # The review link is written last: until it is there, the molecule still waits.
        generations.append_label_reviews(f, {m.molecule_id: start + i for i, m in enumerate(todo)})
    return ConfirmResult(tuple(m.molecule_key for m in todo), already, labeler, timestamp)
