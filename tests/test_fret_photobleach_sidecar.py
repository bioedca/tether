# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Oracle (g), live leg: the photobleach detector and its ``_ref_*`` oracle vs tMAVEN itself.

``tests/test_fret_photobleach.py`` checks :mod:`tether.fret.photobleach` against ``_ref_*``
helpers, a line-for-line transcription of tMAVEN's ``photobleaching.py``. tMAVEN is not
installed on the required matrix, so an error in that transcription would not be caught
(``docs/validation.md`` (g)). Here a child process under ``$TETHER_SIDECAR_PYTHON`` runs
tMAVEN's own ``normal_ln_evidence``, ``normal_mu_ln_evidence``, ``ln_likelihood``,
``get_point_pbtime`` and ``pb_ensemble`` with tMAVEN's own default priors, and this process
compares Tether *and* the ``_ref_*`` helpers with what it returns (#706).

Inputs: the synthetic traces of the unmarked tests (same seeds, same draws) and the
donor, acceptor and summed channels of the committed 4-molecule ``smd_4mol.hdf5``.

Bleach frames and the ensemble rate ``e_k`` must be **equal**. Log evidence must agree to a
relative limit. Measured 2026-11-11 on these inputs (tMAVEN ``10f4230``, CPython 3.12.13,
NumPy 1.26.4, SciPy 1.17.1, Numba 0.60.0): helpers vs tMAVEN at most 7.1e-16 (the same
O(T^2) sums), Tether's O(T) prefix sums vs tMAVEN at most 6.6e-14 (``seed3_signal``,
a trace that never bleaches).
"""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import h5py
import numpy as np
import pytest

from _sidecar_gate import SIDECAR as _SIDECAR
from _sidecar_gate import requires_sidecar
from test_fret_photobleach import (  # noqa: PLC2701 - the transcription under test
    _PRIORS,
    _ref_get_point_pbtime,
    _ref_ln_likelihood,
    _ref_normal_ln_evidence,
    _ref_normal_mu_ln_evidence,
    _ref_pb_ensemble,
    _step_trace,
)
from tether.fret.photobleach import _ln_likelihood as _fast_ln_likelihood
from tether.fret.photobleach import detect_photobleach, ensemble_pbtime, point_pbtime

pytestmark = pytest.mark.sidecar

SMD_4MOL = Path(__file__).parent / "fixtures" / "smd_4mol.hdf5"
REF_RTOL = 1e-14  # measured at most 7.1e-16
TETHER_RTOL = 1e-12  # measured at most 6.6e-14

# Runs under $TETHER_SIDECAR_PYTHON. Only tMAVEN's code computes here: its functions, and
# the priors from its own photobleach preferences rather than Tether's constants.
_CHILD = textwrap.dedent(
    """
    import sys
    import numpy as np
    from tmaven.controllers.photobleaching import photobleaching as pb
    from tmaven.controllers.photobleaching.photobleaching_controller import default_prefs

    lines = default_prefs.split("[photobleach.prior]")[1].splitlines()
    prior = dict((k.strip(), float(v)) for k, v in (ln.split("=") for ln in lines if "=" in ln))
    p = (prior["a"], prior["b"], prior["beta"], prior["mu"])
    src, out = np.load(sys.argv[1]), {"priors": np.array(p)}
    for key in src.files:
        x = np.ascontiguousarray(src[key], dtype=np.float64)
        if x.ndim == 2:
            out["ek_" + key], out["pbt_" + key] = pb.pb_ensemble(x, *p)
            continue
        h = x.size // 2
        out["lnl_" + key] = pb.ln_likelihood(x, *p)
        out["pbt_" + key] = np.int64(pb.get_point_pbtime(x, *p))
        out["ev_" + key] = [pb.normal_ln_evidence(s, *p) for s in (x, x[:h])]
        out["evmu_" + key] = [pb.normal_mu_ln_evidence(s, 0.0, p[0], p[1]) for s in (x, x[h:])]
    np.savez(sys.argv[2], **out)
    """
)


def _inputs() -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], np.ndarray]:
    """Single traces, ensemble cohorts, and the raw (4, 1700, 2) fixture."""
    traces: dict[str, np.ndarray] = {}
    for seed, level, noise, count in ((0, 800.0, 60.0, 8), (1, 700.0, 50.0, 12)):
        rng = np.random.default_rng(seed)
        for i in range(count):
            n = int(rng.integers(6, 130))
            k = int(rng.integers(2, n - 1))
            traces[f"seed{seed}_{i}"] = _step_trace(rng, n, k, level=level, noise=noise)
    rng = np.random.default_rng(2)
    for k in (30, 60, 90, 120, 160):
        for j, (level, noise) in enumerate(((1000.0, 40.0), (600.0, 80.0), (1500.0, 120.0))):
            traces[f"seed2_{k}_{j}"] = _step_trace(rng, 200, k, level=level, noise=noise)
    rng = np.random.default_rng(3)
    traces["seed3_signal"] = rng.normal(1000.0, 40.0, size=100)
    traces["seed3_zeros"] = rng.normal(0.0, 30.0, size=100)
    rng = np.random.default_rng(4)
    steps = [_step_trace(rng, 120, int(rng.integers(10, 110)), 800.0, 60.0) for _ in range(9)]
    with h5py.File(SMD_4MOL, "r") as smd:
        raw = np.asarray(smd["dataset/data/raw"], dtype=np.float64)
    for m in range(raw.shape[0]):
        traces[f"mol{m}_donor"] = raw[m, :, 0]
        traces[f"mol{m}_acceptor"] = raw[m, :, 1]
        traces[f"mol{m}_sum"] = raw[m].sum(-1)
    # tMAVEN's photobleach_sum feeds pb_ensemble the summed colours of the whole cohort.
    return traces, {"seed4": np.stack(steps), "mol_sum": raw.sum(-1)}, raw


@pytest.fixture(scope="module")
def live(tmp_path_factory: pytest.TempPathFactory) -> dict:
    traces, cohorts, raw = _inputs()
    work = tmp_path_factory.mktemp("photobleach_live")
    np.savez(work / "in.npz", **traces, **cohorts)
    proc = subprocess.run(
        [str(_SIDECAR), "-c", _CHILD, str(work / "in.npz"), str(work / "out.npz")],
        cwd=work,
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    assert proc.returncode == 0, f"tMAVEN child failed (exit {proc.returncode}):\n{proc.stderr}"
    with np.load(work / "out.npz") as out:
        tm = {key: out[key] for key in out.files}
    return {"traces": traces, "cohorts": cohorts, "raw": raw, "tm": tm}


@requires_sidecar
def test_tmaven_default_priors_are_the_ones_tether_and_the_helpers_use(live: dict) -> None:
    assert tuple(live["tm"]["priors"]) == _PRIORS


@requires_sidecar
def test_ref_helpers_match_live_tmaven(live: dict) -> None:
    """The transcription itself: each ``_ref_*`` helper reproduces tMAVEN's function."""
    tm, a, b = live["tm"], _PRIORS[0], _PRIORS[1]
    for key, d in live["traces"].items():
        h = d.size // 2
        ev = [_ref_normal_ln_evidence(s, *_PRIORS) for s in (d, d[:h])]
        evmu = [_ref_normal_mu_ln_evidence(s, 0.0, a, b) for s in (d, d[h:])]
        np.testing.assert_allclose(ev, tm["ev_" + key], rtol=REF_RTOL, atol=0, err_msg=key)
        np.testing.assert_allclose(evmu, tm["evmu_" + key], rtol=REF_RTOL, atol=0, err_msg=key)
        lnl = _ref_ln_likelihood(d, *_PRIORS)
        np.testing.assert_allclose(lnl, tm["lnl_" + key], rtol=REF_RTOL, atol=0, err_msg=key)
        assert _ref_get_point_pbtime(d, *_PRIORS) == tm["pbt_" + key], key
    for key, x in live["cohorts"].items():
        e_k, pbt = _ref_pb_ensemble(x, *_PRIORS)
        assert e_k == tm["ek_" + key], key
        np.testing.assert_array_equal(pbt, tm["pbt_" + key], err_msg=key)


@requires_sidecar
def test_tether_detector_matches_live_tmaven(live: dict) -> None:
    """Tether's O(T) detector, at its default priors, reproduces tMAVEN's frames."""
    tm = live["tm"]
    for key, d in live["traces"].items():
        lnl = _fast_ln_likelihood(d, *_PRIORS)
        np.testing.assert_allclose(lnl, tm["lnl_" + key], rtol=TETHER_RTOL, atol=0, err_msg=key)
        assert point_pbtime(d) == tm["pbt_" + key], key
    for key, x in live["cohorts"].items():
        e_k, pbt = ensemble_pbtime(x)
        assert e_k == tm["ek_" + key], key
        np.testing.assert_array_equal(pbt, tm["pbt_" + key], err_msg=key)
    for m, mol in enumerate(live["raw"]):
        res = detect_photobleach(mol[:, 0], mol[:, 1])
        frames = tuple(int(tm[f"pbt_mol{m}_{c}"]) for c in ("donor", "acceptor", "sum"))
        assert (res.donor_pb, res.acceptor_pb, res.sum_pb) == frames, m
        assert res.window == (0, frames[2]), m
