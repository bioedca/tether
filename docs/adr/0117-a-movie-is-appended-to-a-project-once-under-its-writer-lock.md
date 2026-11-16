<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0117 — A movie is appended to a project once, under its writer lock, and keeps its own settings

- **Status:** accepted
- **Date:** 2026-11-16
- **Deciders:** bioedca
- **PRD anchor:** §5.1 (data model), §5.4 (single writer), §7.11 FR-EXTRACT / FR-BATCH, §7.8 (the
  Deep-LASI import)
- **Milestone:** M12
- **Issue:** [#347](https://github.com/bioedca/tether/issues/347); builds on
  [ADR-0016](0016-extraction-trace-store-layout.md) and
  [ADR-0112](0112-every-publishing-writer-holds-its-destination-lock.md)

## Context and problem statement

The D1 decision makes one project hold several movies, and `write_extraction` was written for
that from the start: it appends a `/movies` row, the `/molecules` rows, every `/traces` array
(zero-padded to the widest movie) and both `/patches` arrays, and it refuses a second write of one
`movie_id`. No caller used it that way. `extract_movie` and `reconstruct_project` each build a
fresh project per movie, so a Deep-LASI re-analysis of a condition turns into one file per
acquisition (`deeplasi_executor.py`), and `tether batch --into` (#653) has nothing to call.

Four things have to be decided before an append is safe:

1. how the append writes an existing project without breaking the single-writer rule (§5.4);
2. what makes a movie "already in the project", when each run mints a fresh `movie_id`;
3. where a later movie's settings go, when `/settings/extraction` is write-once and records only
   the first extraction;
4. what the Deep-LASI passes (corrections, photobleach, categories) may touch, when each of them
   was written to process every molecule of a one-movie project.

## Decision drivers

- §5.4: one writer at a time; a writer that holds a project must never lose its file.
- The positional join (ADR-0016): row `i` of `/molecules`, of every `/traces` array and of every
  `/patches` array is one molecule. An append must keep it for every row.
- `molecule_key` is `sha256(movie) + donor position` (§7.10). Labels are keyed by it.
- Additive-only schema after M0 (§9.1): no field may change meaning, and no new required field.
- A curator's edits (manual factors, an apparent-E toggle, narrowed windows) are expensive and
  must not be recomputed away by somebody else's import.

## Considered options

- **A. Append in place**, opening the project `r+` and calling `write_extraction` on it.
  Rejected: h5py appends are not transactional, so a crash or a lost lock during the write leaves
  a project with half a movie in it.
- **B. Rebuild the whole project** from its movies plus the new one at a temporary path.
  Rejected: everything not derived from the movies (labels, curation, fits) would have to be
  carried across by hand, and a project from an import has no movie to re-extract.
- **C. Append into a staged copy under the writer lock and publish it with one atomic replace**
  (chosen). This is the `guarded_project_write` path the correction, γ, leakage and photobleach
  writers already use (ADR-0112).

## Decision outcome

Chosen option: **C**.

### The append is a guarded staged write

- `tether.project.extract.add_movie_to_project(project_path, movie_path, *, options, tmap, tdat,
  publish_guard)` runs the same stages 1-15 as `extract_movie` and persists through the same
  `write_calibration` + `write_extraction` calls: `extract_movie` and the append share one
  `_run_pipeline` and one `_persist`, so they cannot drift.
- `tether.project.reconstruct.add_reconstruction_to_project(project_path, *, …, publish_guard)` is
  `reconstruct_project`'s append path; both share one `_reconstruct_into`.
- Both **require** `publish_guard`, the check `publish_lock` yields (ADR-0112). An append without
  one is refused before any work (`ExtractionError` / `PublishGuardRequiredError`).
- The write goes to a same-directory copy (`guarded_project_write`): the guard runs before the
  copy, after the write and just before the one `os.replace`, and the canonical file's identity is
  checked before the replace. A failure at any point, the guard included, removes the copy, so
  the project is byte-identical to what it was.

### A movie is identified by its content

- Each append mints a fresh `movie_id`, so `write_extraction`'s write-once rule alone would let the
  same file in twice. Every molecule of the second copy would then share a `molecule_key` with one
  already stored, and a label could no longer tell the two apart.
- The append therefore refuses a movie whose content `sha256` matches any `/movies` row, and the
  reconstruction append refuses a repeated `movie_id` too. The check runs against the project
  before the extraction and again against the staged copy before the write.
- `write_extraction` keeps its own rules unchanged: write-once per `movie_id`, and aperture
  parameters (`window`, the radii, `bg_window`) equal to the first extraction's.
- It also gains one check, before it writes anything: every `/traces` and `/patches` array it
  appends to must cover every `/molecules` row. A store that has molecules but lacks an array —
  an analysis-only import (no raw, background or patches) or a subset exported without its raw
  layers — would otherwise get that array created with the new movie's rows at row 0, and the
  positional join would break. Such a store cannot take a movie. `assert_appendable` runs the
  same check read-only, and both appends call it before any work.

### Each movie records its own settings

- `/settings/extraction` stays write-once: its attributes describe the first extraction, as
  before.
- `write_extraction` now also writes one string attribute per movie, named by its `movie_id`, on a
  child group **`/settings/extraction/movies`**, holding that movie's settings profile as JSON (the
  same text the first extraction stores in `profile_json`). The first movie gets one too, so the
  record is the same for every movie. `tether.imaging.extract.read_movie_settings` reads them.
- This is additive data: a new child group, no new or changed field in a frozen table, and the
  schema manifest (`scripts/dump_schema.py --check`) is unchanged, so `schema_version` stays 1. A
  project written before this change has no `movies` child and reads as an empty record; its one
  movie's settings are the stamp's `profile_json`.

### The Deep-LASI passes are scoped to the appended movie

- `compute_corrected_fret` and `compute_photobleach` gain `movie_id: str | None = None`. With a
  value, the pass reads and writes only the rows of that movie; every other row keeps its factors,
  method, bleach frames and window and is not counted. `None` keeps today's whole-project pass.
- The reconstruction append passes its own `movie_id`, so one acquisition's Deep-LASI α and γ are
  never stamped onto another's molecules. `/settings/correction` still records the latest pass,
  as it always has.
- Category seeds on an append **extend** the condition's list: names already there keep their
  order and so their integer codes, and new names go after them. Replacing the list would recode
  the categories that other movies' molecules use.

### What this does not decide

- Trace generations (#921) are not part of this record. An appended movie is written as a new
  movie, which #921 defines as generation 1 of that movie; #921 adds the generation record to this
  path.
- The Deep-LASI wizard and `tether batch` still write one project per movie. Making them call
  these functions is later work (#653 adds `tether batch --into`).

### Consequences

- Good: one project can hold a condition's movies, which is what the population views,
  conditions and ranker training expect.
- Good: an append can never leave a half-written movie, and a lost lock leaves the project as it
  was.
- Good: the per-movie settings record keeps each movie's provenance (NFR-REPRO) without touching
  the first extraction's stamp.
- Trade-off: an append copies the whole project once. For a large project that is a disk-space
  and time cost the caller pays per movie; the alternative (option A) costs a corrupt project on
  failure.
- Trade-off: two different files with byte-identical content cannot both be appended, by design.
- Enforced by: `tests/test_project_append.py` (the growing tables and positional join, the
  zero-pad for a longer and for a shorter movie, the duplicate refusal, write-once per
  `movie_id`, the required guard, a foreign lock, a lock stolen by another process mid-append,
  the aperture check, the refusal of an analysis-only or raw-less subset target, the per-movie
  settings record, and the reconstruction append with its scoped passes and extended
  categories).

## More information

- [ADR-0016](0016-extraction-trace-store-layout.md) — the trace store layout and the zero-pad.
- [ADR-0112](0112-every-publishing-writer-holds-its-destination-lock.md) — `publish_lock` and the
  guarded staged write.
- `src/tether/project/extract.py` (`add_movie_to_project`), `src/tether/project/reconstruct.py`
  (`add_reconstruction_to_project`), `src/tether/imaging/extract.py` (`read_movie_settings`).
