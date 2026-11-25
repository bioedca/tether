# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The carried, new and lost molecules of each new trace generation, as a front end reads them.

The reconciliation page of the application (#923) shows the three lists of
[`tether.project.generation_reconcile`][]. Like [`tether.services.movies`][], this reads and
never writes, so it is not an action: it has no request, no recipe step and no command-line
subcommand. It lives here so that the application reaches the lists through one Qt-free
place instead of reading the core itself (ADR-0100), and like a command it imports the core
only when it runs. The confirmation is a project write, which the application makes through
[`Project.confirm_carried`][tether.project.core.Project.confirm_carried] under its writer
lock, as it makes an accept or a reject.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from os import PathLike

    from tether.project.generation_reconcile import GenerationReconciliation

__all__ = ["read_reconciliations"]


def read_reconciliations(
    path: str | PathLike[str],
) -> tuple[GenerationReconciliation, ...]:
    """Return one reconciliation per movie that has more than one generation (a read).

    This is [`reconcile_project`][tether.project.generation_reconcile.reconcile_project].
    """
    from tether.project.generation_reconcile import reconcile_project  # noqa: PLC0415

    return reconcile_project(path)
