# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Dwell-time distributions + survival fits (M6 B2, FR-ANALYZE; PRD §7.7, Appendix C B2).

Covers tMAVEN's dwell pipeline (``modeler/dwells.py`` ``generate_dwells`` + ``survival``
+ ``optimize_*_surv``): per-state dwell extraction with first/last censoring, the
empirical survival function, and its exponential fit. A verbatim port of the tMAVEN
reference is the parity oracle. The store path additionally enforces the two Tether
invariants tMAVEN has no analogue for — **fresh idealizations only** (STALE molecules
excluded, PRD §5.1) and the §7.5 curation filter. All headless (no Qt) → runs in the
base CI matrix; the store is seeded as post-idealization data under the M0-frozen schema.
"""

from __future__ import annotations

import pytest

pytest.importorskip("numpy")
pytest.importorskip("h5py")
pytest.importorskip("scipy")

import numpy as np  # noqa: E402

from _analysis_store import MEANS, build_store_with_model  # noqa: E402
from tether.analysis import (  # noqa: E402
    DEFAULT_DWELL_CI_LEVEL,
    DEFAULT_DWELL_DT,
    DEFAULT_DWELL_NBINS,
    DwellTimeAnalysis,
    StateDwells,
    fit_survival,
    population_dwell_times,
    state_dwells,
    survival_curve,
)
from tether.idealize import NO_STATE  # noqa: E402

# --- tMAVEN reference oracle (dwells.py, ported verbatim) ---------------------


def _tmaven_dwells(
    state_chunks: list[np.ndarray], *, no_state: int, first_flag: bool
) -> dict[int, list[int]]:
    """tMAVEN ``generate_dwells`` verbatim, on integer state paths (no_state == NaN).

    Splits each molecule's state path into constant runs, drops the last (right-censored)
    and — unless ``first_flag`` — the first (left-censored), recording ``len(run)`` under
    the run's state. Returns ``{state: sorted lengths}``.
    """
    dwell_list: dict[int, list[int]] = {}
    for chunk in state_chunks:
        trace = np.asarray(chunk)
        trace = trace[trace != no_state]  # tMAVEN: trace[~np.isnan(trace)]
        if len(trace) > 0:
            dwell_split = np.split(trace, np.argwhere(np.diff(trace) != 0).flatten() + 1)
            if len(dwell_split) > 1:
                start = 0 if first_flag else 1
                dwell_split = dwell_split[start:-1]
                for d in dwell_split:
                    ind = int(d[0])
                    dwell_list.setdefault(ind, []).append(len(d))
    return {k: sorted(v) for k, v in dwell_list.items()}


def _tmaven_survival(dist: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """tMAVEN ``survival`` verbatim (loop form)."""
    dist = np.asarray(dist)
    if dist.size == 0:
        return np.array([0]), np.array([0.0])
    n = np.int32(np.max(dist))
    raw_surv = np.zeros(n)
    for i in np.arange(n):
        temp = np.zeros_like(dist)
        temp[np.where(dist > i)] = 1
        raw_surv[i] = np.sum(temp)
    if raw_surv[0] == 0:  # noqa: SIM108 - kept verbatim to the tMAVEN reference
        norm_surv = np.zeros_like(raw_surv)
    else:
        norm_surv = raw_surv / raw_surv[0]
    return np.arange(n), norm_surv


# --- pure core: extraction + censoring ---------------------------------------


def test_defaults() -> None:
    assert DEFAULT_DWELL_DT == 1.0
    assert DEFAULT_DWELL_NBINS == 51
    assert DEFAULT_DWELL_CI_LEVEL == 0.95


def test_state_dwells_drops_first_and_last_by_default() -> None:
    # runs: [0x3][1x2][2x3][0x2] -> interior (drop first 0x3 and last 0x2): 1x2, 2x3
    path = np.array([0, 0, 0, 1, 1, 2, 2, 2, 0, 0])
    sd = state_dwells([path], no_state=NO_STATE)
    assert isinstance(sd, StateDwells)
    assert sd.lengths[1].tolist() == [2]
    assert sd.lengths[2].tolist() == [3]
    assert 0 not in sd.lengths  # both state-0 runs are the censored ends
    assert sd.n_molecules == {1: 1, 2: 1}
    assert sd.include_first is False


def test_state_dwells_include_first_keeps_leading_run() -> None:
    path = np.array([0, 0, 0, 1, 1, 2, 2, 2, 0, 0])
    sd = state_dwells([path], no_state=NO_STATE, include_first=True)
    # now keep the leading 0x3, still drop the trailing 0x2
    assert sd.lengths[0].tolist() == [3]
    assert sd.lengths[1].tolist() == [2]
    assert sd.lengths[2].tolist() == [3]


def test_state_dwells_single_run_contributes_nothing() -> None:
    # no transition -> the sole dwell is censored at both ends
    sd = state_dwells([np.array([1, 1, 1, 1])], no_state=NO_STATE)
    assert sd.lengths == {}
    assert sd.n_molecules == {}


def test_state_dwells_strips_no_state_like_tmaven() -> None:
    # NO_STATE frames removed before splitting (tMAVEN NaN strip) — a gap between two
    # equal states merges them into one run, exactly as tMAVEN does.
    path = np.array([NO_STATE, 0, 0, 1, 1, NO_STATE, 1, 2, 2, NO_STATE])
    sd = state_dwells([path], no_state=NO_STATE)
    ref = _tmaven_dwells([path], no_state=NO_STATE, first_flag=False)
    got = {st: sorted(v.tolist()) for st, v in sd.lengths.items()}
    assert got == ref


def test_state_dwells_scalar_misuse_raises() -> None:
    # passing a flat 1-D path (instead of [path]) iterates to scalars -> fail fast
    with pytest.raises(ValueError, match="scalar element"):
        state_dwells(np.array([0, 0, 1, 1, 0]))
    with pytest.raises(ValueError, match="scalar element"):
        state_dwells([0, 0, 1, 1, 0])


def _random_state_paths(rng: np.random.Generator, n_mol: int, n_frames: int, nstates: int):
    return [rng.integers(0, nstates, size=n_frames) for _ in range(n_mol)]


@pytest.mark.parametrize("first_flag", [False, True])
def test_state_dwells_matches_tmaven_oracle(first_flag: bool) -> None:
    rng = np.random.default_rng(20260708)
    paths = _random_state_paths(rng, n_mol=12, n_frames=40, nstates=3)
    sd = state_dwells(paths, no_state=NO_STATE, include_first=first_flag)
    ref = _tmaven_dwells(paths, no_state=NO_STATE, first_flag=first_flag)
    got = {st: sorted(v.tolist()) for st, v in sd.lengths.items()}
    assert got == ref


# --- pure core: survival ------------------------------------------------------


def test_survival_empty_is_tmaven_degenerate() -> None:
    tau, surv = survival_curve(np.empty(0, dtype=int))
    np.testing.assert_array_equal(tau, np.array([0]))
    np.testing.assert_array_equal(surv, np.array([0.0]))


def test_survival_known_distribution() -> None:
    # dwells [1,2,2,3]: n=3; #>0=4, #>1=3, #>2=1 -> raw [4,3,1] -> norm [1, .75, .25]
    d = np.array([1, 2, 2, 3])
    tau, surv = survival_curve(d)
    np.testing.assert_array_equal(tau, np.array([0, 1, 2]))
    np.testing.assert_allclose(surv, [1.0, 0.75, 0.25])
    assert surv[0] == 1.0  # normalized so S(0) == 1


def test_survival_matches_tmaven_oracle() -> None:
    rng = np.random.default_rng(7)
    d = rng.integers(1, 30, size=200)
    tau, surv = survival_curve(d)
    rtau, rsurv = _tmaven_survival(d)
    np.testing.assert_array_equal(tau, rtau)
    np.testing.assert_allclose(surv, rsurv)


def test_survival_rejects_non_positive_dwells() -> None:
    # dwell lengths are frame counts >= 1; a public caller passing 0 or a negative
    # value must get a clear error, not an IndexError (all-zero) or a cryptic
    # np.bincount ValueError (negative mixed with positive).
    with pytest.raises(ValueError, match="positive frame counts"):
        survival_curve(np.array([0, 0, 0]))
    with pytest.raises(ValueError, match="positive frame counts"):
        survival_curve(np.array([3, -1, 2]))
    # the empty input still returns the tMAVEN degenerate (not an error)
    tau, surv = survival_curve(np.empty(0, dtype=int))
    np.testing.assert_array_equal(tau, np.array([0]))


# --- pure core: exponential fit ----------------------------------------------


def test_fit_recovers_known_rate() -> None:
    # ceil of an exponential preserves the survival at integer tau exactly:
    # ceil(T) > i  <=>  T > i for integer i, so S(i) = exp(-k i).
    rng = np.random.default_rng(2026)
    k_true = 0.1
    d = np.ceil(rng.exponential(1.0 / k_true, size=8000)).astype(int)
    tau, surv = survival_curve(d)
    fit = fit_survival(tau, surv, model="single")
    assert fit.success
    assert fit.model == "single"
    assert fit.rates.shape == (1,)
    np.testing.assert_allclose(fit.rates[0], k_true, rtol=0.1)
    np.testing.assert_allclose(fit.amplitudes[0], 1.0, atol=0.05)


def test_fit_ci_and_residuals() -> None:
    from scipy import stats

    rng = np.random.default_rng(11)
    d = np.ceil(rng.exponential(1.0 / 0.2, size=6000)).astype(int)
    tau, surv = survival_curve(d)
    fit = fit_survival(tau, surv, model="single", ci_level=0.95)
    # standard errors + CI half-widths are finite and positive
    assert np.all(np.isfinite(fit.rate_stderr)) and np.all(fit.rate_stderr > 0)
    assert np.all(np.isfinite(fit.amplitude_stderr)) and np.all(fit.amplitude_stderr > 0)
    assert np.all(np.isfinite(fit.rate_ci)) and np.all(fit.rate_ci > 0)
    # CI half-width == Student-t multiplier * SE (t > 1, so the interval exceeds the SE)
    dof = fit.n_points - 2
    t = float(stats.t.ppf(0.975, dof))
    np.testing.assert_allclose(fit.rate_ci, fit.rate_stderr * t, rtol=1e-9)
    np.testing.assert_allclose(fit.amplitude_ci, fit.amplitude_stderr * t, rtol=1e-9)
    assert np.all(fit.rate_ci > fit.rate_stderr)
    # residuals are exactly observed - model at each tau (the residual subplot data)
    np.testing.assert_allclose(fit.residuals, fit.survival - fit.model_survival)
    assert fit.residuals.shape == fit.tau.shape
    assert np.isfinite(fit.r_squared) and fit.r_squared > 0.99


def test_fit_dt_scales_rate_inversely() -> None:
    rng = np.random.default_rng(5)
    d = np.ceil(rng.exponential(1.0 / 0.15, size=5000)).astype(int)
    tau_frames, surv = survival_curve(d)
    per_frame = fit_survival(tau_frames.astype(float), surv, model="single")
    per_two = fit_survival(tau_frames.astype(float) * 2.0, surv, model="single")
    # doubling the time unit halves the rate constant
    np.testing.assert_allclose(per_two.rates[0], per_frame.rates[0] / 2.0, rtol=1e-6)


def test_fit_too_few_points_fails_gracefully() -> None:
    # a single dwell -> survival has one flat point; cannot fit 2 params -> not a crash
    tau, surv = survival_curve(np.array([1]))
    fit = fit_survival(tau, surv, model="single")
    assert fit.success is False
    assert np.all(np.isnan(fit.rates))
    assert np.all(np.isnan(fit.rate_ci))
    assert fit.residuals.shape == fit.tau.shape


def test_fit_non_finite_survival_fails_gracefully() -> None:
    # a NaN in the survival curve is caught up front (never handed to curve_fit)
    tau = np.arange(10.0)
    surv = np.exp(-0.2 * tau)
    surv[5] = np.nan
    fit = fit_survival(tau, surv, model="single")
    assert fit.success is False
    assert np.all(np.isnan(fit.rates))
    assert fit.residuals.shape == fit.tau.shape


def test_fit_curve_fit_exception_fails_gracefully() -> None:
    # a non-finite tau (finite survival, enough points) makes scipy curve_fit raise
    # ValueError; fit_survival must catch it and return a clean failure, not propagate.
    tau = np.array([0.0, 1.0, np.inf, 3.0, 4.0, 5.0])
    surv = np.array([1.0, 0.6, 0.4, 0.3, 0.2, 0.1])
    fit = fit_survival(tau, surv, model="single")
    assert fit.success is False
    assert np.all(np.isnan(fit.rates))


def test_fit_inf_covariance_fails_gracefully(monkeypatch) -> None:
    # a rank-deficient Jacobian yields an inf/NaN pcov diagonal; the fit must be
    # reported as failed (NaN params) rather than success=True with inf standard errors.
    monkeypatch.setattr(
        "scipy.optimize.curve_fit",
        lambda *a, **k: (np.array([0.3, 1.0]), np.array([[np.inf, 0.0], [0.0, 1e-4]])),
    )
    fit = fit_survival(np.arange(10.0), np.exp(-0.3 * np.arange(10.0)), model="single")
    assert fit.success is False
    assert np.all(np.isnan(fit.rates))
    assert np.all(np.isnan(fit.rate_stderr))


def test_fit_double_sorted_by_rate() -> None:
    # a genuine bi-exponential survival: two well-separated rates, equal weight
    tau = np.arange(60, dtype=float)
    surv = 0.5 * np.exp(-0.05 * tau) + 0.5 * np.exp(-0.8 * tau)
    fit = fit_survival(tau, surv, model="double")
    assert fit.success
    assert fit.rates.shape == (2,) and fit.amplitudes.shape == (2,)
    assert fit.rates[0] < fit.rates[1]  # ascending
    np.testing.assert_allclose(np.sort(fit.rates), [0.05, 0.8], rtol=0.15)


def test_fit_double_cosorts_stderr_and_amplitudes_with_rates(monkeypatch) -> None:
    # curve_fit returns DESCENDING rates (a genuine reorder) with DISTINCT per-branch
    # SEs; fit_survival must co-sort rates, amplitudes, AND their standard errors + CIs
    # together into ascending-rate order. A regression that dropped the co-sort would
    # report each error against the wrong rate — invisible to the equal-amplitude,
    # identity-sort test above, so lock it here with a deterministic fit.
    from scipy import stats

    popt = np.array([0.8, 0.05, 0.3, 0.7])  # k1 > k2 -> argsort = [1, 0]
    pcov = np.diag([0.08**2, 0.005**2, 0.03**2, 0.07**2])  # distinct, finite SEs
    monkeypatch.setattr("scipy.optimize.curve_fit", lambda *a, **k: (popt, pcov))
    fit = fit_survival(np.arange(20.0), np.exp(-0.1 * np.arange(20.0)), model="double")
    assert fit.success
    np.testing.assert_allclose(fit.rates, [0.05, 0.8])  # ascending
    np.testing.assert_allclose(fit.amplitudes, [0.7, 0.3])  # amp of the slow rate first
    np.testing.assert_allclose(fit.rate_stderr, [0.005, 0.08])  # SE co-sorted with rates
    np.testing.assert_allclose(fit.amplitude_stderr, [0.07, 0.03])
    t = float(stats.t.ppf(0.975, fit.n_points - 4))
    np.testing.assert_allclose(fit.rate_ci, np.array([0.005, 0.08]) * t)
    np.testing.assert_allclose(fit.amplitude_ci, np.array([0.07, 0.03]) * t)


def test_fit_triple_cosorts_stderr_and_amplitudes_with_rates(monkeypatch) -> None:
    # the 3-component branch has the largest permutation space for the co-sort; lock it
    # the same way as the double case with a deterministic out-of-order fit.
    from scipy import stats

    popt = np.array([0.9, 0.05, 0.4, 0.2, 0.5, 0.3])  # 3 rates then 3 amps, out of order
    ses = np.array([0.09, 0.004, 0.02, 0.04, 0.05, 0.03])
    pcov = np.diag(ses**2)
    monkeypatch.setattr("scipy.optimize.curve_fit", lambda *a, **k: (popt, pcov))
    fit = fit_survival(np.arange(30.0), np.exp(-0.1 * np.arange(30.0)), model="triple")
    assert fit.success
    order = np.argsort(popt[:3])  # ascending-rate permutation
    np.testing.assert_allclose(fit.rates, popt[:3][order])
    assert np.all(np.diff(fit.rates) > 0)  # strictly ascending
    np.testing.assert_allclose(fit.amplitudes, popt[3:][order])  # amps co-sorted
    np.testing.assert_allclose(fit.rate_stderr, ses[:3][order])  # SEs co-sorted with rates
    np.testing.assert_allclose(fit.amplitude_stderr, ses[3:][order])
    t = float(stats.t.ppf(0.975, fit.n_points - 6))
    np.testing.assert_allclose(fit.rate_ci, ses[:3][order] * t)
    np.testing.assert_allclose(fit.amplitude_ci, ses[3:][order] * t)


def test_fit_stretched_reports_beta() -> None:
    tau = np.arange(50, dtype=float)
    surv = np.exp(-((0.1 * tau) ** 0.7))
    fit = fit_survival(tau, surv, model="stretched")
    assert fit.success
    assert fit.beta is not None
    np.testing.assert_allclose(fit.beta, 0.7, rtol=0.15)
    np.testing.assert_allclose(fit.rates[0], 0.1, rtol=0.15)
    assert "β" in fit.annotation


def test_fit_annotation_present() -> None:
    tau, surv = survival_curve(np.ceil(np.random.default_rng(1).exponential(8, 3000)).astype(int))
    fit = fit_survival(tau, surv, model="single")
    assert "k =" in fit.annotation and "A =" in fit.annotation


def test_fit_unknown_model_raises() -> None:
    with pytest.raises(ValueError, match="model must be one of"):
        fit_survival(np.arange(5.0), np.ones(5), model="quadruple")


# --- store-level fixtures (mirror test_analysis_tdp) --------------------------


def _known_states() -> np.ndarray:
    # mol0 runs [0x3][1x2][2x3][0x2] -> interior 1x2, 2x3
    # mol1 runs [1x2][2x4][0x3][1x1] -> interior 2x4, 0x3
    return np.array(
        [
            [0, 0, 0, 1, 1, 2, 2, 2, 0, 0],
            [1, 1, 2, 2, 2, 2, 0, 0, 0, 1],
        ],
        dtype="int64",
    )


def test_population_matches_pure_core(tmp_path) -> None:
    s = _known_states()
    proj, _keys = build_store_with_model(tmp_path, s, MEANS)
    result = population_dwell_times(proj, "vbconhmm")
    assert set(result) == {0, 1, 2}
    # compare to the pure core fed the same reconstructed state windows
    ref = state_dwells([s[0], s[1]], no_state=NO_STATE)
    for st, analysis in result.items():
        assert isinstance(analysis, DwellTimeAnalysis)
        assert sorted(analysis.dwell_lengths.tolist()) == sorted(ref.lengths[st].tolist())
        assert analysis.n_molecules == ref.n_molecules[st]


def test_population_known_dwells(tmp_path) -> None:
    s = _known_states()
    proj, _keys = build_store_with_model(tmp_path, s, MEANS)
    a2 = population_dwell_times(proj, "vbconhmm", state=2)
    assert isinstance(a2, DwellTimeAnalysis)
    assert sorted(a2.dwell_lengths.tolist()) == [3, 4]  # mol0 2x3, mol1 2x4
    assert a2.n_dwells == 2
    assert a2.n_molecules == 2
    assert a2.level == pytest.approx(0.85)  # means[2]
    a1 = population_dwell_times(proj, "vbconhmm", state=1)
    assert a1.dwell_lengths.tolist() == [2]
    a0 = population_dwell_times(proj, "vbconhmm", state=0)
    assert a0.dwell_lengths.tolist() == [3]


def test_population_dt_applied_to_tau(tmp_path) -> None:
    s = _known_states()
    proj, _keys = build_store_with_model(tmp_path, s, MEANS)
    a2 = population_dwell_times(proj, "vbconhmm", state=2, dt=0.1)
    # tau is arange(max_dwell) * dt (frames -> seconds)
    assert a2.dt == 0.1
    np.testing.assert_allclose(a2.tau, np.arange(a2.tau.size) * 0.1)


def test_population_stale_excluded_by_default(tmp_path) -> None:
    s = _known_states()
    proj, _keys = build_store_with_model(tmp_path, s, MEANS, stale=[False, True])
    # only mol0 contributes: interior 1x2, 2x3 ; state0 (mol1-only) now absent
    a2 = population_dwell_times(proj, "vbconhmm", state=2)
    assert a2.dwell_lengths.tolist() == [3]
    assert a2.n_molecules == 1
    a0 = population_dwell_times(proj, "vbconhmm", state=0)
    assert a0.n_dwells == 0
    assert a0.fit is None
    # include_stale restores mol1
    a2_all = population_dwell_times(proj, "vbconhmm", state=2, include_stale=True)
    assert sorted(a2_all.dwell_lengths.tolist()) == [3, 4]


def test_population_rejected_excluded_by_default(tmp_path) -> None:
    s = _known_states()
    proj, _keys = build_store_with_model(tmp_path, s, MEANS, rejected=[False, True])
    a2 = population_dwell_times(proj, "vbconhmm", state=2)
    assert a2.dwell_lengths.tolist() == [3]  # only mol0
    a2_inc = population_dwell_times(proj, "vbconhmm", state=2, include_rejected=True)
    assert sorted(a2_inc.dwell_lengths.tolist()) == [3, 4]


def test_population_missing_state_is_empty_never_raises(tmp_path) -> None:
    s = _known_states()
    proj, _keys = build_store_with_model(tmp_path, s, MEANS)
    a = population_dwell_times(proj, "vbconhmm", state=99)
    assert a.n_dwells == 0
    assert a.dwell_lengths.size == 0
    assert a.fit is None
    assert np.isnan(a.level)  # out of means range
    # histogram of an empty analysis is empty, not a crash
    centres, density = a.histogram()
    assert centres.size == 0 and density.size == 0


def test_population_missing_model_raises(tmp_path) -> None:
    s = _known_states()
    proj, _keys = build_store_with_model(tmp_path, s, MEANS)
    with pytest.raises(KeyError):
        population_dwell_times(proj, "does-not-exist")


def test_analysis_histogram_density_and_dt_scaled_centres(tmp_path) -> None:
    # build many dwells in state 1 by chaining transitions, then check the density hist
    rng = np.random.default_rng(3)
    frames = []
    state = 0
    for _ in range(400):
        run = int(rng.integers(1, 6))
        frames.extend([state] * run)
        state = 1 - state
    s = np.array([frames], dtype="int64")
    means = np.array([0.3, 0.7])
    proj, _keys = build_store_with_model(tmp_path, s, means, name="hist.tether")
    a = population_dwell_times(proj, "vbconhmm", state=1)  # dt = 1 (frames)
    centres, density = a.histogram(nbins=10)
    assert centres.size == 10
    width = centres[1] - centres[0]
    # density integrates to one (guards a density=False regression)...
    np.testing.assert_allclose(float(density.sum() * width), 1.0, rtol=1e-9)
    # ...but that is a normalization identity; the load-bearing check is that the bin
    # centres are the dt-scaled dwell TIMES (locks the `dwell_lengths * self.dt` step).
    times = a.dwell_lengths.astype(float)  # dt = 1 here
    edges = np.linspace(times.min(), times.max(), 11)
    expected = 0.5 * (edges[:-1] + edges[1:])
    np.testing.assert_allclose(centres, expected)

    # a 10x-smaller dt scales every bin centre by 10x (a dt bug on the histogram would
    # cancel out of the density check but shifts the centres).
    a_dt = population_dwell_times(proj, "vbconhmm", state=1, dt=0.1)
    centres_dt, _ = a_dt.histogram(nbins=10)
    np.testing.assert_allclose(centres_dt, centres * 0.1)


def test_population_state_none_returns_all_states_fitted(tmp_path) -> None:
    # a store with plenty of dwells so the single-exp fit converges per state
    rng = np.random.default_rng(99)
    frames = []
    state = 0
    for _ in range(600):
        run = int(np.ceil(rng.exponential(4.0)))
        frames.extend([state] * run)
        state = (state + 1) % 3
    s = np.array([frames], dtype="int64")
    proj, _keys = build_store_with_model(tmp_path, s, MEANS, name="fit.tether")
    result = population_dwell_times(proj, "vbconhmm", model="single")
    assert set(result) == {0, 1, 2}
    for analysis in result.values():
        assert analysis.fit is not None
        assert analysis.fit.success
        assert analysis.fit.rates[0] > 0
        assert "k =" in analysis.fit.annotation


@pytest.mark.parametrize("model", ["double", "triple", "stretched"])
def test_population_plumbs_fit_model_kwarg(tmp_path, model: str) -> None:
    # the `model` kwarg must reach fit_survival end-to-end, not only for the default
    # "single" — a store-level check that population_dwell_times fits with the request.
    rng = np.random.default_rng(2024)
    frames = []
    state = 0
    for _ in range(800):
        run = int(np.ceil(rng.exponential(4.0)))
        frames.extend([state] * run)
        state = (state + 1) % 3
    s = np.array([frames], dtype="int64")
    proj, _keys = build_store_with_model(tmp_path, s, MEANS, name=f"fit-{model}.tether")
    result = population_dwell_times(proj, "vbconhmm", model=model)
    assert set(result) == {0, 1, 2}
    for analysis in result.values():
        assert analysis.fit is not None
        assert analysis.fit.model == model  # the kwarg was plumbed through end-to-end
    # double + stretched converge on these (near-single-exponential) dwells; a 6-param
    # triple over-parameterizes them and grace-fails (success=False) — the honest
    # outcome, covered by the graceful-failure unit tests. So assert convergence only
    # where it is well-posed, but assert the plumbing (model==model) for all three.
    if model in ("double", "stretched"):
        assert all(a.fit.success for a in result.values())
        if model == "stretched":
            assert all(a.fit.beta is not None for a in result.values())
