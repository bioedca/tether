# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Nearest-neighbour distance + aperture-overlap flagging (PRD §7.3, §5.1).

The Qt-free core of the M2 S10 **static overlap view**: for every molecule it
computes the distance to its nearest neighbour and flags the pair whose
integration apertures may overlap, so a curator can spot crowded/contaminated
spots (PRD §7.3: "a neighbor/overlap view — static patch + nearest-neighbour
distance"). The GUI half (:mod:`tether.gui.overlap_dock`) only *renders* what this
module returns; no geometry lives in the widget.

**Which spots.** A molecule owns a **donor** and an **acceptor** spot, imaged in
the two channel halves of the split frame (donor crop vs acceptor crop, §5.2), so
a donor aperture can only overlap **another donor** aperture — never an acceptor
one across the halves. The nearest-neighbour distance is therefore computed over
the **donor** spots (Tether's detection anchor since M1), per movie: molecules in
different movies never neighbour each other (§5.2), so the caller passes each
molecule's ``movie_id`` as its group and the neighbour search runs within a group.

**Overlap geometry (settled, not a tunable).** Each aperture is a PSF disk of
radius ``aperture_radius`` px (default 3, the Deep-LASI disk; PRD Appendix E /
§11.2). Two disks of radius ``r`` overlap **iff** their centres are closer than
``2·r`` (:data:`APERTURE_OVERLAP_FACTOR` × radius) — pure geometry, so the only
free input is the aperture radius (an existing §11.2 parameter, read from the
store's extraction settings), not a new threshold. Note native Tether detection
enforces an **8 px min-separation** per channel half (PRD §11.2 / Appendix E), so
freshly-extracted donor apertures (radius 3 → overlap < 6 px) never overlap; the
flag earns its keep on **imported** coordinates (Deep-LASI / tMAVEN SMD) that were
not subject to that min-separation, and the raw NN distance is an always-useful
crowding readout regardless.

Everything here is pure NumPy + SciPy (the ``cKDTree`` already used by
:mod:`tether.imaging.coloc`), so it runs in the default test matrix without Qt.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "APERTURE_OVERLAP_FACTOR",
    "DEFAULT_APERTURE_RADIUS",
    "NeighborReport",
    "OverlapInfo",
    "neighbor_report",
]

#: PSF-disk aperture radius in px (Deep-LASI disk; PRD Appendix E / §11.2). The
#: same default as :class:`tether.gui.movie_panel.MovieOverlay`.
DEFAULT_APERTURE_RADIUS = 3.0

#: Two apertures overlap when their centre-to-centre distance is below this many
#: aperture radii. ``2.0`` is the exact geometric condition for two equal disks to
#: touch/overlap — it is a definition, not a tunable.
APERTURE_OVERLAP_FACTOR = 2.0


def _validate_coords(coords: object) -> np.ndarray:
    """Validate an ``(N, 2)`` ``[x, y]`` spot-coordinate array (empty allowed).

    Mirrors :func:`tether.gui.movie_panel._validate_xy`: an empty 1-D input maps
    to ``(0, 2)``; any other non-``(N, 2)`` shape or a non-finite value is rejected
    so a NaN can never silently poison the KDTree.
    """
    arr = np.asarray(coords, dtype=np.float64)
    if arr.ndim == 1 and arr.size == 0:
        return np.empty((0, 2), dtype=np.float64)
    if arr.ndim != 2 or arr.shape[1] != 2:
        raise ValueError(f"coords must be an (N, 2) [x, y] array, got shape {arr.shape}")
    if not np.isfinite(arr).all():
        raise ValueError("coords must be finite")
    return arr


@dataclass(frozen=True)
class NeighborReport:
    """Per-molecule nearest-neighbour distance + aperture-overlap flags.

    Every field is aligned to the input ``coords`` row order. A molecule with no
    neighbour (the only one in its movie, or an empty input) carries
    ``nn_index == -1``, ``nn_distance == inf`` and ``overlaps == False``.

    Parameters
    ----------
    nn_index:
        ``(N,)`` ``intp`` — the row of each molecule's nearest neighbour, or ``-1``.
    nn_distance:
        ``(N,)`` ``float64`` — centre-to-centre distance to that neighbour (``inf``
        when there is none).
    overlaps:
        ``(N,)`` ``bool`` — ``nn_distance < overlap_distance``.
    aperture_radius:
        The PSF-disk radius (px) the overlap test used.
    overlap_distance:
        The centre-distance below which two apertures overlap
        (``APERTURE_OVERLAP_FACTOR × aperture_radius``).
    """

    nn_index: np.ndarray
    nn_distance: np.ndarray
    overlaps: np.ndarray
    aperture_radius: float
    overlap_distance: float

    @property
    def n_molecules(self) -> int:
        """Number of molecules the report covers."""
        return int(self.nn_index.shape[0])

    @property
    def n_overlapping(self) -> int:
        """How many molecules have a neighbour inside the overlap distance."""
        return int(np.count_nonzero(self.overlaps))

    def neighbor_of(self, index: int) -> int | None:
        """The nearest-neighbour row of molecule ``index``, or ``None`` if isolated."""
        i = int(self.nn_index[index])
        return i if i >= 0 else None

    def distance_of(self, index: int) -> float:
        """The nearest-neighbour distance of molecule ``index`` (``inf`` if isolated)."""
        return float(self.nn_distance[index])

    def overlaps_at(self, index: int) -> bool:
        """Whether molecule ``index``'s aperture overlaps its nearest neighbour's."""
        return bool(self.overlaps[index])


def neighbor_report(
    coords: object,
    *,
    aperture_radius: float = DEFAULT_APERTURE_RADIUS,
    groups: Sequence[object] | np.ndarray | None = None,
) -> NeighborReport:
    """Nearest-neighbour distance + aperture-overlap flag for each molecule.

    Parameters
    ----------
    coords:
        ``(N, 2)`` ``[x, y]`` spot centres (the ``/molecules`` ``donor_xy``
        convention, §5.1). Euclidean distance is transpose-invariant, so this works
        equally in ``[x, y]`` or napari ``[row, col]``; keep it ``[x, y]`` to match
        detection/storage.
    aperture_radius:
        PSF-disk radius in px (default :data:`DEFAULT_APERTURE_RADIUS`). Two
        apertures overlap when their centres are within
        ``APERTURE_OVERLAP_FACTOR × aperture_radius``. Must be finite and positive.
    groups:
        Optional length-``N`` labels (e.g. each molecule's ``movie_id``) confining
        the neighbour search to within a group — molecules in different movies are
        never neighbours (§5.2). ``None`` treats every molecule as one group.

    Returns
    -------
    NeighborReport
        Row-aligned to ``coords``.
    """
    xy = _validate_coords(coords)
    n = xy.shape[0]
    if not (np.isfinite(aperture_radius) and float(aperture_radius) > 0.0):
        raise ValueError(f"aperture_radius must be finite and positive, got {aperture_radius!r}")
    overlap_distance = APERTURE_OVERLAP_FACTOR * float(aperture_radius)

    nn_index = np.full(n, -1, dtype=np.intp)
    nn_distance = np.full(n, np.inf, dtype=np.float64)

    if groups is None:
        group_labels: np.ndarray = np.zeros(n, dtype=np.intp)
    else:
        group_labels = np.asarray(groups)
        if group_labels.shape != (n,):
            raise ValueError(
                f"groups must be length {n} to match coords, got shape {group_labels.shape}"
            )

    for label in np.unique(group_labels):
        members = np.flatnonzero(group_labels == label)
        if members.size < 2:
            continue  # a lone molecule in its movie has no neighbour (nn_index stays -1)
        idx, dist = _nearest_within(xy[members])
        # Map the group-local nearest-neighbour rows back to global row indices.
        has = idx >= 0
        nn_index[members[has]] = members[idx[has]]
        nn_distance[members[has]] = dist[has]

    overlaps = nn_distance < overlap_distance
    return NeighborReport(
        nn_index=nn_index,
        nn_distance=nn_distance,
        overlaps=overlaps,
        aperture_radius=float(aperture_radius),
        overlap_distance=overlap_distance,
    )


@dataclass(frozen=True)
class OverlapInfo:
    """One molecule's overlap-view payload — its static patch + NN readout.

    The value a store-backed overlap seam hands the GUI
    (:class:`tether.gui.overlap_dock.OverlapDock`) for the selected molecule. It is
    Qt-free (a plain NumPy patch + scalars) so the seam can be built and asserted in
    the default test matrix. ``patch`` is ``None`` for an **analysis-only** project
    that carries no cached patches (§7.4: coordinates/patches absent → the overlap
    view degrades rather than fabricating an image).

    Parameters
    ----------
    nn_distance:
        Distance to the nearest neighbour in px (``inf`` when the molecule is the
        only one in its movie).
    overlaps:
        Whether that neighbour's aperture overlaps this one's.
    aperture_radius:
        The PSF-disk radius (px) the overlap test used.
    patch:
        ``(w, w)`` cached donor image patch for the static view, or ``None``.
    name:
        A short label for the molecule (its list name / key).
    nn_molecule_key:
        The nearest neighbour's ``molecule_key``, or ``None`` if isolated.
    """

    nn_distance: float
    overlaps: bool
    aperture_radius: float
    patch: np.ndarray | None = None
    name: str | None = None
    nn_molecule_key: str | None = None


def _nearest_within(points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Nearest *other* point for each of ``points`` (≥2 rows) via a KDTree.

    Returns ``(index, distance)`` where ``index[i]`` is the local row of ``i``'s
    nearest neighbour and ``distance[i]`` the Euclidean distance. A ``k=2`` query
    returns the point itself (distance 0) and its nearest distinct point, but with
    **tied distances** — two spots at the same centre — SciPy may order the self
    match into either column, so self is excluded by *identity* (the row index),
    not by position: whichever returned column is not the row itself is the
    neighbour. A genuine coincident spot (distance 0) is a real overlap and is kept.
    """
    from scipy.spatial import cKDTree

    tree = cKDTree(points)
    distances, indices = tree.query(points, k=2)
    rows = np.arange(points.shape[0])
    # Take column 1 when column 0 is the point itself, else column 0 (the case a
    # distance-0 tie put self in column 1, or 3+ coincident spots pushed self out).
    self_in_col0 = indices[:, 0] == rows
    nbr_index = np.where(self_in_col0, indices[:, 1], indices[:, 0])
    nbr_dist = np.where(self_in_col0, distances[:, 1], distances[:, 0])
    return nbr_index.astype(np.intp), nbr_dist.astype(np.float64)
