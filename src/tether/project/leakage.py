# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Estimate + store a project's donor→acceptor leakage α (M3, FR-CORRECT).

Runs the post-acceptor-bleach-tail leakage estimator
([`tether.fret.leakage`][]) over a project's stored intensity traces and writes:

* ``/molecules.alpha`` — the applied leakage factor, into the **already-frozen**
  ``/molecules`` field (schema-guard stays green — no structural change). Leakage α
  is a per-condition instrument/dye property, so each condition's median factor is
  written to every processed molecule of that condition (PRD §7.2 / §5.1).
* ``/settings/leakage`` — an additive provenance group (like ``/settings/extraction``):
  the source estimator, the effective gates, ``n_qualifying``, and the app version
  (NFR-REPRO), with the factor and the molecule counts of each condition in its
  ``conditions`` attribute. Recomputable — overwritten on each pass.

**Per condition (#624, ADR-0158).** The molecules are grouped by ``condition_id`` and the
estimator runs once for each group, so a condition's α is the α the same code gives when
that condition is alone in a project, and a project with one condition gives the numbers it
gave before. The top-level ``alpha`` attribute and [`LeakageAlphaSummary.alpha`][] carry the
factor only when the project holds one condition; with several they are ``NaN`` and ``None``,
and the factors are in ``conditions`` and [`LeakageAlphaSummary.conditions`][].

The tail estimate needs the per-channel first-bleach frames written by
[`tether.project.photobleach.compute_photobleach`][], so that pass must run first.
Detection and this estimate both run on the **background-subtracted** (``corrected``)
traces by default.

A condition's factor is **withheld** (its processed ``/molecules.alpha`` rows reset to
``NaN``) when fewer than ``min_qualifying_traces`` of its molecules yield a valid donor-only
tail —
a per-condition α is only trustworthy with enough donor-only tails behind it, and a
fabricated one would silently bias every corrected E (PRD §7.2 total-failure path;
§Data-gaps). Because γ is derived from leakage-corrected intensities, withholding α
also resets that condition's processed ``/molecules.gamma`` rows to ``NaN`` so a downstream
factor from the superseded α cannot remain applied, and removes ``/settings/gamma`` so no
provenance remains for that factor (#549); the next γ pass writes it again. The leakage
provenance group is still stamped (``withheld = True`` when no condition has a factor) so the
attempt is auditable.

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
[`tether.project.photobleach.compute_photobleach`][] (a low-level ``r+`` writer;
the [`Project`][tether.project.core.Project] facade / batch runner holds the lock).
"""

from __future__ import annotations

from collections.abc import Callable
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
from tether.project._condition_scope import (
    condition_ids,
    finite_or_none,
    group_by_condition,
    write_conditions,
)
from tether.project._guarded_publish import guarded_project_write
from tether.project.trace_layers import INTENSITY_QUANTITY_LAYERS

__all__ = ["ConditionLeakage", "LeakageAlphaSummary", "compute_leakage_alpha"]

_MOLECULES = "molecules"
_TRACES = "traces"
_SETTINGS = "settings"
_LEAKAGE_SETTINGS = "leakage"
#: The γ pass's provenance group, removed when a withheld α invalidates γ (#549);
#: same name as ``tether.project.gamma._GAMMA_SETTINGS``.
_GAMMA_SETTINGS = "gamma"

#: ``/settings/leakage`` ``source`` value — the tail estimator's provenance tag.
LEAKAGE_SOURCE_TAIL = "post-acceptor-bleach-tail"


@dataclass(frozen=True)
class ConditionLeakage:
    """The leakage α of one condition in a [`compute_leakage_alpha`][] pass (#624).

    Attributes
    ----------
    condition_id
        The ``/molecules.condition_id`` the molecules share (``""`` when unassigned).
    n_molecules
        Molecules of the condition examined.
    n_qualifying
        Of those, the molecules that yielded a valid per-trace donor-only-tail α.
    alpha
        The condition's factor (median of its qualifying per-trace α), or ``None`` when
        withheld (< ``min_qualifying_traces`` qualified).
    """

    condition_id: str
    n_molecules: int
    n_qualifying: int
    alpha: float | None

    @property
    def applied(self) -> bool:
        """Whether the condition's factor was applied (``alpha is not None``)."""
        return self.alpha is not None


@dataclass(frozen=True)
class LeakageAlphaSummary:
    """Outcome of a [`compute_leakage_alpha`][] pass.

    Attributes
    ----------
    n_molecules
        Molecules examined (rows with a valid ``frame_range``), over every condition.
    n_qualifying
        Molecules that yielded a valid per-trace donor-only-tail α, over every condition.
    alpha
        With one condition, its applied leakage factor (median of the qualifying
        per-trace α), or ``None`` when withheld (< ``min_qualifying_traces`` qualified).
        With several conditions always ``None``: read ``conditions``.
    applied
        Whether a factor was applied to at least one condition. A condition whose
        factor was withheld has its processed ``/molecules.alpha`` and downstream
        ``gamma`` rows cleared to ``NaN``, and ``/settings/gamma`` is removed.
    source
        Estimator provenance tag stamped into ``/settings/leakage``.
    intensity_quantity
        Which ``/traces`` layer the estimate ran on.
    conditions
        One [`ConditionLeakage`][] for each condition, in the order the condition first
        appears in ``/molecules`` (#624).
    """

    n_molecules: int
    n_qualifying: int
    alpha: float | None
    applied: bool
    source: str
    intensity_quantity: str
    conditions: tuple[ConditionLeakage, ...] = ()


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
    write_guard: Callable[[], None] | None = None,
) -> LeakageAlphaSummary:
    """Estimate each condition's leakage α from post-acceptor-bleach tails and store it.

    The molecules are grouped by ``condition_id`` and the estimator runs on each group
    with the same gates (#624, ADR-0158).

    Parameters
    ----------
    project_path
        The ``.tether`` project to update (opened ``r+``). Requires per-channel
        ``bleach_frames`` (run
        [`compute_photobleach`][tether.project.photobleach.compute_photobleach]
        first).
    intensity_quantity
        Which ``/traces`` layer to estimate on — ``"corrected"`` (default,
        background-subtracted) or ``"raw"``.
    min_window_frames
        Reject a donor-only tail shorter than this (PRD §11.2, default 20).
    ceiling
        Reject a per-trace α outside ``[0, ceiling]`` (PRD §11.2, default 0.3).
    min_qualifying_traces
        Withhold a condition's α below this many qualifying traces of that condition
        (PRD §11.2, default 10).
    write_guard
        Optional caller-owned lock/lease check. With a guard, the complete
        ``/molecules.alpha`` update, any downstream ``gamma`` invalidation (rows and
        ``/settings/gamma``), and ``/settings/leakage`` stamp are written to a
        same-directory sibling project and published together by one final guarded
        atomic replace. Without a guard,
        the established in-place write path remains.

    Returns
    -------
    LeakageAlphaSummary
        The estimate + per-pass counts, for logging / the batch runner's summary.

    Raises
    ------
    ValueError
        If ``intensity_quantity`` is not a known ``/traces`` layer, or if a
        molecule's ``bleach_frames`` are still the undetected sentinel (run
        [`compute_photobleach`][tether.project.photobleach.compute_photobleach] first).
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

    with (
        guarded_project_write(path, write_guard=write_guard, label="leakage") as write_path,
        h5py.File(write_path, "r+") as f,
    ):
        traces_grp = f[_TRACES]
        for layer in (donor_layer, acceptor_layer):
            if layer not in traces_grp:
                raise KeyError(f"project has no /traces/{layer}; run extraction first")
        donor_all = traces_grp[donor_layer][:]
        acceptor_all = traces_grp[acceptor_layer][:]

        # Full copy; alpha is updated every pass, and a withheld alpha also
        # invalidates gamma because gamma was derived from leakage-corrected traces.
        table = f[_MOLECULES][TABLE][:]
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
            donor_pb_abs = int(bleach_frames[i][0])
            acceptor_pb_abs = int(bleach_frames[i][1])
            # A real first-bleach frame is >= start >= 0; a negative value is the
            # extraction "undetected" sentinel (-1), i.e. compute_photobleach has not
            # run for this molecule. Fail fast with a clear prerequisite message
            # rather than silently withholding α (every tail would collapse to a
            # zero-length window), mirroring the "run extraction first" guard above.
            if donor_pb_abs < 0 or acceptor_pb_abs < 0:
                raise ValueError(
                    f"/molecules row {i} has no photobleach frames "
                    f"(bleach_frames={(donor_pb_abs, acceptor_pb_abs)}, the undetected "
                    "sentinel); run compute_photobleach() before compute_leakage_alpha()"
                )
            # bleach_frames are absolute (start + local pb, PR #74); convert to the
            # local frame index within this trace slice and clamp to [0, n_local].
            n_local = end - start
            donor_traces.append(np.asarray(donor_all[i, start:end], dtype=np.float64))
            acceptor_traces.append(np.asarray(acceptor_all[i, start:end], dtype=np.float64))
            donor_pbs.append(int(np.clip(donor_pb_abs - start, 0, n_local)))
            acceptor_pbs.append(int(np.clip(acceptor_pb_abs - start, 0, n_local)))
            processed_rows.append(i)

        # One estimate for each condition, on that condition's molecules only (#624).
        ids = condition_ids(table)
        local = {row: k for k, row in enumerate(processed_rows)}
        per_condition: list[ConditionLeakage] = []
        for cond, rows in group_by_condition(ids, processed_rows).items():
            picks = [local[row] for row in rows]
            estimate = estimate_leakage_alpha(
                [donor_traces[k] for k in picks],
                [acceptor_traces[k] for k in picks],
                [acceptor_pbs[k] for k in picks],
                [donor_pbs[k] for k in picks],
                min_window_frames=min_window_frames,
                ceiling=ceiling,
                min_qualifying_traces=min_qualifying_traces,
            )
            per_condition.append(
                ConditionLeakage(cond, estimate.n_traces, estimate.n_qualifying, estimate.alpha)
            )
            for i in rows:
                table["alpha"][i] = estimate.alpha if estimate.alpha is not None else np.nan
                if estimate.alpha is None:
                    table["gamma"][i] = np.nan

        n_molecules = sum(c.n_molecules for c in per_condition)
        n_qualifying = sum(c.n_qualifying for c in per_condition)
        if len(per_condition) == 1:
            alpha = per_condition[0].alpha
        elif per_condition:
            alpha = None  # several factors; none of them is the project's
        else:
            # No molecule was examined: the estimator withholds on an empty set.
            alpha = estimate_leakage_alpha(
                [], [], [], [], min_qualifying_traces=min_qualifying_traces
            ).alpha
        applied = any(c.applied for c in per_condition)
        any_withheld = not per_condition or not all(c.applied for c in per_condition)

        if write_guard is not None:
            write_guard()
        f[_MOLECULES][TABLE][:] = table

        if any_withheld:
            if write_guard is not None:
                write_guard()
            _drop_gamma_settings(f)

        if write_guard is not None:
            write_guard()
        _stamp_leakage_settings(
            f,
            alpha=alpha,
            withheld=not applied,
            n_qualifying=n_qualifying,
            n_molecules=n_molecules,
            min_window_frames=min_window_frames,
            ceiling=ceiling,
            min_qualifying_traces=min_qualifying_traces,
            intensity_quantity=intensity_quantity,
            conditions=per_condition,
        )

    return LeakageAlphaSummary(
        n_molecules=n_molecules,
        n_qualifying=n_qualifying,
        alpha=alpha,
        applied=applied,
        source=LEAKAGE_SOURCE_TAIL,
        intensity_quantity=intensity_quantity,
        conditions=tuple(per_condition),
    )


def _drop_gamma_settings(f: object) -> None:
    """Remove ``/settings/gamma`` after a withheld pass has invalidated γ (#549).

    The withheld pass resets processed ``/molecules.gamma`` rows to ``NaN`` and the
    documented workflow then skips [`compute_gamma`][tether.project.gamma.compute_gamma], so the
    previous γ pass's group would otherwise describe a factor no molecule carries.
    The group is optional and recomputable (absent in a fresh store, rewritten by every
    γ pass), so removing it changes no frozen structure; an invalidated marker would
    have been a new schema field. Absence keeps its existing meaning: no current γ pass.
    """
    settings = f[_SETTINGS]  # type: ignore[index]
    if _GAMMA_SETTINGS in settings:
        del settings[_GAMMA_SETTINGS]
    # The γ evidence table was measured with the superseded α too (#927). The decisions
    # of people and recipes stay: the next γ pass reads them again.
    from tether.project.gamma_contributors import drop_contributors  # noqa: PLC0415

    drop_contributors(f)


def _stamp_leakage_settings(
    f: object,
    *,
    alpha: float | None,
    withheld: bool,
    n_qualifying: int,
    n_molecules: int,
    min_window_frames: int,
    ceiling: float,
    min_qualifying_traces: int,
    intensity_quantity: str,
    conditions: list[ConditionLeakage],
) -> None:
    """Write the ``/settings/leakage`` provenance group (additive; recomputable).

    Mirrors ``/settings/extraction``: an additive child of the frozen ``/settings``
    container recording how the leakage α was derived (NFR-REPRO). Overwritten on
    each pass so the stamp always reflects the latest computation.

    ``withheld`` is true when no condition has a factor. ``conditions`` (#624) records each
    condition's factor and counts; ``alpha`` is ``NaN`` whenever there is not exactly one.
    """
    settings = f[_SETTINGS]  # type: ignore[index]
    if _LEAKAGE_SETTINGS in settings:
        del settings[_LEAKAGE_SETTINGS]
    grp = settings.create_group(_LEAKAGE_SETTINGS, track_order=True)
    grp.attrs["app_version"] = _app_version()
    grp.attrs["source"] = LEAKAGE_SOURCE_TAIL
    # NaN is the honest "no factor derived" marker (HDF5 float attr has no None).
    grp.attrs["alpha"] = float(alpha) if alpha is not None else float("nan")
    grp.attrs["withheld"] = bool(withheld)
    grp.attrs["n_qualifying"] = int(n_qualifying)
    grp.attrs["n_molecules"] = int(n_molecules)
    grp.attrs["min_window_frames"] = int(min_window_frames)
    grp.attrs["ceiling"] = float(ceiling)
    grp.attrs["min_qualifying_traces"] = int(min_qualifying_traces)
    grp.attrs["intensity_quantity"] = intensity_quantity
    # Additive (#624): each condition's factor, written so #926 can add its source and version.
    write_conditions(
        grp,
        [
            {
                "condition_id": c.condition_id,
                "alpha": finite_or_none(c.alpha),
                "withheld": not c.applied,
                "n_molecules": c.n_molecules,
                "n_qualifying": c.n_qualifying,
                "source": LEAKAGE_SOURCE_TAIL,
            }
            for c in conditions
        ],
    )
    grp.attrs["created_utc"] = datetime.now(UTC).isoformat()
