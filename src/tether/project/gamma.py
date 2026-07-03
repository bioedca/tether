# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Estimate + store a project's detection-correction factor γ (M3, FR-CORRECT).

Runs the acceptor-bleach-step γ estimator (:mod:`tether.fret.gamma`) over a
project's stored intensity traces and writes:

* ``/molecules.gamma`` — the applied γ, into the **already-frozen** ``/molecules``
  field (schema-guard stays green — no structural change). Unlike leakage α (one
  per-condition factor), γ is **per-molecule**: a qualifying molecule keeps its own
  γ; a molecule that fails the gates takes the **population-median fallback**
  (PRD §5.1, §7.2, Appendix B.2 step 4). That per-molecule/fallback split is what the
  later staleness scope re-stales on a γ-median shift.
* ``/settings/gamma`` — an additive provenance group (like ``/settings/leakage``):
  the effective gates, the dataset median, ``n_qualifying``, ``n_fallback``, and the
  app version (NFR-REPRO). Recomputable — overwritten on each pass.

γ is the last correction step (``background → leakage α → δ(=0) → γ``), so it needs
both prerequisites in place:

* the per-channel first-bleach frames from
  :func:`tether.project.photobleach.compute_photobleach` (the acceptor step γ is
  measured across); and
* the leakage factor ``/molecules.alpha`` from
  :func:`tether.project.leakage.compute_leakage_alpha` (γ is measured on
  leakage-corrected intensities).

Both are enforced with a clear prerequisite error rather than silently proceeding on
uncorrected data. A legitimately *withheld* leakage (α never computed) means the
correction chain has already fallen to apparent-E, so the total-failure path — not
this writer — is the caller's next step (PRD §7.2).

The dataset γ is **withheld** (``/molecules.gamma`` left untouched) when fewer than
``min_qualifying_traces`` molecules yield a valid acceptor-bleach step — the typical
pure-FRET case lacking clean steps (PRD §7.2 total-failure path; §Data-gaps). The
provenance group is still stamped (``withheld = True``) so the attempt is auditable.

The single-writer ``.lock`` is the caller's responsibility, mirroring
:func:`tether.project.leakage.compute_leakage_alpha` (a low-level ``r+`` writer; the
:class:`~tether.project.core.Project` facade / batch runner holds the lock).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from tether.fret.gamma import (
    DEFAULT_GAMMA_HALF_WINDOW,
    DEFAULT_MIN_QUALIFYING_TRACES,
    DEFAULT_MIN_WINDOW_FRAMES,
    GAMMA_CEILING,
    estimate_gamma,
)
from tether.io.schema import TABLE
from tether.project.trace_layers import INTENSITY_QUANTITY_LAYERS

__all__ = ["GammaSummary", "compute_gamma"]

_MOLECULES = "molecules"
_TRACES = "traces"
_SETTINGS = "settings"
_GAMMA_SETTINGS = "gamma"

#: ``/settings/gamma`` ``source`` value — the acceptor-bleach-step estimator's tag.
GAMMA_SOURCE_ACCEPTOR_BLEACH_STEP = "acceptor-bleach-step"


@dataclass(frozen=True)
class GammaSummary:
    """Outcome of a :func:`compute_gamma` pass.

    Attributes
    ----------
    n_molecules
        Molecules examined (rows with a valid ``frame_range``).
    n_qualifying
        Molecules that yielded a valid per-trace acceptor-bleach-step γ.
    n_fallback
        Molecules that were written the median fallback (examined but did not
        qualify) — ``0`` when the dataset γ was withheld.
    gamma
        The dataset median γ (also the fallback value), or ``None`` when withheld
        (< ``min_qualifying_traces`` qualified).
    applied
        Whether ``/molecules.gamma`` was written (i.e. ``gamma is not None``).
    source
        Estimator provenance tag stamped into ``/settings/gamma``.
    intensity_quantity
        Which ``/traces`` layer the estimate ran on.
    """

    n_molecules: int
    n_qualifying: int
    n_fallback: int
    gamma: float | None
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


def compute_gamma(
    project_path: str | Path,
    *,
    intensity_quantity: str = "corrected",
    half_window: int = DEFAULT_GAMMA_HALF_WINDOW,
    min_window_frames: int = DEFAULT_MIN_WINDOW_FRAMES,
    ceiling: float = GAMMA_CEILING,
    min_qualifying_traces: int = DEFAULT_MIN_QUALIFYING_TRACES,
) -> GammaSummary:
    """Estimate the dataset γ from acceptor-bleach steps and store it per molecule.

    Parameters
    ----------
    project_path
        The ``.tether`` project to update (opened ``r+``). Requires per-channel
        ``bleach_frames`` (run :func:`~tether.project.photobleach.compute_photobleach`)
        and ``/molecules.alpha`` (run
        :func:`~tether.project.leakage.compute_leakage_alpha`) first.
    intensity_quantity
        Which ``/traces`` layer to estimate on — ``"corrected"`` (default,
        background-subtracted) or ``"raw"``.
    half_window
        γ level tolerance half-window each side of the step (PRD §11.2, default 3).
    min_window_frames
        Reject unless both step segments are longer than this (PRD §11.2, default 20).
    ceiling
        Reject a per-trace γ outside ``(0, ceiling]`` (PRD §11.2, default 5).
    min_qualifying_traces
        Withhold the dataset γ below this many qualifying traces (PRD §11.2,
        default 10).

    Returns
    -------
    GammaSummary
        The estimate + per-pass counts, for logging / the batch runner's summary.

    Raises
    ------
    ValueError
        If ``intensity_quantity`` is not a known ``/traces`` layer, or if a molecule's
        ``bleach_frames`` are still the undetected sentinel (run
        :func:`~tether.project.photobleach.compute_photobleach` first), or if its
        ``alpha`` is the ``NaN`` "not computed" sentinel (run
        :func:`~tether.project.leakage.compute_leakage_alpha` first).
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

        table = f[_MOLECULES][TABLE][:]  # full copy; only the gamma column is mutated
        frame_range = table["frame_range"]
        bleach_frames = table["bleach_frames"]
        alpha_col = table["alpha"]

        donor_traces: list[np.ndarray] = []
        acceptor_traces: list[np.ndarray] = []
        alphas: list[float] = []
        acceptor_pbs: list[int] = []
        donor_pbs: list[int] = []
        processed_rows: list[int] = []
        for i in range(table.shape[0]):
            start, end = int(frame_range[i][0]), int(frame_range[i][1])
            if end <= start:
                continue  # no valid native frames
            donor_pb_abs = int(bleach_frames[i][0])
            acceptor_pb_abs = int(bleach_frames[i][1])
            # A real first-bleach frame is >= start >= 0; a negative value is the
            # extraction "undetected" sentinel (-1), i.e. compute_photobleach has not
            # run for this molecule. Fail fast with a clear prerequisite message
            # (mirrors compute_leakage_alpha).
            if donor_pb_abs < 0 or acceptor_pb_abs < 0:
                raise ValueError(
                    f"/molecules row {i} has no photobleach frames "
                    f"(bleach_frames={(donor_pb_abs, acceptor_pb_abs)}, the undetected "
                    "sentinel); run compute_photobleach() before compute_gamma()"
                )
            alpha = float(alpha_col[i])
            # NaN is the leakage "no factor computed" sentinel (PR #75). γ is measured
            # on leakage-corrected intensities, so α must exist first — enforce the
            # correction order rather than silently correcting with an undefined α.
            if np.isnan(alpha):
                raise ValueError(
                    f"/molecules row {i} has no leakage factor (alpha is NaN); run "
                    "compute_leakage_alpha() before compute_gamma()"
                )
            # bleach_frames are absolute (start + local pb, PR #74); convert to the
            # local frame index within this trace slice and clamp to [0, n_local].
            n_local = end - start
            donor_traces.append(np.asarray(donor_all[i, start:end], dtype=np.float64))
            acceptor_traces.append(np.asarray(acceptor_all[i, start:end], dtype=np.float64))
            alphas.append(alpha)
            donor_pbs.append(int(np.clip(donor_pb_abs - start, 0, n_local)))
            acceptor_pbs.append(int(np.clip(acceptor_pb_abs - start, 0, n_local)))
            processed_rows.append(i)

        estimate = estimate_gamma(
            donor_traces,
            acceptor_traces,
            alphas,
            acceptor_pbs,
            donor_pbs,
            half_window=half_window,
            min_window_frames=min_window_frames,
            ceiling=ceiling,
            min_qualifying_traces=min_qualifying_traces,
        )

        applied = estimate.gamma is not None
        n_fallback = 0
        if applied:
            # Per-molecule retention + median fallback: a qualifying molecule keeps
            # its own γ; the rest take the dataset median (PRD §5.1 / §7.2).
            for local_i, row in enumerate(processed_rows):
                table["gamma"][row] = estimate.effective_gamma(local_i)
                if estimate.is_fallback(local_i):
                    n_fallback += 1
            f[_MOLECULES][TABLE][:] = table

        _stamp_gamma_settings(
            f,
            gamma=estimate.gamma,
            n_qualifying=estimate.n_qualifying,
            n_fallback=n_fallback,
            n_molecules=estimate.n_traces,
            half_window=half_window,
            min_window_frames=min_window_frames,
            ceiling=ceiling,
            min_qualifying_traces=min_qualifying_traces,
            intensity_quantity=intensity_quantity,
        )

    return GammaSummary(
        n_molecules=estimate.n_traces,
        n_qualifying=estimate.n_qualifying,
        n_fallback=n_fallback,
        gamma=estimate.gamma,
        applied=applied,
        source=GAMMA_SOURCE_ACCEPTOR_BLEACH_STEP,
        intensity_quantity=intensity_quantity,
    )


def _stamp_gamma_settings(
    f: object,
    *,
    gamma: float | None,
    n_qualifying: int,
    n_fallback: int,
    n_molecules: int,
    half_window: int,
    min_window_frames: int,
    ceiling: float,
    min_qualifying_traces: int,
    intensity_quantity: str,
) -> None:
    """Write the ``/settings/gamma`` provenance group (additive; recomputable).

    Mirrors ``/settings/leakage``: an additive child of the frozen ``/settings``
    container recording how the γ factor was derived (NFR-REPRO). Overwritten on each
    pass so the stamp always reflects the latest computation.
    """
    settings = f[_SETTINGS]  # type: ignore[index]
    if _GAMMA_SETTINGS in settings:
        del settings[_GAMMA_SETTINGS]
    grp = settings.create_group(_GAMMA_SETTINGS, track_order=True)
    grp.attrs["app_version"] = _app_version()
    grp.attrs["source"] = GAMMA_SOURCE_ACCEPTOR_BLEACH_STEP
    # NaN is the honest "no factor derived" marker (HDF5 float attr has no None).
    grp.attrs["gamma"] = float(gamma) if gamma is not None else float("nan")
    grp.attrs["withheld"] = bool(gamma is None)
    grp.attrs["n_qualifying"] = int(n_qualifying)
    grp.attrs["n_fallback"] = int(n_fallback)
    grp.attrs["n_molecules"] = int(n_molecules)
    grp.attrs["half_window"] = int(half_window)
    grp.attrs["min_window_frames"] = int(min_window_frames)
    grp.attrs["ceiling"] = float(ceiling)
    grp.attrs["min_qualifying_traces"] = int(min_qualifying_traces)
    grp.attrs["intensity_quantity"] = intensity_quantity
    grp.attrs["created_utc"] = datetime.now(UTC).isoformat()
