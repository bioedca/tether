# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Populate per-molecule photobleach frames + auto analysis window (PRD §7.2).

Runs the native single-step detector (:mod:`tether.fret.photobleach`) over a
project's stored intensity traces and writes, into the **already-frozen**
``/molecules`` fields (schema-guard stays green — no structural change):

* ``bleach_frames`` — the per-channel ``(donor, acceptor)`` first-bleach frames
  (absolute frame indices; ``== frame_range[1]`` when a channel does not bleach
  within the trace).
* ``analysis_window`` — the auto default ``(start, first-bleach-of-summed)``
  (Appendix B step 6 / §11.2), written **only where the window still equals the
  extraction default** (``analysis_window == frame_range``). A window a curator
  has already narrowed (``!= frame_range``) is a manual override and is left
  untouched — manual bounds win over the auto default.

Detection runs on the **background-subtracted** (``corrected``) traces by
default: tMAVEN's model detects a decay to ``N(0)``, which only holds once the
background pedestal is removed (raw traces keep a large offset and never look
bleached). The window it produces is the one every downstream reader already
consumes via ``analysis_window`` (histograms, cross-correlation, idealization),
so no read path changes.

The single-writer ``.lock`` is the caller's responsibility, mirroring
:func:`tether.project.labels.set_curation_label` (this is a low-level ``r+``
writer; the :class:`~tether.project.core.Project` facade / batch runner holds the
lock).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from tether.fret.photobleach import (
    PB_PRIOR_A,
    PB_PRIOR_B,
    PB_PRIOR_BETA,
    PB_PRIOR_MU,
    detect_photobleach,
)
from tether.io.schema import TABLE
from tether.project.trace_layers import INTENSITY_QUANTITY_LAYERS

__all__ = ["PhotobleachSummary", "compute_photobleach"]

_MOLECULES = "molecules"
_TRACES = "traces"


@dataclass(frozen=True)
class PhotobleachSummary:
    """Outcome of a :func:`compute_photobleach` pass.

    Attributes
    ----------
    n_molecules
        Molecules processed (rows with a valid ``frame_range``).
    n_donor_bleached, n_acceptor_bleached
        How many molecules bleach within the trace in each channel (a first-bleach
        frame strictly before the trace end).
    n_windows_autoset
        How many analysis windows were set to the auto default (i.e. were still at
        the extraction default and the summed intensity bleaches before the end).
    intensity_quantity
        Which ``/traces`` layer the detection ran on.
    """

    n_molecules: int
    n_donor_bleached: int
    n_acceptor_bleached: int
    n_windows_autoset: int
    intensity_quantity: str


def compute_photobleach(
    project_path: str | Path,
    *,
    intensity_quantity: str = "corrected",
    a: float = PB_PRIOR_A,
    b: float = PB_PRIOR_B,
    beta: float = PB_PRIOR_BETA,
    mu: float = PB_PRIOR_MU,
) -> PhotobleachSummary:
    """Detect per-channel photobleaching and store frames + the auto window.

    Parameters
    ----------
    project_path
        The ``.tether`` project to update (opened ``r+``).
    intensity_quantity
        Which ``/traces`` layer to detect on — ``"corrected"`` (default,
        background-subtracted) or ``"raw"``.
    a, b, beta, mu
        Normal-inverse-Gamma priors for the single-step model (defaults are the
        frozen PRD §11.2 values ``a = b = beta = 1``, ``mu = 1000``).

    Returns
    -------
    PhotobleachSummary
        Per-pass counts, for logging / the batch runner's summary.

    Raises
    ------
    ValueError
        If ``intensity_quantity`` is not a known ``/traces`` layer.
    KeyError
        If the project lacks the selected donor/acceptor trace layer.
    """
    import h5py  # noqa: PLC0415

    if intensity_quantity not in INTENSITY_QUANTITY_LAYERS:
        raise ValueError(
            f"intensity_quantity must be one of {sorted(INTENSITY_QUANTITY_LAYERS)}, "
            f"got {intensity_quantity!r}"
        )
    donor_layer, acceptor_layer = INTENSITY_QUANTITY_LAYERS[intensity_quantity]
    path = Path(project_path)

    n_molecules = 0
    n_donor = 0
    n_acceptor = 0
    n_windows = 0

    with h5py.File(path, "r+") as f:
        traces_grp = f[_TRACES]
        for layer in (donor_layer, acceptor_layer):
            if layer not in traces_grp:
                raise KeyError(f"project has no /traces/{layer}; run extraction first")
        donor_all = traces_grp[donor_layer][:]
        acceptor_all = traces_grp[acceptor_layer][:]

        table = f[_MOLECULES][TABLE][:]  # a full copy; only two columns are mutated
        frame_range = table["frame_range"]
        analysis_window = table["analysis_window"]
        bleach_frames = table["bleach_frames"]

        for i in range(table.shape[0]):
            start, end = int(frame_range[i][0]), int(frame_range[i][1])
            if end <= start:
                continue  # no valid native frames — leave the -1 sentinel
            donor = np.asarray(donor_all[i, start:end], dtype=np.float64)
            acceptor = np.asarray(acceptor_all[i, start:end], dtype=np.float64)
            res = detect_photobleach(donor, acceptor, a=a, b=b, beta=beta, mu=mu)

            donor_pb = start + res.donor_pb
            acceptor_pb = start + res.acceptor_pb
            sum_pb = start + res.sum_pb
            bleach_frames[i] = (donor_pb, acceptor_pb)

            n_molecules += 1
            n_donor += int(donor_pb < end)
            n_acceptor += int(acceptor_pb < end)

            # Auto-default the window only if it is still the untouched extraction
            # default (== frame_range); a curator's manual narrowing wins. Skip a
            # summed signal bleached from the first frame (sum_pb == start): a
            # zero-length (start, start) window is indistinguishable from "unset"
            # downstream (readers treat hi <= lo as unset and widen to frame_range),
            # so it would wrongly re-expand to the full extent. Leaving the default
            # and letting the (start, start) bleach_frames flag the dark trace is the
            # honest encoding (an empty window is not representable in this schema).
            still_default = (
                int(analysis_window[i][0]) == start and int(analysis_window[i][1]) == end
            )
            if still_default and sum_pb > start:
                analysis_window[i] = (start, sum_pb)
                n_windows += int(sum_pb < end)

        f[_MOLECULES][TABLE][:] = table

    return PhotobleachSummary(
        n_molecules=n_molecules,
        n_donor_bleached=n_donor,
        n_acceptor_bleached=n_acceptor,
        n_windows_autoset=n_windows,
        intensity_quantity=intensity_quantity,
    )
