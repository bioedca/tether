# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Spot detection (PRD Appendix E, Stages 2-4; §11.2).

Faithful NumPy/SciPy ports of Deep-LASI's selectable particle-detection methods
(`deeplasi/functions/mapping/findPart.m`'s ``method`` dispatch). Deep-LASI offers
several detectors behind the GUI ``Rbg_Detection`` radio group; Tether implements
them behind :class:`ParticleDetectionMode` so a movie can be extracted with the
method (and threshold) it was actually detected with:

* **mode 1 — wavelet** (:func:`detect_spots`): the class-default à trous /
  multiscale-product detector (`external/Wave_Partfind.m`), detailed below.
* **mode 2 — intensity** (:func:`detect_spots_intensity`): the legacy
  intensity-threshold detector (`findPart.m:21-28`) — threshold the detection
  image at ``t * max``, Crocker-Grier band-pass (`external/bpass.m`), re-threshold
  at 3 % of the band-pass max, erode, and take connected-component centroids.

:func:`detect_spots_by_mode` dispatches on :class:`ParticleDetectionMode`. All
modes share the Stage-4 post-detection tail (snap -> border -> NMS ->
``[x, y]``), mirroring ``findPart.m``'s shared post-switch block (lines 63-103).
The band-pass / radial-symmetry detector (mode 3, `find_part_bpass_sort.m`) is a
planned follow-up (ADR-0021).

The mode-1 wavelet path is a faithful port of
(`deeplasi/functions/external/Wave_Partfind.m`, `mapping/findPart.m`):

* **Stage 2 — detection image** (:func:`detection_image`): the "Cumulated" image,
  a moving-average max projection. Frames are grouped into non-overlapping **whole**
  blocks of ``block`` (default 50; a trailing partial block of ``T % block`` frames
  is dropped, as in ``cumIMG.m``), each block is mean-projected, the per-pixel
  **max** across block-means is taken, and the result is normalized to ``[0, 1]``.
  Too few frames (``T < block``) falls back to a sum projection. This suppresses
  single-frame noise/blinking while keeping a spot bright in >= 1 window
  (`tools/cumIMG.m:16-65`).

* **Stage 3 — spot detection** (:func:`detect_spots`): an undecimated *à trous* /
  starlet transform with the separable B3-spline kernel
  ``[1/16, 1/4, 3/8, 1/4, 1/16]`` dilated by ``2**(i-1)`` holes at scale ``i``;
  per-scale noise ``sigma = 2 * MAD`` hard-thresholds each wavelet plane; the
  detection mask is the logical **AND** of the significant pixels at scales 1 & 4
  (`bwmorph clean` -> drop isolated pixels); connected components are labelled and
  their centroids taken (`scipy.ndimage.label` + `center_of_mass`). The
  multiscale-product wavelet detector follows Olivo-Marin (2002, *Pattern
  Recognit.*); wavelet-segmentation + centroid localization follows Izeddin
  (2012).

* **Stage 4 — snap + guardrails** (within :func:`detect_spots`): an optional
  max-pixel **snap** (Gaussian sigma = 1, capped at 3 px) refines each centroid
  toward the local intensity maximum (`mapping/findPart.m:88-101`); then a border
  margin drops spots too close to the edge and an 8 px min-separation keeps only
  the brightest of each cluster (non-maximum suppression by local 3x3 sum). The
  guardrails run **after** the snap so the returned coordinates always satisfy
  the border + separation contract. Every coordinate-to-pixel snap uses MATLAB
  away-from-zero rounding (:func:`tether.imaging._rounding.round_half_away`) to
  match Deep-LASI's ``round`` exactly.

**Coordinate convention.** :func:`detect_spots` returns an ``(N, 2)`` float array
of ``[x, y]`` = ``[column, row]`` (Deep-LASI stores ``fliplr`` -> ``[x, y]``),
0-based, sorted by descending brightness. The radial-symmetry localizer
(Parthasarathy 2012, modes 3/4) is intentionally **not** ported here; mode 1 is
the faithful class default.
"""

from __future__ import annotations

from enum import StrEnum

import numpy as np
from scipy import ndimage, signal

from tether.imaging._rounding import round_half_away

__all__ = [
    "detection_image",
    "atrous_wavelet_planes",
    "b3_spline_kernel",
    "detect_spots",
    "detect_spots_intensity",
    "detect_spots_by_mode",
    "ParticleDetectionMode",
]

# Separable B3-spline scaling taps for the à trous transform (Wave_Partfind.m).
_B3_TAPS: tuple[float, ...] = (1 / 16, 1 / 4, 3 / 8, 1 / 4, 1 / 16)


class ParticleDetectionMode(StrEnum):
    """Selectable spot-detection method (Deep-LASI ``findPart.m`` ``method``).

    A ``str`` enum so the choice serializes verbatim into ``/settings/extraction``
    and round-trips through the CLI/`.tether` store. ``WAVELET`` is the Deep-LASI
    class default (`findPart.m` mode 1). ``BANDPASS`` (mode 3) is a planned
    follow-up (ADR-0021) and is intentionally not yet a member.
    """

    WAVELET = "wavelet"  # mode 1 — à trous multiscale product (class default)
    INTENSITY = "intensity"  # mode 2 — intensity-threshold + Crocker-Grier bandpass


def b3_spline_kernel(step: int) -> np.ndarray:
    """Return the 1-D B3-spline à trous kernel with ``step - 1`` holes per gap.

    At scale ``i`` the dilation is ``step = 2**(i-1)``: the five taps
    ``[1/16, 1/4, 3/8, 1/4, 1/16]`` sit at indices ``0, step, ..., 4*step`` and
    the gaps between them are zero-filled (the "holes" of the *trous*). The
    kernel sums to 1 (it is a low-pass scaling filter).
    """
    if step < 1:
        raise ValueError(f"step must be >= 1, got {step}")
    kernel = np.zeros(4 * step + 1, dtype=np.float64)
    kernel[::step] = _B3_TAPS
    return kernel


def _mad(values: np.ndarray) -> float:
    """Median absolute deviation about the median."""
    med = np.median(values)
    return float(np.median(np.abs(values - med)))


def detection_image(movie: np.ndarray, block: int = 50) -> np.ndarray:
    """Moving-average max-projection detection image, normalized to ``[0, 1]``.

    Parameters
    ----------
    movie:
        A single channel/half as a ``(T, H, W)`` array (any numeric dtype,
        including big-endian — it is cast to ``float64``).
    block:
        Moving-average window in frames (Deep-LASI ``MovingAverageWindowSize``,
        default 50). Frames are grouped into non-overlapping **whole** blocks; the
        trailing partial block (remainder ``T % block`` frames) is **dropped**,
        matching ``cumIMG.m`` (``reshape(...,movAvg,[])`` keeps only whole blocks).
        When ``T < block`` there is no whole block, so a sum projection is used.

    Returns
    -------
    np.ndarray
        ``(H, W)`` ``float64`` image in ``[0, 1]`` (all-zero if the projection
        is flat-zero).
    """
    movie = np.asarray(movie)
    if movie.ndim != 3:
        raise ValueError(f"movie must be 3-D (T, H, W), got shape {movie.shape}")
    if block < 1:
        raise ValueError(f"block must be >= 1, got {block}")
    data = movie.astype(np.float64, copy=False)
    n_frames = data.shape[0]

    if n_frames < block:
        # No whole block -> sum projection fallback (cumIMG.m's `isempty(B)` branch,
        # deeplasi/functions/tools/cumIMG.m:53-56).
        projection = data.sum(axis=0)
    else:
        # Keep only WHOLE blocks: the trailing ``T % block`` remainder frames are
        # dropped, matching cumIMG.m's
        # ``reshape(img(:,:,1:end-mod(s(3),movAvg)), s1, s2, movAvg, [])``
        # (deeplasi/functions/tools/cumIMG.m:49). A smaller partial block would
        # under-suppress temporal noise and could spike the per-pixel max, shifting
        # which spots the a trous detector finds relative to the Deep-LASI oracle.
        n_full = n_frames // block
        block_means = data[: n_full * block].reshape(n_full, block, *data.shape[1:]).mean(axis=1)
        projection = block_means.max(axis=0)

    peak = projection.max()
    if peak <= 0:
        return np.zeros_like(projection)
    return projection / peak


def atrous_wavelet_planes(image: np.ndarray, n_scales: int = 6, k: float = 2.0) -> list[np.ndarray]:
    """Hard-thresholded à trous wavelet planes (one-sided, bright-spot retaining).

    Returns ``n_scales - 1`` wavelet planes ``w_i = A_i - A_{i+1}`` (``A_1`` is
    the input image), each with coefficients below ``k * MAD`` zeroed. The
    threshold is one-sided (keeps ``w_i >= k*MAD``), so only bright (positive)
    structure survives — matching ``Wave_Partfind.m``'s ``tw(tw < sig) = 0``.
    """
    if n_scales < 2:
        raise ValueError(f"n_scales must be >= 2, got {n_scales}")
    approx = np.asarray(image, dtype=np.float64)
    if approx.ndim != 2:
        raise ValueError(f"image must be 2-D, got shape {approx.shape}")

    planes: list[np.ndarray] = []
    for i in range(1, n_scales):
        kernel = b3_spline_kernel(2 ** (i - 1))
        # Separable 2-D convolution; mode='reflect' == MATLAB 'symmetric' padding.
        smoothed = ndimage.convolve1d(approx, kernel, axis=0, mode="reflect")
        smoothed = ndimage.convolve1d(smoothed, kernel, axis=1, mode="reflect")
        wavelet = approx - smoothed
        threshold = k * _mad(wavelet)
        wavelet = np.where(wavelet < threshold, 0.0, wavelet)
        planes.append(wavelet)
        approx = smoothed
    return planes


def _suppress_neighbours(
    coords: np.ndarray, brightness: np.ndarray, min_separation: float
) -> np.ndarray:
    """Greedy non-maximum suppression: keep the brightest spot per cluster.

    ``coords`` is ``(N, 2)`` in ``(row, col)``; returns the kept-row indices
    (into ``coords``) in descending-brightness order. A candidate is accepted
    only if it is ``>= min_separation`` from every already-accepted spot.
    """
    order = np.argsort(brightness)[::-1]
    kept: list[int] = []
    kept_coords: list[np.ndarray] = []
    min_sq = min_separation * min_separation
    for idx in order:
        point = coords[idx]
        if all(np.sum((point - other) ** 2) >= min_sq for other in kept_coords):
            kept.append(int(idx))
            kept_coords.append(point)
    return np.asarray(kept, dtype=np.intp)


def _refine_snap(
    detection_img: np.ndarray, row: float, col: float, half: int = 5
) -> tuple[float, float]:
    """Max-pixel snap: nudge a centroid toward the Gaussian-smoothed local max.

    Crops a ``(2*half+1)`` window, Gaussian-smooths (sigma=1), finds the max, and
    if the offset from the crop centre is < 3 px snaps to ``round(centroid +
    offset)`` (`findPart.m:88-101`). Rounding is MATLAB away-from-zero
    (:func:`round_half_away`), matching the reference ``round`` at both the crop
    centre and the snap. Spots too close to the border to crop are returned
    unchanged.
    """
    height, width = detection_img.shape
    r0, c0 = int(round_half_away(row)), int(round_half_away(col))
    if r0 - half < 0 or r0 + half >= height or c0 - half < 0 or c0 + half >= width:
        return row, col
    crop = detection_img[r0 - half : r0 + half + 1, c0 - half : c0 + half + 1]
    smoothed = ndimage.gaussian_filter(crop, sigma=1.0)
    max_r, max_c = np.unravel_index(int(np.argmax(smoothed)), smoothed.shape)
    off_r, off_c = max_r - half, max_c - half
    if np.hypot(off_r, off_c) < 3:
        # NB Deep-LASI's XY is already an integer pixel here so its findPart.m:97
        # round() is a near-no-op; Tether snaps the RAW center_of_mass float, so the
        # away-from-zero tie-break is load-bearing (not a faithful-port oversight).
        return float(round_half_away(row + off_r)), float(round_half_away(col + off_c))
    return row, col


def _bandpass(image: np.ndarray, lnoise: float = 1.0, lobject: int = 7) -> np.ndarray:
    """Crocker-Grier band-pass filter (`deeplasi/functions/external/bpass.m`).

    Suppresses pixel noise (Gaussian smooth, width ``lnoise``) and long-wavelength
    background (boxcar of size ``2*lobject+1``) and returns ``max(gauss - boxcar,
    0)``, same shape as ``image`` with a ``lobject``-px zero border — the
    ``'valid'``-convolution margin re-padded with zeros, exactly as ``bpass.m``
    does. The 1-D Gaussian and boxcar taps are built verbatim from ``bpass.m`` and
    *not* renormalized. Used by the intensity-threshold detector (mode 2). See
    Crocker & Grier (1996), *J. Colloid Interface Sci.* 179:298.
    """
    image = np.asarray(image, dtype=np.float64)
    if lnoise <= 0:
        raise ValueError(f"lnoise must be > 0, got {lnoise}")
    w = int(round(lobject))
    if w < 1:
        raise ValueError(f"lobject must round to an integer >= 1, got {lobject}")
    n = 2 * w + 1
    height, width = image.shape
    if height < n or width < n:
        # Too small to band-pass without the kernel hanging off both edges.
        return np.zeros_like(image)

    idx = np.arange(n, dtype=np.float64)
    gauss_1d = np.exp(-(((idx - w) / (2.0 * lnoise)) ** 2)) / (2.0 * lnoise * np.sqrt(np.pi))
    gauss_2d = np.outer(gauss_1d, gauss_1d)
    box_2d = np.full((n, n), 1.0 / (n * n), dtype=np.float64)

    # MATLAB conv2(..., 'valid') == scipy.signal.convolve2d(..., mode='valid'):
    # only the part needing no zero-padding, then re-padded to full size below.
    smoothed = signal.convolve2d(image, gauss_2d, mode="valid")
    background = signal.convolve2d(image, box_2d, mode="valid")
    band = np.maximum(smoothed - background, 0.0)

    out = np.zeros_like(image)
    out[w : height - w, w : width - w] = band
    return out


def _finalize_candidates(
    coords: np.ndarray,
    detection_img: np.ndarray,
    *,
    min_separation: float,
    border_margin: int,
    refine: bool,
) -> np.ndarray:
    """Shared Stage-4 post-detection tail for every mode (`findPart.m:63-103`).

    Takes candidate centroids ``coords`` as ``(M, 2)`` ``(row, col)`` and applies,
    in order, the max-pixel snap (when ``refine``), the border guardrail, and
    brightness-ordered non-maximum suppression. Returns ``(N, 2)`` ``[x, y]`` =
    ``[col, row]`` sorted by descending brightness (empty ``(0, 2)`` if none
    survive).

    The snap runs **first** so the guardrails are authoritative over the FINAL
    coordinates: the snap can move a centroid up to ~3 px, which could otherwise
    push it back across the border or within ``min_separation`` of a neighbour.
    """
    coords = np.atleast_2d(np.asarray(coords, dtype=np.float64))
    if coords.shape[0] == 0:
        return np.empty((0, 2), dtype=np.float64)

    if refine:
        coords = np.array([_refine_snap(detection_img, r, c) for r, c in coords], dtype=np.float64)

    height, width = detection_img.shape
    in_bounds = (
        (coords[:, 0] >= border_margin)
        & (coords[:, 0] <= height - 1 - border_margin)
        & (coords[:, 1] >= border_margin)
        & (coords[:, 1] <= width - 1 - border_margin)
    )
    coords = coords[in_bounds]
    if coords.shape[0] == 0:
        return np.empty((0, 2), dtype=np.float64)

    # Brightness = local 3x3 sum of the detection image at each (snapped) centroid.
    # MATLAB away-from-zero rounding keeps the 3x3 window on the same pixel as
    # Deep-LASI. The rounding *direction* here only re-orders NMS tie-breaks (never
    # the returned coordinates), so its correctness is covered by the
    # round_half_away unit tests rather than a fragile NMS-ordering assertion.
    rows = np.clip(round_half_away(coords[:, 0]).astype(int), 1, height - 2)
    cols = np.clip(round_half_away(coords[:, 1]).astype(int), 1, width - 2)
    brightness = np.array(
        [detection_img[r - 1 : r + 2, c - 1 : c + 2].sum() for r, c in zip(rows, cols, strict=True)]
    )

    kept = _suppress_neighbours(coords, brightness, min_separation)
    coords = coords[kept]

    # Return [x, y] = [col, row], descending brightness (suppression order).
    return np.column_stack([coords[:, 1], coords[:, 0]])


def detect_spots(
    detection_img: np.ndarray,
    *,
    n_scales: int = 6,
    scale_pair: tuple[int, int] = (1, 4),
    min_separation: float = 8.0,
    border_margin: int = 1,
    refine: bool = True,
    k: float = 2.0,
) -> np.ndarray:
    """Detect spots in a detection image (Stages 3-4).

    Parameters
    ----------
    detection_img:
        ``(H, W)`` detection image (e.g. from :func:`detection_image`).
    n_scales:
        Number of à trous scales ``J`` (default 6 -> 5 wavelet planes).
    scale_pair:
        1-based scales whose significant pixels are AND-ed for the detection
        mask (default ``(1, 4)``; the pair must be valid for ``n_scales``).
    min_separation:
        Minimum spot separation in px; closer spots are suppressed keeping the
        brightest (default 8).
    border_margin:
        Drop spots within this many px of any edge (default 1).
    refine:
        Apply the Gaussian max-pixel snap (Stage 4) when True.
    k:
        MAD threshold multiplier (``sigma = k * MAD``, default 2).

    Returns
    -------
    np.ndarray
        ``(N, 2)`` ``float64`` array of ``[x, y]`` = ``[col, row]`` coordinates,
        0-based, sorted by descending brightness (empty ``(0, 2)`` if none).
    """
    detection_img = np.asarray(detection_img, dtype=np.float64)
    if detection_img.ndim != 2:
        raise ValueError(f"detection_img must be 2-D, got shape {detection_img.shape}")
    s_lo, s_hi = scale_pair
    if not (1 <= s_lo < n_scales and 1 <= s_hi < n_scales):
        raise ValueError(
            f"scale_pair {scale_pair} out of range for n_scales={n_scales} "
            f"(valid scales are 1..{n_scales - 1})"
        )

    planes = atrous_wavelet_planes(detection_img, n_scales=n_scales, k=k)
    mask = (planes[s_lo - 1] > 0) & (planes[s_hi - 1] > 0)

    # 8-connectivity labelling; 'clean' -> drop isolated single-pixel components.
    structure = np.ones((3, 3), dtype=bool)
    labels, n_labels = ndimage.label(mask, structure=structure)
    if n_labels == 0:
        return np.empty((0, 2), dtype=np.float64)
    sizes = ndimage.sum_labels(np.ones_like(mask, dtype=np.float64), labels, range(1, n_labels + 1))
    keep_labels = np.nonzero(sizes >= 2)[0] + 1
    if keep_labels.size == 0:
        return np.empty((0, 2), dtype=np.float64)
    centroids = ndimage.center_of_mass(mask.astype(np.float64), labels, keep_labels)
    coords = np.atleast_2d(np.asarray(centroids, dtype=np.float64))  # (M, 2) (row, col)

    return _finalize_candidates(
        coords,
        detection_img,
        min_separation=min_separation,
        border_margin=border_margin,
        refine=refine,
    )


def detect_spots_intensity(
    detection_img: np.ndarray,
    *,
    threshold: float = 0.5,
    lnoise: float = 1.0,
    lobject: int = 7,
    fine_threshold: float = 0.03,
    min_separation: float = 8.0,
    border_margin: int = 1,
    refine: bool = True,
) -> np.ndarray:
    """Intensity-threshold spot detection (Deep-LASI ``findPart.m`` mode 2).

    Faithful port of `deeplasi/functions/mapping/findPart.m:21-28` and its nested
    ``fourierImage`` thresholding (lines 107-115): zero pixels below
    ``threshold * max`` of the detection image, apply the Crocker-Grier band-pass
    (:func:`_bandpass`, ``lnoise``/``lobject``), binarize at ``fine_threshold`` of
    the band-pass max (3 % default), erode with a 3x3 element
    (``bwmorph(..., 'erode')``), and take the 8-connected component centroids
    (``regionprops(..., 'Centroid')``). The shared Stage-4 tail
    (:func:`_finalize_candidates`) then snaps, applies the border + min-separation
    guardrails, and orders by brightness.

    Parameters
    ----------
    detection_img:
        ``(H, W)`` detection image (e.g. from :func:`detection_image`).
    threshold:
        Deep-LASI ``DetectionThreshold`` (PRD §11.2): the fraction of the image
        max below which pixels are zeroed before the band-pass (default 0.5).
    lnoise, lobject:
        Crocker-Grier band-pass noise / object length scales (`bpass.m`; mode-2
        defaults ``1`` / ``7``).
    fine_threshold:
        Band-pass binarization level, as a fraction of the band-pass max
        (``findPart.m:24`` uses 3 %).
    min_separation, border_margin, refine:
        Shared Stage-4 guardrails (see :func:`detect_spots`).

    Returns
    -------
    np.ndarray
        ``(N, 2)`` ``float64`` ``[x, y]`` = ``[col, row]`` coordinates, 0-based,
        sorted by descending brightness (empty ``(0, 2)`` if none).
    """
    detection_img = np.asarray(detection_img, dtype=np.float64)
    if detection_img.ndim != 2:
        raise ValueError(f"detection_img must be 2-D, got shape {detection_img.shape}")
    if not 0.0 <= threshold < 1.0:
        raise ValueError(f"threshold must be in [0, 1), got {threshold}")
    if not 0.0 < fine_threshold < 1.0:
        raise ValueError(f"fine_threshold must be in (0, 1), got {fine_threshold}")

    peak = float(detection_img.max())
    if peak <= 0:
        return np.empty((0, 2), dtype=np.float64)
    thresholded = np.where(detection_img < threshold * peak, 0.0, detection_img)

    band = _bandpass(thresholded, lnoise=lnoise, lobject=lobject)
    band_peak = float(band.max())
    if band_peak <= 0:
        return np.empty((0, 2), dtype=np.float64)

    # 3x3 structuring element == MATLAB bwmorph 'erode' / regionprops 8-connectivity.
    structure = np.ones((3, 3), dtype=bool)
    binary = band > fine_threshold * band_peak
    eroded = ndimage.binary_erosion(binary, structure=structure)
    labels, n_labels = ndimage.label(eroded, structure=structure)
    if n_labels == 0:
        return np.empty((0, 2), dtype=np.float64)

    centroids = ndimage.center_of_mass(eroded.astype(np.float64), labels, range(1, n_labels + 1))
    coords = np.atleast_2d(np.asarray(centroids, dtype=np.float64))  # (M, 2) (row, col)
    return _finalize_candidates(
        coords,
        detection_img,
        min_separation=min_separation,
        border_margin=border_margin,
        refine=refine,
    )


def detect_spots_by_mode(
    detection_img: np.ndarray,
    *,
    mode: ParticleDetectionMode | str = ParticleDetectionMode.WAVELET,
    threshold: float = 0.5,
    min_separation: float = 8.0,
    border_margin: int = 1,
    refine: bool = True,
) -> np.ndarray:
    """Dispatch spot detection on :class:`ParticleDetectionMode`.

    ``mode`` accepts a :class:`ParticleDetectionMode` or its string value
    (``"wavelet"`` / ``"intensity"``); an unknown value raises ``ValueError`` at
    the enum coercion. ``threshold`` is consumed only by the intensity mode. The
    returned contract is identical across modes — ``(N, 2)`` ``[x, y]`` sorted by
    descending brightness.
    """
    mode = ParticleDetectionMode(mode)
    if mode is ParticleDetectionMode.INTENSITY:
        return detect_spots_intensity(
            detection_img,
            threshold=threshold,
            min_separation=min_separation,
            border_margin=border_margin,
            refine=refine,
        )
    # ParticleDetectionMode.WAVELET — the Deep-LASI class default.
    return detect_spots(
        detection_img,
        min_separation=min_separation,
        border_margin=border_margin,
        refine=refine,
    )
