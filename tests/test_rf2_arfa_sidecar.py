# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The RF2-ArfA data set of the tMAVEN authors, live leg: the fit and the 2-D histogram (#708).

``tests/test_rf2_arfa.py`` holds the import and kinetics checks, which need no tMAVEN. Here a
child process under ``$TETHER_SIDECAR_PYTHON`` (``tests/_rf2_arfa_child.py``) runs tMAVEN on
the committed extract of ``RF2_core_wtprensEC_smd.hdf5`` (Zenodo 10.5281/zenodo.17246323,
CC-BY-4.0), with the bleed-through correction and the settings of the record's "tMAVEN
parameters" document, and this process compares what it returns with the authors' model file
(ADR-0079).

2. **Idealization.** A new consensus VB-HMM fit (two states, the priors stored in the authors'
   model, seed 1) against the authors' model, with the frozen ``schema/parity_tolerance.json``
   row. The state count, the state means and the Viterbi paths are inside it. The relative
   ELBO is NOT: 0.0194 against a frozen ceiling of 0.01. That is a measured difference, never a
   tolerance, and the row does not change for it (ADR-0009). The whole gap is the
   Gaussian-Wishart term of the ELBO (column 2 of ``likelihood``), which tMAVEN adds once per
   trace; the other three terms (columns 1, 3 and 4; column 0 is their sum, the ELBO) agree
   to 2e-5 relative, and the rates to 5e-4.
4. **Plots.** tMAVEN's 2-D histogram (``data_hist2d``) at the authors' settings. Start-
   synchronized, Tether's A2 histogram equals tMAVEN's counts cell for cell. Post-synchronized
   on the frames of each molecule's window, Tether's A2b histogram also equals tMAVEN's 1608
   counts cell for cell; before #806 Tether lacked the last window frame of the two
   transitions whose next dwell runs to the end of the window. tMAVEN's app reads the whole
   model chain, whose out-of-window frames hold state 0, so it also counts 2 jumps at a window
   border (#707); that difference is pinned below.

Measured 2026-11-12 on tMAVEN ``10f4230``, CPython 3.12.13, NumPy 1.26.4 (the sidecar lock).
The authors' files name no tMAVEN build; the model file is dated 2022-11-29.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import numpy as np
import pytest

from _sidecar_gate import SIDECAR as _SIDECAR
from _sidecar_gate import requires_sidecar
from tether.analysis.histogram import time_signal_histogram2d, transition_sync_histogram2d
from tether.fret.efficiency import corrected_fret
from tether.idealize import read_model, read_smd, states_from_idealized
from tether.idealize.parity import compare_models, load_frozen_tolerance, within_tolerance

pytestmark = pytest.mark.sidecar

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures"
SMD = FIXTURES / "rf2_core_wtprensec_smd.hdf5"
MODEL = FIXTURES / "rf2_core_wtprensec_model.hdf5"
CHILD = REPO / "tests" / "_rf2_arfa_child.py"
FROZEN = REPO / "schema" / "parity_tolerance.json"

#: ``correction.bleedthrough`` of the record's "tMAVEN parameters" document.
BLEEDTHROUGH = 0.035
#: Chosen before the first fit, as in ADR-0075; seed 2 gave the same model.
SEED = 1

#: MEASURED DIFFERENCE, not a tolerance: the relative ELBO of the fresh fit against the
#: authors' model was 0.01943. The bound catches a further move; ``> FROZEN`` keeps the docs
#: honest if the difference goes away.
RELATIVE_ELBO_MEASURED_MAX = 0.020
#: The ``likelihood`` columns other than the Gaussian-Wishart term (column 2) agree to this
#: (measured at most 2.0e-5, the transition-matrix term).
OTHER_ELBO_TERMS_RTOL = 3e-5
#: The rate matrix agrees to this (measured 4.3e-4 and 4.9e-5).
RATES_RTOL = 5e-4

#: tMAVEN's 2-D histogram settings, as ``_rf2_arfa_child.py`` reports them back.
HIST = {"time_bins": 100, "signal_bins": 61, "signal_range": (-0.2, 1.2), "time_dt": 0.1}


def _run(*args: str, cwd: Path) -> None:
    env = dict(os.environ)
    # The child imports tether.idealize._sidecar_runner; the sidecar job installs Tether there.
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(REPO / "src"), env.get("PYTHONPATH")) if p)
    proc = subprocess.run(
        [str(_SIDECAR), str(CHILD), *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=1800,
        check=False,
    )
    assert proc.returncode == 0 and "TETHER_RF2_CHILD_OK" in proc.stdout, (
        f"child failed ({proc.returncode}):\n{proc.stdout[-4000:]}\n{proc.stderr[-4000:]}"
    )


@pytest.fixture(scope="module")
def fresh_fit(tmp_path_factory: pytest.TempPathFactory):
    work = tmp_path_factory.mktemp("rf2_fit")
    out = work / "fit.hdf5"
    _run("fit", str(SMD), str(MODEL), str(BLEEDTHROUGH), str(SEED), str(out), cwd=work)
    return read_model(out)


@pytest.fixture(scope="module")
def hist(tmp_path_factory: pytest.TempPathFactory) -> dict[str, np.ndarray]:
    work = tmp_path_factory.mktemp("rf2_hist")
    out = work / "hist.npz"
    _run("hist2d", str(SMD), str(MODEL), str(BLEEDTHROUGH), str(out), cwd=work)
    with np.load(out) as data:
        return {key: data[key] for key in data.files}


@requires_sidecar
def test_a_fresh_fit_with_the_authors_priors_reproduces_their_model(fresh_fit, capsys) -> None:
    authors = read_model(MODEL)
    assert fresh_fit.model_type == authors.model_type == "vb Consensus HMM"
    assert fresh_fit.nstates == authors.nstates == 2
    metrics = compare_models(authors, fresh_fit)
    with capsys.disabled():
        print(f"\n[rf2-arfa] fresh fit vs authors: {json.dumps(metrics.as_dict())}", flush=True)
    frozen = load_frozen_tolerance(FROZEN)
    ok, failures = within_tolerance(metrics, frozen)
    assert not ok
    assert len(failures) == 1 and failures[0].startswith("relative ΔELBO"), failures
    assert frozen["relative_elbo_max"] < metrics.relative_elbo <= RELATIVE_ELBO_MEASURED_MAX

    # The last row of ``likelihood`` is (ELBO, lnz, Fgw, Fpi, Ftm), the ELBO being the sum of
    # the other four (tMAVEN's ``calc_lowerbound``).
    ours, theirs = fresh_fit.likelihood[-1], authors.likelihood[-1]
    others = [1, 3, 4]
    np.testing.assert_allclose(ours[others], theirs[others], rtol=OTHER_ELBO_TERMS_RTOL)
    np.testing.assert_allclose(fresh_fit.rates, authors.rates, rtol=RATES_RTOL)


def _efficiency() -> tuple[object, np.ndarray]:
    smd = read_smd(SMD)
    e = corrected_fret(smd.raw[:, :, 0], smd.raw[:, :, 1], alpha=BLEEDTHROUGH, gamma=1.0)
    return smd, e


@requires_sidecar
def test_the_settings_the_child_used_are_the_authors(hist) -> None:
    assert int(hist["pref_time_nbins"]) == HIST["time_bins"]
    assert int(hist["pref_signal_nbins"]) == HIST["signal_bins"]
    assert (float(hist["pref_signal_min"]), float(hist["pref_signal_max"])) == HIST["signal_range"]
    assert float(hist["pref_time_dt"]) == HIST["time_dt"]
    assert int(hist["pref_sync_preframe"]) == 50
    assert (int(hist["pref_sync_hmmstate_1"]), int(hist["pref_sync_hmmstate_2"])) == (0, -1)
    assert bool(hist["pref_sync_singledwell"])


@requires_sidecar
def test_the_start_synchronized_histogram_equals_tmavens(hist) -> None:
    smd, e = _efficiency()
    chunks = [e[i, smd.pre_list[i] : smd.post_list[i]] for i in range(smd.n_molecules)]
    ours = time_signal_histogram2d(chunks, **HIST)
    theirs = hist["start_counts"]
    # tMAVEN keeps one more time row, which no frame reaches.
    assert not theirs[-1].any()
    np.testing.assert_array_equal(ours.counts, theirs[:-1])
    assert ours.n_molecules == int(hist["start_nmol"]) == smd.n_molecules


@requires_sidecar
def test_the_post_synchronized_histogram_equals_tmavens_on_the_window_frames(hist) -> None:
    smd, e = _efficiency()
    model = read_model(MODEL)
    paths = states_from_idealized(model.idealized, model.means)
    windows = [slice(smd.pre_list[i], smd.post_list[i]) for i in range(smd.n_molecules)]
    pairs = [(paths[i, w], e[i, w]) for i, w in enumerate(windows)]
    ours = transition_sync_histogram2d(
        pairs, from_state=0, to_state=-1, single_dwell=True, sync_preframe=50, **HIST
    )
    theirs = hist["window_counts"]
    assert (
        (ours.n_transitions, ours.n_molecules)
        == (
            int(hist["window_npoints"]),
            int(hist["window_nmol"]),
        )
        == (38, 29)
    )
    # Before #806 Tether lacked one count in each of the cells (59, 47) and (67, 49): the last
    # window frame of a dwell that runs to the window end. Now the two are equal.
    np.testing.assert_array_equal(ours.counts, theirs)
    assert int(theirs.sum()) == 1608 == int(ours.counts.sum())

    # The app's own post-synchronized histogram reads the whole chain (#707): 2 more jumps,
    # from the state-0 frames outside a window, in 1 more molecule.
    assert (int(hist["post_npoints"]), int(hist["post_nmol"])) == (40, 30)
