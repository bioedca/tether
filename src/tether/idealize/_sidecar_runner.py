# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless tMAVEN runner — executed *inside the isolated sidecar env*.

This module is **not** imported by the Tether base package. It is launched as a
standalone script by :mod:`tether.idealize.driver` using the sidecar
interpreter (PRD §4.3), so its only imports are ones present in the sidecar
lock (``tmaven`` + ``numpy`` + ``h5py`` + the stdlib). It must stay
Python-3.9-compatible (the sidecar pins py3.9 / numpy<2 / PyQt5), so it avoids
3.10+ syntax.

Contract (argv):

    _sidecar_runner.py <smd_path> <group> <model_type> <nstates> <model_out> [nrestarts]

It loads the SMD via tMAVEN's own loader, drives a vbFRET-family idealization
through ``tmaven.maven.maven_class`` *headlessly* (``maven_class.__init__``
builds plain objects; it does not spawn a Qt app), and writes the resulting
model with tMAVEN's own serializer (the Appendix D.2 ``model`` group). The
driver reads that file back in the base env.

The last stdout line is a one-line JSON status prefixed with :data:`STATUS_PREFIX`
so the driver can recover it from amid tMAVEN's verbose logging.
"""

from __future__ import annotations

import json
import os
import sys

#: Sentinel prefixing the JSON status line on stdout (tMAVEN logs verbosely).
STATUS_PREFIX = "TETHER_SIDECAR_STATUS "

#: model_type -> (maven.modeler method name, nstates pref key | None).
#: ``vbconhmm`` (global consensus VB-HMM) is the default: it is the idealizer
#: behind the reference model fixture (Appendix D.2 ``@type='vb Consensus HMM'``)
#: and the M0.5 S4 parity target. ``ebhmm`` is the ebFRET empirical-Bayes HMM
#: [vandeMeent2014] — the second global/population idealizer the M6 analysis
#: suite offers (PRD §4.2, §10); it pools information across molecules to infer a
#: consensus kinetic model and writes the same Appendix-D.2 ``model`` group.
_DISPATCH = {
    "vbconhmm": ("run_vbconhmm", "modeler.vbconhmm.nstates"),
    "vbconhmm_modelselection": ("run_vbconhmm_modelselection", None),
    "ebhmm": ("run_ebhmm", "modeler.ebhmm.nstates"),
    "ebhmm_modelselection": ("run_ebhmm_modelselection", None),
    "vbgmm_vbhmm": ("run_vbgmm_vbhmm_modelselection", None),
    "kmeans_vbhmm": ("run_kmeans_vbhmm", "modeler.vbhmm.nstates"),
}


def run(smd_path, group, model_type, nstates, model_out, nrestarts=None):
    """Load ``smd_path``, run ``model_type`` idealization, export to ``model_out``.

    Returns the status dict that is also emitted as JSON on stdout.
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("NAPARI_ASYNC", "0")

    if model_type not in _DISPATCH:
        raise ValueError(f"unknown model_type {model_type!r}; known: {sorted(_DISPATCH)}")
    method_name, nstates_key = _DISPATCH[model_type]

    from tmaven.maven import maven_class

    maven = maven_class()
    maven.io.load_smdtmaven_hdf5(smd_path, group)

    nmol = int(maven.data.nmol)
    nt = int(maven.data.nt)
    if nmol < 1:
        raise RuntimeError(f"SMD {smd_path!r}/{group!r} loaded 0 molecules")

    maven.prefs["modeler.dtype"] = "FRET"
    # Force single-process fitting. tMAVEN's ``*_parallel`` modelers spawn one
    # worker per CPU, and each fresh worker re-JIT-compiles the Numba kernels
    # from cold — on Windows that recompile storm dominates the runtime for the
    # small molecule counts we drive here. ncpu=1 pays the compile exactly once.
    maven.prefs["ncpu"] = 1
    if nstates_key is not None:
        maven.prefs[nstates_key] = int(nstates)
    if nrestarts is not None:
        maven.prefs["modeler.nrestarts"] = int(nrestarts)

    getattr(maven.modeler, method_name)()
    model = maven.modeler.model
    if model is None:
        raise RuntimeError("idealization produced no model (no usable traces?)")

    if os.path.exists(model_out):
        os.remove(model_out)
    maven.modeler.export_result_to_hdf5(model_out)

    return {
        "ok": True,
        "nmol": nmol,
        "nt": nt,
        "model_type": model_type,
        "method": method_name,
        "result_type": str(model.type),
        "nstates": int(model.nstates),
        "model_out": os.path.abspath(model_out),
    }


def probe():
    """Liveness check: confirm the sidecar env can build the tMAVEN driver.

    Imports and instantiates ``maven_class`` (which builds plain objects and does not
    spawn a Qt app — the M0.5 S1 recon) so a sidecar env that is *present but broken*
    (``tmaven`` missing or unimportable) is caught by the batch runner's startup probe
    before it commits to idealizing. Runs no fit.
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("NAPARI_ASYNC", "0")

    from tmaven.maven import maven_class

    maven_class()
    return {"ok": True, "probe": True, "detail": "tmaven maven_class ready"}


def main(argv):
    args = argv[1:]  # drop the script name (argv[0])
    if args and args[0] == "--probe":
        try:
            status = probe()
        except Exception as exc:
            sys.stdout.write(
                STATUS_PREFIX + json.dumps({"ok": False, "probe": True, "error": str(exc)}) + "\n"
            )
            sys.stdout.flush()
            return 1
        sys.stdout.write(STATUS_PREFIX + json.dumps(status) + "\n")
        sys.stdout.flush()
        return 0
    if len(args) not in (5, 6):
        sys.stderr.write(
            "usage: _sidecar_runner.py <smd_path> <group> <model_type> "
            "<nstates> <model_out> [nrestarts]\n"
            "   or: _sidecar_runner.py --probe\n"
        )
        return 2
    smd_path, group, model_type, nstates, model_out = args[:5]
    nrestarts = args[5] if len(args) == 6 else None
    try:
        status = run(smd_path, group, model_type, int(nstates), model_out, nrestarts)
    except Exception as exc:
        # Any failure is reported to the driver as a JSON status, not a traceback.
        sys.stdout.write(STATUS_PREFIX + json.dumps({"ok": False, "error": str(exc)}) + "\n")
        sys.stdout.flush()
        return 1
    sys.stdout.write(STATUS_PREFIX + json.dumps(status) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
