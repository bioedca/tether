# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Store-integrated one-click idealization (M2 S6, PR-A; FR-IDEALIZE; ADR-0024).

Locks the headless half of "one-click vbFRET from the dock": read selected
molecules' traces from a ``.tether`` -> SMD -> (fake or live) vbFRET -> write
``/idealization/{model}`` back as **additive data** with a per-molecule
input-provenance hash. The sidecar is faked in the default matrix (a canned
:class:`~tether.idealize.IdealizationResult`); the single live end-to-end leg is
``@pytest.mark.sidecar`` (deselected from CI).
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
) -> tuple[Project, list[str]]:
    """Write a ``.tether`` with controlled donor/acceptor *corrected* traces.

    Bypasses the movie-integration pipeline: builds :class:`MoleculeTraces`
    directly so the ``/traces/{donor,acceptor}_corrected`` values (the fit input)
    are exactly ``donor_intensity`` / ``acceptor_intensity``.
    """
    donor_intensity = np.asarray(donor_intensity, dtype="float64")
    acceptor_intensity = np.asarray(acceptor_intensity, dtype="float64")
    n, t = donor_intensity.shape
    coords = _distinct_coords(n)
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


def _fake_result(smd_path, *, nstates: int, elbo: float, model_type: str) -> IdealizationResult:
    """A canned model whose ``idealized`` aligns to the SMD (NaN outside window)."""
    smd = read_smd(smd_path)
    n, t = smd.n_molecules, smd.n_frames
    means = np.linspace(0.2, 0.8, nstates) if nstates > 1 else np.array([0.5])
    idealized = np.full((n, t), np.nan)
    pre = smd.pre_list if smd.pre_list is not None else np.zeros(n, dtype=int)
    post = smd.post_list if smd.post_list is not None else np.full(n, t, dtype=int)
    for i in range(n):
        idealized[i, int(pre[i]) : int(post[i])] = means[0]
    model = StateModel(
        model_type=model_type,
        nstates=nstates,
        means=means,
        variances=np.full(nstates, 0.01),
        tmatrix=np.eye(nstates),
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


def _make_runner(elbo_by_nstates: dict[int, float], calls: list):
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
    with h5py.File(path, "r+") as f:
        f["molecules"]["table"]["analysis_window"][0] = (lo, hi)
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


# --- live end-to-end (sidecar; deselected from the CI matrix) -----------------

import os  # noqa: E402

_SIDECAR = os.environ.get("TETHER_SIDECAR_PYTHON")
requires_sidecar = pytest.mark.skipif(
    not _SIDECAR, reason="set TETHER_SIDECAR_PYTHON to a tMAVEN sidecar interpreter"
)


def _two_state_channels(n: int, t: int) -> tuple[np.ndarray, np.ndarray]:
    """Clean 2-state donor/acceptor: apparent E steps 0.25 -> 0.75 mid-trace.

    A small deterministic ripple keeps the VB fit from a degenerate single-state
    collapse without introducing RNG nondeterminism.
    """
    total = 1000.0
    e = np.concatenate([np.full(t // 2, 0.25), np.full(t - t // 2, 0.75)])
    ripple = 15.0 * np.sin(np.arange(t) * 0.7)
    donor = np.empty((n, t))
    acceptor = np.empty((n, t))
    for i in range(n):
        acceptor[i] = e * total + ripple * (1 + 0.1 * i)
        donor[i] = (1.0 - e) * total - ripple * (1 + 0.1 * i)
    return donor, acceptor


@pytest.mark.sidecar
@requires_sidecar
def test_live_store_integrated_idealize_writes_well_formed_model(tmp_path) -> None:
    """The real store->SMD->sidecar->store path runs live and writes a valid model.

    The unique new surface is the store integration; the raw fit's §11.2 parity is
    already gated by ``test_parity_sidecar`` at the ``run_vbfret`` layer, so this is
    a fast single-fit smoke on a tiny synthetic 2-state input (small ``nrestarts``).
    """
    n, t = 2, 40
    donor, acceptor = _two_state_channels(n, t)
    proj, keys = _build_store(tmp_path / "e.tether", donor, acceptor, sha="5" * 64)

    stored = idealize_molecules(proj, nstates=2, nrestarts=2, timeout=600.0)

    assert stored.elbo is not None and np.isfinite(stored.elbo)
    assert stored.nstates == 2
    assert stored.idealized.shape == (n, t)
    assert stored.molecule_keys == keys
    assert list_idealizations(proj) == ["vbconhmm"]
    # every in-window frame resolved to a finite level; the round-trip reads back
    finite = stored.idealized[np.isfinite(stored.idealized)]
    assert finite.size > 0
    assert read_idealization(proj, "vbconhmm").molecule_keys == keys
