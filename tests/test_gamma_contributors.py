# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The γ evidence table: each contributor, its window, its source and the reason (#927)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("h5py")

import h5py  # noqa: E402
import numpy as np  # noqa: E402

from test_project_gamma import _build_store, _gamma_store, _step_trace  # noqa: E402
from tether.fret.gamma import estimate_gamma  # noqa: E402
from tether.imaging.extract import read_molecules  # noqa: E402
from tether.io.schema import TABLE  # noqa: E402
from tether.project import correction_states as cs  # noqa: E402
from tether.project.core import Project  # noqa: E402
from tether.project.gamma import compute_gamma  # noqa: E402
from tether.project.gamma_contributors import (  # noqa: E402
    read_gamma_contributor_decisions,
    read_gamma_contributors,
    set_gamma_contributor,
)
from tether.project.leakage import compute_leakage_alpha  # noqa: E402

S = cs.CorrectionState
E = cs.CorrectionEvent
ME = cs.Actor.person("tester")
ALPHA = 0.1
ACC, DON, N = 40, 100, 120


def _text(value: object) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _key(path: Path, i: int) -> str:
    return _text(read_molecules(path)["molecule_key"][i])


@pytest.fixture
def project(tmp_path: Path) -> Project:
    path = tmp_path / "g.tether"
    _gamma_store(path, n_mol=12, gamma_true=1.2, alpha=ALPHA)
    compute_gamma(path)
    proj = Project.open(path)
    proj.acquire_lock()
    yield proj
    proj.release_lock()


def _mixed_store(path: Path) -> None:
    """Ten clean steps, then four rows the gates exclude, each with different evidence."""
    rows = [
        _step_trace(n=N, acceptor_pb=ACC, donor_pb=DON, gamma_true=1.2, alpha=ALPHA, seed=i + 1)
        for i in range(10)
    ]
    clean = {"n": N, "acceptor_pb": ACC, "donor_pb": DON, "alpha": ALPHA, "seed": 1, "noise": 0.0}
    rows.append(_step_trace(gamma_true=8.0, **clean))  # 10: above the ceiling of 5
    rows.append(_step_trace(gamma_true=-0.5, **clean))  # 11: the acceptor rises
    rows.append(_step_trace(gamma_true=1.0, donor_lo=800.0, donor_hi=800.0, **clean))  # 12
    rows.append(_step_trace(gamma_true=1.0, donor_lo=900.0, donor_hi=700.0, **clean))  # 13
    _build_store(path, np.stack([r[0] for r in rows]), np.stack([r[1] for r in rows]))
    with h5py.File(path, "r+") as f:
        table = f["molecules"][TABLE][:]
        for i in range(len(rows)):
            table["bleach_frames"][i] = (DON, ACC)
            table["alpha"][i] = ALPHA
        f["molecules"][TABLE][:] = table


# --- the table ------------------------------------------------------------------------------


def test_one_row_per_molecule_with_the_columns_and_absolute_windows(tmp_path: Path) -> None:
    path = tmp_path / "g.tether"
    _gamma_store(path, n_mol=12, start=5)
    summary = compute_gamma(path)
    rows = read_gamma_contributors(path)
    molecules = read_molecules(path)
    assert len(rows) == 12
    assert list(rows["molecule_key"]) == [_text(k) for k in molecules["molecule_key"]]
    assert set(rows["step_source"]) == {"detector"}
    assert set(rows["level_window"]) == {"half-window"}
    assert set(rows["half_window"]) == {20}
    assert np.all(rows["step"] == ACC)  # absolute, as /molecules.bleach_frames
    assert np.all(rows["pre_window"] == (ACC - 20, ACC))
    assert np.all(rows["post_window"] == (ACC, ACC + 20))
    assert rows["included"].all()
    assert set(rows["reason"]) == {""}
    np.testing.assert_allclose(rows["gamma"], molecules["gamma"])
    np.testing.assert_allclose(rows["ratio"], rows["gamma"])
    assert summary.gamma == pytest.approx(float(np.median(rows["gamma"][rows["included"]])))


def test_a_row_can_be_computed_again_from_the_stored_data(tmp_path: Path) -> None:
    path = tmp_path / "g.tether"
    _gamma_store(path, n_mol=12, start=5)
    compute_gamma(path, half_window=None)
    rows = read_gamma_contributors(path)
    assert set(rows["level_window"]) == {"whole-segment"}
    with h5py.File(path, "r") as f:
        donor = f["traces/donor_corrected"][:]
        acceptor = f["traces/acceptor_corrected"][:]
    alpha = read_molecules(path)["alpha"]
    for i, row in enumerate(rows):
        corrected = acceptor[i] - alpha[i] * donor[i]
        (p0, p1), (q0, q1) = row["pre_window"], row["post_window"]
        assert (p0, p1, q0, q1) == (5, ACC, ACC, DON)
        assert row["donor_pre"] == pytest.approx(donor[i, p0:p1].mean())
        assert row["donor_post"] == pytest.approx(donor[i, q0:q1].mean())
        assert row["acceptor_pre"] == pytest.approx(corrected[p0:p1].mean())
        assert row["acceptor_post"] == pytest.approx(corrected[q0:q1].mean())
        delta_a = row["acceptor_pre"] - row["acceptor_post"]
        delta_d = row["donor_post"] - row["donor_pre"]
        assert row["gamma"] == pytest.approx(delta_a / delta_d)


def test_an_excluded_row_keeps_its_raw_ratio_with_its_sign(tmp_path: Path) -> None:
    path = tmp_path / "g.tether"
    _mixed_store(path)
    summary = compute_gamma(path)
    rows = read_gamma_contributors(path)
    assert summary.n_qualifying == 10
    assert rows["included"][:10].all() and not rows["included"][10:].any()
    assert set(rows["by"][10:]) == {"estimator"}

    above, negative, zero, falls = rows[10], rows[11], rows[12], rows[13]
    assert above["gate"] == "out-of-range" and above["reason"] == "out-of-range"
    assert above["ratio"] == pytest.approx(8.0)
    assert negative["reason"] == "out-of-range" and negative["ratio"] == pytest.approx(-0.5)
    assert zero["gate"] == "degenerate-donor" and zero["reason"] == "zero-donor-step"
    assert zero["delta_donor"] == 0.0 and np.isnan(zero["ratio"])
    assert falls["reason"] == "degenerate-donor"
    assert falls["ratio"] == pytest.approx(falls["delta_acceptor"] / falls["delta_donor"])
    assert falls["delta_donor"] < 0.0  # the donor fell: kept, with the sign of each change
    assert np.isnan(rows["gamma"][10:]).all()


def test_a_short_segment_is_excluded_by_the_estimator_with_its_gate(tmp_path: Path) -> None:
    path = tmp_path / "g.tether"
    _gamma_store(path, n_mol=12, donpb_overrides={11: 55})
    compute_gamma(path)
    row = read_gamma_contributors(path)[11]
    assert not row["included"]
    assert (row["gate"], row["reason"], row["by"]) == ("short-post", "short-post", "estimator")
    assert np.isnan(row["ratio"])


def test_with_no_exclusion_the_estimate_is_the_one_before(tmp_path: Path) -> None:
    path = tmp_path / "g.tether"
    _mixed_store(path)
    summary = compute_gamma(path)
    with h5py.File(path, "r") as f:
        donor = f["traces/donor_corrected"][:]
        acceptor = f["traces/acceptor_corrected"][:]
    n = donor.shape[0]
    plain = estimate_gamma(list(donor), list(acceptor), [ALPHA] * n, [ACC] * n, [DON] * n)
    assert summary.gamma == plain.gamma
    assert summary.n_qualifying == plain.n_qualifying
    np.testing.assert_array_equal(
        read_molecules(path)["gamma"], [plain.effective_gamma(i) for i in range(n)]
    )


# --- exclude and include --------------------------------------------------------------------


def test_an_exclusion_needs_a_reason(project: Project) -> None:
    with pytest.raises(ValueError, match="reason"):
        set_gamma_contributor(project, _key(project.path, 0), include=False, actor=ME, reason=" ")
    assert len(read_gamma_contributor_decisions(project.path)) == 0


def test_an_unknown_molecule_is_refused(project: Project) -> None:
    with pytest.raises(KeyError):
        set_gamma_contributor(project, "nope", include=False, actor=ME, reason="bad step")


def test_exclude_recomputes_from_the_included_rows_and_include_restores(project: Project) -> None:
    path = project.path
    before = read_gamma_contributors(path)
    gamma_before = read_molecules(path)["gamma"].copy()
    key = _key(path, 3)

    summary = set_gamma_contributor(project, key, include=False, actor=ME, reason="two-step bleach")
    assert summary is not None and summary.applied
    assert summary.n_qualifying == 11 and summary.n_excluded == 1
    rows = read_gamma_contributors(path)
    assert not rows["included"][3]
    assert (rows["reason"][3], rows["by"][3], rows["actor"][3]) == (
        "two-step bleach",
        "person",
        "tester",
    )
    assert rows["ratio"][3] == before["ratio"][3]  # the evidence stays
    others = np.delete(before["gamma"], 3)
    assert summary.gamma == pytest.approx(float(np.median(others)))
    assert read_molecules(path)["gamma"][3] == pytest.approx(summary.gamma)  # the fallback
    with h5py.File(path, "r") as f:
        assert f["settings/gamma"].attrs["n_excluded"] == 1

    again = set_gamma_contributor(project, key, include=True, actor=ME)
    assert again is not None and again.n_excluded == 0
    np.testing.assert_array_equal(read_molecules(path)["gamma"], gamma_before)
    decisions = read_gamma_contributor_decisions(path)
    assert [bool(d["included"]) for d in decisions] == [False, True]
    assert {str(d["molecule_key"]) for d in decisions} == {key}


def test_a_decision_that_changes_nothing_writes_nothing(project: Project) -> None:
    key = _key(project.path, 0)
    assert set_gamma_contributor(project, key, include=True, actor=ME) is None
    set_gamma_contributor(project, key, include=False, actor=ME, reason="dim")
    assert set_gamma_contributor(project, key, include=False, actor=ME, reason="dim") is None
    assert len(read_gamma_contributor_decisions(project.path)) == 1


def test_a_recipe_policy_can_exclude_and_the_next_pass_keeps_it(project: Project) -> None:
    policy = cs.Actor.policy("exclude steps flagged by the recipe")
    key = _key(project.path, 5)
    set_gamma_contributor(project, key, include=False, actor=policy, reason="flagged")
    summary = compute_gamma(project.path)  # a later pass reads the decision again
    assert summary.n_excluded == 1
    row = read_gamma_contributors(project.path)[5]
    assert (bool(row["included"]), row["by"], row["actor"]) == (False, "policy", policy.name)


def test_a_withheld_leakage_drops_the_table_and_keeps_the_decisions(project: Project) -> None:
    set_gamma_contributor(project, _key(project.path, 0), include=False, actor=ME, reason="x")
    compute_leakage_alpha(project.path, min_qualifying_traces=999)
    assert len(read_gamma_contributors(project.path)) == 0
    assert len(read_gamma_contributor_decisions(project.path)) == 1


# --- the validity rule and the correction states (#925) -------------------------------------


def test_below_min_qualifying_included_rows_the_state_is_blocked(tmp_path: Path) -> None:
    path = tmp_path / "g.tether"
    _gamma_store(path, n_mol=10)
    assert compute_gamma(path).applied
    proj = Project.open(path)
    proj.acquire_lock()
    try:
        assert cs.read_correction_state(path).state is S.GAMMA_REVIEW
        key = _key(path, 0)
        summary = set_gamma_contributor(proj, key, include=False, actor=ME, reason="bad")
        assert summary is not None and summary.gamma is None and summary.n_qualifying == 9
        assert np.isnan(read_molecules(path)["gamma"]).all()
        assert cs.read_correction_state(path).state is S.CALIBRATION_BLOCKED

        set_gamma_contributor(proj, key, include=True, actor=ME)
        status = cs.read_correction_state(path)
        assert status.state is S.GAMMA_REVIEW
        assert status.last is not None and status.last.event is E.NEW_EVIDENCE
    finally:
        proj.release_lock()


def _approve(project: Project) -> None:
    cs.send_event(project, E.APPROVE, ME, gamma=1.2, factor_version="fv1")


def test_the_evidence_is_frozen_in_calibration_approved(project: Project) -> None:
    _approve(project)
    with pytest.raises(cs.CorrectionTransitionError, match="frozen"):
        set_gamma_contributor(project, _key(project.path, 0), include=False, actor=ME, reason="x")
    assert len(read_gamma_contributor_decisions(project.path)) == 0


@pytest.mark.parametrize(
    ("finish", "event"),
    [(False, E.CALIBRATION_EVIDENCE_CHANGES), (True, E.FACTOR_EVIDENCE_CHANGES)],
)
def test_a_change_after_approval_makes_the_calibration_stale(
    project: Project, finish: bool, event: cs.CorrectionEvent
) -> None:
    _approve(project)
    cs.send_event(project, E.APPLY_CORRECTION, ME, correction_version="cv1")
    if finish:
        cs.send_event(project, E.FINISH_REVIEW, ME)
    key = _key(project.path, 2)
    set_gamma_contributor(project, key, include=False, actor=ME, reason="photoblinking")
    status = cs.read_correction_state(project.path)
    assert status.state is S.CALIBRATION_STALE
    assert status.last is not None and status.last.event is event
    assert key in str(status.last.detail["note"])


# --- the estimator's exclusion (tether.fret.gamma) ------------------------------------------


def _cohort(n: int) -> tuple[list[np.ndarray], list[np.ndarray]]:
    pairs = [
        _step_trace(
            n=N, acceptor_pb=ACC, donor_pb=DON, gamma_true=1.0 + 0.05 * i, alpha=ALPHA, seed=i
        )
        for i in range(n)
    ]
    return [p[0] for p in pairs], [p[1] for p in pairs]


def test_estimate_gamma_leaves_an_excluded_trace_out_of_the_median() -> None:
    donor, acceptor = _cohort(12)
    full = estimate_gamma(donor, acceptor, [ALPHA] * 12, [ACC] * 12, [DON] * 12)
    cut = estimate_gamma(donor, acceptor, [ALPHA] * 12, [ACC] * 12, [DON] * 12, exclude={11})
    assert cut.n_qualifying == 11 and cut.excluded == frozenset({11})
    assert cut.gamma == pytest.approx(float(np.median([t.gamma for t in full.per_trace[:11]])))
    assert cut.per_trace == full.per_trace  # the evidence is the same
    assert cut.is_fallback(11) and cut.effective_gamma(11) == cut.gamma
    assert not cut.is_fallback(0) and cut.effective_gamma(0) == full.per_trace[0].gamma


def test_estimate_gamma_refuses_an_exclusion_outside_the_traces() -> None:
    donor, acceptor = _cohort(2)
    with pytest.raises(ValueError, match="outside"):
        estimate_gamma(donor, acceptor, [ALPHA] * 2, [ACC] * 2, [DON] * 2, exclude={2})


# --- the writer lock and the transition lock (review of PR #1335) ---------------------------


def test_an_unlocked_project_is_refused_before_anything_is_written(tmp_path: Path) -> None:
    from tether.project.lock import LockedError  # noqa: PLC0415

    path = tmp_path / "g.tether"
    _gamma_store(path, n_mol=10)
    compute_gamma(path)
    gamma_before = read_molecules(path)["gamma"].copy()
    proj = Project.open(path)  # no lock held
    with pytest.raises(LockedError):
        set_gamma_contributor(proj, _key(path, 0), include=False, actor=ME, reason="bad")
    assert len(read_gamma_contributor_decisions(path)) == 0
    np.testing.assert_array_equal(read_molecules(path)["gamma"], gamma_before)
    assert cs.read_correction_state(path).state is S.GAMMA_REVIEW


def test_an_approval_waits_for_a_contributor_edit_in_flight(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading  # noqa: PLC0415

    from tether.project import gamma as gamma_module  # noqa: PLC0415

    started, approved = threading.Event(), threading.Event()
    real = gamma_module._compute_gamma
    errors: list[BaseException] = []

    def approve() -> None:
        try:
            _approve(project)
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)
        approved.set()

    def slow_pass(*args: object, **kwargs: object):
        started.set()
        threading.Thread(target=approve).start()
        assert not approved.wait(0.3)  # the approval waits for the edit
        return real(*args, **kwargs)

    monkeypatch.setattr(gamma_module, "_compute_gamma", slow_pass)
    set_gamma_contributor(project, _key(project.path, 1), include=False, actor=ME, reason="x")
    assert started.is_set() and approved.wait(5) and not errors
    # The approval landed after the edit, so it froze the new evidence and was not lost.
    assert len(read_gamma_contributor_decisions(project.path)) == 1
    assert cs.read_correction_state(project.path).state is S.CALIBRATION_APPROVED
