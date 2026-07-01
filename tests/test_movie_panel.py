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
import os
import sys
from pathlib import Path

import numpy as np
import pytest

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
# canvas. macOS CI provides none under ``QT_QPA_PLATFORM=offscreen`` (the cocoa GL
# backend segfaults), so the Viewer-instantiating smokes are skipped on that exact
# combo — they still run headless on Linux (xvfb) and Windows, and the panel is
# live-verified on a real display. A macOS dev with a real display is unaffected.
_NO_HEADLESS_GL = sys.platform == "darwin" and os.environ.get("QT_QPA_PLATFORM") == "offscreen"
_needs_gl = pytest.mark.skipif(
    _NO_HEADLESS_GL,
    reason="napari Viewer needs a GL context; macOS offscreen Qt has none (segfaults)",
)


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

        # set_movie replaces rather than appends
        panel.set_movie(reader)
        assert len(panel.layers) == 1

        # the napari Qt main window is exposed for embedding
        assert panel.qt_window is not None


@pytest.mark.gui
@_needs_napari
@_needs_gl
def test_panel_rejects_non_movie_reader(qtbot) -> None:  # qtbot: ensure a QApplication
    with NapariMoviePanel() as panel, pytest.raises(TypeError, match="MovieReader"):
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

_GREEN = (0.0, 0.667, 0.0, 1.0)  # donor #00aa00
_RED = (0.863, 0.176, 0.176, 1.0)  # acceptor #dc2d2d


def _write_native_movie(path: Path, frames: np.ndarray) -> Path:
    """Write a small **native**-endian TIFF (a second, distinct movie fixture)."""
    tifffile = pytest.importorskip("tifffile")
    tifffile.imwrite(path, np.ascontiguousarray(frames, dtype="<u2"), photometric="minisblack")
    return path


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
        assert np.allclose(panel.donor_layer.border_color[0], _GREEN, atol=5e-3)
        assert np.allclose(panel.acceptor_layer.border_color[0], _RED, atol=5e-3)
        assert np.allclose(panel.donor_aperture_layer.face_color[0], [0.0, 0.0, 0.0, 0.0])
        assert tuple(panel.movie_layer.data.shape) == m0.shape

        # switch to the second movie: overlays + image follow, no layer pile-up
        panel.set_active_movie(1)
        assert panel.active_index == 1
        np.testing.assert_array_equal(panel.donor_layer.data, ov1.donor_xy[:, ::-1])
        np.testing.assert_array_equal(panel.acceptor_layer.data, ov1.acceptor_xy[:, ::-1])
        assert len(panel.donor_layer.data) == 1
        assert len(panel.acceptor_layer.data) == 3
        assert np.allclose(np.asarray(panel.donor_aperture_layer.size), 2 * 4.0)
        assert tuple(panel.movie_layer.data.shape) == m1.shape
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
