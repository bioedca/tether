# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Detection-correction factor γ across the acceptor-bleach step (M3, FR-CORRECT).

Locks :mod:`tether.fret.gamma`: the per-trace ``γ = ΔI_A/ΔI_D`` estimator on
leakage-corrected intensities (3-frame half-window levels, both step segments
``> min_window_frames``, ``0 < γ ≤ 5``), the population-median aggregate with
per-molecule retention + median fallback, and the withhold-not-fabricate gate.

Validated against **synthetic known-γ recovery** (a designed acceptor-bleach step
with a baked-in true γ) and a **reference-formula parity** check (the documented
``deep_autocorrect_2color.m`` ΔI_A/ΔI_D with δ = 0, bare ``I_D``, on the same 3-frame
windows). The strict "±10 % of the Deep-LASI median on Deep-LASI's own per-frame
classification" oracle is deferred (ADR-0028): the vendored export carries no
per-frame classification. Headless; runs in the base CI matrix.
"""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from tether.fret.gamma import (
    DEFAULT_GAMMA_HALF_WINDOW,
    GAMMA_CEILING,
    GammaEstimate,
    TraceGamma,
    estimate_gamma,
    gamma_windows,
    trace_gamma,
)


def _step_trace(
    *,
    n: int = 120,
    acceptor_pb: int = 40,
    donor_pb: int = 100,
    gamma_true: float = 1.0,
    alpha: float = 0.1,
    donor_lo: float = 600.0,
    donor_hi: float = 1000.0,
    noise: float = 0.0,
    seed: int = 0,
) -> tuple[np.ndarray, np.ndarray]:
    """Build a donor/acceptor pair with a clean acceptor-bleach step at ``acceptor_pb``.

    Constructed so ``γ = ΔI_A,corr / ΔI_D`` recovers ``gamma_true`` exactly (noise 0):

    * donor is quenched (``donor_lo``) while FRET is active, rises to ``donor_hi`` once
      the acceptor bleaches (dequenching), then bleaches to ~0 at ``donor_pb``;
    * the acceptor is ``A_hi`` (FRET + leakage) pre-step, then pure donor **leakage**
      ``α·donor`` post-step — so the leakage-corrected acceptor drops to ~0;
    * ``A_hi = gamma_true·(donor_hi − donor_lo) + α·donor_lo`` makes
      ``ΔI_A,corr = A_hi − α·donor_lo = gamma_true·ΔI_D``.
    """
    rng = np.random.default_rng(seed)
    donor = np.empty(n, dtype=np.float64)
    donor[:acceptor_pb] = donor_lo
    donor[acceptor_pb:donor_pb] = donor_hi
    donor[donor_pb:] = 0.0
    a_hi = gamma_true * (donor_hi - donor_lo) + alpha * donor_lo
    acceptor = np.empty(n, dtype=np.float64)
    acceptor[:acceptor_pb] = a_hi
    acceptor[acceptor_pb:donor_pb] = alpha * donor_hi  # pure leakage after acceptor dies
    acceptor[donor_pb:] = 0.0
    if noise:
        donor = donor + rng.normal(0.0, noise, n)
        acceptor = acceptor + rng.normal(0.0, noise, n)
    return donor, acceptor


def _reference_gamma(
    donor: np.ndarray,
    acceptor: np.ndarray,
    *,
    alpha: float,
    acceptor_pb: int,
    half_window: int,
) -> float:
    """Independent transcription of the documented γ formula (ADR-0028 / Appendix B.2).

    ``γ = (I_A,spFRET − I_A,after)/(I_D,after − I_D,spFRET)`` on leakage-corrected
    acceptor and **bare** donor, levels averaged over the 3-frame windows each side of
    the step — the δ = 0, bare-``I_D`` simplification of
    ``deep_autocorrect_2color.m:118-130``. Deliberately computed a different way from
    :func:`trace_gamma` (explicit slices, no shared helper) so parity is meaningful.
    """
    ia = np.asarray(acceptor, float) - alpha * np.asarray(donor, float)
    id_ = np.asarray(donor, float)
    pre = slice(acceptor_pb - half_window, acceptor_pb)
    post = slice(acceptor_pb, acceptor_pb + half_window)
    da_delta = ia[pre].mean() - ia[post].mean()
    dd_delta = id_[post].mean() - id_[pre].mean()
    return float(da_delta / dd_delta)


# --- per-trace estimator ------------------------------------------------------


@pytest.mark.parametrize("gamma_true", [0.5, 1.0, 2.0, 4.0])
def test_recovers_known_gamma(gamma_true: float) -> None:
    donor, acceptor = _step_trace(gamma_true=gamma_true, alpha=0.1)
    est = trace_gamma(donor, acceptor, alpha=0.1, acceptor_pb=40, donor_pb=100)
    assert est.reason == "ok"
    assert est.gamma == pytest.approx(gamma_true, abs=1e-9)


def test_recovers_known_gamma_with_noise() -> None:
    donor, acceptor = _step_trace(gamma_true=1.5, alpha=0.1, noise=3.0, seed=7)
    est = trace_gamma(donor, acceptor, alpha=0.1, acceptor_pb=40, donor_pb=100)
    assert est.reason == "ok"
    assert est.gamma == pytest.approx(1.5, abs=0.1)


def test_reference_formula_parity() -> None:
    # trace_gamma must equal an independent transcription of the documented formula on
    # the same frames — the estimator-isolated durable oracle (ADR-0028).
    for gt in (0.4, 0.9, 1.7, 3.2):
        donor, acceptor = _step_trace(gamma_true=gt, alpha=0.12, noise=2.0, seed=int(gt * 10))
        est = trace_gamma(donor, acceptor, alpha=0.12, acceptor_pb=40, donor_pb=100)
        ref = _reference_gamma(
            donor, acceptor, alpha=0.12, acceptor_pb=40, half_window=DEFAULT_GAMMA_HALF_WINDOW
        )
        assert est.gamma == pytest.approx(ref, abs=1e-12)


def test_leakage_correction_is_applied() -> None:
    # γ is measured on the leakage-corrected acceptor: a wrong α changes the estimate,
    # confirming apply_leakage is wired (not measured on the raw acceptor).
    donor, acceptor = _step_trace(gamma_true=1.0, alpha=0.1)
    right = trace_gamma(donor, acceptor, alpha=0.1, acceptor_pb=40, donor_pb=100)
    wrong = trace_gamma(donor, acceptor, alpha=0.0, acceptor_pb=40, donor_pb=100)
    assert right.gamma == pytest.approx(1.0, abs=1e-9)
    assert wrong.gamma != pytest.approx(1.0, abs=1e-3)


def test_no_step_when_acceptor_bleaches_after_donor() -> None:
    donor, acceptor = _step_trace()
    est = trace_gamma(donor, acceptor, alpha=0.1, acceptor_pb=100, donor_pb=100)
    assert est.gamma is None
    assert est.reason == "no-step"


def test_no_step_when_acceptor_pb_zero() -> None:
    donor, acceptor = _step_trace()
    est = trace_gamma(donor, acceptor, alpha=0.1, acceptor_pb=0, donor_pb=100)
    assert est.gamma is None
    assert est.reason == "no-step"


def test_short_pre_segment_rejected() -> None:
    # pre-segment [0, acceptor_pb) length == min_window_frames (20) is NOT > 20 → reject.
    donor, acceptor = _step_trace(acceptor_pb=20, donor_pb=100)
    est = trace_gamma(donor, acceptor, alpha=0.1, acceptor_pb=20, donor_pb=100)
    assert est.gamma is None
    assert est.reason == "short-pre"


def test_short_post_segment_rejected() -> None:
    # post-segment [acceptor_pb, donor_pb) length == 20 is NOT > 20 → reject.
    donor, acceptor = _step_trace(acceptor_pb=40, donor_pb=60)
    est = trace_gamma(donor, acceptor, alpha=0.1, acceptor_pb=40, donor_pb=60)
    assert est.gamma is None
    assert est.reason == "short-post"


def test_degenerate_donor_no_rise_rejected() -> None:
    # Donor does not rise across the step (donor_hi == donor_lo) → ΔI_D ≤ 0 → reject,
    # never a division by a non-positive jump.
    donor, acceptor = _step_trace(donor_lo=800.0, donor_hi=800.0)
    est = trace_gamma(donor, acceptor, alpha=0.1, acceptor_pb=40, donor_pb=100)
    assert est.gamma is None
    assert est.reason == "degenerate-donor"


def test_out_of_range_gamma_rejected() -> None:
    # A designed γ above the ceiling (5) is dropped.
    donor, acceptor = _step_trace(gamma_true=6.0)
    est = trace_gamma(donor, acceptor, alpha=0.1, acceptor_pb=40, donor_pb=100)
    assert est.gamma is None
    assert est.reason == "out-of-range"


def test_ceiling_is_inclusive() -> None:
    donor, acceptor = _step_trace(gamma_true=GAMMA_CEILING)
    est = trace_gamma(donor, acceptor, alpha=0.1, acceptor_pb=40, donor_pb=100)
    assert est.reason == "ok"
    assert est.gamma == pytest.approx(GAMMA_CEILING, abs=1e-9)


def test_negative_gamma_rejected_out_of_range() -> None:
    # If the leakage-corrected acceptor RISES across the step (ΔI_A < 0) while the donor
    # also rises, γ < 0 → rejected by the ``0 < γ`` lower bound (not a spurious pass).
    donor, acceptor = _step_trace(gamma_true=1.0, alpha=0.1)
    acceptor = acceptor.copy()
    acceptor[40:100] += 500.0  # post-step acceptor now exceeds the pre-step level
    est = trace_gamma(donor, acceptor, alpha=0.1, acceptor_pb=40, donor_pb=100)
    assert est.gamma is None
    assert est.reason == "out-of-range"


def test_post_window_clamped_to_donor_bleach_frame() -> None:
    # §11.2's half_window is configurable: a half_window larger than the post-segment
    # must NOT average post-donor-bleach (dark-donor) frames into the level — the post
    # window is clamped to the donor-bleach frame, so γ stays correct.
    donor, acceptor = _step_trace(gamma_true=1.0, alpha=0.1, acceptor_pb=40, donor_pb=65)
    est = trace_gamma(donor, acceptor, alpha=0.1, acceptor_pb=40, donor_pb=65, half_window=30)
    assert est.reason == "ok"
    assert est.post_window == (40, 65)  # clamped to donor_stop, not 40 + 30 = 70
    assert est.gamma == pytest.approx(1.0, abs=1e-9)


def test_shape_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="same shape"):
        trace_gamma(np.ones(10), np.ones(11), alpha=0.1, acceptor_pb=3, donor_pb=8)


def test_non_1d_raises() -> None:
    with pytest.raises(ValueError, match="1-D"):
        trace_gamma(np.ones((4, 4)), np.ones((4, 4)), alpha=0.1, acceptor_pb=1, donor_pb=3)


def test_gamma_windows_ranges_and_clamp() -> None:
    pre, post = gamma_windows(40, 3, 120)
    assert pre == (37, 40)
    assert post == (40, 43)
    # clamp at the trace edges
    assert gamma_windows(1, 3, 120)[0] == (0, 1)
    assert gamma_windows(119, 3, 120)[1] == (119, 120)


# --- dataset aggregate --------------------------------------------------------


def _cohort(n_mol: int, *, gamma_true: float, alpha: float = 0.1, noise: float = 0.0):
    donor = np.stack(
        [
            _step_trace(gamma_true=gamma_true, alpha=alpha, noise=noise, seed=i + 1)[0]
            for i in range(n_mol)
        ]
    )
    acceptor = np.stack(
        [
            _step_trace(gamma_true=gamma_true, alpha=alpha, noise=noise, seed=i + 1)[1]
            for i in range(n_mol)
        ]
    )
    return list(donor), list(acceptor)


def test_estimate_gamma_median_of_qualifying() -> None:
    donor, acceptor = _cohort(12, gamma_true=1.2, noise=2.0)
    est = estimate_gamma(donor, acceptor, [0.1] * 12, [40] * 12, [100] * 12)
    assert est.n_qualifying == 12
    assert est.n_traces == 12
    assert est.gamma == pytest.approx(1.2, abs=0.1)


def test_estimate_gamma_withholds_below_min_qualifying() -> None:
    donor, acceptor = _cohort(4, gamma_true=1.0)  # 4 < default 10
    est = estimate_gamma(donor, acceptor, [0.1] * 4, [40] * 4, [100] * 4)
    assert est.n_qualifying == 4
    assert est.gamma is None


def test_estimate_gamma_all_rejected_no_median_warning() -> None:
    # Every post-segment too short → 0 qualifying; np.median([]) must never run (no
    # RuntimeWarning), gamma withheld. Also covers the degenerate min_qualifying<=0 path.
    donor, acceptor = _cohort(12, gamma_true=1.0)
    with np.errstate(all="raise"):
        est = estimate_gamma(
            donor, acceptor, [0.1] * 12, [40] * 12, [55] * 12, min_qualifying_traces=0
        )
    assert est.n_qualifying == 0
    assert est.gamma is None


def test_effective_gamma_retention_and_fallback() -> None:
    # 10 clean steps (qualify) + 2 short-post (fail): the median applies as fallback to
    # the 2, the 10 keep their own γ, and is_fallback flags exactly the 2.
    good_d, good_a = _cohort(10, gamma_true=1.5, noise=1.0)
    bad = [_step_trace(gamma_true=1.5, acceptor_pb=40, donor_pb=55, seed=100 + i) for i in range(2)]
    donor = good_d + [b[0] for b in bad]
    acceptor = good_a + [b[1] for b in bad]
    accpb = [40] * 12
    donpb = [100] * 10 + [55, 55]
    est = estimate_gamma(donor, acceptor, [0.1] * 12, accpb, donpb)
    assert est.n_qualifying == 10
    assert est.gamma is not None
    for i in range(10):
        assert est.is_fallback(i) is False
        assert est.effective_gamma(i) == pytest.approx(est.per_trace[i].gamma)
    for i in (10, 11):
        assert est.is_fallback(i) is True
        assert est.effective_gamma(i) == pytest.approx(est.gamma)


def test_effective_gamma_none_when_withheld() -> None:
    donor, acceptor = _cohort(3, gamma_true=1.0)
    est = estimate_gamma(donor, acceptor, [0.1] * 3, [40] * 3, [100] * 3)
    assert est.gamma is None
    assert est.effective_gamma(0) is None
    assert est.is_fallback(0) is False


def test_estimate_gamma_length_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="same length"):
        estimate_gamma([np.ones(50)], [np.ones(50)], [0.1], [10], [10, 20])


def test_dataclasses_are_frozen() -> None:
    tg = TraceGamma(1.0, 40, (37, 40), (40, 43), 40, 60, "ok")
    with pytest.raises(dataclasses.FrozenInstanceError):
        tg.gamma = 2.0  # type: ignore[misc]
    ge = GammaEstimate(1.0, 1, 1, (tg,))
    with pytest.raises(dataclasses.FrozenInstanceError):
        ge.gamma = 2.0  # type: ignore[misc]
