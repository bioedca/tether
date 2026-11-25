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

This module holds the **pure** comparison core ([`compare_models`][],
numpy-only — what CI's ``sidecar.yml`` asserts against the frozen numbers) and a
**sidecar-driven** measurement harness ([`measure_spread`][], which runs
[`tether.idealize.run_vbfret`][] ≥ N times to *measure* the cross-seed spread
the four §11.2 numbers are frozen from at M0.5). The frozen numbers live in
``schema/parity_tolerance.json`` and PRD §11.2; CI never recomputes them.

Two further comparisons sit beside the four (issue #545, ADR-0110). **Per-trace
vbFRET** has no ensemble states — each trace carries its own model — so
[`compare_trace_models`][] computes the same four quantities trace by trace
(see its docstring for how). And the population methods (consensus VB-HMM,
ebFRET) are also compared on their **transition matrix**
([`transition_matrix_abs_delta`][]), a fifth quantity with its own frozen
ceiling under ``$.transition_matrix`` ([`load_transition_matrix_tolerance`][]).

State labels are arbitrary across seeds, so every comparison first relabels both
models to a **canonical order — states sorted by ascending mean FRET** — the
standard 1-D-ordered-states alignment for FRET efficiency. Comparisons require
equal state counts; differing counts surface through the state-count metric and
yield ``viterbi=0`` / ``mean_delta=inf`` so they can never be silently scored as
agreeing.
"""

from __future__ import annotations

import json
import math
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
#: replaces these (if wider) or confirms them (if tighter) — see `freeze`.
PROVISIONAL = {
    "state_count_min_fraction": 0.90,  # floor: exact on ≥ 90% of traces
    "state_mean_abs_delta_max": 0.02,  # ceiling: per-state |ΔE| (FRET units)
    "viterbi_min_agreement": 0.95,  # floor: per-frame agreement
    "relative_elbo_max": 0.01,  # ceiling: |ΔELBO| / |ELBO|
}

#: The ``type`` of a per-trace vbFRET model file: the name the pinned tMAVEN's own
#: (commented-out) per-trace ``run_vbhmm_modelselection`` gives that model, which the
#: sidecar runner's ``model_type="vbfret"`` writes (ADR-0110).
PER_TRACE_MODEL_TYPE = "vb HMM_model selection"

#: The bound key of the transition-matrix ceiling in ``$.transition_matrix`` (ADR-0110).
TRANSITION_MATRIX_BOUND = "transition_matrix_abs_delta_max"

#: Resolution of a frozen bound: every frozen number has at most four decimal places, so
#: no transition-matrix ceiling is frozen below one step of it (`freeze_transition_matrix`).
FREEZE_RESOLUTION = 1e-4


@dataclass(frozen=True)
class ParityMetrics:
    """The four parity quantities for one ``(reference, test)`` comparison.

    ``state_count_fraction`` and ``viterbi_agreement`` are *higher-is-better*
    fractions in ``[0, 1]``; ``state_mean_abs_delta`` and ``relative_elbo`` are
    *lower-is-better* magnitudes. ``n_states_ref``/``n_states_test`` record the
    fitted state counts so a count mismatch is visible in the record.

    ``transition_matrix_abs_delta`` is the fifth, *lower-is-better* quantity of the
    population methods ([`transition_matrix_abs_delta`][]). It is ``inf`` when it
    was not computed — a per-trace comparison, or a model with no transition
    matrix — so it can never pass a transition-matrix bound by default.
    """

    state_count_fraction: float
    state_mean_abs_delta: float
    viterbi_agreement: float
    relative_elbo: float
    n_states_ref: int
    n_states_test: int
    transition_matrix_abs_delta: float = float("inf")

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
    window stay [`tether.idealize.NO_STATE`][]. Derived from the model's
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
    """Number of distinct (non-[`NO_STATE`][]) states each row occupies."""
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
    ([`canonical_state_path`][]). Frames where either model is
    [`NO_STATE`][] are excluded. Returns the fraction equal, or ``0.0`` when
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


#: How far a row of ``norm_tmatrix`` may sum from 1 and still be a probability row: well
#: above float64 rounding (tMAVEN's rows sum to 1 within ~2e-16), far below any real error.
_ROW_SUM_TOLERANCE = 1e-6


def _row_normalized(model: StateModel) -> np.ndarray | None:
    """The model's per-frame transition probabilities (rows sum to 1), or ``None``.

    ``None`` for anything that is not a transition matrix: not 2-D, a non-finite or
    negative entry, a ``norm_tmatrix`` row that does not sum to 1, or a raw ``tmatrix``
    row whose total is not positive. Two equally invalid matrices must never compare
    as equal, so a malformed one is refused here rather than compared.
    """
    raw = model.norm_tmatrix is None
    source = model.tmatrix if raw else model.norm_tmatrix
    if source is None:
        return None
    t = np.asarray(source, dtype="float64")
    if t.ndim != 2 or not np.all(np.isfinite(t)) or np.any(t < 0):
        return None
    sums = t.sum(axis=1, keepdims=True)
    if raw:
        return None if np.any(sums <= 0) else t / sums
    return t if np.all(np.abs(sums - 1.0) <= _ROW_SUM_TOLERANCE) else None


def transition_matrix_abs_delta(ref: StateModel, test: StateModel) -> float:
    """Max absolute difference between the two models' transition matrices.

    Both row-normalized matrices (``norm_tmatrix``, the per-frame transition
    probabilities tMAVEN writes; else ``tmatrix`` normalized by row) are put in
    canonical state order — rows and columns permuted by ascending mean FRET,
    the same alignment as every other metric — and the largest entry-wise
    ``|Δ|`` is returned. ``inf`` when the state counts differ, a model has no
    transition matrix, a matrix is not ``K × K`` for its ``K`` means, or it is not
    a matrix of transition probabilities (a non-finite or negative entry, a row
    that does not sum to 1): an incomparable pair can never read as agreement.
    """
    rm = np.asarray(ref.means, dtype="float64").reshape(-1)
    tm = np.asarray(test.means, dtype="float64").reshape(-1)
    rt, tt = _row_normalized(ref), _row_normalized(test)
    if rt is None or tt is None or rm.size == 0 or rm.size != tm.size:
        return float("inf")
    k = rm.size
    if rt.shape != (k, k) or tt.shape != (k, k):
        return float("inf")
    ro = np.argsort(rm, kind="stable")
    to = np.argsort(tm, kind="stable")
    delta = np.abs(rt[np.ix_(ro, ro)] - tt[np.ix_(to, to)])
    if not np.all(np.isfinite(delta)):
        return float("inf")
    return float(np.max(delta))


def _occupied_path(trace: StateModel) -> tuple[np.ndarray, np.ndarray]:
    """One per-trace model's occupied levels and its state path over them.

    Returns ``(levels, path)``: the sorted distinct FRET levels the trace's
    idealized row actually takes, and the per-frame index into ``levels``
    ([`NO_STATE`][] outside the window). Index 0 is the lowest level, so the path
    is in canonical (mean-sorted) labels by construction.
    """
    if trace.idealized is None:
        raise ValueError("per-trace model has no 'idealized' row to derive a state path")
    row = np.asarray(trace.idealized, dtype="float64").reshape(-1)
    finite = np.isfinite(row)
    levels = np.unique(row[finite])
    path = np.full(row.shape, NO_STATE, dtype="int64")
    path[finite] = np.searchsorted(levels, row[finite])
    return levels, path


def compare_trace_models(ref: StateModel, test: StateModel) -> ParityMetrics:
    """The four parity metrics for two **per-trace** vbFRET models (ADR-0110).

    Per-trace vbFRET fits each trace on its own and picks that trace's state count
    by its own evidence, so there is no ensemble state to align. Each trace is
    compared on the states its idealized path **occupies** — the distinct levels
    the path takes, sorted by ascending FRET, which is its canonical order. A state
    the fit kept but no frame uses (variational Bayes leaves such states empty) is
    not a state of the idealization and is not counted. The four quantities are
    pooled over traces:

    1. **state count** — fraction of informative traces whose two paths occupy the
       same number of states, as in [`state_count_fraction`][];
    2. **per-state mean ΔE** — the largest ``|Δ|`` between the sorted occupied
       levels over every trace whose two paths occupy the same number of states
       (``inf`` when no trace does). A trace whose counts differ has no one-to-one
       level match; it is scored by metrics 1 and 3, never silently by this one;
    3. **Viterbi per-frame agreement** — over the frames in-window in both fits, the
       fraction whose canonical states match; every frame of a trace whose two
       paths occupy different numbers of states counts as a disagreement (the
       consensus rule of [`compare_models`][], per trace);
    4. **relative ELBO** — ``|ΣELBO_test − ΣELBO_ref| / |ΣELBO_ref|`` over the
       per-trace final ELBOs (independent per-trace models: the evidence of the
       data set is their sum). ``inf`` when any trace has no ELBO.

    ``n_states_ref``/``n_states_test`` are the largest number of states one trace
    of each fit occupies. ``transition_matrix_abs_delta`` stays ``inf`` (not
    computed): the transition-matrix term belongs to the population methods.

    Raises ``ValueError`` when either model has no per-trace models, or the two
    cover different molecules or different frame counts.
    """
    if not ref.trace_level or not test.trace_level:
        raise ValueError("both models must carry per-trace models (trace_level)")
    if set(ref.trace_level) != set(test.trace_level):
        raise ValueError(
            "per-trace models cover different molecules: "
            f"{sorted(ref.trace_level)} vs {sorted(test.trace_level)}"
        )
    informative = agree = matched = 0
    worst_delta = 0.0
    frames_total = frames_agree = 0
    elbo_ref = elbo_test = 0.0
    elbo_ok = True
    k_ref = k_test = 0
    for mol in sorted(ref.trace_level):
        r, t = ref.trace_level[mol], test.trace_level[mol]
        r_levels, rp = _occupied_path(r)
        t_levels, tp = _occupied_path(t)
        if rp.shape != tp.shape:
            raise ValueError(f"molecule {mol}: per-trace paths are not frame-aligned")
        k_ref, k_test = max(k_ref, r_levels.size), max(k_test, t_levels.size)
        if r_levels.size or t_levels.size:
            informative += 1
            agree += int(r_levels.size == t_levels.size)
        both = (rp != NO_STATE) & (tp != NO_STATE)
        frames_total += int(both.sum())
        if r_levels.size == t_levels.size and r_levels.size > 0:
            matched += 1
            worst_delta = max(worst_delta, float(np.max(np.abs(r_levels - t_levels))))
            frames_agree += int((rp[both] == tp[both]).sum())
        if r.elbo is None or t.elbo is None:
            elbo_ok = False
        else:
            elbo_ref += float(r.elbo)
            elbo_test += float(t.elbo)
    rel_elbo = float("inf")
    if elbo_ok and elbo_ref != 0.0:
        rel_elbo = float(abs(elbo_test - elbo_ref) / abs(elbo_ref))
    return ParityMetrics(
        state_count_fraction=float(agree / informative) if informative else 1.0,
        state_mean_abs_delta=worst_delta if matched else float("inf"),
        viterbi_agreement=float(frames_agree / frames_total) if frames_total else 0.0,
        relative_elbo=rel_elbo,
        n_states_ref=int(k_ref),
        n_states_test=int(k_test),
    )


def compare_models(ref: StateModel, test: StateModel) -> ParityMetrics:
    """Compute the four parity metrics for ``test`` against ``ref``.

    Pure (numpy-only): the same function CI's ``sidecar.yml`` uses to assert a
    fresh sidecar fit against the committed reference model within the frozen
    §11.2 tolerance. State-count and Viterbi metrics need both models'
    ``idealized`` paths; mean-ΔE and ELBO use the model summaries. The fifth,
    ``transition_matrix_abs_delta``, uses their transition matrices.

    Two per-trace vbFRET models (``model_type`` [`PER_TRACE_MODEL_TYPE`][]) are
    compared by [`compare_trace_models`][]. A per-trace model against an ensemble
    model raises ``ValueError``: the two are different models, and no metric of
    one is commensurable with the other's.
    """
    per_trace = (ref.model_type == PER_TRACE_MODEL_TYPE, test.model_type == PER_TRACE_MODEL_TYPE)
    if all(per_trace):
        return compare_trace_models(ref, test)
    if any(per_trace):
        raise ValueError(
            f"cannot compare a per-trace model with an ensemble model "
            f"({ref.model_type!r} vs {test.model_type!r})"
        )
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
        transition_matrix_abs_delta=transition_matrix_abs_delta(ref, test),
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


def within_transition_matrix_tolerance(
    metrics: ParityMetrics, bound: float
) -> tuple[bool, list[str]]:
    """Check one comparison's transition-matrix term against its frozen ceiling.

    The fifth check of the population methods (ADR-0110), kept apart from
    [`within_tolerance`][] so the four-bound rows keep their shape. A non-finite
    term (not computed, or an incomparable pair) is a failure, never a pass.
    """
    value = metrics.transition_matrix_abs_delta
    if not np.isfinite(value):
        return (False, ["non-finite metric(s): transition-matrix |Δ|"])
    if value > bound:
        return (False, [f"transition-matrix |Δ| {value:.3g} > {bound}"])
    return (True, [])


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
        """Worst value of this metric over the runs — a floor's minimum, a ceiling's maximum."""
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


def freeze_transition_matrix(spread: SpreadSummary, margin: float = 0.5) -> float:
    """Freeze the transition-matrix ceiling from its measured spread (ADR-0110).

    The ADR-0009 rule with no provisional default, because the specification
    gives none for this term: the measured worst case widened by ``(1 + margin)×``
    — the same margin as [`freeze`][] — and rounded **up** to the freeze
    resolution ([`FREEZE_RESOLUTION`][], four decimal places), never below one
    step of it. Rounding up, not to nearest, so the frozen ceiling is never below
    the widened worst case; a floor of one step, so a spread that is all but zero
    on one build does not freeze a ceiling another build's rounding noise breaks.

    Raises ``ValueError`` when the spread has a non-finite worst case: an
    incomparable run must stop the freeze, never ratify a finite ceiling over it.
    """
    if spread.direction != "ceiling":
        raise ValueError("the transition-matrix term is a ceiling")
    worst = spread.worst
    if not np.isfinite(worst):
        raise ValueError("cannot freeze a ceiling over a non-finite measured worst case")
    steps = math.ceil(round(worst * (1.0 + margin) / FREEZE_RESOLUTION, 9))
    return round(max(1, steps) * FREEZE_RESOLUTION, 4)


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
    per-metric [`SpreadSummary`][] map and the raw per-run metrics. This is
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


def load_transition_matrix_tolerance(path: str | PathLike[str], method: str) -> float:
    """Read the frozen transition-matrix ceiling of ``method`` (ADR-0110).

    The ceilings live under ``$.transition_matrix.tolerance_by_method`` — apart
    from the four-bound rows, which keep their shape — one per population method
    (``"vbconhmm"``, ``"ebhmm"``). Per-trace vbFRET has none. Raises ``KeyError``
    for a method with no frozen ceiling: there is no default to fall back to.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return float(data["transition_matrix"]["tolerance_by_method"][method][TRANSITION_MATRIX_BOUND])
