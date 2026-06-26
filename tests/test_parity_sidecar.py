# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Live idealization-parity assertion (``@pytest.mark.sidecar`` — deselected from CI).

This is what ``sidecar.yml`` runs: drive the real headless ``maven_class``
consensus VB-HMM in the isolated sidecar env, then assert each fresh fit agrees
with the committed reference (the 281-mol reference model; a cross-seed anchor
for the 4-mol fixture) **within the frozen §11.2 tolerance**
(``schema/parity_tolerance.json``). The tolerance is a frozen *input* ratified
once at M0.5 — these tests assert against it, never recompute it (PRD §12.6).

Needs an interpreter in ``$TETHER_SIDECAR_PYTHON`` (an env built from
``sidecar/conda-lock.yml`` with tMAVEN installed); CI's base matrix has none, so
the module is excluded by ``-m "not ... and not sidecar"``.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from tether.idealize import compare_models, load_frozen_tolerance, read_model, run_vbfret
from tether.idealize.parity import within_tolerance

pytestmark = pytest.mark.sidecar

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures"
FROZEN = REPO / "schema" / "parity_tolerance.json"

_SIDECAR = os.environ.get("TETHER_SIDECAR_PYTHON")
requires_sidecar = pytest.mark.skipif(
    not _SIDECAR, reason="set TETHER_SIDECAR_PYTHON to a tMAVEN sidecar interpreter"
)


@requires_sidecar
def test_281mol_fresh_fit_matches_reference_within_frozen_tolerance(tmp_path):
    """A fresh consensus VB-HMM fit reproduces the committed reference model."""
    tolerance = load_frozen_tolerance(FROZEN)
    reference = read_model(FIXTURES / "large" / "model_281mol.hdf5")

    smd = tmp_path / "smd_281mol.hdf5"
    shutil.copyfile(FIXTURES / "large" / "smd_281mol.hdf5", smd)
    result = run_vbfret(smd, model_type="vbconhmm", nstates=4, timeout=1800.0)

    metrics = compare_models(reference, result.model)
    ok, failures = within_tolerance(metrics, tolerance)
    assert ok, f"parity drift vs frozen tolerance: {failures}"


@requires_sidecar
def test_4mol_cross_seed_matches_within_frozen_tolerance(tmp_path):
    """Two self-reseeded fits of the 4-mol fixture agree within tolerance."""
    tolerance = load_frozen_tolerance(FROZEN)

    smd = tmp_path / "smd_4mol.hdf5"
    shutil.copyfile(FIXTURES / "smd_4mol.hdf5", smd)
    a = run_vbfret(smd, model_type="vbconhmm", nstates=2, timeout=1800.0)
    b = run_vbfret(smd, model_type="vbconhmm", nstates=2, timeout=1800.0)

    metrics = compare_models(a.model, b.model)
    ok, failures = within_tolerance(metrics, tolerance)
    assert ok, f"cross-seed parity drift vs frozen tolerance: {failures}"
