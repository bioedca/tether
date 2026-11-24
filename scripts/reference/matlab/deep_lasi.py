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

The outputs of ``extract`` are the two files that the window saves, the ``.tdat`` state and the
TRACY-style ``.mat`` export, plus ``deeplasi_settings.json`` with the settings that were used.
The pinned commit has no writer for a ``-donc-accc-w.txt`` file. Deep-LASI has no licence file,
so its clone stays on the workstation, and so do these outputs: they are made from private
movies.

The ``findpart`` task calls Deep-LASI's ``findPart`` directly, with no window, on detection
images (one CSV each, in the ``images`` folder), for the detection mode and thresholds in its
parameters, and writes each result as ``<image>_t<k>.csv``
(``m/deep_lasi/tether_deeplasi_findpart.m``). Its inputs are public or synthetic and committed
(``tests/fixtures/deep_lasi_findpart_images/``), so its outputs are committed too
(``tests/fixtures/reference/deep-lasi/findpart-zmw/``; #924, ADR-0143).

    python scripts/reference/matlab/deep_lasi.py run --clone PATH --task extract \\
        --input movie=PATH --input tmap=PATH --input tdat=PATH --out <dir>
    python scripts/reference/matlab/deep_lasi.py run --clone PATH --task findpart \\
        --input images=tests/fixtures/deep_lasi_findpart_images --out <dir>
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
    # The detection of findPart.m on detection images, with no window (#924, ADR-0143). The
    # input is a folder of CSV images; thresholds are indexed 1.. in the output names.
    "findpart": {
        "dir": "deep_lasi",
        "entry": "tether_deeplasi_findpart",
        "inputs": ["images"],
        "params": {"mode": 5, "thresholds": [0.1, 0.3, 0.5], "refine": False},
    },
}

if __name__ == "__main__":
    sys.exit(harness.main(TOOL, TASKS))
