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
  With the factors it records the shape of the acceptor-bleach step behind each of Tether's
  γ (#1233): why each trace did or did not give one, and how the donor and acceptor changed
  across its step (:func:`gamma_step_changes`). These numbers are evidence for #546, never
  a limit.
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
    "GAMMA_REASONS",
    "PARITY_ATOL",
    "RERUN_ID",
    "STATE",
    "agreement_file",
    "check_movie",
    "compare_exports",
    "deeplasi_corrected_fret",
    "fret_agreement",
    "gamma_step_changes",
    "gamma_step_numbers",
    "harness",
    "read_factors",
    "window_export",
    "window_record",
]

#: The role of the saved Deep-LASI state (``.tdat``) of an agreement id. It is not ``tdat``:
#: the speed check (``tests/test_workstation_perf.py``) gives a ``tdat`` role to its extraction,
#: and the agreement ids must not change what that check measures.
STATE = "deeplasi_state"

#: SHA-256 and size of each Deep-LASI file of the agreement checks, by neutral id and role
#: (measured on the workstation, 2026-11-16). ``mat`` is the TRACY-style export (read through
#: the ``window`` role), :data:`STATE` the saved state (detection settings), ``tmap`` the map
#: file of the series. Video 010 (``lab-01``)
#: is in ``tests/_oracle_a.py``. The ``dlexp-*`` ids are exports whose movie is not on the
#: workstation; ``lab-13``'s export was made from the other export route of its movie.
FILES: dict[str, dict[str, tuple[str, int]]] = {
    "lab-02": {
        "tmap": ("ddf3113c323de687d2ebda22b9b14f4cd575e76b214504d417ccc22f1f6e64a7", 3_880_022),
        STATE: ("095eed8349a2f24c28cfc73cf7b2b48085842751bd4c0e8b5712d53741af41c8", 43_541_083),
        "mat": ("35be4b6fc3e1d911d4de3b0e7a3defcae88d0d1a145186e233f57cf5552bcae4", 15_993_472),
    },
    "lab-03": {
        "tmap": ("aede98618faecac0713f1aec76e3cc837bbde84bfa25484f6989e1d473ea64bf", 3_882_547),
        STATE: ("49a787775ed8142825e67ad4fc021c96582e5bffda80ecb5ac98e62cfc389848", 37_007_216),
        "mat": ("1ac25959faf5ad8708b2e6a9e4e0813e5d9b0f1e06d4ad00933a3f5125def205", 9_177_507),
    },
    "lab-04": {
        "tmap": ("aede98618faecac0713f1aec76e3cc837bbde84bfa25484f6989e1d473ea64bf", 3_882_547),
        STATE: ("314cde633fa8adb8bd98dda8f9107e558c4cc2f56295e870395f2de93f67995a", 31_535_633),
        "mat": ("7e4174af9c3e3cfee93675447602dadb772c2f58bd486cf87b23b7c6d2c89bad", 7_379_223),
    },
    "lab-05": {
        "tmap": ("aede98618faecac0713f1aec76e3cc837bbde84bfa25484f6989e1d473ea64bf", 3_882_547),
        STATE: ("fa7deb2be5e82c42ec1d947c2ffe15590747e3fcd0540bedfe4fb1dfac97a5e5", 35_569_038),
        "mat": ("ba6f1ec1ff9e3c4c9d501162e9b13cebe1113fcb089792abc1bd0b432b95c503", 8_653_159),
    },
    "lab-06": {
        "tmap": ("e45ef804e9db33c7200dd6adcc3e7df48cf299bcf523e51c6709c5f3915532ee", 3_873_879),
        STATE: ("a7ba12db7cd75f0b0eea2e2328570233ab3ae11a05b39345ed5c0bab215fdc57", 20_025_932),
        "mat": ("6b003b143d95cd59ee538907bff836d1313ecf8f596d427ecea653af1d83ce0c", 3_247_665),
    },
    "lab-07": {
        "tmap": ("e45ef804e9db33c7200dd6adcc3e7df48cf299bcf523e51c6709c5f3915532ee", 3_873_879),
        STATE: ("150fec80fbc841c4eda602c8f200f52760c119f0d4e09fb228f465070f178719", 25_625_907),
        "mat": ("5b4af7bd1516af2a5d9355bf8901fe4d844c7f128d3099b18f84ff5e5f60a491", 5_100_608),
    },
    "lab-08": {
        "tmap": ("e45ef804e9db33c7200dd6adcc3e7df48cf299bcf523e51c6709c5f3915532ee", 3_873_879),
        STATE: ("da95485b3af587cd402271f85d2bde4c0a590c73c8dda43c032e6c88711e666f", 12_510_821),
        "mat": ("46447cf66e1c22dc346a298f299c747274fd98a565baaca04e52609dde01efd6", 2_425_477),
    },
    "lab-10": {
        "tmap": ("6f1f8d3c5ae088e8110725d3ef8359413dd8cf193e7c86d0e81c2e215bd51ffb", 3_844_279),
        STATE: ("46fa597dc4d4ae661f7083ee9024954fd494acc4c2e2dc13cd87fe1e7a7be5cf", 10_447_214),
        "mat": ("e73ef536df8aa0a617cb0ea2b0fe377e7b971458451cdc969e10a2fb28d42f69", 697_109),
    },
    "lab-12": {
        "tmap": ("6f1f8d3c5ae088e8110725d3ef8359413dd8cf193e7c86d0e81c2e215bd51ffb", 3_844_279),
        STATE: ("b4a027bd09dfb3da8045b368767626d3e192b51094ea5dd8add5beef41c0867f", 13_687_632),
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
EXTRACTION_IDS: tuple[str, ...] = tuple(i for i, roles in FILES.items() if STATE in roles)
#: Every export with per-molecule factors: the corrected-FRET check starts from its traces.
#: Two more exports on the workstation hold only the ``-donc-accc-w.txt`` traces, with no
#: factors and no movie, so no check can use them (ADR-0115).
FRET_IDS: tuple[str, ...] = ("lab-01", *(i for i, roles in FILES.items() if "mat" in roles))
#: The id whose ``rerun`` role names the ``.mat`` export of the scripted Deep-LASI rerun, in
#: the folder that the run wrote (#715).
RERUN_ID = "lab-01"

_HARNESS = Path(__file__).resolve().parents[1] / "scripts" / "reference" / "matlab" / "harness.py"


@functools.cache
def harness():
    """``scripts/reference/matlab/harness.py`` (ADR-0074), loaded by path: it is no package."""
    spec = importlib.util.spec_from_file_location("tether_ref_harness", _HARNESS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    # Not cached: a file that changes content but not size within one pytest process must not
    # pass on the digest of its old bytes (#1193).
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


def window_record(private_data, neutral_id: str) -> tuple[Path, dict]:
    """The ``.mat`` export of ``neutral_id`` that Deep-LASI's window saved, and its record.

    The ``window`` role names the copy of the export in a folder made by ``harness.py freeze``,
    beside a ``provenance.json`` that says no scripted run made it and gives the file's own date
    (ADR-0074, ADR-0115). The role names a file, as every role does, and the folder must not be
    named by the neutral id: the runner treats each name in the mapping file as private. The
    copy must be the export the check was recorded on.
    """
    from _oracle_a import FILES as ORACLE_A_FILES  # noqa: PLC0415

    path = private_data.path(neutral_id, "window")
    record = harness().verify(path.parent)
    assert record["method"] == "saved-files" and record["tool"]["name"] == "deep-lasi", (
        f"{neutral_id}: the window record is not a Deep-LASI saved-files record"
    )
    names = [n for n in record["outputs"] if n.endswith(".mat")]
    assert names == [path.name], f"{neutral_id}: the window role is not the export of its record"
    assert record.get("saved", {}).get(path.name), f"{neutral_id}: the record gives no file date"
    pinned = ORACLE_A_FILES["mat"][0] if neutral_id == RERUN_ID else FILES[neutral_id]["mat"][0]
    assert record["outputs"][path.name] == pinned, (
        f"{neutral_id}: the frozen export is not the one the check was recorded on "
        f"(SHA-256 {pinned[:16]}…); see tests/_deeplasi_agreement.py"
    )
    return path, record


def window_export(private_data, neutral_id: str) -> Path:
    """The export of ``neutral_id`` that Deep-LASI's window saved; see :func:`window_record`."""
    return window_record(private_data, neutral_id)[0]


def check_movie(movie: Path, record: dict, neutral_id: str) -> Path:
    """``movie``, if it is the movie that the ``window`` record of ``neutral_id`` was frozen with.

    ``harness.py freeze`` keeps the SHA-256 of each input it is given (ADR-0074), so the record
    already pins the movie: a name table that points the id at a replaced or shortened movie
    fails here, before extraction, instead of being scored under the same id (#1115). A record
    with no ``movie`` input fails too: it pins nothing to compare with. The message names the id
    and the role, never a file.
    """
    want = sorted(record.get("inputs", {}).get("movie", {}).values())
    assert want, f"{neutral_id}/movie: the window record names no movie input to check against"
    got = _sha256(movie)
    assert want == [got], (
        f"{neutral_id}/movie: the movie is not the one the window record was frozen with "
        f"(SHA-256 {got[:16]}…, the record says {', '.join(w[:16] + '…' for w in want)})"
    )
    return movie


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


def _finite_median(values: np.ndarray) -> float:
    values = values[np.isfinite(values)]
    return float(np.median(values)) if values.size else float("nan")


def _pearson(x: np.ndarray, y: np.ndarray) -> float:
    if x.size < 3 or np.std(x) == 0.0 or np.std(y) == 0.0:
        return float("nan")
    return float(np.corrcoef(x, y)[0, 1])


#: Every ``TraceGamma.reason`` that :func:`tether.fret.gamma.trace_gamma` gives. Each is
#: recorded as ``n_gamma_reason_<reason>`` with ``-`` written ``_``, the workstation recorder's
#: metric-name rule (``[a-z][a-z0-9_]{0,63}``), so a reason no trace gave still reads 0.
GAMMA_REASONS: tuple[str, ...] = (
    "ok",
    "no-step",
    "short-pre",
    "short-post",
    "degenerate-donor",
    "out-of-range",
)

#: The reasons of a trace that passed both segment gates: its step changes were measured.
_PAST_THE_GATES = frozenset({"ok", "degenerate-donor", "out-of-range"})

#: The percentiles of the raw per-trace ratio ``ΔI_A / ΔI_D`` that are recorded.
_RATIO_PERCENTILES: tuple[int, ...] = (10, 25, 50, 75, 90)


def gamma_step_changes(
    gamma_est, donors: list[np.ndarray], acceptors: list[np.ndarray], alpha: float
) -> tuple[np.ndarray, np.ndarray]:
    """``(ΔI_D, ΔI_A)`` across the acceptor-bleach step of each trace, before any γ gate.

    Taken over the ``pre_window`` and ``post_window`` that ``gamma_est`` (an
    :func:`~tether.fret.gamma.estimate_gamma` result) reports for the trace, on the bare donor
    and on the leakage-corrected acceptor ``A − α·D``, as the estimator measures them:
    ``ΔI_D`` is the donor's rise and ``ΔI_A`` the acceptor's drop. Their ratio is therefore the
    estimator's own per-trace γ wherever that trace qualifies. A trace that failed a segment
    gate (``no-step``, ``short-pre``, ``short-post``) has no measured step and reads NaN.
    """
    from tether.fret.leakage import apply_leakage  # noqa: PLC0415

    n = len(gamma_est.per_trace)
    delta_d = np.full(n, np.nan)
    delta_a = np.full(n, np.nan)
    for i, trace in enumerate(gamma_est.per_trace):
        if trace.reason not in _PAST_THE_GATES:
            continue
        donor = np.asarray(donors[i], dtype=np.float64)
        acc = apply_leakage(donor, acceptors[i], alpha)
        (p0, p1), (q0, q1) = trace.pre_window, trace.post_window
        delta_d[i] = float(np.mean(donor[q0:q1])) - float(np.mean(donor[p0:p1]))
        delta_a[i] = float(np.mean(acc[p0:p1])) - float(np.mean(acc[q0:q1]))
    return delta_d, delta_a


def gamma_step_numbers(
    gamma_est, donors: list[np.ndarray], acceptors: list[np.ndarray], alpha: float
) -> dict[str, float]:
    """Summary numbers of the acceptor-bleach steps behind Tether's γ (#1233).

    Evidence for #546, never a limit: why Tether's γ differs from Deep-LASI's on some
    acquisitions should be readable off the result files. Gives a count of traces per
    :data:`GAMMA_REASONS`; ``n_gamma_step_clean``, the traces whose donor rose **and** whose
    corrected acceptor dropped across the step; and over the traces that passed both segment
    gates, the medians of ``ΔI_D`` and ``ΔI_A`` and the percentiles of the raw ratio
    ``ΔI_A / ΔI_D`` before the sign and ceiling gates (a trace with ``ΔI_D = 0`` left out). A
    quantity with no trace behind it is NaN, never a sentinel.
    """
    reasons = [t.reason for t in gamma_est.per_trace]
    unknown = sorted(set(reasons) - set(GAMMA_REASONS))
    if unknown:
        raise ValueError(f"the γ estimator gave a reason this check does not record: {unknown}")
    numbers: dict[str, float] = {
        f"n_gamma_reason_{r.replace('-', '_')}": reasons.count(r) for r in GAMMA_REASONS
    }
    delta_d, delta_a = gamma_step_changes(gamma_est, donors, acceptors, alpha)
    measured = np.isfinite(delta_d) & np.isfinite(delta_a)
    numbers["n_gamma_step_clean"] = int((measured & (delta_d > 0.0) & (delta_a > 0.0)).sum())
    usable = measured & (delta_d != 0.0)
    ratio = delta_a[usable] / delta_d[usable]
    for q in _RATIO_PERCENTILES:
        numbers[f"gamma_raw_ratio_p{q}"] = (
            float(np.percentile(ratio, q)) if ratio.size else float("nan")
        )
    numbers["gamma_step_delta_donor_median"] = _finite_median(delta_d[measured])
    numbers["gamma_step_delta_acceptor_median"] = _finite_median(delta_a[measured])
    return numbers


def fret_agreement(export: DeepLasiExport, b: np.ndarray, g: np.ndarray) -> dict[str, float]:
    """Tether's corrected FRET against Deep-LASI's, on the traces of one Deep-LASI export.

    Both start from the same background-corrected traces, so only the corrections differ:
    Deep-LASI uses its own ``b`` and ``g``; Tether finds the bleach frames with its detector,
    the leakage of the acquisition from the donor-only tails, and the gamma of each molecule
    from its acceptor-bleach step (median fallback). The mean of each molecule is taken over the
    same frames for both tools: up to Tether's first bleach of the summed signal.

    Returns summary numbers only. ``parity_max_abs`` is the largest difference between
    Tether's formula with ``alpha = b, gamma = g * (1 + b)`` and Deep-LASI's formula, over
    the ``n_parity_frames`` frames where both are finite: ADR-0028 says it is rounding.

    A comparison can be empty for two reasons, and the numbers say which: the export has no
    molecule with both factors (``n_deeplasi_factors`` 0), or Tether withholds a factor by its
    own rule, too few qualifying traces (``alpha_tether`` NaN, or ``n_tether_gamma`` 0).

    Wherever Tether's α is finite, the numbers also carry :func:`gamma_step_numbers` of the
    same γ estimate, at the estimator's default level window: the reason counts (summing to
    ``n_molecules``), ``n_gamma_step_clean`` and the step-change medians and raw-ratio
    percentiles. With α withheld no step was measured, and they are absent.
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
    factors = np.isfinite(b) & np.isfinite(g)
    steps = gamma_step_numbers(gamma_est, d_rows, a_rows, alpha) if gamma_est is not None else {}
    return {
        "n_molecules": n,
        "n_deeplasi_factors": int(factors.sum()),
        "n_tether_gamma": int(np.isfinite(gamma_t).sum()),
        "n_compared": int(ok.sum()),
        "n_parity_frames": int(both.sum()),
        "parity_max_abs": parity,
        "alpha_tether": alpha,
        "b_median_deeplasi": _finite_median(b),
        "gamma_tether_median": _finite_median(gamma_t),
        "gamma_deeplasi_median": _finite_median(gamma_dl[factors]),
        "fret_abs_diff_median": float(np.median(diff)) if diff.size else float("nan"),
        "fret_abs_diff_p90": float(np.percentile(diff, 90)) if diff.size else float("nan"),
        "fret_pearson": _pearson(mean_t[ok], mean_d[ok]),
        **steps,
    }


def compare_exports(reference: DeepLasiExport, other: DeepLasiExport) -> dict[str, float]:
    """Score ``other`` against ``reference`` with the oracle (a) scorer; summary numbers only.

    Adds the largest relative difference of the raw donor and acceptor traces of the matched
    molecules over their common frames, and the largest distance between their acceptor
    coordinates, so that an exact repeat reads 0 on both. A NaN in one trace or coordinate where
    the other has a number counts as an infinite difference, never as no difference.
    """
    from tether.project.oracle import evaluate_extraction  # noqa: PLC0415

    res = evaluate_extraction(reference, other.donor_xy, other.donor_raw, other.acceptor_raw)
    frames = min(reference.n_frames, other.n_frames)
    worst = 0.0
    acceptor_px = 0.0
    for gi, ei, _ in res.matches:
        ref_xy = np.asarray(reference.acceptor_xy[gi], float)
        got_xy = np.asarray(other.acceptor_xy[ei], float)
        if np.array_equal(np.isnan(ref_xy), np.isnan(got_xy)):
            known = ~np.isnan(ref_xy)
            step = float(np.linalg.norm((got_xy - ref_xy)[known]))
        else:  # a coordinate in one export and none in the other is no repeat
            step = float("inf")
        acceptor_px = max(acceptor_px, step)
        for ref, got in (
            (reference.donor_raw[gi, :frames], other.donor_raw[ei, :frames]),
            (reference.acceptor_raw[gi, :frames], other.acceptor_raw[ei, :frames]),
        ):
            worst = max(worst, _relative_difference(ref, got))
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
        "acceptor_position_max_px": acceptor_px if res.n_matched else float("nan"),
    }


def _relative_difference(ref: np.ndarray, got: np.ndarray) -> float:
    ref = np.asarray(ref, dtype=np.float64)
    got = np.asarray(got, dtype=np.float64)
    if not np.array_equal(np.isnan(ref), np.isnan(got)) or not np.array_equal(
        np.isinf(ref), np.isinf(got)
    ):
        return float("inf")
    both = np.isfinite(ref) & np.isfinite(got)
    if not both.any():
        return 0.0
    scale = float(np.max(np.abs(ref[both]))) or 1.0
    return float(np.max(np.abs(got[both] - ref[both]))) / scale
