# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-frame background + Sum integration -> coordinate-tagged ``.tether`` traces.

PRD Appendix E **Stages 14-15** and the M1 S8 deliverable: turn a movie's
donor-anchored molecule list ([`tether.imaging.coloc.ColocalizedMolecules`][])
into integrated traces + cached patches and persist them, with full provenance,
into a project store as **additive data** under the M0-frozen ``.tether`` skeleton.

This is the **first writer of extraction data** into a ``.tether``. It sits one
level above the integration primitive [`tether.imaging.aperture.integrate_traces`][]
(landed M0.5 S5: the 10-frame uniform temporal-MA ring background and the top-hat
``I = TOT - bg*N_psf`` Sum integration, which already returns *both* the corrected
intensity and the uncorrected disk sum) and the donor-anchored colocalization of
M1 S7, and follows the additive-HDF5-write discipline of
[`tether.imaging.calibrate`][] (``write_calibration``): open an *existing*
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
  and the [`molecule_tags`][tether.imaging.calibrate.RegistrationMap.molecule_tags] imprinted
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
* **``/molecules/acceptor_detected``** — one ``int8`` per ``/molecules`` row, row-aligned
  like the trace arrays: ``1`` where an acceptor spot was detected at the molecule's
  mapped position, ``0`` where none was, and ``-1`` where it was not recorded (rows
  written before #920, or by a path that does not detect acceptor spots). The rule
  that chose the molecules is recorded per movie as ``colocalization`` in its settings
  profile ([`read_colocalization_policies`][], ADR-0127).
* **``/patches/{donor,acceptor}``** — one ``window×window`` temporal-mean image
  patch per molecule per channel, cached for movie-less curation + the static
  overlap view (PRD §5.1).
* **``/settings/extraction``** — the effective aperture/integration parameters +
  app version, written once per experiment (provenance, NFR-REPRO).
* **``/movies/layouts/<movie_id>``** — the movie's channel layout record (#908,
  [`tether.imaging.layout.write_layout_record`][]), only when the caller's
  [`MovieMetadata`][] carries one.

Coordinate convention follows the rest of [`tether.imaging`][]: points are
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
from tether.imaging.layout import (
    LAYOUTS_GROUP,
    ChannelLayout,
    check_layout_slot,
    write_layout_record,
)
from tether.imaging.split import ChannelGeometry
from tether.io.generations import append_record

if TYPE_CHECKING:
    from collections.abc import Mapping

    from tether.io.filename import ParsedFilename
    from tether.io.generations import Generation

__all__ = [
    "ACCEPTOR_DETECTED_NOT_RECORDED",
    "MOLECULE_KEY_QUANTUM_PX",
    "MovieMetadata",
    "MoleculeTraces",
    "assert_appendable",
    "extract_molecules",
    "molecule_key",
    "read_acceptor_detected",
    "read_colocalization_policies",
    "read_molecules",
    "read_movie_settings",
    "read_patches",
    "read_traces",
    "write_extraction",
    "write_generation",
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

#: ``/traces`` per-channel dataset names, mapped from the `IntegratedTraces`
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
#: Child of ``/settings/extraction`` holding one profile attribute per movie (ADR-0117).
_MOVIE_SETTINGS = "movies"
_MOLECULES_GROUP = "molecules"
#: ``/molecules`` member holding the per-row acceptor-detection flag (#920, ADR-0127).
_ACCEPTOR_DETECTED = "acceptor_detected"
#: The flag of a row whose acceptor detection was never recorded (ADR-0127).
ACCEPTOR_DETECTED_NOT_RECORDED = -1
#: The settings-profile key naming the colocalization rule of a movie (ADR-0127).
_COLOCALIZATION_KEY = "colocalization"
#: The rule of a movie whose profile names none: every extraction before #920 was
#: donor-anchored (ADR-0015), so an existing project reads as donor-anchored.
_LEGACY_COLOCALIZATION = "donor-anchored"

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
    [`MOLECULE_KEY_QUANTUM_PX`][]. Deterministic across runs and platforms (a
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
    ``channel_layout`` is the layout those geometries were resolved from (#908); when
    set, it is stored as ``/movies/layouts/<movie_id>``, and ``None`` stores nothing.
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
    channel_layout: ChannelLayout | None = None

    def __post_init__(self) -> None:
        if not self.movie_id:
            raise ValueError("movie_id must be a non-empty string")
        # write_layout_record reads the layout's attributes after the /movies row is
        # appended, so a wrong type must fail here, before write_extraction mutates.
        if self.channel_layout is not None and not isinstance(self.channel_layout, ChannelLayout):
            raise TypeError(
                "channel_layout must be a ChannelLayout or None, "
                f"got {type(self.channel_layout).__name__}"
            )
        for name in ("n_frames", "height", "width"):
            if int(getattr(self, name)) < 1:
                raise ValueError(f"{name} must be >= 1, got {getattr(self, name)!r}")


# --- the integrated-traces bundle for one movie ------------------------------


@dataclass(frozen=True)
class MoleculeTraces:
    """Integrated donor+acceptor traces + cached patches for one movie's molecules.

    Row ``i`` of every array is one molecule, aligned with the
    [`ColocalizedMolecules`][tether.imaging.coloc.ColocalizedMolecules] it was extracted from.
    Produced by [`extract_molecules`][] and consumed by [`write_extraction`][];
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
    rule as [`tether.imaging.aperture.integrate_traces`][]); an out-of-frame
    molecule (never produced by [`colocalize`][tether.imaging.coloc.colocalize]'s crop-box
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

    Runs [`tether.imaging.aperture.integrate_traces`][] on the donor channel at
    ``molecules.donor_xy`` and the acceptor channel at ``molecules.acceptor_xy``
    (the donor-anchored read positions, S7), and caches one temporal-mean patch per
    molecule per channel. The two channel sub-images are the donor/acceptor halves
    from [`tether.imaging.split.split_channels`][].

    Parameters
    ----------
    donor_channel, acceptor_channel:
        ``(T, H, W)`` raw channel sub-image stacks (same ``T``).
    molecules:
        The donor-anchored molecule list
        ([`ColocalizedMolecules`][tether.imaging.coloc.ColocalizedMolecules]);
        every molecule is guaranteed in-frame in both channels by S7's crop-box gate.
    window, disk_radius, ring_inner, ring_outer, bg_window:
        Aperture geometry + temporal-background window (PRD §11.2 defaults),
        forwarded to [`integrate_traces`][tether.imaging.aperture.integrate_traces].

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
    provenance (which `_validate_alignment` separately asserts is equal).
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


def _write_settings_once(f: Any, traces: MoleculeTraces, profile_json: str | None) -> None:
    """Stamp the effective extraction parameters into ``/settings/extraction`` (write-once).

    Experiment-level provenance (NFR-REPRO): the first extraction into a project
    records the aperture/integration parameters + app version; later movies leave it
    untouched (`_check_against_existing` enforces the parameters stay consistent).
    ``profile_json`` is the caller's optional settings profile, **pre-serialized** by
    [`write_extraction`][] before any append so a non-serializable profile fails
    reject-before-mutate.
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
    if profile_json is not None:
        grp.attrs["profile_json"] = np.array(profile_json, dtype=str_dt)


def _write_movie_settings(f: Any, movie_id: str, profile_json: str | None) -> None:
    """Record one movie's settings profile under ``/settings/extraction/movies`` (#347).

    The experiment-level stamp is write-once ([`_write_settings_once`][]), so a second
    movie appended into the project would otherwise leave no record of its own
    settings (its ``.tmap``/``.tdat`` source, registration verdict, detection mode).
    Each movie therefore gets one string attribute, named by its ``movie_id``, holding
    that movie's pre-serialized profile — the first movie included, so the record is
    the same for every movie. Additive: the stamp's own attributes are never touched
    (ADR-0117). Nothing is written when the caller supplied no profile.
    """
    import h5py  # noqa: PLC0415

    if profile_json is None:
        return
    grp = f[_SETTINGS_GROUP][_EXTRACTION_SETTINGS]
    movies = grp.require_group(_MOVIE_SETTINGS)
    movies.attrs[movie_id] = np.array(profile_json, dtype=h5py.string_dtype(encoding="utf-8"))


def read_movie_settings(project_path: str | Path) -> dict[str, dict[str, Any]]:
    """Read every movie's settings profile, ``{movie_id: profile}`` (#347, ADR-0117).

    Returns an empty dict for a project written before per-movie records existed; its
    one movie's profile is then the stamp's own ``profile_json``.
    """
    import json  # noqa: PLC0415

    import h5py  # noqa: PLC0415

    with h5py.File(Path(project_path), "r") as f:
        grp = f[_SETTINGS_GROUP].get(_EXTRACTION_SETTINGS)
        if grp is None or _MOVIE_SETTINGS not in grp:
            return {}
        out: dict[str, dict[str, Any]] = {}
        for movie_id, raw in grp[_MOVIE_SETTINGS].attrs.items():
            text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
            out[str(movie_id)] = json.loads(text)
        return out


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
    experiment-max frame count), the two ``/patches`` arrays, the movie's own
    settings record, and — on the first extraction — the ``/settings/extraction``
    provenance. Row ``i`` of ``/molecules``,
    every ``/traces`` array, and every ``/patches`` array is the same molecule, so
    the trace<->molecule join is positional.

    The target **must already be a compatible ``.tether`` project**
    ([`tether.io.schema.assert_is_compatible_project`][]); it is opened ``r+`` so
    this can neither create a store nor graft onto a foreign HDF5 file. A movie is
    **write-once** — re-appending the same ``movie_id`` raises (re-extraction is the
    batch runner's per-stage concern, §7.11). A molecule whose ``molecule_key`` is
    already in ``/molecules`` raises too, so no two rows share a key (#347, ADR-0117).

    Parameters
    ----------
    project_path:
        An existing ``.tether`` project store.
    movie:
        Source + geometry provenance for the ``/movies`` row.
    molecules:
        The colocalized molecule list (S7). When its acceptor detection was measured
        ([`detection_recorded`][tether.imaging.coloc.ColocalizedMolecules.detection_recorded]),
        its [`policy`][tether.imaging.coloc.ColocalizedMolecules.policy] is stamped into
        the movie's settings profile under ``colocalization`` (ADR-0127), so the stored
        rule is the one the molecules were kept by, whatever ``settings`` holds.
    traces:
        The integrated traces + patches ([`extract_molecules`][]), row-aligned
        with ``molecules``.
    parsed:
        The provisional filename parse ([`tether.io.filename.parse_filename`][]),
        supplying ``condition_id`` + ``source_filename``.
    registration_map:
        The calibration used; its
        [`molecule_tags`][tether.imaging.calibrate.RegistrationMap.molecule_tags] (a
        ``low-confidence-registration`` tag for an over-gate fit, §7.1) is imprinted
        onto every molecule of this movie. ``None`` writes no tag.
    settings:
        Optional extra settings profile, JSON-serialized into
        ``/settings/extraction@profile_json`` on the first extraction and, for every
        movie, into ``/settings/extraction/movies@<movie_id>``
        ([`read_movie_settings`][], #347, ADR-0117). A ``colocalization`` entry that
        differs from the molecules' rule raises ``ValueError`` before anything is
        written.

    Returns
    -------
    list[str]
        The fresh ``molecule_id`` of each written molecule, in row order.
    """
    import json  # noqa: PLC0415

    import h5py  # noqa: PLC0415

    from tether.io.schema import TABLE, assert_is_compatible_project  # noqa: PLC0415

    project_path = Path(project_path)
    assert_is_compatible_project(project_path)
    _validate_alignment(movie, molecules, traces)
    tags = ",".join(registration_map.molecule_tags) if registration_map is not None else ""

    with h5py.File(project_path, "r+") as f:
        # Reject-before-mutate: every cross-movie precondition is checked AND every
        # late-failing input (the movie row, the molecule rows incl. molecule_key on
        # the donor coords, the serialized settings profile) is precomputed up front,
        # so a rejected movie leaves the file untouched (h5py appends are not
        # transactional — a late raise would orphan a half-written movie's rows).
        _check_against_existing(f, project_path, movie, traces)
        if movie.channel_layout is not None:
            check_layout_slot(f["movies"], movie.movie_id)
        movie_row = _build_movie_row(movie)
        rows, mol_ids = _build_molecule_rows(movie, molecules, parsed, tags, traces.n_frames)
        _refuse_stored_molecule_keys(f, project_path, rows["molecule_key"])
        profile = _stamp_colocalization(settings, molecules)
        profile_json = json.dumps(profile, sort_keys=True) if profile else None

        _append_compound_rows(f["movies"][TABLE], movie_row)
        if movie.channel_layout is not None:
            write_layout_record(f["movies"], movie.movie_id, movie.channel_layout)
        _write_settings_once(f, traces, profile_json)
        _write_movie_settings(f, movie.movie_id, profile_json)
        _append_molecule_block(f, rows, molecules, traces)
        append_record(f, movie.movie_id, generation=1)
    return mol_ids


def _append_molecule_block(
    f: Any, rows: np.ndarray, molecules: ColocalizedMolecules, traces: MoleculeTraces
) -> None:
    """Append one movie's molecules to ``/molecules`` and every row-aligned array."""
    from tether.io.schema import TABLE  # noqa: PLC0415

    if not molecules.n_molecules:
        return
    n_existing = int(f["molecules"][TABLE].shape[0])
    _append_compound_rows(f["molecules"][TABLE], rows)
    _append_acceptor_detected(f[_MOLECULES_GROUP], n_existing, molecules)
    traces_grp = f[_TRACES_GROUP]
    patches_grp = f[_PATCHES_GROUP]
    for channel in _CHANNELS:
        integrated: IntegratedTraces = getattr(traces, channel)
        for quantity, attr in _QUANTITIES:
            _append_padded_2d(traces_grp, f"{channel}_{quantity}", getattr(integrated, attr))
    _append_patches(patches_grp, "donor", traces.donor_patches)
    _append_patches(patches_grp, "acceptor", traces.acceptor_patches)


#: Rows moved per write while a movie's old block is taken out of an array.
_SHIFT_ROWS = 1024


def _remove_rows(dataset: Any, rows: slice) -> None:
    """Take ``rows`` out of a dataset whose first axis is the molecule, keeping order.

    The rows after the block move up by its length, a few at a time, and the dataset
    then shrinks. A dataset shorter than the block's start (an acceptor-flag column
    whose tail rows were never recorded) is left as it is.
    """
    n = int(dataset.shape[0])
    start, stop = rows.start, min(rows.stop, n)
    if start >= n or stop <= start:
        return
    width = stop - start
    for src in range(stop, n, _SHIFT_ROWS):
        end = min(src + _SHIFT_ROWS, n)
        dataset[src - width : end - width] = dataset[src:end]
    dataset.resize((n - width, *dataset.shape[1:]))


def _remove_molecule_block(f: Any, rows: slice) -> None:
    """Take one movie's rows out of ``/molecules`` and every row-aligned array."""
    from tether.io.schema import TABLE  # noqa: PLC0415

    if rows.stop <= rows.start:
        return
    _remove_rows(f["molecules"][TABLE], rows)
    flags = f[_MOLECULES_GROUP].get(_ACCEPTOR_DETECTED)
    if flags is not None:
        _remove_rows(flags, rows)
    for group, name in _layer_names():
        dataset = f[group].get(name)
        if dataset is not None:
            _remove_rows(dataset, rows)


def _carry_from_previous(
    previous: np.ndarray, rows: np.ndarray, n_frames: int, match_px: float
) -> list[str]:
    """Give each molecule of ``rows`` that did not move its previous key, label, category, window.

    Mutates ``rows`` in place and returns the carried ``molecule_key`` values (ADR-0136).
    When every previous molecule of the movie has one ``condition_id`` (a validated
    condition, a re-keyed one included), the new molecules take it too: the condition is
    a property of the movie, not an input of the extraction.
    """
    from tether.io.generations import match_previous  # noqa: PLC0415

    carried: list[str] = []
    if not previous.shape[0]:
        return carried
    pairs = match_previous(previous["donor_xy"], rows["donor_xy"], match_px=match_px)
    for j, i in enumerate(pairs):
        if i < 0:
            continue
        old = previous[i]
        rows["molecule_key"][j] = old["molecule_key"]
        rows["curation_label"][j] = old["curation_label"]
        rows["category"][j] = old["category"]
        window = np.clip(np.asarray(old["analysis_window"], dtype=np.int64), 0, n_frames)
        rows["analysis_window"][j] = window if window[0] < window[1] else [0, n_frames]
        carried.append(_decode_key(old["molecule_key"]))
    conditions = {_decode_key(c) for c in previous["condition_id"]}
    if len(conditions) == 1:
        rows["condition_id"] = conditions.pop()
    return carried


def _refuse_repeated_keys(f: Any, project_path: Path, keep_out: slice, keys: np.ndarray) -> None:
    """Refuse new keys that another movie's molecule, or another new molecule, holds."""
    from tether.io.schema import TABLE  # noqa: PLC0415

    decoded = [_decode_key(k) for k in keys]
    if len(set(decoded)) != len(decoded):
        raise ValueError("two molecules of the new generation share one molecule_key")
    stored = f["molecules"][TABLE]["molecule_key"][()] if f["molecules"][TABLE].shape[0] else []
    others = {
        _decode_key(v) for i, v in enumerate(stored) if not keep_out.start <= i < keep_out.stop
    }
    repeated = sum(1 for key in decoded if key in others)
    if repeated:
        raise ValueError(
            f"{repeated} molecule(s) of the new generation have a molecule_key another movie "
            f"of {project_path.name} holds"
        )


def write_generation(
    project_path: str | Path,
    *,
    movie: MovieMetadata,
    molecules: ColocalizedMolecules,
    traces: MoleculeTraces,
    parsed: ParsedFilename,
    registration_map: RegistrationMap | None = None,
    settings: Mapping[str, Any] | None = None,
    match_px: float | None = None,
) -> tuple[Generation, list[str]]:
    """Write a new generation of a movie the project already holds (#921, ADR-0136).

    ``movie.movie_id`` names the movie, and ``movie.sha256`` must be its content hash:
    a generation is a new extraction of the same bytes. The movie's active generation
    is first copied, unchanged, to ``/generations/archive/<movie_id>/<n>``
    ([`tether.io.generations.archive_active`][]); its rows then leave ``/molecules``,
    ``/traces`` and ``/patches``, and the new generation's rows are appended, so every
    reader of those arrays reads the new generation. The movie's ``/movies`` row, layout
    record and settings profile become the new generation's; its ``frame_time`` is kept.

    A new molecule within ``match_px`` of a previous one, the two being each other's
    closest such partner, "did not move": it keeps that molecule's ``molecule_key`` and
    its curation label, category and analysis window, and its key is recorded as carried.
    Corrections, bleach frames and the ML class start again from their not-computed
    sentinels (ADR-0016). A feature cache with a row for a previous molecule is dropped.

    Every check runs before the first write, as for [`write_extraction`][]; the store is
    not transactional, so callers write into a staged copy (``reextract_movie`` does).

    Returns
    -------
    tuple[Generation, list[str]]
        The new generation's record and the fresh ``molecule_id`` of each molecule.

    Raises
    ------
    ValueError
        If the movie is not in the project or has other bytes, the aperture parameters
        differ from the project's, the arrays are misaligned, the movie's rows are not
        one block, or a new key repeats another movie's (or another new molecule's).
    """
    import json  # noqa: PLC0415

    import h5py  # noqa: PLC0415

    from tether.io.generations import (  # noqa: PLC0415
        GENERATION_MATCH_PX,
        archive_active,
        generations_in,
        molecule_rows,
        movie_row_index,
    )
    from tether.io.schema import TABLE, assert_is_compatible_project  # noqa: PLC0415

    match_px = GENERATION_MATCH_PX if match_px is None else float(match_px)
    project_path = Path(project_path)
    assert_is_compatible_project(project_path)
    _validate_alignment(movie, molecules, traces)
    tags = ",".join(registration_map.molecule_tags) if registration_map is not None else ""

    with h5py.File(project_path, "r+") as f:
        _assert_layers_aligned(f, project_path)
        _check_parameters(f, project_path, traces)
        try:
            row_index = movie_row_index(f, movie.movie_id)
        except KeyError:
            raise ValueError(
                f"movie_id {movie.movie_id!r} is not in {project_path.name}; a new movie "
                "is appended, not given a generation"
            ) from None
        stored_movie = f["movies"][TABLE][row_index]
        if _decode_key(stored_movie["sha256"]) != movie.sha256:
            raise ValueError(
                f"movie {movie.movie_id!r} in {project_path.name} has other bytes "
                "(sha256 differs); a generation is a new extraction of the same movie"
            )
        old_rows = molecule_rows(f, movie.movie_id)
        previous = next(g for g in generations_in(f, movie.movie_id) if g.active)
        previous_molecules = f["molecules"][TABLE][old_rows]
        movie_row = _build_movie_row(movie)
        movie_row["frame_time"] = stored_movie["frame_time"]
        rows, mol_ids = _build_molecule_rows(movie, molecules, parsed, tags, traces.n_frames)
        carried = _carry_from_previous(previous_molecules, rows, traces.n_frames, match_px)
        _refuse_repeated_keys(f, project_path, old_rows, rows["molecule_key"])
        profile = _stamp_colocalization(settings, molecules)
        profile_json = json.dumps(profile, sort_keys=True) if profile else None

        if not previous.recorded:  # a store from before generations: record its gen 1 first
            append_record(f, movie.movie_id, generation=1, created_utc="")
        archive_active(f, previous)
        _remove_molecule_block(f, old_rows)
        f["movies"][TABLE][row_index] = movie_row[0]
        layouts = f["movies"].get(LAYOUTS_GROUP)
        if layouts is not None and movie.movie_id in layouts:
            del layouts[movie.movie_id]  # archived with the previous generation
        if movie.channel_layout is not None:
            write_layout_record(f["movies"], movie.movie_id, movie.channel_layout)
        _write_settings_once(f, traces, profile_json)
        _replace_movie_settings(f, movie.movie_id, profile_json)
        _append_molecule_block(f, rows, molecules, traces)
        _drop_feature_cache(f, {_decode_key(m) for m in previous_molecules["molecule_id"]})
        record = append_record(
            f,
            movie.movie_id,
            generation=previous.generation + 1,
            carried_keys=tuple(carried),
            match_px=match_px,
        )
    return record, mol_ids


def _replace_movie_settings(f: Any, movie_id: str, profile_json: str | None) -> None:
    """Make ``profile_json`` the movie's settings record; the previous one is in its inputs."""
    grp = f[_SETTINGS_GROUP][_EXTRACTION_SETTINGS]
    movies = grp.get(_MOVIE_SETTINGS)
    if movies is not None and movie_id in movies.attrs:
        del movies.attrs[movie_id]
    _write_movie_settings(f, movie_id, profile_json)


def _drop_feature_cache(f: Any, molecule_ids: set[str]) -> None:
    """Delete ``/features/table`` when it holds a row of a previous molecule.

    The table is a recomputable cache of each molecule's traces; the window setters drop
    it the same way (``tether.project.molecule_edits``), so the ranker never serves
    features of traces that are no longer the active generation's.
    """
    from tether.io.schema import TABLE  # noqa: PLC0415

    group = f.get("features")
    if not molecule_ids or group is None or TABLE not in group:
        return
    featured = {_decode_key(mid) for mid in group[TABLE]["molecule_id"]}
    if featured & molecule_ids:
        del group[TABLE]


def _stamp_colocalization(
    settings: Mapping[str, Any] | None, molecules: ColocalizedMolecules
) -> dict[str, Any]:
    """Return the settings profile with the molecules' colocalization rule in it (ADR-0127).

    A molecule list whose acceptor detection was measured came out of
    [`colocalize`][tether.imaging.coloc.colocalize] under its ``policy``, so that rule
    is recorded even when the caller passes no profile or one without the key; a
    caller value that names a different rule is refused. A hand-built list (detection
    not recorded) adds nothing, and its movie reads as ``donor-anchored``.
    """
    profile = dict(settings) if settings else {}
    if not molecules.detection_recorded:
        return profile
    stated = profile.get(_COLOCALIZATION_KEY)
    if stated is not None and stated != molecules.policy:
        raise ValueError(
            f"settings colocalization {stated!r} does not match the rule the molecules "
            f"were kept by ({molecules.policy!r})"
        )
    profile[_COLOCALIZATION_KEY] = molecules.policy
    return profile


def _append_acceptor_detected(group: Any, n_existing: int, molecules: ColocalizedMolecules) -> None:
    """Append one ``int8`` acceptor-detection flag per new molecule (#920, ADR-0127).

    The dataset is created on first need. Rows that precede it -- molecules written
    before #920, or appended by a path that records no flag -- read as
    ``ACCEPTOR_DETECTED_NOT_RECORDED`` through the fill value, so an existing project
    gains no invented value and its molecules are not changed. A molecule list whose
    flag was not measured
    ([`detection_recorded`][tether.imaging.coloc.ColocalizedMolecules.detection_recorded]
    is ``False``) is stored as not recorded too.
    """
    if molecules.detection_recorded:
        flags = np.asarray(molecules.acceptor_detected, dtype=bool).astype(np.int8)
    else:
        flags = np.full(molecules.n_molecules, ACCEPTOR_DETECTED_NOT_RECORDED, dtype=np.int8)
    dataset = group.get(_ACCEPTOR_DETECTED)
    if dataset is None:
        dataset = group.create_dataset(
            _ACCEPTOR_DETECTED,
            shape=(n_existing,),
            maxshape=(None,),
            dtype=np.int8,
            chunks=(4096,),
            fillvalue=ACCEPTOR_DETECTED_NOT_RECORDED,
        )
    # A shorter flag column (rows appended by a path that writes none) is padded
    # with the fill value up to the existing rows, so the new block stays aligned.
    dataset.resize((n_existing + flags.shape[0],))
    dataset[n_existing:] = flags


def _validate_alignment(
    movie: MovieMetadata, molecules: ColocalizedMolecules, traces: MoleculeTraces
) -> None:
    """Refuse a movie/molecule/trace/patch mismatch, or any all-zero (invalid) trace.

    Validates the **full** shape contract (not just intensity row counts): all six
    trace arrays must be ``(n_molecules, trace_width)`` and both patch stacks
    ``(n_molecules, window, window)``, so a malformed [`MoleculeTraces`][] (a
    ``total``/``background`` of a different width, an acceptor narrower than the
    donor, a non-square patch) is rejected before it can break the positional
    trace<->molecule join. The reference width is the actual integrated trace width
    ``traces.n_frames``; ``movie.n_frames`` is separately asserted equal to it so the
    ``/movies`` row and ``frame_range`` match the stored extent.
    """
    n = molecules.n_molecules
    expected_trace = (n, traces.n_frames)
    trace_arrays = {
        "donor intensity": traces.donor.intensity,
        "donor total": traces.donor.total,
        "donor background": traces.donor.background,
        "acceptor intensity": traces.acceptor.intensity,
        "acceptor total": traces.acceptor.total,
        "acceptor background": traces.acceptor.background,
    }
    bad = {k: v.shape for k, v in trace_arrays.items() if v.shape != expected_trace}
    if bad:
        raise ValueError(f"trace shape mismatch vs {expected_trace}: {bad}")
    expected_patch = (n, traces.window, traces.window)
    patch_arrays = {
        "donor patches": traces.donor_patches,
        "acceptor patches": traces.acceptor_patches,
    }
    bad_patches = {k: v.shape for k, v in patch_arrays.items() if v.shape != expected_patch}
    if bad_patches:
        raise ValueError(f"patch shape mismatch vs {expected_patch}: {bad_patches}")
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


#: The aperture/integration parameters stamped into ``/settings/extraction`` that
#: every movie of one experiment must share (so its traces are comparable under one
#: provenance record). ``n_psf`` is derived (``window`` + ``disk_radius``) and not
#: compared independently; ``molecule_key_quantum_px`` is a module constant.
_CONSISTENT_PARAMS: tuple[str, ...] = (
    "window",
    "disk_radius",
    "ring_inner",
    "ring_outer",
    "bg_window",
)


def _layer_names() -> tuple[tuple[str, str], ...]:
    """Every ``(group, dataset)`` a movie appends one row block to, besides the tables."""
    traces = tuple(
        (_TRACES_GROUP, f"{channel}_{quantity}")
        for channel in _CHANNELS
        for quantity, _ in _QUANTITIES
    )
    return (*traces, (_PATCHES_GROUP, "donor"), (_PATCHES_GROUP, "acceptor"))


def _assert_layers_aligned(f: Any, project_path: Path) -> None:
    """Refuse a store whose row-aligned arrays do not cover every ``/molecules`` row.

    ``write_extraction`` appends one block to the six ``/traces`` arrays and the two
    ``/patches`` arrays and creates any that is missing. On a store that already has
    molecules but lacks an array — an analysis-only import (no raw, background or
    patches) or a subset exported without its raw layers — the created array would
    start at row 0 while ``/molecules`` starts after the existing rows, so the
    positional trace↔molecule join would break. Such a store cannot take a movie
    (#347, ADR-0117).
    """
    from tether.io.schema import TABLE  # noqa: PLC0415

    n_rows = int(f["molecules"][TABLE].shape[0])
    for group, name in _layer_names():
        dataset = f[group].get(name)
        if dataset is None:
            if n_rows:
                raise ValueError(
                    f"{project_path.name} has {n_rows} molecule(s) but no /{group}/{name}, so a "
                    "new movie's rows could not stay aligned with /molecules (an analysis-only "
                    "import or a subset exported without its raw layers cannot take a movie)"
                )
            continue
        if int(dataset.shape[0]) != n_rows:
            raise ValueError(
                f"/{group}/{name} in {project_path.name} has {dataset.shape[0]} row(s) but "
                f"/molecules has {n_rows}; a new movie's rows could not stay aligned"
            )
    flags = f[_MOLECULES_GROUP].get(_ACCEPTOR_DETECTED)
    if flags is not None and int(flags.shape[0]) > n_rows:
        # Shorter is legal (unrecorded tail rows); longer means the flags of some
        # rows belong to molecules that are no longer there.
        raise ValueError(
            f"/{_MOLECULES_GROUP}/{_ACCEPTOR_DETECTED} in {project_path.name} has "
            f"{flags.shape[0]} row(s) but /molecules has {n_rows}; a new movie's rows "
            "could not stay aligned"
        )


def _refuse_stored_molecule_keys(f: Any, project_path: Path, keys: np.ndarray) -> None:
    """Refuse molecules whose ``molecule_key`` is already in ``/molecules``.

    A label resolves its ``molecule_key`` to every matching row, so two rows with one
    key cannot be told apart. The ``/movies`` checks do not catch every repeat: a
    subset export has no ``/movies`` rows but keeps its molecules and their keys, so
    appending the subset's own movie would store each molecule twice (#347, ADR-0117).
    """
    from tether.io.schema import TABLE  # noqa: PLC0415

    if not len(keys):
        return
    stored = f["molecules"][TABLE]
    if not stored.shape[0]:
        return
    present = {_decode_key(value) for value in stored["molecule_key"]}
    repeated = sum(1 for key in keys if _decode_key(key) in present)
    if repeated:
        raise ValueError(
            f"{repeated} molecule(s) of this movie are already in {project_path.name} "
            "(same molecule_key); a movie whose molecules the project holds cannot be "
            "appended again"
        )


def _decode_key(value: object) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def assert_appendable(project_path: str | Path) -> None:
    """Refuse a project that cannot take another movie, before any work (#347).

    Checks, read-only, that ``project_path`` is a compatible ``.tether`` and that every
    ``/traces`` and ``/patches`` array a movie appends to covers every ``/molecules``
    row ([`write_extraction`][] checks the same before it writes).

    Raises
    ------
    ValueError
        If the project is not compatible or an array is missing or misaligned.
    """
    import h5py  # noqa: PLC0415

    from tether.io.schema import assert_is_compatible_project  # noqa: PLC0415

    project_path = Path(project_path)
    assert_is_compatible_project(project_path)
    with h5py.File(project_path, "r") as f:
        _assert_layers_aligned(f, project_path)


def _check_against_existing(
    f: Any, project_path: Path, movie: MovieMetadata, traces: MoleculeTraces
) -> None:
    """Reject a movie against the existing store **before any append** (atomicity).

    Both cross-movie preconditions are validated up front so a rejected movie mutates
    nothing (h5py appends are not transactional): the movie is **write-once**, and the
    full set of extraction parameters (window + aperture radii + background window)
    must match the experiment's first-stored ``/settings/extraction`` — a later movie
    extracted with a different ``disk_radius`` / ``ring`` / ``bg_window`` would make
    the accumulated traces incomparable under one provenance record.
    """
    from tether.io.schema import TABLE  # noqa: PLC0415

    _assert_layers_aligned(f, project_path)
    if _movie_id_present(f["movies"][TABLE], movie.movie_id):
        raise ValueError(
            f"movie_id {movie.movie_id!r} already in {project_path} "
            "(movies are write-once; a new extraction of it is a new generation, "
            "write_generation)"
        )
    _check_parameters(f, project_path, traces)


def _check_parameters(f: Any, project_path: Path, traces: MoleculeTraces) -> None:
    """Refuse aperture/integration parameters that differ from the project's first ones."""
    stored = f[_SETTINGS_GROUP].get(_EXTRACTION_SETTINGS)
    if stored is None:
        return  # first extraction: nothing to be consistent with yet
    diffs = {
        name: (float(stored.attrs[name]), float(getattr(traces, name)))
        for name in _CONSISTENT_PARAMS
        if not np.isclose(float(stored.attrs[name]), float(getattr(traces, name)))
    }
    if diffs:
        raise ValueError(
            f"extraction parameters differ from this experiment's first extraction "
            f"in {project_path}: {diffs} (stored -> requested); the aperture / "
            "integration parameters must be consistent across an experiment's movies"
        )


# --- minimal readers (the write<->read round-trip; M2 builds the browser) ----


def read_molecules(project_path: str | Path) -> np.ndarray:
    """Read ``/molecules/table`` back as a structured array (a copy)."""
    import h5py  # noqa: PLC0415

    from tether.io.schema import TABLE  # noqa: PLC0415

    with h5py.File(Path(project_path), "r") as f:
        return f["molecules"][TABLE][:]


def read_acceptor_detected(project_path: str | Path) -> np.ndarray:
    """Read the per-molecule acceptor-detection flag, one ``int8`` per ``/molecules`` row.

    ``1`` -- an acceptor spot was detected within the colocalization distance of the
    molecule's mapped position; ``0`` -- none was; ``ACCEPTOR_DETECTED_NOT_RECORDED``
    (``-1``) -- the flag was never recorded, as for every molecule of a project
    extracted before #920 (ADR-0127). Row ``i`` is row ``i`` of ``/molecules``.
    """
    import h5py  # noqa: PLC0415

    from tether.io.schema import TABLE  # noqa: PLC0415

    with h5py.File(Path(project_path), "r") as f:
        group = f[_MOLECULES_GROUP]
        n_rows = int(group[TABLE].shape[0])
        out = np.full(n_rows, ACCEPTOR_DETECTED_NOT_RECORDED, dtype=np.int8)
        dataset = group.get(_ACCEPTOR_DETECTED)
        if dataset is not None:
            n = min(n_rows, int(dataset.shape[0]))
            out[:n] = dataset[:n]
    return out


def read_colocalization_policies(project_path: str | Path) -> dict[str, str]:
    """Return ``{movie_id: colocalization rule}`` for every movie of a project (ADR-0127).

    The rule is the ``colocalization`` entry of the movie's settings profile
    ([`read_movie_settings`][]). A movie whose profile names none was extracted before
    #920, when every extraction was donor-anchored (ADR-0015), so it reads as
    ``donor-anchored``: an existing project keeps the meaning it was written with.
    """
    import h5py  # noqa: PLC0415

    from tether.io.schema import TABLE  # noqa: PLC0415

    profiles = read_movie_settings(project_path)
    with h5py.File(Path(project_path), "r") as f:
        movie_ids = [_decode_key(v) for v in f["movies"][TABLE]["movie_id"][()]]
    return {
        movie_id: str(profiles.get(movie_id, {}).get(_COLOCALIZATION_KEY, _LEGACY_COLOCALIZATION))
        for movie_id in movie_ids
    }


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
