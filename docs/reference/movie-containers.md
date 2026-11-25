---
description: "The movie containers Tether reads (TIFF, CZI, ND2, SIF, cellSens VSI, SIRA), what each refuses, and where the frame time, pixel size and channels come from."
---

<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# Movie containers

**Who this page is for.** Anyone who wants to know whether Tether opens a movie file, why it
refuses one, and where each metadata value comes from. It assumes you know the
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
| cellSens VSI (Olympus/Evident) | `.vsi` | Bio-Formats 8.0.0, in the optional Bio-Formats stack ([ADR-0147](../adr/0147-cellsens-vsi-movies-are-read-by-bio-formats-in-an-isolated-fourth-stack.md)) |
| SIRA (MASH-FRET) | `.sira` | Tether ([ADR-0152](../adr/0152-tether-reads-sira-with-its-own-reader-and-only-the-current-layout.md)) |

Extraction (the application, `tether extract` and `tether batch`) reads TIFF and ND2
([ADR-0149](../adr/0149-extraction-reads-every-registered-reader-through-read-frames.md)). A CZI,
SIF, cellSens VSI or SIRA movie opens through its reader, but extraction refuses it. The message is
"this CZI movie opens, but extraction does not read CZI yet", or the same for SIF, cellSens VSI or
SIRA. The change that lets extraction read CZI is planned (#1390), and the one for `.vsi` is #1398.
SIF and SIRA each need a change of their own. Until then, export the movie to uncompressed TIFF.

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
- The format version is 65559 or older, or 65566 or newer. A version between them is a layout
  that Tether has not met, so it is refused.
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
| Frame time (s) | the `TimeIncrement` of the image, or else the mean step of the `DeltaT` of its planes | neither is there, or the step is not a positive number |
| Pixel size (µm) | `PhysicalSizeX` and `PhysicalSizeY`, when they agree | either is not there, the two differ, or a value is not positive |
| Channels | the `Name` of the one channel | no name |

Tether has no `.vsi` movie that it may share. So CI checks the reader on OME-TIFF movies through
Bio-Formats, and a laboratory `.vsi` movie is checked on the maintainer's workstation.

## SIRA

MASH-FRET saves a `.sira` file when it exports a simulated movie (Simulation module). It also
saves one for an edited movie or an average image (Video processing module). Tether reads a SIRA
file **only when it can read all of it**:

- The first line is the header of MASH-FRET 1.003.41 or later (`MASH-FRET exported binary graphic
  Version: …`). An older header (`SIRA exported binary graphic`, `MASH smFRET exported binary
  graphic`), or MASH-FRET 1.003 before 1.003.41, is refused. Those layouts can hold 16-bit pixels
  and no offsets, and Tether has not been checked against them.
- The width, the height and the frame count in the header are whole numbers of 1 or more.
- The file holds every frame that the header declares, and nothing after them. An incomplete copy
  is refused.
- Each frame's offset is a finite number of 0 or more (see below).

Any other file is refused, and the message names the reason. An example is "this file has the
older header 'SIRA exported binary graphic'". MASH-FRET stores each pixel as a 32-bit float, so
the pixel type is `<f4`.

**Offsets.** When a frame has negative values, MASH-FRET adds the size of the lowest value to
each pixel before it stores the frame. It stores that number, the offset, after the frame.
Tether takes the offset off again, so each pixel is the value that MASH-FRET wrote. MASH-FRET's
own reader takes it off when it reads one frame, but adds it again when it reads the whole movie
([ADR-0152](../adr/0152-tether-reads-sira-with-its-own-reader-and-only-the-current-layout.md)).
On a frame with no negative value the offset is 0, and the two agree.

**Row order.** Frame row 0 is the top row of the image as MASH-FRET shows it. MASH-FRET's TIFF
export writes that row first too.

| Value | Where it comes from (the SIRA header) | When it is `unknown` |
| --- | --- | --- |
| Frame time (s) | the frame time | one frame (an average image), or a value that is not finite and positive |
| Pixel size (µm) | not read | always. The file does not record it |
| Channels | not read | always. Two or more channels are side by side in each frame, and the file names none |

Two channels side by side in one frame are one movie, and Tether splits the frame (see
[Acquisition geometry](../compatibility.md#acquisition-geometry)).

The SIRA reader is checked on the test movies of the MASH-FRET repository
([RNA-FRETools/MASH-FRET](https://github.com/RNA-FRETools/MASH-FRET), commit 2877992, GPL-3.0). A
64 x 64 movie of 200 frames and a 256 x 256 movie of 20 frames are checked. Each equals, bit for
bit, MASH-FRET's own reader `readSira.m` run under GNU Octave 8.4.0. A movie that MASH-FRET's writer stored with
offsets gives back the frames that were written.
