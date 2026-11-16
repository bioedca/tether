<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0112 — The condition model has its own single-writer lock, and a project records it under `/models`

- **Status:** accepted; completes the store integration that [ADR-0035](0035-portable-model-persistence.md) deferred
- **Date:** 2026-11-15
- **Deciders:** bioedca (the acceptance criteria of #561, approved 2026-11-13)
- **PRD anchor:** §5.1 (`/models`), §5.4 (single writer, steal-lock), §7.5 (FR-ML), §7.10 FR-CONCURRENCY
- **Milestone:** M16
- **Issue:** [#561](https://github.com/bioedca/tether/issues/561)

## Context and problem statement

[ADR-0035](0035-portable-model-persistence.md) made the quality ranker a standalone portable
file that travels with a condition, and deferred two things: the `.tether` `/models` **reference**
to that file and the file's **own single-writer owner-curator lock** (PRD §5.1 `/models`, §7.10).
Without the lock, `save_model` is only an atomic `os.replace`. When two machines or two sessions
retrain the same condition's model from the same version, the second save silently discards the
first retrain.

## Decision drivers

- Use the lock machinery that already exists (`tether.project.lock`: the `(host, user, pid)`
  identity, the wall-clock staleness timeout, the explicit steal, the OS lifecycle guard), not a
  second lock design.
- A retrain must never be lost without an error. A refusal is acceptable; a silent overwrite is
  not.
- `tether.ml.persistence` stays store-free (ADR-0035), so the integration lives in
  `tether.project`.
- No change to the frozen schema: `SCHEMA_VERSION` stays `1` and `schema-guard` stays green.

## Considered options

1. **Lock only.** Take `<model>.lock` around load → retrain → save. This refuses a second writer
   while the first holds the lock, but a writer whose lock was stolen as stale, or a program that
   calls `save_model` without the lock, can still overwrite a newer version.
2. **Version check only.** Compare the file's hash at load and at save, with no lock. This catches
   a lost update, but only after the second curator has done the retrain, and it gives no owner to
   show and no steal to offer.
3. **Lock plus a version check at save time** (chosen). The lock makes the owner-curator the only
   writer. The save runs under the lock's OS lifecycle guard, checks that the session still holds
   the lock (same nonce), checks that the file is still the version the session opened, and checks
   that the model to save is a retrain of that version (its recorded parent).

## Decision outcome

Chosen option: **3**, in the new module `tether.project.model_artifact`.

- **The lock is the project lock, on the model file.** `model_writer(path)` takes `<path>.lock`
  (with its `.guard`) through `tether.project.lock`. It refuses **any** existing lock, also one
  with this process's own identity (the strict claim, `allow_existing_owner=False`): two sessions
  in one program would otherwise both pass. A stale lock is reported as stale on the
  `LockedError` and is recovered only by `steal=True`, the PRD §5.4 rule. The release on exit is
  nonce-checked.
- **The save is checked under the guard.** A new public `lock.holding(path, held)` runs a block
  under the same OS guard as acquire, steal, refresh and release, after it checks that the sidecar
  still carries the session's nonce. `ModelWriter.save` writes inside that block, and only if the
  file's SHA-256 is still the version the session opened (or last saved). Otherwise it raises
  `LockedError` (the lock was stolen) or `ModelArtifactConflictError` (the file changed without
  the lock), and the file on disk is not touched. The block is one file write, because the guard
  makes other lifecycle calls wait only a few seconds; the retrain happens before it.
- **The model must be a retrain of the version on disk.** `warm_start_retrain` now records the
  version of the model it came from as `parent_version`, where a model's version
  (`tether.ml.persistence.model_version`) is the SHA-256 of its canonical manifest, so a reloaded
  model has the version of the one saved. Every fit and retrain also records a random `fit_id`, so
  two retrains of one version with the same data and the same clock are still two versions. The save refuses, with `StaleModelError`, a model whose
  `parent_version` is not the version of the model on disk: for example one retrained from a copy
  loaded before another curator's save, which the hash check cannot see because the session
  opened after that save. Counts and times are not used, because an old copy retrained more
  often than the model on disk would pass them. `parent_version` is an optional manifest key, the
  `source_projects` precedent (ADR-0077), and so is `fit_id`, so `MODEL_FORMAT_VERSION` stays 1;
  an artifact saved before them loads with `parent_version = None` and `fit_id = None`, and a
  retrain of that artifact names it as its parent. `replace=True` replaces the model on purpose (a fresh fit); the lock and the hash check
  still apply.
- **The project is checked before the model file changes.** When `save` is given a project, the
  project's lock (a `Project` handle with its own writer identity, or this process for a path)
  and the condition are checked first, so a project that already refuses the reference refuses
  the save too. The check does not hold the project's lock: if another writer takes it between the
  check and the reference, the model is saved (no retrain is lost) and the reference write is
  refused, so `ModelReference.is_current()` reports the stale reference. A caller that must rule
  this out holds the project's lock for the session.
- **One session loads or saves one model at a time.** `ModelWriter.load` and `ModelWriter.save`
  are serialized per session, so two threads that share one session cannot both pass the version
  check before either writes, and a load cannot cache an older version after a save.
- **The reference is additive data in `/models`.** `record_model_reference` writes one group per
  condition, `/models/<condition_id>`, with attributes `reference_version` (`1`),
  `condition_id`, `artifact_path` (absolute), `artifact_sha256` (the version the project last
  saw), `n_retrains`, `model_updated_utc`, `recorded_utc` and `app_version`. A new save replaces
  the condition's attributes. It is a canonical write, so it is refused while another writer
  holds the **project's** lock, and the project must hold a molecule of the condition.
  `ModelReference.is_current()` compares the recorded hash with the file on disk.
- **No identity in the reference.** The owner's `user@host` is in the model's lock sidecar while
  the owner holds it. The reference does not copy it, because a subset export copies `/models`
  verbatim into a file that is meant to be shared.

## Consequences

- `SCHEMA_VERSION` is unchanged. `/models` was forward-declared empty at M0; its children are not
  in the frozen golden, and the reference carries its own `reference_version`, the
  `/features/table` precedent.
- `tether info` now lists the condition ids under `/models` for a project that records a model.
- A plain `tether.ml.persistence.save_model` call is still unguarded; it is the low-level
  primitive. A guarded session detects such a write and refuses to overwrite it.
- The guard is held for one model write. A model file is small (one gradient-boosting ranker), so
  this is well inside the guard's wait; a much larger artifact would need the write moved to a
  sibling file published by one guarded replace.
- Out of scope, unchanged from ADR-0035: source weighting, cold-start decay, the drift flag and the
  active-learning badge; the GUI controls are #640.
