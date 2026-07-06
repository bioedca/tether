# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Persistent portable quality-ranker artifact — save / load / warm-start-retrain (PRD §7.5; FR-ML).

The per-condition quality ranker (:mod:`tether.ml.gbranker`) is required to be **persistent and
portable**: a standalone file that travels with a *condition* (≈100 videos across many days and
experiment files), reloaded and retrained video-by-video, *not* trapped inside one ``.tether``
(PRD §7.5, §5.1 ``/models``, UC3). This module is that artifact — the load / warm-start-retrain /
save primitives — kept Qt-free and **store-free** (pure :mod:`tether.ml`): it moves a fitted
:class:`~tether.ml.gbranker.QualityRanker` plus its provenance to and from a portable file and
refits it at the video boundary. Wiring the artifact to a project's ``/models`` reference and its
own single-writer owner-curator lock (PRD §5.1 ``/models``, §7.10) is the store-integration layer
and lands in a later PR; nothing here touches a ``.tether``.

The UC3 loop (PRD §7.5) is::

    model = load_model(path)                     # reload the condition's model
    #   ... a human curates one video, /labels grows ...
    model = warm_start_retrain(model, X, y, FEATURE_NAMES, video_id=vid)
    save_model(model, path)                      # persist for the next video

On the first video there is no file yet, so the loop opens with
:func:`train_portable_model` instead of :func:`load_model`.

**"Warm-start" here is the loop, not scikit-learn's** ``warm_start``. A histogram
gradient-boosting model has no exact online update on *new data* (sklearn's ``warm_start`` only
appends trees to an *unchanged* training set), so each video boundary honestly **refits on the
accumulated label set** — every human label seen in the condition so far, which is exactly how an
incremental trace selector is expected to sharpen as labels accrue and adapt to a new lab's
preferences [Li2020]. The caller supplies the accumulated ``(X, y)`` (from the project's
``/features`` ⋈ ``/labels``); this module owns only the fit + persistence + provenance, so the
never-fabricate and never-auto-drop disciplines of :mod:`tether.ml.gbranker` carry through
unchanged.

On-disk format (:data:`MODEL_FORMAT_VERSION`). The artifact is a zip container with a **plaintext
``manifest.json``** (format version, tether/scikit-learn versions, condition id, feature-name
order, hyperparameters, and the video/retrain provenance) plus a pickled model member. The
manifest is validated **before** the model member is unpickled, so an artifact of an unknown
format version is rejected without ever executing its pickle — pickle/joblib deserialization runs
arbitrary code, so :func:`load_model` is for **Tether-owned** artifacts only, never an untrusted
file [sklearn-persistence]. The pickle uses protocol 5 (efficient for the model's NumPy arrays).
A scikit-learn version mismatch between the saved and running environments is surfaced as a
warning (scikit-learn's own ``InconsistentVersionWarning``), not a hard failure, so a model still
loads across an in-range dependency bump.

References
----------
[Li2020] Li, Zhang, Johnson-Buck & Walter. "Automatic classification and segmentation of
    single-molecule fluorescence time traces with deep learning." Nature Communications (2020) —
    an engineered/feature-based smFRET trace selector that adapts to new datasets with only modest
    additional training, the incremental-curation premise UC3 rests on.
[sklearn-persistence] scikit-learn, "Model persistence"
    (https://scikit-learn.org/stable/model_persistence.html) — pickle/joblib load executes
    arbitrary code; validate provenance and load only trusted artifacts.
"""

from __future__ import annotations

import json
import os
import pickle
import tempfile
import warnings
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from zipfile import BadZipFile, ZipFile

from tether.ml.gbranker import (
    DEFAULT_HYPERPARAMS,
    QualityRanker,
    RankerHyperparams,
    train_quality_ranker,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from os import PathLike

    from tether.ml.ranking import RankedTraces

    PathRef = str | PathLike[str]

__all__ = [
    "MODEL_FORMAT_VERSION",
    "CorruptModelError",
    "PortableModelError",
    "PortableRankerModel",
    "UnsupportedModelFormatError",
    "load_model",
    "save_model",
    "train_portable_model",
    "warm_start_retrain",
]

#: The on-disk container format version. Bump **only** on a breaking change to the zip layout
#: (member names, manifest keys/types); :func:`load_model` refuses any other version rather than
#: silently misreading it.
MODEL_FORMAT_VERSION = 1

#: Zip member names.
_MANIFEST_NAME = "manifest.json"
_MODEL_NAME = "model.pkl"

#: Pickle protocol 5 — efficient out-of-band handling of the model's NumPy arrays
#: ([sklearn-persistence]); the base-lock Python (>=3.11) always supports it.
_PICKLE_PROTOCOL = 5

_HP_FIELD_NAMES: tuple[str, ...] = tuple(f.name for f in fields(RankerHyperparams))


class PortableModelError(Exception):
    """Base class for portable-model load/parse failures."""


class UnsupportedModelFormatError(PortableModelError):
    """The artifact's :data:`MODEL_FORMAT_VERSION` is not one this build can read."""


class CorruptModelError(PortableModelError):
    """The artifact is not a readable Tether model (bad zip, missing member, malformed manifest)."""


def _now_iso(now: datetime | None) -> str:
    """UTC ISO-8601 timestamp; ``now`` is injectable for reproducible tests."""
    return (now if now is not None else datetime.now(UTC)).isoformat()


def _tether_version() -> str:
    from tether import __version__

    return str(__version__)


def _sklearn_version() -> str:
    import sklearn

    return str(sklearn.__version__)


# ``eq=False`` -> identity equality/hash, mirroring :class:`~tether.ml.gbranker.QualityRanker`:
# this wraps a fitted estimator (no meaningful ``==``) plus provenance, and identity is the right
# contract for a handle-like object.
@dataclass(frozen=True, eq=False)
class PortableRankerModel:
    """A fitted :class:`~tether.ml.gbranker.QualityRanker` plus the provenance that makes it a
    portable per-condition artifact (PRD §7.5).

    Built by :func:`train_portable_model` (fresh) or :func:`warm_start_retrain` (at a video
    boundary); moved to/from disk by :func:`save_model` / :func:`load_model`. :meth:`score` and
    :meth:`rank` delegate to the wrapped ranker so a loaded model is used exactly like a freshly
    trained one — a reload is behaviourally identical to the model that was saved.

    Attributes
    ----------
    ranker:
        The fitted gradient-boosting scorer.
    condition_id:
        The condition the model belongs to (its identity across the ≈100 videos it spans); carried
        forward unchanged by :func:`warm_start_retrain`.
    tether_version, sklearn_version:
        The Tether and scikit-learn versions the model was last fit/saved under (provenance;
        NFR-REPRO). Stamped at construction time from the running environment.
    hyperparams:
        The :class:`~tether.ml.gbranker.RankerHyperparams` the current fit used — the default for
        the next :func:`warm_start_retrain` so the loop is stable.
    videos_seen:
        The distinct ``video_id`` s whose labels have folded into the model, in first-seen order —
        the provenance of *what the model has learned from*. Empty when no ``video_id`` was passed.
    n_retrains:
        How many times the model has been retrained (0 for a fresh :func:`train_portable_model`,
        incremented by each :func:`warm_start_retrain`).
    created_utc, updated_utc:
        ISO-8601 UTC timestamps of the first fit and the most recent (re)fit.
    """

    ranker: QualityRanker
    condition_id: str
    tether_version: str
    sklearn_version: str
    hyperparams: RankerHyperparams
    videos_seen: tuple[str, ...]
    n_retrains: int
    created_utc: str
    updated_utc: str

    @property
    def feature_names(self) -> tuple[str, ...]:
        """The feature-column order the wrapped ranker was trained on."""
        return self.ranker.feature_names

    @property
    def n_train(self) -> int:
        """The number of labeled molecules the current fit saw."""
        return self.ranker.n_train

    @property
    def n_good(self) -> int:
        """How many of the training molecules were accepted (good)."""
        return self.ranker.n_good

    def score(self, X: object) -> object:
        """``P(good)`` per row — delegates to :meth:`tether.ml.gbranker.QualityRanker.score`."""
        return self.ranker.score(X)

    def rank(self, molecule_ids: Sequence[str], X: object) -> RankedTraces:
        """Never-auto-drop ranking — delegates to :meth:`tether.ml.gbranker.QualityRanker.rank`."""
        return self.ranker.rank(molecule_ids, X)


def train_portable_model(
    X: object,
    y: object,
    feature_names: Sequence[str],
    *,
    condition_id: str,
    hyperparams: RankerHyperparams | None = None,
    video_id: str | None = None,
    now: datetime | None = None,
) -> PortableRankerModel:
    """Fit a fresh portable model for a condition — the start of the UC3 loop (PRD §7.5).

    Trains the gradient-boosting ranker (:func:`tether.ml.gbranker.train_quality_ranker`) on the
    condition's first batch of human accept/reject labels and wraps it with provenance so it can be
    saved and reloaded on the next video.

    Parameters
    ----------
    X, y, feature_names:
        As :func:`tether.ml.gbranker.train_quality_ranker` — the labeled feature matrix, the
        boolean accept (good) / reject labels, and the feature-column order.
    condition_id:
        The condition this model belongs to (its identity across videos/files).
    hyperparams:
        Override :data:`tether.ml.gbranker.DEFAULT_HYPERPARAMS`.
    video_id:
        The video whose labels seed this first fit; recorded in ``videos_seen`` when given.
    now:
        Injectable timestamp (defaults to the current UTC time); for reproducible tests.

    Returns
    -------
    PortableRankerModel
        The fitted, provenance-stamped artifact (``n_retrains == 0``).

    Raises
    ------
    ValueError
        Propagated from the ranker fit (empty/one-class labels, shape mismatch).
    """
    hp = hyperparams if hyperparams is not None else DEFAULT_HYPERPARAMS
    ranker = train_quality_ranker(X, y, feature_names, hyperparams=hp)
    stamp = _now_iso(now)
    return PortableRankerModel(
        ranker=ranker,
        condition_id=str(condition_id),
        tether_version=_tether_version(),
        sklearn_version=_sklearn_version(),
        hyperparams=hp,
        videos_seen=() if video_id is None else (str(video_id),),
        n_retrains=0,
        created_utc=stamp,
        updated_utc=stamp,
    )


def warm_start_retrain(
    model: PortableRankerModel,
    X: object,
    y: object,
    feature_names: Sequence[str],
    *,
    video_id: str | None = None,
    hyperparams: RankerHyperparams | None = None,
    now: datetime | None = None,
) -> PortableRankerModel:
    """Retrain the model at a video boundary on the accumulated label set (PRD §7.5, UC3).

    Refits the ranker on the **full accumulated** ``(X, y)`` the caller supplies — every human
    label the condition has seen so far, not just the new video's — and returns a **new**
    :class:`PortableRankerModel` that carries ``condition_id`` and ``created_utc`` forward,
    appends ``video_id`` to ``videos_seen`` (deduplicated, so re-curating a video does not
    double-count it), increments ``n_retrains``, and refreshes ``updated_utc`` and the
    tether/scikit-learn version stamps. The input model is left unchanged (frozen).

    ``feature_names`` must match the model's existing schema: the accumulated set is the same
    features as before, so a differing column order/count is a caller error (a model cannot be
    extended to new features mid-life). ``hyperparams`` defaults to the model's current
    hyperparameters so the loop is stable unless deliberately changed.

    Raises
    ------
    ValueError
        ``feature_names`` differ from ``model.feature_names``; or propagated from the ranker fit
        (empty/one-class labels, shape mismatch).
    """
    names = tuple(str(n) for n in feature_names)
    if names != model.feature_names:
        raise ValueError(
            "feature_names must match the model's existing schema "
            f"{model.feature_names}, got {names}; a model cannot be retrained on a different "
            "feature set"
        )
    hp = hyperparams if hyperparams is not None else model.hyperparams
    ranker = train_quality_ranker(X, y, names, hyperparams=hp)
    seen = model.videos_seen
    if video_id is not None and str(video_id) not in seen:
        seen = (*seen, str(video_id))
    return PortableRankerModel(
        ranker=ranker,
        condition_id=model.condition_id,
        tether_version=_tether_version(),
        sklearn_version=_sklearn_version(),
        hyperparams=hp,
        videos_seen=seen,
        n_retrains=model.n_retrains + 1,
        created_utc=model.created_utc,
        updated_utc=_now_iso(now),
    )


def _manifest(model: PortableRankerModel) -> dict[str, object]:
    """The plaintext, pre-unpickle-validatable provenance record."""
    return {
        "format_version": MODEL_FORMAT_VERSION,
        "tether_version": model.tether_version,
        "sklearn_version": model.sklearn_version,
        "condition_id": model.condition_id,
        "feature_names": list(model.feature_names),
        "n_train": model.n_train,
        "n_good": model.n_good,
        "hyperparams": {name: getattr(model.hyperparams, name) for name in _HP_FIELD_NAMES},
        "videos_seen": list(model.videos_seen),
        "n_retrains": model.n_retrains,
        "created_utc": model.created_utc,
        "updated_utc": model.updated_utc,
    }


def save_model(model: PortableRankerModel, path: PathRef) -> None:
    """Write the portable model to ``path`` atomically (PRD §7.5).

    Writes a zip container (``manifest.json`` + the pickled ranker) to a temporary file in the
    destination directory, then :func:`os.replace` s it into place, so a reader never sees a
    half-written artifact and a crash mid-write leaves the previous model intact. The
    ``.tethermodel`` extension is the convention, but any ``path`` is accepted.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    manifest = json.dumps(_manifest(model), indent=2, sort_keys=True)
    payload = pickle.dumps(model.ranker, protocol=_PICKLE_PROTOCOL)

    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as raw, ZipFile(raw, "w") as zf:
            zf.writestr(_MANIFEST_NAME, manifest)
            zf.writestr(_MODEL_NAME, payload)
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _read_manifest(zf: ZipFile) -> dict[str, object]:
    """Read + structurally validate ``manifest.json`` **before** any unpickling."""
    try:
        raw = zf.read(_MANIFEST_NAME)
    except KeyError as exc:
        raise CorruptModelError("artifact has no manifest.json") from exc
    try:
        manifest = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise CorruptModelError("manifest.json is not valid JSON") from exc
    if not isinstance(manifest, dict):
        raise CorruptModelError("manifest.json is not a JSON object")

    version = manifest.get("format_version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise CorruptModelError("manifest.json has no integer format_version")
    if version != MODEL_FORMAT_VERSION:
        raise UnsupportedModelFormatError(
            f"model format version {version} is not supported "
            f"(this build reads version {MODEL_FORMAT_VERSION})"
        )
    return manifest


def _require(manifest: dict[str, object], key: str, typ: type | tuple[type, ...]) -> object:
    value = manifest.get(key)
    # ``bool`` is an ``int`` subclass; reject it where an int/str field is expected.
    if not isinstance(value, typ) or (typ is not bool and isinstance(value, bool)):
        raise CorruptModelError(f"manifest.json field {key!r} is missing or the wrong type")
    return value


def load_model(path: PathRef) -> PortableRankerModel:
    """Load a portable model saved by :func:`save_model` (PRD §7.5).

    Validates the plaintext manifest — a supported :data:`MODEL_FORMAT_VERSION` and well-typed
    provenance fields — **before** unpickling the model member, then reconstructs the
    :class:`PortableRankerModel`. The loaded model scores and ranks **identically** to the one
    saved (a faithful round-trip).

    Security: unpickling executes arbitrary code, so this is for **Tether-owned** artifacts only,
    never an untrusted file ([sklearn-persistence]). A scikit-learn version mismatch is surfaced as
    a warning, not an error, so a model survives an in-range dependency bump.

    Raises
    ------
    FileNotFoundError
        ``path`` does not exist.
    UnsupportedModelFormatError
        The manifest's ``format_version`` is not readable by this build.
    CorruptModelError
        ``path`` is not a readable Tether model (bad zip, missing member, malformed manifest, or a
        manifest whose feature-name schema disagrees with the pickled ranker).
    """
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(f"no portable model at {target}")

    try:
        with ZipFile(target, "r") as zf:
            manifest = _read_manifest(zf)
            # Validate *every* provenance field before touching the pickle — the manifest is the
            # trust gate, so a malformed one is rejected without executing model.pkl. Only the
            # manifest<->ranker feature-name cross-check (below) needs the loaded ranker.
            fields = _validated_fields(manifest)
            try:
                payload = zf.read(_MODEL_NAME)
            except KeyError as exc:
                raise CorruptModelError("artifact has no model.pkl") from exc
    except BadZipFile as exc:
        raise CorruptModelError(f"{target} is not a valid zip container") from exc

    # ``feature_names`` is a property of the ranker, not a constructor field — validated from the
    # manifest (pre-unpickle) purely to cross-check it against the pickled ranker.
    expected_feature_names = tuple(str(n) for n in _require(manifest, "feature_names", list))
    ranker = _unpickle_ranker(payload)
    if tuple(ranker.feature_names) != expected_feature_names:
        raise CorruptModelError(
            "manifest feature_names disagree with the pickled ranker; artifact is inconsistent"
        )
    return PortableRankerModel(ranker=ranker, **fields)


def _validated_fields(manifest: dict[str, object]) -> dict[str, object]:
    """Type-check and coerce every :class:`PortableRankerModel` constructor field except
    ``ranker``.

    Runs entirely on the plaintext manifest so it can be called **before** the model member is
    unpickled; a missing or wrong-typed field raises :class:`CorruptModelError`. ``feature_names``
    is excluded — it is a property of the ranker, cross-checked separately in :func:`load_model`.
    """
    return {
        "condition_id": str(_require(manifest, "condition_id", str)),
        "tether_version": str(_require(manifest, "tether_version", str)),
        "sklearn_version": str(_require(manifest, "sklearn_version", str)),
        "hyperparams": _hyperparams_from_manifest(_require(manifest, "hyperparams", dict)),
        "videos_seen": tuple(str(v) for v in _require(manifest, "videos_seen", list)),
        "n_retrains": int(_require(manifest, "n_retrains", int)),
        "created_utc": str(_require(manifest, "created_utc", str)),
        "updated_utc": str(_require(manifest, "updated_utc", str)),
    }


def _unpickle_ranker(payload: bytes) -> QualityRanker:
    """Unpickle the model member, re-surfacing a scikit-learn version mismatch as a warning.

    scikit-learn raises :class:`~sklearn.exceptions.InconsistentVersionWarning` when an estimator
    is unpickled under a different scikit-learn version ([sklearn-persistence]); we let the model
    load but re-emit a Tether-scoped warning so the mismatch is visible, not silent.
    """
    try:
        from sklearn.exceptions import InconsistentVersionWarning
    except ImportError:  # pragma: no cover - scikit-learn is a base-lock dependency
        InconsistentVersionWarning = ()  # type: ignore[assignment]

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            ranker = pickle.loads(payload)
        except Exception as exc:
            # A deserialization trust boundary: a corrupt/crafted stream can raise essentially any
            # exception (a REDUCE opcode whose callable throws surfaces KeyError/TypeError/etc., a
            # deep stream RecursionError), so ``Exception`` is the correct scope — refuse loudly as
            # CorruptModelError. ``BaseException`` (KeyboardInterrupt/SystemExit) still propagates.
            raise CorruptModelError("model.pkl could not be unpickled") from exc

    if not isinstance(ranker, QualityRanker):
        raise CorruptModelError(f"model.pkl is a {type(ranker).__name__}, not a QualityRanker")

    if InconsistentVersionWarning:
        for record in caught:
            if issubclass(record.category, InconsistentVersionWarning):
                warnings.warn(
                    "portable model was saved with a different scikit-learn version "
                    f"({record.message}); loaded anyway",
                    stacklevel=2,
                )
    return ranker


def _hyperparams_from_manifest(raw: dict[str, object]) -> RankerHyperparams:
    """Rebuild :class:`RankerHyperparams` from the manifest's hyperparameter mapping."""
    try:
        return RankerHyperparams(**{name: raw[name] for name in _HP_FIELD_NAMES})
    except (KeyError, TypeError) as exc:
        raise CorruptModelError("manifest hyperparameters are missing or malformed") from exc
