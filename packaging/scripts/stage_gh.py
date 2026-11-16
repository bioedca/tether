#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Stage the pinned, hash-checked ``gh`` update verifier for the installer (issue #332).

The one reader of ``packaging/gh-verifier.toml``, the single source of truth for the bundled
``gh`` version and the SHA-256 of each upstream archive (ADR-0060 picks ``gh attestation
verify --bundle`` as the verifier and ``extra_files`` as its delivery). Both packaging workflows
call ``stage`` before ``constructor`` runs, and ``install_smoke.sh`` calls ``print-version`` to
check what was installed, so neither restates the pin.

``stage`` fails closed, in this order:

1. the pinned version must clear the floor: ``>= 2.67.0`` (CVE-2025-25204, ``gh attestation
   verify`` failed open before it) and ``> 2.92.0`` (CVE-2026-48501);
2. the archive's SHA-256 must equal the pinned digest for this platform — checked on the bytes
   before anything is extracted;
3. the archive must hold exactly one ``bin/gh`` (``bin/gh.exe`` on Windows) and one ``LICENSE``;
4. the extracted binary must run and report the pinned version;
5. ``gh licenses`` must print the third-party license texts compiled into the binary, which are
   staged beside it, so the install carries the notices of ``gh``'s own dependencies.

It writes three files into ``--dest``, the names ``construct.yaml``'s ``extra_files`` reads:
``gh`` (``gh.exe``), ``LICENSE`` and ``THIRD-PARTY-LICENSES.txt``.

Standard library only: it runs in the build env before anything else is installed.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import urllib.request
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

PIN_FILE = Path(__file__).resolve().parents[1] / "gh-verifier.toml"

#: CVE-2025-25204: ``gh attestation verify`` exited 0 on a predicate-type mismatch in
#: 2.49.0 through 2.66.x. The hard floor (ADR-0060, "What Tether must still do itself" 3).
FAIL_OPEN_FIXED = (2, 67, 0)
#: CVE-2026-48501: gh <= 2.92.0 sent auth tokens to TUF mirrors. Tether calls gh without a
#: token, so it is not exposed, but issue #332 asks the pin to clear it anyway.
TOKEN_LEAK_LAST_AFFECTED = (2, 92, 0)

RELEASE_URL = "https://github.com/cli/cli/releases/download/v{version}/gh_{version}_{asset}"

PLATFORMS = ("linux-64", "osx-64", "osx-arm64", "win-64")
_VERSION_RE = re.compile(r"\d+\.\d+\.\d+")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class StageError(RuntimeError):
    """The pin, the download or the staged binary failed a check. Nothing is staged."""


@dataclass(frozen=True)
class Pin:
    """One platform's pinned archive."""

    version: str
    platform: str
    asset: str
    sha256: str

    @property
    def archive_name(self) -> str:
        return f"gh_{self.version}_{self.asset}"

    @property
    def url(self) -> str:
        return RELEASE_URL.format(version=self.version, asset=self.asset)

    @property
    def binary_name(self) -> str:
        return "gh.exe" if self.platform.startswith("win-") else "gh"


def version_tuple(version: str) -> tuple[int, int, int]:
    """``"2.104.0"`` -> ``(2, 104, 0)``; anything else is refused, never guessed."""
    if not _VERSION_RE.fullmatch(version):
        raise StageError(f"gh version {version!r} is not MAJOR.MINOR.PATCH")
    major, minor, patch = (int(p) for p in version.split("."))
    return major, minor, patch


def check_floor(version: str) -> None:
    """Refuse a version that either advisory affects."""
    v = version_tuple(version)
    if v < FAIL_OPEN_FIXED:
        raise StageError(
            f"gh {version} is below 2.67.0: `gh attestation verify` fails open before it "
            "(CVE-2025-25204)"
        )
    if v <= TOKEN_LEAK_LAST_AFFECTED:
        raise StageError(f"gh {version} is at or below 2.92.0 (CVE-2026-48501)")


def load_pins(path: Path = PIN_FILE) -> dict[str, Pin]:
    """Every platform's pin, after the floor and the shape of each entry are checked."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    version = data.get("version")
    if not isinstance(version, str):
        raise StageError(f"{path.name}: `version` must be a string")
    check_floor(version)
    platforms = data.get("platforms")
    if not isinstance(platforms, dict) or sorted(platforms) != sorted(PLATFORMS):
        raise StageError(f"{path.name}: [platforms] must name exactly {', '.join(PLATFORMS)}")
    pins: dict[str, Pin] = {}
    for platform in PLATFORMS:
        entry = platforms[platform]
        asset, digest = entry.get("asset"), entry.get("sha256")
        if not isinstance(asset, str) or not asset:
            raise StageError(f"{path.name}: {platform} has no `asset`")
        if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
            raise StageError(f"{path.name}: {platform} `sha256` is not 64 lower-case hex digits")
        pins[platform] = Pin(version, platform, asset, digest)
    return pins


def verify_digest(data: bytes, pin: Pin) -> None:
    """Refuse the archive unless its SHA-256 is the pinned one."""
    actual = hashlib.sha256(data).hexdigest()
    if not hmac.compare_digest(actual, pin.sha256):
        raise StageError(
            f"{pin.archive_name}: SHA-256 {actual} does not match the pinned {pin.sha256}"
        )


def _members(data: bytes, asset: str) -> dict[str, bytes]:
    """The archive's regular files, by their path inside it."""
    files: dict[str, bytes] = {}
    if asset.endswith(".tar.gz"):
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            for member in tar.getmembers():
                if member.isfile():
                    handle = tar.extractfile(member)
                    if handle is not None:
                        files[member.name] = handle.read()
    elif asset.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for info in archive.infolist():
                if not info.is_dir():
                    files[info.filename] = archive.read(info)
    else:
        raise StageError(f"unknown archive type: {asset}")
    return files


def _one(files: dict[str, bytes], pattern: re.Pattern[str], what: str) -> bytes:
    found = [name for name in files if pattern.fullmatch(name.replace("\\", "/"))]
    if len(found) != 1:
        raise StageError(f"expected exactly one {what} in the archive, found {sorted(found)}")
    return files[found[0]]


def extract(data: bytes, pin: Pin, dest: Path) -> Path:
    """Write the binary and its LICENSE into ``dest``; return the binary's path.

    Only the two named members are written, by a name this script chooses, so a path in the
    archive can never place a file outside ``dest``.
    """
    files = _members(data, pin.asset)
    name = re.escape(pin.binary_name)
    binary = _one(files, re.compile(rf"(?:[^/]+/)?bin/{name}"), f"bin/{pin.binary_name}")
    license_text = _one(files, re.compile(r"(?:[^/]+/)?LICENSE"), "LICENSE")
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / pin.binary_name
    target.write_bytes(binary)
    target.chmod(0o755)
    (dest / "LICENSE").write_bytes(license_text)
    return target


Runner = Callable[[Sequence[str]], str]


def _run(argv: Sequence[str]) -> str:
    """Run the staged ``gh`` with no config, no prompt, no pager and no update check."""
    with tempfile.TemporaryDirectory() as config:
        env = {
            **os.environ,
            "GH_CONFIG_DIR": config,
            "GH_NO_UPDATE_NOTIFIER": "1",
            "GH_PROMPT_DISABLED": "1",
            "GH_PAGER": "",
            "PAGER": "",
            "NO_COLOR": "1",
        }
        env.pop("GH_TOKEN", None)
        env.pop("GITHUB_TOKEN", None)
        done = subprocess.run(
            list(argv), env=env, capture_output=True, text=True, encoding="utf-8", check=False
        )
    if done.returncode != 0:
        raise StageError(f"{' '.join(argv)} exited {done.returncode}: {done.stderr.strip()}")
    return done.stdout


def check_binary(binary: Path, pin: Pin, dest: Path, run: Runner = _run) -> None:
    """The staged binary must report the pinned version; stage its third-party notices."""
    first = run([str(binary), "--version"]).splitlines()[:1]
    if not first or not first[0].startswith(f"gh version {pin.version} "):
        raise StageError(f"{binary} reports {first!r}, not gh version {pin.version}")
    notices = run([str(binary), "licenses"])
    if len(notices.strip()) < 1000:
        raise StageError(f"`gh licenses` printed {len(notices)} characters, not the notices")
    (dest / "THIRD-PARTY-LICENSES.txt").write_text(notices, encoding="utf-8", newline="\n")


def _download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310 - fixed https URL
        return response.read()


def stage(
    platform: str,
    dest: Path,
    *,
    archive: Path | None = None,
    pins: dict[str, Pin] | None = None,
    fetch: Callable[[str], bytes] = _download,
    run: Runner = _run,
) -> Path:
    """Download (or read), verify and stage this platform's ``gh``; return the binary path."""
    pins = load_pins() if pins is None else pins
    if platform not in pins:
        raise StageError(f"no pin for platform {platform!r}; expected one of {sorted(pins)}")
    pin = pins[platform]
    data = archive.read_bytes() if archive is not None else fetch(pin.url)
    verify_digest(data, pin)
    if dest.exists():
        shutil.rmtree(dest)
    try:
        binary = extract(data, pin, dest)
        check_binary(binary, pin, dest, run)
    except BaseException:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    return binary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    stage_p = sub.add_parser("stage", help="download, verify and stage one platform's gh")
    stage_p.add_argument("--platform", required=True, choices=PLATFORMS)
    stage_p.add_argument("--dest", required=True, type=Path)
    stage_p.add_argument(
        "--archive", type=Path, help="a local copy of the upstream archive (skips the download)"
    )
    sub.add_parser("print-version", help="print the pinned gh version")
    args = parser.parse_args(argv)
    try:
        if args.command == "print-version":
            print(load_pins()["linux-64"].version)
            return 0
        binary = stage(args.platform, args.dest, archive=args.archive)
    except StageError as error:
        print(f"stage_gh: {error}", file=sys.stderr)
        return 1
    print(f"staged {binary} ({sorted(p.name for p in args.dest.iterdir())})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
