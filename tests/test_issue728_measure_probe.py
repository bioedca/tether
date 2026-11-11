# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""TEMPORARY measurement probe for #728 (removed before review).

Measures, on each operating system of the required matrix, the agreement behind every
limit the #728 tests set, and reports it as a warning, which the warnings summary of the
CI log prints in full.
"""

from __future__ import annotations

import json
import platform
import tempfile
import warnings
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("h5py")

import h5py  # noqa: E402

import test_fret_gamma as fg  # noqa: E402
import test_fret_leakage as fl  # noqa: E402
import test_fret_photobleach as fp  # noqa: E402
import test_project_gamma as pg  # noqa: E402
import test_project_leakage as pl  # noqa: E402
from tether.fret.gamma import trace_gamma  # noqa: E402
from tether.fret.leakage import tail_alpha  # noqa: E402
from tether.fret.photobleach import _ln_likelihood, ensemble_pbtime  # noqa: E402
from tether.imaging.extract import read_molecules  # noqa: E402
from tether.io.schema import TABLE  # noqa: E402
from tether.project.gamma import compute_gamma  # noqa: E402
from tether.project.leakage import compute_leakage_alpha  # noqa: E402


def _allclose_ratio(fast: np.ndarray, ref: np.ndarray) -> float:
    # Largest |fast - ref| / (atol + rtol*|ref|) for the tests' rtol = atol = 1e-6.
    return float(np.max(np.abs(fast - ref) / (1e-6 + 1e-6 * np.abs(ref))))


def test_issue728_measure() -> None:
    m: dict[str, float] = {}
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        for name, kw in {
            "g_default": {},
            "g_start10": {"start": 10},
            "g_never": {"acc": 40, "don": 120},
        }.items():
            p = d / f"{name}.tether"
            pg._gamma_store(p, n_mol=12, gamma_true=1.2, alpha=0.1, **kw)
            s = compute_gamma(p)
            g = read_molecules(p)["gamma"]
            m[f"{name}_median"] = abs(s.gamma - 1.2)
            m[f"{name}_permol"] = float(np.max(np.abs(g - 1.2)))
        p = d / "g_skip.tether"
        pg._gamma_store(p, n_mol=12, gamma_true=1.2, alpha=0.1)
        with h5py.File(p, "r+") as f:
            t = f["molecules"][TABLE][:]
            t["frame_range"][5] = (50, 50)
            f["molecules"][TABLE][:] = t
        compute_gamma(p)
        m["g_skip_permol"] = float(np.nanmax(np.abs(read_molecules(p)["gamma"] - 1.2)))
        for a in (0.1, 0.3):
            p = d / f"g_nf_{a}.tether"
            pg._gamma_store(p, n_mol=12, gamma_true=1.2, alpha=a, noise=0.0)
            compute_gamma(p)
            m[f"g_noisefree_{a}"] = float(np.max(np.abs(read_molecules(p)["gamma"] - 1.2)))
        for name, kw in {
            "l_default": {},
            "l_start10": {"acc": 30, "don": 110, "start": 10},
            "l_never": {"acc": 30, "don": 120},
            "l_start10_acc100": {"acc": 100, "don": 120, "start": 10},
        }.items():
            p = d / f"{name}.tether"
            pl._cohort_store(p, n_mol=12, alpha=0.1, **kw)
            m[name] = abs(compute_leakage_alpha(p).alpha - 0.1)
        p = d / "l_raw.tether"
        pl._cohort_store(p, n_mol=12, alpha=0.1)
        m["l_raw"] = abs(compute_leakage_alpha(p, intensity_quantity="raw").alpha - 200 / 1100)
    dn, ac = fl._leaky_trace(n=60, acceptor_pb=30, donor_pb=50, alpha=0.1)
    m["fl_tail20"] = abs(tail_alpha(dn, ac, acceptor_pb=30, donor_pb=50).alpha - 0.1)
    dn, ac = fl._leaky_trace(
        n=120, acceptor_pb=30, donor_pb=110, alpha=0.1, donor_level=0.5, fret_level=0.3, noise=0.0
    )
    m["fl_lowcount"] = abs(tail_alpha(dn, ac, acceptor_pb=30, donor_pb=110).alpha - 0.1)
    dn, ac = fg._step_trace(gamma_true=1.0, alpha=0.1, donor_lo=1.0, donor_hi=1.5)
    m["fg_lowcount"] = abs(trace_gamma(dn, ac, alpha=0.1, acceptor_pb=40, donor_pb=100).gamma - 1.0)
    for label, priors in (("def", fp._PRIORS), ("nondef", fp._NON_DEFAULT_PRIORS)):
        rng = np.random.default_rng(0)
        worst = 0.0
        for _ in range(8):
            n = int(rng.integers(6, 130))
            k = int(rng.integers(2, n - 1))
            x = fp._step_trace(rng, n, k, level=800.0, noise=60.0)
            worst = max(
                worst,
                _allclose_ratio(_ln_likelihood(x, *priors), fp._ref_ln_likelihood(x, *priors)),
            )
        m[f"pb_lnl_ratio_{label}"] = worst
        worst = 0.0
        for n_frames in (2, 3, 4, 5):
            rng = np.random.default_rng(10 + n_frames)
            x = fp._step_trace(rng, n_frames, 1, level=900.0, noise=40.0)
            worst = max(
                worst,
                _allclose_ratio(_ln_likelihood(x, *priors), fp._ref_ln_likelihood(x, *priors)),
            )
        m[f"pb_short_ratio_{label}"] = worst
        rng = np.random.default_rng(4)
        traces = np.stack(
            [fp._step_trace(rng, 120, int(rng.integers(10, 110)), 800.0, 60.0) for _ in range(9)]
        )
        e_k, _ = ensemble_pbtime(traces, **fp._prior_kwargs(priors))
        ref_ek, _ = fp._ref_pb_ensemble(traces, *priors)
        m[f"pb_ensemble_ek_rel_{label}"] = abs(e_k - ref_ek) / abs(ref_ek)
    out = {"os": platform.system(), **{k: float(f"{v:.3e}") for k, v in m.items()}}
    warnings.warn("ISSUE728-MEASURE " + json.dumps(out, sort_keys=True), UserWarning, stacklevel=1)
