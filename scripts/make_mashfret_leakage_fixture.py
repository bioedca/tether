# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Make the committed extract of a MASH-FRET project with a non-zero leakage coefficient (#870).

The source is ``2chan2exc.mash``, a test project of the MASH-FRET repository
(``MASH-FRET/source/routines/bar-menu/assets/2chan2exc.mash`` at commit ``b577928``, the commit
that added it; MASH-FRET is GPL-3.0). MASH-FRET "1.3.2 (prev. commit: 3471fdf3)" wrote it, with a
bleedthrough coefficient of 0.0775 and a direct-excitation coefficient of 0.0176. It holds 30
molecules of 2000 frames, two channels and two lasers. It is 2.0 MB; a ``git``-tier reference file
may be at most 1 MB and a plain fixture 512 KiB (ADR-0073), so the extract keeps:

* the first ``N_MOLECULES`` molecules in file order, every frame and both lasers;
* of each molecule, the background-corrected and the cross-talk-corrected intensities, unchanged;
* the project fields that give those arrays their meaning (``MASH_version``, ``nb_channel``,
  ``nb_excitations``, ``excitations``, ``chanExc``, ``labels``, ``FRET``) and, in a ``TP``
  structure as in the source, ``TP.fix``, which holds the coefficients, all unchanged.

It leaves out everything else, among it the raw intensities and every path field of the project
(``proj_file``, ``folderRoot``). No value is changed. The 116-byte MATLAB header text is fixed, so
the same source gives the same bytes::

    python scripts/make_mashfret_leakage_fixture.py PATH/TO/2chan2exc.mash

The weekly ``reference-data`` job downloads the source (manifest id ``mashfret-2chan2exc-project``)
and compares :func:`extract` of it with the committed file
(``tests/test_reference_data_download.py``).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import sys
from pathlib import Path

import numpy as np
import scipy.io as sio

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "tests" / "fixtures" / "mashfret_2chan2exc_leakage_extract.mat"
SOURCE_SHA256 = "adac4b599652fb264caae52613eb81bb66cc40915ea137d648931ccacfa913c6"
N_MOLECULES = 10
PROJECT_FIELDS = (
    "MASH_version",
    "nb_channel",
    "nb_excitations",
    "excitations",
    "chanExc",
    "labels",
    "FRET",
)
#: The 116 header bytes of a MATLAB v5 file are free text; scipy writes the time into them.
HEADER = b"MATLAB 5.0 MAT-file, extract of a MASH-FRET project by make_mashfret_leakage_fixture.py"


def extract(data: bytes) -> bytes:
    """The bytes of the committed extract, made from the bytes of the source project."""
    digest = hashlib.sha256(data).hexdigest()
    if digest != SOURCE_SHA256:
        raise ValueError(f"the source has SHA-256 {digest}, not {SOURCE_SHA256}")
    raw = sio.loadmat(io.BytesIO(data), squeeze_me=False, struct_as_record=False)
    n_channels = int(np.asarray(raw["nb_channel"]).ravel()[0])
    columns = slice(0, N_MOLECULES * n_channels)
    out = {name: raw[name] for name in PROJECT_FIELDS}
    out["TP"] = {"fix": raw["TP"][0, 0].fix}
    for name in ("intensities_bgCorr", "intensities_crossCorr"):
        out[name] = raw[name][:, columns, :]
    buf = io.BytesIO()
    sio.savemat(buf, out, format="5", do_compression=True, oned_as="row")
    blob = bytearray(buf.getvalue())
    blob[:116] = HEADER.ljust(116)
    return bytes(blob)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path, help="the 2chan2exc.mash file")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    blob = extract(args.source.read_bytes())
    args.out.write_bytes(blob)
    print(f"{args.out}: {len(blob)} B, sha256 {hashlib.sha256(blob).hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
