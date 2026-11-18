# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Child of ``_campos_chavez_hmm.py``: one tMAVEN fit with the settings of the article (#757).

Runs under a sidecar interpreter only, never in the test process. Usage::

    python _campos_chavez_hmm_child.py RUNNER SMD GROUP KEY NSTATES OUT SEED PREFS_JSON

``RUNNER`` is Tether's ``_sidecar_runner.py``. With the pinned tMAVEN the fit *is* that runner's
``run``: the only change is that each new ``maven_class`` also takes ``PREFS_JSON`` (the priors
the Methods give), which the runner has no argument for, and that ``KEY`` may be ``vbgmm``,
tMAVEN's plain vbGMM, which the runner has no key for (ADR-0106).

Older tMAVEN (0.2.x) has no ``run_vbconhmm``; its routines are ``run_fret_*(nstates)``. The same
SMD, prefs and seeding then drive that API, so the version comparison of the limits file differs
by the tMAVEN code alone.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("NAPARI_ASYNC", "0")

#: Plain vbGMM is ``run_vbgmm`` with ``modeler.vbgmm.nstates`` (the Two-state population).
EXTRA_KEYS = {"vbgmm": ("run_vbgmm", "modeler.vbgmm.nstates")}
OLD_ROUTINES = {
    "vbconhmm": "run_fret_vbconhmm",
    "kmeans_vbhmm": "run_fret_kmeans_vbhmm",
    "vbgmm": "run_fret_vbgmm",
}


def _runner(path: str):
    spec = importlib.util.spec_from_file_location("_tether_sidecar_runner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv: list[str]) -> int:
    runner_path, smd, group, key, nstates, out, seed, prefs = argv[1:9]
    prefs = json.loads(prefs)
    runner = _runner(runner_path)
    import tmaven.maven as tm

    if hasattr(tm.maven_class().modeler, "run_vbconhmm"):
        base = tm.maven_class

        class WithPrefs(base):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                for name, value in prefs.items():
                    self.prefs[name] = value  # prefs_object wraps each value; no dict.update

        tm.maven_class = WithPrefs
        for name, entry in EXTRA_KEYS.items():
            runner._DISPATCH.setdefault(name, entry)
        status = runner.run(smd, group, key, int(nstates), out, None, int(seed))
    else:
        maven = tm.maven_class()
        maven.io.load_smdtmaven_hdf5(smd, group)
        maven.prefs["ncpu"] = 1
        for name, value in prefs.items():
            maven.prefs[name] = value
        runner.seed_random_streams(int(seed))
        getattr(maven.modeler, OLD_ROUTINES[key])(int(nstates))
        if os.path.exists(out):
            os.remove(out)
        maven.modeler.export_result_to_hdf5(out)
        status = {"ok": True, "result_type": str(maven.modeler.model.type)}
    sys.stdout.write(runner.STATUS_PREFIX + json.dumps(status) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
