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

Entry points:

- :func:`apparent_e_histogram` — the pure-array core (bin a pooled 1-D sample).
- :func:`population_apparent_e_histogram` — read a ``.tether`` store, pool the
  accepted molecules' windowed apparent-E, and reproduce the MVP histogram (the
  PRD §9 M2 acceptance clause "reproduce the MVP histogram from the API").
- :func:`bootstrap_histogram_ci` — the pure bootstrap core: resample **molecules**
  with replacement and re-bin, yielding per-bin percentile confidence intervals
  (the FRET histogram's error bars, PRD §7.7).
- :func:`population_apparent_e_histogram_ci` — the store-level bootstrap CI over
  the accepted molecules.

Rejected traces are excluded by default via the §7.5 curation filter, with a
per-molecule equal-weight toggle (§7.7).

**Bootstrap resampling unit — the molecule.** Following [König2013] (BOBA-FRET,
also implemented in MASH-FRET [Börner2018]), the error bars quantify *cross-sample*
variability: single molecules differ in how they populate the FRET states, so the
resampled unit is the molecule, not the frame. Each bootstrap replicate draws
``n_molecules`` molecules with replacement, re-pools their windowed apparent-E,
re-bins, and the per-bin 2.5/97.5 percentiles over the replicates give the 95%
CI. A molecule drawn twice contributes twice — under the per-molecule
equal-weight toggle each copy still carries total weight 1, so a duplicate counts
as weight 2. With a single molecule (or identical molecules) there is no
cross-sample variability, so the interval collapses onto the point estimate — the
honest answer, never a fabricated spread.

References
----------
[McCann2010] McCann, Choi, Zheng, Bahlke, Zhu, Nienhaus, Schuler & Weiss.
    "Recovering absolute FRET efficiency from single molecules: comparing methods
    of gamma correction." Biophysical Journal (2010).
[König2013] König, Hadzic, Fiorini, Börner, Acuna, Kowerko, Sigel et al.
    "BOBA FRET: bootstrap-based analysis of single-molecule FRET data."
    PLoS ONE 8(12):e84157 (2013).
[Börner2018] Börner, Kowerko, Hadzic, König, Ritter, Sigel. "Simulations of
    camera-based single-molecule fluorescence experiments." PLoS ONE (2018).
    (MASH-FRET, whose BOBA-FRET module performs the same bootstrap.)
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
    "DEFAULT_BOOTSTRAP_RESAMPLES",
    "DEFAULT_CI_LEVEL",
    "DEFAULT_NBINS",
    "DEFAULT_RANGE",
    "DEFAULT_SEED",
    "Histogram1D",
    "HistogramBootstrapCI",
    "apparent_e_histogram",
    "bootstrap_histogram_ci",
    "population_apparent_e_histogram",
    "population_apparent_e_histogram_ci",
]

#: tMAVEN A1 defaults (``data_hist1d.py``): 151 bins over apparent E ∈ [-0.25, 1.25].
#: Integer ``bins`` is a *bin count* (numpy/matplotlib semantics), so this yields
#: 151 bins / 152 edges.
DEFAULT_NBINS = 151
DEFAULT_RANGE: tuple[float, float] = (-0.25, 1.25)

#: Bootstrap defaults (PRD §11.2 "FRET-histogram bootstrap" row; [König2013]).
#: ``DEFAULT_BOOTSTRAP_RESAMPLES`` replicates give stable 95% percentile bands;
#: ``DEFAULT_SEED`` makes the CI reproducible (the §9 M3 "seeded" test).
DEFAULT_BOOTSTRAP_RESAMPLES = 1000
DEFAULT_CI_LEVEL = 0.95
DEFAULT_SEED = 0


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


@dataclass(frozen=True)
class HistogramBootstrapCI:
    """A :class:`Histogram1D` point estimate plus its bootstrap confidence band.

    ``histogram`` is the observed histogram (the point estimate — *not* the
    bootstrap mean, which would be biased). ``ci_low``/``ci_high`` are the
    per-bin percentile bounds of the ``ci_level`` interval over ``n_resamples``
    molecule-level bootstrap replicates (:func:`bootstrap_histogram_ci`), and
    ``std`` is the per-bin replicate standard deviation. All parameters travel
    with the arrays so the band is reproducible (NFR-REPRO).
    """

    histogram: Histogram1D
    ci_low: np.ndarray  # (nbins,) float64 — lower percentile per bin
    ci_high: np.ndarray  # (nbins,) float64 — upper percentile per bin
    std: np.ndarray  # (nbins,) float64 — replicate std per bin
    n_resamples: int
    ci_level: float
    seed: int

    @property
    def counts(self) -> np.ndarray:
        return self.histogram.counts

    @property
    def bin_edges(self) -> np.ndarray:
        return self.histogram.bin_edges

    @property
    def bin_centers(self) -> np.ndarray:
        return self.histogram.bin_centers

    @property
    def nbins(self) -> int:
        return self.histogram.nbins

    @property
    def value_range(self) -> tuple[float, float]:
        return self.histogram.value_range

    @property
    def n_molecules(self) -> int | None:
        return self.histogram.n_molecules

    @property
    def yerr_low(self) -> np.ndarray:
        """Non-negative lower error-bar length (``counts - ci_low``, clamped ≥ 0).

        The point estimate can fall just outside the percentile band (it is the
        observed histogram, not the replicate mean), so clamp so error bars never
        go negative when a GUI renders them.
        """
        return np.maximum(self.histogram.counts - self.ci_low, 0.0)

    @property
    def yerr_high(self) -> np.ndarray:
        """Non-negative upper error-bar length (``ci_high - counts``, clamped ≥ 0)."""
        return np.maximum(self.ci_high - self.histogram.counts, 0.0)


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


def bootstrap_histogram_ci(
    per_molecule_values: list[np.ndarray],
    *,
    per_molecule_weights: list[np.ndarray] | None = None,
    bins: int = DEFAULT_NBINS,
    value_range: tuple[float, float] = DEFAULT_RANGE,
    density: bool = True,
    n_resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES,
    ci_level: float = DEFAULT_CI_LEVEL,
    seed: int = DEFAULT_SEED,
) -> HistogramBootstrapCI:
    """Molecule-level bootstrap CI for a pooled apparent-E histogram (pure core).

    Resample the *molecules* with replacement ``n_resamples`` times, re-pool and
    re-bin each replicate with :func:`apparent_e_histogram`, and take the per-bin
    ``ci_level`` percentile band ([König2013] BOBA-FRET; see the module docstring
    for why the molecule is the resampled unit).

    Parameters
    ----------
    per_molecule_values
        One 1-D apparent-E array per molecule (each already restricted to its
        analysis window). Non-finite entries are dropped by the binning core.
    per_molecule_weights
        Optional matching list of per-frame weights (the per-molecule
        equal-weight path passes ``1/m`` for each of a molecule's ``m`` frames).
        Must align element-wise with ``per_molecule_values``.
    bins, value_range, density
        Binning parameters (see :func:`apparent_e_histogram`); the point estimate
        and every replicate share them.
    n_resamples
        Number of bootstrap replicates (default
        :data:`DEFAULT_BOOTSTRAP_RESAMPLES`).
    ci_level
        Central interval width in ``(0, 1)`` (default :data:`DEFAULT_CI_LEVEL`
        = 0.95 → the 2.5/97.5 percentiles).
    seed
        Seed for :func:`numpy.random.default_rng` (default :data:`DEFAULT_SEED`),
        making the interval reproducible.

    Returns
    -------
    HistogramBootstrapCI
        Wrapping the observed point-estimate :class:`Histogram1D`. With no
        molecules the band is all zeros (never NaN).
    """
    n_resamples = int(n_resamples)
    if n_resamples < 1:
        raise ValueError(f"n_resamples must be a positive integer, got {n_resamples!r}")
    ci_level = float(ci_level)
    if not 0.0 < ci_level < 1.0:
        raise ValueError(f"ci_level must be in (0, 1), got {ci_level!r}")

    n_mol = len(per_molecule_values)
    has_weights = per_molecule_weights is not None
    if has_weights:
        if len(per_molecule_weights) != n_mol:
            raise ValueError(
                f"per_molecule_weights length {len(per_molecule_weights)} must match "
                f"per_molecule_values length {n_mol}"
            )
        for i, (v, w) in enumerate(zip(per_molecule_values, per_molecule_weights, strict=True)):
            if np.asarray(w).shape != np.asarray(v).shape:
                raise ValueError(
                    f"per_molecule_weights[{i}] shape {np.asarray(w).shape} must match "
                    f"per_molecule_values[{i}] shape {np.asarray(v).shape}"
                )

    def _pool(chunks: list[np.ndarray]) -> np.ndarray:
        return np.concatenate([np.asarray(c, dtype=np.float64).ravel() for c in chunks])

    # Observed point estimate: pool every molecule once.
    if n_mol:
        values = _pool(per_molecule_values)
        weights = _pool(per_molecule_weights) if has_weights else None
    else:
        values = np.empty(0, dtype=np.float64)
        weights = np.empty(0, dtype=np.float64) if has_weights else None
    point = apparent_e_histogram(
        values, bins=bins, value_range=value_range, density=density, weights=weights
    )
    nbins = point.nbins

    if n_mol == 0:
        zeros = np.zeros(nbins, dtype=np.float64)
        return HistogramBootstrapCI(
            histogram=point,
            ci_low=zeros,
            ci_high=zeros.copy(),
            std=zeros.copy(),
            n_resamples=n_resamples,
            ci_level=ci_level,
            seed=int(seed),
        )

    rng = np.random.default_rng(seed)
    boot = np.empty((n_resamples, nbins), dtype=np.float64)
    for b in range(n_resamples):
        idx = rng.integers(0, n_mol, size=n_mol)  # high exclusive → indices in [0, n_mol)
        vals_b = np.concatenate([per_molecule_values[i] for i in idx])
        w_b = np.concatenate([per_molecule_weights[i] for i in idx]) if has_weights else None
        rep = apparent_e_histogram(
            vals_b, bins=bins, value_range=value_range, density=density, weights=w_b
        )
        boot[b] = rep.counts

    alpha = (1.0 - ci_level) / 2.0
    ci_low = np.percentile(boot, 100.0 * alpha, axis=0)
    ci_high = np.percentile(boot, 100.0 * (1.0 - alpha), axis=0)
    std = boot.std(axis=0, ddof=1) if n_resamples > 1 else np.zeros(nbins, dtype=np.float64)
    return HistogramBootstrapCI(
        histogram=point,
        ci_low=ci_low.astype(np.float64),
        ci_high=ci_high.astype(np.float64),
        std=std.astype(np.float64),
        n_resamples=n_resamples,
        ci_level=ci_level,
        seed=int(seed),
    )


def _per_molecule_apparent_e(
    project: ProjectRef,
    molecule_keys: list[str] | None,
    intensity_quantity: str,
    include_rejected: bool,
    *,
    per_molecule_equal_weight: bool,
) -> tuple[list[np.ndarray], list[np.ndarray] | None]:
    """Read a ``.tether`` store into per-molecule windowed apparent-E arrays.

    Returns ``(value_chunks, weight_chunks)`` where ``value_chunks[i]`` is the
    finite windowed apparent-E of the i-th contributing molecule (molecules with
    no finite frame are dropped, not zeroed). ``weight_chunks`` is a matching list
    of ``1/m`` per-frame weights when ``per_molecule_equal_weight`` (each molecule
    totals weight 1), else ``None``. Shared by the point-estimate and bootstrap-CI
    entry points so they pool identically.
    """
    pairs = windowed_channels(project, molecule_keys, intensity_quantity, include_rejected)
    value_chunks: list[np.ndarray] = []
    weight_chunks: list[np.ndarray] = []
    for donor, acceptor in pairs:
        e = apparent_fret(donor, acceptor)
        finite = np.isfinite(e)
        m = int(finite.sum())
        if m == 0:
            continue  # molecule with no valid frames contributes nothing (not a zero)
        value_chunks.append(e[finite])
        if per_molecule_equal_weight:
            weight_chunks.append(np.full(m, 1.0 / m, dtype=np.float64))
    return value_chunks, (weight_chunks if per_molecule_equal_weight else None)


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
    value_chunks, weight_chunks = _per_molecule_apparent_e(
        project,
        molecule_keys,
        intensity_quantity,
        include_rejected,
        per_molecule_equal_weight=per_molecule_equal_weight,
    )
    n_molecules = len(value_chunks)
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


def population_apparent_e_histogram_ci(
    project: ProjectRef,
    *,
    molecule_keys: list[str] | None = None,
    intensity_quantity: str = "corrected",
    bins: int = DEFAULT_NBINS,
    value_range: tuple[float, float] = DEFAULT_RANGE,
    density: bool = True,
    per_molecule_equal_weight: bool = False,
    include_rejected: bool = False,
    n_resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES,
    ci_level: float = DEFAULT_CI_LEVEL,
    seed: int = DEFAULT_SEED,
) -> HistogramBootstrapCI:
    """Population apparent-E histogram with a molecule-level bootstrap CI (§7.7).

    The confidence-interval counterpart of :func:`population_apparent_e_histogram`:
    same pooling and curation semantics, plus per-bin error bars from a
    molecule-resampling bootstrap (:func:`bootstrap_histogram_ci`, [König2013]).

    Parameters
    ----------
    project, molecule_keys, intensity_quantity, bins, value_range, density,
    per_molecule_equal_weight, include_rejected
        As for :func:`population_apparent_e_histogram`.
    n_resamples, ci_level, seed
        Bootstrap parameters (see :func:`bootstrap_histogram_ci`).

    Returns
    -------
    HistogramBootstrapCI
        Whose wrapped ``histogram`` equals
        :func:`population_apparent_e_histogram` for the same arguments (with
        ``n_molecules`` set), so the point estimate and its band stay consistent.
    """
    value_chunks, weight_chunks = _per_molecule_apparent_e(
        project,
        molecule_keys,
        intensity_quantity,
        include_rejected,
        per_molecule_equal_weight=per_molecule_equal_weight,
    )
    ci = bootstrap_histogram_ci(
        value_chunks,
        per_molecule_weights=weight_chunks,
        bins=bins,
        value_range=value_range,
        density=density,
        n_resamples=n_resamples,
        ci_level=ci_level,
        seed=seed,
    )
    hist = replace(
        ci.histogram,
        n_molecules=len(value_chunks),
        per_molecule_equal_weight=per_molecule_equal_weight,
    )
    return replace(ci, histogram=hist)
