# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Trace generations: a new extraction never overwrites a reviewed one (#921, ADR-0136).

One test per acceptance criterion of #921, on small synthetic movies:

* each extraction writes a generation (id, inputs, time) and the store is version 2;
* a store written before generations reads as generation 1 of each movie, unchanged;
* a labelled movie extracted again keeps its first generation, labels and all, readable;
* one generation is active, and the readers of traces read it;
* a spot that did not move keeps its ``molecule_key``; a moved one does not;
* a new generation marks the fits and corrections of the movie stale;
* the append of #347 writes a generation.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("skimage")
pytest.importorskip("h5py")
pytest.importorskip("tifffile")

import h5py  # noqa: E402
import numpy as np  # noqa: E402
import tifffile  # noqa: E402

from tether.imaging.extract import molecule_key, read_molecules, read_traces  # noqa: E402
from tether.imaging.split import ChannelGeometry, channel_xy_to_frame  # noqa: E402
from tether.io.generations import (  # noqa: E402
    GENERATION_MATCH_PX,
    active_generation,
    carried_molecule_keys,
    match_previous,
    read_generation,
    read_generations,
)
from tether.io.schema import (  # noqa: E402
    SCHEMA_VERSION,
    assert_is_compatible_project,
    read_schema_version,
)
from tether.project import labels, lock  # noqa: E402
from tether.project.core import Project  # noqa: E402
from tether.project.extract import (  # noqa: E402
    ExtractionError,
    ExtractOptions,
    add_movie_to_project,
    extract_movie,
    reextract_movie,
)
from tether.project.jobs import read_stale_marks, record_inputs  # noqa: E402

_BG = 80.0
_AMP = 400.0
_SIGMA = 1.5
_SHAPE = (64, 96)
_WINDOW = 21
_N_FRAMES = 12
_DONOR_A = np.array([[12.0, 12.0], [24.0, 40.0], [16.0, 52.0]])
_DONOR_B = np.array([[14.0, 20.0], [30.0, 30.0], [20.0, 48.0], [34.0, 14.0]])
_SHIFT = np.array([49.0, 0.0])


def _text(value: object) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _write_movie(path: Path, donor: np.ndarray) -> Path:
    centers = np.vstack([donor, donor + _SHIFT])
    rows, cols = np.mgrid[0 : _SHAPE[0], 0 : _SHAPE[1]]
    spots = np.zeros(_SHAPE)
    for x, y in centers:
        spots += np.exp(-((rows - y) ** 2 + (cols - x) ** 2) / (2.0 * _SIGMA**2))
    ramp = _AMP * (1.0 + 0.02 * np.arange(_N_FRAMES))
    stack = _BG + ramp[:, None, None] * spots[None]
    tifffile.imwrite(path, np.ascontiguousarray(stack, dtype=">u2"), byteorder=">")
    return path


def _movie_a(tmp_path: Path) -> Path:
    return _write_movie(tmp_path / "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif", _DONOR_A)


def _movie_b(tmp_path: Path) -> Path:
    return _write_movie(tmp_path / "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_011.tif", _DONOR_B)


def _options(**overrides) -> ExtractOptions:
    return ExtractOptions(window=_WINDOW, **overrides)


def _project_a_b(tmp_path: Path) -> tuple[Path, Path, Path]:
    project = tmp_path / "experiment.tether"
    movie_a, movie_b = _movie_a(tmp_path), _movie_b(tmp_path)
    extract_movie(movie_a, project, options=_options())
    with lock.publish_lock(project) as guard:
        add_movie_to_project(project, movie_b, options=_options(), publish_guard=guard)
    return project, movie_a, movie_b


def _movie_ids(project: Path) -> list[str]:
    with h5py.File(project, "r") as f:
        return [_text(v) for v in f["movies"]["table"]["movie_id"][()]]


def _reextract(project: Path, movie: Path, **kwargs):
    with lock.publish_lock(project) as guard:
        return reextract_movie(project, movie, options=_options(), publish_guard=guard, **kwargs)


def _assert_rows_equal(actual: np.ndarray, expected: np.ndarray) -> None:
    """Field by field, so a NaN sentinel compares equal to itself."""
    assert actual.dtype.names == expected.dtype.names
    for name in expected.dtype.names:
        np.testing.assert_array_equal(actual[name], expected[name], err_msg=name)


def _make_v1(project: Path) -> None:
    """Turn a store into one written before generations: no table, stamp 1."""
    with h5py.File(project, "r+") as f:
        del f["generations"]
        f.attrs["schema_version"] = 1


def test_each_extraction_writes_a_generation_with_its_inputs_and_time(tmp_path: Path) -> None:
    project, movie_a, movie_b = _project_a_b(tmp_path)
    assert read_schema_version(project) == SCHEMA_VERSION == 2
    records = read_generations(project)
    assert [(g.generation, g.active, g.recorded) for g in records] == [(1, True, True)] * 2
    first = records[0]
    assert first.generation_id.startswith("gen-")
    assert first.created_utc.endswith("Z")
    assert first.n_molecules == len(_DONOR_A)
    sha = hashlib.sha256(movie_a.read_bytes()).hexdigest()
    assert first.inputs["source"]["sha256"] == sha
    assert first.inputs["map"]["calibration_id"].startswith("cal-")
    assert first.inputs["colocalization"] == "partner-in-both"
    assert first.inputs["options"]["window"] == _WINDOW
    assert set(first.inputs["geometry"]) >= {"donor_crop", "acceptor_crop", "donor_flip"}
    # The append of #347 wrote its own generation, with another id.
    assert records[1].movie_id != first.movie_id
    assert records[1].generation_id != first.generation_id


def test_a_store_from_before_generations_reads_as_generation_1_unchanged(tmp_path: Path) -> None:
    project, _, _ = _project_a_b(tmp_path)
    _make_v1(project)
    before = project.read_bytes()
    assert assert_is_compatible_project(project) == 1
    records = read_generations(project)
    assert [(g.generation, g.active, g.recorded, g.generation_id) for g in records] == [
        (1, True, False, "")
    ] * 2
    assert records[0].inputs["source"]["sha256"]
    assert read_generation(project, records[0].movie_id).molecules.shape[0] == len(_DONOR_A)
    assert project.read_bytes() == before


def test_a_labelled_movie_extracted_again_keeps_its_first_generation(tmp_path: Path) -> None:
    project, movie_a, _ = _project_a_b(tmp_path)
    movie_a_id, movie_b_id = _movie_ids(project)
    first = read_generation(project, movie_a_id)
    keys = [_text(k) for k in first.molecules["molecule_key"]]
    labels.accept(project, keys[0], labeler="curator")
    labels.reject(project, keys[1], labeler="curator")
    with h5py.File(project, "r+") as f:
        table = f["molecules"]["table"]
        row = table[0]
        row["category"] = "folded"
        row["analysis_window"] = [2, 9]
        table[0] = row
    labelled = read_generation(project, movie_a_id)
    b_before = read_generation(project, movie_b_id)

    summary = _reextract(project, movie_a)

    assert summary.extraction.movie_id == movie_a_id
    assert summary.generation.generation == 2
    old = read_generation(project, movie_a_id, 1)
    assert not old.generation.active
    _assert_rows_equal(old.molecules, labelled.molecules)
    for name, data in labelled.traces.items():
        np.testing.assert_array_equal(old.traces[name], data)
    for name, data in labelled.patches.items():
        np.testing.assert_array_equal(old.patches[name], data)
    assert int(old.molecules["curation_label"][0]) == labels.CurationLabel.ACCEPT
    assert int(old.molecules["curation_label"][1]) == labels.CurationLabel.REJECT
    # The label log is untouched, and the other movie is exactly as it was.
    assert {_text(k) for k in labels.read_labels(project)["molecule_key"]} >= set(keys[:2])
    b_after = read_generation(project, movie_b_id)
    _assert_rows_equal(b_after.molecules, b_before.molecules)
    for name, data in b_before.traces.items():
        np.testing.assert_array_equal(b_after.traces[name], data)


def test_the_spots_that_did_not_move_keep_their_key_and_carry_their_labels(
    tmp_path: Path,
) -> None:
    project, movie_a, _ = _project_a_b(tmp_path)
    movie_a_id, _ = _movie_ids(project)
    keys = [_text(k) for k in read_generation(project, movie_a_id).molecules["molecule_key"]]
    labels.reject(project, keys[1], labeler="curator")
    with h5py.File(project, "r+") as f:
        table = f["molecules"]["table"]
        row = table[0]
        row["category"] = "folded"
        row["analysis_window"] = [2, 9]
        table[0] = row

    summary = _reextract(project, movie_a)

    new = read_generation(project, movie_a_id)
    assert new.generation.active and new.generation.generation == 2
    assert [_text(k) for k in new.molecules["molecule_key"]] == keys
    assert sorted(summary.generation.carried_keys) == sorted(keys)
    assert summary.n_carried == len(keys)
    assert summary.generation.match_px == GENERATION_MATCH_PX
    assert carried_molecule_keys(project) == set(keys)
    assert _text(new.molecules["category"][0]) == "folded"
    assert list(new.molecules["analysis_window"][0]) == [2, 9]
    assert int(new.molecules["curation_label"][1]) == labels.CurationLabel.REJECT
    assert keys[1] in Project.open(project).rejected_molecule_keys()
    # Corrections start again from their not-computed sentinels.
    assert np.isnan(new.molecules["gamma"]).all()


def test_a_spot_that_moved_gets_a_new_key_and_no_labels(tmp_path: Path) -> None:
    project, movie_a, _ = _project_a_b(tmp_path)
    movie_a_id, _ = _movie_ids(project)
    # As if the first generation had found molecule 0 two pixels away from where the
    # new extraction finds it: its stored position and its key are both moved.
    sha = hashlib.sha256(movie_a.read_bytes()).hexdigest()
    with h5py.File(project, "r+") as f:
        table = f["molecules"]["table"]
        row = table[0]
        row["donor_xy"] = np.asarray(row["donor_xy"]) + [2.0, 0.0]
        row["molecule_key"] = molecule_key(sha, row["donor_xy"])
        table[0] = row
    keys = [_text(k) for k in read_generation(project, movie_a_id).molecules["molecule_key"]]
    labels.reject(project, keys[0], labeler="curator")

    summary = _reextract(project, movie_a)

    new = read_generation(project, movie_a_id)
    new_keys = [_text(k) for k in new.molecules["molecule_key"]]
    assert keys[0] not in new_keys
    assert new_keys[1:] == keys[1:]
    assert sorted(summary.generation.carried_keys) == sorted(keys[1:])
    assert int(new.molecules["curation_label"][0]) == labels.CurationLabel.UNCURATED
    # The moved molecule's labels stay readable on the first generation.
    assert int(read_generation(project, movie_a_id, 1).molecules["curation_label"][0]) == (
        labels.CurationLabel.REJECT
    )


def test_one_generation_is_active_and_the_readers_read_it(tmp_path: Path) -> None:
    project, movie_a, _ = _project_a_b(tmp_path)
    movie_a_id, _ = _movie_ids(project)
    _reextract(project, movie_a)
    _reextract(project, movie_a)

    records = read_generations(project, movie_a_id)
    assert [g.generation for g in records] == [1, 2, 3]
    assert [g.active for g in records] == [False, False, True]
    assert active_generation(project, movie_a_id).generation == 3
    assert len({g.generation_id for g in records}) == 3
    molecules = read_molecules(project)
    assert molecules.shape[0] == len(_DONOR_A) + len(_DONOR_B)
    assert len({_text(k) for k in molecules["molecule_key"]}) == molecules.shape[0]
    traces = read_traces(project)
    assert {data.shape[0] for data in traces.values()} == {molecules.shape[0]}
    # The active generation's rows are the last block of the readers' arrays.
    active = read_generation(project, movie_a_id)
    _assert_rows_equal(molecules[-len(_DONOR_A) :], active.molecules)
    np.testing.assert_array_equal(
        traces["donor_corrected"][-len(_DONOR_A) :, :_N_FRAMES], active.traces["donor_corrected"]
    )
    with pytest.raises(KeyError, match="no generation 7"):
        read_generation(project, movie_a_id, 7)


def test_a_new_generation_marks_the_fits_and_corrections_of_the_movie_stale(
    tmp_path: Path,
) -> None:
    project, movie_a, _ = _project_a_b(tmp_path)
    movie_a_id, movie_b_id = _movie_ids(project)
    gen_a = active_generation(project, movie_a_id).generation_id
    gen_b = active_generation(project, movie_b_id).generation_id
    handle = Project.open(project)
    handle.acquire_lock()
    try:
        record_inputs(handle, "fit", "fit-a", {("trace_generation", movie_a_id): gen_a})
        record_inputs(handle, "fit", "fit-b", {("trace_generation", movie_b_id): gen_b})
        record_inputs(
            handle, "correction_version", "cv-1", {("trace_generation", movie_a_id): gen_a}
        )
    finally:
        handle.release_lock()

    _reextract(project, movie_a)

    marked = {(m.node, m.instance) for m in read_stale_marks(project)}
    assert ("fit", "fit-a") in marked
    assert ("correction_version", "cv-1") in marked
    assert ("fit", "fit-b") not in marked


def test_a_store_from_before_generations_takes_a_new_generation(tmp_path: Path) -> None:
    project, movie_a, _ = _project_a_b(tmp_path)
    _make_v1(project)
    movie_a_id, _ = _movie_ids(project)

    _reextract(project, movie_a)

    assert read_schema_version(project) == SCHEMA_VERSION
    records = read_generations(project, movie_a_id)
    assert [(g.generation, g.recorded, g.created_utc == "") for g in records] == [
        (1, True, True),
        (2, True, False),
    ]
    assert read_generation(project, movie_a_id, 1).molecules.shape[0] == len(_DONOR_A)


def test_a_movie_the_project_lacks_is_not_given_a_generation(tmp_path: Path) -> None:
    project = tmp_path / "experiment.tether"
    extract_movie(_movie_a(tmp_path), project, options=_options())
    before = project.read_bytes()
    with pytest.raises(ExtractionError, match="append it with add_movie_to_project"):
        _reextract(project, _movie_b(tmp_path))
    assert project.read_bytes() == before


def test_the_append_still_refuses_a_movie_the_project_holds(tmp_path: Path) -> None:
    project, movie_a, _ = _project_a_b(tmp_path)
    with lock.publish_lock(project) as guard, pytest.raises(ExtractionError, match="once"):
        add_movie_to_project(project, movie_a, options=_options(), publish_guard=guard)


def test_match_previous_is_one_to_one_closest_first() -> None:
    previous = np.array([[10.0, 10.0], [20.0, 20.0], [40.0, 40.0]])
    new = np.array([[10.3, 10.0], [10.1, 10.0], [20.0, 20.5], [41.0, 40.0]])
    assert match_previous(previous, new).tolist() == [-1, 0, 1, -1]
    assert match_previous(previous, new, match_px=1.0).tolist() == [-1, 0, 1, 2]
    assert match_previous(np.empty((0, 2)), new).tolist() == [-1] * 4
    with pytest.raises(ValueError, match="match_px"):
        match_previous(previous, new, match_px=-1.0)


@pytest.mark.parametrize(
    ("crop_shift", "xy_shift", "carried"),
    [
        (20, 0.0, False),  # another crop, same sub-image positions: other camera places
        (3, -3.0, True),  # another crop, positions moved with it: the same camera places
    ],
)
def test_spots_are_matched_in_the_camera_frame_across_a_changed_crop(
    tmp_path: Path, crop_shift: int, xy_shift: float, carried: bool
) -> None:
    project, movie_a, _ = _project_a_b(tmp_path)
    movie_a_id, _ = _movie_ids(project)
    with h5py.File(project, "r+") as f:
        movies = f["movies"]["table"]
        movie = movies[0]
        movie["donor_crop"] = np.asarray(movie["donor_crop"]) + [0, crop_shift, 0, crop_shift]
        movies[0] = movie
        table = f["molecules"]["table"]
        for i in range(len(_DONOR_A)):
            row = table[i]
            row["donor_xy"] = np.asarray(row["donor_xy"]) + [xy_shift, 0.0]
            table[i] = row
        row = table[0]
        row["category"] = "folded"
        table[0] = row
    keys = [_text(k) for k in read_generation(project, movie_a_id).molecules["molecule_key"]]

    summary = _reextract(project, movie_a)

    new = read_generation(project, movie_a_id)
    if carried:
        assert sorted(summary.generation.carried_keys) == sorted(keys)
        assert _text(new.molecules["category"][0]) == "folded"
    else:
        assert summary.generation.carried_keys == ()
        assert "folded" not in {_text(c) for c in new.molecules["category"]}
        # The same sub-image positions would hash to the old keys; another molecule at
        # one of them takes a key that names its generation, so no key names two.
        new_keys = {_text(k) for k in new.molecules["molecule_key"]}
        assert not new_keys & set(keys)
        assert len(new_keys) == len(keys)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("flip", [(0, 0), (1, 0), (0, 1), (1, 1)])
def test_channel_positions_map_back_to_the_camera_pixel_they_came_from(
    rotation: int, flip: tuple[int, int]
) -> None:
    frame = np.arange(7 * 11).reshape(7, 11)
    whole = ChannelGeometry(rotation_deg=rotation, flip=flip).apply(frame)
    crop = (2, 3, whole.shape[0] - 1, whole.shape[1] - 1)
    sub = ChannelGeometry(crop=crop, rotation_deg=rotation, flip=flip).apply(frame)
    rows, cols = np.mgrid[0 : sub.shape[0], 0 : sub.shape[1]]
    xy = np.column_stack([cols.ravel(), rows.ravel()]).astype(float)
    back = channel_xy_to_frame(
        xy, frame_shape=frame.shape, crop=crop, rotation_deg=rotation, flip=flip
    )
    assert np.array_equal(frame[back[:, 1].astype(int), back[:, 0].astype(int)], sub.ravel())
    # The all-zero stored crop is the whole frame.
    full = channel_xy_to_frame(
        xy[:1], frame_shape=frame.shape, crop=(0, 0, 0, 0), rotation_deg=rotation, flip=flip
    )
    assert frame[int(full[0, 1]), int(full[0, 0])] == whole[0, 0]
