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
from tether.analysis.overlap import APERTURE_OVERLAP_FACTOR, DEFAULT_APERTURE_RADIUS  # noqa: E402
from tether.fret.efficiency import apparent_fret  # noqa: E402
from tether.ml.features import (  # noqa: E402
    FEATURE_NAMES,
    SPATIAL_FEATURE_NAMES,
    TRACE_FEATURE_NAMES,
    SpatialFeatures,
    TraceFeatures,
    compute_spatial_features,
    compute_trace_features,
)


def test_feature_names_and_vector_alignment() -> None:
    feats = compute_trace_features(np.array([1.0, 2.0, 3.0]), np.array([3.0, 1.0, 2.0]))
    vec = feats.as_vector()
    assert vec.dtype == np.float64
    assert vec.shape == (len(TRACE_FEATURE_NAMES),)
    # Every name resolves to a dataclass field and lands in vector order.
    for j, name in enumerate(TRACE_FEATURE_NAMES):
        assert vec[j] == pytest.approx(float(getattr(feats, name)))
    assert vec[0] == pytest.approx(3.0)  # n_frames cast to float


def test_full_schema_is_trace_then_spatial() -> None:
    # FEATURE_NAMES (the /features/table column order) is the trace block followed by
    # the spatial block, with no overlap — the single source of truth the store builds
    # its dtype and the ranker's matrix column order from.
    assert FEATURE_NAMES == TRACE_FEATURE_NAMES + SPATIAL_FEATURE_NAMES
    assert set(TRACE_FEATURE_NAMES).isdisjoint(SPATIAL_FEATURE_NAMES)
    assert SPATIAL_FEATURE_NAMES == ("neighbor_distance", "aperture_overlap")


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


# --- spatial (crowding) features ------------------------------------------------


def test_spatial_features_distance_and_overlap() -> None:
    # Three donor spots in one movie: 0 & 1 are 2 px apart (< 2·3 = 6 -> apertures
    # overlap); 2 is far. neighbor_distance is the nearest-other distance; the flag is
    # the aperture-overlap of that nearest neighbour.
    coords = np.array([[10.0, 10.0], [12.0, 10.0], [80.0, 80.0]])
    movie_ids = np.array(["m", "m", "m"])
    sf = compute_spatial_features(coords, movie_ids=movie_ids)
    assert len(sf) == 3  # one per molecule, none dropped
    assert sf[0].neighbor_distance == pytest.approx(2.0)
    assert sf[1].neighbor_distance == pytest.approx(2.0)
    assert sf[0].aperture_overlap == 1.0
    assert sf[1].aperture_overlap == 1.0
    # Molecule 2's nearest is molecule 1 at sqrt(68^2 + 70^2), far beyond the aperture.
    assert sf[2].neighbor_distance == pytest.approx(float(np.hypot(68.0, 70.0)))
    assert sf[2].aperture_overlap == 0.0
    assert sf[0].as_vector().tolist() == [2.0, 1.0]


def test_spatial_overlap_threshold_tracks_aperture_radius() -> None:
    # A pair 5 px apart overlaps at radius 3 (2·3 = 6 > 5) but not at radius 2
    # (2·2 = 4 < 5). The flag is settled geometry keyed off the aperture radius,
    # not a free threshold.
    coords = np.array([[0.0, 0.0], [5.0, 0.0]])
    movie_ids = np.array(["m", "m"])
    assert APERTURE_OVERLAP_FACTOR == 2.0
    over = compute_spatial_features(coords, movie_ids=movie_ids, aperture_radius=3.0)
    assert over[0].aperture_overlap == 1.0 and over[1].aperture_overlap == 1.0
    under = compute_spatial_features(coords, movie_ids=movie_ids, aperture_radius=2.0)
    assert under[0].aperture_overlap == 0.0 and under[1].aperture_overlap == 0.0
    # ...but the raw neighbour distance is radius-independent.
    assert over[0].neighbor_distance == pytest.approx(5.0)
    assert under[0].neighbor_distance == pytest.approx(5.0)


def test_spatial_neighbour_search_is_per_movie() -> None:
    # A closer spot in a *different* movie is not a neighbour (§5.2): molecule 0's
    # neighbour is molecule 1 in its own movie, never the nearer molecule 2 elsewhere.
    coords = np.array([[10.0, 10.0], [40.0, 10.0], [11.0, 10.0]])
    movie_ids = np.array(["a", "a", "b"])
    sf = compute_spatial_features(coords, movie_ids=movie_ids)
    assert sf[0].neighbor_distance == pytest.approx(30.0)  # to molecule 1, not molecule 2 (1 px)
    assert sf[0].aperture_overlap == 0.0


def test_spatial_isolated_molecule_distance_nan_overlap_zero() -> None:
    # The only molecule in its movie has no neighbour: distance is undefined (NaN,
    # never a fabricated 0 that would read as "touching"), overlap is a defined 0.0
    # (there is no second molecule), and the molecule is never dropped.
    coords = np.array([[10.0, 10.0], [12.0, 10.0], [5.0, 5.0]])
    movie_ids = np.array(["pair", "pair", "lonely"])
    sf = compute_spatial_features(coords, movie_ids=movie_ids)
    assert np.isnan(sf[2].neighbor_distance)
    assert sf[2].aperture_overlap == 0.0


def test_spatial_non_finite_coord_is_nan_not_dropped_and_not_poisoning() -> None:
    # A molecule with a non-finite coordinate gets all-NaN features (reported, never
    # dropped or fabricated a position) and is excluded from the neighbour search, so
    # it can neither poison the KDTree nor become another molecule's phantom neighbour.
    coords = np.array([[10.0, 10.0], [np.nan, 10.0], [13.0, 10.0]])
    movie_ids = np.array(["m", "m", "m"])
    sf = compute_spatial_features(coords, movie_ids=movie_ids)
    assert len(sf) == 3  # population invariant: one row per input, always
    assert np.isnan(sf[1].neighbor_distance) and np.isnan(sf[1].aperture_overlap)
    # Molecule 0's neighbour is molecule 2 (3 px) — the NaN row is simply absent.
    assert sf[0].neighbor_distance == pytest.approx(3.0)
    assert sf[2].neighbor_distance == pytest.approx(3.0)


def test_spatial_coincident_spots_overlap_at_zero_distance() -> None:
    # Two spots at the same centre are a genuine overlap (distance 0), kept as a real
    # signal — not treated as "no neighbour".
    coords = np.array([[7.0, 7.0], [7.0, 7.0]])
    movie_ids = np.array(["m", "m"])
    sf = compute_spatial_features(coords, movie_ids=movie_ids)
    assert sf[0].neighbor_distance == pytest.approx(0.0)
    assert sf[0].aperture_overlap == 1.0


def test_spatial_population_invariant_never_drops() -> None:
    # Output length always equals input length, whatever the mix of finite / NaN /
    # isolated molecules — the never-auto-drop contract at the feature layer.
    rng = np.random.default_rng(4)
    coords = rng.normal(50.0, 20.0, size=(20, 2))
    coords[3] = np.nan  # a bad coordinate
    movie_ids = np.array(["a"] * 10 + ["b"] * 9 + ["solo"])
    sf = compute_spatial_features(coords, movie_ids=movie_ids)
    assert len(sf) == 20
    assert all(isinstance(s, SpatialFeatures) for s in sf)


def test_spatial_all_non_finite_returns_all_nan() -> None:
    coords = np.full((3, 2), np.nan)
    movie_ids = np.array(["m", "m", "m"])
    sf = compute_spatial_features(coords, movie_ids=movie_ids)
    assert len(sf) == 3
    assert all(np.isnan(s.neighbor_distance) and np.isnan(s.aperture_overlap) for s in sf)


def test_spatial_determinism() -> None:
    rng = np.random.default_rng(9)
    coords = rng.normal(60.0, 15.0, size=(12, 2))
    movie_ids = np.array(["a", "b"] * 6)
    a = compute_spatial_features(coords, movie_ids=movie_ids)
    b = compute_spatial_features(coords.copy(), movie_ids=movie_ids.copy())
    assert a == b
    assert DEFAULT_APERTURE_RADIUS == 3.0  # the documented default


def test_spatial_validation_raises() -> None:
    with pytest.raises(ValueError, match=r"\(N, 2\)"):
        compute_spatial_features(np.zeros((3, 3)), movie_ids=np.array(["m", "m", "m"]))
    with pytest.raises(ValueError, match="movie_ids must be length"):
        compute_spatial_features(np.zeros((3, 2)), movie_ids=np.array(["m", "m"]))
    with pytest.raises(ValueError, match="aperture_radius"):
        compute_spatial_features(
            np.zeros((2, 2)), movie_ids=np.array(["m", "m"]), aperture_radius=0.0
        )
