# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""NFR-PERF budget verification (PRD §8, §11.2, §12.10; ADR-0032).

The **light §9 gate** (not an SLA matrix) first verified at M3:

* **Headless** — the ``.tether`` size envelope (measured on a real extracted store vs the
  float32 six-layer model, plus the stored dtype of every ``/traces`` layer), the
  overnight projection of the **extraction stage only** (a real slice extraction scaled by
  pixel volume to the reference movie × ~100-movie condition), and the pure
  projection/timing helpers.
* **``@pytest.mark.gui``** — the per-trace latency of a real
  :class:`~tether.gui.trace_dock.TraceDock` (offscreen, CPU-rendered, so it runs on all
  three OSes): the data update alone, and the data update plus the paint, which is what the
  ≈ 100 ms budget is about.

Wall-clock assertions use :func:`~tether.project.perf.min_runtime` (minimum over repeats).
Each timed test has two gates (#731):

* the **PRD budget** (the hard gate, unchanged), which is far above the measured cost, and
* a **regression ceiling** — a committed baseline times a stated factor — which is far
  below the budget, so a slowdown of the size the audit demonstrated fails here while the
  PRD gate still passes. The factor is wide because the baseline is one machine and the
  required matrix runs on three shared runners; it is not a budget and loosens none.

A history of the measured values (to find a slowdown inside the ceiling) is #722's work.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("h5py")

import numpy as np  # noqa: E402

from tether.project.perf import (  # noqa: E402
    FIXED_STORAGE_BYTES_PER_MOLECULE,
    MAX_CONDITION_BYTES,
    OVERNIGHT_WINDOW_HOURS,
    PER_TRACE_LATENCY_BUDGET_S,
    REFERENCE_MOVIE_PIXELS,
    TRACE_BYTES_PER_MOLECULE_FRAME,
    TRACE_STORAGE_ENVELOPE_BYTES_PER_MOLECULE_FRAME,
    OvernightEstimate,
    StoreSizeReport,
    estimate_condition_bytes,
    estimate_molecule_bytes,
    measure_store_size,
    min_runtime,
    project_overnight,
    scale_seconds_to_reference_movie,
)

# --- regression ceilings (#731) ----------------------------------------------
#
# Baselines measured with this module's own fixtures on the cloud test container
# (4 cores, base lock, offscreen Qt) on 2026-11-11, and cross-checked against the audit
# machine of #695 (load average 8 to 15). They are minima over the test's repeats.

#: ``extract_movie`` on the 64×96×200 slice: 29 ms here, 27.6 to 54.7 ms in the audit.
_EXTRACT_SLICE_BASELINE_S = 0.030
#: ``TraceDock.set_trace`` on a 1740-frame trace, no paint: 0.62 to 0.75 ms here,
#: 0.76 to 1.05 ms in the audit.
_SET_TRACE_BASELINE_S = 0.001
#: A slowdown above this factor of the baseline fails. Wide on purpose: the required
#: matrix runs on shared macOS, Windows and Linux runners that are slower and noisier
#: than the baseline machine. The audit's demonstrations (+1 s per extraction, +90 ms per
#: ``set_trace``) are about 35 and 90 times the baselines, so they fail here, while both
#: stay inside the PRD budgets.
_REGRESSION_FACTOR = 10.0

#: Gzip-compressed ``/traces`` bytes per molecule-frame on the size fixture: 17.72 B with
#: float32 (the dtype the store declares), 25.40 B with float64. The PRD-derived
#: envelope (36 B) cannot tell these apart, because it compares a compressed size with an
#: uncompressed one; this ceiling can.
_TRACES_COMPRESSED_CEILING_B = 22.0


# --- synthetic dual-channel movie (mirrors test_extract_cli) -----------------

_BG, _AMP, _SIGMA = 80.0, 400.0, 1.5
_SHAPE = (64, 96)  # 48-px halves; room for the default 21-px aperture
_DONOR_CENTERS = np.array([[12.0, 12.0], [24.0, 40.0], [16.0, 52.0]])
_ACCEPTOR_CENTERS = np.array([[61.0, 12.0], [73.0, 40.0], [65.0, 52.0]])


def _write_movie(path: Path, n_frames: int) -> int:
    """Write a synthetic big-endian dual-channel movie; return its pixel volume.

    Adds mild per-frame variation + noise so the cached traces are realistically (partly)
    incompressible — a constant-in-time movie would compress to ~nothing and understate
    the size envelope.
    """
    import tifffile  # noqa: PLC0415

    base = np.full(_SHAPE, _BG, dtype=np.float64)
    rows, cols = np.mgrid[0 : _SHAPE[0], 0 : _SHAPE[1]]
    for x, y in np.vstack([_DONOR_CENTERS, _ACCEPTOR_CENTERS]):
        base += _AMP * np.exp(-((rows - y) ** 2 + (cols - x) ** 2) / (2.0 * _SIGMA**2))
    rng = np.random.default_rng(0)
    stack = np.empty((n_frames, *_SHAPE), dtype=np.float64)
    for t in range(n_frames):
        stack[t] = base * (0.85 + 0.3 * rng.random()) + rng.normal(0.0, 5.0, _SHAPE)
    be = np.ascontiguousarray(np.clip(stack, 0, 65535), dtype=">u2")
    tifffile.imwrite(path, be, photometric="minisblack", byteorder=">")
    return _SHAPE[0] * _SHAPE[1] * n_frames


def _extract(tmp_path: Path, n_frames: int) -> tuple[Path, int]:
    """Extract a synthetic movie into a ``.tether``; return (path, movie_pixels)."""
    from tether.project.extract import ExtractOptions, extract_movie  # noqa: PLC0415

    movie = tmp_path / "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif"
    out = tmp_path / "video10.tether"
    pixels = _write_movie(movie, n_frames)
    extract_movie(movie, out, options=ExtractOptions(window=21))
    return out, pixels


# --- min_runtime -------------------------------------------------------------


def test_min_runtime_returns_minimum(monkeypatch: pytest.MonkeyPatch) -> None:
    # A scripted clock: the three runs take 5 s, 1 s and 3 s. The minimum is 1 s; a
    # maximum (5), a first (5), a last (3) or a mean (3) would all differ.
    import tether.project.perf as perf  # noqa: PLC0415

    ticks = iter([0.0, 5.0, 10.0, 11.0, 20.0, 23.0])
    monkeypatch.setattr(perf.time, "perf_counter", lambda: next(ticks))
    calls = {"n": 0}

    def fn() -> None:
        calls["n"] += 1

    dt = min_runtime(fn, repeats=3)
    assert calls["n"] == 3
    assert dt == 1.0


def test_min_runtime_rejects_zero_repeats() -> None:
    with pytest.raises(ValueError, match="repeats must be >= 1"):
        min_runtime(lambda: None, repeats=0)


# --- .tether size envelope ---------------------------------------------------


def test_store_size_envelope_on_real_extraction(tmp_path: Path, capsys) -> None:
    import h5py  # noqa: PLC0415

    # A real extracted store: the six float32 /traces arrays are the dominant cost.
    path, _ = _extract(tmp_path, n_frames=256)
    report = measure_store_size(path)

    assert report.n_molecules == 3
    assert report.n_frames == 256
    assert report.traces_bytes > 0
    # The storage dtype, read directly: every /traces layer is float32, and the six
    # layers hold exactly 6 · 4 = 24 B per molecule-frame before compression. A float64
    # store fails here (8 B per value, 48 B per molecule-frame) whatever gzip does.
    with h5py.File(path, "r") as f:
        layers = {name: ds for name, ds in f["traces"].items() if isinstance(ds, h5py.Dataset)}
        assert len(layers) == 6, sorted(layers)
        for name, ds in layers.items():
            assert ds.dtype == np.dtype("<f4"), f"/traces/{name} is stored as {ds.dtype}"
        uncompressed = sum(ds.dtype.itemsize * ds.size for ds in layers.values())
    assert uncompressed / (report.n_molecules * report.n_frames) == TRACE_BYTES_PER_MOLECULE_FRAME
    # The PRD-derived envelope stays as it is (36 B, compressed vs uncompressed) ...
    assert report.traces_bytes_per_molecule_frame <= TRACE_STORAGE_ENVELOPE_BYTES_PER_MOLECULE_FRAME
    assert report.within_trace_envelope
    # ... and a ceiling near the measured 17.72 B finds the compressed cost of a float64
    # store (25.40 B), which the 36 B envelope lets through.
    assert report.traces_bytes_per_molecule_frame <= _TRACES_COMPRESSED_CEILING_B, (
        f"/traces {report.traces_bytes_per_molecule_frame:.2f} B/mol/frame on disk > "
        f"{_TRACES_COMPRESSED_CEILING_B} B ceiling"
    )
    # The /traces + /patches contributions are both measured (their accumulation
    # branches in measure_store_size are exercised, and grow with T unlike the fixed
    # skeleton overhead that dominates the whole store at this tiny molecule count).
    assert report.patches_bytes > 0
    # Record the measured envelope (visible with -s).
    print(
        f"[size] mol={report.n_molecules} frames={report.n_frames} "
        f"total={report.total_bytes:,}B traces={report.traces_bytes:,}B "
        f"traces B/mol/frame={report.traces_bytes_per_molecule_frame:.2f} "
        f"(envelope {TRACE_STORAGE_ENVELOPE_BYTES_PER_MOLECULE_FRAME})"
    )
    _ = capsys  # keep the fixture wired for the recorded output


def test_measure_store_size_empty_report_is_safe() -> None:
    report = StoreSizeReport(
        total_bytes=0, traces_bytes=0, patches_bytes=0, n_molecules=0, n_frames=0
    )
    assert report.bytes_per_molecule == 0.0
    assert report.traces_bytes_per_molecule_frame == 0.0


def test_estimate_molecule_and_condition_bytes() -> None:
    # One molecule at T frames = six float32 layers + fixed overhead.
    assert estimate_molecule_bytes(1000) == TRACE_BYTES_PER_MOLECULE_FRAME * 1000 + (
        FIXED_STORAGE_BYTES_PER_MOLECULE
    )
    assert estimate_molecule_bytes(0) == FIXED_STORAGE_BYTES_PER_MOLECULE
    # The reference condition (~100 movies × ~250 mol × 1700 frames) stays modest.
    condition = estimate_condition_bytes()
    assert condition <= MAX_CONDITION_BYTES
    assert condition > 0


def test_estimate_molecule_bytes_rejects_negative_frames() -> None:
    with pytest.raises(ValueError, match="n_frames must be non-negative"):
        estimate_molecule_bytes(-1)


# --- overnight extract-time projection ---------------------------------------


def test_scale_seconds_to_reference_movie() -> None:
    # A slice of half the reference pixel volume, measured at 10 s, projects to 20 s.
    projected = scale_seconds_to_reference_movie(10.0, REFERENCE_MOVIE_PIXELS // 2)
    assert projected == pytest.approx(20.0, rel=1e-9)


def test_scale_seconds_rejects_bad_inputs() -> None:
    with pytest.raises(ValueError, match="measured_pixels must be positive"):
        scale_seconds_to_reference_movie(1.0, 0)
    with pytest.raises(ValueError, match="measured_seconds must be non-negative"):
        scale_seconds_to_reference_movie(-1.0, 10)


def test_project_overnight_math_and_gates() -> None:
    est = project_overnight(30.0, n_movies=100, window_hours=12.0)
    assert isinstance(est, OvernightEstimate)
    assert est.total_seconds == 3000.0
    assert est.total_hours == pytest.approx(3000.0 / 3600.0)
    assert est.fits_window  # 0.83 h << 12 h
    assert est.headroom == pytest.approx(12.0 / (3000.0 / 3600.0))
    # A pathologically slow per-movie time overflows the window.
    slow = project_overnight(3600.0, n_movies=100, window_hours=12.0)
    assert not slow.fits_window  # 100 h > 12 h


def test_project_overnight_rejects_bad_inputs() -> None:
    with pytest.raises(ValueError, match="per_movie_seconds must be non-negative"):
        project_overnight(-1.0)
    with pytest.raises(ValueError, match="n_movies must be >= 1"):
        project_overnight(1.0, n_movies=0)
    with pytest.raises(ValueError, match="window_hours must be positive"):
        project_overnight(1.0, window_hours=0.0)


def test_overnight_extraction_only_envelope_from_real_extraction(tmp_path: Path) -> None:
    """Project the **extraction stage only** of a condition and check it fits the window.

    What this measures, and what it does not (#731, PRJ-U2):

    * It times ``extract_movie`` alone. Correction and idealization — the rest of the
      overnight "extract + correct + idealize" of PRD §8 — are not in the number; the
      window's remaining headroom is what they must fit in, and the full-movie run of
      the gated ``large-fixtures.yml`` tier is where the whole pipeline is measured.
    * It scales by pixel volume only. The slice has 3 molecules and the reference movie
      about ``REFERENCE_MOLECULES_PER_MOVIE`` (250), so per-molecule integration cost is
      not scaled up; the projection is low by whatever share that cost has.

    Two gates: the PRD window (hard), and the regression ceiling on the slice time.
    """
    from tether.project.extract import ExtractOptions, extract_movie  # noqa: PLC0415

    movie = tmp_path / "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif"
    pixels = _write_movie(movie, n_frames=200)

    def run_extract() -> None:
        out = tmp_path / "video10.tether"
        extract_movie(movie, out, options=ExtractOptions(window=21), overwrite=True)

    per_slice = min_runtime(run_extract, repeats=3)
    per_movie = scale_seconds_to_reference_movie(per_slice, pixels)
    est = project_overnight(per_movie)

    assert est.fits_window, (
        f"projected {est.total_hours:.2f} h > {OVERNIGHT_WINDOW_HOURS} h window "
        f"(per-slice {per_slice * 1000:.1f} ms, per-movie {per_movie:.1f} s)"
    )
    ceiling = _EXTRACT_SLICE_BASELINE_S * _REGRESSION_FACTOR
    assert per_slice <= ceiling, (
        f"slice extraction {per_slice * 1000:.1f} ms > regression ceiling "
        f"{ceiling * 1000:.0f} ms ({_REGRESSION_FACTOR:g} x the "
        f"{_EXTRACT_SLICE_BASELINE_S * 1000:.0f} ms baseline)"
    )
    print(
        f"[overnight, extraction only] per-slice={per_slice * 1000:.1f}ms "
        f"per-movie~{per_movie:.1f}s "
        f"condition({est.n_movies})~{est.total_hours:.2f}h "
        f"window={est.window_hours}h headroom x{est.headroom:.1f}"
    )


# --- per-trace render+navigate latency budget (GUI) --------------------------

_HAS_QT = all(importlib.util.find_spec(m) is not None for m in ("pyqtgraph", "PySide6"))
_needs_qt = pytest.mark.skipif(not _HAS_QT, reason="pyqtgraph/PySide6 not installed")


def _realistic_traces(n: int, n_frames: int) -> list:
    from tether.gui.trace_dock import TraceView  # noqa: PLC0415

    rng = np.random.default_rng(1)
    frames = np.arange(n_frames)
    out = []
    for i in range(n):
        donor = np.clip(2000 * np.exp(-frames / 800.0) + rng.normal(0, 40, n_frames), 0, None)
        acceptor = np.clip(1500 * np.exp(-frames / 500.0) + rng.normal(0, 40, n_frames), 0, None)
        out.append(TraceView(donor=donor, acceptor=acceptor, frame_time=0.1, name=f"m{i}"))
    return out


def _shown_dock(qtbot):
    from tether.gui.trace_dock import TraceDock  # noqa: PLC0415

    dock = TraceDock()
    qtbot.addWidget(dock.widget)
    dock.widget.resize(900, 600)  # a dock inside the main window, not full screen
    dock.widget.show()
    qtbot.waitExposed(dock.widget)
    return dock


@pytest.mark.gui
@_needs_qt
def test_per_trace_navigate_data_update_ceiling(qtbot) -> None:
    """The data update of a navigation step (``set_trace``, no paint) stays fast.

    ``set_trace`` only gives the plot items their new data; the paint happens later in
    the event loop. This times that update alone against the regression ceiling; the
    paint is timed in :func:`test_per_trace_render_navigate_budget`.
    """
    # A reference-length trace (~1700 frames, the UCKOPSB movie length).
    traces = _realistic_traces(4, n_frames=1740)
    dock = _shown_dock(qtbot)
    dock.set_trace(traces[0])  # warm up (first render builds the bar items)

    cursor = {"i": 0}

    def navigate() -> None:
        cursor["i"] = (cursor["i"] + 1) % len(traces)
        dock.set_trace(traces[cursor["i"]])

    per_trace = min_runtime(navigate, repeats=12)
    assert per_trace < PER_TRACE_LATENCY_BUDGET_S
    ceiling = _SET_TRACE_BASELINE_S * _REGRESSION_FACTOR
    assert per_trace <= ceiling, (
        f"set_trace {per_trace * 1000:.2f} ms > regression ceiling {ceiling * 1000:.0f} ms "
        f"({_REGRESSION_FACTOR:g} x the {_SET_TRACE_BASELINE_S * 1000:.0f} ms baseline)"
    )
    print(f"[latency] set_trace (data update) min={per_trace * 1000:.2f}ms")


@pytest.mark.gui
@_needs_qt
def test_per_trace_render_navigate_budget(qtbot) -> None:
    """Render + navigate, paint included, stays under the PRD ≈ 100 ms budget.

    The dock is shown, and each timed step is ``set_trace`` followed by
    ``QWidget.grab()``, which renders the whole dock (plots and histograms) into a pixmap
    synchronously — so the paint is inside the timed call, not left to a later event
    loop turn (#731, PRJ-U3). Offscreen and CPU-rendered, so it runs on all three OSes.
    """
    traces = _realistic_traces(4, n_frames=1740)
    dock = _shown_dock(qtbot)
    dock.set_trace(traces[0])
    assert not dock.widget.grab().isNull()  # warm up the paint path too

    cursor = {"i": 0}

    def render_navigate() -> None:
        cursor["i"] = (cursor["i"] + 1) % len(traces)
        dock.set_trace(traces[cursor["i"]])
        dock.widget.grab()

    per_trace = min_runtime(render_navigate, repeats=12)
    assert per_trace < PER_TRACE_LATENCY_BUDGET_S, (
        f"per-trace render+navigate {per_trace * 1000:.1f} ms exceeds the "
        f"{PER_TRACE_LATENCY_BUDGET_S * 1000:.0f} ms budget"
    )
    print(
        f"[latency] per-trace render+navigate (painted) min={per_trace * 1000:.2f}ms budget=100ms"
    )
