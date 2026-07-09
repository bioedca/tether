# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Transition-probability histogram (M6 B3, FR-ANALYZE; PRD §7.7, Appendix C B3).

Tether's B3 is the consensus-model analogue of tMAVEN's ``tm_hist`` per-trace
``norm_tmatrix[init, fin]`` histogram: each molecule's transition probability is the
maximum-likelihood one-step ``P(init → fin)`` estimated from its persisted Viterbi
path. The store path enforces the two Tether invariants tMAVEN has no analogue for —
**fresh idealizations only** (PRD §5.1) and the §7.5 curation filter — as the B1 TDP
does. All headless (no Qt) → base CI matrix; the KDE overlay uses ``scipy.stats``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("h5py")

import h5py  # noqa: E402
import numpy as np  # noqa: E402

from tether.analysis import (  # noqa: E402
    DEFAULT_TPROB_KDE_BANDWIDTH,
    DEFAULT_TPROB_KDE_POINTS,
    DEFAULT_TPROB_NBINS,
    DEFAULT_TPROB_RANGE,
    TransitionProbHistogram,
    empirical_transition_probability,
    population_transition_prob_histogram,
    transition_prob_histogram,
)
from tether.idealize import NO_STATE  # noqa: E402
from tether.imaging.aperture import IntegratedTraces  # noqa: E402
from tether.imaging.calibrate import RegistrationMap  # noqa: E402
from tether.imaging.coloc import ColocalizedMolecules  # noqa: E402
from tether.imaging.extract import (  # noqa: E402
    MoleculeTraces,
    MovieMetadata,
    read_molecules,
    read_traces,
    write_extraction,
)
from tether.imaging.register import PolyTransform2D  # noqa: E402
from tether.imaging.split import ChannelGeometry  # noqa: E402
from tether.io.filename import parse_filename  # noqa: E402
from tether.io.schema import create_project  # noqa: E402
from tether.project import Project  # noqa: E402
from tether.project.idealize import input_provenance_hash, write_idealization_model  # noqa: E402
from tether.project.labels import CurationLabel  # noqa: E402

_PARSED = parse_filename("Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif")
_WINDOW = 21
_MEANS = np.array([0.2, 0.55, 0.85])


# --- pure core: empirical_transition_probability ------------------------------


def test_empirical_probability_hand_checked() -> None:
    # from state 0: successors (0->0),(0->0),(0->1) -> denom 3
    v = np.array([0, 0, 0, 1, 1, 1])
    assert empirical_transition_probability(v, 0, 1) == pytest.approx(1 / 3)
    assert empirical_transition_probability(v, 0, 0) == pytest.approx(2 / 3)  # self-pairs count
    # from state 1: only (1->1),(1->1) -> no exit; P(1->0)=0, P(1->1)=1
    assert empirical_transition_probability(v, 1, 0) == pytest.approx(0.0)
    assert empirical_transition_probability(v, 1, 1) == pytest.approx(1.0)


def test_empirical_probability_undefined_when_init_absent() -> None:
    v = np.array([0, 0, 1, 1])
    assert empirical_transition_probability(v, 2, 0) is None  # state 2 never occupied
    assert empirical_transition_probability(np.array([0]), 0, 1) is None  # length < 2
    assert empirical_transition_probability(np.array([], dtype="int64"), 0, 1) is None


def test_empirical_probability_gap_successor_excluded() -> None:
    # state 0 at frame 0, but its successor is a gap -> not an observed transition.
    v = np.array([0, NO_STATE, 1, 1])
    assert empirical_transition_probability(v, 0, 1) is None  # denom 0 (gap successor)
    # a real 0->1 plus a gap-terminated 0: only the observed pair counts.
    v2 = np.array([0, 1, 1, 0, NO_STATE])
    assert empirical_transition_probability(v2, 0, 1) == pytest.approx(1.0)  # 1 of 1 observed


# --- pure core: transition_prob_histogram -------------------------------------


def test_defaults_match_tmaven() -> None:
    assert DEFAULT_TPROB_NBINS == 25
    assert DEFAULT_TPROB_RANGE == (-0.05, 1.05)
    assert DEFAULT_TPROB_KDE_BANDWIDTH == 0.25
    assert DEFAULT_TPROB_KDE_POINTS == 100


def test_shape_and_edges() -> None:
    h = transition_prob_histogram([], init_state=0, final_state=1, prob_bins=10)
    assert isinstance(h, TransitionProbHistogram)
    assert h.counts.shape == (10,)
    assert h.edges.shape == (11,)
    assert h.n_bins == 10
    np.testing.assert_allclose(h.edges, np.linspace(-0.05, 1.05, 11))
    np.testing.assert_allclose(h.centers, 0.5 * (h.edges[:-1] + h.edges[1:]))


def test_empty_is_all_zero_never_nan() -> None:
    h = transition_prob_histogram([], init_state=0, final_state=1)
    assert h.n_molecules == 0
    assert h.counts.sum() == 0.0
    assert not np.any(np.isnan(h.counts))
    assert h.kde_x is None and h.kde_y is None
    assert h.probabilities.size == 0


def test_molecules_without_init_are_dropped() -> None:
    chunks = [
        np.array([0, 0, 1]),  # P(0->1) = 0.5 (successors 0->0, 0->1)
        np.array([2, 2, 2]),  # never in state 0 -> dropped
    ]
    h = transition_prob_histogram(chunks, init_state=0, final_state=1, kde=False)
    assert h.n_molecules == 1
    np.testing.assert_allclose(h.probabilities, np.array([0.5]))


def test_density_integrates_to_one() -> None:
    # three molecules with defined P(0->1); density histogram integrates to 1.
    chunks = [np.array([0, 1]), np.array([0, 0, 1]), np.array([0, 0, 0, 1])]
    h = transition_prob_histogram(chunks, init_state=0, final_state=1, density=True, kde=False)
    width = np.diff(h.edges)[0]
    assert float(h.counts.sum() * width) == pytest.approx(1.0, rel=1e-9)
    assert h.density is True


def test_raw_counts_when_density_false() -> None:
    chunks = [np.array([0, 1]), np.array([0, 1])]  # both P=1.0
    h = transition_prob_histogram(
        chunks, init_state=0, final_state=1, density=False, kde=False, prob_bins=25
    )
    assert h.counts.sum() == 2.0  # raw counts
    assert h.density is False


def test_density_empty_never_nan() -> None:
    h = transition_prob_histogram([], init_state=0, final_state=1, density=True)
    assert h.counts.sum() == 0.0
    assert not np.any(np.isnan(h.counts))


def test_density_all_out_of_range_never_nan() -> None:
    # a non-empty population whose every probability falls OUTSIDE a narrowed prob_range:
    # the density path would divide by the in-range count (0) -> all-NaN. The guard must
    # gate on in-range mass, not population size, so this stays all-zeros (never NaN).
    chunks = [np.array([0, 0, 1]), np.array([0, 0, 1])]  # both P(0->1) = 0.5
    h = transition_prob_histogram(
        chunks, init_state=0, final_state=1, density=True, prob_range=(0.8, 0.9), kde=False
    )
    assert h.n_molecules == 2  # both still counted
    assert h.counts.sum() == 0.0
    assert not np.any(np.isnan(h.counts))


# --- pure core: KDE overlay ---------------------------------------------------


def test_kde_present_with_two_distinct_probs() -> None:
    # P values {0.5, 1.0}: computable KDE.
    chunks = [np.array([0, 0, 1]), np.array([0, 1])]
    h = transition_prob_histogram(chunks, init_state=0, final_state=1, kde=True)
    assert h.kde_x is not None and h.kde_y is not None
    assert h.kde_x.shape == (DEFAULT_TPROB_KDE_POINTS,)
    assert h.kde_y.shape == (DEFAULT_TPROB_KDE_POINTS,)
    np.testing.assert_allclose(h.kde_x[[0, -1]], [0.0, 1.0])
    assert np.all(np.isfinite(h.kde_y))


def test_kde_none_when_all_probs_identical() -> None:
    # two molecules, both P(0->1)=1.0 -> singular covariance -> no curve (never crash).
    chunks = [np.array([0, 1]), np.array([0, 1])]
    h = transition_prob_histogram(chunks, init_state=0, final_state=1, kde=True)
    assert h.n_molecules == 2
    assert h.kde_x is None and h.kde_y is None


def test_kde_none_with_single_molecule() -> None:
    h = transition_prob_histogram([np.array([0, 1])], init_state=0, final_state=1, kde=True)
    assert h.n_molecules == 1
    assert h.kde_x is None and h.kde_y is None


def test_kde_disabled() -> None:
    chunks = [np.array([0, 0, 1]), np.array([0, 1])]
    h = transition_prob_histogram(chunks, init_state=0, final_state=1, kde=False)
    assert h.kde_x is None and h.kde_y is None


def test_kde_points_and_bandwidth_respected() -> None:
    chunks = [np.array([0, 0, 1]), np.array([0, 1]), np.array([0, 0, 0, 1])]
    h = transition_prob_histogram(
        chunks, init_state=0, final_state=1, kde=True, kde_points=50, kde_bandwidth=0.4
    )
    assert h.kde_x is not None
    assert h.kde_x.shape == (50,)


def test_kde_bandwidth_changes_curve() -> None:
    # the bandwidth must actually reach scipy: two curves from the same data at
    # different bandwidths differ (a mutant that hardcodes the default is caught here).
    chunks = [np.array([0, 0, 1]), np.array([0, 1]), np.array([0, 0, 0, 1])]
    h_low = transition_prob_histogram(chunks, init_state=0, final_state=1, kde_bandwidth=0.25)
    h_high = transition_prob_histogram(chunks, init_state=0, final_state=1, kde_bandwidth=0.4)
    assert h_low.kde_y is not None and h_high.kde_y is not None
    assert not np.allclose(h_low.kde_y, h_high.kde_y)


# --- pure core: validation ----------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"init_state": -1, "final_state": 0}, "init_state"),
        ({"init_state": 0, "final_state": -1}, "final_state"),
        ({"init_state": 0, "final_state": 1, "prob_bins": 0}, "prob_bins"),
        ({"init_state": 0, "final_state": 1, "prob_range": (1.0, 1.0)}, "prob_range"),
        ({"init_state": 0, "final_state": 1, "prob_range": (1.0, 0.0)}, "prob_range"),
        ({"init_state": 0, "final_state": 1, "kde_bandwidth": 0.0}, "kde_bandwidth"),
        ({"init_state": 0, "final_state": 1, "kde_points": 1}, "kde_points"),
    ],
)
def test_validation_errors(kwargs, match) -> None:
    with pytest.raises(ValueError, match=match):
        transition_prob_histogram([], **kwargs)


def test_flat_array_misuse_raises_not_silent() -> None:
    with pytest.raises(ValueError, match="scalar element"):
        transition_prob_histogram(np.array([0, 0, 1]), init_state=0, final_state=1)
    with pytest.raises(ValueError, match="scalar element"):
        transition_prob_histogram([0, 0, 1], init_state=0, final_state=1)
    ok = transition_prob_histogram(np.array([[0, 0, 1]]), init_state=0, final_state=1, kde=False)
    assert ok.n_molecules == 1


# --- store-level: seed a .tether with molecules + traces + idealization -------


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


def _to_str(value: object) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _fresh_input_hashes(path: Path) -> list[str]:
    molecules = read_molecules(path)
    traces = read_traces(path)
    donor_all = np.asarray(traces["donor_corrected"], dtype="float64")
    acceptor_all = np.asarray(traces["acceptor_corrected"], dtype="float64")
    pre_all = molecules["analysis_window"]
    fr_all = molecules["frame_range"]
    hashes: list[str] = []
    for i in range(molecules.shape[0]):
        lo, hi = int(pre_all[i][0]), int(pre_all[i][1])
        if hi <= lo:
            lo, hi = int(fr_all[i][0]), int(fr_all[i][1])
        hashes.append(
            input_provenance_hash(
                donor_all[i, lo:hi],
                acceptor_all[i, lo:hi],
                quantity="corrected",
                alpha=float(molecules["alpha"][i]),
                gamma=float(molecules["gamma"][i]),
                correction_method=_to_str(molecules["correction_method"][i]),
                pre=lo,
                post=hi,
            )
        )
    return hashes


def _build_store_with_model(
    tmp_path: Path,
    state_matrix: np.ndarray,
    means: np.ndarray,
    *,
    rejected: list[bool] | None = None,
    stale: list[bool] | None = None,
    model_name: str = "vbconhmm",
    name: str = "exp.tether",
) -> tuple[Project, list[str]]:
    state_matrix = np.asarray(state_matrix, dtype="int64")
    means = np.asarray(means, dtype="float64")
    n, n_frames = state_matrix.shape
    e_matrix = np.full((n, n_frames), np.nan)
    on = state_matrix != NO_STATE
    e_matrix[on] = means[state_matrix[on]]
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
            table["analysis_window"][i] = (0, n_frames)
            if rejected is not None and rejected[i]:
                table["curation_label"][i] = int(CurationLabel.REJECT)
        f["molecules"]["table"][:] = table

    molecules = read_molecules(path)
    keys = [_to_str(k) for k in molecules["molecule_key"]]
    ids = [_to_str(x) for x in molecules["molecule_id"]]

    idealized = np.full((n, n_frames), np.nan)
    idealized[on] = means[state_matrix[on]]

    hashes = _fresh_input_hashes(path)
    if stale is not None:
        hashes = [f"STALE-{h}" if stale[i] else h for i, h in enumerate(hashes)]

    write_idealization_model(
        path,
        model_name=model_name,
        model_type=model_name,
        nstates=int(means.size),
        dtype="FRET",
        means=means,
        variances=np.full(means.size, 0.01),
        tmatrix=None,
        norm_tmatrix=None,
        elbo=1.0,
        idealized=idealized,
        state_paths=state_matrix,
        molecule_keys=keys,
        molecule_ids=ids,
        input_hashes=hashes,
        intensity_quantity="corrected",
        selected_by="fixed",
        elbo_by_nstates=None,
        app_version="test",
        created_utc="2026-01-01T00:00:00Z",
        overwrite=True,
        frac=np.full(means.size, 1.0 / means.size),
    )
    return Project.open(path), keys


def _states() -> np.ndarray:
    # molecule 0: two 0->1 exits out of three 0-with-successor frames -> P(0->1)=2/3
    #   path 0,0,1,0,1,1 : frame0 0->0, frame1 0->1, frame3 0->1 ; denom 3, numer 2
    # molecule 1: single 0->1 -> P(0->1)=1.0
    return np.array(
        [
            [0, 0, 1, 0, 1, 1],
            [0, 1, 1, 1, 1, 1],
        ],
        dtype="int64",
    )


def test_population_matches_core(tmp_path) -> None:
    s = _states()
    proj, _keys = _build_store_with_model(tmp_path, s, _MEANS)
    h = population_transition_prob_histogram(proj, "vbconhmm", 0, 1, kde=False)
    assert isinstance(h, TransitionProbHistogram)
    assert h.n_molecules == 2
    np.testing.assert_allclose(sorted(h.probabilities), [2 / 3, 1.0])
    ref = transition_prob_histogram([s[0], s[1]], init_state=0, final_state=1, kde=False)
    np.testing.assert_array_equal(h.counts, ref.counts)


def test_population_state_range_validation(tmp_path) -> None:
    s = _states()
    proj, _keys = _build_store_with_model(tmp_path, s, _MEANS)  # nstates == 3
    with pytest.raises(ValueError, match="init_state"):
        population_transition_prob_histogram(proj, "vbconhmm", 3, 0)
    with pytest.raises(ValueError, match="final_state"):
        population_transition_prob_histogram(proj, "vbconhmm", 0, 5)


def test_stale_molecule_excluded_by_default(tmp_path) -> None:
    s = _states()
    proj, _keys = _build_store_with_model(tmp_path, s, _MEANS, stale=[False, True])
    h = population_transition_prob_histogram(proj, "vbconhmm", 0, 1, kde=False)
    assert h.n_molecules == 1
    np.testing.assert_allclose(h.probabilities, [2 / 3])


def test_include_stale_restores_the_molecule(tmp_path) -> None:
    s = _states()
    proj, _keys = _build_store_with_model(tmp_path, s, _MEANS, stale=[False, True])
    h = population_transition_prob_histogram(proj, "vbconhmm", 0, 1, include_stale=True, kde=False)
    assert h.n_molecules == 2


def test_rejected_molecule_excluded_by_default(tmp_path) -> None:
    s = _states()
    proj, _keys = _build_store_with_model(tmp_path, s, _MEANS, rejected=[False, True])
    h = population_transition_prob_histogram(proj, "vbconhmm", 0, 1, kde=False)
    assert h.n_molecules == 1


def test_include_rejected_restores_the_molecule(tmp_path) -> None:
    s = _states()
    proj, _keys = _build_store_with_model(tmp_path, s, _MEANS, rejected=[False, True])
    h = population_transition_prob_histogram(
        proj, "vbconhmm", 0, 1, include_rejected=True, kde=False
    )
    assert h.n_molecules == 2


def test_molecule_keys_selection(tmp_path) -> None:
    s = _states()
    proj, keys = _build_store_with_model(tmp_path, s, _MEANS)
    h = population_transition_prob_histogram(
        proj, "vbconhmm", 0, 1, molecule_keys=[keys[1]], kde=False
    )
    assert h.n_molecules == 1
    np.testing.assert_allclose(h.probabilities, [1.0])


def test_molecule_keys_intersect_fresh(tmp_path) -> None:
    # explicitly selecting a STALE key yields nothing: the fresh intersection (not just
    # the molecule_keys selection) gates it. include_stale restores it.
    s = _states()
    proj, keys = _build_store_with_model(tmp_path, s, _MEANS, stale=[False, True])
    h = population_transition_prob_histogram(
        proj, "vbconhmm", 0, 1, molecule_keys=[keys[1]], kde=False
    )
    assert h.n_molecules == 0
    assert h.probabilities.size == 0
    h2 = population_transition_prob_histogram(
        proj, "vbconhmm", 0, 1, molecule_keys=[keys[1]], include_stale=True, kde=False
    )
    assert h2.n_molecules == 1  # the stale molecule (P=1.0) restored
    np.testing.assert_allclose(h2.probabilities, [1.0])


def test_missing_model_raises(tmp_path) -> None:
    s = _states()
    proj, _keys = _build_store_with_model(tmp_path, s, _MEANS)
    with pytest.raises(KeyError):
        population_transition_prob_histogram(proj, "no-such-model", 0, 1)
