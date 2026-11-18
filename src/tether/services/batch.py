# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The ``batch`` service command: extract, correct and idealize many movies.

[`BatchRequest`][] holds what ``tether batch`` takes on its command line, with the same
defaults, and the extraction options of an extraction profile
([`tether.services.profile`][], #349); [`run`][] builds one
[`MovieJob`][tether.project.batch.MovieJob] for each movie and calls
[`tether.project.batch.run_batch`][] with the
[`ExtractOptions`][tether.project.extract.ExtractOptions] those options name. [`plan`][] builds
the same jobs and says what a run would do with each, reading no movie and writing nothing
(``tether batch --dry-run``). A movie argument may be a file, a folder or a pattern, and each
movie's output name follows the rule of [`tether.project.batch_inputs.output_stems`][] (#653).
The core is imported when the command runs, not when this module is imported (ADR-0100).
"""

from __future__ import annotations

import contextlib
import dataclasses
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Protocol

from tether.services.base import CommandFailed, Request, RequestRefused
from tether.services.profile import ExtractionFields

if TYPE_CHECKING:  # pragma: no cover - typing only
    from tether.idealize.supervisor import SidecarSupervision
    from tether.project.batch import BatchSummary, MovieJob, MovieResult
    from tether.project.extract import ExtractOptions

__all__ = ["BatchPlan", "BatchRequest", "BatchResult", "PlannedMovie", "plan", "run"]


@dataclasses.dataclass(frozen=True, kw_only=True)
class BatchRequest(ExtractionFields, Request):
    """Run the extract -> correct -> idealize pipeline over many movies.

    The fields are the options of ``tether batch`` (``docs/cli.md``) with the same
    defaults, and the
    [`ExtractionFields`][tether.services.profile.ExtractionFields] applied to every movie,
    which ``tether batch --profile`` sets (none of them has a flag of its own). Each of
    ``movies`` is a file, a folder or a pattern; ``recursive`` makes a folder include its
    sub-folders. Two flags are stated positively: ``--no-idealize`` is ``idealize=False``
    and ``--no-defer`` is ``defer_if_unavailable=False``. ``None`` in a supervision field
    means the
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
    recursive: bool = False


@dataclasses.dataclass(frozen=True)
class BatchResult:
    """The outcome of a ``batch`` command: the run summary and the log it wrote."""

    summary: BatchSummary
    log_path: Path


@dataclasses.dataclass(frozen=True)
class PlannedMovie:
    """One movie of a dry run: where it goes and what a run would do with it."""

    movie: Path
    output: Path
    action: str


@dataclasses.dataclass(frozen=True)
class BatchPlan:
    """What a ``batch`` request would do, found without reading a movie or writing a file."""

    movies: tuple[PlannedMovie, ...]
    out_dir: Path
    log_path: Path
    settings: str
    sidecar: str

    def format_plan(self) -> str:
        """Format the dry-run report: one line for each movie, then the settings and the sidecar."""
        lines = [
            f"Batch plan (dry run): {len(self.movies)} movie(s) into {self.out_dir}; "
            "no movie was read and nothing was written"
        ]
        lines.extend(f"  {m.movie} -> {m.output}  [{m.action}]" for m in self.movies)
        lines.append(f"Settings: {self.settings}")
        lines.append(f"Structured log: {self.log_path}")
        lines.append(f"Sidecar: {self.sidecar}")
        return "\n".join(lines)


class _Progress(Protocol):
    """What ``run`` needs of a progress observer.

    [`BatchProgress`][tether.project.batch.BatchProgress] provides it. It is written out
    here so that this module imports no part of the core (ADR-0100).
    """

    def run_started(
        self, *, n_movies: int, out_dir: str | Path, policy: str, idealize: bool
    ) -> None: ...

    def event(self, record: dict[str, Any]) -> None: ...

    def movie_started(self, index: int, total: int, job: MovieJob) -> None: ...

    def movie_finished(self, index: int, total: int, result: MovieResult) -> None: ...


def _settings(request: BatchRequest) -> tuple[ExtractOptions, SidecarSupervision | None]:
    """Validate the request's settings; return its extraction options and sidecar supervision."""
    from tether.project.batch import POLICIES
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
    # A .tmap splits each frame at its own crops, so extraction refuses a layout beside it
    # for every movie; refusing it here keeps the dry run from promising a run that fails.
    if request.tmap is not None and extract_options.channel_layout is not None:
        raise RequestRefused(
            "channel_layout and --tmap both set the channel split (the .tmap splits at its "
            "own crops); leave channel_layout null in the profile or drop --tmap"
        )
    # The overnight batch tool supervises the idealization sidecar by default (PR7-B):
    # a startup liveness probe -> idealization-deferred mode when the sidecar env is
    # absent/corrupt, plus per-movie auto-restart on transient failures. Unset overrides
    # fall back to the SidecarSupervision (§11.2) defaults.
    if not request.idealize:
        return extract_options, None
    from tether.idealize.supervisor import SidecarSupervision

    sup_kwargs: dict[str, object] = {"defer_if_unavailable": request.defer_if_unavailable}
    if request.max_restarts is not None:
        sup_kwargs["max_restarts"] = request.max_restarts
    if request.sidecar_timeout is not None:
        sup_kwargs["timeout"] = request.sidecar_timeout
    if request.sidecar_python is not None:
        sup_kwargs["sidecar_python"] = request.sidecar_python
    try:
        return extract_options, SidecarSupervision(**sup_kwargs)
    except ValueError as exc:
        raise RequestRefused(str(exc)) from exc


def _jobs(request: BatchRequest, out_dir: Path) -> list[MovieJob]:
    """Expand the movie arguments and give each movie its own output project.

    Two movies that would write one output would make the checkpoint treat the second as
    already done with the first one's data, so their output names take parent folder names
    until they differ ([`output_stems`][tether.project.batch_inputs.output_stems]); a
    collision that no folder name resolves is refused.
    """
    from tether.project.batch import MovieJob
    from tether.project.batch_inputs import (
        MovieArgumentError,
        OutputNameCollision,
        expand_movie_arguments,
        output_stems,
    )

    try:
        movies = expand_movie_arguments(request.movies, recursive=request.recursive)
        stems = output_stems(movies)
    except (MovieArgumentError, OutputNameCollision) as exc:
        raise RequestRefused(str(exc)) from exc
    return [
        MovieJob(
            movie_path=movie,
            output_path=out_dir / f"{stem}.tether",
            tmap=request.tmap,
            tdat=request.tdat,
        )
        for movie, stem in zip(movies, stems, strict=True)
    ]


def _log_path(request: BatchRequest, out_dir: Path) -> Path:
    return Path(request.log) if request.log else out_dir / "batch-log.jsonl"


def _sidecar_status(supervision: SidecarSupervision | None) -> str:
    """Whether the run would find a sidecar interpreter; nothing is launched."""
    if supervision is None:
        return "not needed (idealization not requested)"
    from tether.idealize.driver import SidecarError, resolve_sidecar_python

    try:
        found = resolve_sidecar_python(supervision.sidecar_python)
    except SidecarError as exc:
        then = (
            "idealization would be deferred"
            if supervision.defer_if_unavailable
            else "each movie's idealize stage would fail (--no-defer)"
        )
        return f"not found ({exc}); {then}"
    return f"found at {found} (the run checks that it starts before the first movie)"


def _rect_text(rect: tuple[int, ...] | None) -> str:
    return "[" + " ".join(str(v) for v in rect or ()) + "]"


def _extraction_settings(options: ExtractOptions, *, tmap: bool, tdat: bool) -> tuple[str, ...]:
    """Name the channel split and the detection the run would extract each movie with."""
    layout = options.channel_layout
    if tmap:  # the .tmap splits at its own crops, so donor_side is not used
        split: tuple[str, ...] = ("channel-layout=from .tmap",)
    elif layout is None:
        split = ("channel-layout=none", f"donor-side={options.donor_side}")
    elif layout.donor is None:  # a rectangles layout names its channels by rectangle
        split = (
            f"channel-layout={layout.kind}",
            f"donor-rect={_rect_text(layout.donor_rect)}",
            f"acceptor-rect={_rect_text(layout.acceptor_rect)}",
        )
    else:
        split = (f"channel-layout={layout.kind}", f"donor={layout.donor}")
    if tdat:  # the .tdat supplies both, and a choice beside it was refused
        return (*split, "detection-mode=from .tdat", "detection-threshold=from .tdat")
    threshold = options.detection_threshold
    return (
        *split,
        f"detection-mode={options.detection_mode}",
        f"detection-threshold={'mode default' if threshold is None else threshold}",
    )


def plan(request: BatchRequest) -> BatchPlan:
    """Say what [`run`][] would do with *request*, reading no movie and writing nothing.

    Raises [`RequestRefused`][tether.services.base.RequestRefused] for the same requests
    that [`run`][] refuses. An existing output project is opened read-only to read its
    checkpoint; the output directory is not created.
    """
    from tether.project.batch_inputs import describe_checkpoint

    extract_options, supervision = _settings(request)
    out_dir = Path(request.out_dir)
    jobs = _jobs(request, out_dir)
    movies = tuple(
        PlannedMovie(
            movie=job.movie_path,
            output=job.output_path,
            action=describe_checkpoint(
                job.output_path,
                policy=request.policy,
                overwrite=request.overwrite,
                idealize=request.idealize,
            ),
        )
        for job in jobs
    )
    settings = ", ".join(
        (
            f"policy={request.policy}",
            f"idealize={'on' if request.idealize else 'off'}",
            f"overwrite={'on' if request.overwrite else 'off'}",
            f"recursive={'on' if request.recursive else 'off'}",
            f"tmap={request.tmap or 'none'}",
            f"tdat={request.tdat or 'none'}",
            *_extraction_settings(
                extract_options, tmap=request.tmap is not None, tdat=request.tdat is not None
            ),
        )
    )
    if supervision is not None:
        settings += (
            f", max-restarts={supervision.max_restarts}, "
            f"sidecar-timeout={supervision.timeout}s, "
            f"defer={'on' if supervision.defer_if_unavailable else 'off'}"
        )
    return BatchPlan(
        movies=movies,
        out_dir=out_dir,
        log_path=_log_path(request, out_dir),
        settings=settings,
        sidecar=_sidecar_status(supervision),
    )


def run(request: BatchRequest, *, progress: _Progress | None = None) -> BatchResult:
    """Run *request*.

    *progress*, when given, watches the run (#648): it is told the run's setup before the
    first movie is read, gets each structured-log event and is told when each movie starts
    and ends. It is a display, not part of the request, so it has no text form; an
    exception it raises is swallowed and costs no movie a stage.

    Raises [`RequestRefused`][tether.services.base.RequestRefused] before any movie is read
    and before anything is written when the policy, the supervision settings or the
    extraction options are invalid, when a ``tdat`` comes with a ``detection_mode`` or
    ``detection_threshold``, when a ``channel_layout`` comes with a ``tmap``, when a movie
    argument gives no movie, or when two movies would write the same project and no parent
    folder name tells them apart. Raises
    [`CommandFailed`][tether.services.base.CommandFailed] when the output directory or the
    log cannot be written. A failed stage is not an error: it is in the summary.
    """
    from tether.project.batch import BatchLog, run_batch

    extract_options, supervision = _settings(request)
    out_dir = Path(request.out_dir)
    jobs = _jobs(request, out_dir)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise CommandFailed(f"cannot create output directory {out_dir}: {exc}") from exc
    log_path = _log_path(request, out_dir)
    if progress is not None:
        with contextlib.suppress(Exception):
            progress.run_started(
                n_movies=len(jobs),
                out_dir=out_dir,
                policy=request.policy,
                idealize=request.idealize,
            )
    on_event = progress.event if progress is not None else None
    try:
        with BatchLog(path=log_path, on_event=on_event) as log:
            # The movie -> output mapping goes in the log first, so that a renamed output
            # (two movies with one file name) can be traced back to its movie (#653).
            for job in jobs:
                log.event(
                    movie=job.label,
                    stage="input",
                    status="mapped",
                    detail=f"{job.movie_path} -> {job.output_path}",
                )
            summary = run_batch(
                jobs,
                policy=request.policy,
                extract_options=extract_options,
                idealize=request.idealize,
                supervision=supervision,
                overwrite=request.overwrite,
                log=log,
                progress=progress,
            )
    except OSError as exc:
        raise CommandFailed(f"cannot write the batch output or log {log_path}: {exc}") from exc
    return BatchResult(summary=summary, log_path=log_path)
