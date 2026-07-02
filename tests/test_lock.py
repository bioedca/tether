# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the single-writer ``.lock`` lifecycle (M2 S9, PRD §5.4/§7.10; FR-CONCURRENCY).

Covers the S9 gate (PLAN §6 S9): the ``<file>.lock`` prevents a second writer;
steal-lock recovers and a cross-machine lock/stale/steal case is exercised
(simulated host/PID); and a locked-out non-owner opens the canonical file
read-only yet writes curation ``/labels`` to a separate split ``.tether`` keyed by
``molecule_key`` while a write to the canonical file is still refused. Plus the
OneDrive conflict-copy detect-and-surface. All headless (no Qt).
"""

from __future__ import annotations

import json
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
