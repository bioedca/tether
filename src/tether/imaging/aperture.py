# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Aperture geometry + Sum integration (PRD Appendix E Stages 5, 11-15; §11.2).

A faithful NumPy/SciPy port of Deep-LASI's aperture photometry — the second half
of the M0.5(b) extraction preview, fed by the :mod:`tether.imaging.detect` spot
coordinates:

* **Aperture geometry** (:func:`aperture_masks`): a ``21x21`` window holding a
  central **PSF disk** (Euclidean radius 3 -> 29 px) and a concentric
  **background ring** (inner 6, outer 8 -> 84 px), with a deliberate **dead-zone**
  gap ``3 < d <= 6`` between them so the ring samples background uncontaminated
  by the PSF tail. The boundary conventions (``d <= r`` for the disk,
  ``r < d <= R`` for the ring) mirror ``deeplasi/functions/filtering/circ.m``.

* **Sum integration** (:func:`integrate_traces`, ``deeplasi/functions/traces/
  extractTracesC.m:13-33``): per frame, the **total** is the disk sum
  ``TOT = sum(crop * disk)``; the **background** is the ring mean of a
  **10-frame uniform temporal moving average** of the crop (replicate edges),
  scaled by the disk pixel count ``BG = bg_ring_mean * N_psf``; the corrected
  intensity is the top-hat ``I = TOT - BG``. Per-molecule cropping mirrors
  ``extractTraces.m:9-25`` (round the coordinate, crop the aperture window;
  out-of-frame apertures yield an all-zero, ``valid=False`` trace).

Aperture-based background estimation with a concentric annulus, and temporal
local background subtraction, are established single-molecule TIRF/FRET intensity
methods (Preus 2016, *Biophys. J.*; Isaacoff 2019, *Biophys. J.*).

**Coordinate convention.** :func:`integrate_traces` consumes ``(N, 2)`` ``[x, y]``
= ``[column, row]`` coordinates — exactly :func:`tether.imaging.detect.detect_spots`'
output — and crops at ``(row=round(y), col=round(x))``.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import uniform_filter1d

from tether.imaging._rounding import round_half_away

__all__ = [
    "IntegratedTraces",
    "aperture_masks",
    "integrate_traces",
]


@dataclass(frozen=True)
class IntegratedTraces:
    """Per-molecule Sum-integration result (one row per input coordinate).

    Attributes
    ----------
    intensity:
        ``(N, T)`` corrected intensity ``I = TOT - BG`` (``float64``).
    total:
        ``(N, T)`` uncorrected disk sum ``TOT``.
    background:
        ``(N, T)`` subtracted background ``BG = bg_ring_mean * N_psf``.
    valid:
        ``(N,)`` bool; ``False`` where the aperture window fell outside the
        frame (that row's traces are all-zero).
    """

    intensity: np.ndarray
    total: np.ndarray
    background: np.ndarray
    valid: np.ndarray


def aperture_masks(
    window: int = 21,
    *,
    disk_radius: float = 3.0,
    ring_inner: float = 6.0,
    ring_outer: float = 8.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Build the boolean PSF-disk and background-ring masks.

    Parameters
    ----------
    window:
        Odd side length of the square aperture grid (default 21).
    disk_radius:
        PSF disk radius; pixels with Euclidean distance ``d <= disk_radius`` from
        the centre are the disk (default 3 -> 29 px).
    ring_inner, ring_outer:
        Background ring radii; pixels with ``ring_inner < d <= ring_outer`` are
        the ring (default 6 / 8 -> 84 px). The gap ``disk_radius < d <= ring_inner``
        is the dead-zone.

    Returns
    -------
    tuple[np.ndarray, np.ndarray]
        ``(disk_mask, ring_mask)``, each ``(window, window)`` bool.
    """
    if window < 1 or window % 2 == 0:
        raise ValueError(f"window must be a positive odd integer, got {window}")
    if not (0 < disk_radius <= ring_inner < ring_outer):
        raise ValueError(
            "radii must satisfy 0 < disk_radius <= ring_inner < ring_outer, got "
            f"disk_radius={disk_radius}, ring_inner={ring_inner}, ring_outer={ring_outer}"
        )
    if 2 * ring_outer > window:
        raise ValueError(f"ring (2*{ring_outer}) does not fit in a {window}px window")

    centre = window // 2
    rows, cols = np.mgrid[0:window, 0:window]
    dist = np.hypot(rows - centre, cols - centre)
    disk = dist <= disk_radius
    ring = (dist > ring_inner) & (dist <= ring_outer)
    if not ring.any():
        raise ValueError(
            f"background ring is empty for radii ({ring_inner}, {ring_outer}] in a "
            f"{window}px window; choose radii that enclose pixels"
        )
    return disk, ring


def integrate_traces(
    movie: np.ndarray,
    coords: np.ndarray,
    *,
    window: int = 21,
    disk_radius: float = 3.0,
    ring_inner: float = 6.0,
    ring_outer: float = 8.0,
    bg_window: int = 10,
) -> IntegratedTraces:
    """Extract Sum-integrated intensity traces at ``coords`` from ``movie``.

    Parameters
    ----------
    movie:
        ``(T, H, W)`` **raw** image stack of non-negative intensities (any numeric
        dtype, incl. big-endian; cast to ``float64``) — as in Deep-LASI, the ring
        background averages the positive in-ring pixels.
    coords:
        ``(N, 2)`` ``[x, y]`` = ``[col, row]`` spot coordinates (e.g. from
        :func:`tether.imaging.detect.detect_spots`).
    window, disk_radius, ring_inner, ring_outer:
        Aperture geometry, passed to :func:`aperture_masks`.
    bg_window:
        Temporal moving-average window for the background estimate, in frames
        (Deep-LASI default 10, replicate edges).

    Returns
    -------
    IntegratedTraces
        Per-molecule ``intensity`` / ``total`` / ``background`` ``(N, T)`` arrays
        plus a ``(N,)`` ``valid`` mask.
    """
    movie = np.asarray(movie)
    if movie.ndim != 3:
        raise ValueError(f"movie must be 3-D (T, H, W), got shape {movie.shape}")
    if bg_window < 1:
        raise ValueError(f"bg_window must be >= 1, got {bg_window}")
    coords = np.atleast_2d(np.asarray(coords, dtype=np.float64))
    if coords.size == 0:
        empty = np.empty((0, movie.shape[0]), dtype=np.float64)
        return IntegratedTraces(empty, empty.copy(), empty.copy(), np.empty(0, dtype=bool))
    if coords.ndim != 2 or coords.shape[1] != 2:
        raise ValueError(f"coords must be (N, 2) [x, y], got shape {coords.shape}")

    disk, ring = aperture_masks(
        window, disk_radius=disk_radius, ring_inner=ring_inner, ring_outer=ring_outer
    )
    n_psf = int(disk.sum())
    half = window // 2
    n_frames, height, width = movie.shape
    n_mol = coords.shape[0]

    intensity = np.zeros((n_mol, n_frames), dtype=np.float64)
    total = np.zeros((n_mol, n_frames), dtype=np.float64)
    background = np.zeros((n_mol, n_frames), dtype=np.float64)
    valid = np.zeros(n_mol, dtype=bool)

    for i in range(n_mol):
        col = int(round_half_away(coords[i, 0]))
        row = int(round_half_away(coords[i, 1]))
        if row - half < 0 or row + half >= height or col - half < 0 or col + half >= width:
            continue  # aperture falls outside the frame -> zero trace, valid=False
        crop = movie[:, row - half : row + half + 1, col - half : col + half + 1].astype(
            np.float64, copy=False
        )
        # 10-frame uniform temporal moving average (origin=0 -> window [i-5, i+4];
        # mode='nearest' replicates edge frames == MATLAB imfilter 'replicate').
        bg_smoothed = uniform_filter1d(crop, size=bg_window, axis=0, mode="nearest", origin=0)

        tot = (crop * disk).sum(axis=(1, 2))
        # Ring background = mean over the POSITIVE in-ring pixels, faithful to
        # Deep-LASI `mean(bg(bg>0))` (extractTracesC.m:22). `movie` is raw,
        # non-negative intensity (as in Deep-LASI), so this equals the full ring
        # mean except where a ring pixel is exactly 0 -- which the reference drops.
        ring_vals = bg_smoothed[:, ring]  # (T, n_ring)
        positive = ring_vals > 0
        counts = positive.sum(axis=1)
        sums = np.where(positive, ring_vals, 0.0).sum(axis=1)
        bg_mean = np.where(counts > 0, sums / np.maximum(counts, 1), 0.0)
        bg = bg_mean * n_psf

        total[i] = tot
        background[i] = bg
        intensity[i] = tot - bg
        valid[i] = True

    return IntegratedTraces(intensity, total, background, valid)
