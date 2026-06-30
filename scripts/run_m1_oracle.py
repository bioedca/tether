# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run the M1 extraction-vs-Deep-LASI acceptance oracle on the gated UCKOPSB pair.

The human-facing one-shot reporter behind the §9 M1 acceptance numbers (the gated
``@pytest.mark.large`` test in ``tests/test_oracle.py`` asserts the same thing).
It (1) natively extracts the movie via the imported Deep-LASI ``.tmap`` — the
apples-to-apples registration leg — into a temporary ``.tether``, then (2) scores
it against the Deep-LASI ``.mat`` export with
:func:`tether.project.oracle.evaluate_project` for both the ``raw`` and
``corrected`` integrated intensities, and (3) reports the native ``.tmap`` fit RMS
(registration gate). The full per-metric summary is printed as JSON.

The gated movie (~0.9 GB) is never committed; point ``--data-dir`` at the local
``example-data/bla-uckopsb-tbox-video10`` (auto-located among the repo's parents
if omitted, or via ``$TETHER_UCKOPSB_DIR``).

Run locally (deps are not in the dev shell)::

    uv run --no-project --python 3.12 --with numpy --with scipy --with scikit-image \\
        --with tifffile --with h5py python scripts/run_m1_oracle.py
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

_BASE = "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010"


def _locate(data_dir: str | None) -> dict[str, Path]:
    candidates: list[Path] = []
    if data_dir:
        candidates.append(Path(data_dir))
    env = os.environ.get("TETHER_UCKOPSB_DIR")
    if env:
        candidates.append(Path(env))
    for parent in Path(__file__).resolve().parents:
        candidates.append(parent / "example-data" / "bla-uckopsb-tbox-video10")
    for src in candidates:
        movie = src / f"{_BASE}.tif"
        mat = src / f"DeepLASI_MAT_export_{_BASE}.mat"
        tmaps = sorted(src.glob("DeepLASI_MAP_*.tmap")) if src.is_dir() else []
        if movie.is_file() and mat.is_file() and tmaps:
            return {"movie": movie, "mat": mat, "tmap": tmaps[0]}
    raise SystemExit(
        "Could not locate the gated UCKOPSB movie + .tmap + Deep-LASI .mat. "
        "Pass --data-dir or set $TETHER_UCKOPSB_DIR."
    )


def _native_tmap_rms(tmap_path: Path) -> float:
    """The native fit RMS of the imported .tmap's own control points (the §9 gate)."""
    from tether.imaging.calibrate import registration_map_from_tmap
    from tether.imaging.register import read_tmap

    reg_map = registration_map_from_tmap(read_tmap(tmap_path))
    return float(reg_map.rms_residual)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default=None, help="dir holding the UCKOPSB movie/.tmap/.mat")
    ap.add_argument("--tol-px", type=float, default=1.0, help="match tolerance (px)")
    ap.add_argument("--keep", action="store_true", help="keep the extracted .tether")
    args = ap.parse_args()

    paths = _locate(args.data_dir)
    print(f"movie: {paths['movie']}")
    print(f".tmap: {paths['tmap']}")
    print(f".mat : {paths['mat']}")

    from tether.project.extract import extract_movie
    from tether.project.oracle import evaluate_project

    tmpdir = Path(tempfile.mkdtemp(prefix="m1-oracle-"))
    out = tmpdir / "uckopsb.tether"
    try:
        print("extracting via imported .tmap ...")
        summary = extract_movie(paths["movie"], out, tmap=paths["tmap"])
        print(
            f"  extracted molecules: {summary.n_molecules} (source={summary.registration_source})"
        )

        rms = _native_tmap_rms(paths["tmap"])
        report: dict[str, object] = {
            "registration_rms_px_native_tmap_fit": round(rms, 4),
            "registration_rms_gate_0.5px": bool(rms <= 0.5),
        }
        for intensity in ("raw", "corrected"):
            res = evaluate_project(out, paths["mat"], tol_px=args.tol_px, intensity=intensity)
            report[intensity] = res.summary()

        print(json.dumps(report, indent=2))
    finally:
        # Always remove the (potentially large) extraction unless --keep, even if
        # extract_movie or an oracle evaluation raised above.
        if not args.keep:
            import shutil

            shutil.rmtree(tmpdir, ignore_errors=True)

    raw = report["raw"]
    accepted = bool(raw["meets_recall"]) and bool(raw["meets_pearson"]) and bool(rms <= 0.5)  # type: ignore[index]
    print(f"\nM1 ACCEPTANCE (raw intensity + native RMS): {'MET' if accepted else 'NOT MET'}")
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
