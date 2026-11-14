# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Make the committed extract of a public MASH-FRET project file (#711, ADR-0087).

The source is ``eD135e_encapsulated_30mMMg_25C.mash``, the smallest of the nine MASH-FRET project
files in ``02_smFRET.zip`` of Zenodo record 24104528 (Zelger-Paulus, Hadzic, Lindner, Börner,
Steffen, Fazliji, Johannsen & Sigel, doi:10.5281/zenodo.24104528, CC-BY-4.0). That file is
4.9 MB; a ``git``-tier reference file may be at most 1 MB (ADR-0073), so the extract keeps:

* the first ``N_MOLECULES`` molecules in file order, every frame and both lasers;
* of each molecule, the background-corrected, cross-talk-corrected and denoised intensities, the
  discretised FRET (``FRET_DTA``), the included frames and its Trace-processing settings
  (``prmTT``), unchanged;
* the project fields that give those arrays their meaning (``MASH_version``, ``nb_channel``,
  ``nb_excitations``, ``excitations``, ``chanExc``, ``labels``, ``FRET``, ``fix``), unchanged.

It leaves out the raw intensities, which no test compares (the file holds no video, so the
background subtraction cannot be repeated), and every path and free-text field of the project,
such as ``proj_file`` and ``exp_parameters``. No value is changed. The result is a MATLAB v5 file
that ``tests/_mashfret.py`` reads exactly as it reads the full project. Its header text is fixed,
so the same source gives the same bytes::

    python scripts/make_mashfret_fixture.py PATH/TO/02_smFRET.zip

The argument may also be the ``.mash`` file itself. The weekly workstation test
(``tests/test_mashfret_reference.py``) calls :func:`extract` on the full archive and compares the
result with the committed file.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import sys
import zipfile
from pathlib import Path

import numpy as np
import scipy.io as sio

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "tests" / "fixtures" / "mashfret_ed135e_30mm_extract.mat"
MEMBER = "02_smFRET/eD135e_encapsulated_30mMMg_25C.mash"
MEMBER_SHA256 = "ee448ffddad370d0ae1530a838aeca66b380d3e0f35e677d368d98583f17be42"
N_MOLECULES = 18
PROJECT_FIELDS = (
    "MASH_version",
    "nb_channel",
    "nb_excitations",
    "excitations",
    "chanExc",
    "labels",
    "FRET",
    "fix",
)
#: The 116 header bytes of a MATLAB v5 file are free text; scipy writes the time into them.
HEADER = b"MATLAB 5.0 MAT-file, extract of a MASH-FRET project by scripts/make_mashfret_fixture.py"


def source_bytes(path: Path) -> bytes:
    """The bytes of the source project, from the archive or from the ``.mash`` file itself."""
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            return zf.read(MEMBER)
    return path.read_bytes()


def extract(data: bytes) -> bytes:
    """The bytes of the committed extract, made from the bytes of the source project."""
    digest = hashlib.sha256(data).hexdigest()
    if digest != MEMBER_SHA256:
        raise ValueError(f"the source has SHA-256 {digest}, not {MEMBER_SHA256}")
    raw = sio.loadmat(io.BytesIO(data), squeeze_me=False, struct_as_record=False)
    n_channels = int(np.asarray(raw["nb_channel"]).ravel()[0])
    n_pairs = np.asarray(raw["FRET"]).reshape(-1, 2).shape[0]
    keep = slice(0, N_MOLECULES)
    columns = slice(0, N_MOLECULES * n_channels)
    out = {name: raw[name] for name in PROJECT_FIELDS}
    for name in ("intensities_bgCorr", "intensities_crossCorr", "intensities_denoise"):
        out[name] = raw[name][:, columns, ...]
    out["FRET_DTA"] = raw["FRET_DTA"][:, : N_MOLECULES * n_pairs]
    out["bool_intensities"] = raw["bool_intensities"][:, keep]
    out["prmTT"] = raw["prmTT"][:, keep]
    buf = io.BytesIO()
    sio.savemat(buf, out, format="5", do_compression=True, oned_as="row")
    blob = bytearray(buf.getvalue())
    blob[:116] = HEADER.ljust(116)
    return bytes(blob)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path, help="02_smFRET.zip, or the .mash file itself")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    blob = extract(source_bytes(args.source))
    args.out.write_bytes(blob)
    print(f"{args.out}: {len(blob)} B, sha256 {hashlib.sha256(blob).hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
