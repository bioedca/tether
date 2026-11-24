# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Workstation: the 5 uM WT tRNA column of Fig. 5b is four per-run files joined (#1128, #1344).

The Source Data archive has no trace matrix for this column (ADR-0097): its 5 uM file is a copy of
the 13 uM one. The per-run trace files of the four 5 uM WT tRNA movies are not published, so they
reach this test only through neutral ids of the workstation runner (ADR-0071): each id whose name
starts with ``tbox-`` and that has a file with the role ``wt5_run``, joined in the order of the ids.
A file with the role ``wt5_other`` is searched but never joined.

All 163 blocks of the column must be the joined runs (66, 8, 64 and 25 molecules), first 400
frames each, to 1e-6, and no block may be left unmatched. A block that fails the join is still
searched in every molecule of every run and every ``wt5_other`` file at every start frame (to 1e-4),
and the number that agree with none is recorded before the test fails. The result file gets numbers
only, under the neutral ids. No file name, path or trace leaves.
"""

from __future__ import annotations

import pytest

from _campos_chavez_2024 import (
    ARCHIVE_ID,
    WT_5UM_COLUMN,
    column,
    locate_blocks,
    read_book,
    read_run,
)
from reference_data import fetch

pytestmark = pytest.mark.workstation

PREFIX, ROLE, OTHER = "tbox-", "wt5_run", "wt5_other"
RUN_MOLECULES = [66, 8, 64, 25]  # the four runs, in the order of their ids
JOINED_MOLECULES = sum(RUN_MOLECULES)  # 163, the whole column


def test_the_5um_wt_column_is_the_four_joined_runs(private_data) -> None:
    def ids_with(role: str) -> list[str]:
        found = private_data.ids
        return [i for i in found if i.startswith(PREFIX) and role in private_data.roles(i)]

    ids, others = ids_with(ROLE), ids_with(OTHER)
    if not ids:
        pytest.skip(f"no id with the role {ROLE!r} in the mapping")
    values = column(
        read_book(fetch.path_for(ARCHIVE_ID))["Fig5b"], "FRET Efficiency", WT_5UM_COLUMN
    )
    got = locate_blocks(
        values,
        [read_run(private_data.path(i, ROLE)) for i in ids],
        [read_run(private_data.path(i, OTHER)) for i in others],
    )
    for neutral_id, molecules in zip(ids, got["run_molecules"], strict=True):
        private_data.record(neutral_id, molecules=molecules)
    private_data.record(
        ids[0],
        column_molecules=got["column_molecules"],
        leading_equal=got["leading_equal"],
        max_abs_deviation=got["max_abs_deviation"],
        unmatched_molecules=len(got["unmatched"]),
        first_unmatched_block=got["unmatched"][0] + 1 if got["unmatched"] else 0,
    )
    print({k: v for k, v in got.items() if k != "unmatched"}, len(got["unmatched"]))
    assert got["run_molecules"] == RUN_MOLECULES, got["run_molecules"]
    assert got["column_molecules"] == JOINED_MOLECULES, got["column_molecules"]
    assert got["leading_equal"] == JOINED_MOLECULES, got
    assert not got["unmatched"], got["unmatched"]
