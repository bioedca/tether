# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""A saved selection of one project's molecules, the series of the plot builder (ADR-0124).

FR-PLOT (PRD §7.7) composes a plot from *series*, and a series names its molecules through
the one selection argument of the population functions
([`MoleculeSelection`][tether.analysis.selection.MoleculeSelection], ADR-0109). A frozen
version of an analysis set (#934) is the series that spans projects; this module is the one
that lives in a single project, which the builder can make today:

- [`SavedSelection`][] is ``(name, project_path, molecule_keys, filter)``. Its membership is
  **frozen when it is made**, as an analysis-set version is: a later edit to the project
  never changes which molecules a saved selection plots. The
  [`SelectionFilter`][] that produced it is kept beside the keys, so a composition can say
  where its molecules came from and offer to run the same filter again as a new selection.
- [`select_molecules`][] runs [`query_molecules`][tether.analysis.query.query_molecules]
  once and freezes its result.
- [`preset_selections`][] is the one-click preset: one selection for each condition, each
  movie or each source file, from the grouping
  [`ConditionQueryResult`][tether.analysis.query.ConditionQueryResult] already does.

Everything here is Qt-free and serializes to JSON, so a composition survives a restart.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tether.analysis.query import query_molecules

if TYPE_CHECKING:  # pragma: no cover - typing only
    from tether.project.core import Project

    ProjectRef = Project | str | os.PathLike[str]

__all__ = [
    "PRESET_GROUPINGS",
    "SAVED_SELECTION_VERSION",
    "SavedSelection",
    "SelectionFilter",
    "preset_selections",
    "select_molecules",
]

#: The format version of [`SavedSelection.to_dict`][].
SAVED_SELECTION_VERSION = 1

#: The one-click presets, each backed by one grouping of the query result.
PRESET_GROUPINGS = ("condition", "movie", "source_file")

_JSON_SCALARS = (str, int, float, bool, type(None))


def _strings(values: Iterable[str] | None, what: str) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, str | bytes):
        raise TypeError(
            f"{what} must be an iterable of strings, not a bare {type(values).__name__}"
        )
    return tuple(sorted({str(v) for v in values}))


def _project_path(project: ProjectRef) -> str:
    raw = getattr(project, "path", project)
    return os.fspath(Path(raw).resolve())


@dataclass(frozen=True)
class SelectionFilter:
    """The filter that produced a [`SavedSelection`][]: its provenance (ADR-0124).

    The fields are the arguments of [`query_molecules`][tether.analysis.query.query_molecules]
    (an empty one is inactive, as there), plus ``group``: the ``(grouping, value)`` a preset
    split the result by, or ``None``. Collections are stored sorted, so one filter has one
    form.
    """

    condition_ids: tuple[str, ...] = ()
    key: tuple[tuple[str, Any], ...] = ()
    categories: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    match_all_tags: bool = True
    group: tuple[str, str] | None = None

    @classmethod
    def of(
        cls,
        *,
        condition_ids: Iterable[str] | None = None,
        key: Mapping[str, object] | None = None,
        categories: Iterable[str] | None = None,
        tags: Iterable[str] | None = None,
        match_all_tags: bool = True,
    ) -> SelectionFilter:
        """Normalize the [`query_molecules`][tether.analysis.query.query_molecules] arguments.

        Raises
        ------
        TypeError
            A collection is a bare string, or a ``key`` value is not a JSON scalar (it
            could not be saved).
        """
        pairs = tuple(sorted((str(k), v) for k, v in (key or {}).items()))
        for field, value in pairs:
            if not isinstance(value, _JSON_SCALARS):
                raise TypeError(f"key[{field!r}] must be a str, number, bool or None to be saved")
        return cls(
            condition_ids=_strings(condition_ids, "condition_ids"),
            key=pairs,
            categories=_strings(categories, "categories"),
            tags=_strings(tags, "tags"),
            match_all_tags=bool(match_all_tags),
        )

    def query_kwargs(self) -> dict[str, Any]:
        """Return the keyword arguments that run this filter through ``query_molecules``."""
        return {
            "condition_ids": list(self.condition_ids) or None,
            "key": dict(self.key) or None,
            "categories": list(self.categories) or None,
            "tags": list(self.tags) or None,
            "match_all_tags": self.match_all_tags,
        }

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-ready form; [`from_dict`][] reads it back to an equal filter."""
        return {
            "condition_ids": list(self.condition_ids),
            "key": dict(self.key),
            "categories": list(self.categories),
            "tags": list(self.tags),
            "match_all_tags": self.match_all_tags,
            "group": list(self.group) if self.group is not None else None,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SelectionFilter:
        """Read the form [`to_dict`][] wrote."""
        base = cls.of(
            condition_ids=data.get("condition_ids") or (),
            key=data.get("key") or {},
            categories=data.get("categories") or (),
            tags=data.get("tags") or (),
            match_all_tags=data.get("match_all_tags", True),
        )
        group = data.get("group")
        if group is None:
            return base
        grouping, value = (str(g) for g in group)
        if grouping not in PRESET_GROUPINGS:
            raise ValueError(
                f"unknown preset grouping {grouping!r}; expected one of {PRESET_GROUPINGS}"
            )
        return replace(base, group=(grouping, value))


@dataclass(frozen=True)
class SavedSelection:
    """A named, frozen selection of one project's molecules (ADR-0124).

    Implements [`MoleculeSelection`][tether.analysis.selection.MoleculeSelection], so it is
    passed as the ``molecule_keys`` of any population function. In its own project it names
    its frozen keys; in any other project it names none, so pooling across projects is
    never silent — that is the analysis set's job (#934).
    """

    name: str
    project_path: str
    molecule_keys: tuple[str, ...]
    filter: SelectionFilter = SelectionFilter()

    def __post_init__(self) -> None:
        """Check the name and store the keys as a tuple of strings."""
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("a saved selection needs a non-empty name")
        if isinstance(self.molecule_keys, str | bytes):
            raise TypeError("molecule_keys must be an iterable of strings, not a bare string")
        object.__setattr__(self, "molecule_keys", tuple(str(k) for k in self.molecule_keys))

    @property
    def n_molecules(self) -> int:
        """How many molecules the selection holds (the N its series shows)."""
        return len(self.molecule_keys)

    def is_in(self, project: ProjectRef) -> bool:
        """Whether ``project`` is the project this selection was made in."""
        mine, theirs = Path(self.project_path), Path(_project_path(project))
        try:
            return os.path.samefile(mine, theirs)
        except OSError:
            return os.path.normcase(os.fspath(mine)) == os.path.normcase(os.fspath(theirs))

    def molecule_keys_in(self, project: Project) -> tuple[str, ...]:
        """Return the frozen keys in this selection's own project, and none in any other."""
        return self.molecule_keys if self.is_in(project) else ()

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-ready form; [`from_dict`][] reads it back to an equal selection."""
        return {
            "version": SAVED_SELECTION_VERSION,
            "name": self.name,
            "project_path": self.project_path,
            "molecule_keys": list(self.molecule_keys),
            "filter": self.filter.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SavedSelection:
        """Read the form [`to_dict`][] wrote.

        Raises
        ------
        ValueError
            The form is of another version, or a field is missing.
        """
        version = data.get("version")
        if version != SAVED_SELECTION_VERSION:
            raise ValueError(
                f"saved selection version {version!r} is not supported "
                f"(this Tether reads version {SAVED_SELECTION_VERSION})"
            )
        try:
            return cls(
                name=data["name"],
                project_path=str(data["project_path"]),
                molecule_keys=tuple(data["molecule_keys"]),
                filter=SelectionFilter.from_dict(data.get("filter") or {}),
            )
        except KeyError as exc:
            raise ValueError(f"saved selection is missing {exc.args[0]!r}") from None


def select_molecules(
    project: ProjectRef,
    *,
    name: str,
    condition_ids: Iterable[str] | None = None,
    key: Mapping[str, object] | None = None,
    categories: Iterable[str] | None = None,
    tags: Iterable[str] | None = None,
    match_all_tags: bool = True,
) -> SavedSelection:
    """Query ``project`` once and freeze the result as a named [`SavedSelection`][]."""
    flt = SelectionFilter.of(
        condition_ids=condition_ids,
        key=key,
        categories=categories,
        tags=tags,
        match_all_tags=match_all_tags,
    )
    result = query_molecules(project, **flt.query_kwargs())
    return SavedSelection(name, _project_path(project), result.molecule_keys, flt)


def preset_selections(
    project: ProjectRef,
    by: str,
    *,
    condition_ids: Iterable[str] | None = None,
    key: Mapping[str, object] | None = None,
    categories: Iterable[str] | None = None,
    tags: Iterable[str] | None = None,
    match_all_tags: bool = True,
) -> tuple[SavedSelection, ...]:
    """One selection per condition, movie or source file of a query (the one-click presets).

    ``by`` is one of [`PRESET_GROUPINGS`][]. The grouping is the query result's own
    (``by_condition``, ``by_movie``, ``by_source_file``), in first-seen order; each selection
    is named by its group value and records it in its filter's ``group``.

    Raises
    ------
    ValueError
        ``by`` is not a preset grouping.
    """
    if by not in PRESET_GROUPINGS:
        raise ValueError(f"unknown preset grouping {by!r}; expected one of {PRESET_GROUPINGS}")
    flt = SelectionFilter.of(
        condition_ids=condition_ids,
        key=key,
        categories=categories,
        tags=tags,
        match_all_tags=match_all_tags,
    )
    result = query_molecules(project, **flt.query_kwargs())
    groups = {
        "condition": result.by_condition,
        "movie": result.by_movie,
        "source_file": result.by_source_file,
    }[by]()
    path = _project_path(project)
    return tuple(
        SavedSelection(value, path, keys, replace(flt, group=(by, value)))
        for value, keys in groups.items()
    )
