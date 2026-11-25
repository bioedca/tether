<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0151 — A movie pair is one /movies row named by the checksum of both files

- **Status:** accepted
- **Date:** 2026-11-25
- **Deciders:** bioedca (the acceptance criteria of #909, approved 2026-11-14 with card A1
  option B, which puts it in v1.0.0)
- **PRD anchor:** §5 (data model), §7.1 FR-EXTRACT, Appendix E stage 1
- **Milestone:** M20
- **Issue:** [#909](https://github.com/bioedca/tether/issues/909)
- **Builds on:** [ADR-0140](0140-a-source-registry-records-each-source-file-and-what-was-derived-from-it.md)
  (the source registry) and
  [ADR-0149](0149-extraction-reads-every-registered-reader-through-read-frames.md) (the
  extraction read)

## Context and problem statement

A setup with two cameras writes one movie for the donor and one for the acceptor. Tether stores
one `/movies` row for each acquisition, and that row's `sha256` is the content identity that
duplicate detection, re-extraction and the molecule keys use. A map file records the channel
geometry of the camera frame it was made from. How is an acquisition of two files stored and
named, and how is a map for two files told apart from a map for one, without a schema change?

## Decision drivers

- **No schema change** (#909 is additive to the v3 store of ADR-0140).
- **One acquisition.** The stages after the split see one acquisition with two channels, so a
  pair and its one-file equivalent give equal traces.
- **Identity names every byte.** Changing either file must change the identity.
- **A wrong map is refused, not applied.** A map made for one camera frame must not be applied to
  two whole frames, or the other way round.

## Considered options

1. **One `/movies` row whose `sha256` is a tagged checksum of both files' checksums, the two files
   in the source registry by role, and a pair map told apart by equal channel geometries**
   (chosen).
2. **Two `/movies` rows, one for each file, linked by a new field.** A schema change, and every
   later stage would need to join two rows into one acquisition.
3. **The donor file's checksum as the row's `sha256`.** A changed acceptor file would not change
   the identity, so re-extraction and duplicate detection would miss it.
4. **A new field in the map file that names it a pair map.** A change of the map format, while the
   geometry the map already stores gives the same answer.

## Decision outcome

Chosen option: **1**.

### The row and its identity

The pair is one `/movies` row. Its `sha256` is `tether.io.pair.pair_sha256(donor, acceptor)`: the
SHA-256 of a fixed tag (`tether-movie-pair-v1`), the donor file's hex checksum and the acceptor
file's hex checksum. The order is part of the identity, so a pair with the roles exchanged is
another acquisition. The tag keeps the value from ever being the checksum of one file, so the
molecule keys of a pair never equal those of either file read alone. The row's `uri`, size and
modification time are the donor file's. After the run each file is checked again against its own
checksum and size, as one movie is.

Compatibility: a pair appended twice is the same identity and is refused as a duplicate, like one
movie. A file of a pair appended alone is a different identity, and is another acquisition.
Re-extraction of a pair needs both files again (`acceptor_movie=`), because the identity names
both.

### The two files in the source registry

Both files are stored in `/sources` (ADR-0140) with role `movie`, the pair's `movie_id`, their own
checksum and size, and the acquisition `{"pair_role": "donor"}` or `{"pair_role": "acceptor"}`
(`tether.io.pair.PAIR_ROLE_KEY`). The settings profile of the run records `movie_pair` with the
two file names.

### The pair map

Each file of a pair is one whole channel, so the two channel geometries of a map made from a bead
file pair (`tether.imaging.beadmap.bead_map_pair_from_files`) are the same whole frame. The
geometries of a map made from one camera frame differ, because its two channels are two regions of
one frame. Extraction therefore reads a map with equal geometries as a pair map: it is refused for
one movie, and a map with different geometries is refused for a pair. A `.tmap`, whose crops are
regions of one camera frame, is refused for a pair. The map's provenance records the acceptor bead
file (`acceptor_bead_source`) and `two_files`.

### What a pair must share

The pair is refused, with a message that names every difference, if the frame count, the frame
time (also a known time against an unknown one), the pixel type or the frame size differ. The byte
order may differ, because each file is read in its own. Tether does not align two movies in time
or size.

## Consequences

- Good: a two-camera acquisition extracts with the stages of a one-file movie, and the traces of a
  dual-view movie split into two files equal those of the one-file movie, bit for bit
  (`tests/test_movie_pair.py`).
- Good: no schema change and no change of the map format.
- Bad: the pair test is a property of the geometry, not a stated field. A one-frame bead map cuts
  the frame into its left and right halves, so its geometries always differ. A map whose two
  channels were one region of one frame would read as a pair map; such a map has no meaning (both
  channels are the same pixels), and it is refused for one movie, so the error is a refusal.
- Neutral: the movie panel shows the donor file of a pair for now; showing both is #1405.
