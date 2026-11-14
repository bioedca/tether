# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tether against PySMACKS on the public PySMACKS tutorial traces (#720). Needs the network.

The data are the traces file of Zenodo record 10.5281/zenodo.21793247 (CC0-1.0; manifest id
``pysmacks-tutorial-traces``): 1,136 ALEX traces of a two-state Holliday junction in OpenFRET
JSON, 52 of them labelled ``FRET``. The record stores no FRET values and no hidden-Markov results,
so the PySMACKS side was made once, offline, by ``scripts/make_pysmacks_reference.py`` with
PySMACKS 1.0.0b4, and committed as ``tests/fixtures/pysmacks_tutorial_reference.json``. PySMACKS
does not run here. Each test reads the original downloaded zip through the test-only reader
``_openfret`` (PRD §2.2 rules 1 and 2: donor-excitation channels only, original bytes).

* **The histogram is exact.** Tether's apparent FRET of each labelled, background-subtracted
  frame, binned by ``apparent_e_histogram`` in the bins of the reference, has the counts of the
  PySMACKS histogram, bin for bin.
* **The rates are a report.** Tether's base-env two-state HMM (``two_state_rate_constants``, the
  fit of the kinSoft oracle) idealizes the same frames, and the test prints its transition
  probabilities and exit rates next to PySMACKS's ensemble HMM. It fails only when the fit does
  not run or does not converge; no frozen limit exists until the maintainer signs one off.

The rates are per frame: the record gives no frame time. ``@pytest.mark.reference_download``
puts the module in the weekly ``reference-data`` job only (``tests/test_marker_contract.py``).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from _openfret import read_openfret
from reference_data import fetch

pytestmark = pytest.mark.reference_download

DATASET = "pysmacks-tutorial-traces"
REFERENCE = Path(__file__).parent / "fixtures" / "pysmacks_tutorial_reference.json"
LABEL = "FRET"


@pytest.fixture(scope="module")
def reference() -> dict:
    return json.loads(REFERENCE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def labelled():
    """The traces labelled ``FRET`` of the downloaded file; skips when it is not here."""
    traces = read_openfret(fetch.path_for(DATASET))
    return traces, traces.labelled(LABEL)


@pytest.fixture(scope="module")
def apparent_e(labelled) -> list[np.ndarray]:
    from tether.fret.efficiency import apparent_fret

    _, fret = labelled
    return [apparent_fret(*trace.segment(LABEL)) for trace in fret]


def test_the_reference_was_made_from_the_downloaded_file(reference) -> None:
    entry = fetch.load_manifest()[DATASET]
    assert reference["source"]["sha256"] == entry.sha256
    assert reference["source"]["file"] == entry.file


def test_the_reader_finds_the_labelled_traces_of_the_reference(labelled, reference) -> None:
    traces, fret = labelled
    assert len(traces.traces) == 1136
    assert traces.metadata["ALEX"] == "yes"
    counts = {label: len(traces.labelled(label)) for label in ("FRET", "DO", "AO")}
    assert counts == {"FRET": 52, "DO": 71, "AO": 26}
    # The same traces, in the same order and over the same frames, as PySMACKS used.
    assert [(t.uuid, t.labels[LABEL][1] - t.labels[LABEL][0]) for t in fret] == [
        (t["uuid"], t["frames"]) for t in reference["traces"]
    ]


def test_tethers_apparent_e_histogram_equals_the_pysmacks_histogram(apparent_e, reference) -> None:
    from tether.analysis.histogram import apparent_e_histogram

    ref = reference["histogram"]
    hist = apparent_e_histogram(
        np.concatenate(apparent_e),
        bins=ref["bins"],
        value_range=tuple(ref["range"]),
        density=False,
    )
    assert hist.n_samples == ref["n_frames"]
    assert [float(hist.bin_edges[0]), float(hist.bin_edges[-1])] == ref["edges_check"]
    counts = hist.counts.astype(np.int64)
    assert int(ref["n_frames"] - counts.sum()) == ref["n_outside_range"]
    differ = np.flatnonzero(counts != np.asarray(ref["counts"]))
    assert differ.size == 0, f"bins {differ.tolist()} differ from PySMACKS"


def test_tethers_two_state_rates_report_their_difference_from_pysmacks(
    apparent_e, reference, capsys
) -> None:
    from tether.analysis.kinetics import two_state_rate_constants

    fit = two_state_rate_constants(apparent_e, dt=1.0)
    assert fit.hmm.converged, f"the HMM stopped at its cap of {fit.hmm.n_iter} iterations"
    assert fit.n_dwells_low > 0 and fit.n_dwells_high > 0
    assert fit.fret_low < fit.fret_high

    ref = reference["hmm"]
    ref_t = np.asarray(ref["transition_matrix_per_frame"])
    ref_e = ref["state_apparent_e"]
    tether_t = fit.hmm.tmatrix
    rows = [
        ("state 1 apparent E", ref_e[0], fit.fret_low),
        ("state 2 apparent E", ref_e[1], fit.fret_high),
        ("p(1 -> 2) per frame", ref_t[0, 1], tether_t[0, 1]),
        ("p(2 -> 1) per frame", ref_t[1, 0], tether_t[1, 0]),
        ("k(1 -> 2) per frame", ref_t[0, 1], fit.k_low_high),
        ("k(2 -> 1) per frame", ref_t[1, 0], fit.k_high_low),
    ]
    lines = [
        f"\n[pysmacks-tutorial] {len(apparent_e)} traces labelled {LABEL}, "
        f"PySMACKS {reference['generator']['packages']['PySMACKS']} vs Tether",
        "  quantity               PySMACKS     Tether       difference",
    ]
    for name, theirs, ours in rows:
        lines.append(f"  {name:<21}  {theirs:11.5f}  {ours:11.5f}  {ours - theirs:+.5f}")
    lines.append(
        "  PySMACKS k = p, as its Kinetics tab shows it (p / frame time); Tether k = 1/<dwell> "
        f"({fit.n_dwells_low} + {fit.n_dwells_high} interior dwells)"
    )
    # Report only: the table goes to the job log, past pytest's capture of a passing test.
    with capsys.disabled():
        print("\n".join(lines), flush=True)
