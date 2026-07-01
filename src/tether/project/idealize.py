# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Store-integrated one-click idealization (PRD §5, §7.4; FR-IDEALIZE; ADR-0024).

This is the **headless** half of the M2 "one-click vbFRET from the dock" feature
(PLAN §6 S6): it turns a selection of extracted molecules in a ``.tether`` store
into a fitted idealization written back under the frozen ``/idealization`` group.
The GUI (``I`` key + Viterbi step overlay) is a thin layer over :func:`idealize_molecules`
and lands separately.

The pipeline is::

    read /molecules + /traces  ->  build an SMD over the selected molecules
      ->  run headless vbFRET / consensus VB-HMM via the isolated sidecar
      ->  (optional) pick the state count by maximum ELBO  [Bronson2009]
      ->  write /idealization/{model} with a per-molecule input-provenance hash

Two design points are load-bearing and homed in ADR-0024:

* **``/idealization/{model}`` is additive data.** The container group ``/idealization``
  is part of the M0-frozen §5 skeleton (:mod:`tether.io.schema`); a *model* subgroup
  written here is per-record data, never a structural change, so ``schema-guard``
  stays green (the guard introspects an empty :func:`~tether.io.schema.create_project`,
  which never contains a model subgroup).
* **Per-molecule input-provenance hash (staleness).** PRD §5 requires each model be
  "stamped with a per-molecule provenance hash of the inputs". Each molecule's hash
  (:func:`input_trace_hash`) covers the exact intensity values fed to the fit over
  its analysis window; if a re-extraction or a later correction changes the trace,
  the recomputed hash diverges and :func:`stale_molecule_keys` flags the model as
  stale — the idealization is never silently trusted against changed inputs.

**Auto state-count.** With ``nstates=None`` the fit is repeated over ``nstates_grid``
and the model with the largest ELBO (the variational evidence lower bound) is kept —
the standard, statistically-consistent state-count selection for VB-HMM idealization
of smFRET traces [Bronson2009] (max-evidence beats max-likelihood; ELBO-maximization
carries theoretical guarantees [CheriefAbdellatif2018]). Pass an explicit ``nstates``
to fix it (a per-trace manual override the GUI exposes).

At the MVP the idealization input is the **background-subtracted** donor/acceptor
intensity (the ``corrected`` trace quantity = disk sum − local background; the M1
meaning — photophysical α/γ corrections are M3), so the states are apparent-E states
consistent with :func:`tether.fret.apparent_fret`.

References
----------
[Bronson2009] Bronson, Fei, Hofman, Gonzalez & Wiggins. "Learning rates and states
    from biophysical time series: a Bayesian approach to model selection and
    single-molecule FRET data." Biophysical Journal (2009) — vbFRET; max-evidence
    (ELBO) model selection of the number of states.
[CheriefAbdellatif2018] Chérief-Abdellatif. "Consistency of ELBO maximization for
    model selection." (2018) — theoretical guarantees for ELBO-based selection.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from shutil import rmtree
from tempfile import mkdtemp
from typing import TYPE_CHECKING

import numpy as np

from tether.idealize.driver import (
    IdealizationResult,
    run_vbfret,
    states_from_idealized,
)
from tether.idealize.smd import write_smd
from tether.imaging.extract import read_molecules, read_traces

if TYPE_CHECKING:
    from os import PathLike

    from tether.project.core import Project

__all__ = [
    "IDEALIZATION_GROUP",
    "MODEL_TYPE_DEFAULT",
    "NSTATES_GRID_DEFAULT",
    "StoredIdealization",
    "idealize_molecules",
    "input_trace_hash",
    "list_idealizations",
    "read_idealization",
    "stale_molecule_keys",
]

#: The frozen §5 container group model subgroups are written under.
IDEALIZATION_GROUP = "idealization"
#: Default idealization method (the consensus VB-HMM behind the M0.5 parity target).
MODEL_TYPE_DEFAULT = "vbconhmm"
#: State counts swept for auto (max-ELBO) model selection when ``nstates`` is None
#: (PRD §11.2 idealization tunable; the vbFRET-family model-selection range).
NSTATES_GRID_DEFAULT: tuple[int, ...] = (1, 2, 3, 4)

#: The ``/traces`` channel/quantity the fit consumes, by ``intensity_quantity`` key.
#: ``corrected`` = background-subtracted disk intensity (the apparent-E input at M2).
_QUANTITY_KEYS = {
    "corrected": ("donor_corrected", "acceptor_corrected"),
    "raw": ("donor_raw", "acceptor_raw"),
}


@dataclass(frozen=True)
class StoredIdealization:
    """One idealization model persisted under ``/idealization/{model_name}``.

    ``idealized`` is ``(n_molecules, n_frames)`` float FRET level (NaN outside each
    molecule's analysis window); ``state_paths`` is the matching int64 state index
    (:data:`tether.idealize.NO_STATE` outside the window). Row ``i`` of every
    per-molecule array corresponds to ``molecule_keys[i]`` / ``molecule_ids[i]`` /
    ``input_hashes[i]``. ``molecule_ids`` is the **unique** per-row identity (the
    ``molecule_key`` is *not* unique — §7.10 quantized-coordinate collisions), so it
    is the correct staleness join key.
    """

    model_name: str
    model_type: str
    nstates: int
    means: np.ndarray
    variances: np.ndarray | None
    tmatrix: np.ndarray | None
    elbo: float | None
    idealized: np.ndarray
    state_paths: np.ndarray
    molecule_keys: list[str]
    molecule_ids: list[str]
    input_hashes: list[str]
    intensity_quantity: str
    nstates_selected_by: str  # "max-elbo" | "fixed"
    elbo_by_nstates: dict[int, float] | None
    app_version: str
    created_utc: str

    @property
    def n_molecules(self) -> int:
        return len(self.molecule_keys)


def input_trace_hash(donor: np.ndarray, acceptor: np.ndarray, quantity: str) -> str:
    """SHA-256 of one molecule's exact idealization input over its analysis window.

    ``donor``/``acceptor`` are the *windowed* 1-D intensity slices actually fed to
    the fit (already trimmed to ``[pre, post)``). The quantity name is folded in so
    the same numbers read from a different trace layer hash differently. Inputs are
    cast to C-contiguous ``float64`` first, so the digest is stable regardless of the
    on-disk float width (``/traces`` is ``float32``). This is the staleness stamp of
    PRD §5: a re-extraction/correction that changes the trace changes the hash.
    """
    d = np.ascontiguousarray(np.asarray(donor, dtype="float64"))
    a = np.ascontiguousarray(np.asarray(acceptor, dtype="float64"))
    h = hashlib.sha256()
    h.update(f"{quantity}|{d.shape[0]}|".encode())
    h.update(d.tobytes())
    h.update(a.tobytes())
    return h.hexdigest()


def _resolve_quantity(quantity: str) -> tuple[str, str]:
    try:
        return _QUANTITY_KEYS[quantity]
    except KeyError:
        raise ValueError(
            f"intensity_quantity must be one of {sorted(_QUANTITY_KEYS)}, got {quantity!r}"
        ) from None


def _to_str(value: object) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _select_rows(molecules: np.ndarray, molecule_keys: list[str] | None) -> list[int]:
    """Row indices (store order) of the molecules to idealize.

    ``None`` selects every extracted molecule. A requested key absent from
    ``/molecules`` is an error (never a silent drop). A key that maps to multiple
    rows (the §7.10 duplicate-``molecule_key`` case) selects all of them, preserving
    store order.
    """
    keys = [_to_str(k) for k in molecules["molecule_key"]]
    if molecule_keys is None:
        return list(range(len(keys)))
    wanted = list(dict.fromkeys(molecule_keys))  # de-dup, keep caller order
    by_key: dict[str, list[int]] = {}
    for i, k in enumerate(keys):
        by_key.setdefault(k, []).append(i)
    missing = [k for k in wanted if k not in by_key]
    if missing:
        raise KeyError(f"no molecule with molecule_key(s) {missing} in the store")
    rows: list[int] = []
    for k in wanted:
        rows.extend(by_key[k])
    return rows


def _windows(molecules: np.ndarray, rows: list[int]) -> tuple[np.ndarray, np.ndarray]:
    """Per-selected-molecule analysis window ``(pre, post)`` (SMD half-open bounds).

    Uses the editable ``analysis_window`` (defaults to the native ``frame_range`` at
    extraction) so the fit idealizes exactly the frames the curator kept. Falls back
    to ``frame_range`` for a row whose window was never set (a zero ``[0, 0]``).
    """
    aw = molecules["analysis_window"]
    fr = molecules["frame_range"]
    pre = np.empty(len(rows), dtype="int64")
    post = np.empty(len(rows), dtype="int64")
    for j, i in enumerate(rows):
        lo, hi = int(aw[i][0]), int(aw[i][1])
        if hi <= lo:  # window unset -> the molecule's native extent
            lo, hi = int(fr[i][0]), int(fr[i][1])
        pre[j], post[j] = lo, hi
    return pre, post


def _select_by_elbo(
    smd_path: Path,
    *,
    nstates_grid: tuple[int, ...],
    runner: Callable[..., IdealizationResult],
    run_kwargs: dict,
) -> tuple[IdealizationResult, int, dict[int, float]]:
    """Fit at each ``nstates`` in the grid and keep the max-ELBO model [Bronson2009].

    Returns ``(winning_result, chosen_nstates, elbo_by_nstates)``. A fit whose model
    reports a **non-finite** ELBO (``None``, ``NaN`` or ``inf`` — a degenerate/failed
    fit) is scored ``-inf`` so it never wins over a comparable finite fit; a raw NaN
    must never reach :func:`max` (NaN comparisons are always ``False``, which would
    make the winner order-dependent and could select the degenerate model). Ties in
    ELBO break toward the **smallest** state count (parsimony), so an all-``-inf``
    grid deterministically keeps the simplest model.
    """
    if not nstates_grid:
        raise ValueError("nstates_grid must be non-empty for auto state-count selection")
    results: dict[int, IdealizationResult] = {}
    elbo_by_nstates: dict[int, float] = {}
    for k in nstates_grid:
        res = runner(smd_path, nstates=int(k), **run_kwargs)
        results[int(k)] = res
        elbo = res.model.elbo
        elbo_by_nstates[int(k)] = (
            float(elbo) if elbo is not None and np.isfinite(elbo) else float("-inf")
        )
    # Highest ELBO wins; ties (incl. an all--inf grid) break toward the smallest k.
    chosen = max(elbo_by_nstates, key=lambda k: (elbo_by_nstates[k], -k))
    return results[chosen], chosen, elbo_by_nstates


def idealize_molecules(
    project: Project | str | PathLike[str],
    molecule_keys: list[str] | None = None,
    *,
    model_type: str = MODEL_TYPE_DEFAULT,
    nstates: int | None = None,
    nstates_grid: tuple[int, ...] = NSTATES_GRID_DEFAULT,
    model_name: str | None = None,
    intensity_quantity: str = "corrected",
    sidecar_python: str | PathLike[str] | None = None,
    nrestarts: int | None = None,
    scratch_dir: str | PathLike[str] | None = None,
    timeout: float | None = 1800.0,
    overwrite: bool = False,
    _runner: Callable[..., IdealizationResult] = run_vbfret,
) -> StoredIdealization:
    """Idealize selected molecules and write ``/idealization/{model_name}``.

    Parameters
    ----------
    project:
        A :class:`~tether.project.core.Project` or a path to a ``.tether`` store.
    molecule_keys:
        The molecules to idealize (``None`` = every extracted molecule). Order is
        store order; a duplicate ``molecule_key`` (§7.10) idealizes each matching row.
    model_type:
        A sidecar model key (default ``"vbconhmm"``; see
        :func:`tether.idealize.run_vbfret`).
    nstates:
        Fixed state count. ``None`` (default) selects it by maximum ELBO over
        ``nstates_grid`` [Bronson2009].
    nstates_grid:
        State counts swept when ``nstates is None``.
    model_name:
        Subgroup name under ``/idealization`` (default: ``model_type``). Refuses to
        clobber an existing model unless ``overwrite=True``.
    intensity_quantity:
        Which ``/traces`` layer feeds the fit: ``"corrected"`` (default,
        background-subtracted — the apparent-E input) or ``"raw"``.
    sidecar_python, nrestarts, scratch_dir, timeout:
        Forwarded to :func:`tether.idealize.run_vbfret`.
    overwrite:
        Replace an existing ``/idealization/{model_name}``.

    Returns
    -------
    StoredIdealization
        The persisted model (also readable back via :func:`read_idealization`).

    Raises
    ------
    ValueError
        Empty selection, unknown ``intensity_quantity``, or a store without the
        requested trace layer.
    KeyError
        A requested ``molecule_key`` absent from ``/molecules``.
    FileExistsError
        ``/idealization/{model_name}`` exists and ``overwrite`` is False.
    """
    from tether.project.core import Project as _Project

    proj = project if isinstance(project, _Project) else _Project.open(project)
    path = proj.path
    donor_key, acceptor_key = _resolve_quantity(intensity_quantity)
    model_name = model_name or model_type

    molecules = read_molecules(path)
    if molecules.shape[0] == 0:
        raise ValueError(f"{path.name} has no extracted molecules to idealize")
    traces = read_traces(path)
    for key in (donor_key, acceptor_key):
        if key not in traces:
            raise ValueError(
                f"{path.name}/traces has no {key!r} layer "
                f"(intensity_quantity={intensity_quantity!r})"
            )

    rows = _select_rows(molecules, molecule_keys)
    if not rows:
        raise ValueError("no molecules selected to idealize")
    _refuse_existing(path, model_name, overwrite)

    donor = np.asarray(traces[donor_key], dtype="float64")[rows]
    acceptor = np.asarray(traces[acceptor_key], dtype="float64")[rows]
    pre, post = _windows(molecules, rows)
    sel_keys = [_to_str(molecules["molecule_key"][i]) for i in rows]
    sel_ids = [_to_str(molecules["molecule_id"][i]) for i in rows]

    # Per-molecule input-provenance hash over the exact windowed input (staleness).
    input_hashes = [
        input_trace_hash(
            donor[j, pre[j] : post[j]], acceptor[j, pre[j] : post[j]], intensity_quantity
        )
        for j in range(len(rows))
    ]

    raw = np.stack([donor, acceptor], axis=-1)  # (n_sel, n_frames, 2)
    # The SMD + per-fit model files are transient. Default to a temp dir cleaned on
    # exit so nothing is left beside the user's project (auto mode writes one model
    # file per grid entry); an explicit scratch_dir is the caller's to own/keep.
    owns_scratch = scratch_dir is None
    scratch = (
        Path(scratch_dir) if scratch_dir is not None else Path(mkdtemp(prefix="tether-idealize-"))
    )
    try:
        scratch.mkdir(parents=True, exist_ok=True)
        smd_path = scratch / f"{path.stem}.{model_name}.idealize-in.hdf5"
        write_smd(
            smd_path,
            raw,
            source_names=[path.name],
            pre_list=pre,
            post_list=post,
            molecule_keys=sel_keys,
            molecule_ids=sel_ids,
            overwrite=True,
        )

        run_kwargs = {
            "model_type": model_type,
            "sidecar_python": sidecar_python,
            "nrestarts": nrestarts,
            "timeout": timeout,
        }
        if nstates is None:
            result, chosen_nstates, elbo_by_nstates = _select_by_elbo(
                smd_path,
                nstates_grid=tuple(int(k) for k in nstates_grid),
                runner=_runner,
                run_kwargs=run_kwargs,
            )
            selected_by = "max-elbo"
        else:
            result = _runner(smd_path, nstates=int(nstates), **run_kwargs)
            chosen_nstates = int(nstates)
            elbo_by_nstates = None
            selected_by = "fixed"

        return _write_model(
            path,
            result=result,
            model_name=model_name,
            model_type=model_type,
            nstates=chosen_nstates,
            molecule_keys=sel_keys,
            molecule_ids=sel_ids,
            input_hashes=input_hashes,
            intensity_quantity=intensity_quantity,
            selected_by=selected_by,
            elbo_by_nstates=elbo_by_nstates,
            overwrite=overwrite,
        )
    finally:
        if owns_scratch:
            rmtree(scratch, ignore_errors=True)


def _refuse_existing(path: Path, model_name: str, overwrite: bool) -> None:
    """Raise before any sidecar work if the model exists and overwrite is False."""
    import h5py

    with h5py.File(path, "r") as f:
        exists = IDEALIZATION_GROUP in f and model_name in f[IDEALIZATION_GROUP]
    if exists and not overwrite:
        raise FileExistsError(
            f"/idealization/{model_name} already exists in {path.name} "
            "(pass overwrite=True to replace it)"
        )


def _idealized_and_paths(result: IdealizationResult) -> tuple[np.ndarray, np.ndarray]:
    """The ``(n_sel, n_frames)`` float idealized levels + int64 state paths.

    The SMD was built from exactly the selected molecules in order, so the model's
    ``idealized`` rows already align with the selection. A model **without** an
    ``idealized`` array (a degenerate/failed fit) is refused rather than persisted:
    writing it would leave a ``(0, 0)`` idealized/state_path beside length-``n_sel``
    key/id/hash datasets — a misaligned, useless model. Failing loud is safer.
    """
    idealized = result.model.idealized
    means = result.model.means
    if idealized is None or means.size == 0:
        raise ValueError(
            "idealization produced no state path (degenerate/failed fit): the model "
            "has no 'idealized' array; refusing to persist an empty model"
        )
    idealized = np.asarray(idealized, dtype="float64")
    paths = states_from_idealized(idealized, means)
    return idealized, paths


def _write_model(
    path: Path,
    *,
    result: IdealizationResult,
    model_name: str,
    model_type: str,
    nstates: int,
    molecule_keys: list[str],
    molecule_ids: list[str],
    input_hashes: list[str],
    intensity_quantity: str,
    selected_by: str,
    elbo_by_nstates: dict[int, float] | None,
    overwrite: bool,
) -> StoredIdealization:
    """Write one model subgroup as additive data (schema-guard stays green)."""
    import h5py

    model = result.model
    idealized, state_paths = _idealized_and_paths(result)
    created = datetime.now(UTC).isoformat()
    # A non-finite ELBO (NaN/inf from a degenerate fit) is recorded as absent rather
    # than written as an out-of-range float attr / non-standard JSON (Infinity/NaN).
    elbo_val = float(model.elbo) if model.elbo is not None and math.isfinite(model.elbo) else None
    str_dt = h5py.string_dtype(encoding="utf-8")

    with h5py.File(path, "r+") as f:
        parent = f.require_group(IDEALIZATION_GROUP)
        if model_name in parent:
            if not overwrite:  # re-checked under the write handle (TOCTOU-safe)
                raise FileExistsError(f"/idealization/{model_name} already exists in {path.name}")
            del parent[model_name]
        g = parent.create_group(model_name)
        g.attrs["type"] = model_type
        g.attrs["nstates"] = int(nstates)
        g.attrs["dtype"] = model.dtype
        g.attrs["intensity_quantity"] = intensity_quantity
        g.attrs["nstates_selected_by"] = selected_by
        g.attrs["n_molecules"] = len(molecule_keys)
        g.attrs["app_version"] = _app_version()
        g.attrs["created_utc"] = created
        if elbo_val is not None:
            g.attrs["elbo"] = elbo_val
        if elbo_by_nstates is not None:
            # Serialize a non-finite score (the -inf sentinel) as JSON null, so the
            # attr is always valid JSON; read_idealization maps null back to -inf.
            g.attrs["elbo_by_nstates"] = json.dumps(
                {str(k): (v if math.isfinite(v) else None) for k, v in elbo_by_nstates.items()}
            )

        g.create_dataset("mean", data=np.asarray(model.means, dtype="float64"))
        if model.variances is not None:
            g.create_dataset("var", data=np.asarray(model.variances, dtype="float64"))
        if model.tmatrix is not None:
            g.create_dataset("tmatrix", data=np.asarray(model.tmatrix, dtype="float64"))
        if model.norm_tmatrix is not None:
            g.create_dataset("norm_tmatrix", data=np.asarray(model.norm_tmatrix, dtype="float64"))
        g.create_dataset("idealized", data=idealized, compression="gzip")
        g.create_dataset("state_path", data=state_paths, dtype="int64", compression="gzip")
        g.create_dataset("molecule_key", data=list(molecule_keys), dtype=str_dt)
        g.create_dataset("molecule_id", data=list(molecule_ids), dtype=str_dt)
        g.create_dataset("input_hash", data=list(input_hashes), dtype=str_dt)

    return StoredIdealization(
        model_name=model_name,
        model_type=model_type,
        nstates=int(nstates),
        means=np.asarray(model.means, dtype="float64"),
        variances=None if model.variances is None else np.asarray(model.variances, dtype="float64"),
        tmatrix=None if model.tmatrix is None else np.asarray(model.tmatrix, dtype="float64"),
        elbo=elbo_val,
        idealized=idealized,
        state_paths=state_paths,
        molecule_keys=list(molecule_keys),
        molecule_ids=list(molecule_ids),
        input_hashes=list(input_hashes),
        intensity_quantity=intensity_quantity,
        nstates_selected_by=selected_by,
        elbo_by_nstates=elbo_by_nstates,
        app_version=_app_version(),
        created_utc=created,
    )


def _app_version() -> str:
    from tether import __version__

    return str(__version__)


def list_idealizations(project: Project | str | PathLike[str]) -> list[str]:
    """Names of the models written under ``/idealization`` (sorted)."""
    import h5py

    from tether.project.core import Project as _Project

    path = project.path if isinstance(project, _Project) else Path(project)
    with h5py.File(path, "r") as f:
        if IDEALIZATION_GROUP not in f:
            return []
        return sorted(f[IDEALIZATION_GROUP].keys())


def read_idealization(
    project: Project | str | PathLike[str], model_name: str
) -> StoredIdealization:
    """Read ``/idealization/{model_name}`` back into a :class:`StoredIdealization`."""
    import h5py

    from tether.project.core import Project as _Project

    path = project.path if isinstance(project, _Project) else Path(project)
    with h5py.File(path, "r") as f:
        if IDEALIZATION_GROUP not in f or model_name not in f[IDEALIZATION_GROUP]:
            raise KeyError(f"no /idealization/{model_name} in {Path(path).name}")
        g = f[IDEALIZATION_GROUP][model_name]

        def _opt(name: str) -> np.ndarray | None:
            return np.asarray(g[name][()], dtype="float64") if name in g else None

        elbo_json = g.attrs.get("elbo_by_nstates")
        elbo_by_nstates = (
            # A JSON null is the -inf sentinel a non-finite score was serialized as.
            {
                int(k): (float(v) if v is not None else float("-inf"))
                for k, v in json.loads(elbo_json).items()
            }
            if elbo_json is not None
            else None
        )
        return StoredIdealization(
            model_name=model_name,
            model_type=_to_str(g.attrs.get("type", "")),
            nstates=int(g.attrs["nstates"]),
            means=_opt("mean"),
            variances=_opt("var"),
            tmatrix=_opt("tmatrix"),
            elbo=float(g.attrs["elbo"]) if "elbo" in g.attrs else None,
            idealized=_opt("idealized"),
            state_paths=np.asarray(g["state_path"][()], dtype="int64"),
            molecule_keys=[_to_str(k) for k in g["molecule_key"][()]],
            molecule_ids=[_to_str(x) for x in g["molecule_id"][()]],
            input_hashes=[_to_str(h) for h in g["input_hash"][()]],
            intensity_quantity=_to_str(g.attrs.get("intensity_quantity", "corrected")),
            nstates_selected_by=_to_str(g.attrs.get("nstates_selected_by", "fixed")),
            elbo_by_nstates=elbo_by_nstates,
            app_version=_to_str(g.attrs.get("app_version", "")),
            created_utc=_to_str(g.attrs.get("created_utc", "")),
        )


def stale_molecule_keys(project: Project | str | PathLike[str], model_name: str) -> list[str]:
    """Molecules whose current trace no longer matches the model's input hash.

    Recomputes each stored molecule's :func:`input_trace_hash` from the *current*
    ``/traces`` + ``/molecules`` window and returns the ``molecule_key`` of every
    molecule whose recomputed hash diverges from the one recorded in
    ``/idealization/{model_name}`` — i.e. whose inputs changed since the fit (a
    re-extraction or a correction), so the idealization is stale for it (PRD §5
    staleness tracking). A molecule in the model but no longer in the store is
    reported as stale.

    The join is on the **unique** ``molecule_id`` (a per-row UUID), *not* the
    ``molecule_key`` — a ``molecule_key`` can name several rows (§7.10 quantized
    coordinate collisions), so joining on the key would recompute one row's hash for
    all its namesakes (spurious stale on one, a missed real change on another). Keys
    are returned de-duplicated in first-seen order.
    """
    from tether.project.core import Project as _Project

    path = project.path if isinstance(project, _Project) else Path(project)
    stored = read_idealization(project, model_name)
    donor_key, acceptor_key = _resolve_quantity(stored.intensity_quantity)

    molecules = read_molecules(path)
    traces = read_traces(path)
    row_by_id = {_to_str(mid): i for i, mid in enumerate(molecules["molecule_id"])}
    donor_all = np.asarray(traces.get(donor_key), dtype="float64") if donor_key in traces else None
    acceptor_all = (
        np.asarray(traces.get(acceptor_key), dtype="float64") if acceptor_key in traces else None
    )
    pre_all = molecules["analysis_window"]
    fr_all = molecules["frame_range"]

    stale: list[str] = []
    for key, mid, recorded in zip(
        stored.molecule_keys, stored.molecule_ids, stored.input_hashes, strict=True
    ):
        row = row_by_id.get(mid)
        if row is None or donor_all is None or acceptor_all is None:
            # the fitted row is gone from the store (or the trace layer vanished)
            if key not in stale:
                stale.append(key)
            continue
        lo, hi = int(pre_all[row][0]), int(pre_all[row][1])
        if hi <= lo:
            lo, hi = int(fr_all[row][0]), int(fr_all[row][1])
        current = input_trace_hash(
            donor_all[row, lo:hi], acceptor_all[row, lo:hi], stored.intensity_quantity
        )
        if current != recorded and key not in stale:
            stale.append(key)
    return stale
