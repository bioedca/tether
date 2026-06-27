# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""tether.imaging — native movie-to-trace extraction and registration (PRD §4.2).

Mirrors the Deep-LASI extraction pipeline (PRD Appendix E): per-channel split,
moving-average max-projection detection image, à trous wavelet spot detection,
a 21x21 aperture (PSF disk r=3) with annular background, and Sum integration.
Plus registration: native bead control-points, phase-correlation prealign,
nearest-neighbour pairing, a degree-2 polynomial map (forward + inverse with a
numeric RMS-residual gate) or an imported ``.tmap``; and donor-anchored
colocalization.
"""

from __future__ import annotations

from tether.imaging.aperture import (
    IntegratedTraces,
    aperture_masks,
    integrate_traces,
)
from tether.imaging.detect import (
    atrous_wavelet_planes,
    b3_spline_kernel,
    detect_spots,
    detection_image,
)
from tether.imaging.register import (
    PolyTransform2D,
    TmapChannel,
    fit_polynomial_transform,
    point_rms,
    poly_basis_deg2,
    read_tmap,
)

__all__ = [
    "IntegratedTraces",
    "PolyTransform2D",
    "TmapChannel",
    "aperture_masks",
    "atrous_wavelet_planes",
    "b3_spline_kernel",
    "detect_spots",
    "detection_image",
    "fit_polynomial_transform",
    "integrate_traces",
    "point_rms",
    "poly_basis_deg2",
    "read_tmap",
]
