# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""A read-only summary of what one ``.tether`` project holds (PRD §4.2, §5.1; #655).

:func:`read_project_summary` is the one function behind ``tether info``. It answers the
questions a user asks after an extraction or a batch run — how many movies and molecules,
how far curation has gone, whether corrections ran, which idealizations exist, who holds
the lock — without the application and without a script. ``tether status`` (#896) is
meant to read a project through the same function.

The read is deliberately narrow:

* **Read-only, no lock.** The store is opened with ``h5py`` mode ``"r"`` and the sidecar
  ``<file>.lock`` is only read (:func:`tether.project.lock.read_lock`). Nothing here calls
  a writer or :func:`tether.project.lock.acquire`, so it works on a project another user
  holds (PRD §7.10 lets a non-owner browse read-only) and leaves both files byte-for-byte
  unchanged.
* **No trace arrays, no movie files.** Only the compound ``table`` rows, group attributes
  and dataset *shapes* are read; ``/traces``, ``/patches`` and the per-molecule model
  arrays are never loaded, and a movie's ``uri`` is reported, never opened or stat'ed —
  so a cloud placeholder movie is not downloaded. The one exception is opt-in:
  ``check_stale=True`` runs the idealization staleness recompute
  (:func:`tether.project.idealize.stale_molecule_keys`), which by its nature reads the
  trace layers each model was fit on. Without it every model's ``stale`` is ``None``
  ("not checked"), never ``0``.

:meth:`ProjectSummary.to_dict` is the documented, versioned JSON layout
(:data:`SUMMARY_FORMAT_VERSION`); ``docs/cli.md`` lists its keys.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from os import PathLike

__all__ = [
    "SUMMARY_FORMAT_VERSION",
    "ConditionSummary",
    "CorrectionSummary",
    "CurationCounts",
    "IdealizationSummary",
    "LockSummary",
    "MovieSummary",
    "ProjectSummary",
    "ProjectSummaryError",
    "read_project_summary",
]

#: Version of the :meth:`ProjectSummary.to_dict` layout. A key may be *added* without a
#: bump; renaming or removing one, or changing what a value means, bumps it.
SUMMARY_FORMAT_VERSION = 1

#: The ``/molecules/table`` fields the summary reads (never the coordinate/trace fields).
_MOLECULE_FIELDS = ("movie_id", "curation_label", "category", "correction_method", "condition_id")

#: The ``/conditions/table`` identity-key fields reported per condition.
_CONDITION_TEXT_FIELDS = (
    "construct_variant",
    "dye",
    "ligand",
    "ligand_concentration_unit",
    "buffer",
)
_CONDITION_NUMBER_FIELDS = ("ligand_concentration", "temperature_c", "laser_power")


class ProjectSummaryError(ValueError):
    """The path is not a readable ``.tether`` project this version of Tether understands."""


@dataclass(frozen=True)
class CurationCounts:
    """Accept / reject / undecided counts over a set of molecules (PRD §7.5)."""

    accepted: int = 0
    rejected: int = 0
    undecided: int = 0

    @property
    def total(self) -> int:
        """All molecules counted."""
        return self.accepted + self.rejected + self.undecided

    def to_dict(self) -> dict[str, int]:
        """The JSON form: the three counts."""
        return {"accepted": self.accepted, "rejected": self.rejected, "undecided": self.undecided}


@dataclass(frozen=True)
class MovieSummary:
    """One ``/movies`` row plus its molecules' curation counts."""

    movie_id: str
    name: str
    uri: str
    n_frames: int
    #: Seconds per frame, or ``None`` when the store records none (written as 0).
    frame_time_s: float | None
    n_molecules: int
    curation: CurationCounts

    def to_dict(self) -> dict[str, Any]:
        """The JSON form of the row."""
        return {
            "movie_id": self.movie_id,
            "name": self.name,
            "uri": self.uri,
            "n_frames": self.n_frames,
            "frame_time_s": self.frame_time_s,
            "n_molecules": self.n_molecules,
            "curation": self.curation.to_dict(),
        }


@dataclass(frozen=True)
class ConditionSummary:
    """One condition: referenced by molecules, materialized in ``/conditions``, or both."""

    condition_id: str
    #: Whether a ``/conditions`` row exists for the id (``sync_conditions`` has run).
    materialized: bool
    n_molecules: int
    #: The identity-key fields of the ``/conditions`` row (empty when not materialized).
    key: dict[str, str | float | None] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """The JSON form of the condition."""
        return {
            "condition_id": self.condition_id,
            "materialized": self.materialized,
            "n_molecules": self.n_molecules,
            "key": dict(self.key),
        }


@dataclass(frozen=True)
class CorrectionSummary:
    """Whether the correction pass ran, and the per-molecule method counts (PRD §7.2)."""

    #: ``True`` when ``/settings/correction`` exists (the pass stamps it each run).
    ran: bool
    #: ``/molecules.correction_method`` value -> molecule count; ``""`` is "no method".
    methods: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        """The JSON form."""
        return {"ran": self.ran, "methods": dict(self.methods)}


@dataclass(frozen=True)
class IdealizationSummary:
    """One model under ``/idealization`` (PRD §7.4), read from attributes and shapes."""

    name: str
    method: str
    n_states: int | None
    n_molecules: int
    #: Stale molecule count, or ``None`` when staleness was not checked.
    stale: int | None

    def to_dict(self) -> dict[str, Any]:
        """The JSON form."""
        return {
            "name": self.name,
            "method": self.method,
            "n_states": self.n_states,
            "n_molecules": self.n_molecules,
            "stale": self.stale,
        }


@dataclass(frozen=True)
class LockSummary:
    """The sidecar ``<file>.lock`` as read (PRD §5.4); never acquired or changed."""

    held: bool
    corrupt: bool = False
    host: str | None = None
    user: str | None = None
    pid: int | None = None
    acquired_utc: str | None = None
    age_s: float | None = None
    stale: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        """The JSON form."""
        return {
            "held": self.held,
            "corrupt": self.corrupt,
            "host": self.host,
            "user": self.user,
            "pid": self.pid,
            "acquired_utc": self.acquired_utc,
            "age_s": self.age_s,
            "stale": self.stale,
        }


@dataclass(frozen=True)
class ProjectSummary:
    """Everything :func:`read_project_summary` reports for one project."""

    path: str
    schema_version: int
    #: The Tether version stamped at creation (root ``app_version``), if any.
    written_by: str | None
    movies: list[MovieSummary]
    conditions: list[ConditionSummary]
    n_molecules: int
    curation: CurationCounts
    #: ``/molecules.category`` value -> count, the empty category excluded.
    categories: dict[str, int]
    uncategorized: int
    corrections: CorrectionSummary
    idealizations: list[IdealizationSummary]
    #: The entries under ``/models`` (the ranker model reference, PRD §5.1); empty if none.
    models: list[str]
    lock: LockSummary

    def to_dict(self) -> dict[str, Any]:
        """The documented JSON layout of one project (``docs/cli.md``)."""
        return {
            "path": self.path,
            "schema_version": self.schema_version,
            "written_by": self.written_by,
            "n_molecules": self.n_molecules,
            "movies": [m.to_dict() for m in self.movies],
            "conditions": [c.to_dict() for c in self.conditions],
            "curation": self.curation.to_dict(),
            "categories": dict(self.categories),
            "uncategorized": self.uncategorized,
            "corrections": self.corrections.to_dict(),
            "idealizations": [i.to_dict() for i in self.idealizations],
            "models": list(self.models),
            "lock": self.lock.to_dict(),
        }


# --- helpers -----------------------------------------------------------------


def _text(value: object) -> str:
    """Decode an ``h5py`` string scalar (``bytes``) or coerce any value to ``str``."""
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _finite(value: object) -> float | None:
    """A finite float, or ``None`` for NaN/inf (the store's "not set" marker)."""
    number = float(value)  # type: ignore[arg-type]
    return number if math.isfinite(number) else None


def _movie_name(uri: str) -> str:
    """The file name of a movie ``uri`` on either path convention, without touching it."""
    return uri.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


def _curation(labels: list[int]) -> CurationCounts:
    """Count the signed curation codes (:class:`tether.project.labels.CurationLabel`)."""
    from tether.project.labels import CurationLabel

    counts = Counter(labels)
    accepted = counts.get(int(CurationLabel.ACCEPT), 0)
    rejected = counts.get(int(CurationLabel.REJECT), 0)
    return CurationCounts(accepted, rejected, len(labels) - accepted - rejected)


def _read_lock(path: Path) -> LockSummary:
    """Read the sidecar lock without acquiring it; a corrupt file is reported, not raised."""
    from tether.project import lock

    try:
        info = lock.read_lock(path)
    except lock.CorruptLockError:
        return LockSummary(held=True, corrupt=True)
    if info is None:
        return LockSummary(held=False)
    return LockSummary(
        held=True,
        host=info.host,
        user=info.user,
        pid=info.pid,
        acquired_utc=info.timestamp,
        age_s=round(info.age_seconds(), 1),
        stale=info.is_stale(),
    )


def read_project_summary(path: str | PathLike[str], *, check_stale: bool = False) -> ProjectSummary:
    """Summarize the project at ``path`` read-only, taking no lock (#655).

    Raises :class:`ProjectSummaryError` when the file is missing, is not a ``.tether``
    project, or carries a schema newer than this version of Tether understands (the
    §5.4 guard, :func:`tether.io.schema.assert_is_compatible_project`).

    ``check_stale=True`` also counts each idealization's stale molecules — the one part
    of the summary that reads trace arrays (see the module docstring).
    """
    import h5py

    from tether.io.schema import TABLE, assert_is_compatible_project
    from tether.project.idealize import _WRITING_SUFFIX, IDEALIZATION_GROUP

    path = Path(path)
    if not path.is_file():
        raise ProjectSummaryError(f"{path}: no such file")
    try:
        schema_version = assert_is_compatible_project(path)
    except ValueError as exc:
        raise ProjectSummaryError(str(exc)) from exc

    try:
        with h5py.File(path, "r") as f:
            written_by = f.attrs.get("app_version")
            written_by = None if written_by is None else _text(written_by)

            molecules_ds = f["molecules"][TABLE]
            n_molecules = int(molecules_ds.shape[0])
            # Only the named fields are read: never the coordinate or window columns.
            columns = {name: list(molecules_ds.fields(name)[:]) for name in _MOLECULE_FIELDS}
            movie_ids = [_text(v) for v in columns["movie_id"]]
            labels = [int(v) for v in columns["curation_label"]]
            categories = [_text(v) for v in columns["category"]]
            methods = [_text(v) for v in columns["correction_method"]]
            condition_ids = [_text(v) for v in columns["condition_id"]]

            movies_table = f["movies"][TABLE][()]
            conditions_table = f["conditions"][TABLE][()]
            corrections_ran = "correction" in f["settings"]

            idealization_rows: list[tuple[str, str, int | None, int]] = []
            if IDEALIZATION_GROUP in f:
                group = f[IDEALIZATION_GROUP]
                for name in sorted(group):
                    if name.endswith(_WRITING_SUFFIX):
                        continue  # a staging group left by a crashed overwrite
                    model = group[name]
                    n_states = model.attrs.get("nstates")
                    keys = model.get("molecule_key")
                    idealization_rows.append(
                        (
                            name,
                            _text(model.attrs.get("type", "")),
                            None if n_states is None else int(n_states),
                            int(keys.shape[0]) if isinstance(keys, h5py.Dataset) else 0,
                        )
                    )
            models = sorted(f["models"]) if "models" in f else []
    except OSError as exc:
        raise ProjectSummaryError(f"{path}: could not be read ({exc})") from exc

    # --- movies, with each movie's own curation counts ------------------------
    labels_by_movie: dict[str, list[int]] = {}
    for movie_id, label in zip(movie_ids, labels, strict=True):
        labels_by_movie.setdefault(movie_id, []).append(label)
    movies: list[MovieSummary] = []
    for row in movies_table:
        movie_id = _text(row["movie_id"])
        uri = _text(row["uri"])
        frame_time = _finite(row["frame_time"])
        own = labels_by_movie.get(movie_id, [])
        movies.append(
            MovieSummary(
                movie_id=movie_id,
                name=_movie_name(uri) if uri else movie_id,
                uri=uri,
                n_frames=int(row["n_frames"]),
                frame_time_s=frame_time if frame_time is not None and frame_time > 0 else None,
                n_molecules=len(own),
                curation=_curation(own),
            )
        )

    # --- conditions: the materialized rows plus every id a molecule references --
    per_condition = Counter(cid for cid in condition_ids if cid)
    conditions: list[ConditionSummary] = []
    seen: set[str] = set()
    for row in conditions_table:
        cid = _text(row["condition_id"])
        seen.add(cid)
        key: dict[str, str | float | None] = {k: _text(row[k]) for k in _CONDITION_TEXT_FIELDS}
        key.update({k: _finite(row[k]) for k in _CONDITION_NUMBER_FIELDS})
        conditions.append(ConditionSummary(cid, True, per_condition.get(cid, 0), key))
    for cid in sorted(set(per_condition) - seen):
        conditions.append(ConditionSummary(cid, False, per_condition[cid]))

    category_counts = Counter(c for c in categories if c)
    idealizations: list[IdealizationSummary] = []
    for name, method, n_states, n_fit in idealization_rows:
        stale: int | None = None
        if check_stale:
            from tether.project.idealize import stale_molecule_keys

            stale = len(stale_molecule_keys(path, name))
        idealizations.append(IdealizationSummary(name, method, n_states, n_fit, stale))

    return ProjectSummary(
        path=str(path),
        schema_version=schema_version,
        written_by=written_by,
        movies=movies,
        conditions=conditions,
        n_molecules=n_molecules,
        curation=_curation(labels),
        categories=dict(sorted(category_counts.items())),
        uncategorized=n_molecules - sum(category_counts.values()),
        corrections=CorrectionSummary(
            ran=corrections_ran, methods=dict(sorted(Counter(methods).items()))
        ),
        idealizations=idealizations,
        models=models,
        lock=_read_lock(path),
    )
