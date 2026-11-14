# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Child of ``test_marzano_vbfret_sidecar.py``: tMAVEN's per-trace vbHMM on the #797 traces.

Runs under ``$TETHER_SIDECAR_PYTHON`` only, never in the test process, and outside the product:
Tether's sidecar runner has no per-trace model type, so this test-only script calls tMAVEN's own
per-trace step (the maintainer's choice on #797). In the pinned tMAVEN that step is
``controllers.modeler.hmm_vb.tracelevel_vbhmm_modelselection``, the first stage of its
"vb GMM + vb HMM" and "kmeans + vb HMM" models; the stand-alone per-trace ``run_vbhmm`` methods
are commented out in that build. Usage::

    python _marzano_vbhmm_child.py SMD OUT.npz SEED

Each trace gets its own variational-Bayes HMM for 1 to 5 states and keeps the one with the
highest evidence -- vbFRET's own procedure. The settings of the papers that tMAVEN has are set:
at most 5 states and the prior ``beta = 0.5``. vbFRET's ``mu = 1.5`` has no tMAVEN counterpart
(tMAVEN puts the prior means at percentiles of the data), so it stays tMAVEN's. The seed makes
the fit reproducible on one build (:func:`tether.idealize._sidecar_runner.seed_random_streams`).
"""

from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("NAPARI_ASYNC", "0")

import numpy as np  # noqa: E402
from tmaven.maven import maven_class  # noqa: E402

from tether.idealize._sidecar_runner import seed_random_streams  # noqa: E402

#: The settings of the two papers that tMAVEN has (see the module docstring).
SETTINGS = {
    "modeler.dtype": "FRET",
    "ncpu": 1,
    "modeler.vbhmm.nstates": 5,
    "modeler.vbhmm.prior.beta": 0.5,
}


def main(smd: str, out_path: str, seed: int) -> None:
    m = maven_class()
    m.io.load_smdtmaven_hdf5(smd, "dataset")
    for key, value in SETTINGS.items():
        m.prefs[key] = value
    seed_random_streams(seed)
    from tmaven.controllers.modeler.hmm_vb import tracelevel_vbhmm_modelselection

    modeler, data = m.modeler, m.data
    success, keep, y = modeler.get_traces(m.prefs["modeler.dtype"])
    if not success or not keep.all():
        raise RuntimeError("tMAVEN did not take every trace")
    # Each trace: a VB HMM for k = 1 .. nstates, the k with the highest evidence kept.
    trace_level, idealized, ran = tracelevel_vbhmm_modelselection(
        y,
        keep,
        int(m.prefs["modeler.vbhmm.nstates"]),
        modeler.get_priors,
        modeler.get_model_specs(),
        data.nmol,
        data.nt,
        data.pre_list,
        data.post_list,
        m.prefs["modeler.dtype"],
    )
    nmol = int(data.nmol)
    means = np.full((nmol, 5), np.nan)
    nstates = np.zeros(nmol, dtype=np.int64)
    for i in ran:
        trace = trace_level[str(i)]
        nstates[i] = int(trace.nstates)
        means[i, : trace.nstates] = np.sort(np.asarray(trace.mean, dtype=np.float64))
    np.savez(
        out_path,
        idealized=np.asarray(idealized, dtype=np.float64),
        nstates=nstates,
        means=means,
        settings=np.array(json.dumps({**SETTINGS, "seed": seed}, sort_keys=True)),
    )


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]))
