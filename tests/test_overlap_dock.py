# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the static overlap-view dock (M2 S10, PRD §7.3 / §5.1).

Two layers, mirroring ``test_histogram_dock``:

* **Pure-numpy** checks of the Qt-free readout helper (:func:`_readout`) — these run
  in the default matrix because ``tether.gui.overlap_dock`` imports pyqtgraph/Qt
  lazily.
* **``@pytest.mark.gui``** smokes that construct a real :class:`OverlapDock` with
  ``qtbot`` and assert the display contract: the readout **shows the nearest-
  neighbour distance** (the §9 M2 S10 "overlap view renders NN distance"), the
  overlap warning fires only when apertures overlap, the static patch draws, an
  analysis-only molecule with no patch degrades to a "no patch" readout, and a
  non-2-D patch is rejected. pyqtgraph is CPU-rendered, so these run headless
  (``QT_QPA_PLATFORM=offscreen``) on all three OSes.
"""

from __future__ import annotations

import importlib.util
import math

import numpy as np
import pytest

from tether.analysis.overlap import OverlapInfo
from tether.gui.overlap_dock import _readout

_HAS_QT = all(importlib.util.find_spec(m) is not None for m in ("pyqtgraph", "PySide6"))
_needs_qt = pytest.mark.skipif(not _HAS_QT, reason="pyqtgraph/PySide6 not installed")


def _patch(fill: float = 1.0, w: int = 21) -> np.ndarray:
    p = np.zeros((w, w), dtype="float32")
    p[w // 2, w // 2] = fill  # a bright centre so the patch has a non-flat range
    return p


# --- pure readout helper (no Qt) ---------------------------------------------


def test_readout_reports_distance_and_overlap() -> None:
    overlapping = OverlapInfo(nn_distance=4.2, overlaps=True, aperture_radius=3.0, name="mol-3")
    assert _readout(overlapping) == "mol-3 · NN 4.2 px · apertures overlap"
    clear = OverlapInfo(nn_distance=12.4, overlaps=False, aperture_radius=3.0, name="mol-3")
    assert _readout(clear) == "mol-3 · NN 12.4 px"


def test_readout_handles_isolated_and_nameless() -> None:
    lone = OverlapInfo(nn_distance=math.inf, overlaps=False, aperture_radius=3.0, name="mol-9")
    assert _readout(lone) == "mol-9 · no neighbour"
    nameless = OverlapInfo(nn_distance=5.0, overlaps=False, aperture_radius=3.0)
    assert _readout(nameless) == "NN 5.0 px"


# --- GUI smokes (@pytest.mark.gui, need a real QApplication via qtbot) --------


@pytest.mark.gui
@_needs_qt
def test_dock_renders_nn_distance_and_patch(qtbot) -> None:
    from tether.gui.overlap_dock import OverlapDock

    patch = _patch(fill=500.0)
    info = OverlapInfo(
        nn_distance=4.2, overlaps=True, aperture_radius=3.0, patch=patch, name="mol-3"
    )
    with OverlapDock() as dock:
        qtbot.addWidget(dock.widget)
        dock.set_molecule(info)

        # the §9 M2 S10 gate: the view renders the nearest-neighbour distance
        assert "NN 4.2 px" in dock.readout
        assert "apertures overlap" in dock.readout
        assert dock.overlaps is True
        assert dock.nn_distance == pytest.approx(4.2)
        # the static patch is drawn
        assert dock.info is info
        np.testing.assert_allclose(np.asarray(dock.image_item.image), patch)


@pytest.mark.gui
@_needs_qt
def test_dock_clear_neighbour_has_no_overlap_warning(qtbot) -> None:
    from tether.gui.overlap_dock import OverlapDock

    info = OverlapInfo(
        nn_distance=15.0, overlaps=False, aperture_radius=3.0, patch=_patch(), name="m"
    )
    with OverlapDock() as dock:
        qtbot.addWidget(dock.widget)
        dock.set_molecule(info)
        assert "NN 15.0 px" in dock.readout
        assert "apertures overlap" not in dock.readout
        assert dock.overlaps is False


@pytest.mark.gui
@_needs_qt
def test_dock_analysis_only_molecule_has_no_patch(qtbot) -> None:
    from tether.gui.overlap_dock import OverlapDock

    # An analysis-only project (§7.4): NN readout still shown, patch panel blank.
    info = OverlapInfo(nn_distance=8.0, overlaps=False, aperture_radius=3.0, patch=None, name="m0")
    with OverlapDock() as dock:
        qtbot.addWidget(dock.widget)
        dock.set_molecule(info)
        assert "NN 8.0 px" in dock.readout
        assert "no patch" in dock.readout
        assert dock.image_item.image is None  # nothing fabricated


@pytest.mark.gui
@_needs_qt
def test_dock_none_and_clear_blank_the_view(qtbot) -> None:
    from tether.gui.overlap_dock import OverlapDock

    with OverlapDock() as dock:
        qtbot.addWidget(dock.widget)
        dock.set_molecule(
            OverlapInfo(nn_distance=5.0, overlaps=False, aperture_radius=3.0, patch=_patch())
        )
        dock.set_molecule(None)
        assert dock.info is None
        assert dock.image_item.image is None
        assert "No molecule selected" in dock.readout
        # clear() from a drawn state also blanks
        dock.set_molecule(
            OverlapInfo(nn_distance=5.0, overlaps=False, aperture_radius=3.0, patch=_patch())
        )
        dock.clear()
        assert dock.info is None
        assert dock.image_item.image is None


@pytest.mark.gui
@_needs_qt
def test_dock_rejects_non_2d_patch(qtbot) -> None:
    from tether.gui.overlap_dock import OverlapDock

    bad = OverlapInfo(
        nn_distance=5.0, overlaps=False, aperture_radius=3.0, patch=np.zeros((3, 3, 3))
    )
    with OverlapDock() as dock:
        qtbot.addWidget(dock.widget)
        with pytest.raises(ValueError, match="2-D"):
            dock.set_molecule(bad)


@pytest.mark.gui
@_needs_qt
def test_dock_rejected_patch_leaves_prior_molecule_intact(qtbot) -> None:
    from tether.gui.overlap_dock import OverlapDock

    good = OverlapInfo(
        nn_distance=6.0, overlaps=False, aperture_radius=3.0, patch=_patch(fill=300.0), name="good"
    )
    bad = OverlapInfo(
        nn_distance=1.0, overlaps=True, aperture_radius=3.0, patch=np.zeros((3, 3, 3)), name="bad"
    )
    with OverlapDock() as dock:
        qtbot.addWidget(dock.widget)
        dock.set_molecule(good)
        with pytest.raises(ValueError, match="2-D"):
            dock.set_molecule(bad)
        # Validation runs before any mutation, so the rejected molecule never
        # overwrote the good one — the dock still shows "good", not a half-update.
        assert dock.info is good
        assert "good" in dock.readout
        assert "NN 6.0 px" in dock.readout
