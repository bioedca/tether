# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The ``batch`` service command: extract, correct and idealize many movies.

[`BatchRequest`][] holds what ``tether batch`` takes on its command line, with the same
defaults, and the seventeen extraction options of an extraction profile
([`tether.services.profile`][], #349); [`run`][] builds one
[`MovieJob`][tether.project.batch.MovieJob] for each movie and calls
[`tether.project.batch.run_batch`][] with the
[`ExtractOptions`][tether.project.extract.ExtractOptions] those options name. The core is
imported when the command runs, not when this module is imported (ADR-0100).
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from tether.services.base import CommandFailed, Request, RequestRefused
from tether.services.profile import ExtractionFields

if TYPE_CHECKING:  # pragma: no cover - typing only
    from tether.project.batch import BatchSummary

__all__ = ["BatchRequest", "BatchResult", "run"]


@dataclasses.dataclass(frozen=True, kw_only=True)
class BatchRequest(ExtractionFields, Request):
    """Run the extract -> correct -> idealize pipeline over many movies.

    The fields are the options of ``tether batch`` (``docs/cli.md``) with the same
    defaults, and the seventeen
    [`ExtractionFields`][tether.services.profile.ExtractionFields] applied to every movie,
    which ``tether batch --profile`` sets (none of them has a flag of its own). Two flags
    are stated positively: ``--no-idealize`` is ``idealize=False`` and ``--no-defer`` is
    ``defer_if_unavailable=False``. ``None`` in a supervision field means the
    [`SidecarSupervision`][tether.idealize.supervisor.SidecarSupervision] default (§11.2).
    """

    COMMAND: ClassVar[str] = "batch"
    VERSION: ClassVar[int] = 1

    movies: tuple[str, ...]
    out_dir: str
    tmap: str | None = None
    tdat: str | None = None
    policy: str = "warn"
    idealize: bool = True
    overwrite: bool = False
    log: str | None = None
    max_restarts: int | None = None
    sidecar_timeout: float | None = None
    sidecar_python: str | None = None
    defer_if_unavailable: bool = True


@dataclasses.dataclass(frozen=True)
class BatchResult:
    """The outcome of a ``batch`` command: the run summary and the log it wrote."""

    summary: BatchSummary
    log_path: Path


def run(request: BatchRequest) -> BatchResult:
    """Run *request*.

    Raises [`RequestRefused`][tether.services.base.RequestRefused] before any movie is read when
    the policy, the supervision settings or the extraction options are invalid, when a
    ``tdat`` comes with a ``detection_mode`` or ``detection_threshold``, when there is no
    movie, or when two movies would write the same project. Raises
    [`CommandFailed`][tether.services.base.CommandFailed] when the output directory or the log
    cannot be written. A failed stage is not an error: it is in the summary.
    """
    from tether.project.batch import POLICIES, BatchLog, MovieJob, run_batch
    from tether.project.extract import ExtractionError

    if not request.movies:
        raise RequestRefused("a batch needs at least one movie")
    if request.policy not in POLICIES:
        raise RequestRefused(f"policy must be one of {POLICIES}, got {request.policy!r}")
    # A .tdat supplies the detection mode and threshold; a choice of either beside it is
    # ambiguous, so it is refused, as tether extract refuses it (#349).
    if request.tdat is not None and request.sets_detection():
        raise RequestRefused(
            "detection_mode/detection_threshold cannot be combined with --tdat (the .tdat "
            "supplies them); leave them null in the profile or drop --tdat"
        )
    try:
        extract_options = request.extract_options()
    except ExtractionError as exc:
        raise RequestRefused(str(exc)) from exc
    # The overnight batch tool supervises the idealization sidecar by default (PR7-B):
    # a startup liveness probe -> idealization-deferred mode when the sidecar env is
    # absent/corrupt, plus per-movie auto-restart on transient failures. Unset overrides
    # fall back to the SidecarSupervision (§11.2) defaults.
    supervision = None
    if request.idealize:
        from tether.idealize.supervisor import SidecarSupervision

        sup_kwargs: dict[str, object] = {"defer_if_unavailable": request.defer_if_unavailable}
        if request.max_restarts is not None:
            sup_kwargs["max_restarts"] = request.max_restarts
        if request.sidecar_timeout is not None:
            sup_kwargs["timeout"] = request.sidecar_timeout
        if request.sidecar_python is not None:
            sup_kwargs["sidecar_python"] = request.sidecar_python
        try:
            supervision = SidecarSupervision(**sup_kwargs)
        except ValueError as exc:
            raise RequestRefused(str(exc)) from exc

    out_dir = Path(request.out_dir)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise CommandFailed(f"cannot create output directory {out_dir}: {exc}") from exc
    # Each movie maps to <out-dir>/<stem>.tether. Two movies with the same basename
    # (e.g. `movie_010.tif` from different condition folders) would collide on one
    # output, and the batch runner's provenance checkpoint would silently treat the
    # second as "already done" using the first's data -- reject them up front.
    jobs = []
    seen: dict[str, str] = {}
    for movie in request.movies:
        stem = Path(movie).stem
        if stem in seen:
            raise RequestRefused(
                f"movies {seen[stem]!r} and {movie!r} both map to "
                f"{(out_dir / (stem + '.tether')).as_posix()!r}; rename one or use a "
                "separate --out-dir"
            )
        seen[stem] = movie
        jobs.append(
            MovieJob(
                movie_path=movie,
                output_path=out_dir / f"{stem}.tether",
                tmap=request.tmap,
                tdat=request.tdat,
            )
        )
    log_path = Path(request.log) if request.log else out_dir / "batch-log.jsonl"
    try:
        with BatchLog(path=log_path) as log:
            summary = run_batch(
                jobs,
                policy=request.policy,
                extract_options=extract_options,
                idealize=request.idealize,
                supervision=supervision,
                overwrite=request.overwrite,
                log=log,
            )
    except OSError as exc:
        raise CommandFailed(f"cannot write the batch output or log {log_path}: {exc}") from exc
    return BatchResult(summary=summary, log_path=log_path)
