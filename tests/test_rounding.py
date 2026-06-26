# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""MATLAB away-from-zero rounding (PRD Appendix E; M0.5 S5 follow-up).

Locks :func:`tether.imaging._rounding.round_half_away` — the single rounding
primitive both the detection snap (`findPart.m`) and the aperture crop centring
(`extractTraces.m`) use to land on the same pixels as Deep-LASI's MATLAB
``round`` (ties away from zero), where Python/NumPy round halves to even.
"""

from __future__ import annotations

import pytest

pytest.importorskip("numpy")

import numpy as np  # noqa: E402

from tether.imaging._rounding import round_half_away  # noqa: E402


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.5, 1),
        (1.5, 2),
        (2.5, 3),
        (3.5, 4),
        (-0.5, -1),
        (-1.5, -2),
        (-2.5, -3),
        (-3.5, -4),
        (0.0, 0),
        (-0.0, 0),
        (1.4, 1),
        (1.6, 2),
        (-1.4, -1),
        (-1.6, -2),
    ],
)
def test_round_half_away_scalar(value: float, expected: int) -> None:
    # Ties go away from zero (MATLAB round); Python's round(0.5)==0, round(2.5)==2.
    assert int(round_half_away(value)) == expected


def test_round_half_away_differs_from_builtin_on_ties() -> None:
    # The whole point: banker's rounding would give the even neighbour.
    assert round(0.5) == 0 and int(round_half_away(0.5)) == 1
    assert round(2.5) == 2 and int(round_half_away(2.5)) == 3


def test_round_half_away_vectorized() -> None:
    values = np.array([0.5, 1.5, 2.5, -0.5, -2.5, 1.4, 1.6])
    result = round_half_away(values)
    assert isinstance(result, np.ndarray)
    np.testing.assert_array_equal(result, [1.0, 2.0, 3.0, -1.0, -3.0, 1.0, 2.0])
    # NumPy's round-half-to-even disagrees on the ties.
    np.testing.assert_array_equal(np.round(values), [0.0, 2.0, 2.0, -0.0, -2.0, 1.0, 2.0])


def test_round_half_away_returns_float_castable_to_int_index() -> None:
    # Callers cast to pixel indices; confirm the cast is clean (no off-by-one).
    arr = round_half_away(np.array([4.5, 5.5])).astype(int)
    assert arr.tolist() == [5, 6]


def test_round_half_away_subulp_contract() -> None:
    # Documented contract limit of the floor(|x|+0.5) idiom: a value one ULP BELOW
    # a tie rounds up to the tie (because the float sum (x + 0.5) rounds to 1.0).
    # This never occurs for real, non-negative pixel centroids; pinned so the
    # shared idiom can't silently change if reused on arbitrary floats.
    just_below_half = np.nextafter(0.5, 0.0)  # 0.49999999999999994
    assert just_below_half < 0.5
    assert int(round_half_away(just_below_half)) == 1  # idiom quirk, not a tie at 0.5
