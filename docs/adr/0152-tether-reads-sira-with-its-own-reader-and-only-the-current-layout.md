<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0152 — Tether reads SIRA with its own reader, and only the current layout

- **Status:** accepted
- **Date:** 2026-11-25
- **Deciders:** bioedca (the acceptance criteria of #905, approved 2026-11-24; card D2 option B,
  2026-11-14, for the test files)
- **PRD anchor:** §7.1 FR-EXTRACT, Appendix A (input formats)
- **Milestone:** M20
- **Issue:** [#905](https://github.com/bioedca/tether/issues/905)
- **Builds on:** [ADR-0113](0113-one-movie-reader-interface-an-adapter-registry-and-a-rule-for-reader-packages.md)
  (the reader interface and the rule for a reader package),
  [ADR-0073](0073-one-manifest-for-reference-data-sets.md) (reference data)

## Context and problem statement

SIRA is the movie format of MASH-FRET, the MATLAB package for smFRET movies and traces of the
RNA-FRETools group (GPL-3.0). MASH-FRET writes it when it exports a simulated movie, an edited
movie or an average image. Tether did not read `.sira`, so a user had to convert each movie to
TIFF with MASH-FRET first, outside the provenance record. Which reader does Tether use, which
files does it accept, how does it treat the per-frame offset of the format, and where does the
metadata come from?

## Decision drivers

- **ADR-0113's rule for a reader package.**
- **Refuse rather than guess** (PRD Appendix A). A file that Tether cannot read whole is refused
  with a message, never read in part.
- **A frame read is not a movie load** (PRD §5.2).
- **A bit-for-bit check** against MASH-FRET's own reader, on files of MASH-FRET.

## Considered options

1. **Tether's own reader** (chosen).
2. **A reader package.** None exists: no package on PyPI or conda-forge reads `.sira` (the PyPI
   name `sira` is an unrelated project), so rule 2 of ADR-0113 cannot be met.
3. **MASH-FRET itself.** It needs MATLAB (or GNU Octave with stand-ins for MATLAB functions) and
   its GUI state, outside every Tether stack. Tether uses it only to make the reference values.

## Decision outcome

Chosen option: **1**. `tether.io.sira` reads SIRA with the standard library and NumPy, so the base
lock does not change. The layout comes from MASH-FRET's description of the format
(`docs/output-files/sira-mash-video.md`) and its `readSira.m`, `getSiraDat.m` and
`writeSiraFile.m` at commit `2877992d246acbe65148ef1c948578f623a518b2`. MASH-FRET is GPL-3.0, a
licence Tether can use; the reader is written new, and no code of MASH-FRET is copied.

### The layout

One line of text, `MASH-FRET exported binary graphic Version: <version>`, ended by a carriage
return (MASH-FRET's `fgetl_MASH.m` stops at a carriage return or a line feed). Then the frame time
as a little-endian 64-bit float, and the width, the height and the frame count as 32-bit floats.
Then each frame: its pixels column by column (MATLAB order), then one value, its offset, all as
little-endian 32-bit floats. Tether returns each frame as `(height, width)`: row 0 is the first
row of MASH-FRET's image matrix, which MASH-FRET shows at the top and its TIFF export writes first.

### What Tether reads, and what it refuses

- **Only the current layout.** `getSiraDat.m` gives the older headers (`SIRA exported binary
  graphic`, `MASH smFRET exported binary graphic`) and MASH-FRET 1.003 before 1.003.41 layouts
  with 16-bit pixels, and before 1.003.39 with no offsets. Its caller `readSira.m` also takes the
  two flags of `getSiraDat.m` in the other order, so for 1.003.39 and 1.003.40 MASH-FRET's own
  reading is not certain. Tether refuses all of these by name. Every other version under the
  `MASH-FRET` header is read as the current layout, as MASH-FRET reads it: the test movies carry
  `1.1.2 (prev. commit: 352a251)` and `dummy`.
- The width, the height and the frame count must be whole numbers from 1 to 2²⁴.
- The file must be exactly the header and the frames. Shorter is an incomplete copy, longer a
  variant that Tether has not met; both are refused.
- Each offset must be finite and 0 or more, as `writeSiraFile.m` writes it. The open reads each
  frame's offset (4 bytes a frame), not the frames.

### The offset

`writeSiraFile.m` stores a frame with negative values plus `|min|`, so that no stored value is
below zero, and stores `|min|` after the frame (0 for a frame with none). **Tether subtracts the
offset**, in 32-bit float arithmetic, so each pixel is the value that the writer was given.
MASH-FRET's `readSira.m` does the same when it reads one frame (`readSira(file, n, …)`), but when it
reads the whole movie (`'all'`) it *adds* the offset to the stored values, which gives the written
frames plus twice the offset. The two paths disagree, and the writer decides between them. The
committed `mash_offsets.sira`, written by `writeSiraFile.m` under Octave, records this: Tether and
the one-frame path give back the written frames; the whole-movie path does not. On a file whose
offsets are all 0, as in every test movie of MASH-FRET, the paths agree, and the reference digests
below come from the whole-movie path.

### Where the metadata comes from

| Field | Read from | When it is `unknown` |
| --- | --- | --- |
| frame time (s) | the header's frame time | one frame (an average image), or a value that is not finite and positive |
| pixel size (um) | not read | always: the file does not record it |
| channels | not read | always: two or more channels are side by side in each frame, and the file names none |

### The test files

- **Two test movies of the MASH-FRET repository** (GPL-3.0-only: the repository gives the version
  3 text with no "or later" statement): `1chan1exc.sira` (200 frames of 64 x 64, 3.3 MB) and
  `2020.sira` (20 frames of 256 x 256, 5.2 MB), as `download` entries of the ADR-0073 manifest, so
  the weekly `reference-data` job checks their pixel digests against `readSira.m` run under GNU
  Octave 8.4.0 (`scripts/make_sira_reference.py`).
- **The first 16 frames of `1chan1exc.sira`**, committed (262 kB), with its reference TIFF as
  `readSira.m` reads it: the per-PR bit-for-bit check.
- **`mash_offsets.sira`**: three frames of Tether's with negative values, written by
  `writeSiraFile.m`: the offset check above.
- **Synthetic movies** of `tests/_sira_synth.py`: the conformance movie, a frame that is not
  square, and one file for each refusal.

### Consequences

- Good: a MASH-FRET movie opens through the registry with its frame time, and every refusal names
  its reason. Extraction does not read it yet, as for CZI and SIF: an adapter enters
  `EXTRACTED_CONTAINERS` once its movies extract to the same traces as their TIFF export.
- Good: no new package.
- Bad / trade-off: a movie that an old MASH-FRET wrote is refused; the user opens it in a current
  MASH-FRET and exports it again.
- Bad / trade-off: Tether's whole-movie read differs from MASH-FRET's on a frame with a non-zero
  offset. Tether follows the writer, and the difference is recorded above.

## More information

- `src/tether/io/sira.py`, `tests/test_movie_sira.py`, `tests/_sira_synth.py`,
  `scripts/make_sira_reference.py`; `tests/fixtures/PROVENANCE.md` has the files, the tools and
  their versions.
- [Movie containers](../reference/movie-containers.md#sira) is the user reference.
