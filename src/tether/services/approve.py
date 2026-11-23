# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The ``approve`` service command: a person approves a step that needs a person (ADR-0102).

``tether approve PROJECT STEP --subject S --report R`` records that a person approved the
approval step ``STEP`` of ``S`` on the evidence ``R``. When a recipe run stopped at that
step (``tether run`` exited ``4``), the record carries the identity of that recipe, read
from the checkpoint of the stopped run, and ``tether run RECIPE --resume`` then goes on
past the step. The record is one row of ``/settings/approvals``
([`tether.project.approvals`][]).

A recipe cannot hold this command: a recipe approves only with a policy (ADR-0102 §3).
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from tether.services.base import CommandFailed, Request, RequestRefused

if TYPE_CHECKING:
    from tether.project.approvals import Approval

__all__ = ["ApproveRequest", "ApproveResult", "run"]


@dataclasses.dataclass(frozen=True, kw_only=True)
class ApproveRequest(Request):
    """Approve ``step`` for ``subject`` in ``project``, on the evidence ``report``.

    ``by`` is the name of the person; ``None`` reads it as the ``labeler`` of a curation
    label is ([`default_labeler`][tether.project.labels.default_labeler]).
    """

    COMMAND: ClassVar[str] = "approve"
    VERSION: ClassVar[int] = 1
    PATH_FIELDS: ClassVar[tuple[str, ...]] = ("project", "report")
    PROJECT_FIELD: ClassVar[str | None] = "project"

    project: str
    step: str
    subject: str
    report: str
    by: str | None = None


@dataclasses.dataclass(frozen=True)
class ApproveResult:
    """The outcome of an ``approve`` command: the project and the stored approval."""

    project: Path
    approval: Approval


def run(request: ApproveRequest) -> ApproveResult:
    """Record the approval; refuse a bad step, subject or report before any write."""
    from tether.project.approvals import (  # noqa: PLC0415
        APPROVAL_STEPS,
        record_approval,
        report_files,
    )
    from tether.project.core import Project  # noqa: PLC0415
    from tether.project.labels import default_labeler  # noqa: PLC0415
    from tether.project.lock import LockError  # noqa: PLC0415
    from tether.services.recipe import stopped_recipe  # noqa: PLC0415

    if request.step not in APPROVAL_STEPS:
        raise RequestRefused(
            f"unknown approval step {request.step!r} (known: {', '.join(APPROVAL_STEPS)})"
        )
    if not request.subject.strip():
        raise RequestRefused("an approval needs a subject (--subject)")
    try:
        report_files(request.report)
    except ValueError as exc:
        raise RequestRefused(f"an approval needs its report: {exc}") from exc
    name = request.by if request.by is not None else default_labeler()
    if not name.strip():
        raise RequestRefused("an approval needs the name of the person (--by)")
    try:
        project = Project.open(request.project)
    except (OSError, ValueError) as exc:
        raise RequestRefused(f"cannot open {request.project}: {exc}") from exc
    try:
        project.acquire_lock()
    except LockError as exc:
        raise CommandFailed(f"cannot take the lock of {request.project}: {exc}") from exc
    try:
        recipe = stopped_recipe(project.path, step=request.step, subject=request.subject)
        sha, version = recipe if recipe is not None else ("", 0)
        approval = record_approval(
            project,
            step=request.step,
            subject=request.subject,
            by="person",
            actor=name,
            report=request.report,
            recipe_sha256=sha,
            recipe_version=version,
        )
    except (LockError, OSError, ValueError) as exc:
        raise CommandFailed(str(exc)) from exc
    finally:
        project.release_lock()
    return ApproveResult(project=project.path, approval=approval)
