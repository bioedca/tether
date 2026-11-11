# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""MATLAB reference driver for smfret-analysis2 (#700, ADR-0074; used by #709).

Each task calls one function of the tool's ``bin/`` folder on every trace file of the input
``traces`` (a file or a folder of ``*.dat`` files: time, donor, acceptor in columns 1 to 3) and
writes one CSV. ``create_histogram.m`` is an interactive script (``uigetfile``, ``input``), so it
is not a task: its saved ``total_FRET.csv`` and ``total_XC.csv`` are frozen with ``freeze``.

    python scripts/reference/matlab/smfret_analysis2.py run --clone PATH --task crosscorr \\
        --input traces=PATH --out tests/fixtures/reference/smfret-analysis2/<run>
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness  # noqa: E402

TOOL = "smfret-analysis2"
TASKS = {
    # bin/calculate_cross_corr.m: one value for each lag 0..N-1, divided by (N - lag) and by
    # std(donor) * std(acceptor). Columns: trace, lag, cross_correlation.
    "crosscorr": {"dir": "smfret_analysis2", "entry": "tether_sa2_crosscorr", "inputs": ["traces"]},
    # bin/calculate_fret.m: acceptor / (acceptor + donor), each frame. Columns: trace, frame, fret.
    "fret": {"dir": "smfret_analysis2", "entry": "tether_sa2_fret", "inputs": ["traces"]},
}

if __name__ == "__main__":
    sys.exit(harness.main(TOOL, TASKS))
