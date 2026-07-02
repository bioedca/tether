# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the nearest-neighbour / aperture-overlap core (M2 S10, PRD §7.3).

Pure NumPy + SciPy, so the whole module runs in the default matrix (no Qt). This
is where the *geometry* of the overlap view is pinned: the NN distance is exact,
overlap is the two-disks-touch condition (centre distance < 2·radius), the search
is confined per movie, and an isolated / empty molecule set degrades cleanly
(``-1`` / ``inf`` / no overlap) rather than raising.
"""

from __future__ import annotations

import numpy as np
import pytest

from tether.analysis.overlap import (
    APERTURE_OVERLAP_FACTOR,
    DEFAULT_APERTURE_RADIUS,
    NeighborReport,
    OverlapInfo,
    neighbor_report,
)


def test_constants() -> None:
    # The geometric overlap condition and the Deep-LASI disk default (PRD §11.2).
    assert APERTURE_OVERLAP_FACTOR == 2.0
    assert DEFAULT_APERTURE_RADIUS == 3.0


# --- nearest-neighbour distance ----------------------------------------------


def test_nn_distance_and_index_are_exact() -> None:
    # mol0-mol1 are 3 px apart; mol2 is far from both.
    coords = np.array([[0.0, 0.0], [3.0, 0.0], [20.0, 20.0]])
    report = neighbor_report(coords, aperture_radius=3.0)

    assert report.n_molecules == 3
    # mol0 and mol1 are each other's nearest neighbour at distance 3.
    assert report.neighbor_of(0) == 1
    assert report.neighbor_of(1) == 0
    assert report.distance_of(0) == pytest.approx(3.0)
    assert report.distance_of(1) == pytest.approx(3.0)
    # mol2's nearest is mol1 at sqrt(17^2 + 20^2).
    assert report.neighbor_of(2) == 1
    assert report.distance_of(2) == pytest.approx(np.hypot(17.0, 20.0))


def test_overlap_flag_is_two_disk_touch_condition() -> None:
    # radius 3 -> apertures overlap when centres are within 2*3 = 6 px.
    coords = np.array([[0.0, 0.0], [3.0, 0.0], [20.0, 20.0]])
    report = neighbor_report(coords, aperture_radius=3.0)
    assert report.overlap_distance == pytest.approx(6.0)
    # 3 px < 6 -> mol0/mol1 overlap; mol2's ~26 px neighbour does not.
    assert report.overlaps_at(0) is True
    assert report.overlaps_at(1) is True
    assert report.overlaps_at(2) is False
    assert report.n_overlapping == 2


def test_overlap_threshold_scales_with_radius() -> None:
    # Same 3 px separation is NOT an overlap for a 1 px aperture (touch < 2 px).
    coords = np.array([[0.0, 0.0], [3.0, 0.0]])
    tight = neighbor_report(coords, aperture_radius=1.0)
    assert tight.overlap_distance == pytest.approx(2.0)
    assert tight.overlaps_at(0) is False
    # ... but is for a 2 px aperture (touch < 4 px).
    loose = neighbor_report(coords, aperture_radius=2.0)
    assert loose.overlaps_at(0) is True


def test_coincident_spots_overlap_at_zero_distance() -> None:
    # Two molecules at the very same centre is a genuine overlap (distance 0), not
    # a self-match skipped by the k=2 query.
    coords = np.array([[5.0, 5.0], [5.0, 5.0]])
    report = neighbor_report(coords, aperture_radius=3.0)
    assert report.distance_of(0) == pytest.approx(0.0)
    assert report.overlaps_at(0) is True
    assert report.neighbor_of(0) == 1


# --- per-movie grouping ------------------------------------------------------


def test_groups_confine_the_search_to_one_movie() -> None:
    # mol0 (movie A) and mol1 (movie B) sit at identical coords but are in different
    # movies, so they must NOT neighbour each other (§5.2). mol0/mol2 share movie A.
    coords = np.array([[0.0, 0.0], [0.0, 0.0], [3.0, 0.0]])
    groups = np.array(["A", "B", "A"])
    report = neighbor_report(coords, aperture_radius=3.0, groups=groups)

    assert report.neighbor_of(0) == 2  # within movie A, not the coincident mol1
    assert report.distance_of(0) == pytest.approx(3.0)
    assert report.neighbor_of(2) == 0
    # mol1 is the only molecule in movie B -> isolated.
    assert report.neighbor_of(1) is None
    assert report.distance_of(1) == np.inf
    assert report.overlaps_at(1) is False


def test_lone_molecule_is_isolated() -> None:
    report = neighbor_report(np.array([[1.0, 2.0]]), aperture_radius=3.0)
    assert report.n_molecules == 1
    assert report.neighbor_of(0) is None
    assert report.distance_of(0) == np.inf
    assert report.overlaps_at(0) is False
    assert report.n_overlapping == 0


def test_empty_input_is_empty_report() -> None:
    report = neighbor_report(np.empty((0, 2)), aperture_radius=3.0)
    assert report.n_molecules == 0
    assert report.nn_index.shape == (0,)
    assert report.nn_distance.shape == (0,)
    assert report.overlaps.shape == (0,)
    # a 1-D empty is normalised to (0, 2) too
    assert neighbor_report([], aperture_radius=3.0).n_molecules == 0


# --- validation --------------------------------------------------------------


def test_rejects_bad_coords() -> None:
    with pytest.raises(ValueError, match=r"\(N, 2\)"):
        neighbor_report(np.zeros((3, 3)), aperture_radius=3.0)
    with pytest.raises(ValueError, match="finite"):
        neighbor_report(np.array([[np.nan, 0.0], [1.0, 1.0]]), aperture_radius=3.0)


def test_rejects_bad_aperture_radius() -> None:
    coords = np.array([[0.0, 0.0], [3.0, 0.0]])
    with pytest.raises(ValueError, match="finite and positive"):
        neighbor_report(coords, aperture_radius=0.0)
    with pytest.raises(ValueError, match="finite and positive"):
        neighbor_report(coords, aperture_radius=-1.0)
    with pytest.raises(ValueError, match="finite and positive"):
        neighbor_report(coords, aperture_radius=np.inf)


def test_rejects_mismatched_groups() -> None:
    coords = np.array([[0.0, 0.0], [3.0, 0.0]])
    with pytest.raises(ValueError, match="groups must be length 2"):
        neighbor_report(coords, aperture_radius=3.0, groups=np.array(["A"]))


# --- report + payload value objects ------------------------------------------


def test_report_is_row_aligned_and_default_radius() -> None:
    coords = np.array([[0.0, 0.0], [3.0, 0.0]])
    report = neighbor_report(coords)  # default aperture radius
    assert isinstance(report, NeighborReport)
    assert report.aperture_radius == DEFAULT_APERTURE_RADIUS
    assert report.overlap_distance == pytest.approx(6.0)
    assert report.nn_index.dtype == np.intp
    assert report.overlaps.dtype == np.bool_


def test_overlap_info_is_qt_free_value_object() -> None:
    patch = np.zeros((21, 21), dtype="float32")
    info = OverlapInfo(
        nn_distance=4.2,
        overlaps=True,
        aperture_radius=3.0,
        patch=patch,
        name="mol-3",
        nn_molecule_key="key-1",
    )
    assert info.nn_distance == 4.2
    assert info.overlaps is True
    assert info.patch is patch
    assert info.name == "mol-3"
    # patch defaults to None (analysis-only project with no cached patches)
    assert OverlapInfo(nn_distance=np.inf, overlaps=False, aperture_radius=3.0).patch is None
