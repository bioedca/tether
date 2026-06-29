# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-frame background + Sum integration -> coordinate-tagged ``.tether`` traces.

PRD Appendix E **Stages 14-15** and the M1 S8 deliverable: turn a movie's
donor-anchored molecule list (:class:`tether.imaging.coloc.ColocalizedMolecules`)
into integrated traces + cached patches and persist them, with full provenance,
into a project store as **additive data** under the M0-frozen ``.tether`` skeleton.

This is the **first writer of extraction data** into a ``.tether``. It sits one
level above the integration primitive :func:`tether.imaging.aperture.integrate_traces`
(landed M0.5 S5: the 10-frame uniform temporal-MA ring background and the top-hat
``I = TOT - bg*N_psf`` Sum integration, which already returns *both* the corrected
intensity and the uncorrected disk sum) and the donor-anchored colocalization of
M1 S7, and follows the additive-HDF5-write discipline of
:mod:`tether.imaging.calibrate` (``write_calibration``): open an *existing*
compatible project ``r+``, write only *data* into the pre-declared frozen container
groups, never touch the structure (so ``schema-guard`` stays green).

What it writes (PRD §5.1)
-------------------------
* **``/movies/table``** — one appended row of source + geometry provenance, so a
  molecule's ``movie_id`` resolves to its source for the trace<->movie round-trip
  (§5.2) and the ``molecule_key`` content hash has its movie ``sha256``.
* **``/molecules/table``** — one appended row per molecule: a fresh stable-UUID
  ``molecule_id``, the cross-file content ``molecule_key`` (movie ``sha256`` +
  quantized ``donor_xy``, §7.10), donor/acceptor coordinates, ``frame_range``, the
  provisional ``condition_id`` parsed from the filename (validated at M4, §7.6),
  and the :attr:`~tether.imaging.calibrate.RegistrationMap.molecule_tags` imprinted
  from a low-confidence registration (§7.1). Correction factors (α/γ) are left
  **un-computed** (``NaN``; M3) and bleach frames un-detected (``-1``; M3) — the
  apparent-E substrate the MVP runs on.
* **``/traces/{donor,acceptor}_{raw,corrected,background}``** — six rectangular
  ``(n_molecules, max_n_frames)`` arrays, chunked + gzip-compressed, **zero-padded
  to the experiment-max frame count** as movies of differing length are appended
  (PRD §5.1; mirroring tMAVEN's ``concatenate_smds`` pad-to-``maxt``). Each
  molecule's ``frame_range`` delimits its valid native extent inside the pad;
  ``raw`` (uncorrected) feeds bleach detection (M3), ``corrected`` is the top-hat
  intensity, ``background`` the subtracted per-frame ring estimate.
* **``/patches/{donor,acceptor}``** — one ``window×window`` temporal-mean image
  patch per molecule per channel, cached for movie-less curation + the static
  overlap view (PRD §5.1).
* **``/settings/extraction``** — the effective aperture/integration parameters +
  app version, written once per experiment (provenance, NFR-REPRO).

Coordinate convention follows the rest of :mod:`tether.imaging`: points are
``(N, 2)`` arrays of ``[x, y] = [col, row]`` in 0-based pixels; frame shapes are
``(H, W) = (rows, cols)``.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import numpy as np

from tether.imaging._rounding import round_half_away
from tether.imaging.aperture import (
    IntegratedTraces,
    aperture_in_frame,
    aperture_masks,
    integrate_traces,
)
from tether.imaging.calibrate import RegistrationMap
from tether.imaging.coloc import ColocalizedMolecules
from tether.imaging.split import ChannelGeometry

if TYPE_CHECKING:
    from collections.abc import Mapping

    from tether.io.filename import ParsedFilename

__all__ = [
    "MOLECULE_KEY_QUANTUM_PX",
    "MovieMetadata",
    "MoleculeTraces",
    "extract_molecules",
    "molecule_key",
    "read_molecules",
    "read_patches",
    "read_traces",
    "write_extraction",
]

#: Sub-pixel quantum (px) the ``donor_xy`` is rounded to before hashing into the
#: cross-file ``molecule_key`` (PRD §5.1/§7.10; registered in §11.2). Detection
#: enforces an 8 px minimum separation, so a 0.1 px quantum never collides two
#: distinct molecules of one movie, yet absorbs float-repr jitter so the same
#: molecule re-extracted (or carried into a split/subset file) hashes identically.
MOLECULE_KEY_QUANTUM_PX = 0.1

#: The float storage dtype for cached traces + patches (the raw/corrected/background
#: arrays are deliberately redundant, PRD §5.1; ``float32`` halves the store and is
#: ample precision for the disk-sum intensities — gzip-compressed and chunked).
_TRACE_DTYPE = "<f4"

#: ``/traces`` per-channel dataset names, mapped from the :class:`IntegratedTraces`
#: fields (``raw`` = uncorrected ``total``, ``corrected`` = ``intensity``).
_QUANTITIES: tuple[tuple[str, str], ...] = (
    ("raw", "total"),
    ("corrected", "intensity"),
    ("background", "background"),
)
_CHANNELS: tuple[str, ...] = ("donor", "acceptor")

_TRACES_GROUP = "traces"
_PATCHES_GROUP = "patches"
_SETTINGS_GROUP = "settings"
_EXTRACTION_SETTINGS = "extraction"

#: Sentinels written for the not-yet-computed M3 correction/bleach fields, so the
#: apparent-E substrate is unambiguous (a finite-factor gate is applied before any
#: median at M3, so a NaN factor never reaches E — the ADR-0003 invariant).
_UNDETECTED_FRAME = -1
_UNCURATED_LABEL = 0


def _app_version() -> str:
    """Best-effort Tether version for the extraction provenance stamp (NFR-REPRO)."""
    try:
        from tether import __version__  # noqa: PLC0415

        return str(__version__)
    except Exception:  # pragma: no cover - defensive; version is normally present
        return "0.0.0+unknown"


# --- the cross-file molecule identity ----------------------------------------


def molecule_key(movie_sha256: str, donor_xy: np.ndarray) -> str:
    """Content identity hash for a molecule (PRD §5.1 ``molecule_key``; §7.10).

    The cross-file join key for split-file merge-back: the SHA-256 of the movie's
    own ``sha256`` content hash and the molecule's ``donor_xy`` quantized to
    :data:`MOLECULE_KEY_QUANTUM_PX`. Deterministic across runs and platforms (a
    pure content hash, no salt), so a labeled subset row always resolves back to
    its canonical molecule regardless of which file carries it.

    Parameters
    ----------
    movie_sha256:
        The movie's content ``sha256`` (``/movies.sha256``).
    donor_xy:
        The molecule's ``[x, y]`` donor (reference) coordinate, sub-pixel.

    Returns
    -------
    str
        A 64-char hex SHA-256 digest.
    """
    xy = np.asarray(donor_xy, dtype=np.float64).ravel()
    if xy.shape != (2,) or not np.isfinite(xy).all():
        raise ValueError(f"donor_xy must be a finite [x, y] pair, got {donor_xy!r}")
    q = round_half_away(xy / MOLECULE_KEY_QUANTUM_PX).astype(np.int64)
    payload = f"{movie_sha256}|{int(q[0])}|{int(q[1])}"
    return sha256(payload.encode("utf-8")).hexdigest()


# --- movie metadata for the /movies row --------------------------------------


@dataclass(frozen=True)
class MovieMetadata:
    """Per-movie source + geometry provenance for a ``/movies`` row (schema §5.1).

    Mirrors the frozen ``MOVIES_DTYPE`` fields the extraction writer populates. The
    fast-signature fields (``file_size`` / ``mtime`` / ``offline_flag`` /
    ``head_tail_hash``, §5.4) default to zero/empty here — the S9 CLI fills them
    from the source file. ``donor_geometry`` / ``acceptor_geometry`` carry the
    per-channel split (crop/rotation/flip); an absent geometry (or a full-frame
    crop) is written as a zero crop ``[0, 0, 0, 0]``, rotation 0, flip ``[0, 0]``.
    """

    movie_id: str
    sha256: str
    n_frames: int
    height: int
    width: int
    uri: str = ""
    pixel_dtype: str = ""
    byteorder: str = ""
    frame_time: float = 0.0
    calibration_id: str = ""
    donor_geometry: ChannelGeometry | None = None
    acceptor_geometry: ChannelGeometry | None = None
    file_size: int = 0
    mtime: float = 0.0
    offline_flag: int = 0
    head_tail_hash: str = ""

    def __post_init__(self) -> None:
        if not self.movie_id:
            raise ValueError("movie_id must be a non-empty string")
        for name in ("n_frames", "height", "width"):
            if int(getattr(self, name)) < 1:
                raise ValueError(f"{name} must be >= 1, got {getattr(self, name)!r}")


# --- the integrated-traces bundle for one movie ------------------------------


@dataclass(frozen=True)
class MoleculeTraces:
    """Integrated donor+acceptor traces + cached patches for one movie's molecules.

    Row ``i`` of every array is one molecule, aligned with the
    :class:`~tether.imaging.coloc.ColocalizedMolecules` it was extracted from.
    Produced by :func:`extract_molecules` and consumed by :func:`write_extraction`;
    it also carries the effective aperture/integration parameters so the writer can
    stamp them into ``/settings`` (provenance).
    """

    donor: IntegratedTraces
    acceptor: IntegratedTraces
    donor_patches: np.ndarray
    acceptor_patches: np.ndarray
    window: int
    disk_radius: float
    ring_inner: float
    ring_outer: float
    bg_window: int

    @property
    def n_molecules(self) -> int:
        """Number of molecules (rows)."""
        return int(self.donor.intensity.shape[0])

    @property
    def n_frames(self) -> int:
        """Native frame count of this movie's traces (the un-padded width)."""
        return int(self.donor.intensity.shape[1])


def _mean_patches(movie: np.ndarray, coords: np.ndarray, window: int) -> np.ndarray:
    """Per-molecule ``window×window`` temporal-mean image patch (``(N, w, w)`` f32).

    The cached representative crop for movie-less curation + the static overlap view
    (PRD §5.1). The crop centre is the away-from-zero rounded coordinate (the same
    rule as :func:`tether.imaging.aperture.integrate_traces`); an out-of-frame
    molecule (never produced by :func:`~tether.imaging.coloc.colocalize`'s crop-box
    guardrail, but guarded defensively) yields an all-zero patch.
    """
    coords = np.atleast_2d(np.asarray(coords, dtype=np.float64))
    n = 0 if coords.size == 0 else coords.shape[0]
    patches = np.zeros((n, window, window), dtype=np.float32)
    if n == 0:
        return patches
    _, height, width = movie.shape
    half = window // 2
    fits = aperture_in_frame(coords, shape=(height, width), window=window)
    for i in range(n):
        if not fits[i]:
            continue
        col = int(round_half_away(coords[i, 0]))
        row = int(round_half_away(coords[i, 1]))
        crop = movie[:, row - half : row + half + 1, col - half : col + half + 1]
        patches[i] = crop.astype(np.float64, copy=False).mean(axis=0)
    return patches


def extract_molecules(
    donor_channel: np.ndarray,
    acceptor_channel: np.ndarray,
    molecules: ColocalizedMolecules,
    *,
    window: int = 21,
    disk_radius: float = 3.0,
    ring_inner: float = 6.0,
    ring_outer: float = 8.0,
    bg_window: int = 10,
) -> MoleculeTraces:
    """Integrate a movie's donor-anchored molecules into traces + patches (Stage 14).

    Runs :func:`tether.imaging.aperture.integrate_traces` on the donor channel at
    ``molecules.donor_xy`` and the acceptor channel at ``molecules.acceptor_xy``
    (the donor-anchored read positions, S7), and caches one temporal-mean patch per
    molecule per channel. The two channel sub-images are the donor/acceptor halves
    from :func:`tether.imaging.split.split_channels`.

    Parameters
    ----------
    donor_channel, acceptor_channel:
        ``(T, H, W)`` raw channel sub-image stacks (same ``T``).
    molecules:
        The donor-anchored molecule list (:class:`~tether.imaging.coloc.ColocalizedMolecules`);
        every molecule is guaranteed in-frame in both channels by S7's crop-box gate.
    window, disk_radius, ring_inner, ring_outer, bg_window:
        Aperture geometry + temporal-background window (PRD §11.2 defaults),
        forwarded to :func:`~tether.imaging.aperture.integrate_traces`.

    Returns
    -------
    MoleculeTraces
        Row-aligned with ``molecules``.
    """
    donor_movie = np.asarray(donor_channel)
    acceptor_movie = np.asarray(acceptor_channel)
    if donor_movie.ndim != 3 or acceptor_movie.ndim != 3:
        raise ValueError("donor_channel and acceptor_channel must be 3-D (T, H, W) stacks")
    if donor_movie.shape[0] != acceptor_movie.shape[0]:
        raise ValueError(
            f"donor/acceptor frame counts differ: {donor_movie.shape[0]} vs "
            f"{acceptor_movie.shape[0]} (the two halves of one movie)"
        )
    aperture_kw = {
        "window": window,
        "disk_radius": disk_radius,
        "ring_inner": ring_inner,
        "ring_outer": ring_outer,
        "bg_window": bg_window,
    }
    donor = integrate_traces(donor_movie, molecules.donor_xy, **aperture_kw)
    acceptor = integrate_traces(acceptor_movie, molecules.acceptor_xy, **aperture_kw)
    return MoleculeTraces(
        donor=donor,
        acceptor=acceptor,
        donor_patches=_mean_patches(donor_movie, molecules.donor_xy, window),
        acceptor_patches=_mean_patches(acceptor_movie, molecules.acceptor_xy, window),
        window=window,
        disk_radius=disk_radius,
        ring_inner=ring_inner,
        ring_outer=ring_outer,
        bg_window=bg_window,
    )


# --- the writer (additive data under the M0-frozen skeleton) -----------------


def _geometry_fields(geom: ChannelGeometry | None) -> tuple[np.ndarray, int, np.ndarray]:
    """Flatten a channel geometry to the ``MOVIES_DTYPE`` (crop4, rotation, flip2).

    An absent geometry or a full-frame (``crop is None``) geometry is written as a
    zero crop ``[0, 0, 0, 0]`` (the "full frame / unspecified" sentinel).
    """
    if geom is None or geom.crop is None:
        crop4 = np.zeros(4, dtype=np.int32)
    else:
        crop4 = np.asarray(geom.crop, dtype=np.int32).ravel()
        if crop4.size != 4:
            raise ValueError(f"channel crop must be [y1, x1, y2, x2], got {geom.crop!r}")
    rotation = 0 if geom is None else int(geom.rotation_deg)
    flip = np.array([0, 0], dtype=np.int8) if geom is None else np.asarray(geom.flip, dtype=np.int8)
    return crop4, rotation, flip


def _build_movie_row(movie: MovieMetadata) -> np.ndarray:
    """Build the single ``MOVIES_DTYPE`` row for a ``/movies/table`` append."""
    from tether.io.schema import MOVIES_DTYPE  # noqa: PLC0415

    d_crop, d_rot, d_flip = _geometry_fields(movie.donor_geometry)
    a_crop, a_rot, a_flip = _geometry_fields(movie.acceptor_geometry)
    row = np.zeros(1, dtype=MOVIES_DTYPE)
    row["movie_id"] = movie.movie_id
    row["uri"] = movie.uri
    row["sha256"] = movie.sha256
    row["file_size"] = int(movie.file_size)
    row["mtime"] = float(movie.mtime)
    row["offline_flag"] = int(movie.offline_flag)
    row["n_frames"] = int(movie.n_frames)
    row["height"] = int(movie.height)
    row["width"] = int(movie.width)
    row["pixel_dtype"] = movie.pixel_dtype
    row["byteorder"] = movie.byteorder
    row["frame_time"] = float(movie.frame_time)
    row["head_tail_hash"] = movie.head_tail_hash
    row["calibration_id"] = movie.calibration_id
    row["donor_crop"] = d_crop
    row["acceptor_crop"] = a_crop
    row["donor_rotation_deg"] = d_rot
    row["acceptor_rotation_deg"] = a_rot
    row["donor_flip"] = d_flip
    row["acceptor_flip"] = a_flip
    return row


def _build_molecule_rows(
    movie: MovieMetadata,
    molecules: ColocalizedMolecules,
    parsed: ParsedFilename,
    tags: str,
    n_frames: int,
) -> tuple[np.ndarray, list[str]]:
    """Build the ``MOLECULES_DTYPE`` rows + the fresh molecule_ids for one movie.

    ``n_frames`` is the actual integrated trace width (``traces.n_frames``), the
    single source of truth for the per-molecule ``frame_range`` delimiter — so it
    always matches the stored extent rather than the independent ``movie.n_frames``
    provenance (which :func:`_validate_alignment` separately asserts is equal).
    """
    from tether.io.schema import MOLECULES_DTYPE  # noqa: PLC0415

    n = molecules.n_molecules
    condition_id = parsed.condition_id
    source_filename = parsed.source_filename
    mol_ids = [f"mol-{uuid4().hex}" for _ in range(n)]
    keys = [molecule_key(movie.sha256, molecules.donor_xy[i]) for i in range(n)]

    rows = np.zeros(n, dtype=MOLECULES_DTYPE)
    rows["molecule_id"] = mol_ids
    rows["molecule_key"] = keys
    rows["movie_id"] = movie.movie_id
    rows["donor_xy"] = molecules.donor_xy
    rows["acceptor_xy"] = molecules.acceptor_xy
    rows["aperture_id"] = 0  # the standard 21x21 aperture (no per-aperture registry yet)
    rows["frame_range"] = [0, n_frames]  # valid native extent inside the zero-pad
    rows["analysis_window"] = [0, n_frames]  # full native window at extraction; refined at M2
    rows["bleach_frames"] = [_UNDETECTED_FRAME, _UNDETECTED_FRAME]  # (D, A) not detected (M3)
    rows["alpha"] = np.nan  # leakage α not yet computed (M3); apparent-E substrate
    rows["gamma"] = np.nan  # detection-correction γ not yet computed (M3)
    rows["delta"] = 0.0  # δ is inert in 2-color (ADR-0008)
    rows["correction_method"] = ""  # none applied yet
    rows["correction_confidence"] = np.nan
    rows["curation_label"] = _UNCURATED_LABEL
    rows["category"] = ""
    rows["quality_class"] = np.nan  # read-only ML output (M5); none yet
    rows["condition_id"] = condition_id  # provisional-from-filename at extraction (§5.1)
    rows["condition_id_provisional"] = condition_id  # retained verbatim across any M4 re-key
    rows["source_filename"] = source_filename
    rows["tags"] = tags
    return rows, mol_ids


def _append_compound_rows(table: Any, rows: np.ndarray) -> None:
    """Append structured ``rows`` to a resizable 1-D compound dataset (``/…/table``)."""
    n0 = table.shape[0]
    table.resize((n0 + rows.shape[0],))
    table[n0:] = rows


def _append_padded_2d(group: Any, name: str, block: np.ndarray) -> None:
    """Append an ``(N, T)`` block to a ``/traces`` array, zero-padding to max-T.

    Creates the chunked + gzip dataset on first write; thereafter grows the time
    axis to ``max(existing_T, T)`` (existing rows gain zero-filled tail columns) and
    the molecule axis by ``N`` (new rows keep zero-fill beyond ``T``). h5py's default
    float fill value is 0, so both the existing-row tail and the new-row tail are the
    zero-pad the schema mandates (PRD §5.1).
    """
    block = np.ascontiguousarray(block, dtype=_TRACE_DTYPE)
    n, t = block.shape
    if name not in group:
        ds = group.create_dataset(
            name,
            shape=(n, t),
            maxshape=(None, None),
            dtype=_TRACE_DTYPE,
            chunks=True,
            compression="gzip",
        )
        ds[...] = block
        return
    ds = group[name]
    n0, w0 = ds.shape
    new_w = max(w0, t)
    if new_w > w0:
        ds.resize((n0, new_w))  # existing rows: cols [w0:new_w] become 0 (zero-pad)
    ds.resize((n0 + n, ds.shape[1]))  # new rows arrive zero-filled
    ds[n0 : n0 + n, :t] = block  # cols [t:new_w] of new rows stay 0 (zero-pad)


def _append_patches(group: Any, name: str, block: np.ndarray) -> None:
    """Append an ``(N, w, w)`` patch block to a ``/patches`` array (axis-0 grows)."""
    block = np.ascontiguousarray(block, dtype=_TRACE_DTYPE)
    n, h, w = block.shape
    if name not in group:
        ds = group.create_dataset(
            name,
            shape=(n, h, w),
            maxshape=(None, h, w),
            dtype=_TRACE_DTYPE,
            chunks=True,
            compression="gzip",
        )
        ds[...] = block
        return
    # The patch window is validated up front by _check_against_existing (reject-
    # before-mutate), so by here it is guaranteed to match.
    ds = group[name]
    n0 = ds.shape[0]
    ds.resize((n0 + n, h, w))
    ds[n0:] = block


def _write_settings_once(f: Any, traces: MoleculeTraces, profile: Mapping[str, Any] | None) -> None:
    """Stamp the effective extraction parameters into ``/settings/extraction`` (write-once).

    Experiment-level provenance (NFR-REPRO): the first extraction into a project
    records the aperture/integration parameters + app version; later movies leave it
    untouched (the per-movie patch-window check enforces real consistency).
    """
    import h5py  # noqa: PLC0415

    settings = f[_SETTINGS_GROUP]
    if _EXTRACTION_SETTINGS in settings:
        return
    grp = settings.create_group(_EXTRACTION_SETTINGS, track_order=True)
    disk, _ = aperture_masks(
        traces.window,
        disk_radius=traces.disk_radius,
        ring_inner=traces.ring_inner,
        ring_outer=traces.ring_outer,
    )
    grp.attrs["window"] = int(traces.window)
    grp.attrs["disk_radius"] = float(traces.disk_radius)
    grp.attrs["ring_inner"] = float(traces.ring_inner)
    grp.attrs["ring_outer"] = float(traces.ring_outer)
    grp.attrs["bg_window"] = int(traces.bg_window)
    grp.attrs["n_psf"] = int(disk.sum())
    grp.attrs["molecule_key_quantum_px"] = float(MOLECULE_KEY_QUANTUM_PX)
    str_dt = h5py.string_dtype(encoding="utf-8")
    grp.attrs["app_version"] = np.array(_app_version(), dtype=str_dt)
    if profile:
        import json  # noqa: PLC0415

        grp.attrs["profile_json"] = np.array(
            json.dumps(dict(profile), sort_keys=True), dtype=str_dt
        )


def write_extraction(
    project_path: str | Path,
    *,
    movie: MovieMetadata,
    molecules: ColocalizedMolecules,
    traces: MoleculeTraces,
    parsed: ParsedFilename,
    registration_map: RegistrationMap | None = None,
    settings: Mapping[str, Any] | None = None,
) -> list[str]:
    """Write one movie's extracted molecules + traces into a ``.tether`` (Stage 15).

    Appends, as **additive data** under the M0-frozen skeleton, the ``/movies`` row,
    the ``/molecules`` rows, the six ``/traces`` arrays (zero-padded to the
    experiment-max frame count), the two ``/patches`` arrays, and — on the first
    extraction — the ``/settings/extraction`` provenance. Row ``i`` of ``/molecules``,
    every ``/traces`` array, and every ``/patches`` array is the same molecule, so
    the trace<->molecule join is positional.

    The target **must already be a compatible ``.tether`` project**
    (:func:`tether.io.schema.assert_is_compatible_project`); it is opened ``r+`` so
    this can neither create a store nor graft onto a foreign HDF5 file. A movie is
    **write-once** — re-appending the same ``movie_id`` raises (re-extraction is the
    batch runner's per-stage concern, §7.11).

    Parameters
    ----------
    project_path:
        An existing ``.tether`` project store.
    movie:
        Source + geometry provenance for the ``/movies`` row.
    molecules:
        The donor-anchored molecule list (S7).
    traces:
        The integrated traces + patches (:func:`extract_molecules`), row-aligned
        with ``molecules``.
    parsed:
        The provisional filename parse (:func:`tether.io.filename.parse_filename`),
        supplying ``condition_id`` + ``source_filename``.
    registration_map:
        The calibration used; its
        :attr:`~tether.imaging.calibrate.RegistrationMap.molecule_tags` (a
        ``low-confidence-registration`` tag for an over-gate fit, §7.1) is imprinted
        onto every molecule of this movie. ``None`` writes no tag.
    settings:
        Optional extra settings profile (JSON-serialized into
        ``/settings/extraction`` on the first extraction).

    Returns
    -------
    list[str]
        The fresh ``molecule_id`` of each written molecule, in row order.
    """
    import h5py  # noqa: PLC0415

    from tether.io.schema import TABLE, assert_is_compatible_project  # noqa: PLC0415

    project_path = Path(project_path)
    assert_is_compatible_project(project_path)
    _validate_alignment(movie, molecules, traces)
    tags = ",".join(registration_map.molecule_tags) if registration_map is not None else ""

    with h5py.File(project_path, "r+") as f:
        # Reject-before-mutate: every cross-movie precondition is checked up front,
        # so a rejected movie leaves the file untouched (h5py appends are not
        # transactional — a late raise would orphan a half-written movie's rows).
        _check_against_existing(f, project_path, movie, traces)
        _append_compound_rows(f["movies"][TABLE], _build_movie_row(movie))
        _write_settings_once(f, traces, settings)

        rows, mol_ids = _build_molecule_rows(movie, molecules, parsed, tags, traces.n_frames)
        if molecules.n_molecules:
            _append_compound_rows(f["molecules"][TABLE], rows)
            traces_grp = f[_TRACES_GROUP]
            patches_grp = f[_PATCHES_GROUP]
            for channel in _CHANNELS:
                integrated: IntegratedTraces = getattr(traces, channel)
                for quantity, attr in _QUANTITIES:
                    _append_padded_2d(
                        traces_grp, f"{channel}_{quantity}", getattr(integrated, attr)
                    )
            _append_patches(patches_grp, "donor", traces.donor_patches)
            _append_patches(patches_grp, "acceptor", traces.acceptor_patches)
    return mol_ids


def _validate_alignment(
    movie: MovieMetadata, molecules: ColocalizedMolecules, traces: MoleculeTraces
) -> None:
    """Refuse a movie/molecule/trace/patch mismatch, or any all-zero (invalid) trace."""
    n = molecules.n_molecules
    shapes = {
        "donor traces": traces.donor.intensity.shape[0],
        "acceptor traces": traces.acceptor.intensity.shape[0],
        "donor patches": traces.donor_patches.shape[0],
        "acceptor patches": traces.acceptor_patches.shape[0],
    }
    bad = {k: v for k, v in shapes.items() if v != n}
    if bad:
        raise ValueError(f"row count mismatch vs {n} molecules: {bad}")
    if movie.n_frames != traces.n_frames:
        # The /movies row's n_frames and each molecule's frame_range must equal the
        # actual stored trace width, or frame_range would mark zero-pad columns as
        # valid native frames (feeding zeros to M3 bleach detection) or truncate real
        # frames. movie describes the same movie the traces were integrated from.
        raise ValueError(
            f"movie.n_frames ({movie.n_frames}) != integrated trace width "
            f"({traces.n_frames}); they must describe the same movie"
        )
    if n and not (traces.donor.valid.all() and traces.acceptor.valid.all()):
        # colocalize()'s crop-box gate guarantees every molecule is in-frame in both
        # channels, so every integrated trace must be valid; an invalid one would
        # write an all-zero trace the coloc contract forbids.
        raise ValueError(
            "an integrated trace is invalid (out-of-frame aperture); colocalize() "
            "should have dropped it — refusing to write an all-zero trace"
        )


def _movie_id_present(table: Any, movie_id: str) -> bool:
    """Whether ``movie_id`` already has a row in ``/movies/table``."""
    if table.shape[0] == 0:
        return False
    existing = table["movie_id"][:]
    return any(
        (v.decode("utf-8") if isinstance(v, bytes) else str(v)) == movie_id for v in existing
    )


def _check_against_existing(
    f: Any, project_path: Path, movie: MovieMetadata, traces: MoleculeTraces
) -> None:
    """Reject a movie against the existing store **before any append** (atomicity).

    Both cross-movie preconditions are validated up front so a rejected movie mutates
    nothing (h5py appends are not transactional): the movie is **write-once**, and the
    extraction ``window`` must match the experiment's existing ``/patches`` window.
    """
    from tether.io.schema import TABLE  # noqa: PLC0415

    if _movie_id_present(f["movies"][TABLE], movie.movie_id):
        raise ValueError(
            f"movie_id {movie.movie_id!r} already in {project_path} "
            "(movies are write-once; extraction is not idempotent here)"
        )
    donor_patches = f[_PATCHES_GROUP].get("donor")
    if donor_patches is not None and donor_patches.shape[1] != traces.window:
        raise ValueError(
            f"extraction window {traces.window} differs from the experiment's "
            f"{donor_patches.shape[1]}; the window must be consistent across an "
            "experiment's movies"
        )


# --- minimal readers (the write<->read round-trip; M2 builds the browser) ----


def read_molecules(project_path: str | Path) -> np.ndarray:
    """Read ``/molecules/table`` back as a structured array (a copy)."""
    import h5py  # noqa: PLC0415

    from tether.io.schema import TABLE  # noqa: PLC0415

    with h5py.File(Path(project_path), "r") as f:
        return f["molecules"][TABLE][:]


def read_traces(project_path: str | Path) -> dict[str, np.ndarray]:
    """Read the ``/traces`` arrays back as a ``{name: (N, max_T) array}`` dict."""
    import h5py  # noqa: PLC0415

    with h5py.File(Path(project_path), "r") as f:
        grp = f[_TRACES_GROUP]
        return {name: grp[name][:] for name in grp}


def read_patches(project_path: str | Path) -> dict[str, np.ndarray]:
    """Read the ``/patches`` arrays back as a ``{channel: (N, w, w) array}`` dict."""
    import h5py  # noqa: PLC0415

    with h5py.File(Path(project_path), "r") as f:
        grp = f[_PATCHES_GROUP]
        return {name: grp[name][:] for name in grp}
