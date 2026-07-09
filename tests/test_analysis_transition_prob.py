# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Transition-probability histogram (M6 B3, FR-ANALYZE; PRD §7.7, Appendix C B3).

Tether's B3 is the consensus-model analogue of tMAVEN's ``tm_hist`` per-trace
``norm_tmatrix[init, fin]`` histogram: each molecule's transition probability is the
maximum-likelihood one-step ``P(init → fin)`` estimated from its persisted Viterbi
path. The store path enforces the two Tether invariants tMAVEN has no analogue for —
**fresh idealizations only** (PRD §5.1) and the §7.5 curation filter — as the B1 TDP
does. All headless (no Qt) → base CI matrix; the KDE overlay uses ``scipy.stats``.
"""

from __future__ import annotations

import pytest

pytest.importorskip("numpy")
pytest.importorskip("h5py")

import numpy as np  # noqa: E402

from _analysis_store import MEANS, build_store_with_model  # noqa: E402
from tether.analysis import (  # noqa: E402
    DEFAULT_TPROB_KDE_BANDWIDTH,
    DEFAULT_TPROB_KDE_POINTS,
    DEFAULT_TPROB_NBINS,
    DEFAULT_TPROB_RANGE,
    TransitionProbHistogram,
    empirical_transition_probability,
    population_transition_prob_histogram,
    transition_prob_histogram,
)
from tether.idealize import NO_STATE  # noqa: E402

# --- pure core: empirical_transition_probability ------------------------------


def test_empirical_probability_hand_checked() -> None:
    # from state 0: successors (0->0),(0->0),(0->1) -> denom 3
    v = np.array([0, 0, 0, 1, 1, 1])
    assert empirical_transition_probability(v, 0, 1) == pytest.approx(1 / 3)
    assert empirical_transition_probability(v, 0, 0) == pytest.approx(2 / 3)  # self-pairs count
    # from state 1: only (1->1),(1->1) -> no exit; P(1->0)=0, P(1->1)=1
    assert empirical_transition_probability(v, 1, 0) == pytest.approx(0.0)
    assert empirical_transition_probability(v, 1, 1) == pytest.approx(1.0)


def test_empirical_probability_undefined_when_init_absent() -> None:
    v = np.array([0, 0, 1, 1])
    assert empirical_transition_probability(v, 2, 0) is None  # state 2 never occupied
    assert empirical_transition_probability(np.array([0]), 0, 1) is None  # length < 2
    assert empirical_transition_probability(np.array([], dtype="int64"), 0, 1) is None


def test_empirical_probability_gap_successor_excluded() -> None:
    # state 0 at frame 0, but its successor is a gap -> not an observed transition.
    v = np.array([0, NO_STATE, 1, 1])
    assert empirical_transition_probability(v, 0, 1) is None  # denom 0 (gap successor)
    # a real 0->1 plus a gap-terminated 0: only the observed pair counts.
    v2 = np.array([0, 1, 1, 0, NO_STATE])
    assert empirical_transition_probability(v2, 0, 1) == pytest.approx(1.0)  # 1 of 1 observed


# --- pure core: transition_prob_histogram -------------------------------------


def test_defaults_match_tmaven() -> None:
    assert DEFAULT_TPROB_NBINS == 25
    assert DEFAULT_TPROB_RANGE == (-0.05, 1.05)
    assert DEFAULT_TPROB_KDE_BANDWIDTH == 0.25
    assert DEFAULT_TPROB_KDE_POINTS == 100


def test_shape_and_edges() -> None:
    h = transition_prob_histogram([], init_state=0, final_state=1, prob_bins=10)
    assert isinstance(h, TransitionProbHistogram)
    assert h.counts.shape == (10,)
    assert h.edges.shape == (11,)
    assert h.n_bins == 10
    np.testing.assert_allclose(h.edges, np.linspace(-0.05, 1.05, 11))
    np.testing.assert_allclose(h.centers, 0.5 * (h.edges[:-1] + h.edges[1:]))


def test_empty_is_all_zero_never_nan() -> None:
    h = transition_prob_histogram([], init_state=0, final_state=1)
    assert h.n_molecules == 0
    assert h.counts.sum() == 0.0
    assert not np.any(np.isnan(h.counts))
    assert h.kde_x is None and h.kde_y is None
    assert h.probabilities.size == 0


def test_molecules_without_init_are_dropped() -> None:
    chunks = [
        np.array([0, 0, 1]),  # P(0->1) = 0.5 (successors 0->0, 0->1)
        np.array([2, 2, 2]),  # never in state 0 -> dropped
    ]
    h = transition_prob_histogram(chunks, init_state=0, final_state=1, kde=False)
    assert h.n_molecules == 1
    np.testing.assert_allclose(h.probabilities, np.array([0.5]))


def test_density_integrates_to_one() -> None:
    # three molecules with defined P(0->1); density histogram integrates to 1.
    chunks = [np.array([0, 1]), np.array([0, 0, 1]), np.array([0, 0, 0, 1])]
    h = transition_prob_histogram(chunks, init_state=0, final_state=1, density=True, kde=False)
    width = np.diff(h.edges)[0]
    assert float(h.counts.sum() * width) == pytest.approx(1.0, rel=1e-9)
    assert h.density is True


def test_raw_counts_when_density_false() -> None:
    chunks = [np.array([0, 1]), np.array([0, 1])]  # both P=1.0
    h = transition_prob_histogram(
        chunks, init_state=0, final_state=1, density=False, kde=False, prob_bins=25
    )
    assert h.counts.sum() == 2.0  # raw counts
    assert h.density is False


def test_density_empty_never_nan() -> None:
    h = transition_prob_histogram([], init_state=0, final_state=1, density=True)
    assert h.counts.sum() == 0.0
    assert not np.any(np.isnan(h.counts))


def test_density_all_out_of_range_never_nan() -> None:
    # a non-empty population whose every probability falls OUTSIDE a narrowed prob_range:
    # the density path would divide by the in-range count (0) -> all-NaN. The guard must
    # gate on in-range mass, not population size, so this stays all-zeros (never NaN).
    chunks = [np.array([0, 0, 1]), np.array([0, 0, 1])]  # both P(0->1) = 0.5
    h = transition_prob_histogram(
        chunks, init_state=0, final_state=1, density=True, prob_range=(0.8, 0.9), kde=False
    )
    assert h.n_molecules == 2  # both still counted
    assert h.counts.sum() == 0.0
    assert not np.any(np.isnan(h.counts))


# --- pure core: KDE overlay ---------------------------------------------------


def test_kde_present_with_two_distinct_probs() -> None:
    # P values {0.5, 1.0}: computable KDE.
    chunks = [np.array([0, 0, 1]), np.array([0, 1])]
    h = transition_prob_histogram(chunks, init_state=0, final_state=1, kde=True)
    assert h.kde_x is not None and h.kde_y is not None
    assert h.kde_x.shape == (DEFAULT_TPROB_KDE_POINTS,)
    assert h.kde_y.shape == (DEFAULT_TPROB_KDE_POINTS,)
    np.testing.assert_allclose(h.kde_x[[0, -1]], [0.0, 1.0])
    assert np.all(np.isfinite(h.kde_y))


def test_kde_none_when_all_probs_identical() -> None:
    # two molecules, both P(0->1)=1.0 -> singular covariance -> no curve (never crash).
    chunks = [np.array([0, 1]), np.array([0, 1])]
    h = transition_prob_histogram(chunks, init_state=0, final_state=1, kde=True)
    assert h.n_molecules == 2
    assert h.kde_x is None and h.kde_y is None


def test_kde_none_with_single_molecule() -> None:
    h = transition_prob_histogram([np.array([0, 1])], init_state=0, final_state=1, kde=True)
    assert h.n_molecules == 1
    assert h.kde_x is None and h.kde_y is None


def test_kde_disabled() -> None:
    chunks = [np.array([0, 0, 1]), np.array([0, 1])]
    h = transition_prob_histogram(chunks, init_state=0, final_state=1, kde=False)
    assert h.kde_x is None and h.kde_y is None


def test_kde_points_and_bandwidth_respected() -> None:
    chunks = [np.array([0, 0, 1]), np.array([0, 1]), np.array([0, 0, 0, 1])]
    h = transition_prob_histogram(
        chunks, init_state=0, final_state=1, kde=True, kde_points=50, kde_bandwidth=0.4
    )
    assert h.kde_x is not None
    assert h.kde_x.shape == (50,)


def test_kde_bandwidth_changes_curve() -> None:
    # the bandwidth must actually reach scipy: two curves from the same data at
    # different bandwidths differ (a mutant that hardcodes the default is caught here).
    chunks = [np.array([0, 0, 1]), np.array([0, 1]), np.array([0, 0, 0, 1])]
    h_low = transition_prob_histogram(chunks, init_state=0, final_state=1, kde_bandwidth=0.25)
    h_high = transition_prob_histogram(chunks, init_state=0, final_state=1, kde_bandwidth=0.4)
    assert h_low.kde_y is not None and h_high.kde_y is not None
    assert not np.allclose(h_low.kde_y, h_high.kde_y)


# --- pure core: validation ----------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"init_state": -1, "final_state": 0}, "init_state"),
        ({"init_state": 0, "final_state": -1}, "final_state"),
        ({"init_state": 0, "final_state": 1, "prob_bins": 0}, "prob_bins"),
        ({"init_state": 0, "final_state": 1, "prob_range": (1.0, 1.0)}, "prob_range"),
        ({"init_state": 0, "final_state": 1, "prob_range": (1.0, 0.0)}, "prob_range"),
        ({"init_state": 0, "final_state": 1, "kde_bandwidth": 0.0}, "kde_bandwidth"),
        ({"init_state": 0, "final_state": 1, "kde_points": 1}, "kde_points"),
    ],
)
def test_validation_errors(kwargs, match) -> None:
    with pytest.raises(ValueError, match=match):
        transition_prob_histogram([], **kwargs)


def test_flat_array_misuse_raises_not_silent() -> None:
    with pytest.raises(ValueError, match="scalar element"):
        transition_prob_histogram(np.array([0, 0, 1]), init_state=0, final_state=1)
    with pytest.raises(ValueError, match="scalar element"):
        transition_prob_histogram([0, 0, 1], init_state=0, final_state=1)
    ok = transition_prob_histogram(np.array([[0, 0, 1]]), init_state=0, final_state=1, kde=False)
    assert ok.n_molecules == 1


# --- store-level: seed a .tether with molecules + traces + idealization -------


def _states() -> np.ndarray:
    # molecule 0: two 0->1 exits out of three 0-with-successor frames -> P(0->1)=2/3
    #   path 0,0,1,0,1,1 : frame0 0->0, frame1 0->1, frame3 0->1 ; denom 3, numer 2
    # molecule 1: single 0->1 -> P(0->1)=1.0
    return np.array(
        [
            [0, 0, 1, 0, 1, 1],
            [0, 1, 1, 1, 1, 1],
        ],
        dtype="int64",
    )


def test_population_matches_core(tmp_path) -> None:
    s = _states()
    proj, _keys = build_store_with_model(tmp_path, s, MEANS)
    h = population_transition_prob_histogram(proj, "vbconhmm", 0, 1, kde=False)
    assert isinstance(h, TransitionProbHistogram)
    assert h.n_molecules == 2
    np.testing.assert_allclose(sorted(h.probabilities), [2 / 3, 1.0])
    ref = transition_prob_histogram([s[0], s[1]], init_state=0, final_state=1, kde=False)
    np.testing.assert_array_equal(h.counts, ref.counts)


def test_population_state_range_validation(tmp_path) -> None:
    s = _states()
    proj, _keys = build_store_with_model(tmp_path, s, MEANS)  # nstates == 3
    with pytest.raises(ValueError, match="init_state"):
        population_transition_prob_histogram(proj, "vbconhmm", 3, 0)
    with pytest.raises(ValueError, match="final_state"):
        population_transition_prob_histogram(proj, "vbconhmm", 0, 5)


def test_stale_molecule_excluded_by_default(tmp_path) -> None:
    s = _states()
    proj, _keys = build_store_with_model(tmp_path, s, MEANS, stale=[False, True])
    h = population_transition_prob_histogram(proj, "vbconhmm", 0, 1, kde=False)
    assert h.n_molecules == 1
    np.testing.assert_allclose(h.probabilities, [2 / 3])


def test_include_stale_restores_the_molecule(tmp_path) -> None:
    s = _states()
    proj, _keys = build_store_with_model(tmp_path, s, MEANS, stale=[False, True])
    h = population_transition_prob_histogram(proj, "vbconhmm", 0, 1, include_stale=True, kde=False)
    assert h.n_molecules == 2


def test_rejected_molecule_excluded_by_default(tmp_path) -> None:
    s = _states()
    proj, _keys = build_store_with_model(tmp_path, s, MEANS, rejected=[False, True])
    h = population_transition_prob_histogram(proj, "vbconhmm", 0, 1, kde=False)
    assert h.n_molecules == 1


def test_include_rejected_restores_the_molecule(tmp_path) -> None:
    s = _states()
    proj, _keys = build_store_with_model(tmp_path, s, MEANS, rejected=[False, True])
    h = population_transition_prob_histogram(
        proj, "vbconhmm", 0, 1, include_rejected=True, kde=False
    )
    assert h.n_molecules == 2


def test_molecule_keys_selection(tmp_path) -> None:
    s = _states()
    proj, keys = build_store_with_model(tmp_path, s, MEANS)
    h = population_transition_prob_histogram(
        proj, "vbconhmm", 0, 1, molecule_keys=[keys[1]], kde=False
    )
    assert h.n_molecules == 1
    np.testing.assert_allclose(h.probabilities, [1.0])


def test_molecule_keys_intersect_fresh(tmp_path) -> None:
    # explicitly selecting a STALE key yields nothing: the fresh intersection (not just
    # the molecule_keys selection) gates it. include_stale restores it.
    s = _states()
    proj, keys = build_store_with_model(tmp_path, s, MEANS, stale=[False, True])
    h = population_transition_prob_histogram(
        proj, "vbconhmm", 0, 1, molecule_keys=[keys[1]], kde=False
    )
    assert h.n_molecules == 0
    assert h.probabilities.size == 0
    h2 = population_transition_prob_histogram(
        proj, "vbconhmm", 0, 1, molecule_keys=[keys[1]], include_stale=True, kde=False
    )
    assert h2.n_molecules == 1  # the stale molecule (P=1.0) restored
    np.testing.assert_allclose(h2.probabilities, [1.0])


def test_missing_model_raises(tmp_path) -> None:
    s = _states()
    proj, _keys = build_store_with_model(tmp_path, s, MEANS)
    with pytest.raises(KeyError):
        population_transition_prob_histogram(proj, "no-such-model", 0, 1)
