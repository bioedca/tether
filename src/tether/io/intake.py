# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Deep-LASI acquisition intake — multi-file discovery + movie pairing (PRD §7.8).

The M7 "New project from Deep-LASI data" re-analysis workflow (PRD §7.8, goal G8)
hands Tether a folder of legacy files and asks it to reconstruct a round-trip-ready
project *without re-extraction*. The first step is **intake**: group a directory's
files into acquisitions and pair each to its raw movie so the user can confirm the
proposed set. This module is that headless core; the wizard UI that drives it is a
later M7 PR.

One acquisition (PRD §7.8 "Intake") is::

    { raw movie .tif, Deep-LASI .tdat }  +  any of { .mat export, .txt, tMAVEN SMD .hdf5 }

The registration ``.tmap`` is **not** part of the per-acquisition set — it is a
session/day-scoped map (its filename carries the acquisition *date*, not the video
index), so it is surfaced as a **shared map** offered to every acquisition of the
same condition (only needed for optional native re-extraction; recovered
coordinates already come pre-registered from the ``.tdat``/``.mat``).

Grouping reuses :func:`tether.io.filename.parse_filename` — the four core roles of
one acquisition (``.tif`` / ``.tdat`` / ``.mat`` / ``.txt``) all canonicalize to the
**same stem** (Deep-LASI glues ``DeepLASI_DATA_`` / ``DeepLASI_MAT_export_``
prefixes, a mid-name source ``.tif<timestamp>``, and a ``-donc-accc-w`` suffix onto
that stem), so the parser's stem is the pairing key PRD §7.8 calls for. The embedded
movie references (``.tdat`` ``LastPath``/source and ``.mat`` ``movie_path`` /
``movie_name``) provide an independent cross-check on the filename pairing.

**Scope (M7 S1).** Filename discovery + stem grouping + the ``.mat``-embedded
movie-reference cross-check (``.mat`` ``movie_name`` / ``movie_path`` are already
surfaced by :func:`tether.io.deeplasi.read_deeplasi_mat`). Reading the ``.tdat``
movie reference out of the ``TIRFdata`` object graph, coordinate recovery, and the
intensity-trace cross-check on the SMD index all belong to the "robust ``TIRFdata``
OOP decode + coordinate recovery" M7 PR; :func:`verify_movie_reference` takes a
:class:`MovieReference` from *either* source, so that later PR plugs its ``.tdat``
reference into the same seam with no API change.

Coordinate availability (PRD §7.8 "Coordinate sources"): only the ``.tdat`` and the
``.mat`` carry per-molecule pixel coordinates, so **full round-trip re-analysis
requires the ``.tdat`` *or* the ``.mat``** (plus the movie to link pixels). A set
with neither is an *analysis-only* candidate (the degraded, round-trip-disabled
branch of PRD §7.8, handled by its own importer PR).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path, PurePath
from typing import Literal

from tether.io.filename import parse_filename

__all__ = [
    "AcquisitionFileSet",
    "DiscoveryResult",
    "FileRole",
    "MovieRefCheck",
    "MovieReference",
    "classify_file",
    "discover_acquisitions",
    "read_mat_movie_reference",
    "verify_movie_reference",
]

#: The role a legacy file plays in a Deep-LASI acquisition (PRD §7.8, Appendix A/D).
#: ``"movie"`` is the raw big-endian TIFF; ``"tdat"`` the ``TIRFdata`` project;
#: ``"tmap"`` the session registration map; ``"mat"``/``"txt"`` the Deep-LASI
#: exports; ``"smd"`` a tMAVEN SMD-HDF5 container; ``"unknown"`` anything else.
FileRole = Literal["movie", "tdat", "tmap", "mat", "txt", "smd", "unknown"]

#: Extension → role. ``.tiff`` and ``.tif`` are both the movie; ``.hdf5`` is the
#: tMAVEN SMD container (the ``.tether`` store uses no bare ``.hdf5`` name in an
#: intake folder). Matched case-insensitively.
_ROLE_BY_EXT: dict[str, FileRole] = {
    ".tif": "movie",
    ".tiff": "movie",
    ".tdat": "tdat",
    ".tmap": "tmap",
    ".mat": "mat",
    ".txt": "txt",
    ".hdf5": "smd",
    ".h5": "smd",
}


def classify_file(path: str | PurePath) -> FileRole:
    """Classify one file into its :data:`FileRole` by extension (PRD §7.8).

    Deep-LASI glues a source ``.tif`` *into* ``.tdat`` names
    (``..._010.tif2025-07-21_00-00.tdat``), so classification keys on the **final**
    suffix (``PurePath.suffix``), never a mid-name extension. An unrecognized
    extension is ``"unknown"`` (collected into :attr:`DiscoveryResult.ignored`,
    never silently dropped).
    """
    return _ROLE_BY_EXT.get(PurePath(path).suffix.lower(), "unknown")


@dataclass(frozen=True)
class MovieReference:
    """A movie reference embedded in a Deep-LASI export (PRD §7.8 "Intake").

    ``name`` is the source-movie filename and ``path`` its containing directory,
    as recorded by the exporter; ``source`` names which artifact carried the
    reference (``"mat"`` or ``"tdat"``) for provenance in the confirm dialog. Both
    strings are best-effort — an exporter may leave either blank.
    """

    name: str
    path: str
    source: Literal["mat", "tdat"]


#: The outcome of cross-checking an acquisition's grouped movie against an embedded
#: reference. ``"confirmed"`` — the reference names the grouped movie; ``"mismatch"``
#: — it names a *different* file (the pairing is suspect); ``"movie_absent"`` — the
#: set has no ``.tif`` but the reference tells the user which movie to locate;
#: ``"no_reference"`` — no export carried a usable reference (fall back to the stem
#: pairing alone).
MovieRefStatus = Literal["confirmed", "mismatch", "movie_absent", "no_reference"]


@dataclass(frozen=True)
class MovieRefCheck:
    """Result of :func:`verify_movie_reference` (PRD §7.8 movie-pairing cross-check)."""

    status: MovieRefStatus
    #: The movie filename the embedded reference points at (``""`` if none).
    expected: str
    #: The grouped acquisition movie filename (``""`` if the set has no ``.tif``).
    found: str
    #: A human-readable one-liner for the pairing-confirm dialog.
    message: str


@dataclass(frozen=True)
class AcquisitionFileSet:
    """One Deep-LASI acquisition's paired files (PRD §7.8).

    Built by :func:`discover_acquisitions`. ``key`` is the shared filename stem the
    files were grouped on (a readable acquisition name, e.g.
    ``Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010``); ``condition_id`` /``video_index``
    are the parsed metadata that also match session-shared ``.tmap`` maps and any
    video-indexed SMD. Role paths are ``None`` when that file is absent.
    """

    key: str
    condition_id: str
    video_index: str
    movie: Path | None = None
    tdat: Path | None = None
    mat: Path | None = None
    txt: Path | None = None
    smd: Path | None = None
    #: Session/day registration maps (``.tmap``) offered to this acquisition
    #: (matched by condition); only needed for optional native re-extraction.
    shared_maps: tuple[Path, ...] = ()
    #: Non-fatal advisories for the user-confirm step (ambiguity, missing pieces).
    warnings: tuple[str, ...] = ()

    @property
    def has_movie(self) -> bool:
        """Whether the raw movie ``.tif`` is present (needed to link pixels)."""
        return self.movie is not None

    @property
    def has_coordinate_source(self) -> bool:
        """Whether a per-molecule coordinate source is present (PRD §7.8).

        Coordinates live only in the ``.tdat`` (``ParticlesColocalized``) or the
        ``.mat`` (``fret_pairs``); the ``.txt`` and SMD carry intensities only.
        """
        return self.tdat is not None or self.mat is not None

    @property
    def round_trip_available(self) -> bool:
        """Whether a full round-trip re-analysis can be reconstructed (PRD §7.8).

        Requires **both** a coordinate source (``.tdat`` or ``.mat``) *and* the
        movie to link those coordinates to pixels.
        """
        return self.has_movie and self.has_coordinate_source

    @property
    def analysis_only(self) -> bool:
        """Whether this set can only seed a degraded *analysis-only* project.

        True when no coordinate source is present (e.g. a ``.txt`` or SMD alone) —
        the round-trip browser and patch views are unavailable (PRD §7.8).
        """
        return not self.has_coordinate_source

    def files(self) -> tuple[Path, ...]:
        """All grouped file paths (roles + shared maps), in a stable role order."""
        roles = (self.movie, self.tdat, self.mat, self.txt, self.smd)
        return tuple(p for p in roles if p is not None) + self.shared_maps


@dataclass(frozen=True)
class DiscoveryResult:
    """The outcome of scanning a folder for Deep-LASI acquisitions (PRD §7.8)."""

    #: Grouped acquisitions, sorted by :attr:`AcquisitionFileSet.key`.
    acquisitions: tuple[AcquisitionFileSet, ...] = ()
    #: All ``.tmap`` registration maps found (session/day-scoped, not per-acquisition).
    shared_maps: tuple[Path, ...] = ()
    #: Files with an unrecognized role, preserved rather than silently dropped.
    ignored: tuple[Path, ...] = ()
    #: SMD/``.txt`` files that matched no acquisition by stem or video index.
    unpaired: tuple[Path, ...] = ()

    @property
    def round_trip_ready(self) -> tuple[AcquisitionFileSet, ...]:
        """Acquisitions that can reconstruct a live round-trip project (PRD §7.8)."""
        return tuple(a for a in self.acquisitions if a.round_trip_available)


# --- internal grouping helpers ----------------------------------------------


@dataclass
class _Group:
    """Mutable accumulator for one acquisition while scanning (finalized to a set)."""

    key: str
    condition_id: str
    video_index: str
    roles: dict[FileRole, list[Path]] = field(default_factory=dict)

    def add(self, role: FileRole, path: Path) -> None:
        self.roles.setdefault(role, []).append(path)


def _iter_files(directory: Path, *, recursive: bool) -> list[Path]:
    """List regular files in ``directory`` (optionally recursive), sorted for determinism.

    Filesystem iteration order is platform-dependent, so sorting makes grouping —
    and which file "wins" a duplicated role — reproducible across the 3-OS matrix.
    """
    walk = directory.rglob("*") if recursive else directory.iterdir()
    return sorted((p for p in walk if p.is_file()), key=lambda p: p.as_posix())


def _finalize(group: _Group) -> AcquisitionFileSet:
    """Collapse a scanned :class:`_Group` into an immutable :class:`AcquisitionFileSet`.

    Picks the single path per role (first by sorted order) and records a warning
    for any role that appeared more than once (an ambiguous set the user resolves).
    """
    warnings: list[str] = []

    def pick(role: FileRole) -> Path | None:
        paths = group.roles.get(role)
        if not paths:
            return None
        if len(paths) > 1:
            names = ", ".join(p.name for p in paths)
            warnings.append(
                f"multiple {role} files for {group.key!r}: {names}; using {paths[0].name}"
            )
        return paths[0]

    # A _Group only ever holds movie/tdat/mat/txt roles (the SMD is grouped
    # separately by video index / stem and attached later), so those four are the
    # only roles to pick here.
    movie, tdat, mat, txt = (pick(r) for r in ("movie", "tdat", "mat", "txt"))

    if movie is None and (tdat is not None or mat is not None):
        warnings.append("no raw movie .tif in this set; the trace↔movie round-trip needs it")
    if movie is not None and tdat is None and mat is None:
        warnings.append(
            "no .tdat or .mat coordinate source; only an analysis-only project is possible"
        )

    return AcquisitionFileSet(
        key=group.key,
        condition_id=group.condition_id,
        video_index=group.video_index,
        movie=movie,
        tdat=tdat,
        mat=mat,
        txt=txt,
        warnings=tuple(warnings),
    )


def discover_acquisitions(directory: str | PurePath, *, recursive: bool = False) -> DiscoveryResult:
    """Scan ``directory`` for Deep-LASI acquisitions and pair each to its movie (PRD §7.8).

    Groups ``.tif`` / ``.tdat`` / ``.mat`` / ``.txt`` files by their shared
    :func:`~tether.io.filename.parse_filename` stem — the pairing key PRD §7.8
    specifies. ``.tmap`` maps are surfaced as session-shared and attached to every
    acquisition of the same condition. An SMD ``.hdf5`` is attached to the
    acquisition it names — by exact stem first, else by ``video_index`` — or listed
    in :attr:`DiscoveryResult.unpaired` when its filename gives no confident match
    (SMD↔movie pairing is ultimately by molecule index + intensity match, a later
    M7 concern). Purely filename-based: no file contents are read.

    Parameters
    ----------
    directory:
        Folder to scan.
    recursive:
        Recurse into sub-folders (default ``False``).

    Returns
    -------
    DiscoveryResult
        Grouped acquisitions plus shared maps, unpaired SMD/txt, and ignored files.

    Raises
    ------
    NotADirectoryError
        If ``directory`` does not exist or is not a directory.
    """
    root = Path(directory)
    if not root.is_dir():
        raise NotADirectoryError(f"intake directory does not exist or is not a directory: {root}")

    groups: dict[str, _Group] = {}
    maps: list[tuple[Path, str]] = []  # (path, condition_id)
    ignored: list[Path] = []
    smds: list[tuple[Path, str, str]] = []  # (path, stem, video_index)

    for path in _iter_files(root, recursive=recursive):
        role = classify_file(path)
        if role == "unknown":
            ignored.append(path)
            continue

        parsed = parse_filename(path.name)
        stem, condition_id, video_index = parsed.stem, parsed.key.condition_id(), parsed.video_index

        if role == "tmap":
            maps.append((path, condition_id))
            continue
        if role == "smd":
            smds.append((path, stem, video_index))
            continue

        # movie / tdat / mat / txt all share the acquisition stem — group on it.
        group = groups.get(stem)
        if group is None:
            group = groups[stem] = _Group(
                key=stem, condition_id=condition_id, video_index=video_index
            )
        group.add(role, path)

    # Attach shared maps to acquisitions of the same condition (best-effort).
    map_by_condition: dict[str, list[Path]] = {}
    for map_path, map_cid in maps:
        map_by_condition.setdefault(map_cid, []).append(map_path)

    acquisitions: list[AcquisitionFileSet] = []
    for group in groups.values():
        acq = _finalize(group)
        shared = map_by_condition.get(group.condition_id)
        if shared:
            acq = replace(acq, shared_maps=tuple(sorted(shared, key=lambda p: p.as_posix())))
        acquisitions.append(acq)
    acquisitions.sort(key=lambda a: a.key)

    # Attach each SMD to its acquisition. The exact filename stem is the strongest
    # signal (it uniquely identifies an acquisition), so try it first; fall back to
    # the video index only when the stem matches nothing — otherwise a colliding
    # video index (per-session numbering restarts) could bolt an SMD onto the wrong
    # acquisition. An SMD with neither a stem nor a video-index match is left
    # unpaired (SMD↔movie pairing is ultimately by molecule index + intensity match,
    # a later M7 concern). Acquisitions are pre-sorted so the video-index fallback's
    # first-match tie-break is deterministic across the 3-OS matrix.
    by_stem = {acq.key: i for i, acq in enumerate(acquisitions)}
    by_video: dict[str, int] = {}
    for i, acq in enumerate(acquisitions):
        if acq.video_index:
            by_video.setdefault(acq.video_index, i)
    unpaired: list[Path] = []
    for smd_path, smd_stem, smd_video in smds:
        idx = by_stem.get(smd_stem)
        if idx is None and smd_video:
            idx = by_video.get(smd_video)
        if idx is None or acquisitions[idx].smd is not None:
            unpaired.append(smd_path)
            continue
        acquisitions[idx] = replace(acquisitions[idx], smd=smd_path)

    return DiscoveryResult(
        acquisitions=tuple(acquisitions),
        shared_maps=tuple(sorted((m for m, _ in maps), key=lambda p: p.as_posix())),
        ignored=tuple(ignored),
        unpaired=tuple(sorted(unpaired, key=lambda p: p.as_posix())),
    )


# --- movie-reference cross-check ---------------------------------------------


def _basename(name: str) -> str:
    """Basename of a possibly-foreign path string, splitting on ``/`` **and** ``\\``.

    A Deep-LASI ``.mat`` records the movie reference with a Windows path
    (``D:\\rig\\movie.tif``), so on a POSIX CI runner ``PurePath(name).name`` would
    not split on ``\\`` and would return the whole string. Normalizing both
    separators keeps the cross-check correct on every OS in the 3-OS matrix.
    """
    return name.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


def verify_movie_reference(
    fileset: AcquisitionFileSet, reference: MovieReference | None
) -> MovieRefCheck:
    """Cross-check a set's grouped movie against an embedded reference (PRD §7.8).

    The stem grouping is confirmed by the movie reference an export records
    (``.mat`` ``movie_name`` / ``.tdat`` source). Comparison is on the filename
    **basename** only (the recorded directory is the exporter's machine, rarely the
    user's), case-insensitively. A ``None`` reference (no export carried one) yields
    ``"no_reference"`` — the stem pairing stands on its own.
    """
    found = fileset.movie.name if fileset.movie is not None else ""
    if reference is None or not reference.name:
        return MovieRefCheck(
            status="no_reference",
            expected="",
            found=found,
            message="no embedded movie reference; pairing rests on the filename stem",
        )

    expected = _basename(reference.name)
    if fileset.movie is None:
        return MovieRefCheck(
            status="movie_absent",
            expected=expected,
            found="",
            message=(
                f"the {reference.source} names movie {expected!r}, absent from this "
                "set — locate it to enable round-trip"
            ),
        )
    if found.lower() == expected.lower():
        return MovieRefCheck(
            status="confirmed",
            expected=expected,
            found=found,
            message=f"movie {found!r} confirmed by the {reference.source} reference",
        )
    return MovieRefCheck(
        status="mismatch",
        expected=expected,
        found=found,
        message=(
            f"grouped movie {found!r} differs from the {reference.source} "
            f"reference {expected!r}; confirm the pairing"
        ),
    )


def read_mat_movie_reference(fileset: AcquisitionFileSet) -> MovieReference | None:
    """Read the ``.mat`` export's embedded movie reference, if the set has one (PRD §7.8).

    A thin wrapper over :func:`tether.io.deeplasi.read_deeplasi_mat` that lifts the
    already-parsed ``movie_name`` / ``movie_path`` provenance into a
    :class:`MovieReference` for :func:`verify_movie_reference`. Returns ``None`` when
    the set carries no ``.mat`` or the export recorded no movie name. The ``.mat``
    read is imported lazily so intake stays importable without SciPy present.
    """
    if fileset.mat is None:
        return None
    from tether.io.deeplasi import read_deeplasi_mat

    export = read_deeplasi_mat(fileset.mat)
    if not export.movie_name:
        return None
    return MovieReference(name=export.movie_name, path=export.movie_path, source="mat")
