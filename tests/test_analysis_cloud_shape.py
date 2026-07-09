# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Raw FRET cloud α-shape + k-vs-RMSE elbow (M6 PR-5b, FR-ANALYZE; PRD §7.7).

The two model-free lenses that read the *same* pooled pre-idealization cloud as
PR-5a: the **α-shape** concave support boundary (Delaunay triangles whose
circumradius is bounded by ``alpha``, in axis-normalized coordinates) and the
**k-vs-RMSE elbow** state-count *hint* (k-means over the pooled apparent-E values,
the knee of the within-cluster RMSE(k) curve). All headless (no Qt) → base CI
matrix; the α-shape uses ``scipy.spatial`` and the elbow ``scipy.cluster.vq``.
"""

from __future__ import annotations

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("h5py")

import numpy as np  # noqa: E402

from _analysis_store import MEANS, build_store_with_model  # noqa: E402
from tether.analysis import (  # noqa: E402
    DEFAULT_ALPHA_FACTOR,
    DEFAULT_ELBOW_K_MAX,
    AlphaShape,
    StateNumberElbow,
    alpha_shape,
    k_rmse_elbow,
    population_fret_cloud_alpha_shape,
    population_fret_cloud_state_number_elbow,
)
from tether.idealize import NO_STATE  # noqa: E402


def _grid(x0: float, x1: float, y0: float, y1: float, nx: int, ny: int) -> np.ndarray:
    """A regular ``nx × ny`` grid of ``(x, y)`` points over the given box."""
    xs = np.linspace(x0, x1, nx)
    ys = np.linspace(y0, y1, ny)
    gx, gy = np.meshgrid(xs, ys, indexing="ij")
    return np.column_stack([gx.ravel(), gy.ravel()])


def _bands_1d(
    centres: list[float], n_each: int = 120, sigma: float = 0.01, seed: int = 0
) -> np.ndarray:
    """Pooled apparent-E from tight Gaussian bands at ``centres`` (a clean elbow signal)."""
    rng = np.random.default_rng(seed)
    return np.concatenate([c + sigma * rng.standard_normal(n_each) for c in centres])


# --- alpha_shape: pure core ---------------------------------------------------


def test_alpha_shape_of_a_filled_square() -> None:
    pts = _grid(0.0, 1.0, 0.0, 1.0, 6, 6)
    shape = alpha_shape(pts, alpha=10.0)
    assert isinstance(shape, AlphaShape)
    assert shape.n_points == 36
    assert shape.n_kept == shape.n_triangles  # a huge alpha keeps every triangle
    assert shape.boundary_edges.shape[1:] == (2, 2)  # (edge, endpoint, (x, y))
    # With every triangle kept the α-shape fills the convex hull, so its boundary is
    # exactly the hull perimeter: 4·(6-1) = 20 unit edges around the 6×6 grid. This pins
    # the `edge in exactly one kept triangle` selection to a known-correct value (a
    # `counts >= 1` / `counts == 2` boundary-predicate bug would give a different count).
    assert shape.n_boundary_edges == 20
    boundary_vertices = np.unique(shape.boundary_edges.reshape(-1, 2), axis=0)
    perimeter = pts[
        np.isclose(pts[:, 0], 0.0)
        | np.isclose(pts[:, 0], 1.0)
        | np.isclose(pts[:, 1], 0.0)
        | np.isclose(pts[:, 1], 1.0)
    ]
    assert boundary_vertices.shape[0] == perimeter.shape[0] == 20
    assert np.array_equal(boundary_vertices, np.unique(perimeter, axis=0))


def test_large_alpha_recovers_the_convex_hull_area() -> None:
    from scipy.spatial import ConvexHull

    pts = _grid(0.0, 4.0, 0.0, 1.0, 7, 5)
    shape = alpha_shape(pts, alpha=10.0)
    assert shape is not None
    assert shape.n_kept == shape.n_triangles
    # every triangle kept -> the α-shape fills the whole convex hull.
    assert shape.area == pytest.approx(float(ConvexHull(pts).volume))


def test_boundary_edge_endpoints_are_input_points() -> None:
    pts = _grid(0.0, 1.0, 0.0, 1.0, 5, 5)
    shape = alpha_shape(pts, alpha=10.0)
    assert shape is not None
    flat = shape.boundary_edges.reshape(-1, 2)
    # every boundary vertex is one of the pooled points (no fabricated coordinates).
    assert np.all(np.any(np.all(flat[:, None, :] == pts[None, :, :], axis=2), axis=1))


def _gap_spanning_boundary_edges(shape: AlphaShape, lo: float = 0.35, hi: float = 0.65) -> int:
    """Boundary edges whose two endpoints straddle the empty E-band ``(lo, hi)``."""
    e = shape.boundary_edges
    y0, y1 = e[:, 0, 1], e[:, 1, 1]
    return int(np.count_nonzero(((y0 < lo) & (y1 > hi)) | ((y0 > hi) & (y1 < lo))))


def test_small_alpha_carves_a_concavity_between_two_blobs() -> None:
    # two E-separated blobs with an empty gap (0.30..0.70) between them; the convex hull
    # spans the gap, but a small alpha drops the gap-spanning slivers.
    blob_lo = _grid(0.0, 5.0, 0.15, 0.30, 6, 4)
    blob_hi = _grid(0.0, 5.0, 0.70, 0.85, 6, 4)
    pts = np.vstack([blob_lo, blob_hi])
    full = alpha_shape(pts, alpha=10.0)
    carved = alpha_shape(pts, alpha=0.18)
    assert full is not None and carved is not None
    assert carved.n_kept < full.n_kept
    assert carved.area < full.area  # the empty E-gap is excluded from the support
    # the defining property (not mere shrinkage): the convex full-α boundary bridges the
    # empty E-band, while the carved α-shape's boundary no longer spans it — the concavity
    # is actually carved, so a wrong-triangle-dropping bug that shrinks area but keeps the
    # gap bridged would be caught here.
    assert _gap_spanning_boundary_edges(full) > 0
    assert _gap_spanning_boundary_edges(carved) == 0


def test_auto_alpha_is_positive_and_reproducible() -> None:
    pts = _grid(0.0, 3.0, 0.0, 1.0, 8, 6)
    a = alpha_shape(pts)  # alpha=None -> auto
    b = alpha_shape(pts)
    assert a is not None and b is not None
    assert a.alpha > 0.0
    # auto = DEFAULT_ALPHA_FACTOR × median finite circumradius (normalized coords) > 0.
    assert a.alpha == pytest.approx(b.alpha)
    assert np.array_equal(a.boundary_edges, b.boundary_edges)
    assert a.area == pytest.approx(b.area)
    assert DEFAULT_ALPHA_FACTOR == 2.0


def test_tiny_alpha_keeps_nothing_but_still_returns_a_shape() -> None:
    # an alpha below every triangle's circumradius keeps no triangle: an honest empty
    # support (n_kept == 0, no edges), never None (the triangulation still succeeded).
    shape = alpha_shape(_grid(0.0, 1.0, 0.0, 1.0, 5, 5), alpha=1e-9)
    assert shape is not None
    assert shape.n_kept == 0
    assert shape.n_boundary_edges == 0
    assert shape.area == 0.0


def test_alpha_shape_reproducible_and_frozen() -> None:
    pts = _grid(0.0, 2.0, 0.0, 1.0, 6, 6)
    shape = alpha_shape(pts, alpha=0.5)
    again = alpha_shape(pts, alpha=0.5)
    assert shape is not None and again is not None
    assert np.array_equal(shape.boundary_edges, again.boundary_edges)
    assert shape.area == again.area
    with pytest.raises((AttributeError, TypeError)):
        shape.area = 1.0  # type: ignore[misc]


def test_alpha_shape_fewer_than_three_points_is_none() -> None:
    assert alpha_shape(np.zeros((0, 2))) is None
    assert alpha_shape(np.array([[0.0, 0.0]])) is None
    assert alpha_shape(np.array([[0.0, 0.0], [1.0, 1.0]])) is None


def test_alpha_shape_collinear_is_none() -> None:
    # all-same-time (degenerate x axis), all-same-E (degenerate y axis), and a diagonal
    # line (Qhull cannot triangulate) all yield no 2-D support.
    assert alpha_shape(np.array([[1.0, 0.0], [1.0, 1.0], [1.0, 2.0]])) is None
    assert alpha_shape(np.array([[0.0, 5.0], [1.0, 5.0], [2.0, 5.0]])) is None
    assert alpha_shape(np.array([[0.0, 0.0], [1.0, 1.0], [2.0, 2.0], [3.0, 3.0]])) is None


def test_alpha_shape_non_finite_points_dropped() -> None:
    pts = np.vstack([_grid(0.0, 1.0, 0.0, 1.0, 4, 4), [[np.nan, 0.5], [0.5, np.inf]]])
    shape = alpha_shape(pts, alpha=10.0)
    assert shape is not None
    assert shape.n_points == 16  # the two non-finite rows are excluded


@pytest.mark.parametrize(
    ("points", "alpha", "match"),
    [
        (np.zeros((3, 3)), None, "n, 2"),
        (np.zeros(4), None, "n, 2"),
        (np.zeros((4, 2)), 0.0, "positive"),
        (np.zeros((4, 2)), -1.0, "positive"),
        (np.zeros((4, 2)), float("inf"), "positive"),
    ],
)
def test_alpha_shape_invalid_input_raises(
    points: np.ndarray, alpha: float | None, match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        alpha_shape(points, alpha=alpha)


# --- k_rmse_elbow: pure core --------------------------------------------------


def test_elbow_finds_two_bands() -> None:
    elbow = k_rmse_elbow(_bands_1d([0.2, 0.8]))
    assert isinstance(elbow, StateNumberElbow)
    assert elbow.elbow_k == 2
    assert elbow.k_values.tolist() == list(range(1, DEFAULT_ELBOW_K_MAX + 1))
    # RMSE plummets from k=1 to k=2 (the two bands resolve), then barely moves.
    assert elbow.rmse[0] > 3.0 * elbow.rmse[1]


def test_elbow_finds_three_bands() -> None:
    elbow = k_rmse_elbow(_bands_1d([0.2, 0.5, 0.8]))
    assert elbow.elbow_k == 3


def test_elbow_rmse_is_monotone_non_increasing() -> None:
    elbow = k_rmse_elbow(_bands_1d([0.2, 0.5, 0.8]), k_max=6)
    diffs = np.diff(elbow.rmse)
    assert np.all(diffs <= 1e-9)  # more clusters never raise the within-cluster RMSE


def test_elbow_k1_rmse_is_the_population_spread() -> None:
    values = _bands_1d([0.2, 0.8])
    elbow = k_rmse_elbow(values)
    # k=1 RMSE == RMS distance to the single mean == population std (ddof=0).
    assert elbow.rmse[0] == pytest.approx(float(np.std(values)))


def test_elbow_reproducible_given_seed() -> None:
    values = _bands_1d([0.2, 0.5, 0.8])
    a = k_rmse_elbow(values, seed=7)
    b = k_rmse_elbow(values, seed=7)
    assert np.array_equal(a.rmse, b.rmse)
    assert a.elbow_k == b.elbow_k
    assert a.seed == 7


def test_elbow_kmax_capped_at_distinct_values() -> None:
    # only three distinct E values -> k-means cannot exceed 3 clusters, whatever k_max.
    elbow = k_rmse_elbow(np.array([0.2, 0.2, 0.5, 0.5, 0.8, 0.8]), k_max=8)
    assert elbow.k_values.tolist() == [1, 2, 3]


def test_elbow_identical_values_have_no_elbow() -> None:
    elbow = k_rmse_elbow(np.full(50, 0.4))
    assert elbow.k_values.tolist() == [1]  # one distinct value
    assert elbow.rmse[0] == pytest.approx(0.0, abs=1e-12)  # zero spread (FP noise only)
    assert elbow.elbow_k is None
    assert elbow.n_samples == 50


def test_elbow_needs_three_k_for_an_interior_knee() -> None:
    # a genuine two-band signal, but only k in {1, 2} probed -> no interior point -> None,
    # even though the structure is there (the elbow needs a curve to bend).
    elbow = k_rmse_elbow(_bands_1d([0.2, 0.8]), k_min=1, k_max=2)
    assert elbow.k_values.tolist() == [1, 2]
    assert elbow.elbow_k is None


def test_elbow_drops_non_finite_and_counts_samples() -> None:
    values = np.concatenate([_bands_1d([0.2, 0.8], n_each=30), [np.nan, np.inf, -np.inf]])
    elbow = k_rmse_elbow(values)
    assert elbow.n_samples == 60  # the three non-finite values are dropped


def test_elbow_empty_values() -> None:
    elbow = k_rmse_elbow(np.array([np.nan, np.inf]))
    assert elbow.k_values.shape == (0,)
    assert elbow.rmse.shape == (0,)
    assert elbow.elbow_k is None
    assert elbow.n_samples == 0


def test_elbow_is_frozen() -> None:
    elbow = k_rmse_elbow(_bands_1d([0.2, 0.8], n_each=20))
    with pytest.raises((AttributeError, TypeError)):
        elbow.elbow_k = 5  # type: ignore[misc]


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [({"k_min": 0}, "k_min"), ({"k_min": 3, "k_max": 2}, "k_max")],
)
def test_elbow_invalid_k_raises(kwargs: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        k_rmse_elbow(_bands_1d([0.2, 0.8], n_each=10), **kwargs)


# --- store entry: population_fret_cloud_alpha_shape ---------------------------


def _states(n_molecules: int, n_frames: int) -> np.ndarray:
    """A state matrix cycling every molecule through all three MEANS levels."""
    base = np.arange(n_frames) % MEANS.size
    return np.tile(base, (n_molecules, 1)).astype("int64")


def test_population_alpha_shape_from_store(tmp_path) -> None:
    project, _ = build_store_with_model(tmp_path, _states(4, 30), MEANS)
    shape = population_fret_cloud_alpha_shape(project)
    assert isinstance(shape, AlphaShape)
    assert shape.n_points > 0
    assert shape.n_boundary_edges > 0


def test_population_alpha_shape_excludes_rejected(tmp_path) -> None:
    project, _ = build_store_with_model(
        tmp_path, _states(4, 30), MEANS, rejected=[True, False, False, False]
    )
    default = population_fret_cloud_alpha_shape(project)
    with_rejected = population_fret_cloud_alpha_shape(project, include_rejected=True)
    assert default is not None and with_rejected is not None
    assert default.n_points < with_rejected.n_points  # the rejected molecule's frames add points


def test_population_alpha_shape_in_grid_filter(tmp_path) -> None:
    # a signal_range that excludes the lowest state (0.2) drops those points when
    # in_grid_only (default); keeping them (in_grid_only=False) triangulates more points.
    project, _ = build_store_with_model(tmp_path, _states(4, 30), MEANS)
    in_grid = population_fret_cloud_alpha_shape(project, signal_range=(0.4, 1.0))
    full = population_fret_cloud_alpha_shape(project, signal_range=(0.4, 1.0), in_grid_only=False)
    assert in_grid is not None and full is not None
    assert in_grid.n_points < full.n_points


def test_population_alpha_shape_molecule_key_selection(tmp_path) -> None:
    project, keys = build_store_with_model(tmp_path, _states(4, 30), MEANS)
    shape = population_fret_cloud_alpha_shape(project, molecule_keys=keys[:2])
    both = population_fret_cloud_alpha_shape(project)
    assert shape is not None and both is not None
    assert shape.n_points < both.n_points


def test_population_alpha_shape_degenerate_returns_none(tmp_path) -> None:
    # a single one-state molecule -> apparent E is constant -> a degenerate E axis ->
    # no 2-D support -> None (honest, not a crash).
    states = np.zeros((1, 20), dtype="int64")
    project, _ = build_store_with_model(tmp_path, states, MEANS)
    assert population_fret_cloud_alpha_shape(project) is None


# --- store entry: population_fret_cloud_state_number_elbow --------------------


def test_population_elbow_from_store(tmp_path) -> None:
    project, _ = build_store_with_model(tmp_path, _states(5, 30), MEANS)
    elbow = population_fret_cloud_state_number_elbow(project)
    assert isinstance(elbow, StateNumberElbow)
    # apparent E takes exactly the three MEANS levels -> three distinct clusters probed.
    assert elbow.k_values.tolist() == [1, 2, 3]
    assert elbow.n_samples == 5 * 30


def test_population_elbow_excludes_rejected(tmp_path) -> None:
    project, _ = build_store_with_model(
        tmp_path, _states(4, 30), MEANS, rejected=[True, False, False, False]
    )
    default = population_fret_cloud_state_number_elbow(project)
    with_rejected = population_fret_cloud_state_number_elbow(project, include_rejected=True)
    assert default.n_samples < with_rejected.n_samples


def test_population_elbow_in_grid_filter_drops_out_of_range_band(tmp_path) -> None:
    # narrowing signal_range to exclude the lowest MEANS level (0.2) leaves 2 in-grid
    # bands (in_grid_only, default); keeping all bands (in_grid_only=False) restores 3.
    project, _ = build_store_with_model(tmp_path, _states(4, 30), MEANS)
    in_grid = population_fret_cloud_state_number_elbow(project, signal_range=(0.4, 1.0))
    full = population_fret_cloud_state_number_elbow(
        project, signal_range=(0.4, 1.0), in_grid_only=False
    )
    assert in_grid.k_values.tolist() == [1, 2]  # only 0.55 / 0.85 survive the grid
    assert full.k_values.tolist() == [1, 2, 3]  # 0.2 re-admitted


def test_population_elbow_drops_gap_frames(tmp_path) -> None:
    # molecule 0 idealized only over frames 0..19; 20..29 are NO_STATE -> NaN E -> dropped.
    states = _states(2, 30)
    states[0, 20:] = NO_STATE
    project, _ = build_store_with_model(tmp_path, states, MEANS)
    elbow = population_fret_cloud_state_number_elbow(project)
    assert elbow.n_samples == 20 + 30


def test_population_elbow_missing_selection_is_empty(tmp_path) -> None:
    project, _ = build_store_with_model(tmp_path, _states(2, 20), MEANS)
    elbow = population_fret_cloud_state_number_elbow(project, molecule_keys=["nope"])
    assert elbow.n_samples == 0
    assert elbow.elbow_k is None


def test_population_elbow_bad_quantity_raises(tmp_path) -> None:
    project, _ = build_store_with_model(tmp_path, _states(2, 20), MEANS)
    with pytest.raises(ValueError, match="intensity_quantity"):
        population_fret_cloud_state_number_elbow(project, intensity_quantity="bogus")
