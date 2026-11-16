<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0112 — Every publishing writer holds its destination lock, and one helper names a file

- **Status:** accepted; amends [ADR-0055](0055-gui-session-writer-ownership.md) on the GUI write
  freeze and the file identity
- **Date:** 2026-11-15
- **Deciders:** bioedca
- **PRD anchor:** §5.4 (single writer), §7.10 FR-CONCURRENCY
- **Milestone:** M12
- **Issue:** [#346](https://github.com/bioedca/tether/issues/346), which absorbs
  [#249](https://github.com/bioedca/tether/issues/249) and defect 1 of
  [#257](https://github.com/bioedca/tether/issues/257)

## Context and problem statement

Three writers build a complete project at a temporary sibling and publish it with one
`os.replace`: `extract_movie`, `reconstruct_project` and `import_analysis_only_project`. The
replace gives the destination name a **new inode**. A writer that already holds the project open —
a GUI session, a batch job — keeps its descriptor on the old inode, and every later write it makes
goes to a file that no name points to any more. The single-writer lock (§5.4) did not stop this:
`extract_movie` took an *optional* `publish_guard` that only the batch resume path passed, and the
other two asserted the lock once at the start, which a holder that is this same process passes by
identity alone.

#249 recorded the defect "conceptually". The regression test this ADR adds
(`test_second_process_cannot_replace_a_project_another_process_holds`) makes it real: on the tree
before this change, process B's `extract_movie(..., overwrite=True)` replaced the project that
process A held, and the test fails there.

Separately, three places each built their own idea of "the same file": the GUI writer registry
(`_project_owner_key`), the guard-sharing check in `lock.py`, and nothing at all in the publish path.

How does Tether make a destructive publish respect the writer lock, and what is the one definition
of "the same file"?

## Decision drivers

- §5.4: one writer at a time; a writer that holds the lock must never lose its file to another.
- A guard that is optional is a guard most callers forget: before this change one production caller
  passed it.
- The GUI session holds the lock under this process's `(host, user, pid)` identity, so any check
  that accepts "this identity already holds it" lets an in-process publish replace the open session.
- #249's decided identity: `(st_dev, st_ino)`, built by one helper only.

## Considered options

1. **Keep the guard optional; document it.** Rejected: that is the state that produced the defect.
2. **Have each writer acquire the lock itself.** Rejected: the CLI, batch and the Deep-LASI wizard
   each hold the destination for different spans (batch holds it from before the existence check to
   after the last stage), so a writer that acquired its own lock would either double-claim or release
   a claim its caller still needs.
3. **Make the guard mandatory for an existing destination, and give callers one strict helper that
   supplies it** (chosen).

## Decision outcome

Chosen option: **3**.

### The mandatory-guard rule

- `extract_movie`, `reconstruct_project` and `import_analysis_only_project` take
  `publish_guard: Callable[[], None] | None`. To replace a path where **any** entry exists they
  require one, and refuse with `PublishGuardRequiredError` (a `LockError`) otherwise — before any
  movie or source I/O, and again at publish time. `extract_movie` reports the first refusal as its
  own `ExtractionError`, like its other input errors.
- With a guard, the guard runs immediately before the one `os.replace`, and an exception from it
  aborts the publish with the destination unchanged.
- Without a guard, a new path is published with `os.link(stage, destination)`, which fails rather
  than replaces when another writer created the destination meanwhile, and the stage is then
  unlinked. Where the filesystem has no hard links, an exclusive create (`O_CREAT | O_EXCL`)
  reserves the name instead, failing the same way, under the destination's writer lock held from
  the reservation to the `os.replace` that fills the placeholder.
- `lock.publish_lock(path)` is the helper callers use. It claims the destination **strictly**
  (`allow_existing_owner=False`) under a process-local reservation, so a lock that this process
  already holds — an open GUI window — is refused like any other. It yields a guard that requires
  the sidecar to carry this claim's nonce, so a steal during a long build stops the publish, and it
  releases the lock nonce-checked on exit.
- Callers: `tether extract --overwrite` holds `publish_lock` across the run. The Deep-LASI wizard
  holds it per acquisition when overwrite is on. Batch keeps its own job-level strict claim for a
  rejected checkpoint (ADR-0056) and holds `publish_lock` for any other overwrite of an existing
  output.
- `guarded_project_write` (the staged-copy path of the correction, γ, leakage, photobleach and
  idealization writers) takes the canonical file's identity before its copy and refuses to publish
  if the identity changed, so a staged copy of a replaced file never overwrites the newer file.

### The file identity

`lock.file_identity(path)` is the one canonical helper: `(st_dev, st_ino)` of the file `path`
names, following symbolic links, so every hard-link spelling has the same identity in every
process. It returns `None` for a missing path and for a filesystem that reports `st_ino == 0`,
which cannot prove that two names are one file. The GUI registry and the lock module call it; a
test asserts no other source file mentions `st_ino`.

### The GUI write freeze

ADR-0055's freeze list — the controls disabled while background idealization owns the HDF5 file —
now includes the Deep-LASI import, and `import_deeplasi_bundle` refuses while idealization runs.
The import publishes whole projects and could target the loaded one. (The GUI has no extraction
action yet; FR-EXTRACT-UI adds one, and it belongs on the same list.) The import needs no writable
session: each output is written under its own `publish_lock`, which refuses the loaded project
while this shell holds it.

### Reopening the loaded project

Reopening the loaded project, by its own name or by a hard-link alias, already reused and
refreshed the retained epoch (ADR-0055); with the registry on `file_identity`, a test now covers
the alias spelling too. That closes #257's defect 1.

### Consequences

- Good: a project that any cooperating writer holds can no longer be replaced under it — from
  another process, another thread, or the same process's GUI.
- Good: one definition of "the same file", shared by the lock module and the GUI.
- Trade-off: a library caller that replaced an existing project without a guard now gets
  `PublishGuardRequiredError` (`ExtractionError` from `extract_movie`) and must use `publish_lock`. That is the purpose of the rule.
- Trade-off: `tether extract --overwrite` and the wizard now refuse a project that this user has
  open in a Tether window; the user closes it there first.
- Not decided here: where the lock sidecar lives for hard-link aliases across processes. #249's
  2026-08-03 policy moves it to a per-user directory, which conflicts with PRD §5.4 and §7.10's
  `<file>.lock` beside the project; that choice is with the maintainer, and its implementation and
  its amendment of this record close #249.
- Enforced by: `tests/test_extract_cli.py` (the two-process regression, refusal before movie I/O,
  the no-replace new-path publish), `tests/test_reconstruct.py` and `tests/test_analysis_import.py`
  (unguarded refusal, guard before replace, foreign lock), `tests/test_lock.py` (identity, strict
  publish lock, steal detection, the single `st_ino` site), `tests/test_project_guarded_publish.py`
  (replaced canonical file), `tests/test_project_batch.py` (batch overwrite under the lock) and
  `tests/test_shell_load_project.py` (the freeze, alias reopen, the wizard refused on the loaded
  project).

## More information

- [ADR-0055](0055-gui-session-writer-ownership.md) — GUI session ownership and the freeze list.
- [ADR-0056](0056-policy-fail-rejected-extraction-checkpoint.md) — the batch strict claim.
- [ADR-0096](0096-the-lock-guard-is-shared-with-directory-writers.md) — the shared guard.
- `src/tether/project/lock.py` (`file_identity`, `publish_lock`), `src/tether/project/_guarded_publish.py`.
