<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0153 — A cellSens .vsi movie's checksum covers its frame files

- **Status:** accepted
- **Date:** 2026-11-25
- **Deciders:** bioedca (the acceptance criteria of #1398, approved 2026-11-24); a P1 finding of
  the Greptile review of PR #1401
- **PRD anchor:** §5 (data model, `/movies.sha256`), §7.1 FR-EXTRACT, Appendix A (input formats)
- **Milestone:** M20
- **Issue:** [#1398](https://github.com/bioedca/tether/issues/1398)
- **Builds on:** [ADR-0147](0147-cellsens-vsi-movies-are-read-by-bio-formats-in-an-isolated-fourth-stack.md)
  (the `.vsi` reader), [ADR-0149](0149-extraction-reads-every-registered-reader-through-read-frames.md)
  (extraction reads every registered reader),
  [ADR-0151](0151-a-movie-pair-is-one-movies-row-named-by-the-checksum-of-both-files.md) (a checksum
  of more than one file)

## Context and problem statement

A movie's checksum is its identity. `/movies.sha256` seeds every `molecule_key`, the source
registry compares a file with it before it stores a checksum or links a moved file again, and
`tether batch --into` uses it to find a movie that is already in the project and skip it. For
every container before #1398 the checksum was the SHA-256 of the movie file, and the frames were
in that file.

A cellSens `.vsi` file is different. It holds the metadata, and its frames are in the `.ets` files
of the `_<name>_` folder beside it (ADR-0147). When #1398 let extraction read `.vsi` movies, the
checksum was still the SHA-256 of the `.vsi` file alone. A movie whose `.ets` files changed and
whose `.vsi` did not then kept its checksum: `tether batch --into` called it "already in the
project" and kept the traces of the old frames, and the stored checksum could not say which frames
gave those traces. What must the checksum of a `.vsi` movie cover?

## Decision drivers

- **The identity covers the pixels** (PRD §5): a change to the frames must change the checksum.
- **One identity everywhere**: extraction, the source registry's checksum job and a link of a
  moved file must compute the same value, or a correct file is refused as changed.
- **The other containers do not change**: their stored checksums stay valid.
- **A rename keeps the identity**, as it does for a TIFF.

## Considered options

1. **One SHA-256 of the `.vsi` file's SHA-256 and each `.ets` file's path and SHA-256** (chosen).
2. **The SHA-256 of the `.vsi` file alone**, as before. Rejected: it is the defect above.
3. **One SHA-256 over the bytes of all the files in a row.** Rejected: the boundaries between
   files are not in it, so two different sets of files can give the same bytes.

## Decision outcome

Chosen option: **1**, in `tether.io.movie_identity`. `frame_files(path)` gives the `.ets` files at
any depth of the `_<name>_` folder of a `.vsi` (the extension in any case), sorted by their path in
that folder, written with `/`. Other files in the folder are not frame files and are left out.
`content_sha256(path, file_sha256)` then gives one SHA-256 of the tag
`tether-vsi-frame-files-v1\0`, the `.vsi` file's own SHA-256 and, for each frame file in that
order, `\0`, its path in the folder, `\0` and its SHA-256. The tag keeps the value from ever being
the checksum of one file, as the pair tag of ADR-0151 does. The folder's own name is not in it, so a
`.vsi` renamed together with its folder, as cellSens needs, keeps its checksum.

A file that is not a `.vsi`, or a `.vsi` with no `_<name>_` folder or no `.ets` file in it, keeps
the SHA-256 of its file. So the checksum of every other container is unchanged.

`tether.project.extract._hash_movie` (extraction and `tether batch --into`) and
`tether.project.sources.file_sha256` (the checksum job and the link of a moved file) both use it.
Only the checksum changes: the size, modification time and file identity that are recorded and
compared are still the `.vsi` file's own, because they describe the file that the path names.

### Consequences

- Good: a changed `.ets` file gives a new checksum, so `tether batch --into` extracts the movie
  again, and the stored checksum names the frames that gave the traces.
- Good: no stored checksum changes. Extraction could not read a `.vsi` before #1398, so no
  `/movies` row of a `.vsi` exists.
- Neutral: the fast check of the movie panel (size and modification time) reads only the `.vsi`
  file, so it does not see a changed `.ets` file. The checksum does.
- Bad: a `.vsi` that was put in the source registry as a source file before this change has a
  checksum of the `.vsi` file alone. The checksum job then calls it changed. No release has
  carried `.vsi` reading, so such a record is not expected.

## More information

- The finding: the Greptile review of PR #1401 (P1, "Changed frames keep old traces").
- Tests: `tests/test_extraction_reads_vsi.py`
  (`test_a_changed_frame_file_is_a_new_movie_to_batch_into`,
  `test_the_frame_file_checksum_is_fixed_by_content_and_names`).
