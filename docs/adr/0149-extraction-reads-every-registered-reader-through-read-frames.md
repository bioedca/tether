<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0149 — Extraction reads every registered reader through read_frames

- **Status:** accepted
- **Date:** 2026-11-24
- **Deciders:** bioedca (the acceptance criteria of #1388, approved 2026-11-24; card "Pick the
  batch rule for an ND2 original beside its TIFF export", option "Refuse the pair with a
  message", 2026-11-24 19:28Z)
- **PRD anchor:** §7.1 FR-EXTRACT, Appendix A (input formats), §11.2
- **Milestone:** M20
- **Issue:** [#1388](https://github.com/bioedca/tether/issues/1388)
- **Builds on:** [ADR-0113](0113-one-movie-reader-interface-an-adapter-registry-and-a-rule-for-reader-packages.md)
  (the reader interface) and
  [ADR-0148](0148-tether-reads-nd2-with-its-own-reader-and-no-reader-package.md) (the ND2 reader)

## Context and problem statement

The extraction pipeline works on one `(n_frames, height, width)` array. The TIFF reader gives
that array as its memory map (`MovieReader.data`), and the steps after it (split, detection
image, aperture sums) slice and cast it with NumPy. The ND2 reader of ADR-0148 gives no map: its
frames are chunks at irregular places in the file, and the reader interface
(`MovieSource.read_frames`) returns a copy of a frame range. Extraction therefore refused ND2.
How does extraction read a reader that has no memory map, without a change of the reader
interface, a load of the full movie into memory, or a second pipeline that could drift from the
TIFF one?

## Decision drivers

- **The TIFF path does not change** (#1388, risk high): same code, same reference values.
- **One pipeline.** An ND2 movie and its TIFF export must give the same traces, bit for bit.
- **Memory.** The read must not hold the movie in memory; a reference movie is about 0.9 GB.
- **Format-neutral.** CZI (#1390) and later readers reuse the path with no extraction code.
- **No change of the reader interface** (out of scope in #1388).

## Considered options

1. **Copy the frames in blocks to a temporary file and map it** (chosen). The pipeline sees a
   read-only `numpy.memmap`, as for a TIFF.
2. **Copy the frames into memory.** Simple, but the movie is in memory for the whole run.
3. **A lazy array object over `read_frames` for the whole pipeline.** The NumPy steps (rotate,
   flip, crop, cast) would load it whole anyway, or each would need a block-wise version: a
   second pipeline.
4. **Map the ND2 file itself.** Only when the frame chunks are evenly spaced, which the format
   does not promise, and it needs a new method on the reader interface.

## Decision outcome

Chosen option: **1**.

### The extraction read

`tether.io.frames.open_extraction_movie(path)` chooses the adapter with `select_adapter`. An
adapter whose name is not in `tether.io.movie.EXTRACTED_CONTAINERS` is refused by name before its
frames are read. A TIFF gives its own memory map and metadata, as before. Any other reader is
copied by `spool_frames`: `read_frames` is called for `READ_BLOCK_FRAMES` (64) frames at a time,
each block is written to a `tempfile.TemporaryFile` with an ordinary write, and the file is then
mapped read-only in the source's own pixel type. The reader is closed at once. A full disk gives an
`OSError` that names the temporary folder and the size; an ordinary write is used, not a write into
the map, because a map of a sparse file on a full disk can stop the process. The file has no name
on disk, so the operating system deletes it when the last view of the map is gone, on every
platform.

The frame time and the pixel size come from the reader's `metadata` (a value of origin `unknown`
is recorded as unknown, as a TIFF without the tag is). The pixel type and byte order come from the
copy, so an ND2 movie records `uint16` little-endian, as a little-endian TIFF does.

`EXTRACTED_CONTAINERS` is now `("TIFF", "ND2")`. A reader enters it when its movies extract to the
same traces as their TIFF export, which `tests/test_extraction_reads_any_reader.py` checks for ND2
through `tether extract`, the service command that the application runs, and `tether batch`.

### The read block is not an analysis parameter

`READ_BLOCK_FRAMES` sets only how much of the movie is in memory while it is copied (64 frames of
512 × 512 `uint16` is 32 MiB). The copy is the same for every block size, which a test checks, so
it changes no result. PRD §11.2 lists it for completeness, with that statement, rather than as a
value a user tunes.

### Display

The movie panel and `tether.services.movies.open_project_movie` take any `MovieSource`. A TIFF
shows its memory map. Another reader shows a `tether.io.frames.FrameView`, a lazy array view
whose index reads only the frames it touches, so display does not copy the movie.

### An original beside its TIFF export in one batch folder

`tether batch` now gives the `.nd2` files of a folder. A folder that holds `x.nd2` and its TIFF
export `x.tif` would give one movie twice with one output name. The maintainer chose that the
batch **refuses the pair before it writes**, with a message that names both files and says to
move or rename one. The rule is in `output_stems`: two movies of one stem in one folder with
different suffixes are refused with that message. It keys on the suffixes, not on ND2, so a CZI
original beside its export (#1390) is refused the same way. A file named on its own and two files
in two folders are not affected; in two folders the output names take the folder names, as before.

## Consequences

- Good: ND2 movies extract in the application, `tether extract` and `tether batch`, and give the
  traces of their TIFF export.
- Good: the TIFF path is the same code; its tests and reference values do not change.
- Good: a later reader needs only its name in `EXTRACTED_CONTAINERS` and the same test.
- Bad: a non-TIFF extraction needs free space for one copy of the movie in the temporary folder,
  and takes the time to write it.
- Neutral: the rest of the pipeline holds what it held before (the detection image casts each
  channel half to `float64`); this decision bounds the read, not the pipeline.
- Neutral: the Deep-LASI raw-movie import (`tether.gui.deeplasi_executor`) still reads TIFF only,
  through `tether.io.movie.open_movie`, whose refusal now says "this step reads only TIFF".
