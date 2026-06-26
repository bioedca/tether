# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""MATLAB-compatible rounding for the extraction pipeline.

Deep-LASI snaps spot coordinates to pixel indices with MATLAB's ``round``, which
rounds halves **away from zero** (``round(0.5) == 1``, ``round(2.5) == 3``,
``round(-0.5) == -1``). Python's built-in :func:`round` and :func:`numpy.round`
round halves **to even** (banker's rounding: ``round(0.5) == 0``,
``round(2.5) == 2``), so an exact ``*.5`` coordinate would otherwise snap to a
different pixel than Deep-LASI. :func:`round_half_away` restores the MATLAB
convention so the native port lands on the same pixels (``extractTraces.m:9``,
``findPart.m:97``, ``Wave_Partfind.m`` brightness sampling).

**Contract.** Intended for the pipeline's pixel coordinates — non-negative,
half-integer-derived centroids (``scipy.ndimage.center_of_mass`` outputs,
imported map coordinates). The ``floor(|x| + 0.5)`` idiom rounds a value within
one ULP *below* a tie up to the tie (``round_half_away(nextafter(0.5, 0)) == 1``);
this never occurs for real pixel centroids and is locked by a regression test.
"""

from __future__ import annotations

import numpy as np


def round_half_away(value: np.ndarray | float) -> np.ndarray | np.floating:
    """Round to the nearest integer, ties away from zero (MATLAB ``round``).

    Works on scalars and arrays. Returns a floating result (``numpy`` scalar for
    scalar input); callers that need integer pixel indices cast with ``int(...)``
    or ``.astype(int)``.
    """
    return np.sign(value) * np.floor(np.abs(value) + 0.5)
