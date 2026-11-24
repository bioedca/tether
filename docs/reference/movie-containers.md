---
description: "The movie containers Tether reads (TIFF, ZEISS CZI, cellSens VSI), what each reader refuses, and where each metadata value comes from."
---

<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# Movie containers

**Who this page is for.** Anyone who wants to know whether Tether opens a movie file, why it
refuses one, and where each metadata value of an opened movie comes from. It assumes you know the
container your microscope software writes. It does not assume you know Python.

Tether opens a movie through a reader for its container. The first bytes of the file choose the
reader, and the file name only breaks a tie, so a renamed file still opens
([ADR-0113](../adr/0113-one-movie-reader-interface-an-adapter-registry-and-a-rule-for-reader-packages.md)).
Each metadata value has an origin: `file` (read from the movie), `user` (you gave it) or
`unknown` (the file does not hold it, and Tether does not guess it).

| Container | Extensions | Reader |
| --- | --- | --- |
| TIFF | `.tif`, `.tiff` | Tether, with tifffile |
| CZI (ZEISS ZEN) | `.czi` | czifile 2026.6.12 ([ADR-0144](../adr/0144-czi-movies-are-read-with-czifile-and-only-as-one-whole-movie.md)) |
| ND2 (Nikon NIS-Elements) | `.nd2` | Tether's own reader ([ADR-0148](../adr/0148-tether-reads-nd2-with-its-own-reader-and-no-reader-package.md), [ND2 movies](../compatibility.md#nd2-movies)) |
| cellSens VSI (Olympus/Evident) | `.vsi` | Bio-Formats 8.0.0, in the optional Bio-Formats stack ([ADR-0147](../adr/0147-cellsens-vsi-movies-are-read-by-bio-formats-in-an-isolated-fourth-stack.md)) |

The extraction commands (`tether extract`, `tether batch`) read TIFF only for now. A CZI, ND2 or
`.vsi` movie opens through its reader, but extraction refuses it with "extraction reads only TIFF
today". The changes that let extraction read them are planned (#1388 for ND2, #1390 for CZI,
#1398 for `.vsi`). Until then, export the movie to uncompressed TIFF.

## TIFF

[Does Tether fit my data?](../compatibility.md#movie-files) gives what the TIFF reader accepts and
refuses.

| Value | Where it comes from | When it is `unknown` |
| --- | --- | --- |
| Frame time (s) | the ImageJ `finterval` tag | no tag, or a value that is not finite and positive |
| Pixel size (µm) | the `XResolution` and `YResolution` tags, when the ImageJ unit is micrometres | no ImageJ micrometre unit, no tags, the `1/1` placeholder, or the two differ |
| Channels | not read | always |

## CZI

Tether reads a CZI file **only when it can read all of it as one movie**:

- One scene, one channel, one Z plane, and one tile for each frame.
- Every frame at full resolution (no image pyramid), of one size and at one position.
- One frame for each time point from the first to the last, with none missing or repeated.
- One pixel type with one real sample: Gray8, Gray16, Gray32, Gray32Float or Gray64.
- The codec none, Zstd, or Zstd with the byte shuffle.
- One file, not one part of a multi-file acquisition.
- Every pixel of a frame acquired. A frame whose validity mask marks pixels that the camera did
  not cover is refused when it is read.

Any other file is refused, and the message names the reason, for example "this file holds 2
channels". JPEG and JPEG XR can be lossy and the file does not say when they are not, so Tether
refuses them by name. A CZI pixel is little-endian, so a Gray16 movie has the pixel type `<u2`.

| Value | Where it comes from (the CZI XML metadata) | When it is `unknown` |
| --- | --- | --- |
| Frame time (s) | `Information/Image/Dimensions/T/Positions/Interval/Increment`, the interval ZEN records | absent, not a number, or not finite and positive |
| Pixel size (µm) | `Scaling/Items/Distance` `X` and `Y`, stored in metres | either is absent or not positive, or the two differ |
| Channels | the `Name` of the one channel in `Information/Image/Dimensions/Channels` | no name, or not exactly one channel |

To make a CZI movie that Tether reads, export one channel and one position for each file from ZEN
with no compression or with Zstd. For two channels in one camera frame, export the whole frame.
Tether splits it (see [Acquisition geometry](../compatibility.md#acquisition-geometry)).

The CZI reader is checked on a ZEN movie of ZEISS, Zenodo
[doi:10.5281/zenodo.7015307](https://doi.org/10.5281/zenodo.7015307) (CC-BY-4.0): its two frames
are equal, byte for byte, to the same frames read by ZEISS pylibCZIrw 6.1.0.

## cellSens VSI

A `.vsi` file needs the [optional Bio-Formats stack](../install.md#the-optional-bio-formats-stack-cellsens-vsi-movies).
The file begins with a TIFF header, so its extension chooses this reader. A renamed `.vsi` is read
as a TIFF. Keep the `_<name>_` folder that cellSens writes beside the file, because the frames are
in its `.ets` files.

Of the series in the file, Tether reads the one with the most time points, at full resolution. It
refuses the file in these cases:

- Two series are movies of the same length, so Tether cannot tell which one to read.
- The movie has more than one channel or focal plane.
- The pixels are color or palette values, not one intensity.
- The tiles are stored with JPEG or JPEG 2000, which can be lossy.

| Value | Where it comes from (the Bio-Formats OME metadata) | When it is `unknown` |
| --- | --- | --- |
| Frame time (s) | the `TimeIncrement` of the image, or else the mean step of the `DeltaT` of its planes | neither is there, or the step is not positive |
| Pixel size (µm) | `PhysicalSizeX` | not there |
| Channels | the `Name` of the one channel | no name |

Tether has no `.vsi` movie that it may share. So CI checks the reader on OME-TIFF movies through
Bio-Formats, and a laboratory `.vsi` movie is checked on the maintainer's workstation.
