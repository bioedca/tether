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
historical à trous detection unchanged. Passing ``tdat=<path>`` auto-applies the
mode decoded from a Deep-LASI ``.tdat`` (``temp/ParticleDetectionMode``),
overriding ``options.detection_mode`` (the per-channel ``DetectionThreshold`` MCOS
decode is a follow-up).

Two registration sources are supported (PRD §7.1 "a native bead/grid fit *and* an
imported ``.tmap``"):

* **native** (default) -- control points are paired from the sample movie's *own*
  detections (coarse phase-correlation prealign -> mutual nearest-neighbour pairing
  -> degree-2 polynomial fit), with the over-gate branch left at ``warn``
  (accept-with-flag, never drop) so a sample movie whose residual exceeds the §11.2
  RMS gate is tagged ``low-confidence-registration`` rather than rejected (ADR-0014);
* **imported** -- pass ``tmap=<path>`` to apply Deep-LASI's own ``.tmap`` via
  :func:`tether.imaging.register.read_tmap` ->
  :func:`tether.imaging.calibrate.registration_map_from_tmap`. This skips the native
  detect/prealign/pair/fit and splits + reads at the ``.tmap``'s stored per-channel
  crop geometry (so ``options.donor_side`` is ignored). An imported bead-fitted map
  is trusted as-is: with no sample-movie control points to measure against, its
  residual is left unknown (NaN), so it never trips the over-gate flag (the
  ~1.6 px molecule-domain scatter of a bead map is colocalization, not registration,
  error -- see ADR-0014).

The imported ``.tmap``'s per-channel ``Rotation``/``Flip`` are decoded but their
*apply* is deferred: a ``.tmap`` whose channels carry a non-identity rotation/flip
is **refused** (a clean ``ExtractionError``) rather than silently split at the wrong
frame; the UCKOPSB calibration map stores neither, so only the crop is needed.

Scope note (M1 S9 split): the extraction-vs-Deep-LASI acceptance oracle (recall /
Pearson / RMS, PRD §8 NFR-VALID(a)) lands in the follow-up PR (S9 PR-C), with the
gated full-movie fixture + full Deep-LASI export; that PR also lands the rotation/
flip *apply* (validated against the real movie) -- ADR-0014/ADR-0019.
"""

from __future__ import annotations

import hashlib
import math
import os
import warnings
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

# These imports are intentionally module-level: ``tether.project.extract`` is
# only imported on the ``tether extract`` code path, so the heavy imaging/IO
# stack never loads for a bare ``tether --version`` (which imports only
# ``tether.cli``). Keep it out of ``tether.project.__init__`` for the same reason.
from tether import __version__
from tether.imaging.calibrate import (
    OverGateRegistrationWarning,
    RegistrationMap,
    fit_registration_map,
    registration_map_from_tmap,
    write_calibration,
)
from tether.imaging.coloc import colocalize
from tether.imaging.detect import ParticleDetectionMode, detect_spots_by_mode, detection_image
from tether.imaging.extract import MovieMetadata, extract_molecules, write_extraction
from tether.imaging.register import (
    estimate_similarity_prealign,
    estimate_translation_prealign,
    pair_control_points,
    read_tmap,
)
from tether.imaging.split import ChannelGeometry, split_channels
from tether.io.filename import parse_filename
from tether.io.movie import open_movie
from tether.io.tdat import read_detection_settings
from tether.project.core import Project

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
    """Tunables for :func:`extract_movie` (PRD §11.2; defaults mirror the library).

    Every field is recorded verbatim into ``/settings/extraction`` for
    reproducibility (NFR-REPRO), so the defaults here are the pinned CLI contract
    rather than a passthrough to whatever the primitives currently default to.
    """

    donor_side: str = "left"
    detection_mode: str = "wavelet"
    detection_threshold: float | None = None
    detection_block: int = 50
    min_separation: float = 8.0
    prealign: str = "translation"
    prealign_upsample: int = 10
    prealign_low_sigma: float = 3.0
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
        if self.donor_side not in ("left", "right"):
            raise ExtractionError(f"donor_side must be 'left' or 'right', got {self.donor_side!r}")
        valid_modes = tuple(m.value for m in ParticleDetectionMode)
        if self.detection_mode not in valid_modes:
            raise ExtractionError(
                f"detection_mode must be one of {valid_modes}, got {self.detection_mode!r}"
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
        if self.prealign not in ("translation", "similarity"):
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
        for name in ("min_separation", "pair_tol", "coloc_distance", "rms_gate"):
            if not getattr(self, name) > 0:
                raise ExtractionError(f"{name} must be > 0, got {getattr(self, name)}")
        if not (0 < self.prealign_low_sigma < self.prealign_high_sigma):
            raise ExtractionError(
                "prealign sigmas must satisfy 0 < prealign_low_sigma < prealign_high_sigma, "
                f"got {self.prealign_low_sigma}, {self.prealign_high_sigma}"
            )


@dataclass(frozen=True)
class ExtractionSummary:
    """The outcome of one :func:`extract_movie` run (returned to the CLI)."""

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


def _half_split_geometry(
    width: int, height: int, donor_side: str
) -> tuple[ChannelGeometry, ChannelGeometry]:
    """Build donor/acceptor :class:`ChannelGeometry` for a vertical L/R split.

    Both halves are cropped to the *same* width ``width // 2`` (a trailing odd
    column is dropped) so the per-half detection images share a shape — a
    precondition of the phase-correlation prealign.
    """
    half = width // 2
    if half < 1:
        raise ExtractionError(f"movie too narrow ({width} px) to split into two channels")
    # ChannelGeometry.crop is [y1, x1, y2, x2], 1-based inclusive.
    left = ChannelGeometry(crop=(1, 1, height, half))
    right = ChannelGeometry(crop=(1, half + 1, height, 2 * half))
    return (left, right) if donor_side == "left" else (right, left)


def _hash_movie(path: Path) -> tuple[str, int, float]:
    """Return ``(sha256_hex, file_size, mtime)`` — the movie's content identity.

    The full SHA-256 feeds :func:`tether.imaging.extract.molecule_key` (the
    cross-file molecule identity, §5.1/§7.10); ``file_size``/``mtime`` populate
    the relocation-robust fast-signature fields of :class:`MovieMetadata`.
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
    return settings


def _apply_tdat_detection(
    options: ExtractOptions, tdat_path: str | os.PathLike[str]
) -> ExtractOptions:
    """Override ``options`` detection settings with those decoded from a ``.tdat``.

    Reads the Deep-LASI ``ParticleDetectionMode`` (and, once the MCOS ``Channel``
    decoder lands, the per-channel ``DetectionThreshold``) and returns a copy of
    ``options`` with ``detection_mode`` / ``detection_threshold`` replaced, so a
    re-extraction reproduces the method the movie was actually detected with
    (NFR-REPRO). The decoded mode is re-validated by :class:`ExtractOptions`. Any
    decode failure becomes a clean ``.tdat``-centric :class:`ExtractionError`.
    """
    try:
        detection = read_detection_settings(tdat_path)
    except Exception as exc:  # not a TIRFdata, unsupported mode, unreadable file
        raise ExtractionError(f"could not use --tdat {Path(tdat_path).name}: {exc}") from exc
    # ``threshold`` is ``None`` until the MCOS decoder lands; keep the caller's
    # threshold (each mode's faithful default) until then rather than wiping it.
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
    exactly (:func:`detect_spots_by_mode` forwards each mode's faithful default).
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
    """Decode a ``.tmap`` and build the imported :class:`RegistrationMap`.

    Wraps :func:`read_tmap` + :func:`registration_map_from_tmap` and translates any
    decode/build failure into a clean, ``.tmap``-centric :class:`ExtractionError`
    (the "never a raw traceback" contract). No sample-movie control points are
    supplied, so the imported map's residual stays unknown (NaN) and the bead-fitted
    calibration is trusted as-is -- it never trips the over-gate flag.
    """
    try:
        channels = read_tmap(tmap_path)
        unsupported = sorted(cid for cid, ch in channels.items() if not ch.has_simple_geometry)
        if unsupported:
            # The .tmap stores a per-channel rotation/flip that processImage applies
            # before cropping; the imported path honors only the crop so far, so
            # applying it would split at the wrong frame. Refuse loudly rather than
            # silently mis-extract (the rotation/flip apply is deferred to a follow-up).
            raise ExtractionError(
                f".tmap channel(s) {unsupported} carry a non-identity rotation/flip, which "
                "the imported registration path does not yet apply (only crop geometry is "
                "honored); re-run without --tmap to use a native fit."
            )
        return registration_map_from_tmap(
            channels,
            app_version=__version__,
            source_file=Path(tmap_path).name,
        )
    except ExtractionError:
        raise
    except Exception as exc:  # bad/foreign .tmap, ambiguous channels, ...
        raise ExtractionError(f"could not use --tmap {Path(tmap_path).name}: {exc}") from exc


def extract_movie(
    movie_path: str | os.PathLike[str],
    output_path: str | os.PathLike[str],
    *,
    options: ExtractOptions | None = None,
    tmap: str | os.PathLike[str] | None = None,
    tdat: str | os.PathLike[str] | None = None,
    overwrite: bool = False,
) -> ExtractionSummary:
    """Extract traces from a dual-channel ``movie_path`` into a new ``.tether``.

    Parameters
    ----------
    movie_path:
        An uncompressed (big-endian) dual-channel TIFF movie; the two channels
        are the left/right halves (see ``options.donor_side``).
    output_path:
        Destination ``.tether`` project; created fresh (must not exist unless
        ``overwrite``).
    options:
        Pipeline tunables (defaults: :class:`ExtractOptions`).
    tmap:
        Optional path to a Deep-LASI ``.tmap``. When given, registration is
        **imported** from it (the native detect/prealign/pair/fit is skipped); the
        two channels are split + read at the ``.tmap``'s own stored crop geometry,
        so ``options.donor_side`` is ignored. When ``None`` (default), a native fit
        is paired from the movie's own detections.
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

    Returns
    -------
    ExtractionSummary
        Counts + registration verdict for the run.

    Raises
    ------
    ExtractionError
        Any operator-actionable failure: invalid options, a missing or
        unreadable/compressed movie, a missing or undecodable ``.tmap`` or
        ``.tdat`` (or a ``.tdat`` with an unsupported detection mode), a
        pre-existing output without ``overwrite``, an un-splittable frame, too few
        matched control points to register (native path), or a write failure. Never
        a raw primitive traceback.
    """
    options = options or ExtractOptions()
    movie_path = Path(movie_path)
    output_path = Path(output_path)

    if not movie_path.exists():
        raise ExtractionError(f"movie not found: {movie_path}")
    if tmap is not None and not Path(tmap).exists():
        raise ExtractionError(f"tmap not found: {tmap}")
    if tdat is not None and not Path(tdat).exists():
        raise ExtractionError(f"tdat not found: {tdat}")
    if output_path.exists() and not overwrite:
        raise ExtractionError(f"output exists: {output_path} (use overwrite=True / --overwrite)")

    # A .tdat supplies the detection mode/threshold the movie was actually detected
    # with; resolve it up front (before any movie IO) so a bad or unsupported-mode
    # .tdat fails fast with a clean error and nothing is touched.
    if tdat is not None:
        options = _apply_tdat_detection(options, tdat)

    # Decode the imported .tmap up front (it is independent of the movie); a bad
    # map fails before any movie IO, so nothing is touched. ``None`` -> native fit.
    imported_map = _imported_registration_map(tmap) if tmap is not None else None

    # --- Stages 1-15, all while the movie memmap is open -----------------------
    # Convert any primitive/IO failure (a bad/compressed TIFF, a prealign image
    # too small, a degenerate fit) into a clean ExtractionError so the CLI never
    # leaks a raw traceback; an ExtractionError raised inside (e.g. too few
    # control points) passes through unchanged.
    try:
        # Hash up front (inside the try, so an IO error becomes a clean
        # ExtractionError) and re-stat after extraction to catch a mid-run change,
        # so the persisted MovieMetadata describes exactly the bytes extracted.
        sha256, file_size, mtime = _hash_movie(movie_path)
        with open_movie(movie_path) as reader:
            n_frames, height, width = reader.shape
            pixel_dtype = str(reader.dtype)
            byteorder = reader.byteorder
            # 0.0 == "frame time unknown" (MovieMetadata's documented default);
            # the authoritative interval arrives with the .tdat/.mat at import.
            frame_time = float(reader.frame_time) if reader.frame_time is not None else 0.0

            if imported_map is not None:
                # Imported path: registration comes from the .tmap. Split + detect
                # at its own stored channel crop geometry (donor = reference half,
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
                donor_geom, acceptor_geom = _half_split_geometry(width, height, options.donor_side)
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

    # --- Persist atomically (movie memmap is now closed) -----------------------
    parsed = parse_filename(movie_path.name)
    movie_id = f"mov-{uuid4()}"
    calibration_id = f"cal-{uuid4()}"

    movie = MovieMetadata(
        movie_id=movie_id,
        sha256=sha256,
        n_frames=n_frames,
        height=height,
        width=width,
        uri=str(movie_path),
        pixel_dtype=pixel_dtype,
        byteorder=byteorder,
        frame_time=frame_time,
        calibration_id=calibration_id,
        donor_geometry=donor_geom,
        acceptor_geometry=acceptor_geom,
        file_size=file_size,
        mtime=mtime,
    )
    settings = _settings(
        options,
        n_control_points=reg_map.n_control_points,
        rms_residual=reg_map.rms_residual,
        source=reg_map.source,
        tmap_source=Path(tmap).name if tmap is not None else None,
        tdat_source=Path(tdat).name if tdat is not None else None,
    )

    # Build at a sibling temp path, then atomically replace the destination only
    # on full success: a failed run never leaves a partial .tether, and an
    # existing project is clobbered only once the new one is complete (the
    # multi-call write_calibration + write_extraction is otherwise non-atomic).
    tmp_path = output_path.with_name(f"{output_path.name}.{uuid4().hex}.tmp")
    try:
        Project.create(tmp_path, overwrite=True)
        write_calibration(tmp_path, reg_map, calibration_id=calibration_id)
        molecule_ids = write_extraction(
            tmp_path,
            movie=movie,
            molecules=molecules,
            traces=traces,
            parsed=parsed,
            registration_map=reg_map,
            settings=settings,
        )
        os.replace(tmp_path, output_path)
    except ExtractionError:
        tmp_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        raise ExtractionError(f"failed to write {output_path.name}: {exc}") from exc

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
        detection_mode=options.detection_mode,
    )
