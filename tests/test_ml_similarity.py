# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Feature-space similarity search — pure core (M5, FR-ML; PRD §4.2, §7.5).

Locks the "find traces like these" retrieval: querying a reference trace returns its
feature-space near-neighbours ranked by distance; the ranking is deterministic and
standardization-aware (a large-scale feature does not dominate); non-finite molecules
are reported not dropped; and no molecule is ever removed from the population
(never-auto-drop). Headless -> base CI matrix.
"""

from __future__ import annotations

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")

import numpy as np  # noqa: E402

from tether.ml.similarity import (  # noqa: E402
    Neighbor,
    SimilarityIndex,
    build_similarity_index,
)

_NAMES = ("f0", "f1")


def _stored(ids, matrix, *, names=_NAMES, keys=None):
    """A minimal StoredFeatures-shaped object for the pure similarity core.

    The index only reads ``feature_names``/``molecule_ids``/``molecule_keys``/``matrix``,
    so a tiny duck-typed stand-in keeps these tests free of the HDF5 store layer.
    """
    from tether.project.features import StoredFeatures

    matrix = np.asarray(matrix, dtype=np.float64)
    ids = list(ids)
    keys = list(keys) if keys is not None else [f"key-{m}" for m in ids]
    return StoredFeatures(
        molecule_ids=ids,
        molecule_keys=keys,
        feature_names=tuple(names),
        matrix=matrix,
        intensity_quantity="corrected",
        app_version="test",
        created_utc="2026-07-05T00:00:00+00:00",
    )


def _raw_nn_order(matrix, seed_row):
    """Nearest-first molecule-row order by UN-standardized Euclidean distance."""
    d = np.linalg.norm(matrix - matrix[seed_row], axis=1)
    return [r for r in np.argsort(d, kind="stable") if r != seed_row]


def test_query_ranks_neighbours_by_distance() -> None:
    # A 1-D line: neighbours of the middle point come out nearest-first.
    ids = ["m0", "m1", "m2", "m3"]
    matrix = [[0.0, 0.0], [1.0, 0.0], [3.0, 0.0], [10.0, 0.0]]
    index = build_similarity_index(_stored(ids, matrix))

    neighbours = index.query("m1")
    assert [n.molecule_id for n in neighbours] == ["m0", "m2", "m3"]
    assert [n.rank for n in neighbours] == [1, 2, 3]
    # Distances are non-decreasing and exclude the reference itself.
    dists = [n.distance for n in neighbours]
    assert dists == sorted(dists)
    assert "m1" not in {n.molecule_id for n in neighbours}
    # molecule_key travels with the neighbour.
    assert neighbours[0].molecule_key == "key-m0"


def test_deterministic_across_repeated_queries() -> None:
    rng = np.random.default_rng(11)
    ids = [f"m{i}" for i in range(12)]
    matrix = rng.normal(size=(12, 2))
    index = build_similarity_index(_stored(ids, matrix))
    first = [(n.molecule_id, n.distance) for n in index.query("m3")]
    second = [(n.molecule_id, n.distance) for n in index.query("m3")]
    assert first == second


def test_standardization_changes_the_ranking() -> None:
    # f0 spans hundreds, f1 spans ~1. Row m1 is nearest to the seed in RAW space
    # (small f0 gap dominates); row m2 is nearest once features are standardized
    # (comparable per-feature deviations). The similarity index must follow the
    # standardized geometry, not the raw one — otherwise the large-magnitude feature
    # silently dominates every ranking.
    ids = ["m0", "m1", "m2", "m3"]
    matrix = np.array([[0.0, 0.0], [8.0, 10.0], [50.0, 1.0], [100.0, 0.0]], dtype=np.float64)
    index = build_similarity_index(_stored(ids, matrix))

    raw_top = ids[_raw_nn_order(matrix, 0)[0]]
    # Standardized order computed independently with the same z-score formula.
    std = matrix.std(axis=0)
    z = (matrix - matrix.mean(axis=0)) / np.where(std > 0, std, 1.0)
    zdist = np.linalg.norm(z - z[0], axis=1)
    std_top = ids[[r for r in np.argsort(zdist, kind="stable") if r != 0][0]]

    assert raw_top == "m1"  # raw NN is dominated by the big-magnitude feature
    assert std_top == "m2"  # standardized NN is a genuinely different molecule
    assert index.query("m0")[0].molecule_id == std_top  # index follows standardized geometry


def test_ties_break_on_molecule_id() -> None:
    # Two molecules with an identical feature vector are equidistant from any seed;
    # the deterministic tiebreak orders them by molecule_id, not kd-tree build order.
    ids = ["seed", "zzz", "aaa"]
    matrix = [[0.0, 0.0], [5.0, 5.0], [5.0, 5.0]]
    index = build_similarity_index(_stored(ids, matrix))
    neighbours = index.query("seed")
    assert [n.molecule_id for n in neighbours] == ["aaa", "zzz"]
    assert neighbours[0].distance == pytest.approx(neighbours[1].distance)


def test_k_caps_result_and_full_ranking_is_default() -> None:
    ids = [f"m{i}" for i in range(6)]
    matrix = [[float(i), 0.0] for i in range(6)]
    index = build_similarity_index(_stored(ids, matrix))
    assert len(index.query("m0")) == 5  # k=None -> every other molecule
    capped = index.query("m0", k=2)
    assert [n.molecule_id for n in capped] == ["m1", "m2"]
    with pytest.raises(ValueError, match="positive"):
        index.query("m0", k=0)
    with pytest.raises(ValueError, match="positive"):
        index.query("m0", k=-3)


def test_never_drops_molecules_only_excludes_the_seed() -> None:
    ids = [f"m{i}" for i in range(7)]
    matrix = np.arange(14, dtype=np.float64).reshape(7, 2)
    index = build_similarity_index(_stored(ids, matrix))
    # Every non-seed molecule appears exactly once; the population is fully accounted for.
    result_ids = {n.molecule_id for n in index.query("m2")}
    assert result_ids == set(ids) - {"m2"}
    assert index.n_indexed + index.n_unindexed == 7


def test_nonfinite_molecule_is_reported_not_dropped() -> None:
    ids = ["m0", "m1", "m2"]
    matrix = [[0.0, 0.0], [1.0, 1.0], [np.nan, 2.0]]  # m2 has an undefined feature
    index = build_similarity_index(_stored(ids, matrix))
    assert index.unindexed_ids == ("m2",)
    assert index.n_indexed == 2
    assert not index.contains("m2")
    # m2 is not silently gone: it is reported and still counted in the population.
    assert index.n_indexed + index.n_unindexed == 3
    # A rankable seed still ranks, and never returns the unrankable molecule.
    neighbours = index.query("m0")
    assert [n.molecule_id for n in neighbours] == ["m1"]
    # Using the unrankable molecule as a reference fails loudly (never a fabricated coord).
    with pytest.raises(ValueError, match="non-finite"):
        index.query("m2")


def test_spatial_absence_molecule_reported_not_dropped() -> None:
    # A molecule alone in its movie has a defined trace but an undefined
    # neighbor_distance (NaN — no neighbour to measure; never a fabricated distance).
    # Like any non-finite-feature molecule it is reported in unindexed_ids, NOT silently
    # dropped, and its exclusion never removes it from the population (the never-drop
    # contract, spatial-absence case documented in tether.ml.similarity).
    names = ("snr", "neighbor_distance")
    ids = ["crowded0", "crowded1", "lonely"]
    matrix = [[5.0, 4.0], [6.0, 3.0], [7.0, np.nan]]  # 'lonely': good trace, no neighbour
    index = build_similarity_index(_stored(ids, matrix, names=names))
    assert index.unindexed_ids == ("lonely",)
    assert index.n_indexed == 2
    assert index.n_indexed + index.n_unindexed == 3  # never silently gone
    # The two crowded molecules still rank against each other, never surfacing 'lonely'.
    assert [n.molecule_id for n in index.query("crowded0")] == ["crowded1"]


def test_constant_feature_column_is_safe() -> None:
    # A feature that is identical for every molecule has std 0; it must contribute 0
    # distance (scale -> 1), never a division-by-zero inf/nan.
    ids = ["m0", "m1", "m2"]
    matrix = [[5.0, 0.0], [5.0, 1.0], [5.0, 9.0]]  # f0 constant
    index = build_similarity_index(_stored(ids, matrix))
    assert index.scale[0] == 1.0
    neighbours = index.query("m0")
    assert all(np.isfinite(n.distance) for n in neighbours)
    assert [n.molecule_id for n in neighbours] == ["m1", "m2"]  # ranked by f1 alone


def test_query_vector_standardizes_and_excludes() -> None:
    ids = ["m0", "m1", "m2"]
    matrix = np.array([[0.0, 0.0], [10.0, 10.0], [20.0, 20.0]], dtype=np.float64)
    index = build_similarity_index(_stored(ids, matrix))
    # A raw vector near m1 ranks m1 first; excluding m1 promotes the next nearest.
    near_m1 = index.query_vector(np.array([10.5, 9.5]))
    assert near_m1[0].molecule_id == "m1"
    excluded = index.query_vector(np.array([10.5, 9.5]), exclude_ids=["m1"])
    assert "m1" not in {n.molecule_id for n in excluded}
    with pytest.raises(ValueError, match="features"):
        index.query_vector(np.array([1.0, 2.0, 3.0]))  # wrong length
    with pytest.raises(ValueError, match="non-finite"):
        index.query_vector(np.array([1.0, np.nan]))


def test_query_many_uses_nearest_seed() -> None:
    # Two well-separated clusters; querying one molecule from each returns the remaining
    # members of both, each ranked by its distance to its own (nearest) seed.
    ids = ["a0", "a1", "b0", "b1"]
    matrix = [[0.0, 0.0], [0.2, 0.1], [50.0, 50.0], [50.2, 49.9]]
    index = build_similarity_index(_stored(ids, matrix))
    result = index.query_many(["a0", "b0"])
    assert {n.molecule_id for n in result} == {"a1", "b1"}  # both seeds excluded
    # a1 (0.22 from a0) is nearer to its seed than b1 (~0.22 from b0) — both close,
    # but neither is matched to the far cluster's seed.
    by_id = {n.molecule_id: n.distance for n in result}
    assert by_id["a1"] < 1.0
    assert by_id["b1"] < 1.0
    with pytest.raises(ValueError, match="at least one"):
        index.query_many([])


def test_unknown_molecule_id_raises_keyerror() -> None:
    index = build_similarity_index(_stored(["m0", "m1"], [[0.0, 0.0], [1.0, 1.0]]))
    with pytest.raises(KeyError, match="nope"):
        index.query("nope")


def test_empty_index_when_all_nonfinite() -> None:
    ids = ["m0", "m1"]
    matrix = [[np.nan, 0.0], [1.0, np.inf]]
    index = build_similarity_index(_stored(ids, matrix))
    assert index.n_indexed == 0
    assert set(index.unindexed_ids) == {"m0", "m1"}
    assert index.query_vector(np.array([0.0, 0.0])) == []  # nothing rankable, honest empty
    with pytest.raises(ValueError, match="non-finite"):
        index.query("m0")


def test_duplicate_molecule_ids_rejected() -> None:
    with pytest.raises(ValueError, match="unique"):
        build_similarity_index(_stored(["dup", "dup"], [[0.0, 0.0], [1.0, 1.0]]))


def test_matrix_shape_mismatch_rejected() -> None:
    with pytest.raises(ValueError, match="inconsistent"):
        build_similarity_index(_stored(["m0"], [[0.0, 0.0, 0.0]]))  # 3 cols, 2 names


def test_neighbor_and_index_are_frozen() -> None:
    index = build_similarity_index(_stored(["m0", "m1"], [[0.0, 0.0], [1.0, 1.0]]))
    assert isinstance(index, SimilarityIndex)
    n = index.query("m0")[0]
    assert isinstance(n, Neighbor)
    with pytest.raises(Exception):  # noqa: B017 - frozen dataclass rejects mutation
        n.distance = 0.0  # type: ignore[misc]


def test_index_is_hashable_and_uses_identity_semantics() -> None:
    # The index holds ndarray/dict/cKDTree fields; identity (eq=False) equality/hash keeps
    # it safely hashable and comparable instead of raising on those fields.
    stored = _stored(["m0", "m1"], [[0.0, 0.0], [1.0, 1.0]])
    a = build_similarity_index(stored)
    b = build_similarity_index(stored)
    assert hash(a) == hash(a)  # does not raise (would, if it hashed the ndarray/dict fields)
    assert a == a  # identity
    assert a != b  # two builds are distinct objects, not structurally compared
    assert a in {a, b}  # usable in a set/dict


def test_fitted_mean_scale_are_read_only() -> None:
    # Freezing the fitted standardization stops a caller from silently corrupting queries.
    index = build_similarity_index(
        _stored(["m0", "m1", "m2"], [[0.0, 0.0], [1.0, 2.0], [3.0, 5.0]])
    )
    with pytest.raises(ValueError, match="read-only"):
        index.mean[0] = 999.0
    with pytest.raises(ValueError, match="read-only"):
        index.scale[0] = 999.0
