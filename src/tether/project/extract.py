# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""End-to-end native extraction pipeline (PRD §7.11, Appendix E; FR-EXTRACT, FR-BATCH).

Orchestrates the M1 imaging primitives into a single headless call that turns a
dual-channel movie (a TIFF, or a movie of any reader in
[`tether.io.movie.EXTRACTED_CONTAINERS`][]) into a populated ``.tether`` project::

    open_extraction_movie -> split_channels -> detection_image / detect_spots_by_mode (both halves)
    -> estimate_*_prealign + pair_control_points -> fit_registration_map
    -> colocalize (options.colocalization) -> extract_molecules -> write_extraction

The spot detector is selectable (``options.detection_mode`` ∈ {``wavelet``,
``intensity``, ``bandpass``, ``zmw``} with an optional ``options.detection_threshold``;
PRD §11.2, ADR-0021, ADR-0143) so a movie can be extracted with the Deep-LASI ``findPart``
method it was actually detected with. The default ``wavelet`` reproduces the
historical à trous detection unchanged. Passing ``tdat=<path>`` auto-applies both
the mode (``temp/ParticleDetectionMode``) and the mapping-reference channel's
``DetectionThreshold`` (decoded from the ``.tdat`` MCOS ``Channel`` blob),
overriding ``options.detection_mode`` / ``options.detection_threshold`` so a
re-extraction matches how the movie was actually detected.

Three registration sources are supported (PRD §7.1 "a native bead/grid fit *and* an
imported ``.tmap``", plus the Stage-10 map file), and each run uses exactly one:

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
  error -- see ADR-0014);
* **map file** -- pass ``map_path=<path>`` to apply a Tether ``.npz`` map
  ([`tether.imaging.calibrate.save_map`][], e.g. a bead map made once and reused for
  later movies) via [`tether.imaging.calibrate.load_map`][]. Like a ``.tmap`` it skips
  the native fit and splits at the map's own stored channel geometry, and it keeps
  the residual and the over-gate verdict the map was saved with (#353).

**Precedence.** With neither file the native fit runs. A ``.tmap`` or a map file
*replaces* the native fit (``options.donor_side`` is then ignored). The two files are
never combined: passing both is refused before any work, since neither can be said to
win. A ``.tdat`` is independent of all three -- it sets detection, not registration --
and its own rule is unchanged: it overrides ``options.detection_mode`` /
``options.detection_threshold``.

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

**A movie pair** (#909): with ``acceptor_movie=<path>`` the movie is the donor movie of
a two-camera acquisition and the other file its acceptor movie ([`tether.io.pair`][]).
Each file is one whole channel, so no channel layout is applied; the pair is checked
(frame count, frame time, pixel type, frame size) before any detection, and then the
same stages run on the two channel stacks. A map file for a pair is one made from a bead
file pair, whose two channels are whole frames; a map of one camera frame is refused for
a pair, and a pair map for one movie. The ``/movies`` row names the pair by
[`tether.io.pair.pair_sha256`][], and both files go into the source registry with their
roles.

[`add_movie_to_project`][] runs the same stages on another movie and appends it to an
existing project under its writer lock, as one atomic replace of a staged copy (#347,
ADR-0117). [`reextract_movie`][] runs them again on a movie the project already holds
and writes a new trace generation of it the same way; the previous generation stays
readable and its labels move to the molecules that did not move (#921, ADR-0136).

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
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

# These imports are intentionally module-level: ``tether.project.extract`` is
# only imported on the ``tether extract`` code path, so the heavy imaging/IO
# stack never loads for a bare ``tether --version`` (which imports only
# ``tether.cli``). Keep it out of ``tether.project.__init__`` for the same reason.
from tether import __version__
from tether.extract_choices import (
    COLOCALIZATION_POLICIES,
    DETECTION_MODES,
    DONOR_SIDES,
    PREALIGN_MODES,
)
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
    write_generation,
)
from tether.imaging.layout import (
    LAYOUT_SIDE_BY_SIDE,
    ChannelLayout,
    ChannelLayoutError,
)
from tether.imaging.map_asset import (
    MapFacts,
    MapReuseError,
    OpticsConfirmationNeeded,
    load_map_asset,
    require_map_reuse,
)
from tether.imaging.recipe import (
    FRAME_STEPS_SOURCES,
    ChannelRecipe,
    ChannelRecipeError,
    check_frame_fits,
    frame_view_shape,
    through_frame_steps,
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
from tether.io.frames import open_extraction_movie
from tether.io.generations import Generation, active_generation
from tether.io.pair import PAIR_ROLES, MoviePairError, check_pair, pair_sha256
from tether.io.tdat import read_detection_settings
from tether.project._guarded_publish import (
    guarded_project_write,
    publish_staged_project,
    require_publish_guard,
)
from tether.project.core import Project
from tether.project.correction_states import mark_traces_replaced_in
from tether.project.jobs import mark_stale_in, record_inputs_in
from tether.project.lock import PublishGuardRequiredError
from tether.project.sources import project_movie_uri

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
    #: The colocalization rule (ADR-0127): ``partner-in-both`` keeps a molecule only
    #: with a detected acceptor spot; ``donor-anchored`` keeps every in-frame donor.
    colocalization: str = "partner-in-both"
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
        # by the intensity/bandpass/zmw detectors; ``None`` lets each mode use its own
        # faithful default (intensity 0.5, bandpass 0.98, zmw 0.5 — PRD §11.2). When supplied
        # it must lie in the detectors' own [0, 1) domain (a fraction of the
        # detection-image max). It is recorded but inert under the wavelet mode.
        if self.detection_threshold is not None and not 0.0 <= self.detection_threshold < 1.0:
            raise ExtractionError(
                "detection_threshold must be in [0, 1) (a fraction of the detection-image "
                f"max), got {self.detection_threshold}"
            )
        # ``min_separation`` is optional: ``None`` (default) lets each detection mode
        # use its own faithful minimum-separation NMS (wavelet 8, intensity/bandpass/zmw
        # 3 px; ADR-0022). When supplied it overrides that for every mode and must be
        # positive.
        if self.min_separation is not None and not self.min_separation > 0:
            raise ExtractionError(f"min_separation must be > 0, got {self.min_separation}")
        if self.colocalization not in COLOCALIZATION_POLICIES:
            raise ExtractionError(
                f"colocalization must be one of {COLOCALIZATION_POLICIES}, "
                f"got {self.colocalization!r}"
            )
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
    #: ``"native"`` (paired fit), ``"imported"`` (``.tmap``) or ``"map-file"`` (``.npz``).
    registration_source: str
    detection_mode: str  # the ParticleDetectionMode actually run (post .tdat apply)
    #: The advisory donor-side check (#646, ADR-0070); also stored in /settings/extraction.
    donor_side_check: DonorSideCheck | None = None
    #: The colocalization rule this run used (ADR-0127).
    colocalization: str = "partner-in-both"
    #: ``{rule: n_molecules}`` -- the molecules each rule keeps from the same spots (#920).
    rule_counts: Mapping[str, int] = field(default_factory=dict)
    #: The RMS gate the registration was judged against (px): the run's ``rms_gate``, or a
    #: map file's own ``gate_px`` (#353). ``None`` only for a summary built without it.
    rms_gate: float | None = None
    #: The content SHA-256 stored for this movie when it was extracted.
    movie_sha256: str | None = None
    #: The channel layout the extraction was given (``options.channel_layout``, #908);
    #: ``None`` when the split came from ``donor_side`` or an imported ``.tmap``. The
    #: donor-side check's remedy names it rather than ``--donor-side`` (#1157).
    channel_layout: ChannelLayout | None = None
    #: The frame steps applied to every frame before the split (#1435), and their
    #: source: ``"map"``, ``"movie"``, or ``None`` when there were none.
    frame_steps: ChannelRecipe = ChannelRecipe()
    frame_steps_source: str | None = None


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
    the relocation-robust fast-signature fields of [`MovieMetadata`][]. For a
    cellSens ``.vsi`` movie the checksum also covers the ``.ets`` files that hold
    its frames ([`tether.io.movie_identity.content_sha256`][], #1398, ADR-0153);
    the size and modification time are the movie file's own.
    """
    from tether.io.movie_identity import content_sha256  # noqa: PLC0415

    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK), b""):
            digest.update(chunk)
    stat = path.stat()
    return content_sha256(path, digest.hexdigest()), int(stat.st_size), float(stat.st_mtime)


@dataclass(frozen=True)
class _FileRead:
    """One input file as the extraction read it: its path, checksum, size and mtime."""

    path: Path
    sha256: str
    size: int
    mtime: float


def _hash_inputs(movie_path: Path, acceptor_path: Path | None) -> tuple[_FileRead, ...]:
    """Hash the movie, or both files of a movie pair (donor first, #909)."""
    paths = (movie_path,) if acceptor_path is None else (movie_path, acceptor_path)
    return tuple(_FileRead(path, *_hash_movie(path)) for path in paths)


def _row_sha256(files: tuple[_FileRead, ...]) -> str:
    """Return the ``/movies.sha256`` of the inputs: the movie's, or the pair's (#909)."""
    if len(files) == 1:
        return files[0].sha256
    return pair_sha256(files[0].sha256, files[1].sha256)


def _whole_frame(height: int, width: int) -> ChannelGeometry:
    """Return the geometry of a whole camera frame as a channel (one file of a pair, #909)."""
    return ChannelGeometry(crop=(1, 1, int(height), int(width)))


def _is_pair_geometry(donor: ChannelGeometry, acceptor: ChannelGeometry) -> bool:
    """Whether a map's two channels share one geometry: a map made for a movie pair.

    The two channels of one camera frame are two different regions of it, so their
    geometries differ; the two channels of a pair are each a whole frame (#909).
    """
    return donor == acceptor


def _split_stacks(
    frames: np.ndarray,
    acceptor_frames: np.ndarray | None,
    donor_geom: ChannelGeometry,
    acceptor_geom: ChannelGeometry,
) -> tuple[np.ndarray, np.ndarray]:
    """Cut the two channel stacks: both from one movie, or one from each file of a pair."""
    if acceptor_frames is None:
        return split_channels(frames, donor_geom, acceptor_geom)
    return donor_geom.apply(frames), acceptor_geom.apply(acceptor_frames)


#: The ``RegistrationMap.provenance`` key of a map's default frame steps (#1435): the
#: map file's own, or the one given with a ``.tmap``, so ``/calibration`` keeps it.
_MAP_FRAME_STEPS = "map_frame_steps"


def _resolve_frame_steps(
    frame_steps: ChannelRecipe | None,
    reg_map: RegistrationMap | None,
    source: str = "movie",
) -> tuple[ChannelRecipe, str | None]:
    """Return the frame steps an extraction applies and their source (#1435).

    A movie's own steps (``"none"`` included) replace the map's default; with neither,
    there are no steps and no source, and the frames are read as stored.
    """
    if frame_steps is not None:
        return frame_steps, source
    default = None if reg_map is None else reg_map.provenance.get(_MAP_FRAME_STEPS)
    if default is not None:
        return ChannelRecipe.parse(str(default)), "map"
    return ChannelRecipe(), None


def _frame_steps_record(steps: ChannelRecipe, source: str) -> dict[str, Any]:
    """Return the ``frame_steps`` entry of a movie's settings profile (#1435)."""
    return {"steps": str(steps), "source": source}


def _settings(
    options: ExtractOptions,
    *,
    n_control_points: int,
    rms_residual: float,
    source: str,
    tmap_source: str | None,
    map_source: str | None,
    tdat_source: str | None,
    donor_side_check: DonorSideCheck,
    frame_steps: ChannelRecipe | None = None,
    frame_steps_source: str | None = None,
) -> dict:
    """Assemble the JSON-serialisable ``/settings/extraction`` provenance dict."""
    frame_steps = ChannelRecipe() if frame_steps is None else frame_steps
    settings = {"app_version": __version__, "pipeline": "native"}
    settings.update(asdict(options))
    settings["n_control_points"] = int(n_control_points)
    # JSON has no NaN; store an explicit ``None`` for an unmeasured residual.
    settings["registration_rms_px"] = float(rms_residual) if math.isfinite(rms_residual) else None
    settings["registration_source"] = source
    # The imported-.tmap source filename (``None`` for the native path) -- the
    # operator-visible provenance of which calibration file produced this extraction.
    settings["tmap_source"] = tmap_source
    # The Tether .npz map-file filename (``None`` unless map_path supplied the map,
    # #353); its sha256 is in the /calibration provenance under ``map_file``.
    settings["map_source"] = map_source
    # The imported-.tdat source filename (``None`` unless --tdat supplied the
    # detection mode) -- provenance of where the detection settings came from. The
    # resolved options.detection_mode / detection_threshold above already record
    # the values that were actually applied.
    settings["tdat_source"] = tdat_source
    # The advisory donor-side check's verdict, its counts and the thresholds it used
    # (#646, ADR-0070). It describes the traces stored here and never changed them.
    settings["donor_side_check"] = donor_side_check.to_dict()
    # The frame steps applied before the split and where they came from (#1435); a run
    # with none from anywhere records nothing, so its profile is as before.
    if frame_steps_source is not None:
        settings["frame_steps"] = _frame_steps_record(frame_steps, frame_steps_source)
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


def _imported_registration_map(
    tmap_path: str | os.PathLike[str], map_frame_steps: ChannelRecipe | None = None
) -> RegistrationMap:
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
        reg_map = registration_map_from_tmap(
            read_tmap(tmap_path),
            app_version=__version__,
            source_file=Path(tmap_path).name,
        )
    except Exception as exc:  # bad/foreign .tmap, ambiguous channels, bad rotation/flip, ...
        raise ExtractionError(f"could not use --tmap {Path(tmap_path).name}: {exc}") from exc
    if map_frame_steps is None:
        return reg_map
    # A .tmap holds no frame steps; the default given with it is kept with the map's
    # record in /calibration (#1435).
    provenance = {**reg_map.provenance, _MAP_FRAME_STEPS: str(map_frame_steps)}
    return replace(reg_map, provenance=provenance)


def _map_file_registration_map(map_path: str | os.PathLike[str]) -> RegistrationMap:
    """Load a Tether ``.npz`` map file for extraction (#353).

    Wraps [`load_map`][] and translates a bad file into a clean, map-centric
    [`ExtractionError`][]. The map is used as saved: its coefficients, geometry,
    residual and over-gate verdict are not changed. Its provenance gains a
    ``map_file`` record (the file name, the sha256 of its bytes, and the map asset's
    record: map id, origin, source ids, layout, optics text and pixel size; #916), so
    the ``/calibration`` group says which file produced the map and a project names
    the id and the checksum of each map it used. [`_resolve_sources`][]
    then records the map's own ``gate_px`` as the run's ``rms_gate``. The file is read once
    and the map decoded from those bytes, so a file replaced during the call cannot
    pair one file's digest with another's coefficients. A map without channel
    geometry for both halves is refused, since the movie could not be split.
    """
    path = Path(map_path)
    try:
        content = path.read_bytes()  # one read: the digest names the bytes decoded
        digest = hashlib.sha256(content).hexdigest()
        asset = load_map_asset(path, content=content)
    except Exception as exc:  # unreadable, not a map file, newer version, pickled data
        raise ExtractionError(f"could not use --map {path.name}: {exc}") from exc
    reg_map = asset.registration_map
    if reg_map.reference_geometry is None or reg_map.moving_geometry is None:
        raise ExtractionError(
            f"could not use --map {path.name}: the map has no channel geometry for both "
            "halves, so the movie cannot be split with it"
        )
    provenance = dict(reg_map.provenance)
    provenance["map_file"] = {"name": path.name, "sha256": digest, **asset.record()}
    if asset.quality is not None:  # the file's record, so a saved copy keeps it
        provenance["map_quality"] = asset.quality
    # The file's own default frame steps are the map's default (#1435); a key inherited in
    # the provenance (a calibration saved with save_project_map) is not.
    provenance.pop(_MAP_FRAME_STEPS, None)
    if asset.frame_steps is not None:
        provenance[_MAP_FRAME_STEPS] = str(asset.frame_steps)
    return replace(reg_map, provenance=provenance)


def _check_map_reuse(
    reg_map: RegistrationMap,
    movie_path: Path,
    pixel_size_um: float | None,
    *,
    map_optics: str | None,
    confirm_optics: bool,
) -> RegistrationMap:
    """Run the reuse check of a map file against this movie and record it (#916).

    The movie's pixel size comes from its file, its optics text from ``map_optics``. A
    different pixel size is refused; a different optics text is refused unless
    ``confirm_optics``. The check's result is added to the ``map_file`` record. A map
    that came from no map file is returned unchanged.
    """
    record = reg_map.provenance.get("map_file")
    if not isinstance(record, dict):
        return reg_map
    layout = record.get("channel_layout")
    map_facts = MapFacts(
        channel_layout=None if layout is None else ChannelLayout.from_dict(layout),
        geometry=(reg_map.reference_geometry, reg_map.moving_geometry),  # type: ignore[arg-type]
        pixel_size_um=record.get("pixel_size_um"),
        optics=str(record.get("optics") or ""),
    )
    movie = MapFacts(pixel_size_um=pixel_size_um, optics=map_optics or "")
    try:
        check = require_map_reuse(map_facts, movie, confirm_optics=confirm_optics)
    except OpticsConfirmationNeeded as exc:
        raise ExtractionError(
            f"{movie_path.name}: could not use --map {record.get('name')}: {exc} "
            "(--confirm-optics, or confirm_optics=True)"
        ) from exc
    except MapReuseError as exc:
        raise ExtractionError(
            f"{movie_path.name}: could not use --map {record.get('name')}: {exc}"
        ) from exc
    provenance = dict(reg_map.provenance)
    provenance["map_file"] = {
        **record,
        "movie_optics": map_optics or "",
        "reuse": check.to_dict(optics_confirmed=bool(check.warnings) and confirm_optics),
    }
    return replace(reg_map, provenance=provenance)


def _registration_source(reg_map: RegistrationMap, map_path: object) -> str:
    """Return the run's registration source: the map's own, or ``"map-file"`` for an ``.npz``."""
    return "map-file" if map_path is not None else reg_map.source


def _roles_from_file(tmap: object, map_path: object) -> bool:
    """Whether a map's geometry, not the ``donor_side`` option, names the channel halves."""
    return tmap is not None or map_path is not None


def _write_metadata_origins(
    path: Path,
    movie_id: str,
    frame_time: float,
    pixel_size_um: float | None,
    *,
    roles_from_file: bool,
) -> None:
    """Record where frame time, pixel size and channel roles came from (#911).

    A value read off the movie is origin ``file`` (a file frame time is verified).
    Channel roles are ``file`` from an imported ``.tmap`` or a map file (#353), whose
    geometry names the halves, and ``user`` on the native path, where the run's
    ``donor_side`` option names the donor half.
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
        channel_roles_origin=mm.ORIGIN_FILE if roles_from_file else mm.ORIGIN_USER,
    )
    with h5py.File(path, "r+") as f:
        mm.write_metadata_record(f, record)


def _write_roles_origin(path: Path, movie_id: str, *, roles_from_file: bool) -> None:
    """Record the channel-role origin of a re-extracted movie's new layout (#1309).

    The new generation's layout comes from this run's sources, so its roles take
    the origin a fresh extraction with those sources records
    (`_write_metadata_origins`). The frame time, its verification and the pixel
    size are the movie's, not the run's, and stay as the store holds them; other
    movies' records are not touched.
    """
    import h5py  # noqa: PLC0415

    from tether.project import movie_metadata as mm  # noqa: PLC0415

    origin = mm.ORIGIN_FILE if roles_from_file else mm.ORIGIN_USER
    record = replace(mm.read_movie_metadata(path)[movie_id], channel_roles_origin=origin)
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
    # The files read: the movie, or the donor and acceptor movies of a pair (#909).
    files: tuple[_FileRead, ...] = ()
    # The frame steps applied before the split and their source (#1435).
    frame_steps: ChannelRecipe = ChannelRecipe()
    frame_steps_source: str | None = None

    @property
    def is_pair(self) -> bool:
        """Whether the acquisition is a movie pair (two files, #909)."""
        return len(self.files) == 2


def _check_inputs(
    movie_path: Path,
    tmap: str | os.PathLike[str] | None,
    tdat: str | os.PathLike[str] | None,
    map_path: str | os.PathLike[str] | None = None,
    acceptor_path: Path | None = None,
) -> None:
    """Refuse a missing movie, ``.tmap``, map file or ``.tdat``, or two maps, before any work.

    With ``acceptor_path`` (a movie pair, #909) the acceptor movie must exist and be
    another file than the donor movie, and no ``.tmap`` is taken: a Deep-LASI map splits
    one camera frame.
    """
    if acceptor_path is not None:
        if tmap is not None:
            raise ExtractionError(
                "a .tmap cannot be applied to a movie pair: its channel crops are regions of "
                "one camera frame; use a native fit or a map file made from a bead file pair"
            )
        if not acceptor_path.exists():
            raise ExtractionError(f"acceptor movie not found: {acceptor_path}")
        if movie_path.exists() and os.path.samefile(movie_path, acceptor_path):
            raise ExtractionError(
                f"the donor and acceptor movies are one file ({movie_path.name}); a movie "
                "pair is two files"
            )
    if tmap is not None and map_path is not None:
        raise ExtractionError(
            "--tmap and --map cannot be combined (each one supplies the registration map); "
            "pass one or the other"
        )
    if not movie_path.exists():
        raise ExtractionError(f"movie not found: {movie_path}")
    if tmap is not None and not Path(tmap).exists():
        raise ExtractionError(f"tmap not found: {tmap}")
    if map_path is not None and not Path(map_path).is_file():
        raise ExtractionError(f"map file not found: {map_path}")
    if tdat is not None and not Path(tdat).exists():
        raise ExtractionError(f"tdat not found: {tdat}")


def _resolve_sources(
    options: ExtractOptions,
    tmap: str | os.PathLike[str] | None,
    tdat: str | os.PathLike[str] | None,
    map_path: str | os.PathLike[str] | None = None,
    *,
    pair: bool = False,
    map_frame_steps: ChannelRecipe | None = None,
) -> tuple[ExtractOptions, RegistrationMap | None]:
    """Apply a ``.tdat``'s detection settings and load a ``.tmap`` or map file, before movie IO.

    A ``.tdat`` supplies the detection mode/threshold the movie was actually detected
    with, and an imported ``.tmap`` or a saved map file is independent of the movie;
    all are resolved up front so a bad file fails fast with a clean error and nothing
    is touched. The map is ``None`` for the native fit. [`_check_inputs`][] has already
    refused a ``.tmap`` together with a map file. A ``channel_layout`` beside a
    ``.tmap`` (#908) or a map file (#353) is refused here, before either file is read. A
    movie pair (``pair``, #909) takes neither a channel layout nor a ``donor_side``: each
    file is one whole channel, and the roles name the donor. ``map_frame_steps`` is the
    default frame steps of a ``.tmap`` (#1435), which holds none of its own; it is refused
    without a ``.tmap``, since a saved map file stores its own default.
    """
    if map_frame_steps is not None and tmap is None:
        raise ExtractionError(
            "map_frame_steps (--map-frame-steps) is the default frame steps of a .tmap; a "
            "saved map file stores its own default, and a movie's own steps are "
            "frame_steps (--frame-steps)"
        )
    if pair and (options.channel_layout is not None or options.donor_side != "left"):
        raise ExtractionError(
            "channel_layout and donor_side apply to one movie with both channels in each "
            "frame; in a movie pair each file is one whole channel, named by its role"
        )
    if tmap is not None and options.channel_layout is not None:
        # The imported path splits at the .tmap's own crops; a layout beside it would
        # name a second, competing split.
        raise ExtractionError(
            "channel_layout and tmap both set the channel split; give only one (the "
            ".tmap splits at its own stored crop geometry)"
        )
    if map_path is not None and options.channel_layout is not None:
        # A map file splits at its own stored geometry, the same way (#353).
        raise ExtractionError(
            "channel_layout and map_path both set the channel split; give only one (the "
            "map file splits at its own stored channel geometry)"
        )
    if tdat is not None:
        options = _apply_tdat_detection(options, tdat)
    if tmap is not None:
        return options, _imported_registration_map(tmap, map_frame_steps)
    if map_path is not None:
        reg_map = _map_file_registration_map(map_path)
        # The saved map was judged against its own gate; record that gate, so the
        # profile and a resumed batch's over-gate check agree with the map applied.
        return replace(options, rms_gate=reg_map.gate_px), reg_map
    return options, None


def _run_pipeline(
    movie_path: Path,
    options: ExtractOptions,
    imported_map: RegistrationMap | None,
    *,
    identity: tuple[_FileRead, ...] | None = None,
    acceptor_path: Path | None = None,
    map_optics: str | None = None,
    confirm_optics: bool = False,
    frame_steps: ChannelRecipe | None = None,
    frame_steps_source: str = "movie",
) -> _Extracted:
    """Run stages 1-15 on one movie, or on a movie pair, and return the unpersisted result.

    ``frame_steps`` are the movie's own frame steps (#1435), or ``None`` to use the map's
    default ([`_resolve_frame_steps`][]). The steps run on every frame before the split;
    the map's, the layout's or a pair's cut is read in the frame after them, and the
    geometry stored for the movie folds both into one geometry of the stored frame.

    ``identity`` is the files' [`_FileRead`][] records when the caller already hashed
    them ([`add_movie_to_project`][] does, to refuse a duplicate before the work);
    otherwise they are hashed here. With ``acceptor_path`` the movie is the donor of a
    movie pair (#909): the pair is checked ([`tether.io.pair.check_pair`][]) and each
    file is one whole channel; a map file must then be one made for a pair, and a map
    made for a pair is refused for one movie. Either way the file is re-stated after
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
        files = identity if identity is not None else _hash_inputs(movie_path, acceptor_path)
        with ExitStack() as opened:
            reader = opened.enter_context(open_extraction_movie(movie_path))
            acceptor_reader = None
            if acceptor_path is not None:
                acceptor_reader = opened.enter_context(open_extraction_movie(acceptor_path))
                try:
                    check_pair(
                        reader,
                        acceptor_reader,
                        donor_name=movie_path.name,
                        acceptor_name=acceptor_path.name,
                    )
                except MoviePairError as exc:
                    raise ExtractionError(str(exc)) from exc
            n_frames, height, width = reader.shape
            pixel_dtype = str(reader.dtype)
            byteorder = reader.byteorder
            # 0.0 == "frame time unknown" (MovieMetadata's documented default). The movie
            # file is the only source (a TIFF's ImageJ finterval tag, an ND2's frame
            # times): the .tdat/.mat decode reads no FrameTime, so a legacy import
            # cannot supply one later either.
            frame_time = float(reader.frame_time) if reader.frame_time is not None else 0.0
            pixel_size_um = reader.pixel_size_um

            # Frame steps (#1435): every frame is turned, flipped, moved or cropped as a
            # whole before the split, so every cut below is read in the frame after them.
            # A view of the stored frames: no pixel is resampled or copied here.
            steps, steps_source = _resolve_frame_steps(
                frame_steps, imported_map, frame_steps_source
            )
            frames = reader.data
            acceptor_frames = None if acceptor_reader is None else acceptor_reader.data
            stored_shape = (height, width)
            if steps.steps:
                try:
                    height, width = frame_view_shape(steps, height, width)
                except ChannelRecipeError as exc:
                    raise ExtractionError(f"{movie_path.name}: {exc}") from exc
                frames = steps.apply(frames)
                if acceptor_frames is not None:
                    acceptor_frames = steps.apply(acceptor_frames)
            after_steps = (
                f" after the frame steps {steps} (the movie's frames are "
                f"{stored_shape[0]} x {stored_shape[1]} px; {height} x {width} px after them)"
                if steps.steps
                else ""
            )

            layout: ChannelLayout | None = None
            if imported_map is not None:
                # Imported path: registration comes from the .tmap or the map file.
                # Split + detect at its own stored channel geometry -- rotate, flip,
                # then crop, as Deep-LASI's processImage did for the map (#559) (donor =
                # reference half, acceptor = moving half), so donor_side / prealign /
                # pairing are not used. A map without crop geometry leaves the split
                # undefined.
                reg_map = imported_map
                donor_geom = reg_map.reference_geometry
                acceptor_geom = reg_map.moving_geometry
                if donor_geom is None or acceptor_geom is None:
                    raise ExtractionError(
                        "imported map lacks per-channel crop geometry; cannot split the movie"
                    )
                if _is_pair_geometry(donor_geom, acceptor_geom) != (acceptor_reader is not None):
                    # A pair map's channels are whole frames of two files; a one-movie
                    # map's are two regions of one frame. Either used for the other
                    # would read both channels from the wrong pixels (#909).
                    raise ExtractionError(
                        "the map was made for a movie pair (donor and acceptor in two files) "
                        "and this is one movie"
                        if acceptor_reader is None
                        else "the map splits one camera frame into two channels and this is "
                        "a movie pair; make the map from a bead file pair"
                    )
                # A movie turned against its map, or smaller than it, is refused here
                # with both geometries named, not split at the wrong pixels (#887, #910).
                try:
                    check_frame_fits(height, width, donor_geom, acceptor_geom)
                except ChannelRecipeError as exc:
                    raise ExtractionError(f"{movie_path.name}: {exc}{after_steps}") from exc
                # A map file is checked against what is known of this movie: its pixel
                # size and the optics text the user gives; never its date (#916).
                reg_map = _check_map_reuse(
                    reg_map,
                    movie_path,
                    pixel_size_um,
                    map_optics=map_optics,
                    confirm_optics=confirm_optics,
                )
                donor_stack, acceptor_stack = _split_stacks(
                    frames, acceptor_frames, donor_geom, acceptor_geom
                )
                _, _, donor_spots, acceptor_spots = _detect_channels(
                    donor_stack, acceptor_stack, options
                )
            else:
                # Native path: pair control points from the movie's own detections.
                if acceptor_reader is None:
                    layout = resolve_channel_layout(options)
                    try:
                        donor_geom, acceptor_geom = _layout_geometry(layout, width, height)
                    except ExtractionError as exc:
                        raise ExtractionError(f"{exc}{after_steps}") from exc
                else:  # a movie pair: each file is one whole channel (#909)
                    donor_geom = acceptor_geom = _whole_frame(height, width)
                donor_stack, acceptor_stack = _split_stacks(
                    frames, acceptor_frames, donor_geom, acceptor_geom
                )
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
                policy=options.colocalization,
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
        for read in files:
            stat = read.path.stat()
            if int(stat.st_size) != read.size or float(stat.st_mtime) != read.mtime:
                raise ExtractionError(f"movie changed during extraction: {read.path}")
    except ExtractionError:
        raise
    except Exception as exc:  # primitive/IO failure -> clean operator message
        raise ExtractionError(f"could not extract from {movie_path.name}: {exc}") from exc

    if steps_source == "map" and _MAP_FRAME_STEPS not in reg_map.provenance:
        # A map default reused from the movie's record (a re-extraction with a .tmap,
        # which holds none) is kept with the map's record, so /calibration and a map
        # saved from it still carry it (#1435). A movie's own steps never become one.
        provenance = {**reg_map.provenance, _MAP_FRAME_STEPS: str(steps)}
        reg_map = replace(reg_map, provenance=provenance)

    return _Extracted(
        options=options,
        sha256=_row_sha256(files),
        file_size=files[0].size,
        mtime=files[0].mtime,
        n_frames=int(n_frames),
        height=int(stored_shape[0]),
        width=int(stored_shape[1]),
        pixel_dtype=pixel_dtype,
        byteorder=byteorder,
        frame_time=frame_time,
        pixel_size_um=pixel_size_um,
        # The stored geometry is one geometry of the stored frame: the frame steps, then
        # the cut (#1435), so every later read of the movie's pixels needs no steps.
        donor_geometry=through_frame_steps(steps, donor_geom, stored_shape),
        acceptor_geometry=through_frame_steps(steps, acceptor_geom, stored_shape),
        reg_map=reg_map,
        molecules=molecules,
        traces=traces,
        # The advisory donor-side check reads the finished traces (#646, ADR-0070).
        side_check=_run_donor_side_check(traces),
        channel_layout=layout,
        files=files,
        frame_steps=steps,
        frame_steps_source=steps_source,
    )


#: The ``/settings/batch`` marks naming the movies each project stage of
#: ``tether batch --into`` covers (#1151); an empty mark means "not every movie".
_BATCH_SETTINGS = "batch"
_BATCH_STAGE_MARKS = ("correct_movies", "idealize_movies")


def _mark_project_stages_unfinished(project_path: Path) -> None:
    """Mark the project's correct and idealize stages as not covering every movie (#1243).

    An append leaves ``/settings/correction`` and ``/idealization`` in place, so their
    checkpoints still read as done although the new movie's molecules are in neither.
    The empty ``{stage}_movies`` marks in ``/settings/batch`` tell the next
    ``tether batch --into`` run to correct and idealize the project again, whichever
    route the movie came by. Written into the staged copy, so the marks publish with
    the movie or not at all.
    """
    import h5py  # noqa: PLC0415

    with h5py.File(project_path, "r+") as f:
        grp = f["settings"].require_group(_BATCH_SETTINGS)
        for name in _BATCH_STAGE_MARKS:
            grp.attrs[name] = ""


def _persist(
    project_path: Path,
    extracted: _Extracted,
    movie_path: Path,
    *,
    tmap: str | os.PathLike[str] | None,
    tdat: str | os.PathLike[str] | None,
    map_path: str | os.PathLike[str] | None = None,
) -> tuple[str, str, list[str]]:
    """Write one movie's calibration + extraction into an existing project.

    Returns ``(movie_id, calibration_id, molecule_ids)``. Both writers are
    append-capable and write-once per id, and ``write_extraction`` keeps its own
    write-once-per-``movie_id`` and same-aperture checks, so the append path goes
    through exactly the rules a new project does (#347).
    """
    movie_id = f"mov-{uuid4()}"
    calibration_id = f"cal-{uuid4()}"
    movie, settings = _movie_and_settings(
        project_path,
        extracted,
        movie_path,
        movie_id,
        calibration_id,
        tmap=tmap,
        tdat=tdat,
        map_path=map_path,
    )
    write_calibration(project_path, extracted.reg_map, calibration_id=calibration_id)
    molecule_ids = write_extraction(
        project_path,
        movie=movie,
        molecules=extracted.molecules,
        traces=extracted.traces,
        parsed=parse_filename(movie_path.name),
        registration_map=extracted.reg_map,
        settings=settings,
    )
    _record_generation_inputs(
        project_path,
        active_generation(project_path, movie_id).generation_id,
        movie_id,
        sha256=extracted.sha256,
        calibration_id=calibration_id,
    )
    _write_metadata_origins(
        project_path,
        movie_id,
        extracted.frame_time,
        extracted.pixel_size_um,
        roles_from_file=_roles_from_file(tmap, map_path),
    )
    if extracted.is_pair:
        # Both files of the acquisition, with their roles and checksums (#909, #912).
        from tether.project.sources import record_movie_pair  # noqa: PLC0415

        record_movie_pair(
            project_path,
            movie_id,
            {
                role: (read.path, read.size, read.sha256)
                for role, read in zip(PAIR_ROLES, extracted.files, strict=True)
            },
        )
    return movie_id, calibration_id, molecule_ids


def _record_generation_inputs(
    project_path: Path,
    generation_id: str,
    movie_id: str,
    *,
    sha256: str,
    calibration_id: str,
) -> None:
    """Record the inputs of a new trace generation, once (ADR-0103, #1503).

    The generation is the ``trace_generation`` instance ``generation_id``. Its direct
    upstream instances are the movie's source (``source_movie``, instance ``movie_id``,
    identity the registered ``/movies.sha256``) and the movie's map (``map``, instance
    ``movie_id``, identity the ``calibration_id`` its ``/movies`` row names): the existing
    stamps of ADR-0103, decision 4. [`tether.project.readiness`][] compares them with the
    store as it is now, so a changed checksum or map shows the generation as stale.
    """
    record_inputs_in(
        project_path,
        _GENERATION_NODE,
        generation_id,
        {("source_movie", movie_id): sha256, ("map", movie_id): calibration_id},
    )


def _movie_and_settings(
    project_path: Path,
    extracted: _Extracted,
    movie_path: Path,
    movie_id: str,
    calibration_id: str,
    *,
    tmap: str | os.PathLike[str] | None,
    tdat: str | os.PathLike[str] | None,
    map_path: str | os.PathLike[str] | None,
) -> tuple[MovieMetadata, dict]:
    """Build the ``/movies`` row provenance and the settings profile of one extracted movie.

    ``project_path`` is the project the row goes into, or a staged copy of it beside it in
    the same folder: the ``uri`` is relative to that folder
    ([`tether.project.sources.project_movie_uri`][]).
    """
    reg_map = extracted.reg_map
    movie = MovieMetadata(
        movie_id=movie_id,
        sha256=extracted.sha256,
        n_frames=extracted.n_frames,
        height=extracted.height,
        width=extracted.width,
        uri=project_movie_uri(project_path, movie_path),
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
        source=_registration_source(reg_map, map_path),
        tmap_source=Path(tmap).name if tmap is not None else None,
        map_source=Path(map_path).name if map_path is not None else None,
        tdat_source=Path(tdat).name if tdat is not None else None,
        donor_side_check=extracted.side_check,
        frame_steps=extracted.frame_steps,
        frame_steps_source=extracted.frame_steps_source,
    )
    if extracted.is_pair:
        settings["movie_pair"] = {
            role: read.path.name for role, read in zip(PAIR_ROLES, extracted.files, strict=True)
        }
    return movie, settings


def _summary(
    output_path: Path,
    extracted: _Extracted,
    movie_id: str,
    calibration_id: str,
    molecule_ids: list[str],
    *,
    map_path: str | os.PathLike[str] | None = None,
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
        registration_source=_registration_source(reg_map, map_path),
        detection_mode=extracted.options.detection_mode,
        donor_side_check=extracted.side_check,
        colocalization=extracted.molecules.policy,
        rule_counts=dict(extracted.molecules.rule_counts),
        rms_gate=float(reg_map.gate_px),
        movie_sha256=extracted.sha256,
        channel_layout=extracted.options.channel_layout,
        frame_steps=extracted.frame_steps,
        frame_steps_source=extracted.frame_steps_source,
    )


def extract_movie(
    movie_path: str | os.PathLike[str],
    output_path: str | os.PathLike[str],
    *,
    options: ExtractOptions | None = None,
    tmap: str | os.PathLike[str] | None = None,
    tdat: str | os.PathLike[str] | None = None,
    map_path: str | os.PathLike[str] | None = None,
    overwrite: bool = False,
    publish_guard: Callable[[], None] | None = None,
    refuse_over_gate: bool = False,
    acceptor_movie: str | os.PathLike[str] | None = None,
    map_optics: str | None = None,
    confirm_optics: bool = False,
    frame_steps: ChannelRecipe | None = None,
    map_frame_steps: ChannelRecipe | None = None,
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
        own detections. Cannot be combined with ``map_path``.
    tdat:
        Optional path to a Deep-LASI ``.tdat``. When given, the particle-detection
        mode (and, once the MCOS ``Channel`` decoder lands, the per-channel
        threshold) is read from it and **overrides** ``options.detection_mode`` /
        ``options.detection_threshold``, and the source filename is recorded as
        ``tdat_source`` in ``/settings/extraction`` -- so a re-extraction matches the
        method the movie was actually detected with. Independent of ``tmap`` (the two
        compose: import both registration and detection settings from Deep-LASI).
    map_path:
        Optional path to a Tether ``.npz`` map file
        ([`save_map`][tether.imaging.calibrate.save_map]; #353). When given, the
        saved map is applied as it is (the native fit is skipped) and the two
        channels are split at the map's own stored geometry, so
        ``options.donor_side`` is ignored and an ``options.channel_layout`` is
        refused; the map keeps the residual and the
        over-gate verdict it was saved with. The file name is recorded as
        ``map_source`` in ``/settings/extraction`` and the ``/calibration``
        provenance gains ``map_file`` (name, sha256, the map id and the map's record,
        and the result of the reuse check; #916). The reuse check refuses a map whose
        pixel size differs from the movie's
        ([`tether.imaging.map_asset.check_map_reuse`][]); it never reads the map's date.

        **Precedence:** one registration source per run. The native fit runs when
        neither ``tmap`` nor ``map_path`` is given; either file replaces it; passing
        both is refused. ``tdat`` is independent and composes with any of the three.
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
    acceptor_movie:
        The acceptor movie of a **movie pair** (#909): ``movie_path`` is then the donor
        movie, and the two files are one acquisition with each file one whole channel
        ([`tether.io.pair`][]). The pair is refused when the frame count, frame time,
        pixel type or frame size differ. A pair takes no ``.tmap``, no
        ``options.channel_layout`` and no ``options.donor_side``; a map file must be one
        made from a bead file pair
        ([`bead_map_pair_from_files`][tether.imaging.beadmap.bead_map_pair_from_files]).
        The ``/movies`` row names the pair ([`tether.io.pair.pair_sha256`][]), and the
        source registry stores both files with their roles. ``None`` (default) reads one
        movie with both channels in each frame.
    map_optics:
        With ``map_path``: the optics text of this movie (objective, filters, camera),
        compared with the map's own. ``None`` compares no optics.
    confirm_optics:
        With ``map_path``: use the map although its optics text differs from
        ``map_optics``. The confirmation is recorded with the map.
    frame_steps:
        The movie's own whole-frame steps
        ([`ChannelRecipe`][tether.imaging.recipe.ChannelRecipe]; #1435), run on every
        frame before the two channels are split. When given, they replace the map's
        default. ``None`` (default) uses the map's default (``map_frame_steps`` for a
        ``.tmap``, or the default a map file was saved with); an empty
        ``ChannelRecipe()`` (``"none"``) applies no steps even when the map has a
        default. When the steps have a source (the movie's own, ``"none"`` included, or
        the map's default), the steps and that source are recorded under
        ``frame_steps`` in the movie's settings profile; with neither, no steps run and
        nothing is recorded there.
    map_frame_steps:
        The default frame steps of the ``.tmap`` given as ``tmap``, which holds none of
        its own (#1435); ``frame_steps`` replaces it. It is refused without ``tmap``: a
        map file stores its own default, and a movie's own steps are ``frame_steps``.

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
        unreadable/compressed movie, a missing or undecodable ``.tmap``, map file or
        ``.tdat`` (or a ``.tdat`` with an unsupported detection mode), both a
        ``.tmap`` and a map file, a
        pre-existing output without ``overwrite`` or without a ``publish_guard``,
        an un-splittable frame (a channel layout whose rectangles are outside the
        frame, or a frame too small to cut in two), a channel layout given together
        with a ``.tmap``, too few
        matched control points to register (native path), a registration over the
        gate under ``refuse_over_gate``, a map file the reuse check refuses (a
        different pixel size, or a different optics text without ``confirm_optics``),
        or a write failure. Never a raw primitive traceback.
    """
    options = options or ExtractOptions()
    movie_path = Path(movie_path)
    output_path = Path(output_path)
    acceptor_path = Path(acceptor_movie) if acceptor_movie is not None else None

    _check_inputs(movie_path, tmap, tdat, map_path, acceptor_path)
    if output_path.exists() and not overwrite:
        raise ExtractionError(f"output exists: {output_path} (use overwrite=True / --overwrite)")
    try:
        require_publish_guard(output_path, publish_guard)
    except PublishGuardRequiredError as exc:
        raise ExtractionError(str(exc)) from exc

    options, imported_map = _resolve_sources(
        options,
        tmap,
        tdat,
        map_path,
        pair=acceptor_path is not None,
        map_frame_steps=map_frame_steps,
    )
    extracted = _run_pipeline(
        movie_path,
        options,
        imported_map,
        acceptor_path=acceptor_path,
        map_optics=map_optics,
        confirm_optics=confirm_optics,
        frame_steps=frame_steps,
    )
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
            tmp_path, extracted, movie_path, tmap=tmap, tdat=tdat, map_path=map_path
        )
        publish_staged_project(tmp_path, output_path, publish_guard=publish_guard)
    except ExtractionError:
        tmp_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        raise ExtractionError(f"failed to write {output_path.name}: {exc}") from exc

    return _summary(
        output_path, extracted, movie_id, calibration_id, molecule_ids, map_path=map_path
    )


def count_molecules_by_rule(
    movie_path: str | os.PathLike[str],
    *,
    options: ExtractOptions | None = None,
    tmap: str | os.PathLike[str] | None = None,
    tdat: str | os.PathLike[str] | None = None,
    map_path: str | os.PathLike[str] | None = None,
    map_optics: str | None = None,
    confirm_optics: bool = False,
    frame_steps: ChannelRecipe | None = None,
    map_frame_steps: ChannelRecipe | None = None,
) -> dict[str, int]:
    """Count the molecules each colocalization rule keeps, before any extraction (#920).

    Runs the stages of [`extract_movie`][] up to colocalization -- through the stage
    preview ([`tether.project.preview.iter_extraction_stages`][]), whose molecules are
    those of a real run -- and stops there: no trace is integrated and nothing is
    written. ``options``, ``tmap``, ``tdat``, ``map_path``, ``map_optics``,
    ``confirm_optics``, ``frame_steps`` and ``map_frame_steps`` mean what they mean for
    [`extract_movie`][], so a count with a map file uses that map (#353) and runs the
    same reuse check first (#916, #1394), and a count runs on the frames as the frame
    steps leave them (#1435).

    Parameters
    ----------
    frame_steps:
        The movie's own whole-frame steps, run before the channel split; they replace
        the map's default. ``None`` uses the map's default, and an empty
        ``ChannelRecipe()`` (``"none"``) applies no steps even when the map has one. As
        for [`extract_movie`][].
    map_frame_steps:
        The default frame steps of a ``.tmap``, which holds none of its own; refused
        without ``tmap``. As for [`extract_movie`][].

    Returns
    -------
    dict[str, int]
        ``{rule: n_molecules}`` for both rules of
        [`COLOCALIZATION_POLICIES`][tether.extract_choices.COLOCALIZATION_POLICIES].

    Raises
    ------
    ExtractionError
        A missing or unreadable input, a map file the reuse check refuses (as
        [`extract_movie`][] would), or a stage before colocalization that fails (for
        example too few control points to register).
    """
    from tether.project.preview import OUTCOME_FAILED, iter_extraction_stages  # noqa: PLC0415

    movie_path = Path(movie_path)
    _check_inputs(movie_path, tmap, tdat, map_path)
    options, imported_map = _resolve_sources(
        options or ExtractOptions(), tmap, tdat, map_path, map_frame_steps=map_frame_steps
    )
    steps, _ = _resolve_frame_steps(frame_steps, imported_map)
    try:
        with open_extraction_movie(movie_path) as reader:
            if imported_map is not None:  # the check extraction would run (#1394)
                imported_map = _check_map_reuse(
                    imported_map,
                    movie_path,
                    reader.pixel_size_um,
                    map_optics=map_optics,
                    confirm_optics=confirm_optics,
                )
            frames = reader.data
            if steps.steps:  # the same frames extraction splits (#1435)
                _, height, width = reader.shape
                try:
                    frame_view_shape(steps, height, width)
                except ChannelRecipeError as exc:
                    raise ExtractionError(f"{movie_path.name}: {exc}") from exc
                frames = steps.apply(frames)
            stages = iter_extraction_stages(frames, options=options, registration=imported_map)
            for stage in stages:
                if stage.outcome == OUTCOME_FAILED:
                    raise ExtractionError(
                        f"could not count the molecules of {movie_path.name}: the "
                        f"{stage.name} stage failed: {stage.message}"
                    )
                if stage.name == "colocalize":
                    return dict(stage.outputs["rule_counts"])
    except ExtractionError:
        raise
    except Exception as exc:  # an unreadable movie -> a clean operator message
        raise ExtractionError(f"could not count the molecules of {movie_path.name}: {exc}") from exc
    raise ExtractionError(f"could not count the molecules of {movie_path.name}")  # pragma: no cover


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
    map_path: str | os.PathLike[str] | None = None,
    publish_guard: Callable[[], None] | None,
    refuse_over_gate: bool = False,
    acceptor_movie: str | os.PathLike[str] | None = None,
    map_optics: str | None = None,
    confirm_optics: bool = False,
    frame_steps: ChannelRecipe | None = None,
    map_frame_steps: ChannelRecipe | None = None,
) -> ExtractionSummary:
    """Extract ``movie_path`` and append it to the existing project ``project_path``.

    Runs the same stages 1-15 as [`extract_movie`][] and writes through the same
    ``write_calibration`` + ``write_extraction`` calls, so the movie gains its own
    ``/movies`` row and calibration, its molecules are appended to ``/molecules``,
    and every ``/traces`` and ``/patches`` array grows in the same row order (the
    positional trace↔molecule join). A movie longer than every stored one widens
    each ``/traces`` array, and the existing rows gain zero-padded columns. The
    movie's settings profile is recorded under ``/settings/extraction/movies``; the
    first extraction's stamp is not changed (#347, ADR-0117). The project's correction
    and idealization no longer cover every movie, so the same publish marks both
    unfinished for ``tether batch --into`` (#1243); the stored results are kept.

    The update is written to a sibling copy of the project and published by one
    atomic replace, under the project's writer lock: ``publish_guard`` is called
    before the copy, after the write and immediately before the replace. Any failure
    — the guard included — removes the copy, so the project is left byte-identical.

    Parameters
    ----------
    project_path:
        An existing, compatible ``.tether`` project
        ([`create_project`][tether.io.schema.create_project] or [`extract_movie`][]).
    movie_path:
        The movie to append; see [`extract_movie`][].
    acceptor_movie:
        The acceptor movie when ``movie_path`` is the donor of a movie pair (#909); see
        [`extract_movie`][]. A pair already in the project (the same two files in the
        same roles) is refused as a duplicate.
    options, tmap, tdat, map_path, map_optics, confirm_optics:
        As for [`extract_movie`][], with the same precedence. The aperture parameters
        (``window``, the radii and ``bg_window``) must equal the ones the project's first
        extraction recorded.
    publish_guard:
        **Required.** The check yielded by
        [`publish_lock`][tether.project.lock.publish_lock] held on ``project_path``
        (#346, ADR-0112).
    refuse_over_gate:
        As for [`extract_movie`][]: a movie whose registration is over the gate is
        not appended, and the project is left as it was (#1151).
    frame_steps:
        The movie's own whole-frame steps, run before the channel split; they replace
        the map's default. ``None`` uses the map's default, and an empty
        ``ChannelRecipe()`` (``"none"``) applies no steps even when the map has one. As
        for [`extract_movie`][] (#1435).
    map_frame_steps:
        The default frame steps of a ``.tmap``, which holds none of its own; refused
        without ``tmap``. As for [`extract_movie`][].

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
    acceptor_path = Path(acceptor_movie) if acceptor_movie is not None else None

    _check_inputs(movie_path, tmap, tdat, map_path, acceptor_path)
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

    options, imported_map = _resolve_sources(
        options,
        tmap,
        tdat,
        map_path,
        pair=acceptor_path is not None,
        map_frame_steps=map_frame_steps,
    )
    try:
        identity = _hash_inputs(movie_path, acceptor_path)
    except Exception as exc:
        raise ExtractionError(f"could not extract from {movie_path.name}: {exc}") from exc
    _refuse_duplicate_movie(project_path, _row_sha256(identity), movie_path)

    extracted = _run_pipeline(
        movie_path,
        options,
        imported_map,
        identity=identity,
        acceptor_path=acceptor_path,
        map_optics=map_optics,
        confirm_optics=confirm_optics,
        frame_steps=frame_steps,
    )
    _check_gate(extracted, refuse_over_gate=refuse_over_gate)

    try:
        with guarded_project_write(
            project_path, write_guard=publish_guard, label="append"
        ) as stage:
            # The copy is the state that will be published: check it, not the name.
            _refuse_duplicate_movie(stage, extracted.sha256, movie_path)
            movie_id, calibration_id, molecule_ids = _persist(
                stage, extracted, movie_path, tmap=tmap, tdat=tdat, map_path=map_path
            )
            _mark_project_stages_unfinished(stage)
    except ExtractionError:
        raise
    except Exception as exc:  # aperture mismatch, lost lock, IO failure
        raise ExtractionError(f"failed to append to {project_path.name}: {exc}") from exc

    return _summary(
        project_path, extracted, movie_id, calibration_id, molecule_ids, map_path=map_path
    )


@dataclass(frozen=True)
class GenerationSummary:
    """The outcome of one [`reextract_movie`][] run.

    ``extraction`` is the run's [`ExtractionSummary`][], whose ``movie_id`` is the movie
    that gained the generation; ``generation`` is its new record
    ([`tether.io.generations.Generation`][]), active, with the carried keys.
    """

    extraction: ExtractionSummary
    generation: Generation

    @property
    def n_carried(self) -> int:
        """How many molecules kept their key, labels, category and window."""
        return len(self.generation.carried_keys)


#: The node of ADR-0103 a new generation edits, and the guarded edge into the factor
#: version that it always crosses: every trace of the movie is integrated again, so the
#: intensities of any alpha or gamma contributor in it change (edge 7's guard).
_GENERATION_NODE = "trace_generation"
_CONTRIBUTOR_INTENSITIES_CHANGE = 7


def reextract_movie(
    project_path: str | os.PathLike[str],
    movie_path: str | os.PathLike[str],
    *,
    options: ExtractOptions | None = None,
    tmap: str | os.PathLike[str] | None = None,
    tdat: str | os.PathLike[str] | None = None,
    map_path: str | os.PathLike[str] | None = None,
    publish_guard: Callable[[], None] | None,
    refuse_over_gate: bool = False,
    match_px: float | None = None,
    acceptor_movie: str | os.PathLike[str] | None = None,
    map_optics: str | None = None,
    confirm_optics: bool = False,
    frame_steps: ChannelRecipe | None = None,
    map_frame_steps: ChannelRecipe | None = None,
) -> GenerationSummary:
    """Extract a movie the project already holds again, as a new trace generation.

    The movie is found by its content hash. Stages 1-15 run as for [`extract_movie`][]
    (a new map, geometry, layout or options may be given), and the result is written by
    [`write_generation`][tether.imaging.extract.write_generation]: the generation that
    was active is copied, unchanged, to the project's generation archive and stays
    readable ([`tether.io.generations.read_generation`][]), and the new one becomes
    active. Molecules within ``match_px`` of a previous molecule
    ([`GENERATION_MATCH_PX`][tether.io.generations.GENERATION_MATCH_PX] by default) keep
    its ``molecule_key``, curation label, category and analysis window, marked carried;
    fits and corrections of the movie are marked stale through the ``trace_generation``
    node of ADR-0103 ([`tether.project.jobs.mark_stale_in`][]).
    [`mark_traces_replaced_in`][tether.project.correction_states.mark_traces_replaced_in]
    then moves an ANALYSIS_READY or CORRECTED_REVIEW correction state to
    CALIBRATION_STALE, and in CALIBRATION_APPROVED, where the state does not change, marks
    the approved factor version stale; either way corrected analysis waits for a new
    approval. The movie's frame time, its verification and its pixel size are kept; the
    channel roles' origin becomes the one this run's sources give, as on a fresh
    extraction ([`tether.project.movie_metadata`][]). No generation is overwritten or
    deleted (ADR-0136).

    The update is written to a sibling copy of the project and published by one atomic
    replace under the project's writer lock, exactly as [`add_movie_to_project`][] is.

    Parameters
    ----------
    project_path:
        An existing, compatible ``.tether`` project that holds the movie.
    movie_path:
        The movie file; its bytes must be the ones the project recorded.
    options, tmap, tdat, map_path, map_optics, confirm_optics, refuse_over_gate:
        As for [`add_movie_to_project`][]; the aperture parameters must equal the
        project's.
    publish_guard:
        **Required.** The check yielded by
        [`publish_lock`][tether.project.lock.publish_lock] held on ``project_path``.
    match_px:
        The "did not move" distance in pixels; ``None`` is ``GENERATION_MATCH_PX``.
    acceptor_movie:
        The acceptor movie when the project holds ``movie_path`` as the donor of a movie
        pair (#909); the pair is found by its checksum, as one movie is.
    frame_steps:
        The movie's own whole-frame steps, run before the channel split, as for
        [`extract_movie`][] (#1435); given steps replace both the map's default and the
        steps stored for the movie, and an empty ``ChannelRecipe()`` (``"none"``) applies
        none, and that ``"none"`` is recorded, so a later re-extraction reuses it. With
        ``None`` (default), the steps the movie was last extracted with are used again:
        a movie's own steps always, and a map's default only when the map used now has
        no default of its own (otherwise that map's default applies). A movie last
        extracted with no steps from any source has no ``frame_steps`` record, so there
        is nothing to reuse: the map used now gives its default if it has one, and
        otherwise no steps run.
    map_frame_steps:
        The default frame steps of a ``.tmap``, which holds none of its own; refused
        without ``tmap``. As for [`extract_movie`][].

    Returns
    -------
    GenerationSummary
        The extraction outcome and the new generation's record.

    Raises
    ------
    ExtractionError
        Every failure [`add_movie_to_project`][] reports, and a movie whose content is
        not in the project (append it instead).
    """
    options = options or ExtractOptions()
    project_path = Path(project_path)
    movie_path = Path(movie_path)
    acceptor_path = Path(acceptor_movie) if acceptor_movie is not None else None

    _check_inputs(movie_path, tmap, tdat, map_path, acceptor_path)
    if not project_path.is_file():
        raise ExtractionError(f"project not found: {project_path}")
    try:
        assert_appendable(project_path)
    except Exception as exc:  # not HDF5, not a .tether, a newer schema, misaligned layers
        raise ExtractionError(f"cannot extract into {project_path.name}: {exc}") from exc
    if publish_guard is None:
        raise ExtractionError(
            f"a new generation in {project_path.name} needs its writer lock "
            "(hold tether.project.lock.publish_lock and pass its check as publish_guard)"
        )

    options, imported_map = _resolve_sources(
        options,
        tmap,
        tdat,
        map_path,
        pair=acceptor_path is not None,
        map_frame_steps=map_frame_steps,
    )
    try:
        identity = _hash_inputs(movie_path, acceptor_path)
    except Exception as exc:
        raise ExtractionError(f"could not extract from {movie_path.name}: {exc}") from exc
    movie_id = _held_movie_id(project_path, _row_sha256(identity), movie_path)
    frame_steps, steps_source = _reextract_frame_steps(
        project_path, movie_id, frame_steps, imported_map
    )

    extracted = _run_pipeline(
        movie_path,
        options,
        imported_map,
        identity=identity,
        acceptor_path=acceptor_path,
        map_optics=map_optics,
        confirm_optics=confirm_optics,
        frame_steps=frame_steps,
        frame_steps_source=steps_source,
    )
    _check_gate(extracted, refuse_over_gate=refuse_over_gate)

    calibration_id = f"cal-{uuid4()}"
    try:
        with guarded_project_write(
            project_path, write_guard=publish_guard, label="generation"
        ) as stage:
            # The copy is the state that will be published: find the movie in it.
            if _held_movie_id(stage, extracted.sha256, movie_path) != movie_id:
                raise ExtractionError(f"{movie_path.name} changed identity in {project_path.name}")
            movie, settings = _movie_and_settings(
                project_path,
                extracted,
                movie_path,
                movie_id,
                calibration_id,
                tmap=tmap,
                tdat=tdat,
                map_path=map_path,
            )
            write_calibration(stage, extracted.reg_map, calibration_id=calibration_id)
            record, molecule_ids = write_generation(
                stage,
                movie=movie,
                molecules=extracted.molecules,
                traces=extracted.traces,
                parsed=parse_filename(movie_path.name),
                registration_map=extracted.reg_map,
                settings=settings,
                match_px=match_px,
            )
            _record_generation_inputs(
                stage,
                record.generation_id,
                movie_id,
                sha256=extracted.sha256,
                calibration_id=calibration_id,
            )
            mark_stale_in(
                stage,
                _GENERATION_NODE,
                movie_id,
                record.generation_id,
                guards_hold=(_CONTRIBUTOR_INTENSITIES_CHANGE,),
            )
            # The corrections were read from the traces this generation replaces, so
            # corrected analysis waits for a new approval (#1284).
            mark_traces_replaced_in(stage, movie_id=movie_id, generation_id=record.generation_id)
            _write_roles_origin(stage, movie_id, roles_from_file=_roles_from_file(tmap, map_path))
    except ExtractionError:
        raise
    except Exception as exc:  # aperture mismatch, lost lock, IO failure
        raise ExtractionError(
            f"failed to write a new generation into {project_path.name}: {exc}"
        ) from exc

    summary = _summary(
        project_path, extracted, movie_id, calibration_id, molecule_ids, map_path=map_path
    )
    return GenerationSummary(extraction=summary, generation=record)


def _reextract_frame_steps(
    project_path: Path,
    movie_id: str,
    frame_steps: ChannelRecipe | None,
    reg_map: RegistrationMap | None,
) -> tuple[ChannelRecipe | None, str]:
    """Return the frame steps a re-extraction applies when it gives none of its own (#1435).

    Steps given now win. Otherwise the steps the movie was last extracted with are used
    again: a movie's own steps always, and a map's default unless the map used now has a
    default of its own. Returns ``(None, "movie")`` to let the map's default decide.
    """
    if frame_steps is not None:
        return frame_steps, "movie"
    from tether.imaging.extract import read_movie_settings  # noqa: PLC0415

    stored = read_movie_settings(project_path).get(movie_id, {}).get("frame_steps")
    if not isinstance(stored, dict) or stored.get("source") not in FRAME_STEPS_SOURCES:
        return None, "movie"
    if stored["source"] == "map" and _resolve_frame_steps(None, reg_map)[1] is not None:
        return None, "movie"
    try:
        return ChannelRecipe.parse(str(stored.get("steps"))), str(stored["source"])
    except ChannelRecipeError as exc:
        raise ExtractionError(
            f"the frame steps stored for movie {movie_id!r} do not read: {exc}"
        ) from exc


def _held_movie_id(project_path: Path, sha256: str, movie_path: Path) -> str:
    """Return the ``movie_id`` the project holds ``sha256`` under; refuse a movie it lacks."""
    existing = _movie_ids_with_sha256(project_path, sha256)
    if not existing:
        raise ExtractionError(
            f"{movie_path.name} is not in {project_path.name} (no movie with its sha256); "
            "append it with add_movie_to_project"
        )
    return existing[0]
