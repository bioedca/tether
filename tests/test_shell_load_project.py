# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Open a produced / extracted ``.tether`` live in the shell (M7 PR #5e, PRD §7.8).

Covers the store↔shell hookup that closes the §7.8 "browse/curate/idealize round-trip
live" clause: :func:`tether.gui.shell.traces_from_store` (the store → ``list[TraceView]``
builder) and :meth:`TetherShell.load_project` (re-wire the running shell's store seams +
load molecules), plus the ``&File → Open project…`` reachability and the single-produced
auto-open from :meth:`TetherShell.import_deeplasi_bundle`.

All ``@pytest.mark.gui``. Stores are real: a round-trip ``.tether`` from the shared
``_analysis_store.build_store_with_channels`` (coordinates + patches → overlap available)
and a coordinate-less analysis-only ``.tether`` from
:func:`~tether.project.analysis_import.import_analysis_only_project` over the committed
``smd_4mol.hdf5`` (movie-less → overlap gated off, banner surfaced).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("h5py")

_HAS_QT = all(importlib.util.find_spec(m) is not None for m in ("pyqtgraph", "PySide6"))
_needs_qt = pytest.mark.skipif(not _HAS_QT, reason="pyqtgraph/PySide6 not installed")

pytestmark = [pytest.mark.gui, _needs_qt]

_FIXTURES = Path(__file__).parent / "fixtures"


# --------------------------------------------------------------------------- #
# fixtures / helpers
# --------------------------------------------------------------------------- #


@pytest.fixture
def shell(qapp, qtbot):
    from tether.gui.shell import TetherShell

    s = TetherShell()
    qtbot.addWidget(s.window)
    yield s
    s.close()


class _StubWizardDialog:
    """A stand-in for ``DeepLasiWizardDialog`` returning preset produced paths."""

    def __init__(self, produced) -> None:
        self._produced = tuple(produced)

    def exec(self):
        return self._produced


def _round_trip_store(tmp_path, *, n=3, t=12, name="rt.tether", seed=1):
    """A real round-trip ``.tether`` (coords + patches) → ``(Project, keys, donor, acceptor)``."""
    from _analysis_store import build_store_with_channels

    rng = np.random.default_rng(seed)
    donor = rng.uniform(400.0, 800.0, size=(n, t))
    acceptor = rng.uniform(200.0, 600.0, size=(n, t))
    project, keys = build_store_with_channels(tmp_path, donor, acceptor, name=name)
    return project, keys, donor, acceptor


def _analysis_only_store(tmp_path, *, name="ao.tether"):
    """A movie-less, coordinate-less analysis-only ``.tether`` from the committed SMD."""
    from tether.idealize import read_smd
    from tether.project.analysis_import import import_analysis_only_project

    smd = read_smd(_FIXTURES / "smd_4mol.hdf5")
    out = tmp_path / name
    import_analysis_only_project(out, source=smd, source_name="smd_4mol.hdf5")
    return out


# --------------------------------------------------------------------------- #
# traces_from_store — the store → list[TraceView] builder
# --------------------------------------------------------------------------- #


def test_traces_from_store_builds_keyed_full_frame_views(qapp, tmp_path) -> None:
    from tether.gui.shell import traces_from_store

    project, keys, donor, acceptor = _round_trip_store(tmp_path, n=3, t=12)
    views = traces_from_store(project)

    assert len(views) == 3
    # every view carries its store molecule_key (so the idealize / overlap seams resolve)
    assert [v.molecule_key for v in views] == keys
    for i, view in enumerate(views):
        # the full native frame_range slice (curation shows the whole trace)
        assert view.donor.shape == (12,)
        assert view.acceptor.shape == (12,)
        # the corrected layer holds the seeded intensities (float32 store), right channel
        np.testing.assert_allclose(view.donor, donor[i].astype(np.float32))
        np.testing.assert_allclose(view.acceptor, acceptor[i].astype(np.float32))
        # the seeded MovieMetadata has no frame interval → 0.0 → None (frame-index axis)
        assert view.frame_time is None


def test_traces_from_store_reads_positive_frame_time(qapp, tmp_path) -> None:
    import h5py

    from tether.gui.shell import traces_from_store

    project, keys, *_ = _round_trip_store(tmp_path, n=2, t=10)
    # A real movie carries a positive seconds/frame; the extractor writes 0.0 only when
    # the TIFF interval is unknown. Stamp a real interval onto the movie row and confirm
    # it flows through to every TraceView (the value branch of _movie_frame_times).
    with h5py.File(project.path, "r+") as f:
        table = f["movies"]["table"][:]
        table["frame_time"][:] = 0.05
        f["movies"]["table"][:] = table

    views = traces_from_store(project)

    assert len(views) == 2
    assert all(v.frame_time == pytest.approx(0.05) for v in views)


def test_traces_from_store_empty_store_returns_empty(qapp, tmp_path) -> None:
    from tether.gui.shell import traces_from_store
    from tether.io.schema import create_project
    from tether.project.core import Project

    path = create_project(tmp_path / "empty.tether")
    assert traces_from_store(Project.open(path)) == []


def test_traces_from_store_rejects_unknown_quantity(qapp, tmp_path) -> None:
    from tether.gui.shell import traces_from_store

    project, *_ = _round_trip_store(tmp_path)
    with pytest.raises(ValueError, match="intensity_quantity"):
        traces_from_store(project, intensity_quantity="bogus")


def test_traces_from_store_analysis_only_is_movie_less(qapp, tmp_path) -> None:
    from tether.gui.shell import traces_from_store

    out = _analysis_only_store(tmp_path)
    views = traces_from_store(out)  # accepts a bare path, not only a Project

    assert len(views) == 4
    assert all(v.frame_time is None for v in views)  # movie-less → no frame interval
    assert all(v.donor.shape == (1700,) for v in views)
    assert all(v.molecule_key for v in views)


def test_traces_from_store_analysis_only_has_no_raw_layer(qapp, tmp_path) -> None:
    # An analysis-only store writes only the corrected layers; asking for "raw" must
    # raise a clear error, not KeyError deep in h5py.
    from tether.gui.shell import traces_from_store

    out = _analysis_only_store(tmp_path)
    with pytest.raises(ValueError, match="raw"):
        traces_from_store(out, intensity_quantity="raw")


# --------------------------------------------------------------------------- #
# TetherShell.load_project — re-wire the running shell + load molecules
# --------------------------------------------------------------------------- #


def test_load_project_opens_round_trip_store_live(shell, tmp_path) -> None:
    project, keys, *_ = _round_trip_store(tmp_path, n=3, t=12)

    opened = shell.load_project(project.path)

    assert opened is not None
    assert shell.molecule_list.count() == 3
    assert "3 molecule(s)" in shell.status_message
    # histogram seam wired → &Analysis draws over the real store
    assert shell.show_histogram() is not None
    # overlap seam wired (coords + patches present) → selecting row 0 built the dock
    assert shell.overlap_dock is not None


def test_load_project_analysis_only_gates_overlap_and_banners(shell, tmp_path) -> None:
    from tether.project.analysis_import import ANALYSIS_ONLY_BANNER

    out = _analysis_only_store(tmp_path)

    opened = shell.load_project(out)

    assert opened is not None
    assert shell.molecule_list.count() == 4
    assert "analysis-only" in shell.status_message
    assert ANALYSIS_ONLY_BANNER in shell.status_message  # the one-time banner surfaced
    # overlap is gated OFF (no coordinates/patches) → no overlap dock built on selection
    assert shell.overlap_dock is None
    # the analysis substrate still works (corrected layer present)
    assert shell.show_histogram() is not None


def test_load_project_replaces_prior_project(shell, tmp_path) -> None:
    p1, *_ = _round_trip_store(tmp_path, n=3, t=12, name="rt1.tether", seed=1)
    shell.load_project(p1.path)
    assert shell.molecule_list.count() == 3
    assert shell.overlap_dock is not None
    first_dock = shell.overlap_dock

    # a second, different store replaces the first and rebuilds the overlap dock fresh
    p2, *_ = _round_trip_store(tmp_path, n=2, t=8, name="rt2.tether", seed=2)
    shell.load_project(p2.path)

    assert shell.molecule_list.count() == 2
    assert shell.overlap_dock is not None
    assert shell.overlap_dock is not first_dock  # rebuilt, not the stale prior dock


def test_load_project_missing_file_is_fail_soft(shell, tmp_path) -> None:
    # First load a valid project so there is real prior state a bad open must preserve.
    project, keys, *_ = _round_trip_store(tmp_path, n=3, t=12)
    shell.load_project(project.path)
    assert shell.molecule_list.count() == 3
    prior_overlap = shell.overlap_dock
    assert prior_overlap is not None

    # A bad open is atomic: every fallible read runs before any state mutates, so the
    # previously loaded project (molecules + seams + docks) stays fully in place — the
    # only change is the failure reported in the status bar (the fail-soft contract).
    opened = shell.load_project(tmp_path / "nope.tether")

    assert opened is None
    assert "Open project failed" in shell.status_message
    assert shell.molecule_list.count() == 3  # prior molecules preserved
    assert shell.overlap_dock is prior_overlap  # prior seams/docks untouched (not reset)
    assert shell.show_histogram() is not None  # the prior histogram seam still resolves


# --------------------------------------------------------------------------- #
# reachability — &File menu + wizard single-produced auto-open
# --------------------------------------------------------------------------- #


def test_file_menu_exposes_open_action(shell) -> None:
    labels = [a.text() for a in shell.file_menu.actions()]
    assert labels == ["&Open project…"]


def test_import_deeplasi_bundle_opens_single_produced_live(shell, tmp_path) -> None:
    project, keys, *_ = _round_trip_store(tmp_path, n=3, t=12)
    stub = _StubWizardDialog((project.path,))

    result = shell.import_deeplasi_bundle(dialog_factory=lambda: stub)

    assert tuple(result) == (project.path,)
    # the single produced project was opened live for curate/idealize (§7.8 round-trip)
    assert shell.molecule_list.count() == 3
    assert "3 molecule(s)" in shell.status_message


def test_import_deeplasi_bundle_multiple_reports_without_auto_open(shell, tmp_path) -> None:
    # With several projects written, which to open is the curator's call — report them
    # and leave &File → Open as the picker (no auto-open, no molecules loaded).
    produced = (tmp_path / "a.tether", tmp_path / "b.tether")
    stub = _StubWizardDialog(produced)

    result = shell.import_deeplasi_bundle(dialog_factory=lambda: stub)

    assert tuple(result) == produced
    assert "wrote 2 project(s)" in shell.status_message
    assert shell.molecule_list.count() == 0
