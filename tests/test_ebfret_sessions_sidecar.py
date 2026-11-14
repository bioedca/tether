# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tether's ebFRET path against stored sessions of the MATLAB ebFRET program (#719, report only).

The ebFRET gate in ``test_parity_sidecar.py`` compares Tether with itself (two seeds, ADR-0075).
This module compares it with a result from outside Tether and tMAVEN: the 11 ebFRET session
files of Zenodo record 13910538 (CC-BY-4.0), packed into ``ebfret-mrec-sessions`` of
``tests/reference_data/manifest.toml`` by ``scripts/make_ebfret_sessions_fixture.py``. For each
condition Tether fits the traces of the session, with the session's crop, number of states and
number of restarts, through the sidecar (``run_ebhmm``, seed 1), and the test prints one table:
state means against the session's empirical-Bayes prior means, and state populations against the
session's expected occupancy.

**It only reports** (maintainer decision R6: A, #695). No numeric difference can fail it, because
two implementations of one method with different starting points and stopping rules give no
agreement band to hold them to. It fails when the data cannot be read or the fit does not run:
a missing or changed fixture, a condition with no series, or a fit that is not a ``K``-state
``eb HMM``. The measured differences are in ``docs/validation.md``.

The conditions are fitted in parallel, one sidecar process each, because the 11 fits take about
15 minutes one after another. Each fit is seeded, so the result does not depend on the order.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from _sidecar_gate import requires_sidecar
from reference_data.fetch import path_for

pytestmark = pytest.mark.sidecar

DATASET = "ebfret-mrec-sessions"
#: The committed file of :data:`DATASET`, named here so that the sidecar path filter's contract
#: (``tests/test_marker_contract.py``) sees it as an input of this module.
FIXTURE = "ebfret_mrec_sessions.hdf5"
#: The seed of every fit (the first of the ebFRET gate's seeds, ADR-0075), so the printed
#: differences are the same on every run of a commit.
SEED = 1
#: The type tMAVEN writes for an ebFRET model.
EBHMM_TYPE = "eb HMM"


def _load_sessions() -> dict[str, dict]:
    """Every condition of the fixture; fails on a fixture that is missing, changed or empty."""
    import h5py

    path = path_for(DATASET)
    assert path.name == FIXTURE, path
    from_file: dict[str, dict] = {}
    with h5py.File(path, "r") as h5:
        for figure in h5:
            for condition in h5[figure]:
                group = h5[figure][condition]
                entry = {name: group[name][()] for name in group}
                entry.update(nstates=int(group.attrs["nstates"]))
                entry.update(restarts=int(group.attrs["restarts"]))
                from_file[f"{figure}/{condition}"] = entry
    return from_file


def _fit(key: str, session: dict, workdir) -> dict:
    """Fit one condition through the sidecar; return the sorted means and populations."""
    from tether.idealize import run_ebhmm, write_smd

    raw = np.stack([session["donor"], session["acceptor"]], axis=-1)
    n = raw.shape[0]
    smd = write_smd(
        workdir / f"{key.replace('/', '__')}.hdf5",
        raw,
        classes=np.zeros(n, dtype=np.int64),
        pre_list=session["crop_min"],
        post_list=session["crop_max"],
    )
    result = run_ebhmm(
        smd,
        nstates=session["nstates"],
        nrestarts=session["restarts"],
        seed=SEED,
        timeout=3600.0,
    )
    model = result.model
    assert model.model_type == EBHMM_TYPE, key
    assert model.nstates == session["nstates"], key
    assert model.frac is not None and model.frac.size == model.nstates, key
    order = np.argsort(model.means)
    return {"means": model.means[order], "population": model.frac[order], "nmol": n}


def _table(key: str, session: dict, fit: dict) -> str:
    order = np.argsort(session["prior_mu"])
    ref_mu, ref_pop = session["prior_mu"][order], session["population"][order]
    lines = [
        f"\n[ebfret-sessions] {key}: K={session['nstates']}, {fit['nmol']} series, seed {SEED}",
        "  state  ebFRET mean  Tether mean  diff     ebFRET pop  Tether pop  diff",
    ]
    for k in range(session["nstates"]):
        d_mu = fit["means"][k] - ref_mu[k]
        d_pop = fit["population"][k] - ref_pop[k]
        lines.append(
            f"  {k + 1:>5}  {ref_mu[k]:11.4f}  {fit['means'][k]:11.4f}  {d_mu:+.4f}  "
            f"{ref_pop[k]:10.4f}  {fit['population'][k]:10.4f}  {d_pop:+.4f}"
        )
    return "\n".join(lines)


@requires_sidecar
def test_ebfret_path_reports_its_difference_from_stored_ebfret_sessions(tmp_path, capsys):
    sessions = _load_sessions()
    assert len(sessions) == 11, sorted(sessions)
    for key, session in sessions.items():
        assert session["length"].size > 0, f"{key}: no series"
        assert session["prior_mu"].size == session["nstates"], key
        assert np.isclose(session["population"].sum(), 1.0), key

    workers = max(1, min(4, os.cpu_count() or 1))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {key: pool.submit(_fit, key, s, tmp_path) for key, s in sessions.items()}
        fits = {key: future.result() for key, future in futures.items()}

    # Report only: the tables go to the job log, past pytest's capture of a passing test.
    with capsys.disabled():
        for key, session in sessions.items():
            print(_table(key, session, fits[key]), flush=True)
