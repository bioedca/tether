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

import functools
import hashlib
import importlib.util
from pathlib import Path

import numpy as np

from tether.io.deeplasi import DeepLasiExport

__all__ = [
    "EXTRACTION_IDS",
    "FILES",
    "FRET_IDS",
    "PARITY_ATOL",
    "RERUN_ID",
    "agreement_file",
    "compare_exports",
    "deeplasi_corrected_fret",
    "fret_agreement",
    "harness",
    "read_factors",
    "window_export",
]

#: SHA-256 and size of each Deep-LASI file of the agreement checks, by neutral id and role
#: (measured on the workstation, 2026-11-16). ``mat`` is the TRACY-style export, ``tdat`` the
#: saved state (detection settings), ``tmap`` the map file of the series. Video 010 (``lab-01``)
#: is in ``tests/_oracle_a.py``. The ``dlexp-*`` ids are exports whose movie is not on the
#: workstation; ``lab-13``'s export was made from the other export route of its movie.
FILES: dict[str, dict[str, tuple[str, int]]] = {
    "lab-02": {
        "tmap": ("ddf3113c323de687d2ebda22b9b14f4cd575e76b214504d417ccc22f1f6e64a7", 3_880_022),
        "tdat": ("095eed8349a2f24c28cfc73cf7b2b48085842751bd4c0e8b5712d53741af41c8", 43_541_083),
        "mat": ("35be4b6fc3e1d911d4de3b0e7a3defcae88d0d1a145186e233f57cf5552bcae4", 15_993_472),
    },
    "lab-03": {
        "tmap": ("aede98618faecac0713f1aec76e3cc837bbde84bfa25484f6989e1d473ea64bf", 3_882_547),
        "tdat": ("49a787775ed8142825e67ad4fc021c96582e5bffda80ecb5ac98e62cfc389848", 37_007_216),
        "mat": ("1ac25959faf5ad8708b2e6a9e4e0813e5d9b0f1e06d4ad00933a3f5125def205", 9_177_507),
    },
    "lab-04": {
        "tmap": ("aede98618faecac0713f1aec76e3cc837bbde84bfa25484f6989e1d473ea64bf", 3_882_547),
        "tdat": ("314cde633fa8adb8bd98dda8f9107e558c4cc2f56295e870395f2de93f67995a", 31_535_633),
        "mat": ("7e4174af9c3e3cfee93675447602dadb772c2f58bd486cf87b23b7c6d2c89bad", 7_379_223),
    },
    "lab-05": {
        "tmap": ("aede98618faecac0713f1aec76e3cc837bbde84bfa25484f6989e1d473ea64bf", 3_882_547),
        "tdat": ("fa7deb2be5e82c42ec1d947c2ffe15590747e3fcd0540bedfe4fb1dfac97a5e5", 35_569_038),
        "mat": ("ba6f1ec1ff9e3c4c9d501162e9b13cebe1113fcb089792abc1bd0b432b95c503", 8_653_159),
    },
    "lab-06": {
        "tmap": ("e45ef804e9db33c7200dd6adcc3e7df48cf299bcf523e51c6709c5f3915532ee", 3_873_879),
        "tdat": ("a7ba12db7cd75f0b0eea2e2328570233ab3ae11a05b39345ed5c0bab215fdc57", 20_025_932),
        "mat": ("6b003b143d95cd59ee538907bff836d1313ecf8f596d427ecea653af1d83ce0c", 3_247_665),
    },
    "lab-07": {
        "tmap": ("e45ef804e9db33c7200dd6adcc3e7df48cf299bcf523e51c6709c5f3915532ee", 3_873_879),
        "tdat": ("150fec80fbc841c4eda602c8f200f52760c119f0d4e09fb228f465070f178719", 25_625_907),
        "mat": ("5b4af7bd1516af2a5d9355bf8901fe4d844c7f128d3099b18f84ff5e5f60a491", 5_100_608),
    },
    "lab-08": {
        "tmap": ("e45ef804e9db33c7200dd6adcc3e7df48cf299bcf523e51c6709c5f3915532ee", 3_873_879),
        "tdat": ("da95485b3af587cd402271f85d2bde4c0a590c73c8dda43c032e6c88711e666f", 12_510_821),
        "mat": ("46447cf66e1c22dc346a298f299c747274fd98a565baaca04e52609dde01efd6", 2_425_477),
    },
    "lab-10": {
        "tmap": ("6f1f8d3c5ae088e8110725d3ef8359413dd8cf193e7c86d0e81c2e215bd51ffb", 3_844_279),
        "tdat": ("46fa597dc4d4ae661f7083ee9024954fd494acc4c2e2dc13cd87fe1e7a7be5cf", 10_447_214),
        "mat": ("e73ef536df8aa0a617cb0ea2b0fe377e7b971458451cdc969e10a2fb28d42f69", 697_109),
    },
    "lab-12": {
        "tmap": ("6f1f8d3c5ae088e8110725d3ef8359413dd8cf193e7c86d0e81c2e215bd51ffb", 3_844_279),
        "tdat": ("b4a027bd09dfb3da8045b368767626d3e192b51094ea5dd8add5beef41c0867f", 13_687_632),
        "mat": ("82a0c20f483025d665bae366b623f6d8bca08d0652c1256b6c08964e9ab395ff", 1_787_705),
    },
    "lab-13": {
        "mat": ("4374db39566e2a7ccc7ae920cec85cb0cb4e2c189b65fbcce6de5a2cdce39e3c", 1_330_598),
    },
    "dlexp-01": {
        "mat": ("9cd6d8cf25316014a925f05f9e8eae8cb42f18caf9b2a7cd477c7ffcc35cbb9c", 1_593_803),
    },
    "dlexp-02": {
        "mat": ("c3744647b9a228ce07a53af45ddec34fb95e18e02db4daef52122fcaec8eba00", 3_223_113),
    },
}

#: Acquisitions whose movie, map file, saved state and export are all on the workstation: the
#: oracle (a) scorer runs on each (ADR-0115). Video 010 is oracle (a) itself
#: (``tests/test_oracle.py``). ``lab-09`` and ``lab-11`` have no export; ``lab-13``'s export
#: was made from a movie that is not on the workstation, so its spots cannot be compared.
EXTRACTION_IDS: tuple[str, ...] = tuple(i for i, roles in FILES.items() if "tdat" in roles)
#: Every export with per-molecule factors: the corrected-FRET check starts from its traces.
#: Two more exports on the workstation hold only the ``-donc-accc-w.txt`` traces, with no
#: factors and no movie, so no check can use them (ADR-0115).
FRET_IDS: tuple[str, ...] = ("lab-01", *(i for i, roles in FILES.items() if "mat" in roles))
#: The id whose ``rerun`` role names the folder of the scripted Deep-LASI rerun (#715).
RERUN_ID = "lab-01"

_HARNESS = Path(__file__).resolve().parents[1] / "scripts" / "reference" / "matlab" / "harness.py"


@functools.cache
def harness():
    """``scripts/reference/matlab/harness.py`` (ADR-0074), loaded by path: it is no package."""
    spec = importlib.util.spec_from_file_location("tether_ref_harness", _HARNESS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@functools.cache
def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 22), b""):
            digest.update(block)
    return digest.hexdigest()


def agreement_file(private_data, neutral_id: str, role: str) -> Path:
    """One Deep-LASI file of the agreement checks, verified against :data:`FILES`."""
    path = private_data.path(neutral_id, role)
    sha256, size = FILES[neutral_id][role]
    assert path.stat().st_size == size and _sha256(path) == sha256, (
        f"{neutral_id}/{role}: the file is not the one the agreement check was recorded on "
        f"(SHA-256 {sha256[:16]}…, {size} B); see tests/_deeplasi_agreement.py"
    )
    return path


def window_export(private_data, neutral_id: str) -> Path:
    """The ``.mat`` export of ``neutral_id`` that Deep-LASI's window saved, with its record.

    The ``window`` role names a folder made by ``harness.py freeze``: a copy of the export and a
    ``provenance.json`` that says no scripted run made it and gives the file's own date
    (ADR-0074, ADR-0115). The copy must be the export the check was recorded on.
    """
    from _oracle_a import FILES as ORACLE_A_FILES  # noqa: PLC0415

    folder = private_data.path(neutral_id, "window")
    record = harness().verify(folder)
    assert record["method"] == "saved-files" and record["tool"]["name"] == "deep-lasi", (
        f"{neutral_id}: the window record is not a Deep-LASI saved-files record"
    )
    [name] = [n for n in record["outputs"] if n.endswith(".mat")]
    assert record.get("saved", {}).get(name), f"{neutral_id}: the record gives no file date"
    pinned = ORACLE_A_FILES["mat"][0] if neutral_id == RERUN_ID else FILES[neutral_id]["mat"][0]
    assert record["outputs"][name] == pinned, (
        f"{neutral_id}: the frozen export is not the one the check was recorded on "
        f"(SHA-256 {pinned[:16]}…); see tests/_deeplasi_agreement.py"
    )
    return folder / name


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
        "gamma_tether_median": float(np.nanmedian(gamma_t))
        if np.isfinite(gamma_t).any()
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
