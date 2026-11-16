# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Agreement with Deep-LASI on the laboratory acquisitions of the workstation (#715).

Oracle (a) (ADR-0022, ``tests/_oracle_a.py``) compares Tether's extraction with Deep-LASI on
one acquisition, video 010. This module holds the arithmetic that the workstation tests of
``tests/test_deeplasi_agreement.py`` apply to every acquisition with a Deep-LASI result, and
that ``tests/test_deeplasi_agreement_math.py`` checks on synthetic numbers in the required
matrix:

* :func:`deeplasi_corrected_fret` is Deep-LASI's corrected FRET on the traces of its ``.mat``
  export, with its own per-molecule factors ``b`` (leakage) and ``g`` (gamma), in the
  convention that its gamma estimator uses: the donor is scaled by ``(1 + b)``
  (``deep_autocorrect_2color.m``, ADR-0028).
* :func:`fret_agreement` puts the same traces through Tether's correction chain (photobleach
  detector, leakage, gamma, :func:`tether.fret.efficiency.corrected_fret`) and compares the
  mean corrected FRET of each molecule with Deep-LASI's. It also checks the convention of
  ADR-0028 on real numbers: Tether's formula with ``alpha = b`` and ``gamma = g * (1 + b)``
  must give Deep-LASI's value to rounding.
* :func:`compare_exports` scores one Deep-LASI export against another with the oracle (a)
  scorer, for the scripted rerun of video 010 against the old export.

The ``.mat`` reader of :mod:`tether.io.deeplasi` leaves ``b`` and ``g`` out on purpose (M3);
:func:`read_factors` reads them here, for the tests only.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from tether.io.deeplasi import DeepLasiExport

__all__ = [
    "PARITY_ATOL",
    "compare_exports",
    "deeplasi_corrected_fret",
    "fret_agreement",
    "read_factors",
]

#: Largest difference allowed between Tether's formula with Deep-LASI's factors (ADR-0028
#: convention) and Deep-LASI's formula. The two are the same expression, so only floating-point
#: rounding separates them; this is not a scientific tolerance.
PARITY_ATOL = 1e-9


def read_factors(mat_path: str | Path) -> tuple[np.ndarray, np.ndarray]:
    """Deep-LASI's per-molecule leakage ``b`` and gamma ``g`` from a ``.mat`` export.

    ``exportTRACY`` writes the median in place of a NaN, so both are finite when present.
    """
    import scipy.io as sio  # noqa: PLC0415

    mat = sio.loadmat(str(mat_path), variable_names=["b", "g"], squeeze_me=False)
    missing = [key for key in ("b", "g") if key not in mat]
    if missing:
        raise ValueError(f"the export has no {' or '.join(missing)} field")
    b = np.asarray(mat["b"], dtype=np.float64).reshape(-1)
    g = np.asarray(mat["g"], dtype=np.float64).reshape(-1)
    if b.shape != g.shape:
        raise ValueError(f"b has {b.size} values and g has {g.size}")
    return b, g


def deeplasi_corrected_fret(
    donor: np.ndarray, acceptor: np.ndarray, b: np.ndarray, g: np.ndarray
) -> np.ndarray:
    """``(A - b*D) / ((A - b*D) + g*(1 + b)*D)``, per molecule (rows) and frame (columns).

    The denominator of Deep-LASI's gamma estimator (``deep_autocorrect_2color.m``): the
    leaked photons are added back to the donor. ``donor`` and ``acceptor`` are the
    background-corrected traces (``donc``, ``accc``); a zero denominator gives NaN.
    """
    donor = np.asarray(donor, dtype=np.float64)
    acceptor = np.asarray(acceptor, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64).reshape(-1, 1)
    g = np.asarray(g, dtype=np.float64).reshape(-1, 1)
    a_corr = acceptor - b * donor
    denom = a_corr + g * (1.0 + b) * donor
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(denom != 0.0, a_corr / denom, np.nan)


def _window_mean(values: np.ndarray, stop: int) -> float:
    part = values[:stop]
    part = part[np.isfinite(part)]
    return float(part.mean()) if part.size else float("nan")


def _pearson(x: np.ndarray, y: np.ndarray) -> float:
    if x.size < 3 or np.std(x) == 0.0 or np.std(y) == 0.0:
        return float("nan")
    return float(np.corrcoef(x, y)[0, 1])


def fret_agreement(export: DeepLasiExport, b: np.ndarray, g: np.ndarray) -> dict[str, float]:
    """Tether's corrected FRET against Deep-LASI's, on the traces of one Deep-LASI export.

    Both start from the same background-corrected traces, so only the corrections differ:
    Deep-LASI uses its own ``b`` and ``g``; Tether finds the bleach frames with its detector,
    the leakage of the acquisition from the donor-only tails, and the gamma of each molecule
    from its acceptor-bleach step (median fallback). The mean of each molecule is taken over the
    same frames for both tools: up to Tether's first bleach of the summed signal.

    Returns summary numbers only. ``parity_max_abs`` is the largest difference between
    Tether's formula with ``alpha = b, gamma = g * (1 + b)`` and Deep-LASI's formula, over
    every frame of every molecule: ADR-0028 says it is rounding.
    """
    from tether.fret.efficiency import corrected_fret  # noqa: PLC0415
    from tether.fret.gamma import estimate_gamma  # noqa: PLC0415
    from tether.fret.leakage import estimate_leakage_alpha  # noqa: PLC0415
    from tether.fret.photobleach import detect_photobleach  # noqa: PLC0415

    donor = export.donor_corrected
    acceptor = export.acceptor_corrected
    n = export.n_molecules
    b = np.asarray(b, dtype=np.float64).reshape(-1)
    g = np.asarray(g, dtype=np.float64).reshape(-1)
    if b.size != n or g.size != n:
        raise ValueError(f"{n} molecules but {b.size} b and {g.size} g values")

    e_deeplasi = deeplasi_corrected_fret(donor, acceptor, b, g)
    e_parity = np.vstack(
        [
            corrected_fret(donor[i], acceptor[i], alpha=b[i], gamma=g[i] * (1.0 + b[i]))
            for i in range(n)
        ]
    ).reshape(donor.shape)
    both = np.isfinite(e_deeplasi) & np.isfinite(e_parity)
    parity = float(np.max(np.abs(e_deeplasi[both] - e_parity[both]))) if both.any() else 0.0

    bleach = [detect_photobleach(donor[i], acceptor[i]) for i in range(n)]
    d_rows = [donor[i] for i in range(n)]
    a_rows = [acceptor[i] for i in range(n)]
    acc_pb = [r.acceptor_pb for r in bleach]
    don_pb = [r.donor_pb for r in bleach]
    leakage = estimate_leakage_alpha(d_rows, a_rows, acc_pb, don_pb)
    alpha = float(leakage.alpha) if leakage.alpha is not None else float("nan")
    gamma_est = None
    if np.isfinite(alpha):
        gamma_est = estimate_gamma(d_rows, a_rows, [alpha] * n, acc_pb, don_pb)
    gamma_t = np.full(n, np.nan)
    if gamma_est is not None and gamma_est.gamma is not None:
        gamma_t = np.array([gamma_est.effective_gamma(i) for i in range(n)], dtype=np.float64)

    mean_t = np.full(n, np.nan)
    mean_d = np.full(n, np.nan)
    for i in range(n):
        stop = max(int(bleach[i].sum_pb), 1)
        mean_d[i] = _window_mean(e_deeplasi[i], stop)
        if np.isfinite(alpha) and np.isfinite(gamma_t[i]):
            e_t = corrected_fret(donor[i], acceptor[i], alpha=alpha, gamma=float(gamma_t[i]))
            mean_t[i] = _window_mean(e_t, stop)
    ok = np.isfinite(mean_t) & np.isfinite(mean_d)
    diff = np.abs(mean_t[ok] - mean_d[ok])
    gamma_dl = g * (1.0 + b)
    return {
        "n_molecules": n,
        "n_compared": int(ok.sum()),
        "parity_max_abs": parity,
        "alpha_tether": alpha,
        "b_median_deeplasi": float(np.median(b)) if n else float("nan"),
        "gamma_tether_median": float(np.nanmedian(gamma_t)) if np.isfinite(gamma_t).any()
        else float("nan"),
        "gamma_deeplasi_median": float(np.median(gamma_dl)) if n else float("nan"),
        "fret_abs_diff_median": float(np.median(diff)) if diff.size else float("nan"),
        "fret_abs_diff_p90": float(np.percentile(diff, 90)) if diff.size else float("nan"),
        "fret_pearson": _pearson(mean_t[ok], mean_d[ok]),
    }


def compare_exports(reference: DeepLasiExport, other: DeepLasiExport) -> dict[str, float]:
    """Score ``other`` against ``reference`` with the oracle (a) scorer; summary numbers only.

    Adds the largest relative difference of the raw donor and acceptor traces of the matched
    molecules over their common frames, so that an exact repeat reads 0.
    """
    from tether.project.oracle import evaluate_extraction  # noqa: PLC0415

    res = evaluate_extraction(reference, other.donor_xy, other.donor_raw, other.acceptor_raw)
    frames = min(reference.n_frames, other.n_frames)
    worst = 0.0
    for gi, ei, _ in res.matches:
        for ref, got in (
            (reference.donor_raw[gi, :frames], other.donor_raw[ei, :frames]),
            (reference.acceptor_raw[gi, :frames], other.acceptor_raw[ei, :frames]),
        ):
            scale = float(np.max(np.abs(ref))) or 1.0
            worst = max(worst, float(np.max(np.abs(got - ref))) / scale)
    return {
        "n_reference": res.n_ground_truth,
        "n_other": res.n_extracted,
        "n_matched": res.n_matched,
        "matched_fraction": res.recall,
        "position_rms_px": res.coord_rms_px,
        "donor_pearson_median": res.donor_pearson_median,
        "acceptor_pearson_median": res.acceptor_pearson_median,
        "n_frames_reference": reference.n_frames,
        "n_frames_other": other.n_frames,
        "trace_max_rel_diff": worst if res.n_matched else float("nan"),
    }
