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
- :func:`per_condition_apparent_e_histograms` — overlay each condition's
  histogram on one shared axis (the M6 FR-ANALYZE §7.7 per-condition-overlay
  view), each density-normalized for cross-condition shape comparison
  [McCann2010], annotated with its molecule count ``N``.

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
    from collections.abc import Iterable, Mapping

    from tether.analysis._store import ProjectRef

__all__ = [
    "DEFAULT_BOOTSTRAP_RESAMPLES",
    "DEFAULT_CI_LEVEL",
    "DEFAULT_NBINS",
    "DEFAULT_RANGE",
    "DEFAULT_SEED",
    "ConditionHistogram",
    "Histogram1D",
    "HistogramBootstrapCI",
    "PerConditionHistograms",
    "apparent_e_histogram",
    "bootstrap_histogram_ci",
    "per_condition_apparent_e_histograms",
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

    # Bin each molecule once on the fixed grid (raw/weighted counts), then resample
    # by summing the drawn rows. Because histograms are additive over disjoint frame
    # groups, this is numerically identical to re-binning each replicate's pooled
    # frames — but O(n_resamples · n_mol · nbins) on the hot path instead of
    # O(n_resamples · total_frames), which matters at the default 1000 resamples on
    # large populations (CodeRabbit review, PR #79). Density is normalized per
    # replicate *after* summing, exactly as ``apparent_e_histogram`` does.
    widths = np.diff(point.bin_edges)
    mol_counts = np.empty((n_mol, nbins), dtype=np.float64)
    for i in range(n_mol):
        rep = apparent_e_histogram(
            per_molecule_values[i],
            bins=bins,
            value_range=value_range,
            density=False,
            weights=per_molecule_weights[i] if has_weights else None,
        )
        mol_counts[i] = rep.counts

    rng = np.random.default_rng(seed)
    boot = np.empty((n_resamples, nbins), dtype=np.float64)
    for b in range(n_resamples):
        idx = rng.integers(0, n_mol, size=n_mol)  # high exclusive → indices in [0, n_mol)
        counts_b = mol_counts[idx].sum(axis=0)
        if density:
            total = float(counts_b.sum())
            if total > 0.0:
                counts_b = counts_b / widths / total
        boot[b] = counts_b

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


@dataclass(frozen=True)
class ConditionHistogram:
    """One condition's apparent-E histogram within a per-condition overlay (§7.7).

    Pairs the ``condition_id`` with its :class:`Histogram1D` so a renderer can
    label each overlaid curve and annotate it with the molecule count ``N`` (the
    §7.7 "N annotation"). The histogram carries its own binning parameters and
    ``n_molecules``; this wrapper only adds the condition identity.
    """

    condition_id: str
    histogram: Histogram1D

    @property
    def n_molecules(self) -> int:
        """Molecules of this condition that contributed ≥1 finite frame (the ``N``)."""
        return int(self.histogram.n_molecules or 0)

    @property
    def n_samples(self) -> int:
        """Finite per-frame apparent-E values pooled for this condition."""
        return self.histogram.n_samples


@dataclass(frozen=True)
class PerConditionHistograms:
    """Several conditions' apparent-E histograms sharing one axis (§7.7 overlay).

    The FR-ANALYZE §7.7 "per-condition overlays" view: each condition is binned on
    the **same** grid (identical ``bins`` + ``value_range``) so the curves overlay
    directly on one axis, density-normalized per condition by default so the
    population *shapes* compare regardless of how many molecules each condition has
    — the standard way FRET-efficiency populations are compared across conditions
    [McCann2010]. Only conditions with at least one queried molecule are present;
    the order is the requested ``condition_ids`` order when given, else the store's
    first-seen order — either way deterministic (NFR-REPRO).

    Every parameter that produced the overlay travels with it (``bin_edges``,
    ``value_range``, ``density``, weighting, ``intensity_quantity``) so the view is
    reproducible without re-deriving it.
    """

    histograms: tuple[ConditionHistogram, ...]
    bin_edges: np.ndarray  # (nbins + 1,) float64 — the grid shared by every condition
    value_range: tuple[float, float]
    density: bool
    per_molecule_equal_weight: bool
    intensity_quantity: str

    @property
    def n_conditions(self) -> int:
        """How many conditions are overlaid."""
        return len(self.histograms)

    @property
    def condition_ids(self) -> tuple[str, ...]:
        """The overlaid conditions, in overlay (legend) order."""
        return tuple(ch.condition_id for ch in self.histograms)

    @property
    def bin_centers(self) -> np.ndarray:
        """Bin-center abscissa shared by every overlaid curve."""
        e = self.bin_edges
        return 0.5 * (e[:-1] + e[1:])

    @property
    def nbins(self) -> int:
        """Number of bins on the shared grid."""
        return int(self.bin_edges.shape[0] - 1)

    @property
    def molecule_counts(self) -> dict[str, int]:
        """``condition_id`` → its molecule count ``N`` (the §7.7 per-curve annotation)."""
        return {ch.condition_id: ch.n_molecules for ch in self.histograms}

    @property
    def total_molecules(self) -> int:
        """Molecules contributing across all overlaid conditions."""
        return sum(ch.n_molecules for ch in self.histograms)

    def __getitem__(self, condition_id: str) -> ConditionHistogram:
        """The :class:`ConditionHistogram` for ``condition_id`` (``KeyError`` if absent)."""
        for ch in self.histograms:
            if ch.condition_id == condition_id:
                return ch
        raise KeyError(condition_id)


def per_condition_apparent_e_histograms(
    project: ProjectRef,
    *,
    condition_ids: Iterable[str] | None = None,
    key: Mapping[str, object] | None = None,
    categories: Iterable[str] | None = None,
    tags: Iterable[str] | None = None,
    match_all_tags: bool = True,
    intensity_quantity: str = "corrected",
    bins: int = DEFAULT_NBINS,
    value_range: tuple[float, float] = DEFAULT_RANGE,
    density: bool = True,
    per_molecule_equal_weight: bool = False,
    include_rejected: bool = False,
) -> PerConditionHistograms:
    """Overlay each condition's apparent-E histogram on one shared axis (§7.7).

    The FR-ANALYZE §7.7 per-condition-overlay view (M6-owned: it only becomes
    meaningful once the M4 condition model exists). Selects the conditioned
    population with :func:`~tether.analysis.query.query_molecules` (the same ANDed
    key/category/tag filters), groups the matches by condition, and bins each
    condition's **accepted** molecules with :func:`population_apparent_e_histogram`
    on a **single shared grid** so the curves overlay directly. Each condition is
    density-normalized independently by default, the standard way FRET-efficiency
    populations are compared across conditions [McCann2010].

    Selection is a two-stage AND: the query picks the *conditioned* molecules
    matching the filters, then the histogram applies the §7.5 curation filter
    (``include_rejected=False`` by default) — so each condition's curve pools its
    **queried ∩ accepted** molecules, and a condition whose molecules are all
    rejected still appears with ``N = 0`` rather than silently vanishing.

    Parameters
    ----------
    project
        A :class:`~tether.project.core.Project` or a path to a ``.tether`` store.
    condition_ids
        Restrict to (and order the overlay by) these conditions; ``None``/empty
        overlays every condition in store order. Conditions requested but absent
        from the store contribute nothing (they are simply not overlaid).
    key, categories, tags, match_all_tags
        Forwarded verbatim to :func:`~tether.analysis.query.query_molecules` to
        narrow which molecules of each condition are pooled (e.g. overlay only the
        ``docked`` category, or a ligand-only ``key``). ``key`` needs materialized
        ``/conditions`` rows (see that function).
    intensity_quantity, bins, value_range, density, per_molecule_equal_weight,
    include_rejected
        Binning / pooling parameters, applied identically to every condition (see
        :func:`population_apparent_e_histogram`). ``bins`` + ``value_range`` are
        shared so the histograms overlay on one axis.

    Returns
    -------
    PerConditionHistograms
        One :class:`ConditionHistogram` per condition with ≥1 queried molecule, on
        the shared grid, in ``condition_ids`` order when given else store order.
        Empty (no ``histograms``) when nothing matches — the shared ``bin_edges``
        are present regardless.
    """
    from tether.analysis.query import query_molecules  # noqa: PLC0415
    from tether.project.core import Project as _Project  # noqa: PLC0415

    # Materialize a possibly one-shot ``condition_ids`` iterable: ``query_molecules``
    # consumes it for filtering AND we need it again for the overlay order. Leave a
    # bare str/bytes untouched so ``query_molecules`` raises its informative TypeError.
    # An empty selection is *inert* (``requested = None`` -> store order), matching
    # ``query_molecules`` where an empty filter overlays every condition.
    requested: list[str] | None = None
    if condition_ids is not None and not isinstance(condition_ids, str | bytes):
        materialized = [str(c) for c in condition_ids]
        condition_ids = materialized
        requested = materialized or None

    proj = project if isinstance(project, _Project) else _Project.open(project)

    result = query_molecules(
        proj,
        condition_ids=condition_ids,
        key=key,
        categories=categories,
        tags=tags,
        match_all_tags=match_all_tags,
    )
    grouped = result.by_condition()

    # Overlay order: the caller's requested order (restricted to conditions actually
    # present, de-duplicated) when given, else the query's first-seen store order.
    if requested is None:
        order: list[str] = list(result.condition_ids)
    else:
        seen: set[str] = set()
        order = []
        for cid in requested:
            if cid in grouped and cid not in seen:
                seen.add(cid)
                order.append(cid)

    # ``len(order)`` is the number of overlaid conditions — a handful on one axis by
    # construction — so re-pooling each condition's traces per curve is fine.
    histograms = tuple(
        ConditionHistogram(
            condition_id=cid,
            histogram=population_apparent_e_histogram(
                proj,
                molecule_keys=list(grouped[cid]),
                intensity_quantity=intensity_quantity,
                bins=bins,
                value_range=value_range,
                density=density,
                per_molecule_equal_weight=per_molecule_equal_weight,
                include_rejected=include_rejected,
            ),
        )
        for cid in order
    )

    # The shared grid — identical for every condition (fixed bins + range), and
    # present even when nothing matched (the canonical empty-sample edges).
    bin_edges = apparent_e_histogram(
        np.empty(0, dtype=np.float64), bins=bins, value_range=value_range, density=density
    ).bin_edges

    lo, hi = float(value_range[0]), float(value_range[1])
    return PerConditionHistograms(
        histograms=histograms,
        bin_edges=bin_edges,
        value_range=(lo, hi),
        density=density,
        per_molecule_equal_weight=per_molecule_equal_weight,
        intensity_quantity=intensity_quantity,
    )
