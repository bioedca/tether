# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reconstruct a round-trip-ready ``.tether`` project from Deep-LASI legacy data (M7).

The M7 legacy-import path (PRD §7.8) turns a paired Deep-LASI acquisition — its raw
movie, the ``TIRFdata`` ``.tdat`` (coordinates + correction factors + detection),
and the ``DeepLASI_MAT_export`` ``.mat`` (per-molecule coordinates + raw/corrected/
background traces) — into a **round-trip-ready** ``.tether`` project *without
re-extraction*. Intake + pairing ([`tether.io.intake`][], PR #122) and per-molecule
coordinate recovery + the SMD intensity cross-check ([`tether.io.recover`][], PR
#124) produce the inputs; this module is the store-writer that consumes them.

The reconstruction reuses the existing, schema-freeze-respecting writers rather than
touching HDF5 directly, so every write is **additive data** under the M0-frozen
skeleton (``schema-guard`` stays green):

* [`tether.imaging.extract.write_extraction`][] lays the ``/movies`` row, the
  ``/molecules`` rows (with the stable ``molecule_key`` = movie ``sha256`` + quantized
  ``donor_xy``, §7.10), the six ``/traces`` arrays (raw + corrected + background), and
  the two ``/patches`` arrays — every molecule linked to the movie (§7.8);
* [`tether.project.correct.compute_corrected_fret`][] stamps the **remapped** Deep-LASI
  correction factors (Appendix B: β→α, α→δ=0, γ→γ; ADR-0008) — or the apparent-E
  substrate when no valid γ was exported (ADR-0003, *never* a fabricated/degenerate γ);
* [`tether.project.photobleach.compute_photobleach`][] writes the frozen
  ``bleach_frames`` + auto ``analysis_window`` (the M3 detector, ADR-0026);
* [`tether.project.conditions.sync_conditions`][] +
  [`tether.project.conditions.set_category_list`][] materialize the ``/conditions``
  row and seed the editable category list;
* [`tether.project.labels.set_curation_label`][] +
  [`tether.project.weighting.recompute_label_weights`][] write the Deep-LASI curated
  selection into ``/labels`` as ``source=deeplasi-provisional`` at the decaying weight
  ``w₀/(1+n_human)`` (§7.5; a provisional prior *never* sets ``curation_label``).

Scope (this PR is the store-writer core; PRD §11 M7 splits the folder→project wizard
and the analysis-only degraded import into their own PRs):

* Deep-LASI **NN/HMM per-molecule category labels** are *not* written — the current
  readers do not decode them (they live in the undecoded ``.tdat`` MCOS object blob /
  unparsed ``.mat`` fields, see [`tether.io.tdat`][]); the category *vocabulary* is
  seeded from a caller-supplied list so a future MCOS-category decode can attach
  per-molecule assignments additively. This is a documented data gap, not a fabrication.
* **Patches** are written from the caller (the wizard, which opens the movie) when
  supplied, else zero-filled — the movie is linked so crops are re-cacheable on demand.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import uuid4

import numpy as np

from tether.imaging.aperture import IntegratedTraces
from tether.imaging.coloc import ColocalizedMolecules
from tether.imaging.extract import (
    MoleculeTraces,
    MovieMetadata,
    assert_appendable,
    molecule_key,
    write_extraction,
)
from tether.io.deeplasi import DeepLasiExport
from tether.io.filename import ParsedFilename, parse_filename
from tether.io.recover import RecoveredCoordinates, SmdCoordinateMatch, recover_coordinates
from tether.io.tdat import TdatCorrections
from tether.ml.weighting import DEFAULT_SEED_WEIGHT
from tether.project._guarded_publish import (
    guarded_project_write,
    publish_staged_project,
    require_publish_guard,
)
from tether.project.conditions import read_category_list, set_category_list, sync_conditions
from tether.project.core import Project
from tether.project.correct import compute_corrected_fret
from tether.project.labels import LABEL_SOURCE_DEEPLASI, CurationLabel, set_curation_label
from tether.project.lock import PublishGuardRequiredError
from tether.project.photobleach import compute_photobleach
from tether.project.weighting import recompute_label_weights

__all__ = ["ReconstructionSummary", "add_reconstruction_to_project", "reconstruct_project"]

#: The standard 21×21 aperture / integration parameters recorded in
#: ``/settings/extraction`` (matching `tether.imaging.extract.extract_molecules`
#: defaults). A reconstruction imports pre-integrated legacy traces, so these describe
#: the *provenance* of the layers, not a fresh integration this module performed.
_APERTURE_WINDOW = 21
_APERTURE_DISK_RADIUS = 3.0
_APERTURE_RING_INNER = 6.0
_APERTURE_RING_OUTER = 8.0
_APERTURE_BG_WINDOW = 10


@dataclass(frozen=True)
class ReconstructionSummary:
    """What a [`reconstruct_project`][] call produced (for logging / the wizard).

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
    """Build the [`MoleculeTraces`][tether.imaging.extract.MoleculeTraces] from legacy traces.

    The Deep-LASI ``.mat`` carries pre-integrated per-molecule traces, so the aperture
    integration [`tether.imaging.extract.extract_molecules`][] would run is skipped:
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
    finite γ ([`tether.project.correct.compute_corrected_fret`][] rejects γ≤0). The
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
    publish_guard: Callable[[], None] | None = None,
) -> ReconstructionSummary:
    """Reconstruct a round-trip-ready ``.tether`` from a Deep-LASI acquisition (§7.8).

    Builds the project atomically at a sibling temp path and ``os.replace``\\ s it into
    place only on full success (the multi-step write is otherwise non-atomic), mirroring
    [`tether.project.extract.extract_movie`][].

    Parameters
    ----------
    output_path:
        Destination ``.tether`` path (a pre-existing file is replaced only when
        ``overwrite`` is set).
    export:
        The decoded Deep-LASI ``.mat`` ([`tether.io.deeplasi.read_deeplasi_mat`][]) —
        the source of the per-molecule raw/corrected/background traces (and, by default,
        the coordinates). Its molecule count sets ``n_molecules``.
    movie:
        The linked movie's provenance ([`MovieMetadata`][tether.imaging.extract.MovieMetadata]);
        its ``sha256`` seeds every ``molecule_key`` and its ``n_frames`` **must** equal
        ``export.n_frames`` (they describe the same movie). The wizard builds this by
        hashing the paired raw ``.tif``.
    coordinates:
        Recovered per-molecule coordinates, index-aligned with ``export`` traces. When
        ``None`` (default), recovered from ``export`` itself
        ([`tether.io.recover.recover_coordinates`][] — ``source="mat"``); pass a
        ``.tdat``-sourced [`RecoveredCoordinates`][tether.io.recover.RecoveredCoordinates] (aligned
        to the
        traced molecules) to reconstruct *from ``.tdat`` coordinates*. Must have
        ``n_molecules == export.n_molecules``.
    corrections:
        The remapped Deep-LASI correction factors
        ([`tether.io.tdat.Tdat.corrections`][]). Injected as ``METHOD_MANUAL`` α/γ when
        a usable γ (>0) is present; otherwise the apparent-E substrate is stamped
        explicitly (``METHOD_APPARENT_UNAVAILABLE``, never a NaN E — ADR-0003).
    parsed:
        The provisional filename parse supplying ``condition_id`` + ``source_filename``;
        defaults to [`tether.io.filename.parse_filename`][] of the export's movie name.
    curated_match:
        The SMD intensity cross-check ([`tether.io.recover.match_smd_to_coordinates`][])
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
    publish_guard:
        Ownership check called after the new project is complete and immediately
        before its atomic publish; an exception aborts the publish and leaves an
        existing destination unchanged. It is **required** to replace an existing
        ``output_path`` (#346, ADR-0112): hold
        [`publish_lock`][tether.project.lock.publish_lock] on ``output_path`` and pass
        the check it yields.

    Returns
    -------
    ReconstructionSummary
        Counts + provenance flags describing the reconstruction.

    Raises
    ------
    FileExistsError
        If ``output_path`` exists and ``overwrite`` is ``False``.
    tether.project.lock.PublishGuardRequiredError
        If ``overwrite`` would replace an existing project without a
        ``publish_guard``, so the atomic publish cannot bypass the single-writer
        invariant (§5.4) that ``Project.create(overwrite=True)`` would otherwise assert.
    ValueError
        If ``coordinates`` are not aligned with ``export`` (row count mismatch), or
        ``movie.n_frames != export.n_frames``.
    """
    output_path = Path(output_path)
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"{output_path} exists (pass overwrite=True to replace)")
    # The atomic publish below is destructive, and the store is built at a fresh
    # tmp_path, so Project.create never sees output_path. Replacing an existing project
    # therefore needs the caller to hold its writer lock (#346, ADR-0112, §5.4).
    require_publish_guard(output_path, publish_guard)

    tmp_path = output_path.with_name(f"{output_path.name}.{uuid4().hex}.tmp")
    try:
        Project.create(tmp_path, overwrite=True)
        summary = _reconstruct_into(
            tmp_path,
            export=export,
            movie=movie,
            coordinates=coordinates,
            corrections=corrections,
            parsed=parsed,
            curated_match=curated_match,
            categories=categories,
            donor_patches=donor_patches,
            acceptor_patches=acceptor_patches,
            detect_photobleach=detect_photobleach,
            scope_to_movie=False,
        )
        publish_staged_project(tmp_path, output_path, publish_guard=publish_guard)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return replace(summary, output_path=output_path)


def add_reconstruction_to_project(
    project_path: str | Path,
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
    publish_guard: Callable[[], None] | None,
) -> ReconstructionSummary:
    """Append one Deep-LASI acquisition to an existing ``.tether`` project (#347).

    The append path of [`reconstruct_project`][]: the same writer and the same
    passes, so a Deep-LASI re-analysis of several acquisitions can build one project
    instead of one file per acquisition. The acquisition gains its own ``/movies``
    row and its molecules are appended in the positional trace↔molecule order. Its
    correction factors and its photobleach detection are applied **to its own
    molecules only**, so the molecules already in the project keep their factors,
    methods, bleach frames and windows (ADR-0117). Its category seeds are added to
    the condition's list after the names already there, and its curated selection
    is written as ``deeplasi-provisional`` labels as for a new project.

    The update is written to a sibling copy and published by one atomic replace
    under the project's writer lock; a guard that fails, or any other error, leaves
    the project byte-identical.

    Parameters
    ----------
    project_path:
        An existing, compatible ``.tether`` project.
    export, movie, coordinates, corrections, parsed, curated_match, categories, \
donor_patches, acceptor_patches, detect_photobleach:
        As for [`reconstruct_project`][]. ``movie.movie_id`` must be new to the
        project, and so must ``movie.sha256``.
    publish_guard:
        **Required.** The check yielded by
        [`publish_lock`][tether.project.lock.publish_lock] held on ``project_path``
        (#346, ADR-0112).

    Returns
    -------
    ReconstructionSummary
        The appended acquisition's counts; ``output_path`` is ``project_path``.

    Raises
    ------
    FileNotFoundError
        If ``project_path`` does not exist.
    tether.project.lock.PublishGuardRequiredError
        If ``publish_guard`` is ``None``.
    ValueError
        If the movie (its ``movie_id`` or its content ``sha256``) is already in the
        project, the inputs do not align (as for [`reconstruct_project`][]), the
        project is not a compatible ``.tether``, or its ``/traces`` or ``/patches``
        arrays do not cover every molecule (an analysis-only import, a subset exported
        without its raw layers).
    """
    project_path = Path(project_path)
    if not project_path.is_file():
        raise FileNotFoundError(f"{project_path} does not exist (reconstruct_project creates one)")
    assert_appendable(project_path)
    if publish_guard is None:
        raise PublishGuardRequiredError(
            f"appending to {project_path.name} needs its writer lock "
            "(hold tether.project.lock.publish_lock and pass its check as publish_guard)"
        )
    _refuse_duplicate_movie(project_path, movie)

    with guarded_project_write(project_path, write_guard=publish_guard, label="append") as stage:
        _refuse_duplicate_movie(stage, movie)
        summary = _reconstruct_into(
            stage,
            export=export,
            movie=movie,
            coordinates=coordinates,
            corrections=corrections,
            parsed=parsed,
            curated_match=curated_match,
            categories=categories,
            donor_patches=donor_patches,
            acceptor_patches=acceptor_patches,
            detect_photobleach=detect_photobleach,
            scope_to_movie=True,
        )
    return replace(summary, output_path=project_path)


def _refuse_duplicate_movie(project_path: Path, movie: MovieMetadata) -> None:
    """Refuse a movie already in the project by id or by content (#347, ADR-0117).

    A second copy of the same content would give every molecule the
    ``molecule_key`` (movie ``sha256`` + donor position) of one already stored, so
    labels keyed by ``molecule_key`` could no longer tell the two apart.
    """
    import h5py  # noqa: PLC0415

    from tether.io.schema import TABLE  # noqa: PLC0415

    with h5py.File(project_path, "r") as f:
        table = f["movies"][TABLE][:]
    for row in table:
        movie_id = _decode(row["movie_id"])
        if movie_id == movie.movie_id or _decode(row["sha256"]).lower() == movie.sha256.lower():
            raise ValueError(
                f"movie {movie.movie_id!r} is already in {project_path.name} as {movie_id!r} "
                "(same movie_id or content sha256); a movie is appended once"
            )


def _decode(value: object) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _reconstruct_into(
    path: Path,
    *,
    export: DeepLasiExport,
    movie: MovieMetadata,
    coordinates: RecoveredCoordinates | None,
    corrections: TdatCorrections | None,
    parsed: ParsedFilename | None,
    curated_match: SmdCoordinateMatch | None,
    categories: Sequence[str],
    donor_patches: np.ndarray | None,
    acceptor_patches: np.ndarray | None,
    detect_photobleach: bool,
    scope_to_movie: bool,
) -> ReconstructionSummary:
    """Write one acquisition into the existing project at ``path`` and run its passes.

    Shared by [`reconstruct_project`][] (a fresh project) and
    [`add_reconstruction_to_project`][] (an existing one). With ``scope_to_movie``
    the correction and photobleach passes cover this movie's molecules only, and the
    category seeds extend the condition's list instead of replacing it.
    """
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

    write_extraction(
        path,
        movie=movie,
        molecules=molecules,
        traces=traces,
        parsed=parsed,
        settings=settings,
    )
    scope = movie.movie_id if scope_to_movie else None
    corrections_applied = _apply_corrections(path, corrections, scope)
    n_donor_bleached, n_acceptor_bleached = _apply_photobleach(path, detect_photobleach, scope)
    _seed_categories(path, parsed.condition_id, categories, extend=scope_to_movie)
    n_curated = _seed_curated_selection(path, movie, coordinates, curated_match, parsed)

    return ReconstructionSummary(
        output_path=path,
        movie_id=movie.movie_id,
        coordinate_source=coordinates.source,
        n_molecules=export.n_molecules,
        n_curated=n_curated,
        n_categories=len(categories),
        corrections_applied=corrections_applied,
        n_donor_bleached=n_donor_bleached,
        n_acceptor_bleached=n_acceptor_bleached,
    )


def _apply_corrections(
    path: Path, corrections: TdatCorrections | None, movie_id: str | None = None
) -> bool:
    """Stamp the remapped correction factors, or the explicit apparent-E substrate.

    Always runs [`tether.project.correct.compute_corrected_fret`][] so the project
    records a definite correction method + ``/settings/correction`` provenance: with the
    remapped α/γ overrides (``METHOD_MANUAL``) when a usable γ exists, else with no
    override so each molecule is stamped ``METHOD_APPARENT_UNAVAILABLE`` (ADR-0003).
    Returns whether real factors were injected.
    """
    if _corrections_usable(corrections):
        assert corrections is not None  # narrowed by _corrections_usable
        compute_corrected_fret(
            path,
            alpha_override=corrections.alpha,
            gamma_override=corrections.gamma,
            movie_id=movie_id,
        )
        return True
    compute_corrected_fret(path, movie_id=movie_id)
    return False


def _apply_photobleach(path: Path, detect: bool, movie_id: str | None = None) -> tuple[int, int]:
    """Run the M3 photobleach detector (bleach_frames + analysis_window) if requested."""
    if not detect:
        return 0, 0
    summary = compute_photobleach(path, movie_id=movie_id)
    return int(summary.n_donor_bleached), int(summary.n_acceptor_bleached)


def _seed_categories(
    path: Path, condition_id: str, categories: Sequence[str], *, extend: bool = False
) -> None:
    """Materialize the ``/conditions`` row then seed its editable category vocabulary.

    With ``extend`` (an append), names already in the condition's list keep their
    order and integer codes, and only new names are added after them: replacing the
    list would recode categories other movies' molecules already use.
    """
    sync_conditions(path)
    if not categories:
        return
    names = [name.strip() if isinstance(name, str) else name for name in categories]
    if extend:
        # Compare stripped names, as set_category_list does, so a seed that differs from
        # a stored name only by surrounding whitespace is the same category.
        existing = list(read_category_list(path, condition_id).categories)
        names = existing + [name for name in names if name not in existing]
    set_category_list(path, condition_id, names)


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
