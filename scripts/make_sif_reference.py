# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Make the reference export of a SIF file with an independent reader (#904, ADR-0150).

The SIF adapter's bit-for-bit test compares Tether's frames with the frames that
**sif_parser** (BSD-3-Clause, PyPI; not in any Tether lock) reads from the same file, saved
by tifffile as an uncompressed little-endian 32-bit float TYX TIFF. sif_parser returns the
frames in the stored row order (bottom row first); this script reverses the rows, because
Tether and Solis's own TIFF export show the top row first.

    python scripts/make_sif_reference.py IN.sif OUT.tif      # write the reference TIFF
    python scripts/make_sif_reference.py IN.sif --digest      # print shape and pixel SHA-256

It needs ``sif_parser`` and ``tifffile``; run it in a scratch environment
(``pip install sif-parser==0.3.6 tifffile``), not in the base one. The versions that made the
committed reference are in ``tests/fixtures/PROVENANCE.md``.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path


def reference_frames(path: Path):  # noqa: ANN201 - numpy is imported lazily.
    """Return the frames of ``path`` as sif_parser reads them, top row first, as ``<f4``."""
    import numpy as np
    import sif_parser

    data, _info = sif_parser.np_open(str(path))
    return np.ascontiguousarray(np.asarray(data, dtype="<f4")[:, ::-1, :])


def main(argv: list[str] | None = None) -> int:
    """Write the reference TIFF, or print the frames' shape and pixel digest."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("sif", type=Path)
    parser.add_argument("out", type=Path, nargs="?")
    parser.add_argument("--digest", action="store_true", help="print shape and pixel SHA-256")
    args = parser.parse_args(argv)
    frames = reference_frames(args.sif)
    if args.digest:
        print(list(frames.shape), hashlib.sha256(frames.tobytes()).hexdigest())
        return 0
    if args.out is None:
        parser.error("give OUT.tif or --digest")
    import tifffile

    tifffile.imwrite(args.out, frames, photometric="minisblack")
    return 0


if __name__ == "__main__":
    sys.exit(main())
