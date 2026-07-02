# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""pyqtgraph static overlap view — patch + nearest-neighbour distance (PRD §7.3).

:class:`OverlapDock` is the *GUI* half of the M2 S10 neighbor/overlap view: for the
selected molecule it shows the cached **static image patch** (``/patches``, §5.1)
and a one-line readout of the **nearest-neighbour distance** with an
apertures-overlap warning. All geometry — the NN distance and the overlap flag —
comes from the Qt-free :mod:`tether.analysis.overlap` core (an
:class:`~tether.analysis.overlap.OverlapInfo`); this dock only draws what that core
returns, exactly as :class:`tether.gui.histogram_dock.HistogramDock` draws a
``Histogram1D``.

Because the patch is cached at extraction/import, the view works **without the
movie loaded** (movie-less curation, §5.1). An **analysis-only** project that
carries no patches (§7.4) still gets the NN readout; the patch panel just shows a
"no patch" placeholder rather than a fabricated image.

**Lazy Qt import.** As in :mod:`tether.gui.histogram_dock`, pyqtgraph and its Qt
binding are imported lazily inside :meth:`OverlapDock.__init__`, so importing this
module costs no Qt. Constructing an :class:`OverlapDock` needs a live
``QApplication`` (the embedding shell, ``qtbot``, or ``pyqtgraph.mkQApp``).
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    import pyqtgraph as pg
    from pyqtgraph.Qt import QtWidgets

    from tether.analysis.overlap import OverlapInfo

__all__ = ["OverlapDock"]

_PLACEHOLDER = "No molecule selected"
_NO_PATCH = "no patch"


def _readout(info: OverlapInfo) -> str:
    """The one-line neighbour readout (Qt-free, so it is unit-tested without a dock).

    ``"mol-3 · NN 4.2 px · apertures overlap"`` when a neighbour is within the
    overlap distance; ``"mol-3 · NN 12.4 px"`` when it is clear; ``"mol-3 · no
    neighbour"`` for the only molecule in its movie (``nn_distance`` is ``inf``).
    """
    parts: list[str] = [info.name] if info.name else []
    if math.isfinite(info.nn_distance):
        parts.append(f"NN {info.nn_distance:.1f} px")
        if info.overlaps:
            parts.append("apertures overlap")
    else:
        parts.append("no neighbour")
    return " · ".join(parts)


class OverlapDock:
    """A pyqtgraph dock showing the selected molecule's patch + NN distance.

    Construct (needs a ``QApplication``), then :meth:`set_molecule` an
    :class:`~tether.analysis.overlap.OverlapInfo` (or ``None`` to blank it). Embed
    :attr:`widget` (a ``QWidget``) into the Tether shell, or drive it in a ``qtbot``
    test. Use as a context manager or call :meth:`close`.

    Purely presentational: it takes an already-computed
    :class:`~tether.analysis.overlap.OverlapInfo` and draws it. No NN geometry lives
    here — that is :func:`tether.analysis.overlap.neighbor_report`.
    """

    def __init__(self) -> None:
        import pyqtgraph as pg
        from pyqtgraph.Qt import QtWidgets

        self._pg = pg
        self._info: OverlapInfo | None = None

        self._widget = QtWidgets.QWidget()
        vbox = QtWidgets.QVBoxLayout(self._widget)
        vbox.setContentsMargins(0, 0, 0, 0)

        # The always-visible neighbour readout (the assertable "renders NN distance").
        self._label = QtWidgets.QLabel(_PLACEHOLDER)
        vbox.addWidget(self._label)

        # The static image patch: an ImageItem in an aspect-locked ViewBox. patches
        # are (w, w) [row, col] images, so axisOrder="row-major" draws them upright.
        self._graphics = pg.GraphicsLayoutWidget()
        vbox.addWidget(self._graphics)
        self._view = self._graphics.addViewBox(row=0, col=0, lockAspect=True)
        self._view.invertY(True)  # image row 0 at the top, like napari / the movie
        # A single-channel ImageItem renders greyscale by default (levels → black to
        # white), which is what a raw intensity patch wants — no lookup table needed.
        self._image = pg.ImageItem(axisOrder="row-major")
        self._view.addItem(self._image)

    # --- accessors -----------------------------------------------------------

    @property
    def widget(self) -> QtWidgets.QWidget:
        """The embeddable ``QWidget`` (readout label + patch view)."""
        return self._widget

    @property
    def label(self) -> QtWidgets.QLabel:
        """The neighbour-readout ``QLabel``."""
        return self._label

    @property
    def graphics(self) -> pg.GraphicsLayoutWidget:
        """The underlying ``pyqtgraph.GraphicsLayoutWidget``."""
        return self._graphics

    @property
    def view(self) -> pg.ViewBox:
        """The aspect-locked ``ViewBox`` holding the patch image."""
        return self._view

    @property
    def image_item(self) -> pg.ImageItem:
        """The patch ``ImageItem``."""
        return self._image

    @property
    def info(self) -> OverlapInfo | None:
        """The currently displayed :class:`OverlapInfo`, or ``None``."""
        return self._info

    @property
    def readout(self) -> str:
        """The neighbour-readout text currently shown (the NN distance + flag)."""
        return self._label.text()

    @property
    def nn_distance(self) -> float | None:
        """The displayed nearest-neighbour distance, or ``None`` when nothing is shown."""
        return None if self._info is None else self._info.nn_distance

    @property
    def overlaps(self) -> bool:
        """Whether the displayed molecule's aperture overlaps its nearest neighbour's."""
        return bool(self._info is not None and self._info.overlaps)

    # --- display -------------------------------------------------------------

    def set_molecule(self, info: OverlapInfo | None) -> None:
        """Draw ``info`` (patch + NN readout), or blank the dock when ``None``.

        ``info`` is an :class:`~tether.analysis.overlap.OverlapInfo` from a
        store-backed overlap seam. A ``None`` ``info`` (nothing selected) or a
        ``None`` ``info.patch`` (analysis-only project, no cached patch) still draws
        the readout and leaves the patch panel blank — never a fabricated image.
        """
        if info is None:
            self._info = None
            self._label.setText(_PLACEHOLDER)
            self._image.clear()
            return
        # Validate the patch BEFORE mutating any dock state, so a rejected input
        # leaves the previously-shown molecule intact rather than a half-updated
        # view (new readout over a stale image).
        patch: np.ndarray | None = None
        if info.patch is not None:
            patch = np.asarray(info.patch, dtype=np.float64)
            if patch.ndim != 2:
                raise ValueError(f"patch must be a 2-D (w, w) image, got shape {patch.shape}")
        self._info = info
        if patch is None:
            self._label.setText(f"{_readout(info)} · {_NO_PATCH}")
            self._image.clear()
            return
        self._label.setText(_readout(info))
        # Explicit levels so a flat patch (e.g. an all-zero placeholder trace) does
        # not trip pyqtgraph's auto-level on a zero-width range.
        lo = float(patch.min())
        hi = float(patch.max())
        if hi <= lo:
            hi = lo + 1.0
        self._image.setImage(patch, levels=(lo, hi))
        self._view.autoRange()

    def clear(self) -> None:
        """Remove the current molecule and blank the patch + readout."""
        self._info = None
        self._label.setText(_PLACEHOLDER)
        self._image.clear()

    # --- lifecycle -----------------------------------------------------------

    def close(self) -> None:
        """Close and release the dock widget."""
        self._widget.close()

    def __enter__(self) -> OverlapDock:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()
