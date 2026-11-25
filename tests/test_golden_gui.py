# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The GUI shows the golden numbers: the Analyze section draws what the golden file holds (#702).

The self-drift suite (``tests/test_golden.py``) freezes the headless numbers. This check
closes the gap to the screen: for the same input (the corrected ``synthetic_traces`` store)
the histogram bars on the Analyze section's preview canvas, their bin edges (#865) and the N
label must equal the golden values, so a change in the drawing path that moves a displayed
number fails here even when the headless numbers hold. Headless (``QT_QPA_PLATFORM=offscreen``),
as the GUI gate requires. The preview was the pyqtgraph histogram dock until #374 replaced it
with the A1 entry of the plot registry, drawn by Matplotlib; this check moved with it.
"""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("h5py")
pytest.importorskip("pyqtgraph")
pytest.importorskip("PySide6")

import _golden  # noqa: E402


@pytest.mark.gui
@pytest.mark.parametrize("shift_edges", [False, True], ids=["golden", "shifted-edges"])
def test_the_analysis_section_draws_the_golden_histogram(
    qtbot, tmp_path: Path, monkeypatch, shift_edges: bool
) -> None:
    from matplotlib.axes import Axes

    from tether.gui.analysis_section import AnalysisSection
    from tether.services.plots import ALL_MOLECULES, PlotSeries

    golden = _golden.load_golden("synthetic_traces")
    assert golden is not None
    path = _golden.synthetic_traces_store(tmp_path)
    expected_edges = np.asarray(golden["histogram.edges"], dtype=float)
    golden_counts = np.asarray(golden["histogram.counts"], dtype=float)

    if shift_edges:
        half_bin = float(np.diff(expected_edges)[0]) / 2
        stairs = Axes.stairs

        def draw_shifted_edges(self, values, edges=None, *args, **kwargs):
            shifted = np.asarray(edges, dtype=float) + half_bin
            return stairs(self, values, shifted, *args, **kwargs)

        monkeypatch.setattr(Axes, "stairs", draw_shifted_edges)

    with AnalysisSection() as section:
        qtbot.addWidget(section.widget)
        section.set_project(path)
        section.set_plot("population_apparent_e_histogram")
        section.add_series(PlotSeries(ALL_MOLECULES, None))
        assert section.canvas is not None and section.canvas.figure is section.figure

        ax = section.figure.axes[0]
        (step,) = ax.patches
        drawn, edges, _baseline = step.get_data()
        # The A1 entry draws the density (its default); back to counts with the bin widths
        # and the golden total, the drawn bars are the golden counts.
        counts = np.asarray(drawn, dtype=float) * np.diff(expected_edges) * golden_counts.sum()
        np.testing.assert_allclose(counts, golden_counts, rtol=0, atol=1e-6)
        n_mol = golden["histogram.n_molecules"]
        assert f"N = {n_mol}" in [t.get_text() for t in ax.texts]
        assert ax.get_ylabel() == "Density"

        # The bars' x values are the bin edges. The shifted case must fail this check.
        if shift_edges:
            np.testing.assert_allclose(edges, expected_edges + half_bin)
        expectation = pytest.raises(AssertionError) if shift_edges else nullcontext()
        with expectation:
            np.testing.assert_allclose(edges, expected_edges, rtol=_golden.FLOAT_RTOL, atol=0)
