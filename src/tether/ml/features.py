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

The **spatial crowding** block (``neighbor_distance``, ``aperture_overlap``) is a
*population* function — a molecule's donor spot relative to its same-movie
neighbours — computed by :func:`compute_spatial_features` (reusing the audited
:func:`tether.analysis.overlap.neighbor_report`) rather than the per-molecule
:func:`compute_trace_features`; the store layer writes both blocks to the one
``/features/table`` in :data:`FEATURE_NAMES` order. The remaining PRD §7.5
features — the edge-proximity readout (needs the donor channel's frame bounds, a
separate coordinate surface), idealization-coupled dwell statistics, the
bleach-step count, and the composite single-anticorrelated-acceptor-then-donor-bleach
detector (its own science gate) — are separate follow-up units.

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
from typing import TYPE_CHECKING

import numpy as np

from tether.analysis.crosscorr import cross_correlation
from tether.analysis.overlap import (
    APERTURE_OVERLAP_FACTOR,
    DEFAULT_APERTURE_RADIUS,
    neighbor_report,
)
from tether.fret.efficiency import apparent_fret

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "APERTURE_OVERLAP_FACTOR",
    "DEFAULT_APERTURE_RADIUS",
    "FEATURE_NAMES",
    "SPATIAL_FEATURE_NAMES",
    "TRACE_FEATURE_NAMES",
    "SpatialFeatures",
    "TraceFeatures",
    "compute_spatial_features",
    "compute_trace_features",
]

#: The ordered **trace-derived** feature schema — a pure function of one molecule's
#: windowed ``(donor, acceptor)`` pair, the :meth:`TraceFeatures.as_vector` layout.
#: New trace-derived features are appended (additive) here.
TRACE_FEATURE_NAMES: tuple[str, ...] = (
    "n_frames",
    "total_intensity",
    "snr",
    "fret_mean",
    "fret_var",
    "anticorr_lag0",
    "anticorr_lag1_magnitude",
)

#: The ordered **spatial** feature schema — a function of a molecule's position
#: relative to its *same-movie* neighbours, the :meth:`SpatialFeatures.as_vector`
#: layout. Appended after the trace block in :data:`FEATURE_NAMES`.
SPATIAL_FEATURE_NAMES: tuple[str, ...] = (
    "neighbor_distance",
    "aperture_overlap",
)

#: The full ordered engineered-feature schema written to ``/features/table`` — the
#: trace-derived block followed by the spatial block. The store layer builds its
#: compound dtype and the ranker's feature-matrix column order from this tuple, so
#: the feature vector has exactly one source of truth (PRD §7.5).
FEATURE_NAMES: tuple[str, ...] = TRACE_FEATURE_NAMES + SPATIAL_FEATURE_NAMES


@dataclass(frozen=True)
class TraceFeatures:
    """One molecule's trace-derived engineered features (PRD §7.5).

    Every field mirrors a :data:`TRACE_FEATURE_NAMES` entry; :meth:`as_vector` emits
    them in that order as a ``float64`` vector for the ranker. Undefined features (a
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
        """The features as a ``float64`` vector in :data:`TRACE_FEATURE_NAMES` order.

        ``n_frames`` is cast to float so the whole vector is one dtype — the
        ranker's feature matrix is a plain float array; the exact integer count is
        preserved in ``/features/table``'s typed column.
        """
        return np.array(
            [float(getattr(self, name)) for name in TRACE_FEATURE_NAMES], dtype=np.float64
        )


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
        ``donor`` or ``acceptor`` is not 1-D, or they differ in length.
    """
    # Validate ndim BEFORE any flatten: a bare ``.ravel()`` would collapse a 2-D
    # input to 1-D, so two differently-shaped multi-D inputs with the same element
    # count (e.g. (2, 3) and (3, 2)) would pass the length check and be silently
    # misaligned into one feature vector — the opposite of this module's "never
    # fabricate a value for malformed input" contract. Fail loudly instead.
    d = np.asarray(donor, dtype=np.float64)
    a = np.asarray(acceptor, dtype=np.float64)
    if d.ndim != 1 or a.ndim != 1:
        raise ValueError(f"donor and acceptor must be 1-D, got shapes {d.shape} and {a.shape}")
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


@dataclass(frozen=True)
class SpatialFeatures:
    """One molecule's spatial (crowding) quality features (PRD §7.5).

    A quality trace is a *single* donor–acceptor pair cleanly separated from its
    neighbours: a second emitter whose integration aperture overlaps this one's
    contaminates the integrated intensity, and automated smFRET trace selectors
    screen exactly such spatial/photophysical artifacts [Li2020]. These features
    surface that signal to the ranker. Every field mirrors a
    :data:`SPATIAL_FEATURE_NAMES` entry; :meth:`as_vector` emits them in that order.

    Fields
    ------
    neighbor_distance
        Centre-to-centre distance (px) from this molecule's donor spot to the
        nearest *other* donor spot **in the same movie** — a continuous crowding
        readout (small ⇒ crowded/contaminated). ``NaN`` (never a fabricated ``0``)
        when the molecule is the only one in its movie, or its coordinate is
        non-finite, so "no neighbour" is undefined rather than falsely "very close".
    aperture_overlap
        ``1.0`` when that nearest neighbour's aperture overlaps this molecule's
        (the "second-molecule-in-aperture" flag), else ``0.0``; ``NaN`` when the
        coordinate is non-finite. A lone molecule has no overlap → ``0.0`` (defined:
        no second molecule), even though its ``neighbor_distance`` is ``NaN``.
    """

    neighbor_distance: float
    aperture_overlap: float

    def as_vector(self) -> np.ndarray:
        """The features as a ``float64`` vector in :data:`SPATIAL_FEATURE_NAMES` order."""
        return np.array(
            [float(getattr(self, name)) for name in SPATIAL_FEATURE_NAMES], dtype=np.float64
        )


def compute_spatial_features(
    coords: np.ndarray,
    *,
    movie_ids: Sequence[object] | np.ndarray,
    aperture_radius: float = DEFAULT_APERTURE_RADIUS,
) -> list[SpatialFeatures]:
    """Per-molecule spatial (crowding) features over a population of donor spots.

    Unlike :func:`compute_trace_features` (a per-molecule pure function), crowding is
    a **population** property: it depends on where a molecule sits relative to the
    other molecules in its movie. The nearest-neighbour geometry reuses the audited
    :func:`tether.analysis.overlap.neighbor_report` (the M2 static overlap view),
    grouped by ``movie_ids`` so molecules in different movies never neighbour each
    other (§5.2). The aperture-overlap test is settled geometry — two PSF disks of
    radius ``aperture_radius`` overlap iff their centres are within
    ``APERTURE_OVERLAP_FACTOR × aperture_radius`` — so the only free input is the
    aperture radius (an existing §11.2 extraction parameter), not a new threshold.

    Parameters
    ----------
    coords
        ``(N, 2)`` ``[x, y]`` donor-spot centres (the ``/molecules`` ``donor_xy``
        convention, §5.1), one row per molecule.
    movie_ids
        Length-``N`` group labels (each molecule's ``movie_id``); the neighbour
        search is confined within a group.
    aperture_radius
        PSF-disk radius in px (default :data:`DEFAULT_APERTURE_RADIUS`); must be
        finite and positive.

    Returns
    -------
    list[SpatialFeatures]
        Row-aligned to ``coords`` (length ``N``). **Never drops a molecule**: a
        non-finite coordinate yields an all-``NaN`` :class:`SpatialFeatures`
        (excluded from the neighbour search so it can neither poison the KDTree nor
        be fabricated a position), and a lone molecule yields
        ``neighbor_distance=NaN`` / ``aperture_overlap=0.0``.

    Raises
    ------
    ValueError
        ``coords`` is not ``(N, 2)``, ``movie_ids`` is not length ``N``, or
        ``aperture_radius`` is not finite and positive.
    """
    xy = np.asarray(coords, dtype=np.float64)
    if xy.ndim != 2 or xy.shape[1] != 2:
        raise ValueError(f"coords must be an (N, 2) [x, y] array, got shape {xy.shape}")
    n = xy.shape[0]
    labels = np.asarray(movie_ids)
    if labels.shape != (n,):
        raise ValueError(f"movie_ids must be length {n} to match coords, got shape {labels.shape}")
    if not (np.isfinite(aperture_radius) and float(aperture_radius) > 0.0):
        raise ValueError(f"aperture_radius must be finite and positive, got {aperture_radius!r}")

    nan = float("nan")
    out = [SpatialFeatures(nan, nan) for _ in range(n)]

    # neighbor_report rejects any non-finite coordinate (a NaN must never poison the
    # KDTree), so run it over the finite-coordinate rows only and leave a molecule
    # with an unknown position as all-NaN — reported, never dropped or fabricated.
    finite = np.isfinite(xy).all(axis=1)
    if not bool(finite.any()):
        return out
    rows = np.flatnonzero(finite)
    report = neighbor_report(xy[rows], aperture_radius=aperture_radius, groups=labels[rows])
    for local, row in enumerate(rows):
        dist = float(report.nn_distance[local])
        out[int(row)] = SpatialFeatures(
            # inf = the only molecule in its movie: no neighbour distance is defined.
            neighbor_distance=dist if np.isfinite(dist) else nan,
            aperture_overlap=1.0 if bool(report.overlaps[local]) else 0.0,
        )
    return out
