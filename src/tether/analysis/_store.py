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

if TYPE_CHECKING:  # pragma: no cover - typing only
    from os import PathLike

    from tether.project.core import Project

    ProjectRef = Project | str | PathLike[str]

__all__ = ["QUANTITY_KEYS", "resolve_quantity", "windowed_channels"]

#: The ``/traces`` (donor, acceptor) layer names by ``intensity_quantity`` key.
#: ``corrected`` = background-subtracted disk intensity (the apparent-E input at
#: M2 — photophysical α/γ corrections are M3). Mirrors the frozen schema (§5) and
#: ``tether.project.idealize._QUANTITY_KEYS``.
QUANTITY_KEYS = {
    "corrected": ("donor_corrected", "acceptor_corrected"),
    "raw": ("donor_raw", "acceptor_raw"),
}


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


def windowed_channels(
    project: ProjectRef,
    molecule_keys: list[str] | None,
    intensity_quantity: str,
    include_rejected: bool,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Per-molecule ``(donor, acceptor)`` intensity slices over the analysis window.

    Reads ``/molecules`` + ``/traces``, keeps the non-rejected population (unless
    ``include_rejected``), optionally intersects with ``molecule_keys``, and slices
    each kept molecule to its ``analysis_window`` — falling back to ``frame_range``
    when unset ``[0, 0]`` (matching idealization). Returns store order.

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
    analysis_window = molecules["analysis_window"]
    frame_range = molecules["frame_range"]

    pairs: list[tuple[np.ndarray, np.ndarray]] = []
    for i in rows:
        lo, hi = int(analysis_window[i][0]), int(analysis_window[i][1])
        if hi <= lo:  # unset [0, 0] -> native extent (mirrors idealize._windows)
            lo, hi = int(frame_range[i][0]), int(frame_range[i][1])
        donor = np.asarray(donor_all[i, lo:hi], dtype=np.float64)
        acceptor = np.asarray(acceptor_all[i, lo:hi], dtype=np.float64)
        pairs.append((donor, acceptor))
    return pairs
