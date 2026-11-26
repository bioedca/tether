# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Calibration windows apart from analysis windows, and the two G03 events (#928)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("h5py")

import h5py  # noqa: E402
import numpy as np  # noqa: E402

from test_project_gamma import _gamma_store  # noqa: E402
from tether.imaging.extract import read_molecules  # noqa: E402
from tether.io.schema import SCHEMA_VERSION, read_schema_version  # noqa: E402
from tether.project import correction_states as cs  # noqa: E402
from tether.project.calibration_windows import (  # noqa: E402
    SOURCE_BLEACH_STEP,
    SOURCE_SET,
    read_calibration_window_log,
    read_calibration_windows,
    reset_calibration_window,
    set_calibration_window,
)
from tether.project.core import Project  # noqa: E402
from tether.project.gamma import compute_gamma  # noqa: E402
from tether.project.gamma_contributors import read_gamma_contributors  # noqa: E402

S = cs.CorrectionState
E = cs.CorrectionEvent
ME = cs.Actor.person("tester")
START, ACC, DON, N = 5, 40, 100, 120


def _key(path: Path, i: int) -> str:
    value = read_molecules(path)["molecule_key"][i]
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _make_v3(path: Path) -> None:
    """Turn a store into one written before calibration windows: no table, stamp 3."""
    with h5py.File(path, "r+") as f:
        del f["calibration_windows"]
        f.attrs["schema_version"] = 3


@pytest.fixture
def project(tmp_path: Path) -> Project:
    path = tmp_path / "c.tether"
    _gamma_store(path, n_mol=12, start=START)
    compute_gamma(path)
    proj = Project.open(path)
    proj.acquire_lock()
    yield proj
    proj.release_lock()


def _to(project: Project, state: cs.CorrectionState) -> None:
    """Walk the scope (which starts in GAMMA_REVIEW, it holds factors) to ``state``."""
    cs.send_event(project, E.APPROVE, ME, gamma=1.2, factor_version="fv1")
    if state is S.CALIBRATION_APPROVED:
        return
    cs.send_event(project, E.APPLY_CORRECTION, ME, correction_version="cv1")
    if state is S.ANALYSIS_READY:
        cs.send_event(project, E.FINISH_REVIEW, ME)
    assert cs.read_correction_state(project.path).state is state


def test_an_older_store_reads_its_windows_from_the_bleach_step(tmp_path: Path) -> None:
    path = tmp_path / "c.tether"
    _gamma_store(path, n_mol=12, start=START)
    _make_v3(path)
    before = path.read_bytes()
    windows, sources = read_calibration_windows(path)
    assert np.all(windows == (START, DON))  # frame_range start to the donor bleach
    assert set(sources) == {SOURCE_BLEACH_STEP}
    assert len(read_calibration_window_log(path)) == 0
    assert path.read_bytes() == before  # a read writes nothing
    # The default reads the frames a γ pass read before #928: the same γ, no rewrite needed.
    summary = compute_gamma(path)
    assert summary.applied and summary.gamma == pytest.approx(1.2, rel=0.05)
    assert read_schema_version(path) == 3


def test_a_set_window_is_stored_apart_from_the_analysis_window(project: Project) -> None:
    path = project.path
    key = _key(path, 0)
    analysis = read_molecules(path)["analysis_window"].copy()
    # A window that ends before the acceptor bleach holds no step: the row has no γ.
    set_calibration_window(project, key, START, ACC - 5, actor=ME, reason="blinking after")
    np.testing.assert_array_equal(read_molecules(path)["analysis_window"], analysis)
    windows, sources = read_calibration_windows(path)
    assert tuple(windows[0]) == (START, ACC - 5) and sources[0] == SOURCE_SET
    assert set(sources[1:]) == {SOURCE_BLEACH_STEP}
    (row,) = read_calibration_window_log(path)
    assert tuple(row["window"]) == (START, ACC - 5)
    assert row["by"].decode() == "person" and row["reason"].decode() == "blinking after"
    evidence = read_gamma_contributors(path)
    assert evidence["gate"][0] == "no-step" and not evidence["included"][0]
    assert evidence["included"][1:].all()

    # A reset gives the row back its default; a second reset changes nothing.
    assert reset_calibration_window(project, key, actor=ME) is not None
    windows, sources = read_calibration_windows(path)
    assert tuple(windows[0]) == (START, DON) and sources[0] == SOURCE_BLEACH_STEP
    assert read_gamma_contributors(path)["included"].all()
    assert reset_calibration_window(project, key, actor=ME) is None
    assert len(read_calibration_window_log(path)) == 2


def test_the_first_write_to_an_older_store_backs_it_up(tmp_path: Path) -> None:
    path = tmp_path / "c.tether"
    _gamma_store(path, n_mol=12)
    _make_v3(path)
    original = path.read_bytes()
    proj = Project.open(path)
    proj.acquire_lock()
    try:
        set_calibration_window(proj, _key(path, 1), 0, 90, actor=ME)
    finally:
        proj.release_lock()
    assert (tmp_path / "c.v3-backup.tether").read_bytes() == original
    assert read_schema_version(path) == SCHEMA_VERSION == 4
    assert len(read_calibration_window_log(path)) == 1


def test_an_analysis_window_edit_in_analysis_ready_returns_to_review(project: Project) -> None:
    _to(project, S.ANALYSIS_READY)
    evidence = read_gamma_contributors(project.path)
    windows, _ = read_calibration_windows(project.path)
    written = project.set_analysis_window(_key(project.path, 3), START + 2, ACC, labeler="tester")
    assert len(written) == 1
    status = cs.read_correction_state(project.path)
    assert status.state is S.CORRECTED_REVIEW
    assert status.last is not None and status.last.event is E.EDIT_WINDOWS_OR_CURATION
    # The γ evidence and the calibration windows are not changed.
    after = read_gamma_contributors(project.path)
    assert after.dtype == evidence.dtype
    for name in evidence.dtype.names:
        np.testing.assert_array_equal(after[name], evidence[name])
    np.testing.assert_array_equal(read_calibration_windows(project.path)[0], windows)


def test_an_analysis_window_edit_before_analysis_ready_sends_nothing(project: Project) -> None:
    n = len(cs.read_correction_transitions(project.path))
    project.set_analysis_window(_key(project.path, 3), START + 2, ACC, labeler="tester")
    assert len(cs.read_correction_transitions(project.path)) == n


@pytest.mark.parametrize(
    ("state", "event"),
    [
        (S.CORRECTED_REVIEW, E.CALIBRATION_EVIDENCE_CHANGES),
        (S.ANALYSIS_READY, E.FACTOR_EVIDENCE_CHANGES),
    ],
)
def test_a_calibration_window_edit_after_approval_makes_it_stale(
    project: Project, state: cs.CorrectionState, event: cs.CorrectionEvent
) -> None:
    _to(project, state)
    key = _key(project.path, 2)
    set_calibration_window(project, key, START, 90, actor=ME, reason="donor blinks late")
    status = cs.read_correction_state(project.path)
    assert status.state is S.CALIBRATION_STALE
    assert status.last is not None and status.last.event is event
    assert key in str(status.last.detail["note"])


def test_the_calibration_evidence_is_frozen_in_calibration_approved(project: Project) -> None:
    _to(project, S.CALIBRATION_APPROVED)
    with pytest.raises(cs.CorrectionTransitionError, match="frozen"):
        set_calibration_window(project, _key(project.path, 0), START, 90, actor=ME)
    assert len(read_calibration_window_log(project.path)) == 0


def test_bad_requests_are_refused_before_any_write(project: Project) -> None:
    with pytest.raises(ValueError, match="start < stop"):
        set_calibration_window(project, _key(project.path, 0), 50, 50, actor=ME)
    with pytest.raises(KeyError):
        set_calibration_window(project, "no-such-key", 0, 10, actor=ME)
    assert len(read_calibration_window_log(project.path)) == 0
