# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""A2 2-D time-vs-signal occupancy heatmap (M6, FR-ANALYZE; Appendix C plot A2).

Covers tMAVEN's A2 plot (``data_hist2d.py``) raw / start-synchronized mode
(:func:`~tether.analysis.histogram.time_signal_histogram2d` and its store-level
:func:`~tether.analysis.histogram.population_time_signal_histogram2d`): each
molecule's windowed apparent E binned into a ``(time, signal)`` grid, aligned to
its analysis-window start. Frame index drives the time column (NaN / out-of-range
/ beyond-``time_bins`` frames drop without shifting later frames — faithful to
``histogram_raw`` after ``sync_start``). All headless (no Qt) → runs in the base CI
matrix; the store is seeded as post-extraction data under the M0-frozen schema
(mirrors ``test_analysis_per_condition_histogram._build_store``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("h5py")

import h5py  # noqa: E402
import numpy as np  # noqa: E402

from tether.analysis import (  # noqa: E402
    DEFAULT_SIGNAL_BINS,
    DEFAULT_SIGNAL_RANGE,
    DEFAULT_TIME_BINS,
    DEFAULT_TIME_DT,
    Histogram2D,
    population_time_signal_histogram2d,
    time_signal_histogram2d,
)
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
from tether.project import Project  # noqa: E402
from tether.project.labels import CurationLabel  # noqa: E402

_PARSED = parse_filename("Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif")
_WINDOW = 21


# --- helpers ------------------------------------------------------------------


def _sbin(
    value: float,
    rng: tuple[float, float] = DEFAULT_SIGNAL_RANGE,
    nbins: int = DEFAULT_SIGNAL_BINS,
) -> int:
    """The signal-bin index a value lands in (matches numpy/tMAVEN left-closed bins)."""
    lo, hi = rng
    return int((value - lo) / (hi - lo) * nbins)


def _e_traces(e_matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(n, t) donor/acceptor whose corrected apparent E is ``e_matrix`` per frame.

    ``E = A / (D + A)`` with ``D = (1 - E)·1000``, ``A = E·1000``. A NaN entry in
    ``e_matrix`` is encoded as ``D = A = 0`` so :func:`apparent_fret` yields NaN
    there (``D + A == 0``), exercising the non-finite drop.
    """
    e = np.asarray(e_matrix, dtype="float64")
    donor = (1.0 - e) * 1000.0
    acceptor = e * 1000.0
    nan = np.isnan(e)
    donor[nan] = 0.0
    acceptor[nan] = 0.0
    return donor, acceptor


def _distinct_coords(n: int) -> np.ndarray:
    return np.array([[12.0 + 1.7 * i, 14.0 + 2.3 * (i % 7)] for i in range(n)], dtype="float64")


def _reg_map() -> RegistrationMap:
    poly = PolyTransform2D(
        a=np.array([0.0, 1.0, 0.0, 0.0, 0.0, 0.0]),
        b=np.array([0.0, 0.0, 1.0, 0.0, 0.0, 0.0]),
        norm_xy=np.eye(3),
        norm_uv=np.eye(3),
    )
    return RegistrationMap(
        reference_channel=1,
        moving_channel=2,
        ref_to_moving=poly,
        moving_to_ref=poly,
        rms_residual=0.1,
        n_control_points=100,
    )


def _integrated(intensity: np.ndarray) -> IntegratedTraces:
    intensity = np.asarray(intensity, dtype="float64")
    n = intensity.shape[0]
    background = np.full_like(intensity, 100.0)
    return IntegratedTraces(
        intensity=intensity,
        total=intensity + background,
        background=background,
        valid=np.ones(n, dtype=bool),
    )


def _build_store(
    tmp_path: Path,
    e_matrix: np.ndarray,
    *,
    windows: list[tuple[int, int]] | None = None,
    rejected: list[bool] | None = None,
    name: str = "exp.tether",
) -> tuple[Project, list[str]]:
    """A ``.tether`` whose molecule ``i`` carries the per-frame apparent E ``e_matrix[i]``.

    ``windows[i]`` sets that molecule's ``analysis_window`` (defaults to the full
    trace extent); ``rejected[i]`` sticky-rejects it. Everything else mirrors a
    single-movie extraction under the M0-frozen schema.
    """
    e_matrix = np.asarray(e_matrix, dtype="float64")
    n, n_frames = e_matrix.shape
    donor, acceptor = _e_traces(e_matrix)
    coords = _distinct_coords(n)
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
        donor_patches=np.zeros((n, _WINDOW, _WINDOW), dtype="float32"),
        acceptor_patches=np.zeros((n, _WINDOW, _WINDOW), dtype="float32"),
        window=_WINDOW,
        disk_radius=3.0,
        ring_inner=6.0,
        ring_outer=8.0,
        bg_window=10,
    )
    movie = MovieMetadata(
        movie_id="mov-1",
        sha256="a" * 64,
        n_frames=n_frames,
        height=64,
        width=64,
        donor_geometry=ChannelGeometry(crop=(1, 1, 64, 64)),
        acceptor_geometry=ChannelGeometry(crop=(1, 65, 64, 128)),
    )
    path = tmp_path / name
    create_project(path, overwrite=True)
    write_extraction(
        path,
        movie=movie,
        molecules=mols,
        traces=traces,
        parsed=_PARSED,
        registration_map=_reg_map(),
    )
    with h5py.File(path, "r+") as f:
        table = f["molecules"]["table"][:]
        for i in range(n):
            win = (0, n_frames) if windows is None else windows[i]
            table["analysis_window"][i] = win
            if rejected is not None and rejected[i]:
                table["curation_label"][i] = int(CurationLabel.REJECT)
        f["molecules"]["table"][:] = table
    proj = Project.open(path)
    keys = [
        k.decode() if isinstance(k, bytes) else str(k) for k in read_molecules(path)["molecule_key"]
    ]
    return proj, keys


# --- pure core: time_signal_histogram2d ---------------------------------------


def test_basic_shape_and_edges() -> None:
    h = time_signal_histogram2d([np.array([0.5, 0.5])], time_bins=10, signal_bins=20)
    assert isinstance(h, Histogram2D)
    assert h.counts.shape == (10, 20)
    assert h.time_edges.shape == (11,)
    assert h.signal_edges.shape == (21,)
    assert h.time_bins == 10
    assert h.signal_bins == 20
    # time edges are frame index × dt; signal edges span the range.
    np.testing.assert_allclose(h.time_edges, np.arange(11) * DEFAULT_TIME_DT)
    np.testing.assert_allclose(h.signal_edges, np.linspace(-0.2, 1.2, 21))


def test_single_frame_lands_in_expected_cell() -> None:
    h = time_signal_histogram2d([np.array([0.5])])
    assert h.counts.sum() == 1.0
    assert h.counts[0, _sbin(0.5)] == 1.0
    assert h.n_samples == 1
    assert h.n_molecules == 1


def test_time_axis_tracks_frame_index() -> None:
    # E = 0.2 at frame 0, E = 0.8 at frame 1 -> two different columns and rows.
    h = time_signal_histogram2d([np.array([0.2, 0.8])])
    assert h.counts[0, _sbin(0.2)] == 1.0
    assert h.counts[1, _sbin(0.8)] == 1.0
    assert h.counts.sum() == 2.0
    # column 0 holds only the low-E point, column 1 only the high-E point.
    assert h.counts[0, _sbin(0.8)] == 0.0
    assert h.counts[1, _sbin(0.2)] == 0.0


def test_nan_frame_dropped_without_shifting_time() -> None:
    # [E0, NaN, E2]: the third value must stay at time column 2, not slide to 1.
    h = time_signal_histogram2d([np.array([0.3, np.nan, 0.7])])
    assert h.counts[0, _sbin(0.3)] == 1.0
    assert h.counts[2, _sbin(0.7)] == 1.0
    assert h.counts[1].sum() == 0.0  # the NaN frame leaves an empty column
    assert h.n_samples == 2
    assert h.n_molecules == 1


def test_out_of_range_signal_dropped() -> None:
    # 2.0 is above hi, -1.0 below lo; both dropped, only 0.5 survives.
    h = time_signal_histogram2d([np.array([0.5, 2.0, -1.0])])
    assert h.counts.sum() == 1.0
    assert h.counts[0, _sbin(0.5)] == 1.0
    assert h.n_samples == 1
    assert h.n_molecules == 1  # still had finite frames


def test_right_open_signal_interval() -> None:
    # tMAVEN uses d >= ymin and d < ymax: the low edge is kept, the high edge dropped.
    lo, hi = -0.2, 1.2
    h = time_signal_histogram2d([np.array([lo, hi])], signal_range=(lo, hi), signal_bins=7)
    assert h.counts.sum() == 1.0  # only the low edge counted
    assert h.counts[0, 0] == 1.0
    assert h.counts[1].sum() == 0.0  # the value == hi frame dropped


def test_frame_beyond_time_bins_dropped() -> None:
    # 5-frame trace into a 3-column grid: frames 3, 4 drop, molecule still counts.
    h = time_signal_histogram2d([np.array([0.5, 0.5, 0.5, 0.5, 0.5])], time_bins=3)
    assert h.counts.shape == (3, DEFAULT_SIGNAL_BINS)
    assert h.counts.sum() == 3.0
    assert h.n_samples == 3
    assert h.n_molecules == 1


def test_frame_at_time_bins_index_dropped() -> None:
    # Right-open time axis: frame index == time_bins is out of the last column.
    h = time_signal_histogram2d([np.array([0.5, 0.5])], time_bins=1)
    assert h.counts.sum() == 1.0  # only frame 0
    assert h.n_samples == 1


def test_all_nan_molecule_not_counted() -> None:
    h = time_signal_histogram2d([np.array([np.nan, np.nan]), np.array([0.5])])
    assert h.n_molecules == 1  # the all-NaN molecule contributes nothing
    assert h.counts.sum() == 1.0
    assert h.n_samples == 1


def test_finite_but_all_out_of_range_molecule_counts_as_molecule() -> None:
    # A molecule with finite frames that all fall outside the window: N counts it
    # (tMAVEN's nmol counts >= 1 finite frame), but nothing is binned.
    h = time_signal_histogram2d([np.array([5.0, 6.0])])
    assert h.n_molecules == 1
    assert h.n_samples == 0
    assert h.counts.sum() == 0.0
    assert not np.any(np.isnan(h.counts))


def test_per_molecule_equal_weight_balances_traces() -> None:
    # A long molecule (10 frames at E=0.5) vs a short one (2 frames at E=0.5).
    long_e = np.full(10, 0.5)
    short_e = np.full(2, 0.5)
    raw = time_signal_histogram2d([long_e, short_e])
    weighted = time_signal_histogram2d([long_e, short_e], per_molecule_equal_weight=True)
    # Raw column 0 has both molecules (2 counts); column 5 only the long one (1).
    assert raw.counts[0, _sbin(0.5)] == 2.0
    assert raw.counts[5, _sbin(0.5)] == 1.0
    # Weighted: each molecule spreads total weight 1 over its finite frames, so
    # column 0 = 1/10 + 1/2 = 0.6, and every molecule sums to 1 across time.
    assert weighted.counts[0, _sbin(0.5)] == pytest.approx(1.0 / 10 + 1.0 / 2)
    assert weighted.counts.sum() == pytest.approx(2.0)  # two molecules, weight 1 each


def test_density_integrates_to_one() -> None:
    rng = np.random.default_rng(0)
    chunks = [rng.uniform(0.0, 1.0, size=50) for _ in range(20)]
    h = time_signal_histogram2d(chunks, density=True)
    dt_w = np.diff(h.time_edges)[0]
    ds_w = np.diff(h.signal_edges)[0]
    assert (h.counts * dt_w * ds_w).sum() == pytest.approx(1.0)


def test_raw_counts_conserved() -> None:
    rng = np.random.default_rng(1)
    chunks = [rng.uniform(0.0, 1.0, size=30) for _ in range(10)]
    h = time_signal_histogram2d(chunks)
    assert h.counts.sum() == pytest.approx(float(h.n_samples))


def test_empty_input_is_zero_heatmap() -> None:
    h = time_signal_histogram2d([])
    assert h.counts.shape == (DEFAULT_TIME_BINS, DEFAULT_SIGNAL_BINS)
    assert h.counts.sum() == 0.0
    assert h.n_molecules == 0
    assert h.n_samples == 0
    assert not np.any(np.isnan(h.counts))


def test_empty_input_density_is_zero_not_nan() -> None:
    # density on empty must not divide by zero into NaN.
    h = time_signal_histogram2d([], density=True)
    assert h.counts.sum() == 0.0
    assert not np.any(np.isnan(h.counts))


def test_time_dt_scales_edges_only() -> None:
    e = np.array([0.2, 0.8])
    base = time_signal_histogram2d([e], time_bins=5)
    scaled = time_signal_histogram2d([e], time_bins=5, time_dt=0.1)
    # Cell placement (frame index) is identical; only the time-edge coordinates scale.
    np.testing.assert_array_equal(base.counts, scaled.counts)
    np.testing.assert_allclose(scaled.time_edges, np.arange(6) * 0.1)
    assert scaled.time_dt == 0.1


def test_non_integer_time_dt_keeps_frames_in_their_own_columns() -> None:
    # Regression guard: the time-bin edges must be built with the same ``idx * dt``
    # arithmetic as the frame coordinates. A ``linspace(0, n*dt, n+1)`` edge array
    # disagrees by 1 ULP for non-integer dt (here 0.1 at time_bins=3), collapsing
    # frame 1 into column 0. Each in-range frame must occupy its own time column.
    h = time_signal_histogram2d([np.array([0.2, 0.5, 0.8])], time_bins=3, time_dt=0.1)
    np.testing.assert_array_equal(h.counts.sum(axis=1), np.array([1.0, 1.0, 1.0]))
    assert h.counts[0, _sbin(0.2)] == 1.0
    assert h.counts[1, _sbin(0.5)] == 1.0
    assert h.counts[2, _sbin(0.8)] == 1.0
    # The returned edges follow the documented arange(time_bins + 1) * dt contract.
    np.testing.assert_array_equal(h.time_edges, np.arange(4) * 0.1)


def test_per_molecule_equal_weight_costs_dropped_frames() -> None:
    # 10 finite frames, 5 in-range (E=0.5) then 5 out-of-range (E=9.0). Equal weight
    # is 1/m with m = the FINITE count (10), so the 5 survivors total 0.5 — an
    # out-of-window frame costs the molecule weight (docstring contract). A denominator
    # of keep.sum() (5) would wrongly total 1.0, so this pins the finite-count divisor.
    e = np.array([0.5] * 5 + [9.0] * 5)
    h = time_signal_histogram2d([e], per_molecule_equal_weight=True)
    assert h.n_molecules == 1
    assert h.n_samples == 5
    assert h.counts.sum() == pytest.approx(0.5)
    for t in range(5):
        assert h.counts[t, _sbin(0.5)] == pytest.approx(0.1)


def test_dataclass_properties() -> None:
    h = time_signal_histogram2d([np.array([0.5])], time_bins=4, signal_bins=8)
    assert h.time_centers.shape == (4,)
    assert h.signal_centers.shape == (8,)
    np.testing.assert_allclose(h.time_centers, 0.5 * (h.time_edges[:-1] + h.time_edges[1:]))
    np.testing.assert_allclose(h.signal_centers, 0.5 * (h.signal_edges[:-1] + h.signal_edges[1:]))
    assert h.signal_range == (-0.2, 1.2)
    assert h.density is False
    assert h.per_molecule_equal_weight is False


def test_generator_input_consumed_once() -> None:
    gen = (np.array([0.5]) for _ in range(3))
    h = time_signal_histogram2d(gen)
    assert h.n_molecules == 3
    assert h.counts.sum() == 3.0


def test_2d_array_rows_are_molecules() -> None:
    arr = np.array([[0.2, 0.8], [0.2, 0.8]])
    h = time_signal_histogram2d(arr)
    assert h.n_molecules == 2
    assert h.counts[0, _sbin(0.2)] == 2.0
    assert h.counts[1, _sbin(0.8)] == 2.0


@pytest.mark.parametrize("bad", [0, -1])
def test_invalid_time_bins_raises(bad: int) -> None:
    with pytest.raises(ValueError, match="time_bins must be >= 1"):
        time_signal_histogram2d([np.array([0.5])], time_bins=bad)


@pytest.mark.parametrize("bad", [0, -3])
def test_invalid_signal_bins_raises(bad: int) -> None:
    with pytest.raises(ValueError, match="signal_bins must be >= 1"):
        time_signal_histogram2d([np.array([0.5])], signal_bins=bad)


@pytest.mark.parametrize("rng", [(1.0, 1.0), (1.2, -0.2)])
def test_invalid_signal_range_raises(rng: tuple[float, float]) -> None:
    with pytest.raises(ValueError, match="signal_range must be"):
        time_signal_histogram2d([np.array([0.5])], signal_range=rng)


@pytest.mark.parametrize("bad", [0.0, -1.0, np.inf, np.nan])
def test_invalid_time_dt_raises(bad: float) -> None:
    with pytest.raises(ValueError, match="time_dt must be finite and > 0"):
        time_signal_histogram2d([np.array([0.5])], time_dt=bad)


# --- store-level: population_time_signal_histogram2d --------------------------


def test_population_captures_time_evolution(tmp_path: Path) -> None:
    # 4 molecules, each low E (0.25) for the first 5 frames then high E (0.75).
    e = np.tile(np.array([0.25] * 5 + [0.75] * 5), (4, 1))
    proj, _ = _build_store(tmp_path, e)
    h = population_time_signal_histogram2d(proj, time_bins=10)
    assert h.n_molecules == 4
    assert h.n_samples == 40
    centers = h.signal_centers
    # Early columns peak near E=0.25, late columns near E=0.75.
    early_peak = centers[np.argmax(h.counts[0])]
    late_peak = centers[np.argmax(h.counts[9])]
    assert early_peak == pytest.approx(0.25, abs=0.05)
    assert late_peak == pytest.approx(0.75, abs=0.05)
    assert late_peak > early_peak


def test_population_start_synchronization_via_analysis_window(tmp_path: Path) -> None:
    # Molecule A: window (0,10), pattern [0.2]*5 + [0.8]*5.
    # Molecule B: window (5,15), first 5 frames junk then [0.2]*5 + [0.8]*5.
    # After windowing both contribute the SAME start-aligned pattern.
    a = np.array([0.2] * 5 + [0.8] * 5 + [0.5] * 5)  # only [0:10] used
    b = np.array([0.99] * 5 + [0.2] * 5 + [0.8] * 5)  # only [5:15] used
    e = np.vstack([a, b])
    proj, _ = _build_store(tmp_path, e, windows=[(0, 10), (5, 15)])
    h = population_time_signal_histogram2d(proj, time_bins=10)
    assert h.n_molecules == 2
    # Both molecules stack in columns 0-4 at E=0.2 and 5-9 at E=0.8.
    assert h.counts[0, _sbin(0.2)] == 2.0
    assert h.counts[9, _sbin(0.8)] == 2.0
    assert h.n_samples == 20


def test_population_rejected_excluded_by_default(tmp_path: Path) -> None:
    e = np.tile(np.full(8, 0.5), (3, 1))
    proj, _ = _build_store(tmp_path, e, rejected=[False, True, False])
    default = population_time_signal_histogram2d(proj, time_bins=8)
    assert default.n_molecules == 2  # the rejected molecule is filtered out
    with_rej = population_time_signal_histogram2d(proj, time_bins=8, include_rejected=True)
    assert with_rej.n_molecules == 3


def test_population_molecule_keys_filter(tmp_path: Path) -> None:
    e = np.tile(np.full(6, 0.5), (4, 1))
    proj, keys = _build_store(tmp_path, e)
    h = population_time_signal_histogram2d(proj, molecule_keys=keys[:2], time_bins=6)
    assert h.n_molecules == 2
    assert h.counts[0, _sbin(0.5)] == 2.0


def test_population_empty_project_is_zero_heatmap(tmp_path: Path) -> None:
    e = np.full((1, 6), 0.5)
    proj, keys = _build_store(tmp_path, e)
    # Restrict to a non-existent key -> no molecules selected.
    h = population_time_signal_histogram2d(proj, molecule_keys=["nope"], time_bins=6)
    assert h.n_molecules == 0
    assert h.counts.sum() == 0.0
    assert not np.any(np.isnan(h.counts))


def test_population_bad_intensity_quantity_raises(tmp_path: Path) -> None:
    e = np.full((1, 6), 0.5)
    proj, _ = _build_store(tmp_path, e)
    with pytest.raises(ValueError, match="intensity_quantity must be one of"):
        population_time_signal_histogram2d(proj, intensity_quantity="bogus")


def test_population_defaults_match_tmaven_a2(tmp_path: Path) -> None:
    e = np.full((2, 20), 0.5)
    proj, _ = _build_store(tmp_path, e)
    h = population_time_signal_histogram2d(proj)
    assert h.counts.shape == (DEFAULT_TIME_BINS, DEFAULT_SIGNAL_BINS)
    assert h.signal_range == DEFAULT_SIGNAL_RANGE
    assert h.time_dt == DEFAULT_TIME_DT
