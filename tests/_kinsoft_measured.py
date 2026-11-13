# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The measured kinSoftChallenge data sets and the published results of the challenge (#718).

The kinSoft record (Zenodo 5701310) holds five measured data sets that the simulated-level oracle
of ``test_kinsoft_kinetics.py`` does not use: the two sets of Fig. 2 of Götz et al. 2022 (ACTR and
NCBD binding, confocal, 10 ms and 1 ms bins) and the three sets of its Fig. 5 (SSB binding to a DNA
hairpin, TIRF, 100 ms frames). ``reference_data/kinsoft_published_results.json`` holds the rate
matrices the analyses of the challenge reported for them, copied from the paper's Supplementary
Data, and Tether's own frozen result. ADR-0081 records the choices.

Measured data has no ground truth, so the comparison is a position, never a pass or a fail: the
exit rate of each FRET class from Tether's fit beside the same quantity from every analysis.

* :func:`read_measured_set` reads one set from its Zenodo archive (columns time, donor, acceptor).
* :func:`class_exit_rates` turns a reported rate matrix into the exit rate of the low-FRET and the
  high-FRET class, ``1 / mean residence time``. For a 2-state model that is the matrix's own two
  rates; for a model with hidden states it is the rate of the aggregated class.
* :func:`tether_exit_rates` fits the set with the tMAVEN sidecar (consensus VB-HMM, two states)
  and pools the dwells of the Viterbi paths, as ``tether.analysis.kinetics.pooled_exit_rates`` does.
* :func:`position` says where a value lies among the published values.
* :func:`read_workbook` and :func:`transcribe_sheet` read a sheet of the paper's spreadsheets the
  way the copy was made, with the standard library only (openpyxl is in no lock), so the weekly
  job can compare each copied number with its cell.
"""

from __future__ import annotations

import fnmatch
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

import numpy as np

RESULTS = Path(__file__).resolve().parent / "reference_data" / "kinsoft_published_results.json"
#: The fields of an analysis that are copied from its sheet.
COPIED = ("fret_e", "rates_s_inv", "uncertainty_plus", "uncertainty_minus")
#: The two FRET classes, low then high, in every result of this module.
CLASSES = ("low", "high")


def load_results(path: Path = RESULTS) -> dict:
    """The published-results file as a dict."""
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass(frozen=True, eq=False)
class MeasuredTrace:
    """One measured trace: background-corrected donor and acceptor intensities."""

    name: str
    donor: np.ndarray
    acceptor: np.ndarray


def read_measured_set(
    archive: Path, members: str, time_unit_s: float
) -> tuple[list[MeasuredTrace], float]:
    """The traces of one set, in member-name order, and the frame time in seconds.

    ``members`` is an :func:`fnmatch.fnmatch` pattern on the archive member names; the macOS
    resource copies (``__MACOSX/``) and the read-me files never match it. Each member is a
    whitespace-separated table of time, donor and acceptor; ``time_unit_s`` converts its time
    column to seconds. Every trace of a set must have the same frame time.
    """
    traces: list[MeasuredTrace] = []
    frame_times: set[float] = set()
    with zipfile.ZipFile(archive) as zf:
        names = sorted(
            n for n in zf.namelist() if fnmatch.fnmatch(n, members) and not n.startswith("__")
        )
        for name in names:
            table = np.loadtxt(zf.open(name), ndmin=2)
            if table.shape[1] != 3 or table.shape[0] < 2:
                raise ValueError(f"{name}: expected 3 columns and 2 rows, got {table.shape}")
            frame_times.add(round(float(table[1, 0] - table[0, 0]) * time_unit_s, 9))
            traces.append(MeasuredTrace(name, table[:, 1], table[:, 2]))
    if not traces:
        raise ValueError(f"no member of {archive.name} matches {members!r}")
    if len(frame_times) != 1:
        raise ValueError(f"the traces of {members!r} have frame times {sorted(frame_times)}")
    return traces, frame_times.pop()


def fret_classes(fret_e: list[float]) -> np.ndarray:
    """Class of each state, 0 = low and 1 = high FRET, split at the largest gap in FRET E."""
    e = np.asarray(fret_e, dtype=float)
    if e.size < 2 or np.isnan(e).any():
        raise ValueError(f"need two or more FRET efficiencies, got {fret_e}")
    ranked = np.sort(e)
    cut = ranked[int(np.argmax(np.diff(ranked)))]
    return (e > cut).astype(int)


def class_exit_rates(fret_e: list[float], rates_s_inv: list[list[float]]) -> dict[str, float]:
    """Exit rate (1/s) of the low- and high-FRET class of a reported kinetic model.

    ``rates_s_inv[i][j]`` is the rate from state ``i`` to state ``j`` (the convention of the
    challenge, Supplementary Data 4). The model is a continuous-time Markov chain with generator
    ``Q``. A class ``A`` is entered from outside it with the stationary flux
    ``e_j ∝ Σ_{i∉A} π_i Q_ij``, and its mean residence time is ``e · (−Q_AA)⁻¹ · 1``; the exit rate
    is the reciprocal. For two states this is ``k12`` for the low and ``k21`` for the high class.
    """
    q = np.array(rates_s_inv, dtype=float)
    n = len(fret_e)
    if q.shape != (n, n) or (q < 0).any():
        raise ValueError(f"rate matrix must be {n}x{n} and non-negative")
    np.fill_diagonal(q, 0.0)
    np.fill_diagonal(q, -q.sum(axis=1))
    # Stationary distribution: pi Q = 0, sum(pi) = 1.
    lhs = np.vstack([q.T, np.ones(n)])
    rhs = np.zeros(n + 1)
    rhs[-1] = 1.0
    pi, *_ = np.linalg.lstsq(lhs, rhs, rcond=None)
    if not np.allclose(pi @ q, 0.0, atol=1e-9) or (pi < -1e-12).any():
        raise ValueError("the rate matrix has no unique stationary distribution")
    classes = fret_classes(fret_e)
    out: dict[str, float] = {}
    for c, label in enumerate(CLASSES):
        inside = np.flatnonzero(classes == c)
        outside = np.flatnonzero(classes != c)
        entry = pi[outside] @ q[np.ix_(outside, inside)]
        if entry.sum() <= 0:
            raise ValueError(f"the {label}-FRET class is never entered")
        entry = entry / entry.sum()
        residence = entry @ np.linalg.solve(-q[np.ix_(inside, inside)], np.ones(inside.size))
        out[label] = float(1.0 / residence)
    return out


def published_exit_rates(analyses: list[dict]) -> dict[str, dict[str, float]]:
    """``{sheet: {"low": k, "high": k}}`` for every analysis the comparison includes.

    An analysis is left out when it reported no FRET efficiencies (its states cannot be put in a
    class) or no rate matrix; ``excluded`` in the file says which and why.
    """
    return {
        a["sheet"]: class_exit_rates(a["fret_e"], a["rates_s_inv"])
        for a in analyses
        if "excluded" not in a
    }


def position(value: float, published: list[float]) -> dict:
    """Where ``value`` lies among ``published``: the count below it, the range and the median."""
    values = np.sort(np.asarray(published, dtype=float))
    return {
        "n": int(values.size),
        "below": int(np.sum(values < value)),
        "min": float(values[0]),
        "median": float(np.median(values)),
        "max": float(values[-1]),
        "inside_range": bool(values[0] <= value <= values[-1]),
    }


def tether_exit_rates(
    traces: list[MeasuredTrace], dt: float, workdir: Path, *, seed: int = 0
) -> dict[str, float]:
    """Tether's 2-state fit of a set: FRET levels, exit rates (1/s) and pooled dwell counts.

    The traces go to an SMD file with each trace's length as its analysis window, the sidecar
    runs the consensus VB-HMM with two states and the given seed, and the Viterbi paths give the
    pooled exit rate ``1/⟨τ⟩`` of each state (first and last dwell of each trace dropped).
    """
    from tether.analysis.dwell import state_dwells
    from tether.analysis.kinetics import pooled_exit_rates
    from tether.idealize import NO_STATE, run_vbconhmm, write_smd

    length = max(t.donor.size for t in traces)
    raw = np.zeros((len(traces), length, 2))
    for i, t in enumerate(traces):
        raw[i, : t.donor.size, 0] = t.donor
        raw[i, : t.donor.size, 1] = t.acceptor
    post = np.array([t.donor.size for t in traces], dtype=np.int64)
    smd = write_smd(workdir / "set.smd.hdf5", raw, pre_list=np.zeros_like(post), post_list=post)
    result = run_vbconhmm(smd, nstates=2, seed=seed, timeout=None)
    rank = np.argsort(np.argsort(result.model.means))
    paths = []
    for i in sorted(result.state_paths):
        p = result.state_paths[i]
        paths.append(rank[p[p != NO_STATE]])
    rates = pooled_exit_rates(paths, dt)
    dwells = state_dwells(paths, no_state=-1)
    means = np.sort(result.model.means)
    return {
        "fret_low": float(means[0]),
        "fret_high": float(means[1]),
        "k_low": float(rates[0]),
        "k_high": float(rates[1]),
        "n_dwells_low": int(dwells.lengths[0].size),
        "n_dwells_high": int(dwells.lengths[1].size),
    }


_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PACKAGE = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _column(ref: str) -> int:
    """1-based column number of a cell reference such as ``AB12``."""
    letters = re.match(r"[A-Z]+", ref).group(0)
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - ord("A") + 1
    return n


def read_workbook(path: Path) -> dict[str, list[tuple]]:
    """The non-empty rows of every sheet of an ``.xlsx`` file, by sheet name, in workbook order.

    Each row runs from column A to the last used column of its sheet; a missing cell is ``None``,
    a shared or inline string is a ``str`` and a number is a ``float``. The cached value of a
    formula cell is read, as ``openpyxl.load_workbook(data_only=True)`` does.
    """
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        strings: list[str] = []
        if "xl/sharedStrings.xml" in names:
            for si in ElementTree.fromstring(zf.read("xl/sharedStrings.xml")).iter(f"{_MAIN}si"):
                strings.append("".join(t.text or "" for t in si.iter(f"{_MAIN}t")))
        rels = {
            r.get("Id"): r.get("Target")
            for r in ElementTree.fromstring(zf.read("xl/_rels/workbook.xml.rels")).iter(
                f"{_PACKAGE}Relationship"
            )
        }
        book: dict[str, list[tuple]] = {}
        for sheet in ElementTree.fromstring(zf.read("xl/workbook.xml")).iter(f"{_MAIN}sheet"):
            target = rels[sheet.get(f"{_REL}id")]
            part = target.lstrip("/") if target.startswith("/") else f"xl/{target}"
            cells: dict[tuple[int, int], object] = {}
            for row in ElementTree.fromstring(zf.read(part)).iter(f"{_MAIN}row"):
                for c in row.iter(f"{_MAIN}c"):
                    kind, v = c.get("t"), c.find(f"{_MAIN}v")
                    if kind == "inlineStr":
                        value: object = "".join(t.text or "" for t in c.iter(f"{_MAIN}t"))
                    elif v is None or v.text is None:
                        continue
                    elif kind == "s":
                        value = strings[int(v.text)]
                    elif kind in ("str", "e"):
                        value = v.text
                    elif kind == "b":
                        value = v.text == "1"
                    else:
                        value = float(v.text)
                    cells[(int(row.get("r")), _column(c.get("r")))] = value
            width = max((col for _, col in cells), default=0)
            book[sheet.get("name")] = [
                row
                for row in (
                    tuple(cells.get((r, col)) for col in range(1, width + 1))
                    for r in range(1, max((r for r, _ in cells), default=0) + 1)
                )
                if any(v is not None for v in row)
            ]
    return book


def transcribe_sheet(rows: list[tuple]) -> dict:
    """The copied fields of one analysis sheet, by the rule the copy was made with.

    Row 1 names the ``n`` states; the row after it is ``FRET E``. Each of the labelled blocks
    ``kinetic model`` and the two ``uncertainty rates`` blocks is the ``n`` rows that start at its
    label, columns B onwards. A row with no value is ``None``, and so is a block or a FRET row with
    no value at all.
    """
    if rows[1][0] != "FRET E":
        raise ValueError(f"row 2 is {rows[1][0]!r}, not 'FRET E'")
    n = sum(v is not None for v in rows[0][1:])

    def numbers(row: tuple) -> list[float | None] | None:
        vals = [None if v is None else float(v) for v in row[1 : n + 1]]
        vals += [None] * (n - len(vals))
        return None if all(v is None for v in vals) else vals

    def block(label: str) -> list | None:
        (start,) = [i for i, r in enumerate(rows) if r[0] == label]
        out = [numbers(r) for r in rows[start : start + n]]
        return None if all(o is None for o in out) else out

    return {
        "fret_e": numbers(rows[1]),
        "rates_s_inv": block("kinetic model"),
        "uncertainty_plus": block("uncertainty rates (as in k+Δu)"),
        "uncertainty_minus": block("uncertainty rates (as in k-Δv)"),
    }
