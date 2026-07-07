# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Cold-start label-weight decay law — the ``sample_weight`` the ranker trains on (PRD §7.5; FR-ML).

The per-condition quality ranker (:mod:`tether.ml.gbranker`) trains **weighted by each label's
``source``** (PRD §5.1 ``/labels``, §7.5): a human accept/reject is full weight, while the two
provisional cold-start priors — a Deep-LASI-provisional label or a cross-condition seed — carry a
**down-weighted** effective weight that **decays toward zero as human labels in the condition
accrue**. This module is that decay law, kept pure (NumPy only) and **store-free**: it turns a
``source``-derived *is-this-a-human-label?* mask plus the condition's human-label count into the
per-row training weight, with no knowledge of the ``.tether`` store or the ``/labels`` vocabulary
(that mapping lives in the store layer, :mod:`tether.project.weighting`).

The law (PRD §7.5, §11.2)::

    w = w₀ / (1 + n_human)          for a provisional/seed label
    w = 1.0                         for a human label

where ``w₀`` is the seed weight (default :data:`DEFAULT_SEED_WEIGHT` ≈ 0.3, the PRD §11.2
"Cold-start seed weight w₀ / decay law" tunable) and ``n_human`` is the count of human labels in
the *condition* at retrain time. The weight is **mutable** — recomputed and rewritten on every
retrain (:func:`tether.project.weighting.recompute_label_weights`) — so a seed that bootstrapped an
empty condition (``n_human = 0`` → ``w = w₀``) is progressively discounted as real curation arrives
and never masquerades as ground truth once the condition is well-labeled.

Why ``1 / (1 + n_human)``. Decaying a sample's training weight as trusted labels accumulate — so an
incremental classifier leans on priors while data is scarce and shifts onto the real labels as they
arrive — is an established mechanism for weighted incremental learning [Nguyen2019]. The ``1 + n``
denominator keeps the seed's influence finite and non-zero at ``n_human = 0`` (a lone seed still
counts fully, the cold-start case) and shrinks it hyperbolically (∝ ``1/n``) thereafter; it is a
Tether design default (PRD §11.2), not an empirical constant.

References
----------
[Nguyen2019] Nguyen, Nguyen, Liew & Wang. "Multi-label classification via incremental clustering on
    an evolving data stream." Pattern Recognition 95:96–113 (2019) — an incremental learner whose
    per-sample weights decay over time so the model favours newer labelled data, the weighted
    incremental-learning mechanism the cold-start decay applies to provisional priors.
"""

from __future__ import annotations

import math

import numpy as np

__all__ = ["DEFAULT_SEED_WEIGHT", "HUMAN_WEIGHT", "effective_weights", "seed_weight"]

#: The default cold-start seed weight ``w₀`` (PRD §11.2 "Cold-start seed weight w₀ / decay law";
#: human labels are the full-weight reference this normalizes against). A provisional/seed label
#: starts at ``w₀`` when the condition has no human labels and decays from there.
DEFAULT_SEED_WEIGHT = 0.3

#: The full training weight of a human label (PRD §7.5: "human labels are full weight (1.0)"). The
#: fixed reference the seed-weight decay is measured against; kept here as the pure-layer default so
#: this module is usable without the store's :data:`tether.project.labels.HUMAN_WEIGHT`.
HUMAN_WEIGHT = 1.0


def _check_w0(w0: float) -> float:
    """Validate the seed weight ``w₀``: a finite, strictly positive scalar."""
    w0 = float(w0)
    if not (math.isfinite(w0) and w0 > 0.0):
        raise ValueError(f"w0 (seed weight) must be finite and > 0, got {w0}")
    return w0


def seed_weight(n_human: int, *, w0: float = DEFAULT_SEED_WEIGHT) -> float:
    """The decayed weight ``w = w₀ / (1 + n_human)`` of one provisional/seed label (PRD §7.5).

    The cold-start prior's effective training weight given ``n_human`` human labels already present
    in its condition: full seed weight ``w₀`` at ``n_human = 0`` (a lone seed bootstrapping an empty
    condition), shrinking hyperbolically toward zero as human curation accrues.

    Parameters
    ----------
    n_human:
        The count of human labels in the condition (``>= 0``). Not the *seed's* own count — the
        amount of trusted evidence that discounts it.
    w0:
        The seed weight ``w₀`` (default :data:`DEFAULT_SEED_WEIGHT`).

    Raises
    ------
    ValueError
        ``n_human`` is negative or not an integer, or ``w0`` is not finite and positive.
    """
    w0 = _check_w0(w0)
    if isinstance(n_human, bool) or not isinstance(n_human, (int, np.integer)):
        raise ValueError(f"n_human must be a non-negative integer, got {n_human!r}")
    if int(n_human) < 0:
        raise ValueError(f"n_human must be >= 0, got {n_human}")
    return w0 / (1.0 + int(n_human))


def effective_weights(
    is_human: object,
    n_human: object,
    *,
    w0: float = DEFAULT_SEED_WEIGHT,
    human_weight: float = HUMAN_WEIGHT,
) -> np.ndarray:
    """Vectorized per-row training weights for a label set (PRD §7.5) — the recompute primitive.

    Each row is either a **human** label (weight ``human_weight``, full weight) or a
    **provisional/seed** prior (weight ``w₀ / (1 + n_human)``), selected by the boolean
    ``is_human`` mask. ``n_human`` is the per-row human-label count of that row's condition (a human
    row's own weight does not depend on it), so a single call recomputes a whole ``/labels`` table
    whose rows span several conditions.

    Parameters
    ----------
    is_human:
        Boolean array, ``True`` where the row is a human label (full weight).
    n_human:
        Integer array (broadcast to ``is_human``'s shape) of each row's condition's human-label
        count, ``>= 0``. A scalar applies to every row.
    w0:
        The seed weight ``w₀`` (default :data:`DEFAULT_SEED_WEIGHT`).
    human_weight:
        The full weight of a human label (default :data:`HUMAN_WEIGHT` = ``1.0``).

    Returns
    -------
    numpy.ndarray
        A ``float64`` weight per row, aligned to ``is_human``.

    Raises
    ------
    ValueError
        ``w0`` is not finite and positive; ``human_weight`` is not finite and non-negative;
        ``n_human`` has any negative or non-integer entry; or the two arrays do not broadcast.
    """
    w0 = _check_w0(w0)
    human_weight = float(human_weight)
    if not (math.isfinite(human_weight) and human_weight >= 0.0):
        raise ValueError(f"human_weight must be finite and >= 0, got {human_weight}")

    mask = np.asarray(is_human, dtype=bool)
    counts = np.asarray(n_human)
    if counts.dtype.kind not in ("i", "u"):
        # A float count is a caller error (a fractional "number of labels" is meaningless), not
        # something to silently truncate.
        raise ValueError(f"n_human must be an integer array, got dtype {counts.dtype}")
    if np.any(counts < 0):
        raise ValueError("n_human must be >= 0 for every row")
    try:
        counts = np.broadcast_to(counts, mask.shape)
    except ValueError as exc:
        raise ValueError(
            f"n_human shape {counts.shape} does not broadcast to is_human shape {mask.shape}"
        ) from exc

    seed = w0 / (1.0 + counts.astype(np.float64))
    return np.where(mask, human_weight, seed).astype(np.float64)
