# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shell integration of the static overlap view (M2 S10, PRD §7.3 / §5.1).

Two layers:

* **Headless** checks of :func:`tether.gui.shell.make_store_overlap` — a plain
  ``molecule_key -> OverlapInfo`` callable, so it is exercised over a real
  ``.tether`` in the default matrix (no Qt): the nearest-neighbour distance,
  apertures-overlap flag, cached patch, and per-movie confinement are all asserted
  against a controlled store.
* **``@pytest.mark.gui``** smokes that wire an overlap seam into a real
  :class:`~tether.gui.shell.TetherShell` and assert the right-hand overlap dock is
  built lazily on the first selection, shows the NN distance, and refreshes as the
  curator navigates — while a shell with **no** overlap seam builds no dock.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("h5py")
pytest.importorskip("scipy")

from tether.analysis.overlap import OverlapInfo  # noqa: E402
from tether.imaging.aperture import IntegratedTraces  # noqa: E402
from tether.imaging.calibrate import RegistrationMap  # noqa: E402
from tether.imaging.coloc import ColocalizedMolecules  # noqa: E402
from tether.imaging.extract import (  # noqa: E402
    MoleculeTraces,
    MovieMetadata,
    read_molecules,
    write_extraction,
)
from tether.imaging.register import PolyTransform2D  # noqa: E402
from tether.imaging.split import ChannelGeometry  # noqa: E402
from tether.io.filename import parse_filename  # noqa: E402
from tether.io.schema import create_project  # noqa: E402
from tether.project.core import Project  # noqa: E402

_HAS_QT = all(importlib.util.find_spec(m) is not None for m in ("pyqtgraph", "PySide6"))
_needs_qt = pytest.mark.skipif(not _HAS_QT, reason="pyqtgraph/PySide6 not installed")

_PARSED = parse_filename("Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif")
_WINDOW = 21


def _integrated(intensity: np.ndarray) -> IntegratedTraces:
    intensity = np.asarray(intensity, dtype="float64")
    background = np.full_like(intensity, 100.0)
    return IntegratedTraces(
        intensity=intensity,
        total=intensity + background,
        background=background,
        valid=np.ones(intensity.shape[0], dtype=bool),
    )


def _reg_map() -> RegistrationMap:
    poly = PolyTransform2D(
        a=np.array([0.0, 1.0, 0.0, 0.0, 0.0, 0.0]),
        b=np.array([0.0, 0.0, 1.0, 0.0, 0.0, 0.0]),
        norm_xy=np.eye(3),
        norm_uv=np.eye(3),
    )
    return RegistrationMap(1, 2, poly, poly, rms_residual=0.1, n_control_points=100)


def _build_store(path: Path, coords: np.ndarray, patches: np.ndarray) -> tuple[Project, list[str]]:
    """A ``.tether`` with the given donor coordinates + per-molecule donor patches."""
    n, t = coords.shape[0], 12
    donor = np.full((n, t), 400.0)
    acceptor = np.full((n, t), 600.0)
    mols = ColocalizedMolecules(
        donor_xy=coords,
        acceptor_xy=coords,
        acceptor_detected=np.zeros(n, dtype=bool),
        donor_index=np.arange(n, dtype=np.intp),
        acceptor_index=np.full(n, -1, dtype=np.intp),
    )
    traces = MoleculeTraces(
        donor=_integrated(donor),
        acceptor=_integrated(acceptor),
        donor_patches=np.asarray(patches, dtype="float32"),
        acceptor_patches=np.asarray(patches, dtype="float32"),
        window=_WINDOW,
        disk_radius=3.0,
        ring_inner=6.0,
        ring_outer=8.0,
        bg_window=10,
    )
    movie = MovieMetadata(
        movie_id="mov-1",
        sha256="a" * 64,
        n_frames=t,
        height=64,
        width=64,
        donor_geometry=ChannelGeometry(crop=(1, 1, 64, 64)),
        acceptor_geometry=ChannelGeometry(crop=(1, 65, 64, 128)),
    )
    create_project(path, overwrite=True)
    write_extraction(
        path,
        movie=movie,
        molecules=mols,
        traces=traces,
        parsed=_PARSED,
        registration_map=_reg_map(),
    )
    proj = Project.open(path)
    keys = [
        k.decode() if isinstance(k, bytes) else str(k) for k in read_molecules(path)["molecule_key"]
    ]
    return proj, keys


# --- make_store_overlap (headless) -------------------------------------------


def test_make_store_overlap_reports_neighbours_flags_and_patch(tmp_path: Path) -> None:
    from tether.gui.shell import make_store_overlap

    # mol0/mol1 are 4 px apart (radius 3 → overlap < 6 px); mol2 is far from both.
    coords = np.array([[10.0, 10.0], [14.0, 10.0], [40.0, 40.0]])
    patches = np.stack([np.full((_WINDOW, _WINDOW), (i + 1) * 10.0) for i in range(3)])
    proj, keys = _build_store(tmp_path / "overlap.tether", coords, patches)

    seam = make_store_overlap(proj)
    info0 = seam(keys[0])
    assert info0 is not None
    assert info0.nn_distance == pytest.approx(4.0)
    assert info0.overlaps is True
    assert info0.aperture_radius == pytest.approx(3.0)  # from stored disk_radius
    assert info0.nn_molecule_key == keys[1]
    np.testing.assert_allclose(info0.patch, patches[0])

    info2 = seam(keys[2])
    assert info2 is not None
    assert info2.overlaps is False  # nearest neighbour is ~40 px away
    assert info2.nn_distance > 6.0


def test_make_store_overlap_unknown_key_is_none(tmp_path: Path) -> None:
    from tether.gui.shell import make_store_overlap

    coords = np.array([[10.0, 10.0], [30.0, 30.0]])
    patches = np.zeros((2, _WINDOW, _WINDOW), dtype="float32")
    proj, _ = _build_store(tmp_path / "unknown.tether", coords, patches)
    assert make_store_overlap(proj)("not-a-real-key") is None


# --- shell wiring (@pytest.mark.gui) -----------------------------------------


def _keyed_traces(n: int):
    from tether.gui.trace_dock import TraceView

    return [
        TraceView(
            donor=np.full(20, 400.0),
            acceptor=np.full(20, 600.0),
            frame_time=0.1,
            name=f"m{i}",
            molecule_key=f"m{i}",
        )
        for i in range(n)
    ]


@pytest.mark.gui
@_needs_qt
def test_shell_overlap_dock_shows_nn_distance_on_selection(qtbot) -> None:
    from tether.gui.shell import TetherShell

    # A fake seam decoupled from a store: the shell-wiring under test is the lazy
    # dock build + per-selection refresh, not the store read (covered above).
    payloads = {
        "m0": OverlapInfo(
            nn_distance=4.0,
            overlaps=True,
            aperture_radius=3.0,
            patch=np.zeros((21, 21), dtype="float32"),
            name="m0",
        ),
        "m1": OverlapInfo(
            nn_distance=25.0,
            overlaps=False,
            aperture_radius=3.0,
            patch=np.zeros((21, 21), dtype="float32"),
            name="m1",
        ),
    }
    with TetherShell(overlap=payloads.get) as shell:
        qtbot.addWidget(shell.window)
        assert shell.overlap_dock is None  # not built until a molecule resolves
        shell.set_molecules(_keyed_traces(2))  # selects row 0
        assert shell.overlap_dock is not None
        assert "NN 4.0 px" in shell.overlap_dock.readout
        assert shell.overlap_dock.overlaps is True
        # Navigating to m1 refreshes the same dock (no second dock built).
        dock = shell.overlap_dock
        shell.molecule_list.setCurrentRow(1)
        assert shell.overlap_dock is dock
        assert "NN 25.0 px" in shell.overlap_dock.readout
        assert shell.overlap_dock.overlaps is False


@pytest.mark.gui
@_needs_qt
def test_shell_without_overlap_seam_builds_no_dock(qtbot) -> None:
    from tether.gui.shell import TetherShell

    with TetherShell() as shell:  # no overlap seam
        qtbot.addWidget(shell.window)
        shell.set_molecules(_keyed_traces(2))
        assert shell.overlap_dock is None


@pytest.mark.gui
@_needs_qt
def test_shell_overlap_seam_failure_does_not_crash(qtbot) -> None:
    from tether.gui.shell import TetherShell

    def _boom(_key: str):
        raise RuntimeError("seam exploded")

    with TetherShell(overlap=_boom) as shell:
        qtbot.addWidget(shell.window)
        shell.set_molecules(_keyed_traces(1))  # must not raise
        assert "Overlap view failed" in shell.status_message
        assert shell.overlap_dock is None  # a seam that raised built no dock


@pytest.mark.gui
@_needs_qt
def test_shell_overlap_bad_patch_does_not_crash(qtbot) -> None:
    from tether.gui.shell import TetherShell

    # The seam resolves but returns a shape-invalid patch — OverlapDock.set_molecule
    # raises, and the shell must catch it (like show_histogram) rather than crash the
    # selection slot, attaching no half-built dock.
    seam = {
        "m0": OverlapInfo(
            nn_distance=4.0,
            overlaps=True,
            aperture_radius=3.0,
            patch=np.zeros((2, 2, 2)),
            name="m0",
        )
    }
    with TetherShell(overlap=seam.get) as shell:
        qtbot.addWidget(shell.window)
        shell.set_molecules(_keyed_traces(1))  # must not raise
        assert "Overlap view failed" in shell.status_message
        assert shell.overlap_dock is None  # a first-draw failure attaches no dock
