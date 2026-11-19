# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The successive-frame FRET cloud, E(i) against E(i+1) (#710, ADR-0105; PRD §7.7).

Behaviour of the pure core and the store entry. The agreement with the laboratory script that
the requirements come from is ``tests/test_fret_cloud_reference.py``.
"""

from __future__ import annotations

import pytest

pytest.importorskip("numpy")
pytest.importorskip("h5py")

import numpy as np  # noqa: E402

from _analysis_store import MEANS, build_store_with_model  # noqa: E402
from tether.analysis import (  # noqa: E402
    DEFAULT_SUCCESSIVE_GRID_POINTS,
    DEFAULT_SUCCESSIVE_HDR_COVERAGES,
    SuccessiveFrameCloud,
    population_successive_frame_cloud,
    successive_frame_cloud,
    successive_frame_pairs,
)
from tether.analysis.successive import _lloyd  # noqa: E402
from tether.idealize import NO_STATE  # noqa: E402

# --- the pairs -----------------------------------------------------------------


def test_pairs_are_successive_frames_of_one_molecule() -> None:
    pairs = successive_frame_pairs([np.array([0.1, 0.2, 0.3]), np.array([0.7, 0.8])])
    np.testing.assert_array_equal(pairs.pairs, [[0.1, 0.2], [0.2, 0.3], [0.7, 0.8]])
    np.testing.assert_array_equal(pairs.molecule_index, [0, 0, 1])
    np.testing.assert_array_equal(pairs.frame_index, [0, 1, 0])
    # The last frame of molecule 0 is never paired with the first frame of molecule 1.
    assert not np.any(np.all(pairs.pairs == [0.3, 0.7], axis=1))
    assert pairs.n_pairs == 3
    assert pairs.n_molecules == 2


def test_no_pair_crosses_a_masked_frame() -> None:
    e = np.array([0.1, 0.2, np.nan, 0.4, 0.5, np.inf, 0.7])
    pairs = successive_frame_pairs([e])
    np.testing.assert_array_equal(pairs.pairs, [[0.1, 0.2], [0.4, 0.5]])
    np.testing.assert_array_equal(pairs.frame_index, [0, 3])


def test_short_and_fully_masked_molecules_add_no_pair() -> None:
    pairs = successive_frame_pairs([np.array([0.5]), np.array([]), np.full(4, np.nan)])
    assert pairs.n_pairs == 0
    assert pairs.pairs.shape == (0, 2)
    assert pairs.n_molecules == 0


def test_molecule_index_counts_every_input_even_one_without_pairs() -> None:
    pairs = successive_frame_pairs([np.array([0.5]), np.array([0.2, 0.3])])
    np.testing.assert_array_equal(pairs.molecule_index, [1])


def test_a_flat_array_is_refused() -> None:
    with pytest.raises(ValueError, match="wrap a single molecule"):
        successive_frame_pairs(np.array([0.1, 0.2, 0.3]))


# --- the density and its contour levels ----------------------------------------


def _two_state_molecules(n: int = 40, frames: int = 60, seed: int = 1) -> list[np.ndarray]:
    """Molecules that dwell at E 0.3 or 0.7 and jump between them, with noise 0.03."""
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        level = rng.choice([0.3, 0.7])
        e = np.empty(frames)
        for t in range(frames):
            if rng.random() < 0.05:
                level = 1.0 - level
            e[t] = level + rng.normal(0.0, 0.03)
        out.append(e)
    return out


def test_density_is_the_normalized_product_gaussian_kernel_sum() -> None:
    chunks = [np.array([0.2, 0.25, 0.6]), np.array([0.9, 0.85])]
    cloud = successive_frame_cloud(chunks, grid_points=11, bandwidth=0.1, n_centroids=None)
    grid = np.linspace(0.0, 1.0, 11)
    pts = np.array([[0.2, 0.25], [0.25, 0.6], [0.9, 0.85]])
    raw = np.zeros((11, 11))
    for x, y in pts:
        raw += np.exp(
            -0.5 * ((grid[:, None] - x) / 0.1) ** 2 - 0.5 * ((grid[None, :] - y) / 0.1) ** 2
        )
    expected = raw / (raw.sum() * 0.1 * 0.1)
    np.testing.assert_allclose(cloud.density, expected, rtol=1e-13)
    np.testing.assert_array_equal(cloud.grid, grid)
    assert cloud.density.sum() * cloud.node_area == pytest.approx(1.0, rel=1e-13)


def test_density_first_axis_is_frame_i() -> None:
    cloud = successive_frame_cloud([np.array([0.2, 0.8])], grid_points=101, n_centroids=None)
    i, j = np.unravel_index(np.argmax(cloud.density), cloud.density.shape)
    assert cloud.grid[i] == pytest.approx(0.2)
    assert cloud.grid[j] == pytest.approx(0.8)


def test_density_is_summed_in_blocks_without_changing_it(monkeypatch) -> None:
    chunks = _two_state_molecules(10, 30)
    whole = successive_frame_cloud(chunks, grid_points=21, n_centroids=None)
    monkeypatch.setattr("tether.analysis.successive._BLOCK", 7)
    blocked = successive_frame_cloud(chunks, grid_points=21, n_centroids=None)
    np.testing.assert_allclose(blocked.density, whole.density, rtol=1e-12)


def test_contour_levels_enclose_their_coverage() -> None:
    cloud = successive_frame_cloud(_two_state_molecules(), n_centroids=None)
    assert cloud.hdr_levels is not None
    np.testing.assert_array_equal(cloud.hdr_coverages, DEFAULT_SUCCESSIVE_HDR_COVERAGES)
    assert np.all(np.diff(cloud.hdr_levels) < 0)  # a larger region has a lower threshold
    flat = cloud.density.ravel()
    for p, level in zip(cloud.hdr_coverages, cloud.hdr_levels, strict=True):
        mass = flat[flat >= level].sum() * cloud.node_area
        # The super-level set holds at least p, and less than p plus one cell at that level.
        assert p <= mass < p + level * cloud.node_area + 1e-12


def test_states_sit_on_the_diagonal_and_jumps_off_it() -> None:
    cloud = successive_frame_cloud(_two_state_molecules(), n_centroids=None)
    g = cloud.grid
    at = {v: int(np.argmin(np.abs(g - v))) for v in (0.3, 0.7)}
    on_diagonal = cloud.density[at[0.3], at[0.3]]
    jump = cloud.density[at[0.3], at[0.7]]
    off = cloud.density[at[0.3], int(np.argmin(np.abs(g - 0.05)))]
    assert on_diagonal > jump > off


def test_a_pair_outside_the_range_is_counted_and_kept() -> None:
    inside = [np.array([0.4, 0.5, 0.45])]
    cloud = successive_frame_cloud(inside, n_centroids=None)
    both = successive_frame_cloud([*inside, np.array([1.02, 0.98])], n_centroids=None)
    assert cloud.n_out_of_range == 0
    assert both.n_out_of_range == 1
    assert both.n_pairs == 3
    assert not np.allclose(both.density, cloud.density)  # its kernel reaches the grid


def test_no_pair_gives_no_density_and_no_centroids() -> None:
    cloud = successive_frame_cloud([np.array([0.5])])
    assert cloud.density is None
    assert cloud.hdr_levels is None
    assert cloud.centroids is None
    assert cloud.centroid_sse is None
    assert cloud.n_pairs == 0
    assert cloud.grid.size == DEFAULT_SUCCESSIVE_GRID_POINTS


def test_a_cloud_too_far_from_the_grid_has_no_density() -> None:
    cloud = successive_frame_cloud([np.array([50.0, 50.0])], n_centroids=None)
    assert cloud.density is None
    assert cloud.hdr_levels is None
    assert cloud.n_out_of_range == 1


# --- the centroids --------------------------------------------------------------


def test_centroids_find_separated_clusters_in_sorted_order() -> None:
    rng = np.random.default_rng(3)
    centres = np.array([[0.2, 0.2], [0.2, 0.8], [0.8, 0.2], [0.8, 0.8]])
    chunks = []
    for c in centres:
        pts = c + rng.normal(0.0, 0.01, size=(50, 2))
        chunks.extend(np.array([x, y]) for x, y in pts)  # each pair is its own molecule
    cloud = successive_frame_cloud(chunks)
    assert cloud.n_centroids == 4
    found = cloud.centroids[np.lexsort((cloud.centroids[:, 1], cloud.centroids[:, 0].round(1)))]
    np.testing.assert_allclose(found, centres, atol=0.01)
    assert np.all(np.diff(cloud.centroids[:, 0]) >= 0)  # sorted by E(i)


def test_centroids_are_reproducible_from_the_seed() -> None:
    chunks = _two_state_molecules()
    a = successive_frame_cloud(chunks, n_centroids=3, seed=7)
    b = successive_frame_cloud(chunks, n_centroids=3, seed=7)
    np.testing.assert_array_equal(a.centroids, b.centroids)
    assert a.centroid_sse == b.centroid_sse


def test_centroids_are_a_fixed_point_with_the_reported_sse() -> None:
    chunks = _two_state_molecules()
    cloud = successive_frame_cloud(chunks, n_centroids=4)
    points = cloud.pairs.pairs
    again, sse, iterations = _lloyd(points, cloud.centroids, 100)
    np.testing.assert_allclose(again, cloud.centroids, atol=1e-15)
    assert iterations == 1
    assert sse == pytest.approx(cloud.centroid_sse, rel=1e-12)


def test_more_restarts_never_give_a_worse_fit() -> None:
    chunks = _two_state_molecules()
    one = successive_frame_cloud(chunks, n_centroids=6, kmeans_restarts=1)
    many = successive_frame_cloud(chunks, n_centroids=6, kmeans_restarts=10)
    assert many.centroid_sse <= one.centroid_sse


def test_fewer_distinct_pairs_than_centroids_gives_fewer_centroids() -> None:
    cloud = successive_frame_cloud([np.array([0.3, 0.3, 0.3, 0.6])], n_centroids=4)
    assert cloud.n_centroids == 2
    np.testing.assert_allclose(cloud.centroids, [[0.3, 0.3], [0.3, 0.6]])
    assert cloud.centroid_sse == pytest.approx(0.0)


def test_no_centroids_when_none_are_asked_for() -> None:
    cloud = successive_frame_cloud(_two_state_molecules(), n_centroids=None)
    assert cloud.centroids is None
    assert cloud.n_centroids == 0


def test_an_empty_cluster_takes_the_farthest_point() -> None:
    points = np.array([[0.0, 0.0], [0.1, 0.0], [1.0, 1.0]])
    start = np.array([[0.05, 0.0], [5.0, 5.0], [6.0, 6.0]])  # two centres own no point
    centres, sse, _ = _lloyd(points, start, 100)
    assert sse == pytest.approx(0.0)
    assert sorted(map(tuple, centres.round(12))) == sorted(map(tuple, points))


def test_a_repaired_cluster_does_not_end_the_run_at_a_stale_mean() -> None:
    # Review finding on #1087: the repair of an empty cluster moves a point after the cluster
    # that lost it had its mean computed. If the next assignment then equals the repaired
    # labels, the run must not stop: that cluster's centre is not the mean of its members yet.
    points = np.array(
        [
            [0.6, 0.6], [1.0, 0.3], [0.3, 0.3], [1.0, 0.4], [1.0, 0.7], [1.0, 0.8],
            [0.2, 0.3], [0.2, 0.1], [1.0, 0.0], [0.2, 0.2], [0.5, 0.7], [0.0, 0.7],
        ]
    )  # fmt: skip
    start = np.array([[0.5, 0.7], [1.0, 0.4], [0.6, 0.6], [0.0, 0.7]])
    centres, sse, _ = _lloyd(points, start, 100)
    labels = np.argmin(((points[:, None, :] - centres[None, :, :]) ** 2).sum(axis=2), axis=1)
    for c in range(centres.shape[0]):
        np.testing.assert_allclose(centres[c], points[labels == c].mean(axis=0), atol=1e-15)
    assert sse == pytest.approx(float(((points - centres[labels]) ** 2).sum()), rel=1e-12)


# --- parameters and provenance ----------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"grid_points": 1}, "grid_points"),
        ({"value_range": (1.0, 0.0)}, "value_range"),
        ({"value_range": (0.0, np.inf)}, "value_range"),
        ({"bandwidth": 0.0}, "bandwidth"),
        ({"bandwidth": np.nan}, "bandwidth"),
        ({"hdr_coverages": (0.0, 0.5)}, "hdr_coverages"),
        ({"hdr_coverages": (1.0,)}, "hdr_coverages"),
        ({"n_centroids": 0}, "n_centroids"),
        ({"kmeans_restarts": 0}, "kmeans_restarts"),
        ({"kmeans_max_iter": 0}, "kmeans_max_iter"),
    ],
)
def test_invalid_parameters_raise(kwargs: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        successive_frame_cloud([np.array([0.2, 0.3])], **kwargs)


def test_the_result_carries_its_provenance() -> None:
    cloud = successive_frame_cloud(
        [np.array([0.2, 0.3, 0.4])], grid_points=50, bandwidth=0.08, n_centroids=2, seed=5
    )
    assert isinstance(cloud, SuccessiveFrameCloud)
    prov = cloud.provenance
    assert prov["analysis"] == "successive_frame_cloud"
    assert prov["app_version"]
    assert prov["parameters"] == {
        "grid_points": 50,
        "value_range": [0.0, 1.0],
        "bandwidth": 0.08,
        "hdr_coverages": list(DEFAULT_SUCCESSIVE_HDR_COVERAGES),
        "n_centroids": 2,
        "kmeans_restarts": 5,
        "kmeans_max_iter": 100,
        "seed": 5,
    }
    assert prov["n_pairs"] == 2
    assert prov["n_molecules"] == 1


# --- store entry ----------------------------------------------------------------


def _cycling_states(n_molecules: int, n_frames: int) -> np.ndarray:
    return np.tile(np.arange(n_frames) % MEANS.size, (n_molecules, 1)).astype("int64")


def test_population_cloud_pairs_each_molecule_inside_its_window(tmp_path) -> None:
    states = _cycling_states(3, 20)
    project, _ = build_store_with_model(
        tmp_path, states, MEANS, windows=[(0, 20), (5, 15), (0, 20)]
    )
    cloud = population_successive_frame_cloud(project, n_centroids=None)
    assert cloud.n_molecules == 3
    assert cloud.n_pairs == 19 + 9 + 19
    # apparent E follows the levels, so every pair is two successive MEANS of the cycle.
    expected = {(round(MEANS[s], 6), round(MEANS[(s + 1) % MEANS.size], 6)) for s in range(3)}
    got = {tuple(p) for p in np.round(cloud.pairs.pairs, 6)}
    assert got == expected
    params = cloud.provenance["parameters"]
    assert params["intensity_quantity"] == "corrected"
    assert params["include_rejected"] is False


def test_population_cloud_masks_the_frames_after_a_gap(tmp_path) -> None:
    states = _cycling_states(1, 30)
    states[0, 10:12] = NO_STATE  # D = A = 0 -> apparent E NaN on frames 10 and 11
    project, _ = build_store_with_model(tmp_path, states, MEANS)
    cloud = population_successive_frame_cloud(project, n_centroids=None)
    assert cloud.n_pairs == 9 + 17
    assert not np.any(np.isin(cloud.pairs.frame_index, [9, 10, 11]))


def test_population_cloud_excludes_rejected_by_default(tmp_path) -> None:
    project, keys = build_store_with_model(
        tmp_path, _cycling_states(3, 10), MEANS, rejected=[True, False, False]
    )
    assert population_successive_frame_cloud(project, n_centroids=None).n_molecules == 2
    everyone = population_successive_frame_cloud(project, include_rejected=True, n_centroids=None)
    assert everyone.n_molecules == 3
    one = population_successive_frame_cloud(project, molecule_keys=keys[1:2], n_centroids=None)
    assert one.n_molecules == 1


def test_population_cloud_provenance_names_the_molecules_it_used(tmp_path) -> None:
    # Two selections with the same counts must not share one provenance (CodeRabbit, PR #1087):
    # the record lists the keys that entered, in input order, so keys[molecule_index[r]] is the
    # molecule of pair r.
    project, keys = build_store_with_model(
        tmp_path, _cycling_states(3, 10), MEANS, rejected=[True, False, False]
    )
    first = population_successive_frame_cloud(project, molecule_keys=keys[1:2], n_centroids=None)
    second = population_successive_frame_cloud(project, molecule_keys=keys[2:3], n_centroids=None)
    assert first.n_molecules == second.n_molecules == 1
    assert first.n_pairs == second.n_pairs
    assert first.provenance["parameters"]["molecule_keys"] == [str(keys[1])]
    assert second.provenance["parameters"]["molecule_keys"] == [str(keys[2])]
    everyone = population_successive_frame_cloud(project, n_centroids=None)
    used = everyone.provenance["parameters"]["molecule_keys"]
    assert used == [str(k) for k in keys[1:]]  # the rejected molecule is left out
    assert {used[i] for i in everyone.pairs.molecule_index} == set(used)
