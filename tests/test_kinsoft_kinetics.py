# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""kinSoftChallenge kinetics oracle (M8; PRD §8 NFR-VALID(c), §9 M8).

Three layers, mirroring ``test_oracle.py``:

* **pure primitives** (numpy only, required matrix): the base-env Gaussian HMM
  recovers a synthetic 2-state chain's states/means; the pooled dwell-time MLE
  gives the exact ``1/⟨τ⟩`` on a hand-built path; the frozen-reference loader and
  the deferred-level guards.
* **synthetic recovery**: the full ``two_state_rate_constants`` pipeline recovers
  the exit rates of a generated 2-state Markov process (compared to the rates of
  its own ground-truth idealization, so the check is sampling-robust).
* **data-present, gated** (``@pytest.mark.large``; skipped without the LFS
  ``kinsoft_sim.hdf5``): Tether's fit on the real challenge level-1 traces lands
  within the reported inter-tool spread of [Götz2022] (the advisory band).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from tether.analysis.kinetics import (
    evaluate_kinsoft_level,
    fit_gaussian_hmm,
    load_kinsoft_reference,
    pooled_exit_rates,
    two_state_rate_constants,
    viterbi_paths,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FIXTURE = FIXTURES / "large" / "kinsoft_sim.hdf5"
REFERENCE = Path(__file__).resolve().parents[1] / "schema" / "kinsoft_reference.json"


def _is_lfs_pointer(path: Path) -> bool:
    """True if ``path`` is absent or a Git-LFS pointer stub (not real data)."""
    if not path.exists():
        return True
    if path.stat().st_size > 4096:
        return False
    return path.read_bytes()[:64].startswith(b"version https://git-lfs")


def _synthetic_two_state(
    n_traces: int,
    length: int,
    *,
    p01: float,
    p10: float,
    means: tuple[float, float],
    sigma: float,
    seed: int,
) -> tuple[list[np.ndarray], list[np.ndarray]]:
    """Generate ``n_traces`` 2-state Markov FRET traces with Gaussian emissions.

    Returns ``(observations, true_state_paths)``; deterministic given ``seed``.
    """
    rng = np.random.default_rng(seed)
    obs: list[np.ndarray] = []
    paths: list[np.ndarray] = []
    for _ in range(n_traces):
        states = np.empty(length, dtype=np.int64)
        s = int(rng.integers(2))
        for t in range(length):
            states[t] = s
            r = rng.random()
            if s == 0 and r < p01:
                s = 1
            elif s == 1 and r < p10:
                s = 0
        obs.append(rng.normal(np.asarray(means)[states], sigma))
        paths.append(states)
    return obs, paths


# --- pure primitives ----------------------------------------------------------


def test_pooled_exit_rates_exact() -> None:
    # runs: [0,0] [1,1,1] [0,0] [1,1] [0]; state_dwells drops the first + last run.
    # interior dwells: state1 -> {3, 2}, state0 -> {2}.
    path = np.array([0, 0, 1, 1, 1, 0, 0, 1, 1, 0], dtype=np.int64)
    rates = pooled_exit_rates([path], dt=0.5)
    # state0 mean dwell = 2 frames * 0.5 s = 1.0 s -> k = 1.0
    assert rates[0] == pytest.approx(1.0)
    # state1 mean dwell = mean(3, 2) * 0.5 s = 1.25 s -> k = 0.8
    assert rates[1] == pytest.approx(0.8)


def test_pooled_exit_rates_include_first() -> None:
    path = np.array([0, 0, 1, 1, 1, 0, 0, 1, 1, 0], dtype=np.int64)
    rates = pooled_exit_rates([path], dt=1.0, include_first=True)
    # keeping the first [0,0] run: state0 dwells {2, 2} -> mean 2 -> k 0.5
    assert rates[0] == pytest.approx(0.5)
    assert rates[1] == pytest.approx(1.0 / (np.mean([3, 2])))


def test_no_transition_path_has_no_rates() -> None:
    # A single run is censored on both ends -> no dwell contributes.
    assert pooled_exit_rates([np.zeros(50, dtype=np.int64)], dt=0.2) == {}


def test_fit_gaussian_hmm_validation() -> None:
    with pytest.raises(ValueError, match="nstates must be >= 1"):
        fit_gaussian_hmm([np.zeros(5)], 0)
    with pytest.raises(ValueError, match="no non-empty traces"):
        fit_gaussian_hmm([np.empty(0)], 2)


def test_viterbi_recovers_clean_two_state_signal() -> None:
    # A clean low-then-high step: the fit + Viterbi must return [0]*10 + [1]*10.
    x = np.concatenate([np.full(10, 0.2), np.full(10, 0.8)])
    hmm = fit_gaussian_hmm([x], 2)
    assert hmm.nstates == 2
    assert hmm.means[0] < hmm.means[1]  # canonical ascending order
    (path,) = viterbi_paths([x], hmm)
    expected = np.array([0] * 10 + [1] * 10, dtype=np.int64)
    assert np.array_equal(path, expected)


def test_fit_gaussian_hmm_recovers_synthetic_states() -> None:
    obs, true_paths = _synthetic_two_state(
        40, 800, p01=0.05, p10=0.08, means=(0.25, 0.72), sigma=0.06, seed=7
    )
    hmm = fit_gaussian_hmm(obs, 2)
    assert hmm.means[0] == pytest.approx(0.25, abs=0.03)
    assert hmm.means[1] == pytest.approx(0.72, abs=0.03)
    # Viterbi agrees with the generating state path on the vast majority of frames.
    paths = viterbi_paths(obs, hmm)
    agree = np.mean([np.mean(p == t) for p, t in zip(paths, true_paths, strict=True)])
    assert agree > 0.95


def test_two_state_rate_constants_recovers_synthetic_rates() -> None:
    obs, true_paths = _synthetic_two_state(
        40, 800, p01=0.05, p10=0.08, means=(0.25, 0.72), sigma=0.06, seed=11
    )
    dt = 0.2
    truth = pooled_exit_rates(true_paths, dt)  # rates of the ground-truth idealization
    kin = two_state_rate_constants(obs, dt)
    # The HMM-idealized rates match the true-path rates to within sampling noise.
    assert kin.k_low_high == pytest.approx(truth[0], rel=0.15)
    assert kin.k_high_low == pytest.approx(truth[1], rel=0.15)
    assert kin.n_dwells_low > 100
    assert kin.n_dwells_high > 100


# --- frozen reference ---------------------------------------------------------


def test_load_kinsoft_reference() -> None:
    ref = load_kinsoft_reference(REFERENCE)
    assert ref.band_rate_rel_deviation_max == pytest.approx(0.12)
    # Only the archetypal 2-state level has an active oracle.
    assert ref.active_levels() == ["level1"]
    lvl1 = ref.levels["level1"]
    assert lvl1["nstates"] == 2
    gt = lvl1["ground_truth"]["rates_s_inv"]
    assert gt["k12_low_high"] == pytest.approx(0.15)
    assert gt["k21_high_low"] == pytest.approx(0.22)
    # The 4-state (Fig. 4) ground truth is recorded exactly (Suppl. Table 1).
    l3 = ref.levels["level3"]["ground_truth"]["rates_s_inv"]
    assert l3["k32"] == pytest.approx(0.68)
    assert l3["k23"] == pytest.approx(0.25)
    # Tether's own measured deviations sit comfortably inside the band.
    meas = lvl1["tether_measured"]
    assert meas["k12_rel_dev"] < ref.band_rate_rel_deviation_max
    assert meas["k21_rel_dev"] < ref.band_rate_rel_deviation_max


@pytest.mark.parametrize("deferred", ["level2", "level3"])
def test_evaluate_rejects_deferred_levels(deferred: str) -> None:
    ref = load_kinsoft_reference(REFERENCE)

    class _Dummy:  # never fitted (the guard fires first)
        n_traces = 0
        frame_time_s = 0.2

    with pytest.raises(ValueError, match="no active 2-state oracle"):
        evaluate_kinsoft_level(_Dummy(), ref, deferred)


# --- data-present, gated ------------------------------------------------------


@pytest.mark.large
def test_kinsoft_level1_within_inter_tool_spread() -> None:
    if _is_lfs_pointer(FIXTURE):
        pytest.skip("LFS large-tier fixture not materialized (default checkout)")
    pytest.importorskip("h5py")
    from tether.io.kinsoft import read_kinsoft_fixture

    ref = load_kinsoft_reference(REFERENCE)
    level1 = read_kinsoft_fixture(FIXTURE)["level1"]
    result = evaluate_kinsoft_level(level1, ref, "level1")

    # Both rates fall within the reported inter-tool spread (advisory band).
    assert result.within_band, result.failures
    assert result.rel_deviation["k12_low_high"] <= ref.band_rate_rel_deviation_max
    assert result.rel_deviation["k21_high_low"] <= ref.band_rate_rel_deviation_max

    # And they match the frozen tether_measured values (base-env HMM is deterministic;
    # allow a small margin for cross-platform floating-point in the Viterbi path).
    meas = ref.levels["level1"]["tether_measured"]
    assert result.rates["k12_low_high"] == pytest.approx(meas["k12_low_high"], abs=0.01)
    assert result.rates["k21_high_low"] == pytest.approx(meas["k21_high_low"], abs=0.01)
    # Rates rest on thousands of pooled dwells across the 75 traces.
    assert result.kinetics.n_dwells_low > 500
    assert result.kinetics.n_dwells_high > 500
