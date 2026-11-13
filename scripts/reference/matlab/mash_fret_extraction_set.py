# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Make the MASH-FRET simulated extraction set of #714 (ADR-0088) with the harness of #700.

Four two-channel videos with known truth, one ``simulate`` run each at the pinned MASH-FRET
commit, every parameter and seed below:

* ``easy``     — bright, isolated molecules on a flat background;
* ``low-snr``  — dim molecules on a brighter flat background;
* ``dense``    — many molecules, so that apertures and rings overlap;
* ``uneven``   — a TIRF-like (Gaussian) background profile in place of a flat one.

One excitation only (#714 item 4), no photobleaching, no bleedthrough or direct excitation, and
gamma 1, so that the truth of each channel is the ideal intensity MASH-FRET exports and nothing
else. The video is 160 px wide (two 80-px channels) and 80 px high, so that Tether's 21-px
aperture fits around a molecule in more than half of each channel, and it has 40 frames, so that
the compressed video stays below the 512 KB limit of the repository's large-file hook.

    python scripts/reference/matlab/mash_fret_extraction_set.py --clone PATH [--matlab EXE]

writes ``tests/fixtures/reference/mash-fret/<scenario>/`` with a ``provenance.json`` each, and
refuses an output directory that is not empty (the harness rule).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import harness  # noqa: E402
import mash_fret  # noqa: E402

OUT = HERE.parents[2] / "tests" / "fixtures" / "reference" / "mash-fret"

# Shared by every scenario. Names are MASH-FRET's own (getDefault_S); lists are rows.
COMMON = {
    "L": 40,  # frames
    "rate": 10,  # frames per second
    "pixdim": 0.25,  # um per pixel
    "viddim": [160, 80],  # width, height: two 80 x 80 channels side by side
    "camnoise": 1,  # MASH-FRET "P" model: Poisson photoelectrons, offset 113, eta 0.95, K 1
    "J": 2,
    "FRET": [0.3, 0.7],
    "wFRET": [0, 0],
    "k": [[0, 2], [2, 0]],  # s-1: a transition every 5 frames on average
    "pc_in": True,
    "wItot": 0,
    "gamma": 1,
    "wGamma": 0,
    "dE": [0, 0],
    "Bt": [0, 0],
    "bleach": False,
    "psf": True,
    "psfW": [0.32, 0.35],  # um: 1.28 and 1.40 px
    "defocus": False,
    "bgDec": False,
}

SCENARIOS = {
    "easy": {"seed": 714001, "overrides": {"N": 15, "Itot": 400, "bg": 1, "bgI": [3, 3]}},
    "low-snr": {"seed": 714002, "overrides": {"N": 15, "Itot": 80, "bg": 1, "bgI": [8, 8]}},
    "dense": {"seed": 714003, "overrides": {"N": 60, "Itot": 400, "bg": 1, "bgI": [3, 3]}},
    "uneven": {
        "seed": 714004,
        # bg = 2 is the TIRF profile: a Gaussian of amplitude bgI and widths bgW (px) centred
        # in each channel.
        "overrides": {"N": 15, "Itot": 400, "bg": 2, "bgI": [12, 12], "bgW": [30, 30]},
    },
}


def params_for(name: str) -> dict:
    spec = SCENARIOS[name]
    return {"seed": spec["seed"], "overrides": {**COMMON, **spec["overrides"]}, "name": name}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--clone", type=Path, required=True, help="MASH-FRET clone at the pin")
    parser.add_argument("--matlab", default="matlab")
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--only", choices=sorted(SCENARIOS), action="append")
    args = parser.parse_args(argv)
    try:
        for name in args.only or sorted(SCENARIOS):
            path = harness.run(
                mash_fret.TOOL,
                "simulate",
                mash_fret.TASKS,
                clone=args.clone,
                inputs={},
                out=args.out / name,
                params=params_for(name),
                matlab=args.matlab,
            )
            print(path)
    except harness.HarnessError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
