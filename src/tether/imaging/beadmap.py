# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Make a channel map from a bead-slide video or from one image (#915, plan D1 of #890).

The workflow plan's mapping lane (G02) starts with "Calibration source: Image or
daily bead-slide video" and "Video: choose frames + max projection / Image: use
supplied calibration view"; its note 02 says "Keep projection settings and source
IDs." This module is that entry point. It sits on top of the present registration
stages and changes none of them:

1. **Source** -- one image ``(H, W)`` is used as it is. A video ``(T, H, W)`` is
   reduced to one image over a 0-based half-open frame range ``[start, stop)``
   with a ``max`` or ``mean`` projection ([`project_frames`][]).
2. **Stages 6 to 9** (PRD Appendix E) then run with no change: split into the
   donor and acceptor halves, the detection image and the spot detection
   ([`tether.imaging.detect`][]), the phase-correlation prealign and the
   mutual nearest-neighbour pairing ([`tether.imaging.register`][]), and the
   degree-2 polynomial fit with the RMS gate
   ([`tether.imaging.calibrate.fit_registration_map`][]).
3. **Quality record** ([`MapQuality`][]) -- the RMS residual, the number of
   pairs, the residual of each pair, and how much of the field the pairs cover.

The source record ([`BeadMapSource`][]: source id, frame range, projection),
the quality record and the bead settings go into the map's ``provenance``. The map
file ([`tether.imaging.calibrate.save_map`][]) and the project's
``/calibration`` group ([`tether.imaging.calibrate.write_calibration`][])
already persist ``provenance`` as JSON, so both keep them with no schema change.

A source with too few bead spots in either half is refused with
[`BeadMapError`][]; the message gives the count and the limit
([`DEFAULT_MIN_BEAD_SPOTS`][], PRD §11.2).

Coordinates follow the rest of [`tether.imaging`][]: ``(N, 2)`` arrays of
``[x, y] = [col, row]`` in 0-based pixels of each channel half.
"""

from __future__ import annotations

import hashlib
import operator
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import numpy as np

from tether.imaging.calibrate import (
    DEFAULT_RMS_GATE_PX,
    RegistrationMap,
    fit_registration_map,
)
from tether.imaging.detect import detect_spots_by_mode, detection_image
from tether.imaging.register import (
    estimate_similarity_prealign,
    estimate_translation_prealign,
    pair_control_points,
    point_rms,
)
from tether.imaging.split import ChannelGeometry, split_channels

if TYPE_CHECKING:
    from tether.project.lock import FileIdentity

__all__ = [
    "BEAD_PROJECTIONS",
    "DEFAULT_MIN_BEAD_SPOTS",
    "BeadMapError",
    "BeadMapResult",
    "BeadMapSource",
    "MapQuality",
    "bead_map",
    "bead_map_from_file",
    "map_quality",
    "project_frames",
]

#: Minimum bead spots in **each** channel half before a map is made (PRD §11.2).
#: Six is the number of coefficients of one output of the degree-2 polynomial map
#: (``[1, x, y, x*y, x**2, y**2]``), so fewer spots cannot give the pairs a
#: degree-2 fit needs. A source under the limit is refused, never fitted.
DEFAULT_MIN_BEAD_SPOTS = 6

#: The projections a bead video can be reduced with.
BEAD_PROJECTIONS: tuple[str, ...] = ("max", "mean")

#: The donor half is the registration reference (channel 0), the acceptor half
#: the moving channel (1) -- the same convention as ``tether.project.extract``.
_DONOR_CHANNEL = 0
_ACCEPTOR_CHANNEL = 1

_HASH_CHUNK = 1 << 20  # 1 MiB streaming read for the source content hash.

#: Frames reduced at a time by `project_frames` (bounds its working memory).
_PROJECTION_BLOCK = 32

#: TIFF series axes `bead_map_from_file` accepts: one image, or a stack whose
#: first axis is frames (tifffile's ``I`` = generic page stack, ``T`` = time,
#: ``Q`` = unknown stack). A channel (``C``), sample (``S``), focus (``Z``) or any
#: other axis is refused, so separate channels are never projected as frames.
_IMAGE_AXES = "YX"
_VIDEO_AXES: tuple[str, ...] = ("IYX", "TYX", "QYX")


class BeadMapError(ValueError):
    """A bead source that cannot give a map (bad frames, too few spots or pairs)."""


@dataclass(frozen=True)
class BeadMapSource:
    """Where a bead map came from (G02 note 02: "Keep projection settings and source IDs").

    ``kind`` is ``"image"`` for one supplied image, which has no frame range and
    no projection, or ``"video"`` for a bead video reduced over the 0-based
    half-open frame range ``[frame_start, frame_stop)`` with ``projection``.
    ``n_frames`` is the length of the whole video (``None`` for an image).
    """

    source_id: str
    kind: Literal["image", "video"]
    frame_start: int | None = None
    frame_stop: int | None = None
    projection: Literal["max", "mean"] | None = None
    n_frames: int | None = None

    def __post_init__(self) -> None:
        """Refuse a record whose fields do not agree with its kind."""
        if not isinstance(self.source_id, str) or not self.source_id.strip():
            raise BeadMapError("source_id must be a non-empty string")
        if self.kind == "image":
            if any(
                v is not None
                for v in (self.frame_start, self.frame_stop, self.projection, self.n_frames)
            ):
                raise BeadMapError("an image source has no frame range, projection or n_frames")
        elif self.kind == "video":
            if self.frame_start is None or self.frame_stop is None or self.n_frames is None:
                raise BeadMapError("a video source needs frame_start, frame_stop and n_frames")
            if not 0 <= self.frame_start < self.frame_stop <= self.n_frames:
                raise BeadMapError(
                    f"frame range [{self.frame_start}, {self.frame_stop}) is not inside "
                    f"the video's {self.n_frames} frames"
                )
            if self.projection not in BEAD_PROJECTIONS:
                raise BeadMapError(
                    f"projection must be one of {BEAD_PROJECTIONS}, got {self.projection!r}"
                )
        else:
            raise BeadMapError(f"kind must be 'image' or 'video', got {self.kind!r}")

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-serialisable record stored in the map's provenance."""
        return {
            "source_id": self.source_id,
            "kind": self.kind,
            "frame_start": self.frame_start,
            "frame_stop": self.frame_stop,
            "projection": self.projection,
            "n_frames": self.n_frames,
        }


@dataclass(frozen=True)
class MapQuality:
    """The quality record of a bead map.

    * ``rms_residual`` -- the RMS of the pair residuals in px (the value the RMS
      gate reads; equal to [`RegistrationMap.rms_residual`][]).
    * ``n_pairs`` -- the number of matched bead pairs the map was fitted on.
    * ``pair_residuals`` -- the residual of each pair in px: the distance between
      the acceptor position and the mapped donor position, in pair order.
    * ``coverage`` -- the fraction of the donor field, from 0 to 1, inside the
      convex hull of the paired donor positions. A map is an interpolation inside
      that hull and an extrapolation outside it, so a low value warns that much of
      the field is mapped with no bead near it. Fewer than three pairs, or pairs on
      one line, enclose no area and give 0.
    """

    rms_residual: float
    n_pairs: int
    pair_residuals: tuple[float, ...]
    coverage: float

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-serialisable record stored in the map's provenance."""
        return {
            "rms_residual": float(self.rms_residual),
            "n_pairs": int(self.n_pairs),
            "pair_residuals": [float(r) for r in self.pair_residuals],
            "coverage": float(self.coverage),
        }


@dataclass(frozen=True, eq=False)
class BeadMapResult:
    """A bead map with its quality record, its source record and the image it used."""

    registration_map: RegistrationMap
    quality: MapQuality
    source: BeadMapSource
    #: The image the stages ran on: the supplied image, or the video projection.
    image: np.ndarray


class _TiffFrames:
    """A TIFF video read one block of pages at a time, never decoded whole.

    ``frames[a:b]`` decodes pages ``a`` to ``b - 1`` of the series and nothing
    else, so [`project_frames`][] reads only the selected frames of a compressed
    file, one block at a time.
    """

    ndim = 3

    def __init__(self, tif: Any, shape: tuple[int, int, int]) -> None:
        """Wrap series 0 of the open ``tif``, whose shape is ``(T, H, W)``."""
        self._tif = tif
        self.shape = shape

    def __getitem__(self, key: slice) -> np.ndarray:
        """Decode the pages ``key`` selects, as an array ``(n, H, W)``."""
        pages = range(*key.indices(self.shape[0]))
        block = self._tif.asarray(series=0, key=list(pages))
        return np.asarray(block).reshape(len(pages), *self.shape[1:])


def project_frames(
    video: np.ndarray,
    *,
    frames: tuple[int, int] | None = None,
    projection: str = "max",
) -> np.ndarray:
    """Reduce a bead video ``(T, H, W)`` to one ``float64`` image ``(H, W)``.

    The video is reduced one block of frames at a time, so a long or
    memory-mapped video is never copied whole.

    Parameters
    ----------
    video : numpy.ndarray
        The bead video or memory map, shape ``(T, H, W)``, in camera counts.
    frames : tuple of (int, int) or None, optional
        The 0-based half-open frame range ``(start, stop)``, in frames
        (PRD §11.1). ``stop`` is excluded. ``None`` (the default) uses every
        frame.
    projection : {"max", "mean"}, optional
        ``"max"`` (the default, G02's choice) takes the per-pixel maximum over the
        frames; ``"mean"`` takes the per-pixel mean.

    Returns
    -------
    numpy.ndarray of float64
        The projected image, shape ``(H, W)``, in camera counts.

    Raises
    ------
    BeadMapError
        If ``video`` is not 3-D or has no frames, ``frames`` is an invalid
        range, or ``projection`` is not ``"max"`` or ``"mean"``.

    Examples
    --------
    >>> video = np.array([[[1, 5]], [[3, 2]], [[8, 0]]], dtype=np.uint16)
    >>> project_frames(video)
    array([[8., 5.]])
    >>> project_frames(video, frames=(0, 2), projection="mean")
    array([[2. , 3.5]])
    """
    if not isinstance(video, _TiffFrames):
        video = np.asarray(video)
    if video.ndim != 3:
        raise BeadMapError(f"a bead video must be 3-D (T, H, W), got shape {video.shape}")
    start, stop = _frame_range(frames, video.shape[0])
    if projection not in BEAD_PROJECTIONS:
        raise BeadMapError(f"projection must be one of {BEAD_PROJECTIONS}, got {projection!r}")
    # Reduce in blocks of frames, so a long (or memory-mapped) video never needs a
    # float64 copy of the whole selection: memory is bounded by one block + one image.
    acc: np.ndarray | None = None
    for first in range(start, stop, _PROJECTION_BLOCK):
        block = np.asarray(video[first : min(first + _PROJECTION_BLOCK, stop)], dtype=np.float64)
        part = block.max(axis=0) if projection == "max" else block.sum(axis=0)
        if acc is None:
            acc = part
        elif projection == "max":
            np.maximum(acc, part, out=acc)
        else:
            acc += part
    assert acc is not None  # _frame_range guarantees at least one frame
    return acc if projection == "max" else acc / float(stop - start)


def _frame_range(frames: tuple[int, int] | None, n_frames: int) -> tuple[int, int]:
    if n_frames < 1:
        raise BeadMapError("a bead video needs at least one frame")
    if frames is None:
        return 0, n_frames
    try:
        # operator.index, not int(): a fractional bound is refused, never truncated.
        start, stop = (operator.index(v) for v in frames)
    except (TypeError, ValueError) as exc:
        raise BeadMapError(
            f"frames must be a (start, stop) pair of integers, got {frames!r}"
        ) from exc
    if not 0 <= start < stop <= n_frames:
        raise BeadMapError(
            f"frame range [{start}, {stop}) is not inside the video's {n_frames} frames "
            "(0-based, stop excluded)"
        )
    return start, stop


def _half_split(
    width: int, height: int, donor_side: str
) -> tuple[ChannelGeometry, ChannelGeometry]:
    """Donor/acceptor geometry for a vertical left/right split of the frame.

    The same split ``tether.project.extract`` makes for a movie: both halves are
    ``width // 2`` wide (a trailing odd column is dropped) so the two detection
    images share a shape, which the phase-correlation prealign needs.
    """
    if donor_side not in ("left", "right"):
        raise BeadMapError(f"donor_side must be 'left' or 'right', got {donor_side!r}")
    half = width // 2
    if half < 1:
        raise BeadMapError(f"image too narrow ({width} px) to split into two channels")
    left = ChannelGeometry(crop=(1, 1, height, half))
    right = ChannelGeometry(crop=(1, half + 1, height, 2 * half))
    return (left, right) if donor_side == "left" else (right, left)


def map_quality(
    reg_map: RegistrationMap,
    reference_points: np.ndarray,
    moving_points: np.ndarray,
    *,
    field_shape: tuple[int, int],
) -> MapQuality:
    """Compute the [`MapQuality`][] of a map from its matched pairs.

    Parameters
    ----------
    reg_map : RegistrationMap
        The donor-to-acceptor map that was fitted on the pairs.
    reference_points : numpy.ndarray
        The matched donor positions, shape ``(N, 2)``, as ``[x, y]`` in pixels of
        the donor half.
    moving_points : numpy.ndarray
        The matched acceptor positions, shape ``(N, 2)``, as ``[x, y]`` in pixels
        of the acceptor half, in the same order as ``reference_points``.
    field_shape : tuple of (int, int)
        The donor half's ``(H, W)``, in pixels: the field that the coverage is a
        fraction of.

    Returns
    -------
    MapQuality
        The RMS residual and the residual of each pair (in pixels), the number of
        pairs, and the coverage (unitless, from 0 to 1).

    Raises
    ------
    ValueError
        If the two point arrays are not matching ``(N, 2)`` arrays, or
        ``field_shape`` is not positive.

    Examples
    --------
    A 3 x 3 grid of pairs, each moved by one pixel, spans a 50 x 50 pixel square
    of a 100 x 100 pixel field:

    >>> from tether.imaging.calibrate import fit_registration_map
    >>> grid = np.array([[x, y] for x in (10, 35, 60) for y in (10, 35, 60)], dtype=float)
    >>> reg_map = fit_registration_map(grid, grid + 1.0, reference_channel=0, moving_channel=1)
    >>> quality = map_quality(reg_map, grid, grid + 1.0, field_shape=(100, 100))
    >>> quality.n_pairs, round(quality.coverage, 6), round(quality.rms_residual, 6)
    (9, 0.25, 0.0)
    """
    ref = np.atleast_2d(np.asarray(reference_points, dtype=np.float64))
    mov = np.atleast_2d(np.asarray(moving_points, dtype=np.float64))
    if ref.shape != mov.shape or ref.ndim != 2 or ref.shape[1] != 2:
        raise ValueError("reference_points and moving_points must be matching (N, 2) arrays")
    height, width = (int(v) for v in field_shape)
    if height < 1 or width < 1:
        raise ValueError(f"field_shape must be positive, got {field_shape}")
    mapped = reg_map.apply_reference_to_moving(ref)
    residuals = np.sqrt(np.sum((mapped - mov) ** 2, axis=1))
    # The same computation the fit's RMS gate uses, so the two agree to the bit
    # (re-deriving it from the rounded per-pair residuals can differ by one ulp).
    rms = point_rms(mapped, mov) if len(residuals) else float("nan")
    return MapQuality(
        rms_residual=rms,
        n_pairs=len(ref),
        pair_residuals=tuple(float(r) for r in residuals),
        coverage=_hull_coverage(ref, height * width),
    )


def _hull_coverage(points: np.ndarray, field_area: int) -> float:
    """Fraction of ``field_area`` inside the convex hull of ``points`` (0 if degenerate)."""
    if len(points) < 3:
        return 0.0
    from scipy.spatial import ConvexHull, QhullError  # noqa: PLC0415

    try:
        area = float(ConvexHull(points).volume)  # in 2-D, "volume" is the area
    except QhullError:  # all points on one line (or coincident): no enclosed area
        return 0.0
    return min(1.0, area / float(field_area))


def bead_map(
    data: np.ndarray,
    *,
    source_id: str,
    frames: tuple[int, int] | None = None,
    projection: str = "max",
    donor_side: str = "left",
    detection_mode: str = "wavelet",
    detection_threshold: float | None = None,
    min_separation: float | None = None,
    prealign: str = "translation",
    prealign_upsample: int = 10,
    prealign_low_sigma: float = 1.0,
    prealign_high_sigma: float = 20.0,
    pair_tol: float = 2.0,
    min_bead_spots: int = DEFAULT_MIN_BEAD_SPOTS,
    gate_px: float = DEFAULT_RMS_GATE_PX,
    on_over_gate: Literal["warn", "fail"] = "warn",
    app_version: str | None = None,
    bead_file: str | None = None,
) -> BeadMapResult:
    """Make a donor<->acceptor map from a bead video or one bead image.

    Parameters
    ----------
    data : numpy.ndarray
        One image ``(H, W)`` (used as supplied) or a bead video ``(T, H, W)``
        (reduced by [`project_frames`][] with ``frames`` and ``projection``), in
        camera counts. The full camera frame: it is split left/right into the
        two channels.
    source_id : str
        The identity of the source, stored with the map (for a file,
        [`bead_map_from_file`][] uses ``"sha256:<hex>"`` of its bytes).
    frames : tuple of (int, int) or None, optional
        For a video only: the 0-based half-open frame range, in frames
        (``None``, the default, uses every frame). Passing it with an image is
        an error.
    projection : {"max", "mean"}, optional
        For a video only: the projection (default ``"max"``). Passing
        ``"mean"`` with an image is an error.
    donor_side : {"left", "right"}, optional
        Which half of the frame is the donor (default ``"left"``).
    detection_mode : str, optional
        The spot detector, as in ``tether extract`` (PRD §11.2; default
        ``"wavelet"``).
    detection_threshold : float or None, optional
        The detection threshold, unitless (PRD §11.2). Only the
        ``"intensity"`` and ``"bandpass"`` modes use it; the default
        ``"wavelet"`` mode ignores it. ``None`` (the default) uses the mode's own
        value.
    min_separation : float or None, optional
        The least distance between two spots, in pixels (PRD §11.2). ``None``
        (the default) uses the mode's own value.
    prealign : {"translation", "similarity"}, optional
        The prealign that seeds the pairing, as in ``tether extract`` (default
        ``"translation"``).
    prealign_upsample : int, optional
        The phase-correlation upsampling factor (default 10) for the translation
        step, which is resolved to ``1 / prealign_upsample`` pixels.
    prealign_low_sigma : float, optional
        The low Gaussian sigma of the similarity prealign's band-pass, in pixels
        (default 1.0).
    prealign_high_sigma : float, optional
        The high Gaussian sigma of the similarity prealign's band-pass, in pixels
        (default 20.0).
    pair_tol : float, optional
        The largest distance between an acceptor spot, moved into the donor
        frame by the prealign, and its donor partner, in pixels of the donor
        half (default 2.0).
    min_bead_spots : int, optional
        The least number of bead spots each half must have (PRD §11.2, default
        [`DEFAULT_MIN_BEAD_SPOTS`][]). Under it the source is refused.
    gate_px : float, optional
        The RMS gate on the fit residual, in pixels (default
        [`DEFAULT_RMS_GATE_PX`][tether.imaging.calibrate.DEFAULT_RMS_GATE_PX];
        [`tether.imaging.calibrate.fit_registration_map`][]).
    on_over_gate : {"warn", "fail"}, optional
        What a map over the gate does (default ``"warn"``).
    app_version : str or None, optional
        The Tether version stamped in the map's provenance.
    bead_file : str or None, optional
        The bead file name stamped in the map's provenance.

    Returns
    -------
    BeadMapResult
        The [`RegistrationMap`][] (``source="native"``; its ``provenance``
        holds ``bead_source``, ``map_quality`` and ``bead_settings``), the
        [`MapQuality`][], the [`BeadMapSource`][] and the image used.

    Raises
    ------
    BeadMapError
        A bad shape, frame range or projection; fewer than ``min_bead_spots``
        spots in either half (the message gives both counts and the limit); or
        fewer than two matched pairs.

    Examples
    --------
    With ``image`` one bead frame ``(H, W)``, donor half on the left:

    >>> result = bead_map(image, source_id="beads-2026-11-17")
    >>> result.source.kind
    'image'
    >>> result.quality.n_pairs == result.registration_map.n_control_points
    True
    """
    if int(min_bead_spots) != min_bead_spots or min_bead_spots < 1:
        raise BeadMapError(f"min_bead_spots must be a positive integer, got {min_bead_spots}")
    if prealign not in ("translation", "similarity"):
        raise BeadMapError(f"prealign must be 'translation' or 'similarity', got {prealign!r}")
    if not isinstance(data, _TiffFrames):
        data = np.asarray(data)
    if data.ndim == 2:
        if frames is not None or projection != "max":
            raise BeadMapError("frames and projection apply to a bead video, not to one image")
        image = np.array(data, dtype=np.float64)  # a copy: data may be a memory map
        source = BeadMapSource(source_id=source_id, kind="image")
    elif data.ndim == 3:
        image = project_frames(data, frames=frames, projection=projection)
        start, stop = _frame_range(frames, data.shape[0])
        source = BeadMapSource(
            source_id=source_id,
            kind="video",
            frame_start=start,
            frame_stop=stop,
            projection=projection,  # type: ignore[arg-type]  # checked by project_frames
            n_frames=int(data.shape[0]),
        )
    else:
        raise BeadMapError(
            f"bead data must be one image (H, W) or a video (T, H, W), got shape {data.shape}"
        )
    if not np.isfinite(image).all():
        raise BeadMapError("the bead image contains non-finite values")

    # --- Stages 6 to 9, unchanged ------------------------------------------------
    height, width = image.shape
    donor_geom, acceptor_geom = _half_split(width, height, donor_side)
    donor_half, acceptor_half = split_channels(image[np.newaxis], donor_geom, acceptor_geom)
    # A one-frame stack: the detection image is that frame, normalised to [0, 1]
    # (the same image ``tether extract`` detects on for a one-frame movie).
    donor_det = detection_image(donor_half, block=1)
    acceptor_det = detection_image(acceptor_half, block=1)
    detect_kw = {
        "mode": detection_mode,
        "threshold": detection_threshold,
        "min_separation": min_separation,
    }
    donor_spots = detect_spots_by_mode(donor_det, **detect_kw)
    acceptor_spots = detect_spots_by_mode(acceptor_det, **detect_kw)

    n_donor, n_acceptor = len(donor_spots), len(acceptor_spots)
    if n_donor < min_bead_spots or n_acceptor < min_bead_spots:
        raise BeadMapError(
            f"too few bead spots: {n_donor} in the donor half and {n_acceptor} in the "
            f"acceptor half; the limit is {int(min_bead_spots)} in each half "
            "(min_bead_spots, PRD §11.2)"
        )

    if prealign == "similarity":
        prealign_tf = estimate_similarity_prealign(
            donor_det,
            acceptor_det,
            upsample_factor=prealign_upsample,
            low_sigma=prealign_low_sigma,
            high_sigma=prealign_high_sigma,
        )
    else:
        prealign_tf = estimate_translation_prealign(
            donor_det, acceptor_det, upsample_factor=prealign_upsample
        )
    paired = pair_control_points(donor_spots, acceptor_spots, tol=pair_tol, prealign=prealign_tf)
    if len(paired.reference) < 2:
        raise BeadMapError(
            f"only {len(paired.reference)} bead pair(s) matched (need >= 2) from "
            f"{n_donor} donor and {n_acceptor} acceptor spots; check donor_side, the "
            "detection settings or pair_tol"
        )

    reg_map = fit_registration_map(
        paired.reference,
        paired.moving,
        reference_channel=_DONOR_CHANNEL,
        moving_channel=_ACCEPTOR_CHANNEL,
        reference_geometry=donor_geom,
        moving_geometry=acceptor_geom,
        gate_px=gate_px,
        on_over_gate=on_over_gate,
        app_version=app_version,
        bead_file=bead_file,
    )

    # --- quality record + provenance ---------------------------------------------
    quality = map_quality(reg_map, paired.reference, paired.moving, field_shape=donor_det.shape)
    provenance = dict(reg_map.provenance)
    provenance["bead_source"] = source.to_dict()
    provenance["map_quality"] = quality.to_dict()
    provenance["bead_settings"] = {
        "donor_side": donor_side,
        "detection_mode": str(detection_mode),
        "detection_threshold": detection_threshold,
        "min_separation": min_separation,
        "prealign": prealign,
        "prealign_upsample": int(prealign_upsample),
        "prealign_low_sigma": float(prealign_low_sigma),
        "prealign_high_sigma": float(prealign_high_sigma),
        "pair_tol": float(pair_tol),
        "min_bead_spots": int(min_bead_spots),
        "n_donor_spots": int(n_donor),
        "n_acceptor_spots": int(n_acceptor),
    }
    return BeadMapResult(
        registration_map=replace(reg_map, provenance=provenance),
        quality=quality,
        source=source,
        image=image,
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bead_map_from_file(
    path: str | os.PathLike[str],
    *,
    frames: tuple[int, int] | None = None,
    projection: str = "max",
    **kwargs: Any,
) -> BeadMapResult:
    """Make a map from a bead TIFF: a multi-frame video or a one-page image.

    The source id is ``"sha256:<hex>"`` of the file's bytes, so it names the
    content and stays the same when the file moves. The file name is the default
    ``bead_file`` stamp. A one-page TIFF is an image (no frame range, no projection);
    a multi-page TIFF is a video reduced with ``frames`` and ``projection``. The
    video is projected block by block: an uncompressed file is memory-mapped, and
    a compressed one is decoded one block of the selected pages at a time, so a
    long video need not fit in memory or on disk. A TIFF whose pixels come in part
    from other files (a multi-file OME-TIFF) is refused, because the source id
    names this one file only. A TIFF of another layout (an RGB image, a multichannel
    or a focus stack: any series axes but ``YX``, ``IYX``, ``TYX`` or ``QYX``) is
    refused. The file is checked again after it is read: if its identity (device
    and inode), its size or its modification time differ from those before the
    hash, it is refused, because the source id would name other bytes.

    Parameters
    ----------
    path : str or os.PathLike
        The bead TIFF.
    frames : tuple of (int, int) or None, optional
        For a video only: the 0-based half-open frame range, in frames
        (``None``, the default, uses every frame).
    projection : {"max", "mean"}, optional
        For a video only: the projection (default ``"max"``).
    **kwargs
        Other keyword arguments of [`bead_map`][], such as ``donor_side`` or
        ``pair_tol``.

    Returns
    -------
    BeadMapResult
        The map, quality record, image and source record returned by [`bead_map`][].

    Raises
    ------
    BeadMapError
        If the file is missing at entry, the TIFF cannot be opened, its layout
        is unsupported or its pages span files, or its final state differs.
        Exceptions from [`bead_map`][] propagate unchanged.

    Examples
    --------
    With ``beads.tif`` a bead video of at least 50 frames:

    >>> result = bead_map_from_file("beads.tif", frames=(0, 50))
    >>> result.source.kind, result.source.frame_start, result.source.frame_stop
    ('video', 0, 50)
    >>> result.source.source_id.startswith("sha256:")
    True
    """
    import tifffile  # noqa: PLC0415

    path = Path(path)
    if not path.is_file():
        raise BeadMapError(f"bead file not found: {path}")
    before = _file_state(path)
    source_id = f"sha256:{_sha256_file(path)}"
    try:
        tif = tifffile.TiffFile(path)
    except (OSError, ValueError, tifffile.TiffFileError) as exc:  # not a TIFF, or unreadable
        raise BeadMapError(f"could not read the bead file {path.name}: {exc}") from exc
    with tif:
        series = tif.series[0]
        axes = str(series.axes)
        shape = tuple(int(n) for n in series.shape)
        if axes not in (_IMAGE_AXES, *_VIDEO_AXES):
            raise BeadMapError(
                f"{path.name}: expected one image (axes 'YX') or a video of frames "
                f"(axes {' / '.join(repr(a) for a in _VIDEO_AXES)}), got shape {shape} "
                f"(axes {axes!r}); channels, colour samples and focus planes are not frames"
            )
        if any(page is None or page.parent is not tif for page in series.pages):
            # A multi-file OME-TIFF: some pages are in (or missing from) other files,
            # which the sha256 source id of this one file would not name.
            raise BeadMapError(
                f"{path.name}: some of its pages are in other files (a multi-file "
                "OME-TIFF); give one file that holds every frame"
            )
        data: Any
        if len(shape) == 3 and shape[0] > 1 and series.dataoffset is None:
            # Compressed or scattered pages: decode only the selected frames, one
            # block at a time (project_frames), never the whole video.
            if len(series.pages) != shape[0]:
                raise BeadMapError(
                    f"{path.name}: {len(series.pages)} pages for {shape[0]} frames; "
                    "expected one page per frame"
                )
            data = _TiffFrames(tif, (shape[0], shape[1], shape[2]))
        else:
            try:
                # Uncompressed and contiguous: tifffile maps the file itself, so a
                # long video is reduced block by block without being loaded.
                data = tif.asarray(series=0, out="memmap")
            except (OSError, ValueError, tifffile.TiffFileError) as exc:
                raise BeadMapError(f"could not read the bead file {path.name}: {exc}") from exc
        if data.ndim == 3 and data.shape[0] == 1:
            data = data[0]  # a one-frame stack is one image
        if data.ndim == 2 and (frames is not None or projection != "max"):
            raise BeadMapError(f"{path.name} has one page; frames and projection apply to a video")
        kwargs.setdefault("bead_file", path.name)
        if data.ndim == 2:
            result = bead_map(data, source_id=source_id, **kwargs)
        else:
            result = bead_map(
                data, source_id=source_id, frames=frames, projection=projection, **kwargs
            )
    # The memory map is read lazily, so check after the projection: a file replaced
    # since it was hashed would make the source id name bytes that were not used.
    # Size and modification time alone miss a replacement that copies both, so the
    # file identity (device and inode) is compared too.
    if _file_state(path) != before:
        raise BeadMapError(f"{path.name} changed while it was read; run it again")
    return result


def _file_state(path: Path) -> tuple[FileIdentity | None, int, int]:
    """Return the identity, size and modification time of the file at ``path``."""
    # The identity (device and inode) comes from the one canonical helper. On a
    # file system with no inode numbers it is None on both sides and the
    # comparison falls back to size and modification time.
    from tether.project.lock import file_identity  # noqa: PLC0415

    stat = path.stat()
    return (file_identity(path), stat.st_size, stat.st_mtime_ns)
