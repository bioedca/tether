# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Engineered per-trace quality features (PRD §7.5 FR-ML).

The classical, GPU-free feature layer the per-condition quality **ranker** (PRD
§7.5, PLAN §9 M5) consumes: each molecule's windowed donor/acceptor intensity
trace is reduced to a small, deterministic feature vector. Engineered-feature
quality classification is the field-standard route to automated smFRET trace
selection — AutoSiM [Li2020], DeepFRET [Thomsen2020] and Deep-LASI [Wanninger2023]
all sort/select traces on features of exactly this kind — and PRD §7.5 names the
set explicitly (SNR, anticorrelation/XC magnitude, bleach-step count, FRET
mean/variance, dwell statistics, total intensity, edge/overlap, the
single-anticorrelated-acceptor-then-donor-bleach detector, and the
second-molecule-in-aperture flag).

Scope (M5 first PR). This module computes the **trace-derived** block — the
features that are a pure function of one molecule's windowed ``(donor, acceptor)``
pair, with no dependency on idealization or spatial geometry:

===========================  ============================================================
``n_frames``                 analysis-window length (frames)
``total_intensity``          mean per-frame total signal ``mean(D + A)``
``snr``                      total-intensity signal-to-noise ``mean(D + A) / std(D + A)``
``fret_mean``                mean apparent FRET efficiency over the window
``fret_var``                 variance (population, ddof=0) of apparent FRET efficiency
``anticorr_lag0``            zero-lag Pearson donor–acceptor correlation (negative ⇒ anti-phase)
``anticorr_lag1_magnitude``  ``|r[+1]|`` of the normalized cross-correlation
===========================  ============================================================

The idealization-coupled (dwell statistics), spatial (edge/overlap,
second-molecule-in-aperture) and composite (acceptor-then-donor bleach) features,
and the bleach-step count, are separate follow-up units — each carries its own
input surface and, for the composite bleach signature, its own science gate.

Design invariants (shared with :mod:`tether.analysis.crosscorr`):

* **Reuse, one definition.** ``fret_mean``/``fret_var`` reduce
  :func:`tether.fret.apparent_fret` (the single apparent-E definition), and the
  anticorrelation features are :func:`tether.analysis.cross_correlation` — no
  second copy of either can drift.
* **Never fabricate a value for undefined input.** A window too short (``< 2``
  frames), non-finite, or constant has an undefined correlation / SNR and yields
  ``NaN`` (never a fabricated ``0``), matching the crosscorr contract. The ranker
  handles missing features; a fabricated value would be a silent bug.

The ``snr`` definition. Under FRET, energy transfer conserves total emission — a
distance change that lowers the donor raises the acceptor in anti-phase — so the
*total* intensity ``D + A`` is approximately constant across FRET state
transitions, and its frame-to-frame fluctuation is dominated by
detection/shot noise rather than by real conformational dynamics (single-molecule
fluorescence noise is Poisson shot-noise dominated [Lee2022]). ``mean(D + A) /
std(D + A)`` is therefore a per-molecule signal-to-noise proxy that stays high for
a genuinely dynamic but *clean* trace — the property a quality feature needs (a
raw per-channel ``mean/std`` would instead penalise real FRET dynamics as
"noise"). It is a ranker feature, not an absolute physical SNR; a bleach-referenced
signal-step / background-noise SNR is a later refinement that pairs with the
photobleach block.

References
----------
[Li2020] Li, Zhang, Johnson-Buck & Walter. "Automatic classification and
    segmentation of single-molecule fluorescence time traces with deep learning."
    Nature Communications (2020) — AutoSiM smFRET trace selection.
[Thomsen2020] Thomsen et al. "DeepFRET, a software for rapid and automated
    single-molecule FRET data classification using deep learning." eLife (2020).
[Wanninger2023] Wanninger et al. "Deep-LASI: deep-learning assisted, single-molecule
    imaging analysis of multi-color DNA origami structures." Nature Communications (2023).
[Lee2022] Lee et al. "Characterization of Noise in a Single-Molecule Fluorescence
    Signal." The Journal of Physical Chemistry B (2022) — shot-noise-dominated
    single-molecule fluorescence noise.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from tether.analysis.crosscorr import cross_correlation
from tether.fret.efficiency import apparent_fret

__all__ = ["FEATURE_NAMES", "TraceFeatures", "compute_trace_features"]

#: The ordered engineered-feature schema — the column order of ``/features/table``
#: and the :meth:`TraceFeatures.as_vector` layout. New trace-derived features are
#: appended (additive) here; the store layer builds its dtype from this tuple, so
#: the feature vector has exactly one source of truth (PRD §7.5).
FEATURE_NAMES: tuple[str, ...] = (
    "n_frames",
    "total_intensity",
    "snr",
    "fret_mean",
    "fret_var",
    "anticorr_lag0",
    "anticorr_lag1_magnitude",
)


@dataclass(frozen=True)
class TraceFeatures:
    """One molecule's trace-derived engineered features (PRD §7.5).

    Every field mirrors a :data:`FEATURE_NAMES` entry; :meth:`as_vector` emits them
    in that order as a ``float64`` vector for the ranker. Undefined features (a
    window too short / non-finite / constant) are ``NaN``, never fabricated.
    """

    n_frames: int
    total_intensity: float
    snr: float
    fret_mean: float
    fret_var: float
    anticorr_lag0: float
    anticorr_lag1_magnitude: float

    def as_vector(self) -> np.ndarray:
        """The features as a ``float64`` vector in :data:`FEATURE_NAMES` order.

        ``n_frames`` is cast to float so the whole vector is one dtype — the
        ranker's feature matrix is a plain float array; the exact integer count is
        preserved in ``/features/table``'s typed column.
        """
        return np.array([float(getattr(self, name)) for name in FEATURE_NAMES], dtype=np.float64)


def compute_trace_features(donor: np.ndarray, acceptor: np.ndarray) -> TraceFeatures:
    """Reduce one windowed ``(donor, acceptor)`` intensity pair to its features.

    Parameters
    ----------
    donor, acceptor
        1-D per-frame intensity slices over the molecule's analysis window, of
        equal length. Already background-subtracted (the ``corrected`` trace
        quantity) at the call site.

    Returns
    -------
    TraceFeatures
        The trace-derived feature block. Features whose input is undefined (a
        window of ``< 2`` frames, non-finite, or constant) are ``NaN`` — never a
        fabricated ``0`` (mirrors :func:`tether.analysis.cross_correlation`).

    Raises
    ------
    ValueError
        ``donor`` and ``acceptor`` differ in length.
    """
    d = np.asarray(donor, dtype=np.float64).ravel()
    a = np.asarray(acceptor, dtype=np.float64).ravel()
    if d.shape != a.shape:
        raise ValueError(f"donor and acceptor must be the same length, got {d.shape} vs {a.shape}")
    n = int(d.shape[0])
    nan = float("nan")

    if n == 0:  # an empty window has no defined features (never a fabricated 0)
        return TraceFeatures(0, nan, nan, nan, nan, nan, nan)

    total = d + a
    total_intensity = float(total.mean())

    # SNR = mean(D + A) / std(D + A). Undefined for < 2 frames or a constant total
    # (population std 0 -> the ratio is undefined). Test the range directly rather
    # than `std == 0`, so a bit-identical non-dyadic constant (whose mean-subtraction
    # leaves a ~1e-17 residue) is still NaN, not a spurious huge SNR.
    if n >= 2 and float(total.max()) != float(total.min()):
        sd = float(np.std(total))  # population std (ddof=0)
        snr = float(total_intensity / sd) if sd > 0.0 else nan
    else:
        snr = nan

    # FRET mean/variance over the finite apparent-E frames. apparent_fret is NaN
    # where D + A == 0 (undefined ratio); reduce over the finite frames so a few
    # dead frames don't void the whole feature, and yield NaN only when no frame is
    # defined. ddof=0 population variance (a descriptive feature, not an estimator).
    e = apparent_fret(d, a)
    finite = np.isfinite(e)
    if bool(finite.any()):
        ev = e[finite]
        fret_mean = float(ev.mean())
        fret_var = float(ev.var())
    else:
        fret_mean = nan
        fret_var = nan

    # Anticorrelation: reuse the Pearson-normalized cross-correlation (one definition,
    # §7.7). cross_correlation raises on non-finite / < 2 frames, so guard those and
    # fall to NaN; a constant channel it already returns as NaN internally.
    if n >= 2 and bool(np.isfinite(d).all()) and bool(np.isfinite(a).all()):
        cc = cross_correlation(d, a, max_lag=1)
        anticorr_lag0 = float(cc.lag0)
        anticorr_lag1_magnitude = float(cc.lag1_magnitude)
    else:
        anticorr_lag0 = nan
        anticorr_lag1_magnitude = nan

    return TraceFeatures(
        n_frames=n,
        total_intensity=total_intensity,
        snr=snr,
        fret_mean=fret_mean,
        fret_var=fret_var,
        anticorr_lag0=anticorr_lag0,
        anticorr_lag1_magnitude=anticorr_lag1_magnitude,
    )
