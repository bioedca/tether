# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Estimate + store a project's detection-correction factor γ (M3, FR-CORRECT).

Runs the acceptor-bleach-step γ estimator ([`tether.fret.gamma`][]) over a
project's stored intensity traces and writes:

* ``/molecules.gamma`` — the applied γ, into the **already-frozen** ``/molecules``
  field (schema-guard stays green — no structural change). Unlike leakage α (one
  per-condition factor), γ is **per-molecule**: a qualifying molecule keeps its own
  γ; a molecule that fails the gates takes the **population-median fallback**
  (PRD §5.1, §7.2, Appendix B.2 step 4). That per-molecule/fallback split is what the
  later staleness scope re-stales on a γ-median shift.
* ``/settings/gamma`` — an additive provenance group (like ``/settings/leakage``):
  the effective gates, the dataset median, ``n_qualifying``, ``n_fallback``, the
  clean-step counts and their warning (ADR-0134), and the app version (NFR-REPRO),
  with each condition's median and counts in its ``conditions`` attribute (#624).
  Recomputable — overwritten on each pass.
* ``/settings/gamma_contributors`` — the γ evidence table, one row for each examined
  molecule ([`tether.project.gamma_contributors`][], #927). A molecule that a person or a
  recipe excluded there is left out of the median and takes the fallback.

γ is the last correction step (``background → leakage α → δ(=0) → γ``), so it needs
both prerequisites in place:

* the per-channel first-bleach frames from
  [`tether.project.photobleach.compute_photobleach`][] (the acceptor step γ is
  measured across); and
* the leakage factor ``/molecules.alpha`` from
  [`tether.project.leakage.compute_leakage_alpha`][] (γ is measured on
  leakage-corrected intensities).

Both are enforced with a clear prerequisite error rather than silently proceeding on
uncorrected data. A legitimately *withheld* leakage (α never computed) means the
correction chain has already fallen to apparent-E, so the total-failure path — not
this writer — is the caller's next step (PRD §7.2).

**Per condition (#624, ADR-NNNN).** The molecules are grouped by ``condition_id`` and the
estimator runs once for each group: a condition's median, its fallback and its gates are
those the same code gives when the condition is alone in a project, and a project with one
condition gives the numbers it gave before. The top-level ``gamma`` attribute and
[`GammaSummary.gamma`][] carry the median only when the project holds one condition; with
several they are ``NaN`` and ``None``, and the medians are in ``conditions`` and
[`GammaSummary.conditions`][]. A condition whose leakage pass withheld α (its rows hold the
``NaN`` sentinel and ``/settings/leakage`` records it withheld) is not examined: its γ is
withheld with it, while the other conditions are estimated. A missing α outside such a
condition, or in every condition, is still the prerequisite error.

A condition's γ is **withheld** (its processed ``/molecules.gamma`` rows reset to ``NaN``)
when fewer than ``min_qualifying_traces`` of its molecules yield a valid acceptor-bleach
step — the typical pure-FRET case lacking clean steps (PRD §7.2 total-failure path;
§Data-gaps). The provenance group is still stamped (``withheld = True``) so the
attempt is auditable.

**Clean steps (ADR-0134).** Of the molecules whose step was measured (both segment
gates passed), those where the donor rose and the acceptor dropped are counted. When
an applied γ rests on fewer than ``min_clean_step_fraction`` clean steps, the summary
and the stamp say so (``few_clean_steps``): the ``(0, ceiling]`` gate may then be keeping
the positive tail of noise. The warning is a reason to check γ, not proof that it is wrong,
and it changes no γ value.

The single-writer ``.lock`` is the caller's responsibility, mirroring
[`tether.project.leakage.compute_leakage_alpha`][] (a low-level ``r+`` writer; the
[`Project`][tether.project.core.Project] facade / batch runner holds the lock).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from tether.fret.gamma import (
    DEFAULT_GAMMA_HALF_WINDOW,
    DEFAULT_MIN_CLEAN_STEP_FRACTION,
    DEFAULT_MIN_QUALIFYING_TRACES,
    DEFAULT_MIN_WINDOW_FRAMES,
    GAMMA_CEILING,
    _check_half_window,
    estimate_gamma,
    few_clean_steps,
)
from tether.io.schema import TABLE
from tether.project import gamma_contributors as contributors
from tether.project._condition_scope import (
    condition_ids,
    finite_or_none,
    group_by_condition,
    read_conditions,
    write_conditions,
)
from tether.project._guarded_publish import guarded_project_write
from tether.project.calibration_windows import resolved_calibration_windows
from tether.project.trace_layers import INTENSITY_QUANTITY_LAYERS

__all__ = ["ConditionGamma", "GammaSummary", "compute_gamma"]

_MOLECULES = "molecules"
_TRACES = "traces"
_SETTINGS = "settings"
_GAMMA_SETTINGS = "gamma"

#: ``/settings/gamma`` ``source`` value — the acceptor-bleach-step estimator's tag.
GAMMA_SOURCE_ACCEPTOR_BLEACH_STEP = "acceptor-bleach-step"

#: ``/settings/gamma`` ``level_window`` values (ADR-0130): the levels were averaged over
#: the whole pre- and post-step segments, or over ``half_window`` frames each side.
GAMMA_LEVEL_WINDOW_WHOLE_SEGMENT = "whole-segment"
GAMMA_LEVEL_WINDOW_HALF_WINDOW = "half-window"


@dataclass(frozen=True)
class ConditionGamma:
    """The γ of one condition in a [`compute_gamma`][] pass (#624).

    Attributes
    ----------
    condition_id
        The ``/molecules.condition_id`` the molecules share (``""`` when unassigned).
    n_molecules
        Molecules of the condition examined; ``0`` when its leakage was withheld.
    n_qualifying, n_fallback, n_excluded, n_step_evaluated, n_step_clean, few_clean_steps
        As in [`GammaSummary`][], for this condition's molecules only.
    gamma
        The condition's median γ (also its fallback), or ``None`` when withheld.
    leakage_withheld
        ``True`` when the condition was not examined because its leakage α was withheld.
    """

    condition_id: str
    n_molecules: int
    n_qualifying: int
    n_fallback: int
    gamma: float | None
    n_excluded: int = 0
    n_step_evaluated: int = 0
    n_step_clean: int = 0
    few_clean_steps: bool = False
    leakage_withheld: bool = False

    @property
    def applied(self) -> bool:
        """Whether the condition's γ was applied (``gamma is not None``)."""
        return self.gamma is not None


@dataclass(frozen=True)
class GammaSummary:
    """Outcome of a [`compute_gamma`][] pass.

    Attributes
    ----------
    n_molecules
        Molecules examined (rows with a valid ``frame_range``), over every condition.
    n_qualifying
        Molecules that yielded a valid per-trace acceptor-bleach-step γ.
    n_fallback
        Molecules that were written their condition's median fallback (examined but did
        not qualify) — ``0`` for a condition whose γ was withheld.
    gamma
        With one condition, its median γ (also the fallback value), or ``None`` when
        withheld (< ``min_qualifying_traces`` qualified). With several conditions always
        ``None``: read ``conditions``.
    applied
        Whether a γ was applied to at least one condition. A withheld condition's
        processed ``/molecules.gamma`` rows are cleared to ``NaN``.
    source
        Estimator provenance tag stamped into ``/settings/gamma``.
    intensity_quantity
        Which ``/traces`` layer the estimate ran on.
    n_step_evaluated
        Molecules whose acceptor-bleach step was measured (both segment gates passed).
    n_step_clean
        Of those, the steps where the donor rose and the acceptor dropped (ADR-0134).
    few_clean_steps
        ``True`` when γ was applied and fewer than ``min_clean_step_fraction`` of the
        evaluated steps are clean: the γ is reported, but may rest largely on noise.
        Always ``False`` when γ was withheld. Changes no γ value.
    n_excluded
        Molecules that a person or a recipe excluded from the estimate (#927); they are
        not counted in ``n_qualifying``.
    conditions
        One [`ConditionGamma`][] for each condition, in the order the condition first
        appears in ``/molecules`` (#624).
    """

    n_molecules: int
    n_qualifying: int
    n_fallback: int
    gamma: float | None
    applied: bool
    source: str
    intensity_quantity: str
    n_step_evaluated: int = 0
    n_step_clean: int = 0
    few_clean_steps: bool = False
    n_excluded: int = 0
    conditions: tuple[ConditionGamma, ...] = ()


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
    half_window: int | None = DEFAULT_GAMMA_HALF_WINDOW,
    min_window_frames: int = DEFAULT_MIN_WINDOW_FRAMES,
    ceiling: float = GAMMA_CEILING,
    min_qualifying_traces: int = DEFAULT_MIN_QUALIFYING_TRACES,
    min_clean_step_fraction: float = DEFAULT_MIN_CLEAN_STEP_FRACTION,
    write_guard: Callable[[], None] | None = None,
) -> GammaSummary:
    """Estimate each condition's γ from acceptor-bleach steps and store it per molecule.

    The molecules are grouped by ``condition_id`` and the estimator runs on each group
    with the same gates (#624, ADR-NNNN).

    Parameters
    ----------
    project_path
        The ``.tether`` project to update (opened ``r+``). Requires per-channel
        ``bleach_frames`` (run
        [`compute_photobleach`][tether.project.photobleach.compute_photobleach])
        and ``/molecules.alpha`` (run
        [`compute_leakage_alpha`][tether.project.leakage.compute_leakage_alpha]) first.
    intensity_quantity
        Which ``/traces`` layer to estimate on — ``"corrected"`` (default,
        background-subtracted) or ``"raw"``.
    half_window
        Frames averaged each side of the step for the γ levels (PRD §11.2, default
        20 since ADR-0130). ``None`` averages the whole pre-step FRET segment and the
        whole post-step donor-only segment instead. A zero or negative value raises
        ``ValueError``.
    min_window_frames
        Reject unless both step segments are longer than this (PRD §11.2, default 20).
    ceiling
        Reject a per-trace γ outside ``(0, ceiling]`` (PRD §11.2, default 5).
    min_qualifying_traces
        Withhold a condition's γ below this many qualifying traces of that condition
        (PRD §11.2, default 10).
    min_clean_step_fraction
        Warn (``few_clean_steps``) when an applied γ rests on fewer than this fraction
        of clean evaluated steps (PRD §11.2, default 0.65, ADR-0134). A value outside
        ``[0, 1]`` raises ``ValueError``. Changes no γ value.
    write_guard
        Optional caller-owned lock/lease check. With a guard, the complete
        ``/molecules.gamma`` update and ``/settings/gamma`` stamp are written to a
        same-directory sibling project and published together by one final guarded
        atomic replace. Without a guard, the established in-place write path remains.

    Returns
    -------
    GammaSummary
        The estimate + per-pass counts, for logging / the batch runner's summary.

    Raises
    ------
    ValueError
        If ``intensity_quantity`` is not a known ``/traces`` layer, ``half_window`` is
        not a positive integer or ``None``, ``min_clean_step_fraction`` is outside
        ``[0, 1]``, or if a molecule's
        ``bleach_frames`` are still the undetected sentinel (run
        [`compute_photobleach`][tether.project.photobleach.compute_photobleach] first), or if its
        ``alpha`` is the ``NaN`` "not computed" sentinel outside a condition whose
        leakage was withheld, or in every condition (run
        [`compute_leakage_alpha`][tether.project.leakage.compute_leakage_alpha] first).
    KeyError
        If the project lacks the selected donor/acceptor trace layer.
    """
    return _compute_gamma(
        project_path,
        intensity_quantity=intensity_quantity,
        half_window=half_window,
        min_window_frames=min_window_frames,
        ceiling=ceiling,
        min_qualifying_traces=min_qualifying_traces,
        min_clean_step_fraction=min_clean_step_fraction,
        write_guard=write_guard,
    )


def _compute_gamma(
    project_path: str | Path,
    *,
    intensity_quantity: str = "corrected",
    half_window: int | None = DEFAULT_GAMMA_HALF_WINDOW,
    min_window_frames: int = DEFAULT_MIN_WINDOW_FRAMES,
    ceiling: float = GAMMA_CEILING,
    min_qualifying_traces: int = DEFAULT_MIN_QUALIFYING_TRACES,
    min_clean_step_fraction: float = DEFAULT_MIN_CLEAN_STEP_FRACTION,
    write_guard: Callable[[], None] | None = None,
    decision: Mapping[str, object] | None = None,
) -> GammaSummary:
    """Run [`compute_gamma`][]; ``decision`` is a contributor decision written in the same pass."""
    import h5py  # noqa: PLC0415

    if intensity_quantity not in INTENSITY_QUANTITY_LAYERS:
        raise ValueError(
            f"intensity_quantity must be one of {sorted(INTENSITY_QUANTITY_LAYERS)}, "
            f"got {intensity_quantity!r}"
        )
    donor_layer, acceptor_layer = INTENSITY_QUANTITY_LAYERS[intensity_quantity]
    half_window = _check_half_window(half_window)
    few_clean_steps(0, 0, min_clean_step_fraction)  # refuse a bad fraction before any write
    path = Path(project_path)

    with (
        guarded_project_write(path, write_guard=write_guard, label="gamma") as write_path,
        h5py.File(write_path, "r+") as f,
    ):
        traces_grp = f[_TRACES]
        for layer in (donor_layer, acceptor_layer):
            if layer not in traces_grp:
                raise KeyError(f"project has no /traces/{layer}; run extraction first")
        donor_all = traces_grp[donor_layer][:]
        acceptor_all = traces_grp[acceptor_layer][:]

        table = f[_MOLECULES][TABLE][:]  # full copy; only the gamma column is mutated
        frame_range = table["frame_range"]
        # The step is read inside each row's calibration window (#928, ADR-0156), not its
        # analysis window; the default is the frames a pass read before calibration windows.
        calibration, _ = resolved_calibration_windows(f, table)
        bleach_frames = table["bleach_frames"]
        alpha_col = table["alpha"]
        ids = condition_ids(table)
        # The conditions whose α the last leakage pass withheld (#624): γ is withheld with it.
        leak_conditions = read_conditions(f[_SETTINGS].get("leakage")) or []
        leak_withheld = {str(c["condition_id"]) for c in leak_conditions if c.get("withheld")}
        skipped_rows: list[int] = []

        donor_traces: list[np.ndarray] = []
        acceptor_traces: list[np.ndarray] = []
        alphas: list[float] = []
        acceptor_pbs: list[int] = []
        donor_pbs: list[int] = []
        processed_rows: list[int] = []
        starts: list[int] = []
        steps: list[int] = []
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
            if np.isnan(alpha) and ids[i] in leak_withheld:
                skipped_rows.append(i)  # its condition's α was withheld (#624)
                continue
            if np.isnan(alpha):
                raise ValueError(
                    f"/molecules row {i} has no leakage factor (alpha is NaN); run "
                    "compute_leakage_alpha() before compute_gamma()"
                )
            # bleach_frames are absolute (start + local pb, PR #74); convert to the
            # local frame index within the calibration window and clamp to [0, n_local].
            start, end = int(calibration[i][0]), int(calibration[i][1])
            n_local = end - start
            donor_traces.append(np.asarray(donor_all[i, start:end], dtype=np.float64))
            acceptor_traces.append(np.asarray(acceptor_all[i, start:end], dtype=np.float64))
            alphas.append(alpha)
            donor_pbs.append(int(np.clip(donor_pb_abs - start, 0, n_local)))
            acceptor_pbs.append(int(np.clip(acceptor_pb_abs - start, 0, n_local)))
            processed_rows.append(i)
            starts.append(start)
            steps.append(acceptor_pb_abs)  # the detected step, also outside the window

        if skipped_rows and not processed_rows:
            # No condition has α: the correction order is still the caller's to fix.
            raise ValueError(
                f"/molecules row {skipped_rows[0]} has no leakage factor (alpha is NaN); run "
                "compute_leakage_alpha() before compute_gamma()"
            )

        keys = [_text(table["molecule_key"][row]) for row in processed_rows]
        decisions = contributors.latest_decisions(f)
        if decision is not None:
            key = str(decision["molecule_key"])
            decisions[key] = (
                bool(decision["included"]),
                str(decision["reason"]),
                str(decision["by"]),
                str(decision["actor"]),
            )
        level_window = (
            GAMMA_LEVEL_WINDOW_WHOLE_SEGMENT
            if half_window is None
            else GAMMA_LEVEL_WINDOW_HALF_WINDOW
        )
        contributor_table = np.zeros(len(processed_rows), dtype=contributors.CONTRIBUTORS_DTYPE)
        local = {row: k for k, row in enumerate(processed_rows)}
        per_condition: list[ConditionGamma] = []
        n_excluded = 0
        # One estimate for each condition, on that condition's molecules only (#624).
        for cond, rows in group_by_condition(ids, processed_rows).items():
            picks = [local[row] for row in rows]
            group_keys = [keys[k] for k in picks]
            excluded = contributors.excluded_indices(group_keys, decisions)
            n_excluded += len(excluded)
            estimate = estimate_gamma(
                [donor_traces[k] for k in picks],
                [acceptor_traces[k] for k in picks],
                [alphas[k] for k in picks],
                [acceptor_pbs[k] for k in picks],
                [donor_pbs[k] for k in picks],
                half_window=half_window,
                min_window_frames=min_window_frames,
                ceiling=ceiling,
                min_qualifying_traces=min_qualifying_traces,
                exclude=excluded,
            )
            group_applied = estimate.gamma is not None
            n_fallback = 0
            if group_applied:
                # Per-molecule retention + median fallback: a qualifying molecule keeps
                # its own γ; the rest take its condition's median (PRD §5.1 / §7.2).
                for group_i, row in enumerate(rows):
                    table["gamma"][row] = estimate.effective_gamma(group_i)
                    if estimate.is_fallback(group_i):
                        n_fallback += 1
            else:
                table["gamma"][rows] = np.nan
            # Only an applied γ can mislead; a withheld one is already reported as withheld.
            warn = group_applied and few_clean_steps(
                estimate.n_step_clean, estimate.n_step_evaluated, min_clean_step_fraction
            )
            per_condition.append(
                ConditionGamma(
                    condition_id=cond,
                    n_molecules=estimate.n_traces,
                    n_qualifying=estimate.n_qualifying,
                    n_fallback=n_fallback,
                    gamma=estimate.gamma,
                    n_excluded=len(excluded),
                    n_step_evaluated=estimate.n_step_evaluated,
                    n_step_clean=estimate.n_step_clean,
                    few_clean_steps=warn,
                )
            )
            contributor_table[picks] = contributors.contributor_rows(
                estimate,
                group_keys,
                [starts[k] for k in picks],
                decisions,
                alphas=[alphas[k] for k in picks],
                steps=[steps[k] for k in picks],
                level_window=level_window,
                half_window=0 if half_window is None else int(half_window),
            )
        for cond, rows in group_by_condition(ids, skipped_rows).items():
            table["gamma"][rows] = np.nan
            if not any(c.condition_id == cond for c in per_condition):
                per_condition.append(ConditionGamma(cond, 0, 0, 0, None, leakage_withheld=True))

        # With several medians none of them is the project's; with none nothing was examined.
        gamma = per_condition[0].gamma if len(per_condition) == 1 else None
        applied = any(c.applied for c in per_condition)
        n_molecules = sum(c.n_molecules for c in per_condition)
        n_qualifying = sum(c.n_qualifying for c in per_condition)
        n_fallback_all = sum(c.n_fallback for c in per_condition)
        n_step_evaluated = sum(c.n_step_evaluated for c in per_condition)
        n_step_clean = sum(c.n_step_clean for c in per_condition)
        warn_steps = any(c.few_clean_steps for c in per_condition)
        if write_guard is not None:
            write_guard()
        f[_MOLECULES][TABLE][:] = table

        if write_guard is not None:
            write_guard()
        _stamp_gamma_settings(
            f,
            gamma=gamma,
            withheld=not applied,
            n_qualifying=n_qualifying,
            n_fallback=n_fallback_all,
            n_molecules=n_molecules,
            half_window=half_window,
            min_window_frames=min_window_frames,
            ceiling=ceiling,
            min_qualifying_traces=min_qualifying_traces,
            intensity_quantity=intensity_quantity,
            n_step_evaluated=n_step_evaluated,
            n_step_clean=n_step_clean,
            min_clean_step_fraction=min_clean_step_fraction,
            few_clean_steps=warn_steps,
            n_excluded=n_excluded,
            conditions=per_condition,
        )
        # The guard before the stamp also covers the table, which is written with it.
        contributors.write_contributors(f, contributor_table)
        if decision is not None:
            contributors.append_decision(f, decision)

    return GammaSummary(
        n_molecules=n_molecules,
        n_qualifying=n_qualifying,
        n_fallback=n_fallback_all,
        gamma=gamma,
        applied=applied,
        source=GAMMA_SOURCE_ACCEPTOR_BLEACH_STEP,
        intensity_quantity=intensity_quantity,
        n_step_evaluated=n_step_evaluated,
        n_step_clean=n_step_clean,
        few_clean_steps=warn_steps,
        n_excluded=n_excluded,
        conditions=tuple(per_condition),
    )


def _text(value: object) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _stamp_gamma_settings(
    f: object,
    *,
    gamma: float | None,
    withheld: bool,
    n_qualifying: int,
    n_fallback: int,
    n_molecules: int,
    half_window: int | None,
    min_window_frames: int,
    ceiling: float,
    min_qualifying_traces: int,
    intensity_quantity: str,
    n_step_evaluated: int,
    n_step_clean: int,
    min_clean_step_fraction: float,
    few_clean_steps: bool,
    n_excluded: int = 0,
    conditions: list[ConditionGamma] | None = None,
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
    grp.attrs["withheld"] = bool(withheld)
    grp.attrs["n_qualifying"] = int(n_qualifying)
    grp.attrs["n_fallback"] = int(n_fallback)
    grp.attrs["n_molecules"] = int(n_molecules)
    # ``half_window`` stays an int attribute (additive schema): 0 when the whole
    # segments were averaged, which ``level_window`` names explicitly (ADR-0130).
    grp.attrs["level_window"] = (
        GAMMA_LEVEL_WINDOW_WHOLE_SEGMENT if half_window is None else GAMMA_LEVEL_WINDOW_HALF_WINDOW
    )
    grp.attrs["half_window"] = 0 if half_window is None else int(half_window)
    grp.attrs["min_window_frames"] = int(min_window_frames)
    grp.attrs["ceiling"] = float(ceiling)
    grp.attrs["min_qualifying_traces"] = int(min_qualifying_traces)
    grp.attrs["intensity_quantity"] = intensity_quantity
    # The step-shape evidence behind γ (ADR-0134), additive: a project written before it
    # has none of these four attributes.
    grp.attrs["n_step_evaluated"] = int(n_step_evaluated)
    grp.attrs["n_step_clean"] = int(n_step_clean)
    grp.attrs["min_clean_step_fraction"] = float(min_clean_step_fraction)
    grp.attrs["few_clean_steps"] = bool(few_clean_steps)
    # Additive (#927): the molecules a person or a recipe excluded from the median.
    grp.attrs["n_excluded"] = int(n_excluded)
    # Additive (#624): each condition's median and counts, in a form #926 and #927 extend.
    write_conditions(
        grp,
        [
            {
                "condition_id": c.condition_id,
                "gamma": finite_or_none(c.gamma),
                "withheld": not c.applied,
                "leakage_withheld": c.leakage_withheld,
                "n_molecules": c.n_molecules,
                "n_qualifying": c.n_qualifying,
                "n_fallback": c.n_fallback,
                "n_excluded": c.n_excluded,
                "n_step_evaluated": c.n_step_evaluated,
                "n_step_clean": c.n_step_clean,
                "few_clean_steps": c.few_clean_steps,
                "source": GAMMA_SOURCE_ACCEPTOR_BLEACH_STEP,
            }
            for c in conditions or []
        ],
    )
    grp.attrs["created_utc"] = datetime.now(UTC).isoformat()
