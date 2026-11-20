# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Channel layouts: where the donor and the acceptor channel are in a frame (#908).

A dual-view camera frame holds the two FRET channels side by side, one above the
other, or at two places that the optics choose. A [`ChannelLayout`][] records
which, and names the donor and the acceptor channel. It is the user's statement of
the layout; [`ChannelLayout.geometries`][] turns it, for one frame size, into the
two per-channel [`ChannelGeometry`][tether.imaging.split.ChannelGeometry] crops that
[`split_channels`][tether.imaging.split.split_channels] applies (PRD Appendix E stage 1). The
resolved crops are what ``/movies/table`` stores in ``donor_crop`` /
``acceptor_crop``; the layout record itself is stored next to it as additive data,
``/movies/layouts/<movie_id>`` ([`write_layout_record`][]).

Three kinds ([`LAYOUT_KINDS`][]):

* ``side-by-side`` -- the frame is cut into a left and a right half; ``donor`` is
  ``left`` (default) or ``right``. Both halves are ``width // 2`` wide, so an odd
  trailing column is dropped. **This is the default layout, and it is the split
  Tether used before #908** (``ExtractOptions.donor_side``).
* ``top-bottom`` -- the frame is cut into a top and a bottom half; ``donor`` is
  ``top`` (default) or ``bottom``. Both halves are ``height // 2`` tall, so an odd
  trailing row is dropped. The halves are stacked, not rotated.
* ``rectangles`` -- one free rectangle for each channel, ``donor_rect`` and
  ``acceptor_rect``, each ``[y1, x1, y2, x2]`` with 1-based inclusive bounds (the
  crop convention of [`tether.imaging.split`][]).

Validation. The two channels must have the same size, because the registration
prealign and colocalization compare the two channel images pixel for pixel. A
``rectangles`` layout whose rectangles differ in size is refused when it is made
(with a channel recipe, the sizes after the recipes are compared instead); a
rectangle outside the frame, or a frame too small to cut in two, is refused by
[`ChannelLayout.geometries`][] with [`ChannelLayoutError`][]. Rectangles that
overlap are allowed, because some optical layouts share pixels at an edge, but they
give a [`ChannelOverlapWarning`][]: the overlapping pixels feed both channels.

Each channel can also carry a [`ChannelRecipe`][tether.imaging.recipe.ChannelRecipe]
(#910): rotate, flip, translate and crop steps that run on the channel's cut, in
order. [`ChannelLayout.geometries`][] folds the cut and the recipe into one
rotate -> flip -> crop geometry, and refuses a recipe that leaves the two channels
at different sizes. Without a recipe a layout gives identity rotation and flip. A
layout with two movie files (#909) is out of scope here.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Any

from tether.imaging.recipe import ChannelRecipe, ChannelRecipeError
from tether.imaging.split import ChannelGeometry

__all__ = [
    "DEFAULT_CHANNEL_LAYOUT",
    "LAYOUT_KINDS",
    "LAYOUT_RECTANGLES",
    "LAYOUT_SIDE_BY_SIDE",
    "LAYOUT_TOP_BOTTOM",
    "LAYOUTS_GROUP",
    "ChannelLayout",
    "ChannelLayoutError",
    "ChannelOverlapWarning",
    "check_layout_slot",
    "read_layout_record",
    "write_layout_record",
]

#: Two halves, left and right (the default, and the split used before #908).
LAYOUT_SIDE_BY_SIDE = "side-by-side"
#: Two halves, top and bottom (stacked, not rotated).
LAYOUT_TOP_BOTTOM = "top-bottom"
#: One free rectangle for each channel.
LAYOUT_RECTANGLES = "rectangles"

#: Every layout kind, in the order the docs list them.
LAYOUT_KINDS: tuple[str, ...] = (LAYOUT_SIDE_BY_SIDE, LAYOUT_TOP_BOTTOM, LAYOUT_RECTANGLES)

#: For each half layout: the donor positions it accepts, the first being the default,
#: and the acceptor position each donor position implies.
_HALF_POSITIONS: dict[str, dict[str, str]] = {
    LAYOUT_SIDE_BY_SIDE: {"left": "right", "right": "left"},
    LAYOUT_TOP_BOTTOM: {"top": "bottom", "bottom": "top"},
}

#: Version of the ``/movies/layouts/<movie_id>`` attribute set (`write_layout_record`).
#: Version 2 adds the channel recipes (#910); a record without recipes stays version 1,
#: so a build from before #910 still reads it.
_RECORD_VERSION = 2

#: The ``/movies`` child group that holds one layout record per movie.
LAYOUTS_GROUP = "layouts"


class ChannelLayoutError(ValueError):
    """A channel layout that cannot split a frame (bad values, sizes or bounds)."""


class ChannelOverlapWarning(UserWarning):
    """The donor and the acceptor rectangle share pixels; those pixels feed both channels."""


def _rect(value: Any, name: str) -> tuple[int, int, int, int]:
    """Validate one ``[y1, x1, y2, x2]`` rectangle (1-based inclusive) and return a tuple."""
    try:
        items = list(value)
    except TypeError:
        raise ChannelLayoutError(f"{name} must be [y1, x1, y2, x2], got {value!r}") from None
    if len(items) != 4:
        raise ChannelLayoutError(f"{name} must have 4 values [y1, x1, y2, x2], got {value!r}")
    out: list[int] = []
    for item in items:
        # bool is an int subclass; a True/False bound is a caller mistake, not a pixel.
        try:
            whole = not isinstance(item, bool) and float(item).is_integer()
        except (TypeError, ValueError):
            whole = False
        if not whole:
            raise ChannelLayoutError(f"{name} values must be integers, got {value!r}")
        out.append(int(item))
    y1, x1, y2, x2 = out
    if y1 < 1 or x1 < 1 or y2 < y1 or x2 < x1:
        raise ChannelLayoutError(
            f"{name} {out} must be 1-based with y2 >= y1 and x2 >= x1 (inclusive bounds)"
        )
    return y1, x1, y2, x2


def _size(rect: tuple[int, int, int, int]) -> tuple[int, int]:
    """``(height, width)`` of a 1-based inclusive rectangle."""
    y1, x1, y2, x2 = rect
    return y2 - y1 + 1, x2 - x1 + 1


def _overlaps(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    """Whether two 1-based inclusive rectangles share at least one pixel."""
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


@dataclass(frozen=True)
class ChannelLayout:
    """Where the donor and the acceptor channel are in a frame (#908).

    Parameters
    ----------
    kind:
        One of [`LAYOUT_KINDS`][]: ``side-by-side`` (default), ``top-bottom`` or
        ``rectangles``.
    donor:
        For a half layout, the donor half: ``left``/``right`` (``side-by-side``,
        default ``left``) or ``top``/``bottom`` (``top-bottom``, default ``top``).
        The acceptor is the other half ([`acceptor`][]). ``None`` takes the
        default. A ``rectangles`` layout names its channels by the two rectangles
        and must leave this ``None``.
    donor_rect, acceptor_rect:
        For ``rectangles`` only (both required there, refused otherwise): the donor
        and the acceptor rectangle, ``[y1, x1, y2, x2]`` with 1-based inclusive
        bounds. They must have the same size, unless a channel has a recipe; then
        the two channel images after the recipes must be.
    donor_recipe, acceptor_recipe:
        Optional [`ChannelRecipe`][tether.imaging.recipe.ChannelRecipe] for each
        channel (#910): rotate, flip, translate and crop steps that run on the
        channel's cut, in order. ``None`` leaves the cut as it is.

    Raises
    ------
    ChannelLayoutError
        On an unknown kind or donor position, a rectangle given to a half layout or
        missing from a ``rectangles`` layout, a malformed rectangle, or two
        rectangles of different size. Bounds against a frame are checked by
        [`geometries`][], because the layout does not know the frame size.
    """

    kind: str = LAYOUT_SIDE_BY_SIDE
    donor: str | None = None
    donor_rect: tuple[int, int, int, int] | None = None
    acceptor_rect: tuple[int, int, int, int] | None = None
    donor_recipe: ChannelRecipe | None = None
    acceptor_recipe: ChannelRecipe | None = None

    def __post_init__(self) -> None:
        """Refuse a malformed layout and fill in the default donor of a half layout."""
        for name in ("donor_recipe", "acceptor_recipe"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, ChannelRecipe):
                raise ChannelLayoutError(
                    f"{name} must be a ChannelRecipe or None, got {type(value).__name__}"
                )
        if self.kind not in LAYOUT_KINDS:
            raise ChannelLayoutError(
                f"channel layout kind must be one of {LAYOUT_KINDS}, got {self.kind!r}"
            )
        if self.kind == LAYOUT_RECTANGLES:
            if self.donor is not None:
                raise ChannelLayoutError(
                    "a 'rectangles' layout names its channels by donor_rect and "
                    f"acceptor_rect; donor must be None, got {self.donor!r}"
                )
            if self.donor_rect is None or self.acceptor_rect is None:
                raise ChannelLayoutError(
                    "a 'rectangles' layout needs both donor_rect and acceptor_rect"
                )
            donor_rect = _rect(self.donor_rect, "donor_rect")
            acceptor_rect = _rect(self.acceptor_rect, "acceptor_rect")
            # With a recipe the sizes are compared after it, by ``geometries`` (#910).
            no_recipe = self.donor_recipe is None and self.acceptor_recipe is None
            if no_recipe and _size(donor_rect) != _size(acceptor_rect):
                raise ChannelLayoutError(
                    "the donor and the acceptor rectangle must be the same size (height x "
                    f"width): donor_rect {list(donor_rect)} is "
                    f"{_size(donor_rect)[0]}x{_size(donor_rect)[1]}, acceptor_rect "
                    f"{list(acceptor_rect)} is "
                    f"{_size(acceptor_rect)[0]}x{_size(acceptor_rect)[1]}"
                )
            # Frozen dataclass: normalize through object.__setattr__ (value semantics).
            object.__setattr__(self, "donor_rect", donor_rect)
            object.__setattr__(self, "acceptor_rect", acceptor_rect)
            return
        if self.donor_rect is not None or self.acceptor_rect is not None:
            raise ChannelLayoutError(
                f"donor_rect and acceptor_rect apply only to a 'rectangles' layout, "
                f"not to {self.kind!r}"
            )
        positions = _HALF_POSITIONS[self.kind]
        donor = next(iter(positions)) if self.donor is None else self.donor
        if donor not in positions:
            raise ChannelLayoutError(
                f"donor for a {self.kind!r} layout must be one of {tuple(positions)}, "
                f"got {self.donor!r}"
            )
        object.__setattr__(self, "donor", donor)

    @property
    def acceptor(self) -> str | None:
        """The acceptor half of a half layout; ``None`` for ``rectangles``."""
        if self.kind == LAYOUT_RECTANGLES:
            return None
        return _HALF_POSITIONS[self.kind][str(self.donor)]

    def rectangles(self, height: int, width: int) -> tuple[tuple[int, ...], tuple[int, ...]]:
        """Return the ``(donor, acceptor)`` ``[y1, x1, y2, x2]`` rectangles for one frame.

        Raises
        ------
        ChannelLayoutError
            If the frame is too small to cut in two, or a rectangle is outside it.
        """
        height, width = int(height), int(width)
        if height < 1 or width < 1:
            raise ChannelLayoutError(f"frame must be at least 1x1 px, got {height}x{width}")
        if self.kind == LAYOUT_SIDE_BY_SIDE:
            half = width // 2
            if half < 1:
                raise ChannelLayoutError(
                    f"movie too narrow ({width} px) to split into two channels side by side"
                )
            halves = {"left": (1, 1, height, half), "right": (1, half + 1, height, 2 * half)}
        elif self.kind == LAYOUT_TOP_BOTTOM:
            half = height // 2
            if half < 1:
                raise ChannelLayoutError(
                    f"movie too short ({height} px) to split into two channels top and bottom"
                )
            halves = {"top": (1, 1, half, width), "bottom": (half + 1, 1, 2 * half, width)}
        else:
            donor_rect = self.donor_rect
            acceptor_rect = self.acceptor_rect
            assert donor_rect is not None and acceptor_rect is not None  # __post_init__
            for name, rect in (("donor_rect", donor_rect), ("acceptor_rect", acceptor_rect)):
                if rect[2] > height or rect[3] > width:
                    raise ChannelLayoutError(
                        f"{name} {list(rect)} is outside the {height}x{width} frame "
                        "(bounds are 1-based inclusive)"
                    )
            return donor_rect, acceptor_rect
        return halves[str(self.donor)], halves[str(self.acceptor)]

    def geometries(self, height: int, width: int) -> tuple[ChannelGeometry, ChannelGeometry]:
        """Return the ``(donor, acceptor)`` [`ChannelGeometry`][] for one frame size.

        Without recipes each geometry is a crop with identity rotation and flip. With
        a recipe, the channel's cut and its recipe fold into one rotate -> flip -> crop
        geometry that gives the same pixels (#910). Overlapping ``rectangles`` give a
        [`ChannelOverlapWarning`][] and are still returned.

        Raises
        ------
        ChannelLayoutError
            As [`rectangles`][]; also when a recipe does not fit its channel, or the
            two channel images after the recipes differ in size.
        """
        donor_rect, acceptor_rect = self.rectangles(height, width)
        if _overlaps(donor_rect, acceptor_rect):
            warnings.warn(
                f"the donor rectangle {list(donor_rect)} and the acceptor rectangle "
                f"{list(acceptor_rect)} overlap; the shared pixels feed both channels",
                ChannelOverlapWarning,
                stacklevel=2,
            )
        if self.donor_recipe is None and self.acceptor_recipe is None:
            return ChannelGeometry(crop=donor_rect), ChannelGeometry(crop=acceptor_rect)
        out = []
        for name, rect, recipe in (
            ("donor", donor_rect, self.donor_recipe),
            ("acceptor", acceptor_rect, self.acceptor_recipe),
        ):
            full = ChannelRecipe((("crop", rect),)).then(recipe or ChannelRecipe())
            try:
                out.append((full.canonical((height, width)), full.view_shape((height, width))))
            except ChannelRecipeError as exc:
                raise ChannelLayoutError(
                    f"the {name} recipe does not fit its channel: {exc}"
                ) from exc
        (donor_geom, donor_shape), (acceptor_geom, acceptor_shape) = out
        if donor_shape != acceptor_shape:
            raise ChannelLayoutError(
                "after the recipes the two channel images must be the same size (height x "
                f"width): donor {donor_shape[0]}x{donor_shape[1]} "
                f"({self.donor_recipe or ChannelRecipe()}), acceptor "
                f"{acceptor_shape[0]}x{acceptor_shape[1]} "
                f"({self.acceptor_recipe or ChannelRecipe()})"
            )
        return donor_geom, acceptor_geom

    def to_dict(self) -> dict[str, Any]:
        """Return the record as a JSON-ready dict (rectangles as lists, ``None`` when unused)."""
        return {
            "kind": self.kind,
            "donor": self.donor,
            "acceptor": self.acceptor,
            "donor_rect": None if self.donor_rect is None else list(self.donor_rect),
            "acceptor_rect": None if self.acceptor_rect is None else list(self.acceptor_rect),
            "donor_recipe": None if self.donor_recipe is None else str(self.donor_recipe),
            "acceptor_recipe": None if self.acceptor_recipe is None else str(self.acceptor_recipe),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChannelLayout:
        """Rebuild a layout from [`to_dict`][] output (``acceptor`` is derived, not read)."""
        recipes = {
            name: None if data.get(name) is None else ChannelRecipe.parse(data[name])
            for name in ("donor_recipe", "acceptor_recipe")
        }
        return cls(
            kind=data.get("kind", LAYOUT_SIDE_BY_SIDE),
            donor=data.get("donor"),
            donor_rect=data.get("donor_rect"),
            acceptor_rect=data.get("acceptor_rect"),
            **recipes,
        )


#: The default layout: side by side, donor on the left (the split before #908).
DEFAULT_CHANNEL_LAYOUT = ChannelLayout()


def check_layout_slot(movies_group: Any, movie_id: str) -> None:
    """Refuse a ``movie_id`` that cannot take a new layout record; write nothing.

    [`write_layout_record`][] runs this first; a writer that must reject before it
    mutates anything (``write_extraction``) runs it before its first append too.

    Raises
    ------
    ValueError
        If ``movie_id`` is empty, holds ``/``, is ``.`` or ``..``, or already has a
        record.
    """
    if not movie_id or "/" in movie_id or movie_id in (".", ".."):
        raise ValueError(f"movie_id {movie_id!r} cannot name a layout record")
    group = movies_group.get(LAYOUTS_GROUP)
    if group is not None and movie_id in group:
        raise ValueError(f"movie_id {movie_id!r} already has a layout record")


def write_layout_record(movies_group: Any, movie_id: str, layout: ChannelLayout) -> None:
    """Store ``layout`` for one movie as ``/movies/layouts/<movie_id>`` (additive data).

    The record is an empty group whose attributes are ``version`` (``1``), ``kind``,
    ``donor`` and ``acceptor`` (``""`` for ``rectangles``) and, for ``rectangles``
    only, ``donor_rect`` and ``acceptor_rect`` (``int32[4]``, 1-based inclusive). A
    layout with a channel recipe (#910) is written as ``version`` ``2`` with
    ``donor_recipe`` and/or ``acceptor_recipe``, the text form of each recipe the
    layout has. The
    ``layouts`` group is made on first use, like ``/conditions/categories``; it is not
    part of the frozen skeleton, so ``schema/schema_frozen.json`` does not change.

    Raises
    ------
    ValueError
        If ``movie_id`` is empty, holds ``/``, or already has a record (a movie is
        write-once).
    """
    import numpy as np  # noqa: PLC0415

    check_layout_slot(movies_group, movie_id)
    group = (
        movies_group[LAYOUTS_GROUP]
        if LAYOUTS_GROUP in movies_group
        else movies_group.create_group(LAYOUTS_GROUP, track_order=True)
    )
    record = group.create_group(movie_id, track_order=True)
    has_recipes = layout.donor_recipe is not None or layout.acceptor_recipe is not None
    record.attrs["version"] = _RECORD_VERSION if has_recipes else 1
    record.attrs["kind"] = layout.kind
    record.attrs["donor"] = layout.donor or ""
    record.attrs["acceptor"] = layout.acceptor or ""
    if layout.donor_rect is not None and layout.acceptor_rect is not None:
        record.attrs["donor_rect"] = np.asarray(layout.donor_rect, dtype=np.int32)
        record.attrs["acceptor_rect"] = np.asarray(layout.acceptor_rect, dtype=np.int32)
    for name in ("donor_recipe", "acceptor_recipe"):
        if getattr(layout, name) is not None:
            record.attrs[name] = str(getattr(layout, name))


def _text(value: Any) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def read_layout_record(project_path: Any, movie_id: str) -> ChannelLayout | None:
    """Read the layout stored for ``movie_id``; ``None`` when that movie has none.

    A store written before #908, a Deep-LASI reconstruction, and an extraction that
    took its split from an imported ``.tmap`` carry no record; the movie's split is
    then only the ``donor_crop`` / ``acceptor_crop`` of its ``/movies`` row.

    Raises
    ------
    ValueError
        If the record has a ``version`` newer than this build reads.
    """
    from pathlib import Path  # noqa: PLC0415

    import h5py  # noqa: PLC0415

    with h5py.File(Path(project_path), "r") as f:
        group = f["movies"].get(LAYOUTS_GROUP)
        if group is None or movie_id not in group:
            return None
        attrs = group[movie_id].attrs
        version = int(attrs.get("version", 0))
        if version > _RECORD_VERSION:
            raise ValueError(
                f"layout record of {movie_id!r} has version {version}; this build reads "
                f"up to {_RECORD_VERSION}"
            )
        kind = _text(attrs["kind"])
        donor = _text(attrs.get("donor", "")) or None
        recipes = {
            name: ChannelRecipe.parse(_text(attrs[name])) if name in attrs else None
            for name in ("donor_recipe", "acceptor_recipe")
        }
        if kind == LAYOUT_RECTANGLES:
            return ChannelLayout(
                kind=kind,
                donor_rect=tuple(int(v) for v in attrs["donor_rect"]),
                acceptor_rect=tuple(int(v) for v in attrs["acceptor_rect"]),
                **recipes,
            )
        return ChannelLayout(kind=kind, donor=donor, **recipes)
