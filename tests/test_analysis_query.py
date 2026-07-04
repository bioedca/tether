# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the cross-movie condition query/filter (M4, PRD §5.1/§7.7; FR-ANNOTATE).

Covers the M4 query gate (PLAN §8): a query aggregates the right molecule set
across a condition's many files; filtering by key fields / category / tags (ANDed);
unconditioned molecules are never returned; key filtering only sees materialized
conditions and rejects an unknown field; empty filters are inert; and the
:func:`~tether.project.conditions.read_condition_keys` primitive behind key
filtering. All headless (no Qt); the store is seeded as post-extraction data under
the M0-frozen schema.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("h5py")
pytest.importorskip("numpy")

import h5py  # noqa: E402
import numpy as np  # noqa: E402

from tether.analysis import (  # noqa: E402
    ConditionQueryResult,
    MoleculeMatch,
    query_molecules,
)
from tether.io.filename import parse_filename  # noqa: E402
from tether.io.schema import MOLECULES_DTYPE, TABLE, create_project  # noqa: E402
from tether.project import Project  # noqa: E402
from tether.project import conditions as C  # noqa: E402

# Two acquisitions of the *same* condition (differ only in the non-key video index):
# construct "Bla UCKOPSB T-box", ligand tRNA @ 600 nM.
_FILE_A10 = "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif"
_FILE_A11 = "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_011.tif"
# A near-miss ("Tbox" vs "T-box") — a different construct string, so a *separate*
# condition (same ligand tRNA, so ligand-only key filters still span both).
_FILE_NEARMISS = "Bla_UCKOPSB_Tbox_35pM_tRNA_600nM_010.tif"

_KEY_A = parse_filename(_FILE_A10).key
_CID_A = parse_filename(_FILE_A10).condition_id
_CID_NM = parse_filename(_FILE_NEARMISS).condition_id


def _seed(tmp_path: Path, specs: list[dict[str, Any]], *, name: str = "exp.tether") -> Path:
    """Create a ``.tether`` in the post-extraction state from molecule ``specs``.

    Each spec: ``key`` (molecule_key), ``src`` (source_filename); optional ``movie``
    (default = ``src``), ``category`` (default ``""``), ``tags`` (tuple, comma-joined
    like extraction, default ``()``), ``condition_id`` (default: derived from ``src``
    the way :mod:`tether.imaging.extract` derives it, so each molecule is a faithful
    witness of its own condition). Mirrors ``test_project_conditions._seed_extracted``.
    """
    path = create_project(tmp_path / name)
    rows = np.zeros(len(specs), dtype=MOLECULES_DTYPE)
    for field in MOLECULES_DTYPE.names:
        if MOLECULES_DTYPE[field].kind == "O":
            rows[field] = ""
    for i, spec in enumerate(specs):
        src = spec["src"]
        rows["molecule_id"][i] = f"mol-{i}"
        rows["molecule_key"][i] = spec["key"]
        rows["source_filename"][i] = src
        rows["movie_id"][i] = spec.get("movie", src)
        rows["category"][i] = spec.get("category", "")
        rows["tags"][i] = ",".join(spec.get("tags", ()))
        cid = spec.get("condition_id")
        rows["condition_id"][i] = parse_filename(src).condition_id if cid is None else cid
    with h5py.File(path, "r+") as f:
        table = f["molecules"][TABLE]
        table.resize((len(specs),))
        table[:] = rows
    return path


def _cross_file_project(tmp_path: Path) -> Path:
    """Condition A across two files (k0,k1 in movie m10; k2 in m11) + a near-miss (k3)."""
    return _seed(
        tmp_path,
        [
            {"key": "k0", "src": _FILE_A10, "movie": "m10"},
            {"key": "k1", "src": _FILE_A10, "movie": "m10"},
            {"key": "k2", "src": _FILE_A11, "movie": "m11"},
            {"key": "k3", "src": _FILE_NEARMISS, "movie": "mNM"},
        ],
    )


# --- the §9 M4 gate: aggregate a condition's molecules across its files --------


def test_query_aggregates_condition_molecule_set_across_files(tmp_path: Path) -> None:
    path = _cross_file_project(tmp_path)
    # A10 and A11 are the same condition; the near-miss is a different one.
    assert parse_filename(_FILE_A11).condition_id == _CID_A
    assert _CID_NM != _CID_A

    result = query_molecules(path, condition_ids=[_CID_A])

    assert isinstance(result, ConditionQueryResult)
    assert set(result.molecule_keys) == {"k0", "k1", "k2"}  # spans two files
    assert result.n_conditions == 1
    assert result.n_files == 2
    assert set(result.source_filenames) == {_FILE_A10, _FILE_A11}
    assert result.by_source_file() == {_FILE_A10: ("k0", "k1"), _FILE_A11: ("k2",)}
    assert result.by_condition() == {_CID_A: ("k0", "k1", "k2")}
    assert "k3" not in result.molecule_keys  # the near-miss condition is excluded


# --- key-field filtering ------------------------------------------------------


def test_key_field_selects_one_condition_across_its_files(tmp_path: Path) -> None:
    path = _cross_file_project(tmp_path)
    C.sync_conditions(path)  # key filtering needs materialized /conditions rows

    result = query_molecules(path, key={"construct_variant": _KEY_A.construct_variant})
    assert set(result.molecule_keys) == {"k0", "k1", "k2"}
    assert result.n_files == 2

    near = query_molecules(path, key={"construct_variant": "Bla UCKOPSB Tbox"})
    assert set(near.molecule_keys) == {"k3"}


def test_key_field_can_span_multiple_conditions(tmp_path: Path) -> None:
    path = _cross_file_project(tmp_path)
    C.sync_conditions(path)

    # Both conditions share ligand tRNA -> the query spans both, across all 3 files.
    result = query_molecules(path, key={"ligand": _KEY_A.ligand})
    assert set(result.molecule_keys) == {"k0", "k1", "k2", "k3"}
    assert result.n_conditions == 2
    assert result.n_files == 3


def test_key_field_numeric_value_matches_int_float_and_none(tmp_path: Path) -> None:
    path = _cross_file_project(tmp_path)
    C.sync_conditions(path)

    # Stored ligand_concentration is 600.0; an int or float query matches it.
    assert query_molecules(path, key={"ligand_concentration": 600}).n_matches == 4
    assert query_molecules(path, key={"ligand_concentration": 600.0}).n_matches == 4
    assert query_molecules(path, key={"ligand_concentration": 999}).n_matches == 0
    # Temperature is absent (None) on every condition -> None matches, a value does not.
    assert query_molecules(path, key={"temperature_c": None}).n_matches == 4
    assert query_molecules(path, key={"temperature_c": 25.0}).n_matches == 0


def test_key_filtering_requires_materialized_conditions(tmp_path: Path) -> None:
    path = _cross_file_project(tmp_path)  # no sync_conditions -> /conditions is empty

    # A key filter can only match materialized conditions, so it returns nothing...
    assert query_molecules(path, key={"ligand": _KEY_A.ligand}).n_matches == 0
    # ...until the rows are materialized.
    C.sync_conditions(path)
    assert query_molecules(path, key={"ligand": _KEY_A.ligand}).n_matches == 4


def test_unknown_key_field_raises(tmp_path: Path) -> None:
    path = _cross_file_project(tmp_path)
    with pytest.raises(ValueError, match="unknown condition key field"):
        query_molecules(path, key={"not_a_field": 1})


# --- category & tag filtering -------------------------------------------------


def _annotated_project(tmp_path: Path) -> Path:
    """Condition A with per-molecule categories + tags for the annotation filters."""
    return _seed(
        tmp_path,
        [
            {"key": "k0", "src": _FILE_A10, "category": "docked", "tags": ("low-conf",)},
            {"key": "k1", "src": _FILE_A10, "category": "free", "tags": ("low-conf", "blink")},
            {"key": "k2", "src": _FILE_A11, "category": "docked", "tags": ("blink",)},
            {"key": "k3", "src": _FILE_A11, "category": "", "tags": ()},
        ],
    )


def test_category_filter(tmp_path: Path) -> None:
    path = _annotated_project(tmp_path)
    assert set(query_molecules(path, categories=["docked"]).molecule_keys) == {"k0", "k2"}
    assert set(query_molecules(path, categories=["docked", "free"]).molecule_keys) == {
        "k0",
        "k1",
        "k2",
    }
    assert query_molecules(path, categories=["absent"]).n_matches == 0


def test_tag_filter_all_of_and_any_of(tmp_path: Path) -> None:
    path = _annotated_project(tmp_path)
    assert set(query_molecules(path, tags=["low-conf"]).molecule_keys) == {"k0", "k1"}
    # all-of (default): only the molecule carrying *both* tags.
    assert set(query_molecules(path, tags=["low-conf", "blink"]).molecule_keys) == {"k1"}
    # any-of: either tag.
    any_hit = query_molecules(path, tags=["low-conf", "blink"], match_all_tags=False)
    assert set(any_hit.molecule_keys) == {"k0", "k1", "k2"}


def test_match_splits_stored_tags(tmp_path: Path) -> None:
    path = _annotated_project(tmp_path)
    result = query_molecules(path, condition_ids=[_CID_A])
    by_key = {m.molecule_key: m for m in result.matches}
    assert isinstance(by_key["k1"], MoleculeMatch)
    assert by_key["k1"].tags == ("low-conf", "blink")
    assert by_key["k3"].tags == ()
    assert by_key["k0"].category == "docked"


def test_filters_are_anded(tmp_path: Path) -> None:
    path = _annotated_project(tmp_path)
    # condition A ∧ category docked -> k0 (A10) and k2 (A11), not the free/empty ones.
    result = query_molecules(path, condition_ids=[_CID_A], categories=["docked"])
    assert set(result.molecule_keys) == {"k0", "k2"}


# --- unconditioned molecules, empty filters, and edge cases -------------------


def test_unconditioned_molecules_never_returned(tmp_path: Path) -> None:
    path = _seed(
        tmp_path,
        [
            {"key": "k0", "src": _FILE_A10, "tags": ("blink",)},
            {"key": "orphan", "src": "unparsed.tif", "condition_id": "", "tags": ("blink",)},
        ],
    )
    # No filter: the empty-condition molecule is excluded.
    assert set(query_molecules(path).molecule_keys) == {"k0"}
    # Even a tag filter it would match cannot surface it (condition-centric query).
    assert set(query_molecules(path, tags=["blink"]).molecule_keys) == {"k0"}


def test_empty_filters_are_inert(tmp_path: Path) -> None:
    path = _cross_file_project(tmp_path)  # unsynced on purpose
    everything = set(query_molecules(path).molecule_keys)
    assert everything == {"k0", "k1", "k2", "k3"}
    # None/empty collections are no-ops, not match-nothing...
    assert set(query_molecules(path, tags=[]).molecule_keys) == everything
    assert set(query_molecules(path, categories=[]).molecule_keys) == everything
    assert set(query_molecules(path, condition_ids=[]).molecule_keys) == everything
    # ...including an empty *generator* (truthy, but yields nothing): it must still
    # deactivate the filter, not flip to a match-nothing set — even in the tag
    # any-of branch, where an empty active set would otherwise match zero molecules.
    assert (
        set(query_molecules(path, tags=(t for t in []), match_all_tags=False).molecule_keys)
        == everything
    )
    assert set(query_molecules(path, condition_ids=(c for c in [])).molecule_keys) == everything
    assert set(query_molecules(path, categories=(c for c in [])).molecule_keys) == everything
    # ...and an empty key mapping skips the key branch entirely (so it does NOT
    # require materialized conditions the way a real key constraint does).
    assert set(query_molecules(path, key={}).molecule_keys) == everything


def test_accepts_project_handle_and_path(tmp_path: Path) -> None:
    path = _cross_file_project(tmp_path)
    from_path = query_molecules(path, condition_ids=[_CID_A]).molecule_keys
    from_handle = query_molecules(Project.open(path), condition_ids=[_CID_A]).molecule_keys
    assert from_path == from_handle == ("k0", "k1", "k2")


def test_empty_project_yields_empty_result(tmp_path: Path) -> None:
    path = create_project(tmp_path / "empty.tether")
    result = query_molecules(path)
    assert result.n_matches == 0
    assert result.n_files == 0
    assert result.by_condition() == {}


def test_rollups_and_store_order(tmp_path: Path) -> None:
    path = _cross_file_project(tmp_path)
    result = query_molecules(path)
    assert result.molecule_keys == ("k0", "k1", "k2", "k3")  # store order preserved
    assert result.movie_ids == ("m10", "m11", "mNM")
    assert result.by_movie() == {"m10": ("k0", "k1"), "m11": ("k2",), "mNM": ("k3",)}
    assert result.n_movies == 3


# --- read_condition_keys primitive (behind key filtering) ---------------------


def test_read_condition_keys_maps_ids_to_reconstructed_keys(tmp_path: Path) -> None:
    path = _cross_file_project(tmp_path)
    assert C.read_condition_keys(path) == {}  # nothing materialized yet
    C.sync_conditions(path)

    keys = C.read_condition_keys(path)
    assert set(keys) == {_CID_A, _CID_NM}
    assert keys[_CID_A].construct_variant == _KEY_A.construct_variant
    assert keys[_CID_A].ligand == _KEY_A.ligand
    assert keys[_CID_A].ligand_concentration == 600.0
    assert keys[_CID_A].temperature_c is None  # absent numeric round-trips to None
    # Each reconstructed key hashes back to its own id (self-consistent rows).
    assert keys[_CID_A].condition_id() == _CID_A


def test_project_wrapper_matches_module_read_condition_keys(tmp_path: Path) -> None:
    path = _cross_file_project(tmp_path)
    C.sync_conditions(path)
    assert Project.open(path).read_condition_keys() == C.read_condition_keys(path)


# --- exports ------------------------------------------------------------------


def test_query_symbols_exported() -> None:
    import tether.analysis as analysis

    for name in ("query_molecules", "ConditionQueryResult", "MoleculeMatch"):
        assert name in analysis.__all__
        assert hasattr(analysis, name)
