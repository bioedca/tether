# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Hold the docstring allowlist to its rule: it can only become shorter (issue #611, ADR-0093).

Ruff checks the docstrings of ``src/tether`` with its ``D`` rules and the NumPy convention. The
violations that existed when the check was turned on are allowed by file and by rule, as
entries of ``[tool.ruff.lint.per-file-ignores]`` in ``pyproject.toml`` whose path is under
``src/tether/``. This script and ``tests/test_docstring_standard.py`` keep that allowlist honest:

* ``stale_entries`` names each allowed ``(file, rule)`` that ruff no longer finds. The CI
  ``lint`` job runs this script, which fails (exit 1) on a stale entry, so the entry is removed
  in the pull request that fixes the docstring.
* ``added_entries`` names each allowed ``(file, rule)`` that is not on ``main``. The test fails on
  one, so a pull request can never allow a new violation.

Usage::

    python scripts/check_docstring_allowlist.py

It needs ``ruff`` on ``PATH``, the version that ``.pre-commit-config.yaml`` pins.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
PYPROJECT = _REPO / "pyproject.toml"
#: The allowlist is every per-file-ignores entry for a path under this prefix.
PREFIX = "src/tether/"

Entry = tuple[str, str]  # (file path from the repository root, rule code)


def allowlist(pyproject_text: str) -> set[Entry]:
    """Return the allowed ``(file, rule)`` pairs that a ``pyproject.toml`` text declares."""
    lint = tomllib.loads(pyproject_text).get("tool", {}).get("ruff", {}).get("lint", {})
    ignores = lint.get("per-file-ignores", {})
    return {
        (path, code)
        for path, codes in ignores.items()
        if path.startswith(PREFIX)
        for code in codes
        if code.startswith("D")
    }


def added_entries(base_text: str, head_text: str) -> list[Entry]:
    """The allowed pairs in *head_text* that *base_text* does not allow, sorted."""
    return sorted(allowlist(head_text) - allowlist(base_text))


def violations(root: Path = _REPO) -> set[Entry]:
    """Run ruff on ``src/tether`` with no per-file ignores and return its ``D`` findings."""
    run = subprocess.run(
        [
            "ruff",
            "check",
            "--select",
            "D",
            "--config",
            "lint.per-file-ignores = {}",
            "--output-format",
            "json",
            "--exit-zero",
            "--no-cache",
            PREFIX,
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    found = set()
    for item in json.loads(run.stdout):
        path = Path(item["filename"]).resolve().relative_to(root.resolve()).as_posix()
        found.add((path, item["code"]))
    return found


def stale_entries(allowed: set[Entry], found: set[Entry]) -> list[Entry]:
    """The allowed pairs that no violation needs any more, sorted."""
    return sorted(allowed - found)


def main() -> int:
    allowed = allowlist(PYPROJECT.read_text(encoding="utf-8"))
    stale = stale_entries(allowed, violations())
    for path, code in stale:
        print(f"{path}: {code} is allowed but no longer occurs; remove it from the allowlist")
    print(f"{len(allowed)} allowed pairs, {len(stale)} stale")
    return 1 if stale else 0


if __name__ == "__main__":
    sys.exit(main())
