# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Donor–acceptor cross-correlation (PRD §7.7 FR-ANALYZE).

A model-free anticorrelation lens: the Pearson-normalized cross-correlation of
the donor and acceptor intensity time-series. During FRET the two channels
fluctuate in **anti-phase** — a distance change that raises one lowers the other —
so their zero-lag cross-correlation is negative, and its magnitude scores how
cleanly a molecule anticorrelates. Analyzing the donor–acceptor cross-correlation
this way recovers conformational/kinetic information directly from the intensity
fluctuations [Torres2007], which arise from correlated donor–acceptor
fluctuations during energy transfer [Yu2007]. The lag-1 magnitude feeds the
anticorrelation-event finder (§7.6/§7.7).

Implementation — vectorized FFT with principled Pearson normalization:
``scipy.signal.correlate(d0, a0, method="fft")`` on the mean-subtracted channels,
divided by ``N · σ_d · σ_a`` (population std) so the zero-lag value equals the
Pearson correlation coefficient in ``[-1, 1]`` (negative ⇒ anticorrelated). This
is the *biased* normalized cross-correlation (every lag divided by the same ``N``),
so the curve tapers toward 0 at large ``|lag|``. A constant channel (σ = 0) has an
undefined correlation → NaN, never a fabricated 0.

This deliberately diverges from tMAVEN's ``selection.py`` cross-correlation, which
is a raw (unnormalized), gradient-based, lag-0-only "do the jumps coincide?"
heuristic; PRD §7.7 specifies the principled Pearson-normalized curve plus a
lag-1 magnitude.

References
----------
[Torres2007] Torres & Levitus. "Measuring conformational dynamics: a new FCS-FRET
    approach." The Journal of Physical Chemistry B (2007). — the auto- and
    cross-correlation of donor/acceptor intensities carries the kinetics.
[Yu2007] Yu. "Fluorescent resonant energy transfer: correlated fluctuations of
    donor and acceptor." The Journal of Chemical Physics (2007).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
from scipy import signal

from tether.analysis._store import windowed_channels

if TYPE_CHECKING:  # pragma: no cover - typing only
    from tether.analysis._store import ProjectRef

__all__ = ["CrossCorrelation", "cross_correlation", "population_cross_correlation"]


@dataclass(frozen=True)
class CrossCorrelation:
    """A Pearson-normalized donor–acceptor cross-correlation curve (self-describing).

    ``values[i]`` is the normalized cross-correlation at lag ``lags[i]`` (frames),
    with the convention ``r[k] ∝ Σ_t donor[t]·acceptor[t − k]`` — so a positive lag
    correlates the donor with an *earlier* acceptor frame. ``lag0`` is the zero-lag
    Pearson coefficient (negative ⇒ anticorrelated); ``lag1_magnitude`` is
    ``|r[+1]|``. Undefined correlations (a constant channel) are ``NaN`` throughout.
    """

    lags: np.ndarray  # (2L + 1,) int64 lag axis, frames
    values: np.ndarray  # (2L + 1,) float64 normalized cross-correlation
    lag0: float  # r at lag 0 = zero-lag Pearson coefficient (negative ⇒ anticorrelated)
    lag1_magnitude: float  # |r at lag +1|
    n_frames: int  # single pair: trace length; population: shortest contributing trace
    normalize: str
    n_molecules: int | None  # contributing molecules (population); None for a single pair


def _at_lag(lags: np.ndarray, values: np.ndarray, k: int) -> float:
    idx = np.nonzero(lags == k)[0]
    return float(values[idx[0]]) if idx.size else float("nan")


def cross_correlation(
    donor: np.ndarray,
    acceptor: np.ndarray,
    *,
    max_lag: int | None = None,
    normalize: str = "pearson",
) -> CrossCorrelation:
    """Pearson-normalized donor–acceptor cross-correlation of one trace (pure core).

    Parameters
    ----------
    donor, acceptor
        1-D per-frame intensity time-series of equal length (``>= 2`` frames). Must
        be finite — cross-correlation is undefined across NaN gaps, so a non-finite
        input raises rather than silently propagating NaN into every lag.
    max_lag
        If given, return only lags ``|k| <= max_lag``; else the full ``±(N - 1)``.
    normalize
        Only ``"pearson"`` is supported (the §7.7 principled normalization).

    Returns
    -------
    CrossCorrelation

    Raises
    ------
    ValueError
        Mismatched/short/non-finite inputs, an unknown ``normalize``, or a negative
        ``max_lag``.
    """
    d = np.asarray(donor, dtype=np.float64).ravel()
    a = np.asarray(acceptor, dtype=np.float64).ravel()
    if d.shape != a.shape:
        raise ValueError(f"donor and acceptor must be the same length, got {d.shape} vs {a.shape}")
    n = int(d.shape[0])
    if n < 2:
        raise ValueError(f"need at least 2 frames for cross-correlation, got {n}")
    if not (np.isfinite(d).all() and np.isfinite(a).all()):
        raise ValueError("donor and acceptor must be finite (no NaN/inf gaps)")
    if normalize != "pearson":
        raise ValueError(f"normalize must be 'pearson', got {normalize!r}")
    if max_lag is not None:  # validate before the FFT so a bad arg doesn't waste the correlation
        max_lag = int(max_lag)
        if max_lag < 0:
            raise ValueError(f"max_lag must be non-negative, got {max_lag}")

    d0 = d - d.mean()
    a0 = a - a.mean()
    sd = float(np.sqrt(np.mean(d0 * d0)))  # population std (ddof=0)
    sa = float(np.sqrt(np.mean(a0 * a0)))
    denom = n * sd * sa

    full = signal.correlate(d0, a0, mode="full", method="fft")
    lags = signal.correlation_lags(n, n, mode="full").astype(np.int64)
    # A flat channel (max == min) has an undefined correlation -> NaN. Test the range
    # directly rather than `denom == 0.0`: a bit-identical non-dyadic value (e.g. 0.1)
    # leaves a ~1e-17 mean-subtraction residue, so `denom` is tiny-but-nonzero and would
    # slip a spurious pseudo-correlation through. Never fabricate a value for undefined input.
    undefined = float(d.max()) == float(d.min()) or float(a.max()) == float(a.min())
    values = np.full(full.shape, np.nan, dtype=np.float64) if undefined else full / denom

    if max_lag is not None:  # already validated above; here just truncate the curve
        keep = np.abs(lags) <= max_lag
        lags = lags[keep]
        values = values[keep]

    return CrossCorrelation(
        lags=lags,
        values=values,
        lag0=_at_lag(lags, values, 0),
        lag1_magnitude=abs(_at_lag(lags, values, 1)),
        n_frames=n,
        normalize=normalize,
        n_molecules=None,
    )


def population_cross_correlation(
    project: ProjectRef,
    *,
    molecule_keys: list[str] | None = None,
    intensity_quantity: str = "corrected",
    max_lag: int | None = None,
    normalize: str = "pearson",
    include_rejected: bool = False,
) -> CrossCorrelation:
    """Population donor–acceptor cross-correlation over a ``.tether`` store (§7.7).

    Averages the per-molecule Pearson-normalized cross-correlation curves on a
    common lag axis. The common half-width is the smallest usable molecule's
    ``N - 1`` (clamped to ``max_lag`` when given). Molecules that are too short
    (``< 2`` frames), non-finite, or constant (undefined correlation) are skipped —
    ``n_molecules`` reports how many actually contributed.

    Parameters mirror :func:`population_apparent_e_histogram`; ``max_lag``,
    ``normalize`` mirror :func:`cross_correlation`.

    Raises
    ------
    ValueError
        No selected molecule has ``>= 2`` frames.
    """
    pairs = windowed_channels(project, molecule_keys, intensity_quantity, include_rejected)
    usable = [
        (d, a)
        for (d, a) in pairs
        if d.shape[0] >= 2 and np.isfinite(d).all() and np.isfinite(a).all()
    ]
    if not usable:
        raise ValueError("no selected molecule has >= 2 finite frames for cross-correlation")

    common = min(d.shape[0] - 1 for (d, _) in usable)
    if max_lag is not None:
        if int(max_lag) < 0:
            raise ValueError(f"max_lag must be non-negative, got {max_lag}")
        common = min(common, int(max_lag))
    lags = np.arange(-common, common + 1, dtype=np.int64)

    acc = np.zeros(lags.shape[0], dtype=np.float64)
    n_contributing = 0
    min_len = 0
    for donor, acceptor in usable:
        cc = cross_correlation(donor, acceptor, max_lag=common, normalize=normalize)
        if not np.isfinite(cc.values).any():
            continue  # constant channel -> undefined; skip rather than fabricate a 0
        acc += cc.values
        min_len = cc.n_frames if n_contributing == 0 else min(min_len, cc.n_frames)
        n_contributing += 1

    values = acc / n_contributing if n_contributing else np.full(lags.shape[0], np.nan)
    return CrossCorrelation(
        lags=lags,
        values=values,
        lag0=_at_lag(lags, values, 0),
        lag1_magnitude=abs(_at_lag(lags, values, 1)),
        n_frames=int(min_len),
        normalize=normalize,
        n_molecules=n_contributing,
    )
