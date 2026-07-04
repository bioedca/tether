# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for structured conditions + referential validation (M4 S1, PRD §5.1/§7.6).

Covers the first M4 gate (PLAN §8, FR-ANNOTATE): a condition aggregates molecules
across ≥ 2 files; near-miss strings stay separate (keep-separate-by-default); a
molecule's ``condition_id`` is valid only when it resolves to a ``/conditions`` row
built from that key (referential validation). All headless (no Qt); every write is
additive data under the M0-frozen schema.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("h5py")
pytest.importorskip("numpy")

import h5py  # noqa: E402
import numpy as np  # noqa: E402

from tether.io.filename import ConditionKey, parse_filename  # noqa: E402
from tether.io.schema import MOLECULES_DTYPE, TABLE, create_project  # noqa: E402
from tether.project import (
    Project,  # noqa: E402
    lock,  # noqa: E402
)
from tether.project import conditions as C  # noqa: E402
from tether.project.lock import LockedError, LockIdentity  # noqa: E402

HOST_A = LockIdentity(host="HOST-A", user="alice", pid=111)
HOST_B = LockIdentity(host="HOST-B", user="bob", pid=222)

# Two acquisitions of the *same* condition (differ only in the non-key video index
# + sample concentration): construct "Bla UCKOPSB T-box", ligand tRNA @ 600 nM.
_FILE_A10 = "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif"
_FILE_A11 = "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_011.tif"
# A near-miss of the same intent — "Tbox" vs "T-box" — a *different* construct
# string, so a different key and a separate condition (never auto-merged).
_FILE_NEARMISS = "Bla_UCKOPSB_Tbox_35pM_tRNA_600nM_010.tif"


def _seed_extracted(
    tmp_path: Path,
    specs: list[tuple[str, str]],
    *,
    name: str = "exp.tether",
) -> Path:
    """Create a ``.tether`` in the realistic post-extraction state.

    ``specs`` = ``(molecule_key, source_filename)``; ``condition_id`` is derived the
    way :mod:`tether.imaging.extract` derives it (``parse_filename(...).condition_id``),
    so each molecule is a *faithful witness* of its own condition. Mirrors
    ``test_labels._seed`` for the field zero-fill discipline.
    """
    path = create_project(tmp_path / name)
    rows = np.zeros(len(specs), dtype=MOLECULES_DTYPE)
    for field in MOLECULES_DTYPE.names:
        if MOLECULES_DTYPE[field].kind == "O":
            rows[field] = ""
    rows["molecule_id"] = [f"mol-{i}" for i in range(len(specs))]
    rows["molecule_key"] = [key for key, _ in specs]
    rows["source_filename"] = [src for _, src in specs]
    rows["condition_id"] = [parse_filename(src).condition_id for _, src in specs]
    with h5py.File(path, "r+") as f:
        table = f["molecules"][TABLE]
        table.resize((len(specs),))
        table[:] = rows
    return path


def _seed_raw(
    tmp_path: Path,
    specs: list[tuple[str, str, str]],
    *,
    name: str = "exp.tether",
) -> Path:
    """Create a ``.tether`` with explicit ``(molecule_key, source_filename, condition_id)``.

    Lets a test set a ``condition_id`` that *disagrees* with the filename parse (a
    drifted / hand-assigned id), which the post-extraction path never produces.
    """
    path = create_project(tmp_path / name)
    rows = np.zeros(len(specs), dtype=MOLECULES_DTYPE)
    for field in MOLECULES_DTYPE.names:
        if MOLECULES_DTYPE[field].kind == "O":
            rows[field] = ""
    rows["molecule_id"] = [f"mol-{i}" for i in range(len(specs))]
    rows["molecule_key"] = [key for key, _, _ in specs]
    rows["source_filename"] = [src for _, src, _ in specs]
    rows["condition_id"] = [cid for _, _, cid in specs]
    with h5py.File(path, "r+") as f:
        table = f["molecules"][TABLE]
        table.resize((len(specs),))
        table[:] = rows
    return path


# --- sync materializes conditions --------------------------------------------


def test_sync_materializes_one_row_per_distinct_key(tmp_path: Path) -> None:
    path = _seed_extracted(tmp_path, [("k0", _FILE_A10), ("k1", _FILE_A11)])
    summary = C.sync_conditions(path)

    expected_id = parse_filename(_FILE_A10).condition_id
    assert parse_filename(_FILE_A11).condition_id == expected_id  # same condition
    assert summary.n_molecules == 2
    assert summary.n_conditions == 1
    assert summary.created_ids == (expected_id,)
    assert summary.n_unresolved == 0

    rows = C.read_conditions(path)
    assert rows.shape[0] == 1
    assert C._to_str(rows["condition_id"][0]) == expected_id
    assert C._to_str(rows["construct_variant"][0]) == "Bla UCKOPSB T-box"
    assert C._to_str(rows["ligand"][0]) == "tRNA"
    assert float(rows["ligand_concentration"][0]) == pytest.approx(600.0)


def test_condition_aggregates_molecules_across_two_files(tmp_path: Path) -> None:
    """§9 M4: one condition aggregates molecules drawn from ≥ 2 distinct files."""
    path = _seed_extracted(tmp_path, [("k0", _FILE_A10), ("k1", _FILE_A11)])
    C.sync_conditions(path)

    agg = C.aggregate_molecules_by_condition(path)
    assert len(agg) == 1  # a single condition
    ((cid, keys),) = agg.items()
    assert sorted(keys) == ["k0", "k1"]  # both molecules aggregated under it

    # ...and they genuinely came from two different files.
    with h5py.File(path, "r") as f:
        srcs = {C._to_str(s) for s in f["molecules"][TABLE]["source_filename"][:]}
    assert srcs == {_FILE_A10, _FILE_A11}
    assert cid == parse_filename(_FILE_A10).condition_id


def test_near_miss_strings_stay_separate(tmp_path: Path) -> None:
    """§5.1 keep-separate-by-default: near-miss constructs are never auto-merged."""
    path = _seed_extracted(tmp_path, [("k0", _FILE_A10), ("k1", _FILE_NEARMISS)])
    summary = C.sync_conditions(path)

    id_a = parse_filename(_FILE_A10).condition_id
    id_near = parse_filename(_FILE_NEARMISS).condition_id
    assert id_a != id_near  # a different key → a different id

    assert summary.n_conditions == 2
    agg = C.aggregate_molecules_by_condition(path)
    assert set(agg) == {id_a, id_near}
    assert agg[id_a] == ["k0"]
    assert agg[id_near] == ["k1"]


def test_sync_is_idempotent(tmp_path: Path) -> None:
    path = _seed_extracted(tmp_path, [("k0", _FILE_A10)])
    first = C.sync_conditions(path)
    assert len(first.created_ids) == 1

    before = C.read_conditions(path)
    second = C.sync_conditions(path)
    after = C.read_conditions(path)

    assert second.created_ids == ()  # nothing new
    assert second.n_conditions == first.n_conditions
    assert after.shape[0] == before.shape[0] == 1
    # Existing row untouched (NaN-safe field compare: the empty numeric key fields
    # persist as the NaN sentinel, which array-equality would spuriously reject).
    assert C._to_str(before["condition_id"][0]) == C._to_str(after["condition_id"][0])
    assert C._row_to_key(before[0]) == C._row_to_key(after[0])


# --- referential validation ---------------------------------------------------


def test_validate_ok_after_sync(tmp_path: Path) -> None:
    path = _seed_extracted(tmp_path, [("k0", _FILE_A10), ("k1", _FILE_A11)])
    C.sync_conditions(path)

    report = C.validate_conditions(path)
    assert report.ok
    assert report.dangling == {}
    assert report.inconsistent == ()
    assert report.n_molecules == 2
    assert report.n_conditions == 1


def test_validate_flags_dangling_before_sync(tmp_path: Path) -> None:
    """Before conditions are materialized every reference dangles (§5.1)."""
    path = _seed_extracted(tmp_path, [("k0", _FILE_A10)])
    report = C.validate_conditions(path)

    assert not report.ok
    expected_id = parse_filename(_FILE_A10).condition_id
    assert report.dangling == {expected_id: ("k0",)}
    assert report.inconsistent == ()


def test_drifted_id_is_unresolved_and_dangling(tmp_path: Path) -> None:
    """A stored id no filename parses back to cannot be materialized (§5.1)."""
    bogus = "cond-deadbeef0000"
    path = _seed_raw(tmp_path, [("k0", _FILE_A10, bogus)])
    summary = C.sync_conditions(path)

    assert summary.created_ids == ()  # no faithful witness for the bogus id
    assert summary.n_unresolved == 1
    assert C.read_conditions(path).shape[0] == 0

    report = C.validate_conditions(path)
    assert report.dangling == {bogus: ("k0",)}
    assert not report.ok


def test_validate_flags_inconsistent_row(tmp_path: Path) -> None:
    """A /conditions row whose fields no longer hash to its id is inconsistent (§5.1)."""
    path = _seed_extracted(tmp_path, [("k0", _FILE_A10)])
    C.sync_conditions(path)
    cid = parse_filename(_FILE_A10).condition_id

    # Hand-edit a key field without re-keying (what the M4 re-key PR must prevent):
    # the row's fields now hash to a *different* id than its stored condition_id.
    with h5py.File(path, "r+") as f:
        table = f["conditions"][TABLE]
        row = table[0]
        row["construct_variant"] = "MUTATED CONSTRUCT"
        table[0] = row

    report = C.validate_conditions(path)
    assert report.inconsistent == (cid,)
    assert not report.ok


# --- register_condition (the low-level upsert) --------------------------------


def test_register_condition_is_idempotent_and_updates_provenance(tmp_path: Path) -> None:
    path = create_project(tmp_path / "exp.tether")
    key = parse_filename(_FILE_A10).key

    first = C.register_condition(path, key, tags="baseline")
    again = C.register_condition(path, key, tags="edited")  # same id → update in place

    rows = C.read_conditions(path)
    assert rows.shape[0] == 1  # one row, not two
    assert C._to_str(first["condition_id"]) == key.condition_id()
    assert C._to_str(again["tags"]) == "edited"
    assert C._to_str(rows["tags"][0]) == "edited"


@pytest.mark.parametrize(
    "key",
    [
        ConditionKey(construct_variant="C", dye="Cy3"),  # None numerics
        ConditionKey(
            construct_variant="WCBN",
            dye="Cy5",
            ligand="tRNA",
            ligand_concentration=600.0,
            ligand_concentration_unit="nM",
            buffer="T50",
            temperature_c=25.0,
            laser_power=2.5,
        ),
    ],
)
def test_register_row_roundtrips_to_the_same_key(tmp_path: Path, key: ConditionKey) -> None:
    """A stored row reconstructs to the same key + id (NaN↔None numeric round-trip)."""
    path = create_project(tmp_path / "exp.tether")
    C.register_condition(path, key)

    rows = C.read_conditions(path)
    assert rows.shape[0] == 1
    recovered = C._row_to_key(rows[0])
    assert recovered == key
    assert recovered.condition_id() == key.condition_id()
    assert C._to_str(rows["condition_id"][0]) == key.condition_id()


def test_read_conditions_empty_on_fresh_project(tmp_path: Path) -> None:
    path = create_project(tmp_path / "exp.tether")
    assert C.read_conditions(path).shape[0] == 0
    assert C.aggregate_molecules_by_condition(path) == {}
    assert C.validate_conditions(path).ok  # no molecules, no rows → trivially ok


# --- Project wrappers + the write-lock gate -----------------------------------


def test_project_wrappers_match_module(tmp_path: Path) -> None:
    path = _seed_extracted(tmp_path, [("k0", _FILE_A10), ("k1", _FILE_A11)])
    proj = Project.open(path)

    summary = proj.sync_conditions()
    assert summary.n_conditions == 1
    assert proj.read_conditions().shape[0] == 1
    assert proj.validate_conditions().ok
    assert proj.molecules_by_condition() == C.aggregate_molecules_by_condition(path)


def test_project_sync_conditions_refuses_foreign_lock(tmp_path: Path) -> None:
    """The write wrapper honors the single-writer lock (§5.4)."""
    path = _seed_extracted(tmp_path, [("k0", _FILE_A10)])
    lock.acquire(path, identity=HOST_B)
    proj = Project(path, identity=HOST_A)
    with pytest.raises(LockedError):
        proj.sync_conditions()
