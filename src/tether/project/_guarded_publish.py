# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Atomic sibling-project publication for guarded multi-write persistence."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from shutil import copy2
from tempfile import mkstemp


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
    """
    canonical_path = Path(project_path)
    if write_guard is None:
        yield canonical_path
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
        yield guarded_stage
        write_guard()
        os.replace(guarded_stage, canonical_path)
        guarded_stage = None
    finally:
        if guarded_stage is not None:
            guarded_stage.unlink(missing_ok=True)
