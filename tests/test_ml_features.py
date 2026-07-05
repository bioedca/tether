# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Engineered per-trace features — pure core (M5, FR-ML; PRD §7.5).

Locks the trace-derived feature block: the reuse-consistency with the underlying
:func:`tether.fret.apparent_fret` / :func:`tether.analysis.cross_correlation`
definitions (no drift), the total-intensity SNR, and the undefined -> NaN
discipline (never a fabricated 0). Headless -> base CI matrix.
"""

from __future__ import annotations

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")

import numpy as np  # noqa: E402

from tether.analysis import cross_correlation  # noqa: E402
from tether.fret.efficiency import apparent_fret  # noqa: E402
from tether.ml.features import (  # noqa: E402
    FEATURE_NAMES,
    TraceFeatures,
    compute_trace_features,
)


def test_feature_names_and_vector_alignment() -> None:
    feats = compute_trace_features(np.array([1.0, 2.0, 3.0]), np.array([3.0, 1.0, 2.0]))
    vec = feats.as_vector()
    assert vec.dtype == np.float64
    assert vec.shape == (len(FEATURE_NAMES),)
    # Every name resolves to a dataclass field and lands in vector order.
    for j, name in enumerate(FEATURE_NAMES):
        assert vec[j] == pytest.approx(float(getattr(feats, name)))
    assert vec[0] == pytest.approx(3.0)  # n_frames cast to float


def test_reuse_matches_underlying_primitives() -> None:
    # A faithful aggregator: features must equal the very definitions they reduce,
    # so the ranker never sees a second (drifting) copy of apparent-E / cross-corr.
    rng = np.random.default_rng(11)
    donor = rng.normal(600.0, 80.0, size=40)
    acceptor = rng.normal(500.0, 70.0, size=40)
    feats = compute_trace_features(donor, acceptor)

    total = donor + acceptor
    assert feats.n_frames == 40
    assert feats.total_intensity == pytest.approx(float(total.mean()))
    assert feats.snr == pytest.approx(float(total.mean() / total.std()))  # ddof=0

    e = apparent_fret(donor, acceptor)
    finite = np.isfinite(e)
    assert feats.fret_mean == pytest.approx(float(e[finite].mean()))
    assert feats.fret_var == pytest.approx(float(e[finite].var()))

    cc = cross_correlation(donor, acceptor)
    assert feats.anticorr_lag0 == pytest.approx(cc.lag0)
    assert feats.anticorr_lag1_magnitude == pytest.approx(cc.lag1_magnitude)


def test_snr_is_ddof0_mean_over_std() -> None:
    donor = np.array([10.0, 20.0, 30.0, 40.0])
    acceptor = np.array([5.0, 5.0, 20.0, 10.0])
    total = donor + acceptor
    feats = compute_trace_features(donor, acceptor)
    assert feats.snr == pytest.approx(float(total.mean() / np.std(total)))  # population std
    # A ddof=1 std would give a different value; guard the definition.
    assert feats.snr != pytest.approx(float(total.mean() / np.std(total, ddof=1)))


def test_perfect_anticorrelation_conserves_total_snr_undefined() -> None:
    # acceptor = C - donor: exact anti-phase (lag0 = -1) AND a conserved constant
    # total -> SNR is undefined (constant total, std 0), reported NaN not fabricated.
    donor = np.array([1.0, 3.0, 2.0, 5.0, 4.0, 6.0])
    acceptor = 10.0 - donor
    feats = compute_trace_features(donor, acceptor)
    assert feats.anticorr_lag0 == pytest.approx(-1.0)
    assert np.isnan(feats.snr)
    assert feats.total_intensity == pytest.approx(10.0)
    assert feats.fret_mean == pytest.approx(float(np.mean((10.0 - donor) / 10.0)))


def test_constant_channel_anticorr_is_nan_not_zero() -> None:
    donor = np.full(10, 500.0)  # sigma 0 -> correlation undefined
    acceptor = np.arange(10, dtype=float)
    feats = compute_trace_features(donor, acceptor)
    assert np.isnan(feats.anticorr_lag0)
    assert np.isnan(feats.anticorr_lag1_magnitude)


def test_empty_window_all_nan() -> None:
    feats = compute_trace_features(np.array([]), np.array([]))
    assert feats.n_frames == 0
    assert np.isnan(feats.total_intensity)
    assert np.isnan(feats.snr)
    assert np.isnan(feats.fret_mean)
    assert np.isnan(feats.fret_var)
    assert np.isnan(feats.anticorr_lag0)
    assert np.isnan(feats.anticorr_lag1_magnitude)


def test_single_frame_defines_only_pointwise_features() -> None:
    feats = compute_trace_features(np.array([30.0]), np.array([10.0]))
    assert feats.n_frames == 1
    assert feats.total_intensity == pytest.approx(40.0)
    assert feats.fret_mean == pytest.approx(0.25)  # 10 / 40
    assert feats.fret_var == pytest.approx(0.0)  # one finite frame
    assert np.isnan(feats.snr)  # < 2 frames
    assert np.isnan(feats.anticorr_lag0)  # < 2 frames


def test_zero_total_frames_excluded_from_fret() -> None:
    # A D+A==0 frame has an undefined apparent-E (NaN); fret_mean reduces over the
    # finite frames only, and is NaN only when no frame is defined.
    donor = np.array([0.0, 30.0, 10.0])
    acceptor = np.array([0.0, 10.0, 30.0])  # frame 0: total 0 -> NaN apparent-E
    feats = compute_trace_features(donor, acceptor)
    e = apparent_fret(donor, acceptor)
    assert np.isnan(e[0])
    assert feats.fret_mean == pytest.approx(float(np.mean(e[1:])))

    dead = compute_trace_features(np.zeros(4), np.zeros(4))
    assert np.isnan(dead.fret_mean)
    assert np.isnan(dead.fret_var)


def test_length_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="same length"):
        compute_trace_features(np.arange(4.0), np.arange(5.0))


def test_non_1d_input_raises_not_silently_flattened() -> None:
    # Two multi-D inputs with equal element count but different shapes must fail
    # loudly — a bare .ravel() would flatten both to length 6 and silently misalign
    # them into one feature vector (the "never fabricate for malformed input" rule).
    with pytest.raises(ValueError, match="1-D"):
        compute_trace_features(np.zeros((2, 3)), np.zeros((3, 2)))
    with pytest.raises(ValueError, match="1-D"):
        compute_trace_features(np.zeros((2, 3)), np.zeros((2, 3)))


def test_determinism() -> None:
    rng = np.random.default_rng(3)
    donor = rng.normal(500.0, 40.0, size=25)
    acceptor = rng.normal(450.0, 40.0, size=25)
    a = compute_trace_features(donor, acceptor)
    b = compute_trace_features(donor.copy(), acceptor.copy())
    assert isinstance(a, TraceFeatures)
    assert a == b
    np.testing.assert_array_equal(a.as_vector(), b.as_vector())
