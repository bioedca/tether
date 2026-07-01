# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for apparent FRET efficiency (the proximity ratio), PRD §7.3/§7.4.

Pure-numpy, no Qt: :func:`tether.fret.apparent_fret` is the single definition of
the uncorrected ``A / (D + A)`` the dock, corrections, and analysis all share.
"""

from __future__ import annotations

import numpy as np

from tether.fret import apparent_fret


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
