# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-molecule category and analysis-window setters (PRD §5.1, §7.2, §7.6; #358).

The headless writers behind a curator's two per-molecule edits:

* **Category** — the molecule's editable per-condition category value, stored by
  *name* in the M0-frozen ``/molecules.category`` field. [`set_category`][] accepts
  only a name in the molecule's condition's [`CategoryList`][tether.project.conditions.CategoryList]
  (that vocabulary stays authoritative); [`clear_category`][] writes ``""``, the
  "no category" value every importer writes (``rows["category"] = ""`` in
  [`tether.project.analysis_import`][]).
* **Analysis window** — the ``[start, stop)`` frames the analysis reads, the frozen
  ``/molecules.analysis_window`` field. [`set_analysis_window`][] clamps the request
  with the same rule an SMD import applies
  (`tether.project.analysis_import._resolve_windows`), bounded by the molecule's
  own ``frame_range`` inside ``[0, n_frames]``. [`reset_analysis_window`][] restores
  ``frame_range``, the extraction default ([`tether.project.photobleach`][]).

Both fields already exist in the frozen schema, so these writes are additive **data**
and the ``schema-guard`` freeze (ADR-0005) is untouched.

Manual window versus the photobleach auto-default
-------------------------------------------------
[`tether.project.photobleach.compute_photobleach`][] sets the auto window only on a
row whose window still equals ``frame_range``. A window set here that differs from
``frame_range`` is therefore a manual override, and a later photobleach pass leaves it
alone. A reset, or a set that resolves to the whole ``frame_range``, puts the row back
under the auto-default — the schema has one window field and no "manual" flag, so the
whole frame range is how "not narrowed" is written.

Provenance (append-only)
------------------------
Each changed ``/molecules`` row gets one row in ``/settings/molecule_audit``, a
lazily-created, resizable compound dataset under the frozen ``/settings`` container
(the ``/settings/condition_audit`` idiom). The audit row is written **before** the
``/molecules`` field, as [`tether.project.labels.set_curation_label`][] orders its
two writes, so a crash leaves at worst an orphan audit row, never an unaudited change.

Category edits are **not** logged to ``/labels``: ``/labels.label_value`` carries the
[`CurationLabel`][tether.project.labels.CurationLabel] accept/reject codec that the ML ranker
and the merge-back read, and a category code there would read as a training label.

An edit that would not change a row writes nothing for that row (the
[`tether.project.labels.unreject`][] no-op rule), so the log holds only real changes.

The feature cache
-----------------
``/features/table`` ([`tether.project.features`][]) is computed from each molecule's
windowed traces. A window edit that changes a row the table holds deletes the table,
before the audit row, so the ranker reads "run compute_features first" instead of
features from the old frames. A category edit leaves it alone: features never read
the category. A window write holds ``tether.project.features.WINDOW_WRITE_GUARD``, which
``compute_features`` also holds while it checks the windows and writes the table, so
an edit cannot land between that check and the write.

The single-writer ``.lock`` is the caller's responsibility, as for every module-level
``r+`` writer in this package; the [`Project`][tether.project.core.Project] methods check
it before they call in.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from tether.io.schema import TABLE

if TYPE_CHECKING:
    import numpy as np

__all__ = [
    "MOLECULE_AUDIT_EVENTS",
    "clear_category",
    "read_molecule_audit",
    "reset_analysis_window",
    "set_analysis_window",
    "set_category",
]

_MOLECULES = "molecules"
_TRACES = "traces"
_SETTINGS = "settings"
#: The append-only per-molecule edit log, created lazily under ``/settings``.
_AUDIT_NAME = "molecule_audit"

_SET_CATEGORY = "set_category"
_CLEAR_CATEGORY = "clear_category"
_SET_WINDOW = "set_analysis_window"
_RESET_WINDOW = "reset_analysis_window"
#: The closed ``event`` vocabulary of ``/settings/molecule_audit``.
MOLECULE_AUDIT_EVENTS = frozenset({_SET_CATEGORY, _CLEAR_CATEGORY, _SET_WINDOW, _RESET_WINDOW})

#: The ``old_window`` / ``new_window`` value of an audit row that edits the category.
_NO_WINDOW = (-1, -1)


def _audit_dtype() -> np.dtype:
    """Return the ``/settings/molecule_audit`` compound dtype (a ``/settings`` artifact)."""
    import h5py  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    s = h5py.string_dtype(encoding="utf-8")
    return np.dtype(
        [
            ("event", s),  # one of MOLECULE_AUDIT_EVENTS
            ("molecule_id", s),
            ("molecule_key", s),
            ("condition_id", s),
            ("old_category", s),
            ("new_category", s),
            ("old_window", "<i4", (2,)),
            ("new_window", "<i4", (2,)),
            ("labeler", s),
            ("timestamp", s),
            ("reason", s),
            ("app_version", s),
        ]
    )


def _app_version() -> str:
    """Best-effort Tether version for the audit provenance stamp (NFR-REPRO)."""
    try:
        from tether import __version__  # noqa: PLC0415

        return str(__version__)
    except Exception:  # pragma: no cover - defensive; version is normally present
        return "0.0.0+unknown"


def _resolve_provenance(labeler: str | None, timestamp: str | None, reason: str) -> tuple[str, str]:
    """Default and validate ``labeler`` / ``timestamp`` / ``reason`` before any file I/O."""
    from tether.project.conditions import (  # noqa: PLC0415
        _refuse_nul_audit_text,
        _utc_now_iso,
        _validate_timestamp,
    )
    from tether.project.labels import default_labeler  # noqa: PLC0415

    labeler = labeler if labeler is not None else default_labeler()
    timestamp = timestamp if timestamp is not None else _utc_now_iso()
    _validate_timestamp(timestamp)  # a bad stamp would be persisted forever
    # h5py refuses NUL mid-append; fromisoformat accepts it as the date/time separator
    _refuse_nul_audit_text(labeler=labeler, reason=reason, timestamp=timestamp)
    return labeler, timestamp


def _matched_rows(table: np.ndarray, molecule_key: str, path: Path) -> list[int]:
    """Row indices carrying ``molecule_key``; `KeyError` when there are none."""
    from tether.project.labels import _match_indices  # noqa: PLC0415

    idx = _match_indices(table["molecule_key"], molecule_key)
    if not idx:
        raise KeyError(f"no molecule with molecule_key {molecule_key!r} in {path.name}")
    return idx


def _to_str(value: object) -> str:
    """Decode an h5py variable-length string field (``bytes`` or ``str``)."""
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _append_audit(f: object, rows: list[dict[str, object]]) -> np.ndarray:
    """Append ``rows`` to ``/settings/molecule_audit`` (created lazily); return them."""
    import numpy as np  # noqa: PLC0415

    from tether.project.conditions import _append_rows  # noqa: PLC0415

    dtype = _audit_dtype()
    out = np.zeros(len(rows), dtype=dtype)
    for name in dtype.names:  # zero-fill vlen-string fields (h5py refuses int 0)
        if dtype[name].kind == "O":
            out[name] = ""
    for i, row in enumerate(rows):
        for name, value in row.items():
            out[name][i] = value
    out["app_version"] = _app_version()
    if not rows:
        return out
    settings = f[_SETTINGS]  # type: ignore[index]
    if _AUDIT_NAME not in settings:
        settings.create_dataset(_AUDIT_NAME, shape=(0,), maxshape=(None,), dtype=dtype)
    _append_rows(settings[_AUDIT_NAME], out)  # shrinks back if the rows are refused
    return out


def _write_category(
    path: str | Path,
    molecule_key: str,
    category: str,
    *,
    event: str,
    labeler: str | None,
    reason: str,
    timestamp: str | None,
) -> np.ndarray:
    """Shared body of [`set_category`][] / [`clear_category`][]."""
    import h5py  # noqa: PLC0415

    from tether.project.conditions import (  # noqa: PLC0415
        _read_category_names,
        _refuse_nul_audit_text,
    )

    labeler, timestamp = _resolve_provenance(labeler, timestamp, reason)
    _refuse_nul_audit_text(category=category)
    path = Path(path)
    with h5py.File(path, "r+") as f:
        mol_table = f[_MOLECULES][TABLE]
        table = mol_table[:]
        idx = _matched_rows(table, molecule_key, path)
        condition_ids = {_to_str(table["condition_id"][i]) for i in idx}
        if len(condition_ids) > 1:
            # One molecule_key is one physical molecule, so its rows share a condition;
            # a divergence would make "the condition's vocabulary" ambiguous.
            raise ValueError(
                f"molecule_key {molecule_key!r} maps to multiple condition_ids "
                f"{sorted(condition_ids)}; cannot pick one category vocabulary"
            )
        condition_id = condition_ids.pop()
        if category:
            vocabulary = _read_category_names(f, condition_id) if condition_id else ()
            if category not in vocabulary:
                raise ValueError(
                    f"category {category!r} is not in condition {condition_id!r}'s "
                    f"category list {list(vocabulary)}; add it with add_category first"
                )

        changed = [i for i in idx if _to_str(table["category"][i]) != category]
        audit = [
            {
                "event": event,
                "molecule_id": _to_str(table["molecule_id"][i]),
                "molecule_key": molecule_key,
                "condition_id": condition_id,
                "old_category": _to_str(table["category"][i]),
                "new_category": category,
                "old_window": _NO_WINDOW,
                "new_window": _NO_WINDOW,
                "labeler": labeler,
                "timestamp": timestamp,
                "reason": reason,
            }
            for i in changed
        ]
        # 1) the audit rows FIRST, 2) then the field (ordered for recovery).
        written = _append_audit(f, audit)
        for i in changed:
            row = mol_table[i]
            row["category"] = category
            mol_table[i] = row
    return written


def set_category(
    path: str | Path,
    molecule_key: str,
    category: str,
    *,
    labeler: str | None = None,
    reason: str = "",
    timestamp: str | None = None,
) -> np.ndarray:
    """Set a molecule's category to a name in its condition's category list (§5.1).

    Resolves ``molecule_key`` against ``/molecules`` and writes ``category`` into the
    ``category`` field of every matching row, after one ``/settings/molecule_audit``
    row per changed row. The name is whitespace-stripped, as the category list stores
    it. A row that already carries the name is left alone and logs nothing.

    Parameters
    ----------
    path:
        The ``.tether`` project (opened ``r+``).
    molecule_key:
        The molecule's cross-file content identity (§5.1).
    category:
        A name in the molecule's condition's
        [`CategoryList`][tether.project.conditions.CategoryList].
    labeler:
        Curator identity; defaults to [`tether.project.labels.default_labeler`][].
    reason:
        Free-text audit note.
    timestamp:
        An offset-aware ISO-8601 stamp; defaults to the current UTC time. Validated
        before any write.

    Returns
    -------
    numpy.ndarray
        The appended audit rows (zero rows when nothing changed).

    Raises
    ------
    KeyError
        If no ``/molecules`` row carries ``molecule_key``.
    ValueError
        If ``category`` is empty (use [`clear_category`][]), is not in the
        condition's category list, the matched rows disagree on ``condition_id``,
        ``timestamp`` is not an offset-aware ISO-8601 string, or ``category``,
        ``labeler``, ``reason`` or ``timestamp`` contains a NUL character. Nothing is
        written.
    """
    if not isinstance(category, str):
        raise TypeError(f"category must be a string, got {type(category).__name__}")
    name = category.strip()
    if not name:
        raise ValueError("category must be a non-empty name; use clear_category to clear it")
    return _write_category(
        path,
        molecule_key,
        name,
        event=_SET_CATEGORY,
        labeler=labeler,
        reason=reason,
        timestamp=timestamp,
    )


def clear_category(
    path: str | Path,
    molecule_key: str,
    *,
    labeler: str | None = None,
    reason: str = "",
    timestamp: str | None = None,
) -> np.ndarray:
    """Clear a molecule's category to ``""``, the "no category" value (§5.1).

    Same matching, provenance and no-op rule as [`set_category`][]; returns the
    appended audit rows. Raises `KeyError` for an unknown ``molecule_key``.
    """
    return _write_category(
        path,
        molecule_key,
        "",
        event=_CLEAR_CATEGORY,
        labeler=labeler,
        reason=reason,
        timestamp=timestamp,
    )


def _n_frames(f: object) -> int | None:
    """Return the stored trace length (``/traces`` axis 1), or ``None`` without a layer."""
    traces = f[_TRACES]  # type: ignore[index]
    for name in traces:
        layer = traces[name]
        if getattr(layer, "ndim", 0) == 2:
            return int(layer.shape[1])
    return None


def _clamped_window(frame_range: object, start: int, stop: int, n_frames: int | None) -> tuple:
    """Clamp ``[start, stop)`` into the row's ``frame_range`` inside ``[0, n_frames]``.

    Reuses `tether.project.analysis_import._resolve_windows` rather than restating
    its rule: the request is shifted so the row's valid extent ``[lo, hi)`` reads as
    ``[0, hi - lo]``, resolved there, and shifted back. That clamps both ends into the
    extent, and a window that is empty after clamping falls back to the whole extent —
    the same fallback an SMD import and the analysis views apply.
    """
    import numpy as np  # noqa: PLC0415

    from tether.project.analysis_import import _resolve_windows  # noqa: PLC0415

    lo, hi = int(frame_range[0]), int(frame_range[1])  # type: ignore[index]
    lo = max(lo, 0)
    if n_frames is not None:
        hi = min(hi, n_frames)
    if hi <= lo:
        raise ValueError(
            f"the molecule has no valid frames (frame_range {[lo, hi]}); there is no window to set"
        )
    request = np.array([[start - lo, stop - lo]], dtype=np.int64)
    resolved = _resolve_windows(request, 1, hi - lo)[0]
    return (int(resolved[0]) + lo, int(resolved[1]) + lo)


def _invalidate_features(f: object, molecule_ids: set[str]) -> None:
    """Delete ``/features/table`` when it holds a row for any of ``molecule_ids``.

    The table is a recomputable cache of each molecule's windowed traces
    ([`tether.project.features`][]), so a window edit makes that molecule's row stale.
    Deleting the table makes [`feature_matrix`][tether.project.features.feature_matrix]
    raise "run compute_features first" rather than serve features from frames that are
    no longer the stored window. A table with no row for an edited molecule is kept.
    """
    from tether.project.features import FEATURES_GROUP  # noqa: PLC0415

    if not molecule_ids or FEATURES_GROUP not in f or TABLE not in f[FEATURES_GROUP]:
        return
    group = f[FEATURES_GROUP]
    featured = {_to_str(mid) for mid in group[TABLE]["molecule_id"]}
    if featured & molecule_ids:
        del group[TABLE]


def _write_window(
    path: str | Path,
    molecule_key: str,
    window: tuple[int, int] | None,
    *,
    event: str,
    labeler: str | None,
    reason: str,
    timestamp: str | None,
) -> np.ndarray:
    """Shared body of [`set_analysis_window`][] / [`reset_analysis_window`][].

    ``window=None`` is the reset: each row's target is its own ``frame_range``.
    """
    import h5py  # noqa: PLC0415

    from tether.project.features import WINDOW_WRITE_GUARD  # noqa: PLC0415

    labeler, timestamp = _resolve_provenance(labeler, timestamp, reason)
    path = Path(path)
    with WINDOW_WRITE_GUARD, h5py.File(path, "r+") as f:
        mol_table = f[_MOLECULES][TABLE]
        table = mol_table[:]
        idx = _matched_rows(table, molecule_key, path)
        n_frames = _n_frames(f)
        targets: dict[int, tuple[int, int]] = {}
        for i in idx:
            frame_range = table["frame_range"][i]
            if window is None:
                targets[i] = (int(frame_range[0]), int(frame_range[1]))
            else:
                targets[i] = _clamped_window(frame_range, window[0], window[1], n_frames)

        audit = []
        changed = []
        for i in idx:
            old = (int(table["analysis_window"][i][0]), int(table["analysis_window"][i][1]))
            if old == targets[i]:
                continue
            changed.append(i)
            audit.append(
                {
                    "event": event,
                    "molecule_id": _to_str(table["molecule_id"][i]),
                    "molecule_key": molecule_key,
                    "condition_id": _to_str(table["condition_id"][i]),
                    "old_category": "",
                    "new_category": "",
                    "old_window": old,
                    "new_window": targets[i],
                    "labeler": labeler,
                    "timestamp": timestamp,
                    "reason": reason,
                }
            )
        # 0) drop a feature cache built from the old frames, 1) the audit rows,
        # 2) then the field (ordered for recovery: a crash after 0 costs a recompute).
        _invalidate_features(f, {_to_str(table["molecule_id"][i]) for i in changed})
        written = _append_audit(f, audit)
        for i in changed:
            row = mol_table[i]
            row["analysis_window"] = targets[i]
            mol_table[i] = row
    return written


def set_analysis_window(
    path: str | Path,
    molecule_key: str,
    start: int,
    stop: int,
    *,
    labeler: str | None = None,
    reason: str = "",
    timestamp: str | None = None,
) -> np.ndarray:
    """Set a molecule's ``[start, stop)`` analysis window, clamped to its frames (§7.2).

    Each row carrying ``molecule_key`` gets the request clamped into its own
    ``frame_range`` (itself bounded by ``[0, n_frames]``, the stored trace length), by
    the rule `tether.project.analysis_import._resolve_windows` applies. A request
    that is empty after clamping — it lies wholly outside the frames — resolves to the
    whole ``frame_range``, as that rule's fallback does; read the returned audit rows'
    ``new_window`` for what was stored. A window that differs from ``frame_range`` is a
    manual override that a later
    [`compute_photobleach`][tether.project.photobleach.compute_photobleach]
    leaves alone. A change to a row that ``/features/table`` holds deletes that table,
    a cache of the old frames; recompute it with
    [`compute_features`][tether.project.features.compute_features].

    Parameters
    ----------
    path:
        The ``.tether`` project (opened ``r+``).
    molecule_key:
        The molecule's cross-file content identity (§5.1).
    start, stop:
        The requested absolute frame indices, ``start < stop``.
    labeler, reason, timestamp:
        Audit provenance, as for [`set_category`][].

    Returns
    -------
    numpy.ndarray
        The appended audit rows (zero rows when nothing changed).

    Raises
    ------
    KeyError
        If no ``/molecules`` row carries ``molecule_key``.
    TypeError
        If ``start`` or ``stop`` is not an integer.
    ValueError
        If ``start >= stop``, a matched row has no valid frames, ``timestamp`` is
        not an offset-aware ISO-8601 string, or ``labeler``, ``reason`` or
        ``timestamp`` contains a NUL character. Nothing is written.
    """
    import numbers  # noqa: PLC0415

    for name, value in (("start", start), ("stop", stop)):
        if isinstance(value, bool) or not isinstance(value, numbers.Integral):
            raise TypeError(f"{name} must be an integer frame index, got {value!r}")
    start, stop = int(start), int(stop)
    if start >= stop:
        raise ValueError(f"analysis window must have start < stop, got [{start}, {stop})")
    return _write_window(
        path,
        molecule_key,
        (start, stop),
        event=_SET_WINDOW,
        labeler=labeler,
        reason=reason,
        timestamp=timestamp,
    )


def reset_analysis_window(
    path: str | Path,
    molecule_key: str,
    *,
    labeler: str | None = None,
    reason: str = "",
    timestamp: str | None = None,
) -> np.ndarray:
    """Restore a molecule's analysis window to its ``frame_range`` (the extraction default).

    After a reset the row is again under the photobleach auto-default
    ([`tether.project.photobleach`][]): the next
    [`compute_photobleach`][tether.project.photobleach.compute_photobleach] pass may narrow it. Same
    matching, provenance, no-op and feature-cache rule as [`set_analysis_window`][].
    """
    return _write_window(
        path,
        molecule_key,
        None,
        event=_RESET_WINDOW,
        labeler=labeler,
        reason=reason,
        timestamp=timestamp,
    )


def read_molecule_audit(path: str | Path) -> np.ndarray:
    """Read ``/settings/molecule_audit`` (append order); zero rows when never written."""
    import h5py  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    with h5py.File(Path(path), "r") as f:
        settings = f[_SETTINGS]
        if _AUDIT_NAME not in settings:
            return np.zeros(0, dtype=_audit_dtype())
        return settings[_AUDIT_NAME][:]
