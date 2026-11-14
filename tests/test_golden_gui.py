# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The GUI shows the golden numbers: the histogram dock draws what the golden file holds (#702).

The self-drift suite (``tests/test_golden.py``) freezes the headless numbers. This check
closes the gap to the screen: for the same input (the corrected ``synthetic_traces`` store)
the plotted histogram bars and the plot's title label must equal the golden values, so a
change in the dock's drawing path that moves a displayed number fails here even when the
headless numbers hold. Headless (``QT_QPA_PLATFORM=offscreen``), as the GUI gate requires.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("h5py")
pytest.importorskip("pyqtgraph")
pytest.importorskip("PySide6")

import _golden  # noqa: E402


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

        _x, drawn = dock.curve.getData()
        np.testing.assert_array_equal(drawn, np.asarray(golden["histogram.counts"], dtype=float))
        title = dock.plot.titleLabel.text
        n_mol, n_frames = golden["histogram.n_molecules"], golden["histogram.n_samples"]
        assert title == f"{n_mol} molecules · {n_frames} frames"
        assert dock.plot.getAxis("left").labelText == "count"
