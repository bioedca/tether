# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The extraction preview and options, as the Prepare page of the application shows them (#350).

Like [`tether.services.movies`][], this reads and never writes, so it is not an action: it
has no request, no recipe step and no command-line subcommand. It is a set of plain
functions that the application's extraction page
([`tether.gui.extraction`][]) calls, so that the page reaches the extraction core through
one Qt-free place instead of reading the core itself (ADR-0100). Like a command, it imports
the core only inside the function that needs it. The extraction itself is the registered
``extract`` command ([`tether.services.extract`][]).

* [`FORM_FIELDS`][] is one row for each field of
  [`ExtractionFields`][tether.services.profile.ExtractionFields], and
  [`profile_from_texts`][] reads the rows into an
  [`ExtractionProfile`][tether.services.profile.ExtractionProfile]. A value is checked by
  [`ExtractOptions`][tether.project.extract.ExtractOptions], the path of the command line,
  and a refused value is a [`RequestError`][tether.services.base.RequestError].
* [`run_preview`][] runs the stage preview of #348
  ([`preview_extraction`][tether.project.preview.preview_extraction]) on one movie, with the
  registration a native fit, a map file or a ``.tmap`` gives, read as ``extract_movie``
  reads it. The movie is opened by the extraction reader
  ([`open_extraction_movie`][tether.io.frames.open_extraction_movie]), so the preview reads
  the frames the run reads, for every container in
  [`EXTRACTED_CONTAINERS`][tether.io.movie.EXTRACTED_CONTAINERS] (#1415); the open movie is
  a [`PreviewMovie`][]. [`stage_rows`][] gives every stage an outcome.
* [`spot_views`][] puts the detected spots and the control-point residuals in the raw
  camera frame, for the movie viewer, and [`residual_summary`][] says the residuals in one
  line.
* [`donor_side_note`][] is the sentence ``tether extract`` prints for the advisory
  donor-side check (#646, ADR-0070).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from os import PathLike
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tether.services.base import RequestError

if TYPE_CHECKING:
    import numpy as np

    from tether.imaging import ChannelGeometry
    from tether.io.adapters import MovieMetadata
    from tether.io.frames import ExtractionMovie
    from tether.project.extract import ExtractionSummary, ExtractOptions
    from tether.project.preview import ExtractionPreview
    from tether.services.profile import ExtractionProfile

__all__ = [
    "FORM_FIELDS",
    "NOT_RUN",
    "RESIDUALS",
    "SPOTS",
    "FormField",
    "PreviewMovie",
    "default_texts",
    "donor_side_note",
    "profile_from_texts",
    "residual_summary",
    "run_preview",
    "spot_views",
    "stage_rows",
]

#: The outcome of a stage the preview did not reach.
NOT_RUN = "not run"
#: The key of the detected-spots view of [`spot_views`][].
SPOTS = "spots"
#: The key of the control-point-residuals view of [`spot_views`][].
RESIDUALS = "residuals"


@dataclass(frozen=True)
class FormField:
    """One row of the options form: a field of the extraction profile and its input kind.

    ``kind`` is ``choice``, ``int``, ``float`` or ``rect`` (four whole numbers
    ``y1, x1, y2, x2``). With ``optional`` an empty input is ``None``.
    """

    name: str
    kind: str
    choices: tuple[str, ...] = ()
    optional: bool = False


#: Every field of [`ExtractionFields`][tether.services.profile.ExtractionFields], in its order.
#: The choices are written out so that importing this module loads no core module; a test
#: checks them, and the names, against the core, so a new extraction field cannot miss the form.
FORM_FIELDS: tuple[FormField, ...] = (
    FormField("donor_side", "choice", ("left", "right")),
    FormField("channel_layout", "choice", ("side-by-side", "top-bottom", "rectangles"), True),
    FormField("channel_layout_donor", "choice", ("left", "right", "top", "bottom"), True),
    FormField("channel_layout_donor_rect", "rect", optional=True),
    FormField("channel_layout_acceptor_rect", "rect", optional=True),
    FormField("detection_mode", "choice", ("wavelet", "intensity", "bandpass", "zmw"), True),
    FormField("detection_threshold", "float", optional=True),
    FormField("detection_block", "int"),
    FormField("min_separation", "float", optional=True),
    FormField("prealign", "choice", ("translation", "similarity")),
    FormField("prealign_upsample", "int"),
    FormField("prealign_low_sigma", "float"),
    FormField("prealign_high_sigma", "float"),
    FormField("pair_tol", "float"),
    FormField("rms_gate", "float"),
    FormField("window", "int"),
    FormField("coloc_distance", "float"),
    FormField("colocalization", "choice", ("partner-in-both", "donor-anchored")),
    FormField("disk_radius", "float"),
    FormField("ring_inner", "float"),
    FormField("ring_outer", "float"),
    FormField("bg_window", "int"),
)


def default_texts() -> dict[str, str]:
    """Return the text of each form row for the default profile."""
    from tether.services.profile import ExtractionProfile

    values = ExtractionProfile().extraction_values()
    return {name: _to_text(value) for name, value in values.items()}


def _to_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, tuple):
        return ", ".join(str(v) for v in value)
    return str(value)


def _parse(spec: FormField, text: str) -> object:
    """Read one form row; raise ``ValueError`` with a plain message."""
    text = text.strip()
    if not text:
        if spec.optional:
            return None
        raise ValueError("a value is required")
    if spec.kind == "choice":
        return text  # ExtractOptions checks the value against its choices
    if spec.kind == "int":
        try:
            return int(text)
        except ValueError:
            raise ValueError(f"{text!r} is not a whole number") from None
    if spec.kind == "float":
        try:
            number = float(text)
        except ValueError:
            raise ValueError(f"{text!r} is not a number") from None
        if not math.isfinite(number):
            raise ValueError(f"{text!r} is not a finite number")
        return number
    parts = text.replace(",", " ").split()
    try:
        rect = tuple(int(p) for p in parts)
    except ValueError:
        rect = ()
    if len(rect) != 4:
        raise ValueError(f"{text!r} is not four whole numbers y1, x1, y2, x2")
    return rect


def profile_from_texts(texts: Mapping[str, str]) -> ExtractionProfile:
    """Return the profile that the form texts name, checked as the command line checks it.

    A row missing from ``texts`` takes its default. Raises
    [`RequestError`][tether.services.base.RequestError] for text that is not a value of its
    row, and with the message of the core's
    [`ExtractionError`][tether.project.extract.ExtractionError] for values
    [`ExtractOptions`][tether.project.extract.ExtractOptions] refuses.
    """
    from tether.project.extract import ExtractionError
    from tether.services.profile import ExtractionProfile

    defaults = default_texts()
    values: dict[str, object] = {}
    for spec in FORM_FIELDS:
        try:
            values[spec.name] = _parse(spec, texts.get(spec.name, defaults[spec.name]))
        except ValueError as exc:
            raise RequestError(f"{spec.name}: {exc}") from None
    profile = ExtractionProfile(**values)  # type: ignore[arg-type]
    try:
        profile.extract_options()
    except ExtractionError as exc:
        raise RequestError(str(exc)) from None
    return profile


class PreviewMovie:
    """A movie the preview opened, as the extraction reader opened it (#1415).

    It is a [`MovieSource`][tether.io.adapters.MovieSource] over
    [`ExtractionMovie.data`][tether.io.frames.ExtractionMovie], the frames extraction reads:
    a TIFF's own memory map, or the temporary copy of a movie of another container. The
    movie viewer shows it while the page does, and the page closes it when it clears the
    preview, previews another movie or closes. [`close`][] closes the TIFF reader, or lets
    the operating system delete the temporary copy once no view of it is left; it is
    idempotent, and a read after it raises `ValueError`.
    """

    def __init__(self, opened: Any, movie: ExtractionMovie) -> None:
        # What the movie says about itself is copied here, so a closed handle that a caller
        # keeps holds no reference to the frames: only ``_data`` does, and close drops it.
        self._opened: Any = opened
        self._path = movie.path
        self._container = movie.container
        self._shape = movie.shape
        self._dtype = movie.dtype
        self._frame_time = movie.frame_time
        self._pixel_size_um = movie.pixel_size_um
        self._data: np.ndarray | None = movie.data

    @property
    def path(self) -> Path:
        """The movie's filesystem path."""
        return self._path

    @property
    def container(self) -> str:
        """The name of the adapter that read the movie."""
        return self._container

    @property
    def shape(self) -> tuple[int, int, int]:
        """``(n_frames, height, width)``."""
        return self._shape

    @property
    def n_frames(self) -> int:
        """The number of frames."""
        return self._shape[0]

    @property
    def pixel_type(self) -> str:
        """The on-disk pixel type as a NumPy dtype string."""
        return self._dtype.str

    @property
    def metadata(self) -> MovieMetadata:
        """The frame time and pixel size extraction reads, each from the file or unknown."""
        from tether.io.adapters import MetadataValue, MovieMetadata

        def value(v: float | None) -> MetadataValue[float]:
            return MetadataValue.unknown() if v is None else MetadataValue.from_file(v)

        return MovieMetadata(
            frame_time=value(self._frame_time),
            pixel_size=value(self._pixel_size_um),
        )

    @property
    def data(self) -> np.ndarray:
        """The ``(n_frames, height, width)`` frames that extraction reads."""
        if self._data is None:
            raise ValueError(f"{self.path}: the preview movie is closed")
        return self._data

    @property
    def closed(self) -> bool:
        """Whether [`close`][] has run."""
        return self._data is None

    def read_frames(self, start: int, stop: int) -> np.ndarray:
        """Return frames ``start`` to ``stop - 1``; ``0 <= start <= stop <= n_frames``."""
        data = self.data
        if not 0 <= start <= stop <= data.shape[0]:
            raise IndexError(f"frames [{start}, {stop}) out of range for {data.shape[0]} frames")
        return data[start:stop]

    def close(self) -> None:
        """Close the movie's file or drop the temporary copy (idempotent)."""
        if self._data is None:
            return
        self._data = None
        opened, self._opened = self._opened, None
        opened.__exit__(None, None, None)

    def __enter__(self) -> PreviewMovie:
        """Enter the context; return the movie."""
        return self

    def __exit__(self, *exc: object) -> None:
        """Close the movie."""
        self.close()


def run_preview(
    movie: str | PathLike[str],
    options: ExtractOptions,
    *,
    tmap: str | PathLike[str] | None = None,
    map_path: str | PathLike[str] | None = None,
    map_optics: str | None = None,
    confirm_optics: bool = False,
) -> tuple[PreviewMovie, ExtractionPreview]:
    """Open ``movie`` and run the stage preview on it, with the registration source given.

    The movie is opened as extraction opens it,
    [`open_extraction_movie`][tether.io.frames.open_extraction_movie], so the preview reads
    the same frames as the run, for every container extraction reads; a movie the reader
    refuses raises [`ExtractionError`][tether.project.extract.ExtractionError] with the
    message ``extract_movie`` gives it. The ``.tmap`` or map file is read as
    ``extract_movie`` reads it, so the preview and the run use the same registration.
    A map file is first put through the reuse check extraction runs
    (``_check_map_reuse``, #916, #1434) with the movie's pixel size, ``map_optics`` and
    ``confirm_optics``, which mean what they mean for ``extract_movie``: a map the check
    refuses raises ``ExtractionError`` with the message extraction gives, before any
    stage runs.
    Returns the open [`PreviewMovie`][] (the caller closes it) and the preview. Raises
    ``ExtractionError`` or an ``OSError`` when the inputs cannot be read; a stage that
    fails is a result, not an error.
    """
    from tether.io.frames import open_extraction_movie
    from tether.project.extract import (
        ExtractionError,
        _check_inputs,
        _check_map_reuse,
        _resolve_sources,
    )
    from tether.project.preview import preview_extraction

    movie_path = Path(movie)
    _check_inputs(movie_path, tmap, None, map_path)
    options, registration = _resolve_sources(options, tmap, None, map_path)
    opened = open_extraction_movie(movie_path)
    try:
        extraction_movie = opened.__enter__()
    except Exception as exc:
        # The words of extract_movie for a movie its reader refuses, so Preview and
        # Extract say the same thing.
        raise ExtractionError(f"could not extract from {movie_path.name}: {exc}") from exc
    source = PreviewMovie(opened, extraction_movie)
    try:
        if registration is not None:  # the check extraction runs on a map file (#1434)
            registration = _check_map_reuse(
                registration,
                movie_path,
                extraction_movie.pixel_size_um,
                map_optics=map_optics,
                confirm_optics=confirm_optics,
            )
        preview = preview_extraction(source.data, options=options, registration=registration)
    except BaseException:
        source.close()
        raise
    return source, preview


def _stage_detail(name: str, outputs: Mapping[str, Any], parameters: Mapping[str, Any]) -> str:
    """Describe in a few words what a stage that ran produced."""
    if name == "split":
        donor, acceptor = outputs["donor_stack"].shape, outputs["acceptor_stack"].shape
        return f"donor {donor[-1]}×{donor[-2]} px, acceptor {acceptor[-1]}×{acceptor[-2]} px"
    if name == "detect":
        n_donor, n_acceptor = len(outputs["donor_spots"]), len(outputs["acceptor_spots"])
        return f"{n_donor} donor, {n_acceptor} acceptor spots"
    if name == "pair":
        return f"{outputs['n_pairs']} control-point pairs"
    if name == "register":
        reg = outputs["registration"]
        return f"RMS {reg.rms_residual:.3f} px, gate {reg.gate_px:g} px ({reg.source})"
    if name == "colocalize":
        counts = ", ".join(f"{rule} {n}" for rule, n in outputs["rule_counts"].items())
        return f"{outputs['n_molecules']} molecules ({counts})"
    if name == "extract":
        n, t = outputs["traces"].donor.intensity.shape
        return f"{n} traces of {t} frames"
    return ", ".join(f"{k} {v}" for k, v in parameters.items())


def stage_rows(preview: ExtractionPreview | None) -> list[tuple[str, str, str]]:
    """Return ``(stage, outcome, detail)`` for every stage of the preview, in run order.

    A stage the preview did not reach is [`NOT_RUN`][]. A failed or over-gate stage gives
    its message as the detail, so the table says why.
    """
    from tether.project.preview import OUTCOME_OK, STAGES

    rows = []
    for name in STAGES:
        result = None if preview is None else preview.stage(name)
        if result is None:
            rows.append((name, NOT_RUN, "an earlier stage did not finish"))
        elif result.outcome == OUTCOME_OK:
            detail = _stage_detail(name, result.outputs, result.parameters)
            rows.append((name, result.outcome, detail))
        else:
            rows.append((name, result.outcome, result.message))
    return rows


def _to_frame(
    xy: np.ndarray, geometry: ChannelGeometry, frame_shape: tuple[int, int]
) -> np.ndarray:
    from tether.imaging.split import channel_xy_to_frame

    return channel_xy_to_frame(
        xy,
        frame_shape=frame_shape,
        crop=geometry.crop,
        rotation_deg=geometry.rotation_deg,
        flip=geometry.flip,
    )


def _residual_pairs(preview: ExtractionPreview) -> tuple[np.ndarray, np.ndarray] | None:
    """Return the mapped donor and the paired acceptor control points of a native fit."""
    import numpy as np

    pair, registration = preview.stage("pair"), preview.registration
    if pair is None or "paired" not in pair.outputs or registration is None:
        return None
    paired = pair.outputs["paired"]
    mapped = registration.apply_reference_to_moving(np.asarray(paired.reference))
    return np.asarray(mapped, dtype=np.float64), np.asarray(paired.moving, dtype=np.float64)


def spot_views(
    preview: ExtractionPreview, frame_shape: tuple[int, int]
) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Return the spot views of a preview as ``(green_xy, red_xy)`` in the raw camera frame.

    [`SPOTS`][] needs the ``detect`` stage: the donor spots (green) and the acceptor spots
    (red). [`RESIDUALS`][] needs a native fit: each donor control point mapped into the
    acceptor half by the fitted map (green), beside the acceptor control point that was
    paired with it (red), so the distance between the two is the residual of that pair.
    Each array is ``(N, 2)`` ``[x, y]``.
    """
    from tether.project.preview import OUTCOME_FAILED

    split, detect = preview.stage("split"), preview.stage("detect")
    views: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    if detect is None or detect.outcome == OUTCOME_FAILED or split is None:
        return views
    donor_geometry = split.outputs["donor_geometry"]
    acceptor_geometry = split.outputs["acceptor_geometry"]
    views[SPOTS] = (
        _to_frame(detect.outputs["donor_spots"], donor_geometry, frame_shape),
        _to_frame(detect.outputs["acceptor_spots"], acceptor_geometry, frame_shape),
    )
    pairs = _residual_pairs(preview)
    if pairs is not None:
        mapped, found = pairs
        views[RESIDUALS] = (
            _to_frame(mapped, acceptor_geometry, frame_shape),
            _to_frame(found, acceptor_geometry, frame_shape),
        )
    return views


def residual_summary(preview: ExtractionPreview) -> str:
    """Return one line on the control-point residuals of a native fit, ``""`` without one."""
    import numpy as np

    pairs = _residual_pairs(preview)
    if pairs is None:  # a fit has at least two pairs, so a fit's pairs are never empty
        return ""
    mapped, found = pairs
    distances = np.hypot(*(mapped - found).T)
    registration = preview.registration
    assert registration is not None  # noqa: S101 - _residual_pairs checked it
    return (
        f"Control-point residuals: {distances.size} pairs, RMS "
        f"{registration.rms_residual:.3f} px, largest {float(distances.max()):.3f} px, "
        f"gate {registration.gate_px:g} px"
    )


def donor_side_note(
    summary: ExtractionSummary, *, donor_side: str, map_file: str | None = None
) -> tuple[str, str] | None:
    """Return ``(level, text)`` for the donor-side check of an extraction, or ``None``.

    ``None`` when the summary has no check. ``level`` is ``warning`` when the halves look
    swapped and ``note`` for every other verdict (``mixed``, ``insufficient``,
    ``consistent``, ``not-run``). The text is the one ``tether extract`` prints: the
    evidence and, for swapped halves, the remedy.
    """
    check = summary.donor_side_check
    if check is None:
        return None
    text = check.describe(
        donor_side=donor_side,
        imported_map=summary.registration_source != "native",
        map_file=map_file,
        channel_layout=summary.channel_layout,
    )
    return ("warning" if check.swapped else "note"), text
