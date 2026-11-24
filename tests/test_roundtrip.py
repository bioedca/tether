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
from pathlib import Path

import numpy as np
import pytest

from _gui_harness import needs_gl as _needs_gl
from tether.gui.movie_panel import MovieOverlay, NapariMoviePanel
from tether.gui.roundtrip import (
    MoleculeSite,
    RoundTripIndex,
    RoundTripNavigator,
    sites_for_molecules,
)
from tether.io.movie import open_movie

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "movie_be_64x64x50.tif"

# Collection-time GUI guards (identical rationale to test_movie_panel): gate before
# pytest resolves ``qtbot`` so a missing Qt binding skips cleanly, and skip the
# Viewer smoke on macOS-offscreen whose Qt gives vispy no GL context (segfaults). The
# GL guard is the one the offscreen GUI harness carries (#344).
_HAS_NAPARI_QT = all(importlib.util.find_spec(m) is not None for m in ("napari", "qtpy"))
_needs_napari = pytest.mark.skipif(not _HAS_NAPARI_QT, reason="napari/qtpy not installed")


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


# --- sites_for_molecules: the shell's molecule list → sites (Qt-free, #357) ----------


class _Spots:
    """A stand-in for :class:`~tether.services.movies.ProjectMovie`'s spot fields."""

    def __init__(self, movie_id: str, keys: list[str], donor: list, acceptor: list) -> None:
        self.movie_id = movie_id
        self.molecule_keys = tuple(keys)
        self.donor_xy = np.asarray(donor, dtype=np.float64).reshape(-1, 2)
        self.acceptor_xy = np.asarray(acceptor, dtype=np.float64).reshape(-1, 2)


def test_sites_for_molecules_maps_each_site_to_its_list_row() -> None:
    movies = [
        _Spots("m0", ["a", "b"], [[1.0, 2.0], [3.0, 4.0]], [[11.0, 2.0], [13.0, 4.0]]),
        _Spots("m1", ["c"], [[5.0, 6.0]], [[15.0, 6.0]]),
    ]
    # "x" has no spot in a shown movie and None has no store row: neither gets a site.
    sites, rows = sites_for_molecules(["c", "x", "a", None, "b"], movies)

    assert rows == [0, 2, 4]
    assert [site.movie_id for site in sites] == ["m1", "m0", "m0"]
    np.testing.assert_array_equal(sites[0].donor_xy, [5.0, 6.0])
    np.testing.assert_array_equal(sites[2].acceptor_xy, [13.0, 4.0])


def test_sites_for_molecules_rejects_keys_out_of_step_with_spots() -> None:
    spots = _Spots("m0", ["a"], [[1.0, 2.0], [3.0, 4.0]], [[1.0, 2.0], [3.0, 4.0]])
    with pytest.raises(ValueError, match="1 molecule key"):
        sites_for_molecules(["a"], [spots])


def _two_movie_project(tmp_path: Path) -> Path:
    """A ``.tether`` with two ``/movies`` rows (the #347 append), three molecules each."""
    import dataclasses

    from _analysis_store import PARSED, _mols_traces_movie, build_store_with_channels, reg_map
    from tether.imaging.extract import write_extraction
    from tether.imaging.split import ChannelGeometry

    rng = np.random.default_rng(357)
    channels = [rng.uniform(200.0, 800.0, size=(3, 12)) for _ in range(4)]
    project, _keys = build_store_with_channels(tmp_path, channels[0], channels[1])
    mols, traces, movie = _mols_traces_movie(channels[2], channels[3])
    movie = dataclasses.replace(
        movie,
        movie_id="mov-2",
        sha256="b" * 64,
        height=40,
        width=48,
        donor_geometry=ChannelGeometry(crop=(1, 1, 40, 24)),
        acceptor_geometry=ChannelGeometry(crop=(1, 25, 40, 48)),
    )
    write_extraction(
        project.path,
        movie=movie,
        molecules=mols,
        traces=traces,
        parsed=PARSED,
        registration_map=reg_map(),
    )
    return Path(project.path)


def test_a_two_movie_project_round_trips_both_ways_without_qt(tmp_path) -> None:
    """The literal §9 M2 criterion on a real store, with no display: ≥ 2 movies, both legs."""
    pytest.importorskip("h5py")
    from tether.imaging.extract import read_molecules
    from tether.services.movies import read_project_movies

    path = _two_movie_project(tmp_path)
    mols = read_molecules(path)
    keys = [k.decode() if isinstance(k, bytes) else str(k) for k in mols["molecule_key"]]
    movies = read_project_movies(path).movies
    assert [m.movie_id for m in movies] == ["mov-1", "mov-2"]
    sites, rows = sites_for_molecules(keys, movies)
    assert rows == list(range(6))

    panel = _FakePanel(active=0)
    selected: list[int] = []
    nav = RoundTripNavigator(
        RoundTripIndex(sites),
        panel,
        [m.movie_id for m in movies],
        on_select=selected.append,
        max_click_distance=2.5,
    )
    nav.connect()
    # Both movies put their donor spots at the same frame pixels; the acceptor crops differ.
    shift = {"mov-1": 64.0, "mov-2": 24.0}
    for row in range(6):
        movie = "mov-1" if row < 3 else "mov-2"
        donor = np.asarray(mols["donor_xy"][row], dtype=np.float64)
        acceptor = np.asarray(mols["acceptor_xy"][row], dtype=np.float64) + [shift[movie], 0.0]
        # trace → movie: the molecule's own movie, centred on its donor spot.
        nav.focus_molecule(row)
        assert panel.active_index == (0 if movie == "mov-1" else 1)
        assert panel.centered_on[-1] == (donor[1], donor[0])
        # movie → trace: a click on either spot, in the movie shown, selects it back.
        panel.click_callback((donor[1], donor[0]))
        panel.click_callback((acceptor[1], acceptor[0]))
        assert selected[-2:] == [row, row]


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


class _MouseEvent:
    """A stand-in for the napari mouse event a drag callback holds.

    napari calls a ``mouse_drag`` generator once with the press event and then
    advances it on every move and on the release; the generator keeps reading the
    same event object, whose ``type`` napari updates in place (it swaps the wrapped
    event). This object models that: the test changes ``type`` between steps.
    """

    def __init__(self, position: tuple[float, ...]) -> None:
        self.position = position
        self.type = "mouse_press"


def _drive_drag(callback, viewer, position, *, moves: int) -> None:
    """Press at ``position``, send ``moves`` mouse_move steps, then release."""
    event = _MouseEvent(position)
    gen = callback(viewer, event)
    next(gen)  # press
    for _ in range(moves):
        event.type = "mouse_move"
        next(gen)
    event.type = "mouse_release"
    with pytest.raises(StopIteration):
        next(gen)


@pytest.mark.gui
@_needs_napari
@_needs_gl
def test_panel_spot_click_fires_on_click_and_not_on_drag(qtbot) -> None:
    # Drive the callback that connect_spot_click registers on the viewer, the way
    # napari does: a press and a release with no move is a click and delivers the
    # (row, col) of the press (the last two components of the napari position); a
    # press, a move and a release is a pan and selects nothing (#745 IDZ-F12).
    received: list[tuple[float, float]] = []
    with open_movie(FIXTURE) as reader, NapariMoviePanel() as panel:
        panel.add_movie(reader, name="m0")
        n_before = len(panel.viewer.mouse_drag_callbacks)
        panel.connect_spot_click(received.append)
        (callback,) = panel.viewer.mouse_drag_callbacks[n_before:]

        _drive_drag(callback, panel.viewer, (0.0, 20.0, 10.0), moves=0)
        assert received == [(20.0, 10.0)]

        _drive_drag(callback, panel.viewer, (0.0, 30.0, 5.0), moves=1)
        _drive_drag(callback, panel.viewer, (0.0, 30.0, 5.0), moves=3)
        assert received == [(20.0, 10.0)]  # panning never selects a molecule

        _drive_drag(callback, panel.viewer, (7.0, 12.0), moves=0)  # a 2-D position
        assert received == [(20.0, 10.0), (7.0, 12.0)]


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
        n_before = len(panel.viewer.mouse_drag_callbacks)
        nav.connect()
        (click_callback,) = panel.viewer.mouse_drag_callbacks[n_before:]

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
        # ...and a real click on the canvas takes the same route: the callback that
        # nav.connect() registered on the viewer selects mol2 (#745 IDZ-F12)
        _drive_drag(click_callback, panel.viewer, (0.0, 20.0, 10.0), moves=0)
        assert selected == [2, 3, 2]
