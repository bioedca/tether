# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The ``reextract`` service command: extract a movie of a project again (#923).

[`ReextractRequest`][] names a ``.tether`` project, a movie the project holds and the
extraction options of the new run. [`run`][] holds the project's writer lock and passes the
request to [`reextract_movie`][tether.project.extract.reextract_movie], which writes a new
trace generation: the molecules that did not move keep their key, and their labels,
category and window move to the new rows with the mark *carried*; every fit and correction
of the movie becomes stale; the previous generation stays readable (ADR-0136). The result
holds the reconciliation of the new generation with the one before it
([`tether.project.generation_reconcile`][]): the carried, new and lost molecules.

``tether reextract`` and a recipe step run this command, so the command line, a recipe
and the application make the same move. The imaging and HDF5 stack is imported when the
command runs, not when this module is imported (ADR-0100).
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, ClassVar

from tether.services.base import CommandFailed, Request
from tether.services.profile import ExtractionFields, frame_steps_from_text

if TYPE_CHECKING:  # pragma: no cover - typing only
    from tether.project.extract import GenerationSummary
    from tether.project.generation_reconcile import GenerationReconciliation

__all__ = ["ReextractRequest", "ReextractResult", "run"]


@dataclasses.dataclass(frozen=True, kw_only=True)
class ReextractRequest(ExtractionFields, Request):
    """Extract a movie that a ``.tether`` project holds again, as a new trace generation.

    The extraction fields are those of
    [`ExtractionFields`][tether.services.profile.ExtractionFields], with the same defaults;
    ``tether reextract --profile`` sets them. The aperture fields (``disk_radius``,
    ``ring_inner``, ``ring_outer``, ``bg_window``) must equal the project's.

    Attributes
    ----------
    project
        Path to the ``.tether`` project that holds the movie.
    movie
        Path to the movie file; its bytes must be the ones the project recorded.
    tmap
        Path to an imported Deep-LASI ``.tmap`` to apply instead of a native fit, or
        ``None``.
    tdat
        Path to a Deep-LASI ``.tdat`` whose particle-detection mode is applied, or ``None``.
    map_path
        Path to a saved Tether ``.npz`` map file to apply instead of a native fit, or ``None``.
    match_px
        The "did not move" distance in pixels, or ``None`` for
        [`GENERATION_MATCH_PX`][tether.io.generations.GENERATION_MATCH_PX].
    frame_steps
        The movie's own frame steps (#1435), or ``None`` to use again the steps the movie
        was last extracted with (a map's default gives way to the new map's own).
    map_frame_steps
        With ``tmap``: the ``.tmap``'s default frame steps, or ``None``.
    """

    COMMAND: ClassVar[str] = "reextract"
    VERSION: ClassVar[int] = 1
    PATH_FIELDS: ClassVar[tuple[str, ...]] = ("project", "movie", "tmap", "tdat", "map_path")
    PROJECT_FIELD: ClassVar[str | None] = "project"

    project: str
    movie: str
    tmap: str | None = None
    tdat: str | None = None
    map_path: str | None = None
    match_px: float | None = None
    frame_steps: str | None = None
    map_frame_steps: str | None = None


@dataclasses.dataclass(frozen=True)
class ReextractResult:
    """The outcome of a ``reextract`` command.

    ``summary`` is the core's [`GenerationSummary`][tether.project.extract.GenerationSummary];
    ``reconciliation`` lists the carried, new and lost molecules of the new generation.
    """

    summary: GenerationSummary
    reconciliation: GenerationReconciliation


def run(request: ReextractRequest) -> ReextractResult:
    """Run *request*; raise [`CommandFailed`][tether.services.base.CommandFailed] on a user error.

    The message of the error is the message of the core's
    [`ExtractionError`][tether.project.extract.ExtractionError], of a
    [`LockError`][tether.project.lock.LockError] or of an `OSError`, unchanged.
    """
    from tether.project.extract import ExtractionError, reextract_movie
    from tether.project.generation_reconcile import reconcile_generation
    from tether.project.lock import LockError, publish_lock

    try:
        if request.tdat is not None and request.sets_detection():
            raise ExtractionError(
                "--detection-mode/--detection-threshold cannot be combined with --tdat "
                "(the .tdat supplies the detection mode); pass one or the other"
            )
        options = request.extract_options()
        with publish_lock(request.project) as guard:
            summary = reextract_movie(
                request.project,
                request.movie,
                options=options,
                tmap=request.tmap,
                tdat=request.tdat,
                map_path=request.map_path,
                publish_guard=guard,
                match_px=request.match_px,
                frame_steps=frame_steps_from_text(request.frame_steps),
                map_frame_steps=frame_steps_from_text(request.map_frame_steps, "map_frame_steps"),
            )
            # Read under the same lock, so the lists are those of this new generation.
            reconciliation = reconcile_generation(request.project, summary.extraction.movie_id)
    except (ExtractionError, LockError, OSError) as exc:
        raise CommandFailed(str(exc)) from exc
    assert reconciliation is not None  # a new generation always has a previous one
    return ReextractResult(summary=summary, reconciliation=reconciliation)
