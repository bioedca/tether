# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The headless project/experiment core (PRD §4.2, §7.11).

[`Project`][] is the scriptable, display-free entry point to a ``.tether``
store. PRD §7.11 (FR-BATCH) requires every operation to be usable without the
GUI; the GUI ([`tether.gui`][]) is a thin layer over this core. This M0 scaffold
wraps the frozen [`tether.io`][] store lifecycle (create / open / version
compatibility) and the provisional filename→condition parse. Row-level
extraction (movies, molecules, traces) lands additively at M1+ — this never
mutates the M0-frozen schema, only the *data* inside it.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

# A signature default is evaluated at import time, so this one constant cannot come
# from the deferred ``from tether.project import idealize`` the methods below use. The
# edge is one-way — nothing under ``tether.idealize`` imports ``tether.project`` — so
# there is no cycle; it only makes an already-declared dependency (the TYPE_CHECKING
# import of ``IdealizationResult`` below) eager.
from tether.idealize.driver import DEFAULT_SIDECAR_TIMEOUT
from tether.io.filename import ParsedFilename, parse_filename
from tether.io.schema import (
    SCHEMA_VERSION,
    assert_is_compatible_project,
    create_project,
    read_schema_version,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping
    from contextlib import AbstractContextManager

    import numpy as np

    from tether.idealize.driver import IdealizationResult
    from tether.io.filename import ConditionKey
    from tether.io.movie import MovieReader
    from tether.project.conditions import (
        CategoryList,
        ClassLookup,
        ConditionSyncSummary,
        ConditionValidationReport,
        RekeyPreview,
        RekeyResult,
    )
    from tether.project.generation_reconcile import ConfirmResult
    from tether.project.handoff import AppliedReconcile, HandoffManifest, ReconcileReport
    from tether.project.idealize import StoredIdealization
    from tether.project.lock import LockIdentity, LockInfo, RecoveryAction
    from tether.project.movie_metadata import MovieMetadataRecord

__all__ = ["Project"]


class Project:
    """A handle to a ``.tether`` project store (the headless experiment model).

    A *condition* spans many movies across many days/files (PRD §5.1); this
    handle is the headless seam the batch runner (§7.11) and the GUI both build
    on. It is intentionally lightweight — it owns the *path*, not an open HDF5
    handle, so it is cheap to pass around and safe to construct without touching
    disk. Each operation opens the file for the minimum scope it needs.
    """

    def __init__(self, path: str | Path, *, identity: LockIdentity | None = None) -> None:
        self.path = Path(path)
        #: The single-writer identity this handle acts as (§5.4). ``None`` until
        #: first resolved to the local process by `_acting_identity`;
        #: overridable (e.g. to model another host/machine in tests, or a shared
        #: workstation login).
        self._identity: LockIdentity | None = identity
        #: Whether ``identity`` was explicitly supplied. An injected identity is
        #: fixed; an auto-resolved one is re-resolved if the PID changes (see
        #: `_acting_identity`) so a forked child never inherits the parent's.
        self._identity_injected: bool = identity is not None
        #: The lock this handle currently holds (set by `acquire_lock` /
        #: `steal_lock`, cleared by `release_lock`); ``None`` when the
        #: handle holds no lock.
        self._held_lock: LockInfo | None = None

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"Project({str(self.path)!r})"

    @classmethod
    def create(
        cls, path: str | Path, *, overwrite: bool = False, identity: LockIdentity | None = None
    ) -> Project:
        """Create a fresh ``.tether`` with the full frozen §5 skeleton.

        Thin wrapper over [`tether.io.schema.create_project`][]; refuses to
        clobber an existing file unless ``overwrite=True``. ``identity`` sets the
        handle's single-writer identity (§5.4; defaults to the local process). An
        ``overwrite=True`` that would truncate an existing project is refused
        (``LockedError``) when a **foreign** ``.lock`` or another local execution
        context's destination reservation is held, so the single-writer invariant is
        not bypassed by a destructive re-create.
        """
        if overwrite:
            from tether.project import lock

            lock.assert_writable(path, identity=identity)
        create_project(path, overwrite=overwrite)
        return cls(path, identity=identity)

    @classmethod
    def open(cls, path: str | Path, *, identity: LockIdentity | None = None) -> Project:
        """Open an existing ``.tether``, rejecting non-projects and future files.

        Validates the on-disk Tether contract — the file is readable HDF5 carrying
        the ``format`` marker (PRD §5.1), and its ``schema_version`` is not newer
        than this app (the §5.4 forward-compatibility guard) — before handing back
        a usable [`Project`][]. A foreign or partial HDF5 file is refused, not
        silently accepted ([`tether.io.schema.assert_is_compatible_project`][]).
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"no such .tether project: {path}")
        assert_is_compatible_project(path)
        return cls(path, identity=identity)

    @property
    def schema_version(self) -> int:
        """The on-disk ``schema_version`` of this project (PRD §5)."""
        return read_schema_version(self.path)

    @property
    def app_schema_version(self) -> int:
        """The schema version this build of Tether writes ([`SCHEMA_VERSION`][])."""
        return SCHEMA_VERSION

    @staticmethod
    def parse_condition(filename: str) -> ParsedFilename:
        """Parse a source filename into a **provisional** condition (PRD §7.6).

        The parse is filename-derived and must be human-validated at M4
        (PRD §7.6); [`ParsedFilename.condition_id`][] is the provisional id
        written to ``/molecules.condition_id_provisional`` at extraction.
        """
        return parse_filename(filename)

    def open_movie(self, movie_path: str | Path) -> MovieReader:
        """Open a source movie via the lazy reader ([`tether.io.movie.open_movie`][]).

        Imported lazily so the headless core stays importable without the imaging
        stack present (mirrors the GUI's lazy-import discipline).
        """
        from tether.io.movie import open_movie

        return open_movie(movie_path)

    # --- single-writer lock (PRD §5.4, §7.10; the concurrency seam) -----------

    def _acting_identity(self) -> LockIdentity:
        """This handle's single-writer identity, resolved to the local process.

        An **injected** identity is returned verbatim (tests / an explicit override).
        An **auto-resolved** identity is cached so the "is this lock ours?" judgment
        is stable and cheap, but is **re-resolved when the PID no longer matches** —
        so a handle inherited across ``fork()`` never lets a child impersonate the
        parent as the lock owner (the "single live process is one owner" contract).
        On such a fork the inherited `_held_lock` is also dropped, so a child
        cannot [`release_lock`][] (or refresh) the parent's lock.
        """
        from tether.project import lock

        if self._identity is None:
            self._identity = lock.local_identity()
        elif not self._identity_injected and self._identity.pid != os.getpid():
            # Forked child: the inherited identity/held-lock belong to the parent.
            self._identity = lock.local_identity()
            self._held_lock = None
        return self._identity

    def _assert_writable(self) -> None:
        """Refuse a canonical write if another writer context holds the destination.

        The single-writer boundary every canonical mutator on this handle passes
        through: an untransferred local reservation or foreign/corrupt sidecar raises
        [`LockedError`][tether.project.lock.LockedError]; the owning reservation context and
        self-owned sidecar pass. Reads never call this — a non-owner may always browse
        read-only (§7.10).
        """
        from tether.project import lock

        lock.assert_writable(self.path, identity=self._acting_identity())

    def _assert_held_lock(self, *, expected_nonce: str | None = None) -> str:
        """Require and return this retained session's exact ownership nonce.

        GUI sessions retain a lock across long background writes. An identity-only
        writer check is insufficient for their point-of-persistence guard because a
        stolen lock may be released before the worker returns. This stricter boundary
        refuses an absent, corrupt, or replacement sidecar. ``expected_nonce`` binds
        a long-running write to the epoch it validated before work began. Retained
        refresh preserves that nonce, so a timer heartbeat may interleave this read
        without spuriously changing the ownership epoch.
        """
        from tether.project import lock

        self._acting_identity()  # fork-safety: clear a child's inherited held lock
        held = self._held_lock
        try:
            current = lock.read_lock(self.path)
        except lock.CorruptLockError as exc:
            raise lock.LockedError(None, corrupt=True, path=self.lock_path) from exc
        if (
            held is None
            or current is None
            or current.nonce != held.nonce
            or (expected_nonce is not None and held.nonce != expected_nonce)
        ):
            raise lock.LockedError(current, path=self.lock_path)
        return held.nonce

    @property
    def lock_path(self) -> Path:
        """The sidecar ``<file>.lock`` path for this project (§5.1/§5.4)."""
        from tether.project import lock

        return lock.lock_path(self.path)

    def lock_owner(self) -> LockInfo | None:
        """The current lock holder, or ``None`` if unlocked (`lock.read_lock`).

        Raises [`CorruptLockError`][tether.project.lock.CorruptLockError] if the sidecar exists
        but cannot be parsed.
        """
        from tether.project import lock

        return lock.read_lock(self.path)

    def is_locked_by_other(self) -> LockInfo | None:
        """The foreign lock holder if the file is locked by someone else, else ``None``.

        Drives the GUI read-only banner (M2 S9 PR-B): returns ``None`` when the file
        is unlocked or the lock is ours, and the holding [`LockInfo`][] otherwise.
        This reports sidecar ownership only; a canonical write can still fail when
        another local execution context holds a process reservation.
        """
        owner = self.lock_owner()
        if owner is None or owner.identity == self._acting_identity():
            return None
        return owner

    def acquire_lock(self, *, steal: bool = False, timeout_s: float | None = None) -> LockInfo:
        """Acquire the single-writer lock for this handle (`lock.acquire`).

        Raises [`LockedError`][tether.project.lock.LockedError] if a foreign lock or another
        local execution context's reservation blocks acquisition. With no reservation,
        ``steal=True`` may replace a sidecar; a stale lock still requires that explicit
        steal (§5.4). ``timeout_s`` overrides the staleness window (default
        [`DEFAULT_STALENESS_TIMEOUT_S`][tether.project.lock.DEFAULT_STALENESS_TIMEOUT_S]).
        """
        from tether.project import lock

        info = lock.acquire(
            self.path,
            identity=self._acting_identity(),
            timeout_s=lock.DEFAULT_STALENESS_TIMEOUT_S if timeout_s is None else timeout_s,
            steal=steal,
        )
        self._held_lock = info
        return info

    def refresh_lock(self) -> LockInfo:
        """Refresh this retained lock's timestamp without changing its nonce.

        This is the long-lived GUI-session heartbeat. It requires the sidecar to
        carry the exact nonce already held by this handle and never recreates a
        vanished lock, so ownership lost and later released is still detected as a
        broken epoch rather than silently reacquired.
        """
        from tether.project import lock

        self._assert_held_lock()
        held = self._held_lock
        assert held is not None  # established by _assert_held_lock
        refreshed = lock.refresh(self.path, held)
        self._held_lock = refreshed
        return refreshed

    def steal_lock(self) -> tuple[LockInfo, LockInfo | None]:
        """Replace the sidecar lock, returning ``(new_lock, ousted_owner_or_None)``.

        The ousted owner is what the GUI surfaces to warn the stealer (§5.4); the
        typed-confirmation UX is M2 S9 PR-B. Last-write-wins — the prior owner's
        unsaved work is not merged back. A process-local reservation held by another
        execution context is never stolen and raises
        [`LockedError`][tether.project.lock.LockedError].
        """
        from tether.project import lock

        info, prior = lock.steal_lock(self.path, identity=self._acting_identity())
        self._held_lock = info
        return info, prior

    def record_lock_takeover(
        self, action: RecoveryAction | str, taken: LockInfo, ousted: LockInfo | None
    ) -> np.ndarray:
        """Keep a durable record of a successful Recover or Take over (#1099, ADR-0121).

        Appends one row to ``/settings/lock_takeovers``
        ([`lock_takeovers.record_takeover`][tether.project.lock_takeovers.record_takeover])
        for the ``(taken, ousted)`` pair that
        [`recover_lock`][tether.project.lock.recover_lock] returned. Call it once the
        project has been reopened writable under the new lock; it goes through the same
        single-writer check as every other write (`_assert_writable`, §5.4), so a lock
        lost again in the meantime raises [`LockedError`][tether.project.lock.LockedError]
        and nothing is written.
        """
        from tether.project import lock_takeovers

        self._assert_writable()
        return lock_takeovers.record_takeover(self.path, action, taken, ousted)

    def read_lock_takeovers(self) -> np.ndarray:
        """The append-only takeover record (`lock_takeovers.read_takeovers`); may be empty."""
        from tether.project import lock_takeovers

        return lock_takeovers.read_takeovers(self.path)

    def release_lock(self) -> bool:
        """Release the lock this handle holds; ``False`` if it holds none / lost it.

        Nonce-checked ([`lock.release`][tether.project.lock.release]): never deletes a lock that was
        stolen
        away in the meantime. Resolving the identity first drops an inherited
        ``_held_lock`` in a forked child (see `_acting_identity`), so a child
        never releases the parent's lock. After a ``False`` result, a readable
        missing or replacement sidecar definitively ends this handle's epoch and
        clears the retained nonce. An unreadable sidecar, or the original nonce
        still being present, is indeterminate and retains the nonce so lifecycle
        owners can retry rather than dropping their claim.
        """
        from tether.project import lock

        self._acting_identity()  # fork-safety: clears a child's inherited held lock
        held = self._held_lock
        if held is None:
            return False
        released = lock.release(self.path, held)
        if released:
            self._held_lock = None
            return True
        try:
            current = lock.read_lock(self.path)
        except lock.CorruptLockError:
            return False
        if current is None or current.nonce != held.nonce:
            self._held_lock = None
        return released

    def write_lock(
        self, *, steal: bool = False, timeout_s: float | None = None
    ) -> AbstractContextManager[LockInfo]:
        """A context manager that holds the single-writer lock for a write session.

        ``with project.write_lock():`` acquires on entry (raising
        [`LockedError`][tether.project.lock.LockedError] if blocked and not stealing) and
        releases on exit; the release is nonce-checked, so a lock stolen mid-session
        is not clobbered.
        """
        from tether.project import lock

        return lock.held_lock(
            self.path,
            identity=self._acting_identity(),
            timeout_s=lock.DEFAULT_STALENESS_TIMEOUT_S if timeout_s is None else timeout_s,
            steal=steal,
        )

    # --- curation (PRD §7.5; the scriptable seam behind the GUI keymap) -------

    def accept(self, molecule_key: str, **provenance: object) -> np.ndarray:
        """Accept a molecule, logging the ``/labels`` event (`labels.accept`).

        Refuses the canonical write if the file is locked by another writer
        (`_assert_writable`, §5.4).
        """
        from tether.project import labels

        self._assert_writable()
        return labels.accept(self.path, molecule_key, **provenance)

    def reject(self, molecule_key: str, **provenance: object) -> np.ndarray:
        """Reject a molecule (reversible sticky tag, `labels.reject`).

        ``reason`` / ``note`` / ``value`` pass through to
        [`labels.reject`][tether.project.labels.reject], which
        records them in ``/labels/reject_log``. Refuses the canonical write if the file
        is locked by another writer (§5.4).
        """
        from tether.project import labels

        self._assert_writable()
        return labels.reject(self.path, molecule_key, **provenance)

    def unreject(self, molecule_key: str, **provenance: object) -> np.ndarray | None:
        """Un-reject a molecule; ``None`` if it was not rejected (`labels.unreject`).

        Refuses the canonical write if the file is locked by another writer (§5.4).
        """
        from tether.project import labels

        self._assert_writable()
        return labels.unreject(self.path, molecule_key, **provenance)

    def restore_curation(
        self, molecule_key: str, label: int, **options: object
    ) -> list[np.ndarray]:
        """Write ``label`` back as the molecule's curation state, by new events only.

        The writer behind an undo or redo of an accept, reject or un-reject (#632): the
        ``/labels`` log gains rows and none is deleted. ``pending``, ``reason``, ``note``,
        ``value``, ``restore_note`` and the provenance keywords pass through to
        [`labels.restore_curation`][tether.project.labels.restore_curation]. Refuses the
        canonical write if the file is locked by another writer (§5.4).
        """
        from tether.project import labels

        self._assert_writable()
        return labels.restore_curation(self.path, molecule_key, label, **options)  # type: ignore[arg-type]

    def confirm_carried(self, molecule_keys: Iterable[str], **provenance: object) -> ConfirmResult:
        """Confirm carried molecules of a new trace generation (#923).

        Removes the mark *carried* and records who confirmed and when
        ([`confirm_carried`][tether.project.generation_reconcile.confirm_carried]; the
        keyword arguments ``labeler``, ``timestamp`` and ``source_file`` pass through).
        Refuses the canonical write if the file is locked by another writer (§5.4).
        """
        from tether.project import generation_reconcile

        self._assert_writable()
        return generation_reconcile.confirm_carried(self.path, molecule_keys, **provenance)  # type: ignore[arg-type]

    def curation_label(self, molecule_key: str) -> int:
        """The molecule's current ``curation_label`` (`labels.curation_label_of`)."""
        from tether.project import labels

        return labels.curation_label_of(self.path, molecule_key)

    def read_labels(self) -> np.ndarray:
        """The ``/labels/table`` provenance log (`labels.read_labels`)."""
        from tether.project import labels

        return labels.read_labels(self.path)

    def reject_record(self, molecule_key: str) -> np.ndarray | None:
        """The reason row of the molecule's current reject (`labels.reject_record_of`)."""
        from tether.project import labels

        return labels.reject_record_of(self.path, molecule_key)

    def rejected_molecule_keys(self) -> set[str]:
        """The molecules currently rejected (`labels.rejected_molecule_keys`)."""
        from tether.project import labels

        return labels.rejected_molecule_keys(self.path)

    # --- conditions (PRD §5.1, §7.6; the annotation/referential-validation seam) --

    def sync_conditions(self) -> ConditionSyncSummary:
        """Materialize the ``/conditions`` rows ``/molecules`` reference (§5.1).

        Delegates to [`conditions.sync_conditions`][tether.project.conditions.sync_conditions];
        refuses the write if the
        file is locked by another writer (`_assert_writable`, §5.4).
        """
        from tether.project import conditions

        self._assert_writable()
        return conditions.sync_conditions(self.path)

    def read_conditions(self) -> np.ndarray:
        """The structured ``/conditions/table`` (`conditions.read_conditions`)."""
        from tether.project import conditions

        return conditions.read_conditions(self.path)

    def read_condition_keys(self) -> dict[str, ConditionKey]:
        """Each materialized ``condition_id`` → its identity [`ConditionKey`][] (§5.1).

        Delegates to
        [`conditions.read_condition_keys`][tether.project.conditions.read_condition_keys]; the map
        behind querying a
        condition by key fields across its movies ([`tether.analysis.query_molecules`][]).
        """
        from tether.project import conditions

        return conditions.read_condition_keys(self.path)

    def validate_conditions(self) -> ConditionValidationReport:
        """Referential-integrity report of ``/molecules`` → ``/conditions`` (§5.1).

        Delegates to
        [`conditions.validate_conditions`][tether.project.conditions.validate_conditions].
        """
        from tether.project import conditions

        return conditions.validate_conditions(self.path)

    def molecules_by_condition(self) -> dict[str, list[str]]:
        """Each ``condition_id`` → its aggregated ``molecule_key`` list (§5.1).

        Delegates to
        [`conditions.aggregate_molecules_by_condition`][tether.project.conditions.aggregate_molecules_by_condition].
        """
        from tether.project import conditions

        return conditions.aggregate_molecules_by_condition(self.path)

    def preview_rekey(self, from_condition_id: str, to_key: ConditionKey) -> RekeyPreview:
        """Read-only preview of a condition re-key (`conditions.preview_rekey`)."""
        from tether.project import conditions

        return conditions.preview_rekey(self.path, from_condition_id, to_key)

    def rekey_condition(
        self,
        from_condition_id: str,
        to_key: ConditionKey,
        *,
        confirm: bool = False,
        labeler: str | None = None,
        reason: str = "",
        timestamp: str | None = None,
    ) -> RekeyResult:
        """Transactionally re-key a condition; a merge needs ``confirm=True`` (§5.1).

        Delegates to [`conditions.rekey_condition`][tether.project.conditions.rekey_condition];
        refuses the write if the file
        is locked by another writer (`_assert_writable`, §5.4).
        """
        from tether.project import conditions

        self._assert_writable()
        return conditions.rekey_condition(
            self.path,
            from_condition_id,
            to_key,
            confirm=confirm,
            labeler=labeler,
            reason=reason,
            timestamp=timestamp,
        )

    def read_condition_audit(self) -> np.ndarray:
        """The append-only re-key/merge audit log (`conditions.read_condition_audit`)."""
        from tether.project import conditions

        return conditions.read_condition_audit(self.path)

    # --- editable per-condition category list (PRD §5.1; FR-ANNOTATE) ---------

    def category_list(self, condition_id: str) -> CategoryList:
        """A condition's editable per-trace category vocabulary (§5.1).

        Delegates to
        [`conditions.read_category_list`][tether.project.conditions.read_category_list]; read-only,
        returns an
        empty list when the condition has no categories yet.
        """
        from tether.project import conditions

        return conditions.read_category_list(self.path, condition_id)

    def class_lookup(self, condition_id: str) -> ClassLookup:
        """A condition's persisted tMAVEN class ↔ category lookup (§7.4; ADR-0125).

        Delegates to
        [`conditions.read_class_lookup`][tether.project.conditions.read_class_lookup];
        read-only.
        """
        from tether.project import conditions

        return conditions.read_class_lookup(self.path, condition_id)

    def set_category_list(self, condition_id: str, categories: object) -> CategoryList:
        """Replace a condition's whole ordered category list (`conditions.set_category_list`).

        Refuses the write if the file is locked by another writer (`_assert_writable`, §5.4).
        """
        from tether.project import conditions

        self._assert_writable()
        return conditions.set_category_list(self.path, condition_id, categories)

    def add_category(self, condition_id: str, name: str) -> CategoryList:
        """Append one category to a condition's list (`conditions.add_category`).

        Refuses the write if the file is locked by another writer (§5.4).
        """
        from tether.project import conditions

        self._assert_writable()
        return conditions.add_category(self.path, condition_id, name)

    def rename_category(self, condition_id: str, old: str, new: str) -> CategoryList:
        """Rename a category in place, preserving its code (`conditions.rename_category`).

        Refuses the write if the file is locked by another writer (§5.4).
        """
        from tether.project import conditions

        self._assert_writable()
        return conditions.rename_category(self.path, condition_id, old, new)

    def remove_category(self, condition_id: str, name: str) -> CategoryList:
        """Remove a category from a condition's list (`conditions.remove_category`).

        Refuses the write if the file is locked by another writer (§5.4).
        """
        from tether.project import conditions

        self._assert_writable()
        return conditions.remove_category(self.path, condition_id, name)

    # --- per-molecule category + analysis window (PRD §5.1, §7.2; #358) --------

    def set_category(
        self,
        molecule_key: str,
        category: str,
        *,
        labeler: str | None = None,
        reason: str = "",
        timestamp: str | None = None,
    ) -> np.ndarray:
        """Set a molecule's category to a name in its condition's list
        ([`molecule_edits.set_category`][tether.project.molecule_edits.set_category]).

        The condition's [`category_list`][] is the vocabulary: a name not in it is
        refused. Refuses the write if the file is locked by another writer
        (`_assert_writable`, §5.4).
        """
        from tether.project import molecule_edits

        self._assert_writable()
        return molecule_edits.set_category(
            self.path,
            molecule_key,
            category,
            labeler=labeler,
            reason=reason,
            timestamp=timestamp,
        )

    def clear_category(
        self,
        molecule_key: str,
        *,
        labeler: str | None = None,
        reason: str = "",
        timestamp: str | None = None,
    ) -> np.ndarray:
        """Clear a molecule's category to ``""`` (`molecule_edits.clear_category`).

        Refuses the write if the file is locked by another writer (§5.4).
        """
        from tether.project import molecule_edits

        self._assert_writable()
        return molecule_edits.clear_category(
            self.path, molecule_key, labeler=labeler, reason=reason, timestamp=timestamp
        )

    def set_analysis_window(
        self,
        molecule_key: str,
        start: int,
        stop: int,
        *,
        labeler: str | None = None,
        reason: str = "",
        timestamp: str | None = None,
    ) -> np.ndarray:
        """Set a molecule's ``[start, stop)`` analysis window, clamped to its frames
        ([`molecule_edits.set_analysis_window`][tether.project.molecule_edits.set_analysis_window]).

        A window narrower than ``frame_range`` is a manual override that photobleach
        detection leaves alone. Refuses the write if the file is locked by another
        writer (§5.4).
        """
        from tether.project import molecule_edits

        self._assert_writable()
        return self._window_edit(
            labeler,
            lambda path: molecule_edits.set_analysis_window(
                path,
                molecule_key,
                start,
                stop,
                labeler=labeler,
                reason=reason,
                timestamp=timestamp,
            ),
        )

    def reset_analysis_window(
        self,
        molecule_key: str,
        *,
        labeler: str | None = None,
        reason: str = "",
        timestamp: str | None = None,
    ) -> np.ndarray:
        """Restore a molecule's analysis window to its ``frame_range``
        ([`molecule_edits.reset_analysis_window`][tether.project.molecule_edits.reset_analysis_window]).

        Refuses the write if the file is locked by another writer (§5.4).
        """
        from tether.project import molecule_edits

        self._assert_writable()
        return self._window_edit(
            labeler,
            lambda path: molecule_edits.reset_analysis_window(
                path, molecule_key, labeler=labeler, reason=reason, timestamp=timestamp
            ),
        )

    def restore_analysis_windows(
        self,
        molecule_key: str,
        windows: Mapping[str, tuple[int, int]],
        *,
        labeler: str | None = None,
        reason: str = "",
        timestamp: str | None = None,
    ) -> np.ndarray:
        """Give each row of a molecule its own window back, by ``molecule_id``
        ([`molecule_edits.restore_analysis_windows`][tether.project.molecule_edits.restore_analysis_windows]).

        The writer behind an undo or redo of a window edit (#632). Refuses the write if
        the file is locked by another writer (§5.4).
        """
        from tether.project import molecule_edits

        self._assert_writable()
        return self._window_edit(
            labeler,
            lambda path: molecule_edits.restore_analysis_windows(
                path,
                molecule_key,
                windows,
                labeler=labeler,
                reason=reason,
                timestamp=timestamp,
            ),
        )

    def _window_edit(self, labeler: str | None, write: Callable[[Path], np.ndarray]) -> np.ndarray:
        """Run an analysis-window ``write``; in ANALYSIS_READY a change returns to review.

        G03's *edit analysis windows or curation* (#928, ADR-0156): a window that changed in
        ANALYSIS_READY sends the project to CORRECTED_REVIEW. The calibration windows and the
        γ evidence are not touched. In that state the window is written into a staged copy,
        the event is stored, and the copy (with the stored transition) is published: a failed
        event publishes no window, and a window is never stored in ANALYSIS_READY.
        """
        from tether.project import correction_states as cs
        from tether.project import lock
        from tether.project._guarded_publish import guarded_project_write
        from tether.project.calibration_windows import _copy_transitions

        with cs._transition_lock(self.path):
            ready = cs.read_correction_state(self.path).state is cs.CorrectionState.ANALYSIS_READY
            if not ready:
                return write(self.path)
            epoch = cs._held_nonce(self)

            def guard() -> None:
                if cs._held_nonce(self) != epoch:
                    raise lock.LockedError(lock.read_lock(self.path), path=self.lock_path)

            with guarded_project_write(self.path, write_guard=guard, label="window") as staged:
                written = write(staged)
                if len(written):
                    cs.send_event(
                        self,
                        cs.CorrectionEvent.EDIT_WINDOWS_OR_CURATION,
                        cs.Actor.person(labeler),
                        note=f"analysis window of {len(written)} row(s) changed",
                    )
                    _copy_transitions(self.path, staged)
        return written

    def restore_categories(
        self,
        molecule_key: str,
        categories: Mapping[str, str],
        *,
        labeler: str | None = None,
        reason: str = "",
        timestamp: str | None = None,
    ) -> np.ndarray:
        """Give each row of a molecule its own category back, by ``molecule_id``
        ([`molecule_edits.restore_categories`][tether.project.molecule_edits.restore_categories]).

        The writer behind an undo or redo of a category edit (#632). Refuses the write if
        the file is locked by another writer (§5.4).
        """
        from tether.project import molecule_edits

        self._assert_writable()
        return molecule_edits.restore_categories(
            self.path, molecule_key, categories, labeler=labeler, reason=reason, timestamp=timestamp
        )

    def read_molecule_audit(self) -> np.ndarray:
        """The append-only per-molecule edit log (`molecule_edits.read_molecule_audit`)."""
        from tether.project import molecule_edits

        return molecule_edits.read_molecule_audit(self.path)

    # --- movie metadata with its origin (#911; §7.1, §11.1 units) --------------

    def movie_metadata(self) -> dict[str, MovieMetadataRecord]:
        """Return each movie's frame time, pixel size and channel roles with their origins.

        The call only reads; it delegates to
        [`movie_metadata.read_movie_metadata`][tether.project.movie_metadata.read_movie_metadata].

        Returns
        -------
        dict of str to MovieMetadataRecord
            One [`MovieMetadataRecord`][tether.project.movie_metadata.MovieMetadataRecord]
            for each ``/movies`` row, keyed by ``movie_id``. Its ``frame_time`` is in
            seconds per frame and its ``pixel_size_um`` in micrometers per pixel;
            ``0.0`` means unknown.

        Examples
        --------
        >>> import tempfile
        >>> from pathlib import Path
        >>> import h5py
        >>> from tether.project.core import Project
        >>> project = Project.create(Path(tempfile.mkdtemp()) / "demo.tether")
        >>> with h5py.File(project.path, "r+") as f:  # one /movies row, as extraction writes it
        ...     table = f["movies/table"]
        ...     table.resize((1,))
        ...     row = table[0]
        ...     row["movie_id"] = "mov-1"
        ...     table[0] = row
        >>> project.movie_metadata()["mov-1"].frame_time_origin
        'unknown'
        """
        from tether.project import movie_metadata

        return movie_metadata.read_movie_metadata(self.path)

    def set_frame_time(self, movie_id: str, seconds: float) -> MovieMetadataRecord:
        """Give a movie's frame time, with origin ``user`` and not verified.

        Delegates to
        [`movie_metadata.set_frame_time`][tether.project.movie_metadata.set_frame_time]
        after the single-writer check.

        Parameters
        ----------
        movie_id : str
            The ``movie_id`` of the ``/movies`` row to change.
        seconds : float
            The frame time, in seconds per frame. It must be finite and greater than
            zero.

        Returns
        -------
        MovieMetadataRecord
            The record as written.

        Raises
        ------
        tether.project.lock.LockedError
            If the project's writer lock blocks the operation (§5.4).
        KeyError
            If no ``/movies`` row has ``movie_id``.
        ValueError
            If ``seconds`` is not finite and positive.

        Examples
        --------
        >>> import tempfile
        >>> from pathlib import Path
        >>> import h5py
        >>> from tether.project.core import Project
        >>> project = Project.create(Path(tempfile.mkdtemp()) / "demo.tether")
        >>> with h5py.File(project.path, "r+") as f:  # one /movies row, as extraction writes it
        ...     table = f["movies/table"]
        ...     table.resize((1,))
        ...     row = table[0]
        ...     row["movie_id"] = "mov-1"
        ...     table[0] = row
        >>> record = project.set_frame_time("mov-1", 0.05)
        >>> record.frame_time, record.frame_time_origin, record.frame_time_verified
        (0.05, 'user', False)
        """
        from tether.project import movie_metadata

        self._assert_writable()
        return movie_metadata.set_frame_time(self.path, movie_id, seconds)

    def confirm_frame_time(self, movie_id: str) -> MovieMetadataRecord:
        """Verify a movie's frame time, the user's separate action.

        Delegates to
        [`movie_metadata.confirm_frame_time`][tether.project.movie_metadata.confirm_frame_time]
        after the single-writer check.

        Parameters
        ----------
        movie_id : str
            The ``movie_id`` of the ``/movies`` row to change.

        Returns
        -------
        MovieMetadataRecord
            The record as written, with ``frame_time_verified`` set to ``True``.

        Raises
        ------
        tether.project.lock.LockedError
            If the project's writer lock blocks the operation (§5.4).
        KeyError
            If no ``/movies`` row has ``movie_id``.
        ValueError
            If the movie has no frame time to confirm (its frame time is ``0.0``).

        Examples
        --------
        >>> import tempfile
        >>> from pathlib import Path
        >>> import h5py
        >>> from tether.project.core import Project
        >>> project = Project.create(Path(tempfile.mkdtemp()) / "demo.tether")
        >>> with h5py.File(project.path, "r+") as f:  # one /movies row, as extraction writes it
        ...     table = f["movies/table"]
        ...     table.resize((1,))
        ...     row = table[0]
        ...     row["movie_id"] = "mov-1"
        ...     table[0] = row
        >>> record = project.set_frame_time("mov-1", 0.05)
        >>> project.confirm_frame_time("mov-1").frame_time_verified
        True
        """
        from tether.project import movie_metadata

        self._assert_writable()
        return movie_metadata.confirm_frame_time(self.path, movie_id)

    def update_movie_metadata(
        self,
        movie_id: str,
        *,
        frame_time: float | None = None,
        pixel_size_um: float | None = None,
        confirm_frame_time: bool = False,
    ) -> MovieMetadataRecord:
        """Apply several metadata changes to one movie.

        Delegates to
        [`movie_metadata.update_movie_metadata`][tether.project.movie_metadata.update_movie_metadata]
        after the single-writer check. A given value gets the origin ``user``, and a
        given frame time starts unverified. A validation error leaves the record untouched.

        Parameters
        ----------
        movie_id : str
            The ``movie_id`` of the ``/movies`` row to change.
        frame_time : float or None, optional
            The frame time to record, in seconds per frame. It must be finite and
            greater than zero. The default, ``None``, leaves the frame time unchanged.
        pixel_size_um : float or None, optional
            The pixel size to record, in micrometers per pixel. It must be finite and
            greater than zero. The default, ``None``, leaves the pixel size unchanged.
        confirm_frame_time : bool, optional
            Mark the stored frame time as verified. The default is ``False``.

        Returns
        -------
        MovieMetadataRecord
            The record as written, with every requested change applied.

        Raises
        ------
        tether.project.lock.LockedError
            If the project's writer lock blocks the operation (§5.4).
        KeyError
            If no ``/movies`` row has ``movie_id``.
        ValueError
            If ``frame_time`` or ``pixel_size_um`` is not finite and positive, if
            ``confirm_frame_time`` is set and the movie has no frame time, or if
            ``frame_time`` is given and ``confirm_frame_time`` is set in one call.

        Examples
        --------
        >>> import tempfile
        >>> from pathlib import Path
        >>> import h5py
        >>> from tether.project.core import Project
        >>> project = Project.create(Path(tempfile.mkdtemp()) / "demo.tether")
        >>> with h5py.File(project.path, "r+") as f:  # one /movies row, as extraction writes it
        ...     table = f["movies/table"]
        ...     table.resize((1,))
        ...     row = table[0]
        ...     row["movie_id"] = "mov-1"
        ...     table[0] = row
        >>> record = project.update_movie_metadata("mov-1", frame_time=0.05, pixel_size_um=0.16)
        >>> record.frame_time, record.pixel_size_um, record.frame_time_verified
        (0.05, 0.16, False)
        """
        from tether.project import movie_metadata

        self._assert_writable()
        return movie_metadata.update_movie_metadata(
            self.path,
            movie_id,
            frame_time=frame_time,
            pixel_size_um=pixel_size_um,
            confirm_frame_time=confirm_frame_time,
        )

    def set_pixel_size(self, movie_id: str, micrometres: float) -> MovieMetadataRecord:
        """Give a movie's pixel size, with origin ``user``.

        Delegates to
        [`movie_metadata.set_pixel_size`][tether.project.movie_metadata.set_pixel_size]
        after the single-writer check.

        Parameters
        ----------
        movie_id : str
            The ``movie_id`` of the ``/movies`` row to change.
        micrometres : float
            The pixel size, in micrometers per pixel. It must be finite and greater
            than zero.

        Returns
        -------
        MovieMetadataRecord
            The record as written.

        Raises
        ------
        tether.project.lock.LockedError
            If the project's writer lock blocks the operation (§5.4).
        KeyError
            If no ``/movies`` row has ``movie_id``.
        ValueError
            If ``micrometres`` is not finite and positive.

        Examples
        --------
        >>> import tempfile
        >>> from pathlib import Path
        >>> import h5py
        >>> from tether.project.core import Project
        >>> project = Project.create(Path(tempfile.mkdtemp()) / "demo.tether")
        >>> with h5py.File(project.path, "r+") as f:  # one /movies row, as extraction writes it
        ...     table = f["movies/table"]
        ...     table.resize((1,))
        ...     row = table[0]
        ...     row["movie_id"] = "mov-1"
        ...     table[0] = row
        >>> record = project.set_pixel_size("mov-1", 0.16)
        >>> record.pixel_size_um, record.pixel_size_origin
        (0.16, 'user')
        """
        from tether.project import movie_metadata

        self._assert_writable()
        return movie_metadata.set_pixel_size(self.path, movie_id, micrometres)

    # --- idealization (PRD §7.4; the one-click-vbFRET seam behind the GUI) -----

    def idealize(
        self,
        molecule_keys: list[str] | None = None,
        *,
        model_type: str = "vbconhmm",
        nstates: int | None = None,
        nstates_grid: tuple[int, ...] = (1, 2, 3, 4),
        model_name: str | None = None,
        intensity_quantity: str = "corrected",
        sidecar_python: str | Path | None = None,
        nrestarts: int | None = None,
        scratch_dir: str | Path | None = None,
        timeout: float | None = DEFAULT_SIDECAR_TIMEOUT,
        overwrite: bool = False,
        include_rejected: bool = False,
        _runner: Callable[..., IdealizationResult] | None = None,
    ) -> StoredIdealization:
        """Idealize selected molecules into ``/idealization`` (`idealize.idealize_molecules`).

        The headless core behind the dock's ``I`` key: reads the selected molecules'
        traces, fits vbFRET / consensus VB-HMM via the sidecar, and writes the model
        back as additive data with a per-molecule input-provenance hash. Rejected
        molecules are excluded unless ``include_rejected=True``. The defaults mirror
        [`tether.project.idealize.idealize_molecules`][] (its
        ``MODEL_TYPE_DEFAULT`` / ``NSTATES_GRID_DEFAULT``); ``_runner`` is a private
        test seam for injecting a fake sidecar.
        """
        from tether.project import idealize

        self._assert_writable()
        # Only forward the private runner override when supplied, so the module
        # default (`run_vbfret`) is used otherwise.
        extra = {} if _runner is None else {"_runner": _runner}
        return idealize.idealize_molecules(
            self,
            molecule_keys,
            model_type=model_type,
            nstates=nstates,
            nstates_grid=nstates_grid,
            model_name=model_name,
            intensity_quantity=intensity_quantity,
            sidecar_python=sidecar_python,
            nrestarts=nrestarts,
            scratch_dir=scratch_dir,
            timeout=timeout,
            overwrite=overwrite,
            include_rejected=include_rejected,
            **extra,
        )

    def read_idealization(self, model_name: str) -> StoredIdealization:
        """Read a persisted model (`idealize.read_idealization`)."""
        from tether.project import idealize

        return idealize.read_idealization(self, model_name)

    def list_idealizations(self) -> list[str]:
        """Names of the models under ``/idealization`` (`idealize.list_idealizations`)."""
        from tether.project import idealize

        return idealize.list_idealizations(self)

    def stale_idealization_keys(self, model_name: str) -> list[str]:
        """Molecules whose inputs changed since the fit (`idealize.stale_molecule_keys`)."""
        from tether.project import idealize

        return idealize.stale_molecule_keys(self, model_name)

    def live_idealization_keys(self, model_name: str) -> list[str]:
        """Non-stale molecules TDP/dwell analysis includes (`idealize.live_molecule_keys`)."""
        from tether.project import idealize

        return idealize.live_molecule_keys(self, model_name)

    def reidealize(
        self,
        model_name: str,
        *,
        sidecar_python: str | Path | None = None,
        nrestarts: int | None = None,
        scratch_dir: str | Path | None = None,
        timeout: float | None = DEFAULT_SIDECAR_TIMEOUT,
        _runner: Callable[..., IdealizationResult] | None = None,
    ) -> StoredIdealization:
        """Re-fit a stale model over current inputs (`idealize.reidealize`)."""
        from tether.project import idealize

        self._assert_writable()
        return idealize.reidealize(
            self,
            model_name,
            sidecar_python=sidecar_python,
            nrestarts=nrestarts,
            scratch_dir=scratch_dir,
            timeout=timeout,
            _runner=_runner,
        )

    # --- tMAVEN hand-off + non-destructive re-import (PRD §7.4, §5.3) ----------

    def hand_off_to_tmaven(
        self,
        molecule_keys: list[str] | None = None,
        *,
        out_path: str | Path,
        intensity_quantity: str = "corrected",
        overwrite: bool = True,
    ) -> HandoffManifest:
        """Export selected molecules to an SMD the standalone tMAVEN GUI opens
        ([`handoff.hand_off_to_tmaven`][tether.project.handoff.hand_off_to_tmaven])."""
        from tether.project import handoff

        return handoff.hand_off_to_tmaven(
            self,
            molecule_keys,
            out_path=out_path,
            intensity_quantity=intensity_quantity,
            overwrite=overwrite,
        )

    def read_return_leg(
        self,
        smd_path: str | Path,
        *,
        model_path: str | Path | None = None,
        intensity_quantity: str = "corrected",
        model_name: str | None = None,
    ) -> ReconcileReport:
        """Preview a returning tMAVEN SMD: intensity-match + reconcile diff
        ([`handoff.read_return_leg`][tether.project.handoff.read_return_leg])."""
        from tether.project import handoff

        return handoff.read_return_leg(
            self,
            smd_path,
            model_path=model_path,
            intensity_quantity=intensity_quantity,
            model_name=model_name,
        )

    def apply_reconcile(
        self,
        smd_path: str | Path,
        *,
        model_path: str | Path | None = None,
        intensity_quantity: str = "corrected",
        model_name: str | None = None,
        accept_windows: bool | Iterable[str] = False,
        accept_classes: bool | Iterable[str] = False,
        import_idealization: bool = False,
        overwrite: bool = False,
        require_held_lock: bool = False,
        previewed: ReconcileReport | None = None,
    ) -> AppliedReconcile:
        """Commit accepted return-leg changes, non-destructively
        ([`handoff.apply_reconcile`][tether.project.handoff.apply_reconcile]).

        Refuses the canonical write if the file is locked by another writer (§5.4).

        The entry check alone does not bound the write:
        [`handoff.apply_reconcile`][tether.project.handoff.apply_reconcile]
        re-resolves the match and reads the returned model before it persists anything,
        and a foreign writer can steal the lock during that work. The guard built here
        is therefore re-invoked at each point of persistence. ``require_held_lock`` is
        the retained-session contract used by the GUI — it binds every write to the
        exact ownership epoch validated here, so a stolen-then-released lock is still
        refused; the default identity-only guard is the batch/CLI boundary (§5.4).

        ``previewed`` is the report a prior
        [`read_return_leg`][tether.project.handoff.read_return_leg] returned; with it, an
        accepted class change whose category no longer matches that preview is not
        written (see [`handoff.apply_reconcile`][tether.project.handoff.apply_reconcile]).
        """
        from tether.project import handoff

        writer_guard: Callable[[], object]
        if require_held_lock:
            start_nonce = self._assert_held_lock()

            def writer_guard() -> None:
                self._assert_held_lock(expected_nonce=start_nonce)

        else:
            writer_guard = self._assert_writable
        writer_guard()
        return handoff.apply_reconcile(
            self,
            smd_path,
            model_path=model_path,
            intensity_quantity=intensity_quantity,
            model_name=model_name,
            accept_windows=accept_windows,
            accept_classes=accept_classes,
            import_idealization=import_idealization,
            overwrite=overwrite,
            writer_guard=writer_guard,
            previewed=previewed,
        )
