# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The checksum job of the source registry (#912, ADR-0140).

A checksum reads the whole file, and a movie can be many gigabytes, so the registry
([`tether.project.sources`][]) calculates it as a stored **job** of the coordinator (#895,
ADR-0126) with one part for each source. [`submit_checksum_job`][] stores the job;
[`run_checksum_job`][] does its parts, on the thread that calls it. A front end calls it
from a worker thread, never from the window thread, and shows the two progress
callbacks: ``progress(done, total)`` after each source and ``file_progress(source_id,
read, size)`` after each 1 MiB block. Cancel stops between blocks, the store stays as it
was before that source, and a later run continues from the checkpoint. A file whose checksum
differs from the one already known for it (it changed since extraction) fails its part and
writes nothing.

Like [`tether.services.movies`][], this is not a registered service command: it has no
command-line subcommand. The request records only source ids, never a path, so a stored
job holds no private path.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, ClassVar

from tether.services.base import Request

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from tether.project.core import Project
    from tether.project.jobs import CancelToken, JobRecord

__all__ = ["SourceChecksumRequest", "run_checksum_job", "submit_checksum_job"]


@dataclasses.dataclass(frozen=True, kw_only=True)
class SourceChecksumRequest(Request):
    """Calculate the checksum of each named source of the project."""

    COMMAND: ClassVar[str] = "source-checksum"
    VERSION: ClassVar[int] = 1

    source_ids: tuple[str, ...]


def submit_checksum_job(project: Project, source_ids: Sequence[str] | None = None) -> JobRecord:
    """Store a queued checksum job for ``source_ids`` (default: every source with none).

    Needs the writer lock. Raises ``ValueError`` when there is no source to read.
    """
    from tether.project import jobs  # noqa: PLC0415
    from tether.project.sources import ensure_registry, read_sources  # noqa: PLC0415

    if source_ids is None:
        source_ids = [r.source_id for r in read_sources(project.path) if not r.checksum_calculated]
    ids = tuple(source_ids)
    if not ids:
        raise ValueError("every source of the project already has its checksum")
    # Add the table before the job starts, so no part's write replaces the file (PRD §5.4).
    ensure_registry(project)
    return jobs.submit_job(project, SourceChecksumRequest(source_ids=ids), ids)


def run_checksum_job(
    project: Project,
    job_id: str,
    *,
    cancel: CancelToken | None = None,
    progress: Callable[[int, int], None] | None = None,
    file_progress: Callable[[str, int, int], None] | None = None,
) -> JobRecord:
    """Do the parts of a checksum job that are not complete; return the final record.

    A queued job starts; a failed, cancelled or interrupted one continues from its
    checkpoint. Needs the writer lock retained by ``project`` (``project.acquire_lock()``).
    """
    from tether.project import jobs  # noqa: PLC0415
    from tether.project.sources import file_sha256, read_sources, set_checksum  # noqa: PLC0415

    # The job binds to the lock epoch it starts under; each checksum write checks it again
    # just before it writes, so a steal during a long read stops the write.
    nonce = project._assert_held_lock()

    def guard() -> None:
        project._assert_held_lock(expected_nonce=nonce)

    def work(source_id: str, token: CancelToken) -> None:
        record = next((r for r in read_sources(project.path) if r.source_id == source_id), None)
        if record is None:
            raise KeyError(f"no source {source_id!r} in {project.path.name}")

        def report(read: int, size: int) -> None:
            if file_progress is not None:
                file_progress(source_id, read, size)

        digest = file_sha256(record.resolve(project.path), cancel=token, progress=report)
        set_checksum(project, source_id, digest, guard=guard)

    job = jobs.read_job(project.path, job_id)
    start = jobs.run_job if job.stored_state is jobs.JobState.QUEUED else jobs.retry_job
    return start(project, job_id, work, cancel=cancel, progress=progress)
