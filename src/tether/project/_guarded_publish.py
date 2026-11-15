# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Atomic project publication under the destination's writer lock (#346, ADR-0112)."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from shutil import copy2
from tempfile import mkstemp

from tether.project.lock import LockError, PublishGuardRequiredError, file_identity


def require_publish_guard(
    destination: str | Path,
    publish_guard: Callable[[], None] | None,
) -> None:
    """Refuse an unguarded publish onto an existing ``destination`` (ADR-0112).

    Raises
    ------
    PublishGuardRequiredError
        If ``publish_guard`` is ``None`` and any entry exists at ``destination``.
    """
    if publish_guard is None and os.path.lexists(destination):
        raise PublishGuardRequiredError(
            f"{Path(destination).name} exists; replacing it needs its writer lock "
            "(hold tether.project.lock.publish_lock and pass its check as publish_guard)"
        )


def publish_staged_project(
    stage: str | Path,
    destination: str | Path,
    *,
    publish_guard: Callable[[], None] | None,
) -> None:
    """Publish a complete, closed ``stage`` project onto ``destination`` atomically.

    With a ``publish_guard`` the guard runs immediately before one ``os.replace``.
    Without one the destination must not exist: the stage is hard-linked to the new
    name, which fails rather than replaces if another writer created it meanwhile,
    and is then unlinked. Where the filesystem has no hard links, the publish checks
    that the destination is still absent and uses ``os.replace``.

    Raises
    ------
    PublishGuardRequiredError
        If ``publish_guard`` is ``None`` and ``destination`` exists.
    """
    if publish_guard is not None:
        publish_guard()
        os.replace(stage, destination)
        return
    require_publish_guard(destination, None)
    try:
        os.link(stage, destination)
    except FileExistsError:
        require_publish_guard(destination, None)
        raise
    except OSError:
        # No hard links on this filesystem (FAT, some network shares): keep the
        # established replace, but only onto a destination that is still absent.
        require_publish_guard(destination, None)
        os.replace(stage, destination)
        return
    Path(stage).unlink()


@contextmanager
def guarded_project_write(
    project_path: str | Path,
    *,
    write_guard: Callable[[], None] | None,
    label: str,
) -> Iterator[Path]:
    """Yield the canonical path or a guarded sibling copy and publish on success.

    Unguarded callers retain the established in-place write path. Guarded callers
    instead receive a complete same-directory project copy. After the caller closes
    that staged HDF5 file, ownership is checked once more and the entire dependent
    update is published with one atomic ``os.replace``. Any exception or ownership
    loss removes the sibling without changing canonical data.

    The canonical file's identity ([`file_identity`][tether.project.lock.file_identity])
    is taken before the copy and checked again before the publish. If another writer
    replaced the canonical file meanwhile, the staged copy holds the old file's data,
    so the publish is refused with [`LockError`][tether.project.lock.LockError] rather
    than overwriting the newer file. A filesystem with no inode numbers gives no
    identity, and there only the caller's ``write_guard`` protects the publish.
    """
    canonical_path = Path(project_path)
    if write_guard is None:
        yield canonical_path
        return

    write_guard()
    source_identity = file_identity(canonical_path)
    fd, raw_stage = mkstemp(
        prefix=f".{canonical_path.name}.",
        suffix=f".{label}.tmp",
        dir=canonical_path.parent,
    )
    os.close(fd)
    guarded_stage: Path | None = Path(raw_stage)
    try:
        copy2(canonical_path, guarded_stage)
        write_guard()
        yield guarded_stage
        write_guard()
        if source_identity is not None and file_identity(canonical_path) != source_identity:
            raise LockError(
                f"{canonical_path.name} was replaced by another writer during the {label} write"
            )
        os.replace(guarded_stage, canonical_path)
        guarded_stage = None
    finally:
        if guarded_stage is not None:
            guarded_stage.unlink(missing_ok=True)
