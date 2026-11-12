# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fail a pull request that changes a golden file without the old/new table (#702, ADR-0078).

The maintainer approves each change of a frozen number on the pull request, which must show the
old value, the new value and the cause (maintainer decision Q6). ``scripts/update_golden.py``
prints the table; this check, run by ``.github/workflows/golden-table.yml``, reads the pull
request body from the GitHub event and requires, when a file under ``tests/golden/`` changed:

* the table header that ``update_golden.py`` prints,
* a row of that table for each changed golden file (its name in the first cell), and
* a line that starts with ``Cause:`` and says something.

Text inside HTML comments does not count, so the template's own guidance cannot satisfy it.
Standard library only; it never runs code from the pull request.

Usage::

    python scripts/check_golden_table.py --event "$GITHUB_EVENT_PATH"
    python scripts/check_golden_table.py --body-file body.md --changed tests/golden/x.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path, PurePosixPath

GOLDEN_PREFIX = "tests/golden/"
#: Must equal ``_golden.TABLE_HEADER`` (``tests/test_golden.py`` asserts it).
TABLE_HEADER = "| golden file | quantity | old | new | relative change |"
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_CAUSE = re.compile(r"^[ \t]*(?:[-*][ \t]+)?(?:\*\*)?Cause:(?:\*\*)?[ \t]*\S", re.MULTILINE)


def golden_files(changed: Iterable[str]) -> list[str]:
    """The changed paths that are golden files, sorted."""
    return sorted(p for p in changed if p.startswith(GOLDEN_PREFIX) and p.endswith(".json"))


def table_files(text: str) -> set[str]:
    """The golden-file names in the first cell of the rows of each golden table in ``text``.

    A table starts at a line that is :data:`TABLE_HEADER` and runs while lines start with
    ``|``. Only a row with the table's five cells counts, and the separator row does not, so a
    name that appears only in prose, or a header with no rows under it, names nothing.
    """
    names: set[str] = set()
    in_table = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == TABLE_HEADER:
            in_table = True
            continue
        if not in_table:
            continue
        if not stripped.startswith("|"):
            in_table = False
            continue
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", stripped)[1:-1]]
        if len(cells) != 5 or set(cells[0]) <= set("-: "):
            continue
        first = cells[0]
        if len(first) > 2 and first.startswith("`") and first.endswith("`"):
            names.add(first[1:-1])
    return names


def problems(body: str, changed: Iterable[str]) -> list[str]:
    """What the pull request body lacks; empty when it passes."""
    golden = golden_files(changed)
    if not golden:
        return []
    text = _COMMENT.sub("", body or "")
    found: list[str] = []
    if TABLE_HEADER not in text:
        found.append(
            "the body has no golden table; paste the output of `python scripts/update_golden.py`"
        )
    rows = table_files(text)
    for path in golden:
        name = PurePosixPath(path).name
        if name not in rows:
            found.append(f"the golden table names no row for {name}")
    if not _CAUSE.search(text):
        found.append("the body has no 'Cause:' line saying why the golden values moved")
    return found


def _changed_from_git(base: str, head: str) -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", f"{base}...{head}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in out.stdout.splitlines() if line]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--event", type=Path, help="GitHub pull_request event JSON")
    parser.add_argument("--body-file", type=Path, help="pull request body (instead of --event)")
    parser.add_argument(
        "--changed", action="append", default=None, help="a changed path (repeatable)"
    )
    args = parser.parse_args(argv)

    if args.event is not None:
        event = json.loads(args.event.read_text(encoding="utf-8"))
        pr = event.get("pull_request")
        if not pr:
            print("not a pull_request event; nothing to check")
            return 0
        body = pr.get("body") or ""
        changed = (
            args.changed
            if args.changed is not None
            else _changed_from_git(pr["base"]["sha"], pr["head"]["sha"])
        )
    elif args.body_file is not None:
        body = args.body_file.read_text(encoding="utf-8")
        changed = args.changed or []
    else:
        parser.error("give --event or --body-file")
        return 2  # pragma: no cover

    golden = golden_files(changed)
    found = problems(body, changed)
    if not golden:
        print("no golden file changed")
        return 0
    if found:
        print(f"golden files changed: {', '.join(golden)}", file=sys.stderr)
        for line in found:
            print(f"error: {line}", file=sys.stderr)
        return 1
    print(f"golden table present for: {', '.join(golden)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
