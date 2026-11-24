---
description: "The movie containers Tether reads (TIFF, ZEISS CZI, Andor SIF), what each reader refuses, and where the frame time, pixel size and channel name come from."
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
| SIF (Andor Solis) | `.sif` | Tether ([ADR-0150](../adr/0150-tether-reads-sif-with-its-own-reader-and-only-raw-counts.md)) |

The extraction commands (`tether extract`, `tether batch`) read TIFF only for now. A CZI, ND2 or
SIF movie opens through the reader, but extraction refuses it with "extraction reads only TIFF
today". The changes that let extraction read CZI and ND2 are planned (#1388 for ND2, #1390 for
CZI). SIF needs a change of its own. Until then, export the movie to uncompressed TIFF.

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

## SIF

Andor Solis saves a `.sif` file with up to five data blocks. They are the signal, a reference, a
background, a live image and a source. The movie is the signal block. Tether reads a
SIF file **only when it can read all of it as one movie of counts**:

- The signal block is present, and its data type is `Counts`. A file whose stored frames Solis
  has processed (`Counts (Bg Corrected)`, `Counts per second` and the like) is refused, because
  those are not the values that the camera recorded.
- One image area for each frame. A multi-track, random-track or fast-kinetics acquisition is
  refused.
- Every frame that the header declares is in the file. A file cut short, or an acquisition that
  Solis spooled to other files, is refused.
- The frames end where the header says. Any other layout is refused, not guessed.

Any other file is refused, and the message names the reason. An example is "this file holds
frames of the data type 'Counts (Bg Corrected)'". Solis stores each count as a 32-bit float, so the pixel
type is `<f4`. The other blocks (a background, for example) are not read.

**Row order.** A SIF file stores the bottom row of each frame first. Tether gives the top row
first, as Solis shows the image and as Solis exports it to TIFF. A movie that you open as SIF
therefore has the orientation of the TIFF you exported from Solis. A map or a channel layout that
you made on that TIFF still fits.

| Value | Where it comes from (the SIF header) | When it is `unknown` |
| --- | --- | --- |
| Frame time (s) | the kinetic cycle time | one frame, or a value that is not finite and positive |
| Pixel size (µm) | not read | always. The header gives the camera's pixel size on the sensor (16 µm for an iXon 897), not the size at the sample |
| Channels | not read | always. The file names no channel |

To make a SIF movie that Tether reads, save a kinetic series of raw counts with one image area,
without background correction. Two channels side by side on one camera are one image area, and
Tether splits the frame (see [Acquisition geometry](../compatibility.md#acquisition-geometry)).

The SIF reader is checked on files that Solis wrote. Two spectra of Zenodo
[doi:10.5281/zenodo.18293266](https://doi.org/10.5281/zenodo.18293266) (CC-BY-4.0) equal Solis's
own ASCII export, value for value. An Andor Zyla image of Dryad
[doi:10.5061/dryad.80gb5mkpx](https://doi.org/10.5061/dryad.80gb5mkpx) (CC0) equals the
independent reader sif_parser 0.3.6, bit for bit. Its row order is that of the TIFF that Solis
exported from it. A 250-frame iXon movie of Zenodo
[doi:10.5281/zenodo.11185140](https://doi.org/10.5281/zenodo.11185140) (CC-BY-4.0) reads whole.
