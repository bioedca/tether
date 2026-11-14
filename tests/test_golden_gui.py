# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The GUI shows the golden numbers: the histogram dock draws what the golden file holds (#702).

The self-drift suite (``tests/test_golden.py``) freezes the headless numbers. This check
closes the gap to the screen: for the same input (the corrected ``synthetic_traces`` store)
the plotted histogram bars, their bin edges (#865) and the plot's title label must equal the
golden values, so a change in the dock's drawing path that moves a displayed number fails here
even when the headless numbers hold. Headless (``QT_QPA_PLATFORM=offscreen``), as the GUI gate
requires.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pytest

pytest.importorskip("h5py")
pytest.importorskip("pyqtgraph")
pytest.importorskip("PySide6")

import _golden  # noqa: E402

if TYPE_CHECKING:
    from tether.gui.histogram_dock import HistogramDock


def _assert_draws_golden(dock: HistogramDock, golden: dict[str, Any]) -> None:
    """The dock's drawn bars, bin edges and labels equal the golden values."""
    edges, drawn = dock.curve.getData()
    np.testing.assert_array_equal(drawn, np.asarray(golden["histogram.counts"], dtype=float))
    # The x values are the bin edges (stepMode="center"); the golden float policy applies.
    np.testing.assert_allclose(
        edges, np.asarray(golden["histogram.edges"], dtype=float), rtol=_golden.FLOAT_RTOL, atol=0
    )
    title = dock.plot.titleLabel.text
    n_mol, n_frames = golden["histogram.n_molecules"], golden["histogram.n_samples"]
    assert title == f"{n_mol} molecules · {n_frames} frames"
    assert dock.plot.getAxis("left").labelText == "count"


@pytest.mark.gui
def test_histogram_dock_draws_the_golden_histogram(qtbot, tmp_path: Path) -> None:
    from tether.analysis.histogram import population_apparent_e_histogram
    from tether.gui.histogram_dock import HistogramDock

    golden = _golden.load_golden("synthetic_traces")
    assert golden is not None
    path = _golden.synthetic_traces_store(tmp_path)

    with HistogramDock() as dock:
        qtbot.addWidget(dock.widget)
        dock.set_histogram(population_apparent_e_histogram(path, density=False))
        _assert_draws_golden(dock, golden)


@pytest.mark.gui
def test_shifted_drawn_edges_fail_the_golden_check(qtbot, tmp_path: Path, monkeypatch) -> None:
    """A dock that draws the right counts at edges shifted by half a bin fails the check (#865)."""
    from tether.analysis.histogram import population_apparent_e_histogram
    from tether.gui.histogram_dock import HistogramDock

    golden = _golden.load_golden("synthetic_traces")
    assert golden is not None
    path = _golden.synthetic_traces_store(tmp_path)
    histogram = population_apparent_e_histogram(path, density=False)
    half_bin = float(np.diff(histogram.bin_edges)[0]) / 2

    with HistogramDock() as dock:
        qtbot.addWidget(dock.widget)
        set_data = dock.curve.setData

        def shifted(x, y, *args, **kwargs):  # the test double: counts kept, edges moved
            return set_data(np.asarray(x, dtype=float) + half_bin, y, *args, **kwargs)

        monkeypatch.setattr(dock.curve, "setData", shifted)
        dock.set_histogram(histogram)
        edges, drawn = dock.curve.getData()
        np.testing.assert_array_equal(drawn, np.asarray(golden["histogram.counts"], dtype=float))
        np.testing.assert_allclose(edges, np.asarray(golden["histogram.edges"]) + half_bin)
        with pytest.raises(AssertionError):
            _assert_draws_golden(dock, golden)
