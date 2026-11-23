# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the pyqtgraph trace dock (M2 S1, PRD §7.3 / Appendix C D1).

Two layers, mirroring ``test_movie_panel``:

* **Pure-numpy** checks of the Qt-free value object (:class:`TraceView`), the
  marginal-histogram helper, and the SMD adapter — these run in the default
  matrix because ``tether.gui.trace_dock`` imports pyqtgraph/Qt lazily.
* **``@pytest.mark.gui``** smokes that construct a real :class:`TraceDock` with
  ``qtbot`` and assert the display contract: donor/acceptor/total colours (#636) +
  FRET(blue) curves render a committed fixture trace, the FRET axis is labelled
  **"apparent E"** and pinned to ``0–1``, the seconds/frame-index toggle works,
  and the idealization step overlay is drawn by ``set_idealization`` (empty/hidden
  until a molecule is idealized, cleared when the trace changes).
  pyqtgraph is CPU-rendered (no GL context), so these run headless
  (``QT_QPA_PLATFORM=offscreen``) on all three OSes. Pixel rendering is left to
  the live computer-use smoke.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

from tether.gui.trace_dock import TraceView, _marginal_histogram, trace_from_smd
from tether.idealize.smd import read_smd

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "smd_4mol.hdf5"

# Gate the GUI smokes at COLLECTION time (before the ``qtbot`` fixture resolves),
# the same way test_movie_panel gates napari: find_spec only locates the packages.
_HAS_QT = all(importlib.util.find_spec(m) is not None for m in ("pyqtgraph", "PySide6"))
_needs_qt = pytest.mark.skipif(not _HAS_QT, reason="pyqtgraph/PySide6 not installed")


# --- pure value object + helpers (no Qt) -------------------------------------


def test_traceview_total_and_apparent_e() -> None:
    trace = TraceView(donor=np.array([100.0, 60.0]), acceptor=np.array([0.0, 40.0]))
    np.testing.assert_allclose(trace.total, [100.0, 100.0])
    np.testing.assert_allclose(trace.apparent_e, [0.0, 0.4])
    assert trace.n_frames == 2


def test_traceview_rejects_mismatched_empty_and_non1d() -> None:
    with pytest.raises(ValueError, match="same length"):
        TraceView(donor=np.zeros(3), acceptor=np.zeros(4))
    with pytest.raises(ValueError, match="at least one frame"):
        TraceView(donor=np.zeros(0), acceptor=np.zeros(0))
    with pytest.raises(ValueError, match="1-D"):
        TraceView(donor=np.zeros((2, 2)), acceptor=np.zeros((2, 2)))


def test_traceview_rejects_nonpositive_or_nonfinite_frame_time() -> None:
    # 0, negative, and non-finite (NaN / +inf) frame_times are all rejected at the
    # boundary so a corrupt movie FrameTime can never poison the seconds axis.
    for bad in (0.0, -0.1, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="frame_time"):
            TraceView(donor=np.zeros(2), acceptor=np.zeros(2), frame_time=bad)


def test_traceview_time_axis_seconds_and_frames() -> None:
    trace = TraceView(donor=np.zeros(4), acceptor=np.zeros(4), frame_time=0.25)
    np.testing.assert_allclose(trace.time_axis("frames"), [0, 1, 2, 3])
    np.testing.assert_allclose(trace.time_axis("seconds"), [0.0, 0.25, 0.5, 0.75])


def test_traceview_time_axis_seconds_falls_back_without_frame_time() -> None:
    trace = TraceView(donor=np.zeros(3), acceptor=np.zeros(3), frame_time=None)
    # Requesting seconds with no frame_time returns the frame-index axis, not a
    # fabricated time scale.
    np.testing.assert_allclose(trace.time_axis("seconds"), [0, 1, 2])


def test_marginal_histogram_fret_drops_nan_and_fixes_range() -> None:
    values = np.array([0.1, 0.5, 0.5, np.nan, 0.9])
    counts, centers, width = _marginal_histogram(values, 0.0, 1.0, 40)
    assert counts.sum() == 4  # the NaN is dropped
    assert centers[0] > 0.0 and centers[-1] < 1.0
    assert width == pytest.approx(1.0 / 40)
    # Each bar sits on its own bin: compare the counts with NumPy bin by bin, so a
    # mirrored or shifted histogram (E = 0.1 drawn at 0.9) fails here (#745 GUI-F5).
    ref_counts, ref_edges = np.histogram([0.1, 0.5, 0.5, 0.9], bins=40, range=(0.0, 1.0))
    np.testing.assert_array_equal(counts, ref_counts)
    np.testing.assert_allclose(centers, 0.5 * (ref_edges[:-1] + ref_edges[1:]))


def test_marginal_histogram_all_nan_is_empty_not_error() -> None:
    counts, centers, _ = _marginal_histogram(np.full(5, np.nan), 0.0, 1.0, 10)
    assert counts.sum() == 0
    assert centers.size == 10


def test_trace_from_smd_slices_donor_acceptor() -> None:
    raw = np.arange(2 * 3 * 2, dtype=np.float64).reshape(2, 3, 2)
    trace = trace_from_smd(raw, 1, frame_time=0.1, name="mol-1")
    np.testing.assert_allclose(trace.donor, raw[1, :, 0])
    np.testing.assert_allclose(trace.acceptor, raw[1, :, 1])
    assert trace.frame_time == 0.1
    assert trace.name == "mol-1"


def test_traceview_arrays_are_readonly_copies() -> None:
    # The value object is documented immutable: it must not alias the caller's
    # buffer (trace_from_smd passes a view into an SMD raw array) and its stored
    # arrays must be read-only.
    src_donor = np.array([1.0, 2.0, 3.0])
    trace = TraceView(donor=src_donor, acceptor=np.array([3.0, 2.0, 1.0]))
    src_donor[0] = 999.0
    assert trace.donor[0] == 1.0  # no aliasing
    with pytest.raises(ValueError):  # read-only
        trace.donor[0] = 0.0


def test_traceview_is_hashable_and_identity_compared() -> None:
    # frozen dataclass + numpy fields would make the default __eq__/__hash__ crash
    # (ValueError / TypeError); eq=False gives safe identity semantics instead.
    trace = TraceView(donor=np.zeros(2), acceptor=np.zeros(2))
    assert hash(trace) == hash(trace)
    assert trace == trace
    assert trace != TraceView(donor=np.zeros(2), acceptor=np.zeros(2))
    assert trace in {trace}


# --- GUI smokes (@pytest.mark.gui, need a real QApplication via qtbot) --------


def _rgb(pen: object) -> tuple[int, int, int]:
    import pyqtgraph as pg

    return pg.mkPen(pen).color().getRgb()[:3]


@pytest.mark.gui
@_needs_qt
def test_dock_renders_fixture_trace(qtbot) -> None:
    from tether.gui.trace_dock import TraceDock

    smd = read_smd(FIXTURE)
    trace = trace_from_smd(smd.raw, 0, frame_time=0.1, name="mol-0")

    with TraceDock() as dock:
        qtbot.addWidget(dock.widget)
        dock.set_trace(trace)

        dx, dy = dock.donor_curve.getData()
        assert dy.shape == (trace.n_frames,)
        np.testing.assert_allclose(dy, trace.donor)
        np.testing.assert_allclose(dock.acceptor_curve.getData()[1], trace.acceptor)
        np.testing.assert_allclose(dock.total_curve.getData()[1], trace.total)

        # FRET curve matches apparent E on the finite frames.
        _, fy = dock.fret_curve.getData()
        exp = trace.apparent_e
        finite = np.isfinite(exp)
        np.testing.assert_allclose(fy[finite], exp[finite])


@pytest.mark.gui
@_needs_qt
def test_dock_axis_conventions_and_colors(qtbot) -> None:
    from tether.gui import theme
    from tether.gui.trace_dock import TraceDock

    with TraceDock() as dock:
        qtbot.addWidget(dock.widget)
        # The default is the colour-blind-safe set of the active theme (PRD §7.3, #636):
        # green donor, magenta acceptor, blue FRET, and a neutral total.
        safe = theme.TRACE_PALETTES["safe"][theme.current().name]
        for curve, token in (
            (dock.donor_curve, "donor"),
            (dock.acceptor_curve, "acceptor"),
            (dock.fret_curve, "fret"),
            (dock.total_curve, "total"),
        ):
            assert _rgb(curve.opts["pen"]) == theme.rgba(safe[token])[:3], token
        # The classic preference: donor green, acceptor red, FRET blue, total grey.
        try:
            theme.set_trace_palette("classic")
            assert _rgb(dock.donor_curve.opts["pen"]) == (0, 170, 0)
            assert _rgb(dock.acceptor_curve.opts["pen"]) == (220, 45, 45)
            assert _rgb(dock.fret_curve.opts["pen"]) == (40, 90, 220)
            assert _rgb(dock.total_curve.opts["pen"]) == (140, 140, 140)
        finally:
            theme.set_trace_palette(theme.DEFAULT_TRACE_PALETTE)


@pytest.mark.gui
@_needs_qt
def test_dock_fret_axis_labeled_apparent_e_and_pinned_0_1(qtbot) -> None:
    from tether.gui.trace_dock import TraceDock

    trace = TraceView(donor=np.array([90.0, 50.0, 10.0]), acceptor=np.array([10.0, 50.0, 90.0]))
    with TraceDock() as dock:
        qtbot.addWidget(dock.widget)
        dock.set_trace(trace)
        assert dock.fret_plot.getAxis("left").labelText == "apparent E"
        ymin, ymax = dock.fret_plot.getViewBox().viewRange()[1]
        assert ymin == pytest.approx(0.0, abs=1e-6)
        assert ymax == pytest.approx(1.0, abs=1e-6)


@pytest.mark.gui
@_needs_qt
def test_dock_time_toggle_seconds_and_frames(qtbot) -> None:
    from tether.gui.trace_dock import TraceDock

    trace = TraceView(donor=np.zeros(5), acceptor=np.zeros(5), frame_time=0.2)
    with TraceDock() as dock:
        qtbot.addWidget(dock.widget)
        dock.set_trace(trace)

        # Defaults to seconds when a frame_time is present; toggle is enabled/checked.
        assert dock.time_mode == "seconds"
        assert dock.time_checkbox.isEnabled()
        assert dock.time_checkbox.isChecked()
        np.testing.assert_allclose(dock.donor_curve.getData()[0], [0.0, 0.2, 0.4, 0.6, 0.8])

        dock.set_time_mode("frames")
        assert dock.time_mode == "frames"
        assert not dock.time_checkbox.isChecked()
        np.testing.assert_allclose(dock.donor_curve.getData()[0], [0, 1, 2, 3, 4])

        # Clicking the checkbox drives the axis back to seconds.
        dock.time_checkbox.setChecked(True)
        assert dock.time_mode == "seconds"
        np.testing.assert_allclose(dock.donor_curve.getData()[0], [0.0, 0.2, 0.4, 0.6, 0.8])


@pytest.mark.gui
@_needs_qt
def test_dock_bottom_axis_label_follows_time_mode(qtbot) -> None:
    # The x data alone cannot tell "Frame" from "Time (s)": frame numbers shown
    # under the label "Time (s)" read as seconds. Read the label itself (#745 GUI-F5).
    from tether.gui.trace_dock import TraceDock

    trace = TraceView(donor=np.zeros(5), acceptor=np.zeros(5), frame_time=0.2)
    with TraceDock() as dock:
        qtbot.addWidget(dock.widget)
        dock.set_trace(trace)
        axis = dock.fret_plot.getAxis("bottom")
        assert dock.time_mode == "seconds"
        assert axis.labelText == "Time"
        assert axis.labelUnits == "s"

        dock.set_time_mode("frames")
        assert axis.labelText == "Frame"
        assert axis.labelUnits == ""

        dock.set_time_mode("seconds")
        assert axis.labelText == "Time"
        assert axis.labelUnits == "s"


@pytest.mark.gui
@_needs_qt
def test_dock_toggle_disabled_without_frame_time(qtbot) -> None:
    from tether.gui.trace_dock import TraceDock

    trace = TraceView(donor=np.zeros(3), acceptor=np.zeros(3), frame_time=None)
    with TraceDock() as dock:
        qtbot.addWidget(dock.widget)
        dock.set_trace(trace)
        assert dock.time_mode == "frames"
        assert not dock.time_checkbox.isEnabled()
        np.testing.assert_allclose(dock.donor_curve.getData()[0], [0, 1, 2])


@pytest.mark.gui
@_needs_qt
def test_dock_idealization_overlay_empty_and_hidden_before_idealize(qtbot) -> None:
    from tether.gui.trace_dock import TraceDock

    trace = TraceView(donor=np.ones(4), acceptor=np.ones(4))
    with TraceDock() as dock:
        qtbot.addWidget(dock.widget)
        dock.set_trace(trace)
        # The step overlay exists but carries no data and stays hidden until a
        # molecule is idealized via set_idealization.
        assert dock.idealization_curve is not None
        assert not dock.idealization_curve.isVisible()
        assert dock.idealized_path is None
        xdata, _ = dock.idealization_curve.getData()
        assert xdata is None or len(xdata) == 0


@pytest.mark.gui
@_needs_qt
def test_dock_set_idealization_draws_centered_step(qtbot) -> None:
    from tether.gui.trace_dock import TraceDock

    # A full-window two-level idealized path over a 6-frame trace (frame_time 0.1s).
    trace = TraceView(donor=np.ones(6), acceptor=np.ones(6), frame_time=0.1)
    path = np.array([0.3, 0.3, 0.3, 0.7, 0.7, 0.7])
    with TraceDock() as dock:
        qtbot.addWidget(dock.widget)
        dock.set_trace(trace)
        dock.set_idealization(path)

        assert dock.idealization_curve.isVisible()
        assert dock.idealization_curve.opts["stepMode"] == "center"
        xe, ye = dock.idealization_curve.getData()
        # stepMode="center": n levels bracketed by n+1 edges centred on the frames.
        np.testing.assert_allclose(ye, path)
        assert len(xe) == len(ye) + 1
        # Seconds axis: frames 0..5 at 0.1s spacing -> half-step-overhung edges.
        np.testing.assert_allclose(xe, [-0.05, 0.05, 0.15, 0.25, 0.35, 0.45, 0.55])
        np.testing.assert_allclose(dock.idealized_path, path)


@pytest.mark.gui
@_needs_qt
def test_dock_set_idealization_draws_only_finite_window(qtbot) -> None:
    from tether.gui.trace_dock import TraceDock

    # NaN outside the analysis window (frames 1..4); only that span is drawn.
    trace = TraceView(donor=np.ones(6), acceptor=np.ones(6), frame_time=0.1)
    path = np.array([np.nan, 0.3, 0.3, 0.7, 0.7, np.nan])
    with TraceDock() as dock:
        qtbot.addWidget(dock.widget)
        dock.set_trace(trace)
        dock.set_idealization(path)

        xe, ye = dock.idealization_curve.getData()
        np.testing.assert_allclose(ye, [0.3, 0.3, 0.7, 0.7])
        assert len(xe) == len(ye) + 1
        # Edges bracket the in-window frames (0.1..0.4 s), not the NaN ends.
        np.testing.assert_allclose(xe, [0.05, 0.15, 0.25, 0.35, 0.45])


@pytest.mark.gui
@_needs_qt
def test_dock_idealization_overlay_relays_on_time_toggle(qtbot) -> None:
    from tether.gui.trace_dock import TraceDock

    trace = TraceView(donor=np.ones(6), acceptor=np.ones(6), frame_time=0.1)
    path = np.array([0.3, 0.3, 0.3, 0.7, 0.7, 0.7])
    with TraceDock() as dock:
        qtbot.addWidget(dock.widget)
        dock.set_trace(trace)
        dock.set_idealization(path)
        np.testing.assert_allclose(
            dock.idealization_curve.getData()[0], [-0.05, 0.05, 0.15, 0.25, 0.35, 0.45, 0.55]
        )
        # Toggling to the frame-index axis re-lays the overlay in frame units.
        dock.set_time_mode("frames")
        xe, ye = dock.idealization_curve.getData()
        np.testing.assert_allclose(ye, path)
        np.testing.assert_allclose(xe, [-0.5, 0.5, 1.5, 2.5, 3.5, 4.5, 5.5])


@pytest.mark.gui
@_needs_qt
def test_dock_set_idealization_all_nan_hides(qtbot) -> None:
    from tether.gui.trace_dock import TraceDock

    trace = TraceView(donor=np.ones(4), acceptor=np.ones(4))
    with TraceDock() as dock:
        qtbot.addWidget(dock.widget)
        dock.set_trace(trace)
        dock.set_idealization(np.full(4, np.nan))
        assert not dock.idealization_curve.isVisible()
        xe, _ = dock.idealization_curve.getData()
        assert xe is None or len(xe) == 0


@pytest.mark.gui
@_needs_qt
def test_dock_set_idealization_validates_trace_and_length(qtbot) -> None:
    from tether.gui.trace_dock import TraceDock

    with TraceDock() as dock:
        qtbot.addWidget(dock.widget)
        # No trace shown yet -> the overlay is always trace-relative.
        with pytest.raises(RuntimeError, match="set_trace"):
            dock.set_idealization(np.zeros(4))
        dock.set_trace(TraceView(donor=np.ones(4), acceptor=np.ones(4)))
        with pytest.raises(ValueError, match="length 4"):
            dock.set_idealization(np.zeros(3))


@pytest.mark.gui
@_needs_qt
def test_dock_new_trace_clears_stale_idealization(qtbot) -> None:
    from tether.gui.trace_dock import TraceDock

    a = TraceView(donor=np.ones(6), acceptor=np.ones(6), frame_time=0.1)
    b = TraceView(donor=np.ones(5), acceptor=np.ones(5), frame_time=0.1)
    with TraceDock() as dock:
        qtbot.addWidget(dock.widget)
        dock.set_trace(a)
        dock.set_idealization(np.array([0.3, 0.3, 0.3, 0.7, 0.7, 0.7]))
        assert dock.idealization_curve.isVisible()
        # Switching molecules drops the previous idealization (never bleeds over).
        dock.set_trace(b)
        assert dock.idealized_path is None
        assert not dock.idealization_curve.isVisible()


@pytest.mark.gui
@_needs_qt
def test_dock_clear_idealization_keeps_trace(qtbot) -> None:
    from tether.gui.trace_dock import TraceDock

    trace = TraceView(donor=np.ones(6), acceptor=np.ones(6), frame_time=0.1)
    with TraceDock() as dock:
        qtbot.addWidget(dock.widget)
        dock.set_trace(trace)
        dock.set_idealization(np.array([0.3, 0.3, 0.3, 0.7, 0.7, 0.7]))
        dock.clear_idealization()
        # Overlay gone, but the trace + FRET curve remain displayed.
        assert dock.idealized_path is None
        assert not dock.idealization_curve.isVisible()
        assert dock.trace is trace
        assert len(dock.fret_curve.getData()[1]) == trace.n_frames


@pytest.mark.gui
@_needs_qt
def test_dock_clear_blanks_everything(qtbot) -> None:
    from tether.gui.trace_dock import TraceDock

    trace = TraceView(donor=np.ones(4), acceptor=np.ones(4), frame_time=0.1)
    with TraceDock() as dock:
        qtbot.addWidget(dock.widget)
        dock.set_trace(trace)
        dock.set_idealization(np.array([0.3, 0.3, 0.7, 0.7]))
        dock.clear()
        assert dock.trace is None
        assert dock.idealized_path is None
        assert not dock.idealization_curve.isVisible()
        for curve in (dock.donor_curve, dock.acceptor_curve, dock.total_curve, dock.fret_curve):
            xdata, _ = curve.getData()
            assert xdata is None or len(xdata) == 0


@pytest.mark.gui
@_needs_qt
def test_dock_fret_curve_breaks_at_zero_total_frame(qtbot) -> None:
    from tether.gui.trace_dock import TraceDock

    # Frame 1 has zero total intensity -> apparent E is undefined there. The FRET
    # curve must carry a NaN gap (drawn as a break via connect="finite"), never a
    # fabricated interpolated value across it (DoD: apparent-E is never fabricated).
    trace = TraceView(donor=np.array([100.0, 0.0, 50.0]), acceptor=np.array([0.0, 0.0, 50.0]))
    with TraceDock() as dock:
        qtbot.addWidget(dock.widget)
        dock.set_trace(trace)
        _, fy = dock.fret_curve.getData()
        assert np.isnan(fy[1])
        assert np.isfinite(fy[0]) and np.isfinite(fy[2])
        assert dock.fret_curve.opts["connect"] == "finite"


@pytest.mark.gui
@_needs_qt
def test_dock_histograms_replace_on_update_and_clear(qtbot) -> None:
    import pyqtgraph as pg

    from tether.gui.trace_dock import TraceDock

    def n_bars(plot) -> int:
        return sum(isinstance(item, pg.BarGraphItem) for item in plot.items)

    t1 = TraceView(donor=np.ones(4), acceptor=np.ones(4))
    t2 = TraceView(donor=np.arange(1.0, 5.0), acceptor=np.arange(4.0, 0.0, -1.0))
    with TraceDock() as dock:
        qtbot.addWidget(dock.widget)
        dock.set_trace(t1)
        assert n_bars(dock.fret_histogram) == 1
        assert n_bars(dock.intensity_histogram) == 1
        # A second trace replaces the marginals rather than stacking new bars.
        dock.set_trace(t2)
        assert n_bars(dock.fret_histogram) == 1
        assert n_bars(dock.intensity_histogram) == 1
        # clear() really blanks "everything" — including the histogram bars.
        dock.clear()
        assert n_bars(dock.fret_histogram) == 0
        assert n_bars(dock.intensity_histogram) == 0


def _bars(plot):
    import pyqtgraph as pg

    (bars,) = [item for item in plot.items if isinstance(item, pg.BarGraphItem)]
    return bars


@pytest.mark.gui
@_needs_qt
def test_dock_marginals_draw_the_histograms_of_the_trace(qtbot) -> None:
    # The marginals are horizontal bars: bar length (``width``) is the count and
    # ``y`` the bin centre. Compare both with NumPy on the quantity each marginal
    # is documented to show — apparent E on 0-1 for the FRET panel, the total
    # intensity (auto range) for the intensity panel — so a marginal built from
    # the wrong signal, or drawn mirrored, fails (#745 GUI-F5). Donor, acceptor
    # and total are chosen so that no two of them share a histogram.
    from tether.gui.trace_dock import _FRET_RANGE, _N_HIST_BINS, TraceDock

    donor = np.array([90.0, 80.0, 70.0, 60.0, 20.0, 10.0, 15.0, 85.0])
    acceptor = np.array([10.0, 20.0, 60.0, 90.0, 100.0, 150.0, 30.0, 5.0])
    trace = TraceView(donor=donor, acceptor=acceptor)
    with TraceDock() as dock:
        qtbot.addWidget(dock.widget)
        dock.set_trace(trace)

        for plot, values, hist_range in (
            (dock.fret_histogram, trace.apparent_e, _FRET_RANGE),
            (dock.intensity_histogram, trace.total, None),
        ):
            ref_counts, ref_edges = np.histogram(values, bins=_N_HIST_BINS, range=hist_range)
            opts = _bars(plot).opts
            np.testing.assert_array_equal(np.asarray(opts["width"]), ref_counts)
            np.testing.assert_allclose(
                np.asarray(opts["y"]), 0.5 * (ref_edges[:-1] + ref_edges[1:])
            )

        # A second trace re-draws both marginals from its own data (the bars are
        # reused through setOpts, so the new values must reach the same item).
        # The total of t2 has another range, so the intensity bin centres move too.
        t2 = TraceView(donor=acceptor, acceptor=donor * 3.0)
        dock.set_trace(t2)
        for plot, values, hist_range in (
            (dock.fret_histogram, t2.apparent_e, _FRET_RANGE),
            (dock.intensity_histogram, t2.total, None),
        ):
            ref_counts, ref_edges = np.histogram(values, bins=_N_HIST_BINS, range=hist_range)
            opts = _bars(plot).opts
            np.testing.assert_array_equal(np.asarray(opts["width"]), ref_counts)
            np.testing.assert_allclose(
                np.asarray(opts["y"]), 0.5 * (ref_edges[:-1] + ref_edges[1:])
            )


# --- cross-correlation panel (#363) -------------------------------------------


def _anticorrelated(n_frames: int, *, frame_time: float | None = 0.1) -> TraceView:
    """A noisy two-level trace whose donor and acceptor move in anti-phase."""
    rng = np.random.default_rng(363)
    level = np.where((np.arange(n_frames) // 25) % 2 == 0, 0.3, 0.7)
    total = 1000.0
    acceptor = level * total + rng.normal(0.0, 20.0, n_frames)
    donor = (1.0 - level) * total + rng.normal(0.0, 20.0, n_frames)
    return TraceView(donor=donor, acceptor=acceptor, frame_time=frame_time)


@pytest.mark.gui
@_needs_qt
def test_dock_crosscorr_panel_draws_the_library_curve(qtbot) -> None:
    from tether.analysis.crosscorr import cross_correlation
    from tether.gui.trace_dock import DEFAULT_CROSSCORR_MAX_LAG, TraceDock

    trace = _anticorrelated(400)
    with TraceDock() as dock:
        qtbot.addWidget(dock.widget)
        # The panel is the grid's third row, under the time-series column.
        assert dock.graphics.ci.getItem(2, 0) is dock.crosscorr_plot
        dock.set_trace(trace)

        expected = cross_correlation(trace.donor, trace.acceptor, max_lag=DEFAULT_CROSSCORR_MAX_LAG)
        cc = dock.crosscorr
        assert cc is not None
        assert dock.crosscorr_message is None
        np.testing.assert_array_equal(cc.lags, expected.lags)
        np.testing.assert_allclose(cc.values, expected.values)
        assert cc.lag0 < 0  # anti-phase channels: the honest sign survives display
        x, y = dock.crosscorr_curve.getData()
        np.testing.assert_allclose(y, expected.values)
        np.testing.assert_allclose(x, expected.lags * 0.1)  # seconds mode by default
        assert x[0] == pytest.approx(-DEFAULT_CROSSCORR_MAX_LAG * 0.1)
        # The Pearson axis is pinned to [-1, 1].
        ymin, ymax = dock.crosscorr_plot.getViewBox().viewRange()[1]
        assert (ymin, ymax) == (pytest.approx(-1.0), pytest.approx(1.0))


@pytest.mark.gui
@_needs_qt
def test_dock_crosscorr_is_driven_by_the_analysis_function(qtbot, monkeypatch) -> None:
    import tether.analysis.crosscorr as crosscorr
    from tether.gui.trace_dock import TraceDock

    calls: list[int | None] = []
    real = crosscorr.cross_correlation

    def spy(donor, acceptor, **kwargs):
        calls.append(kwargs.get("max_lag"))
        return real(donor, acceptor, **kwargs)

    monkeypatch.setattr(crosscorr, "cross_correlation", spy)
    with TraceDock(crosscorr_max_lag=7) as dock:
        qtbot.addWidget(dock.widget)
        dock.set_trace(_anticorrelated(100))
        assert calls == [7]
        assert dock.crosscorr is not None
        assert dock.crosscorr.lags[0] == -7
        # A time-mode toggle re-lays the axis; it does not recompute the curve.
        dock.set_time_mode("frames")
        assert calls == [7]


@pytest.mark.gui
@_needs_qt
def test_dock_crosscorr_lag_axis_follows_time_mode(qtbot) -> None:
    from tether.gui.trace_dock import TraceDock

    with TraceDock(crosscorr_max_lag=10) as dock:
        qtbot.addWidget(dock.widget)
        dock.set_trace(_anticorrelated(200, frame_time=0.05))
        axis = dock.crosscorr_plot.getAxis("bottom")
        assert axis.labelText == "Lag"
        assert axis.labelUnits == "s"
        np.testing.assert_allclose(dock.crosscorr_curve.getData()[0], np.arange(-10, 11) * 0.05)
        assert dock.crosscorr_plot.getViewBox().viewRange()[0] == [
            pytest.approx(-0.5),
            pytest.approx(0.5),
        ]

        dock.time_checkbox.setChecked(False)  # the user's frame-index toggle
        assert axis.labelUnits == "frames"
        np.testing.assert_allclose(dock.crosscorr_curve.getData()[0], np.arange(-10, 11))
        assert dock.crosscorr_plot.getViewBox().viewRange()[0] == [
            pytest.approx(-10.0),
            pytest.approx(10.0),
        ]

        # A trace with no frame_time can only show lags in frames.
        dock.set_time_mode("seconds")
        dock.set_trace(_anticorrelated(200, frame_time=None))
        assert axis.labelUnits == "frames"
        np.testing.assert_allclose(dock.crosscorr_curve.getData()[0], np.arange(-10, 11))


@pytest.mark.gui
@_needs_qt
def test_dock_crosscorr_is_not_x_linked_to_the_time_series(qtbot) -> None:
    from tether.gui.trace_dock import TraceDock

    with TraceDock() as dock:
        qtbot.addWidget(dock.widget)
        dock.set_trace(_anticorrelated(400))
        vb = dock.crosscorr_plot.getViewBox()
        assert vb.linkedView(vb.XAxis) is None
        # Zooming the time series leaves the lag axis where it was.
        before = vb.viewRange()[0]
        dock.fret_plot.setXRange(10.0, 20.0, padding=0)
        assert vb.viewRange()[0] == before


@pytest.mark.gui
@_needs_qt
@pytest.mark.parametrize("n_frames", [1, 2, 10, 50])
def test_dock_crosscorr_too_short_trace_shows_empty_state(qtbot, n_frames: int) -> None:
    from tether.gui.trace_dock import TraceDock

    with TraceDock(crosscorr_max_lag=50) as dock:
        qtbot.addWidget(dock.widget)
        dock.set_trace(_anticorrelated(n_frames))  # must not raise
        assert dock.crosscorr is None
        assert dock.crosscorr_message is not None
        assert "too short" in dock.crosscorr_message
        assert f"{n_frames} frame" in dock.crosscorr_message
        assert (
            dock.crosscorr_curve.getData()[1] is None or dock.crosscorr_curve.getData()[1].size == 0
        )
        assert dock.crosscorr_plot.listDataItems() == [dock.crosscorr_curve]
        # The note is drawn in the panel, and the other panels still show the trace.
        assert dock._crosscorr_text.isVisible()
        assert dock._crosscorr_text.textItem.toPlainText() == dock.crosscorr_message
        assert dock.donor_curve.getData()[1].size == n_frames


@pytest.mark.gui
@_needs_qt
def test_dock_crosscorr_exactly_long_enough_draws(qtbot) -> None:
    from tether.gui.trace_dock import TraceDock

    with TraceDock(crosscorr_max_lag=50) as dock:
        qtbot.addWidget(dock.widget)
        dock.set_trace(_anticorrelated(51))
        assert dock.crosscorr is not None
        assert dock.crosscorr.lags.tolist() == list(range(-50, 51))
        assert not dock._crosscorr_text.isVisible()


@pytest.mark.gui
@_needs_qt
def test_dock_crosscorr_non_finite_and_flat_traces_show_empty_state(qtbot) -> None:
    from tether.gui.trace_dock import TraceDock

    with TraceDock(crosscorr_max_lag=5) as dock:
        qtbot.addWidget(dock.widget)
        gappy = np.full(100, 500.0) + np.arange(100.0)
        gappy[40] = np.nan
        dock.set_trace(TraceView(donor=gappy, acceptor=np.arange(100.0)))
        assert dock.crosscorr is None
        assert "non-finite" in (dock.crosscorr_message or "")

        dock.set_trace(TraceView(donor=np.full(100, 300.0), acceptor=np.arange(100.0)))
        assert dock.crosscorr is None
        assert "constant" in (dock.crosscorr_message or "")


@pytest.mark.gui
@_needs_qt
def test_dock_crosscorr_new_trace_replaces_and_clear_blanks(qtbot) -> None:
    from tether.gui.trace_dock import TraceDock

    with TraceDock(crosscorr_max_lag=20) as dock:
        qtbot.addWidget(dock.widget)
        dock.set_trace(_anticorrelated(300))
        assert dock.crosscorr is not None
        dock.set_trace(_anticorrelated(5))  # a stale curve must not survive
        assert dock.crosscorr is None
        assert (
            dock.crosscorr_curve.getData()[1] is None or dock.crosscorr_curve.getData()[1].size == 0
        )
        dock.set_trace(_anticorrelated(300))
        assert dock.crosscorr is not None
        assert not dock._crosscorr_text.isVisible()

        dock.clear()
        assert dock.crosscorr is None
        assert dock.crosscorr_message is None
        assert not dock._crosscorr_text.isVisible()
        assert (
            dock.crosscorr_curve.getData()[1] is None or dock.crosscorr_curve.getData()[1].size == 0
        )


@pytest.mark.gui
@_needs_qt
@pytest.mark.parametrize("bad", [0, -3, True, 2.5, "10"])
def test_dock_crosscorr_max_lag_must_be_a_positive_integer(qtbot, bad: object) -> None:
    from tether.gui.trace_dock import TraceDock

    with pytest.raises(ValueError, match="crosscorr_max_lag"):
        TraceDock(crosscorr_max_lag=bad)  # type: ignore[arg-type]


def test_curation_guide_states_the_crosscorr_lag_default() -> None:
    """The how-to page prints the panel's lag span; keep it equal to the constant."""
    from tether.gui.trace_dock import DEFAULT_CROSSCORR_MAX_LAG

    page = Path(__file__).resolve().parents[1] / "docs" / "how-to" / "curate-traces.md"
    text = " ".join(page.read_text(encoding="utf-8").split())
    assert f"spans {DEFAULT_CROSSCORR_MAX_LAG} frames either side of 0" in text
    assert f"shorter than {DEFAULT_CROSSCORR_MAX_LAG + 1} frames" in text


# --- the stored analysis window (#359) -----------------------------------------------


@pytest.mark.parametrize("window", [(5, 5), (6, 5), (-1, 4), (0, 11), (0.5, 4), (True, 4), (1,)])
def test_traceview_refuses_a_window_outside_the_trace(window) -> None:
    with pytest.raises(ValueError, match="analysis_window"):
        TraceView(donor=np.ones(10), acceptor=np.ones(10), analysis_window=window)


def test_traceview_window_is_an_int_pair() -> None:
    trace = TraceView(donor=np.ones(10), acceptor=np.ones(10), analysis_window=(np.int64(2), 9))
    assert trace.analysis_window == (2, 9)
    assert all(type(bound) is int for bound in trace.analysis_window)


@pytest.mark.gui
@_needs_qt
def test_dock_draws_the_window_and_follows_an_edit(qtbot) -> None:
    from tether.gui.trace_dock import TraceDock

    with TraceDock(seconds_by_default=False) as dock:
        qtbot.addWidget(dock.widget)
        with pytest.raises(RuntimeError):
            dock.set_analysis_window((0, 1))
        dock.set_trace(TraceView(donor=np.ones(20), acceptor=np.ones(20)))
        assert dock.analysis_window is None
        assert not any(line.isVisible() for line in dock.window_lines)

        dock.set_idealization(np.full(20, 0.4))
        dock.set_analysis_window((3, 12))
        assert [line.value() for line in dock.window_lines] == [2.5, 11.5]
        x, y = dock.idealization_curve.getData()
        assert (x[0], x[-1], y.size) == (2.5, 11.5, 9)
        with pytest.raises(ValueError):
            dock.set_analysis_window((3, 21))
        assert dock.analysis_window == (3, 12)  # a refused window changes nothing

        dock.set_analysis_window(None)
        assert not any(line.isVisible() for line in dock.window_lines)
        assert dock.idealization_curve.getData()[1].size == 20

        dock.set_trace(
            TraceView(
                donor=np.ones(20), acceptor=np.ones(20), frame_time=0.5, analysis_window=(4, 8)
            )
        )
        dock.set_time_mode("seconds")
        assert [line.value() for line in dock.window_lines] == [1.75, 3.75]
        dock.clear()
        assert dock.analysis_window is None
        assert not any(line.isVisible() for line in dock.window_lines)
