# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Resolve + stamp each molecule's correction method, with the apparent-E fallback (M3, FR-CORRECT).

The last step of the M3 correction chain (``photobleach → leakage α → γ →
corrected FRET``; PRD §7.2, Appendix B.2). Given the per-molecule factors written by
the earlier passes — the global leakage ``/molecules.alpha`` (PR #75) and the
per-molecule ``/molecules.gamma`` (PR #76) — this writer decides, **per molecule**,
whether an absolute γ-corrected efficiency can be formed and records *how* E is to be
computed downstream:

* ``/molecules.correction_method`` — one of
  :data:`METHOD_CORRECTED`, :data:`METHOD_MANUAL`, :data:`METHOD_APPARENT_UNAVAILABLE`
  (total correction failure), or :data:`METHOD_APPARENT_TOGGLE` (user asked to view
  apparent E despite valid factors). The GUI/analysis derive the per-frame E from this
  tag: an ``apparent-E`` method uses :func:`tether.fret.apparent_fret` (``α=0, γ=1``);
  otherwise :func:`tether.fret.corrected_fret` with the molecule's applied
  ``/molecules.alpha`` and ``/molecules.gamma``.
* ``/molecules.correction_confidence`` — ``1.0`` when a real photophysical correction
  (finite α and γ > 0, estimated or manually entered) was applied, ``0.0`` when the
  molecule fell back to apparent E. (A finer confidence distinguishing a molecule's
  *own* γ from the population-median fallback is deferred to the staleness-flagging
  PR, which persists that per-molecule distinction; ``correction_confidence`` here is a
  provenance flag, not a statistical interval.)
* ``/settings/correction`` — an additive provenance group (like ``/settings/gamma``):
  the effective toggle/overrides, the per-method counts, the ``total_failure`` flag,
  and the app version (NFR-REPRO). Recomputable — overwritten on each pass.

**The total-correction-failure path (PRD §7.2).** The min-qualifying-traces gate is
applied *before* the population median by the earlier α/γ passes, so a withheld factor
arrives here as the ``NaN`` "no factor computed" sentinel — never a fabricated value.
This writer therefore never emits a NaN factor or NaN corrected-E: a molecule whose
effective α or γ is missing (or γ ≤ 0) is stamped
:data:`METHOD_APPARENT_UNAVAILABLE` and rendered as apparent E (the *expected* case for
the lab's typical pure-FRET acquisitions lacking a clean acceptor-bleach step). The GUI
banner + recovery actions that surface this state are a follow-up GUI PR; this is the
headless decision + provenance they read.

**Apparent-E toggle.** ``apparent_e_only=True`` stamps every analysable molecule
:data:`METHOD_APPARENT_TOGGLE` regardless of factor availability, so a project can be
kept on apparent E deliberately; re-running without the toggle restores the corrected
methods (the toggle round-trips through ``/settings/correction``).

**Manual override.** ``alpha_override`` / ``gamma_override`` supply a manual
per-condition factor (a §7.2 recovery action, or an override of a successfully
estimated factor). Since leakage α is a single global factor (PRD §5.1/§7.2) and a
manual γ replaces the per-condition value, an override is applied to **every** examined
molecule: it is written into ``/molecules.alpha`` / ``/molecules.gamma`` (so those
fields remain the single source of the *effective applied* factor the staleness hash
keys on) and the molecule is stamped :data:`METHOD_MANUAL`. The finer per-molecule
fallback scope of a γ override (re-stale fallback molecules only; PRD §5.1) is deferred
to the staleness-flagging PR, which persists own-vs-fallback γ. ``apparent_e_only``
takes precedence over an override (an explicit request to view apparent E wins).

Unlike the α/γ passes, this writer **does not require** the factors to be present —
graceful degradation to apparent E is the whole point (corrections are never required
to *view* traces; PRD §7.2). It only reads ``/molecules`` (no ``/traces`` needed: the
per-frame E is derived downstream). The single-writer ``.lock`` is the caller's
responsibility, mirroring :func:`tether.project.gamma.compute_gamma`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from tether.io.schema import TABLE

__all__ = [
    "METHOD_APPARENT_TOGGLE",
    "METHOD_APPARENT_UNAVAILABLE",
    "METHOD_CORRECTED",
    "METHOD_MANUAL",
    "CorrectionSummary",
    "compute_corrected_fret",
]

_MOLECULES = "molecules"
_SETTINGS = "settings"
_CORRECTION_SETTINGS = "correction"

#: ``correction_method`` value — an absolute γ-corrected E from estimated α and γ.
METHOD_CORRECTED = "corrected"
#: ``correction_method`` value — corrected E from a manually entered α and/or γ.
METHOD_MANUAL = "manual"
#: ``correction_method`` value — total correction failure; retained/displayed as
#: apparent E because a required factor was withheld (PRD §7.2). Matches the PRD's
#: ``method = "apparent-E (corrections unavailable)"`` stamp.
METHOD_APPARENT_UNAVAILABLE = "apparent-E (corrections unavailable)"
#: ``correction_method`` value — apparent E shown by explicit user toggle even though
#: valid correction factors exist (distinct from total failure).
METHOD_APPARENT_TOGGLE = "apparent-E (user toggle)"

#: ``/settings/correction`` ``source`` value — the accurate-FRET procedure tag.
CORRECTION_SOURCE = "accurate-fret"

#: Provenance-flag confidence for a molecule that carries a real correction vs one
#: that fell back to apparent E (see the module docstring — not a statistical CI).
_CONFIDENCE_CORRECTED = 1.0
_CONFIDENCE_APPARENT = 0.0


@dataclass(frozen=True)
class CorrectionSummary:
    """Outcome of a :func:`compute_corrected_fret` pass.

    Attributes
    ----------
    n_molecules
        Molecules examined (rows with a valid ``frame_range``).
    n_corrected
        Molecules stamped :data:`METHOD_CORRECTED` (absolute E from estimated α, γ).
    n_manual
        Molecules stamped :data:`METHOD_MANUAL` (a manual α/γ override was applied).
    n_apparent
        Molecules stamped an ``apparent-E`` method — total failure
        (:data:`METHOD_APPARENT_UNAVAILABLE`) or the user toggle
        (:data:`METHOD_APPARENT_TOGGLE`).
    total_failure
        ``True`` when **no** molecule carries a correction (``n_corrected +
        n_manual == 0``) — the whole project falls to apparent E (PRD §7.2). Also
        ``True`` for the degenerate empty project.
    apparent_e_only
        Whether the apparent-E toggle was in force for this pass.
    source
        Provenance tag stamped into ``/settings/correction``.
    """

    n_molecules: int
    n_corrected: int
    n_manual: int
    n_apparent: int
    total_failure: bool
    apparent_e_only: bool
    source: str


def _app_version() -> str:
    """Best-effort Tether version for the provenance stamp (NFR-REPRO)."""
    try:
        from tether import __version__

        return str(__version__)
    except Exception:  # pragma: no cover - defensive; version is normally present
        return "0.0.0+unknown"


def compute_corrected_fret(
    project_path: str | Path,
    *,
    apparent_e_only: bool = False,
    alpha_override: float | None = None,
    gamma_override: float | None = None,
) -> CorrectionSummary:
    """Resolve + stamp each molecule's correction method and store the provenance.

    Decides, per analysable molecule, whether an absolute γ-corrected efficiency can
    be formed from the applied ``/molecules.alpha`` and ``/molecules.gamma`` — falling
    back to apparent E (never a NaN factor) when a factor was withheld — and writes
    ``/molecules.correction_method`` + ``/molecules.correction_confidence`` and the
    ``/settings/correction`` provenance group.

    Parameters
    ----------
    project_path
        The ``.tether`` project to update (opened ``r+``). Reads the applied factors
        the earlier passes wrote (:func:`~tether.project.leakage.compute_leakage_alpha`,
        :func:`~tether.project.gamma.compute_gamma`); neither is *required* — a missing
        factor degrades that molecule to apparent E.
    apparent_e_only
        Force apparent E for every analysable molecule (:data:`METHOD_APPARENT_TOGGLE`),
        even where valid factors exist. Takes precedence over an override.
    alpha_override, gamma_override
        Manual per-condition factors. When given (and ``apparent_e_only`` is off), the
        value is written into the applied ``/molecules.alpha`` / ``/molecules.gamma``
        for every examined molecule and the molecule is stamped :data:`METHOD_MANUAL`.
        ``gamma_override`` must be ``> 0`` (a non-physical γ is rejected).

    Returns
    -------
    CorrectionSummary
        Per-method counts + the ``total_failure`` flag, for logging / the batch runner.

    Raises
    ------
    ValueError
        If ``gamma_override`` is not strictly positive, or ``alpha_override`` /
        ``gamma_override`` is not finite.
    """
    import h5py  # noqa: PLC0415

    if alpha_override is not None:
        alpha_override = float(alpha_override)
        if not np.isfinite(alpha_override):
            raise ValueError(f"alpha_override must be finite, got {alpha_override!r}")
    if gamma_override is not None:
        gamma_override = float(gamma_override)
        if not np.isfinite(gamma_override) or gamma_override <= 0.0:
            raise ValueError(
                f"gamma_override must be finite and strictly positive, got {gamma_override!r}"
            )

    path = Path(project_path)
    n_molecules = n_corrected = n_manual = n_apparent = 0

    with h5py.File(path, "r+") as f:
        table = f[_MOLECULES][TABLE][:]  # full copy; only the correction columns mutate
        frame_range = table["frame_range"]
        alpha_col = table["alpha"]
        gamma_col = table["gamma"]

        for i in range(table.shape[0]):
            start, end = int(frame_range[i][0]), int(frame_range[i][1])
            if end <= start:
                continue  # no valid native frames → not analysable; leave row untouched
            n_molecules += 1

            # Effective applied factors: a manual override replaces the stored factor
            # (and is persisted below so /molecules stays the source of the applied
            # factor); otherwise the estimated /molecules value is used.
            eff_alpha = alpha_override if alpha_override is not None else float(alpha_col[i])
            eff_gamma = gamma_override if gamma_override is not None else float(gamma_col[i])
            factors_ok = np.isfinite(eff_alpha) and np.isfinite(eff_gamma) and eff_gamma > 0.0
            has_override = alpha_override is not None or gamma_override is not None

            if apparent_e_only:
                # Explicit request to view apparent E wins over any available factor.
                method, confidence = METHOD_APPARENT_TOGGLE, _CONFIDENCE_APPARENT
                n_apparent += 1
            elif not factors_ok:
                # Total correction failure: a required factor was withheld (NaN
                # sentinel) or is non-physical → apparent E, never a NaN factor.
                method, confidence = METHOD_APPARENT_UNAVAILABLE, _CONFIDENCE_APPARENT
                n_apparent += 1
            elif has_override:
                method, confidence = METHOD_MANUAL, _CONFIDENCE_CORRECTED
                # Persist the manual factor as the effective applied factor.
                if alpha_override is not None:
                    table["alpha"][i] = alpha_override
                if gamma_override is not None:
                    table["gamma"][i] = gamma_override
                n_manual += 1
            else:
                method, confidence = METHOD_CORRECTED, _CONFIDENCE_CORRECTED
                n_corrected += 1

            table["correction_method"][i] = method
            table["correction_confidence"][i] = confidence

        f[_MOLECULES][TABLE][:] = table

        total_failure = (n_corrected + n_manual) == 0
        _stamp_correction_settings(
            f,
            apparent_e_only=apparent_e_only,
            alpha_override=alpha_override,
            gamma_override=gamma_override,
            n_molecules=n_molecules,
            n_corrected=n_corrected,
            n_manual=n_manual,
            n_apparent=n_apparent,
            total_failure=total_failure,
        )

    return CorrectionSummary(
        n_molecules=n_molecules,
        n_corrected=n_corrected,
        n_manual=n_manual,
        n_apparent=n_apparent,
        total_failure=total_failure,
        apparent_e_only=apparent_e_only,
        source=CORRECTION_SOURCE,
    )


def _stamp_correction_settings(
    f: object,
    *,
    apparent_e_only: bool,
    alpha_override: float | None,
    gamma_override: float | None,
    n_molecules: int,
    n_corrected: int,
    n_manual: int,
    n_apparent: int,
    total_failure: bool,
) -> None:
    """Write the ``/settings/correction`` provenance group (additive; recomputable).

    Mirrors ``/settings/gamma``: an additive child of the frozen ``/settings``
    container recording how E is to be computed (NFR-REPRO). Overwritten on each pass
    so the stamp always reflects the latest computation. NaN is the honest "not set"
    marker for an absent override (an HDF5 float attr has no ``None``).
    """
    settings = f[_SETTINGS]  # type: ignore[index]
    if _CORRECTION_SETTINGS in settings:
        del settings[_CORRECTION_SETTINGS]
    grp = settings.create_group(_CORRECTION_SETTINGS, track_order=True)
    grp.attrs["app_version"] = _app_version()
    grp.attrs["source"] = CORRECTION_SOURCE
    grp.attrs["apparent_e_only"] = bool(apparent_e_only)
    grp.attrs["alpha_override"] = (
        float(alpha_override) if alpha_override is not None else float("nan")
    )
    grp.attrs["gamma_override"] = (
        float(gamma_override) if gamma_override is not None else float("nan")
    )
    grp.attrs["n_molecules"] = int(n_molecules)
    grp.attrs["n_corrected"] = int(n_corrected)
    grp.attrs["n_manual"] = int(n_manual)
    grp.attrs["n_apparent"] = int(n_apparent)
    grp.attrs["total_failure"] = bool(total_failure)
    grp.attrs["created_utc"] = datetime.now(UTC).isoformat()
