# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Apparent FRET efficiency — the uncorrected proximity ratio (PRD §7.3 / §7.4).

At the MVP, before any leakage/gamma correction (which land at M3), the trace
dock displays *apparent* FRET efficiency: the ratio of acceptor to total
(donor + acceptor) intensity,

    E_app = I_A / (I_A + I_D).

This uncorrected quantity is the **proximity ratio** — "internally consistent
only if the photophysical properties and instrument remain unchanged" and *not*
an absolute distance measure [McCann2010]; the multi-laboratory smFRET benchmark
[Hellenkamp2018] formalises the correction procedure that turns it into an
accurate efficiency. It is exactly the gamma = 1, no-leakage special case of the
corrected formula ``E = I_A,corr / (I_A,corr + gamma * I_D,corr)`` (PRD §7.4), so
Tether labels the axis "apparent E" until M3 supplies the correction factors.

Kept in :mod:`tether.fret` (headless, Qt-free) so the GUI, the corrections
pipeline, and analysis all compute apparent E one way.

References
----------
[McCann2010] McCann, Choi, Zheng, Bahlke, Zhu, Nienhaus, Schuler & Weiss.
    "Recovering absolute FRET efficiency from single molecules: comparing
    methods of gamma correction." Biophysical Journal (2010).
[Hellenkamp2018] Hellenkamp et al. "Precision and accuracy of single-molecule
    FRET measurements — a multi-laboratory benchmark study." Nature Methods
    (2018).
"""

from __future__ import annotations

import numpy as np

__all__ = ["apparent_fret"]


def apparent_fret(donor: np.ndarray, acceptor: np.ndarray) -> np.ndarray:
    """Return the apparent FRET efficiency (proximity ratio) ``A / (D + A)``.

    Parameters
    ----------
    donor, acceptor
        Per-frame donor and acceptor intensities. Broadcast against each other,
        so scalars or matching-shape arrays are both accepted.

    Returns
    -------
    numpy.ndarray
        ``float64`` apparent efficiency, same broadcast shape as the inputs.
        Frames whose total intensity ``D + A`` is exactly zero yield ``NaN``
        (the ratio is undefined there) rather than raising or fabricating a
        value — the caller draws those as gaps. No clipping to ``[0, 1]`` is
        applied: the uncorrected proximity ratio may sit slightly outside that
        range on noisy frames, and hiding that is a silent distortion.
    """
    donor = np.asarray(donor, dtype=np.float64)
    acceptor = np.asarray(acceptor, dtype=np.float64)
    total = donor + acceptor
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(total != 0.0, acceptor / total, np.nan)
