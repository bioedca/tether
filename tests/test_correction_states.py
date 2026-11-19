# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The correction states of G03, stored in the project (#925, ADR-0131)."""

from __future__ import annotations

import ast
import subprocess
import sys
import threading
from pathlib import Path

import h5py
import pytest

from tether.project import correction_states as cs
from tether.project import jobs
from tether.project.core import Project
from tether.project.lock import LockedError, LockIdentity

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "tether" / "project" / "correction_states.py"
FORMAT_PAGE = ROOT / "docs" / "reference" / "tether-format.md"
_QT = ("PySide6", "PySide2", "PyQt6", "PyQt5", "shiboken6", "qtpy", "pyqtgraph", "napari")

S = cs.CorrectionState
E = cs.CorrectionEvent
ME = cs.Actor.person("tester")

#: The table of issue #925 and PRD §7.2, cell by cell: (from, event, to).
G03 = {
    (S.APPARENT_REVIEW, E.FINISH_INITIAL_CURATION, S.BLEEDTHROUGH_REQUIRED),
    (S.BLEEDTHROUGH_REQUIRED, E.CONFIRM_BLEEDTHROUGH, S.GAMMA_REVIEW),
    (S.GAMMA_REVIEW, E.INSUFFICIENT_EVIDENCE, S.CALIBRATION_BLOCKED),
    (S.CALIBRATION_BLOCKED, E.NEW_EVIDENCE, S.GAMMA_REVIEW),
    (S.CALIBRATION_BLOCKED, E.OVERRIDE, S.GAMMA_REVIEW),
    (S.GAMMA_REVIEW, E.APPROVE, S.CALIBRATION_APPROVED),
    (S.CALIBRATION_APPROVED, E.APPLY_CORRECTION, S.CORRECTED_REVIEW),
    (S.CORRECTED_REVIEW, E.FINISH_REVIEW, S.ANALYSIS_READY),
    (S.CORRECTED_REVIEW, E.CALIBRATION_EVIDENCE_CHANGES, S.CALIBRATION_STALE),
    (S.ANALYSIS_READY, E.FACTOR_EVIDENCE_CHANGES, S.CALIBRATION_STALE),
    (S.ANALYSIS_READY, E.EDIT_WINDOWS_OR_CURATION, S.CORRECTED_REVIEW),
    (S.CALIBRATION_STALE, E.RECALCULATE, S.BLEEDTHROUGH_REQUIRED),
    (S.CALIBRATION_STALE, E.RECALCULATE, S.GAMMA_REVIEW),
}

#: Values that make the guard of each event hold.
GOOD: dict[cs.CorrectionEvent, dict[str, object]] = {
    E.CONFIRM_BLEEDTHROUGH: {"alpha": 0.07, "source": "donor-only sample, donly.tether"},
    E.OVERRIDE: {"gamma": 1.2, "source": "manual gamma from the 2026-10 calibration"},
    E.APPROVE: {"gamma": 0.9, "factor_version": "fv1"},
    E.APPLY_CORRECTION: {"correction_version": "cv1"},
    E.RECALCULATE: {"alpha": 0.07},
}

#: The events that bring a fresh project to each state.
PATH: dict[cs.CorrectionState, tuple[cs.CorrectionEvent, ...]] = {
    S.APPARENT_REVIEW: (),
    S.BLEEDTHROUGH_REQUIRED: (E.FINISH_INITIAL_CURATION,),
    S.GAMMA_REVIEW: (E.FINISH_INITIAL_CURATION, E.CONFIRM_BLEEDTHROUGH),
    S.CALIBRATION_BLOCKED: (
        E.FINISH_INITIAL_CURATION,
        E.CONFIRM_BLEEDTHROUGH,
        E.INSUFFICIENT_EVIDENCE,
    ),
    S.CALIBRATION_APPROVED: (E.FINISH_INITIAL_CURATION, E.CONFIRM_BLEEDTHROUGH, E.APPROVE),
    S.CORRECTED_REVIEW: (
        E.FINISH_INITIAL_CURATION,
        E.CONFIRM_BLEEDTHROUGH,
        E.APPROVE,
        E.APPLY_CORRECTION,
    ),
    S.ANALYSIS_READY: (
        E.FINISH_INITIAL_CURATION,
        E.CONFIRM_BLEEDTHROUGH,
        E.APPROVE,
        E.APPLY_CORRECTION,
        E.FINISH_REVIEW,
    ),
    S.CALIBRATION_STALE: (
        E.FINISH_INITIAL_CURATION,
        E.CONFIRM_BLEEDTHROUGH,
        E.APPROVE,
        E.APPLY_CORRECTION,
        E.CALIBRATION_EVIDENCE_CHANGES,
    ),
}


@pytest.fixture
def project(tmp_path: Path) -> Project:
    proj = Project.create(tmp_path / "p.tether")
    proj.acquire_lock()
    yield proj
    proj.release_lock()


def _send(project: Project, event: cs.CorrectionEvent, **values: object) -> cs.CorrectionStatus:
    return cs.send_event(project, event, ME, **{**GOOD.get(event, {}), **values})


def _drive(project: Project, state: cs.CorrectionState) -> None:
    for event in PATH[state]:
        _send(project, event)
    assert cs.read_correction_state(project.path).state is state


def _n_rows(path: Path) -> int:
    return len(cs.read_correction_transitions(path))


# --- Qt-free ----------------------------------------------------------------------------------


def test_the_module_imports_no_qt_and_no_gui_module() -> None:
    names: set[str] = set()
    for n in ast.walk(ast.parse(SOURCE.read_text(encoding="utf-8"))):
        if isinstance(n, ast.Import):
            names.update(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            names.add(n.module)
    assert {m for m in names if m.split(".")[0] in _QT or m.startswith("tether.gui")} == set()
    code = (
        "import sys, tether.project.correction_states\n"
        f"qt = {_QT!r}\n"
        "print([m for m in sys.modules if m.split('.')[0] in qt or m.startswith('tether.gui')])"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


# --- the table --------------------------------------------------------------------------------


def test_the_states_and_edges_are_those_of_g03_and_no_others() -> None:
    assert {s.name for s in S} == {
        "APPARENT_REVIEW",
        "BLEEDTHROUGH_REQUIRED",
        "GAMMA_REVIEW",
        "CALIBRATION_BLOCKED",
        "CALIBRATION_APPROVED",
        "CORRECTED_REVIEW",
        "ANALYSIS_READY",
        "CALIBRATION_STALE",
    }
    edges = [(t.source, t.event, t.target) for t in cs.TRANSITIONS]
    assert len(edges) == len(set(edges)) == 13
    assert set(edges) == G03
    assert cs.INITIAL_STATE is S.APPARENT_REVIEW


def test_a_fresh_project_starts_in_apparent_review_and_stores_nothing(project: Project) -> None:
    status = cs.read_correction_state(project.path)
    assert (status.state, status.stored, status.last) == (S.APPARENT_REVIEW, False, None)
    with h5py.File(project.path, "r") as f:
        assert "correction_transitions" not in f["settings"]


@pytest.mark.parametrize(("source", "event", "target"), sorted(G03, key=str))
def test_each_edge_of_the_table(
    project: Project,
    source: cs.CorrectionState,
    event: cs.CorrectionEvent,
    target: cs.CorrectionState,
) -> None:
    _drive(project, source)
    values: dict[str, object] = {}
    if event is E.RECALCULATE and target is S.BLEEDTHROUGH_REQUIRED:
        values = {"alpha": None}
    status = _send(project, event, **values)
    assert status.state is target
    assert (status.last.from_state, status.last.event, status.last.to_state) == (
        source,
        event,
        target,
    )


@pytest.mark.parametrize("state", list(S))
def test_no_other_transition_is_possible(project: Project, state: cs.CorrectionState) -> None:
    _drive(project, state)
    allowed = {e for (s, e, _t) in G03 if s is state}
    made_it = PATH[state][-1] if PATH[state] else None
    for event in E:
        if event in allowed or event is made_it:
            continue
        n = _n_rows(project.path)
        with pytest.raises(cs.CorrectionTransitionError, match=state.value):
            _send(project, event)
        assert _n_rows(project.path) == n
        assert cs.read_correction_state(project.path).state is state


# --- guards -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "values",
    [
        {"source": ""},
        {"source": "   "},
        {"alpha": float("nan")},
        {"alpha": None},
    ],
)
def test_confirming_the_bleedthrough_factor_needs_a_valid_sourced_value(
    project: Project, values: dict[str, object]
) -> None:
    _drive(project, S.BLEEDTHROUGH_REQUIRED)
    with pytest.raises(cs.CorrectionGuardError, match="valid and has a source"):
        _send(project, E.CONFIRM_BLEEDTHROUGH, **values)
    assert cs.read_correction_state(project.path).state is S.BLEEDTHROUGH_REQUIRED


@pytest.mark.parametrize(
    "values",
    [{"gamma": 0.0}, {"gamma": -1.0}, {"gamma": None}, {"source": ""}, {"alpha": float("inf")}],
)
def test_an_override_needs_a_valid_sourced_value(
    project: Project, values: dict[str, object]
) -> None:
    _drive(project, S.CALIBRATION_BLOCKED)
    with pytest.raises(cs.CorrectionGuardError, match="override valid"):
        _send(project, E.OVERRIDE, **values)


@pytest.mark.parametrize("gamma", [None, 0.0, float("nan")])
def test_approval_needs_a_valid_gamma(project: Project, gamma: float | None) -> None:
    _drive(project, S.GAMMA_REVIEW)
    with pytest.raises(cs.CorrectionGuardError, match="gamma valid"):
        _send(project, E.APPROVE, gamma=gamma)
    with pytest.raises(ValueError, match="factor_version"):
        _send(project, E.APPROVE, factor_version="")
    assert cs.read_correction_state(project.path).state is S.GAMMA_REVIEW


def test_applying_a_correction_names_its_version(project: Project) -> None:
    _drive(project, S.CALIBRATION_APPROVED)
    with pytest.raises(ValueError, match="correction_version"):
        _send(project, E.APPLY_CORRECTION, correction_version=" ")


def test_finishing_the_review_needs_current_dependencies(project: Project) -> None:
    _drive(project, S.CORRECTED_REVIEW)
    jobs.record_inputs(project, "correction_version", "cv1", {("factor_version", "fv1"): "h1"})
    jobs.set_stale_marks(project, "factor_version", "fv1", "h2")  # marks cv1 through edge 19
    with pytest.raises(cs.CorrectionGuardError, match="all dependencies current"):
        _send(project, E.FINISH_REVIEW)
    assert cs.read_correction_state(project.path).state is S.CORRECTED_REVIEW


def test_a_stale_mark_that_lands_after_the_guard_refuses_the_review(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A stale mark moves neither the lock nor the log, so the guard is read again under the
    # file the row is written to.
    _drive(project, S.CORRECTED_REVIEW)
    jobs.record_inputs(project, "correction_version", "cv1", {("factor_version", "fv1"): "h1"})
    choose = cs._choose

    def choose_then_mark(*args: object, **kwargs: object) -> object:
        result = choose(*args, **kwargs)
        jobs.set_stale_marks(project, "factor_version", "fv1", "h2")  # marks cv1
        return result

    monkeypatch.setattr(cs, "_choose", choose_then_mark)
    with pytest.raises(cs.CorrectionGuardError, match="all dependencies current"):
        _send(project, E.FINISH_REVIEW)
    assert cs.read_correction_state(project.path).state is S.CORRECTED_REVIEW


def test_a_mark_on_another_instance_does_not_block_the_review(project: Project) -> None:
    _drive(project, S.CORRECTED_REVIEW)
    jobs.record_inputs(project, "correction_version", "cv0", {("factor_version", "fv0"): "h1"})
    jobs.set_stale_marks(project, "factor_version", "fv0", "h2")
    assert _send(project, E.FINISH_REVIEW).state is S.ANALYSIS_READY


@pytest.mark.parametrize(
    ("alpha", "target"),
    [
        (0.07, S.GAMMA_REVIEW),
        (None, S.BLEEDTHROUGH_REQUIRED),
        (float("nan"), S.BLEEDTHROUGH_REQUIRED),
    ],
)
def test_leaving_stale_follows_the_bleedthrough_factor(
    project: Project, alpha: float | None, target: cs.CorrectionState
) -> None:
    _drive(project, S.CALIBRATION_STALE)
    assert _send(project, E.RECALCULATE, alpha=alpha).state is target


# --- the transition record --------------------------------------------------------------------


def test_each_transition_records_who_or_what_and_when(project: Project) -> None:
    _send(project, E.FINISH_INITIAL_CURATION)
    policy = '{"step": "alpha.confirm", "rule": "value", "alpha": 0.07, "otherwise": "stop"}'
    cs.send_event(
        project, E.CONFIRM_BLEEDTHROUGH, cs.Actor.policy(policy), alpha=0.07, source="donly"
    )
    first, second = cs.read_correction_transitions(project.path)
    assert first.actor == cs.Actor("person", "tester")
    assert second.actor == cs.Actor("policy", policy)
    assert second.detail == {
        "alpha": 0.07,
        "source": "donly",
        "guard": "bleedthrough factor valid and has a source",
        "action": "estimate gamma",
    }
    assert first.timestamp.endswith("+00:00") and first.timestamp <= second.timestamp
    assert {first.scope, second.scope} == {"project"}
    assert second.app_version


def test_an_actor_is_a_person_or_a_policy_with_a_name() -> None:
    with pytest.raises(ValueError, match="person"):
        cs.Actor("robot", "x")
    with pytest.raises(ValueError, match="name"):
        cs.Actor.policy(" ")
    assert cs.Actor.person().by == "person"


# --- analysis modes ---------------------------------------------------------------------------


@pytest.mark.parametrize("state", list(S))
def test_a_corrected_analysis_needs_analysis_ready_and_apparent_e_never_waits(
    project: Project, state: cs.CorrectionState
) -> None:
    _drive(project, state)
    assert cs.require_analysis_mode(project.path, "apparent").state is state
    if state is S.ANALYSIS_READY:
        assert cs.require_analysis_mode(project.path, cs.AnalysisMode.CORRECTED).state is state
        return
    with pytest.raises(cs.CorrectedAnalysisRefused, match=state.value) as err:
        cs.require_analysis_mode(project.path, cs.AnalysisMode.CORRECTED)
    assert err.value.state is state


# --- existing projects ------------------------------------------------------------------------


def test_a_project_with_stored_factors_opens_in_gamma_review_with_them_as_proposal(
    project: Project,
) -> None:
    with h5py.File(project.path, "r+") as f:
        f["settings"].create_group("leakage").attrs["alpha"] = 0.08
        f["settings"].create_group("gamma").attrs["gamma"] = float("nan")
    status = cs.read_correction_state(project.path)
    assert (status.state, status.stored) == (S.GAMMA_REVIEW, False)
    assert status.proposal == {"alpha": 0.08, "gamma": None}
    with pytest.raises(cs.CorrectedAnalysisRefused, match="GAMMA_REVIEW"):
        cs.require_analysis_mode(project.path, "corrected")
    approved = _send(project, E.APPROVE)
    assert approved.last.from_state is S.GAMMA_REVIEW
    assert approved.last.detail["start_proposal"] == {"alpha": 0.08, "gamma": None}


def test_a_manual_override_of_a_correction_pass_is_the_proposal(project: Project) -> None:
    with h5py.File(project.path, "r+") as f:
        grp = f["settings"].create_group("correction")
        grp.attrs["alpha_override"] = float("nan")
        grp.attrs["gamma_override"] = 1.3
    status = cs.read_correction_state(project.path)
    assert (status.state, status.proposal) == (S.GAMMA_REVIEW, {"alpha": None, "gamma": 1.3})


# --- idempotence, scope, lock, store ----------------------------------------------------------


@pytest.mark.parametrize(("source", "event", "target"), sorted(G03, key=str))
def test_an_event_sent_twice_gives_the_state_of_one(
    project: Project,
    source: cs.CorrectionState,
    event: cs.CorrectionEvent,
    target: cs.CorrectionState,
) -> None:
    _drive(project, source)
    values = {"alpha": None} if target is S.BLEEDTHROUGH_REQUIRED and event is E.RECALCULATE else {}
    once = _send(project, event, **values)
    n = _n_rows(project.path)
    twice = _send(project, event, **values)
    assert twice.state is once.state is target
    assert _n_rows(project.path) == n


def test_only_the_project_scope_until_conditions_have_their_own(project: Project) -> None:
    with pytest.raises(ValueError, match="#624"):
        cs.read_correction_state(project.path, scope="condition:KJG")
    with pytest.raises(ValueError, match="#624"):
        cs.send_event(project, E.FINISH_INITIAL_CURATION, ME, scope="condition:KJG")


def test_a_transition_needs_the_writer_lock_and_a_read_does_not(tmp_path: Path) -> None:
    project = Project.create(tmp_path / "p.tether")
    project.acquire_lock()
    _send(project, E.FINISH_INITIAL_CURATION)
    thief = Project(project.path, identity=LockIdentity("other-host", "someone", 4242))
    thief.steal_lock()
    with pytest.raises(LockedError):
        _send(project, E.CONFIRM_BLEEDTHROUGH)
    assert cs.read_correction_state(project.path).state is S.BLEEDTHROUGH_REQUIRED


def test_the_store_layout_is_on_the_format_page(project: Project) -> None:
    _send(project, E.FINISH_INITIAL_CURATION)
    with h5py.File(project.path, "r") as f:
        ds = f["settings"]["correction_transitions"]
        assert ds.maxshape == (None,)
        fields = ds.dtype.names
    page = FORMAT_PAGE.read_text(encoding="utf-8")
    assert "/settings/correction_transitions" in page
    for name in fields:
        assert f"`{name}`" in page


def test_a_transition_on_an_unlocked_project_is_refused(tmp_path: Path) -> None:
    # Greptile P1 on PR #1237: the identity check alone let an unlocked project write.
    project = Project.create(tmp_path / "p.tether")
    with pytest.raises(LockedError):
        _send(project, E.FINISH_INITIAL_CURATION)
    assert _n_rows(project.path) == 0
    with project.write_lock():  # a lock held for the session, not retained on the handle
        assert _send(project, E.FINISH_INITIAL_CURATION).state is S.BLEEDTHROUGH_REQUIRED


def test_a_transition_that_lands_while_another_is_checked_refuses_the_second(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    _drive(project, S.GAMMA_REVIEW)
    choose = cs._choose
    raced: list[bool] = []

    def racing_choose(*args: object, **kwargs: object) -> object:
        if not raced:  # another writer of the same lock moves the state in between
            raced.append(True)
            cs.send_event(project, E.INSUFFICIENT_EVIDENCE, ME)
        return choose(*args, **kwargs)

    monkeypatch.setattr(cs, "_choose", racing_choose)
    with pytest.raises(cs.CorrectionTransitionError, match="changed"):
        _send(project, E.APPROVE)
    status = cs.read_correction_state(project.path)
    assert status.state is S.CALIBRATION_BLOCKED
    assert [r.event for r in cs.read_correction_transitions(project.path)][-1] is (
        E.INSUFFICIENT_EVIDENCE
    )


def test_a_transition_that_lands_after_the_state_is_read_refuses_the_second(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The state and the row count are one snapshot: a transition that lands after the state
    # was read cannot be counted as seen, so the edge from the old state is not appended.
    _drive(project, S.GAMMA_REVIEW)
    snapshot = cs._snapshot
    raced: list[bool] = []

    def racing_snapshot(*args: object, **kwargs: object) -> object:
        result = snapshot(*args, **kwargs)
        if not raced:  # another writer of the same lock moves the state right after the read
            raced.append(True)
            cs.send_event(project, E.INSUFFICIENT_EVIDENCE, ME)
        return result

    monkeypatch.setattr(cs, "_snapshot", racing_snapshot)
    with pytest.raises(cs.CorrectionTransitionError, match="changed"):
        _send(project, E.APPROVE)
    records = cs.read_correction_transitions(project.path)
    assert records[-1].to_state is S.CALIBRATION_BLOCKED
    for before, after in zip(records, records[1:], strict=False):
        assert after.from_state is before.to_state  # the log has no gap


def test_two_threads_on_one_project_send_their_transitions_one_at_a_time(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    # One thread has checked its row and not yet written it. A second thread on the same
    # project waits for it, then reads the new state, so no two rows leave the same state.
    _drive(project, S.GAMMA_REVIEW)
    append = cs._append
    checked, release = threading.Event(), threading.Event()

    def held_append(path: object, row: dict[str, str], check: object) -> None:
        def held_check(f: object) -> None:
            check(f)  # type: ignore[operator]
            if row["event"] == E.APPROVE:
                checked.set()
                release.wait(10)

        append(path, row, held_check)  # type: ignore[arg-type]

    monkeypatch.setattr(cs, "_append", held_append)
    outcome: dict[str, object] = {}

    def run(name: str, event: cs.CorrectionEvent) -> None:
        try:
            outcome[name] = _send(project, event).state
        except Exception as exc:  # noqa: BLE001 - the test reads which error it was
            outcome[name] = exc

    first = threading.Thread(target=run, args=("first", E.APPROVE))
    first.start()
    assert checked.wait(10)
    second = threading.Thread(target=run, args=("second", E.INSUFFICIENT_EVIDENCE))
    second.start()
    second.join(0.5)
    assert second.is_alive()  # it waits for the first transition
    release.set()
    first.join(10)
    second.join(10)
    assert outcome["first"] is S.CALIBRATION_APPROVED
    assert isinstance(outcome["second"], cs.CorrectionTransitionError)
    records = cs.read_correction_transitions(project.path)
    assert records[-1].to_state is S.CALIBRATION_APPROVED
    for before, after in zip(records, records[1:], strict=False):
        assert after.from_state is before.to_state
