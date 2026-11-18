# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Atomic project publication under the destination's writer lock (#346, ADR-0112)."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from shutil import copy2
from tempfile import mkstemp

from tether.project.lock import (
    LockError,
    PublishGuardRequiredError,
    file_identity,
    publish_lock,
    replacement_identity,
)

# The staged siblings this context is writing into right now. A guarded writer asked to
# update one of them writes in place: the enclosing ``guarded_project_write`` already
# owns that sibling's copy and its publish (#258). A ContextVar, not a module global, so
# two threads staging two projects never see each other's siblings.
_ACTIVE_STAGES: ContextVar[frozenset[str]] = ContextVar(
    "tether_guarded_publish_active_stages", default=frozenset()
)


def _stage_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path))


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
    and is then unlinked. Where the filesystem has no hard links, the destination's
    writer lock is taken, an exclusive create reserves the name the same way, and
    ``os.replace`` fills it.

    Raises
    ------
    PublishGuardRequiredError
        If ``publish_guard`` is ``None`` and ``destination`` exists.
    """
    if publish_guard is not None:
        publish_guard()
        with replacement_identity(stage, destination):
            os.replace(stage, destination)
        return
    require_publish_guard(destination, None)
    try:
        os.link(stage, destination)
    except FileExistsError:
        require_publish_guard(destination, None)
        raise
    except OSError:
        # No hard links on this filesystem (FAT, some network shares). Under the
        # destination's lock, reserve the name with an exclusive create, which fails
        # rather than replaces if another writer created it, then fill the placeholder.
        _publish_over_reserved_name(stage, destination)
        return
    Path(stage).unlink()


def _publish_over_reserved_name(stage: str | Path, destination: str | Path) -> None:
    """Publish ``stage`` onto an absent ``destination`` without hard links.

    The destination's writer lock is held across the reservation and the replace,
    so no cooperating writer can publish into the reserved name in between.
    """
    with publish_lock(destination) as guard:
        try:
            fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)
        except FileExistsError:
            require_publish_guard(destination, None)
            raise
        os.close(fd)
        placeholder = file_identity(destination)
        try:
            guard()
            with replacement_identity(stage, destination):
                os.replace(stage, destination)
        except BaseException:
            # Remove only this call's own empty placeholder: after a lock steal the
            # name may already hold the successor's project.
            if _is_placeholder(destination, placeholder):
                Path(destination).unlink(missing_ok=True)
            raise


def _is_placeholder(path: str | Path, placeholder: tuple[int, int] | None) -> bool:
    try:
        st = os.stat(path)
    except OSError:
        return False
    if st.st_size != 0:
        return False
    return placeholder is None or file_identity(path) == placeholder


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

    Stages nest. When ``project_path`` is a sibling an enclosing guarded write staged
    and has not yet published, the path is yielded as it is: the caller writes into
    that sibling in place, ``write_guard`` still runs on entry and on a clean exit, and
    the enclosing write keeps the only copy, the only identity check and the only
    publish. This lets one staged sibling span several dependent writers (the batch
    correct stage) instead of each writer copying and publishing the whole project on
    its own.
    """
    canonical_path = Path(project_path)
    if write_guard is None:
        yield canonical_path
        return

    if _stage_key(canonical_path) in _ACTIVE_STAGES.get():
        write_guard()
        yield canonical_path
        write_guard()
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
        token = _ACTIVE_STAGES.set(_ACTIVE_STAGES.get() | {_stage_key(guarded_stage)})
        try:
            yield guarded_stage
        finally:
            _ACTIVE_STAGES.reset(token)
        write_guard()
        if source_identity is not None and file_identity(canonical_path) != source_identity:
            raise LockError(
                f"{canonical_path.name} was replaced by another writer during the {label} write"
            )
        with replacement_identity(guarded_stage, canonical_path):
            os.replace(guarded_stage, canonical_path)
        guarded_stage = None
    finally:
        if guarded_stage is not None:
            guarded_stage.unlink(missing_ok=True)
