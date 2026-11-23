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

The photobleach writer is not staged on its own: it writes its two columns in place,
after a second ownership check. Its tests check that a refusal at either check leaves
the canonical columns unchanged (PRJ-F3).

A guarded ``run_correct_stage`` stages once for all four passes (#258): one copy, one
publish, every writer's own ownership checks kept, and nothing published when a pass
or the last check fails.
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


def test_guarded_write_refuses_a_canonical_file_replaced_meanwhile(tmp_path: Path) -> None:
    """A staged copy of a replaced file is never published over the newer file (#346)."""
    from tether.project._guarded_publish import guarded_project_write
    from tether.project.lock import LockError

    canonical = tmp_path / "p.tether"
    canonical.write_bytes(b"old project")
    newer = tmp_path / "newer.tmp"
    newer.write_bytes(b"newer project")

    with (
        pytest.raises(LockError, match="replaced by another writer"),
        guarded_project_write(canonical, write_guard=lambda: None, label="test") as stage,
    ):
        stage.write_bytes(b"staged from old project")
        newer.replace(canonical)  # an unguarded publisher swaps the inode

    assert canonical.read_bytes() == b"newer project"
    assert list(tmp_path.glob(".p.tether.*.test.tmp")) == []


def _stamp_project(path: Path, version: int) -> None:
    with h5py.File(path, "a") as project:
        project.attrs["schema_version"] = version


def test_failed_migration_backup_does_not_publish_a_partial_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tether.project import _guarded_publish

    canonical = tmp_path / "p.tether"
    _stamp_project(canonical, 1)
    original = canonical.read_bytes()
    real_copy = _guarded_publish.copy2

    def fail_backup(source: Path, target: Path) -> None:
        if "backup" in target.name:
            target.write_bytes(b"partial copy")
            raise OSError("backup interrupted")
        real_copy(source, target)

    monkeypatch.setattr(_guarded_publish, "copy2", fail_backup)
    with (
        pytest.raises(OSError, match="backup interrupted"),
        _guarded_publish.guarded_project_write(
            canonical, write_guard=lambda: None, label="test"
        ) as stage,
    ):
        _stamp_project(stage, 2)

    assert canonical.read_bytes() == original
    assert list(tmp_path.iterdir()) == [canonical]


@pytest.mark.parametrize("interruption", ["ownership", "canonical"])
def test_refuses_migration_if_the_source_changes_during_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, interruption: str
) -> None:
    from tether.project import _guarded_publish

    canonical = tmp_path / "p.tether"
    _stamp_project(canonical, 1)
    original = canonical.read_bytes()
    real_copy = _guarded_publish.copy2
    ownership_lost = False
    replacement = tmp_path / "replacement.tmp"
    replacement_bytes = b"another writer's project"
    if interruption == "canonical":
        replacement.write_bytes(replacement_bytes)

    def interrupt_backup(source: Path, target: Path) -> None:
        nonlocal ownership_lost
        real_copy(source, target)
        if "backup" in target.name:
            if interruption == "ownership":
                ownership_lost = True
            else:
                replacement.replace(canonical)

    def guard() -> None:
        if ownership_lost:
            raise lock.LockError("lock ownership changed before publish")

    monkeypatch.setattr(_guarded_publish, "copy2", interrupt_backup)
    with (
        pytest.raises(lock.LockError, match="ownership changed|replaced by another writer"),
        _guarded_publish.guarded_project_write(canonical, write_guard=guard, label="test") as stage,
    ):
        _stamp_project(stage, 2)

    expected = original if interruption == "ownership" else replacement_bytes
    assert canonical.read_bytes() == expected
    assert list(tmp_path.iterdir()) == [canonical]


# --- #258: one staged sibling spans the whole correct stage -------------------


def _count_stage_io(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Count the copies and publishes ``guarded_project_write`` makes from here on."""
    from tether.project import _guarded_publish  # noqa: PLC0415

    counts = {"copies": 0, "publishes": 0}
    real_copy = _guarded_publish.copy2
    real_replace = _guarded_publish.os.replace

    def copy2(src, dst, *args, **kwargs):
        counts["copies"] += 1
        return real_copy(src, dst, *args, **kwargs)

    def replace(src, dst, *args, **kwargs):
        counts["publishes"] += 1
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(_guarded_publish, "copy2", copy2)
    monkeypatch.setattr(_guarded_publish.os, "replace", replace)
    return counts


def _sha256(path: Path) -> str:
    import hashlib  # noqa: PLC0415

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _no_stage_left(path: Path) -> bool:
    return not [p.name for p in path.parent.iterdir() if p.name.endswith(".tmp")]


def test_guarded_correct_stage_copies_once_and_publishes_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    guarded, in_place = _twin(tmp_path, lambda p: _step_cohort_extract(None, p))
    guard = _PassingGuard(guarded, "correct-stage")
    counts = _count_stage_io(monkeypatch)

    detail = run_correct_stage(guarded, write_guard=guard)

    # Before #258 each of leakage, gamma and corrected FRET copied and published the
    # whole project on its own: three copies and three publishes.
    assert counts == {"copies": 1, "publishes": 1}
    assert guard.saw_stage
    assert not guard.stages_left()
    assert _no_stage_left(guarded)
    assert "α=0.100" in detail and "12 corrected" in detail

    assert run_correct_stage(in_place) == detail
    assert [m.decode() for m in _column(guarded, "correction_method")] == [METHOD_CORRECTED] * 12
    _assert_same_columns(guarded, in_place, _CORRECTION_COLUMNS)
    for group in ("leakage", "gamma", "correction"):
        _assert_same_stamp(guarded, in_place, group)


def test_guarded_correct_stage_keeps_every_writer_guard_check(tmp_path: Path) -> None:
    # The single stage must not drop any ownership check a writer makes on its own:
    # every pass still checks before it touches the file and before it persists.
    path = tmp_path / "x.tether"
    _step_cohort_extract(None, path)
    calls: list[None] = []

    run_correct_stage(path, write_guard=lambda: calls.append(None))

    # photobleach 2; leakage, gamma, corrected FRET 2 each in the sibling plus 2
    # nested-stage checks each; the enclosing stage 4 (before and after the
    # copy, and before and after reading the migration stamps).
    assert len(calls) == 18


def test_guarded_correct_stage_failure_publishes_no_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A pass that raises part-way through discards the whole stage: the canonical
    # project is byte-for-byte what it was, photobleach and leakage included. Before
    # #258 the photobleach columns (in place) and the leakage alpha (its own publish)
    # had already reached the canonical file when gamma failed.
    from tether.project import gamma as gamma_module  # noqa: PLC0415

    path = tmp_path / "x.tether"
    _step_cohort_extract(None, path)
    before = _sha256(path)

    def gamma_fails(*_args, **_kwargs):
        raise RuntimeError("gamma pass failed")

    monkeypatch.setattr(gamma_module, "compute_gamma", gamma_fails)
    with pytest.raises(RuntimeError, match="gamma pass failed"):
        run_correct_stage(path, write_guard=lambda: None)

    assert _sha256(path) == before
    assert _no_stage_left(path)


def test_guard_refusal_at_correct_stage_publish_keeps_canonical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tether.project import correct as correct_module  # noqa: PLC0415

    path = tmp_path / "x.tether"
    _step_cohort_extract(None, path)
    before = _sha256(path)
    counts = _count_stage_io(monkeypatch)
    lost = False
    real_corrected = correct_module.compute_corrected_fret

    def corrected_then_lose_ownership(*args, **kwargs):
        nonlocal lost
        result = real_corrected(*args, **kwargs)
        lost = True  # every pass is complete in the sibling; the publish is next
        return result

    def guard() -> None:
        if lost:
            raise lock.LockError("destination ownership changed before publish")

    monkeypatch.setattr(correct_module, "compute_corrected_fret", corrected_then_lose_ownership)
    with pytest.raises(lock.LockError, match="before publish"):
        run_correct_stage(path, write_guard=guard)

    assert counts == {"copies": 1, "publishes": 0}
    assert _sha256(path) == before
    assert _no_stage_left(path)


def test_nested_guarded_write_reuses_the_enclosing_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tether.io.schema import create_project  # noqa: PLC0415
    from tether.project import _guarded_publish  # noqa: PLC0415
    from tether.project._guarded_publish import guarded_project_write  # noqa: PLC0415

    path = create_project(tmp_path / "x.tether")
    other = create_project(tmp_path / "other.tether")
    original = path.read_bytes()
    counts = _count_stage_io(monkeypatch)
    calls: list[None] = []

    def guard() -> None:
        calls.append(None)

    with guarded_project_write(path, write_guard=guard, label="outer") as stage:
        assert stage != path
        with guarded_project_write(stage, write_guard=guard, label="inner") as inner:
            assert inner == stage  # in place: no second copy, no second publish
            with h5py.File(inner, "r+") as f:
                f.attrs["updated"] = True
        # A different project inside the same stage still gets a stage of its own.
        with guarded_project_write(other, write_guard=guard, label="inner") as own:
            assert own not in (other, stage)
        assert path.read_bytes() == original  # nothing published before the outer exit

    with h5py.File(path, "r") as f:
        assert bool(f.attrs["updated"])
    assert counts == {"copies": 2, "publishes": 2}
    assert len(calls) == 4 + 2 + 4
    # Once published, the old sibling path is not treated as staged any more.
    assert _guarded_publish._ACTIVE_STAGES.get() == frozenset()
    assert _no_stage_left(path)


@pytest.mark.parametrize("failure_target", ["canonical", "stage"])
def test_guarded_migration_requires_readable_schema_stamps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_target: str
) -> None:
    from tether.io.schema import create_project
    from tether.project._guarded_publish import guarded_project_write  # noqa: PLC2701

    project = create_project(tmp_path / "migration.tether")
    with h5py.File(project, "r+") as f:
        f.attrs["schema_version"] = 1
    original = project.read_bytes()
    open_file = h5py.File

    with (
        pytest.raises(OSError, match="stamp read failed"),
        guarded_project_write(project, write_guard=lambda: None, label="generation") as stage,
    ):
        with open_file(stage, "r+") as f:
            f.attrs["schema_version"] = 2
        target = project if failure_target == "canonical" else stage

        def fail_stamp(path, mode, *args, **kwargs):
            if mode == "r" and path == target:
                raise OSError("stamp read failed")
            return open_file(path, mode, *args, **kwargs)

        monkeypatch.setattr(h5py, "File", fail_stamp)

    assert project.read_bytes() == original
    assert not list(tmp_path.glob("*backup*"))


def test_guarded_publish_rechecks_ownership_after_same_version_stamp_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tether.io.schema import create_project
    from tether.project._guarded_publish import guarded_project_write  # noqa: PLC2701

    project = create_project(tmp_path / "replaced.tether")
    original = project.read_bytes()
    open_file = h5py.File
    lost_ownership = False

    def guard() -> None:
        if lost_ownership:
            raise lock.LockError("writer lock lost while reading the stamp")

    with (
        pytest.raises(lock.LockError, match="writer lock lost"),
        guarded_project_write(project, write_guard=guard, label="correction") as stage,
    ):
        with open_file(stage, "r+") as f:
            f.attrs["staged_change"] = True

        def note_stamp_read(path, mode, *args, **kwargs):
            nonlocal lost_ownership
            if mode == "r" and path == stage:
                lost_ownership = True
            return open_file(path, mode, *args, **kwargs)

        monkeypatch.setattr(h5py, "File", note_stamp_read)

    assert project.read_bytes() == original
