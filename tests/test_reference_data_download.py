# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The weekly check of the ``download`` tier (#696, ADR-0073): it needs the network.

Every test here is ``@pytest.mark.reference_download``. The required matrix deselects the marker,
and the weekly ``reference-data`` workflow runs it with ``TETHER_REFERENCE_DOWNLOAD=1`` and a cache
keyed on the manifest checksums (``tests/test_marker_contract.py`` keeps the two in step). Without
that variable a set that is not cached skips, so a local run of this module opens no URL either.

Each download entry is fetched and verified against its SHA-256, and each committed extract that
copies one archive member verbatim is compared with that member, so a changed upstream record or
a drifted extract fails here and not in a later oracle.
"""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import pytest

from reference_data import fetch

pytestmark = pytest.mark.reference_download

REPO = Path(__file__).resolve().parents[1]
MANIFEST = fetch.load_manifest()
DOWNLOADS = sorted(d.id for d in MANIFEST.values() if d.tier == "download")
MEMBERS = sorted(d.id for d in MANIFEST.values() if d.extract_member)


@pytest.mark.parametrize("dataset_id", DOWNLOADS)
def test_each_download_entry_fetches_with_its_checksum(dataset_id: str) -> None:
    path = fetch.path_for(dataset_id)
    assert path.stat().st_size == MANIFEST[dataset_id].size


@pytest.mark.parametrize("dataset_id", MEMBERS)
def test_each_verbatim_extract_equals_its_archive_member(dataset_id: str) -> None:
    """Each committed copy is its archive member, byte for byte.

    The weekly job does not pull Git LFS, so an LFS extract is compared through its pointer: the
    member's SHA-256 must be the oid the committed pointer names (#755). The LFS object store
    keys the object by that oid, so this compares the same bytes without fetching them.
    """
    extract = MANIFEST[dataset_id]
    (source,) = extract.extract_of
    with zipfile.ZipFile(fetch.path_for(source)) as archive:
        member = archive.read(extract.extract_member)
    pointer_oid = fetch.lfs_oid(REPO / extract.file) if extract.tier == "lfs" else None
    if pointer_oid is not None:
        assert hashlib.sha256(member).hexdigest() == pointer_oid == extract.sha256
    else:
        assert member == fetch.path_for(dataset_id).read_bytes()
