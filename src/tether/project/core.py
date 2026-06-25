# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The headless project/experiment core (PRD §4.2, §7.11).

:class:`Project` is the scriptable, display-free entry point to a ``.tether``
store. PRD §7.11 (FR-BATCH) requires every operation to be usable without the
GUI; the GUI (:mod:`tether.gui`) is a thin layer over this core. This M0 scaffold
wraps the frozen :mod:`tether.io` store lifecycle (create / open / version
compatibility) and the provisional filename→condition parse. Row-level
extraction (movies, molecules, traces) lands additively at M1+ — this never
mutates the M0-frozen schema, only the *data* inside it.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from tether.io.filename import ParsedFilename, parse_filename
from tether.io.schema import (
    SCHEMA_VERSION,
    assert_is_compatible_project,
    create_project,
    read_schema_version,
)

if TYPE_CHECKING:
    from tether.io.movie import MovieReader

__all__ = ["Project"]


class Project:
    """A handle to a ``.tether`` project store (the headless experiment model).

    A *condition* spans many movies across many days/files (PRD §5.1); this
    handle is the headless seam the batch runner (§7.11) and the GUI both build
    on. It is intentionally lightweight — it owns the *path*, not an open HDF5
    handle, so it is cheap to pass around and safe to construct without touching
    disk. Each operation opens the file for the minimum scope it needs.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"Project({str(self.path)!r})"

    @classmethod
    def create(cls, path: str | Path, *, overwrite: bool = False) -> Project:
        """Create a fresh ``.tether`` with the full frozen §5 skeleton.

        Thin wrapper over :func:`tether.io.schema.create_project`; refuses to
        clobber an existing file unless ``overwrite=True``.
        """
        create_project(path, overwrite=overwrite)
        return cls(path)

    @classmethod
    def open(cls, path: str | Path) -> Project:
        """Open an existing ``.tether``, rejecting non-projects and future files.

        Validates the on-disk Tether contract — the file is readable HDF5 carrying
        the ``format`` marker (PRD §5.1), and its ``schema_version`` is not newer
        than this app (the §5.4 forward-compatibility guard) — before handing back
        a usable :class:`Project`. A foreign or partial HDF5 file is refused, not
        silently accepted (:func:`tether.io.schema.assert_is_compatible_project`).
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"no such .tether project: {path}")
        assert_is_compatible_project(path)
        return cls(path)

    @property
    def schema_version(self) -> int:
        """The on-disk ``schema_version`` of this project (PRD §5)."""
        return read_schema_version(self.path)

    @property
    def app_schema_version(self) -> int:
        """The schema version this build of Tether writes (:data:`SCHEMA_VERSION`)."""
        return SCHEMA_VERSION

    @staticmethod
    def parse_condition(filename: str) -> ParsedFilename:
        """Parse a source filename into a **provisional** condition (PRD §7.6).

        The parse is filename-derived and must be human-validated at M4
        (PRD §7.6); :attr:`ParsedFilename.condition_id` is the provisional id
        written to ``/molecules.condition_id_provisional`` at extraction.
        """
        return parse_filename(filename)

    def open_movie(self, movie_path: str | Path) -> MovieReader:
        """Open a source movie via the lazy reader (:func:`tether.io.movie.open_movie`).

        Imported lazily so the headless core stays importable without the imaging
        stack present (mirrors the GUI's lazy-import discipline).
        """
        from tether.io.movie import open_movie

        return open_movie(movie_path)
