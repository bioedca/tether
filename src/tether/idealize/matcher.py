# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Return-leg intensity-trace matching for the tMAVEN hand-off (PRD §7.4).

tMAVEN's SMD writer has no per-molecule slot and its exporter may subset or
reorder molecules by the GUI selection mask (Appendix D.1), so coordinates in a
returning SMD are *not trusted*. Instead Tether re-resolves each returning trace
to its molecule by **exact intensity-trace matching** of the SMD ``raw`` series
against the retained store, using the molecule id / row order only as a hint
(:func:`match_return_leg`). tMAVEN preserves ``raw`` byte-for-byte on a save
(corrections and idealization live in separate arrays), so an exact match is the
correct identity test; unmatched returning molecules are reported, never guessed
(§5.3).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class MatchResult:
    """Outcome of resolving returning traces against the retained store.

    ``mapping[i]`` is the store index matched to returning trace ``i``, or ``-1``
    if it is unmatched. The match is **one-to-one**: each store molecule is
    claimed by at most one returning trace.
    """

    mapping: np.ndarray
    matched: list[tuple[int, int]] = field(default_factory=list)
    unmatched: list[int] = field(default_factory=list)

    @property
    def n_matched(self) -> int:
        """Number of returning traces resolved to a store molecule."""
        return len(self.matched)

    @property
    def n_unmatched(self) -> int:
        """Number of returning traces left unresolved."""
        return len(self.unmatched)

    @property
    def all_matched(self) -> bool:
        """True when every returning trace was resolved to a store molecule."""
        return not self.unmatched


def _validate_raw(arr: np.ndarray, name: str) -> np.ndarray:
    arr = np.asarray(arr, dtype="float64")
    if arr.ndim != 3 or arr.shape[2] != 2:
        raise ValueError(f"{name} must be (n_molecules, n_frames, 2); got {arr.shape}")
    return arr


def match_return_leg(
    returned_raw,
    store_raw,
    *,
    atol: float = 1e-6,
    rtol: float = 0.0,
    id_hint=None,
) -> MatchResult:
    """Match returning SMD traces to the retained store by raw-intensity identity.

    Comparison is over the leading ``min(n_frames)`` frames of both channels,
    which is the shared real-data region when tMAVEN has zero-padded traces to a
    common length (its ``concatenate_smds`` pads at the tail; PRD §5.2). A pair
    matches when ``|returned - store| <= atol + rtol*|store|`` everywhere in that
    region (the :func:`numpy.allclose` predicate).

    Parameters
    ----------
    returned_raw:
        ``(M, T_r, 2)`` raw traces from the returning SMD.
    store_raw:
        ``(N, T_s, 2)`` raw traces retained in the Tether store.
    atol, rtol:
        Absolute / relative tolerance of the intensity match. The default is a
        tight absolute tolerance (raw is preserved exactly across a tMAVEN save).
    id_hint:
        Optional length-``M`` integer array of candidate store indices (from
        molecule id / row order). A hint is honoured only when it *also* matches
        on intensity; otherwise the full intensity search decides. ``-1`` (or
        out-of-range) means "no hint" for that trace.

    Returns
    -------
    MatchResult
        One-to-one mapping, the matched pairs, and the unmatched returning rows.
    """
    returned = _validate_raw(returned_raw, "returned_raw")
    store = _validate_raw(store_raw, "store_raw")
    m, n = returned.shape[0], store.shape[0]
    tr, ts = returned.shape[1], store.shape[1]
    t = min(tr, ts)

    if id_hint is not None:
        id_hint = np.asarray(id_hint, dtype="int64").reshape(-1)
        if id_hint.shape[0] != m:
            raise ValueError(f"id_hint must have length {m}, got {id_hint.shape[0]}")

    store_t = store[:, :t, :]
    threshold = atol + rtol * np.abs(store_t)  # (N, t, 2), elementwise allclose bound

    # The leading-region match only implies identity when the frames discarded
    # past the common length ``t`` are zero padding, not real data (tMAVEN pads
    # at the tail, PRD §5.2). Gate per longer side.
    store_tail_ok = (
        np.all(store[:, t:, :] == 0.0, axis=(1, 2)) if ts > t else np.ones(n, dtype="bool")
    )

    mapping = np.full(m, -1, dtype="int64")
    used = np.zeros(n, dtype="bool")
    matched: list[tuple[int, int]] = []
    unmatched: list[int] = []

    for i in range(m):
        if tr > t and not np.all(returned[i, t:, :] == 0.0):
            # Returning trace carries real data past the store's length: not a match.
            unmatched.append(i)
            continue
        diff = np.abs(store_t - returned[i, :t, :])  # (N, t, 2)
        within = np.all(diff <= threshold, axis=(1, 2)) & ~used & store_tail_ok  # (N,)

        chosen = -1
        # Honour a hint only when it is itself a valid intensity match.
        if id_hint is not None:
            h = int(id_hint[i])
            if 0 <= h < n and within[h]:
                chosen = h
        if chosen < 0 and within.any():
            # Tie-break by smallest worst-case deviation for a stable result.
            maxdiff = diff.reshape(n, -1).max(axis=1)
            maxdiff[~within] = np.inf
            chosen = int(np.argmin(maxdiff))

        if chosen >= 0:
            mapping[i] = chosen
            used[chosen] = True
            matched.append((i, chosen))
        else:
            unmatched.append(i)

    return MatchResult(mapping=mapping, matched=matched, unmatched=unmatched)
