# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Structured condition metadata + referential validation (PRD §5.1, §7.6; FR-ANNOTATE).

Extraction writes every molecule a *provisional* ``condition_id`` parsed from its
filename (:mod:`tether.io.filename`) but leaves the ``/conditions`` table empty:
the structured condition rows that ``condition_id`` *references* are materialized
here, at the M4 annotation step. This module is the headless writer/validator
behind that step — the GUI confirm/correct + transactional re-key is the *next* M4
PR — and, like the rest of :mod:`tether.project`, it is Qt-free and scriptable
(§7.11).

Condition identity (PRD §5.1)
-----------------------------
A condition is identified by the chemistry/optics **key** —
``(construct/variant, dye, ligand + concentration, buffer, temperature, laser
power)`` — carried by :class:`~tether.io.filename.ConditionKey`. ``date``,
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
(:meth:`~tether.io.filename.ConditionKey.condition_id`). :func:`validate_conditions`
reports the two ways that fails: a molecule referencing a **missing** condition
(*dangling*), and a ``/conditions`` row whose fields no longer hash to its id
(*inconsistent* — the signal that a human edit needs the transactional re-key of
the next M4 PR).

**Keep-separate by default (PRD §5.1).** Because the id is a content hash of the
*exact* key, two movies that parse to slightly different strings ("near-miss")
yield *different* ids and stay separate conditions — never silently merged. An
explicit, human-confirmed merge is the next M4 PR; nothing here fuzzy-matches.

All writes are additive **data** into the M0-frozen ``/conditions/table`` (same
dtype; rows resized/assigned) — no group/dataset/dtype/field change — so the
``schema-guard`` freeze holds.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from tether.io.filename import ConditionKey, parse_filename
from tether.io.schema import CONDITIONS_DTYPE, TABLE

if TYPE_CHECKING:
    import numpy as np

__all__ = [
    "ConditionSyncSummary",
    "ConditionValidationReport",
    "aggregate_molecules_by_condition",
    "read_conditions",
    "register_condition",
    "sync_conditions",
    "validate_conditions",
]

_MOLECULES = "molecules"
_CONDITIONS = "conditions"


# --- summaries ----------------------------------------------------------------


@dataclass(frozen=True)
class ConditionSyncSummary:
    """Outcome of a :func:`sync_conditions` pass."""

    #: Number of ``/molecules`` rows scanned.
    n_molecules: int
    #: Total ``/conditions`` rows after the pass (existing + newly created).
    n_conditions: int
    #: The ``condition_id`` values freshly materialized this pass (append order).
    created_ids: tuple[str, ...]
    #: Distinct referenced ids that could **not** be materialized — a stored
    #: ``condition_id`` with no existing row and no filename that parses back to
    #: it (a drifted/hand-assigned id). :func:`validate_conditions` flags the
    #: molecules as *dangling*; the M4 re-key PR resolves them.
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
    :class:`ConditionKey` field is ``None``); the frozen ``/conditions`` dtype has
    no nullable float, so absence is persisted as ``NaN`` and mapped back here — so
    a row reconstructs to the *same* :class:`ConditionKey`, and the same id, it was
    written from.
    """
    v = float(value)
    return None if math.isnan(v) else v


def _row_to_key(row: np.ndarray) -> ConditionKey:
    """Reconstruct the identity :class:`ConditionKey` from a ``/conditions`` row."""
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
    """Build a fresh ``/conditions`` row (shape ``(1,)``) from a :class:`ConditionKey`.

    The identity fields come from ``key`` (so :meth:`ConditionKey.condition_id`
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

    This is the low-level single-condition writer; :func:`sync_conditions`
    materializes a whole project's conditions from ``/molecules`` in one pass.
    """
    import h5py  # noqa: PLC0415

    condition_id = key.condition_id()
    with h5py.File(Path(path), "r+") as f:
        table = f[_CONDITIONS][TABLE]
        ids = [_to_str(c) for c in table["condition_id"][:]]
        if condition_id in ids:
            i = ids.index(condition_id)
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
        n0 = table.shape[0]
        table.resize((n0 + 1,))
        table[n0:] = row
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
    :func:`validate_conditions` to flag as dangling.
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
