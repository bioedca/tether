# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""1-D population apparent-E histogram (PRD §7.7 FR-ANALYZE; Appendix C plot A1).

The population FRET-efficiency histogram — tMAVEN's A1 plot
(``tmaven/tmaven/controllers/analysis_plots/data_hist1d.py``): pool the per-frame
apparent-E over each selected molecule's analysis window and bin it. Defaults
mirror tMAVEN's A1 — ``signal_nbins = 151`` bins over ``[-0.25, 1.25]``,
density-normalized — so the *uncorrected* proximity ratio's excursions beyond
``[0, 1]`` on noisy frames stay visible rather than clipped away [McCann2010]
(``tether.fret.apparent_fret`` deliberately does not clip). Non-finite samples
(``apparent_fret`` yields NaN where ``D + A == 0``) are dropped before binning —
never fabricated.

Two headless entry points:

- :func:`apparent_e_histogram` — the pure-array core (bin a pooled 1-D sample).
- :func:`population_apparent_e_histogram` — read a ``.tether`` store, pool the
  accepted molecules' windowed apparent-E, and reproduce the MVP histogram (the
  PRD §9 M2 acceptance clause "reproduce the MVP histogram from the API").
  Rejected traces are excluded by default via the §7.5 curation filter, with a
  per-molecule equal-weight toggle (§7.7).

References
----------
[McCann2010] McCann, Choi, Zheng, Bahlke, Zhu, Nienhaus, Schuler & Weiss.
    "Recovering absolute FRET efficiency from single molecules: comparing methods
    of gamma correction." Biophysical Journal (2010).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

import numpy as np

from tether.analysis._store import windowed_channels
from tether.fret.efficiency import apparent_fret

if TYPE_CHECKING:  # pragma: no cover - typing only
    from tether.analysis._store import ProjectRef

__all__ = [
    "DEFAULT_NBINS",
    "DEFAULT_RANGE",
    "Histogram1D",
    "apparent_e_histogram",
    "population_apparent_e_histogram",
]

#: tMAVEN A1 defaults (``data_hist1d.py``): 151 bins over apparent E ∈ [-0.25, 1.25].
#: Integer ``bins`` is a *bin count* (numpy/matplotlib semantics), so this yields
#: 151 bins / 152 edges.
DEFAULT_NBINS = 151
DEFAULT_RANGE: tuple[float, float] = (-0.25, 1.25)


@dataclass(frozen=True)
class Histogram1D:
    """A binned 1-D apparent-E population histogram (self-describing for NFR-REPRO).

    ``counts`` is the density (if ``density``) or the raw/weighted bin counts;
    row ``i`` spans ``[bin_edges[i], bin_edges[i + 1])``. The parameters that
    produced it (range, density, per-molecule weighting, sample/molecule counts)
    travel with the array so the view is reproducible without re-deriving it.
    """

    counts: np.ndarray  # (nbins,) float64
    bin_edges: np.ndarray  # (nbins + 1,) float64
    density: bool
    value_range: tuple[float, float]
    n_samples: int  # finite per-frame apparent-E values fed to the histogram
    n_molecules: int | None  # molecules pooled (project API); None for the pure core
    per_molecule_equal_weight: bool

    @property
    def bin_centers(self) -> np.ndarray:
        e = self.bin_edges
        return 0.5 * (e[:-1] + e[1:])

    @property
    def nbins(self) -> int:
        return int(self.counts.shape[0])


def apparent_e_histogram(
    values: np.ndarray,
    *,
    bins: int = DEFAULT_NBINS,
    value_range: tuple[float, float] = DEFAULT_RANGE,
    density: bool = True,
    weights: np.ndarray | None = None,
) -> Histogram1D:
    """Bin a pooled 1-D apparent-E sample (the pure-array core).

    Parameters
    ----------
    values
        Pooled per-frame apparent-E values (any shape; flattened). Non-finite
        entries (NaN/inf) are dropped before binning.
    bins
        Bin count (default :data:`DEFAULT_NBINS` = 151, matching tMAVEN A1).
    value_range
        ``(low, high)`` histogram range (default :data:`DEFAULT_RANGE`). Finite
        values outside it fall in no bin (consistent with tMAVEN's fixed range).
    density
        If ``True`` (default), normalize so the histogram integrates to 1 over
        ``value_range``; else return (weighted) counts.
    weights
        Optional per-sample weights (same shape as ``values``); used by the
        per-molecule equal-weight path. Non-finite-weighted samples are dropped.

    Returns
    -------
    Histogram1D
    """
    values = np.asarray(values, dtype=np.float64).ravel()
    finite = np.isfinite(values)
    w: np.ndarray | None = None
    if weights is not None:
        weights = np.asarray(weights, dtype=np.float64).ravel()
        if weights.shape != values.shape:
            raise ValueError(
                f"weights shape {weights.shape} must match values shape {values.shape}"
            )
        finite &= np.isfinite(weights)
        w = weights[finite]
    v = values[finite]

    lo, hi = float(value_range[0]), float(value_range[1])
    if not hi > lo:
        raise ValueError(f"value_range must be (low, high) with high > low, got {value_range!r}")
    if int(bins) < 1:
        raise ValueError(f"bins must be a positive integer, got {bins!r}")

    # Raw (weighted) counts first, then normalize density by hand: np.histogram's
    # density path divides by the total, so an empty or all-out-of-range sample
    # yields 0/0 = NaN. Zeros are the honest "no data" answer — never fabricate NaN.
    counts, edges = np.histogram(v, bins=int(bins), range=(lo, hi), weights=w)
    counts = counts.astype(np.float64)
    if density:
        total = float(counts.sum())
        if total > 0.0:
            counts = counts / np.diff(edges) / total
    return Histogram1D(
        counts=counts,
        bin_edges=edges.astype(np.float64),
        density=density,
        value_range=(lo, hi),
        n_samples=int(v.shape[0]),
        n_molecules=None,
        per_molecule_equal_weight=False,
    )


def population_apparent_e_histogram(
    project: ProjectRef,
    *,
    molecule_keys: list[str] | None = None,
    intensity_quantity: str = "corrected",
    bins: int = DEFAULT_NBINS,
    value_range: tuple[float, float] = DEFAULT_RANGE,
    density: bool = True,
    per_molecule_equal_weight: bool = False,
    include_rejected: bool = False,
) -> Histogram1D:
    """Reproduce the MVP apparent-E histogram from a ``.tether`` store (PRD §9 M2).

    Pools the per-frame apparent-E over each selected molecule's analysis window
    and bins it with :func:`apparent_e_histogram`. This is the headless source of
    truth the GUI population histogram renders.

    Parameters
    ----------
    project
        A :class:`~tether.project.core.Project` or a path to a ``.tether`` store.
    molecule_keys
        Restrict to these molecules (``None`` = all). Combined with the curation
        filter by intersection.
    intensity_quantity
        Which ``/traces`` layer feeds apparent E: ``"corrected"`` (default,
        background-subtracted — the apparent-E input at M2) or ``"raw"``.
    bins, value_range, density
        Binning parameters (see :func:`apparent_e_histogram`).
    per_molecule_equal_weight
        If ``True``, every molecule contributes total weight 1 (each of its
        finite frames weighted ``1/m``) so long traces do not dominate (§7.7);
        else every finite frame is weighted equally (tMAVEN A1 behavior).
    include_rejected
        If ``True``, keep rejected molecules; else exclude them (§7.5 default).

    Returns
    -------
    Histogram1D
        With ``n_molecules`` set to the number of molecules that contributed at
        least one finite frame.
    """
    pairs = windowed_channels(project, molecule_keys, intensity_quantity, include_rejected)

    value_chunks: list[np.ndarray] = []
    weight_chunks: list[np.ndarray] = []
    n_molecules = 0
    for donor, acceptor in pairs:
        e = apparent_fret(donor, acceptor)
        finite = np.isfinite(e)
        m = int(finite.sum())
        if m == 0:
            continue  # molecule with no valid frames contributes nothing (not a zero)
        n_molecules += 1
        value_chunks.append(e[finite])
        if per_molecule_equal_weight:
            weight_chunks.append(np.full(m, 1.0 / m, dtype=np.float64))

    if value_chunks:
        values = np.concatenate(value_chunks)
        weights = np.concatenate(weight_chunks) if per_molecule_equal_weight else None
    else:
        values = np.empty(0, dtype=np.float64)
        weights = np.empty(0, dtype=np.float64) if per_molecule_equal_weight else None

    hist = apparent_e_histogram(
        values, bins=bins, value_range=value_range, density=density, weights=weights
    )
    return replace(
        hist, n_molecules=n_molecules, per_molecule_equal_weight=per_molecule_equal_weight
    )
