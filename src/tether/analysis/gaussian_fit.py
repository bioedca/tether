# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fit a sum of Gaussians to a 1-D FRET-efficiency histogram (PRD §7.7, ADR-0120).

A FRET-efficiency histogram of molecules that sit in a few conformational states is, to a good
approximation, a sum of Gaussians, one for each state [Gopich2010]. Fitting such a sum to the
histogram by least squares, and reading the area of each Gaussian as the population of its state,
is the standard first analysis of a histogram, which more detailed methods are compared with
[Gopich2024]. Campos-Chavez et al. 2024 report the fraction of the closed state of the T-box
riboswitch in exactly this way (Fig. 5c, Methods "IntraFRET data analysis") [CamposChavez2024].

[`fit_gaussians`][] fits ``n`` Gaussians, ``n`` being the number of initial means given, to a
[`Histogram1D`][tether.analysis.histogram.Histogram1D]:

    f(x) = sum_i A_i * exp(-(x - mu_i)**2 / (2 * sigma_i**2))

by unconstrained non-linear least squares (``scipy.optimize.curve_fit``, Levenberg-Marquardt) on
the bin centres and the bin heights, with each bin given the same weight. The result,
[`GaussianFit`][], holds each Gaussian's mean, width, peak height, area and fraction of the total
area, each with a standard error, and the full parameter covariance.

**Two definitions of the area.** ``area="integral"`` (the default) is the area of the Gaussian
over the whole axis, ``A * sqrt(2 pi) * sigma``, the formula of the Methods of
[CamposChavez2024]. ``area="bin_sum"`` is the sum of the Gaussian's height at each bin centre
times the bin width: the area *inside the histogram range*, by the midpoint rule. That is what the
published notebook computes, and it is the one that reproduces the published fractions to their
fourth decimal (ADR-0120). The two differ where a Gaussian reaches past the end of the range.

**The errors.** The standard errors of the parameters are ``sqrt(diag(pcov))``, with ``pcov``
scaled by the variance of the residuals (``curve_fit``'s default for unweighted data). The errors
of the areas and of the fractions are propagated from the *full* covariance, by the first-order
propagation formula for a function of correlated parameters [Tellinghuisen2001]: for a derived
quantity ``g(p)``, ``var(g) = J C J^T`` with ``J = dg/dp``. The peak height and the width of one
Gaussian are strongly anti-correlated, and the area of one Gaussian is in the total of all of them,
so a propagation that leaves the covariances out gives a different number. The published notebook
of [CamposChavez2024] leaves them out; ADR-0120 records by how much.

The fit has no random part and no bound: a width that comes back negative is reported as its
absolute value (the model depends only on ``sigma**2``), and the Gaussians are reported in
ascending order of mean, whatever the order of the initial means. A fit that does not converge, or
whose covariance cannot be estimated, gives a [`GaussianFit`][] with ``success = False`` and
``NaN`` values instead of raising.

References
----------
[Gopich2010] Gopich & Szabo. "FRET efficiency distributions of multistate single molecules."
    J. Phys. Chem. B 114(46):15221-15226 (2010). doi:10.1021/jp105359z. The histogram of a
    multi-state molecule is approximated by a sum of Gaussians, one for each state.
[Gopich2024] Gopich & Chung. "Unraveling burst selection bias in single-molecule FRET of species
    with unequal brightness and diffusivity." J. Phys. Chem. B 128 (2024).
    doi:10.1021/acs.jpcb.4c01178. Compares a likelihood method with standard Gaussian fitting of
    FRET efficiency histograms for species populations.
[CamposChavez2024] Campos-Chavez, Paul, Zhou, Alonso, Verma, Fei & Mondragón. "Translational T-box
    riboswitches bind tRNA by modulating conformational flexibility." Nat. Commun. 15:6592 (2024).
    doi:10.1038/s41467-024-50885-x. Fig. 5c: the fraction of the closed state from a
    two-Gaussian fit of the intraFRET histogram.
[Tellinghuisen2001] Tellinghuisen. "Statistical error propagation." J. Phys. Chem. A 105 (2001).
    doi:10.1021/jp003484u. The propagation formula with the covariances of correlated
    least-squares parameters, and its test on non-linear fits.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Sequence

    from tether.analysis.histogram import Histogram1D

__all__ = [
    "AREA_METHODS",
    "DEFAULT_AREA_METHOD",
    "DEFAULT_INITIAL_WIDTH",
    "GaussianFit",
    "fit_gaussians",
    "gaussian_sum",
]

#: Initial width of every Gaussian, in FRET efficiency: the value of the published notebook of
#: Campos-Chavez et al. 2024. A starting point of the optimizer, which a converged fit forgets.
DEFAULT_INITIAL_WIDTH = 0.1
#: The two definitions of a Gaussian's area (module docstring).
AREA_METHODS: tuple[str, ...] = ("integral", "bin_sum")
DEFAULT_AREA_METHOD = "integral"

_SQRT_2PI = float(np.sqrt(2.0 * np.pi))


def gaussian_sum(x: np.ndarray, *params: float) -> np.ndarray:
    """Evaluate ``sum_i A_i * exp(-(x - mu_i)**2 / (2 * sigma_i**2))`` at ``x``.

    ``params`` is ``(A1, mu1, sigma1, A2, mu2, sigma2, ...)``, three for each Gaussian.
    """
    x = np.asarray(x, dtype=np.float64)
    out = np.zeros_like(x)
    for i in range(0, len(params), 3):
        a, mu, s = params[i : i + 3]
        out = out + a * np.exp(-((x - mu) ** 2) / (2.0 * s**2))
    return out


@dataclass(frozen=True)
class GaussianFit:
    """A sum of Gaussians fitted to a 1-D histogram (see [`fit_gaussians`][]).

    Each per-Gaussian array has one entry for each Gaussian, in ascending order of mean.
    ``amplitudes`` are peak heights in the histogram's unit (a density for a density histogram);
    ``widths`` are the standard deviations ``sigma``. ``areas`` follow ``area_method`` and
    ``fractions`` are ``areas / areas.sum()``. Every ``*_stderr`` is a one-sigma standard error:
    of a parameter from ``sqrt(diag(covariance))``, of an area or a fraction propagated from the
    full ``covariance``. ``covariance`` is ``(3n, 3n)`` in the order ``(A1, mu1, sigma1, A2, ...)``
    of the sorted Gaussians. ``model`` is the fitted sum at ``bin_centers`` and ``residuals`` is
    ``observed - model``. ``success`` is ``False`` when the fit did not converge or its covariance
    could not be estimated; every fitted value is then ``NaN``.
    """

    means: np.ndarray
    widths: np.ndarray
    amplitudes: np.ndarray
    mean_stderr: np.ndarray
    width_stderr: np.ndarray
    amplitude_stderr: np.ndarray
    covariance: np.ndarray
    areas: np.ndarray
    area_stderr: np.ndarray
    fractions: np.ndarray
    fraction_stderr: np.ndarray
    area_method: str
    initial_means: tuple[float, ...]
    initial_width: float
    bin_centers: np.ndarray
    observed: np.ndarray
    model: np.ndarray
    residuals: np.ndarray
    r_squared: float
    n_points: int
    success: bool

    @property
    def n_components(self) -> int:
        """Number of Gaussians fitted."""
        return len(self.initial_means)

    @property
    def params(self) -> np.ndarray:
        """The fitted parameters ``(A1, mu1, sigma1, A2, ...)`` of the sorted Gaussians."""
        return np.column_stack([self.amplitudes, self.means, self.widths]).ravel()

    def evaluate(self, x: np.ndarray) -> np.ndarray:
        """Evaluate the fitted sum of Gaussians at ``x``."""
        return gaussian_sum(x, *self.params)


def _area_jacobian(
    params: np.ndarray, centers: np.ndarray, widths: np.ndarray, method: str
) -> tuple[np.ndarray, np.ndarray]:
    """``(areas, d areas / d params)`` for ``params = (A1, mu1, s1, ...)`` with every ``s > 0``."""
    n = params.size // 3
    areas = np.empty(n)
    jac = np.zeros((n, params.size))
    for i in range(n):
        a, mu, s = params[3 * i : 3 * i + 3]
        if method == "integral":
            areas[i] = a * _SQRT_2PI * s
            jac[i, 3 * i : 3 * i + 3] = (_SQRT_2PI * s, 0.0, a * _SQRT_2PI)
        else:
            d = centers - mu
            g = np.exp(-(d**2) / (2.0 * s**2)) * widths
            areas[i] = a * float(np.sum(g))
            jac[i, 3 * i] = float(np.sum(g))
            jac[i, 3 * i + 1] = a * float(np.sum(g * d)) / s**2
            jac[i, 3 * i + 2] = a * float(np.sum(g * d**2)) / s**3
    return areas, jac


def fit_gaussians(
    histogram: Histogram1D,
    initial_means: Sequence[float],
    *,
    initial_width: float = DEFAULT_INITIAL_WIDTH,
    area: str = DEFAULT_AREA_METHOD,
) -> GaussianFit:
    """Fit a sum of Gaussians to a 1-D histogram and report each Gaussian's area and fraction.

    One Gaussian is fitted for each initial mean. Each starts at the given mean, the width
    ``initial_width`` and the peak height ``max(counts) / n``; with two Gaussians that is the
    starting point of the published notebook of Campos-Chavez et al. 2024. The fit is unweighted
    least squares on the bin centres and bin heights (module docstring).

    Parameters
    ----------
    histogram
        The histogram to fit, as
        [`apparent_e_histogram`][tether.analysis.histogram.apparent_e_histogram] returns it; a
        density or a count histogram (the peak heights are in its unit).
    initial_means
        One initial mean for each Gaussian, in the histogram's unit (FRET efficiency).
    initial_width
        The initial standard deviation of every Gaussian (default [`DEFAULT_INITIAL_WIDTH`][]).
    area
        ``"integral"`` (default; ``A * sqrt(2 pi) * sigma``) or ``"bin_sum"`` (the Gaussian
        summed over the bin centres, times the bin widths). See the module docstring.

    Returns
    -------
    GaussianFit

    Raises
    ------
    ValueError
        If there is no initial mean, an initial mean or the width is not finite, the width is not
        positive, or ``area`` is not one of [`AREA_METHODS`][].
    """
    from scipy.optimize import OptimizeWarning, curve_fit

    means0 = tuple(float(m) for m in initial_means)
    if not means0:
        raise ValueError("initial_means must hold at least one mean")
    if not all(np.isfinite(means0)):
        raise ValueError(f"initial_means must be finite, got {means0!r}")
    width0 = float(initial_width)
    if not (np.isfinite(width0) and width0 > 0.0):
        raise ValueError(f"initial_width must be a positive number, got {initial_width!r}")
    if area not in AREA_METHODS:
        raise ValueError(f"area must be one of {AREA_METHODS}, got {area!r}")

    centers = np.asarray(histogram.bin_centers, dtype=np.float64)
    # A copy: the result keeps the counts it was fitted to if the caller later changes them.
    observed = np.array(histogram.counts, dtype=np.float64, copy=True)
    bin_widths = np.diff(np.asarray(histogram.bin_edges, dtype=np.float64))
    n = len(means0)
    n_points = int(centers.size)

    def _failed() -> GaussianFit:
        nan_n = np.full(n, np.nan)
        return GaussianFit(
            means=nan_n.copy(),
            widths=nan_n.copy(),
            amplitudes=nan_n.copy(),
            mean_stderr=nan_n.copy(),
            width_stderr=nan_n.copy(),
            amplitude_stderr=nan_n.copy(),
            covariance=np.full((3 * n, 3 * n), np.nan),
            areas=nan_n.copy(),
            area_stderr=nan_n.copy(),
            fractions=nan_n.copy(),
            fraction_stderr=nan_n.copy(),
            area_method=area,
            initial_means=means0,
            initial_width=width0,
            bin_centers=centers,
            observed=observed,
            model=np.full_like(centers, np.nan),
            residuals=np.full_like(centers, np.nan),
            r_squared=float("nan"),
            n_points=n_points,
            success=False,
        )

    if n_points <= 3 * n or not np.all(np.isfinite(observed)):
        return _failed()

    peak = float(np.max(observed)) / n
    p0 = np.array([v for m in means0 for v in (peak, m, width0)])
    try:
        with warnings.catch_warnings():
            # An inestimable covariance comes back as inf; the check below turns it into a
            # failed fit, so the warning that announces it adds nothing.
            warnings.simplefilter("ignore", OptimizeWarning)
            popt, pcov = curve_fit(gaussian_sum, centers, observed, p0=p0, maxfev=10_000)
    except (RuntimeError, ValueError):
        return _failed()
    if not (np.all(np.isfinite(popt)) and np.all(np.isfinite(pcov))):
        return _failed()

    # The model depends on sigma**2 only: report |sigma|, flipping the sign of its covariance row
    # and column with it, then put the Gaussians in ascending order of mean.
    sign = np.ones_like(popt)
    sign[2::3] = np.sign(popt[2::3])
    if np.any(sign == 0.0):
        return _failed()
    popt = popt * sign
    pcov = pcov * np.outer(sign, sign)
    order = np.argsort(popt[1::3], kind="stable")
    idx = np.concatenate([[3 * i, 3 * i + 1, 3 * i + 2] for i in order])
    popt = popt[idx]
    pcov = pcov[np.ix_(idx, idx)]
    perr = np.sqrt(np.diag(pcov))

    areas, jac_area = _area_jacobian(popt, centers, bin_widths, area)
    total = float(np.sum(areas))
    if not (np.isfinite(total) and total != 0.0):
        return _failed()
    fractions = areas / total
    # d f_i / d a_j = (delta_ij - f_i) / total
    jac_fraction = ((np.eye(n) - fractions[:, None]) / total) @ jac_area
    area_var = np.einsum("ij,jk,ik->i", jac_area, pcov, jac_area)
    fraction_var = np.einsum("ij,jk,ik->i", jac_fraction, pcov, jac_fraction)

    model = gaussian_sum(centers, *popt)
    residuals = observed - model
    ss_tot = float(np.sum((observed - np.mean(observed)) ** 2))
    r_squared = 1.0 - float(np.sum(residuals**2)) / ss_tot if ss_tot > 0.0 else float("nan")
    return GaussianFit(
        means=popt[1::3].copy(),
        widths=popt[2::3].copy(),
        amplitudes=popt[0::3].copy(),
        mean_stderr=perr[1::3].copy(),
        width_stderr=perr[2::3].copy(),
        amplitude_stderr=perr[0::3].copy(),
        covariance=pcov,
        areas=areas,
        area_stderr=np.sqrt(np.maximum(area_var, 0.0)),
        fractions=fractions,
        fraction_stderr=np.sqrt(np.maximum(fraction_var, 0.0)),
        area_method=area,
        initial_means=means0,
        initial_width=width0,
        bin_centers=centers,
        observed=observed,
        model=model,
        residuals=residuals,
        r_squared=r_squared,
        n_points=n_points,
        success=True,
    )
