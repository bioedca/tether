# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The readiness of each stage of a project, as every front end reads it (#896).

A status read writes nothing, so it is not an action: like the notices of
[`tether.services.notices`][], it has no request, no recipe step and no registered service
command, and it is a plain function here. The application (the guided mode, the disabled
controls, the resume) and ``tether status`` both call [`project_status`][], so the two read
one record and cannot disagree on the next step (PRD §7.13 FR-WORKFLOW-3). Like a command,
it imports the core only when it runs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from os import PathLike

    from tether.project.readiness import ProjectReadiness

__all__ = ["project_status"]


def project_status(path: str | PathLike[str]) -> ProjectReadiness:
    """Return the readiness record of the project at ``path`` (a read, no lock).

    This is [`read_project_readiness`][tether.project.readiness.read_project_readiness];
    it raises its [`ProjectReadinessError`][tether.project.readiness.ProjectReadinessError]
    when the project cannot be read.
    """
    from tether.project import readiness  # noqa: PLC0415

    return readiness.read_project_readiness(path)
