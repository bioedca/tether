# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Persistent portable quality-ranker artifact (M5, FR-ML; PRD §7.5, UC3).

Locks :mod:`tether.ml.persistence`: the per-condition model round-trips through a standalone
file **faithfully** (a reloaded model scores and ranks identically to the one saved), is
**portable** (independent of any experiment file / directory), warm-start-retrains at a video
boundary on the accumulated label set (carrying provenance forward, never regressing the
ordering), and preserves the **never-auto-drop** permutation through a reload. Malformed or
unsupported artifacts are refused loudly — and an unsupported *format version* is refused
**before** the model member is unpickled. Needs scikit-learn (base lock, #92) -> base CI matrix.
"""

from __future__ import annotations

import json
import operator
import pickle
import warnings
import zipfile

import pytest

pytest.importorskip("numpy")
pytest.importorskip("sklearn")

import numpy as np  # noqa: E402

from tether.ml import persistence  # noqa: E402
from tether.ml.features import FEATURE_NAMES  # noqa: E402
from tether.ml.gbranker import RankerHyperparams  # noqa: E402
from tether.ml.persistence import (  # noqa: E402
    MODEL_FORMAT_VERSION,
    CorruptModelError,
    PortableRankerModel,
    UnsupportedModelFormatError,
    load_model,
    save_model,
    train_portable_model,
    warm_start_retrain,
)
from tether.ml.ranking import precision_at_k  # noqa: E402


class _Exploding:
    """An object whose unpickling triggers a non-``ValueError`` exception (a REDUCE whose callable
    raises ``KeyError``) — used to prove :func:`load_model` refuses such a payload loudly."""

    def __reduce__(self):
        return (operator.getitem, ({}, "missing-key"))


def _repack(src, dst, *, manifest=None, model_bytes=None) -> None:
    """Copy the ``src`` artifact to ``dst``, optionally swapping the manifest and/or model member.

    Lets a test start from a *real* saved model and tamper exactly one member (a mutated manifest
    dict or arbitrary ``model.pkl`` bytes) to exercise a specific rejection path.
    """
    with zipfile.ZipFile(src, "r") as zf:
        orig_manifest = json.loads(zf.read("manifest.json"))
        orig_model = zf.read("model.pkl")
    with zipfile.ZipFile(dst, "w") as zf:
        zf.writestr("manifest.json", json.dumps(orig_manifest if manifest is None else manifest))
        zf.writestr("model.pkl", orig_model if model_bytes is None else model_bytes)


FEATURES = ("f0", "f1", "f2")


def _separable(
    n_per_class: int = 30, seed: int = 0, n_features: int = 3
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """A cleanly separable set: ``n_per_class`` good rows (high), then that many bad (low)."""
    rng = np.random.default_rng(seed)
    good = rng.normal(6.0, 0.5, size=(n_per_class, n_features))
    bad = rng.normal(0.0, 0.5, size=(n_per_class, n_features))
    X = np.vstack([good, bad]).astype(np.float64)
    y = np.array([True] * n_per_class + [False] * n_per_class)
    ids = [f"m{i:03d}" for i in range(2 * n_per_class)]
    return X, y, ids


def _overlapping(
    n_per_class: int, seed: int, sep: float = 1.6, n_features: int = 4
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Two overlapping Gaussian classes — good = mean ``+sep/2``, bad = ``-sep/2``.

    Overlap is what makes a *small* labeled set generalize worse than a larger one, so warm-start
    retraining on more accumulated labels measurably sharpens the ranking.
    """
    rng = np.random.default_rng(seed)
    good = rng.normal(+sep / 2, 1.0, size=(n_per_class, n_features))
    bad = rng.normal(-sep / 2, 1.0, size=(n_per_class, n_features))
    X = np.vstack([good, bad]).astype(np.float64)
    y = np.array([True] * n_per_class + [False] * n_per_class)
    ids = [f"o{seed}_{i:03d}" for i in range(2 * n_per_class)]
    return X, y, ids


def _p_at_k(model: PortableRankerModel, X: np.ndarray, y: np.ndarray, ids: list[str]) -> float:
    """Precision@(#good) of ``model`` ranking the ``(ids, X)`` set against labels ``y``."""
    is_good = {mid: bool(g) for mid, g in zip(ids, y.tolist(), strict=True)}
    ranked = model.rank(ids, X)
    return precision_at_k(ranked.ranked_relevance(is_good), int(np.count_nonzero(y)))


# --- faithful round-trip -----------------------------------------------------


def test_round_trip_scores_and_ranks_identically(tmp_path) -> None:
    X, y, ids = _separable()
    model = train_portable_model(X, y, FEATURES, condition_id="cond-A", video_id="v1")
    path = tmp_path / "cond-A.tethermodel"
    save_model(model, path)

    loaded = load_model(path)
    # A reload is behaviourally identical to the saved model — bit-identical scores + ranking.
    assert np.array_equal(loaded.score(X), model.score(X))
    assert loaded.rank(ids, X).molecule_ids == model.rank(ids, X).molecule_ids
    assert loaded.rank(ids, X).scores == pytest.approx(model.rank(ids, X).scores)


def test_round_trip_preserves_provenance(tmp_path) -> None:
    X, y, _ = _separable()
    hp = RankerHyperparams(max_iter=40, random_state=5)
    model = train_portable_model(
        X, y, FEATURES, condition_id="cond-B", hyperparams=hp, video_id="v7"
    )
    path = tmp_path / "cond-B.tethermodel"
    save_model(model, path)
    loaded = load_model(path)

    assert loaded.condition_id == "cond-B"
    assert loaded.feature_names == FEATURES
    assert loaded.videos_seen == ("v7",)
    assert loaded.n_retrains == 0
    assert loaded.n_train == model.n_train
    assert loaded.n_good == model.n_good
    assert loaded.hyperparams == hp
    assert loaded.created_utc == model.created_utc
    assert loaded.updated_utc == model.updated_utc
    assert loaded.tether_version == model.tether_version
    assert loaded.sklearn_version == model.sklearn_version


def test_portable_across_directories(tmp_path) -> None:
    # "Portable across experiment files": the artifact is independent of any .tether — saving in
    # one directory and loading from another reproduces the model exactly.
    X, y, _ = _separable()
    model = train_portable_model(X, y, FEATURES, condition_id="cond-C")
    src = tmp_path / "experiment-1" / "cond-C.tethermodel"
    save_model(model, src)

    dst = tmp_path / "elsewhere" / "cond-C.tethermodel"
    dst.parent.mkdir(parents=True)
    dst.write_bytes(src.read_bytes())

    assert np.array_equal(load_model(dst).score(X), model.score(X))


def test_works_with_the_real_feature_schema(tmp_path) -> None:
    # End-to-end on the actual /features column order, not just a toy tuple.
    X, y, ids = _separable(30, seed=2, n_features=len(FEATURE_NAMES))
    model = train_portable_model(X, y, FEATURE_NAMES, condition_id="cond-real")
    path = tmp_path / "cond-real.tethermodel"
    save_model(model, path)
    loaded = load_model(path)
    assert loaded.feature_names == FEATURE_NAMES
    assert np.array_equal(loaded.score(X), model.score(X))


# --- never-auto-drop survives a reload ---------------------------------------


def test_never_auto_drop_permutation_survives_reload(tmp_path) -> None:
    X, y, ids = _separable()
    model = train_portable_model(X, y, FEATURES, condition_id="cond-D")
    path = tmp_path / "cond-D.tethermodel"
    save_model(model, path)
    ranked = load_model(path).rank(ids, X)
    assert ranked.n == len(ids)
    assert set(ranked.molecule_ids) == set(ids)
    assert len(set(ranked.molecule_ids)) == len(ids)


# --- warm-start retrain at the video boundary (UC3) --------------------------


def test_warm_start_retrain_advances_provenance() -> None:
    Xa, ya, _ = _overlapping(20, seed=1)
    Xb, yb, _ = _overlapping(20, seed=2)
    model1 = train_portable_model(Xa, ya, ("f0", "f1", "f2", "f3"), condition_id="c", video_id="v1")

    acc_X = np.vstack([Xa, Xb])
    acc_y = np.concatenate([ya, yb])
    model2 = warm_start_retrain(model1, acc_X, acc_y, ("f0", "f1", "f2", "f3"), video_id="v2")

    assert model2.condition_id == "c"  # carried forward
    assert model2.created_utc == model1.created_utc  # first-fit time preserved
    assert model2.n_retrains == 1
    assert model2.videos_seen == ("v1", "v2")
    assert model2.n_train == acc_y.shape[0]
    # The input model is frozen — untouched by the retrain.
    assert model1.n_retrains == 0
    assert model1.videos_seen == ("v1",)


def test_warm_start_retrain_dedups_revisited_video() -> None:
    X, y, _ = _overlapping(20, seed=3)
    m1 = train_portable_model(X, y, ("f0", "f1", "f2", "f3"), condition_id="c", video_id="v1")
    # Re-curating the same video must not double-count it in the learned-from provenance.
    m2 = warm_start_retrain(m1, X, y, ("f0", "f1", "f2", "f3"), video_id="v1")
    assert m2.videos_seen == ("v1",)
    assert m2.n_retrains == 1


def test_warm_start_retrain_lifts_mean_precision_at_k() -> None:
    # UC3: reload → retrain on the accumulated label set → the ordering improves *on average*.
    # Held-out precision@k is noisy per video, so a single-seed `p2 >= p1` is not an invariant of
    # the model (it regresses on a minority of held-out draws by chance). The honest artifact-level
    # claim is that the model retrained on more accumulated labels lifts the *mean* held-out
    # precision@k across many videos — asserted here; the prequential median-across-videos ship
    # gate is its own later PR.
    names = ("f0", "f1", "f2", "f3")
    Xa, ya, _ = _overlapping(5, seed=10)  # video 1: a small (under-fit) labeled batch
    Xb, yb, _ = _overlapping(45, seed=11)  # video 2: many more accumulated labels

    model1 = train_portable_model(Xa, ya, names, condition_id="c", video_id="v1")
    model2 = warm_start_retrain(
        model1, np.vstack([Xa, Xb]), np.concatenate([ya, yb]), names, video_id="v2"
    )

    held_out = [_overlapping(30, seed=s) for s in range(100, 125)]  # 25 held-out videos
    mean_p1 = float(np.mean([_p_at_k(model1, Xh, yh, idh) for Xh, yh, idh in held_out]))
    mean_p2 = float(np.mean([_p_at_k(model2, Xh, yh, idh) for Xh, yh, idh in held_out]))

    assert mean_p2 > mean_p1  # more accumulated labels lift the mean held-out ordering
    assert mean_p2 >= 0.85  # and the retrained model ranks held-out videos well


def test_uc3_loop_round_trips_through_disk(tmp_path) -> None:
    # The full UC3 loop across two videos, persisting between them: train → save → load → retrain
    # → save → load. The final reloaded model matches the in-memory retrained model exactly.
    names = ("f0", "f1", "f2", "f3")
    Xa, ya, _ = _overlapping(20, seed=20)
    Xb, yb, _ = _overlapping(20, seed=21)
    path = tmp_path / "c.tethermodel"

    save_model(train_portable_model(Xa, ya, names, condition_id="c", video_id="v1"), path)
    reloaded = load_model(path)
    retrained = warm_start_retrain(
        reloaded, np.vstack([Xa, Xb]), np.concatenate([ya, yb]), names, video_id="v2"
    )
    save_model(retrained, path)

    final = load_model(path)
    assert final.n_retrains == 1
    assert final.videos_seen == ("v1", "v2")
    assert np.array_equal(final.score(Xb), retrained.score(Xb))


def test_warm_start_rejects_feature_schema_change() -> None:
    X, y, _ = _separable()
    model = train_portable_model(X, y, FEATURES, condition_id="c")
    with pytest.raises(ValueError, match="feature_names must match"):
        warm_start_retrain(model, X, y, ("f0", "f1", "different"))


# --- malformed / unsupported artifacts refused loudly ------------------------


def test_missing_file_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_model("does-not-exist.tethermodel")


def test_not_a_zip_raises_corrupt(tmp_path) -> None:
    path = tmp_path / "junk.tethermodel"
    path.write_bytes(b"this is not a zip file")
    with pytest.raises(CorruptModelError, match="valid zip"):
        load_model(path)


def test_unsupported_format_version_refused_before_unpickle(tmp_path) -> None:
    # A future/unknown format version must be rejected on the manifest alone — the model member is
    # deliberately un-unpicklable garbage, so a raised UnsupportedModelFormatError (not a pickle
    # error) proves the pickle was never touched.
    path = tmp_path / "future.tethermodel"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"format_version": MODEL_FORMAT_VERSION + 1}))
        zf.writestr("model.pkl", b"\x00 not a pickle \x00")
    with pytest.raises(UnsupportedModelFormatError, match="not supported"):
        load_model(path)


def test_missing_manifest_raises_corrupt(tmp_path) -> None:
    path = tmp_path / "nomanifest.tethermodel"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("model.pkl", b"whatever")
    with pytest.raises(CorruptModelError, match="manifest"):
        load_model(path)


def test_bad_json_manifest_raises_corrupt(tmp_path) -> None:
    path = tmp_path / "badjson.tethermodel"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", "{not valid json")
        zf.writestr("model.pkl", b"whatever")
    with pytest.raises(CorruptModelError, match="valid JSON"):
        load_model(path)


def test_missing_format_version_raises_corrupt(tmp_path) -> None:
    path = tmp_path / "noversion.tethermodel"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"condition_id": "c"}))
        zf.writestr("model.pkl", b"whatever")
    with pytest.raises(CorruptModelError, match="format_version"):
        load_model(path)


def test_missing_model_member_raises_corrupt(tmp_path) -> None:
    # A complete, valid manifest (so field validation passes) but no model.pkl member.
    X, y, _ = _separable()
    good = tmp_path / "good.tethermodel"
    save_model(train_portable_model(X, y, FEATURES, condition_id="c"), good)
    with zipfile.ZipFile(good, "r") as zf:
        manifest = zf.read("manifest.json")
    path = tmp_path / "nomodel.tethermodel"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", manifest)
    with pytest.raises(CorruptModelError, match="model.pkl"):
        load_model(path)


def test_manifest_feature_names_disagree_with_pickle_raises(tmp_path) -> None:
    # Tamper: rewrite the manifest's feature_names so they no longer match the pickled ranker.
    # The cross-check must reject the inconsistent artifact.
    X, y, _ = _separable()
    model = train_portable_model(X, y, FEATURES, condition_id="c")
    path = tmp_path / "c.tethermodel"
    save_model(model, path)

    with zipfile.ZipFile(path, "r") as zf:
        manifest = json.loads(zf.read("manifest.json"))
        model_bytes = zf.read("model.pkl")
    manifest["feature_names"] = ["x0", "x1", "x2"]
    tampered = tmp_path / "tampered.tethermodel"
    with zipfile.ZipFile(tampered, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("model.pkl", model_bytes)

    with pytest.raises(CorruptModelError, match="feature_names"):
        load_model(tampered)


def test_truncated_pickle_member_raises_corrupt(tmp_path) -> None:
    # A valid manifest but corrupt model.pkl bytes must be refused loudly (the caught pickle-error
    # path), not escape as a raw pickle exception.
    X, y, _ = _separable()
    good = tmp_path / "good.tethermodel"
    save_model(train_portable_model(X, y, FEATURES, condition_id="c"), good)
    bad = tmp_path / "bad.tethermodel"
    _repack(good, bad, model_bytes=b"\x80\x05 not a valid pickle stream")
    with pytest.raises(CorruptModelError, match="could not be unpickled"):
        load_model(bad)


def test_unpicklable_reduce_payload_raises_corrupt(tmp_path) -> None:
    # A structurally-valid pickle whose reconstruction raises a non-ValueError (here KeyError) must
    # still surface as CorruptModelError, not the bare KeyError — the deserialization trust boundary
    # catches every Exception.
    X, y, _ = _separable()
    good = tmp_path / "good.tethermodel"
    save_model(train_portable_model(X, y, FEATURES, condition_id="c"), good)
    bad = tmp_path / "bad.tethermodel"
    _repack(good, bad, model_bytes=pickle.dumps(_Exploding()))
    with pytest.raises(CorruptModelError, match="could not be unpickled"):
        load_model(bad)


def test_non_ranker_pickle_raises_corrupt(tmp_path) -> None:
    # A valid manifest whose model.pkl unpickles to some other object is rejected by the type guard.
    X, y, _ = _separable()
    good = tmp_path / "good.tethermodel"
    save_model(train_portable_model(X, y, FEATURES, condition_id="c"), good)
    bad = tmp_path / "bad.tethermodel"
    _repack(good, bad, model_bytes=pickle.dumps({"not": "a ranker"}))
    with pytest.raises(CorruptModelError, match="not a QualityRanker"):
        load_model(bad)


def test_wrong_typed_manifest_field_raises_corrupt(tmp_path) -> None:
    # A manifest whose format_version is fine but a provenance field is the wrong type is refused —
    # and, per the security contract, before the model member is unpickled.
    X, y, _ = _separable()
    good = tmp_path / "good.tethermodel"
    save_model(train_portable_model(X, y, FEATURES, condition_id="c"), good)
    with zipfile.ZipFile(good, "r") as zf:
        manifest = json.loads(zf.read("manifest.json"))
    manifest["n_retrains"] = "five"  # int expected
    bad = tmp_path / "bad.tethermodel"
    _repack(good, bad, manifest=manifest)
    with pytest.raises(CorruptModelError, match="wrong type"):
        load_model(bad)


def test_malformed_hyperparams_raise_corrupt(tmp_path) -> None:
    X, y, _ = _separable()
    good = tmp_path / "good.tethermodel"
    save_model(train_portable_model(X, y, FEATURES, condition_id="c"), good)
    with zipfile.ZipFile(good, "r") as zf:
        manifest = json.loads(zf.read("manifest.json"))
    del manifest["hyperparams"]["max_iter"]  # a required hyperparameter key
    bad = tmp_path / "bad.tethermodel"
    _repack(good, bad, manifest=manifest)
    with pytest.raises(CorruptModelError, match="hyperparam"):
        load_model(bad)


# --- warnings surfaced during unpickling are re-emitted, never swallowed ------


def test_sklearn_version_mismatch_warns_but_loads(tmp_path, monkeypatch) -> None:
    # The documented contract: a scikit-learn version mismatch (sklearn's InconsistentVersionWarning
    # during unpickle) is surfaced as a Tether-scoped warning, but the model still loads and works.
    # The warning keys off sklearn's *actual* version check during unpickle (not the manifest's
    # sklearn_version provenance field), so it is exercised by injecting it at pickle.loads.
    from sklearn.exceptions import InconsistentVersionWarning

    X, y, _ = _separable()
    model = train_portable_model(X, y, FEATURES, condition_id="c")
    path = tmp_path / "c.tethermodel"
    save_model(model, path)

    real_loads = pickle.loads

    def _loads_then_warn(data, *args, **kwargs):
        obj = real_loads(data, *args, **kwargs)
        warnings.warn(
            InconsistentVersionWarning(
                estimator_name="QualityRanker",
                current_sklearn_version="99.0",
                original_sklearn_version="1.0",
            ),
            stacklevel=2,
        )
        return obj

    monkeypatch.setattr(persistence.pickle, "loads", _loads_then_warn)
    with pytest.warns(UserWarning, match="different scikit-learn version"):
        loaded = load_model(path)
    assert np.array_equal(loaded.score(X), model.score(X))


def test_other_unpickle_warning_is_resurfaced(tmp_path, monkeypatch) -> None:
    # A non-version warning raised while unpickling (e.g. a DeprecationWarning from an estimator's
    # __setstate__) must not be swallowed by the record=True capture — it is re-emitted as-is.
    X, y, _ = _separable()
    model = train_portable_model(X, y, FEATURES, condition_id="c")
    path = tmp_path / "c.tethermodel"
    save_model(model, path)

    real_loads = pickle.loads

    def _loads_then_warn(data, *args, **kwargs):
        obj = real_loads(data, *args, **kwargs)
        warnings.warn("estimator is deprecated", DeprecationWarning, stacklevel=2)
        return obj

    monkeypatch.setattr(persistence.pickle, "loads", _loads_then_warn)
    with pytest.warns(DeprecationWarning, match="estimator is deprecated"):
        loaded = load_model(path)
    assert np.array_equal(loaded.score(X), model.score(X))


# --- provenance stamping + atomic save ---------------------------------------


def test_train_portable_stamps_provenance() -> None:
    X, y, _ = _separable()
    model = train_portable_model(X, y, FEATURES, condition_id="cond-Z")
    assert model.condition_id == "cond-Z"
    assert model.tether_version  # non-empty
    assert model.sklearn_version
    assert model.videos_seen == ()  # no video_id passed
    assert model.n_retrains == 0
    assert model.created_utc == model.updated_utc


def test_save_overwrites_and_leaves_no_temp_files(tmp_path) -> None:
    X, y, _ = _separable()
    path = tmp_path / "c.tethermodel"
    save_model(train_portable_model(X, y, FEATURES, condition_id="c"), path)
    # Overwriting an existing artifact in place must succeed (atomic os.replace)...
    save_model(train_portable_model(X, y, FEATURES, condition_id="c2"), path)
    assert load_model(path).condition_id == "c2"
    # ...and leave no half-written temp files behind in the directory.
    assert [p.name for p in tmp_path.iterdir()] == ["c.tethermodel"]


def test_save_failure_cleans_up_temp_and_preserves_prior(tmp_path, monkeypatch) -> None:
    # If the write fails after the temp is written but before the atomic rename, the temp is cleaned
    # up and the previously-saved model is left intact (the atomicity contract).
    X, y, _ = _separable()
    path = tmp_path / "c.tethermodel"
    save_model(train_portable_model(X, y, FEATURES, condition_id="prior"), path)

    def _boom(src, dst):
        raise OSError("simulated write failure")

    monkeypatch.setattr(persistence.os, "replace", _boom)
    with pytest.raises(OSError, match="simulated write failure"):
        save_model(train_portable_model(X, y, FEATURES, condition_id="new"), path)

    # No leftover temp file, and the prior model still loads unchanged.
    assert [p.name for p in tmp_path.iterdir()] == ["c.tethermodel"]
    assert load_model(path).condition_id == "prior"


# --- sample_weight passthrough (cold-start label weighting, PRD §7.5) ---------


def test_train_portable_model_forwards_sample_weight() -> None:
    # The cold-start weighting reaches the fit through the portable trainer: None matches the
    # unweighted fit, and non-uniform weights change it.
    X, y, _ = _separable(40, seed=3)
    unweighted = train_portable_model(X, y, FEATURES, condition_id="c").score(X)
    none = train_portable_model(X, y, FEATURES, condition_id="c", sample_weight=None).score(X)
    weighted = train_portable_model(
        X, y, FEATURES, condition_id="c", sample_weight=np.where(y, 100.0, 0.01)
    ).score(X)
    assert np.array_equal(unweighted, none)
    assert not np.allclose(unweighted, weighted)


def test_warm_start_retrain_forwards_sample_weight() -> None:
    X, y, _ = _separable(40, seed=4)
    base = train_portable_model(X, y, FEATURES, condition_id="c", video_id="v1")
    unweighted = warm_start_retrain(base, X, y, FEATURES, video_id="v2").score(X)
    weighted = warm_start_retrain(
        base, X, y, FEATURES, video_id="v2", sample_weight=np.where(y, 100.0, 0.01)
    ).score(X)
    assert not np.allclose(unweighted, weighted)


def test_portable_model_rejects_invalid_sample_weight() -> None:
    X, y, _ = _separable()
    with pytest.raises(ValueError, match="sample_weight"):
        train_portable_model(X, y, FEATURES, condition_id="c", sample_weight=np.ones(len(y) - 1))
