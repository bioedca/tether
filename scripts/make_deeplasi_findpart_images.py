# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Make the detection images that Deep-LASI's ``findPart`` is run on (#924, ADR-0143).

Two images, each a CSV with one image row per line, in
``tests/fixtures/deep_lasi_findpart_images/``:

``public.csv``
    A 128 x 128 crop of a detection image that Deep-LASI itself made and saved: the first
    ``Cumulated`` image (its moving-average maximum projection) of channel 1 in
    ``DNAorigami_GR_2state_7p5nt_1color.tdat``, the one file of ``Figure_2.zip`` of Zenodo record
    7561162 (Wanninger, Asadiatouei, Bohlen, Salem, Tinnefeld, Ploetz & Lamb, CC-BY-4.0; the
    record names doi:10.1101/2023.01.31.526220, published as *Nat Commun* **14**:6564, 2023,
    doi:10.1038/s41467-023-42272-9). The crop is rows 1-128 and columns 257-384, 1-based, in
    MATLAB's orientation; no value is changed.
``synthetic.csv``
    A 48 x 48 image of seven Gaussian spots, three of them on or next to the image edge, on a
    seeded uniform background. The edge spots are what tell a zero-padded erosion from MATLAB's.

Each value is written as Python's shortest repr of the double, which MATLAB's ``readmatrix`` and
NumPy read back to the same double. The same source gives the same bytes::

    python scripts/make_deeplasi_findpart_images.py PATH/TO/Figure_2.zip OUT_DIR

The argument may also be the ``.tdat`` itself.
"""

from __future__ import annotations

import argparse
import hashlib
import tempfile
import zipfile
from pathlib import Path

import h5py
import numpy as np

from tether.io.mcos import McosDecoder, object_reference_id

ARCHIVE_SHA256 = "fccd230c630272503bcf7d3dbe1c7953c10adb9a6d87ba9570d7d1153ec570ee"
TDAT_NAME = "DNAorigami_GR_2state_7p5nt_1color.tdat"
TDAT_SHA256 = "4248894186a6353c7c642c20a73d8b33b72b7fcb7c00393a862fa243f10a0d72"
#: 0-based [row slice, col slice] of the crop, in MATLAB's (row, col) orientation.
CROP = (slice(0, 128), slice(256, 384))
#: (row, col, amplitude) of the synthetic spots; sigma and the seed are fixed below.
SYNTHETIC_SPOTS = (
    (10, 10, 1.0),
    (10, 16, 0.8),
    (30, 30, 0.6),
    (0, 20, 0.9),
    (47, 47, 0.7),
    (25, 1, 0.5),
    (40, 12, 0.3),
)
SYNTHETIC_SHAPE = (48, 48)
SYNTHETIC_SIGMA = 1.3
SYNTHETIC_NOISE = 0.02
SYNTHETIC_SEED = 924


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def public_image(tdat: Path) -> np.ndarray:
    """The crop of channel 1's first ``Cumulated`` image, as MATLAB holds it."""
    if _sha256(tdat) != TDAT_SHA256:
        raise ValueError(f"{tdat.name} is not the pinned {TDAT_NAME}")
    with h5py.File(tdat, "r") as f:
        temp = f["temp"]
        decoder = McosDecoder.from_file(f)
        for ref in np.asarray(temp["Channel"][()]).reshape(-1):
            if not ref:
                continue
            object_id = object_reference_id(np.asarray(f[ref][()]).reshape(-1))
            if decoder.property_scalar(object_id, "ChannelID") != 1.0:
                continue
            cells = decoder.property_dataset(object_id, "Cumulated")
            # h5py reads a MATLAB matrix transposed; .T gives MATLAB's (row, col).
            image = np.asarray(f[cells[0, 0]][()], dtype=np.float64).T
            return np.ascontiguousarray(image[CROP])
    raise ValueError(f"{tdat.name} has no channel 1")


def synthetic_image() -> np.ndarray:
    """Seven Gaussian spots on a seeded uniform background."""
    rows, cols = np.mgrid[0 : SYNTHETIC_SHAPE[0], 0 : SYNTHETIC_SHAPE[1]]
    image = np.zeros(SYNTHETIC_SHAPE)
    for row, col, amp in SYNTHETIC_SPOTS:
        image += amp * np.exp(-((rows - row) ** 2 + (cols - col) ** 2) / (2 * SYNTHETIC_SIGMA**2))
    image += SYNTHETIC_NOISE * np.random.default_rng(SYNTHETIC_SEED).random(SYNTHETIC_SHAPE)
    return image


def write_csv(image: np.ndarray, path: Path) -> None:
    """One image row per line, each value as the shortest repr of its double."""
    lines = (",".join(repr(float(v)) for v in row) for row in image)
    path.write_text("\n".join(lines) + "\n", encoding="ascii", newline="\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path, help="Figure_2.zip of Zenodo 7561162, or its .tdat")
    parser.add_argument("out_dir", type=Path)
    args = parser.parse_args(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    if args.source.suffix == ".zip":
        if _sha256(args.source) != ARCHIVE_SHA256:
            raise SystemExit(f"{args.source.name} is not the pinned Figure_2.zip")
        with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(args.source) as archive:
            tdat = Path(archive.extract(TDAT_NAME, tmp))
            public = public_image(tdat)
    else:
        public = public_image(args.source)
    write_csv(public, args.out_dir / "public.csv")
    write_csv(synthetic_image(), args.out_dir / "synthetic.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
