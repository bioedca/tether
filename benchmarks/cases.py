# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The benchmark cases: one per operation of #722, each at two input sizes (ADR-0078).

A case is a ``setup(size, workdir)`` that builds its input outside the timed part and returns
the zero-argument callable to time, plus a dictionary of extra values to report (for example
the ``.tether`` size). All inputs come from the test-only generator ``tests/_synth.py`` (#701),
so the same seed gives the same input on base and head.

``gate`` says whether ``bench.py compare`` may fail a pull request on the case. It is set from
the noise report in the pull request that added the suite (#722): a case
whose spread on a GitHub runner was too large for the 1.5 limit only reports.

This module imports ``tether`` lazily, inside each setup, so ``bench.py`` can put the source
tree under test on ``sys.path`` before the first import.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

Setup = Callable[[str, Path], tuple[Callable[[], object], dict[str, Any]]]

#: The two input sizes. ``n``/``t`` are the trace sets (molecules, frames); ``movie`` is the
#: ``(frames, height, half_width)`` of the two-channel movie.
SIZES: dict[str, dict[str, Any]] = {
    "small": {"n": 40, "t": 400, "movie": (40, 64, 64)},
    "large": {"n": 160, "t": 1600, "movie": (120, 128, 128)},
}
_SEED = 722


@dataclass(frozen=True)
class Case:
    name: str
    group: str  # "default", "gui" or "sidecar"
    gate: bool
    setup: Setup


def _traces(size: str):
    from _synth import fret_traces

    p = SIZES[size]
    return fret_traces(p["n"], p["t"], seed=_SEED, bleach_probability=1.0 / p["t"])


def _movie(size: str, workdir: Path):
    from _synth import two_channel_movie, write_movie_tiff

    frames, height, half = SIZES[size]["movie"]
    movie = two_channel_movie(
        seed=_SEED, n_frames=frames, height=height, half_width=half, noise_sigma=3.0
    )
    return movie, write_movie_tiff(movie, workdir / "bench_movie.tif")


def _store(size: str, workdir: Path) -> Path:
    from tether.project.extract import ExtractOptions, extract_movie

    _, tif = _movie(size, workdir)
    out = workdir / "bench.tether"
    extract_movie(tif, out, options=ExtractOptions(window=21), overwrite=True)
    return out


def _with_tails(size: str):
    """Trace lists whose acceptor bleaches before the donor, for the α and γ estimators."""
    tr = _traces(size)
    donors, acceptors, a_pb, d_pb = [], [], [], []
    for m in range(tr.donor.shape[0]):
        d, a = tr.donor[m].copy(), tr.acceptor[m].copy()
        db = int(tr.bleach_frame[m])
        ab = max(1, (2 * db) // 3)
        d[ab:db] = d[ab:db] + a[ab:db]  # the donor regains the acceptor's share of the signal
        a[ab:db] = 0.08 * d[ab:db]  # leakage of the donor into the acceptor channel
        donors.append(d)
        acceptors.append(a)
        a_pb.append(ab)
        d_pb.append(db)
    return donors, acceptors, a_pb, d_pb


def _apparent_e(size: str) -> list[np.ndarray]:
    tr = _traces(size)
    return [a / (d + a) for d, a in zip(tr.donor, tr.acceptor, strict=True)]


def _spot_finding(size: str, workdir: Path):
    from tether.imaging.detect import detect_spots, detection_image

    movie, _ = _movie(size, workdir)
    donor = movie.frames[:, :, : movie.frames.shape[2] // 2]

    def run() -> object:
        image = detection_image(donor)
        return detect_spots(image / image.max())

    return run, {}


def _extraction(size: str, workdir: Path):
    from tether.project.extract import ExtractOptions, extract_movie

    _, tif = _movie(size, workdir)
    out = workdir / "bench.tether"

    def run() -> object:
        return extract_movie(tif, out, options=ExtractOptions(window=21), overwrite=True)

    run()
    return run, {"tether_bytes": out.stat().st_size}


def _background(size: str, workdir: Path):
    from tether.imaging.aperture import integrate_traces

    movie, _ = _movie(size, workdir)
    donor = movie.frames[:, :, : movie.frames.shape[2] // 2]
    return lambda: integrate_traces(donor, movie.donor_xy, window=21), {}


def _photobleach(size: str, workdir: Path):
    from tether.fret.photobleach import detect_photobleach

    tr = _traces(size)
    return lambda: [
        detect_photobleach(d, a) for d, a in zip(tr.donor, tr.acceptor, strict=True)
    ], {}


def _leakage(size: str, workdir: Path):
    from tether.fret.leakage import estimate_leakage_alpha

    donors, acceptors, a_pb, d_pb = _with_tails(size)
    return lambda: estimate_leakage_alpha(donors, acceptors, a_pb, d_pb), {}


def _gamma(size: str, workdir: Path):
    from tether.fret.gamma import estimate_gamma

    donors, acceptors, a_pb, d_pb = _with_tails(size)
    alphas = [0.08] * len(donors)
    return lambda: estimate_gamma(donors, acceptors, alphas, a_pb, d_pb), {}


def _histogram(size: str, workdir: Path):
    from tether.analysis.histogram import apparent_e_histogram

    pooled = np.concatenate(_apparent_e(size))
    return lambda: apparent_e_histogram(pooled), {}


def _cross_correlation(size: str, workdir: Path):
    from tether.analysis.crosscorr import cross_correlation

    tr = _traces(size)
    pairs = list(zip(tr.donor, tr.acceptor, strict=True))
    return lambda: [cross_correlation(d, a, max_lag=50) for d, a in pairs], {}


def _cloud(size: str, workdir: Path):
    from tether.analysis.cloud import raw_fret_cloud

    chunks = _apparent_e(size)
    chunks = chunks[: len(chunks) // 16]  # the KDE costs points x grid: 800 and 16 000 points
    return lambda: raw_fret_cloud(chunks), {}


def _transition_density(size: str, workdir: Path):
    from tether.analysis.tdp import transition_density

    tr = _traces(size)
    levels = np.where(tr.states >= 0, tr.efficiency[np.maximum(tr.states, 0)], np.nan)
    return lambda: transition_density(list(levels)), {}


def _dwell_times(size: str, workdir: Path):
    from tether.analysis.dwell import fit_survival, state_dwells, survival_curve

    paths = list(_traces(size).states)

    def run() -> object:
        dwells = state_dwells(paths, no_state=-1)
        tau, surv = survival_curve(dwells.lengths[0])
        return fit_survival(tau, surv)

    return run, {}


def _project_open(size: str, workdir: Path):
    from tether.imaging.extract import read_molecules, read_traces
    from tether.project.core import Project

    store = _store(size, workdir)

    def run() -> object:
        Project.open(store)
        return read_molecules(store), read_traces(store)

    return run, {}


def _project_save(size: str, workdir: Path):
    from tether.project.export import export_subset_tether

    store = _store(size, workdir)
    out = workdir / "bench_saved.tether"
    return lambda: export_subset_tether(store, out, include_raw=True, overwrite=True), {}


def _project_export(size: str, workdir: Path):
    from tether.project.export import export_molecule_table_csv

    store = _store(size, workdir)
    out = workdir / "bench_table.csv"
    return lambda: export_molecule_table_csv(store, out), {}


def _trace_display(size: str, workdir: Path):
    from PySide6.QtWidgets import QApplication

    from tether.gui.trace_dock import TraceDock, TraceView

    app = QApplication.instance() or QApplication([])
    tr = _traces(size)
    views = [
        TraceView(donor=d, acceptor=a, frame_time=0.1, name=f"m{i}")
        for i, (d, a) in enumerate(zip(tr.donor[:8], tr.acceptor[:8], strict=True))
    ]
    dock = TraceDock()
    dock.set_trace(views[0])

    def run() -> object:
        for view in views:  # display each trace, then navigate to the next
            dock.set_trace(view)
        app.processEvents()
        return dock

    return run, {}


def _idealize_sidecar(size: str, workdir: Path):
    from tether.idealize.driver import run_vbfret
    from tether.idealize.smd import write_smd

    tr = _traces(size)
    n = tr.donor.shape[0] // 4  # 10 and 40 molecules: the sidecar fits each one
    smd = write_smd(workdir / "bench.hdf5", np.stack([tr.donor[:n], tr.acceptor[:n]], axis=-1))
    return lambda: run_vbfret(smd, nstates=2, nrestarts=1, timeout=1800.0), {}


CASES: dict[str, Case] = {
    c.name: c
    for c in (
        Case("spot_finding", "default", True, _spot_finding),
        Case("extraction", "default", True, _extraction),
        Case("background", "default", True, _background),
        Case("photobleach", "default", True, _photobleach),
        Case("leakage_alpha", "default", True, _leakage),
        Case("gamma", "default", True, _gamma),
        Case("histogram", "default", True, _histogram),
        Case("cross_correlation", "default", True, _cross_correlation),
        Case("cloud", "default", True, _cloud),
        Case("transition_density", "default", True, _transition_density),
        Case("dwell_times", "default", True, _dwell_times),
        Case("project_open", "default", True, _project_open),
        Case("project_save", "default", True, _project_save),
        Case("project_export", "default", True, _project_export),
        Case("trace_display", "gui", True, _trace_display),
        Case("idealize_sidecar", "sidecar", True, _idealize_sidecar),
    )
}
