<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0154 — Tether reads AVI with its own reader, and only uncompressed 8-bit gray

- **Status:** accepted
- **Date:** 2026-11-26
- **Deciders:** bioedca (the acceptance criteria of #906; card D2 option B, 2026-11-14, for the
  test files)
- **PRD anchor:** §7.1 FR-EXTRACT, Appendix A (input formats)
- **Milestone:** M20
- **Issue:** [#906](https://github.com/bioedca/tether/issues/906)
- **Builds on:** [ADR-0113](0113-one-movie-reader-interface-an-adapter-registry-and-a-rule-for-reader-packages.md)
  (the reader interface and the rule for a reader package)

## Context and problem statement

G02, lane IMPORT, lists AVI as an input container, and Tether did not read `.avi`. A user had to
convert each movie to TIFF with other software first, outside the provenance record. An AVI file
is a RIFF container that can hold its frames in any of many codecs, and most of them (Motion JPEG,
MPEG-4, H.264) can be lossy: then the pixel values are not the measured values. Which reader does
Tether use, which files does it accept, and where does the metadata come from?

## Decision drivers

- **ADR-0113's rule for a reader package.**
- **Refuse rather than guess** (PRD Appendix A). A file that Tether cannot read whole is refused
  with a message, never read in part.
- **A frame read is not a movie load** (PRD §5.2).
- **A bit-for-bit check** against an independent AVI reader.

## Considered options

1. **Tether's own reader, uncompressed 8-bit gray only** (chosen).
2. **PyAV** (`av` 19.0.2, BSD-3-Clause, on conda-forge for the four platforms), the binding of
   FFmpeg, which would also decode the lossless codecs (FFV1, HuffYUV). Its conda-forge build
   depends on `ffmpeg` 9.x (GPL-2.0-or-later, 14.7 MB compressed on `linux-64`). A solve of
   `python=3.12 av=19.0.2` on conda-forge `linux-64` (micromamba 2.8.1 `--dry-run`, 2026-11-26)
   adds **65 packages that the base lock does not hold, 113 MB to download**, among them FFmpeg,
   OpenVINO and the Intel media driver. It fails rule 4 of ADR-0113 (25 MB installed) by a wide
   margin. PyAV 19.0.2's own documentation (Context7, `/pyav-org/pyav/v19.0.2`) confirms it needs
   FFmpeg's libraries (`libavcodec`, `libavformat`, `libswscale` and others), bundled or the
   user's own.
3. **imageio with its FFmpeg plugin, or OpenCV.** imageio (in the base lock) reads AVI only
   through `imageio-ffmpeg` or PyAV, neither in the lock. `imageio-ffmpeg` ships an FFmpeg binary
   and reads it through a pipe; `libopencv` 5.1.0 (Apache-2.0, 36.1 MB compressed) depends on
   `ffmpeg` too. Both fail rule 4 for the same reason as option 2, and OpenCV's `VideoCapture`
   gives BGR frames by default.

## Decision outcome

Chosen option: **1**. `tether.io.avi` reads the RIFF structure with the standard library and
NumPy, so the base lock does not change. The layout comes from Microsoft's AVI RIFF and
`BITMAPINFOHEADER` references and the OpenDML AVI 2.0 extension. A lossless codec is a later
change: a reader for it needs FFmpeg, through the isolated stack of ADR-0147 or a package that
meets the ADR-0113 rule.

### What Tether reads

- An uncompressed DIB (`biCompression` = `BI_RGB`) of 8 bits a pixel, with a color table of 256
  entries that is the gray ramp (entry *i* has blue = green = red = *i*; the fourth byte is not
  read, ImageJ writes 0 and FFmpeg 255). The stored index is then the gray level. Rows are
  bottom-up for a positive `biHeight` and top-down for a negative one, and each row is padded to a
  multiple of 4 bytes. ImageJ 1.54u's `AVI_Writer` writes this form for an 8-bit image saved with
  no compression (bottom-up, 256-entry table from the image's lookup table, `00db` chunks).
- The 8-bit gray FourCCs `Y800`, `Y8` and `GREY`, top-down whatever the sign of the height (the
  rule for FourCC formats) and not padded. FFmpeg 6.1.1 writes `Y800`, unpadded, for `gray`.
- One video stream. An audio stream is left out. Frames are the `##db` and `##dc` chunks of that
  stream in the `movi` lists, in file order, `rec` lists included, across the `RIFF AVI` part and
  every OpenDML `RIFF AVIX` part, so a movie larger than 1 GB is read whole. The `idx1` and
  OpenDML index chunks are not read: the walk over the chunk headers finds the same frames and
  does not depend on a writer filling an index correctly.
- The open reads the headers and the 8-byte header of each chunk, not the frames.

### What Tether refuses

- A codec that can be lossy, with `LossyCodecError`, by name (Motion JPEG, MPEG-4, DivX, Xvid,
  H.264, H.265, Windows Media Video, VC-1, VP8, VP9, AV1, Cinepak, Indeo, MPEG-1 and 2, Microsoft
  Video 1, DV).
- A lossless codec that Tether does not decode (FFV1, HuffYUV, Lagarith, PNG, zlib, MSZH,
  MagicYUV, Ut Video, lossless JPEG, run-length DIBs), and a codec it does not know, with
  `UnsupportedContainerError`: Tether cannot tell whether an unknown codec is lossless.
- Color pixels (a DIB of 16, 24 or 32 bits, or `BI_BITFIELDS`), and an 8-bit DIB whose color
  table is not the gray ramp (an inverted lookup table, or FFmpeg's `pal8` RGB 3-3-2 palette),
  with `InvalidPixelError`: its pixels are table indices, not the stored intensities.
- 16-bit gray. No AVI FourCC for it is standard, and FFmpeg 6.1.1 refuses to write `gray16le` to
  AVI, so Tether has no writer to check a reading against.
- A main header that sets `AVIF_MUSTUSEINDEX`: Microsoft's `AVIMAINHEADER` reference says the
  index then gives the presentation order, which can differ from the order of the chunks in the
  file, and Tether reads frames in file order (Codex on #1457).
- Two or more video streams; a color-table change (`##pc`) inside the movie; a frame chunk of the
  wrong size; a frame chunk of no bytes (a dropped frame, which a player shows as a repeat of the
  one before, so the frame was not measured); fewer or more frames than the stream header's
  `dwLength` (ImageJ and FFmpeg write the whole movie's count there, OpenDML parts included); a
  chunk that runs past its list or the file; and bytes after the last RIFF part. An incomplete
  copy is refused by one of these.

### Where the metadata comes from

| Field | Read from | When it is `unknown` |
| --- | --- | --- |
| frame time (s) | not read | always |
| pixel size (um) | not read | always: AVI does not record it |
| channels | not read | always: AVI names none |

**The frame time is not read.** An AVI records `dwRate / dwScale`, the rate at which a player
shows the frames. The writing program sets it, and often by default: ImageJ 1.54u writes its
animation rate, 7 frames a second unless the user changed it (`Animator`, `Prefs.FPS` default 7.0),
and rounds it to a whole number in `dwRate`; FFmpeg's raw input defaults to 25. A file carries no
mark that tells a default from an acquisition interval, and a wrong frame time with the origin
`file` would pass into dwell times and rates as if measured. So the value is `unknown`, and the
user gives it (origin `user`).

### The test files

- **Three readable movies**, 8 frames of 37 x 23 from one seeded array: `gray_y800_37x23x8.avi`
  (FFmpeg 6.1.1, `Y800`), `gray_dib_top_down_37x23x8.avi` (FFmpeg 6.1.1, an 8-bit DIB with the gray
  ramp, negative height, rows padded to 40 bytes) and `gray_dib_bottom_up_37x23x8.avi`
  (`tests/_avi_synth.py`, positive height, as ImageJ writes it; FFmpeg writes no bottom-up DIB).
  Each has a reference TIFF of the frames that FFmpeg 6.1.1 decodes from that file, saved by
  tifffile 2026.6.1; Tether's frames equal it bit for bit.
- **Four refusals written by FFmpeg 6.1.1**: FFV1, Motion JPEG, `pal8` and `bgr24`.
- **Synthetic files** of `tests/_avi_synth.py`: the conformance movie, the OpenDML, `rec` and
  audio-stream variants, and each refusal.

No public AVI sample was needed: the frames are Tether's own, seeded, and the writing and the
decoding tools and their versions are recorded (card D2 option B allows a public file; a file
Tether writes with a recorded tool meets the same criterion of a known source and licence).

### Consequences

- Good: an uncompressed gray AVI opens through the registry, and every refusal names its reason.
  Extraction does not read it yet, as for SIF and SIRA: an adapter enters `EXTRACTED_CONTAINERS`
  once its movies extract to the same traces as their TIFF export.
- Good: no new package.
- Bad / trade-off: a movie saved with a lossless codec (FFV1, HuffYUV) is refused; the user
  exports it again without compression, or to TIFF.
- Bad / trade-off: AVI is 8-bit here, so a 16-bit acquisition exported to AVI has already lost
  its range (ImageJ scales a 16-bit stack to 8 bits when it writes AVI). Tether reads what the file
  holds and says so in the reference page.
- Bad / trade-off: the frame time of every AVI is `unknown`, also when the writer recorded the
  true interval.

## More information

- `src/tether/io/avi.py`, `tests/test_movie_avi.py`, `tests/_avi_synth.py`,
  `scripts/make_avi_fixtures.py`; `tests/fixtures/PROVENANCE.md` has the files, the tools and
  their versions.
- [Movie containers](../reference/movie-containers.md#avi) is the user reference.
