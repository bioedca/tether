# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""FRET efficiency — corrected (accurate) and apparent (proximity ratio) (PRD §7.4).

The accurate-FRET efficiency turns the raw donor/acceptor intensities into an
absolute energy-transfer efficiency by applying the two photophysical corrections
Tether estimates (PRD §7.2, Appendix B.2; ``background → leakage α → δ(=0) → γ``):

    I_A,corr = I_A − α · I_D            (donor→acceptor leakage, additive; δ = 0, no ALEX)
    I_D,corr = I_D                      (bare background-subtracted donor — Tether's
                                         additive convention; see below)
    E        = I_A,corr / (I_A,corr + γ · I_D,corr)                       (PRD §7.4)

with **apparent E** = the same formula at ``α = δ = 0, γ = 1`` — i.e. the raw
proximity ratio ``A / (D + A)`` shown before any correction is available
[McCann2010][Hellenkamp2018]. Keeping both in one module means the dock, the
corrections pipeline, and analysis all compute E exactly one way: apparent E is
literally :func:`corrected_fret` with the identity factors, so there is no second
definition to drift.

The uncorrected proximity ratio is "internally consistent only if the photophysical
properties and instrument remain unchanged" and is *not* an absolute distance measure
[McCann2010]; the multi-laboratory smFRET benchmark [Hellenkamp2018] formalises the
correction procedure implemented here. Per-molecule γ from single-molecule
photobleaching is the most effective route to an absolute efficiency [McCann2010]
(estimated in :mod:`tether.fret.gamma`).

Donor convention (why bare ``I_D``, not ``I_D·(1+α)``)
------------------------------------------------------
Tether's additive scheme (PRD Appendix B.2) defines the corrected donor as the *bare*
background-subtracted donor ``I_D,corr = I_D``: the leakage subtraction only removes
donor-leaked photons *from the acceptor* (``I_A,corr = I_A − α·I_D``) and does not add
them back to the donor. γ (:mod:`tether.fret.gamma`) is defined consistently with this
``I_D,corr`` (it divides by the bare ``ΔI_D``), so the pair is internally
self-consistent — the ``(1+α)`` cancels between a group's γ definition and its ``E``.
Deep-LASI scales the donor by ``(1+α)`` instead; each convention is self-consistent,
so a cross-tool γ comparison must control for it (ADR-0028).

Kept in :mod:`tether.fret` (headless, Qt-free) so the GUI, the corrections pipeline,
and analysis all share one definition.

References
----------
[McCann2010] McCann, Choi, Zheng, Bahlke, Zhu, Nienhaus, Schuler & Weiss.
    "Optimizing methods to recover absolute FRET efficiency from immobilized single
    molecules." Biophysical Journal (2010).
[Hellenkamp2018] Hellenkamp et al. "Precision and accuracy of single-molecule
    FRET measurements — a multi-laboratory benchmark study." Nature Methods (2018).
"""

from __future__ import annotations

import numpy as np

__all__ = ["apparent_fret", "corrected_fret"]


def corrected_fret(
    donor: np.ndarray,
    acceptor: np.ndarray,
    *,
    alpha: float,
    gamma: float,
) -> np.ndarray:
    """Return the γ-corrected FRET efficiency ``I_A,corr / (I_A,corr + γ·I_D,corr)``.

    Applies the additive leakage correction ``I_A,corr = I_A − α·I_D`` and the
    detection-correction factor γ on the bare corrected donor ``I_D,corr = I_D``
    (PRD §7.4, Appendix B.2; δ = 0, single-laser — no ALEX direct-excitation term).

    Parameters
    ----------
    donor, acceptor
        Per-frame background-subtracted donor and acceptor intensities. Broadcast
        against each other, so scalars or matching-shape arrays are both accepted.
    alpha
        Donor→acceptor leakage factor α (from :mod:`tether.fret.leakage`). ``0``
        disables the leakage correction.
    gamma
        Detection-correction factor γ (from :mod:`tether.fret.gamma`). ``1`` leaves
        the donor/acceptor balance unchanged; with ``alpha=0`` this yields apparent E
        (see :func:`apparent_fret`).

    Returns
    -------
    numpy.ndarray
        ``float64`` corrected efficiency, same broadcast shape as the inputs. Frames
        whose corrected denominator ``I_A,corr + γ·I_D,corr`` is exactly zero yield
        ``NaN`` (the ratio is undefined there) rather than raising or fabricating a
        value — the caller draws those as gaps. No clipping to ``[0, 1]`` is applied:
        a corrected value slightly outside that range on a noisy frame is a real
        fluctuation, and hiding it would be a silent distortion.
    """
    donor = np.asarray(donor, dtype=np.float64)
    acceptor = np.asarray(acceptor, dtype=np.float64)
    i_a_corr = acceptor - float(alpha) * donor
    i_d_corr = donor
    denom = i_a_corr + float(gamma) * i_d_corr
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(denom != 0.0, i_a_corr / denom, np.nan)


def apparent_fret(donor: np.ndarray, acceptor: np.ndarray) -> np.ndarray:
    """Return the apparent FRET efficiency (proximity ratio) ``A / (D + A)``.

    The uncorrected efficiency shown before leakage/γ corrections are available —
    exactly :func:`corrected_fret` at the identity factors ``alpha=0, gamma=1``
    (PRD §7.4). Tether labels the FRET axis "apparent E" until M3 supplies the
    correction factors [McCann2010][Hellenkamp2018].

    Parameters
    ----------
    donor, acceptor
        Per-frame donor and acceptor intensities; broadcast against each other.

    Returns
    -------
    numpy.ndarray
        ``float64`` apparent efficiency, same broadcast shape as the inputs. Frames
        whose total intensity ``D + A`` is exactly zero yield ``NaN`` (the ratio is
        undefined there); no clipping to ``[0, 1]`` is applied (the proximity ratio
        may sit slightly outside that range on noisy frames).
    """
    return corrected_fret(donor, acceptor, alpha=0.0, gamma=1.0)
