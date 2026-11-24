<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0144 — Tether reads ND2 with its own reader and no reader package

- **Status:** accepted
- **Date:** 2026-11-24
- **Deciders:** bioedca (the acceptance criteria of #902, approved 2026-11-24; card "Choose how
  Tether reads ND2 files, because the nd2 package is too big", option "Tether's own small ND2
  reader", 2026-11-24 18:15Z; card D2 option B, 2026-11-14)
- **PRD anchor:** §7.1 FR-EXTRACT, Appendix A (input formats)
- **Milestone:** M20
- **Issue:** [#902](https://github.com/bioedca/tether/issues/902)
- **Builds on:** [ADR-0113](0113-one-movie-reader-interface-an-adapter-registry-and-a-rule-for-reader-packages.md)
  (the reader interface and the rule for reader packages)

## Context and problem statement

ND2 is the movie container of Nikon NIS-Elements. ADR-0113 lets a reader package enter the base
lock only when it passes seven rules, among them a licence rule and a size rule: the package and
everything it adds to the lock install at most 25 MB on `linux-64`. The maintained Python reader is
the `nd2` package (BSD-3-Clause, 0.12.0). Its conda-forge build depends on
`resource_backed_dask_array`, which depends on the full `dask` metapackage. A minimal lock of the
base environment plus `nd2>=0.12,<0.13` (conda-lock 4.0.1 with micromamba 1.5.12, every other
package held) added **50 packages on `linux-64`, about 60 MB to download**: `distributed`,
`bokeh`, `pyarrow` with `libarrow`, the AWS, Azure and Google Cloud C++ SDKs, gRPC, and `ruff`
(through `xsdata`). The older `nd2reader` has had no release since 2021 and fails the maintenance
rule. How does Tether read ND2?

## Decision drivers

- **The base lock stays installable and near its present size** (ADR-0113 rule 4, ADR-0004).
- **Refuse rather than guess** (PRD Appendix A): a file that cannot be read fully is refused with
  a message, never read in part.
- **Bit-for-bit pixels** against an independent reference export (#902).
- **Metadata says where it came from** (ADR-0113): frame time, pixel size and channel names each
  carry an origin.

## Considered options

1. **Tether's own reader of ND2 format version 3, for the files it can read fully** (chosen).
2. **ND2 only through the optional Bio-Formats stack** (#907). No ND2 code in the base install,
   but no ND2 at all until that stack exists, and then only with Java.
3. **Admit `nd2` and change rule 4.** The base lock and every installer grow by the 50 packages
   above, for one container.

## Decision outcome

Chosen option: **1**, by the maintainer's card of 2026-11-24.

### What the reader reads

`tether.io.nd2.Nd2Movie` reads the chunk layout of ND2 version 3 (NIS-Elements 4.0 and later):
the start chunk, the chunk map whose place the last 40 bytes give, the binary metadata tree
(`ImageAttributesLV!`, `ImageMetadataLV!`, `ImageMetadataSeqLV|0!`), and one `ImageDataSeq|i!`
chunk per frame, which holds the acquisition time in milliseconds (one float64) and then the
pixels, little-endian. It reads only:

- one sample per pixel (`uiComp` 1), of 8, 16 or 32 bits in memory (`<u1`, `<u2`, `<f4`);
- frames that are not compressed (`eCompression` 2) and not tiled;
- rows with no padding (`uiWidthBytes` = width × bytes per pixel);
- no loop other than time: an XY-position, Z, spectral or custom loop with more than one step
  is refused, and the message names it;
- every frame that `uiSequenceCount` declares present as a complete chunk.

Everything else is refused at open with a message that names the cause:
`LossyCodecError` for lossy compression and for the first ND2 format (a JPEG 2000 file),
`InvalidPixelError` for more than one component or an unknown pixel width, and
`UnsupportedContainerError` for the rest. Padded rows are refused because the two reference
readers disagree on them: the `nd2` package skips the padding, Bio-Formats 7.3.1 reads it as
pixels. `read_frames` reads only the frames asked for and returns a copy.

### Metadata and its origin

| Field | Value read | Origin |
|---|---|---|
| `frame_time` | (time of the last frame − time of the first) / (frames − 1), from the per-frame acquisition times, in seconds | `file`; `unknown` for one frame or a non-positive interval |
| `pixel_size` | `SLxPictureMetadata.dCalibration`, µm per pixel | `file` when `bCalibrated` is set; else `unknown` |
| `channels` | `sDescription` of each picture plane, in file order | `file`; `unknown` when the file has no plane list |

The frame time is the measured mean interval, not the period that the time loop asked for,
which can be zero ("as fast as possible").

### Registry and callers

`ND2_ADAPTER` (`.nd2`) registers after TIFF. Its sniff accepts the start chunk of version 3 and
the JPEG 2000 signature of version 1, so an old file gets its own refusal. The built-ins now
register all together or not at all, so a failed first read leaves nothing half-registered.
`open_movie`, which extraction and the movie panel use, still accepts only the TIFF reader, whose
memory map those callers read; an ND2 movie opens through the registry
(`open_movie_source`) and is refused by extraction with a message. A folder or a pattern given
to `tether batch` contributes only the files of the readers extraction reads
(`tether.io.movie.EXTRACTED_CONTAINERS`), so an ND2 original beside its TIFF export is not taken
as a second movie that would fail and collide with the TIFF's output name. Moving those callers to
`read_frames` is a separate change of its own.

### Consequences

- Good: no new package; the base lock does not change; `import tether.io` stays as light as it
  was.
- Good: the refusals are explicit and tested, one synthetic file for each.
- Bad / trade-off: Tether now maintains a parser of a vendor format. A file from a future
  NIS-Elements whose layout differs is refused (the version and structure checks fail), not
  misread, and the Bio-Formats route (#907) remains for it.
- Bad / trade-off: compressed (lossless), padded, multi-channel and multi-position ND2 files are
  refused until a change adds them with a public test file for each.
- Follow-up: `tests/test_movie_nd2.py` and the ND2 case of
  `tests/test_movie_adapter_conformance.py` enforce this record.

## More information

- Test files: two public CC-BY-4.0 ND2 files from Zenodo
  ([doi:10.5281/zenodo.17487086](https://doi.org/10.5281/zenodo.17487086) and
  [doi:10.5281/zenodo.24363428](https://doi.org/10.5281/zenodo.24363428)) and a synthetic
  writer, `tests/_nd2_synth.py`; their provenance, the reference export and its tool are in
  `tests/fixtures/PROVENANCE.md` and `tests/fixtures/nd2/reference.json`.
- The pixel reference is a TIFF export by Bio-Formats `bfconvert` 7.3.1; the `nd2` package 0.12.0
  gives the same pixels. The synthetic files read the same in the `nd2` package 0.12.0 (frames,
  frame times, pixel size, channel names) and in Bio-Formats 7.3.1 (frames).
- The layout was read from the source of the `nd2` package 0.12.0 (BSD-3-Clause) and checked on
  the files above; no code of it is copied.
- [Movie and file compatibility](../compatibility.md#nd2-movies) and
  [Troubleshooting](../troubleshooting.md) give the user-facing messages.
