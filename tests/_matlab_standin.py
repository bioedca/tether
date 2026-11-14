# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Stand-in for ``matlab`` in the harness tests (#700 item 8): CI has no MATLAB.

Takes ``-batch "addpath(...); tether_run_job('<job>')"`` like MATLAB, reads the job, reports the
release ``R0000x`` and writes one small known output per task, so the tests can check what the
harness records. ``params.fail = true`` makes it exit 1, like a MATLAB error under ``-batch``;
``params.touch_inputs`` names inputs it changes during the "run".
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

STANDIN_RELEASE = "0000x"


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] != "-batch":
        print("usage: _matlab_standin.py -batch COMMAND", file=sys.stderr)
        return 2
    match = re.search(r"tether_run_job\('((?:[^']|'')*)'\)", argv[1])
    if not match:
        print("no tether_run_job call in the command", file=sys.stderr)
        return 2
    job = json.loads(Path(match.group(1).replace("''", "'")).read_text(encoding="utf-8"))
    if job["params"].get("fail"):
        return 1
    if job["params"].get("return_early"):
        # The Windows launcher that returns 0 before MATLAB has run the job (#714).
        return 0
    for name in job["params"].get("touch_inputs", []):
        with open(job["inputs"][name], "a", encoding="utf-8") as fh:
            fh.write("changed while MATLAB ran\n")
    out = Path(job["output_dir"], f"{job['task']}.csv")
    out.write_text(f"entry,value\n{job['entry']},1\n", encoding="utf-8", newline="\n")
    # As tether_run_job.m: the release is reported only after the task ended without an error.
    info = {"release": STANDIN_RELEASE, "version": "0.0.0 (stand-in)"}
    Path(job["matlab_info"]).write_text(json.dumps(info), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
