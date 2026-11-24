<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0144 — CZI movies are read with czifile, and only as one whole movie

- **Status:** accepted
- **Date:** 2026-11-24
- **Deciders:** bioedca (the acceptance criteria of #903, approved 2026-11-24; card D2 option B,
  2026-11-14, for the test file)
- **PRD anchor:** §7.1 FR-EXTRACT, Appendix A (input formats)
- **Milestone:** M20
- **Issue:** [#903](https://github.com/bioedca/tether/issues/903)
- **Builds on:** [ADR-0113](0113-one-movie-reader-interface-an-adapter-registry-and-a-rule-for-reader-packages.md)
  (the reader interface and the rule for a reader package), [ADR-0004](0004-pin-and-hold-dual-lock-isolation.md)
  (isolated locks), [ADR-0073](0073-one-manifest-for-reference-data-sets.md) (reference data)

## Context and problem statement

The import lane of the workflow plan lists CZI, the container of ZEISS ZEN, as an input. Tether
did not read `.czi`, so a user had to convert each movie to TIFF with other software, outside the
provenance record. A CZI file can hold much more than a movie: scenes, channels, Z planes, mosaic
tiles, image pyramids, phases and illumination angles, with lossless or lossy codecs. Which
package reads it, which of those files does Tether accept, and where do the frame time, the pixel
size and the channel name come from?

## Decision drivers

- **ADR-0113's rule for a reader package**, all seven points.
- **Refuse rather than guess** (PRD Appendix A). A file that Tether cannot read whole is refused
  with a message, never read in part.
- **A frame read is not a movie load** (PRD §5.2).
- **A bit-for-bit check against an independent reader**, on a file that ZEN wrote.

## Considered options

1. **czifile, in the base lock** (chosen).
2. **pylibCZIrw** (ZEISS libCZI, LGPL-3.0). The vendor's own reader, and the one that makes the
   reference here. Rejected for the base lock: it is a compiled C++ library on PyPI, and the
   ADR-0113 rule asks for a conda-forge build on all four platforms.
3. **aicspylibczi or bioio-czi** (GPL-3.0). Compiled wrappers of libCZI, and bioio-czi brings the
   bioio stack. Each is larger than czifile, for no reading that the adapter needs.
4. **Tether's own ZISRAW parser**, as #902 did for ND2. Rejected: the ZISRAW specification is
   confidential, a maintained BSD reader passes the rule, and a parser of our own would have to
   track ZEN's changes alone.

## Decision outcome

Chosen option: **1**.

### The package

**czifile 2026.6.12** (Christoph Gohlke, BSD-3-Clause, conda-forge `noarch`) is pinned exactly
in `environment.yml`. Its API "is not stable yet", and 2026.6.12 is the newest revision whose
imagecodecs floor (`>=2026.6.6`) the locked imagecodecs 2026.6.26 already meets; the next
revisions ask for imagecodecs 2026.8.16, which would move a compiled package on four platforms.
The conda-forge recipe depends on **xarray**, so the lock gains two packages and moves nothing
else: czifile 2026.6.12 and xarray 2026.7.0, on each of the four platforms.

The rule of ADR-0113, point by point:

1. **Licence.** czifile BSD-3-Clause, xarray Apache-2.0; both compatible with GPL-3.0-or-later.
   No vendor SDK; czifile decodes with imagecodecs, which the lock already had.
2. **Distribution.** Both are `noarch` conda-forge builds, so all four platforms.
3. **No JVM and no service.** Pure Python.
4. **Size.** On `linux-64` the two packages download 1,087,261 B and install 7,325,672 B
   (czifile 289,836 B, xarray 7,035,836 B, measured from the `.conda` archives of the lock), under
   the 25 MB limit.
5. **Maintenance.** czifile released 2026.11.12, twelve days before this record, from a public
   tracker (github.com/cgohlke/czifile); xarray released 2026.7.0 in July 2026.
6. **Lazy import.** `tether.io.czi` imports czifile inside `CziMovie.__init__` only; a test runs
   `import tether.io` and the registry in a fresh interpreter and checks that neither czifile nor
   xarray was imported. czifile itself does not import xarray when it is imported.
7. **Conformance.** The adapter joins `tests/test_movie_adapter_conformance.py` with one `CASES`
   line and passes every test there.

The re-lock used conda-lock 4.0.1 with micromamba 1.5.12 and `--update czifile`. conda-lock
re-solves every package when the updated name is not yet in the lock, so its solver was run with a
local change that treats a new name as an update and asks micromamba to `install` it into the
locked environment, which adds a package and moves none. The result is the added packages and
ten dependency strings, in the entries of `glib` and `glib-tools` (`win-64`), `libclang13` and
`typing-extensions`, that write an exact pin as `2.88.2` where the lock had `==2.88.2`: the same
constraint, as micromamba 1.5 writes it. `conda-lock --check-input-hash` without the change then
reports the lock up to date.

### What Tether reads

A CZI file opens as a movie only when it is **one movie, whole**: one scene, one value of every
dimension that is not T, Y or X (channel, Z plane, phase, illumination, rotation, block, view),
one mosaic tile, no image pyramid, every subblock at full resolution and of one size and position,
one pixel type of one real sample, and one subblock for each time point from the first to the last
with none missing or repeated. It is not part of a multi-file acquisition. Anything else raises
`UnsupportedContainerError` with the reason (for example "holds 2 channels"). A pixel that is not
one real sample (BGR colour, complex) raises `InvalidPixelError`. JPEG and JPEG XR, which can be
lossy and do not say when they are not, raise `LossyCodecError`. The codecs read are none, Zstd and
Zstd with the byte shuffle; any other (LZW, the undocumented lossless JPEG, camera RAW) is refused,
because no test file exercises it.

`read_frames` reads and decodes the subblock of each frame that it returns, under a lock, since
the source shares one file handle between threads. The pixel type is little-endian (`'<u2'` for
Gray16), as CZI stores it.

### Where the metadata comes from

| Field | Read from the CZI XML document | When it is `unknown` |
| --- | --- | --- |
| frame time (s) | `Metadata/Information/Image/Dimensions/T/Positions/Interval/Increment` | absent, not a number, not finite, or not positive |
| pixel size (um) | `Metadata/Scaling/Items/Distance` `X` and `Y`, in metres | either is absent or not positive, or the two differ |
| channels | the `Name` of the one `Channel` of `Metadata/Information/Image/Dimensions/Channels` | no name, or not exactly one channel listed |

The frame time is the interval that ZEN records, not one computed from the `TimeStamps`
attachment, whose unit czifile documents as depending on the producer (seconds, or OLE dates).
On the public file the two agree: increment 0.172 s, time stamps 0.2312 s and 0.4025 s apart by
0.1713 s.

### The test files

The committed CZI files (`tests/fixtures/czi/`) are written by **ZEISS libCZI** through
pylibCZIrw 6.1.0 (`scripts/make_czi_fixtures.py`), with seeded synthetic pixels: the
conformance movie (64 Zstd frames holding the conformance module's frames), a small uncompressed
movie, and one file for each refusal. libCZI writes no JPEG, JPEG XR or LZW, so those cases are
made in the test by changing the codec field of each directory entry.

The file that ZEN wrote is `T=2_CH=1.czi` of Zenodo **doi:10.5281/zenodo.7015307** (Rhode S,
ZEISS, CC-BY-4.0), the card D2 option B case: a public file whose licence permits redistribution.
It is 1.57 MB, over the 1 MB git tier, so it is a `download` entry of the ADR-0073 manifest, and
the bit-for-bit test runs in the weekly `reference-data` job. Its reference, the two frames as
**pylibCZIrw 6.1.0** reads them, saved by tifffile 2026.6.1 as an uncompressed TIFF, is committed
(262,518 B, CC-BY-4.0). czifile and libCZI are two independent readers of the format; the two
frames agree byte for byte. The two-channel movie of the same record is the public refusal case.

### Consequences

- Good: a ZEN movie opens through the registry with its frame time, pixel size and channel name,
  each with its origin. Extraction does not read it yet: #1388 moves the callers of `open_movie`
  to `read_frames`, and #1390 then adds CZI to the containers that extraction reads.
- Good: every refusal names its reason, and the message says how to export a movie that Tether
  reads.
- Bad / trade-off: a two-channel CZI, the usual output of a two-camera or sequential acquisition,
  is refused. Channel layouts across CZI channels are not decided here; #908 owns channel layouts.
- Bad / trade-off: the bit-for-bit test on a ZEN-written file runs weekly, not on each pull
  request, because the file is over the git-tier limit; every pull request runs the conformance
  tests on the libCZI-written files.
- Bad / trade-off: czifile is pinned at a June 2026 revision; a later bump of imagecodecs can
  bring czifile with it, in its own pull request.

## More information

- `src/tether/io/czi.py`, `tests/test_czi_adapter.py`, `scripts/make_czi_fixtures.py`.
- czifile 2026.6.12 read from its installed source and docstring on 2026-11-24 (the API
  `CziFile.subblock_directory`, `CziDirectoryEntryDV`, `read_segment_data`, `xml_element`,
  `CziCompressionType`, `CziPixelType`); the conda-forge recipe and sizes from the `.conda`
  archives that the lock names.
- [Movie containers](../reference/movie-containers.md) is the user reference.
