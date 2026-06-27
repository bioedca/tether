# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Channel split geometry -- the ``processImage`` port (PRD Appendix E Stage 1).

Deep-LASI extracts each FRET channel as a sub-image of the raw camera frame: a
fixed-order geometric transform ``imrotate(I, -rot) -> flipud/fliplr -> crop``
applied **identically** to the calibration map and every movie frame
(``tools/processImage.m:1-32``; ``classes/TIRFdata.m:118-120``). Tether reproduces
it faithfully and **file-driven** -- the per-channel ``{crop, rotation_deg, flip}``
comes from the movie's stored geometry (schema ``/movies`` ``donor_crop`` /
``acceptor_crop`` / ``*_rotation_deg`` / ``*_flip``), never hardcoded.

Conventions
-----------
* **Rotation** is one of ``{0, 90, 180, 270}`` degrees. ``processImage`` applies
  ``imrotate(I, -rotation_deg)``; for 90-degree multiples that is a lossless
  :func:`numpy.rot90` (MATLAB ``imrotate(I, 90*k) == rot90(I, k)``, and MATLAB's
  ``rot90`` shares NumPy's counter-clockwise convention on ``[row, col]`` arrays),
  so a positive ``rotation_deg`` rotates the image *clockwise* by that angle.
* **Flip** is ``[v, h]`` in ``{0, 1}``: ``v`` -> ``flipud`` (reverse rows),
  ``h`` -> ``fliplr`` (reverse columns), applied **after** the rotation.
* **Crop** is ``[y1, x1, y2, x2]``, MATLAB 1-based *inclusive* bounds in the
  rotated/flipped frame (matching Deep-LASI's ``Crop = [[y1, x1], [y2, x2]]`` and
  the schema's flat 4-vector / :attr:`tether.imaging.register.TmapChannel.origin`),
  converted here to a 0-based half-open slice ``rows[y1-1:y2], cols[x1-1:x2]``.

Both a single frame ``(H, W)`` and a movie stack ``(T, H, W)`` are accepted; the
transform acts on the trailing two (spatial) axes, so a stack is split frame-wise
in one call. The result may be a **view** into the input (``rot90`` / ``flip`` /
slicing return views) -- callers that mutate must copy first; downstream readers
(:func:`tether.imaging.detect.detection_image`) cast to ``float64`` and so copy.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

__all__ = [
    "ChannelGeometry",
    "process_image",
    "split_channels",
]

#: Rotations Deep-LASI stores (``classes/TIRFdata.m``); only 90-degree multiples,
#: so the transform stays a lossless ``rot90`` with no interpolation.
_ALLOWED_ROTATIONS: tuple[int, ...] = (0, 90, 180, 270)


def _spatial_axes(ndim: int) -> tuple[int, int]:
    """Return the ``(row, col)`` axes for a frame ``(H, W)`` or stack ``(T, H, W)``."""
    if ndim == 2:
        return 0, 1
    if ndim == 3:
        return 1, 2  # (T, H, W) -> act on the (H, W) plane, frame-wise
    raise ValueError(f"image must be 2-D (H, W) or 3-D (T, H, W), got {ndim}-D")


def process_image(
    image: np.ndarray,
    *,
    rotation_deg: int = 0,
    crop: np.ndarray | None = None,
    flip: tuple[int, int] | np.ndarray = (0, 0),
) -> np.ndarray:
    """Apply the Deep-LASI ``processImage`` channel transform (rotate -> flip -> crop).

    Parameters
    ----------
    image:
        A single frame ``(H, W)`` or a stack ``(T, H, W)`` (any dtype; the
        transform is dtype-preserving and lossless for 90-degree rotations).
    rotation_deg:
        One of ``{0, 90, 180, 270}``. Applied as ``imrotate(I, -rotation_deg)``,
        i.e. a *clockwise* rotation by ``rotation_deg`` (``processImage.m:12``).
    crop:
        ``[y1, x1, y2, x2]`` 1-based inclusive bounds in the rotated/flipped frame,
        or ``None`` for the full frame. Converted to ``rows[y1-1:y2], cols[x1-1:x2]``.
    flip:
        ``[v, h]`` in ``{0, 1}``; ``v`` reverses rows (``flipud``), ``h`` reverses
        columns (``fliplr``), applied after the rotation (``processImage.m:19-20``).

    Returns
    -------
    np.ndarray
        The channel sub-image, same number of dimensions as ``image`` (possibly a
        view; see the module docstring).
    """
    image = np.asarray(image)
    row_axis, col_axis = _spatial_axes(image.ndim)

    if rotation_deg not in _ALLOWED_ROTATIONS:
        raise ValueError(f"rotation_deg must be one of {_ALLOWED_ROTATIONS}, got {rotation_deg}")

    # imrotate(I, -rotation_deg): clockwise by rotation_deg. np.rot90 is CCW, so
    # k = (-rotation_deg // 90) % 4 turns it into the equivalent CCW count.
    out = image
    k = (-rotation_deg // 90) % 4
    if k:
        out = np.rot90(out, k, axes=(row_axis, col_axis))

    flip_arr = np.asarray(flip)
    if flip_arr.shape != (2,):
        raise ValueError(f"flip must be a length-2 [v, h] sequence, got shape {flip_arr.shape}")
    if flip_arr[0]:
        out = np.flip(out, axis=row_axis)
    if flip_arr[1]:
        out = np.flip(out, axis=col_axis)

    if crop is not None:
        cr = np.asarray(crop).ravel()
        if cr.size != 4:
            raise ValueError(f"crop must have 4 elements [y1, x1, y2, x2], got {cr.size}")
        y1, x1, y2, x2 = (int(v) for v in cr)
        height, width = out.shape[row_axis], out.shape[col_axis]
        if y1 < 1 or x1 < 1 or y2 < y1 or x2 < x1:
            raise ValueError(
                f"crop [y1, x1, y2, x2]={[y1, x1, y2, x2]} must be 1-based with y2>=y1, x2>=x1"
            )
        if y2 > height or x2 > width:
            raise ValueError(
                f"crop [y1, x1, y2, x2]={[y1, x1, y2, x2]} exceeds the rotated frame "
                f"({height}x{width}); bounds are 1-based inclusive"
            )
        sl: list[slice] = [slice(None)] * out.ndim
        sl[row_axis] = slice(y1 - 1, y2)  # 1-based inclusive -> 0-based half-open
        sl[col_axis] = slice(x1 - 1, x2)
        out = out[tuple(sl)]

    return out


@dataclass(frozen=True)
class ChannelGeometry:
    """Per-channel split geometry (schema ``/movies``; PRD Appendix E Stage 1).

    A typed record of the stored fields: ``crop`` = ``[y1, x1, y2, x2]`` 1-based
    inclusive (or ``None`` for the full frame), ``rotation_deg`` in
    ``{0, 90, 180, 270}``, and ``flip`` = ``[v, h]``. Validation happens in
    :func:`process_image` (the single source), so the record stays a thin holder.
    """

    crop: np.ndarray | None = None
    rotation_deg: int = 0
    flip: tuple[int, int] = (0, 0)

    def apply(self, image: np.ndarray) -> np.ndarray:
        """Split ``image`` (``(H, W)`` or ``(T, H, W)``) into this channel's sub-image."""
        return process_image(image, rotation_deg=self.rotation_deg, crop=self.crop, flip=self.flip)


def split_channels(
    movie: np.ndarray, donor: ChannelGeometry, acceptor: ChannelGeometry
) -> tuple[np.ndarray, np.ndarray]:
    """Split a raw movie/frame into ``(donor, acceptor)`` channel sub-images.

    Each channel's :class:`ChannelGeometry` is applied independently
    (``rotate -> flip -> crop``); the donor (reference) and acceptor halves come
    back as separate arrays in the same ``(..., H', W')`` layout as the input.
    Feed each half straight into :func:`tether.imaging.detect.detection_image`.
    """
    return donor.apply(movie), acceptor.apply(movie)
