# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""MATLAB reference driver for Deep-LASI (#715, ADR-0074).

Deep-LASI has no function interface apart from its window: its main object builds the main
figure. The ``extract`` task opens that figure from ``matlab -batch``, hides it, and runs the
extraction of the window's own code with no step that waits for a person
(``m/deep_lasi/tether_deeplasi_extract.m`` lists the call sequence):

* ``movie``: the movie to extract.
* ``tmap``: the Deep-LASI map file of the acquisition series.
* ``tdat``: a saved Deep-LASI state whose settings the run repeats (detection mode and
  threshold, channels, masks, extraction mode, frame range). For video 010 this is the state
  of the old export, so the rerun repeats the run that made it (#715, step 3).

The outputs are the two files that the window saves, the ``.tdat`` state and the TRACY-style
``.mat`` export, plus ``deeplasi_settings.json`` with the settings that were used. The pinned
commit has no writer for a ``-donc-accc-w.txt`` file. Deep-LASI has no licence file, so its
clone stays on the workstation, and so do these outputs: they are made from private movies.

    python scripts/reference/matlab/deep_lasi.py run --clone PATH --task extract \\
        --input movie=PATH --input tmap=PATH --input tdat=PATH --out <dir>
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness  # noqa: E402

TOOL = "deep-lasi"
TASKS = {
    # frames: [first, last] of the extraction, 1-based; [] takes the state's frame range.
    # name: the part of the .mat file name after "DeepLASI_MAT_export_".
    "extract": {
        "dir": "deep_lasi",
        "entry": "tether_deeplasi_extract",
        "inputs": ["movie", "tmap", "tdat"],
        "params": {"frames": [], "name": "rerun"},
    },
}

if __name__ == "__main__":
    sys.exit(harness.main(TOOL, TASKS))
