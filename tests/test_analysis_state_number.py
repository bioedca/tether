# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""State-number bar chart (M6 C1, FR-ANALYZE; PRD §7.7, Appendix C C1).

Tether's C1 is the consensus-model analogue of tMAVEN's ``model_vbstates`` per-trace
vbFRET state count: each molecule's state number is the number of **distinct states
its persisted Viterbi path occupies**. The store path enforces the two Tether
invariants tMAVEN has no analogue for — **fresh idealizations only** (PRD §5.1) and
the §7.5 curation filter — exactly as the B1 TDP does. All headless (no Qt) → base CI
matrix; the store is seeded as post-idealization data under the M0-frozen schema.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("h5py")

import h5py  # noqa: E402
import numpy as np  # noqa: E402

from tether.analysis import (  # noqa: E402
    DEFAULT_STATE_NUMBER_LOW,
    StateNumberCounts,
    occupied_state_count,
    population_state_number,
    state_number_counts,
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


# --- pure core: occupied_state_count -----------------------------------------


def test_occupied_state_count_distinct_states() -> None:
    assert occupied_state_count(np.array([0, 0, 1, 1, 2, 2])) == 3
    assert occupied_state_count(np.array([1, 1, 1, 1])) == 1
    assert occupied_state_count(np.array([0, 2, 0, 2])) == 2  # revisits don't double-count


def test_occupied_state_count_ignores_no_state_gaps() -> None:
    v = np.array([NO_STATE, 0, 0, NO_STATE, 1, NO_STATE])
    assert occupied_state_count(v) == 2  # only states 0 and 1
    assert occupied_state_count(np.full(5, NO_STATE)) == 0  # all-gap -> zero


# --- pure core: state_number_counts ------------------------------------------


def test_defaults() -> None:
    assert DEFAULT_STATE_NUMBER_LOW == 1


def test_empty_input_is_all_zero() -> None:
    c = state_number_counts([])
    assert isinstance(c, StateNumberCounts)
    assert c.n_molecules == 0
    assert c.n_in_range == 0
    assert c.n_out_of_range == 0
    # states_high defaults down to states_low when there is no data
    assert c.states_low == 1
    assert c.states_high == 1
    np.testing.assert_array_equal(c.states, np.array([1]))
    np.testing.assert_array_equal(c.counts, np.array([0]))


def test_single_molecule_bar() -> None:
    c = state_number_counts([np.array([0, 0, 1, 1, 2])])  # 3 distinct states
    assert c.n_molecules == 1
    assert c.states_high == 3  # derived from data
    np.testing.assert_array_equal(c.states, np.array([1, 2, 3]))
    np.testing.assert_array_equal(c.counts, np.array([0, 0, 1]))
    assert c.n_in_range == 1
    assert c.n_bars == 3


def test_all_gap_molecule_not_counted() -> None:
    # a molecule whose path is entirely NO_STATE contributes nothing at all.
    c = state_number_counts([np.array([0, 0, 1]), np.full(4, NO_STATE)])
    assert c.n_molecules == 1  # only the real one
    np.testing.assert_array_equal(c.states, np.array([1, 2]))
    np.testing.assert_array_equal(c.counts, np.array([0, 1]))


def test_mixed_population_histogram() -> None:
    chunks = [
        np.array([0, 0, 0]),  # 1 state
        np.array([0, 1, 1]),  # 2 states
        np.array([0, 1, 2]),  # 3 states
        np.array([2, 2, 1]),  # 2 states
    ]
    c = state_number_counts(chunks)
    np.testing.assert_array_equal(c.states, np.array([1, 2, 3]))
    np.testing.assert_array_equal(c.counts, np.array([1, 2, 1]))
    assert c.n_molecules == 4
    assert c.n_in_range == 4


def test_states_high_clips_and_reports_out_of_range() -> None:
    # states 0,1,2 occupied -> 3-state molecule; clip axis at high=2 -> it is out of range.
    chunks = [np.array([0, 1]), np.array([0, 1, 2])]  # 2 states, 3 states
    c = state_number_counts(chunks, states_low=1, states_high=2)
    np.testing.assert_array_equal(c.states, np.array([1, 2]))
    np.testing.assert_array_equal(c.counts, np.array([0, 1]))  # only the 2-state molecule
    assert c.n_molecules == 2
    assert c.n_in_range == 1
    assert c.n_out_of_range == 1  # the 3-state molecule, honestly reported (no silent cap)


def test_states_low_above_one_excludes_low_molecules() -> None:
    chunks = [np.array([0, 0]), np.array([0, 1, 2])]  # 1 state, 3 states
    c = state_number_counts(chunks, states_low=2, states_high=3)
    np.testing.assert_array_equal(c.states, np.array([2, 3]))
    np.testing.assert_array_equal(c.counts, np.array([0, 1]))
    assert c.n_out_of_range == 1  # the 1-state molecule below the floor


def test_auto_range_below_floor_does_not_crash() -> None:
    # states_high=None with every molecule below states_low must NOT raise on its own
    # derived bound: the axis clamps up to [low, low] and the molecules count as
    # out-of-range (honest), mirroring the B1 TDP never-crash-on-underfull invariant.
    chunks = [np.array([0, 0, 0]), np.array([1, 1, 1])]  # each occupies 1 state
    c = state_number_counts(chunks, states_low=3, states_high=None)
    np.testing.assert_array_equal(c.states, np.array([3]))
    np.testing.assert_array_equal(c.counts, np.array([0]))
    assert c.n_molecules == 2
    assert c.n_in_range == 0
    assert c.n_out_of_range == 2


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"states_low": 0}, "states_low"),
        ({"states_low": -1}, "states_low"),
        ({"states_low": 3, "states_high": 2}, "states_high"),
    ],
)
def test_validation_errors(kwargs, match) -> None:
    with pytest.raises(ValueError, match=match):
        state_number_counts([], **kwargs)


def test_flat_array_misuse_raises_not_silent() -> None:
    with pytest.raises(ValueError, match="scalar element"):
        state_number_counts(np.array([0, 0, 1, 2]))
    with pytest.raises(ValueError, match="scalar element"):
        state_number_counts([0, 0, 1, 2])
    # a 2-D array is fine: each row is a molecule
    ok = state_number_counts(np.array([[0, 0, 1, 2]]))
    assert ok.n_molecules == 1
    np.testing.assert_array_equal(ok.counts, np.array([0, 0, 1]))


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
    """A ``.tether`` whose molecule ``i`` carries ``state_matrix[i]`` as its Viterbi
    path (NO_STATE outside the window) and state levels ``means``; input hashes are the
    real current ones (reads back FRESH) unless ``stale[i]`` corrupts them."""
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
    # molecule 0 visits {0,1,2} (3 states); molecule 1 visits {0,2} (2 states)
    return np.array(
        [
            [0, 0, 0, 1, 1, 1, 2, 2, 2, 2],
            [0, 0, 0, 0, 0, 2, 2, 2, 2, 2],
        ],
        dtype="int64",
    )


def test_population_matches_core(tmp_path) -> None:
    s = _states()
    proj, _keys = _build_store_with_model(tmp_path, s, _MEANS)
    c = population_state_number(proj, "vbconhmm")
    assert isinstance(c, StateNumberCounts)
    assert c.n_molecules == 2
    # molecule 0 -> 3 states, molecule 1 -> 2 states
    np.testing.assert_array_equal(c.states, np.array([1, 2, 3]))
    np.testing.assert_array_equal(c.counts, np.array([0, 1, 1]))
    # equal to feeding the pure core the per-molecule state rows
    ref = state_number_counts([s[0], s[1]])
    np.testing.assert_array_equal(c.counts, ref.counts)


def test_stale_molecule_excluded_by_default(tmp_path) -> None:
    s = _states()
    proj, _keys = _build_store_with_model(tmp_path, s, _MEANS, stale=[False, True])
    c = population_state_number(proj, "vbconhmm")
    assert c.n_molecules == 1  # only molecule 0 (3 states)
    np.testing.assert_array_equal(c.states, np.array([1, 2, 3]))
    np.testing.assert_array_equal(c.counts, np.array([0, 0, 1]))


def test_include_stale_restores_the_molecule(tmp_path) -> None:
    s = _states()
    proj, _keys = _build_store_with_model(tmp_path, s, _MEANS, stale=[False, True])
    c = population_state_number(proj, "vbconhmm", include_stale=True)
    assert c.n_molecules == 2


def test_rejected_molecule_excluded_by_default(tmp_path) -> None:
    s = _states()
    proj, _keys = _build_store_with_model(tmp_path, s, _MEANS, rejected=[False, True])
    c = population_state_number(proj, "vbconhmm")
    assert c.n_molecules == 1


def test_include_rejected_restores_the_molecule(tmp_path) -> None:
    s = _states()
    proj, _keys = _build_store_with_model(tmp_path, s, _MEANS, rejected=[False, True])
    c = population_state_number(proj, "vbconhmm", include_rejected=True)
    assert c.n_molecules == 2


def test_molecule_keys_selection(tmp_path) -> None:
    s = _states()
    proj, keys = _build_store_with_model(tmp_path, s, _MEANS)
    c = population_state_number(proj, "vbconhmm", molecule_keys=[keys[1]])
    assert c.n_molecules == 1  # molecule 1 (2 states)
    np.testing.assert_array_equal(c.states, np.array([1, 2]))
    np.testing.assert_array_equal(c.counts, np.array([0, 1]))


def test_molecule_keys_intersect_fresh(tmp_path) -> None:
    # explicitly selecting a STALE key yields nothing: the fresh intersection (not just
    # the molecule_keys selection) gates it. include_stale restores it.
    s = _states()
    proj, keys = _build_store_with_model(tmp_path, s, _MEANS, stale=[False, True])
    c = population_state_number(proj, "vbconhmm", molecule_keys=[keys[1]])
    assert c.n_molecules == 0
    assert c.n_in_range == 0
    c2 = population_state_number(proj, "vbconhmm", molecule_keys=[keys[1]], include_stale=True)
    assert c2.n_molecules == 1  # the stale 2-state molecule restored
    np.testing.assert_array_equal(c2.states, np.array([1, 2]))
    np.testing.assert_array_equal(c2.counts, np.array([0, 1]))


def test_missing_model_raises(tmp_path) -> None:
    s = _states()
    proj, _keys = _build_store_with_model(tmp_path, s, _MEANS)
    with pytest.raises(KeyError):
        population_state_number(proj, "no-such-model")
