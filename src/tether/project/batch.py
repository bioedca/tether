# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless batch runner (PRD §6 "Batch", §7.11 FR-BATCH).

Orchestrates the per-movie pipeline — **extract → correct → idealize** — over the
frozen headless functions, isolating each movie (continue-on-error), checkpointing
**per stage** (a resume re-runs only the stages a movie has not completed), and
emitting a structured log plus an end-of-run summary that enumerates every movie's
status and names any failure.

Design (ADR-0030):

* **One ``.tether`` per movie** is the unit of isolation — it mirrors
  [`tether.project.extract.extract_movie`][] (which writes a fresh project per
  movie, atomically) and means a corrupt movie can never damage another's store.
  [`run_batch_into`][] is the one exception (``tether batch --into``, #1151): every movie
  is appended to one project by a guarded staged write (ADR-0117), so a failed append
  still leaves that project as it was, and correct and idealize run once for the project.
* **Checkpoint = provenance presence, with the ADR-0056 extraction exception.**
  Presence normally means "already done" — ``/settings/extraction`` (extract),
  ``/settings/correction`` (correct), a non-empty ``/idealization`` (idealize).
  Under ``policy="fail"``, however, a saved over-gate extraction remains rejected on
  resume; ``overwrite=True`` re-extracts it while holding the destination lock.
  Nothing new is written to the frozen §5 skeleton, so ``schema-guard`` stays green.
* **The correct stage** runs the Appendix-B order photobleach → leakage α →
  γ → corrected-E. γ is skipped when leakage *withholds* the dataset α (an
  intentional "withhold rather than fabricate" outcome, not a failure —
  [`tether.project.gamma.compute_gamma`][] requires a non-sentinel
  ``/molecules.alpha`` and would otherwise raise); the corrected-E pass then degrades
  the missing factors to apparent E, never a NaN factor (PRD §7.2).

Scope — **M3 PR7-A** landed the queue, per-movie isolation, per-stage checkpoint, the
structured log/summary, the warn-vs-fail over-gate policy (PRD §11.2, ADR-0014), and a
``/settings/batch`` provenance stamp. **M3 PR7-B** (this addition, ADR-0031) layers
**sidecar supervision** over the idealize stage via an opt-in ``supervision``
([`tether.idealize.supervisor.SidecarSupervision`][]): a startup liveness probe that
puts the whole run into **idealization-deferred mode** when the sidecar env is
absent/corrupt (extract + correct still run and checkpoint; a later run resumes only the
deferred idealize stage), and **auto-restart up to N** on a transient sidecar failure
(a crash/timeout, not a cleanly-reported fit error), failing **only that movie's**
idealization when the restart budget is spent. With ``supervision=None`` (the default)
the idealize stage stays PR7-A's single, error-isolated call.
"""

from __future__ import annotations

import contextlib
import copy
import json
import logging
import math
import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import IO, TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tether.idealize.supervisor import ProbeResult, SidecarSupervision

__all__ = [
    "STAGE_EXTRACT",
    "STAGE_CORRECT",
    "STAGE_IDEALIZE",
    "STAGES",
    "STATUS_DONE",
    "STATUS_SKIPPED",
    "STATUS_FAILED",
    "STATUS_BLOCKED",
    "STATUS_NOT_REQUESTED",
    "STATUS_WARNING",
    "STATUS_DEFERRED",
    "POLICY_WARN",
    "POLICY_FAIL",
    "POLICIES",
    "MovieJob",
    "StageResult",
    "MovieResult",
    "BatchSummary",
    "BatchLog",
    "BatchProgress",
    "TextProgress",
    "run_batch",
    "run_batch_into",
]

# --- Stage + status + policy vocabulary --------------------------------------

STAGE_EXTRACT = "extract"
STAGE_CORRECT = "correct"
STAGE_IDEALIZE = "idealize"
#: The pipeline stages, in dependency order.
STAGES: tuple[str, ...] = (STAGE_EXTRACT, STAGE_CORRECT, STAGE_IDEALIZE)

STATUS_DONE = "done"  #: the stage ran and completed this pass
STATUS_SKIPPED = "skipped"  #: checkpoint hit — the stage's output already existed
STATUS_FAILED = "failed"  #: the stage raised, or an over-gate movie under ``fail``
STATUS_BLOCKED = "blocked"  #: an upstream stage did not complete, so this could not run
STATUS_NOT_REQUESTED = "not-requested"  #: idealize when ``idealize=False``
STATUS_WARNING = "warning"  #: a non-fatal issue (e.g. provenance stamping failed)
STATUS_DEFERRED = "deferred"  #: idealize skipped this run — sidecar unavailable at startup

#: Statuses that count as "this stage is satisfied" for downstream gating + checkpoint.
_OK_STATUSES = frozenset({STATUS_DONE, STATUS_SKIPPED})

POLICY_WARN = "warn"  #: over-gate registration → accept-with-flag (default; never abort)
POLICY_FAIL = "fail"  #: over-gate registration → fail the movie
#: Valid over-gate batch policies (PRD §11.2 "Over-gate batch policy", ADR-0014).
POLICIES: tuple[str, ...] = (POLICY_WARN, POLICY_FAIL)

# The frozen §5.1 container group and the additive provenance subgroup this writes.
_SETTINGS_GROUP = "settings"
_BATCH_SETTINGS = "batch"
_BATCH_SOURCE = "batch-runner"
#: The ``/settings/batch`` attribute suffix naming the movies a stage covers (#1151).
_COVERED_SUFFIX = "_movies"
_EXTRACTION_SETTINGS = "extraction"
_CORRECTION_SETTINGS = "correction"

_LOG = logging.getLogger("tether.batch")

#: The ``movie`` field of an event about the whole run rather than one movie.
_RUN_LABEL = "(batch)"
#: The marker a stage's ``detail`` carries when it kept a movie with a flag (#648).
_FLAG_MARK = "(flagged)"


def _app_version() -> str:
    """Best-effort Tether version for the provenance stamp (NFR-REPRO)."""
    try:
        from tether import __version__

        return str(__version__)
    except Exception:  # pragma: no cover - defensive; version is normally present
        return "0.0.0+unknown"


# --- Value types -------------------------------------------------------------


@dataclass(frozen=True)
class MovieJob:
    """One input movie and the ``.tether`` it extracts into.

    ``tmap`` / ``tdat`` are optional per-movie Deep-LASI imports (a condition
    typically shares one ``.tmap``); paths are coerced to `Path`.
    """

    movie_path: Path
    output_path: Path
    tmap: Path | None = None
    tdat: Path | None = None
    #: The job's name in the log and the summary; ``None`` is the output's stem. Jobs
    #: that share one output project (``tether batch --into``, #1151) each need one.
    name: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "movie_path", Path(self.movie_path))
        object.__setattr__(self, "output_path", Path(self.output_path))
        if self.tmap is not None:
            object.__setattr__(self, "tmap", Path(self.tmap))
        if self.tdat is not None:
            object.__setattr__(self, "tdat", Path(self.tdat))

    @property
    def label(self) -> str:
        """A short, per-job-unique name for logs/summary.

        Derived from the output ``.tether`` stem rather than the movie file name:
        batch datasets commonly reuse identical movie basenames across condition
        folders, and ``output_path`` is the job's identity (the CLI rejects colliding
        output stems), so this stays unambiguous in the log and end-of-run summary.
        A job that shares its output with others (``--into``) carries its own ``name``.
        """
        return self.name if self.name is not None else self.output_path.stem


@dataclass(frozen=True)
class StageResult:
    """The outcome of one stage for one movie."""

    stage: str
    status: str
    detail: str = ""
    error: str | None = None

    @property
    def ok(self) -> bool:
        """Whether this stage is satisfied (``done`` or ``skipped``)."""
        return self.status in _OK_STATUSES


@dataclass(frozen=True)
class MovieResult:
    """Every stage's outcome for one movie.

    ``donor_side`` is the advisory donor-side check read back from the movie's stored
    extraction provenance (#646, ADR-0070): the check's record from
    ``/settings/extraction`` plus the ``donor_side`` and ``registration_source`` it was
    extracted with, or ``None`` when the movie has no readable record.
    """

    job: MovieJob
    stages: dict[str, StageResult]
    donor_side: dict[str, Any] | None = None

    @property
    def donor_side_swapped(self) -> bool:
        """Whether the donor-side check says this movie's halves look swapped."""
        return bool(self.donor_side) and self.donor_side.get("verdict") == "swapped"

    @property
    def ok(self) -> bool:
        """True when no stage failed (blocked/not-requested do not count as failure)."""
        return not any(s.status == STATUS_FAILED for s in self.stages.values())

    @property
    def failures(self) -> list[StageResult]:
        """The failed stages, for naming in the summary."""
        return [s for s in self.stages.values() if s.status == STATUS_FAILED]

    @property
    def deferred(self) -> list[str]:
        """The names of this movie's ``deferred`` stages, in pipeline order (#647)."""
        return [
            name
            for name in STAGES
            if (sr := self.stages.get(name)) is not None and sr.status == STATUS_DEFERRED
        ]


@dataclass(frozen=True)
class BatchSummary:
    """The end-of-run summary over every movie (PRD §7.11)."""

    results: list[MovieResult]
    policy: str
    idealize_requested: bool

    @property
    def n_movies(self) -> int:
        """Number of movies in the run (one result per queued job)."""
        return len(self.results)

    @property
    def n_ok(self) -> int:
        """Number of movies in which no stage failed."""
        return sum(1 for r in self.results if r.ok)

    @property
    def n_failed(self) -> int:
        """Number of movies with at least one failed stage."""
        return sum(1 for r in self.results if not r.ok)

    @property
    def n_deferred(self) -> int:
        """Number of movies with at least one ``deferred`` stage (#647).

        Only a requested stage can be deferred; one the user did not ask for is
        ``not-requested``. ``tether batch`` exits ``3`` when this is non-zero and no movie
        failed.
        """
        return sum(1 for r in self.results if r.deferred)

    def deferred_by_stage(self) -> dict[str, int]:
        """For each deferred stage, the number of movies it was deferred in (#647)."""
        counts: dict[str, int] = {}
        for name in STAGES:
            n = sum(1 for r in self.results if name in r.deferred)
            if n:
                counts[name] = n
        return counts

    @property
    def donor_side_flagged(self) -> list[MovieResult]:
        """The movies whose donor and acceptor halves look swapped (#646)."""
        return [r for r in self.results if r.donor_side_swapped]

    def format_report(self) -> str:
        """A human-readable summary enumerating every movie + naming any failure."""
        idealize = "on" if self.idealize_requested else "off"
        header = (
            f"Batch run: {self.n_movies} movie(s), {self.n_ok} ok, "
            f"{self.n_failed} failed (policy={self.policy}, idealize={idealize})"
        )
        lines = [header]
        width = max((len(r.job.label) for r in self.results), default=0)
        for r in self.results:
            flag = "ok  " if r.ok else "FAIL"
            stage_bits = []
            for name in STAGES:
                sr = r.stages.get(name)
                if sr is None:
                    continue
                bit = f"{name}={sr.status}"
                if sr.status == STATUS_FAILED and sr.error:
                    bit += f"({sr.error})"
                stage_bits.append(bit)
            if r.donor_side_swapped:
                stage_bits.append("halves-swapped?")
            lines.append(f"  {flag}  {r.job.label:<{width}}  {' '.join(stage_bits)}")
        deferred = self.deferred_by_stage()
        if deferred:
            stages = ", ".join(
                f"{name} in {n} of {self.n_movies} movie(s)" for name, n in deferred.items()
            )
            lines.append(f"Deferred: {stages}; re-run the same command to resume.")
        flagged = self.donor_side_flagged
        if flagged:
            lines.append(
                f"Donor and acceptor halves look swapped in {len(flagged)} movie(s) "
                "(advisory; nothing was changed):"
            )
            for r in flagged:
                lines.append(f"  {r.job.label}: {_describe_donor_side(r.donor_side)}")
        return "\n".join(lines)


def _describe_donor_side(record: dict[str, Any] | None) -> str:
    """The donor-side check's operator sentence for a stored record (#646)."""
    from tether.imaging.side_check import DonorSideCheck  # noqa: PLC0415

    record = dict(record or {})
    donor_side = str(record.pop("extracted_donor_side", "left"))
    imported = record.pop("registration_source", "native") == "imported"
    return DonorSideCheck.from_dict(record).describe(donor_side=donor_side, imported_map=imported)


# --- Structured log ----------------------------------------------------------


class BatchLog:
    """An append-only structured event log (one JSON object per stage transition).

    Records are plain dicts (assertion-friendly in tests, JSONL-serializable). Each is
    mirrored to a `logging` logger as a human line and, if ``path`` is given,
    written as one JSON object per line. Usable as a context manager so the file
    handle is closed on exit.

    ``on_event``, if given, receives a copy of each record after it is stored, so a
    progress display reads the same events as the log (#648). An exception it raises is
    swallowed: a display must never cost a movie its stage.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        logger: logging.Logger | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.records: list[dict[str, Any]] = []
        self._path = Path(path) if path is not None else None
        self._logger = logger if logger is not None else _LOG
        self._on_event = on_event
        self._fh: IO[str] | None = None
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # Append, not truncate: a resumed batch reuses the same log path and its
            # prior stage records are part of the append-only audit trail (§7.11).
            self._fh = self._path.open("a", encoding="utf-8")

    def event(
        self,
        *,
        movie: str,
        stage: str,
        status: str,
        detail: str = "",
        error: str | None = None,
    ) -> dict[str, Any]:
        """Record one stage transition; returns the stored record."""
        record: dict[str, Any] = {
            "movie": movie,
            "stage": stage,
            "status": status,
            "detail": detail,
        }
        if error is not None:
            record["error"] = error
        self.records.append(record)
        if self._fh is not None:
            self._fh.write(json.dumps(record) + "\n")
            self._fh.flush()
        if status == STATUS_FAILED:
            level = logging.ERROR
        elif status == STATUS_WARNING:
            level = logging.WARNING
        else:
            level = logging.INFO
        msg = f"{movie} · {stage}: {status}"
        if error:
            msg += f" — {error}"
        elif detail:
            msg += f" — {detail}"
        self._logger.log(level, msg)
        if self._on_event is not None:
            with contextlib.suppress(Exception):
                self._on_event(dict(record))
        return record

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def __enter__(self) -> BatchLog:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


# --- Progress (#648) ----------------------------------------------------------


class BatchProgress:
    """An observer of a running batch; every method is a no-op until overridden (#648).

    The ``batch`` service command ([`tether.services.batch.run`][]) calls ``run_started``
    once, before the first movie is read, and passes ``event`` to the
    [`BatchLog`][tether.project.batch.BatchLog] as its ``on_event``.
    [`run_batch`][tether.project.batch.run_batch] calls ``movie_started`` before it
    touches a movie and ``movie_finished`` once that movie's result is final. ``index``
    counts from 1. An exception raised here is swallowed, so a display can never stop the
    queue.
    """

    def run_started(
        self, *, n_movies: int, out_dir: str | Path, policy: str, idealize: bool
    ) -> None:
        """The run's setup is known and no movie has been read yet."""

    def event(self, record: dict[str, Any]) -> None:
        """A copy of one structured-log record, after the log stored it."""

    def movie_started(self, index: int, total: int, job: MovieJob) -> None:
        """A movie is about to be processed."""

    def movie_finished(self, index: int, total: int, result: MovieResult) -> None:
        """A movie's stages are all recorded."""


def _format_elapsed(seconds: float) -> str:
    """``3.2s``, ``4m05s`` or ``2h03m``: short enough for one progress line."""
    seconds = max(0.0, seconds)
    if seconds < 60:
        return f"{seconds:.1f}s"
    whole = int(seconds)
    if whole < 3600:
        return f"{whole // 60}m{whole % 60:02d}s"
    return f"{whole // 3600}h{whole % 3600 // 60:02d}m"


class TextProgress(BatchProgress):
    """Plain-text progress lines for ``tether batch`` (#648).

    One line states the run's setup (``run_started``), then one line when each movie
    starts and one when it ends: its position, its name, the result, the elapsed time and
    every stage's status. A stage that kept the movie with a flag, or raised a warning, is
    named on the end line. Events about the whole run (an idealization deferred at
    startup) are always printed. With ``verbose``, every stage event is printed as well.

    The stage text comes from the [`BatchLog`][tether.project.batch.BatchLog] events:
    pass [`event`][tether.project.batch.TextProgress.event] as the log's ``on_event``.
    The lines are plain text with no cursor control, so they read the same in a terminal,
    a redirected file and a scheduler's log.
    """

    def __init__(
        self,
        stream: IO[str],
        *,
        verbose: bool = False,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._stream = stream
        self._verbose = verbose
        self._clock = clock
        self._label: str | None = None
        self._position = ""
        self._started = 0.0
        self._flagged: list[str] = []

    def _write(self, line: str) -> None:
        self._stream.write(line + "\n")
        self._stream.flush()

    def run_started(
        self, *, n_movies: int, out_dir: str | Path, policy: str, idealize: bool
    ) -> None:
        """State the run's setup before the first movie is read.

        This is called outside ``run_batch``, so it isolates its own failure: a closed or
        broken stream must not stop the run from starting.
        """
        state = "on" if idealize else "off"
        with contextlib.suppress(Exception):
            self._write(
                f"tether batch: {n_movies} movie(s) into {out_dir} "
                f"(policy={policy}, idealize={state})"
            )

    def movie_started(self, index: int, total: int, job: MovieJob) -> None:
        """Print the movie's start line and begin timing it."""
        self._label = job.label
        self._position = f"[{index}/{total}]"
        self._started = self._clock()
        self._flagged = []
        self._write(f"{self._position} {job.label}: started")

    def event(self, record: dict[str, Any]) -> None:
        """Take one [`BatchLog`][tether.project.batch.BatchLog] record."""
        movie = str(record.get("movie", ""))
        stage = str(record.get("stage", ""))
        status = str(record.get("status", ""))
        text = record.get("error") or record.get("detail") or ""
        current = self._label is not None and movie == self._label
        flagged = status == STATUS_WARNING or _FLAG_MARK in str(record.get("detail", ""))
        if current and flagged and stage not in self._flagged:
            self._flagged.append(stage)
        if movie == _RUN_LABEL:
            line = f"tether batch: {stage} {status}"
        elif not self._verbose:
            return
        elif current:
            line = f"{self._position} {movie} {stage}: {status}"
        else:
            line = f"{movie} {stage}: {status}"
        self._write(f"{line} - {text}" if text else line)

    def movie_finished(self, index: int, total: int, result: MovieResult) -> None:
        """Print the movie's end line: result, elapsed time and stage statuses."""
        elapsed = _format_elapsed(self._clock() - self._started)
        bits = []
        for name in STAGES:
            sr = result.stages.get(name)
            if sr is None:
                continue
            bit = f"{name}={sr.status}"
            if sr.status == STATUS_FAILED and sr.error:
                bit += f"({sr.error})"
            bits.append(bit)
        stages = " ".join(bits)
        if self._flagged:
            stages += f"; flagged: {', '.join(self._flagged)}"
        outcome = "ok" if result.ok else "FAIL"
        self._write(f"[{index}/{total}] {result.job.label}: {outcome} in {elapsed} ({stages})")
        self._label = None


# --- Checkpoint probes (provenance presence) ---------------------------------


def _group_present(path: Path, group_path: str) -> bool:
    """True when ``group_path`` exists in the ``.tether`` at ``path`` (else False).

    Any error (missing file, not an HDF5 file) reads as "not present" so a stage is
    (re-)attempted rather than falsely skipped.
    """
    if not path.exists():
        return False
    try:
        import h5py  # noqa: PLC0415

        with h5py.File(path, "r") as f:
            return group_path in f
    except Exception:
        return False


def _is_extracted(path: Path) -> bool:
    return _group_present(path, f"/{_SETTINGS_GROUP}/{_EXTRACTION_SETTINGS}")


def _stored_extraction_is_over_gate(path: Path) -> bool:
    """Recompute an existing extraction's over-gate verdict from its saved profile.

    ``extract_movie`` persists both the measured ``registration_rms_px`` and the
    effective ``rms_gate`` in ``/settings/extraction.profile_json``.  A ``fail`` resume
    must consult those values before treating the provenance group as a satisfied
    checkpoint; otherwise the first run's policy rejection disappears on the next run.

    Missing, unreadable, or malformed profile data is not enough evidence to reject a
    checkpoint.  Those stores retain the historical checkpoint behavior and are skipped
    when ``/settings/extraction`` is present.
    """
    if not path.exists():
        return False
    try:
        import h5py  # noqa: PLC0415

        with h5py.File(path, "r") as f:
            raw_profile = f[f"/{_SETTINGS_GROUP}/{_EXTRACTION_SETTINGS}"].attrs.get("profile_json")
        if isinstance(raw_profile, bytes):
            raw_profile = raw_profile.decode("utf-8")
        if not isinstance(raw_profile, str):
            return False
        profile = json.loads(raw_profile)
        if not isinstance(profile, dict):
            return False
        saved_residual = profile["registration_rms_px"]
        saved_gate = profile["rms_gate"]
        if any(
            isinstance(value, bool) or not isinstance(value, (int, float))
            for value in (saved_residual, saved_gate)
        ):
            return False
        residual = float(saved_residual)
        gate = float(saved_gate)
        return bool(
            math.isfinite(residual) and math.isfinite(gate) and gate > 0 and residual > gate
        )
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError, OSError):
        return False


def _stored_donor_side_check(path: Path) -> dict[str, Any] | None:
    """Read the advisory donor-side check back from a saved extraction profile (#646).

    Reading the stored record, rather than the run's return value, gives the same
    answer for a fresh extraction and for a resumed one whose extract stage was skipped.
    A profile with no record (an extraction older than the check), or one that cannot be
    read, gives ``None``: the check is advisory, so a missing record is silence.
    """
    try:
        import h5py  # noqa: PLC0415

        with h5py.File(path, "r") as f:
            raw_profile = f[f"/{_SETTINGS_GROUP}/{_EXTRACTION_SETTINGS}"].attrs.get("profile_json")
        if isinstance(raw_profile, bytes):
            raw_profile = raw_profile.decode("utf-8")
        profile = json.loads(raw_profile) if isinstance(raw_profile, str) else None
        return _donor_side_from_profile(profile)
    except (KeyError, TypeError, ValueError, RecursionError, OSError):
        return None


def _stored_movie_donor_side_check(path: Path, movie_id: str) -> dict[str, Any] | None:
    """The donor-side record of one movie of a several-movie project (#1151).

    Read from that movie's own settings record (``/settings/extraction/movies``,
    ADR-0117), so each appended movie reports its own check. ``None`` as above.
    """
    try:
        from tether.imaging.extract import read_movie_settings  # noqa: PLC0415

        return _donor_side_from_profile(read_movie_settings(path).get(movie_id))
    except (KeyError, TypeError, ValueError, RecursionError, OSError):
        return None


def _donor_side_from_profile(profile: Any) -> dict[str, Any] | None:
    """The donor-side record of an extraction profile, or ``None`` when it has none."""
    record = profile.get("donor_side_check") if isinstance(profile, dict) else None
    if not isinstance(record, dict):
        return None
    return {
        **record,
        "extracted_donor_side": profile.get("donor_side", "left"),
        "registration_source": profile.get("registration_source", "native"),
    }


def _is_corrected(path: Path) -> bool:
    return _group_present(path, f"/{_SETTINGS_GROUP}/{_CORRECTION_SETTINGS}")


def _assert_output_not_newer(path: Path) -> None:
    """Refuse an existing output project written by a **newer** Tether (PRD §5.4).

    The probes above decide what to skip by opening the output ``.tether`` directly, so
    a resume never passes through a guarded entry point: with extract already done its
    stage is skipped, [`tether.imaging.extract.write_extraction`][]'s check never
    runs, and the later stages open the same file ``r+`` and write to it. This is the
    guard for that path.

    Deliberately narrower than [`tether.io.schema.assert_is_compatible_project`][]:
    only a file carrying our ``format`` marker *and* declaring a newer
    ``schema_version`` is refused. A missing, unreadable, foreign or half-written
    project raises nothing, so the probes keep the "(re-)attempt rather than falsely
    skip" behaviour documented on `_group_present` — a crashed run must stay
    resumable, and an incomplete store is a stage to redo, not a movie to fail.

    Checking the marker matters: a foreign HDF5 that happens to carry a
    ``schema_version`` attribute is not a future Tether project, and refusing it here
    would both break the documented ``--overwrite`` path and report it with the wrong
    reason. Falling through hands it to ``write_extraction``, which rejects it
    accurately as "not a .tether project".

    A genuine newer-schema project **is** refused even under ``overwrite=True``. That is
    deliberate: ``--overwrite`` means "redo my extraction", not "discard whatever a newer
    Tether wrote", and re-creating the store would destroy a colleague's work.

    Raises
    ------
    ValueError
        If the file declares a ``schema_version`` newer than this app's. The message is
        [`tether.io.schema.assert_compatible`][]'s, so a refusal reads the same
        wherever the user meets it.
    """
    if not path.exists():
        return
    from tether.io.schema import FORMAT_TAG, assert_compatible  # noqa: PLC0415

    # Everything that can raise on a malformed store stays inside the try; only the
    # deliberate refusal on the last line is allowed to escape.
    try:
        import h5py  # noqa: PLC0415

        with h5py.File(path, "r") as f:
            fmt = f.attrs.get("format")
            raw = f.attrs.get("schema_version")
        if isinstance(fmt, bytes):
            fmt = fmt.decode("utf-8", "replace")
        # The `format` marker is what makes this OUR file. A foreign HDF5 that happens
        # to carry a `schema_version` attribute is not a future Tether project — it
        # falls through to the normal re-attempt/overwrite path, where
        # `write_extraction` refuses it with the accurate "not a .tether project"
        # message instead.
        #
        # Test `isinstance(fmt, str)` FIRST. h5py returns a numpy ARRAY for an
        # array-valued attribute, and `array != str` is an elementwise array whose
        # truthiness raises — that would escape as a refusal and block the very
        # re-attempt path this branch exists to preserve. `numpy.str_` subclasses
        # `str`, so an ordinary scalar marker still compares here.
        if not isinstance(fmt, str) or fmt != FORMAT_TAG or raw is None:
            return
        version = int(raw)
    except Exception:
        return
    assert_compatible(version)


def _is_idealized(path: Path) -> bool:
    """True when ``/idealization`` holds at least one *completed* fitted model.

    Delegates to the public [`tether.project.idealize.list_idealizations`][], which
    already filters out the transient ``{model}.__writing__`` staging group a crashed
    overwrite can leave behind — so a crashed idealization is correctly re-run on resume
    rather than falsely reported done, and the checkpoint does not depend on a private
    idealize symbol. Any read error reads as "not idealized" so the stage is
    re-attempted rather than falsely skipped.
    """
    if not path.exists():
        return False
    try:
        from tether.project.idealize import list_idealizations  # noqa: PLC0415

        return bool(list_idealizations(path))
    except Exception:
        return False


# --- The correct stage (Appendix-B ordered corrections) ----------------------


def run_correct_stage(
    project_path: str | Path,
    *,
    write_guard: Callable[[], None] | None = None,
) -> str:
    """Run the ordered corrections on one extracted ``.tether`` (PRD §7.2, Appendix B).

    photobleach → leakage α → **(γ only when α was applied)** → corrected FRET. γ is
    skipped when leakage *withholds* the dataset α: that is a deliberate
    "withhold rather than fabricate" outcome (fewer than ``min_qualifying_traces``
    donor-only tails), and [`compute_gamma`][tether.project.gamma.compute_gamma] would raise on
    the resulting NaN α-sentinel. The corrected-FRET pass then degrades the missing
    factors to apparent E — never a NaN factor (PRD §7.2, §1.3 invariant 3).

    ``write_guard`` is an optional caller-owned lock/lease check carried into every
    correction writer. Guarded leakage, gamma, and corrected-FRET passes stage their
    complete data-plus-provenance update in a sibling project and publish only after a
    final ownership check; ordinary unguarded callers retain in-place persistence.

    Returns a short human detail string for the log / summary.

    Raises
    ------
    ValueError
        If the project was written by a newer Tether. Each correction below opens the
        store ``r+`` on its own, so the guard belongs here rather than in any one of
        them (PRD §5.4).
    """
    from tether.project.correct import compute_corrected_fret  # noqa: PLC0415
    from tether.project.gamma import compute_gamma  # noqa: PLC0415
    from tether.project.leakage import compute_leakage_alpha  # noqa: PLC0415
    from tether.project.photobleach import compute_photobleach  # noqa: PLC0415

    _assert_output_not_newer(Path(project_path))
    pb = compute_photobleach(project_path, write_guard=write_guard)
    lk = compute_leakage_alpha(project_path, write_guard=write_guard)
    parts = [f"pb {pb.n_donor_bleached}D/{pb.n_acceptor_bleached}A"]
    if lk.applied and lk.alpha is not None:
        gm = compute_gamma(project_path, write_guard=write_guard)
        parts.append(f"α={lk.alpha:.3f}")
        if gm.applied and gm.gamma is not None:
            parts.append(f"γ={gm.gamma:.3f}")
        else:
            parts.append("γ withheld")
    else:
        parts.append("α withheld")
    cf = compute_corrected_fret(project_path, write_guard=write_guard)
    if cf.total_failure and not cf.apparent_e_only:
        parts.append(f"apparent-E fallback ({cf.n_apparent} mol)")
    else:
        parts.append(f"{cf.n_corrected} corrected")
    return "; ".join(parts)


def _idealize_detail(stored: Any) -> str:
    """Best-effort one-line detail from an idealization result (tolerant of stubs)."""
    model = getattr(stored, "model_name", None) or getattr(stored, "model_type", "?")
    nstates = getattr(stored, "nstates", None)
    keys = getattr(stored, "molecule_keys", None)
    bits = [str(model)]
    if nstates is not None:
        bits.append(f"n={nstates}")
    if keys is not None:
        with contextlib.suppress(TypeError):  # pragma: no cover - defensive
            bits.append(f"{len(keys)} mol")
    return " ".join(bits)


# --- Provenance stamp --------------------------------------------------------


def _stamp_under_writer_lock(
    path: Path,
    *,
    ownership_guard: Callable[[], None] | None,
    policy: str,
    idealize_requested: bool,
    stages: dict[str, StageResult],
    covered: dict[str, str] | None = None,
) -> None:
    """Stamp ``/settings/batch`` while this job holds ``path``'s writer lock (ADR-0112).

    The job-level claim supplies the lock when there is one. Otherwise the stamp takes
    [`publish_lock`][tether.project.lock.publish_lock] for just this write, so a writer
    that opened the project after this job published it stops the stamp (a provenance
    warning) instead of having its file rewritten.
    """
    scope: contextlib.AbstractContextManager[Callable[[], None]]
    if ownership_guard is not None:
        scope = contextlib.nullcontext(ownership_guard)
    else:
        from tether.project.lock import publish_lock  # noqa: PLC0415

        scope = publish_lock(path)
    with scope as guard:
        guard()
        _stamp_batch_settings(
            path,
            policy=policy,
            idealize_requested=idealize_requested,
            stages=stages,
            covered=covered,
            write_guard=guard,
        )
        guard()


def _stamp_batch_settings(
    path: Path,
    *,
    policy: str,
    idealize_requested: bool,
    stages: dict[str, StageResult],
    covered: dict[str, str] | None = None,
    write_guard: Callable[[], None] | None = None,
) -> None:
    """Write the additive ``/settings/batch`` provenance group (NFR-REPRO).

    Records the batch policy + app version + per-stage status, and for an ``--into`` run
    the movies each project stage covers (``covered``, written as ``{stage}_movies``
    attributes, #1151). Recomputable (replaced on each run). Additive under the frozen
    ``/settings`` container — ``schema-guard`` stays green (no structural change to the
    §5 skeleton).

    Stamping runs at the end of *every* job, including one whose stages failed, so it
    carries its own newer-schema guard: without it a movie refused above would still be
    written to here. The caller records the refusal as a provenance warning.
    ``write_guard`` revalidates caller-owned lock/lease state before the canonical
    file is opened and immediately before the provenance group is replaced.
    """
    import h5py  # noqa: PLC0415

    _assert_output_not_newer(path)
    if write_guard is not None:
        write_guard()
    with h5py.File(path, "r+") as f:
        settings = f[_SETTINGS_GROUP]
        if write_guard is not None:
            write_guard()
        if _BATCH_SETTINGS in settings:
            del settings[_BATCH_SETTINGS]
        grp = settings.create_group(_BATCH_SETTINGS, track_order=True)
        grp.attrs["app_version"] = _app_version()
        grp.attrs["source"] = _BATCH_SOURCE
        grp.attrs["policy"] = policy
        grp.attrs["idealize_requested"] = bool(idealize_requested)
        grp.attrs["created_utc"] = datetime.now(UTC).isoformat()
        for name in STAGES:
            sr = stages.get(name)
            if sr is not None:
                grp.attrs[f"{name}_status"] = sr.status
        for name, digest in (covered or {}).items():
            grp.attrs[f"{name}{_COVERED_SUFFIX}"] = digest


# --- The runner --------------------------------------------------------------


def _safe_event(log: BatchLog, **kwargs: Any) -> None:
    """Emit a log event, swallowing a failing log sink so the queue is never aborted."""
    with contextlib.suppress(Exception):
        log.event(**kwargs)


def _default_probe(supervision: SidecarSupervision) -> ProbeResult:
    """Run the startup sidecar liveness probe for ``run_batch`` (the default seam)."""
    from tether.idealize.supervisor import probe_sidecar  # noqa: PLC0415

    return probe_sidecar(supervision.sidecar_python, timeout=supervision.probe_timeout)


def _supervise(
    log: BatchLog,
    *,
    idealize: bool,
    idealize_kwargs: dict[str, Any] | None,
    supervision: SidecarSupervision | None,
    probe: Callable[[SidecarSupervision], ProbeResult] | None,
) -> tuple[dict[str, Any], bool]:
    """Set up sidecar supervision; return the idealize kwargs and whether it is deferred.

    A startup liveness probe decides whether to *defer* idealization for the whole run
    when the sidecar env is absent/corrupt (PR7-B); each idealize call is then
    auto-restarted up to N on a transient failure.
    """
    idealize_kwargs = idealize_kwargs or {}
    if not idealize or supervision is None:
        return idealize_kwargs, False
    # Supervision owns the sidecar interpreter + per-call timeout: the startup probe
    # and every idealize call must target the SAME env, so these take precedence
    # over any values in idealize_kwargs.
    idealize_kwargs = {
        **idealize_kwargs,
        "timeout": supervision.timeout,
        "sidecar_python": supervision.sidecar_python,
    }
    if not supervision.defer_if_unavailable:
        return idealize_kwargs, False
    probe_result = (probe if probe is not None else _default_probe)(supervision)
    if probe_result.available:
        return idealize_kwargs, False
    _safe_event(
        log,
        movie=_RUN_LABEL,
        stage=STAGE_IDEALIZE,
        status=STATUS_DEFERRED,
        detail=f"sidecar unavailable — idealization deferred: {probe_result.detail}",
    )
    return idealize_kwargs, True


@dataclass
class _Recorder:
    """Collects a movie's stage results and mirrors each to the log."""

    job: MovieJob
    log: BatchLog
    stages: dict[str, StageResult] = field(default_factory=dict)
    canonical_write_safe: bool = True

    def record(
        self, stage: str, status: str, *, detail: str = "", error: str | None = None
    ) -> StageResult:
        sr = StageResult(stage=stage, status=status, detail=detail, error=error)
        self.stages[stage] = sr
        self.log.event(
            movie=self.job.label,
            stage=stage,
            status=status,
            detail=detail,
            error=error,
        )
        return sr


def _needs_rejected_checkpoint_lock(
    job: MovieJob,
    *,
    policy: str,
    overwrite: bool,
) -> bool:
    """Whether this run may replace a saved policy-rejected extraction checkpoint.

    The caller owns the destination lock while these fallible HDF5 probes run. That
    ordering prevents an unreadable writer-owned project from collapsing to "not a
    checkpoint" and reaching the destructive overwrite runner without ownership.
    """
    return bool(
        overwrite
        and policy == POLICY_FAIL
        and _is_extracted(job.output_path)
        and _stored_extraction_is_over_gate(job.output_path)
    )


def _destination_ownership_guard(
    job: MovieJob,
    rec: _Recorder,
    lock_module: Any,
    owner: Any,
    process_reservation: Any,
) -> Callable[[], None]:
    """Return a nonce check bound to one rejected-checkpoint replacement."""

    def guard() -> None:
        # Fail closed before and after every canonical write boundary. A stale lock
        # may be explicitly stolen during a long runner; comparing the per-acquisition
        # nonce prevents this job from proceeding as if it still owned the destination.
        rec.canonical_write_safe = False
        lock_module.assert_process_reservation(job.output_path, process_reservation)
        current_owner = lock_module.read_lock(job.output_path)
        if current_owner is None or current_owner.nonce != owner.nonce:
            raise lock_module.LockError(
                f"{lock_module.lock_path(job.output_path)} lock ownership changed during "
                "rejected-checkpoint replacement"
            )
        rec.canonical_write_safe = True

    return guard


def run_batch(
    jobs: Sequence[MovieJob],
    *,
    policy: str = POLICY_WARN,
    extract_options: Any = None,
    idealize: bool = True,
    idealize_kwargs: dict[str, Any] | None = None,
    supervision: SidecarSupervision | None = None,
    overwrite: bool = False,
    stamp_provenance: bool = True,
    log: BatchLog | None = None,
    progress: BatchProgress | None = None,
    _extract: Callable[..., Any] | None = None,
    _correct: Callable[..., str] | None = None,
    _idealize: Callable[..., Any] | None = None,
    _probe: Callable[[SidecarSupervision], ProbeResult] | None = None,
) -> BatchSummary:
    """Run the extract → correct → idealize pipeline over ``jobs``, movie-isolated.

    Each movie is processed independently (continue-on-error): a stage that raises is
    recorded ``failed`` and its downstream stages are ``blocked``, but the queue
    continues. Each stage is checkpointed by the provenance it writes, so re-running
    ``run_batch`` over the same jobs re-runs only the stages a movie has not completed.

    Parameters
    ----------
    jobs
        The movies to process (each into its own ``output_path`` ``.tether``).
    policy
        Over-gate registration policy (PRD §11.2): ``"warn"`` (default) keeps an
        over-gate movie with a flag; ``"fail"`` fails it, including on resume when
        the persisted extraction profile remains over its recorded gate.
    extract_options
        A [`tether.project.extract.ExtractOptions`][] (or ``None`` for defaults),
        applied to every movie.
    idealize
        Whether to run the idealize stage (``False`` marks it *not-requested*).
    idealize_kwargs
        Extra keyword arguments forwarded to the idealize runner (e.g. ``model_type``,
        ``sidecar_python``, ``timeout``). When ``supervision`` is given it **owns**
        ``timeout`` and ``sidecar_python`` (so the startup probe and every idealize call
        use the same env/timeout); any values for those two keys here are overridden.
    supervision
        Opt-in sidecar supervision (PR7-B): a
        [`tether.idealize.supervisor.SidecarSupervision`][] enabling a startup
        liveness probe (→ idealization-deferred mode when the sidecar is absent/corrupt)
        and per-movie auto-restart on transient sidecar failures. ``None`` (default)
        keeps the idealize stage a single error-isolated call.
    overwrite
        Re-extract a movie whose ``output_path`` is incomplete, or whose completed
        extraction is rejected by ``policy="fail"``. Accepted completed extractions
        remain skipped via checkpoint.
    stamp_provenance
        Write the additive ``/settings/batch`` provenance group into each project.
    log
        A [`BatchLog`][] sink; a throwaway one is created if omitted.
    progress
        A [`BatchProgress`][tether.project.batch.BatchProgress] told when each movie
        starts and ends (#648). It changes nothing that is recorded.
    _extract, _correct, _idealize
        Injectable stage runners (test seams). Default to
        [`tether.project.extract.extract_movie`][], [`run_correct_stage`][], and
        [`tether.project.idealize.idealize_molecules`][] respectively.
    _probe
        Injectable startup-liveness probe (test seam) taking the ``supervision`` and
        returning a [`ProbeResult`][tether.idealize.supervisor.ProbeResult]. Defaults to a real
        sidecar probe. Only consulted when ``supervision.defer_if_unavailable``.

    Returns
    -------
    BatchSummary
        Per-movie, per-stage outcomes + the end-of-run tallies.

    Raises
    ------
    ValueError
        If ``policy`` is not a recognized over-gate policy.
    """
    if policy not in POLICIES:
        raise ValueError(f"policy must be one of {POLICIES}, got {policy!r}")

    if _extract is None:
        from tether.project.extract import extract_movie  # noqa: PLC0415

        _extract = extract_movie
    if _correct is None:
        _correct = run_correct_stage
    if _idealize is None:
        from tether.project.idealize import idealize_molecules  # noqa: PLC0415

        _idealize = idealize_molecules

    own_log = log is None
    log = log if log is not None else BatchLog()
    idealize_kwargs, idealize_deferred = _supervise(
        log,
        idealize=idealize,
        idealize_kwargs=idealize_kwargs,
        supervision=supervision,
        probe=_probe,
    )

    results: list[MovieResult] = []
    total = len(jobs)
    try:
        for index, job in enumerate(jobs, start=1):
            if progress is not None:
                with contextlib.suppress(Exception):
                    progress.movie_started(index, total, job)
            rec = _Recorder(job=job, log=log)
            destination_lock_module: Any = None
            destination_owner: Any = None
            destination_reservation: Any = None
            destination_lock_acquired = False
            ownership_guard: Callable[[], None] | None = None
            donor_side: dict[str, Any] | None = None
            # Isolate the ENTIRE per-job body — not just each runner call. A failure of
            # the log sink itself (disk full, permission change, share drop) inside a
            # `rec.record()` must not abort the queue; it is caught here so the loop
            # continues to the next movie (§7.11 "isolate each movie").
            try:
                if overwrite and policy == POLICY_FAIL:
                    from tether.project import lock as destination_lock_module  # noqa: PLC0415

                    try:
                        # Claim the destination *before* checking whether its HDF5 file
                        # exists or classifying a checkpoint. A writer may own the
                        # sidecar before creating the project, and the project can appear
                        # immediately after an existence check. The strict claim refuses
                        # even a lock owned by this process: (host, user, pid) identity
                        # alone cannot prove that another thread/GUI writer transferred
                        # its critical section.
                        destination_reservation = (
                            destination_lock_module.acquire_process_reservation(job.output_path)
                        )
                        try:
                            destination_owner = destination_lock_module.acquire(
                                job.output_path,
                                allow_existing_owner=False,
                                process_reservation=destination_reservation,
                            )
                        except Exception:
                            destination_lock_module.release_process_reservation(
                                job.output_path,
                                destination_reservation,
                            )
                            destination_reservation = None
                            raise
                        destination_lock_acquired = True
                        ownership_guard = _destination_ownership_guard(
                            job,
                            rec,
                            destination_lock_module,
                            destination_owner,
                            destination_reservation,
                        )
                        ownership_guard()
                        # Intentionally run and discard this checkpoint classification
                        # under fresh ownership. It surfaces a fallible read here;
                        # _do_extract re-derives the actual ADR-0056 stage decision.
                        _needs_rejected_checkpoint_lock(
                            job,
                            policy=policy,
                            overwrite=overwrite,
                        )
                        ownership_guard()
                    except Exception as exc:
                        # If another writer owns this destination (including an
                        # untransferred lock from this process), fail this movie before
                        # any existence check, fallible probe, or stage can write and
                        # preserve the downstream-blocked result.
                        rec.canonical_write_safe = False
                        extract_ok = rec.record(
                            STAGE_EXTRACT,
                            STATUS_FAILED,
                            error=str(exc),
                        ).ok
                        correct_ok = _do_correct(
                            job,
                            rec,
                            upstream_ok=extract_ok,
                            runner=_correct,
                        )
                        _do_idealize(
                            job,
                            rec,
                            upstream_ok=correct_ok,
                            idealize=idealize,
                            idealize_kwargs=idealize_kwargs,
                            runner=_idealize,
                            supervision=supervision,
                            deferred=idealize_deferred,
                        )
                        continue

                extract_ok = _do_extract(
                    job,
                    rec,
                    policy=policy,
                    options=extract_options,
                    overwrite=overwrite,
                    runner=_extract,
                    ownership_guard=ownership_guard,
                )
                if extract_ok:
                    # Advisory (#646): reading or reporting the check must never cost the
                    # movie its later stages, so any failure here is silence.
                    with contextlib.suppress(Exception):
                        donor_side = _stored_donor_side_check(job.output_path)
                        if donor_side is not None and donor_side.get("verdict") == "swapped":
                            _safe_event(
                                log,
                                movie=job.label,
                                stage="donor-side-check",
                                status=STATUS_WARNING,
                                detail=_describe_donor_side(donor_side),
                            )
                correct_ok = _do_correct(
                    job,
                    rec,
                    upstream_ok=extract_ok,
                    runner=_correct,
                    ownership_guard=ownership_guard,
                )
                _do_idealize(
                    job,
                    rec,
                    upstream_ok=correct_ok,
                    idealize=idealize,
                    idealize_kwargs=idealize_kwargs,
                    runner=_idealize,
                    supervision=supervision,
                    deferred=idealize_deferred,
                    ownership_guard=ownership_guard,
                )
                if stamp_provenance and rec.canonical_write_safe and job.output_path.exists():
                    try:
                        _stamp_under_writer_lock(
                            job.output_path,
                            ownership_guard=ownership_guard,
                            policy=policy,
                            idealize_requested=idealize,
                            stages=rec.stages,
                        )
                    except Exception as exc:  # provenance must never fail a movie
                        _safe_event(
                            log,
                            movie=job.label,
                            stage="provenance",
                            status=STATUS_WARNING,
                            error=str(exc),
                        )
            except Exception as exc:  # a log-sink / infra failure must not kill the queue
                _safe_event(
                    log, movie=job.label, stage="batch", status=STATUS_FAILED, error=str(exc)
                )
            finally:
                try:
                    if destination_lock_acquired:
                        try:
                            released = destination_lock_module.release(
                                job.output_path,
                                destination_owner,
                            )
                            if not released:
                                _safe_event(
                                    log,
                                    movie=job.label,
                                    stage="lock-release",
                                    status=STATUS_WARNING,
                                    error=(
                                        "destination lock ownership changed before cleanup; "
                                        "successor lock preserved"
                                    ),
                                )
                        except Exception as exc:
                            # Cleanup infrastructure must not discard this movie's result
                            # or terminate the remaining queue. Surface the stranded lock.
                            _safe_event(
                                log,
                                movie=job.label,
                                stage="lock-release",
                                status=STATUS_WARNING,
                                error=str(exc),
                            )
                finally:
                    if destination_reservation is not None:
                        try:
                            destination_lock_module.release_process_reservation(
                                job.output_path,
                                destination_reservation,
                            )
                        except Exception as exc:
                            _safe_event(
                                log,
                                movie=job.label,
                                stage="lock-release",
                                status=STATUS_WARNING,
                                error=f"process reservation cleanup failed: {exc}",
                            )
                results.append(MovieResult(job=job, stages=dict(rec.stages), donor_side=donor_side))
                if progress is not None:
                    with contextlib.suppress(Exception):
                        # A deep copy, so an observer that edits its argument (the stages
                        # or the donor-side record) cannot change the result returned.
                        progress.movie_finished(index, total, copy.deepcopy(results[-1]))
    finally:
        if own_log:
            log.close()

    return BatchSummary(results=results, policy=policy, idealize_requested=idealize)


def _do_extract(
    job: MovieJob,
    rec: _Recorder,
    *,
    policy: str,
    options: Any,
    overwrite: bool,
    runner: Callable[..., Any],
    ownership_guard: Callable[[], None] | None = None,
) -> bool:
    """Run (or skip) the extract stage; returns whether it is satisfied."""
    # Before trusting anything already in the output project, refuse one written by a
    # newer Tether. This is the batch pipeline's FIRST touch of an existing store, so
    # failing here fails the movie and leaves correct/idealize `blocked` — the run
    # carries on with the other movies (PRD §5.4, §7.11).
    try:
        if ownership_guard is not None:
            ownership_guard()
        _assert_output_not_newer(job.output_path)
    except Exception as exc:
        return rec.record(STAGE_EXTRACT, STATUS_FAILED, error=str(exc)).ok
    rejected_checkpoint = False
    if _is_extracted(job.output_path):
        rejected_checkpoint = policy == POLICY_FAIL and _stored_extraction_is_over_gate(
            job.output_path
        )
        if rejected_checkpoint and not overwrite:
            return rec.record(
                STAGE_EXTRACT,
                STATUS_FAILED,
                error="registration residual exceeds the gate (policy=fail)",
            ).ok
        if rejected_checkpoint and ownership_guard is None:
            # The checkpoint changed after the job-level preflight. Never overwrite
            # that newly rejected canonical project without first owning its lock.
            rec.canonical_write_safe = False
            return rec.record(
                STAGE_EXTRACT,
                STATUS_FAILED,
                error="rejected extraction checkpoint changed before destination lock; retry",
            ).ok
        if not rejected_checkpoint:
            return rec.record(STAGE_EXTRACT, STATUS_SKIPPED, detail="already extracted").ok
    try:
        # Replacing an existing output needs its writer lock (#346, ADR-0112). The
        # job-level claim supplies it for a rejected checkpoint; any other overwrite of
        # an existing path holds the lock for just this publish.
        publish_scope: contextlib.AbstractContextManager[Callable[[], None] | None]
        publish_scope = contextlib.nullcontext(ownership_guard)
        locked_for_publish = False
        if ownership_guard is None and overwrite and os.path.lexists(job.output_path):
            from tether.project.lock import publish_lock  # noqa: PLC0415

            publish_scope = publish_lock(job.output_path)
            # Until this publish succeeds the job does not own the project, so the
            # unguarded provenance stamp must not write to it (another writer may hold it).
            rec.canonical_write_safe = False
            locked_for_publish = True
        with publish_scope as publish_guard:
            runner_kwargs: dict[str, Any] = {}
            if publish_guard is not None:
                runner_kwargs["publish_guard"] = publish_guard
            summary = runner(
                job.movie_path,
                job.output_path,
                options=options,
                tmap=job.tmap,
                tdat=job.tdat,
                overwrite=overwrite,
                **runner_kwargs,
            )
        if ownership_guard is not None:
            ownership_guard()
    except Exception as exc:  # ExtractionError and any lower-level failure
        if ownership_guard is None:
            # An unguarded attempt may have lost a race to another writer, whose
            # project now sits at the output path; never stamp that one.
            rec.canonical_write_safe = False
        return rec.record(STAGE_EXTRACT, STATUS_FAILED, error=str(exc)).ok
    if locked_for_publish:
        rec.canonical_write_safe = True  # this job published the project just now
    low_conf = bool(getattr(summary, "low_confidence_registration", False))
    if low_conf and policy == POLICY_FAIL:
        return rec.record(
            STAGE_EXTRACT,
            STATUS_FAILED,
            error="registration residual exceeds the gate (policy=fail)",
        ).ok
    n_mol = getattr(summary, "n_molecules", "?")
    detail = f"{n_mol} molecule(s)"
    if low_conf:
        detail += f"; low-confidence registration {_FLAG_MARK}"
    return rec.record(STAGE_EXTRACT, STATUS_DONE, detail=detail).ok


def _do_correct(
    job: MovieJob,
    rec: _Recorder,
    *,
    upstream_ok: bool,
    runner: Callable[..., str],
    ownership_guard: Callable[[], None] | None = None,
    rerun: bool = False,
) -> bool:
    """Run (or skip/block) the correct stage; returns whether it is satisfied.

    ``rerun`` ignores the checkpoint: a project that gained a movie is corrected again
    (``--into``, #1151).
    """
    if not upstream_ok:
        return rec.record(STAGE_CORRECT, STATUS_BLOCKED, detail="extract did not complete").ok
    if not rerun and _is_corrected(job.output_path):
        return rec.record(STAGE_CORRECT, STATUS_SKIPPED, detail="already corrected").ok
    try:
        if ownership_guard is not None:
            ownership_guard()
        runner_kwargs = {}
        if ownership_guard is not None:
            runner_kwargs["write_guard"] = ownership_guard
        detail = runner(job.output_path, **runner_kwargs)
        if ownership_guard is not None:
            ownership_guard()
    except Exception as exc:
        return rec.record(STAGE_CORRECT, STATUS_FAILED, error=str(exc)).ok
    return rec.record(STAGE_CORRECT, STATUS_DONE, detail=detail).ok


def _do_idealize(
    job: MovieJob,
    rec: _Recorder,
    *,
    upstream_ok: bool,
    idealize: bool,
    idealize_kwargs: dict[str, Any],
    runner: Callable[..., Any],
    supervision: SidecarSupervision | None = None,
    deferred: bool = False,
    ownership_guard: Callable[[], None] | None = None,
    rerun: bool = False,
) -> bool:
    """Run (or skip/block/decline/defer) the idealize stage; returns whether it is satisfied.

    ``rerun`` ignores the checkpoint and fits the model again over the stored one: a
    project that gained a movie is idealized again (``--into``, #1151).

    With ``supervision`` set, the runner call is wrapped by
    [`tether.idealize.supervisor.supervise_idealize`][], which auto-restarts a
    transient sidecar failure up to ``supervision.max_restarts`` and re-raises the
    spent budget as ``RestartsExhausted`` (caught here → this movie's idealization
    fails in isolation). ``deferred`` (a failed startup probe) records the stage
    ``deferred`` so a later run resumes it via the per-stage checkpoint.
    """
    if not idealize:
        return rec.record(
            STAGE_IDEALIZE, STATUS_NOT_REQUESTED, detail="idealization not requested"
        ).ok
    if not upstream_ok:
        return rec.record(STAGE_IDEALIZE, STATUS_BLOCKED, detail="correct did not complete").ok
    if not rerun and _is_idealized(job.output_path):
        return rec.record(STAGE_IDEALIZE, STATUS_SKIPPED, detail="already idealized").ok
    if deferred:
        return rec.record(
            STAGE_IDEALIZE,
            STATUS_DEFERRED,
            detail="sidecar unavailable at startup — deferred to a later run",
        ).ok
    try:
        if ownership_guard is not None:
            ownership_guard()
        runner_kwargs = {**idealize_kwargs, "overwrite": True} if rerun else idealize_kwargs
        if ownership_guard is not None:
            # The default idealizer can spend the full sidecar timeout fitting before
            # it opens the canonical project. Carry the nonce check into that actual
            # persistence boundary rather than relying only on this outer call seam.
            runner_kwargs = {**runner_kwargs, "write_guard": ownership_guard}
        if supervision is not None:
            from tether.idealize.supervisor import supervise_idealize  # noqa: PLC0415

            def _on_restart(n: int, exc: Exception) -> None:
                _safe_event(
                    rec.log,
                    movie=job.label,
                    stage=STAGE_IDEALIZE,
                    status=STATUS_WARNING,
                    detail=f"sidecar restart {n}/{supervision.max_restarts}",
                    error=str(exc),
                )

            stored = supervise_idealize(
                runner,
                job.output_path,
                supervision=supervision,
                on_restart=_on_restart,
                **runner_kwargs,
            )
        else:
            stored = runner(job.output_path, **runner_kwargs)
        if ownership_guard is not None:
            ownership_guard()
    except Exception as exc:  # SidecarError / RestartsExhausted / any lower-level failure
        return rec.record(STAGE_IDEALIZE, STATUS_FAILED, error=str(exc)).ok
    return rec.record(STAGE_IDEALIZE, STATUS_DONE, detail=_idealize_detail(stored)).ok


# --- Every movie into one project (--into, #1151) ----------------------------


def run_batch_into(
    jobs: Sequence[MovieJob],
    project: str | Path,
    *,
    policy: str = POLICY_WARN,
    extract_options: Any = None,
    idealize: bool = True,
    idealize_kwargs: dict[str, Any] | None = None,
    supervision: SidecarSupervision | None = None,
    stamp_provenance: bool = True,
    log: BatchLog | None = None,
    progress: BatchProgress | None = None,
    _extract: Callable[..., Any] | None = None,
    _append: Callable[..., Any] | None = None,
    _correct: Callable[..., str] | None = None,
    _idealize: Callable[..., Any] | None = None,
    _probe: Callable[[SidecarSupervision], ProbeResult] | None = None,
) -> BatchSummary:
    """Run the pipeline with every movie of ``jobs`` going into the one ``project`` (#1151).

    The multi-movie form of [`run_batch`][] (PRD §7.11: a run does not split an
    experiment into one file for each movie). Each movie's extract stage appends it to
    ``project`` with [`add_movie_to_project`][tether.project.extract.add_movie_to_project]
    under the project's writer lock (ADR-0117); the first movie creates the project when
    it does not exist yet. Movies stay isolated: a failed append leaves the project as it
    was and the queue goes on.

    The extract checkpoint is the movie's content. A movie whose ``sha256`` is already in
    ``/movies`` is ``skipped``, so a re-run appends no movie twice, and two copies of one
    file in a batch go in once.

    Correction and idealization belong to the project, not to one movie, so they run once,
    after the last append, over every movie in it. Their checkpoint is the one
    [`run_batch`][] uses, except that a run that appended a movie runs both again: the
    dataset α and γ then include the new molecules, and the idealization model is fitted
    again over the stored one. A movie in the project shares the project's correct and
    idealize results in the summary; a movie that is not in it has them ``blocked``.
    Those two stages are logged once, under the run's own label. They run, and the
    project is stamped, under the project's writer lock: a project that another writer
    holds (an open app session) is not touched, and both stages report the lock. Each
    append first marks both stages unfinished in ``/settings/batch``, so a run cut off
    before its stamp leaves the next run knowing they no longer cover every movie.

    Under ``policy="fail"`` a movie whose registration is over the gate is not written at
    all, so it never enters the project. ``overwrite`` has no meaning here: a movie
    already in the project is skipped, never replaced.

    Parameters
    ----------
    jobs
        The movies; every ``output_path`` must be ``project``, and each job needs its own
        ``name`` for the log and the summary.
    project
        The ``.tether`` every movie goes into. It is created by the first movie when it
        does not exist.
    policy, extract_options, idealize, idealize_kwargs, supervision, stamp_provenance, log
        As for [`run_batch`][]. The ``/settings/batch`` stamp is written once, to the
        project.
    progress
        As for [`run_batch`][]; ``movie_finished`` is told after the movie's extract
        stage, and the project's correct and idealize stages reach it as run events.
    _extract, _append, _correct, _idealize, _probe
        Injectable runners (test seams). ``_extract`` creates the project from the first
        movie ([`extract_movie`][tether.project.extract.extract_movie]); ``_append``
        appends each later one
        ([`add_movie_to_project`][tether.project.extract.add_movie_to_project]).

    Returns
    -------
    BatchSummary
        One result for each job, in order.

    Raises
    ------
    ValueError
        If ``policy`` is not a recognized policy, or a job's output is not ``project``
        or it has no ``name``.
    """
    if policy not in POLICIES:
        raise ValueError(f"policy must be one of {POLICIES}, got {policy!r}")
    project = Path(project)
    for job in jobs:
        if job.output_path != project or job.name is None:
            raise ValueError(
                f"every job of an --into run must go to {project} and carry a name; "
                f"got {job.output_path} named {job.name!r}"
            )
    if _extract is None or _append is None:
        from tether.project.extract import add_movie_to_project, extract_movie  # noqa: PLC0415

        _extract = _extract if _extract is not None else extract_movie
        _append = _append if _append is not None else add_movie_to_project
    if _correct is None:
        _correct = run_correct_stage
    if _idealize is None:
        from tether.project.idealize import idealize_molecules  # noqa: PLC0415

        _idealize = idealize_molecules

    own_log = log is None
    log = log if log is not None else BatchLog()
    try:
        idealize_kwargs, idealize_deferred = _supervise(
            log,
            idealize=idealize,
            idealize_kwargs=idealize_kwargs,
            supervision=supervision,
            probe=_probe,
        )
        recorders: list[_Recorder] = []
        donor_sides: list[dict[str, Any] | None] = []
        stamp_safe = True
        total = len(jobs)
        for index, job in enumerate(jobs, start=1):
            if progress is not None:
                with contextlib.suppress(Exception):
                    progress.movie_started(index, total, job)
            rec = _Recorder(job=job, log=log)
            movie_id: str | None = None
            try:
                movie_id = _do_append(
                    job,
                    rec,
                    project,
                    policy=policy,
                    options=extract_options,
                    create=_extract,
                    append=_append,
                )
                stamp_safe = stamp_safe and rec.canonical_write_safe
            except Exception as exc:  # a log-sink / infra failure must not kill the queue
                _safe_event(
                    log, movie=job.label, stage="batch", status=STATUS_FAILED, error=str(exc)
                )
            donor_side = None
            if movie_id is not None:
                # Advisory (#646): any failure to read or report the check is silence.
                with contextlib.suppress(Exception):
                    donor_side = _stored_movie_donor_side_check(project, movie_id)
                    if donor_side is not None and donor_side.get("verdict") == "swapped":
                        _safe_event(
                            log,
                            movie=job.label,
                            stage="donor-side-check",
                            status=STATUS_WARNING,
                            detail=_describe_donor_side(donor_side),
                        )
            recorders.append(rec)
            donor_sides.append(donor_side)
            if progress is not None:
                with contextlib.suppress(Exception):
                    progress.movie_finished(
                        index,
                        total,
                        copy.deepcopy(
                            MovieResult(job=job, stages=dict(rec.stages), donor_side=donor_side)
                        ),
                    )

        _finish_project(
            project,
            recorders,
            log=log,
            policy=policy,
            idealize=idealize,
            idealize_kwargs=idealize_kwargs,
            supervision=supervision,
            deferred=idealize_deferred,
            correct=_correct,
            run_idealize=_idealize,
            stamp=stamp_provenance and stamp_safe,
        )
        results = [
            MovieResult(job=rec.job, stages=dict(rec.stages), donor_side=donor_side)
            for rec, donor_side in zip(recorders, donor_sides, strict=True)
        ]
    finally:
        if own_log:
            log.close()
    return BatchSummary(results=results, policy=policy, idealize_requested=idealize)


def _finish_project(
    project: Path,
    recorders: list[_Recorder],
    *,
    log: BatchLog,
    policy: str,
    idealize: bool,
    idealize_kwargs: dict[str, Any],
    supervision: SidecarSupervision | None,
    deferred: bool,
    correct: Callable[..., str],
    run_idealize: Callable[..., Any],
    stamp: bool,
) -> None:
    """Run the project's correct and idealize stages and stamp it, under its writer lock.

    The lock is held from the first checkpoint read to the stamp (ADR-0112), so a
    project that another writer (an open app session, another append) owns is never
    corrected, idealized or stamped: those stages fail, naming the owner, and the
    movies already appended stay in the project for a later run to finish.
    """
    from tether.project.lock import publish_lock  # noqa: PLC0415

    # Claim the project whenever a movie of the run went into it, whether or not the
    # path is there now: a project that another writer creates or replaces in the
    # meantime is then never read or written by the stages without the lock.
    in_project = any((sr := r.stages.get(STAGE_EXTRACT)) and sr.ok for r in recorders)
    scope: contextlib.AbstractContextManager[Callable[[], None] | None]
    scope = publish_lock(project) if in_project else contextlib.nullcontext()
    try:
        with scope as guard:
            if guard is not None:
                guard()
                if not os.path.lexists(project):
                    raise FileNotFoundError(f"the project {project} is no longer there")
            outcome = _project_stages(
                project,
                recorders,
                log=log,
                idealize=idealize,
                idealize_kwargs=idealize_kwargs,
                supervision=supervision,
                deferred=deferred,
                correct=correct,
                run_idealize=run_idealize,
                ownership_guard=guard,
            )
            if outcome is not None and stamp and guard is not None:
                project_stages, covered = outcome
                try:
                    _stamp_under_writer_lock(
                        project,
                        ownership_guard=guard,
                        policy=policy,
                        idealize_requested=idealize,
                        stages=project_stages,
                        covered=covered,
                    )
                except Exception as exc:  # provenance must never fail a movie
                    _safe_event(
                        log,
                        movie=_RUN_LABEL,
                        stage="provenance",
                        status=STATUS_WARNING,
                        error=str(exc),
                    )
    except Exception as exc:  # a project owned by another writer, unreadable, a log sink
        failed = StageResult(stage=STAGE_CORRECT, status=STATUS_FAILED, error=str(exc))
        after = (
            StageResult(
                stage=STAGE_IDEALIZE, status=STATUS_BLOCKED, detail="correct did not complete"
            )
            if idealize
            else StageResult(
                stage=STAGE_IDEALIZE,
                status=STATUS_NOT_REQUESTED,
                detail="idealization not requested",
            )
        )
        for rec in recorders:
            rec.stages.setdefault(STAGE_CORRECT, failed)
            rec.stages.setdefault(STAGE_IDEALIZE, after)
        _safe_event(log, movie=_RUN_LABEL, stage="batch", status=STATUS_FAILED, error=str(exc))


def _do_append(
    job: MovieJob,
    rec: _Recorder,
    project: Path,
    *,
    policy: str,
    options: Any,
    create: Callable[..., Any],
    append: Callable[..., Any],
) -> str | None:
    """Run (or skip) one movie's extract stage into ``project``; return its ``movie_id``.

    ``None`` means the movie is not in the project (its extract stage failed).
    """
    from tether.project.batch_inputs import movie_in_project  # noqa: PLC0415

    try:
        _assert_output_not_newer(project)
    except Exception as exc:
        rec.record(STAGE_EXTRACT, STATUS_FAILED, error=str(exc))
        return None
    # An unreadable project or movie reads as "not in it": the append then reports why.
    existing = None
    with contextlib.suppress(Exception):
        existing = movie_in_project(project, job.movie_path)
    if existing is not None:
        rec.record(
            STAGE_EXTRACT,
            STATUS_SKIPPED,
            detail=f"already in {project.name} as movie {existing!r}",
        )
        return existing
    refuse = policy == POLICY_FAIL
    creating = not os.path.lexists(project)
    try:
        if creating:
            # A new path is published only while it is still absent, so no lock is
            # needed; a project that appeared meanwhile is refused, never replaced.
            summary = create(
                job.movie_path,
                project,
                options=options,
                tmap=job.tmap,
                tdat=job.tdat,
                refuse_over_gate=refuse,
            )
        else:
            from tether.project.lock import publish_lock  # noqa: PLC0415

            with publish_lock(project) as guard:
                # Mark the project stages unfinished before the append, so a run cut off
                # after it still knows they no longer cover every movie; a refused
                # append puts the marks back as they were.
                earlier = _mark_stages_pending(project, guard)
                try:
                    summary = append(
                        project,
                        job.movie_path,
                        options=options,
                        tmap=job.tmap,
                        tdat=job.tdat,
                        publish_guard=guard,
                        refuse_over_gate=refuse,
                    )
                except Exception:
                    with contextlib.suppress(Exception):
                        _restore_stage_marks(project, earlier, guard)
                    raise
    except Exception as exc:  # ExtractionError, a lost lock, any lower-level failure
        if creating and os.path.lexists(project):
            # An unguarded creation may have lost a race to another writer whose project
            # now sits at the path; the run must not stamp that one.
            rec.canonical_write_safe = False
        rec.record(STAGE_EXTRACT, STATUS_FAILED, error=str(exc))
        return None
    n_mol = getattr(summary, "n_molecules", "?")
    detail = f"{n_mol} molecule(s) " + (
        f"into a new {project.name}" if creating else f"appended to {project.name}"
    )
    if getattr(summary, "low_confidence_registration", False):
        detail += f"; low-confidence registration {_FLAG_MARK}"
    rec.record(STAGE_EXTRACT, STATUS_DONE, detail=detail)
    return str(getattr(summary, "movie_id", "")) or None


_MARKED_STAGES = (STAGE_CORRECT, STAGE_IDEALIZE)


def _mark_stages_pending(project: Path, guard: Callable[[], None]) -> dict[str, str | None]:
    """Stamp the project stages as covering no movie set yet; return the earlier marks.

    Written to ``/settings/batch`` as an empty ``{stage}_movies`` (the unfinished mark
    ``_covered_movies`` reads), which the run's final stamp replaces (#1151).
    """
    import h5py  # noqa: PLC0415

    guard()
    with h5py.File(project, "r+") as f:
        guard()
        grp = f[_SETTINGS_GROUP].require_group(_BATCH_SETTINGS)
        earlier: dict[str, str | None] = {}
        for stage in _MARKED_STAGES:
            name = f"{stage}{_COVERED_SUFFIX}"
            raw = grp.attrs.get(name)
            earlier[name] = None if raw is None else _as_text(raw)
            grp.attrs[name] = ""
    return earlier


def _restore_stage_marks(
    project: Path, earlier: dict[str, str | None], guard: Callable[[], None]
) -> None:
    """Put back the marks ``_mark_stages_pending`` replaced (an append was refused)."""
    import h5py  # noqa: PLC0415

    guard()
    with h5py.File(project, "r+") as f:
        guard()
        grp = f[_SETTINGS_GROUP][_BATCH_SETTINGS]
        for name, value in earlier.items():
            if value is None:
                if name in grp.attrs:
                    del grp.attrs[name]
            else:
                grp.attrs[name] = value
        if not len(grp.attrs) and not len(grp):
            del f[_SETTINGS_GROUP][_BATCH_SETTINGS]


def _as_text(raw: Any) -> str:
    return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)


def _movies_digest(project: Path) -> str:
    """A digest of the content hashes of every movie in ``project`` (#1151)."""
    import hashlib  # noqa: PLC0415

    import h5py  # noqa: PLC0415

    from tether.io.schema import TABLE  # noqa: PLC0415

    with h5py.File(project, "r") as f:
        table = f["movies"][TABLE][:]
    hashes = sorted(
        (v.decode("utf-8") if isinstance(v, bytes) else str(v)).lower() for v in table["sha256"]
    )
    return hashlib.sha256("\n".join(hashes).encode("utf-8")).hexdigest()


def _covered_movies(project: Path, stage: str) -> str | None:
    """The movies digest an earlier ``--into`` run stamped for ``stage``, if any.

    An empty string means that run left the stage unfinished; ``None`` means no
    ``--into`` run stamped it (a project from ``tether extract``, the app or a
    per-movie batch).
    """
    try:
        import h5py  # noqa: PLC0415

        with h5py.File(project, "r") as f:
            grp = f[_SETTINGS_GROUP].get(_BATCH_SETTINGS)
            raw = None if grp is None else grp.attrs.get(f"{stage}{_COVERED_SUFFIX}")
    except Exception:
        return None
    if raw is None:
        return None
    return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)


def _project_stages(
    project: Path,
    recorders: list[_Recorder],
    *,
    log: BatchLog,
    idealize: bool,
    idealize_kwargs: dict[str, Any],
    supervision: SidecarSupervision | None,
    deferred: bool,
    correct: Callable[..., str],
    run_idealize: Callable[..., Any],
    ownership_guard: Callable[[], None] | None = None,
) -> tuple[dict[str, StageResult], dict[str, str]] | None:
    """Correct and idealize ``project`` once, and give each movie's recorder the outcome.

    A stage runs again, over the stored one, when it may not cover every movie now in the
    project: this run appended a movie, or the last ``--into`` stamp records the stage for
    another set of movies or as unfinished. A project that no ``--into`` run has stamped
    keeps the plain checkpoint, so a curated project that gains no movie is not
    recomputed. A correction that ran makes the stored model stale too.

    ``ownership_guard`` checks the project's writer lock, which the caller holds for
    the whole call; both runners carry it to their writes.

    Returns the project's stages and, for each of correct and idealize, the movies
    digest to stamp: the current one when the stage is satisfied, an empty string when it
    is not, and the earlier stamp's when idealization was not asked for and nothing it
    covered changed. A stage skipped on a checkpoint no ``--into`` run stamped gets no
    digest, since that checkpoint may not cover every movie now in the project.
    ``None`` when no movie of the run is in the project (nothing ran).
    """
    in_project = [r for r in recorders if (sr := r.stages.get(STAGE_EXTRACT)) and sr.ok]
    for rec in recorders:
        if rec in in_project:
            continue
        _do_correct(rec.job, rec, upstream_ok=False, runner=correct)
        _do_idealize(
            rec.job,
            rec,
            upstream_ok=False,
            idealize=idealize,
            idealize_kwargs=idealize_kwargs,
            runner=run_idealize,
        )
    if not in_project:
        return None
    appended = any(r.stages[STAGE_EXTRACT].status == STATUS_DONE for r in in_project)
    current = _movies_digest(project)

    def stale(stage: str) -> bool:
        covered = _covered_movies(project, stage)
        return appended or (covered is not None and covered != current)

    project_job = MovieJob(movie_path=project, output_path=project, name=_RUN_LABEL)
    project_rec = _Recorder(job=project_job, log=log)
    correct_ok = _do_correct(
        project_job,
        project_rec,
        upstream_ok=True,
        runner=correct,
        ownership_guard=ownership_guard,
        rerun=stale(STAGE_CORRECT),
    )
    corrected_now = project_rec.stages[STAGE_CORRECT].status == STATUS_DONE
    _do_idealize(
        project_job,
        project_rec,
        upstream_ok=correct_ok,
        idealize=idealize,
        idealize_kwargs=idealize_kwargs,
        runner=run_idealize,
        supervision=supervision,
        deferred=deferred,
        ownership_guard=ownership_guard,
        rerun=corrected_now or stale(STAGE_IDEALIZE),
    )
    for rec in in_project:
        rec.stages[STAGE_CORRECT] = project_rec.stages[STAGE_CORRECT]
        rec.stages[STAGE_IDEALIZE] = project_rec.stages[STAGE_IDEALIZE]
    stages = {
        STAGE_EXTRACT: StageResult(
            stage=STAGE_EXTRACT,
            status=STATUS_DONE if appended else STATUS_SKIPPED,
            detail=f"{len(in_project)} movie(s) of this run in the project",
        ),
        **project_rec.stages,
    }
    covered: dict[str, str] = {}
    for name in (STAGE_CORRECT, STAGE_IDEALIZE):
        previous = _covered_movies(project, name)
        if stages[name].status == STATUS_SKIPPED and previous is None:
            # Skipped on a checkpoint no ``--into`` run stamped: it may predate a movie
            # added since by another route, so the stamp claims no movies for it.
            continue
        if stages[name].ok:
            covered[name] = current
        elif stages[name].status != STATUS_NOT_REQUESTED or appended or corrected_now:
            covered[name] = ""  # unfinished, or a model the project has outgrown
        elif previous is not None:
            covered[name] = previous  # not asked for this time: what it covered stands
    return stages, covered
