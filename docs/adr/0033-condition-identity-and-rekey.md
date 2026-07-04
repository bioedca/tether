<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0033 — Condition identity (content-hash, keep-separate) + transactional re-key with human-confirmed merge

- **Status:** accepted
- **Date:** 2026-07-04
- **Deciders:** bioedca (maintainer)
- **PRD anchor:** §5.1 (condition identity + referential validation), §7.6 (human-validated filename auto-fill), §9 M4
- **Milestone:** M4

## Context and problem statement

PRD §5.1 makes an experimental **condition** — not a movie or a file — the unit that
metadata, curation labels, and per-condition corrections attach to. A condition spans many
movies across many days/files, so its identity must be **content-derived** (two movies are
the same condition iff their chemistry/optics key matches), and it must survive the
provisional, best-effort filename parse (§7.6), which requires **human validation** at M4.

Two decisions were left unhomed by the M4 work so far:

1. **Condition identity + keep-separate-by-default** (implemented in PR #83, `feat(io):
   structured condition fields + referential validation`, but with its ADR deliberately
   deferred to this PR): what makes two acquisitions "the same condition," and what happens
   to near-miss strings.
2. **The transactional re-key + human-confirmed merge** (this PR): when a molecule's
   provisional `condition_id` is wrong, how it is corrected without corrupting the store or
   silently collapsing conditions.

The design tension for (2): HDF5 `r+` is **not** journaled, yet re-keying "all affected
molecules" must be all-or-nothing (a half re-keyed store is undetectably corrupt), and a
merge that folds ~100 videos into one condition must **never** happen silently (§5.1). This
must hold under the M0 schema freeze (additive data only) and behind the headless
`tether.project` core (the GUI is a thin layer, §7.11).

## Decision

**Identity = a content hash of the exact key (keep-separate by default).** A condition's id
is `cond-<12 hex>` = SHA-256 of the canonical JSON of the six-field
`ConditionKey` (construct/variant, dye, ligand + concentration, buffer, temperature, laser
power); `date`/`replicate`/source-file deliberately vary *within* a condition and are not
identity. Because the id hashes the **exact** key, two movies that parse to slightly
different strings ("T-box" vs "Tbox") get **different** ids and stay **separate** conditions
— never fuzzy-matched or auto-merged. Referential validation is exact: a `condition_id` is
valid only when it resolves to a `/conditions` row *built from that key* (the row exists
**and** its fields canonically hash back to its own id), so `validate_conditions` reports
**dangling** references (no row) and **inconsistent** rows (fields edited away from the id).

**Correcting a wrong id = `rekey_condition` (transactional) + human-confirmed merge.** Add a
Qt-free `tether.project.conditions.rekey_condition(path, from_condition_id, to_key, *,
confirm=False, …)` that, in one `h5py` `r+` session:

1. **materializes the destination `/conditions` row** from the corrected `to_key`
   (idempotent, insert-only) so the re-keyed molecules resolve — never left dangling;
2. **re-keys every affected `/molecules` row in a single full-table write**
   (`data = table[:]; data["condition_id"][mask] = to_id; table[:] = data`): one `H5Dwrite`
   moves all affected rows together, so no molecule is ever left half re-keyed (the
   "transactional" guarantee available without a journal — and any residual partial state
   is still **detectable** by `validate_conditions` and repairable, never silent);
3. **appends one provenance-stamped row** to an append-only `/settings/condition_audit` log
   (event · from/to id · count · labeler · timestamp · reason · app version).

**Merge is human-confirmed.** The operation is a *merge* iff the destination id already has
members (disjoint from the source, since a molecule holds exactly one id) — i.e. two
conditions would collapse into one. A merge raises `ConfirmationRequired` unless
`confirm=True`; a plain correction into an **empty** destination (nothing collapses)
proceeds without it. A read-only `preview_rekey` returns the affected `molecule_key` set,
`is_merge`, and the destination's current members, so the GUI (next M4 PR) shows the effect
before the user confirms.

## Scope and consequences

- **Additive under the M0 freeze.** The audit log is a **lazily-created** resizable dataset
  `/settings/condition_audit` under the frozen `/settings` container (the `/settings/batch`
  provenance idiom, ADR-0030) — absent from a fresh project, so `build_manifest` is
  unchanged and `schema-guard` stays green. No `/molecules`/`/conditions` dtype, field, or
  group change; the re-key rewrites only *data* in the frozen `/molecules/table`.
- **No new §11.2 tunable, no conda-lock change.** Re-key is pure data movement over the
  existing store; identity uses the already-frozen key + SHA-256.
- **Provenance travels with the change (NFR-REPRO).** Every re-key is an append-only,
  timestamped, labeler-attributed, app-version-stamped audit event; the timestamp is
  validated offset-aware before any write (as `/labels`, ADR-0023), so a bad stamp cannot
  enter the permanent log.
- **Never a silent no-op or silent merge.** Re-keying an absent id raises `KeyError`; an
  empty source id or a to-key that hashes back to the source raises `ValueError`; a merge
  without `confirm` raises `ConfirmationRequired`.
- **Headless core only.** The confirm/correct + merge **dialogs** are the next M4 PR, a thin
  layer over `preview_rekey`/`rekey_condition` (computer-use GUI gate applies there, not
  here). This mirrors the codebase's headless-core-then-GUI split (M2 S6/S8, M3 histogram).

## Alternatives considered

- **Fuzzy/auto-merge of near-miss keys** — rejected: §5.1 mandates keep-separate-by-default;
  a wrong silent merge of ~100-video conditions is unrecoverable. Merging is explicit and
  human-confirmed.
- **Per-row read-modify-write for the re-key** (the `/labels` pattern) — rejected here: it
  widens the crash window across N molecules. A single full-table write of the metadata-sized
  `/molecules` table is the most atomic option `h5py` offers and round-trips every other
  field exactly.
- **A frozen `/audit` table declared at M0** — rejected: it would enlarge the frozen
  skeleton for a feature that is naturally additive; the lazily-created `/settings` dataset
  keeps the freeze minimal (the `/settings/batch` precedent).
- **Writing the audit event before the molecule re-key** (the `/labels` ordering) — rejected:
  a crash would leave a *phantom* audit of a re-key that never applied. Materialize
  destination → re-key (single write) → audit means a crash before the audit leaves a
  self-consistent, `validate_conditions`-clean store (an un-logged but correct re-key),
  which is preferable to a logged-but-unapplied one.
