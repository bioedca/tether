# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Store-integrated one-click idealization (M2 S6, PR-A; FR-IDEALIZE; ADR-0024).

Locks the headless half of "one-click vbFRET from the dock": read selected
molecules' traces from a ``.tether`` -> SMD -> vbFRET -> write
``/idealization/{model}`` back as **additive data** with a per-molecule
input-provenance hash. The sidecar is faked with a canned
:class:`~tether.idealize.IdealizationResult`, so the whole suite is headless and
runs in the base CI matrix; the live ``run_vbfret`` parity is gated separately by
``test_parity_sidecar``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("h5py")

import h5py  # noqa: E402
import numpy as np  # noqa: E402

from tether.idealize import IdealizationResult, StateModel, read_smd  # noqa: E402
from tether.idealize.driver import NO_STATE  # noqa: E402
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
from tether.io.schema import build_manifest, create_project, diff_manifest, introspect  # noqa: E402
from tether.project.core import Project  # noqa: E402
from tether.project.idealize import (  # noqa: E402
    idealize_molecules,
    input_trace_hash,
    list_idealizations,
    read_idealization,
    stale_molecule_keys,
)

_PARSED = parse_filename("Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif")
_WINDOW = 21


def _distinct_coords(n: int) -> np.ndarray:
    """``(n, 2)`` distinct in-frame coordinates (so each molecule_key differs)."""
    return np.array([[12.0 + 1.7 * i, 14.0 + 2.3 * (i % 7)] for i in range(n)], dtype="float64")


# --- store builder (controlled trace values, no imaging pipeline) ------------


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
    path: Path,
    donor_intensity: np.ndarray,
    acceptor_intensity: np.ndarray,
    *,
    movie_id: str = "mov-1",
    sha: str = "a" * 64,
    coords: np.ndarray | None = None,
) -> tuple[Project, list[str]]:
    """Write a ``.tether`` with controlled donor/acceptor *corrected* traces.

    Bypasses the movie-integration pipeline: builds :class:`MoleculeTraces`
    directly so the ``/traces/{donor,acceptor}_corrected`` values (the fit input)
    are exactly ``donor_intensity`` / ``acceptor_intensity``. Pass ``coords`` to
    control donor positions (e.g. two in one quantum bin → a shared molecule_key).
    """
    donor_intensity = np.asarray(donor_intensity, dtype="float64")
    acceptor_intensity = np.asarray(acceptor_intensity, dtype="float64")
    n, t = donor_intensity.shape
    coords = _distinct_coords(n) if coords is None else np.asarray(coords, dtype="float64")
    mols = ColocalizedMolecules(
        donor_xy=coords,
        acceptor_xy=coords,
        acceptor_detected=np.zeros(n, dtype=bool),
        donor_index=np.arange(n, dtype=np.intp),
        acceptor_index=np.full(n, -1, dtype=np.intp),
    )
    traces = MoleculeTraces(
        donor=_integrated(donor_intensity),
        acceptor=_integrated(acceptor_intensity),
        donor_patches=np.zeros((n, _WINDOW, _WINDOW), dtype="float32"),
        acceptor_patches=np.zeros((n, _WINDOW, _WINDOW), dtype="float32"),
        window=_WINDOW,
        disk_radius=3.0,
        ring_inner=6.0,
        ring_outer=8.0,
        bg_window=10,
    )
    movie = MovieMetadata(
        movie_id=movie_id,
        sha256=sha,
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


def _step_trace(n: int, t: int, *, low: float = 200.0, high: float = 800.0) -> np.ndarray:
    """``(n, t)`` two-level step (a distinct switch frame per molecule)."""
    out = np.full((n, t), low, dtype="float64")
    for i in range(n):
        out[i, (t // 3) + i :] = high
    return out


# --- the fake sidecar --------------------------------------------------------


def _fake_result(
    smd_path,
    *,
    nstates: int,
    elbo,
    model_type: str,
    multistate: bool = False,
    degenerate: bool = False,
) -> IdealizationResult:
    """A canned model whose ``idealized`` aligns to the SMD (NaN outside window).

    ``multistate`` fills the window with a mid-trace switch between two distinct
    means (so the state path has >1 level); ``degenerate`` returns a model with no
    ``idealized`` array (the failed-fit path).
    """
    smd = read_smd(smd_path)
    n, t = smd.n_molecules, smd.n_frames
    means = np.linspace(0.2, 0.8, nstates) if nstates > 1 else np.array([0.5])
    pre = smd.pre_list if smd.pre_list is not None else np.zeros(n, dtype=int)
    post = smd.post_list if smd.post_list is not None else np.full(n, t, dtype=int)
    if degenerate:
        idealized = None
    else:
        idealized = np.full((n, t), np.nan)
        for i in range(n):
            lo, hi = int(pre[i]), int(post[i])
            if multistate and means.size > 1 and hi - lo >= 2:
                mid = (lo + hi) // 2
                idealized[i, lo:mid] = means[0]
                idealized[i, mid:hi] = means[-1]
            else:
                idealized[i, lo:hi] = means[0]
    model = StateModel(
        model_type=model_type,
        nstates=nstates,
        means=means,
        variances=np.full(nstates, 0.01),
        tmatrix=np.eye(nstates),
        norm_tmatrix=np.eye(nstates) * 0.9,
        elbo=elbo,
        dtype="FRET",
        idealized=idealized,
        ran=np.arange(n, dtype="int64"),
    )
    return IdealizationResult(
        model=model,
        state_paths={},
        dwells=[],
        model_path=Path(smd_path),
        status={"ok": True},
        molecule_keys=smd.molecule_keys,
    )


def _make_runner(elbo_by_nstates: dict, calls: list, **fake_kwargs):
    def runner(smd_path, *, nstates, model_type="vbconhmm", **_kw):
        # Capture the SMD *now* (the caller cleans up its temp scratch dir on return,
        # so a post-hoc read of the path would find nothing).
        calls.append(
            {
                "smd_path": Path(smd_path),
                "smd": read_smd(smd_path),
                "nstates": int(nstates),
                "model_type": model_type,
            }
        )
        return _fake_result(
            smd_path,
            nstates=int(nstates),
            elbo=elbo_by_nstates[int(nstates)],
            model_type=model_type,
            **fake_kwargs,
        )

    return runner


# --- input-provenance hash ---------------------------------------------------


def test_input_trace_hash_is_deterministic_and_input_sensitive() -> None:
    d = np.array([1.0, 2.0, 3.0])
    a = np.array([4.0, 5.0, 6.0])
    base = input_trace_hash(d, a, "corrected")
    assert base == input_trace_hash(d.copy(), a.copy(), "corrected")  # deterministic
    assert base != input_trace_hash(d + 1e-6, a, "corrected")  # donor change
    assert base != input_trace_hash(d, a + 1e-6, "corrected")  # acceptor change
    assert base != input_trace_hash(d, a, "raw")  # quantity change
    assert base != input_trace_hash(d[:-1], a[:-1], "corrected")  # window length


# --- write + round-trip ------------------------------------------------------


def test_idealize_writes_model_group_and_round_trips(tmp_path) -> None:
    n, t = 3, 30
    proj, keys = _build_store(tmp_path / "e.tether", _step_trace(n, t), _step_trace(n, t) * 0.5)
    calls: list = []
    runner = _make_runner({2: -5.0}, calls)

    stored = idealize_molecules(proj, nstates=2, _runner=runner)

    assert stored.model_name == "vbconhmm"
    assert stored.nstates == 2
    assert stored.nstates_selected_by == "fixed"
    assert stored.molecule_keys == keys
    assert len(stored.input_hashes) == n
    assert stored.idealized.shape == (n, t)
    # states: 0 in-window (single fake level), NO_STATE nowhere (window is full here)
    assert set(np.unique(stored.state_paths)) <= {0, NO_STATE}

    back = read_idealization(proj, "vbconhmm")
    assert back.molecule_keys == stored.molecule_keys
    assert back.input_hashes == stored.input_hashes
    assert back.nstates == 2
    np.testing.assert_allclose(back.means, stored.means)
    np.testing.assert_allclose(back.tmatrix, stored.tmatrix)
    np.testing.assert_allclose(back.norm_tmatrix, stored.norm_tmatrix)  # round-trips
    assert back.norm_tmatrix is not None
    np.testing.assert_array_equal(back.state_paths, stored.state_paths)
    assert list_idealizations(proj) == ["vbconhmm"]


def test_input_hash_matches_windowed_corrected_trace(tmp_path) -> None:
    n, t = 2, 24
    donor = _step_trace(n, t)
    acceptor = _step_trace(n, t) * 0.7
    proj, keys = _build_store(tmp_path / "e.tether", donor, acceptor)
    calls: list = []
    stored = idealize_molecules(proj, nstates=2, _runner=_make_runner({2: -1.0}, calls))
    # full window [0, t): the recorded hash must equal a fresh hash of the input
    for i in range(len(keys)):
        expected = input_trace_hash(donor[i], acceptor[i], "corrected")
        assert stored.input_hashes[i] == expected


# --- auto state-count (max ELBO) ---------------------------------------------


def test_auto_nstates_picks_max_elbo(tmp_path) -> None:
    proj, _ = _build_store(tmp_path / "e.tether", _step_trace(3, 30), _step_trace(3, 30))
    calls: list = []
    # ELBO peaks at nstates=3
    runner = _make_runner({1: -10.0, 2: -5.0, 3: -2.0, 4: -3.0}, calls)

    stored = idealize_molecules(proj, nstates=None, nstates_grid=(1, 2, 3, 4), _runner=runner)

    assert [c["nstates"] for c in calls] == [1, 2, 3, 4]  # swept the whole grid
    assert stored.nstates == 3
    assert stored.nstates_selected_by == "max-elbo"
    assert stored.elbo == -2.0
    assert stored.elbo_by_nstates == {1: -10.0, 2: -5.0, 3: -2.0, 4: -3.0}
    assert read_idealization(proj, "vbconhmm").elbo_by_nstates == stored.elbo_by_nstates


def test_fixed_nstates_runs_once(tmp_path) -> None:
    proj, _ = _build_store(tmp_path / "e.tether", _step_trace(2, 20), _step_trace(2, 20))
    calls: list = []
    stored = idealize_molecules(proj, nstates=2, _runner=_make_runner({2: -1.0}, calls))
    assert [c["nstates"] for c in calls] == [2]
    assert stored.nstates_selected_by == "fixed"
    assert stored.elbo_by_nstates is None


# --- selection ---------------------------------------------------------------


def test_subset_selection_by_key_preserves_request_order(tmp_path) -> None:
    n, t = 3, 24
    proj, keys = _build_store(tmp_path / "e.tether", _step_trace(n, t), _step_trace(n, t))
    calls: list = []
    requested = [keys[2], keys[0]]  # reversed subset
    stored = idealize_molecules(proj, requested, nstates=2, _runner=_make_runner({2: -1.0}, calls))

    assert stored.molecule_keys == requested
    smd = calls[-1]["smd"]
    assert smd.n_molecules == 2
    assert smd.molecule_keys == requested  # SMD carries the requested selection order


def test_missing_key_raises(tmp_path) -> None:
    proj, _ = _build_store(tmp_path / "e.tether", _step_trace(1, 12), _step_trace(1, 12))
    with pytest.raises(KeyError, match="no molecule with molecule_key"):
        idealize_molecules(proj, ["not-a-real-key"], nstates=2, _runner=_make_runner({2: 0.0}, []))


def test_unknown_quantity_raises(tmp_path) -> None:
    proj, _ = _build_store(tmp_path / "e.tether", _step_trace(1, 12), _step_trace(1, 12))
    with pytest.raises(ValueError, match="intensity_quantity"):
        idealize_molecules(
            proj, intensity_quantity="bogus", nstates=2, _runner=_make_runner({2: 0.0}, [])
        )


def test_uses_raw_quantity_when_requested(tmp_path) -> None:
    n, t = 2, 18
    donor_corr = _step_trace(n, t)
    proj, keys = _build_store(tmp_path / "e.tether", donor_corr, donor_corr * 0.5)
    calls: list = []
    stored = idealize_molecules(
        proj, intensity_quantity="raw", nstates=2, _runner=_make_runner({2: -1.0}, calls)
    )
    # raw = corrected + 100 bg (see _integrated); the SMD donor channel must be the RAW trace
    smd = calls[-1]["smd"]
    np.testing.assert_allclose(smd.raw[0, :, 0], donor_corr[0] + 100.0)
    assert stored.intensity_quantity == "raw"


# --- overwrite guard ---------------------------------------------------------


def test_refuses_overwrite_then_replaces(tmp_path) -> None:
    proj, _ = _build_store(tmp_path / "e.tether", _step_trace(2, 20), _step_trace(2, 20))
    idealize_molecules(proj, nstates=2, _runner=_make_runner({2: -5.0}, []))
    with pytest.raises(FileExistsError, match="already exists"):
        idealize_molecules(proj, nstates=2, _runner=_make_runner({2: -9.0}, []))
    stored = idealize_molecules(
        proj, nstates=3, overwrite=True, _runner=_make_runner({3: -9.0}, [])
    )
    assert stored.nstates == 3
    assert read_idealization(proj, "vbconhmm").nstates == 3
    # the stage-then-swap leaves no transient group behind
    assert list_idealizations(proj) == ["vbconhmm"]
    with h5py.File(proj.path, "r") as f:
        assert list(f["idealization"].keys()) == ["vbconhmm"]


def test_distinct_model_names_coexist(tmp_path) -> None:
    proj, _ = _build_store(tmp_path / "e.tether", _step_trace(2, 20), _step_trace(2, 20))
    idealize_molecules(proj, model_name="vbfret", nstates=2, _runner=_make_runner({2: -5.0}, []))
    idealize_molecules(proj, model_name="consensus", nstates=3, _runner=_make_runner({3: -4.0}, []))
    assert list_idealizations(proj) == ["consensus", "vbfret"]


# --- analysis window ---------------------------------------------------------


def test_analysis_window_bounds_the_fit_and_hash(tmp_path) -> None:
    n, t = 1, 30
    donor = _step_trace(n, t)
    acceptor = _step_trace(n, t) * 0.6
    path = tmp_path / "e.tether"
    proj, keys = _build_store(path, donor, acceptor)
    # trim the analysis window to [5, 20)
    lo, hi = 5, 20
    with h5py.File(path, "r+") as f:  # read-modify-write the compound table
        table = f["molecules"]["table"][:]
        table["analysis_window"][0] = (lo, hi)
        f["molecules"]["table"][:] = table
    calls: list = []
    stored = idealize_molecules(proj, nstates=2, _runner=_make_runner({2: -1.0}, calls))

    smd = calls[-1]["smd"]
    assert int(smd.pre_list[0]) == lo
    assert int(smd.post_list[0]) == hi
    # the state path is NO_STATE outside the window and defined inside
    assert np.all(stored.state_paths[0, :lo] == NO_STATE)
    assert np.all(stored.state_paths[0, hi:] == NO_STATE)
    assert np.all(stored.state_paths[0, lo:hi] != NO_STATE)
    # the input hash covers only the windowed slice
    assert stored.input_hashes[0] == input_trace_hash(
        donor[0, lo:hi], acceptor[0, lo:hi], "corrected"
    )


# --- staleness ---------------------------------------------------------------


def test_stale_keys_flag_changed_inputs(tmp_path) -> None:
    n, t = 3, 24
    path = tmp_path / "e.tether"
    proj, keys = _build_store(path, _step_trace(n, t), _step_trace(n, t) * 0.5)
    idealize_molecules(proj, nstates=2, _runner=_make_runner({2: -1.0}, []))
    assert stale_molecule_keys(proj, "vbconhmm") == []  # nothing changed yet

    # mutate molecule row 0's corrected donor trace in place -> its input diverges
    with h5py.File(path, "r+") as f:
        f["traces"]["donor_corrected"][0, :] += 500.0
    stale = stale_molecule_keys(proj, "vbconhmm")
    assert stale == [keys[0]]


# --- schema freeze -----------------------------------------------------------


def test_writing_idealization_is_additive_only(tmp_path) -> None:
    path = tmp_path / "e.tether"
    proj, _ = _build_store(path, _step_trace(2, 20), _step_trace(2, 20))
    idealize_molecules(proj, nstates=2, _runner=_make_runner({2: -1.0}, []))
    with h5py.File(path, "r") as f:
        current = introspect(f)
    # the frozen skeleton is intact; the model subgroup is additive data only
    assert diff_manifest(build_manifest(), current) == []


# --- empty store -------------------------------------------------------------


def test_empty_store_raises(tmp_path) -> None:
    path = tmp_path / "empty.tether"
    create_project(path, overwrite=True)
    with pytest.raises(ValueError, match="no extracted molecules"):
        idealize_molecules(path, nstates=2, _runner=_make_runner({2: 0.0}, []))


# --- Project delegators ------------------------------------------------------


def test_project_delegators(tmp_path) -> None:
    proj, keys = _build_store(tmp_path / "e.tether", _step_trace(2, 20), _step_trace(2, 20))
    stored = proj.idealize(nstates=2, _runner=_make_runner({2: -3.0}, []))
    assert stored.nstates == 2
    assert proj.list_idealizations() == ["vbconhmm"]
    assert proj.read_idealization("vbconhmm").molecule_keys == keys
    assert proj.stale_idealization_keys("vbconhmm") == []


# --- unique molecule_id join + multistate round-trip -------------------------


def test_molecule_ids_are_unique_and_round_trip(tmp_path) -> None:
    path = tmp_path / "e.tether"
    proj, keys = _build_store(path, _step_trace(3, 20), _step_trace(3, 20))
    stored = idealize_molecules(proj, nstates=2, _runner=_make_runner({2: -1.0}, []))
    store_ids = [_to_str(x) for x in read_molecules(path)["molecule_id"]]
    assert stored.molecule_ids == store_ids
    assert len(set(stored.molecule_ids)) == len(stored.molecule_ids)  # unique
    assert read_idealization(proj, "vbconhmm").molecule_ids == store_ids


def _to_str(v) -> str:
    return v.decode() if isinstance(v, bytes) else str(v)


def test_multistate_state_path_round_trips(tmp_path) -> None:
    proj, _ = _build_store(tmp_path / "e.tether", _step_trace(2, 30), _step_trace(2, 30))
    stored = idealize_molecules(
        proj, nstates=2, _runner=_make_runner({2: -1.0}, [], multistate=True)
    )
    in_window = stored.state_paths[stored.state_paths != NO_STATE]
    assert len(np.unique(in_window)) >= 2  # a genuine 2-state path, not a flat line
    np.testing.assert_array_equal(
        read_idealization(proj, "vbconhmm").state_paths, stored.state_paths
    )


# --- analysis-window unset fallback ------------------------------------------


def test_windows_fallback_to_frame_range_when_unset(tmp_path) -> None:
    n, t = 1, 24
    donor, acceptor = _step_trace(n, t), _step_trace(n, t) * 0.6
    path = tmp_path / "e.tether"
    proj, _ = _build_store(path, donor, acceptor)
    with h5py.File(path, "r+") as f:  # zero the analysis window -> "unset"
        table = f["molecules"]["table"][:]
        table["analysis_window"][0] = (0, 0)
        f["molecules"]["table"][:] = table
        frame_range = tuple(int(v) for v in table["frame_range"][0])
    calls: list = []
    stored = idealize_molecules(proj, nstates=2, _runner=_make_runner({2: -1.0}, calls))
    smd = calls[-1]["smd"]
    assert (int(smd.pre_list[0]), int(smd.post_list[0])) == frame_range
    lo, hi = frame_range
    assert stored.input_hashes[0] == input_trace_hash(
        donor[0, lo:hi], acceptor[0, lo:hi], "corrected"
    )


# --- staleness: removed molecule + duplicate molecule_key --------------------


def test_stale_reports_removed_molecule(tmp_path) -> None:
    n, t = 3, 20
    path = tmp_path / "e.tether"
    proj, keys = _build_store(path, _step_trace(n, t), _step_trace(n, t))
    idealize_molecules(proj, nstates=2, _runner=_make_runner({2: -1.0}, []))
    with h5py.File(path, "r+") as f:  # drop the last molecule row from /molecules
        f["molecules"]["table"].resize((n - 1,))
    assert stale_molecule_keys(proj, "vbconhmm") == [keys[-1]]


def test_duplicate_molecule_key_staleness_is_per_row(tmp_path) -> None:
    # Two donor coords in one 0.1 px quantum bin -> a SHARED molecule_key, but each
    # row keeps a unique molecule_id. Distinct traces so the input hashes differ.
    coords = np.array([[20.0, 20.0], [20.03, 20.0]])
    t = 24
    donor = np.stack([_step_trace(1, t)[0], _step_trace(1, t, low=300.0, high=900.0)[0]])
    acceptor = donor * 0.5
    path = tmp_path / "e.tether"
    proj, keys = _build_store(path, donor, acceptor, coords=coords)
    assert keys[0] == keys[1]  # the §7.10 shared-key case
    ids = [_to_str(x) for x in read_molecules(path)["molecule_id"]]
    assert ids[0] != ids[1]  # but unique ids

    idealize_molecules(proj, nstates=2, _runner=_make_runner({2: -1.0}, []))
    # nothing changed -> no spurious stale from collapsing the shared key
    assert stale_molecule_keys(proj, "vbconhmm") == []
    # change only ROW 1's trace -> the shared key is reported (row-1 change caught,
    # not masked by row 0 as a molecule_key join would)
    with h5py.File(path, "r+") as f:
        f["traces"]["donor_corrected"][1, :] += 500.0
    assert stale_molecule_keys(proj, "vbconhmm") == [keys[0]]


# --- ELBO edge cases: NaN, all-infinite, ties, empty grid --------------------


def test_auto_nstates_ignores_nonfinite_elbo(tmp_path) -> None:
    proj, _ = _build_store(tmp_path / "e.tether", _step_trace(2, 20), _step_trace(2, 20))
    # nstates=1 reports NaN (a degenerate fit); it must never win over finite fits.
    runner = _make_runner({1: float("nan"), 2: -5.0, 3: -2.0}, [])
    stored = idealize_molecules(proj, nstates=None, nstates_grid=(1, 2, 3), _runner=runner)
    assert stored.nstates == 3
    assert stored.elbo == -2.0
    assert stored.elbo_by_nstates[1] == float("-inf")  # NaN normalized to the sentinel
    assert read_idealization(proj, "vbconhmm").elbo_by_nstates[1] == float("-inf")


def test_all_infinite_elbo_prefers_smallest_nstates(tmp_path) -> None:
    proj, _ = _build_store(tmp_path / "e.tether", _step_trace(2, 20), _step_trace(2, 20))
    runner = _make_runner({2: None, 3: None, 4: None}, [])  # every fit reports no ELBO
    stored = idealize_molecules(proj, nstates=None, nstates_grid=(2, 3, 4), _runner=runner)
    assert stored.nstates == 2  # parsimony: smallest state count when nothing is finite


def test_tie_break_prefers_smaller_nstates(tmp_path) -> None:
    proj, _ = _build_store(tmp_path / "e.tether", _step_trace(2, 20), _step_trace(2, 20))
    runner = _make_runner({2: -5.0, 3: -5.0}, [])  # equal ELBO
    stored = idealize_molecules(proj, nstates=None, nstates_grid=(2, 3), _runner=runner)
    assert stored.nstates == 2


def test_empty_nstates_grid_raises(tmp_path) -> None:
    proj, _ = _build_store(tmp_path / "e.tether", _step_trace(1, 12), _step_trace(1, 12))
    with pytest.raises(ValueError, match="nstates_grid must be non-empty"):
        idealize_molecules(proj, nstates=None, nstates_grid=(), _runner=_make_runner({}, []))


def test_degenerate_fit_raises(tmp_path) -> None:
    proj, _ = _build_store(tmp_path / "e.tether", _step_trace(1, 12), _step_trace(1, 12))
    runner = _make_runner({2: -1.0}, [], degenerate=True)
    with pytest.raises(ValueError, match="no state path"):
        idealize_molecules(proj, nstates=2, _runner=runner)
    assert list_idealizations(proj) == []  # nothing persisted


# NOTE: a *live* store-integrated sidecar test is intentionally not added here.
# `tether.project.idealize` is a base-env module (Python >= 3.11; it uses
# `datetime.UTC`) that spawns the sidecar as a subprocess; it cannot import in the
# isolated sidecar interpreter (older Python) that `sidecar.yml` collects
# `test_*sidecar*.py` under, and the base `test` matrix has no sidecar interpreter.
# The raw fit's §11.2 parity is already gated by `test_parity_sidecar` at the
# `run_vbfret` layer, and the store-integration is fully covered above (faked
# sidecar). See ADR-0024.
