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

import os
import sys
from pathlib import Path

import numpy as np
import pytest

from tether.gui.movie_panel import (
    _display_array,
    _first_frame_contrast,
    _NativeDisplayArray,
)
from tether.io.movie import open_movie

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "movie_be_64x64x50.tif"
_NATIVE = "<" if sys.byteorder == "little" else ">"

# Instantiating a real napari Viewer needs an OpenGL context for its vispy canvas.
# macOS CI provides none under ``QT_QPA_PLATFORM=offscreen`` (the cocoa GL backend
# segfaults), so the Viewer-instantiating smokes are skipped on that exact combo —
# they still run headless on Linux (xvfb) and Windows, and the panel is
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
@_needs_gl
def test_panel_displays_movie_headless(qtbot) -> None:  # qtbot: ensure a QApplication
    pytest.importorskip("napari")
    pytest.importorskip("qtpy")
    from tether.gui.movie_panel import NapariMoviePanel

    reader = open_movie(FIXTURE)
    panel = NapariMoviePanel()
    try:
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
    finally:
        panel.close()
        reader.close()


@pytest.mark.gui
@_needs_gl
def test_panel_rejects_non_movie_reader(qtbot) -> None:  # qtbot: ensure a QApplication
    pytest.importorskip("napari")
    from tether.gui.movie_panel import NapariMoviePanel

    panel = NapariMoviePanel()
    try:
        with pytest.raises(TypeError, match="MovieReader"):
            panel.set_movie(np.zeros((3, 4, 4), dtype=np.uint16))  # type: ignore[arg-type]
    finally:
        panel.close()
