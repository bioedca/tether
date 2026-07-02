# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the population apparent-E histogram dock (M2 S8, PRD §7.7 / App C A1).

Two layers, mirroring ``test_trace_dock``:

* **Pure-numpy** checks of the Qt-free title helper (:func:`_summary`) — these run
  in the default matrix because ``tether.gui.histogram_dock`` imports pyqtgraph/Qt
  lazily.
* **``@pytest.mark.gui``** smokes that construct a real :class:`HistogramDock` with
  ``qtbot`` and assert the display contract: the filled centred step draws the
  frozen ``(bin_edges, counts)`` of a :class:`Histogram1D`, the x-axis is labelled
  **"apparent E"** and pinned to the histogram's ``value_range``, the y-label
  tracks density-vs-count, the title summarizes the pooled molecules/frames, an
  empty histogram draws a flat baseline (never a crash), and a shape-mismatched
  input is rejected. pyqtgraph is CPU-rendered, so these run headless
  (``QT_QPA_PLATFORM=offscreen``) on all three OSes; pixel rendering is left to the
  live smoke.
"""

from __future__ import annotations

import importlib.util

import numpy as np
import pytest

from tether.analysis.histogram import (
    DEFAULT_NBINS,
    DEFAULT_RANGE,
    Histogram1D,
    apparent_e_histogram,
)
from tether.gui.histogram_dock import _summary

_HAS_QT = all(importlib.util.find_spec(m) is not None for m in ("pyqtgraph", "PySide6"))
_needs_qt = pytest.mark.skipif(not _HAS_QT, reason="pyqtgraph/PySide6 not installed")


def _sample_histogram(density: bool = True) -> Histogram1D:
    """A real Histogram1D over a small pooled apparent-E sample (with n_molecules)."""
    from dataclasses import replace

    pooled = np.array([0.1, 0.2, 0.2, 0.5, 0.5, 0.5, 0.9])
    return replace(apparent_e_histogram(pooled, density=density), n_molecules=3)


# --- pure title helper (no Qt) -----------------------------------------------


def test_summary_pluralizes_and_reports_counts() -> None:
    from dataclasses import replace

    hist = _sample_histogram()
    assert _summary(hist) == "3 molecules · 7 frames"
    # Singular molecule/frame; per-molecule-weighted flag surfaced.
    one = replace(hist, n_molecules=1, n_samples=1, per_molecule_equal_weight=True)
    assert _summary(one) == "1 molecule · 1 frame · per-molecule weighted"
    # The pure core leaves n_molecules None (no molecule pooling) — only frames shown.
    core = apparent_e_histogram(np.array([0.3, 0.6]))
    assert core.n_molecules is None
    assert _summary(core) == "2 frames"


# --- GUI smokes (@pytest.mark.gui, need a real QApplication via qtbot) --------


def _rgba(spec: object) -> tuple[int, int, int, int]:
    import pyqtgraph as pg

    return pg.mkBrush(spec).color().getRgb()


@pytest.mark.gui
@_needs_qt
def test_dock_renders_histogram_edges_and_counts(qtbot) -> None:
    from tether.gui.histogram_dock import HistogramDock

    hist = _sample_histogram()
    with HistogramDock() as dock:
        qtbot.addWidget(dock.widget)
        dock.set_histogram(hist)

        x, y = dock.curve.getData()
        # stepMode="center" contract: one more x edge than y count.
        assert y.shape == (DEFAULT_NBINS,)
        assert x.shape == (DEFAULT_NBINS + 1,)
        np.testing.assert_allclose(x, hist.bin_edges)
        np.testing.assert_allclose(y, hist.counts)
        assert dock.histogram is hist


@pytest.mark.gui
@_needs_qt
def test_dock_axis_conventions_and_fill(qtbot) -> None:
    from tether.gui.histogram_dock import HistogramDock

    with HistogramDock() as dock:
        qtbot.addWidget(dock.widget)
        dock.set_histogram(_sample_histogram())

        assert dock.plot.getAxis("bottom").labelText == "apparent E"
        assert dock.plot.getAxis("left").labelText == "density"
        # A filled centred step in FRET-blue (matching the per-trace dock). The
        # ``brush=`` passed to plot() is stored by pyqtgraph as ``fillBrush``.
        assert dock.curve.opts["stepMode"] == "center"
        assert dock.curve.opts["fillLevel"] == 0
        assert _rgba(dock.curve.opts["fillBrush"]) == (40, 90, 220, 120)

        # x pinned to the histogram value_range (default [-0.25, 1.25]).
        (x_lo, x_hi), _ = dock.plot.viewRange()
        assert x_lo == pytest.approx(DEFAULT_RANGE[0], abs=1e-6)
        assert x_hi == pytest.approx(DEFAULT_RANGE[1], abs=1e-6)


@pytest.mark.gui
@_needs_qt
def test_dock_density_vs_count_label(qtbot) -> None:
    from tether.gui.histogram_dock import HistogramDock

    with HistogramDock() as dock:
        qtbot.addWidget(dock.widget)
        dock.set_histogram(_sample_histogram(density=True))
        assert dock.plot.getAxis("left").labelText == "density"
        dock.set_histogram(_sample_histogram(density=False))
        assert dock.plot.getAxis("left").labelText == "count"


@pytest.mark.gui
@_needs_qt
def test_dock_title_summarizes_pool(qtbot) -> None:
    from tether.gui.histogram_dock import HistogramDock

    with HistogramDock() as dock:
        qtbot.addWidget(dock.widget)
        dock.set_histogram(_sample_histogram())
        # PlotItem.titleLabel carries the self-describing pool summary.
        assert "3 molecules" in dock.plot.titleLabel.text
        assert "7 frames" in dock.plot.titleLabel.text


@pytest.mark.gui
@_needs_qt
def test_dock_empty_histogram_draws_baseline(qtbot) -> None:
    from tether.gui.histogram_dock import HistogramDock

    # An all-rejected / empty pool: zero counts, n_molecules 0 — a valid flat draw.
    empty = apparent_e_histogram(np.empty(0))
    from dataclasses import replace

    empty = replace(empty, n_molecules=0)
    with HistogramDock() as dock:
        qtbot.addWidget(dock.widget)
        dock.set_histogram(empty)  # must not raise
        x, y = dock.curve.getData()
        assert x.shape == (DEFAULT_NBINS + 1,)
        assert float(np.asarray(y).sum()) == 0.0
        assert "0 molecules" in dock.plot.titleLabel.text


@pytest.mark.gui
@_needs_qt
def test_dock_rejects_shape_mismatch(qtbot) -> None:
    from tether.gui.histogram_dock import HistogramDock

    # bin_edges must be exactly one longer than counts (the stepMode="center" law).
    bad = Histogram1D(
        counts=np.zeros(5),
        bin_edges=np.zeros(5),  # should be 6
        density=True,
        value_range=DEFAULT_RANGE,
        n_samples=0,
        n_molecules=0,
        per_molecule_equal_weight=False,
    )
    with HistogramDock() as dock:
        qtbot.addWidget(dock.widget)
        with pytest.raises(ValueError, match="bin_edges of length counts"):
            dock.set_histogram(bad)


@pytest.mark.gui
@_needs_qt
def test_dock_clear_blanks_curve_and_title(qtbot) -> None:
    from tether.gui.histogram_dock import HistogramDock

    with HistogramDock() as dock:
        qtbot.addWidget(dock.widget)
        dock.set_histogram(_sample_histogram())
        dock.clear()
        assert dock.histogram is None
        x, y = dock.curve.getData()
        assert x is None or np.asarray(x).size == 0
        assert y is None or np.asarray(y).size == 0
