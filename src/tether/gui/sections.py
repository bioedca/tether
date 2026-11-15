# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The section registry: a Qt-free description of each section of the shell (#340).

The unified shell (M14) shows one *section* at a time — for example Prepare, Curate and
correct, Analyze and Deposit (#897). This module describes a section as data, and the shell
builds the widgets from that data. One :class:`SectionSpec` holds:

* an ``id`` and a ``title``;
* an ``order``: the position of the section in the shell and in the guided mode;
* the factory of the central widget;
* the docks (:class:`DockSpec`), each with its :class:`DockArea`;
* the menus (:class:`MenuSpec`);
* the keymap scope that is active while the section shows;
* ``writes_project``: whether the section may write to the open project. The publish path
  (#352) reads this flag from :meth:`SectionRegistry.may_write` and from no other place.

A :class:`SectionRegistry` holds the sections. It refuses a second section with the same
``id`` or the same ``order``, and it always gives the sections in ascending ``order``, so the
order does not depend on the order of registration.

**No Qt.** This module imports only the standard library, so the registry and its tests run
without a display and without a ``QApplication``. A factory receives a
:class:`SectionHostLike`, the part of the shell that a section drives. The shell satisfies that
:class:`~typing.Protocol` by its structure, and a test satisfies it with a small fake object, as
:class:`~tether.gui.roundtrip.MoviePanelLike` does for the movie panel. :class:`DockArea` gives
the name of each Qt dock area as text (:attr:`DockArea.qt_name`); the shell converts that name
to the Qt value, so every area takes the same code path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator

    from tether.gui.curation import Keymap
    from tether.project import Project

__all__ = [
    "DockArea",
    "DockSpec",
    "DuplicateSectionError",
    "MenuSpec",
    "SectionHostLike",
    "SectionRegistry",
    "SectionSpec",
]

# A section, dock or menu id: lower case, starts with a letter, then letters, digits, "_" or "-".
_ID = re.compile(r"[a-z][a-z0-9_-]*")


def _check_id(value: object, what: str) -> None:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise ValueError(f"{what} must match {_ID.pattern!r}, got {value!r}")


def _check_title(value: object, what: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{what} must be a non-empty str, got {value!r}")


def _check_unique(ids: Iterable[str], what: str, owner: str) -> None:
    seen: set[str] = set()
    for item in ids:
        if item in seen:
            raise ValueError(f"section {owner!r} declares the {what} id {item!r} twice")
        seen.add(item)


@runtime_checkable
class SectionHostLike(Protocol):
    """The part of :class:`~tether.gui.shell.TetherShell` that a section drives.

    A factory of a :class:`SectionSpec` or a :class:`DockSpec` receives the host. The real
    shell satisfies this protocol by its structure; tests use a small fake object.
    """

    @property
    def window(self) -> object:
        """The main window: the parent of the widgets that the section makes."""
        ...

    @property
    def curation_project(self) -> Project | None:
        """The writable open project, or ``None`` when no write is allowed."""
        ...

    @property
    def read_only_reason(self) -> str | None:
        """Why the open project is browse-only, or ``None`` when writes are not blocked."""
        ...

    @property
    def is_idealizing(self) -> bool:
        """``True`` while a background idealization runs; the shell then refuses writes."""
        ...

    @property
    def keymap(self) -> Keymap:
        """The keymap of the shell; a section with a keymap scope binds its keys here."""
        ...


class DockArea(StrEnum):
    """The side of the main window where a dock attaches.

    The value is the name a dock spec uses; :attr:`qt_name` is the name of the member of
    ``Qt.DockWidgetArea``. The shell looks up the Qt value by that name, so no area needs
    its own code.
    """

    LEFT = "left"
    RIGHT = "right"
    TOP = "top"
    BOTTOM = "bottom"

    @property
    def qt_name(self) -> str:
        """The ``Qt.DockWidgetArea`` member name, for example ``"LeftDockWidgetArea"``."""
        return f"{self.value.capitalize()}DockWidgetArea"


@dataclass(frozen=True, kw_only=True)
class DockSpec:
    """One dock of a section: its id, its title, its :class:`DockArea` and its factory.

    ``factory`` receives the :class:`SectionHostLike` and returns the widget of the dock.
    """

    id: str
    title: str
    area: DockArea
    factory: Callable[[SectionHostLike], object]

    def __post_init__(self) -> None:
        _check_id(self.id, "dock id")
        _check_title(self.title, "dock title")
        if not isinstance(self.area, DockArea):
            raise ValueError(f"dock {self.id!r}: area must be a DockArea, got {self.area!r}")
        if not callable(self.factory):
            raise ValueError(f"dock {self.id!r}: factory must be callable")


@dataclass(frozen=True, kw_only=True)
class MenuSpec:
    """One menu of a section on the menu bar.

    ``populate`` receives the :class:`SectionHostLike` and the new menu, and adds the actions.
    ``None`` makes an empty menu.
    """

    id: str
    title: str
    populate: Callable[[SectionHostLike, object], None] | None = None

    def __post_init__(self) -> None:
        _check_id(self.id, "menu id")
        _check_title(self.title, "menu title")
        if self.populate is not None and not callable(self.populate):
            raise ValueError(f"menu {self.id!r}: populate must be callable or None")


@dataclass(frozen=True, kw_only=True)
class SectionSpec:
    """The description of one section of the shell.

    Parameters
    ----------
    id
        The stable id of the section, for example ``"curate"``.
    title
        The title the user sees, for example ``"Curate and correct"``.
    order
        The position of the section in the shell and in the guided mode; lower comes first.
    central
        Receives the :class:`SectionHostLike` and returns the central widget of the section.
    writes_project
        ``True`` when the section may write to the open project. There is no default: each
        section must declare it.
    docks, menus
        The docks and the menus of the section, in the order the shell adds them. The ids must
        be unique in the section.
    keymap_scope
        The name of the keymap scope that is active while the section shows, or ``None`` for a
        section with no keys of its own.
    """

    id: str
    title: str
    order: int
    central: Callable[[SectionHostLike], object]
    writes_project: bool
    docks: tuple[DockSpec, ...] = field(default=())
    menus: tuple[MenuSpec, ...] = field(default=())
    keymap_scope: str | None = None

    def __post_init__(self) -> None:
        _check_id(self.id, "section id")
        _check_title(self.title, "section title")
        if isinstance(self.order, bool) or not isinstance(self.order, int):
            raise ValueError(f"section {self.id!r}: order must be an int, got {self.order!r}")
        if not callable(self.central):
            raise ValueError(f"section {self.id!r}: central must be callable")
        if not isinstance(self.writes_project, bool):
            raise ValueError(f"section {self.id!r}: writes_project must be a bool")
        object.__setattr__(self, "docks", tuple(self.docks))
        object.__setattr__(self, "menus", tuple(self.menus))
        for dock in self.docks:
            if not isinstance(dock, DockSpec):
                raise ValueError(f"section {self.id!r}: docks must be DockSpec, got {dock!r}")
        for menu in self.menus:
            if not isinstance(menu, MenuSpec):
                raise ValueError(f"section {self.id!r}: menus must be MenuSpec, got {menu!r}")
        _check_unique((dock.id for dock in self.docks), "dock", self.id)
        _check_unique((menu.id for menu in self.menus), "menu", self.id)
        if self.keymap_scope is not None:
            _check_id(self.keymap_scope, f"section {self.id!r}: keymap_scope")


class DuplicateSectionError(ValueError):
    """A section with the same id or the same order is already in the registry."""


class SectionRegistry:
    """The sections of the shell, always in ascending :attr:`SectionSpec.order`.

    Iteration, :attr:`ids` and :meth:`sections` give the same order each time, whatever
    the order of :meth:`register` calls.
    """

    def __init__(self, sections: Iterable[SectionSpec] = ()) -> None:
        self._by_id: dict[str, SectionSpec] = {}
        for spec in sections:
            self.register(spec)

    def register(self, spec: SectionSpec) -> None:
        """Add ``spec``; raise :class:`DuplicateSectionError` for a used id or order."""
        if not isinstance(spec, SectionSpec):
            raise TypeError(f"register needs a SectionSpec, got {spec!r}")
        if spec.id in self._by_id:
            raise DuplicateSectionError(f"a section with id {spec.id!r} is already registered")
        for other in self._by_id.values():
            if other.order == spec.order:
                raise DuplicateSectionError(
                    f"sections {other.id!r} and {spec.id!r} both have order {spec.order}"
                )
        self._by_id[spec.id] = spec

    def sections(self) -> tuple[SectionSpec, ...]:
        """All sections, in ascending order."""
        return tuple(sorted(self._by_id.values(), key=lambda spec: spec.order))

    @property
    def ids(self) -> tuple[str, ...]:
        """The section ids, in ascending order."""
        return tuple(spec.id for spec in self.sections())

    def get(self, section_id: str) -> SectionSpec:
        """The section with ``section_id``; raise :class:`KeyError` when there is none."""
        try:
            return self._by_id[section_id]
        except KeyError:
            raise KeyError(f"no section with id {section_id!r}") from None

    def may_write(self, section_id: str) -> bool:
        """``True`` when the section may write to the open project (#352 reads this)."""
        return self.get(section_id).writes_project

    def __iter__(self) -> Iterator[SectionSpec]:
        return iter(self.sections())

    def __len__(self) -> int:
        return len(self._by_id)

    def __contains__(self, section_id: object) -> bool:
        return section_id in self._by_id
