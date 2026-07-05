# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Feature-space trace similarity search — "find traces like these" (PRD §4.2, §7.5; FR-ML).

The distinct FR-ML retrieval capability PRD §4.2 (L212 "similarity search") and §7.5
(L519 ``"find traces like these"``) name alongside the per-condition quality ranker:
given one (or several) reference molecules, return the *other* molecules whose
engineered :mod:`tether.ml.features` vectors sit nearest in feature space, ranked by
distance. It is a **ranking/sorting** aid for curation — it never removes a molecule
from the population (PRD §7.5 "sort/rank only, never auto-drop").

Method. Each molecule's stored feature vector (:func:`tether.project.features.feature_matrix`)
is **z-score standardized** per feature — ``z = (x - mean) / std`` over the searchable
population — and neighbours are the nearest points under the ordinary Euclidean
(``p=2``) metric via a :class:`scipy.spatial.cKDTree`. Standardization is load-bearing,
not cosmetic: the raw features span wildly different scales (``total_intensity`` is
hundreds–thousands; ``fret_mean`` is in ``[0, 1]``), so an un-standardized Euclidean
distance would be dominated by whichever feature happens to carry the largest
magnitude and the small-but-informative features would not affect the ranking at all.
Feature normalization is the field-standard, empirically-validated preprocessing step
for distance-based (k-NN / nearest-neighbour) methods, and z-score standardization is
among the most robust choices across datasets [Vikri2026][Singh2020][Yusran2025].

Never-fabricate discipline (shared with :mod:`tether.ml.features`). A molecule whose
feature vector is not fully finite (an undefined feature is ``NaN``, never a fabricated
``0``) cannot be embedded in the metric space, so it is **excluded from the searchable
index and reported** (:attr:`SimilarityIndex.unindexed_ids`) rather than silently
dropped or given a fabricated coordinate — the caller/GUI can surface it as
"not rankable (undefined features)". Excluding an unrankable point from a *ranking* is
not the forbidden auto-drop: no molecule is removed from the project, and a query never
deletes anything — it returns an ordering.

Determinism. The ranking is deterministic across platforms: neighbours are sorted by
``(distance, molecule_id)``, so exact-distance ties (e.g. duplicate feature vectors)
break on the stable ``molecule_id`` rather than on the kd-tree's build-order-dependent
index.

References
----------
[Vikri2026] Vikri et al. "Impact of Data Normalization on K-Nearest Neighbor
    Classification Performance." (2026) — z-score standardization the most stable
    preprocessing for distance-based KNN.
[Singh2020] Singh & Singh. "Investigating the impact of data normalization on
    classification performance." Applied Soft Computing (2020) — z-score among the best
    normalizers for nearest-neighbour classifiers over 21 datasets.
[Yusran2025] Yusran et al. "Effect of Feature Normalization and Distance Metrics on
    K-Nearest Neighbors Performance." (2025) — normalization + metric choice govern
    nearest-neighbour behaviour; empirical evaluation is required.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np
from scipy.spatial import cKDTree

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from tether.project.features import StoredFeatures

__all__ = ["Neighbor", "SimilarityIndex", "build_similarity_index"]


@dataclass(frozen=True)
class Neighbor:
    """One ranked feature-space neighbour of a similarity query.

    ``rank`` is 1-based (``rank == 1`` is the nearest neighbour); ``distance`` is the
    standardized-feature Euclidean distance to the query.
    """

    molecule_id: str
    molecule_key: str
    distance: float
    rank: int


# ``eq=False`` -> identity equality/hash. This is a handle-like object whose fields include
# numpy arrays, a dict, and a cKDTree; dataclass-generated ``__eq__``/``__hash__`` would compare
# and hash those (an ndarray ``==`` is elementwise -> ambiguous truth value; an ndarray/dict is
# unhashable), so a plain ``index_a == index_b`` or ``hash(index)`` would raise. Identity semantics
# are the right contract here and keep the object safely comparable/hashable.
@dataclass(frozen=True, eq=False)
class SimilarityIndex:
    """A standardized feature-space nearest-neighbour index over a molecule set.

    Built by :func:`build_similarity_index`. Query it with :meth:`query` (one reference
    molecule), :meth:`query_many` ("find traces like *these*"), or :meth:`query_vector`
    (an arbitrary feature vector). Every query returns a distance-ranked
    :class:`Neighbor` list and never mutates the project.

    Attributes
    ----------
    feature_names:
        The feature column order the vectors are in.
    molecule_ids, molecule_keys:
        The **searchable** (fully finite) molecules, in kd-tree row order.
    unindexed_ids, unindexed_keys:
        Molecules excluded from the index because their feature vector is not fully
        finite — reported, never silently dropped (they remain in the project).
    mean, scale:
        The per-feature standardization applied before indexing (``scale`` is the
        population std, with a constant feature's ``0`` replaced by ``1`` so it
        contributes no distance rather than dividing by zero).
    """

    feature_names: tuple[str, ...]
    molecule_ids: tuple[str, ...]
    molecule_keys: tuple[str, ...]
    unindexed_ids: tuple[str, ...]
    unindexed_keys: tuple[str, ...]
    mean: np.ndarray
    scale: np.ndarray
    _standardized: np.ndarray = field(repr=False)
    _tree: cKDTree | None = field(repr=False)
    _row_of: dict[str, int] = field(repr=False)

    @property
    def n_indexed(self) -> int:
        """Number of searchable (rankable) molecules."""
        return len(self.molecule_ids)

    @property
    def n_unindexed(self) -> int:
        """Number of molecules excluded for non-finite features."""
        return len(self.unindexed_ids)

    def contains(self, molecule_id: str) -> bool:
        """Whether ``molecule_id`` is a searchable (indexed) molecule."""
        return str(molecule_id) in self._row_of

    def query(self, molecule_id: str, *, k: int | None = None) -> list[Neighbor]:
        """Rank the other molecules by similarity to ``molecule_id``.

        Parameters
        ----------
        molecule_id:
            The reference molecule (must be a searchable molecule of this index).
        k:
            Return only the ``k`` nearest neighbours (``None`` = the full ranking).

        Returns
        -------
        list[Neighbor]
            The neighbours ranked nearest-first, excluding ``molecule_id`` itself.

        Raises
        ------
        ValueError
            ``molecule_id`` is present but unrankable (non-finite features), or ``k`` is
            not positive.
        KeyError
            ``molecule_id`` is not in this index's molecule set at all.
        """
        mid = str(molecule_id)
        row = self._row_of.get(mid)
        if row is None:
            if mid in set(self.unindexed_ids):
                raise ValueError(
                    f"molecule_id {mid!r} has undefined (non-finite) features and cannot "
                    "be used as a similarity reference"
                )
            raise KeyError(f"molecule_id {mid!r} is not in this similarity index")
        distances = self._row_distances(self._standardized[row])
        return self._rank(distances, exclude_ids=(mid,), k=k)

    def query_many(self, molecule_ids: Sequence[str], *, k: int | None = None) -> list[Neighbor]:
        """Rank molecules by similarity to the *nearest* of several references.

        The multi-seed "find traces like these": a candidate's distance is the minimum
        of its distances to the reference molecules, so a candidate close to *any*
        reference ranks highly. All references are excluded from the result.

        Raises
        ------
        ValueError
            ``molecule_ids`` is empty, a reference is unrankable, or ``k`` is not positive.
        KeyError
            A reference is not in this index's molecule set.
        """
        seeds = [str(m) for m in molecule_ids]
        if not seeds:
            raise ValueError("query_many needs at least one reference molecule_id")
        rows: list[int] = []
        for mid in seeds:
            row = self._row_of.get(mid)
            if row is None:
                if mid in set(self.unindexed_ids):
                    raise ValueError(
                        f"molecule_id {mid!r} has undefined (non-finite) features and cannot "
                        "be used as a similarity reference"
                    )
                raise KeyError(f"molecule_id {mid!r} is not in this similarity index")
            rows.append(row)
        best = np.full(self.n_indexed, np.inf, dtype=np.float64)
        for row in rows:
            best = np.minimum(best, self._row_distances(self._standardized[row]))
        return self._rank(best, exclude_ids=seeds, k=k)

    def query_vector(
        self,
        vector: np.ndarray,
        *,
        k: int | None = None,
        exclude_ids: Iterable[str] = (),
    ) -> list[Neighbor]:
        """Rank molecules by similarity to an arbitrary (unstandardized) feature vector.

        ``vector`` is in :attr:`feature_names` order and is standardized with this
        index's fitted ``mean``/``scale`` before searching. ``exclude_ids`` drops named
        molecules from the result (e.g. the reference the vector came from).

        Raises
        ------
        ValueError
            ``vector`` has the wrong length or a non-finite entry, or ``k`` is not positive.
        """
        q = self._standardize_query(vector)
        return self._rank(self._row_distances(q), exclude_ids=exclude_ids, k=k)

    # -- internals ---------------------------------------------------------------

    def _standardize_query(self, vector: np.ndarray) -> np.ndarray:
        v = np.asarray(vector, dtype=np.float64).ravel()
        if v.shape[0] != len(self.feature_names):
            raise ValueError(
                f"query vector has {v.shape[0]} features, expected {len(self.feature_names)}"
            )
        if not bool(np.isfinite(v).all()):
            raise ValueError("query vector has non-finite features and cannot be ranked")
        return (v - self.mean) / self.scale

    def _row_distances(self, standardized_query: np.ndarray) -> np.ndarray:
        """Standardized Euclidean distance from ``standardized_query`` to every indexed row.

        Uses the kd-tree (querying all ``n_indexed`` points), then scatters the
        nearest-first result back into molecule row order so the caller can rank/exclude
        by identity. At curation scale (tens–hundreds of molecules per video) retrieving
        the full ranking is trivial and keeps ordering deterministic; a bounded top-``k``
        tree query is a later optimization if a searchable population ever grows large.
        """
        n = self.n_indexed
        if self._tree is None or n == 0:
            return np.empty(0, dtype=np.float64)
        dist, idx = self._tree.query(np.asarray(standardized_query, dtype=np.float64), k=n)
        dist = np.atleast_1d(np.asarray(dist, dtype=np.float64))
        idx = np.atleast_1d(np.asarray(idx, dtype=np.intp))
        out = np.empty(n, dtype=np.float64)
        out[idx] = dist
        return out

    def _rank(
        self, distances: np.ndarray, *, exclude_ids: Iterable[str], k: int | None
    ) -> list[Neighbor]:
        if k is not None and k <= 0:
            raise ValueError(f"k must be a positive integer, got {k}")
        exclude = {str(x) for x in exclude_ids}
        candidates = [
            (float(distances[r]), self.molecule_ids[r], self.molecule_keys[r])
            for r in range(self.n_indexed)
            if self.molecule_ids[r] not in exclude
        ]
        # Sort by distance, then molecule_id: exact-distance ties (duplicate feature
        # vectors) break deterministically instead of on kd-tree index order.
        candidates.sort(key=lambda t: (t[0], t[1]))
        if k is not None:
            candidates = candidates[:k]
        return [
            Neighbor(molecule_id=mid, molecule_key=mkey, distance=dist, rank=i + 1)
            for i, (dist, mid, mkey) in enumerate(candidates)
        ]


def build_similarity_index(features: StoredFeatures) -> SimilarityIndex:
    """Build a standardized feature-space :class:`SimilarityIndex` from stored features.

    Parameters
    ----------
    features:
        A :class:`~tether.project.features.StoredFeatures` (e.g. from
        :func:`tether.project.features.feature_matrix`). Its ``molecule_ids`` must be the
        unique per-row identity (the §7.10 join key).

    Returns
    -------
    SimilarityIndex
        Indexes every molecule whose feature vector is fully finite; molecules with any
        non-finite feature are recorded in ``unindexed_ids`` (reported, not dropped).

    Raises
    ------
    ValueError
        The feature matrix shape is inconsistent with the id/name lists, or two rows
        share a ``molecule_id`` (which would make identity-based exclusion ambiguous).
    """
    names = tuple(str(n) for n in features.feature_names)
    ids = [str(x) for x in features.molecule_ids]
    keys = [str(x) for x in features.molecule_keys]
    matrix = np.asarray(features.matrix, dtype=np.float64)
    if matrix.ndim != 2 or matrix.shape[1] != len(names):
        raise ValueError(
            f"feature matrix shape {matrix.shape} is inconsistent with {len(names)} feature names"
        )
    if matrix.shape[0] != len(ids) or len(ids) != len(keys):
        raise ValueError(
            f"feature matrix has {matrix.shape[0]} rows but {len(ids)} molecule_ids / "
            f"{len(keys)} molecule_keys"
        )
    if len(set(ids)) != len(ids):
        raise ValueError("molecule_ids are not unique; cannot build a similarity index")

    finite = np.isfinite(matrix).all(axis=1)
    idx_rows = np.nonzero(finite)[0]
    un_rows = np.nonzero(~finite)[0]

    indexed_ids = tuple(ids[i] for i in idx_rows)
    indexed_keys = tuple(keys[i] for i in idx_rows)
    unindexed_ids = tuple(ids[i] for i in un_rows)
    unindexed_keys = tuple(keys[i] for i in un_rows)

    sub = matrix[idx_rows]
    if sub.shape[0] > 0:
        mean = sub.mean(axis=0)
        std = sub.std(axis=0)  # population std (ddof=0)
        # A constant feature (std 0) carries no discriminating information: use scale 1
        # so it contributes 0 to every distance instead of dividing by zero.
        scale = np.where(std > 0.0, std, 1.0)
        standardized = (sub - mean) / scale
        tree: cKDTree | None = cKDTree(standardized)
    else:
        mean = np.zeros(len(names), dtype=np.float64)
        scale = np.ones(len(names), dtype=np.float64)
        standardized = np.empty((0, len(names)), dtype=np.float64)
        tree = None

    # Freeze the fitted standardization + indexed vectors: a caller mutating them in place
    # would silently corrupt every subsequent query's standardization.
    for arr in (mean, scale, standardized):
        arr.flags.writeable = False

    return SimilarityIndex(
        feature_names=names,
        molecule_ids=indexed_ids,
        molecule_keys=indexed_keys,
        unindexed_ids=unindexed_ids,
        unindexed_keys=unindexed_keys,
        mean=mean,
        scale=scale,
        _standardized=standardized,
        _tree=tree,
        _row_of={mid: r for r, mid in enumerate(indexed_ids)},
    )
