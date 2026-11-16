# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The condition model's owner-curator lock and the ``/models`` reference (#561, ADR-0112).

The acceptance criteria of #561, on synthetic data:

* a project records the reference to its condition's model file under ``/models`` (additive only:
  ``SCHEMA_VERSION`` is unchanged and the store stays a compatible project);
* a second writer to the same model file is refused while the first holds the lock, and a stale
  lock is recoverable by an explicit steal;
* two writers on one model file: neither retrain is silently lost.

Needs scikit-learn (base lock) + h5py -> the base CI matrix.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("h5py")
pytest.importorskip("sklearn")

import h5py  # noqa: E402
import numpy as np  # noqa: E402

from tether.imaging.aperture import IntegratedTraces  # noqa: E402
from tether.imaging.calibrate import RegistrationMap  # noqa: E402
from tether.imaging.coloc import ColocalizedMolecules  # noqa: E402
from tether.imaging.extract import (  # noqa: E402
    MoleculeTraces,
    MovieMetadata,
    read_molecules,
    write_extraction,
)
from tether.imaging.register import PolyTransform2D  # noqa: E402
from tether.imaging.split import ChannelGeometry  # noqa: E402
from tether.io.filename import parse_filename  # noqa: E402
from tether.io.schema import (  # noqa: E402
    SCHEMA_VERSION,
    assert_is_compatible_project,
    create_project,
)
from tether.ml.persistence import (  # noqa: E402
    load_model,
    model_version,
    save_model,
    train_portable_model,
    warm_start_retrain,
)
from tether.project import lock  # noqa: E402
from tether.project.condition_model import (  # noqa: E402
    retrain_condition_model,
    train_condition_model,
)
from tether.project.core import Project  # noqa: E402
from tether.project.features import compute_features  # noqa: E402
from tether.project.labels import accept, reject  # noqa: E402
from tether.project.lock import LockedError, LockIdentity  # noqa: E402
from tether.project.model_artifact import (  # noqa: E402
    MODEL_REFERENCE_VERSION,
    ModelArtifactConflictError,
    StaleModelError,
    artifact_sha256,
    model_writer,
    read_model_reference,
    read_model_references,
    record_model_reference,
)

ALICE = LockIdentity(host="lab-a", user="alice", pid=101)
BOB = LockIdentity(host="lab-b", user="bob", pid=202)
_FEATURES = ("f0", "f1", "f2")


def _xy(seed: int, n: int = 40) -> tuple[np.ndarray, np.ndarray]:
    """A separable synthetic training set: ``y`` is ``f0 > 0``."""
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, len(_FEATURES)))
    return X, X[:, 0] > 0.0


def _seed_model(path: Path) -> None:
    """The condition's first model, saved as version 0 (one video seen)."""
    X, y = _xy(1)
    save_model(train_portable_model(X, y, _FEATURES, condition_id="cond-x", video_id="v1"), path)


def _retrain(model, seed: int, video_id: str):
    X, y = _xy(seed)
    return warm_start_retrain(model, X, y, _FEATURES, video_id=video_id)


# --- AC2: a second writer is refused; a stale lock is recovered by a steal -------------------


def test_a_second_writer_is_refused_while_the_first_holds_the_lock(tmp_path) -> None:
    path = tmp_path / "cond.tethermodel"
    _seed_model(path)
    with model_writer(path, identity=ALICE):
        with pytest.raises(LockedError) as info, model_writer(path, identity=BOB):
            pytest.fail("the second writer must not enter the session")
        assert info.value.owner is not None
        assert info.value.owner.identity == ALICE
        assert not info.value.stale
    # Released on exit: the next writer gets in.
    assert lock.read_lock(path) is None
    with model_writer(path, identity=BOB) as writer:
        assert writer.held.identity == BOB


def test_a_second_session_in_the_same_process_is_refused(tmp_path) -> None:
    path = tmp_path / "cond.tethermodel"
    _seed_model(path)
    with model_writer(path), pytest.raises(LockedError), model_writer(path):
        pytest.fail("one identity must not hold two sessions on one model file")


def test_a_stale_lock_is_recovered_only_by_an_explicit_steal(tmp_path) -> None:
    path = tmp_path / "cond.tethermodel"
    _seed_model(path)
    long_ago = datetime.now(UTC) - timedelta(hours=2)
    with model_writer(path, identity=ALICE, now=long_ago) as alice:
        retrained_by_alice = _retrain(alice.load(), seed=2, video_id="v2")

        with pytest.raises(LockedError) as info, model_writer(path, identity=BOB):
            pytest.fail("a stale lock still needs an explicit steal")
        assert info.value.stale

        with model_writer(path, identity=BOB, steal=True) as bob:
            assert bob.ousted is not None
            assert bob.ousted.identity == ALICE
            bob.save(_retrain(bob.load(), seed=3, video_id="v3"))
            saved_by_bob = artifact_sha256(path)

        # Alice's lock was stolen: her save is refused, and Bob's version stays on disk.
        with pytest.raises(LockedError):
            alice.save(retrained_by_alice)
        assert artifact_sha256(path) == saved_by_bob
    assert load_model(path).videos_seen == ("v1", "v3")


def test_holding_refuses_a_lock_that_was_stolen(tmp_path) -> None:
    path = tmp_path / "cond.tethermodel"
    held = lock.acquire(path, identity=ALICE)
    with lock.holding(path, held) as current:
        assert current.nonce == held.nonce
    lock.steal_lock(path, identity=BOB)
    ran = []
    with pytest.raises(LockedError), lock.holding(path, held):
        ran.append(True)
    assert not ran


# --- AC3: two writers on one model file, neither retrain silently lost -----------------------


def test_two_writers_on_one_model_lose_neither_retrain(tmp_path) -> None:
    path = tmp_path / "cond.tethermodel"
    _seed_model(path)

    with model_writer(path, identity=ALICE) as alice:
        model = alice.load()
        # Bob tries while Alice holds the lock: refused, not queued behind her.
        with pytest.raises(LockedError), model_writer(path, identity=BOB):
            pytest.fail("refused")
        alice.save(_retrain(model, seed=2, video_id="v2"))

    with model_writer(path, identity=BOB) as bob:
        bob.save(_retrain(bob.load(), seed=3, video_id="v3"))

    final = load_model(path)
    assert final.videos_seen == ("v1", "v2", "v3")
    assert final.n_retrains == 2


def test_a_retrain_of_a_copy_loaded_before_another_save_is_refused(tmp_path) -> None:
    """The classic lost update: Bob loads, Alice saves, Bob saves his retrain of the old copy."""
    path = tmp_path / "cond.tethermodel"
    _seed_model(path)
    bobs_old_copy = load_model(path)  # read without the lock, before Alice's save

    with model_writer(path, identity=ALICE) as alice:
        alice.save(_retrain(alice.load(), seed=2, video_id="v2"))
    alices = artifact_sha256(path)

    with model_writer(path, identity=BOB) as bob:
        with pytest.raises(StaleModelError, match="retrained from version"):
            bob.save(_retrain(bobs_old_copy, seed=3, video_id="v3"))
        assert artifact_sha256(path) == alices  # Alice's retrain is still on disk
        bob.save(_retrain(bob.load(), seed=3, video_id="v3"))
    assert load_model(path).videos_seen == ("v1", "v2", "v3")


def test_an_old_copy_retrained_more_often_is_still_refused(tmp_path) -> None:
    """Ancestry, not counts: Bob's old copy retrained twice outnumbers Alice's one retrain."""
    path = tmp_path / "cond.tethermodel"
    _seed_model(path)
    bobs_old_copy = load_model(path)

    with model_writer(path, identity=ALICE) as alice:
        alice.save(_retrain(alice.load(), seed=2, video_id="v2"))
    alices = artifact_sha256(path)

    bobs_branch = _retrain(_retrain(bobs_old_copy, seed=3, video_id="v3"), seed=4, video_id="v4")
    assert bobs_branch.n_retrains == 2 > load_model(path).n_retrains == 1
    with (
        model_writer(path, identity=BOB) as bob,
        pytest.raises(StaleModelError, match="retrained from version"),
    ):
        bob.save(bobs_branch)
    assert artifact_sha256(path) == alices


def test_a_reloaded_model_keeps_its_version_and_parent(tmp_path) -> None:
    path = tmp_path / "cond.tethermodel"
    X, y = _xy(1)
    first = train_portable_model(X, y, _FEATURES, condition_id="cond-x", video_id="v1")
    assert first.parent_version is None
    save_model(first, path)
    assert model_version(load_model(path)) == model_version(first)

    second = _retrain(load_model(path), seed=2, video_id="v2")
    assert second.parent_version == model_version(first)
    save_model(second, path)
    reloaded = load_model(path)
    assert reloaded.parent_version == model_version(first)
    assert model_version(reloaded) == model_version(second) != model_version(first)


def test_an_artifact_saved_before_parent_versions_loads_without_one(tmp_path) -> None:
    import json
    import zipfile

    path = tmp_path / "cond.tethermodel"
    _seed_model(path)
    with zipfile.ZipFile(path) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        payload = zf.read("model.pkl")
    del manifest["parent_version"]
    old = tmp_path / "old.tethermodel"
    with zipfile.ZipFile(old, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("model.pkl", payload)
    assert load_model(old).parent_version is None
    # A retrain of the old artifact names it as its parent, so the guarded save accepts it.
    with model_writer(old, identity=ALICE) as alice:
        alice.save(_retrain(alice.load(), seed=2, video_id="v2"))
    assert load_model(old).videos_seen == ("v1", "v2")


def test_a_save_without_the_lock_during_a_session_is_not_overwritten(tmp_path) -> None:
    path = tmp_path / "cond.tethermodel"
    _seed_model(path)
    with model_writer(path, identity=ALICE) as alice:
        retrained = _retrain(alice.load(), seed=2, video_id="v2")
        # Another program saves straight through the unguarded primitive.
        save_model(_retrain(load_model(path), seed=3, video_id="v3"), path)
        theirs = artifact_sha256(path)
        with pytest.raises(ModelArtifactConflictError) as info:
            alice.save(retrained)
        assert info.value.found_sha256 == theirs
        assert artifact_sha256(path) == theirs
    assert load_model(path).videos_seen == ("v1", "v3")


def test_replacing_the_model_with_a_fresh_fit_needs_replace(tmp_path) -> None:
    path = tmp_path / "cond.tethermodel"
    _seed_model(path)
    X, y = _xy(5)
    fresh = train_portable_model(X, y, _FEATURES, condition_id="cond-x", video_id="v9")
    with model_writer(path, identity=ALICE) as alice:
        with pytest.raises(StaleModelError, match="fresh fit"):
            alice.save(fresh)
        alice.save(fresh, replace=True)
    assert load_model(path).videos_seen == ("v9",)


def test_the_first_save_creates_the_model_file(tmp_path) -> None:
    path = tmp_path / "models" / "cond.tethermodel"
    X, y = _xy(1)
    with model_writer(path, identity=ALICE) as alice:
        assert alice.load() is None
        digest = alice.save(train_portable_model(X, y, _FEATURES, condition_id="cond-x"))
    assert digest == artifact_sha256(path)
    assert lock.read_lock(path) is None


def test_a_session_releases_its_lock_when_the_block_raises(tmp_path) -> None:
    path = tmp_path / "cond.tethermodel"
    _seed_model(path)
    with pytest.raises(RuntimeError, match="boom"), model_writer(path, identity=ALICE):
        raise RuntimeError("boom")
    assert lock.read_lock(path) is None


def test_an_ousted_session_does_not_release_its_successors_lock(tmp_path) -> None:
    path = tmp_path / "cond.tethermodel"
    _seed_model(path)
    with model_writer(path, identity=ALICE):
        new, _prior = lock.steal_lock(path, identity=BOB)
    current = lock.read_lock(path)
    assert current is not None
    assert current.nonce == new.nonce


# --- AC1: the /models reference ---------------------------------------------------------------

_NAME = "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif"
_WINDOW = 21


def _reg_map() -> RegistrationMap:
    poly = PolyTransform2D(
        a=np.array([0.0, 1.0, 0.0, 0.0, 0.0, 0.0]),
        b=np.array([0.0, 0.0, 1.0, 0.0, 0.0, 0.0]),
        norm_xy=np.eye(3),
        norm_uv=np.eye(3),
    )
    return RegistrationMap(
        reference_channel=1,
        moving_channel=2,
        ref_to_moving=poly,
        moving_to_ref=poly,
        rms_residual=0.1,
        n_control_points=100,
    )


def _integrated(intensity: np.ndarray) -> IntegratedTraces:
    background = np.full_like(intensity, 100.0)
    return IntegratedTraces(
        intensity=intensity,
        total=intensity + background,
        background=background,
        valid=np.ones(intensity.shape[0], dtype=bool),
    )


def _store(path: Path, *, seed: int, n_per: int = 6) -> list[str]:
    """A featured one-condition store; even rows good, odd rows bad, all human-labelled."""
    rng = np.random.default_rng(seed)
    t, n = 40, 2 * n_per
    donor = np.empty((n, t))
    acceptor = np.empty((n, t))
    for j in range(n_per):
        d = rng.normal(600.0, 8.0, size=t)
        donor[2 * j] = d
        acceptor[2 * j] = 1400.0 - d + rng.normal(0.0, 8.0, size=t)
        donor[2 * j + 1] = rng.normal(250.0, 130.0, size=t)
        acceptor[2 * j + 1] = rng.normal(250.0, 130.0, size=t)
    coords = np.array([[12.0 + 1.7 * i, 14.0 + 2.3 * (i % 7)] for i in range(n)])
    create_project(path, overwrite=True)
    write_extraction(
        path,
        movie=MovieMetadata(
            movie_id="mov-1",
            sha256="a" * 64,
            n_frames=t,
            height=64,
            width=64,
            donor_geometry=ChannelGeometry(crop=(1, 1, 64, 64)),
            acceptor_geometry=ChannelGeometry(crop=(1, 65, 64, 128)),
        ),
        molecules=ColocalizedMolecules(
            donor_xy=coords,
            acceptor_xy=coords,
            acceptor_detected=np.zeros(n, dtype=bool),
            donor_index=np.arange(n, dtype=np.intp),
            acceptor_index=np.full(n, -1, dtype=np.intp),
        ),
        traces=MoleculeTraces(
            donor=_integrated(donor),
            acceptor=_integrated(acceptor),
            donor_patches=np.zeros((n, _WINDOW, _WINDOW), dtype="float32"),
            acceptor_patches=np.zeros((n, _WINDOW, _WINDOW), dtype="float32"),
            window=_WINDOW,
            disk_radius=3.0,
            ring_inner=6.0,
            ring_outer=8.0,
            bg_window=10,
        ),
        parsed=parse_filename(_NAME),
        registration_map=_reg_map(),
    )
    compute_features(path)
    keys = [_s(k) for k in read_molecules(path)["molecule_key"]]
    for i, key in enumerate(keys):
        (accept if i % 2 == 0 else reject)(path, key)
    return keys


def _s(value: object) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _condition(path: Path) -> str:
    return _s(read_molecules(path)["condition_id"][0])


def test_a_save_records_the_reference_under_models(tmp_path) -> None:
    project = tmp_path / "day1.tether"
    _store(project, seed=7)
    cid = _condition(project)
    path = tmp_path / "cond.tethermodel"
    stamp = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)

    assert read_model_references(project) == {}
    with model_writer(path) as writer:
        model = train_condition_model(project, condition_id=cid, video_id="v1")
        digest = writer.save(model, project=project, now=stamp)

    ref = read_model_reference(project, cid)
    assert ref is not None
    assert ref.condition_id == cid
    assert ref.artifact_path == str(path.absolute())
    assert ref.artifact_sha256 == digest == artifact_sha256(path)
    assert ref.n_retrains == 0
    assert ref.model_updated_utc == model.updated_utc
    assert ref.recorded_utc == stamp.isoformat()
    assert ref.reference_version == MODEL_REFERENCE_VERSION
    assert ref.is_current()

    # Additive data in the frozen container group: the store is unchanged in structure.
    assert assert_is_compatible_project(project) == SCHEMA_VERSION == 1
    with h5py.File(project, "r") as f:
        assert list(f["models"]) == [cid]
        assert isinstance(f["models"][cid], h5py.Group)
        assert len(f["models"][cid]) == 0  # attributes only, no datasets
        assert "recorded_by" not in f["models"][cid].attrs  # no user or host name


def test_two_curators_on_two_projects_lose_neither_retrain(tmp_path) -> None:
    """The UC3 loop across two days and two curators, each saving through the lock."""
    day1, day2 = tmp_path / "day1.tether", tmp_path / "day2.tether"
    _store(day1, seed=7)
    _store(day2, seed=11)
    cid = _condition(day1)
    path = tmp_path / "cond.tethermodel"

    with model_writer(path, identity=ALICE) as alice:
        assert alice.load() is None
        alice.save(train_condition_model(day1, condition_id=cid, video_id="v1"), project=day1)
    with model_writer(path, identity=BOB) as bob:
        bob.save(retrain_condition_model(bob.load(), day2, video_id="v2"), project=day2)

    final = load_model(path)
    assert final.videos_seen == ("v1", "v2")
    assert [s.path for s in final.source_projects] == [str(day1), str(day2)]
    # Each project records the version it saved; day1's is now behind the file.
    assert read_model_reference(day2, cid).is_current()
    assert not read_model_reference(day1, cid).is_current()
    assert read_model_reference(day1, cid).n_retrains == 0
    assert read_model_reference(day2, cid).n_retrains == 1


def test_a_later_save_replaces_the_conditions_reference(tmp_path) -> None:
    project = tmp_path / "day1.tether"
    _store(project, seed=7)
    cid = _condition(project)
    path = tmp_path / "cond.tethermodel"
    with model_writer(path) as writer:
        writer.save(train_condition_model(project, condition_id=cid), project=project)
        writer.save(retrain_condition_model(writer.load(), project), project=project)
    refs = read_model_references(project)
    assert list(refs) == [cid]
    assert refs[cid].n_retrains == 1
    assert refs[cid].is_current()


def test_recording_is_refused_while_another_writer_holds_the_project(tmp_path) -> None:
    project = tmp_path / "day1.tether"
    _store(project, seed=7)
    cid = _condition(project)
    path = tmp_path / "cond.tethermodel"
    model = train_condition_model(project, condition_id=cid)
    save_model(model, path)
    lock.acquire(project, identity=BOB)
    with pytest.raises(LockedError):
        record_model_reference(project, model, path)
    assert read_model_references(project) == {}


def test_recording_needs_a_project_that_holds_the_condition(tmp_path) -> None:
    project = tmp_path / "day1.tether"
    _store(project, seed=7)
    path = tmp_path / "cond.tethermodel"
    X, y = _xy(1)
    model = train_portable_model(X, y, _FEATURES, condition_id="cond-elsewhere")
    save_model(model, path)
    with pytest.raises(ValueError, match="no molecule of condition"):
        record_model_reference(project, model, path)
    with pytest.raises(ValueError, match="must not contain"):
        read_model_reference(project, "a/b")
    assert read_model_references(project) == {}


def test_recording_a_missing_model_file_is_an_error(tmp_path) -> None:
    project = tmp_path / "day1.tether"
    _store(project, seed=7)
    model = train_condition_model(project, condition_id=_condition(project))
    with pytest.raises(FileNotFoundError, match="save the model before recording it"):
        record_model_reference(project, model, tmp_path / "nowhere.tethermodel")


def test_a_project_handle_with_its_own_identity_records_its_save(tmp_path) -> None:
    project_path = tmp_path / "day1.tether"
    _store(project_path, seed=7)
    cid = _condition(project_path)
    handle = Project(project_path, identity=ALICE)
    handle.acquire_lock()  # Alice owns the project, under an identity that is not this process's
    path = tmp_path / "cond.tethermodel"
    with model_writer(path, identity=ALICE) as writer:
        writer.save(train_condition_model(project_path, condition_id=cid), project=handle)
    assert read_model_reference(project_path, cid).is_current()


def test_a_save_refused_by_the_project_lock_leaves_the_model_file_alone(tmp_path) -> None:
    project_path = tmp_path / "day1.tether"
    _store(project_path, seed=7)
    cid = _condition(project_path)
    path = tmp_path / "cond.tethermodel"
    with model_writer(path, identity=ALICE) as writer:
        writer.save(train_condition_model(project_path, condition_id=cid), project=project_path)
        before = artifact_sha256(path)
        lock.acquire(project_path, identity=BOB)  # Bob takes the project
        with pytest.raises(LockedError):
            writer.save(retrain_condition_model(writer.load(), project_path), project=project_path)
        assert artifact_sha256(path) == before  # the model file was not replaced
    assert read_model_reference(project_path, cid).artifact_sha256 == before


def test_the_model_lock_sits_beside_the_model_file(tmp_path) -> None:
    path = tmp_path / "cond.tethermodel"
    _seed_model(path)
    with model_writer(path, identity=ALICE):
        assert os.path.exists(tmp_path / "cond.tethermodel.lock")
    assert not os.path.exists(tmp_path / "cond.tethermodel.lock")
