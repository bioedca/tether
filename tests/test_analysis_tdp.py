# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real Transition Density Plot (M6 B1, FR-ANALYZE; PRD §7.7, Appendix C B1).

Covers tMAVEN's TDP (``data_tdp.py`` ``get_neighbor_data`` + ``gen_histogram``): the
initial-vs-final idealized-FRET density over neighbour pairs (``nskip = 2``),
restricted to state-change frames, log-normalized. A verbatim port of the tMAVEN
reference is the parity oracle. The store path additionally enforces the two Tether
invariants tMAVEN has no analogue for: **fresh idealizations only** (STALE molecules
excluded, PRD §5.1) and the §7.5 curation filter. All headless (no Qt) → runs in the
base CI matrix; the store is seeded as post-idealization data under the M0-frozen
schema.
"""

from __future__ import annotations

import pytest

pytest.importorskip("numpy")
pytest.importorskip("h5py")

import numpy as np  # noqa: E402

from _analysis_store import MEANS, build_store_with_model  # noqa: E402
from tether.analysis import (  # noqa: E402
    DEFAULT_TDP_NSKIP,
    DEFAULT_TDP_SIGNAL_BINS,
    DEFAULT_TDP_SIGNAL_RANGE,
    TransitionDensityPlot,
    population_transition_density,
    transition_density,
)

# --- tMAVEN reference oracle (data_tdp.py, ported verbatim) -------------------


def _tmaven_tdp(
    idealized: np.ndarray,
    *,
    nskip: int,
    signal_nbins: int,
    signal_min: float,
    signal_max: float,
) -> tuple[np.ndarray, int]:
    """tMAVEN's ``get_neighbor_data`` (idealized branch) + ``gen_histogram`` verbatim.

    ``idealized`` is a rectangular ``(nmol, nframes)`` idealized-level array (NaN
    outside each trace's window) — tMAVEN's ``v`` for the ``hist_rawsignal=False``,
    model-present path. Returns ``(z, npoints)`` with ``z`` the raw (unsmoothed,
    un-normalized) ``histogram2d`` and ``npoints`` tMAVEN's ``d1.size``.
    """
    v = np.asarray(idealized, dtype="float64")
    d1 = v[:, :-nskip].copy()
    d2 = v[:, nskip:].copy()
    jump = np.abs(v[:, 1:] - v[:, :-1]) > 0
    if nskip > 1:
        jump = jump[:, : -(nskip - 1)]
    d1 = d1[jump]
    d2 = d2[jump]
    d1 = d1.flatten()
    d2 = d2.flatten()
    keep = np.isfinite(d1) * np.isfinite(d2)
    d1 = d1[keep]
    d2 = d2[keep]
    x = np.linspace(signal_min, signal_max, signal_nbins)
    z, _hx, _hy = np.histogram2d(
        d1, d2, bins=[x.size, x.size], range=[[x.min(), x.max()], [x.min(), x.max()]]
    )
    return z, int(d1.size)


def _chunks_from_rect(idealized: np.ndarray) -> list[np.ndarray]:
    """Rows of a rectangular NaN-padded oracle input as ragged per-molecule chunks."""
    return [np.asarray(idealized[i], dtype="float64") for i in range(idealized.shape[0])]


def _sbin(value: float, rng=DEFAULT_TDP_SIGNAL_RANGE, nbins: int = DEFAULT_TDP_SIGNAL_BINS) -> int:
    lo, hi = rng
    return int((value - lo) / (hi - lo) * nbins)


# --- pure core: shape + edges -------------------------------------------------


def test_defaults_match_tmaven() -> None:
    assert DEFAULT_TDP_NSKIP == 2
    assert DEFAULT_TDP_SIGNAL_BINS == 101
    assert DEFAULT_TDP_SIGNAL_RANGE == (-0.25, 1.25)


def test_shape_and_edges() -> None:
    h = transition_density([], signal_bins=20, signal_range=(-0.2, 1.2))
    assert isinstance(h, TransitionDensityPlot)
    assert h.counts.shape == (20, 20)
    assert h.signal_bins == 20
    assert h.signal_edges.shape == (21,)
    np.testing.assert_allclose(h.signal_edges, np.linspace(-0.2, 1.2, 21))
    # both axes share one signal grid (square E x E)
    np.testing.assert_array_equal(h.initial_centers, h.final_centers)
    np.testing.assert_allclose(h.signal_centers, 0.5 * (h.signal_edges[:-1] + h.signal_edges[1:]))


def test_empty_input_is_all_zero_never_nan() -> None:
    h = transition_density([], signal_bins=8)
    assert h.counts.shape == (8, 8)
    assert h.counts.sum() == 0.0
    assert not np.any(np.isnan(h.counts))
    assert h.n_molecules == 0
    assert h.n_transitions == 0


# --- pure core: hand-checked transitions --------------------------------------


def test_single_transition_lands_off_diagonal() -> None:
    # 0.2 -> 0.8 once; with nskip=2 the pair is (v[t], v[t+2]) at the jump frame t.
    v = np.array([0.2, 0.2, 0.2, 0.8, 0.8, 0.8])
    h = transition_density([v], nskip=2)
    assert h.n_molecules == 1
    assert h.n_transitions == 1
    # the jump is between frames 2 and 3; initial = v[2] = 0.2, final = v[4] = 0.8.
    assert h.counts[_sbin(0.2), _sbin(0.8)] == 1.0
    assert h.counts.sum() == 1.0
    # nothing on the reverse / diagonal cell
    assert h.counts[_sbin(0.8), _sbin(0.2)] == 0.0


def test_nskip_one_uses_adjacent_frames() -> None:
    # nskip=1: final is the immediately next frame. Single 0.2->0.8 step at t=1->2.
    v = np.array([0.2, 0.2, 0.8, 0.8])
    h = transition_density([v], nskip=1)
    assert h.n_transitions == 1
    # jump at t=1; initial v[1]=0.2, final v[2]=0.8.
    assert h.counts[_sbin(0.2), _sbin(0.8)] == 1.0


def test_constant_trace_has_no_transition() -> None:
    v = np.full(10, 0.5)
    h = transition_density([v], nskip=2)
    assert h.n_transitions == 0
    assert h.n_molecules == 0
    assert h.counts.sum() == 0.0


def test_only_state_change_frames_contribute() -> None:
    # two distinct transitions: 0.2->0.5 and 0.5->0.85. The many non-change frames
    # in between must NOT emit diagonal (0.5, 0.5) self-pairs.
    v = np.array([0.2, 0.2, 0.2, 0.5, 0.5, 0.5, 0.5, 0.85, 0.85, 0.85])
    h = transition_density([v], nskip=2)
    assert h.n_transitions == 2
    assert h.counts[_sbin(0.2), _sbin(0.5)] == 1.0
    assert h.counts[_sbin(0.5), _sbin(0.85)] == 1.0
    # no self-transition on the 0.5 plateau
    assert h.counts[_sbin(0.5), _sbin(0.5)] == 0.0
    assert h.counts.sum() == 2.0


def test_n_molecules_counts_only_contributors() -> None:
    v_with = np.array([0.2, 0.2, 0.8, 0.8, 0.8])  # one transition
    v_flat = np.full(5, 0.3)  # none
    h = transition_density([v_with, v_flat], nskip=2)
    assert h.n_molecules == 1
    assert h.n_transitions == 1


def test_length_at_or_below_nskip_skipped() -> None:
    # length == nskip -> no pairs; length < nskip -> no pairs; empty -> no pairs.
    h = transition_density(
        [np.array([0.2, 0.8]), np.array([0.5]), np.array([], dtype="float64")], nskip=2
    )
    assert h.n_molecules == 0
    assert h.n_transitions == 0
    assert h.counts.sum() == 0.0


# --- pure core: NaN / range handling ------------------------------------------


def test_nan_gap_is_not_a_transition() -> None:
    # NaN padding on both ends + an interior gap: none of the NaN borders are jumps.
    v = np.array([np.nan, np.nan, 0.2, 0.2, 0.8, 0.8, np.nan])
    h = transition_density([v], nskip=2)
    assert h.n_transitions == 1  # only the real 0.2 -> 0.8
    assert h.counts[_sbin(0.2), _sbin(0.8)] == 1.0
    assert not np.any(np.isnan(h.counts))


def test_interior_nan_breaks_a_run() -> None:
    # 0.3 [gap] 0.3: the gap means neither border is a jump and no 0.3->0.3 pair.
    v = np.array([0.3, 0.3, np.nan, 0.3, 0.3])
    h = transition_density([v], nskip=2)
    assert h.n_transitions == 0
    assert h.counts.sum() == 0.0


def test_out_of_range_transition_counts_but_not_binned() -> None:
    # a real transition whose levels fall outside signal_range: counts toward n but
    # is dropped from the histogram (tMAVEN semantics: n = d1.size, z drops range).
    v = np.array([5.0, 5.0, -5.0, -5.0])
    h = transition_density([v], nskip=2, signal_range=(-0.25, 1.25))
    assert h.n_transitions == 1
    assert h.n_molecules == 1
    assert h.counts.sum() == 0.0
    assert not np.any(np.isnan(h.counts))


def test_top_edge_included_like_histogram2d() -> None:
    # np.histogram2d puts the exact upper edge in the last bin (matches tMAVEN, which
    # also uses histogram2d — not the A2b manual right-open convention).
    lo, hi = -0.25, 1.25
    v = np.array([lo, lo, hi, hi])
    h = transition_density([v], nskip=2, signal_bins=10, signal_range=(lo, hi))
    assert h.counts.sum() == 1.0
    assert h.counts[0, -1] == 1.0  # initial at lo (bin 0), final at hi (last bin)


# --- pure core: density + validation ------------------------------------------


def _oracle_case() -> np.ndarray:
    """Two equal-length molecules with several transitions, all interior + in-range."""
    levels = {0: 0.15, 1: 0.55, 2: 0.85}
    s = np.array(
        [
            [0, 0, 0, 0, 1, 1, 1, 1, 1, 2, 2, 2, 2, 2, 0, 0, 0, 0, 0, 0],
            [0, 0, 0, 0, 0, 2, 2, 2, 2, 2, 2, 1, 1, 1, 1, 1, 1, 1, 1, 1],
        ]
    )
    v = np.vectorize(levels.get)(s).astype("float64")
    return v


def test_density_integrates_to_one() -> None:
    v = _oracle_case()
    h = transition_density(_chunks_from_rect(v), density=True)
    cell = np.diff(h.signal_edges)[0] ** 2
    integral = float(h.counts.sum() * cell)
    np.testing.assert_allclose(integral, 1.0, rtol=1e-9)


def test_density_empty_is_zero_never_nan() -> None:
    # density=True with no points must NOT divide-by-zero into all-NaN (the numpy
    # density path would); it stays all-zeros — the invariant the log-norm render needs.
    h = transition_density([], density=True)
    assert h.counts.sum() == 0.0
    assert not np.any(np.isnan(h.counts))
    assert h.n_transitions == 0


def test_density_all_out_of_range_is_zero_never_nan() -> None:
    # a real transition, but both levels out of signal_range: in-range mass is zero,
    # so density normalization must not produce NaN.
    v = np.array([5.0, 5.0, -5.0, -5.0])
    h = transition_density([v], nskip=2, density=True, signal_range=(-0.25, 1.25))
    assert h.n_transitions == 1  # still counted
    assert h.counts.sum() == 0.0
    assert not np.any(np.isnan(h.counts))


@pytest.mark.parametrize("nskip", [1, 2, 3])
def test_parity_with_tmaven_oracle(nskip: int) -> None:
    v = _oracle_case()
    lo, hi = DEFAULT_TDP_SIGNAL_RANGE
    nbins = DEFAULT_TDP_SIGNAL_BINS
    ref, npoints = _tmaven_tdp(v, nskip=nskip, signal_nbins=nbins, signal_min=lo, signal_max=hi)
    h = transition_density(
        _chunks_from_rect(v), nskip=nskip, signal_bins=nbins, signal_range=(lo, hi)
    )
    np.testing.assert_array_equal(h.counts, ref)
    assert h.n_transitions == npoints


def test_ragged_windows_match_padded_oracle() -> None:
    # Genuinely ragged windows (variable length, NaN padding stripped) must give the
    # same TDP as the NaN-padded rectangular oracle — the module's core equivalence
    # claim: padding never adds or drops a transition.
    padded = np.array(
        [
            [np.nan, np.nan, 0.2, 0.2, 0.8, 0.8, np.nan, np.nan],
            [0.2, 0.2, 0.2, 0.8, 0.8, 0.8, 0.8, 0.8],
        ]
    )
    ref, npoints = _tmaven_tdp(padded, nskip=2, signal_nbins=61, signal_min=-0.2, signal_max=1.2)
    # strip the NaN padding to variable-length real windows (what the store yields)
    ragged = [np.array([0.2, 0.2, 0.8, 0.8]), np.array([0.2, 0.2, 0.2, 0.8, 0.8, 0.8, 0.8, 0.8])]
    assert ragged[0].size != ragged[1].size  # actually ragged
    h = transition_density(ragged, nskip=2, signal_bins=61, signal_range=(-0.2, 1.2))
    np.testing.assert_array_equal(h.counts, ref)
    assert h.n_transitions == npoints == 2


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"nskip": 0}, "nskip"),
        ({"nskip": -1}, "nskip"),
        ({"signal_bins": 0}, "signal_bins"),
        ({"signal_range": (1.0, 1.0)}, "signal_range"),
        ({"signal_range": (1.0, 0.0)}, "signal_range"),
    ],
)
def test_validation_errors(kwargs, match) -> None:
    with pytest.raises(ValueError, match=match):
        transition_density([], **kwargs)


def test_flat_array_misuse_raises_not_silent() -> None:
    # Passing a single molecule's flat 1-D array (instead of [v]) iterates to scalars;
    # fail fast rather than silently return an all-zero TDP.
    with pytest.raises(ValueError, match="scalar element"):
        transition_density(np.array([0.2, 0.2, 0.8, 0.8]), nskip=2)
    # a bare list of floats is the same misuse
    with pytest.raises(ValueError, match="scalar element"):
        transition_density([0.2, 0.2, 0.8, 0.8], nskip=2)
    # a 2-D array is fine: each row is a molecule
    ok = transition_density(np.array([[0.2, 0.2, 0.8, 0.8]]), nskip=2)
    assert ok.n_transitions == 1


# --- store-level: seed a .tether with molecules + traces + idealization -------


def _two_transition_states() -> np.ndarray:
    # molecule 0: 0->1->2 ; molecule 1: 0->2 (single transition)
    return np.array(
        [
            [0, 0, 0, 1, 1, 1, 2, 2, 2, 2],
            [0, 0, 0, 0, 0, 2, 2, 2, 2, 2],
        ],
        dtype="int64",
    )


def test_population_matches_manual_core(tmp_path) -> None:
    s = _two_transition_states()
    proj, _keys = build_store_with_model(tmp_path, s, MEANS)
    h = population_transition_density(proj, "vbconhmm")
    assert isinstance(h, TransitionDensityPlot)
    # molecule 0 has two transitions, molecule 1 one -> 3 points, 2 molecules
    assert h.n_transitions == 3
    assert h.n_molecules == 2
    # equal to feeding the pure core the reconstructed level rows (means[state]).
    v = MEANS[s]
    ref = transition_density(_chunks_from_rect(v))
    np.testing.assert_array_equal(h.counts, ref.counts)
    assert h.n_transitions == ref.n_transitions


def test_population_matches_tmaven_oracle_via_store(tmp_path) -> None:
    s = _two_transition_states()
    proj, _keys = build_store_with_model(tmp_path, s, MEANS)
    h = population_transition_density(proj, "vbconhmm")
    ref, npoints = _tmaven_tdp(
        MEANS[s],
        nskip=DEFAULT_TDP_NSKIP,
        signal_nbins=DEFAULT_TDP_SIGNAL_BINS,
        signal_min=DEFAULT_TDP_SIGNAL_RANGE[0],
        signal_max=DEFAULT_TDP_SIGNAL_RANGE[1],
    )
    np.testing.assert_array_equal(h.counts, ref)
    assert h.n_transitions == npoints


def test_stale_molecule_excluded_by_default(tmp_path) -> None:
    s = _two_transition_states()
    # mark molecule 1 (the single 0->2 transition) STALE
    proj, _keys = build_store_with_model(tmp_path, s, MEANS, stale=[False, True])
    h = population_transition_density(proj, "vbconhmm")
    # only molecule 0's two transitions survive
    assert h.n_molecules == 1
    assert h.n_transitions == 2
    # molecule 1's 0.2 -> 0.85 jump (the only source of that cell) is gone
    rng = DEFAULT_TDP_SIGNAL_RANGE
    assert h.counts[_sbin(0.2, rng), _sbin(0.85, rng)] == 0.0


def test_include_stale_restores_the_molecule(tmp_path) -> None:
    s = _two_transition_states()
    proj, _keys = build_store_with_model(tmp_path, s, MEANS, stale=[False, True])
    h = population_transition_density(proj, "vbconhmm", include_stale=True)
    assert h.n_molecules == 2
    assert h.n_transitions == 3


def test_all_stale_gives_empty_tdp(tmp_path) -> None:
    s = _two_transition_states()
    proj, _keys = build_store_with_model(tmp_path, s, MEANS, stale=[True, True])
    h = population_transition_density(proj, "vbconhmm")
    assert h.n_molecules == 0
    assert h.n_transitions == 0
    assert h.counts.sum() == 0.0
    assert not np.any(np.isnan(h.counts))


def test_rejected_molecule_excluded_by_default(tmp_path) -> None:
    s = _two_transition_states()
    proj, _keys = build_store_with_model(tmp_path, s, MEANS, rejected=[False, True])
    h = population_transition_density(proj, "vbconhmm")
    assert h.n_molecules == 1
    assert h.n_transitions == 2


def test_include_rejected_restores_the_molecule(tmp_path) -> None:
    s = _two_transition_states()
    proj, _keys = build_store_with_model(tmp_path, s, MEANS, rejected=[False, True])
    h = population_transition_density(proj, "vbconhmm", include_rejected=True)
    assert h.n_molecules == 2
    assert h.n_transitions == 3


def test_molecule_keys_selection(tmp_path) -> None:
    s = _two_transition_states()
    proj, keys = build_store_with_model(tmp_path, s, MEANS)
    h = population_transition_density(proj, "vbconhmm", molecule_keys=[keys[0]])
    assert h.n_molecules == 1
    assert h.n_transitions == 2  # molecule 0's two transitions only


def test_molecule_keys_intersect_fresh(tmp_path) -> None:
    # selecting a STALE key yields nothing (fresh filter still applies).
    s = _two_transition_states()
    proj, keys = build_store_with_model(tmp_path, s, MEANS, stale=[False, True])
    h = population_transition_density(proj, "vbconhmm", molecule_keys=[keys[1]])
    assert h.n_molecules == 0
    assert h.n_transitions == 0


def test_missing_model_raises(tmp_path) -> None:
    s = _two_transition_states()
    proj, _keys = build_store_with_model(tmp_path, s, MEANS)
    with pytest.raises(KeyError):
        population_transition_density(proj, "no-such-model")


def test_windowed_states_reads_state_only(tmp_path) -> None:
    # the state-only reader returns per-molecule int64 state windows (no /traces), and
    # honours the curation filter — the substrate population_transition_density uses.
    from tether.analysis._store import windowed_states

    s = _two_transition_states()
    proj, keys = build_store_with_model(tmp_path, s, MEANS, rejected=[False, True])
    states = windowed_states(proj, "vbconhmm", None, include_rejected=False)
    assert len(states) == 1  # molecule 1 rejected
    assert states[0].dtype == np.int64
    np.testing.assert_array_equal(states[0], s[0])
    # include_rejected restores it; molecule_keys selects
    assert len(windowed_states(proj, "vbconhmm", None, include_rejected=True)) == 2
    only0 = windowed_states(proj, "vbconhmm", [keys[0]], include_rejected=True)
    assert len(only0) == 1
    np.testing.assert_array_equal(only0[0], s[0])
