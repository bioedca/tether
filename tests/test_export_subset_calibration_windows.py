# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""A subset export carries the calibration windows of the molecules it copies (#1471).

Headless (no Qt). The calibration window a person or a policy set (``/calibration_windows``,
schema version 4, #928, ADR-0156) travels with its molecule, so the subset reads the window
the copied γ evidence was computed from.
"""

from __future__ import annotations

import pytest

pytest.importorskip("numpy")
h5py = pytest.importorskip("h5py")

import numpy as np  # noqa: E402

from _analysis_store import MEANS, build_store_with_model, to_str  # noqa: E402
from tether.imaging.extract import read_molecules  # noqa: E402
from tether.io.schema import read_schema_version  # noqa: E402
from tether.project import correction_states as cs  # noqa: E402
from tether.project.calibration_windows import (  # noqa: E402
    SOURCE_BLEACH_STEP,
    SOURCE_SET,
    read_calibration_window_log,
    read_calibration_windows,
    reset_calibration_window,
    set_calibration_window,
)
from tether.project.export import export_subset_tether  # noqa: E402

PERSON = "invented-w5r-curator"


def _state_matrix() -> np.ndarray:
    """A 4-molecule × 12-frame Viterbi state matrix (3 states)."""
    return np.array(
        [
            [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
            [0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1],
            [1, 1, 1, 2, 2, 2, 2, 1, 1, 1, 0, 0],
            [2, 2, 1, 1, 0, 0, 0, 1, 1, 2, 2, 2],
        ],
        dtype="int64",
    )


@pytest.fixture
def source(tmp_path):
    project, keys = build_store_with_model(tmp_path, _state_matrix(), MEANS)
    project.acquire_lock()
    yield project, keys
    project.release_lock()


def _by_key(path) -> dict[str, tuple[tuple[int, int], str]]:
    """Each molecule's calibration window and its source, keyed by ``molecule_key``."""
    windows, sources = read_calibration_windows(path)
    keys = [to_str(k) for k in read_molecules(path)["molecule_key"]]
    return {k: ((int(w[0]), int(w[1])), s) for k, w, s in zip(keys, windows, sources, strict=True)}


def test_a_set_window_reads_the_same_in_the_subset(source, tmp_path):
    project, keys = source
    set_calibration_window(project, keys[0], 2, 9, actor=cs.Actor.person(PERSON))
    expected = _by_key(project.path)[keys[0]]
    assert expected[1] == SOURCE_SET
    out = tmp_path / "subset.tether"

    export_subset_tether(project, out, molecule_keys=[keys[0], keys[1]])

    got = _by_key(out)
    assert got[keys[0]] == expected
    assert got[keys[1]] == _by_key(project.path)[keys[1]]
    assert got[keys[1]][1] == SOURCE_BLEACH_STEP


def test_rows_of_molecules_outside_the_subset_are_not_copied(source, tmp_path):
    project, keys = source
    set_calibration_window(project, keys[0], 2, 9, actor=cs.Actor.person(PERSON))
    set_calibration_window(project, keys[2], 1, 8, actor=cs.Actor.person(PERSON))
    out = tmp_path / "subset.tether"

    export_subset_tether(project, out, molecule_keys=[keys[2]])

    log = read_calibration_window_log(out)
    assert [to_str(k) for k in log["molecule_key"]] == [keys[2]]
    assert _by_key(out)[keys[2]] == _by_key(project.path)[keys[2]]


def test_the_latest_row_still_holds_in_the_subset(source, tmp_path):
    # Rows keep their order: a set, a reset and a set again read the last set.
    project, keys = source
    actor = cs.Actor.person(PERSON)
    set_calibration_window(project, keys[0], 2, 9, actor=actor)
    reset_calibration_window(project, keys[0], actor=actor)
    set_calibration_window(project, keys[0], 3, 7, actor=actor)
    reset_calibration_window(project, keys[1], actor=actor)  # nothing changes: no row
    out = tmp_path / "subset.tether"

    export_subset_tether(project, out, molecule_keys=[keys[0], keys[1]])

    source_log = read_calibration_window_log(project.path)
    sub_log = read_calibration_window_log(out)
    assert sub_log.shape == source_log.shape == (3,)
    for field in ("molecule_id", "molecule_key", "by", "actor", "reason", "timestamp"):
        assert [to_str(v) for v in sub_log[field]] == [to_str(v) for v in source_log[field]]
    np.testing.assert_array_equal(sub_log["window"], source_log["window"])
    assert _by_key(out)[keys[0]] == ((3, 7), SOURCE_SET)


def test_an_aliased_copy_replaces_the_person_who_set_a_window(source, tmp_path):
    project, keys = source
    set_calibration_window(project, keys[0], 2, 9, actor=cs.Actor.person(PERSON))
    out = tmp_path / "subset.tether"

    export_subset_tether(project, out, molecule_keys=[keys[0]], curator_alias="reviewer")

    log = read_calibration_window_log(out)
    assert [to_str(v) for v in log["actor"]] == ["reviewer-1"]
    assert PERSON.encode() not in out.read_bytes()
    assert _by_key(out)[keys[0]] == ((2, 9), SOURCE_SET)


def test_a_source_stamped_before_4_exports_as_before(tmp_path):
    project, keys = build_store_with_model(tmp_path, _state_matrix(), MEANS)
    with h5py.File(project.path, "r+") as f:
        del f["calibration_windows"]
        f.attrs["schema_version"] = 3
    out = tmp_path / "subset.tether"

    export_subset_tether(project, out, molecule_keys=[keys[0]])

    assert read_calibration_window_log(out).shape == (0,)
    assert {s for _, s in _by_key(out).values()} == {SOURCE_BLEACH_STEP}
    assert read_schema_version(project.path) == 3  # the source is not upgraded
