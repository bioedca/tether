# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for FRET efficiency — corrected and apparent, PRD §7.3/§7.4.

Pure-numpy, no Qt: :func:`tether.fret.corrected_fret` is the single definition of
``I_A,corr / (I_A,corr + γ·I_D,corr)``; :func:`tether.fret.apparent_fret` is its
``α=0, γ=1`` special case (``A / (D + A)``), shared by the dock, corrections, and
analysis.
"""

from __future__ import annotations

import numpy as np

from tether.fret import apparent_fret, corrected_fret


def test_apparent_fret_basic_ratio() -> None:
    donor = np.array([100.0, 75.0, 50.0, 0.0])
    acceptor = np.array([0.0, 25.0, 50.0, 100.0])
    e = apparent_fret(donor, acceptor)
    np.testing.assert_allclose(e, [0.0, 0.25, 0.5, 1.0])


def test_apparent_fret_zero_total_is_nan_not_error() -> None:
    # Total intensity of exactly zero -> undefined ratio -> NaN (never raises,
    # never fabricates a value). Both-zero and cancelling(=0) both count.
    donor = np.array([0.0, 10.0])
    acceptor = np.array([0.0, -10.0])
    e = apparent_fret(donor, acceptor)
    assert np.isnan(e[0])
    assert np.isnan(e[1])


def test_apparent_fret_returns_float64_matching_shape() -> None:
    donor = np.ones((3,), dtype=np.float32)
    acceptor = np.ones((3,), dtype=np.float32)
    e = apparent_fret(donor, acceptor)
    assert e.dtype == np.float64
    assert e.shape == (3,)
    np.testing.assert_allclose(e, 0.5)


def test_apparent_fret_not_clipped_to_unit_interval() -> None:
    # The uncorrected proximity ratio can exceed [0, 1] on noisy frames; the
    # helper must not silently clip (that would distort the displayed value).
    donor = np.array([-5.0])
    acceptor = np.array([105.0])
    e = apparent_fret(donor, acceptor)
    assert e[0] > 1.0


def test_apparent_fret_is_corrected_at_identity_factors() -> None:
    # apparent E is EXACTLY corrected_fret at α=0, γ=1 (PRD §7.4) — one definition,
    # no drift between the two entry points.
    rng = np.random.default_rng(0)
    donor = rng.uniform(50.0, 1000.0, 200)
    acceptor = rng.uniform(50.0, 1000.0, 200)
    np.testing.assert_array_equal(
        apparent_fret(donor, acceptor),
        corrected_fret(donor, acceptor, alpha=0.0, gamma=1.0),
    )


def test_corrected_fret_recovers_known_efficiency() -> None:
    # Construct donor/acceptor from a known true E with known α, γ, then check the
    # formula inverts it. With I_D the bare donor and I_A = γ·(E/(1-E))·I_D + α·I_D,
    # corrected_fret must return E.
    e_true = np.array([0.1, 0.3, 0.5, 0.75, 0.9])
    alpha, gamma = 0.09, 1.4
    i_d = np.array([800.0, 700.0, 600.0, 500.0, 400.0])
    i_a = gamma * (e_true / (1.0 - e_true)) * i_d + alpha * i_d
    e = corrected_fret(i_d, i_a, alpha=alpha, gamma=gamma)
    np.testing.assert_allclose(e, e_true, atol=1e-12)


def test_corrected_fret_gamma_shifts_efficiency() -> None:
    # γ > 1 down-weights the donor imbalance; on the same intensities a larger γ
    # yields a smaller E (the acceptor share of the corrected total shrinks).
    donor = np.array([500.0])
    acceptor = np.array([500.0])
    e_lo = corrected_fret(donor, acceptor, alpha=0.0, gamma=1.0)
    e_hi = corrected_fret(donor, acceptor, alpha=0.0, gamma=2.0)
    assert e_hi[0] < e_lo[0]


def test_corrected_fret_leakage_lowers_acceptor() -> None:
    # A positive α subtracts donor leakage from the acceptor, lowering E relative to
    # the uncorrected proximity ratio on the same frame.
    donor = np.array([600.0])
    acceptor = np.array([400.0])
    e_app = corrected_fret(donor, acceptor, alpha=0.0, gamma=1.0)
    e_leak = corrected_fret(donor, acceptor, alpha=0.1, gamma=1.0)
    assert e_leak[0] < e_app[0]


def test_corrected_fret_zero_denominator_is_nan() -> None:
    # A corrected denominator of exactly zero (I_A,corr + γ·I_D,corr == 0) is
    # undefined -> NaN, not an exception or a fabricated value. Here I_A,corr =
    # 100 - 1*100 = 0 and γ·I_D = 0 -> denom 0.
    donor = np.array([100.0])
    acceptor = np.array([100.0])
    e = corrected_fret(donor, acceptor, alpha=1.0, gamma=0.0)
    assert np.isnan(e[0])


def test_corrected_fret_returns_float64_matching_shape() -> None:
    donor = np.ones((4,), dtype=np.float32)
    acceptor = np.full((4,), 3.0, dtype=np.float32)
    e = corrected_fret(donor, acceptor, alpha=0.0, gamma=1.0)
    assert e.dtype == np.float64
    assert e.shape == (4,)
    np.testing.assert_allclose(e, 0.75)
