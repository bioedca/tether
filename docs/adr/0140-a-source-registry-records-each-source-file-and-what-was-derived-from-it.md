<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0140 — A source registry records each source file and what was derived from it

- **Status:** accepted
- **Date:** 2026-11-24
- **Deciders:** bioedca (issue #912 approved 2026-11-14, grooming card "Approve all recommended", Q3; card A1 option B puts it in v1.0.0)
- **PRD anchor:** §5 (data model), §5.4 (migration), §7.3 (FR-ROUNDTRIP), §7.13 (jobs)
- **Milestone:** M20
- **Issue:** [#912](https://github.com/bioedca/tether/issues/912)

## Context and problem statement

G02 stores an "immutable source registry: originals + derived files + recipes", and G06 needs the
raw movies and the map sources for a deposit. Until now a project knew the path of each movie
(`/movies.uri`) and the SHA-256 its extraction read, and nothing else: not the bead video that made
the map, not a file derived from another one, and not a checksum that a later check of the file
could be measured against when the file moved. Where is that record kept, how does an existing
project get one, and when is a file in a new place the same file?

## Decision drivers

- **One record for each source file**, with its path, original name, size, checksum, adapter and
  version, role and acquisition. A derived file points to its original and to the recipe between
  them. Raw movies are not copied into the project.
- **Additive, with an honest stamp.** A new table in the skeleton is a structural change: an ADR, a
  `schema_version` bump and a regenerated golden (PRD §12.6). Files stamped 1 and 2 must still
  open, without a rewrite (`docs/stability.md`, *Older files in newer apps*).
- **A checksum of a large file is a job** (#895, ADR-0126), never work in the window thread.
- **The checksum decides** whether a moved file is the same file, by one rule that the
  missing-movie prompt (#638) uses too.
- **A copy for sharing (#620) holds no absolute private path.**

## Considered options

- **A. A sixth rich table, `/sources/table`**, in the skeleton from version 3.
- **B. A dataset under `/settings`**, additive data with no version bump, as the jobs are
  (ADR-0126).
- **C. Fields appended to `/movies/table`.**

## Decision outcome

Chosen option: **A**, implemented in `src/tether/io/schema.py` (`SOURCES_DTYPE`, `SOURCES_SINCE`,
`upgrade_skeleton`), `src/tether/project/sources.py` (records, registration, link again, the share
rule) and `src/tether/services/sources.py` (the checksum job).

C cannot hold a bead video or a map image, which have no `/movies` row, and a derived file would
need a second kind of row in the movie table. B keeps the record out of the frozen skeleton, so
`schema-guard` would not stop a later change from renaming or retyping its fields; the registry is
what a deposit (#951) will be built from, so it is part of the data model, and the issue asks for
the version change. A is the same step ADR-0136 took for `/generations`.

### The record

`/sources/table` has 13 fields: `source_id`, `relative_path`, `original_name`, `file_size`,
`sha256`, `adapter`, `adapter_version`, `role` (`movie`, `bead_video`, `map_image`), `acquisition`
(JSON), `movie_id`, `derived_from`, `recipe` and `created_utc`. `sha256 == ""` means *not
calculated*. A derived file names its original's `source_id` and the recipe; an original gives
neither, and one without the other is refused.

### An existing project

`_missing_skeleton` asks for `/sources/table` only of a file stamped 3 or newer, so a file stamped
1 or 2 stays a complete project. `read_sources` returns the stored rows and then one record for each
`/movies` row that has none: id `movie:<movie_id>`, the stored `uri` as its path, the stored size,
role `movie`, checksum not calculated. Reading writes nothing. The first write that needs the
record (a checksum, a link again, a derived file of that movie) stores it under the same id, so a
reference to it never changes.

The first write into a store stamped before 3 runs `upgrade_skeleton` in a guarded write: every
missing table is added, the file is stamped 3, and the publish that raises the stamp first keeps the
file as it was beside it, as `<stem>.v<n>-backup.tether` (PRD §5.4, ADR-0136).
`upgrade_to_generations` now calls `upgrade_skeleton`, so a file it stamps 3 always has every table
of version 3.

### The checksum job

`submit_checksum_job` stores a job of the coordinator with one part for each source whose checksum
is not calculated; the request records source ids only, never a path. `run_checksum_job` reads each
file in 1 MiB blocks, asks the cancel token between blocks, and reports progress after each source
and after each block. A front end calls it from a worker thread. A cancel leaves the store as it
was before that source, and a later run continues from the checkpoint. The table is added before
the job starts, so no part's write replaces the file. A checksum already known for a source is
its identity: the registry's, or for a movie the `/movies.sha256` of its extraction. A file whose
checksum differs (it changed since then) fails its part and writes nothing, and a stored checksum is
never replaced. Each write checks the lock epoch the job started under, so a steal during a long
read stops the write, as the coordinator's own writes do.

### Link again

`relink_source` reads the new file once and accepts it only when `same_file(expected, actual)`: the
two checksums are equal. The expected checksum is the registry's, or, for a movie whose record has
none, the `sha256` of its `/movies` row, which every native extraction records. A checksum that is
not known decides nothing, so a source with none is refused rather than linked on trust. On a match
the record's path and size change and the checksum is stored. Like the checksum job, the write
checks the lock epoch the call started under. `same_file` is the rule that the
missing-movie prompt (#638) shares.

### Paths and the copy for sharing

A file is stored relative to the project file's folder, with `/` separators. A file outside the
folder is stored with `..` steps, and a file on another Windows drive, which has no relative path,
keeps its absolute path: refusing it would refuse every movie on a data drive. Such a path can name
a private folder, so `export_subset_tether` copies the registry (every record, including the ones
made from `/movies`, so the subset still says where its molecules came from) and, for a copy for
sharing (`curator_alias`), keeps only the file name of a path that is absolute or has a `..` step
(`shareable_path`), as it does for `PATH_ATTRS`. A plain subset reads each relative path against
the source project's folder and stores it relative to the subset's, so it names the same file.

### Consequences

- Good: each source file has one record that a deposit (#951) and the missing-movie prompt (#638)
  can build on, without copying a raw movie.
- Good: a file stamped 1 or 2 opens as before and reads as one record for each movie.
- Bad: a project stamped 3 does not open in an app that predates this change; that is the
  forward-only rule doing its job.
- Neutral: extraction does not yet write a registry row with its adapter and version; the record
  made from `/movies` stands in for it until a writer that knows them registers the movie. The movie
  panel still reads `/movies.uri`; making it follow a link again is #638's work.
