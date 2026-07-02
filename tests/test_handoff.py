# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bidirectional tMAVEN hand-off + non-destructive return-leg re-import (M2 S7; FR-IDEALIZE).

Locks the headless core of "Hand to tMAVEN" (PLAN §6 S7, PRD §7.4/§5.3):

* the outbound SMD export (superset coords/ids + analysis windows ride along);
* the return leg's **exact intensity-trace matching** against the retained store —
  robust to reordered / subset returning SMDs, unmatched rows reported not guessed;
* the per-trace reconcile diff (analysis-window + class), and its **non-destructive**
  commit — an imported tMAVEN model lands as a *new* ``/idealization/{model}`` (rows
  remapped by the match, additive so ``schema-guard`` holds), an accepted window edit
  re-stales that molecule's dependent idealizations, and the ``class 0 ↔
  uncategorized`` leg applies while a non-zero class is surfaced as M4-deferred.

Wholly headless (no sidecar / Qt); the interactive reconcile dialog is the M2 S7
PR-B GUI follow-up.
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
from tether.idealize.smd import write_smd  # noqa: E402
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
from tether.project.handoff import (  # noqa: E402
    ClassChange,
    WindowChange,
    apply_reconcile,
    hand_off_to_tmaven,
    read_return_leg,
)
from tether.project.idealize import (  # noqa: E402
    idealize_molecules,
    list_idealizations,
    read_idealization,
    stale_molecule_keys,
)

_PARSED = parse_filename("Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif")
_WINDOW = 21


# --------------------------------------------------------------------------- #
# store builder (controlled trace values, no imaging pipeline)
# --------------------------------------------------------------------------- #
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
    path: Path,
    donor: np.ndarray,
    acceptor: np.ndarray,
    *,
    coords: np.ndarray | None = None,
) -> tuple[Project, list[str]]:
    """A ``.tether`` whose ``/traces/{donor,acceptor}_corrected`` equal the inputs."""
    donor = np.asarray(donor, dtype="float64")
    acceptor = np.asarray(acceptor, dtype="float64")
    n, t = donor.shape
    coords = _distinct_coords(n) if coords is None else np.asarray(coords, dtype="float64")
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
    """``(n, t)`` two-level step with a distinct switch frame per molecule (unique rows)."""
    out = np.full((n, t), low, dtype="float64")
    for i in range(n):
        out[i, (t // 3) + i :] = high
    return out


def _write_tmaven_model(
    path: Path,
    *,
    idealized: np.ndarray,
    means: np.ndarray,
    model_type: str,
    ran: np.ndarray | None = None,
):
    """A minimal Appendix-D.2 ``model`` file (what a tMAVEN return leg brings back).

    ``ran`` records the SMD indices tMAVEN actually fit (default: all rows); pass a
    subset to model a deselected-before-fit trace (its ``idealized`` row stays NaN).
    """
    idealized = np.asarray(idealized, dtype="float64")
    means = np.asarray(means, dtype="float64")
    ran = np.arange(idealized.shape[0]) if ran is None else np.asarray(ran)
    with h5py.File(path, "w") as f:
        g = f.create_group("model")
        g.attrs["type"] = model_type
        g.create_dataset("nstates", data=means.shape[0])
        g.create_dataset("mean", data=means)
        g.create_dataset("var", data=np.full(means.shape[0], 0.01))
        g.create_dataset("tmatrix", data=np.eye(means.shape[0]))
        g.create_dataset("norm_tmatrix", data=np.eye(means.shape[0]) * 0.9)
        g.create_dataset("idealized", data=idealized)
        g.create_dataset("likelihood", data=np.array([[1.0, 0, 0, 0, 0], [3.5, 0, 0, 0, 0]]))
        g.create_dataset("ran", data=np.asarray(ran, dtype="int64"))
        g.create_dataset("dtype", data="FRET")
    return path


def _model_idealized(smd_raw_len: int, n: int, means: np.ndarray) -> np.ndarray:
    """A canned per-frame idealized path (constant at ``means[0]`` over the window)."""
    out = np.full((n, smd_raw_len), np.nan)
    out[:, :] = means[0]
    return out


def _fake_idealizer(nstates: int = 2):
    """A fake ``run_vbfret`` for seeding a prior in-app model over the store windows."""

    def _run(smd_path, **_kwargs):
        smd = read_smd(smd_path)
        nm, t = smd.n_molecules, smd.n_frames
        means = np.linspace(0.2, 0.8, nstates)
        pre = smd.pre_list if smd.pre_list is not None else np.zeros(nm, dtype=int)
        post = smd.post_list if smd.post_list is not None else np.full(nm, t, dtype=int)
        idealized = np.full((nm, t), np.nan)
        for i in range(nm):
            idealized[i, int(pre[i]) : int(post[i])] = means[0]
        model = StateModel(
            model_type="vbconhmm",
            nstates=nstates,
            means=means,
            variances=np.full(nstates, 0.01),
            tmatrix=np.eye(nstates),
            norm_tmatrix=np.eye(nstates) * 0.9,
            elbo=-10.0,
            dtype="FRET",
            idealized=idealized,
            ran=np.arange(nm, dtype="int64"),
        )
        return IdealizationResult(
            model=model, state_paths={}, dwells=[], model_path=Path(smd_path), status={}
        )

    return _run


# --------------------------------------------------------------------------- #
# outbound leg
# --------------------------------------------------------------------------- #
def test_hand_off_writes_smd_with_superset_and_windows(tmp_path):
    proj, _keys = _build_store(tmp_path / "p.tether", _step_trace(4, 30), _step_trace(4, 30) * 0.5)
    out = tmp_path / "handoff.hdf5"
    manifest = hand_off_to_tmaven(proj, out_path=out)

    assert out.exists()
    assert manifest.n_molecules == 4
    smd = read_smd(out)
    assert smd.raw.shape == (4, 30, 2)
    # superset coords + identities ride along (Tether->Tether recoverable, §5.3)
    assert smd.molecule_ids == manifest.molecule_ids
    assert smd.molecule_keys == manifest.molecule_keys
    assert smd.donor_xy is not None and smd.donor_xy.shape == (4, 2)
    # analysis windows ride along; classes neutral (uncategorized) at M2
    assert smd.pre_list is not None and smd.post_list is not None
    np.testing.assert_array_equal(smd.classes, np.zeros(4, dtype="int64"))
    # SMD raw == the store's corrected traces (donor, acceptor)
    np.testing.assert_allclose(smd.raw[:, :, 0], _step_trace(4, 30))


def test_hand_off_subset_in_order(tmp_path):
    proj, keys = _build_store(tmp_path / "p.tether", _step_trace(5, 20), _step_trace(5, 20) * 0.5)
    out = tmp_path / "sub.hdf5"
    manifest = hand_off_to_tmaven(proj, [keys[3], keys[1]], out_path=out)
    assert manifest.molecule_keys == [keys[3], keys[1]]
    assert read_smd(out).raw.shape[0] == 2


def test_hand_off_empty_store_raises(tmp_path):
    p = tmp_path / "empty.tether"
    create_project(p, overwrite=True)
    with pytest.raises(ValueError, match="no extracted molecules"):
        hand_off_to_tmaven(Project.open(p), out_path=tmp_path / "o.hdf5")


def test_hand_off_unknown_quantity_raises(tmp_path):
    proj, _ = _build_store(tmp_path / "p.tether", _step_trace(2, 10), _step_trace(2, 10))
    with pytest.raises(ValueError, match="intensity_quantity"):
        hand_off_to_tmaven(proj, out_path=tmp_path / "o.hdf5", intensity_quantity="bogus")


# --------------------------------------------------------------------------- #
# return leg — matching
# --------------------------------------------------------------------------- #
def test_return_leg_matches_reordered(tmp_path):
    donor, acceptor = _step_trace(4, 30), _step_trace(4, 30) * 0.5
    proj, _keys = _build_store(tmp_path / "p.tether", donor, acceptor)
    # a returning SMD in a scrambled order (coords/ids intentionally absent as a hint)
    perm = [2, 0, 3, 1]
    raw = np.stack([donor[perm], acceptor[perm]], axis=-1)
    ret = tmp_path / "ret.hdf5"
    write_smd(ret, raw, overwrite=True)

    report = read_return_leg(proj, ret)
    assert report.all_matched and report.n_matched == 4
    # returning row i resolves to the correct store row (== perm[i]) purely by intensity
    for tr in report.matched:
        assert tr.store_row == perm[tr.returned_index]


def test_return_leg_subset_and_unmatched(tmp_path):
    donor, acceptor = _step_trace(4, 30), _step_trace(4, 30) * 0.5
    proj, _keys = _build_store(tmp_path / "p.tether", donor, acceptor)
    # returning: two real store traces + one foreign trace that is in no store row
    foreign = np.full((1, 30), 111.0)
    raw = np.stack(
        [np.concatenate([donor[[1, 3]], foreign]), np.concatenate([acceptor[[1, 3]], foreign])],
        axis=-1,
    )
    ret = tmp_path / "ret.hdf5"
    write_smd(ret, raw, overwrite=True)

    report = read_return_leg(proj, ret)
    assert report.n_matched == 2
    assert report.unmatched_returned == [2]  # the foreign row, reported not guessed
    assert {tr.store_row for tr in report.matched} == {1, 3}


# --------------------------------------------------------------------------- #
# return leg — reconcile diff
# --------------------------------------------------------------------------- #
def _returning_smd(path, donor, acceptor, *, classes=None, pre=None, post=None, ids=None):
    raw = np.stack([np.asarray(donor), np.asarray(acceptor)], axis=-1)
    write_smd(
        path, raw, classes=classes, pre_list=pre, post_list=post, molecule_ids=ids, overwrite=True
    )
    return path


def test_reconcile_surfaces_window_edit(tmp_path):
    donor, acceptor = _step_trace(3, 40), _step_trace(3, 40) * 0.5
    proj, _keys = _build_store(tmp_path / "p.tether", donor, acceptor)
    n_frames = 40
    pre = np.zeros(3, dtype="int64")
    post = np.full(3, n_frames, dtype="int64")
    post[1] = 25  # edited a leading-frame trim on molecule 1 only
    ret = _returning_smd(tmp_path / "ret.hdf5", donor, acceptor, pre=pre, post=post)

    report = read_return_leg(proj, ret)
    changed = report.window_changes
    assert len(changed) == 1
    tr = changed[0]
    assert tr.store_row == 1
    assert tr.window_change == WindowChange(old=(0, n_frames), new=(0, 25))
    # the untouched traces carry no window change
    assert all(t.window_change is None for t in report.matched if t.store_row != 1)


def test_reconcile_class_zero_to_uncategorized(tmp_path):
    donor, acceptor = _step_trace(2, 20), _step_trace(2, 20) * 0.5
    proj, keys = _build_store(tmp_path / "p.tether", donor, acceptor)
    # give store molecule 0 a category so a returning class 0 is a real clear-to-uncategorized
    _set_category(proj.path, row=0, value="good")
    ret = _returning_smd(tmp_path / "ret.hdf5", donor, acceptor, classes=np.zeros(2, dtype="int64"))

    report = read_return_leg(proj, ret)
    by_row = {t.store_row: t for t in report.matched}
    assert by_row[0].class_change == ClassChange(0, "good", proposed_category="", applicable=True)
    assert by_row[1].class_change is None  # already uncategorized -> no change


def test_reconcile_nonzero_class_is_m4_deferred(tmp_path):
    donor, acceptor = _step_trace(2, 20), _step_trace(2, 20) * 0.5
    proj, _keys = _build_store(tmp_path / "p.tether", donor, acceptor)
    ret = _returning_smd(tmp_path / "ret.hdf5", donor, acceptor, classes=np.array([2, 0]))

    report = read_return_leg(proj, ret)
    by_row = {t.store_row: t for t in report.matched}
    cc = by_row[0].class_change
    assert cc is not None and cc.returned_class == 2
    assert cc.applicable is False and cc.proposed_category is None


def _set_category(path, *, row: int, value: str) -> None:
    from tether.io.schema import TABLE

    with h5py.File(path, "r+") as f:
        table = f["molecules"][TABLE]
        rec = table[row]
        rec["category"] = value
        table[row] = rec


# --------------------------------------------------------------------------- #
# return leg — apply (window / class)
# --------------------------------------------------------------------------- #
def test_apply_window_edit_updates_store_and_restales(tmp_path):
    donor, acceptor = _step_trace(3, 40), _step_trace(3, 40) * 0.5
    proj, keys = _build_store(tmp_path / "p.tether", donor, acceptor)
    # a prior in-app idealization over the ORIGINAL windows (fake sidecar)
    idealize_molecules(proj, model_name="prior", nstates=2, _runner=_fake_idealizer())
    assert stale_molecule_keys(proj, "prior") == []  # fresh: nothing stale

    pre = np.zeros(3, dtype="int64")
    post = np.full(3, 40, dtype="int64")
    post[1] = 25
    ret = _returning_smd(tmp_path / "ret.hdf5", donor, acceptor, pre=pre, post=post)

    applied = apply_reconcile(proj, ret, accept_windows=True)
    assert applied.windows_applied  # molecule 1's id
    # store window is now the edited one
    aw = read_molecules(proj.path)["analysis_window"][1]
    np.testing.assert_array_equal(aw, [0, 25])
    # the accepted window edit re-stales that molecule's prior idealization (§5.1)
    assert keys[1] in applied.stale_after
    assert keys[1] in stale_molecule_keys(proj, "prior")
    assert keys[0] not in stale_molecule_keys(proj, "prior")


def test_apply_window_default_no_change(tmp_path):
    donor, acceptor = _step_trace(2, 30), _step_trace(2, 30) * 0.5
    proj, _keys = _build_store(tmp_path / "p.tether", donor, acceptor)
    post = np.full(2, 30, dtype="int64")
    post[0] = 15
    ret = _returning_smd(
        tmp_path / "ret.hdf5", donor, acceptor, pre=np.zeros(2, "int64"), post=post
    )

    applied = apply_reconcile(proj, ret)  # accept nothing by default
    assert applied.windows_applied == []
    np.testing.assert_array_equal(read_molecules(proj.path)["analysis_window"][0], [0, 30])


def test_apply_class_zero_clears_category(tmp_path):
    donor, acceptor = _step_trace(2, 20), _step_trace(2, 20) * 0.5
    proj, _keys = _build_store(tmp_path / "p.tether", donor, acceptor)
    _set_category(proj.path, row=0, value="good")
    ret = _returning_smd(tmp_path / "ret.hdf5", donor, acceptor, classes=np.zeros(2, "int64"))

    applied = apply_reconcile(proj, ret, accept_classes=True)
    assert len(applied.classes_applied) == 1
    cats = [
        c.decode() if isinstance(c, bytes) else str(c)
        for c in read_molecules(proj.path)["category"]
    ]
    assert cats[0] == ""  # cleared to uncategorized


def test_apply_nonzero_class_is_deferred_not_written(tmp_path):
    donor, acceptor = _step_trace(2, 20), _step_trace(2, 20) * 0.5
    proj, _keys = _build_store(tmp_path / "p.tether", donor, acceptor)
    _set_category(proj.path, row=0, value="orig")
    ret = _returning_smd(tmp_path / "ret.hdf5", donor, acceptor, classes=np.array([2, 0]))

    applied = apply_reconcile(proj, ret, accept_classes=True)
    assert applied.classes_deferred  # molecule 0's id: needs the M4 lookup
    assert applied.classes_applied == []
    cats = [
        c.decode() if isinstance(c, bytes) else str(c)
        for c in read_molecules(proj.path)["category"]
    ]
    assert cats[0] == "orig"  # untouched (no free-text mapping at M2)


# --------------------------------------------------------------------------- #
# return leg — non-destructive model import
# --------------------------------------------------------------------------- #
def test_import_model_writes_new_idealization(tmp_path):
    donor, acceptor = _step_trace(3, 30), _step_trace(3, 30) * 0.5
    proj, keys = _build_store(tmp_path / "p.tether", donor, acceptor)
    ret = _returning_smd(tmp_path / "ret.hdf5", donor, acceptor)
    means = np.array([0.3, 0.7])
    model = _write_tmaven_model(
        tmp_path / "model.hdf5",
        idealized=_model_idealized(30, 3, means),
        means=means,
        model_type="vb Consensus HMM",
    )

    applied = apply_reconcile(
        proj, ret, model_path=model, model_name="tmaven-import", import_idealization=True
    )
    assert applied.idealization_written == "tmaven-import"
    assert "tmaven-import" in list_idealizations(proj)
    stored = read_idealization(proj, "tmaven-import")
    assert stored.nstates == 2
    assert stored.nstates_selected_by == "imported"
    assert set(stored.molecule_keys) == set(keys)
    # return-leg provenance stamped on disk (§NFR-REPRO)
    with h5py.File(proj.path, "r") as f:
        g = f["idealization"]["tmaven-import"]
        assert g.attrs["source_model"] == "model.hdf5"
        assert int(g.attrs["reconcile_matched"]) == 3


def test_import_model_remaps_reordered_rows(tmp_path):
    donor, acceptor = _step_trace(3, 30), _step_trace(3, 30) * 0.5
    proj, keys = _build_store(tmp_path / "p.tether", donor, acceptor)
    perm = [2, 0, 1]
    raw = np.stack([donor[perm], acceptor[perm]], axis=-1)
    ret = tmp_path / "ret.hdf5"
    write_smd(ret, raw, overwrite=True)
    # a per-row-distinct idealized level so we can verify the remap by value
    means = np.array([0.10, 0.20, 0.30, 0.40])
    idealized = np.full((3, 30), np.nan)
    for i in range(3):
        idealized[i, :] = 0.10 * (i + 1)  # returning row i -> level 0.1*(i+1)
    model = _write_tmaven_model(
        tmp_path / "model.hdf5", idealized=idealized, means=means, model_type="vbFRET"
    )

    apply_reconcile(proj, ret, model_path=model, model_name="imp", import_idealization=True)
    stored = read_idealization(proj, "imp")
    key_to_level = dict(zip(stored.molecule_keys, stored.idealized[:, 0], strict=True))
    # store row perm[i] carries returning row i's level -> level 0.1*(i+1)
    for i, store_row in enumerate(perm):
        assert key_to_level[keys[store_row]] == pytest.approx(0.10 * (i + 1))


def test_import_model_non_destructive_refuses_clobber(tmp_path):
    donor, acceptor = _step_trace(2, 20), _step_trace(2, 20) * 0.5
    proj, _keys = _build_store(tmp_path / "p.tether", donor, acceptor)
    ret = _returning_smd(tmp_path / "ret.hdf5", donor, acceptor)
    means = np.array([0.5])
    model = _write_tmaven_model(
        tmp_path / "m.hdf5", idealized=_model_idealized(20, 2, means), means=means, model_type="vb"
    )
    apply_reconcile(proj, ret, model_path=model, model_name="dup", import_idealization=True)
    with pytest.raises(FileExistsError, match="dup"):
        apply_reconcile(proj, ret, model_path=model, model_name="dup", import_idealization=True)
    # overwrite=True replaces it
    apply_reconcile(
        proj, ret, model_path=model, model_name="dup", import_idealization=True, overwrite=True
    )
    assert list_idealizations(proj) == ["dup"]


def test_import_model_drops_unmatched(tmp_path):
    donor, acceptor = _step_trace(3, 30), _step_trace(3, 30) * 0.5
    proj, keys = _build_store(tmp_path / "p.tether", donor, acceptor)
    # returning: 2 real store traces + 1 foreign -> only 2 import, foreign reported
    foreign = np.full((1, 30), 55.0)
    raw = np.stack(
        [np.concatenate([donor[[0, 2]], foreign]), np.concatenate([acceptor[[0, 2]], foreign])],
        axis=-1,
    )
    ret = tmp_path / "ret.hdf5"
    write_smd(ret, raw, overwrite=True)
    means = np.array([0.5])
    model = _write_tmaven_model(
        tmp_path / "m.hdf5", idealized=_model_idealized(30, 3, means), means=means, model_type="vb"
    )
    apply_reconcile(proj, ret, model_path=model, model_name="imp", import_idealization=True)
    stored = read_idealization(proj, "imp")
    assert set(stored.molecule_keys) == {keys[0], keys[2]}


def test_import_model_without_idealized_raises(tmp_path):
    donor, acceptor = _step_trace(2, 20), _step_trace(2, 20) * 0.5
    proj, _keys = _build_store(tmp_path / "p.tether", donor, acceptor)
    ret = _returning_smd(tmp_path / "ret.hdf5", donor, acceptor)
    bad = tmp_path / "bad.hdf5"
    with h5py.File(bad, "w") as f:
        g = f.create_group("model")
        g.create_dataset("mean", data=np.array([0.5]))  # no 'idealized'
    with pytest.raises(ValueError, match="idealized"):
        apply_reconcile(proj, ret, model_path=bad, model_name="x", import_idealization=True)


def test_import_drops_traces_outside_ran(tmp_path):
    """A matched-but-not-fit trace (outside `ran`) is dropped + reported, not written NaN."""
    donor, acceptor = _step_trace(3, 30), _step_trace(3, 30) * 0.5
    proj, keys = _build_store(tmp_path / "p.tether", donor, acceptor)
    ret = _returning_smd(tmp_path / "ret.hdf5", donor, acceptor)
    means = np.array([0.5])
    idealized = _model_idealized(30, 3, means)
    idealized[1, :] = np.nan  # tMAVEN deselected molecule 1 before fitting
    model = _write_tmaven_model(
        tmp_path / "m.hdf5", idealized=idealized, means=means, model_type="vb", ran=[0, 2]
    )

    applied = apply_reconcile(
        proj, ret, model_path=model, model_name="imp", import_idealization=True
    )
    # molecule 1 dropped + reported, NOT written as an all-NaN idealization
    assert applied.import_unfit_dropped == [keys[1]]
    stored = read_idealization(proj, "imp")
    assert set(stored.molecule_keys) == {keys[0], keys[2]}
    assert keys[1] not in stored.molecule_keys
    with h5py.File(proj.path, "r") as f:
        g = f["idealization"]["imp"]
        assert int(g.attrs["reconcile_imported"]) == 2
        assert int(g.attrs["reconcile_unfit_dropped"]) == 1


def test_import_all_unfit_raises(tmp_path):
    donor, acceptor = _step_trace(2, 20), _step_trace(2, 20) * 0.5
    proj, _keys = _build_store(tmp_path / "p.tether", donor, acceptor)
    ret = _returning_smd(tmp_path / "ret.hdf5", donor, acceptor)
    means = np.array([0.5])
    idealized = np.full((2, 20), np.nan)  # nothing fit
    model = _write_tmaven_model(
        tmp_path / "m.hdf5", idealized=idealized, means=means, model_type="vb", ran=[]
    )
    with pytest.raises(ValueError, match="fit by the model"):
        apply_reconcile(proj, ret, model_path=model, model_name="imp", import_idealization=True)


def test_import_degenerate_returning_window_not_false_stale(tmp_path):
    """A degenerate returning window hashes over the same frames staleness uses (no false stale)."""
    donor, acceptor = _step_trace(3, 30), _step_trace(3, 30) * 0.5
    proj, keys = _build_store(tmp_path / "p.tether", donor, acceptor)
    pre = np.zeros(3, dtype="int64")
    post = np.full(3, 30, dtype="int64")
    post[1] = 0  # molecule 1: degenerate window (post <= pre)
    ret = _returning_smd(tmp_path / "ret.hdf5", donor, acceptor, pre=pre, post=post)
    means = np.array([0.5])
    model = _write_tmaven_model(
        tmp_path / "m.hdf5", idealized=_model_idealized(30, 3, means), means=means, model_type="vb"
    )
    # do NOT accept the degenerate window: store window stays full (0, 30)
    apply_reconcile(proj, ret, model_path=model, model_name="imp", import_idealization=True)
    # the imported model's hash for molecule 1 fell back to frame_range, matching the
    # store window recompute -> not falsely stale
    assert stale_molecule_keys(proj, "imp") == []
    assert keys  # sanity


def test_apply_classes_generator_spec_not_exhausted(tmp_path):
    """A single-use generator accept-spec is materialized once (deferred split intact)."""
    donor, acceptor = _step_trace(2, 20), _step_trace(2, 20) * 0.5
    proj, _keys = _build_store(tmp_path / "p.tether", donor, acceptor)
    ret = _returning_smd(tmp_path / "ret.hdf5", donor, acceptor, classes=np.array([2, 0]))
    mid0 = read_molecules(proj.path)["molecule_id"][0]
    mid0 = mid0.decode() if isinstance(mid0, bytes) else str(mid0)

    applied = apply_reconcile(proj, ret, accept_classes=(m for m in [mid0]))
    # molecule 0's non-zero class is M4-deferred; the generator must not be exhausted
    # before _deferred_class_ids reads it (finding: accept_classes consumed twice)
    assert applied.classes_deferred == [mid0]
    assert applied.classes_applied == []


# --------------------------------------------------------------------------- #
# schema freeze
# --------------------------------------------------------------------------- #
def test_import_keeps_schema_additive(tmp_path):
    """An imported /idealization is additive data — schema-guard stays green."""
    donor, acceptor = _step_trace(2, 20), _step_trace(2, 20) * 0.5
    proj, _keys = _build_store(tmp_path / "p.tether", donor, acceptor)
    ret = _returning_smd(tmp_path / "ret.hdf5", donor, acceptor)
    means = np.array([0.5])
    model = _write_tmaven_model(
        tmp_path / "m.hdf5", idealized=_model_idealized(20, 2, means), means=means, model_type="vb"
    )
    apply_reconcile(proj, ret, model_path=model, model_name="imp", import_idealization=True)

    with h5py.File(proj.path, "r") as f:
        current = introspect(f)
    # the frozen skeleton is intact; the imported model subgroup is additive data only
    assert diff_manifest(build_manifest(), current) == []
