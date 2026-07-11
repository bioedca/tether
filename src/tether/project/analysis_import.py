# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Analysis-only import of a coordinate-less SMD / ``.txt`` source (M7, PRD §7.8/§5.3).

The **degraded branch** of the M7 legacy path (the sibling of the round-trip
reconstruction in :mod:`tether.project.reconstruct`, ADR-0045). A raw
``.txt``-sourced tMAVEN SMD imported standalone — no ``.tdat``, no ``.mat``, no
movie, e.g. the M6 281-molecule parity fixture — carries **neither coordinates nor
patches** (PRD §5.3, §7.8). It is therefore imported as an explicit
**analysis-only project**: idealization, FRET histograms, TDP, and kinetics are
fully usable (exactly what M6 parity needs), but the trace↔movie round-trip browser
(§7.3) and the patch-dependent movie-less curation are **disabled**, a one-time
banner announces *"coordinates and patches absent; movie round-trip and spot/overlap
views unavailable"*, and every molecule is tagged ``round-trip-unavailable`` in
provenance. This is the branch distinct from the Deep-LASI-bundle re-analysis path
(ADR-0045), which re-imports **with** coordinates intact.

Design (why a new writer, not :func:`tether.imaging.extract.write_extraction`)
------------------------------------------------------------------------------
``write_extraction`` cannot serve this case: it mandates a valid
:class:`~tether.imaging.extract.MovieMetadata` (non-empty ``movie_id``, positive
dims), unconditionally appends a ``/movies`` row, requires finite per-molecule
coordinates (its ``molecule_key`` is the movie ``sha256`` + quantized ``donor_xy``,
which would collide to one identical key for every molecule when coordinates are
absent — breaking the ``/labels`` join), and refuses an all-zero trace. An
analysis-only source has none of that. Fabricating a stub movie or fake coordinates
to satisfy it would violate the "never fabricate" rule (PLAN §0.4). So this module
is a small, honest, **movie-less** writer that (like :func:`export_subset_tether`,
which proves a ``/movies``-row-less store is first-class) writes only additive data
under the M0-frozen skeleton (``schema-guard`` stays green — no ``schema.py`` touch):

* **``/molecules/table``** — one row per SMD/``.txt`` trace: a fresh stable-UUID
  ``molecule_id``; a synthesized **unique, deterministic** ``molecule_key`` (a
  content+provenance identity hash of the source id + row index + the raw
  donor/acceptor trace bytes — an identity hash, never fabricated coordinate data);
  ``movie_id = ""`` (movie-less); ``donor_xy``/``acceptor_xy`` = ``NaN``
  (coordinates genuinely **absent** — a ``NaN`` sentinel, never a fake ``[0, 0]``);
  the per-molecule ``analysis_window`` from the SMD's tMAVEN ``pre_list``/``post_list``
  when present, else the full native window; the provisional ``condition_id`` parsed
  from the source filename; and ``tags = "round-trip-unavailable"``.
* **``/traces/{donor,acceptor}_corrected``** — the SMD/``.txt`` intensity series as
  the **apparent-E analysis substrate** (the ``intensity_quantity="corrected"`` layer
  every analysis consumer reads by default). No ``raw``/``background`` layers are
  synthesized (there is no movie to decompose the intensities against) and no
  ``/patches`` are written — both genuinely absent.
* **``/settings/analysis_only``** — an additive project-level marker recording
  ``round_trip_available = False`` + the one-time banner text + source provenance, so
  the GUI wizard (a later M7 PR) can gate the round-trip/patch views off an O(1) read.
* **``/molecules.correction_method``** is stamped ``METHOD_APPARENT_UNAVAILABLE`` via
  :func:`tether.project.correct.compute_corrected_fret` (α/γ left ``NaN``): the honest
  apparent-E substrate (ADR-0003), never a fabricated correction factor.
* **``/conditions``** materialized (+ an optional seeded category vocabulary) so
  condition-scoped analysis works.

The build is atomic — a sibling temp file → :func:`os.replace` — and re-asserts the
single-writer lock when ``overwrite`` replaces an existing project (§5.4), mirroring
:func:`tether.project.reconstruct.reconstruct_project`.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import numpy as np

from tether.idealize.smd import SMDData
from tether.io.deeplasi import DeepLasiTraces
from tether.io.filename import ParsedFilename, parse_filename
from tether.io.schema import TABLE
from tether.project.conditions import set_category_list, sync_conditions
from tether.project.core import Project
from tether.project.correct import compute_corrected_fret

__all__ = [
    "ANALYSIS_ONLY_BANNER",
    "ANALYSIS_ONLY_TAG",
    "AnalysisOnlyImportSummary",
    "AnalysisOnlyMarker",
    "import_analysis_only_project",
    "read_analysis_only_marker",
]

#: The per-molecule provenance tag written into the frozen ``/molecules.tags`` field
#: for every analysis-only-imported molecule (PRD §7.8). Comma-joinable with any other
#: tag; queryable via :func:`tether.analysis.query.query_molecules`.
ANALYSIS_ONLY_TAG = "round-trip-unavailable"

#: The one-time banner text (PRD §7.8) recorded in the ``/settings/analysis_only``
#: marker so the GUI wizard can surface it once on open.
ANALYSIS_ONLY_BANNER = (
    "coordinates and patches absent; movie round-trip and spot/overlap views unavailable"
)

#: The float storage dtype for the cached ``/traces`` arrays (matches
#: :mod:`tether.imaging.extract`: ``float32`` is ample for disk-sum intensities and
#: halves the store; gzip-compressed + chunked).
_TRACE_DTYPE = "<f4"

#: Sentinel for the (not-run) M3 photobleach fields — the same "-1 not detected"
#: convention :func:`tether.imaging.extract.write_extraction` writes.
_UNDETECTED_FRAME = -1

_SETTINGS_GROUP = "settings"
_ANALYSIS_ONLY_SETTINGS = "analysis_only"
_MOLECULE_KEY_PREFIX = "tether-analysis-only"


@dataclass(frozen=True)
class AnalysisOnlyImportSummary:
    """What an :func:`import_analysis_only_project` call produced (for logging / the wizard).

    Attributes
    ----------
    output_path:
        The written ``.tether`` project.
    n_molecules, n_frames:
        Molecules written (rows in ``/molecules`` = trace rows) and their shared native
        frame extent.
    source:
        The source identity string (the SMD/``.txt`` filename) stamped into provenance
        and folded into every ``molecule_key``.
    source_kind:
        Which reader produced the input: ``"smd"`` (an SMD-HDF5) or ``"txt"`` (a bare
        Deep-LASI ``.txt``).
    banner:
        The one-time :data:`ANALYSIS_ONLY_BANNER` recorded in the project marker.
    n_categories:
        Category-list seeds written to the condition's editable vocabulary (0 if none).
    """

    output_path: Path
    n_molecules: int
    n_frames: int
    source: str
    source_kind: str
    banner: str
    n_categories: int


@dataclass(frozen=True)
class AnalysisOnlyMarker:
    """The ``/settings/analysis_only`` project-level marker (PRD §7.8).

    Read by :func:`read_analysis_only_marker`; ``None`` from that reader means the
    project is a normal (round-trip-capable) store. The GUI wizard gates the
    round-trip browser + patch-dependent views on ``round_trip_available``.
    """

    round_trip_available: bool
    banner: str
    source: str
    n_molecules: int


# --------------------------------------------------------------------------- #
# source normalization
# --------------------------------------------------------------------------- #


def _normalize_source(
    source: SMDData | DeepLasiTraces,
) -> tuple[np.ndarray, np.ndarray, np.ndarray | None, str]:
    """Normalize an SMD or bare-``.txt`` source to (donor, acceptor, windows, kind).

    An SMD-HDF5 (:class:`~tether.idealize.smd.SMDData`, from
    :func:`tether.idealize.read_smd`) carries ``raw`` ``(N, T, 2)`` with the fixed
    ``(donor, acceptor)`` channel order (Appendix D.1) and, when it has a tMAVEN group,
    per-molecule analysis windows ``(pre_list, post_list)``. A bare Deep-LASI ``.txt``
    (:class:`~tether.io.deeplasi.DeepLasiTraces`, from
    :func:`tether.io.deeplasi.read_deeplasi_txt`) carries the two ``(N, T)`` intensity
    matrices and no windows. Both become the analysis-only project's corrected traces.
    """
    if isinstance(source, SMDData):
        raw = np.asarray(source.raw, dtype=np.float64)
        if raw.ndim != 3 or raw.shape[2] != 2:
            raise ValueError(f"SMD raw must be (n_molecules, n_frames, 2); got {raw.shape}")
        donor = np.ascontiguousarray(raw[:, :, 0])
        acceptor = np.ascontiguousarray(raw[:, :, 1])
        windows: np.ndarray | None = None
        if source.has_tmaven:
            # tMAVEN pre_list/post_list are the half-open window [pre, post) — the same
            # convention Tether's tested tMAVEN hand-off round-trips (analysis_window
            # [lo, hi) ↔ pre_list=lo/post_list=hi, tether.project.handoff._store_window)
            # and write_smd defaults (post_list = n_frames, i.e. exclusive), corroborated
            # by the committed SMD fixtures (post_list == n_frames). No off-by-one remap.
            windows = np.stack(
                [
                    np.asarray(source.pre_list, dtype=np.int64).reshape(-1),
                    np.asarray(source.post_list, dtype=np.int64).reshape(-1),
                ],
                axis=1,
            )
        return donor, acceptor, windows, "smd"
    if isinstance(source, DeepLasiTraces):
        donor = np.ascontiguousarray(np.asarray(source.donor_corrected, dtype=np.float64))
        acceptor = np.ascontiguousarray(np.asarray(source.acceptor_corrected, dtype=np.float64))
        return donor, acceptor, None, "txt"
    raise TypeError(
        "source must be an SMDData (tether.idealize.read_smd) or a DeepLasiTraces "
        f"(tether.io.deeplasi.read_deeplasi_txt), got {type(source).__name__}"
    )


def _validate_traces(donor: np.ndarray, acceptor: np.ndarray) -> None:
    """Refuse an empty or malformed trace pair before any output is written."""
    if donor.ndim != 2 or acceptor.ndim != 2:
        raise ValueError("donor/acceptor traces must be 2-D (n_molecules, n_frames)")
    if donor.shape != acceptor.shape:
        raise ValueError(
            f"donor {donor.shape} and acceptor {acceptor.shape} traces must be the same shape"
        )
    if donor.shape[0] == 0:
        raise ValueError("no molecules to import (the SMD/.txt source is empty)")
    if donor.shape[1] == 0:
        raise ValueError("traces have zero frames; nothing to analyze")


# --------------------------------------------------------------------------- #
# per-molecule identity + rows
# --------------------------------------------------------------------------- #


def _analysis_only_molecule_key(
    source_id: str, index: int, donor_row: np.ndarray, acceptor_row: np.ndarray
) -> str:
    """A unique, deterministic ``molecule_key`` for a coordinate-less molecule (§7.10).

    The frozen ``molecule_key`` is a cross-file content hash; the native form
    (:func:`tether.imaging.extract.molecule_key`) hashes the movie ``sha256`` +
    quantized ``donor_xy``, which is unavailable here (no movie, no coordinates) and
    would collide to one identical key for every molecule if the coordinates were
    zeroed. Instead this hashes the source identity + the molecule's **row index**
    (guaranteeing uniqueness and stability across a re-import of the same source, the
    §7.8 "molecule index is the primary key" contract) together with the raw
    donor/acceptor **trace bytes** (an intensity-anchored identity, echoing the §5.3
    exact-intensity-match key). It is a hash of real inputs — an identity, not
    fabricated coordinate data.
    """
    digest = hashlib.sha256()
    digest.update(_MOLECULE_KEY_PREFIX.encode("utf-8"))
    digest.update(f"|{source_id}|{index}|".encode())
    digest.update(np.ascontiguousarray(donor_row, dtype="<f8").tobytes())
    digest.update(np.ascontiguousarray(acceptor_row, dtype="<f8").tobytes())
    return digest.hexdigest()


def _resolve_windows(windows: np.ndarray | None, n: int, n_frames: int) -> np.ndarray:
    """Per-molecule ``analysis_window``: the SMD's, clamped to ``[0, n_frames]``, else full.

    A supplied tMAVEN ``(pre, post)`` window is clamped to the valid frame extent; a
    degenerate window (``post <= pre`` after clamping) falls back to the full native
    window ``[0, n_frames]`` (the same fallback the analysis views apply).
    """
    full = np.tile(np.array([0, n_frames], dtype=np.int32), (n, 1))
    if windows is None:
        return full
    w = np.asarray(windows, dtype=np.int64)
    if w.shape != (n, 2):
        raise ValueError(f"analysis windows must be (n_molecules, 2) = ({n}, 2); got {w.shape}")
    lo = np.clip(w[:, 0], 0, n_frames)
    hi = np.clip(w[:, 1], 0, n_frames)
    out = full.copy()
    valid = hi > lo
    out[valid, 0] = lo[valid].astype(np.int32)
    out[valid, 1] = hi[valid].astype(np.int32)
    return out


def _build_molecule_rows(
    donor: np.ndarray,
    acceptor: np.ndarray,
    windows: np.ndarray | None,
    parsed: ParsedFilename,
    source_id: str,
) -> tuple[np.ndarray, list[str]]:
    """Build the ``MOLECULES_DTYPE`` rows for a movie-less, coordinate-less import.

    Mirrors :func:`tether.imaging.extract._build_molecule_rows`' sentinel recipe, but
    with ``movie_id = ""`` (movie-less), ``donor_xy``/``acceptor_xy`` = ``NaN``
    (coordinates absent — never a fake ``[0, 0]``), a synthesized unique
    ``molecule_key``, the SMD's per-molecule ``analysis_window``, and
    ``tags = "round-trip-unavailable"``. Correction factors are left ``NaN`` (the
    apparent-E substrate; :func:`compute_corrected_fret` stamps the method).
    """
    from tether.io.schema import MOLECULES_DTYPE  # noqa: PLC0415

    n, n_frames = int(donor.shape[0]), int(donor.shape[1])
    mol_ids = [f"mol-{uuid4().hex}" for _ in range(n)]
    keys = [_analysis_only_molecule_key(source_id, i, donor[i], acceptor[i]) for i in range(n)]
    analysis_window = _resolve_windows(windows, n, n_frames)

    rows = np.zeros(n, dtype=MOLECULES_DTYPE)
    rows["molecule_id"] = mol_ids
    rows["molecule_key"] = keys
    rows["movie_id"] = ""  # analysis-only: no movie to link (movie-less, §5.4)
    rows["donor_xy"] = np.nan  # coordinates absent — a NaN sentinel, never fabricated [0, 0]
    rows["acceptor_xy"] = np.nan
    rows["aperture_id"] = 0
    rows["frame_range"] = [0, n_frames]  # the full native extent (no zero-pad: one source)
    rows["analysis_window"] = analysis_window
    rows["bleach_frames"] = [_UNDETECTED_FRAME, _UNDETECTED_FRAME]  # not detected (no raw traces)
    rows["alpha"] = np.nan  # apparent-E substrate; no correction factors (no movie)
    rows["gamma"] = np.nan
    rows["delta"] = 0.0
    rows["correction_method"] = ""  # stamped by compute_corrected_fret -> APPARENT_UNAVAILABLE
    rows["correction_confidence"] = np.nan
    rows["curation_label"] = 0  # UNCURATED
    rows["category"] = ""
    rows["quality_class"] = np.nan
    rows["condition_id"] = parsed.condition_id  # provisional-from-filename (§5.1)
    rows["condition_id_provisional"] = parsed.condition_id
    rows["source_filename"] = parsed.source_filename
    rows["tags"] = ANALYSIS_ONLY_TAG
    return rows, mol_ids


# --------------------------------------------------------------------------- #
# additive writers
# --------------------------------------------------------------------------- #


def _append_molecule_rows(table: object, rows: np.ndarray) -> None:
    """Append structured ``rows`` into the fresh ``/molecules/table`` (store order)."""
    n0 = table.shape[0]
    table.resize((n0 + rows.shape[0],))
    table[n0:] = rows


def _write_corrected_traces(f: object, donor: np.ndarray, acceptor: np.ndarray) -> None:
    """Write the SMD/``.txt`` intensities as the ``corrected`` ``/traces`` pair.

    The apparent-E analysis substrate (the ``intensity_quantity="corrected"`` layer);
    stored ``float32``, chunked + gzip, with a ``(None, None)`` maxshape matching the
    :mod:`tether.imaging.extract` store convention. No ``raw``/``background`` layers
    are written — they are genuinely absent for a coordinate-less import.
    """
    traces = f["traces"]
    for name, block in (("donor_corrected", donor), ("acceptor_corrected", acceptor)):
        arr = np.ascontiguousarray(block, dtype=_TRACE_DTYPE)
        traces.create_dataset(
            name,
            data=arr,
            dtype=_TRACE_DTYPE,
            chunks=True,
            compression="gzip",
            maxshape=(None, None),
        )


def _app_version() -> str:
    """Best-effort Tether version for the provenance stamp (NFR-REPRO)."""
    try:
        from tether import __version__  # noqa: PLC0415

        return str(__version__)
    except Exception:  # pragma: no cover - defensive; __version__ has its own fallback
        return "0.0.0+unknown"


def _stamp_analysis_only_settings(
    f: object, *, source: str, source_kind: str, n_molecules: int, n_frames: int
) -> None:
    """Write the additive ``/settings/analysis_only`` project-level marker (PRD §7.8).

    Mirrors the ``_stamp_*_settings`` child-group idiom (create-or-replace a named
    ``/settings`` child, write scalar attrs) used by the correction/leakage/gamma
    passes. A new ``/settings`` child is additive data under the frozen container —
    ``schema-guard`` stays green (no ``schema.py`` change).
    """
    import h5py  # noqa: PLC0415

    settings = f[_SETTINGS_GROUP]
    if _ANALYSIS_ONLY_SETTINGS in settings:
        del settings[_ANALYSIS_ONLY_SETTINGS]
    grp = settings.create_group(_ANALYSIS_ONLY_SETTINGS, track_order=True)
    grp.attrs["round_trip_available"] = False
    grp.attrs["reason"] = "analysis-only-import"
    grp.attrs["n_molecules"] = int(n_molecules)
    grp.attrs["n_frames"] = int(n_frames)
    str_dt = h5py.string_dtype(encoding="utf-8")
    grp.attrs["banner"] = np.array(ANALYSIS_ONLY_BANNER, dtype=str_dt)
    grp.attrs["source"] = np.array(source, dtype=str_dt)
    grp.attrs["source_kind"] = np.array(source_kind, dtype=str_dt)
    grp.attrs["app_version"] = np.array(_app_version(), dtype=str_dt)
    grp.attrs["created_utc"] = np.array(datetime.now(UTC).isoformat(), dtype=str_dt)


def _seed_categories(path: Path, condition_id: str, categories: Sequence[str]) -> None:
    """Materialize the ``/conditions`` row then seed its editable category vocabulary."""
    sync_conditions(path)
    if categories:
        set_category_list(path, condition_id, list(categories))


# --------------------------------------------------------------------------- #
# the public importer
# --------------------------------------------------------------------------- #


def import_analysis_only_project(
    output_path: str | Path,
    *,
    source: SMDData | DeepLasiTraces,
    source_name: str = "",
    parsed: ParsedFilename | None = None,
    categories: Sequence[str] = (),
    overwrite: bool = False,
) -> AnalysisOnlyImportSummary:
    """Import a coordinate-less SMD/``.txt`` source as an analysis-only ``.tether`` (§7.8).

    Builds the project atomically at a sibling temp path and :func:`os.replace`\\ s it
    into place only on full success, mirroring
    :func:`tether.project.reconstruct.reconstruct_project`. The result is **movie-less**
    and **coordinate-less** by construction: idealization / histograms / TDP / kinetics
    run on it, but the round-trip browser and patch views are disabled (recorded in the
    ``/settings/analysis_only`` marker + every molecule's ``round-trip-unavailable`` tag).

    Parameters
    ----------
    output_path:
        Destination ``.tether`` path (a pre-existing file is replaced only when
        ``overwrite`` is set).
    source:
        The decoded trace source — an :class:`~tether.idealize.smd.SMDData` (from
        :func:`tether.idealize.read_smd`, the SMD-HDF5 case, incl. every "raw
        ``.txt``-sourced" SMD and the 281-molecule parity fixture) or a
        :class:`~tether.io.deeplasi.DeepLasiTraces` (from
        :func:`tether.io.deeplasi.read_deeplasi_txt`, a bare ``.txt``). Its donor/acceptor
        intensities become the ``corrected`` ``/traces`` layers.
    source_name:
        The source filename, used for the provisional ``condition_id`` parse, the
        ``source_filename`` provenance, and the ``molecule_key`` identity. Defaults to
        the output filename when empty (a caller normally passes the real SMD/``.txt``
        name).
    parsed:
        A pre-computed provisional filename parse; defaults to
        :func:`tether.io.filename.parse_filename` of ``source_name``.
    categories:
        Editable-category-list seeds for the condition's vocabulary (optional).
    overwrite:
        Replace an existing ``output_path`` (default ``False`` refuses to clobber).

    Returns
    -------
    AnalysisOnlyImportSummary
        Counts + provenance describing the import.

    Raises
    ------
    FileExistsError
        If ``output_path`` exists and ``overwrite`` is ``False``.
    tether.project.lock.LockedError
        If ``overwrite`` replaces an existing project a **foreign** ``.lock`` holds
        (an open session), so the atomic publish cannot bypass the single-writer
        invariant (§5.4).
    TypeError
        If ``source`` is not an ``SMDData`` or ``DeepLasiTraces``.
    ValueError
        If the source carries no molecules or zero-length traces.
    """
    import h5py  # noqa: PLC0415

    output_path = Path(output_path)
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"{output_path} exists (pass overwrite=True to replace)")
    if output_path.exists():
        # The atomic publish (os.replace below) is destructive, but the store is built
        # at a fresh tmp_path so Project.create never sees output_path and its
        # overwrite=True foreign-lock guard is skipped. Re-assert it here, matching
        # every other overwrite path, so a project held open by another writer is not
        # silently clobbered (§5.4).
        from tether.project import lock  # noqa: PLC0415

        lock.assert_writable(output_path)

    donor, acceptor, windows, source_kind = _normalize_source(source)
    _validate_traces(donor, acceptor)
    n_molecules, n_frames = int(donor.shape[0]), int(donor.shape[1])

    if parsed is None:
        parsed = parse_filename(source_name or output_path.name)
    source_id = parsed.source_filename or source_name or output_path.name

    rows, _mol_ids = _build_molecule_rows(donor, acceptor, windows, parsed, source_id)

    tmp_path = output_path.with_name(f"{output_path.name}.{uuid4().hex}.tmp")
    try:
        Project.create(tmp_path, overwrite=True)
        with h5py.File(tmp_path, "r+") as f:
            _append_molecule_rows(f["molecules"][TABLE], rows)
            _write_corrected_traces(f, donor, acceptor)
            _stamp_analysis_only_settings(
                f,
                source=source_id,
                source_kind=source_kind,
                n_molecules=n_molecules,
                n_frames=n_frames,
            )
        # α/γ are NaN, so every molecule resolves to METHOD_APPARENT_UNAVAILABLE — the
        # explicit apparent-E substrate (ADR-0003), stamped for idealization staleness.
        compute_corrected_fret(tmp_path)
        _seed_categories(tmp_path, parsed.condition_id, categories)
        os.replace(tmp_path, output_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise

    return AnalysisOnlyImportSummary(
        output_path=output_path,
        n_molecules=n_molecules,
        n_frames=n_frames,
        source=source_id,
        source_kind=source_kind,
        banner=ANALYSIS_ONLY_BANNER,
        n_categories=len(categories),
    )


def _to_str(value: object) -> str:
    """Decode an ``h5py`` attribute (``bytes``/``np.bytes_``) or coerce to ``str``."""
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def read_analysis_only_marker(path: str | Path) -> AnalysisOnlyMarker | None:
    """Read the ``/settings/analysis_only`` marker, or ``None`` for a normal project.

    The O(1) gate the GUI wizard (a later M7 PR) reads to disable the round-trip
    browser + patch-dependent views and surface the one-time banner. ``None`` means the
    store carries no analysis-only marker — a normal, round-trip-capable project.
    """
    import h5py  # noqa: PLC0415

    with h5py.File(Path(path), "r") as f:
        grp = f.get(f"/{_SETTINGS_GROUP}/{_ANALYSIS_ONLY_SETTINGS}")
        if grp is None:
            return None
        return AnalysisOnlyMarker(
            # Fail-safe: the marker group's mere presence marks an analysis-only project,
            # so a missing/partially-written attr must NOT re-enable the round-trip
            # browser over a coordinate-less store — default to False (round-trip absent).
            round_trip_available=bool(grp.attrs.get("round_trip_available", False)),
            banner=_to_str(grp.attrs.get("banner", "")),
            source=_to_str(grp.attrs.get("source", "")),
            n_molecules=int(grp.attrs.get("n_molecules", 0)),
        )
