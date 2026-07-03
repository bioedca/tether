# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Estimate + store a project's donor→acceptor leakage α (M3, FR-CORRECT).

Runs the post-acceptor-bleach-tail leakage estimator
(:mod:`tether.fret.leakage`) over a project's stored intensity traces and writes:

* ``/molecules.alpha`` — the applied leakage factor, into the **already-frozen**
  ``/molecules`` field (schema-guard stays green — no structural change). Leakage α
  is a per-condition instrument/dye property, so the single dataset-median factor is
  written to every processed molecule (PRD §7.2 / §5.1).
* ``/settings/leakage`` — an additive provenance group (like ``/settings/extraction``):
  the source estimator, the effective gates, ``n_qualifying``, and the app version
  (NFR-REPRO). Recomputable — overwritten on each pass.

The tail estimate needs the per-channel first-bleach frames written by
:func:`tether.project.photobleach.compute_photobleach`, so that pass must run first.
Detection and this estimate both run on the **background-subtracted** (``corrected``)
traces by default.

The factor is **withheld** (``/molecules.alpha`` left untouched) when fewer than
``min_qualifying_traces`` molecules yield a valid donor-only tail — a per-condition α
is only trustworthy with enough donor-only tails behind it, and a fabricated one
would silently bias every corrected E (PRD §7.2 total-failure path; §Data-gaps). The
provenance group is still stamped (``withheld = True``) so the attempt is auditable.

The separate Cy3-only donor-only *sample* path (a global α from a dedicated
calibration acquisition, cross-checked against this tail α under the §11.2
conjunctive band) is **not** implemented here: the committed
``cy3-donor-only-calibration`` ``.tdat`` carries its per-frame traces only in the
undecoded MCOS ``FileWrapper__`` blob (``DefaultBeta = 0``, no movie, no ``.txt``
export), so computing ``median(I_DA/I_DD)`` from it needs an MCOS trace decoder
(legacy-importer scope). It rides as a follow-up (ADR-0027) — the tail α here is the
always-available primary estimator, and post-acceptor-bleach *is* a per-molecule
donor-only condition.

The single-writer ``.lock`` is the caller's responsibility, mirroring
:func:`tether.project.photobleach.compute_photobleach` (a low-level ``r+`` writer;
the :class:`~tether.project.core.Project` facade / batch runner holds the lock).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from tether.fret.leakage import (
    DEFAULT_MIN_QUALIFYING_TRACES,
    DEFAULT_MIN_WINDOW_FRAMES,
    LEAKAGE_CEILING,
    estimate_leakage_alpha,
)
from tether.io.schema import TABLE
from tether.project.trace_layers import INTENSITY_QUANTITY_LAYERS

__all__ = ["LeakageAlphaSummary", "compute_leakage_alpha"]

_MOLECULES = "molecules"
_TRACES = "traces"
_SETTINGS = "settings"
_LEAKAGE_SETTINGS = "leakage"

#: ``/settings/leakage`` ``source`` value — the tail estimator's provenance tag.
LEAKAGE_SOURCE_TAIL = "post-acceptor-bleach-tail"


@dataclass(frozen=True)
class LeakageAlphaSummary:
    """Outcome of a :func:`compute_leakage_alpha` pass.

    Attributes
    ----------
    n_molecules
        Molecules examined (rows with a valid ``frame_range``).
    n_qualifying
        Molecules that yielded a valid per-trace donor-only-tail α.
    alpha
        The applied dataset leakage factor (median of the qualifying per-trace α),
        or ``None`` when withheld (< ``min_qualifying_traces`` qualified).
    applied
        Whether ``/molecules.alpha`` was written (i.e. ``alpha is not None``).
    source
        Estimator provenance tag stamped into ``/settings/leakage``.
    intensity_quantity
        Which ``/traces`` layer the estimate ran on.
    """

    n_molecules: int
    n_qualifying: int
    alpha: float | None
    applied: bool
    source: str
    intensity_quantity: str


def _app_version() -> str:
    """Best-effort Tether version for the provenance stamp (NFR-REPRO)."""
    try:
        from tether import __version__

        return str(__version__)
    except Exception:  # pragma: no cover - defensive; version is normally present
        return "0.0.0+unknown"


def compute_leakage_alpha(
    project_path: str | Path,
    *,
    intensity_quantity: str = "corrected",
    min_window_frames: int = DEFAULT_MIN_WINDOW_FRAMES,
    ceiling: float = LEAKAGE_CEILING,
    min_qualifying_traces: int = DEFAULT_MIN_QUALIFYING_TRACES,
) -> LeakageAlphaSummary:
    """Estimate the dataset leakage α from post-acceptor-bleach tails and store it.

    Parameters
    ----------
    project_path
        The ``.tether`` project to update (opened ``r+``). Requires per-channel
        ``bleach_frames`` (run :func:`~tether.project.photobleach.compute_photobleach`
        first).
    intensity_quantity
        Which ``/traces`` layer to estimate on — ``"corrected"`` (default,
        background-subtracted) or ``"raw"``.
    min_window_frames
        Reject a donor-only tail shorter than this (PRD §11.2, default 20).
    ceiling
        Reject a per-trace α outside ``[0, ceiling]`` (PRD §11.2, default 0.3).
    min_qualifying_traces
        Withhold the dataset α below this many qualifying traces (PRD §11.2,
        default 10).

    Returns
    -------
    LeakageAlphaSummary
        The estimate + per-pass counts, for logging / the batch runner's summary.

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

    with h5py.File(path, "r+") as f:
        traces_grp = f[_TRACES]
        for layer in (donor_layer, acceptor_layer):
            if layer not in traces_grp:
                raise KeyError(f"project has no /traces/{layer}; run extraction first")
        donor_all = traces_grp[donor_layer][:]
        acceptor_all = traces_grp[acceptor_layer][:]

        table = f[_MOLECULES][TABLE][:]  # full copy; only the alpha column is mutated
        frame_range = table["frame_range"]
        bleach_frames = table["bleach_frames"]

        donor_traces: list[np.ndarray] = []
        acceptor_traces: list[np.ndarray] = []
        acceptor_pbs: list[int] = []
        donor_pbs: list[int] = []
        processed_rows: list[int] = []
        for i in range(table.shape[0]):
            start, end = int(frame_range[i][0]), int(frame_range[i][1])
            if end <= start:
                continue  # no valid native frames
            donor_traces.append(np.asarray(donor_all[i, start:end], dtype=np.float64))
            acceptor_traces.append(np.asarray(acceptor_all[i, start:end], dtype=np.float64))
            # bleach_frames are absolute (start + local pb, PR #74); convert to the
            # local frame index within this trace slice and clamp to [0, n_local].
            n_local = end - start
            donor_pb = int(bleach_frames[i][0]) - start
            acceptor_pb = int(bleach_frames[i][1]) - start
            donor_pbs.append(int(np.clip(donor_pb, 0, n_local)))
            acceptor_pbs.append(int(np.clip(acceptor_pb, 0, n_local)))
            processed_rows.append(i)

        estimate = estimate_leakage_alpha(
            donor_traces,
            acceptor_traces,
            acceptor_pbs,
            donor_pbs,
            min_window_frames=min_window_frames,
            ceiling=ceiling,
            min_qualifying_traces=min_qualifying_traces,
        )

        applied = estimate.alpha is not None
        if applied:
            for i in processed_rows:
                table["alpha"][i] = estimate.alpha
            f[_MOLECULES][TABLE][:] = table

        _stamp_leakage_settings(
            f,
            alpha=estimate.alpha,
            n_qualifying=estimate.n_qualifying,
            n_molecules=estimate.n_traces,
            min_window_frames=min_window_frames,
            ceiling=ceiling,
            min_qualifying_traces=min_qualifying_traces,
            intensity_quantity=intensity_quantity,
        )

    return LeakageAlphaSummary(
        n_molecules=estimate.n_traces,
        n_qualifying=estimate.n_qualifying,
        alpha=estimate.alpha,
        applied=applied,
        source=LEAKAGE_SOURCE_TAIL,
        intensity_quantity=intensity_quantity,
    )


def _stamp_leakage_settings(
    f: object,
    *,
    alpha: float | None,
    n_qualifying: int,
    n_molecules: int,
    min_window_frames: int,
    ceiling: float,
    min_qualifying_traces: int,
    intensity_quantity: str,
) -> None:
    """Write the ``/settings/leakage`` provenance group (additive; recomputable).

    Mirrors ``/settings/extraction``: an additive child of the frozen ``/settings``
    container recording how the leakage α was derived (NFR-REPRO). Overwritten on
    each pass so the stamp always reflects the latest computation.
    """
    settings = f[_SETTINGS]  # type: ignore[index]
    if _LEAKAGE_SETTINGS in settings:
        del settings[_LEAKAGE_SETTINGS]
    grp = settings.create_group(_LEAKAGE_SETTINGS, track_order=True)
    grp.attrs["app_version"] = _app_version()
    grp.attrs["source"] = LEAKAGE_SOURCE_TAIL
    # NaN is the honest "no factor derived" marker (HDF5 float attr has no None).
    grp.attrs["alpha"] = float(alpha) if alpha is not None else float("nan")
    grp.attrs["withheld"] = bool(alpha is None)
    grp.attrs["n_qualifying"] = int(n_qualifying)
    grp.attrs["n_molecules"] = int(n_molecules)
    grp.attrs["min_window_frames"] = int(min_window_frames)
    grp.attrs["ceiling"] = float(ceiling)
    grp.attrs["min_qualifying_traces"] = int(min_qualifying_traces)
    grp.attrs["intensity_quantity"] = intensity_quantity
    grp.attrs["created_utc"] = datetime.now(UTC).isoformat()
