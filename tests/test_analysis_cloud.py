# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Raw FRET cloud QC view (M6 PR-5a, FR-ANALYZE; PRD §7.7).

The cloud pools each accepted molecule's windowed **apparent E** into a
``(time, E)`` scatter, then attaches a 2-D Gaussian-KDE surface and its
highest-density-region (HDR) percentile-contour thresholds (the numerical-grid
density-quantile method [Hyndman1996][Haselsteiner2017]). It is a *pre-idealization*
view: it reads ``/traces`` only, applies the §7.5 curation filter, and has no
fresh/stale model filter. All headless (no Qt) → base CI matrix; the KDE uses
``scipy.stats``.
"""

from __future__ import annotations

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("h5py")

import numpy as np  # noqa: E402

from _analysis_store import MEANS, build_store_with_model  # noqa: E402
from tether.analysis import (  # noqa: E402
    DEFAULT_CLOUD_HDR_COVERAGES,
    DEFAULT_CLOUD_SIGNAL_BINS,
    DEFAULT_CLOUD_SIGNAL_RANGE,
    DEFAULT_CLOUD_TIME_BINS,
    RawFretCloud,
    population_raw_fret_cloud,
    raw_fret_cloud,
)
from tether.idealize import NO_STATE  # noqa: E402


def _two_band_cloud(n_molecules: int = 30, n_frames: int = 80, seed: int = 0) -> list[np.ndarray]:
    """Per-molecule apparent-E traces from a deterministic two-band (0.3 / 0.7) mixture.

    A genuine (time, E) cloud with structure for the KDE/HDR assertions; seeded so the
    pooled sample is fixed (the function under test is deterministic given it).
    """
    rng = np.random.default_rng(seed)
    chunks: list[np.ndarray] = []
    for i in range(n_molecules):
        centre = 0.3 if i % 2 == 0 else 0.7
        chunks.append(centre + 0.03 * rng.standard_normal(n_frames))
    return chunks


def _grid_mass_fraction_above(cloud: RawFretCloud, level: float) -> float:
    """Fraction of the KDE grid's mass sitting in cells with density >= ``level``."""
    assert cloud.density is not None
    t_lo, t_hi = cloud.time_range
    s_lo, s_hi = cloud.signal_range
    cell = (t_hi - t_lo) / cloud.time_bins * (s_hi - s_lo) / cloud.signal_bins
    d = cloud.density
    total = float(d.sum()) * cell
    return float(d[d >= level].sum()) * cell / total


# --- pure core: raw_fret_cloud pooling ----------------------------------------


def test_pools_finite_points_and_counts_molecules() -> None:
    chunks = [
        np.array([0.2, 0.3, np.nan, 0.4]),  # 3 finite
        np.array([np.nan, 0.6, 0.7]),  # 2 finite
        np.array([np.nan, np.nan]),  # 0 finite -> not a contributor
    ]
    cloud = raw_fret_cloud(chunks, kde=False)
    assert cloud.n_samples == 5
    assert cloud.n_molecules == 2  # the all-NaN molecule contributes nothing
    assert cloud.points.shape == (5, 2)


def test_time_coordinate_is_frame_index_times_dt() -> None:
    # frame 1 and frame 3 are finite -> times 1*dt and 3*dt (index within the window).
    chunks = [np.array([np.nan, 0.5, np.nan, 0.6])]
    cloud = raw_fret_cloud(chunks, time_dt=2.0, kde=False)
    assert cloud.points[:, 0].tolist() == [2.0, 6.0]
    assert cloud.points[:, 1].tolist() == [0.5, 0.6]


def test_kde_surface_shape_and_nonnegative() -> None:
    cloud = raw_fret_cloud(_two_band_cloud(), time_bins=40, signal_bins=50)
    assert cloud.density is not None
    assert cloud.density.shape == (40, 50)
    assert np.all(cloud.density >= 0.0)
    assert np.all(np.isfinite(cloud.density))
    assert cloud.density.sum() > 0.0
    assert cloud.bandwidth is not None and cloud.bandwidth > 0.0


def _centers(edges: np.ndarray) -> np.ndarray:
    return 0.5 * (edges[:-1] + edges[1:])


def test_kde_density_sits_on_the_two_bands() -> None:
    # #741, ANA-F4: the surface must be WHERE the data is, not only the right shape.
    cloud = raw_fret_cloud(_two_band_cloud(), time_bins=60, signal_bins=60)
    assert cloud.density is not None
    e = _centers(cloud.signal_edges)
    width = float(e[1] - e[0])
    marginal = cloud.density.sum(axis=0)  # E-marginal of the surface
    low, high = e < 0.5, e >= 0.5
    assert abs(float(e[low][np.argmax(marginal[low])]) - 0.3) <= width
    assert abs(float(e[high][np.argmax(marginal[high])]) - 0.7) <= width
    # the empty gap between the bands carries almost no density
    at_gap = float(marginal[np.argmin(np.abs(e - 0.5))])
    assert at_gap < 0.1 * float(marginal.max())


def test_kde_density_follows_the_time_axis() -> None:
    # Every molecule sits at E = 0.3 for frames 0..39 and at E = 0.7 for frames 40..79,
    # so each time row of the surface must peak at the band of its own time. This pins
    # the time and E grid centres and their pairing in the KDE evaluation (#741, ANA-F4).
    rng = np.random.default_rng(5)
    frames = np.arange(80)
    chunks = [np.where(frames < 40, 0.3, 0.7) + 0.03 * rng.standard_normal(80) for _ in range(20)]
    cloud = raw_fret_cloud(chunks, time_bins=40, signal_bins=60)
    assert cloud.density is not None
    t = _centers(cloud.time_edges)
    e = _centers(cloud.signal_edges)
    width = float(e[1] - e[0])
    row_peak = e[np.argmax(cloud.density, axis=1)]
    early = (t > 5.0) & (t < 30.0)
    late = (t > 50.0) & (t < 75.0)
    assert early.sum() >= 10 and late.sum() >= 10
    assert np.all(np.abs(row_peak[early] - 0.3) <= 2.0 * width)
    assert np.all(np.abs(row_peak[late] - 0.7) <= 2.0 * width)


def test_default_grid_shape() -> None:
    cloud = raw_fret_cloud(_two_band_cloud())
    assert cloud.density is not None
    assert cloud.density.shape == (DEFAULT_CLOUD_TIME_BINS, DEFAULT_CLOUD_SIGNAL_BINS)
    assert cloud.signal_range == DEFAULT_CLOUD_SIGNAL_RANGE


def test_hdr_levels_ordered_and_positive() -> None:
    cloud = raw_fret_cloud(_two_band_cloud())
    assert cloud.hdr_levels is not None
    # coverages come back sorted ascending; a smaller coverage -> a higher threshold.
    assert cloud.hdr_coverages.tolist() == sorted(DEFAULT_CLOUD_HDR_COVERAGES)
    assert cloud.hdr_levels.shape == (len(DEFAULT_CLOUD_HDR_COVERAGES),)
    assert cloud.hdr_levels[0] > cloud.hdr_levels[1] > 0.0  # 50% level > 95% level


def test_hdr_levels_enclose_requested_mass() -> None:
    cloud = raw_fret_cloud(_two_band_cloud(), time_bins=60, signal_bins=60)
    assert cloud.hdr_levels is not None
    frac50 = _grid_mass_fraction_above(cloud, float(cloud.hdr_levels[0]))
    frac95 = _grid_mass_fraction_above(cloud, float(cloud.hdr_levels[1]))
    # the 50% contour encloses ~half the grid mass, the 95% contour ~all of it, and the
    # larger coverage never encloses less mass (the density-quantile guarantee).
    # Tight limits (#741, ANA-F5): the grid method reaches 0.5011 / 0.9502 on this cloud,
    # so a threshold read 10 points too far along the sorted mass (a 60 % contour
    # reported as the 50 % one) fails here.
    assert frac50 == pytest.approx(0.50, abs=0.01)
    assert frac95 == pytest.approx(0.95, abs=0.01)
    assert frac50 <= frac95 + 1e-9


def test_custom_hdr_coverages_sorted() -> None:
    cloud = raw_fret_cloud(_two_band_cloud(), hdr_coverages=(0.9, 0.25, 0.68))
    assert cloud.hdr_levels is not None
    assert cloud.hdr_coverages.tolist() == [0.25, 0.68, 0.9]
    # thresholds strictly decrease as coverage grows.
    assert cloud.hdr_levels[0] > cloud.hdr_levels[1] > cloud.hdr_levels[2] > 0.0


def test_reproducible_given_the_same_cloud() -> None:
    chunks = _two_band_cloud()
    a = raw_fret_cloud(chunks)
    b = raw_fret_cloud(chunks)
    assert a.density is not None and b.density is not None
    assert np.array_equal(a.density, b.density)
    assert a.hdr_levels is not None and b.hdr_levels is not None
    assert np.array_equal(a.hdr_levels, b.hdr_levels)
    assert np.array_equal(a.points, b.points)


def test_kde_disabled_keeps_scatter_only() -> None:
    cloud = raw_fret_cloud(_two_band_cloud(), kde=False)
    assert cloud.density is None
    assert cloud.hdr_levels is None
    assert cloud.bandwidth is None
    assert cloud.n_samples > 0  # the raw scatter is still there
    assert cloud.points.shape == (cloud.n_samples, 2)


def test_bandwidth_is_scotts_factor() -> None:
    # 10 molecules x 50 frames, all in-range -> gaussian_kde Scott factor n**(-1/(d+4)),
    # d = 2 -> n**(-1/6). Pins the self-describing bandwidth to the value actually used.
    cloud = raw_fret_cloud(_two_band_cloud(10, 50))
    assert cloud.n_out_of_range == 0
    n = cloud.n_samples
    assert cloud.bandwidth == pytest.approx(n ** (-1.0 / 6.0))


def test_out_of_range_outliers_excluded_from_kde_fit() -> None:
    # apparent E is un-clipped, so bleached/blinking frames can produce finite but
    # off-grid E. Those must NOT perturb the in-range KDE surface: adding two all-outlier
    # molecules (E = 5.0 / -3.0, both outside [-0.25, 1.25]) leaves the density and HDR
    # levels byte-identical, while the outliers are counted, not silently folded in.
    clean_chunks = _two_band_cloud(20, 60)
    dirty_chunks = [*clean_chunks, np.full(60, 5.0), np.full(60, -3.0)]
    clean = raw_fret_cloud(clean_chunks)
    dirty = raw_fret_cloud(dirty_chunks)

    assert clean.n_out_of_range == 0
    assert dirty.n_out_of_range == 2 * 60
    assert dirty.n_samples == clean.n_samples + 2 * 60  # scatter keeps every finite point
    # the in-grid fit is exactly the same sample -> exactly the same surface + contours.
    assert clean.density is not None and dirty.density is not None
    assert np.array_equal(clean.density, dirty.density)
    assert clean.hdr_levels is not None and dirty.hdr_levels is not None
    assert np.array_equal(clean.hdr_levels, dirty.hdr_levels)
    assert clean.bandwidth == dirty.bandwidth


def test_points_below_zero_inside_the_grid_are_fit() -> None:
    # #741, ANA-F7: the lower E bound of the KDE fit is signal_range[0] (-0.25 by
    # default), not 0. Points at E = -0.1 are in the grid and must not be counted out.
    rng = np.random.default_rng(2)
    chunks = [-0.1 + 0.02 * rng.standard_normal(40) for _ in range(5)]
    chunks += [0.6 + 0.02 * rng.standard_normal(40) for _ in range(5)]
    cloud = raw_fret_cloud(chunks)
    assert cloud.n_out_of_range == 0
    assert cloud.density is not None
    e = _centers(cloud.signal_edges)
    marginal = cloud.density.sum(axis=0)
    width = float(e[1] - e[0])
    low = e < 0.25
    assert abs(float(e[low][np.argmax(marginal[low])]) + 0.1) <= width


def test_explicit_time_range_excludes_later_points_from_the_fit() -> None:
    # #741, ANA-F7: a time_range narrower than the data keeps every point in the scatter
    # but counts the later ones out of range and leaves them out of the KDE fit.
    chunks = _two_band_cloud(10, 80)
    cloud = raw_fret_cloud(chunks, time_range=(0.0, 39.0))
    assert cloud.n_samples == 10 * 80
    assert cloud.n_out_of_range == 10 * 40  # frames 40..79 lie after the time range
    clipped = raw_fret_cloud([c[:40] for c in chunks], time_range=(0.0, 39.0))
    assert clipped.n_out_of_range == 0
    assert cloud.density is not None and clipped.density is not None
    np.testing.assert_allclose(cloud.density, clipped.density, rtol=1e-12, atol=0)
    assert cloud.bandwidth == clipped.bandwidth


# --- pure core: never-crash guards --------------------------------------------


def test_single_point_yields_no_surface() -> None:
    cloud = raw_fret_cloud([np.array([0.5])])
    assert cloud.n_samples == 1
    assert cloud.density is None  # < 2 points -> no KDE
    assert cloud.hdr_levels is None
    assert cloud.points.shape == (1, 2)


def test_identical_points_are_singular_no_surface() -> None:
    # two molecules, one finite frame each at the same (t=0, E=0.5): a singular covariance.
    cloud = raw_fret_cloud([np.array([0.5]), np.array([0.5])])
    assert cloud.n_samples == 2
    assert cloud.density is None  # singular covariance -> caught, not a crash
    assert cloud.hdr_levels is None


def test_constant_e_axis_is_degenerate_no_surface() -> None:
    # E constant across time -> the KDE mass sits on a zero-width line; scipy returns a
    # silently-degenerate all-zero grid rather than raising, so it is suppressed to None.
    cloud = raw_fret_cloud([np.full(50, 0.5)])
    assert cloud.n_samples == 50
    assert cloud.density is None
    assert cloud.hdr_levels is None


def test_empty_cloud() -> None:
    cloud = raw_fret_cloud([np.array([np.nan, np.nan]), np.array([])], time_dt=1.0)
    assert cloud.n_samples == 0
    assert cloud.n_molecules == 0
    assert cloud.n_out_of_range == 0
    assert cloud.density is None
    assert cloud.hdr_levels is None
    assert cloud.points.shape == (0, 2)
    assert cloud.time_range == (0.0, 1.0)  # empty -> the dt-wide fallback axis


def test_no_chunks_at_all() -> None:
    cloud = raw_fret_cloud([])
    assert cloud.n_samples == 0
    assert cloud.n_molecules == 0
    assert cloud.density is None


def test_empty_hdr_coverages_gives_empty_levels() -> None:
    cloud = raw_fret_cloud(_two_band_cloud(), hdr_coverages=())
    assert cloud.density is not None
    assert cloud.hdr_levels is not None
    assert cloud.hdr_levels.shape == (0,)


# --- pure core: time-range handling -------------------------------------------


def test_time_range_derived_from_data() -> None:
    cloud = raw_fret_cloud([np.array([0.3, 0.4, 0.5])], time_dt=1.0, kde=False)
    assert cloud.time_range == (0.0, 2.0)  # last finite frame index * dt
    assert cloud.time_edges[0] == 0.0
    assert cloud.time_edges[-1] == pytest.approx(2.0)


def test_time_range_explicit_overrides() -> None:
    cloud = raw_fret_cloud(_two_band_cloud(), time_range=(0.0, 200.0), kde=False)
    assert cloud.time_range == (0.0, 200.0)
    assert cloud.time_edges[-1] == pytest.approx(200.0)


def test_single_finite_frame_gives_nonzero_time_width() -> None:
    # one finite point at t=0: the derived range must still have hi > lo for the grid,
    # exactly time_lo + dt (the degenerate-width fallback), not some wider guess.
    cloud = raw_fret_cloud([np.array([0.5])], time_dt=1.0, kde=False)
    assert cloud.time_range == (0.0, 1.0)
    cloud3 = raw_fret_cloud([np.array([0.5])], time_dt=3.0, kde=False)
    assert cloud3.time_range == (0.0, 3.0)


# --- pure core: validation ----------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"time_bins": 0}, "time_bins"),
        ({"signal_bins": 0}, "signal_bins"),
        ({"signal_range": (1.0, 0.0)}, "signal_range"),
        ({"time_dt": 0.0}, "time_dt"),
        ({"time_dt": float("nan")}, "time_dt"),
        ({"time_range": (5.0, 5.0)}, "time_range"),
        ({"hdr_coverages": (0.0, 0.5)}, "hdr_coverages"),
        ({"hdr_coverages": (0.5, 1.0)}, "hdr_coverages"),
        ({"hdr_coverages": (1.5,)}, "hdr_coverages"),
    ],
)
def test_invalid_parameters_raise(kwargs: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        raw_fret_cloud(_two_band_cloud(4, 10), **kwargs)


def test_scalar_chunk_is_rejected() -> None:
    with pytest.raises(ValueError, match="wrap a single molecule"):
        raw_fret_cloud(np.array([0.2, 0.3, 0.4]))  # a flat array, not an iterable of arrays


# --- store entry: population_raw_fret_cloud -----------------------------------


def _states(n_molecules: int, n_frames: int) -> np.ndarray:
    """A state matrix cycling every molecule through all three MEANS levels."""
    base = np.arange(n_frames) % MEANS.size
    return np.tile(base, (n_molecules, 1)).astype("int64")


def test_population_cloud_from_store(tmp_path) -> None:
    project, _ = build_store_with_model(tmp_path, _states(4, 30), MEANS)
    cloud = population_raw_fret_cloud(project)
    assert cloud.n_molecules == 4
    assert cloud.n_samples == 4 * 30  # every frame on-state -> finite apparent E
    assert cloud.density is not None
    assert cloud.hdr_levels is not None
    # apparent E follows the idealized level, so every E value is one of the MEANS.
    assert np.all(np.isin(np.round(cloud.points[:, 1], 6), np.round(MEANS, 6)))


def test_population_cloud_excludes_rejected_by_default(tmp_path) -> None:
    project, _ = build_store_with_model(
        tmp_path, _states(4, 20), MEANS, rejected=[True, False, False, False]
    )
    default = population_raw_fret_cloud(project)
    assert default.n_molecules == 3
    with_rejected = population_raw_fret_cloud(project, include_rejected=True)
    assert with_rejected.n_molecules == 4


def test_population_cloud_molecule_key_selection(tmp_path) -> None:
    project, keys = build_store_with_model(tmp_path, _states(4, 20), MEANS)
    cloud = population_raw_fret_cloud(project, molecule_keys=keys[:2])
    assert cloud.n_molecules == 2


def test_population_cloud_drops_gap_frames(tmp_path) -> None:
    # molecule 0 idealized only over frames 0..19; 20..29 are NO_STATE -> NaN apparent E.
    states = _states(2, 30)
    states[0, 20:] = NO_STATE
    project, _ = build_store_with_model(tmp_path, states, MEANS)
    cloud = population_raw_fret_cloud(project, kde=False)
    # molecule 0 contributes 20 finite frames, molecule 1 all 30.
    assert cloud.n_samples == 20 + 30


def test_population_cloud_missing_selection_is_empty(tmp_path) -> None:
    project, _ = build_store_with_model(tmp_path, _states(2, 20), MEANS)
    cloud = population_raw_fret_cloud(project, molecule_keys=["does-not-exist"])
    assert cloud.n_samples == 0
    assert cloud.n_molecules == 0
    assert cloud.density is None


def test_population_cloud_bad_quantity_raises(tmp_path) -> None:
    project, _ = build_store_with_model(tmp_path, _states(2, 20), MEANS)
    with pytest.raises(ValueError, match="intensity_quantity"):
        population_raw_fret_cloud(project, intensity_quantity="bogus")


def test_population_cloud_kde_disabled(tmp_path) -> None:
    project, _ = build_store_with_model(tmp_path, _states(3, 20), MEANS)
    cloud = population_raw_fret_cloud(project, kde=False)
    assert cloud.density is None
    assert cloud.hdr_levels is None
    assert cloud.n_samples == 3 * 20


def test_population_cloud_time_range_spans_window(tmp_path) -> None:
    project, _ = build_store_with_model(tmp_path, _states(2, 25), MEANS)
    cloud = population_raw_fret_cloud(project, kde=False)
    assert cloud.time_range[0] == 0.0
    assert cloud.time_range[1] == pytest.approx(24.0)  # last frame index of a 25-frame window


def test_population_cloud_forwards_time_dt(tmp_path) -> None:
    # the store entry point must scale the time axis by time_dt, not silently frame-index.
    project, _ = build_store_with_model(tmp_path, _states(2, 25), MEANS)
    cloud = population_raw_fret_cloud(project, time_dt=2.0, kde=False)
    assert cloud.time_dt == 2.0
    assert cloud.time_range[1] == pytest.approx(24 * 2.0)  # (n_frames - 1) * time_dt


def test_population_cloud_forwards_grid_and_kde_arguments(tmp_path) -> None:
    # #741, ANA-F7: each documented pass-through argument must reach raw_fret_cloud.
    project, _ = build_store_with_model(tmp_path, _states(4, 30), MEANS)
    cloud = population_raw_fret_cloud(
        project,
        time_bins=7,
        signal_bins=9,
        time_range=(0.0, 14.0),
        hdr_coverages=(0.9, 0.25, 0.5),
        bw_method=0.5,
    )
    assert cloud.density is not None and cloud.hdr_levels is not None
    assert cloud.density.shape == (7, 9)
    assert cloud.time_bins == 7 and cloud.signal_bins == 9
    assert cloud.time_range == (0.0, 14.0)
    assert cloud.n_out_of_range == 4 * 15  # frames 15..29 lie after the time range
    assert cloud.hdr_coverages.tolist() == [0.25, 0.5, 0.9]
    assert cloud.hdr_levels.shape == (3,)
    assert cloud.bandwidth == pytest.approx(0.5)  # a scalar bw_method is the KDE factor


def test_dataclass_is_frozen() -> None:
    cloud = raw_fret_cloud(_two_band_cloud(4, 10), kde=False)
    with pytest.raises((AttributeError, TypeError)):
        cloud.n_samples = 7  # type: ignore[misc]
