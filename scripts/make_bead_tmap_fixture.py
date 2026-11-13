# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Derive the LFS bead-map fixture ``tests/fixtures/large/bead_prealign.tmap`` (#733).

The source is the Deep-LASI ``.tmap`` of the bead-calibration acquisition whose decoded
ground truth is ``tests/fixtures/bead_prealign_oracle.npz``. The source holds the absolute
acquisition directory (a drive path) in six strings. This script replaces each such directory
with a placeholder of the same byte length, so the MAT-file structure does not change, and
recompresses the element that holds it. All other bytes stay as they are. It then checks that
``read_tmap`` decodes the copy to exactly the same channels as the source.

The output SHA-256 can differ with the zlib version; the decoded content cannot.

    python scripts/make_bead_tmap_fixture.py
"""

from __future__ import annotations

import hashlib
import re
import struct
import sys
import zlib
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tether.imaging.register import read_tmap  # noqa: E402

SOURCE_SHA256 = "a993036a53d1d4920fe2a1f8409a33889d0f72ca417162efc910845bc9c3f462"
NAME = "DeepLASI_MAP_2bla_UCKOPSB_35pM_tRNA_600nM_20250721_2025-07-21_15-36.tmap"
OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "large" / "bead_prealign.tmap"
_DRIVE_DIR = re.compile(rb"[A-Za-z]:\\(?:[A-Za-z0-9 _.()\-]+\\)+")
_MI_COMPRESSED = 15


def _placeholder(match: re.Match[bytes]) -> bytes:
    return b"<redacted-directory>".ljust(len(match.group()), b"_")[: len(match.group())]


def _source() -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "example-data" / "2bla-uckopsb-bead-calibration-20250721" / NAME
        if candidate.is_file():
            return candidate
    raise SystemExit(f"could not locate {NAME} under an 'example-data' sibling directory")


def main() -> None:
    src = _source()
    data = src.read_bytes()
    if hashlib.sha256(data).hexdigest() != SOURCE_SHA256:
        raise SystemExit(f"{src.name}: SHA-256 is not {SOURCE_SHA256}")
    out, pos, replaced = bytearray(data[:128]), 128, 0  # the 128-byte MAT header is kept
    while pos < len(data):
        kind, size = struct.unpack("<II", data[pos : pos + 8])
        body = data[pos + 8 : pos + 8 + size]
        if kind != _MI_COMPRESSED:
            raise SystemExit(f"unexpected top-level element type {kind} at byte {pos}")
        raw, n = _DRIVE_DIR.subn(_placeholder, zlib.decompress(body))
        if n:
            body, replaced = zlib.compress(raw, 9), replaced + n
        out += struct.pack("<II", kind, len(body)) + body
        pos += 8 + size
    if replaced == 0:
        raise SystemExit("no drive path found; the source is not the expected file")
    OUT.write_bytes(bytes(out))

    before, after = read_tmap(src), read_tmap(OUT)
    assert sorted(before) == sorted(after)  # noqa: S101
    for cid, ch in before.items():
        for field in ("crop", "map_particles", "rotation", "flip"):
            assert np.array_equal(getattr(ch, field), getattr(after[cid], field))  # noqa: S101
        for name in ("ref_to_channel", "channel_to_ref"):
            for field in ("a", "b", "norm_xy", "norm_uv"):
                got = getattr(getattr(after[cid], name), field)
                assert np.array_equal(getattr(getattr(ch, name), field), got)  # noqa: S101
    print(f"replaced {replaced} directory string(s); decode identical for channels {sorted(after)}")
    print(f"wrote {OUT} ({OUT.stat().st_size} B, sha256 {hashlib.sha256(out).hexdigest()})")


if __name__ == "__main__":
    main()
