# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Verify the one hash-locked setuptools compatibility wheel staged for packaging.

The committed requirements file is the source of truth.  This helper derives the only
permitted universal-wheel filename from its exact version and verifies the staged bytes
against its SHA-256 before either constructor workflow exports the artifact path.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK = REPO_ROOT / "packaging" / "setuptools-compatibility.txt"
_LOCK_RE = re.compile(
    r"setuptools==(?P<version>\d+\.\d+\.\d+)"
    r"\s+--hash=sha256:(?P<sha256>[0-9a-f]{64})"
)


class VerificationError(RuntimeError):
    """The compatibility lock or staged wheel does not satisfy the exact contract."""


@dataclass(frozen=True)
class CompatibilityLock:
    """Exact setuptools version, wheel filename, and SHA-256 from the committed lock."""

    version: str
    sha256: str

    @property
    def filename(self) -> str:
        """Return the one permitted universal wheel filename."""
        return f"setuptools-{self.version}-py3-none-any.whl"


def _logical_requirements(text: str) -> list[str]:
    """Collapse requirement continuations and discard comments/blank lines."""
    logical: list[str] = []
    current = ""
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        continued = stripped.endswith("\\")
        part = stripped[:-1].rstrip() if continued else stripped
        current = f"{current} {part}".strip()
        if not continued:
            logical.append(current)
            current = ""
    if current:
        raise VerificationError("unterminated line continuation in compatibility lock")
    return logical


def load_lock(path: Path = DEFAULT_LOCK) -> CompatibilityLock:
    """Parse exactly one pinned, SHA-256-hashed setuptools requirement from *path*."""
    try:
        requirements = _logical_requirements(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise VerificationError(f"cannot read compatibility lock: {path}") from exc
    if len(requirements) != 1:
        raise VerificationError(
            f"compatibility lock must contain exactly one requirement; got {len(requirements)}"
        )
    match = _LOCK_RE.fullmatch(requirements[0])
    if match is None:
        raise VerificationError(
            "compatibility lock must be exactly "
            "`setuptools==X.Y.Z --hash=sha256:<64 lowercase hex>`"
        )
    return CompatibilityLock(**match.groupdict())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_wheel(
    wheel_dir: Path,
    *,
    lock: CompatibilityLock | None = None,
) -> Path:
    """Return the staged wheel only after filename and SHA-256 both match *lock*."""
    resolved_lock = lock or load_lock()
    candidates = sorted(wheel_dir.glob("setuptools-*.whl"))
    expected = wheel_dir / resolved_lock.filename
    if candidates != [expected]:
        names = [path.name for path in candidates]
        raise VerificationError(f"expected exactly {resolved_lock.filename!r}; found {names}")
    actual = _sha256(expected)
    if actual != resolved_lock.sha256:
        raise VerificationError(
            f"SHA-256 mismatch for {expected.name}: expected {resolved_lock.sha256}, got {actual}"
        )
    return expected


def build_parser() -> argparse.ArgumentParser:
    """Build the verifier CLI parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel_dir", type=Path, help="directory containing the staged wheel")
    parser.add_argument(
        "--requirements",
        type=Path,
        default=DEFAULT_LOCK,
        help="hash-locked requirements file (default: repository compatibility lock)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Verify the staged wheel and print its path for workflow consumption."""
    args = build_parser().parse_args(argv)
    try:
        wheel = verify_wheel(args.wheel_dir, lock=load_lock(args.requirements))
    except VerificationError as exc:
        print(f"verify_setuptools_wheel: {exc}", file=sys.stderr)
        return 1
    print(wheel.as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
