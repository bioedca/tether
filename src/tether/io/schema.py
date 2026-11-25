# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The frozen ``.tether`` HDF5 project-store skeleton (PRD §5, §9 M0).

This module is the **single source of truth** for the project-store structure.
[`create_project`][] writes a fresh ``.tether`` with the *entire* §5.1 group
skeleton forward-declared and version-stamped, so that every later milestone adds
*data*, not *structure* (the M0 schema freeze, PRD §9 M0 / §12.6).

The keystone invariant — **additive-only after M0** — is enforced mechanically by
the ``schema-guard`` CI gate: [`build_manifest`][] dumps the structure this code
declares and [`diff_manifest`][] compares it to the committed golden manifest
``schema/schema_frozen.json``. Adding a group, dataset, or field passes; removing,
renaming, or changing the dtype/shape of a frozen field fails (naming the field),
and the monotonic ``schema_version`` may never decrease. A deliberate structural
change is not forbidden — it carries an ADR + a ``schema_version`` bump + a
regenerated golden in the same PR (PRD §12.6).

Design notes
------------
* **Rich entities are groups holding a 0-row compound ``table`` dataset.** The
  field set (e.g. ``molecule_key`` on ``/molecules`` *and* ``/labels``, the
  ``/labels`` provenance fields, the ``/movies`` metadata-only fast signature, the
  ``/conditions`` identity key) lives in the table's compound dtype, so the freeze
  is introspectable now even though no rows exist yet. The group wrapper leaves
  room for additive per-id children later (e.g. the editable per-condition
  category list, §5.1 — additive *data*, not a freeze exception).
* **The remaining groups are empty containers** (``/calibration``, ``/traces``,
  ``/patches``, ``/idealization``, ``/settings``, ``/features``, ``/models``);
  their per-record payloads are additive data added when extraction / idealization
  / curation lands (M1+). ``/idealization/{model}`` will mirror the tMAVEN model
  schema (PRD Appendix D.2) as additive data.
* **Version 2 adds ``/generations/table``** (#921, ADR-0136), one row per trace
  generation of a movie. A file stamped 1 lacks it and is still complete
  (`_missing_skeleton` asks for a table only of the versions that declare it);
  [`upgrade_to_generations`][] adds it when a writer records the first generation.
* **Version 3 adds ``/sources/table``** (#912, ADR-0140), the source registry: one row
  per source file (a movie, a bead video, a map image) with its checksum, adapter and
  role, and for a derived file its original and the recipe between them. A file stamped
  1 or 2 lacks it and is still complete; [`upgrade_skeleton`][] adds every table a newer
  version declares when a writer first needs one.
* **The ``<file>.lock`` single-writer marker is a sidecar file**, part of the §5.4
  concurrency lifecycle (M2), not the in-HDF5 schema — it is not written here.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import h5py
import numpy as np

if TYPE_CHECKING:
    from collections.abc import Mapping

# --- The freeze stamp --------------------------------------------------------

#: Monotonic on-disk schema version. Bumped *only* by a deliberate structural
#: change (ADR + regenerated golden, PRD §12.6); the guard refuses a decrement.
#: Version 2 adds the ``/generations`` table of trace generations (#921, ADR-0136).
#: Version 3 adds the ``/sources`` table, the source registry (#912, ADR-0140).
SCHEMA_VERSION = 3

#: The first ``schema_version`` whose skeleton has the ``/generations`` table. A store
#: stamped older is still a complete project without it (it reads as generation 1 of
#: each movie, ADR-0136), so the skeleton check asks for it only from this version on.
GENERATIONS_SINCE = 2

#: The first ``schema_version`` whose skeleton has the ``/sources`` table (ADR-0140). An
#: older store reads as one source record for each of its movies, made from ``/movies``.
SOURCES_SINCE = 3

#: Root ``format`` identity attribute distinguishing a ``.tether`` project store.
FORMAT_TAG = "tether-project"

#: Name of the compound dataset inside each rich-entity group.
TABLE = "table"


def _str() -> np.dtype:
    """Variable-length UTF-8 string dtype (PRD §5; h5py ``string_dtype``)."""
    return h5py.string_dtype(encoding="utf-8")


# --- Frozen compound dtypes (PRD §5.1, Appendix D) ---------------------------
# Field order is part of the freeze; new fields are appended (additive) only.

#: ``/movies/table`` — per-movie source + the **metadata-only fast signature**
#: (``file_size`` + ``mtime`` + ``offline_flag``: zero byte reads, never hydrates
#: a OneDrive placeholder, §5.1/§5.4) + geometry/calibration reference.
MOVIES_DTYPE = np.dtype(
    [
        ("movie_id", _str()),
        ("uri", _str()),
        ("sha256", _str()),
        ("file_size", "<i8"),
        ("mtime", "<f8"),
        ("offline_flag", "i1"),
        ("n_frames", "<i4"),
        ("height", "<i4"),
        ("width", "<i4"),
        ("pixel_dtype", _str()),
        ("byteorder", _str()),
        ("frame_time", "<f8"),
        ("head_tail_hash", _str()),
        ("calibration_id", _str()),
        ("donor_crop", "<i4", (4,)),
        ("acceptor_crop", "<i4", (4,)),
        ("donor_rotation_deg", "<i4"),
        ("acceptor_rotation_deg", "<i4"),
        ("donor_flip", "i1", (2,)),
        ("acceptor_flip", "i1", (2,)),
    ]
)

#: ``/molecules/table`` — the stable-UUID ``molecule_id``, the cross-file
#: ``molecule_key`` (movie ``sha256`` + quantized ``donor_xy``, §7.10), coordinates,
#: window/bleach/corrections, the three *independent* label fields (``curation_label``
#: accept/reject, ``category`` editable per-condition value, ``quality_class``
#: read-only ML output, §7.5), and condition-key + provisional provenance fields.
MOLECULES_DTYPE = np.dtype(
    [
        ("molecule_id", _str()),
        ("molecule_key", _str()),
        ("movie_id", _str()),
        ("donor_xy", "<f8", (2,)),
        ("acceptor_xy", "<f8", (2,)),
        ("aperture_id", "<i4"),
        ("frame_range", "<i4", (2,)),
        ("analysis_window", "<i4", (2,)),
        ("bleach_frames", "<i4", (2,)),
        ("alpha", "<f8"),
        ("gamma", "<f8"),
        ("delta", "<f8"),
        ("correction_method", _str()),
        ("correction_confidence", "<f8"),
        ("curation_label", "<i4"),
        ("category", _str()),
        ("quality_class", "<f8"),
        ("condition_id", _str()),
        ("condition_id_provisional", _str()),
        ("source_filename", _str()),
        ("tags", _str()),
    ]
)

#: ``/labels/table`` — ML labels scoped per condition with full provenance: the
#: ``molecule_key`` join key, labeler identity, timestamp, source experiment file,
#: ``source`` ∈ {human, deeplasi-provisional, cross-condition-seed}, and the
#: effective training ``weight`` (recomputed per retrain, §5.1/§7.5). All frozen
#: now because adding label-provenance structure later is forbidden (§9 M0).
LABELS_DTYPE = np.dtype(
    [
        ("molecule_key", _str()),
        ("labeler", _str()),
        ("timestamp", _str()),
        ("source_file", _str()),
        ("source", _str()),
        ("weight", "<f8"),
        ("label_value", "<i4"),
        ("condition_id", _str()),
    ]
)

#: ``/conditions/table`` — the structured condition metadata. The condition
#: identity **key** = (construct/variant, dye, ligand + concentration, buffer,
#: temperature, laser power); ``date``/``replicate``/source deliberately vary
#: within a condition (§5.1). The per-condition leakage α + its donor-only-sample
#: provenance are stored here.
CONDITIONS_DTYPE = np.dtype(
    [
        ("condition_id", _str()),
        ("construct_variant", _str()),
        ("dye", _str()),
        ("ligand", _str()),
        ("ligand_concentration", "<f8"),
        ("ligand_concentration_unit", _str()),
        ("buffer", _str()),
        ("temperature_c", "<f8"),
        ("laser_power", "<f8"),
        ("date", _str()),
        ("replicate", _str()),
        ("leakage_alpha", "<f8"),
        ("leakage_alpha_source", _str()),
        ("tags", _str()),
    ]
)

#: ``/generations/table`` — one row per trace generation of a movie (#921, ADR-0136):
#: the movie, its per-movie ``generation`` number (1, 2, ...), a project-unique
#: ``generation_id``, the UTC time, the ``inputs`` that made it (JSON: source, geometry,
#: layout, map, colocalization rule, options), its molecule count, the ``molecule_key``
#: values carried from the previous generation (JSON list), the ``match_px`` that
#: decided "did not move" for them, and the app version. Append-only.
GENERATIONS_DTYPE = np.dtype(
    [
        ("movie_id", _str()),
        ("generation", "<i4"),
        ("generation_id", _str()),
        ("created_utc", _str()),
        ("inputs", _str()),
        ("n_molecules", "<i4"),
        ("carried_keys", _str()),
        ("match_px", "<f8"),
        ("app_version", _str()),
    ]
)

#: ``/sources/table`` — the source registry (#912, ADR-0140): one row per source file.
#: ``relative_path`` is read against the project's folder (``/`` separators; a file on
#: another drive keeps its absolute path), ``sha256`` is ``""`` until the checksum job has
#: read the file, ``role`` is ``movie``, ``bead_video`` or ``map_image``, ``acquisition``
#: is JSON, ``movie_id`` links a movie to its ``/movies`` row, and a derived file names
#: its original in ``derived_from`` and the recipe between them in ``recipe``.
SOURCES_DTYPE = np.dtype(
    [
        ("source_id", _str()),
        ("relative_path", _str()),
        ("original_name", _str()),
        ("file_size", "<i8"),
        ("sha256", _str()),
        ("adapter", _str()),
        ("adapter_version", _str()),
        ("role", _str()),
        ("acquisition", _str()),
        ("movie_id", _str()),
        ("derived_from", _str()),
        ("recipe", _str()),
        ("created_utc", _str()),
    ]
)

#: Rich entities: a group containing a 0-row compound ``table`` dataset.
_RICH_TABLES: dict[str, np.dtype] = {
    "movies": MOVIES_DTYPE,
    "molecules": MOLECULES_DTYPE,
    "conditions": CONDITIONS_DTYPE,
    "labels": LABELS_DTYPE,
    "generations": GENERATIONS_DTYPE,
    "sources": SOURCES_DTYPE,
}

#: The rich tables a store stamped before a given version does not have: name -> the
#: first ``schema_version`` that declares it.
_RICH_TABLES_SINCE: dict[str, int] = {
    "generations": GENERATIONS_SINCE,
    "sources": SOURCES_SINCE,
}

#: Empty container groups; their payloads are additive data (M1+).
_CONTAINER_GROUPS: tuple[str, ...] = (
    "calibration",
    "traces",
    "patches",
    "idealization",
    "settings",
    "features",
    "models",
)


def _app_version() -> str:
    """Best-effort Tether version for the ``.tether`` provenance stamp."""
    try:
        from tether import __version__

        return str(__version__)
    except Exception:  # pragma: no cover - defensive; version is normally present
        return "0.0.0+unknown"


def create_project(
    path: str | Path, *, overwrite: bool = False, stamp_app_version: bool = True
) -> Path:
    """Write a fresh ``.tether`` with the full frozen §5 skeleton.

    Parameters
    ----------
    path:
        Destination ``.tether`` (HDF5) file.
    overwrite:
        If ``False`` (default) refuse to clobber an existing file (mode ``w-``);
        if ``True`` truncate any existing file (mode ``w``).
    stamp_app_version:
        Stamp the Tether version into the root ``app_version`` attribute for
        provenance (NFR-REPRO). The value is intentionally excluded from the
        schema manifest (it is volatile, not structural).

    Returns
    -------
    pathlib.Path
        The path written.
    """
    path = Path(path)
    mode = "w" if overwrite else "w-"
    # track_order keeps groups/datasets/attrs in creation order on disk for
    # deterministic, diff-friendly project files (the manifest sorts regardless).
    with h5py.File(path, mode, track_order=True) as f:
        f.attrs["format"] = FORMAT_TAG
        f.attrs["schema_version"] = SCHEMA_VERSION
        if stamp_app_version:
            f.attrs["app_version"] = _app_version()
        for name, dtype in _RICH_TABLES.items():
            g = f.create_group(name, track_order=True)
            g.create_dataset(TABLE, shape=(0,), maxshape=(None,), dtype=dtype)
        for name in _CONTAINER_GROUPS:
            f.create_group(name, track_order=True)
    return path


def read_schema_version(path: str | Path) -> int:
    """Return the ``schema_version`` stamped in an existing ``.tether``."""
    with h5py.File(path, "r") as f:
        return int(f.attrs["schema_version"])


def upgrade_skeleton(f: h5py.File) -> None:
    """Give an open, writable store every table of [`SCHEMA_VERSION`][] that it lacks.

    Called by a writer that is about to record into a table a newer version added (the
    generations of ADR-0136, the source registry of ADR-0140). It creates each missing
    empty table and stamps the file with [`SCHEMA_VERSION`][], so an app that predates
    them refuses it rather than ignoring the records, and a file stamped 3 always has
    every table up to 3. No existing data is rewritten.
    """
    for name, dtype in _RICH_TABLES.items():
        if not isinstance(f.get(name), h5py.Group):
            f.create_group(name, track_order=True)
        group = f[name]
        if TABLE not in group:
            group.create_dataset(TABLE, shape=(0,), maxshape=(None,), dtype=dtype)
    if int(f.attrs["schema_version"]) < SCHEMA_VERSION:
        f.attrs["schema_version"] = SCHEMA_VERSION


def upgrade_to_generations(f: h5py.File) -> None:
    """Give an open, writable store its ``/generations`` table (ADR-0136).

    The same step as [`upgrade_skeleton`][]: the file is stamped [`SCHEMA_VERSION`][], so
    every newer table is added with it. A store that already has them is left as it is.
    """
    upgrade_skeleton(f)


def assert_compatible(file_version: int) -> None:
    """Refuse a file newer than this app's schema (PRD §5.4).

    Raises
    ------
    ValueError
        If ``file_version`` exceeds [`SCHEMA_VERSION`][].
    """
    if file_version > SCHEMA_VERSION:
        raise ValueError(
            f"file schema_version {file_version} is newer than this app's "
            f"{SCHEMA_VERSION}; refusing to open (PRD section 5.4)."
        )


def _missing_skeleton(f: h5py.File, version: int = SCHEMA_VERSION) -> list[str]:
    """Return the frozen top-level §5.1 skeleton paths absent from an open file.

    Validates only the *top-level* structure [`create_project`][] writes — the
    rich-entity groups + their 0-row ``table`` datasets and the empty container
    groups — using the same single-source-of-truth constants. (Deep field-level
    structure is the separate ``schema-guard`` gate's job, on the *writer*.) A table
    that a later ``schema_version`` added is required only of a file stamped with
    that version or newer, so an older store stays a complete project (ADR-0136).
    """
    missing: list[str] = []
    for name in _RICH_TABLES:
        if version < _RICH_TABLES_SINCE.get(name, 0):
            continue
        group = f.get(name)
        if not isinstance(group, h5py.Group):
            missing.append(f"/{name}")
        elif not isinstance(group.get(TABLE), h5py.Dataset):
            missing.append(f"/{name}/{TABLE}")
    for name in _CONTAINER_GROUPS:
        if not isinstance(f.get(name), h5py.Group):
            missing.append(f"/{name}")
    return missing


def assert_is_compatible_project(path: str | Path) -> int:
    """Validate that ``path`` is a readable, complete, compatible ``.tether`` store.

    Checks, in this order: the file is HDF5-readable; its root ``format`` attribute
    is the [`FORMAT_TAG`][] marker; a ``schema_version`` stamp is present; the
    frozen §5.1 top-level skeleton is present (so a foreign **or truncated** HDF5
    file is rejected, not silently accepted as a project); and that version is not
    newer than this app ([`assert_compatible`][], PRD §5.4). The stamp is checked
    **before** the skeleton, so a stamp-less file reports the missing stamp rather
    than the missing groups. Returns the on-disk ``schema_version``.

    Raises
    ------
    ValueError
        If the file is not readable HDF5, lacks the Tether ``format`` marker, is
        missing part of the frozen skeleton, or has no ``schema_version`` stamp.
    """
    try:
        with h5py.File(path, "r") as f:
            fmt = f.attrs.get("format")
            if isinstance(fmt, bytes):
                fmt = fmt.decode("utf-8")
            if fmt != FORMAT_TAG:
                raise ValueError(f"{path} is not a .tether project (format marker={fmt!r})")
            version = f.attrs.get("schema_version")
            if version is None:
                raise ValueError(f"{path} is missing the schema_version stamp")
            try:
                version = int(version)
            except (TypeError, ValueError):
                raise ValueError(f"{path} has an unreadable schema_version stamp") from None
            missing = _missing_skeleton(f, version)
            if missing:
                raise ValueError(
                    f"{path} is not a complete .tether project; missing: {', '.join(missing)}"
                )
    except OSError as exc:
        raise ValueError(f"{path} is not a readable .tether HDF5 project") from exc
    assert_compatible(version)
    return version


# --- Manifest (introspection) ------------------------------------------------


def _canonical_scalar(dt: np.dtype) -> str:
    """Platform-independent name for a leaf dtype (strings collapse to one tag)."""
    # Every object-kind field in this schema is an h5py variable-length string.
    if dt.kind == "O" or h5py.check_string_dtype(dt) is not None:
        return "str:utf-8"
    return f"{dt.kind}{dt.itemsize}"


def _canonical_dtype(dt: np.dtype) -> dict[str, Any]:
    """Canonicalize a dataset dtype (scalar / sub-array / compound) for the manifest."""
    if dt.names:  # compound
        fields = []
        for name in dt.names:
            field_dt = dt.fields[name][0]
            if field_dt.subdtype is not None:
                base, shape = field_dt.subdtype
            else:
                base, shape = field_dt, ()
            fields.append({"name": name, "dtype": _canonical_scalar(base), "shape": list(shape)})
        return {"kind": "compound", "fields": fields}
    if dt.subdtype is not None:
        base, shape = dt.subdtype
        return {"kind": "array", "dtype": _canonical_scalar(base), "shape": list(shape)}
    return {"kind": "scalar", "dtype": _canonical_scalar(dt)}


#: Root attributes whose *value* (not just dtype) is part of the freeze.
_VALUE_ATTRS = frozenset({"format", "schema_version"})


def _attrs_manifest(obj: h5py.Group | h5py.Dataset | h5py.File) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in obj.attrs:
        value = obj.attrs[key]
        entry: dict[str, Any] = {"dtype": _canonical_attr(value)}
        if key in _VALUE_ATTRS:
            entry["value"] = value.item() if hasattr(value, "item") else value
        out[key] = entry
    return out


def _canonical_attr(value: Any) -> str:
    if isinstance(value, bytes | str):
        return "str"
    if isinstance(value, bool | np.bool_):
        return "bool"
    if isinstance(value, int | np.integer):
        return "int"
    if isinstance(value, float | np.floating):
        return "float"
    return type(value).__name__


def introspect(f: h5py.File) -> dict[str, Any]:
    """Build the structural manifest of an open ``.tether`` file."""
    groups: list[str] = ["/"]
    datasets: dict[str, Any] = {}
    attrs: dict[str, Any] = {}

    root_attrs = _attrs_manifest(f)
    if root_attrs:
        attrs["/"] = root_attrs

    def visit(name: str, obj: h5py.Group | h5py.Dataset) -> None:
        path = "/" + name
        if isinstance(obj, h5py.Dataset):
            datasets[path] = {
                "dtype": _canonical_dtype(obj.dtype),
                "shape": list(obj.shape),
                "maxshape": list(obj.maxshape),
            }
        else:
            groups.append(path)
        obj_attrs = _attrs_manifest(obj)
        if obj_attrs:
            attrs[path] = obj_attrs

    f.visititems(visit)  # visits every object except the root group "/"

    return {
        "schema_version": int(f.attrs["schema_version"]),
        "format": str(f.attrs["format"]),
        "groups": sorted(set(groups)),
        "datasets": dict(sorted(datasets.items())),
        "attrs": dict(sorted(attrs.items())),
    }


def build_manifest() -> dict[str, Any]:
    """Create a throwaway ``.tether`` and return its structural manifest.

    This is the schema "the code declares" (PRD §12.6): it runs the very builder
    that writes a real project file, so the golden can never drift from reality.
    """
    import os
    import tempfile

    fd, tmp = tempfile.mkstemp(suffix=".tether")
    os.close(fd)
    try:
        create_project(tmp, overwrite=True)
        with h5py.File(tmp, "r") as f:
            return introspect(f)
    finally:
        os.unlink(tmp)


# --- Diff (the guard) --------------------------------------------------------


def _diff_compound(path: str, golden: Mapping[str, Any], current: Mapping[str, Any]) -> list[str]:
    """Field-level diff naming each offending frozen field (PRD §12.6)."""
    violations: list[str] = []
    g_fields = {fld["name"]: fld for fld in golden.get("fields", [])}
    c_fields = {fld["name"]: fld for fld in current.get("fields", [])}

    # A compound dtype's on-disk byte layout is positional, so field *order* is
    # part of the freeze: new fields may only be appended. A reorder or a
    # mid-list insertion changes the layout and breaks binary compatibility with
    # existing `.tether` files, so require the golden field sequence to remain an
    # exact prefix of the current one (additive tails are still allowed).
    g_order = [fld["name"] for fld in golden.get("fields", [])]
    c_order = [fld["name"] for fld in current.get("fields", [])]
    if c_order[: len(g_order)] != g_order:
        violations.append(
            f"{path}: frozen field order changed or a field was inserted out of "
            f"append-only position: {g_order} -> {c_order}"
        )

    for name, g_fld in g_fields.items():
        c_fld = c_fields.get(name)
        if c_fld is None:
            violations.append(f"{path}: frozen field removed or renamed: '{name}'")
            continue
        if g_fld.get("dtype") != c_fld.get("dtype"):
            violations.append(
                f"{path}: field '{name}' dtype changed "
                f"{g_fld.get('dtype')!r} -> {c_fld.get('dtype')!r}"
            )
        if g_fld.get("shape") != c_fld.get("shape"):
            violations.append(
                f"{path}: field '{name}' shape changed "
                f"{g_fld.get('shape')!r} -> {c_fld.get('shape')!r}"
            )
    return violations


def diff_manifest(golden: Mapping[str, Any], current: Mapping[str, Any]) -> list[str]:
    """Return freeze violations of ``current`` against the ``golden`` manifest.

    Additions (new group / dataset / attribute / field) are allowed and produce no
    violation. Removals, renames, dtype/shape/identity changes of a frozen item
    fail, as does a missing or decremented ``schema_version`` (PRD §12.6). An empty
    list means the freeze is intact.
    """
    violations: list[str] = []

    # --- version stamp: present + monotonic ---
    g_ver = golden.get("schema_version")
    c_ver = current.get("schema_version")
    if c_ver is None:
        violations.append("schema_version is missing (must be present and monotonic)")
    elif g_ver is not None:
        if c_ver < g_ver:
            violations.append(
                f"schema_version decreased {g_ver} -> {c_ver} (monotonic freeze violation)"
            )
        elif c_ver != g_ver:
            violations.append(
                f"schema_version changed {g_ver} -> {c_ver}: a deliberate bump must "
                "regenerate the golden manifest (and carry an ADR, PRD section 12.6)"
            )

    # --- format identity ---
    if golden.get("format") != current.get("format"):
        violations.append(
            f"root 'format' changed {golden.get('format')!r} -> {current.get('format')!r}"
        )

    # --- groups: none removed ---
    current_groups = set(current.get("groups", []))
    for grp in golden.get("groups", []):
        if grp not in current_groups:
            violations.append(f"frozen group removed: {grp}")

    # --- datasets: none removed; dtype/shape/maxshape stable ---
    current_ds = current.get("datasets", {})
    for path, g_ds in golden.get("datasets", {}).items():
        c_ds = current_ds.get(path)
        if c_ds is None:
            violations.append(f"frozen dataset removed: {path}")
            continue
        g_dt, c_dt = g_ds.get("dtype", {}), c_ds.get("dtype", {})
        if g_dt.get("kind") == "compound" and c_dt.get("kind") == "compound":
            violations.extend(_diff_compound(path, g_dt, c_dt))
        elif g_dt != c_dt:
            violations.append(f"frozen dataset {path}: dtype changed {g_dt!r} -> {c_dt!r}")
        if g_ds.get("maxshape") != c_ds.get("maxshape"):
            violations.append(
                f"frozen dataset {path}: maxshape changed "
                f"{g_ds.get('maxshape')!r} -> {c_ds.get('maxshape')!r}"
            )

    # --- attributes: none removed; dtype stable; frozen values stable ---
    current_attrs = current.get("attrs", {})
    for path, g_obj_attrs in golden.get("attrs", {}).items():
        c_obj_attrs = current_attrs.get(path, {})
        for name, g_attr in g_obj_attrs.items():
            c_attr = c_obj_attrs.get(name)
            if c_attr is None:
                violations.append(f"frozen attribute removed: {path}@{name}")
                continue
            if g_attr.get("dtype") != c_attr.get("dtype"):
                violations.append(
                    f"frozen attribute {path}@{name} dtype changed "
                    f"{g_attr.get('dtype')!r} -> {c_attr.get('dtype')!r}"
                )

    return violations
