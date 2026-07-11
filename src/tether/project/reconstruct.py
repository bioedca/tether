# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reconstruct a round-trip-ready ``.tether`` project from Deep-LASI legacy data (M7).

The M7 legacy-import path (PRD §7.8) turns a paired Deep-LASI acquisition — its raw
movie, the ``TIRFdata`` ``.tdat`` (coordinates + correction factors + detection),
and the ``DeepLASI_MAT_export`` ``.mat`` (per-molecule coordinates + raw/corrected/
background traces) — into a **round-trip-ready** ``.tether`` project *without
re-extraction*. Intake + pairing (:mod:`tether.io.intake`, PR #122) and per-molecule
coordinate recovery + the SMD intensity cross-check (:mod:`tether.io.recover`, PR
#124) produce the inputs; this module is the store-writer that consumes them.

The reconstruction reuses the existing, schema-freeze-respecting writers rather than
touching HDF5 directly, so every write is **additive data** under the M0-frozen
skeleton (``schema-guard`` stays green):

* :func:`tether.imaging.extract.write_extraction` lays the ``/movies`` row, the
  ``/molecules`` rows (with the stable ``molecule_key`` = movie ``sha256`` + quantized
  ``donor_xy``, §7.10), the six ``/traces`` arrays (raw + corrected + background), and
  the two ``/patches`` arrays — every molecule linked to the movie (§7.8);
* :func:`tether.project.correct.compute_corrected_fret` stamps the **remapped** Deep-LASI
  correction factors (Appendix B: β→α, α→δ=0, γ→γ; ADR-0008) — or the apparent-E
  substrate when no valid γ was exported (ADR-0003, *never* a fabricated/degenerate γ);
* :func:`tether.project.photobleach.compute_photobleach` writes the frozen
  ``bleach_frames`` + auto ``analysis_window`` (the M3 detector, ADR-0026);
* :func:`tether.project.conditions.sync_conditions` +
  :func:`tether.project.conditions.set_category_list` materialize the ``/conditions``
  row and seed the editable category list;
* :func:`tether.project.labels.set_curation_label` +
  :func:`tether.project.weighting.recompute_label_weights` write the Deep-LASI curated
  selection into ``/labels`` as ``source=deeplasi-provisional`` at the decaying weight
  ``w₀/(1+n_human)`` (§7.5; a provisional prior *never* sets ``curation_label``).

Scope (this PR is the store-writer core; PRD §11 M7 splits the folder→project wizard
and the analysis-only degraded import into their own PRs):

* Deep-LASI **NN/HMM per-molecule category labels** are *not* written — the current
  readers do not decode them (they live in the undecoded ``.tdat`` MCOS object blob /
  unparsed ``.mat`` fields, see :mod:`tether.io.tdat`); the category *vocabulary* is
  seeded from a caller-supplied list so a future MCOS-category decode can attach
  per-molecule assignments additively. This is a documented data gap, not a fabrication.
* **Patches** are written from the caller (the wizard, which opens the movie) when
  supplied, else zero-filled — the movie is linked so crops are re-cacheable on demand.
"""

from __future__ import annotations

import math
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import numpy as np

from tether.imaging.aperture import IntegratedTraces
from tether.imaging.coloc import ColocalizedMolecules
from tether.imaging.extract import (
    MoleculeTraces,
    MovieMetadata,
    molecule_key,
    write_extraction,
)
from tether.io.deeplasi import DeepLasiExport
from tether.io.filename import ParsedFilename, parse_filename
from tether.io.recover import RecoveredCoordinates, SmdCoordinateMatch, recover_coordinates
from tether.io.tdat import TdatCorrections
from tether.ml.weighting import DEFAULT_SEED_WEIGHT
from tether.project.conditions import set_category_list, sync_conditions
from tether.project.core import Project
from tether.project.correct import compute_corrected_fret
from tether.project.labels import LABEL_SOURCE_DEEPLASI, CurationLabel, set_curation_label
from tether.project.photobleach import compute_photobleach
from tether.project.weighting import recompute_label_weights

__all__ = ["ReconstructionSummary", "reconstruct_project"]

#: The standard 21×21 aperture / integration parameters recorded in
#: ``/settings/extraction`` (matching :func:`tether.imaging.extract.extract_molecules`
#: defaults). A reconstruction imports pre-integrated legacy traces, so these describe
#: the *provenance* of the layers, not a fresh integration this module performed.
_APERTURE_WINDOW = 21
_APERTURE_DISK_RADIUS = 3.0
_APERTURE_RING_INNER = 6.0
_APERTURE_RING_OUTER = 8.0
_APERTURE_BG_WINDOW = 10


@dataclass(frozen=True)
class ReconstructionSummary:
    """What a :func:`reconstruct_project` call produced (for logging / the wizard).

    Attributes
    ----------
    output_path:
        The written ``.tether`` project.
    movie_id:
        The ``/movies`` row id every molecule links to.
    coordinate_source:
        Which artifact the per-molecule coordinates came from (``"tdat"`` | ``"mat"``).
    n_molecules:
        Molecules written (rows in ``/molecules`` = trace rows = the export's count).
    n_curated:
        Deep-LASI curated-selection molecules written as ``deeplasi-provisional``
        accept labels into ``/labels`` (0 when no SMD cross-check was supplied).
    n_categories:
        Category-list seeds written to the condition's editable vocabulary.
    corrections_applied:
        ``True`` when a valid remapped γ was injected (``METHOD_MANUAL``); ``False``
        when the export carried no usable γ, leaving the apparent-E substrate.
    n_donor_bleached, n_acceptor_bleached:
        Photobleach steps the M3 detector found (0 when ``detect_photobleach=False``).
    """

    output_path: Path
    movie_id: str
    coordinate_source: str
    n_molecules: int
    n_curated: int
    n_categories: int
    corrections_applied: bool
    n_donor_bleached: int
    n_acceptor_bleached: int


def _traces_from_export(
    export: DeepLasiExport,
    donor_patches: np.ndarray | None,
    acceptor_patches: np.ndarray | None,
) -> MoleculeTraces:
    """Build the :class:`~tether.imaging.extract.MoleculeTraces` from legacy traces.

    The Deep-LASI ``.mat`` carries pre-integrated per-molecule traces, so the aperture
    integration :func:`tether.imaging.extract.extract_molecules` would run is skipped:
    the corrected series (``donc``/``accc``) becomes the ``intensity`` (corrected)
    layer, the raw series (``don``/``acc``) the ``total`` (raw) layer, and the
    background (``bdon``/``bacc``) the ``background`` layer. Every row is ``valid``
    (write_extraction forbids an all-zero trace), the positional trace↔molecule join
    the writer relies on being preserved row-for-row from the export.
    """
    n = export.n_molecules
    valid = np.ones(n, dtype=bool)
    donor = IntegratedTraces(
        intensity=export.donor_corrected,
        total=export.donor_raw,
        background=export.donor_background,
        valid=valid,
    )
    acceptor = IntegratedTraces(
        intensity=export.acceptor_corrected,
        total=export.acceptor_raw,
        background=export.acceptor_background,
        valid=valid.copy(),
    )
    dpatch = _resolve_patches(donor_patches, n, "donor_patches")
    apatch = _resolve_patches(acceptor_patches, n, "acceptor_patches")
    return MoleculeTraces(
        donor=donor,
        acceptor=acceptor,
        donor_patches=dpatch,
        acceptor_patches=apatch,
        window=_APERTURE_WINDOW,
        disk_radius=_APERTURE_DISK_RADIUS,
        ring_inner=_APERTURE_RING_INNER,
        ring_outer=_APERTURE_RING_OUTER,
        bg_window=_APERTURE_BG_WINDOW,
    )


def _resolve_patches(patches: np.ndarray | None, n: int, name: str) -> np.ndarray:
    """A supplied ``(N, w, w)`` patch stack, or a zero-filled one (movie re-cacheable)."""
    if patches is None:
        return np.zeros((n, _APERTURE_WINDOW, _APERTURE_WINDOW), dtype=np.float32)
    arr = np.asarray(patches, dtype=np.float32)
    expected = (n, _APERTURE_WINDOW, _APERTURE_WINDOW)
    if arr.shape != expected:
        raise ValueError(f"{name} must be {expected}, got {arr.shape}")
    return arr


def _colocalized_from_coordinates(coordinates: RecoveredCoordinates) -> ColocalizedMolecules:
    """Wrap recovered donor/acceptor coordinates as a donor-anchored molecule list.

    The molecules are two-colour colocalized by construction (the recovery drops any
    non-two-colour ``.tdat``), so ``acceptor_detected`` is all-``True``; the source
    detection indices are unavailable (this is a recovered, not freshly-detected, set)
    so the informational ``donor_index`` / ``acceptor_index`` are ``arange(N)``.
    """
    n = coordinates.n_molecules
    return ColocalizedMolecules(
        donor_xy=np.ascontiguousarray(coordinates.donor_xy, dtype=np.float64),
        acceptor_xy=np.ascontiguousarray(coordinates.acceptor_xy, dtype=np.float64),
        acceptor_detected=np.ones(n, dtype=bool),
        donor_index=np.arange(n, dtype=np.int64),
        acceptor_index=np.arange(n, dtype=np.int64),
    )


def _corrections_usable(corrections: TdatCorrections | None) -> bool:
    """Whether the remapped Deep-LASI factors can be injected as an absolute correction.

    A valid absolute γ-correction needs a finite leakage α and a **strictly positive**
    finite γ (:func:`tether.project.correct.compute_corrected_fret` rejects γ≤0). The
    committed Cy3-only fixture carries ``DefaultGamma=0`` → remapped γ=0, so it (rightly)
    reconstructs as an apparent-E substrate rather than a degenerate γ.
    """
    if corrections is None:
        return False
    return (
        math.isfinite(corrections.alpha)
        and math.isfinite(corrections.gamma)
        and corrections.gamma > 0.0
    )


def reconstruct_project(
    output_path: str | Path,
    *,
    export: DeepLasiExport,
    movie: MovieMetadata,
    coordinates: RecoveredCoordinates | None = None,
    corrections: TdatCorrections | None = None,
    parsed: ParsedFilename | None = None,
    curated_match: SmdCoordinateMatch | None = None,
    categories: Sequence[str] = (),
    donor_patches: np.ndarray | None = None,
    acceptor_patches: np.ndarray | None = None,
    detect_photobleach: bool = True,
    overwrite: bool = False,
) -> ReconstructionSummary:
    """Reconstruct a round-trip-ready ``.tether`` from a Deep-LASI acquisition (§7.8).

    Builds the project atomically at a sibling temp path and ``os.replace``\\ s it into
    place only on full success (the multi-step write is otherwise non-atomic), mirroring
    :func:`tether.project.extract.extract_movie`.

    Parameters
    ----------
    output_path:
        Destination ``.tether`` path (a pre-existing file is replaced only when
        ``overwrite`` is set).
    export:
        The decoded Deep-LASI ``.mat`` (:func:`tether.io.deeplasi.read_deeplasi_mat`) —
        the source of the per-molecule raw/corrected/background traces (and, by default,
        the coordinates). Its molecule count sets ``n_molecules``.
    movie:
        The linked movie's provenance (:class:`~tether.imaging.extract.MovieMetadata`);
        its ``sha256`` seeds every ``molecule_key`` and its ``n_frames`` **must** equal
        ``export.n_frames`` (they describe the same movie). The wizard builds this by
        hashing the paired raw ``.tif``.
    coordinates:
        Recovered per-molecule coordinates, index-aligned with ``export`` traces. When
        ``None`` (default), recovered from ``export`` itself
        (:func:`tether.io.recover.recover_coordinates` — ``source="mat"``); pass a
        ``.tdat``-sourced :class:`~tether.io.recover.RecoveredCoordinates` (aligned to the
        traced molecules) to reconstruct *from ``.tdat`` coordinates*. Must have
        ``n_molecules == export.n_molecules``.
    corrections:
        The remapped Deep-LASI correction factors
        (:attr:`tether.io.tdat.Tdat.corrections`). Injected as ``METHOD_MANUAL`` α/γ when
        a usable γ (>0) is present; otherwise the apparent-E substrate is stamped
        explicitly (``METHOD_APPARENT_UNAVAILABLE``, never a NaN E — ADR-0003).
    parsed:
        The provisional filename parse supplying ``condition_id`` + ``source_filename``;
        defaults to :func:`tether.io.filename.parse_filename` of the export's movie name.
    curated_match:
        The SMD intensity cross-check (:func:`tether.io.recover.match_smd_to_coordinates`)
        identifying the Deep-LASI **curated selection**; each matched molecule is written
        as a ``deeplasi-provisional`` accept into ``/labels`` (never ``curation_label``).
    categories:
        Editable-category-list seeds for the condition's vocabulary (the Deep-LASI class
        names). Per-molecule NN/HMM assignment is deferred (readers do not decode it yet).
    donor_patches, acceptor_patches:
        Optional ``(N, 21, 21)`` cached image patches (the wizard supplies these from the
        movie); zero-filled when ``None`` (the movie link makes crops re-cacheable).
    detect_photobleach:
        Run the M3 photobleach detector on the imported corrected traces to write the
        frozen ``bleach_frames`` + auto ``analysis_window`` (default ``True``).
    overwrite:
        Replace an existing ``output_path`` (default ``False`` refuses to clobber).

    Returns
    -------
    ReconstructionSummary
        Counts + provenance flags describing the reconstruction.

    Raises
    ------
    FileExistsError
        If ``output_path`` exists and ``overwrite`` is ``False``.
    tether.project.lock.LockedError
        If ``overwrite`` replaces an existing project a **foreign** ``.lock`` holds
        (an open GUI session), so the atomic publish cannot bypass the single-writer
        invariant (§5.4) that ``Project.create(overwrite=True)`` would otherwise assert.
    ValueError
        If ``coordinates`` are not aligned with ``export`` (row count mismatch), or
        ``movie.n_frames != export.n_frames``.
    """
    output_path = Path(output_path)
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"{output_path} exists (pass overwrite=True to replace)")
    if output_path.exists():
        # The atomic publish (os.replace onto output_path below) is destructive, but the
        # store is built at a fresh tmp_path so Project.create never sees output_path and
        # its overwrite=True foreign-lock guard is skipped. Re-assert it here, matching
        # every other overwrite path, so a project held open by another writer is not
        # silently clobbered (§5.4).
        from tether.project import lock  # noqa: PLC0415

        lock.assert_writable(output_path)

    if coordinates is None:
        coordinates = recover_coordinates(mat=export)
    if coordinates.n_molecules != export.n_molecules:
        raise ValueError(
            f"coordinates ({coordinates.n_molecules} molecules) must align with the "
            f"export traces ({export.n_molecules}); reconstruction is a positional join"
        )
    if int(movie.n_frames) != int(export.n_frames):
        raise ValueError(
            f"movie.n_frames ({movie.n_frames}) != export trace width ({export.n_frames}); "
            "they must describe the same movie"
        )

    if parsed is None:
        parsed = parse_filename(export.movie_name or f"{movie.movie_id}.tif")

    molecules = _colocalized_from_coordinates(coordinates)
    traces = _traces_from_export(export, donor_patches, acceptor_patches)
    settings = {
        "source": "m7-deeplasi-reconstruction",
        "coordinate_source": coordinates.source,
        "movie_name": export.movie_name,
        "exported_by": export.exported_by,
    }

    tmp_path = output_path.with_name(f"{output_path.name}.{uuid4().hex}.tmp")
    try:
        Project.create(tmp_path, overwrite=True)
        write_extraction(
            tmp_path,
            movie=movie,
            molecules=molecules,
            traces=traces,
            parsed=parsed,
            settings=settings,
        )
        corrections_applied = _apply_corrections(tmp_path, corrections)
        n_donor_bleached, n_acceptor_bleached = _apply_photobleach(tmp_path, detect_photobleach)
        _seed_categories(tmp_path, parsed.condition_id, categories)
        n_curated = _seed_curated_selection(tmp_path, movie, coordinates, curated_match, parsed)
        os.replace(tmp_path, output_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise

    return ReconstructionSummary(
        output_path=output_path,
        movie_id=movie.movie_id,
        coordinate_source=coordinates.source,
        n_molecules=export.n_molecules,
        n_curated=n_curated,
        n_categories=len(categories),
        corrections_applied=corrections_applied,
        n_donor_bleached=n_donor_bleached,
        n_acceptor_bleached=n_acceptor_bleached,
    )


def _apply_corrections(path: Path, corrections: TdatCorrections | None) -> bool:
    """Stamp the remapped correction factors, or the explicit apparent-E substrate.

    Always runs :func:`tether.project.correct.compute_corrected_fret` so the project
    records a definite correction method + ``/settings/correction`` provenance: with the
    remapped α/γ overrides (``METHOD_MANUAL``) when a usable γ exists, else with no
    override so each molecule is stamped ``METHOD_APPARENT_UNAVAILABLE`` (ADR-0003).
    Returns whether real factors were injected.
    """
    if _corrections_usable(corrections):
        assert corrections is not None  # narrowed by _corrections_usable
        compute_corrected_fret(
            path, alpha_override=corrections.alpha, gamma_override=corrections.gamma
        )
        return True
    compute_corrected_fret(path)
    return False


def _apply_photobleach(path: Path, detect: bool) -> tuple[int, int]:
    """Run the M3 photobleach detector (bleach_frames + analysis_window) if requested."""
    if not detect:
        return 0, 0
    summary = compute_photobleach(path)
    return int(summary.n_donor_bleached), int(summary.n_acceptor_bleached)


def _seed_categories(path: Path, condition_id: str, categories: Sequence[str]) -> None:
    """Materialize the ``/conditions`` row then seed its editable category vocabulary."""
    sync_conditions(path)
    if categories:
        set_category_list(path, condition_id, list(categories))


def _seed_curated_selection(
    path: Path,
    movie: MovieMetadata,
    coordinates: RecoveredCoordinates,
    curated_match: SmdCoordinateMatch | None,
    parsed: ParsedFilename,
) -> int:
    """Write the Deep-LASI curated subset as ``deeplasi-provisional`` accept labels.

    The SMD is the tMAVEN/Deep-LASI curated export, so each acquisition molecule an SMD
    trace matched (by the exact intensity cross-check) was curated-in — recorded as a
    provisional accept prior (which decays as human labels accrue, §7.5), never as a
    human ``curation_label``. Nothing is guessed for the unmatched rows.
    """
    if curated_match is None:
        return 0
    source_file = parsed.source_filename or None
    for _smd_row, acq_idx in curated_match.matched:
        key = molecule_key(movie.sha256, coordinates.donor_xy[acq_idx])
        set_curation_label(
            path,
            key,
            CurationLabel.ACCEPT,
            source=LABEL_SOURCE_DEEPLASI,
            weight=DEFAULT_SEED_WEIGHT,
            source_file=source_file,
        )
    n_curated = len(curated_match.matched)
    if n_curated:
        # Normalize every provisional weight to the decaying law w₀/(1+n_human) (§7.5);
        # the seed weight passed above is provisional until this pass.
        recompute_label_weights(path)
    return n_curated
