# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Trace generations of a movie in a ``.tether`` store (#921, ADR-0136).

Each extraction of a movie writes a **generation**: a row of ``/generations/table``
with the movie, its per-movie number, a project-unique id, the UTC time and the inputs
that made it. One generation of a movie is **active**: its molecules are the movie's
rows of ``/molecules`` and of every ``/traces`` and ``/patches`` array, so every reader
of those arrays reads the active generation. When a movie is extracted again, the
generation that was active is first copied, unchanged, to
``/generations/archive/<movie_id>/<generation>`` and stays readable there through
[`read_generation`][]. An extraction never overwrites and never deletes a generation.

A store with no row for a movie (every store written before ``schema_version`` 2) reads
as generation 1 of that movie; reading it writes nothing.

This module holds the store layout and the readers. The writer that makes a new
generation is [`tether.imaging.extract.write_generation`][], and the command that runs
one is [`tether.project.extract.reextract_movie`][].
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "ARCHIVE",
    "GENERATION_MATCH_PX",
    "Generation",
    "GenerationData",
    "active_generation",
    "carried_molecule_keys",
    "match_previous",
    "read_generation",
    "read_generations",
]

#: A molecule of a new generation "did not move" when its donor position is within
#: this many pixels of a molecule of the previous generation of the same movie, the two
#: being each other's closest such partner (one-to-one). It then keeps that molecule's
#: ``molecule_key``, and its labels, category and analysis window are carried (ADR-0136;
#: registered in PRD §11.2). Five quanta of the key (0.1 px, ADR-0016) and a sixteenth
#: of the 8 px detection minimum separation, so two distinct molecules never compete.
GENERATION_MATCH_PX = 0.5

#: The group under ``/generations`` that holds every generation no longer active.
ARCHIVE = "archive"

_GROUP = "generations"
_TABLE = "table"
_TRACE_LAYERS = tuple(
    f"{channel}_{quantity}"
    for channel in ("donor", "acceptor")
    for quantity in ("raw", "corrected", "background")
)
_PATCH_LAYERS = ("donor", "acceptor")


@dataclass(frozen=True)
class Generation:
    """One trace generation of a movie (a row of ``/generations/table``).

    Attributes
    ----------
    movie_id:
        The movie it belongs to.
    generation:
        Its number among the movie's generations, from 1.
    generation_id:
        A project-unique id; the identity of the ``trace_generation`` record of
        ADR-0103 whose instance is ``movie_id``. ``""`` for an implied generation 1.
    created_utc:
        When it was written (ISO 8601, UTC); ``""`` when the store does not say.
    inputs:
        What made it: ``source``, ``geometry``, ``layout``, ``map``, ``colocalization``
        and ``options`` (the movie's settings profile).
    n_molecules:
        How many molecules it has.
    carried_keys:
        The ``molecule_key`` of each molecule whose labels, category and analysis window
        were carried from the previous generation ("did not move").
    match_px:
        The distance that decided "did not move"; ``nan`` for a first generation.
    active:
        Whether it is the movie's active generation.
    recorded:
        ``False`` for the generation 1 a store written before ``schema_version`` 2
        implies: nothing was stored for it and its inputs are read from the movie.
    """

    movie_id: str
    generation: int
    generation_id: str
    created_utc: str
    inputs: Mapping[str, Any]
    n_molecules: int
    carried_keys: tuple[str, ...]
    match_px: float
    active: bool
    recorded: bool


@dataclass(frozen=True)
class GenerationData:
    """The stored data of one generation, row-aligned like the store itself.

    Row ``i`` of ``molecules``, ``acceptor_detected``, every array of ``traces`` and
    every array of ``patches`` is the same molecule. ``traces`` arrays are cut to the
    movie's own frame count.
    """

    generation: Generation
    movie: np.ndarray
    molecules: np.ndarray
    acceptor_detected: np.ndarray
    traces: dict[str, np.ndarray]
    patches: dict[str, np.ndarray]


def _text(value: object) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _jsonable(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return [_jsonable(v) for v in value.tolist()]
    if isinstance(value, bytes | np.bytes_):
        return value.decode("utf-8")
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return value


def movie_row_index(f: Any, movie_id: str) -> int:
    """Return the row of ``movie_id`` in ``/movies/table``; raise ``KeyError`` if absent."""
    ids = [_text(v) for v in f["movies"][_TABLE]["movie_id"][()]]
    if movie_id not in ids:
        raise KeyError(f"no movie {movie_id!r} in /movies")
    return ids.index(movie_id)


def molecule_rows(f: Any, movie_id: str) -> slice:
    """Return the ``/molecules`` rows of ``movie_id`` as one contiguous slice.

    A movie's molecules are written as one block, so they are contiguous. Raises
    ``ValueError`` if they are not, since such a store cannot take a new generation
    without moving another movie's rows into it.
    """
    table = f["molecules"][_TABLE]
    if not table.shape[0]:
        return slice(0, 0)
    where = np.flatnonzero([_text(v) == movie_id for v in table["movie_id"][()]])
    if not where.size:
        return slice(0, 0)
    start, stop = int(where[0]), int(where[-1]) + 1
    if where.size != stop - start:
        raise ValueError(
            f"the molecules of movie {movie_id!r} are not one block of /molecules, so its "
            "active generation cannot be told apart from another movie's rows"
        )
    return slice(start, stop)


def _settings_profile(f: Any, movie_id: str) -> dict[str, Any]:
    """Return the movie's settings profile (``/settings/extraction``; ADR-0117)."""
    grp = f["settings"].get("extraction")
    if grp is None:
        return {}
    movies = grp.get("movies")
    raw = None
    if movies is not None and movie_id in movies.attrs:
        raw = movies.attrs[movie_id]
    elif "profile_json" in grp.attrs and f["movies"][_TABLE].shape[0] == 1:
        raw = grp.attrs["profile_json"]  # a one-movie store from before per-movie records
    return json.loads(_text(raw)) if raw is not None else {}


def _layout(f: Any, movie_id: str) -> dict[str, Any] | None:
    layouts = f["movies"].get("layouts")
    if layouts is None or movie_id not in layouts:
        return None
    return {str(k): _jsonable(v) for k, v in layouts[movie_id].attrs.items()}


def generation_inputs(f: Any, movie_id: str) -> dict[str, Any]:
    """Return the inputs of the movie's active generation, read from the store as it is now.

    ``source`` (content hash, URI, size, mtime), ``geometry`` (crops, rotations and flips),
    ``layout`` (the channel layout record or ``None``), ``map`` (the calibration id),
    ``colocalization`` (the rule the molecules were kept by; ``donor-anchored`` when the
    profile names none, ADR-0127) and ``options`` (the whole settings profile).
    """
    row = f["movies"][_TABLE][movie_row_index(f, movie_id)]
    profile = _settings_profile(f, movie_id)
    return {
        "source": {
            "sha256": _text(row["sha256"]),
            "uri": _text(row["uri"]),
            "file_size": int(row["file_size"]),
            "mtime": float(row["mtime"]),
        },
        "geometry": {
            name: _jsonable(row[name])
            for name in (
                "donor_crop",
                "acceptor_crop",
                "donor_rotation_deg",
                "acceptor_rotation_deg",
                "donor_flip",
                "acceptor_flip",
            )
        },
        "layout": _layout(f, movie_id),
        "map": {"calibration_id": _text(row["calibration_id"])},
        "colocalization": str(profile.get("colocalization", "donor-anchored")),
        "options": profile,
    }


def _stored_rows(f: Any) -> list[np.void]:
    group = f.get(_GROUP)
    if group is None or _TABLE not in group:
        return []
    return list(group[_TABLE][()])


def _record(row: Any, *, active: bool) -> Generation:
    return Generation(
        movie_id=_text(row["movie_id"]),
        generation=int(row["generation"]),
        generation_id=_text(row["generation_id"]),
        created_utc=_text(row["created_utc"]),
        inputs=json.loads(_text(row["inputs"]) or "{}"),
        n_molecules=int(row["n_molecules"]),
        carried_keys=tuple(json.loads(_text(row["carried_keys"]) or "[]")),
        match_px=float(row["match_px"]),
        active=active,
        recorded=True,
    )


def implied_generation(f: Any, movie_id: str) -> Generation:
    """Return generation 1 of a movie the store has no generation row for (writes nothing)."""
    rows = molecule_rows(f, movie_id)
    return Generation(
        movie_id=movie_id,
        generation=1,
        generation_id="",
        created_utc="",
        inputs=generation_inputs(f, movie_id),
        n_molecules=rows.stop - rows.start,
        carried_keys=(),
        match_px=float("nan"),
        active=True,
        recorded=False,
    )


def generations_in(f: Any, movie_id: str | None = None) -> list[Generation]:
    """Every generation of every movie (or of ``movie_id``) in an open store, in order."""
    movie_ids = [_text(v) for v in f["movies"][_TABLE]["movie_id"][()]]
    if movie_id is not None:
        if movie_id not in movie_ids:
            raise KeyError(f"no movie {movie_id!r} in /movies")
        movie_ids = [movie_id]
    by_movie: dict[str, list[Any]] = {}
    for row in _stored_rows(f):
        by_movie.setdefault(_text(row["movie_id"]), []).append(row)
    out: list[Generation] = []
    for mid in movie_ids:
        rows = sorted(by_movie.get(mid, []), key=lambda r: int(r["generation"]))
        if not rows:
            out.append(implied_generation(f, mid))
            continue
        newest = int(rows[-1]["generation"])
        out.extend(_record(r, active=int(r["generation"]) == newest) for r in rows)
    return out


def read_generations(project_path: str | Path, movie_id: str | None = None) -> list[Generation]:
    """Return every generation of every movie, or of ``movie_id``, oldest first per movie.

    A movie the store has no generation row for reads as its generation 1
    (``recorded=False``), so a store written before ``schema_version`` 2 needs no rewrite.

    Raises
    ------
    KeyError
        If ``movie_id`` is given and is not a movie of the project.
    """
    import h5py  # noqa: PLC0415

    with h5py.File(Path(project_path), "r") as f:
        return generations_in(f, movie_id)


def active_generation(project_path: str | Path, movie_id: str) -> Generation:
    """Return the active generation of ``movie_id`` (the one the readers of traces read)."""
    return next(g for g in read_generations(project_path, movie_id) if g.active)


def carried_molecule_keys(project_path: str | Path) -> set[str]:
    """Return the ``molecule_key`` of every molecule whose labels were carried.

    Only the active generations count: a molecule keeps the mark "carried" until a new
    generation of its movie is written without carrying it. A person confirming a
    carried label is the reconciliation of #923.
    """
    return {key for g in read_generations(project_path) if g.active for key in g.carried_keys}


def _flags(group: Any, rows: slice, n_rows: int) -> np.ndarray:
    out = np.full(rows.stop - rows.start, -1, dtype=np.int8)
    flags = group.get("acceptor_detected")
    if flags is not None:
        stop = min(rows.stop, int(flags.shape[0]), n_rows)
        if stop > rows.start:
            out[: stop - rows.start] = flags[rows.start : stop]
    return out


def active_data(f: Any, movie_id: str) -> tuple[np.ndarray, np.ndarray, dict, dict, np.ndarray]:
    """Return the active rows of a movie: (movie, molecules, traces, patches, flags)."""
    movie = f["movies"][_TABLE][movie_row_index(f, movie_id) : movie_row_index(f, movie_id) + 1]
    rows = molecule_rows(f, movie_id)
    n_frames = int(movie["n_frames"][0])
    molecules = f["molecules"][_TABLE][rows]
    traces = {
        name: f["traces"][name][rows, :n_frames] for name in _TRACE_LAYERS if name in f["traces"]
    }
    patches = {name: f["patches"][name][rows] for name in _PATCH_LAYERS if name in f["patches"]}
    flags = _flags(f["molecules"], rows, int(f["molecules"][_TABLE].shape[0]))
    return movie, molecules, traces, patches, flags


def archive_active(f: Any, record: Generation) -> None:
    """Copy the active generation ``record`` of its movie to the archive, unchanged.

    Writes ``/generations/archive/<movie_id>/<generation>`` with ``movie`` (the
    ``/movies`` row), ``molecules``, ``acceptor_detected``, ``traces/<layer>`` (cut to the
    movie's frame count), ``patches/<layer>`` and, when the movie has one, its ``layout``
    record. Refuses a generation that is already archived: an archive is written once.
    """
    archive = f[_GROUP].require_group(ARCHIVE).require_group(record.movie_id)
    name = str(record.generation)
    if name in archive:
        raise ValueError(
            f"generation {record.generation} of movie {record.movie_id!r} is already archived"
        )
    movie, molecules, traces, patches, flags = active_data(f, record.movie_id)
    group = archive.create_group(name, track_order=True)
    group.create_dataset("movie", data=movie)
    group.create_dataset("molecules", data=molecules)
    group.create_dataset("acceptor_detected", data=flags)
    traces_group = group.create_group("traces")
    for layer, data in traces.items():
        traces_group.create_dataset(layer, data=data, compression="gzip")
    patches_group = group.create_group("patches")
    for layer, data in patches.items():
        patches_group.create_dataset(layer, data=data, compression="gzip")
    layouts = f["movies"].get("layouts")
    if layouts is not None and record.movie_id in layouts:
        f.copy(layouts[record.movie_id], group, name="layout")


def read_generation(
    project_path: str | Path, movie_id: str, generation: int | None = None
) -> GenerationData:
    """Read the data of one generation of ``movie_id``; the active one by default.

    The active generation is read from ``/molecules``, ``/traces`` and ``/patches``; an
    earlier one from its archive. Either way the arrays are row-aligned.

    Raises
    ------
    KeyError
        If the movie, or that generation of it, is not in the project.
    """
    import h5py  # noqa: PLC0415

    with h5py.File(Path(project_path), "r") as f:
        records = generations_in(f, movie_id)
        wanted = next((g for g in records if g.active), None) if generation is None else None
        if generation is not None:
            wanted = next((g for g in records if g.generation == int(generation)), None)
        if wanted is None:
            raise KeyError(f"movie {movie_id!r} has no generation {generation}")
        if wanted.active:
            movie, molecules, traces, patches, flags = active_data(f, movie_id)
        else:
            stored = f[_GROUP][ARCHIVE][movie_id][str(wanted.generation)]
            movie = stored["movie"][()]
            molecules = stored["molecules"][()]
            flags = stored["acceptor_detected"][()]
            traces = {name: stored["traces"][name][()] for name in stored["traces"]}
            patches = {name: stored["patches"][name][()] for name in stored["patches"]}
    return GenerationData(
        generation=wanted,
        movie=movie,
        molecules=molecules,
        acceptor_detected=flags,
        traces=traces,
        patches=patches,
    )


def match_previous(
    previous_xy: np.ndarray, new_xy: np.ndarray, *, match_px: float = GENERATION_MATCH_PX
) -> np.ndarray:
    """For each new molecule, the previous molecule that "did not move", or ``-1``.

    A pair is within ``match_px`` (Euclidean, inclusive, on the donor position). Pairs
    are taken closest first, and each molecule is in at most one pair, so the result is
    one-to-one; ties go to the lower previous, then the lower new, index. Deterministic.
    """
    previous_xy = np.asarray(previous_xy, dtype=np.float64).reshape(-1, 2)
    new_xy = np.asarray(new_xy, dtype=np.float64).reshape(-1, 2)
    if not np.isfinite(match_px) or match_px < 0:
        raise ValueError(f"match_px must be a finite distance >= 0, got {match_px!r}")
    out = np.full(new_xy.shape[0], -1, dtype=np.int64)
    if not previous_xy.size or not new_xy.size:
        return out
    cell = max(float(match_px), 1e-9)
    grid: dict[tuple[int, int], list[int]] = {}
    for i, (x, y) in enumerate(previous_xy):
        grid.setdefault((int(np.floor(x / cell)), int(np.floor(y / cell))), []).append(i)
    pairs: list[tuple[float, int, int]] = []
    for j, (x, y) in enumerate(new_xy):
        cx, cy = int(np.floor(x / cell)), int(np.floor(y / cell))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for i in grid.get((cx + dx, cy + dy), ()):
                    d = float(np.hypot(*(previous_xy[i] - new_xy[j])))
                    if d <= match_px:
                        pairs.append((d, i, j))
    used: set[int] = set()
    for _d, i, j in sorted(pairs):
        if i in used or out[j] != -1:
            continue
        used.add(i)
        out[j] = i
    return out


def append_record(
    f: Any,
    movie_id: str,
    *,
    generation: int,
    carried_keys: tuple[str, ...] = (),
    match_px: float = float("nan"),
    inputs: Mapping[str, Any] | None = None,
    created_utc: str | None = None,
) -> Generation:
    """Append the row of one generation of ``movie_id`` to ``/generations/table``.

    ``inputs`` defaults to the movie's inputs as the store holds them now
    ([`generation_inputs`][]), so a writer calls this after it has written the movie's
    rows. A store stamped before version 2 gains the table first
    ([`upgrade_to_generations`][tether.io.schema.upgrade_to_generations]).
    """
    from datetime import UTC, datetime  # noqa: PLC0415
    from uuid import uuid4  # noqa: PLC0415

    from tether.io.schema import (  # noqa: PLC0415
        GENERATIONS_DTYPE,
        _app_version,
        upgrade_to_generations,
    )

    upgrade_to_generations(f)
    rows = molecule_rows(f, movie_id)
    record = np.zeros(1, dtype=GENERATIONS_DTYPE)
    record["movie_id"] = movie_id
    record["generation"] = int(generation)
    record["generation_id"] = f"gen-{uuid4().hex}"
    record["created_utc"] = (
        created_utc
        if created_utc is not None
        else datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    )
    record["inputs"] = json.dumps(
        _jsonable(dict(inputs) if inputs is not None else generation_inputs(f, movie_id)),
        sort_keys=True,
    )
    record["n_molecules"] = rows.stop - rows.start
    record["carried_keys"] = json.dumps(list(carried_keys))
    record["match_px"] = float(match_px)
    record["app_version"] = _app_version()
    table = f[_GROUP][_TABLE]
    n0 = table.shape[0]
    table.resize((n0 + 1,))
    table[n0:] = record
    return _record(record[0], active=True)
