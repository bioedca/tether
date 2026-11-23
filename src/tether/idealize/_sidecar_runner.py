# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless tMAVEN runner — executed *inside the isolated sidecar env*.

This module is **not** imported by the Tether base package. It is launched as a
standalone script by [`tether.idealize.driver`][] using the sidecar
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
`seed_random_streams`), so two fits of one file differ; that is tMAVEN's
own behaviour and stays the default.

The last stdout line is a one-line JSON status prefixed with `STATUS_PREFIX`
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
#: ``vbgmm`` is tMAVEN's plain variational-Bayes Gaussian mixture with a fixed number
#: of states (issue #1076): no HMM and no model selection. Campos-Chavez et al. 2024
#: fitted their Two-state population with it (ADR-0106); ``vbgmm_vbhmm`` is not that
#: routine, since it chooses the number of states itself.
_DISPATCH = {
    "vbconhmm": ("run_vbconhmm", "modeler.vbconhmm.nstates"),
    "vbconhmm_modelselection": ("run_vbconhmm_modelselection", None),
    "ebhmm": ("run_ebhmm", "modeler.ebhmm.nstates"),
    "ebhmm_modelselection": ("run_ebhmm_modelselection", None),
    "vbgmm_vbhmm": ("run_vbgmm_vbhmm_modelselection", None),
    "kmeans_vbhmm": ("run_kmeans_vbhmm", "modeler.vbhmm.nstates"),
    "vbgmm": ("run_vbgmm", "modeler.vbgmm.nstates"),
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


def vbgmm_lower_bound(x, r, a, b, m, beta, alpha, a0, b0, m0, beta0, alpha0):
    """Return the evidence lower bound of a 1-D variational Gaussian mixture (issue #1337).

    The model is tMAVEN's plain vbGMM (Bronson et al. 2009, doi:10.1016/j.bpj.2009.09.031):
    a Normal-Gamma prior on each state's mean and precision, ``N(m0, 1 / (beta0 lambda))
    Gamma(a0, b0)`` with ``b0`` a rate, a ``Dirichlet(alpha0)`` prior on the weights, and the
    factorized posterior ``q(Z) q(pi) prod_k q(mu_k, lambda_k)`` of the same forms. The terms are
    Bishop's (2006) equations 10.71 to 10.77 written for one dimension, where his Wishart with
    ``nu`` degrees of freedom and scale ``W`` is ``Gamma(nu / 2, 1 / (2 W))``.

    ``r`` is ``q(Z)``; the sufficient statistics come from ``r`` itself, and the posterior
    parameters are any ones of the right form. Evaluated as `vbgmm_outer_loop` does, right after
    the M step from ``r``, it cannot decrease from one iteration to the next (Blei, Kucukelbir
    and McAuliffe 2017, doi:10.1080/01621459.2017.1285773).

    Returns ``[total, data, normal-gamma, dirichlet, assignments]`` (tMAVEN's five columns of
    ``model.likelihood``): the expected log likelihood (10.71), the Normal-Gamma prior minus its
    posterior (10.74 - 10.77), the Dirichlet prior minus its posterior (10.73 - 10.76), and the
    assignment prior minus the assignment entropy (10.72 - 10.75).
    """
    import numpy as np
    from scipy.special import digamma, gammaln

    nk = r.sum(axis=0) + 1e-300
    xbar = (r * x[:, None]).sum(axis=0) / nk
    sk = (r * (x[:, None] - xbar) ** 2).sum(axis=0) / nk

    e_lam = a / b
    e_lnlam = digamma(a) - np.log(b)
    e_lnpi = digamma(alpha) - digamma(alpha.sum())
    log_2pi = np.log(2.0 * np.pi)

    data = 0.5 * np.sum(nk * (e_lnlam - 1.0 / beta - e_lam * (sk + (xbar - m) ** 2) - log_2pi))

    prior_ng = np.sum(
        0.5 * (np.log(beta0) - log_2pi + e_lnlam - beta0 / beta - beta0 * e_lam * (m - m0) ** 2)
        + a0 * np.log(b0)
        - gammaln(a0)
        + (a0 - 1.0) * e_lnlam
        - b0 * e_lam
    )
    gamma_entropy = a - np.log(b) + gammaln(a) + (1.0 - a) * digamma(a)
    post_ng = np.sum(0.5 * e_lnlam + 0.5 * (np.log(beta) - log_2pi) - 0.5 - gamma_entropy)

    def ln_dirichlet_norm(c):
        return gammaln(c.sum()) - gammaln(c).sum()

    dirichlet = (
        ln_dirichlet_norm(alpha0)
        + np.sum((alpha0 - 1.0) * e_lnpi)
        - ln_dirichlet_norm(alpha)
        - np.sum((alpha - 1.0) * e_lnpi)
    )
    assignments = np.sum(r * (e_lnpi - np.log(r + 1e-300)))

    normal_gamma = prior_ng - post_ng
    total = data + normal_gamma + dirichlet + assignments
    return np.array((total, data, normal_gamma, dirichlet, assignments))


def vbgmm_outer_loop(x, mu, var, ppi, maxiters, threshold, priors):
    """Run tMAVEN's ``gmm_vb.outer_loop`` with a correct lower bound and stopping test (#1337).

    It has the same signature, start and return value as the pinned tMAVEN's (10f4230), and the
    same priors and update equations: each state's prior is centred on its k-means start ``mu``, and
    the M step is tMAVEN's ``m_updates`` written in NumPy. Two things differ, and only these:

    1. The bound is `vbgmm_lower_bound`. tMAVEN's ``calc_lowerbound`` subtracts
       ``(xbar - m)**2`` where Bishop's 10.71 adds it, uses the Gamma shape ``a`` where the
       Dirichlet term needs ``alpha``, writes ``log(beta / 2 * pi)`` for ``log(beta / (2 pi))``,
       and pairs the new ``r`` with sufficient statistics of the previous one. Its bound falls at
       every step on ``smd_4mol.hdf5``, so its relative-change test ends the fit at the second
       iteration with the k-means start barely moved.
    2. The bound is evaluated after each M step, where ``r`` and the posterior parameters belong
       together, and the fit stops when its relative increase falls below ``threshold`` (tMAVEN's
       ``modeler.converge``) or at ``maxiters``. The returned ``r`` is the one the returned
       parameters were updated from.

    Rows of the returned ``ll`` past ``iteration`` are zero, as in tMAVEN; ``vb_em_gmm`` keeps
    rows ``0..iteration``.
    """
    import numpy as np
    from scipy.special import digamma

    x = np.asarray(x, dtype="float64")
    mu = np.asarray(mu, dtype="float64")
    var = np.asarray(var, dtype="float64")
    ppi = np.asarray(ppi, dtype="float64")
    maxiters = int(maxiters)
    if maxiters < 1:
        raise ValueError(f"maxiters must be at least 1, got {maxiters}")

    beta0 = priors[0] + np.zeros_like(mu)
    m0 = mu + np.zeros_like(mu)
    a0 = priors[1] + np.zeros_like(mu)
    b0 = priors[2] + np.zeros_like(mu)
    alpha0 = priors[3] + np.zeros_like(mu)

    def m_step(resp):
        nk = resp.sum(axis=0) + 1e-300
        xbar = (resp * x[:, None]).sum(axis=0) / nk
        sk = (resp * (x[:, None] - xbar) ** 2).sum(axis=0) / nk
        beta = beta0 + nk
        m = (beta0 * m0 + nk * xbar) / beta
        a = a0 + nk / 2.0
        b = b0 + 0.5 * (nk * sk + beta0 * nk / (beta0 + nk) * (xbar - m0) ** 2)
        alpha = alpha0 + nk
        return a, b, m, beta, alpha

    # tMAVEN's start: responsibilities from the k-means Gaussians, then one M step.
    log_p = -0.5 * np.log(2.0 * np.pi * var) - 0.5 * (x[:, None] - mu) ** 2 / var
    r = ppi * (np.exp(log_p) + 1e-300)
    r /= r.sum(axis=1, keepdims=True) + 1e-10
    a, b, m, beta, alpha = m_step(r)

    ll = np.zeros((maxiters, 5))
    iteration = 0
    previous = -np.inf
    while True:
        ll[iteration] = vbgmm_lower_bound(x, r, a, b, m, beta, alpha, a0, b0, m0, beta0, alpha0)
        current = ll[iteration, 0]
        if np.isnan(current):
            break
        if iteration > 0 and (current - previous) / np.abs(previous) < threshold:
            break
        if iteration + 1 >= maxiters:
            break
        previous = current

        # E step (Bishop 10.46-10.50), shifted by the row maximum before exponentiating.
        e_lnlam = digamma(a) - np.log(b)
        e_lnpi = digamma(alpha) - digamma(alpha.sum())
        e_dist = 1.0 / beta + (a / b) * (x[:, None] - m) ** 2
        log_rho = e_lnpi + 0.5 * e_lnlam - 0.5 * np.log(2.0 * np.pi) - 0.5 * e_dist
        log_rho -= log_rho.max(axis=1, keepdims=True)
        r = np.exp(log_rho)
        r /= r.sum(axis=1, keepdims=True)

        a, b, m, beta, alpha = m_step(r)
        iteration += 1

    e_lnlam = digamma(a) - np.log(b)
    e_lnpi = digamma(alpha) - digamma(alpha.sum())
    return r, a, b, m, beta, alpha, e_lnlam, e_lnpi, ll, iteration


def use_corrected_vbgmm():
    """Make tMAVEN's plain vbGMM in this process run `vbgmm_outer_loop` (issue #1337, ADR-0139).

    ``gmm_vb.vb_em_gmm`` looks ``outer_loop`` up in its module when it is called, so replacing
    that one name changes the loop and leaves tMAVEN's start (``initialize_gmm``), priors,
    result container and serializer as they are. Only the ``vbgmm`` key calls this; every other
    key runs tMAVEN unchanged. The process is a short-lived child that runs one fit.
    """
    from tmaven.controllers.modeler import gmm_vb

    gmm_vb.outer_loop = vbgmm_outer_loop


def run(
    smd_path,
    group,
    model_type,
    nstates,
    model_out,
    nrestarts=None,
    seed=None,
    tmaven_vbgmm_loop=False,
):
    """Load ``smd_path``, run ``model_type`` idealization, export to ``model_out``.

    ``seed`` (a non-negative integer, or ``None`` for tMAVEN's own unseeded
    behaviour) makes the fit reproducible; see `seed_random_streams`.

    The ``vbgmm`` key runs with Tether's corrected loop (`use_corrected_vbgmm`, #1337).
    ``tmaven_vbgmm_loop=True`` keeps the pinned tMAVEN's own loop, early stop included; it is
    there for a check that reproduces a fit published with tMAVEN (ADR-0106) and is not
    reachable from the command line.

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
    corrected_vbgmm = model_type == "vbgmm" and not tmaven_vbgmm_loop
    if corrected_vbgmm:
        use_corrected_vbgmm()

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
        # Which vbGMM loop ran: Tether's corrected one (#1337), tMAVEN's own, or none.
        "vbgmm_loop": (
            ("tether" if corrected_vbgmm else "tmaven") if model_type == "vbgmm" else None
        ),
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
