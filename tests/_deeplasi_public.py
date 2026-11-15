# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Test-only reader and analysis for the public Deep-LASI ``.tdat`` files (#716).

The Deep-LASI authors published their DNA-origami data as Zenodo record 7561162 (CC BY 4.0;
Wanninger *et al.*, *Nat Commun* **14**:6564, 2023, doi:10.1038/s41467-023-42272-9). Each
archive holds one ``TIRFdata`` ``.tdat``. Tether's product reader
(:func:`tether.io.read_tdat`) takes the coordinates, the correction factors and the
detection mode from a ``.tdat``; the traces are not read by any product code. This module
reads the traces for a test only, under PRD §2.2 "What the tests can use", rule 2 (an old
format through a small reader under ``tests/``), and it starts from the original bytes.

What it reads
    For each colocalized molecule, the background-subtracted donor and acceptor traces under
    **donor excitation** (Deep-LASI ``TRACEdata.TracesCorrected``, which is
    ``TracesUncorrected - TracesBackground`` exactly on the public two-colour file). The set
    is an ALEX set; PRD §2.2 rule 1 limits a test to these two channels, so the acceptor
    channel under acceptor excitation is never read.

Where the traces are
    ``temp/Channel{k}`` is an MCOS ``Channel`` object. Its ``Traces`` property is a cell with
    one ``TRACEdata`` object for each excitation, in the order of ``temp/ALEXorder`` (``"GR"``:
    cell 0 is green, the donor excitation). Each ``TRACEdata`` property is a cell with one
    ``(n_frames, 1)`` array for each molecule. Each excitation has every second camera frame,
    so the time step of one excitation is ``ALEXnumberofchannels * FrameTime``.

A header that Tether's decoder does not read
    The public files carry MCOS metadata **version 3**. Its two header words after the six
    region offsets are zero, and :class:`tether.io.mcos.McosDecoder` (written against a
    version 4 file, where they are not zero) counts two names too few and refuses the file.
    :func:`mcos_metadata_for_decoder` gives the decoder a copy whose two words are not zero,
    so that the product decoder reads the name table it already reads in version 4. The
    product defect is tracked on its own issue; this module changes no product code.

The analysis (:func:`two_state_analysis`)
    Tether's own tools, in a fixed order with fixed constants: the window of each molecule
    ends at the first photobleaching frame of its donor or its acceptor
    (:func:`tether.fret.photobleach.detect_photobleach`); a molecule stays when its window is
    at least :data:`MIN_WINDOW_FRAMES` long and its summed-intensity signal-to-noise ratio is
    at least :data:`MIN_SNR`; one shared 2-state Gaussian HMM
    (:func:`tether.analysis.kinetics.fit_gaussian_hmm`) is fit to the apparent FRET of the
    first :data:`HMM_FIT_TRACES` molecules that spend time on both sides of
    :data:`CANDIDATE_SPLIT`, then every molecule gets its Viterbi path; a molecule with at
    least one transition is *dynamic*; the dwell times of each state come from
    :func:`tether.analysis.dwell.state_dwells` (the first and the last dwell of a molecule are
    dropped) and a mono-exponential fit to their survival curve
    (:func:`tether.analysis.dwell.fit_survival`), as the paper fits the cumulative
    distribution. Deep-LASI selected its traces with a neural network and the paper's
    manual selection with a human; Tether has neither, so the dynamic count is expected to
    differ. No number here is a frozen limit: a limit needs the maintainer's sign-off.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import h5py
import numpy as np

from tether.analysis.dwell import fit_survival, state_dwells, survival_curve
from tether.analysis.kinetics import fit_gaussian_hmm, viterbi_paths
from tether.fret.efficiency import apparent_fret
from tether.fret.photobleach import detect_photobleach
from tether.io.mcos import McosDecoder, object_reference_id

#: A molecule needs this many frames before its first photobleaching step.
MIN_WINDOW_FRAMES = 50
#: Mean summed intensity over the frame-to-frame noise of the summed intensity, in the window.
MIN_SNR = 4.0
#: The HMM fit uses this many candidate molecules (the Python Baum-Welch is slow on 6100).
HMM_FIT_TRACES = 400
#: A candidate spends at least :data:`CANDIDATE_FRACTION` of its window on each side of this.
CANDIDATE_SPLIT = 0.5
CANDIDATE_FRACTION = 0.05
#: Apparent FRET is clipped to this range before the HMM, so one dark frame is not a state.
FRET_CLIP = (-0.2, 1.2)
#: EM settings of the shared HMM.
HMM_MAX_ITER = 300
HMM_TOL = 1e-9

#: What the paper and the record state for the two archives this check opens. Each value
#: names where it is printed. The record's file table is ``Data_Overview.xlsx`` of Zenodo
#: record 7561162; "Fig. 3" is the two-colour figure of the paper.
PAPER = {
    "one_colour_traces": (7448, "record 7561162, Data_Overview.xlsx, Figure 2 row"),
    "two_colour_traces": (6100, "paper, Results, two-colour paragraph; Fig. 3g caption"),
    "two_colour_dynamic_deeplasi": (1499, "paper, Results, two-colour paragraph; Fig. 3g"),
    "two_colour_dynamic_manual": (1731, "paper, Results, paragraph after Fig. 3"),
    "apparent_fret_high": (0.8, "paper, Fig. 3c caption (apparent FRET of state 1)"),
    "apparent_fret_low": (0.2, "paper, Fig. 3c caption (apparent FRET of state 2)"),
    "dwell_high_s": (1.76, "paper, Fig. 3d caption (state 1, high FRET)"),
    "dwell_low_s": (2.64, "paper, Fig. 3d caption (state 2, low FRET)"),
}

_DONOR_COLOUR = "G"
_ACCEPTOR_COLOUR = "R"


def mcos_metadata_for_decoder(meta: np.ndarray) -> np.ndarray:
    """A copy of MCOS metadata that :class:`McosDecoder` reads, for version 3 too.

    The name table starts at byte 40. Bytes 32 to 39 are two header words; the decoder takes
    them as two NUL-separated tokens and skips them. In version 3 both words are zero, so
    they give no token and the decoder skips two real names. Two words of value 1 give the
    two tokens it expects. A version 4 header, or one whose words are not zero, is returned
    unchanged (as a copy).
    """
    out = np.array(meta, dtype=np.uint8, copy=True).reshape(-1)
    if out.size >= 40 and int(out[:4].view("<u4")[0]) == 3 and not out[32:40].any():
        out[32:40] = np.array([1, 0, 0, 0, 1, 0, 0, 0], dtype=np.uint8)
    return out


def _decoder(file: h5py.File) -> McosDecoder:
    sub = file.get("#subsystem#")
    if sub is None or "MCOS" not in sub:
        raise ValueError("the .tdat has no MCOS subsystem, so it holds no traces")
    cells = np.asarray(sub["MCOS"][()]).reshape(-1)
    meta = np.asarray(file[cells[0]][()]).reshape(-1).astype(np.uint8)
    return McosDecoder(file, cells, mcos_metadata_for_decoder(meta))


def _chars(dataset: h5py.Dataset) -> str:
    return "".join(map(chr, np.asarray(dataset[()]).reshape(-1)))


@dataclass(frozen=True)
class DonorExcitationTraces:
    """Background-subtracted ``(n_molecules, n_frames)`` traces under donor excitation."""

    donor: np.ndarray
    acceptor: np.ndarray
    #: Seconds between two frames of one excitation (the ALEX cycle).
    dt_s: float


def read_donor_excitation_traces(path: str | Path) -> DonorExcitationTraces:
    """Read the donor and acceptor traces under donor excitation from a two-colour ``.tdat``."""
    with h5py.File(path, "r") as file:
        temp = file["temp"]
        decoder = _decoder(file)
        order = _chars(temp["ALEXorder"])
        if _DONOR_COLOUR not in order:
            raise ValueError(f"ALEXorder {order!r} names no donor excitation")
        excitation = order.index(_DONOR_COLOUR)
        n_exc = int(np.asarray(temp["ALEXnumberofchannels"][()]).reshape(-1)[0])
        frame_s = float(np.asarray(temp["FrameTime"][()]).reshape(-1)[0]) / 1000.0
        series: dict[str, np.ndarray] = {}
        for ref in np.asarray(temp["Channel"][()]).reshape(-1):
            if not ref:
                continue
            channel = object_reference_id(np.asarray(file[ref][()]).reshape(-1))
            colour = decoder.property_value(channel, "ChannelColor")
            if colour not in (_DONOR_COLOUR, _ACCEPTOR_COLOUR):
                continue
            per_excitation = np.asarray(decoder.property_dataset(channel, "Traces")[()]).reshape(-1)
            tracedata = object_reference_id(
                np.asarray(file[per_excitation[excitation]][()]).reshape(-1)
            )
            cell = np.asarray(decoder.property_dataset(tracedata, "TracesCorrected")[()])
            series[colour] = np.stack(
                [np.asarray(file[r][()], dtype=np.float64).reshape(-1) for r in cell.reshape(-1)]
            )
    if set(series) != {_DONOR_COLOUR, _ACCEPTOR_COLOUR}:
        raise ValueError(f"expected a donor and an acceptor channel, found {sorted(series)}")
    return DonorExcitationTraces(series[_DONOR_COLOUR], series[_ACCEPTOR_COLOUR], n_exc * frame_s)


@dataclass(frozen=True)
class TwoStateResult:
    """The summary numbers of :func:`two_state_analysis` (state 0 is the low-FRET state)."""

    n_molecules: int
    n_kept: int
    n_fit: int
    n_dynamic: int
    fret: tuple[float, float]
    fret_sigma: tuple[float, float]
    dwell_s: tuple[float, float]
    n_dwells: tuple[int, int]
    fit_r_squared: tuple[float, float]
    hmm_converged: bool


def _snr(total: np.ndarray) -> float:
    noise = float(np.std(np.diff(total))) / np.sqrt(2.0)
    return float(np.mean(total)) / noise if noise > 0 else 0.0


def two_state_analysis(donor: np.ndarray, acceptor: np.ndarray, dt_s: float) -> TwoStateResult:
    """FRET states and dwell times of a two-state trace set with Tether's own tools."""
    donor = np.asarray(donor, dtype=np.float64)
    acceptor = np.asarray(acceptor, dtype=np.float64)
    fret: list[np.ndarray] = []
    for d, a in zip(donor, acceptor, strict=True):
        bleach = detect_photobleach(d, a)
        end = min(bleach.donor_pb, bleach.acceptor_pb)
        if end < MIN_WINDOW_FRAMES or _snr(d[:end] + a[:end]) < MIN_SNR:
            continue
        with np.errstate(divide="ignore", invalid="ignore"):
            e = np.asarray(apparent_fret(d[:end], a[:end]), dtype=np.float64)
        if np.isfinite(e).all():
            fret.append(np.clip(e, *FRET_CLIP))
    candidates = [
        e
        for e in fret
        if (e > CANDIDATE_SPLIT).mean() >= CANDIDATE_FRACTION
        and (e < CANDIDATE_SPLIT).mean() >= CANDIDATE_FRACTION
    ][:HMM_FIT_TRACES]
    if not candidates:
        raise ValueError("no molecule spends time in both FRET ranges; nothing to fit")
    hmm = fit_gaussian_hmm(candidates, 2, n_iter=HMM_MAX_ITER, tol=HMM_TOL)
    paths = viterbi_paths(fret, hmm)
    dynamic = [p for p in paths if np.any(np.diff(p) != 0)]
    lengths = state_dwells(dynamic, no_state=-1).lengths
    dwell, count, r2 = [], [], []
    for state in (0, 1):
        dwells = np.asarray(lengths.get(state, []), dtype=np.int64)
        tau, survival = survival_curve(dwells)
        fit = fit_survival(tau * dt_s, survival)
        dwell.append(1.0 / float(fit.rates[0]) if fit.success else float("nan"))
        count.append(int(dwells.size))
        r2.append(float(fit.r_squared) if fit.success else float("nan"))
    return TwoStateResult(
        n_molecules=int(donor.shape[0]),
        n_kept=len(fret),
        n_fit=len(candidates),
        n_dynamic=len(dynamic),
        fret=(float(hmm.means[0]), float(hmm.means[1])),
        fret_sigma=(float(hmm.sigmas[0]), float(hmm.sigmas[1])),
        dwell_s=(dwell[0], dwell[1]),
        n_dwells=(count[0], count[1]),
        fit_r_squared=(r2[0], r2[1]),
        hmm_converged=bool(hmm.converged),
    )
