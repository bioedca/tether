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
  :func:`tether.project.extract.extract_movie` (which writes a fresh project per
  movie, atomically) and means a corrupt movie can never damage another's store.
* **Checkpoint = provenance presence.** A stage is "already done" when the group it
  writes is present — ``/settings/extraction`` (extract), ``/settings/correction``
  (correct), a non-empty ``/idealization`` (idealize). Nothing new is written to the
  frozen §5 skeleton, so ``schema-guard`` stays green; a resume simply skips the
  stages whose output already exists.
* **The correct stage** runs the Appendix-B order photobleach → leakage α →
  γ → corrected-E. γ is skipped when leakage *withholds* the dataset α (an
  intentional "withhold rather than fabricate" outcome, not a failure —
  :func:`tether.project.gamma.compute_gamma` requires a non-sentinel
  ``/molecules.alpha`` and would otherwise raise); the corrected-E pass then degrades
  the missing factors to apparent E, never a NaN factor (PRD §7.2).

Scope — **M3 PR7-A** landed the queue, per-movie isolation, per-stage checkpoint, the
structured log/summary, the warn-vs-fail over-gate policy (PRD §11.2, ADR-0014), and a
``/settings/batch`` provenance stamp. **M3 PR7-B** (this addition, ADR-0031) layers
**sidecar supervision** over the idealize stage via an opt-in ``supervision``
(:class:`tether.idealize.supervisor.SidecarSupervision`): a startup liveness probe that
puts the whole run into **idealization-deferred mode** when the sidecar env is
absent/corrupt (extract + correct still run and checkpoint; a later run resumes only the
deferred idealize stage), and **auto-restart up to N** on a transient sidecar failure
(a crash/timeout, not a cleanly-reported fit error), failing **only that movie's**
idealization when the restart budget is spent. With ``supervision=None`` (the default)
the idealize stage stays PR7-A's single, error-isolated call.
"""

from __future__ import annotations

import contextlib
import json
import logging
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
    "run_batch",
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
_EXTRACTION_SETTINGS = "extraction"
_CORRECTION_SETTINGS = "correction"

_LOG = logging.getLogger("tether.batch")


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
    typically shares one ``.tmap``); paths are coerced to :class:`~pathlib.Path`.
    """

    movie_path: Path
    output_path: Path
    tmap: Path | None = None
    tdat: Path | None = None

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
        """
        return self.output_path.stem


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
    """Every stage's outcome for one movie."""

    job: MovieJob
    stages: dict[str, StageResult]

    @property
    def ok(self) -> bool:
        """True when no stage failed (blocked/not-requested do not count as failure)."""
        return not any(s.status == STATUS_FAILED for s in self.stages.values())

    @property
    def failures(self) -> list[StageResult]:
        """The failed stages, for naming in the summary."""
        return [s for s in self.stages.values() if s.status == STATUS_FAILED]


@dataclass(frozen=True)
class BatchSummary:
    """The end-of-run summary over every movie (PRD §7.11)."""

    results: list[MovieResult]
    policy: str
    idealize_requested: bool

    @property
    def n_movies(self) -> int:
        return len(self.results)

    @property
    def n_ok(self) -> int:
        return sum(1 for r in self.results if r.ok)

    @property
    def n_failed(self) -> int:
        return sum(1 for r in self.results if not r.ok)

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
            lines.append(f"  {flag}  {r.job.label:<{width}}  {' '.join(stage_bits)}")
        return "\n".join(lines)


# --- Structured log ----------------------------------------------------------


class BatchLog:
    """An append-only structured event log (one JSON object per stage transition).

    Records are plain dicts (assertion-friendly in tests, JSONL-serializable). Each is
    mirrored to a :mod:`logging` logger as a human line and, if ``path`` is given,
    written as one JSON object per line. Usable as a context manager so the file
    handle is closed on exit.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self.records: list[dict[str, Any]] = []
        self._path = Path(path) if path is not None else None
        self._logger = logger if logger is not None else _LOG
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


def _is_corrected(path: Path) -> bool:
    return _group_present(path, f"/{_SETTINGS_GROUP}/{_CORRECTION_SETTINGS}")


def _assert_output_not_newer(path: Path) -> None:
    """Refuse an existing output project written by a **newer** Tether (PRD §5.4).

    The probes above decide what to skip by opening the output ``.tether`` directly, so
    a resume never passes through a guarded entry point: with extract already done its
    stage is skipped, :func:`tether.imaging.extract.write_extraction`'s check never
    runs, and the later stages open the same file ``r+`` and write to it. This is the
    guard for that path.

    Deliberately narrower than :func:`tether.io.schema.assert_is_compatible_project`:
    only a file carrying our ``format`` marker *and* declaring a newer
    ``schema_version`` is refused. A missing, unreadable, foreign or half-written
    project raises nothing, so the probes keep the "(re-)attempt rather than falsely
    skip" behaviour documented on :func:`_group_present` — a crashed run must stay
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
        :func:`tether.io.schema.assert_compatible`'s, so a refusal reads the same
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

    Delegates to the public :func:`tether.project.idealize.list_idealizations`, which
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


def run_correct_stage(project_path: str | Path) -> str:
    """Run the ordered corrections on one extracted ``.tether`` (PRD §7.2, Appendix B).

    photobleach → leakage α → **(γ only when α was applied)** → corrected FRET. γ is
    skipped when leakage *withholds* the dataset α: that is a deliberate
    "withhold rather than fabricate" outcome (fewer than ``min_qualifying_traces``
    donor-only tails), and :func:`~tether.project.gamma.compute_gamma` would raise on
    the resulting NaN α-sentinel. The corrected-FRET pass then degrades the missing
    factors to apparent E — never a NaN factor (PRD §7.2, §1.3 invariant 3).

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
    pb = compute_photobleach(project_path)
    lk = compute_leakage_alpha(project_path)
    parts = [f"pb {pb.n_donor_bleached}D/{pb.n_acceptor_bleached}A"]
    if lk.applied and lk.alpha is not None:
        gm = compute_gamma(project_path)
        parts.append(f"α={lk.alpha:.3f}")
        if gm.applied and gm.gamma is not None:
            parts.append(f"γ={gm.gamma:.3f}")
        else:
            parts.append("γ withheld")
    else:
        parts.append("α withheld")
    cf = compute_corrected_fret(project_path)
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


def _stamp_batch_settings(
    path: Path,
    *,
    policy: str,
    idealize_requested: bool,
    stages: dict[str, StageResult],
) -> None:
    """Write the additive ``/settings/batch`` provenance group (NFR-REPRO).

    Records the batch policy + app version + per-stage status. Recomputable (replaced
    on each run). Additive under the frozen ``/settings`` container — ``schema-guard``
    stays green (no structural change to the §5 skeleton).

    Stamping runs at the end of *every* job, including one whose stages failed, so it
    carries its own newer-schema guard: without it a movie refused above would still be
    written to here. The caller records the refusal as a provenance warning.
    """
    import h5py  # noqa: PLC0415

    _assert_output_not_newer(path)
    with h5py.File(path, "r+") as f:
        settings = f[_SETTINGS_GROUP]
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


# --- The runner --------------------------------------------------------------


def _safe_event(log: BatchLog, **kwargs: Any) -> None:
    """Emit a log event, swallowing a failing log sink so the queue is never aborted."""
    with contextlib.suppress(Exception):
        log.event(**kwargs)


def _default_probe(supervision: SidecarSupervision) -> ProbeResult:
    """Run the startup sidecar liveness probe for ``run_batch`` (the default seam)."""
    from tether.idealize.supervisor import probe_sidecar  # noqa: PLC0415

    return probe_sidecar(supervision.sidecar_python, timeout=supervision.probe_timeout)


@dataclass
class _Recorder:
    """Collects a movie's stage results and mirrors each to the log."""

    job: MovieJob
    log: BatchLog
    stages: dict[str, StageResult] = field(default_factory=dict)

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
    _extract: Callable[..., Any] | None = None,
    _correct: Callable[[str | Path], str] | None = None,
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
        over-gate movie with a flag; ``"fail"`` fails it.
    extract_options
        A :class:`tether.project.extract.ExtractOptions` (or ``None`` for defaults),
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
        :class:`tether.idealize.supervisor.SidecarSupervision` enabling a startup
        liveness probe (→ idealization-deferred mode when the sidecar is absent/corrupt)
        and per-movie auto-restart on transient sidecar failures. ``None`` (default)
        keeps the idealize stage a single error-isolated call.
    overwrite
        Re-extract a movie whose ``output_path`` exists but is not a completed
        extraction (a completed extraction is always skipped via checkpoint).
    stamp_provenance
        Write the additive ``/settings/batch`` provenance group into each project.
    log
        A :class:`BatchLog` sink; a throwaway one is created if omitted.
    _extract, _correct, _idealize
        Injectable stage runners (test seams). Default to
        :func:`tether.project.extract.extract_movie`, :func:`run_correct_stage`, and
        :func:`tether.project.idealize.idealize_molecules` respectively.
    _probe
        Injectable startup-liveness probe (test seam) taking the ``supervision`` and
        returning a :class:`~tether.idealize.supervisor.ProbeResult`. Defaults to a real
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
    idealize_kwargs = idealize_kwargs or {}

    # Sidecar supervision (PR7-B). A startup liveness probe decides whether to *defer*
    # idealization for the whole run when the sidecar env is absent/corrupt; each
    # movie's idealize call is then auto-restarted up to N on a transient failure.
    idealize_deferred = False
    if idealize and supervision is not None:
        # Supervision owns the sidecar interpreter + per-call timeout: the startup probe
        # and every idealize call must target the SAME env, so these take precedence
        # over any values in idealize_kwargs.
        idealize_kwargs = {
            **idealize_kwargs,
            "timeout": supervision.timeout,
            "sidecar_python": supervision.sidecar_python,
        }
        if supervision.defer_if_unavailable:
            probe = _probe if _probe is not None else _default_probe
            probe_result = probe(supervision)
            if not probe_result.available:
                idealize_deferred = True
                _safe_event(
                    log,
                    movie="(batch)",
                    stage=STAGE_IDEALIZE,
                    status=STATUS_DEFERRED,
                    detail=f"sidecar unavailable — idealization deferred: {probe_result.detail}",
                )

    results: list[MovieResult] = []
    try:
        for job in jobs:
            rec = _Recorder(job=job, log=log)
            # Isolate the ENTIRE per-job body — not just each runner call. A failure of
            # the log sink itself (disk full, permission change, share drop) inside a
            # `rec.record()` must not abort the queue; it is caught here so the loop
            # continues to the next movie (§7.11 "isolate each movie").
            try:
                extract_ok = _do_extract(
                    job,
                    rec,
                    policy=policy,
                    options=extract_options,
                    overwrite=overwrite,
                    runner=_extract,
                )
                correct_ok = _do_correct(job, rec, upstream_ok=extract_ok, runner=_correct)
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
                if stamp_provenance and job.output_path.exists():
                    try:
                        _stamp_batch_settings(
                            job.output_path,
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
                results.append(MovieResult(job=job, stages=dict(rec.stages)))
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
) -> bool:
    """Run (or skip) the extract stage; returns whether it is satisfied."""
    # Before trusting anything already in the output project, refuse one written by a
    # newer Tether. This is the batch pipeline's FIRST touch of an existing store, so
    # failing here fails the movie and leaves correct/idealize `blocked` — the run
    # carries on with the other movies (PRD §5.4, §7.11).
    try:
        _assert_output_not_newer(job.output_path)
    except ValueError as exc:
        return rec.record(STAGE_EXTRACT, STATUS_FAILED, error=str(exc)).ok
    if _is_extracted(job.output_path):
        return rec.record(STAGE_EXTRACT, STATUS_SKIPPED, detail="already extracted").ok
    try:
        summary = runner(
            job.movie_path,
            job.output_path,
            options=options,
            tmap=job.tmap,
            tdat=job.tdat,
            overwrite=overwrite,
        )
    except Exception as exc:  # ExtractionError and any lower-level failure
        return rec.record(STAGE_EXTRACT, STATUS_FAILED, error=str(exc)).ok
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
        detail += "; low-confidence registration (flagged)"
    return rec.record(STAGE_EXTRACT, STATUS_DONE, detail=detail).ok


def _do_correct(
    job: MovieJob,
    rec: _Recorder,
    *,
    upstream_ok: bool,
    runner: Callable[[str | Path], str],
) -> bool:
    """Run (or skip/block) the correct stage; returns whether it is satisfied."""
    if not upstream_ok:
        return rec.record(STAGE_CORRECT, STATUS_BLOCKED, detail="extract did not complete").ok
    if _is_corrected(job.output_path):
        return rec.record(STAGE_CORRECT, STATUS_SKIPPED, detail="already corrected").ok
    try:
        detail = runner(job.output_path)
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
) -> bool:
    """Run (or skip/block/decline/defer) the idealize stage; returns whether it is satisfied.

    With ``supervision`` set, the runner call is wrapped by
    :func:`tether.idealize.supervisor.supervise_idealize`, which auto-restarts a
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
    if _is_idealized(job.output_path):
        return rec.record(STAGE_IDEALIZE, STATUS_SKIPPED, detail="already idealized").ok
    if deferred:
        return rec.record(
            STAGE_IDEALIZE,
            STATUS_DEFERRED,
            detail="sidecar unavailable at startup — deferred to a later run",
        ).ok
    try:
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
                **idealize_kwargs,
            )
        else:
            stored = runner(job.output_path, **idealize_kwargs)
    except Exception as exc:  # SidecarError / RestartsExhausted / any lower-level failure
        return rec.record(STAGE_IDEALIZE, STATUS_FAILED, error=str(exc)).ok
    return rec.record(STAGE_IDEALIZE, STATUS_DONE, detail=_idealize_detail(stored)).ok
