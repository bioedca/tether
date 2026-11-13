# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Self-drift of the full laboratory acquisitions, on the workstation only (#703, ADR-0071).

Each data acquisition of the name table runs from its movie to the FRET histogram with the
default settings and its series' map file, and its summary numbers are compared with
``tests/golden/workstation/<id>.json`` under the tolerance policy of #702 (ADR-0080). One test
per neutral id, so the runner's result file lists each id with ``passed`` or ``failed``. A
separate check proves that the two export routes of one movie give the same molecules and the
same traces.

The data is private: the ``private_data`` fixture skips every test here unless the workstation
runner started it, and an assertion message names neutral ids and quantities, never a file.
The format of the golden files is checked on the required matrix by
``tests/test_lab_golden_format.py``.
"""

from __future__ import annotations

import pytest

import _golden
import _lab_golden

pytestmark = pytest.mark.workstation


@pytest.mark.parametrize("neutral_id", _lab_golden.LAB_ACQUISITIONS)
def test_lab_numbers_match_the_golden_file(private_data, neutral_id: str, tmp_path) -> None:
    golden = _lab_golden.load_lab_golden(neutral_id)
    assert golden is not None, (
        f"no golden file for {neutral_id}; run scripts/workstation/update_lab_golden.py"
    )
    actual = _lab_golden.lab_quantities(
        private_data.path(neutral_id, _lab_golden.movie_role(private_data.roles(neutral_id))),
        private_data.path(neutral_id, _lab_golden.MAP_ROLE),
        tmp_path,
    )
    diffs = _golden.compare(golden, actual)
    relative = [d.relative for d in diffs if d.relative is not None]
    private_data.record(
        neutral_id,
        n_molecules=actual["molecules.count"],
        n_accepted=actual["molecules.accepted"],
        n_moved=len(diffs),
        max_relative_change=max(relative, default=0.0),
    )
    assert not diffs, (
        f"{len(diffs)} golden quantities of {neutral_id} moved (rtol {_golden.FLOAT_RTOL:g}). "
        "If intended, run scripts/workstation/update_lab_golden.py and paste its table in the "
        "pull request:\n" + _golden.diff_table({neutral_id: diffs})
    )


def test_export_routes_give_the_same_molecules_and_traces(private_data, tmp_path) -> None:
    """Item 4 of #703: the direct TIFF and the TIFF exported from the vendor format agree.

    Both routes are extracted with the map file of the vendor route, so only the movie differs.
    """
    ((nid, direct),) = _lab_golden.EXPORT_ROUTES.items()
    tmap = private_data.path(nid, _lab_golden.MAP_ROLE)
    projects = []
    for route in (nid, direct):
        movie = private_data.path(route, _lab_golden.movie_role(private_data.roles(route)))
        code, project = _lab_golden.extract_project(movie, tmap, tmp_path / route)
        assert code == 0 and project is not None, f"{route}: tether batch exited {code}"
        projects.append(project)
    diff = _lab_golden.compare_export_routes(*projects)
    private_data.record(nid, **diff)
    agree = diff["n_unmatched"] == 0 and diff["n_frames_a"] == diff["n_frames_b"]
    assert agree and diff["max_trace_diff"] == 0.0, f"{nid}: the two export routes differ: {diff}"
