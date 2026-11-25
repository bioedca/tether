# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Write the CZI test files of the CZI adapter (#903, ADR-0144) with ZEISS's own writer.

Tether does not write CZI (#903 non-goal), and the ZISRAW specification does not permit a
third-party writer, so these files are written by **pylibCZIrw 6.1.0**, the Python binding of
ZEISS libCZI (LGPL-3.0). The pixel values are synthetic and seeded; the files are Tether's own
work under the repository licence.

This is a developer-only tool and never runs in CI. pylibCZIrw is not in any Tether lock::

    uv run --no-project --with pylibCZIrw==6.1.0 --with "numpy<2.2" \\
        python scripts/make_czi_fixtures.py

A second run writes the same pixels but not the same bytes: libCZI puts a new file GUID and
time stamp in each file. ``tests/fixtures/PROVENANCE.md`` records the SHA-256 of the committed
files.

Outputs, in ``tests/fixtures/czi/``:

* ``movie_zstd_64x64x64.czi`` — 64 frames of 64 x 64 Gray16, Zstd with the byte shuffle
  (``zstd1``), holding exactly ``frames(901, 64)``: the frames of the adapter conformance
  module. Pixel size 0.16 um, channel ``Cy3``.
* ``movie_raw_16x16x4.czi`` — 4 uncompressed frames of 16 x 16 Gray16 (``frames(903, 4, 16)``).
* The refusal cases, 2 planes of 8 x 8 each: ``two_channels.czi`` (C = 2), ``two_scenes.czi``
  (S = 2), ``z_stack.czi`` (Z = 2), ``two_tiles.czi`` (one frame in two mosaic tiles),
  ``rgb.czi`` (Bgr24) and ``gap_in_time.czi`` (frames T = 0 and T = 2, no T = 1).

With ``--reference PATH``, where ``PATH`` is ``T=2_CH=1.czi`` of Zenodo record 7015307
(CC-BY-4.0, a movie written by ZEN; ``tests/reference_data/manifest.toml`` id
``zeiss-czi-t2-ch1``), it instead writes ``zeiss_t2_ch1_reference.tif``: the two frames of that
movie as pylibCZIrw reads them, saved by tifffile as an uncompressed little-endian TYX TIFF.
That TIFF is the reference export of the bit-for-bit test.

libCZI 6.1.0 writes no JPEG XR, so the lossy-codec case is made inside the test: it sets the
compression field of each directory entry of ``movie_raw_16x16x4.czi`` to JPEG XR.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from pylibCZIrw import czi as pyczi

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "czi"


def frames(seed: int, n: int, size: int = 64) -> np.ndarray:
    """The seeded frames; ``frames(901, 64)`` equals the conformance module's ``_frames``."""
    rng = np.random.default_rng(seed)
    return rng.integers(0, 4000, size=(n, size, size)).astype("<u2")


def _write(name: str, planes: list[tuple[dict[str, int], np.ndarray]], **kw: object) -> None:
    path = OUT / name
    path.unlink(missing_ok=True)
    compression = kw.pop("compression", None)
    with pyczi.create_czi(str(path), exist_ok=True, compression_options=compression) as czi:
        for plane, data, *rest in planes:
            location = rest[0] if rest else (0, 0)
            scene = rest[1] if len(rest) > 1 else 0
            czi.write(
                data[..., np.newaxis] if data.ndim == 2 else data,
                location=location,
                plane=plane,
                scene=scene,
            )
        czi.write_metadata(**kw)
    print(f"{path.relative_to(OUT.parents[2])}: {path.stat().st_size} B")


def reference(czi_path: Path) -> None:
    """Export the public ZEN movie to TIFF with ZEISS's reader (the bit-for-bit reference)."""
    import tifffile

    with pyczi.open_czi(str(czi_path)) as czi:
        box = czi.total_bounding_box
        n = box["T"][1] - box["T"][0]
        stack = np.stack(
            [czi.read(plane={"T": t, "C": 0, "Z": 0})[..., 0] for t in range(n)]
        ).astype("<u2", copy=False)
    path = OUT / "zeiss_t2_ch1_reference.tif"
    tifffile.imwrite(path, stack, byteorder="<", photometric="minisblack", metadata=None)
    print(f"{path.name}: {stack.shape} {stack.dtype} {path.stat().st_size} B")


def main() -> None:
    """Write every CZI test file.

    The scales are in metres. The pylibCZIrw docstring says micrometres, but the writer puts
    the number unchanged into ``Scaling/Items/Distance/Value``, which a CZI reader takes in
    metres (ZEN's own files hold ``1E-07`` there for 0.1 um).
    """
    OUT.mkdir(parents=True, exist_ok=True)
    movie = frames(901, 64)
    _write(
        "movie_zstd_64x64x64.czi",
        [({"T": t}, movie[t]) for t in range(movie.shape[0])],
        compression="zstd1:ExplicitLevel=10;PreProcess=HiLoByteUnpack",
        document_name="tether-czi-conformance",
        channel_names={0: "Cy3"},
        scale_x=0.16e-6,
        scale_y=0.16e-6,
    )
    small = frames(903, 4, 16)
    _write(
        "movie_raw_16x16x4.czi",
        [({"T": t}, small[t]) for t in range(small.shape[0])],
        channel_names={0: "Cy5"},
        scale_x=0.1e-6,
        scale_y=0.1e-6,
    )
    a, b = frames(904, 2, 8)
    _write("two_channels.czi", [({"T": 0, "C": 0}, a), ({"T": 0, "C": 1}, b)])
    _write("two_scenes.czi", [({"T": 0}, a, (0, 0), 0), ({"T": 0}, b, (100, 0), 1)])
    _write("z_stack.czi", [({"T": 0, "Z": 0}, a), ({"T": 0, "Z": 1}, b)])
    _write("two_tiles.czi", [({"T": 0}, a, (0, 0)), ({"T": 0}, b, (8, 0))])
    rgb = np.stack([a, b, a], axis=-1).astype(np.uint8)
    _write("rgb.czi", [({"T": 0}, rgb), ({"T": 1}, rgb)])
    _write("gap_in_time.czi", [({"T": 0}, a), ({"T": 2}, b)])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reference", type=Path, help="the public ZEN movie T=2_CH=1.czi")
    args = parser.parse_args()
    if args.reference is None:
        main()
    else:
        reference(args.reference)
