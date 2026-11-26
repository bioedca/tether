# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Find a project's movie again on this computer, without changing the project (#638).

A project records where each movie was when it was extracted (``/movies.uri``). On another
computer that path is often wrong, or the file is a cloud placeholder whose data is not on
the disk. [`read_project_movies`][tether.services.movies.read_project_movies] says so for
each movie; this module is the next step. Like [`tether.services.movies`][] it is a set of
plain Qt-free functions, not registered service commands, and **it never writes the
project**: a movie found again is remembered for this computer by the application
([`tether.gui.preferences`][]), so the project file stays byte-for-byte as it was and the
single-writer rule and the provenance are untouched. Linking the project itself to a new
path is [`relink_source`][tether.project.sources.relink_source], which needs the writer lock.

**The identity rule.** A file is accepted as a project's movie only when its SHA-256
checksum is the one the project holds for it: the source registry's, else the one
``/movies`` recorded at extraction ([`expected_movie_sha256`][]). That is the link-again rule
of the registry, [`same_file`][tether.project.sources.same_file]. A file name, a size or a
modification time can match by chance, a checksum cannot. A movie with no recorded checksum
cannot be found again this way, and says so. [`check_relocated`][] is the step itself: it
takes a path and a checksum rule, reads the file once, and is what the deposit package check
and the source registry call too (#952, #912).

**Cloud placeholders.** Reading a file's checksum reads all its bytes, which downloads a
placeholder. So nothing here reads a file that [`is_placeholder`][] says is not on this disk
unless the caller asked for it: [`locate_in_folder`][] skips placeholders and lists them, and
a front end asks the user, stating the file size, before it calls [`check_relocated`][] or
[`download_movie`][] on one (PRD §5.4).

**Remembered locations.** A located movie is a [`MovieLocation`][]: the path, and the size and
modification time read when its checksum matched. [`apply_locations`][] puts them in the
place of the recorded path of each movie that cannot be shown, and the fast signature check of
[`tether.services.movies`][] then compares the file with the signature read here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from tether.project.jobs import CancelToken
    from tether.services.movies import ProjectMovie, ProjectMovies

__all__ = [
    "MAX_FILES_WALKED",
    "FolderSearch",
    "MovieLocation",
    "apply_locations",
    "check_relocated",
    "download_movie",
    "download_size",
    "FILE_REASONS",
    "expected_movie_sha256",
    "guard_placeholders",
    "is_placeholder",
    "locatable",
    "locate_in_folder",
    "movie_files",
]

_CHUNK = 1 << 20

#: The most files [`locate_in_folder`][] looks at, so a folder picked by mistake (a whole
#: drive) ends the walk instead of hanging it.
MAX_FILES_WALKED = 200_000


@dataclass(frozen=True)
class MovieLocation:
    """Where a movie was found on this computer, and its file's signature at that moment.

    ``path`` is absolute. ``file_size`` and ``mtime`` are the file's size and modification
    time read just after its checksum matched, the fast signature a later open compares with
    (PRD §5.4).
    """

    path: Path
    file_size: int
    mtime: float


@dataclass(frozen=True)
class FolderSearch:
    """What [`locate_in_folder`][] found, by ``movie_id``.

    Attributes
    ----------
    found:
        The movies whose file was found and matched the checksum.
    refused:
        A movie with files of its name that are not it, with the reason in plain words.
    placeholders:
        A movie whose only files of its name are cloud placeholders, which were not read.
    not_found:
        The movies with no file of their name under the folder.
    """

    found: Mapping[str, MovieLocation]
    refused: Mapping[str, str]
    placeholders: Mapping[str, Path]
    not_found: tuple[str, ...]


def _text(value: object) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def expected_movie_sha256(project_path: str | os.PathLike[str], movie_id: str) -> str:
    """Return the checksum a file must have to be the movie ``movie_id``, or ``""``.

    The source registry's checksum of the movie when it holds one, else the one ``/movies``
    recorded at extraction, lower case. ``""`` when neither is known. Reads the project only.
    """
    import h5py

    from tether.project.sources import read_sources

    for record in read_sources(project_path):
        if record.role == "movie" and record.movie_id == movie_id and record.sha256:
            return record.sha256.lower()
    with h5py.File(project_path, "r") as handle:
        movies = handle.get("movies")
        if movies is None or "table" not in movies:
            return ""
        for row in movies["table"][()]:
            if _text(row["movie_id"]) == movie_id:
                return _text(row["sha256"]).strip().lower()
    return ""


def is_placeholder(path: str | os.PathLike[str]) -> bool:
    """Whether reading the movie at ``path`` would download data that is not on this disk.

    True when the file is a cloud placeholder, or, for a cellSens ``.vsi`` movie, when any of
    the ``.ets`` files that hold its frames is one: its checksum reads them too
    ([`frame_files`][tether.io.movie_identity.frame_files]). Reads the files' attributes
    only, never their bytes, with the definition the movie panel uses (PRD §5.4). ``False``
    for a file that does not exist or cannot be examined.
    """
    from tether.project.condition_model import _OFFLINE_ATTRIBUTES, _file_attributes

    def offline(file: Path) -> bool:
        try:
            return bool(_file_attributes(file) & _OFFLINE_ATTRIBUTES)
        except OSError:
            return False

    return any(offline(file) for file in movie_files(path))


def check_relocated(
    candidate: str | os.PathLike[str],
    expected_sha256: str,
    *,
    rule: Callable[[str, str], bool] | None = None,
    cancel: CancelToken | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> MovieLocation:
    """Return where the file ``candidate`` is when it is, by ``rule``, the expected file.

    The file is read once ([`file_sha256`][tether.project.sources.file_sha256], cancellable
    between blocks). ``rule(expected, actual)`` decides; the default is the registry's
    [`same_file`][tether.project.sources.same_file], equal checksums. Nothing is written.

    Raises
    ------
    RelinkRefused
        If ``expected_sha256`` is empty (no checksum to compare with) or the rule says the
        file is another one.
    SourceReplaced
        If the file at ``candidate`` was replaced or changed during the read.
    OSError
        If the file cannot be read.
    """
    from tether.project.sources import RelinkRefused, SourceReplaced, file_sha256, same_file

    path = Path(candidate).absolute()
    if not expected_sha256:
        raise RelinkRefused(
            f"{path.name}: the project records no checksum for this movie, so a file cannot be "
            "shown to be the same file"
        )
    digest = file_sha256(path, cancel=cancel, progress=progress)
    if not (rule or same_file)(expected_sha256, digest.sha256):
        raise RelinkRefused(f"{path.name} is not the project's movie: its checksum is different")
    # The signature remembered is the one of the file that was read: taken before the path
    # check, and compared with the read's own, so a file replaced after the check is never
    # remembered under this checksum.
    st = path.stat()
    if st.st_size != digest.size or st.st_mtime_ns != digest.mtime_ns:
        raise SourceReplaced(f"{path.name} changed while its checksum was read")
    digest.check_path(path)
    return MovieLocation(path=path, file_size=int(st.st_size), mtime=float(st.st_mtime))


def _names(folder: Path, wanted: set[str]) -> dict[str, list[Path]]:
    """Return the files under ``folder`` whose case-folded name is in ``wanted``."""
    hits: dict[str, list[Path]] = {}
    walked = 0
    for root, _dirs, files in os.walk(folder, followlinks=False):
        for name in files:
            walked += 1
            if walked > MAX_FILES_WALKED:
                return hits
            key = name.casefold()
            if key in wanted:
                hits.setdefault(key, []).append(Path(root) / name)
    for paths in hits.values():
        paths.sort()
    return hits


def locate_in_folder(
    project_path: str | os.PathLike[str],
    movies: Iterable[ProjectMovie],
    folder: str | os.PathLike[str],
    *,
    cancel: CancelToken | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> FolderSearch:
    """Find each of ``movies`` under ``folder`` by its file name, and check its checksum.

    A movie is looked for by the file name of its recorded path, case-insensitively, in the
    folder and every folder below it (links to folders are not followed, and the walk stops
    after [`MAX_FILES_WALKED`][] files). Each file of that name is read with
    [`check_relocated`][] until one matches, except a cloud placeholder, which is never read
    here (it is listed in ``placeholders``). ``progress(read, total)`` counts the bytes of
    every file read. Nothing is written.
    """
    from tether.project.sources import RelinkRefused, SourceReplaced

    # Two movies of one project can share a file name (from two folders): each is looked
    # for, and each candidate of that name is checked against each movie's own checksum.
    wanted = [(movie.name.casefold(), movie) for movie in movies]
    hits = _names(Path(folder), {key for key, _movie in wanted})
    readable = {
        key: [path for path in paths if not is_placeholder(path)] for key, paths in hits.items()
    }
    sizes: dict[Path, int] = {}
    for paths in readable.values():
        for path in paths:
            try:
                sizes[path] = path.stat().st_size
            except OSError:
                sizes[path] = 0
    # At most every candidate of each movie's name is read once for that movie.
    total = sum(sizes[path] for key, _movie in wanted for path in readable.get(key, ()))
    done = 0

    found: dict[str, MovieLocation] = {}
    refused: dict[str, str] = {}
    placeholders: dict[str, Path] = {}
    not_found: list[str] = []
    for key, movie in wanted:
        if key not in hits:
            not_found.append(movie.movie_id)
            continue
        if not readable[key]:
            placeholders[movie.movie_id] = hits[key][0]
            continue
        expected = expected_movie_sha256(project_path, movie.movie_id)
        reasons: list[str] = []
        for path in readable[key]:
            base = done

            def _report(read: int, _size: int, base: int = base) -> None:
                if progress is not None:
                    progress(base + read, total)

            try:
                found[movie.movie_id] = check_relocated(
                    path, expected, cancel=cancel, progress=_report
                )
            except (RelinkRefused, SourceReplaced, OSError) as exc:
                reasons.append(str(exc))
                continue
            finally:
                done = base + sizes[path]
            break
        else:
            refused[movie.movie_id] = reasons[0] if reasons else "not the project's movie"
    return FolderSearch(
        found=found, refused=refused, placeholders=placeholders, not_found=tuple(not_found)
    )


def movie_files(path: str | os.PathLike[str]) -> tuple[Path, ...]:
    """Return every file a movie's checksum reads: ``path``, then a ``.vsi`` movie's frame files.

    The frame files are the ``.ets`` files of [`frame_files`][tether.io.movie_identity.frame_files];
    a frame folder that cannot be listed gives none. Reads directory entries only.
    """
    from tether.io.movie_identity import frame_files

    try:
        frames = frame_files(path)
    except OSError:
        frames = ()
    return (Path(path), *frames)


def download_size(path: str | os.PathLike[str]) -> int:
    """Return the bytes [`download_movie`][] would read: every file of [`movie_files`][].

    A placeholder reports its full size without its data being on the disk, so this reads
    the files' attributes only. A file that cannot be examined counts as zero bytes.
    """
    total = 0
    for file in movie_files(path):
        try:
            total += file.stat().st_size
        except OSError:
            continue
    return total


def download_movie(
    path: str | os.PathLike[str],
    *,
    cancel: CancelToken | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> int:
    """Read every byte of the movie at ``path``, which makes a cloud placeholder local.

    The explicit "Download and open" of a movie that is not on this disk (PRD §5.4): a sync
    client such as OneDrive fetches a placeholder's data when it is read. Every file of
    [`movie_files`][] is read, so a ``.vsi`` movie's frame files come down with its header.
    The bytes are not kept. Returns the number of bytes read; ``cancel`` is asked between
    1 MiB blocks and ``progress(read, total)`` counts every file.
    """
    files = movie_files(path)
    total = download_size(path)
    done = 0
    for file in files:
        with open(file, "rb") as handle:
            for block in iter(lambda handle=handle: handle.read(_CHUNK), b""):
                if cancel is not None:
                    cancel.raise_if_cancelled()
                done += len(block)
                if progress is not None:
                    progress(done, total)
    return done


def guard_placeholders(movies: ProjectMovies) -> ProjectMovies:
    """Return ``movies`` with each movie whose files are not all on this disk marked so.

    The movie panel's own check reads a movie file's attributes only, so a cellSens ``.vsi``
    movie whose header is local but whose ``.ets`` frame files are cloud placeholders reads
    as available, and opening it would download frames. This marks such a movie
    ``"file not on this disk"`` ([`is_placeholder`][], metadata only) so that only the
    explicit **Download and open** reads it (PRD §5.4).
    """
    out = []
    for movie in movies.movies:
        if movie.available and movie.path is not None and is_placeholder(movie.path):
            movie = replace(movie, unavailable="file not on this disk")
        out.append(movie)
    return replace(movies, movies=tuple(out))


#: The reasons [`tether.services.movies`][] gives for a movie *file* that cannot be read
#: here. Only a movie held back by one of these is located again; another reason (a movie
#: pair, whose view is not available yet, #1405) is not about where the file is.
FILE_REASONS = frozenset(
    {
        "file missing",
        "file not on this disk",
        "file unreadable",
        "file changed since extraction",
        "no movie file recorded",
    }
)


def locatable(movie: ProjectMovie) -> bool:
    """Whether ``movie`` is held back only by where its file is ([`FILE_REASONS`][])."""
    return movie.unavailable in FILE_REASONS


def apply_locations(movies: ProjectMovies, locations: Mapping[str, MovieLocation]) -> ProjectMovies:
    """Return ``movies`` with each movie that cannot be shown moved to its remembered location.

    ``locations`` maps a ``movie_id`` to where it was found on this computer. A movie that can
    be shown where the project records it keeps that path. Each moved movie is checked again
    at its new path, against the signature in its [`MovieLocation`][] and metadata only: a file
    that has gone or changed since is listed with the reason, as any other movie is.
    """
    from tether.services.movies import _unavailable  # noqa: PLC2701 - one definition of "why"

    out = []
    for movie in movies.movies:
        where = locations.get(movie.movie_id)
        if not locatable(movie) or where is None:
            out.append(movie)
            continue
        out.append(
            replace(
                movie,
                path=where.path,
                file_size=where.file_size,
                mtime=where.mtime,
                unavailable=_unavailable(where.path, where.file_size, where.mtime),
            )
        )
    return replace(movies, movies=tuple(out))
