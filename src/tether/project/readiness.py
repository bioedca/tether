# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The readiness of each stage of a project: what is complete, what is stale, what is next.

[`read_project_readiness`][] is the one Qt-free function behind ``tether status`` (#896,
PRD §7.13 FR-WORKFLOW-3) and behind the application's view of the same project: the guided
mode (#897), the disabled controls (#898) and the resume (#628) read this record, so the
window and a script cannot disagree on what the next step is.

The read is the one of ``tether info`` (#655): the project is opened read-only and no lock
is taken. It reads [`tether.project.summary.read_project_summary`][] with the stale check,
the stored jobs and stale marks of ADR-0126 ([`tether.project.jobs`][]) and the correction
state of G03 ([`tether.project.correction_states`][]). A stage whose issue has not landed
yet is reported as [`NOT_AVAILABLE_TEXT`][], never as ready or as missing.

[`ProjectReadiness.to_dict`][] is the documented, versioned JSON layout
([`READINESS_FORMAT_VERSION`][]); ``docs/cli.md`` lists its keys.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tether.project.summary import ProjectSummaryError

if TYPE_CHECKING:
    from os import PathLike

    from tether.project.correction_states import CorrectionStatus
    from tether.project.jobs import JobRecord
    from tether.project.summary import ProjectSummary

__all__ = [
    "NOT_AVAILABLE_TEXT",
    "READINESS_FORMAT_VERSION",
    "SECTIONS",
    "STAGES",
    "JobStatus",
    "NextStep",
    "ProjectReadiness",
    "ProjectReadinessError",
    "StaleItem",
    "StageReadiness",
    "StageState",
    "read_project_readiness",
]

#: Version of the `ProjectReadiness.to_dict` layout. A key may be *added* without a bump;
#: renaming or removing one, or changing what a value means, bumps it.
READINESS_FORMAT_VERSION = 1

#: What a stage that a later issue adds says until that issue lands.
NOT_AVAILABLE_TEXT = "not available in this version"

#: The four sections of the workflow (FR-WORKFLOW-1), in order: ``(id, title)``.
SECTIONS: tuple[tuple[str, str], ...] = (
    ("prepare", "Prepare"),
    ("curate", "Curate and correct"),
    ("analyze", "Analyze"),
    ("deposit", "Deposit"),
)

#: Every stage in workflow order: ``(key, name, section id, issue that adds it or None)``.
STAGES: tuple[tuple[str, str, str, int | None], ...] = (
    ("sources", "sources and geometry", "prepare", None),
    ("map", "map", "prepare", None),
    ("traces", "trace generation", "prepare", None),
    ("curation", "curation", "curate", None),
    ("corrections", "corrections", "curate", None),
    ("analysis_sets", "analysis sets", "analyze", 934),
    ("fits", "fits", "analyze", None),
    ("results", "results", "analyze", 943),
    ("deposit", "deposit", "deposit", 956),
)

#: The stage of each node of the dependency graph (ADR-0103), for the stale marks.
_NODE_STAGE = {
    "source_movie": "sources",
    "geometry_recipe": "sources",
    "map": "map",
    "trace_generation": "traces",
    "curation_labels": "curation",
    "analysis_windows": "corrections",
    "calibration_windows": "corrections",
    "factor_version": "corrections",
    "correction_version": "corrections",
    "review_snapshot": "corrections",
    "analysis_set_version": "analysis_sets",
    "fit": "fits",
    "result_snapshot": "results",
    "package_manifest": "deposit",
    "deposit_record": "deposit",
}

#: The stage that the job of each service command (ADR-0100) works on.
_COMMAND_STAGE = {"extract": "traces", "batch": "traces", "idealize": "fits", "map": "map"}

#: The ``nstates_selected_by`` of a fit imported from standalone tMAVEN (#367 handoff).
_IMPORTED = "imported"
_REFIT_ACTION = "refit the stale models (tether idealize PROJECT --stale-only)"

#: What to do next in each correction state of G03; ``""`` where nothing is left to do.
_CORRECTION_ACTIONS = {
    "APPARENT_REVIEW": "finish the initial curation, then confirm the bleedthrough factor α",
    "BLEEDTHROUGH_REQUIRED": "give the bleedthrough factor α a source and confirm it",
    "GAMMA_REVIEW": "review the γ contributors, then approve the factors",
    "CALIBRATION_BLOCKED": "add γ evidence, or give an override with a source",
    "CALIBRATION_APPROVED": "apply the correction version",
    "CORRECTED_REVIEW": "review the corrected traces and windows, then finish the review",
    "ANALYSIS_READY": "",
    "CALIBRATION_STALE": "calculate the factors again: the calibration evidence changed",
}


class ProjectReadinessError(ProjectSummaryError):
    """The project could not be read; the message names the path and the reason."""


class StageState(StrEnum):
    """The state of one stage. The value is the JSON text; [`text`][] the printed one."""

    READY = "ready"
    #: The stage does not apply to this project (an analysis-only import has no movie).
    NOT_NEEDED = "not_needed"
    IN_PROGRESS = "in_progress"
    NOT_STARTED = "not_started"
    STALE = "stale"
    BLOCKED = "blocked"
    #: A job of the stage is running now.
    RUNNING = "running"
    NOT_AVAILABLE = "not_available"

    @property
    def text(self) -> str:
        """The words that the text output prints."""
        if self is StageState.NOT_AVAILABLE:
            return NOT_AVAILABLE_TEXT
        return self.value.replace("_", " ")

    @property
    def done(self) -> bool:
        """Whether the stage needs nothing more: ready, or not needed."""
        return self in (StageState.READY, StageState.NOT_NEEDED)


@dataclass(frozen=True)
class StageReadiness:
    """One stage: its state, a short detail, and what to do when it is not done."""

    key: str
    name: str
    section: str
    state: StageState
    detail: str
    action: str
    #: The issue that adds the stage, for a stage that is not available yet.
    issue: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON form."""
        return {
            "key": self.key,
            "name": self.name,
            "section": self.section,
            "state": self.state.value,
            "detail": self.detail,
            "action": self.action,
            "issue": self.issue,
        }


@dataclass(frozen=True)
class StaleItem:
    """One stale record and the reason it is stale."""

    stage: str
    item: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON form."""
        return {"stage": self.stage, "item": self.item, "reason": self.reason}


@dataclass(frozen=True)
class JobStatus:
    """One stored job (ADR-0126) as a reader sees it."""

    job_id: str
    command: str
    stage: str | None
    state: str
    n_parts: int
    n_completed: int
    error: str

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON form."""
        return {
            "job_id": self.job_id,
            "command": self.command,
            "stage": self.stage,
            "state": self.state,
            "n_parts": self.n_parts,
            "n_completed": self.n_completed,
            "error": self.error,
        }


@dataclass(frozen=True)
class NextStep:
    """The next step: the stage and section to go to, and what to do there."""

    stage: str | None
    section: str | None
    action: str

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON form."""
        return {"stage": self.stage, "section": self.section, "action": self.action}


@dataclass(frozen=True)
class ProjectReadiness:
    """Everything [`read_project_readiness`][] reports for one project."""

    path: str
    stages: tuple[StageReadiness, ...]
    #: The correction state of G03 by name, for example ``"GAMMA_REVIEW"``.
    correction_state: str
    stale: tuple[StaleItem, ...]
    jobs: tuple[JobStatus, ...]
    next_step: NextStep

    def stage(self, key: str) -> StageReadiness:
        """Return the stage ``key``; raise ``KeyError`` for a name that is not a stage."""
        for stage in self.stages:
            if stage.key == key:
                return stage
        raise KeyError(f"no stage {key!r}")

    def to_dict(self) -> dict[str, Any]:
        """Return the documented JSON layout of one project (``docs/cli.md``)."""
        return {
            "path": self.path,
            "stages": [s.to_dict() for s in self.stages],
            "correction_state": self.correction_state,
            "stale": [s.to_dict() for s in self.stale],
            "jobs": [j.to_dict() for j in self.jobs],
            "next_step": self.next_step.to_dict(),
        }


def _node_name(graph: Any, key: str) -> str:
    """Return the name of a dependency-graph node, or the key of a node it does not know."""
    try:
        return str(graph.node(key).name)
    except KeyError:
        return key


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _text(value: object) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _store_facts(path: Path) -> tuple[bool, int, int, frozenset[str]]:
    """Read the analysis-only flag, the movies with a map (and low), and the imported fits.

    A fit imported from standalone tMAVEN is one ``--stale-only`` never refits.
    """
    import h5py  # noqa: PLC0415

    with h5py.File(path, "r") as f:
        analysis_only = "analysis_only" in f["settings"]
        calibration = f["calibration"]
        rows = f["movies"]["table"].fields("calibration_id")[:]
        ids = [_text(v) for v in rows]
        mapped = [i for i in ids if i and i in calibration]
        low = sum(bool(calibration[i].attrs.get("low_confidence", False)) for i in set(mapped))
        models = f.get("idealization")
        imported = frozenset(
            name
            for name in (models if models is not None else ())
            if _text(models[name].attrs.get("nstates_selected_by", "")) == _IMPORTED
        )
    return analysis_only, len(mapped), low, imported


def _prepare_stages(
    summary: ProjectSummary, analysis_only: bool, n_mapped: int, n_low: int
) -> dict[str, tuple[StageState, str, str]]:
    n_movies = len(summary.movies)
    out: dict[str, tuple[StageState, str, str]] = {}
    if analysis_only:
        skipped = (StageState.NOT_NEEDED, "the traces were imported (analysis-only)", "")
        out["sources"] = out["map"] = skipped
    elif n_movies == 0:
        add = "extract a movie into the project (tether extract MOVIE -o PROJECT)"
        out["sources"] = (StageState.NOT_STARTED, "no movie", add)
        out["map"] = (StageState.NOT_STARTED, "no movie", add)
    else:
        out["sources"] = (StageState.READY, _plural(n_movies, "movie"), "")
        low = f", {n_low} low-confidence" if n_low else ""
        if n_mapped >= n_movies:
            out["map"] = (StageState.READY, f"{_plural(n_mapped, 'movie')} with a map{low}", "")
        else:
            out["map"] = (
                StageState.IN_PROGRESS,
                f"{n_movies - n_mapped} of {n_movies} movies have no map",
                "extract each movie with a map (tether extract --map)",
            )
    if summary.n_molecules:
        out["traces"] = (StageState.READY, _plural(summary.n_molecules, "molecule"), "")
    else:
        out["traces"] = (
            StageState.NOT_STARTED,
            "no molecules",
            "extract the traces of a movie (tether extract MOVIE -o PROJECT)",
        )
    return out


def _curation_stage(
    summary: ProjectSummary, initial_curation_finished: bool
) -> tuple[StageState, str, str]:
    counts = summary.curation
    detail = f"{counts.accepted} accepted, {counts.rejected} rejected, {counts.undecided} undecided"
    if summary.n_molecules == 0:
        return StageState.NOT_STARTED, "no molecules", "extract the traces first"
    if initial_curation_finished or counts.undecided == 0:
        return StageState.READY, detail, ""
    action = "accept or reject the undecided molecules"
    if counts.undecided == summary.n_molecules:
        return StageState.NOT_STARTED, detail, action
    return StageState.IN_PROGRESS, detail, action


def _corrections_stage(status: CorrectionStatus) -> tuple[StageState, str, str]:
    name = status.state.value
    action = _CORRECTION_ACTIONS[name]
    detail = f"correction state {name}"
    if name == "ANALYSIS_READY":
        return StageState.READY, detail, ""
    if name == "CALIBRATION_STALE":
        return StageState.STALE, detail, action
    if name == "CALIBRATION_BLOCKED":
        return StageState.BLOCKED, detail, action
    if name == "APPARENT_REVIEW" and not status.stored:
        return StageState.NOT_STARTED, detail, action
    return StageState.IN_PROGRESS, detail, action


def _fits_stage(summary: ProjectSummary, imported: frozenset[str]) -> tuple[StageState, str, str]:
    models = summary.idealizations
    if not models:
        return StageState.NOT_STARTED, "no fit", "fit the molecules (tether idealize PROJECT)"
    detail = _plural(len(models), "model")
    stale = [m.name for m in models if m.stale]
    if not stale:
        return StageState.READY, detail, ""
    actions = []
    if any(name not in imported for name in stale):
        actions.append(_REFIT_ACTION)
    from_tmaven = [name for name in stale if name in imported]
    if from_tmaven:
        actions.append(
            f"hand the molecules of {', '.join(from_tmaven)} to tMAVEN again and import "
            "the new fit (--stale-only never refits a fit imported from tMAVEN)"
        )
    return StageState.STALE, detail, ", then ".join(actions)


def _job_status(job: JobRecord) -> JobStatus:
    return JobStatus(
        job_id=job.job_id,
        command=job.command,
        stage=_COMMAND_STAGE.get(job.command),
        state=job.state.value,
        n_parts=len(job.parts),
        n_completed=len(job.completed),
        error=job.error,
    )


def _jobs_to_retry(jobs: tuple[JobStatus, ...]) -> list[JobStatus]:
    """Failed or interrupted jobs that no later job of the same command finished."""
    pending: list[JobStatus] = []
    for i, job in enumerate(jobs):
        if job.state not in ("failed", "interrupted"):
            continue
        if any(later.command == job.command and later.state == "done" for later in jobs[i + 1 :]):
            continue
        pending.append(job)
    return pending


def _outstanding(marks: list[Any], recorded: list[Any]) -> list[Any]:
    """Keep the stale marks whose record has not been made again (Codex review of #1273).

    Marks are never removed (ADR-0126), and a changed record is a new instance. A mark on
    ``(node, instance)`` is therefore superseded once an unmarked instance of the same node
    was made from the record the mark came through: from that same upstream instance at an
    identity other than the one the marked record holds, or from an instance that in its turn
    replaced that upstream record.
    """
    first: dict[tuple[str, str], Any] = {}
    for m in marks:
        first.setdefault((m.node, m.instance), m)
    identity_of = {
        (r.node, r.instance, r.upstream_node, r.upstream_instance): r.identity for r in recorded
    }

    def replacements(node: str, instance: str, seen: frozenset = frozenset()) -> set[str]:
        mark = first.get((node, instance))
        if mark is None or (node, instance) in seen:
            return set()
        up = (mark.upstream_node, mark.upstream_instance)
        old = identity_of.get((node, instance, *up))
        up_new = replacements(*up, seen | {(node, instance)})
        return {
            r.instance
            for r in recorded
            if r.node == node
            and r.instance != instance
            and (r.node, r.instance) not in first
            and r.upstream_node == up[0]
            and (
                (r.upstream_instance == up[1] and r.identity != old)
                or r.upstream_instance in up_new
            )
        }

    return [m for m in marks if not replacements(m.node, m.instance)]


def _next_step(stages: tuple[StageReadiness, ...], jobs: tuple[JobStatus, ...]) -> NextStep:
    """Choose the next step: a job to retry, a running job, a stale stage, a stage not done."""
    section_of = {s.key: s.section for s in stages}
    retry = _jobs_to_retry(jobs)
    if retry:
        job = retry[0]
        return NextStep(
            job.stage,
            section_of.get(job.stage or ""),
            f"retry the {job.state} {job.command} job {job.job_id[:8]} "
            f"({job.n_completed} of {job.n_parts} parts are complete)",
        )
    running = [j for j in jobs if j.state == "running"]
    if running:  # never send the user to start what a job is doing now
        job = running[0]
        return NextStep(
            job.stage,
            section_of.get(job.stage or ""),
            f"wait for the running {job.command} job {job.job_id[:8]}",
        )
    open_stages = [
        s for s in stages if s.state is not StageState.NOT_AVAILABLE and not s.state.done
    ]
    stale = [s for s in open_stages if s.state is StageState.STALE]
    for stage in stale or open_stages:
        return NextStep(stage.key, stage.section, stage.action)
    return NextStep(None, None, "nothing: every stage available in this version is ready")


def read_project_readiness(path: str | PathLike[str]) -> ProjectReadiness:
    """Return the readiness of each stage of the project at ``path`` (a read, no lock).

    Raises [`ProjectReadinessError`][] when the project cannot be read: the reasons of
    [`read_project_summary`][tether.project.summary.read_project_summary], or a job, mark
    or correction table that cannot be read.
    """
    from tether.project import correction_states, jobs  # noqa: PLC0415
    from tether.project import dependency_graph as graph  # noqa: PLC0415
    from tether.project.summary import read_project_summary  # noqa: PLC0415

    given = os.fspath(path)
    try:
        summary = read_project_summary(path, check_stale=True)
    except ProjectSummaryError as exc:
        raise ProjectReadinessError(str(exc)) from exc
    store = Path(path)
    try:
        analysis_only, n_mapped, n_low, imported = _store_facts(store)
        correction = correction_states.read_correction_state(store)
        finished = any(
            t.event is correction_states.CorrectionEvent.FINISH_INITIAL_CURATION
            for t in correction_states.read_correction_transitions(store)
        )
        job_records = tuple(_job_status(j) for j in jobs.read_jobs(store))
        marks = _outstanding(jobs.read_stale_marks(store), jobs.read_recorded_inputs(store))
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise ProjectReadinessError(f"{store}: could not be read ({exc})") from exc

    stale: list[StaleItem] = []
    events = {e.number: e.event for e in graph.EDGES}
    for mark in marks:
        stale.append(
            StaleItem(
                _NODE_STAGE.get(mark.node, mark.node),
                f"{_node_name(graph, mark.node)} {mark.instance}",
                f"{events.get(mark.edge, mark.event)} "
                f"({_node_name(graph, mark.edited_node)} {mark.edited_instance} changed)",
            )
        )
    for model in summary.idealizations:
        if model.stale:
            stale.append(
                StaleItem(
                    "fits",
                    f"fit {model.name}",
                    f"{model.stale} of {model.n_molecules} molecules changed since the fit "
                    "(their traces, window or correction factors)",
                )
            )
    if correction.state is correction_states.CorrectionState.CALIBRATION_STALE:
        stale.append(
            StaleItem("corrections", "correction state", "the calibration evidence changed")
        )

    computed = _prepare_stages(summary, analysis_only, n_mapped, n_low)
    computed["curation"] = _curation_stage(summary, finished)
    computed["corrections"] = _corrections_stage(correction)
    computed["fits"] = _fits_stage(summary, imported)
    stale_stages = {s.stage for s in stale}
    running = {j.stage for j in job_records if j.state == "running"}
    stages: list[StageReadiness] = []
    for key, name, section, issue in STAGES:
        if issue is not None:
            stages.append(
                StageReadiness(key, name, section, StageState.NOT_AVAILABLE, f"#{issue}", "", issue)
            )
            continue
        state, detail, action = computed[key]
        if key in running:
            state = StageState.RUNNING
            job = next(j for j in job_records if j.stage == key and j.state == "running")
            action = f"wait for the running {job.command} job {job.job_id[:8]}"
        elif key in stale_stages and state is not StageState.NOT_NEEDED:
            state = StageState.STALE
            action = action or f"make the stale records of {name} again (listed under stale)"
        stages.append(StageReadiness(key, name, section, state, detail, action))
    stage_tuple = tuple(stages)
    return ProjectReadiness(
        path=given,
        stages=stage_tuple,
        correction_state=correction.state.value,
        stale=tuple(stale),
        jobs=job_records,
        next_step=_next_step(stage_tuple, job_records),
    )
