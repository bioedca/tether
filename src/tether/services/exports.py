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

    ``movie_of`` is a ``molecule_key``: when set, the Deep-LASI export takes the molecules of
    that molecule's movie, as ``molecule_keys``. ``curator_alias`` is the subset's
    ``curator_alias`` (``None`` keeps the names).
    """

    kind: ExportKind
    out_path: Path
    include_rejected: bool
    include_raw: bool = False
    curator_alias: str | None = None
    movie_of: str | None = None


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


def _movie_keys(project_path: Path, molecule_key: str) -> list[str]:
    """Return the ``molecule_key`` of each molecule of the movie of ``molecule_key``."""
    from tether.imaging.extract import read_molecules

    molecules = read_molecules(project_path)
    keys = [_text(k) for k in molecules["molecule_key"]]
    movies = [_text(m) for m in molecules["movie_id"]]
    if molecule_key not in keys:
        raise KeyError(f"no molecule with molecule_key(s) {[molecule_key]} in the store")
    movie = movies[keys.index(molecule_key)]
    return [k for k, m in zip(keys, movies, strict=True) if m == movie]


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
        keys = None if request.movie_of is None else _movie_keys(project_path, request.movie_of)
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
    try:
        result = _call(request, project, staged)
        if cancel is not None:
            cancel.raise_if_cancelled()
        provenance = out_path.with_name(out_path.name + ".provenance.json")
        os.replace(staged, out_path)
        os.replace(staged_provenance, provenance)
    except BaseException:
        for leftover in (staged, staged_provenance):
            with contextlib.suppress(FileNotFoundError):
                leftover.unlink()
        raise
    return ExportResult(path=out_path, provenance_path=provenance, n_molecules=result.n_molecules)


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
