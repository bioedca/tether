<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0136 — A new extraction of a movie is a new trace generation, and the previous one is archived unchanged

- **Status:** accepted
- **Date:** 2026-11-19
- **Deciders:** bioedca (issue #921 approved 2026-11-19; cards C5 option A and A1 option B, 2026-11-14)
- **PRD anchor:** §5 (data model), §9.1 (additive-only schema), §7.1, §7.5 (FR-ML), §11.2
- **Milestone:** M15
- **Issue:** [#921](https://github.com/bioedca/tether/issues/921)

## Context and problem statement

G02 note 04 says that changing the channel geometry or the map invalidates the extraction, that a
re-extraction creates a new generation and requires label and fit reconciliation, and that it
never silently overwrites a reviewed generation. Until now a movie was write-once: a second
extraction of it had no place in the store and was refused (#347, ADR-0117). Curation is the most
expensive step, so a rule that can lose labels is not acceptable.

[ADR-0103](0103-the-project-dependency-graph-and-the-stale-rules.md) already names the
`trace_generation` node and its edges: labels and windows are carried by `molecule_key` (edges 5
and 6), corrections, factor versions and fits become stale (edges 7, 8, 10). The maintainer chose
on card C5 that labels, categories and windows move to the molecules with the same key and get the
mark "carried", that every fit and correction becomes stale, and that the old generation stays
readable. Where is a generation stored, which one do the readers read, and when has a spot "not
moved"?

## Decision drivers

- **No generation is overwritten or deleted by an extraction.** A labelled generation must be
  readable, labels and all, after any number of re-extractions.
- **The positional join holds.** Row *i* of `/molecules`, of every `/traces` array and of every
  `/patches` array is one molecule (ADR-0016), and dozens of readers and writers rely on one row
  per molecule with a unique `molecule_key`.
- **Additive, with an honest stamp.** A new table in the skeleton is a structural change: an ADR,
  a `schema_version` bump and a regenerated golden (PRD §12.6). Files stamped 1 must still open,
  without a rewrite (`docs/stability.md`, *Older files in newer apps*).
- **A partial write publishes nothing.** h5py writes are not transactional.

## Considered options

- **A. Keep every generation's rows in the shared arrays**, with a per-row generation column, and
  teach every reader and writer to keep to the active rows.
- **B. Keep only the active generation in the shared arrays**, and copy the previous one,
  unchanged, to an archive group before its rows are replaced.
- **C. One file per generation** beside the project.

## Decision outcome

Chosen option: **B**, implemented in `src/tether/io/generations.py` (layout and readers),
`write_generation` in `src/tether/imaging/extract.py` (the writer) and `reextract_movie` in
`src/tether/project/extract.py` (the command).

A puts two rows with one `molecule_key` into `/molecules` for every carried molecule: each of the
writers that resolve a key to its rows (labels, categories, windows, re-keying, leakage, γ,
corrections, merge-back) would write the inactive generation as well, which is the overwrite the
issue forbids, unless every one of them is changed, and a reader that is missed reads both. B keeps
the invariant every existing reader and writer already assumes, so none of them has to learn
which rows are active. C is lost
when a project is copied without its siblings, as ADR-0126 found for jobs.

1. **The record.** `/generations/table` (`GENERATIONS_DTYPE`) has one row per generation, append-only:
   `movie_id`, `generation` (1, 2, … per movie), `generation_id` (`gen-<uuid4 hex>`, unique in the
   project), `created_utc`, `inputs` (JSON: source hash, URI, size and mtime; the crop, rotation and
   flip fields; the layout record; the map's `calibration_id`; the colocalization rule; the whole
   settings profile), `n_molecules`, `carried_keys` (JSON list), `match_px`, `app_version`.
   `write_extraction` writes generation 1 of every new movie, so the append of #347 writes one, and
   the publish of #352 will, since it goes through the same writer.
2. **The active generation** of a movie is its newest. Its molecules are the movie's rows of the
   shared arrays, so every reader of traces (`read_molecules`, `read_traces`, `read_patches`, and
   everything built on them) reads the active generation. A caller names another with
   `read_generation(path, movie_id, generation)`.
3. **A new generation** (`write_generation`): the active one is copied to
   `/generations/archive/<movie_id>/<generation>` (`movie`, `molecules`, `acceptor_detected`,
   `traces/<layer>` cut to the movie's frame count, `patches/<layer>`, `layout`); then its rows
   leave the shared arrays, the rows after them move up, and the new rows are appended at the end;
   the movie's `/movies` row, layout record and settings profile become the new generation's, and
   its `frame_time` is kept. An archive is written once. `reextract_movie` does all of it in a
   staged copy published by one atomic replace under the writer lock (ADR-0112), as the append
   does, so a failure leaves the project byte-identical.
4. **"Did not move"** is a number: a new molecule within **0.5 px** (`GENERATION_MATCH_PX`, PRD
   §11.2) of a molecule of the previous generation, by `donor_xy` mapped back to the raw camera
   frame through each generation's own donor crop, rotation and flip (each `donor_xy` is in its
   own donor sub-image, so a geometry change can shift its origin). Measure Euclidean distance
   inclusively and require each position to be the other's closest partner. Equal-distance
   candidates resolve to the lower partner index in the other generation. The default is five
   quanta of the key (0.1 px, ADR-0016). The value used is stored per generation (`match_px`),
   and `reextract_movie(match_px=…)` takes another.
5. **Carried.** A molecule that did not move keeps the previous `molecule_key` (so the `/labels` log
   and every key-joined record still resolve to it), and its `curation_label`, `category` and
   `analysis_window` (clipped to its frames) are copied. The immutable `carried_keys` list records
   that transfer. `carried_molecule_keys` reads the carry-overs still waiting for confirmation.
   The `condition_id` of the movie is kept when
   every previous molecule had one: the condition is a property of the movie, not an input of the
   extraction, and a re-keyed condition is not lost. A molecule that moved, or is new, has a fresh
   key and no labels. When its position-derived key is one an earlier generation of the movie held
   (a changed donor geometry can put another molecule at the same sub-image position), the key
   also names its generation (`|g<n>` appended to the payload), so a key never names two
   molecules. Corrections, bleach frames and the ML class start again from their
   not-computed sentinels (ADR-0016), and a `/features/table` holding a previous molecule is dropped,
   as the window setters drop it.
6. **Stale marks.** The `trace_generation` instance of a movie is its `movie_id`, and its identity is
   the active `generation_id`. A record that recorded it (ADR-0126 `record_inputs`) is marked stale
   through edges 5 to 10 by `mark_stale_in`, the store-path form of `set_stale_marks`, written into
   the staged copy so the marks are published with the generation. Edge 7's guard (the intensities
   of an α or γ contributor change) always holds: every trace of the movie is integrated again.
7. **The ranker (§7.5) and a carried label.** A carried label waits for human confirmation
   before it can train the ranker, count toward provisional-label decay, or serve as evaluation
   ground truth. The ranker can still score the molecule on current features. An explicit human
   accept or reject on the current extraction appends the ordinary `/labels/table` event and
   links that event to the current `molecule_id` in `/generations/label_reviews`. This leaves the
   prior label provenance and generation record intact. A clear restores the pending mark.
   Non-human and imported events do not confirm a carried label. Each new extraction gets new
   molecule IDs and requires its own review. The reconciliation page is #923.
8. **The version.** `SCHEMA_VERSION` is 2. `_missing_skeleton` asks for `/generations/table` only
   of a file stamped 2 or newer (`_RICH_TABLES_SINCE`), so a file stamped 1 is a complete project
   and reads as generation 1 of each movie, with `recorded=False` and its inputs read from the movie
   row; reading writes nothing. The first writer that records a generation into such a file adds the
   empty table and stamps it 2 (`upgrade_to_generations`), so an app that predates generations then
   refuses it rather than ignoring the record. A first new generation of such a movie also records
   its generation 1, with `created_utc` `""`, before archiving it.
9. **Back up, then migrate.** This is the first schema-version step, so it carries the
   backup-and-migrate path PRD §5.4 asks of it. A staged publish whose copy carries a higher
   `schema_version` than the file it replaces first copies that file, as it was, to
   `<stem>.v<old>-backup<suffix>` beside it (`experiment.v1-backup.tether`; `-2`, `-3`, … when the
   name is taken, so a backup is never overwritten), and only then replaces it. It copies to a
   temporary sibling, rechecks the source file and writer lock, and publishes the completed backup.
   A failed copy or source check at this point publishes neither file. A completed backup remains
   available if the subsequent project publish fails. Every writer that can migrate a file (the
   append and a new generation) runs in that publish (`guarded_project_write`, ADR-0112).
   The tests join oracle (f).

### Consequences

- Good: no existing reader or key-resolving writer changes, and a labelled generation can be read back, labels,
  traces and patches, byte for byte, after any number of re-extractions.
- Good: the record carries the identities ADR-0103 asks of the node: the source, the recipe and the
  map that made it.
- Bad / trade-off: a re-extraction moves the rows of the movies after it, so a row index is not a
  stable reference across a generation; nothing stored uses one (stored records key by
  `molecule_key` or `molecule_id`).
- Bad / trade-off: every archived generation keeps a full copy of its traces and patches, so the
  file grows with each re-extraction. Deleting an old generation is a later decision of the
  maintainer (#921, out of scope).
- Trade-off: a movie-less subset gets the selected active rows and their carried-label review state.
  It retains the label events for those rows and translates their review indexes into its own log.
- Follow-up: the reconciliation page (#923); the GUI publish (#352), the background extraction
  (#354) and the extraction queue (#645) call `reextract_movie` for a movie the project holds.
- Tests: `tests/test_trace_generations.py`, the backup-and-migrate test of oracle (f) among them; `scripts/dump_schema.py --check` with the regenerated
  golden.

## More information

G02 note 04; [ADR-0103](0103-the-project-dependency-graph-and-the-stale-rules.md) (the node, its
edges and its guard), [ADR-0126](0126-jobs-and-stale-marks-are-stored-in-the-project.md) (recorded
inputs and marks), [ADR-0117](0117-a-movie-is-appended-to-a-project-once-under-its-writer-lock.md)
(the append and its write-once rule), [ADR-0112](0112-every-publishing-writer-holds-its-destination-lock.md)
(the staged publish), [ADR-0016](0016-extraction-trace-store-layout.md) (the positional join, the
key and its quantum), [ADR-0005](0005-m0-schema-freeze.md) (the freeze). The maintainer confirmed
the default distance, mutual closest pairing and the training gate on 2026-11-20.
