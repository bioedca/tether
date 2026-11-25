# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Corrections → Run correction pass… in the Curate and correct section (#360), headless.

The pass is ``compute_corrected_fret`` over the open project, run on the background-task
service under the retained session lock. Each test reads the real ``.tether`` store after
the pass.
"""

from __future__ import annotations

import importlib.util
import threading

import numpy as np
import pytest

pytest.importorskip("h5py")

_HAS_QT = all(importlib.util.find_spec(m) is not None for m in ("pyqtgraph", "PySide6"))
pytestmark = [
    pytest.mark.gui,
    pytest.mark.skipif(not _HAS_QT, reason="pyqtgraph/PySide6 not installed"),
]

N_FRAMES = 40
#: The applied factors of the four molecules: two valid, one withheld γ, one withheld α.
ALPHA = [0.1, 0.1, 0.1, float("nan")]
GAMMA = [1.2, 0.9, float("nan"), 1.1]


def _store(tmp_path, *, alpha=ALPHA, gamma=GAMMA, factors=True):
    """A real ``.tether`` with four molecules and the applied α and γ above.

    ``factors`` also writes ``/settings/leakage`` and ``/settings/gamma``, as the α and γ
    passes do, so the project starts in GAMMA_REVIEW (ADR-0131 §6); without them it starts
    in APPARENT_REVIEW.
    """
    import h5py

    from _analysis_store import build_store_with_channels

    rng = np.random.default_rng(7)
    donor = rng.normal(600.0, 15.0, size=(4, N_FRAMES))
    acceptor = rng.normal(400.0, 15.0, size=(4, N_FRAMES))
    project, _keys = build_store_with_channels(tmp_path, donor, acceptor, name="corr.tether")
    with h5py.File(project.path, "r+") as f:
        table = f["molecules"]["table"][:]
        table["alpha"] = alpha
        table["gamma"] = gamma
        f["molecules"]["table"][:] = table
        if factors:
            f["settings"].create_group("leakage").attrs["alpha"] = 0.1
            f["settings"].create_group("gamma").attrs["gamma"] = 1.1
    return project


def _molecules(path):
    from tether.imaging.extract import read_molecules

    return read_molecules(path)


def _text(value) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _has_correction_settings(path) -> bool:
    import h5py

    with h5py.File(path, "r") as f:
        return "correction" in f["settings"]


@pytest.fixture
def shell(qapp, qtbot, tmp_path):
    from tether.gui.shell import TetherShell

    s = TetherShell(preferences_file=tmp_path / "user" / "preferences.json")
    qtbot.addWidget(s.window)
    yield s
    s.close()


def _loaded(shell, tmp_path, **kwargs):
    project = _store(tmp_path, **kwargs)
    assert shell.load_project(project.path) is not None
    assert shell.curation_project is not None
    return project


def _run(shell, qtbot, **kwargs):
    assert shell.run_correction_pass(**kwargs), shell.messages.latest.text
    qtbot.waitUntil(lambda: not shell.is_idealizing, timeout=10_000)
    return shell.correction_summary


def test_the_pass_stamps_each_molecule_and_shows_the_summary_field_by_field(
    shell, qtbot, tmp_path
) -> None:
    from tether.project.correct import METHOD_APPARENT_UNAVAILABLE, METHOD_CORRECTED
    from tether.project.correction_states import read_correction_transitions

    project = _loaded(shell, tmp_path)
    shell.sync_correction_controls()
    assert shell.correction_pass_action.isEnabled()

    summary = _run(shell, qtbot)

    assert summary is not None
    assert summary.fields == {
        "Molecules examined": "4",
        "Corrected (estimated α and γ)": "2",
        "Corrected (manual α or γ)": "0",
        "Apparent E": "2",
        "Total correction failure": "no",
        "Apparent E only (by choice)": "no",
        "Source": "accurate-fret",
        "Correction state": "GAMMA_REVIEW",
    }
    assert summary.banner_kind is None
    assert summary.dialog.isVisible()
    methods = [_text(m) for m in _molecules(project.path)["correction_method"]]
    assert methods == [METHOD_CORRECTED, METHOD_CORRECTED] + [METHOD_APPARENT_UNAVAILABLE] * 2
    # The pass is no event of G03: the state stays as it was, with no row (ADR-0131).
    assert read_correction_transitions(project.path) == []
    assert shell.messages.latest.text.startswith("Correction pass: 2 of 4 molecules corrected")


def test_a_nan_factor_and_a_nan_corrected_e_are_never_written(shell, qtbot, tmp_path) -> None:
    from tether.fret import apparent_fret, corrected_fret
    from tether.imaging.extract import read_traces
    from tether.project.correct import METHOD_CORRECTED

    project = _loaded(shell, tmp_path)
    before = _molecules(project.path)

    _run(shell, qtbot)

    after = _molecules(project.path)
    # The pass applies no factor of its own: α and γ are what the α and γ passes stored.
    np.testing.assert_array_equal(after["alpha"], before["alpha"])
    np.testing.assert_array_equal(after["gamma"], before["gamma"])
    traces = read_traces(project.path)
    for i, row in enumerate(after):
        donor, acceptor = traces["donor_corrected"][i], traces["acceptor_corrected"][i]
        if _text(row["correction_method"]) == METHOD_CORRECTED:
            # A corrected molecule has a finite α and a γ above 0, and its E is finite.
            assert np.isfinite(row["alpha"])
            assert np.isfinite(row["gamma"])
            assert row["gamma"] > 0.0
            e = corrected_fret(donor, acceptor, alpha=row["alpha"], gamma=row["gamma"])
        else:
            # A withheld factor routes the molecule to apparent E, never to a NaN E.
            assert not (np.isfinite(row["alpha"]) and np.isfinite(row["gamma"]))
            e = apparent_fret(donor, acceptor)
        assert np.all(np.isfinite(e)), f"molecule {i} has a NaN E"
        assert float(row["correction_confidence"]) in (0.0, 1.0)


def test_apparent_e_only_is_a_choice_and_never_raises_the_failure_banner(
    shell, qtbot, tmp_path
) -> None:
    from tether.gui.corrections import BANNER_CHOICE, BANNER_FAILURE
    from tether.gui.messages import Level
    from tether.project.correct import METHOD_APPARENT_TOGGLE

    project = _loaded(shell, tmp_path)

    summary = _run(shell, qtbot, apparent_e_only=True)

    # total_failure is True under the toggle too (correct.py): the banner reads the toggle.
    assert summary.result.summary.total_failure is True
    assert summary.fields["Total correction failure"] == "yes"
    assert summary.fields["Apparent E only (by choice)"] == "yes"
    assert summary.banner_kind == BANNER_CHOICE
    assert summary.banner_kind != BANNER_FAILURE
    assert "not a correction failure" in summary.banner.text()
    assert shell.messages.latest.level is not Level.WARNING
    methods = {_text(m) for m in _molecules(project.path)["correction_method"]}
    assert methods == {METHOD_APPARENT_TOGGLE}


def test_a_real_total_failure_raises_the_failure_banner(shell, qtbot, tmp_path) -> None:
    from tether.gui.corrections import BANNER_FAILURE
    from tether.gui.messages import Level

    nan = float("nan")
    _loaded(shell, tmp_path, alpha=[nan] * 4, gamma=[nan] * 4)

    summary = _run(shell, qtbot)

    assert summary.fields["Total correction failure"] == "yes"
    assert summary.fields["Apparent E only (by choice)"] == "no"
    assert summary.banner_kind == BANNER_FAILURE
    assert summary.banner.isVisibleTo(summary.dialog)
    assert "Corrections unavailable" in summary.banner.text()
    assert shell.messages.latest.level is Level.WARNING
    # A screen reader hears the banner's text and each shown value, not only a label.
    from pyqtgraph.Qt import QtWidgets

    assert summary.banner.text() in summary.banner.accessibleName()
    names = {w.accessibleName() for w in summary.dialog.findChildren(QtWidgets.QLabel)}
    assert "Molecules examined: 4" in names
    assert "Total correction failure: yes" in names


@pytest.mark.parametrize(
    ("events", "state"),
    [
        ((), "APPARENT_REVIEW"),
        (("approve",), "CALIBRATION_APPROVED"),
        (("approve", "apply correction version"), "CORRECTED_REVIEW"),
    ],
)
def test_the_action_is_enabled_from_the_correction_state(
    shell, qtbot, tmp_path, events, state
) -> None:
    from tether.project import correction_states as cs

    project = _loaded(shell, tmp_path, factors=bool(events))
    held = shell.curation_project
    for event in events:
        cs.send_event(
            held,
            event,
            cs.Actor.person("curator"),
            gamma=1.1,
            factor_version="f1",
            correction_version="c1",
        )
    assert cs.read_correction_state(project.path).state.value == state

    shell.sync_correction_controls()

    action = shell.correction_pass_action
    assert not action.isEnabled()
    assert state in action.toolTip()
    assert shell.run_correction_pass() is False
    assert not shell.is_idealizing
    assert f"the correction state is {state}" in shell.messages.latest.text
    assert not _has_correction_settings(project.path)


def test_the_menu_syncs_the_action_when_it_opens(shell, tmp_path) -> None:
    _loaded(shell, tmp_path, factors=False)
    shell.correction_pass_action.setEnabled(True)

    shell.corrections_menu.aboutToShow.emit()

    assert not shell.correction_pass_action.isEnabled()
    # The reason is in the tooltip, and a QMenu shows tooltips only when asked to.
    assert shell.corrections_menu.toolTipsVisible()


def test_a_read_only_project_refuses_the_pass(shell, tmp_path) -> None:
    from tether.project import lock

    project = _store(tmp_path)
    other = lock.LockIdentity(host="OTHER-HOST", user="other", pid=999)
    lock.acquire(project.path, identity=other)
    try:
        assert shell.load_project(project.path) is not None
        assert shell.curation_project is None

        shell.sync_correction_controls()

        assert not shell.correction_pass_action.isEnabled()
        assert "read-only" in shell.correction_pass_action.toolTip()
        assert shell.run_correction_pass() is False
        assert not _has_correction_settings(project.path)
    finally:
        lock.release(project.path, lock.read_lock(project.path))


def test_the_pass_freezes_idealization_and_other_writes_while_it_runs(
    shell, qtbot, tmp_path, monkeypatch
) -> None:
    from tether.project import correct

    project = _loaded(shell, tmp_path)
    shell.molecule_list.setCurrentRow(0)
    real = correct.compute_corrected_fret
    started, go = threading.Event(), threading.Event()

    def held_open(*args, **kwargs):
        started.set()
        assert go.wait(10)
        return real(*args, **kwargs)

    monkeypatch.setattr(correct, "compute_corrected_fret", held_open)
    assert shell.run_correction_pass()
    try:
        assert started.wait(10)
        assert shell.is_idealizing  # the one task slot is taken: idealize cannot start
        assert shell.run_correction_pass() is False
        assert "background task is running" in shell.messages.latest.text
        assert shell._writable_selection("Accept") is None
        assert "unavailable" in shell.messages.latest.text
    finally:
        go.set()
    qtbot.waitUntil(lambda: not shell.is_idealizing, timeout=10_000)
    assert shell.correction_summary is not None
    assert _has_correction_settings(project.path)


@pytest.mark.parametrize("stolen", ["released", "replaced"])
def test_a_lock_that_changed_hands_during_the_pass_refuses_its_write(
    shell, qtbot, tmp_path, monkeypatch, stolen
) -> None:
    from tether.gui.messages import Level
    from tether.project import correct, lock

    project = _loaded(shell, tmp_path)
    real = correct.compute_corrected_fret
    other = lock.LockIdentity(host="OTHER-HOST", user="other", pid=999)

    def steal_first(path, **kwargs):
        assert lock.release(path, lock.acquire(path, identity=other, steal=True))
        if stolen == "replaced":
            lock.acquire(path, identity=other, steal=True)
        return real(path, **kwargs)

    monkeypatch.setattr(correct, "compute_corrected_fret", steal_first)

    assert shell.run_correction_pass()
    qtbot.waitUntil(lambda: not shell.is_idealizing, timeout=10_000)

    assert not _has_correction_settings(project.path)
    assert {_text(m) for m in _molecules(project.path)["correction_method"]} == {""}
    assert shell.correction_summary is None
    assert shell.messages.latest.text.startswith("Correction pass failed")
    assert shell.messages.latest.level is Level.ERROR
    assert shell.curation_project is None
    assert shell.read_only_reason is not None


@pytest.mark.parametrize("toggle", [False, True])
def test_manual_factors_refuse_the_pass_so_their_provenance_stays(
    shell, qtbot, tmp_path, toggle
) -> None:
    # A pass with no override would stamp the supplied factors "corrected" and write NaN
    # overrides (Codex P2 on PR #1383). The how-to's manual route, then the menu, refuses.
    import h5py

    from tether.project.correct import METHOD_MANUAL, compute_corrected_fret

    project = _store(tmp_path)
    compute_corrected_fret(
        project.path, alpha_override=0.1, gamma_override=1.2, apparent_e_only=toggle
    )
    with h5py.File(project.path, "r") as f:
        stamp_before = dict(f["settings"]["correction"].attrs)
    methods_before = [_text(m) for m in _molecules(project.path)["correction_method"]]
    assert (METHOD_MANUAL in methods_before) is not toggle
    assert shell.load_project(project.path) is not None

    shell.sync_correction_controls()

    assert not shell.correction_pass_action.isEnabled()
    assert "manual α or γ" in shell.correction_pass_action.toolTip()
    assert shell.run_correction_pass() is False
    assert [_text(m) for m in _molecules(project.path)["correction_method"]] == methods_before
    with h5py.File(project.path, "r") as f:
        assert dict(f["settings"]["correction"].attrs)["created_utc"] == stamp_before["created_utc"]


def test_the_service_refuses_manual_factors_before_it_writes(tmp_path) -> None:
    from tether.project.correct import compute_corrected_fret
    from tether.services.base import RequestRefused
    from tether.services.corrections import run_correction_pass

    project = _store(tmp_path)
    compute_corrected_fret(project.path, gamma_override=1.2)

    with pytest.raises(RequestRefused, match="manual"):
        run_correction_pass(project.path)


def test_the_window_keeps_its_claim_on_the_file_the_pass_published(
    shell, qapp, qtbot, tmp_path
) -> None:
    # The pass publishes a sibling copy with one atomic replace, so the file gets a new
    # identity. The window's in-process claim must follow it: else a second window takes
    # the same file writable, and the first window's close frees nothing.
    from tether.gui import shell as shell_mod
    from tether.gui.shell import TetherShell

    project = _loaded(shell, tmp_path)
    before = shell_mod._project_owner_key(project.path)
    assert {before: shell._project_owner_token} == shell_mod._GUI_PROJECT_OWNERS
    _run(shell, qtbot)
    after = shell_mod._project_owner_key(project.path)
    assert after != before, "the pass published a new file"
    assert shell._claimed_project_key == after
    assert {after: shell._project_owner_token} == shell_mod._GUI_PROJECT_OWNERS

    other = TetherShell(preferences_file=tmp_path / "other" / "preferences.json")
    qtbot.addWidget(other.window)
    try:
        other.load_project(project.path)
        assert not other.holds_project_claim
        assert other.curation_project is None
    finally:
        other.close()
    assert {after: shell._project_owner_token} == shell_mod._GUI_PROJECT_OWNERS


def test_the_claim_moves_only_for_its_owner_and_only_to_a_free_file(monkeypatch) -> None:
    from tether.gui import shell as shell_mod

    mine, theirs = object(), object()
    old, new = ("path", "old"), ("path", "new")
    owners = shell_mod._GUI_PROJECT_OWNERS
    monkeypatch.setattr(shell_mod, "_project_owner_key", lambda path: new)
    try:
        assert shell_mod._rebind_gui_project(None, "p", mine) is None
        owners[old] = theirs  # another token holds the old key: nothing moves
        assert shell_mod._rebind_gui_project(old, "p", mine) == old
        assert owners == {old: theirs}
        owners[old], owners[new] = mine, theirs  # the new file is taken: nothing moves
        assert shell_mod._rebind_gui_project(old, "p", mine) == old
        assert owners == {old: mine, new: theirs}
        del owners[new]
        assert shell_mod._rebind_gui_project(old, "p", mine) == new
        assert owners == {new: mine}
    finally:
        owners.clear()


def test_a_window_closed_during_the_pass_frees_the_moved_claim(
    shell, qtbot, tmp_path, monkeypatch
) -> None:
    # Closing the window abandons the task, so the pass never reaches its finish
    # handler. The release after the task must still free the claim on the new file.
    from tether.gui import shell as shell_mod
    from tether.project import correct

    project = _loaded(shell, tmp_path)
    before = shell_mod._project_owner_key(project.path)
    real = correct.compute_corrected_fret
    started, go = threading.Event(), threading.Event()

    def held_open(*args, **kwargs):
        started.set()
        assert go.wait(10)
        return real(*args, **kwargs)

    monkeypatch.setattr(correct, "compute_corrected_fret", held_open)
    assert shell.run_correction_pass()
    future = shell._idealize_future
    assert future is not None
    try:
        assert started.wait(10)
        shell.close()
    finally:
        go.set()
    future.result(timeout=10)
    qtbot.waitUntil(lambda: not shell_mod._GUI_PROJECT_OWNERS, timeout=10_000)
    assert shell_mod._project_owner_key(project.path) != before
    assert not shell.holds_project_claim
    assert _has_correction_settings(project.path)


def test_no_window_can_claim_the_file_between_the_publish_and_the_claim_move(
    shell, qapp, qtbot, tmp_path, monkeypatch
) -> None:
    # The pass replaces the file before its claim moves to the new file. Hold the task
    # right after the publish: a second window must still open the file read-only.
    from tether.gui import shell as shell_mod
    from tether.gui.shell import TetherShell
    from tether.project import correct

    project = _loaded(shell, tmp_path)
    before = shell_mod._project_owner_key(project.path)
    real = correct.compute_corrected_fret
    published, go = threading.Event(), threading.Event()

    def held_after_publish(*args, **kwargs):
        summary = real(*args, **kwargs)
        published.set()
        assert go.wait(10)
        return summary

    monkeypatch.setattr(correct, "compute_corrected_fret", held_after_publish)
    other = TetherShell(preferences_file=tmp_path / "other" / "preferences.json")
    qtbot.addWidget(other.window)
    try:
        assert shell.run_correction_pass()
        try:
            assert published.wait(10)
            assert shell_mod._project_owner_key(project.path) != before
            other.load_project(project.path)
            assert not other.holds_project_claim
            assert other.curation_project is None
        finally:
            go.set()
        qtbot.waitUntil(lambda: not shell.is_idealizing, timeout=10_000)
    finally:
        other.close()
    after = shell_mod._project_owner_key(project.path)
    assert {after: shell._project_owner_token} == shell_mod._GUI_PROJECT_OWNERS
    assert not shell_mod._GUI_PUBLISHING_PATHS


def test_closing_the_window_closes_the_summary(shell, qtbot, tmp_path) -> None:
    # The summary is a top-level window of its own, so it must not outlive the shell.
    _loaded(shell, tmp_path)
    summary = _run(shell, qtbot)
    assert summary.dialog.isVisible()
    shell.close()
    assert not summary.dialog.isVisible()
    assert shell.correction_summary is None


def test_closing_the_project_closes_the_summary(shell, qtbot, tmp_path) -> None:
    # #1393: the summary names no project, so it must not outlive the one it describes.
    _loaded(shell, tmp_path)
    summary = _run(shell, qtbot)
    assert summary is not None and summary.dialog.isVisible()

    assert shell.close_project()

    assert not summary.dialog.isVisible()
    assert shell.correction_summary is None


def test_opening_another_project_closes_the_summary(shell, qtbot, tmp_path) -> None:
    _loaded(shell, tmp_path)
    summary = _run(shell, qtbot)
    assert summary is not None and summary.dialog.isVisible()

    other_dir = tmp_path / "other"
    other_dir.mkdir()
    other = _store(other_dir)
    assert shell.load_project(other.path) is not None

    assert not summary.dialog.isVisible()
    assert shell.correction_summary is None
