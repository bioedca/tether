# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Advisory notices that every front end shows when it opens a project.

A notice reads and never writes, so it is not an action: it has no request, no recipe step
and no command-line subcommand, and it is a plain function here rather than a registered
service command. It lives in this package so that the application and the command line take
the same text from one place, as ADR-0100 asks of an action, and cannot word it differently.
Like a command, it imports the core only when it runs.

[`conflict_copy_warning`][] is the first: the OneDrive/SharePoint conflict copies that
[`tether.project.lock.conflict_copies`][] finds beside a project are *detected, not
prevented*, and named to the user (PRD §5.4).
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["conflict_copy_warning"]


def conflict_copy_warning(project_path: str | Path) -> str | None:
    """Return the one-line warning for possible conflict copies of a project, or ``None``.

    The application (when it opens a project) and the command line (``tether extract``,
    ``tether batch`` and ``tether info``) show this same text. It names every sibling that
    [`tether.project.lock.conflict_copies`][] matches, by file name, calls them only
    *possible* conflict copies (the match is by name alone), and says that Tether leaves them
    alone: detection only, never reconciliation, deletion or merge (PRD §5.4).

    A folder that cannot be listed (``OSError``) gives ``None``: the notice is advisory, and
    failing to look must never block or fail an open.
    """
    from tether.project import lock

    p = Path(project_path)
    try:
        copies = lock.conflict_copies(p)
    except OSError:
        return None
    if not copies:
        return None
    one = len(copies) == 1
    names = ", ".join(c.name for c in copies)
    noun = "copy" if one else "copies"
    pronoun = "it" if one else "them"
    return (
        f"{len(copies)} possible OneDrive/SharePoint conflict {noun} beside {p.name}: "
        f"{names}. Tether does not merge or delete {pronoun}; compare and reconcile by hand"
    )
