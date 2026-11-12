# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""A condition's ranker retrains on the labels of every project that holds it (#678, ADR-0077).

A condition can span several projects (ADR-0068). These tests pin the four acceptance criteria of
#678 on synthetic stores:

* a model retrained from the second project equals a model trained in one call on the labels of
  both projects, with the same seeds;
* ``n_human`` of the cold-start decay counts the human labels of every project;
* a missing or offline source project stops the retrain with a message that names the file, and the
  saved model does not change;
* no source project is opened for writing, and no lock is taken on one.

Needs scikit-learn (base lock) + h5py -> the base CI matrix.
"""

from __future__ import annotations

import hashlib
import shutil
import stat
from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")
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
from tether.io.schema import create_project  # noqa: E402
from tether.ml.features import FEATURE_NAMES  # noqa: E402
from tether.ml.persistence import (  # noqa: E402
    load_model,
    save_model,
    train_portable_model,
    warm_start_retrain,
)
from tether.ml.weighting import DEFAULT_SEED_WEIGHT  # noqa: E402
from tether.project import condition_model  # noqa: E402
from tether.project.condition_model import (  # noqa: E402
    SourceProjectUnavailableError,
    condition_training_set,
    retrain_condition_model,
    train_condition_model,
)
from tether.project.features import compute_features  # noqa: E402
from tether.project.labels import (  # noqa: E402
    LABEL_SOURCE_CROSS_CONDITION,
    CurationLabel,
    accept,
    reject,
    set_curation_label,
)
from tether.project.lock import lock_path  # noqa: E402
from tether.project.ranking import ranking_dataset  # noqa: E402

_NAME = "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif"
_OTHER_NAME = "Bla_UCKOPSB_T-box_35pM_tRNA_900nM_010.tif"
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
    n = intensity.shape[0]
    background = np.full_like(intensity, 100.0)
    return IntegratedTraces(
        intensity=intensity,
        total=intensity + background,
        background=background,
        valid=np.ones(n, dtype=bool),
    )


def _store(path: Path, *, seed: int, name: str = _NAME, n_per: int = 6) -> list[str]:
    """A featured, unlabeled one-condition store; even rows are good traces, odd rows bad.

    Returns the ``molecule_key`` list in ``/molecules`` order. ``seed`` makes two stores differ.
    """
    rng = np.random.default_rng(seed)
    t, n = 40, 2 * n_per
    donor = np.empty((n, t), dtype=np.float64)
    acceptor = np.empty((n, t), dtype=np.float64)
    for j in range(n_per):
        d = rng.normal(600.0, 8.0, size=t)
        donor[2 * j] = d
        acceptor[2 * j] = 1400.0 - d + rng.normal(0.0, 8.0, size=t)
        donor[2 * j + 1] = rng.normal(250.0, 130.0, size=t)
        acceptor[2 * j + 1] = rng.normal(250.0, 130.0, size=t)
    coords = np.array([[12.0 + 1.7 * i, 14.0 + 2.3 * (i % 7)] for i in range(n)], dtype="float64")
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
        parsed=parse_filename(name),
        registration_map=_reg_map(),
    )
    compute_features(path)
    return [_s(k) for k in read_molecules(path)["molecule_key"]]


def _s(value: object) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _condition(path: Path) -> str:
    return _s(read_molecules(path)["condition_id"][0])


def _label(path: Path, keys: list[str], rows: range | list[int]) -> None:
    """Human-label ``rows``: even rows accept (good), odd rows reject."""
    for i in rows:
        (accept if i % 2 == 0 else reject)(path, keys[i])


def _labelled_rows(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """``(X, y)`` of the human-labelled rows, in stored feature-row order (the test's own read)."""
    data = ranking_dataset(path)
    mask = data.labeled_mask
    return data.X[mask], data.curation_label[mask] == int(CurationLabel.ACCEPT)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def two_days(tmp_path) -> tuple[Path, list[str], Path, list[str]]:
    a, b = tmp_path / "day1.tether", tmp_path / "day2.tether"
    keys_a, keys_b = _store(a, seed=7), _store(b, seed=11)
    assert _condition(a) == _condition(b)  # one condition spans both projects
    _label(a, keys_a, range(6))
    _label(b, keys_b, range(4))
    return a, keys_a, b, keys_b


# --- AC1: a retrain from the second project equals one fit on both ---------------------------


def test_retrain_from_second_project_equals_one_fit_on_both(two_days, tmp_path) -> None:
    a, _, b, _ = two_days
    cid = _condition(a)
    first = train_condition_model(a, condition_id=cid, video_id="v1")
    model_path = tmp_path / "cond.tethermodel"
    save_model(first, model_path)

    retrained = retrain_condition_model(load_model(model_path), b, video_id="v2")

    xa, ya = _labelled_rows(a)
    xb, yb = _labelled_rows(b)
    one_call = train_portable_model(
        np.vstack([xa, xb]), np.concatenate([ya, yb]), FEATURE_NAMES, condition_id=cid
    )
    probe = np.vstack([ranking_dataset(a).X, ranking_dataset(b).X])
    assert np.array_equal(retrained.score(probe), one_call.score(probe))
    assert retrained.n_train == len(ya) + len(yb) == 10
    assert retrained.videos_seen == ("v1", "v2")
    assert [s.path for s in retrained.source_projects] == [str(a), str(b)]
    assert [s.n_rows for s in retrained.source_projects] == [6, 4]

    # Without day 1 the fit is a different model: the retrain did read the older project.
    day2_only = train_portable_model(xb, yb, FEATURE_NAMES, condition_id=cid)
    assert retrained.n_train != day2_only.n_train


def test_a_label_changed_in_an_older_project_reaches_the_next_retrain(two_days) -> None:
    a, keys_a, b, _ = two_days
    model = retrain_condition_model(train_condition_model(a, condition_id=_condition(a)), b)
    before = model.source_projects[0]

    reject(a, keys_a[0])  # the curator changes their mind on day 1
    again = retrain_condition_model(model, b)

    assert again.n_good == model.n_good - 1
    assert again.source_projects[0].rows_sha256 != before.rows_sha256
    assert again.source_projects[1].rows_sha256 == model.source_projects[1].rows_sha256


def test_retrain_from_a_recorded_project_keeps_the_order(two_days) -> None:
    a, _, b, _ = two_days
    model = retrain_condition_model(train_condition_model(a, condition_id=_condition(a)), b)
    again = retrain_condition_model(model, a)  # back on day 1: read b too, no file twice
    assert [s.path for s in again.source_projects] == [str(a), str(b)]
    assert again.n_train == 10


# --- AC2: n_human counts every project --------------------------------------------------------


def test_seed_weight_counts_the_human_labels_of_every_project(two_days) -> None:
    a, _, b, keys_b = two_days
    set_curation_label(b, keys_b[6], CurationLabel.ACCEPT, source=LABEL_SOURCE_CROSS_CONDITION)

    training = condition_training_set([a, b], _condition(a))

    assert training.n_human == 6 + 4
    assert [s.n_human for s in training.sources] == [6, 4]
    seed_rows = training.sample_weight != 1.0
    assert int(np.count_nonzero(seed_rows)) == 1
    assert training.sample_weight[seed_rows][0] == pytest.approx(DEFAULT_SEED_WEIGHT / (1 + 10))
    # Day 2 alone would count only its own four human labels.
    alone = condition_training_set([b], _condition(b))
    assert alone.sample_weight[alone.sample_weight != 1.0][0] == pytest.approx(
        DEFAULT_SEED_WEIGHT / (1 + 4)
    )


def test_retrain_fits_with_the_cross_project_seed_weight(two_days, monkeypatch) -> None:
    a, _, b, keys_b = two_days
    set_curation_label(b, keys_b[6], CurationLabel.ACCEPT, source=LABEL_SOURCE_CROSS_CONDITION)
    model = train_condition_model(a, condition_id=_condition(a))

    seen: dict[str, object] = {}
    real = condition_model.condition_training_set

    def spy(*args, **kwargs):
        seen["set"] = real(*args, **kwargs)
        return seen["set"]

    monkeypatch.setattr(condition_model, "condition_training_set", spy)
    retrained = retrain_condition_model(model, b)

    training = seen["set"]
    one_call = train_portable_model(
        training.X,
        training.y,
        FEATURE_NAMES,
        condition_id=model.condition_id,
        sample_weight=training.sample_weight,
    )
    assert np.array_equal(retrained.score(training.X), one_call.score(training.X))


# --- AC3: a missing or offline project stops the retrain --------------------------------------


def test_missing_source_project_stops_the_retrain_and_names_it(two_days, tmp_path) -> None:
    a, _, b, _ = two_days
    model_path = tmp_path / "cond.tethermodel"
    save_model(train_condition_model(a, condition_id=_condition(a)), model_path)
    saved = _sha(model_path)

    shutil.move(a, tmp_path / "moved.tether")
    with pytest.raises(SourceProjectUnavailableError, match="does not exist") as caught:
        retrain_condition_model(load_model(model_path), b)

    assert caught.value.path == a
    assert str(a) in str(caught.value)
    assert _sha(model_path) == saved  # the saved model did not change


def test_offline_source_project_is_never_opened(two_days, tmp_path, monkeypatch) -> None:
    a, _, b, _ = two_days
    model_path = tmp_path / "cond.tethermodel"
    save_model(train_condition_model(a, condition_id=_condition(a)), model_path)
    saved = _sha(model_path)

    real_attrs = condition_model._file_attributes

    def attrs(path: Path) -> int:
        return stat.FILE_ATTRIBUTE_OFFLINE if Path(path) == a else real_attrs(path)

    opened: list[Path] = []
    real_file = h5py.File

    def spy_file(name, *args, **kwargs):
        opened.append(Path(name))
        return real_file(name, *args, **kwargs)

    monkeypatch.setattr(condition_model, "_file_attributes", attrs)
    monkeypatch.setattr(h5py, "File", spy_file)
    with pytest.raises(SourceProjectUnavailableError, match="offline") as caught:
        retrain_condition_model(load_model(model_path), b)

    assert caught.value.reason == "offline"
    assert str(a) in str(caught.value)
    assert opened == []  # checked before any read: no placeholder is hydrated, no partial set
    assert _sha(model_path) == saved


# --- AC4: read-only, no lock ------------------------------------------------------------------


def test_source_projects_are_read_only_and_never_locked(two_days, monkeypatch) -> None:
    a, _, b, _ = two_days
    model = train_condition_model(a, condition_id=_condition(a))
    before = _sha(a)

    modes: list[tuple[Path, str]] = []
    real_file = h5py.File

    def spy_file(name, mode="r", *args, **kwargs):
        modes.append((Path(name), mode))
        return real_file(name, mode, *args, **kwargs)

    monkeypatch.setattr(h5py, "File", spy_file)
    retrain_condition_model(model, b)

    assert {p for p, _ in modes} == {a, b}
    assert {m for _, m in modes} == {"r"}
    assert not lock_path(a).exists()
    assert _sha(a) == before


# --- scope ------------------------------------------------------------------------------------


def test_only_the_model_condition_is_read(two_days, tmp_path) -> None:
    a, _, _, _ = two_days
    other = tmp_path / "other.tether"
    keys = _store(other, seed=3, name=_OTHER_NAME)
    _label(other, keys, range(4))
    assert _condition(other) != _condition(a)

    model = train_condition_model(a, condition_id=_condition(a))
    with pytest.raises(ValueError, match="holds no molecule of condition"):
        retrain_condition_model(model, other)
    training = condition_training_set([a, other], _condition(a))
    assert training.n_train == 6 and [s.n_rows for s in training.sources] == [6, 0]


def test_a_project_named_twice_is_refused(two_days) -> None:
    a, _, _, _ = two_days
    with pytest.raises(ValueError, match="more than once"):
        condition_training_set([a, a], _condition(a))


def test_a_project_reached_through_a_symlink_is_one_project(two_days, tmp_path) -> None:
    a, _, b, _ = two_days
    alias = tmp_path / "alias.tether"
    try:
        alias.symlink_to(a)
    except OSError:  # pragma: no cover - Windows without the symlink privilege
        pytest.skip("cannot create a symlink here")
    with pytest.raises(ValueError, match="more than once"):
        condition_training_set([a, alias], _condition(a))

    model = retrain_condition_model(train_condition_model(a, condition_id=_condition(a)), b)
    again = retrain_condition_model(model, alias)  # the recorded day 1, by another path
    assert [s.path for s in again.source_projects] == [str(a), str(b)]
    assert again.n_train == 10


def test_a_model_without_a_project_record_is_not_retrained_from_one_project(two_days) -> None:
    a, _, b, _ = two_days
    xa, ya = _labelled_rows(a)
    legacy = train_portable_model(xa, ya, FEATURE_NAMES, condition_id=_condition(a))
    assert legacy.source_projects == ()
    with pytest.raises(ValueError, match="records no source projects"):
        retrain_condition_model(legacy, b)

    # The explicit migration the message names: one fit on the condition's projects.
    training = condition_training_set([a, b], _condition(a))
    migrated = warm_start_retrain(
        legacy, training.X, training.y, training.feature_names, source_projects=training.sources
    )
    assert [s.path for s in migrated.source_projects] == [str(a), str(b)]
    assert retrain_condition_model(migrated, b).n_train == 10


def test_a_refit_on_caller_arrays_drops_the_record(two_days) -> None:
    # A caller refits a recorded model on arrays this library did not read. Those rows may not
    # be the recorded projects' rows, so the record is dropped rather than kept stale: the next
    # project retrain refuses with the migration message instead of leaving labels out.
    a, _, b, _ = two_days
    model = train_condition_model(a, condition_id=_condition(a))
    training = condition_training_set([a, b], _condition(a))
    refit = warm_start_retrain(model, training.X, training.y, training.feature_names)
    assert refit.source_projects == ()
    with pytest.raises(ValueError, match="records no source projects"):
        retrain_condition_model(refit, b)
