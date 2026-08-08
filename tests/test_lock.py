# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the single-writer ``.lock`` lifecycle (M2 S9, PRD §5.4/§7.10; FR-CONCURRENCY).

Covers the M2 single-writer gate (PRD §5.3): the ``<file>.lock`` prevents a second writer;
steal-lock recovers and a cross-machine lock/stale/steal case is exercised
(simulated host/PID); and a locked-out non-owner opens the canonical file
read-only yet writes curation ``/labels`` to a separate split ``.tether`` keyed by
``molecule_key`` while a write to the canonical file is still refused. Plus the
OneDrive conflict-copy detect-and-surface. All headless (no Qt).
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

pytest.importorskip("h5py")
pytest.importorskip("numpy")

import h5py  # noqa: E402
import numpy as np  # noqa: E402

from tether.io.schema import MOLECULES_DTYPE, TABLE, create_project  # noqa: E402
from tether.project import (
    Project,  # noqa: E402
    lock,  # noqa: E402
)
from tether.project import labels as L  # noqa: E402
from tether.project.lock import CorruptLockError, LockedError, LockIdentity, LockInfo  # noqa: E402

HOST_A = LockIdentity(host="HOST-A", user="alice", pid=111)
HOST_B = LockIdentity(host="HOST-B", user="bob", pid=222)


def _seed(tmp_path: Path, specs: list[tuple[str, str]], *, name: str = "exp.tether") -> Path:
    """Create a ``.tether`` with molecule rows ``(molecule_key, condition_id)``.

    Mirrors ``test_labels._seed`` — seeds only the fields curation resolves on so
    the store is schema-faithful without the extraction pipeline.
    """
    path = create_project(tmp_path / name)
    rows = np.zeros(len(specs), dtype=MOLECULES_DTYPE)
    for field in MOLECULES_DTYPE.names:
        if MOLECULES_DTYPE[field].kind == "O":
            rows[field] = ""
    rows["molecule_id"] = [f"mol-{i}" for i in range(len(specs))]
    rows["molecule_key"] = [key for key, _ in specs]
    rows["condition_id"] = [cond for _, cond in specs]
    rows["curation_label"] = int(L.CurationLabel.UNCURATED)
    with h5py.File(path, "r+") as f:
        table = f["molecules"][TABLE]
        table.resize((len(specs),))
        table[:] = rows
    return path


def _write_lock_file(path: Path, info: LockInfo) -> None:
    """Write a hand-crafted lock record to the sidecar (test helper, no private API)."""
    lock.lock_path(path).write_text(json.dumps(info.to_dict()), encoding="utf-8")


# --- LockInfo value semantics + staleness ------------------------------------


def test_lockinfo_roundtrip_and_identity() -> None:
    info = LockInfo(host="h", user="u", pid=7, timestamp="2020-01-01T00:00:00+00:00", nonce="abc")
    assert LockInfo.from_dict(info.to_dict()) == info
    assert info.identity == LockIdentity(host="h", user="u", pid=7)


def test_staleness_is_wall_clock_with_injected_now() -> None:
    acquired = datetime(2020, 1, 1, 12, 0, tzinfo=UTC)
    info = LockInfo(host="h", user="u", pid=1, timestamp=acquired.isoformat(), nonce="n")
    twenty_min = datetime(2020, 1, 1, 12, 20, tzinfo=UTC)
    forty_min = datetime(2020, 1, 1, 12, 40, tzinfo=UTC)
    assert info.age_seconds(now=twenty_min) == pytest.approx(1200.0)
    # Default timeout is the §11.2 ≈30 min window.
    assert pytest.approx(1800.0) == lock.DEFAULT_STALENESS_TIMEOUT_S
    assert not info.is_stale(now=twenty_min)  # 20 min < 30 min
    assert info.is_stale(now=forty_min)  # 40 min > 30 min


def test_lock_path_appends_suffix(tmp_path: Path) -> None:
    assert lock.lock_path(tmp_path / "exp.tether").name == "exp.tether.lock"


# --- acquire / read / release round-trip -------------------------------------


def test_acquire_read_release_roundtrip(tmp_path: Path) -> None:
    path = _seed(tmp_path, [("k0", "c0")])
    assert lock.read_lock(path) is None
    info = lock.acquire(path, identity=HOST_A)
    on_disk = lock.read_lock(path)
    assert on_disk == info
    assert on_disk.identity == HOST_A
    assert lock.release(path, info) is True
    assert lock.read_lock(path) is None
    # A second release is a harmless no-op.
    assert lock.release(path, info) is False


def test_reacquire_by_same_identity_refreshes(tmp_path: Path) -> None:
    path = _seed(tmp_path, [("k0", "c0")])
    first = lock.acquire(path, identity=HOST_A)
    second = lock.acquire(path, identity=HOST_A)  # same (host, pid) -> allowed
    assert second.nonce != first.nonce
    assert lock.read_lock(path) == second


def test_retained_refresh_preserves_nonce_and_requires_existing_epoch(tmp_path: Path) -> None:
    path = _seed(tmp_path, [("k0", "c0")])
    acquired = datetime(2020, 1, 1, 12, 0, tzinfo=UTC)
    refreshed_at = datetime(2020, 1, 1, 12, 10, tzinfo=UTC)
    first = lock.acquire(path, identity=HOST_A, now=acquired)

    refreshed = lock.refresh(path, first, now=refreshed_at)

    assert refreshed.nonce == first.nonce
    assert refreshed.timestamp == refreshed_at.isoformat()
    assert lock.read_lock(path) == refreshed
    assert lock.release(path, refreshed)
    with pytest.raises(LockedError):
        lock.refresh(path, refreshed)


def test_refresh_cannot_overwrite_successor_steal_between_validation_and_publish(
    tmp_path: Path, monkeypatch
) -> None:
    path = _seed(tmp_path, [("k0", "c0")])
    first = lock.acquire(path, identity=HOST_A)
    original_publish_refresh = lock._publish_refresh
    refresh_at_publish = threading.Event()
    allow_refresh_publish = threading.Event()
    steal_started = threading.Event()
    steal_finished = threading.Event()
    refresh_results: list[LockInfo] = []
    successor_results: list[LockInfo] = []
    refresh_errors: list[BaseException] = []
    steal_errors: list[BaseException] = []

    def controlled_publish_refresh(lock_file: Path, info: LockInfo) -> None:
        refresh_at_publish.set()
        assert allow_refresh_publish.wait(timeout=5.0)
        original_publish_refresh(lock_file, info)

    monkeypatch.setattr(lock, "_publish_refresh", controlled_publish_refresh)

    def retained_refresh() -> None:
        try:
            refresh_results.append(lock.refresh(path, first))
        except BaseException as exc:  # noqa: BLE001 - propagate worker failures below
            refresh_errors.append(exc)

    def foreign_steal() -> None:
        try:
            steal_started.set()
            successor_results.append(lock.acquire(path, identity=HOST_B, steal=True))
        except BaseException as exc:  # noqa: BLE001 - propagate worker failures below
            steal_errors.append(exc)
        finally:
            steal_finished.set()

    refresh_thread = threading.Thread(target=retained_refresh, name="retained-refresh")
    steal_thread = threading.Thread(target=foreign_steal, name="foreign-steal")
    refresh_thread.start()
    assert refresh_at_publish.wait(timeout=2.0)
    steal_thread.start()
    assert steal_started.wait(timeout=2.0)
    # The peer entered the public cross-process lifecycle but cannot publish
    # between refresh's nonce check and atomic replace.
    assert not steal_finished.wait(timeout=0.2)
    allow_refresh_publish.set()
    refresh_thread.join(timeout=5.0)
    steal_thread.join(timeout=5.0)

    assert not refresh_thread.is_alive()
    assert not steal_thread.is_alive()
    assert refresh_errors == []
    assert steal_errors == []
    assert len(refresh_results) == 1
    assert len(successor_results) == 1
    successor = successor_results[0]
    assert successor.nonce != first.nonce
    assert lock.read_lock(path) == successor


def test_refresh_guard_serializes_a_foreign_subprocess_steal(tmp_path: Path, monkeypatch) -> None:
    path = _seed(tmp_path, [("k0", "c0")])
    first = lock.acquire(path, identity=HOST_A)
    original_publish_refresh = lock._publish_refresh
    refresh_at_publish = threading.Event()
    allow_refresh_publish = threading.Event()
    refresh_results: list[LockInfo] = []
    refresh_errors: list[BaseException] = []

    def controlled_publish_refresh(lock_file: Path, refreshed: LockInfo) -> None:
        refresh_at_publish.set()
        assert allow_refresh_publish.wait(timeout=5.0)
        original_publish_refresh(lock_file, refreshed)

    monkeypatch.setattr(lock, "_publish_refresh", controlled_publish_refresh)

    def retained_refresh() -> None:
        try:
            refresh_results.append(lock.refresh(path, first))
        except BaseException as exc:  # noqa: BLE001 - propagate worker failures below
            refresh_errors.append(exc)

    refresh_thread = threading.Thread(target=retained_refresh)
    refresh_thread.start()
    assert refresh_at_publish.wait(timeout=2.0)

    child_script = """
import json
import sys
from tether.project import lock
from tether.project.lock import LockIdentity

print("ready", flush=True)
successor = lock.acquire(
    sys.argv[1],
    identity=LockIdentity(host="CHILD-HOST", user="child", pid=4242),
    steal=True,
)
print(json.dumps(successor.to_dict()), flush=True)
"""
    child = subprocess.Popen(
        [sys.executable, "-c", child_script, os.fspath(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=os.environ.copy(),
    )
    assert child.stdout is not None
    assert child.stdout.readline().strip() == "ready"
    with pytest.raises(subprocess.TimeoutExpired):
        child.wait(timeout=0.2)

    allow_refresh_publish.set()
    refresh_thread.join(timeout=5.0)
    stdout, stderr = child.communicate(timeout=5.0)

    assert not refresh_thread.is_alive()
    assert refresh_errors == []
    assert len(refresh_results) == 1
    assert child.returncode == 0, stderr
    successor = LockInfo.from_dict(json.loads(stdout.strip()))
    assert successor.nonce != first.nonce
    assert lock.read_lock(path) == successor


def test_lifecycle_guard_inode_persists_across_operations(tmp_path: Path) -> None:
    path = _seed(tmp_path, [("k0", "c0")])
    first = lock.acquire(path, identity=HOST_A)
    guard = lock._guard_path(lock.lock_path(path))
    first_inode = guard.stat().st_ino

    assert lock.release(path, first)
    second = lock.acquire(path, identity=HOST_B)

    assert guard.stat().st_ino == first_inode
    assert lock.release(path, second)
    assert guard.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode/umask semantics")
def test_lifecycle_guard_creation_allows_group_shared_writers(tmp_path: Path) -> None:
    path = _seed(tmp_path, [("k0", "c0")])
    guard = lock._guard_path(lock.lock_path(path))
    directory_mode = stat.S_IMODE(tmp_path.stat().st_mode)
    previous_umask = os.umask(0o002)
    try:
        with lock._lifecycle_guard(lock.lock_path(path)):
            pass
    finally:
        os.umask(previous_umask)

    assert stat.S_IMODE(guard.stat().st_mode) == 0o664
    assert stat.S_IMODE(tmp_path.stat().st_mode) == directory_mode


@pytest.mark.skipif(os.name != "nt", reason="Windows byte-range lock semantics")
def test_windows_guard_byte_initialization_retries_stale_zero_observation(
    tmp_path: Path,
) -> None:
    path = _seed(tmp_path, [("k0", "c0")])
    lock_file = lock.lock_path(path)
    barrier = tmp_path / "guard-init-barrier"
    barrier.mkdir()

    contender_script = r"""
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from tether.project import lock

lock_file = Path(sys.argv[1])
barrier = Path(sys.argv[2])
original_fstat = lock.os.fstat
original_write = lock.os.write
first_fstat = True

def wait_for(path):
    deadline = time.monotonic() + 5.0
    while not path.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError(path)
        time.sleep(0.01)

def coordinated_fstat(fd):
    global first_fstat
    current = original_fstat(fd)
    if first_fstat:
        first_fstat = False
        (barrier / "contender-saw-zero").touch()
        wait_for(barrier / "holder-locked")
        return SimpleNamespace(st_size=0)
    return current

def signalled_write(fd, data):
    (barrier / "contender-write-attempted").touch()
    return original_write(fd, data)

lock.os.fstat = coordinated_fstat
lock.os.write = signalled_write
with lock._lifecycle_guard(lock_file):
    pass
"""
    holder_script = r"""
import sys
import time
from pathlib import Path
from tether.project import lock

lock_file = Path(sys.argv[1])
barrier = Path(sys.argv[2])

def wait_for(path):
    deadline = time.monotonic() + 5.0
    while not path.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError(path)
        time.sleep(0.01)

wait_for(barrier / "contender-saw-zero")
with lock._lifecycle_guard(lock_file):
    (barrier / "holder-locked").touch()
    wait_for(barrier / "contender-write-attempted")
"""
    contender = subprocess.Popen(
        [sys.executable, "-c", contender_script, os.fspath(lock_file), os.fspath(barrier)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=os.environ.copy(),
    )
    deadline = time.monotonic() + 5.0
    while not (barrier / "contender-saw-zero").exists():
        if time.monotonic() >= deadline:
            contender.kill()
            raise AssertionError("contender did not reach zero-size observation")
        time.sleep(0.01)
    holder = subprocess.Popen(
        [sys.executable, "-c", holder_script, os.fspath(lock_file), os.fspath(barrier)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=os.environ.copy(),
    )
    contender_stdout, contender_stderr = contender.communicate(timeout=10.0)
    holder_stdout, holder_stderr = holder.communicate(timeout=10.0)

    assert holder.returncode == 0, holder_stdout + holder_stderr
    assert contender.returncode == 0, contender_stdout + contender_stderr
    assert lock._guard_path(lock_file).stat().st_size == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing-violation semantics")
def test_windows_refresh_retries_atomic_replace_while_observer_is_open(
    tmp_path: Path,
) -> None:
    path = _seed(tmp_path, [("k0", "c0")])
    held = lock.acquire(path, identity=HOST_A)
    observer = lock.lock_path(path).open("rb")
    refreshed: list[LockInfo] = []
    errors: list[BaseException] = []

    def retained_refresh() -> None:
        try:
            refreshed.append(lock.refresh(path, held))
        except BaseException as exc:  # noqa: BLE001 - propagate worker failures below
            errors.append(exc)

    worker = threading.Thread(target=retained_refresh)
    worker.start()
    time.sleep(0.1)
    assert worker.is_alive()
    observer.close()
    worker.join(timeout=5.0)

    assert not worker.is_alive()
    assert errors == []
    assert len(refreshed) == 1
    assert lock.read_lock(path) == refreshed[0]
    assert list(tmp_path.glob(f"{lock.lock_path(path).name}.tmp-*")) == []


def test_atomic_write_removes_temp_after_replace_failure(tmp_path: Path, monkeypatch) -> None:
    lock_file = tmp_path / "exp.tether.lock"
    info = LockInfo(
        host="HOST-A",
        user="alice",
        pid=111,
        timestamp=datetime.now(UTC).isoformat(),
        nonce="cleanup",
    )

    def fail_replace(_source, _destination):
        raise PermissionError("destination is busy")

    monkeypatch.setattr(lock.os, "replace", fail_replace)
    monkeypatch.setattr(lock, "_ATOMIC_REPLACE_TIMEOUT_S", 0.0, raising=False)

    with pytest.raises(PermissionError):
        lock._atomic_write(lock_file, info)

    assert not lock_file.exists()
    assert list(tmp_path.glob(f"{lock_file.name}.tmp-*")) == []


def test_atomic_write_removes_partial_temp_after_write_failure(tmp_path: Path, monkeypatch) -> None:
    lock_file = tmp_path / "exp.tether.lock"
    info = LockInfo(
        host="HOST-A",
        user="alice",
        pid=111,
        timestamp=datetime.now(UTC).isoformat(),
        nonce="partial-write",
    )
    original_write_text = Path.write_text

    def partial_then_fail(path, data, *, encoding=None, errors=None, newline=None):
        if path.name == f"{lock_file.name}.tmp-{info.nonce}":
            with path.open("w", encoding=encoding, errors=errors, newline=newline) as stream:
                stream.write(data[:5])
            raise OSError("simulated partial temp write")
        return original_write_text(
            path,
            data,
            encoding=encoding,
            errors=errors,
            newline=newline,
        )

    monkeypatch.setattr(Path, "write_text", partial_then_fail)

    with pytest.raises(OSError, match="partial temp write"):
        lock._atomic_write(lock_file, info)

    assert not lock_file.exists()
    assert list(tmp_path.glob(f"{lock_file.name}.tmp-*")) == []


def test_fork_child_reset_closes_inherited_guard_descriptors(tmp_path: Path) -> None:
    descriptor = os.open(tmp_path / "inherited.guard", os.O_CREAT | os.O_RDWR, 0o644)
    assert set() == lock._ACTIVE_GUARD_FDS
    lock._guard_registry_before_fork()
    lock._ACTIVE_GUARD_FDS.add(descriptor)

    lock._guard_registry_after_fork_child()

    assert set() == lock._ACTIVE_GUARD_FDS
    with pytest.raises(OSError):
        os.fstat(descriptor)
    assert lock._GUARD_REGISTRY_LOCK.acquire(timeout=0.2)
    lock._GUARD_REGISTRY_LOCK.release()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX fork")
def test_fork_child_does_not_prolong_parent_lifecycle_guard(tmp_path: Path) -> None:
    path = _seed(tmp_path, [("k0", "c0")])
    guard_held = threading.Event()
    release_guard = threading.Event()
    errors: list[BaseException] = []

    def hold_parent_guard() -> None:
        try:
            with lock._lifecycle_guard(lock.lock_path(path)):
                guard_held.set()
                assert release_guard.wait(timeout=5.0)
        except BaseException as exc:  # noqa: BLE001 - propagate worker failures below
            errors.append(exc)

    holder = threading.Thread(target=hold_parent_guard)
    holder.start()
    assert guard_held.wait(timeout=2.0)
    pid = os.fork()
    if pid == 0:  # pragma: no cover - assertion is the child exit status
        try:
            acquired = lock.acquire(path, identity=HOST_B, steal=True)
            assert lock.release(path, acquired)
        except BaseException:
            os._exit(1)
        os._exit(0)

    release_guard.set()
    holder.join(timeout=5.0)
    _, status = os.waitpid(pid, 0)

    assert not holder.is_alive()
    assert errors == []
    assert os.waitstatus_to_exitcode(status) == 0


def test_strict_acquire_refuses_same_identity_without_refresh(tmp_path: Path) -> None:
    path = _seed(tmp_path, [("k0", "c0")])
    first = lock.acquire(path, identity=HOST_A)
    with pytest.raises(LockedError) as exc:
        lock.acquire(path, identity=HOST_A, allow_existing_owner=False)
    assert exc.value.owner == first
    assert lock.read_lock(path) == first
    assert lock.release(path, first)


def test_strict_acquire_retries_exclusive_claim_without_overwriting_successor(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = _seed(tmp_path, [("k0", "c0")])
    successor = LockInfo(
        host=HOST_B.host,
        user=HOST_B.user,
        pid=HOST_B.pid,
        timestamp=datetime.now(UTC).isoformat(),
        nonce="successor",
    )
    exclusive_calls = 0
    real_exclusive_create = lock._exclusive_create
    real_read_settled = lock._read_settled

    def lose_then_meet_successor(lp: Path, info: LockInfo) -> bool:
        nonlocal exclusive_calls
        exclusive_calls += 1
        if exclusive_calls == 1:
            return False
        _write_lock_file(path, successor)
        return real_exclusive_create(lp, info)

    def first_winner_disappeared(lp: Path, **kwargs):
        if exclusive_calls == 1:
            return None, False
        return real_read_settled(lp, **kwargs)

    monkeypatch.setattr(lock, "_exclusive_create", lose_then_meet_successor)
    monkeypatch.setattr(lock, "_read_settled", first_winner_disappeared)

    with pytest.raises(LockedError) as exc:
        lock.acquire(path, identity=HOST_A, allow_existing_owner=False)
    assert exclusive_calls == 2
    assert exc.value.owner == successor
    assert lock.read_lock(path) == successor


def test_strict_acquire_refuses_present_lock_even_with_steal(tmp_path: Path) -> None:
    path = _seed(tmp_path, [("k0", "c0")])
    first = lock.acquire(path, identity=HOST_A)
    with pytest.raises(LockedError) as exc:
        lock.acquire(
            path,
            identity=HOST_B,
            steal=True,
            allow_existing_owner=False,
        )
    assert exc.value.owner == first
    assert lock.read_lock(path) == first
    assert lock.release(path, first)


def test_strict_acquire_bounds_vanished_winner_retries(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = _seed(tmp_path, [("k0", "c0")])
    exclusive_calls = 0

    def always_lose(_lp: Path, _info: LockInfo) -> bool:
        nonlocal exclusive_calls
        exclusive_calls += 1
        return False

    monkeypatch.setattr(lock, "_exclusive_create", always_lose)
    monkeypatch.setattr(lock, "_read_settled", lambda _lp: (None, False))

    with pytest.raises(LockedError):
        lock.acquire(path, identity=HOST_A, allow_existing_owner=False)
    assert exclusive_calls == lock._STRICT_CLAIM_ATTEMPTS
    assert lock.read_lock(path) is None


def test_process_reservation_allows_owner_writes_but_refuses_recursive_claim(
    tmp_path: Path,
) -> None:
    path = _seed(tmp_path, [("k0", "c0")])
    reservation = lock.acquire_process_reservation(path)
    owner = lock.acquire(
        path,
        identity=HOST_A,
        allow_existing_owner=False,
        process_reservation=reservation,
    )
    try:
        lock.assert_process_reservation(path, reservation)
        lock.assert_writable(path, identity=HOST_A)
        with pytest.raises(LockedError):
            lock.acquire(path, identity=HOST_A)
        with pytest.raises(LockedError):
            lock.acquire_process_reservation(path)
    finally:
        assert lock.release(path, owner)
        lock.release_process_reservation(path, reservation)

    assert lock.read_lock(path) is None


def test_process_reservations_do_not_serialize_distinct_destinations(tmp_path: Path) -> None:
    first = _seed(tmp_path, [("k0", "c0")], name="first.tether")
    second = _seed(tmp_path, [("k1", "c1")], name="second.tether")
    first_reservation = lock.acquire_process_reservation(first)
    second_acquired = threading.Event()
    release_second = threading.Event()
    errors: list[BaseException] = []

    def reserve_second() -> None:
        try:
            reservation = lock.acquire_process_reservation(second)
            second_acquired.set()
            assert release_second.wait(timeout=5)
            lock.release_process_reservation(second, reservation)
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    contender = threading.Thread(target=reserve_second)
    contender.start()
    try:
        assert second_acquired.wait(timeout=5)
    finally:
        release_second.set()
        contender.join(timeout=5)
        lock.release_process_reservation(first, first_reservation)

    assert not contender.is_alive()
    assert not errors


def test_process_reservation_registry_resets_after_pid_change(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = _seed(tmp_path, [("k0", "c0")])
    lock.acquire_process_reservation(path)
    monkeypatch.setattr(lock, "_PROCESS_RESERVATIONS_PID", -1)

    replacement = lock.acquire_process_reservation(path)
    lock.release_process_reservation(path, replacement)


def test_process_reservation_key_survives_symlink_leaf_replacement(tmp_path: Path) -> None:
    target = tmp_path / "target.tether"
    target.write_text("old target", encoding="utf-8")
    alias = tmp_path / "alias.tether"
    try:
        alias.symlink_to(target.name)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable on this platform: {exc}")

    reservation = lock.acquire_process_reservation(alias)
    owner = lock.acquire(
        alias,
        identity=HOST_A,
        allow_existing_owner=False,
        process_reservation=reservation,
    )
    replacement = tmp_path / "replacement.tether"
    replacement.write_text("new canonical", encoding="utf-8")
    os.replace(replacement, alias)

    assert alias.read_text(encoding="utf-8") == "new canonical"
    assert target.read_text(encoding="utf-8") == "old target"
    lock.assert_process_reservation(alias, reservation)
    assert lock.read_lock(alias) == owner
    assert lock.release(alias, owner)
    lock.release_process_reservation(alias, reservation)
    assert lock.read_lock(alias) is None


# --- single-writer: a foreign lock prevents a second writer ------------------


def test_foreign_live_lock_refuses_acquire(tmp_path: Path) -> None:
    path = _seed(tmp_path, [("k0", "c0")])
    lock.acquire(path, identity=HOST_A)
    with pytest.raises(LockedError) as exc:
        lock.acquire(path, identity=HOST_B)
    assert exc.value.owner is not None
    assert exc.value.owner.identity == HOST_A
    assert exc.value.stale is False


def test_stale_foreign_lock_refused_without_steal_then_stealable(tmp_path: Path) -> None:
    path = _seed(tmp_path, [("k0", "c0")])
    ancient = datetime(2000, 1, 1, tzinfo=UTC).isoformat()
    _write_lock_file(
        path, LockInfo(host="HOST-A", user="alice", pid=111, timestamp=ancient, nonce="old")
    )
    # Stale, but still refused without an explicit steal (§5.4), flagged stale.
    with pytest.raises(LockedError) as exc:
        lock.acquire(path, identity=HOST_B)
    assert exc.value.stale is True
    # Steal reclaims it.
    info = lock.acquire(path, identity=HOST_B, steal=True)
    assert lock.read_lock(path) == info
    assert info.identity == HOST_B


def test_steal_lock_returns_ousted_owner(tmp_path: Path) -> None:
    path = _seed(tmp_path, [("k0", "c0")])
    a = lock.acquire(path, identity=HOST_A)
    new, prior = lock.steal_lock(path, identity=HOST_B)
    assert prior == a
    assert lock.read_lock(path) == new
    assert new.identity == HOST_B
    # The ousted owner cannot release the stolen lock (nonce mismatch).
    assert lock.release(path, a) is False


def test_assert_writable_passes_when_unlocked_or_self(tmp_path: Path) -> None:
    path = _seed(tmp_path, [("k0", "c0")])
    lock.assert_writable(path, identity=HOST_A)  # unlocked -> ok
    lock.acquire(path, identity=HOST_A)
    lock.assert_writable(path, identity=HOST_A)  # our own lock -> ok
    with pytest.raises(LockedError):
        lock.assert_writable(path, identity=HOST_B)


def test_ownership_is_full_host_user_pid_identity(tmp_path: Path) -> None:
    # Ownership is the full (host, user, pid) identity: a recycled PID on the same
    # host owned by a *different* login is foreign, never silently granted write
    # access (§5.4 single-writer safety against PID reuse).
    path = _seed(tmp_path, [("k0", "c0")])
    alice = LockIdentity(host="WS1", user="alice", pid=500)
    bob = LockIdentity(host="WS1", user="bob", pid=500)  # same host+pid, different login
    lock.acquire(path, identity=alice)
    with pytest.raises(LockedError):
        lock.assert_writable(path, identity=bob)
    with pytest.raises(LockedError):
        lock.acquire(path, identity=bob)
    # The genuine owner still refreshes its own lock.
    lock.assert_writable(path, identity=alice)


# --- corrupt lock ------------------------------------------------------------


def test_corrupt_lock_is_surfaced_not_ignored(tmp_path: Path) -> None:
    path = _seed(tmp_path, [("k0", "c0")])
    lock.lock_path(path).write_text("{not json", encoding="utf-8")
    with pytest.raises(CorruptLockError):
        lock.read_lock(path)
    with pytest.raises(LockedError) as exc:
        lock.assert_writable(path, identity=HOST_A)
    assert exc.value.corrupt is True
    # A steal recovers a corrupt lock.
    info = lock.acquire(path, identity=HOST_A, steal=True)
    assert lock.read_lock(path) == info


def test_missing_field_lock_is_corrupt(tmp_path: Path) -> None:
    path = _seed(tmp_path, [("k0", "c0")])
    lock.lock_path(path).write_text(json.dumps({"host": "h"}), encoding="utf-8")
    with pytest.raises(CorruptLockError):
        lock.read_lock(path)


def test_unparseable_timestamp_lock_is_corrupt(tmp_path: Path) -> None:
    # A malformed timestamp is caught at parse time (the corrupt-lock contract),
    # not later as a raw ValueError inside a staleness check.
    path = _seed(tmp_path, [("k0", "c0")])
    bad = {"host": "h", "user": "u", "pid": 1, "timestamp": "not-a-time", "nonce": "n"}
    lock.lock_path(path).write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(CorruptLockError):
        lock.read_lock(path)
    with pytest.raises(LockedError):
        lock.assert_writable(path, identity=HOST_A)


@pytest.mark.parametrize(
    "bad",
    [
        {
            "host": None,
            "user": "u",
            "pid": 1,
            "timestamp": "2020-01-01T00:00:00+00:00",
            "nonce": "n",
        },
        {
            "host": ["h"],
            "user": "u",
            "pid": 1,
            "timestamp": "2020-01-01T00:00:00+00:00",
            "nonce": "n",
        },
        {
            "host": "h",
            "user": "u",
            "pid": True,
            "timestamp": "2020-01-01T00:00:00+00:00",
            "nonce": "n",
        },
        {
            "host": "h",
            "user": "u",
            "pid": "1",
            "timestamp": "2020-01-01T00:00:00+00:00",
            "nonce": "n",
        },
    ],
)
def test_ill_typed_field_lock_is_corrupt(tmp_path: Path, bad: dict) -> None:
    # Fields are type-checked, not coerced: a null/array/bool/str-pid must be a
    # corrupt lock, never a valid-looking foreign lock (e.g. host "None").
    path = _seed(tmp_path, [("k0", "c0")])
    lock.lock_path(path).write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(CorruptLockError):
        lock.read_lock(path)


def test_steal_lock_over_corrupt_reports_none_prior(tmp_path: Path) -> None:
    # steal_lock cannot name an owner for an unparseable prior lock, so it reports
    # the ousted owner as None while still overwriting it with a valid record.
    path = _seed(tmp_path, [("k0", "c0")])
    lock.lock_path(path).write_text("{not json", encoding="utf-8")
    new, prior = lock.steal_lock(path, identity=HOST_B)
    assert prior is None
    assert lock.read_lock(path) == new
    assert new.identity == HOST_B


# --- held_lock context manager -----------------------------------------------


def test_held_lock_context_manager_acquires_and_releases(tmp_path: Path) -> None:
    path = _seed(tmp_path, [("k0", "c0")])
    with lock.held_lock(path, identity=HOST_A) as info:
        assert lock.read_lock(path) == info
        with pytest.raises(LockedError):
            lock.assert_writable(path, identity=HOST_B)
    assert lock.read_lock(path) is None


# --- OneDrive / SharePoint conflict-copy detection ---------------------------


def test_conflict_copies_detects_onedrive_and_parenthetical_forms(tmp_path: Path) -> None:
    path = _seed(tmp_path, [("k0", "c0")], name="exp.tether")
    # Two conflict-copy naming forms, both siblings of the canonical file.
    onedrive = tmp_path / "exp-DESKTOP-AB12.tether"
    numbered = tmp_path / "exp (1).tether"
    onedrive.write_bytes(b"copy")
    numbered.write_bytes(b"copy")
    # Noise that must NOT be reported: the canonical file, its lock, an unrelated file.
    lock.acquire(path, identity=HOST_A)  # writes exp.tether.lock
    (tmp_path / "other.tether").write_bytes(b"unrelated")

    found = lock.conflict_copies(path)
    # Exact set: the two conflict copies are reported, and the canonical file, its
    # own .lock sidecar (present on disk), and the unrelated sibling are all excluded.
    assert set(found) == {onedrive, numbered}
    assert path.exists() and lock.lock_path(path).exists()  # they were on disk to exclude


def test_conflict_copies_empty_when_none(tmp_path: Path) -> None:
    path = _seed(tmp_path, [("k0", "c0")])
    assert lock.conflict_copies(path) == []


# --- Project write-guard (§9 M2: lock prevents a second writer) ---------------


def test_project_write_guard_blocks_nonowner_curation(tmp_path: Path) -> None:
    path = _seed(tmp_path, [("m1", "cond")])
    owner = Project(path, identity=HOST_A)
    owner.acquire_lock()

    nonowner = Project(path, identity=HOST_B)
    assert nonowner.is_locked_by_other() is not None
    assert nonowner.is_locked_by_other().identity == HOST_A
    # A non-owner may still browse read-only.
    assert nonowner.read_labels().shape[0] == 0
    assert nonowner.curation_label("m1") == int(L.CurationLabel.UNCURATED)
    # ...but not write the canonical file.
    with pytest.raises(LockedError):
        nonowner.accept("m1")

    # The owner writes freely.
    owner.accept("m1")
    assert owner.curation_label("m1") == int(L.CurationLabel.ACCEPT)
    assert owner.read_labels().shape[0] == 1


def test_project_steal_lock_recovers_write_access(tmp_path: Path) -> None:
    path = _seed(tmp_path, [("m1", "cond")])
    owner = Project(path, identity=HOST_A)
    owner.acquire_lock()
    nonowner = Project(path, identity=HOST_B)
    with pytest.raises(LockedError):
        nonowner.reject("m1")

    new, prior = nonowner.steal_lock()
    assert prior is not None and prior.identity == HOST_A
    assert new.identity == HOST_B
    # The stealer now writes; the ousted owner is refused.
    nonowner.reject("m1")
    assert nonowner.curation_label("m1") == int(L.CurationLabel.REJECT)
    with pytest.raises(LockedError):
        owner.accept("m1")


def test_project_release_lock_is_nonce_checked(tmp_path: Path) -> None:
    path = _seed(tmp_path, [("m1", "cond")])
    owner = Project(path, identity=HOST_A)
    owner.acquire_lock()
    thief = Project(path, identity=HOST_B)
    thief.steal_lock()
    # The ousted owner's release must not delete the thief's lock.
    assert owner.release_lock() is False
    assert owner._held_lock is None
    assert thief.lock_owner().identity == HOST_B


def test_project_write_lock_context_manager(tmp_path: Path) -> None:
    path = _seed(tmp_path, [("m1", "cond")])
    owner = Project(path, identity=HOST_A)
    with owner.write_lock():
        assert owner.lock_owner().identity == HOST_A
        with pytest.raises(LockedError):
            Project(path, identity=HOST_B).accept("m1")
    assert owner.lock_owner() is None


def test_unguarded_write_allowed_when_no_lock(tmp_path: Path) -> None:
    # Backward-compat: with no lock at all, curation writes proceed (the S5 path).
    path = _seed(tmp_path, [("m1", "cond")])
    Project(path, identity=HOST_B).accept("m1")
    assert L.curation_label_of(path, "m1") == int(L.CurationLabel.ACCEPT)


def test_auto_identity_reresolves_after_pid_change(tmp_path: Path, monkeypatch) -> None:
    # A handle inherited across fork() must not let the child impersonate the parent:
    # an auto-resolved identity is re-resolved when the PID no longer matches.
    path = _seed(tmp_path, [("m1", "cond")])
    proj = Project(path)  # auto-resolved identity
    first = proj._acting_identity()
    monkeypatch.setattr(os, "getpid", lambda: first.pid + 1)  # simulate the forked child
    second = proj._acting_identity()
    assert second.pid == first.pid + 1
    # An *injected* identity is fixed — never re-resolved on a PID change.
    injected = Project(path, identity=HOST_A)
    assert injected._acting_identity() == HOST_A


def test_pid_drift_never_waits_on_an_inherited_project_mutex(tmp_path: Path, monkeypatch) -> None:
    class InheritedLockedMutex:
        def __enter__(self):
            raise AssertionError("fork child attempted to acquire an inherited mutex")

        def __exit__(self, *_args):
            return False

    path = _seed(tmp_path, [("m1", "cond")])
    project = Project(path)
    held = project.acquire_lock()
    project._lock_state_lock = InheritedLockedMutex()
    monkeypatch.setattr(os, "getpid", lambda: held.pid + 1)
    try:
        child_identity = project._acting_identity()
        assert child_identity.pid == held.pid + 1
        assert project._held_lock is None
    finally:
        assert lock.release(path, held)


def test_create_overwrite_refused_when_foreign_locked(tmp_path: Path) -> None:
    path = _seed(tmp_path, [("m1", "cond")])
    lock.acquire(path, identity=HOST_A)  # a foreign writer holds the canonical
    with pytest.raises(LockedError):
        Project.create(path, overwrite=True, identity=HOST_B)
    # The refusal happened BEFORE create_project ran — the seeded data is intact
    # (the exact truncation-ordering the guard protects).
    assert L.curation_label_of(path, "m1") == int(L.CurationLabel.UNCURATED)


def test_forked_child_cannot_release_parent_lock(tmp_path: Path, monkeypatch) -> None:
    # A handle that acquired a lock, then is inherited across fork(), must not let
    # the child release the parent's lock: the inherited _held_lock is dropped when
    # the auto-resolved identity re-resolves on the PID change.
    path = _seed(tmp_path, [("m1", "cond")])
    parent = Project(path)  # auto-resolved identity
    held = parent.acquire_lock()
    monkeypatch.setattr(os, "getpid", lambda: held.pid + 1)  # now acting as the child
    assert parent.release_lock() is False  # child does not delete the parent's lock
    assert lock.read_lock(path) is not None  # the parent's lock survives


# --- split-file curation (§9 M2: read-only browse + curate into own split) ----


def test_split_curation_while_canonical_locked(tmp_path: Path) -> None:
    canonical = _seed(tmp_path, [("m1", "cond"), ("m2", "cond")], name="canonical.tether")
    owner = Project(canonical, identity=HOST_A)
    owner.acquire_lock()

    nonowner = Project(canonical, identity=HOST_B)
    # Canonical write refused while HOST-A holds the lock.
    with pytest.raises(LockedError):
        nonowner.accept("m1")

    # HOST-B browses read-only and curates into their own split, keyed by molecule_key.
    split_path = tmp_path / "bob-split.tether"
    split = lock.create_split_curation(canonical, split_path, ["m1"], identity=HOST_B)
    split.accept("m1")

    # The split carries only the requested molecule + the label HOST-B wrote.
    split_labels = split.read_labels()
    assert split_labels.shape[0] == 1
    assert L._to_str(split_labels[0]["molecule_key"]) == "m1"
    assert split.curation_label("m1") == int(L.CurationLabel.ACCEPT)
    assert set(L.curation_labels(split_path)) == {"m1"}  # subset copy

    # The canonical file was never written (owner's view is untouched).
    assert owner.read_labels().shape[0] == 0
    assert owner.curation_label("m1") == int(L.CurationLabel.UNCURATED)
    # And a canonical write is still refused.
    with pytest.raises(LockedError):
        nonowner.reject("m1")


def test_split_curation_copies_all_when_keys_none(tmp_path: Path) -> None:
    canonical = _seed(tmp_path, [("m1", "cond"), ("m2", "cond")], name="canonical.tether")
    split = lock.create_split_curation(canonical, tmp_path / "split.tether", identity=HOST_B)
    assert set(L.curation_labels(split.path)) == {"m1", "m2"}


def test_split_missing_key_resolution_raises(tmp_path: Path) -> None:
    canonical = _seed(tmp_path, [("m1", "cond")], name="canonical.tether")
    split = lock.create_split_curation(
        canonical, tmp_path / "split.tether", ["m1"], identity=HOST_B
    )
    # A molecule not copied into the split cannot be curated there (never a silent no-op).
    with pytest.raises(KeyError):
        split.accept("m2")


def test_split_overwrite_refused_when_destination_locked(tmp_path: Path) -> None:
    # A destructive overwrite of the split destination is refused if another writer
    # holds its lock (never clobber a foreign-locked split, §5.4).
    canonical = _seed(tmp_path, [("m1", "cond")], name="canonical.tether")
    split_path = tmp_path / "split.tether"
    Project.create(split_path)  # the split already exists...
    lock.acquire(split_path, identity=HOST_A)  # ...and is locked by another writer
    with pytest.raises(LockedError):
        lock.create_split_curation(canonical, split_path, ["m1"], overwrite=True, identity=HOST_B)
