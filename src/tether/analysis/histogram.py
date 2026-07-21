# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Population apparent-E histograms — 1-D (A1) and 2-D time-vs-signal (A2).

PRD §7.7 FR-ANALYZE; Appendix C plots A1 and A2.

The population FRET-efficiency histogram — tMAVEN's A1 plot
(``tmaven/tmaven/controllers/analysis_plots/data_hist1d.py``): pool the per-frame
apparent-E over each selected molecule's analysis window and bin it. Defaults
mirror tMAVEN's A1 — ``signal_nbins = 151`` bins over ``[-0.25, 1.25]``,
density-normalized — so the *uncorrected* proximity ratio's excursions beyond
``[0, 1]`` on noisy frames stay visible rather than clipped away [McCann2010]
(``tether.fret.apparent_fret`` deliberately does not clip). Non-finite samples
(``apparent_fret`` yields NaN where ``D + A == 0``) are dropped before binning —
never fabricated.

The A2 view (``data_hist2d.py``) keeps the *time* axis the A1 pool collapses: it
bins per-frame apparent-E into a 2-D ``(time, signal)`` occupancy heatmap so the
population's FRET evolution over the analysis window is visible, a standard lens on
time-resolved single-molecule dynamics [Nettels2024]. This module lands both A2 modes:
the **raw / start-synchronized** mode aligns each molecule to its own
analysis-window start (``windowed_channels`` already slices there), faithful to
tMAVEN's ``histogram_raw`` after ``sync_start``; the **post-synchronized**
(transition-aligned) mode reads the persisted ``/idealization`` per-molecule state
paths and aligns every selected state jump to a common column so asynchronous
transitions add coherently and the population's average approach-to and
departure-from the transition become visible (tMAVEN's ``histogram_sync_list``, a
transition-synchronized ensemble average [Blackwell2020]). A2's signal defaults
mirror tMAVEN's ``data_hist2d.py`` (61 bins over ``[-0.2, 1.2]``), which differ
from A1's — exactly as in tMAVEN itself; pass A1's ``value_range`` explicitly for a
shared E axis across the two plots.

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
- :func:`model_gaussian_overlay` — the idealized state model drawn on the A1
  axis as per-state Gaussians (tMAVEN's ``data_hist1d.py`` ``model_on`` overlay):
  each state ``i`` contributes ``frac[i]·𝒩(mean[i], var[i])`` and the sum is the
  mixture density the histogram is compared against. The multi-state FRET
  histogram *is* such a sum of per-state Gaussians [Gopich2010] — so the overlay
  is the idealized model's own state emissions, **not a fresh GMM re-fit** of the
  pooled E (the faithful tMAVEN behavior).
- :func:`population_model_gaussian_overlay` — read a persisted ``/idealization``
  population model from a ``.tether`` store and build that overlay from its state
  levels, variances and populations.
- :func:`time_signal_histogram2d` — the pure-array A2 core: bin a list of
  per-molecule time-ordered signal traces into a 2-D ``(time, signal)`` occupancy
  heatmap (start-synchronized; NaN and out-of-range frames dropped).
- :func:`population_time_signal_histogram2d` — read a ``.tether`` store, window
  each accepted molecule's apparent-E, and build the A2 raw heatmap.
- :func:`transition_sync_histogram2d` — the pure-array A2 **post-synchronized**
  core: align per-molecule ``(state_path, signal)`` pairs on their selected state
  transitions and bin the observed signal into a transition-relative
  ``(time, signal)`` heatmap (tMAVEN ``gen_sync_list_*`` + ``histogram_sync_list``).
- :func:`population_transition_sync_histogram2d` — read a persisted
  ``/idealization`` model's state paths from a ``.tether`` store, pair them with
  each molecule's windowed apparent-E, and build the A2 post-synchronized heatmap.

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
[Gopich2010] Gopich & Szabo. "FRET efficiency distributions of multistate single
    molecules." J. Phys. Chem. B 114(46):15221-15226 (2010). The multi-state FRET
    efficiency histogram is a sum of Gaussians whose parameters are set by the
    states' FRET efficiencies — the per-state model overlay Tether draws on A1.
[Verma2024] Verma, Kinz-Thompson et al. "Increasing the accuracy of single-molecule
    data analysis using tMAVEN." Biophysical Journal (2024). tMAVEN, the source of
    the A1/A2 plot definitions (``data_hist1d.py`` / ``data_hist2d.py``).
[Nettels2024] Nettels, Schuler et al. "Single-molecule FRET for probing nanoscale
    biomolecular dynamics." Nature Reviews Physics (2024). Time-resolved population
    FRET is a standard lens on single-molecule conformational dynamics — the 2-D
    time-vs-signal occupancy heatmap the A2 view renders.
[Blackwell2020] Blackwell, Nariya et al. "Computational Tool for Ensemble Averaging
    of Single-Molecule Data." bioRxiv (2020); Biophysical Journal. Aligning
    individual single-molecule trajectories to a common transition point and
    ensemble-averaging is the established way to recover the average signal
    evolution through a transition that per-trace stochasticity otherwise obscures —
    the transition-synchronized ("post-synchronized") heatmap the A2 view renders.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

import numpy as np

from tether.analysis._store import windowed_channels, windowed_state_and_channels
from tether.fret.efficiency import apparent_fret

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Iterable, Mapping

    from tether.analysis._store import ProjectRef

__all__ = [
    "DEFAULT_BOOTSTRAP_RESAMPLES",
    "DEFAULT_CI_LEVEL",
    "DEFAULT_NBINS",
    "DEFAULT_OVERLAY_POINTS",
    "DEFAULT_RANGE",
    "DEFAULT_SEED",
    "DEFAULT_SIGNAL_BINS",
    "DEFAULT_SIGNAL_RANGE",
    "DEFAULT_SYNC_PREFRAME",
    "DEFAULT_TIME_BINS",
    "DEFAULT_TIME_DT",
    "ConditionHistogram",
    "Histogram1D",
    "Histogram2D",
    "HistogramBootstrapCI",
    "ModelGaussianOverlay",
    "PerConditionHistograms",
    "TransitionSyncHistogram2D",
    "apparent_e_histogram",
    "bootstrap_histogram_ci",
    "model_gaussian_overlay",
    "per_condition_apparent_e_histograms",
    "population_apparent_e_histogram",
    "population_apparent_e_histogram_ci",
    "population_model_gaussian_overlay",
    "population_time_signal_histogram2d",
    "population_transition_sync_histogram2d",
    "time_signal_histogram2d",
    "transition_sync_histogram2d",
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

#: tMAVEN A1 model-overlay grid resolution: ``data_hist1d.py`` evaluates the state
#: Gaussians on ``np.linspace(signal_min, signal_max, 1001)``. A fixed rendering
#: fidelity like :data:`DEFAULT_NBINS` (not a science tunable), exposed as the
#: ``n_points`` keyword with this faithful default.
DEFAULT_OVERLAY_POINTS = 1001

#: tMAVEN A2 2-D-histogram defaults (``data_hist2d.py``): the time axis holds
#: ``time_nbins = 100`` per-frame columns, the signal axis ``signal_nbins = 61``
#: bins over ``[signal_min, signal_max] = [-0.2, 1.2]``, one frame = one column of
#: width ``time_dt = 1``. These intentionally differ from the A1 defaults
#: (:data:`DEFAULT_NBINS` / :data:`DEFAULT_RANGE`) — exactly as in tMAVEN, whose A1
#: and A2 carry distinct defaults. Pass A1's :data:`DEFAULT_RANGE` for a shared E
#: axis across the two plots. These are faithful rendering defaults, not §11.2
#: science tunables.
DEFAULT_TIME_BINS = 100
DEFAULT_SIGNAL_BINS = 61
DEFAULT_SIGNAL_RANGE: tuple[float, float] = (-0.2, 1.2)
DEFAULT_TIME_DT = 1.0

#: tMAVEN A2 **post-synchronized** default (``data_hist2d.py`` ``sync_preframe``):
#: the number of time columns before the transition column, so the selected state
#: jump sits at column ``sync_preframe`` (relative-time zero) with ``sync_preframe``
#: columns of the approach on its left and ``time_bins - sync_preframe`` of the
#: departure on its right. With the tMAVEN default 50 over 100 time bins the
#: transition is centred. A faithful rendering default, not a §11.2 science tunable.
DEFAULT_SYNC_PREFRAME = 50


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
        """Bin-center abscissa in apparent-E."""
        e = self.bin_edges
        return 0.5 * (e[:-1] + e[1:])

    @property
    def nbins(self) -> int:
        """Number of bins in the histogram."""
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
        """Per-bin density or counts of the point estimate (from the wrapped histogram)."""
        return self.histogram.counts

    @property
    def bin_edges(self) -> np.ndarray:
        """Apparent-E bin edges of the point estimate (from the wrapped histogram)."""
        return self.histogram.bin_edges

    @property
    def bin_centers(self) -> np.ndarray:
        """Bin-center abscissa in apparent-E (from the wrapped point-estimate histogram)."""
        return self.histogram.bin_centers

    @property
    def nbins(self) -> int:
        """Number of bins shared by the point estimate and the band."""
        return self.histogram.nbins

    @property
    def value_range(self) -> tuple[float, float]:
        """The ``(low, high)`` apparent-E binning range (from the wrapped histogram)."""
        return self.histogram.value_range

    @property
    def n_molecules(self) -> int | None:
        """Molecules pooled into the point estimate (``None`` for the pure core)."""
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


# --- model-Gaussian overlay (A1 ``model_on``) --------------------------------


@dataclass(frozen=True)
class ModelGaussianOverlay:
    """The idealized state model drawn on the A1 histogram as per-state Gaussians.

    tMAVEN's A1 ``model_on`` overlay (``data_hist1d.py``): each state ``i`` of the
    idealized population model contributes a Gaussian
    ``frac[i]·𝒩(x; means[i], variances[i])`` to the density, and the sum over states
    (``total``) is the mixture curve the histogram is compared against — the
    multi-state FRET efficiency histogram *is* such a sum of per-state Gaussians
    [Gopich2010]. These are the idealized model's own state emissions, **not a fresh
    GMM re-fit** of the pooled apparent-E — the faithful tMAVEN behavior.

    Evaluated on the dense grid ``x`` spanning the histogram's ``value_range`` so it
    overlays a density-normalized :class:`Histogram1D` directly: with ``frac``
    summing to 1 and each Gaussian integrating to 1, ``total`` integrates to ~1 over
    the reals (matching ``density=True``). No renormalization to the finite
    ``value_range`` is applied — exactly as tMAVEN plots it — so a state whose mass
    spills past an edge leaves ``total`` slightly under 1 there (honest, not
    rescaled). The overlay is meaningful when the model was idealized on the same
    FRET-efficiency signal the histogram bins (they share the E axis). Every
    parameter that produced the curve travels with it (NFR-REPRO).
    """

    x: np.ndarray  # (n_points,) abscissa on the FRET-efficiency axis
    components: np.ndarray  # (nstates, n_points) per-state ``frac·Normal`` density
    total: np.ndarray  # (n_points,) mixture density = ``components.sum(axis=0)``
    means: np.ndarray  # (nstates,) state emission means (the E levels)
    variances: np.ndarray  # (nstates,) state emission variances (σ² > 0)
    frac: np.ndarray  # (nstates,) normalized state populations (weights; sum ≈ 1)
    value_range: tuple[float, float]
    model_name: str | None  # source model name (store path); None for the pure core

    @property
    def nstates(self) -> int:
        """Number of Gaussian components (states) in the overlay."""
        return int(self.means.shape[0])

    @property
    def n_points(self) -> int:
        """Number of abscissa samples the curves are evaluated on."""
        return int(self.x.shape[0])


def model_gaussian_overlay(
    means: np.ndarray,
    variances: np.ndarray,
    frac: np.ndarray,
    *,
    value_range: tuple[float, float] = DEFAULT_RANGE,
    n_points: int = DEFAULT_OVERLAY_POINTS,
    model_name: str | None = None,
) -> ModelGaussianOverlay:
    """Build the per-state-Gaussian model overlay for the A1 histogram (pure core).

    Reproduces tMAVEN's ``data_hist1d.py`` ``model_on`` branch: for each state ``i``
    evaluate ``frac[i]·𝒩(x; means[i], variances[i])`` on
    ``x = linspace(*value_range, n_points)`` and sum over states. The result overlays
    a density-normalized :class:`Histogram1D` on the same ``value_range`` (see
    :class:`ModelGaussianOverlay`). These are the idealized model's own state
    emissions — never a fresh GMM fit of the pooled sample [Gopich2010].

    Parameters
    ----------
    means, variances, frac
        The idealized model's per-state emission means, emission variances
        (σ² > 0), and normalized populations (each state's weight). Same length
        ``nstates ≥ 1``. These are ``StateModel.means`` / ``.variances`` / ``.frac``
        (Appendix D.2).
    value_range
        ``(low, high)`` abscissa span (default :data:`DEFAULT_RANGE`, matching the A1
        histogram so the curves overlay). ``high > low`` required.
    n_points
        Grid resolution (default :data:`DEFAULT_OVERLAY_POINTS` = 1001, tMAVEN's).
        Must be ≥ 2.
    model_name
        Optional source-model tag carried onto the result (set by the store-level
        entry point).

    Returns
    -------
    ModelGaussianOverlay

    Raises
    ------
    ValueError
        If the three arrays differ in length or are empty; if any mean is not
        finite; if any variance is not finite and positive (a zero/negative/NaN
        variance is a degenerate state — withheld, never drawn as an infinite
        spike); if any ``frac`` is not finite and non-negative; or if
        ``value_range`` / ``n_points`` are degenerate.
    """
    means = np.asarray(means, dtype=np.float64).ravel()
    variances = np.asarray(variances, dtype=np.float64).ravel()
    frac = np.asarray(frac, dtype=np.float64).ravel()
    if not means.shape == variances.shape == frac.shape:
        raise ValueError(
            f"means {means.shape}, variances {variances.shape} and frac {frac.shape} "
            "must be 1-D arrays of the same length"
        )
    if means.shape[0] < 1:
        raise ValueError("means/variances/frac must describe at least one state")
    if not np.all(np.isfinite(means)):
        # A NaN/Inf mean would poison ``x - mean[i]`` and silently corrupt the whole
        # mixture — withheld with the same honesty guard as variance/frac, not drawn.
        raise ValueError(f"every state mean must be finite, got {means!r}")
    if not np.all(np.isfinite(variances) & (variances > 0.0)):
        raise ValueError(
            "every state variance must be finite and > 0 to draw its Gaussian, got "
            f"{variances!r} (a zero/negative/NaN variance is a degenerate state — "
            "withheld rather than drawn as an infinite spike)"
        )
    if not np.all(np.isfinite(frac) & (frac >= 0.0)):
        raise ValueError(f"every state population 'frac' must be finite and >= 0, got {frac!r}")

    lo, hi = float(value_range[0]), float(value_range[1])
    if not hi > lo:
        raise ValueError(f"value_range must be (low, high) with high > low, got {value_range!r}")
    if int(n_points) < 2:
        raise ValueError(f"n_points must be >= 2 to span the range, got {n_points!r}")

    x = np.linspace(lo, hi, int(n_points))
    # frac[i] / sqrt(2*pi*var[i]) * exp(-0.5 * (x - mean[i])**2 / var[i]), vectorized
    # over states -> (nstates, n_points). Faithful to tMAVEN ``data_hist1d.py``.
    z = x[None, :] - means[:, None]
    norm = frac / np.sqrt(2.0 * np.pi * variances)
    components = norm[:, None] * np.exp(-0.5 * z * z / variances[:, None])
    total = components.sum(axis=0)
    return ModelGaussianOverlay(
        x=x,
        components=components,
        total=total,
        means=means,
        variances=variances,
        frac=frac,
        value_range=(lo, hi),
        model_name=model_name,
    )


def population_model_gaussian_overlay(
    project: ProjectRef,
    model_name: str,
    *,
    value_range: tuple[float, float] = DEFAULT_RANGE,
    n_points: int = DEFAULT_OVERLAY_POINTS,
) -> ModelGaussianOverlay:
    """Model-Gaussian overlay for a persisted ``/idealization`` model (§10 A1).

    Reads the ``model_name`` population model from a ``.tether`` store
    (:meth:`~tether.project.core.Project.read_idealization`) and builds the tMAVEN A1
    overlay from its state levels, variances and populations
    (:func:`model_gaussian_overlay`). The overlay is meaningful when the model was
    idealized on the same FRET-efficiency signal the A1 histogram bins — they share
    the E axis — so pass the histogram's ``value_range`` to align them.

    Parameters
    ----------
    project
        A :class:`~tether.project.core.Project` or a path to a ``.tether`` store.
    model_name
        Which ``/idealization/{model_name}`` to overlay (see
        :meth:`~tether.project.core.Project.list_idealizations`).
    value_range, n_points
        Forwarded to :func:`model_gaussian_overlay` (defaults match the A1
        histogram).

    Returns
    -------
    ModelGaussianOverlay
        With ``model_name`` set to the source model.

    Raises
    ------
    KeyError
        If no ``/idealization/{model_name}`` exists in the store.
    ValueError
        If the model carries no per-state means / variances / populations — a
        threshold or k-means model (or a legacy model written before the
        population members landed) has no Gaussian emissions to overlay. The
        overlay needs a population model (vbFRET / vbconhmm / ebFRET); the missing
        spread is **withheld, never fabricated**.
    """
    from tether.project.core import Project as _Project  # noqa: PLC0415

    proj = project if isinstance(project, _Project) else _Project.open(project)
    stored = proj.read_idealization(model_name)
    missing = [
        name
        for name, val in (
            ("means", stored.means),
            ("variances", stored.variances),
            ("frac", stored.frac),
        )
        if val is None
    ]
    if missing:
        raise ValueError(
            f"model {model_name!r} (type {stored.model_type!r}) has no per-state "
            f"{' / '.join(missing)}; the A1 model-Gaussian overlay needs a population "
            "model (vbFRET / vbconhmm / ebFRET), not a threshold/k-means model — "
            "the missing spread is withheld rather than fabricated"
        )
    return model_gaussian_overlay(
        stored.means,
        stored.variances,
        stored.frac,
        value_range=value_range,
        n_points=n_points,
        model_name=model_name,
    )


@dataclass(frozen=True)
class Histogram2D:
    """A binned 2-D ``(time, signal)`` occupancy heatmap (self-describing, NFR-REPRO).

    tMAVEN's A2 plot (``data_hist2d.py``): row ``i`` is the ``i``-th time column (one
    frame, ``time_dt`` seconds wide) and column ``j`` the ``j``-th signal bin, so
    ``counts[i, j]`` is how much of the pooled population sat in signal bin ``j`` at
    frame ``i``. ``counts`` is the raw (or per-molecule-weighted) occupancy unless
    ``density`` — then a 2-D probability density integrating to 1 over the plotted
    area (numpy ``histogram2d`` semantics). Smoothing, colormap floors and
    max-normalization are display concerns the view applies, not stored here. Every
    parameter that produced it travels with the arrays so the heatmap reproduces
    without re-deriving it.
    """

    counts: np.ndarray  # (time_bins, signal_bins) float64 — [time, signal] occupancy
    time_edges: np.ndarray  # (time_bins + 1,) float64 — time-column edges
    signal_edges: np.ndarray  # (signal_bins + 1,) float64 — signal-bin edges
    time_dt: float  # frame duration; time_edges = arange(time_bins + 1) * time_dt
    signal_range: tuple[float, float]
    density: bool
    n_samples: int  # finite in-range (frame, signal) points actually binned
    n_molecules: int  # molecules contributing >= 1 finite frame (tMAVEN's N)
    per_molecule_equal_weight: bool

    @property
    def time_centers(self) -> np.ndarray:
        """Time-column centres in seconds (``time_dt`` per frame) after the window start."""
        e = self.time_edges
        return 0.5 * (e[:-1] + e[1:])

    @property
    def signal_centers(self) -> np.ndarray:
        """Signal-bin centre abscissa on the binned signal axis (apparent-E for A2)."""
        e = self.signal_edges
        return 0.5 * (e[:-1] + e[1:])

    @property
    def time_bins(self) -> int:
        """Number of time columns (rows of ``counts``)."""
        return int(self.counts.shape[0])

    @property
    def signal_bins(self) -> int:
        """Number of signal bins (columns of ``counts``)."""
        return int(self.counts.shape[1])


def time_signal_histogram2d(
    signal_chunks: Iterable[np.ndarray],
    *,
    time_bins: int = DEFAULT_TIME_BINS,
    signal_bins: int = DEFAULT_SIGNAL_BINS,
    signal_range: tuple[float, float] = DEFAULT_SIGNAL_RANGE,
    time_dt: float = DEFAULT_TIME_DT,
    density: bool = False,
    per_molecule_equal_weight: bool = False,
) -> Histogram2D:
    """Bin per-molecule time-ordered signals into a 2-D (time, signal) heatmap.

    The pure-array A2 core, faithful to tMAVEN's ``histogram_raw`` applied after
    ``sync_start`` (``data_hist2d.py``): each ``signal_chunks[i]`` is one molecule's
    per-frame signal over its analysis window, already **start-synchronized** (frame
    0 = the window start — :func:`~tether.analysis._store.windowed_channels` slices
    there). Frame ``t`` of a molecule contributes its value to time column ``t`` and
    to the signal bin holding that value; a frame is **dropped** — without shifting
    the time index of later frames, so gaps stay in place — when it is non-finite,
    when its value falls outside ``[signal_range[0], signal_range[1])``, or when
    ``t >= time_bins``. This reproduces tMAVEN's ``d >= ymin and d < ymax`` and
    ``for x in range(time_nbins)`` masking exactly (the signal interval is
    left-closed / right-open, like tMAVEN — unlike numpy's default closed top edge).

    Parameters
    ----------
    signal_chunks
        Iterable of per-molecule 1-D signal arrays (e.g. windowed apparent E),
        each ordered by frame from its analysis-window start. Consumed once.
    time_bins
        Number of per-frame time columns (tMAVEN ``time_nbins``); frames at index
        ``>= time_bins`` are dropped. Must be >= 1.
    signal_bins
        Number of signal bins over ``signal_range`` (tMAVEN ``signal_nbins``).
        Must be >= 1.
    signal_range
        ``(low, high)`` signal axis span; values outside ``[low, high)`` are dropped.
        ``high > low`` required.
    time_dt
        Frame duration used for the time-axis edges (tMAVEN ``time_dt``); the time
        column index is unaffected, only the edge coordinates scale. Must be finite
        and > 0.
    density
        If ``True``, normalize to a 2-D probability density (numpy ``histogram2d``
        ``density``); else raw (or per-molecule-weighted) occupancy counts. Defaults
        to ``False`` — the honest occupancy tMAVEN max-normalizes only for display
        (unlike the 1-D A1 histogram, which is density-normalized by default).
    per_molecule_equal_weight
        If ``True``, every molecule's finite frames are weighted ``1/m`` (``m`` its
        finite-frame count) so a long trace does not dominate a short one (§7.7);
        else every binned frame counts once (tMAVEN A2 behavior). A molecule with
        frames outside the plotted window contributes less than its full weight 1,
        exactly as the 1-D A1 histogram treats out-of-range frames.

    Returns
    -------
    Histogram2D
        With ``n_molecules`` = molecules that had >= 1 finite frame (tMAVEN's ``N``,
        counted before the range/time mask) and ``n_samples`` = points actually
        binned. Empty or all-masked input yields an all-zero heatmap (never NaN).

    Raises
    ------
    ValueError
        If ``time_bins`` or ``signal_bins`` < 1, if ``signal_range`` is not
        ``(low, high)`` with ``high > low``, or if ``time_dt`` is not finite and > 0.
    """
    n_time = int(time_bins)
    n_signal = int(signal_bins)
    if n_time < 1:
        raise ValueError(f"time_bins must be >= 1, got {time_bins!r}")
    if n_signal < 1:
        raise ValueError(f"signal_bins must be >= 1, got {signal_bins!r}")
    lo, hi = float(signal_range[0]), float(signal_range[1])
    if not hi > lo:
        raise ValueError(f"signal_range must be (low, high) with high > low, got {signal_range!r}")
    dt = float(time_dt)
    if not (np.isfinite(dt) and dt > 0.0):
        raise ValueError(f"time_dt must be finite and > 0, got {time_dt!r}")

    # Build the time edges with the *same* arithmetic path as the per-frame
    # coordinates below (``idx * dt``) so ``idx * dt`` aligns exactly with
    # ``time_edges[idx]``. ``np.linspace(0, n*dt, n+1)`` would differ from ``idx*dt``
    # by 1 ULP for non-integer dt, mis-binning a frame into the neighbouring column.
    time_edges = np.arange(n_time + 1, dtype=np.float64) * dt
    signal_edges = np.linspace(lo, hi, n_signal + 1)

    time_coord_chunks: list[np.ndarray] = []
    signal_coord_chunks: list[np.ndarray] = []
    weight_chunks: list[np.ndarray] = []
    n_molecules = 0
    for chunk in signal_chunks:
        e = np.asarray(chunk, dtype=np.float64).ravel()
        if e.size == 0:
            continue
        finite = np.isfinite(e)
        m = int(finite.sum())
        if m == 0:
            continue  # no valid frame -> contributes nothing (not a molecule with data)
        n_molecules += 1  # counted like tMAVEN's N: >= 1 finite frame, before masking
        idx = np.nonzero(finite)[0]  # finite frame indices (positions preserved)
        vals = e[idx]
        # Faithful tMAVEN raw mask: frame within the window and value in [lo, hi).
        keep = (idx < n_time) & (vals >= lo) & (vals < hi)
        if not np.any(keep):
            continue
        time_coord_chunks.append(idx[keep].astype(np.float64) * dt)
        signal_coord_chunks.append(vals[keep])
        if per_molecule_equal_weight:
            # 1/m per surviving frame (m = the molecule's finite count), so out-of-window
            # frames cost the molecule weight, matching the A1 out-of-range convention.
            weight_chunks.append(np.full(int(keep.sum()), 1.0 / m, dtype=np.float64))

    if time_coord_chunks:
        time_coords = np.concatenate(time_coord_chunks)
        signal_coords = np.concatenate(signal_coord_chunks)
        weights = np.concatenate(weight_chunks) if per_molecule_equal_weight else None
        counts, _, _ = np.histogram2d(
            time_coords,
            signal_coords,
            bins=(time_edges, signal_edges),
            density=density,
            weights=weights,
        )
        n_samples = int(signal_coords.shape[0])
    else:
        # No binned points: an honest all-zero heatmap (density on empty would be NaN).
        counts = np.zeros((n_time, n_signal), dtype=np.float64)
        n_samples = 0

    return Histogram2D(
        counts=np.asarray(counts, dtype=np.float64),
        time_edges=time_edges,
        signal_edges=signal_edges,
        time_dt=dt,
        signal_range=(lo, hi),
        density=density,
        n_samples=n_samples,
        n_molecules=n_molecules,
        per_molecule_equal_weight=per_molecule_equal_weight,
    )


def population_time_signal_histogram2d(
    project: ProjectRef,
    *,
    molecule_keys: list[str] | None = None,
    intensity_quantity: str = "corrected",
    time_bins: int = DEFAULT_TIME_BINS,
    signal_bins: int = DEFAULT_SIGNAL_BINS,
    signal_range: tuple[float, float] = DEFAULT_SIGNAL_RANGE,
    time_dt: float = DEFAULT_TIME_DT,
    density: bool = False,
    per_molecule_equal_weight: bool = False,
    include_rejected: bool = False,
) -> Histogram2D:
    """A2 raw time-vs-signal heatmap from a ``.tether`` store (§10 A2; Appendix C A2).

    Reads each accepted molecule's windowed apparent E
    (:func:`~tether.analysis._store.windowed_channels` +
    :func:`~tether.fret.efficiency.apparent_fret`) — already start-synchronized to
    its analysis-window start — and bins it with :func:`time_signal_histogram2d`.
    This is the headless source of truth the GUI A2 heatmap renders. The
    transition-aligned post-synchronized mode (reading the persisted
    ``/idealization`` per-molecule state paths) is a follow-up.

    Parameters
    ----------
    project
        A :class:`~tether.project.core.Project` or a path to a ``.tether`` store.
    molecule_keys
        Restrict to these molecules (``None`` = all); intersected with the curation
        filter.
    intensity_quantity
        Which ``/traces`` layer feeds apparent E: ``"corrected"`` (default) or
        ``"raw"``.
    time_bins, signal_bins, signal_range, time_dt, density, per_molecule_equal_weight
        Binning parameters (see :func:`time_signal_histogram2d`).
    include_rejected
        If ``True``, keep rejected molecules; else exclude them (§7.5 default).

    Returns
    -------
    Histogram2D
    """
    pairs = windowed_channels(project, molecule_keys, intensity_quantity, include_rejected)
    signal_chunks = [apparent_fret(donor, acceptor) for donor, acceptor in pairs]
    return time_signal_histogram2d(
        signal_chunks,
        time_bins=time_bins,
        signal_bins=signal_bins,
        signal_range=signal_range,
        time_dt=time_dt,
        density=density,
        per_molecule_equal_weight=per_molecule_equal_weight,
    )


# --- A2b: transition-aligned (post-synchronized) heatmap ----------------------


@dataclass(frozen=True)
class TransitionSyncHistogram2D:
    """A transition-aligned 2-D ``(time, signal)`` occupancy heatmap (A2 post-sync).

    tMAVEN's A2 **post-synchronized** mode (``data_hist2d.py`` ``gen_sync_list_*`` +
    ``histogram_sync_list``): instead of aligning every molecule to its own start,
    align every *selected state transition* to a common column so the otherwise
    asynchronous stochastic jumps add coherently and the population's average
    approach-to and departure-from the transition become visible — the standard
    transition-synchronized ensemble average of single-molecule trajectories
    [Blackwell2020, Verma2024]. Row ``k`` is the ``k``-th time column and column
    ``j`` the ``j``-th signal bin, so ``counts[k, j]`` is how much signal density
    sat in bin ``j`` at relative time ``time_centers[k]``. The heatmap has
    ``time_bins + 1`` columns (the extra column is tMAVEN's ``xbins + 1``): the
    selected transition sits at column :attr:`sync_preframe` — relative-time zero —
    with the approach on its left and the departure on its right, so
    ``time_edges`` are **relative** to the transition and run negative before it.

    ``counts`` is the raw occupancy unless ``density`` (then a 2-D probability
    density integrating to 1 over the plotted area, numpy ``histogram2d``
    semantics). Smoothing, colormap floors and max-normalization are display
    concerns the view applies, not stored here. ``n_molecules`` (tMAVEN's ``N``)
    and ``n_transitions`` (its ``n``) count the molecules and selected transitions
    that fed the synchronization — computed from the selected jumps, so a
    transition whose window is entirely out of range still counts (faithful to
    tMAVEN), while ``n_samples`` counts only the points actually binned. Every
    parameter that produced the heatmap travels with the arrays (NFR-REPRO).
    """

    counts: np.ndarray  # (time_bins + 1, signal_bins) float64 — [time, signal] occupancy
    time_edges: np.ndarray  # (time_bins + 2,) float64 — relative-time edges (negative before sync)
    signal_edges: np.ndarray  # (signal_bins + 1,) float64 — signal-bin edges
    time_dt: float  # frame duration; column k spans relative time (k - sync_preframe) frames
    sync_preframe: int  # transition column index (relative-time zero)
    signal_range: tuple[float, float]
    from_state: int  # vi: state left at the transition (-1 = any) — tMAVEN sync_hmmstate_1
    to_state: int  # vj: state entered at the transition (-1 = any) — tMAVEN sync_hmmstate_2
    single_dwell: bool  # single-dwell windows (True) vs a fixed +-window per transition
    density: bool
    n_samples: int  # finite in-range (frame, signal) points actually binned
    n_transitions: int  # selected transitions (tMAVEN's npoints)
    n_molecules: int  # molecules with >= 1 selected transition (tMAVEN's N)

    @property
    def time_centers(self) -> np.ndarray:
        """Relative-time column centres; ``time_centers[sync_preframe] == 0``."""
        e = self.time_edges
        return 0.5 * (e[:-1] + e[1:])

    @property
    def signal_centers(self) -> np.ndarray:
        """Signal-bin centre abscissa on the binned signal axis (apparent-E for A2)."""
        e = self.signal_edges
        return 0.5 * (e[:-1] + e[1:])

    @property
    def n_columns(self) -> int:
        """Number of time columns (``time_bins + 1`` — tMAVEN's ``xbins + 1``)."""
        return int(self.counts.shape[0])

    @property
    def signal_bins(self) -> int:
        """Number of signal bins (columns of ``counts``)."""
        return int(self.counts.shape[1])

    @property
    def transition_column(self) -> int:
        """Column index holding the synchronization point (== ``sync_preframe``)."""
        return self.sync_preframe


def transition_sync_histogram2d(
    trace_pairs: Iterable[tuple[np.ndarray, np.ndarray]],
    *,
    from_state: int = -1,
    to_state: int = -1,
    single_dwell: bool = True,
    sync_preframe: int = DEFAULT_SYNC_PREFRAME,
    time_bins: int = DEFAULT_TIME_BINS,
    signal_bins: int = DEFAULT_SIGNAL_BINS,
    signal_range: tuple[float, float] = DEFAULT_SIGNAL_RANGE,
    time_dt: float = DEFAULT_TIME_DT,
    density: bool = False,
    no_state: int = -1,
) -> TransitionSyncHistogram2D:
    """Bin per-molecule (state path, signal) pairs into a transition-aligned heatmap.

    The pure-array A2 **post-synchronized** core, faithful to tMAVEN's
    ``gen_sync_list_single`` / ``gen_sync_list_fixed`` + ``histogram_sync_list``
    (``data_hist2d.py``). For each molecule it finds every state transition (a frame
    ``t`` where ``state[t]`` and ``state[t + 1]`` are both valid — not ``no_state`` —
    and differ), keeps the ones matching the ``(from_state, to_state)`` selection,
    and for each such transition bins the *observed* ``signal`` around it, mapping
    frame ``f`` to time column ``x = (f - t) + sync_preframe`` (so the transition
    lands at column ``sync_preframe``). A frame is dropped — without shifting later
    frames — when its column is outside ``[0, time_bins]``, its value is non-finite,
    or its value is outside ``[signal_range[0], signal_range[1])`` (left-closed /
    right-open, like tMAVEN's ``d >= ymin and d < ymax``).

    Two window shapes match tMAVEN:

    - ``single_dwell=True`` (``gen_sync_list_single``): the window runs from the end
      of the previous dwell (the prior transition + 1, or the trace start) to the
      start of the next dwell (the next transition, or the trace end), so each
      transition contributes the single dwell before and after it.
    - ``single_dwell=False`` (``gen_sync_list_fixed``): a fixed window of
      ``sync_preframe`` frames before and ``time_bins - sync_preframe + 1`` after
      every selected transition (overlapping windows may double-count a frame, as
      in tMAVEN).

    This adapts tMAVEN's rectangular NaN-padded array to Tether's per-molecule
    ragged inputs: each pair is one molecule's ``(state_path, signal)`` (equal
    length), and out-of-range **frame indices are dropped** rather than wrapping —
    the one deliberate departure from tMAVEN, whose fixed window can index a padded
    row with a negative frame; Tether never bins a frame outside a molecule's own
    trace (see :func:`~tether.analysis._store.windowed_state_and_channels`, which
    slices to the idealized window). ``no_state`` is the sentinel for a frame with
    no assigned state (:data:`tether.idealize.NO_STATE` = ``-1``, tMAVEN's NaN
    analogue) — such frames break a state run without forming a transition, so a
    window boundary between ``no_state`` and a real state is never a jump.

    Parameters
    ----------
    trace_pairs
        Iterable of per-molecule ``(state_path, signal)`` pairs. ``state_path`` is
        the integer Viterbi state per frame (``no_state`` where none); ``signal`` is
        the matching observed value (e.g. windowed apparent E) at the same frames.
        Same length within a pair. Consumed once.
    from_state, to_state
        Select transitions leaving ``from_state`` (tMAVEN ``sync_hmmstate_1``) and
        entering ``to_state`` (``sync_hmmstate_2``); ``-1`` matches any state, so
        ``from_state=-1, to_state=-1`` synchronizes on every transition.
    single_dwell
        Window shape (see above); default ``True`` (tMAVEN's default).
    sync_preframe
        Columns before the transition (tMAVEN ``sync_preframe``); the transition
        lands at column ``sync_preframe``. Must be in ``[0, time_bins]``.
    time_bins
        Time columns *excluding* the extra ``+1`` (tMAVEN ``time_nbins``); the
        heatmap has ``time_bins + 1`` columns. Must be >= 1.
    signal_bins, signal_range
        Signal-axis bin count and ``(low, high)`` span (``high > low``).
    time_dt
        Frame duration for the relative-time edge coordinates (tMAVEN ``time_dt``);
        the column index is unaffected. Must be finite and > 0.
    density
        If ``True``, normalize to a 2-D probability density; else raw occupancy
        counts (default — tMAVEN max-normalizes only for display).
    no_state
        State-path sentinel for "no state" (default ``-1`` =
        :data:`tether.idealize.NO_STATE`).

    Returns
    -------
    TransitionSyncHistogram2D
        With ``n_molecules`` / ``n_transitions`` counted from the selected
        transitions and ``n_samples`` from the points binned. No selected
        transition anywhere yields an all-zero heatmap (never NaN).

    Raises
    ------
    ValueError
        If ``time_bins`` or ``signal_bins`` < 1, ``sync_preframe`` is outside
        ``[0, time_bins]``, ``signal_range`` is not ``(low, high)`` with
        ``high > low``, ``time_dt`` is not finite and > 0, or a pair's ``state_path``
        and ``signal`` differ in length.
    """
    n_time = int(time_bins)
    n_signal = int(signal_bins)
    if n_time < 1:
        raise ValueError(f"time_bins must be >= 1, got {time_bins!r}")
    if n_signal < 1:
        raise ValueError(f"signal_bins must be >= 1, got {signal_bins!r}")
    lo, hi = float(signal_range[0]), float(signal_range[1])
    if not hi > lo:
        raise ValueError(f"signal_range must be (low, high) with high > low, got {signal_range!r}")
    dt = float(time_dt)
    if not (np.isfinite(dt) and dt > 0.0):
        raise ValueError(f"time_dt must be finite and > 0, got {time_dt!r}")
    pre = int(sync_preframe)
    if not 0 <= pre <= n_time:
        raise ValueError(
            f"sync_preframe must be in [0, time_bins]=[0, {n_time}], got {sync_preframe!r}"
        )
    vi = int(from_state)
    vj = int(to_state)
    ns = int(no_state)

    # Time axis: n_time + 1 columns (tMAVEN's xbins + 1), column x holding relative
    # time (x - pre) * dt so the transition column x = pre is at t = 0. Edges are the
    # column midpoints -> n_time + 2 edges, negative before the sync column.
    time_edges = (np.arange(n_time + 2, dtype=np.float64) - 0.5 - pre) * dt
    signal_edges = np.linspace(lo, hi, n_signal + 1)

    time_coord_chunks: list[np.ndarray] = []
    signal_coord_chunks: list[np.ndarray] = []
    n_transitions = 0
    n_molecules = 0
    for state_path, signal in trace_pairs:
        s = np.asarray(state_path).ravel()
        e = np.asarray(signal, dtype=np.float64).ravel()
        if s.shape != e.shape:
            raise ValueError(f"state_path {s.shape} and signal {e.shape} must be the same length")
        length = int(s.shape[0])
        if length < 2:
            continue
        s = s.astype(np.int64, copy=False)
        # Transitions: both frames carry a real state and the state changes. A frame
        # bordering no_state is never a jump (tMAVEN's NaN check), so a window edge
        # against the un-idealized region is not spuriously counted.
        valid_pair = (s[:-1] != ns) & (s[1:] != ns)
        changed = s[:-1] != s[1:]
        jump_frames = np.nonzero(valid_pair & changed)[0]  # transition between t and t+1, at t
        if jump_frames.size == 0:
            continue
        sel = np.ones(jump_frames.size, dtype=bool)
        if vi >= 0:
            sel &= s[jump_frames] == vi
        if vj >= 0:
            sel &= s[jump_frames + 1] == vj
        sel_idx = np.nonzero(sel)[0]  # positions in jump_frames of the selected transitions
        if sel_idx.size == 0:
            continue
        n_molecules += 1
        n_transitions += int(sel_idx.size)

        for k in sel_idx:
            sync_t = int(jump_frames[k])
            if single_dwell:
                start = int(jump_frames[k - 1]) + 1 if k >= 1 else 0
                end = int(jump_frames[k + 1]) if k + 1 < jump_frames.size else length - 1
            else:
                start = sync_t - pre
                end = sync_t + (n_time - pre + 1)
            frames = np.arange(start, end)
            x = (frames - sync_t) + pre
            keep = (frames >= 0) & (frames < length) & (x >= 0) & (x <= n_time)
            if not np.any(keep):
                continue
            fk = frames[keep]
            xk = x[keep]
            d = e[fk]
            good = np.isfinite(d) & (d >= lo) & (d < hi)
            if not np.any(good):
                continue
            time_coord_chunks.append((xk[good] - pre).astype(np.float64) * dt)
            signal_coord_chunks.append(d[good])

    if time_coord_chunks:
        time_coords = np.concatenate(time_coord_chunks)
        signal_coords = np.concatenate(signal_coord_chunks)
        counts, _, _ = np.histogram2d(
            time_coords, signal_coords, bins=(time_edges, signal_edges), density=density
        )
        n_samples = int(signal_coords.shape[0])
    else:
        # No binned points: an honest all-zero heatmap (density on empty would be NaN).
        counts = np.zeros((n_time + 1, n_signal), dtype=np.float64)
        n_samples = 0

    return TransitionSyncHistogram2D(
        counts=np.asarray(counts, dtype=np.float64),
        time_edges=time_edges,
        signal_edges=signal_edges,
        time_dt=dt,
        sync_preframe=pre,
        signal_range=(lo, hi),
        from_state=vi,
        to_state=vj,
        single_dwell=bool(single_dwell),
        density=bool(density),
        n_samples=n_samples,
        n_transitions=n_transitions,
        n_molecules=n_molecules,
    )


def population_transition_sync_histogram2d(
    project: ProjectRef,
    model_name: str,
    *,
    from_state: int = -1,
    to_state: int = -1,
    single_dwell: bool = True,
    sync_preframe: int = DEFAULT_SYNC_PREFRAME,
    time_bins: int = DEFAULT_TIME_BINS,
    signal_bins: int = DEFAULT_SIGNAL_BINS,
    signal_range: tuple[float, float] = DEFAULT_SIGNAL_RANGE,
    time_dt: float = DEFAULT_TIME_DT,
    density: bool = False,
    molecule_keys: list[str] | None = None,
    intensity_quantity: str = "corrected",
    include_rejected: bool = False,
) -> TransitionSyncHistogram2D:
    """A2 transition-aligned heatmap from a ``.tether`` store (§10 A2; Appendix C A2).

    Pairs a persisted ``/idealization/{model_name}`` model's per-molecule Viterbi
    state paths with each molecule's observed windowed apparent E
    (:func:`~tether.analysis._store.windowed_state_and_channels` +
    :func:`~tether.fret.efficiency.apparent_fret`) and synchronizes on the selected
    state transitions with :func:`transition_sync_histogram2d`. This is the headless
    source of truth the GUI A2 post-synchronized heatmap renders; the raw /
    start-synchronized mode is :func:`population_time_signal_histogram2d`.

    Parameters
    ----------
    project
        A :class:`~tether.project.core.Project` or a path to a ``.tether`` store.
    model_name
        Which ``/idealization/{model_name}`` supplies the state paths (see
        :meth:`~tether.project.core.Project.list_idealizations`).
    from_state, to_state, single_dwell, sync_preframe, time_bins, signal_bins,
    signal_range, time_dt, density
        Synchronization / binning parameters (see
        :func:`transition_sync_histogram2d`).
    molecule_keys
        Restrict to these molecules (``None`` = all); intersected with the curation
        filter.
    intensity_quantity
        Which ``/traces`` layer feeds apparent E: ``"corrected"`` (default) or
        ``"raw"``.
    include_rejected
        If ``True``, keep rejected molecules; else exclude them (§7.5 default).

    Returns
    -------
    TransitionSyncHistogram2D
    """
    from tether.idealize import NO_STATE  # noqa: PLC0415

    triples = windowed_state_and_channels(
        project, model_name, molecule_keys, intensity_quantity, include_rejected
    )
    trace_pairs = [(state, apparent_fret(donor, acceptor)) for state, donor, acceptor in triples]
    return transition_sync_histogram2d(
        trace_pairs,
        from_state=from_state,
        to_state=to_state,
        single_dwell=single_dwell,
        sync_preframe=sync_preframe,
        time_bins=time_bins,
        signal_bins=signal_bins,
        signal_range=signal_range,
        time_dt=time_dt,
        density=density,
        no_state=NO_STATE,
    )
