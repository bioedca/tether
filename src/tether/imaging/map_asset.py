# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The map file as a separate, reusable asset, and the reuse check (#916, ADR-0141).

The workflow plan's mapping lane (G02) stores the map as a "separate reusable map file:
source IDs + geometry + transform" and asks "map accepted with recorded QC?". Its note 02
says: "A map file is distinct from its calibration image/video. Reuse checks geometry and
optics, not date alone." This module is that file and that check.

A [`MapAsset`][] is a [`RegistrationMap`][tether.imaging.calibrate.RegistrationMap] (the
transform in both directions and the channel geometry, PRD Appendix E stage 10) with:

* ``map_id`` -- the map's own id, ``map:<32 hex>``, made once when the map is saved. A map
  file written before this format (version 0 or 1) has none; it reads with the id
  ``sha256:<hex>`` of its bytes, which names it as well.
* ``origin`` -- how the map was made ([`MAP_ORIGINS`][]): from beads (#915), from an
  imported Deep-LASI ``.tmap``, or fitted on a sample movie's own spots.
* ``source_ids`` -- the source ids (#912) of the files the map was made from, for example
  ``sha256:<hex>`` of the bead video.
* ``channel_layout`` -- the [`ChannelLayout`][tether.imaging.layout.ChannelLayout] (#908)
  with its per-channel geometry recipes (#910), or ``None`` when it is not known.
* ``quality`` -- the quality record of the map (#915: RMS residual, pairs, the residual of
  each pair, coverage), or ``None``. A map from a ``.tmap`` has none unless one is
  calculated.
* ``optics`` -- a text the user gives for the optics (objective, filters, camera, binning).
* ``pixel_size_um`` -- the camera pixel size in micrometres, or ``None`` when not known.
* ``created_utc`` and ``app_version`` -- when and by which Tether the map was saved.

[`save_map_asset`][] and [`load_map_asset`][] write and read the ``.npz`` map file at
format version 2 ([`MAP_FORMAT_VERSION`][tether.imaging.calibrate.MAP_FORMAT_VERSION]);
[`save_map`][tether.imaging.calibrate.save_map] writes the same format.

**The reuse check** ([`check_map_reuse`][]) compares what a map says with what is known of
the movie it is about to register, as two [`MapFacts`][]. A different channel layout,
channel geometry or pixel size is a refusal, and the message names each difference. A
different optics text is a warning that the user must confirm ([`require_map_reuse`][]).
A field one side does not know is not compared and is listed as such. **The date of a map
is never compared**: an old map with the same optics is as good as a new one.

A project records the id and the checksum of each map file it used in its ``/calibration``
provenance (``map_file``); [`project_map_links`][] reads them back, so one map file can
serve more than one project. [`save_project_map`][] saves a map that a project holds as a
separate asset, and [`map_asset_from_tmap`][] makes an asset from a ``.tmap``.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import uuid
import zipfile
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from tether.imaging.calibrate import (
    MAP_FORMAT_VERSION,
    MapFileError,
    RegistrationMap,
    _map_arrays,
    _map_format_version,
    load_map,
    read_calibration,
    registration_map_from_tmap,
)
from tether.imaging.layout import ChannelLayout, ChannelLayoutError
from tether.imaging.recipe import ChannelRecipeError
from tether.imaging.split import ChannelGeometry

__all__ = [
    "MAP_ORIGINS",
    "MAP_ORIGIN_BEADS",
    "MAP_ORIGIN_NATIVE_FIT",
    "MAP_ORIGIN_TMAP",
    "MapAsset",
    "MapFacts",
    "MapLink",
    "MapReuseCheck",
    "MapReuseError",
    "OpticsConfirmationNeeded",
    "check_map_reuse",
    "load_map_asset",
    "map_asset_from_tmap",
    "new_map_id",
    "project_map_links",
    "require_map_reuse",
    "save_map_asset",
    "save_project_map",
]

#: A map made from a bead video or image (#915).
MAP_ORIGIN_BEADS = "beads"
#: A map imported from a Deep-LASI ``.tmap``.
MAP_ORIGIN_TMAP = "tmap"
#: A map fitted on the spots of a sample movie (the native extraction fit).
MAP_ORIGIN_NATIVE_FIT = "native-fit"
#: Every origin a map file may record.
MAP_ORIGINS = (MAP_ORIGIN_BEADS, MAP_ORIGIN_TMAP, MAP_ORIGIN_NATIVE_FIT)

#: The arrays format version 2 adds to every map file (ADR-0141).
_ASSET_KEYS = (
    "map_id",
    "map_origin",
    "source_ids_json",
    "channel_layout_json",
    "quality_json",
    "optics",
    "pixel_size_um",
    "created_utc",
    "app_version",
)

#: Two pixel sizes are the same when they differ by no more than this relative amount.
#: This is the rounding of one value written two ways (a TIFF resolution is a ratio), not
#: a tolerance of the optics: a binning or an objective change is a factor, never 1e-6.
_PIXEL_SIZE_REL_TOL = 1e-6


class MapReuseError(ValueError):
    """A map that may not register this movie; the message names each difference."""


class OpticsConfirmationNeeded(MapReuseError):
    """The map's optics text differs from the movie's; the user must confirm to go on."""


def new_map_id() -> str:
    """Return a new map id, ``map:<32 hex>``."""
    return f"map:{uuid.uuid4().hex}"


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class MapFacts:
    """What a map, or a movie, says about the optics a map depends on.

    ``geometry`` is the ``(donor, acceptor)`` channel geometry. ``None`` for a field, and
    ``""`` for ``optics``, mean *not known*; a field not known on either side is not
    compared.
    """

    channel_layout: ChannelLayout | None = None
    geometry: tuple[ChannelGeometry, ChannelGeometry] | None = None
    pixel_size_um: float | None = None
    optics: str = ""

    @classmethod
    def for_movie(
        cls,
        *,
        channel_layout: ChannelLayout | None = None,
        frame_shape: tuple[int, int] | None = None,
        pixel_size_um: float | None = None,
        optics: str | None = None,
    ) -> MapFacts:
        """Return the facts of a movie; with a layout and a frame shape, its geometry too.

        Raises
        ------
        MapReuseError
            If the layout cannot split a frame of ``frame_shape``.
        """
        geometry = None
        if channel_layout is not None and frame_shape is not None:
            try:
                geometry = channel_layout.geometries(*frame_shape)
            except ChannelLayoutError as exc:
                raise MapReuseError(f"the movie's channel layout cannot split it: {exc}") from exc
        return cls(
            channel_layout=channel_layout,
            geometry=geometry,
            pixel_size_um=pixel_size_um,
            optics=optics or "",
        )


@dataclass(frozen=True)
class MapReuseCheck:
    """The result of [`check_map_reuse`][]: refusals, warnings, and fields not compared."""

    refusals: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    not_compared: tuple[str, ...] = ()

    @property
    def refused(self) -> bool:
        """Whether a layout, geometry or pixel-size difference refuses the map."""
        return bool(self.refusals)

    def to_dict(self, *, optics_confirmed: bool = False) -> dict[str, Any]:
        """Return the JSON-ready record a project stores with the map it used."""
        return {
            "refusals": list(self.refusals),
            "warnings": list(self.warnings),
            "not_compared": list(self.not_compared),
            "optics_confirmed": bool(optics_confirmed),
        }


def _geometry_text(geometry: tuple[ChannelGeometry, ChannelGeometry]) -> str:
    from tether.imaging.recipe import ChannelRecipe  # noqa: PLC0415

    donor, acceptor = geometry
    return (
        f"donor {ChannelRecipe.from_geometry(donor)}, "
        f"acceptor {ChannelRecipe.from_geometry(acceptor)}"
    )


def _layout_text(layout: ChannelLayout) -> str:
    parts = [layout.kind]
    if layout.donor is not None:
        parts.append(f"donor {layout.donor}")
    if layout.donor_rect is not None:
        parts.append(f"donor_rect {list(layout.donor_rect)}")
        parts.append(f"acceptor_rect {list(layout.acceptor_rect or ())}")
    for name in ("donor_recipe", "acceptor_recipe"):
        if getattr(layout, name) is not None:
            parts.append(f"{name} {getattr(layout, name)}")
    return ", ".join(parts)


def check_map_reuse(map_facts: MapFacts, movie: MapFacts) -> MapReuseCheck:
    """Compare a map's facts with a movie's; never read a date (G02 note 02).

    A different channel layout, channel geometry or pixel size is a refusal. A different
    optics text (compared after removing leading and trailing white space) is a warning
    the user must confirm. A field that either side does not know is listed in
    ``not_compared``.
    """
    refusals: list[str] = []
    warnings: list[str] = []
    not_compared: list[str] = []

    if map_facts.channel_layout is None or movie.channel_layout is None:
        not_compared.append("channel layout")
    elif map_facts.channel_layout.to_dict() != movie.channel_layout.to_dict():
        refusals.append(
            f"channel layout: the map has {_layout_text(map_facts.channel_layout)}; the "
            f"movie has {_layout_text(movie.channel_layout)}"
        )

    if map_facts.geometry is None or movie.geometry is None:
        not_compared.append("channel geometry")
    elif tuple(map_facts.geometry) != tuple(movie.geometry):
        refusals.append(
            f"channel geometry: the map has {_geometry_text(map_facts.geometry)}; the movie "
            f"has {_geometry_text(movie.geometry)}"
        )

    if map_facts.pixel_size_um is None or movie.pixel_size_um is None:
        not_compared.append("pixel size")
    elif not math.isclose(
        map_facts.pixel_size_um, movie.pixel_size_um, rel_tol=_PIXEL_SIZE_REL_TOL
    ):
        refusals.append(
            f"pixel size: the map has {map_facts.pixel_size_um:g} um; the movie has "
            f"{movie.pixel_size_um:g} um"
        )

    map_optics, movie_optics = map_facts.optics.strip(), movie.optics.strip()
    if not map_optics or not movie_optics:
        not_compared.append("optics")
    elif map_optics != movie_optics:
        warnings.append(f"optics: the map says {map_optics!r}; the movie says {movie_optics!r}")

    return MapReuseCheck(tuple(refusals), tuple(warnings), tuple(not_compared))


def require_map_reuse(
    map_facts: MapFacts, movie: MapFacts, *, confirm_optics: bool = False
) -> MapReuseCheck:
    """Run [`check_map_reuse`][] and refuse a map that may not register this movie.

    Raises
    ------
    MapReuseError
        On a refusal, naming every difference.
    OpticsConfirmationNeeded
        When the only differences are warnings and ``confirm_optics`` is false.
    """
    check = check_map_reuse(map_facts, movie)
    if check.refusals:
        raise MapReuseError("the map cannot be reused for this movie: " + "; ".join(check.refusals))
    if check.warnings and not confirm_optics:
        raise OpticsConfirmationNeeded(
            "the map's optics differ from the movie's ("
            + "; ".join(check.warnings)
            + "); confirm that the map applies to use it"
        )
    return check


# --- the asset --------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class MapAsset:
    """A map file's content: the registration map and the record that makes it reusable.

    See the module docstring for each field. ``format_version`` is the version the file
    was read at (the version written is always
    [`MAP_FORMAT_VERSION`][tether.imaging.calibrate.MAP_FORMAT_VERSION]).
    """

    registration_map: RegistrationMap
    map_id: str
    origin: str
    source_ids: tuple[str, ...] = ()
    channel_layout: ChannelLayout | None = None
    quality: dict[str, Any] | None = None
    optics: str = ""
    pixel_size_um: float | None = None
    created_utc: str = ""
    app_version: str = ""
    format_version: int = MAP_FORMAT_VERSION

    def __post_init__(self) -> None:
        """Refuse an asset whose record cannot be written."""
        if not isinstance(self.map_id, str) or not self.map_id.strip():
            raise ValueError("map_id must be a non-empty string")
        if self.origin not in MAP_ORIGINS:
            raise ValueError(f"origin must be one of {MAP_ORIGINS}, got {self.origin!r}")
        object.__setattr__(self, "source_ids", tuple(str(s) for s in self.source_ids))
        if self.channel_layout is not None and not isinstance(self.channel_layout, ChannelLayout):
            raise TypeError("channel_layout must be a ChannelLayout or None")
        if self.pixel_size_um is not None and not (
            math.isfinite(self.pixel_size_um) and self.pixel_size_um > 0
        ):
            raise ValueError(f"pixel_size_um must be > 0 or None, got {self.pixel_size_um!r}")

    @property
    def geometry(self) -> tuple[ChannelGeometry, ChannelGeometry] | None:
        """The ``(donor, acceptor)`` channel geometry, or ``None`` when the map has none."""
        reg = self.registration_map
        if reg.reference_geometry is None or reg.moving_geometry is None:
            return None
        return reg.reference_geometry, reg.moving_geometry

    def facts(self) -> MapFacts:
        """Return what this map says, for [`check_map_reuse`][]."""
        return MapFacts(
            channel_layout=self.channel_layout,
            geometry=self.geometry,
            pixel_size_um=self.pixel_size_um,
            optics=self.optics,
        )

    def record(self) -> dict[str, Any]:
        """Return the JSON-ready record a project keeps of the map it used (no transform)."""
        return {
            "map_id": self.map_id,
            "origin": self.origin,
            "source_ids": list(self.source_ids),
            "channel_layout": None
            if self.channel_layout is None
            else self.channel_layout.to_dict(),
            "optics": self.optics,
            "pixel_size_um": self.pixel_size_um,
            "has_quality": self.quality is not None,
            "created_utc": self.created_utc,
            "format_version": self.format_version,
        }

    @classmethod
    def from_registration_map(
        cls,
        reg_map: RegistrationMap,
        *,
        map_id: str | None = None,
        channel_layout: ChannelLayout | None = None,
        optics: str = "",
        pixel_size_um: float | None = None,
        created_utc: str | None = None,
        app_version: str | None = None,
    ) -> MapAsset:
        """Make an asset of ``reg_map``, taking origin, sources and quality from its provenance.

        The origin is the one a ``map_file`` record names (a map a project used from a
        file), else ``tmap`` for an imported map, else ``beads`` when the provenance holds
        a bead source (#915), else ``native-fit``. The source id is the bead source's, and
        the quality record is the provenance's ``map_quality`` (#915), when present.
        """
        prov = reg_map.provenance
        recorded = prov.get("map_file") if isinstance(prov.get("map_file"), dict) else {}
        bead_source = prov.get("bead_source") if isinstance(prov.get("bead_source"), dict) else {}
        if recorded.get("origin") in MAP_ORIGINS:
            origin = str(recorded["origin"])
        elif reg_map.source == "imported":
            origin = MAP_ORIGIN_TMAP
        elif bead_source:
            origin = MAP_ORIGIN_BEADS
        else:
            origin = MAP_ORIGIN_NATIVE_FIT
        source_ids: tuple[str, ...] = tuple(recorded.get("source_ids") or ())
        if not source_ids and bead_source.get("source_id"):
            source_ids = (str(bead_source["source_id"]),)
        elif not source_ids and prov.get("source_id"):
            source_ids = (str(prov["source_id"]),)
        quality = prov.get("map_quality") if isinstance(prov.get("map_quality"), dict) else None
        if app_version is None:
            import tether  # noqa: PLC0415

            app_version = str(tether.__version__)
        return cls(
            registration_map=reg_map,
            map_id=map_id or new_map_id(),
            origin=origin,
            source_ids=source_ids,
            channel_layout=channel_layout,
            quality=quality,
            optics=optics,
            pixel_size_um=pixel_size_um,
            created_utc=_utc_now() if created_utc is None else created_utc,
            app_version=app_version,
        )


def _json(value: Any) -> np.ndarray:
    return np.array(json.dumps(value, sort_keys=True))


def save_map_asset(asset: MapAsset, path: str | Path) -> Path:
    """Write ``asset`` as a ``.npz`` map file at format version 2 (ADR-0141).

    Every array of a version-1 file is written as before, so every reader of version 1
    finds them; the asset record is added as plain arrays and JSON strings. Nothing is
    pickled. Returns the path written (``np.savez`` adds ``.npz`` unless the suffix is
    exactly ``.npz``).
    """
    path = Path(path)
    arrays = _map_arrays(asset.registration_map)
    arrays.update(
        {
            "map_id": np.array(asset.map_id),
            "map_origin": np.array(asset.origin),
            "source_ids_json": _json(list(asset.source_ids)),
            "channel_layout_json": _json(
                None if asset.channel_layout is None else asset.channel_layout.to_dict()
            ),
            "quality_json": _json(asset.quality),
            "optics": np.array(asset.optics),
            "pixel_size_um": np.array(
                math.nan if asset.pixel_size_um is None else asset.pixel_size_um, dtype=np.float64
            ),
            "created_utc": np.array(asset.created_utc),
            "app_version": np.array(asset.app_version),
        }
    )
    np.savez(path, **arrays)
    return path if path.suffix == ".npz" else path.with_suffix(path.suffix + ".npz")


def load_map_asset(path: str | Path, *, content: bytes | None = None) -> MapAsset:
    """Read a map file as a [`MapAsset`][]; a version 0 or 1 file reads with a derived record.

    ``content`` is the file's bytes when the caller has read them already. A version-2
    file must hold every array of the record. An older file has none: it reads with the
    id ``sha256:<hex>`` of its bytes, the origin, source id and quality record its
    provenance gives, no layout, no optics text, no pixel size and no date.

    Raises
    ------
    MapFileError
        As [`load_map`][tether.imaging.calibrate.load_map], and for a version-2 file that
        lacks an array of the record or holds one that does not decode.
    """
    path = Path(path)
    if content is None:
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise MapFileError(f"{path.name} is not a readable .npz map file: {exc}") from exc
    reg_map = load_map(path, content=content)  # every check of versions 0 and 1
    with np.load(io.BytesIO(content), allow_pickle=False) as data:
        try:
            version = _map_format_version(data)
        except MapFileError as exc:  # pragma: no cover - load_map refused it already
            raise MapFileError(f"{path.name}: {exc}") from exc
        if version < 2:
            digest = hashlib.sha256(content).hexdigest()
            legacy = MapAsset.from_registration_map(
                reg_map, map_id=f"sha256:{digest}", created_utc="", app_version=""
            )
            app = reg_map.provenance.get("app_version")
            return replace(legacy, app_version=str(app or ""), format_version=version)
        missing = [key for key in _ASSET_KEYS if key not in data.files]
        if missing:
            raise MapFileError(f"{path.name} is a version-{version} map file that lacks {missing}")
        try:
            layout_raw = json.loads(str(data["channel_layout_json"]))
            quality = json.loads(str(data["quality_json"]))
            if quality is not None and not isinstance(quality, dict):
                raise TypeError("quality_json must hold one JSON object or null")
            pixel = float(data["pixel_size_um"])
            return MapAsset(
                registration_map=reg_map,
                map_id=str(data["map_id"]),
                origin=str(data["map_origin"]),
                source_ids=tuple(json.loads(str(data["source_ids_json"]))),
                channel_layout=None if layout_raw is None else ChannelLayout.from_dict(layout_raw),
                quality=quality,
                optics=str(data["optics"]),
                pixel_size_um=None if math.isnan(pixel) else pixel,
                created_utc=str(data["created_utc"]),
                app_version=str(data["app_version"]),
                format_version=version,
            )
        except (
            ValueError,
            TypeError,
            KeyError,
            ChannelLayoutError,
            ChannelRecipeError,
            zipfile.BadZipFile,
        ) as exc:
            raise MapFileError(
                f"{path.name} holds a map record that does not decode: {exc}"
            ) from exc


def map_asset_from_tmap(
    tmap_path: str | Path, *, optics: str = "", pixel_size_um: float | None = None
) -> MapAsset:
    """Make an asset from a Deep-LASI ``.tmap``: origin ``tmap``, no quality record.

    The source id is ``sha256:<hex>`` of the ``.tmap`` bytes. The ``.tmap`` carries no
    quality record, so the asset has none; one is recorded only when it is calculated.
    """
    import tether  # noqa: PLC0415
    from tether.imaging.register import read_tmap  # noqa: PLC0415

    tmap_path = Path(tmap_path)
    digest = hashlib.sha256(tmap_path.read_bytes()).hexdigest()
    reg_map = registration_map_from_tmap(
        read_tmap(tmap_path), app_version=tether.__version__, source_file=tmap_path.name
    )
    asset = MapAsset.from_registration_map(reg_map, optics=optics, pixel_size_um=pixel_size_um)
    return replace(asset, source_ids=(f"sha256:{digest}",), quality=None)


# --- the link from a project ------------------------------------------------------


@dataclass(frozen=True)
class MapLink:
    """One map file a project used: the calibration it became, the map id and the checksum."""

    calibration_id: str
    map_id: str
    sha256: str
    name: str


def project_map_links(project_path: str | Path) -> list[MapLink]:
    """Return the map files a project used, from its ``/calibration`` provenance.

    A calibration made with no map file (a native fit, an imported ``.tmap``) has no
    link. Reading writes nothing.
    """
    import h5py  # noqa: PLC0415

    links = []
    with h5py.File(Path(project_path), "r") as f:
        group = f.get("calibration")
        for calibration_id in [] if group is None else list(group):
            raw = group[calibration_id].attrs.get("provenance_json")
            if raw is None:
                continue
            provenance = json.loads(raw.decode() if isinstance(raw, bytes) else str(raw))
            record = provenance.get("map_file") if isinstance(provenance, dict) else None
            if not isinstance(record, dict) or "sha256" not in record:
                continue
            links.append(
                MapLink(
                    calibration_id=str(calibration_id),
                    map_id=str(record.get("map_id") or f"sha256:{record['sha256']}"),
                    sha256=str(record["sha256"]),
                    name=str(record.get("name", "")),
                )
            )
    return links


def save_project_map(
    project_path: str | Path,
    calibration_id: str,
    output: str | Path,
    *,
    channel_layout: ChannelLayout | None = None,
    optics: str = "",
    pixel_size_um: float | None = None,
) -> MapAsset:
    """Save the map a project holds in ``/calibration/<calibration_id>`` as a separate asset.

    The project is only read. A map that came from a map file keeps that file's id;
    any other map gets a new one. The output must not exist.

    Raises
    ------
    FileExistsError
        If ``output`` exists.
    KeyError
        If the project has no such calibration.
    """
    output = Path(output)
    if output.exists():
        raise FileExistsError(f"output exists: {output}")
    reg_map = read_calibration(project_path, calibration_id)
    recorded = reg_map.provenance.get("map_file")
    map_id = recorded.get("map_id") if isinstance(recorded, dict) else None
    asset = MapAsset.from_registration_map(
        reg_map,
        map_id=map_id or None,
        channel_layout=channel_layout,
        optics=optics,
        pixel_size_um=pixel_size_um,
    )
    save_map_asset(asset, output)
    return asset
