# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Store-integrated engineered-feature extraction to ``/features`` (PRD §7.5; FR-ML).

The **headless** writer behind the M5 quality ranker's feature layer (PLAN §9 M5):
it reads a project's extracted molecules + traces, reduces each to its
trace-derived :class:`~tether.ml.features.TraceFeatures`
(:func:`tether.ml.features.compute_trace_features`), and writes the result as a
compound ``/features/table`` dataset.

``/features`` is one of the M0-frozen empty **container** groups
(:mod:`tether.io.schema`); a ``table`` dataset written under it is additive
per-record **data**, never a structural change, so the ``schema-guard`` freeze
holds (the guard introspects a fresh :func:`~tether.io.schema.create_project`,
which never contains a ``/features/table`` — ADR-0005). The feature table is a
**derived, recomputable cache** of the traces: :func:`compute_features` recomputes
and replaces it wholesale (``overwrite=True`` by default), so a crashed write
loses only a re-derivable cache, never source data.

Selection contract. Features are a **per-molecule property**, so by default they
are computed for *every* extracted molecule — including rejected ones
(``include_rejected=True``): a reject is the ranker's negative training label, and
its features are that label's input. This deliberately differs from the analysis
population views (:mod:`tether.analysis`), which drop rejected molecules by
default.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from tether.io.schema import TABLE
from tether.ml.features import FEATURE_NAMES, compute_trace_features

if TYPE_CHECKING:
    from os import PathLike

    from tether.project.core import Project

    ProjectRef = Project | str | PathLike[str]

__all__ = [
    "FEATURES_GROUP",
    "StoredFeatures",
    "compute_features",
    "feature_matrix",
    "read_features",
]

#: The frozen §5 container group the feature table is written under.
FEATURES_GROUP = "features"

#: Bumped only when the stored feature layout changes incompatibly. The table is a
#: recomputable cache, so a mismatch is a recompute prompt, not a migration.
FEATURE_SCHEMA_VERSION = 1

#: Feature columns stored with an integer dtype; every other feature is ``<f8``.
_INT_FEATURES = frozenset({"n_frames"})


@dataclass(frozen=True)
class StoredFeatures:
    """The engineered features persisted under ``/features/table``.

    ``matrix`` is the ranker-ready ``(n_molecules, n_features)`` ``float64`` feature
    matrix, column ``j`` = ``feature_names[j]``; row ``i`` corresponds to
    ``molecule_ids[i]`` / ``molecule_keys[i]``. ``molecule_ids`` is the **unique**
    per-row identity (a ``molecule_key`` can name several rows — §7.10 quantized
    coordinate collisions), so it is the correct join key.
    """

    molecule_ids: list[str]
    molecule_keys: list[str]
    feature_names: tuple[str, ...]
    matrix: np.ndarray
    intensity_quantity: str
    app_version: str
    created_utc: str

    @property
    def n_molecules(self) -> int:
        return len(self.molecule_ids)


def _to_str(value: object) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _app_version() -> str:
    from tether import __version__

    return str(__version__)


def _feature_table_dtype() -> np.dtype:
    """The ``/features/table`` compound dtype: id columns + one column per feature."""
    import h5py

    str_dt = h5py.string_dtype(encoding="utf-8")
    fields: list[tuple[str, object]] = [("molecule_id", str_dt), ("molecule_key", str_dt)]
    for name in FEATURE_NAMES:
        fields.append((name, "<i8" if name in _INT_FEATURES else "<f8"))
    return np.dtype(fields)


def _windowed_rows(
    path: Path,
    molecule_keys: list[str] | None,
    intensity_quantity: str,
    include_rejected: bool,
) -> list[tuple[str, str, np.ndarray, np.ndarray]]:
    """Per-selected-molecule ``(molecule_id, molecule_key, donor, acceptor)``.

    The identity-carrying analog of
    :func:`tether.analysis._store.windowed_channels`: reads ``/molecules`` +
    ``/traces`` once, keeps every molecule by default (``include_rejected=True`` —
    features are computed for rejected molecules too), optionally intersects with
    ``molecule_keys``, and slices each kept molecule to its ``analysis_window``
    (falling back to ``frame_range`` when unset ``[0, 0]``, mirroring idealization).
    Returns store order — the ``molecule_id`` travels with the trace so the feature
    row can never be mis-joined.
    """
    from tether.analysis._store import resolve_quantity
    from tether.imaging.extract import read_molecules, read_traces
    from tether.project.labels import curation_filter_mask

    molecules = read_molecules(path)
    if molecules.shape[0] == 0:
        return []
    donor_key, acceptor_key = resolve_quantity(intensity_quantity)
    traces = read_traces(path)
    for key in (donor_key, acceptor_key):
        if key not in traces:
            raise ValueError(
                f"{path.name}/traces has no {key!r} layer "
                f"(intensity_quantity={intensity_quantity!r})"
            )

    keep = curation_filter_mask(molecules, include_rejected=include_rejected)
    if molecule_keys is not None:
        wanted = {str(k) for k in molecule_keys}
        selected = np.array([_to_str(k) in wanted for k in molecules["molecule_key"]], dtype=bool)
        keep = keep & selected
    rows = np.nonzero(keep)[0]

    donor_all = traces[donor_key]
    acceptor_all = traces[acceptor_key]
    analysis_window = molecules["analysis_window"]
    frame_range = molecules["frame_range"]
    mol_ids = molecules["molecule_id"]
    mol_keys = molecules["molecule_key"]

    out: list[tuple[str, str, np.ndarray, np.ndarray]] = []
    for i in rows:
        lo, hi = int(analysis_window[i][0]), int(analysis_window[i][1])
        if hi <= lo:  # unset [0, 0] -> native extent (mirrors idealize._windows)
            lo, hi = int(frame_range[i][0]), int(frame_range[i][1])
        donor = np.asarray(donor_all[i, lo:hi], dtype=np.float64)
        acceptor = np.asarray(acceptor_all[i, lo:hi], dtype=np.float64)
        out.append((_to_str(mol_ids[i]), _to_str(mol_keys[i]), donor, acceptor))
    return out


def compute_features(
    project: ProjectRef,
    *,
    molecule_keys: list[str] | None = None,
    intensity_quantity: str = "corrected",
    include_rejected: bool = True,
    overwrite: bool = True,
) -> StoredFeatures:
    """Compute engineered features for a project's molecules and write ``/features/table``.

    Parameters
    ----------
    project:
        A :class:`~tether.project.core.Project` or a path to a ``.tether`` store.
    molecule_keys:
        The molecules to feature (``None`` = every extracted molecule). A key
        absent from ``/molecules`` is skipped (features are a bulk derived cache,
        not a targeted mutation).
    intensity_quantity:
        Which ``/traces`` layer feeds the features: ``"corrected"`` (default,
        background-subtracted) or ``"raw"``.
    include_rejected:
        Compute features for rejected molecules too (default ``True``): a reject is
        an ML label whose features the ranker still needs.
    overwrite:
        Replace an existing ``/features/table`` (default ``True`` — the table is a
        recomputable cache). ``False`` raises if it already exists.

    Returns
    -------
    StoredFeatures
        The computed features (also readable back via :func:`feature_matrix`).

    Raises
    ------
    ValueError
        The store has no extracted molecules, or lacks the requested trace layer.
    FileExistsError
        ``/features/table`` exists and ``overwrite`` is ``False``.
    """
    from tether.project.core import Project as _Project

    proj = project if isinstance(project, _Project) else _Project.open(project)
    path = proj.path

    rows = _windowed_rows(path, molecule_keys, intensity_quantity, include_rejected)
    if not rows:
        raise ValueError(
            f"{path.name} has no molecules to feature "
            f"(no extracted molecules, or none matched the selection)"
        )

    dtype = _feature_table_dtype()
    table = np.zeros(len(rows), dtype=dtype)
    matrix = np.empty((len(rows), len(FEATURE_NAMES)), dtype=np.float64)
    molecule_ids: list[str] = []
    molecule_keys_out: list[str] = []
    for i, (mol_id, mol_key, donor, acceptor) in enumerate(rows):
        feats = compute_trace_features(donor, acceptor)
        table["molecule_id"][i] = mol_id
        table["molecule_key"][i] = mol_key
        for name in FEATURE_NAMES:
            table[name][i] = getattr(feats, name)
        matrix[i] = feats.as_vector()
        molecule_ids.append(mol_id)
        molecule_keys_out.append(mol_key)

    created_utc = datetime.now(UTC).isoformat()
    app_version = _app_version()
    _write_features_table(
        path,
        table,
        intensity_quantity=intensity_quantity,
        app_version=app_version,
        created_utc=created_utc,
        overwrite=overwrite,
    )
    return StoredFeatures(
        molecule_ids=molecule_ids,
        molecule_keys=molecule_keys_out,
        feature_names=FEATURE_NAMES,
        matrix=matrix,
        intensity_quantity=intensity_quantity,
        app_version=app_version,
        created_utc=created_utc,
    )


def _write_features_table(
    path: Path,
    table: np.ndarray,
    *,
    intensity_quantity: str,
    app_version: str,
    created_utc: str,
    overwrite: bool,
) -> None:
    """Write the compound feature table under ``/features`` as additive data.

    Stamps provenance (app version, UTC time, source trace quantity, the ordered
    feature names + schema version) on the dataset so the cache is self-describing
    and traceable. Replaces any prior table when ``overwrite`` (the recompute path);
    refuses to clobber otherwise.

    Note: the recompute path is ``del`` + ``create_dataset``. HDF5 does not return
    the freed block to the filesystem, so heavy re-derivation slowly grows the
    ``.tether`` (the same characteristic as the ``/idealization`` model writer);
    it is reclaimed by an occasional ``h5repack``, not per-write. Feature recompute
    is an infrequent event (a re-extraction, a correction change, or a retrain
    boundary), so the growth is bounded in practice.
    """
    import h5py

    with h5py.File(path, "r+") as f:
        group = f.require_group(FEATURES_GROUP)
        if TABLE in group:
            if not overwrite:
                raise FileExistsError(
                    f"/features/{TABLE} already exists in {path.name} "
                    "(pass overwrite=True to recompute it)"
                )
            del group[TABLE]
        dataset = group.create_dataset(TABLE, data=table, maxshape=(None,))
        dataset.attrs["app_version"] = app_version
        dataset.attrs["created_utc"] = created_utc
        dataset.attrs["intensity_quantity"] = intensity_quantity
        dataset.attrs["feature_schema_version"] = FEATURE_SCHEMA_VERSION
        dataset.attrs["feature_names"] = json.dumps(list(FEATURE_NAMES))
        dataset.attrs["n_molecules"] = int(table.shape[0])


def read_features(project: ProjectRef) -> np.ndarray:
    """Read ``/features/table`` back as a structured array (a copy, store order).

    Raises :class:`KeyError` if no feature table has been written.
    """
    import h5py

    from tether.project.core import Project as _Project

    path = project.path if isinstance(project, _Project) else Path(project)
    with h5py.File(path, "r") as f:
        if FEATURES_GROUP not in f or TABLE not in f[FEATURES_GROUP]:
            raise KeyError(
                f"no /features/{TABLE} in {Path(path).name} (run compute_features first)"
            )
        return f[FEATURES_GROUP][TABLE][:]


def feature_matrix(project: ProjectRef) -> StoredFeatures:
    """Read the stored features into a :class:`StoredFeatures` (ranker-ready matrix).

    Reconstructs the ``(n_molecules, n_features)`` ``float64`` matrix from the stored
    columns in the table's recorded ``feature_names`` order, so the matrix column
    order is authoritative even if :data:`~tether.ml.features.FEATURE_NAMES` later
    grows (a stale cache is read faithfully, then recomputed on demand).

    Raises :class:`KeyError` if no feature table has been written.
    """
    import h5py

    from tether.project.core import Project as _Project

    path = project.path if isinstance(project, _Project) else Path(project)
    with h5py.File(path, "r") as f:
        if FEATURES_GROUP not in f or TABLE not in f[FEATURES_GROUP]:
            raise KeyError(
                f"no /features/{TABLE} in {Path(path).name} (run compute_features first)"
            )
        dataset = f[FEATURES_GROUP][TABLE]
        table = dataset[:]
        names_attr = dataset.attrs.get("feature_names")
        feature_names = (
            tuple(json.loads(_to_str(names_attr))) if names_attr is not None else FEATURE_NAMES
        )
        intensity_quantity = _to_str(dataset.attrs.get("intensity_quantity", "corrected"))
        app_version = _to_str(dataset.attrs.get("app_version", ""))
        created_utc = _to_str(dataset.attrs.get("created_utc", ""))

    molecule_ids = [_to_str(x) for x in table["molecule_id"]]
    molecule_keys = [_to_str(x) for x in table["molecule_key"]]
    matrix = np.empty((table.shape[0], len(feature_names)), dtype=np.float64)
    for j, name in enumerate(feature_names):
        matrix[:, j] = np.asarray(table[name], dtype=np.float64)
    return StoredFeatures(
        molecule_ids=molecule_ids,
        molecule_keys=molecule_keys,
        feature_names=feature_names,
        matrix=matrix,
        intensity_quantity=intensity_quantity,
        app_version=app_version,
        created_utc=created_utc,
    )
