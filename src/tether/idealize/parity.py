# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Idealization-parity metrics + cross-seed tolerance measurement (PRD §7.4, §11.2).

tMAVEN self-reseeds its RNG (``initialize_gmm`` calls ``np.random.seed()``;
``clip_traces`` reseeds from wall-clock), so two idealizations of the *same*
traces never reproduce bit-for-bit (PRD §7.4, §10). Parity is therefore defined
as **statistical agreement within a stated tolerance** on four quantities
[Bronson2009, vandeMeent2014]:

1. **state count** — per-trace agreement on the number of distinct states the
   idealized path actually occupies (exact on ≥ X% of traces);
2. **per-state mean ΔE** — the largest absolute FRET-unit difference between
   matched state levels (≤ Y);
3. **Viterbi per-frame agreement** — the fraction of in-window frames whose
   assigned state matches, after resolving the arbitrary state-label
   permutation (≥ Z);
4. **relative ELBO change** — ``|ΔELBO| / |ELBO|`` (≤ W).

This module holds the **pure** comparison core (:func:`compare_models`,
numpy-only — what CI's ``sidecar.yml`` asserts against the frozen numbers) and a
**sidecar-driven** measurement harness (:func:`measure_spread`, which runs
:func:`tether.idealize.run_vbfret` ≥ N times to *measure* the cross-seed spread
the four §11.2 numbers are frozen from at M0.5). The frozen numbers live in
``schema/parity_tolerance.json`` and PRD §11.2; CI never recomputes them.

State labels are arbitrary across seeds, so every comparison first relabels both
models to a **canonical order — states sorted by ascending mean FRET** — the
standard 1-D-ordered-states alignment for FRET efficiency. Comparisons require
equal state counts; differing counts surface through the state-count metric and
yield ``viterbi=0`` / ``mean_delta=inf`` so they can never be silently scored as
agreeing.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from tether.idealize.driver import (
    NO_STATE,
    StateModel,
    read_model,
    run_vbfret,
    states_from_idealized,
)

if TYPE_CHECKING:
    from os import PathLike

#: Provisional §11.2 defaults (PRD, pre-ratification). The measured spread either
#: replaces these (if wider) or confirms them (if tighter) — see :func:`freeze`.
PROVISIONAL = {
    "state_count_min_fraction": 0.90,  # floor: exact on ≥ 90% of traces
    "state_mean_abs_delta_max": 0.02,  # ceiling: per-state |ΔE| (FRET units)
    "viterbi_min_agreement": 0.95,  # floor: per-frame agreement
    "relative_elbo_max": 0.01,  # ceiling: |ΔELBO| / |ELBO|
}


@dataclass(frozen=True)
class ParityMetrics:
    """The four parity quantities for one ``(reference, test)`` comparison.

    ``state_count_fraction`` and ``viterbi_agreement`` are *higher-is-better*
    fractions in ``[0, 1]``; ``state_mean_abs_delta`` and ``relative_elbo`` are
    *lower-is-better* magnitudes. ``n_states_ref``/``n_states_test`` record the
    fitted state counts so a count mismatch is visible in the record.
    """

    state_count_fraction: float
    state_mean_abs_delta: float
    viterbi_agreement: float
    relative_elbo: float
    n_states_ref: int
    n_states_test: int

    def as_dict(self) -> dict:
        return asdict(self)


def _canonical_order(means: np.ndarray) -> np.ndarray:
    """Return the relabel map sending each original state to its mean-rank.

    ``order[s]`` is the canonical label of original state ``s`` (0 = lowest
    mean). Applying it makes two models with the same states comparable despite
    arbitrary VB label permutations.
    """
    means = np.asarray(means, dtype="float64").reshape(-1)
    ranks = np.empty(means.shape[0], dtype="int64")
    ranks[np.argsort(means, kind="stable")] = np.arange(means.shape[0])
    return ranks


def canonical_state_path(model: StateModel) -> np.ndarray:
    """Per-frame integer state path in canonical (mean-sorted) labels.

    ``(n_molecules, n_frames)`` int64; frames outside a molecule's analysis
    window stay :data:`tether.idealize.NO_STATE`. Derived from the model's
    ``idealized`` levels via nearest-mean assignment, then relabeled so state 0
    is always the lowest-FRET state.
    """
    if model.idealized is None:
        raise ValueError("model has no 'idealized' array to derive a state path")
    raw = states_from_idealized(model.idealized, model.means)
    relabel = _canonical_order(model.means)
    out = np.full(raw.shape, NO_STATE, dtype="int64")
    valid = raw != NO_STATE
    out[valid] = relabel[raw[valid]]
    return out


def _distinct_counts(path: np.ndarray) -> np.ndarray:
    """Number of distinct (non-:data:`NO_STATE`) states each row occupies."""
    counts = np.empty(path.shape[0], dtype="int64")
    for i in range(path.shape[0]):
        row = path[i]
        occ = row[row != NO_STATE]
        counts[i] = np.unique(occ).size
    return counts


def state_count_fraction(ref_path: np.ndarray, test_path: np.ndarray) -> float:
    """Fraction of traces where ref and test occupy the same number of states.

    Only traces with at least one in-window frame in *either* model count toward
    the denominator (an all-empty trace is uninformative, not a disagreement).
    """
    ref_counts = _distinct_counts(ref_path)
    test_counts = _distinct_counts(test_path)
    informative = (ref_counts > 0) | (test_counts > 0)
    if not informative.any():
        return 1.0
    agree = (ref_counts == test_counts) & informative
    return float(agree.sum() / informative.sum())


def state_mean_abs_delta(ref: StateModel, test: StateModel) -> float:
    """Max absolute difference between mean-sorted matched state levels.

    Returns ``inf`` when the state counts differ (no valid one-to-one match), so
    a count mismatch can never read as small-ΔE agreement.
    """
    rm = np.sort(np.asarray(ref.means, dtype="float64").reshape(-1))
    tm = np.sort(np.asarray(test.means, dtype="float64").reshape(-1))
    if rm.shape != tm.shape or rm.size == 0:
        return float("inf")
    return float(np.max(np.abs(rm - tm)))


def viterbi_agreement(ref_path: np.ndarray, test_path: np.ndarray) -> float:
    """Per-frame state-path agreement over frames in-window in *both* models.

    Both paths must already be in canonical labels
    (:func:`canonical_state_path`). Frames where either model is
    :data:`NO_STATE` are excluded. Returns the fraction equal, or ``0.0`` when
    the paths are not frame-aligned (differing shapes) or share no in-window
    frames.
    """
    if ref_path.shape != test_path.shape:
        return 0.0
    both = (ref_path != NO_STATE) & (test_path != NO_STATE)
    if not both.any():
        return 0.0
    return float((ref_path[both] == test_path[both]).sum() / both.sum())


def relative_elbo(ref: StateModel, test: StateModel) -> float:
    """``|ELBO_test - ELBO_ref| / |ELBO_ref|``; ``inf`` if either ELBO is absent.

    A zero reference ELBO (degenerate) also yields ``inf`` rather than a divide
    by zero.
    """
    if ref.elbo is None or test.elbo is None:
        return float("inf")
    denom = abs(float(ref.elbo))
    if denom == 0.0:
        return float("inf")
    return float(abs(float(test.elbo) - float(ref.elbo)) / denom)


def compare_models(ref: StateModel, test: StateModel) -> ParityMetrics:
    """Compute the four parity metrics for ``test`` against ``ref``.

    Pure (numpy-only): the same function CI's ``sidecar.yml`` uses to assert a
    fresh sidecar fit against the committed reference model within the frozen
    §11.2 tolerance. State-count and Viterbi metrics need both models'
    ``idealized`` paths; mean-ΔE and ELBO use the model summaries.
    """
    ref_path = canonical_state_path(ref)
    test_path = canonical_state_path(test)
    n_states_ref = int(np.asarray(ref.means).reshape(-1).size)
    n_states_test = int(np.asarray(test.means).reshape(-1).size)
    # A differing state count is an invalid one-to-one comparison: force Viterbi
    # disagreement (matching the inf returned by state_mean_abs_delta) so an
    # extra unused state can never read as perfect framewise agreement.
    same_count = n_states_ref == n_states_test
    return ParityMetrics(
        state_count_fraction=state_count_fraction(ref_path, test_path),
        state_mean_abs_delta=state_mean_abs_delta(ref, test),
        viterbi_agreement=viterbi_agreement(ref_path, test_path) if same_count else 0.0,
        relative_elbo=relative_elbo(ref, test),
        n_states_ref=n_states_ref,
        n_states_test=n_states_test,
    )


def within_tolerance(metrics: ParityMetrics, tolerance: dict) -> tuple[bool, list[str]]:
    """Check one comparison against a frozen tolerance dict.

    Returns ``(ok, failures)`` where ``failures`` names each violated bound, so a
    CI assertion can report *which* metric drifted, not just that one did. A
    non-finite metric (``nan``/``inf`` — e.g. an incomparable fit or a malformed
    ELBO) is a hard failure, never silently "within tolerance".
    """
    failures: list[str] = []
    nonfinite = [
        name
        for name, value in (
            ("state-count agreement", metrics.state_count_fraction),
            ("per-state |ΔE|", metrics.state_mean_abs_delta),
            ("Viterbi agreement", metrics.viterbi_agreement),
            ("relative ΔELBO", metrics.relative_elbo),
        )
        if not np.isfinite(value)
    ]
    if nonfinite:
        failures.append(f"non-finite metric(s): {', '.join(nonfinite)}")
        return (False, failures)
    if metrics.state_count_fraction < tolerance["state_count_min_fraction"]:
        failures.append(
            f"state-count agreement {metrics.state_count_fraction:.4f} "
            f"< {tolerance['state_count_min_fraction']}"
        )
    if metrics.state_mean_abs_delta > tolerance["state_mean_abs_delta_max"]:
        failures.append(
            f"per-state |ΔE| {metrics.state_mean_abs_delta:.4f} "
            f"> {tolerance['state_mean_abs_delta_max']}"
        )
    if metrics.viterbi_agreement < tolerance["viterbi_min_agreement"]:
        failures.append(
            f"Viterbi agreement {metrics.viterbi_agreement:.4f} "
            f"< {tolerance['viterbi_min_agreement']}"
        )
    if metrics.relative_elbo > tolerance["relative_elbo_max"]:
        failures.append(
            f"relative ΔELBO {metrics.relative_elbo:.4f} > {tolerance['relative_elbo_max']}"
        )
    return (not failures, failures)


# --------------------------------------------------------------------------- #
# Cross-seed spread measurement (sidecar-driven — NOT part of the CI matrix).  #
# --------------------------------------------------------------------------- #


@dataclass
class SpreadSummary:
    """Aggregated worst-case spread of one metric over the replicate runs."""

    name: str
    direction: str  # "floor" (higher better) | "ceiling" (lower better)
    values: list[float] = field(default_factory=list)

    @property
    def worst(self) -> float:
        # Non-finite values are sentinel failures (an incomparable run) and MUST
        # surface as the worst case — never be filtered out — or freeze() could
        # ratify a finite tolerance over invalid comparisons. A ceiling's worst
        # is the failing direction (inf); a floor's is 0.0.
        if not self.values:
            return float("inf") if self.direction == "ceiling" else 0.0
        arr = np.asarray(self.values, dtype="float64")
        if not np.all(np.isfinite(arr)):
            return float("inf") if self.direction == "ceiling" else 0.0
        return float(np.min(arr)) if self.direction == "floor" else float(np.max(arr))

    def percentile(self, q: float) -> float:
        finite = [v for v in self.values if np.isfinite(v)]
        return float(np.percentile(finite, q)) if finite else float("nan")

    def as_dict(self) -> dict:
        return {
            "direction": self.direction,
            "n": len(self.values),
            "min": float(np.min(self.values)) if self.values else None,
            "max": float(np.max(self.values)) if self.values else None,
            "mean": float(np.mean(self.values)) if self.values else None,
            "worst": self.worst,
            "values": [float(v) for v in self.values],
        }


def freeze(spread: dict[str, SpreadSummary], margin: float = 0.5) -> dict:
    """Freeze the four §11.2 numbers from the measured spread.

    Policy (provenance-driven, documented in ``parity_tolerance.json`` + the
    ADR): the frozen bound is the **more permissive** of the provisional §11.2
    default and the measured worst case expanded by a safety ``margin`` — so the
    gate never flakes on an unseen seed yet is never looser than the design
    intent unless the data demands it. ``margin`` widens ceilings by
    ``(1 + margin)×`` and lowers floors by ``margin × (1 − worst)``.
    """
    sc = spread["state_count_fraction"].worst
    md = spread["state_mean_abs_delta"].worst
    va = spread["viterbi_agreement"].worst
    re = spread["relative_elbo"].worst

    def floor(worst: float, default: float) -> float:
        measured = worst - margin * (1.0 - worst)  # widen downward by margin
        return round(min(default, max(0.0, measured)), 4)

    def ceiling(worst: float, default: float) -> float:
        measured = worst * (1.0 + margin)
        return round(max(default, measured), 4)

    return {
        "state_count_min_fraction": floor(sc, PROVISIONAL["state_count_min_fraction"]),
        "state_mean_abs_delta_max": ceiling(md, PROVISIONAL["state_mean_abs_delta_max"]),
        "viterbi_min_agreement": floor(va, PROVISIONAL["viterbi_min_agreement"]),
        "relative_elbo_max": ceiling(re, PROVISIONAL["relative_elbo_max"]),
    }


def measure_spread(
    smd_path: str | PathLike[str],
    *,
    reference: StateModel | str | PathLike[str] | None = None,
    n_runs: int = 20,
    model_type: str = "vbconhmm",
    nstates: int = 4,
    sidecar_python: str | PathLike[str] | None = None,
    scratch_dir: str | PathLike[str] | None = None,
    nrestarts: int | None = None,
    progress: bool = True,
) -> tuple[dict[str, SpreadSummary], list[ParityMetrics]]:
    """Run ``n_runs`` self-reseeded sidecar fits and measure the parity spread.

    Each fit is compared to ``reference`` — a committed reference model
    (``model_281mol.hdf5``) when supplied, else the first run (cross-seed
    self-comparison, for fixtures with no committed model). Returns the
    per-metric :class:`SpreadSummary` map and the raw per-run metrics. This is
    the M0.5 ratification harness — sidecar-only, never run in the CI matrix.

    Raises ``ValueError`` for configurations that would yield zero comparisons
    (so a freeze can never ratify ``0``/``inf`` placeholders): ``n_runs < 1``, or
    ``reference is None`` with ``n_runs < 2`` (the first run is the anchor, not a
    comparison).
    """
    if n_runs < 1:
        raise ValueError("n_runs must be at least 1")
    if reference is None and n_runs < 2:
        raise ValueError("n_runs must be >= 2 when anchoring on the first run (reference=None)")

    smd_path = Path(smd_path)
    scratch = Path(scratch_dir) if scratch_dir else smd_path.parent
    scratch.mkdir(parents=True, exist_ok=True)

    ref_model: StateModel | None = None
    if reference is not None:
        ref_model = reference if isinstance(reference, StateModel) else read_model(reference)

    spread = {
        "state_count_fraction": SpreadSummary("state_count_fraction", "floor"),
        "state_mean_abs_delta": SpreadSummary("state_mean_abs_delta", "ceiling"),
        "viterbi_agreement": SpreadSummary("viterbi_agreement", "floor"),
        "relative_elbo": SpreadSummary("relative_elbo", "ceiling"),
    }
    per_run: list[ParityMetrics] = []

    for i in range(n_runs):
        model_out = scratch / f"{smd_path.stem}.run{i:02d}.model.hdf5"
        result = run_vbfret(
            smd_path,
            sidecar_python=sidecar_python,
            model_type=model_type,
            nstates=nstates,
            nrestarts=nrestarts,
            model_out=model_out,
        )
        if ref_model is None:
            ref_model = result.model  # first run anchors the cross-seed comparison
            if progress:
                print(
                    f"[parity] run {i:02d}: reference anchored "
                    f"(nstates={result.model.nstates}, elbo={result.model.elbo})"
                )
            continue
        m = compare_models(ref_model, result.model)
        per_run.append(m)
        spread["state_count_fraction"].values.append(m.state_count_fraction)
        spread["state_mean_abs_delta"].values.append(m.state_mean_abs_delta)
        spread["viterbi_agreement"].values.append(m.viterbi_agreement)
        spread["relative_elbo"].values.append(m.relative_elbo)
        if progress:
            print(
                f"[parity] run {i:02d}: count={m.state_count_fraction:.3f} "
                f"dE={m.state_mean_abs_delta:.4f} vit={m.viterbi_agreement:.3f} "
                f"dElbo={m.relative_elbo:.4f}"
            )

    return spread, per_run


def load_frozen_tolerance(path: str | PathLike[str], method: str | None = None) -> dict:
    """Read the frozen four-metric tolerance from ``schema/parity_tolerance.json``.

    ``method`` selects a per-method tolerance from ``tolerance_by_method`` when that
    method carries its own measured freeze (e.g. ``"ebhmm"``). A method without one —
    or ``method=None`` (the default, back-compatible) — falls back to the top-level
    (vbconhmm-derived, default) ``tolerance``. ebFRET is frozen separately because its
    empirical-Bayes per-trace state selection is more seed-variable than vbconhmm's,
    so the vbconhmm floor is too tight for it (ADR-0043).
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if method is not None:
        by_method = data.get("tolerance_by_method", {})
        if method in by_method:
            return by_method[method]
    return data["tolerance"]
