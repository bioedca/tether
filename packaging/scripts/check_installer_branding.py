#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check that a built installer shows the Tether images and texts (issue #672).

``construct.yaml`` names the images in ``packaging/images/`` and the three macOS texts.
Without those keys constructor uses its own defaults: generated blue images and a "T" icon
on Windows, and Anaconda's logo, read-me and conclusion on macOS. This script reads what the
built installer holds and fails when that is not the Tether set.

``install_smoke.sh`` runs it on the runner that built the installer, with the build env's
Python (constructor brings Pillow on macOS and Windows)::

    python packaging/scripts/check_installer_branding.py pkg DIR      # DIR: pkgutil --expand
    python packaging/scripts/check_installer_branding.py exe EXE DIR  # DIR: 7z x EXE *.bmp

It prints what it found before it decides, so a failed run also records what the installer
showed.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

PACKAGING = Path(__file__).resolve().parents[1]
CONSTRUCT = PACKAGING / "construct.yaml"
IMAGES = PACKAGING / "images"

#: Whole words only: the GPL text that the installer shows says "secondarily".
FORBIDDEN = re.compile(r"\b(?:ana)?conda\b", re.IGNORECASE)
TEXT_KEYS = ("welcome", "readme", "conclusion")
#: The names NSIS Modern UI 2 gives the welcome-page and header bitmaps in $PLUGINSDIR.
NSIS_BITMAPS = {
    "modern-wizard.bmp": IMAGES / "windows-welcome-164x314.png",
    "modern-header.bmp": IMAGES / "windows-header-150x57.png",
}
#: The icon resource is in the NSIS stub, before the compressed payload.
EXE_HEAD_BYTES = 16 * 1024 * 1024


def expected_texts(construct: Path = CONSTRUCT) -> dict[str, str]:
    """The ``<key>_text`` values that ``construct.yaml`` sets on one ``# [osx]`` line each."""
    line = re.compile(r'^(welcome|readme|conclusion)_text:\s*("(?:[^"\\]|\\.)*")\s*#\s*\[osx\]\s*$')
    found = {}
    for text in construct.read_text(encoding="utf-8").splitlines():
        if match := line.match(text):
            found[match.group(1)] = json.loads(match.group(2))
    return found


def same_pixels(found: Path, expected: Path, mode: str) -> bool:
    from PIL import Image

    with Image.open(found) as a, Image.open(expected) as b:
        return a.size == b.size and a.convert(mode).tobytes() == b.convert(mode).tobytes()


def icon_frames(icon: Path) -> dict[int, bytes]:
    """The frames of the ``icon.ico`` that constructor writes from *icon*, by width.

    constructor opens ``icon_image``, resizes it to 256 x 256 and saves it as ICO. The file
    is already 256 x 256, so with the same Pillow this rebuilds the same bytes.
    """
    from PIL import Image

    buffer = io.BytesIO()
    with Image.open(icon) as image:
        image.resize((256, 256)).save(buffer, format="ICO")
    data = buffer.getvalue()
    frames = {}
    for i in range(int.from_bytes(data[4:6], "little")):
        entry = data[6 + 16 * i : 22 + 16 * i]
        size, offset = int.from_bytes(entry[8:12], "little"), int.from_bytes(entry[12:16], "little")
        frames[entry[0] or 256] = data[offset : offset + size]
    return frames


def _resource(expanded: Path, name: str) -> Path | None:
    hits = sorted((expanded / "Resources").rglob(Path(name).name)) or sorted(
        expanded.rglob(Path(name).name)
    )
    return hits[0] if hits else None


def check_pkg(expanded: Path, texts: dict[str, str], background: Path) -> list[str]:
    """Problems with the macOS package expanded at *expanded*; prints what it holds."""
    distribution = expanded / "Distribution"
    if not distribution.is_file():
        return [f"no Distribution file in {expanded}"]
    root = ET.parse(distribution).getroot()
    print("Resources:", sorted(p.name for p in (expanded / "Resources").rglob("*")))
    shown = [root.findtext("title") or ""]
    shown += [c.get(a, "") for c in root.iter("choice") for a in ("title", "description")]
    print("title:", repr(shown[0]))
    problems = []
    for key in ("background", "background-darkAqua"):
        element = root.find(key)
        name = "" if element is None else element.get("file", "")
        path = _resource(expanded, name) if name else None
        print(f"{key}: {name or '(none)'}")
        if path is None or not same_pixels(path, background, "RGBA"):
            problems.append(f"<{key}> is not {background.name}: {name or 'absent'}")
    for key in (*TEXT_KEYS, "license"):
        element = root.find(key)
        name = "" if element is None else element.get("file", "")
        path = _resource(expanded, name) if name else None
        text = path.read_text(encoding="utf-8", errors="replace") if path else ""
        print(f"{key}: {name or '(none)'}: {text[:300]!r}")
        shown.append(text)
        if key == "license":
            continue
        if key not in texts:
            problems.append(f"construct.yaml sets no {key}_text on an [osx] line")
        elif text.strip() != texts[key].strip():
            problems.append(f"<{key}> does not show the {key}_text: {name or 'absent'}")
    for text in shown:
        for match in FORBIDDEN.finditer(text):
            context = text[max(0, match.start() - 40) : match.end() + 40]
            problems.append(f"the installer shows {match.group(0)!r}: ...{context!r}...")
    return problems


def check_exe(exe: Path, extracted: Path, icon: Path) -> list[str]:
    """Problems with the Windows installer *exe*, whose bitmaps 7-Zip unpacked to *extracted*."""
    problems = []
    for name, expected in NSIS_BITMAPS.items():
        hits = sorted(extracted.rglob(name))
        print(f"{name}: {[str(p.relative_to(extracted)) for p in hits] or '(none)'}")
        if not hits:
            problems.append(f"7-Zip unpacked no {name} from {exe.name}")
        problems += [
            f"{hit.relative_to(extracted)} is not {expected.name}"
            for hit in hits
            if not same_pixels(hit, expected, "RGB")
        ]
    with exe.open("rb") as handle:
        head = handle.read(EXE_HEAD_BYTES)
    frames = icon_frames(icon)
    found = sorted(width for width, data in frames.items() if data in head)
    print(f"icon frames of {icon.name} in {exe.name}: {found} of {sorted(frames)}")
    if 256 not in found:
        problems.append(f"{exe.name} does not hold the 256 px frame of {icon.name}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    kinds = parser.add_subparsers(dest="kind", required=True)
    kinds.add_parser("pkg").add_argument("expanded", type=Path)
    exe = kinds.add_parser("exe")
    exe.add_argument("installer", type=Path)
    exe.add_argument("extracted", type=Path)
    args = parser.parse_args(argv)
    if args.kind == "pkg":
        problems = check_pkg(args.expanded, expected_texts(), IMAGES / "macos-welcome-1227x600.png")
    else:
        problems = check_exe(args.installer, args.extracted, IMAGES / "windows-icon-256.png")
    for problem in problems:
        print(f"::error::installer branding: {problem}")
    if not problems:
        print("installer branding OK")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
