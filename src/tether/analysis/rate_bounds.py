# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Rate constants with Dirichlet error bounds from a transition count matrix (#758, ADR-0092).

The method is the one of the Source Data of [CamposChavez2024]
(``Kmatrix/CalculateKbounds.py``), which reports the rate constants of its Supplementary
Table 5 with a lower and an upper bound. It needs the **un-normalized** transition count
matrix of an HMM (the ``tmatrix`` that tMAVEN stores with a model, and that Tether stores
with each imported or fitted model under ``/idealization/{model}``):

1. Each row ``i`` of the count matrix ``C`` is the concentration vector of a Dirichlet
   distribution. With ``a0 = sum_j C[i, j]`` the row sum, the expected transition
   probability is ``p = C[i, j] / a0`` and its variance is
   ``var = p (1 - p) / (a0 + 1)`` (the variance of one component of a Dirichlet
   distribution).
2. A per-frame transition probability ``p`` and a frame time ``dt`` give the rate
   constant ``k = -ln(1 - p) / dt``: a first-order process of rate ``k`` leaves its state
   within one frame with the probability ``1 - exp(-k dt)``.
3. The bounds are the rates at ``p + sqrt(var)`` and at ``p - sqrt(var)``. They are not
   symmetric about ``k``, because the logarithm is not linear.

:func:`dirichlet_rate_bounds` is the pure-array core; :func:`model_rate_bounds` reads the
count matrix of a stored idealization model of a project and stamps the result with its
provenance.

Like the published script, the function returns the full ``N x N`` matrices. The
off-diagonal entries are the rate constants ``i -> j``. The diagonal entries are
``-ln(1 - p_ii) / dt`` as the script prints them; they are **not** rate constants, and
they are kept only so that the whole published output can be compared.

Four cases fall outside the published files, and each has one defined result
(ADR-0092):

* A count of zero gives ``p = 0``, ``var = 0`` and a rate and bounds of ``0``.
* A row whose sum is zero has no defined probability: its whole row is ``NaN``.
* A probability of ``1`` (the only non-zero count of its row) gives ``var = 0`` and a
  rate and bounds of ``+inf``.
* A bound probability is clipped to ``[0, 1]``: ``p + sqrt(var) >= 1`` gives an upper
  bound of ``+inf``, and ``p - sqrt(var) < 0`` a lower bound of ``0``. A rate constant is
  never negative, and the logarithm of a value below zero is not defined. No entry of the
  five published matrices reaches either limit.

References
----------
[CamposChavez2024] Campos-Chavez E, Paul S, Zhou Z, Alonso D, Verma AR, Fei J,
    Mondragón A. "Translational T-box riboswitches bind tRNA by modulating
    conformational flexibility." Nature Communications 15:6592 (2024).
    doi:10.1038/s41467-024-50885-x. Source Data ``Kmatrix/CalculateKbounds.py``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:  # pragma: no cover - typing only
    from os import PathLike

    from tether.project.core import Project

    ProjectRef = Project | str | PathLike[str]

__all__ = [
    "NORMALIZED_ROW_TOLERANCE",
    "RATE_BOUNDS_METHOD",
    "RateBounds",
    "dirichlet_rate_bounds",
    "model_rate_bounds",
]

#: The name of the method in the provenance stamp.
RATE_BOUNDS_METHOD = "dirichlet-rate-bounds (Campos-Chavez et al. 2024, CalculateKbounds.py)"

#: A matrix whose every row sums to 1 within this tolerance is a normalized transition
#: matrix, not a count matrix, and is refused: the variance needs the counts. This is an
#: input check, **not** a PRD §11.2 tunable.
NORMALIZED_ROW_TOLERANCE = 1e-9


@dataclass(frozen=True)
class RateBounds:
    """The rate matrix of a count matrix with its Dirichlet lower and upper bounds.

    All matrices are ``(N, N)`` float64 and row ``i``, column ``j`` is the transition
    ``i -> j``. ``counts`` is the input, ``norm_tmatrix`` the expected transition
    probabilities, ``variance`` their Dirichlet variance, ``rates`` the rate matrix in
    ``1 / s`` (when ``dt`` is in seconds), and ``lower``/``upper`` its bounds. The
    diagonal of ``rates``/``lower``/``upper`` is not a rate constant (module docstring).
    ``provenance`` is the analysis stamp (app version, time, parameters and source
    model) when the result came from :func:`model_rate_bounds`, else ``None``.
    """

    counts: np.ndarray
    dt: float
    norm_tmatrix: np.ndarray
    variance: np.ndarray
    rates: np.ndarray
    upper: np.ndarray
    lower: np.ndarray
    provenance: dict[str, object] | None = field(default=None, compare=False)

    @property
    def upper_difference(self) -> np.ndarray:
        """``upper - rates``, the published "Upper bounds difference" (``>= 0``)."""
        return self.upper - self.rates

    @property
    def lower_difference(self) -> np.ndarray:
        """``lower - rates``, the published "Lower bounds difference" (``<= 0``)."""
        return self.lower - self.rates


def _check_counts(counts: object) -> np.ndarray:
    c = np.asarray(counts, dtype="float64")
    if c.ndim != 2 or c.shape[0] != c.shape[1] or c.shape[0] == 0:
        raise ValueError(f"the count matrix must be square and not empty; its shape is {c.shape}")
    if not np.all(np.isfinite(c)):
        raise ValueError("the count matrix holds a value that is not finite")
    if np.any(c < 0):
        raise ValueError("the count matrix holds a negative count")
    if np.allclose(c.sum(axis=1), 1.0, rtol=0.0, atol=NORMALIZED_ROW_TOLERANCE):
        raise ValueError(
            "every row of this matrix sums to 1, so it is a normalized transition matrix; "
            "the Dirichlet variance needs the un-normalized transition counts (the model's "
            "tmatrix, not its norm_tmatrix)"
        )
    return c


def _rate(p: np.ndarray, dt: float) -> np.ndarray:
    # The expression of the published script, so that the digits agree; log(0) is -inf.
    with np.errstate(divide="ignore", invalid="ignore"):
        return -np.log(1 - p) / dt


def dirichlet_rate_bounds(counts: object, dt: float) -> RateBounds:
    """Rate constants with Dirichlet bounds from an un-normalized transition count matrix.

    Parameters
    ----------
    counts
        The ``(N, N)`` un-normalized transition count matrix: finite, not negative, and
        not a normalized matrix (a matrix whose every row sums to 1 is refused).
    dt
        The frame time, in seconds for rates in ``1 / s``. Finite and positive.

    Returns
    -------
    RateBounds
        The probabilities, variances, rates and bounds; ``provenance`` is ``None``.

    Raises
    ------
    ValueError
        For a matrix that is not square, holds a non-finite or negative value, or is
        normalized; or for a ``dt`` that is not finite and positive.
    """
    dt = float(dt)
    if not math.isfinite(dt) or dt <= 0:
        raise ValueError(f"dt must be a finite positive frame time; got {dt}")
    c = _check_counts(counts)
    a0 = c.sum(axis=1)[:, None]
    with np.errstate(divide="ignore", invalid="ignore"):
        p = c / a0  # a row of zero sum is 0/0 = NaN: its probabilities are undefined
    variance = (p * (1 - p)) / (a0 + 1)
    sd = np.sqrt(variance)
    rates = _rate(p, dt)
    # np.minimum / np.maximum keep NaN, so an undefined row stays undefined.
    upper = _rate(np.minimum(p + sd, 1.0), dt)
    lower = _rate(np.maximum(p - sd, 0.0), dt)
    # -log(1 - 0) is -0.0; a rate of zero is reported as +0.0.
    for m in (rates, upper, lower):
        m[m == 0] = 0.0
    return RateBounds(
        counts=c,
        dt=dt,
        norm_tmatrix=p,
        variance=variance,
        rates=rates,
        upper=upper,
        lower=lower,
    )


def _app_version() -> str:
    """Best-effort Tether version for the provenance stamp (NFR-REPRO); never raises."""
    try:
        from tether import __version__

        return str(__version__)
    except Exception:  # pragma: no cover - defensive; __version__ has its own fallback
        return "0.0.0+unknown"


def model_rate_bounds(
    project: ProjectRef,
    model_name: str,
    *,
    dt: float,
    created_utc: str | None = None,
) -> RateBounds:
    """:func:`dirichlet_rate_bounds` of the count matrix of a stored idealization model.

    Reads ``/idealization/{model_name}/tmatrix`` (the un-normalized count matrix) and
    returns its rates and bounds with ``provenance`` set: the method, the app version,
    ``created_utc`` (now, unless given), the parameters (``dt``), and the source (the
    project file name and the model's name, type, app version and creation time). The
    project is only read; nothing is written to it.

    Raises
    ------
    KeyError
        When the project has no such model.
    ValueError
        When the model stores no count matrix (for example a threshold or k-means
        model), or for the reasons of :func:`dirichlet_rate_bounds`.
    """
    from tether.project.core import Project as _Project
    from tether.project.idealize import read_idealization

    model = read_idealization(project, model_name)
    if model.tmatrix is None:
        raise ValueError(
            f"the model {model_name!r} (type {model.model_type!r}) stores no transition "
            "count matrix (tmatrix), so it has no Dirichlet rate bounds"
        )
    result = dirichlet_rate_bounds(model.tmatrix, dt)
    path = project.path if isinstance(project, _Project) else Path(project)
    provenance: dict[str, object] = {
        "analysis": "rate_bounds",
        "method": RATE_BOUNDS_METHOD,
        "app_version": _app_version(),
        "created_utc": datetime.now(UTC).isoformat() if created_utc is None else created_utc,
        "parameters": {"dt": result.dt},
        "source_project": Path(path).name,
        "model_name": model.model_name,
        "model_type": model.model_type,
        "model_app_version": model.app_version,
        "model_created_utc": model.created_utc,
    }
    return RateBounds(
        counts=result.counts,
        dt=result.dt,
        norm_tmatrix=result.norm_tmatrix,
        variance=result.variance,
        rates=result.rates,
        upper=result.upper,
        lower=result.lower,
        provenance=provenance,
    )
