# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Workstation: the 5 uM WT tRNA column of Fig. 5b beside the authors' per-run files (#1128).

The Source Data archive has no trace matrix for this column (ADR-0097): its 5 uM file is a copy of
the 13 uM one. The per-run trace files of the three 5 uM WT tRNA movies are not published, so they
reach this test only through neutral ids of the workstation runner (ADR-0071): each id whose name
starts with ``tbox-`` and that has a file with the role ``wt5_run``, joined in the order of the ids.

Blocks 1 to 138 of the column must be the joined runs, first 400 frames each, to 1e-6. The blocks
after those are searched in every molecule of every run at every start frame (to 1e-4); the number
that agree with none (25 on 2026-11-16) is recorded, not failed. The result file gets numbers
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

PREFIX, ROLE = "tbox-", "wt5_run"
JOINED_MOLECULES = 138  # the three runs: 66, 8 and 64 molecules


def test_the_5um_wt_column_is_the_joined_runs_and_the_rest_is_recorded(private_data) -> None:
    ids = [i for i in private_data.ids if i.startswith(PREFIX) and ROLE in private_data.roles(i)]
    if not ids:
        pytest.skip(f"no id with the role {ROLE!r} in the mapping")
    values = column(
        read_book(fetch.path_for(ARCHIVE_ID))["Fig5b"], "FRET Efficiency", WT_5UM_COLUMN
    )
    got = locate_blocks(values, [read_run(private_data.path(i, ROLE)) for i in ids])
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
    assert sum(got["run_molecules"]) == JOINED_MOLECULES, got["run_molecules"]
    assert got["leading_equal"] == JOINED_MOLECULES, got
