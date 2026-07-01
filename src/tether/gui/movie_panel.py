# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Embedded napari movie panel — the FR-ROUNDTRIP movie surface (PRD §4.1, §7.3).

:class:`NapariMoviePanel` wraps a single :class:`napari.Viewer` that displays a
lazy TIRF movie (:class:`tether.io.movie.MovieReader`) as a 2-D image layer with
**donor/acceptor spot points + integration-aperture overlays**, and switches
between **multiple movies** for a multi-movie experiment (PRD §7.3: "an embedded
napari movie panel showing the lazy movie + donor/acceptor points + aperture
overlays, with a movie switcher for multi-movie experiments").

* :meth:`set_movie` displays one movie (the M0 single-movie contract, unchanged).
* :meth:`add_movie` registers a movie — optionally with a :class:`MovieOverlay` of
  donor/acceptor spot coordinates — and :meth:`set_active_movie` switches which is
  shown; :attr:`switcher` is a ready :class:`~qtpy.QtWidgets.QComboBox` a host can
  place to drive the switch. The napari Qt window is exposed as :attr:`qt_window`
  so the Tether shell can embed it (the shell embed lands at M2 S4).
* :meth:`center_on` and :meth:`connect_spot_click` are the napari half of the
  M2 S4 trace↔movie round-trip (PRD §7.3, §5.2): centre the camera on a spot
  (trace → movie) and receive a canvas click (movie → trace). The resolver that
  drives them lives in :mod:`tether.gui.roundtrip`.

**Overlays.** Molecule spot positions are frame-independent, so a plain **2-D**
Points layer (``[row, col]``) rides over every frame of the ``(T, H, W)`` movie as
it is scrubbed. Each channel gets a small centre marker (donor green / acceptor
red, matching the pyqtgraph trace dock, PRD Appendix C D1) plus a ring drawn at the
integration-aperture PSF-disk radius (default 3 px, PRD Appendix E / §11.2). A
movie switch replaces the single image layer and updates the overlay layers in
place, so switching never piles up layers and only the active movie's ``memmap``
is displayed. Resolving a molecule to a displayed spot (and back) is the
round-trip resolver's job (:mod:`tether.gui.roundtrip`); the panel stays
presentational, exposing only the camera-centre / click seam it drives.

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

import math
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from tether.io.movie import MovieReader

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    import napari
    from qtpy.QtWidgets import QComboBox

__all__ = ["MovieOverlay", "NapariMoviePanel"]

_MOVIE_LAYER_NAME = "movie"
_DONOR_LAYER_NAME = "donor"
_ACCEPTOR_LAYER_NAME = "acceptor"
_DONOR_APERTURE_LAYER_NAME = "donor aperture"
_ACCEPTOR_APERTURE_LAYER_NAME = "acceptor aperture"

# Familiar smFRET channel colours (match the pyqtgraph trace dock, PRD §7.3 /
# Appendix C D1): donor green (0, 170, 0), acceptor red (220, 45, 45). Passed as
# hex so napari/vispy parses them unambiguously as a single colour.
_DONOR_HEX = "#00aa00"
_ACCEPTOR_HEX = "#dc2d2d"
_TRANSPARENT = "transparent"

# Overlay marker sizes, in movie pixels (napari Points ``size`` is the marker
# **diameter**). The centre marker is a small dot; the aperture ring is drawn at
# the PSF-disk diameter (2 × radius).
_CENTER_MARKER_DIAMETER = 3.0
_DEFAULT_APERTURE_RADIUS = 3.0  # Deep-LASI PSF disk radius (PRD Appendix E, §11.2)


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


def _validate_xy(xy: object, name: str) -> np.ndarray:
    """Validate an ``(N, 2)`` ``[x, y]`` spot-coordinate array (empty allowed).

    Returns a read-only ``float64`` copy so the immutable :class:`MovieOverlay`
    never aliases the caller's buffer. An empty 1-D input (e.g. ``[]``) normalises
    to a read-only ``(0, 2)``; any other non-``(N, 2)`` shape — including a
    malformed empty like ``(0, 3)`` — is rejected rather than silently normalised.
    """
    arr = np.asarray(xy, dtype=np.float64)
    if arr.ndim == 1 and arr.size == 0:
        arr = np.empty((0, 2), dtype=np.float64)
    elif arr.ndim != 2 or arr.shape[1] != 2:
        raise ValueError(f"{name} must be an (N, 2) [x, y] array, got shape {arr.shape}")
    if not np.isfinite(arr).all():
        raise ValueError(f"{name} must be finite")
    out = arr.copy()
    out.setflags(write=False)
    return out


def _to_rowcol(xy: np.ndarray) -> np.ndarray:
    """Convert ``(N, 2)`` ``[x, y]`` = ``[col, row]`` to napari ``(N, 2)`` ``[row, col]``.

    napari Points live in ``[row, col]`` order, the transpose of the ``[x, y]``
    convention the imaging layer uses (``detect_spots`` / ``donor_xy``). An empty
    input maps to a ``(0, 2)`` array so an empty Points layer stays 2-D.
    """
    arr = np.asarray(xy, dtype=np.float64)
    if arr.size == 0:
        return np.empty((0, 2), dtype=np.float64)
    return arr[:, ::-1].astype(np.float64, copy=True)


@dataclass(frozen=True)
class MovieOverlay:
    """Donor/acceptor spot positions to overlay on one movie (PRD §7.3, §5.2).

    Coordinates are ``[x, y]`` = ``[column, row]`` in the **displayed movie's**
    pixel frame (the full dual-channel frame the panel shows) — the same ``[x, y]``
    convention as :func:`tether.imaging.detect.detect_spots` and the ``/molecules``
    ``donor_xy`` / ``acceptor_xy`` fields (§5.1). The panel draws a small centre
    marker at each spot plus a ring at the integration-aperture (PSF-disk) radius.

    Parameters
    ----------
    donor_xy, acceptor_xy:
        ``(N, 2)`` ``[x, y]`` spot centres (each may be empty).
    aperture_radius:
        PSF-disk radius in px for the aperture ring (default 3, the Deep-LASI disk
        radius; PRD Appendix E / §11.2).
    """

    donor_xy: np.ndarray
    acceptor_xy: np.ndarray
    aperture_radius: float = _DEFAULT_APERTURE_RADIUS

    def __post_init__(self) -> None:
        object.__setattr__(self, "donor_xy", _validate_xy(self.donor_xy, "donor_xy"))
        object.__setattr__(self, "acceptor_xy", _validate_xy(self.acceptor_xy, "acceptor_xy"))
        if not (math.isfinite(self.aperture_radius) and float(self.aperture_radius) > 0.0):
            raise ValueError(
                f"aperture_radius must be a finite positive number, got {self.aperture_radius!r}"
            )


@dataclass(frozen=True)
class _MovieEntry:
    """One registered movie: its GL-ready display array, contrast, overlay, label."""

    display: object  # np.ndarray or _NativeDisplayArray (array-like napari accepts)
    contrast: list[int]
    overlay: MovieOverlay | None
    name: str


class NapariMoviePanel:
    """A napari viewer showing lazy TIRF movies with donor/acceptor overlays.

    Create the panel (optionally headless with ``show=False``), then either
    :meth:`set_movie` a single :class:`MovieReader` or :meth:`add_movie` several
    (each optionally with a :class:`MovieOverlay`) and :meth:`set_active_movie`
    between them. Use as a context manager or call :meth:`close`. The napari Qt
    main window is exposed as :attr:`qt_window` for embedding into the Tether shell
    (M2 S4).
    """

    def __init__(self, *, title: str = "Tether — movie", show: bool = False) -> None:
        # napari (and the Qt stack) is imported lazily so the pure-numpy display
        # helpers above stay importable without a GUI environment, and a headless
        # ``import tether.gui.movie_panel`` does not drag in Qt.
        import napari

        # show=False keeps the panel from popping a stray top-level window; a host
        # (the M2 shell, or a smoke launcher) calls .show()/embeds qt_window.
        self._viewer = napari.Viewer(title=title, ndisplay=2, show=show)
        self._movies: list[_MovieEntry] = []
        self._active_index: int = -1
        # One reusable layer each — swapped in place on a movie switch.
        self._movie_layer = None
        self._donor_layer = None
        self._acceptor_layer = None
        self._donor_aperture_layer = None
        self._acceptor_aperture_layer = None
        self._switcher: QComboBox | None = None  # lazily created on first access
        # Viewer-level mouse-drag callbacks registered by connect_spot_click, kept
        # so they can be detached on close (the pytest-qt QApplication is shared).
        self._spot_click_callbacks: list = []

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
        """The image layer for the active movie, or None before any movie is set."""
        return self._movie_layer

    @property
    def donor_layer(self):  # napari Points layer or None
        """The donor spot-centre Points layer (None until an overlay is shown)."""
        return self._donor_layer

    @property
    def acceptor_layer(self):  # napari Points layer or None
        """The acceptor spot-centre Points layer (None until an overlay is shown)."""
        return self._acceptor_layer

    @property
    def donor_aperture_layer(self):  # napari Points layer or None
        """The donor aperture-ring Points layer (None until an overlay is shown)."""
        return self._donor_aperture_layer

    @property
    def acceptor_aperture_layer(self):  # napari Points layer or None
        """The acceptor aperture-ring Points layer (None until an overlay is shown)."""
        return self._acceptor_aperture_layer

    @property
    def n_movies(self) -> int:
        """Number of registered movies."""
        return len(self._movies)

    @property
    def active_index(self) -> int:
        """Index of the movie currently shown, or ``-1`` when none is set."""
        return self._active_index

    @property
    def movie_names(self) -> list[str]:
        """The registered movies' switcher labels, in registration order."""
        return [entry.name for entry in self._movies]

    @property
    def active_overlay(self) -> MovieOverlay | None:
        """The :class:`MovieOverlay` of the active movie (``None`` if none/plain)."""
        if 0 <= self._active_index < len(self._movies):
            return self._movies[self._active_index].overlay
        return None

    @property
    def qt_window(self):  # qtpy QMainWindow
        """The napari Qt main window, for embedding/showing in the Tether shell."""
        return self._viewer.window._qt_window

    @property
    def switcher(self) -> QComboBox:
        """A :class:`~qtpy.QtWidgets.QComboBox` that switches the active movie.

        Lazily created and kept in sync with :meth:`add_movie` /
        :meth:`set_active_movie`; selecting an entry calls :meth:`set_active_movie`.
        A host (the Tether shell, M2 S4) places it in its chrome.
        """
        from qtpy.QtWidgets import QComboBox

        if self._switcher is None:
            combo = QComboBox()
            combo.addItems(self.movie_names)
            if 0 <= self._active_index < combo.count():
                combo.setCurrentIndex(self._active_index)
            combo.currentIndexChanged.connect(self._on_switcher_changed)
            self._switcher = combo
        return self._switcher

    # --- movies --------------------------------------------------------------

    def set_movie(self, movie: MovieReader, *, name: str = _MOVIE_LAYER_NAME):
        """Display ``movie`` as the sole movie, replacing any previous ones.

        The single-movie M0 contract: shows one image layer (no overlays). Returns
        the napari image layer. To overlay spots or hold several movies, use
        :meth:`add_movie` + :meth:`set_active_movie` instead.
        """
        if not isinstance(movie, MovieReader):
            raise TypeError(f"set_movie expects a MovieReader, got {type(movie).__name__}")
        self.clear_movies()
        self.add_movie(movie, name=name)
        return self._movie_layer

    def add_movie(
        self,
        movie: MovieReader,
        *,
        overlay: MovieOverlay | None = None,
        name: str | None = None,
    ) -> int:
        """Register ``movie`` (optionally with ``overlay``); return its index.

        The movie's lazy ``memmap`` is prepared for native-order display and its
        first-frame contrast is sampled now, so a later switch is a cheap layer
        swap. The **first** movie added becomes active immediately. Its ``memmap``
        must stay open (the caller owns the :class:`MovieReader` lifetime, as with
        :meth:`set_movie`).
        """
        if not isinstance(movie, MovieReader):
            raise TypeError(f"add_movie expects a MovieReader, got {type(movie).__name__}")
        if overlay is not None and not isinstance(overlay, MovieOverlay):
            raise TypeError(f"overlay must be a MovieOverlay or None, got {type(overlay).__name__}")
        index = len(self._movies)
        entry = _MovieEntry(
            display=_display_array(movie.data),
            contrast=_first_frame_contrast(movie.data),
            overlay=overlay,
            name=name if name is not None else f"movie-{index}",
        )
        self._movies.append(entry)
        if self._switcher is not None:
            self._switcher.blockSignals(True)
            self._switcher.addItem(entry.name)
            self._switcher.blockSignals(False)
        if self._active_index < 0:
            self.set_active_movie(index)
        return index

    def set_active_movie(self, index: int):
        """Switch the displayed movie to ``index`` (swaps image + overlays).

        Re-frames the view when the movie shape changes. Returns the image layer.
        """
        if not 0 <= index < len(self._movies):
            raise IndexError(f"movie index {index} out of range (have {len(self._movies)})")
        entry = self._movies[index]
        shape_changed = self._movie_layer is None or tuple(self._movie_layer.data.shape) != tuple(
            entry.display.shape
        )
        self._set_image(entry.display, entry.contrast)
        self._render_overlay(entry.overlay)
        self._active_index = index
        if self._switcher is not None and self._switcher.currentIndex() != index:
            self._switcher.blockSignals(True)
            self._switcher.setCurrentIndex(index)
            self._switcher.blockSignals(False)
        if shape_changed:
            # Re-fit the camera so a differently-sized movie is fully framed.
            self._viewer.reset_view()
        return self._movie_layer

    def clear_movies(self) -> None:
        """Forget every registered movie and remove the panel-owned layers.

        Removing the image + overlay layers (not just blanking them) resets the
        viewer to a pristine state, so :meth:`set_movie` after an overlay session
        restores the M0 single-image-layer contract rather than leaving empty
        Points layers behind.
        """
        self._movies.clear()
        self._active_index = -1
        if self._switcher is not None:
            self._switcher.blockSignals(True)
            self._switcher.clear()
            self._switcher.blockSignals(False)
        for layer in (
            self._movie_layer,
            self._donor_layer,
            self._acceptor_layer,
            self._donor_aperture_layer,
            self._acceptor_aperture_layer,
        ):
            if layer is not None and layer in self._viewer.layers:
                self._viewer.layers.remove(layer)
        self._movie_layer = None
        self._donor_layer = None
        self._acceptor_layer = None
        self._donor_aperture_layer = None
        self._acceptor_aperture_layer = None

    # --- internals -----------------------------------------------------------

    def _on_switcher_changed(self, index: int) -> None:
        if 0 <= index < len(self._movies) and index != self._active_index:
            self.set_active_movie(index)

    def _set_image(self, display: object, contrast: list[int]):
        """Replace the movie image layer with ``display`` (kept beneath overlays).

        The image layer is removed and re-added rather than having its ``.data``
        swapped: a fresh :meth:`add_image` sets ``contrast_limits`` for the new
        movie atomically at creation, sidestepping napari's thumbnail update
        clipping the new (differently-scaled) data against the previous movie's
        limits. The re-added layer lands on top of the layer stack, so it is moved
        back to index 0 to stay under the spot/aperture overlays.
        """
        if self._movie_layer is not None and self._movie_layer in self._viewer.layers:
            self._viewer.layers.remove(self._movie_layer)
        self._movie_layer = self._viewer.add_image(
            display, name=_MOVIE_LAYER_NAME, colormap="gray", contrast_limits=contrast
        )
        index = self._viewer.layers.index(self._movie_layer)
        if index != 0:
            self._viewer.layers.move(index, 0)
        return self._movie_layer

    def _render_overlay(self, overlay: MovieOverlay | None) -> None:
        """Create/update the four overlay layers for ``overlay`` (or blank them).

        A plain movie (``overlay is None``) that has never had overlays creates no
        overlay layers (the M0 single-image contract). Once any movie carried an
        overlay, a subsequent plain movie blanks the layers rather than removing
        them, so the reusable set stays stable across switches.
        """
        if overlay is None:
            if self._donor_layer is None:
                return  # no overlay layers ever created — stay a single image layer
            donor = acceptor = np.empty((0, 2), dtype=np.float64)
            radius = _DEFAULT_APERTURE_RADIUS
        else:
            donor, acceptor, radius = overlay.donor_xy, overlay.acceptor_xy, overlay.aperture_radius

        diameter = 2.0 * float(radius)
        self._donor_layer = self._set_points(
            self._donor_layer,
            _DONOR_LAYER_NAME,
            _to_rowcol(donor),
            symbol="disc",
            face=_DONOR_HEX,
            border=_DONOR_HEX,
            size=_CENTER_MARKER_DIAMETER,
        )
        self._acceptor_layer = self._set_points(
            self._acceptor_layer,
            _ACCEPTOR_LAYER_NAME,
            _to_rowcol(acceptor),
            symbol="disc",
            face=_ACCEPTOR_HEX,
            border=_ACCEPTOR_HEX,
            size=_CENTER_MARKER_DIAMETER,
        )
        self._donor_aperture_layer = self._set_points(
            self._donor_aperture_layer,
            _DONOR_APERTURE_LAYER_NAME,
            _to_rowcol(donor),
            symbol="ring",
            face=_TRANSPARENT,
            border=_DONOR_HEX,
            size=diameter,
        )
        self._acceptor_aperture_layer = self._set_points(
            self._acceptor_aperture_layer,
            _ACCEPTOR_APERTURE_LAYER_NAME,
            _to_rowcol(acceptor),
            symbol="ring",
            face=_TRANSPARENT,
            border=_ACCEPTOR_HEX,
            size=diameter,
        )

    def _set_points(
        self,
        layer,
        name: str,
        data: np.ndarray,
        *,
        symbol: str,
        face: str,
        border: str,
        size: float,
    ):
        """Create a Points overlay layer, or swap its data + size in place.

        Colour/symbol are fixed per layer (set once at creation); only ``data`` and
        the (per-movie) aperture ``size`` change on a switch.
        """
        if layer is not None and layer in self._viewer.layers:
            layer.data = data
            layer.size = size
            return layer
        return self._viewer.add_points(
            data,
            name=name,
            symbol=symbol,
            size=size,
            face_color=face,
            border_color=border,
        )

    # --- round-trip navigation (M2 S4, PRD §7.3, §5.2) -----------------------

    def center_on(self, row: float, col: float, *, zoom: float | None = None) -> None:
        """Centre the camera on a ``(row, col)`` world coordinate (trace → movie jump).

        The napari half of the trace → movie leg: given a molecule's spot in napari
        ``[row, col]`` world coordinates (from
        :meth:`~tether.gui.roundtrip.RoundTripIndex.camera_target`), pan the camera
        so the spot is centred, optionally setting ``zoom`` (canvas px per world px).

        ``napari.components.Camera.center`` is always a 3-tuple ``(depth, row, col)``
        even in the 2-D display; the leading depth component is preserved and only
        the in-plane ``(row, col)`` is replaced, so this works whether the viewer is
        2-D or 3-D without assuming the tuple length.
        """
        center = tuple(float(v) for v in self._viewer.camera.center)
        self._viewer.camera.center = (*center[:-2], float(row), float(col))
        if zoom is not None:
            self._viewer.camera.zoom = float(zoom)

    def connect_spot_click(self, callback: Callable[[tuple[float, float]], None]) -> None:
        """Call ``callback((row, col))`` on a canvas **click** (movie → trace leg).

        Registers a viewer-level napari ``mouse_drag`` callback — at the viewer, not
        a layer, so it survives the image/overlay layer swaps of a movie switch. The
        callback fires only on a **click** (press with no drag): the generator yields
        once on press, consumes any ``mouse_move`` events, and invokes ``callback``
        on release **iff** the pointer never moved — so panning the movie never
        selects a molecule. ``event.position`` is the napari ``(…, row, col)`` world
        coordinate; its last two components are passed on.
        """

        def _on_mouse_drag(_viewer: object, event: object) -> object:
            start = tuple(float(v) for v in event.position)  # type: ignore[attr-defined]
            dragged = False
            yield
            while event.type == "mouse_move":  # type: ignore[attr-defined]
                dragged = True
                yield
            if not dragged:
                callback((start[-2], start[-1]))

        self._viewer.mouse_drag_callbacks.append(_on_mouse_drag)
        self._spot_click_callbacks.append(_on_mouse_drag)

    def disconnect_spot_clicks(self) -> None:
        """Detach every callback registered by :meth:`connect_spot_click`."""
        for cb in self._spot_click_callbacks:
            if cb in self._viewer.mouse_drag_callbacks:
                self._viewer.mouse_drag_callbacks.remove(cb)
        self._spot_click_callbacks.clear()

    # --- lifecycle -----------------------------------------------------------

    def show(self) -> None:
        """Show the napari window (no-op under an offscreen Qt platform)."""
        self._viewer.window.show()

    def screenshot(self, *, canvas_only: bool = True) -> np.ndarray:
        """Return an RGBA screenshot of the canvas as a numpy array."""
        return self._viewer.screenshot(canvas_only=canvas_only)

    def close(self) -> None:
        """Close the viewer and release its window (and the switcher, if built)."""
        self.disconnect_spot_clicks()
        if self._switcher is not None:
            self._switcher.deleteLater()
            self._switcher = None
        self._viewer.close()

    def __enter__(self) -> NapariMoviePanel:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()
