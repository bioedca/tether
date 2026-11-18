# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Write (or verify) the tutorial's synthetic dual-view movie (issue #176).

``docs/tutorial/data/tutorial_movie.tif`` is the one movie the guided first run
(``docs/tutorial.md``) starts from. It is first-party synthetic output of this script
and is derived from no external dataset. It promotes the test-private writer
``tests/test_extract_cli.py::_write_movie`` into a committed, seed-fixed generator and
adds what a learner needs to see: shot noise, FRET changes and one molecule to reject.

What the file contains:

* **Shape** ``(24, 40, 80)``: 24 frames of 40 x 80 px, big-endian ``uint16``
  (byteorder ``>``), uncompressed and contiguous, like a real acquisition. The left
  40-px half is the donor view and the right half the acceptor view.
* **Background** 100 counts per pixel; **sigma** 1.5 px for every spot;
  **total amplitude** 800 counts per molecule, split between the two views as
  ``(1 - E)`` and ``E``.
* **Three colocalized pairs.** Donor centers ``(x, y)`` = ``(12, 12)``, ``(27, 17)`` and
  ``(14, 28)`` within the donor half; each acceptor copy sits at a known +1 px x
  translation in its own half.
* **Molecule 1** switches between E = 0.25 and E = 0.75 every 6 frames (four dwells).
* **Molecule 2** holds E = 0.75 for 12 frames, then drops to E = 0.25.
* **Molecule 3** holds E = 0.5 while its total brightness doubles at frame 12, so donor
  and acceptor rise together instead of mirroring each other. It is the one the
  tutorial rejects.
* **Noise.** Each pixel is a Poisson draw (``numpy.random.default_rng(176)``) around the
  noiseless image rounded to whole counts.

Regenerate the committed file, or check it is byte-identical to a fresh derivation::

    python scripts/make_tutorial_movie.py
    python scripts/make_tutorial_movie.py --check

**A second, larger movie (issue #596).** Three molecules and 24 frames are too few for the
corrections: the leakage and gamma estimates each need at least 10 molecules whose acceptor
bleaches with 20 or more frames left. ``docs/reproducibility.md`` therefore shows its sample
output from a second movie, which this script writes only on request and which is not
committed (about 3.6 MB)::

    python scripts/make_tutorial_movie.py --reproducibility-movie reproducibility_movie.tif

It never touches the tutorial movie above, and the tutorial's expected values do not depend
on it. What it contains:

* **Shape** ``(150, 78, 156)``: 150 frames of 78 x 156 px, big-endian ``uint16``, the donor
  view in the left 78-px half. Background, sigma and total amplitude as above.
* **Sixteen colocalized pairs** on a 4 x 4 grid, 18 px apart, the first at ``(12, 12)``;
  each acceptor copy again sits at +1 px x in its own half.
* **FRET.** Molecule ``k`` (0-based, row by row) switches between E = 0.3 and E = 0.7
  with a dwell of ``8 + 3 * (k % 5)`` frames, starting high when ``k`` is odd.
* **Leakage** α = 0.08: the acceptor view also receives 8 % of the donor emission. The
  detection-efficiency ratio γ is 1, because the split is exactly ``(1 - E)`` and ``E``.
* **Photobleaching.** The acceptor of molecule ``k`` bleaches at frame
  ``40 + (7 * k) % 40``; from then on the donor emits the full amplitude and the acceptor
  view holds only its leakage. The donor bleaches ``70 + (5 * k) % 20`` frames later, which
  for some molecules is after the last frame.
* **Noise.** Poisson, as above, from ``numpy.random.default_rng(596)``.
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

import numpy as np
import tifffile

_REPO_ROOT = Path(__file__).resolve().parent.parent
MOVIE_PATH = _REPO_ROOT / "docs" / "tutorial" / "data" / "tutorial_movie.tif"

SEED = 176
N_FRAMES = 24
HEIGHT = 40
WIDTH = 80  # two 40-px halves: room for the default 21-px aperture around each spot
BACKGROUND = 100.0
TOTAL_AMPLITUDE = 800.0
SIGMA = 1.5
SHIFT_X = 1.0  # acceptor = donor + 1 px in x, within its own half

#: Donor spot centers ``(x, y)`` within the donor half.
DONOR_CENTERS = np.array([[12.0, 12.0], [27.0, 17.0], [14.0, 28.0]])

# --- The reproducibility page's movie (#596) ---------------------------------------------
REPRO_SEED = 596
REPRO_GRID = 4
REPRO_SPACING = 18
REPRO_MARGIN = 12
REPRO_HALF = 2 * REPRO_MARGIN + (REPRO_GRID - 1) * REPRO_SPACING  # 78 px per view
REPRO_N_FRAMES = 150
REPRO_LEAKAGE = 0.08
REPRO_LOW_E = 0.3
REPRO_HIGH_E = 0.7

#: Donor spot centers ``(x, y)`` of the reproducibility movie, row by row.
REPRO_DONOR_CENTERS = np.array(
    [
        [REPRO_MARGIN + REPRO_SPACING * col, REPRO_MARGIN + REPRO_SPACING * row]
        for row in range(REPRO_GRID)
        for col in range(REPRO_GRID)
    ],
    dtype=float,
)


def _efficiency_and_brightness() -> tuple[np.ndarray, np.ndarray]:
    """Per-molecule, per-frame FRET efficiency and total amplitude, ``(3, N_FRAMES)``."""
    frames = np.arange(N_FRAMES)
    efficiency = np.empty((3, N_FRAMES))
    efficiency[0] = np.where((frames // 6) % 2 == 0, 0.25, 0.75)
    efficiency[1] = np.where(frames < 12, 0.75, 0.25)
    efficiency[2] = 0.5
    brightness = np.full((3, N_FRAMES), TOTAL_AMPLITUDE)
    brightness[2, 12:] = 2.0 * TOTAL_AMPLITUDE
    return efficiency, brightness


def make_stack() -> np.ndarray:
    """Return the movie as a ``(N_FRAMES, HEIGHT, WIDTH)`` big-endian ``uint16`` array."""
    efficiency, brightness = _efficiency_and_brightness()
    half = WIDTH // 2
    rows, cols = np.mgrid[0:HEIGHT, 0:WIDTH]

    def spot(x: float, y: float) -> np.ndarray:
        return np.exp(-((rows - y) ** 2 + (cols - x) ** 2) / (2.0 * SIGMA**2))

    donor_spots = [spot(x, y) for x, y in DONOR_CENTERS]
    acceptor_spots = [spot(x + half + SHIFT_X, y) for x, y in DONOR_CENTERS]

    rng = np.random.default_rng(SEED)
    stack = np.empty((N_FRAMES, HEIGHT, WIDTH), dtype=np.int64)
    for frame in range(N_FRAMES):
        expected = np.full((HEIGHT, WIDTH), BACKGROUND)
        for k in range(len(DONOR_CENTERS)):
            e, total = efficiency[k, frame], brightness[k, frame]
            expected += (1.0 - e) * total * donor_spots[k] + e * total * acceptor_spots[k]
        # Round before sampling so the Poisson draw sees whole-count means: a last-bit
        # difference in exp() between platforms cannot change the committed bytes.
        stack[frame] = rng.poisson(np.rint(expected))
    return np.ascontiguousarray(stack, dtype=">u2")


def _serialize(stack: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    tifffile.imwrite(buffer, stack, photometric="minisblack", byteorder=">")
    return buffer.getvalue()


def movie_bytes() -> bytes:
    """Serialize :func:`make_stack` exactly as the committed file is written."""
    return _serialize(make_stack())


def reproducibility_schedule() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-frame FRET efficiency ``(16, REPRO_N_FRAMES)`` and the two bleach frames.

    Returns ``(efficiency, acceptor_bleach, donor_bleach)``; a bleach frame is the first
    frame without that dye's emission and may lie past the last frame.
    """
    n = len(REPRO_DONOR_CENTERS)
    frames = np.arange(REPRO_N_FRAMES)
    efficiency = np.empty((n, REPRO_N_FRAMES))
    acceptor_bleach = np.empty(n, dtype=int)
    donor_bleach = np.empty(n, dtype=int)
    for k in range(n):
        dwell = 8 + 3 * (k % 5)
        high = (frames // dwell + k) % 2 == 1
        efficiency[k] = np.where(high, REPRO_HIGH_E, REPRO_LOW_E)
        acceptor_bleach[k] = 40 + (7 * k) % 40
        donor_bleach[k] = acceptor_bleach[k] + 70 + (5 * k) % 20
    return efficiency, acceptor_bleach, donor_bleach


def make_reproducibility_stack() -> np.ndarray:
    """Return the reproducibility page's movie as a big-endian ``uint16`` array."""
    efficiency, acceptor_bleach, donor_bleach = reproducibility_schedule()
    height, width = REPRO_HALF, 2 * REPRO_HALF
    rows, cols = np.mgrid[0:height, 0:width]

    def spot(x: float, y: float) -> np.ndarray:
        return np.exp(-((rows - y) ** 2 + (cols - x) ** 2) / (2.0 * SIGMA**2))

    donor_spots = [spot(x, y) for x, y in REPRO_DONOR_CENTERS]
    acceptor_spots = [spot(x + REPRO_HALF + SHIFT_X, y) for x, y in REPRO_DONOR_CENTERS]

    rng = np.random.default_rng(REPRO_SEED)
    stack = np.empty((REPRO_N_FRAMES, height, width), dtype=np.int64)
    for frame in range(REPRO_N_FRAMES):
        expected = np.full((height, width), BACKGROUND)
        for k in range(len(REPRO_DONOR_CENTERS)):
            if frame >= donor_bleach[k]:
                continue
            e = efficiency[k, frame] if frame < acceptor_bleach[k] else 0.0
            donor = (1.0 - e) * TOTAL_AMPLITUDE
            acceptor = e * TOTAL_AMPLITUDE + REPRO_LEAKAGE * donor
            expected += donor * donor_spots[k] + acceptor * acceptor_spots[k]
        stack[frame] = rng.poisson(np.rint(expected))
    return np.ascontiguousarray(stack, dtype=">u2")


def reproducibility_movie_bytes() -> bytes:
    """Serialize :func:`make_reproducibility_stack` as ``--reproducibility-movie`` writes it."""
    return _serialize(make_reproducibility_stack())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="re-derive the movie and fail unless it matches the committed file byte for byte",
    )
    parser.add_argument(
        "--reproducibility-movie",
        metavar="PATH",
        type=Path,
        help=(
            "write the larger movie behind docs/reproducibility.md's sample output to PATH "
            "instead (not committed; the tutorial movie is left alone)"
        ),
    )
    args = parser.parse_args(argv)

    if args.reproducibility_movie is not None:
        if args.check:
            parser.error("--check verifies the committed tutorial movie only")
        data = reproducibility_movie_bytes()
        args.reproducibility_movie.write_bytes(data)
        print(f"wrote {args.reproducibility_movie} ({len(data)} bytes)")
        return 0

    data = movie_bytes()
    if args.check:
        if not MOVIE_PATH.is_file():
            print(f"missing: {MOVIE_PATH.relative_to(_REPO_ROOT)}", file=sys.stderr)
            return 1
        if MOVIE_PATH.read_bytes() != data:
            print(
                f"stale: {MOVIE_PATH.relative_to(_REPO_ROOT)} differs from a fresh derivation; "
                "rerun scripts/make_tutorial_movie.py",
                file=sys.stderr,
            )
            return 1
        print(f"ok: {MOVIE_PATH.relative_to(_REPO_ROOT)} ({len(data)} bytes)")
        return 0

    MOVIE_PATH.parent.mkdir(parents=True, exist_ok=True)
    MOVIE_PATH.write_bytes(data)
    print(f"wrote {MOVIE_PATH.relative_to(_REPO_ROOT)} ({len(data)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
