# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The one selection argument of the population functions (PRD §7.7; ADR-0109).

Every population function of :mod:`tether.analysis` takes the molecules it pools through
one keyword, ``molecule_keys``. That keyword accepts three things:

- ``None`` — every molecule of the project (then the curation filter and, for a
  model-derived view, the freshness filter apply as before);
- an iterable of ``molecule_key`` strings — the selection the functions took before;
- a :class:`MoleculeSelection` — any object that can name its molecules *in one
  project*. A frozen version of an analysis set (#934) is the selection this protocol is
  for: the set spans several projects, so the version names, for the project at hand,
  the keys of its members that come from that project.

:func:`resolve_selection` turns each of the three into ``list[str] | None`` before the
function reads the store, so a selection never changes how a number is computed — only
which molecules enter it.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover - typing only
    from os import PathLike

    from tether.project.core import Project

    ProjectRef = Project | str | PathLike[str]

__all__ = ["MoleculeSelection", "Selection", "resolve_selection"]


@runtime_checkable
class MoleculeSelection(Protocol):
    """A selection that names its molecules in one project (ADR-0109).

    The protocol a frozen analysis-set version (#934) implements so it can be passed as
    the ``molecule_keys`` of any population function. :meth:`molecule_keys_in` gets the
    open :class:`~tether.project.core.Project` and returns the ``molecule_key`` values of
    the selection's members that belong to it — an empty collection when none do. It
    must not return ``None``: "every molecule" is said with ``molecule_keys=None``, never
    by a selection object.
    """

    def molecule_keys_in(self, project: Project) -> Iterable[str]:
        """The ``molecule_key`` values of this selection's members in ``project``."""
        ...


#: What ``molecule_keys`` accepts on every population function.
Selection = MoleculeSelection | Iterable[str] | None


def resolve_selection(selection: Selection, project: ProjectRef) -> list[str] | None:
    """Turn the ``molecule_keys`` argument into ``list[str] | None`` (ADR-0109).

    ``None`` stays ``None``. A :class:`MoleculeSelection` is asked for its keys in
    ``project`` (opened only in this case, when a path is given). Any other iterable is
    read into a list of strings once, so a one-shot iterator is not consumed twice.

    Raises
    ------
    TypeError
        ``selection`` is a bare ``str`` or ``bytes`` (it would select its characters), a
        :class:`MoleculeSelection` returns ``None`` or a bare string, or ``selection``
        is not iterable.
    """
    if selection is None:
        return None
    if isinstance(selection, MoleculeSelection):
        from tether.project.core import Project as _Project  # noqa: PLC0415

        proj = project if isinstance(project, _Project) else _Project.open(project)
        keys = selection.molecule_keys_in(proj)
        if keys is None or isinstance(keys, str | bytes):
            raise TypeError(
                f"{type(selection).__name__}.molecule_keys_in must return an iterable of "
                f"molecule_key strings, got {type(keys).__name__}"
            )
        return [str(k) for k in keys]
    if isinstance(selection, str | bytes):
        raise TypeError(
            "molecule_keys must be an iterable of molecule_key strings or a "
            f"MoleculeSelection, not a bare {type(selection).__name__} ({selection!r})"
        )
    if not isinstance(selection, Iterable):
        raise TypeError(
            "molecule_keys must be None, an iterable of molecule_key strings or a "
            f"MoleculeSelection, got {type(selection).__name__}"
        )
    return [str(k) for k in selection]
