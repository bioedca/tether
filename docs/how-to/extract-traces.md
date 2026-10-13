# Extract traces from a dual-view movie

## Who this page is for

You have a dual-view TIRF movie on disk, saved as a TIFF with the donor and acceptor channels
side by side in each frame, and you want a `.tether` project that holds one donor trace and
one acceptor trace for every molecule in it. You may also have a Deep-LASI `.tmap`
(a channel map) or `.tdat` (a detection setup) from the same microscope; this page says when to
use them.

If your traces were already extracted in Deep-LASI, you do not need this page: bring them in
with [Legacy Deep-LASI import](../io/legacy-import.md) instead.

## Before you start

- Tether is installed and `tether --version` answers, as in
  [step 5 of the install guide](../install.md#5-check-that-tether-runs).
- Your movie fits what Tether reads: an uncompressed multi-page TIFF, one camera, split down
  the vertical midline into a left and a right half. [Does Tether fit my
  data?](../compatibility.md) has the details.
- You know which half of the frame is the donor channel. Ask whoever set up the emission
  path, or check an earlier analysis of the same setup.
- Optional: a `.tmap` from a calibration of the same microscope, or a `.tdat` from a
  Deep-LASI analysis whose detection you want to match.

The examples below use a movie called `movie.tif` and run in the folder that holds it.

## 1. Name the donor half

Tether assumes the donor is the **left** half. If yours is on the right, you will add
`--donor-side right` to the command in step 4
([`donor_side`](../reference/parameters.md#param-donor-side)).

Getting this backwards raises no error. It swaps the channels and inverts every FRET
efficiency, so step 5 shows you how to check it.

## 2. Choose how molecules are found

Pick one of these:

- **Most movies:** add nothing. Tether uses its `wavelet` detector, which copes with an
  uneven background.
- **To match a Deep-LASI analysis of the same movies:** add `--tdat run.tdat`. Tether reads
  the detection method and threshold from that file. You cannot combine `--tdat` with the
  two options in the next item.
- **To set the detector yourself:** add `--detection-mode intensity` or
  `--detection-mode bandpass`, optionally with `--detection-threshold`, given as a fraction
  (at least 0, below 1) of the brightest pixel in the image the detector searches
  ([`detection_mode`](../reference/parameters.md#param-detection-mode),
  [`detection_threshold`](../reference/parameters.md#param-detection-threshold)). A lower
  threshold finds more, dimmer molecules.

If two molecules close together come out as one, lower `--min-separation`
([`min_separation`](../reference/parameters.md#param-min-separation)).

## 3. Choose how the two halves are lined up

- **Native registration (the default):** add nothing. Tether matches spots between the two
  halves of your movie and fits the map from them.
- **An imported map:** add `--tmap beads.tmap` to use a Deep-LASI channel map instead,
  for example one made from a bead slide. The map carries its own channel layout, so
  `--donor-side` is ignored when you pass one. Tether reads only the crop of each channel
  from the map, so a map that also rotates or flips a channel stops the extraction with an
  error; use native registration for that movie instead.

## 4. Run the extraction

With the defaults:

```bash
tether extract movie.tif -o movie.tether
```

With the donor on the right and detection matched to a Deep-LASI `.tdat`:

```bash
tether extract movie.tif -o movie.tether --donor-side right --tdat run.tdat
```

With an imported channel map:

```bash
tether extract movie.tif -o movie.tether --tmap beads.tmap
```

Tether refuses to replace a project that already exists. To extract again into the same
file, add `--overwrite`.

## 5. Check that it worked

A successful run prints one line naming the molecule count and the project:

```text
Extracted 412 molecule(s) -> movie.tether
```

With `--tmap` it adds a line starting `registration: imported from`, and with `--tdat` a line
starting `detection: mode`, naming what was applied. Then check three things:

1. No line starting `warning: registration RMS` appeared. If one did, the extraction still
   finished, but every molecule is tagged as having a doubtful channel map; see
   [Registration was flagged low-confidence](../troubleshooting.md#registration-was-flagged-low-confidence).
2. The molecule count is roughly what you see in the movie. If it is far off, see
   [Extraction found far fewer, or far more, molecules than
   expected](../troubleshooting.md#extraction-found-far-fewer-or-far-more-molecules-than-expected).
   If the command stopped with `registration failed` instead, see
   [Extraction found no molecules](../troubleshooting.md#extraction-found-no-molecules).
3. The donor half is right. Open the project with `tether-gui movie.tether` and find a
   molecule whose acceptor bleaches partway through. When the acceptor goes dark, the
   **donor** trace (green) should step **up**. If it steps down instead, extract again with
   the other `--donor-side`. With `--tmap`, `--donor-side` is ignored, so extract again
   with a correctly oriented map or without `--tmap`.

## Many movies at once

`tether batch` extracts a whole folder of movies into one project each, then corrects and
idealizes them:

```bash
tether batch data/*.tif -d projects
```

It has no `--donor-side`, `--detection-mode` or `--detection-threshold` option, so every
movie is extracted with the donor on the left and the default detector. A shared `--tdat`
replaces only the detector settings, never the donor side, and a shared `--tmap` sets the
channel layout from the map. So use `tether batch` when your donor is on the left, or when
you pass a `--tmap` from the same microscope; otherwise run `tether extract --donor-side
right` on each movie. [Command
line](../cli.md#tether-batch) lists its options.

## Next

[Curate the traces](curate-traces.md) to set aside the molecules you do not want in your
analysis.
