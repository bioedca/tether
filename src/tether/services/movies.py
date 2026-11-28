# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The movies of a project, as a front end shows them (#356, PRD §7.3).

Like [`tether.services.notices`][], this reads and never writes, so it is not an action: it
has no request, no recipe step and no command-line subcommand, and it is a plain function
rather than a registered service command. It lives here so that the application reaches the
project's movies through one Qt-free place instead of reading the core itself (ADR-0100), and
like a command it imports the core only when it runs.

[`read_project_movies`][] reads ``/movies/table`` and the spot coordinates of ``/molecules``
once. It returns one [`ProjectMovie`][] per ``/movies`` row, in table order, and the aperture
radius the spots are drawn at. The application's movie switcher lists the rows, and
[`open_project_movie`][] opens each one that can be shown for its napari movie panel.

**Where the movie file is.** A row's ``uri`` is the path recorded at extraction. PRD §5.1
calls it relative, so a relative ``uri`` is read against the folder of the project file;
an absolute one is used as it is. A movie whose file is missing, or whose ``uri`` is
empty, is listed with the reason it cannot be shown. So is a file whose data is not on
this disk (a OneDrive Files-On-Demand placeholder): the check reads only the file's
attributes, as [`tether.project.condition_model`][] does, because PRD §5.4 says a routine
open never hydrates a placeholder. [`read_project_movies`][] opens no movie file.

**Which pixel frame.** The movie panel shows the whole camera frame, but ``/molecules``
stores ``donor_xy`` in the pixels of the donor *sub-image* and ``acceptor_xy`` in the
pixels of the acceptor sub-image (``docs/reference/tether-format.md``). A sub-image is the
frame after the channel's rotate, flip and crop ([`tether.imaging.split.process_image`][]).
[`subimage_to_frame`][] undoes those three steps, so each spot is drawn on the half of the
frame it was measured in. A channel whose geometry is all zero (crop, rotation and flip)
is a full-frame channel and keeps its coordinates. When *both* channels are all zero, as a
Deep-LASI reconstruction writes them, the zeros mean "not recorded": nothing says where
either sub-image sat in the frame, so the movie is shown without spots rather than with
spots on the wrong half ([`ProjectMovie.no_spots`][tether.services.movies.ProjectMovie]).

**Which folder.** PRD §5.1 records the ``uri`` relative to the project, so a relative
``uri`` is looked up in the project's folder first. Every writer records it that way since
#1282 ([`tether.project.sources.project_movie_uri`][]). A project written before then can
hold the path the extraction was given, relative to the folder the command ran in; when the
project-relative file does not exist, the same relative path is tried from the current
folder, and taken only when it matches the recorded fast signature (size and
modification time). Every movie file's size and modification time are checked against that
signature before it is shown; a mismatch is flagged, never trusted (PRD §5.4).

**A movie pair.** A project can hold the donor and the acceptor of one acquisition in two
files (#909): one ``/movies`` row whose ``uri`` names the donor file, and two source records
([`tether.project.sources.read_sources`][]) whose acquisition carries the ``pair_role``
``donor`` and ``acceptor``. The movie panel shows one file as one dual-channel frame, so it
would show only the donor movie and draw the acceptor spots on donor-camera pixels. Until
the panel can show each channel from its own file, such a row is listed as not available,
with a reason that names the pair (#1405), and [`open_project_movie`][] refuses it.

**A movie linked again.** ``/movies`` is frozen, so a movie whose file moved and was linked
again ([`tether.project.sources.relink_source`][], #912) keeps its old ``uri``; the new path
is in the movie's source record. When the file the ``uri`` names cannot be shown, and the
movie's stored record names another file, that file is used instead (#1515), unless it is
missing too. It is checked against the same fast signature, the size and modification time
``/movies`` recorded (PRD §5.4): the registry records no modification time of its own. A
file moved with its time kept is shown; a copy, which has a new time, is flagged
``"file changed since extraction"``, and **Locate movie…**
([`tether.services.movie_locations`][]) can then remember it after its checksum is read.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence
    from os import PathLike

    import numpy as np

    from tether.io.adapters import MovieSource

__all__ = [
    "ProjectMovie",
    "ProjectMovies",
    "movie_file_suffixes",
    "movie_frames",
    "open_project_movie",
    "read_project_movies",
    "subimage_to_frame",
]


def _to_str(value: object) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _movie_name(uri: str) -> str:
    """Return the file name of ``uri`` on either path convention, without touching the file."""
    return uri.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


@dataclass(frozen=True)
class ProjectMovie:
    """One ``/movies`` row: where its file is, whether it can be shown, and its spots.

    Attributes
    ----------
    movie_id:
        The row's ``movie_id``, the key ``/molecules`` rows point at.
    name:
        The label a front end shows: the file name of the ``uri``, or the ``movie_id``
        when no ``uri`` is recorded.
    path:
        The movie file, the ``uri`` resolved against the project's folder (or, for a
        relative ``uri`` not found there, a file at that path from the current folder that
        matches the recorded fast signature), or ``None`` when the row records no ``uri``.
    unavailable:
        ``None`` when the file can be opened; otherwise a short reason in plain words:
        ``"file missing"``, ``"file not on this disk"``, ``"file unreadable"``,
        ``"file changed since extraction"`` (its size or modification time differs from the
        recorded fast signature, PRD §5.4), ``"no movie file recorded"``, or, for a movie
        pair, ``"movie pair with <acceptor file>: the view of a pair is not available
        yet"`` (#1405).
    donor_xy, acceptor_xy:
        ``(N, 2)`` ``[x, y]`` spot centres of this movie's molecules, in pixels of the
        whole camera frame (see [`subimage_to_frame`][]). A molecule whose stored
        coordinates are not finite is left out. Both are empty when
        [`no_spots`][tether.services.movies.ProjectMovie] gives a reason.
    no_spots:
        ``None`` when the spots can be placed on the frame; otherwise why not, in plain
        words: ``"spot positions not recorded"`` when neither channel's geometry is.
    molecule_keys:
        The ``/molecules`` ``molecule_key`` of each spot pair, aligned with the rows of
        [`donor_xy`][] and [`acceptor_xy`][], so a front end can tell which molecule a
        spot belongs to (the trace↔movie round-trip, #357). Empty when
        [`no_spots`][tether.services.movies.ProjectMovie] gives a reason.
    file_size, mtime:
        The row's recorded fast signature (PRD §5.4), zero where the row records none.
        [`open_project_movie`][] compares the file with it again just before it opens.
    """

    movie_id: str
    name: str
    path: Path | None
    unavailable: str | None
    donor_xy: np.ndarray
    acceptor_xy: np.ndarray
    no_spots: str | None = None
    molecule_keys: tuple[str, ...] = ()
    file_size: int = 0
    mtime: float = 0.0

    @property
    def available(self) -> bool:
        """Whether the movie file can be opened: [`unavailable`][] is ``None``."""
        return self.unavailable is None


@dataclass(frozen=True)
class ProjectMovies:
    """What [`read_project_movies`][] returns: the rows, and the radius to draw spots at.

    Attributes
    ----------
    movies:
        One [`ProjectMovie`][] per ``/movies`` row, in table order; empty for a project
        with no ``/movies`` table.
    aperture_radius:
        The extraction's stored ``disk_radius`` (``/settings/extraction``), or
        [`DEFAULT_APERTURE_RADIUS`][tether.analysis.overlap.DEFAULT_APERTURE_RADIUS] when
        the project stores none: the same radius the overlap view uses.
    """

    movies: tuple[ProjectMovie, ...]
    aperture_radius: float


def _rotated_shape(height: int, width: int, rotation_deg: int) -> tuple[int, int]:
    """Return the ``(H, W)`` of a frame rotated by ``rotation_deg`` (a 90° multiple)."""
    return (width, height) if (rotation_deg // 90) % 2 else (height, width)


def subimage_to_frame(
    xy: np.ndarray,
    *,
    frame_shape: tuple[int, int],
    crop: Sequence[int] | None,
    rotation_deg: int = 0,
    flip: Sequence[int] = (0, 0),
) -> np.ndarray:
    """Map ``(N, 2)`` ``[x, y]`` sub-image coordinates back to the whole camera frame.

    The inverse of [`process_image`][tether.imaging.split.process_image], which makes a
    channel's sub-image from a ``frame_shape`` frame by rotating it clockwise by
    ``rotation_deg``, then flipping it (``flip`` = ``[v, h]``), then cropping it to
    ``crop`` (``[y1, x1, y2, x2]``, 1-based inclusive; ``None`` or all zeros for no crop).
    This undoes the crop, then the flip, then the rotation. A pixel centre sits on an
    integer coordinate in both frames, and sub-pixel positions map the same way.

    Returns a new ``float64`` array; ``xy`` is not changed.
    """
    import numpy as np

    pts = np.asarray(xy, dtype=np.float64).reshape(-1, 2)
    rows = pts[:, 1].copy()
    cols = pts[:, 0].copy()
    height, width = (int(v) for v in frame_shape)
    rotation = int(rotation_deg) % 360
    if rotation % 90:
        raise ValueError(f"rotation_deg must be a multiple of 90, got {rotation_deg}")
    rot_h, rot_w = _rotated_shape(height, width, rotation)
    # 1. The crop starts at (y1, x1), 1-based, in the rotated and flipped frame.
    if crop is not None and any(int(v) for v in crop):
        y1, x1 = int(crop[0]), int(crop[1])
        rows += y1 - 1
        cols += x1 - 1
    # 2. A flip is its own inverse, in the rotated frame's size.
    v_flip, h_flip = (int(v) for v in flip)
    if v_flip:
        rows = (rot_h - 1) - rows
    if h_flip:
        cols = (rot_w - 1) - cols
    # 3. process_image turned the frame with np.rot90(frame, k) (counter-clockwise k
    # times, k = (-rotation // 90) % 4). One np.rot90 step maps source (r, c) of an
    # (h, w) array to (w - 1 - c, r); undo the k steps one at a time, last first.
    k = (-rotation // 90) % 4
    shapes = [(height, width)]
    for _ in range(k):
        h, w = shapes[-1]
        shapes.append((w, h))
    for step in range(k, 0, -1):
        _h, w = shapes[step - 1]  # the shape before this step
        rows, cols = cols, (w - 1) - rows
    return np.column_stack([cols, rows])


def _to_frame(xy: np.ndarray, row: np.void, channel: str) -> np.ndarray:
    import numpy as np

    crop = [int(v) for v in np.asarray(row[f"{channel}_crop"]).ravel()]
    rotation = int(row[f"{channel}_rotation_deg"])
    flip = [int(v) for v in np.asarray(row[f"{channel}_flip"]).ravel()]
    if not any(crop) and rotation == 0 and not any(flip):
        return np.asarray(xy, dtype=np.float64).reshape(-1, 2).copy()
    return subimage_to_frame(
        xy,
        frame_shape=(int(row["height"]), int(row["width"])),
        crop=crop,
        rotation_deg=rotation,
        flip=flip,
    )


def _signature_matches(path: Path, file_size: int, mtime: float) -> bool:
    """Whether ``path``'s size and modification time equal the recorded fast signature.

    PRD §5.4: a routine open checks only the metadata signature, with no byte read. A field
    the row does not record (zero) is not compared.
    """
    st = path.stat()
    if file_size > 0 and int(st.st_size) != file_size:
        return False
    return not (mtime > 0 and float(st.st_mtime) != mtime)


def _unavailable(path: Path | None, file_size: int, mtime: float) -> str | None:
    """Why ``path`` cannot be shown now, or ``None``. Reads metadata only (PRD §5.4)."""
    # The offline test of the retrain's project check: one definition of "not on this disk".
    from tether.project.condition_model import _OFFLINE_ATTRIBUTES, _file_attributes

    if path is None:
        return "no movie file recorded"
    try:
        attributes = _file_attributes(path)
    except FileNotFoundError:
        return "file missing"
    except OSError:
        return "file unreadable"
    try:
        if not path.is_file():
            return "file missing"
    except OSError:
        return "file unreadable"
    if attributes & _OFFLINE_ATTRIBUTES:
        return "file not on this disk"
    try:
        if not _signature_matches(path, file_size, mtime):
            # PRD §5.4: a mismatch is flagged and never silently trusted.
            return "file changed since extraction"
    except OSError:
        return "file unreadable"
    return None


def _resolve(uri: str, project_dir: Path, file_size: int, mtime: float) -> Path | None:
    """Return the movie file ``uri`` names: project-relative first (PRD §5.1).

    A relative ``uri`` whose project-relative file does not exist is tried from the current
    folder too, where the extraction writer's caller may have meant it. A file there is
    taken only when the row records a fast signature and the file matches all of it (size
    and modification time); anything else would be a relink, which needs the content
    hash. Only ``stat`` is read, never the file's bytes.
    """
    if not uri:
        return None
    path = Path(uri)
    if path.is_absolute():
        return path
    in_project = project_dir / path
    try:
        # A folder that cannot be read raises here; the file is then classified as
        # unreadable by ``_unavailable`` rather than failing the project's open.
        if in_project.exists():
            return in_project
    except OSError:
        return in_project
    try:
        found = (
            file_size > 0
            and mtime > 0
            and path.is_file()
            and _signature_matches(path, file_size, mtime)
        )
    except OSError:
        found = False
    return path.absolute() if found else in_project


def _geometry_recorded(row: np.void) -> bool:
    """Whether either channel of a ``/movies`` row records a crop, rotation or flip."""
    import numpy as np

    for channel in ("donor", "acceptor"):
        if any(int(v) for v in np.asarray(row[f"{channel}_crop"]).ravel()):
            return True
        if int(row[f"{channel}_rotation_deg"]):
            return True
        if any(int(v) for v in np.asarray(row[f"{channel}_flip"]).ravel()):
            return True
    return False


def _aperture_radius(handle: object) -> float:
    from tether.analysis.overlap import DEFAULT_APERTURE_RADIUS

    group = handle.get("/settings/extraction")  # type: ignore[attr-defined]
    if group is not None and "disk_radius" in group.attrs:
        return float(group.attrs["disk_radius"])
    return float(DEFAULT_APERTURE_RADIUS)


def _pair_partners(project_path: Path) -> dict[str, str]:
    """Return ``{movie_id: acceptor file name}`` for each movie that is a movie pair (#909).

    A movie is a pair when any of its source records carries a ``pair_role`` in its
    acquisition; the name is that of the record whose role is ``acceptor`` (empty when the
    registry holds none). Reads the source registry only, never a movie file.
    """
    from tether.io.pair import PAIR_ROLE_KEY
    from tether.project.sources import read_sources

    partners: dict[str, str] = {}
    for record in read_sources(project_path):
        role = record.acquisition.get(PAIR_ROLE_KEY) if record.role == "movie" else None
        if not role or not record.movie_id:
            continue
        name = record.original_name or _movie_name(record.relative_path)
        if role == "acceptor":
            partners[record.movie_id] = name
        else:
            partners.setdefault(record.movie_id, "")
    return partners


def _relinked_paths(project_path: Path) -> dict[str, Path]:
    """Return ``{movie_id: path}`` of each movie's stored source record (#1515).

    Only a record the registry holds (``stored``) is returned, not one made from the
    ``/movies`` row. A movie with more than one movie record (a movie pair, #909) is left
    out. Reads the source registry only, never a movie file.
    """
    from tether.project.sources import read_sources

    found: dict[str, Path] = {}
    seen: set[str] = set()
    for record in read_sources(project_path):
        if record.role != "movie" or not record.movie_id or not record.relative_path:
            continue
        if record.movie_id in seen:
            found.pop(record.movie_id, None)
            continue
        seen.add(record.movie_id)
        if record.stored:
            found[record.movie_id] = record.resolve(project_path)
    return found


def _pair_refusal(acceptor_name: str) -> str:
    """Return the ``unavailable`` reason of a movie pair, naming the acceptor file (#1405)."""
    pair = f"movie pair with {acceptor_name}" if acceptor_name else "movie pair"
    return f"{pair}: the view of a pair is not available yet"


def read_project_movies(path: str | PathLike[str]) -> ProjectMovies:
    """Read every ``/movies`` row of the project at ``path``, with its molecules' spots.

    Opens the project read-only with ``h5py`` and takes no lock. A project with no
    ``/movies`` table (an analysis-only import) gives no movies. A ``/molecules`` row whose
    ``movie_id`` names no ``/movies`` row (a subset export) belongs to no movie. A movie
    pair (donor and acceptor in two files, #909) is listed as not available, with a reason
    that names the pair, and is never offered as one dual-channel movie (#1405). A movie
    whose file is not where its ``uri`` says is looked for where its source record was
    linked again (#1515).

    Raises ``OSError`` when the project cannot be read.
    """
    import h5py
    import numpy as np

    from tether.io.schema import TABLE

    project_path = Path(path)
    with h5py.File(project_path, "r") as handle:
        radius = _aperture_radius(handle)
        movies_group = handle.get("movies")
        if movies_group is None or TABLE not in movies_group:
            return ProjectMovies(movies=(), aperture_radius=radius)
        movies = movies_group[TABLE][:]
        molecules_group = handle.get("molecules")
        molecules = (
            molecules_group[TABLE][:]
            if molecules_group is not None and TABLE in molecules_group
            else None
        )

    partners = _pair_partners(project_path)
    relinked = _relinked_paths(project_path)

    if molecules is not None and len(molecules):
        mol_movie = np.array([_to_str(m) for m in molecules["movie_id"]])
        mol_key = np.array([_to_str(k) for k in molecules["molecule_key"]])
        donor_all = np.asarray(molecules["donor_xy"], dtype=np.float64).reshape(-1, 2)
        acceptor_all = np.asarray(molecules["acceptor_xy"], dtype=np.float64).reshape(-1, 2)
    else:
        mol_movie = mol_key = np.empty(0, dtype=str)
        donor_all = acceptor_all = np.empty((0, 2), dtype=np.float64)

    out: list[ProjectMovie] = []
    for row in movies:
        movie_id = _to_str(row["movie_id"])
        uri = _to_str(row["uri"])
        file_size, mtime = int(row["file_size"]), float(row["mtime"])
        resolved = _resolve(uri, project_path.parent, file_size, mtime)
        reason = _unavailable(resolved, file_size, mtime)
        if reason is not None and movie_id in relinked:
            # A movie linked again (#1515): its stored record names where the file is now.
            new_path = relinked[movie_id]
            new_reason = _unavailable(new_path, file_size, mtime)
            # The new place is taken unless its file is gone too; a file there that cannot
            # be shown (changed, not on this disk) is listed with its own reason.
            if new_path != resolved and new_reason != "file missing":
                resolved, reason = new_path, new_reason
        mine = mol_movie == movie_id
        donor = donor_all[mine]
        acceptor = acceptor_all[mine]
        finite = np.isfinite(donor).all(axis=1) & np.isfinite(acceptor).all(axis=1)
        no_spots = None if _geometry_recorded(row) else "spot positions not recorded"
        if no_spots is None:
            donor_xy = _to_frame(donor[finite], row, "donor")
            acceptor_xy = _to_frame(acceptor[finite], row, "acceptor")
            keys = tuple(str(k) for k in mol_key[mine][finite])
        else:
            donor_xy = acceptor_xy = np.empty((0, 2), dtype=np.float64)
            keys = ()
        out.append(
            ProjectMovie(
                movie_id=movie_id,
                name=_movie_name(uri) if uri else movie_id,
                path=resolved,
                unavailable=(_pair_refusal(partners[movie_id]) if movie_id in partners else reason),
                donor_xy=donor_xy,
                acceptor_xy=acceptor_xy,
                no_spots=no_spots,
                molecule_keys=keys,
                file_size=file_size,
                mtime=mtime,
            )
        )
    return ProjectMovies(movies=tuple(out), aperture_radius=radius)


def movie_frames(source: MovieSource) -> Any:
    """Return the ``(n_frames, height, width)`` frames of an open movie, for display.

    A TIFF gives its memory map ([`tether.io.movie.MovieReader.data`][]); any other
    reader a lazy [`tether.io.frames.FrameView`][] that reads only the frames an index
    touches (#1388). Nothing is read here. A value that is not a
    [`MovieSource`][tether.io.adapters.MovieSource] raises `TypeError`.
    """
    from tether.io.adapters import MovieSource
    from tether.io.frames import FrameView
    from tether.io.movie import MovieReader

    if isinstance(source, MovieReader):
        return source.data
    if not isinstance(source, MovieSource):
        raise TypeError(f"expected an open movie source, got {type(source).__name__}")
    return FrameView(source)


def movie_file_suffixes() -> tuple[str, ...]:
    """Return the file suffixes of the movies that extraction reads, for a file dialog.

    They are [`tether.project.batch_inputs.movie_suffixes`][]: the extensions of every
    registered reader in [`tether.io.movie.EXTRACTED_CONTAINERS`][] (#1388).
    """
    from tether.project.batch_inputs import movie_suffixes

    return movie_suffixes()


def open_project_movie(movie: ProjectMovie) -> MovieSource:
    """Open ``movie``'s file as a lazy [`MovieSource`][tether.io.adapters.MovieSource].

    Any registered reader opens it (#1388): a TIFF as a
    [`MovieReader`][tether.io.movie.MovieReader], an ND2 through its own reader. The caller
    owns the source and closes it. A movie that is not
    [`available`][tether.services.movies.ProjectMovie.available] raises ``ValueError``
    naming the reason, so a missing or cloud-only file is never touched; a file the reader
    refuses raises what [`tether.io.adapters.open_movie_source`][] raises.

    The file is checked again just before the reader opens it, against the recorded fast
    signature (PRD §5.4, metadata only): a sync can replace it after
    [`read_project_movies`][] read it (#1293). A file that has changed, gone or left this
    disk since then raises the same ``ValueError``.
    """
    from tether.io.adapters import open_movie_source

    if movie.path is None or not movie.available:
        raise ValueError(f"movie {movie.name} cannot be opened: {movie.unavailable}")
    reason = _unavailable(movie.path, movie.file_size, movie.mtime)
    if reason is not None:
        raise ValueError(f"movie {movie.name} cannot be opened: {reason}")
    return open_movie_source(movie.path)
