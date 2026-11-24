# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""A stand-in for ``tether/io/_bioformats_runner.py`` that needs no Java (#907, ADR-0147).

It speaks the runner's protocol (one JSON line in, one JSON line and the frame bytes out) and
serves the frames of ``<movie>.frames.npy``, so the base matrix tests the base side of the
Bio-Formats route — the pipe, the replies, the adapter and the conformance tests of #901 —
without the optional stack. ``<movie>.meta.json``, if present, holds the metadata reply and
may set ``"refuse": [kind, message]`` to make ``open`` refuse, ``"crash"`` to make it exit 3,
or ``"crash_slowly"`` to close its pipes first and exit 3 a moment later (the order Windows
shows the parent). The live runner is tested in
the ``bioformats`` tier (``tests/test_*_bioformats.py``).
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import numpy as np


def _reply(out, payload: dict, data: bytes = b"") -> None:
    out.write(json.dumps(payload).encode("utf-8") + b"\n")
    if data:
        out.write(data)
    out.flush()


def main() -> int:
    out = sys.stdout.buffer
    print("noise on stderr, as the JVM makes", file=sys.stderr)
    frames = None
    for line in sys.stdin.buffer:
        request = json.loads(line)
        op = request["op"]
        if op == "hello":
            _reply(out, {"ok": True, "bioformats": "fake", "java": "none", "python": "fake"})
        elif op == "open":
            path = Path(request["path"])
            meta_file = path.with_name(path.name + ".meta.json")
            meta = json.loads(meta_file.read_text()) if meta_file.is_file() else {}
            if "refuse" in meta:
                kind, message = meta["refuse"]
                _reply(out, {"ok": False, "error": kind, "message": message})
                continue
            if meta.get("crash"):
                return 3
            if meta.get("crash_slowly"):
                os.close(0)
                os.close(1)
                time.sleep(0.5)
                return 3
            frames = np.load(path.with_name(path.name + ".frames.npy"), mmap_mode="r")
            _reply(
                out,
                {
                    "ok": True,
                    "shape": list(frames.shape),
                    "pixel_type": frames.dtype.str,
                    "series": 1,
                    "format": "CellSens VSI",
                    "metadata": meta.get(
                        "metadata", {"frame_time": 0.05, "pixel_size": None, "channels": ["Cy3"]}
                    ),
                },
            )
        elif op == "read":
            start, stop = int(request["start"]), int(request["stop"])
            if not (0 <= start <= stop <= frames.shape[0]):
                _reply(out, {"ok": False, "error": "index", "message": f"{start}:{stop} outside"})
                continue
            data = np.ascontiguousarray(frames[start:stop]).tobytes()
            _reply(out, {"ok": True, "nbytes": len(data)}, data)
        elif op == "close":
            _reply(out, {"ok": True})
            return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
