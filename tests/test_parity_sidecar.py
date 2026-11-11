# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Live idealization-parity assertion (``@pytest.mark.sidecar`` — deselected from CI).

This is what ``sidecar.yml`` runs: drive the real headless ``maven_class``
idealizers in the isolated sidecar env, then assert each fresh fit agrees **within
the frozen tolerance** (``schema/parity_tolerance.json``), a frozen *input* the
tests assert against and never recompute (PRD §12.6). Three arms:

- **consensus VB-HMM (vbconhmm)** vs the committed 281-mol reference, and a
  cross-seed anchor on the 4-mol fixture — both against the top-level (M0.5)
  tolerance;
- **ebFRET (ebhmm)** by cross-seed self-consistency on the 281-mol SMD, against its
  own per-method tolerance (``load_frozen_tolerance(..., method="ebhmm")``) — ebFRET
  is frozen separately because its empirical-Bayes per-trace state selection is more
  seed-variable than vbconhmm's (ADR-0043; see the test's docstring).

Needs an interpreter in ``$TETHER_SIDECAR_PYTHON`` (an env built from
``sidecar/conda-lock.yml`` with tMAVEN installed); CI's base matrix has none, so
the module is excluded by ``-m "not ... and not sidecar"``.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from tether.idealize import (
    compare_models,
    load_frozen_tolerance,
    read_model,
    run_ebhmm,
    run_vbfret,
)
from tether.idealize.parity import within_tolerance

pytestmark = pytest.mark.sidecar

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures"
FROZEN = REPO / "schema" / "parity_tolerance.json"

_SIDECAR = os.environ.get("TETHER_SIDECAR_PYTHON")
requires_sidecar = pytest.mark.skipif(
    not _SIDECAR, reason="set TETHER_SIDECAR_PYTHON to a tMAVEN sidecar interpreter"
)

#: The two fixed seeds of the ebFRET cross-seed gate (issue #311, ADR-0075). Chosen as
#: as 1 and 2 before any seeded fit was run, and never re-chosen to make it pass.
EBFRET_SEEDS = (1, 2)


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


@requires_sidecar
def test_281mol_ebfret_cross_seed_matches_within_frozen_tolerance(tmp_path):
    """Two self-reseeded ebFRET fits of the 281-mol SMD agree within its tolerance.

    The **ebFRET (empirical-Bayes HMM)** arm of the M6 idealization-parity oracle
    (PRD §9 M6). ebFRET pools information across the population of molecules
    to infer a consensus kinetic model, sharpening state/rate estimates that vary
    widely trace-by-trace [vandeMeent2014]; its reproducibility is an established
    benchmark axis for smFRET idealizers [Hadzic2018].

    Asserts **cross-seed self-consistency** — two fresh Tether-driven ebFRET fits of
    the same SMD agree — against ebFRET's **own** measured tolerance
    (``method="ebhmm"``), not the vbconhmm top-level row. Two reasons ebFRET is frozen
    separately (ADR-0043): (1) the committed 281-mol reference was fit with vbconhmm,
    and :func:`~tether.idealize.parity.compare_models` scores ``relative_elbo`` — a
    model-specific variational bound not commensurable across methods — so a
    cross-method comparison is invalid; (2) ebFRET's empirical-Bayes per-trace state
    selection is measurably more seed-variable (its cross-seed state-count agreement
    ~0.68–0.74 vs vbconhmm's ~1.0), so the vbconhmm-derived 0.9 floor is too tight. Its
    per-method tolerance was ratified from 20 self-reseeded fits (run 28963324581).
    ``nstates=4`` matches the 281-mol fixture's state count.

    **The two seeds are fixed** (:data:`EBFRET_SEEDS`; issue #311, ADR-0075). Unseeded,
    tMAVEN draws each fit's starting points from operating-system entropy, so this
    gate compared two *random* fits and its result was a draw from the cross-seed
    distribution: a rare poor fit failed it (Viterbi 0.7568 against the 0.9034 floor)
    and a re-run of the same commit passed. Seeded, both fits are reproducible, so the
    gate gives one answer per commit and a failure is a change, not a draw. The frozen
    band is unchanged; the two seeds were fixed before any seeded fit was scored.
    """
    tolerance = load_frozen_tolerance(FROZEN, method="ebhmm")

    smd = tmp_path / "smd_281mol.hdf5"
    shutil.copyfile(FIXTURES / "large" / "smd_281mol.hdf5", smd)
    a = run_ebhmm(smd, nstates=4, timeout=1800.0, seed=EBFRET_SEEDS[0])
    b = run_ebhmm(smd, nstates=4, timeout=1800.0, seed=EBFRET_SEEDS[1])

    metrics = compare_models(a.model, b.model)
    # Seeded, these numbers are the same on every run of a commit; print them so the
    # CI log records them (``pytest -s`` or ``-rP`` shows them on a pass).
    print(f"ebFRET seeds {EBFRET_SEEDS} cross-seed metrics: {metrics}")
    ok, failures = within_tolerance(metrics, tolerance)
    assert ok, f"ebFRET cross-seed parity drift vs frozen ebhmm tolerance: {failures}"


def _model_datasets(path):
    """Every dataset in a tMAVEN model file, by HDF5 path (for a bit-level compare).

    tMAVEN stamps each model and each per-trace result with a ``time_made`` wall-clock
    string; those are the only datasets left out, because they record *when* the fit
    ran, not what it found.
    """
    import h5py

    out = {}

    def _keep(name, obj):
        if isinstance(obj, h5py.Dataset) and not name.endswith("time_made"):
            out[name] = obj[()]

    with h5py.File(path, "r") as f:
        f.visititems(_keep)
    return out


@requires_sidecar
def test_4mol_ebfret_same_seed_gives_the_same_model(tmp_path):
    """Two ebFRET fits with one seed write the same model, value for value.

    The guard behind the fixed-seed gate above (issue #311, ADR-0075): if a tMAVEN
    change adds a random source the sidecar does not seed, the two fits differ here
    and this fails on every run, instead of the 281-mol gate going back to a draw.
    The 4-mol fixture keeps it to a few seconds of fitting.
    """
    import numpy as np

    smd = tmp_path / "smd_4mol.hdf5"
    shutil.copyfile(FIXTURES / "smd_4mol.hdf5", smd)
    runs = []
    for name in ("a", "b"):
        out = tmp_path / f"model_{name}.hdf5"
        run_ebhmm(smd, nstates=2, timeout=1800.0, seed=EBFRET_SEEDS[0], model_out=out)
        runs.append(_model_datasets(out))

    a, b = runs
    assert a and a.keys() == b.keys()
    for name in a:
        np.testing.assert_array_equal(a[name], b[name], err_msg=name)
