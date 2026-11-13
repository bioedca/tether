# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run the M1 extraction-vs-Deep-LASI acceptance oracle on the gated UCKOPSB pair.

The human-facing one-shot reporter behind the §9 M1 acceptance numbers. It runs the
same configuration as the enforcing gated test
``tests/test_oracle.py::test_extraction_meets_m1_acceptance_on_uckopsb`` (ADR-0022):

1. natively extracts the movie into a temporary ``.tether`` with registration
   **imported** from the Deep-LASI ``.tmap`` and the particle-detection mode and
   threshold decoded from the acquisition's ``.tdat`` (mode-2 intensity);
2. scores it against the Deep-LASI ``.mat`` export with
   :func:`tether.project.oracle.evaluate_project` for both the ``raw`` and
   ``corrected`` integrated intensities, at the frozen
   :data:`~tether.project.oracle.MATCH_TOL_PX` match tolerance unless ``--tol-px``
   overrides it;
3. gates the registration RMS only when the map was **fit natively**. An imported
   ``.tmap`` has no native residual, so the RMS leg is not applicable there; the
   native-fit leg is evidenced by
   ``tests/test_register.py::test_native_fit_reproduces_tmap_within_tolerance``.

The full per-metric summary is printed as JSON, and the exit status is ``0`` only when
the ``raw`` result meets acceptance.

The gated movie (~0.9 GB) is never committed; point ``--data-dir`` at the local
``example-data/bla-uckopsb-tbox-video10`` (auto-located among the repo's parents
if omitted, or via ``$TETHER_UCKOPSB_DIR``). The directory must hold exactly one
``DeepLASI_MAP_*.tmap`` and exactly one ``.tdat``, as the enforcing test requires.

Run it in the base environment::

    python scripts/run_m1_oracle.py --data-dir <dir>
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

_BASE = "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010"


def _locate(data_dir: str | None) -> dict[str, Path]:
    """Find the movie, ``.mat``, ``.tmap`` and ``.tdat`` exactly as the enforcing test does.

    A directory with more than one ``.tmap`` or ``.tdat`` is ambiguous and is skipped,
    so the movie is never paired with the wrong registration or detection settings.
    """
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
        tdats = sorted(src.glob("*.tdat")) if src.is_dir() else []
        if movie.is_file() and mat.is_file() and len(tmaps) == 1 and len(tdats) == 1:
            return {"movie": movie, "mat": mat, "tmap": tmaps[0], "tdat": tdats[0]}
    raise SystemExit(
        "Could not locate the gated UCKOPSB movie + .mat + exactly one .tmap + exactly "
        "one .tdat. Pass --data-dir or set $TETHER_UCKOPSB_DIR."
    )


def _gated_rms(summary: Any) -> float:
    """The registration RMS to gate on: the native fit residual, else ``nan`` (N/A).

    ADR-0022: an imported ``.tmap`` carries no native residual, so its RMS leg is not
    applicable. ``nan`` makes :attr:`~tether.project.oracle.OracleResult.meets_rms`
    report N/A rather than fail.
    """
    if summary.registration_source == "native":
        return float(summary.rms_residual)
    return math.nan


def _build_parser() -> argparse.ArgumentParser:
    from tether.project.oracle import MATCH_TOL_PX

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--data-dir", default=None, help="dir holding the UCKOPSB movie/.tmap/.tdat/.mat"
    )
    ap.add_argument(
        "--tol-px",
        type=float,
        default=MATCH_TOL_PX,
        help=f"match tolerance in px (default: the frozen MATCH_TOL_PX, {MATCH_TOL_PX})",
    )
    ap.add_argument("--keep", action="store_true", help="keep the extracted .tether")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    paths = _locate(args.data_dir)
    print(f"movie: {paths['movie']}")
    print(f".tmap: {paths['tmap']}")
    print(f".tdat: {paths['tdat']}")
    print(f".mat : {paths['mat']}")

    from tether.project.extract import extract_movie
    from tether.project.oracle import evaluate_project

    tmpdir = Path(tempfile.mkdtemp(prefix="m1-oracle-"))
    out = tmpdir / "uckopsb.tether"
    results = {}
    try:
        print("extracting via imported .tmap + .tdat detector settings ...")
        summary = extract_movie(paths["movie"], out, tmap=paths["tmap"], tdat=paths["tdat"])
        print(
            f"  extracted molecules: {summary.n_molecules} "
            f"(registration={summary.registration_source}, detection={summary.detection_mode})"
        )

        rms = _gated_rms(summary)
        report: dict[str, object] = {
            "registration_source": summary.registration_source,
            "detection_mode": summary.detection_mode,
            "registration_rms_px": round(rms, 4) if math.isfinite(rms) else None,
            "registration_rms_gated": math.isfinite(rms),
        }
        for intensity in ("raw", "corrected"):
            results[intensity] = evaluate_project(
                out,
                paths["mat"],
                tol_px=args.tol_px,
                intensity=intensity,
                registration_rms_px=rms,
            )
            report[intensity] = results[intensity].summary()

        print(json.dumps(report, indent=2))
    finally:
        # Always remove the (potentially large) extraction unless --keep, even if
        # extract_movie or an oracle evaluation raised above.
        if not args.keep:
            shutil.rmtree(tmpdir, ignore_errors=True)

    accepted = results["raw"].meets_acceptance()
    gate = "raw intensity, RMS gated" if math.isfinite(rms) else "raw intensity, RMS N/A (imported)"
    print(f"\nM1 ACCEPTANCE ({gate}): {'MET' if accepted else 'NOT MET'}")
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
