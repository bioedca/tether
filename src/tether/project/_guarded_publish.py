# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Atomic sibling-project publication for guarded multi-write persistence."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from shutil import copy2
from tempfile import mkstemp

# The staged siblings this context is writing into right now. A guarded writer asked to
# update one of them writes in place: the enclosing ``guarded_project_write`` already
# owns that sibling's copy and its publish (#258). A ContextVar, not a module global, so
# two threads staging two projects never see each other's siblings.
_ACTIVE_STAGES: ContextVar[frozenset[str]] = ContextVar(
    "tether_guarded_publish_active_stages", default=frozenset()
)


def _stage_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path))


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

    Stages nest. When ``project_path`` is a sibling an enclosing guarded write staged
    and has not yet published, the path is yielded as it is: the caller writes into
    that sibling in place, ``write_guard`` still runs on entry and on a clean exit, and
    the enclosing write keeps the only copy and the only publish. This lets one staged
    sibling span several dependent writers (the batch correct stage) instead of each
    writer copying and publishing the whole project on its own.
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
        os.replace(guarded_stage, canonical_path)
        guarded_stage = None
    finally:
        if guarded_stage is not None:
            guarded_stage.unlink(missing_ok=True)
