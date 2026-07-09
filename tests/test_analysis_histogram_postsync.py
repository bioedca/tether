# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""A2 post-synchronized (transition-aligned) heatmap (M6, FR-ANALYZE; Appendix C A2).

Covers tMAVEN's A2 post-synchronized mode (``data_hist2d.py`` ``gen_sync_list_*`` +
``histogram_sync_list``): the pure-array core
:func:`~tether.analysis.histogram.transition_sync_histogram2d` (align each
molecule's Viterbi state transitions to a common column and bin the observed signal
around them) and the store-level
:func:`~tether.analysis.histogram.population_transition_sync_histogram2d` (pair a
persisted ``/idealization`` model's state paths with each molecule's windowed
apparent E). A verbatim port of the tMAVEN reference functions is the parity oracle.
All headless (no Qt) → runs in the base CI matrix; the store is seeded as
post-extraction data under the M0-frozen schema.
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
    TransitionSyncHistogram2D,
    population_transition_sync_histogram2d,
    transition_sync_histogram2d,
)
from tether.analysis._store import windowed_state_and_channels  # noqa: E402
from tether.idealize import NO_STATE  # noqa: E402
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
from tether.project.idealize import write_idealization_model  # noqa: E402
from tether.project.labels import CurationLabel  # noqa: E402

_PARSED = parse_filename("Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif")
_WINDOW = 21


# --- tMAVEN reference oracle (data_hist2d.py, ported verbatim) ----------------


def _tmaven_postsync(
    viterbis: np.ndarray,
    data: np.ndarray,
    *,
    vi: int,
    vj: int,
    single_dwell: bool,
    xbins: int,
    xsync: int,
    ymin: float,
    ymax: float,
    ybins: int,
) -> tuple[np.ndarray, int, int]:
    """tMAVEN's ``gen_sync_list_* + histogram_sync_list`` verbatim (the oracle).

    ``viterbis`` is a rectangular ``(nmol, nframes)`` state-index array (NaN where no
    state); ``data`` the matching observed signal. Returns ``(out, nmol, npoints)``
    with ``out`` shaped ``(xbins + 1, ybins)`` — exactly tMAVEN's post-sync path.
    Callers must keep fixed windows inside ``[0, nframes)`` (tMAVEN would otherwise
    index the padded row with a wrapped/out-of-bounds frame — the one edge Tether's
    ragged core deliberately drops instead).
    """
    jl: list[tuple[int, int, int, int]] = []
    for i in range(viterbis.shape[0]):
        for t in range(viterbis.shape[1] - 1):
            a, b = viterbis[i, t], viterbis[i, t + 1]
            if (not np.isnan(a)) and (not np.isnan(b)) and a != b:
                jl.append((i, t, int(a), int(b)))
    jumplist = np.array(jl, dtype=int).reshape(-1, 4)

    if vi < 0 and vj < 0:
        jumpind = np.arange(jumplist.shape[0])
    elif vi < 0 and vj >= 0:
        jumpind = np.nonzero(jumplist[:, 3] == vj)[0]
    elif vi >= 0 and vj < 0:
        jumpind = np.nonzero(jumplist[:, 2] == vi)[0]
    else:
        jumpind = np.nonzero((jumplist[:, 2] == vi) & (jumplist[:, 3] == vj))[0]

    synclist = np.zeros((jumpind.size, 4), dtype=int)
    if single_dwell:
        synclist[:, 3] = viterbis.shape[1] - 1
        for i in range(jumpind.size):
            synclist[i, 0] = jumplist[jumpind[i], 0]
            if jumpind[i] - 1 >= 0 and jumplist[jumpind[i] - 1, 0] == jumplist[jumpind[i], 0]:
                synclist[i, 1] = jumplist[jumpind[i] - 1, 1] + 1
            synclist[i, 2] = jumplist[jumpind[i], 1]
            if (
                jumpind[i] + 1 < jumplist.shape[0]
                and jumplist[jumpind[i] + 1, 0] == jumplist[jumpind[i], 0]
            ):
                synclist[i, 3] = jumplist[jumpind[i] + 1, 1]
    else:
        npre = xsync
        npost = xbins - xsync + 1
        for i in range(jumpind.size):
            synclist[i, 0] = jumplist[jumpind[i], 0]
            synclist[i, 1] = jumplist[jumpind[i], 1] - npre
            synclist[i, 2] = jumplist[jumpind[i], 1]
            synclist[i, 3] = jumplist[jumpind[i], 1] + npost

    out = np.zeros((xbins + 1, ybins))
    for si in range(synclist.shape[0]):
        sync = synclist[si]
        for t in range(sync[1], sync[3]):
            x = t - sync[2] + xsync
            if x > xbins:
                break
            elif x >= 0:
                d = data[sync[0], t]
                if (not np.isnan(d)) and (d >= ymin) and (d < ymax):
                    yind = int((d - ymin) / (ymax - ymin) * ybins)
                    out[x, yind] += 1
    nmol = int(np.unique(synclist[:, 0]).size) if synclist.shape[0] else 0
    npoints = int(synclist.shape[0])
    return out, nmol, npoints


def _pairs_from_rect(viterbis: np.ndarray, data: np.ndarray, no_state: int = NO_STATE):
    """Convert a rectangular NaN-padded oracle input into ragged (state, signal) pairs.

    NaN state -> ``no_state``; the per-molecule arrays keep full width so the ragged
    core and the rectangular oracle see identical frames (equal-length molecules).
    """
    pairs = []
    for i in range(viterbis.shape[0]):
        s = np.where(np.isnan(viterbis[i]), no_state, viterbis[i]).astype(np.int64)
        pairs.append((s, np.asarray(data[i], dtype="float64")))
    return pairs


def _sbin(value: float, rng=DEFAULT_SIGNAL_RANGE, nbins: int = DEFAULT_SIGNAL_BINS) -> int:
    lo, hi = rng
    return int((value - lo) / (hi - lo) * nbins)


# --- pure core: shape + edges -------------------------------------------------


def test_shape_and_edges_have_extra_column() -> None:
    h = transition_sync_histogram2d([], time_bins=10, signal_bins=20, sync_preframe=3)
    assert isinstance(h, TransitionSyncHistogram2D)
    # tMAVEN's xbins + 1 columns; edges are the column midpoints -> xbins + 2.
    assert h.counts.shape == (11, 20)
    assert h.n_columns == 11
    assert h.signal_bins == 20
    assert h.time_edges.shape == (12,)
    assert h.signal_edges.shape == (21,)
    # The sync column sits at sync_preframe with relative-time zero at its centre.
    assert h.transition_column == 3
    np.testing.assert_allclose(h.time_centers[3], 0.0)
    np.testing.assert_allclose(h.signal_edges, np.linspace(-0.2, 1.2, 21))


def test_time_centers_are_relative_and_negative_before_sync() -> None:
    h = transition_sync_histogram2d([], time_bins=6, sync_preframe=2, time_dt=0.5)
    # column k -> (k - preframe) * dt
    expected = (np.arange(7) - 2) * 0.5
    np.testing.assert_allclose(h.time_centers, expected)
    assert h.time_centers[0] < 0.0  # before the transition


def test_empty_input_is_all_zero_never_nan() -> None:
    h = transition_sync_histogram2d([], time_bins=5, signal_bins=8, sync_preframe=2)
    assert h.counts.shape == (6, 8)
    assert h.counts.sum() == 0.0
    assert not np.any(np.isnan(h.counts))
    assert h.n_molecules == 0
    assert h.n_transitions == 0
    assert h.n_samples == 0


# --- pure core: a single hand-checked transition ------------------------------


def test_single_transition_lands_at_sync_column() -> None:
    # states 0->1 between frame 2 and 3; signal E=0.2 in state 0, E=0.8 in state 1.
    state = np.array([0, 0, 0, 1, 1, 1])
    signal = np.array([0.2, 0.2, 0.2, 0.8, 0.8, 0.8])
    pre = 3
    h = transition_sync_histogram2d(
        [(state, signal)],
        from_state=0,
        to_state=1,
        single_dwell=True,
        sync_preframe=pre,
        time_bins=8,
    )
    assert h.n_molecules == 1
    assert h.n_transitions == 1
    # The transition is between frames 2 and 3; x = (f - 2) + pre. Frame 3 (first of
    # the new state) maps to column pre + 1; frame 2 (last of old state) to column pre.
    assert h.counts[pre, _sbin(0.2)] == 1.0  # last frame of state 0 sits at the sync column
    assert h.counts[pre + 1, _sbin(0.8)] == 1.0  # first frame of state 1 just after
    # The low-E value never appears after the sync column, nor high-E before it.
    assert h.counts[pre + 1, _sbin(0.2)] == 0.0
    assert h.counts[pre, _sbin(0.8)] == 0.0
    # single-dwell over one transition: whole trace [0, L-1) binned -> 5 frames.
    assert h.n_samples == 5


def test_frame_mapping_matches_relative_offset() -> None:
    # Distinct E per frame so every column is identifiable. Fixed window so the
    # trailing frame is not clipped by the single-dwell next-dwell boundary.
    state = np.array([0, 0, 1, 1])
    signal = np.array([0.10, 0.30, 0.60, 0.90])
    pre = 5
    h = transition_sync_histogram2d(
        [(state, signal)],
        from_state=-1,
        to_state=-1,
        single_dwell=False,
        sync_preframe=pre,
        time_bins=12,
    )
    # transition between frame 1 and 2 -> sync_t = 1; x = (f - 1) + 5.
    assert h.counts[4, _sbin(0.10)] == 1.0  # f=0 -> x=4
    assert h.counts[5, _sbin(0.30)] == 1.0  # f=1 -> x=5 (== sync column)
    assert h.counts[6, _sbin(0.60)] == 1.0  # f=2 -> x=6
    assert h.counts[7, _sbin(0.90)] == 1.0  # f=3 -> x=7


def test_single_dwell_excludes_trailing_boundary_frame() -> None:
    # tMAVEN single-dwell ends the window at the next transition (or trace end
    # exclusive): the very last frame of the trace is the next-dwell boundary and is
    # not binned. Here the window for the only transition is [0, L-1) = frames 0..2.
    state = np.array([0, 0, 1, 1])
    signal = np.array([0.10, 0.30, 0.60, 0.90])
    h = transition_sync_histogram2d(
        [(state, signal)], single_dwell=True, sync_preframe=5, time_bins=12
    )
    assert h.n_samples == 3  # frame 3 (the trailing boundary) is excluded
    assert h.counts[7, _sbin(0.90)] == 0.0  # the excluded frame never bins


# --- pure core: parity with the tMAVEN oracle ---------------------------------


def _oracle_case() -> tuple[np.ndarray, np.ndarray]:
    """Two equal-length molecules with several transitions, all interior."""
    # molecule 0: 0->1 at t=3, 1->2 at t=8, 2->0 at t=13
    # molecule 1: 0->2 at t=4, 2->1 at t=10
    v = np.full((2, 20), np.nan)
    d = np.full((2, 20), np.nan)
    v[0] = np.array([0, 0, 0, 0, 1, 1, 1, 1, 1, 2, 2, 2, 2, 2, 0, 0, 0, 0, 0, 0])
    v[1] = np.array([0, 0, 0, 0, 0, 2, 2, 2, 2, 2, 2, 1, 1, 1, 1, 1, 1, 1, 1, 1])
    # observed signal: state-dependent mean plus a tiny per-frame ripple, all in-range
    means = {0: 0.15, 1: 0.55, 2: 0.85}
    for i in range(2):
        for t in range(20):
            st = int(v[i, t])
            d[i, t] = means[st] + 0.001 * ((t % 3) - 1)
    return v, d


@pytest.mark.parametrize("single_dwell", [True, False])
@pytest.mark.parametrize(("vi", "vj"), [(-1, -1), (0, 1), (-1, 2), (1, -1), (2, 0)])
def test_parity_with_tmaven_oracle(single_dwell: bool, vi: int, vj: int) -> None:
    v, d = _oracle_case()
    xbins, xsync, ybins = 8, 3, DEFAULT_SIGNAL_BINS
    lo, hi = DEFAULT_SIGNAL_RANGE
    ref, nmol, npoints = _tmaven_postsync(
        v,
        d,
        vi=vi,
        vj=vj,
        single_dwell=single_dwell,
        xbins=xbins,
        xsync=xsync,
        ymin=lo,
        ymax=hi,
        ybins=ybins,
    )
    h = transition_sync_histogram2d(
        _pairs_from_rect(v, d),
        from_state=vi,
        to_state=vj,
        single_dwell=single_dwell,
        sync_preframe=xsync,
        time_bins=xbins,
        signal_bins=ybins,
        signal_range=(lo, hi),
    )
    np.testing.assert_array_equal(h.counts, ref)
    assert h.n_molecules == nmol
    assert h.n_transitions == npoints


# --- pure core: transition selection ------------------------------------------


def test_from_to_state_selection_counts() -> None:
    v, d = _oracle_case()
    pairs = _pairs_from_rect(v, d)
    # any -> any: 5 transitions across 2 molecules
    h_all = transition_sync_histogram2d(
        pairs, from_state=-1, to_state=-1, time_bins=8, sync_preframe=3
    )
    assert h_all.n_transitions == 5
    assert h_all.n_molecules == 2
    # 0 -> 1 occurs only in molecule 0 (one transition)
    h01 = transition_sync_histogram2d(
        _pairs_from_rect(v, d), from_state=0, to_state=1, time_bins=8, sync_preframe=3
    )
    assert h01.n_transitions == 1
    assert h01.n_molecules == 1
    # entering state 2 (vj=2, vi any): molecule 0 (1->2) + molecule 1 (0->2) = 2
    h_to2 = transition_sync_histogram2d(
        _pairs_from_rect(v, d), from_state=-1, to_state=2, time_bins=8, sync_preframe=3
    )
    assert h_to2.n_transitions == 2
    assert h_to2.n_molecules == 2


def test_no_selected_transition_is_all_zero() -> None:
    v, d = _oracle_case()
    # 1 -> 0 never occurs in the fixture
    h = transition_sync_histogram2d(
        _pairs_from_rect(v, d), from_state=1, to_state=0, time_bins=8, sync_preframe=3
    )
    assert h.counts.sum() == 0.0
    assert h.n_molecules == 0
    assert h.n_transitions == 0
    assert not np.any(np.isnan(h.counts))


# --- pure core: NO_STATE handling ---------------------------------------------


def test_no_state_boundary_is_not_a_transition() -> None:
    # NO_STATE padding on both ends; the -1 -> 0 and 1 -> -1 borders must NOT be jumps.
    state = np.array([NO_STATE, NO_STATE, 0, 0, 1, 1, NO_STATE, NO_STATE])
    signal = np.array([0.5, 0.5, 0.2, 0.2, 0.8, 0.8, 0.5, 0.5])
    h = transition_sync_histogram2d(
        [(state, signal)],
        from_state=-1,
        to_state=-1,
        single_dwell=True,
        sync_preframe=3,
        time_bins=8,
    )
    # exactly one real transition (0->1 between frames 3 and 4)
    assert h.n_transitions == 1
    assert h.n_molecules == 1


def test_interior_no_state_breaks_a_run() -> None:
    # 0 [gap] 0: the gap means no 0->0 "transition" and the borders are not jumps.
    state = np.array([0, 0, NO_STATE, 0, 0])
    signal = np.array([0.3, 0.3, 0.5, 0.3, 0.3])
    h = transition_sync_histogram2d([(state, signal)], time_bins=6, sync_preframe=2)
    assert h.n_transitions == 0
    assert h.counts.sum() == 0.0


# --- pure core: signal masking + column bounds --------------------------------


def test_right_open_signal_interval() -> None:
    lo, hi = -0.2, 1.2
    # state 0->1; the two flanking values are lo (kept) and hi (dropped, right-open).
    # single_dwell=False so BOTH frames fall inside the binned window and the hi frame
    # is dropped by the `d < ymax` right-open check itself (not the trailing-boundary
    # exclusion that single_dwell=True would apply first).
    state = np.array([0, 1])
    signal = np.array([lo, hi])
    h = transition_sync_histogram2d(
        [(state, signal)],
        single_dwell=False,
        sync_preframe=1,
        time_bins=4,
        signal_bins=7,
        signal_range=(lo, hi),
    )
    assert h.counts.sum() == 1.0  # only the low-edge frame counted (hi dropped, right-open)
    assert h.counts[1, 0] == 1.0  # frame 0 -> sync column 1, low bin


def test_out_of_range_signal_dropped_but_transition_still_counts() -> None:
    # both flanking values out of the signal range -> nothing binned, but the
    # transition still counts toward N / npoints (faithful to tMAVEN).
    state = np.array([0, 0, 1, 1])
    signal = np.array([5.0, 5.0, -5.0, -5.0])
    h = transition_sync_histogram2d(
        [(state, signal)], from_state=0, to_state=1, sync_preframe=2, time_bins=6
    )
    assert h.counts.sum() == 0.0
    assert h.n_samples == 0
    assert h.n_molecules == 1
    assert h.n_transitions == 1


def test_non_finite_signal_frame_dropped() -> None:
    state = np.array([0, 0, 1, 1])
    signal = np.array([0.3, np.nan, 0.7, 0.7])
    h = transition_sync_histogram2d(
        [(state, signal)],
        from_state=0,
        to_state=1,
        single_dwell=False,
        sync_preframe=2,
        time_bins=6,
    )
    # the NaN frame (frame 1) is dropped; the other three finite frames bin.
    assert h.n_samples == 3
    assert not np.any(np.isnan(h.counts))


def test_columns_beyond_time_bins_dropped() -> None:
    # A long dwell after the transition: only frames mapping to x <= time_bins bin.
    state = np.array([0, 1, 1, 1, 1, 1, 1, 1])
    signal = np.full(8, 0.5)
    pre = 1
    tb = 3
    h = transition_sync_histogram2d(
        [(state, signal)],
        from_state=0,
        to_state=1,
        single_dwell=False,
        sync_preframe=pre,
        time_bins=tb,
    )
    # sync_t=0; the fixed window [sync_t - pre, sync_t + (tb - pre + 1)) = [-1, 3)
    # maps frames 0, 1, 2 to columns 1, 2, 3 (the negative pre-transition frame is
    # dropped, and nothing spills past the last column tb=3). Exactly 3 frames bin.
    assert h.counts.shape == (tb + 1, DEFAULT_SIGNAL_BINS)
    assert h.counts.sum() == 3.0
    assert h.counts[0].sum() == 0.0  # column 0 (the dropped pre-transition frame) stays empty
    assert h.counts[tb, _sbin(0.5)] == 1.0  # the last kept frame lands in the final column


def test_fixed_window_near_trace_start_drops_negative_frames() -> None:
    # transition at frame 1 with sync_preframe 4 -> fixed window would start at -3;
    # those frames are outside [0, L) and are dropped (not wrapped).
    state = np.array([0, 1, 1, 1, 1, 1])
    signal = np.array([0.1, 0.9, 0.9, 0.9, 0.9, 0.9])
    h = transition_sync_histogram2d(
        [(state, signal)],
        from_state=0,
        to_state=1,
        single_dwell=False,
        sync_preframe=4,
        time_bins=8,
    )
    total = h.counts.sum()
    # sync_t = 0; fixed window is [sync_t - pre, sync_t + (time_bins - pre + 1)) =
    # [-4, 5), so frames 0..4 bin (5 frames). The negative window frames are dropped,
    # never wrapped to the trace tail; frame 5 falls past the exclusive window end.
    assert total == 5.0
    assert not np.any(np.isnan(h.counts))


# --- pure core: density + validation ------------------------------------------


def test_density_integrates_to_one() -> None:
    v, d = _oracle_case()
    h = transition_sync_histogram2d(
        _pairs_from_rect(v, d),
        from_state=-1,
        to_state=-1,
        density=True,
        sync_preframe=3,
        time_bins=8,
    )
    dt = h.time_dt
    signal_width = np.diff(h.signal_edges)[0]
    integral = float(h.counts.sum() * dt * signal_width)
    np.testing.assert_allclose(integral, 1.0, rtol=1e-9)


def test_length_below_two_skipped() -> None:
    h = transition_sync_histogram2d(
        [(np.array([0]), np.array([0.5])), (np.array([], dtype=int), np.array([]))]
    )
    assert h.n_molecules == 0
    assert h.counts.sum() == 0.0


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"time_bins": 0}, "time_bins"),
        ({"signal_bins": 0}, "signal_bins"),
        ({"sync_preframe": 11, "time_bins": 10}, "sync_preframe"),
        ({"sync_preframe": -1}, "sync_preframe"),
        ({"signal_range": (1.0, 1.0)}, "signal_range"),
        ({"time_dt": 0.0}, "time_dt"),
        ({"time_dt": float("nan")}, "time_dt"),
    ],
)
def test_validation_errors(kwargs, match) -> None:
    with pytest.raises(ValueError, match=match):
        transition_sync_histogram2d([], **kwargs)


def test_mismatched_pair_lengths_raise() -> None:
    with pytest.raises(ValueError, match="same length"):
        transition_sync_histogram2d([(np.array([0, 1, 1]), np.array([0.5, 0.5]))])


def test_sync_preframe_at_bounds_allowed() -> None:
    # both extremes are valid (transition at the far left / far right column).
    for pre in (0, 5):
        h = transition_sync_histogram2d([], sync_preframe=pre, time_bins=5)
        assert h.transition_column == pre


# --- store-level: seed a .tether with molecules + traces + idealization --------


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


def _e_traces(e_matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    e = np.asarray(e_matrix, dtype="float64")
    donor = (1.0 - e) * 1000.0
    acceptor = e * 1000.0
    nan = np.isnan(e)
    donor[nan] = 0.0
    acceptor[nan] = 0.0
    return donor, acceptor


def _build_store_with_model(
    tmp_path: Path,
    e_matrix: np.ndarray,
    state_matrix: np.ndarray,
    *,
    windows: list[tuple[int, int]] | None = None,
    rejected: list[bool] | None = None,
    model_name: str = "vbconhmm",
    name: str = "exp.tether",
) -> tuple[Project, list[str], list[str]]:
    """A ``.tether`` with molecule ``i`` carrying apparent E ``e_matrix[i]`` and a
    persisted ``/idealization/{model_name}`` whose ``state_matrix[i]`` is its Viterbi
    path (NO_STATE outside the window). Returns ``(project, molecule_keys, molecule_ids)``.
    """
    e_matrix = np.asarray(e_matrix, dtype="float64")
    state_matrix = np.asarray(state_matrix, dtype="int64")
    n, n_frames = e_matrix.shape
    donor, acceptor = _e_traces(e_matrix)
    coords = np.array([[12.0 + 1.7 * i, 14.0 + 2.3 * (i % 7)] for i in range(n)], dtype="float64")
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

    molecules = read_molecules(path)
    keys = [k.decode() if isinstance(k, bytes) else str(k) for k in molecules["molecule_key"]]
    ids = [x.decode() if isinstance(x, bytes) else str(x) for x in molecules["molecule_id"]]

    # An idealized level array (NaN outside the window); state levels are the means.
    # Width follows the state matrix, not the trace: a fixture may deliberately write
    # a state path *wider* than the (re-extracted) traces to exercise the reader's
    # trace-length guard.
    state_w = int(state_matrix.shape[1])
    idealized = np.full((n, state_w), np.nan)
    for i in range(n):
        valid = state_matrix[i] != NO_STATE
        idealized[i, valid] = state_matrix[i, valid].astype("float64")
    nstates = (
        int(state_matrix[state_matrix != NO_STATE].max()) + 1
        if np.any(state_matrix != NO_STATE)
        else 1
    )
    write_idealization_model(
        path,
        model_name=model_name,
        model_type=model_name,
        nstates=nstates,
        dtype="FRET",
        means=np.arange(nstates, dtype="float64"),
        variances=np.full(nstates, 0.01),
        tmatrix=None,
        norm_tmatrix=None,
        elbo=1.0,
        idealized=idealized,
        state_paths=state_matrix,
        molecule_keys=keys,
        molecule_ids=ids,
        input_hashes=[f"h{i}" for i in range(n)],
        intensity_quantity="corrected",
        selected_by="fixed",
        elbo_by_nstates=None,
        app_version="test",
        created_utc="2026-01-01T00:00:00Z",
        overwrite=True,
        frac=np.full(nstates, 1.0 / nstates),
    )
    return Project.open(path), keys, ids


def _two_molecule_store(tmp_path: Path, **kw):
    """Two molecules, 10 frames, one 0->1 transition each at different frames."""
    e = np.array(
        [
            [0.2, 0.2, 0.2, 0.2, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8],
            [0.2, 0.2, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8],
        ]
    )
    s = np.array(
        [
            [0, 0, 0, 0, 1, 1, 1, 1, 1, 1],
            [0, 0, 1, 1, 1, 1, 1, 1, 1, 1],
        ],
        dtype="int64",
    )
    return _build_store_with_model(tmp_path, e, s, **kw)


def test_population_matches_manual_core(tmp_path) -> None:
    proj, keys, _ = _two_molecule_store(tmp_path)
    h = population_transition_sync_histogram2d(
        proj,
        "vbconhmm",
        from_state=0,
        to_state=1,
        sync_preframe=3,
        time_bins=8,
    )
    assert isinstance(h, TransitionSyncHistogram2D)
    assert h.n_molecules == 2
    assert h.n_transitions == 2
    # Identical to feeding the pure core the read-back (state, apparent_e) pairs.
    triples = windowed_state_and_channels(proj, "vbconhmm", None, "corrected", False)
    from tether.fret.efficiency import apparent_fret

    ref = transition_sync_histogram2d(
        [(st, apparent_fret(d, a)) for st, d, a in triples],
        from_state=0,
        to_state=1,
        sync_preframe=3,
        time_bins=8,
        no_state=NO_STATE,
    )
    np.testing.assert_array_equal(h.counts, ref.counts)


def test_population_accepts_path_and_project(tmp_path) -> None:
    proj, _, _ = _two_molecule_store(tmp_path)
    from_path = population_transition_sync_histogram2d(
        proj.path, "vbconhmm", sync_preframe=3, time_bins=8
    )
    from_proj = population_transition_sync_histogram2d(
        proj, "vbconhmm", sync_preframe=3, time_bins=8
    )
    np.testing.assert_array_equal(from_path.counts, from_proj.counts)


def test_population_curation_filter_excludes_rejected(tmp_path) -> None:
    proj, keys, _ = _two_molecule_store(tmp_path, rejected=[False, True])
    kept = population_transition_sync_histogram2d(
        proj, "vbconhmm", from_state=0, to_state=1, sync_preframe=3, time_bins=8
    )
    assert kept.n_molecules == 1  # the rejected molecule is dropped
    withrej = population_transition_sync_histogram2d(
        proj,
        "vbconhmm",
        from_state=0,
        to_state=1,
        sync_preframe=3,
        time_bins=8,
        include_rejected=True,
    )
    assert withrej.n_molecules == 2


def test_population_molecule_keys_filter(tmp_path) -> None:
    proj, keys, _ = _two_molecule_store(tmp_path)
    only_first = population_transition_sync_histogram2d(
        proj,
        "vbconhmm",
        from_state=0,
        to_state=1,
        molecule_keys=[keys[0]],
        sync_preframe=3,
        time_bins=8,
    )
    assert only_first.n_molecules == 1


def test_population_windowed_boundary_is_not_a_transition(tmp_path) -> None:
    # A sub-window idealization: state paths carry NO_STATE outside [2, 8); the store
    # reader must slice there and the -1 border must not read as a state jump.
    e = np.array([[0.2, 0.2, 0.2, 0.2, 0.8, 0.8, 0.8, 0.8, 0.5, 0.5]])
    s = np.array([[NO_STATE, NO_STATE, 0, 0, 1, 1, 1, 1, NO_STATE, NO_STATE]], dtype="int64")
    proj, _, _ = _build_store_with_model(tmp_path, e, s, windows=[(2, 8)])
    h = population_transition_sync_histogram2d(proj, "vbconhmm", sync_preframe=2, time_bins=6)
    assert h.n_transitions == 1  # only the real 0->1 jump inside the window
    assert h.n_molecules == 1


def test_population_empty_when_no_kept_molecule(tmp_path) -> None:
    proj, keys, _ = _two_molecule_store(tmp_path, rejected=[True, True])
    h = population_transition_sync_histogram2d(proj, "vbconhmm", sync_preframe=3, time_bins=8)
    assert h.counts.sum() == 0.0
    assert h.n_molecules == 0
    assert not np.any(np.isnan(h.counts))


def test_population_unknown_model_raises(tmp_path) -> None:
    proj, _, _ = _two_molecule_store(tmp_path)
    with pytest.raises(KeyError):
        population_transition_sync_histogram2d(proj, "nope", sync_preframe=3, time_bins=8)


# --- reader: windowed_state_and_channels --------------------------------------


def test_reader_slices_to_idealized_window(tmp_path) -> None:
    e = np.array([[0.2, 0.2, 0.2, 0.2, 0.8, 0.8, 0.8, 0.8, 0.5, 0.5]])
    s = np.array([[NO_STATE, NO_STATE, 0, 0, 1, 1, 1, 1, NO_STATE, NO_STATE]], dtype="int64")
    proj, _, _ = _build_store_with_model(tmp_path, e, s, windows=[(2, 8)])
    triples = windowed_state_and_channels(proj, "vbconhmm", None, "corrected", False)
    assert len(triples) == 1
    state, donor, acceptor = triples[0]
    # Sliced to the contiguous idealized extent [2, 8) -> 6 frames, re-based to 0.
    assert state.shape == (6,)
    assert donor.shape == (6,)
    np.testing.assert_array_equal(state, np.array([0, 0, 1, 1, 1, 1]))
    assert NO_STATE not in state


def test_reader_excludes_rejected(tmp_path) -> None:
    proj, keys, _ = _two_molecule_store(tmp_path, rejected=[False, True])
    triples = windowed_state_and_channels(proj, "vbconhmm", None, "corrected", False)
    assert len(triples) == 1
    with_rej = windowed_state_and_channels(proj, "vbconhmm", None, "corrected", True)
    assert len(with_rej) == 2


def test_reader_bad_quantity_raises(tmp_path) -> None:
    proj, _, _ = _two_molecule_store(tmp_path)
    with pytest.raises(ValueError, match="intensity_quantity"):
        windowed_state_and_channels(proj, "vbconhmm", None, "does-not-exist", False)


def test_reader_skips_when_state_path_outruns_trace(tmp_path) -> None:
    # A re-extraction shortened the traces after the fit: the persisted state path is
    # WIDER than the current trace. Molecule 0's idealized extent still fits the
    # 8-frame trace; molecule 1's runs to frame 10 (past the trace) -> it must be
    # skipped (honest, never a truncated/misaligned pairing), not raise or misalign.
    e = np.array(
        [
            [0.2, 0.2, 0.8, 0.8, 0.8, 0.8, 0.5, 0.5],  # 8-frame (re-extracted) traces
            [0.2, 0.2, 0.8, 0.8, 0.8, 0.8, 0.5, 0.5],
        ]
    )
    s = np.array(
        [
            [0, 0, 1, 1, 1, 1, NO_STATE, NO_STATE, NO_STATE, NO_STATE, NO_STATE],  # fits [0, 6)
            [NO_STATE, NO_STATE, NO_STATE, 0, 0, 1, 1, 1, 1, 1, 1],  # valid to frame 10 > 8
        ],
        dtype="int64",
    )
    proj, _, _ = _build_store_with_model(tmp_path, e, s)
    triples = windowed_state_and_channels(proj, "vbconhmm", None, "corrected", False)
    assert len(triples) == 1  # the outrunning molecule is dropped
    state, donor, acceptor = triples[0]
    assert state.shape == donor.shape == acceptor.shape  # the surviving pairing is aligned
    np.testing.assert_array_equal(state, np.array([0, 0, 1, 1, 1, 1]))
    # The population entry point does not raise on the mixed store either.
    h = population_transition_sync_histogram2d(proj, "vbconhmm", sync_preframe=3, time_bins=8)
    assert h.n_molecules == 1
