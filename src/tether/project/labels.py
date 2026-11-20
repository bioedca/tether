# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Curation accept/reject logging to ``/labels`` (PRD §5.1, §7.5; FR-ML).

Every curation keystroke is a provenance-stamped, append-only event. This module
is the headless writer behind the M2 curation surface (PRD §7.11 — the GUI is a
thin layer over the scriptable core): an *accept* / *reject* / *un-reject*

* sets the molecule's authoritative [`MOLECULES_DTYPE`][tether.io.schema.MOLECULES_DTYPE]
  ``curation_label`` (its current accept/reject/uncurated state, §5.1), **and**
* appends one fully-provenanced row to ``/labels/table`` (§5.1: ``molecule_key`` +
  labeler + timestamp + source experiment file + ``source`` + effective training
  ``weight`` + the coded ``label_value`` + ``condition_id``).

Both writes are additive **data** under the M0-frozen schema — no group, dataset,
dtype or field changes — so the ``schema-guard`` freeze holds (ADR-0005, ADR-0023).

The curation-label codec (ADR-0023)
-----------------------------------
One small signed codec, [`CurationLabel`][], is shared by *both* the
``/molecules.curation_label`` state field and each ``/labels.label_value`` event.
``curation_label`` reflects the molecule's most recent **human** accept/reject/clear
event; ``/labels`` is the full append-only history (including provisional-source
priors, which never touch ``curation_label``). The codes:

* ``UNCURATED = 0`` — never curated, or **cleared** by an un-reject (matches the
  ``_UNCURATED_LABEL`` an extraction writes, [`tether.imaging.extract`][]).
* ``ACCEPT = +1`` — the ML "good" class.
* ``REJECT = -1`` — the ML "bad" class.

**Reject semantics (§7.5).** A reject is a *reversible sticky tag*, never a
deletion: it persists in ``curation_label`` (and so carries across files on the
stable ``molecule_key``), is one-click reversible via [`unreject`][] (which
clears to ``UNCURATED`` and logs the reversal), and is excluded from default
histograms/idealization through the **toggleable filter** [`curation_filter_mask`][]
(``include_rejected=False`` by default). The reject also lives on as an ML training
label (its ``/labels`` row).

**Reject reasons (#932, PRD §7.2/§7.5).** A human reject also appends one row to the
append-only ``/labels/reject_log`` dataset: the reason (one code of the fixed list
[`REJECT_REASONS`][]), an optional free-text note, the threshold value of a
``"threshold"`` reject, and who rejected and when (the same ``labeler`` and
``timestamp`` as the reject's ``/labels`` row, so the two rows join exactly; ADR-0104). An
[`unreject`][] appends a ``"restore"`` row. The log is created lazily on the first
reject, under the frozen ``/labels`` group, so a fresh store and the ``schema-guard``
manifest are unchanged. The log is a record only: the filter
([`curation_filter_mask`][]), the ranker and every analysis read ``curation_label``
and never the reason.

Scope (M2 S5). This logs **accept / reject / un-reject** — the ML training signal
the M5 ranker consumes. A *category* assignment is **not** logged here: category is
independent of accept/reject (§7.6) and its authoritative home is
``/molecules.category``. Since #358 it is written by
[`tether.project.molecule_edits.set_category`][] / ``clear_category``, which check
the name against the condition's editable category list and log each change to their
own append-only ``/settings/molecule_audit`` — never to ``/labels``, whose
``label_value`` is this accept/reject codec, so a category code there would read as an
ML training label (ADR-0023). Each row's ``weight`` is written at the human full weight here;
the ``source``-driven cold-start decay (``w = w₀/(1+n_human)``, §7.5) that down-weights a
provisional/seed prior is applied at retrain time by
[`tether.project.weighting.recompute_label_weights`][] (ADR-0036), which rewrites this
column from the current label set.
"""

from __future__ import annotations

import getpass
import math
from datetime import UTC, datetime
from enum import IntEnum
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING

from tether.io.schema import LABELS_DTYPE, TABLE

if TYPE_CHECKING:
    import numpy as np

__all__ = [
    "HUMAN_WEIGHT",
    "LABEL_SOURCES",
    "LABEL_SOURCE_CROSS_CONDITION",
    "LABEL_SOURCE_DEEPLASI",
    "LABEL_SOURCE_HUMAN",
    "PROVISIONAL_LABEL_SOURCES",
    "REJECT_EVENT",
    "REJECT_LOG",
    "REJECT_REASONS",
    "REJECT_REASON_OTHER",
    "REJECT_REASON_THRESHOLD",
    "REJECT_REASON_UNSPECIFIED",
    "RESTORE_EVENT",
    "CurationLabel",
    "accept",
    "curation_filter_mask",
    "curation_label_of",
    "curation_labels",
    "default_labeler",
    "read_labels",
    "read_reject_log",
    "reject",
    "reject_record_of",
    "rejected_molecule_keys",
    "set_curation_label",
    "unreject",
]

_MOLECULES = "molecules"
_LABELS = "labels"


class CurationLabel(IntEnum):
    """The signed curation-label codec shared by ``curation_label`` + ``label_value``.

    An `IntEnum` so a member writes straight into the frozen ``<i4``
    fields and reads back comparably as a plain int (ADR-0023).
    """

    UNCURATED = 0  # never curated, or cleared by an un-reject (§7.5)
    ACCEPT = 1  # the ML "good" class
    REJECT = -1  # the ML "bad" class


#: ``/labels.source`` provenance vocabulary (PRD §5.1). Human curation (M2) is
#: full weight; the two provisional sources are down-weighted cold-start priors
#: whose weight decays as human labels accrue (§7.5, applied at M5 retrain).
LABEL_SOURCE_HUMAN = "human"
LABEL_SOURCE_DEEPLASI = "deeplasi-provisional"
LABEL_SOURCE_CROSS_CONDITION = "cross-condition-seed"
#: The two provisional (non-human) sources — cold-start priors the M5 ranker folds into training
#: at a decayed weight (§7.5), as opposed to an authoritative human accept/reject. The one canonical
#: definition of "which sources are seed priors," so the training-fold
#: (`tether.project.gbranking.weighted_training_set`) and this vocabulary never drift apart.
PROVISIONAL_LABEL_SOURCES = frozenset({LABEL_SOURCE_DEEPLASI, LABEL_SOURCE_CROSS_CONDITION})
LABEL_SOURCES = frozenset({LABEL_SOURCE_HUMAN}) | PROVISIONAL_LABEL_SOURCES

#: The effective training weight of a human label (PRD §7.5: "human labels are
#: full weight (1.0)"). Not a §11.2 tunable — it is the fixed 1.0 reference the
#: decay law normalizes provisional-source weights against.
HUMAN_WEIGHT = 1.0

#: Name of the append-only reject-reason dataset under ``/labels`` (created lazily).
REJECT_LOG = "reject_log"
#: ``/labels/reject_log.event`` values: a reject, or the restore of a rejected trace.
REJECT_EVENT = "reject"
RESTORE_EVENT = "restore"

REJECT_REASON_UNSPECIFIED = "unspecified"
REJECT_REASON_THRESHOLD = "threshold"
REJECT_REASON_OTHER = "other"
#: The fixed list of reject reasons (#932), the one place they are defined: code -> meaning.
#: A ``"threshold"`` reject must carry its threshold value; an ``"other"`` reject must carry
#: a note. A reason is a record for a later reader and never changes which molecules an
#: analysis keeps (that is ``curation_label``).
REJECT_REASONS = MappingProxyType(
    {
        REJECT_REASON_UNSPECIFIED: "No reason was given.",
        "no-anticorrelation": "Donor and acceptor do not anti-correlate.",
        "multiple-molecules": "More than one donor or acceptor in the spot.",
        "artifact": "A blink, spike, drift or other artifact makes the trace unusable.",
        "low-signal": "The signal is too low or too noisy to use.",
        REJECT_REASON_THRESHOLD: "Rejected by a score threshold; the record keeps the value.",
        REJECT_REASON_OTHER: "Another reason; the note says which.",
    }
)


def default_labeler() -> str:
    """Best-effort identity for the curator (the OS login), or ``"unknown"``.

    Overridable per call so a shared workstation or a batch run can attribute
    labels explicitly (the ``labeler`` provenance field enables multi-curator
    reconciliation on the stable ``molecule_key``, §7.5/§7.10).
    """
    try:
        user = getpass.getuser()
    except Exception:  # pragma: no cover - env without a resolvable login
        return "unknown"
    return user or "unknown"


def _utc_now_iso() -> str:
    """An ISO-8601 UTC timestamp with explicit offset (sortable, unambiguous)."""
    return datetime.now(UTC).isoformat()


def _to_str(value: object) -> str:
    """Decode an h5py variable-length string field (``bytes`` or ``str``)."""
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _match_indices(mol_keys: np.ndarray, molecule_key: str) -> list[int]:
    """Row indices in ``/molecules`` whose ``molecule_key`` equals ``molecule_key``."""
    return [i for i, k in enumerate(mol_keys) if _to_str(k) == molecule_key]


def set_curation_label(
    path: str | Path,
    molecule_key: str,
    label: CurationLabel | int,
    *,
    labeler: str | None = None,
    source: str = LABEL_SOURCE_HUMAN,
    weight: float | None = None,
    source_file: str | None = None,
    timestamp: str | None = None,
) -> np.ndarray:
    """Append a ``/labels`` event row and (for a human label) set ``curation_label``.

    Resolves ``molecule_key`` against ``/molecules`` (the cross-file join key,
    §5.1/§7.10), appends one fully-provenanced row to ``/labels/table``, and — only
    when ``source`` is human — writes ``label`` into every matching row's
    ``curation_label``. The molecule's ``curation_label`` is the **human**
    accept/reject state (§5.1); provisional ML sources (Deep-LASI / cross-condition
    seeds) are cold-start priors that live **only** in ``/labels`` and must never
    masquerade as a human decision.

    HDF5 ``r+`` is not transactional, so the two writes are ordered for safe
    recovery, not atomicity: the ``/labels`` audit row is written **first**, then
    ``curation_label``. A crash between them leaves at worst an orphan ``/labels``
    row (harmless, re-derivable) rather than an *unaudited* state change — the
    single-writer ``.lock`` + recovery path is the §5.4 concurrency concern (M2 S9).

    Parameters
    ----------
    path:
        The ``.tether`` project to curate (opened ``r+``).
    molecule_key:
        The target molecule's cross-file content identity (§5.1).
    label:
        A [`CurationLabel`][] (or its int value).
    labeler:
        Curator identity; defaults to [`default_labeler`][].
    source:
        One of [`LABEL_SOURCES`][]; human curation is ``"human"`` (default).
    weight:
        Effective training weight; defaults to [`HUMAN_WEIGHT`][] (``1.0``).
        Provisional sources pass their own decayed weight (§7.5, M5).
    source_file:
        The source experiment file the label came from; defaults to the project
        file name (the file being curated), for multi-curator merge-back (§7.10).
    timestamp:
        An **offset-aware** ISO-8601 stamp; defaults to the current UTC time.
        Validated before any write (rejected if unparseable or naive). Injectable
        for tests.

    Returns
    -------
    numpy.ndarray
        A copy of the single appended ``/labels`` row, for the caller/test.

    Raises
    ------
    KeyError
        If no ``/molecules`` row carries ``molecule_key`` (never a silent no-op).
    ValueError
        If ``label`` is not a valid [`CurationLabel`][], ``source`` is not in
        [`LABEL_SOURCES`][], ``weight`` is not finite and non-negative,
        ``timestamp`` is not an offset-aware ISO-8601 string, or the matched rows
        disagree on ``condition_id`` (an ambiguous label scope).
    """
    import h5py  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    label = CurationLabel(label)
    if source not in LABEL_SOURCES:
        raise ValueError(f"source must be one of {sorted(LABEL_SOURCES)}, got {source!r}")
    weight = HUMAN_WEIGHT if weight is None else float(weight)
    if not (math.isfinite(weight) and weight >= 0.0):
        raise ValueError(f"weight must be finite and >= 0, got {weight}")
    labeler = labeler if labeler is not None else default_labeler()
    timestamp = timestamp if timestamp is not None else _utc_now_iso()
    # Validate a caller-supplied timestamp before any file I/O: a bad value would be
    # permanently persisted into the append-only /labels log, breaking the provenance
    # contract (§5.1) and hard to repair. Require an offset-aware ISO-8601 instant.
    try:
        parsed_ts = datetime.fromisoformat(timestamp)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"timestamp must be an ISO-8601 string, got {timestamp!r}") from exc
    if parsed_ts.tzinfo is None:
        raise ValueError(f"timestamp must include an explicit UTC offset, got {timestamp!r}")
    path = Path(path)
    source_file = source_file if source_file is not None else path.name

    with h5py.File(path, "r+") as f:
        mol_table = f[_MOLECULES][TABLE]
        mol_keys = mol_table["molecule_key"][:]
        idx = _match_indices(mol_keys, molecule_key)
        if not idx:
            raise KeyError(f"no molecule with molecule_key {molecule_key!r} in {path.name}")

        # A molecule_key identifies one physical molecule (movie sha256 + quantized
        # donor_xy, §7.10), so every matched row must share a condition_id; a
        # divergence (a corrupt or mis-merged file) would silently mis-attribute the
        # label's condition scope, so refuse rather than pick one.
        cond_col = mol_table["condition_id"][:]
        condition_ids = {_to_str(cond_col[i]) for i in idx}
        if len(condition_ids) > 1:
            raise ValueError(
                f"molecule_key {molecule_key!r} maps to multiple condition_ids "
                f"{sorted(condition_ids)}; cannot log an unambiguously-scoped label"
            )
        condition_id = condition_ids.pop()

        # 1) Append the audit row FIRST (ordered for recovery, not atomicity): a
        #    crash before step 2 leaves a re-derivable orphan row, never unaudited
        #    state.
        label_row = np.zeros(1, dtype=LABELS_DTYPE)
        label_row["molecule_key"] = molecule_key
        label_row["labeler"] = labeler
        label_row["timestamp"] = timestamp
        label_row["source_file"] = source_file
        label_row["source"] = source
        label_row["weight"] = weight
        label_row["label_value"] = int(label)
        label_row["condition_id"] = condition_id

        labels_table = f[_LABELS][TABLE]
        n0 = labels_table.shape[0]
        labels_table.resize((n0 + 1,))
        labels_table[n0:] = label_row

        # 2) Update the molecule's human accept/reject state. Only a human label
        #    owns curation_label (§5.1); a provisional ML seed stays out of it (it
        #    is a /labels-only prior). Read-modify-write per matched row leaves the
        #    other frozen fields untouched.
        if source == LABEL_SOURCE_HUMAN:
            for i in idx:
                row = mol_table[i]
                row["curation_label"] = int(label)
                mol_table[i] = row

    return label_row[0].copy()


def accept(path: str | Path, molecule_key: str, **provenance: object) -> np.ndarray:
    """Accept a molecule: ``curation_label = ACCEPT`` + an ``/labels`` row (§7.5)."""
    return set_curation_label(path, molecule_key, CurationLabel.ACCEPT, **provenance)  # type: ignore[arg-type]


def _reject_log_dtype() -> np.dtype:
    """The ``/labels/reject_log`` compound dtype (additive data, created lazily)."""
    import h5py  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    s = h5py.string_dtype(encoding="utf-8")
    return np.dtype(
        [
            ("molecule_key", s),
            ("event", s),  # REJECT_EVENT | RESTORE_EVENT
            ("reason", s),  # a REJECT_REASONS code; "" on a restore
            ("note", s),  # optional free text
            ("value", "<f8"),  # the threshold of a "threshold" reject, else NaN
            ("labeler", s),
            ("timestamp", s),
            ("condition_id", s),
        ]
    )


def _check_note(note: object) -> None:
    """Refuse a note that ``/labels/reject_log`` cannot store, before any write.

    An HDF5 variable-length string holds UTF-8 text without an embedded NUL, so a note
    with a NUL or a lone surrogate would fail only at the reason write, after the label
    had already changed.
    """
    if not isinstance(note, str):
        raise ValueError(f"note must be a string, got {type(note).__name__}")
    if "\x00" in note:
        raise ValueError("note must not contain a NUL character")
    try:
        note.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"note must be valid UTF-8 text: {exc.reason}") from None


def _check_reject_reason(reason: str, note: str, value: float | None) -> None:
    """Refuse a reason outside [`REJECT_REASONS`][] or a reason missing its detail."""
    if reason not in REJECT_REASONS:
        raise ValueError(f"reason must be one of {sorted(REJECT_REASONS)}, got {reason!r}")
    _check_note(note)
    if reason == REJECT_REASON_THRESHOLD:
        if value is None or not math.isfinite(float(value)):
            raise ValueError("a 'threshold' reject must carry its finite threshold value")
    elif value is not None:
        raise ValueError(f"only a 'threshold' reject carries a value, not {reason!r}")
    if reason == REJECT_REASON_OTHER and not note.strip():
        raise ValueError("an 'other' reject must carry a note that says the reason")


def _append_reject_log(
    path: Path, label_row: np.ndarray, *, event: str, reason: str, note: str, value: float | None
) -> None:
    """Append one row to ``/labels/reject_log``, joined to ``label_row`` by key + who + when."""
    import h5py  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    dtype = _reject_log_dtype()
    row = np.zeros(1, dtype=dtype)
    row["molecule_key"] = _to_str(label_row["molecule_key"])
    row["event"] = event
    row["reason"] = reason
    row["note"] = note
    row["value"] = math.nan if value is None else float(value)
    row["labeler"] = _to_str(label_row["labeler"])
    row["timestamp"] = _to_str(label_row["timestamp"])
    row["condition_id"] = _to_str(label_row["condition_id"])
    with h5py.File(path, "r+") as f:
        group = f[_LABELS]
        if REJECT_LOG not in group:
            group.create_dataset(REJECT_LOG, shape=(0,), maxshape=(None,), dtype=dtype)
        log = group[REJECT_LOG]
        n0 = log.shape[0]
        log.resize((n0 + 1,))
        log[n0:] = row


def reject(
    path: str | Path,
    molecule_key: str,
    *,
    reason: str = REJECT_REASON_UNSPECIFIED,
    note: str = "",
    value: float | None = None,
    **provenance: object,
) -> np.ndarray:
    """Reject a molecule: ``curation_label = REJECT`` + an ``/labels`` row + its reason.

    A reject is a reversible sticky tag (§7.5) — see [`unreject`][] and
    [`curation_filter_mask`][] for the reversal and the exclusion filter. A human
    reject also appends a ``/labels/reject_log`` row with ``reason`` (a code of
    [`REJECT_REASONS`][]), the optional ``note``, and ``value`` (required for a
    ``"threshold"`` reject, refused for any other reason). The reason is checked before
    any write. The ``/labels`` row is written first, so a crash between the two writes
    leaves a reject with no reason row ([`reject_record_of`][] then returns ``None``),
    never a reason for a trace that was not rejected (ADR-0104). A provisional-source reject is a
    ``/labels``-only prior and writes no reason row.
    """
    _check_reject_reason(reason, note, value)
    row = set_curation_label(path, molecule_key, CurationLabel.REJECT, **provenance)  # type: ignore[arg-type]
    if _to_str(row["source"]) == LABEL_SOURCE_HUMAN:
        _append_reject_log(
            Path(path), row, event=REJECT_EVENT, reason=reason, note=note, value=value
        )
    return row


def unreject(
    path: str | Path, molecule_key: str, *, note: str = "", **provenance: object
) -> np.ndarray | None:
    """Reverse a reject, restoring the uncurated state (§7.5, "one-click un-rejectable").

    Acts **only** when the molecule is currently ``REJECT``: it clears
    ``curation_label`` back to ``UNCURATED`` and logs the reversal as its own
    append-only ``/labels`` event (``label_value = UNCURATED``), preserving the
    audit trail rather than erasing the prior reject, and appends a ``"restore"`` row
    with the optional ``note`` to ``/labels/reject_log``. On any other current state it
    is a **no-op returning ``None``** (nothing written) — so it never clobbers an
    accept (§7.5 "never silently drop") and never pollutes the append-only ML log
    with spurious clear rows. Raises `KeyError` if ``molecule_key`` is absent.
    """
    _check_note(note)
    if curation_label_of(path, molecule_key) != CurationLabel.REJECT:
        return None
    row = set_curation_label(path, molecule_key, CurationLabel.UNCURATED, **provenance)  # type: ignore[arg-type]
    if _to_str(row["source"]) == LABEL_SOURCE_HUMAN:
        _append_reject_log(Path(path), row, event=RESTORE_EVENT, reason="", note=note, value=None)
    return row


# --- readers / queries -------------------------------------------------------


def read_labels(path: str | Path) -> np.ndarray:
    """Read ``/labels/table`` back as a structured array (a copy, append order)."""
    import h5py  # noqa: PLC0415

    with h5py.File(Path(path), "r") as f:
        return f[_LABELS][TABLE][:]


def read_reject_log(path: str | Path) -> np.ndarray:
    """Read ``/labels/reject_log`` as a structured array (a copy, append order).

    Returns a length-0 array of the log dtype when no reject has been logged yet (the
    dataset is created lazily on the first human [`reject`][]).
    """
    import h5py  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    with h5py.File(Path(path), "r") as f:
        group = f[_LABELS]
        if REJECT_LOG not in group:
            return np.zeros(0, dtype=_reject_log_dtype())
        return group[REJECT_LOG][:]


def reject_record_of(path: str | Path, molecule_key: str) -> np.ndarray | None:
    """The reason row of the molecule's current reject, or ``None``.

    The current reject is the molecule's latest **human** ``/labels`` event; its reason row
    is the ``"reject"`` row of ``/labels/reject_log`` with the same ``molecule_key``,
    ``labeler`` and ``timestamp``. ``None`` when the molecule is not currently rejected,
    or when its current reject has no reason row (a store written before #932, a reject
    written straight through [`set_curation_label`][], or a crash between the two
    writes, or two human rejects of the molecule that share the same ``labeler`` and
    ``timestamp``, so the join cannot tell them apart) — an earlier reject's reason is
    never returned for a later decision. Raises `KeyError` if ``molecule_key`` is absent.
    """
    if curation_label_of(path, molecule_key) != CurationLabel.REJECT:
        return None
    events = read_labels(path)
    current = None
    for i in range(events.shape[0] - 1, -1, -1):
        if _to_str(events["molecule_key"][i]) == molecule_key and (
            _to_str(events["source"][i]) == LABEL_SOURCE_HUMAN
        ):
            current = events[i]
            break
    if current is None or int(current["label_value"]) != CurationLabel.REJECT:
        return None
    who, when = _to_str(current["labeler"]), _to_str(current["timestamp"])
    same_join = sum(
        1
        for i in range(events.shape[0])
        if _to_str(events["molecule_key"][i]) == molecule_key
        and _to_str(events["source"][i]) == LABEL_SOURCE_HUMAN
        and int(events["label_value"][i]) == CurationLabel.REJECT
        and _to_str(events["labeler"][i]) == who
        and _to_str(events["timestamp"][i]) == when
    )
    if same_join > 1:
        return None  # the join key names more than one reject: no reason can be attributed
    log = read_reject_log(path)
    for i in range(log.shape[0] - 1, -1, -1):
        row = log[i]
        if (
            _to_str(row["molecule_key"]) == molecule_key
            and _to_str(row["event"]) == REJECT_EVENT
            and _to_str(row["labeler"]) == who
            and _to_str(row["timestamp"]) == when
        ):
            return row.copy()
    return None


def curation_labels(path: str | Path) -> dict[str, int]:
    """Map each molecule's ``molecule_key`` to its current ``curation_label`` (int)."""
    import h5py  # noqa: PLC0415

    with h5py.File(Path(path), "r") as f:
        table = f[_MOLECULES][TABLE]
        keys = table["molecule_key"][:]
        labels = table["curation_label"][:]
    return {_to_str(k): int(v) for k, v in zip(keys, labels, strict=True)}


def curation_label_of(path: str | Path, molecule_key: str) -> int:
    """The current ``curation_label`` for ``molecule_key`` (raises if absent)."""
    import h5py  # noqa: PLC0415

    with h5py.File(Path(path), "r") as f:
        table = f[_MOLECULES][TABLE]
        keys = table["molecule_key"][:]
        idx = _match_indices(keys, molecule_key)
        if not idx:
            raise KeyError(f"no molecule with molecule_key {molecule_key!r} in {Path(path).name}")
        return int(table["curation_label"][idx[0]])


def rejected_molecule_keys(path: str | Path) -> set[str]:
    """The ``molecule_key`` set currently tagged ``REJECT`` (the sticky reject bin)."""
    return {key for key, label in curation_labels(path).items() if label == CurationLabel.REJECT}


def curation_filter_mask(molecules: np.ndarray, *, include_rejected: bool = False) -> np.ndarray:
    """A boolean *included* mask over a ``/molecules`` structured array (§7.5).

    The toggleable curation filter the analysis surfaces consume: ``True`` keeps a
    molecule. Rejected molecules (``curation_label == REJECT``) are dropped by
    default (``include_rejected=False``) so histograms/idealization exclude them,
    and kept when the filter is toggled on. Pure over the passed array (no IO), so
    a caller reads ``/molecules`` once and applies the mask.
    """
    import numpy as np  # noqa: PLC0415

    labels = np.asarray(molecules["curation_label"])
    if include_rejected:
        return np.ones(labels.shape, dtype=bool)
    return labels != int(CurationLabel.REJECT)
