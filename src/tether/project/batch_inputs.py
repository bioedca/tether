# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The movie arguments of ``tether batch``: files, folders and patterns (#653).

Three steps turn what a user typed into the list of jobs the batch runner works through.
None of them opens a movie, so they are cheap enough to run before anything is written and
to show in a dry run:

* [`expand_movie_arguments`][] makes each argument a list of movie files. A file is used as
  it is. A folder means the movie files directly inside it, or every movie file below it
  when ``recursive`` is set. Anything else is a pattern that Tether expands itself, with
  the standard-library `glob` module, so ``"data/*.tif"`` gives the same list in
  PowerShell, cmd and bash. A pattern match that is a folder is expanded like a folder
  argument. The whole list is sorted and each movie appears once. An argument that gives
  no movie is refused with a message that names it.
* [`output_stems`][] gives each movie the stem of its output project by one rule: the
  movie's file stem, and when two movies would share a stem, as many of their parent
  folder names as make each one unique, joined with [`NAME_SEPARATOR`][]. Stems are
  compared without regard to case, so the rule gives the same names on every operating
  system. With no collision every stem is the movie's own file stem, as before.
* [`describe_checkpoint`][] says, from the output alone, what a run will do with a movie
  whose output already exists. It reads the same provenance groups the runner's
  checkpoint reads, opened read-only.

``tether batch --into`` (#1151) puts every movie into one project, so a movie's checkpoint
there is its content: [`movie_in_project`][] finds the movie in the project's ``/movies``
table, and [`describe_append`][] says what a run will do with it. These two read the
movie's bytes to hash it, but only when the project holds a movie of the same size.
[`describe_project_stages`][] says what the run will do with the project's correct and
idealize stages, which run once for the whole project.
"""

from __future__ import annotations

import fnmatch
import glob
import os
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

__all__ = [
    "NAME_SEPARATOR",
    "MovieArgumentError",
    "OutputNameCollision",
    "describe_append",
    "describe_checkpoint",
    "describe_project_stages",
    "expand_movie_arguments",
    "movie_in_project",
    "movie_suffixes",
    "output_stems",
]

#: What joins the parent folder names and the file stem in a collision-resolved output
#: stem: ``day1/movie_010.tif`` becomes ``day1__movie_010``.
NAME_SEPARATOR = "__"

_GLOB_CHARACTERS = frozenset("*?[")


class MovieArgumentError(ValueError):
    """A movie argument of ``tether batch`` gave no movie; the message names it."""


class OutputNameCollision(ValueError):
    """Two movies would write one output project and no parent folder tells them apart."""


def movie_suffixes() -> tuple[str, ...]:
    """Return the file suffixes a folder or a pattern contributes as movies.

    They are the extensions of the registered movie readers
    ([`tether.io.adapters.registered_adapters`][]) whose movies extraction reads
    ([`tether.io.movie.EXTRACTED_CONTAINERS`][]), lower case and in registration order.
    A reader that opens a movie extraction refuses contributes nothing. A folder that
    holds an original beside its TIFF export (``x.nd2`` and ``x.tif``) gives both, and
    [`output_stems`][] then refuses the pair by name. Suffixes are compared without
    regard to case. A file named as an argument of its own is used whatever its suffix,
    and the registry then decides by its content.
    """
    from tether.io import movie  # noqa: PLC0415 - imports the readers
    from tether.io.adapters import registered_adapters  # noqa: PLC0415

    suffixes: list[str] = []
    for adapter in registered_adapters():
        if adapter.name not in movie.EXTRACTED_CONTAINERS:
            continue
        suffixes.extend(ext for ext in adapter.extensions if ext not in suffixes)
    return tuple(suffixes)


def _readable_suffixes() -> str:
    return ", ".join(movie_suffixes())


def _is_movie_file(path: Path) -> bool:
    return path.suffix.lower() in movie_suffixes() and path.is_file()


def _folder_movies(folder: Path, *, recursive: bool) -> list[Path]:
    """List the movie files in ``folder``, leaving out hidden names (a leading ``.``).

    Hidden names are left out because the patterns of `glob` leave them out too, and
    because macOS writes ``._name.tif`` resource files beside a movie on some drives.

    A sub-folder that cannot be listed raises its [`OSError`][] rather than being passed
    over, so a recursive batch never runs on a list with a folder silently missing. That is
    why this walks with [`os.walk`][] and its ``onerror`` instead of [`pathlib.Path.rglob`][],
    which drops such an error. Links to folders are not followed, as with ``rglob``.
    """
    if not recursive:
        return [
            path
            for path in folder.iterdir()
            if not path.name.startswith(".") and _is_movie_file(path)
        ]
    found = []

    def _refuse(exc: OSError) -> None:
        raise exc

    for top, dirnames, filenames in os.walk(folder, onerror=_refuse):
        dirnames[:] = [name for name in dirnames if not name.startswith(".")]
        for name in filenames:
            path = Path(top, name)
            if not name.startswith(".") and _is_movie_file(path):
                found.append(path)
    return found


def _listed_names(folder: Path) -> list[os.DirEntry[str]]:
    """List ``folder``; a folder that is missing gives nothing, one that cannot be read raises."""
    try:
        with os.scandir(folder) as entries:
            return list(entries)
    except (FileNotFoundError, NotADirectoryError):
        return []


def _present(path: Path) -> bool:
    """Whether ``path`` exists; a path whose folder cannot be searched raises."""
    try:
        os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return False
    return True


def _folders_below(folder: Path) -> list[Path]:
    """Return *folder* and every folder that ``**`` lists below it, as ``glob`` does.

    ``glob`` follows links to folders and skips hidden names, so this does the same; a
    folder reached twice (through a link) is listed once. A listing error is let through.
    """
    if not folder.is_dir():
        return []
    found: list[Path] = []
    seen: set[str] = set()
    pending = [folder]
    while pending:
        current = pending.pop()
        key = os.path.normcase(os.path.realpath(current))
        if key in seen:
            continue
        seen.add(key)
        found.append(current)
        with os.scandir(current) as entries:
            for entry in entries:
                if entry.name.startswith("."):
                    continue
                try:
                    is_folder = entry.is_dir()
                except OSError:
                    continue
                if is_folder:
                    pending.append(current / entry.name)
    return found


def _check_pattern_folders(pattern: str, root: Path) -> None:
    """Raise the [`OSError`][] of any folder that ``pattern`` must list and cannot.

    [`glob.glob`][] treats a folder it cannot list as empty, so ``data/**/*.tif`` with one
    unreadable day folder would give the other days' movies and no error. This walks the
    folders the pattern looks in, as ``glob`` does (a wildcard part lists its folder, ``**``
    every folder below it, hidden names only for a part that starts with ``.``), and lets
    a listing error through so that the argument is refused by name.
    """
    parts = Path(pattern).parts
    anchor = Path(pattern).anchor
    folders = [Path(anchor) if anchor else root]
    if anchor:
        parts = parts[1:]
    for index, part in enumerate(parts):
        last = index == len(parts) - 1
        if part == "**":
            folders = [below for folder in folders for below in _folders_below(folder)]
        elif glob.has_magic(part):
            matched = []
            for folder in folders:
                for entry in _listed_names(folder):
                    if entry.name.startswith(".") and not part.startswith("."):
                        continue
                    if fnmatch.fnmatch(entry.name, part) and (last or entry.is_dir()):
                        matched.append(folder / entry.name)
            folders = matched
        else:
            folders = [folder / part for folder in folders if _present(folder / part)]
            if not last:
                folders = [folder for folder in folders if folder.is_dir()]
        if last or not folders:
            return


def _expand_one(argument: str, *, recursive: bool, root: Path) -> list[Path]:
    path = root / argument
    if path.is_dir():
        found = _folder_movies(path, recursive=recursive)
        if not found:
            hint = "" if recursive else "; add --recursive to include its sub-folders"
            raise MovieArgumentError(
                f"folder {argument!r} holds no movie file ({_readable_suffixes()}){hint}"
            )
        return found
    if path.exists():
        # A file named on its own is the user's explicit choice: keep it whatever its
        # suffix, as before #653, and let the reader say if it cannot open it.
        return [path]
    if not _GLOB_CHARACTERS.intersection(argument):
        raise MovieArgumentError(f"no such movie file or folder: {argument!r}")
    _check_pattern_folders(argument, root)
    found = []
    for match in sorted(glob.glob(argument, root_dir=root, recursive=True)):
        match_path = root / match
        if match_path.is_dir():
            found.extend(_folder_movies(match_path, recursive=recursive))
        elif _is_movie_file(match_path):
            found.append(match_path)
    if not found:
        raise MovieArgumentError(
            f"pattern {argument!r} matches no movie file ({_readable_suffixes()})"
        )
    return found


def _sort_key(path: Path) -> str:
    """Return the key that sorts the movie list: the absolute path, in the OS's case rule."""
    return os.path.normcase(os.path.abspath(path))


def _identity(path: Path) -> str:
    """Return the key that finds a movie named twice: the path with its links resolved.

    Not [`os.path.abspath`][], which removes ``..`` as text: after a link, ``link/../m.tif``
    names a file in the link target's parent, and two different movies would share a key.
    """
    return os.path.normcase(os.path.realpath(path))


def expand_movie_arguments(
    arguments: Sequence[str], *, recursive: bool = False, root: Path | None = None
) -> tuple[Path, ...]:
    """Expand the movie arguments into a sorted list of movies with no repeats.

    Parameters
    ----------
    arguments
        What the user typed: files, folders or patterns (``*``, ``?``, ``[...]`` and
        ``**`` for any depth of folders). An argument that names an existing file or
        folder is never read as a pattern, so a file name with ``[`` in it works.
    recursive
        Include the movies in the sub-folders of a folder argument, and of a folder that
        a pattern matches.
    root
        Literal directory for relative arguments. Defaults to the current directory.

    Returns
    -------
    tuple[pathlib.Path, ...]
        The movies, sorted by their absolute path (without regard to case on Windows).
        A movie that two arguments both give, also through a link, appears once, with the
        spelling of the first.

    Raises
    ------
    MovieArgumentError
        If an argument is a missing path, a folder with no movie file, a pattern that
        matches no movie file, or a folder that cannot be listed (also a sub-folder that
        ``recursive`` or a pattern looks in). The message names the argument.
    """
    root = root if root is not None else Path()
    found: dict[str, Path] = {}
    for argument in arguments:
        try:
            paths = _expand_one(argument, recursive=recursive, root=root)
        except OSError as exc:
            # A folder that can be seen but not listed: a refusal that names it, not a
            # traceback from the middle of the expansion.
            raise MovieArgumentError(f"cannot read movie argument {argument!r}: {exc}") from exc
        for path in paths:
            found.setdefault(_identity(path), path)
    return tuple(sorted(found.values(), key=_sort_key))


def _parent_names(movie: Path) -> tuple[str, ...]:
    """Return the folder names above ``movie``, outermost first.

    They are those of its absolute path, read as text. Where that text names another file
    than the movie, the real location's folders are used instead: [`os.path.abspath`][]
    removes ``..`` as text, so after a link ``link/../m.tif`` would read as the folder
    that holds the link, while the file is in the link target's parent.
    """
    absolute = Path(os.path.abspath(movie))
    if _identity(absolute) != _identity(movie):
        absolute = Path(os.path.realpath(movie))
    return absolute.parent.relative_to(absolute.anchor).parts


def output_stems(movies: Sequence[Path]) -> tuple[str, ...]:
    """Give each movie the stem of its output project, unique without regard to case.

    The rule: a movie's output stem is its file stem. While two stems are equal (compared
    with `str.casefold`), each movie of that group that has a parent folder not yet
    used puts the next one in front, joined with [`NAME_SEPARATOR`][]. So
    ``day1/movie_010.tif`` and ``day2/movie_010.tif`` become ``day1__movie_010`` and
    ``day2__movie_010``, and a movie whose stem collides with no other keeps its own.
    The folder names are those of the movie's absolute path, so the result does not
    depend on the folder the command was run from. Where that path, read as text, names
    another file (a link followed by ``..``), the folders of the movie's real location
    are used.

    Parameters
    ----------
    movies
        The movies, as [`expand_movie_arguments`][] gives them (no movie twice).

    Returns
    -------
    tuple[str, ...]
        One stem for each movie, in the order of ``movies``.

    Raises
    ------
    OutputNameCollision
        If two movies still share a stem when neither has a parent folder left, such as
        ``A.tif`` and ``a.tif`` in one folder. Two movies of one stem in one folder with
        different suffixes, such as an ND2 original ``x.nd2`` beside its TIFF export
        ``x.tif``, are refused with a message that says so: the batch does not choose
        between them (#1388).
    """
    parents = [_parent_names(Path(movie)) for movie in movies]
    stems = [Path(movie).stem for movie in movies]
    depth = [0] * len(stems)

    def name(index: int) -> str:
        used = parents[index][len(parents[index]) - depth[index] :] if depth[index] else ()
        return NAME_SEPARATOR.join((*used, stems[index]))

    while True:
        names = [name(i) for i in range(len(stems))]
        groups: dict[str, list[int]] = defaultdict(list)
        for i, candidate in enumerate(names):
            groups[candidate.casefold()].append(i)
        colliding = [group for group in groups.values() if len(group) > 1]
        if not colliding:
            return tuple(names)
        grew = False
        for group in colliding:
            for i in group:
                if depth[i] < len(parents[i]):
                    depth[i] += 1
                    grew = True
        if not grew:
            first, second = colliding[0][:2]
            a, b = Path(movies[first]), Path(movies[second])
            if a.suffix.casefold() != b.suffix.casefold():
                # One stem, one folder, two containers: an original beside its export
                # (x.nd2 and x.tif). Neither is taken for the other; the user picks one
                # (#1388, ADR-0149).
                raise OutputNameCollision(
                    f"movies {str(a)!r} and {str(b)!r} have one name in two containers "
                    f"({a.suffix} and {b.suffix}), as an original and its export do; the "
                    "batch reads only one of them: move or rename the other"
                )
            raise OutputNameCollision(
                f"movies {str(movies[first])!r} and {str(movies[second])!r} both map to "
                f"the output name {names[first]!r} (names that differ only in case are "
                "one name); rename one or use a separate --out-dir"
            )


def describe_checkpoint(output: Path, *, policy: str, overwrite: bool, idealize: bool) -> str:
    """Say what a batch run will do with the existing output ``output``, without writing.

    The answer follows the runner's per-stage checkpoint
    ([`tether.project.batch.run_batch`][]): an absent output is ``new``; a complete one is
    skipped; a partial one resumes at its first missing stage; an output that holds no
    extraction is replaced only with ``--overwrite``; under ``policy="fail"`` a saved
    extraction over its gate fails again, or is extracted again with ``--overwrite``.
    The project file is opened read-only, and no movie is read.
    """
    from tether.project import batch  # noqa: PLC0415 - h5py only when an output exists

    if not os.path.lexists(output):
        return "new"
    try:
        batch._assert_output_not_newer(output)
    except Exception as exc:
        return f"exists, will fail: {exc}"
    if not batch._is_extracted(output):
        if overwrite:
            return "exists, incomplete: will be overwritten (--overwrite)"
        return "exists, incomplete: will fail; pass --overwrite to replace it"
    if policy == batch.POLICY_FAIL and batch._stored_extraction_is_over_gate(output):
        if overwrite:
            return "exists, over the gate: will be extracted again (--overwrite)"
        return "exists, over the gate: extract will fail again (policy=fail)"
    remaining = []
    if not batch._is_corrected(output):
        remaining.append(batch.STAGE_CORRECT)
    if idealize and not batch._is_idealized(output):
        remaining.append(batch.STAGE_IDEALIZE)
    if not remaining:
        return "exists, complete: will be skipped"
    return f"exists: will resume at {' + '.join(remaining)}"


def movie_in_project(project: Path, movie: Path) -> str | None:
    """Return the ``movie_id`` under which ``movie``'s content is in ``project``, or ``None``.

    The identity is the content ``sha256`` the append itself refuses a repeat by (#347,
    ADR-0117). The movie is hashed only when a ``/movies`` row has its file size, or has
    no size recorded, so a movie of a size the project does not hold is never read. The
    project is opened read-only; an absent project holds no movie.

    Raises
    ------
    OSError
        If the project or the movie cannot be read.
    """
    match = _find_movie_in_project(project, movie)
    return None if match is None else match[0]


def _find_movie_in_project(project: Path, movie: Path) -> tuple[str, str] | None:
    """Return the matching movie ID and content hash from the same project read."""
    if not os.path.lexists(project):
        return None
    import h5py  # noqa: PLC0415

    from tether.io.schema import TABLE  # noqa: PLC0415

    with h5py.File(project, "r") as f:
        table = f["movies"][TABLE][:]
    size = movie.stat().st_size
    candidates = [row for row in table if int(row["file_size"]) in (size, 0)]
    if not candidates:
        return None
    from tether.project.extract import _hash_movie  # noqa: PLC0415

    sha256 = _hash_movie(movie)[0]
    for row in candidates:
        if _text(row["sha256"]).lower() == sha256:
            return _text(row["movie_id"]), sha256
    return None


def _text(value: object) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def describe_append(project: Path, movie: Path, *, first: bool) -> str:
    """Say what an ``--into`` run will do with ``movie``, without writing (#1151).

    ``first`` is whether ``movie`` is the first of the run, which creates an absent
    project. A movie already in the project is skipped; any other is appended. A project
    written by a newer Tether, or one that cannot be read, is named as a failure.
    """
    from tether.project import batch  # noqa: PLC0415 - h5py only when the project exists

    if not os.path.lexists(project):
        return "new project: will be created with this movie" if first else "will be appended"
    try:
        batch._assert_output_not_newer(project)
        existing = movie_in_project(project, movie)
    except Exception as exc:
        return f"will fail: {exc}"
    if existing is not None:
        return f"already in the project as movie {existing!r}: will be skipped"
    return "will be appended"


def describe_appends(project: Path, movies: Sequence[Path]) -> list[str]:
    """Say what an ``--into`` run will do with each of ``movies``, in order (#1243).

    As [`describe_append`][] for each movie, and a movie with the same content as an
    earlier one the run adds is skipped, as the run skips it: the earlier one is in the
    project by then. Movies are hashed only when an earlier added movie has their size.
    """
    from tether.project.extract import _hash_movie  # noqa: PLC0415

    added: list[tuple[Path, int]] = []
    hashes: dict[Path, str | None] = {}

    def content(movie: Path) -> str | None:
        if movie not in hashes:
            try:
                hashes[movie] = _hash_movie(movie)[0]
            except OSError:
                hashes[movie] = None
        return hashes[movie]

    actions: list[str] = []
    for index, movie in enumerate(movies):
        action = describe_append(project, movie, first=index == 0)
        if action == "will be appended" or action.startswith("new project"):
            twin: Path | None = None
            try:
                size = movie.stat().st_size
            except OSError:
                size = -1
            else:
                twin = next(
                    (
                        earlier
                        for earlier, earlier_size in added
                        if earlier_size == size
                        and (earlier_hash := content(earlier)) is not None
                        and earlier_hash == content(movie)
                    ),
                    None,
                )
            if twin is not None:
                action = f"same content as {twin.name}: will be skipped"
            else:
                added.append((movie, size))
        actions.append(action)
    return actions


def describe_project_stages(project: Path, *, idealize: bool, appending: bool) -> str:
    """Say what an ``--into`` run will do with the project's correct and idealize stages.

    They run once over the whole project after the appends (#1151): always when the run
    appends a movie (``appending``), otherwise by the checkpoint of
    [`tether.project.batch.run_batch_into`][]. Read-only, like the rest of this module.
    """
    from tether.project import batch  # noqa: PLC0415

    stages = [batch.STAGE_CORRECT] + ([batch.STAGE_IDEALIZE] if idealize else [])
    named = " and ".join(stages)
    if appending:
        return f"{named} will run over every movie in the project after the appends"
    try:
        current = batch._movies_digest(project)
    except Exception as exc:
        return f"will fail: {exc}"

    def due(stage: str) -> bool:
        covered = batch._covered_movies(project, stage)
        if covered is not None and covered != current:
            return True
        if stage == batch.STAGE_CORRECT:
            return not batch._is_corrected(project)
        return not batch._is_idealized(project)

    remaining = [stage for stage in stages if due(stage)]
    if batch.STAGE_CORRECT in remaining and idealize:
        remaining = stages  # a new correction makes the stored model stale as well
    if not remaining:
        return f"no movie to append; {named} complete: will be skipped"
    return f"no movie to append; {' and '.join(remaining)} will run over the whole project"
