# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The three project exports of the application's **File → Export** menu (#621, PRD §7.9).

A front end builds an [`ExportRequest`][] and runs [`run_export`][] on a worker thread. It
calls one existing function of [`tether.project.export`][] and adds no export logic:

* ``table`` calls [`export_molecule_table_csv`][tether.project.export.export_molecule_table_csv];
* ``traces`` calls [`export_deeplasi_txt`][tether.project.export.export_deeplasi_txt];
* ``subset`` calls [`export_subset_tether`][tether.project.export.export_subset_tether].

[`run_export`][] writes the export and its provenance file under a temporary name in the
destination's folder, then moves both into place. So a cancel asked while the function runs
writes nothing, and the files are the ones the function writes for the same arguments.
[`export_message`][] is the status line of a finished export, in the form ``tether export``
prints, and [`error_text`][] the message of a failed one, unchanged from the function.

Like [`tether.services.movies`][], this is not a registered service command: ``tether
export`` is the command-line route to the same functions.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from tether.project.export import ExportResult

__all__ = [
    "DEFAULT_CURATOR_ALIAS",
    "ExportKind",
    "ExportRequest",
    "default_destination",
    "error_text",
    "export_message",
    "include_rejected_default",
    "run_export",
    "with_suffix",
]


class ExportKind(StrEnum):
    """The three project exports of the **File → Export** menu."""

    TABLE = "table"
    TRACES = "traces"
    SUBSET = "subset"


_SUFFIX: dict[ExportKind, str] = {
    ExportKind.TABLE: ".csv",
    ExportKind.TRACES: ".txt",
    ExportKind.SUBSET: ".tether",
}

#: The ``include_rejected`` default of each function: the table is a full inventory.
INCLUDE_REJECTED: dict[ExportKind, bool] = {
    ExportKind.TABLE: True,
    ExportKind.TRACES: False,
    ExportKind.SUBSET: False,
}

#: The default prefix of "Replace curator names", as in the how-to guide (#620).
DEFAULT_CURATOR_ALIAS = "curator"


@dataclass(frozen=True)
class ExportRequest:
    """The arguments of one export, as the dialog chose them.

    ``movie_of`` is the selected molecule as ``(row, molecule_key)``, its ``/molecules`` row
    and the key the window shows for it: when set, the Deep-LASI export takes the molecules
    of that row's movie, as ``molecule_keys``. The row names the movie, since a
    ``molecule_key`` is not unique (§7.10). ``curator_alias`` is the subset's
    ``curator_alias`` (``None`` keeps the names).
    """

    kind: ExportKind
    out_path: Path
    include_rejected: bool
    include_raw: bool = False
    curator_alias: str | None = None
    movie_of: tuple[int, str] | None = None


def default_destination(kind: ExportKind, project_path: str | os.PathLike[str]) -> Path:
    """Return the destination the dialog proposes: beside the project, named after it.

    The trace file takes the ``-donc-accc-w.txt`` ending that Deep-LASI reads. The path is
    absolute, so the dialog shows the folder it writes to.
    """
    project = Path(project_path).absolute()
    names = {
        ExportKind.TABLE: f"{project.stem}.csv",
        ExportKind.TRACES: f"{project.stem}-donc-accc-w.txt",
        ExportKind.SUBSET: f"{project.stem}-subset.tether",
    }
    return project.with_name(names[kind])


def with_suffix(kind: ExportKind, text: str) -> Path:
    """Return the path ``text``, with the file ending of ``kind`` added when it has none."""
    path = Path(text)
    suffix = _SUFFIX[kind]
    return path if path.name.lower().endswith(suffix) else path.with_name(path.name + suffix)


def _text(value: object) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _movie_keys(project_path: Path, row: int, molecule_key: str) -> list[str]:
    """Return the ``molecule_key`` of each molecule of the movie of ``/molecules`` row ``row``.

    The export function selects by ``molecule_key``, which is not unique (§7.10): a key
    the movie shares with a molecule of another movie would bring that molecule in too.
    Such a movie is refused rather than exported with another movie's rows.

    Raises
    ------
    ValueError
        If ``row`` is not the molecule ``molecule_key`` (the project changed), or a key of
        the movie also names a molecule of another movie.
    """
    from tether.imaging.extract import read_molecules

    molecules = read_molecules(project_path)
    keys = [_text(k) for k in molecules["molecule_key"]]
    movies = [_text(m) for m in molecules["movie_id"]]
    if not 0 <= row < len(keys) or keys[row] != molecule_key:
        raise ValueError(
            f"the selected molecule {molecule_key!r} is not row {row} of the project; "
            "open the project again"
        )
    movie = movies[row]
    chosen = [k for k, m in zip(keys, movies, strict=True) if m == movie]
    elsewhere = {k for k, m in zip(keys, movies, strict=True) if m != movie}
    shared = sorted(set(chosen) & elsewhere)
    if shared:
        raise ValueError(
            f"molecule_key(s) {shared} of movie {movie!r} also name molecules of another "
            "movie, so the trace file cannot hold this movie alone"
        )
    return chosen


def _call(request: ExportRequest, project_path: Path, out_path: Path) -> ExportResult:
    from tether.project.export import (
        export_deeplasi_txt,
        export_molecule_table_csv,
        export_subset_tether,
    )

    if request.kind is ExportKind.TABLE:
        return export_molecule_table_csv(
            project_path, out_path, include_rejected=request.include_rejected
        )
    if request.kind is ExportKind.TRACES:
        keys = None if request.movie_of is None else _movie_keys(project_path, *request.movie_of)
        return export_deeplasi_txt(
            project_path,
            out_path,
            molecule_keys=keys,
            include_rejected=request.include_rejected,
        )
    return export_subset_tether(
        project_path,
        out_path,
        include_rejected=request.include_rejected,
        include_raw=request.include_raw,
        overwrite=True,  # the staged name exists; the destination is replaced only at the end
        curator_alias=request.curator_alias,
    )


def run_export(
    project_path: str | os.PathLike[str],
    request: ExportRequest,
    *,
    cancel: Any = None,
) -> ExportResult:
    """Write the export ``request`` of ``project_path``; the work of the background task.

    The function writes to a temporary name in the destination's folder, so a relative path
    the subset stores still names the same file. When ``cancel`` was asked for by the time
    the function returns, both files are removed and the token's ``raise_if_cancelled()``
    raises: a cancelled export writes nothing. Otherwise the export and its provenance file
    replace the destination's; a cancel asked after that is too late, and the export
    finishes as usual.

    Raises
    ------
    LockedError
        For a subset, when another writer holds the destination project
        ([`publish_lock`][tether.project.lock.publish_lock]).
    ValueError, KeyError, FileExistsError, OSError
        As the export function raises them, and ``ValueError`` when the destination is the
        project itself
        ([`refuse_source_destination`][tether.project.export.refuse_source_destination]).
    Exception
        What ``cancel.raise_if_cancelled()`` raises, when ``cancel`` was asked for. ``cancel``
        is a token with ``raise_if_cancelled()``, such as the window's background-task token.
    """
    from tether.project.export import ExportResult, refuse_source_destination

    project = Path(project_path)
    out_path = Path(request.out_path)
    what = "subset out_path" if request.kind is ExportKind.SUBSET else "export destination"
    refuse_source_destination(project, out_path, what=what)
    if cancel is not None:
        cancel.raise_if_cancelled()
    fd, name = tempfile.mkstemp(
        dir=out_path.parent, prefix=f".{out_path.name}.", suffix=f".export{out_path.suffix}"
    )
    os.close(fd)
    staged = Path(name)
    staged_provenance = staged.with_name(staged.name + ".provenance.json")
    provenance = out_path.with_name(out_path.name + ".provenance.json")
    try:
        result = _call(request, project, staged)
        if cancel is not None:
            cancel.raise_if_cancelled()
        moves = [(staged, out_path), (staged_provenance, provenance)]
        if request.kind is ExportKind.SUBSET:
            # The destination is a project: another writer may hold it (ADR-0112).
            from tether.project.lock import publish_lock

            with publish_lock(out_path) as guard:
                _publish(moves, staged, guard=guard)
        else:
            _publish(moves, staged)
    except BaseException:
        for leftover in (staged, staged_provenance):
            with contextlib.suppress(FileNotFoundError):
                leftover.unlink()
        raise
    return ExportResult(path=out_path, provenance_path=provenance, n_molecules=result.n_molecules)


def _publish(
    moves: list[tuple[Path, Path]], staged: Path, *, guard: Callable[[], None] | None = None
) -> None:
    """Move each staged file onto its destination, all of them or none.

    The files already at the destinations are set aside first, under names beside the
    staged file. When a move fails, the files moved so far are removed and the previous
    ones are put back, so a failed export leaves the destination pair as it was: never new
    data with stale provenance, and never a lost previous export.

    ``guard`` is the check of the destination project's
    [`publish_lock`][tether.project.lock.publish_lock], and the first move is then that
    project. The project file is never set aside, since another writer could take the lock
    while it is away. The other files are placed first; then ``guard`` runs immediately
    before one atomic replace of the project, which its identity record follows. When the
    check or the replace fails, the other files are put back only if the lock is still held;
    after a takeover nothing at the destination is touched, and the files set aside stay
    beside it under their temporary names.
    """
    if guard is None:
        kept, placed = _place(moves, staged)
        _drop(kept)
        return
    (source, target), others = moves[0], moves[1:]
    guard()
    kept, placed = _place(others, staged)
    try:
        guard()
        from tether.project.lock import replacement_identity

        with replacement_identity(source, target):
            os.replace(source, target)
    except BaseException:
        # Put the other files back only while this export still holds the lock: after a
        # takeover the files there may be the new owner's, and the ones set aside stay
        # under their temporary names.
        if _still_held(guard):
            _undo(kept, placed)
        raise
    _drop(kept)


def _still_held(guard: Callable[[], None]) -> bool:
    """Return whether the lock check ``guard`` still passes."""
    try:
        guard()
    except Exception:  # noqa: BLE001 - any failed check means the lock is not ours
        return False
    return True


def _place(
    moves: list[tuple[Path, Path]], staged: Path
) -> tuple[list[tuple[Path, Path]], list[Path]]:
    """Set the destination files aside and move the staged ones in; undo on a failure."""
    kept: list[tuple[Path, Path]] = []
    placed: list[Path] = []
    try:
        for i, (_source, target) in enumerate(moves):
            if target.is_file():
                aside = staged.with_name(f"{staged.name}.previous-{i}")
                os.replace(target, aside)
                kept.append((aside, target))
        for source, target in moves:
            os.replace(source, target)
            placed.append(target)
    except BaseException:
        _undo(kept, placed)
        raise
    return kept, placed


def _undo(kept: list[tuple[Path, Path]], placed: list[Path]) -> None:
    """Remove the files placed and put the ones set aside back."""
    for target in placed:
        with contextlib.suppress(FileNotFoundError):
            target.unlink()
    for aside, target in kept:
        os.replace(aside, target)


def _drop(kept: list[tuple[Path, Path]]) -> None:
    """Delete the files set aside, once every move succeeded."""
    for aside, _target in kept:
        with contextlib.suppress(FileNotFoundError):
            aside.unlink()


def export_message(result: ExportResult) -> str:
    """Return the status line of a finished export, in the form ``tether export`` prints."""
    return f"Exported {result.n_molecules} molecule(s) -> {result.path.name}"


def error_text(error: BaseException | None) -> str:
    """Return the message of a failed export, as the function raised it.

    ``str()`` of a ``KeyError`` quotes its message; its argument is the message itself.
    """
    if isinstance(error, KeyError) and len(error.args) == 1:
        return str(error.args[0])
    return str(error)


def include_rejected_default(kind: ExportKind) -> bool:
    """Return the ``include_rejected`` default of the function of ``kind``."""
    return INCLUDE_REJECTED[kind]
