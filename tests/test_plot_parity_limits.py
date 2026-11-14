# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""``schema/plot_parity_limits.json`` keeps each limit tied to its measurement (#707).

The live comparison runs only in the sidecar job; this unmarked check runs everywhere. Each
limit must be its recorded measurement rounded up at the second significant digit, so a limit
cannot be widened (or a measurement moved under it) without the other changing too.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

LIMITS = Path(__file__).resolve().parents[1] / "schema" / "plot_parity_limits.json"


def _round_up_2sig(value: float) -> float:
    if value == 0:
        return 0.0
    exp = math.floor(math.log10(value))
    return float(f"{math.ceil(round(value / 10 ** (exp - 1), 9))}e{exp - 1}")


def test_every_limit_is_its_measurement_rounded_up() -> None:
    doc = json.loads(LIMITS.read_text("utf-8"))
    entries = [(b, k, e) for b, blk in doc["limits"].items() for k, e in blk["values"].items()]
    # 10 A2b selections against the app, 2 x 6 B3 pairs, 1 C1. A2b against the tMAVEN
    # routine is an exact assertion since #806 and needs no limit.
    assert len(entries) == 23
    for block, key, entry in entries:
        measured, limit = entry["measured"], entry["limit"]
        assert math.isfinite(measured) and 0.0 <= measured <= 1.0, (block, key)
        assert limit == _round_up_2sig(measured), (block, key, measured, limit)


def test_rounding_rule() -> None:
    assert _round_up_2sig(0.006120016) == 0.0062
    assert _round_up_2sig(0.0004) == 0.0004
    assert _round_up_2sig(0.16725978) == 0.17
    assert _round_up_2sig(0.0) == 0.0
