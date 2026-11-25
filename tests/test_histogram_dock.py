# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The population apparent-E histogram (Appendix C A1) as the Analyze section draws it.

Until #374 a pyqtgraph ``HistogramDock`` drew this histogram from the one-action
``&Analysis`` menu. #374 retired that dock: the histogram is now the
``population_apparent_e_histogram`` entry of the plot registry
([`tether.services.plots`][]), drawn by [`tether.analysis.plot_export.render_histogram1d`][] on
the section's Matplotlib preview canvas, so what shows is what an export writes (ADR-0124).
The dock's tests are kept here, each moved to the same promise of the new path:

* the bars carry the frozen ``(bin_edges, counts)`` of the
  [`Histogram1D`][tether.analysis.histogram.Histogram1D] the population function returned;
* the axes are labelled, the x range is the histogram's ``value_range`` and the bars are filled;
* the y label follows density or count;
* the figure says how many molecules went in (``N``), and the status line names them;
* an empty pool (every molecule rejected) draws zero bars, never a crash or a made-up shape;
* a shape-invalid histogram is refused rather than drawn;
* removing the last series blanks the preview.

The renderer checks run with no ``QApplication``; the section checks are ``@pytest.mark.gui``
and run headless (``QT_QPA_PLATFORM=offscreen``) on all three operating systems.
"""

from __future__ import annotations

import importlib.util
from dataclasses import replace

import numpy as np
import pytest

from tether.analysis.histogram import (
    DEFAULT_NBINS,
    DEFAULT_RANGE,
    Histogram1D,
    apparent_e_histogram,
    population_apparent_e_histogram,
)
from tether.services.plots import ALL_MOLECULES, PlotSeries, compose_plot, plot_spec

_HAS_QT = all(importlib.util.find_spec(m) is not None for m in ("pyqtgraph", "PySide6"))
_needs_qt = pytest.mark.skipif(not _HAS_QT, reason="pyqtgraph/PySide6 not installed")

A1 = "population_apparent_e_histogram"


def _sample_histogram(density: bool = True) -> Histogram1D:
    """A real Histogram1D over a small pooled apparent-E sample (with n_molecules)."""
    pooled = np.array([0.1, 0.2, 0.2, 0.5, 0.5, 0.5, 0.9])
    return replace(apparent_e_histogram(pooled, density=density), n_molecules=3)


def _store(tmp_path, *, rejected=None):
    """A three-molecule store with explicit channels (no model), and its keys."""
    from _analysis_store import build_store_with_channels

    rng = np.random.default_rng(7)
    donor = rng.uniform(100.0, 200.0, size=(3, 24))
    acceptor = rng.uniform(100.0, 200.0, size=(3, 24))
    return build_store_with_channels(tmp_path, donor, acceptor, rejected=rejected)


def _bars(figure):
    """The filled step of the A1 figure: its values and its edges."""
    (step,) = figure.axes[0].patches
    values, edges, _baseline = step.get_data()
    return step, np.asarray(values), np.asarray(edges)


# --- the A1 entry's renderer (no Qt) -------------------------------------------


def test_the_a1_entry_draws_the_edges_and_counts_it_is_given() -> None:
    hist = _sample_histogram()
    figure = plot_spec(A1).render([(ALL_MOLECULES, hist)])
    step, values, edges = _bars(figure)
    assert values.shape == (DEFAULT_NBINS,)
    assert edges.shape == (DEFAULT_NBINS + 1,)
    np.testing.assert_allclose(edges, hist.bin_edges)
    np.testing.assert_allclose(values, hist.counts)
    assert step.get_fill()


def test_the_a1_axis_conventions() -> None:
    ax = plot_spec(A1).render([(ALL_MOLECULES, _sample_histogram())]).axes[0]
    assert ax.get_xlabel() == "Apparent FRET efficiency $E$"
    assert ax.get_ylabel() == "Density"
    # x is the histogram's value_range (default [-0.25, 1.25]): excursions stay visible.
    assert ax.get_xlim() == pytest.approx(DEFAULT_RANGE)


def test_the_a1_y_label_follows_density_or_count() -> None:
    density = plot_spec(A1).render([(ALL_MOLECULES, _sample_histogram(density=True))])
    count = plot_spec(A1).render([(ALL_MOLECULES, _sample_histogram(density=False))])
    assert density.axes[0].get_ylabel() == "Density"
    assert count.axes[0].get_ylabel() == "Counts"


def test_the_a1_figure_names_how_many_molecules_went_in() -> None:
    ax = plot_spec(A1).render([(ALL_MOLECULES, _sample_histogram())]).axes[0]
    assert "N = 3" in [t.get_text() for t in ax.texts]
    assert ax.get_title() == "FRET-efficiency histogram (A1)"


def test_an_empty_pool_draws_zero_bars(tmp_path) -> None:
    # Every molecule rejected: the population is empty. Zero bars are the honest answer.
    project, _keys = _store(tmp_path, rejected=[True, True, True])
    outcome = compose_plot(project.path, A1, [PlotSeries(ALL_MOLECULES, None)])
    assert outcome.drawn, outcome.missing
    _step, values, edges = _bars(outcome.figure)
    assert edges.shape == (DEFAULT_NBINS + 1,)
    assert float(values.sum()) == 0.0
    assert "N = 0" in [t.get_text() for t in outcome.figure.axes[0].texts]


def test_a_shape_invalid_histogram_is_refused() -> None:
    bad = Histogram1D(
        counts=np.zeros(5),
        bin_edges=np.zeros(5),  # should be 6: one more edge than counts
        density=True,
        value_range=DEFAULT_RANGE,
        n_samples=0,
        n_molecules=0,
        per_molecule_equal_weight=False,
    )
    with pytest.raises(ValueError):
        plot_spec(A1).render([(ALL_MOLECULES, bad)])


# --- the A1 entry in the Analyze section (@pytest.mark.gui) --------------------


@pytest.mark.gui
@_needs_qt
def test_the_section_draws_the_population_histogram_of_the_store(qtbot, tmp_path) -> None:
    from tether.gui.analysis_section import AnalysisSection

    project, _keys = _store(tmp_path, rejected=[True, False, False])
    messages: list[str] = []
    with AnalysisSection(status=messages.append) as section:
        qtbot.addWidget(section.widget)
        section.set_project(project.path)
        assert section.plot_id == A1  # the first plot of the list
        section.add_series(PlotSeries(ALL_MOLECULES, None))
        expected = population_apparent_e_histogram(project.path)
        assert section.canvas is not None and section.canvas.figure is section.figure
        _step, values, edges = _bars(section.figure)
        np.testing.assert_allclose(edges, expected.bin_edges)
        np.testing.assert_allclose(values, expected.counts)
        assert expected.n_molecules == 2  # the rejected molecule is left out
        assert messages[-1] == "FRET-efficiency histogram (A1) — All molecules: 2 molecule(s)"


@pytest.mark.gui
@_needs_qt
def test_removing_the_last_series_blanks_the_preview(qtbot, tmp_path) -> None:
    from tether.gui.analysis_section import AnalysisSection

    project, _keys = _store(tmp_path)
    with AnalysisSection() as section:
        qtbot.addWidget(section.widget)
        section.set_project(project.path)
        section.add_series(PlotSeries(ALL_MOLECULES, None))
        assert section.figure is not None
        section.remove_series(0)
        assert section.figure is None
        assert section.canvas is None
        assert section.empty_text == (
            "FRET-efficiency histogram (A1): add a selection to the layer list to draw it."
        )
