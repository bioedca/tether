# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the pure idealization-parity metrics (no sidecar).

These exercise :mod:`tether.idealize.parity` entirely in the base environment:
the canonical (mean-sorted) state alignment that makes comparisons invariant to
tMAVEN's arbitrary label permutations, the four metric functions, the
``compare_models`` aggregate CI asserts against, ``within_tolerance`` reporting,
and the ``freeze`` policy. The *live* ≥20-run measurement is driven by the
sidecar and lives behind ``@pytest.mark.sidecar`` (deselected from CI).
"""

from __future__ import annotations

import numpy as np
import pytest

from tether.idealize import (
    NO_STATE,
    ParityMetrics,
    StateModel,
    compare_models,
    freeze,
    state_count_fraction,
    state_mean_abs_delta,
    viterbi_agreement,
    within_tolerance,
)
from tether.idealize.parity import (
    PROVISIONAL,
    SpreadSummary,
    canonical_state_path,
    relative_elbo,
)

NAN = float("nan")


def _model(means, idealized=None, elbo=None):
    """Build a StateModel from state means and an optional idealized array.

    ``idealized`` is given as the per-frame FRET *level* (use ``NAN`` for
    out-of-window frames); it is stored verbatim so ``canonical_state_path`` maps
    each level back to its nearest state.
    """
    means = np.asarray(means, dtype="float64")
    ideal = None if idealized is None else np.asarray(idealized, dtype="float64")
    return StateModel(
        model_type="test",
        nstates=means.size,
        means=means,
        elbo=elbo,
        idealized=ideal,
    )


# --------------------------------------------------------------------------- #
# canonical alignment                                                          #
# --------------------------------------------------------------------------- #


def test_canonical_path_relabels_to_mean_order():
    # means out of order: state 0 is high-FRET, state 1 low — canonical flips them.
    model = _model([0.9, 0.1], idealized=[[0.9, 0.1, 0.9]])
    path = canonical_state_path(model)
    # 0.9 -> highest mean -> canonical label 1; 0.1 -> canonical 0.
    assert path.tolist() == [[1, 0, 1]]


def test_canonical_path_preserves_no_state_for_nan():
    model = _model([0.1, 0.9], idealized=[[0.1, NAN, 0.9]])
    path = canonical_state_path(model)
    assert path[0, 1] == NO_STATE
    assert path[0, 0] == 0 and path[0, 2] == 1


def test_compare_is_permutation_invariant():
    # Same physical idealization; B has its state labels permuted (means reordered).
    ideal = [[0.1, 0.1, 0.5, 0.9, 0.9], [0.5, 0.9, 0.9, 0.1, 0.5]]
    a = _model([0.1, 0.5, 0.9], idealized=ideal, elbo=-100.0)
    b = _model([0.9, 0.1, 0.5], idealized=ideal, elbo=-100.0)
    m = compare_models(a, b)
    assert m.state_count_fraction == 1.0
    assert m.state_mean_abs_delta == 0.0
    assert m.viterbi_agreement == 1.0
    assert m.relative_elbo == 0.0


# --------------------------------------------------------------------------- #
# individual metrics                                                          #
# --------------------------------------------------------------------------- #


def test_state_count_fraction_counts_distinct_occupied():
    # ref trace 0 occupies {0,1}=2 states; test occupies {0}=1 -> disagree.
    # trace 1 both occupy 2 states -> agree. -> 1/2 = 0.5.
    ref = np.array([[0, 1, 1], [0, 1, 0]], dtype="int64")
    test = np.array([[0, 0, 0], [0, 1, 1]], dtype="int64")
    assert state_count_fraction(ref, test) == pytest.approx(0.5)


def test_state_count_fraction_ignores_all_empty_traces():
    ref = np.array([[NO_STATE, NO_STATE], [0, 1]], dtype="int64")
    test = np.array([[NO_STATE, NO_STATE], [0, 1]], dtype="int64")
    # only the second trace is informative and it agrees -> 1.0
    assert state_count_fraction(ref, test) == 1.0


def test_state_mean_abs_delta_sorted_match():
    a = _model([0.10, 0.50, 0.90])
    b = _model([0.92, 0.11, 0.48])  # same states, permuted + small drift
    # sorted: a=[.1,.5,.9] b=[.11,.48,.92] -> max|Δ|=0.02
    assert state_mean_abs_delta(a, b) == pytest.approx(0.02, abs=1e-9)


def test_state_mean_abs_delta_inf_on_count_mismatch():
    assert state_mean_abs_delta(_model([0.1, 0.9]), _model([0.1, 0.5, 0.9])) == float("inf")


def test_viterbi_agreement_partial_and_window():
    ref = np.array([[0, 1, 2, NO_STATE]], dtype="int64")
    test = np.array([[0, 1, 1, NO_STATE]], dtype="int64")
    # in-window frames: 3 (cols 0,1,2); 2 match -> 2/3
    assert viterbi_agreement(ref, test) == pytest.approx(2 / 3)


def test_viterbi_agreement_zero_on_shape_mismatch():
    ref = np.zeros((2, 5), dtype="int64")
    test = np.zeros((2, 4), dtype="int64")
    assert viterbi_agreement(ref, test) == 0.0


def test_viterbi_agreement_zero_when_no_shared_window():
    ref = np.array([[0, 1, NO_STATE]], dtype="int64")
    test = np.array([[NO_STATE, NO_STATE, 1]], dtype="int64")
    assert viterbi_agreement(ref, test) == 0.0


def test_relative_elbo_basic_and_guards():
    drift = relative_elbo(_model([0.1], elbo=-100.0), _model([0.1], elbo=-101.0))
    assert drift == pytest.approx(0.01)
    assert relative_elbo(_model([0.1], elbo=None), _model([0.1], elbo=-1.0)) == float("inf")
    assert relative_elbo(_model([0.1], elbo=0.0), _model([0.1], elbo=1.0)) == float("inf")


# --------------------------------------------------------------------------- #
# tolerance checking                                                          #
# --------------------------------------------------------------------------- #


def test_within_tolerance_passes_clean_metrics():
    m = ParityMetrics(
        state_count_fraction=0.99,
        state_mean_abs_delta=0.005,
        viterbi_agreement=0.99,
        relative_elbo=0.001,
        n_states_ref=4,
        n_states_test=4,
    )
    ok, failures = within_tolerance(m, PROVISIONAL)
    assert ok and failures == []


def test_within_tolerance_names_each_violation():
    m = ParityMetrics(
        state_count_fraction=0.5,  # < 0.90
        state_mean_abs_delta=0.10,  # > 0.02
        viterbi_agreement=0.50,  # < 0.95
        relative_elbo=0.50,  # > 0.01
        n_states_ref=4,
        n_states_test=3,
    )
    ok, failures = within_tolerance(m, PROVISIONAL)
    assert not ok
    assert len(failures) == 4
    joined = " ".join(failures)
    assert "state-count" in joined and "Viterbi" in joined and "ELBO" in joined


# --------------------------------------------------------------------------- #
# freeze policy                                                               #
# --------------------------------------------------------------------------- #


def _spread(values_by_metric):
    directions = {
        "state_count_fraction": "floor",
        "state_mean_abs_delta": "ceiling",
        "viterbi_agreement": "floor",
        "relative_elbo": "ceiling",
    }
    return {k: SpreadSummary(k, directions[k], list(v)) for k, v in values_by_metric.items()}


def test_freeze_confirms_provisional_when_spread_is_tight():
    # A tight, near-perfect spread should keep the provisional defaults.
    tol = freeze(
        _spread(
            {
                "state_count_fraction": [1.0, 0.99, 1.0],
                "state_mean_abs_delta": [0.001, 0.002, 0.0],
                "viterbi_agreement": [0.99, 0.995, 1.0],
                "relative_elbo": [0.0, 0.001, 0.0005],
            }
        )
    )
    assert tol == PROVISIONAL


def test_freeze_widens_when_spread_exceeds_provisional():
    tol = freeze(
        _spread(
            {
                "state_count_fraction": [0.80, 0.85, 0.82],  # worst 0.80
                "state_mean_abs_delta": [0.03, 0.05, 0.04],  # worst 0.05
                "viterbi_agreement": [0.90, 0.88, 0.92],  # worst 0.88
                "relative_elbo": [0.02, 0.03, 0.025],  # worst 0.03
            }
        ),
        margin=0.5,
    )
    # ceilings widen (×1.5), floors drop below the defaults
    assert tol["state_mean_abs_delta_max"] == pytest.approx(0.075)
    assert tol["relative_elbo_max"] == pytest.approx(0.045)
    assert tol["state_count_min_fraction"] < 0.90
    assert tol["viterbi_min_agreement"] < 0.95
