# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The private data of oracle (a): video 010 of ADR-0022, under its neutral id (#699).

The movie, its ``.tmap``, its ``.tdat`` and its two Deep-LASI exports are private laboratory
data. They are on no CI runner, so every test that reads them is a ``workstation`` test
(ADR-0071) and finds them through the ``private_data`` fixture of ``tests/conftest.py``. The
names of the files stay in the name table on the workstation; a test and its assertion
messages name only the neutral id.

A file is used only when its SHA-256 is the one below, so a committed result of these tests
always names the bytes it was measured on. The first 16 hex digits of the movie, ``.tmap``,
``.tdat`` and ``.mat`` digests are the ones ``tests/fixtures/PROVENANCE.md`` records for the
sources of the committed fixtures and of the ADR-0022 measurement, and
``tests/test_oracle_a_record.py`` keeps the two in step.
"""

from __future__ import annotations

import functools
import hashlib
from pathlib import Path

#: The neutral id of video 010 in the workstation's name table.
ORACLE_A = "lab-01"

#: SHA-256 and size of each file of video 010, by role (measured on the workstation, 2026-11-13).
FILES: dict[str, tuple[str, int]] = {
    "movie": (
        "c4293f00ed2ac72d0aa6c55f0c3fbb4910e928eed8f2c4c73a3087cf109e8448",
        891_955_083,
    ),
    "tmap": (
        "7db0cf80d161847e620d820060dbbf856a3dca3dc3f1e5e8b8c44f9aa124f13f",
        3_872_385,
    ),
    "tdat": (
        "b6a911d48bc27cd1b5f43a4a4713c659c73b834cdbbc769e38176da24bfab47a",
        37_039_831,
    ),
    "mat": (
        "af1b5be33aa63f87378df19a4a288cd23001409b5c40473201d1e7d19fb11886",
        9_053_155,
    ),
    "txt": (
        "0892ae965a947003b5a131a2172fbfeef0967f5a5aa579cf591e45cae59b43af",
        7_739_364,
    ),
}


@functools.cache
def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 22), b""):
            digest.update(block)
    return digest.hexdigest()


def oracle_a_file(private_data, role: str) -> Path:
    """One file of video 010 (``movie``, ``tmap``, ``tdat``, ``mat`` or ``txt``), verified."""
    path = private_data.path(ORACLE_A, role)
    sha256, size = FILES[role]
    assert path.stat().st_size == size and _sha256(path) == sha256, (
        f"{ORACLE_A}/{role}: the file is not the one oracle (a) was recorded on "
        f"(SHA-256 {sha256[:16]}…, {size} B); see tests/_oracle_a.py"
    )
    return path


def oracle_a_folder(private_data) -> Path:
    """The folder of the whole bundle of video 010: every role file must be in it."""
    folders = {oracle_a_file(private_data, role).parent for role in FILES}
    assert len(folders) == 1, f"{ORACLE_A}: the name table puts its files in more than one folder"
    return folders.pop()
