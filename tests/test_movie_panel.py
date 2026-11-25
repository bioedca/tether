# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the embedded napari movie panel (M0 S8, PRD §4.1 / §7.3).

Two layers:

* **Pure-numpy** checks of the display helpers (``_display_array`` /
  ``_NativeDisplayArray`` / ``_first_frame_contrast``) — these need no Qt because
  ``tether.gui.movie_panel`` imports napari lazily, so they run in the default
  matrix.
* A **``@pytest.mark.gui``** smoke that actually instantiates the panel, displays
  the committed big-endian fixture, and asserts the layer is present with the
  right name / shape / native byte order, then tears down cleanly. It runs
  headless (``QT_QPA_PLATFORM=offscreen``) on Linux (xvfb) and Windows; it is
  skipped on macOS-offscreen, whose Qt provides no GL context for the vispy
  canvas (segfault). Pixel rendering is covered by the live computer-use smoke,
  not asserted here (the offscreen canvas is 0×0).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

from _gui_harness import needs_gl as _needs_gl
from tether.gui.movie_panel import (
    MovieOverlay,
    NapariMoviePanel,
    _display_array,
    _first_frame_contrast,
    _NativeDisplayArray,
    _to_rowcol,
    _validate_xy,
)
from tether.io.movie import open_movie

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "movie_be_64x64x50.tif"
_NATIVE = "<" if sys.byteorder == "little" else ">"

# Gate the GUI smokes at COLLECTION time, before pytest resolves the ``qtbot``
# fixture: a body-level ``importorskip`` runs after fixture setup, so a missing
# Qt binding would error in setup instead of skipping cleanly. ``find_spec`` only
# locates the packages (no import / no Qt cost).
_HAS_NAPARI_QT = all(importlib.util.find_spec(m) is not None for m in ("napari", "qtpy"))
_needs_napari = pytest.mark.skipif(not _HAS_NAPARI_QT, reason="napari/qtpy not installed")

# Instantiating a real napari Viewer also needs an OpenGL context for its vispy
# canvas, which macOS offscreen Qt does not provide (it segfaults). ``_needs_gl`` is the
# guard of the offscreen GUI harness (#344), so every Viewer test skips on the same
# condition: still headless on Linux (xvfb) and Windows, and live-verified on a real
# display.


def _is_native(dtype: np.dtype) -> bool:
    return np.dtype(dtype).byteorder in ("=", "|", _NATIVE)


# --- pure-numpy display helpers (no Qt) --------------------------------------


def test_display_array_wraps_non_native_and_preserves_values() -> None:
    native = np.arange(2 * 3 * 4, dtype=np.uint16).reshape(2, 3, 4)
    big_endian = native.astype(">u2")

    disp = _display_array(big_endian)

    assert isinstance(disp, _NativeDisplayArray)
    assert _is_native(disp.dtype)
    assert disp.shape == (2, 3, 4)
    assert disp.ndim == 3
    assert len(disp) == 2
    # value-preserving (a byte swap, not a reinterpret) on a per-frame slice
    np.testing.assert_array_equal(np.asarray(disp[0]).astype(np.int64), native[0].astype(np.int64))
    # and via the __array__ fallback for the whole stack
    np.testing.assert_array_equal(np.asarray(disp).astype(np.int64), native.astype(np.int64))


def test_display_array_passthrough_for_native_order() -> None:
    native = np.arange(24, dtype=np.uint16).reshape(2, 3, 4)
    # native data is returned unwrapped (zero copy) — no per-frame conversion.
    assert _display_array(native) is native


def test_native_display_array_iterates_frames() -> None:
    native = np.arange(2 * 3 * 4, dtype=np.uint16).reshape(2, 3, 4)
    disp = _NativeDisplayArray(native.astype(">u2"))
    frames = list(disp)
    assert len(frames) == 2
    assert all(_is_native(f.dtype) for f in frames)
    np.testing.assert_array_equal(frames[1].astype(np.int64), native[1].astype(np.int64))


def test_native_display_array_rejects_zero_copy() -> None:
    # the adapter always byte-swaps, so it cannot honour copy=False (NumPy 2.x)
    disp = _NativeDisplayArray(np.arange(24, dtype=np.uint16).reshape(2, 3, 4).astype(">u2"))
    with pytest.raises(ValueError, match="copy=False"):
        np.asarray(disp, copy=False)


def test_first_frame_contrast_samples_first_frame() -> None:
    native = np.arange(2 * 3 * 4, dtype=np.uint16).reshape(2, 3, 4)
    lo, hi = _first_frame_contrast(native.astype(">u2"))
    assert [lo, hi] == [int(native[0].min()), int(native[0].max())]
    assert lo < hi


def test_first_frame_contrast_widens_flat_frame() -> None:
    flat = np.full((2, 3, 4), 7, dtype=">u2")
    # a flat first frame (lo == hi) is widened by one so napari gets a valid range
    assert _first_frame_contrast(flat) == [7, 8]


# --- napari panel smoke (headless) -------------------------------------------


@pytest.mark.gui
@_needs_napari
@_needs_gl
def test_panel_displays_movie_headless(qtbot) -> None:  # qtbot: ensure a QApplication
    # Both the reader and the panel are context managers, so cleanup is robust
    # even if the panel constructor raises after the reader is opened.
    with open_movie(FIXTURE) as reader, NapariMoviePanel() as panel:
        layer = panel.set_movie(reader)

        # the movie is displayed as exactly one image layer
        assert len(panel.layers) == 1
        assert layer is panel.movie_layer
        assert layer.name == "movie"
        assert tuple(layer.data.shape) == reader.shape

        # displayed in native byte order (the reference fixture is big-endian)
        assert reader.byteorder == ">"
        assert _is_native(layer.data.dtype)

        # value-preserving end to end: displayed frame == reader frame
        np.testing.assert_array_equal(
            np.asarray(layer.data[0]).astype(np.int64),
            np.asarray(reader.frame(0)).astype(np.int64),
        )
        # the display range spans the first frame, so a 16-bit movie is not shown
        # saturated against a [0, 1] range (#745 GUI-F15)
        frame0 = np.asarray(reader.frame(0)).astype(np.int64)
        assert frame0.min() < frame0.max()
        assert list(layer.contrast_limits) == [int(frame0.min()), int(frame0.max())]

        # set_movie replaces rather than appends
        panel.set_movie(reader)
        assert len(panel.layers) == 1

        # the napari Qt main window is exposed for embedding
        assert panel.qt_window is not None


@pytest.mark.gui
@_needs_napari
@_needs_gl
def test_panel_displays_an_nd2_movie_frame_by_frame(qtbot, tmp_path: Path) -> None:
    """An ND2 source shows through a lazy view: a frame step reads that frame only (#1388)."""
    from _nd2_synth import write_nd2  # noqa: PLC0415
    from tether.io.adapters import open_movie_source  # noqa: PLC0415
    from tether.io.frames import FrameView  # noqa: PLC0415

    frames = np.random.default_rng(1388).integers(0, 4000, size=(6, 8, 10)).astype("<u2")
    path = write_nd2(tmp_path / "movie.nd2", frames)
    with open_movie_source(path) as source, NapariMoviePanel() as panel:
        reads: list[tuple[int, int]] = []
        real = source.read_frames

        def _spy(start: int, stop: int) -> np.ndarray:
            reads.append((start, stop))
            return real(start, stop)

        source.read_frames = _spy  # type: ignore[method-assign]
        layer = panel.set_movie(source)
        assert isinstance(layer.data, FrameView)
        assert tuple(layer.data.shape) == frames.shape
        assert _is_native(layer.data.dtype)
        assert list(layer.contrast_limits) == [int(frames[0].min()), int(frames[0].max())]

        reads.clear()
        panel._viewer.dims.set_current_step(0, 4)
        qtbot.wait(10)
        assert reads, "the frame step read nothing"
        assert all(stop - start == 1 for start, stop in reads), reads
        assert (4, 5) in reads
        np.testing.assert_array_equal(np.asarray(layer._slice.image.raw), frames[4])


@pytest.mark.gui
@_needs_napari
@_needs_gl
def test_panel_rejects_non_movie_reader(qtbot) -> None:  # qtbot: ensure a QApplication
    with NapariMoviePanel() as panel, pytest.raises(TypeError, match="movie source"):
        panel.set_movie(np.zeros((3, 4, 4), dtype=np.uint16))  # type: ignore[arg-type]


# --- overlay value objects (pure numpy, no Qt) -------------------------------


def test_movie_overlay_validates_normalises_and_is_readonly() -> None:
    ov = MovieOverlay(donor_xy=[[1.0, 2.0], [3.0, 4.0]], acceptor_xy=[[5.0, 6.0]])
    assert ov.donor_xy.shape == (2, 2)
    assert ov.acceptor_xy.shape == (1, 2)
    assert ov.aperture_radius == 3.0  # Deep-LASI PSF disk radius default
    # immutable value object: stored arrays are read-only copies of the input
    assert not ov.donor_xy.flags.writeable
    assert not ov.acceptor_xy.flags.writeable
    # empty channels normalise to a 2-D (0, 2) array (an empty overlay is valid)
    empty = MovieOverlay(donor_xy=[], acceptor_xy=np.empty((0, 2)))
    assert empty.donor_xy.shape == (0, 2)
    assert empty.acceptor_xy.shape == (0, 2)


def test_movie_overlay_rejects_bad_input() -> None:
    with pytest.raises(ValueError, match="donor_xy"):
        MovieOverlay(donor_xy=[[1.0, 2.0, 3.0]], acceptor_xy=[])  # not (N, 2)
    with pytest.raises(ValueError, match="finite"):
        MovieOverlay(donor_xy=[[np.nan, 2.0]], acceptor_xy=[])
    for bad in (0.0, -1.0, np.inf, np.nan):
        with pytest.raises(ValueError, match="aperture_radius"):
            MovieOverlay(donor_xy=[], acceptor_xy=[], aperture_radius=bad)


def test_to_rowcol_transposes_xy_and_handles_empty() -> None:
    # [x, y] = [col, row] -> napari [row, col]
    np.testing.assert_array_equal(
        _to_rowcol(np.array([[1.0, 2.0], [3.0, 4.0]])), [[2.0, 1.0], [4.0, 3.0]]
    )
    assert _to_rowcol(np.empty((0, 2))).shape == (0, 2)
    assert _to_rowcol([]).shape == (0, 2)


def test_validate_xy_empty_and_readonly() -> None:
    out = _validate_xy([[1.0, 2.0]], "donor_xy")
    assert out.shape == (1, 2)
    assert not out.flags.writeable
    # an empty 1-D input normalises to a read-only (0, 2)
    empty = _validate_xy([], "x")
    assert empty.shape == (0, 2)
    assert not empty.flags.writeable
    # a malformed empty (wrong column count) is rejected, not silently normalised
    with pytest.raises(ValueError, match="donor_xy"):
        _validate_xy(np.empty((0, 3)), "donor_xy")


# --- multi-movie overlays + switcher (headless GUI) --------------------------


def _trace_rgba(token: str) -> tuple[float, ...]:
    """The active theme's colour of ``token`` as 0-1 RGBA (the default palette, #636)."""
    from tether.gui import theme

    return tuple(c / 255 for c in theme.rgba(theme.current().trace(token)))


def _donor() -> tuple[float, ...]:
    return _trace_rgba("donor")


def _acceptor() -> tuple[float, ...]:
    return _trace_rgba("acceptor")


def _write_native_movie(path: Path, frames: np.ndarray) -> Path:
    """Write a small **native**-endian TIFF (a second, distinct movie fixture)."""
    tifffile = pytest.importorskip("tifffile")
    tifffile.imwrite(path, np.ascontiguousarray(frames, dtype="<u2"), photometric="minisblack")
    return path


def _assert_acceptor_rings(panel, overlay) -> None:
    """The acceptor aperture layer draws ``overlay``'s acceptor rings (#745 GUI-F15)."""
    layer = panel.acceptor_aperture_layer
    expected = overlay.acceptor_xy[:, ::-1]
    np.testing.assert_array_equal(layer.data, expected)
    assert np.allclose(np.asarray(layer.size), 2 * overlay.aperture_radius)
    for i in range(len(expected)):
        assert np.allclose(layer.border_color[i], _acceptor(), atol=5e-3)
        assert np.allclose(layer.face_color[i], [0.0, 0.0, 0.0, 0.0])


def _assert_first_frame_contrast(panel, reader) -> None:
    """The image display range is the first frame's [min, max] (#745 GUI-F15)."""
    frame0 = np.asarray(reader.frame(0)).astype(np.int64)
    assert frame0.min() < frame0.max()
    assert list(panel.movie_layer.contrast_limits) == [int(frame0.min()), int(frame0.max())]


@pytest.mark.gui
@_needs_napari
@_needs_gl
def test_overlays_present_for_multiple_movies(qtbot, tmp_path) -> None:
    # A second movie with a different shape + intensity range, native-endian (so
    # the display path also exercises the zero-copy branch of _display_array).
    frames = ((np.arange(8 * 48 * 32).reshape(8, 48, 32) % 500) + 100).astype("<u2")
    second = _write_native_movie(tmp_path / "movie2.tif", frames)

    ov0 = MovieOverlay(
        donor_xy=np.array([[10.0, 12.0], [20.0, 22.0]]),
        acceptor_xy=np.array([[40.0, 12.0]]),
        aperture_radius=3.0,
    )
    ov1 = MovieOverlay(
        donor_xy=np.array([[5.0, 6.0]]),
        acceptor_xy=np.array([[25.0, 7.0], [30.0, 8.0], [35.0, 9.0]]),
        aperture_radius=4.0,
    )
    with open_movie(FIXTURE) as m0, open_movie(second) as m1, NapariMoviePanel() as panel:
        assert (
            panel.add_movie(m0, overlay=ov0, name="movie-A"),
            panel.add_movie(m1, overlay=ov1, name="movie-B"),
        ) == (0, 1)
        assert panel.n_movies == 2
        assert panel.movie_names == ["movie-A", "movie-B"]
        assert panel.active_index == 0  # first movie active by default

        # all four overlay layers + the image are present for the first movie
        assert {ly.name for ly in panel.layers} >= {
            "movie",
            "donor",
            "acceptor",
            "donor aperture",
            "acceptor aperture",
        }
        # donor/acceptor centres are the overlay coords, transposed to (row, col)
        np.testing.assert_array_equal(panel.donor_layer.data, ov0.donor_xy[:, ::-1])
        np.testing.assert_array_equal(panel.acceptor_layer.data, ov0.acceptor_xy[:, ::-1])
        # aperture rings sit at the same centres, sized to the PSF-disk diameter
        np.testing.assert_array_equal(panel.donor_aperture_layer.data, ov0.donor_xy[:, ::-1])
        assert np.allclose(np.asarray(panel.donor_aperture_layer.size), 2 * 3.0)
        # familiar smFRET channel colours; the aperture face is transparent
        assert np.allclose(panel.donor_layer.border_color[0], _donor(), atol=5e-3)
        assert np.allclose(panel.acceptor_layer.border_color[0], _acceptor(), atol=5e-3)
        assert np.allclose(panel.donor_aperture_layer.face_color[0], [0.0, 0.0, 0.0, 0.0])
        assert tuple(panel.movie_layer.data.shape) == m0.shape
        # the acceptor rings: at the ACCEPTOR centres, PSF-disk diameter, acceptor
        # colour, transparent face — not a copy of the donor layer (#745 GUI-F15)
        _assert_acceptor_rings(panel, ov0)
        _assert_first_frame_contrast(panel, m0)

        # switch to the second movie: overlays + image follow, no layer pile-up
        panel.set_active_movie(1)
        assert panel.active_index == 1
        np.testing.assert_array_equal(panel.donor_layer.data, ov1.donor_xy[:, ::-1])
        np.testing.assert_array_equal(panel.acceptor_layer.data, ov1.acceptor_xy[:, ::-1])
        assert len(panel.donor_layer.data) == 1
        assert len(panel.acceptor_layer.data) == 3
        assert np.allclose(np.asarray(panel.donor_aperture_layer.size), 2 * 4.0)
        assert tuple(panel.movie_layer.data.shape) == m1.shape
        _assert_acceptor_rings(panel, ov1)
        _assert_first_frame_contrast(panel, m1)
        # exactly one layer of each kind (switch swaps in place, never appends)
        for kind in ("movie", "donor", "acceptor", "donor aperture", "acceptor aperture"):
            assert sum(1 for ly in panel.layers if ly.name == kind) == 1


@pytest.mark.gui
@_needs_napari
@_needs_gl
def test_switcher_changes_active_movie(qtbot, tmp_path) -> None:
    frames = (np.arange(6 * 40 * 24).reshape(6, 40, 24) % 300).astype("<u2")
    second = _write_native_movie(tmp_path / "movie2.tif", frames)

    with open_movie(FIXTURE) as m0, open_movie(second) as m1, NapariMoviePanel() as panel:
        panel.add_movie(m0, name="A")  # plain movies (no overlay): a single image layer
        panel.add_movie(m1, name="B")
        assert {ly.name for ly in panel.layers} == {"movie"}

        combo = panel.switcher
        assert [combo.itemText(i) for i in range(combo.count())] == ["A", "B"]
        assert combo.currentIndex() == 0  # tracks the active movie

        # driving the switcher changes the active movie + the displayed image
        combo.setCurrentIndex(1)
        assert panel.active_index == 1
        assert tuple(panel.movie_layer.data.shape) == m1.shape

        # a programmatic switch syncs the combo back
        panel.set_active_movie(0)
        assert combo.currentIndex() == 0
        assert tuple(panel.movie_layer.data.shape) == m0.shape


@pytest.mark.gui
@_needs_napari
@_needs_gl
def test_a_movie_that_cannot_be_shown_leaves_no_trace(qtbot, tmp_path, monkeypatch) -> None:
    # A first movie whose display fails is neither registered nor active, so the next one
    # added becomes the active movie and the image shown is that movie's (#356 review).
    frames = (np.arange(6 * 40 * 24).reshape(6, 40, 24) % 300).astype("<u2")
    second = _write_native_movie(tmp_path / "movie2.tif", frames)

    with open_movie(FIXTURE) as m0, open_movie(second) as m1, NapariMoviePanel() as panel:
        combo = panel.switcher
        real_render = panel._render_overlay
        calls: list[object] = []

        def _fails_once(overlay) -> None:
            calls.append(overlay)
            if len(calls) == 1:
                raise ValueError("cannot draw the spots")
            real_render(overlay)

        monkeypatch.setattr(panel, "_render_overlay", _fails_once)
        with pytest.raises(ValueError, match="cannot draw the spots"):
            panel.add_movie(m0, name="A")
        assert panel.n_movies == 0
        assert panel.active_index == -1
        assert combo.count() == 0
        # Nothing of the failed movie stays in the viewer: not its image, not its spots.
        assert len(panel.layers) == 0

        assert panel.add_movie(m1, name="B") == 0
        assert panel.active_index == 0
        assert panel.movie_names == ["B"]
        assert tuple(panel.movie_layer.data.shape) == m1.shape


@pytest.mark.gui
@_needs_napari
@_needs_gl
def test_set_movie_stays_single_image_layer(qtbot) -> None:
    # The M0 single-movie contract survives the multi-movie refactor: set_movie
    # shows exactly one image layer and no overlay layers.
    with open_movie(FIXTURE) as reader, NapariMoviePanel() as panel:
        layer = panel.set_movie(reader)
        assert layer is panel.movie_layer
        assert panel.n_movies == 1
        assert panel.active_index == 0
        assert [ly.name for ly in panel.layers] == ["movie"]
        assert panel.donor_layer is None


@pytest.mark.gui
@_needs_napari
@_needs_gl
def test_switching_to_plain_movie_blanks_overlays(qtbot, tmp_path) -> None:
    # Once a movie has carried an overlay, switching to a plain (overlay=None)
    # movie blanks the reusable Points layers to empty rather than removing them.
    frames = (np.arange(6 * 40 * 24).reshape(6, 40, 24) % 300).astype("<u2")
    second = _write_native_movie(tmp_path / "movie2.tif", frames)
    ov = MovieOverlay(donor_xy=np.array([[10.0, 12.0]]), acceptor_xy=np.array([[30.0, 14.0]]))

    with open_movie(FIXTURE) as m0, open_movie(second) as m1, NapariMoviePanel() as panel:
        panel.add_movie(m0, overlay=ov, name="A")
        panel.add_movie(m1, name="B")  # plain: no overlay
        # overlay layers exist and are populated for the first (overlay) movie
        assert len(panel.donor_layer.data) == 1
        assert len(panel.acceptor_layer.data) == 1

        panel.set_active_movie(1)
        # layers are kept (reused set stays stable) but blanked to empty (0, 2)
        assert panel.donor_layer is not None
        assert len(panel.donor_layer.data) == 0
        assert len(panel.acceptor_layer.data) == 0
        assert len(panel.donor_aperture_layer.data) == 0
        assert len(panel.acceptor_aperture_layer.data) == 0
        # switching back restores the overlay
        panel.set_active_movie(0)
        assert len(panel.donor_layer.data) == 1


# --- the frame half of synchronized scrubbing (#366) ---------------------------------


@pytest.mark.gui
@_needs_napari
@_needs_gl
def test_set_frame_moves_the_time_slider_and_tells_the_callbacks(qtbot) -> None:
    told: list[int] = []
    with open_movie(FIXTURE) as reader, NapariMoviePanel() as panel:
        assert (panel.current_frame, panel.n_frames) == (-1, 0)
        with pytest.raises(RuntimeError, match="no movie is shown"):
            panel.set_frame(0)
        panel.add_movie(reader, name="m0")
        assert panel.n_frames == 50
        panel.connect_frame_changed(told.append)

        panel.set_frame(17)
        assert panel.current_frame == 17
        assert panel.viewer.dims.current_step[0] == 17
        # The user moves napari's own slider: the callbacks hear that too.
        panel.viewer.dims.set_current_step(0, 30)
        assert told == [17, 30]
        # The same frame again is not a change.
        panel.set_frame(30)
        assert told == [17, 30]

        for bad in (-1, 50):
            with pytest.raises(IndexError, match="out of range"):
                panel.set_frame(bad)
        with pytest.raises(TypeError, match="integer"):
            panel.set_frame(2.0)  # type: ignore[arg-type]

        panel.disconnect_frame_changed()
        panel.set_frame(3)
        assert told == [17, 30]


@pytest.mark.gui
@_needs_napari
@_needs_gl
def test_a_movie_switch_keeps_the_frame_and_tells_it_once(qtbot, tmp_path) -> None:
    # The second movie has 8 frames, the fixture 50: a switch keeps the frame shown,
    # moved to the shorter movie's last frame. napari resets its slider on the layer swap,
    # and the callbacks must not hear those passing frames, only the one the switch ends on.
    frames = ((np.arange(8 * 48 * 32).reshape(8, 48, 32) % 500) + 100).astype("<u2")
    second = _write_native_movie(tmp_path / "movie2.tif", frames)
    told: list[int] = []
    with open_movie(FIXTURE) as m0, open_movie(second) as m1, NapariMoviePanel() as panel:
        panel.add_movie(m0, name="A")
        panel.add_movie(m1, name="B")
        panel.set_frame(5)
        panel.connect_frame_changed(told.append)

        panel.set_active_movie(1)
        assert (panel.n_frames, panel.current_frame) == (8, 5)
        # Frame 5 of another movie is another frame shown: told once, at the switch's end.
        assert told == [5]
        panel.set_active_movie(1)  # the movie already shown: nothing new
        assert told == [5]

        panel.set_active_movie(0)
        panel.set_frame(40)
        told.clear()
        panel.set_active_movie(1)
        assert panel.current_frame == 7
        assert told == [7]

        panel.clear_movies()
        assert (panel.current_frame, panel.n_frames) == (-1, 0)
        assert told == [7]
