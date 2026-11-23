# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Channel geometry recipes: rotate, flip, translate and crop one channel (#910).

A [`ChannelRecipe`][] is an ordered list of operations that turns a camera frame
into one channel image (PRD Appendix E stage 1). Four operations exist:

* ``("rotate", deg)`` -- ``deg`` in ``{0, 90, 180, 270}``, *clockwise*, as Deep-LASI's
  ``imrotate(I, -deg)`` ([`tether.imaging.split.process_image`][]);
* ``("flip", (v, h))`` -- ``v`` reverses the rows, ``h`` the columns, each ``0`` or ``1``;
* ``("translate", (down, right))`` -- moves the image by whole pixels, ``down`` rows
  and ``right`` columns (negative values move it up or left). The frame keeps its
  size: pixels moved out of it are dropped, and the pixels it leaves empty have no
  source, so a later ``crop`` must remove them;
* ``("crop", (y1, x1, y2, x2))`` -- 1-based inclusive bounds in the current frame
  (the crop convention of [`tether.imaging.split`][]).

The operations run in the order given. No operation resamples a pixel: each output
pixel is one input pixel, and a recipe that would leave an output pixel without a
source is refused with [`ChannelRecipeError`][]. So a 90-degree rotation, a flip, a
whole-pixel move and a crop only reorder and select pixels.

Every recipe is equal, on a given frame size, to one Deep-LASI ``processImage``
geometry -- rotate, then flip, then crop -- because the rotations and flips form a
closed group of eight and the moves and crops reduce to one crop window.
[`ChannelRecipe.canonical`][] returns that
[`ChannelGeometry`][tether.imaging.split.ChannelGeometry], which is what
``/movies/table`` stores, and it gives the same pixels as [`ChannelRecipe.apply`][].
A ``.tmap`` channel's ``Rotation``, ``Flip`` and ``Crop`` become a recipe with
[`ChannelRecipe.from_geometry`][].

Coordinates. [`ChannelRecipe.to_view`][] takes points in the original frame to the
channel image after the recipe, and [`ChannelRecipe.to_original`][] takes them back.
Points are ``(N, 2)`` ``[x, y] = [col, row]`` arrays in 0-based pixels, as in the
rest of [`tether.imaging`][]; sub-pixel values are allowed. A recipe is a
one-to-one map of the plane, so a point that goes to the view and back is the same
point.

[`check_frame_fits`][] is the geometry check of an imported map (#887): it refuses a
movie whose frames cannot hold the map's channel geometry and names both.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from tether.imaging.split import ChannelGeometry

__all__ = [
    "RECIPE_OPERATIONS",
    "ChannelRecipe",
    "ChannelRecipeError",
    "check_frame_fits",
]

#: The operation names, in the order the docs list them.
RECIPE_OPERATIONS: tuple[str, ...] = ("rotate", "flip", "translate", "crop")

_ROTATIONS = (0, 90, 180, 270)
_FLIP_TEXT = {(0, 0): "none", (1, 0): "v", (0, 1): "h", (1, 1): "vh"}


class ChannelRecipeError(ValueError):
    """A channel recipe that is malformed, or that cannot be applied to a frame."""


def _whole(value: Any, name: str, count: int) -> tuple[int, ...]:
    """Return ``count`` whole numbers from ``value`` or raise [`ChannelRecipeError`][]."""
    try:
        items = list(np.asarray(value, dtype=object).ravel())
    except TypeError:  # pragma: no cover - np.asarray(object) takes anything
        items = []
    out: list[int] = []
    for item in items:
        try:
            ok = not isinstance(item, bool) and float(item).is_integer()
        except (TypeError, ValueError):
            ok = False
        if not ok:
            raise ChannelRecipeError(f"{name} values must be whole numbers, got {value!r}")
        out.append(int(item))
    if len(out) != count:
        raise ChannelRecipeError(f"{name} needs {count} whole numbers, got {value!r}")
    return tuple(out)


def _step(name: Any, value: Any) -> tuple[str, Any]:
    """Validate one operation and return it in its normal form."""
    if name == "rotate":
        (deg,) = _whole(value, "rotate", 1)
        if deg not in _ROTATIONS:
            raise ChannelRecipeError(f"rotate must be one of {_ROTATIONS} degrees, got {deg}")
        return name, deg
    if name == "flip":
        flip = _whole(value, "flip", 2)
        if any(v not in (0, 1) for v in flip):
            raise ChannelRecipeError(f"flip values must each be 0 or 1, got {list(flip)}")
        return name, flip
    if name == "translate":
        return name, _whole(value, "translate", 2)
    if name == "crop":
        y1, x1, y2, x2 = _whole(value, "crop", 4)
        if y1 < 1 or x1 < 1 or y2 < y1 or x2 < x1:
            raise ChannelRecipeError(
                f"crop {[y1, x1, y2, x2]} must be 1-based with y2 >= y1 and x2 >= x1"
            )
        return name, (y1, x1, y2, x2)
    raise ChannelRecipeError(f"unknown recipe operation {name!r}; use one of {RECIPE_OPERATIONS}")


def _point(name: str, value: Any, r: Any, c: Any, frame: tuple[int, int]) -> tuple[Any, Any]:
    """Map a ``(row, col)`` point through one operation on a frame of size ``frame``."""
    fh, fw = frame
    if name == "rotate":
        for _ in range(value // 90):  # one clockwise quarter turn: (r, c) -> (c, H-1-r)
            r, c = c, fh - 1 - r
            fh, fw = fw, fh
        return r, c
    if name == "flip":
        return (fh - 1 - r if value[0] else r), (fw - 1 - c if value[1] else c)
    if name == "translate":
        return r + value[0], c + value[1]
    return r - (value[0] - 1), c - (value[1] - 1)  # crop


def _point_back(name: str, value: Any, r: Any, c: Any, frame: tuple[int, int]) -> tuple[Any, Any]:
    """Invert [`_point`][] for a frame of size ``frame`` *before* the operation."""
    fh, fw = frame
    if name == "rotate":
        turns = value // 90
        for i in range(turns):  # undo the last quarter turn first
            h_before = fh if (turns - 1 - i) % 2 == 0 else fw
            r, c = h_before - 1 - c, r
        return r, c
    if name == "flip":
        return (fh - 1 - r if value[0] else r), (fw - 1 - c if value[1] else c)
    if name == "translate":
        return r - value[0], c - value[1]
    return r + (value[0] - 1), c + (value[1] - 1)  # crop


def _next_frame(name: str, value: Any, frame: tuple[int, int]) -> tuple[int, int]:
    """Return the frame size after one operation."""
    if name == "rotate" and value in (90, 270):
        return frame[1], frame[0]
    if name == "crop":
        return value[2] - value[0] + 1, value[3] - value[1] + 1
    return frame


@dataclass(frozen=True)
class ChannelRecipe:
    """An ordered list of rotate, flip, translate and crop operations for one channel.

    Parameters
    ----------
    steps:
        ``((name, value), ...)`` in the order they run; see the module docstring for
        each operation. The empty recipe is the identity.

    Raises
    ------
    ChannelRecipeError
        On an unknown operation or a malformed value. Bounds against a frame are
        checked when the recipe is applied, because the recipe does not know the
        frame size.
    """

    steps: tuple[tuple[str, Any], ...] = ()

    def __post_init__(self) -> None:
        """Validate every operation and store it in its normal form."""
        normal: list[tuple[str, Any]] = []
        for item in self.steps:
            try:
                name, value = item
            except (TypeError, ValueError):
                raise ChannelRecipeError(
                    f"each recipe step is a (name, value) pair, got {item!r}"
                ) from None
            normal.append(_step(name, value))
        object.__setattr__(self, "steps", tuple(normal))

    @classmethod
    def from_geometry(cls, geometry: ChannelGeometry) -> ChannelRecipe:
        """Return the recipe of a ``processImage`` geometry: rotate, flip, crop.

        Identity steps (rotation ``0``, flip ``(0, 0)``, no crop) are left out.
        """
        steps: list[tuple[str, Any]] = []
        if geometry.rotation_deg:
            steps.append(("rotate", geometry.rotation_deg))
        if any(geometry.flip):
            steps.append(("flip", geometry.flip))
        if geometry.crop is not None:
            steps.append(("crop", geometry.crop))
        return cls(tuple(steps))

    @classmethod
    def parse(cls, text: str) -> ChannelRecipe:
        """Read the text form of [`__str__`][], such as ``"crop 1 1 256 256, rotate 90"``.

        Operations are separated by commas; a flip is ``v``, ``h``, ``vh`` or ``none``;
        ``"none"`` or an empty string is the identity. Every recipe's text form reads
        back as the same recipe.
        """
        steps: list[tuple[str, Any]] = []
        for part in str(text).split(","):
            words = part.split()
            if not words or words == ["none"]:
                continue
            name, args = words[0], words[1:]
            if name == "flip":
                if len(args) != 1 or args[0] not in ("v", "h", "vh", "none"):
                    raise ChannelRecipeError(f"flip takes v, h, vh or none, got {part.strip()!r}")
                steps.append((name, (int("v" in args[0]), int("h" in args[0]))))
                continue
            try:
                numbers = [int(a) for a in args]
            except ValueError:
                raise ChannelRecipeError(f"{part.strip()!r} needs whole numbers") from None
            steps.append((name, numbers[0] if name == "rotate" and len(numbers) == 1 else numbers))
        return cls(tuple(steps))

    def __str__(self) -> str:
        """Return the text form: ``"rotate 90, flip v, translate 2 -1, crop 1 1 64 64"``."""
        parts = []
        for name, value in self.steps:
            if name == "rotate":
                parts.append(f"rotate {value}")
            elif name == "flip":
                parts.append(f"flip {_FLIP_TEXT[value]}")
            else:
                parts.append(" ".join([name, *(str(v) for v in value)]))
        return ", ".join(parts) or "none"

    def then(self, other: ChannelRecipe | Iterable[tuple[str, Any]]) -> ChannelRecipe:
        """Return this recipe followed by ``other``."""
        more = other.steps if isinstance(other, ChannelRecipe) else tuple(other)
        return ChannelRecipe(self.steps + more)

    def apply(self, image: np.ndarray) -> np.ndarray:
        """Return the channel image of a frame ``(H, W)`` or a stack ``(T, H, W)``.

        The result holds only input pixels, reordered and selected (it may be a view).

        Raises
        ------
        ChannelRecipeError
            If a crop is outside its frame, or an output pixel would have no source
            (a move that no later crop removes, or a crop of the part a move left empty).
        """
        out = np.asarray(image)
        if out.ndim not in (2, 3):
            raise ChannelRecipeError(f"image must be (H, W) or (T, H, W), got {out.ndim}-D")
        ra, ca = out.ndim - 2, out.ndim - 1
        frame = (out.shape[ra], out.shape[ca])
        oy = ox = 0  # the frame position of out[0, 0]
        for name, value in self.steps:
            h, w = out.shape[ra], out.shape[ca]
            if name == "rotate":
                out = np.rot90(out, -(value // 90), axes=(ra, ca))
            elif name == "flip":
                if value[0]:
                    out = np.flip(out, axis=ra)
                if value[1]:
                    out = np.flip(out, axis=ca)
            elif name == "crop" and (value[2] > frame[0] or value[3] > frame[1]):
                raise ChannelRecipeError(
                    f"crop {list(value)} is outside the {frame[0]} x {frame[1]} px frame "
                    "(bounds are 1-based inclusive)"
                )
            if name in ("rotate", "flip"):
                corners = [_point(name, value, oy, ox, frame)]
                corners.append(_point(name, value, oy + h - 1, ox + w - 1, frame))
                oy, ox = min(p[0] for p in corners), min(p[1] for p in corners)
            else:
                oy, ox = _point(name, value, oy, ox, frame)
            frame = _next_frame(name, value, frame)
            # Drop the pixels that are now outside the frame.
            h, w = out.shape[ra], out.shape[ca]
            r0, r1 = max(0, -oy), min(h, frame[0] - oy)
            c0, c1 = max(0, -ox), min(w, frame[1] - ox)
            if r1 <= r0 or c1 <= c0:
                raise ChannelRecipeError(f"recipe {self} moves the whole image out of the frame")
            sl: list[slice] = [slice(None)] * out.ndim
            sl[ra], sl[ca] = slice(r0, r1), slice(c0, c1)
            out = out[tuple(sl)]
            oy, ox = oy + r0, ox + c0
        if (oy, ox) != (0, 0) or (out.shape[ra], out.shape[ca]) != frame:
            raise ChannelRecipeError(
                f"recipe {self} leaves pixels of the {frame[0]} x {frame[1]} px channel "
                "image with no source pixel; crop away the part a translate leaves empty"
            )
        return out

    def view_shape(self, frame_shape: Sequence[int]) -> tuple[int, int]:
        """Return the ``(height, width)`` of the channel image for a frame size.

        Raises [`ChannelRecipeError`][] as [`apply`][] does, without reading pixels.
        """
        height, width = (int(v) for v in frame_shape)
        if height < 1 or width < 1:
            raise ChannelRecipeError(f"frame must be at least 1 x 1 px, got {height} x {width}")
        out = self.apply(np.broadcast_to(np.uint8(0), (height, width)))
        return int(out.shape[0]), int(out.shape[1])

    def _frames(self, frame_shape: Sequence[int]) -> list[tuple[int, int]]:
        frames = [(int(frame_shape[0]), int(frame_shape[1]))]
        for name, value in self.steps:
            frames.append(_next_frame(name, value, frames[-1]))
        return frames

    def to_view(self, points: np.ndarray, frame_shape: Sequence[int]) -> np.ndarray:
        """Map ``(N, 2)`` ``[x, y]`` points of the original frame into the channel image."""
        pts = np.atleast_2d(np.asarray(points, dtype=np.float64))
        r, c = pts[:, 1].copy(), pts[:, 0].copy()
        for (name, value), frame in zip(self.steps, self._frames(frame_shape), strict=False):
            r, c = _point(name, value, r, c, frame)
        return np.column_stack([c, r])

    def to_original(self, points: np.ndarray, frame_shape: Sequence[int]) -> np.ndarray:
        """Map ``(N, 2)`` ``[x, y]`` points of the channel image back to the original frame.

        ``frame_shape`` is the original frame's ``(height, width)``, as for [`to_view`][].
        """
        pts = np.atleast_2d(np.asarray(points, dtype=np.float64))
        r, c = pts[:, 1].copy(), pts[:, 0].copy()
        frames = self._frames(frame_shape)
        for (name, value), frame in reversed(list(zip(self.steps, frames, strict=False))):
            r, c = _point_back(name, value, r, c, frame)
        return np.column_stack([c, r])

    def canonical(self, frame_shape: Sequence[int]) -> ChannelGeometry:
        """Return the rotate -> flip -> crop geometry equal to this recipe on a frame size.

        The geometry gives the same pixels as [`apply`][]; it is what ``/movies/table``
        stores. Raises [`ChannelRecipeError`][] as [`view_shape`][] does.
        """
        frame = (int(frame_shape[0]), int(frame_shape[1]))
        vh, vw = self.view_shape(frame)
        # Where the view's origin and its two unit steps come from in the original frame.
        origin = self.to_original(np.array([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]]), frame)
        # The eight rotate/flip pairs, without flip ``vh`` (rotate 180 again): a pure
        # rotation comes back unflipped, and a ``.tmap`` geometry turned by 0 or 90
        # degrees comes back as it was stored.
        for rotation in _ROTATIONS:
            for flip in ((0, 0), (1, 0), (0, 1)):
                base = ChannelRecipe((("rotate", rotation), ("flip", flip)))
                p = base.to_view(origin, frame)  # [x, y]
                if np.array_equal(p[1] - p[0], [1, 0]) and np.array_equal(p[2] - p[0], [0, 1]):
                    y1, x1 = int(round(p[0, 1])) + 1, int(round(p[0, 0])) + 1
                    return ChannelGeometry(
                        crop=(y1, x1, y1 + vh - 1, x1 + vw - 1), rotation_deg=rotation, flip=flip
                    )
        raise AssertionError("no rotate/flip matches the recipe")  # pragma: no cover


def check_frame_fits(
    height: int,
    width: int,
    donor: ChannelGeometry,
    acceptor: ChannelGeometry,
    *,
    source: str = "the map",
) -> None:
    """Refuse a movie whose frames cannot hold a map's channel geometry (#887, #910).

    A map stores each channel as rotate -> flip -> crop of the camera frame. A movie
    turned by 90 degrees against a non-square map, or one smaller than the map, does
    not fit; splitting it anyway would read the wrong pixels or fail deep inside.

    Raises
    ------
    ChannelRecipeError
        Naming the movie's frame size and the geometry of both channels of the map.
    """
    problems = []
    for name, geometry in (("donor", donor), ("acceptor", acceptor)):
        try:
            ChannelRecipe.from_geometry(geometry).view_shape((height, width))
        except ChannelRecipeError as exc:
            problems.append(f"{name}: {exc}")
    if problems:
        raise ChannelRecipeError(
            f"the movie's frames ({height} x {width} px, height x width) do not agree with "
            f"the channel geometry of {source} (donor: "
            f"{ChannelRecipe.from_geometry(donor)}; acceptor: "
            f"{ChannelRecipe.from_geometry(acceptor)}); {'; '.join(problems)}. Is the movie "
            "rotated against its map?"
        )
