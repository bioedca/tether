# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Write the deep-classifier golden file and print what moved (#759, ADR-0098).

Usage, in the deep env (``deep/conda-lock.yml``), from a clone of the commit to freeze::

    TETHER_REFERENCE_DOWNLOAD=1 python scripts/update_deep_golden.py
    TETHER_REFERENCE_DOWNLOAD=1 python scripts/update_deep_golden.py --check
    TETHER_REFERENCE_DOWNLOAD=1 python scripts/update_deep_golden.py --seed 3 --measure out.json

``TETHER_REFERENCE_DOWNLOAD=1`` lets the script download the PySMACKS traces when they are not in
the reference-data cache yet; without it, and with no cached copy, it stops with exit ``2``.
``--check`` and ``--measure`` exclude each other.

The script trains the deep classifier once on the train part of the PySMACKS tutorial labels,
with the training seed of ``schema/deep_classifier_reference.json``, scores the test part, writes
``tests/golden/deep/pysmacks_classifier.json`` and prints the same Markdown old/new table as
``scripts/update_golden.py``. A pull request that changes the file pastes that table with a
``Cause:`` line; the ``golden-table`` workflow checks it (the rule of #702).

``--measure`` writes the quantities of one training to a JSON file and touches no golden file.
The ten runs and the ten seeds of ADR-0098 were measured with it.

Exit status: ``0`` when nothing moved or the file was rewritten, ``1`` under ``--check`` when a
quantity moved, ``2`` when the data set is not here.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_TESTS = _REPO / "tests"
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))

import _deep_reference  # noqa: E402
import _golden  # noqa: E402


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    # --check compares with the golden file and --measure never reads it, so one run does one.
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check", action="store_true", help="write nothing; exit 1 when a quantity moved"
    )
    parser.add_argument("--seed", type=int, help="training seed (default: the reference file's)")
    mode.add_argument(
        "--measure", type=Path, help="write the quantities of one training here and stop"
    )
    args = parser.parse_args(argv)

    reference = _deep_reference.load_reference()
    try:
        samples = _deep_reference.load_samples(reference["dataset"]["id"])
    except BaseException as exc:  # pytest's Skipped is a BaseException
        if type(exc).__name__ != "Skipped":
            raise
        print(f"error: {exc} (set TETHER_REFERENCE_DOWNLOAD=1)", file=sys.stderr)
        return 2
    run = _deep_reference.train_and_score(samples, reference, seed=args.seed)

    if args.measure is not None:
        doc = {"seed": run.trained.hyperparameters["seed"], "quantities": run.quantities}
        args.measure.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
        print(json.dumps(doc))
        return 0
    if args.seed is not None and args.seed != reference["training"]["seed"]:
        parser.error("the golden file holds the reference seed only; use --measure with --seed")

    old = _golden.load_golden(_deep_reference.GOLDEN_NAME, _deep_reference.GOLDEN_DIR) or {}
    # rtol=0: the table lists every change, also one inside the test's limit.
    diffs = _golden.compare(old, run.quantities, rtol=0.0)
    if diffs:
        print(_golden.diff_table({_deep_reference.GOLDEN_NAME: diffs}))
    else:
        print("nothing moved")
    if args.check:
        return 1 if diffs else 0
    if diffs:
        path = _deep_reference.dump_golden(run.quantities)
        print(f"wrote {path.relative_to(_REPO).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
