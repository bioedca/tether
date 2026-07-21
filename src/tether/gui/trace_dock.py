# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""pyqtgraph trace dock — the per-trace curation surface (PRD §7.3, Appendix C D1).

:class:`TraceDock` is the keyboard-driven per-trace viewer at the heart of the
FR-ROUNDTRIP browser: donor/acceptor/total intensity + the FRET time-series with
its marginal histograms. It is tMAVEN's per-trace viewer (Appendix C, **D1**)
reimagined as Tether's curation surface, so it keeps the familiar conventions —
**donor green / acceptor red / FRET blue**, the FRET axis fixed to ``0–1``, the
x-axis in **seconds** (from the linked ``/movies`` row's ``frame_time``, when the
movie declares one) with a **frame-index toggle**, and an idealization **step
overlay** drawn over the FRET panel.

At the MVP the FRET axis reads **"apparent E"**: the uncorrected proximity ratio
``A / (D + A)`` (see :func:`tether.fret.apparent_fret`); leakage/gamma
corrections land at M3. The idealization **step overlay** is populated by
one-click vbFRET (M2 S6) through :meth:`TraceDock.set_idealization`; it stays
empty/hidden until a molecule is idealized, and is cleared when a new trace is
shown so a stale path never bleeds onto the next molecule. tMAVEN visual-parity
of this plot is verified with the M6 seven-plot gallery (§10), not in this PR.

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
from typing import TYPE_CHECKING, Any, Literal

import numpy as np

from tether.fret.efficiency import apparent_fret

if TYPE_CHECKING:
    # Type-only imports (never executed at runtime, so the module stays Qt-free to
    # import): give the accessors real pyqtgraph/Qt types for consumers/IDEs.
    import pyqtgraph as pg
    from pyqtgraph.Qt import QtWidgets

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


@dataclass(frozen=True, eq=False)
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
        Optional display label for the dock title / legend.
    molecule_key
        Optional ``/molecules`` identity (§5.1) of the molecule this trace came
        from. Carried so the shell's one-click idealize (``I``) can resolve the
        selected trace back to its store row; ``None`` for a synthetic trace with
        no backing store.
    """

    donor: np.ndarray
    acceptor: np.ndarray
    frame_time: float | None = None
    name: str | None = None
    molecule_key: str | None = None

    def __post_init__(self) -> None:
        # Defensive copies (``np.array`` always copies), so the stored arrays never
        # alias the caller's buffer — e.g. ``trace_from_smd`` passes a *view* into an
        # SMD ``raw`` array. Frozen read-only below makes the "immutable value object"
        # claim true: neither an upstream mutation nor ``trace.donor[i] = x`` changes it.
        donor = np.array(self.donor, dtype=np.float64)
        acceptor = np.array(self.acceptor, dtype=np.float64)
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
        donor.setflags(write=False)
        acceptor.setflags(write=False)
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


def _step_edges(centers: np.ndarray, step: float) -> np.ndarray:
    """``n`` frame x-positions → ``n + 1`` edges centred on each (for ``stepMode="center"``).

    Each edge sits halfway between adjacent centres, with a half-``step`` overhang at
    both ends, so the idealized level for frame ``i`` is drawn as a horizontal segment
    centred on that frame — aligned with the FRET curve's per-frame samples. ``step``
    is the (uniform) axis spacing (``frame_time`` in seconds mode, ``1.0`` in frames).
    """
    centers = np.asarray(centers, dtype=np.float64)
    n = centers.size
    if n == 1:
        half = step / 2.0
        return np.array([centers[0] - half, centers[0] + half])
    edges = np.empty(n + 1, dtype=np.float64)
    edges[1:-1] = 0.5 * (centers[:-1] + centers[1:])
    edges[0] = centers[0] - (edges[1] - centers[0])
    edges[-1] = centers[-1] + (centers[-1] - edges[-2])
    return edges


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
        # The current idealization step path (per-frame FRET level, NaN outside the
        # analysis window), or None when nothing is idealized. Kept so a seconds/
        # frame-index toggle re-lays the overlay on the new x-axis (see _render).
        self._idealized: np.ndarray | None = None
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
    def widget(self) -> QtWidgets.QWidget:
        """The embeddable ``QWidget`` (checkbox toggle + plot grid)."""
        return self._widget

    @property
    def graphics(self) -> pg.GraphicsLayoutWidget:
        """The underlying ``pyqtgraph.GraphicsLayoutWidget``."""
        return self._graphics

    @property
    def time_checkbox(self) -> QtWidgets.QCheckBox:
        """The seconds/frame-index toggle ``QCheckBox``."""
        return self._time_checkbox

    @property
    def intensity_plot(self) -> pg.PlotItem:
        return self._intensity_plot

    @property
    def fret_plot(self) -> pg.PlotItem:
        return self._fret_plot

    @property
    def intensity_histogram(self) -> pg.PlotItem:
        return self._intensity_hist

    @property
    def fret_histogram(self) -> pg.PlotItem:
        return self._fret_hist

    @property
    def donor_curve(self) -> pg.PlotDataItem:
        return self._donor_curve

    @property
    def acceptor_curve(self) -> pg.PlotDataItem:
        return self._acceptor_curve

    @property
    def total_curve(self) -> pg.PlotDataItem:
        return self._total_curve

    @property
    def fret_curve(self) -> pg.PlotDataItem:
        return self._fret_curve

    @property
    def idealization_curve(self) -> pg.PlotDataItem:
        """The idealized-path step overlay (empty/hidden until :meth:`set_idealization`)."""
        return self._idealization_curve

    @property
    def idealized_path(self) -> np.ndarray | None:
        """The current per-frame idealization overlay array, or ``None`` if unset."""
        return self._idealized

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
        # A new molecule invalidates any prior idealization overlay (_render below
        # hides it via _render_idealization); one-click idealize repopulates it.
        self._idealized = None

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
        self._idealized = None
        for curve in (self._donor_curve, self._acceptor_curve, self._total_curve, self._fret_curve):
            curve.setData([], [])
        self._idealization_curve.setData([], [])
        self._idealization_curve.setVisible(False)
        self._remove_bars()

    def set_idealization(self, idealized: np.ndarray) -> None:
        """Draw the idealized FRET **step overlay** over the FRET panel (§7.4).

        ``idealized`` is the per-frame idealized FRET level for the *current* trace
        (the row :func:`tether.project.idealize.idealize_molecules` returns), NaN
        outside the molecule's analysis window. It must be a 1-D array matching the
        displayed trace length. Only the (contiguous) in-window finite span is
        drawn, as a centred step aligned to the FRET curve's x-axis; a toggle
        between the seconds and frame-index axes re-lays it. Raises if no trace is
        shown yet or the length disagrees — the overlay is always trace-relative.
        """
        if self._trace is None:
            raise RuntimeError("set_trace before its idealization overlay")
        # Copy + freeze (like TraceView) so the overlay never aliases the caller's
        # buffer: a later mutation of the passed array must not change what the dock
        # re-lays on the next toggle, and idealized_path hands back a read-only view.
        arr = np.array(idealized, dtype=np.float64, copy=True)
        if arr.ndim != 1 or arr.size != self._trace.n_frames:
            raise ValueError(
                "idealized must be a 1-D per-frame array of length "
                f"{self._trace.n_frames}, got shape {arr.shape}"
            )
        arr.setflags(write=False)
        self._idealized = arr
        self._render_idealization()

    def clear_idealization(self) -> None:
        """Hide the idealization step overlay (keeps the trace displayed)."""
        self._idealized = None
        self._idealization_curve.setData([], [])
        self._idealization_curve.setVisible(False)

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
        # Re-lay the idealization overlay on the (possibly new) x-axis so a
        # seconds/frame-index toggle moves it in lock-step with the FRET curve.
        self._render_idealization()

    def _axis_step(self) -> float:
        """The x-axis spacing between adjacent frames in the effective time mode."""
        if (
            self.time_mode == "seconds"
            and self._trace is not None
            and self._trace.frame_time is not None
        ):
            return float(self._trace.frame_time)
        return 1.0

    def _render_idealization(self) -> None:
        """Draw ``self._idealized`` as a centred step over the FRET panel, or hide it.

        Uses ``stepMode="center"`` (pyqtgraph 0.14: ``len(x) == len(y) + 1``), so the
        in-window finite levels are bracketed by ``n + 1`` edges centred on the same
        frame positions as the FRET curve. Nothing to draw (no overlay, no trace, or
        an all-NaN path) hides the reserved curve.
        """
        arr = self._idealized
        if arr is None or self._trace is None or not np.isfinite(arr).any():
            self._idealization_curve.setData([], [])
            self._idealization_curve.setVisible(False)
            return
        # vbFRET idealizes one contiguous analysis window, so the finite span is a
        # single block; take first..last finite frame as that window.
        finite = np.flatnonzero(np.isfinite(arr))
        lo, hi = int(finite[0]), int(finite[-1]) + 1
        x = self._trace.time_axis(self.time_mode)[lo:hi]
        y = arr[lo:hi]
        edges = _step_edges(x, self._axis_step())
        self._idealization_curve.setData(edges, y)
        self._idealization_curve.setVisible(True)

    def _draw_bars(
        self, plot: Any, existing: Any, counts: np.ndarray, centers: np.ndarray, width: float
    ) -> Any:
        """Update ``existing`` horizontal bars on ``plot`` in place, or create them.

        Reusing the ``BarGraphItem`` via ``setOpts`` (rather than remove + recreate)
        avoids tearing down and rebuilding two graphics items on every trace step —
        the dock is stepped through many molecules quickly.
        """
        if existing is not None:
            existing.setOpts(x0=0.0, width=counts, y=centers, height=width * 0.9)
            return existing
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
    raw: np.ndarray,
    index: int,
    *,
    frame_time: float | None = None,
    name: str | None = None,
    molecule_key: str | None = None,
) -> TraceView:
    """Build a :class:`TraceView` from an SMD ``raw`` array's molecule ``index``.

    ``raw`` is the ``(n_molecules, n_frames, 2)`` array from
    :func:`tether.idealize.smd.read_smd` (``[..., 0]`` donor, ``[..., 1]``
    acceptor). A convenience for wiring a stored/handed-off trace into the dock;
    pass ``molecule_key`` to keep the trace's store identity for one-click idealize.
    """
    donor = np.asarray(raw)[index, :, 0]
    acceptor = np.asarray(raw)[index, :, 1]
    return TraceView(
        donor=donor, acceptor=acceptor, frame_time=frame_time, name=name, molecule_key=molecule_key
    )
