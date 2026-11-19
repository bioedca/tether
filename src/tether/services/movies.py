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
frame it was measured in. A row with no stored geometry (an all-zero crop, rotation 0, no
flip, as a Deep-LASI reconstruction writes) leaves the coordinates as they are, because
there is nothing recorded to undo.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence
    from os import PathLike

    import numpy as np

    from tether.io.movie import MovieReader

__all__ = [
    "ProjectMovie",
    "ProjectMovies",
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
        The movie file, the ``uri`` resolved against the project's folder, or ``None``
        when the row records no ``uri``.
    unavailable:
        ``None`` when the file can be opened; otherwise a short reason in plain words:
        ``"file missing"``, ``"file not on this disk"``, ``"file unreadable"`` or
        ``"no movie file recorded"``.
    donor_xy, acceptor_xy:
        ``(N, 2)`` ``[x, y]`` spot centres of this movie's molecules, in pixels of the
        whole camera frame (see [`subimage_to_frame`][]). A molecule whose stored
        coordinates are not finite is left out.
    """

    movie_id: str
    name: str
    path: Path | None
    unavailable: str | None
    donor_xy: np.ndarray
    acceptor_xy: np.ndarray

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


def _unavailable(path: Path | None) -> str | None:
    """Why ``path`` cannot be opened now, or ``None``. Reads attributes only (PRD §5.4)."""
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
    if not path.is_file():
        return "file missing"
    if attributes & _OFFLINE_ATTRIBUTES:
        return "file not on this disk"
    return None


def _resolve(uri: str, project_dir: Path) -> Path | None:
    if not uri:
        return None
    path = Path(uri)
    return path if path.is_absolute() else project_dir / path


def _aperture_radius(handle: object) -> float:
    from tether.analysis.overlap import DEFAULT_APERTURE_RADIUS

    group = handle.get("/settings/extraction")  # type: ignore[attr-defined]
    if group is not None and "disk_radius" in group.attrs:
        return float(group.attrs["disk_radius"])
    return float(DEFAULT_APERTURE_RADIUS)


def read_project_movies(path: str | PathLike[str]) -> ProjectMovies:
    """Read every ``/movies`` row of the project at ``path``, with its molecules' spots.

    Opens the project read-only with ``h5py`` and takes no lock. A project with no
    ``/movies`` table (an analysis-only import) gives no movies. A ``/molecules`` row whose
    ``movie_id`` names no ``/movies`` row (a subset export) belongs to no movie.

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

    if molecules is not None and len(molecules):
        mol_movie = np.array([_to_str(m) for m in molecules["movie_id"]])
        donor_all = np.asarray(molecules["donor_xy"], dtype=np.float64).reshape(-1, 2)
        acceptor_all = np.asarray(molecules["acceptor_xy"], dtype=np.float64).reshape(-1, 2)
    else:
        mol_movie = np.empty(0, dtype=str)
        donor_all = acceptor_all = np.empty((0, 2), dtype=np.float64)

    out: list[ProjectMovie] = []
    for row in movies:
        movie_id = _to_str(row["movie_id"])
        uri = _to_str(row["uri"])
        resolved = _resolve(uri, project_path.parent)
        mine = mol_movie == movie_id
        donor = donor_all[mine]
        acceptor = acceptor_all[mine]
        finite = np.isfinite(donor).all(axis=1) & np.isfinite(acceptor).all(axis=1)
        out.append(
            ProjectMovie(
                movie_id=movie_id,
                name=_movie_name(uri) if uri else movie_id,
                path=resolved,
                unavailable=_unavailable(resolved),
                donor_xy=_to_frame(donor[finite], row, "donor"),
                acceptor_xy=_to_frame(acceptor[finite], row, "acceptor"),
            )
        )
    return ProjectMovies(movies=tuple(out), aperture_radius=radius)


def open_project_movie(movie: ProjectMovie) -> MovieReader:
    """Open ``movie``'s file as a lazy [`MovieReader`][tether.io.movie.MovieReader].

    The caller owns the reader and closes it. A movie that is not
    [`available`][tether.services.movies.ProjectMovie.available] raises ``ValueError``
    naming the reason, so a missing or cloud-only file is never touched; a file the reader
    refuses raises what [`tether.io.movie.open_movie`][] raises.
    """
    from tether.io.movie import open_movie

    if movie.path is None or not movie.available:
        raise ValueError(f"movie {movie.name} cannot be opened: {movie.unavailable}")
    return open_movie(movie.path)
