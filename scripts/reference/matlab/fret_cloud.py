# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""MATLAB reference driver for the laboratory FRET_cloud scripts (#710, ADR-0105).

The scripts are "FRET_cloud scripts, Eduardo Campos Chavez, 2025, unpublished". They have no
public commit, so ``pins.toml`` pins them by the SHA-256 of each file (``kind = "files"``), and
``--clone`` names a folder on the workstation that holds the pinned file. The scripts stay on the
workstation; no line of them enters the repository.

The one task runs ``FRET_cloud_contours_centroids.m`` unchanged on every trace file of the input
``traces`` (the smfret-analysis2 test traces) and writes what the script computed:
``files.csv``, ``pairs.csv``, ``density.h5``, ``thresholds.csv``, ``centroids.csv`` and
``toolbox.csv`` (``m/fret_cloud/tether_fret_cloud.m`` says how). The script's own constants
(column 4, 50 pairs a file, a 200 x 200 grid on [0, 1], bandwidth 0.05, coverages 10 % to 90 %,
4 centroids with 5 replicates) are not parameters: the script sets them after its ``clear``.
``seed`` seeds the random stream of its ``kmeans``.

    python scripts/reference/matlab/fret_cloud.py run --clone FOLDER --task cloud \\
        --input traces=tests/fixtures/smfret_analysis2/traces \\
        --out tests/fixtures/reference/fret-cloud-scripts/sa2-traces
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness  # noqa: E402

TOOL = "fret-cloud-scripts"
TASKS = {
    "cloud": {
        "dir": "fret_cloud",
        "entry": "tether_fret_cloud",
        "inputs": ["traces"],
        "params": {"seed": 0},
    },
}

if __name__ == "__main__":
    sys.exit(harness.main(TOOL, TASKS))
