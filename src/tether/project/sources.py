# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The source registry: the original files of a project and the files derived from them.

A project records **one source record for each source file** it uses (#912, ADR-0140): a
movie, the bead video that made a channel map, a map image. The records are the rows of
``/sources/table`` (schema version 3). A record holds the file's path relative to the
project, its original name, size, SHA-256 checksum, the adapter that reads it and that
adapter's version, its role and its acquisition. A **derived file** (a file made from
another one) names its original in ``derived_from`` and the recipe between them in
``recipe``. The registry never copies a raw movie into the project.

* **An existing project.** A store with no record for one of its movies (any store stamped
  1 or 2, and a version-3 store whose movie was extracted without one) reads as one record
  for that movie, made from the ``/movies`` row: the stored ``uri``, size and file name,
  with the checksum **not calculated** (``sha256 == ""``). Its id is ``movie:<movie_id>``.
  Reading it writes nothing; the first write that needs it stores it.
* **The checksum.** [`file_sha256`][] reads a file in 1 MiB blocks, asks a
  [`CancelToken`][tether.project.jobs.CancelToken] between blocks and reports the bytes
  read. A large file is read by the checksum job of [`tether.services.sources`][], which
  runs on a worker thread, never in the window thread.
* **Link again.** A source that moved is linked to its new path with [`relink_source`][].
  The checksum decides: the new file must have the checksum the registry holds (or, for a
  movie whose record has none, the one ``/movies`` recorded at extraction). The same rule,
  [`same_file`][], is the one the missing-movie prompt (#638) uses.
* **Private paths.** A file inside the project's folder is stored relative to it, with
  ``/`` separators. A file outside it is stored with ``..`` steps, or as its absolute path
  when it is on another drive. A copy for sharing (#620) keeps only the file name of such a
  path ([`shareable_path`][]).

Every write needs the single-writer lock (PRD §7.10). The first write into a store stamped
before 3 adds the table and stamps the file 3, after a backup of the file as it was (PRD
§5.4, ADR-0136).
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from tether.project.core import Project
    from tether.project.jobs import CancelToken

__all__ = [
    "SOURCE_ROLES",
    "ChecksumMismatch",
    "RelinkRefused",
    "SourceRecord",
    "ensure_registry",
    "file_sha256",
    "project_movie_uri",
    "read_sources",
    "record_movie_pair",
    "register_source",
    "relink_source",
    "same_file",
    "set_checksum",
    "shareable_path",
]

_GROUP = "sources"
_TABLE = "table"
_MOVIE_PREFIX = "movie:"
_CHUNK = 1 << 20

#: The roles a source file can have.
SOURCE_ROLES = ("movie", "bead_video", "map_image")


class ChecksumMismatch(ValueError):
    """A file's checksum differs from the one the project already holds for it."""


class RelinkRefused(ValueError):
    """A file given to [`relink_source`][] is not, by its checksum, the registered file."""


@dataclass(frozen=True)
class SourceRecord:
    """One source file of a project, as [`read_sources`][] returns it.

    ``stored`` is ``False`` for a record made from a ``/movies`` row that the registry
    does not hold yet. ``sha256`` is ``""`` while the checksum is not calculated.
    """

    source_id: str
    relative_path: str
    original_name: str
    file_size: int
    sha256: str
    adapter: str
    adapter_version: str
    role: str
    acquisition: Mapping[str, object]
    movie_id: str
    derived_from: str
    recipe: str
    created_utc: str
    stored: bool = True

    @property
    def checksum_calculated(self) -> bool:
        """Whether the registry holds the file's checksum."""
        return bool(self.sha256)

    def resolve(self, project_path: str | Path) -> Path:
        """Return the file's path: ``relative_path`` read against the project's folder."""
        path = Path(self.relative_path)
        return path if path.is_absolute() else Path(project_path).parent / path


def _text(value: object) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _name(path: str) -> str:
    return path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


def _is_absolute(path: str) -> bool:
    return PurePosixPath(path).is_absolute() or PureWindowsPath(path).is_absolute()


def _stored_path(project_path: Path, file_path: str | Path) -> str:
    """Return ``file_path`` relative to the project's folder, or absolute on another drive."""
    file_path = Path(file_path).absolute()
    try:
        rel = os.path.relpath(file_path, Path(project_path).absolute().parent)
    except ValueError:  # another drive (Windows): no relative path exists
        return file_path.as_posix()
    return rel.replace(os.sep, "/")


def project_movie_uri(
    project_path: str | os.PathLike[str], movie_path: str | os.PathLike[str]
) -> str:
    """Return the ``/movies`` ``uri`` of ``movie_path`` in the project at ``project_path``.

    PRD §5.1 records the source movie relative to the project: the ``uri`` is the path from
    the folder of the project file to the movie, with POSIX separators (``../raw/m.tif``),
    so a reader finds the movie from any working folder. A relative ``movie_path`` is read
    from the current folder. Where no relative path exists (the movie is on another Windows
    drive than the project) the absolute path is recorded instead (#1282). So it is when the
    relative path leads somewhere else, as it does past a linked folder: ``..`` is taken
    from where the link points, not from where it sits. That check reads only the files'
    attributes; a movie that is not there to compare with keeps the relative path.

    Examples
    --------
    >>> project_movie_uri("/data/out/m.tether", "/data/raw/m.tif")
    '../raw/m.tif'
    """
    uri = _stored_path(Path(project_path), movie_path)
    if _is_absolute(uri):
        return uri
    try:
        movie = os.stat(movie_path)
    except OSError:  # no movie file to compare with
        return uri
    try:
        same = os.path.samestat(os.stat(Path(project_path).absolute().parent / uri), movie)
    except OSError:  # the relative path leads to no file
        same = False
    return uri if same else Path(movie_path).absolute().as_posix()


def shareable_path(relative_path: str) -> str:
    """Return the path a copy for sharing keeps: the file name of a path outside the folder.

    A path that is absolute or steps out of the project's folder (``..``) can name a private
    folder, such as a user's home; only its file name is kept (#620).
    """
    parts = relative_path.replace("\\", "/").split("/")
    if _is_absolute(relative_path) or ".." in parts:
        return _name(relative_path)
    return relative_path


def _record(row: object, *, stored: bool = True) -> SourceRecord:
    acquisition = _text(row["acquisition"])
    return SourceRecord(
        source_id=_text(row["source_id"]),
        relative_path=_text(row["relative_path"]),
        original_name=_text(row["original_name"]),
        file_size=int(row["file_size"]),
        sha256=_text(row["sha256"]),
        adapter=_text(row["adapter"]),
        adapter_version=_text(row["adapter_version"]),
        role=_text(row["role"]),
        acquisition=json.loads(acquisition) if acquisition else {},
        movie_id=_text(row["movie_id"]),
        derived_from=_text(row["derived_from"]),
        recipe=_text(row["recipe"]),
        created_utc=_text(row["created_utc"]),
        stored=stored,
    )


def _movie_path(uri: str, row: object, project_path: Path | None) -> str:
    """Return the registry path of a ``/movies`` row's ``uri``, read as the movie panel does.

    A relative ``uri`` names a file in the project's folder, or one in the current folder
    whose size and modification time match the row (the rule of
    [`tether.services.movies`][]); the path is then stored relative to the project's folder.
    """
    if _is_absolute(uri) or project_path is None or not uri:
        return uri if _is_absolute(uri) else uri.replace("\\", "/")
    from tether.services.movies import _resolve  # noqa: PLC0415, PLC2701

    project_path = Path(project_path)
    resolved = _resolve(
        uri, project_path.absolute().parent, int(row["file_size"]), float(row["mtime"])
    )
    return _stored_path(project_path, resolved) if resolved is not None else uri


def _records_in(f: object, project_path: Path | None = None) -> list[SourceRecord]:
    """Return the stored records of an open store, then one for each unregistered movie.

    ``project_path`` is the store's own path: a relative ``/movies.uri`` is then read as the
    movie panel reads it ([`_movie_path`][]).
    """
    group = f.get(_GROUP)
    stored = [_record(r) for r in group[_TABLE][()]] if group is not None else []
    have = {r.movie_id for r in stored if r.role == "movie" and r.movie_id}
    made: list[SourceRecord] = []
    movies = f.get("movies")
    for row in movies[_TABLE][()] if movies is not None else ():
        movie_id = _text(row["movie_id"])
        if movie_id in have:
            continue
        uri = _text(row["uri"])
        made.append(
            SourceRecord(
                source_id=_MOVIE_PREFIX + movie_id,
                relative_path=_movie_path(uri, row, project_path),
                original_name=_name(uri),
                file_size=int(row["file_size"]),
                sha256="",
                adapter="",
                adapter_version="",
                role="movie",
                acquisition={},
                movie_id=movie_id,
                derived_from="",
                recipe="",
                created_utc="",
                stored=False,
            )
        )
    return stored + made


def read_sources(project_path: str | Path) -> list[SourceRecord]:
    """Return every source record of the project at ``project_path`` (a read).

    The stored records come first, in table order, then one record for each ``/movies``
    row that has none (``stored=False``, checksum not calculated).
    """
    import h5py  # noqa: PLC0415

    with h5py.File(project_path, "r") as f:
        return _records_in(f, Path(project_path))


def _find(project_path: str | Path, source_id: str) -> SourceRecord:
    for record in read_sources(project_path):
        if record.source_id == source_id:
            return record
    raise KeyError(f"no source {source_id!r} in {Path(project_path).name}")


def _row(record: SourceRecord) -> object:
    import numpy as np  # noqa: PLC0415

    from tether.io.schema import SOURCES_DTYPE  # noqa: PLC0415

    row = np.zeros(1, dtype=SOURCES_DTYPE)
    for name in SOURCES_DTYPE.names:
        value = getattr(record, name)
        row[name] = json.dumps(dict(value), sort_keys=True) if name == "acquisition" else value
    return row


def _write(
    project: Project,
    change: Callable[[object], None],
    guard: Callable[[], object] | None = None,
) -> None:
    """Run ``change(table)`` on ``/sources/table`` under the writer lock.

    ``guard`` (default: the handle's writer check) runs before the upgrade and again once the
    file is open and before the change, so a long write binds to the lock epoch it started
    under. A store
    stamped before 3 first gains the table through a guarded write that backs the file up
    as it was (PRD §5.4).
    """
    import h5py  # noqa: PLC0415

    from tether.io.schema import (  # noqa: PLC0415
        SOURCES_SINCE,
        read_schema_version,
        upgrade_skeleton,
    )
    from tether.project._guarded_publish import guarded_project_write  # noqa: PLC0415

    check = guard or project._assert_writable
    check()
    if read_schema_version(project.path) < SOURCES_SINCE:
        with (
            guarded_project_write(project.path, write_guard=check, label="sources") as staged,
            h5py.File(staged, "r+") as f,
        ):
            upgrade_skeleton(f)
    with h5py.File(project.path, "r+") as f:
        check()
        change(f[_GROUP][_TABLE])


def ensure_registry(project: Project) -> None:
    """Give the project its ``/sources`` table now, so a later write is in place (needs the lock).

    A store stamped 3 is left as it is; an older one is backed up and stamped 3 (PRD §5.4).
    """
    _write(project, lambda _table: None)


def _append(table: object, record: SourceRecord) -> None:
    n0 = table.shape[0]
    table.resize((n0 + 1,))
    try:
        table[n0:] = _row(record)
    except BaseException:
        table.resize((n0,))
        raise


def _store(
    project: Project,
    record: SourceRecord,
    guard: Callable[[], object] | None = None,
    **fields: object,
) -> SourceRecord:
    """Write ``record`` with ``fields`` changed: in place, or appended when not yet stored."""
    new = replace(record, stored=True, **fields)
    if not new.created_utc:
        new = replace(new, created_utc=_now())

    def change(table: object) -> None:
        ids = [_text(v) for v in table["source_id"]]
        if new.source_id in ids:
            table[ids.index(new.source_id)] = _row(new)[0]
        else:
            _append(table, new)

    _write(project, change, guard)
    return new


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def register_source(
    project: Project,
    file_path: str | Path,
    *,
    role: str,
    adapter: str = "",
    adapter_version: str = "",
    acquisition: Mapping[str, object] | None = None,
    movie_id: str = "",
    derived_from: str = "",
    recipe: str = "",
) -> SourceRecord:
    """Store a new source record for ``file_path`` and return it (needs the writer lock).

    The file must exist; its size is recorded and its checksum is not calculated (run the
    checksum job). A derived file names its original's ``source_id`` in ``derived_from``
    and the recipe that made it in ``recipe``; an original gives neither.

    Raises
    ------
    ValueError
        If ``role`` is not one of [`SOURCE_ROLES`][], the file is already registered,
        only one of ``derived_from`` and ``recipe`` is given, or a movie has no
        ``movie_id``.
    KeyError
        If ``derived_from`` names no source of the project.
    """
    if role not in SOURCE_ROLES:
        raise ValueError(f"role must be one of {SOURCE_ROLES}, not {role!r}")
    if bool(derived_from) != bool(recipe):
        raise ValueError("a derived file needs both its original (derived_from) and its recipe")
    if role == "movie" and not movie_id:
        raise ValueError("a movie source needs the movie_id of its /movies row")
    path = Path(file_path)
    size = path.stat().st_size
    stored_path = _stored_path(project.path, path)
    records = read_sources(project.path)
    if any(r.stored and r.relative_path == stored_path for r in records):
        raise ValueError(f"{path.name} is already registered in {project.path.name}")
    if derived_from:
        original = next((r for r in records if r.source_id == derived_from), None)
        if original is None:
            raise KeyError(f"no source {derived_from!r} in {project.path.name}")
        if not original.stored:
            _store(project, original)
    record = SourceRecord(
        source_id=f"src-{uuid4().hex}",
        relative_path=stored_path,
        original_name=path.name,
        file_size=int(size),
        sha256="",
        adapter=adapter,
        adapter_version=adapter_version,
        role=role,
        acquisition=dict(acquisition or {}),
        movie_id=movie_id,
        derived_from=derived_from,
        recipe=recipe,
        created_utc="",
    )
    if role == "movie":
        # A movie's record replaces the one made from its /movies row (same movie_id).
        made = next((r for r in records if not r.stored and r.movie_id == movie_id), None)
        if made is not None:
            record = replace(record, source_id=made.source_id)
    return _store(project, record)


def record_movie_pair(
    project_path: str | Path,
    movie_id: str,
    files: Mapping[str, tuple[str | Path, int, str]],
) -> list[SourceRecord]:
    """Store the two files of a movie pair as the sources of ``movie_id`` (#909).

    ``files`` maps each role of [`PAIR_ROLES`][tether.io.pair.PAIR_ROLES] to the file's
    ``(path, size, sha256)`` as the extraction read it. Each file gets a stored record
    with role ``movie``, ``movie_id``, its checksum and the acquisition
    ``{"pair_role": role}``, so ``read_sources`` gives both files of the pair and makes
    no record from the ``/movies`` row. This is a write into the project an extraction
    is building (the staged copy of an append): the caller holds the writer lock and
    publishes the file, so no lock is checked here. A store stamped before 3 gains the
    table first ([`upgrade_skeleton`][tether.io.schema.upgrade_skeleton]); the publish
    keeps the file as it was beside it.

    Raises
    ------
    ValueError
        If ``files`` does not give exactly the two roles.
    """
    import h5py  # noqa: PLC0415

    from tether.io.pair import PAIR_ROLE_KEY, PAIR_ROLES  # noqa: PLC0415
    from tether.io.schema import upgrade_skeleton  # noqa: PLC0415

    if set(files) != set(PAIR_ROLES):
        raise ValueError(f"a movie pair needs the roles {PAIR_ROLES}, got {sorted(files)}")
    project_path = Path(project_path)
    now = _now()
    records = [
        SourceRecord(
            source_id=f"src-{uuid4().hex}",
            relative_path=_stored_path(project_path, files[role][0]),
            original_name=Path(files[role][0]).name,
            file_size=int(files[role][1]),
            sha256=str(files[role][2]).lower(),
            adapter="",
            adapter_version="",
            role="movie",
            acquisition={PAIR_ROLE_KEY: role},
            movie_id=movie_id,
            derived_from="",
            recipe="",
            created_utc=now,
        )
        for role in PAIR_ROLES
    ]
    with h5py.File(project_path, "r+") as f:
        upgrade_skeleton(f)
        for record in records:
            _append(f[_GROUP][_TABLE], record)
    return records


def file_sha256(
    path: str | Path,
    *,
    cancel: CancelToken | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> str:
    """Return the SHA-256 of the file at ``path``, read in 1 MiB blocks.

    ``cancel`` is asked between blocks (it raises
    [`JobCancelled`][tether.project.jobs.JobCancelled]); ``progress(read, size)`` is
    called after each block.
    """
    digest = hashlib.sha256()
    size = os.stat(path).st_size
    done = 0
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(_CHUNK), b""):
            if cancel is not None:
                cancel.raise_if_cancelled()
            digest.update(block)
            done += len(block)
            if progress is not None:
                progress(done, size)
    if cancel is not None:  # a cancel during the last block still stops the calculation
        cancel.raise_if_cancelled()
    return digest.hexdigest()


def set_checksum(
    project: Project,
    source_id: str,
    sha256: str,
    *,
    guard: Callable[[], object] | None = None,
) -> SourceRecord:
    """Store the checksum of a source (the checksum job's write; needs the writer lock).

    A checksum already known for the source (the registry's, or for a movie the one
    ``/movies`` recorded at extraction) is its identity: a different one is refused, so a
    file that changed since then never replaces it. ``guard`` runs just before the write.

    Raises
    ------
    ChecksumMismatch
        If ``sha256`` differs from the checksum already known. Nothing is written.
    """
    record = _find(project.path, source_id)
    expected = _expected_sha256(project.path, record)
    if expected and not same_file(expected, sha256):
        raise ChecksumMismatch(
            f"{record.original_name} has changed: its checksum is not the one recorded for it"
        )
    return _store(project, record, guard, sha256=sha256.lower())


def _expected_sha256(project_path: Path, record: SourceRecord) -> str:
    """Return the checksum ``record``'s file must have: the registry's, else a movie's ``/movies``'.

    Another role that names a movie (a map image) has no checksum but its own.
    """
    if record.sha256 or record.role != "movie" or not record.movie_id:
        return record.sha256
    import h5py  # noqa: PLC0415

    with h5py.File(project_path, "r") as f:
        for row in f["movies"][_TABLE][()]:
            if _text(row["movie_id"]) == record.movie_id:
                return _text(row["sha256"]).lower()
    return ""


def same_file(expected_sha256: str, candidate_sha256: str) -> bool:
    """Return whether a file is the same file: the link-again rule (#912, #638).

    The two checksums must be equal.

    A checksum that is not known (``""``) decides nothing, so it is never the same file.
    """
    return bool(expected_sha256) and expected_sha256.lower() == candidate_sha256.lower()


def _lock_epoch_guard(project: Project) -> Callable[[], None]:
    """Bind a long write to the lock epoch it starts under; return the check to run before it.

    A retained lock binds to its own nonce; otherwise the present lock file's nonce. An absent
    lock refuses with ``LockedError``. The check refuses when the epoch has changed since, so
    a steal during a long read stops the write even when the thief has released the lock.
    """
    from tether.project import lock  # noqa: PLC0415

    def nonce() -> str:
        project._assert_writable()
        if project._held_lock is not None:  # a retained lock binds to its own nonce
            return project._assert_held_lock()
        current = lock.read_lock(project.path)
        if current is None:
            raise lock.LockedError(None, path=project.lock_path)
        return current.nonce

    epoch = nonce()

    def check() -> None:
        if nonce() != epoch:
            raise lock.LockedError(lock.read_lock(project.path), path=project.lock_path)

    return check


def relink_source(
    project: Project,
    source_id: str,
    new_path: str | Path,
    *,
    cancel: CancelToken | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> SourceRecord:
    """Link a source that moved to ``new_path`` when the checksum says it is the same file.

    The new file is read once ([`file_sha256`][]; a front end runs this off the window
    thread). On a match the record's path and size are updated, and a checksum not yet in
    the registry is stored. Needs the writer lock; the write checks the lock epoch the call
    started under, so a steal during the read stops it.

    Raises
    ------
    RelinkRefused
        If the checksum differs, or no checksum is known to compare with. Nothing is
        written.
    """
    guard = _lock_epoch_guard(project)
    record = _find(project.path, source_id)
    expected = _expected_sha256(project.path, record)
    if not expected:
        raise RelinkRefused(
            f"{record.original_name}: no checksum is known for this source, so a moved file "
            "cannot be shown to be the same file"
        )
    actual = file_sha256(new_path, cancel=cancel, progress=progress)
    if not same_file(expected, actual):
        raise RelinkRefused(
            f"{Path(new_path).name} is not {record.original_name}: its checksum is different"
        )
    return _store(
        project,
        record,
        relative_path=_stored_path(project.path, new_path),
        file_size=int(os.stat(new_path).st_size),
        sha256=actual,
        guard=guard,
    )
