<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0150 — Tether reads SIF with its own reader, and only movies of raw counts

- **Status:** accepted
- **Date:** 2026-11-24
- **Deciders:** bioedca (the acceptance criteria of #904, approved 2026-11-24; card D2 option B,
  2026-11-14, for the test files)
- **PRD anchor:** §7.1 FR-EXTRACT, Appendix A (input formats)
- **Milestone:** M20
- **Issue:** [#904](https://github.com/bioedca/tether/issues/904)
- **Builds on:** [ADR-0113](0113-one-movie-reader-interface-an-adapter-registry-and-a-rule-for-reader-packages.md)
  (the reader interface and the rule for a reader package),
  [ADR-0073](0073-one-manifest-for-reference-data-sets.md) (reference data)

## Context and problem statement

SIF is the container of Andor Solis, the acquisition software of the Andor EMCCD and sCMOS
cameras (iXon, Zyla) that many smFRET setups use. Tether did not read `.sif`, so a user had to
convert each movie to TIFF with other software, outside the provenance record. The format has no
public specification. A file holds up to five data blocks (signal, reference, background, live,
source), each with a text header and its frames as uncompressed little-endian 32-bit floats, and
the header layout changes between Solis versions. Which reader does Tether use, which files does
it accept, in what row order does it return a frame, and where does the metadata come from?

## Decision drivers

- **ADR-0113's rule for a reader package**, all seven points.
- **Refuse rather than guess** (PRD Appendix A). A file that Tether cannot read whole is refused
  with a message, never read in part.
- **A frame read is not a movie load** (PRD §5.2).
- **A bit-for-bit check** against an independent reader and against Solis's own exports, on files
  that Solis wrote.

## Considered options

1. **Tether's own reader of the signal block** (chosen).
2. **sif_parser in the base lock.** The maintained Python reader (BSD-3-Clause; 0.3.6, August 2025,
   on PyPI). It fails rule 2 in practice: conda-forge carries only **0.3.0** (2022), more than
   18 months old (rule 5), and that build lacks the later fixes. Its reader skips a fixed number of
   header lines for each Solis version, and 0.3.6 cannot open the public iXon movie below (a
   `UnicodeDecodeError` in its header parser).
3. **sifreader** (PyPI 0.2.4, 2018). Fails rules 2 and 5.
4. **SIF only through the optional Bio-Formats stack** (#907). Bio-Formats has a `SIFReader`, but it
   needs the JVM (rule 3), and it takes the frames to end 8 bytes before the end of the file. That
   holds for the public iXon movie below, and not for the Solis 4 files that end in an XML trailer
   or carry a background block (the Zyla image and the spectra below), whose frames it would read
   from the wrong offset.

## Decision outcome

Chosen option: **1**. `tether.io.sif` reads SIF with the standard library and NumPy, so the base
lock does not change and rule 6 holds with nothing to import.

### How the reader finds the frames

It reads the block list (line 2), the first record of the signal header (the format version and
the kinetic cycle time, by position), and then **anchors on the image-structure record** rather
than counting lines: the first record of the form `65541|65538 left top right bottom frames
sub-images total-length frame-length` that has the three axis labels before it (each written as
`<length>\n<text>`, the middle one the data type) and a sub-image record after it. Then come one
time-stamp line for each frame and, in the newer versions, a flag line (`0`, or `1` and one more
line for each frame). The frames start after them. **The start is accepted only when the frames
end where the next block's presence flag (`0\n` or `1\n`) or the end of the file is**; the
candidates that the three layouts give must leave exactly one such start. A frame-range read seeks
to the frames it returns.

### What Tether reads

A SIF file opens as a movie only when the signal block is present, its data type is `Counts`, each
frame is **one image area** (one sub-image) whose size divides by its binning, the frame lengths
match the area, and the file holds every declared frame. Anything else raises
`UnsupportedContainerError` with the reason:

- data type other than `Counts`, for example `Counts (Bg Corrected)` or `Counts per second`:
  Solis has processed the stored frames, so they are not the values the camera recorded;
- more than one image area in a frame (multi-track, random-track, fast kinetics);
- frames shorter than the header declares (an incomplete copy, or frames spooled to other files);
- frames that do not end where the header says (compressed frames, or a layout not met).

The pixel type is `<f4`, as Solis stores it. The blocks after the signal (a background, for
example) are not read.

### Row order

**A SIF file stores the bottom row of each frame first. Tether returns the top row first**, as
Solis shows the image and as Solis exports it to TIFF. A movie opened from SIF therefore has the
orientation of the TIFF that a user exported from Solis before this change, so a map or a channel
geometry made on that TIFF still applies. The public Zyla image below fixes this: its frame in
Tether's row order maps onto the 8-bit TIFF that Solis exported from it within one grey level after
a linear fit (correlation 0.99998); the stored order does not (0.64).

### Where the metadata comes from

| Field | Read from | When it is `unknown` |
| --- | --- | --- |
| frame time (s) | the kinetic cycle time of the signal header's first record | one frame, or a value that is not finite and positive |
| pixel size (um) | not read | always: the header gives the sensor's pixel pitch (16 um for an iXon 897), not the size at the sample, and the file does not record the magnification |
| channels | not read | always: the file names no channel |

On the public iXon movie the kinetic cycle time is 0.03035 s and the header's time stamps step by
30,350 µs.

### The test files

- **Two spectra that Solis wrote** (Chukova O, Zenodo doi:10.5281/zenodo.18293266, CC-BY-4.0),
  committed unchanged (8.5 kB and 16.9 kB): one of data type `Counts`, whose frame equals, bit for
  bit, the ASCII export that Solis made of it and its reference TIFF export by **sif_parser 0.3.6**;
  and one of `Counts (Bg Corrected)`, the public refusal case. All 106 SIF files of that record and
  its companion were read: the 9 of `Counts` equal sif_parser, the 97 others are refused.
- **An Andor Zyla image** (Wu Y, Dryad doi:10.5061/dryad.80gb5mkpx, CC0-1.0, 16.8 MB) and the TIFF
  that Solis exported from it, as `download` entries of the ADR-0073 manifest: the weekly
  `reference-data` job checks its pixel digest against sif_parser 0.3.6 and its row order against
  the Solis TIFF.
- **An Andor iXon movie** (Chazot-Franguiadakis L, Zenodo doi:10.5281/zenodo.11185140, CC-BY-4.0,
  250 frames of 512 x 512, 262 MB), checked once in this change and recorded in
  `tests/fixtures/PROVENANCE.md`; it is over the 200 MB download limit.
- **Synthetic movies** of `tests/_sif_synth.py`, which writes the layout of a Solis file of format
  version 65567 at test time: the conformance movie, the binning, the older layouts and one file
  for each refusal. sif_parser 0.3.6 reads its files to the written frames.

### Consequences

- Good: an Andor movie opens through the registry with its frame time, and every refusal names its
  reason. Extraction does not read it yet: #1388 moves the callers of `open_movie` to
  `read_frames`.
- Good: no new package; `import tether.io` stays as light as it was.
- Bad / trade-off: Tether maintains a parser of an undocumented vendor format. A layout it has not
  met is refused (no anchor, or frames that do not end where they must), not misread. The format
  versions checked on real files are 65559, 65566 and 65567.
- Bad / trade-off: a background-corrected or multi-track file is refused even where its signal
  could be read; the user re-saves or exports it. A refusal of `Counts (Bg Corrected)` covers 97 of
  the 106 public spectra above.
- Bad / trade-off: no committed file is a kinetic series that Solis wrote; the public one is too
  large for any tier but `workstation`, so the per-PR tests use the synthetic movies and the
  committed one-frame spectra.

## More information

- `src/tether/io/sif.py`, `tests/test_movie_sif.py`, `tests/_sif_synth.py`,
  `scripts/make_sif_reference.py`.
- The layout was read from the files above and checked against the source of sif_parser 0.3.6
  (BSD-3-Clause); no code of it is copied.
- [Movie containers](../reference/movie-containers.md#sif) is the user reference.
