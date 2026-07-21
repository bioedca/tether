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

# The literal `reference` value scripts/measure_parity.py records for a spread
# anchored on its own first run (`measure_parity.py:222`); anything else names a
# committed reference model.
_ANCHOR_REFERENCE = "cross-seed (run00 anchor)"

# What each frozen block was measured on: the SMD fixture, the reference anchor and
# the fitted state count, plus how many comparisons each fixture contributed (a
# cross-seed spread anchored on run00 spends one of its `n_runs` on the anchor,
# hence 19 of 20). Pinned here because *which evidence exists* is part of the
# freeze: dropping a fixture or a run, or re-pointing one at a different SMD file,
# reference model or state count, is a re-freeze (`$.freeze_policy`,
# ADR-0009/ADR-0043), never a silent edit. Without this a PR could satisfy a bound
# by deleting the runs that stressed it, and every remaining value would still pass,
# or re-label the anchor the evidence claims to have been measured against while
# every number stayed put. Values mirror `scripts/measure_parity.py:70-81`.
_EXPECTED_EVIDENCE = {
    "smd_4mol": {
        "smd": "tests/fixtures/smd_4mol.hdf5",
        "reference": _ANCHOR_REFERENCE,  # producer `reference=None` -> the sentinel
        "nstates": 2,
        "n_comparisons": 19,
    },
    "smd_281mol": {
        "smd": "tests/fixtures/large/smd_281mol.hdf5",
        "reference": "tests/fixtures/large/model_281mol.hdf5",
        "nstates": 4,
        "n_comparisons": 20,
    },
}
# ebFRET was measured on the 281-mol fixture in `--cross-seed` mode (ADR-0043), so
# its anchor is the sentinel, not the committed reference model.
_EXPECTED_EVIDENCE_BY_METHOD = {
    "ebhmm": {
        "smd_281mol": {
            "smd": "tests/fixtures/large/smd_281mol.hdf5",
            "reference": _ANCHOR_REFERENCE,
            "nstates": 4,
            "n_comparisons": 19,
        }
    }
}


def _assert_spread_within(spread_by_fixture, tol, *, expected_evidence, n_runs):
    """Every recorded per-run value must sit within ``tol``'s floors/ceilings.

    Also asserts the evidence is *complete and self-consistent*, so a bound can
    never be satisfied by deleting the runs that stressed it: the measured fixture
    set must match ``expected_evidence`` exactly and each fixture must still name
    the SMD path, reference anchor, state count and comparison count pinned there;
    that comparison count must equal the block's declared ``n_runs``, less the
    anchor run for a cross-seed spread; every fixture must carry all four metrics
    with their declared direction; and the recorded
    ``n``/``min``/``max``/``mean``/``worst`` must be reproducible from the ``values``
    list via the production :class:`SpreadSummary`. Dropping an outlier therefore
    fails on the count, doctoring the count fails here too, and re-labelling the
    anchor or fixture path fails on the identity check.

    The identity check pins the *strings the frozen artifact records*. It does not
    open the named files, so it cannot attest that ``model_281mol.hdf5`` exists, is
    unmodified, or is the file the fit actually loaded — only that the artifact
    still names the anchor it claims to have been measured against.
    """
    assert set(spread_by_fixture) == set(expected_evidence), "measured fixture set changed"
    for name, fixture in spread_by_fixture.items():
        expected = expected_evidence[name]
        for key in ("smd", "reference", "nstates"):
            assert fixture[key] == expected[key], (
                f"{name}: frozen {key} is {fixture[key]!r}, pinned evidence is {expected[key]!r}"
            )
        expected_n = expected["n_comparisons"]
        # Exact, not a band: a run00-anchored spread spends its first run on the
        # anchor and yields n_runs - 1 comparisons, while one measured against a
        # committed reference model yields n_runs (`parity.measure_spread`). The
        # mode comes from the pin, not from the artifact, so neither a doctored
        # `n_runs_per_fixture` nor a re-labelled `reference` can hide behind the
        # other mode's count.
        anchored = expected["reference"] == _ANCHOR_REFERENCE
        assert expected_n == (n_runs - 1 if anchored else n_runs), (
            f"{name}: pinned {expected_n} comparisons vs declared n_runs={n_runs} "
            f"(reference={fixture['reference']})"
        )
        assert fixture["n_comparisons"] == expected_n, (
            f"{name}: {fixture['n_comparisons']} comparisons, frozen evidence has {expected_n}"
        )
        assert set(fixture["metrics"]) == set(_FLOORS) | set(_CEILINGS), (
            f"{name} must record all four metrics"
        )
        for metric, summ in fixture["metrics"].items():
            is_floor = metric in _FLOORS
            assert summ["direction"] == ("floor" if is_floor else "ceiling")
            # Evidence completeness: no run may vanish from under the bound.
            values = summ["values"]
            assert len(values) == summ["n"] == expected_n, (
                f"{name}/{metric}: recorded n disagrees with the frozen evidence"
            )
            # Summary consistency: recompute from `values` with the production
            # aggregator, so an edited-out value cannot leave the worst case behind.
            recomputed = SpreadSummary(metric, summ["direction"], values).as_dict()
            for key in ("n", "min", "max", "worst"):
                assert recomputed[key] == summ[key], f"{name}/{metric}: stale {key}"
            assert recomputed["mean"] == pytest.approx(summ["mean"], rel=1e-12)
            for v in values:
                if is_floor:
                    assert v >= tol[_FLOORS[metric]], f"{metric}={v} below frozen floor"
                else:
                    assert v <= tol[_CEILINGS[metric]], f"{metric}={v} above frozen ceiling"


def test_frozen_artifact_covers_its_own_measured_evidence():
    """The committed tolerance must satisfy every recorded per-run measurement.

    This is the PR-facing parity check: it needs no sidecar (it asserts the
    *frozen JSON* against its own recorded spread), so branch protection can gate
    on it via the base `test` matrix, while the live sidecar fit stays in the
    out-of-band `sidecar.yml`.

    Be precise about the direction it protects: the assertions are `value within
    bound`, so a bound **tightened** below its own evidence, evidence deleted or
    truncated from under a bound (see ``_assert_spread_within``), or a
    ``$.provisional`` that has drifted from the imported ``PROVISIONAL`` all fail
    here — but a **loosened** bound does not, because widening a ceiling (or
    lowering a floor) only leaves the recorded values further inside it. The
    ``$.provisional`` assertion is a *drift* check, not a pin to literals: editing
    the artifact or ``PROVISIONAL`` alone fails, but a PR that moves both in
    lockstep does not. Loosening, and the PRD 11.2 defaults themselves, are held
    by review plus the deliberate re-freeze rule (``$.freeze_policy``, PRD 11.2,
    ADR-0009), not by this test. Same caveat applies to the per-method check below.
    """
    data = json.loads(FROZEN_JSON.read_text(encoding="utf-8"))
    # Provisional defaults are the documented floor/ceiling design intent.
    assert data["provisional"] == PROVISIONAL
    _assert_spread_within(
        data["spread_by_fixture"],
        data["tolerance"],
        expected_evidence=_EXPECTED_EVIDENCE,
        n_runs=data["method"]["n_runs_per_fixture"],
    )


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
    in the base matrix. It fails on a per-method tolerance with no recorded evidence,
    on a missing bound, on evidence deleted or truncated from under a bound, on a
    re-labelled fixture path, reference anchor or state count, and on a tolerance
    tightened below its evidence — not on a loosened one (see the direction caveat
    above).
    """
    data = json.loads(FROZEN_JSON.read_text(encoding="utf-8"))
    by_tol = data.get("tolerance_by_method", {})
    by_measured = data.get("measured_by_method", {})
    assert set(by_tol) == set(by_measured), "every per-method tolerance needs its evidence"
    assert set(by_tol) == set(_EXPECTED_EVIDENCE_BY_METHOD), "per-method freeze set changed"
    for method, tol in by_tol.items():
        assert set(tol) == set(PROVISIONAL), f"{method} tolerance must carry the four bounds"
        measured = by_measured[method]
        _assert_spread_within(
            measured["spread_by_fixture"],
            tol,
            expected_evidence=_EXPECTED_EVIDENCE_BY_METHOD[method],
            n_runs=measured["method"]["n_runs_per_fixture"],
        )


def test_load_frozen_tolerance_selects_per_method():
    default = load_frozen_tolerance(FROZEN_JSON)
    ebhmm = load_frozen_tolerance(FROZEN_JSON, method="ebhmm")
    assert set(ebhmm) == set(PROVISIONAL)
    # ebFRET's measured state-count floor is genuinely looser than the vbconhmm default.
    assert ebhmm["state_count_min_fraction"] < default["state_count_min_fraction"]
    # A method without its own block, and an explicit None, fall back to the default.
    assert load_frozen_tolerance(FROZEN_JSON, method="vbconhmm") == default
    assert load_frozen_tolerance(FROZEN_JSON, method=None) == default
