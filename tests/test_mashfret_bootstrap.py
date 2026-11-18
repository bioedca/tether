# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tether's molecule bootstrap against MASH-FRET's BOBA-FRET bootstrap (#713, ADR-0108).

Tether cites BOBA-FRET [König2013] for the bootstrap of its FRET histogram: resample the molecules
with replacement, rebuild the histogram. MASH-FRET implements the same method in its Histogram
analysis (``gauss_ana.m``), and the committed Path A extract of #712 keeps the result: 100 samples
of 17 molecules drawn from the tag's 17, each molecule's histogram weighted to sum 1, the histogram
of each sample, and the three-Gaussian fit of each sample with the population of each Gaussian. So
the reference is stored in the project file, and no MATLAB run is needed.

A bootstrap is random, so the two cannot give equal numbers. Tether runs its bootstrap
(:func:`~tether.analysis.histogram.bootstrap_histogram_ci`) on the same FRET values with the same
weights and the same number of samples, for ten seeds, and each comparison is a z-score: the
difference from MASH-FRET divided by the seed-to-seed SD that the ten seeds measure, times
``sqrt(1 + 1/10)`` because MASH-FRET's own number is one draw with that spread. Two comparisons:

1. **The SD of each bin** of the normalized histogram over the samples, from Tether's own ``std``
   output, on the bins whose MASH-FRET SD is at least a tenth of the largest.
2. **The mean and the SD of each Gaussian population.** Tether's function returns no samples, so
   each Tether sample is one call with ``n_resamples=1`` (its interval is then that sample), and
   each sample histogram is fitted with MASH-FRET's model: the sum of ``A·exp(-(x-μ)²/2σ²)``, by
   bounded nonlinear least squares from MASH-FRET's stored start and bounds, the population of a
   Gaussian being its share of the summed curve over the bin centres.

``schema/mashfret_bootstrap_limits.json`` holds the limit (``|z| <= 3``) and the measurement. A
bootstrap that resamples frames in place of molecules fails comparison 1 by two orders of
magnitude, which the last test shows.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

import numpy as np
from scipy.optimize import curve_fit

from _mashfret import TagAnalysis, read_tag_analysis
from reference_data.fetch import path_for
from tether.analysis.histogram import apparent_e_histogram, bootstrap_histogram_ci
from tether.fret.efficiency import corrected_fret

LIMITS = Path(__file__).resolve().parents[1] / "schema" / "mashfret_bootstrap_limits.json"
DOC = json.loads(LIMITS.read_text("utf-8"))
SEEDS: list[int] = DOC["seeds"]
Z_LIMIT: float = DOC["z_limit"]
FWHM = 2 * np.sqrt(2 * np.log(2))


@cache
def path_a() -> TagAnalysis:
    return read_tag_analysis(path_for("mashfret-ed135e-30mm-path-a"), 12)


def _inputs(analysis: TagAnalysis) -> tuple[list[np.ndarray], list[np.ndarray], dict]:
    values = [
        corrected_fret(d, a, alpha=0.0, gamma=g)
        for d, a, g in zip(analysis.donor, analysis.acceptor, analysis.gamma, strict=True)
    ]
    # getHist.m with "weighted": each molecule's histogram over all its frames sums to 1.
    weights = [np.full(v.shape, 1.0 / np.count_nonzero(np.isfinite(v))) for v in values]
    half = analysis.bin_width / 2
    bins = {
        "bins": analysis.centres.size,
        "value_range": (analysis.centres[0] - half, analysis.centres[-1] + half),
    }
    return values, weights, bins


def z_scores(tether: np.ndarray, mash: np.ndarray) -> np.ndarray:
    """``(mean over seeds - MASH-FRET) / (seed-to-seed SD * sqrt(1 + 1/seeds))``, axis 0 = seed."""
    spread = tether.std(axis=0, ddof=1) * np.sqrt(1 + 1 / tether.shape[0])
    return (tether.mean(axis=0) - mash) / spread


def bin_sd_z(sds: np.ndarray, mash_hists: np.ndarray) -> np.ndarray:
    """z of each bin's SD; ``sds`` is ``(seeds, bins)``, ``mash_hists`` ``(bins, samples)``."""
    mash_sd = mash_hists.std(axis=1, ddof=1)
    keep = mash_sd >= 0.1 * mash_sd.max()
    return z_scores(sds[:, keep], mash_sd[keep])


def gaussian_populations(x: np.ndarray, hist: np.ndarray, start: np.ndarray) -> np.ndarray:
    """MASH-FRET's ``gauss_ana.m`` model fitted to one sample: the populations, μ ascending."""
    n = start.shape[0]
    lower = np.r_[start[:, 0], start[:, 3], start[:, 6] / FWHM]
    p0 = np.r_[start[:, 1], start[:, 4], start[:, 7] / FWHM]
    upper = np.r_[start[:, 2], start[:, 5], start[:, 8] / FWHM]

    def curves(p: np.ndarray) -> np.ndarray:
        a, mu, sd = p[:n, None], p[n : 2 * n, None], p[2 * n :, None]
        return a * np.exp(-((x - mu) ** 2) / (2 * sd**2))

    p, _ = curve_fit(
        lambda xx, *q: curves(np.asarray(q)).sum(axis=0), x, hist, p0=p0, bounds=(lower, upper)
    )
    area = curves(p).sum(axis=1)
    return (area / area.sum())[np.argsort(p[n : 2 * n])]


@cache
def tether_samples() -> np.ndarray:
    """``(seeds, samples, bins)``: Tether's bootstrap samples, each normalized to sum 1."""
    analysis = path_a()
    values, weights, bins = _inputs(analysis)
    n = analysis.bootstrap.samples.shape[0]
    out = np.empty((len(SEEDS), n, analysis.centres.size))
    for i, seed in enumerate(SEEDS):
        for j in range(n):
            one = bootstrap_histogram_ci(
                values,
                per_molecule_weights=weights,
                density=False,
                n_resamples=1,
                seed=seed * n + j,
                **bins,
            )
            assert np.array_equal(one.ci_low, one.ci_high)  # one sample: the interval is it
            out[i, j] = one.ci_low / one.ci_low.sum()
    return out


@cache
def tether_populations() -> np.ndarray:
    """``(seeds, samples, J)``: the Gaussian populations of each Tether sample."""
    analysis = path_a()
    start = analysis.bootstrap.fit_start
    hists = tether_samples()
    return np.array(
        [[gaussian_populations(analysis.centres, h, start) for h in seed] for seed in hists]
    )


def test_the_extract_stores_mashfret_s_bootstrap() -> None:
    analysis = path_a()
    boot = analysis.bootstrap
    assert boot is not None and boot.enabled and boot.weighted
    # Each sample draws as many molecules as the tag has, as Tether's bootstrap does.
    assert boot.molecules_per_sample == analysis.molecules.size == 17
    assert boot.samples.shape == (DOC["samples"], 12)
    assert boot.histograms.shape == (analysis.centres.size, DOC["samples"])
    assert np.allclose(boot.histograms.sum(axis=0), 1.0)
    # thm_res{2,1} is the mean and SD of the samples.
    assert np.allclose(analysis.gaussians[:, 6], boot.populations.mean(axis=0))
    assert np.allclose(analysis.gaussians[:, 7], boot.populations.std(axis=0, ddof=1))


def test_tether_s_bin_sd_agrees_with_mashfret_s() -> None:
    analysis = path_a()
    values, weights, bins = _inputs(analysis)
    n = DOC["samples"]
    width = analysis.bin_width
    sds = np.array(
        [
            bootstrap_histogram_ci(
                values,
                per_molecule_weights=weights,
                density=True,
                n_resamples=n,
                seed=seed,
                **bins,
            ).std
            * width  # density × width = each bin's share, as MASH-FRET normalizes
            for seed in SEEDS
        ]
    )
    worst = np.abs(bin_sd_z(sds, analysis.bootstrap.histograms)).max()
    print("bin SD, max |z|", worst)
    assert worst <= Z_LIMIT
    assert round(worst, 2) == DOC["measured"]["max_abs_z"]["bin_sd"]  # NumPy's RNG is portable


def test_tether_s_population_mean_and_sd_agree_with_mashfret_s() -> None:
    mash = path_a().bootstrap.populations
    pops = tether_populations()
    z_mean = z_scores(pops.mean(axis=1), mash.mean(axis=0))
    z_sd = z_scores(pops.std(axis=1, ddof=1), mash.std(axis=0, ddof=1))
    print("mean", pops.mean(axis=(0, 1)), "MASH", mash.mean(axis=0), "z", z_mean)
    print("sd", pops.std(axis=1, ddof=1).mean(axis=0), "MASH", mash.std(axis=0, ddof=1), "z", z_sd)
    assert np.abs(z_mean).max() <= Z_LIMIT
    assert np.abs(z_sd).max() <= Z_LIMIT


def test_a_frame_bootstrap_fails_the_comparison() -> None:
    # Teeth: resample the frames of the pooled molecules in place of the molecules.
    analysis = path_a()
    values, weights, bins = _inputs(analysis)
    pooled, pooled_w = np.concatenate(values), np.concatenate(weights)
    n = DOC["samples"]
    hists = np.empty((len(SEEDS), n, analysis.centres.size))
    for i, seed in enumerate(SEEDS):
        rng = np.random.default_rng(seed)
        for j in range(n):
            pick = rng.integers(0, pooled.size, pooled.size)
            counts = apparent_e_histogram(
                pooled[pick], weights=pooled_w[pick], density=False, **bins
            ).counts
            hists[i, j] = counts / counts.sum()
    worst = np.abs(bin_sd_z(hists.std(axis=1, ddof=1), analysis.bootstrap.histograms)).max()
    assert worst > 10 * Z_LIMIT


def test_the_limits_file_records_a_passing_measurement() -> None:
    assert DOC["issue"] == 713 and Z_LIMIT == 3.0 and len(SEEDS) == 10
    for name, value in DOC["measured"]["max_abs_z"].items():
        assert 0.0 <= value <= Z_LIMIT, name


def test_the_z_score_has_teeth() -> None:
    # A Tether mean four seed-to-seed SDs away from MASH-FRET's number fails.
    tether = np.random.default_rng(713).normal(0.0, 1.0, size=(10, 3))
    far = tether.mean(axis=0) - 4 * tether.std(axis=0, ddof=1)
    assert np.all(np.abs(z_scores(tether, far)) > Z_LIMIT)
