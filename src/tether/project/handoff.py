# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bidirectional tMAVEN hand-off + non-destructive return-leg re-import (PRD §7.4, §5.3).

This is the **headless** core of the M2 "Hand to tMAVEN" round trip (PLAN §6 S7,
FR-IDEALIZE); the interactive per-trace reconcile *dialog* is a thin GUI layer over
it and lands separately.

Outbound leg — :func:`hand_off_to_tmaven`
    Export the selected molecules to an SMD-HDF5 file the standalone tMAVEN GUI opens
    directly (:mod:`tether.idealize.smd`). Tether's own coordinates + identities ride
    along in the superset group; the per-trace analysis windows ride along as the
    tMAVEN ``pre_list``/``post_list``. At M2 the tMAVEN integer ``classes`` are written
    neutral (``0`` = uncategorized) because the free-text ``category`` ↔ integer-class
    lookup table lands at M4 (ADR-0023, §7.6).

Return leg — :func:`read_return_leg` (preview) + :func:`apply_reconcile` (commit)
    tMAVEN's writer has no per-molecule slot and its exporter may subset/reorder by the
    GUI selection mask (Appendix D.1), so the returning SMD's coordinates are **not
    trusted**. Each returning trace is re-resolved to its store molecule by **exact
    intensity-trace matching** of the SMD ``raw`` series against the retained store
    (:func:`tether.idealize.match_return_leg`), with the molecule id / row order as a
    hint only; unmatched returning traces are **reported, never guessed** (§5.3).

    The return leg is **non-destructive**: an imported tMAVEN model is written as a
    *new* ``/idealization/{model}`` entry (via
    :func:`tether.project.idealize.write_idealization_model`), and an edited analysis
    window or class is surfaced as a **per-trace reconcile
    diff** the caller accepts or rejects — never a silent overwrite. An accepted
    analysis-window change is written to ``/molecules.analysis_window``, which
    **re-stales** that molecule's dependent idealizations (their input-provenance hash
    was computed over the old window; §5.1, :func:`tether.project.idealize.stale_molecule_keys`).
    tMAVEN's integer ``classes`` map onto Tether's free-text per-condition ``category``
    through the stored integer↔category lookup — at M2 only the non-lossy ``class 0 ↔
    uncategorized`` leg is applicable; a non-zero class is surfaced but its free-text
    mapping is deferred to M4 (§7.4, ADR-0025).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from tether.idealize.driver import read_model, states_from_idealized
from tether.idealize.matcher import match_return_leg
from tether.idealize.smd import read_smd, write_smd
from tether.imaging.extract import read_molecules, read_traces
from tether.io.schema import TABLE
from tether.project.idealize import (
    MODEL_TYPE_DEFAULT,
    input_trace_hash,
    write_idealization_model,
)
from tether.project.trace_layers import INTENSITY_QUANTITY_LAYERS

if TYPE_CHECKING:
    from collections.abc import Iterable
    from os import PathLike

    from tether.idealize.matcher import MatchResult
    from tether.idealize.smd import SMDData
    from tether.project.core import Project

__all__ = [
    "DEFAULT_MATCH_ATOL",
    "DEFAULT_MATCH_RTOL",
    "AppliedReconcile",
    "ClassChange",
    "HandoffManifest",
    "ReconcileReport",
    "TraceReconcile",
    "WindowChange",
    "apply_reconcile",
    "hand_off_to_tmaven",
    "read_return_leg",
]

#: Match tolerance defaults. The return-leg identity test is **exact** (tMAVEN
#: preserves the SMD ``raw`` byte-for-byte across a save — corrections/idealization
#: live in separate arrays), so this is an equality test with a tight float guard,
#: not a scientific tunable (hence no PRD §11.2 row); see :func:`match_return_leg`.
DEFAULT_MATCH_ATOL = 1e-6
DEFAULT_MATCH_RTOL = 0.0

#: The tMAVEN integer class reserved for "uncategorized" — the one non-lossy leg of
#: the integer↔category map available before the M4 editable list (§7.4, §7.6).
UNCATEGORIZED_CLASS = 0

#: ``/traces`` layer keys per ``intensity_quantity`` (the canonical mapping shared
#: with idealization and analysis).
_QUANTITY_KEYS = INTENSITY_QUANTITY_LAYERS


# --------------------------------------------------------------------------- #
# Public result types
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class HandoffManifest:
    """What :func:`hand_off_to_tmaven` exported, in SMD-row (export) order.

    ``molecule_keys[i]`` / ``molecule_ids[i]`` identify the store molecule written to
    SMD row ``i``; ``path`` is the SMD the standalone tMAVEN GUI opens.
    """

    path: Path
    intensity_quantity: str
    molecule_keys: list[str]
    molecule_ids: list[str]

    @property
    def n_molecules(self) -> int:
        return len(self.molecule_ids)


@dataclass(frozen=True)
class WindowChange:
    """A returning analysis-window edit vs the store's current window (half-open)."""

    old: tuple[int, int]
    new: tuple[int, int]


@dataclass(frozen=True)
class ClassChange:
    """A returning tMAVEN integer class vs the store's current ``category``.

    ``proposed_category`` is the free-text value an accept would write (``""`` =
    uncategorized for ``class 0``); it is ``None`` when the mapping is **deferred to
    M4** (a non-zero class has no free-text lookup yet, §7.6) and ``applicable`` is
    ``False`` — such a change is surfaced for the user but not committed at M2.
    """

    returned_class: int
    store_category: str
    proposed_category: str | None
    applicable: bool


@dataclass(frozen=True)
class TraceReconcile:
    """The per-trace return-leg diff for one matched molecule.

    ``returned_index`` is the row in the returning SMD; ``store_row`` the matched row
    in ``/molecules``. A ``None`` change means that facet is unchanged.
    """

    returned_index: int
    store_row: int
    molecule_key: str
    molecule_id: str
    window_change: WindowChange | None
    class_change: ClassChange | None

    @property
    def has_changes(self) -> bool:
        return self.window_change is not None or self.class_change is not None


@dataclass(frozen=True)
class ReconcileReport:
    """The full return-leg preview: per-trace diffs + the unmatched report.

    This is what the GUI reconcile prompt renders. ``unmatched_returned`` lists the
    returning-SMD rows that matched no store molecule (reported, never guessed).
    ``model_name`` is the ``/idealization`` entry an :func:`apply_reconcile` with
    ``import_idealization=True`` would write.
    """

    matched: list[TraceReconcile]
    unmatched_returned: list[int]
    intensity_quantity: str
    smd_path: Path
    model_path: Path | None
    model_name: str
    imported_model_type: str | None
    imported_nstates: int | None

    @property
    def n_matched(self) -> int:
        return len(self.matched)

    @property
    def n_unmatched(self) -> int:
        return len(self.unmatched_returned)

    @property
    def all_matched(self) -> bool:
        return not self.unmatched_returned

    @property
    def has_idealization(self) -> bool:
        return self.model_path is not None

    @property
    def window_changes(self) -> list[TraceReconcile]:
        return [t for t in self.matched if t.window_change is not None]

    @property
    def class_changes(self) -> list[TraceReconcile]:
        return [t for t in self.matched if t.class_change is not None]


@dataclass(frozen=True)
class AppliedReconcile:
    """What :func:`apply_reconcile` committed. Molecule identities are ``molecule_id``.

    ``classes_deferred`` are accepted class changes whose free-text mapping needs the
    M4 lookup (non-zero class) and were **not** written. ``stale_after`` are the
    ``molecule_key`` values whose dependent idealizations are now stale because their
    analysis window changed (§5.1). ``import_unfit_dropped`` are the ``molecule_key``
    values that matched by intensity but were **excluded from the tMAVEN fit** (outside
    the model's ``ran`` mask), so they were reported and left out of the imported model
    rather than written as an all-NaN idealization.
    """

    windows_applied: list[str] = field(default_factory=list)
    classes_applied: list[str] = field(default_factory=list)
    classes_deferred: list[str] = field(default_factory=list)
    idealization_written: str | None = None
    stale_after: list[str] = field(default_factory=list)
    import_unfit_dropped: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #
def _to_str(value: object) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _resolve_quantity(quantity: str) -> tuple[str, str]:
    try:
        return _QUANTITY_KEYS[quantity]
    except KeyError:
        raise ValueError(
            f"intensity_quantity must be one of {sorted(_QUANTITY_KEYS)}, got {quantity!r}"
        ) from None


def _project_path(project: Project | str | PathLike[str]) -> Path:
    from tether.project.core import Project as _Project

    return project.path if isinstance(project, _Project) else Path(project)


def _store_window(molecules: np.ndarray, row: int) -> tuple[int, int]:
    """The molecule's current analysis window, falling back to its native extent."""
    aw = molecules["analysis_window"][row]
    lo, hi = int(aw[0]), int(aw[1])
    if hi <= lo:  # window never set -> the native frame_range
        fr = molecules["frame_range"][row]
        lo, hi = int(fr[0]), int(fr[1])
    return lo, hi


def _store_raw(project_path: Path, intensity_quantity: str) -> tuple[np.ndarray, np.ndarray]:
    """The store's ``(N, T)`` donor / acceptor arrays for the given quantity."""
    donor_key, acceptor_key = _resolve_quantity(intensity_quantity)
    traces = read_traces(project_path)
    for key in (donor_key, acceptor_key):
        if key not in traces:
            raise ValueError(
                f"{project_path.name}/traces has no {key!r} layer "
                f"(intensity_quantity={intensity_quantity!r})"
            )
    return (
        np.asarray(traces[donor_key], dtype="float64"),
        np.asarray(traces[acceptor_key], dtype="float64"),
    )


def _class_change(returned_class: int, store_category: str) -> ClassChange | None:
    """Diff a returning integer class against the store's free-text ``category``.

    Only ``class 0 ↔ uncategorized`` is non-lossy and applicable before M4 (§7.6): a
    non-zero class is surfaced (``applicable=False``) but its free-text mapping waits
    on the M4 integer↔category lookup table.
    """
    rc = int(returned_class)
    if rc == UNCATEGORIZED_CLASS:
        if store_category == "":
            return None  # both uncategorized — no change
        return ClassChange(rc, store_category, proposed_category="", applicable=True)
    return ClassChange(rc, store_category, proposed_category=None, applicable=False)


def _selection_rows(molecules: np.ndarray, molecule_keys: list[str] | None) -> list[int]:
    """Store rows to hand off (``None`` = all extracted; duplicate keys take every row)."""
    keys = [_to_str(k) for k in molecules["molecule_key"]]
    if molecule_keys is None:
        return list(range(len(keys)))
    wanted = list(dict.fromkeys(molecule_keys))
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


@dataclass
class _ReconcileState:
    """The full return-leg resolution: the public report plus the arrays apply needs."""

    report: ReconcileReport
    smd: SMDData
    match: MatchResult
    molecules: np.ndarray
    donor_all: np.ndarray
    acceptor_all: np.ndarray


# --------------------------------------------------------------------------- #
# Outbound leg
# --------------------------------------------------------------------------- #
def hand_off_to_tmaven(
    project: Project | str | PathLike[str],
    molecule_keys: list[str] | None = None,
    *,
    out_path: str | PathLike[str],
    intensity_quantity: str = "corrected",
    overwrite: bool = True,
) -> HandoffManifest:
    """Export selected molecules to an SMD the standalone tMAVEN GUI opens directly.

    Parameters
    ----------
    project:
        A :class:`~tether.project.core.Project` or a path to a ``.tether`` store.
    molecule_keys:
        Molecules to export. ``None`` = every extracted molecule in store order; a list
        is exported in the **requested key order**, with a duplicate ``molecule_key``
        (§7.10) expanding to each matching store row (those in store order).
    out_path:
        Destination ``.hdf5`` SMD path.
    intensity_quantity:
        Which ``/traces`` layer feeds the SMD ``raw`` (``"corrected"`` default —
        background-subtracted, the apparent-E input — or ``"raw"``). The **same**
        quantity must be passed to the return leg for the intensity match to hold.
    overwrite:
        Replace the SMD's ``dataset`` group if the file already holds one.

    Returns
    -------
    HandoffManifest
        The written path + the exported molecules' identities in SMD-row order.
    """
    path = _project_path(project)
    molecules = read_molecules(path)
    if molecules.shape[0] == 0:
        raise ValueError(f"{path.name} has no extracted molecules to hand off")
    donor_all, acceptor_all = _store_raw(path, intensity_quantity)

    rows = _selection_rows(molecules, molecule_keys)
    if not rows:
        raise ValueError("no molecules selected to hand off")

    raw = np.stack([donor_all[rows], acceptor_all[rows]], axis=-1)  # (n_sel, T, 2)
    pre = np.empty(len(rows), dtype="int64")
    post = np.empty(len(rows), dtype="int64")
    for j, i in enumerate(rows):
        pre[j], post[j] = _store_window(molecules, i)

    sel_keys = [_to_str(molecules["molecule_key"][i]) for i in rows]
    sel_ids = [_to_str(molecules["molecule_id"][i]) for i in rows]
    donor_xy = np.stack([molecules["donor_xy"][i] for i in rows]).astype("float64")
    acceptor_xy = np.stack([molecules["acceptor_xy"][i] for i in rows]).astype("float64")

    out = Path(out_path)
    write_smd(
        out,
        raw,
        source_names=[path.name],
        # tMAVEN integer classes are neutral at M2 (category↔class lookup is M4, §7.6);
        # the per-trace analysis windows ride along so the GUI opens with them set.
        pre_list=pre,
        post_list=post,
        donor_xy=donor_xy,
        acceptor_xy=acceptor_xy,
        molecule_keys=sel_keys,
        molecule_ids=sel_ids,
        overwrite=overwrite,
    )
    return HandoffManifest(
        path=out,
        intensity_quantity=intensity_quantity,
        molecule_keys=sel_keys,
        molecule_ids=sel_ids,
    )


# --------------------------------------------------------------------------- #
# Return leg — resolution
# --------------------------------------------------------------------------- #
def _reconcile(
    project: Project | str | PathLike[str],
    smd_path: str | PathLike[str],
    *,
    model_path: str | PathLike[str] | None,
    intensity_quantity: str,
    model_name: str | None,
    atol: float,
    rtol: float,
) -> _ReconcileState:
    """Read the returning SMD, intensity-match it to the store, and build the diff."""
    path = _project_path(project)
    smd = read_smd(smd_path)
    molecules = read_molecules(path)
    donor_all, acceptor_all = _store_raw(path, intensity_quantity)
    store_raw = np.stack([donor_all, acceptor_all], axis=-1)  # (N, T, 2)

    # molecule-id / row order is a hint only; intensity identity decides (§5.3).
    id_hint = None
    if smd.molecule_ids is not None:
        row_by_id = {_to_str(mid): i for i, mid in enumerate(molecules["molecule_id"])}
        id_hint = np.array([row_by_id.get(mid, -1) for mid in smd.molecule_ids], dtype="int64")

    match = match_return_leg(smd.raw, store_raw, atol=atol, rtol=rtol, id_hint=id_hint)

    categories = [_to_str(c) for c in molecules["category"]]
    matched: list[TraceReconcile] = []
    for i, s in match.matched:
        window_change = None
        if smd.pre_list is not None and smd.post_list is not None:
            old_win = _store_window(molecules, s)
            # Normalize the returned window through the same hi<=lo -> frame_range
            # fallback the hash/apply use, so a degenerate returned window is treated as
            # the effective store window (no spurious change, no no-op degenerate write).
            new_win = _returning_window(smd, i, molecules, s)
            if new_win != old_win:
                window_change = WindowChange(old=old_win, new=new_win)
        class_change = None
        if smd.classes is not None:
            class_change = _class_change(int(smd.classes[i]), categories[s])
        matched.append(
            TraceReconcile(
                returned_index=int(i),
                store_row=int(s),
                molecule_key=_to_str(molecules["molecule_key"][s]),
                molecule_id=_to_str(molecules["molecule_id"][s]),
                window_change=window_change,
                class_change=class_change,
            )
        )

    resolved_name, model_type, nstates = _resolve_import_model(model_path, model_name)
    report = ReconcileReport(
        matched=matched,
        unmatched_returned=list(match.unmatched),
        intensity_quantity=intensity_quantity,
        smd_path=Path(smd_path),
        model_path=None if model_path is None else Path(model_path),
        model_name=resolved_name,
        imported_model_type=model_type,
        imported_nstates=nstates,
    )
    return _ReconcileState(
        report=report,
        smd=smd,
        match=match,
        molecules=molecules,
        donor_all=donor_all,
        acceptor_all=acceptor_all,
    )


def _resolve_import_model(
    model_path: str | PathLike[str] | None, model_name: str | None
) -> tuple[str, str | None, int | None]:
    """The ``/idealization`` name + summary an import of ``model_path`` would carry."""
    if model_path is None:
        # No model to import; a name is only meaningful for an idealization write.
        return (model_name or "tmaven-import"), None, None
    model = read_model(model_path)
    name = model_name or "tmaven-import"
    return name, (model.model_type or MODEL_TYPE_DEFAULT), int(model.nstates)


def read_return_leg(
    project: Project | str | PathLike[str],
    smd_path: str | PathLike[str],
    *,
    model_path: str | PathLike[str] | None = None,
    intensity_quantity: str = "corrected",
    model_name: str | None = None,
    atol: float = DEFAULT_MATCH_ATOL,
    rtol: float = DEFAULT_MATCH_RTOL,
) -> ReconcileReport:
    """Preview the return leg: intensity-match + per-trace reconcile diff + unmatched.

    Read-only — computes what :func:`apply_reconcile` *would* change so the GUI can
    render the per-trace reconcile prompt. ``intensity_quantity`` must match the
    outbound :func:`hand_off_to_tmaven`. When ``model_path`` is given the report notes
    the model type / state count an ``import_idealization`` apply would write.
    """
    return _reconcile(
        project,
        smd_path,
        model_path=model_path,
        intensity_quantity=intensity_quantity,
        model_name=model_name,
        atol=atol,
        rtol=rtol,
    ).report


# --------------------------------------------------------------------------- #
# Return leg — commit
# --------------------------------------------------------------------------- #
def _accepted(spec: bool | Iterable[str], candidates: set[str]) -> set[str]:
    """Resolve an accept spec (``True`` = all candidates; an iterable = its members)."""
    if spec is True:
        return set(candidates)
    if not spec:
        return set()
    return set(spec) & candidates


def apply_reconcile(
    project: Project | str | PathLike[str],
    smd_path: str | PathLike[str],
    *,
    model_path: str | PathLike[str] | None = None,
    intensity_quantity: str = "corrected",
    model_name: str | None = None,
    accept_windows: bool | Iterable[str] = False,
    accept_classes: bool | Iterable[str] = False,
    import_idealization: bool = False,
    overwrite: bool = False,
    atol: float = DEFAULT_MATCH_ATOL,
    rtol: float = DEFAULT_MATCH_RTOL,
) -> AppliedReconcile:
    """Commit accepted return-leg changes (non-destructive).

    Re-resolves the match (deterministic; the intensity identity does not depend on
    windows/classes) so the commit acts on exactly the diff a prior
    :func:`read_return_leg` showed. Then, in order:

    1. If ``import_idealization`` and ``model_path`` is given, write the tMAVEN model
       as a **new** ``/idealization/{model_name}`` (matched rows remapped to store
       molecules; unmatched dropped and reported). Refuses to clobber an existing
       model unless ``overwrite=True``.
    2. Apply accepted analysis-window edits to ``/molecules.analysis_window`` — which
       re-stales those molecules' dependent idealizations (§5.1).
    3. Apply accepted class changes: ``class 0`` → clear ``category`` to uncategorized;
       a non-zero class is recorded as deferred (needs the M4 lookup, §7.6).

    ``accept_windows`` / ``accept_classes`` are ``True`` (accept every applicable
    change) or an iterable of ``molecule_id`` to accept.
    """
    path = _project_path(project)
    # Materialize an iterable accept-spec once: ``accept_classes`` is consumed by two
    # passes (``_accepted`` + ``_deferred_class_ids``), so a single-use generator must
    # not be exhausted by the first — likewise ``accept_windows`` for symmetry.
    if not isinstance(accept_windows, bool):
        accept_windows = tuple(accept_windows)
    if not isinstance(accept_classes, bool):
        accept_classes = tuple(accept_classes)
    state = _reconcile(
        project,
        smd_path,
        model_path=model_path,
        intensity_quantity=intensity_quantity,
        model_name=model_name,
        atol=atol,
        rtol=rtol,
    )
    report = state.report

    window_candidates = {t.molecule_id for t in report.window_changes}
    class_candidates = {t.molecule_id for t in report.class_changes if t.class_change.applicable}
    accept_win_ids = _accepted(accept_windows, window_candidates)
    accept_cls_ids = _accepted(accept_classes, class_candidates)
    # A non-zero class accepted by the caller is surfaced as deferred (no M4 lookup).
    deferred_ids = _deferred_class_ids(report, accept_classes)

    written = None
    unfit_dropped: list[str] = []
    if import_idealization and model_path is not None:
        written, unfit_dropped = _import_model(
            path,
            state=state,
            model_path=Path(model_path),
            model_name=report.model_name,
            intensity_quantity=intensity_quantity,
            overwrite=overwrite,
        )

    windows_applied, classes_applied, stale_after = _commit_store_edits(
        path,
        report=report,
        accept_win_ids=accept_win_ids,
        accept_cls_ids=accept_cls_ids,
    )
    return AppliedReconcile(
        windows_applied=windows_applied,
        classes_applied=classes_applied,
        classes_deferred=deferred_ids,
        idealization_written=written,
        stale_after=stale_after,
        import_unfit_dropped=unfit_dropped,
    )


def _deferred_class_ids(report: ReconcileReport, accept_classes: bool | Iterable[str]) -> list[str]:
    """molecule_ids the caller accepted whose class mapping is M4-deferred (non-zero)."""
    deferred = {
        t.molecule_id
        for t in report.class_changes
        if t.class_change is not None and not t.class_change.applicable
    }
    if accept_classes is True:
        return sorted(deferred)
    if not accept_classes:
        return []
    return sorted(set(accept_classes) & deferred)


def _import_model(
    path: Path,
    *,
    state: _ReconcileState,
    model_path: Path,
    model_name: str,
    intensity_quantity: str,
    overwrite: bool,
) -> tuple[str, list[str]]:
    """Write a matched tMAVEN model as a new non-destructive ``/idealization`` entry.

    Only rows the model **actually fit** are imported. tMAVEN's exported ``idealized``
    is full length with **NaN rows for traces excluded from the fit**, and ``ran``
    records the SMD indices that were fit (driver.py). A matched returning trace that is
    outside ``ran`` **or** whose idealized row carries no state path at all (entirely
    non-finite) is treated as unfit — **dropped and reported**, never written as an
    all-NaN idealization with a valid-looking input hash (which ``stale_molecule_keys``
    would then read as fresh, masking that no fit exists). The all-NaN check is the
    authoritative signal: an *absent* and an *explicitly empty* ``ran`` are
    indistinguishable after :func:`read_model`, so a model that fit nothing (ran empty,
    all rows NaN) is correctly rejected rather than imported as empty state paths.
    Returns ``(model_name, unfit_dropped_keys)``.
    """
    model = read_model(model_path)
    if model.idealized is None:
        raise ValueError(
            f"{model_path.name} has no 'idealized' array; nothing to import as an "
            "/idealization state path"
        )
    means = np.asarray(model.means, dtype="float64").reshape(-1)
    if means.size == 0:
        # Match the in-app persistence guard (idealize._idealized_and_paths): a model
        # with no state means is degenerate — states_from_idealized cannot assign states.
        raise ValueError(
            f"{model_path.name} has no state means; refusing to import a degenerate model"
        )
    idealized_src = np.asarray(model.idealized, dtype="float64")
    if idealized_src.shape[0] != state.smd.n_molecules:
        raise ValueError(
            f"model rows ({idealized_src.shape[0]}) do not match the returning SMD "
            f"({state.smd.n_molecules}); the model and SMD must be the same session"
        )
    if not state.match.matched:
        raise ValueError("no returning trace matched the store; nothing to import")

    ran = np.asarray(model.ran, dtype="int64").reshape(-1)
    fit_set = set(ran.tolist()) if ran.size else None  # None => rely on the state-path check

    kept: list[tuple[int, int]] = []  # (returned_index, store_row) actually fit
    unfit_dropped: list[str] = []
    for i, s in state.match.matched:
        excluded_by_ran = fit_set is not None and i not in fit_set
        has_state_path = bool(np.isfinite(idealized_src[i]).any())
        if excluded_by_ran or not has_state_path:
            unfit_dropped.append(_to_str(state.molecules["molecule_key"][s]))
            continue
        kept.append((i, s))
    if not kept:
        raise ValueError(
            "no matched returning trace was fit by the model (every match falls outside "
            "the model's `ran` mask); nothing to import"
        )

    store_n_frames = state.donor_all.shape[1]
    idealized_rows = np.full((len(kept), store_n_frames), np.nan, dtype="float64")
    keys: list[str] = []
    ids: list[str] = []
    hashes: list[str] = []
    for j, (i, s) in enumerate(kept):
        _align_row(idealized_rows[j], idealized_src[i])
        keys.append(_to_str(state.molecules["molecule_key"][s]))
        ids.append(_to_str(state.molecules["molecule_id"][s]))
        # The model was fit over the returning window, so hash the store trace over
        # that window (== the returning raw over it, since a match means raw is equal).
        lo, hi = _returning_window(state.smd, i, state.molecules, s)
        hashes.append(
            input_trace_hash(
                state.donor_all[s, lo:hi], state.acceptor_all[s, lo:hi], intensity_quantity
            )
        )

    state_paths = states_from_idealized(idealized_rows, means)
    elbo = float(model.elbo) if model.elbo is not None and np.isfinite(model.elbo) else None

    write_idealization_model(
        path,
        model_name=model_name,
        model_type=model.model_type or MODEL_TYPE_DEFAULT,
        nstates=int(model.nstates),
        dtype=model.dtype,
        means=means,
        variances=model.variances,
        tmatrix=model.tmatrix,
        norm_tmatrix=model.norm_tmatrix,
        elbo=elbo,
        idealized=idealized_rows,
        state_paths=state_paths,
        molecule_keys=keys,
        molecule_ids=ids,
        input_hashes=hashes,
        intensity_quantity=intensity_quantity,
        selected_by="imported",
        elbo_by_nstates=None,
        app_version=_app_version(),
        created_utc=datetime.now(UTC).isoformat(),
        overwrite=overwrite,
        extra_attrs={
            "source_smd": state.report.smd_path.name,
            "source_model": model_path.name,
            "reconcile_matched": state.match.n_matched,
            "reconcile_unmatched": state.match.n_unmatched,
            "reconcile_imported": len(kept),
            "reconcile_unfit_dropped": len(unfit_dropped),
        },
    )
    return model_name, unfit_dropped


def _align_row(dst: np.ndarray, src: np.ndarray) -> None:
    """Copy a model's idealized row into a store-length row (trim / NaN-pad the tail)."""
    n = min(dst.shape[0], src.shape[0])
    dst[:n] = src[:n]  # dst is pre-filled with NaN, so a shorter src leaves NaN padding


def _returning_window(
    smd: SMDData, returned_index: int, molecules: np.ndarray, store_row: int
) -> tuple[int, int]:
    """The window the returning trace was idealized over (SMD window, else store).

    Applies the same ``hi <= lo`` → native ``frame_range`` fallback as
    :func:`_store_window` and :func:`tether.project.idealize.stale_molecule_keys`, so a
    degenerate returning window (``post <= pre``) hashes over the *same* frames the
    staleness recomputation later uses — a freshly imported model never reads as stale.
    """
    if smd.pre_list is not None and smd.post_list is not None:
        lo, hi = int(smd.pre_list[returned_index]), int(smd.post_list[returned_index])
        if hi > lo:
            return lo, hi
    return _store_window(molecules, store_row)


def _commit_store_edits(
    path: Path,
    *,
    report: ReconcileReport,
    accept_win_ids: set[str],
    accept_cls_ids: set[str],
) -> tuple[list[str], list[str], list[str]]:
    """Write accepted window / class edits to ``/molecules`` (one r+ pass)."""
    import h5py

    windows_applied: list[str] = []
    classes_applied: list[str] = []
    stale_after: list[str] = []
    if not accept_win_ids and not accept_cls_ids:
        return windows_applied, classes_applied, stale_after

    by_id = {t.molecule_id: t for t in report.matched}
    with h5py.File(path, "r+") as f:
        table = f["molecules"][TABLE]
        for mid in sorted(accept_win_ids | accept_cls_ids):
            trace = by_id[mid]
            rec = table[trace.store_row]  # a mutable np.void copy of the row
            if mid in accept_win_ids and trace.window_change is not None:
                rec["analysis_window"] = list(trace.window_change.new)
                windows_applied.append(mid)
                stale_after.append(trace.molecule_key)
            if mid in accept_cls_ids and trace.class_change is not None:
                # Only class 0 -> uncategorized is applicable at M2 (candidates were
                # filtered to applicable class changes before this point).
                rec["category"] = trace.class_change.proposed_category or ""
                classes_applied.append(mid)
            table[trace.store_row] = rec
    return windows_applied, classes_applied, stale_after


def _app_version() -> str:
    from tether import __version__

    return str(__version__)
