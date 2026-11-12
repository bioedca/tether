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

    _sidecar_runner.py [--seed N] <smd_path> <group> <model_type> <nstates> <model_out> [nrestarts]

It loads the SMD via tMAVEN's own loader, drives a vbFRET-family idealization
through ``tmaven.maven.maven_class`` *headlessly* (``maven_class.__init__``
builds plain objects; it does not spawn a Qt app), and writes the resulting
model with tMAVEN's own serializer (the Appendix D.2 ``model`` group). The
driver reads that file back in the base env.

``--seed N`` (issue #311) makes the fit reproducible. Without it tMAVEN draws its
starting points from two sources this process does not otherwise control (see
:func:`seed_random_streams`), so two fits of one file differ; that is tMAVEN's
own behaviour and stays the default.

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


def seed_random_streams(seed):
    """Make every random draw of a tMAVEN fit in this process follow ``seed``.

    tMAVEN (pinned 10f4230) has no seed setting. Its ebFRET path draws from two
    random streams, and neither is seeded by anything the caller sets (issue #311):

    1. **Numba's own generator.** ``hmm_eb.eb_outer_loop`` is compiled with
       ``nopython=True`` and picks each restart's starting means with
       ``np.random.rand``. In compiled code that call uses Numba's internal
       generator, which Numba seeds from operating-system entropy and which a
       ``np.random.seed`` call made from ordinary Python does not reach. Only a
       ``np.random.seed`` call made *inside* compiled code seeds it.
    2. **NumPy's global generator, re-seeded from entropy.** The per-trace pass
       after the empirical-Bayes fit calls ``fxns.initializations.initialize_gmm``
       once per molecule, which calls ``np.random.seed()`` with no argument (a fresh
       draw from operating-system entropy) and then samples the starting means from
       a Gaussian KDE. Seeding NumPy once is undone at the first molecule.

    This seeds (1) from compiled code, seeds (2), and replaces ``np.random.seed`` in
    this process so that a call **with no argument** re-seeds from the next child of
    ``numpy.random.SeedSequence(seed)`` instead of from entropy. Calls that pass an
    explicit seed (tMAVEN's ``clip_traces`` uses ``666``) are passed through
    unchanged. The process is a short-lived child that runs one fit, so the patch
    does not reach any other code. Fitting is single-process (``ncpu = 1``) and
    ``eb_outer_loop`` is compiled with ``parallel=False``, so there is one thread and
    one stream of each kind.
    """
    import numba
    import numpy as np

    seed = int(seed)
    if seed < 0:
        raise ValueError(f"seed must be a non-negative integer, got {seed}")

    # NumPy's own function, even if an earlier call in this process wrapped it. Numba
    # types the call by the function object, so the compiled seeder names it directly.
    original_seed = getattr(np.random.seed, "_tether_original", np.random.seed)

    @numba.njit(cache=False)
    def _seed_numba(value):  # pragma: no cover - compiled code
        original_seed(value)

    _seed_numba(seed % (2**32))

    children = np.random.SeedSequence(seed)
    counter = [0]

    def _reproducible_seed(value=None):
        if value is None:
            child = np.random.SeedSequence(children.entropy, spawn_key=(counter[0],))
            counter[0] += 1
            value = int(child.generate_state(1)[0])
        original_seed(value)

    _reproducible_seed._tether_original = original_seed
    np.random.seed = _reproducible_seed
    original_seed(seed % (2**32))


def run(smd_path, group, model_type, nstates, model_out, nrestarts=None, seed=None):
    """Load ``smd_path``, run ``model_type`` idealization, export to ``model_out``.

    ``seed`` (a non-negative integer, or ``None`` for tMAVEN's own unseeded
    behaviour) makes the fit reproducible; see :func:`seed_random_streams`.

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
    if seed is not None:
        seed_random_streams(seed)

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
        "seed": None if seed is None else int(seed),
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


def load_check(smd_path, group):
    """Open an SMD with tMAVEN's OWN loader and report what the GUI would see.

    Drives ``maven.io.load_smdtmaven_hdf5`` — the exact loader the standalone tMAVEN
    GUI's *File → Load SMD* menu uses (``load_smd_hdf5`` → ``pysmd.load_smd_in_hdf5``
    for the ``data``/``sources`` groups, plus ``load_tmaven_hdf5`` for the per-trace
    ``tMAVEN`` classes/windows) — so a Tether-authored SMD that loads cleanly here is
    one that opens in the GUI (PRD §7.4, issue #13). Runs no fit.

    Returns a JSON-serializable status with the molecule/frame counts tMAVEN parsed,
    the analysis-window arrays it read back (``pre_list``/``post_list`` — the per-trace
    windows Tether rides along), and a raw-intensity checksum, so the caller can assert
    the trace data survived the hand-off intact. tMAVEN's loader ignores Tether's
    ``tether/`` coordinate superset group (verified separately on the base side); this
    check proves the *standard* SMD the GUI reads is well-formed and complete.
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("NAPARI_ASYNC", "0")

    import numpy as np
    from tmaven.maven import maven_class

    maven = maven_class()
    maven.io.load_smdtmaven_hdf5(smd_path, group)

    data = maven.data
    nmol = int(data.nmol)
    if nmol < 1:
        raise RuntimeError(f"tMAVEN loaded 0 molecules from {smd_path!r}/{group!r}")
    raw = np.asarray(data.raw, dtype="float64")
    return {
        "ok": True,
        "load_check": True,
        "nmol": nmol,
        "nt": int(data.nt),
        "ncolors": int(data.ncolors),
        "raw_shape": [int(s) for s in raw.shape],
        "raw_sum": float(np.nansum(raw)),
        "pre_list": [int(x) for x in np.asarray(data.pre_list).reshape(-1)],
        "post_list": [int(x) for x in np.asarray(data.post_list).reshape(-1)],
        "classes": [int(x) for x in np.asarray(data.classes).reshape(-1)],
    }


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
    if args and args[0] == "--load-check":
        rest = args[1:]
        if len(rest) != 2:
            sys.stderr.write("usage: _sidecar_runner.py --load-check <smd_path> <group>\n")
            return 2
        try:
            status = load_check(rest[0], rest[1])
        except Exception as exc:
            sys.stdout.write(
                STATUS_PREFIX
                + json.dumps({"ok": False, "load_check": True, "error": str(exc)})
                + "\n"
            )
            sys.stdout.flush()
            return 1
        sys.stdout.write(STATUS_PREFIX + json.dumps(status) + "\n")
        sys.stdout.flush()
        return 0
    seed = None
    if args and args[0] == "--seed":
        if len(args) < 2:
            sys.stderr.write("usage: _sidecar_runner.py --seed N <smd_path> ...\n")
            return 2
        seed = args[1]
        args = args[2:]
    if len(args) not in (5, 6):
        sys.stderr.write(
            "usage: _sidecar_runner.py [--seed N] <smd_path> <group> <model_type> "
            "<nstates> <model_out> [nrestarts]\n"
            "   or: _sidecar_runner.py --probe\n"
            "   or: _sidecar_runner.py --load-check <smd_path> <group>\n"
        )
        return 2
    smd_path, group, model_type, nstates, model_out = args[:5]
    nrestarts = args[5] if len(args) == 6 else None
    try:
        status = run(
            smd_path,
            group,
            model_type,
            int(nstates),
            model_out,
            nrestarts,
            None if seed is None else int(seed),
        )
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
