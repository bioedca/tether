<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0113 — One movie reader interface, an adapter registry, and a rule for reader packages

- **Status:** accepted
- **Date:** 2026-11-16
- **Deciders:** bioedca (the acceptance criteria of #901, approved 2026-11-16; card D1 option B
  and card A1 option B, 2026-11-14)
- **PRD anchor:** §7.1 FR-EXTRACT, Appendix A (input formats)
- **Milestone:** M20
- **Issue:** [#901](https://github.com/bioedca/tether/issues/901)
- **Builds on:** [ADR-0004](0004-pin-and-hold-dual-lock-isolation.md) (isolated locks)

## Context and problem statement

The import lane of the workflow plan starts with "Movie input: TIFF / ND2 / CZI / SIF / SIRA /
AVI". Tether read one container, uncompressed TIFF, in one class, and each place that opened a movie
called that class. A second container would have needed a change in each of those places, and
nothing said which reader packages may enter the base environment. The maintainer chose Python
readers in the base environment for ND2, CZI and SIF, then SIRA and lossless AVI, and an optional
isolated stack with Bio-Formats for `.vsi` and the other containers (#907). Vendor containers come
after `v1.0.0`. What does a reader look like, how is one chosen, and when may its package enter the
base lock?

## Decision drivers

- **No change of behavior for TIFF.** The extraction tests keep every expected value.
- **The full movie never loads** (PRD §5.2). A frame-range read is a seek, not a load.
- **Refuse rather than guess** (PRD Appendix A). Conversion cannot recover information that a lossy
  codec discarded, and a palette index or an RGB sample is not an intensity.
- **Metadata says where it came from.** A frame time read from the file, a frame time the user
  typed and no frame time are three different facts.
- **The base lock stays installable on four platforms** (`linux-64`, `osx-64`, `osx-arm64`,
  `win-64`), and isolation is kept (ADR-0004).

## Considered options

1. **A `Protocol`, a registry chosen by content then extension, and a package rule** (chosen).
2. **A choice by extension only.** Simple, but a renamed TIFF is refused and a non-TIFF named
   `.tif` reaches the TIFF parser.
3. **An abstract base class that every reader subclasses.** It couples third-party wrappers to
   Tether's class tree, and a structural `Protocol` asks for the same methods without that.
4. **No rule for packages; decide each one in its pull request.** Each decision would then be made
   under the pressure of the feature that needs it.

## Decision outcome

Chosen option: **1**.

### The interface

`tether.io.adapters.MovieSource` is a `runtime_checkable` `Protocol`: `path`, `shape`
(`(n_frames, height, width)`), `n_frames`, `pixel_type` (the on-disk NumPy dtype string, for
example `'>u2'`), `metadata`, `read_frames(start, stop)`, `close()` and the context-manager pair.
`read_frames` requires `0 <= start <= stop <= n_frames` and has no negative indices, so an
off-by-one cannot wrap to the end of the movie. `metadata` is a `MovieMetadata` whose fields
(`frame_time` in seconds, `pixel_size` in micrometres, `channels`) are each a `MetadataValue` with
an origin of `file`, `user` or `unknown`; the value is `None` exactly when the origin is
`unknown`, and the constructor refuses anything else.

### The registry

A `MovieAdapter` is a name, its extensions, a `sniff` of the first 16 bytes, and an `open`.
`select_adapter` takes every adapter whose `sniff` accepts the bytes; of those, the one that claims
the extension wins, else the first registered. **The content decides and the extension only breaks
a tie.** A file that no adapter accepts raises `UnsupportedContainerError`, which lists the
containers Tether reads and names the Bio-Formats route; a file whose name claims a container its
content is not raises the same error and says so. `register_adapter` refuses a duplicate name or a
shared extension, so the tie-break stays unambiguous. The module is Qt-free and imports no reader
package; the built-in adapters register on the first read of the registry.

### The TIFF adapter

`tether.io.movie.MovieReader` is the first adapter, `TIFF_ADAPTER`, unchanged in what it returns. It
now also refuses, by name, a codec that can be lossy (`LossyCodecError`: the JPEG family, JPEG 2000
whole — the tag does not say whether a stream is its lossless mode — JPEG XR, JPEG XL, WebP and
LERC) and a pixel that is not one real sample (`InvalidPixelError`: a palette, more than one sample
per pixel, or a dtype that is not an integer or a float). Both are `ValueError`s, so every caller
that caught the old error still catches them. A lossless compressed TIFF keeps its old "not
memory-mappable" refusal. `open_movie` now reaches the reader through the registry; because its
callers use the TIFF memory map (`MovieReader.data`), it refuses a source from any other adapter
until those callers read through `read_frames`, which is the first container's work (#902).

### The rule for a reader package in the base lock

A reader package enters the base `conda-lock` only when **all** of these hold, each recorded in the
pull request that adds it:

1. **Licence.** OSI-approved and compatible with GPL-3.0-or-later. No non-commercial clause, no
   proprietary SDK, and no vendor library fetched at install or first use.
2. **Distribution.** A conda-forge build for all four locked platforms. A package that is only on
   PyPI, or that lacks a platform, does not enter the base lock.
3. **No JVM and no service.** It runs in-process in CPython. Bio-Formats and anything else that
   needs a JVM goes to the optional isolated stack (#907), as the sidecar and deep stacks do.
4. **Size.** It and every package it adds to the lock together install at most **25 MB** on
   `linux-64`, measured from the lock diff.
5. **Maintenance.** A release within the 18 months before the pull request, a public issue tracker,
   and no archived or "unmaintained" status.
6. **Lazy import.** Only its adapter imports it, inside `open`, so `import tether.io` and every
   other path stay free of it.
7. **Conformance.** Its adapter joins `tests/test_movie_adapter_conformance.py` with one `CASES`
   line and passes every test there, with a small redistributable fixture whose licence and
   provenance are recorded.

A package that fails any of 1 to 5 can still be read through the isolated stack; failing 6 or 7 is
fixed in the adapter, not waived.

### Consequences

- Good: a new container is one adapter, one registry entry and one line in the conformance module;
  no caller changes until the first adapter whose reader has no memory map (#902), and that change
  is the one switch from `.data` to `read_frames`.
- Good: an unknown or renamed container gets a message that says what Tether reads, not a TIFF
  parser error.
- Bad / trade-off: the size and maintenance thresholds are judgement numbers, chosen so the base
  installer stays near its present footprint; a package just over one of them goes to the isolated
  stack even when it would work in the base one.
- Bad / trade-off: the TIFF adapter reads no pixel size or channel names (they are `unknown`); the
  ImageJ calibration is not interpreted here, which keeps this change free of a new metadata claim.
- Follow-up: `tests/test_movie_adapter_conformance.py` (every adapter, and every registered adapter
  must have a case) and `tests/test_movie_adapters.py` (the registry rules, the refusals, the
  origins, the Qt-free import) enforce this record. Containers: #902 to #907; channel layouts:
  #908.

## More information

- `src/tether/io/adapters.py`, `src/tether/io/movie.py`.
- The lossy-codec names are the `tifffile.COMPRESSION` members of the locked tifffile 2026.6.1,
  read from the installed package on 2026-11-16 (Context7 `/cgohlke/tifffile` for the enum and
  `TiffPage` attributes).
- [Movie and file compatibility](../compatibility.md#movie-files) and
  [Troubleshooting](../troubleshooting.md) give the user-facing messages.
