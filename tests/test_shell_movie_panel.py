# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The napari movie panel embedded in the shell, driven from ``/movies`` (#356, PRD §7.3).

Every assertion goes **through the shell**: open a ``.tether`` whose ``/movies`` rows record
real movie files, then read the Curate and correct dock, the movie switcher and the panel's
layers. A test that builds a napari ``Viewer`` carries ``needs_gl``: it runs headless on Linux
(xvfb) and Windows and is skipped on macOS offscreen, which has no GL context. The tests of a
panel that is *not* built (no movie file, an analysis-only project, a platform without GL, a
failed build) run everywhere. No pixel is asserted: the offscreen canvas is 0×0.
"""

from __future__ import annotations

import dataclasses
import importlib.util
from pathlib import Path

import numpy as np
import pytest

from _gui_harness import MOVIE_FIXTURE, needs_gl, needs_qt

h5py = pytest.importorskip("h5py")

_HAS_NAPARI = importlib.util.find_spec("napari") is not None
needs_napari = pytest.mark.skipif(not _HAS_NAPARI, reason="napari not installed")

pytestmark = [pytest.mark.gui, needs_qt]

#: The radius written into /settings/extraction, unlike the 3 px default, so a panel that drew
#: the default instead of the stored value would fail.
_STORED_RADIUS = 2.5

#: Movie 1's channel crops (``[y1, x1, y2, x2]``, 1-based inclusive): the two halves of the
#: 64×64 fixture, so every stored spot lands inside that frame.
_MOVIE_1_DONOR_CROP = (1, 1, 64, 32)
_MOVIE_1_ACCEPTOR_CROP = (1, 33, 64, 64)


def _channels(seed: int, n: int = 3, t: int = 12) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    return rng.uniform(400.0, 800.0, size=(n, t)), rng.uniform(200.0, 600.0, size=(n, t))


def _second_movie(tmp_path: Path) -> Path:
    """A native-order 5×40×48 TIFF, a movie of another size than the 64×64 fixture."""
    import tifffile

    path = tmp_path / "second_movie_011.tif"
    data = (np.arange(5 * 40 * 48) % 997).astype(np.uint16).reshape(5, 40, 48)
    tifffile.imwrite(path, data)
    return path


def _store(tmp_path: Path, *, uri: str = str(MOVIE_FIXTURE), second: Path | None = None) -> Path:
    """A round-trip ``.tether`` whose movie file is ``uri``; ``second`` appends a second movie.

    Movie 1 is the 64×64 fixture split side by side, so its crops are rewritten to fit that
    frame: donor ``(1, 1, 64, 32)`` and acceptor ``(1, 33, 64, 64)`` (#1293).
    """
    from _analysis_store import PARSED, _mols_traces_movie, build_store_with_channels, reg_map
    from tether.imaging.extract import write_extraction
    from tether.imaging.split import ChannelGeometry

    donor, acceptor = _channels(356)
    project, _keys = build_store_with_channels(tmp_path, donor, acceptor, name="m.tether", uri=uri)
    path = Path(project.path)
    if second is not None:
        mols, traces, movie = _mols_traces_movie(*_channels(357), uri=str(second))
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
            path,
            movie=movie,
            molecules=mols,
            traces=traces,
            parsed=PARSED,
            registration_map=reg_map(),
        )
    with h5py.File(path, "r+") as handle:
        table = handle["movies"]["table"][:]
        table["donor_crop"][0] = _MOVIE_1_DONOR_CROP
        table["acceptor_crop"][0] = _MOVIE_1_ACCEPTOR_CROP
        handle["movies"]["table"][:] = table
        handle["/settings/extraction"].attrs["disk_radius"] = _STORED_RADIUS
    return path


def _molecules(path: Path, movie_id: str) -> tuple[np.ndarray, np.ndarray]:
    from tether.imaging.extract import read_molecules

    mols = read_molecules(path)
    ids = np.array([m.decode() if isinstance(m, bytes) else str(m) for m in mols["movie_id"]])
    mine = ids == movie_id
    return np.asarray(mols["donor_xy"])[mine], np.asarray(mols["acceptor_xy"])[mine]


def _rowcol(xy: np.ndarray) -> np.ndarray:
    return np.asarray(xy, dtype=np.float64)[:, ::-1]


def _assert_inside(points: np.ndarray, frame_shape: tuple[int, int]) -> None:
    """Every ``[row, col]`` of ``points`` is a position on a ``frame_shape`` frame."""
    points = np.asarray(points, dtype=np.float64)
    assert len(points)
    assert (points >= 0).all()
    assert (points < np.asarray(frame_shape, dtype=np.float64)).all()


def _open(section_shell, path: Path):
    shell = section_shell(project=False)
    assert shell.load_project(path) is not None, shell.status_message
    return shell


# --- the panel is docked in Curate and correct ------------------------------------------


@needs_napari
@needs_gl
def test_opening_a_project_docks_the_movie_panel_in_curate(section_shell, tmp_path) -> None:
    from pyqtgraph.Qt import QtCore

    from tether.gui.movie_panel import NapariMoviePanel
    from tether.gui.shell import CURATE_SECTION

    shell = _open(section_shell, _store(tmp_path))

    assert shell.status_message == "Opened m.tether — 3 molecule(s)"
    panel = shell.movie_panel
    assert isinstance(panel, NapariMoviePanel)
    dock = shell.movie_dock
    assert dock is not None
    assert dock.widget() is panel.qt_window
    assert dock in shell.section_docks(CURATE_SECTION)
    assert shell.window.dockWidgetArea(dock) == QtCore.Qt.DockWidgetArea.RightDockWidgetArea
    assert not dock.isHidden()
    # napari's own menu bar is hidden, so its Ctrl+W / Ctrl+Q cannot shadow the File menu.
    assert panel.qt_window.menuBar().isHidden()
    # The dock leaves with its section and comes back with it.
    shell.set_section("analyze")
    assert dock.isHidden()
    shell.set_section(CURATE_SECTION)
    assert not dock.isHidden()


@needs_napari
@needs_gl
def test_the_curation_keys_act_in_a_floating_movie_dock(section_shell, tmp_path) -> None:
    from pyqtgraph.Qt import QtCore, QtGui

    shell = _open(section_shell, _store(tmp_path))
    dock = shell.movie_dock
    space = QtGui.QKeyEvent(
        QtCore.QEvent.Type.KeyPress, QtCore.Qt.Key.Key_Space, QtCore.Qt.KeyboardModifier.NoModifier
    )
    # Float it, decide one key, and dock it again before the event loop runs: a floating
    # napari canvas cannot draw under xvfb, and the decision is synchronous.
    dock.setFloating(True)
    try:
        assert dock.window() is dock  # floated: its own top-level window
        consumed = shell._event_filter.filter_event(dock, space, focus_widget=dock)
    finally:
        dock.setFloating(False)
    assert consumed
    # Accepted, and the selection moved on (#631).
    assert shell.status_message.startswith("Accepted ")
    assert shell.status_message.endswith("; Molecule mol-1 (2/3)")


@needs_napari
@needs_gl
def test_the_switcher_lists_the_movies_table_and_the_shell_owns_it(section_shell, tmp_path) -> None:
    from pyqtgraph.Qt import QtCore

    second = _second_movie(tmp_path)
    shell = _open(section_shell, _store(tmp_path, second=second))

    switcher = shell.movie_switcher
    assert switcher.isEnabled()
    assert [switcher.itemText(i) for i in range(switcher.count())] == [
        MOVIE_FIXTURE.name,
        second.name,
    ]
    assert [switcher.itemData(i) for i in range(switcher.count())] == ["mov-1", "mov-2"]
    assert switcher.itemData(1, QtCore.Qt.ItemDataRole.ToolTipRole) == str(second)
    assert switcher.currentIndex() == 0
    panel = shell.movie_panel
    assert panel.movie_names == ["mov-1", "mov-2"]
    # The shell's switcher drives the panel; the panel's own switcher is never built.
    assert panel._switcher is None


@needs_napari
@needs_gl
def test_overlays_show_each_movies_spots_at_the_stored_radius(section_shell, tmp_path) -> None:
    path = _store(tmp_path, second=_second_movie(tmp_path))
    shell = _open(section_shell, path)
    panel = shell.movie_panel

    donor, acceptor = _molecules(path, "mov-1")
    assert panel.active_overlay.aperture_radius == _STORED_RADIUS
    np.testing.assert_allclose(panel.donor_layer.data, _rowcol(donor))
    # The acceptor crop of movie 1 starts at column 33, so the spots sit 32 px right of the
    # stored acceptor sub-image coordinates: in the acceptor half of the frame.
    np.testing.assert_allclose(panel.acceptor_layer.data, _rowcol(acceptor + [32.0, 0.0]))
    np.testing.assert_allclose(panel.donor_aperture_layer.size, 2 * _STORED_RADIUS)
    _assert_inside(panel.donor_layer.data, (64, 64))
    _assert_inside(panel.acceptor_layer.data, (64, 64))

    shell.movie_switcher.setCurrentIndex(1)
    donor2, acceptor2 = _molecules(path, "mov-2")
    np.testing.assert_allclose(panel.donor_layer.data, _rowcol(donor2))
    np.testing.assert_allclose(panel.acceptor_layer.data, _rowcol(acceptor2 + [24.0, 0.0]))
    np.testing.assert_allclose(panel.acceptor_aperture_layer.size, 2 * _STORED_RADIUS)
    _assert_inside(panel.donor_layer.data, (40, 48))
    _assert_inside(panel.acceptor_layer.data, (40, 48))


@needs_napari
@needs_gl
def test_a_movie_with_no_channel_geometry_is_shown_without_spots(section_shell, tmp_path) -> None:
    # A Deep-LASI reconstruction records no channel geometry, so nothing says where its
    # sub-image coordinates sit in the frame: no spot rather than a spot on the wrong half.
    path = _store(tmp_path)
    with h5py.File(path, "r+") as handle:
        table = handle["movies"]["table"][:]
        table["donor_crop"][0] = 0
        table["acceptor_crop"][0] = 0
        handle["movies"]["table"][:] = table
    shell = _open(section_shell, path)
    panel = shell.movie_panel

    switcher = shell.movie_switcher
    assert switcher.itemText(0) == f"{MOVIE_FIXTURE.name} (spot positions not recorded)"
    assert switcher.model().item(0).isEnabled()
    assert panel.movie_layer.data.shape == (50, 64, 64)
    assert panel.active_overlay is None
    assert panel.donor_layer is None
    assert panel.acceptor_layer is None


@needs_napari
@needs_gl
def test_a_movie_switch_replaces_the_image_without_piling_up_layers(
    section_shell, tmp_path
) -> None:
    shell = _open(section_shell, _store(tmp_path, second=_second_movie(tmp_path)))
    panel = shell.movie_panel
    names = [layer.name for layer in panel.layers]
    assert len(names) == 5
    assert panel.movie_layer.data.shape == (50, 64, 64)

    shell.movie_switcher.setCurrentIndex(1)
    assert panel.active_index == 1
    assert [layer.name for layer in panel.layers] == names
    assert panel.movie_layer.data.shape == (5, 40, 48)
    assert shell.status_message == "Movie second_movie_011.tif"

    shell.movie_switcher.setCurrentIndex(0)
    assert panel.active_index == 0
    assert [layer.name for layer in panel.layers] == names
    assert panel.movie_layer.data.shape == (50, 64, 64)
    # Only the active movie's memory map is shown: one image layer, the movie's own.
    images = [layer for layer in panel.layers if layer.name == "movie"]
    assert len(images) == 1


@needs_napari
@needs_gl
def test_a_first_movie_the_panel_cannot_show_leaves_the_next_one_shown_and_selected(
    section_shell, tmp_path, monkeypatch
) -> None:
    # The switcher names the movie the viewer shows, not a movie that failed (#356 review).
    from tether.gui.movie_panel import NapariMoviePanel

    real_render = NapariMoviePanel._render_overlay
    calls: list[object] = []

    def _fails_once(self, overlay) -> None:
        calls.append(overlay)
        if len(calls) == 1:
            raise ValueError("cannot draw the spots")
        real_render(self, overlay)

    monkeypatch.setattr(NapariMoviePanel, "_render_overlay", _fails_once)
    shell = _open(section_shell, _store(tmp_path, second=_second_movie(tmp_path)))

    panel = shell.movie_panel
    switcher = shell.movie_switcher
    assert panel.n_movies == 1
    assert panel.active_index == 0
    assert panel.movie_layer.data.shape == (5, 40, 48)
    assert switcher.currentIndex() == 1
    assert switcher.itemText(0) == f"{MOVIE_FIXTURE.name} (cannot be opened)"
    assert not switcher.model().item(0).isEnabled()
    assert shell.status_message.endswith("; 1 of 2 movie(s) not shown")


@needs_napari
@needs_gl
def test_closing_the_project_removes_the_panel_and_its_readers(section_shell, tmp_path) -> None:
    from pyqtgraph.Qt import QtCore

    shell = _open(section_shell, _store(tmp_path))
    dock = shell.movie_dock
    readers = list(shell._movie_readers)
    assert readers

    assert shell.close_project()

    assert shell.movie_panel is None
    assert shell.movie_dock is None
    assert shell.window.dockWidgetArea(dock) == QtCore.Qt.DockWidgetArea.NoDockWidgetArea
    assert all(reader._closed for reader in readers)
    assert shell._movie_readers == []
    assert shell.movie_switcher.count() == 0
    assert not shell.movie_switcher.isEnabled()


@needs_napari
@needs_gl
def test_reopening_builds_one_new_panel(section_shell, tmp_path) -> None:
    path = _store(tmp_path)
    shell = _open(section_shell, path)
    first = shell.movie_panel

    assert shell.load_project(path) is not None, shell.status_message

    assert shell.movie_panel is not None and shell.movie_panel is not first
    movie_docks = [d for d in shell.section_docks("curate") if d.objectName() == "curate-movie"]
    assert movie_docks == [shell.movie_dock]
    assert len(shell._movie_readers) == 1


# --- when no panel is built -------------------------------------------------------------


def test_an_analysis_only_project_leaves_the_panel_unwired(section_shell, tmp_path) -> None:
    from tether.idealize import read_smd
    from tether.project.analysis_import import (
        ANALYSIS_ONLY_TAG,
        import_analysis_only_project,
        read_analysis_only_marker,
    )

    fixtures = Path(__file__).resolve().parent / "fixtures"
    out = tmp_path / "ao.tether"
    import_analysis_only_project(out, source=read_smd(fixtures / "smd_4mol.hdf5"))

    shell = _open(section_shell, out)

    assert shell.movie_panel is None
    assert shell.movie_dock is None
    assert shell.movie_switcher.count() == 0
    assert not shell.movie_switcher.isEnabled()
    marker = read_analysis_only_marker(out)
    assert marker is not None and marker.banner in shell.status_message
    assert ANALYSIS_ONLY_TAG == "round-trip-unavailable"


def test_the_analysis_only_marker_wins_over_a_movie_file(section_shell, tmp_path) -> None:
    # A marker on a store that does record a real movie still keeps the panel unbuilt: the
    # gate is the marker (PRD §7.8), not the absence of /movies.
    path = _store(tmp_path)
    with h5py.File(path, "r+") as handle:
        group = handle["settings"].create_group("analysis_only")
        group.attrs["banner"] = "coordinates and patches absent"

    shell = _open(section_shell, path)

    assert shell.movie_panel is None
    assert shell.movie_switcher.count() == 0
    assert "coordinates and patches absent" in shell.status_message


def test_a_missing_movie_file_is_listed_disabled_and_said(section_shell, tmp_path) -> None:
    path = _store(tmp_path, uri=str(tmp_path / "moved_away.tif"))

    shell = _open(section_shell, path)

    assert shell.movie_panel is None
    switcher = shell.movie_switcher
    assert switcher.count() == 1
    assert switcher.itemText(0) == "moved_away.tif (file missing)"
    assert not switcher.model().item(0).isEnabled()
    assert shell.status_message == "Opened m.tether — 3 molecule(s); 1 of 1 movie(s) not shown"


def test_a_platform_without_gl_opens_the_project_without_a_panel(
    section_shell, tmp_path, monkeypatch
) -> None:
    from pyqtgraph.Qt import QtCore

    from tether.gui import shell as shell_mod

    monkeypatch.setattr(shell_mod, "_movie_panel_unavailable", lambda: "no OpenGL here")
    built: list[object] = []
    monkeypatch.setattr(shell_mod, "_new_movie_panel", lambda: built.append(1))

    shell = _open(section_shell, _store(tmp_path))

    assert built == []
    assert shell.movie_panel is None
    assert shell.movie_switcher.count() == 1
    assert shell.curation_project is not None
    assert shell.status_message.endswith("; movie panel unavailable: no OpenGL here")
    # The row is not offered as if selecting it would show the movie (#356 review).
    assert not shell.movie_switcher.model().item(0).isEnabled()
    assert shell.movie_switcher.itemData(0, QtCore.Qt.ItemDataRole.ToolTipRole) == (
        "movie panel unavailable: no OpenGL here"
    )


def test_a_movie_the_panel_cannot_prepare_is_disabled_and_its_reader_closed(
    section_shell, tmp_path, monkeypatch
) -> None:
    # A float TIFF with NaN in its first frame opens, then fails the panel's contrast step:
    # the open still finishes, on the new project's molecules (#356 review).
    from tether.gui import shell as shell_mod
    from tether.services import movies as movies_mod

    class _Panel:
        closed = False

        def add_movie(self, *_args, **_kwargs) -> int:
            raise ValueError("cannot convert float NaN to integer")

        def close(self) -> None:
            _Panel.closed = True

    opened: list[object] = []
    real_open = movies_mod.open_project_movie

    def _open_and_keep(movie):
        reader = real_open(movie)
        opened.append(reader)
        return reader

    monkeypatch.setattr(shell_mod, "_movie_panel_unavailable", lambda: None)
    monkeypatch.setattr(shell_mod, "_new_movie_panel", _Panel)
    monkeypatch.setattr(movies_mod, "open_project_movie", _open_and_keep)

    shell = _open(section_shell, _store(tmp_path))

    assert shell.status_message == "Opened m.tether — 3 molecule(s); 1 of 1 movie(s) not shown"
    assert shell.molecule_list.count() == 3
    assert shell.movie_panel is None
    assert _Panel.closed
    switcher = shell.movie_switcher
    assert switcher.itemText(0) == f"{MOVIE_FIXTURE.name} (cannot be opened)"
    assert not switcher.model().item(0).isEnabled()
    (reader,) = opened
    assert reader._closed


def test_a_failed_panel_build_is_reported_not_raised(section_shell, tmp_path, monkeypatch) -> None:
    from tether.gui import shell as shell_mod

    def _broken() -> object:
        raise RuntimeError("no GL context")

    monkeypatch.setattr(shell_mod, "_movie_panel_unavailable", lambda: None)
    monkeypatch.setattr(shell_mod, "_new_movie_panel", _broken)

    shell = _open(section_shell, _store(tmp_path))

    assert shell.movie_panel is None
    assert shell.curation_project is not None
    assert shell.status_message.endswith("; movie panel unavailable: no GL context")
    assert not shell.movie_switcher.model().item(0).isEnabled()


@needs_napari
def test_the_macos_offscreen_platform_is_refused_up_front(qapp, monkeypatch) -> None:
    # Building a Viewer there ends the process, which no except can catch, so the shell
    # must refuse before it tries.
    import sys

    from pyqtgraph.Qt import QtGui

    from tether.gui import shell as shell_mod

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(QtGui.QGuiApplication, "platformName", staticmethod(lambda: "offscreen"))
    assert shell_mod._movie_panel_unavailable() == (
        "the offscreen display platform on macOS has no OpenGL"
    )
    monkeypatch.setattr(QtGui.QGuiApplication, "platformName", staticmethod(lambda: "cocoa"))
    assert shell_mod._movie_panel_unavailable() is None
