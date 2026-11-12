# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The self-check of the workstation runner (ADR-0071, #698).

Every neutral id of the name-mapping file must name files that exist and that can be read. On the
maintainer's workstation it checks the real data directory at each scheduled run. In the required
CI matrix, ``tests/test_workstation_runner.py`` runs it through ``run_private_checks.py run
--dry-run`` against a tiny synthetic directory. The ``workstation`` marker keeps it out of every
CI job's own selection (``tests/test_marker_contract.py``).

An assertion message names the neutral id and the role, never the file: pytest's output goes to a
local log only, but the check is written as if it did not.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.workstation


def test_every_neutral_id_names_readable_files(private_data) -> None:
    assert private_data.ids, "the name-mapping file lists no neutral id"
    for neutral_id in private_data.ids:
        roles = private_data.roles(neutral_id)
        size = 0
        for role in roles:
            path = private_data.path(neutral_id, role)
            assert path.is_file(), f"{neutral_id}/{role}: no such file in the data directory"
            with path.open("rb") as fh:
                fh.read(1)
            size += path.stat().st_size
        private_data.record(neutral_id, n_files=len(roles), n_bytes=size)
