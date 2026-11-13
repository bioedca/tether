# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Child of ``test_rf2_arfa_sidecar.py``: run tMAVEN on the RF2-ArfA extract (#708, ADR-0079).

Runs under ``$TETHER_SIDECAR_PYTHON`` only, never in the test process. Two commands::

    python _rf2_arfa_child.py fit SMD MODEL BLEEDTHROUGH SEED OUT.hdf5
    python _rf2_arfa_child.py hist2d SMD MODEL BLEEDTHROUGH OUT.npz

Both load the SMD into a headless ``maven_class`` and apply the authors' bleed-through
correction (``corrections.bleedthrough``, the value of the record's "tMAVEN parameters"
document), as the authors did before they fitted and plotted.

``fit`` runs tMAVEN's consensus VB-HMM (``modeler.run_vbconhmm``, two states) with the priors
stored in the authors' model file, instead of the priors tMAVEN would derive from the data,
and writes the model with tMAVEN's own serializer. The random draws follow ``SEED``
(:func:`tether.idealize._sidecar_runner.seed_random_streams`, ADR-0075).

``hist2d`` loads the authors' model as well and saves the arrays behind tMAVEN's 2-D histogram
(``data_hist2d.get_data``) with the authors' settings: start-synchronized, and
post-synchronized with tMAVEN's default transition selection.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("NAPARI_ASYNC", "0")

import h5py  # noqa: E402
import numpy as np  # noqa: E402

#: The 2-D histogram settings of the record's "tMAVEN parameters" document. ``hist_interp_res``
#: and the colour settings act only on the drawn image, never on the counts compared here.
AUTHORS_HIST2D = {
    "color_cmap": "jet",
    "hist_interp_res": 800,
    "time_dt": 0.1,
    "time_nticks": 3,
    "time_nbins": 100,
    "color_floor": 0.05,
    "color_dblfloor": 0.05,
}

#: The order tMAVEN's ``get_priors`` returns the consensus VB-HMM priors in.
PRIOR_ORDER = ("mu_prior", "beta_prior", "a_prior", "b_prior", "pi_prior", "tm_prior")


def _maven(smd: str, bleedthrough: float):
    from tmaven.maven import maven_class

    m = maven_class()
    m.io.load_smdtmaven_hdf5(smd, "dataset")
    m.prefs["correction.bleedthrough"] = float(bleedthrough)
    m.corrections.bleedthrough()
    return m


def fit(smd: str, model: str, bleedthrough: str, seed: str, out: str) -> None:
    from tether.idealize._sidecar_runner import seed_random_streams

    m = _maven(smd, float(bleedthrough))
    m.prefs["modeler.dtype"] = "FRET"
    m.prefs["ncpu"] = 1
    m.prefs["modeler.vbconhmm.nstates"] = 2
    with h5py.File(model, "r") as f:
        stored = [np.asarray(f["model/priors"][name][()], dtype=np.float64) for name in PRIOR_ORDER]
    m.modeler.get_priors = lambda *_args, **_kwargs: stored  # the authors' priors
    seed_random_streams(int(seed))
    m.modeler.run_vbconhmm()
    if os.path.exists(out):
        os.remove(out)
    m.modeler.export_result_to_hdf5(out)


def hist2d(smd: str, model: str, bleedthrough: str, out: str) -> None:
    m = _maven(smd, float(bleedthrough))
    m.modeler.load_result_from_hdf5(model)
    p = m.plots.data_hist2d
    for key, value in AUTHORS_HIST2D.items():
        p.prefs[key] = value  # prefs_object wraps each value; dict.update skips that
    p.plot_mode = p.prefs["plot_mode"]
    result: dict[str, np.ndarray] = {}
    p.prefs["sync_postsync"] = False
    counts, nmol, _ = p.get_data()
    result["start_counts"], result["start_nmol"] = counts, np.array(nmol)
    p.prefs["sync_postsync"] = True
    counts, nmol, npoints = p.get_data()
    result["post_counts"], result["post_nmol"] = counts, np.array(nmol)
    result["post_npoints"] = np.array(npoints)
    # The app reads the model's whole ``chain``, whose frames outside a molecule's window hold
    # state 0, so it also synchronizes on a jump at a window border (#707). The same tMAVEN
    # routines on the chain with the frames outside the window set to NaN give the counts of
    # the window alone, which is what Tether bins.
    from tmaven.controllers.analysis_plots import data_hist2d as h2d

    chain = m.modeler.model.chain.astype(np.float64)
    for i in range(m.data.nmol):
        chain[i, : m.data.pre_list[i]] = np.nan
        chain[i, m.data.post_list[i] :] = np.nan
    pr = p.prefs
    synclist = h2d.gen_sync_list_single(chain, pr["sync_hmmstate_1"], pr["sync_hmmstate_2"])
    result["window_counts"] = h2d.histogram_sync_list(
        synclist,
        p.get_plot_data()[:, :, 1].copy(),
        pr["time_nbins"],
        pr["sync_preframe"],
        pr["signal_min"],
        pr["signal_max"],
        pr["signal_nbins"],
    )
    result["window_nmol"] = np.array(np.unique(synclist[:, 0]).size)
    result["window_npoints"] = np.array(synclist.shape[0])
    for key in (
        "time_nbins",
        "signal_nbins",
        "signal_min",
        "signal_max",
        "sync_preframe",
        "sync_hmmstate_1",
        "sync_hmmstate_2",
        "sync_singledwell",
        "time_dt",
    ):
        result["pref_" + key] = np.array(p.prefs[key])
    np.savez(out, **result)


def main(argv: list[str]) -> None:
    command, args = argv[0], argv[1:]
    {"fit": fit, "hist2d": hist2d}[command](*args)
    print("TETHER_RF2_CHILD_OK")


if __name__ == "__main__":
    main(sys.argv[1:])
