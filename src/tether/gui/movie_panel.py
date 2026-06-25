# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Embedded napari movie panel — the M0 seed of FR-ROUNDTRIP (PRD §4.1, §7.3).

:class:`NapariMoviePanel` is a thin wrapper around a single :class:`napari.Viewer`
that displays a lazy TIRF movie (:class:`tether.io.movie.MovieReader`) as a 2-D
image layer. At M0 the panel only needs to **instantiate headlessly and show the
movie**; the donor/acceptor points + aperture overlays, the multi-movie switcher,
and docking into the Tether PySide6 shell land at M2 (PRD §7.3). The panel
already exposes the napari Qt window (:attr:`qt_window`) so a future Tether shell
can embed/show it.

**Byte order.** The reference acquisitions are big-endian ``>u2`` (PRD Appendix A);
:class:`MovieReader` preserves that on-disk order so the lazy ``memmap`` stays
zero-copy. GL textures, however, interpret 16-bit pixel bytes in the host's
**native** order, so uploading big-endian bytes would render garbled. The panel
therefore converts to native order **at the display boundary**, and does so
**lazily, one frame at a time** (:class:`_NativeDisplayArray`): napari slices a
single frame per redraw, so only that frame is byte-swapped and the ≈0.9 GB stack
never materializes. Movies already in native order are passed through untouched
(zero copy). Explicit ``contrast_limits`` (sampled from the first frame) keep
napari from scanning the whole array to auto-range it.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import numpy as np

from tether.io.movie import MovieReader

if TYPE_CHECKING:
    from collections.abc import Iterator

    import napari

__all__ = ["NapariMoviePanel"]

_MOVIE_LAYER_NAME = "movie"


class _NativeDisplayArray:
    """Lazy native-byte-order adapter over a movie array for GL display.

    Presents the same ``(n_frames, H, W)`` shape as the wrapped array but reports
    a **native**-order dtype; indexing returns the touched slice converted to
    native order. Used only when the source is non-native (big-endian); native
    sources are passed through directly. Supports the array-like protocol napari
    needs (``shape``/``dtype``/``ndim``/``__getitem__``), the same way napari
    accepts dask/zarr lazy arrays.
    """

    def __init__(self, base: np.ndarray) -> None:
        self._base = base
        self.shape: tuple[int, ...] = tuple(base.shape)
        self.ndim: int = base.ndim
        self.dtype: np.dtype = base.dtype.newbyteorder("=")

    @property
    def size(self) -> int:
        return int(np.prod(self.shape)) if self.shape else 1

    def __len__(self) -> int:
        return self.shape[0]

    def __getitem__(self, index: object) -> np.ndarray:
        # ``astype`` to the native-order dtype performs a value-preserving byte
        # swap on just the sliced data (typically one frame), not the whole stack.
        return np.asarray(self._base[index]).astype(self.dtype)

    def __array__(self, dtype: np.dtype | None = None, copy: bool | None = None) -> np.ndarray:
        # Fallback only; the per-frame ``__getitem__`` path is what napari uses
        # for slicing. This adapter always byte-swaps to native order, so it can
        # never return a no-copy view — honour the NumPy 2.x ``copy`` contract by
        # rejecting ``copy=False`` (NumPy 1.x never passes ``copy``).
        if copy is False:
            raise ValueError(
                "_NativeDisplayArray.__array__ always byte-swaps to native order; "
                "a zero-copy array (copy=False) is not possible."
            )
        out = np.asarray(self._base).astype(self.dtype)
        return out if dtype is None else out.astype(dtype, copy=False)

    def __iter__(self) -> Iterator[np.ndarray]:
        for i in range(self.shape[0]):
            yield self[i]


def _display_array(data: np.ndarray) -> np.ndarray | _NativeDisplayArray:
    """Return ``data`` in native byte order for GL display, lazily if non-native."""
    if data.dtype.byteorder in ("=", "|"):
        return data
    # Resolve explicit '<'/'>' against the host so the platform's own order is a
    # pass-through (zero copy); only a genuinely foreign order is wrapped.
    native = "<" if sys.byteorder == "little" else ">"
    if data.dtype.byteorder == native:
        return data
    return _NativeDisplayArray(data)


def _first_frame_contrast(data: np.ndarray) -> list[int]:
    """Cheap [lo, hi] contrast limits sampled from the first frame (native order).

    Reading one frame keeps the auto-range off the full ≈0.9 GB stack. A flat
    first frame (lo == hi) is widened by one so napari gets a valid range.
    """
    frame = np.asarray(data[0]).astype(data.dtype.newbyteorder("="))
    lo, hi = int(frame.min()), int(frame.max())
    if hi <= lo:
        hi = lo + 1
    return [lo, hi]


class NapariMoviePanel:
    """A napari viewer showing one lazy TIRF movie as a 2-D image layer.

    Create the panel (optionally headless with ``show=False``), then call
    :meth:`set_movie` to display a :class:`MovieReader`. Use as a context manager
    or call :meth:`close` to release the viewer. The napari Qt main window is
    exposed as :attr:`qt_window` for embedding into the Tether shell (M2).
    """

    def __init__(self, *, title: str = "Tether — movie", show: bool = False) -> None:
        # napari (and the Qt stack) is imported lazily so the pure-numpy display
        # helpers above stay importable without a GUI environment, and a headless
        # ``import tether.gui.movie_panel`` does not drag in Qt.
        import napari

        # show=False keeps the panel from popping a stray top-level window; a host
        # (the M2 shell, or a smoke launcher) calls .show()/embeds qt_window.
        self._viewer = napari.Viewer(title=title, ndisplay=2, show=show)
        self._movie_layer = None

    # --- accessors -----------------------------------------------------------

    @property
    def viewer(self) -> napari.Viewer:
        """The underlying :class:`napari.Viewer`."""
        return self._viewer

    @property
    def layers(self):  # napari LayerList (untyped to keep napari off the import path)
        """The viewer's layer list."""
        return self._viewer.layers

    @property
    def movie_layer(self):  # napari Image layer or None
        """The image layer for the current movie, or None before :meth:`set_movie`."""
        return self._movie_layer

    @property
    def qt_window(self):  # qtpy QMainWindow
        """The napari Qt main window, for embedding/showing in the Tether shell."""
        return self._viewer.window._qt_window

    # --- movie ---------------------------------------------------------------

    def set_movie(self, movie: MovieReader, *, name: str = _MOVIE_LAYER_NAME):
        """Display ``movie`` as the panel's image layer, replacing any previous one.

        ``movie`` is a :class:`MovieReader`; its lazy ``memmap`` is shown in native
        byte order (converted per frame for big-endian sources, see module docs).
        Returns the napari image layer.
        """
        if not isinstance(movie, MovieReader):
            raise TypeError(f"set_movie expects a MovieReader, got {type(movie).__name__}")
        data = _display_array(movie.data)
        if self._movie_layer is not None and self._movie_layer in self._viewer.layers:
            self._viewer.layers.remove(self._movie_layer)
            self._movie_layer = None
        self._movie_layer = self._viewer.add_image(
            data,
            name=name,
            colormap="gray",
            contrast_limits=_first_frame_contrast(movie.data),
        )
        return self._movie_layer

    # --- lifecycle -----------------------------------------------------------

    def show(self) -> None:
        """Show the napari window (no-op under an offscreen Qt platform)."""
        self._viewer.window.show()

    def screenshot(self, *, canvas_only: bool = True) -> np.ndarray:
        """Return an RGBA screenshot of the canvas as a numpy array."""
        return self._viewer.screenshot(canvas_only=canvas_only)

    def close(self) -> None:
        """Close the viewer and release its window."""
        self._viewer.close()

    def __enter__(self) -> NapariMoviePanel:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()
