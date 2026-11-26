# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The work of **Locate movie…**, **Locate movie folder…** and **Download and open movie…** (#638).

The shell runs each of these on a background-task service of its own, so a checksum read of a
large movie neither freezes the window nor holds up a curation write. They read files and never
the project's writer lock, and they write nothing into the project: what they find is
remembered for this computer ([`tether.gui.preferences.record_movie_location`][]). The rules
are [`tether.services.movie_locations`][]'s; this module only binds them to one project and
says what happened in plain words.

No Qt is imported here: these functions run on the worker thread.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from tether.gui.tasks import ProgressReport
    from tether.project.jobs import CancelToken
    from tether.services.movie_locations import MovieLocation
    from tether.services.movies import ProjectMovie

__all__ = ["LocateResult", "download", "format_size", "locate_folder", "locate_one"]


@dataclass(frozen=True)
class LocateResult:
    """What a locate job found: ``found`` maps a ``movie_id`` to its checksum and location.

    ``problems`` are one-line reasons, in plain words, for the movies that were not found.
    """

    found: Mapping[str, tuple[str, MovieLocation]] = field(default_factory=dict)
    problems: tuple[str, ...] = ()


def format_size(n_bytes: int) -> str:
    """Return ``n_bytes`` as a size a person reads: ``"512 bytes"``, ``"3.2 MB"``, ``"1.5 GB"``.

    Decimal units (1 GB = 10⁹ bytes), as file managers show a file's size.

    Examples
    --------
    >>> format_size(2_500_000_000)
    '2.5 GB'
    """
    if n_bytes < 1000:
        return f"{n_bytes} bytes"
    size = float(n_bytes)
    for unit in ("kB", "MB", "GB", "TB"):
        size /= 1000.0
        if size < 1000.0 or unit == "TB":
            return f"{size:.1f} {unit}"
    raise AssertionError("unreachable")  # pragma: no cover


def locate_one(
    project_path: Path,
    movie_id: str,
    candidate: Path,
    *,
    cancel: CancelToken | None = None,
    progress: ProgressReport | None = None,
) -> LocateResult:
    """Check that ``candidate`` is the project movie ``movie_id``, by its checksum."""
    from tether.services.movie_locations import check_relocated, expected_movie_sha256

    expected = expected_movie_sha256(project_path, movie_id)
    location = check_relocated(
        candidate,
        expected,
        cancel=cancel,
        progress=progress.report if progress is not None else None,
    )
    return LocateResult(found={movie_id: (expected, location)})


def locate_folder(
    project_path: Path,
    movies: Sequence[ProjectMovie],
    folder: Path,
    *,
    cancel: CancelToken | None = None,
    progress: ProgressReport | None = None,
) -> LocateResult:
    """Find each of ``movies`` under ``folder``, by name and then by checksum.

    The search is [`locate_in_folder`][tether.services.movie_locations.locate_in_folder].
    """
    from tether.services.movie_locations import expected_movie_sha256, locate_in_folder

    search = locate_in_folder(
        project_path,
        movies,
        folder,
        cancel=cancel,
        progress=progress.report if progress is not None else None,
    )
    names = {movie.movie_id: movie.name for movie in movies}
    found = {
        movie_id: (expected_movie_sha256(project_path, movie_id), location)
        for movie_id, location in search.found.items()
    }
    problems = [
        *(f"{names[m]}: {why}" for m, why in search.refused.items()),
        *(
            f"{names[m]}: found only as a file that is not on this disk (not downloaded)"
            for m in search.placeholders
        ),
        *(f"{names[m]}: no file of that name in the folder" for m in search.not_found),
    ]
    return LocateResult(found=found, problems=tuple(problems))


def download(
    path: Path,
    *,
    cancel: CancelToken | None = None,
    progress: ProgressReport | None = None,
) -> int:
    """Read the movie file at ``path`` once, which downloads a cloud placeholder (PRD §5.4)."""
    from tether.services.movie_locations import download_movie

    return download_movie(
        path, cancel=cancel, progress=progress.report if progress is not None else None
    )
