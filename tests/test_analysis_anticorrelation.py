# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Anticorrelation-event finder (M6 PR-6, FR-ANALYZE; PRD §7.7).

The model-free lens that *localizes* donor–acceptor anticorrelation events in time:
a sliding window sweeps each trace, and windows that are both anti-phase (signed
lag-0 Pearson < 0, the reliable same-frame direction) and temporally structured
(lag-1 magnitude >= a threshold, which rejects white shot-noise anticorrelation)
merge into events. All headless (no Qt) → base CI matrix; the finder uses
``scipy.signal`` only via the reused :func:`tether.analysis.cross_correlation` core.
"""

from __future__ import annotations

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("h5py")

import numpy as np  # noqa: E402

from _analysis_store import build_store_with_channels  # noqa: E402
from tether.analysis import (  # noqa: E402
    DEFAULT_ANTICORR_MIN_MAGNITUDE,
    DEFAULT_ANTICORR_STEP,
    DEFAULT_ANTICORR_WINDOW,
    AnticorrelationEvent,
    AnticorrelationScan,
    MoleculeAnticorrelation,
    PopulationAnticorrelation,
    cross_correlation,
    find_anticorrelation_events,
    population_anticorrelation_events,
)
from tether.analysis._store import windowed_channels, windowed_channels_with_keys  # noqa: E402


def _antiphase(n: int, *, amp: float = 120.0, base: float = 500.0, period: float = 12.0) -> tuple:
    """A perfectly anti-phase, temporally-smooth donor/acceptor pair (acceptor = 2·base − donor)."""
    t = np.arange(n, dtype="float64")
    donor = base + amp * np.sin(2.0 * np.pi * t / period)
    acceptor = 2.0 * base - donor  # exact anti-phase (lag0 = -1)
    return donor, acceptor


def _correlated(n: int, *, base: float = 500.0, slope: float = 3.0) -> tuple:
    """A perfectly correlated donor/acceptor ramp (in-phase, lag0 = +1)."""
    t = np.arange(n, dtype="float64")
    donor = base + slope * t
    acceptor = 0.5 * donor + 100.0  # positively correlated, never anti-phase
    return donor, acceptor


def _localized_event(n: int, lo: int, hi: int, *, amp: float = 120.0) -> tuple:
    """Correlated ramp flanks with an anti-phase burst injected over ``[lo, hi)``."""
    donor, acceptor = _correlated(n)
    t = np.arange(lo, hi, dtype="float64")
    osc = amp * np.sin(2.0 * np.pi * (t - lo) / 12.0)
    donor[lo:hi] = 500.0 + osc
    acceptor[lo:hi] = 500.0 - osc
    return donor, acceptor


def _flagged_mask(scan: AnticorrelationScan, min_magnitude: float) -> np.ndarray:
    """Recompute the finder's flag predicate independently (anti-phase AND structured)."""
    return (scan.lag0 < 0.0) & (scan.lag1_magnitude >= min_magnitude)


# --- pure-array core ---------------------------------------------------------


def test_defaults_are_sane() -> None:
    assert DEFAULT_ANTICORR_WINDOW >= 2
    assert DEFAULT_ANTICORR_STEP >= 1
    assert 0.0 <= DEFAULT_ANTICORR_MIN_MAGNITUDE <= 1.0


def test_full_antiphase_trace_is_one_event() -> None:
    donor, acceptor = _antiphase(80)
    scan = find_anticorrelation_events(donor, acceptor, window=15, step=1, min_magnitude=0.5)
    assert isinstance(scan, AnticorrelationScan)
    assert scan.n_frames == 80
    assert scan.n_windows == 80 - 15 + 1
    # every window is perfectly anti-phase and temporally structured -> all flagged -> one
    # event spanning the whole scannable extent.
    assert scan.n_events == 1
    (event,) = scan.events
    assert event.start == 0
    assert event.stop == 80  # last window starts at 65, +15 -> 80
    assert event.peak_lag0 == pytest.approx(-1.0)  # exact anti-phase
    assert event.peak_magnitude == pytest.approx(abs(event.peak_lag0))
    assert np.all(scan.lag0 < 0.0)  # anti-phase everywhere
    assert np.all(scan.lag1_magnitude >= 0.5)  # temporally structured everywhere


def test_correlated_trace_finds_no_events() -> None:
    # a positively correlated (in-phase) pair must never be flagged as anticorrelation.
    donor, acceptor = _correlated(80)
    scan = find_anticorrelation_events(donor, acceptor, window=15, min_magnitude=0.5)
    assert scan.n_events == 0
    assert np.all(scan.lag0 > 0.0)  # in-phase throughout


def test_inphase_fast_oscillation_is_not_flagged() -> None:
    # REGRESSION (the reason detection is driven by the lag-0 SIGN, not the signed lag-1):
    # a fast IN-PHASE oscillation has same-frame Pearson +1, but the biased-normalized
    # signed r[+1] is strongly NEGATIVE (period < 4 frames). A signed-lag-1 driver would
    # misflag it as an anti-phase FRET event; the lag-0 sign gate correctly rejects it.
    t = np.arange(60, dtype="float64")
    donor = 500.0 + 100.0 * np.sin(2.0 * np.pi * t / 2.5)
    acceptor = 300.0 + 50.0 * np.sin(2.0 * np.pi * t / 2.5)  # in-phase, positively correlated
    scan = find_anticorrelation_events(donor, acceptor, window=15, step=1, min_magnitude=0.5)
    assert scan.n_events == 0
    assert np.all(scan.lag0 > 0.0)  # the reliable same-frame sign says in-phase
    # document the trap the lag-0 gate defuses: the SIGNED lag-1 here is < -0.5.
    cc = cross_correlation(donor[:15], acceptor[:15], max_lag=1)
    signed_lag1 = float(cc.values[cc.lags == 1][0])
    assert signed_lag1 < -0.5


def test_antiphase_shot_noise_is_rejected_by_lag1_magnitude() -> None:
    # perfectly anti-phase but WHITE (no temporal structure): lag-0 is strongly negative
    # everywhere, yet the lag-1 magnitude never reaches the threshold, so no event is
    # flagged -- same-frame shot-noise anticorrelation is not a conformational event.
    rng = np.random.default_rng(1)
    donor = 500.0 + rng.normal(0.0, 50.0, 200)
    acceptor = 1000.0 - donor  # exact anti-phase (lag0 = -1), but white
    scan = find_anticorrelation_events(donor, acceptor, window=25, step=1, min_magnitude=0.7)
    assert np.all(scan.lag0 < 0.0)  # anti-phase in every window
    assert scan.n_events == 0  # rejected: no temporal structure (lag-1 magnitude too low)


def test_localized_event_is_found_and_localized() -> None:
    donor, acceptor = _localized_event(120, 45, 75)
    scan = find_anticorrelation_events(donor, acceptor, window=15, step=1, min_magnitude=0.5)
    assert scan.n_events == 1
    (event,) = scan.events
    # the peak sits inside the injected anti-phase region, and the event overlaps it.
    assert 45 <= event.peak_frame < 75
    assert event.start < 75 and event.stop > 45
    assert event.peak_lag0 < -0.5
    # the correlated flanks are not part of the event: an early window is in-phase.
    assert np.any(scan.lag0[:20] > 0.0)


def test_two_separated_events_stay_separate() -> None:
    # two anti-phase bursts on a flat background, separated by a gap wider than the
    # window: the flat gap reads NaN (never flagged), so the two events cannot merge.
    n = 160
    donor = np.full(n, 500.0)
    acceptor = np.full(n, 500.0)
    for lo, hi in ((20, 45), (110, 135)):
        t = np.arange(lo, hi, dtype="float64")
        osc = 120.0 * np.sin(2.0 * np.pi * (t - lo) / 12.0)
        donor[lo:hi] = 500.0 + osc
        acceptor[lo:hi] = 500.0 - osc
    scan = find_anticorrelation_events(donor, acceptor, window=15, step=1, min_magnitude=0.5)
    assert scan.n_events == 2
    first, second = scan.events
    assert first.stop < second.start  # a genuine gap, not merged
    # each event's span covers its own burst; assert on the span, not the peak_frame, since
    # a perfect anti-phase burst gives many windows lag0 == -1 and the tie-broken peak is
    # FP-build-dependent (its exact window differs across numpy/BLAS).
    assert first.start < 45 and first.stop > 20  # first event covers burst [20, 45)
    assert second.start < 135 and second.stop > 110  # second event covers burst [110, 135)
    assert first.peak_frame < second.peak_frame  # ordered, well-separated events


def test_scan_centers_and_shape() -> None:
    donor, acceptor = _antiphase(50)
    scan = find_anticorrelation_events(donor, acceptor, window=11, step=1)
    n_windows = 50 - 11 + 1
    assert scan.n_windows == n_windows
    assert scan.centers.shape == (n_windows,)
    assert scan.lag0.shape == (n_windows,)
    assert scan.lag1_magnitude.shape == (n_windows,)
    np.testing.assert_array_equal(scan.centers, np.arange(n_windows) + 11 // 2)
    assert scan.centers.dtype == np.int64


def test_step_subsamples_windows() -> None:
    donor, acceptor = _antiphase(60)
    scan = find_anticorrelation_events(donor, acceptor, window=10, step=5)
    starts = np.arange(0, 60 - 10 + 1, 5)
    assert scan.n_windows == starts.size
    np.testing.assert_array_equal(scan.centers, starts + 10 // 2)


def test_constant_window_is_nan_not_flagged() -> None:
    # a flat (bleached) trace: every window is constant -> undefined -> NaN, never flagged.
    donor = np.full(60, 700.0)
    acceptor = np.full(60, 300.0)
    scan = find_anticorrelation_events(donor, acceptor, window=15)
    assert scan.n_events == 0
    assert np.all(np.isnan(scan.lag0))
    assert np.all(np.isnan(scan.lag1_magnitude))


def test_partial_flat_region_yields_nan_windows() -> None:
    # anti-phase first half, flat second half: windows fully inside the flat tail read
    # NaN (an undefined correlation — never 0, never an event), and every event stays in
    # the anti-phase head.
    donor, acceptor = _antiphase(40)
    donor = np.concatenate([donor, np.full(40, 500.0)])
    acceptor = np.concatenate([acceptor, np.full(40, 500.0)])
    scan = find_anticorrelation_events(donor, acceptor, window=15, step=1, min_magnitude=0.5)
    starts = np.arange(0, 80 - 15 + 1)
    flat_windows = starts >= 40  # windows entirely within the flat tail [40, 80)
    assert np.all(np.isnan(scan.lag0[flat_windows]))
    assert np.all(np.isnan(scan.lag1_magnitude[flat_windows]))
    assert np.isnan(scan.lag0[-1])  # the last window is fully flat
    assert scan.n_events >= 1
    # every event begins in the anti-phase head: a flagged window must overlap [0, 40),
    # so its start index (and thus the event's) is < 40 (flat-only windows are NaN above).
    assert all(ev.start < 40 for ev in scan.events)


def test_too_short_trace_is_empty_scan_not_error() -> None:
    donor, acceptor = _antiphase(10)
    scan = find_anticorrelation_events(donor, acceptor, window=15)
    assert scan.n_windows == 0
    assert scan.n_events == 0
    assert scan.n_frames == 10
    assert scan.lag0.shape == (0,)
    assert scan.lag1_magnitude.shape == (0,)
    assert scan.centers.shape == (0,)


def test_min_windows_drops_short_runs() -> None:
    # a fully anti-phase trace is one run of every window; require more windows than the
    # run holds and it is dropped (min_windows never invents events).
    donor, acceptor = _antiphase(40)
    n_windows = 40 - 15 + 1  # 26 windows, all flagged -> one run
    lenient = find_anticorrelation_events(donor, acceptor, window=15, step=1, min_windows=1)
    strict = find_anticorrelation_events(donor, acceptor, window=15, step=1, min_windows=100)
    assert lenient.n_events == 1
    assert lenient.events[0].n_windows == n_windows
    assert strict.n_events == 0  # 26 < 100 -> the single run is filtered out
    assert strict.n_events <= lenient.n_events


def test_min_windows_boundary_is_inclusive() -> None:
    # pin the < / <= boundary of the length filter: a run of EXACTLY L windows is KEPT at
    # min_windows=L and DROPPED at min_windows=L+1 (catches an off-by-one in _merge_events).
    donor, acceptor = _antiphase(40)
    length = 40 - 15 + 1  # the single run has exactly 26 windows
    kept = find_anticorrelation_events(donor, acceptor, window=15, min_windows=length)
    dropped = find_anticorrelation_events(donor, acceptor, window=15, min_windows=length + 1)
    assert kept.n_events == 1
    assert kept.events[0].n_windows == length
    assert dropped.n_events == 0


def test_min_magnitude_threshold_gates_events() -> None:
    # the anti-phase sine has lag-1 magnitude ~0.87; a threshold below it flags, one above
    # it drops (pins the lag-1-magnitude gate, not just the sign).
    donor, acceptor = _antiphase(60)
    lenient = find_anticorrelation_events(donor, acceptor, window=15, min_magnitude=0.3)
    strict = find_anticorrelation_events(donor, acceptor, window=15, min_magnitude=0.999)
    assert lenient.n_events == 1
    assert strict.n_events == 0  # ~0.87 < 0.999
    assert strict.n_events <= lenient.n_events


def test_event_span_is_union_of_flagged_windows() -> None:
    donor, acceptor = _antiphase(60)
    scan = find_anticorrelation_events(donor, acceptor, window=12, step=1, min_magnitude=0.5)
    (event,) = scan.events
    flagged = _flagged_mask(scan, 0.5)
    first_widx = int(np.argmax(flagged))
    last_widx = int(len(flagged) - 1 - np.argmax(flagged[::-1]))
    starts = np.arange(0, 60 - 12 + 1)
    assert event.start == int(starts[first_widx])
    assert event.stop == int(starts[last_widx]) + 12
    assert event.start <= event.peak_frame < event.stop  # peak is a real centre in-span
    assert event.n_frames == event.stop - event.start


def test_peak_and_mean_are_pinned_to_the_run() -> None:
    # peak = most anti-phase (most-negative lag0) window of the run; peak_lag1_magnitude is
    # that window's |lag-1|; mean_lag0 is the run's mean lag0. Pins all three against a
    # scan (a wrong peak, wrong peak-magnitude, or wrong mean aggregation would fail here).
    donor, acceptor = _localized_event(120, 45, 90)
    scan = find_anticorrelation_events(donor, acceptor, window=15, step=1, min_magnitude=0.5)
    (event,) = scan.events  # a single run, so all flagged windows form one event
    flagged = _flagged_mask(scan, 0.5)
    run_lag0 = scan.lag0[flagged]
    peak_widx = int(np.argmin(np.where(flagged, scan.lag0, np.inf)))
    assert event.peak_lag0 == pytest.approx(float(np.min(run_lag0)))
    assert event.peak_frame == int(scan.centers[peak_widx])
    assert event.peak_lag1_magnitude == pytest.approx(float(scan.lag1_magnitude[peak_widx]))
    assert event.mean_lag0 == pytest.approx(float(np.mean(run_lag0)))


def test_lag0_and_lag1_magnitude_match_reused_cross_correlation() -> None:
    # the per-window lag0 / lag1_magnitude are exactly cross_correlation(win)'s values —
    # proving the finder reuses the crosscorr core rather than reimplementing normalization.
    rng = np.random.default_rng(3)
    donor = rng.normal(500.0, 40.0, size=50)
    acceptor = rng.normal(500.0, 40.0, size=50)
    window, step = 12, 1
    scan = find_anticorrelation_events(donor, acceptor, window=window, step=step)
    for i in (0, 5, 17, scan.n_windows - 1):
        s = i * step
        cc = cross_correlation(donor[s : s + window], acceptor[s : s + window], max_lag=1)
        assert scan.lag0[i] == pytest.approx(cc.lag0)
        assert scan.lag1_magnitude[i] == pytest.approx(cc.lag1_magnitude)


def test_event_dataclass_properties() -> None:
    ev = AnticorrelationEvent(
        start=10,
        stop=25,
        peak_frame=17,
        peak_lag0=-0.8,
        peak_lag1_magnitude=0.7,
        mean_lag0=-0.65,
        n_windows=4,
    )
    assert ev.n_frames == 15
    assert ev.peak_magnitude == pytest.approx(0.8)


def test_core_validation() -> None:
    good = np.arange(20.0)
    with pytest.raises(ValueError, match="same length"):
        find_anticorrelation_events(np.arange(4.0), np.arange(5.0))
    with pytest.raises(ValueError, match="finite"):
        find_anticorrelation_events(np.array([1.0, np.nan, 3.0] + [0.0] * 17), good)
    with pytest.raises(ValueError, match="window must be >= 2"):
        find_anticorrelation_events(good, good, window=1)
    with pytest.raises(ValueError, match="step must be >= 1"):
        find_anticorrelation_events(good, good, step=0)
    with pytest.raises(ValueError, match=r"min_magnitude must be in \[0, 1\]"):
        find_anticorrelation_events(good, good, min_magnitude=1.5)
    with pytest.raises(ValueError, match=r"min_magnitude must be in \[0, 1\]"):
        find_anticorrelation_events(good, good, min_magnitude=-0.1)
    with pytest.raises(ValueError, match="min_windows must be >= 1"):
        find_anticorrelation_events(good, good, min_windows=0)


# --- store-level population ---------------------------------------------------


def _population_channels(n_frames: int = 120) -> tuple:
    """Three molecules: [anti-phase event, correlated, flat] donor/acceptor stacks."""
    d0, a0 = _localized_event(n_frames, 45, 90)  # molecule 0: has an event
    d1, a1 = _correlated(n_frames)  # molecule 1: correlated, no event
    d2 = np.full(n_frames, 400.0)  # molecule 2: flat, no event
    a2 = np.full(n_frames, 600.0)
    donor = np.vstack([d0, d1, d2])
    acceptor = np.vstack([a0, a1, a2])
    return donor, acceptor


def test_population_scans_and_finds_events(tmp_path) -> None:
    donor, acceptor = _population_channels()
    proj, keys = build_store_with_channels(tmp_path, donor, acceptor)

    pop = population_anticorrelation_events(proj, window=15, step=1, min_magnitude=0.5)
    assert isinstance(pop, PopulationAnticorrelation)
    assert pop.n_molecules == 3  # all three are >= window frames
    assert {m.molecule_key for m in pop.molecules} == set(keys)
    assert all(isinstance(m, MoleculeAnticorrelation) for m in pop.molecules)

    by_key = {m.molecule_key: m.scan for m in pop.molecules}
    assert by_key[keys[0]].n_events == 1  # the anti-phase molecule
    assert by_key[keys[1]].n_events == 0  # correlated
    assert by_key[keys[2]].n_events == 0  # flat
    assert pop.n_events == 1


def test_population_excludes_rejected(tmp_path) -> None:
    donor, acceptor = _population_channels()
    proj, keys = build_store_with_channels(tmp_path, donor, acceptor, rejected=[True, False, False])
    pop = population_anticorrelation_events(proj, window=15, min_magnitude=0.5)
    assert pop.n_molecules == 2  # the rejected anti-phase molecule is excluded
    assert keys[0] not in {m.molecule_key for m in pop.molecules}
    assert pop.n_events == 0  # only the correlated + flat survive


def test_population_respects_analysis_window(tmp_path) -> None:
    donor, acceptor = _population_channels()
    # window that excludes the event region [45, 90) for molecule 0.
    proj, keys = build_store_with_channels(
        tmp_path, donor, acceptor, windows=[(0, 40), (0, 120), (0, 120)]
    )
    pop = population_anticorrelation_events(proj, window=15, min_magnitude=0.5)
    by_key = {m.molecule_key: m.scan for m in pop.molecules}
    # molecule 0 is scanned over frames [0, 40) (correlated flank only) -> no event.
    assert by_key[keys[0]].n_frames == 40
    assert by_key[keys[0]].n_events == 0
    assert pop.n_events == 0


def test_population_skips_too_short_molecules(tmp_path) -> None:
    donor, acceptor = _population_channels()
    # molecule 2's analysis window is narrower than the finder window -> skipped.
    proj, keys = build_store_with_channels(
        tmp_path, donor, acceptor, windows=[(0, 120), (0, 120), (0, 10)]
    )
    pop = population_anticorrelation_events(proj, window=15, min_magnitude=0.5)
    assert pop.n_molecules == 2  # molecule 2 (10 frames < window 15) skipped
    assert keys[2] not in {m.molecule_key for m in pop.molecules}


def test_population_molecule_keys_selection(tmp_path) -> None:
    donor, acceptor = _population_channels()
    proj, keys = build_store_with_channels(tmp_path, donor, acceptor)
    pop = population_anticorrelation_events(
        proj, molecule_keys=[keys[0]], window=15, min_magnitude=0.5
    )
    assert pop.n_molecules == 1
    assert pop.molecules[0].molecule_key == keys[0]
    assert pop.n_events == 1


def test_population_invalid_quantity_raises(tmp_path) -> None:
    donor, acceptor = _population_channels()
    proj, _ = build_store_with_channels(tmp_path, donor, acceptor)
    with pytest.raises(ValueError, match="intensity_quantity must be one of"):
        population_anticorrelation_events(proj, intensity_quantity="bogus")


def test_population_invalid_params_raise_even_when_no_molecule_scanned(tmp_path) -> None:
    # the documented ValueError contract must hold even when no molecule is scanned:
    # a bad scan parameter raises up front, not after silently returning an empty result.
    donor, acceptor = _population_channels()
    proj, _ = build_store_with_channels(tmp_path, donor, acceptor)
    with pytest.raises(ValueError, match="window must be >= 2"):
        population_anticorrelation_events(proj, molecule_keys=["nonexistent"], window=1)
    with pytest.raises(ValueError, match=r"min_magnitude must be in \[0, 1\]"):
        population_anticorrelation_events(proj, molecule_keys=["nonexistent"], min_magnitude=5.0)


# --- shared store helper (windowed_channels_with_keys) ------------------------


def test_windowed_channels_with_keys_matches_keyless(tmp_path) -> None:
    donor, acceptor = _population_channels()
    proj, keys = build_store_with_channels(tmp_path, donor, acceptor)

    keyed = windowed_channels_with_keys(proj, None, "corrected", False)
    keyless = windowed_channels(proj, None, "corrected", False)
    assert [k for k, _d, _a in keyed] == keys  # store order, all accepted
    assert len(keyed) == len(keyless)
    for (_k, kd, ka), (ld, la) in zip(keyed, keyless, strict=True):
        np.testing.assert_array_equal(kd, ld)
        np.testing.assert_array_equal(ka, la)
