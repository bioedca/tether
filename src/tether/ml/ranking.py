# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Quality-ranking evaluation and the never-auto-drop ranking contract (PRD §7.5; FR-ML).

The evaluation substrate the per-condition quality ranker (PRD §7.5, PLAN §9 M5) is
measured on, and the invariant it must obey — both defined *independently of which model
produces the scores*:

* **precision@k** (:func:`precision_at_k`) — the M5 success metric (PRD §7.5): the
  fraction of *good* (human-accepted) traces among the first ``k`` reviewed, where ``k``
  is a curation sitting's budget (≈ 20–50 traces at ~1–2 s each). It is the standard
  information-retrieval ranking-quality metric [Manzhos2025][Divekar2026]; the M5 §9 gate
  is a precision@k **uplift over the file-/extraction-order baseline**
  (:func:`precision_at_k_uplift`), whose prequential median-across-videos protocol lands
  in its own later PR.
* the **never-auto-drop** contract (:class:`RankedTraces`, :func:`rank_by_score`) — PRD §7.5
  "the model **shall only re-order / pre-sort — never auto-drop**". A ranking is a
  *permutation* of the molecule set: every molecule appears exactly once and none is
  removed. A molecule the model cannot score (a ``NaN`` quality score, never a fabricated
  ``0``) is ranked **last** (least-confident) and **kept**, not dropped — the ranking
  analogue of :mod:`tether.ml.similarity`'s "reported, not dropped" discipline.

This module is deliberately **model-free and dependency-free**: it fixes the metric and the
ordering contract in pure NumPy so the gradient-boosting ranker (PRD §7.5 [Chen2016], a
later PR that adds the scikit-learn/XGBoost dependency) plugs its scores into
:func:`rank_by_score` and is scored by :func:`precision_at_k` without either concern
importing the other.

Determinism. Every ordering breaks ties on the stable ``molecule_id`` (the
:mod:`tether.ml.similarity` precedent), so a ranking is reproducible across platforms
regardless of a scorer's or a sort's internal ordering.

References
----------
[Manzhos2025] Manzhos T., et al. "Average Precision at Cutoff k under Random Rankings:
    Expectation and Variance." Modern Stochastics: Theory and Applications (2026) / ArXiv
    (2025) — precision@k / MAP@k as the standard ranking-quality metric and its
    random-ranking baseline.
[Divekar2026] Divekar A., et al. "PRECISE: Reducing the Bias of LLM Evaluations Using
    Prediction-Powered Ranking Estimation." (2026) — Precision@K as the "business-critical"
    retrieval metric, and relevance *uplift* as the comparison of interest.
[Chen2016] Chen T. & Guestrin C. "XGBoost: A Scalable Tree Boosting System." KDD (2016) —
    the gradient-boosting ranker this substrate will measure (a later PR).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = [
    "RankedTraces",
    "file_order_ranking",
    "precision_at_k",
    "precision_at_k_uplift",
    "rank_by_score",
]


def _is_positive_int(k: object) -> bool:
    """Whether ``k`` is a positive integer (a Python/NumPy int, and *not* a ``bool``).

    ``bool`` is a subclass of ``int`` in Python, so ``precision_at_k(rel, True)`` would
    otherwise be silently read as ``k == 1``; reject it so a boolean cutoff fails loudly.
    """
    return isinstance(k, (int, np.integer)) and not isinstance(k, bool) and int(k) > 0


def _as_relevance(relevance: object) -> np.ndarray:
    """Coerce ``relevance`` to a 1-D boolean array, rejecting non-0/1 or non-finite input.

    Relevance is a *known* accept/reject label, never an undefined feature, so a
    non-finite or non-boolean entry is a caller error rather than something to tolerate.
    """
    arr = np.asarray(relevance)
    if arr.ndim != 1:
        raise ValueError(f"relevance must be 1-D, got shape {arr.shape}")
    if arr.dtype == bool:
        return arr
    values = arr.astype(np.float64)
    if not bool(np.isfinite(values).all()):
        raise ValueError("relevance has non-finite entries; expected a boolean 0/1 array")
    if not bool(np.isin(values, (0.0, 1.0)).all()):
        raise ValueError("relevance must be a boolean 0/1 array")
    return values.astype(bool)


def precision_at_k(relevance: object, k: int) -> float:
    """Precision@k: the fraction of *good* items among the top ``k`` of a ranking (PRD §7.5).

    Parameters
    ----------
    relevance:
        A 1-D boolean (or 0/1) array **in ranked order** (best first); ``True`` marks a
        good (human-accepted) trace.
    k:
        The review budget — a positive integer (PRD §7.5: ``k`` ≈ 20–50).

    Returns
    -------
    float
        ``(good in the top min(k, n)) / min(k, n)``. For the normal curation regime
        ``n >= k`` this is textbook precision@k; when fewer than ``k`` labeled traces
        exist all ``n`` are reviewed, so the denominator is the number *actually
        reviewable* — never dividing by phantom slots (which would cap precision below
        ``1`` even for a perfect ranking).

    Raises
    ------
    ValueError
        ``k`` is not a positive integer; ``relevance`` is empty (precision is undefined
        with no data — surfaced loudly, never a fabricated ``0``/``NaN``); or ``relevance``
        is not a 1-D finite 0/1 array.
    """
    rel = _as_relevance(relevance)
    n = rel.shape[0]
    if n == 0:
        raise ValueError("precision_at_k is undefined over an empty ranking (no labeled traces)")
    if not _is_positive_int(k):
        raise ValueError(f"k must be a positive integer, got {k!r}")
    cutoff = min(int(k), n)
    return float(np.count_nonzero(rel[:cutoff])) / cutoff


@dataclass(frozen=True)
class RankedTraces:
    """A ranking of a molecule set — a *permutation*, never a filter (PRD §7.5).

    ``molecule_ids`` is the ranked order (index ``0`` = best / most likely good);
    ``scores[i]`` is the quality score of ``molecule_ids[i]`` (higher = better for a
    ``descending`` ranking; ``NaN`` marks an unscored molecule ranked last). The
    never-auto-drop invariant is a *construction guarantee*: the ids are unique and no
    molecule is ever removed, so a :class:`RankedTraces` always ranks **every** molecule it
    was built from.
    """

    molecule_ids: tuple[str, ...]
    scores: tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.molecule_ids) != len(self.scores):
            raise ValueError(
                f"molecule_ids ({len(self.molecule_ids)}) and scores ({len(self.scores)}) "
                "must be the same length"
            )
        if len(set(self.molecule_ids)) != len(self.molecule_ids):
            raise ValueError("molecule_ids must be unique (a ranking is a permutation)")

    @property
    def n(self) -> int:
        """The number of ranked molecules."""
        return len(self.molecule_ids)

    def top(self, k: int) -> list[str]:
        """The ``min(k, n)`` best-ranked molecule ids."""
        if not _is_positive_int(k):
            raise ValueError(f"k must be a positive integer, got {k!r}")
        return list(self.molecule_ids[: int(k)])

    def rank_of(self, molecule_id: str) -> int:
        """The 1-based rank of ``molecule_id`` (``1`` = best); raises if it is not ranked."""
        try:
            return self.molecule_ids.index(str(molecule_id)) + 1
        except ValueError:
            raise KeyError(f"molecule_id {molecule_id!r} is not in this ranking") from None

    def ranked_relevance(self, is_good: Mapping[str, bool]) -> np.ndarray:
        """Relevance of the *labeled* molecules, in this ranking's order.

        Walks the ranking and, for every molecule that carries a ground-truth label in
        ``is_good`` (a human accept/reject; unlabeled molecules are skipped), records
        ``True`` where accepted. The result is the ranked-order relevance array
        :func:`precision_at_k` consumes.
        """
        return np.array(
            [bool(is_good[mid]) for mid in self.molecule_ids if mid in is_good],
            dtype=bool,
        )


def rank_by_score(
    molecule_ids: Sequence[str], scores: object, *, descending: bool = True
) -> RankedTraces:
    """Rank molecules by quality ``scores`` — a never-auto-drop permutation (PRD §7.5).

    Every molecule appears exactly once in the result. A molecule whose score is ``NaN``
    (unscored — never a fabricated ``0``) is ranked **last**, after all scored molecules,
    so it is kept and visibly least-confident rather than dropped. Ties (equal scores, or
    within the ``NaN`` group) break on the ascending ``molecule_id`` so the ranking is
    deterministic across platforms.

    Parameters
    ----------
    molecule_ids:
        The molecules to rank (must be unique).
    scores:
        Their quality scores, aligned to ``molecule_ids``. ``NaN`` is allowed and ranks
        last; ``±inf`` is rejected (a score must be a real number or ``NaN``).
    descending:
        ``True`` (default) ranks the highest score first (best quality first).

    Raises
    ------
    ValueError
        ``molecule_ids`` and ``scores`` differ in length, ``molecule_ids`` are not unique,
        or a score is infinite.
    """
    ids = [str(m) for m in molecule_ids]
    values = np.asarray(scores, dtype=np.float64)
    if values.ndim != 1 or values.shape[0] != len(ids):
        raise ValueError(
            f"scores must be 1-D aligned to molecule_ids ({len(ids)}), got shape {values.shape}"
        )
    if len(set(ids)) != len(ids):
        raise ValueError("molecule_ids must be unique (a ranking is a permutation)")
    if bool(np.isinf(values).any()):
        raise ValueError("scores must be finite or NaN (an infinite score cannot be ranked)")

    is_nan = np.isnan(values)
    ordered = -values if descending else values  # only read where the score is finite
    # (is_nan flag, ordered score, molecule_id): the NaN group sorts last; every group
    # breaks ties on the ascending molecule_id -> a deterministic, never-drop permutation.
    order = sorted(
        range(len(ids)),
        key=lambda i: (bool(is_nan[i]), 0.0 if is_nan[i] else float(ordered[i]), ids[i]),
    )
    return RankedTraces(
        molecule_ids=tuple(ids[i] for i in order),
        scores=tuple(float(values[i]) for i in order),
    )


def file_order_ranking(molecule_ids: Sequence[str]) -> RankedTraces:
    """The identity ranking — molecules in file/extraction order (the §7.5 baseline).

    The precision@k **baseline** the ranker's uplift is measured against (PRD §7.5):
    curation in the order traces come out of the file, with no quality model. Preserves the
    given order *exactly* (it is not re-sorted); ``scores`` are the descending rank position
    purely so the object is a well-formed :class:`RankedTraces`.

    Raises
    ------
    ValueError
        ``molecule_ids`` are not unique.
    """
    ids = [str(m) for m in molecule_ids]
    if len(set(ids)) != len(ids):
        raise ValueError("molecule_ids must be unique (a ranking is a permutation)")
    n = len(ids)
    return RankedTraces(molecule_ids=tuple(ids), scores=tuple(float(n - i) for i in range(n)))


def precision_at_k_uplift(
    candidate: RankedTraces, baseline: RankedTraces, is_good: Mapping[str, bool], k: int
) -> float:
    """precision@k of ``candidate`` minus that of ``baseline`` (PRD §7.5, oracle (d)).

    The M5 success signal: how much a quality ranking improves the fraction of good traces
    in the first ``k`` reviewed over the file-order (:func:`file_order_ranking`) baseline.
    Returned as a **fraction** — multiply by 100 for the PRD §11.2 "percentage-point"
    ship-bar. Both rankings must cover the same labeled population so the two precisions are
    comparable; the prequential, median-across-videos gate that *consumes* this uplift lands
    in its own later PR.

    Raises
    ------
    ValueError
        The two rankings do not label the same molecule set, or ``k``/relevance is invalid
        (propagated from :func:`precision_at_k`).
    """
    candidate_labeled = [mid for mid in candidate.molecule_ids if mid in is_good]
    baseline_labeled = [mid for mid in baseline.molecule_ids if mid in is_good]
    if set(candidate_labeled) != set(baseline_labeled):
        raise ValueError(
            "candidate and baseline must rank the same labeled molecules to compare precision@k"
        )
    candidate_p = precision_at_k(candidate.ranked_relevance(is_good), k)
    baseline_p = precision_at_k(baseline.ranked_relevance(is_good), k)
    return candidate_p - baseline_p
