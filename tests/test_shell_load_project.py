# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Open a produced / extracted ``.tether`` live in the shell (M7 PR #5e, PRD §7.8).

Covers the store↔shell hookup that closes the §7.8 "browse/curate/idealize round-trip
live" clause: :func:`tether.gui.shell.traces_from_store` (the store → ``list[TraceView]``
builder) and :meth:`TetherShell.load_project` (re-wire the running shell's store seams +
load molecules), plus the ``&File → Open project…`` reachability and the single-produced
auto-open from :meth:`TetherShell.import_deeplasi_bundle`.

All ``@pytest.mark.gui``. Stores are real: a round-trip ``.tether`` from the shared
``_analysis_store.build_store_with_channels`` (coordinates + patches → overlap available)
and a coordinate-less analysis-only ``.tether`` from
:func:`~tether.project.analysis_import.import_analysis_only_project` over the committed
``smd_4mol.hdf5`` (movie-less → overlap gated off, banner surfaced).
"""

from __future__ import annotations

import importlib.util
import os
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("h5py")

_HAS_QT = all(importlib.util.find_spec(m) is not None for m in ("pyqtgraph", "PySide6"))
_needs_qt = pytest.mark.skipif(not _HAS_QT, reason="pyqtgraph/PySide6 not installed")

pytestmark = [pytest.mark.gui, _needs_qt]

_FIXTURES = Path(__file__).parent / "fixtures"


# --------------------------------------------------------------------------- #
# fixtures / helpers
# --------------------------------------------------------------------------- #


@pytest.fixture
def shell(qapp, qtbot):
    from tether.gui.shell import TetherShell

    s = TetherShell()
    qtbot.addWidget(s.window)
    yield s
    s.close()


class _StubWizardDialog:
    """A stand-in for ``DeepLasiWizardDialog`` returning preset produced paths."""

    def __init__(self, produced) -> None:
        self._produced = tuple(produced)

    def exec(self):
        return self._produced


def _round_trip_store(tmp_path, *, n=3, t=12, name="rt.tether", seed=1):
    """A real round-trip ``.tether`` (coords + patches) → ``(Project, keys, donor, acceptor)``."""
    from _analysis_store import build_store_with_channels

    rng = np.random.default_rng(seed)
    donor = rng.uniform(400.0, 800.0, size=(n, t))
    acceptor = rng.uniform(200.0, 600.0, size=(n, t))
    project, keys = build_store_with_channels(tmp_path, donor, acceptor, name=name)
    return project, keys, donor, acceptor


def _analysis_only_store(tmp_path, *, name="ao.tether"):
    """A movie-less, coordinate-less analysis-only ``.tether`` from the committed SMD."""
    from tether.idealize import read_smd
    from tether.project.analysis_import import import_analysis_only_project

    smd = read_smd(_FIXTURES / "smd_4mol.hdf5")
    out = tmp_path / name
    import_analysis_only_project(out, source=smd, source_name="smd_4mol.hdf5")
    return out


def _blocking_idealization_runner(project, started, gate, seen_nonces):
    """Return a real store runner held behind ``gate`` after recording its lock epoch."""
    from tether.idealize import IdealizationResult, StateModel, read_smd

    def runner(smd_path, *, nstates, model_type="vbconhmm", **_kwargs):
        current = project.lock_owner()
        assert current is not None
        seen_nonces.append(current.nonce)
        started.set()
        assert gate.wait(timeout=5.0)

        smd = read_smd(smd_path)
        n, t = smd.n_molecules, smd.n_frames
        means = np.linspace(0.2, 0.8, nstates)
        model = StateModel(
            model_type=model_type,
            nstates=nstates,
            means=means,
            variances=np.full(nstates, 0.01),
            tmatrix=np.eye(nstates),
            norm_tmatrix=np.eye(nstates) * 0.9,
            elbo=-3.0,
            dtype="FRET",
            idealized=np.full((n, t), means[0]),
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

    return runner


# --------------------------------------------------------------------------- #
# traces_from_store — the store → list[TraceView] builder
# --------------------------------------------------------------------------- #


def test_traces_from_store_builds_keyed_full_frame_views(qapp, tmp_path) -> None:
    from tether.gui.shell import traces_from_store

    project, keys, donor, acceptor = _round_trip_store(tmp_path, n=3, t=12)
    views = traces_from_store(project)

    assert len(views) == 3
    # every view carries its store molecule_key (so the idealize / overlap seams resolve)
    assert [v.molecule_key for v in views] == keys
    for i, view in enumerate(views):
        # the full native frame_range slice (curation shows the whole trace)
        assert view.donor.shape == (12,)
        assert view.acceptor.shape == (12,)
        # the corrected layer holds the seeded intensities (float32 store), right channel
        np.testing.assert_allclose(view.donor, donor[i].astype(np.float32))
        np.testing.assert_allclose(view.acceptor, acceptor[i].astype(np.float32))
        # the seeded MovieMetadata has no frame interval → 0.0 → None (frame-index axis)
        assert view.frame_time is None


def test_traces_from_store_reads_positive_frame_time(qapp, tmp_path) -> None:
    import h5py

    from tether.gui.shell import traces_from_store

    project, keys, *_ = _round_trip_store(tmp_path, n=2, t=10)
    # A real movie carries a positive seconds/frame; the extractor writes 0.0 only when
    # the TIFF interval is unknown. Stamp a real interval onto the movie row and confirm
    # it flows through to every TraceView (the value branch of _movie_frame_times).
    with h5py.File(project.path, "r+") as f:
        table = f["movies"]["table"][:]
        table["frame_time"][:] = 0.05
        f["movies"]["table"][:] = table

    views = traces_from_store(project)

    assert len(views) == 2
    assert all(v.frame_time == pytest.approx(0.05) for v in views)


def test_traces_from_store_empty_store_returns_empty(qapp, tmp_path) -> None:
    from tether.gui.shell import traces_from_store
    from tether.io.schema import create_project
    from tether.project.core import Project

    path = create_project(tmp_path / "empty.tether")
    assert traces_from_store(Project.open(path)) == []


def test_traces_from_store_rejects_unknown_quantity(qapp, tmp_path) -> None:
    from tether.gui.shell import traces_from_store

    project, *_ = _round_trip_store(tmp_path)
    with pytest.raises(ValueError, match="intensity_quantity"):
        traces_from_store(project, intensity_quantity="bogus")


def test_traces_from_store_analysis_only_is_movie_less(qapp, tmp_path) -> None:
    from tether.gui.shell import traces_from_store

    out = _analysis_only_store(tmp_path)
    views = traces_from_store(out)  # accepts a bare path, not only a Project

    assert len(views) == 4
    assert all(v.frame_time is None for v in views)  # movie-less → no frame interval
    assert all(v.donor.shape == (1700,) for v in views)
    assert all(v.molecule_key for v in views)


def test_traces_from_store_analysis_only_has_no_raw_layer(qapp, tmp_path) -> None:
    # An analysis-only store writes only the corrected layers; asking for "raw" must
    # raise a clear error, not KeyError deep in h5py.
    from tether.gui.shell import traces_from_store

    out = _analysis_only_store(tmp_path)
    with pytest.raises(ValueError, match="raw"):
        traces_from_store(out, intensity_quantity="raw")


# --------------------------------------------------------------------------- #
# TetherShell.load_project — re-wire the running shell + load molecules
# --------------------------------------------------------------------------- #


def test_load_project_opens_round_trip_store_live(shell, tmp_path) -> None:
    project, keys, *_ = _round_trip_store(tmp_path, n=3, t=12)

    opened = shell.load_project(project.path)

    assert opened is not None
    assert opened.lock_owner() is not None
    assert shell.molecule_list.count() == 3
    assert "3 molecule(s)" in shell.status_message
    # histogram seam wired → &Analysis draws over the real store
    assert shell.show_histogram() is not None
    # overlap seam wired (coords + patches present) → selecting row 0 built the dock
    assert shell.overlap_dock is not None


def test_second_shell_in_process_cannot_share_writable_project(qapp, qtbot, tmp_path) -> None:
    from pyqtgraph.Qt import QtCore

    from tether.gui.shell import TetherShell
    from tether.project.labels import read_labels

    project, *_ = _round_trip_store(tmp_path, n=3, t=12)
    first = TetherShell()
    second = TetherShell()
    qtbot.addWidget(first.window)
    qtbot.addWidget(second.window)
    try:
        assert first.load_project(project.path) is not None
        assert second.load_project(project.path) is not None
        assert second._curation_project is None
        assert "another Tether window" in second.status_message

        qtbot.keyClick(second.molecule_list, QtCore.Qt.Key.Key_Space)
        assert read_labels(project.path).shape == (0,)
        assert "read-only" in second.status_message

        first.close()
        assert second.load_project(project.path) is not None
        assert second._curation_project is not None
    finally:
        first.close()
        second.close()


def test_hard_link_alias_cannot_create_a_second_writable_gui_claim(qapp, qtbot, tmp_path) -> None:
    from tether.gui.shell import TetherShell
    from tether.project import lock

    project, *_ = _round_trip_store(tmp_path, n=3, t=12)
    alias = tmp_path / "hard-link-alias.tether"
    os.link(project.path, alias)
    first = TetherShell()
    second = TetherShell()
    qtbot.addWidget(first.window)
    qtbot.addWidget(second.window)
    try:
        assert first.load_project(project.path) is not None
        assert second.load_project(alias) is not None

        assert second._curation_project is None
        assert "another Tether window" in second.status_message
        assert not lock.lock_path(alias).exists()
        assert not lock._guard_path(lock.lock_path(alias)).exists()

        first.close()
        assert second.load_project(alias) is not None
        assert second._curation_project is not None
    finally:
        first.close()
        second.close()


def test_read_only_project_keeps_export_but_disables_return_import(shell, tmp_path) -> None:
    from tether.project import lock
    from tether.project.lock import LockIdentity

    project, *_ = _round_trip_store(tmp_path, n=3, t=12)
    held = lock.acquire(
        project.path, identity=LockIdentity(host="OTHER-HOST", user="other", pid=999)
    )
    try:
        assert shell.load_project(project.path) is not None
        out = tmp_path / "read-only-export.hdf5"
        manifest = shell.hand_off_to_tmaven(out)

        assert manifest is not None
        assert manifest.n_molecules == 3
        assert out.exists()
        assert shell._act_hand_off.isEnabled()
        assert not shell._act_import.isEnabled()
        assert shell.import_return_leg(tmp_path / "return.hdf5") is None
        assert "Import unavailable: project is read-only" in shell.status_message
    finally:
        assert lock.release(project.path, held)


def test_project_file_must_open_writable_before_gui_enables_mutations(
    shell, tmp_path, monkeypatch
) -> None:
    project, *_ = _round_trip_store(tmp_path, n=3, t=12)

    def deny_write(_path) -> None:
        raise OSError("project file is read-only")

    monkeypatch.setattr(
        "tether.gui.shell._probe_project_writable",
        deny_write,
        raising=False,
    )

    assert shell.load_project(project.path) is not None

    assert shell._curation_project is None
    assert project.lock_owner() is None
    assert "project file is not writable" in shell.status_message
    assert "project file is read-only" in shell.status_message


def test_gui_session_lock_refreshes_before_stale_timeout(shell, tmp_path) -> None:
    from tether.project.lock import DEFAULT_STALENESS_TIMEOUT_S

    project, *_ = _round_trip_store(tmp_path, n=3, t=12)
    assert shell.load_project(project.path) is not None
    before = project.lock_owner()
    assert before is not None
    assert shell._lock_refresh_timer.isActive()
    assert shell._lock_refresh_timer.interval() < DEFAULT_STALENESS_TIMEOUT_S * 1000

    shell._refresh_project_lock()

    after = project.lock_owner()
    assert after is not None
    assert after.identity == before.identity
    assert after.nonce == before.nonce
    assert datetime.fromisoformat(after.timestamp) > datetime.fromisoformat(before.timestamp)
    shell.close()
    assert not shell._lock_refresh_timer.isActive()
    assert project.lock_owner() is None


def test_reopening_loaded_project_preserves_session_epoch(shell, tmp_path) -> None:
    project, *_ = _round_trip_store(tmp_path, n=3, t=12)
    retained = shell.load_project(project.path)
    assert retained is not None
    before = project.lock_owner()
    assert before is not None

    reopened = shell.load_project(project.path)

    after = project.lock_owner()
    assert reopened is retained
    assert shell._session_project is retained
    assert shell._curation_project is retained
    assert after is not None
    assert after.nonce == before.nonce
    assert not shell._lock_release_timer.isActive()


def test_idealization_rejects_lost_epoch_without_session_reacquire(shell, qtbot, tmp_path) -> None:
    import threading

    from tether.gui.shell import make_store_idealizer
    from tether.project import lock
    from tether.project.idealize import list_idealizations
    from tether.project.lock import LockIdentity

    project, keys, *_ = _round_trip_store(tmp_path, n=3, t=12)
    session_project = shell.load_project(project.path)
    assert session_project is not None
    started = threading.Event()
    gate = threading.Event()
    seen_nonces = []

    shell._idealizer = make_store_idealizer(
        session_project,
        nstates=2,
        require_held_lock=True,
        _runner=_blocking_idealization_runner(
            session_project,
            started,
            gate,
            seen_nonces,
        ),
    )
    shell._idealize_current()
    try:
        qtbot.waitUntil(started.is_set, timeout=2000)
        assert len(seen_nonces) == 1
        start_nonce = seen_nonces[0]
        retained = project.lock_owner()
        assert retained is not None
        assert retained.nonce == start_nonce

        foreign = lock.acquire(
            project.path,
            identity=LockIdentity(host="OTHER-HOST", user="other", pid=999),
            steal=True,
        )
        assert lock.release(project.path, foreign)

        # Exercise the live GUI timer seam after the retained epoch vanished. It must
        # fail closed instead of silently establishing a successor session.
        shell._refresh_project_lock()
        assert project.lock_owner() is None
        assert shell._curation_project is None
        assert shell._curation_read_only_reason is not None
    finally:
        gate.set()
        qtbot.waitUntil(lambda: not shell.is_idealizing, timeout=5000)

    assert list_idealizations(project) == []
    assert "Idealize failed" in shell.status_message
    assert project.lock_owner() is None
    assert shell._curation_project is None
    assert project.curation_label(keys[0]) == 0


def test_idealization_survives_legitimate_same_epoch_session_refresh(
    shell, qtbot, tmp_path
) -> None:
    import threading

    from tether.gui.shell import make_store_idealizer
    from tether.project.idealize import list_idealizations

    project, *_ = _round_trip_store(tmp_path, n=3, t=12)
    session_project = shell.load_project(project.path)
    assert session_project is not None
    started = threading.Event()
    gate = threading.Event()
    seen_nonces = []
    shell._idealizer = make_store_idealizer(
        session_project,
        nstates=2,
        require_held_lock=True,
        _runner=_blocking_idealization_runner(
            session_project,
            started,
            gate,
            seen_nonces,
        ),
    )

    shell._idealize_current()
    try:
        qtbot.waitUntil(started.is_set, timeout=2000)
        assert len(seen_nonces) == 1
        shell._refresh_project_lock()
        refreshed = session_project.lock_owner()
        assert refreshed is not None
        assert refreshed.nonce == seen_nonces[0]
    finally:
        gate.set()
        qtbot.waitUntil(lambda: not shell.is_idealizing, timeout=5000)

    assert list_idealizations(project) == ["vbfret"]
    assert "Idealized" in shell.status_message
    assert session_project.lock_owner() == refreshed


def test_lost_session_lock_disables_gui_writes_and_refresh(shell, tmp_path) -> None:
    from tether.project import lock
    from tether.project.lock import LockIdentity

    project, *_ = _round_trip_store(tmp_path, n=3, t=12)
    assert shell.load_project(project.path) is not None
    foreign = lock.acquire(
        project.path,
        identity=LockIdentity(host="OTHER-HOST", user="other", pid=999),
        steal=True,
    )
    try:
        shell._refresh_project_lock()

        assert shell._curation_project is None
        assert shell._idealizer is None
        assert not shell._lock_refresh_timer.isActive()
        assert "became read-only" in shell.status_message
        assert "locked" in shell.status_message
    finally:
        assert lock.release(project.path, foreign)


def test_definitive_lock_loss_drops_session_claim_without_retry(
    shell, qapp, qtbot, tmp_path
) -> None:
    from tether.gui.shell import TetherShell
    from tether.project import lock
    from tether.project.lock import LockIdentity

    project, *_ = _round_trip_store(tmp_path, n=3, t=12)
    retained = shell.load_project(project.path)
    assert retained is not None
    foreign = lock.acquire(
        project.path,
        identity=LockIdentity(host="OTHER-HOST", user="other", pid=999),
        steal=True,
    )

    shell._refresh_project_lock()

    assert shell._curation_project is None
    assert shell._session_project is None
    assert shell._claimed_project_key is None
    assert retained._held_lock is None
    assert not shell._lock_release_timer.isActive()

    assert lock.release(project.path, foreign)
    successor = TetherShell()
    qtbot.addWidget(successor.window)
    try:
        opened = successor.load_project(project.path)
        assert opened is not None
        assert successor._curation_project is opened
    finally:
        successor.close()


def test_lock_loss_closes_open_conditions_dialog_before_unlocked_write(
    shell, tmp_path, monkeypatch
) -> None:
    from tether.project import lock
    from tether.project.lock import LockIdentity

    project, *_ = _round_trip_store(tmp_path, n=3, t=12)
    session_project = shell.load_project(project.path)
    assert session_project is not None
    sync_calls = []

    def record_sync():
        sync_calls.append(True)

    monkeypatch.setattr(session_project, "sync_conditions", record_sync)

    class StealThenReleaseDialog:
        instance = None

        def __init__(self, target, *, writer_guard, parent):
            self._project = target
            self._writer_guard = writer_guard
            self.dialog = self
            self.rejected = False
            type(self).instance = self

        def reject(self):
            self.rejected = True

        def exec(self):
            foreign = lock.acquire(
                project.path,
                identity=LockIdentity(host="OTHER-HOST", user="other", pid=999),
                steal=True,
            )
            try:
                shell._refresh_project_lock()
            finally:
                assert lock.release(project.path, foreign)
            if not self.rejected:
                self._project.sync_conditions()

    monkeypatch.setattr(
        "tether.gui.conditions.ConditionValidationDialog",
        StealThenReleaseDialog,
    )

    shell._validate_conditions_dialog()

    assert StealThenReleaseDialog.instance is not None
    assert StealThenReleaseDialog.instance.rejected
    assert sync_calls == []
    assert shell._curation_project is None
    assert project.lock_owner() is None
    assert "became read-only" in shell.status_message
    assert "Conditions validation closed" not in shell.status_message


def test_conditions_dialog_guard_detects_steal_release_between_heartbeats(
    shell, tmp_path, monkeypatch
) -> None:
    from tether.project import lock
    from tether.project.lock import LockedError, LockIdentity

    project, *_ = _round_trip_store(tmp_path, n=3, t=12)
    session_project = shell.load_project(project.path)
    assert session_project is not None
    sync_calls = []

    def record_sync():
        sync_calls.append(True)

    monkeypatch.setattr(session_project, "sync_conditions", record_sync)

    class BetweenHeartbeatDialog:
        instance = None

        def __init__(self, target, *, writer_guard, parent):
            self._project = target
            self._writer_guard = writer_guard
            self.dialog = self
            self.rejected = False
            type(self).instance = self

        def reject(self):
            self.rejected = True

        def exec(self):
            foreign = lock.acquire(
                project.path,
                identity=LockIdentity(host="OTHER-HOST", user="other", pid=999),
                steal=True,
            )
            assert lock.release(project.path, foreign)
            try:
                self._writer_guard()
            except LockedError:
                return
            self._project.sync_conditions()

    monkeypatch.setattr(
        "tether.gui.conditions.ConditionValidationDialog",
        BetweenHeartbeatDialog,
    )

    shell._validate_conditions_dialog()

    assert BetweenHeartbeatDialog.instance is not None
    assert BetweenHeartbeatDialog.instance.rejected
    assert sync_calls == []
    assert shell._curation_project is None
    assert project.lock_owner() is None
    assert "became read-only" in shell.status_message
    assert "Conditions validation closed" not in shell.status_message


def test_refresh_io_failure_retries_nonce_safe_session_release(
    shell, tmp_path, monkeypatch
) -> None:
    from tether.project.core import Project

    project, *_ = _round_trip_store(tmp_path, n=3, t=12)
    assert shell.load_project(project.path) is not None
    original_release = Project.release_lock
    release_calls = 0

    def fail_refresh(_project, **_kwargs):
        raise OSError("temporary network-share failure")

    def flaky_release(project_ref):
        nonlocal release_calls
        release_calls += 1
        if release_calls == 1:
            raise OSError("temporary unlink failure")
        return original_release(project_ref)

    monkeypatch.setattr(Project, "refresh_lock", fail_refresh)
    monkeypatch.setattr(Project, "release_lock", flaky_release)

    shell._refresh_project_lock()

    assert shell._curation_project is None
    assert shell._session_project is not None
    assert project.lock_owner() is not None
    assert release_calls == 1
    assert shell._lock_release_timer.isActive()

    shell._retry_session_release()

    assert release_calls == 2
    assert shell._session_project is None
    assert project.lock_owner() is None
    assert not shell._lock_release_timer.isActive()


def test_close_retries_transient_nonce_safe_session_release(shell, tmp_path, monkeypatch) -> None:
    from tether.project.core import Project

    project, *_ = _round_trip_store(tmp_path, n=3, t=12)
    assert shell.load_project(project.path) is not None
    original_release = Project.release_lock
    release_calls = 0

    def flaky_release(project_ref):
        nonlocal release_calls
        release_calls += 1
        if release_calls == 1:
            raise OSError("temporary unlink failure")
        return original_release(project_ref)

    monkeypatch.setattr(Project, "release_lock", flaky_release)

    shell.close()

    assert release_calls == 1
    assert shell._session_project is not None
    assert shell._claimed_project_key is not None
    assert project.lock_owner() is not None
    assert shell._lock_release_timer.isActive()

    shell._retry_session_release()

    assert release_calls == 2
    assert shell._session_project is None
    assert shell._claimed_project_key is None
    assert project.lock_owner() is None
    assert not shell._lock_release_timer.isActive()


def test_close_retries_indeterminate_false_session_release(shell, tmp_path, monkeypatch) -> None:
    from tether.project import lock

    project, *_ = _round_trip_store(tmp_path, n=3, t=12)
    assert shell.load_project(project.path) is not None
    original_release = lock.release
    release_calls = 0

    def false_then_release(project_path, info):
        nonlocal release_calls
        release_calls += 1
        if release_calls == 1:
            return False
        return original_release(project_path, info)

    monkeypatch.setattr(lock, "release", false_then_release)

    shell.close()

    assert release_calls == 1
    assert shell._session_project is not None
    assert shell._session_project._held_lock is not None
    assert shell._claimed_project_key is not None
    assert project.lock_owner() is not None
    assert shell._lock_release_timer.isActive()

    shell._retry_session_release()

    assert release_calls == 2
    assert shell._session_project is None
    assert shell._claimed_project_key is None
    assert project.lock_owner() is None
    assert not shell._lock_release_timer.isActive()


def test_project_switch_retains_new_lock_when_both_releases_fail(
    shell, tmp_path, monkeypatch
) -> None:
    from tether.project.core import Project

    first, *_ = _round_trip_store(tmp_path, n=3, t=12, name="first.tether")
    second, *_ = _round_trip_store(tmp_path, n=3, t=12, name="second.tether")
    assert shell.load_project(first.path) is not None
    original_release = Project.release_lock
    failed_paths: set[Path] = set()

    def fail_once_per_project(project_ref):
        path = project_ref.path
        if path not in failed_paths:
            failed_paths.add(path)
            raise OSError(f"temporary unlink failure for {path.name}")
        return original_release(project_ref)

    monkeypatch.setattr(Project, "release_lock", fail_once_per_project)

    assert shell.load_project(second.path) is None

    assert shell._curation_project is not None
    assert shell._curation_project.path == first.path
    assert shell._session_project is not None
    assert shell._session_project.path == first.path
    assert shell._rollback_session_project is not None
    assert shell._rollback_session_project.path == second.path
    assert shell._rollback_claimed_project_key is not None
    assert second.lock_owner() is not None
    assert shell._lock_release_timer.isActive()

    shell._retry_session_release()

    assert shell._rollback_session_project is None
    assert shell._rollback_claimed_project_key is None
    assert second.lock_owner() is None
    assert first.lock_owner() is not None
    assert not shell._lock_release_timer.isActive()


def test_project_switch_fails_closed_when_prior_session_epoch_was_lost(
    shell, qtbot, tmp_path
) -> None:
    from pyqtgraph.Qt import QtCore

    from tether.project import lock
    from tether.project.labels import read_labels
    from tether.project.lock import LockIdentity

    first, *_ = _round_trip_store(tmp_path, n=3, t=12, name="first.tether")
    second, *_ = _round_trip_store(tmp_path, n=3, t=12, name="second.tether")
    retained = shell.load_project(first.path)
    assert retained is not None
    foreign = lock.acquire(
        first.path,
        identity=LockIdentity(host="OTHER-HOST", user="other", pid=999),
        steal=True,
    )
    assert lock.release(first.path, foreign)
    assert first.lock_owner() is None

    assert shell.load_project(second.path) is None

    assert retained._held_lock is None
    assert shell._curation_project is None
    assert shell._session_project is None
    assert shell._claimed_project_key is None
    assert shell._curation_read_only_reason is not None
    assert "session lock lost" in shell._curation_read_only_reason
    assert not shell._lock_refresh_timer.isActive()
    assert first.lock_owner() is None
    assert second.lock_owner() is None

    qtbot.keyClick(shell.molecule_list, QtCore.Qt.Key.Key_Space)

    assert read_labels(first.path).shape == (0,)
    assert "Accept unavailable" in shell.status_message
    assert "read-only" in shell.status_message


@pytest.mark.parametrize(
    ("key_name", "method_name", "expected_label", "success_text"),
    [
        ("Key_Space", "accept", 1, "Accepted"),
        ("Key_Backspace", "reject", -1, "Rejected"),
    ],
)
def test_real_curation_keys_persist_selected_label_and_survive_reopen(
    qapp, qtbot, tmp_path, monkeypatch, key_name, method_name, expected_label, success_text
) -> None:
    from pyqtgraph.Qt import QtCore

    from tether.gui.shell import TetherShell
    from tether.project.core import Project
    from tether.project.labels import read_labels

    def text(value) -> str:
        return value.decode() if isinstance(value, bytes) else str(value)

    project, keys, *_ = _round_trip_store(tmp_path, n=3, t=12)
    shell = TetherShell()
    qtbot.addWidget(shell.window)
    try:
        assert shell.load_project(project.path) is not None
        shell.molecule_list.setCurrentRow(1)
        writer = getattr(Project, method_name)

        def checked_writer(project_ref, molecule_key):
            assert success_text not in shell.status_message
            return writer(project_ref, molecule_key)

        monkeypatch.setattr(Project, method_name, checked_writer)

        qtbot.keyClick(shell.molecule_list, getattr(QtCore.Qt.Key, key_name))

        assert success_text in shell.status_message
    finally:
        shell.close()

    reopened = Project.open(project.path)
    assert reopened.lock_owner() is None
    rows = read_labels(reopened.path)
    assert rows.shape == (1,)
    row = rows[0]
    assert text(row["molecule_key"]) == keys[1]
    assert int(row["label_value"]) == expected_label
    assert text(row["source"]) == "human"
    assert text(row["source_file"]) == project.path.name
    assert text(row["labeler"])
    assert datetime.fromisoformat(text(row["timestamp"])).tzinfo is not None
    assert reopened.curation_label(keys[1]) == expected_label


def test_unreject_button_persists_reversal_and_noops_when_not_rejected(
    shell, qtbot, tmp_path
) -> None:
    from pyqtgraph.Qt import QtCore

    from tether.gui.curation import Command, CurationAction
    from tether.project.labels import read_labels

    project, keys, *_ = _round_trip_store(tmp_path, n=3, t=12)
    assert shell.load_project(project.path) is not None
    shell.molecule_list.setCurrentRow(1)
    qtbot.keyClick(shell.molecule_list, QtCore.Qt.Key.Key_Backspace)
    assert project.curation_label(keys[1]) == -1

    qtbot.mouseClick(shell.unreject_button, QtCore.Qt.MouseButton.LeftButton)

    rows = read_labels(project.path)
    assert rows.shape == (2,)
    assert [int(value) for value in rows["label_value"]] == [-1, 0]
    assert project.curation_label(keys[1]) == 0
    assert shell.controller.last == Command(CurationAction.UNREJECT)
    assert "Un-rejected" in shell.status_message

    qtbot.mouseClick(shell.unreject_button, QtCore.Qt.MouseButton.LeftButton)
    assert read_labels(project.path).shape == (2,)
    assert "not rejected" in shell.status_message
    assert "Un-rejected" not in shell.status_message


def test_one_click_idealize_skips_rejected_selection(shell, qtbot, tmp_path) -> None:
    project, keys, *_ = _round_trip_store(tmp_path, n=3, t=12)
    assert shell.load_project(project.path) is not None
    shell.molecule_list.setCurrentRow(1)
    shell._curation_project.reject(keys[1])
    calls: list[str] = []

    def idealize(molecule_key):
        calls.append(molecule_key)
        return np.full(12, 0.5)

    shell._idealizer = idealize
    shell._idealize_current()
    qtbot.waitUntil(lambda: not shell.is_idealizing, timeout=5000)

    assert calls == []
    assert "rejected" in shell.status_message


def test_curation_without_selection_adds_no_row_and_reports_action(shell, qtbot, tmp_path) -> None:
    from pyqtgraph.Qt import QtCore

    from tether.project.labels import read_labels

    project, *_ = _round_trip_store(tmp_path, n=3, t=12)
    assert shell.load_project(project.path) is not None
    shell.molecule_list.setCurrentRow(-1)

    qtbot.keyClick(shell.molecule_list, QtCore.Qt.Key.Key_Space)

    assert read_labels(project.path).shape == (0,)
    assert "select a project molecule" in shell.status_message
    assert "Accepted" not in shell.status_message


def test_curation_locked_project_adds_no_row_and_reports_lock(shell, qtbot, tmp_path) -> None:
    from pyqtgraph.Qt import QtCore

    from tether.project import lock
    from tether.project.labels import read_labels
    from tether.project.lock import LockIdentity

    project, *_ = _round_trip_store(tmp_path, n=3, t=12)
    held = lock.acquire(
        project.path, identity=LockIdentity(host="OTHER-HOST", user="other", pid=999)
    )
    try:
        assert shell.load_project(project.path) is not None
        assert "read-only" in shell.status_message
        assert "locked" in shell.status_message
        qtbot.keyClick(shell.molecule_list, QtCore.Qt.Key.Key_Space)
    finally:
        assert lock.release(project.path, held) is True

    assert read_labels(project.path).shape == (0,)
    assert "Accept unavailable" in shell.status_message
    assert "read-only" in shell.status_message
    assert "Accepted" not in shell.status_message


def test_read_only_banner_persists_when_navigation_replaces_status(shell, tmp_path) -> None:
    from tether.project import lock
    from tether.project.lock import LockIdentity

    project, *_ = _round_trip_store(tmp_path, n=3, t=12)
    held = lock.acquire(
        project.path, identity=LockIdentity(host="OTHER-HOST", user="other", pid=999)
    )
    try:
        assert shell.load_project(project.path) is not None
        banner_text = shell.read_only_banner.text()
        assert not shell.read_only_banner.isHidden()
        assert "Read-only" in banner_text
        assert "locked" in banner_text
        assert not shell.unreject_button.isEnabled()

        shell.molecule_list.setCurrentRow(1)

        assert "Molecule" in shell.status_message
        assert not shell.read_only_banner.isHidden()
        assert shell.read_only_banner.text() == banner_text
    finally:
        assert lock.release(project.path, held)


def test_floating_browser_remains_in_persistent_shortcut_scope(shell, tmp_path) -> None:
    from pyqtgraph.Qt import QtCore, QtGui

    project, keys, *_ = _round_trip_store(tmp_path, n=3, t=12)
    assert shell.load_project(project.path) is not None
    shell.browser_dock.setFloating(True)
    assert shell.molecule_list.window() is shell.browser_dock
    event = QtGui.QKeyEvent(
        QtCore.QEvent.Type.KeyPress,
        QtCore.Qt.Key.Key_Space,
        QtCore.Qt.KeyboardModifier.NoModifier,
    )

    consumed = shell.event_filter.filter_event(
        shell.molecule_list,
        event,
        focus_widget=shell.molecule_list,
    )

    assert consumed
    assert project.curation_label(keys[0]) == 1


def test_curation_waits_for_background_idealization(shell, qtbot, tmp_path) -> None:
    import threading

    from pyqtgraph.Qt import QtCore

    from tether.project.labels import read_labels

    project, *_ = _round_trip_store(tmp_path, n=3, t=12)
    assert shell.load_project(project.path) is not None
    started = threading.Event()
    gate = threading.Event()

    def blocking_idealizer(_molecule_key):
        started.set()
        gate.wait(timeout=5.0)
        return np.full(12, 0.5)

    shell._idealizer = blocking_idealizer
    try:
        qtbot.keyClick(shell.molecule_list, QtCore.Qt.Key.Key_I)
        qtbot.waitUntil(started.is_set, timeout=2000)
        qtbot.keyClick(shell.molecule_list, QtCore.Qt.Key.Key_Space)

        assert read_labels(project.path).shape == (0,)
        assert "Idealize in progress" in shell.status_message
        assert "Accepted" not in shell.status_message
    finally:
        gate.set()
        qtbot.waitUntil(lambda: not shell.is_idealizing, timeout=5000)


def test_every_source_writer_waits_for_background_idealization(
    shell, qtbot, tmp_path, monkeypatch
) -> None:
    import threading

    from pyqtgraph.Qt import QtCore

    from tether.project.labels import read_labels

    project, *_ = _round_trip_store(tmp_path, n=3, t=12)
    assert shell.load_project(project.path) is not None

    class SpyHandoff:
        preview_calls = 0

        def preview(self, *_args, **_kwargs):
            self.preview_calls += 1
            raise AssertionError("return-leg preview must not run during idealization")

    conditions_dialog_calls = []

    class SpyConditionsDialog:
        def __init__(self, target, *, parent):
            conditions_dialog_calls.append((target, parent))

        def exec(self):
            return None

    handoff = SpyHandoff()
    shell._handoff = handoff
    monkeypatch.setattr(
        "tether.gui.conditions.ConditionValidationDialog",
        SpyConditionsDialog,
    )

    started = threading.Event()
    gate = threading.Event()

    def blocking_idealizer(_molecule_key):
        started.set()
        gate.wait(timeout=5.0)
        return np.full(12, 0.5)

    shell._idealizer = blocking_idealizer
    try:
        qtbot.keyClick(shell.molecule_list, QtCore.Qt.Key.Key_I)
        qtbot.waitUntil(started.is_set, timeout=2000)

        assert shell.import_return_leg(tmp_path / "return.hdf5") is None
        import_status = shell.status_message
        shell._validate_conditions_dialog()
        conditions_status = shell.status_message
        qtbot.keyClick(shell.molecule_list, QtCore.Qt.Key.Key_Space)
        curation_status = shell.status_message

        assert handoff.preview_calls == 0
        assert conditions_dialog_calls == []
        assert read_labels(project.path).shape == (0,)
        assert "Idealize in progress" in import_status
        assert "Idealize in progress" in conditions_status
        assert "Idealize in progress" in curation_status
        assert not shell.unreject_button.isEnabled()
        assert not shell._act_import.isEnabled()
        assert not shell._act_validate_conditions.isEnabled()
    finally:
        gate.set()
        qtbot.waitUntil(lambda: not shell.is_idealizing, timeout=5000)

    assert shell.unreject_button.isEnabled()
    assert shell._act_import.isEnabled()
    assert shell._act_validate_conditions.isEnabled()


def test_close_retains_session_lock_until_background_idealization_finishes(
    shell, qtbot, tmp_path
) -> None:
    import threading

    from pyqtgraph.Qt import QtCore

    project, *_ = _round_trip_store(tmp_path, n=3, t=12)
    assert shell.load_project(project.path) is not None
    started = threading.Event()
    gate = threading.Event()

    def blocking_idealizer(_molecule_key):
        started.set()
        gate.wait(timeout=5.0)
        return np.full(12, 0.5)

    shell._idealizer = blocking_idealizer
    qtbot.keyClick(shell.molecule_list, QtCore.Qt.Key.Key_I)
    qtbot.waitUntil(started.is_set, timeout=2000)

    shell.close()
    assert project.lock_owner() is not None

    gate.set()
    # Observe the sidecar without opening it: repeatedly calling lock_owner()
    # here can race the done callback's unlink on Windows and itself hold the
    # file open long enough to cause a sharing violation.
    qtbot.waitUntil(lambda: not project.lock_path.exists(), timeout=5000)


def test_close_retries_release_after_background_idealization(
    shell, qtbot, tmp_path, monkeypatch
) -> None:
    import threading

    from pyqtgraph.Qt import QtCore

    from tether.project.core import Project

    project, *_ = _round_trip_store(tmp_path, n=3, t=12)
    assert shell.load_project(project.path) is not None
    started = threading.Event()
    gate = threading.Event()

    def blocking_idealizer(_molecule_key):
        started.set()
        gate.wait(timeout=5.0)
        return np.full(12, 0.5)

    shell._idealizer = blocking_idealizer
    qtbot.keyClick(shell.molecule_list, QtCore.Qt.Key.Key_I)
    qtbot.waitUntil(started.is_set, timeout=2000)
    running = shell._idealize_future
    original_release = Project.release_lock
    release_calls = 0

    def flaky_release(project_ref):
        nonlocal release_calls
        release_calls += 1
        if release_calls == 1:
            raise OSError("temporary unlink failure")
        return original_release(project_ref)

    monkeypatch.setattr(Project, "release_lock", flaky_release)

    shell.close()
    # Drive the lifecycle helper explicitly so the regression does not depend on
    # wall-clock QTimer scheduling.
    shell._lock_release_timer.stop()
    gate.set()
    qtbot.waitUntil(running.done, timeout=5000)
    qtbot.waitUntil(lambda: release_calls == 1, timeout=2000)

    assert release_calls == 1
    assert shell._session_project is not None
    assert shell._claimed_project_key is not None
    assert project.lock_owner() is not None

    shell._retry_session_release()

    assert release_calls == 2
    assert shell._session_project is None
    assert shell._claimed_project_key is None
    assert project.lock_owner() is None
    assert not shell._lock_release_timer.isActive()


def test_return_leg_revalidates_writer_ownership_after_modal_dialog(
    shell, tmp_path, monkeypatch
) -> None:
    from tether.gui import reconcile as reconcile_mod
    from tether.gui.reconcile import ReconcileDecision
    from tether.project import lock
    from tether.project.handoff import AppliedReconcile
    from tether.project.lock import LockIdentity

    project, *_ = _round_trip_store(tmp_path, n=3, t=12)
    assert shell.load_project(project.path) is not None
    returning = tmp_path / "return.hdf5"
    assert shell.hand_off_to_tmaven(returning) is not None
    report = shell._handoff.preview(returning)
    apply_calls = []

    class RecordingHandoff:
        def preview(self, _smd_path, *, model_path=None):
            return report

        def apply(self, smd_path, decision, *, model_path=None):
            apply_calls.append((smd_path, decision, model_path))
            return AppliedReconcile()

    shell._handoff = RecordingHandoff()
    foreign = None

    def steal_while_modal(_dialog):
        nonlocal foreign
        foreign = lock.acquire(
            project.path,
            identity=LockIdentity(host="OTHER-HOST", user="other", pid=999),
            steal=True,
        )
        return ReconcileDecision()

    monkeypatch.setattr(reconcile_mod.ReconcileDialog, "exec", steal_while_modal)
    try:
        assert shell.import_return_leg(returning) is None
        assert apply_calls == []
        assert shell._curation_project is None
        assert "read-only" in shell.status_message
    finally:
        if foreign is not None:
            assert lock.release(project.path, foreign)


def test_main_window_close_releases_session_state_while_application_lives(
    shell, qapp, qtbot, tmp_path
) -> None:
    project, *_ = _round_trip_store(tmp_path, n=3, t=12)
    assert shell.load_project(project.path) is not None
    old_quit_policy = qapp.quitOnLastWindowClosed()
    qapp.setQuitOnLastWindowClosed(False)
    try:
        shell.window.show()
        shell.window.close()
        qtbot.waitUntil(lambda: not project.lock_path.exists(), timeout=2000)
        assert shell._event_filter._app is None
        assert not shell._lock_refresh_timer.isActive()
    finally:
        qapp.setQuitOnLastWindowClosed(old_quit_policy)


def test_curation_write_error_adds_no_row_and_never_reports_success(
    shell, qtbot, tmp_path, monkeypatch
) -> None:
    from pyqtgraph.Qt import QtCore

    from tether.project.core import Project
    from tether.project.labels import read_labels

    project, *_ = _round_trip_store(tmp_path, n=3, t=12)
    assert shell.load_project(project.path) is not None

    def fail_write(_project, _molecule_key):
        raise OSError("simulated disk full")

    monkeypatch.setattr(Project, "accept", fail_write)
    qtbot.keyClick(shell.molecule_list, QtCore.Qt.Key.Key_Space)

    assert read_labels(project.path).shape == (0,)
    assert "Accept failed: simulated disk full" in shell.status_message
    assert "Accepted" not in shell.status_message


def test_successful_curation_defers_open_population_histogram_refresh(
    shell, qtbot, tmp_path
) -> None:
    from pyqtgraph.Qt import QtCore

    project, *_ = _round_trip_store(tmp_path, n=3, t=12)
    assert shell.load_project(project.path) is not None
    histogram = shell._histogram_seam
    histogram_calls = 0

    def counted_histogram():
        nonlocal histogram_calls
        histogram_calls += 1
        return histogram()

    shell._histogram_seam = counted_histogram
    dock = shell.show_histogram()
    assert dock is not None
    assert dock.histogram.n_molecules == 3
    assert histogram_calls == 1

    qtbot.keyClick(shell.molecule_list, QtCore.Qt.Key.Key_Backspace)

    assert histogram_calls == 1
    assert shell._histogram_refresh_timer.isActive()
    assert "Rejected" in shell.status_message
    qtbot.waitUntil(lambda: histogram_calls == 2, timeout=2000)
    assert dock.histogram.n_molecules == 2


def test_load_project_analysis_only_gates_overlap_and_banners(shell, tmp_path) -> None:
    from tether.project.analysis_import import ANALYSIS_ONLY_BANNER

    out = _analysis_only_store(tmp_path)

    opened = shell.load_project(out)

    assert opened is not None
    assert shell.molecule_list.count() == 4
    assert "analysis-only" in shell.status_message
    assert ANALYSIS_ONLY_BANNER in shell.status_message  # the one-time banner surfaced
    # overlap is gated OFF (no coordinates/patches) → no overlap dock built on selection
    assert shell.overlap_dock is None
    # the analysis substrate still works (corrected layer present)
    assert shell.show_histogram() is not None


def test_load_project_replaces_prior_project(shell, tmp_path) -> None:
    p1, *_ = _round_trip_store(tmp_path, n=3, t=12, name="rt1.tether", seed=1)
    shell.load_project(p1.path)
    assert p1.lock_owner() is not None
    assert shell.molecule_list.count() == 3
    assert shell.overlap_dock is not None
    first_dock = shell.overlap_dock

    # a second, different store replaces the first and rebuilds the overlap dock fresh
    p2, *_ = _round_trip_store(tmp_path, n=2, t=8, name="rt2.tether", seed=2)
    shell.load_project(p2.path)

    assert p1.lock_owner() is None
    assert p2.lock_owner() is not None
    assert shell.molecule_list.count() == 2
    assert shell.overlap_dock is not None
    assert shell.overlap_dock is not first_dock  # rebuilt, not the stale prior dock


def test_load_project_missing_file_is_fail_soft(shell, tmp_path) -> None:
    # First load a valid project so there is real prior state a bad open must preserve.
    project, keys, *_ = _round_trip_store(tmp_path, n=3, t=12)
    shell.load_project(project.path)
    assert shell.molecule_list.count() == 3
    prior_overlap = shell.overlap_dock
    assert prior_overlap is not None

    # A bad open is atomic: every fallible read runs before any state mutates, so the
    # previously loaded project (molecules + seams + docks) stays fully in place — the
    # only change is the failure reported in the status bar (the fail-soft contract).
    opened = shell.load_project(tmp_path / "nope.tether")

    assert opened is None
    assert "Open project failed" in shell.status_message
    assert shell.molecule_list.count() == 3  # prior molecules preserved
    assert shell.overlap_dock is prior_overlap  # prior seams/docks untouched (not reset)
    assert shell.show_histogram() is not None  # the prior histogram seam still resolves


# --------------------------------------------------------------------------- #
# reachability — &File menu + wizard single-produced auto-open
# --------------------------------------------------------------------------- #


def test_file_menu_exposes_open_action(shell) -> None:
    labels = [a.text() for a in shell.file_menu.actions()]
    assert labels == ["&Open project…"]


def test_import_deeplasi_bundle_opens_single_produced_live(shell, tmp_path) -> None:
    project, keys, *_ = _round_trip_store(tmp_path, n=3, t=12)
    stub = _StubWizardDialog((project.path,))

    result = shell.import_deeplasi_bundle(dialog_factory=lambda: stub)

    assert tuple(result) == (project.path,)
    # the single produced project was opened live for curate/idealize (§7.8 round-trip)
    assert shell.molecule_list.count() == 3
    assert "3 molecule(s)" in shell.status_message


def test_import_deeplasi_bundle_multiple_reports_without_auto_open(shell, tmp_path) -> None:
    # With several projects written, which to open is the curator's call — report them
    # and leave &File → Open as the picker (no auto-open, no molecules loaded).
    produced = (tmp_path / "a.tether", tmp_path / "b.tether")
    stub = _StubWizardDialog(produced)

    result = shell.import_deeplasi_bundle(dialog_factory=lambda: stub)

    assert tuple(result) == produced
    assert "wrote 2 project(s)" in shell.status_message
    assert shell.molecule_list.count() == 0
