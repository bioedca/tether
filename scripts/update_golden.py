# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Regenerate the self-drift golden files and print what moved (#702, ADR-0078).

Usage::

    python scripts/update_golden.py            # rewrite every golden file, print the table
    python scripts/update_golden.py --check    # write nothing; exit 1 if anything moved
    python scripts/update_golden.py --only synthetic_traces

Each input of ``tests/_golden.py`` runs end to end in a temporary directory. The command
prints one Markdown table: golden file, quantity, old value, new value, relative change. A
pull request that changes a file under ``tests/golden/`` pastes that table into its body
with a ``Cause:`` line; the ``golden-table`` workflow checks that it did.

Exit status: ``0`` when nothing moved or the files were rewritten, ``1`` under ``--check``
when a quantity moved, ``2`` on a usage error.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

_TESTS = Path(__file__).resolve().parents[1] / "tests"
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))

import _golden  # noqa: E402


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--check", action="store_true", help="write nothing; exit 1 when a quantity moved"
    )
    parser.add_argument(
        "--only",
        action="append",
        choices=sorted(_golden.INPUTS),
        help="regenerate only this input (repeatable)",
    )
    parser.add_argument(
        "--golden-dir",
        type=Path,
        default=_golden.GOLDEN_DIR,
        help="directory of the golden files (default: tests/golden)",
    )
    args = parser.parse_args(argv)
    names = args.only or sorted(_golden.INPUTS)

    changes: dict[str, list[_golden.Difference]] = {}
    results: dict[str, dict] = {}
    with tempfile.TemporaryDirectory(prefix="tether-golden-") as tmp:
        for name in names:
            new = _golden.run_input(name, Path(tmp) / name)
            old = _golden.load_golden(name, args.golden_dir) or {}
            # rtol=0: the table lists every change, also one inside the test tolerance,
            # so a rewrite never moves a value without saying so.
            diffs = _golden.compare(old, new, rtol=0.0)
            if diffs:
                changes[name] = diffs
                results[name] = new
    # Write only after every input ran: a failure part way leaves every golden file as it
    # was, so a retry still compares with the old values and its table shows every change.
    if not args.check:
        for name, new in results.items():
            _golden.dump_golden(name, new, args.golden_dir)

    if not changes:
        print("No golden quantity moved.")
        return 0
    print(_golden.diff_table(changes))
    print()
    if args.check:
        print("Golden quantities moved; run `python scripts/update_golden.py` to rewrite them.")
        return 1
    files = ", ".join(f"tests/golden/{n}.json" for n in sorted(changes))
    print(f"Rewrote {files}. Paste the table above into the pull request with a 'Cause:' line.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
