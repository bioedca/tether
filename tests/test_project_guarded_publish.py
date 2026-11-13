# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The success path of the guarded stage-and-publish writers (#727).

With a ``write_guard``, the leakage, gamma, corrected-FRET and idealization writers
build their update in a sibling copy of the project and publish it with one
``os.replace`` after a last ownership check (``tether.project._guarded_publish`` and
``write_idealization_model``). ``run_batch`` takes this path when it overwrites under
``policy="fail"``. The older tests only make the guard refuse. These tests let the
guard pass and then read the result back from the canonical project, so a lost
publish step fails here (PRJ-F1, PRJ-F2). A guarded run must leave the canonical
file exactly as the in-place run does, so each test runs the same writer on a twin
store without a guard and compares the two.

The photobleach writer is not staged: it writes its two columns in place, after a
second ownership check. Its tests check that a refusal at either check leaves the
canonical columns unchanged (PRJ-F3).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("numpy")
pytest.importorskip("h5py")

import h5py  # noqa: E402
import numpy as np  # noqa: E402

from test_project_correct import _build_store as _correct_store  # noqa: E402, PLC2701
from test_project_correct import _set_factors  # noqa: E402, PLC2701
from test_project_gamma import _build_store as _gamma_traces_store  # noqa: E402, PLC2701
from test_project_gamma import _gamma_store  # noqa: E402, PLC2701
from test_project_gamma import _step_trace as _gamma_step_trace  # noqa: E402, PLC2701
from test_project_idealize import _build_store as _idealize_store  # noqa: E402, PLC2701
from test_project_idealize import _make_runner  # noqa: E402, PLC2701
from test_project_idealize import _step_trace as _idealize_step_trace  # noqa: E402, PLC2701
from test_project_leakage import _cohort_store  # noqa: E402, PLC2701
from test_project_photobleach import _build_store as _photobleach_store  # noqa: E402, PLC2701
from test_project_photobleach import _traces_with_known_bleach  # noqa: E402, PLC2701
from tether.imaging.extract import read_molecules  # noqa: E402
from tether.project import lock  # noqa: E402
from tether.project.batch import (  # noqa: E402
    POLICY_FAIL,
    STAGE_CORRECT,
    STAGE_EXTRACT,
    STAGE_IDEALIZE,
    STATUS_DONE,
    MovieJob,
    run_batch,
    run_correct_stage,
)
from tether.project.correct import METHOD_CORRECTED, compute_corrected_fret  # noqa: E402
from tether.project.gamma import compute_gamma  # noqa: E402
from tether.project.idealize import (  # noqa: E402
    idealize_molecules,
    list_idealizations,
    read_idealization,
)
from tether.project.leakage import compute_leakage_alpha  # noqa: E402
from tether.project.photobleach import compute_photobleach  # noqa: E402

_CORRECTION_COLUMNS = (
    "bleach_frames",
    "analysis_window",
    "alpha",
    "gamma",
    "correction_method",
    "correction_confidence",
)


class _PassingGuard:
    """An ownership check that always passes and notes whether a staged sibling exists."""

    def __init__(self, project: Path, label: str) -> None:
        self._folder = project.parent
        self._pattern = f".{project.name}.*.{label}.tmp"
        self.calls = 0
        self.saw_stage = False

    def __call__(self) -> None:
        self.calls += 1
        if self.stages_left():
            self.saw_stage = True

    def stages_left(self) -> list[Path]:
        return list(self._folder.glob(self._pattern))


def _twin(tmp_path: Path, build: Callable[[Path], object]) -> tuple[Path, Path]:
    """Build the same store twice: one for the guarded run, one for the in-place run."""
    paths = []
    for name in ("guarded", "in_place"):
        folder = tmp_path / name
        folder.mkdir()
        path = folder / "x.tether"
        build(path)
        paths.append(path)
    return paths[0], paths[1]


def _column(path: Path, name: str) -> np.ndarray:
    return np.asarray(read_molecules(path)[name])


def _assert_same_columns(guarded: Path, in_place: Path, names: tuple[str, ...]) -> None:
    for name in names:
        np.testing.assert_array_equal(_column(guarded, name), _column(in_place, name), err_msg=name)


def _assert_same_stamp(guarded: Path, in_place: Path, group: str) -> None:
    """The guarded run published the same ``/settings/<group>`` as the in-place run."""
    with h5py.File(guarded, "r") as g, h5py.File(in_place, "r") as p:
        assert group in g["settings"], f"/settings/{group} was not published"
        published = dict(g["settings"][group].attrs)
        expected = dict(p["settings"][group].attrs)
    expected.pop("created_utc", None)
    published.pop("created_utc", None)
    assert sorted(published) == sorted(expected)
    for key, value in expected.items():
        np.testing.assert_array_equal(published[key], value, err_msg=key)


# --- PRJ-F1: leakage, gamma and corrected FRET ------------------------------


def test_guarded_leakage_publishes_alpha_and_stamp(tmp_path: Path) -> None:
    guarded, in_place = _twin(tmp_path, lambda p: _cohort_store(p, n_mol=12, alpha=0.1))
    guard = _PassingGuard(guarded, "leakage")

    summary = compute_leakage_alpha(guarded, write_guard=guard)
    expected = compute_leakage_alpha(in_place)

    assert summary.applied is True
    assert summary.alpha == expected.alpha
    assert guard.saw_stage
    assert not guard.stages_left()
    assert np.all(np.isfinite(_column(guarded, "alpha")))
    _assert_same_columns(guarded, in_place, ("alpha",))
    _assert_same_stamp(guarded, in_place, "leakage")


def test_guarded_gamma_publishes_gamma_and_stamp(tmp_path: Path) -> None:
    guarded, in_place = _twin(tmp_path, lambda p: _gamma_store(p, n_mol=12))
    guard = _PassingGuard(guarded, "gamma")

    summary = compute_gamma(guarded, write_guard=guard)
    expected = compute_gamma(in_place)

    assert summary.applied is True
    assert summary.gamma == expected.gamma
    assert guard.saw_stage
    assert not guard.stages_left()
    assert np.all(np.isfinite(_column(guarded, "gamma")))
    _assert_same_columns(guarded, in_place, ("gamma",))
    _assert_same_stamp(guarded, in_place, "gamma")


def test_guarded_correction_publishes_methods_and_stamp(tmp_path: Path) -> None:
    def build(path: Path) -> None:
        _correct_store(path, n_mol=6)
        _set_factors(path, alpha=0.09, gamma=1.2)

    guarded, in_place = _twin(tmp_path, build)
    guard = _PassingGuard(guarded, "correction")

    summary = compute_corrected_fret(guarded, write_guard=guard)
    compute_corrected_fret(in_place)

    assert summary.n_corrected == 6
    assert guard.saw_stage
    assert not guard.stages_left()
    assert [m.decode() for m in _column(guarded, "correction_method")] == [METHOD_CORRECTED] * 6
    _assert_same_columns(guarded, in_place, _CORRECTION_COLUMNS)
    _assert_same_stamp(guarded, in_place, "correction")


def _step_cohort_extract(movie_path, output_path, *, publish_guard=None, **_kwargs):
    """Stand in for extraction: 12 step traces that give alpha, gamma and corrected E."""
    rows = [
        _gamma_step_trace(
            n=120, acceptor_pb=40, donor_pb=100, gamma_true=1.2, alpha=0.1, seed=i + 1
        )
        for i in range(12)
    ]
    donor = np.stack([d for d, _ in rows])
    acceptor = np.stack([a for _, a in rows])
    _gamma_traces_store(Path(output_path), donor, acceptor)
    if publish_guard is not None:
        publish_guard()
    return SimpleNamespace(n_molecules=12, low_confidence_registration=False)


def test_batch_overwrite_publishes_every_guarded_stage(tmp_path: Path) -> None:
    # overwrite + policy="fail" is the batch path that owns the destination lock and
    # passes its nonce check into every writer. Run it with the real correct stage
    # and the real idealize writer (fake sidecar) and compare with in-place runs.
    (tmp_path / "guarded").mkdir()
    (tmp_path / "in_place").mkdir()
    out = tmp_path / "guarded" / "video_010.tether"
    in_place = tmp_path / "in_place" / "video_010.tether"
    jobs = [MovieJob(movie_path=tmp_path / "video_010.tif", output_path=out)]

    summary = run_batch(
        jobs,
        policy=POLICY_FAIL,
        overwrite=True,
        idealize_kwargs={"nstates": 2, "_runner": _make_runner({2: -1.0}, [])},
        _extract=_step_cohort_extract,
    )

    result = summary.results[0]
    statuses = [result.stages[s].status for s in (STAGE_EXTRACT, STAGE_CORRECT, STAGE_IDEALIZE)]
    assert statuses == [STATUS_DONE] * 3
    assert result.ok
    assert "α=0.100" in result.stages[STAGE_CORRECT].detail
    assert lock.read_lock(out) is None

    _step_cohort_extract(None, in_place)
    run_correct_stage(in_place)
    expected = idealize_molecules(in_place, nstates=2, _runner=_make_runner({2: -1.0}, []))

    assert [m.decode() for m in _column(out, "correction_method")] == [METHOD_CORRECTED] * 12
    _assert_same_columns(out, in_place, _CORRECTION_COLUMNS)
    for group in ("leakage", "gamma", "correction"):
        _assert_same_stamp(out, in_place, group)
    model = read_idealization(out, "vbconhmm")
    assert model.molecule_keys == expected.molecule_keys
    assert model.input_hashes == expected.input_hashes
    np.testing.assert_array_equal(model.state_paths, expected.state_paths)
    assert not [p.name for p in out.parent.iterdir() if p.name.endswith(".tmp")]


# --- PRJ-F2: the idealization model -----------------------------------------


def test_guarded_idealization_publishes_the_model(tmp_path: Path) -> None:
    path = tmp_path / "ideal.tether"
    proj, keys = _idealize_store(path, _idealize_step_trace(3, 30), _idealize_step_trace(3, 30))
    guard = _PassingGuard(path, "idealization")

    stored = idealize_molecules(
        proj, nstates=2, write_guard=guard, _runner=_make_runner({2: -1.0}, [])
    )

    assert guard.saw_stage
    assert not guard.stages_left()
    assert list_idealizations(proj) == ["vbconhmm"]
    back = read_idealization(proj, "vbconhmm")
    assert back.molecule_keys == keys == stored.molecule_keys
    assert back.input_hashes == stored.input_hashes
    assert back.nstates == 2
    np.testing.assert_allclose(back.means, stored.means)
    np.testing.assert_array_equal(back.state_paths, stored.state_paths)


def test_guard_refusal_at_publish_keeps_the_previous_model(tmp_path: Path) -> None:
    path = tmp_path / "ideal.tether"
    proj, _ = _idealize_store(path, _idealize_step_trace(3, 30), _idealize_step_trace(3, 30))
    before = idealize_molecules(proj, nstates=2, _runner=_make_runner({2: -1.0}, []))
    pattern = f".{path.name}.*.idealization.tmp"
    refused: list[Path] = []

    def refuse_when_stage_holds_new_model() -> None:
        # Ownership is lost after the new model is complete in the staged copy:
        # this is the last check before the os.replace.
        for stage in path.parent.glob(pattern):
            try:
                with h5py.File(stage, "r") as staged:
                    complete = int(staged["idealization/vbconhmm"].attrs["nstates"]) == 3
            except (OSError, KeyError):
                complete = False
            if complete:
                refused.append(stage)
                raise lock.LockError("destination ownership changed before publish")

    with pytest.raises(lock.LockError, match="before publish"):
        idealize_molecules(
            proj,
            nstates=3,
            overwrite=True,
            write_guard=refuse_when_stage_holds_new_model,
            _runner=_make_runner({3: -1.0}, []),
        )

    assert refused
    after = read_idealization(proj, "vbconhmm")
    assert after.nstates == 2
    np.testing.assert_allclose(after.means, before.means)
    np.testing.assert_array_equal(after.state_paths, before.state_paths)
    assert not list(path.parent.glob(pattern))


# --- PRJ-F3: the photobleach ownership checks --------------------------------


@pytest.mark.parametrize("refuse_on_call", [1, 2], ids=["before-open", "before-table-write"])
def test_photobleach_refusal_leaves_canonical_columns_unchanged(
    tmp_path: Path, refuse_on_call: int
) -> None:
    donor, acceptor, _, _ = _traces_with_known_bleach()
    path = tmp_path / "pb.tether"
    _photobleach_store(path, donor, acceptor)
    before = {name: _column(path, name) for name in ("bleach_frames", "analysis_window")}
    calls = 0

    def lose_ownership() -> None:
        nonlocal calls
        calls += 1
        if calls == refuse_on_call:
            raise lock.LockError("destination ownership changed")

    with pytest.raises(lock.LockError, match="ownership changed"):
        compute_photobleach(path, write_guard=lose_ownership)

    assert calls == refuse_on_call
    for name, value in before.items():
        np.testing.assert_array_equal(_column(path, name), value, err_msg=name)


def test_photobleach_guard_that_passes_writes_the_detector_result(tmp_path: Path) -> None:
    donor, acceptor, _, _ = _traces_with_known_bleach()
    guarded, in_place = _twin(tmp_path, lambda p: _photobleach_store(p, donor, acceptor))
    before = _column(guarded, "bleach_frames")
    calls: list[None] = []

    compute_photobleach(guarded, write_guard=lambda: calls.append(None))
    compute_photobleach(in_place)

    assert len(calls) == 2  # before the store is opened, and before the table write
    assert not np.array_equal(_column(guarded, "bleach_frames"), before)
    _assert_same_columns(guarded, in_place, ("bleach_frames", "analysis_window"))
