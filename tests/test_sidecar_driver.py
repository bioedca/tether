# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Live tMAVEN sidecar round-trip (``@pytest.mark.sidecar`` — deselected from CI).

These run the *real* headless ``maven_class`` vbFRET pipeline in the isolated
sidecar env, so they need an interpreter in ``$TETHER_SIDECAR_PYTHON`` (an env
built from ``sidecar/conda-lock.yml`` with tMAVEN installed). CI has no such env
(M0.5 S1), so the whole module is excluded by ``-m "not ... and not sidecar"``;
a developer runs it as the de-risk check. They are slow (cold Numba compile).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest

from _sidecar_gate import requires_sidecar
from _vbgmm_reference import VbgmmDisagreement, deviations, disagreement, load_reference
from tether.idealize import NO_STATE, read_smd, run_vbfret, states_from_idealized, write_smd

pytestmark = pytest.mark.sidecar

FIXTURES = Path(__file__).parent / "fixtures"
FOUR_MOL = FIXTURES / "smd_4mol.hdf5"


@requires_sidecar
def test_vbfret_roundtrip_on_4mol(tmp_path):
    # Copy the fixture so the model file is written in the tmp dir, not beside it.
    smd = tmp_path / "smd_4mol.hdf5"
    shutil.copyfile(FOUR_MOL, smd)

    result = run_vbfret(smd, nstates=2, nrestarts=1, timeout=1800.0)

    # Status round-trip from the sidecar.
    assert result.status["ok"] is True
    assert result.status["nmol"] == 4
    assert result.status["nt"] == 1700

    # The fitted ensemble model is the requested one: the default consensus VB-HMM with the
    # requested 2 states (a runner that ignored ``nstates`` fails here, #737 IDZ-N2).
    model = result.model
    assert model.model_type == "vb Consensus HMM"
    assert result.status["result_type"] == "vb Consensus HMM"
    assert model.nstates == 2
    assert result.status["nstates"] == 2
    assert model.means.size == model.nstates
    assert np.all(np.isfinite(model.means))
    assert model.idealized is not None
    assert model.idealized.shape == (4, 1700)
    assert model.elbo is not None  # ELBO trace present

    # Per-molecule state paths + dwells were derived for every fitted molecule: each path
    # is the state path of that molecule's own idealized row, and the dwells rebuild exactly
    # those paths (a driver that gave every molecule one row's path fails, #737 IDZ-F7).
    assert sorted(result.state_paths) == [0, 1, 2, 3]
    for mol_idx, path in result.state_paths.items():
        assert path.shape == (1700,)
        assert set(np.unique(path)).issubset(set(range(model.nstates)) | {NO_STATE})
        expected = states_from_idealized(model.idealized[mol_idx], model.means)
        np.testing.assert_array_equal(path, expected)
    assert result.dwells, "expected at least one dwell segment"
    rebuilt = {mol_idx: np.full(1700, NO_STATE, dtype="int64") for mol_idx in result.state_paths}
    for d in result.dwells:
        assert 0 <= d.state < model.nstates
        assert d.length >= 1
        assert d.start >= 0
        segment = rebuilt[d.molecule_index][d.start : d.start + d.length]
        assert np.all(segment == NO_STATE), "two dwells cover the same frame"
        segment[:] = d.state
    for mol_idx, path in result.state_paths.items():
        np.testing.assert_array_equal(rebuilt[mol_idx], path)

    # The model file is a real Appendix-D.2 artifact on disk.
    assert result.model_path.exists()


@requires_sidecar
def test_vbfret_carries_molecule_keys(tmp_path):
    # Build an SMD with a Tether superset so the result maps back by molecule_key.
    src = read_smd(FOUR_MOL)
    keys = [f"mol-{i:03d}" for i in range(src.n_molecules)]
    smd = tmp_path / "keyed.hdf5"
    write_smd(
        smd,
        src.raw,
        classes=src.classes,
        pre_list=src.pre_list,
        post_list=src.post_list,
        donor_xy=np.zeros((src.n_molecules, 2)),
        acceptor_xy=np.ones((src.n_molecules, 2)),
        molecule_keys=keys,
    )

    result = run_vbfret(smd, nstates=2, nrestarts=1, timeout=1800.0)
    assert result.molecule_keys == keys
    # Every keyed molecule was fitted, so each carried store key has a state path.
    assert sorted(result.state_paths) == list(range(len(keys)))


@requires_sidecar
def test_vbfret_unknown_model_type_errors(tmp_path):
    from tether.idealize import SidecarError

    smd = tmp_path / "smd_4mol.hdf5"
    shutil.copyfile(FOUR_MOL, smd)
    with pytest.raises(SidecarError, match="unknown model_type"):
        run_vbfret(smd, model_type="not_a_model", timeout=300.0)


@requires_sidecar
def test_vbgmm_key_fits_the_requested_states_reproducibly(tmp_path):
    # ``vbgmm`` is tMAVEN's plain vbGMM (#1076): a fixed number of states, no HMM and no model
    # selection. Three states is not tMAVEN's default of two, so a runner that dropped the
    # ``modeler.vbgmm.nstates`` pref, or dispatched to ``vbgmm_vbhmm`` (which chooses the
    # number itself and returns a vbHMM), fails here.
    smd = tmp_path / "smd_4mol.hdf5"
    shutil.copyfile(FOUR_MOL, smd)

    fits = [
        run_vbfret(
            smd,
            model_type="vbgmm",
            nstates=3,
            nrestarts=1,
            seed=1076,
            model_out=tmp_path / f"vbgmm_{run}.hdf5",
            timeout=1800.0,
        )
        for run in range(2)
    ]
    for result in fits:
        assert result.status["ok"] is True
        assert result.status["method"] == "run_vbgmm"
        assert result.status["result_type"] == "vb GMM"
        assert result.status["nstates"] == 3
        model = result.model
        assert model.model_type == "vb GMM"
        assert model.nstates == 3
        assert model.means.shape == (3,)
        assert np.all(np.isfinite(model.means))
        assert model.idealized is not None
        assert model.idealized.shape == (4, 1700)
        assert sorted(result.state_paths) == [0, 1, 2, 3]
        for mol_idx, path in result.state_paths.items():
            expected = states_from_idealized(model.idealized[mol_idx], model.means)
            np.testing.assert_array_equal(path, expected)

    # The same SMD, settings and seed give the same model (the ``--seed`` contract, ADR-0075).
    first, second = (result.model for result in fits)
    np.testing.assert_array_equal(first.means, second.means)
    np.testing.assert_array_equal(first.idealized, second.idealized)


@requires_sidecar
@pytest.mark.xfail(
    raises=VbgmmDisagreement,
    strict=True,
    reason=(
        "tMAVEN 10f4230's plain vbGMM stops after two iterations on this fixture, because the "
        "lower bound its convergence test reads goes down at every step; its state means are up "
        "to 0.44 from the converged independent fit (#1302, ADR-0138). Strict: a tMAVEN that "
        "fits to convergence passes, and the XPASS fails until this marker is removed."
    ),
)
def test_vbgmm_key_agrees_with_the_independent_reference(tmp_path, capsys):
    # The numbers come from scikit-learn's variational Gaussian mixture with tMAVEN's vbGMM
    # priors on the same pooled FRET (tests/_vbgmm_reference.py), fitted on the base side and
    # committed, because the sidecar environment has no scikit-learn. Only a deviation over the
    # approved tolerance is the expected failure; a fit that does not run, or a reference that
    # names another fixture, fails the test as usual.
    reference = load_reference()
    smd = tmp_path / "smd_4mol.hdf5"
    shutil.copyfile(FOUR_MOL, smd)
    assert reference["fixture"]["path"].endswith(FOUR_MOL.name)

    settings = reference["tmaven"]
    result = run_vbfret(
        smd,
        model_type="vbgmm",
        nstates=settings["nstates"],
        nrestarts=settings["nrestarts"],
        seed=settings["seed"],
        model_out=tmp_path / "vbgmm.hdf5",
        timeout=1800.0,
    )
    assert result.status["ok"] is True
    assert result.status["method"] == "run_vbgmm"
    model = result.model
    assert model.nstates == settings["nstates"]
    assert model.frac is not None and model.frac.shape == (settings["nstates"],)

    found = deviations(model.means, model.frac, reference)
    with capsys.disabled():
        print(f"\n[parity] vbgmm smd_4mol vs scikit-learn reference: {found}")
    problem = disagreement(model.means, model.frac, reference)
    if problem is not None:
        raise VbgmmDisagreement(problem)
