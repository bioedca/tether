# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Deep-LASI re-analysis wizard — the executor (PRD §7.8, M7).

The M7 "New project from Deep-LASI data" workflow (PRD §7.8, goal G8) reconstructs a
round-trip-ready project *without re-extraction*. :mod:`tether.io.intake` discovers the
acquisitions, :mod:`tether.gui.deeplasi_wizard` turns that discovery into a reviewed,
user-confirmed :class:`~tether.gui.deeplasi_wizard.WizardPlan`, and **this module runs
it**: for each runnable acquisition it decodes the Deep-LASI files and drives the
already-frozen importers.

* :class:`~tether.gui.deeplasi_wizard.WizardMode` ``reconstruct`` →
  :func:`tether.project.reconstruct.reconstruct_project` — movie *provenance* (hashed
  from the raw ``.tif``, never re-extracted), the ``.mat`` pre-integrated traces, the
  recovered donor/acceptor coordinates (``.tdat`` or ``.mat`` per the plan), the ``.tdat``
  correction factors, and the SMD-curated selection.
* ``analysis_only`` → :func:`tether.project.analysis_import.import_analysis_only_project`
  — a movie-less, coordinate-less degraded import of the SMD or bare ``.txt``.

Like the controller (and :mod:`tether.gui.roundtrip`), the executor is **Qt-free**: it
touches only the plan dataclasses, the reader/importer modules, and NumPy, so it runs in
the default test matrix without a display. The QWizard widget and the shell "Import
Deep-LASI bundle…" action are a follow-up M7 PR that drive this executor through
:func:`execute_plan`.

**Coordinate source (the one place the plan's choice can degrade).** The ``.mat``
coordinates are aligned to the export's traced molecules by construction. The ``.tdat``
``ParticlesColocalized`` may span *more* molecules than the export traces (the export is
a colocalized subset), and :func:`tether.io.recover.recover_coordinates` deliberately does
not align the two. So a plan's ``"tdat"`` choice is honoured only when the ``.tdat``
molecule count already matches the export (an identity alignment — the real-bundle case
where the export covers the full colocalized set); otherwise the executor falls back to
the export-aligned ``.mat`` coordinates and records a warning, rather than fabricate a
``.tdat``→traced join. On validated UCKOPSB data the two sources are coordinate-identical
(``tests/test_recover.py``), so the fallback is faithful; a general ``.tdat``→traced
alignment is future work in :mod:`tether.io.recover`.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import numpy as np

from tether.gui.deeplasi_wizard import PlannedAcquisition, WizardMode, WizardPlan
from tether.idealize import read_smd
from tether.imaging.extract import MovieMetadata
from tether.io.deeplasi import DeepLasiExport, read_deeplasi_mat, read_deeplasi_txt
from tether.io.intake import AcquisitionFileSet
from tether.io.movie import open_movie
from tether.io.recover import (
    RecoveredCoordinates,
    SmdCoordinateMatch,
    match_smd_to_coordinates,
    recover_coordinates,
)
from tether.io.tdat import Tdat, read_tdat
from tether.project.analysis_import import (
    AnalysisOnlyImportSummary,
    import_analysis_only_project,
)
from tether.project.reconstruct import ReconstructionSummary, reconstruct_project

__all__ = [
    "ExecutedAcquisition",
    "ExecutionReport",
    "execute_plan",
]

#: 1 MiB streaming read for the movie hash — matches ``tether.project.extract._hash_movie``.
_HASH_CHUNK = 1 << 20


# --------------------------------------------------------------------------- #
# the per-acquisition + whole-run result records
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ExecutedAcquisition:
    """The outcome of importing one runnable acquisition (PRD §7.8).

    Exactly one of :attr:`reconstruct` / :attr:`analysis_only` is set on success; on
    failure both are ``None`` and :attr:`error` carries the ``"Type: message"`` reason.
    """

    #: The acquisition's grouping key (its identity in the wizard/plan).
    key: str
    #: The mode that was run (:class:`~tether.gui.deeplasi_wizard.WizardMode`).
    mode: WizardMode
    #: Where the ``.tether`` was (or would have been) written.
    output_path: Path
    #: Whether the import succeeded.
    ok: bool
    #: For a reconstruction, which coordinate source was actually used (``"tdat"`` /
    #: ``"mat"``); ``""`` for an analysis-only import or a failure before resolution.
    coordinate_source: str = ""
    #: The reconstruction summary (reconstruct mode, success).
    reconstruct: ReconstructionSummary | None = None
    #: The analysis-only import summary (analysis-only mode, success).
    analysis_only: AnalysisOnlyImportSummary | None = None
    #: A ``"Type: message"`` reason when :attr:`ok` is ``False``; ``""`` otherwise.
    error: str = ""
    #: Non-fatal advisories raised during the import (e.g. a coordinate-source fallback).
    warnings: tuple[str, ...] = ()

    @property
    def summary(self) -> ReconstructionSummary | AnalysisOnlyImportSummary | None:
        """The mode-appropriate importer summary (``None`` on failure)."""
        return self.reconstruct if self.reconstruct is not None else self.analysis_only


@dataclass(frozen=True)
class ExecutionReport:
    """The result of executing a whole :class:`~tether.gui.deeplasi_wizard.WizardPlan`."""

    #: The directory the projects were written into.
    output_dir: Path
    #: One entry per runnable acquisition, in plan order.
    executed: tuple[ExecutedAcquisition, ...] = ()

    @property
    def succeeded(self) -> tuple[ExecutedAcquisition, ...]:
        """The acquisitions that imported successfully."""
        return tuple(e for e in self.executed if e.ok)

    @property
    def failed(self) -> tuple[ExecutedAcquisition, ...]:
        """The acquisitions that failed to import."""
        return tuple(e for e in self.executed if not e.ok)

    @property
    def n_ok(self) -> int:
        """How many acquisitions imported successfully."""
        return len(self.succeeded)

    @property
    def n_failed(self) -> int:
        """How many acquisitions failed to import."""
        return len(self.failed)

    @property
    def ok(self) -> bool:
        """Whether every runnable acquisition imported (and at least one ran)."""
        return bool(self.executed) and not self.failed


# --------------------------------------------------------------------------- #
# movie provenance (hashed from the .tif, never re-extracted)
# --------------------------------------------------------------------------- #


def _hash_file(path: Path) -> tuple[str, int, float]:
    """Stream a SHA-256 over ``path`` and return ``(hex, size, mtime)``.

    The content hash is the movie's identity and seeds every ``molecule_key``; mirrors
    the private ``tether.project.extract._hash_movie`` (the executor needs the same
    provenance, but must not re-run the extraction pipeline).
    """
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK), b""):
            digest.update(chunk)
    stat = path.stat()
    return digest.hexdigest(), int(stat.st_size), float(stat.st_mtime)


def _movie_metadata(path: Path) -> MovieMetadata:
    """Build a :class:`~tether.imaging.extract.MovieMetadata` from a raw ``.tif``.

    Reconstruction imports the Deep-LASI *pre-integrated* traces and never re-opens the
    movie for pixels, so only its provenance is recovered: the content hash, frame count
    (must equal ``export.n_frames``), and on-disk geometry/dtype for the ``/movies`` row.
    This replicates the ``MovieMetadata`` assembly in
    :func:`tether.project.extract.extract_movie` without running the extraction, and
    leaves the channel geometry / calibration unset (reconstruction re-uses the legacy
    traces, so no split calibration is needed).
    """
    sha256, file_size, mtime = _hash_file(path)
    with open_movie(path) as reader:
        n_frames = len(reader)
        height = reader.height
        width = reader.width
        pixel_dtype = str(reader.dtype)
        byteorder = reader.byteorder
        frame_time = reader.frame_time or 0.0
    return MovieMetadata(
        movie_id=f"mov-{uuid4().hex}",
        sha256=sha256,
        n_frames=n_frames,
        height=height,
        width=width,
        uri=str(path),
        pixel_dtype=pixel_dtype,
        byteorder=byteorder,
        frame_time=float(frame_time),
        file_size=file_size,
        mtime=mtime,
    )


# --------------------------------------------------------------------------- #
# per-mode orchestration
# --------------------------------------------------------------------------- #


def _resolve_coordinates(
    plan: PlannedAcquisition, export: DeepLasiExport, tdat: Tdat | None
) -> tuple[RecoveredCoordinates, tuple[str, ...]]:
    """The reconstruction coordinate model + any advisories (honours the plan's source).

    Honours a ``"tdat"`` request only when the ``.tdat`` colocalized-molecule count
    already matches the export (identity alignment); otherwise — or when the ``.tdat`` is
    not two-colour donor/acceptor — falls back to the export-aligned ``.mat`` coordinates
    with a surfaced warning, never fabricating a ``.tdat``→traced join (module docstring).
    """
    if plan.coordinate_source == "tdat" and tdat is not None:
        try:
            tdat_coords = recover_coordinates(tdat=tdat)
        except ValueError as exc:
            fallback = (
                f"requested .tdat coordinates could not be recovered ({exc}); used the "
                "export-aligned .mat coordinates instead"
            )
            return recover_coordinates(mat=export), (fallback,)
        if tdat_coords.n_molecules == export.n_molecules:
            return tdat_coords, ()
        mismatch = (
            f"requested .tdat coordinates span {tdat_coords.n_molecules} colocalized "
            f"molecules but the export has {export.n_molecules} traced molecules; the "
            ".tdat→traced alignment is not yet supported — used the export-aligned .mat "
            "coordinates instead (identical on validated UCKOPSB data)"
        )
        return recover_coordinates(mat=export), (mismatch,)
    return recover_coordinates(mat=export), ()


def _match_reference(
    fileset: AcquisitionFileSet, export: DeepLasiExport
) -> tuple[np.ndarray, np.ndarray, int]:
    """The corrected donor/acceptor reference traces + frame count for the SMD match.

    A Deep-LASI ``video*.hdf5`` SMD reproduces the exported ``-donc-accc-w`` series
    **exactly** — which is the bare ``.txt`` — so when a ``.txt`` is present (and covers
    the same molecule set as the export) it is the reference: an exact match under the
    matcher's tight default tolerance. The ``.mat`` ``donc``/``accc`` carry a ~5e-6
    storage difference from the SMD, so they are only the fallback (and may under-match a
    trace). Both artifacts are row-aligned with the export, so either yields export
    molecule indices — the index-pairing key ``reconstruct_project`` marks curated.
    """
    if fileset.txt is not None:
        txt = read_deeplasi_txt(fileset.txt)
        if txt.n_molecules == export.n_molecules:
            return txt.donor_corrected, txt.acceptor_corrected, txt.n_frames
    return export.donor_corrected, export.acceptor_corrected, export.n_frames


def _curated_match(
    fileset: AcquisitionFileSet, export: DeepLasiExport, recovered: RecoveredCoordinates
) -> SmdCoordinateMatch:
    """Align a curated SMD selection to the recovered coordinates by intensity match.

    Re-resolves each SMD row to its acquisition molecule (§7.8 index-pairing key) against
    the exact-match reference traces (:func:`_match_reference`); frames are clipped to the
    common extent so the two trace sets align. Unmatched SMD rows are reported by the
    matcher, never guessed.
    """
    smd = read_smd(fileset.smd)
    donor_ref, acceptor_ref, n_frames = _match_reference(fileset, export)
    t = min(n_frames, smd.n_frames)
    reference = np.stack([donor_ref[:, :t], acceptor_ref[:, :t]], axis=-1)
    return match_smd_to_coordinates(smd.raw[:, :t, :], reference, recovered)


def _run_reconstruct(
    plan: PlannedAcquisition, out: Path, *, overwrite: bool, detect_photobleach: bool
) -> tuple[ReconstructionSummary, str, tuple[str, ...]]:
    """Decode a reconstruct acquisition and drive ``reconstruct_project`` (no re-extraction)."""
    fileset = plan.fileset
    if fileset.movie is None or fileset.mat is None:
        raise ValueError(
            f"{plan.key!r} cannot reconstruct: needs a movie and a .mat "
            f"(movie={fileset.movie is not None}, mat={fileset.mat is not None})"
        )
    export = read_deeplasi_mat(fileset.mat)
    tdat = read_tdat(fileset.tdat) if fileset.tdat is not None else None
    corrections = tdat.corrections if tdat is not None else None
    recovered, warnings = _resolve_coordinates(plan, export, tdat)
    curated = _curated_match(fileset, export, recovered) if fileset.smd is not None else None
    movie = _movie_metadata(fileset.movie)
    summary = reconstruct_project(
        out,
        export=export,
        movie=movie,
        coordinates=recovered,
        corrections=corrections,
        curated_match=curated,
        categories=plan.categories,
        detect_photobleach=detect_photobleach,
        overwrite=overwrite,
    )
    return summary, recovered.source, warnings


def _run_analysis_only(
    plan: PlannedAcquisition, out: Path, *, overwrite: bool
) -> AnalysisOnlyImportSummary:
    """Decode an analysis-only acquisition and drive ``import_analysis_only_project``."""
    fileset = plan.fileset
    if fileset.smd is not None:
        source: object = read_smd(fileset.smd)
        source_name = fileset.smd.name
    elif fileset.txt is not None:
        source = read_deeplasi_txt(fileset.txt)
        source_name = fileset.txt.name
    else:
        raise ValueError(
            f"{plan.key!r} cannot import analysis-only: no SMD or .txt intensity source"
        )
    return import_analysis_only_project(
        out,
        source=source,  # type: ignore[arg-type]  # SMDData | DeepLasiTraces, narrowed above
        source_name=source_name,
        categories=plan.categories,
        overwrite=overwrite,
    )


# --------------------------------------------------------------------------- #
# the public entry point
# --------------------------------------------------------------------------- #


def execute_plan(
    plan: WizardPlan,
    output_dir: str | Path,
    *,
    overwrite: bool = False,
    detect_photobleach: bool = True,
    raise_on_error: bool = False,
) -> ExecutionReport:
    """Execute a finalized :class:`~tether.gui.deeplasi_wizard.WizardPlan` (PRD §7.8).

    Writes one ``.tether`` per runnable acquisition into ``output_dir`` (named by each
    plan entry's ``output_name``), decoding its Deep-LASI files and driving the frozen
    importers without re-extraction (module docstring). Each import is atomic (temp →
    ``os.replace``), so a per-acquisition failure leaves no partial output.

    By default the run is **fail-soft**: a failing acquisition is recorded in the report
    and the batch continues (the wizard shows per-acquisition outcomes). Pass
    ``raise_on_error=True`` to re-raise the first failure instead.

    Parameters
    ----------
    plan:
        The finalized plan (only its runnable ``acquisitions`` are executed).
    output_dir:
        The directory the ``.tether`` projects are written into (created if absent).
    overwrite:
        Passed through to each importer; ``False`` refuses to clobber an existing file.
    detect_photobleach:
        Passed through to :func:`~tether.project.reconstruct.reconstruct_project`.
    raise_on_error:
        Re-raise the first import failure instead of recording it and continuing.

    Returns
    -------
    ExecutionReport
        One :class:`ExecutedAcquisition` per runnable acquisition, plus roll-ups.
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    results: list[ExecutedAcquisition] = []
    for acq in plan.acquisitions:
        out = out_dir / acq.output_name
        try:
            if acq.mode is WizardMode.RECONSTRUCT:
                summary, source, warnings = _run_reconstruct(
                    acq, out, overwrite=overwrite, detect_photobleach=detect_photobleach
                )
                results.append(
                    ExecutedAcquisition(
                        key=acq.key,
                        mode=acq.mode,
                        output_path=out,
                        ok=True,
                        coordinate_source=source,
                        reconstruct=summary,
                        warnings=warnings,
                    )
                )
            elif acq.mode is WizardMode.ANALYSIS_ONLY:
                ao_summary = _run_analysis_only(acq, out, overwrite=overwrite)
                results.append(
                    ExecutedAcquisition(
                        key=acq.key,
                        mode=acq.mode,
                        output_path=out,
                        ok=True,
                        analysis_only=ao_summary,
                    )
                )
            else:  # a finalized plan never carries a skip, but never trust that blindly
                raise ValueError(f"{acq.key!r} has non-runnable mode {acq.mode.value}")
        except Exception as exc:  # noqa: BLE001 — fail-soft: record + continue (atomic imports)
            if raise_on_error:
                raise
            results.append(
                ExecutedAcquisition(
                    key=acq.key,
                    mode=acq.mode,
                    output_path=out,
                    ok=False,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
    return ExecutionReport(output_dir=out_dir, executed=tuple(results))
