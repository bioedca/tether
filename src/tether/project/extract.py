# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""End-to-end native extraction pipeline (PRD §7.11, Appendix E; FR-EXTRACT, FR-BATCH).

Orchestrates the M1 imaging primitives into a single headless call that turns a
dual-channel TIFF movie into a populated ``.tether`` project::

    open_movie -> split_channels -> detection_image / detect_spots_by_mode (both halves)
    -> estimate_*_prealign + pair_control_points -> fit_registration_map
    -> colocalize (donor-anchored) -> extract_molecules -> write_extraction

The spot detector is selectable (``options.detection_mode`` ∈ {``wavelet``,
``intensity``, ``bandpass``} with an optional ``options.detection_threshold``;
PRD §11.2, ADR-0021) so a movie can be extracted with the Deep-LASI ``findPart``
method it was actually detected with. The default ``wavelet`` reproduces the
historical à trous detection unchanged. Passing ``tdat=<path>`` auto-applies both
the mode (``temp/ParticleDetectionMode``) and the mapping-reference channel's
``DetectionThreshold`` (decoded from the ``.tdat`` MCOS ``Channel`` blob),
overriding ``options.detection_mode`` / ``options.detection_threshold`` so a
re-extraction matches how the movie was actually detected.

Two registration sources are supported (PRD §7.1 "a native bead/grid fit *and* an
imported ``.tmap``"):

* **native** (default) -- control points are paired from the sample movie's *own*
  detections (coarse phase-correlation prealign -> mutual nearest-neighbour pairing
  -> degree-2 polynomial fit), with the over-gate branch left at ``warn``
  (accept-with-flag, never drop) so a sample movie whose residual exceeds the §11.2
  RMS gate is tagged ``low-confidence-registration`` rather than rejected (ADR-0014);
* **imported** -- pass ``tmap=<path>`` to apply Deep-LASI's own ``.tmap`` via
  [`tether.imaging.register.read_tmap`][] ->
  [`tether.imaging.calibrate.registration_map_from_tmap`][]. This skips the native
  detect/prealign/pair/fit and splits + reads at the ``.tmap``'s stored per-channel
  crop geometry (so ``options.donor_side`` is ignored). An imported bead-fitted map
  is trusted as-is: with no sample-movie control points to measure against, its
  residual is left unknown (NaN), so it never trips the over-gate flag (the
  ~1.6 px molecule-domain scatter of a bead map is colocalization, not registration,
  error -- see ADR-0014).

The native path splits each frame by a **channel layout** (#908,
[`tether.imaging.layout`][]): ``options.channel_layout`` -- side by side, top and
bottom, or one free rectangle per channel -- or, when that is ``None``, the
side-by-side split with the donor on ``options.donor_side``, which is the split
Tether used before #908. The layout is stored for each movie as
``/movies/layouts/<movie_id>``; the imported path takes its split from the ``.tmap``
and stores no layout record.

The imported ``.tmap``'s per-channel ``Rotation``/``Flip`` are **applied**: each
channel of the movie is split in Deep-LASI's ``processImage`` order (rotate by a
multiple of 90 degrees, then flip, then crop in the rotated/flipped frame), the same
transform Deep-LASI applied to the calibration image the map was fitted on (#559,
PRD Appendix E Stage 1). The rotation and flip are recorded per channel in
``/movies``. A stored rotation or flip that does not decode (an angle that is not
0, 90, 180 or 270, or a flip that is not ``[v, h]`` in ``{0, 1}``) is a clean
``ExtractionError``. The UCKOPSB calibration map stores neither, so it is split by
its crop alone, as before.

After the traces are integrated, an advisory check reads the acceptor-bleach steps for
the signature of swapped donor and acceptor halves ([`tether.imaging.side_check`][],
#646, ADR-0070). Its verdict is returned in [`ExtractionSummary`][] and stored in
``/settings/extraction``; it never changes the data and never fails the extraction.

[`add_movie_to_project`][] runs the same stages on another movie and appends it to an
existing project under its writer lock, as one atomic replace of a staged copy (#347,
ADR-0117).

Scope note (M1 S9 split): the extraction-vs-Deep-LASI acceptance oracle (recall /
Pearson / RMS, PRD §8 NFR-VALID(a)) lands in the follow-up PR (S9 PR-C), with the
gated full-movie fixture + full Deep-LASI export -- ADR-0014/ADR-0019. The rotation/
flip apply landed later (#559) and is tested on synthetic movies only: no real
rotated or flipped ``.tmap`` is in the reference data.
"""

from __future__ import annotations

import hashlib
import math
import os
import warnings
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

# These imports are intentionally module-level: ``tether.project.extract`` is
# only imported on the ``tether extract`` code path, so the heavy imaging/IO
# stack never loads for a bare ``tether --version`` (which imports only
# ``tether.cli``). Keep it out of ``tether.project.__init__`` for the same reason.
from tether import __version__
from tether.extract_choices import DETECTION_MODES, DONOR_SIDES, PREALIGN_MODES
from tether.imaging.calibrate import (
    OverGateRegistrationWarning,
    RegistrationMap,
    fit_registration_map,
    registration_map_from_tmap,
    write_calibration,
)
from tether.imaging.coloc import ColocalizedMolecules, colocalize
from tether.imaging.detect import detect_spots_by_mode, detection_image
from tether.imaging.extract import (
    MoleculeTraces,
    MovieMetadata,
    assert_appendable,
    extract_molecules,
    write_extraction,
)
from tether.imaging.layout import (
    LAYOUT_SIDE_BY_SIDE,
    ChannelLayout,
    ChannelLayoutError,
)
from tether.imaging.register import (
    estimate_similarity_prealign,
    estimate_translation_prealign,
    pair_control_points,
    read_tmap,
)
from tether.imaging.side_check import VERDICT_NOT_RUN, DonorSideCheck, check_donor_side
from tether.imaging.split import ChannelGeometry, split_channels
from tether.io.filename import parse_filename
from tether.io.movie import open_movie
from tether.io.tdat import read_detection_settings
from tether.project._guarded_publish import (
    guarded_project_write,
    publish_staged_project,
    require_publish_guard,
)
from tether.project.core import Project
from tether.project.lock import PublishGuardRequiredError

if TYPE_CHECKING:
    import numpy as np

# Channel-id convention for the native two-channel split: the donor is the
# registration *reference* (channel 0), the acceptor the *moving* channel (1).
_DONOR_CHANNEL = 0
_ACCEPTOR_CHANNEL = 1

_HASH_CHUNK = 1 << 20  # 1 MiB streaming read for the movie content hash.


class ExtractionError(Exception):
    """A user-facing extraction failure (bad inputs, too few control points).

    Raised for conditions the operator can act on; the CLI maps it to a non-zero
    exit code with the message on stderr (never a raw traceback).
    """


@dataclass(frozen=True)
class ExtractOptions:
    """Tunables for [`extract_movie`][] (PRD §11.2; defaults mirror the library).

    Every field is recorded verbatim into ``/settings/extraction`` for
    reproducibility (NFR-REPRO), so the defaults here are the pinned CLI contract
    rather than a passthrough to whatever the primitives currently default to.
    """

    donor_side: str = "left"
    channel_layout: ChannelLayout | None = None
    detection_mode: str = "wavelet"
    detection_threshold: float | None = None
    detection_block: int = 50
    min_separation: float | None = None
    prealign: str = "translation"
    prealign_upsample: int = 10
    prealign_low_sigma: float = 1.0
    prealign_high_sigma: float = 20.0
    pair_tol: float = 2.0
    rms_gate: float = 0.5
    window: int = 21
    coloc_distance: float = 3.0
    disk_radius: float = 3.0
    ring_inner: float = 6.0
    ring_outer: float = 8.0
    bg_window: int = 10

    def __post_init__(self) -> None:
        # Validate every operator-actionable tunable here so bad CLI input fails
        # with a clean ExtractionError (-> exit 1 + stderr) rather than a raw
        # ValueError from deep in a primitive (the "never a raw traceback" contract).
        # The allowed values are the tuples the CLI also gives argparse as `choices`
        # (#654), so the CLI refuses a wrong one at parse time; these checks still
        # guard the library API and the GUI.
        if self.donor_side not in DONOR_SIDES:
            raise ExtractionError(f"donor_side must be 'left' or 'right', got {self.donor_side!r}")
        # ``channel_layout`` (#908) says where each channel is in the frame; ``None``
        # is the side-by-side split ``donor_side`` describes (the layout before #908).
        # A layout names its own donor, so a non-default ``donor_side`` beside it is
        # a contradiction to refuse, not a value to ignore silently.
        if self.channel_layout is not None:
            if not isinstance(self.channel_layout, ChannelLayout):
                raise ExtractionError(
                    "channel_layout must be a tether.imaging.layout.ChannelLayout or None, "
                    f"got {type(self.channel_layout).__name__}"
                )
            if self.donor_side != "left":
                raise ExtractionError(
                    "donor_side applies only when channel_layout is None; name the donor "
                    f"in the channel layout instead (got donor_side={self.donor_side!r} "
                    f"and a {self.channel_layout.kind!r} layout)"
                )
        if self.detection_mode not in DETECTION_MODES:
            raise ExtractionError(
                f"detection_mode must be one of {DETECTION_MODES}, got {self.detection_mode!r}"
            )
        # The detection threshold (Deep-LASI ``DetectionThreshold``) is consumed only
        # by the intensity/bandpass detectors; ``None`` lets each mode use its own
        # faithful default (intensity 0.5, bandpass 0.98 — PRD §11.2). When supplied
        # it must lie in the detectors' own [0, 1) domain (a fraction of the
        # detection-image max). It is recorded but inert under the wavelet mode.
        if self.detection_threshold is not None and not 0.0 <= self.detection_threshold < 1.0:
            raise ExtractionError(
                "detection_threshold must be in [0, 1) (a fraction of the detection-image "
                f"max), got {self.detection_threshold}"
            )
        # ``min_separation`` is optional: ``None`` (default) lets each detection mode
        # use its own faithful minimum-separation NMS (wavelet 8, intensity/bandpass
        # 3 px; ADR-0022). When supplied it overrides that for every mode and must be
        # positive.
        if self.min_separation is not None and not self.min_separation > 0:
            raise ExtractionError(f"min_separation must be > 0, got {self.min_separation}")
        if self.prealign not in PREALIGN_MODES:
            raise ExtractionError(
                f"prealign must be 'translation' or 'similarity', got {self.prealign!r}"
            )
        if self.window < 1 or self.window % 2 == 0:
            raise ExtractionError(f"window must be a positive odd integer, got {self.window}")
        if not (0 < self.disk_radius <= self.ring_inner < self.ring_outer):
            raise ExtractionError(
                "radii must satisfy 0 < disk_radius <= ring_inner < ring_outer, got "
                f"disk_radius={self.disk_radius}, ring_inner={self.ring_inner}, "
                f"ring_outer={self.ring_outer}"
            )
        if 2 * self.ring_outer > self.window:
            raise ExtractionError(
                f"ring (2*{self.ring_outer}) does not fit in a {self.window}px window"
            )
        for name in ("detection_block", "prealign_upsample", "bg_window"):
            if getattr(self, name) < 1:
                raise ExtractionError(f"{name} must be >= 1, got {getattr(self, name)}")
        for name in ("pair_tol", "coloc_distance", "rms_gate"):
            if not getattr(self, name) > 0:
                raise ExtractionError(f"{name} must be > 0, got {getattr(self, name)}")
        if not (0 < self.prealign_low_sigma < self.prealign_high_sigma):
            raise ExtractionError(
                "prealign sigmas must satisfy 0 < prealign_low_sigma < prealign_high_sigma, "
                f"got {self.prealign_low_sigma}, {self.prealign_high_sigma}"
            )


@dataclass(frozen=True)
class ExtractionSummary:
    """The outcome of one [`extract_movie`][] run (returned to the CLI)."""

    output_path: Path
    movie_id: str
    calibration_id: str
    n_molecules: int
    n_control_points: int
    rms_residual: float
    low_confidence_registration: bool
    molecule_tags: tuple[str, ...]
    registration_source: str  # "native" (paired fit) or "imported" (.tmap)
    detection_mode: str  # the ParticleDetectionMode actually run (post .tdat apply)
    #: The advisory donor-side check (#646, ADR-0070); also stored in /settings/extraction.
    donor_side_check: DonorSideCheck | None = None


def resolve_channel_layout(options: ExtractOptions) -> ChannelLayout:
    """Return the channel layout an extraction with ``options`` applies (#908).

    ``options.channel_layout`` when set; otherwise the side-by-side split with the
    donor on ``options.donor_side`` -- the one layout Tether had before #908, so a
    default extraction is unchanged.
    """
    if options.channel_layout is not None:
        return options.channel_layout
    return ChannelLayout(kind=LAYOUT_SIDE_BY_SIDE, donor=options.donor_side)


def _layout_geometry(
    layout: ChannelLayout, width: int, height: int
) -> tuple[ChannelGeometry, ChannelGeometry]:
    """Build donor/acceptor [`ChannelGeometry`][] for ``layout`` on one frame size.

    Both channels get the *same* size (the half layouts drop an odd trailing column
    or row, and a ``rectangles`` layout must give two equal rectangles) so the
    per-channel detection images share a shape -- a precondition of the
    phase-correlation prealign. A layout that cannot split this frame becomes a
    clean [`ExtractionError`][].
    """
    try:
        return layout.geometries(height, width)
    except ChannelLayoutError as exc:
        raise ExtractionError(f"channel layout cannot split this movie: {exc}") from exc


def _hash_movie(path: Path) -> tuple[str, int, float]:
    """Return ``(sha256_hex, file_size, mtime)`` — the movie's content identity.

    The full SHA-256 feeds [`tether.imaging.extract.molecule_key`][] (the
    cross-file molecule identity, §5.1/§7.10); ``file_size``/``mtime`` populate
    the relocation-robust fast-signature fields of [`MovieMetadata`][].
    """
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK), b""):
            digest.update(chunk)
    stat = path.stat()
    return digest.hexdigest(), int(stat.st_size), float(stat.st_mtime)


def _settings(
    options: ExtractOptions,
    *,
    n_control_points: int,
    rms_residual: float,
    source: str,
    tmap_source: str | None,
    tdat_source: str | None,
    donor_side_check: DonorSideCheck,
) -> dict:
    """Assemble the JSON-serialisable ``/settings/extraction`` provenance dict."""
    settings = {"app_version": __version__, "pipeline": "native"}
    settings.update(asdict(options))
    settings["n_control_points"] = int(n_control_points)
    # JSON has no NaN; store an explicit ``None`` for an unmeasured residual.
    settings["registration_rms_px"] = float(rms_residual) if math.isfinite(rms_residual) else None
    settings["registration_source"] = source
    # The imported-.tmap source filename (``None`` for the native path) -- the
    # operator-visible provenance of which calibration file produced this extraction.
    settings["tmap_source"] = tmap_source
    # The imported-.tdat source filename (``None`` unless --tdat supplied the
    # detection mode) -- provenance of where the detection settings came from. The
    # resolved options.detection_mode / detection_threshold above already record
    # the values that were actually applied.
    settings["tdat_source"] = tdat_source
    # The advisory donor-side check's verdict, its counts and the thresholds it used
    # (#646, ADR-0070). It describes the traces stored here and never changed them.
    settings["donor_side_check"] = donor_side_check.to_dict()
    return settings


def _run_donor_side_check(traces: MoleculeTraces) -> DonorSideCheck:
    """Run the advisory donor-side check on the extracted traces; never raise.

    The check reads the background-subtracted traces and changes nothing. It is
    advisory, so any failure inside it is recorded as ``not-run`` with the reason
    instead of failing the extraction (#646).
    """
    try:
        return check_donor_side(traces.donor.intensity, traces.acceptor.intensity)
    except Exception as exc:  # advisory: an error here must never fail an extraction
        return DonorSideCheck(verdict=VERDICT_NOT_RUN, reason=f"{type(exc).__name__}: {exc}")


def _apply_tdat_detection(
    options: ExtractOptions, tdat_path: str | os.PathLike[str]
) -> ExtractOptions:
    """Override ``options`` detection settings with those decoded from a ``.tdat``.

    Reads the Deep-LASI ``ParticleDetectionMode`` and the mapping-reference
    channel's per-channel ``DetectionThreshold`` (from the MCOS ``Channel`` blob)
    and returns a copy of ``options`` with ``detection_mode`` /
    ``detection_threshold`` replaced, so a re-extraction reproduces the method the
    movie was actually detected with (NFR-REPRO). The decoded mode is re-validated
    by [`ExtractOptions`][]. Any decode failure becomes a clean
    ``.tdat``-centric [`ExtractionError`][].
    """
    try:
        detection = read_detection_settings(tdat_path)
    except Exception as exc:  # not a TIRFdata, unsupported mode, unreadable file
        raise ExtractionError(f"could not use --tdat {Path(tdat_path).name}: {exc}") from exc
    # A .tdat slimmed to plain leaves (no MCOS blob) decodes threshold=None; keep
    # the caller's threshold (each mode's faithful default) rather than wiping it.
    threshold = (
        detection.threshold if detection.threshold is not None else options.detection_threshold
    )
    return replace(options, detection_mode=detection.mode, detection_threshold=threshold)


def _detect_channels(
    donor_stack: np.ndarray,
    acceptor_stack: np.ndarray,
    options: ExtractOptions,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Build the per-half detection images and detected spot sets.

    Returns ``(donor_det, acceptor_det, donor_spots, acceptor_spots)``; the
    detection images seed the native prealign, the spots feed both paths' colocalize.
    Both halves are detected with the same selected mode/threshold
    (``options.detection_mode`` / ``options.detection_threshold``, PRD §11.2); the
    default ``wavelet`` + ``None`` reproduces the historical à trous detection
    exactly ([`detect_spots_by_mode`][] forwards each mode's faithful default).
    """
    donor_det = detection_image(donor_stack, block=options.detection_block)
    acceptor_det = detection_image(acceptor_stack, block=options.detection_block)
    donor_spots = detect_spots_by_mode(
        donor_det,
        mode=options.detection_mode,
        threshold=options.detection_threshold,
        min_separation=options.min_separation,
    )
    acceptor_spots = detect_spots_by_mode(
        acceptor_det,
        mode=options.detection_mode,
        threshold=options.detection_threshold,
        min_separation=options.min_separation,
    )
    return donor_det, acceptor_det, donor_spots, acceptor_spots


def _imported_registration_map(tmap_path: str | os.PathLike[str]) -> RegistrationMap:
    """Decode a ``.tmap`` and build the imported [`RegistrationMap`][].

    Wraps [`read_tmap`][] + [`registration_map_from_tmap`][] and translates any
    decode/build failure into a clean, ``.tmap``-centric [`ExtractionError`][]
    (the "never a raw traceback" contract). No sample-movie control points are
    supplied, so the imported map's residual stays unknown (NaN) and the bead-fitted
    calibration is trusted as-is -- it never trips the over-gate flag.

    The map's channel geometry carries each channel's crop **and** its decoded
    ``Rotation`` / ``Flip`` (#559), so the caller splits the movie in the same
    rotate -> flip -> crop frame the map was fitted in. A rotation or flip that does
    not decode becomes the same clean ``.tmap``-centric error as any bad map.
    """
    try:
        return registration_map_from_tmap(
            read_tmap(tmap_path),
            app_version=__version__,
            source_file=Path(tmap_path).name,
        )
    except Exception as exc:  # bad/foreign .tmap, ambiguous channels, bad rotation/flip, ...
        raise ExtractionError(f"could not use --tmap {Path(tmap_path).name}: {exc}") from exc


def _write_metadata_origins(
    path: Path,
    movie_id: str,
    frame_time: float,
    pixel_size_um: float | None,
    *,
    roles_from_tmap: bool,
) -> None:
    """Record where frame time, pixel size and channel roles came from (#911).

    A value read off the movie is origin ``file`` (a file frame time is verified).
    Channel roles are ``file`` from an imported ``.tmap`` and ``user`` on the native
    path, where the run's ``donor_side`` option names the donor half.
    """
    import h5py  # noqa: PLC0415

    from tether.project import movie_metadata as mm  # noqa: PLC0415

    known_time = frame_time > 0.0
    record = mm.MovieMetadataRecord(
        movie_id=movie_id,
        frame_time=frame_time if known_time else 0.0,
        frame_time_origin=mm.ORIGIN_FILE if known_time else mm.ORIGIN_UNKNOWN,
        frame_time_verified=known_time,
        pixel_size_um=pixel_size_um or 0.0,
        pixel_size_origin=mm.ORIGIN_FILE if pixel_size_um else mm.ORIGIN_UNKNOWN,
        channel_roles_origin=mm.ORIGIN_FILE if roles_from_tmap else mm.ORIGIN_USER,
    )
    with h5py.File(path, "r+") as f:
        mm.write_metadata_record(f, record)


@dataclass(frozen=True)
class _Extracted:
    """Everything stages 1-15 produce for one movie, before anything is persisted.

    [`extract_movie`][] and [`add_movie_to_project`][] both build this with the one
    [`_run_pipeline`][] and persist it with the one [`_persist`][], so the append path
    shares the new-project path verbatim (#347).
    """

    options: ExtractOptions
    sha256: str
    file_size: int
    mtime: float
    n_frames: int
    height: int
    width: int
    pixel_dtype: str
    byteorder: str
    frame_time: float
    pixel_size_um: float | None
    donor_geometry: ChannelGeometry
    acceptor_geometry: ChannelGeometry
    reg_map: RegistrationMap
    molecules: ColocalizedMolecules
    traces: MoleculeTraces
    side_check: DonorSideCheck
    # The layout the native split used (#908); None on the imported .tmap path.
    channel_layout: ChannelLayout | None


def _check_inputs(
    movie_path: Path,
    tmap: str | os.PathLike[str] | None,
    tdat: str | os.PathLike[str] | None,
) -> None:
    """Refuse a missing movie, ``.tmap`` or ``.tdat`` before any work."""
    if not movie_path.exists():
        raise ExtractionError(f"movie not found: {movie_path}")
    if tmap is not None and not Path(tmap).exists():
        raise ExtractionError(f"tmap not found: {tmap}")
    if tdat is not None and not Path(tdat).exists():
        raise ExtractionError(f"tdat not found: {tdat}")


def _resolve_sources(
    options: ExtractOptions,
    tmap: str | os.PathLike[str] | None,
    tdat: str | os.PathLike[str] | None,
) -> tuple[ExtractOptions, RegistrationMap | None]:
    """Apply a ``.tdat``'s detection settings and decode a ``.tmap``, before movie IO.

    A ``.tdat`` supplies the detection mode/threshold the movie was actually detected
    with, and an imported ``.tmap`` is independent of the movie; both are resolved up
    front so a bad file fails fast with a clean error and nothing is touched. The map
    is ``None`` for the native fit. A ``channel_layout`` beside a ``.tmap`` is refused
    here, before either file is read (#908).
    """
    if tmap is not None and options.channel_layout is not None:
        # The imported path splits at the .tmap's own crops; a layout beside it would
        # name a second, competing split.
        raise ExtractionError(
            "channel_layout and tmap both set the channel split; give only one (the "
            ".tmap splits at its own stored crop geometry)"
        )
    if tdat is not None:
        options = _apply_tdat_detection(options, tdat)
    imported_map = _imported_registration_map(tmap) if tmap is not None else None
    return options, imported_map


def _run_pipeline(
    movie_path: Path,
    options: ExtractOptions,
    imported_map: RegistrationMap | None,
    *,
    identity: tuple[str, int, float] | None = None,
) -> _Extracted:
    """Run stages 1-15 on one movie and return the unpersisted result.

    ``identity`` is the movie's ``(sha256, file_size, mtime)`` when the caller already
    hashed it ([`add_movie_to_project`][] does, to refuse a duplicate before the
    work); otherwise the movie is hashed here. Either way the file is re-stated after
    extraction, so a mid-run change is refused and the persisted
    [`MovieMetadata`][] describes exactly the bytes extracted.
    """
    # Convert any primitive/IO failure (a bad/compressed TIFF, a prealign image
    # too small, a degenerate fit) into a clean ExtractionError so the CLI never
    # leaks a raw traceback; an ExtractionError raised inside (e.g. too few
    # control points) passes through unchanged.
    try:
        # Hash up front (inside the try, so an IO error becomes a clean
        # ExtractionError) and re-stat after extraction to catch a mid-run change,
        # so the persisted MovieMetadata describes exactly the bytes extracted.
        sha256, file_size, mtime = identity if identity is not None else _hash_movie(movie_path)
        with open_movie(movie_path) as reader:
            n_frames, height, width = reader.shape
            pixel_dtype = str(reader.dtype)
            byteorder = reader.byteorder
            # 0.0 == "frame time unknown" (MovieMetadata's documented default). The
            # ImageJ finterval tag is the only source: the .tdat/.mat decode reads no
            # FrameTime, so a legacy import cannot supply one later either.
            frame_time = float(reader.frame_time) if reader.frame_time is not None else 0.0
            pixel_size_um = reader.pixel_size_um

            layout: ChannelLayout | None = None
            if imported_map is not None:
                # Imported path: registration comes from the .tmap. Split + detect
                # at its own stored channel geometry -- rotate, flip, then crop, as
                # Deep-LASI's processImage did for the map (#559) (donor = reference half,
                # acceptor = moving half), so donor_side / prealign / pairing are
                # not used. A map without crop geometry leaves the split undefined.
                reg_map = imported_map
                donor_geom = reg_map.reference_geometry
                acceptor_geom = reg_map.moving_geometry
                if donor_geom is None or acceptor_geom is None:
                    raise ExtractionError(
                        "imported .tmap lacks per-channel crop geometry; cannot split the movie"
                    )
                donor_stack, acceptor_stack = split_channels(reader.data, donor_geom, acceptor_geom)
                _, _, donor_spots, acceptor_spots = _detect_channels(
                    donor_stack, acceptor_stack, options
                )
            else:
                # Native path: pair control points from the movie's own detections.
                layout = resolve_channel_layout(options)
                donor_geom, acceptor_geom = _layout_geometry(layout, width, height)
                donor_stack, acceptor_stack = split_channels(reader.data, donor_geom, acceptor_geom)
                donor_det, acceptor_det, donor_spots, acceptor_spots = _detect_channels(
                    donor_stack, acceptor_stack, options
                )

                if options.prealign == "similarity":
                    prealign = estimate_similarity_prealign(
                        donor_det,
                        acceptor_det,
                        upsample_factor=options.prealign_upsample,
                        low_sigma=options.prealign_low_sigma,
                        high_sigma=options.prealign_high_sigma,
                    )
                else:
                    prealign = estimate_translation_prealign(
                        donor_det, acceptor_det, upsample_factor=options.prealign_upsample
                    )

                paired = pair_control_points(
                    donor_spots, acceptor_spots, tol=options.pair_tol, prealign=prealign
                )
                if len(paired.reference) < 2:
                    raise ExtractionError(
                        f"registration failed: only {len(paired.reference)} control-point "
                        f"pair(s) matched (need >= 2). Detected {len(donor_spots)} donor / "
                        f"{len(acceptor_spots)} acceptor spots; check the channel split, "
                        "detection sensitivity (--min-separation) or pairing tolerance "
                        "(--pair-tol)."
                    )

                # The over-gate verdict is reported once via ExtractionSummary; mute
                # the library's duplicate warning so headless output isn't doubled.
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", OverGateRegistrationWarning)
                    reg_map = fit_registration_map(
                        paired.reference,
                        paired.moving,
                        reference_channel=_DONOR_CHANNEL,
                        moving_channel=_ACCEPTOR_CHANNEL,
                        reference_geometry=donor_geom,
                        moving_geometry=acceptor_geom,
                        gate_px=options.rms_gate,
                        on_over_gate="warn",
                        app_version=__version__,
                    )

            molecules = colocalize(
                donor_spots,
                reg_map,
                donor_shape=donor_stack.shape[1:],
                acceptor_shape=acceptor_stack.shape[1:],
                acceptor_spots=acceptor_spots,
                window=options.window,
                coloc_distance_px=options.coloc_distance,
            )

            traces = extract_molecules(
                donor_stack,
                acceptor_stack,
                molecules,
                window=options.window,
                disk_radius=options.disk_radius,
                ring_inner=options.ring_inner,
                ring_outer=options.ring_outer,
                bg_window=options.bg_window,
            )
        stat = movie_path.stat()
        if int(stat.st_size) != file_size or float(stat.st_mtime) != mtime:
            raise ExtractionError(f"movie changed during extraction: {movie_path}")
    except ExtractionError:
        raise
    except Exception as exc:  # primitive/IO failure -> clean operator message
        raise ExtractionError(f"could not extract from {movie_path.name}: {exc}") from exc

    return _Extracted(
        options=options,
        sha256=sha256,
        file_size=file_size,
        mtime=mtime,
        n_frames=int(n_frames),
        height=int(height),
        width=int(width),
        pixel_dtype=pixel_dtype,
        byteorder=byteorder,
        frame_time=frame_time,
        pixel_size_um=pixel_size_um,
        donor_geometry=donor_geom,
        acceptor_geometry=acceptor_geom,
        reg_map=reg_map,
        molecules=molecules,
        traces=traces,
        # The advisory donor-side check reads the finished traces (#646, ADR-0070).
        side_check=_run_donor_side_check(traces),
        channel_layout=layout,
    )


def _persist(
    project_path: Path,
    extracted: _Extracted,
    movie_path: Path,
    *,
    tmap: str | os.PathLike[str] | None,
    tdat: str | os.PathLike[str] | None,
) -> tuple[str, str, list[str]]:
    """Write one movie's calibration + extraction into an existing project.

    Returns ``(movie_id, calibration_id, molecule_ids)``. Both writers are
    append-capable and write-once per id, and ``write_extraction`` keeps its own
    write-once-per-``movie_id`` and same-aperture checks, so the append path goes
    through exactly the rules a new project does (#347).
    """
    movie_id = f"mov-{uuid4()}"
    calibration_id = f"cal-{uuid4()}"
    reg_map = extracted.reg_map
    movie = MovieMetadata(
        movie_id=movie_id,
        sha256=extracted.sha256,
        n_frames=extracted.n_frames,
        height=extracted.height,
        width=extracted.width,
        uri=str(movie_path),
        pixel_dtype=extracted.pixel_dtype,
        byteorder=extracted.byteorder,
        frame_time=extracted.frame_time,
        calibration_id=calibration_id,
        donor_geometry=extracted.donor_geometry,
        acceptor_geometry=extracted.acceptor_geometry,
        file_size=extracted.file_size,
        mtime=extracted.mtime,
        channel_layout=extracted.channel_layout,
    )
    settings = _settings(
        extracted.options,
        n_control_points=reg_map.n_control_points,
        rms_residual=reg_map.rms_residual,
        source=reg_map.source,
        tmap_source=Path(tmap).name if tmap is not None else None,
        tdat_source=Path(tdat).name if tdat is not None else None,
        donor_side_check=extracted.side_check,
    )
    write_calibration(project_path, reg_map, calibration_id=calibration_id)
    molecule_ids = write_extraction(
        project_path,
        movie=movie,
        molecules=extracted.molecules,
        traces=extracted.traces,
        parsed=parse_filename(movie_path.name),
        registration_map=reg_map,
        settings=settings,
    )
    _write_metadata_origins(
        project_path,
        movie_id,
        extracted.frame_time,
        extracted.pixel_size_um,
        roles_from_tmap=tmap is not None,
    )
    return movie_id, calibration_id, molecule_ids


def _summary(
    output_path: Path,
    extracted: _Extracted,
    movie_id: str,
    calibration_id: str,
    molecule_ids: list[str],
) -> ExtractionSummary:
    reg_map = extracted.reg_map
    return ExtractionSummary(
        output_path=output_path,
        movie_id=movie_id,
        calibration_id=calibration_id,
        n_molecules=len(molecule_ids),
        n_control_points=int(reg_map.n_control_points),
        rms_residual=float(reg_map.rms_residual),
        low_confidence_registration=bool(reg_map.low_confidence),
        molecule_tags=tuple(reg_map.molecule_tags),
        registration_source=reg_map.source,
        detection_mode=extracted.options.detection_mode,
        donor_side_check=extracted.side_check,
    )


def extract_movie(
    movie_path: str | os.PathLike[str],
    output_path: str | os.PathLike[str],
    *,
    options: ExtractOptions | None = None,
    tmap: str | os.PathLike[str] | None = None,
    tdat: str | os.PathLike[str] | None = None,
    overwrite: bool = False,
    publish_guard: Callable[[], None] | None = None,
    refuse_over_gate: bool = False,
) -> ExtractionSummary:
    """Extract traces from a dual-channel ``movie_path`` into a new ``.tether``.

    Parameters
    ----------
    movie_path:
        An uncompressed (big-endian) dual-channel TIFF movie; where the two
        channels are in the frame is ``options.channel_layout`` (#908), by default
        the left/right halves with the donor on ``options.donor_side``.
    output_path:
        Destination ``.tether`` project; created fresh (must not exist unless
        ``overwrite``).
    options:
        Pipeline tunables (defaults: [`ExtractOptions`][]).
    tmap:
        Optional path to a Deep-LASI ``.tmap``. When given, registration is
        **imported** from it (the native detect/prealign/pair/fit is skipped); the
        two channels are split + read at the ``.tmap``'s own stored channel
        geometry (rotation, flip and crop, in that order), so
        ``options.donor_side`` is ignored and an ``options.channel_layout`` is
        refused. When ``None`` (default), a native fit is paired from the movie's
        own detections.
    tdat:
        Optional path to a Deep-LASI ``.tdat``. When given, the particle-detection
        mode (and, once the MCOS ``Channel`` decoder lands, the per-channel
        threshold) is read from it and **overrides** ``options.detection_mode`` /
        ``options.detection_threshold``, and the source filename is recorded as
        ``tdat_source`` in ``/settings/extraction`` -- so a re-extraction matches the
        method the movie was actually detected with. Independent of ``tmap`` (the two
        compose: import both registration and detection settings from Deep-LASI).
    overwrite:
        Replace an existing ``output_path``.
    publish_guard:
        Ownership check called after the new project is complete and immediately
        before its atomic publish; an exception aborts the publish and leaves an
        existing destination unchanged. It is **required** to replace an existing
        ``output_path`` (#346, ADR-0112): hold
        [`publish_lock`][tether.project.lock.publish_lock] on ``output_path`` and pass
        the check it yields. Without one, a new path is published only if it is
        still absent, and an existing one is refused.
    refuse_over_gate:
        Write nothing when the registration residual is over ``options.rms_gate``
        (a low-confidence registration), and raise instead. ``tether batch --policy
        fail --into`` uses it so that a rejected movie never enters the project
        (#1151). By default such a movie is written and flagged.

    Returns
    -------
    ExtractionSummary
        Counts + registration verdict for the run, and the advisory donor-side check
        (``donor_side_check``, #646): a verdict on whether the donor and acceptor halves
        look swapped. The check never changes the data and never fails the run; its
        result is also stored in ``/settings/extraction``.

    Raises
    ------
    ExtractionError
        Any operator-actionable failure: invalid options, a missing or
        unreadable/compressed movie, a missing or undecodable ``.tmap`` or
        ``.tdat`` (or a ``.tdat`` with an unsupported detection mode), a
        pre-existing output without ``overwrite`` or without a ``publish_guard``,
        an un-splittable frame (a channel layout whose rectangles are outside the
        frame, or a frame too small to cut in two), a channel layout given together
        with a ``.tmap``, too few
        matched control points to register (native path), a registration over the
        gate under ``refuse_over_gate``, or a write failure. Never a raw primitive
        traceback.
    """
    options = options or ExtractOptions()
    movie_path = Path(movie_path)
    output_path = Path(output_path)

    _check_inputs(movie_path, tmap, tdat)
    if output_path.exists() and not overwrite:
        raise ExtractionError(f"output exists: {output_path} (use overwrite=True / --overwrite)")
    try:
        require_publish_guard(output_path, publish_guard)
    except PublishGuardRequiredError as exc:
        raise ExtractionError(str(exc)) from exc

    options, imported_map = _resolve_sources(options, tmap, tdat)
    extracted = _run_pipeline(movie_path, options, imported_map)
    _check_gate(extracted, refuse_over_gate=refuse_over_gate)

    # --- Persist atomically (movie memmap is now closed) -----------------------
    # Build at a sibling temp path, then atomically replace the destination only
    # on full success: a failed run never leaves a partial .tether, and an
    # existing project is clobbered only once the new one is complete (the
    # multi-call write_calibration + write_extraction is otherwise non-atomic).
    tmp_path = output_path.with_name(f"{output_path.name}.{uuid4().hex}.tmp")
    try:
        Project.create(tmp_path, overwrite=True)
        movie_id, calibration_id, molecule_ids = _persist(
            tmp_path, extracted, movie_path, tmap=tmap, tdat=tdat
        )
        publish_staged_project(tmp_path, output_path, publish_guard=publish_guard)
    except ExtractionError:
        tmp_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        raise ExtractionError(f"failed to write {output_path.name}: {exc}") from exc

    return _summary(output_path, extracted, movie_id, calibration_id, molecule_ids)


def _movie_ids_with_sha256(project_path: Path, sha256: str) -> list[str]:
    """Return the ``movie_id`` of every ``/movies`` row whose content hash is ``sha256``."""
    import h5py  # noqa: PLC0415

    from tether.io.schema import TABLE  # noqa: PLC0415

    with h5py.File(project_path, "r") as f:
        table = f["movies"][TABLE][:]
    if table.shape[0] == 0:
        return []
    return [
        _decode(row["movie_id"])
        for row in table
        if _decode(row["sha256"]).lower() == sha256.lower()
    ]


def _decode(value: object) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _refuse_duplicate_movie(project_path: Path, sha256: str, movie_path: Path) -> None:
    """Refuse a movie whose content is already in the project (#347, ADR-0117).

    Each append mints a fresh ``movie_id``, so ``write_extraction``'s write-once rule
    alone would let the same file in twice under two ids, and every molecule would
    then share its ``molecule_key`` (movie ``sha256`` + donor position) with a
    molecule already stored: labels keyed by ``molecule_key`` could no longer tell
    the two apart. The content hash is the movie's identity, so it is refused here.
    """
    existing = _movie_ids_with_sha256(project_path, sha256)
    if existing:
        raise ExtractionError(
            f"{movie_path.name} is already in {project_path.name} as movie "
            f"{existing[0]!r} (same content sha256); a movie is appended once"
        )


#: The error of a movie refused by ``refuse_over_gate``; ``tether batch`` records the
#: same words for a movie its ``fail`` policy rejects.
OVER_GATE_ERROR = "registration residual exceeds the gate (policy=fail)"


def _check_gate(extracted: _Extracted, *, refuse_over_gate: bool) -> None:
    """Raise before anything is written when the registration is over its gate (#1151)."""
    if refuse_over_gate and extracted.reg_map.low_confidence:
        raise ExtractionError(OVER_GATE_ERROR)


def add_movie_to_project(
    project_path: str | os.PathLike[str],
    movie_path: str | os.PathLike[str],
    *,
    options: ExtractOptions | None = None,
    tmap: str | os.PathLike[str] | None = None,
    tdat: str | os.PathLike[str] | None = None,
    publish_guard: Callable[[], None] | None,
    refuse_over_gate: bool = False,
) -> ExtractionSummary:
    """Extract ``movie_path`` and append it to the existing project ``project_path``.

    Runs the same stages 1-15 as [`extract_movie`][] and writes through the same
    ``write_calibration`` + ``write_extraction`` calls, so the movie gains its own
    ``/movies`` row and calibration, its molecules are appended to ``/molecules``,
    and every ``/traces`` and ``/patches`` array grows in the same row order (the
    positional trace↔molecule join). A movie longer than every stored one widens
    each ``/traces`` array, and the existing rows gain zero-padded columns. The
    movie's settings profile is recorded under ``/settings/extraction/movies``; the
    first extraction's stamp is not changed (#347, ADR-0117).

    The update is written to a sibling copy of the project and published by one
    atomic replace, under the project's writer lock: ``publish_guard`` is called
    before the copy, after the write and immediately before the replace. Any failure
    — the guard included — removes the copy, so the project is left byte-identical.

    Parameters
    ----------
    project_path:
        An existing, compatible ``.tether`` project
        ([`create_project`][tether.project.create_project] or [`extract_movie`][]).
    movie_path:
        The movie to append; see [`extract_movie`][].
    options, tmap, tdat:
        As for [`extract_movie`][]. The aperture parameters (``window``, the radii and
        ``bg_window``) must equal the ones the project's first extraction recorded.
    publish_guard:
        **Required.** The check yielded by
        [`publish_lock`][tether.project.lock.publish_lock] held on ``project_path``
        (#346, ADR-0112).
    refuse_over_gate:
        As for [`extract_movie`][]: a movie whose registration is over the gate is
        not appended, and the project is left as it was (#1151).

    Returns
    -------
    ExtractionSummary
        The appended movie's outcome; ``output_path`` is ``project_path``.

    Raises
    ------
    ExtractionError
        Every operator-actionable failure [`extract_movie`][] reports, and also: a
        missing or incompatible project, a project whose ``/traces`` or ``/patches``
        arrays do not cover every molecule (an analysis-only import, a subset exported
        without its raw layers), no ``publish_guard``, a movie whose content
        is already in the project, aperture parameters that differ from the
        project's, a registration over the gate under ``refuse_over_gate``, or a
        guard that fails (the writer lock was lost).
    """
    options = options or ExtractOptions()
    project_path = Path(project_path)
    movie_path = Path(movie_path)

    _check_inputs(movie_path, tmap, tdat)
    if not project_path.is_file():
        raise ExtractionError(
            f"project not found: {project_path} (extract_movie creates a new project)"
        )
    try:
        assert_appendable(project_path)
    except Exception as exc:  # not HDF5, not a .tether, a newer schema, misaligned layers
        raise ExtractionError(f"cannot append to {project_path.name}: {exc}") from exc
    if publish_guard is None:
        raise ExtractionError(
            f"appending to {project_path.name} needs its writer lock "
            "(hold tether.project.lock.publish_lock and pass its check as publish_guard)"
        )

    options, imported_map = _resolve_sources(options, tmap, tdat)
    try:
        identity = _hash_movie(movie_path)
    except Exception as exc:
        raise ExtractionError(f"could not extract from {movie_path.name}: {exc}") from exc
    _refuse_duplicate_movie(project_path, identity[0], movie_path)

    extracted = _run_pipeline(movie_path, options, imported_map, identity=identity)
    _check_gate(extracted, refuse_over_gate=refuse_over_gate)

    try:
        with guarded_project_write(
            project_path, write_guard=publish_guard, label="append"
        ) as stage:
            # The copy is the state that will be published: check it, not the name.
            _refuse_duplicate_movie(stage, extracted.sha256, movie_path)
            movie_id, calibration_id, molecule_ids = _persist(
                stage, extracted, movie_path, tmap=tmap, tdat=tdat
            )
    except ExtractionError:
        raise
    except Exception as exc:  # aperture mismatch, lost lock, IO failure
        raise ExtractionError(f"failed to append to {project_path.name}: {exc}") from exc

    return _summary(project_path, extracted, movie_id, calibration_id, molecule_ids)
