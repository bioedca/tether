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

Two further model-free lenses (PRD §7.7), the second half of the raw-cloud deliverable
(PR-5b), read the *same* pooled cloud:

* **Alpha-shape support boundary** — the α-shape generalizes the convex hull to
  reconstruct a **non-convex** support, so the boundary traces where the population's
  signal actually lives (hugging separated bands) instead of the convex hull's
  gap-spanning envelope [Edelsbrunner1983][PateiroLopez2010]. It is built from the
  cloud's Delaunay triangulation: a triangle is kept when its circumradius does not
  exceed the threshold ``alpha``, and the boundary is the set of edges belonging to
  exactly one kept triangle. Because the time and E axes carry very different units,
  the circumradius test runs in coordinates normalized so each axis spans ``[0, 1]``
  over the cloud's bounding box (the α-shape analogue of the KDE's covariance-scaled
  bandwidth); ``alpha`` is therefore a dimensionless fraction, larger → closer to the
  convex hull, smaller → more concave / fragmented. :func:`alpha_shape` is the pure
  core, :func:`population_fret_cloud_alpha_shape` the store entry.
* **k-vs-RMSE elbow state-count hint** — k-means is run on the pooled **apparent-E
  values** (the FRET dimension only — a state is an E *level*, persisting across time,
  so clustering the 2-D (time, E) cloud would split one state into several time-blobs)
  for a range of ``k``, and the within-cluster RMSE(``k``) curve's "elbow" (the knee by
  maximum distance to the first–last chord [Satopaa2011]) is returned as a suggested
  state count [Thorndike1953]. This is a **pre-idealization hint only, not a
  determination**: the elbow criterion is a heuristic that "severely lacks theoretic
  support" [Schubert2022], so the trustworthy state count remains the HMM/vbFRET model
  view (BIC + the TDP) [McKinney2006]; the elbow merely orients the eye before an HMM is
  committed. :func:`k_rmse_elbow` is the pure core,
  :func:`population_fret_cloud_state_number_elbow` the store entry.

:func:`raw_fret_cloud` is the pure-array core (an iterable of per-molecule windowed
apparent-E arrays -> a :class:`RawFretCloud`); :func:`population_raw_fret_cloud` is the
``.tether`` store entry point (curation filter, channel -> apparent-E). The alpha-shape
and elbow reuse that pooling and, like the KDE surface, are fit on the **in-grid points
only** (the off-grid bleach/blink outliers are excluded from both the support boundary
and the E-band clustering, matching the density view).

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
[Edelsbrunner1983] Edelsbrunner H, Kirkpatrick DG, Seidel R. "On the shape of a set of
    points in the plane." IEEE Trans. Information Theory 29(4):551-559 (1983) — the
    α-shape: the triangles of the Delaunay triangulation whose circumradius is bounded
    by α, generalizing the convex hull to a non-convex boundary.
[PateiroLopez2010] Pateiro-López B, Rodríguez-Casal A. "Generalizing the convex hull of
    a sample: the R package alphahull." J. Statistical Software 34(5):1-28 (2010) — the
    α-convex hull / α-shape as a set (support) estimator, computed from the Delaunay
    triangulation, reconstructing non-convex sets the convex hull cannot.
[Thorndike1953] Thorndike RL. "Who belongs in the family?" Psychometrika 18(4):267-276
    (1953) — the original "elbow" heuristic for the number of clusters.
[Satopaa2011] Satopää V, Albrecht J, Irwin D, Raghavan B. "Finding a 'kneedle' in a
    haystack: detecting knee points in system behavior." ICDCS Workshops (2011) — the
    knee/elbow as the point of maximum distance to the chord between the curve's ends.
[Schubert2022] Schubert E. "Stop using the elbow criterion for k-means and how to choose
    the number of clusters instead." ACM SIGKDD Explorations 25(1):36-42 (2022) — the
    elbow method lacks theoretic support; use it only as a heuristic hint, never a
    determination (here it is subordinate to the HMM/BIC state count).
[McKinney2006] McKinney SA, Joo C, Ha T. "Analysis of single-molecule FRET trajectories
    using hidden Markov modeling." Biophys. J. 91(5):1941-1951 (2006) — the number of
    smFRET states is properly determined by HMM + BIC + the transition-density plot, the
    rigorous counterpart the elbow hint precedes.
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
    "DEFAULT_ALPHA_FACTOR",
    "DEFAULT_CLOUD_BW_METHOD",
    "DEFAULT_CLOUD_HDR_COVERAGES",
    "DEFAULT_CLOUD_SIGNAL_BINS",
    "DEFAULT_CLOUD_SIGNAL_RANGE",
    "DEFAULT_CLOUD_TIME_BINS",
    "DEFAULT_CLOUD_TIME_DT",
    "DEFAULT_ELBOW_K_MAX",
    "DEFAULT_ELBOW_RESTARTS",
    "DEFAULT_ELBOW_SEED",
    "AlphaShape",
    "RawFretCloud",
    "StateNumberElbow",
    "alpha_shape",
    "k_rmse_elbow",
    "population_fret_cloud_alpha_shape",
    "population_fret_cloud_state_number_elbow",
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

#: Auto-``alpha`` multiplier: when :func:`alpha_shape` is given no ``alpha``, the
#: circumradius threshold is this factor times the **median** finite triangle
#: circumradius (in normalized coordinates), keeping the bulk of triangles while
#: dropping the gap-spanning slivers — a mildly concave support. A rendering default,
#: **not** a §11.2 science tunable (the boundary is a QC visualization, not a factor
#: that enters any downstream result).
DEFAULT_ALPHA_FACTOR = 2.0

#: Largest cluster count ``k`` probed by the k-vs-RMSE elbow. Capped further at the
#: number of distinct pooled E values. A rendering default, **not** a §11.2 tunable —
#: the elbow is a heuristic hint, not a state-count determination [Schubert2022].
DEFAULT_ELBOW_K_MAX = 8

#: Number of k-means restarts per ``k`` (scipy ``kmeans`` ``iter``; the lowest-distortion
#: codebook is kept), for a stable elbow curve. A rendering default, **not** a §11.2
#: tunable.
DEFAULT_ELBOW_RESTARTS = 10

#: Seed for the k-means initialization, so the elbow curve is reproducible (NFR-REPRO).
#: A rendering default, **not** a §11.2 tunable.
DEFAULT_ELBOW_SEED = 0


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


# --- alpha-shape support boundary (PR-5b) --------------------------------------


@dataclass(frozen=True)
class AlphaShape:
    """The α-shape (concave support boundary) of a pooled ``(time, E)`` FRET cloud.

    The α-shape is the set of Delaunay triangles whose circumradius does not exceed
    ``alpha`` [Edelsbrunner1983][PateiroLopez2010]; its boundary is the edges shared by
    exactly one such triangle. Because the time and E axes carry different units, the
    circumradius test is applied in coordinates normalized so each axis spans ``[0, 1]``
    over the cloud's bounding box, so :attr:`alpha` is a **dimensionless** threshold
    (larger → nearer the convex hull, smaller → more concave / fragmented).

    :attr:`boundary_edges` are the boundary segments in the **original** ``(time, E)``
    coordinates (``(n_edges, 2, 2)`` — ``[edge, endpoint, (time, E)]``), ready to draw.
    :attr:`area` is the total area of the kept triangles in original units (a QC scalar:
    the occupied ``time·E`` support area). :attr:`n_kept` is how many of the
    :attr:`n_triangles` Delaunay triangles fell inside the shape; an empty
    :attr:`boundary_edges` with ``n_kept == 0`` means ``alpha`` was too small to keep any
    triangle (an honest "no support at this α", never a fabricated boundary).
    """

    alpha: float  # circumradius threshold actually used (normalized coordinates)
    boundary_edges: np.ndarray  # (n_edges, 2, 2) float64 original-coord segments
    area: float  # total area of kept triangles, original (time·E) units
    n_points: int  # finite points triangulated
    n_triangles: int  # total Delaunay triangles
    n_kept: int  # triangles with circumradius <= alpha (inside the shape)

    @property
    def n_boundary_edges(self) -> int:
        """Number of boundary edges (segments) of the α-shape."""
        return int(self.boundary_edges.shape[0])


def _triangle_circumradii(tri_pts: np.ndarray) -> np.ndarray:
    """Circumradius of each triangle in ``tri_pts`` (``(m, 3, 2)``); ``inf`` if degenerate.

    ``R = abc / (4·area)`` with side lengths ``a, b, c``; a zero-area (collinear) triangle
    gives ``inf`` so it is never kept (its circumscribed circle is unbounded).
    """
    p0, p1, p2 = tri_pts[:, 0], tri_pts[:, 1], tri_pts[:, 2]
    a = np.linalg.norm(p1 - p2, axis=1)
    b = np.linalg.norm(p0 - p2, axis=1)
    c = np.linalg.norm(p0 - p1, axis=1)
    # |cross((p1-p0), (p2-p0))| == 2·area
    area2 = np.abs(
        (p1[:, 0] - p0[:, 0]) * (p2[:, 1] - p0[:, 1])
        - (p1[:, 1] - p0[:, 1]) * (p2[:, 0] - p0[:, 0])
    )
    circ = np.full(tri_pts.shape[0], np.inf, dtype=np.float64)
    nondegenerate = area2 > 0.0
    circ[nondegenerate] = (
        a[nondegenerate] * b[nondegenerate] * c[nondegenerate] / (2.0 * area2[nondegenerate])
    )
    return circ


def alpha_shape(points: np.ndarray, *, alpha: float | None = None) -> AlphaShape | None:
    """The α-shape (concave support boundary) of a 2-D ``(time, E)`` point cloud.

    Triangulates ``points`` (Delaunay), keeps triangles whose **normalized-coordinate**
    circumradius is ``<= alpha``, and returns the boundary (edges in exactly one kept
    triangle) as segments in the original coordinates. The normalization (each axis to
    ``[0, 1]`` over the bounding box) makes the single threshold ``alpha`` comparable
    across the very different time and E scales.

    Parameters
    ----------
    points
        ``(n, 2)`` array of ``(time, E)`` samples (typically a
        :attr:`RawFretCloud.points` subset). Non-finite rows are dropped.
    alpha
        Circumradius threshold in normalized coordinates (a positive dimensionless
        fraction). ``None`` (default) auto-selects :data:`DEFAULT_ALPHA_FACTOR` times the
        median finite triangle circumradius — a mildly concave support that adapts to the
        cloud's density.

    Returns
    -------
    AlphaShape or None
        ``None`` when the cloud cannot span a 2-D region — fewer than three finite
        points, a degenerate axis (all one time or all one E), or a fully collinear set
        (Qhull cannot triangulate it). Never raises for those; a misleading boundary is
        never fabricated.

    Raises
    ------
    ValueError
        ``points`` is not ``(n, 2)``, or an explicit ``alpha`` is not positive.
    """
    pts = np.ascontiguousarray(np.asarray(points, dtype=np.float64))
    if pts.ndim != 2 or pts.shape[1] != 2:
        raise ValueError(f"points must be an (n, 2) array, got shape {pts.shape!r}")
    if alpha is not None and not (float(alpha) > 0.0 and np.isfinite(float(alpha))):
        raise ValueError(f"alpha must be a positive finite number or None, got {alpha!r}")

    pts = pts[np.all(np.isfinite(pts), axis=1)]
    if pts.shape[0] < 3:
        return None
    span = np.ptp(pts, axis=0)
    if span[0] <= 0.0 or span[1] <= 0.0:
        return None  # collinear along an axis -> no 2-D support to bound

    from scipy.spatial import Delaunay, QhullError

    normalized = (pts - pts.min(axis=0)) / span
    try:
        tri = Delaunay(normalized)
    except (QhullError, ValueError):
        # A fully collinear (but not axis-aligned) cloud: Qhull cannot triangulate it, so
        # there is no 2-D support — None, not a crash and not a fabricated boundary.
        return None
    simplices = np.asarray(tri.simplices)
    if simplices.shape[0] == 0:
        return None

    circ = _triangle_circumradii(normalized[simplices])
    finite = np.isfinite(circ)
    if not finite.any():
        return None  # every triangle degenerate -> no meaningful shape
    if alpha is None:
        alpha_used = float(DEFAULT_ALPHA_FACTOR * np.median(circ[finite]))
    else:
        alpha_used = float(alpha)

    kept = circ <= alpha_used
    kept_simplices = simplices[kept]
    # triangle areas in ORIGINAL units (0.5·|cross|), so the reported support area is real
    kept_pts = pts[kept_simplices]
    area = 0.0
    if kept_simplices.shape[0]:
        q0, q1, q2 = kept_pts[:, 0], kept_pts[:, 1], kept_pts[:, 2]
        area2_orig = np.abs(
            (q1[:, 0] - q0[:, 0]) * (q2[:, 1] - q0[:, 1])
            - (q1[:, 1] - q0[:, 1]) * (q2[:, 0] - q0[:, 0])
        )
        area = float(0.5 * area2_orig.sum())

    if kept_simplices.shape[0] == 0:
        boundary_edges = np.empty((0, 2, 2), dtype=np.float64)
    else:
        # boundary edge == an edge in exactly one kept triangle
        e01 = np.sort(kept_simplices[:, [0, 1]], axis=1)
        e12 = np.sort(kept_simplices[:, [1, 2]], axis=1)
        e20 = np.sort(kept_simplices[:, [2, 0]], axis=1)
        all_edges = np.vstack([e01, e12, e20])
        uniq, counts = np.unique(all_edges, axis=0, return_counts=True)
        boundary = uniq[counts == 1]
        boundary_edges = np.ascontiguousarray(pts[boundary], dtype=np.float64)

    return AlphaShape(
        alpha=alpha_used,
        boundary_edges=boundary_edges,
        area=area,
        n_points=int(pts.shape[0]),
        n_triangles=int(simplices.shape[0]),
        n_kept=int(kept_simplices.shape[0]),
    )


# --- k-vs-RMSE elbow state-count hint (PR-5b) ----------------------------------


@dataclass(frozen=True)
class StateNumberElbow:
    """A k-vs-RMSE elbow **hint** for the number of FRET states (pre-idealization QC).

    :attr:`rmse` ``[i]`` is the within-cluster root-mean-square distance of the pooled
    apparent-E values to their nearest of :attr:`k_values` ``[i]`` k-means centroids
    (decreasing in ``k``). :attr:`elbow_k` is the "knee" of that curve — the ``k`` of
    maximum distance to the first→last chord [Satopaa2011] — offered as a **suggested**
    state count [Thorndike1953], or ``None`` when no interior elbow exists (fewer than
    three probed ``k``, or a flat curve).

    This is a heuristic hint only: the elbow criterion "severely lacks theoretic support"
    [Schubert2022], so the trustworthy state count remains the HMM/vbFRET model view
    (BIC + the TDP) [McKinney2006]. :attr:`seed` records the k-means seed so the curve is
    reproducible (NFR-REPRO).
    """

    k_values: np.ndarray  # (K,) int64 — cluster counts probed, ascending
    rmse: np.ndarray  # (K,) float64 — within-cluster RMSE at each k
    elbow_k: int | None  # suggested state count (the knee), or None
    n_samples: int  # finite apparent-E values clustered
    seed: int  # k-means initialization seed used


def _elbow_index(k_values: np.ndarray, rmse: np.ndarray) -> int | None:
    """The knee of the (k, RMSE) curve by maximum distance to the first→last chord.

    Both axes are min-max normalized to ``[0, 1]`` so ``k`` and RMSE are comparable, then
    the interior point farthest from the chord joining the curve's ends is the elbow
    [Satopaa2011]. ``None`` when there is no interior point (< 3 probed ``k``) or the
    curve is flat (no RMSE spread).
    """
    k = np.asarray(k_values, dtype=np.float64)
    r = np.asarray(rmse, dtype=np.float64)
    if k.size < 3:
        return None
    r_span = float(r.max() - r.min())
    k_span = float(k.max() - k.min())
    if not (np.isfinite(r_span) and r_span > 0.0) or k_span <= 0.0:
        return None
    kn = (k - k.min()) / k_span
    rn = (r - r.min()) / r_span
    x1, y1, x2, y2 = kn[0], rn[0], kn[-1], rn[-1]
    denom = float(np.hypot(y2 - y1, x2 - x1))
    if denom <= 0.0:
        return None
    dist = np.abs((y2 - y1) * kn - (x2 - x1) * rn + x2 * y1 - y2 * x1) / denom
    dist[0] = dist[-1] = -1.0  # the chord endpoints are never the elbow
    return int(k_values[int(np.argmax(dist))])


def k_rmse_elbow(
    values: np.ndarray,
    *,
    k_min: int = 1,
    k_max: int = DEFAULT_ELBOW_K_MAX,
    restarts: int = DEFAULT_ELBOW_RESTARTS,
    seed: int = DEFAULT_ELBOW_SEED,
) -> StateNumberElbow:
    """k-vs-RMSE elbow **hint** for the number of FRET states from pooled apparent-E.

    Runs k-means (:func:`scipy.cluster.vq.kmeans`, ``restarts`` restarts, keeping the
    lowest-distortion codebook) on the 1-D ``values`` for each ``k`` in
    ``[k_min, k_max]`` and records the within-cluster RMSE. Clustering is on the **E
    values alone** — a state is an E *level* that persists over time, so the count of E
    bands, not spatiotemporal blobs, is the state-number analogue. ``k_max`` is capped at
    the number of distinct values (k-means cannot exceed that). The elbow of the RMSE(k)
    curve is returned as :attr:`~StateNumberElbow.elbow_k`.

    This is a **pre-idealization hint, not a determination** [Schubert2022]: it orients
    the eye before an HMM is committed; the state count of record is the model view (BIC +
    the TDP) [McKinney2006].

    Parameters
    ----------
    values
        1-D apparent-E samples (non-finite dropped); e.g. a :attr:`RawFretCloud.points`
        E column.
    k_min, k_max
        Inclusive range of cluster counts to probe (``1 <= k_min <= k_max``).
    restarts
        k-means restarts per ``k`` (the best-distortion codebook is kept), for a stable
        curve.
    seed
        k-means initialization seed, so the curve is reproducible.

    Returns
    -------
    StateNumberElbow

    Raises
    ------
    ValueError
        ``k_min < 1`` or ``k_max < k_min``.
    """
    if int(k_min) < 1:
        raise ValueError(f"k_min must be >= 1, got {k_min!r}")
    if int(k_max) < int(k_min):
        raise ValueError(f"k_max must be >= k_min, got k_max={k_max!r}, k_min={k_min!r}")

    v = np.asarray(values, dtype=np.float64).ravel()
    v = v[np.isfinite(v)]
    n = int(v.size)
    n_distinct = int(np.unique(v).size) if n else 0
    k_hi = min(int(k_max), n_distinct)
    ks = list(range(int(k_min), k_hi + 1))
    if not ks:
        return StateNumberElbow(
            k_values=np.empty(0, dtype=np.int64),
            rmse=np.empty(0, dtype=np.float64),
            elbow_k=None,
            n_samples=n,
            seed=int(seed),
        )

    from scipy.cluster.vq import kmeans, vq

    obs = v.reshape(-1, 1)
    rmses = np.empty(len(ks), dtype=np.float64)
    for i, k in enumerate(ks):
        if k == 1:
            dist = np.abs(v - float(v.mean()))
        else:
            codebook, _ = kmeans(obs, k, iter=int(restarts), rng=int(seed))
            _, dist = vq(obs, codebook)
        rmses[i] = float(np.sqrt(np.mean(np.square(dist))))

    k_arr = np.asarray(ks, dtype=np.int64)
    return StateNumberElbow(
        k_values=k_arr,
        rmse=rmses,
        elbow_k=_elbow_index(k_arr, rmses),
        n_samples=n,
        seed=int(seed),
    )


# --- store entry points (PR-5b) ------------------------------------------------


def _in_grid_points(cloud: RawFretCloud) -> np.ndarray:
    """The subset of ``cloud.points`` inside the KDE grid range (the visible cloud).

    The alpha-shape and elbow share the KDE's in-grid contract: finite-but-off-grid
    bleach/blink outliers stay in :attr:`RawFretCloud.points` (the honest scatter) but are
    excluded here, so the support boundary and the E-band clustering match the density
    surface rather than being dragged out by an invisible outlier.
    """
    p = cloud.points
    if p.shape[0] == 0:
        return p
    t_lo, t_hi = cloud.time_range
    s_lo, s_hi = cloud.signal_range
    mask = (p[:, 0] >= t_lo) & (p[:, 0] <= t_hi) & (p[:, 1] >= s_lo) & (p[:, 1] <= s_hi)
    return p[mask]


def population_fret_cloud_alpha_shape(
    project: ProjectRef,
    *,
    molecule_keys: list[str] | None = None,
    intensity_quantity: str = "corrected",
    signal_range: tuple[float, float] = DEFAULT_CLOUD_SIGNAL_RANGE,
    time_range: tuple[float, float] | None = None,
    time_dt: float = DEFAULT_CLOUD_TIME_DT,
    alpha: float | None = None,
    include_rejected: bool = False,
    in_grid_only: bool = True,
) -> AlphaShape | None:
    """α-shape support boundary of a ``.tether`` store's raw FRET cloud (§10 PR-5b).

    Pools the same pre-idealization ``(time, apparent-E)`` cloud as
    :func:`population_raw_fret_cloud` (curation filter, ``intensity_quantity`` channels,
    analysis window) and returns its α-shape (:func:`alpha_shape`). By default only the
    in-grid points feed the boundary (``in_grid_only``), matching the KDE surface.

    Parameters mirror :func:`population_raw_fret_cloud` (plus ``alpha`` from
    :func:`alpha_shape`); ``in_grid_only=False`` bounds the full scatter including
    off-grid outliers. Returns ``None`` when the cloud cannot span a 2-D region.
    """
    cloud = population_raw_fret_cloud(
        project,
        molecule_keys=molecule_keys,
        intensity_quantity=intensity_quantity,
        signal_range=signal_range,
        time_range=time_range,
        time_dt=time_dt,
        include_rejected=include_rejected,
        kde=False,
    )
    pts = _in_grid_points(cloud) if in_grid_only else cloud.points
    return alpha_shape(pts, alpha=alpha)


def population_fret_cloud_state_number_elbow(
    project: ProjectRef,
    *,
    molecule_keys: list[str] | None = None,
    intensity_quantity: str = "corrected",
    signal_range: tuple[float, float] = DEFAULT_CLOUD_SIGNAL_RANGE,
    time_range: tuple[float, float] | None = None,
    time_dt: float = DEFAULT_CLOUD_TIME_DT,
    k_min: int = 1,
    k_max: int = DEFAULT_ELBOW_K_MAX,
    restarts: int = DEFAULT_ELBOW_RESTARTS,
    seed: int = DEFAULT_ELBOW_SEED,
    include_rejected: bool = False,
    in_grid_only: bool = True,
) -> StateNumberElbow:
    """k-vs-RMSE elbow state-count **hint** from a ``.tether`` store (§10 PR-5b).

    Pools the same pre-idealization apparent-E as :func:`population_raw_fret_cloud`, then
    runs :func:`k_rmse_elbow` on the pooled **E values** (the FRET dimension only). By
    default only in-grid values are clustered (``in_grid_only``), so off-grid bleach/blink
    outliers do not create a spurious extra band. Returns a
    :class:`StateNumberElbow` whose :attr:`~StateNumberElbow.elbow_k` is a heuristic
    suggestion, subordinate to the HMM/BIC state count [Schubert2022][McKinney2006].
    """
    cloud = population_raw_fret_cloud(
        project,
        molecule_keys=molecule_keys,
        intensity_quantity=intensity_quantity,
        signal_range=signal_range,
        time_range=time_range,
        time_dt=time_dt,
        include_rejected=include_rejected,
        kde=False,
    )
    pts = _in_grid_points(cloud) if in_grid_only else cloud.points
    return k_rmse_elbow(pts[:, 1], k_min=k_min, k_max=k_max, restarts=restarts, seed=seed)
