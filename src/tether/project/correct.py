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
  [`METHOD_CORRECTED`][], [`METHOD_MANUAL`][], [`METHOD_APPARENT_UNAVAILABLE`][]
  (total correction failure), or [`METHOD_APPARENT_TOGGLE`][] (user asked to view
  apparent E despite valid factors). The GUI/analysis derive the per-frame E from this
  tag: an ``apparent-E`` method uses [`tether.fret.apparent_fret`][] (``α=0, γ=1``);
  otherwise [`tether.fret.corrected_fret`][] with the molecule's applied
  ``/molecules.alpha`` and ``/molecules.gamma``.
* ``/molecules.correction_confidence`` — ``1.0`` when a real photophysical correction
  (finite α and γ > 0, estimated or manually entered) was applied, ``0.0`` when the
  molecule fell back to apparent E. (A finer confidence distinguishing a molecule's
  *own* γ from the population-median fallback is deferred to the staleness-flagging
  PR, which persists that per-molecule distinction; ``correction_confidence`` here is a
  provenance flag, not a statistical interval.)
* ``/settings/correction`` — an additive provenance group (like ``/settings/gamma``):
  the effective toggle/overrides, the per-method counts, the ``total_failure`` flag,
  and the app version (NFR-REPRO), with the factors, the molecule count and the
  ``total_failure`` flag of each condition in its ``conditions`` attribute (#624).
  Recomputable — overwritten on each pass.

**The total-correction-failure path (PRD §7.2).** The min-qualifying-traces gate is
applied *before* the population median by the earlier α/γ passes, so a withheld factor
arrives here as the ``NaN`` "no factor computed" sentinel — never a fabricated value.
This writer therefore never lets a missing factor become a NaN in E: a molecule whose
effective α or γ is missing (or γ ≤ 0) is stamped
[`METHOD_APPARENT_UNAVAILABLE`][] and rendered as apparent E (the *expected* case for
the lab's typical pure-FRET acquisitions lacking a clean acceptor-bleach step). That does
not make every frame finite: a frame whose denominator is zero gives NaN, because the ratio
is not defined there ([`tether.fret.corrected_fret`][]). For apparent E that is a frame
with zero total intensity; for corrected E it is a zero ``I_A,corr + γ·I_D,corr``, so a
frame with both channels at zero is NaN in both. The GUI
banner + recovery actions that surface this state are a follow-up GUI PR; this is the
headless decision + provenance they read.

**Per condition (#624).** The §7.2 total-correction-failure rule is evaluated for each
condition as well as for the pass: a condition none of whose molecules carries a
correction is recorded with ``total_failure`` in ``conditions``, while ``total_failure`` of
the summary and of the group still means that no molecule of the pass carries one.

**Apparent-E toggle.** ``apparent_e_only=True`` stamps every analysable molecule
[`METHOD_APPARENT_TOGGLE`][] regardless of factor availability, so a project can be
kept on apparent E deliberately; re-running without the toggle restores the corrected
methods (the toggle round-trips through ``/settings/correction``).

**Manual override.** ``alpha_override`` / ``gamma_override`` supply a manual
per-condition factor (a §7.2 recovery action, or an override of a successfully
estimated factor). The factors are scoped per condition (#624, ADR-0158), so an override
applies to **the condition the caller names** with ``condition_id``: it is written into
``/molecules.alpha`` / ``/molecules.gamma`` of every examined molecule of that condition
(so those fields remain the single source of the *effective applied* factor the
staleness hash keys on) and the molecule is stamped [`METHOD_MANUAL`][]; the other
conditions keep their estimated factors. Where the pass examines one condition only,
``condition_id`` may be left out and the override applies to it, as before #624; with
several conditions an override that names none is refused. The finer per-molecule
fallback scope of a γ override (re-stale fallback molecules only; PRD §5.1) is deferred
to the staleness-flagging PR, which persists own-vs-fallback γ. ``apparent_e_only``
takes precedence over an override (an explicit request to view apparent E wins).

Unlike the α/γ passes, this writer **does not require** the factors to be present —
graceful degradation to apparent E is the whole point (corrections are never required
to *view* traces; PRD §7.2). It only reads ``/molecules`` (no ``/traces`` needed: the
per-frame E is derived downstream). The single-writer ``.lock`` is the caller's
responsibility, mirroring [`tether.project.gamma.compute_gamma`][].
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from tether.io.schema import TABLE
from tether.project._condition_scope import (
    condition_ids,
    finite_or_none,
    group_by_condition,
    read_conditions,
    write_conditions,
)
from tether.project._guarded_publish import guarded_project_write

__all__ = [
    "METHOD_APPARENT_TOGGLE",
    "METHOD_APPARENT_UNAVAILABLE",
    "METHOD_CORRECTED",
    "METHOD_MANUAL",
    "ConditionCorrection",
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
class ConditionCorrection:
    """How the molecules of one condition were corrected in a pass (#624).

    Attributes
    ----------
    condition_id
        The ``/molecules.condition_id`` the molecules share (``""`` when unassigned).
    alpha
        The leakage α applied to the condition's examined molecules — its estimated
        factor or the manual one — or ``None`` when they carry no single finite α.
    gamma
        The condition's γ: the manual factor, else the median (the fallback) its γ pass
        stamped in ``/settings/gamma``, or ``None`` when there is none. Its molecules
        that qualified keep their own γ in ``/molecules.gamma``.
    alpha_source, gamma_source
        ``"manual"`` for an override, ``"estimated"`` for a stored factor, ``None`` when
        the factor is missing.
    n_molecules, n_corrected, n_manual, n_apparent
        As in [`CorrectionSummary`][], for this condition's molecules only.
    total_failure
        ``True`` when no molecule of the condition carries a correction (PRD §7.2).
    """

    condition_id: str
    alpha: float | None
    gamma: float | None
    alpha_source: str | None
    gamma_source: str | None
    n_molecules: int
    n_corrected: int
    n_manual: int
    n_apparent: int
    total_failure: bool


@dataclass(frozen=True)
class CorrectionSummary:
    """Outcome of a [`compute_corrected_fret`][] pass.

    Attributes
    ----------
    n_molecules
        Molecules examined (rows with a valid ``frame_range``).
    n_corrected
        Molecules stamped [`METHOD_CORRECTED`][] (absolute E from estimated α, γ).
    n_manual
        Molecules stamped [`METHOD_MANUAL`][] (a manual α/γ override was applied).
    n_apparent
        Molecules stamped an ``apparent-E`` method — total failure
        ([`METHOD_APPARENT_UNAVAILABLE`][]) or the user toggle
        ([`METHOD_APPARENT_TOGGLE`][]).
    total_failure
        ``True`` when **no** molecule carries a correction (``n_corrected +
        n_manual == 0``) — the whole project falls to apparent E (PRD §7.2). Also
        ``True`` for the degenerate empty project. **Note** this is also ``True``
        under ``apparent_e_only`` (nothing is corrected *by design*, not by
        failure): a consumer surfacing a "correction failed" banner must first
        check ``apparent_e_only`` and treat the deliberate-toggle case differently.
    apparent_e_only
        Whether the apparent-E toggle was in force for this pass.
    source
        Provenance tag stamped into ``/settings/correction``.
    conditions
        One [`ConditionCorrection`][] for each examined condition, in the order the
        condition first appears in ``/molecules`` (#624).
    override_condition_id
        The condition a manual override was applied to, or ``None`` without an override.
    """

    n_molecules: int
    n_corrected: int
    n_manual: int
    n_apparent: int
    total_failure: bool
    apparent_e_only: bool
    source: str
    conditions: tuple[ConditionCorrection, ...] = ()
    override_condition_id: str | None = None


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
    write_guard: Callable[[], None] | None = None,
    movie_id: str | None = None,
    condition_id: str | None = None,
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
        the earlier passes wrote
        ([`compute_leakage_alpha`][tether.project.leakage.compute_leakage_alpha],
        [`compute_gamma`][tether.project.gamma.compute_gamma]); neither is *required* — a missing
        factor degrades that molecule to apparent E.
    apparent_e_only
        Force apparent E for every analysable molecule ([`METHOD_APPARENT_TOGGLE`][]),
        even where valid factors exist. Takes precedence over an override.
    alpha_override, gamma_override
        Manual per-condition factors. When given (and ``apparent_e_only`` is off), the
        value is written into the applied ``/molecules.alpha`` / ``/molecules.gamma``
        for every examined molecule of the condition ``condition_id`` names and the
        molecule is stamped [`METHOD_MANUAL`][].
        ``gamma_override`` must be ``> 0`` (a non-physical γ is rejected).
    condition_id
        The condition a manual override applies to (#624). Needed with an override when
        the pass examines more than one condition; with one it may be left out. It
        does not narrow the pass: every other condition is stamped from its own
        estimated factors. Ignored without an override.
    write_guard
        Optional caller-owned lock/lease check. With a guard, the complete
        ``/molecules`` update and ``/settings/correction`` stamp are written to a
        same-directory sibling project and published together by one final guarded
        atomic replace. Without a guard, the established in-place write path remains.
    movie_id
        Restrict the pass to the molecules of this ``/movies`` row; every other row
        is left untouched and is not counted. ``None`` (default) examines every
        molecule. A Deep-LASI acquisition appended to a project carries its own
        factors, which must not be stamped on the molecules already there (#347,
        ADR-0117). ``/settings/correction`` still records the latest pass.

    Returns
    -------
    CorrectionSummary
        Per-method counts + the ``total_failure`` flag, for logging / the batch runner.

    Raises
    ------
    ValueError
        If ``gamma_override`` is not strictly positive, or ``alpha_override`` /
        ``gamma_override`` is not finite; or if an override names no condition while the
        pass examines several, or names a condition with no examined molecule.
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

    with (
        guarded_project_write(path, write_guard=write_guard, label="correction") as write_path,
        h5py.File(write_path, "r+") as f,
    ):
        table = f[_MOLECULES][TABLE][:]  # full copy; only the correction columns mutate
        frame_range = table["frame_range"]
        alpha_col = table["alpha"]
        gamma_col = table["gamma"]

        # Whether a manual factor was supplied at all (depends only on the arguments).
        has_override = alpha_override is not None or gamma_override is not None

        in_scope = _rows_of_movie(table, movie_id)
        examined = [
            i
            for i in range(table.shape[0])
            # Another movie's molecule is outside this pass (#347); a row with no valid
            # native frames is not analysable. Both are left untouched.
            if in_scope[i] and int(frame_range[i][1]) > int(frame_range[i][0])
        ]
        ids = condition_ids(table)
        groups = group_by_condition(ids, examined)
        target = _override_condition(groups, condition_id) if has_override else None
        counts = {cond: [0, 0, 0] for cond in groups}  # corrected, manual, apparent

        for i in examined:
            n_molecules += 1
            # A manual factor applies to its condition only (#624).
            applies = has_override and ids[i] == target

            # Effective applied factors: a manual override replaces the stored factor
            # (and is persisted below so /molecules stays the source of the applied
            # factor); otherwise the estimated /molecules value is used.
            eff_alpha = (
                alpha_override if applies and alpha_override is not None else float(alpha_col[i])
            )
            eff_gamma = (
                gamma_override if applies and gamma_override is not None else float(gamma_col[i])
            )
            factors_ok = np.isfinite(eff_alpha) and np.isfinite(eff_gamma) and eff_gamma > 0.0

            if apparent_e_only:
                # Explicit request to view apparent E wins over any available factor.
                method, confidence = METHOD_APPARENT_TOGGLE, _CONFIDENCE_APPARENT
                n_apparent += 1
                counts[ids[i]][2] += 1
            elif not factors_ok:
                # Total correction failure: a required factor was withheld (NaN
                # sentinel) or is non-physical → apparent E, never a NaN factor.
                method, confidence = METHOD_APPARENT_UNAVAILABLE, _CONFIDENCE_APPARENT
                n_apparent += 1
                counts[ids[i]][2] += 1
            elif applies:
                method, confidence = METHOD_MANUAL, _CONFIDENCE_CORRECTED
                # Persist the manual factor as the effective applied factor.
                if alpha_override is not None:
                    table["alpha"][i] = alpha_override
                if gamma_override is not None:
                    table["gamma"][i] = gamma_override
                n_manual += 1
                counts[ids[i]][1] += 1
            else:
                method, confidence = METHOD_CORRECTED, _CONFIDENCE_CORRECTED
                n_corrected += 1
                counts[ids[i]][0] += 1

            table["correction_method"][i] = method
            table["correction_confidence"][i] = confidence

        if write_guard is not None:
            write_guard()
        f[_MOLECULES][TABLE][:] = table

        total_failure = (n_corrected + n_manual) == 0
        per_condition = _condition_corrections(
            f,
            table,
            groups,
            counts,
            target=target,
            alpha_override=alpha_override,
            gamma_override=gamma_override,
            apparent_e_only=apparent_e_only,
        )
        if write_guard is not None:
            write_guard()
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
            conditions=per_condition,
            override_condition_id=target,
        )

    return CorrectionSummary(
        n_molecules=n_molecules,
        n_corrected=n_corrected,
        n_manual=n_manual,
        n_apparent=n_apparent,
        total_failure=total_failure,
        apparent_e_only=apparent_e_only,
        source=CORRECTION_SOURCE,
        conditions=tuple(per_condition),
        override_condition_id=target,
    )


def _override_condition(groups: dict[str, list[int]], condition_id: str | None) -> str | None:
    """Return the condition a manual override applies to, or refuse an ambiguous one (#624)."""
    if condition_id is None:
        if len(groups) > 1:
            raise ValueError(
                "a manual alpha or gamma applies to one condition, and this pass examines "
                f"{len(groups)}: name it with condition_id= (one of {sorted(groups)})"
            )
        return next(iter(groups), None)
    if condition_id not in groups:
        raise ValueError(
            f"condition_id {condition_id!r} has no molecule in this pass "
            f"(conditions: {sorted(groups)})"
        )
    return condition_id


def _condition_corrections(
    f: object,
    table: np.ndarray,
    groups: dict[str, list[int]],
    counts: dict[str, list[int]],
    *,
    target: str | None,
    alpha_override: float | None,
    gamma_override: float | None,
    apparent_e_only: bool,
) -> list[ConditionCorrection]:
    """Return each condition's applied factors and counts, for the provenance (#624)."""
    gamma_grp = f[_SETTINGS].get("gamma")  # type: ignore[index]
    stamped = read_conditions(gamma_grp)
    if stamped is not None:
        medians = {str(c["condition_id"]): c.get("gamma") for c in stamped}
    elif gamma_grp is not None and len(groups) == 1:
        # A γ pass from before #624 stamped one median, of the project's one condition.
        medians = {cond: finite_or_none(gamma_grp.attrs.get("gamma")) for cond in groups}
    else:
        medians = {}
    out: list[ConditionCorrection] = []
    for cond, rows in groups.items():
        manual = target == cond and not apparent_e_only
        values = np.unique(np.asarray(table["alpha"][rows], dtype=np.float64))
        alpha = finite_or_none(values[0]) if values.size == 1 else None
        alpha_source = "manual" if manual and alpha_override is not None else None
        if alpha_source is None and alpha is not None:
            alpha_source = "estimated"
        if manual and gamma_override is not None:
            gamma, gamma_source = float(gamma_override), "manual"
        else:
            median = medians.get(cond)
            gamma = finite_or_none(median) if median is not None else None
            gamma_source = "estimated" if gamma is not None else None
        corrected, n_manual, apparent = counts[cond]
        out.append(
            ConditionCorrection(
                condition_id=cond,
                alpha=alpha,
                gamma=gamma,
                alpha_source=alpha_source,
                gamma_source=gamma_source,
                n_molecules=len(rows),
                n_corrected=corrected,
                n_manual=n_manual,
                n_apparent=apparent,
                total_failure=(corrected + n_manual) == 0,
            )
        )
    return out


def _rows_of_movie(table: np.ndarray, movie_id: str | None) -> np.ndarray:
    """Return a boolean mask of the ``/molecules`` rows a ``movie_id``-scoped pass covers."""
    if movie_id is None:
        return np.ones(table.shape[0], dtype=bool)
    ids = table["movie_id"]
    return np.array(
        [(v.decode("utf-8") if isinstance(v, bytes) else str(v)) == movie_id for v in ids],
        dtype=bool,
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
    conditions: list[ConditionCorrection] | None = None,
    override_condition_id: str | None = None,
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
    # Additive (#624): the condition an override applied to ("" when none), and each
    # condition's factors and counts, in a form #926 extends with a source and a version.
    grp.attrs["override_condition_id"] = override_condition_id or ""
    write_conditions(
        grp,
        [
            {
                "condition_id": c.condition_id,
                "alpha": c.alpha,
                "alpha_source": c.alpha_source,
                "gamma": c.gamma,
                "gamma_source": c.gamma_source,
                "n_molecules": c.n_molecules,
                "n_corrected": c.n_corrected,
                "n_manual": c.n_manual,
                "n_apparent": c.n_apparent,
                "total_failure": c.total_failure,
            }
            for c in conditions or []
        ],
    )
    grp.attrs["created_utc"] = datetime.now(UTC).isoformat()
