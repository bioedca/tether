# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Trace generation review follow-ups (#1284, ADR-0136 amendment).

One test or more per acceptance criterion of #1284, on the small synthetic movies of
``test_trace_generations``:

* a stale model of a re-extracted movie refits on ``--stale-only``, its members found by
  ``molecule_id`` or else by ``molecule_key``; a member that moved is reported;
* a re-keyed condition survives a generation with no molecules;
* after a new generation, corrected analysis is refused until the correction workflow is
  approved again.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("skimage")
pytest.importorskip("h5py")
pytest.importorskip("tifffile")

import h5py  # noqa: E402
import numpy as np  # noqa: E402

import tether.project.extract as project_extract  # noqa: E402
from test_trace_generations import _movie_ids, _project_a_b, _reextract  # noqa: E402
from tether import cli  # noqa: E402
from tether.idealize import IdealizationResult, StateModel, read_smd  # noqa: E402
from tether.idealize.supervisor import ProbeResult  # noqa: E402
from tether.imaging.extract import molecule_key, read_molecules  # noqa: E402
from tether.io.generations import read_generation  # noqa: E402
from tether.project import correction_states as cs  # noqa: E402
from tether.project import labels  # noqa: E402
from tether.project.core import Project  # noqa: E402
from tether.project.idealize import (  # noqa: E402
    RefitMembersMoved,
    idealize_molecules,
    read_idealization,
    refit_membership,
    reidealize,
    stale_row_ids,
)
from tether.services import idealize as idealize_service  # noqa: E402

#: The molecules movie A holds (``test_trace_generations._DONOR_A``).
_N_A = 3
S = cs.CorrectionState
E = cs.CorrectionEvent
ME = cs.Actor.person("tester")


def _runner(smd_path, *, nstates, model_type="vbconhmm", **_kw) -> IdealizationResult:
    """A canned one-level fit over the SMD window: no sidecar needed."""
    smd = read_smd(smd_path)
    n, t = smd.n_molecules, smd.n_frames
    idealized = np.full((n, t), np.nan)
    pre = smd.pre_list if smd.pre_list is not None else np.zeros(n, dtype=int)
    post = smd.post_list if smd.post_list is not None else np.full(n, t, dtype=int)
    for i in range(n):
        idealized[i, int(pre[i]) : int(post[i])] = 0.5
    k = int(nstates)
    model = StateModel(
        model_type=model_type,
        nstates=k,
        means=np.linspace(0.2, 0.8, k) if k > 1 else np.array([0.5]),
        variances=np.full(k, 0.01),
        tmatrix=np.eye(k),
        norm_tmatrix=np.eye(k) * 0.9,
        elbo=-1.0,
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


def _text(value: object) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _fit(project: Path, keys: list[str] | None = None) -> None:
    idealize_molecules(project, keys, nstates=2, include_rejected=True, _runner=_runner)


# --- 1. the refit membership ----------------------------------------------------------------


def test_a_stale_fit_of_a_reextracted_movie_refits_on_its_carried_keys(tmp_path: Path) -> None:
    project, movie_a, _ = _project_a_b(tmp_path)
    movie_a_id, _ = _movie_ids(project)
    _fit(project)
    before = read_idealization(project, "vbconhmm")

    _reextract(project, movie_a)

    # Every row of movie A has a new molecule_id, so the whole movie is stale.
    new_a = read_generation(project, movie_a_id).molecules
    stale = set(stale_row_ids(project, before))
    assert stale and stale.isdisjoint(_text(m) for m in new_a["molecule_id"])
    membership = refit_membership(project, "vbconhmm")
    assert membership.moved_keys == ()

    refit = reidealize(project, "vbconhmm", _runner=_runner)

    # The same molecules, by key, in store order; the refreshed model reads live.
    assert sorted(refit.molecule_keys) == sorted(before.molecule_keys)
    current_ids = {_text(m) for m in read_molecules(project)["molecule_id"]}
    assert set(refit.molecule_ids) <= current_ids
    assert stale_row_ids(project, refit) == []


def _fake_sidecar(monkeypatch) -> None:
    monkeypatch.setattr(idealize_service, "_runner", _runner)
    monkeypatch.setattr(idealize_service, "_probe", lambda sup: ProbeResult(True, "fake sidecar"))


def test_stale_only_refits_the_model_of_a_reextracted_movie(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    project, movie_a, _ = _project_a_b(tmp_path)
    _fit(project)
    _reextract(project, movie_a)
    _fake_sidecar(monkeypatch)

    assert cli.main(["idealize", str(project), "--stale-only"]) == 0

    out = capsys.readouterr().out
    assert f"vbconhmm: {_N_A} stale" in out
    assert "refitted" in out and "moved" not in out
    assert stale_row_ids(project, read_idealization(project, "vbconhmm")) == []


def test_a_member_that_moved_is_reported_and_left_out_of_the_refit(tmp_path: Path) -> None:
    project, movie_a, _ = _project_a_b(tmp_path)
    movie_a_id, _ = _movie_ids(project)
    # As in test_a_spot_that_moved_gets_a_new_key_and_no_labels: the first generation
    # found molecule 0 two pixels from where the new extraction finds it.
    sha = hashlib.sha256(movie_a.read_bytes()).hexdigest()
    with h5py.File(project, "r+") as f:
        table = f["molecules"]["table"]
        row = table[0]
        row["donor_xy"] = np.asarray(row["donor_xy"]) + [2.0, 0.0]
        row["molecule_key"] = molecule_key(sha, row["donor_xy"])
        table[0] = row
    moved = _text(read_generation(project, movie_a_id).molecules["molecule_key"][0])
    _fit(project)
    before = read_idealization(project, "vbconhmm")

    _reextract(project, movie_a)

    membership = refit_membership(project, "vbconhmm")
    assert membership.moved_keys == (moved,)
    with pytest.warns(RefitMembersMoved, match=moved):
        refit = reidealize(project, "vbconhmm", _runner=_runner)
    assert moved not in refit.molecule_keys
    assert sorted(refit.molecule_keys) == sorted(k for k in before.molecule_keys if k != moved)


def test_stale_only_reports_a_member_that_moved(tmp_path: Path, monkeypatch, capsys) -> None:
    project, movie_a, _ = _project_a_b(tmp_path)
    movie_a_id, _ = _movie_ids(project)
    sha = hashlib.sha256(movie_a.read_bytes()).hexdigest()
    with h5py.File(project, "r+") as f:
        table = f["molecules"]["table"]
        row = table[0]
        row["donor_xy"] = np.asarray(row["donor_xy"]) + [2.0, 0.0]
        row["molecule_key"] = molecule_key(sha, row["donor_xy"])
        table[0] = row
    moved = _text(read_generation(project, movie_a_id).molecules["molecule_key"][0])
    _fit(project)
    _reextract(project, movie_a)
    _fake_sidecar(monkeypatch)

    with pytest.warns(RefitMembersMoved):
        result = idealize_service.run(
            idealize_service.IdealizeRequest(project=str(project), stale_only=True)
        )

    (refit,) = result.refits
    assert refit.refit is not None
    assert refit.moved_keys == (moved,)
    assert moved not in refit.refit.molecule_keys


def test_a_rejected_carried_member_stays_in_the_refit(tmp_path: Path) -> None:
    project, movie_a, _ = _project_a_b(tmp_path)
    movie_a_id, _ = _movie_ids(project)
    keys = [_text(k) for k in read_generation(project, movie_a_id).molecules["molecule_key"]]
    _fit(project)
    labels.reject(project, keys[1], labeler="curator")

    _reextract(project, movie_a)
    refit = reidealize(project, "vbconhmm", _runner=_runner)

    assert keys[1] in refit.molecule_keys


def test_a_refit_with_no_member_left_is_refused(tmp_path: Path) -> None:
    project, movie_a, _ = _project_a_b(tmp_path)
    movie_a_id, _ = _movie_ids(project)
    keys = [_text(k) for k in read_generation(project, movie_a_id).molecules["molecule_key"]]
    _fit(project, keys)
    real = project_extract.colocalize
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(project_extract, "colocalize", lambda spots, *a, **k: real(spots[:0], *a, **k))
        _reextract(project, movie_a)

    assert refit_membership(project, "vbconhmm").moved_keys == tuple(keys)
    with pytest.warns(RefitMembersMoved), pytest.raises(ValueError, match="no molecules"):
        reidealize(project, "vbconhmm", _runner=_runner)


# --- 2. the condition across an empty generation --------------------------------------------


def _set_condition(project: Path, movie_id: str, condition: str) -> None:
    """Give every molecule of ``movie_id`` ``condition``, as a re-key does."""
    with h5py.File(project, "r+") as f:
        table = f["molecules"]["table"]
        data = table[()]
        for i, mid in enumerate(data["movie_id"]):
            if _text(mid) == movie_id:
                data["condition_id"][i] = condition
        table[...] = data


def test_a_rekeyed_condition_survives_an_empty_generation(tmp_path: Path) -> None:
    project, movie_a, _ = _project_a_b(tmp_path)
    movie_a_id, movie_b_id = _movie_ids(project)
    parsed = _text(read_generation(project, movie_a_id).molecules["condition_id"][0])
    _set_condition(project, movie_a_id, "rekeyed-condition")

    real = project_extract.colocalize
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(project_extract, "colocalize", lambda spots, *a, **k: real(spots[:0], *a, **k))
        empty = _reextract(project, movie_a)
    assert empty.generation.n_molecules == 0
    _reextract(project, movie_a)
    _reextract(project, movie_a)  # and again, past two archived generations

    now = read_generation(project, movie_a_id).molecules
    assert now.shape[0] == _N_A
    assert {_text(c) for c in now["condition_id"]} == {"rekeyed-condition"}
    assert parsed != "rekeyed-condition"
    # The provisional condition parsed from the file name is kept beside it.
    assert {_text(c) for c in now["condition_id_provisional"]} == {parsed}
    # Movie B is not touched.
    other = read_generation(project, movie_b_id).molecules
    assert "rekeyed-condition" not in {_text(c) for c in other["condition_id"]}


# --- 3. corrected analysis after a new generation -------------------------------------------


_PATH = {
    S.CORRECTED_REVIEW: (
        (E.FINISH_INITIAL_CURATION, {}),
        (E.CONFIRM_BLEEDTHROUGH, {"alpha": 0.07, "source": "donor-only sample"}),
        (E.APPROVE, {"gamma": 0.9, "factor_version": "fv1"}),
        (E.APPLY_CORRECTION, {"correction_version": "cv1"}),
    ),
}
_PATH[S.ANALYSIS_READY] = (*_PATH[S.CORRECTED_REVIEW], (E.FINISH_REVIEW, {}))


def _drive(project: Path, state: cs.CorrectionState) -> None:
    handle = Project.open(project)
    handle.acquire_lock()
    try:
        for event, values in _PATH[state]:
            cs.send_event(handle, event, ME, **values)
    finally:
        handle.release_lock()
    assert cs.read_correction_state(project).state is state


@pytest.mark.parametrize("state", [S.ANALYSIS_READY, S.CORRECTED_REVIEW])
def test_a_new_generation_refuses_corrected_analysis_until_approved_again(
    tmp_path: Path, state: cs.CorrectionState
) -> None:
    project, movie_a, _ = _project_a_b(tmp_path)
    _drive(project, state)
    if state is S.ANALYSIS_READY:
        cs.require_analysis_mode(project, cs.AnalysisMode.CORRECTED)
    n_before = len(cs.read_correction_transitions(project))

    summary = _reextract(project, movie_a)

    status = cs.read_correction_state(project)
    assert status.state is S.CALIBRATION_STALE
    records = cs.read_correction_transitions(project)
    assert len(records) == n_before + 1
    last = records[-1]
    assert last.actor.by == "policy"
    assert last.detail["generation_id"] == summary.generation.generation_id
    with pytest.raises(cs.CorrectedAnalysisRefused, match="CALIBRATION_STALE"):
        cs.require_analysis_mode(project, cs.AnalysisMode.CORRECTED)
    cs.require_analysis_mode(project, cs.AnalysisMode.APPARENT)

    # Approved again (and reviewed), corrected analysis is allowed again.
    handle = Project.open(project)
    handle.acquire_lock()
    try:
        cs.send_event(handle, E.RECALCULATE, ME, alpha=0.07)
        cs.send_event(handle, E.APPROVE, ME, gamma=0.9, factor_version="fv2")
        cs.send_event(handle, E.APPLY_CORRECTION, ME, correction_version="cv2")
        cs.send_event(handle, E.FINISH_REVIEW, ME)
    finally:
        handle.release_lock()
    cs.require_analysis_mode(project, cs.AnalysisMode.CORRECTED)


def test_a_new_generation_before_approval_writes_no_transition(tmp_path: Path) -> None:
    project, movie_a, _ = _project_a_b(tmp_path)
    _reextract(project, movie_a)
    assert cs.read_correction_transitions(project) == []
    assert cs.read_correction_state(project).state is S.APPARENT_REVIEW


def test_a_failed_generation_writes_no_transition(tmp_path: Path, monkeypatch) -> None:
    project, movie_a, _ = _project_a_b(tmp_path)
    _drive(project, S.ANALYSIS_READY)
    n_before = len(cs.read_correction_transitions(project))

    def boom(*_a, **_k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(project_extract, "mark_stale_in", boom)
    with pytest.raises(project_extract.ExtractionError, match="disk full"):
        _reextract(project, movie_a)

    assert len(cs.read_correction_transitions(project)) == n_before
    assert cs.read_correction_state(project).state is S.ANALYSIS_READY
