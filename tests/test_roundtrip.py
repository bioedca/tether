# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for trace↔movie round-trip navigation (M2 S4, PRD §7.3 / §5.2).

Three layers, mirroring the module split:

* **Pure resolver** (:class:`~tether.gui.roundtrip.RoundTripIndex`) — needs only
  NumPy + SciPy, so it runs in the default matrix. This is where the §9 M2
  round-trip acceptance is pinned at the data-model level: **select → jump and
  click → trace both resolve across ≥2 movies**, with per-movie isolation.
* **Navigator** (:class:`~tether.gui.roundtrip.RoundTripNavigator`) — Qt-free, so
  it is driven with a lightweight fake panel that records ``set_active_movie`` /
  ``center_on`` calls; the full two-way round-trip is asserted headlessly.
* A **``@pytest.mark.gui``** smoke that wires the resolver + navigator to a **real**
  :class:`~tether.gui.movie_panel.NapariMoviePanel` over ≥2 movies and asserts the
  camera actually moves and a click resolves to the right molecule. It runs headless
  (``QT_QPA_PLATFORM=offscreen``) on Linux (xvfb) + Windows; the Viewer-instantiating
  smoke is skipped on macOS-offscreen (no GL context), matching ``test_movie_panel``.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import numpy as np
import pytest

from tether.gui.movie_panel import MovieOverlay, NapariMoviePanel
from tether.gui.roundtrip import MoleculeSite, RoundTripIndex, RoundTripNavigator
from tether.io.movie import open_movie

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "movie_be_64x64x50.tif"

# Collection-time GUI guards (identical rationale to test_movie_panel): gate before
# pytest resolves ``qtbot`` so a missing Qt binding skips cleanly, and skip the
# Viewer smoke on macOS-offscreen whose Qt gives vispy no GL context (segfaults).
_HAS_NAPARI_QT = all(importlib.util.find_spec(m) is not None for m in ("napari", "qtpy"))
_needs_napari = pytest.mark.skipif(not _HAS_NAPARI_QT, reason="napari/qtpy not installed")
_NO_HEADLESS_GL = sys.platform == "darwin" and os.environ.get("QT_QPA_PLATFORM") == "offscreen"
_needs_gl = pytest.mark.skipif(
    _NO_HEADLESS_GL,
    reason="napari Viewer needs a GL context; macOS offscreen Qt has none (segfaults)",
)


def _sites() -> list[MoleculeSite]:
    """Four molecules across two movies; ``mol2`` shares ``mol0``'s coords (isolation).

    Coordinates are ``[x, y] = [col, row]`` (the ``/molecules`` convention), all
    inside a 64×64 frame so the same list drives the GUI smoke on the fixture.
    """
    return [
        MoleculeSite("m0", donor_xy=[10.0, 20.0], acceptor_xy=[40.0, 20.0]),  # 0
        MoleculeSite("m0", donor_xy=[12.0, 55.0], acceptor_xy=[42.0, 55.0]),  # 1
        MoleculeSite("m1", donor_xy=[10.0, 20.0], acceptor_xy=[40.0, 20.0]),  # 2 (== 0 coords)
        MoleculeSite("m1", donor_xy=[50.0, 50.0], acceptor_xy=[8.0, 50.0]),  # 3
    ]


@pytest.fixture
def index() -> RoundTripIndex:
    """The resolver over :func:`_sites` (shared by the RoundTripIndex tests)."""
    return RoundTripIndex(_sites())


# --- MoleculeSite value object (pure) ----------------------------------------


def test_molecule_site_validates_and_is_readonly() -> None:
    site = MoleculeSite("m0", donor_xy=[10.0, 20.0], acceptor_xy=(40.0, 20.0))
    assert site.movie_id == "m0"
    np.testing.assert_array_equal(site.donor_xy, [10.0, 20.0])
    np.testing.assert_array_equal(site.acceptor_xy, [40.0, 20.0])
    # immutable value object: stored coords are read-only float64 copies
    assert site.donor_xy.dtype == np.float64
    assert not site.donor_xy.flags.writeable
    assert not site.acceptor_xy.flags.writeable


def test_molecule_site_rejects_bad_input() -> None:
    with pytest.raises(ValueError, match="movie_id"):
        MoleculeSite("", donor_xy=[1.0, 2.0], acceptor_xy=[3.0, 4.0])
    with pytest.raises(ValueError, match="donor_xy"):
        MoleculeSite("m0", donor_xy=[1.0, 2.0, 3.0], acceptor_xy=[3.0, 4.0])  # not (2,)
    with pytest.raises(ValueError, match="acceptor_xy"):
        MoleculeSite("m0", donor_xy=[1.0, 2.0], acceptor_xy=[[3.0, 4.0]])  # not (2,)
    with pytest.raises(ValueError, match="finite"):
        MoleculeSite("m0", donor_xy=[np.nan, 2.0], acceptor_xy=[3.0, 4.0])


# --- RoundTripIndex: trace → movie -------------------------------------------


def test_camera_target_returns_movie_and_donor_rowcol(index: RoundTripIndex) -> None:
    assert index.n_molecules == 4
    # camera target = the molecule's own movie + its donor spot, transposed
    # [x, y] = [col, row] -> napari (row, col).
    movie_id, (row, col) = index.camera_target(0)
    assert movie_id == "m0"
    assert (row, col) == (20.0, 10.0)
    movie_id, (row, col) = index.camera_target(3)
    assert movie_id == "m1"
    assert (row, col) == (50.0, 50.0)  # donor [50, 50] -> (row 50, col 50)


def test_camera_target_out_of_range_raises(index: RoundTripIndex) -> None:
    with pytest.raises(IndexError):
        index.camera_target(99)


# --- RoundTripIndex: movie → trace (per-movie KDTree) ------------------------


def test_nearest_molecule_resolves_donor_and_acceptor_clicks(index: RoundTripIndex) -> None:
    # click near mol0's donor spot (row 20, col 10)
    assert index.nearest_molecule("m0", (20.3, 10.2)) == 0
    # click near mol0's acceptor spot (row 20, col 40) -> same molecule
    assert index.nearest_molecule("m0", (19.8, 40.4)) == 0
    # click near mol1's donor spot (row 55, col 12)
    assert index.nearest_molecule("m0", (55.0, 12.0)) == 1


def test_nearest_molecule_is_per_movie_isolated(index: RoundTripIndex) -> None:
    # mol0 (m0) and mol2 (m1) sit at identical coordinates: a click resolves within
    # the queried movie only, never leaking across movies (PRD §5.2 per-movie tree).
    assert index.nearest_molecule("m0", (20.0, 10.0)) == 0
    assert index.nearest_molecule("m1", (20.0, 10.0)) == 2


def test_nearest_molecule_max_distance_and_unknown_movie(index: RoundTripIndex) -> None:
    # a click far from every spot is rejected when a max distance is set
    assert index.nearest_molecule("m0", (0.0, 63.0), max_distance=5.0) is None
    # ... but still resolves to the nearest when no cap is given
    assert index.nearest_molecule("m0", (0.0, 63.0)) in (0, 1)
    # an unknown / molecule-less movie yields nothing rather than raising
    assert index.nearest_molecule("does-not-exist", (20.0, 10.0)) is None


def test_index_movie_ids_are_the_populated_movies(index: RoundTripIndex) -> None:
    assert sorted(index.movie_ids) == ["m0", "m1"]


def test_nearest_molecule_rejects_bad_position(index: RoundTripIndex) -> None:
    # a napari nD position must be reduced to (row, col) at the panel boundary; the
    # resolver rejects a 3-tuple rather than silently querying the wrong axes ...
    with pytest.raises(ValueError, match="row, col"):
        index.nearest_molecule("m0", (0.0, 20.0, 10.0))
    # ... and a non-finite click is rejected too
    with pytest.raises(ValueError, match="finite"):
        index.nearest_molecule("m0", (np.nan, 10.0))


def test_roundtrip_both_directions_across_two_movies(index: RoundTripIndex) -> None:
    """The §9 M2 gate at the resolver level: select→jump and click→trace, ≥2 movies."""
    for mol_index, expected_movie in ((0, "m0"), (2, "m1"), (3, "m1")):
        # select → jump: the molecule resolves to its own movie + spot ...
        movie_id, (row, col) = index.camera_target(mol_index)
        assert movie_id == expected_movie
        # ... and a click at that very spot round-trips back to the molecule.
        assert index.nearest_molecule(movie_id, (row, col)) == mol_index


# --- RoundTripNavigator (Qt-free, fake panel) --------------------------------


class _FakePanel:
    """A minimal :class:`MoviePanelLike` recording the navigator's panel calls."""

    def __init__(self, *, active: int = 0) -> None:
        self._active = active
        self.switched_to: list[int] = []
        self.centered_on: list[tuple[float, float]] = []
        self.click_callback = None
        self.connect_calls = 0

    @property
    def active_index(self) -> int:
        return self._active

    def set_active_movie(self, index: int) -> object:
        self._active = index
        self.switched_to.append(index)
        return None

    def center_on(self, row: float, col: float, *, zoom: float | None = None) -> None:
        self.centered_on.append((row, col))

    def connect_spot_click(self, callback) -> None:
        self.click_callback = callback
        self.connect_calls += 1


def test_navigator_focus_molecule_switches_and_centers() -> None:
    index = RoundTripIndex(_sites())
    panel = _FakePanel(active=0)
    focused: list[int] = []
    nav = RoundTripNavigator(index, panel, movie_ids=["m0", "m1"], on_focus=focused.append)
    # focusing a molecule in m1 (panel index 1) switches + centers on its donor spot
    nav.focus_molecule(2)
    assert panel.switched_to == [1]
    assert panel.centered_on == [(20.0, 10.0)]
    assert focused == [2]


def test_navigator_focus_molecule_skips_switch_when_already_active() -> None:
    index = RoundTripIndex(_sites())
    panel = _FakePanel(active=0)  # already on m0
    nav = RoundTripNavigator(index, panel, movie_ids=["m0", "m1"])
    nav.focus_molecule(0)  # mol0 is in m0
    assert panel.switched_to == []  # no redundant switch
    assert panel.centered_on == [(20.0, 10.0)]


def test_navigator_focus_molecule_unregistered_movie_raises() -> None:
    index = RoundTripIndex(_sites())
    panel = _FakePanel(active=0)
    nav = RoundTripNavigator(index, panel, movie_ids=["m0"])  # m1 not registered
    with pytest.raises(KeyError, match="m1"):
        nav.focus_molecule(2)  # mol2 lives in m1


def test_navigator_handle_spot_click_uses_active_movie() -> None:
    index = RoundTripIndex(_sites())
    selected: list[int] = []
    panel = _FakePanel(active=0)
    nav = RoundTripNavigator(index, panel, movie_ids=["m0", "m1"], on_select=selected.append)
    # identical click position resolves to a different molecule per active movie
    assert nav.handle_spot_click((20.0, 10.0)) == 0
    panel._active = 1
    assert nav.handle_spot_click((20.0, 10.0)) == 2
    assert selected == [0, 2]


def test_navigator_handle_spot_click_miss_does_not_select() -> None:
    index = RoundTripIndex(_sites())
    selected: list[int] = []
    panel = _FakePanel(active=0)
    nav = RoundTripNavigator(
        index,
        panel,
        movie_ids=["m0", "m1"],
        on_select=selected.append,
        max_click_distance=3.0,
    )
    assert nav.handle_spot_click((0.0, 63.0)) is None
    assert selected == []


def test_navigator_connect_wires_panel_callback() -> None:
    index = RoundTripIndex(_sites())
    panel = _FakePanel(active=0)
    nav = RoundTripNavigator(index, panel, movie_ids=["m0", "m1"])
    nav.connect()
    assert panel.click_callback == nav.handle_spot_click


def test_navigator_connect_is_idempotent() -> None:
    # re-binding the navigator must not register a second callback (which would
    # fire on_select twice per click).
    index = RoundTripIndex(_sites())
    panel = _FakePanel(active=0)
    nav = RoundTripNavigator(index, panel, movie_ids=["m0", "m1"])
    nav.connect()
    nav.connect()
    assert panel.connect_calls == 1


def test_navigator_rejects_duplicate_movie_ids() -> None:
    # the one-to-one movie_id -> panel-index contract: a duplicate would misroute
    index = RoundTripIndex(_sites())
    panel = _FakePanel(active=0)
    with pytest.raises(ValueError, match="unique"):
        RoundTripNavigator(index, panel, movie_ids=["m0", "m0"])


# --- GUI smoke: real napari panel, ≥2 movies ---------------------------------


@pytest.mark.gui
@_needs_napari
@_needs_gl
def test_panel_center_on_moves_camera(qtbot) -> None:  # qtbot: ensure a QApplication
    with open_movie(FIXTURE) as reader, NapariMoviePanel() as panel:
        panel.add_movie(reader, name="m0")
        panel.center_on(30.0, 12.0)
        # camera.center is (depth, row, col); the in-plane (row, col) is what we set
        assert tuple(panel.viewer.camera.center[-2:]) == (30.0, 12.0)
        panel.center_on(5.0, 50.0, zoom=3.0)
        assert tuple(panel.viewer.camera.center[-2:]) == (5.0, 50.0)
        assert panel.viewer.camera.zoom == pytest.approx(3.0)


@pytest.mark.gui
@_needs_napari
@_needs_gl
def test_panel_connect_and_disconnect_spot_click(qtbot) -> None:
    received: list[tuple[float, float]] = []
    with open_movie(FIXTURE) as reader, NapariMoviePanel() as panel:
        panel.add_movie(reader, name="m0")
        n_before = len(panel.viewer.mouse_drag_callbacks)
        panel.connect_spot_click(received.append)
        assert len(panel.viewer.mouse_drag_callbacks) == n_before + 1
        panel.disconnect_spot_clicks()
        assert len(panel.viewer.mouse_drag_callbacks) == n_before


@pytest.mark.gui
@_needs_napari
@_needs_gl
def test_roundtrip_navigator_drives_real_panel_two_movies(qtbot) -> None:
    """End-to-end §9 M2 round-trip on a real panel across two registered movies."""
    index = RoundTripIndex(_sites())
    selected: list[int] = []
    focused: list[int] = []
    donor = np.array([[10.0, 20.0], [12.0, 55.0]])
    with open_movie(FIXTURE) as reader, NapariMoviePanel() as panel:
        # register the fixture twice as the two experiment movies m0, m1
        panel.add_movie(reader, overlay=MovieOverlay(donor, np.empty((0, 2))), name="m0")
        panel.add_movie(reader, overlay=MovieOverlay(donor, np.empty((0, 2))), name="m1")
        nav = RoundTripNavigator(
            index,
            panel,
            movie_ids=["m0", "m1"],
            on_select=selected.append,
            on_focus=focused.append,
        )
        nav.connect()

        # trace → movie: focusing mol2 (in m1) switches the active movie + centers
        assert panel.active_index == 0
        nav.focus_molecule(2)
        assert panel.active_index == 1
        assert tuple(panel.viewer.camera.center[-2:]) == (20.0, 10.0)
        assert focused == [2]

        # movie → trace: a click on the active movie (m1) resolves to an m1 molecule
        assert nav.handle_spot_click((20.0, 10.0)) == 2
        assert nav.handle_spot_click((50.0, 50.0)) == 3
        assert selected == [2, 3]
