# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""pyqtgraph trace dock — the per-trace curation surface (PRD §7.3, Appendix C D1).

:class:`TraceDock` is the keyboard-driven per-trace viewer at the heart of the
FR-ROUNDTRIP browser: donor/acceptor/total intensity + the FRET time-series with
its marginal histograms. It is tMAVEN's per-trace viewer (Appendix C, **D1**)
reimagined as Tether's curation surface, so it keeps the familiar conventions —
**donor green / acceptor red / FRET blue**, the FRET axis fixed to ``0–1``, the
x-axis in **seconds** (from the movie ``FrameTime``) with a **frame-index
toggle**, and an idealization **step overlay** drawn over the FRET panel.

At the MVP the FRET axis reads **"apparent E"**: the uncorrected proximity ratio
``A / (D + A)`` (see :func:`tether.fret.apparent_fret`); leakage/gamma
corrections land at M3. The idealization overlay is a **reserved placeholder**
here (empty, hidden) — one-click vbFRET populates it at M2 S6. tMAVEN
visual-parity of this plot is verified with the M6 seven-plot gallery (§10), not
in this PR.

**Lazy Qt import.** As in :mod:`tether.gui.movie_panel`, pyqtgraph and its Qt
binding are imported lazily inside :meth:`TraceDock.__init__`, so importing this
module (and using the Qt-free :class:`TraceView` value object) costs no Qt — the
pure trace/efficiency logic stays testable headless. Constructing a
:class:`TraceDock` needs a live ``QApplication`` (the embedding shell, ``qtbot``,
or ``pyqtgraph.mkQApp`` provides one).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np

from tether.fret.efficiency import apparent_fret

__all__ = ["TimeMode", "TraceDock", "TraceView", "trace_from_smd"]

TimeMode = Literal["seconds", "frames"]

# --- familiar smFRET display conventions (PRD §7.3) --------------------------
# RGB triples; pyqtgraph accepts these directly as pen colours.
_DONOR_RGB = (0, 170, 0)  # green
_ACCEPTOR_RGB = (220, 45, 45)  # red
_TOTAL_RGB = (140, 140, 140)  # neutral grey (donor + acceptor)
_FRET_RGB = (40, 90, 220)  # blue
_IDEALIZATION_RGB = (20, 20, 20)  # near-black step overlay

_FRET_LABEL = "apparent E"
_FRET_RANGE = (0.0, 1.0)
_N_HIST_BINS = 40


@dataclass(frozen=True)
class TraceView:
    """One molecule's donor/acceptor time-series to display (Qt-free).

    A thin, immutable value object decoupling the dock from any particular data
    source (an SMD file, a ``.tether`` ``/traces`` slice, a simulator): a caller
    resolves a molecule to its donor/acceptor arrays and its movie ``frame_time``
    and hands over a :class:`TraceView`. Apparent E and the total-intensity trace
    are derived here so the dock stays purely presentational.

    Parameters
    ----------
    donor, acceptor
        1-D per-frame intensities of equal length (``>= 1`` frame).
    frame_time
        Seconds per frame (from the movie metadata). ``None`` when unknown, in
        which case the dock can only show a frame-index axis.
    name
        Optional label (e.g. molecule key) for the dock title / legend.
    """

    donor: np.ndarray
    acceptor: np.ndarray
    frame_time: float | None = None
    name: str | None = None

    def __post_init__(self) -> None:
        donor = np.asarray(self.donor, dtype=np.float64)
        acceptor = np.asarray(self.acceptor, dtype=np.float64)
        if donor.ndim != 1 or acceptor.ndim != 1:
            raise ValueError("donor and acceptor must be 1-D per-frame arrays")
        if donor.shape != acceptor.shape:
            raise ValueError(
                f"donor and acceptor must be the same length, got {donor.shape} vs {acceptor.shape}"
            )
        if donor.size == 0:
            raise ValueError("a trace needs at least one frame")
        if self.frame_time is not None and not (
            math.isfinite(self.frame_time) and float(self.frame_time) > 0.0
        ):
            # Reject 0, negatives, NaN, and +/-inf: a non-finite frame_time (e.g.
            # from corrupt movie metadata) would poison the seconds axis with a
            # non-finite time scale (arange * inf -> nan/inf) instead of failing here.
            raise ValueError(
                f"frame_time must be a finite positive number when given, got {self.frame_time!r}"
            )
        # Normalise the stored arrays to contiguous float64 (frozen: via object.__setattr__).
        object.__setattr__(self, "donor", donor)
        object.__setattr__(self, "acceptor", acceptor)

    @property
    def n_frames(self) -> int:
        return int(self.donor.size)

    @property
    def total(self) -> np.ndarray:
        """Total intensity ``donor + acceptor`` (anticorrelation / bleaching cue)."""
        return self.donor + self.acceptor

    @property
    def apparent_e(self) -> np.ndarray:
        """Apparent FRET efficiency (proximity ratio); ``NaN`` where total is 0."""
        return apparent_fret(self.donor, self.acceptor)

    def time_axis(self, mode: TimeMode) -> np.ndarray:
        """Return the x-axis for ``mode``: frame index, or seconds if available.

        ``"seconds"`` requires :attr:`frame_time`; without it the frame-index
        axis is returned regardless, so the caller never plots a bogus time axis.
        """
        frames = np.arange(self.n_frames, dtype=np.float64)
        if mode == "seconds" and self.frame_time is not None:
            return frames * float(self.frame_time)
        return frames


def _marginal_histogram(
    values: np.ndarray, lo: float | None, hi: float | None, bins: int
) -> tuple[np.ndarray, np.ndarray, float]:
    """Return ``(counts, bin_centers, bin_width)`` of finite ``values``.

    Non-finite entries (the ``NaN`` gaps of apparent E) are dropped. ``lo``/``hi``
    fix the range for a stable axis (the FRET marginal spans ``0–1`` regardless of
    the trace); ``None`` lets NumPy pick from the data (intensity marginal).
    """
    finite = values[np.isfinite(values)]
    hist_range = None if lo is None or hi is None else (lo, hi)
    if finite.size == 0:
        # Empty (all-NaN) — return zero counts over the requested/degenerate range.
        edges = np.linspace(lo if lo is not None else 0.0, hi if hi is not None else 1.0, bins + 1)
        counts = np.zeros(bins, dtype=np.float64)
    else:
        counts, edges = np.histogram(finite, bins=bins, range=hist_range)
        counts = counts.astype(np.float64)
    centers = 0.5 * (edges[:-1] + edges[1:])
    width = float(edges[1] - edges[0]) if edges.size > 1 else 1.0
    return counts, centers, width


class TraceDock:
    """A pyqtgraph per-trace viewer: intensity + apparent-E + marginal histograms.

    Construct (needs a ``QApplication``), then :meth:`set_trace` a
    :class:`TraceView`. Embed :attr:`widget` (a ``QWidget``) into the Tether
    shell, or drive it in a ``qtbot`` test. Use as a context manager or call
    :meth:`close`.

    Layout is a 2x2 pyqtgraph grid: intensity time-series (top-left) over the
    FRET time-series (bottom-left, x-linked), each with its marginal histogram in
    the right column (y-linked). The FRET panel carries the reserved idealization
    step overlay.
    """

    def __init__(self, *, seconds_by_default: bool = True) -> None:
        import pyqtgraph as pg
        from pyqtgraph.Qt import QtWidgets

        self._pg = pg
        self._trace: TraceView | None = None
        # Preference; the *effective* mode also depends on whether the current
        # trace carries a frame_time (a frame-index-only trace forces "frames").
        self._time_mode: TimeMode = "seconds" if seconds_by_default else "frames"

        # Container = a checkbox toggle above the plot grid.
        self._widget = QtWidgets.QWidget()
        vbox = QtWidgets.QVBoxLayout(self._widget)
        vbox.setContentsMargins(0, 0, 0, 0)
        self._time_checkbox = QtWidgets.QCheckBox("Time axis in seconds")
        self._time_checkbox.setChecked(self._time_mode == "seconds")
        self._time_checkbox.setEnabled(False)  # enabled once a trace with frame_time loads
        self._time_checkbox.toggled.connect(self._on_time_checkbox_toggled)
        vbox.addWidget(self._time_checkbox)

        self._graphics = pg.GraphicsLayoutWidget()
        vbox.addWidget(self._graphics)

        # --- row 0: intensity time-series + its marginal ---------------------
        self._intensity_plot = self._graphics.addPlot(row=0, col=0)
        self._intensity_plot.setLabel("left", "Intensity", units="a.u.")
        # "a.u." is dimensionless — suppress pyqtgraph's auto SI prefixing, which
        # would relabel the axis "k a.u." and silently rescale the shown values.
        self._intensity_plot.getAxis("left").enableAutoSIPrefix(False)
        self._intensity_plot.addLegend(offset=(-10, 10))
        self._intensity_plot.showGrid(x=True, y=True, alpha=0.2)
        self._intensity_plot.getAxis("bottom").setStyle(showValues=False)  # shared axis below

        self._intensity_hist = self._graphics.addPlot(row=0, col=1)
        self._intensity_hist.setLabel("bottom", "count")
        self._intensity_hist.setYLink(self._intensity_plot)
        self._intensity_hist.getAxis("left").setStyle(showValues=False)

        # --- row 1: FRET time-series + its marginal --------------------------
        self._fret_plot = self._graphics.addPlot(row=1, col=0)
        self._fret_plot.setLabel("left", _FRET_LABEL)
        self._fret_plot.setLabel("bottom", "Time", units="s")
        self._fret_plot.showGrid(x=True, y=True, alpha=0.2)
        self._fret_plot.setXLink(self._intensity_plot)
        # Default view is exactly 0-1 (PRD §7.3), with no stray pyqtgraph margin.
        self._fret_plot.setYRange(*_FRET_RANGE, padding=0)
        self._fret_plot.enableAutoRange(axis="y", enable=False)
        # y-mouse stays enabled so the user can zoom out to inspect the (rare)
        # uncorrected-ratio excursions beyond 0-1 rather than have them hidden.
        self._fret_plot.setMouseEnabled(x=True, y=True)

        self._fret_hist = self._graphics.addPlot(row=1, col=1)
        self._fret_hist.setLabel("bottom", "count")
        self._fret_hist.setYLink(self._fret_plot)
        self._fret_hist.getAxis("left").setStyle(showValues=False)

        # Give the time-series column the bulk of the width.
        layout = self._graphics.ci.layout
        layout.setColumnStretchFactor(0, 4)
        layout.setColumnStretchFactor(1, 1)

        # --- persistent curves (updated in place by set_trace) ---------------
        self._donor_curve = self._intensity_plot.plot(
            [], [], pen=pg.mkPen(_DONOR_RGB, width=1), name="Donor"
        )
        self._acceptor_curve = self._intensity_plot.plot(
            [], [], pen=pg.mkPen(_ACCEPTOR_RGB, width=1), name="Acceptor"
        )
        self._total_curve = self._intensity_plot.plot(
            [], [], pen=pg.mkPen(_TOTAL_RGB, width=1), name="Total"
        )
        self._fret_curve = self._fret_plot.plot(
            [], [], pen=pg.mkPen(_FRET_RGB, width=1), connect="finite"
        )
        # Reserved idealization step overlay (empty/hidden until M2 S6).
        self._idealization_curve = self._fret_plot.plot(
            [], [], pen=pg.mkPen(_IDEALIZATION_RGB, width=1.5), stepMode="center"
        )
        self._idealization_curve.setVisible(False)

        # Marginal-histogram bar items (created lazily; replaced on each update).
        self._intensity_bars: Any | None = None
        self._fret_bars: Any | None = None

    # --- accessors -----------------------------------------------------------

    @property
    def widget(self) -> Any:
        """The embeddable ``QWidget`` (checkbox toggle + plot grid)."""
        return self._widget

    @property
    def graphics(self) -> Any:
        """The underlying ``pyqtgraph.GraphicsLayoutWidget``."""
        return self._graphics

    @property
    def time_checkbox(self) -> Any:
        """The seconds/frame-index toggle ``QCheckBox``."""
        return self._time_checkbox

    @property
    def intensity_plot(self) -> Any:
        return self._intensity_plot

    @property
    def fret_plot(self) -> Any:
        return self._fret_plot

    @property
    def intensity_histogram(self) -> Any:
        return self._intensity_hist

    @property
    def fret_histogram(self) -> Any:
        return self._fret_hist

    @property
    def donor_curve(self) -> Any:
        return self._donor_curve

    @property
    def acceptor_curve(self) -> Any:
        return self._acceptor_curve

    @property
    def total_curve(self) -> Any:
        return self._total_curve

    @property
    def fret_curve(self) -> Any:
        return self._fret_curve

    @property
    def idealization_curve(self) -> Any:
        """Reserved step overlay for the idealized path (empty until M2 S6)."""
        return self._idealization_curve

    @property
    def trace(self) -> TraceView | None:
        """The currently displayed :class:`TraceView`, or ``None``."""
        return self._trace

    @property
    def time_mode(self) -> TimeMode:
        """The *effective* x-axis mode given the current trace's ``frame_time``."""
        if self._time_mode == "seconds" and self._trace is not None and self.seconds_available:
            return "seconds"
        return "frames" if self._trace is not None else self._time_mode

    @property
    def seconds_available(self) -> bool:
        """Whether the current trace can show a seconds axis (has a ``frame_time``)."""
        return self._trace is not None and self._trace.frame_time is not None

    # --- display -------------------------------------------------------------

    def set_trace(self, trace: TraceView) -> None:
        """Display ``trace``, replacing any previous one, and refresh histograms."""
        if not isinstance(trace, TraceView):
            raise TypeError(f"set_trace expects a TraceView, got {type(trace).__name__}")
        self._trace = trace

        # The seconds toggle is only meaningful when a frame_time is known.
        self._time_checkbox.blockSignals(True)
        self._time_checkbox.setEnabled(trace.frame_time is not None)
        self._time_checkbox.setChecked(self.time_mode == "seconds")
        self._time_checkbox.blockSignals(False)

        self._render()

    def set_time_mode(self, mode: TimeMode) -> None:
        """Set the x-axis to ``"seconds"`` or ``"frames"`` and re-render.

        Requesting ``"seconds"`` for a trace with no ``frame_time`` falls back to
        the frame-index axis rather than fabricating a time scale.
        """
        if mode not in ("seconds", "frames"):
            raise ValueError(f"mode must be 'seconds' or 'frames', got {mode!r}")
        self._time_mode = mode
        self._time_checkbox.blockSignals(True)
        self._time_checkbox.setChecked(self.time_mode == "seconds")
        self._time_checkbox.blockSignals(False)
        if self._trace is not None:
            self._render()

    def clear(self) -> None:
        """Remove the current trace and blank every curve/histogram."""
        self._trace = None
        for curve in (self._donor_curve, self._acceptor_curve, self._total_curve, self._fret_curve):
            curve.setData([], [])
        self._idealization_curve.setData([], [])
        self._idealization_curve.setVisible(False)
        self._remove_bars()

    # --- internals -----------------------------------------------------------

    def _on_time_checkbox_toggled(self, checked: bool) -> None:
        self.set_time_mode("seconds" if checked else "frames")

    def _render(self) -> None:
        trace = self._trace
        if trace is None:  # _render is only reached with a trace; defensive guard
            return
        mode = self.time_mode
        x = trace.time_axis(mode)

        self._donor_curve.setData(x, trace.donor)
        self._acceptor_curve.setData(x, trace.acceptor)
        self._total_curve.setData(x, trace.total)
        self._fret_curve.setData(x, trace.apparent_e, connect="finite")

        self._fret_plot.setLabel(
            "bottom",
            "Time" if mode == "seconds" else "Frame",
            units="s" if mode == "seconds" else "",
        )

        # Marginal histograms: FRET pinned to 0-1; intensity auto-ranged on total.
        f_counts, f_centers, f_width = _marginal_histogram(
            trace.apparent_e, _FRET_RANGE[0], _FRET_RANGE[1], _N_HIST_BINS
        )
        i_counts, i_centers, i_width = _marginal_histogram(trace.total, None, None, _N_HIST_BINS)
        self._fret_bars = self._draw_bars(
            self._fret_hist, self._fret_bars, f_counts, f_centers, f_width
        )
        self._intensity_bars = self._draw_bars(
            self._intensity_hist, self._intensity_bars, i_counts, i_centers, i_width
        )

    def _draw_bars(
        self, plot: Any, existing: Any, counts: np.ndarray, centers: np.ndarray, width: float
    ) -> Any:
        """Replace ``existing`` horizontal bars on ``plot`` with a fresh marginal."""
        if existing is not None:
            plot.removeItem(existing)
        bars = self._pg.BarGraphItem(
            x0=0.0, width=counts, y=centers, height=width * 0.9, brush=(120, 120, 120, 160)
        )
        plot.addItem(bars)
        return bars

    def _remove_bars(self) -> None:
        for plot, attr in (
            (self._fret_hist, "_fret_bars"),
            (self._intensity_hist, "_intensity_bars"),
        ):
            bars = getattr(self, attr)
            if bars is not None:
                plot.removeItem(bars)
                setattr(self, attr, None)

    # --- lifecycle -----------------------------------------------------------

    def close(self) -> None:
        """Close and release the dock widget."""
        self._widget.close()

    def __enter__(self) -> TraceDock:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()


def trace_from_smd(
    raw: np.ndarray, index: int, *, frame_time: float | None = None, name: str | None = None
) -> TraceView:
    """Build a :class:`TraceView` from an SMD ``raw`` array's molecule ``index``.

    ``raw`` is the ``(n_molecules, n_frames, 2)`` array from
    :func:`tether.idealize.smd.read_smd` (``[..., 0]`` donor, ``[..., 1]``
    acceptor). A convenience for wiring a stored/handed-off trace into the dock.
    """
    donor = np.asarray(raw)[index, :, 0]
    acceptor = np.asarray(raw)[index, :, 1]
    return TraceView(donor=donor, acceptor=acceptor, frame_time=frame_time, name=name)
