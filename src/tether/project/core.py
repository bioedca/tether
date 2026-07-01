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
    import numpy as np

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

    # --- curation (PRD §7.5; the scriptable seam behind the GUI keymap) -------

    def accept(self, molecule_key: str, **provenance: object) -> np.ndarray:
        """Accept a molecule, logging the ``/labels`` event (:func:`labels.accept`)."""
        from tether.project import labels

        return labels.accept(self.path, molecule_key, **provenance)

    def reject(self, molecule_key: str, **provenance: object) -> np.ndarray:
        """Reject a molecule (reversible sticky tag, :func:`labels.reject`)."""
        from tether.project import labels

        return labels.reject(self.path, molecule_key, **provenance)

    def unreject(self, molecule_key: str, **provenance: object) -> np.ndarray | None:
        """Un-reject a molecule; ``None`` if it was not rejected (:func:`labels.unreject`)."""
        from tether.project import labels

        return labels.unreject(self.path, molecule_key, **provenance)

    def curation_label(self, molecule_key: str) -> int:
        """The molecule's current ``curation_label`` (:func:`labels.curation_label_of`)."""
        from tether.project import labels

        return labels.curation_label_of(self.path, molecule_key)

    def read_labels(self) -> np.ndarray:
        """The ``/labels/table`` provenance log (:func:`labels.read_labels`)."""
        from tether.project import labels

        return labels.read_labels(self.path)

    def rejected_molecule_keys(self) -> set[str]:
        """The molecules currently rejected (:func:`labels.rejected_molecule_keys`)."""
        from tether.project import labels

        return labels.rejected_molecule_keys(self.path)

    # --- idealization (PRD §7.4; the one-click-vbFRET seam behind the GUI) -----

    def idealize(self, molecule_keys: list[str] | None = None, **kwargs: object):
        """Idealize selected molecules into ``/idealization`` (:func:`idealize.idealize_molecules`).

        The headless core behind the dock's ``I`` key: reads the selected molecules'
        traces, fits vbFRET / consensus VB-HMM via the sidecar, and writes the model
        back as additive data with a per-molecule input-provenance hash.
        """
        from tether.project import idealize

        return idealize.idealize_molecules(self, molecule_keys, **kwargs)  # type: ignore[arg-type]

    def read_idealization(self, model_name: str):
        """Read a persisted model (:func:`idealize.read_idealization`)."""
        from tether.project import idealize

        return idealize.read_idealization(self, model_name)

    def list_idealizations(self) -> list[str]:
        """Names of the models under ``/idealization`` (:func:`idealize.list_idealizations`)."""
        from tether.project import idealize

        return idealize.list_idealizations(self)

    def stale_idealization_keys(self, model_name: str) -> list[str]:
        """Molecules whose inputs changed since the fit (:func:`idealize.stale_molecule_keys`)."""
        from tether.project import idealize

        return idealize.stale_molecule_keys(self, model_name)
