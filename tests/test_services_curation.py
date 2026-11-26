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


def _two_model_store(tmp_path):
    """Model ``vbconhmm`` fits all four rows fresh; ``ebfret`` fits rows 2-3, row 3 stale."""
    from _analysis_store import build_store_with_model, fresh_input_hashes
    from tether.project.idealize import read_staleness_stamp, write_idealization_model

    project, keys = build_store_with_model(
        tmp_path, np.zeros((4, 10), dtype="int64"), np.array([0.5]), name="two.tether"
    )
    ids = list(read_staleness_stamp(project.path, "vbconhmm").molecule_ids)
    hashes = fresh_input_hashes(project.path)
    write_idealization_model(
        project.path,
        model_name="ebfret",
        model_type="ebfret",
        nstates=1,
        dtype="FRET",
        means=np.array([0.5]),
        variances=np.array([0.01]),
        tmatrix=None,
        norm_tmatrix=None,
        elbo=1.0,
        idealized=np.full((2, 10), 0.5),
        state_paths=np.zeros((2, 10), dtype="int64"),
        molecule_keys=keys[2:],
        molecule_ids=ids[2:],
        input_hashes=[hashes[2], f"STALE-{hashes[3]}"],
        intensity_quantity="corrected",
        selected_by="fixed",
        elbo_by_nstates=None,
        app_version="test",
        created_utc="2026-01-01T00:00:00Z",
        overwrite=True,
        frac=np.array([1.0]),
    )
    return project


def test_molecule_states_reads_the_traces_once_for_every_model(tmp_path, monkeypatch) -> None:
    """#1410: one read of ``/molecules`` + ``/traces`` serves the staleness of every model."""
    from tether.project import idealize

    project = _two_model_store(tmp_path)
    assert len(idealize.list_idealizations(project.path)) == 2
    reads: list[object] = []
    real = idealize.read_traces
    monkeypatch.setattr(idealize, "read_traces", lambda p: reads.append(p) or real(p))

    marks = [(s.idealized, s.stale) for s in curation.molecule_states(project.path)]

    assert len(reads) == 1
    assert marks == [(True, False), (True, False), (True, False), (True, True)]


def test_the_shared_read_gives_each_model_what_stale_row_ids_gives(tmp_path) -> None:
    from tether.project.idealize import (
        list_idealizations,
        read_staleness_stamp,
        stale_row_ids,
        stale_row_ids_each,
    )

    project = _two_model_store(tmp_path)
    stamps = [read_staleness_stamp(project.path, m) for m in list_idealizations(project.path)]
    each = stale_row_ids_each(project.path, stamps)
    assert each == [stale_row_ids(project.path, s) for s in stamps]
    assert sorted(len(ids) for ids in each) == [0, 1]
    assert stale_row_ids_each(project.path, []) == []
