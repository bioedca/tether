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

import json
from pathlib import Path

import numpy as np
import pytest

from tether.idealize import (
    NO_STATE,
    ParityMetrics,
    StateModel,
    compare_models,
    freeze,
    load_frozen_tolerance,
    measure_spread,
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

FROZEN_JSON = Path(__file__).resolve().parents[1] / "schema" / "parity_tolerance.json"

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


# --------------------------------------------------------------------------- #
# state-count-mismatch invariants and non-finite guards                       #
# --------------------------------------------------------------------------- #


def test_compare_forces_viterbi_zero_on_state_count_mismatch():
    # Identical low-FRET path, but test declares an extra unused high state.
    ideal = [[0.1, 0.1, 0.1]]
    ref = _model([0.1, 0.9], idealized=ideal)
    test = _model([0.1, 0.5, 0.9], idealized=ideal)  # 3 states vs 2
    m = compare_models(ref, test)
    assert m.n_states_ref != m.n_states_test
    assert m.viterbi_agreement == 0.0  # not silently 1.0
    assert m.state_mean_abs_delta == float("inf")


def test_within_tolerance_rejects_non_finite_metric():
    m = ParityMetrics(
        state_count_fraction=1.0,
        state_mean_abs_delta=float("nan"),  # malformed
        viterbi_agreement=1.0,
        relative_elbo=0.0,
        n_states_ref=2,
        n_states_test=2,
    )
    ok, failures = within_tolerance(m, PROVISIONAL)
    assert not ok
    assert any("non-finite" in f for f in failures)


def test_spread_worst_preserves_non_finite_sentinels():
    # An inf ΔE (incomparable run) must surface as the worst ceiling, not be dropped.
    ceiling = SpreadSummary("state_mean_abs_delta", "ceiling", [0.01, float("inf"), 0.02])
    assert ceiling.worst == float("inf")
    floor = SpreadSummary("viterbi_agreement", "floor", [0.99, float("nan"), 1.0])
    assert floor.worst == 0.0


def test_freeze_cannot_ratify_over_a_sentinel_failure():
    # A single incomparable run (inf ΔE) blows the frozen ceiling open, so the
    # freeze can never quietly ratify a finite tolerance over invalid evidence.
    tol = freeze(
        _spread(
            {
                "state_count_fraction": [1.0, 1.0],
                "state_mean_abs_delta": [0.001, float("inf")],
                "viterbi_agreement": [1.0, 1.0],
                "relative_elbo": [0.0, 0.0],
            }
        )
    )
    assert not np.isfinite(tol["state_mean_abs_delta_max"])


def test_measure_spread_rejects_zero_comparison_configs():
    with pytest.raises(ValueError, match="n_runs must be at least 1"):
        measure_spread("ignored.hdf5", n_runs=0)
    with pytest.raises(ValueError, match="anchoring on the first run"):
        measure_spread("ignored.hdf5", reference=None, n_runs=1)


# --------------------------------------------------------------------------- #
# the committed frozen artifact (PR-facing — runs in the base CI matrix)       #
# --------------------------------------------------------------------------- #

# metric name -> its bound key in a tolerance dict; shared by the top-level and
# the per-method evidence checks so the metric→bound mapping lives in one place.
_FLOORS = {
    "state_count_fraction": "state_count_min_fraction",
    "viterbi_agreement": "viterbi_min_agreement",
}
_CEILINGS = {
    "state_mean_abs_delta": "state_mean_abs_delta_max",
    "relative_elbo": "relative_elbo_max",
}


def _assert_spread_within(spread_by_fixture, tol):
    """Every recorded per-run value must sit within ``tol``'s floors/ceilings."""
    for fixture in spread_by_fixture.values():
        for metric, summ in fixture["metrics"].items():
            for v in summ["values"]:
                if metric in _FLOORS:
                    assert v >= tol[_FLOORS[metric]], f"{metric}={v} below frozen floor"
                else:
                    assert v <= tol[_CEILINGS[metric]], f"{metric}={v} above frozen ceiling"


def test_frozen_artifact_covers_its_own_measured_evidence():
    """The committed tolerance must satisfy every recorded per-run measurement.

    This is the PR-facing parity check: it needs no sidecar (it asserts the
    *frozen JSON* against its own recorded spread), so branch protection can gate
    on it via the base `test` matrix, while the live sidecar fit stays in the
    out-of-band `sidecar.yml`. A tampered/loosened artifact or a freeze that does
    not cover its evidence fails here.
    """
    data = json.loads(FROZEN_JSON.read_text(encoding="utf-8"))
    # Provisional defaults are the documented floor/ceiling design intent.
    assert data["provisional"] == PROVISIONAL
    _assert_spread_within(data["spread_by_fixture"], data["tolerance"])


def test_load_frozen_tolerance_returns_the_four_bounds():
    tol = load_frozen_tolerance(FROZEN_JSON)
    assert set(tol) == set(PROVISIONAL)
    assert all(np.isfinite(v) for v in tol.values())


# --------------------------------------------------------------------------- #
# per-method tolerances (ebFRET frozen separately from vbconhmm — ADR-0043)     #
# --------------------------------------------------------------------------- #


def test_per_method_tolerances_cover_their_own_measured_evidence():
    """Each per-method frozen tolerance must satisfy its own recorded spread.

    The per-method counterpart of
    :func:`test_frozen_artifact_covers_its_own_measured_evidence`: ebFRET (``ebhmm``)
    is frozen separately (ADR-0043) because its empirical-Bayes per-trace state
    selection is more seed-variable than vbconhmm's, so its bounds are validated
    against its *own* measured evidence, never the vbconhmm top-level row. Also runs
    in the base matrix, so a tampered/loosened per-method block fails here.
    """
    data = json.loads(FROZEN_JSON.read_text(encoding="utf-8"))
    by_tol = data.get("tolerance_by_method", {})
    by_measured = data.get("measured_by_method", {})
    assert set(by_tol) == set(by_measured), "every per-method tolerance needs its evidence"
    for method, tol in by_tol.items():
        assert set(tol) == set(PROVISIONAL), f"{method} tolerance must carry the four bounds"
        _assert_spread_within(by_measured[method]["spread_by_fixture"], tol)


def test_load_frozen_tolerance_selects_per_method():
    default = load_frozen_tolerance(FROZEN_JSON)
    ebhmm = load_frozen_tolerance(FROZEN_JSON, method="ebhmm")
    assert set(ebhmm) == set(PROVISIONAL)
    # ebFRET's measured state-count floor is genuinely looser than the vbconhmm default.
    assert ebhmm["state_count_min_fraction"] < default["state_count_min_fraction"]
    # A method without its own block, and an explicit None, fall back to the default.
    assert load_frozen_tolerance(FROZEN_JSON, method="vbconhmm") == default
    assert load_frozen_tolerance(FROZEN_JSON, method=None) == default
