# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""kinSoftChallenge kinetics oracle (M8; PRD §8 NFR-VALID(c), §9 M8).

Three layers, mirroring ``test_oracle.py``:

* **pure primitives** (numpy only, required matrix): the base-env Gaussian HMM
  recovers every parameter of a synthetic 2-state chain, iterates, and stops on the
  documented ``tol`` rule; the pooled dwell-time MLE gives the exact ``1/⟨τ⟩`` on a
  hand-built path; the frozen-reference loader and the deferred-level guards.
* **synthetic recovery**: the full ``two_state_rate_constants`` pipeline recovers
  the exit rates of a generated 2-state Markov process (compared to the rates of
  its own ground-truth idealization, so the check is sampling-robust), and
  ``evaluate_kinsoft_level`` scores a synthetic level built in the test.
* **data-present, gated** (``@pytest.mark.large``; skipped without the LFS
  ``kinsoft_sim.hdf5``): Tether's fit on the real challenge level-1 traces lands
  within the reported inter-tool spread of [Götz2022] (the advisory band) and
  reproduces the frozen rates.

The finding ids in the comments (ANA-F1, ANA-F2, ANA-U1, DCN-F5) are those of #740.
"""

from __future__ import annotations

import copy
from pathlib import Path

import numpy as np
import pytest

from tether.analysis.dwell import state_dwells
from tether.analysis.kinetics import (
    DEFAULT_HMM_MAX_ITER,
    DEFAULT_HMM_TOL,
    KinsoftReference,
    evaluate_kinsoft_level,
    fit_gaussian_hmm,
    load_kinsoft_reference,
    pooled_exit_rates,
    two_state_rate_constants,
    viterbi_paths,
)
from tether.io.kinsoft import KinsoftLevel

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


def _transition_frequencies(paths: list[np.ndarray]) -> np.ndarray:
    """Row-normalized count of the frame-to-frame transitions of 2-state ``paths``."""
    counts = np.zeros((2, 2))
    for p in paths:
        np.add.at(counts, (p[:-1], p[1:]), 1)
    return counts / counts.sum(axis=1, keepdims=True)


def _class_statistics(
    obs: list[np.ndarray], paths: list[np.ndarray]
) -> tuple[np.ndarray, np.ndarray]:
    """Mean and population standard deviation of the frames of each state of ``paths``."""
    x = np.concatenate(obs)
    s = np.concatenate(paths)
    return (
        np.array([x[s == k].mean() for k in (0, 1)]),
        np.array([x[s == k].std() for k in (0, 1)]),
    )


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
    # A single run: neither its start nor its exit was observed -> no dwell contributes.
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


def _overlapping_two_state() -> tuple[list[np.ndarray], list[np.ndarray]]:
    """Two states 2 sigma apart, so the quantile initial guess is not already the fit.

    On well-separated states (the generator calls below, 7.8 sigma apart) one EM step
    lands on the answer, which is how a fit that never iterated passed them (#754).
    Returns ``(observations, true_state_paths)``.
    """
    return _synthetic_two_state(4, 1000, p01=0.05, p10=0.08, means=(0.40, 0.60), sigma=0.10, seed=3)


def test_fit_gaussian_hmm_iterates_past_the_first_pass() -> None:
    # #754: the stop rule compared against ``prev_ll = -inf`` on the first pass, which is
    # ``inf <= tol * inf`` and so true for every ``tol > 0``: each default call was one EM step.
    obs, _ = _overlapping_two_state()
    one_step = fit_gaussian_hmm(obs, 2, n_iter=1)
    default = fit_gaussian_hmm(obs, 2)
    assert default.n_iter > 1
    # EM never lowers the likelihood, and the first pass is far from a fixed point here.
    assert default.log_likelihood > one_step.log_likelihood


def test_fit_gaussian_hmm_converged_reports_what_ended_the_loop() -> None:
    obs, _ = _overlapping_two_state()
    capped = fit_gaussian_hmm(obs, 2, n_iter=1)
    assert capped.n_iter == 1
    assert capped.converged is False  # the cap ended the loop, not the stop rule

    default = fit_gaussian_hmm(obs, 2)
    assert default.converged is True
    assert default.n_iter < DEFAULT_HMM_MAX_ITER
    # The fit is deterministic, so a cap one short of the iteration that met the stop
    # rule replays the same iterations and must end on the cap.
    short = fit_gaussian_hmm(obs, 2, n_iter=default.n_iter - 1)
    assert short.n_iter == default.n_iter - 1
    assert short.converged is False


def test_em_iterations_bring_an_overlapping_fit_to_its_generating_path() -> None:
    # ANA-F1: on states 2 sigma apart the first EM step is measurably wrong, so this case
    # can tell a fit that iterates from one that stops early. The bound is the same for
    # both fits; only the iterated one meets it. This says what the iterations do on this
    # data set, not how accurate the fit is in general.
    obs, true_paths = _overlapping_two_state()
    target_t = _transition_frequencies(true_paths)
    target_means, _ = _class_statistics(obs, true_paths)

    first_pass = fit_gaussian_hmm(obs, 2, n_iter=1)
    assert np.abs(first_pass.tmatrix - target_t).max() > 0.005
    assert np.abs(first_pass.means - target_means).max() > 0.005

    fit = fit_gaussian_hmm(obs, 2)
    assert fit.tmatrix == pytest.approx(target_t, abs=0.005)
    assert fit.means == pytest.approx(target_means, abs=0.005)


@pytest.mark.parametrize("tol", [1e-2, 1e-4, DEFAULT_HMM_TOL])
def test_fit_gaussian_hmm_stops_on_the_first_iteration_that_meets_tol(tol: float) -> None:
    # The documented rule: EM stops when two successive total log-likelihoods differ by no
    # more than ``tol * (1 + |previous|)``. The fit is deterministic, so a cap at ``k``
    # replays iterations 1..k and reports iteration k's log-likelihood: that recovers
    # the whole sequence, and the rule can be checked at every iteration. A rule that
    # stops after a fixed number of iterations fails here (#764 review, deferred to #740).
    obs, _ = _overlapping_two_state()
    fit = fit_gaussian_hmm(obs, 2, tol=tol)
    assert fit.converged is True
    lls = [
        fit_gaussian_hmm(obs, 2, n_iter=k, tol=tol).log_likelihood for k in range(1, fit.n_iter + 1)
    ]
    assert lls[-1] == fit.log_likelihood

    def meets_rule(previous: float, current: float) -> bool:
        return abs(current - previous) <= tol * (1.0 + abs(previous))

    steps = list(zip(lls, lls[1:], strict=False))
    assert meets_rule(*steps[-1])  # the last two differ by no more than the threshold
    assert not any(meets_rule(*step) for step in steps[:-1])  # and no earlier pair did
    assert all(current >= previous for previous, current in steps)  # EM is monotone


def test_a_larger_tol_never_iterates_longer() -> None:
    obs, _ = _overlapping_two_state()
    counts = [fit_gaussian_hmm(obs, 2, tol=t).n_iter for t in (1e-2, 1e-4, 1e-6, 1e-8, 1e-10)]
    # Strictly increasing as tol falls (3, 7, 10, 14, 17 iterations on this data set), so
    # the count follows tol and is not fixed.
    assert all(a < b for a, b in zip(counts, counts[1:], strict=False)), counts


def test_tol_zero_stops_once_the_likelihood_stops_changing() -> None:
    # "No more than" means ``<=``: with ``tol=0`` the loop ends on the first iteration
    # whose log-likelihood equals the previous one. A single state makes that exact: each
    # posterior is 1, so every M-step returns the same parameters, and iterations 2 and 3
    # compute the same log-likelihood to the last bit. A strict ``<`` never stops here.
    x = np.array([0.0, 0.0, 0.1, 0.9, 0.3, 0.2, 0.25])
    fit = fit_gaussian_hmm([x], 1, tol=0.0)
    assert fit.converged is True
    assert fit.n_iter == 3  # iteration 1 cannot meet the rule; 2 differs (median -> mean)


def test_fit_gaussian_hmm_recovers_synthetic_states() -> None:
    obs, true_paths = _synthetic_two_state(
        40, 800, p01=0.05, p10=0.08, means=(0.25, 0.72), sigma=0.06, seed=7
    )
    hmm = fit_gaussian_hmm(obs, 2)
    assert hmm.means[0] == pytest.approx(0.25, abs=0.03)
    assert hmm.means[1] == pytest.approx(0.72, abs=0.03)

    # ANA-F1: the fit iterated, the stop rule (not the cap) ended it, and it raised the
    # likelihood above the first pass.
    first_pass = fit_gaussian_hmm(obs, 2, n_iter=1)
    assert hmm.converged is True
    assert 1 < hmm.n_iter < DEFAULT_HMM_MAX_ITER
    assert hmm.log_likelihood > first_pass.log_likelihood

    # Every parameter, against the generating path's own statistics. With the states
    # 7.8 sigma apart the posterior is all but certain, so Baum-Welch's expected counts
    # equal the path's counts (measured within 1e-5).
    class_means, class_sds = _class_statistics(obs, true_paths)
    assert hmm.means == pytest.approx(class_means, abs=1e-4)
    assert hmm.sigmas == pytest.approx(class_sds, abs=1e-4)
    assert hmm.tmatrix == pytest.approx(_transition_frequencies(true_paths), abs=1e-4)
    starts_low = np.mean([p[0] == 0 for p in true_paths])
    assert hmm.start_prob == pytest.approx([starts_low, 1.0 - starts_low], abs=1e-4)
    # And the generating matrix (p01 = 0.05, p10 = 0.08), within the sampling noise of
    # about a thousand exits from each state.
    assert hmm.tmatrix == pytest.approx(np.array([[0.95, 0.05], [0.08, 0.92]]), abs=0.01)

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
    true_dwells = state_dwells(true_paths, no_state=-1)
    kin = two_state_rate_constants(obs, dt)
    # ANA-F2: with the states 7.8 sigma apart, Viterbi gives back every frame of the
    # generating path, so the pipeline's rates come from the same integer dwells as the
    # true-path rates. The agreement is exact, so the limit is floating-point only.
    paths = viterbi_paths(obs, kin.hmm)
    assert all(np.array_equal(p, t) for p, t in zip(paths, true_paths, strict=True))
    assert kin.k_low_high == pytest.approx(truth[0], rel=1e-9)
    assert kin.k_high_low == pytest.approx(truth[1], rel=1e-9)
    assert kin.n_dwells_low == true_dwells.lengths[0].size
    assert kin.n_dwells_high == true_dwells.lengths[1].size
    # The reported FRET levels are the fitted state means, low then high.
    assert kin.fret_low == kin.hmm.means[0]
    assert kin.fret_high == kin.hmm.means[1]
    class_means, _ = _class_statistics(obs, true_paths)
    assert [kin.fret_low, kin.fret_high] == pytest.approx(class_means, abs=1e-4)


def _synthetic_level(seed: int) -> tuple[KinsoftLevel, list[np.ndarray]]:
    """A level-1-shaped :class:`KinsoftLevel` built in the test, and its true state paths.

    Generated at the level-1 ground-truth rates (0.15 and 0.22 per second at 0.2 s per
    frame). The rows are zero-padded to different lengths, as in the packed fixture, so
    the evaluation reads each trace through :meth:`KinsoftLevel.trace`.
    """
    dt, n, frames = 0.2, 24, 1000
    obs, paths = _synthetic_two_state(
        n, frames, p01=0.15 * dt, p10=0.22 * dt, means=(0.25, 0.72), sigma=0.06, seed=seed
    )
    length = np.array([frames - 10 * i for i in range(n)], dtype=np.int64)
    fret_e = np.zeros((n, frames))
    for i, m in enumerate(length):
        fret_e[i, :m] = obs[i][:m]
    zeros = np.zeros_like(fret_e)
    level = KinsoftLevel(
        figure="Fig2",
        frame_time_s=dt,
        length=length,
        idd=zeros,
        ida=zeros,
        iaa=zeros,
        fret_e=fret_e,
    )
    return level, [p[:m] for p, m in zip(paths, length, strict=True)]


def _reference_with(edit: dict) -> KinsoftReference:
    """The frozen reference with ``level1`` fields replaced (``edit`` maps a key path)."""
    ref = load_kinsoft_reference(REFERENCE)
    levels = copy.deepcopy(ref.levels)
    for path, value in edit.items():
        node = levels["level1"]
        *parents, leaf = path.split(".")
        for key in parents:
            node = node[key]
        node[leaf] = value
    return KinsoftReference(
        band_rate_rel_deviation_max=ref.band_rate_rel_deviation_max, levels=levels, raw=ref.raw
    )


# --- frozen reference ---------------------------------------------------------


def test_load_kinsoft_reference() -> None:
    # DCN-F5: every rate of the file, pinned by literal, so a changed value fails here
    # and not only on the docs page that publishes it.
    ref = load_kinsoft_reference(REFERENCE)
    assert ref.band_rate_rel_deviation_max == pytest.approx(0.12)
    # Only the archetypal 2-state level has an active oracle.
    assert ref.active_levels() == ["level1"]
    lvl1 = ref.levels["level1"]
    assert lvl1["nstates"] == 2
    gt = lvl1["ground_truth"]["rates_s_inv"]
    assert gt == pytest.approx({"k12_low_high": 0.15, "k21_high_low": 0.22})
    l2 = ref.levels["level2"]["ground_truth"]["rates_s_inv"]
    assert l2 == pytest.approx({"k12_low_mid": 0.05, "k21_mid_low": 0.30})
    # The 4-state (Fig. 4) ground truth is recorded exactly (Suppl. Table 1).
    l3 = ref.levels["level3"]["ground_truth"]["rates_s_inv"]
    assert l3 == pytest.approx(
        {"k12": 0.053, "k14": 0.018, "k21": 0.080, "k23": 0.250, "k32": 0.680, "k41": 0.032}
    )
    # Tether's own frozen level-1 result. The large-tier test below ties the two rates to
    # the fit; each deviation must be the one its rate gives, to the file's 3 decimals.
    meas = lvl1["tether_measured"]
    measured = {k: meas[k] for k in ("k12_low_high", "k21_high_low", "k12_rel_dev", "k21_rel_dev")}
    assert measured == pytest.approx(
        {"k12_low_high": 0.1465, "k21_high_low": 0.2131, "k12_rel_dev": 0.023, "k21_rel_dev": 0.031}
    )
    for rate, dev in (("k12_low_high", "k12_rel_dev"), ("k21_high_low", "k21_rel_dev")):
        assert meas[dev] == round(abs(meas[rate] - gt[rate]) / gt[rate], 3)
    # Tether's own measured deviations sit comfortably inside the band.
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


def test_evaluate_rejects_an_active_level_that_is_not_two_state() -> None:
    level, _ = _synthetic_level(seed=7)
    with pytest.raises(ValueError, match="no active 2-state oracle"):
        evaluate_kinsoft_level(level, _reference_with({"nstates": 3}), "level1")


def test_evaluate_kinsoft_level_scores_a_synthetic_level() -> None:
    # ANA-U1: the oracle's success path on the required matrix, on a level built here
    # rather than the LFS fixture. Viterbi recovers the generating paths exactly, so the
    # rates are the true-path rates, about 1 % from the ground truth on this seed.
    level, true_paths = _synthetic_level(seed=7)
    ref = load_kinsoft_reference(REFERENCE)
    result = evaluate_kinsoft_level(level, ref, "level1")

    truth = pooled_exit_rates(true_paths, level.frame_time_s)
    assert result.rates == pytest.approx(
        {"k12_low_high": truth[0], "k21_high_low": truth[1]}, rel=1e-9
    )
    assert result.ground_truth == {"k12_low_high": 0.15, "k21_high_low": 0.22}
    for k, gt in result.ground_truth.items():
        assert result.rel_deviation[k] == pytest.approx(abs(result.rates[k] - gt) / gt, rel=1e-12)
    assert result.level == "level1"
    assert result.band == ref.band_rate_rel_deviation_max
    assert result.within_band is True
    assert result.failures == []


def test_evaluate_kinsoft_level_names_the_rate_outside_the_band() -> None:
    # The same level against a ground truth whose k12 is twice the generating rate: k12
    # leaves the band and k21 stays in it, so the result must name k12 and only k12.
    level, _ = _synthetic_level(seed=7)
    ref = _reference_with({"ground_truth.rates_s_inv.k12_low_high": 0.30})
    result = evaluate_kinsoft_level(level, ref, "level1")
    rel = result.rel_deviation["k12_low_high"]
    assert rel > ref.band_rate_rel_deviation_max
    assert result.rel_deviation["k21_high_low"] <= ref.band_rate_rel_deviation_max
    assert result.within_band is False
    assert result.failures == [f"k12_low_high: {rel:.3f} > 0.120"]


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

    # ANA-U1: the frozen tether_measured values are these rates rounded to the four
    # decimals the file keeps, so a rate that moves by more than half a unit there fails.
    meas = ref.levels["level1"]["tether_measured"]
    for rate in ("k12_low_high", "k21_high_low"):
        assert round(result.rates[rate], 4) == meas[rate], rate
    # The rates themselves are pinned at a relative 1e-6. A rate is pooled dwells over
    # pooled dwell time of the integer Viterbi paths: 939 dwells in 32 046 frames and 954
    # in 22 383 frames, at 0.2 s. Moving one frame between the states changes a rate by
    # at least 3e-5 relative, so 1e-6 admits floating-point noise and no change of path.
    # Measured 2026-11-11 (#740) on Linux x86-64 with the base lock, the platform of the
    # large-fixtures job; the default tol, 1e-8 and 0 give the same paths.
    assert result.rates["k12_low_high"] == pytest.approx(939 / (32046 * 0.2), rel=1e-6)
    assert result.rates["k21_high_low"] == pytest.approx(954 / (22383 * 0.2), rel=1e-6)
    # The dwells the two rates rest on, pooled across the 75 traces.
    assert result.kinetics.n_dwells_low == 939
    assert result.kinetics.n_dwells_high == 954
