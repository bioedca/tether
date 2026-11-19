<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0121 — Each lock takeover leaves a record in the project

- **Status:** accepted
- **Date:** 2026-11-16
- **Deciders:** bioedca
- **PRD anchor:** §7.10 FR-CONCURRENCY, §5.4 (single writer), §5 (schema)
- **Milestone:** M14
- **Issue:** [#1099](https://github.com/bioedca/tether/issues/1099), the follow-up of
  [#626](https://github.com/bioedca/tether/issues/626)

## Context and problem statement

\#626 gave the read-only banner **Recover** (a crashed session of this user on this computer) and
**Take over…** (a stale lock of another computer or user, or a corrupt lock). Each successful
takeover is logged at WARNING on the `tether.project.lock` logger. A log line ends with the session
that wrote it, so a later reader of the `.tether` cannot tell that a takeover happened, or whose
unsaved work it may have cost. The maintainer chose the log line for #626 and asked for a durable
record later.

Where does the record live, what does it hold, does it need a schema-version step, and who writes it?

## Decision drivers

- PRD §5 and ADR-0005: the schema is additive-only after M0, and `schema-guard` fails a removal,
  rename or dtype change. A structural change raises `SCHEMA_VERSION` and brings the
  backup-and-migrate path of §5.4 with it.
- A project written before this change must still open, and must read as having no takeovers.
- A refused or failed takeover must leave no record: the record says a takeover happened.
- Writes to the `.tether` go through the single-writer check (§5.4); the lock owner writes.
- The lock file format is out of scope (#1099), and so is recording ordinary acquire and release.

## Considered options

1. **A lazily created compound dataset `/settings/lock_takeovers`**, one row per takeover, written
   by the caller after it has reopened the project writable under the new lock.
2. **A new top-level group `/lock_history`** declared in the skeleton by `create_project`.
3. **The same dataset, written by `recover_lock` itself**, inside the lock call.
4. **A JSON attribute on the root group**, rewritten on each takeover.

## Decision outcome

Chosen option: **1, `/settings/lock_takeovers`, written after the writable reopen.**

The record is additive **data** under the `/settings` container that M0 already declares, created
on the first takeover — the idiom of `/settings/condition_audit` and `/settings/molecule_audit`.
The skeleton `create_project` writes does not change, so `scripts/dump_schema.py --check` passes
unchanged, `schema/schema_frozen.json` is not regenerated, and **`SCHEMA_VERSION` stays 1**. That
is the version step §5 requires: none, because §5 asks for one only for a structural change, and
PRD §5.4 says the first such step is the one that brings backup-and-migrate. A project written
before this change has no dataset; `read_takeovers` returns zero rows of the record's dtype for it.

Option 2 is a structural change. It would raise `SCHEMA_VERSION` for a log, and so make this the
first schema step and owe the backup-and-migrate path for a project that needs no migration.
Option 3 writes before the application has a writable session: `recover_lock` returns, the GUI then
reopens, and a reopen that comes back read-only gives the lock back. A record written inside the
lock call would then name a takeover the user was told failed. It would also make the lock module,
which needs only a path today, open HDF5 under the lifecycle guard. Option 4 rewrites a growing
value in place, which is the opposite of append-only, and an attribute has a size ceiling.

**The row.** `/settings/lock_takeovers` is a resizable 1-D compound dataset, one row per takeover,
in append order. Its fields, strings unless stated:

| Field | Meaning |
|---|---|
| `timestamp` | The new lock's acquisition instant, offset-aware ISO-8601 UTC: when the takeover happened. |
| `action` | `recover` or `take_over`, the two `RecoveryAction` values that take a lock. |
| `holder_user`, `holder_host`, `holder_pid` (`i8`) | The new holder. |
| `ousted_corrupt` (`u1`) | `1` when the ousted lock could not be parsed, so its holder is unknown. |
| `ousted_user`, `ousted_host`, `ousted_pid` (`i8`) | The ousted holder; `""`, `""` and `-1` for a corrupt lock. |
| `ousted_timestamp` | The ousted lock's last acquire or refresh; `""` for a corrupt lock. |
| `app_version` | The Tether version that wrote the row. |

**Who writes it.** `Project.record_lock_takeover(action, taken, ousted)`, with the pair
`recover_lock` returned, through the same `_assert_writable` check as every other `Project`
writer. The application calls it once `load_project` has reopened the project writable, so a
refused takeover (`LockedError`), a failed one, and one whose reopen is read-only write nothing.
`recover_lock` itself still never writes the `.tether`. A record that cannot be written does not
undo the takeover — the ousted holder has already lost the lock, and giving it back would not
restore their work — so the shell keeps the lock, logs the failure at WARNING on
`tether.project.lock`, and says so in its status line.

**Appending.** The dataset is resized by one and the row assigned; if the assignment raises, the
dataset is shrunk back before the error propagates, so a failed append never leaves a blank row.

### Consequences

- Good: the takeover survives the session, travels with the file, and names whose work was lost.
- Good: no schema step, no migration, and old projects need no change to open.
- Good: the record and its reader are headless; the GUI adds only the one call after the reopen.
- Trade-off: the row stores user and host names, as `/labels.labeler` and the audit logs store
  user names. `export_subset_tether` copies `/settings` verbatim, so a subset copy carries the
  source's takeover history; the share-copy option that replaces stored user names
  ([#620](https://github.com/bioedca/tether/issues/620)) has to cover these fields too.
- Trade-off: a takeover made by a script that calls `recover_lock` and never
  `record_lock_takeover` leaves no row. The WARNING log line is still written.
- Follow-up: `tests/test_lock_takeovers.py` (both actions, the corrupt lock, the refusal, a writer
  without the lock, a failed append, a project from before this change) and
  `tests/test_shell_lock_recovery.py` (the banner actions record once, a refusal or a failed or
  read-only reopen records nothing, a failed record keeps the lock).

## More information

- Code: `src/tether/project/lock_takeovers.py`, `Project.record_lock_takeover` and
  `Project.read_lock_takeovers` in `src/tether/project/core.py`, the call in
  `TetherShell._take_project_lock` in `src/tether/gui/shell.py`.
- Related: [ADR-0005](0005-m0-schema-freeze.md) (the schema freeze),
  [ADR-0055](0055-gui-session-writer-ownership.md) (the GUI session owns the writer),
  [ADR-0112](0112-every-publishing-writer-holds-its-destination-lock.md) (the lock a recovery takes).
- Docs: `docs/troubleshooting.md` ("The project is locked by another user or machine") and
  `docs/reference/tether-format.md` (`/settings`).
