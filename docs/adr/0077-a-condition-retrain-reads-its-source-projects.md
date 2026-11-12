<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0077 — A condition's retrain reads the labels from its source projects

- **Status:** accepted
- **Date:** 2026-11-12
- **Deciders:** bioedca
- **PRD anchor:** §7.5 (FR-ML), §5.1 (`/labels`, `/models`, the project definition), §5.4, UC3
- **Milestone:** M16

## Context and problem statement

ADR-0035 makes a warm-start retrain refit the portable ranker on every human label the condition
has: "the caller supplies the accumulated `(X, y)` from `/features` ⋈ `/labels`". ADR-0068 makes
one project hold one experiment, and a condition can span several projects. The labels of one
condition are then in several project files.

On `main` at `67cfa5f`, nothing read `/features` and `/labels` from more than one project. A
retrain started from one project saw only the labels of that project. Two results followed
([#678](https://github.com/bioedca/tether/issues/678)):

- The model forgot what it learned from the earlier experiments of the condition, unless the
  caller collected their labels by hand.
- The cold-start decay `w = w₀ / (1 + n_human)` (PRD §7.5) counted the human labels of one file
  only, so the seed labels kept too much weight on the second day.

Where does a retrain get the accumulated label set of a condition that spans projects?

## Decision drivers

- **`/labels` is the label store of record** (PRD §5.1). A label that a curator changes in an
  older project must reach the next retrain.
- **A project has one writer at a time** (PRD §5.4, ADR-0055). A retrain from one project must not
  take the lock of another, or write to it.
- **A placeholder is never hydrated automatically** (PRD §5.4). A project on OneDrive can be a
  placeholder whose data is not on the disk.
- **A partial label set must not pass silently.** A retrain that skips a project trains a model
  that looks complete and is not.

## Considered options

- **A. Read the source projects.** The model file records the project files it was trained on. At
  a retrain, the library reads `/features` ⋈ `/labels` for the model's condition from each of them,
  read-only and without their locks, and adds the rows of the open project.
- **B. Keep the rows in the model file.** The model file stores its accumulated training rows, so a
  retrain never opens an older project.

## Decision outcome

Chosen option: **A**, the maintainer's decision of 2026-11-12 (decision card in the project
thread, recorded in the body of #678). Option B copies the labels out of `/labels`, so a label
changed in an older project would not reach the next retrain, and the model file would become a
second label store that can disagree with the first.

What is built (`tether.project.condition_model`, `tether.ml.persistence`):

1. **The model records its source projects.** `PortableRankerModel.source_projects` is a tuple of
   `SourceProject` (the absolute path of the file, the SHA-256 of the training rows the fit took
   from it, and its row and human-label counts), in training-row order. The manifest key
   `source_projects` is additive and optional, so `MODEL_FORMAT_VERSION` stays `1`: an older build
   ignores the key, and an older artifact loads with an empty record. The digest is provenance
   only. A retrain does not require it to match, because a source project's labels can change.
2. **`retrain_condition_model(model, project)`** reads the recorded projects in their recorded
   order, then the open project if it is new, and refits with `warm_start_retrain`. Only the
   molecules of the model's condition are read. A human label supersedes a provisional prior on the
   same molecule, as in `tether.project.gbranking`. `train_condition_model(project, condition_id=…)`
   starts the loop and records the first project. Two paths to one file (a symlink or a junction)
   are one project: paths are compared after `os.path.realpath`, so no project is read twice.
   A model that records no source projects (an artifact saved before this ADR) is **refused**,
   because a retrain from one project would forget the labels it was trained on; it is fitted
   again once from its projects (`condition_training_set`, then `warm_start_retrain` with
   `source_projects=`).
3. **`n_human` counts every project.** A provisional prior trains at `w₀ / (1 + n_human)`, where
   `n_human` is the sum of the condition's human labels in all the projects read.
4. **A missing or offline project stops the retrain.** Every project is checked before any is
   read. A file that does not exist, or whose Windows attributes carry `FILE_ATTRIBUTE_OFFLINE`
   (read with `os.stat`, which reads metadata only), raises `SourceProjectUnavailableError`, which
   names the file. Nothing is saved by the retrain, so a saved model does not change.
5. **Read-only, no lock.** The source projects are opened only through the path-based readers,
   which open the file with `h5py` mode `"r"` and never call the lock.

## Consequences

- A retrain costs one read of each source project. For ≈100 videos over some tens of projects this
  is a read of their `/features` and `/molecules` tables and the `/labels` log, not of any movie or
  trace.
- **A moved project stops the retrain.** The record holds absolute paths, so a project that moved
  or a model that moved to another computer names a file that is not there. The message names it;
  relinking a source project is not built here.
- **Placeholder detection is the `FILE_ATTRIBUTE_OFFLINE` bit only.** It is the attribute the
  Win32 documentation gives for "the data of a file is not immediately available", and the
  standard library carries its value. The documentation for the newer cloud-file recall attributes
  could not be read from the session that built this, so they are not tested. A placeholder that
  sets only a recall attribute is not detected before the read.
- **No schema change.** Nothing is written to a `.tether`; `schema-guard` and `dump_schema.py`
  are unaffected. No dependency and no new §11.2 tunable: `w0` is the existing seed weight.
- **Out of scope:** the project's `/models` reference and the owner-curator lock
  ([#561](https://github.com/bioedca/tether/issues/561)), the application controls
  ([#640](https://github.com/bioedca/tether/issues/640)), and seeding from another condition
  ([#644](https://github.com/bioedca/tether/issues/644)).

## Validation

`tests/test_project_condition_model.py`, on synthetic stores: a model retrained from the second
project gives the same scores as one `train_portable_model` call on the labels of both projects;
the seed weight uses the human labels of both projects; a missing project and an offline project
each stop the retrain with the file's name, the offline one before any `h5py` open, and the saved
model's bytes do not change; every `h5py` open during a retrain is mode `"r"`, and no `.lock`
sidecar appears next to the source project. `tests/test_ml_persistence.py` covers the manifest
record: round trip, an artifact without the key, and malformed or repeated entries.
