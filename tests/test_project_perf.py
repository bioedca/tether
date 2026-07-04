# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""NFR-PERF budget verification (PRD §8, §11.2, §12.10; ADR-0032).

The **light §9 gate** (not an SLA matrix) first verified at M3:

* **Headless** — the ``.tether`` size envelope (measured on a real extracted store vs the
  float32 six-layer model), the overnight extract-time projection (a real slice
  extraction scaled by pixel volume to the reference movie × ~100-movie condition), and
  the pure projection/timing helpers.
* **``@pytest.mark.gui``** — the per-trace render+navigate latency budget (≈ 100 ms),
  timed against a real :class:`~tether.gui.trace_dock.TraceDock` (offscreen, CPU-rendered,
  so it runs on all three OSes; pixel rendering is left to the live computer-use smoke).

Wall-clock assertions use :func:`~tether.project.perf.min_runtime` (minimum over repeats)
and carry generous headroom over the measured cost, so they flag a gross regression
(a super-linear render, a storage-dtype bloat) without flaking on shared CI runners.
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


def test_min_runtime_returns_minimum() -> None:
    calls = {"n": 0}

    def fn() -> None:
        calls["n"] += 1

    dt = min_runtime(fn, repeats=4)
    assert calls["n"] == 4
    assert dt >= 0.0


def test_min_runtime_rejects_zero_repeats() -> None:
    with pytest.raises(ValueError, match="repeats must be >= 1"):
        min_runtime(lambda: None, repeats=0)


# --- .tether size envelope ---------------------------------------------------


def test_store_size_envelope_on_real_extraction(tmp_path: Path, capsys) -> None:
    # A real extracted store: the six float32 /traces arrays are the dominant cost.
    path, _ = _extract(tmp_path, n_frames=256)
    report = measure_store_size(path)

    assert report.n_molecules == 3
    assert report.n_frames == 256
    assert report.traces_bytes > 0
    # The N-robust claim: gzip'd on-disk /traces stay within the float32 six-layer
    # envelope (~18 B measured vs the 36 B envelope; float64 would blow it at 48 B).
    assert report.traces_bytes_per_molecule_frame <= TRACE_STORAGE_ENVELOPE_BYTES_PER_MOLECULE_FRAME
    assert report.within_trace_envelope
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


def test_overnight_envelope_from_real_extraction(tmp_path: Path) -> None:
    # Time a real slice extraction, scale by pixel volume to the reference full movie,
    # project across a ~100-movie condition, and assert it fits the overnight window.
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
    print(
        f"[overnight] per-slice={per_slice * 1000:.1f}ms per-movie~{per_movie:.1f}s "
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


@pytest.mark.gui
@_needs_qt
def test_per_trace_render_navigate_budget(qtbot) -> None:
    from tether.gui.trace_dock import TraceDock  # noqa: PLC0415

    # A reference-length trace (~1700 frames, the UCKOPSB movie length).
    traces = _realistic_traces(4, n_frames=1740)
    dock = TraceDock()
    qtbot.addWidget(dock.widget)
    dock.set_trace(traces[0])  # warm up (first render builds the bar items)

    cursor = {"i": 0}

    def render_navigate() -> None:
        cursor["i"] = (cursor["i"] + 1) % len(traces)
        dock.set_trace(traces[cursor["i"]])

    per_trace = min_runtime(render_navigate, repeats=12)
    assert per_trace < PER_TRACE_LATENCY_BUDGET_S, (
        f"per-trace render+navigate {per_trace * 1000:.1f} ms exceeds the "
        f"{PER_TRACE_LATENCY_BUDGET_S * 1000:.0f} ms budget"
    )
    print(f"[latency] per-trace render+navigate min={per_trace * 1000:.2f}ms budget=100ms")
