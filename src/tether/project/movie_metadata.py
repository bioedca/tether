# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Movie metadata with its origin: frame time, pixel size, channel roles (#911).

A TIFF export often has no frame time, and a rate in s⁻¹ is only as good as the
frame interval behind it (§11.1). This module records, for each ``/movies`` row,
where three values came from: ``file`` (read from the movie's own metadata),
``user`` (typed in), or ``unknown``. A frame time is **verified** only when it
came from the file, or when the user confirms it in a separate action
(:func:`confirm_frame_time`); giving a value never verifies it.

Storage is additive data: one attribute-only group ``/movies/metadata/<movie_id>``,
the same per-id child pattern as ``/conditions/categories``. The frozen
``/movies.frame_time`` field keeps its meaning (the file's ``finterval``, ``0.0``
when absent) and is never rewritten here; a user's value lives in the group. A
store without the group (written before #911, or by a writer that does not record
origins) reads with the rule :func:`read_movie_metadata` documents.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, replace
from pathlib import Path

ORIGIN_FILE = "file"
ORIGIN_USER = "user"
ORIGIN_UNKNOWN = "unknown"
ORIGINS = (ORIGIN_FILE, ORIGIN_USER, ORIGIN_UNKNOWN)

_GROUP = "metadata"

__all__ = [
    "ORIGINS",
    "ORIGIN_FILE",
    "ORIGIN_UNKNOWN",
    "ORIGIN_USER",
    "MovieMetadataRecord",
    "confirm_frame_time",
    "positive_value",
    "read_movie_metadata",
    "set_frame_time",
    "set_pixel_size",
    "write_metadata_record",
]


@dataclass(frozen=True)
class MovieMetadataRecord:
    """One movie's frame time, pixel size and channel roles, each with its origin.

    ``frame_time`` is seconds per frame and ``pixel_size_um`` micrometres per pixel;
    ``0.0`` means unknown for both, the convention of ``/movies.frame_time``. The
    channel roles' *value* is the ``donor_crop``/``acceptor_crop`` geometry of the
    ``/movies`` row; only its origin is recorded here.
    """

    movie_id: str
    frame_time: float = 0.0
    frame_time_origin: str = ORIGIN_UNKNOWN
    frame_time_verified: bool = False
    pixel_size_um: float = 0.0
    pixel_size_origin: str = ORIGIN_UNKNOWN
    channel_roles_origin: str = ORIGIN_UNKNOWN

    def __post_init__(self) -> None:
        for name in ("frame_time_origin", "pixel_size_origin", "channel_roles_origin"):
            if getattr(self, name) not in ORIGINS:
                raise ValueError(f"{name} must be one of {ORIGINS}, got {getattr(self, name)!r}")
        if self.frame_time_verified and self.frame_time <= 0.0:
            raise ValueError("an unknown frame time cannot be verified")

    def to_dict(self) -> dict[str, object]:
        """The record as a plain JSON-ready mapping."""
        return asdict(self)


def positive_value(value: float, name: str) -> float:
    """Return ``value`` as a float; :class:`ValueError` unless finite and positive."""
    number = float(value)
    if not (math.isfinite(number) and number > 0.0):
        raise ValueError(f"{name} must be a finite positive number, got {value!r}")
    return number


def write_metadata_record(f: object, record: MovieMetadataRecord) -> None:
    """Write ``record`` into an open, writable store as ``/movies/metadata/<id>``."""
    movies = f["movies"]  # type: ignore[index]
    group = movies.require_group(_GROUP)
    node = group.require_group(record.movie_id)
    for key, value in record.to_dict().items():
        if key != "movie_id":
            node.attrs[key] = value


def _legacy_record(row: object) -> MovieMetadataRecord:
    """A record for a movie row with no stored origins.

    ``/movies.frame_time`` has only ever been filled from the movie's ImageJ
    ``finterval``, so a positive value is a file value (and so verified); ``0.0`` is
    unknown. Pixel size and channel roles were never recorded: unknown.
    """
    movie_id = _to_str(row["movie_id"])  # type: ignore[index]
    frame_time = float(row["frame_time"])  # type: ignore[index]
    if math.isfinite(frame_time) and frame_time > 0.0:
        return MovieMetadataRecord(movie_id, frame_time, ORIGIN_FILE, True)
    return MovieMetadataRecord(movie_id)


def _to_str(value: object) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _read_all(f: object) -> dict[str, MovieMetadataRecord]:
    movies = f["movies"]  # type: ignore[index]
    stored = movies.get(_GROUP, {})
    out: dict[str, MovieMetadataRecord] = {}
    for row in movies["table"][:]:
        movie_id = _to_str(row["movie_id"])
        if movie_id in stored:
            attrs = stored[movie_id].attrs
            out[movie_id] = MovieMetadataRecord(
                movie_id=movie_id,
                frame_time=float(attrs["frame_time"]),
                frame_time_origin=_to_str(attrs["frame_time_origin"]),
                frame_time_verified=bool(attrs["frame_time_verified"]),
                pixel_size_um=float(attrs["pixel_size_um"]),
                pixel_size_origin=_to_str(attrs["pixel_size_origin"]),
                channel_roles_origin=_to_str(attrs["channel_roles_origin"]),
            )
        else:
            out[movie_id] = _legacy_record(row)
    return out


def read_movie_metadata(path: str | Path) -> dict[str, MovieMetadataRecord]:
    """Every ``/movies`` row's metadata record, keyed by ``movie_id``. Read-only.

    A row without a stored record reads with the legacy rule: a positive
    ``/movies.frame_time`` has origin ``file`` and is verified, ``0.0`` is
    ``unknown``; pixel size and channel roles are ``unknown``.
    """
    import h5py  # noqa: PLC0415

    with h5py.File(Path(path), "r") as f:
        return _read_all(f)


def _update(path: str | Path, movie_id: str, **changes: object) -> MovieMetadataRecord:
    import h5py  # noqa: PLC0415

    with h5py.File(Path(path), "r+") as f:
        records = _read_all(f)
        if movie_id not in records:
            raise KeyError(f"no /movies row with movie_id {movie_id!r}")
        record = replace(records[movie_id], **changes)
        write_metadata_record(f, record)
    return record


def set_frame_time(path: str | Path, movie_id: str, seconds: float) -> MovieMetadataRecord:
    """Record a frame time the user gives (origin ``user``, **not** verified).

    The value replaces any earlier one, a file value included, and clears the
    verified flag: only :func:`confirm_frame_time` verifies a user's value.
    """
    value = positive_value(seconds, "frame time")
    return _update(
        path, movie_id, frame_time=value, frame_time_origin=ORIGIN_USER, frame_time_verified=False
    )


def confirm_frame_time(path: str | Path, movie_id: str) -> MovieMetadataRecord:
    """Mark a movie's frame time as verified by the user (the separate action).

    Refuses (:class:`ValueError`) when the frame time is unknown.
    """
    record = read_movie_metadata(path).get(movie_id)
    if record is None:
        raise KeyError(f"no /movies row with movie_id {movie_id!r}")
    if record.frame_time <= 0.0:
        raise ValueError(f"movie {movie_id!r} has no frame time to confirm; give one first")
    return _update(path, movie_id, frame_time_verified=True)


def set_pixel_size(path: str | Path, movie_id: str, micrometres: float) -> MovieMetadataRecord:
    """Record a pixel size (µm per pixel) the user gives (origin ``user``)."""
    value = positive_value(micrometres, "pixel size")
    return _update(path, movie_id, pixel_size_um=value, pixel_size_origin=ORIGIN_USER)
