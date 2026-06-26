# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Measure the cross-seed idealization-parity spread and freeze §11.2 (M0.5 S4).

Dev/ratification tool (not imported by the package, like ``make_fixtures.py``).
Runs ``tether.idealize.run_vbfret`` ``--n-runs`` times (self-reseeded) on the
committed SMD fixtures, measures the spread of the four parity metrics, and
writes ``schema/parity_tolerance.json`` — the machine-readable freeze that CI's
``sidecar.yml`` asserts against and PRD §11.2 mirrors.

Two comparison anchors (PRD §8 NFR-VALID(b), §9 M0.5):

* **281-mol** fits are compared to the *committed reference model*
  (``tests/fixtures/large/model_281mol.hdf5``) — exactly what ``sidecar.yml``
  will assert (fresh fit vs the frozen reference).
* **4-mol** has no committed model, so its fits are compared cross-seed to the
  first run (the same kind of self-reseeded spread).

Run (sidecar env on PATH via the env var)::

    TETHER_SIDECAR_PYTHON=C:/ProgramData/miniconda3/envs/tmaven/python.exe \
    NUMBA_CACHE_DIR=<persistent dir> PYTHONPATH=src \
    python scripts/measure_parity.py --n-runs 20 --out schema/parity_tolerance.json

The frozen numbers are a one-time M0.5 ratification: regenerate only with an ADR
+ a deliberate re-freeze (PRD §11.2 "frozen from the measured cross-seed spread
at M0.5").
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from tether.idealize.parity import PROVISIONAL, freeze, measure_spread

# Fixture -> (reference anchor, fitted state count). 281-mol anchors on the
# committed reference model; 4-mol anchors cross-seed on its own first run.
_FIXTURES = {
    "smd_4mol": {
        "smd": "tests/fixtures/smd_4mol.hdf5",
        "reference": None,  # cross-seed (first run)
        "nstates": 2,
    },
    "smd_281mol": {
        "smd": "tests/fixtures/large/smd_281mol.hdf5",
        "reference": "tests/fixtures/large/model_281mol.hdf5",
        "nstates": 4,
    },
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n-runs", type=int, default=20, help="self-reseeded fits per fixture")
    ap.add_argument("--margin", type=float, default=0.5, help="safety margin for freeze()")
    ap.add_argument("--out", default="schema/parity_tolerance.json")
    ap.add_argument("--scratch", default=None, help="scratch dir for run model files")
    ap.add_argument(
        "--fixtures",
        nargs="+",
        default=list(_FIXTURES),
        choices=list(_FIXTURES),
        help="which fixtures to measure",
    )
    args = ap.parse_args()

    scratch = (
        args.scratch or os.environ.get("TMPDIR") or str(Path(args.out).parent / "_parity_runs")
    )

    per_fixture: dict[str, dict] = {}
    # Worst-case spread across *all* measured fixtures defines the frozen bound.
    combined: dict[str, list[float]] = {
        "state_count_fraction": [],
        "state_mean_abs_delta": [],
        "viterbi_agreement": [],
        "relative_elbo": [],
    }

    for name in args.fixtures:
        cfg = _FIXTURES[name]
        print(f"\n=== measuring {name} (n={args.n_runs}, nstates={cfg['nstates']}) ===")
        spread, per_run = measure_spread(
            cfg["smd"],
            reference=cfg["reference"],
            n_runs=args.n_runs,
            nstates=cfg["nstates"],
            scratch_dir=str(Path(scratch) / name),
        )
        per_fixture[name] = {
            "smd": cfg["smd"],
            "reference": cfg["reference"] or "cross-seed (run00 anchor)",
            "nstates": cfg["nstates"],
            "n_comparisons": len(per_run),
            "metrics": {k: v.as_dict() for k, v in spread.items()},
        }
        for k, summ in spread.items():
            combined[k].extend(summ.values)

    # Re-summarise the pooled values to feed freeze().
    from tether.idealize.parity import SpreadSummary

    pooled = {
        "state_count_fraction": SpreadSummary(
            "state_count_fraction", "floor", combined["state_count_fraction"]
        ),
        "state_mean_abs_delta": SpreadSummary(
            "state_mean_abs_delta", "ceiling", combined["state_mean_abs_delta"]
        ),
        "viterbi_agreement": SpreadSummary(
            "viterbi_agreement", "floor", combined["viterbi_agreement"]
        ),
        "relative_elbo": SpreadSummary("relative_elbo", "ceiling", combined["relative_elbo"]),
    }
    tolerance = freeze(pooled, margin=args.margin)

    out = {
        "schema_version": 1,
        "frozen_at_milestone": "M0.5",
        "measured_utc": time.strftime("%Y-%m-%d", time.gmtime()),
        "method": {
            "model_type": "vbconhmm",
            "n_runs_per_fixture": args.n_runs,
            "sidecar_python": os.environ.get("TETHER_SIDECAR_PYTHON", "unset"),
            "note": "tMAVEN self-reseeds; parity is statistical, never bit-exact (PRD §7.4).",
        },
        "freeze_policy": (
            "frozen = more permissive of (provisional §11.2 default, measured worst-case "
            f"± margin); margin={args.margin}: ceilings ×(1+margin), floors −margin·(1−worst)."
        ),
        "provisional": PROVISIONAL,
        "tolerance": tolerance,
        "pooled_worst": {k: v.worst for k, v in pooled.items()},
        "spread_by_fixture": per_fixture,
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(f"\nFROZEN tolerance -> {out_path}")
    print(json.dumps(tolerance, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
