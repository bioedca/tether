# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The independent vbGMM reference of ``smd_4mol.hdf5`` still says what it records (#1302).

``tests/reference_data/smd_4mol_vbgmm_sklearn.json`` holds a scikit-learn fit that the sidecar
test compares with tMAVEN's plain vbGMM (ADR-0138). The sidecar environment has no
scikit-learn, so it reads those numbers and never fits them; this module is where the numbers
are fitted again. It runs on the base test matrix, so a change of the fixture, of the data
preparation, of the prior mapping or of the base-lock scikit-learn that moves the reference
fails here, on every operating system, and not only on the sidecar job.
"""

from __future__ import annotations

import hashlib

import numpy as np
import pytest

import _vbgmm_reference as ref

REFERENCE = ref.load_reference()


def test_the_reference_names_the_committed_fixture():
    fixture = REFERENCE["fixture"]
    assert fixture["path"] == "tests/fixtures/smd_4mol.hdf5"
    assert hashlib.sha256(ref.FIXTURE.read_bytes()).hexdigest() == fixture["sha256"]


def test_the_pooled_data_is_the_recorded_data():
    x = ref.pooled_fret()
    assert x.size == REFERENCE["data"]["n_points"] == 6799  # 1699 + 3 x 1700 frames
    assert np.all((x >= ref.CLIP_LOW) & (x <= ref.CLIP_HIGH))
    found = ref.data_fingerprint()
    recorded = REFERENCE["data"]
    for key in ("n_points", "n_clipped", "sha256_kept_float64_le", "sha256_clipped_mask"):
        assert found[key] == recorded[key], key
    # The clip draws may differ in the last bit between platforms (see data_fingerprint).
    assert found["clipped_sum"] == pytest.approx(recorded["clipped_sum"], rel=0, abs=1e-9)


def test_the_comparator_uses_tmavens_priors_in_sklearn_terms():
    # Gamma(a, b) on the precision is Wishart(nu = 2a, W0^-1 = 2b) in one dimension.
    assert REFERENCE["tmaven"]["priors"] == ref.TMAVEN_PRIORS
    assert REFERENCE["comparator"]["priors"] == ref.SKLEARN_PRIORS
    assert ref.SKLEARN_PRIORS["degrees_of_freedom_prior"] == pytest.approx(0.2)
    assert ref.SKLEARN_PRIORS["covariance_prior"] == pytest.approx(0.02)
    assert ref.SKLEARN_PRIORS["mean_precision_prior"] == 0.25
    assert ref.SKLEARN_PRIORS["weight_concentration_prior"] == 1.0
    assert REFERENCE["comparator"]["settings"] == ref.SKLEARN_SETTINGS


def test_a_fresh_fit_gives_the_committed_numbers():
    fresh = ref.fit_reference(ref.pooled_fret())
    committed = REFERENCE["fit"]
    assert fresh["converged"] is True
    for key in ("means", "fractions", "variances", "sklearn_weights"):
        np.testing.assert_allclose(fresh[key], committed[key], rtol=0, atol=1e-6, err_msg=key)
    assert np.isclose(sum(fresh["fractions"]), 1.0)


def test_the_tolerance_admits_the_converged_fit_and_not_the_stopped_one():
    # The recorded measurements are the basis of the tolerance (ADR-0138). Recompute their
    # deviations so a hand edit of either number, or of the limits, cannot leave them apart.
    measured = REFERENCE["measured"]
    for name in ("vbgmm_key", "tmaven_updates_converged", "vbgmm_key_corrected_loop"):
        run = measured[name]
        assert ref.deviations(run["means"], run["fractions"], REFERENCE) == pytest.approx(
            run["deviation"]
        )
    converged = measured["tmaven_updates_converged"]
    assert ref.disagreement(converged["means"], converged["fractions"], REFERENCE) is None
    stopped = measured["vbgmm_key"]
    assert stopped["iteration"] == 2
    assert np.all(np.diff(stopped["lower_bound_by_iteration"]) < 0)
    message = ref.disagreement(stopped["means"], stopped["fractions"], REFERENCE)
    assert message is not None
    assert "means_max_abs" in message and "fractions_max_abs" in message
    # The key's fit since #1337 (ADR-0139): Tether's corrected loop, inside the tolerance.
    corrected = measured["vbgmm_key_corrected_loop"]
    assert corrected["iteration"] > 2
    assert ref.disagreement(corrected["means"], corrected["fractions"], REFERENCE) is None
    bound = corrected["lower_bound_first_three"] + corrected["lower_bound_last_three"]
    assert np.all(np.diff(bound) > 0)


def test_deviations_sort_the_states_by_mean():
    fit = REFERENCE["fit"]
    shuffled = [2, 0, 1]
    means = [fit["means"][i] for i in shuffled]
    fractions = [fit["fractions"][i] for i in shuffled]
    assert ref.deviations(means, fractions, REFERENCE) == {
        "means_max_abs": 0.0,
        "fractions_max_abs": 0.0,
    }
    assert ref.disagreement(means, fractions, REFERENCE) is None
