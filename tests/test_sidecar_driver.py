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

import os
import shutil
from pathlib import Path

import numpy as np
import pytest

from tether.idealize import NO_STATE, read_smd, run_vbfret, write_smd

pytestmark = pytest.mark.sidecar

FIXTURES = Path(__file__).parent / "fixtures"
FOUR_MOL = FIXTURES / "smd_4mol.hdf5"

_SIDECAR = os.environ.get("TETHER_SIDECAR_PYTHON")
requires_sidecar = pytest.mark.skipif(
    not _SIDECAR, reason="set TETHER_SIDECAR_PYTHON to a tMAVEN sidecar interpreter"
)


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

    # The fitted ensemble model.
    model = result.model
    assert model.nstates >= 1
    assert model.means.size == model.nstates
    assert np.all(np.isfinite(model.means))
    assert model.idealized is not None
    assert model.idealized.shape == (4, 1700)
    assert model.elbo is not None  # ELBO trace present

    # Per-molecule state paths + dwells were derived for every fitted molecule.
    assert len(result.state_paths) == 4
    for path in result.state_paths.values():
        assert path.shape == (1700,)
        assert set(np.unique(path)).issubset(set(range(model.nstates)) | {NO_STATE})
    assert result.dwells, "expected at least one dwell segment"
    for d in result.dwells:
        assert 0 <= d.state < model.nstates
        assert d.length >= 1
        assert d.start >= 0

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
    # Each fitted SMD row maps back to a carried store key.
    for mol_idx in result.state_paths:
        assert result.molecule_keys[mol_idx] == keys[mol_idx]


@requires_sidecar
def test_vbfret_unknown_model_type_errors(tmp_path):
    from tether.idealize import SidecarError

    smd = tmp_path / "smd_4mol.hdf5"
    shutil.copyfile(FOUR_MOL, smd)
    with pytest.raises(SidecarError, match="unknown model_type"):
        run_vbfret(smd, model_type="not_a_model", timeout=300.0)
