# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The headless project/experiment core (PRD §4.2, §7.11).

:class:`Project` is the scriptable, display-free entry point to a ``.tether``
store. PRD §7.11 (FR-BATCH) requires every operation to be usable without the
GUI; the GUI (:mod:`tether.gui`) is a thin layer over this core. This M0 scaffold
wraps the frozen :mod:`tether.io` store lifecycle (create / open / version
compatibility) and the provisional filename→condition parse. Row-level
extraction (movies, molecules, traces) lands additively at M1+ — this never
mutates the M0-frozen schema, only the *data* inside it.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from tether.io.filename import ParsedFilename, parse_filename
from tether.io.schema import (
    SCHEMA_VERSION,
    assert_is_compatible_project,
    create_project,
    read_schema_version,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from contextlib import AbstractContextManager

    import numpy as np

    from tether.idealize.driver import IdealizationResult
    from tether.io.filename import ConditionKey
    from tether.io.movie import MovieReader
    from tether.project.conditions import (
        CategoryList,
        ConditionSyncSummary,
        ConditionValidationReport,
        RekeyPreview,
        RekeyResult,
    )
    from tether.project.handoff import AppliedReconcile, HandoffManifest, ReconcileReport
    from tether.project.idealize import StoredIdealization
    from tether.project.lock import LockIdentity, LockInfo

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
        #: first resolved to the local process by :meth:`_acting_identity`;
        #: overridable (e.g. to model another host/machine in tests, or a shared
        #: workstation login).
        self._identity: LockIdentity | None = identity
        #: Whether ``identity`` was explicitly supplied. An injected identity is
        #: fixed; an auto-resolved one is re-resolved if the PID changes (see
        #: :meth:`_acting_identity`) so a forked child never inherits the parent's.
        self._identity_injected: bool = identity is not None
        #: The lock this handle currently holds (set by :meth:`acquire_lock` /
        #: :meth:`steal_lock`, cleared by :meth:`release_lock`); ``None`` when the
        #: handle holds no lock.
        self._held_lock: LockInfo | None = None

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"Project({str(self.path)!r})"

    @classmethod
    def create(
        cls, path: str | Path, *, overwrite: bool = False, identity: LockIdentity | None = None
    ) -> Project:
        """Create a fresh ``.tether`` with the full frozen §5 skeleton.

        Thin wrapper over :func:`tether.io.schema.create_project`; refuses to
        clobber an existing file unless ``overwrite=True``. ``identity`` sets the
        handle's single-writer identity (§5.4; defaults to the local process). An
        ``overwrite=True`` that would truncate an existing project is refused
        (``LockedError``) when a **foreign** ``.lock`` is held, so the single-writer
        invariant is not bypassed by a destructive re-create.
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
        a usable :class:`Project`. A foreign or partial HDF5 file is refused, not
        silently accepted (:func:`tether.io.schema.assert_is_compatible_project`).
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
        """The schema version this build of Tether writes (:data:`SCHEMA_VERSION`)."""
        return SCHEMA_VERSION

    @staticmethod
    def parse_condition(filename: str) -> ParsedFilename:
        """Parse a source filename into a **provisional** condition (PRD §7.6).

        The parse is filename-derived and must be human-validated at M4
        (PRD §7.6); :attr:`ParsedFilename.condition_id` is the provisional id
        written to ``/molecules.condition_id_provisional`` at extraction.
        """
        return parse_filename(filename)

    def open_movie(self, movie_path: str | Path) -> MovieReader:
        """Open a source movie via the lazy reader (:func:`tether.io.movie.open_movie`).

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
        On such a fork the inherited :attr:`_held_lock` is also dropped, so a child
        cannot :meth:`release_lock` (or refresh) the parent's lock.
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
        """Refuse a canonical write if a foreign lock is held (raises ``LockedError``, §5.4).

        The single-writer boundary every canonical mutator on this handle passes
        through: unlocked or self-owned -> silent; a foreign (or corrupt) lock ->
        :class:`~tether.project.lock.LockedError`. Reads never call this — a
        non-owner may always browse read-only (§7.10).
        """
        from tether.project import lock

        lock.assert_writable(self.path, identity=self._acting_identity())

    @property
    def lock_path(self) -> Path:
        """The sidecar ``<file>.lock`` path for this project (§5.1/§5.4)."""
        from tether.project import lock

        return lock.lock_path(self.path)

    def lock_owner(self) -> LockInfo | None:
        """The current lock holder, or ``None`` if unlocked (:func:`lock.read_lock`).

        Raises :class:`~tether.project.lock.CorruptLockError` if the sidecar exists
        but cannot be parsed.
        """
        from tether.project import lock

        return lock.read_lock(self.path)

    def is_locked_by_other(self) -> LockInfo | None:
        """The foreign lock holder if the file is locked by someone else, else ``None``.

        Drives the GUI read-only banner (M2 S9 PR-B): returns ``None`` when the file
        is unlocked or the lock is ours, and the holding :class:`LockInfo` otherwise.
        """
        owner = self.lock_owner()
        if owner is None or owner.identity == self._acting_identity():
            return None
        return owner

    def acquire_lock(self, *, steal: bool = False, timeout_s: float | None = None) -> LockInfo:
        """Acquire the single-writer lock for this handle (:func:`lock.acquire`).

        Raises :class:`~tether.project.lock.LockedError` if a foreign lock blocks
        acquisition and ``steal`` is ``False`` (a stale lock still requires an
        explicit steal, §5.4). ``timeout_s`` overrides the staleness window
        (default :data:`~tether.project.lock.DEFAULT_STALENESS_TIMEOUT_S`).
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

    def steal_lock(self) -> tuple[LockInfo, LockInfo | None]:
        """Force-acquire the lock, returning ``(new_lock, ousted_owner_or_None)``.

        The ousted owner is what the GUI surfaces to warn the stealer (§5.4); the
        typed-confirmation UX is M2 S9 PR-B. Last-write-wins — the prior owner's
        unsaved work is not merged back.
        """
        from tether.project import lock

        info, prior = lock.steal_lock(self.path, identity=self._acting_identity())
        self._held_lock = info
        return info, prior

    def release_lock(self) -> bool:
        """Release the lock this handle holds; ``False`` if it holds none / lost it.

        Nonce-checked (:func:`lock.release`): never deletes a lock that was stolen
        away in the meantime. Resolving the identity first drops an inherited
        ``_held_lock`` in a forked child (see :meth:`_acting_identity`), so a child
        never releases the parent's lock.
        """
        from tether.project import lock

        self._acting_identity()  # fork-safety: clears a child's inherited held lock
        if self._held_lock is None:
            return False
        released = lock.release(self.path, self._held_lock)
        self._held_lock = None
        return released

    def write_lock(
        self, *, steal: bool = False, timeout_s: float | None = None
    ) -> AbstractContextManager[LockInfo]:
        """A context manager that holds the single-writer lock for a write session.

        ``with project.write_lock():`` acquires on entry (raising
        :class:`~tether.project.lock.LockedError` if blocked and not stealing) and
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
        """Accept a molecule, logging the ``/labels`` event (:func:`labels.accept`).

        Refuses the canonical write if the file is locked by another writer
        (:meth:`_assert_writable`, §5.4).
        """
        from tether.project import labels

        self._assert_writable()
        return labels.accept(self.path, molecule_key, **provenance)

    def reject(self, molecule_key: str, **provenance: object) -> np.ndarray:
        """Reject a molecule (reversible sticky tag, :func:`labels.reject`).

        Refuses the canonical write if the file is locked by another writer (§5.4).
        """
        from tether.project import labels

        self._assert_writable()
        return labels.reject(self.path, molecule_key, **provenance)

    def unreject(self, molecule_key: str, **provenance: object) -> np.ndarray | None:
        """Un-reject a molecule; ``None`` if it was not rejected (:func:`labels.unreject`).

        Refuses the canonical write if the file is locked by another writer (§5.4).
        """
        from tether.project import labels

        self._assert_writable()
        return labels.unreject(self.path, molecule_key, **provenance)

    def curation_label(self, molecule_key: str) -> int:
        """The molecule's current ``curation_label`` (:func:`labels.curation_label_of`)."""
        from tether.project import labels

        return labels.curation_label_of(self.path, molecule_key)

    def read_labels(self) -> np.ndarray:
        """The ``/labels/table`` provenance log (:func:`labels.read_labels`)."""
        from tether.project import labels

        return labels.read_labels(self.path)

    def rejected_molecule_keys(self) -> set[str]:
        """The molecules currently rejected (:func:`labels.rejected_molecule_keys`)."""
        from tether.project import labels

        return labels.rejected_molecule_keys(self.path)

    # --- conditions (PRD §5.1, §7.6; the annotation/referential-validation seam) --

    def sync_conditions(self) -> ConditionSyncSummary:
        """Materialize the ``/conditions`` rows ``/molecules`` reference (§5.1).

        Delegates to :func:`conditions.sync_conditions`; refuses the write if the
        file is locked by another writer (:meth:`_assert_writable`, §5.4).
        """
        from tether.project import conditions

        self._assert_writable()
        return conditions.sync_conditions(self.path)

    def read_conditions(self) -> np.ndarray:
        """The structured ``/conditions/table`` (:func:`conditions.read_conditions`)."""
        from tether.project import conditions

        return conditions.read_conditions(self.path)

    def read_condition_keys(self) -> dict[str, ConditionKey]:
        """Each materialized ``condition_id`` → its identity :class:`ConditionKey` (§5.1).

        Delegates to :func:`conditions.read_condition_keys`; the map behind querying a
        condition by key fields across its movies (:func:`tether.analysis.query_molecules`).
        """
        from tether.project import conditions

        return conditions.read_condition_keys(self.path)

    def validate_conditions(self) -> ConditionValidationReport:
        """Referential-integrity report of ``/molecules`` → ``/conditions`` (§5.1).

        Delegates to :func:`conditions.validate_conditions`.
        """
        from tether.project import conditions

        return conditions.validate_conditions(self.path)

    def molecules_by_condition(self) -> dict[str, list[str]]:
        """Each ``condition_id`` → its aggregated ``molecule_key`` list (§5.1).

        Delegates to :func:`conditions.aggregate_molecules_by_condition`.
        """
        from tether.project import conditions

        return conditions.aggregate_molecules_by_condition(self.path)

    def preview_rekey(self, from_condition_id: str, to_key: ConditionKey) -> RekeyPreview:
        """Read-only preview of a condition re-key (:func:`conditions.preview_rekey`)."""
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

        Delegates to :func:`conditions.rekey_condition`; refuses the write if the file
        is locked by another writer (:meth:`_assert_writable`, §5.4).
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
        """The append-only re-key/merge audit log (:func:`conditions.read_condition_audit`)."""
        from tether.project import conditions

        return conditions.read_condition_audit(self.path)

    # --- editable per-condition category list (PRD §5.1; FR-ANNOTATE) ---------

    def category_list(self, condition_id: str) -> CategoryList:
        """A condition's editable per-trace category vocabulary (§5.1).

        Delegates to :func:`conditions.read_category_list`; read-only, returns an
        empty list when the condition has no categories yet.
        """
        from tether.project import conditions

        return conditions.read_category_list(self.path, condition_id)

    def set_category_list(self, condition_id: str, categories: object) -> CategoryList:
        """Replace a condition's whole ordered category list (:func:`conditions.set_category_list`).

        Refuses the write if the file is locked by another writer (:meth:`_assert_writable`, §5.4).
        """
        from tether.project import conditions

        self._assert_writable()
        return conditions.set_category_list(self.path, condition_id, categories)

    def add_category(self, condition_id: str, name: str) -> CategoryList:
        """Append one category to a condition's list (:func:`conditions.add_category`).

        Refuses the write if the file is locked by another writer (§5.4).
        """
        from tether.project import conditions

        self._assert_writable()
        return conditions.add_category(self.path, condition_id, name)

    def rename_category(self, condition_id: str, old: str, new: str) -> CategoryList:
        """Rename a category in place, preserving its code (:func:`conditions.rename_category`).

        Refuses the write if the file is locked by another writer (§5.4).
        """
        from tether.project import conditions

        self._assert_writable()
        return conditions.rename_category(self.path, condition_id, old, new)

    def remove_category(self, condition_id: str, name: str) -> CategoryList:
        """Remove a category from a condition's list (:func:`conditions.remove_category`).

        Refuses the write if the file is locked by another writer (§5.4).
        """
        from tether.project import conditions

        self._assert_writable()
        return conditions.remove_category(self.path, condition_id, name)

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
        timeout: float | None = 1800.0,
        overwrite: bool = False,
        _runner: Callable[..., IdealizationResult] | None = None,
    ) -> StoredIdealization:
        """Idealize selected molecules into ``/idealization`` (:func:`idealize.idealize_molecules`).

        The headless core behind the dock's ``I`` key: reads the selected molecules'
        traces, fits vbFRET / consensus VB-HMM via the sidecar, and writes the model
        back as additive data with a per-molecule input-provenance hash. The defaults
        mirror :func:`tether.project.idealize.idealize_molecules` (its
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
            **extra,
        )

    def read_idealization(self, model_name: str) -> StoredIdealization:
        """Read a persisted model (:func:`idealize.read_idealization`)."""
        from tether.project import idealize

        return idealize.read_idealization(self, model_name)

    def list_idealizations(self) -> list[str]:
        """Names of the models under ``/idealization`` (:func:`idealize.list_idealizations`)."""
        from tether.project import idealize

        return idealize.list_idealizations(self)

    def stale_idealization_keys(self, model_name: str) -> list[str]:
        """Molecules whose inputs changed since the fit (:func:`idealize.stale_molecule_keys`)."""
        from tether.project import idealize

        return idealize.stale_molecule_keys(self, model_name)

    def live_idealization_keys(self, model_name: str) -> list[str]:
        """Non-stale molecules TDP/dwell analysis includes (:func:`idealize.live_molecule_keys`)."""
        from tether.project import idealize

        return idealize.live_molecule_keys(self, model_name)

    def reidealize(
        self,
        model_name: str,
        *,
        sidecar_python: str | Path | None = None,
        nrestarts: int | None = None,
        scratch_dir: str | Path | None = None,
        timeout: float | None = 1800.0,
        _runner: Callable[..., IdealizationResult] | None = None,
    ) -> StoredIdealization:
        """Re-fit a stale model over current inputs (:func:`idealize.reidealize`)."""
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
        (:func:`handoff.hand_off_to_tmaven`)."""
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
        (:func:`handoff.read_return_leg`)."""
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
    ) -> AppliedReconcile:
        """Commit accepted return-leg changes, non-destructively
        (:func:`handoff.apply_reconcile`).

        Refuses the canonical write if the file is locked by another writer (§5.4).
        """
        from tether.project import handoff

        self._assert_writable()
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
        )
