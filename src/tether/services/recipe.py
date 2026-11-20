# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Recipe files: a list of service requests that ``tether run`` runs as jobs (#899).

A **recipe** is the file form of ADR-0100 §6, a UTF-8 JSON object::

    {"format": "tether-recipe", "version": 1, "steps": [<request>, <request>]}

Each step is the text form of one service request, and may carry a ``policy`` for an
approval step that its command reaches (ADR-0102 §3). [`read_recipe`][] checks the whole
file before anything runs: the format and version, each request, each policy (a policy
states every value, so it has no hidden default), no publish step (card B3), no step that
approves for a person, and no field that holds an account secret. A relative path in a
step is read from the folder of the recipe file.

[`run_recipe`][] runs the steps in order, each through its service command and each as a
stored job (ADR-0126) in the project the step writes. The job's one part names the recipe
by its SHA-256 and the step by its number. A per-user checkpoint marks the completed
prefix of the latest run, including steps that write no project.
A command that reaches an approval step asks an [`ApprovalGate`][]: an approval already
recorded lets it go on, a policy is the command's to apply, and with neither the run
stops with [`ApprovalStop`][] (exit code ``4`` of ``tether run``). [`recipe_from_project`][]
writes the requests of a project's completed jobs back as a recipe (**Save as recipe**).

This module imports only the standard library and other service modules; the core is
imported when a step runs.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import os
from collections.abc import Callable, Mapping, Sequence
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from uuid import uuid4

from tether.services.base import (
    Request,
    RequestError,
    ServiceError,
    request_from_mapping,
    request_to_mapping,
)
from tether.services.extract import ExtractRequest

__all__ = [
    "APPROVAL_STEPS",
    "RECIPE_FORMAT",
    "RECIPE_VERSION",
    "ApprovalGate",
    "ApprovalStop",
    "Policy",
    "Recipe",
    "RecipeError",
    "RecipeRun",
    "RecipeStep",
    "StepOutcome",
    "check_policy",
    "plan_recipe",
    "read_recipe",
    "recipe_from_project",
    "recipe_from_text",
    "recipe_part",
    "recipe_to_text",
    "run_recipe",
    "stopped_recipe",
    "write_recipe",
]

#: The ``format`` key of a recipe file.
RECIPE_FORMAT = "tether-recipe"
#: The version of the recipe file format that this Tether reads and writes.
RECIPE_VERSION = 1

#: Words that a field name of an account secret holds. A request has no such field; a
#: recipe that names one is refused, so a recipe never carries a secret.
_SECRET_WORDS = ("password", "passwd", "secret", "token", "api_key", "apikey", "credential")


class RecipeError(RequestError):
    """A recipe file is not a valid recipe; the message names the step and the reason."""


class ApprovalStop(ServiceError):
    """A run came to an approval step that has no policy and no recorded approval.

    ``project`` is the project that a person approves in, ``report`` the evidence a command
    wrote for the person (``""`` when it wrote none). A command whose policy says
    ``otherwise: stop`` raises it too, with a ``reason`` that says what the rule found.
    """

    def __init__(
        self,
        step: str,
        subject: str,
        *,
        project: str,
        report: str = "",
        reason: str = "the recipe has no policy for it",
    ) -> None:
        self.step = step
        self.subject = subject
        self.project = project
        self.report = report
        super().__init__(f"stopped at approval step {step!r} for {subject!r}: {reason}")


# --- policies (ADR-0102 §3) -------------------------------------------------------------------


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _positive(value: Any) -> bool:
    return _finite(value) and value > 0


def _count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _range(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2
        and all(_positive(v) for v in value)
        and value[0] < value[1]
    )


def _one_of(*choices: str) -> Callable[[Any], bool]:
    def check(value: Any) -> bool:
        return value in choices

    check.__doc__ = " or ".join(repr(c) for c in choices)
    return check


_CHECK_TEXT = {
    _finite: "a finite number",
    _positive: "a number above 0",
    _count: "a whole number of at least 1",
    _text: "a text that is not empty",
    _range: "a list of two numbers above 0, the first below the second",
}

#: For each approval step: its rules with the value each rule needs, and its ``otherwise``.
_POLICIES: dict[str, tuple[dict[str, dict[str, Callable[[Any], bool]]], tuple[str, ...]]] = {
    "map.accept": (
        {"rms-gate": {"gate_px": _positive, "no_residual": _one_of("approve", "stop")}},
        ("flag", "fail", "stop"),
    ),
    "alpha.confirm": (
        {
            "estimate": {"min_qualifying_traces": _count},
            "value": {"alpha": _finite, "source": _text},
        },
        ("apparent", "fail", "stop"),
    ),
    "gamma.approve": (
        {
            "estimate": {"min_qualifying_traces": _count, "range": _range},
            "value": {"gamma": _positive, "source": _text},
        },
        ("apparent", "fail", "stop"),
    ),
    "fit.select": (
        {
            "best-score": {"family": _text, "score": _text},
            "model": {"family": _text, "n_states": _count},
        },
        ("fail", "stop"),
    ),
}

#: The four approval steps of ADR-0102 §1, the ones a policy may name. The store keeps the
#: same tuple ([`tether.project.approvals.APPROVAL_STEPS`][]), and a test compares them.
APPROVAL_STEPS: tuple[str, ...] = tuple(_POLICIES)

#: Model families that have no score, so ``best-score`` cannot rank them (ADR-0102 §3).
_UNSCORED_FAMILIES = frozenset({"kmeans", "k-means"})


@dataclasses.dataclass(frozen=True)
class Policy:
    """A checked recipe policy for one approval step.

    ``text`` is the policy as the recipe gives it, in the one-line JSON form; it is the
    text the store records with an approval by this policy.
    """

    step: str
    rule: str
    otherwise: str
    values: Mapping[str, Any]
    text: str


def check_policy(data: Any) -> Policy:
    """Check a recipe ``policy`` object (ADR-0102 §3); raise [`RecipeError`][] if not valid."""
    if not isinstance(data, dict):
        raise RecipeError("a policy must be a JSON object")
    step = data.get("step")
    if not isinstance(step, str) or step not in _POLICIES:
        raise RecipeError(f"a policy names an unknown approval step {step!r}")
    rules, otherwise = _POLICIES[step]
    rule = data.get("rule")
    if not isinstance(rule, str) or rule not in rules:
        raise RecipeError(f"the {step} policy has no rule {rule!r} (rules: {', '.join(rules)})")
    needed = rules[rule]
    expected = {"step", "rule", "otherwise", *needed}
    missing = sorted(expected - set(data))
    if missing:
        raise RecipeError(
            f"the {step} policy {rule!r} must state every value; missing: {', '.join(missing)}"
        )
    unknown = sorted(set(data) - expected)
    if unknown:
        raise RecipeError(f"the {step} policy {rule!r} has unknown key(s): {', '.join(unknown)}")
    for name, check in needed.items():
        if not check(data[name]):
            wanted = _CHECK_TEXT.get(check) or check.__doc__
            raise RecipeError(f"the {step} policy value {name!r} must be {wanted}")
    if data["otherwise"] not in otherwise:
        raise RecipeError(
            f"the {step} policy 'otherwise' must be one of {', '.join(otherwise)}, "
            f"not {data['otherwise']!r}"
        )
    if rule == "best-score" and str(data["family"]).lower() in _UNSCORED_FAMILIES:
        raise RecipeError(
            f"'best-score' cannot rank the family {data['family']!r}: it has no score"
        )
    text = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    values = {k: data[k] for k in needed}
    return Policy(step=step, rule=rule, otherwise=data["otherwise"], values=values, text=text)


# --- the file ---------------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class RecipeStep:
    """One step of a recipe: its number (from 1), its request and its policy, if any."""

    number: int
    request: Request
    policy: Policy | None = None


@dataclasses.dataclass(frozen=True)
class Recipe:
    """A checked recipe: its identity (``sha256`` of the file text, ``version``) and steps.

    The paths of each request are already read from ``folder``, the folder of the file.
    ``path`` identifies the source file when the recipe was read from disk.
    """

    folder: Path
    sha256: str
    version: int
    steps: tuple[RecipeStep, ...]
    path: Path | None = None


def _registry():  # noqa: ANN202 - the registry module
    # The registry imports the command modules, which import this one through approve.
    from tether.services import registry  # noqa: PLC0415

    return registry


def _resolve(value: Any, folder: Path) -> Any:
    if isinstance(value, str) and value and not os.path.isabs(value):
        return os.path.normpath(os.path.join(folder, value))
    if isinstance(value, tuple):
        return tuple(_resolve(v, folder) for v in value)
    return value


def _relative(value: Any, folder: Path) -> Any:
    if isinstance(value, str) and value and os.path.isabs(value):
        try:
            return Path(os.path.relpath(value, folder)).as_posix()
        except ValueError:  # another drive on Windows: keep the full path
            return value
    if isinstance(value, tuple):
        return tuple(_relative(v, folder) for v in value)
    return value


def _with_paths(request: Request, change: Callable[[Any, Path], Any], folder: Path) -> Request:
    fields = {name: change(getattr(request, name), folder) for name in request.PATH_FIELDS}
    return dataclasses.replace(request, **fields) if fields else request


def _check_step(number: int, data: Any, folder: Path) -> RecipeStep:
    where = f"step {number}"
    if not isinstance(data, dict):
        raise RecipeError(f"{where}: a step must be a JSON object")
    name = data.get("command")
    if isinstance(name, str) and "publish" in name.lower():
        raise RecipeError(
            f"{where}: a recipe can never publish (card B3); {name!r} is refused. A recipe "
            "may make and upload a deposit draft; publish it with its own command."
        )
    if name == "approve":
        raise RecipeError(
            f"{where}: a recipe cannot approve for a person; name a policy for the step, or "
            "let the run stop and approve with tether approve"
        )
    fields = data.get("fields")
    if isinstance(fields, dict):
        for key in fields:
            if any(word in key.lower() for word in _SECRET_WORDS):
                raise RecipeError(f"{where}: a recipe holds no account secret; field {key!r}")
    registry = _registry()
    try:
        entry = registry.command(name) if isinstance(name, str) else None
    except KeyError:
        entry = None
    if entry is None:
        known = ", ".join(c.name for c in registry.COMMANDS if c.name != "approve")
        raise RecipeError(f"{where}: unknown command {name!r} (known: {known})")
    request_data = {k: v for k, v in data.items() if k != "policy"}
    try:
        request = request_from_mapping(request_data, entry.request_type)
    except RequestError as exc:
        raise RecipeError(f"{where}: {exc}") from exc
    policy = None
    if "policy" in data:
        try:
            policy = check_policy(data["policy"])
        except RecipeError as exc:
            raise RecipeError(f"{where}: {exc}") from exc
        if policy.step not in entry.approval_steps:
            reach = ", ".join(entry.approval_steps) or "no approval step"
            raise RecipeError(
                f"{where}: the {name!r} command reaches {reach}, so a policy for "
                f"{policy.step!r} has nothing to apply to"
            )
    return RecipeStep(number, _with_paths(request, _resolve, folder), policy)


def recipe_from_text(text: str, *, folder: str | Path) -> Recipe:
    """Read and check a recipe from its text; relative paths are read from ``folder``."""
    folder = Path(folder)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RecipeError(f"a recipe is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise RecipeError("a recipe must be a JSON object")
    if data.get("format") != RECIPE_FORMAT:
        raise RecipeError(f"not a recipe: 'format' must be {RECIPE_FORMAT!r}")
    unknown = sorted(set(data) - {"format", "version", "steps"})
    if unknown:
        raise RecipeError(f"unknown recipe key(s): {', '.join(unknown)}")
    version = data.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version != RECIPE_VERSION:
        raise RecipeError(
            f"recipe version {version!r} is not supported (this Tether reads {RECIPE_VERSION})"
        )
    steps = data.get("steps")
    if not isinstance(steps, list) or not steps:
        raise RecipeError("a recipe needs a non-empty 'steps' list")
    checked = tuple(_check_step(i, step, folder) for i, step in enumerate(steps, start=1))
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return Recipe(folder=folder, sha256=sha, version=version, steps=checked)


def read_recipe(path: str | Path) -> Recipe:
    """Read and check the recipe file at ``path``; raise [`RecipeError`][] if not valid."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise RecipeError(f"cannot read the recipe {path}: {exc}") from exc
    origin = path.resolve()
    return dataclasses.replace(recipe_from_text(text, folder=origin.parent), path=origin)


def recipe_to_text(requests: Sequence[Request], *, folder: str | Path) -> str:
    """Return the recipe text of ``requests``, with paths relative to ``folder`` if possible."""
    folder = Path(folder)
    steps = [request_to_mapping(_with_paths(r, _relative, folder)) for r in requests]
    data = {"format": RECIPE_FORMAT, "version": RECIPE_VERSION, "steps": steps}
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"


def write_recipe(path: str | Path, requests: Sequence[Request]) -> Path:
    """Write ``requests`` as the recipe file ``path`` and return the path."""
    path = Path(path)
    text = recipe_to_text(requests, folder=path.resolve().parent)
    path.write_text(text, encoding="utf-8")
    return path


# --- jobs of a recipe run ---------------------------------------------------------------------


def recipe_part(recipe: Recipe, number: int) -> str:
    """Return the name of the one part of the job of step ``number`` (recipe and step)."""
    return json.dumps(
        {"recipe_sha256": recipe.sha256, "recipe_version": recipe.version, "step": number},
        sort_keys=True,
        separators=(",", ":"),
    )


def _parse_part(part: str) -> dict[str, Any] | None:
    try:
        data = json.loads(part)
    except (json.JSONDecodeError, TypeError):
        return None
    if isinstance(data, dict) and {"recipe_sha256", "recipe_version", "step"} <= set(data):
        return data
    return None


def stopped_recipe(path: str | Path) -> tuple[str, int] | None:
    """Return the ``(sha256, version)`` of the recipe whose run stopped last here.

    It is read from the checkpoint of the stopped run: the latest recipe job that was
    stopped (stored *cancelled*). ``None`` when no recipe run stopped in the project.
    """
    from tether.project.jobs import JobState, read_jobs  # noqa: PLC0415

    for job in reversed(read_jobs(path)):
        if job.stored_state is not JobState.CANCELLED or len(job.parts) != 1:
            continue
        part = _parse_part(job.parts[0])
        if part is not None:
            return str(part["recipe_sha256"]), int(part["recipe_version"])
    return None


def _project_of(request: Request) -> str | None:
    if isinstance(request, ExtractRequest) and request.count_only:
        return None
    field = request.PROJECT_FIELD
    value = getattr(request, field) if field else None
    return value if isinstance(value, str) and value else None


def _done_parts(path: str) -> set[str]:
    """Return the recipe parts whose latest job in ``path`` is done.

    A later run that failed or deferred a step stores a newer job for it, so an older
    *done* job of the same step does not make it complete.
    """
    from tether.project.jobs import JobState, read_jobs  # noqa: PLC0415

    latest = {job.parts[0]: job.stored_state for job in read_jobs(path) if len(job.parts) == 1}
    return {part for part, state in latest.items() if state is JobState.DONE}


def _project_done(recipe: Recipe, step: RecipeStep, project: str) -> bool:
    return Path(project).is_file() and recipe_part(recipe, step.number) in _done_parts(project)


def _checkpoint_path(recipe: Recipe) -> Path:
    from tether.project.lock import user_lock_dir  # noqa: PLC0415

    origin = os.path.normcase(recipe.path or recipe.folder.resolve())
    key = hashlib.sha256(os.fsencode(origin) + b"\0" + recipe.sha256.encode("utf-8"))
    return user_lock_dir().parent / "recipe-runs" / f"{key.hexdigest()}.json"


def _legacy_checkpoint(recipe: Recipe) -> int:
    """Find the last completed project job when no recipe checkpoint exists."""
    for step in reversed(recipe.steps):
        project = _project_of(step.request)
        if project and _project_done(recipe, step, project):
            return step.number
    return 0


def _resume_prefix(recipe: Recipe, path: Path) -> int:
    """Find the first unfinished step, including a project that lost its completed job."""
    has_checkpoint = True
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        completed = _legacy_checkpoint(recipe)
        has_checkpoint = False
    except (OSError, UnicodeError) as exc:
        raise RecipeError(f"cannot read the recipe checkpoint: {exc}") from exc
    else:
        try:
            completed = json.loads(text)["completed"]
        except (ValueError, KeyError, TypeError) as exc:
            raise RecipeError("the recipe checkpoint is invalid") from exc
        if (
            not isinstance(completed, int)
            or isinstance(completed, bool)
            or not 0 <= completed <= len(recipe.steps)
        ):
            raise RecipeError("the recipe checkpoint is invalid")

    verified_projects: set[str] = set()
    for step in reversed(recipe.steps[:completed]):
        project = _project_of(step.request)
        if project and project not in verified_projects:
            if not _project_done(recipe, step, project):
                completed = step.number - 1
            elif has_checkpoint:
                # A later completed step may have replaced the project and its job table.
                verified_projects.add(project)
    return completed


def _save_checkpoint(path: Path, completed: int) -> None:
    from tether.project.lock import _atomic_write_json  # noqa: PLC0415, PLC2701

    _atomic_write_json(path, {"completed": completed}, tag=uuid4().hex)


class _StepFailed(Exception):  # noqa: N818 - stored as the job's error text
    pass


def _record(project_path: str, request: Request, part: str, outcome: str, message: str) -> None:
    """Store the job of a step in ``project_path`` with its end state.

    The command holds the project's lock while it runs, so the job is written around it:
    *queued* before the command (when the project exists), and its end state after.
    """
    from tether.project import jobs  # noqa: PLC0415
    from tether.project.core import Project  # noqa: PLC0415

    def work(_part: str, _cancel: object) -> None:
        if outcome == "stopped":
            raise jobs.JobCancelled
        if outcome == "failed":
            raise _StepFailed(message)

    project = Project.open(project_path)
    project.acquire_lock()
    try:
        job = _open_job(project, request, part)
        if job.stored_state is jobs.JobState.QUEUED:
            jobs.run_job(project, job.job_id, work)
        else:
            jobs.retry_job(project, job.job_id, work)
    finally:
        project.release_lock()


def _open_job(project: Any, request: Request, part: str) -> Any:
    """Return the job of this step that is not done, or a new queued one (needs the lock)."""
    from tether.project import jobs  # noqa: PLC0415
    from tether.services.base import request_to_text  # noqa: PLC0415

    text = request_to_text(request)
    for job in reversed(jobs.read_jobs(project.path)):
        if (
            job.parts == (part,)
            and job.request == text
            and job.stored_state is not (jobs.JobState.DONE)
        ):
            if job.stored_state is jobs.JobState.RUNNING:
                break
            return job
    return jobs.submit_job(project, request, [part])


def _queue(project_path: str, request: Request, part: str) -> None:
    from tether.project.core import Project  # noqa: PLC0415

    project = Project.open(project_path)
    project.acquire_lock()
    try:
        _open_job(project, request, part)
    finally:
        project.release_lock()


class ApprovalGate:
    """What a command asks at an approval step of a recipe run (ADR-0102 §2-4).

    A command that reaches an approval step calls [`decide`][] before it writes what
    needs the approval. When the step's policy approves, the command records it with
    [`record`][]; a policy that does not approve records nothing.
    """

    def __init__(self, recipe: Recipe, step: RecipeStep) -> None:
        self.recipe = recipe
        self.step = step

    def decide(self, step: str, subject: str, *, project: str, report: str = "") -> Policy | None:
        """Return ``None`` when the step is approved already, else the policy to apply.

        A recorded approval of ``step`` for ``subject`` (by a person, or by a policy of an
        earlier run) approves it. With none and no policy for the step, raise
        [`ApprovalStop`][].
        """
        from tether.project.approvals import find_approval  # noqa: PLC0415

        if Path(project).is_file() and find_approval(project, step, subject) is not None:
            return None
        policy = self.step.policy
        if policy is not None and policy.step == step:
            return policy
        raise ApprovalStop(step, subject, project=project, report=report)

    def record(self, project: Any, *, step: str, subject: str, report: str) -> Any:
        """Record an approval of ``step`` for ``subject`` by this step's policy."""
        from tether.project.approvals import record_approval  # noqa: PLC0415

        policy = self.step.policy
        if policy is None or policy.step != step:
            raise ValueError(f"this step has no policy for {step!r}")
        return record_approval(
            project,
            step=step,
            subject=subject,
            by="policy",
            actor=policy.text,
            report=report,
            recipe_sha256=self.recipe.sha256,
            recipe_version=self.recipe.version,
        )


# --- the run ----------------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class StepOutcome:
    """What happened to one step: ``done``, ``skipped`` (done before), ``failed``, ``stopped``.

    ``deferred`` is true when the command finished with a stage deferred (a ``batch``
    whose sidecar was not available).
    """

    number: int
    command: str
    state: str
    message: str = ""
    deferred: bool = False


@dataclasses.dataclass(frozen=True)
class RecipeRun:
    """The outcome of [`run_recipe`][]: each step reached, and the stop, if any."""

    recipe: Recipe
    steps: tuple[StepOutcome, ...]
    stop: ApprovalStop | None = None

    @property
    def failed(self) -> bool:
        """Whether a step failed."""
        return any(s.state == "failed" for s in self.steps)

    @property
    def deferred(self) -> bool:
        """Whether a step deferred a stage."""
        return any(s.deferred for s in self.steps)


#: The message of a step that ran with a stage deferred (``tether batch`` exit 3).
_DEFERRED = "a stage was deferred; run the recipe again with --resume to finish it"


def _result_outcome(result: Any) -> tuple[str, bool]:
    """``(failure text, deferred)`` from a command's result (a ``batch`` summary)."""
    summary = getattr(result, "summary", None)
    failed = int(getattr(summary, "n_failed", 0) or 0)
    deferred = int(getattr(summary, "n_deferred", 0) or 0) > 0
    return (f"{failed} movie(s) had a failed stage" if failed else ""), deferred


def _run_step(recipe: Recipe, step: RecipeStep) -> tuple[StepOutcome, ApprovalStop | None]:
    registry = _registry()
    request = step.request
    entry = registry.command(request.COMMAND)
    part = recipe_part(recipe, step.number)
    project = _project_of(request)
    if project and Path(project).is_file():
        try:
            _queue(project, request, part)
        except _STORE_ERRORS as exc:
            message = f"cannot store the job of the step in {project}: {exc}"
            return StepOutcome(step.number, request.COMMAND, "failed", message), None
    observers = {"approvals": ApprovalGate(recipe, step)} if entry.approval_steps else {}
    stop: ApprovalStop | None = None
    deferred = False
    try:
        result = registry.run(request, **observers)
    except ApprovalStop as exc:
        state, message, stop = "stopped", str(exc), exc
    except ServiceError as exc:
        state, message = "failed", str(exc)
    else:
        message, deferred = _result_outcome(result)
        state = "failed" if message else "done"
        if deferred and not message:
            message = _DEFERRED
    # A deferred stage is not complete: its job is stored *failed*, so --resume runs the
    # step again and the command's own checkpoint resumes just that stage.
    stored = "failed" if deferred else state
    if project and Path(project).is_file():
        try:
            _record(project, request, part, stored, message)
        except _STORE_ERRORS as exc:
            # The command ran; without its job a later --resume would run it again.
            state, stop = "failed", None
            message = f"the step ran, but its job could not be stored in {project}: {exc}"
    return StepOutcome(step.number, request.COMMAND, state, message, deferred), stop


#: What opening, locking or writing a project's job table can raise (``LockError`` and
#: ``JobError`` are ``RuntimeError``\ s).
_STORE_ERRORS = (OSError, ValueError, KeyError, RuntimeError)


def run_recipe(
    recipe: Recipe,
    *,
    resume: bool = False,
    progress: Callable[[StepOutcome], None] | None = None,
) -> RecipeRun:
    """Run ``recipe`` in order until a step fails, defers a stage or needs approval.

    With ``resume``, the completed prefix of the latest run of the same recipe is
    skipped. The first unfinished step and all following steps run again. Without
    ``resume``, the new run starts with an empty checkpoint. ``progress`` is called
    with the outcome of each step.
    """
    from tether.project.lock import _lifecycle_guard  # noqa: PLC0415, PLC2701

    checkpoint = _checkpoint_path(recipe)
    with ExitStack() as stack:
        try:
            checkpoint.parent.mkdir(parents=True, exist_ok=True)
            stack.enter_context(_lifecycle_guard(checkpoint))
            completed = _resume_prefix(recipe, checkpoint) if resume else 0
            _save_checkpoint(checkpoint, completed)
        except OSError as exc:
            raise RecipeError(f"cannot start a recipe checkpoint: {exc}") from exc
        return _run_from_checkpoint(recipe, checkpoint, completed, progress)


def _run_from_checkpoint(
    recipe: Recipe,
    checkpoint: Path,
    start: int,
    progress: Callable[[StepOutcome], None] | None,
) -> RecipeRun:
    outcomes: list[StepOutcome] = []
    stop: ApprovalStop | None = None
    for step in recipe.steps:
        if step.number <= start:
            outcome = StepOutcome(step.number, step.request.COMMAND, "skipped")
        else:
            outcome, stop = _run_step(recipe, step)
            if outcome.state == "done" and not outcome.deferred:
                try:
                    _save_checkpoint(checkpoint, step.number)
                except OSError as exc:
                    outcome = dataclasses.replace(
                        outcome, state="failed", message=f"cannot record recipe progress: {exc}"
                    )
        outcomes.append(outcome)
        if progress is not None:
            progress(outcome)
        if outcome.state in ("failed", "stopped") or outcome.deferred:
            break
    return RecipeRun(recipe=recipe, steps=tuple(outcomes), stop=stop)


def plan_recipe(recipe: Recipe, *, resume: bool = False) -> list[str]:
    """Return what ``tether run --dry-run`` prints: each step, its inputs and approval steps.

    With ``resume``, it also reads the checkpoint and any relevant job records.
    """
    registry = _registry()
    start = _resume_prefix(recipe, _checkpoint_path(recipe)) if resume else 0
    total = len(recipe.steps)
    lines = [f"recipe {recipe.sha256[:12]} (version {recipe.version}), {total} step(s)"]
    for step in recipe.steps:
        request = step.request
        entry = registry.command(request.COMMAND)
        done = " (done; skipped by --resume)" if step.number <= start else ""
        lines.append(f"step {step.number}/{total}: tether {request.COMMAND}{done}")
        for name in request.PATH_FIELDS:
            value = getattr(request, name)
            if value not in (None, (), ""):
                shown = ", ".join(value) if isinstance(value, tuple) else value
                lines.append(f"  {name}: {shown}")
        project = _project_of(request)
        lines.append(f"  job stored in: {project if project else '(no project; not stored)'}")
        for approval in entry.approval_steps:
            if step.policy is not None and step.policy.step == approval:
                lines.append(f"  approval step {approval}: policy {step.policy.text}")
            else:
                lines.append(f"  approval step {approval}: stops for a person (no policy)")
    return lines


def recipe_from_project(path: str | Path) -> list[Request]:
    """Return the requests of a project's completed jobs, in order (**Save as recipe**).

    These are the actions that made the present state of the project, in the order they
    completed (a job retried after a later one completed takes its place after it). A
    request that a later job repeats is kept again, since a job between the two can
    change what the repeat does.
    """
    from tether.project.jobs import JobState, read_jobs  # noqa: PLC0415

    registry = _registry()
    requests: list[Request] = []
    done = [job for job in read_jobs(path) if job.stored_state is JobState.DONE]
    for job in sorted(done, key=lambda job: job.ended_utc):
        try:
            requests.append(registry.request_from_text(job.request))
        except RequestError as exc:
            raise RecipeError(
                f"the job {job.job_id} holds a request this Tether cannot read: {exc}"
            ) from exc
    if not requests:
        raise RecipeError(f"{Path(path).name} has no completed job to save as a recipe")
    return requests
