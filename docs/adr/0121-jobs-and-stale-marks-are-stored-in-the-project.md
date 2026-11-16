<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0121 — Jobs, their checkpoints and the stale marks are stored in the project

- **Status:** accepted
- **Date:** 2026-11-16
- **Deciders:** bioedca (issue #895 approved 2026-11-14, plan item B1 of #890)
- **PRD anchor:** §7.13 (FR-WORKFLOW), §7.10 (FR-CONCURRENCY), §5 (data model)
- **Milestone:** M14

## Context and problem statement

The workflow design asks that a long step runs in the background, keeps completed work, can be
cancelled and retried, and continues after an interruption (G02 “Checkpointed extraction job”, G04
“Run model jobs in background”, G06 “Reconcile server state”). G05 has one “Job + dependency
coordinator” for every stage. Today a background task lives only in the Qt layer (#343) and
nothing is stored, so the command line and a restarted application cannot continue a run.

ADR-0103 decides the dependency graph and says that a record is stale when an input identity that
it recorded differs from the current one, and that a stored mark is a cache of that comparison. It
leaves the store layout of the recorded identities and of the marks to #895.

Where is a job stored, what does it hold, when is a running job interrupted, and where are the
recorded inputs and the marks stored?

## Decision drivers

- **The command line and the window see one run.** A job must outlive the process that started it.
- **Additive only** (ADR-0005, §9.1): no frozen field changes, and `schema_version` stays 1.
- **One writer; a reader never blocks** (§7.10). Outside SWMR mode, which Tether does not use, a
  read of a file that another process holds open for writing “would fail or result in garbage
  data” (h5py documentation, `docs/swmr.rst`, read through Context7 on 2026-11-16 for h5py 3.16.0,
  the locked version). So the coordinator must not hold the file open while a part works.
- **Qt-free** (§7.11). #343 becomes the Qt adapter of this coordinator.

## Considered options

- **A. Three lazily created compound datasets under `/settings`**: `jobs` (one row per job, changed
  in place as the job runs), `recorded_inputs` and `stale_marks` (append-only).
- **B. A sidecar file beside the project** (`<file>.jobs.json`), like the lock.
- **C. One group per job** with scalar attributes.

## Decision outcome

Chosen option: **A**, implemented in `src/tether/project/jobs.py`.

B is lost when a project is copied or shared without its sidecar, and a job's checkpoint describes
data inside the file, so it belongs with that data. C writes one new group for each job, which a
reader must walk; a table is read in one call and follows the `/settings/condition_audit` and
`/settings/molecule_audit` idiom that already exists.

1. **The job record.** One row of `/settings/jobs` per job, every field a UTF-8 string: `job_id`,
   `command`, `request` (the ADR-0100 text form), `state`, `parts`, `completed` (the checkpoint),
   `inputs` (identity of each input, keyed by node and instance of ADR-0103), `owner`,
   `created_utc`, `started_utc`, `ended_utc`, `error`, `app_version`. JSON is used inside the
   strings that hold a list or a mapping, so a later field of an object is an additive change.
2. **The states.** `queued`, `running`, `done`, `failed`, `cancelled`, `interrupted`. A job does its
   parts in order and stores each completed part before it starts the next, so a retry of a failed,
   cancelled or interrupted job does only the parts that are not in `completed`. A part's own writes
   are its owner's; a part that is not complete must leave the store as it found it.
3. **The owner and interruption.** A job records the lock epoch (the nonce of `<file>.lock`) that it
   started under and checks it before every write, so a steal stops it. A stored `running` job has
   no live owner when the lock no longer carries that nonce, when its process on this computer is
   not running, or when it is this process and no run of the job is active here. A reader reports
   such a job as `interrupted` and writes nothing; the next writer stores `interrupted`
   (`reconcile_jobs`). A lock that cannot be read counts as a live owner, so a reader never reports
   an interruption by mistake. Another computer's process is judged by the nonce alone, as the lock
   judges it (PRD §5.4).
4. **The file is open only to write.** The coordinator opens the project for each short write and
   closes it before the next part, so a reader in another process opens the file while a job runs.
5. **Recorded inputs and stale marks.** `/settings/recorded_inputs` has one row per direct input of
   a record, written once when the record is made; a changed record is a new instance.
   `set_stale_marks` is the one function that sets marks: it marks each record that recorded the
   edited instance with another identity, through the edge between them, and then each record made
   from a marked record, with the stop of `CONTENT_COMPARED` (ADR-0103, decision 2). A guarded edge
   is crossed only when the caller, the owner of the downstream record, says the guard holds. Marks
   are append-only and never removed (ADR-0103, decision 3).

**Schema version: no change.** The three datasets are absent from a fresh project, so
`schema/schema_frozen.json` does not change and `scripts/dump_schema.py --check` passes.

### Consequences

- Good: a run started in the window can be continued from the command line, and a crash costs at
  most the part that was running.
- Good: one function sets the marks for every edge, so #896 and #925 read the same marks.
- Trade-off: a part is the unit of resumption. Work inside a part is redone after an interruption,
  so an owner chooses parts small enough to redo (a movie, a model).
- Trade-off: a mark is a cache; when it and the comparison of identities differ, the comparison
  wins (ADR-0103). Code that needs certainty compares the identities.
- Follow-up: #343 shows a job's progress and cancel in the window; #896 reports job states in
  `tether status`; #899 runs a recipe as jobs.

## More information

- Issue #895; plan #890. ADR-0103 (the graph), ADR-0100 (requests), ADR-0112 (writer lock),
  ADR-0005 (schema freeze). Tests: `tests/test_jobs.py`. Store layout:
  [`docs/reference/tether-format.md`](../reference/tether-format.md).
