# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Child of ``test_analysis_plots_sidecar.py``: run tMAVEN's seven plot routines (#707).

Runs under ``$TETHER_SIDECAR_PYTHON`` only, never in the test process. Usage::

    python _tmaven_plots_child.py SMD MODEL OUT.npz

It loads the SMD and the consensus model into a headless ``maven_class``, calls the routine
named in each row of ``docs/analysis/parity-gallery.md``, and saves the arrays that feed each
plot. Only tMAVEN computes here. B3 and C1 need a per-trace fit (tMAVEN's "vb GMM + vb HMM");
its seeds are made deterministic because tMAVEN reseeds from the clock.
"""

from __future__ import annotations

import itertools
import os
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# multi_plot selects Qt5Agg on import, which fails once pyplot is loaded on a headless host,
# so it comes first and the plots then switch to Agg.
from tmaven.trace_plot.multi_plot import multi_canvas  # noqa: E402

# isort: split
import matplotlib.pyplot as plt  # noqa: E402

plt.switch_backend("Agg")
import numba  # noqa: E402
import numpy as np  # noqa: E402
from tmaven.controllers.analysis_plots import data_hist2d as h2d  # noqa: E402
from tmaven.controllers.modeler.dwells import calculate_dwells, survival  # noqa: E402
from tmaven.maven import maven_class  # noqa: E402

PAIRS = ((-1, -1), (0, -1), (-1, 2), (1, 2), (2, 1))  # A2b (from, to) selections
FITS = ("Single Exponential", "Double Exponential", "Stretched Exponential")
TM_PAIRS = ((0, 1), (1, 0), (1, 2), (2, 1), (2, 3), (3, 2))  # B3 (init, final)


def _set(prefs, **values) -> None:
    for key, value in values.items():  # prefs_object wraps each value; dict.update skips that
        prefs[key] = value


def _axes():
    return plt.subplots()


def main(smd: str, model: str, out_path: str) -> None:
    out: dict[str, np.ndarray] = {}
    m = maven_class()
    m.io.load_smdtmaven_hdf5(smd, "dataset")
    m.modeler.load_result_from_hdf5(model)
    mod = m.modeler.model

    # A1: data_hist1d.plot (histogram and the model_on overlay drawn on the axes).
    p = m.plots.data_hist1d
    fig, ax = _axes()
    p.plot(fig, ax)
    out["a1_y"], out["a1_x"] = p.hist_y, p.hist_x
    out["a1_line_x"] = np.asarray(ax.lines[0].get_xdata())
    out["a1_lines"] = np.array([ln.get_ydata() for ln in ax.lines])
    plt.close(fig)

    # A2: data_hist2d.get_data, raw/start-synchronized and post-synchronized.
    p = m.plots.data_hist2d
    p.plot_mode = p.prefs["plot_mode"]
    p.prefs["sync_postsync"] = False
    out["a2_raw"], out["a2_raw_nmol"], _ = p.get_data()
    p.prefs["sync_postsync"] = True
    dpb = p.get_plot_data()[:, :, 1].copy()
    chain = mod.chain.astype(np.float64)
    for i in range(m.data.nmol):  # the NaN-padded state array the gallery's port reads
        chain[i, : m.data.pre_list[i]] = np.nan
        chain[i, m.data.post_list[i] :] = np.nan
    pr = p.prefs
    for single in (True, False):
        p.prefs["sync_singledwell"] = single
        for vi, vj in PAIRS:
            key = f"{int(single)}_{vi}_{vj}"
            p.prefs["sync_hmmstate_1"], p.prefs["sync_hmmstate_2"] = vi, vj
            out["a2b_ctrl_" + key], out["a2b_ctrl_n_" + key], _ = p.get_data()
            if single:
                sl = h2d.gen_sync_list_single(chain, vi, vj)
            else:
                npre = pr["sync_preframe"]
                sl = h2d.gen_sync_list_fixed(chain, vi, vj, npre, pr["time_nbins"] - npre + 1)
            out["a2b_fn_" + key] = h2d.histogram_sync_list(
                sl, dpb, pr["time_nbins"], pr["sync_preframe"],
                pr["signal_min"], pr["signal_max"], pr["signal_nbins"],
            )  # fmt: skip
            out["a2b_fn_n_" + key] = np.array([np.unique(sl[:, 0]).size, sl.shape[0]])

    # B1: data_tdp.get_neighbor_data (idealized branch) + gen_histogram, unsmoothed.
    p = m.plots.data_tdp
    p.plot_mode = p.prefs["plot_mode"]
    _set(p.prefs, hist_rawsignal=False, hist_smoothx=0.0, hist_smoothy=0.0, hist_normalize=False)
    for nskip in (1, 2, 3):
        p.prefs["nskip"] = nskip
        d1, d2, _n = p.get_neighbor_data()
        out[f"b1_z_{nskip}"] = p.gen_histogram(d1, d2)[2]
        out[f"b1_n_{nskip}"] = np.array(d1.size)

    # B2: modeler dwells, survival and the survival fits of run_dwell_analysis.
    for first in (False, True):
        calculate_dwells(mod, first)
        for s, lengths in mod.dwells.items():
            out[f"b2_dw_{int(first)}_{s}"] = np.sort(np.asarray(lengths, dtype=np.int64))
    calculate_dwells(mod, False)
    for s in range(mod.nstates):
        tau, surv = survival(np.array(mod.dwells[str(s)]))
        out[f"b2_tau_{s}"], out[f"b2_surv_{s}"] = tau, surv
        for j, ftype in enumerate(FITS):
            m.modeler.run_dwell_analysis(ftype, s)
            r = m.modeler.model.rates[s]
            out[f"b2_fit_{j}_{s}_k"], out[f"b2_fit_{j}_{s}_a"] = r["ks"], r["As"]
            out[f"b2_fit_{j}_{s}_r2"] = np.array(r["R2"])
            if "betas" in r:
                out[f"b2_fit_{j}_{s}_beta"] = np.asarray(r["betas"])

    # D1: the trace viewer's calc_trajectory and calc_model_traj, for every molecule.
    view = SimpleNamespace(gui=SimpleNamespace(maven=m), plot_mode="smFRET")
    m.prefs.add_dictionary(multi_canvas.default_prefs(view))  # what the viewer adds at start
    traj = [multi_canvas.calc_trajectory(view, i) for i in range(m.data.nmol)]
    out["d1_t"] = traj[0][0]
    out["d1_int"] = np.stack([t[1] for t in traj])
    out["d1_rel"] = np.stack([t[2] for t in traj])
    out["d1_win"] = np.array([(t[3], t[4]) for t in traj])
    out["d1_ideal"] = np.stack([multi_canvas.calc_model_traj(view, i) for i in range(m.data.nmol)])
    out["dt"] = np.array(m.prefs["plot.time_dt"])

    # C1 on the consensus model: model_vbstates reads model.hmms, which no modeler sets.
    fig, ax = _axes()
    m.plots.model_vbstates.plot(fig, ax)
    out["c1_consensus_n"] = np.array(m.plots.model_vbstates.N)
    plt.close(fig)

    # B3 and C1 on a per-trace fit. tMAVEN reseeds from the clock (np.random.seed() with no
    # argument, or time-based); replace each reseed with the next integer, in NumPy and Numba.
    seed, count = np.random.seed, itertools.count()

    @numba.njit("void(int64)")  # compiled now, while np.random.seed is still NumPy's
    def _nb_seed(s):
        np.random.seed(s)

    def _seed(_value=None):
        s = next(count)
        seed(s)
        _nb_seed(s)

    np.random.seed = _seed
    _set(m.prefs, **{"modeler.dtype": "FRET", "ncpu": 1, "modeler.vbhmm.nstates": 4})
    m.modeler.run_vbgmm_vbhmm()
    np.random.seed = seed
    fit = m.modeler.model
    out["fit_mean"] = fit.mean
    out["fit_ns"] = np.array([tl.mean.size for tl in fit.trace_level.values()])
    p = m.plots.tm_hist
    p.prefs["hist_on"] = True
    for i, j in TM_PAIRS:
        p.prefs["states"] = [i, j]
        fig, ax = _axes()
        p.plot(fig, ax)
        out[f"b3_tp_{i}_{j}"], out[f"b3_y_{i}_{j}"] = p.tp, p.hist_y
        out[f"b3_x_{i}_{j}"], out[f"b3_pdf_{i}_{j}"] = p.hist_x, p.pdf
        plt.close(fig)
    # model_vbstates.plot counts r.mu.size over model.hmms; hand it the per-trace models.
    fit.hmms = [SimpleNamespace(mu=tl.mean) for tl in fit.trace_level.values()]
    fig, ax = _axes()
    m.plots.model_vbstates.plot(fig, ax)
    out["c1_x"] = np.array([b.get_x() + b.get_width() / 2 for b in ax.patches])
    out["c1_y"] = np.array([b.get_height() for b in ax.patches])
    plt.close(fig)
    np.savez(out_path, **out)


if __name__ == "__main__":
    main(*sys.argv[1:4])
