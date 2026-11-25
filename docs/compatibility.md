---
description: "Check if Tether can read your smFRET acquisition before you install it: the movie geometry, the file formats, the limits and the cost of a dataset."
---

# Does Tether fit my data?

**Who this page is for.** Anyone who is deciding whether to install Tether at all. It
describes what an acquisition must look like for Tether to read it. It also says what Tether
does not do by design, and roughly what a dataset costs in time and disk. Read it before you
download an installer. If your experiment is in the "does not do" list below, nothing later on
this site changes that.

## The short version

Tether reads **uncompressed multi-page TIFF movies from a single camera**. The camera frame is
**split side by side, down the vertical midline, into a left and a right half**. One half is
the donor channel and the other half is the acceptor channel. The movie is recorded with **one
excitation laser** and **two colors**. That is the dual-view TIRF geometry that the whole
pipeline is built around. It is the only geometry that the `tether extract` and `tether batch`
commands split by.

From Python, Tether extracts other single-camera layouts with a channel layout. These are a
top/bottom (stacked) export, or two channels at two free rectangles of the frame. See
[Acquisition geometry](#acquisition-geometry). A Deep-LASI `.tmap` whose crops for each channel
describe the stacked tiles also splits at those crops with `--tmap`. That combination is
untested, so treat it as unsupported.

If that describes your setup, Tether fits. If any of *ALEX/PIE*, *stoichiometry*,
*three-color* or *confocal point detectors* describes your setup, Tether does not fit. See
[What Tether does not do](#what-tether-does-not-do).

If your movies are in a vendor container (`.czi`, `.sif`), export them to uncompressed TIFF
first, and Tether reads them. Tether opens a ZEISS `.czi` movie of one channel and an Andor `.sif` movie of raw counts (see
[Movie containers](reference/movie-containers.md)). Extraction does not read CZI or SIF yet.
Tether reads a current ND2 movie with its own reader, and it extracts the movie as it extracts a
TIFF (see [ND2 movies](#nd2-movies)).

## Movie files

| Requirement | Detail |
|---|---|
| Container | multi-page TIFF; a ZEISS `.czi` or Andor `.sif` movie opens but is not extracted yet ([Movie containers](reference/movie-containers.md)) |
| Compression | **none**. The movie must be uncompressed and contiguous |
| Dimensions | exactly 3-D: `(frames, height, width)` |
| Codecs that can be lossy | **refused by name** (JPEG, JPEG 2000, JPEG XR, JPEG XL, WebP, LERC). The TIFF tags do not show whether a file used a lossless mode, so Tether refuses these codecs rather than guess. Export again from the original acquisition |
| Pixel type | one real sample per pixel: an unsigned or signed integer, or a float, in either byte order; the reference acquisition is 16-bit unsigned big-endian, and byte order is preserved end to end. RGB, palette and complex pixels are refused |
| Geometry | one frame containing both channels; side by side, split down the vertical midline, from the command line; side by side, top and bottom, or two equal rectangles from Python (`ExtractOptions(channel_layout=…)`) |

The reader is `tether.io.movie.MovieReader`. It refuses a file and does not guess. It raises
`ValueError` when:

- The TIFF series is not 3-D.
- The codec can be lossy (the message names it).
- A pixel is not one real intensity sample.
- The file is not memory-mappable, which is what a compressed or non-contiguous TIFF looks
  like from here.
- The memory-map geometry disagrees with the geometry the TIFF directory reported.

That last check is a deliberate second check. Serving frames from a mismatched map would return
wrong data quietly, which is worse than failing.

**The content decides, not the extension.** Every movie opens through the reader registry
in `tether.io.adapters`, which reads the first bytes of the file. A TIFF saved as
`movie.dat` still opens. A file named `movie.tif` that is not a TIFF is refused, and the
message says so. A container that Tether does not read, such as a `.czi`, is refused too.
That message lists what Tether reads (today, TIFF and ND2) and names the Bio-Formats route for
the others.

Extension matching exists only in the Deep-LASI folder-intake wizard. There,
`tether.io.intake.classify_file` maps `.tif`/`.tiff` to the movie role. It maps `.tdat`,
`.tmap`, `.mat`, `.txt` and `.hdf5`/`.h5` to their own legacy roles. It maps anything that it
does not recognize to `unknown`. The wizard collects those files into the `ignored` list of the
discovery result, and it does not drop them silently.

**Frame time.** Tether reads seconds-per-frame from the TIFF only when the file carries an
ImageJ `finterval` tag. Tether rejects a non-finite or non-positive value at that boundary, so
a bad value never reaches the time axis. The reference big-endian acquisitions carry no such
tag. So a natively extracted project records the frame time as *unknown* (`0.0`), and Tether
plots traces against a frame index, not seconds. Nothing is guessed.

The performance envelope is written against a reference acquisition. It is a
512 × 512 × 1700 big-endian `uint16` movie, about **0.83 GiB** for each movie, uncompressed.

## ND2 movies

Tether reads a Nikon NIS-Elements `.nd2` movie with its own reader, `tether.io.nd2`, and no
reader package ([ADR-0148](adr/0148-tether-reads-nd2-with-its-own-reader-and-no-reader-package.md)).
It opens through the registry. `tether.io.adapters.open_movie_source("movie.nd2")` gives the
geometry, the pixel type, a frame-range read and the metadata below.

**Extraction reads an ND2 movie** (the application, `tether extract` and `tether batch`). It copies
the frames to a temporary file, a block of frames at a time. Then it extracts from that copy with
the same steps as for a TIFF. An ND2 movie and its TIFF export therefore give the same traces
([ADR-0149](adr/0149-extraction-reads-every-registered-reader-through-read-frames.md)).

The copy is the size of the movie and is in the system temporary folder. The operating system deletes it
when the extraction ends. A `tether batch` folder can hold an ND2 original beside its TIFF
export (`x.nd2` and `x.tif`). The two files are one movie, so the batch refuses that folder
before it writes anything. Move or rename one of them.

| Requirement | Detail |
|---|---|
| Format | ND2 version 3 (NIS-Elements 4.0 and later). The first ND2 format, a JPEG 2000 file, is refused as a codec that can be lossy. Version 2 is refused by name |
| Compression | **none**. Lossy compression is refused as lossy. Lossless (zlib) compression is refused as not read yet |
| Loops | one time series. A file with more than one XY position, Z plane, spectral plane or custom-loop step is refused, and the message names the loop |
| Pixel type | one sample per pixel of 8, 16 or 32 bits in memory (`uint8`, `uint16`, `float32`), little-endian. More than one component (camera channels or RGB) is refused |
| Rows | no padding at the end of a row. A padded row is refused, because two reference readers disagree on it |
| Frames | every frame the file declares must be present as a complete chunk. A missing or cut frame refuses the whole file |

Each metadata value carries where it came from: `file` or `unknown`. A value that you give
later is `user`:

| Field | Read from | Origin |
|---|---|---|
| Frame time (s) | the acquisition times of the first and the last frame: their difference divided by (frames − 1). This is the measured mean interval, not the period the time loop asked for | `file`. It is `unknown` for one frame or an interval that is not positive |
| Pixel size (µm) | `dCalibration` of the picture metadata | `file` when the file marks the calibration as set, else `unknown` |
| Channels | the description of each picture plane, in file order (one name, since one channel is read) | `file`, or `unknown` when the file has no plane list |

The project checked the reader bit for bit against a Bio-Formats 7.3.1 TIFF export of a public
ND2 file. It also checked the reader against the `nd2` package 0.12.0. The
[fixture record](https://github.com/bioedca/tether/blob/main/tests/fixtures/PROVENANCE.md)
names the files and the tools.

## Acquisition geometry

By default, Tether splits each frame into a left half and a right half. It treats one half as
donor and the other as acceptor. You declare which half is which, with `--donor-side` (default
`left`).

For other optical layouts, `ExtractOptions(channel_layout=…)` takes a `ChannelLayout`
(`tether.imaging.layout`) from Python. A `top-bottom` layout has the channels one above the other,
stacked and not rotated, with the donor `top` or `bottom`. A `rectangles` layout has one free
`[y1, x1, y2, x2]` rectangle for each channel, 1-based and inclusive. The two channels must be the same
size, so Tether refuses a layout that gives two different sizes. It also refuses a rectangle
outside the frame. Overlapping rectangles give a warning.

A channel that the optics turn or mirror takes a recipe (#910,
`tether.imaging.recipe.ChannelRecipe`): `ChannelLayout(donor_recipe=…, acceptor_recipe=…)`. A
recipe is an ordered list of steps that run on the cut of the channel. These are the steps:

- `rotate` by 90, 180 or 270 degrees clockwise.
- `flip` the rows (`v`), the columns (`h`) or both.
- `translate` by whole pixels.
- `crop`.

No step resamples a pixel. A `translate` leaves part of the channel empty, so a later `crop`
must remove that part, or Tether refuses the recipe. After the recipes, the two channels must be
the same size. With a recipe, two `rectangles` of different size are allowed when the recipes
make the channels equal.

Tether stores the layout of each movie, with its recipes, as `/movies/layouts/<movie_id>` in the
project. There is no command-line option for a layout or a recipe yet. An extraction profile
cannot hold a recipe yet.

A setup with two cameras writes the donor and the acceptor to two files. From Python,
`extract_movie(donor_movie, output, acceptor_movie=acceptor_movie)` reads the two files as one
acquisition (#909, `tether.io.pair`). Each file is one whole channel. Thus no layout applies,
and Tether does not use `--donor-side`.

The two movies must have the same frame count, frame time, pixel type and frame size. If not,
Tether refuses the pair and names each difference. Tether does not align two movies in time.

`add_movie_to_project` and `reextract_movie` also take `acceptor_movie`. The project keeps one
`/movies` row for the pair. Its source registry keeps both files with their roles, `donor` and
`acceptor`. A map between the two cameras comes from a bead file pair, through
`tether.imaging.beadmap.bead_map_pair_from_files`.

Tether refuses a `.tmap` or a map of one camera frame for a pair. It also refuses a pair map for
one movie. There is no command-line option for a pair yet.
[ADR-0151](adr/0151-a-movie-pair-is-one-movies-row-named-by-the-checksum-of-both-files.md)
records these rules.

Tether refuses a movie whose frames cannot hold the channel geometry of its `--tmap` or `--map`,
before it splits the movie. The message names the frame size of the movie and the geometry of
both channels of the map. This check catches a movie turned by 90 degrees against a map made on
non-square frames (#887). A square movie turned by 90 degrees has the same frame size as its map,
so this check cannot see it.

The donor-side check also works with a layout. When it finds swapped channels, its message names
a change to the layout. It does not name `--donor-side`. For a half layout, give the other half as
the donor. For a `rectangles` layout, exchange `donor_rect` and `acceptor_rect`. A layout with a recipe
exchanges `donor_recipe` and `acceptor_recipe` as well.

**`--donor-side` is a `tether extract` flag only.** The headless batch runner (`tether batch`)
does not expose it. Its service command,
[`tether.services.batch.run`](https://github.com/bioedca/tether/blob/main/src/tether/services/batch.py),
builds its `MovieJob`s without an `ExtractOptions`. So every movie in a batch run uses the
default **left** donor. If your acquisition puts the donor on the right, do one of these:

- Run each movie through `tether extract`.
- Supply a `.tmap`. Its reference channel then defines the donor.
- Drive `tether.project.batch.run_batch` from Python with
  `extract_options=ExtractOptions(donor_side="right")`.

With `--tmap`, the split comes from the geometry of each channel in the imported Deep-LASI map
instead. `--donor-side` is ignored, because the donor is the reference channel of the map. The
Deep-LASI `processImage` turns each channel by its `Rotation` (0, 90, 180 or 270 degrees,
clockwise). It then mirrors the channel by its `Flip` (`[v, h]`: rows, then columns) and crops
it in that turned frame. The `.tmap` polynomials were fitted in that frame. Tether splits the
movie in the same three steps, and it records the rotation and flip of each channel in
`/movies` next to its crop.

So a turned movie gives the same molecules as an unturned movie of the same scene.
[`tests/test_extract_cli.py::test_extract_imported_tmap_applies_rotation_and_flip`](https://github.com/bioedca/tether/blob/main/tests/test_extract_cli.py)
covers every rotation and flip, on either channel (#559). Tether refuses a `Rotation` or `Flip`
that `processImage` could not have applied, with "could not use --tmap". That is an angle other
than 0, 90, 180 or 270, or a flip value other than 0 or 1. Tether refuses a `Crop` value the
same way unless it is a real, finite whole number (#1190).

Empty or all-zero values, which the reference UCKOPSB map stores, split by the crop alone. This
is tested on synthetic movies only, because the reference data holds no real `.tmap` with a
rotation or a flip.

> ### Getting `--donor-side` backwards raises no error, and relabeling afterwards will not fix it
>
> **A wrong `--donor-side` raises no error during extraction.** Both values are legal, so
> extraction succeeds and every trace looks plausible.
>
> It is worse than a mislabeling. Spot detection is **anchored on the donor half**. Tether
> finds spots in the half that you called donor, and it colocalizes the other channel against
> them. If you get it backwards, you anchor on the acceptor half, so the molecule *set* itself
> is different. You cannot repair the project by swapping the column names afterwards. You also
> cannot repair the efficiencies by taking `1 − E`.
>
> That is a clean mirror only for the uncorrected proximity ratio. Once leakage α and γ are
> applied, it is not even that. Re-extract.
>
> **How to tell.** Find a molecule whose acceptor photobleaches partway through. When the
> acceptor photobleaches, energy transfer stops and the donor de-quenches. So the **donor trace
> steps up** at that instant. If the trace that you labeled "donor" steps *down* while the other
> steps up, your halves are swapped.
>
> Tether makes this check for you after every extraction. It counts each molecule whose dyes
> bleach one after the other, with a clear drop in one channel and a clear rise in the other.
> It warns when at least 80 % of at least 15 such bleach events point to swapped halves.
> `tether batch` lists those movies in its summary.
>
> The check is advisory and cannot decide on every sample. With low FRET, no acceptor, short
> movies or few molecules, it reports "not enough bleach events to check". Then the visual check
> above is the one that remains. See
> [the donor-side check](cli.md#get-donor-side-right-and-read-the-donor-side-check).
>
> This is a correctness risk, not a preference. That is why this page states it: you read this
> page before you install.

## Legacy project files

Besides native extraction, Tether imports existing Deep-LASI and tMAVEN projects. These are
`.tdat`, `.tmap`, `.txt` and `.mat` from Deep-LASI, and SMD `.hdf5`/`.h5` from tMAVEN. See the
[legacy import page](io/legacy-import.md) for what each format carries.

Know two things before you plan a round trip.

**Not every legacy format carries pixel coordinates.** The Deep-LASI `.tdat` and `.mat` do. The
`.txt` intensity export and the tMAVEN SMD do not, whatever the SMD was exported from. Without
coordinates, nothing ties a trace back to the spot it came from.

**Coordinates alone are not enough: the `.mat` is mandatory.** Tether rebuilds a project from a
legacy import *without* re-extraction. So it needs the pre-integrated traces of each molecule,
and those live only in the `.mat`. The `.tdat` carries coordinates and correction factors but
**no traces**. A movie + `.tdat` set therefore has coordinates and still cannot be
reconstructed. The import wizard classifies it as skipped and names the missing `.mat`
([`_can_reconstruct`](https://github.com/bioedca/tether/blob/main/src/tether/gui/deeplasi_wizard.py) in `tether.gui.deeplasi_wizard`, locked by
[`tests/test_deeplasi_wizard.py::test_movie_and_tdat_without_mat_cannot_reconstruct`](https://github.com/bioedca/tether/blob/main/tests/test_deeplasi_wizard.py)).

If you have movies and `.tdat` files but no `.mat` export, plan on native re-extraction, not
reconstruction.

## What Tether does not do

These are deliberate limits of scope. They are not gaps for later work.

- **No ALEX or PIE, no stoichiometry, no three-color.** Tether is two-color and single-laser
  only. As a direct consequence, the direct-excitation correction factor δ is structurally inert
  (it is zero). To estimate δ, Tether needs the acceptor-under-acceptor-excitation channel, and
  only ALEX provides that channel.
- **No confocal or point-detector data.** This follows from the item above, and it is not a
  separate decision. The pipeline needs a camera frame that it can split into two spatial
  halves and detect spots in. A photon-arrival stream from a point detector has no such frame.
- **No compressed or tiled TIFFs.** The lazy reader memory-maps the file for O(1) frame access.
  A compressed TIFF cannot be mapped. Tether refuses it, and it does not load the whole file
  silently. Vendor containers are not read either. Convert them to uncompressed TIFF first.
- **Deep-LASI detection mode 4 is not ported, so a `.tdat` that names it cannot be
  decoded.** Tether **refuses** a `.tdat` saved with particle-detection mode 4
  (local-variance), with a `ValueError` from `tether.io.tdat`: "mode 4 'local-variance' is not
  ported". Tether does not re-detect it with a different method. The Deep-LASI mode 4 itself
  cannot run. It reads a variable that is never set. Its window offers only modes 1 to 3, so no
  detection was ever made with it
  ([ADR-0143](adr/0143-deep-lasi-detection-mode-5-is-ported-and-mode-4-stays-refused.md)).
  Tether implements modes 1, 2, 3 and 5: `wavelet`, `intensity`, `bandpass` and `zmw`
  (`tether.imaging.detect.ParticleDetectionMode`). The refusal is on *decoding* that file. So
  `tether extract --tdat` re-extraction and the correction factors of the `.tdat` are
  unavailable. A bundle that also has the `.mat` still reconstructs. The import wizard's
  [`_read_tdat_best_effort`](https://github.com/bioedca/tether/blob/main/src/tether/gui/deeplasi_executor.py)
  (`tether.gui.deeplasi_executor`) catches the failure and rebuilds from the `.mat` without
  corrections. The result is degraded to apparent-E and carries a warning that "the .tdat could
  not be decoded"
  ([`tests/test_deeplasi_executor.py::test_undecodable_tdat_degrades_to_mat_apparent_e`](https://github.com/bioedca/tether/blob/main/tests/test_deeplasi_executor.py)).
- **Older `.dat` and vbFRET `.mat` formats are out of scope.**
- **No data simulator.** Validation runs against real labeled traces and a published
  benchmark dataset, not against synthesized data.
- **No central server.** Tether is a desktop application. Each person runs it on their own
  machine against their own storage.

## Scale and performance

Every figure below is a named constant in `tether.project.perf`, decided in
[ADR-0032](adr/0032-nfr-perf-budget-verification.md). Read them as **engineering budgets
and derived envelopes, not measurements or a service guarantee**.
[`tests/test_project_perf.py`](https://github.com/bioedca/tether/blob/main/tests/test_project_perf.py)
runs a real extraction and holds the projections under these budgets. That test checks that
the figures stay true.

These budgets cannot see a regression that stays inside them. The `benchmarks` workflow
([ADR-0078](adr/0078-benchmarks-compare-a-pull-request-with-its-merge-base.md)) does that part.
On a pull request that changes `src/tether/`, it times each operation and records its peak
memory on one runner. It does this on the merge base and on the pull request. It fails when a
gated operation becomes more than 1.5 times slower or larger. The job summary shows the table.

| Quantity | Value | Constant |
|---|---|---|
| Reference movie | 512 × 512, 1700 frames | `REFERENCE_MOVIE_*` |
| Movies per condition | 100 | `REFERENCE_CONDITION_MOVIES` |
| Molecules per movie | 250 | `REFERENCE_MOLECULES_PER_MOVIE` |
| Unattended batch run time | 12 h ("overnight") | `OVERNIGHT_WINDOW_HOURS` |
| Per-trace UI latency budget | 100 ms | `PER_TRACE_LATENCY_BUDGET_S` |
| Trace bytes per molecule per frame | 24 B (six `float32` layers) | `TRACE_BYTES_PER_MOLECULE_FRAME` |
| Fixed bytes per molecule | 5 kB | `FIXED_STORAGE_BYTES_PER_MOLECULE` |
| Per-condition projection ceiling | 5 GiB | `MAX_CONDITION_BYTES` |

`perf.estimate_condition_bytes()` projects a full reference condition at about **1.07 GiB** of
`.tether` project file. A full reference condition is 100 movies × 250 molecules × 1700 frames.
The raw movies of that condition take roughly 90 GB at the reference geometry. One molecule
projects to about **46 kB**.

Both figures are deliberately *pessimistic*. The model costs the six trace layers
uncompressed, but the project file writes them gzip-chunked. So real projects are smaller than
the projection. The 5 GiB ceiling is a design self-check: the test suite holds the projection
beneath it. It is **not enforced at runtime**. Nothing stops or warns you if a real condition
exceeds it.

The budget of 100 ms for each trace is what keeps curation at a pace of 1–2 seconds for each
trace. It covers drawing the donor/acceptor and FRET curves of one molecule and stepping to the
next.

<a id="the-store-references-your-movies-it-does-not-copy-them"></a>

### The project file references your movies, it does not copy them

This is the most useful fact for disk planning. For each movie, a `.tether` project records the
path you gave it, a `sha256` and a metadata-only signature. The signature is the file size, the
mtime and an offline-availability flag (see `MOVIES_DTYPE` in `tether.io.schema`). The movie
pixels stay where they are.

Reopening a project does not read your movie files. So it can never force a cloud-storage
placeholder to download its content just to check whether something changed.

A **natively extracted** project file caches one thing from the pixels: a `window × window`
temporal-mean patch for each molecule and each channel (`/patches/{donor,acceptor}`). So
curation and the static overlap view still work with the movies offline. At the default
21-pixel aperture window, that is about 3.5 kB for each molecule. That is already inside the
fixed figure of 5 kB for each molecule above.

A project reconstructed from a Deep-LASI export is the exception. That path never reads the
movie pixels, so it writes its patches zero-filled. Curation, the overlap distance and the
overlap flag still work. But the patch image itself renders blank, and Tether does not report a
missing patch.

In practice, the traces, the patches and all analysis survive when you move or rename your
movies. What you lose is the trip back to the pixels: re-extraction, and inspecting a molecule
against its frames.

## Disk footprint of the application

The installer lays down two complete, isolated environments under the install root.
`envs/tether` is for the application. `envs/sidecar` is for the tMAVEN idealization backend,
which needs an older, incompatible numeric stack of its own. Two full environments and a bundled
Python make the install considerably larger than a desktop utility with one environment.

**Measured on Linux only: 3.6 GB.** The project installed the `v1.0.0-rc3` Linux installer
(`Tether-1.0.0-rc3-Linux-x86_64.sh`) into a clean folder. The install folder was then 3.6 GB
(`du -sh`, 12 November 2026). The `v1.0.0-rc2` installer measured the same. That is the figure
that [Install Tether](install.md#what-gets-installed-and-where) reports for the same release.

**Windows and macOS have not been measured**, so this page quotes no figure for them. A guess
about disk usage is worse than no number, and a Linux figure does not stand in for theirs.

Installer **download** sizes are a different quantity. An installer is compressed, and it
unpacks to more than its own size. The [release page](https://github.com/bioedca/tether/releases)
gives the download sizes for every published build.

## Still not sure?

If your setup is close to the geometry above but not exactly the same, run `tether extract` on
a single movie. Then look at what comes out. This is the fastest check. `tether extract --help`
lists the options. Extraction either produces a project with a sensible molecule count, or it
fails with a message that names what it could not read.
