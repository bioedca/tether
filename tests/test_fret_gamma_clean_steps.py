# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The clean acceptor-bleach steps behind γ, and the warning when few are clean (#1232).

Locks the count of :mod:`tether.fret.gamma` (ADR-0134): among the traces that pass both
segment gates, a step is clean when the donor rose **and** the leakage-corrected acceptor
dropped over the estimator's own level windows. The count must be the one the workstation
check records as ``n_gamma_step_clean`` (#1233), so the fraction chosen from those records
means the same thing in the product. Then the warning: a dataset whose donor does not rise at
most steps still gets a γ, from the positive tail of noise that the ``(0, 5]`` gate keeps, and
[`few_clean_steps`][tether.fret.gamma.few_clean_steps] says so; a clean dataset raises nothing.
Headless; runs in the base CI matrix.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

import _deeplasi_agreement as agree
from tether.fret.gamma import (
    DEFAULT_MIN_CLEAN_STEP_FRACTION,
    estimate_gamma,
    few_clean_steps,
    trace_gamma,
)

ALPHA = 0.1
N_FRAMES = 300
STEP = 150
DONOR_BLEACH = 280
NOISE = 40.0


def _clean(seed: int, *, gamma_true: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
    """A step of the expected shape: the donor rises 400 as the acceptor drops ``γ·400``."""
    rng = np.random.default_rng(seed)
    donor = np.zeros(N_FRAMES)
    donor[:STEP] = 600.0
    donor[STEP:DONOR_BLEACH] = 1000.0
    acceptor = np.zeros(N_FRAMES)
    acceptor[:STEP] = gamma_true * 400.0 + ALPHA * 600.0
    acceptor[STEP:DONOR_BLEACH] = ALPHA * 1000.0
    return donor + rng.normal(0.0, NOISE, N_FRAMES), acceptor + rng.normal(0.0, NOISE, N_FRAMES)


def _no_rise(seed: int) -> tuple[np.ndarray, np.ndarray]:
    """The acceptor drops 60 at the step but the donor stays level: no dequenching seen.

    A rise of 60 hidden by a donor that dims by as much at the same frame, or a donor whose
    rise is lost in its noise: either way the estimator sees ``ΔI_D`` of pure noise, and the
    ratio ``60 / ΔI_D`` is kept only on the traces where that noise came out above 12.
    """
    rng = np.random.default_rng(seed)
    donor = np.zeros(N_FRAMES)
    donor[:DONOR_BLEACH] = 800.0
    acceptor = np.zeros(N_FRAMES)
    acceptor[:STEP] = ALPHA * 800.0 + 60.0
    acceptor[STEP:DONOR_BLEACH] = ALPHA * 800.0
    return donor + rng.normal(0.0, NOISE, N_FRAMES), acceptor + rng.normal(0.0, NOISE, N_FRAMES)


def _estimate(rows: list[tuple[np.ndarray, np.ndarray]], *, donor_pbs: list[int] | None = None):
    n = len(rows)
    donors = [r[0] for r in rows]
    acceptors = [r[1] for r in rows]
    donor_pbs = donor_pbs if donor_pbs is not None else [DONOR_BLEACH] * n
    est = estimate_gamma(donors, acceptors, [ALPHA] * n, [STEP] * n, donor_pbs)
    return est, donors, acceptors


# --- the per-trace step changes ----------------------------------------------------


def test_a_measured_step_carries_its_two_changes() -> None:
    donor, acceptor = _clean(1, gamma_true=1.2)
    t = trace_gamma(donor, acceptor, alpha=ALPHA, acceptor_pb=STEP, donor_pb=DONOR_BLEACH)
    assert t.reason == "ok"
    assert t.step_measured and t.step_clean
    assert t.gamma == pytest.approx(t.delta_acceptor / t.delta_donor, rel=1e-12)
    assert t.delta_donor == pytest.approx(400.0, abs=40.0)


def test_a_donor_that_does_not_rise_is_measured_but_not_clean() -> None:
    donor = np.full(N_FRAMES, 800.0)
    donor[STEP:] = 700.0  # dims at the step
    acceptor = np.full(N_FRAMES, 200.0)
    acceptor[STEP:] = ALPHA * 700.0
    t = trace_gamma(donor, acceptor, alpha=ALPHA, acceptor_pb=STEP, donor_pb=N_FRAMES)
    assert t.reason == "degenerate-donor"
    assert t.step_measured and not t.step_clean
    assert t.delta_donor == pytest.approx(-100.0)
    assert t.delta_acceptor > 0.0


def test_an_acceptor_that_rises_is_measured_but_not_clean() -> None:
    # ΔI_D > 0 but ΔI_A < 0: the negative ratio fails the ceiling gate as out-of-range.
    donor = np.full(N_FRAMES, 600.0)
    donor[STEP:] = 1000.0
    acceptor = np.full(N_FRAMES, 100.0)
    acceptor[STEP:] = 300.0
    t = trace_gamma(donor, acceptor, alpha=0.0, acceptor_pb=STEP, donor_pb=N_FRAMES)
    assert t.reason == "out-of-range"
    assert t.step_measured and not t.step_clean


def test_a_clean_step_above_the_ceiling_is_still_clean() -> None:
    donor, acceptor = _clean(2, gamma_true=8.0)
    t = trace_gamma(donor, acceptor, alpha=ALPHA, acceptor_pb=STEP, donor_pb=DONOR_BLEACH)
    assert t.reason == "out-of-range"
    assert t.step_clean


@pytest.mark.parametrize(
    ("acceptor_pb", "donor_pb", "reason"),
    [(0, DONOR_BLEACH, "no-step"), (10, DONOR_BLEACH, "short-pre"), (STEP, STEP + 5, "short-post")],
)
def test_a_trace_that_fails_a_segment_gate_has_no_measured_step(
    acceptor_pb: int, donor_pb: int, reason: str
) -> None:
    donor, acceptor = _clean(3)
    t = trace_gamma(donor, acceptor, alpha=ALPHA, acceptor_pb=acceptor_pb, donor_pb=donor_pb)
    assert t.reason == reason
    assert math.isnan(t.delta_donor) and math.isnan(t.delta_acceptor)
    assert not t.step_measured and not t.step_clean


# --- one definition with the workstation check (#1233) --------------------------------


def test_the_clean_count_is_the_workstation_checks_n_gamma_step_clean() -> None:
    """AC 1: Tether's count and #1233's ``n_gamma_step_clean`` agree on one acquisition.

    The acquisition mixes every reason the estimator gives: clean steps, steps whose donor
    does not rise, clean steps above the ceiling, and traces that fail each segment gate.
    """
    rows = (
        [_clean(10 + i) for i in range(12)]
        + [_no_rise(40 + i) for i in range(30)]
        + [_clean(80 + i, gamma_true=8.0) for i in range(3)]
        + [_clean(90 + i) for i in range(4)]
    )
    donor_pbs = [DONOR_BLEACH] * 45 + [STEP + 5] * 2 + [STEP] * 2  # short-post, then no-step
    est, donors, acceptors = _estimate(rows, donor_pbs=donor_pbs)
    reasons = {t.reason for t in est.per_trace}
    assert reasons == {"ok", "degenerate-donor", "out-of-range", "short-post", "no-step"}

    numbers = agree.gamma_step_numbers(est, donors, acceptors, ALPHA)
    assert est.n_step_clean == numbers["n_gamma_step_clean"]
    assert est.n_step_evaluated == sum(
        numbers[f"n_gamma_reason_{r}"] for r in ("ok", "degenerate_donor", "out_of_range")
    )
    delta_d, delta_a = agree.gamma_step_changes(est, donors, acceptors, ALPHA)
    for i, t in enumerate(est.per_trace):
        if t.step_measured:
            assert t.delta_donor == pytest.approx(delta_d[i], rel=1e-12)
            assert t.delta_acceptor == pytest.approx(delta_a[i], rel=1e-12)
        else:
            assert math.isnan(delta_d[i]) and math.isnan(delta_a[i])


# --- the warning ---------------------------------------------------------------------


def test_a_dataset_whose_donor_rarely_rises_gets_a_gamma_and_the_warning() -> None:
    """AC 2: most steps lack the donor rise, γ is still reported, and the warning fires.

    The acceptor drops 60 on every trace and the donor never rises, so there is no dye γ to
    find. The ``(0, 5]`` gate keeps the traces whose donor noise came out above 12, and the
    median of ``60 / noise`` over them is what the estimator reports: about 3, the shape the
    workstation found on ``lab-01``. About half the steps read clean by chance alone, which
    is why the warning fraction sits above one half.
    """
    est, _, _ = _estimate([_no_rise(i) for i in range(100)])
    assert est.gamma is not None
    assert est.gamma == pytest.approx(3.127, abs=1e-3)
    assert est.n_qualifying == 15
    assert est.n_step_evaluated == 100
    assert est.n_step_clean == 46
    assert few_clean_steps(est.n_step_clean, est.n_step_evaluated)


def test_a_clean_dataset_raises_no_warning() -> None:
    est, _, _ = _estimate([_clean(200 + i) for i in range(40)])
    assert est.gamma == pytest.approx(1.0, abs=0.05)
    assert est.n_step_evaluated == est.n_step_clean == 40
    assert not few_clean_steps(est.n_step_clean, est.n_step_evaluated)


def test_the_fraction_is_strict_and_reads_the_default() -> None:
    assert DEFAULT_MIN_CLEAN_STEP_FRACTION == 0.65
    assert few_clean_steps(64, 100)
    assert not few_clean_steps(65, 100)
    assert not few_clean_steps(0, 100, 0.0)
    assert few_clean_steps(99, 100, 1.0)


def test_no_evaluated_step_is_no_warning() -> None:
    assert not few_clean_steps(0, 0)


@pytest.mark.parametrize("bad", [-0.1, 1.5, float("nan"), True])
def test_a_fraction_outside_zero_to_one_is_refused(bad: object) -> None:
    with pytest.raises(ValueError, match="min_fraction"):
        few_clean_steps(1, 2, bad)  # type: ignore[arg-type]


@pytest.mark.parametrize(("clean", "evaluated"), [(-1, 2), (3, 2), (0, -1)])
def test_impossible_counts_are_refused(clean: int, evaluated: int) -> None:
    with pytest.raises(ValueError, match="n_step_clean"):
        few_clean_steps(clean, evaluated)
