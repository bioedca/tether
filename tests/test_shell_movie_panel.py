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


# --- the trace↔movie round-trip (#357, PRD §7.3, §9 M2) ----------------------------------
#
# The two-movie store: rows 0-2 of the molecule list are mov-1's molecules and rows 3-5
# mov-2's, in /molecules order. Both movies' donor spots sit at the same frame pixels, so a
# click or a jump resolved in the wrong movie would land on the wrong molecule.


class _MouseEvent:
    """The napari mouse event a ``mouse_drag`` generator reads; ``type`` changes in place."""

    def __init__(self, position: tuple[float, ...]) -> None:
        self.position = position
        self.type = "mouse_press"


def _click(panel, row_col: np.ndarray) -> None:
    """Press and release on the canvas at ``row_col`` with no move: a click, not a pan."""
    (callback,) = panel._spot_click_callbacks
    event = _MouseEvent((0.0, float(row_col[0]), float(row_col[1])))
    gen = callback(panel.viewer, event)
    next(gen)
    event.type = "mouse_release"
    with pytest.raises(StopIteration):
        next(gen)


def _centre(panel) -> tuple[float, float]:
    return tuple(float(v) for v in panel.viewer.camera.center[-2:])


@needs_napari
@needs_gl
def test_selecting_a_molecule_shows_its_own_movie_centred_on_its_donor_spot(
    section_shell, tmp_path
) -> None:
    path = _store(tmp_path, second=_second_movie(tmp_path))
    shell = _open(section_shell, path)
    panel = shell.movie_panel
    donor1, _ = _molecules(path, "mov-1")
    donor2, _ = _molecules(path, "mov-2")
    assert shell.roundtrip is not None
    assert shell.molecule_list.count() == 6
    # The first selection of the open already centres on molecule 0's donor spot.
    assert panel.active_index == 0
    assert _centre(panel) == tuple(_rowcol(donor1)[0])

    shell.molecule_list.setCurrentRow(4)  # mov-2's second molecule

    assert panel.active_index == 1
    assert panel.movie_layer.data.shape == (5, 40, 48)
    assert _centre(panel) == tuple(_rowcol(donor2)[1])
    # The shell's switcher names the movie the jump showed.
    assert shell.movie_switcher.currentIndex() == 1
    # The selection keeps its own status line; the jump adds none.
    assert shell.status_message == "Molecule mol-4 (5/6)"

    shell.molecule_list.setCurrentRow(2)  # back to mov-1

    assert panel.active_index == 0
    assert _centre(panel) == tuple(_rowcol(donor1)[2])
    assert shell.movie_switcher.currentIndex() == 0


@needs_napari
@needs_gl
@pytest.mark.parametrize("key_name", ["Key_Return", "Key_Enter"])
def test_enter_jumps_back_to_the_molecules_spot_and_writes_nothing(
    section_shell, tmp_path, qtbot, key_name
) -> None:
    import hashlib

    from pyqtgraph.Qt import QtCore

    path = _store(tmp_path, second=_second_movie(tmp_path))
    shell = _open(section_shell, path)
    panel = shell.movie_panel
    donor2, _ = _molecules(path, "mov-2")
    shell.molecule_list.setCurrentRow(5)
    # The user looks at the other movie, and pans away.
    shell.movie_switcher.setCurrentIndex(0)
    panel.center_on(1.0, 1.0)
    before = hashlib.sha256(path.read_bytes()).hexdigest()

    qtbot.keyClick(shell.molecule_list, getattr(QtCore.Qt.Key, key_name))

    assert shell.status_message == "Jumped to mol-5 in movie second_movie_011.tif"
    assert panel.active_index == 1
    assert shell.movie_switcher.currentIndex() == 1
    assert _centre(panel) == tuple(_rowcol(donor2)[2])
    assert shell.molecule_list.currentRow() == 5
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


@needs_napari
@needs_gl
def test_enter_opens_a_closed_movie_dock(section_shell, tmp_path, qtbot) -> None:
    from pyqtgraph.Qt import QtCore

    shell = _open(section_shell, _store(tmp_path))
    dock = shell.movie_dock
    dock.close()
    assert dock.isHidden()

    qtbot.keyClick(shell.molecule_list, QtCore.Qt.Key.Key_Return)

    assert not dock.isHidden()
    assert shell.status_message.startswith("Jumped to mol-0 in movie ")


@needs_napari
@needs_gl
def test_a_click_on_either_spot_selects_its_molecule_in_the_movie_shown(
    section_shell, tmp_path
) -> None:
    path = _store(tmp_path, second=_second_movie(tmp_path))
    shell = _open(section_shell, path)
    panel = shell.movie_panel
    _, acceptor1 = _molecules(path, "mov-1")
    donor2, acceptor2 = _molecules(path, "mov-2")

    # Movie 1: a click on molecule 1's acceptor spot, in the acceptor half of the frame.
    centre = _centre(panel)
    _click(panel, _rowcol(acceptor1 + [32.0, 0.0])[1])
    assert shell.molecule_list.currentRow() == 1
    assert shell.status_message == "Molecule mol-1 (2/6)"
    # Selecting by a click leaves the camera where the user clicked.
    assert _centre(panel) == centre

    # Movie 2: the same donor pixels belong to mov-2's molecules now.
    shell.movie_switcher.setCurrentIndex(1)
    _click(panel, _rowcol(donor2)[1])
    assert shell.molecule_list.currentRow() == 4
    _click(panel, _rowcol(acceptor2 + [24.0, 0.0])[2])
    assert shell.molecule_list.currentRow() == 5
    assert panel.active_index == 1

    # A click on the background, outside every aperture ring, selects nothing.
    _click(panel, np.array([38.0, 46.0]))
    assert shell.molecule_list.currentRow() == 5


@needs_napari
@needs_gl
def test_a_molecule_whose_movie_is_not_shown_cannot_be_jumped_to(
    section_shell, tmp_path, qtbot
) -> None:
    from pyqtgraph.Qt import QtCore

    second = _second_movie(tmp_path)
    path = _store(tmp_path, second=second)
    second.unlink()  # mov-2 is listed (disabled) but not shown
    shell = _open(section_shell, path)
    panel = shell.movie_panel
    centre = _centre(panel)

    shell.molecule_list.setCurrentRow(4)
    assert panel.active_index == 0
    assert _centre(panel) == centre

    qtbot.keyClick(shell.molecule_list, QtCore.Qt.Key.Key_Return)
    assert shell.status_message == (
        "Jump to movie spot unavailable: mol-4 has no spot in a movie shown here"
    )


@needs_napari
@needs_gl
def test_listing_the_molecules_again_keeps_one_click_handler(section_shell, tmp_path) -> None:
    shell = _open(section_shell, _store(tmp_path, second=_second_movie(tmp_path)))
    panel = shell.movie_panel
    n_callbacks = len(panel.viewer.mouse_drag_callbacks)
    first = shell.roundtrip

    shell.set_molecules(list(shell._traces))

    assert shell.roundtrip is not None and shell.roundtrip is not first
    assert len(panel._spot_click_callbacks) == 1
    assert len(panel.viewer.mouse_drag_callbacks) == n_callbacks


@needs_napari
@needs_gl
def test_closing_the_project_drops_the_round_trip(section_shell, tmp_path, qtbot) -> None:
    from pyqtgraph.Qt import QtCore

    shell = _open(section_shell, _store(tmp_path))
    assert shell.roundtrip is not None

    assert shell.close_project()

    assert shell.roundtrip is None
    shell.set_molecules([])
    qtbot.keyClick(shell.molecule_list, QtCore.Qt.Key.Key_Return)
    assert "Jump to movie spot unavailable" in shell.status_message


# --- synchronized scrubbing between the trace dock and the movie panel (#366) ----------


def _drag_cursor(shell, frame: int) -> None:
    """Drag the trace dock's frame cursor to trace frame ``frame`` (what a mouse drag sets)."""
    dock = shell.trace_dock
    dock.set_time_mode("frames")
    dock.cursor_lines[1].setValue(float(frame))


@needs_napari
@needs_gl
def test_the_trace_cursor_and_the_movie_frame_move_together(section_shell, tmp_path) -> None:
    shell = _open(section_shell, _store(tmp_path, second=_second_movie(tmp_path)))
    panel, dock = shell.movie_panel, shell.trace_dock
    assert shell.frame_link is not None and shell.frame_link.linked
    # The open's first selection already shows the cursor frame in the movie.
    assert dock.frame_cursor == panel.current_frame == 0

    _drag_cursor(shell, 7)
    assert panel.current_frame == 7
    panel.viewer.dims.set_current_step(0, 3)  # the user moves napari's time slider
    assert dock.frame_cursor == 3
    panel.viewer.dims.set_current_step(0, 40)  # past the 12-frame trace: its last frame
    assert dock.frame_cursor == 11

    # Another molecule of the same movie keeps the frame.
    _drag_cursor(shell, 6)
    shell.molecule_list.setCurrentRow(1)
    assert dock.frame_cursor == 6 and panel.current_frame == 6
    # A molecule of the 5-frame second movie: its own movie shows, at that movie's last frame.
    shell.molecule_list.setCurrentRow(4)
    assert panel.active_index == 1
    assert panel.current_frame == 4
    panel.viewer.dims.set_current_step(0, 2)
    assert dock.frame_cursor == 2


@needs_napari
@needs_gl
def test_another_movie_on_screen_is_not_moved_by_the_cursor(section_shell, tmp_path) -> None:
    path = _store(tmp_path, second=_second_movie(tmp_path))
    shell = _open(section_shell, path)
    panel, dock = shell.movie_panel, shell.trace_dock
    shell.movie_switcher.setCurrentIndex(1)  # molecule 0 is in mov-1; mov-2 is shown
    panel.viewer.dims.set_current_step(0, 3)

    _drag_cursor(shell, 9)
    assert panel.current_frame == 3
    panel.viewer.dims.set_current_step(0, 1)
    assert dock.frame_cursor == 9

    # A click on molecule 4's spot selects it; the frame the user was looking at stays,
    # and the cursor moves to it.
    donor2, _ = _molecules(path, "mov-2")
    _click(panel, _rowcol(donor2)[1])
    assert shell.molecule_list.currentRow() == 4
    assert panel.current_frame == 1 and dock.frame_cursor == 1
    _drag_cursor(shell, 3)
    assert panel.current_frame == 3


@needs_napari
@needs_gl
def test_back_on_the_molecules_movie_the_cursor_follows_it_again(section_shell, tmp_path) -> None:
    # Away on the other movie, a drag moves only the cursor. Back on the molecule's movie at
    # the same frame number, the cursor gets back in step with the movie (#1368 review).
    shell = _open(section_shell, _store(tmp_path, second=_second_movie(tmp_path)))
    panel, dock = shell.movie_panel, shell.trace_dock
    _drag_cursor(shell, 3)
    shell.movie_switcher.setCurrentIndex(1)
    _drag_cursor(shell, 9)
    assert panel.current_frame == 3

    shell.movie_switcher.setCurrentIndex(0)

    assert panel.current_frame == 3 and dock.frame_cursor == 3
    _drag_cursor(shell, 8)
    assert panel.current_frame == 8


@needs_napari
@needs_gl
def test_a_shorter_trace_of_the_previous_movie_still_jumps(section_shell, tmp_path) -> None:
    # Molecule 4 (mov-2, 5 frames) has three frames, molecule 3 of the same movie twelve.
    # From molecule 3, the user shows mov-1 at frame 40, then selects molecule 4: the jump
    # switches back to mov-2, which tells frame 4 (40 kept, clamped) on the way. That frame
    # must not be mapped onto the new 3-frame trace with the old trace's length, which raised
    # inside the jump and left the switcher on the wrong movie (#1368 review).
    path = _store(tmp_path, second=_second_movie(tmp_path))
    with h5py.File(path, "r+") as handle:
        table = handle["molecules"]["table"][:]
        table["frame_range"][4] = (0, 3)
        handle["molecules"]["table"][:] = table
    shell = _open(section_shell, path)
    panel, dock = shell.movie_panel, shell.trace_dock
    donor2, _ = _molecules(path, "mov-2")
    shell.molecule_list.setCurrentRow(3)
    shell.movie_switcher.setCurrentIndex(0)
    panel.viewer.dims.set_current_step(0, 40)

    shell.molecule_list.setCurrentRow(4)

    assert shell.status_message == "Molecule mol-4 (5/6)"
    assert panel.active_index == 1 and shell.movie_switcher.currentIndex() == 1
    assert _centre(panel) == tuple(_rowcol(donor2)[1])
    assert dock.trace.n_frames == 3
    assert dock.frame_cursor == panel.current_frame == 0


@needs_napari
@needs_gl
def test_the_frame_link_is_opt_out_in_curate_and_not_a_preference(section_shell, tmp_path) -> None:
    from tether.gui.shell import CURATE_SECTION

    path = _store(tmp_path)
    shell = _open(section_shell, path)
    panel = shell.movie_panel
    action = shell._act_link_frames
    curation = [m for m in shell.section_menus(CURATE_SECTION) if m.title() == "&Curation"]
    assert curation and action in curation[0].actions()  # Curate and correct's own menu
    assert action.isCheckable() and action.isChecked() and shell.frame_link_enabled

    action.trigger()  # off
    assert not shell.frame_link_enabled and not shell.frame_link.enabled
    assert shell.status_message == "Trace and movie frames not linked"
    _drag_cursor(shell, 5)
    assert panel.current_frame == 0
    panel.viewer.dims.set_current_step(0, 9)
    assert shell.trace_dock.frame_cursor == 5

    # Off stays off when a project is opened again in this window.
    assert shell.close_project()
    assert shell.load_project(path) is not None, shell.status_message
    assert not shell.frame_link.enabled and not action.isChecked()

    shell.set_frame_link_enabled(True)  # on again: the movie shows the cursor frame
    assert action.isChecked()
    assert shell.movie_panel.current_frame == shell.trace_dock.frame_cursor

    # It is the section's state in this window, not a preference: a new shell starts on.
    assert section_shell(project=False).frame_link_enabled


@needs_napari
@needs_gl
def test_closing_the_project_drops_the_frame_link(section_shell, tmp_path) -> None:
    shell = _open(section_shell, _store(tmp_path))
    assert shell.frame_link is not None
    assert shell.close_project()
    assert shell.frame_link is None
    shell.set_frame_link_enabled(False)  # no panel: only the state changes
    assert not shell.frame_link_enabled


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


def test_a_movie_pair_is_refused_by_name_not_shown_as_one_movie(section_shell, tmp_path) -> None:
    # A project extracted from a movie pair (donor and acceptor in two files, #909) keeps
    # one /movies row whose uri names the donor file. The panel must not show that file
    # as one dual-channel frame with the acceptor spots on it; it says why instead (#1405).
    import tifffile

    from tether.project.extract import extract_movie

    tutorial = Path(__file__).resolve().parents[1] / "docs" / "tutorial" / "data"
    movie = tifffile.imread(tutorial / "tutorial_movie.tif")
    half = movie.shape[2] // 2
    donor, acceptor = tmp_path / "cam1_donor.tif", tmp_path / "cam2_acceptor.tif"
    tifffile.imwrite(donor, np.ascontiguousarray(movie[:, :, :half]))
    tifffile.imwrite(acceptor, np.ascontiguousarray(movie[:, :, half:]))
    path = tmp_path / "pair.tether"
    extract_movie(donor, path, acceptor_movie=acceptor)

    shell = _open(section_shell, path)

    assert shell.movie_panel is None
    switcher = shell.movie_switcher
    assert switcher.count() == 1
    assert switcher.itemText(0) == (
        "cam1_donor.tif (movie pair with cam2_acceptor.tif: "
        "the view of a pair is not available yet)"
    )
    assert not switcher.model().item(0).isEnabled()
    assert shell.status_message.endswith("; 1 of 1 movie(s) not shown"), shell.status_message


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


@needs_napari
@needs_gl
def test_a_filter_that_shows_no_molecule_unlinks_the_frames(section_shell, tmp_path) -> None:
    # A filter that hides every molecule leaves nothing selected and clears the trace dock
    # (#630). The link must drop its target too, so the movie's slider moves no cursor.
    shell = _open(section_shell, _store(tmp_path, second=_second_movie(tmp_path)))
    panel, dock = shell.movie_panel, shell.trace_dock
    assert shell.frame_link is not None and shell.frame_link.linked

    shell.molecule_view.set_filter("accepted")  # no molecule is accepted yet
    assert shell.molecule_list.currentRow() == -1
    assert shell.frame_link.target is None and not shell.frame_link.linked
    panel.viewer.dims.set_current_step(0, 3)  # must not reach the empty dock
    assert dock.frame_cursor is None
    assert panel.current_frame == 3

    # Back to all molecules and a selection: the link follows the new molecule again.
    shell.molecule_view.set_filter("all")
    shell.molecule_list.setCurrentRow(0)
    assert shell.frame_link.linked
    _drag_cursor(shell, 5)
    assert panel.current_frame == 5
