# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The M0 HDF5 schema freeze (PRD §5, §9 M0, §12.6).

Covers the two halves of the keystone:

* **Round-trip** — a fresh ``.tether`` reopens with its version stamp and every
  M0-frozen field intact (``molecule_key`` on ``/molecules`` *and* ``/labels``,
  the ``/labels`` provenance fields, the ``/conditions`` identity key, the
  ``/movies`` metadata-only signature).
* **The guard** — the committed golden matches the code, and ``diff_manifest``
  fails a deliberately structure-breaking change while letting additions pass.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

pytest.importorskip("h5py")
pytest.importorskip("numpy")

import h5py  # noqa: E402  (guarded by importorskip above)

from tether.io import schema  # noqa: E402

GOLDEN_PATH = Path(__file__).resolve().parent.parent / "schema" / "schema_frozen.json"

ALL_GROUPS = (
    "movies",
    "calibration",
    "molecules",
    "traces",
    "patches",
    "idealization",
    "conditions",
    "settings",
    "features",
    "labels",
    "models",
)


def _field_names(dataset: h5py.Dataset) -> set[str]:
    return set(dataset.dtype.names or ())


# --- create + round-trip -----------------------------------------------------


def test_create_project_writes_full_skeleton(tmp_path: Path) -> None:
    path = schema.create_project(tmp_path / "exp.tether")
    with h5py.File(path, "r") as f:
        for grp in ALL_GROUPS:
            assert grp in f, f"missing frozen group: /{grp}"
        assert f.attrs["format"] == schema.FORMAT_TAG
        assert int(f.attrs["schema_version"]) == schema.SCHEMA_VERSION


def test_round_trip_preserves_version_and_frozen_fields(tmp_path: Path) -> None:
    path = schema.create_project(tmp_path / "exp.tether")
    # Reopen — the version stamp and every frozen field survive the round-trip.
    assert schema.read_schema_version(path) == schema.SCHEMA_VERSION
    with h5py.File(path, "r") as f:
        mol = _field_names(f["molecules/table"])
        lab = _field_names(f["labels/table"])
        cond = _field_names(f["conditions/table"])
        mov = _field_names(f["movies/table"])

    # molecule_key is the cross-file join key on BOTH tables (§5.1, §9 M0).
    assert "molecule_key" in mol
    assert "molecule_key" in lab
    # stable-UUID molecule_id + condition-key reference + provisional provenance.
    assert {"molecule_id", "condition_id", "condition_id_provisional", "source_filename"} <= mol
    # the three independent label fields (§7.5).
    assert {"curation_label", "category", "quality_class"} <= mol
    # /labels provenance (§5.1): labeler / timestamp / source / weight.
    assert {"labeler", "timestamp", "source", "weight"} <= lab
    # /conditions identity key (§5.1): construct/variant, dye, ligand+conc, buffer, temp, laser.
    assert {
        "construct_variant",
        "dye",
        "ligand",
        "ligand_concentration",
        "buffer",
        "temperature_c",
        "laser_power",
    } <= cond
    # /movies metadata-only fast signature (§5.1): size + mtime + offline flag.
    assert {"file_size", "mtime", "offline_flag"} <= mov


def test_create_project_refuses_clobber(tmp_path: Path) -> None:
    target = tmp_path / "exp.tether"
    schema.create_project(target)
    with pytest.raises((OSError, ValueError)):
        schema.create_project(target)  # overwrite=False -> mode "w-"
    # overwrite=True succeeds.
    assert schema.create_project(target, overwrite=True) == target


def test_assert_compatible_refuses_newer_file() -> None:
    schema.assert_compatible(schema.SCHEMA_VERSION)  # equal: OK
    schema.assert_compatible(schema.SCHEMA_VERSION - 1)  # older: OK
    with pytest.raises(ValueError, match="newer"):
        schema.assert_compatible(schema.SCHEMA_VERSION + 1)


# --- the guard ---------------------------------------------------------------


def _golden() -> dict:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def test_committed_golden_matches_code() -> None:
    """The committed golden must equal what the builder declares (no drift)."""
    assert schema.diff_manifest(_golden(), schema.build_manifest()) == []


def test_golden_carries_required_frozen_fields() -> None:
    golden = _golden()
    assert golden["schema_version"] == schema.SCHEMA_VERSION
    assert golden["format"] == schema.FORMAT_TAG

    def names(path: str) -> set[str]:
        return {fld["name"] for fld in golden["datasets"][path]["dtype"]["fields"]}

    assert "molecule_key" in names("/molecules/table")
    assert "molecule_key" in names("/labels/table")
    assert {"file_size", "mtime", "offline_flag"} <= names("/movies/table")


def test_guard_flags_removed_field() -> None:
    golden = schema.build_manifest()
    current = copy.deepcopy(golden)
    fields = current["datasets"]["/molecules/table"]["dtype"]["fields"]
    current["datasets"]["/molecules/table"]["dtype"]["fields"] = [
        f for f in fields if f["name"] != "molecule_key"
    ]
    violations = schema.diff_manifest(golden, current)
    assert any("molecule_key" in v for v in violations)


def test_guard_flags_dtype_change() -> None:
    golden = schema.build_manifest()
    current = copy.deepcopy(golden)
    for fld in current["datasets"]["/labels/table"]["dtype"]["fields"]:
        if fld["name"] == "weight":
            fld["dtype"] = "i4"  # f8 -> i4
    violations = schema.diff_manifest(golden, current)
    assert any("weight" in v and "dtype" in v for v in violations)


def test_guard_flags_reordered_field() -> None:
    """A compound dtype's byte layout is positional — a reorder must fail."""
    golden = schema.build_manifest()
    current = copy.deepcopy(golden)
    fields = current["datasets"]["/molecules/table"]["dtype"]["fields"]
    fields[0], fields[1] = fields[1], fields[0]  # swap molecule_id <-> molecule_key
    violations = schema.diff_manifest(golden, current)
    assert any("append-only" in v or "order" in v for v in violations)


def test_guard_flags_mid_insertion() -> None:
    """A field inserted in the middle (not appended) breaks the frozen layout."""
    golden = schema.build_manifest()
    current = copy.deepcopy(golden)
    fields = current["datasets"]["/labels/table"]["dtype"]["fields"]
    fields.insert(1, {"name": "inserted_in_middle", "dtype": "f8", "shape": []})
    violations = schema.diff_manifest(golden, current)
    assert any("append-only" in v or "order" in v for v in violations)


def test_guard_flags_removed_group() -> None:
    golden = schema.build_manifest()
    current = copy.deepcopy(golden)
    current["groups"] = [g for g in current["groups"] if g != "/idealization"]
    violations = schema.diff_manifest(golden, current)
    assert any("/idealization" in v for v in violations)


def test_guard_flags_version_decrement() -> None:
    golden = schema.build_manifest()
    current = copy.deepcopy(golden)
    current["schema_version"] = golden["schema_version"] - 1
    violations = schema.diff_manifest(golden, current)
    assert any("decreased" in v for v in violations)


def test_guard_allows_additions() -> None:
    """Additive changes — a new group, dataset, and field — never fail the freeze."""
    golden = schema.build_manifest()
    current = copy.deepcopy(golden)
    current["groups"].append("/molecules/extra")
    current["datasets"]["/molecules/extra/data"] = {
        "dtype": {"kind": "scalar", "dtype": "f8"},
        "shape": [0],
        "maxshape": [None],
    }
    current["datasets"]["/molecules/table"]["dtype"]["fields"].append(
        {"name": "new_additive_field", "dtype": "f8", "shape": []}
    )
    assert schema.diff_manifest(golden, current) == []
