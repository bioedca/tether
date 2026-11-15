# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""A test-only reader for MASH-FRET project files (``.mash``) (#711, ADR-0087).

A MASH-FRET project file is a MATLAB v5 file, so :func:`scipy.io.loadmat` reads it and no MATLAB
run is needed. This reader returns the arrays and the correction parameters that the MASH-FRET
reference tests (``tests/test_mashfret_reference.py``) need, and says what each one means. It is
test code: Tether ships no MASH-FRET reader (PRD §2.2, "What the tests can use").

The meaning of each field comes from the MASH-FRET source at the commit that wrote the reference
files, ``1a1f465983c59cb513459b10c19474fded317af5`` (its version text is "1.1.2 (prev. commit:
1a1f465)"), and from its page ``docs/output-files/mash-mash-project.md`` at that commit. Where
the page and the source disagree, the source wins, because the source wrote the file:

``intensities``, ``intensities_bgCorr``, ``intensities_crossCorr``, ``intensities_denoise``
    ``L``-by-``nC*M``-by-``nL`` arrays: frame, then channel ``c`` of molecule ``m`` in column
    ``m*nC + c`` (0-based), then laser. The page says ``L``-by-``nC*M``; the third axis (one
    slice for each laser, in the order of ``excitations``) is in the files and in the source.
    ``bgCorr`` is after background subtraction, ``crossCorr`` after the cross-talk correction
    (``source/mod-trace-processing/cross-talk/crossCorr.m``), ``denoise`` after smoothing.
``excitations``
    The laser wavelengths in nm, one for each slice of the third axis.
``chanExc``
    For each channel, the wavelength of the laser that excites its emitter directly, 0 for none.
    The page calls these laser indexes; ``crossCorr.m`` compares them with ``excitations``
    (``find(exc==chanExc(c))``), so they are wavelengths.
``FRET``
    ``nF``-by-2 donor and acceptor channel numbers (1-based in the file, 0-based here).
``fix{4}{1}`` — the bleedthrough (leakage) coefficients
    ``nC``-by-``(nC-1)``: row ``c`` is the channel whose photons leak, column ``n`` the ``n``-th
    *other* channel in increasing order. ``crossCorr.m`` does
    ``I_corr(:,c2,:) = I_corr(:,c2,:) - coeff*I_bgCorr(:,c,:)`` on every laser, so for a donor in
    channel 0 and an acceptor in channel 1, ``leakage[0, 0]`` is Tether's α in
    ``I_A,corr = I_A − α·I_D`` (Tether's additive convention, :mod:`tether.fret.efficiency`).
``fix{4}{2}`` — the direct-excitation coefficients
    ``(nL-1)``-by-``nC``. An ALEX-only correction (the acceptor excited by the donor laser); Tether
    has no such term (δ = 0, PRD §7.4), so the tests require it to be 0.
``prmTT{m}{6}{1}`` — the factor corrections of molecule ``m``
    2-by-``nF``: row 1 is γ, row 2 is β, one column for each FRET pair
    (``source/project/setDefPrm_traces.m``). ``prmTT{m}{6}{2}`` is the γ method (0 manual,
    1 photobleaching, 2 linear regression).
``prmTT{m}{4}{1}`` — the discretisation settings of molecule ``m``
    ``[method, apply-to, recalculate]``. With recalculate = 1, ``getDiscr.m`` replaces each state
    value by the mean of the trace over the frames that have that value (``aveStates``).
``FRET_DTA``
    ``L``-by-``nF*M``: the discretised FRET of pair ``p`` of molecule ``m`` in column
    ``m*nF + p``. ``discrTraces.m`` discretises the γ-corrected FRET of
    ``source/mod-trace-processing/FRET/calcFRET.m``, which for two emitters is
    ``1 / (1 + γ·I_DD / I_AD)`` (``buildFretExpr.m``) from the ``denoise`` intensities at the
    donor's laser, with NaN set to 0. The file holds no FRET value for each frame.
``bool_intensities``
    ``L``-by-``M``: the frames that are included (``discrTraces.m`` uses only these).

``prmThm{t+1, k}`` — the Histogram-analysis results (#712)
    One cell for each molecule tag (row 1 is "all molecules", row ``t + 1`` the ``t``-th tag of
    ``molTagNames``) and each data type. The data type of the first FRET pair is
    ``k = 2*nC*nL + 2*nDE + 1``, after the intensities, their discretised copies and the total
    intensities (``ud_thmPlot.m``). ``plot{1}`` is ``[bin, low, high, overflow]``; ``plot{2}`` is
    ``getHist.m``'s ``[centres, counts, cumulative counts]``: MATLAB's ``hist`` of the γ-corrected
    FRET of the ``denoise`` intensities over the included frames of the tag's molecules, with the
    first and the last bin, which collect the overflow, removed when ``overflow`` is 0.
    ``thm_res{2,1}`` is the Gaussian fit (``gauss_ana.m``): one row ``[A, σ(A), μ, σ(μ), FWHM,
    σ(FWHM), population, σ(population)]`` for each Gaussian, the mean and SD over the bootstrap
    samples.
``prmTDP{t+1, k}`` — the Transition-analysis results (#712)
    ``clst_res{3}`` is the number of clusters chosen and ``clst_res{1}.clusters{J}`` the dwell
    table of that choice (``clustTrans.m``): one row ``[dwell time in s, value before, value after,
    molecule, x, y, j1, j2]`` for each dwell that ends in a transition, ``j1 → j2`` its cluster's
    states (0 if no cluster took it). ``kin_res``, the fitted rates, is empty in every file of the
    record, so the record holds no MASH-FRET rate.

Only the layout of that generation of MASH-FRET is read: ``fix`` and ``prmTT`` at the top level.
A later layout (a ``TP`` structure, as in the MASH-FRET 1.3.4 file of the same record) raises
:class:`UnsupportedProject`, so a test never reads a field from the wrong place.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import scipy.io as sio

__all__ = [
    "MashProject",
    "TagAnalysis",
    "UnsupportedProject",
    "read_project",
    "read_tag_analysis",
    "stored_histogram_tags",
]

_COMMIT = re.compile(r"prev\. commit: ([0-9a-f]{7,40})")


class UnsupportedProject(ValueError):
    """A project file whose layout this reader does not know."""


@dataclass(frozen=True)
class MashProject:
    """The fields of one MASH-FRET project that the reference tests use (see the module text)."""

    version: str
    commit: str | None
    n_channels: int
    excitations: np.ndarray  # (nL,) laser wavelengths, nm
    chan_exc: np.ndarray  # (nC,) wavelength of each channel's direct laser, 0 for none
    labels: tuple[str, ...]
    fret_pairs: np.ndarray  # (nF, 2) donor, acceptor channel, 0-based
    leakage: np.ndarray  # (nC, nC-1) fix{4}{1}
    direct_excitation: np.ndarray  # (nL-1, nC) fix{4}{2}
    intensities: np.ndarray | None  # (L, nC*M, nL) raw; None in the committed extract
    background_corrected: np.ndarray  # (L, nC*M, nL)
    crosstalk_corrected: np.ndarray  # (L, nC*M, nL)
    denoised: np.ndarray  # (L, nC*M, nL)
    fret_dta: np.ndarray  # (L, nF*M)
    included: np.ndarray  # (L, M) bool
    gamma: np.ndarray  # (M, nF)
    beta: np.ndarray  # (M, nF)
    gamma_method: np.ndarray  # (M, nF) 0 manual, 1 photobleaching, 2 linear regression
    dta_settings: np.ndarray  # (M, 3) method, apply-to, recalculate

    @property
    def n_molecules(self) -> int:
        return self.included.shape[1]

    @property
    def n_pairs(self) -> int:
        return self.fret_pairs.shape[0]

    def laser_of(self, channel: int) -> int:
        """The slice of the third axis that holds *channel*'s emitter under its own laser."""
        hits = np.flatnonzero(self.excitations == self.chan_exc[channel])
        if hits.size == 0:
            raise ValueError(f"channel {channel} has no emitter-specific laser")
        return int(hits[0])

    def trace(self, array: np.ndarray, molecule: int, channel: int, laser: int) -> np.ndarray:
        """One intensity trace of *array* (one of the four intensity arrays)."""
        return array[:, molecule * self.n_channels + channel, laser]


def _cell(value: object) -> np.ndarray:
    """A MATLAB cell (an object array) as a flat 1-D object array."""
    return np.asarray(value, dtype=object).ravel()


def _num(value: object) -> np.ndarray:
    return np.asarray(value, dtype=np.float64)


def _text(value: object) -> str:
    arr = np.asarray(value).ravel()
    return str(arr[0]) if arr.size else ""


def _intensity(raw: dict, name: str, n_lasers: int) -> np.ndarray:
    arr = _num(raw[name])
    if arr.ndim == 2:  # MATLAB drops a trailing axis of length 1 (one laser)
        arr = arr[:, :, None]
    if arr.ndim != 3 or arr.shape[2] != n_lasers:
        raise UnsupportedProject(f"{name} has the shape {arr.shape}, not L x nC*M x {n_lasers}")
    return arr


def read_project(path: Path | str) -> MashProject:
    """Read the fields of a MASH-FRET project file that the reference tests use."""
    raw = sio.loadmat(str(path), squeeze_me=False, struct_as_record=False)
    if "prmTT" not in raw or "fix" not in raw:
        layout = "a TP structure" if "TP" in raw else "no prmTT or fix"
        raise UnsupportedProject(
            f"{Path(path).name}: {layout}; this reader knows only the top-level fields"
        )

    version = _text(raw["MASH_version"])
    commit = _COMMIT.search(version)
    n_channels = int(_num(raw["nb_channel"]).ravel()[0])
    excitations = _num(raw["excitations"]).ravel()
    n_lasers = int(_num(raw["nb_excitations"]).ravel()[0])
    if excitations.size != n_lasers:
        raise UnsupportedProject(f"{excitations.size} excitations for {n_lasers} lasers")
    chan_exc = _num(raw["chanExc"]).ravel()[:n_channels]
    labels = tuple(_text(x) for x in _cell(raw["labels"])[:n_channels])
    fret_pairs = _num(raw["FRET"]).reshape(-1, 2).astype(int) - 1

    factors = _cell(_cell(raw["fix"])[3])  # fix{4} = {bleedthrough, direct excitation}
    leakage = _num(factors[0]).reshape(n_channels, max(n_channels - 1, 0))
    direct = _num(factors[1]).reshape(max(n_lasers - 1, 0), n_channels)

    arrays = {
        name: _intensity(raw, name, n_lasers)
        for name in (
            "intensities",
            "intensities_bgCorr",
            "intensities_crossCorr",
            "intensities_denoise",
        )
        if name in raw or name != "intensities"  # the committed extract leaves the raw out
    }
    included = _num(raw["bool_intensities"]).astype(bool)
    n_molecules = included.shape[1]
    if any(a.shape[1] != n_channels * n_molecules for a in arrays.values()):
        raise UnsupportedProject("the intensity columns are not nC x the molecule count")
    fret_dta = _num(raw["FRET_DTA"])
    n_pairs = fret_pairs.shape[0]
    if fret_dta.shape[1] != n_pairs * n_molecules:
        raise UnsupportedProject("the FRET_DTA columns are not nF x the molecule count")

    prm = _cell(raw["prmTT"])
    if prm.size != n_molecules:
        raise UnsupportedProject(f"{prm.size} prmTT entries for {n_molecules} molecules")
    gamma = np.empty((n_molecules, n_pairs))
    beta = np.empty((n_molecules, n_pairs))
    method = np.empty((n_molecules, n_pairs))
    dta = np.empty((n_molecules, 3))
    for m, entry in enumerate(prm):
        cells = _cell(entry)
        factor = _cell(cells[5])  # prmTT{m}{6}
        gb = _num(factor[0]).reshape(2, n_pairs)
        gamma[m], beta[m] = gb[0], gb[1]
        method[m] = _num(factor[1]).ravel()[:n_pairs]
        dta[m] = _num(_cell(cells[3])[0]).ravel()[:3]  # prmTT{m}{4}{1}

    return MashProject(
        version=version,
        commit=commit.group(1) if commit else None,
        n_channels=n_channels,
        excitations=excitations,
        chan_exc=chan_exc,
        labels=labels,
        fret_pairs=fret_pairs,
        leakage=leakage,
        direct_excitation=direct,
        intensities=arrays.get("intensities"),
        background_corrected=arrays["intensities_bgCorr"],
        crosstalk_corrected=arrays["intensities_crossCorr"],
        denoised=arrays["intensities_denoise"],
        fret_dta=fret_dta,
        included=included,
        gamma=gamma,
        beta=beta,
        gamma_method=method,
        dta_settings=dta,
    )


@dataclass(frozen=True)
class TagAnalysis:
    """MASH-FRET's analysis of one molecule tag, and the traces it was made from (#712)."""

    tag: int  # 0 = all molecules, t = the t-th tag (1-based, as in molTagNames)
    molecules: np.ndarray  # (n,) project indexes of the tag's included molecules
    donor: list[np.ndarray]  # denoised donor at the donor's laser, included frames
    acceptor: list[np.ndarray]  # the same for the acceptor
    gamma: np.ndarray  # (n,) γ of the first FRET pair
    states: list[np.ndarray]  # FRET_DTA over the same frames
    frame_time: float  # s, MASH-FRET's frame_rate field
    bin_width: float
    centres: np.ndarray  # MASH-FRET's bin centres, overflow bins removed
    counts: np.ndarray  # MASH-FRET's histogram counts
    gaussians: np.ndarray | None  # thm_res{2,1}: (J, 8), or None if no fit is stored
    dwells: np.ndarray | None  # clst_res{1}.clusters{J}: (n, 8), or None


def _load(path: Path | str) -> dict:
    raw = sio.loadmat(str(path), squeeze_me=True, struct_as_record=False)
    if "prmThm" not in raw or "prmTT" not in raw:
        layout = "a TP structure" if "TP" in raw else "no prmThm or prmTT"
        raise UnsupportedProject(f"{Path(path).name}: {layout}")
    return raw


def _fret_type(raw: dict) -> int:
    """0-based column of the first FRET pair in prmThm (``ud_thmPlot.m``'s list of types)."""
    n_channels = int(raw["nb_channel"])
    n_lasers = int(raw["nb_excitations"])
    n_direct = int(np.count_nonzero(np.atleast_1d(raw["chanExc"])[:n_channels]))
    return 2 * n_channels * n_lasers + 2 * n_direct


def _filled(cell: object) -> bool:
    return hasattr(cell, "plot") and len(cell.plot) > 1 and np.size(cell.plot[1]) > 0


def stored_histogram_tags(path: Path | str) -> list[int]:
    """The tags (0 = all molecules) for which the project stores a FRET histogram."""
    raw = _load(path)
    column = raw["prmThm"][:, _fret_type(raw)]
    return [t for t, cell in enumerate(column) if _filled(cell)]


def read_tag_analysis(path: Path | str, tag: int) -> TagAnalysis:
    """MASH-FRET's FRET histogram, Gaussian fit and dwell table of *tag*, with its traces."""
    raw = _load(path)
    cell = raw["prmThm"][tag, _fret_type(raw)]
    if not _filled(cell):
        raise ValueError(f"{Path(path).name}: no FRET histogram is stored for tag {tag}")
    bin_width, _, _, overflow = np.asarray(cell.plot[0], dtype=np.float64)
    if overflow:
        raise UnsupportedProject("a histogram that keeps its overflow bins")
    table = np.atleast_2d(np.asarray(cell.plot[1], dtype=np.float64))
    fit = np.asarray(cell.thm_res, dtype=object)[1, 0]
    gaussians = np.atleast_2d(np.asarray(fit, dtype=np.float64)) if np.size(fit) else None

    dwells = None
    # prmTDP is absent where no Transition analysis was saved, and short of rows for tags made
    # after it was.
    transition = raw.get("prmTDP")
    has_row = transition is not None and tag < transition.shape[0]
    for tdp in np.atleast_1d(transition[tag] if has_row else []):
        if hasattr(tdp, "clst_res"):
            chosen = int(tdp.clst_res[2])
            dwells = np.atleast_2d(np.asarray(tdp.clst_res[0].clusters[chosen - 1], float))

    n_channels = int(raw["nb_channel"])
    excitations = np.atleast_1d(raw["excitations"]).astype(np.float64)
    chan_exc = np.atleast_1d(raw["chanExc"]).astype(np.float64)[:n_channels]
    pairs = np.atleast_2d(raw["FRET"]).astype(int) - 1
    donor, acceptor = pairs[0]
    laser = int(np.flatnonzero(excitations == chan_exc[donor])[0])
    denoised = np.asarray(raw["intensities_denoise"], dtype=np.float64)
    if denoised.ndim == 2:
        denoised = denoised[:, :, None]
    n_frames, n_columns = denoised.shape[:2]
    n_molecules = n_columns // n_channels
    included = np.asarray(raw["bool_intensities"]).astype(bool).reshape(n_frames, n_molecules)
    selected = np.atleast_1d(raw["coord_incl"]).astype(bool)
    if tag:
        tags = np.asarray(raw["molTag"]).astype(bool).reshape(n_molecules, -1)
        selected = selected & tags[:, tag - 1]
    molecules = np.flatnonzero(selected)
    prm = np.atleast_1d(raw["prmTT"])
    n_pairs = pairs.shape[0]
    gamma = np.array(
        [np.asarray(prm[m][5][0], dtype=np.float64).reshape(2, n_pairs)[0, 0] for m in molecules]
    )
    dta = np.asarray(raw["FRET_DTA"], dtype=np.float64).reshape(n_frames, -1)
    frames = [included[:, m] for m in molecules]
    return TagAnalysis(
        tag=tag,
        molecules=molecules,
        donor=[
            denoised[f, m * n_channels + donor, laser]
            for m, f in zip(molecules, frames, strict=True)
        ],
        acceptor=[
            denoised[f, m * n_channels + acceptor, laser]
            for m, f in zip(molecules, frames, strict=True)
        ],
        gamma=gamma,
        states=[dta[f, m * n_pairs] for m, f in zip(molecules, frames, strict=True)],
        frame_time=float(raw["frame_rate"]),
        bin_width=float(bin_width),
        centres=table[:, 0],
        counts=table[:, 1],
        gaussians=gaussians,
        dwells=dwells,
    )
