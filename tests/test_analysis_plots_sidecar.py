# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The numbers behind the seven parity-gallery plots vs live tMAVEN (#707).

``docs/analysis/parity-gallery.md`` names one tMAVEN routine for each plot type, and the
unmarked tests compare Tether with copies of those routines. tMAVEN is not installed on the
required matrix, so a copy with a transcription error, or a copy that no longer matches the
pinned tMAVEN, failed no test. Here ``_tmaven_plots_child.py`` runs the real routines under
``$TETHER_SIDECAR_PYTHON`` on the committed 281-molecule SMD and its consensus model, and this
process compares what they return with Tether **and** with each in-test copy.

Two classes of agreement (Q7: A). A **port** (A1, A2, B1, B2, D1) must be equal; the only
rounding is in the A1 overlay, whose vectorized form differs from tMAVEN's loop by at most
1.5e-14 relative. A **re-derivation** (B3, C1: one consensus model instead of independent
per-trace fits) and A2b on Tether's windowed rows against what the tMAVEN app plots are measured:
the total-variation distance between the two normalized arrays must stay inside the limit frozen
in ``schema/plot_parity_limits.json``, which records the measurement and its conditions. A2b on
Tether's windowed rows against the tMAVEN routine is equal (#806).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import h5py
import numpy as np
import pytest

from _sidecar_gate import SIDECAR as _SIDECAR
from _sidecar_gate import requires_sidecar
from test_analysis_dwell import _tmaven_dwells, _tmaven_survival  # noqa: PLC2701
from test_analysis_histogram_overlay import _tmaven_reference  # noqa: PLC2701
from test_analysis_histogram_postsync import _tmaven_postsync  # noqa: PLC2701
from test_analysis_tdp import _tmaven_tdp  # noqa: PLC2701
from tether.analysis.dwell import fit_survival, state_dwells, survival_curve
from tether.analysis.histogram import (
    apparent_e_histogram,
    model_gaussian_overlay,
    time_signal_histogram2d,
    transition_sync_histogram2d,
)
from tether.analysis.state_number import state_number_counts
from tether.analysis.tdp import _levels_from_states, transition_density
from tether.analysis.transition_prob import transition_prob_histogram
from tether.fret.efficiency import apparent_fret
from tether.gui.trace_dock import trace_from_smd
from tether.idealize import NO_STATE

pytestmark = pytest.mark.sidecar

TESTS = Path(__file__).parent
SMD = TESTS / "fixtures" / "large" / "smd_281mol.hdf5"
MODEL = TESTS / "fixtures" / "large" / "model_281mol.hdf5"
LIMITS = json.loads((TESTS.parent / "schema" / "plot_parity_limits.json").read_text("utf-8"))
OVERLAY_RTOL = 1e-13  # measured at most 1.44e-14 (vectorized form vs tMAVEN's loop)
# Tether (base env) and tMAVEN (sidecar env) make the same curve_fit call, but on different
# NumPy/SciPy builds the optimizer can stop at a slightly different point: bit-equal on one
# host, up to 3.9e-6 relative (double-exponential rate) on another. 1e-5 is the bound the
# unmarked multi-exponential fits in test_analysis_dwell.py use; R2 agrees far closer.
FIT_RTOL, R2_RTOL = 1e-5, 1e-9
A2B_PAIRS = ((-1, -1), (0, -1), (-1, 2), (1, 2), (2, 1))
FITS = ("single", "double", "stretched")
TM_PAIRS = ((0, 1), (1, 0), (1, 2), (2, 1), (2, 3), (3, 2))


def _tv(p: np.ndarray, q: np.ndarray) -> float:
    """Total-variation distance of two non-negative arrays, each normalized to sum 1."""
    p, q = np.ravel(p).astype(float), np.ravel(q).astype(float)
    return float(0.5 * np.abs(p / p.sum() - q / q.sum()).sum())


def _within(block: str, key: str, value: float) -> None:
    entry = LIMITS["limits"][block]["values"][key]
    assert value <= entry["limit"], f"{block}[{key}]: {value!r} > frozen {entry['limit']!r}"


@pytest.fixture(scope="module")
def live(tmp_path_factory: pytest.TempPathFactory) -> dict:
    for path, digest in ((SMD, "smd_281mol_sha256"), (MODEL, "model_281mol_sha256")):
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        assert sha == LIMITS["measured"][digest], f"{path.name} is not the measured fixture"
    out = tmp_path_factory.mktemp("plots_live") / "out.npz"
    proc = subprocess.run(
        [
            str(_SIDECAR),
            "-I",
            str(TESTS / "_tmaven_plots_child.py"),
            str(SMD),
            str(MODEL),
            str(out),
        ],
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    assert proc.returncode == 0, f"tMAVEN child failed (exit {proc.returncode}):\n{proc.stderr}"
    with np.load(out) as npz:
        tm = {key: npz[key] for key in npz.files}
    with h5py.File(SMD, "r") as smd, h5py.File(MODEL, "r") as model:
        raw = np.asarray(smd["dataset/data/raw"], dtype=np.float64)
        pre = np.asarray(smd["dataset/tMAVEN/pre_list"])
        post = np.asarray(smd["dataset/tMAVEN/post_list"])
        mod = {k: np.asarray(model["model/" + k]) for k in ("chain", "idealized", "mean", "var")}
        mod["frac"] = np.asarray(model["model/frac"])
    windows = [slice(int(a), int(b)) for a, b in zip(pre, post, strict=True)]
    e = [apparent_fret(raw[i, w, 0], raw[i, w, 1]) for i, w in enumerate(windows)]
    states = [mod["chain"][i, w].astype(np.int64) for i, w in enumerate(windows)]
    # tMAVEN's own rectangular rows: NaN signal outside the window; the state is NaN there
    # in the gallery's port and 0 in the model the app holds (its int chain pads with 0).
    e_rect = np.full(raw.shape[:2], np.nan)
    s_nan = np.full(raw.shape[:2], NO_STATE, dtype=np.int64)
    for i, w in enumerate(windows):
        e_rect[i, w], s_nan[i, w] = e[i], states[i]
    s_zero = mod["chain"].astype(np.int64)
    return {"tm": tm, "raw": raw, "mod": mod, "windows": windows, "e": e, "states": states,
            "e_rect": e_rect, "s_nan": s_nan, "s_zero": s_zero}  # fmt: skip


@requires_sidecar
def test_a1_histogram_and_overlay_match_live_tmaven(live: dict) -> None:
    tm, mod = live["tm"], live["mod"]
    h = apparent_e_histogram(np.concatenate(live["e"]))
    np.testing.assert_array_equal(h.counts, tm["a1_y"])
    np.testing.assert_array_equal(h.bin_edges, tm["a1_x"])
    ov = model_gaussian_overlay(mod["mean"], mod["var"], mod["frac"])
    np.testing.assert_array_equal(ov.x, tm["a1_line_x"])
    lines = np.vstack([ov.components, ov.total])  # tMAVEN draws each component, then the sum
    np.testing.assert_allclose(lines, tm["a1_lines"], rtol=OVERLAY_RTOL, atol=0)


@requires_sidecar
def test_a2_raw_histogram_matches_live_tmaven(live: dict) -> None:
    tm = live["tm"]
    h = time_signal_histogram2d(live["e"])
    # histogram_raw allocates time_bins + 1 rows and fills only the first time_bins.
    assert not tm["a2_raw"][-1].any()
    np.testing.assert_array_equal(h.counts, tm["a2_raw"][:-1])
    assert h.n_molecules == tm["a2_raw_nmol"]


@requires_sidecar
def test_a2b_post_sync_matches_live_tmaven(live: dict) -> None:
    """Given tMAVEN's rows Tether is equal; on its own windowed rows the app gap is frozen.

    On Tether's windowed rows the routine is equal too: a trailing single-dwell window keeps
    the window's final frame in both (#806). tMAVEN alone drops the movie's last frame when a
    window runs to the movie end; on this fixture that frame never lands inside the time
    columns, so the rectangular comparison stays equal. The gap to the app is one deliberate
    difference, measured: the app's model pads the state path with 0 outside the window, so
    it also synchronizes on window borders as if they were jumps.
    """
    tm = live["tm"]
    rows = {"fn": live["s_nan"], "ctrl": live["s_zero"]}
    for single in (True, False):
        for vi, vj in A2B_PAIRS:
            key = f"{int(single)}_{vi}_{vj}"
            kw = {"from_state": vi, "to_state": vj, "single_dwell": single}
            for leg, s in rows.items():
                rect = transition_sync_histogram2d(list(zip(s, live["e_rect"], strict=True)), **kw)
                np.testing.assert_array_equal(rect.counts, tm[f"a2b_{leg}_{key}"], err_msg=key)
            own = transition_sync_histogram2d(
                list(zip(live["states"], live["e"], strict=True)), **kw
            )
            assert (own.n_molecules, own.n_transitions) == tuple(tm["a2b_fn_n_" + key]), key
            np.testing.assert_array_equal(own.counts, tm["a2b_fn_" + key], err_msg=key)
            _within("A2b_windowed_vs_tmaven_app", key, _tv(own.counts, tm["a2b_ctrl_" + key]))


@requires_sidecar
def test_b1_tdp_matches_live_tmaven(live: dict) -> None:
    ideal = [live["mod"]["idealized"][i, w] for i, w in enumerate(live["windows"])]
    for nskip in (1, 2, 3):
        tdp = transition_density(ideal, nskip=nskip)
        np.testing.assert_array_equal(tdp.counts, live["tm"][f"b1_z_{nskip}"])
        assert tdp.n_transitions == live["tm"][f"b1_n_{nskip}"]


@requires_sidecar
def test_b2_dwells_survival_and_fits_match_live_tmaven(live: dict) -> None:
    tm = live["tm"]
    for first in (False, True):
        dw = state_dwells(live["states"], include_first=first)
        for s in range(4):
            got = np.sort(dw.lengths[s])
            np.testing.assert_array_equal(
                got, tm[f"b2_dw_{int(first)}_{s}"], err_msg=f"{first} {s}"
            )
    dw = state_dwells(live["states"])
    for s in range(4):
        tau, surv = survival_curve(dw.lengths[s])
        np.testing.assert_array_equal(tau, tm[f"b2_tau_{s}"])
        np.testing.assert_array_equal(surv, tm[f"b2_surv_{s}"])
        for j, model in enumerate(FITS):  # the same curve_fit call, to the optimizer's tolerance
            fit, key = fit_survival(tau, surv, model=model), f"b2_fit_{j}_{s}"
            close = {"rtol": FIT_RTOL, "atol": 0, "err_msg": key}
            np.testing.assert_allclose(fit.rates, tm[key + "_k"], **close)
            np.testing.assert_allclose(fit.amplitudes, tm[key + "_a"], **close)
            np.testing.assert_allclose(fit.r_squared, tm[key + "_r2"], rtol=R2_RTOL, err_msg=key)
            if model == "stretched":
                np.testing.assert_allclose(np.ravel(fit.beta), tm[key + "_beta"], **close)


@requires_sidecar
def test_embedded_copies_match_live_tmaven(live: dict) -> None:
    """Each in-test copy of a tMAVEN routine reproduces the routine itself.

    Exactly, except the floating-point A1 loop: it runs on the base NumPy and tMAVEN on the
    sidecar NumPy, which can differ in the last bit (3.8e-16 relative seen), so to rounding.
    """
    tm, mod = live["tm"], live["mod"]
    _x, comps, total = _tmaven_reference(mod["mean"], mod["var"], mod["frac"], -0.25, 1.25, 1001)
    np.testing.assert_allclose(np.vstack([comps, total]), tm["a1_lines"], rtol=OVERLAY_RTOL, atol=0)
    chain = np.where(live["s_nan"] == NO_STATE, np.nan, live["s_nan"].astype(np.float64))
    for single in (True, False):
        for vi, vj in A2B_PAIRS:
            key = f"{int(single)}_{vi}_{vj}"
            out, nmol, npts = _tmaven_postsync(
                chain, live["e_rect"], vi=vi, vj=vj, single_dwell=single,
                xbins=100, xsync=50, ymin=-0.2, ymax=1.2, ybins=61,
            )  # fmt: skip
            np.testing.assert_array_equal(out, tm["a2b_fn_" + key], err_msg=key)
            assert (nmol, npts) == tuple(tm["a2b_fn_n_" + key]), key
    for nskip in (1, 2, 3):
        z, n = _tmaven_tdp(mod["idealized"], nskip=nskip, signal_nbins=101, signal_min=-0.25,
                           signal_max=1.25)  # fmt: skip
        np.testing.assert_array_equal(z, tm[f"b1_z_{nskip}"])
        assert n == tm[f"b1_n_{nskip}"]
    for first in (False, True):
        dwells = _tmaven_dwells(live["states"], no_state=NO_STATE, first_flag=first)
        for s in range(4):
            np.testing.assert_array_equal(dwells.get(s, []), tm[f"b2_dw_{int(first)}_{s}"])
    for s in range(4):
        tau, surv = _tmaven_survival(tm[f"b2_dw_0_{s}"])
        np.testing.assert_array_equal(tau, tm[f"b2_tau_{s}"])
        np.testing.assert_array_equal(surv, tm[f"b2_surv_{s}"])


@requires_sidecar
def test_b3_transition_probability_within_frozen_limit(live: dict) -> None:
    """Re-derivation: per-molecule empirical P(i->j) vs tMAVEN's per-trace VB matrices."""
    tm = live["tm"]
    for i, j in TM_PAIRS:
        key = f"{i}_{j}"
        tph = transition_prob_histogram(live["states"], init_state=i, final_state=j)
        np.testing.assert_array_equal(tph.edges, tm["b3_x_" + key])
        np.testing.assert_array_equal(tph.kde_x, np.linspace(0.0, 1.0, 100))  # tMAVEN's grid
        _within("B3_histogram", key, _tv(tph.counts, tm["b3_y_" + key]))
        _within("B3_kde", key, _tv(tph.kde_y, tm["b3_pdf_" + key]))


@requires_sidecar
def test_c1_state_number_within_frozen_limit(live: dict) -> None:
    """Re-derivation: distinct states a consensus path visits vs per-trace model sizes."""
    tm = live["tm"]
    # model_vbstates reads model.hmms, which no tMAVEN 0.3.0 modeler sets: on a real model
    # it draws nothing. The child hands it the per-trace fit's sizes under that name.
    assert tm["c1_consensus_n"] == 0
    np.testing.assert_array_equal(tm["c1_x"], np.arange(1, 11))
    counts = state_number_counts(live["states"], states_low=1, states_high=10)
    np.testing.assert_array_equal(counts.states, tm["c1_x"])
    _within("C1_bars", "all", _tv(counts.counts, tm["c1_y"]))


@requires_sidecar
def test_d1_trace_viewer_values_match_live_tmaven(live: dict) -> None:
    tm, mod = live["tm"], live["mod"]
    np.testing.assert_array_equal(tm["d1_win"], [(w.start, w.stop) for w in live["windows"]])
    zero_total = 0
    for i in range(live["raw"].shape[0]):
        view = trace_from_smd(live["raw"], i, frame_time=float(tm["dt"]))
        np.testing.assert_array_equal(view.time_axis("seconds"), tm["d1_t"])
        np.testing.assert_array_equal(np.stack([view.donor, view.acceptor], -1), tm["d1_int"][i])
        e, ok = view.apparent_e, view.total != 0
        np.testing.assert_array_equal(e[ok], tm["d1_rel"][i, ok, 1], err_msg=str(i))
        assert np.isnan(e[~ok]).all()  # tMAVEN divides by total + 1e-300 there instead
        zero_total += int((~ok).sum())
        path = np.where(np.isnan(mod["idealized"][i]), NO_STATE, mod["chain"][i]).astype(np.int64)
        levels = _levels_from_states(path, mod["mean"], NO_STATE)
        np.testing.assert_array_equal(levels, tm["d1_ideal"][i], err_msg=str(i))
    assert zero_total == 4  # frames of molecules 36, 175, 183 and 211
