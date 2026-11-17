# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The condition model's owner-curator lock and the project's ``/models`` reference.

PRD §5.1, §5.4, §7.5, §7.10; ADR-0119.

The portable ranker of [`tether.ml.persistence`][] is a standalone file that travels with a
condition, not with one ``.tether`` (ADR-0035). This module adds the two parts of the store
integration that ADR-0035 deferred:

1. **The artifact's own single-writer lock.** [`model_writer`][] takes the
   [`tether.project.lock`][] lock on the model file itself (``cond.tethermodel.lock`` beside
   ``cond.tethermodel``), so the condition-owner curator is the only writer while they hold it.
   A second writer is refused with [`LockedError`][tether.project.lock.LockedError], also from
   the same process. A lock older than the staleness timeout is recoverable only by an explicit
   steal (``steal=True``), the same rule as a project lock (PRD §5.4).
2. **The project's reference.** [`record_model_reference`][] writes, under
   ``/models/<condition_id>``, where the condition's model file is and which version of it the
   project last saw. The model itself is not copied into the project (ADR-0035).

A retrain is never lost silently. [`ModelWriter.save`][] writes only while the writer's lock is
still the current one (checked and written under the lock's OS guard,
[`holding`][tether.project.lock.holding]), only while the file on disk is still the version the
writer opened, and only when the model to save is a retrain of that version. A writer whose lock
was stolen gets ``LockedError``; a writer whose file was replaced by a save that did not take the
lock gets [`ModelArtifactConflictError`][]; a model retrained from a copy loaded before someone
else's save gets [`StaleModelError`][]. In each case the file on disk is left as it is, and the
caller retrains again from the version on disk.

The UC3 loop with this module is::

    with model_writer(path) as writer:
        model = writer.load()                       # None on the first video
        model = (
            train_condition_model(project, condition_id=cid, video_id=vid)
            if model is None
            else retrain_condition_model(model, project, video_id=vid)
        )
        writer.save(model, project=project)         # also records /models/<cid>

The reference is additive data inside the frozen ``/models`` container group: ``SCHEMA_VERSION``
does not change, and the payload carries its own [`MODEL_REFERENCE_VERSION`][].
"""

from __future__ import annotations

import hashlib
import threading
from contextlib import AbstractContextManager, contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from tether.project import lock

if TYPE_CHECKING:
    from collections.abc import Iterator
    from os import PathLike

    from tether.ml.persistence import PortableRankerModel
    from tether.project.core import Project
    from tether.project.lock import LockIdentity, LockInfo

    PathRef = str | PathLike[str]
    ProjectRef = Project | str | PathLike[str]

__all__ = [
    "MODEL_REFERENCE_VERSION",
    "ModelArtifactConflictError",
    "ModelReference",
    "ModelWriter",
    "StaleModelError",
    "artifact_sha256",
    "model_writer",
    "read_model_reference",
    "read_model_references",
    "record_model_reference",
]

#: The layout version of one ``/models/<condition_id>`` reference group. It is independent of the
#: store's ``SCHEMA_VERSION``, the same way ``/features/table`` carries its own version.
MODEL_REFERENCE_VERSION = 1

_MODELS = "models"
_READ_CHUNK = 1 << 20


class ModelArtifactConflictError(lock.LockError):
    """The model file changed on disk after the writer opened it; nothing was saved.

    Another program saved the file without taking its lock (for example a direct
    [`tether.ml.persistence.save_model`][] call). Saving now would discard that version, so the
    save is refused. Load the file again, retrain, and save.

    Attributes
    ----------
    path:
        The model file.
    expected_sha256:
        The SHA-256 of the file when the writer opened it (``None``: there was no file).
    found_sha256:
        The SHA-256 of the file now (``None``: the file was removed).
    """

    def __init__(self, path: Path, expected_sha256: str | None, found_sha256: str | None) -> None:
        self.path = Path(path)
        self.expected_sha256 = expected_sha256
        self.found_sha256 = found_sha256
        before = "did not exist" if expected_sha256 is None else f"was {expected_sha256[:12]}"
        now = "is gone" if found_sha256 is None else f"is {found_sha256[:12]}"
        super().__init__(
            f"model file {self.path} changed after this writer opened it (it {before}, it now "
            f"{now}); the save was refused so that the other version is not lost. Load it "
            "again, retrain, and save"
        )


class StaleModelError(lock.LockError):
    """The model to save does not descend from the version on disk; nothing was saved.

    A retrain records the version of the model it came from (``parent_version``,
    [`tether.ml.persistence.warm_start_retrain`][]). A model whose parent is not the model on
    disk, for example one retrained from a copy loaded before another curator's save, would
    discard that save. Load the file in the session, retrain, and save;
    or pass ``replace=True`` to [`ModelWriter.save`][] to replace the model on purpose.

    Attributes
    ----------
    path:
        The model file.
    reason:
        Why the model is not a descendant of the version on disk.
    """

    def __init__(self, path: Path, reason: str) -> None:
        self.path = Path(path)
        self.reason = reason
        super().__init__(
            f"the model to save to {self.path} is not a retrain of the version on disk "
            f"({reason}); the save was refused so that the version on disk is not lost. Load "
            "it in this session, retrain, and save, or save with replace=True to replace it on "
            "purpose"
        )


def _descent_problem(model: PortableRankerModel, current: PortableRankerModel) -> str | None:
    """Return why ``model`` is not a retrain of ``current``, or ``None`` when it is.

    A retrain records the version of the model it came from (``parent_version``,
    [`tether.ml.persistence.model_version`][]), so ancestry is checked on that identity and not
    on counts or times, which an older copy retrained more often would also pass.
    """
    from tether.ml.persistence import model_version  # noqa: PLC0415

    if model.condition_id != current.condition_id:
        return (
            f"it is a model of condition {model.condition_id!r}, the file holds "
            f"{current.condition_id!r}"
        )
    if model.parent_version is None:
        return "it is a fresh fit, not a retrain"
    expected = model_version(current)
    if model.parent_version != expected:
        return (
            f"it was retrained from version {model.parent_version[:12]}, the file holds "
            f"version {expected[:12]}"
        )
    return None


def artifact_sha256(path: PathRef) -> str | None:
    """Return the SHA-256 of the file at ``path``, or ``None`` if there is no file."""
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as fh:
            while chunk := fh.read(_READ_CHUNK):
                digest.update(chunk)
    except FileNotFoundError:
        return None
    return digest.hexdigest()


def _now_iso(now: datetime | None) -> str:
    return (now if now is not None else datetime.now(UTC)).isoformat()


def _project_path(project: ProjectRef) -> Path:
    path = getattr(project, "path", project)
    return Path(path)  # type: ignore[arg-type]


def _require_safe_condition_id(condition_id: str) -> str:
    """Refuse a ``condition_id`` that cannot name an HDF5 group (empty, ``.``, or with ``/``)."""
    if not condition_id or condition_id in {".", ".."}:
        raise ValueError(f"condition_id must be a non-empty condition id, got {condition_id!r}")
    if "/" in condition_id:
        raise ValueError(f"condition_id must not contain '/': {condition_id!r}")
    return condition_id


@dataclass(frozen=True)
class ModelReference:
    """A project's record of its condition's model file (``/models/<condition_id>``).

    Attributes
    ----------
    condition_id:
        The condition the model belongs to.
    artifact_path:
        The model file, as an absolute path when it was recorded.
    artifact_sha256:
        The SHA-256 of the model file when it was recorded: the version this project last saw.
        Compare it with [`artifact_sha256`][] to see whether the file changed since.
    n_retrains, model_updated_utc:
        The model's own provenance at that version ([`tether.ml.PortableRankerModel`][]).
    recorded_utc:
        When the reference was written (ISO-8601 UTC).
    app_version:
        The Tether version that wrote the reference.
    reference_version:
        The layout version of the group ([`MODEL_REFERENCE_VERSION`][]).
    """

    condition_id: str
    artifact_path: str
    artifact_sha256: str
    n_retrains: int
    model_updated_utc: str
    recorded_utc: str
    app_version: str
    reference_version: int

    def is_current(self) -> bool:
        """Whether the model file on disk is still the version this reference recorded."""
        return artifact_sha256(self.artifact_path) == self.artifact_sha256


def _app_version() -> str:
    from tether import __version__  # noqa: PLC0415

    return str(__version__)


def _text(value: object) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _assert_project_writable(project: ProjectRef, identity: LockIdentity | None) -> None:
    """Refuse a write to ``project`` while another writer holds its lock.

    A [`Project`][tether.project.core.Project] handle is checked with its own writer identity,
    so a handle made with an explicit identity that holds the project's lock is its owner here
    too; a path is checked with ``identity`` (default: this process).
    """
    from tether.project.core import Project  # noqa: PLC0415

    if identity is None and isinstance(project, Project):
        project._assert_writable()
    else:
        lock.assert_writable(_project_path(project), identity=identity)


def _project_write(
    project: ProjectRef, identity: LockIdentity | None
) -> AbstractContextManager[None]:
    """Hold off other writers of ``project`` while a short write runs ([`lock.writing`][]).

    The writer identity is resolved as in `_assert_project_writable`.
    """
    from tether.project.core import Project  # noqa: PLC0415

    if identity is None and isinstance(project, Project):
        identity = project._acting_identity()
    return lock.writing(_project_path(project), identity=identity)


def _require_condition(path: Path, condition_id: str) -> None:
    """Refuse a project that holds no molecule of the condition it records a model for."""
    from tether.imaging.extract import read_molecules  # noqa: PLC0415

    molecules = read_molecules(path)
    if not any(_text(c) == condition_id for c in molecules["condition_id"]):
        raise ValueError(f"{path} holds no molecule of condition {condition_id!r}")


def record_model_reference(
    project: ProjectRef,
    model: PortableRankerModel,
    artifact_path: PathRef,
    *,
    artifact_sha256: str | None = None,
    identity: LockIdentity | None = None,
    now: datetime | None = None,
) -> ModelReference:
    """Record in ``project`` where its condition's model file is (``/models/<condition_id>``).

    Writes one group per condition and replaces that condition's earlier reference, so a project
    keeps one reference per condition. This is a canonical write: it is refused with
    [`LockedError`][tether.project.lock.LockedError] while another writer holds the *project's*
    lock. The check and the write run under the project's lock guard
    ([`tether.project.lock.writing`][]), so a writer that takes the lock meanwhile waits for
    the write to end rather than finding the file changed under its lock.

    Parameters
    ----------
    project:
        The project being curated. It must hold a molecule of ``model.condition_id``.
    model:
        The model that was saved to ``artifact_path``.
    artifact_path:
        The model file. It is recorded as an absolute path, and that path can name a user or
        a host (a home folder, a network share); subset export copies it into the shared
        file, so keep a model you share under a neutral folder.
    artifact_sha256:
        The SHA-256 of the saved file ([`ModelWriter.save`][] returns it). When ``None`` the file
        is read and hashed now; a missing file is then an error.
    identity:
        The writer identity for the project-lock check. By default a
        [`Project`][tether.project.core.Project] handle's own identity, and this process for a
        path.
    now:
        Injectable timestamp for reproducible tests.

    Raises
    ------
    FileNotFoundError
        ``artifact_sha256`` is ``None`` and there is no file at ``artifact_path``.
    ValueError
        ``model.condition_id`` cannot name an HDF5 group, or the project holds no molecule of it.
    LockedError
        Another writer holds the project's lock.
    """
    import h5py  # noqa: PLC0415

    path = _project_path(project)
    condition_id = _require_safe_condition_id(model.condition_id)
    target = Path(artifact_path).absolute()
    digest = artifact_sha256 if artifact_sha256 is not None else _sha_or_raise(target)
    _assert_project_writable(project, identity)  # refuse early, before reading the project
    _require_condition(path, condition_id)
    reference = ModelReference(
        condition_id=condition_id,
        artifact_path=str(target),
        artifact_sha256=digest,
        n_retrains=int(model.n_retrains),
        model_updated_utc=model.updated_utc,
        recorded_utc=_now_iso(now),
        app_version=_app_version(),
        reference_version=MODEL_REFERENCE_VERSION,
    )
    # Check and write under the project's lock guard, so a writer that takes the lock
    # after the early check waits for this write instead of finding the file changed.
    with _project_write(project, identity), h5py.File(path, "r+") as f:
        models = f[_MODELS]
        group = (
            models[condition_id]
            if condition_id in models
            else models.create_group(condition_id, track_order=True)
        )
        for key, value in _reference_attrs(reference).items():
            group.attrs[key] = value
    return reference


def _sha_or_raise(path: Path) -> str:
    digest = artifact_sha256(path)
    if digest is None:
        raise FileNotFoundError(f"no model file at {path}; save the model before recording it")
    return digest


def _reference_attrs(reference: ModelReference) -> dict[str, object]:
    return {
        "reference_version": reference.reference_version,
        "condition_id": reference.condition_id,
        "artifact_path": reference.artifact_path,
        "artifact_sha256": reference.artifact_sha256,
        "n_retrains": reference.n_retrains,
        "model_updated_utc": reference.model_updated_utc,
        "recorded_utc": reference.recorded_utc,
        "app_version": reference.app_version,
    }


def _reference_from_group(name: str, group: object) -> ModelReference:
    attrs = group.attrs  # type: ignore[attr-defined]
    try:
        return ModelReference(
            condition_id=_text(attrs["condition_id"]),
            artifact_path=_text(attrs["artifact_path"]),
            artifact_sha256=_text(attrs["artifact_sha256"]),
            n_retrains=int(attrs["n_retrains"]),
            model_updated_utc=_text(attrs["model_updated_utc"]),
            recorded_utc=_text(attrs["recorded_utc"]),
            app_version=_text(attrs["app_version"]),
            reference_version=int(attrs["reference_version"]),
        )
    except KeyError as exc:
        raise ValueError(f"/models/{name} is not a model reference (missing {exc})") from exc


def read_model_references(project: ProjectRef) -> dict[str, ModelReference]:
    """Every model reference in ``project``, by condition id. Read-only.

    Returns an empty mapping for a project that records none. A ``/models`` child that is not a
    group is ignored (``/models`` is reserved for these references).

    Raises
    ------
    ValueError
        A ``/models`` group lacks a reference field.
    """
    import h5py  # noqa: PLC0415

    out: dict[str, ModelReference] = {}
    with h5py.File(_project_path(project), "r") as f:
        if _MODELS not in f:
            return out
        for name, item in f[_MODELS].items():
            if isinstance(item, h5py.Group):
                out[name] = _reference_from_group(name, item)
    return out


def read_model_reference(project: ProjectRef, condition_id: str) -> ModelReference | None:
    """Return one condition's model reference, or ``None`` if the project records none."""
    return read_model_references(project).get(_require_safe_condition_id(condition_id))


class ModelWriter:
    """One owner-curator session on a model file; made by [`model_writer`][].

    Attributes
    ----------
    path:
        The model file.
    held:
        The lock record this session holds on the model file.
    ousted:
        The previous owner when the session was opened with ``steal=True`` (``None`` otherwise,
        or when there was no previous owner or its record was corrupt).
    base_sha256:
        The SHA-256 of the file version this session works from (``None``: no file yet). A
        successful [`save`][] moves it to the version just written.
    """

    def __init__(
        self, path: Path, held: LockInfo, ousted: LockInfo | None, base_sha256: str | None
    ) -> None:
        self.path = path
        self.held = held
        self.ousted = ousted
        self.base_sha256 = base_sha256
        #: The model of version ``base_sha256``, once loaded (``None``: not loaded, or no file).
        self._current: PortableRankerModel | None = None
        #: Serializes `load` and `save` on this session, so that two threads that share it
        #: cannot both pass the ancestry check against one parent, and a load cannot put an old
        #: version in the cache after a save moved the session on. Re-entrant: `save` loads.
        self._session_lock = threading.RLock()

    def load(self) -> PortableRankerModel | None:
        """Load the model file, or return ``None`` when there is none yet (the first video).

        Raises
        ------
        ModelArtifactConflictError
            The file is no longer the version the session opened.
        """
        from tether.ml.persistence import load_model  # noqa: PLC0415

        with self._session_lock:
            found = artifact_sha256(self.path)
            if found != self.base_sha256:
                raise ModelArtifactConflictError(self.path, self.base_sha256, found)
            if found is None:
                return None
            if self._current is None:
                self._current = load_model(self.path)
            return self._current

    def save(
        self,
        model: PortableRankerModel,
        *,
        project: ProjectRef | None = None,
        replace: bool = False,
        now: datetime | None = None,
    ) -> str:
        """Save ``model`` to the file, and record it in ``project`` when one is given.

        The save runs under [`holding`][tether.project.lock.holding]: the session's lock must
        still be the current lock, and the file must still be the version the session opened (or
        the version its last save wrote). When the file holds a model, ``model`` must also be a
        retrain of exactly that model — its ``parent_version`` is that model's
        [`model_version`][tether.ml.persistence.model_version] — unless ``replace`` is true.
        Otherwise nothing is written. After the write the lock's timestamp is refreshed, so a
        long curation session that keeps saving does not go stale.

        Parameters
        ----------
        model:
            The model to save ([`tether.ml.persistence.save_model`][]).
        project:
            When given, [`record_model_reference`][] records the saved version in it. The
            project's lock and condition are checked before the model file is written. A
            writer that takes the project's lock in the moment between that check and the
            reference write gets the reference refused (``LockedError``) after the model was
            saved: the model is not lost, and the project's reference still names the earlier
            version ([`ModelReference.is_current`][] is false) until it is recorded again.
            Hold the project's lock for the session to rule that out.
        replace:
            Replace the model on disk even when ``model`` is not a retrain of it (a deliberate
            fresh fit). The lock and the version checks still apply.
        now:
            Injectable timestamp for the reference, for reproducible tests.

        Returns
        -------
        str
            The SHA-256 of the file written.

        Raises
        ------
        LockedError
            The session's lock was stolen or removed; nothing was saved.
        ModelArtifactConflictError
            The file changed on disk after the session opened it; nothing was saved.
        StaleModelError
            ``model`` is not a retrain of the model on disk and ``replace`` is false; nothing
            was saved.
        """
        with self._session_lock:
            return self._save(model, project=project, replace=replace, now=now)

    def _save(
        self,
        model: PortableRankerModel,
        *,
        project: ProjectRef | None,
        replace: bool,
        now: datetime | None,
    ) -> str:
        from tether.ml.persistence import save_model  # noqa: PLC0415

        self._assert_still_held()  # a stolen lock is reported as such, before anything else
        if project is not None:
            # Check the project before the model file changes, so that a refused reference
            # does not normally leave a saved model the project does not record.
            _assert_project_writable(project, None)
            condition_id = _require_safe_condition_id(model.condition_id)
            _require_condition(_project_path(project), condition_id)
        current = self.load()  # also refuses a file that changed since the session opened it
        if current is not None and not replace:
            problem = _descent_problem(model, current)
            if problem is not None:
                raise StaleModelError(self.path, problem)
        with lock.holding(self.path, self.held):
            found = artifact_sha256(self.path)
            if found != self.base_sha256:
                raise ModelArtifactConflictError(self.path, self.base_sha256, found)
            save_model(model, self.path)
            digest = _sha_or_raise(self.path)
        self.base_sha256 = digest
        self._current = model
        # The save is done. A steal that lands after the guard is released is seen by the
        # next save, so a refused refresh here is not an error of this one.
        with suppress(lock.LockedError):
            self.held = lock.refresh(self.path, self.held)
        if project is not None:
            record_model_reference(project, model, self.path, artifact_sha256=digest, now=now)
        return digest

    def _assert_still_held(self) -> None:
        """Raise ``LockedError`` unless the file's lock still carries this session's nonce."""
        try:
            current = lock.read_lock(self.path)
        except lock.CorruptLockError as exc:
            raise lock.LockedError(None, corrupt=True, path=lock.lock_path(self.path)) from exc
        if current is None or current.nonce != self.held.nonce:
            raise lock.LockedError(current, path=lock.lock_path(self.path))

    def refresh(self) -> LockInfo:
        """Refresh the session's lock timestamp without saving.

        See [`tether.project.lock.refresh`][]; a stolen lock raises ``LockedError``.
        """
        self.held = lock.refresh(self.path, self.held)
        return self.held


@contextmanager
def model_writer(
    artifact_path: PathRef,
    *,
    steal: bool = False,
    identity: LockIdentity | None = None,
    timeout_s: float = lock.DEFAULT_STALENESS_TIMEOUT_S,
    now: datetime | None = None,
) -> Iterator[ModelWriter]:
    """Hold the model file's single-writer lock for one owner-curator session (PRD §7.10).

    Takes ``<artifact>.lock`` with [`tether.project.lock`][] and releases it on exit (the
    release is nonce-checked, so a stolen lock is not removed). Without ``steal``, any lock
    already on the file is refused, **including one with this process's own identity**: two
    sessions in one program would otherwise both pass, and the later save would discard the
    earlier one. A lock older than ``timeout_s`` is reported as stale on the
    [`LockedError`][tether.project.lock.LockedError] and is recovered only with ``steal=True``,
    which replaces it and records the previous owner on [`ModelWriter.ousted`][].

    Parameters
    ----------
    artifact_path:
        The model file. It need not exist yet; its directory is created.
    steal:
        Replace any lock on the file (an explicit steal, PRD §5.4).
    identity:
        The writer identity (default: this process).
    timeout_s:
        The staleness timeout reported on a refusal (PRD §11.2, default 30 min).
    now:
        Injectable lock timestamp, for reproducible tests.

    Raises
    ------
    LockedError
        Another session holds the file's lock and ``steal`` is false.
    """
    path = Path(artifact_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ousted: LockInfo | None = None
    if steal:
        held, ousted = lock.steal_lock(path, identity=identity, now=now)
    else:
        held = lock.acquire(
            path, identity=identity, timeout_s=timeout_s, allow_existing_owner=False, now=now
        )
    writer = ModelWriter(path, held, ousted, None)
    try:
        writer.base_sha256 = artifact_sha256(path)
        yield writer
    finally:
        lock.release(path, writer.held)
