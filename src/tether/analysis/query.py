# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Cross-movie condition query / filter (PRD §5.1, §7.7; FR-ANNOTATE).

A condition spans many movies across many days/files (PRD §5.1); this module
selects the molecules that make up a condition — or any slice of one — **across
all** those movies/files, filtered by the condition **key fields**, the per-trace
**category**, and per-molecule **tags**. It is the headless query seam behind the
M4 annotation/analysis views (the GUI condition browser is a later layer):
read-only, Qt-free, and — like the rest of :mod:`tether.analysis` — a standalone
function taking a project reference (not a :class:`~tether.project.core.Project`
method).

The result is inherently cross-file: each :class:`MoleculeMatch` carries its
``movie_id`` and ``source_filename``, and :class:`ConditionQueryResult` groups the
matched molecules by condition, by movie, and by source file — so "a query
aggregates the right molecule set across files" (§9 M4) is a direct read-off.

Filter semantics
----------------
Every supplied filter is **ANDed**. A filter is *inactive* when it is ``None`` or
empty (so ``query_molecules(project)`` returns the whole conditioned population and
``tags=[]`` is a no-op, never a match-nothing trap):

* ``condition_ids`` — keep only molecules already carrying one of these ids.
* ``key`` — keep only molecules whose **condition** matches the given
  :class:`~tether.io.filename.ConditionKey` field constraints (a *partial* mapping,
  e.g. ``{"ligand": "tRNA"}``). Resolving a molecule's key needs its ``/conditions``
  row, so key filtering only sees **materialized** conditions
  (:func:`tether.project.conditions.sync_conditions`); a molecule whose condition
  row is absent (a not-yet-synced or drifted id) is not key-matchable and is
  excluded by a ``key`` filter. An unknown field name is a hard error, never
  silently ignored (a silently-dropped constraint would return a wrong set).
* ``categories`` — keep only molecules whose editable per-trace ``category``
  (PRD §5.1) is one of these names.
* ``tags`` — keep only molecules carrying the requested tag(s); ``match_all_tags``
  chooses all-of (default) vs any-of. Molecule tags are the comma-joined
  ``/molecules.tags`` string (e.g. the low-confidence-registration tag, §7.1).

Molecules with **no condition** (empty ``condition_id``) are never returned: this
is a *condition* query, mirroring
:func:`tether.project.conditions.aggregate_molecules_by_condition`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from tether.io.filename import ConditionKey

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Iterable, Mapping
    from os import PathLike

    from tether.project.core import Project

    ProjectRef = Project | str | PathLike[str]

__all__ = ["ConditionQueryResult", "MoleculeMatch", "query_molecules"]

#: The identity key fields a ``key`` filter may constrain — exactly the
#: :class:`~tether.io.filename.ConditionKey` fields, so a typo raises instead of
#: silently dropping a constraint.
_KEY_FIELDS: frozenset[str] = frozenset(ConditionKey.__dataclass_fields__)


def _to_str(value: object) -> str:
    """Decode an h5py variable-length string cell (``bytes`` or ``str``)."""
    return value.decode() if isinstance(value, bytes) else str(value)


def _split_tags(raw: str) -> tuple[str, ...]:
    """Split a ``/molecules.tags`` cell into its tags.

    Extraction stores tags comma-joined (``",".join(...)``,
    :attr:`tether.imaging.calibrate.RegistrationMap.molecule_tags`); this is the
    inverse — each tag stripped, empties dropped.
    """
    return tuple(t for t in (part.strip() for part in raw.split(",")) if t)


def _key_matches(condition_key: ConditionKey, constraints: Mapping[str, object]) -> bool:
    """Whether ``condition_key`` satisfies every field constraint in ``constraints``.

    Only the fields present in ``constraints`` are compared (a *partial* key match);
    numeric fields compare by value, so ``600`` and ``600.0`` match and ``None``
    (an absent field) matches ``None``.
    """
    return all(getattr(condition_key, field) == expected for field, expected in constraints.items())


def _tags_match(molecule_tags: tuple[str, ...], requested: frozenset[str], *, all_of: bool) -> bool:
    """Whether a molecule's tag set satisfies the requested tags (all-of vs any-of)."""
    have = set(molecule_tags)
    return requested <= have if all_of else bool(requested & have)


def _as_filter_set(values: Iterable[str] | None) -> frozenset[str] | None:
    """Normalize an optional filter collection to a set — empty means *inactive*.

    Materializes the iterable **before** the emptiness test, so an empty *generator*
    (truthy even though it yields nothing) deactivates the filter exactly like an
    empty list/tuple/set does — rather than silently becoming a match-nothing filter
    (the never-silent contract; see the module docstring's filter semantics). Returns
    ``None`` for both ``None`` and an empty collection.

    A bare ``str``/``bytes`` is rejected with :class:`TypeError`: it *is* iterable, so
    ``tags="blink"`` would otherwise iterate characters and silently match nothing —
    the same never-silent trap the emptiness handling avoids.
    """
    if values is None:
        return None
    if isinstance(values, str | bytes):
        raise TypeError(
            f"filter values must be an iterable of strings, not a bare "
            f"{type(values).__name__} (wrap a single value in a list, e.g. [value])"
        )
    return frozenset(str(v) for v in values) or None


@dataclass(frozen=True)
class MoleculeMatch:
    """One molecule returned by :func:`query_molecules`, with its cross-file locus.

    Carries the condition it belongs to plus the movie / source file it was
    extracted from, so the caller can see the same condition span many movies.
    """

    #: The cross-file molecule identity (movie ``sha256`` + quantized position, §7.10).
    molecule_key: str
    #: The condition this molecule belongs to (never empty — unconditioned molecules
    #: are not returned).
    condition_id: str
    #: The movie this molecule was extracted from.
    movie_id: str
    #: The acquisition filename the molecule came from (the cross-file provenance).
    source_filename: str
    #: The molecule's editable per-trace category value (``""`` when unset, §5.1).
    category: str
    #: The molecule's tags, split from the comma-joined ``/molecules.tags`` cell.
    tags: tuple[str, ...]


@dataclass(frozen=True)
class ConditionQueryResult:
    """The molecules matching a :func:`query_molecules` call, with cross-file rollups.

    :attr:`matches` is in ``/molecules`` store order; the aggregations preserve
    first-seen order so the result is deterministic.
    """

    #: The matched molecules, in store order.
    matches: tuple[MoleculeMatch, ...]

    @property
    def n_matches(self) -> int:
        """How many molecules matched."""
        return len(self.matches)

    @property
    def molecule_keys(self) -> tuple[str, ...]:
        """The matched ``molecule_key`` values, in store order."""
        return tuple(m.molecule_key for m in self.matches)

    def _ordered_unique(self, attr: str) -> tuple[str, ...]:
        seen: set[str] = set()
        out: list[str] = []
        for m in self.matches:
            value = getattr(m, attr)
            if value not in seen:
                seen.add(value)
                out.append(value)
        return tuple(out)

    def _group_by(self, attr: str) -> dict[str, tuple[str, ...]]:
        out: dict[str, list[str]] = {}
        for m in self.matches:
            out.setdefault(getattr(m, attr), []).append(m.molecule_key)
        return {key: tuple(keys) for key, keys in out.items()}

    @property
    def condition_ids(self) -> tuple[str, ...]:
        """The distinct conditions spanned, in first-seen order."""
        return self._ordered_unique("condition_id")

    @property
    def movie_ids(self) -> tuple[str, ...]:
        """The distinct movies spanned, in first-seen order."""
        return self._ordered_unique("movie_id")

    @property
    def source_filenames(self) -> tuple[str, ...]:
        """The distinct source files spanned, in first-seen order."""
        return self._ordered_unique("source_filename")

    @property
    def n_conditions(self) -> int:
        """How many distinct conditions the matches span."""
        return len(self.condition_ids)

    @property
    def n_movies(self) -> int:
        """How many distinct movies the matches span."""
        return len(self.movie_ids)

    @property
    def n_files(self) -> int:
        """How many distinct source files the matches span (the cross-file breadth)."""
        return len(self.source_filenames)

    def by_condition(self) -> dict[str, tuple[str, ...]]:
        """``condition_id`` → its matched ``molecule_key`` list (the §5.1 aggregation)."""
        return self._group_by("condition_id")

    def by_movie(self) -> dict[str, tuple[str, ...]]:
        """``movie_id`` → its matched ``molecule_key`` list."""
        return self._group_by("movie_id")

    def by_source_file(self) -> dict[str, tuple[str, ...]]:
        """``source_filename`` → its matched ``molecule_key`` list (across movies)."""
        return self._group_by("source_filename")


def query_molecules(
    project: ProjectRef,
    *,
    condition_ids: Iterable[str] | None = None,
    key: Mapping[str, object] | None = None,
    categories: Iterable[str] | None = None,
    tags: Iterable[str] | None = None,
    match_all_tags: bool = True,
) -> ConditionQueryResult:
    """Query/filter a condition's molecules across its movies (PRD §5.1, §7.7).

    Selects ``/molecules`` rows matching the ANDed filters (see the module docstring
    for the full semantics) and returns them as a :class:`ConditionQueryResult` whose
    rollups make the cross-file aggregation explicit. Read-only; ``project`` may be a
    :class:`~tether.project.core.Project` or a path to one.

    Parameters
    ----------
    project:
        The ``.tether`` project (handle or path) to query.
    condition_ids:
        Keep only molecules on one of these condition ids (inactive if ``None``/empty).
    key:
        Partial :class:`~tether.io.filename.ConditionKey` field constraints selecting
        which *conditions* to include (inactive if ``None``/empty). Needs the
        ``/conditions`` rows materialized first
        (:func:`~tether.project.conditions.sync_conditions`).
    categories:
        Keep only molecules whose ``category`` is one of these (inactive if ``None``/empty).
    tags:
        Keep only molecules carrying these tag(s) (inactive if ``None``/empty).
    match_all_tags:
        With ``tags``, require **all** requested tags (default) or **any** one.

    Returns
    -------
    ConditionQueryResult
        The matched molecules (store order) plus by-condition / by-movie / by-file rollups.

    Raises
    ------
    ValueError
        If ``key`` names a field that is not a :class:`ConditionKey` field.
    TypeError
        If ``condition_ids``, ``categories``, or ``tags`` is a bare ``str``/``bytes``
        instead of an iterable of strings (wrap a single value in a list).
    """
    from tether.imaging.extract import read_molecules  # noqa: PLC0415
    from tether.project.conditions import read_condition_keys  # noqa: PLC0415
    from tether.project.core import Project as _Project  # noqa: PLC0415

    proj = project if isinstance(project, _Project) else _Project.open(project)
    path = proj.path

    id_filter = _as_filter_set(condition_ids)
    category_filter = _as_filter_set(categories)
    tag_filter = _as_filter_set(tags)

    key_condition_ids: set[str] | None = None
    # `key` is a Mapping (always sized), so plain truthiness treats `{}` as inactive.
    if key:
        unknown = set(key) - _KEY_FIELDS
        if unknown:
            raise ValueError(
                f"unknown condition key field(s) {sorted(unknown)}; "
                f"valid fields are {sorted(_KEY_FIELDS)}"
            )
        condition_keys = read_condition_keys(path)
        key_condition_ids = {cid for cid, ckey in condition_keys.items() if _key_matches(ckey, key)}

    molecules = read_molecules(path)
    matches: list[MoleculeMatch] = []
    for i in range(molecules.shape[0]):
        condition_id = _to_str(molecules["condition_id"][i])
        if not condition_id:  # a condition query never returns an unconditioned molecule
            continue
        if id_filter is not None and condition_id not in id_filter:
            continue
        if key_condition_ids is not None and condition_id not in key_condition_ids:
            continue
        category = _to_str(molecules["category"][i])
        if category_filter is not None and category not in category_filter:
            continue
        molecule_tags = _split_tags(_to_str(molecules["tags"][i]))
        if tag_filter is not None and not _tags_match(
            molecule_tags, tag_filter, all_of=match_all_tags
        ):
            continue
        matches.append(
            MoleculeMatch(
                molecule_key=_to_str(molecules["molecule_key"][i]),
                condition_id=condition_id,
                movie_id=_to_str(molecules["movie_id"][i]),
                source_filename=_to_str(molecules["source_filename"][i]),
                category=category,
                tags=molecule_tags,
            )
        )
    return ConditionQueryResult(matches=tuple(matches))
