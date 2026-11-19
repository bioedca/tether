# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The read-only helpers behind the curation keys (#359): no Qt, no write."""

from __future__ import annotations

import hashlib

import numpy as np
import pytest

pytest.importorskip("h5py")

from tether.services import curation  # noqa: E402


def _store(tmp_path, **kwargs):
    from _analysis_store import build_store_with_channels

    rng = np.random.default_rng(5)
    donor = rng.normal(600.0, 15.0, size=(2, 40))
    acceptor = rng.normal(400.0, 15.0, size=(2, 40))
    donor[1, 25:] = rng.normal(0.0, 15.0, size=15)
    acceptor[1, 25:] = rng.normal(0.0, 15.0, size=15)
    return build_store_with_channels(tmp_path, donor, acceptor, **kwargs)


def test_unset_ranges_resolve_like_the_windowed_readers() -> None:
    assert curation.resolved_frame_range((0, 0), 40) == (0, 40)
    assert curation.resolved_frame_range((3, 30), 40) == (3, 30)
    assert curation.resolved_window((0, 0), (3, 30)) == (3, 30)
    assert curation.resolved_window((5, 4), (3, 30)) == (3, 30)
    assert curation.resolved_window((5, 9), (3, 30)) == (5, 9)


def test_stored_molecule_reads_the_row_and_writes_nothing(tmp_path) -> None:
    project, keys = _store(tmp_path, windows=[(2, 30), (0, 0)])
    before = hashlib.sha256(project.path.read_bytes()).hexdigest()

    first = curation.stored_molecule(project.path, keys[0])
    second = curation.stored_molecule(project.path, keys[1])

    assert (first.row, first.frame_range, first.window) == (0, (0, 40), (2, 30))
    assert (second.row, second.window) == (1, (0, 40))  # unset window -> frame_range
    assert first.condition_id
    assert hashlib.sha256(project.path.read_bytes()).hexdigest() == before
    with pytest.raises(KeyError, match="no molecule"):
        curation.stored_molecule(project.path, "not-a-key")


def test_summed_bleach_frame_finds_the_step_or_the_trace_end(tmp_path) -> None:
    project, keys = _store(tmp_path)
    steady = curation.stored_molecule(project.path, keys[0])
    bleached = curation.stored_molecule(project.path, keys[1])

    assert curation.summed_bleach_frame(project.path, steady) == 40
    assert abs(curation.summed_bleach_frame(project.path, bleached) - 25) <= 2


def test_stored_molecule_reads_the_selected_row_of_a_shared_key(tmp_path) -> None:
    import h5py

    project, keys = _store(tmp_path, windows=[(2, 30), (5, 35)])
    with h5py.File(project.path, "r+") as f:
        table = f["molecules"]["table"][:]
        table["molecule_key"][1] = table["molecule_key"][0]
        f["molecules"]["table"][:] = table

    assert [m.row for m in curation.stored_molecules(project.path, keys[0])] == [0, 1]
    assert curation.stored_molecule(project.path, keys[0], row=1).window == (5, 35)
    assert curation.stored_molecule(project.path, keys[0]).window == (2, 30)
    assert curation.stored_molecule(project.path, keys[0], row=7).row == 0  # not its row


def test_stored_rows_reads_many_keys_in_row_order_and_skips_unknown_ones(tmp_path) -> None:
    project, keys = _store(tmp_path, windows=[(2, 30), (4, 20)])

    rows = curation.stored_rows(project.path, [keys[1], "not-a-key", keys[0]])

    assert [(m.row, m.molecule_key, m.window) for m in rows] == [
        (0, keys[0], (2, 30)),
        (1, keys[1], (4, 20)),
    ]
    assert curation.stored_rows(project.path, ["not-a-key"]) == ()
