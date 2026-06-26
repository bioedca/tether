# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""à trous wavelet spot detection (PRD Appendix E, Stages 2-4; §11.2).

A faithful NumPy/SciPy port of Deep-LASI's class-default (mode 1) detection path
(`deeplasi/functions/external/Wave_Partfind.m`, `mapping/findPart.m`):

* **Stage 2 — detection image** (:func:`detection_image`): the "Cumulated" image,
  a moving-average max projection. Frames are grouped into non-overlapping blocks
  of ``block`` (default 50), each block is mean-projected, the per-pixel **max**
  across block-means is taken, and the result is normalized to ``[0, 1]``. Too
  few frames (``T < block``) falls back to a sum projection. This suppresses
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

* **Stage 4 — guardrails + snap** (within :func:`detect_spots`): a border margin
  drops spots too close to the edge; an 8 px min-separation keeps only the
  brightest of each cluster (non-maximum suppression by local 3x3 sum); and an
  optional max-pixel **snap** (Gaussian sigma = 1, capped at 3 px) refines each
  centroid toward the local intensity maximum (`mapping/findPart.m:88-101`).

**Coordinate convention.** :func:`detect_spots` returns an ``(N, 2)`` float array
of ``[x, y]`` = ``[column, row]`` (Deep-LASI stores ``fliplr`` -> ``[x, y]``),
0-based, sorted by descending brightness. The radial-symmetry localizer
(Parthasarathy 2012, modes 3/4) is intentionally **not** ported here; mode 1 is
the faithful class default.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage

__all__ = [
    "detection_image",
    "atrous_wavelet_planes",
    "b3_spline_kernel",
    "detect_spots",
]

# Separable B3-spline scaling taps for the à trous transform (Wave_Partfind.m).
_B3_TAPS: tuple[float, ...] = (1 / 16, 1 / 4, 3 / 8, 1 / 4, 1 / 16)


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
        default 50). Frames are grouped into non-overlapping blocks; a final
        partial block (remainder ``T % block``) is kept as its own (smaller)
        block-mean so no signal is dropped.

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
        # Too few frames for a single window -> sum projection fallback.
        projection = data.sum(axis=0)
    else:
        n_full = n_frames // block
        block_means = data[: n_full * block].reshape(n_full, block, *data.shape[1:]).mean(axis=1)
        remainder = n_frames - n_full * block
        if remainder:
            tail = data[n_full * block :].mean(axis=0, keepdims=True)
            block_means = np.concatenate([block_means, tail], axis=0)
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
    offset)`` (`findPart.m:88-101`). Spots too close to the border to crop are
    returned unchanged.
    """
    height, width = detection_img.shape
    r0, c0 = int(round(row)), int(round(col))
    if r0 - half < 0 or r0 + half >= height or c0 - half < 0 or c0 + half >= width:
        return row, col
    crop = detection_img[r0 - half : r0 + half + 1, c0 - half : c0 + half + 1]
    smoothed = ndimage.gaussian_filter(crop, sigma=1.0)
    max_r, max_c = np.unravel_index(int(np.argmax(smoothed)), smoothed.shape)
    off_r, off_c = max_r - half, max_c - half
    if np.hypot(off_r, off_c) < 3:
        return float(round(row + off_r)), float(round(col + off_c))
    return row, col


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

    # Border guardrail (Stage 4).
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

    # Brightness = local 3x3 sum of the detection image at each rounded centroid.
    rows = np.clip(np.round(coords[:, 0]).astype(int), 1, height - 2)
    cols = np.clip(np.round(coords[:, 1]).astype(int), 1, width - 2)
    brightness = np.array(
        [detection_img[r - 1 : r + 2, c - 1 : c + 2].sum() for r, c in zip(rows, cols, strict=True)]
    )

    kept = _suppress_neighbours(coords, brightness, min_separation)
    coords = coords[kept]

    if refine:
        coords = np.array([_refine_snap(detection_img, r, c) for r, c in coords], dtype=np.float64)

    # Return [x, y] = [col, row], descending brightness (suppression order).
    return np.column_stack([coords[:, 1], coords[:, 0]])
