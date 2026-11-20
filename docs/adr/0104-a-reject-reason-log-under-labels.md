<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0104 — The reason of a reject is an append-only log under `/labels`

- **Status:** accepted
- **Date:** 2026-11-14
- **Deciders:** bioedca (issue #932 approved 2026-11-14, backlog grooming card "Approve all
  recommended"); this record was added on a Codex finding on PR #1050
- **PRD anchor:** §5.1 (`/labels`), §7.2 ("a reject has a reason"), §7.5 (reject semantics)
- **Milestone:** M16

## Context and problem statement

PRD §7.5 makes a reject a reversible tag, and #932 asks that each reject also store a reason
from a short fixed list, an optional text, who rejected and when, and that a restore be recorded.
The threshold reject of #643 must record the reason `threshold` and its value. The M0-frozen
`LABELS_DTYPE` has no field for any of that, and the schema is additive-only after M0
(PRD §9 M0, `schema-guard`). Where do the reason and the restore live, and how does a reader find
the reason of the *current* reject?

## Decision drivers

- **Additive data, no structural change.** A fresh store, the golden manifest and
  `SCHEMA_VERSION` stay as they are.
- **One home for curation provenance.** `/labels` is the curation log (ADR-0023).
- **No result changes.** The filter, the ranker and the analyses read `curation_label`; a reason
  must not become a second label.
- **Recovery, not atomicity.** HDF5 `r+` is not transactional (the ADR-0023 ordering).

## Decision

1. **Where.** A resizable 1-D compound dataset `/labels/reject_log`, created on the first human
   reject (the `/settings/condition_audit` idiom of ADR-0033). Fields: `molecule_key`, `event`
   (`reject` | `restore`), `reason`, `note`, `value` (`f8`, the threshold of a `threshold` reject,
   else `NaN`), `labeler`, `timestamp`, `condition_id`. Append-only.
2. **The reasons.** One fixed list, `REJECT_REASONS` in `src/tether/project/labels.py`:
   `unspecified` (the default), `no-anticorrelation`, `multiple-molecules`, `artifact`,
   `low-signal`, `threshold` (needs a finite `value`), `other` (needs a `note`). Anything else is
   refused before a write. The list is documented in `docs/reference/tether-format.md`.
3. **Join.** A reason row copies `labeler` and `timestamp` from the reject's own `/labels` row, so
   the two join on `molecule_key` + `labeler` + `timestamp`; no row index is stored, because a
   merge-back appends rows and would move indices.
4. **Write order.** The `/labels` row and `curation_label` first (`set_curation_label`), then the
   reason row. A crash between the two leaves a reject with no reason, never a reason for a trace
   that was not rejected. A note the log cannot store (not a string, an embedded NUL, or text that
   is not valid UTF-8) is refused before the first write.
5. **The current reason.** `reject_record_of` takes the molecule's latest **human** `/labels`
   event. If it is a reject, the answer is the `reject` row that joins it; otherwise, with no
   joining row, or when two human rejects of the molecule share the same `labeler` and
   `timestamp` (the join cannot tell them apart), it is `None`. An earlier reject's reason is
   never returned for a later decision.
6. **Scope of the log.** Only a human reject or restore writes a row; provisional sources stay
   `/labels`-only priors. An `unreject` appends a `restore` row with an optional note.

## Consequences

- A store written before this change reads `None` for every reason; nothing needs migrating.
- The reason is a record for a later reader. No analysis, filter or training path reads it.
- A subset export copies reject-log rows for its selected molecules. Split-file merge-back
  copies a reason when its join identifies exactly one matching human label of the
  corresponding decision. It skips ambiguous joins (#1051).
- A future reason is a new code in `REJECT_REASONS` plus a row in the format page; the dtype does
  not change.

## Considered options

- **A new field in `LABELS_DTYPE`.** Rejected: a structural change to a frozen table, which needs
  a `SCHEMA_VERSION` bump and breaks every reader of the M0 table.
- **Encode the reason in `/labels.source` or `/molecules.tags`.** Rejected: `source` drives the
  training weight (ADR-0036) and `tags` is free text with other users; either would mix a record
  into a value that code reads.
- **A dataset under `/settings`.** Rejected: `/settings` holds run settings and a subset export
  copies it verbatim, so every reason would travel with every subset whatever was selected.
- **Join by `/labels` row index.** Rejected: a merge-back appends rows, so an index is not stable
  across files; key + who + when is.
