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
