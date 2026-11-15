# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Successive-frame FRET cloud: E(i) against E(i+1) (#710, ADR-0105, PRD §7.7).

The **successive-frame FRET cloud** plots, for each molecule, the apparent E of every frame
``i`` against the apparent E of the next frame ``i + 1``. A molecule that stays in one state puts
its pairs on the diagonal ``E(i+1) = E(i)``, at that state's E; a transition between two frames
puts one pair off the diagonal. The cloud therefore shows the states *and* the jumps between
them without an idealization, which is why PRD §1 counts it among the model-free lenses. It is a
different plot from the **time–FRET cloud** of :mod:`tether.analysis.cloud`
(:func:`~tether.analysis.cloud.raw_fret_cloud`), which plots E against time; neither replaces
the other.

The requirements come from the laboratory's scripts, "FRET_cloud scripts, Eduardo Campos Chavez,
2025, unpublished" (``FRET_cloud_contours_centroids.m``), and Tether reproduces what they
compute on the smfret-analysis2 test traces to the limits that
``tests/test_fret_cloud_reference.py`` states (ADR-0105):

* **The pairs.** ``(E[t], E[t + 1])`` for every ``t`` of one molecule. A pair never joins two
  molecules. A frame whose E is not finite is a masked frame: neither pair that touches it is
  formed, so no pair crosses a masked gap. The analysis window (the store reader) and the
  photobleach mask therefore decide which frames are paired; a caller that masks a frame for
  another reason sets its E to ``NaN``.
* **The density.** A Gaussian kernel-density estimate [Silverman1986] with one fixed bandwidth
  ``h`` on both axes, evaluated at the ``grid_points x grid_points`` nodes of
  ``linspace(lo, hi, grid_points)``, and normalized so that the sum over the nodes times the
  node area is 1 (the script's ``pdfNormalized``). The bandwidth is fixed, not estimated from
  the data, so a pair outside the grid range still adds the part of its kernel that falls on the
  grid; no pair is excluded from the estimate, as in the script. Each kernel is the whole
  Gaussian: MATLAB's ``mvksdensity``, which the script calls, leaves out the part beyond 4
  bandwidths on either axis, which moves the density by about 1e-4 of its peak (ADR-0105).
* **The contour levels.** For each coverage ``p``, the density threshold whose super-level set
  holds the fraction ``p`` of the grid mass: the highest-density region [Hyndman1996] by the
  numerical-grid method [Haselsteiner2017], the same rule as the time–FRET cloud.
* **The centroids (optional).** k-means [Lloyd1982] on all the pairs, started by k-means++
  [Arthur2007] and repeated ``kmeans_restarts`` times from a seeded generator; the run with the
  smallest within-cluster sum of squares is kept. A centroid is a *description* of where the
  cloud is dense, not a state determination: the state count of record stays the HMM model view
  [McKinney2006], as for the elbow hint of the time–FRET cloud.

:func:`successive_frame_pairs` and :func:`successive_frame_cloud` are the pure-array core;
:func:`population_successive_frame_cloud` is the ``.tether`` store entry point. Every result
carries :attr:`SuccessiveFrameCloud.provenance`, the analysis name, the Tether version and every
parameter that made it (NFR-REPRO).

References
----------
[Silverman1986] Silverman BW. "Density Estimation for Statistics and Data Analysis." Chapman &
    Hall (1986) — the product Gaussian kernel estimate with a fixed bandwidth.
[Hyndman1996] Hyndman RJ. "Computing and graphing highest density regions." The American
    Statistician 50(2):120-126 (1996).
[Haselsteiner2017] Haselsteiner AF, Ohlendorf J-H, Wosniok W, Thoben K-D. "Deriving
    environmental contours from highest density regions." Coastal Engineering 123:42-51 (2017).
[Lloyd1982] Lloyd SP. "Least squares quantization in PCM." IEEE Trans. Information Theory
    28(2):129-137 (1982) — the batch k-means iteration.
[Arthur2007] Arthur D, Vassilvitskii S. "k-means++: the advantages of careful seeding."
    Proc. 18th ACM-SIAM Symposium on Discrete Algorithms, 1027-1035 (2007).
[McKinney2006] McKinney SA, Joo C, Ha T. "Analysis of single-molecule FRET trajectories using
    hidden Markov modeling." Biophys. J. 91(5):1941-1951 (2006).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from tether.analysis.cloud import _hdr_levels_from_grid

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Iterable, Sequence
    from os import PathLike

    from tether.project.core import Project

    ProjectRef = Project | str | PathLike[str]

__all__ = [
    "DEFAULT_SUCCESSIVE_BANDWIDTH",
    "DEFAULT_SUCCESSIVE_GRID_POINTS",
    "DEFAULT_SUCCESSIVE_HDR_COVERAGES",
    "DEFAULT_SUCCESSIVE_KMEANS_MAX_ITER",
    "DEFAULT_SUCCESSIVE_KMEANS_RESTARTS",
    "DEFAULT_SUCCESSIVE_N_CENTROIDS",
    "DEFAULT_SUCCESSIVE_RANGE",
    "DEFAULT_SUCCESSIVE_SEED",
    "SuccessiveFrameCloud",
    "SuccessiveFramePairs",
    "population_successive_frame_cloud",
    "successive_frame_cloud",
    "successive_frame_pairs",
]

#: Grid nodes on each axis: the density is evaluated at ``linspace(lo, hi, n)`` on both axes.
#: A PRD §11.2 science tunable (it moves the contour levels); the scripts use 200.
DEFAULT_SUCCESSIVE_GRID_POINTS = 200

#: The E range of the grid on both axes. A PRD §11.2 science tunable; the scripts use [0, 1].
DEFAULT_SUCCESSIVE_RANGE: tuple[float, float] = (0.0, 1.0)

#: Gaussian kernel bandwidth, in apparent-E units, on both axes (the smoothing). A PRD §11.2
#: science tunable; the scripts use 0.05.
DEFAULT_SUCCESSIVE_BANDWIDTH = 0.05

#: Coverages of the highest-density-region contours, 10 % to 90 % in steps of 10 %. A PRD §11.2
#: science tunable; the scripts use ``0.1:0.1:0.9``.
DEFAULT_SUCCESSIVE_HDR_COVERAGES: tuple[float, ...] = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)

#: Number of k-means centroids (``None`` computes none). A PRD §11.2 science tunable; the
#: scripts use 4.
DEFAULT_SUCCESSIVE_N_CENTROIDS: int | None = 4

#: k-means restarts; the run with the smallest within-cluster sum of squares is kept. A PRD
#: §11.2 science tunable; the scripts use 5 (``'Replicates', 5``).
DEFAULT_SUCCESSIVE_KMEANS_RESTARTS = 5

#: Seed of the k-means++ starts, so the centroids are reproducible (NFR-REPRO). A PRD §11.2
#: science tunable.
DEFAULT_SUCCESSIVE_SEED = 0

#: Iteration cap of one k-means run; a run stops earlier when no pair changes cluster. MATLAB
#: ``kmeans`` has the same cap (``MaxIter`` 100). A PRD §11.2 science tunable.
DEFAULT_SUCCESSIVE_KMEANS_MAX_ITER = 100

#: Pairs per block when the density is summed, which bounds the memory to
#: ``2 * grid_points * _BLOCK`` doubles whatever the number of pairs.
_BLOCK = 8192


@dataclass(frozen=True)
class SuccessiveFramePairs:
    """The ``(E(i), E(i+1))`` pairs of a set of molecules.

    Row ``r`` of :attr:`pairs` is ``(E[t], E[t + 1])`` of molecule :attr:`molecule_index` ``[r]``
    (its position in the input) at frame ``t =`` :attr:`frame_index` ``[r]``, the frame index
    within that molecule's array. Rows are in input order, then frame order.
    """

    pairs: np.ndarray  # (n, 2) float64
    molecule_index: np.ndarray  # (n,) int64
    frame_index: np.ndarray  # (n,) int64

    @property
    def n_pairs(self) -> int:
        """Number of pairs."""
        return int(self.pairs.shape[0])

    @property
    def n_molecules(self) -> int:
        """Number of molecules that contribute at least one pair."""
        return int(np.unique(self.molecule_index).size)


@dataclass(frozen=True)
class SuccessiveFrameCloud:
    """The successive-frame FRET cloud of a set of molecules (#710).

    :attr:`density` ``[i, j]`` is the normalized density at ``(E(i), E(i+1)) =``
    ``(``:attr:`grid` ``[i],`` :attr:`grid` ``[j])``, so its first axis is E at frame ``i``;
    ``density.sum() * node_area == 1``. It is ``None`` when there is no pair, or when every pair is
    so far from the grid that no kernel mass reaches it; :attr:`hdr_levels` is ``None`` exactly
    then. The levels are aligned with :attr:`hdr_coverages` (ascending) and so decrease.
    :attr:`centroids` holds the k-means centroids sorted by E(i) and then E(i+1), or ``None`` when
    none were asked for or there is no pair; :attr:`n_centroids` is how many were found, which is
    fewer than asked when there are fewer distinct pairs. :attr:`centroid_sse` is the
    within-cluster sum of squared distances of the kept run.
    """

    pairs: SuccessiveFramePairs
    grid: np.ndarray  # (g,) float64 node coordinates, the same on both axes
    density: np.ndarray | None  # (g, g) float64, or None
    hdr_coverages: np.ndarray  # (k,) float64 ascending
    hdr_levels: np.ndarray | None  # (k,) float64, or None
    bandwidth: float
    centroids: np.ndarray | None  # (c, 2) float64, or None
    centroid_sse: float | None
    n_out_of_range: int  # pairs with a coordinate outside the grid range (kept in every estimate)
    provenance: dict = field(compare=False)

    @property
    def node_area(self) -> float:
        """Area of one grid cell, the spacing of the nodes squared."""
        step = float(self.grid[1] - self.grid[0])
        return step * step

    @property
    def n_pairs(self) -> int:
        """Number of pairs."""
        return self.pairs.n_pairs

    @property
    def n_molecules(self) -> int:
        """Number of molecules that contribute at least one pair."""
        return self.pairs.n_molecules

    @property
    def n_centroids(self) -> int:
        """Number of centroids found (0 when none)."""
        return 0 if self.centroids is None else int(self.centroids.shape[0])


def successive_frame_pairs(fret_chunks: Iterable[np.ndarray]) -> SuccessiveFramePairs:
    """The ``(E(i), E(i+1))`` pairs of each molecule's apparent-E array.

    Each element of ``fret_chunks`` is one molecule's per-frame apparent E, already cut to the
    frames that may be analysed (the analysis window). A pair is formed only between two
    consecutive frames of the same array whose E are both finite: a non-finite frame is masked,
    and no pair crosses it.

    Raises
    ------
    ValueError
        An element is a scalar (a flat array was passed in place of a list of arrays).
    """
    pairs: list[np.ndarray] = []
    molecules: list[np.ndarray] = []
    frames: list[np.ndarray] = []
    for index, chunk in enumerate(fret_chunks):
        if np.ndim(chunk) == 0:
            raise ValueError(
                "fret_chunks must be an iterable of 1-D per-molecule apparent-E arrays, "
                "got a scalar element — wrap a single molecule as [e], not e"
            )
        e = np.asarray(chunk, dtype=np.float64).ravel()
        if e.size < 2:
            continue
        both = np.isfinite(e[:-1]) & np.isfinite(e[1:])
        t = np.nonzero(both)[0]
        if t.size == 0:
            continue
        pairs.append(np.column_stack([e[t], e[t + 1]]))
        molecules.append(np.full(t.size, index, dtype=np.int64))
        frames.append(t.astype(np.int64))
    if not pairs:
        return SuccessiveFramePairs(
            pairs=np.empty((0, 2), dtype=np.float64),
            molecule_index=np.empty(0, dtype=np.int64),
            frame_index=np.empty(0, dtype=np.int64),
        )
    return SuccessiveFramePairs(
        pairs=np.ascontiguousarray(np.concatenate(pairs), dtype=np.float64),
        molecule_index=np.concatenate(molecules),
        frame_index=np.concatenate(frames),
    )


def _density(points: np.ndarray, grid: np.ndarray, bandwidth: float) -> np.ndarray | None:
    """The normalized product-Gaussian density at the grid nodes, or ``None`` with no mass."""
    if points.shape[0] == 0:
        return None
    raw = np.zeros((grid.size, grid.size), dtype=np.float64)
    for start in range(0, points.shape[0], _BLOCK):
        block = points[start : start + _BLOCK]
        kx = np.exp(-0.5 * np.square((grid[:, None] - block[None, :, 0]) / bandwidth))
        ky = np.exp(-0.5 * np.square((grid[:, None] - block[None, :, 1]) / bandwidth))
        raw += kx @ ky.T
    step = float(grid[1] - grid[0])
    total = float(raw.sum()) * step * step
    if not (np.isfinite(total) and total > 0.0):
        return None
    return raw / total


def _kmeans_plus_plus(points: np.ndarray, k: int, rng: np.random.Generator) -> np.ndarray:
    """k-means++ starting centroids [Arthur2007]."""
    n = points.shape[0]
    centers = np.empty((k, 2), dtype=np.float64)
    centers[0] = points[rng.integers(n)]
    nearest = np.sum(np.square(points - centers[0]), axis=1)
    for c in range(1, k):
        total = float(nearest.sum())
        # total is 0 only when every point sits on a centre already chosen.
        choice = int(rng.choice(n, p=nearest / total)) if total > 0.0 else int(rng.integers(n))
        centers[c] = points[choice]
        nearest = np.minimum(nearest, np.sum(np.square(points - centers[c]), axis=1))
    return centers


def _assign(points: np.ndarray, centers: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Nearest centre of each point and its squared distance."""
    d2 = np.sum(np.square(points[:, None, :] - centers[None, :, :]), axis=2)
    labels = np.argmin(d2, axis=1)
    return labels, d2[np.arange(points.shape[0]), labels]


def _lloyd(points: np.ndarray, centers: np.ndarray, max_iter: int) -> tuple[np.ndarray, float, int]:
    """Batch k-means from ``centers`` [Lloyd1982]; returns ``(centers, sse, iterations)``.

    Stops when no point changes cluster, or after ``max_iter`` updates. A cluster left empty
    takes the point farthest from its own centre, as MATLAB's ``EmptyAction`` ``singleton``.
    """
    centers = centers.copy()
    labels, d2 = _assign(points, centers)
    iterations = 0
    for iterations in range(1, max_iter + 1):  # noqa: B007 - the count is returned
        for c in range(centers.shape[0]):
            members = labels == c
            if members.any():
                centers[c] = points[members].mean(axis=0)
            else:
                far = int(np.argmax(d2))
                centers[c] = points[far]
                labels[far] = c
                d2[far] = 0.0
        new_labels, d2 = _assign(points, centers)
        if np.array_equal(new_labels, labels):
            break
        labels = new_labels
    return centers, float(d2.sum()), iterations


def _kmeans(
    points: np.ndarray, k: int, restarts: int, seed: int, max_iter: int
) -> tuple[np.ndarray, float]:
    """Best of ``restarts`` k-means++ runs; centroids sorted by E(i), then E(i+1)."""
    rng = np.random.default_rng(seed)
    best: tuple[np.ndarray, float] | None = None
    for _ in range(restarts):
        centers, sse, _ = _lloyd(points, _kmeans_plus_plus(points, k, rng), max_iter)
        if best is None or sse < best[1]:
            best = (centers, sse)
    assert best is not None
    centers, sse = best
    order = np.lexsort((centers[:, 1], centers[:, 0]))
    return np.ascontiguousarray(centers[order]), sse


def _app_version() -> str:
    try:
        from tether import __version__

        return str(__version__)
    except Exception:  # pragma: no cover - defensive; __version__ has its own fallback
        return "0.0.0+unknown"


def successive_frame_cloud(
    fret_chunks: Iterable[np.ndarray],
    *,
    grid_points: int = DEFAULT_SUCCESSIVE_GRID_POINTS,
    value_range: tuple[float, float] = DEFAULT_SUCCESSIVE_RANGE,
    bandwidth: float = DEFAULT_SUCCESSIVE_BANDWIDTH,
    hdr_coverages: Sequence[float] = DEFAULT_SUCCESSIVE_HDR_COVERAGES,
    n_centroids: int | None = DEFAULT_SUCCESSIVE_N_CENTROIDS,
    kmeans_restarts: int = DEFAULT_SUCCESSIVE_KMEANS_RESTARTS,
    kmeans_max_iter: int = DEFAULT_SUCCESSIVE_KMEANS_MAX_ITER,
    seed: int = DEFAULT_SUCCESSIVE_SEED,
) -> SuccessiveFrameCloud:
    """The successive-frame FRET cloud of per-molecule apparent-E arrays (the pure core).

    Parameters
    ----------
    fret_chunks
        One 1-D apparent-E array per molecule, cut to its analysis window; a non-finite frame is
        masked (:func:`successive_frame_pairs`).
    grid_points
        Nodes on each axis (``>= 2``).
    value_range
        ``(lo, hi)`` E range of the grid on both axes (``hi > lo``).
    bandwidth
        Gaussian kernel bandwidth on both axes, in E units (``> 0``).
    hdr_coverages
        Coverages in ``(0, 1)`` whose density thresholds are returned, sorted ascending.
    n_centroids
        k-means centroids to find (``>= 1``), or ``None`` for none.
    kmeans_restarts
        k-means++ starts (``>= 1``); the run with the smallest sum of squares is kept.
    kmeans_max_iter
        Iteration cap of one k-means run (``>= 1``).
    seed
        Seed of the k-means++ starts.

    Returns
    -------
    SuccessiveFrameCloud

    Raises
    ------
    ValueError
        A parameter is outside the range stated above, or an element of ``fret_chunks`` is a
        scalar.
    """
    g = int(grid_points)
    if g < 2:
        raise ValueError(f"grid_points must be >= 2, got {grid_points!r}")
    lo, hi = float(value_range[0]), float(value_range[1])
    if not (np.isfinite(lo) and np.isfinite(hi) and hi > lo):
        raise ValueError(f"value_range must be finite with hi > lo, got {value_range!r}")
    h = float(bandwidth)
    if not (np.isfinite(h) and h > 0.0):
        raise ValueError(f"bandwidth must be finite and > 0, got {bandwidth!r}")
    coverages = np.sort(np.asarray(hdr_coverages, dtype=np.float64))
    if coverages.size and not (np.all(coverages > 0.0) and np.all(coverages < 1.0)):
        raise ValueError(f"hdr_coverages must each be in (0, 1), got {tuple(hdr_coverages)!r}")
    if n_centroids is not None and int(n_centroids) < 1:
        raise ValueError(f"n_centroids must be >= 1 or None, got {n_centroids!r}")
    if int(kmeans_restarts) < 1:
        raise ValueError(f"kmeans_restarts must be >= 1, got {kmeans_restarts!r}")
    if int(kmeans_max_iter) < 1:
        raise ValueError(f"kmeans_max_iter must be >= 1, got {kmeans_max_iter!r}")

    pairs = successive_frame_pairs(fret_chunks)
    points = pairs.pairs
    grid = np.linspace(lo, hi, g)
    density = _density(points, grid, h)
    step = float(grid[1] - grid[0])
    levels = None if density is None else _hdr_levels_from_grid(density, step * step, coverages)

    centroids: np.ndarray | None = None
    sse: float | None = None
    if n_centroids is not None and points.shape[0]:
        k = min(int(n_centroids), int(np.unique(points, axis=0).shape[0]))
        centroids, sse = _kmeans(points, k, int(kmeans_restarts), int(seed), int(kmeans_max_iter))

    outside = np.any((points < lo) | (points > hi), axis=1)
    provenance = {
        "analysis": "successive_frame_cloud",
        "app_version": _app_version(),
        "parameters": {
            "grid_points": g,
            "value_range": [lo, hi],
            "bandwidth": h,
            "hdr_coverages": coverages.tolist(),
            "n_centroids": None if n_centroids is None else int(n_centroids),
            "kmeans_restarts": int(kmeans_restarts),
            "kmeans_max_iter": int(kmeans_max_iter),
            "seed": int(seed),
        },
        "n_pairs": pairs.n_pairs,
        "n_molecules": pairs.n_molecules,
    }
    return SuccessiveFrameCloud(
        pairs=pairs,
        grid=grid,
        density=density,
        hdr_coverages=np.ascontiguousarray(coverages),
        hdr_levels=levels,
        bandwidth=h,
        centroids=centroids,
        centroid_sse=sse,
        n_out_of_range=int(np.count_nonzero(outside)),
        provenance=provenance,
    )


def population_successive_frame_cloud(
    project: ProjectRef,
    *,
    molecule_keys: list[str] | None = None,
    intensity_quantity: str = "corrected",
    include_rejected: bool = False,
    grid_points: int = DEFAULT_SUCCESSIVE_GRID_POINTS,
    value_range: tuple[float, float] = DEFAULT_SUCCESSIVE_RANGE,
    bandwidth: float = DEFAULT_SUCCESSIVE_BANDWIDTH,
    hdr_coverages: Sequence[float] = DEFAULT_SUCCESSIVE_HDR_COVERAGES,
    n_centroids: int | None = DEFAULT_SUCCESSIVE_N_CENTROIDS,
    kmeans_restarts: int = DEFAULT_SUCCESSIVE_KMEANS_RESTARTS,
    kmeans_max_iter: int = DEFAULT_SUCCESSIVE_KMEANS_MAX_ITER,
    seed: int = DEFAULT_SUCCESSIVE_SEED,
) -> SuccessiveFrameCloud:
    """Successive-frame FRET cloud from a ``.tether`` store (#710, PRD §7.7).

    Each accepted molecule's apparent E (:func:`tether.fret.efficiency.apparent_fret` of the
    ``intensity_quantity`` channels) is cut to its analysis window — which by default ends at the
    first photobleach — and its frames are paired within the window only. Rejected molecules are
    left out unless ``include_rejected`` (§7.5). The view reads ``/traces`` only, never an
    idealization. :attr:`SuccessiveFramePairs.molecule_index` counts the molecules that the
    filter kept, in store order. The other parameters go to :func:`successive_frame_cloud`.
    """
    from tether.analysis._store import windowed_channels
    from tether.fret.efficiency import apparent_fret

    channels = windowed_channels(project, molecule_keys, intensity_quantity, include_rejected)
    cloud = successive_frame_cloud(
        [apparent_fret(donor, acceptor) for donor, acceptor in channels],
        grid_points=grid_points,
        value_range=value_range,
        bandwidth=bandwidth,
        hdr_coverages=hdr_coverages,
        n_centroids=n_centroids,
        kmeans_restarts=kmeans_restarts,
        kmeans_max_iter=kmeans_max_iter,
        seed=seed,
    )
    cloud.provenance["parameters"].update(
        {
            "intensity_quantity": intensity_quantity,
            "include_rejected": bool(include_rejected),
            "molecule_keys": None if molecule_keys is None else list(molecule_keys),
        }
    )
    return cloud
