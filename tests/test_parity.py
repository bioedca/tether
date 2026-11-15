# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the pure idealization-parity metrics (no sidecar).

These exercise :mod:`tether.idealize.parity` entirely in the base environment:
the canonical (mean-sorted) state alignment that makes comparisons invariant to
tMAVEN's arbitrary label permutations, the four metric functions, the
``compare_models`` aggregate CI asserts against, ``within_tolerance`` reporting,
and the ``freeze`` policy. The measurement harness ``measure_spread`` is tested in
``test_parity_harness.py`` with its sidecar boundary replaced by prepared fits; a
*live* ≥20-run measurement is a manual ``sidecar-measure.yml`` dispatch, and no
test runs one.
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
    FREEZE_RESOLUTION,
    PROVISIONAL,
    SpreadSummary,
    canonical_state_path,
    freeze_transition_matrix,
    load_transition_matrix_tolerance,
    relative_elbo,
    transition_matrix_abs_delta,
    within_transition_matrix_tolerance,
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
    # ceilings widen (×1.5); floors drop by margin × (1 − worst), the documented formula:
    # 0.80 − 0.5 × 0.20 = 0.70 and 0.88 − 0.5 × 0.12 = 0.82. Exact values, not a range
    # check: a floor that collapsed to 0.0 is also below the default (#737, IDZ-F9).
    assert tol["state_mean_abs_delta_max"] == pytest.approx(0.075)
    assert tol["relative_elbo_max"] == pytest.approx(0.045)
    assert tol["state_count_min_fraction"] == pytest.approx(0.70)
    assert tol["viterbi_min_agreement"] == pytest.approx(0.82)


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
    lockstep does not. Loosening is caught by
    :func:`test_frozen_bounds_match_the_accepted_copy` instead, which pins every bound
    to a copy outside the file (#545, ADR-0110); the PRD 11.2 defaults themselves are
    held by review plus the deliberate re-freeze rule (``$.freeze_policy``, PRD 11.2,
    ADR-0009). Same caveat applies to the per-method check below.
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


#: THE ACCEPTED FROZEN BOUNDS, copied out of ``schema/parity_tolerance.json`` (#545, ADR-0110).
#:
#: The checks above assert *recorded value within bound*, so they cannot see a bound that was
#: widened: a wider bound only leaves every recorded value further inside it. This copy can.
#: Every frozen bound of the artifact must equal its accepted value here, so a bound widened
#: (or tightened, or added, or removed) in the file alone fails. A deliberate re-freeze changes
#: both copies in one pull request, under an ADR (ADR-0009, ADR-0043, ADR-0110), which is then
#: a visible two-file edit for review rather than a one-number edit nothing reads.
_ACCEPTED_BOUNDS = {
    "$.tolerance": {
        "state_count_min_fraction": 0.9,
        "state_mean_abs_delta_max": 0.02,
        "viterbi_min_agreement": 0.95,
        "relative_elbo_max": 0.01,
    },
    "$.tolerance_by_method.ebhmm": {
        "state_count_min_fraction": 0.5249,
        "state_mean_abs_delta_max": 0.0518,
        "viterbi_min_agreement": 0.9034,
        "relative_elbo_max": 0.01,
    },
    "$.transition_matrix.tolerance_by_method.vbconhmm": {
        "transition_matrix_abs_delta_max": 0.0001,
    },
    "$.transition_matrix.tolerance_by_method.ebhmm": {
        "transition_matrix_abs_delta_max": 0.0441,
    },
}


def _frozen_bound_rows(data: dict) -> dict[str, dict]:
    """Every frozen bound row of the artifact, by JSON pointer."""
    rows = {"$.tolerance": data["tolerance"]}
    for method, row in data.get("tolerance_by_method", {}).items():
        rows[f"$.tolerance_by_method.{method}"] = row
    for method, row in data.get("transition_matrix", {}).get("tolerance_by_method", {}).items():
        rows[f"$.transition_matrix.tolerance_by_method.{method}"] = row
    return rows


def _bound_changes(data: dict) -> list[str]:
    """Each way the artifact's frozen bounds differ from the accepted copy, named."""
    rows = _frozen_bound_rows(data)
    changes = [f"{ptr}: row added" for ptr in sorted(set(rows) - set(_ACCEPTED_BOUNDS))]
    changes += [f"{ptr}: row removed" for ptr in sorted(set(_ACCEPTED_BOUNDS) - set(rows))]
    for ptr in sorted(set(rows) & set(_ACCEPTED_BOUNDS)):
        frozen, accepted = rows[ptr], _ACCEPTED_BOUNDS[ptr]
        for key in sorted(set(frozen) | set(accepted)):
            if key not in frozen or key not in accepted:
                changes.append(f"{ptr}.{key}: bound added or removed")
                continue
            was, now = accepted[key], frozen[key]
            if now == was:
                continue
            floor = key.endswith("_min_fraction") or key.endswith("_min_agreement")
            widened = now < was if floor else now > was
            changes.append(
                f"{ptr}.{key}: {'WIDENED' if widened else 'tightened'} from {was!r} to {now!r}"
            )
    return changes


def test_frozen_bounds_match_the_accepted_copy():
    """No frozen bound changes without the accepted copy changing with it (#545, item 4)."""
    data = json.loads(FROZEN_JSON.read_text(encoding="utf-8"))
    assert _bound_changes(data) == [], (
        "a frozen parity bound differs from its accepted copy in tests/test_parity.py; a "
        "re-freeze updates both under an ADR"
    )


def test_widening_any_one_frozen_bound_fails():
    """Each bound, widened alone by a small step, is reported as widened."""
    import copy

    data = json.loads(FROZEN_JSON.read_text(encoding="utf-8"))
    pointers = _frozen_bound_rows(data)
    assert set(pointers) == set(_ACCEPTED_BOUNDS)
    widened_any = 0
    for ptr, row in pointers.items():
        for key in row:
            mutated = copy.deepcopy(data)
            target = _frozen_bound_rows(mutated)[ptr]
            floor = key.endswith("_min_fraction") or key.endswith("_min_agreement")
            target[key] = target[key] - 1e-4 if floor else target[key] + 1e-4
            changes = _bound_changes(mutated)
            assert changes == [f"{ptr}.{key}: WIDENED from {row[key]!r} to {target[key]!r}"]
            widened_any += 1
    assert widened_any == sum(len(r) for r in _ACCEPTED_BOUNDS.values())


def test_a_removed_or_added_bound_fails():
    import copy

    data = json.loads(FROZEN_JSON.read_text(encoding="utf-8"))
    removed = copy.deepcopy(data)
    del removed["tolerance_by_method"]["ebhmm"]["relative_elbo_max"]
    assert _bound_changes(removed) == [
        "$.tolerance_by_method.ebhmm.relative_elbo_max: bound added or removed"
    ]
    added = copy.deepcopy(data)
    added["tolerance_by_method"]["vbfret"] = dict(data["tolerance"])
    assert _bound_changes(added) == ["$.tolerance_by_method.vbfret: row added"]


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
    tightened below its evidence — not on a loosened one, which
    :func:`test_frozen_bounds_match_the_accepted_copy` catches (see the direction caveat
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


def _measured_blocks(data):
    """Every block of the artifact that records a ``pooled_worst`` beside its evidence."""
    blocks = {"$": data}
    for method, block in data.get("measured_by_method", {}).items():
        blocks[f"$.measured_by_method.{method}"] = block
    return blocks


def test_pooled_worst_is_the_aggregate_of_its_own_evidence():
    """``pooled_worst``, the headline "measured result" of (b), is its own evidence (#748).

    ``scripts/measure_parity.py`` writes each ``pooled_worst`` metric as the worst case
    of every run of every fixture pooled — a floor's minimum, a ceiling's maximum,
    through the production :class:`SpreadSummary`. ``docs/validation.md`` publishes this
    block and its guard pins the page to it, but the evidence checks above read only
    ``spread_by_fixture``. Without this, the JSON and the page could state a different
    headline together while the per-run values in the same file stayed as they were.
    Exact equality: the pooled worst case is one of the recorded values, not an estimate.
    """
    data = json.loads(FROZEN_JSON.read_text(encoding="utf-8"))
    blocks = _measured_blocks(data)
    assert set(blocks) == {
        "$",
        *(f"$.measured_by_method.{m}" for m in _EXPECTED_EVIDENCE_BY_METHOD),
    }
    for label, block in blocks.items():
        pooled = block["pooled_worst"]
        assert set(pooled) == set(_FLOORS) | set(_CEILINGS), f"{label}: pooled_worst metric set"
        for metric, recorded in pooled.items():
            direction = "floor" if metric in _FLOORS else "ceiling"
            values = [
                value
                for fixture in block["spread_by_fixture"].values()
                for value in fixture["metrics"][metric]["values"]
            ]
            assert values, f"{label}: no recorded {metric} values to pool"
            pooled_worst = SpreadSummary(metric, direction, values).worst
            assert recorded == pooled_worst, (
                f"{label}.pooled_worst.{metric} is {recorded!r}, but the worst of the "
                f"{len(values)} recorded runs is {pooled_worst!r}"
            )


# --------------------------------------------------------------------------- #
# transition-matrix term (#545, ADR-0110)                                      #
# --------------------------------------------------------------------------- #


def _tm_model(means, norm_tmatrix):
    model = _model(means)
    model.norm_tmatrix = None if norm_tmatrix is None else np.asarray(norm_tmatrix, "float64")
    return model


def test_transition_matrix_delta_is_permutation_invariant():
    # B is A with its two state labels swapped: rows and columns permute together.
    a = _tm_model([0.1, 0.9], [[0.95, 0.05], [0.10, 0.90]])
    b = _tm_model([0.9, 0.1], [[0.90, 0.10], [0.05, 0.95]])
    assert transition_matrix_abs_delta(a, b) == 0.0
    c = _tm_model([0.9, 0.1], [[0.88, 0.12], [0.05, 0.95]])
    assert transition_matrix_abs_delta(a, c) == pytest.approx(0.02)


def test_transition_matrix_delta_normalizes_a_raw_tmatrix():
    a = _tm_model([0.1, 0.9], [[0.95, 0.05], [0.10, 0.90]])
    b = _model([0.1, 0.9])
    b.tmatrix = np.array([[95.0, 5.0], [10.0, 90.0]])  # counts, rows not normalized
    assert transition_matrix_abs_delta(a, b) == pytest.approx(0.0, abs=1e-15)


def test_transition_matrix_delta_is_inf_when_incomparable():
    two = _tm_model([0.1, 0.9], [[0.95, 0.05], [0.10, 0.90]])
    three = _tm_model([0.1, 0.5, 0.9], np.full((3, 3), 1 / 3))
    assert transition_matrix_abs_delta(two, three) == float("inf")  # state counts differ
    assert transition_matrix_abs_delta(two, _tm_model([0.1, 0.9], None)) == float("inf")
    wrong_shape = _tm_model([0.1, 0.9], np.full((3, 3), 1 / 3))
    assert transition_matrix_abs_delta(two, wrong_shape) == float("inf")
    broken = _tm_model([0.1, 0.9], [[NAN, 0.05], [0.10, 0.90]])
    assert transition_matrix_abs_delta(two, broken) == float("inf")
    # A raw (not row-normalized) matrix that is not 2-D is malformed: inf, not an exception.
    for raw in (np.array([0.95, 0.05]), np.array(0.9)):
        flat = _model([0.1, 0.9])
        flat.tmatrix = raw
        assert transition_matrix_abs_delta(two, flat) == float("inf")


def test_compare_models_carries_the_transition_matrix_term():
    ideal = [[0.1, 0.9, 0.9]]
    a = _tm_model([0.1, 0.9], [[0.95, 0.05], [0.10, 0.90]])
    a.idealized = np.asarray(ideal)
    b = _tm_model([0.1, 0.9], [[0.94, 0.06], [0.10, 0.90]])
    b.idealized = np.asarray(ideal)
    assert compare_models(a, b).transition_matrix_abs_delta == pytest.approx(0.01)
    # Not computed is inf, so a hand-built record can never pass the bound by default.
    m = ParityMetrics(1.0, 0.0, 1.0, 0.0, 2, 2)
    assert m.transition_matrix_abs_delta == float("inf")


def test_within_transition_matrix_tolerance():
    ok_m = ParityMetrics(1.0, 0.0, 1.0, 0.0, 2, 2, transition_matrix_abs_delta=0.001)
    assert within_transition_matrix_tolerance(ok_m, 0.002) == (True, [])
    ok, failures = within_transition_matrix_tolerance(ok_m, 0.0005)
    assert not ok and "transition-matrix" in failures[0]
    ok, failures = within_transition_matrix_tolerance(ParityMetrics(1.0, 0.0, 1.0, 0.0, 2, 2), 1.0)
    assert not ok and "non-finite" in failures[0]


def test_freeze_transition_matrix_rounds_up_and_never_below_one_step():
    def spread(values):
        return SpreadSummary("transition_matrix_abs_delta", "ceiling", values)

    # 0.0123 × 1.5 = 0.01845 -> up to 0.0185 (round-to-nearest would also give it; 0.01841 not)
    assert freeze_transition_matrix(spread([0.01, 0.0123])) == pytest.approx(0.0185)
    assert freeze_transition_matrix(spread([0.01227])) == pytest.approx(0.0185)  # 0.018405 up
    # An all-but-zero spread freezes one step of the resolution, not 0.0.
    assert freeze_transition_matrix(spread([1e-9, 3e-9])) == FREEZE_RESOLUTION
    assert freeze_transition_matrix(spread([0.0])) == FREEZE_RESOLUTION
    # The frozen ceiling is never below the widened worst case.
    for worst in (0.00011, 0.0123456, 0.031, 0.2):
        assert freeze_transition_matrix(spread([worst])) >= worst * 1.5 - 1e-12
    with pytest.raises(ValueError, match="non-finite"):
        freeze_transition_matrix(spread([0.01, float("inf")]))
    with pytest.raises(ValueError, match="ceiling"):
        freeze_transition_matrix(SpreadSummary("x", "floor", [0.5]))


#: The evidence behind each `$.transition_matrix` ceiling (#545, ADR-0110), pinned for the
#: reason `_EXPECTED_EVIDENCE` is: deleting the run that set a ceiling, or re-labelling what
#: it was measured against, is a re-freeze, never a silent edit. vbconhmm's term comes from
#: the same runs as the default row's re-measure; ebFRET's from its own unseeded
#: `--cross-seed` run of `smd_281mol`, the fixture its four-bound row was measured on.
_EXPECTED_TM_EVIDENCE = {
    "vbconhmm": _EXPECTED_EVIDENCE,
    "ebhmm": _EXPECTED_EVIDENCE_BY_METHOD["ebhmm"],
}
_TM_METRIC = "transition_matrix_abs_delta"


def test_transition_matrix_block_covers_its_own_evidence():
    """Each transition-matrix ceiling is the freeze of its own recorded runs (#545, item 2).

    For each method: the measured fixture set, SMD paths, anchors, state counts and
    comparison counts match the pin; each summary is recomputed from its ``values``; every
    value is within the ceiling; ``pooled_worst`` is the worst of all of them; the ceiling
    *is* ``freeze_transition_matrix`` of that pooled spread under the block's recorded
    margin; the loader returns it; and the runs were measured on the build of ``$.method``.
    """
    data = json.loads(FROZEN_JSON.read_text(encoding="utf-8"))
    block = data["transition_matrix"]
    assert block["metric"] == _TM_METRIC
    assert block["freeze_policy"]["resolution"] == FREEZE_RESOLUTION
    margin = block["freeze_policy"]["margin"]
    assert set(block["tolerance_by_method"]) == set(block["measured_by_method"])
    assert set(block["tolerance_by_method"]) == set(_EXPECTED_TM_EVIDENCE)
    for method, measured in block["measured_by_method"].items():
        ceiling = block["tolerance_by_method"][method]["transition_matrix_abs_delta_max"]
        assert load_transition_matrix_tolerance(FROZEN_JSON, method) == ceiling
        build = measured["method"]
        assert build["model_type"] == method
        for key in ("tmaven_commit", "sidecar_python_version"):
            assert build[key] == data["method"][key], f"{method}: measured on another build"
        n_runs = build["n_runs_per_fixture"]
        expected = _EXPECTED_TM_EVIDENCE[method]
        spread = measured["spread_by_fixture"]
        assert set(spread) == set(expected), f"{method}: measured fixture set changed"
        pooled = []
        for name, fixture in spread.items():
            pin = expected[name]
            for key in ("smd", "reference", "nstates"):
                assert fixture[key] == pin[key], f"{method}/{name}: frozen {key} changed"
            anchored = pin["reference"] == _ANCHOR_REFERENCE
            assert pin["n_comparisons"] == (n_runs - 1 if anchored else n_runs)
            assert fixture["n_comparisons"] == pin["n_comparisons"]
            assert set(fixture["metrics"]) == {_TM_METRIC}
            summ = fixture["metrics"][_TM_METRIC]
            assert summ["direction"] == "ceiling"
            values = summ["values"]
            assert len(values) == summ["n"] == pin["n_comparisons"]
            recomputed = SpreadSummary(_TM_METRIC, "ceiling", values).as_dict()
            for key in ("n", "min", "max", "worst"):
                assert recomputed[key] == summ[key], f"{method}/{name}: stale {key}"
            assert recomputed["mean"] == pytest.approx(summ["mean"], rel=1e-12)
            assert all(v <= ceiling for v in values), f"{method}/{name}: above the ceiling"
            pooled += values
        worst = SpreadSummary(_TM_METRIC, "ceiling", pooled)
        assert measured["pooled_worst"] == {_TM_METRIC: worst.worst}
        assert ceiling == freeze_transition_matrix(worst, margin=margin), (
            f"{method}: the ceiling is not the freeze of its own evidence"
        )
