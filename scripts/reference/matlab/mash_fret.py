# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""MATLAB reference driver for MASH-FRET (#700, ADR-0074; used by #713 and #714).

MASH-FRET has no function interface apart from its window. The ``simulate`` task opens the
window from ``matlab -batch`` and drives it with MASH-FRET's own routine helpers (the ones its
``routinetest_S`` uses): the default simulation parameters of ``getDefault_S``, changed by the
``overrides`` parameter, then generation and export to the output directory. ``seed`` is given to
``rng`` before generation. Where a result is only in a file the window saved, use ``freeze``
(ADR-0074, rule of saved files first).

    python scripts/reference/matlab/mash_fret.py run --clone PATH --task simulate \\
        --param seed=1 --param 'overrides={"L": 300}' --out <dir>

The first real run of this task made the extraction set of #714
(``mash_fret_extraction_set.py``, ADR-0086).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness  # noqa: E402

TOOL = "mash-fret"
TASKS = {
    # export: the seven switches of set_S_fileExport (.mat, .sira, .avi, .txt, .dt, .log, .crd).
    # units: the export unit list (1 photon counts, 2 electron counts = the camera's image
    # counts). gzip_video: compress the .sira losslessly so that it fits the git tier (#714).
    "simulate": {
        "dir": "mash_fret",
        "entry": "tether_mash_simulate",
        "inputs": [],
        "params": {
            "seed": 0,
            "overrides": {},
            "export": [False, True, False, True, False, True, True],
            "units": 2,
            "gzip_video": True,
            "name": "sim",
        },
    },
}

if __name__ == "__main__":
    sys.exit(harness.main(TOOL, TASKS))
