# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tether beside the published numbers of Campos-Chavez et al. 2024 (#756, ADR-0097).

The Source Data archive of the paper (ADR-0083, manifest id ``campos2024-source-data``) holds the
trace matrices of ``smFRET_SourceData/`` and the numbers behind each panel in
``MainFigures-AllCombined.xlsx``. This module reads both and puts Tether's result beside each
published number. ``test_campos_chavez_2024.py`` holds each result to its frozen limit.

Nothing here is changed or written into the repository: a workbook is read from a temporary copy
when a test runs, as ADR-0083 requires.

Two facts of the archive set the method:

* The trace matrices hold each intensity with 8 significant digits (``%.7e``), and the workbook
  holds each published efficiency with 15. An efficiency that Tether calculates from the matrix can
  therefore differ from the published one by the rounding of the two intensities, never more.
  :func:`rounding_bound` is that difference, and each comparison of an efficiency is a ratio to it.
* The workbook does not say which frames of which molecule a histogram pools. The frames are found
  by :func:`find_windows`: each published value is located in the trace matrix, and a run of
  published values is one window of one molecule.

Run ``python tests/_campos_chavez_2024.py <archive>`` to print every measured deviation as JSON;
that is how the limits of the test module were measured on the three operating systems.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import zipfile
from pathlib import Path

import numpy as np

from _kinsoft_measured import read_workbook
from tether.analysis.dwell import fit_survival, survival_curve
from tether.analysis.histogram import apparent_e_histogram
from tether.analysis.tdp import transition_density
from tether.fret import apparent_fret

ARCHIVE_ID = "campos2024-source-data"
WORKBOOK = "MainFigures-AllCombined.xlsx"
DOUBLE_DIR = "ErrorCalculations_CodeAndSourceData/ErrorsDoubleExponential"
DT_S = 0.1  # 100 ms frames (Methods, "smFRET measurement")

#: FRET histogram sheet -> trace matrix it pools (Figs. 2c, 3b, 3h, 4b, 4f, 6b, 7b).
HISTOGRAMS = {
    "Fig2c": "WT+WT_tRNA",
    "Fig3b": "WT+NCCA_tRNA",
    "Fig3h": "Discriminator mutant",
    "Fig4b": "WT+NCCA_tRNA",
    "Fig4f": "WT+NCCA_tRNA",
    "Fig6b": "Linker mutant",
    "Fig7b": "RAG mutant",
}
#: Fig. 5b column (0-based) -> intraFRET trace matrix. Column 2 (5 uM WT tRNA) has no matrix: the
#: archive's ``intraFRET_WT+WT_5uM.txt`` is a byte copy of the 13 uM file.
INTRAFRET = {
    0: "intraFRET_WT_notRNA",
    4: "intraFRET_WT+WT_13uM",
    6: "intraFRET_WT+NCCA_5uM",
    8: "intraFRET_WT+NCCA_13uM",
}
INTRAFRET_FRAMES = 400  # Methods, "IntraFRET data analysis"
#: Representative-trace sheets (Figs. 2b, 3g, 4a, 4e): Cy3, Cy5 and FRET efficiency columns.
TRACE_SHEETS = ("Fig2b", "Fig3g", "Fig4a", "Fig4e")
#: Survival panel (sheet, first column) -> the exponential model the published curve is.
SURVIVAL = {
    ("Fig2e", 0): "double",
    ("Fig2e", 5): "single",
    ("Fig3d", 0): "single",
    ("Fig3e", 0): "double",
    ("Fig3j", 0): "single",
    ("Fig4d", 0): "single",
    ("Fig4h", 0): "single",
    ("Fig4h", 5): "single",
    ("Fig6d", 0): "single",
    ("Fig6e", 0): "single",
    ("Fig7d", 0): "double",
    ("Fig7e", 0): "single",
    ("Fig7f", 0): "single",
}
#: Double-exponential panel -> its parameter file of the archive (A1, A2, K1, K2 with errors).
DOUBLE_PARAMS = {
    ("Fig2e", 0): "Double-Wt+Wt-4",
    ("Fig3e", 0): "Double-Wt+NCCA-5",
    ("Fig7d", 0): "Double-RAG-4",
}
HIST_BINS = 151  # tMAVEN data_hist1d.py signal_nbins (the workbook gives no bins)
HIST_RANGE = (-0.25, 1.25)
#: Transition density sheets (Figs. 2d, 3c, 3i, 4c, 4g, 6c, 7c), drawn by the archive's checkTDP.py.
TDPS = ("Fig2d", "Fig3c", "Fig3i", "Fig4c", "Fig4g", "Fig6c", "Fig7c")
TDP_BINS = 50
TDP_RANGE = (-0.25, 1.25)
EPS = float(np.finfo(np.float64).eps)


# --- reading ------------------------------------------------------------------------------------


def read_book(archive: Path) -> dict[str, list[tuple]]:
    """Every sheet of the main-figure workbook, read from a temporary copy of the member."""
    with zipfile.ZipFile(archive) as zf, tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / WORKBOOK
        path.write_bytes(zf.read(WORKBOOK))
        return read_workbook(path)


def read_traces(archive: Path, name: str) -> tuple[np.ndarray, np.ndarray]:
    """``(donor, acceptor)`` of a trace matrix, each ``(frames, molecules)``; columns alternate."""
    with zipfile.ZipFile(archive) as zf:
        m = np.loadtxt(io.BytesIO(zf.read(f"smFRET_SourceData/{name}.txt")))
    return m[:, 0::2], m[:, 1::2]


def read_double_params(archive: Path, name: str) -> np.ndarray:
    """``A1, A1err, A2, A2err, K1, K1err, K2, K2err`` of one parameter file."""
    with zipfile.ZipFile(archive) as zf:
        return np.loadtxt(io.BytesIO(zf.read(f"{DOUBLE_DIR}/{name}.txt")))


def column(rows: list[tuple], header: str, col: int) -> np.ndarray:
    """The numbers below the first cell ``header`` of column ``col``, down to the first gap."""
    start = next(i for i, r in enumerate(rows) if len(r) > col and r[col] == header) + 1
    out = []
    for r in rows[start:]:
        v = r[col] if len(r) > col else None
        if not isinstance(v, float):
            break
        out.append(v)
    return np.asarray(out, dtype=np.float64)


# --- the two facts of the archive ---------------------------------------------------------------


def rounding_bound(donor: np.ndarray, acceptor: np.ndarray) -> np.ndarray:
    """Largest change of ``A / (D + A)`` that rounding ``D`` and ``A`` to 8 digits can cause.

    A value written as ``%.7e`` is off by at most half a unit in its 8th significant digit,
    ``h(x) = 0.5 * 10**(floor(log10|x|) - 7)``, so the unrounded intensities lie in the box
    ``[D - h(D), D + h(D)] x [A - h(A), A + h(A)]``. While ``D + A`` keeps its sign on that box,
    the efficiency is monotonic in ``D`` for each ``A`` and in ``A`` for each ``D``, so its extremes
    are at the four corners, and the bound is the largest corner difference (both denominators
    enter it). Where the interval of ``D + A`` contains zero there is no finite bound and the value
    is ``inf``: such a frame cannot be checked. Two machine epsilons of the efficiency are added for
    the division itself.
    """

    def half_unit(x: np.ndarray) -> np.ndarray:
        a = np.abs(x)
        return 0.5 * 10.0 ** (np.floor(np.log10(np.where(a > 0, a, 1.0))) - 7)

    hd, ha = half_unit(donor), half_unit(acceptor)
    with np.errstate(divide="ignore", invalid="ignore"):
        total = donor + acceptor
        e = acceptor / total
        worst = np.zeros(np.broadcast(donor, acceptor).shape)
        for sd in (-1.0, 1.0):
            for sa in (-1.0, 1.0):
                a = acceptor + sa * ha
                worst = np.maximum(worst, np.abs(a / (donor + sd * hd + a) - e))
        bound = worst + 2 * EPS * np.abs(e)
    # The sum D + A itself is off by a few epsilons of |D| + |A|; a margin of four keeps a box
    # whose edge touches zero on the unbounded side.
    margin = 4 * EPS * (np.abs(donor) + np.abs(acceptor))
    return np.where(np.abs(total) - (hd + ha) > margin, bound, np.inf)


def find_windows(
    e: np.ndarray, bound: np.ndarray, values: np.ndarray
) -> list[tuple[int, int, int]]:
    """Locate a pooled histogram sample in a trace matrix, as ``(molecule, first frame, frames)``.

    ``e`` and ``bound`` are ``(frames, molecules)``. From the first published value onward, each
    value is looked for in every frame of every molecule (within ``bound``), and the longest run of
    consecutive frames that matches the following values is one window. A value that is in no frame
    is ``(-1, -1, 1)``.
    """
    frames = e.shape[0]
    flat = e.T.ravel()
    flat_bound = bound.T.ravel()
    order = np.argsort(flat)
    ordered = flat[order]
    reach = float(np.max(flat_bound[np.isfinite(flat) & np.isfinite(flat_bound)]))
    windows: list[tuple[int, int, int]] = []
    pos = 0
    while pos < values.size:
        v = values[pos]
        lo, hi = np.searchsorted(ordered, [v - reach, v + reach])
        best = (-1, -1, 1)
        for k in order[lo:hi]:
            if not abs(flat[k] - v) <= flat_bound[k]:
                continue
            mol, first = divmod(int(k), frames)
            n = min(frames - first, values.size - pos)
            seg = slice(first, first + n)
            bad = ~(np.abs(e[seg, mol] - values[pos : pos + n]) <= bound[seg, mol])
            run = int(np.argmax(bad)) if bad.any() else n
            if run > best[2] or best[0] < 0:
                best = (mol, first, run)
        windows.append(best)
        pos += best[2]
    return windows


def dwells_from_survival(survival: np.ndarray, limit: int = 100_000) -> np.ndarray:
    """The dwell lengths (frames) whose survival curve is ``survival``, for the least event count.

    ``survival[i]`` is the fraction of the ``N`` dwells longer than ``i`` frames, so ``N *
    survival`` is a whole number for the true ``N``; the least such ``N`` is taken.
    """
    for n in range(1, limit + 1):
        counts = survival * n
        if np.max(np.abs(counts - np.round(counts))) < 1e-6:
            longer = np.round(counts).astype(np.int64)
            exact = np.append(longer[:-1] - longer[1:], longer[-1])
            return np.repeat(np.arange(1, survival.size + 1), exact)
    raise ValueError("no event count up to the limit gives whole numbers")


# --- the comparisons: each returns the deviations a test holds to its limit -----------------------


def compare_trace_sheet(rows: list[tuple]) -> dict:
    """Tether's efficiency beside each published representative trace of one sheet."""
    header = next(r for r in rows if "FRET Efficiency" in r)
    worst = 0.0
    n = 0
    for col, name in enumerate(header):
        if name != "FRET Efficiency":
            continue
        cy3, cy5, pub = (
            column(rows, h, c)
            for h, c in (("Intensity Cy3", col - 2), ("Intensity Cy5", col - 1), (name, col))
        )
        got = apparent_fret(cy3, cy5)
        dev = np.abs(got - pub) / np.maximum(np.abs(pub), EPS)
        # A non-finite efficiency (or published value) is a failure, not a value to skip.
        worst = max(worst, float(np.max(dev)) if np.all(np.isfinite(dev)) else np.inf)
        n += pub.size
    return {"values": n, "max_rel": worst}


def compare_histogram(values: np.ndarray, donor: np.ndarray, acceptor: np.ndarray) -> dict:
    """Tether's efficiency in each recovered window and Tether's histogram, beside the published."""
    e = apparent_fret(donor, acceptor)
    bound = rounding_bound(donor, acceptor)
    windows = find_windows(e, bound, values)
    pooled = []
    ratio = 0.0
    lost_end = 0
    for i, (mol, first, n) in enumerate(windows):
        if mol < 0:
            prev = windows[i - 1] if i else (-1, 0, 0)
            # WT+NCCA_tRNA.txt has 1499 frames; a window of 1500 loses its last frame.
            lost_end += int(prev[0] >= 0 and prev[1] + prev[2] == donor.shape[0])
            continue
        seg = slice(first, first + n)
        pooled.append(e[seg, mol])
    pos = 0
    kept = []
    unbounded = 0
    for mol, first, n in windows:
        if mol >= 0:
            seg = slice(first, first + n)
            dev = np.abs(e[seg, mol] - values[pos : pos + n]) / bound[seg, mol]
            ratio = max(ratio, float(np.max(dev)))
            unbounded += int(np.sum(np.isinf(bound[seg, mol])))
            kept.append(values[pos : pos + n])
        pos += n
    tether = apparent_e_histogram(np.concatenate(pooled), density=False)
    # The reference binning is tMAVEN's data_hist1d default, by numpy and not by Tether.
    published, _ = np.histogram(np.concatenate(kept), bins=HIST_BINS, range=HIST_RANGE)
    return {
        "values": int(values.size),
        "windows": sum(1 for w in windows if w[0] >= 0),
        "molecules": len({w[0] for w in windows if w[0] >= 0}),
        "not_found": sum(1 for w in windows if w[0] < 0),
        "not_found_after_last_frame": lost_end,
        "max_bound_ratio": ratio,
        "unbounded_values": unbounded,
        "bins_differing": int(np.sum(tether.counts != published)),
    }


def compare_intrafret(values: np.ndarray, donor: np.ndarray, acceptor: np.ndarray) -> dict:
    """Tether's efficiency of the first 400 frames of each molecule beside Fig. 5b.

    The published notebook reads each movie's file with ``skiprows=1``; the archive's matrix joins
    the files, so for each molecule the window starts at frame 0 or at frame 1. Each published block
    of 400 values is matched with one of the two.
    """
    e = apparent_fret(donor, acceptor)
    bound = rounding_bound(donor, acceptor)
    blocks = values.reshape(-1, INTRAFRET_FRAMES)
    starts = []
    ratio = 0.0
    unbounded = 0
    for mol, block in enumerate(blocks):
        found = None
        for first in (0, 1):
            seg = slice(first, first + INTRAFRET_FRAMES)
            dev = float(np.max(np.abs(e[seg, mol] - block) / bound[seg, mol]))
            if dev <= 1.0 and found is None:
                found, best = first, dev
                unbounded += int(np.sum(np.isinf(bound[seg, mol])))
        starts.append(found)
        if found is not None:
            ratio = max(ratio, best)
    groups: list[list] = []
    for s in starts:
        if groups and groups[-1][0] == s:
            groups[-1][1] += 1
        else:
            groups.append([s, 1])
    return {
        "molecules": int(blocks.shape[0]),
        "matrix_molecules": int(e.shape[1]),
        "start_groups": groups,
        "max_bound_ratio": ratio,
        "unbounded_values": unbounded,
    }


def compare_survival(rows: list[tuple], col: int, model: str, params: np.ndarray | None) -> dict:
    """Tether's survival curve and exponential fit beside one published survival panel."""
    t = column(rows, "Time (s)", col)
    surv = column(rows, "Survival probability", col + 1)
    curve = column(rows, "Model", col + 2)
    residual = column(rows, "Residual", col + 3)
    n = min(t.size, surv.size, curve.size)
    t, surv, curve, residual = t[:n], surv[:n], curve[:n], residual[:n]
    lengths = dwells_from_survival(surv)
    tau, s_tether = survival_curve(lengths)
    fit = fit_survival(tau * DT_S, s_tether, model=model)
    out = {
        "events": int(lengths.size),
        "points": n,
        "survival_max_abs": float(np.max(np.abs(s_tether - surv))),
        "time_max_rel": float(np.max(np.abs(tau * DT_S - t)) / t[-1]),
        "model_max_rel": float(np.max(np.abs(fit.model_survival - curve)) / curve[0]),
        "residual_max_rel": float(np.max(np.abs(fit.residuals - residual)) / curve[0]),
    }
    if params is not None:
        a1, a1e, a2, a2e, k1, k1e, k2, k2e = params
        got = np.r_[fit.amplitudes, fit.rates]
        ref = np.array([a1, a2, k1, k2])
        out["params_max_rel"] = float(np.max(np.abs(got - ref) / ref))
        got_err = np.r_[fit.amplitude_stderr, fit.rate_stderr]
        # tMAVEN's optimize_double_surv sorts the rates and amplitudes but not their errors.
        swapped = np.array([a2e, a1e, k2e, k1e])
        out["errors_swapped_max_rel"] = float(np.max(np.abs(got_err - swapped) / swapped))
        labelled = np.array([a1e, a2e, k1e, k2e])
        out["errors_as_labelled_max_rel"] = float(np.max(np.abs(got_err - labelled) / labelled))
    return out


def compare_tdp(rows: list[tuple]) -> dict:
    """Tether's transition density counts beside ``checkTDP.py``'s histogram of one sheet."""
    initial = column(rows, "Initial E_FRET", 0)
    final = column(rows, "Final E_FRET", 1)
    # One idealized two-frame path per published transition: Tether reads (v[t], v[t + 1]).
    tdp = transition_density(
        [np.array([a, b]) for a, b in zip(initial, final, strict=True)],
        nskip=1,
        signal_bins=TDP_BINS,
        signal_range=TDP_RANGE,
    )
    edges = np.linspace(TDP_RANGE[0], TDP_RANGE[1], TDP_BINS + 1)  # checkTDP.py's x_edges
    ref, _, _ = np.histogram2d(initial, final, bins=[edges, edges])
    return {
        "transitions": int(initial.size),
        "tether_transitions": tdp.n_transitions,
        "counts_differing": int(np.sum(tdp.counts != ref)),
        "edges_equal": bool(np.array_equal(tdp.signal_edges, edges)),
    }


def measure(archive: Path) -> dict:
    """Every comparison of this module on one archive, by panel."""
    book = read_book(archive)
    out: dict = {"traces": {}, "histograms": {}, "intrafret": {}, "survival": {}, "tdp": {}}
    for sheet in TRACE_SHEETS:
        out["traces"][sheet] = compare_trace_sheet(book[sheet])
    cache: dict[str, tuple] = {}
    for sheet, name in HISTOGRAMS.items():
        cache.setdefault(name, read_traces(archive, name))
        values = column(book[sheet], "FRET Efficiency", 0)
        out["histograms"][sheet] = compare_histogram(values, *cache[name])
    for col, name in INTRAFRET.items():
        values = column(book["Fig5b"], "FRET Efficiency", col)
        out["intrafret"][name] = compare_intrafret(values, *read_traces(archive, name))
    for (sheet, col), model in SURVIVAL.items():
        name = DOUBLE_PARAMS.get((sheet, col))
        params = read_double_params(archive, name) if name else None
        out["survival"][f"{sheet}:{col}"] = compare_survival(book[sheet], col, model, params)
    for sheet in TDPS:
        out["tdp"][sheet] = compare_tdp(book[sheet])
    return out


if __name__ == "__main__":
    print(json.dumps(measure(Path(sys.argv[1])), indent=1))
