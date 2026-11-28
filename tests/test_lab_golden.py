# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Self-drift of the full laboratory acquisitions, on the workstation only (#703, ADR-0071).

Each data acquisition of the name table runs from its movie to the FRET histogram with the
default settings and its series' map file, and its summary numbers are compared with
``tests/golden/workstation/<id>.json`` under the tolerance policy of #702 (ADR-0080). One test
per neutral id, so the runner's result file lists each id with ``passed`` or ``failed``. A
separate check proves that the two export routes of one movie give the same molecules and the
same traces. The cellSens ``.vsi`` movies (#1433) run the same way, each turned to its map's
orientation first, and a route check holds each one to the TIFF made from it in ImageJ; both
skip with a reason when the Bio-Formats stack is not installed.

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


def _skip_without_the_vsi_reader(neutral_id: str) -> None:
    """A ``.vsi`` id needs the Bio-Formats stack (ADR-0147); without it, skip and say why."""
    if neutral_id in _lab_golden.VSI_ACQUISITIONS:
        reason = _lab_golden.vsi_skip_reason()
        if reason is not None:
            pytest.skip(f"{neutral_id}: {reason}")


@pytest.mark.parametrize("neutral_id", _lab_golden.GOLDEN_IDS)
def test_lab_numbers_match_the_golden_file(private_data, neutral_id: str, tmp_path) -> None:
    _skip_without_the_vsi_reader(neutral_id)
    golden = _lab_golden.load_lab_golden(neutral_id)
    assert golden is not None, (
        f"no golden file for {neutral_id}; run scripts/workstation/update_lab_golden.py"
    )
    actual = _lab_golden.lab_quantities(
        private_data.path(neutral_id, _lab_golden.movie_role(private_data.roles(neutral_id))),
        private_data.path(neutral_id, _lab_golden.MAP_ROLE),
        tmp_path,
        turns=_lab_golden.map_turns(neutral_id),
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

    The vendor route is the direct TIFF turned a quarter turn (#887), so its frames are turned
    back first. Both routes are then extracted with the map file of the direct route, so only
    the export route differs.
    """
    ((nid, direct),) = _lab_golden.EXPORT_ROUTES.items()
    tmap = private_data.path(direct, _lab_golden.MAP_ROLE)
    vendor = private_data.path(nid, _lab_golden.movie_role(private_data.roles(nid)))
    (tmp_path / "turned").mkdir()
    movies = {
        nid: _lab_golden.rotate_movie(vendor, tmp_path / "turned", _lab_golden.map_turns(nid)),
        direct: private_data.path(direct, _lab_golden.movie_role(private_data.roles(direct))),
    }
    projects = []
    for route, movie in movies.items():
        code, project = _lab_golden.extract_project(movie, tmap, tmp_path / route)
        assert code == 0 and project is not None, f"{route}: tether batch exited {code}"
        projects.append(project)
    diff = _lab_golden.compare_export_routes(*projects)
    private_data.record(nid, rotation_k=_lab_golden.EXPORT_ROUTE_ROTATION, **diff)
    agree = (
        diff["n_matched"] > 0
        and diff["n_unmatched"] == 0
        and diff["n_nonfinite_diff"] == 0
        and diff["n_frames_a"] == diff["n_frames_b"]
    )
    assert agree and diff["max_trace_diff"] == 0.0, f"{nid}: the two export routes differ: {diff}"


@pytest.mark.parametrize("neutral_id", _lab_golden.VSI_ACQUISITIONS)
def test_a_turned_vsi_and_its_imagej_tiff_give_the_same_molecules_and_traces(
    private_data, neutral_id: str, tmp_path
) -> None:
    """#1433: the ``.vsi`` turned by its recorded turn is the TIFF made from it in ImageJ.

    Both are extracted with the map file of the series, so a wrong recorded turn, or a ``.vsi``
    read that differs from what ImageJ read, shows as unmatched molecules or a trace difference.
    """
    _skip_without_the_vsi_reader(neutral_id)
    tmap = private_data.path(neutral_id, _lab_golden.MAP_ROLE)
    (tmp_path / "turned").mkdir()
    vsi = private_data.path(neutral_id, _lab_golden.VSI_ROLE)
    movies = {
        "vsi": _lab_golden.rotate_movie(
            vsi, tmp_path / "turned", _lab_golden.map_turns(neutral_id)
        ),
        "tiff": private_data.path(neutral_id, _lab_golden.VSI_TIFF_ROLE),
    }
    projects = []
    for route, movie in movies.items():
        code, project = _lab_golden.extract_project(movie, tmap, tmp_path / route)
        assert code == 0 and project is not None, (
            f"{neutral_id} {route}: tether batch exited {code}"
        )
        projects.append(project)
    diff = _lab_golden.compare_export_routes(*projects)
    private_data.record(neutral_id, rotation_k=_lab_golden.map_turns(neutral_id), **diff)
    agree = (
        diff["n_matched"] > 0
        and diff["n_unmatched"] == 0
        and diff["n_nonfinite_diff"] == 0
        and diff["n_frames_a"] == diff["n_frames_b"]
    )
    assert agree and diff["max_trace_diff"] == 0.0, (
        f"{neutral_id}: the turned .vsi and its ImageJ TIFF differ: {diff}"
    )
