# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The steps of the import page, as Qt-free functions (#913, FR-WORKFLOW, FR-EXTRACT).

The import page of the Prepare section adds movies, pairs two files as one acquisition,
sets the channel layout and the donor and acceptor, sets the frame steps, asks for a
missing frame time and links a moved source again. Each step goes through this module, so
the application reaches the core only through [`tether.services`][] (ADR-0100) and the
list of direct calls from the GUI into the core does not grow (#892).

The functions fall into two groups:

* **Before extraction**, on movie files: [`describe_movie`][] reads the reader, the
  shape, the pixel type and each metadata value with its origin; [`read_first_frame`][]
  reads the frame the page shows; [`check_movie_pair`][] refuses two files that cannot be
  one acquisition (#909); [`frame_view`][] applies the frame steps (#1435) and the channel
  layout (#908) to that frame, so the page shows each change before it is applied; and
  [`extract_request`][] and [`save_recipe`][] turn the applied choices into ``extract``
  requests and a recipe file (#899).
* **On the open project**: [`read_project_sources`][] lists the source registry (#912)
  with the sources whose file is missing, [`relink_source`][] links one again, and
  [`read_frame_times`][], [`give_frame_time`][] and [`confirm_frame_time`][] record a
  frame time the user gives, with origin ``user`` and not verified until a separate
  confirm (#911).

Like [`tether.services.movies`][], these are plain functions and not registered service
commands: none has a command-line subcommand. The command-line route of the same steps is
#914. The core is imported only when a function runs.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from tether.services.base import CommandFailed, RequestRefused
from tether.services.extract import ExtractRequest

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from os import PathLike

    import numpy as np

    from tether.project.core import Project
    from tether.project.jobs import CancelToken

__all__ = [
    "LAYOUT_KINDS",
    "ORIGIN_TEXT",
    "FrameTime",
    "FrameView",
    "LayoutChoice",
    "MetadataItem",
    "MovieDescription",
    "ProjectSource",
    "check_movie_pair",
    "confirm_frame_time",
    "describe_movie",
    "donor_positions",
    "extract_request",
    "frame_view",
    "give_frame_time",
    "read_first_frame",
    "read_frame_times",
    "read_project_sources",
    "relink_source",
    "save_recipe",
]

#: The channel layout kinds (#908), in the order the page offers them.
LAYOUT_KINDS: tuple[str, ...] = ("side-by-side", "top-bottom", "rectangles")

#: The donor positions of each half layout; the acceptor is the other one.
_POSITIONS: dict[str, tuple[str, str]] = {
    "side-by-side": ("left", "right"),
    "top-bottom": ("top", "bottom"),
}

#: The words the page shows for each metadata origin.
ORIGIN_TEXT: dict[str, str] = {
    "file": "from the file",
    "user": "from you",
    "unknown": "unknown",
}


def donor_positions(kind: str) -> tuple[str, ...]:
    """Return the donor positions a layout ``kind`` offers; ``()`` for ``rectangles``."""
    return _POSITIONS.get(kind, ())


# --- movies before extraction ------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class MetadataItem:
    """One metadata value of a movie: its name, its value as text and its origin.

    ``origin`` is ``file``, ``user`` or ``unknown``; ``value`` is ``""`` when unknown.
    """

    name: str
    value: str
    origin: str


@dataclasses.dataclass(frozen=True)
class MovieDescription:
    """What the import page shows of one movie file, read once when it is added.

    ``reader`` names the reader that opens the file and its file types, ``shape`` is
    ``(frames, height, width)``, ``pixel_type`` is the NumPy name of the pixels (such as
    ``uint16``), and ``frame_time`` is seconds per frame, or ``None`` when the file does not
    record it. ``metadata`` holds each acquisition value with its origin.
    """

    path: Path
    reader: str
    shape: tuple[int, int, int]
    pixel_type: str
    frame_time: float | None
    metadata: tuple[MetadataItem, ...]

    @property
    def name(self) -> str:
        """The file name of the movie."""
        return self.path.name

    @property
    def frame_shape(self) -> tuple[int, int]:
        """The ``(height, width)`` of one frame."""
        return self.shape[1], self.shape[2]


def _value_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, tuple):
        return ", ".join(str(v) for v in value)
    return f"{value:g}" if isinstance(value, float) else str(value)


def _pixel_name(pixel_type: object) -> str:
    """Return the NumPy name of a pixel type, such as ``uint16`` for ``'<u2'``."""
    import numpy as np  # noqa: PLC0415

    try:
        return np.dtype(pixel_type).name  # type: ignore[call-overload]
    except TypeError:
        return str(pixel_type)


def _reader_errors() -> tuple[type[BaseException], ...]:
    """Return the errors a reader raises for one file it cannot open or read.

    ``OSError`` is a file that cannot be read, and ``ValueError`` a reader that refuses the
    file's content. The Bio-Formats runner error is a runner that stopped, timed out or
    failed on one ``.vsi`` file. Each one refuses that file only, so a batch of movies goes
    on with the next file.
    """
    from tether.io.bioformats import BioformatsRunnerError  # noqa: PLC0415

    return (OSError, ValueError, BioformatsRunnerError)


def _open(path: Path) -> Any:
    from tether.io.adapters import (  # noqa: PLC0415
        InvalidPixelError,
        LossyCodecError,
        UnsupportedContainerError,
        open_movie_source,
        select_adapter,
    )

    try:
        return select_adapter(path), open_movie_source(path)
    except (UnsupportedContainerError, LossyCodecError, InvalidPixelError) as exc:
        raise RequestRefused(f"{path.name}: {exc}") from exc
    except _reader_errors() as exc:
        raise RequestRefused(f"{path.name}: {exc}") from exc


def describe_movie(path: str | PathLike[str]) -> MovieDescription:
    """Read the reader, the shape, the pixel type and the metadata of the movie at ``path``.

    The file is opened once and closed again; no pixel is read. A file that no reader
    opens raises [`RequestRefused`][tether.services.base.RequestRefused] with the reader's
    message.
    """
    path = Path(path)
    adapter, source = _open(path)
    with source:
        meta = source.metadata
        items = tuple(
            MetadataItem(label, _value_text(field.value), field.origin)
            for label, field in (
                ("Frame time (s)", meta.frame_time),
                ("Pixel size (µm)", meta.pixel_size),
                ("Channels", meta.channels),
            )
        )
        shape = tuple(int(v) for v in source.shape)
        return MovieDescription(
            path=path,
            reader=adapter.describe(),
            shape=(shape[0], shape[1], shape[2]),
            pixel_type=_pixel_name(source.pixel_type),
            frame_time=None if meta.frame_time.value is None else float(meta.frame_time.value),
            metadata=items,
        )


def read_first_frame(path: str | PathLike[str]) -> np.ndarray:
    """Return the first frame of the movie at ``path`` as a ``(height, width)`` array.

    Raises [`RequestRefused`][tether.services.base.RequestRefused] as [`describe_movie`][].
    """
    import numpy as np  # noqa: PLC0415

    path = Path(path)
    _adapter, source = _open(path)
    with source:
        try:
            return np.asarray(source.read_frames(0, 1)[0])
        except _reader_errors() as exc:
            raise RequestRefused(f"{path.name}: {exc}") from exc


def check_movie_pair(donor: MovieDescription, acceptor: MovieDescription) -> None:
    """Refuse two movies that cannot be the donor and the acceptor of one acquisition (#909).

    The frame count, the frame time, the pixel type and the frame size must be the same
    ([`tether.io.pair.check_pair`][]). Raises
    [`RequestRefused`][tether.services.base.RequestRefused] naming each value that differs,
    and also when the two are the same file.
    """
    from tether.io.pair import MoviePairError, check_pair  # noqa: PLC0415

    if donor.path.resolve() == acceptor.path.resolve():
        raise RequestRefused(f"{donor.name} cannot be paired with itself")

    def view(movie: MovieDescription) -> SimpleNamespace:
        return SimpleNamespace(
            shape=movie.shape, frame_time=movie.frame_time, dtype=movie.pixel_type
        )

    try:
        check_pair(view(donor), view(acceptor), donor_name=donor.name, acceptor_name=acceptor.name)  # type: ignore[arg-type]
    except MoviePairError as exc:
        raise RequestRefused(str(exc)) from exc


@dataclasses.dataclass(frozen=True)
class LayoutChoice:
    """The channel layout a user chose (#908): its kind and where the donor is.

    ``donor`` is a half (``left``/``right`` or ``top``/``bottom``) of a half layout and
    ``None`` for ``rectangles``. ``donor_rect`` and ``acceptor_rect`` are the two
    ``[y1, x1, y2, x2]`` rectangles of a ``rectangles`` layout, 1-based inclusive, read in
    the frame after the frame steps.
    """

    kind: str = "side-by-side"
    donor: str | None = "left"
    donor_rect: tuple[int, int, int, int] | None = None
    acceptor_rect: tuple[int, int, int, int] | None = None

    def swapped(self) -> LayoutChoice:
        """Return the same layout with the donor and the acceptor exchanged."""
        if self.kind == "rectangles":
            return dataclasses.replace(
                self, donor_rect=self.acceptor_rect, acceptor_rect=self.donor_rect
            )
        first, second = _POSITIONS[self.kind]
        return dataclasses.replace(self, donor=second if self.donor == first else first)

    def fields(self) -> dict[str, Any]:
        """Return the four layout fields of an ``extract`` request (ExtractionFields)."""
        rects = self.kind == "rectangles"
        return {
            "channel_layout": self.kind,
            "channel_layout_donor": None if rects else self.donor,
            "channel_layout_donor_rect": self.donor_rect if rects else None,
            "channel_layout_acceptor_rect": self.acceptor_rect if rects else None,
        }

    def profile_texts(self) -> dict[str, str]:
        """Return the layout fields as the extraction page's form texts (``""`` for none)."""

        def text(value: Any) -> str:
            if value is None:
                return ""
            return ", ".join(str(v) for v in value) if isinstance(value, tuple) else str(value)

        return {name: text(value) for name, value in self.fields().items()}


@dataclasses.dataclass(frozen=True)
class FrameView:
    """The first frame after the frame steps, and where each channel is in it.

    ``frame`` is the ``(height, width)`` frame after the steps. ``donor_rect`` and
    ``acceptor_rect`` are ``(y1, x1, y2, x2)``, 1-based inclusive, in that frame. For a
    movie pair each file is one whole channel, so both rectangles are the whole frame.
    ``warning`` names overlapping rectangles, which the layout allows; it is ``""`` otherwise.
    """

    frame: np.ndarray
    donor_rect: tuple[int, int, int, int]
    acceptor_rect: tuple[int, int, int, int]
    warning: str = ""


def _steps(text: str | None) -> Any:
    from tether.project.extract import ExtractionError  # noqa: PLC0415
    from tether.services.profile import frame_steps_from_text  # noqa: PLC0415

    try:
        return frame_steps_from_text(text or None)
    except ExtractionError as exc:
        raise RequestRefused(str(exc)) from exc


def frame_view(
    frame: np.ndarray,
    layout: LayoutChoice,
    frame_steps: str | None = None,
    *,
    pair: bool = False,
) -> FrameView:
    """Apply ``frame_steps`` and then ``layout`` to one frame, as extraction does (#1435).

    The steps (such as ``"rotate 90"``) run on the whole frame before the channel split,
    and the layout reads its cut positions in the frame after them. With ``pair`` the
    frame is one file of a movie pair, so the layout is not used. Raises
    [`RequestRefused`][tether.services.base.RequestRefused] with the reason when the steps
    do not read or do not fit the frame, or the layout does not fit it.
    """
    import warnings  # noqa: PLC0415

    import numpy as np  # noqa: PLC0415

    from tether.imaging.layout import (  # noqa: PLC0415
        ChannelLayout,
        ChannelLayoutError,
        ChannelOverlapWarning,
    )
    from tether.imaging.recipe import ChannelRecipeError  # noqa: PLC0415

    recipe = _steps(frame_steps)
    try:
        shown = np.asarray(frame) if recipe is None else np.asarray(recipe.apply(frame))
    except ChannelRecipeError as exc:
        raise RequestRefused(f"frame steps {frame_steps!r}: {exc}") from exc
    height, width = (int(v) for v in shown.shape)
    if pair:
        whole = (1, 1, height, width)
        return FrameView(frame=shown, donor_rect=whole, acceptor_rect=whole)
    fields = layout.fields()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ChannelOverlapWarning)
        try:
            core = ChannelLayout(
                kind=fields["channel_layout"],
                donor=fields["channel_layout_donor"],
                donor_rect=fields["channel_layout_donor_rect"],
                acceptor_rect=fields["channel_layout_acceptor_rect"],
            )
            core.geometries(height, width)
            donor_rect, acceptor_rect = core.rectangles(height, width)
        except ChannelLayoutError as exc:
            raise RequestRefused(f"channel layout: {exc}") from exc
    warning = "; ".join(str(w.message) for w in caught if w.category is ChannelOverlapWarning)
    return FrameView(
        frame=shown,
        donor_rect=tuple(int(v) for v in donor_rect),  # type: ignore[arg-type]
        acceptor_rect=tuple(int(v) for v in acceptor_rect),  # type: ignore[arg-type]
        warning=warning,
    )


def extract_request(
    movie: str | PathLike[str],
    output: str | PathLike[str],
    layout: LayoutChoice,
    frame_steps: str | None = None,
    *,
    acceptor_movie: str | PathLike[str] | None = None,
) -> ExtractRequest:
    """Return the ``extract`` request of one movie with the chosen layout and frame steps.

    A movie pair (``acceptor_movie``) is refused with
    [`RequestRefused`][tether.services.base.RequestRefused]: the ``extract`` request has no
    field for the acceptor file yet, so a recipe cannot hold a pair.
    """
    if acceptor_movie is not None:
        raise RequestRefused(
            f"{Path(movie).name} is a movie pair: an extract step cannot hold a movie pair yet"
        )
    _steps(frame_steps)  # refuse steps that do not read before a request holds them
    return ExtractRequest(
        movie=str(movie),
        output=str(output),
        frame_steps=frame_steps or None,
        **layout.fields(),
    )


def save_recipe(path: str | PathLike[str], requests: Sequence[ExtractRequest]) -> Path:
    """Write ``requests`` as the recipe file ``path`` (#899) and return the path.

    Raises [`RequestRefused`][tether.services.base.RequestRefused] when there is no request,
    and [`CommandFailed`][tether.services.base.CommandFailed] when the file cannot be written.
    """
    from tether.services.recipe import write_recipe  # noqa: PLC0415

    if not requests:
        raise RequestRefused("there is no applied movie to save as a recipe step")
    try:
        return write_recipe(path, list(requests))
    except OSError as exc:
        raise CommandFailed(f"{Path(path).name}: {exc}") from exc


# --- the open project -------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ProjectSource:
    """One source file of the open project (#912), and whether its file is where it was.

    ``missing`` is ``None`` when the file is at its recorded path; otherwise ``"file
    missing"`` or ``"file unreadable"``. Only the file's attributes are read.
    """

    source_id: str
    name: str
    role: str
    path: Path
    missing: str | None


def _missing(path: Path) -> str | None:
    try:
        return None if path.is_file() else "file missing"
    except OSError:
        return "file unreadable"


def read_project_sources(project_path: str | PathLike[str]) -> tuple[ProjectSource, ...]:
    """Return the source records of the project at ``project_path``, in registry order.

    Opens the project read-only and reads no movie file. Raises
    [`CommandFailed`][tether.services.base.CommandFailed] when the project cannot be read.
    """
    from tether.project.sources import read_sources  # noqa: PLC0415

    try:
        records = read_sources(project_path)
    except OSError as exc:
        raise CommandFailed(f"{Path(project_path).name}: {exc}") from exc
    out = []
    for record in records:
        path = record.resolve(project_path)
        name = record.original_name or Path(record.relative_path).name
        out.append(ProjectSource(record.source_id, name, record.role, path, _missing(path)))
    return tuple(out)


def relink_source(
    project: Project,
    source_id: str,
    new_path: str | PathLike[str],
    *,
    cancel: CancelToken | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> ProjectSource:
    """Link a moved source to ``new_path`` when its checksum says it is the same file.

    It reads the whole new file, so a front end runs it on a worker thread. Needs the
    writer lock that ``project`` retains. Raises
    [`CommandFailed`][tether.services.base.CommandFailed] with the reason when the checksum
    differs or is not known, when the file changes during the read, or when the lock is
    lost; nothing is written then.
    """
    from tether.project.lock import LockError  # noqa: PLC0415
    from tether.project.sources import (  # noqa: PLC0415
        RelinkRefused,
        SourceReplaced,
    )
    from tether.project.sources import relink_source as relink  # noqa: PLC0415

    try:
        record = relink(project, source_id, new_path, cancel=cancel, progress=progress)
    except (RelinkRefused, SourceReplaced, LockError, KeyError, OSError) as exc:
        raise CommandFailed(str(exc)) from exc
    path = record.resolve(project.path)
    name = record.original_name or Path(record.relative_path).name
    return ProjectSource(record.source_id, name, record.role, path, _missing(path))


@dataclasses.dataclass(frozen=True)
class FrameTime:
    """The frame time of one movie of the project, with its origin (#911).

    ``seconds`` is seconds per frame, ``0.0`` when unknown. ``origin`` is ``file``,
    ``user`` or ``unknown``. A value from the user is ``verified`` only after
    [`confirm_frame_time`][].
    """

    movie_id: str
    seconds: float
    origin: str
    verified: bool

    @property
    def state(self) -> str:
        """The words the page shows: the origin, and for a user's value whether it is verified."""
        if self.origin == "user":
            return "from you, confirmed" if self.verified else "from you, not verified"
        return ORIGIN_TEXT.get(self.origin, self.origin)


def _frame_time(record: Any) -> FrameTime:
    return FrameTime(
        record.movie_id,
        float(record.frame_time),
        str(record.frame_time_origin),
        bool(record.frame_time_verified),
    )


def read_frame_times(project_path: str | PathLike[str]) -> tuple[FrameTime, ...]:
    """Return the frame time of each movie of the project, in ``/movies`` order.

    Raises [`CommandFailed`][tether.services.base.CommandFailed] when the project cannot be read.
    """
    from tether.project.movie_metadata import read_movie_metadata  # noqa: PLC0415

    try:
        records = read_movie_metadata(project_path)
    except (OSError, KeyError) as exc:
        raise CommandFailed(f"{Path(project_path).name}: {exc}") from exc
    return tuple(_frame_time(r) for r in records.values())


def _write(project: Project, change: Callable[[], Any]) -> FrameTime:
    from tether.project.lock import LockError  # noqa: PLC0415

    try:
        project._assert_held_lock()  # the session's own writer lock, never another's
        return _frame_time(change())
    except (LockError, KeyError, ValueError, OSError) as exc:
        raise CommandFailed(str(exc)) from exc


def give_frame_time(project: Project, movie_id: str, seconds: float) -> FrameTime:
    """Record a frame time the user gives: origin ``user``, **not** verified (#911).

    Needs the writer lock that ``project`` retains. Raises
    [`CommandFailed`][tether.services.base.CommandFailed] when the value is not finite and
    positive, the movie is not in the project, or the lock is not held.
    """
    from tether.project.movie_metadata import set_frame_time  # noqa: PLC0415

    return _write(project, lambda: set_frame_time(project.path, movie_id, seconds))


def confirm_frame_time(project: Project, movie_id: str) -> FrameTime:
    """Mark the frame time of ``movie_id`` as verified: the separate confirm of #911.

    Raises [`CommandFailed`][tether.services.base.CommandFailed] as [`give_frame_time`][],
    and when the movie has no frame time to confirm.
    """
    from tether.project.movie_metadata import confirm_frame_time as confirm  # noqa: PLC0415

    return _write(project, lambda: confirm(project.path, movie_id))
