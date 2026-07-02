# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""pyqtgraph population apparent-E histogram dock (PRD §7.7, Appendix C plot A1).

:class:`HistogramDock` renders a :class:`~tether.analysis.histogram.Histogram1D`
— the pooled per-frame apparent-E of the accepted molecule population — as the
familiar tMAVEN A1 histogram. It is the *GUI* half of M2 S8: the headless
:func:`~tether.analysis.histogram.population_apparent_e_histogram` is the source
of truth (it reproduces the MVP histogram from the API, PRD §9 M2); this dock only
draws what that function returns, so no binning/science logic lives here.

At the MVP the x-axis reads **"apparent E"** — the uncorrected proximity ratio
``A / (D + A)`` (:func:`tether.fret.apparent_fret`); leakage/gamma corrections land
at M3, and the default range keeps the ratio's excursions beyond ``[0, 1]`` visible
rather than clipped (matching the headless core). The histogram is drawn as a
filled centred step over the frozen bin edges, with the pooled molecule/frame
counts shown in the plot title so the view is self-describing (NFR-REPRO).

**Lazy Qt import.** As in :mod:`tether.gui.trace_dock`, pyqtgraph and its Qt
binding are imported lazily inside :meth:`HistogramDock.__init__`, so importing
this module costs no Qt. Constructing a :class:`HistogramDock` needs a live
``QApplication`` (the embedding shell, ``qtbot``, or ``pyqtgraph.mkQApp``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from tether.analysis.histogram import DEFAULT_RANGE

if TYPE_CHECKING:
    import pyqtgraph as pg
    from pyqtgraph.Qt import QtWidgets

    from tether.analysis.histogram import Histogram1D

__all__ = ["HistogramDock"]

_E_LABEL = "apparent E"
# Blue, matching the FRET conventions of the per-trace dock (tether.gui.trace_dock):
# the population apparent-E histogram is the FRET marginal at population scale.
_HIST_PEN_RGB = (40, 90, 220)
_HIST_BRUSH = (40, 90, 220, 120)  # semi-transparent fill under the step


def _summary(histogram: Histogram1D) -> str:
    """A one-line title describing what was pooled (self-describing, NFR-REPRO)."""
    parts: list[str] = []
    n_mol = histogram.n_molecules
    if n_mol is not None:
        parts.append(f"{n_mol} molecule" + ("" if n_mol == 1 else "s"))
    n = histogram.n_samples
    parts.append(f"{n} frame" + ("" if n == 1 else "s"))
    if histogram.per_molecule_equal_weight:
        parts.append("per-molecule weighted")
    return " · ".join(parts)


class HistogramDock:
    """A pyqtgraph dock rendering the population apparent-E histogram.

    Construct (needs a ``QApplication``), then :meth:`set_histogram` a
    :class:`~tether.analysis.histogram.Histogram1D`. Embed :attr:`widget` (a
    ``QWidget``) into the Tether shell, or drive it in a ``qtbot`` test. Use as a
    context manager or call :meth:`close`.

    The dock is purely presentational: it takes an already-binned
    :class:`Histogram1D` and draws it. The x-axis is pinned to the histogram's
    ``value_range`` (default :data:`~tether.analysis.histogram.DEFAULT_RANGE`);
    the y-axis auto-ranges to the (density or count) peak.
    """

    def __init__(self) -> None:
        import pyqtgraph as pg
        from pyqtgraph.Qt import QtWidgets

        self._pg = pg
        self._histogram: Histogram1D | None = None

        self._widget = QtWidgets.QWidget()
        vbox = QtWidgets.QVBoxLayout(self._widget)
        vbox.setContentsMargins(0, 0, 0, 0)
        self._graphics = pg.GraphicsLayoutWidget()
        vbox.addWidget(self._graphics)

        self._plot = self._graphics.addPlot(row=0, col=0)
        self._plot.setLabel("bottom", _E_LABEL)
        self._plot.setLabel("left", "density")
        # "density"/"count" are dimensionless — suppress pyqtgraph's SI prefixing,
        # which would relabel the axis "k count" and silently rescale the values.
        self._plot.getAxis("left").enableAutoSIPrefix(False)
        self._plot.showGrid(x=True, y=True, alpha=0.2)
        # x pinned to the apparent-E range (PRD §7.7); y follows the data peak.
        self._plot.setXRange(*DEFAULT_RANGE, padding=0)
        self._plot.enableAutoRange(axis="x", enable=False)
        self._plot.enableAutoRange(axis="y", enable=True)

        # Filled centred step over the bin edges. stepMode="center" wants
        # len(x) == len(y) + 1 (pyqtgraph 0.14) — exactly Histogram1D's
        # (bin_edges, counts) shapes. Empty until set_histogram.
        self._curve = self._plot.plot(
            [],
            [],
            stepMode="center",
            fillLevel=0,
            brush=_HIST_BRUSH,
            pen=pg.mkPen(_HIST_PEN_RGB, width=1),
        )

    # --- accessors -----------------------------------------------------------

    @property
    def widget(self) -> QtWidgets.QWidget:
        """The embeddable ``QWidget`` (the plot)."""
        return self._widget

    @property
    def graphics(self) -> pg.GraphicsLayoutWidget:
        """The underlying ``pyqtgraph.GraphicsLayoutWidget``."""
        return self._graphics

    @property
    def plot(self) -> pg.PlotItem:
        """The histogram ``PlotItem``."""
        return self._plot

    @property
    def curve(self) -> pg.PlotDataItem:
        """The filled-step histogram curve."""
        return self._curve

    @property
    def histogram(self) -> Histogram1D | None:
        """The currently displayed :class:`Histogram1D`, or ``None``."""
        return self._histogram

    # --- display -------------------------------------------------------------

    def set_histogram(self, histogram: Histogram1D) -> None:
        """Draw ``histogram``, replacing any previous one.

        ``histogram`` is a :class:`~tether.analysis.histogram.Histogram1D` (the
        row :func:`~tether.analysis.histogram.population_apparent_e_histogram`
        returns). Its ``bin_edges`` must be one longer than ``counts`` (the
        ``stepMode="center"`` contract); a mismatch raises rather than letting
        pyqtgraph fail opaquely. An empty/all-zero histogram is a valid input (it
        draws a flat baseline — the honest "no data" answer, never fabricated).
        """
        counts = np.asarray(histogram.counts, dtype=np.float64)
        edges = np.asarray(histogram.bin_edges, dtype=np.float64)
        if counts.ndim != 1 or edges.ndim != 1 or edges.shape[0] != counts.shape[0] + 1:
            raise ValueError(
                "histogram must have 1-D bin_edges of length counts + 1, got "
                f"counts {counts.shape} vs bin_edges {edges.shape}"
            )
        self._histogram = histogram
        self._curve.setData(edges, counts)
        lo, hi = histogram.value_range
        self._plot.setXRange(float(lo), float(hi), padding=0)
        self._plot.setLabel("left", "density" if histogram.density else "count")
        self._plot.setTitle(_summary(histogram))

    def clear(self) -> None:
        """Remove the current histogram and blank the curve/title."""
        self._histogram = None
        self._curve.setData([], [])
        self._plot.setTitle(None)

    # --- lifecycle -----------------------------------------------------------

    def close(self) -> None:
        """Close and release the dock widget."""
        self._widget.close()

    def __enter__(self) -> HistogramDock:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()
