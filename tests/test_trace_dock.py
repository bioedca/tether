# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the pyqtgraph trace dock (M2 S1, PRD §7.3 / Appendix C D1).

Two layers, mirroring ``test_movie_panel``:

* **Pure-numpy** checks of the Qt-free value object (:class:`TraceView`), the
  marginal-histogram helper, and the SMD adapter — these run in the default
  matrix because ``tether.gui.trace_dock`` imports pyqtgraph/Qt lazily.
* **``@pytest.mark.gui``** smokes that construct a real :class:`TraceDock` with
  ``qtbot`` and assert the display contract: donor(green)/acceptor(red)/total +
  FRET(blue) curves render a committed fixture trace, the FRET axis is labelled
  **"apparent E"** and pinned to ``0–1``, the seconds/frame-index toggle works,
  and the idealization overlay is a reserved (empty, hidden) placeholder.
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
    from tether.gui.trace_dock import TraceDock

    with TraceDock() as dock:
        qtbot.addWidget(dock.widget)
        # Donor green, acceptor red, FRET blue (PRD §7.3).
        assert _rgb(dock.donor_curve.opts["pen"]) == (0, 170, 0)
        assert _rgb(dock.acceptor_curve.opts["pen"]) == (220, 45, 45)
        assert _rgb(dock.fret_curve.opts["pen"]) == (40, 90, 220)
        # Total present as a third intensity curve, drawn in neutral grey.
        assert _rgb(dock.total_curve.opts["pen"]) == (140, 140, 140)


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
def test_dock_idealization_overlay_reserved_empty_hidden(qtbot) -> None:
    from tether.gui.trace_dock import TraceDock

    trace = TraceView(donor=np.ones(4), acceptor=np.ones(4))
    with TraceDock() as dock:
        qtbot.addWidget(dock.widget)
        dock.set_trace(trace)
        # The step overlay exists but carries no data and stays hidden until M2 S6.
        assert dock.idealization_curve is not None
        assert not dock.idealization_curve.isVisible()
        xdata, _ = dock.idealization_curve.getData()
        assert xdata is None or len(xdata) == 0


@pytest.mark.gui
@_needs_qt
def test_dock_clear_blanks_everything(qtbot) -> None:
    from tether.gui.trace_dock import TraceDock

    trace = TraceView(donor=np.ones(4), acceptor=np.ones(4), frame_time=0.1)
    with TraceDock() as dock:
        qtbot.addWidget(dock.widget)
        dock.set_trace(trace)
        dock.clear()
        assert dock.trace is None
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
