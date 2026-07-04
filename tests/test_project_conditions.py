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


# --- transactional re-key + human-confirmed merge (M4 PR-2, §9 M4) ------------

_BOGUS = "cond-deadbeef0000"  # a drifted/hand-assigned id no filename parses back to


def _condition_id_of(path: Path, molecule_key: str) -> str:
    """The ``condition_id`` currently stored on ``molecule_key`` (test helper)."""
    with h5py.File(path, "r") as f:
        table = f["molecules"][TABLE]
        keys = [C._to_str(k) for k in table["molecule_key"][:]]
        cids = [C._to_str(c) for c in table["condition_id"][:]]
    return cids[keys.index(molecule_key)]


def test_rekey_corrects_a_mis_parsed_id_transactionally(tmp_path: Path) -> None:
    """§9 M4: a mis-parsed id re-keys all affected molecules with an audit entry.

    A molecule stored with a drifted id (``_BOGUS``) but whose filename parses to the
    real key: re-keying to that key moves it, materializes its ``/conditions`` row, and
    clears the dangling reference — validated end to end.
    """
    path = _seed_raw(tmp_path, [("k0", _FILE_A10, _BOGUS)])
    correct_key = parse_filename(_FILE_A10).key
    correct_id = correct_key.condition_id()

    # Before: the drifted reference dangles (no /conditions row built from it).
    assert C.validate_conditions(path).dangling == {_BOGUS: ("k0",)}

    result = C.rekey_condition(path, _BOGUS, correct_key, reason="filename mis-parse")

    assert result.from_condition_id == _BOGUS
    assert result.to_condition_id == correct_id
    assert result.n_molecules == 1
    assert result.is_merge is False
    assert result.event == "rekey"
    # The molecule moved, its destination /conditions row exists, and it resolves now.
    assert _condition_id_of(path, "k0") == correct_id
    ids = {C._to_str(c) for c in C.read_conditions(path)["condition_id"]}
    assert correct_id in ids
    assert C.validate_conditions(path).ok

    audit = C.read_condition_audit(path)
    assert audit.shape[0] == 1
    assert C._to_str(audit["event"][0]) == "rekey"
    assert C._to_str(audit["from_condition_id"][0]) == _BOGUS
    assert C._to_str(audit["to_condition_id"][0]) == correct_id
    assert int(audit["n_molecules"][0]) == 1
    assert C._to_str(audit["reason"][0]) == "filename mis-parse"
    assert C._to_str(audit["labeler"][0])  # a non-empty default labeler
    assert C._to_str(audit["timestamp"][0])  # a non-empty stamp
    assert C._to_str(audit["app_version"][0])  # provenance stamp present


def test_rekey_moves_all_affected_together_and_leaves_others(tmp_path: Path) -> None:
    """The re-key is all-or-nothing over the affected set; unrelated molecules untouched."""
    # The near-miss "Tbox" parses to a *different* key/id than "T-box", so k3 is a
    # genuinely separate condition (an empty T-box destination → a plain correction).
    other_id = parse_filename(_FILE_NEARMISS).condition_id
    path = _seed_raw(
        tmp_path,
        [
            ("k0", _FILE_A10, _BOGUS),
            ("k1", _FILE_A10, _BOGUS),
            ("k2", _FILE_A10, _BOGUS),
            ("k3", _FILE_NEARMISS, other_id),  # a different, self-consistent condition
        ],
    )
    correct_key = parse_filename(_FILE_A10).key
    correct_id = correct_key.condition_id()

    result = C.rekey_condition(path, _BOGUS, correct_key)

    assert result.n_molecules == 3
    for mk in ("k0", "k1", "k2"):
        assert _condition_id_of(path, mk) == correct_id
    assert _condition_id_of(path, "k3") == other_id  # the unrelated molecule is untouched

    # The full-table write round-trips every other field (molecule_key preserved).
    with h5py.File(path, "r") as f:
        keys = {C._to_str(k) for k in f["molecules"][TABLE]["molecule_key"][:]}
    assert keys == {"k0", "k1", "k2", "k3"}

    audit = C.read_condition_audit(path)
    assert audit.shape[0] == 1  # one event, not one-per-molecule
    assert int(audit["n_molecules"][0]) == 3


def test_merge_requires_confirmation(tmp_path: Path) -> None:
    """§9 M4: collapsing two conditions into one is human-confirmed (never silent, §5.1).

    A near-miss "Tbox" molecule (its own condition) re-keyed into the real "T-box"
    condition (already holding two molecules) is a *merge* — refused without confirm,
    committed with it (audit event ``"merge"``).
    """
    path = _seed_extracted(
        tmp_path,
        [("k0", _FILE_A10), ("k1", _FILE_A11), ("k2", _FILE_NEARMISS)],
    )
    C.sync_conditions(path)  # → two conditions: T-box (k0,k1) and near-miss Tbox (k2)

    near_id = parse_filename(_FILE_NEARMISS).condition_id
    tbox_key = parse_filename(_FILE_A10).key
    tbox_id = tbox_key.condition_id()
    assert near_id != tbox_id

    # Without confirm the merge is refused and nothing changes.
    with pytest.raises(C.ConfirmationRequired):
        C.rekey_condition(path, near_id, tbox_key)
    assert _condition_id_of(path, "k2") == near_id  # untouched
    assert C.read_condition_audit(path).shape[0] == 0  # no event logged

    # With confirm the near-miss molecule joins the T-box condition.
    result = C.rekey_condition(path, near_id, tbox_key, confirm=True)
    assert result.is_merge is True
    assert result.event == "merge"
    assert result.n_molecules == 1
    assert _condition_id_of(path, "k2") == tbox_id
    assert sorted(C.aggregate_molecules_by_condition(path)[tbox_id]) == ["k0", "k1", "k2"]
    # The now-empty near-miss /conditions row is a valid orphan — still self-consistent.
    assert C.validate_conditions(path).ok
    assert C._to_str(C.read_condition_audit(path)["event"][0]) == "merge"


def test_preview_rekey_is_read_only_and_reports_merge(tmp_path: Path) -> None:
    """``preview_rekey`` describes the effect (incl. ``is_merge``) without mutating."""
    path = _seed_extracted(
        tmp_path,
        [("k0", _FILE_A10), ("k1", _FILE_A11), ("k2", _FILE_NEARMISS)],
    )
    C.sync_conditions(path)
    near_id = parse_filename(_FILE_NEARMISS).condition_id
    tbox_key = parse_filename(_FILE_A10).key

    before_mol = _condition_id_of(path, "k2")
    before_cond = C.read_conditions(path).shape[0]

    preview = C.preview_rekey(path, near_id, tbox_key)
    assert preview.from_condition_id == near_id
    assert preview.to_condition_id == tbox_key.condition_id()
    assert preview.molecule_keys == ("k2",)
    assert preview.n_molecules == 1
    assert preview.is_merge is True
    assert sorted(preview.destination_molecule_keys) == ["k0", "k1"]

    # Read-only: nothing was written (no audit, no moved molecule, no new row).
    assert _condition_id_of(path, "k2") == before_mol
    assert C.read_conditions(path).shape[0] == before_cond
    assert C.read_condition_audit(path).shape[0] == 0


def test_preview_plain_correction_not_a_merge(tmp_path: Path) -> None:
    """A correction into an empty destination is not a merge (no confirm needed)."""
    path = _seed_raw(tmp_path, [("k0", _FILE_A10, _BOGUS)])
    correct_key = parse_filename(_FILE_A10).key

    preview = C.preview_rekey(path, _BOGUS, correct_key)
    assert preview.is_merge is False
    assert preview.destination_molecule_keys == ()
    assert preview.molecule_keys == ("k0",)


def test_rekey_to_same_id_rejected(tmp_path: Path) -> None:
    path = _seed_extracted(tmp_path, [("k0", _FILE_A10)])
    key = parse_filename(_FILE_A10).key
    with pytest.raises(ValueError, match="nothing to re-key"):
        C.rekey_condition(path, key.condition_id(), key)


def test_rekey_absent_source_id_raises(tmp_path: Path) -> None:
    """Re-keying an id no molecule carries is never a silent no-op."""
    path = _seed_extracted(tmp_path, [("k0", _FILE_A10)])
    key = parse_filename(_FILE_NEARMISS).key
    with pytest.raises(KeyError):
        C.rekey_condition(path, "cond-nonexistent0", key)


def test_rekey_empty_source_id_raises(tmp_path: Path) -> None:
    path = _seed_extracted(tmp_path, [("k0", _FILE_A10)])
    with pytest.raises(ValueError, match="non-empty"):
        C.rekey_condition(path, "", parse_filename(_FILE_A10).key)


def test_rekey_rejects_naive_timestamp(tmp_path: Path) -> None:
    """A caller-supplied timestamp must be offset-aware before it enters the audit log."""
    path = _seed_raw(tmp_path, [("k0", _FILE_A10, _BOGUS)])
    key = parse_filename(_FILE_A10).key
    with pytest.raises(ValueError, match="offset"):
        C.rekey_condition(path, _BOGUS, key, timestamp="2026-07-04T12:00:00")
    # Nothing was written despite the failed call.
    assert C.read_condition_audit(path).shape[0] == 0
    assert _condition_id_of(path, "k0") == _BOGUS


def test_condition_audit_is_append_only_across_rekeys(tmp_path: Path) -> None:
    """Successive re-keys append in order; earlier events are never rewritten."""
    other_id = parse_filename(_FILE_NEARMISS).condition_id
    path = _seed_raw(
        tmp_path,
        [("k0", _FILE_A10, _BOGUS), ("k1", _FILE_NEARMISS, other_id)],
    )
    a10_key = parse_filename(_FILE_A10).key
    a11_key = parse_filename(_FILE_A11).key  # same key as A10 → same id (T-box)

    C.rekey_condition(path, _BOGUS, a10_key)  # k0 → T-box (event 0)
    # k1 is on the near-miss id; re-key it to T-box too (a merge, event 1).
    C.rekey_condition(path, other_id, a11_key, confirm=True)

    audit = C.read_condition_audit(path)
    assert audit.shape[0] == 2
    assert C._to_str(audit["from_condition_id"][0]) == _BOGUS
    assert C._to_str(audit["event"][0]) == "rekey"
    assert C._to_str(audit["from_condition_id"][1]) == other_id
    assert C._to_str(audit["event"][1]) == "merge"


def test_read_condition_audit_empty_before_any_rekey(tmp_path: Path) -> None:
    path = _seed_extracted(tmp_path, [("k0", _FILE_A10)])
    audit = C.read_condition_audit(path)
    assert audit.shape[0] == 0
    assert "event" in audit.dtype.names  # typed empty, not an untyped array


def test_project_rekey_wrappers_match_module(tmp_path: Path) -> None:
    path = _seed_raw(tmp_path, [("k0", _FILE_A10, _BOGUS)])
    correct_key = parse_filename(_FILE_A10).key
    proj = Project.open(path)

    preview = proj.preview_rekey(_BOGUS, correct_key)
    assert preview.molecule_keys == ("k0",)
    assert preview.is_merge is False

    result = proj.rekey_condition(_BOGUS, correct_key, reason="via wrapper")
    assert result.to_condition_id == correct_key.condition_id()
    assert proj.read_condition_audit().shape[0] == 1
    assert proj.validate_conditions().ok


def test_project_rekey_refuses_foreign_lock(tmp_path: Path) -> None:
    """The re-key write wrapper honors the single-writer lock (§5.4)."""
    path = _seed_raw(tmp_path, [("k0", _FILE_A10, _BOGUS)])
    lock.acquire(path, identity=HOST_B)
    proj = Project(path, identity=HOST_A)
    with pytest.raises(LockedError):
        proj.rekey_condition(_BOGUS, parse_filename(_FILE_A10).key)
