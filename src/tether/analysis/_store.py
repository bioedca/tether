# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared ``.tether`` readers for the analysis population views (PRD §7.7).

Analysis pools per-frame quantities over the *non-rejected* population (§7.5),
a different selection contract from :mod:`tether.project.idealize` (which takes
explicit ``molecule_keys``). The ``/traces`` layer names are schema-frozen
(PRD §5) and the analysis-window fallback mirrors
``tether.project.idealize._windows`` semantics.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from tether.project.trace_layers import INTENSITY_QUANTITY_LAYERS as QUANTITY_KEYS

if TYPE_CHECKING:  # pragma: no cover - typing only
    from os import PathLike

    from tether.project.core import Project

    ProjectRef = Project | str | PathLike[str]

__all__ = [
    "QUANTITY_KEYS",
    "resolve_quantity",
    "windowed_channels",
    "windowed_channels_with_keys",
    "windowed_state_and_channels",
    "windowed_states",
]


def resolve_quantity(quantity: str) -> tuple[str, str]:
    """Map an ``intensity_quantity`` key to its (donor, acceptor) ``/traces`` layers."""
    try:
        return QUANTITY_KEYS[quantity]
    except KeyError:
        raise ValueError(
            f"intensity_quantity must be one of {sorted(QUANTITY_KEYS)}, got {quantity!r}"
        ) from None


def _to_str(value: object) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def windowed_channels_with_keys(
    project: ProjectRef,
    molecule_keys: list[str] | None,
    intensity_quantity: str,
    include_rejected: bool,
) -> list[tuple[str, np.ndarray, np.ndarray]]:
    """Per-molecule ``(molecule_key, donor, acceptor)`` slices over the analysis window.

    Reads ``/molecules`` + ``/traces``, keeps the non-rejected population (unless
    ``include_rejected``), optionally intersects with ``molecule_keys``, and slices
    each kept molecule to its ``analysis_window`` — falling back to ``frame_range``
    when unset ``[0, 0]`` (matching idealization). Returns store order, each row tagged
    with its ``molecule_key`` for per-molecule views (the anticorrelation-event finder).
    :func:`windowed_channels` is the key-less projection of this.

    Raises
    ------
    ValueError
        The store lacks the requested trace layer.
    """
    from tether.imaging.extract import read_molecules, read_traces
    from tether.project.core import Project as _Project
    from tether.project.labels import curation_filter_mask

    proj = project if isinstance(project, _Project) else _Project.open(project)
    path = proj.path
    donor_key, acceptor_key = resolve_quantity(intensity_quantity)

    molecules = read_molecules(path)
    if molecules.shape[0] == 0:
        return []
    traces = read_traces(path)
    for key in (donor_key, acceptor_key):
        if key not in traces:
            raise ValueError(
                f"{path.name}/traces has no {key!r} layer "
                f"(intensity_quantity={intensity_quantity!r})"
            )

    keep = curation_filter_mask(molecules, include_rejected=include_rejected)
    if molecule_keys is not None:
        wanted = {str(k) for k in molecule_keys}
        selected = np.array([_to_str(k) in wanted for k in molecules["molecule_key"]], dtype=bool)
        keep = keep & selected
    rows = np.nonzero(keep)[0]

    donor_all = traces[donor_key]
    acceptor_all = traces[acceptor_key]
    molecule_key_col = molecules["molecule_key"]
    analysis_window = molecules["analysis_window"]
    frame_range = molecules["frame_range"]

    out: list[tuple[str, np.ndarray, np.ndarray]] = []
    for i in rows:
        lo, hi = int(analysis_window[i][0]), int(analysis_window[i][1])
        if hi <= lo:  # unset [0, 0] -> native extent (mirrors idealize._windows)
            lo, hi = int(frame_range[i][0]), int(frame_range[i][1])
        donor = np.asarray(donor_all[i, lo:hi], dtype=np.float64)
        acceptor = np.asarray(acceptor_all[i, lo:hi], dtype=np.float64)
        out.append((_to_str(molecule_key_col[i]), donor, acceptor))
    return out


def windowed_channels(
    project: ProjectRef,
    molecule_keys: list[str] | None,
    intensity_quantity: str,
    include_rejected: bool,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Per-molecule ``(donor, acceptor)`` intensity slices over the analysis window.

    The key-less projection of :func:`windowed_channels_with_keys` (same reading,
    curation filter, ``molecule_keys`` selection, and window fallback; store order).

    Raises
    ------
    ValueError
        The store lacks the requested trace layer.
    """
    return [
        (donor, acceptor)
        for _key, donor, acceptor in windowed_channels_with_keys(
            project, molecule_keys, intensity_quantity, include_rejected
        )
    ]


def windowed_state_and_channels(
    project: ProjectRef,
    model_name: str,
    molecule_keys: list[str] | None,
    intensity_quantity: str,
    include_rejected: bool,
) -> list[tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """Per-molecule ``(state_path, donor, acceptor)`` slices over the idealized window.

    Pairs a persisted ``/idealization/{model_name}`` model's per-molecule Viterbi
    **state path** (:data:`tether.idealize.NO_STATE` = ``-1`` outside the window)
    with the *observed* ``/traces`` intensities of the **same** molecule and frames —
    the substrate the transition-aligned (post-synchronized) analysis views need
    (A2b heatmap; the coming TDP / dwell fits). Both arrays are sliced to the
    model's idealized extent (the contiguous run of non-:data:`~tether.idealize.NO_STATE`
    frames — the window the fit actually covered, robust to a later window edit) and
    re-based to frame 0, so ``state_path[t]`` and the intensities at ``t`` refer to
    the same frame.

    The idealization is joined to ``/molecules`` on ``molecule_id`` (the **unique**
    per-molecule identity — the ``molecule_key`` is not unique, §7.10), then the
    §7.5 curation filter and the optional ``molecule_keys`` selection are applied.
    A molecule whose idealized extent runs past the width of the ``/traces`` arrays
    is skipped rather than misaligned — the honest answer, never a fabricated
    pairing. That width is store-wide and zero-padded to the experiment-max frame
    count, not trimmed per molecule, so a re-extraction that shortened *this*
    molecule alone does **not** trip the guard: the vanished frames are read out of
    its zero pad. Rows are returned in idealization (fit) order.

    Parameters
    ----------
    project
        A :class:`~tether.project.core.Project` or a path to a ``.tether`` store.
    model_name
        Which ``/idealization/{model_name}`` supplies the state paths.
    molecule_keys
        Restrict to molecules with one of these ``molecule_key`` values (``None`` =
        all); intersected with the curation filter.
    intensity_quantity
        Which ``/traces`` layers feed the returned intensities: ``"corrected"`` or
        ``"raw"`` (see :func:`resolve_quantity`).
    include_rejected
        If ``True``, keep rejected molecules; else exclude them (§7.5 default).

    Returns
    -------
    list of (state_path, donor, acceptor)
        ``state_path`` is int64 (still carrying any interior
        :data:`~tether.idealize.NO_STATE` gap); ``donor``/``acceptor`` are float64.
        All three share one length (the molecule's idealized-window extent). Empty
        when the model idealizes no kept molecule.

    Raises
    ------
    KeyError
        No ``/idealization/{model_name}`` in the store.
    ValueError
        The store lacks the requested trace layer.
    """
    from tether.idealize import NO_STATE
    from tether.imaging.extract import read_molecules, read_traces
    from tether.project.core import Project as _Project
    from tether.project.idealize import read_idealization
    from tether.project.labels import curation_filter_mask

    proj = project if isinstance(project, _Project) else _Project.open(project)
    path = proj.path
    donor_key, acceptor_key = resolve_quantity(intensity_quantity)

    molecules = read_molecules(path)
    if molecules.shape[0] == 0:
        return []
    traces = read_traces(path)
    for key in (donor_key, acceptor_key):
        if key not in traces:
            raise ValueError(
                f"{path.name}/traces has no {key!r} layer "
                f"(intensity_quantity={intensity_quantity!r})"
            )
    donor_all = traces[donor_key]
    acceptor_all = traces[acceptor_key]
    trace_len = int(donor_all.shape[1]) if donor_all.ndim == 2 else 0

    stored = read_idealization(proj, model_name)
    state_paths = np.asarray(stored.state_paths)
    if state_paths.ndim != 2 or state_paths.shape[0] == 0:
        return []

    # Join on molecule_id (unique); the molecule_key is not (§7.10). The last row
    # would win a duplicate id, but molecule_id is unique by construction.
    id_to_row = {_to_str(mid): j for j, mid in enumerate(molecules["molecule_id"])}
    accepted = curation_filter_mask(molecules, include_rejected=include_rejected)
    wanted = {str(k) for k in molecule_keys} if molecule_keys is not None else None
    molecule_key_col = molecules["molecule_key"]

    out: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []
    for i, mid in enumerate(stored.molecule_ids):
        j = id_to_row.get(_to_str(mid))
        if j is None or not bool(accepted[j]):
            continue
        if wanted is not None and _to_str(molecule_key_col[j]) not in wanted:
            continue
        state_full = np.asarray(state_paths[i], dtype=np.int64)
        valid = np.nonzero(state_full != NO_STATE)[0]
        if valid.size == 0:
            continue
        lo, hi = int(valid[0]), int(valid[-1]) + 1
        if hi > trace_len:  # state path outruns the shared (padded) /traces width -> skip
            continue
        state_win = np.asarray(state_full[lo:hi], dtype=np.int64)
        donor_win = np.asarray(donor_all[j, lo:hi], dtype=np.float64)
        acceptor_win = np.asarray(acceptor_all[j, lo:hi], dtype=np.float64)
        out.append((state_win, donor_win, acceptor_win))
    return out


def windowed_states(
    project: ProjectRef,
    model_name: str,
    molecule_keys: list[str] | None,
    include_rejected: bool,
) -> list[np.ndarray]:
    """Per-molecule Viterbi **state-path** windows — the state alone, no ``/traces`` I/O.

    Like :func:`windowed_state_and_channels` but returns only each kept molecule's
    int64 state path over its idealized window (the contiguous non-:data:`~tether.idealize.NO_STATE`
    run), for analyses that read the idealized *state* and never the observed signal
    (the TDP; the coming dwell fits). Skipping the ``/traces`` read avoids loading two
    channel datasets per molecule on large stores / interactive filter changes.

    Same ``molecule_id`` join, §7.5 curation filter, and ``molecule_keys`` selection
    as :func:`windowed_state_and_channels`; rows in idealization (fit) order. There is
    **no** trace-length guard (there is no observed signal to misalign with — a state
    path outrunning a re-extracted trace is already STALE and excluded upstream by the
    fresh-idealizations filter).

    Parameters
    ----------
    project
        A :class:`~tether.project.core.Project` or a path to a ``.tether`` store.
    model_name
        Which ``/idealization/{model_name}`` supplies the state paths.
    molecule_keys
        Restrict to these ``molecule_key`` values (``None`` = all), intersected with
        the curation filter.
    include_rejected
        If ``True`` keep rejected molecules; else exclude them (§7.5 default).

    Returns
    -------
    list of np.ndarray
        One int64 state-path window per kept molecule (still carrying any interior
        :data:`~tether.idealize.NO_STATE` gap). Empty when the model idealizes no kept
        molecule.

    Raises
    ------
    KeyError
        No ``/idealization/{model_name}`` in the store.
    """
    from tether.idealize import NO_STATE
    from tether.imaging.extract import read_molecules
    from tether.project.core import Project as _Project
    from tether.project.idealize import read_idealization
    from tether.project.labels import curation_filter_mask

    proj = project if isinstance(project, _Project) else _Project.open(project)
    path = proj.path

    molecules = read_molecules(path)
    if molecules.shape[0] == 0:
        return []

    stored = read_idealization(proj, model_name)
    state_paths = np.asarray(stored.state_paths)
    if state_paths.ndim != 2 or state_paths.shape[0] == 0:
        return []

    id_to_row = {_to_str(mid): j for j, mid in enumerate(molecules["molecule_id"])}
    accepted = curation_filter_mask(molecules, include_rejected=include_rejected)
    wanted = {str(k) for k in molecule_keys} if molecule_keys is not None else None
    molecule_key_col = molecules["molecule_key"]

    out: list[np.ndarray] = []
    for i, mid in enumerate(stored.molecule_ids):
        j = id_to_row.get(_to_str(mid))
        if j is None or not bool(accepted[j]):
            continue
        if wanted is not None and _to_str(molecule_key_col[j]) not in wanted:
            continue
        state_full = np.asarray(state_paths[i], dtype=np.int64)
        valid = np.nonzero(state_full != NO_STATE)[0]
        if valid.size == 0:
            continue
        lo, hi = int(valid[0]), int(valid[-1]) + 1
        out.append(np.asarray(state_full[lo:hi], dtype=np.int64))
    return out
