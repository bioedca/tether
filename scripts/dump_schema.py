# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Dump / verify the frozen ``.tether`` schema manifest (PRD §12.6, M0 S6).

This is the engine behind the ``schema-guard`` CI gate. It runs the very builder
that writes a real ``.tether`` (:func:`tether.io.schema.build_manifest`) and:

* ``--write`` (default): (re)generate the golden manifest ``schema/schema_frozen.json``.
  Run this in a PR that *deliberately* changes the schema — together with an ADR and
  a ``schema_version`` bump (PRD §12.6).
* ``--check``: compare the code's declared schema to the committed golden and exit
  non-zero on any freeze violation (a removed/renamed/retyped frozen field, a
  decremented version, a stale golden). Additions pass.

Run locally (deps are not in the dev shell)::

    uv run --no-project --with "h5py==3.16.0" --with numpy python scripts/dump_schema.py --check
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
# Make ``tether`` importable when run from a checkout without an install
# (CI installs the package, so this shim is a harmless no-op there).
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from tether.io import schema  # noqa: E402  (after the src-path shim above)

GOLDEN_PATH = _REPO_ROOT / "schema" / "schema_frozen.json"


def _serialize(manifest: dict) -> str:
    """Stable, diff-friendly JSON (sorted keys, trailing newline)."""
    return json.dumps(manifest, indent=2, sort_keys=True) + "\n"


def _write_golden() -> int:
    manifest = schema.build_manifest()
    GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN_PATH.write_text(_serialize(manifest), encoding="utf-8")
    print(f"Wrote golden schema manifest -> {GOLDEN_PATH.relative_to(_REPO_ROOT)}")
    print(f"schema_version = {manifest['schema_version']}")
    return 0


def _check_golden() -> int:
    if not GOLDEN_PATH.is_file():
        print(
            f"::error::golden manifest missing: {GOLDEN_PATH.relative_to(_REPO_ROOT)} "
            "(run scripts/dump_schema.py --write)",
            file=sys.stderr,
        )
        return 1
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    current = schema.build_manifest()
    violations = schema.diff_manifest(golden, current)
    if violations:
        print("::error::HDF5 schema freeze violated (PRD section 5 / M0):", file=sys.stderr)
        for v in violations:
            print(f"::error::  - {v}", file=sys.stderr)
        print(
            "\nA deliberate structural change must bump schema_version, carry an ADR, "
            "and regenerate the golden (scripts/dump_schema.py --write) in the same PR.",
            file=sys.stderr,
        )
        return 1
    print(
        "schema-guard: OK - declared schema matches the golden "
        f"(version {current['schema_version']})."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--write",
        action="store_true",
        help="(re)generate the golden manifest (deliberate schema change only).",
    )
    group.add_argument(
        "--check",
        action="store_true",
        help="verify the declared schema against the committed golden; exit 1 on drift.",
    )
    args = parser.parse_args(argv)
    if args.check:
        return _check_golden()
    return _write_golden()


if __name__ == "__main__":
    raise SystemExit(main())
