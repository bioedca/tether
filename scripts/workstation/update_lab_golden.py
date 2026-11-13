# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Write the laboratory golden files on the workstation and print what moved (#703).

Usage, on the workstation, in the locked environment, from a clone of the commit to freeze::

    python scripts/workstation/update_lab_golden.py --data <data folder>
    python scripts/workstation/update_lab_golden.py --data <data folder> --check
    python scripts/workstation/update_lab_golden.py --data <data folder> --only lab-03

Each data acquisition of ``tests/_lab_golden.py`` (``lab-01`` to ``lab-10``) runs from its movie
through the default pipeline with the map file of its series, in a temporary folder. The script
then writes ``tests/golden/workstation/<id>.json`` with summary numbers only and prints the
same Markdown old/new table as ``scripts/update_golden.py``; a pull request that changes one of
these files pastes that table with a ``Cause:`` line (the ``golden-table`` workflow checks it).

The data folder and its name-mapping file stay on the workstation. The script prints neutral
ids, quantity names and numbers, never a file name, and it refuses to write a file that the
format rule of ``_lab_golden.validate_document`` rejects.

Exit status: ``0`` when nothing moved or the files were rewritten, ``1`` under ``--check`` when
a quantity moved, ``2`` on a usage error or a failed acquisition.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_TESTS = _REPO / "tests"
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))

import _golden  # noqa: E402
import _lab_golden  # noqa: E402


def _runner():
    path = Path(__file__).resolve().parent / "run_private_checks.py"
    spec = importlib.util.spec_from_file_location("tether_run_private_checks_update", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data", type=Path, required=True, help="the private data folder")
    parser.add_argument(
        "--check", action="store_true", help="write nothing; exit 1 when a quantity moved"
    )
    parser.add_argument(
        "--only",
        action="append",
        choices=_lab_golden.LAB_ACQUISITIONS,
        help="run only this neutral id (repeatable)",
    )
    parser.add_argument(
        "--golden-dir",
        type=Path,
        default=_lab_golden.LAB_GOLDEN_DIR,
        help="directory of the laboratory golden files (default: tests/golden/workstation)",
    )
    args = parser.parse_args(argv)
    ids = args.only or list(_lab_golden.LAB_ACQUISITIONS)
    data = _runner().PrivateData(args.data, "scripts/workstation/update_lab_golden.py")

    changes: dict[str, list[_golden.Difference]] = {}
    results: dict[str, dict] = {}
    with tempfile.TemporaryDirectory(prefix="tether-lab-golden-") as tmp:
        for nid in ids:
            print(f"{nid}: running", flush=True)
            try:
                movie = data.path(nid, _lab_golden.movie_role(data.roles(nid)))
                tmap = data.path(nid, _lab_golden.MAP_ROLE)
                new = _lab_golden.lab_quantities(movie, tmap, Path(tmp) / nid)
            except Exception as exc:  # noqa: BLE001 - report the id and the kind, not the text
                print(f"{nid}: failed ({type(exc).__name__}); nothing was written", file=sys.stderr)
                return 2
            problems = _lab_golden.validate_document(
                _lab_golden.document(nid, new), expected_id=nid
            )
            if problems:
                message = "; ".join(problems)
                print(f"{nid}: {message}; nothing was written", file=sys.stderr)
                return 2
            old = _lab_golden.load_lab_golden(nid, args.golden_dir) or {}
            diffs = _golden.compare(old, new, rtol=0.0)
            if diffs:
                changes[nid] = diffs
                results[nid] = new
    # Write only after every acquisition ran, as update_golden.py does.
    if not args.check:
        for nid, new in results.items():
            _lab_golden.dump_lab_golden(nid, new, args.golden_dir)

    if not changes:
        print("No laboratory golden quantity moved.")
        return 0
    print(_golden.diff_table(changes))
    print()
    if args.check:
        print("Laboratory golden quantities moved; rerun without --check to rewrite them.")
        return 1
    files = ", ".join(f"tests/golden/workstation/{n}.json" for n in sorted(changes))
    print(f"Rewrote {files}. Paste the table above into the pull request with a 'Cause:' line.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
