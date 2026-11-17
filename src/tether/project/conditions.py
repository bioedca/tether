# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Structured condition metadata + referential validation (PRD §5.1, §7.6; FR-ANNOTATE).

Extraction writes every molecule a *provisional* ``condition_id`` parsed from its
filename ([`tether.io.filename`][]) but leaves the ``/conditions`` table empty:
the structured condition rows that ``condition_id`` *references* are materialized
here, at the M4 annotation step. This module is the headless writer/validator
behind that step — including the **transactional re-key + human-confirmed merge**
([`rekey_condition`][], [`preview_rekey`][]) that corrects a mis-parsed
``condition_id``; the GUI confirm/correct + merge *dialogs* are the next M4 PR, a
thin layer over this core. Like the rest of [`tether.project`][], it is Qt-free
and scriptable (§7.11).

Condition identity (PRD §5.1)
-----------------------------
A condition is identified by the chemistry/optics **key** —
``(construct/variant, dye, ligand + concentration, buffer, temperature, laser
power)`` — carried by [`ConditionKey`][tether.io.filename.ConditionKey]. ``date``,
``replicate`` and the source file deliberately **vary within** a condition, so they
are *not* identity and are left empty on the aggregated ``/conditions`` row (a
representative value may be filled later; the per-file values are molecule/file
provenance). A condition spans many movies across many days/files, so its one
``/conditions`` row aggregates molecules from ≥ 2 files (§9 M4).

Referential validation (PRD §5.1)
---------------------------------
Validation is **referential**: a molecule's ``condition_id`` is valid only when it
resolves to a ``/conditions`` row *built from that key* — the row exists **and** its
stored key fields canonically hash back to its own ``condition_id``
([`condition_id`][tether.io.filename.ConditionKey.condition_id]). [`validate_conditions`][]
reports the two ways that fails: a molecule referencing a **missing** condition
(*dangling*), and a ``/conditions`` row whose fields no longer hash to its id
(*inconsistent* — the signal that a human edit needs the transactional re-key of
[`rekey_condition`][]).

**Keep-separate by default (PRD §5.1).** Because the id is a content hash of the
*exact* key, two movies that parse to slightly different strings ("near-miss")
yield *different* ids and stay separate conditions — never silently merged. An
explicit, human-confirmed merge ([`rekey_condition`][] with ``confirm=True``)
is required to collapse them; nothing here fuzzy-matches.

All writes are additive **data** into the M0-frozen ``/conditions/table`` (same
dtype; rows resized/assigned) — no group/dataset/dtype/field change — so the
``schema-guard`` freeze holds.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from tether.io.filename import ConditionKey, parse_filename
from tether.io.schema import CONDITIONS_DTYPE, TABLE

if TYPE_CHECKING:
    import numpy as np

__all__ = [
    "CategoryDifference",
    "CategoryList",
    "CategoryListComparison",
    "ConditionSyncSummary",
    "ConditionValidationReport",
    "ConfirmationRequired",
    "RekeyPreview",
    "RekeyResult",
    "add_category",
    "aggregate_molecules_by_condition",
    "compare_category_lists",
    "copy_category_list",
    "preview_rekey",
    "read_category_list",
    "read_condition_audit",
    "read_condition_keys",
    "read_conditions",
    "register_condition",
    "rekey_condition",
    "remove_category",
    "rename_category",
    "set_category_list",
    "sync_conditions",
    "validate_conditions",
]

_MOLECULES = "molecules"
_CONDITIONS = "conditions"
_SETTINGS = "settings"

#: The editable per-condition category list lives in a lazily-created sub-group of
#: the frozen ``/conditions`` group — one 1-D variable-length string dataset per
#: condition, ``/conditions/categories/<condition_id>`` (additive *data*, so
#: ``schema-guard`` stays green: the sub-group is absent from a fresh project, like
#: ``/settings/condition_audit``). The schema builder's docstring reserves the
#: ``/conditions`` group for exactly this (PRD §5.1).
_CATEGORIES_GROUP = "categories"

#: The append-only re-key/merge audit log, a lazily-created dataset under the
#: frozen ``/settings`` container (additive data — ``schema-guard`` stays green,
#: mirrors the ``/settings/batch`` provenance idiom). Each row records one
#: committed re-key/merge event.
_AUDIT_NAME = "condition_audit"
_REKEY_EVENT = "rekey"  # a correction into an empty destination (nothing collapses)
_MERGE_EVENT = "merge"  # two conditions collapse into one (requires confirmation)


# --- summaries ----------------------------------------------------------------


@dataclass(frozen=True)
class ConditionSyncSummary:
    """Outcome of a [`sync_conditions`][] pass."""

    #: Number of ``/molecules`` rows scanned.
    n_molecules: int
    #: Total ``/conditions`` rows after the pass (existing + newly created).
    n_conditions: int
    #: The ``condition_id`` values freshly materialized this pass (append order).
    created_ids: tuple[str, ...]
    #: Distinct referenced ids that could **not** be materialized — a stored
    #: ``condition_id`` with no existing row and no filename that parses back to
    #: it (a drifted/hand-assigned id). `validate_conditions` flags the
    #: molecules as *dangling*; `rekey_condition` resolves them.
    n_unresolved: int


@dataclass(frozen=True)
class ConditionValidationReport:
    """Referential-integrity report for a project's conditions (PRD §5.1)."""

    #: Number of ``/molecules`` rows checked.
    n_molecules: int
    #: Number of ``/conditions`` rows checked.
    n_conditions: int
    #: ``condition_id`` → the ``molecule_key`` set referencing a **missing**
    #: ``/conditions`` row (the reference does not resolve).
    dangling: dict[str, tuple[str, ...]]
    #: ``/conditions`` ids whose stored key fields do **not** canonically hash back
    #: to the id — a row not built from (or edited away from) its key.
    inconsistent: tuple[str, ...]

    @property
    def ok(self) -> bool:
        """``True`` iff every molecule reference resolves and every row is self-consistent."""
        return not self.dangling and not self.inconsistent


@dataclass(frozen=True)
class RekeyPreview:
    """What a re-key of ``from_condition_id`` → ``to_key`` *would* do (read-only, PRD §5.1).

    Surfaces exactly what [`rekey_condition`][] would change so the GUI (next M4
    PR) can show the user *before* they confirm, and never touches the file.
    """

    #: The (wrong) ``condition_id`` currently stored on the affected molecules.
    from_condition_id: str
    #: The corrected id = ``to_key.condition_id()`` the molecules would move to.
    to_condition_id: str
    #: The ``molecule_key`` set that would be re-keyed (currently on ``from_condition_id``).
    molecule_keys: tuple[str, ...]
    #: ``True`` iff the destination id already has (disjoint) members — the re-key
    #: would **merge** two conditions into one, so `rekey_condition` requires
    #: ``confirm=True`` (§5.1 "never silent on ~100-video conditions").
    is_merge: bool
    #: The destination's current members (the molecules already on ``to_condition_id``);
    #: empty for a plain correction, non-empty for a merge.
    destination_molecule_keys: tuple[str, ...]

    @property
    def n_molecules(self) -> int:
        """How many molecules the re-key would move."""
        return len(self.molecule_keys)


@dataclass(frozen=True)
class RekeyResult:
    """Outcome of a committed [`rekey_condition`][]."""

    #: The (wrong) ``condition_id`` that was re-keyed away from.
    from_condition_id: str
    #: The corrected destination id the molecules now carry.
    to_condition_id: str
    #: How many ``/molecules`` rows were re-keyed.
    n_molecules: int
    #: ``True`` iff the destination already had members (a merge, not a plain correction).
    is_merge: bool
    #: The logged event kind (``"merge"`` if `is_merge` else ``"rekey"``).
    event: str
    #: 0-based row position of this event in ``/settings/condition_audit``.
    audit_index: int


class ConfirmationRequired(RuntimeError):
    """A re-key would **merge** two conditions and ``confirm=True`` was not passed (§5.1).

    Raised by [`rekey_condition`][] when the destination condition already has
    members, so the re-key would collapse two conditions into one — the human-in-the
    -loop guard that keeps a ~100-video condition merge from happening silently.
    """


# --- vlen / numeric field helpers --------------------------------------------


def _to_str(value: object) -> str:
    """Decode an h5py variable-length string field (``bytes`` or ``str``)."""
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _num(value: float | None) -> float:
    """A key numeric (``float | None``) as an ``<f8`` cell (``None`` → ``NaN`` sentinel)."""
    return float("nan") if value is None else float(value)


def _opt_float(value: object) -> float | None:
    """Read a stored ``<f8`` key field back to ``float | None`` (``NaN`` → ``None``).

    The canonical key hashes an *absent* numeric as JSON ``null`` (the
    [`ConditionKey`][] field is ``None``); the frozen ``/conditions`` dtype has
    no nullable float, so absence is persisted as ``NaN`` and mapped back here — so
    a row reconstructs to the *same* [`ConditionKey`][], and the same id, it was
    written from.
    """
    v = float(value)
    return None if math.isnan(v) else v


def _row_to_key(row: np.ndarray) -> ConditionKey:
    """Reconstruct the identity [`ConditionKey`][] from a ``/conditions`` row."""
    return ConditionKey(
        construct_variant=_to_str(row["construct_variant"]),
        dye=_to_str(row["dye"]),
        ligand=_to_str(row["ligand"]),
        ligand_concentration=_opt_float(row["ligand_concentration"]),
        ligand_concentration_unit=_to_str(row["ligand_concentration_unit"]),
        buffer=_to_str(row["buffer"]),
        temperature_c=_opt_float(row["temperature_c"]),
        laser_power=_opt_float(row["laser_power"]),
    )


def _new_row_from_key(
    key: ConditionKey,
    *,
    date: str = "",
    replicate: str = "",
    tags: str = "",
    leakage_alpha: float = float("nan"),
    leakage_alpha_source: str = "",
) -> np.ndarray:
    """Build a fresh ``/conditions`` row (shape ``(1,)``) from a [`ConditionKey`][].

    The identity fields come from ``key`` (so [`ConditionKey.condition_id`][]
    self-consistency holds by construction); ``date``/``replicate``/``tags`` and the
    per-condition leakage provenance are additive within-condition provenance.
    """
    import numpy as np  # noqa: PLC0415

    row = np.zeros(1, dtype=CONDITIONS_DTYPE)
    # np.zeros leaves variable-length-string fields as int 0, which h5py refuses to
    # write; default every string field to "" (as the real writers do) first.
    for name in CONDITIONS_DTYPE.names:
        if CONDITIONS_DTYPE[name].kind == "O":
            row[name] = ""
    row["condition_id"] = key.condition_id()
    row["construct_variant"] = key.construct_variant
    row["dye"] = key.dye
    row["ligand"] = key.ligand
    row["ligand_concentration"] = _num(key.ligand_concentration)
    row["ligand_concentration_unit"] = key.ligand_concentration_unit
    row["buffer"] = key.buffer
    row["temperature_c"] = _num(key.temperature_c)
    row["laser_power"] = _num(key.laser_power)
    row["date"] = date
    row["replicate"] = replicate
    row["leakage_alpha"] = float(leakage_alpha)
    row["leakage_alpha_source"] = leakage_alpha_source
    row["tags"] = tags
    return row


# --- condition-row + audit-log helpers ---------------------------------------


def _find_condition_index(cond_table: object, condition_id: str) -> int | None:
    """Row index of the ``/conditions`` row carrying ``condition_id`` (or ``None``).

    One pass over the id column, short-circuiting at the first match.
    """
    existing = cond_table["condition_id"][:]  # type: ignore[index]
    return next((j for j, c in enumerate(existing) if _to_str(c) == condition_id), None)


def _append_condition_row(cond_table: object, row: np.ndarray) -> int:
    """Append one built ``/conditions`` row; return its new row index."""
    n0 = cond_table.shape[0]  # type: ignore[attr-defined]
    cond_table.resize((n0 + 1,))  # type: ignore[attr-defined]
    cond_table[n0:] = row  # type: ignore[index]
    return n0


def _ensure_condition_row(cond_table: object, key: ConditionKey) -> int:
    """Insert a ``/conditions`` row built from ``key`` if absent; return its index.

    Idempotent (the re-key destination may already be materialized). Only inserts —
    never edits an existing row — so a re-key never clobbers condition provenance.
    """
    i = _find_condition_index(cond_table, key.condition_id())
    if i is not None:
        return i
    return _append_condition_row(cond_table, _new_row_from_key(key))


def _app_version() -> str:
    """Best-effort Tether version for the audit provenance stamp (NFR-REPRO)."""
    try:
        from tether import __version__  # noqa: PLC0415

        return str(__version__)
    except Exception:  # pragma: no cover - defensive; version is normally present
        return "0.0.0+unknown"


def _utc_now_iso() -> str:
    """An ISO-8601 UTC timestamp with explicit offset (sortable, unambiguous)."""
    return datetime.now(UTC).isoformat()


def _validate_timestamp(timestamp: str) -> None:
    """Require an offset-aware ISO-8601 instant before it enters the append-only log.

    Mirrors [`tether.project.labels.set_curation_label`][]'s guard: a bad value
    would be permanently persisted into the audit provenance and is hard to repair.
    """
    try:
        parsed = datetime.fromisoformat(timestamp)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"timestamp must be an ISO-8601 string, got {timestamp!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp must include an explicit UTC offset, got {timestamp!r}")


def _audit_dtype() -> np.dtype:
    """The append-only re-key/merge audit-log compound dtype (a ``/settings`` artifact)."""
    import h5py  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    s = h5py.string_dtype(encoding="utf-8")
    return np.dtype(
        [
            ("event", s),  # _REKEY_EVENT | _MERGE_EVENT
            ("from_condition_id", s),
            ("to_condition_id", s),
            ("n_molecules", "<i8"),
            ("labeler", s),
            ("timestamp", s),
            ("reason", s),
            ("app_version", s),
        ]
    )


def _append_condition_audit(
    f: object,
    *,
    event: str,
    from_id: str,
    to_id: str,
    n_molecules: int,
    labeler: str,
    timestamp: str,
    reason: str,
) -> int:
    """Append one re-key/merge event to ``/settings/condition_audit``; return its index.

    Creates the resizable audit dataset lazily on first use (additive under the
    frozen ``/settings`` container — no structural change to the §5 skeleton, so
    ``schema-guard`` stays green; it is absent from a fresh project so the golden
    manifest is unaffected). Append-only history, like ``/labels`` (never rewritten).
    """
    import numpy as np  # noqa: PLC0415

    settings = f[_SETTINGS]  # type: ignore[index]
    dtype = _audit_dtype()
    if _AUDIT_NAME not in settings:
        settings.create_dataset(_AUDIT_NAME, shape=(0,), maxshape=(None,), dtype=dtype)
    ds = settings[_AUDIT_NAME]
    row = np.zeros(1, dtype=dtype)
    for name in dtype.names:  # zero-fill vlen-string fields (h5py refuses int 0)
        if dtype[name].kind == "O":
            row[name] = ""
    row["event"] = event
    row["from_condition_id"] = from_id
    row["to_condition_id"] = to_id
    row["n_molecules"] = int(n_molecules)
    row["labeler"] = labeler
    row["timestamp"] = timestamp
    row["reason"] = reason
    row["app_version"] = _app_version()
    n0 = ds.shape[0]
    ds.resize((n0 + 1,))
    ds[n0:] = row
    return n0


# --- writers ------------------------------------------------------------------


def register_condition(
    path: str | Path,
    key: ConditionKey,
    *,
    date: str | None = None,
    replicate: str | None = None,
    tags: str | None = None,
    leakage_alpha: float | None = None,
    leakage_alpha_source: str | None = None,
) -> np.ndarray:
    """Upsert a single ``/conditions`` row from ``key`` (idempotent on identity).

    Keyed by ``key.condition_id()``: if no row carries that id one is **inserted**
    from the key; if one exists only the **explicitly-provided** optional provenance
    (``date``/``replicate``/``tags``/``leakage_alpha``/``leakage_alpha_source``) is
    updated in place — the identity key fields are never touched, so the row stays
    self-consistent. Two calls with the same key produce **one** row (the M4
    aggregation invariant). Returns a copy of the resulting row.

    This is the low-level single-condition writer (one file open + one id-column
    scan); to materialize *many* conditions, call [`sync_conditions`][], which
    batches the scan — do not loop this per condition over a large table.
    """
    import h5py  # noqa: PLC0415

    condition_id = key.condition_id()
    with h5py.File(Path(path), "r+") as f:
        table = f[_CONDITIONS][TABLE]
        # One pass over the id column, short-circuiting at the first match; the
        # single-condition path is not meant for bulk loops (use sync_conditions).
        i = _find_condition_index(table, condition_id)
        if i is not None:
            row = table[i]
            if date is not None:
                row["date"] = date
            if replicate is not None:
                row["replicate"] = replicate
            if tags is not None:
                row["tags"] = tags
            if leakage_alpha is not None:
                row["leakage_alpha"] = float(leakage_alpha)
            if leakage_alpha_source is not None:
                row["leakage_alpha_source"] = leakage_alpha_source
            table[i] = row
            return table[i].copy()
        row = _new_row_from_key(
            key,
            date=date or "",
            replicate=replicate or "",
            tags=tags or "",
            leakage_alpha=float(leakage_alpha) if leakage_alpha is not None else float("nan"),
            leakage_alpha_source=leakage_alpha_source or "",
        )
        _append_condition_row(table, row)
        return row[0].copy()


def sync_conditions(path: str | Path) -> ConditionSyncSummary:
    """Materialize the ``/conditions`` rows referenced by ``/molecules`` (PRD §5.1).

    Scans ``/molecules`` and, for each referenced ``condition_id`` that is not yet
    materialized, creates one ``/conditions`` row from a **faithful witness** — a
    molecule whose ``source_filename`` parses back to that id — so every written row
    is self-consistent (its key hashes to its own id). Molecules sharing a key
    (same condition across many files) collapse to a single row; near-miss keys
    yield distinct ids and stay **separate** (never fuzzy-merged).

    Idempotent: existing ``/conditions`` rows (from a prior sync, or the M4 re-key
    PR) are never re-created or clobbered — a referenced id already present is
    skipped. A referenced id with neither an existing row nor a faithful filename
    witness (a drifted/hand-assigned id) is counted as ``n_unresolved`` and left for
    [`validate_conditions`][] to flag as dangling.
    """
    import h5py  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    with h5py.File(Path(path), "r+") as f:
        mol_table = f[_MOLECULES][TABLE]
        source_files = [_to_str(s) for s in mol_table["source_filename"][:]]
        stored_ids = [_to_str(c) for c in mol_table["condition_id"][:]]
        n_molecules = len(stored_ids)

        cond_table = f[_CONDITIONS][TABLE]
        existing_ids = {_to_str(c) for c in cond_table["condition_id"][:]}

        # First-seen faithful witness per not-yet-materialized referenced id.
        witness_key: dict[str, ConditionKey] = {}
        for src, cid in zip(source_files, stored_ids, strict=True):
            if not cid or cid in existing_ids or cid in witness_key:
                continue
            parsed = parse_filename(src)
            if parsed.condition_id == cid:
                witness_key[cid] = parsed.key

        referenced = {cid for cid in stored_ids if cid}
        unresolved = referenced - existing_ids - set(witness_key)

        created_ids = tuple(witness_key)
        if created_ids:
            block = np.concatenate([_new_row_from_key(witness_key[cid]) for cid in created_ids])
            n0 = cond_table.shape[0]
            cond_table.resize((n0 + len(created_ids),))
            cond_table[n0:] = block
        n_conditions = int(cond_table.shape[0])

    return ConditionSyncSummary(
        n_molecules=n_molecules,
        n_conditions=n_conditions,
        created_ids=created_ids,
        n_unresolved=len(unresolved),
    )


# --- readers / queries --------------------------------------------------------


def read_conditions(path: str | Path) -> np.ndarray:
    """Read ``/conditions/table`` back as a structured array (a copy, append order)."""
    import h5py  # noqa: PLC0415

    with h5py.File(Path(path), "r") as f:
        return f[_CONDITIONS][TABLE][:]


def read_condition_keys(path: str | Path) -> dict[str, ConditionKey]:
    """Map each materialized ``condition_id`` → its identity [`ConditionKey`][] (PRD §5.1).

    Reconstructs the key every ``/conditions`` row was built from (inverse of the row
    builder, ``NaN`` numeric cells mapping back to ``None`` via `_opt_float`), so
    a caller can filter conditions by **key fields** — e.g. "every condition with
    ligand tRNA" — without re-parsing filenames (the cross-movie query,
    [`tether.analysis.query`][]). A row whose fields no longer hash to its own id (an
    *inconsistent* row, [`validate_conditions`][]) is still included under its stored
    id — the reconstructed key reflects what the fields currently say. Append order is
    preserved. Read-only.
    """
    rows = read_conditions(path)
    return {_to_str(rows["condition_id"][i]): _row_to_key(rows[i]) for i in range(rows.shape[0])}


def aggregate_molecules_by_condition(path: str | Path) -> dict[str, list[str]]:
    """Map each ``condition_id`` to the ``molecule_key`` list it aggregates (PRD §5.1).

    The cross-file aggregation the §9 M4 gate checks: molecules that share a
    condition key — even from different movies/files/days — group under the one id
    (they carry the same content-hashed ``condition_id``). Molecules with no
    condition (empty ``condition_id``) are omitted. Insertion order is preserved.
    """
    import h5py  # noqa: PLC0415

    with h5py.File(Path(path), "r") as f:
        table = f[_MOLECULES][TABLE]
        cids = [_to_str(c) for c in table["condition_id"][:]]
        mkeys = [_to_str(m) for m in table["molecule_key"][:]]

    out: dict[str, list[str]] = {}
    for cid, mkey in zip(cids, mkeys, strict=True):
        if not cid:
            continue
        out.setdefault(cid, []).append(mkey)
    return out


def validate_conditions(path: str | Path) -> ConditionValidationReport:
    """Check referential integrity of ``/molecules`` → ``/conditions`` (PRD §5.1).

    A molecule's ``condition_id`` is valid only when it resolves to a
    ``/conditions`` row **built from that key** — the row exists and its fields hash
    back to its id. Reports *dangling* references (a ``condition_id`` with no row)
    and *inconsistent* rows (fields that no longer hash to the id). Molecules with
    an empty ``condition_id`` (no condition assigned) are skipped. Read-only.
    """
    import h5py  # noqa: PLC0415

    with h5py.File(Path(path), "r") as f:
        mol_table = f[_MOLECULES][TABLE]
        cids = [_to_str(c) for c in mol_table["condition_id"][:]]
        mkeys = [_to_str(m) for m in mol_table["molecule_key"][:]]
        cond_rows = f[_CONDITIONS][TABLE][:]

    n_molecules = len(cids)
    n_conditions = int(cond_rows.shape[0])
    cond_ids = {_to_str(cond_rows["condition_id"][i]) for i in range(n_conditions)}

    inconsistent = tuple(
        _to_str(cond_rows["condition_id"][i])
        for i in range(n_conditions)
        if _row_to_key(cond_rows[i]).condition_id() != _to_str(cond_rows["condition_id"][i])
    )

    dangling: dict[str, list[str]] = {}
    for cid, mkey in zip(cids, mkeys, strict=True):
        if not cid:
            continue
        if cid not in cond_ids:
            dangling.setdefault(cid, []).append(mkey)

    return ConditionValidationReport(
        n_molecules=n_molecules,
        n_conditions=n_conditions,
        dangling={cid: tuple(keys) for cid, keys in dangling.items()},
        inconsistent=inconsistent,
    )


# --- transactional re-key + human-confirmed merge (PRD §5.1) ------------------


def preview_rekey(path: str | Path, from_condition_id: str, to_key: ConditionKey) -> RekeyPreview:
    """Read-only: describe re-keying every molecule on ``from_condition_id`` → ``to_key``.

    The GUI (next M4 PR) calls this first to show the user exactly what
    [`rekey_condition`][] would change — the affected ``molecule_key`` set, whether
    it **merges** into an already-populated destination, and the destination's current
    members — *before* they confirm. Never mutates the file.
    """
    import h5py  # noqa: PLC0415

    to_id = to_key.condition_id()
    with h5py.File(Path(path), "r") as f:
        table = f[_MOLECULES][TABLE]
        cids = [_to_str(c) for c in table["condition_id"][:]]
        mkeys = [_to_str(m) for m in table["molecule_key"][:]]

    affected = tuple(mk for mk, cid in zip(mkeys, cids, strict=True) if cid == from_condition_id)
    # to_id != from_condition_id ⇒ the destination set and the affected set are
    # disjoint (a molecule holds exactly one condition_id), so a non-empty destination
    # is a genuine two-conditions-into-one merge.
    dest = tuple(
        mk
        for mk, cid in zip(mkeys, cids, strict=True)
        if cid == to_id and to_id != from_condition_id
    )
    return RekeyPreview(
        from_condition_id=from_condition_id,
        to_condition_id=to_id,
        molecule_keys=affected,
        is_merge=len(dest) > 0,
        destination_molecule_keys=dest,
    )


def rekey_condition(
    path: str | Path,
    from_condition_id: str,
    to_key: ConditionKey,
    *,
    confirm: bool = False,
    labeler: str | None = None,
    reason: str = "",
    timestamp: str | None = None,
) -> RekeyResult:
    """Transactionally re-key every molecule on ``from_condition_id`` → ``to_key`` (PRD §5.1).

    The corrective counterpart to [`validate_conditions`][]: when a molecule's
    provisional ``condition_id`` is wrong (a mis-parse → *dangling*/*inconsistent*), a
    human supplies the corrected [`ConditionKey`][tether.io.filename.ConditionKey] and this

    1. materializes the destination ``/conditions`` row from ``to_key`` (idempotent), so
       the re-keyed molecules resolve and never dangle;
    2. re-keys **all** affected ``/molecules`` rows to ``to_key.condition_id()`` in a
       **single full-table write** — HDF5 ``r+`` is not journaled, so this is a single-write
       update with post-crash *detectability*, not a durability transaction: rewriting the
       whole ``/molecules`` table in one ``H5Dwrite`` moves the affected rows together (not
       one at a time), so a re-key is never applied to only *some* of them, and any partial
       state a crash could leave is still detectable/repairable via [`validate_conditions`][]; and
    3. appends one provenance-stamped row to the append-only ``/settings/condition_audit``
       log (event · from/to id · count · labeler · timestamp · reason · app version).

    **Human-confirmed merge (§5.1, "never silent on ~100-video conditions").** When the
    destination id already has members, the re-key would *collapse two conditions into
    one*; it then raises [`ConfirmationRequired`][] unless ``confirm=True``. A plain
    correction into an empty destination (nothing collapses) needs no confirmation. Use
    [`preview_rekey`][] to inspect the effect (and ``is_merge``) beforehand.

    Parameters
    ----------
    path:
        The ``.tether`` project to re-key (opened ``r+``).
    from_condition_id:
        The (wrong) ``condition_id`` currently stored on the molecules to move.
    to_key:
        The corrected condition key; its
        [`condition_id`][tether.io.filename.ConditionKey.condition_id]
        is the destination id and its fields build the destination ``/conditions`` row
        (so that row is self-consistent by construction).
    confirm:
        Must be ``True`` to proceed when the operation is a merge (see above).
    labeler:
        Who performed the re-key; defaults to
        [`default_labeler`][tether.project.labels.default_labeler].
    reason:
        Free-text audit note (e.g. ``"filename mis-parse: Tbox→T-box"``).
    timestamp:
        Offset-aware ISO-8601 stamp; defaults to now (UTC). Validated before any write.

    Returns
    -------
    RekeyResult
        The committed outcome (counts, merge flag, audit row index).

    Raises
    ------
    ValueError
        If ``from_condition_id`` is empty, ``to_key`` hashes back to it (a no-op
        re-key), or ``timestamp`` is not an offset-aware ISO-8601 string.
    KeyError
        If no ``/molecules`` row carries ``from_condition_id`` (never a silent no-op).
    ConfirmationRequired
        If the operation is a merge and ``confirm`` is not ``True``.
    """
    import h5py  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    from tether.project.labels import default_labeler  # noqa: PLC0415

    if not from_condition_id:
        raise ValueError("from_condition_id must be a non-empty condition id")
    to_id = to_key.condition_id()
    if to_id == from_condition_id:
        raise ValueError(f"to_key already hashes to {from_condition_id!r}; nothing to re-key")
    labeler = labeler if labeler is not None else default_labeler()
    timestamp = timestamp if timestamp is not None else _utc_now_iso()
    _validate_timestamp(timestamp)  # reject a bad stamp before it enters the audit log
    path = Path(path)

    with h5py.File(path, "r+") as f:
        mol_table = f[_MOLECULES][TABLE]
        data = mol_table[:]  # full structured copy (single read → single write-back)
        cids = np.array([_to_str(c) for c in data["condition_id"]], dtype=object)
        affected_mask = cids == from_condition_id
        n_affected = int(np.count_nonzero(affected_mask))
        if n_affected == 0:
            raise KeyError(f"no molecule with condition_id {from_condition_id!r} in {path.name}")
        n_dest = int(np.count_nonzero(cids == to_id))  # disjoint (to_id != from_id)
        is_merge = n_dest > 0
        if is_merge and not confirm:
            raise ConfirmationRequired(
                f"re-keying {from_condition_id!r} into {to_id!r} would merge "
                f"{n_affected} molecule(s) into a condition already holding {n_dest}; "
                f"pass confirm=True to merge (PRD §5.1)"
            )
        event = _MERGE_EVENT if is_merge else _REKEY_EVENT

        # 1) Destination /conditions row exists (from the corrected key) before any
        #    molecule references it — so the re-key never leaves a dangling reference.
        _ensure_condition_row(f[_CONDITIONS][TABLE], to_key)

        # 2) The re-key itself: one full-table write, all affected rows together.
        data["condition_id"][affected_mask] = to_id
        mol_table[:] = data

        # 3) Append the audit event (append-only history).
        audit_index = _append_condition_audit(
            f,
            event=event,
            from_id=from_condition_id,
            to_id=to_id,
            n_molecules=n_affected,
            labeler=labeler,
            timestamp=timestamp,
            reason=reason,
        )

    return RekeyResult(
        from_condition_id=from_condition_id,
        to_condition_id=to_id,
        n_molecules=n_affected,
        is_merge=is_merge,
        event=event,
        audit_index=audit_index,
    )


def read_condition_audit(path: str | Path) -> np.ndarray:
    """Read the append-only ``/settings/condition_audit`` re-key/merge log (a copy).

    Returns a length-0 array of the audit dtype when no re-key has run yet (the
    dataset is created lazily on the first [`rekey_condition`][]).
    """
    import h5py  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    with h5py.File(Path(path), "r") as f:
        settings = f[_SETTINGS]
        if _AUDIT_NAME not in settings:
            return np.zeros(0, dtype=_audit_dtype())
        return settings[_AUDIT_NAME][:]


# --- editable per-condition category list (PRD §5.1, FR-ANNOTATE) -------------


@dataclass(frozen=True)
class CategoryList:
    """A condition's editable, ordered per-trace category vocabulary (PRD §5.1).

    The fully user-editable per-trace category list — **no presets** — scoped to
    one ``condition_id`` and shared by every molecule in that condition, across all
    its movies/days/files (a condition spans ≥ 2 files, §5.1). The **integer code**
    of a category is its 0-based position in [`categories`][] (the "int ↔ category
    lookup", exposed as [`lookup`][] / [`code_of`][]).

    Molecules store their chosen category by **name** in the frozen
    ``/molecules.category`` string field, so this list is the editable *vocabulary*:
    reordering, renaming, or removing an entry re-numbers the codes but never
    silently rewrites an existing molecule's stored category (that is a separate,
    explicit labeling step — mirroring the keep-separate/never-silent ethos of the
    condition re-key, [`rekey_condition`][]).
    """

    #: The condition this vocabulary belongs to.
    condition_id: str
    #: The ordered category names; the index of each is its integer code.
    categories: tuple[str, ...]

    @property
    def n_categories(self) -> int:
        """How many categories the list holds."""
        return len(self.categories)

    def __len__(self) -> int:
        return len(self.categories)

    def __iter__(self):  # noqa: ANN204 - iterates the ordered names
        return iter(self.categories)

    def __contains__(self, category: object) -> bool:
        return category in self.categories

    @property
    def lookup(self) -> dict[int, str]:
        """The integer code → category-name map (code = 0-based position, §5.1)."""
        return dict(enumerate(self.categories))

    def code_of(self, category: str) -> int:
        """The integer code (0-based position) of ``category``.

        Raises [`KeyError`][] if ``category`` is not in the list.
        """
        try:
            return self.categories.index(category)
        except ValueError:
            raise KeyError(category) from None


def _require_safe_condition_id(condition_id: str) -> str:
    """Guard a ``condition_id`` used as an HDF5 link name (non-empty, no ``/``).

    Real ids are ``cond-<hex>`` ([`condition_id`][tether.io.filename.ConditionKey.condition_id]),
    but the public functions take an arbitrary string, so refuse an empty id or one
    containing ``/`` (which HDF5 would treat as a group path) before it names a
    dataset.
    """
    if not condition_id:
        raise ValueError("condition_id must be a non-empty condition id")
    if "/" in condition_id:
        raise ValueError(f"condition_id must not contain '/': {condition_id!r}")
    return condition_id


def _validate_name(name: object, *, label: str = "category name") -> str:
    """Type-check + whitespace-strip + non-empty-check a single category name.

    The one place the name-entry rules live, shared by `_normalize_categories`
    (per list element), [`add_category`][], and [`rename_category`][] so they
    stay in sync if the rules ever change (e.g. a max-length or character-set rule).
    """
    if not isinstance(name, str):
        raise TypeError(f"{label} must be a string, got {type(name).__name__}")
    stripped = name.strip()
    if not stripped:
        raise ValueError(f"{label} must be non-empty (after stripping whitespace)")
    return stripped


def _normalize_categories(categories: object) -> tuple[str, ...]:
    """Validate + normalize a category list: strip, reject empty/duplicate names.

    Each name is whitespace-stripped and must be non-empty; exact duplicates (after
    stripping) are rejected so the code ↔ name map stays one-to-one. Order is
    preserved (it defines the integer codes). Returns the normalized tuple.
    """
    if isinstance(categories, str):
        # A bare string is almost certainly a mistake (it would iterate characters).
        raise TypeError("categories must be a sequence of names, not a single string")
    names: list[str] = []
    seen: set[str] = set()
    for raw in categories:
        name = _validate_name(raw)
        if name in seen:
            raise ValueError(f"duplicate category name: {name!r}")
        seen.add(name)
        names.append(name)
    return tuple(names)


def _assert_condition_exists(f: object, condition_id: str) -> None:
    """Require ``condition_id`` to resolve to a ``/conditions`` row (else ``KeyError``).

    Enforces that a category list is *scoped to a real condition* — writers refuse
    to attach a vocabulary to an unregistered id (materialize it first via
    [`register_condition`][] / [`sync_conditions`][]).
    """
    table = f[_CONDITIONS][TABLE]  # type: ignore[index]
    if _find_condition_index(table, condition_id) is None:
        raise KeyError(f"no /conditions row with condition_id {condition_id!r}")


def _read_category_names(f: object, condition_id: str) -> tuple[str, ...]:
    """Read a condition's ordered category names ``()`` when unset (no group/dataset)."""
    conditions = f[_CONDITIONS]  # type: ignore[index]
    if _CATEGORIES_GROUP not in conditions:
        return ()
    group = conditions[_CATEGORIES_GROUP]
    if condition_id not in group:
        return ()
    return tuple(_to_str(v) for v in group[condition_id][:])


def _write_category_names(f: object, condition_id: str, names: tuple[str, ...]) -> None:
    """Persist a condition's ordered category names (create-or-resize the vlen dataset).

    Lazily creates the ``/conditions/categories`` sub-group and the per-condition
    resizable vlen-string dataset on first use (additive data). An emptied list
    resizes the dataset to 0 rows (kept, not deleted, to avoid HDF5 free-space churn).
    """
    import h5py  # noqa: PLC0415

    conditions = f[_CONDITIONS]  # type: ignore[index]
    group = (
        conditions[_CATEGORIES_GROUP]
        if _CATEGORIES_GROUP in conditions
        else conditions.create_group(_CATEGORIES_GROUP, track_order=True)
    )
    if condition_id in group:
        ds = group[condition_id]
        ds.resize((len(names),))
    else:
        ds = group.create_dataset(
            condition_id,
            shape=(len(names),),
            maxshape=(None,),
            dtype=h5py.string_dtype(encoding="utf-8"),
        )
    if names:
        ds[:] = list(names)


def read_category_list(path: str | Path, condition_id: str) -> CategoryList:
    """Read a condition's editable per-trace category list (PRD §5.1). Read-only.

    Returns an empty [`CategoryList`][] when the condition has no vocabulary yet
    (the "no presets" default) or the id is not present. Lenient about the id's
    existence — unlike the writers, a read never fails on an unregistered condition.
    """
    import h5py  # noqa: PLC0415

    _require_safe_condition_id(condition_id)
    with h5py.File(Path(path), "r") as f:
        names = _read_category_names(f, condition_id)
    return CategoryList(condition_id=condition_id, categories=names)


def set_category_list(path: str | Path, condition_id: str, categories: object) -> CategoryList:
    """Replace a condition's whole ordered category list (PRD §5.1).

    The general editor — set, reorder (pass a permutation), or clear (pass ``[]``) a
    condition's vocabulary in one write. Names are stripped and must be non-empty and
    unique; order defines the integer codes. Requires ``condition_id`` to be a
    materialized ``/conditions`` row ([`KeyError`][] otherwise). Returns the
    resulting [`CategoryList`][].
    """
    import h5py  # noqa: PLC0415

    _require_safe_condition_id(condition_id)
    names = _normalize_categories(categories)
    with h5py.File(Path(path), "r+") as f:
        _assert_condition_exists(f, condition_id)
        _write_category_names(f, condition_id, names)
    return CategoryList(condition_id=condition_id, categories=names)


def add_category(path: str | Path, condition_id: str, name: str) -> CategoryList:
    """Append one category to a condition's list; return the updated list (PRD §5.1).

    The new category's integer code is the (previous) list length. Rejects a name
    that (after stripping) is empty or already present ([`ValueError`][]), and an
    unregistered ``condition_id`` ([`KeyError`][]).
    """
    import h5py  # noqa: PLC0415

    _require_safe_condition_id(condition_id)
    stripped = _validate_name(name)
    with h5py.File(Path(path), "r+") as f:
        _assert_condition_exists(f, condition_id)
        current = _read_category_names(f, condition_id)
        if stripped in current:
            raise ValueError(f"category already present: {stripped!r}")
        names = (*current, stripped)
        _write_category_names(f, condition_id, names)
    return CategoryList(condition_id=condition_id, categories=names)


def rename_category(path: str | Path, condition_id: str, old: str, new: str) -> CategoryList:
    """Rename ``old`` → ``new`` in place, preserving its integer code (PRD §5.1).

    A list-level edit only: it keeps the category's position (so its code is
    unchanged) but does **not** rewrite the ``/molecules.category`` values that
    reference ``old`` — re-labeling molecules is a separate explicit step (never
    silent, §5.1). Rejects an absent ``old`` or an already-present ``new``
    ([`KeyError`][] / [`ValueError`][]), and an unregistered condition.
    """
    import h5py  # noqa: PLC0415

    _require_safe_condition_id(condition_id)
    new_stripped = _validate_name(new, label="new category name")
    with h5py.File(Path(path), "r+") as f:
        _assert_condition_exists(f, condition_id)
        current = _read_category_names(f, condition_id)
        if old not in current:
            raise KeyError(f"category not in list: {old!r}")
        if new_stripped != old and new_stripped in current:
            raise ValueError(f"category already present: {new_stripped!r}")
        names = tuple(new_stripped if c == old else c for c in current)
        _write_category_names(f, condition_id, names)
    return CategoryList(condition_id=condition_id, categories=names)


def remove_category(path: str | Path, condition_id: str, name: str) -> CategoryList:
    """Remove ``name`` from a condition's list; return the updated list (PRD §5.1).

    A list-level edit only: later categories shift down one code, and molecules that
    referenced ``name`` keep their stored ``/molecules.category`` string (they are
    not silently re-labeled, §5.1). Rejects an absent ``name`` ([`KeyError`][])
    and an unregistered condition.
    """
    import h5py  # noqa: PLC0415

    _require_safe_condition_id(condition_id)
    with h5py.File(Path(path), "r+") as f:
        _assert_condition_exists(f, condition_id)
        current = _read_category_names(f, condition_id)
        if name not in current:
            raise KeyError(f"category not in list: {name!r}")
        names = tuple(c for c in current if c != name)
        _write_category_names(f, condition_id, names)
    return CategoryList(condition_id=condition_id, categories=names)


# --- one condition's category list across projects (ADR-0068, #679) ----------

#: The three ways one category can differ between two projects' lists.
_ONLY_IN_A = "only_in_a"
_ONLY_IN_B = "only_in_b"
_CODE = "code"


@dataclass(frozen=True)
class CategoryDifference:
    """One category whose name or integer code differs between two projects' lists.

    A name that only one list holds has ``None`` as its code in the other list. A
    rename therefore reports two differences, one for each name, which is what makes
    the integer code ambiguous across the two projects.
    """

    #: The category name.
    category: str
    #: Its integer code in project A, or ``None`` when A does not hold the name.
    code_a: int | None
    #: Its integer code in project B, or ``None`` when B does not hold the name.
    code_b: int | None

    @property
    def kind(self) -> str:
        """``"only_in_a"``, ``"only_in_b"``, or ``"code"`` (both hold it, at different codes)."""
        if self.code_b is None:
            return _ONLY_IN_A
        if self.code_a is None:
            return _ONLY_IN_B
        return _CODE


@dataclass(frozen=True)
class CategoryListComparison:
    """The result of [`compare_category_lists`][] for one condition in two projects."""

    #: The condition both lists belong to.
    condition_id: str
    #: The list in project A.
    a: CategoryList
    #: The list in project B.
    b: CategoryList
    #: Each category whose name or code differs: A's order first, then B-only names.
    differences: tuple[CategoryDifference, ...]

    @property
    def equal(self) -> bool:
        """``True`` when both projects name the same categories with the same codes."""
        return not self.differences


def _read_held_category_names(path: str | Path, condition_id: str, role: str) -> tuple[str, ...]:
    """Read a condition's list from a project that must hold the condition.

    Unlike the lenient [`read_category_list`][], a cross-project read refuses a
    project with no ``/conditions`` row for ``condition_id`` ([`KeyError`][]
    naming the file, ADR-0068): an empty list read from a project that does not
    hold the condition would otherwise copy as a clear or compare as "every
    category missing".
    """
    import h5py  # noqa: PLC0415

    with h5py.File(Path(path), "r") as f:
        table = f[_CONDITIONS][TABLE]
        if _find_condition_index(table, condition_id) is None:
            raise KeyError(
                f"the {role} project {Path(path).name!r} does not hold condition "
                f"{condition_id!r}; register the condition there first"
            )
        return _read_category_names(f, condition_id)


def copy_category_list(
    source: str | Path, destination: str | Path, condition_id: str
) -> CategoryList:
    """Copy one condition's category list from one project into another (ADR-0068).

    A condition can span several projects, and each project keeps its own copy of
    the list. This writes the source list into the destination in the same order,
    so every category keeps its integer code (code = 0-based position, PRD §5.1).
    The destination's previous list is replaced; an empty source list empties it.

    Both projects must hold ``condition_id`` as a ``/conditions`` row: a copy from
    or into a project that does not is refused with a [`KeyError`][] that names
    the file. Like the other list edits, molecules' stored ``/molecules.category``
    names in the destination are not rewritten. Returns the destination's list.
    """
    import h5py  # noqa: PLC0415

    _require_safe_condition_id(condition_id)
    names = _read_held_category_names(source, condition_id, "source")
    with h5py.File(Path(destination), "r+") as f:
        if _find_condition_index(f[_CONDITIONS][TABLE], condition_id) is None:
            raise KeyError(
                f"the destination project {Path(destination).name!r} does not hold "
                f"condition {condition_id!r}; register the condition there first"
            )
        _write_category_names(f, condition_id, names)
    return CategoryList(condition_id=condition_id, categories=names)


def compare_category_lists(
    path_a: str | Path, path_b: str | Path, condition_id: str
) -> CategoryListComparison:
    """Compare one condition's category list in two projects (ADR-0068). Read-only.

    Reports each category whose name or integer code differs: a name only one
    project holds, or a name both hold at different codes. Two equal lists report
    nothing ([`CategoryListComparison.equal`][]). Both projects must hold
    ``condition_id`` ([`KeyError`][] naming the file otherwise).
    """
    _require_safe_condition_id(condition_id)
    a = CategoryList(condition_id, _read_held_category_names(path_a, condition_id, "first"))
    b = CategoryList(condition_id, _read_held_category_names(path_b, condition_id, "second"))
    codes_a = {name: code for code, name in a.lookup.items()}
    codes_b = {name: code for code, name in b.lookup.items()}
    differences = [
        CategoryDifference(name, code, codes_b.get(name))
        for name, code in codes_a.items()
        if codes_b.get(name) != code
    ]
    differences += [
        CategoryDifference(name, None, code)
        for name, code in codes_b.items()
        if name not in codes_a
    ]
    return CategoryListComparison(
        condition_id=condition_id, a=a, b=b, differences=tuple(differences)
    )
