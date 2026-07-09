# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Raw FRET cloud — M6 PR-5a, FR-ANALYZE (PRD §7.7).

A **consolidated pre-idealization QC view** of a population's per-frame FRET: the
pooled scatter of ``(time-in-window, apparent E)`` points over every accepted
molecule, summarized by a 2-D kernel-density estimate and its highest-density-region
(HDR) percentile contours. It answers the model-free question "does the FRET signal
sit in stable bands, and where does the population's density concentrate?" *before*
any HMM is committed — the counterpart to the A2 time-vs-signal heatmap
(:func:`tether.analysis.histogram.time_signal_histogram2d`), but as a smoothed density
with credible-region contours rather than a raw occupancy histogram.

Unlike the B1 TDP / B3 / C1 model views, the cloud reads **no idealization**: it pools
the observed ``apparent_fret`` over each molecule's analysis window (the pre-idealization
signal), applying only the §7.5 curation filter (rejected excluded by default). "Raw"
here means *pre-idealization*, not raw intensities — the E is computed from the chosen
(corrected, by default) ``/traces`` channels via
:func:`tether.fret.efficiency.apparent_fret`.

Two estimators are attached (both self-describing for NFR-REPRO):

* **2-D KDE** — a Gaussian kernel-density estimate evaluated on a ``(time_bins,
  signal_bins)`` grid. gaussian_kde's covariance-scaled bandwidth [Scott1992] absorbs
  the very different time-vs-E scales, so no manual axis normalization is needed. The
  estimate is fit on the points **within the grid range only**: ``apparent_fret`` is
  deliberately un-clipped (:func:`tether.fret.efficiency.apparent_fret`), so bleached /
  blinking frames with a near-zero ``D + A`` produce finite but extreme E far outside
  ``[-0.25, 1.25]``; left in, those (invisible, off-grid) outliers would inflate Scott's
  covariance-scaled bandwidth and silently over-smooth or merge the very bands the view
  exists to reveal. They are counted in :attr:`~RawFretCloud.n_out_of_range` (a QC signal
  in its own right) but excluded from the fit. The surface is ``None`` (never a crash,
  never a fabricated field) when the estimate is undefined — fewer than two in-range
  points, or a singular covariance (all points share one time or one E) — the guard the
  B3 KDE overlay uses.
* **HDR percentile contours** [Hyndman1996][Haselsteiner2017] — the density threshold
  ``level_p`` whose super-level set ``{f >= level_p}`` encloses coverage ``p`` of the
  cloud's mass, for each requested ``p`` (default 50% and 95%). Computed by the
  numerical-grid density-quantile method [Haselsteiner2017]: sort the grid cells by
  density, accumulate ``density x cell_area`` until the cumulative fraction of the
  in-grid mass reaches ``p``, and take the crossing density. The GUI draws the
  ``f = level_p`` isocontours; a smaller coverage yields a higher threshold, so the
  returned levels decrease as coverage increases (the smallest region enclosing the
  most probable mass, [Hyndman1996]).

The **alpha-shape** support boundary and the **k-vs-RMSE elbow** state-count hint
(PRD §7.7) are the second half of the raw-cloud deliverable and land in PR-5b; this
module is their pooled-cloud substrate.

:func:`raw_fret_cloud` is the pure-array core (an iterable of per-molecule windowed
apparent-E arrays -> a :class:`RawFretCloud`); :func:`population_raw_fret_cloud` is the
``.tether`` store entry point (curation filter, channel -> apparent-E).

References
----------
[Hyndman1996] Hyndman RJ. "Computing and graphing highest density regions." The
    American Statistician 50(2):120-126 (1996) — an HDR is the smallest region of a
    given probability coverage; the classical density-quantile estimator thresholds the
    density.
[Haselsteiner2017] Haselsteiner AF, Ohlendorf J-H, Wosniok W, Thoben K-D. "Deriving
    environmental contours from highest density regions." Coastal Engineering 123:42-51
    (2017) — the numerical-grid HDR: discretize the variable space, weight each cell by
    its probability, and enclose the smallest-volume region of the target coverage.
[Scott1992] Scott DW. "Multivariate Density Estimation." Wiley (1992) — the
    covariance-scaled (Scott's-rule) bandwidth used by gaussian_kde.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Iterable, Sequence
    from os import PathLike

    from tether.project.core import Project

    ProjectRef = Project | str | PathLike[str]

__all__ = [
    "DEFAULT_CLOUD_BW_METHOD",
    "DEFAULT_CLOUD_HDR_COVERAGES",
    "DEFAULT_CLOUD_SIGNAL_BINS",
    "DEFAULT_CLOUD_SIGNAL_RANGE",
    "DEFAULT_CLOUD_TIME_BINS",
    "DEFAULT_CLOUD_TIME_DT",
    "RawFretCloud",
    "population_raw_fret_cloud",
    "raw_fret_cloud",
]

#: KDE-surface resolution across the E (signal) axis. A rendering fidelity default
#: (like the A1 :data:`~tether.analysis.histogram.DEFAULT_NBINS`), **not** a PRD §11.2
#: science tunable.
DEFAULT_CLOUD_SIGNAL_BINS = 100

#: E (signal) range of the cloud, matching the A1 / TDP full range
#: (:data:`tether.analysis.histogram.DEFAULT_RANGE`) so the cloud shares an E axis with
#: the FRET histogram. A rendering default, **not** a §11.2 tunable.
DEFAULT_CLOUD_SIGNAL_RANGE: tuple[float, float] = (-0.25, 1.25)

#: KDE-surface resolution across the time axis. A rendering fidelity default, **not** a
#: §11.2 tunable. The time *extent* is taken from the data (0 .. longest window),
#: unlike the A2 heatmap's fixed ``time_bins`` truncation, so the whole cloud is shown.
DEFAULT_CLOUD_TIME_BINS = 100

#: Frame duration for the time axis (``time = frame_index * time_dt``); frames map to
#: columns exactly as the A2 heatmap (:data:`~tether.analysis.histogram.DEFAULT_TIME_DT`).
DEFAULT_CLOUD_TIME_DT = 1.0

#: HDR contour coverages drawn by default: the 50% and 95% highest-density regions
#: [Hyndman1996]. A rendering default, **not** a §11.2 tunable.
DEFAULT_CLOUD_HDR_COVERAGES: tuple[float, ...] = (0.5, 0.95)

#: gaussian_kde bandwidth rule (Scott's rule) [Scott1992]. A rendering default, **not**
#: a §11.2 tunable.
DEFAULT_CLOUD_BW_METHOD = "scott"


@dataclass(frozen=True)
class RawFretCloud:
    """A pooled ``(time, apparent-E)`` FRET cloud with a KDE surface + HDR levels.

    :attr:`points` is the raw scatter — one row ``(time, E)`` per finite per-frame
    sample, kept so the cloud and any statistic are reproducible (NFR-REPRO) and the
    GUI can always draw the scatter even when the smoothed surface is undefined.
    :attr:`density` is the 2-D KDE on the ``(time_bins, signal_bins)`` grid
    (``density[i, j]`` at time :attr:`time_centers` ``[i]`` and E
    :attr:`signal_centers` ``[j]``), or ``None`` when no KDE could be formed (fewer than
    two points, or a singular covariance). :attr:`hdr_levels` holds the density
    threshold per :attr:`hdr_coverages` entry (the ``f = level`` isocontour encloses
    that coverage of the in-grid mass, [Hyndman1996][Haselsteiner2017]); it is ``None``
    exactly when :attr:`density` is. Because a smaller coverage encloses a
    higher-density core, the levels **decrease** as the aligned coverages increase.
    :attr:`points` keeps **all** finite samples (the full scatter, so the GUI can show
    outliers), but the KDE/HDR are fit on the in-grid subset only; :attr:`n_out_of_range`
    reports how many finite samples fell outside the grid range (excluded from the fit —
    a data-QC signal in its own right, e.g. an un-trimmed post-bleach tail).
    """

    points: np.ndarray  # (n_samples, 2) float64 — pooled (time, apparent-E), finite
    density: np.ndarray | None  # (time_bins, signal_bins) float64 KDE grid, or None
    time_edges: np.ndarray  # (time_bins + 1,) float64 — time-column edges
    signal_edges: np.ndarray  # (signal_bins + 1,) float64 — E-bin edges
    time_range: tuple[float, float]
    signal_range: tuple[float, float]
    time_dt: float  # frame duration used for the time axis
    hdr_coverages: np.ndarray  # (k,) float64 — requested coverages, ascending
    hdr_levels: np.ndarray | None  # (k,) float64 density thresholds aligned to coverages, or None
    bandwidth: float | None  # gaussian_kde .factor actually used, or None (no KDE)
    n_samples: int  # finite (time, E) points pooled (the full scatter)
    n_out_of_range: int  # finite points outside the grid range (excluded from the KDE fit)
    n_molecules: int  # molecules contributing >= 1 finite sample

    @property
    def time_centers(self) -> np.ndarray:
        """Time-column centres of the KDE grid."""
        e = self.time_edges
        return 0.5 * (e[:-1] + e[1:])

    @property
    def signal_centers(self) -> np.ndarray:
        """E-bin centres of the KDE grid."""
        e = self.signal_edges
        return 0.5 * (e[:-1] + e[1:])

    @property
    def time_bins(self) -> int:
        """Number of time columns on the KDE grid."""
        return int(self.time_edges.shape[0] - 1)

    @property
    def signal_bins(self) -> int:
        """Number of E bins on the KDE grid."""
        return int(self.signal_edges.shape[0] - 1)


def _hdr_levels_from_grid(
    density: np.ndarray, cell_area: float, coverages: np.ndarray
) -> np.ndarray | None:
    """HDR density thresholds by the numerical-grid density-quantile method.

    Sorts the grid cells by density (descending), accumulates each cell's mass
    ``density * cell_area``, and for each coverage ``p`` returns the density at which the
    cumulative fraction of the **in-grid** mass first reaches ``p`` — the threshold whose
    super-level set encloses coverage ``p`` [Hyndman1996][Haselsteiner2017]. Returns
    ``None`` when the grid carries no mass (an all-zero surface), so the empty case never
    yields a fabricated contour.
    """
    flat = np.asarray(density, dtype=np.float64).ravel()
    total = float(flat.sum()) * float(cell_area)
    if not np.isfinite(total) or total <= 0.0:
        return None
    order = np.argsort(flat)[::-1]
    sorted_density = flat[order]
    cum_fraction = np.cumsum(sorted_density) * float(cell_area) / total
    levels = np.empty(coverages.shape[0], dtype=np.float64)
    last = sorted_density.size - 1
    for k, p in enumerate(coverages):
        idx = int(np.searchsorted(cum_fraction, float(p), side="left"))
        if idx > last:
            idx = last
        levels[k] = sorted_density[idx]
    return levels


def _kde_surface(
    points: np.ndarray,
    time_centers: np.ndarray,
    signal_centers: np.ndarray,
    bw_method: str | float,
) -> tuple[np.ndarray, float] | None:
    """2-D gaussian_kde of the ``(time, E)`` cloud on the grid; ``None`` when undefined.

    Returns ``(density, factor)`` where ``density[i, j]`` is the estimate at
    ``(time_centers[i], signal_centers[j])`` and ``factor`` the bandwidth actually used.
    Returns ``None`` (never raises) when the estimate is degenerate — fewer than two
    points, or a singular covariance (all points collinear / one axis constant). scipy
    raises :class:`~numpy.linalg.LinAlgError` for some singular inputs but *silently*
    returns a degenerate all-zero surface for others (e.g. a constant E axis), so both
    the raise and the resulting zero-mass grid are treated as "no surface" — the B3
    ``try/except`` guard, extended for the 2-D case so a misleading empty field is never
    returned.
    """
    if points.shape[0] < 2:
        return None
    try:
        from scipy.stats import gaussian_kde

        kde = gaussian_kde(points.T, bw_method=bw_method)
        grid_time, grid_signal = np.meshgrid(time_centers, signal_centers, indexing="ij")
        positions = np.vstack([grid_time.ravel(), grid_signal.ravel()])
        density = np.asarray(kde(positions), dtype=np.float64).reshape(
            time_centers.size, signal_centers.size
        )
        factor = float(kde.factor)
    except (np.linalg.LinAlgError, ValueError):
        # Singular covariance (identical/collinear points) or a degenerate dataset that
        # scipy rejects outright: no surface, not a crash and not a fabricated field.
        return None
    if not np.any(np.isfinite(density) & (density > 0.0)):
        # A silently-degenerate KDE (a constant axis leaves the mass on a zero-width
        # line between grid centres) or a cloud entirely outside the grid range: the
        # surface carries no mass, so there is nothing honest to draw.
        return None
    return density, factor


def raw_fret_cloud(
    fret_chunks: Iterable[np.ndarray],
    *,
    time_bins: int = DEFAULT_CLOUD_TIME_BINS,
    signal_bins: int = DEFAULT_CLOUD_SIGNAL_BINS,
    signal_range: tuple[float, float] = DEFAULT_CLOUD_SIGNAL_RANGE,
    time_range: tuple[float, float] | None = None,
    time_dt: float = DEFAULT_CLOUD_TIME_DT,
    hdr_coverages: Sequence[float] = DEFAULT_CLOUD_HDR_COVERAGES,
    kde: bool = True,
    bw_method: str | float = DEFAULT_CLOUD_BW_METHOD,
) -> RawFretCloud:
    """Pool per-molecule windowed apparent-E into a raw FRET cloud (the pure-array core).

    Each element of ``fret_chunks`` is one molecule's per-frame **apparent E** over its
    analysis window (``NaN`` where ``D + A == 0``). Frame ``t`` contributes the point
    ``(t * time_dt, E[t])``; non-finite E are dropped (never a fabricated value). Every
    finite point is kept in :attr:`~RawFretCloud.points` (the scatter), but the 2-D
    Gaussian KDE on the ``(time_bins, signal_bins)`` grid — and the HDR contour thresholds
    derived from it for each ``hdr_coverages`` entry — are fit on the **in-grid points
    only**, so finite-but-off-grid E outliers (bleached/blinking frames) do not inflate
    the bandwidth; those are counted in :attr:`~RawFretCloud.n_out_of_range`.

    Parameters
    ----------
    fret_chunks
        Iterable of 1-D per-molecule apparent-E arrays.
    time_bins, signal_bins
        KDE-grid resolution on the time and E axes (each ``>= 1``).
    signal_range
        ``(lo, hi)`` E range for the grid (``hi > lo``).
    time_range
        ``(lo, hi)`` time range for the grid; ``None`` (default) spans ``0`` to the
        largest pooled time (the whole cloud). ``hi > lo`` required when given.
    time_dt
        Frame duration; ``time = frame_index * time_dt`` (``> 0``, finite).
    hdr_coverages
        Coverages (each in the open interval ``(0, 1)``) whose HDR contour density
        thresholds are returned; sorted ascending in the result.
    kde
        Compute the KDE surface + HDR levels; if ``False`` both are ``None`` (raw
        scatter only).
    bw_method
        gaussian_kde bandwidth rule/factor (``"scott"``, ``"silverman"``, or a positive
        scalar).

    Returns
    -------
    RawFretCloud

    Raises
    ------
    ValueError
        A bin count ``< 1``; a range without ``hi > lo``; a non-finite / non-positive
        ``time_dt``; or an ``hdr_coverages`` value outside ``(0, 1)``.
    """
    if int(time_bins) < 1:
        raise ValueError(f"time_bins must be >= 1, got {time_bins!r}")
    if int(signal_bins) < 1:
        raise ValueError(f"signal_bins must be >= 1, got {signal_bins!r}")
    sig_lo, sig_hi = float(signal_range[0]), float(signal_range[1])
    if not sig_hi > sig_lo:
        raise ValueError(f"signal_range must have hi > lo, got {signal_range!r}")
    dt = float(time_dt)
    if not (np.isfinite(dt) and dt > 0.0):
        raise ValueError(f"time_dt must be finite and > 0, got {time_dt!r}")
    coverages = np.sort(np.asarray(hdr_coverages, dtype=np.float64))
    if coverages.size and not (np.all(coverages > 0.0) and np.all(coverages < 1.0)):
        raise ValueError(f"hdr_coverages must each be in (0, 1), got {tuple(hdr_coverages)!r}")

    time_bins = int(time_bins)
    signal_bins = int(signal_bins)

    times: list[np.ndarray] = []
    values: list[np.ndarray] = []
    n_molecules = 0
    for chunk in fret_chunks:
        if np.ndim(chunk) == 0:
            # A scalar element means a flat 1-D array was passed instead of an iterable
            # of per-molecule arrays (``raw_fret_cloud(e)`` vs ``[e]``); iterating it
            # yields single frames. Fail fast on the public-API misuse (mirrors tdp.py).
            raise ValueError(
                "fret_chunks must be an iterable of 1-D per-molecule apparent-E arrays, "
                "got a scalar element — wrap a single molecule as [e], not e"
            )
        e = np.asarray(chunk, dtype=np.float64).ravel()
        if e.size == 0:
            continue
        finite = np.isfinite(e)
        if not finite.any():
            continue
        idx = np.nonzero(finite)[0]
        times.append(idx.astype(np.float64) * dt)
        values.append(e[idx])
        n_molecules += 1

    if times:
        cat_time = np.concatenate(times)
        cat_value = np.concatenate(values)
    else:
        cat_time = np.empty(0, dtype=np.float64)
        cat_value = np.empty(0, dtype=np.float64)
    points = np.ascontiguousarray(np.column_stack([cat_time, cat_value]), dtype=np.float64)

    if time_range is None:
        time_lo = 0.0
        time_hi = float(cat_time.max()) if cat_time.size else dt
        if not time_hi > time_lo:  # a single frame at t=0 -> give the axis width
            time_hi = time_lo + dt
    else:
        time_lo, time_hi = float(time_range[0]), float(time_range[1])
        if not time_hi > time_lo:
            raise ValueError(f"time_range must have hi > lo, got {time_range!r}")

    time_edges = np.linspace(time_lo, time_hi, time_bins + 1)
    signal_edges = np.linspace(sig_lo, sig_hi, signal_bins + 1)
    time_centers = 0.5 * (time_edges[:-1] + time_edges[1:])
    signal_centers = 0.5 * (signal_edges[:-1] + signal_edges[1:])

    # The KDE is fit on the in-grid points only. apparent_fret is un-clipped, so
    # bleached/blinking frames (near-zero D + A) yield finite but extreme, off-grid E;
    # left in the fit those invisible outliers inflate Scott's covariance-scaled
    # bandwidth and over-smooth the bands. They stay in ``points`` (the honest scatter)
    # and are counted, but excluded from the surface.
    in_range = (
        (points[:, 0] >= time_lo)
        & (points[:, 0] <= time_hi)
        & (points[:, 1] >= sig_lo)
        & (points[:, 1] <= sig_hi)
    )
    n_out_of_range = int(points.shape[0] - int(np.count_nonzero(in_range)))

    density: np.ndarray | None = None
    hdr_levels: np.ndarray | None = None
    bandwidth: float | None = None
    if kde:
        surface = _kde_surface(points[in_range], time_centers, signal_centers, bw_method)
        if surface is not None:
            density, bandwidth = surface
            cell_area = (time_hi - time_lo) / time_bins * (sig_hi - sig_lo) / signal_bins
            hdr_levels = _hdr_levels_from_grid(density, cell_area, coverages)

    return RawFretCloud(
        points=points,
        density=density,
        time_edges=time_edges,
        signal_edges=signal_edges,
        time_range=(time_lo, time_hi),
        signal_range=(sig_lo, sig_hi),
        time_dt=dt,
        hdr_coverages=np.ascontiguousarray(coverages, dtype=np.float64),
        hdr_levels=hdr_levels,
        bandwidth=bandwidth,
        n_samples=int(points.shape[0]),
        n_out_of_range=n_out_of_range,
        n_molecules=int(n_molecules),
    )


def population_raw_fret_cloud(
    project: ProjectRef,
    *,
    molecule_keys: list[str] | None = None,
    intensity_quantity: str = "corrected",
    time_bins: int = DEFAULT_CLOUD_TIME_BINS,
    signal_bins: int = DEFAULT_CLOUD_SIGNAL_BINS,
    signal_range: tuple[float, float] = DEFAULT_CLOUD_SIGNAL_RANGE,
    time_range: tuple[float, float] | None = None,
    time_dt: float = DEFAULT_CLOUD_TIME_DT,
    hdr_coverages: Sequence[float] = DEFAULT_CLOUD_HDR_COVERAGES,
    kde: bool = True,
    bw_method: str | float = DEFAULT_CLOUD_BW_METHOD,
    include_rejected: bool = False,
) -> RawFretCloud:
    """Raw FRET cloud from a ``.tether`` store (§10 PR-5; PRD §7.7).

    Pools each accepted molecule's windowed **apparent E**
    (:func:`tether.fret.efficiency.apparent_fret` over the ``intensity_quantity``
    channels, sliced to the ``analysis_window``) and feeds it to :func:`raw_fret_cloud`.
    Rejected molecules are excluded unless ``include_rejected`` (§7.5). This is a
    **pre-idealization** view — it reads ``/traces`` only, never ``/idealization`` — so
    there is no fresh/stale filter (that guards model plots against re-extraction; the
    cloud has no model to go stale) and no ``model_name``.

    Parameters
    ----------
    project
        A :class:`~tether.project.core.Project` or a path to a ``.tether`` store.
    molecule_keys
        Restrict to these ``molecule_key`` values (``None`` = all), intersected with the
        curation filter.
    intensity_quantity
        Which ``/traces`` layers supply the apparent-E channels (``"corrected"`` default,
        or ``"raw"``; see :func:`tether.analysis._store.resolve_quantity`).
    time_bins, signal_bins, signal_range, time_range, time_dt, hdr_coverages, kde, bw_method
        Passed through to :func:`raw_fret_cloud`.
    include_rejected
        Keep rejected molecules (default excludes them, §7.5).

    Returns
    -------
    RawFretCloud

    Raises
    ------
    ValueError
        The store lacks the requested trace layer, or a :func:`raw_fret_cloud`
        parameter is invalid.
    """
    from tether.analysis._store import windowed_channels
    from tether.fret.efficiency import apparent_fret

    pairs = windowed_channels(project, molecule_keys, intensity_quantity, include_rejected)
    fret_chunks = [apparent_fret(donor, acceptor) for donor, acceptor in pairs]
    return raw_fret_cloud(
        fret_chunks,
        time_bins=time_bins,
        signal_bins=signal_bins,
        signal_range=signal_range,
        time_range=time_range,
        time_dt=time_dt,
        hdr_coverages=hdr_coverages,
        kde=kde,
        bw_method=bw_method,
    )
