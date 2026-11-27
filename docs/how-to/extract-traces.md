---
description: "Make a .tether project of donor and acceptor traces from a dual-view TIRF movie, with or without a Deep-LASI channel map."
---

# Extract traces from a dual-view movie

## Who this page is for

You have a dual-view TIRF movie on disk, saved as a TIFF. Each frame holds the donor and
acceptor channels side by side. You want a `.tether` project that holds one donor trace and
one acceptor trace for every molecule in the movie.

You may also have a Deep-LASI `.tmap` (a channel map) or `.tdat` (a detection setup) from the
same microscope. This page says when to use them.

If you have not run Tether before, do [Your first analysis](../tutorial.md) first. It takes a
small sample movie through every stage once. This page and the five after it pick up from
there with your own data.

If your traces were already extracted in Deep-LASI, you do not need this page. Bring them in
with [Legacy Deep-LASI import](../io/legacy-import.md) instead.

## Before you start

- Tether is installed and `tether --version` answers, as in
  [step 5 of the install guide](../install.md#5-check-that-tether-runs).
- Your movie fits what Tether reads. That is an uncompressed multi-page TIFF from one camera.
  The vertical midline splits each frame into a left and a right half.
  [Does Tether fit my data?](../compatibility.md) has the details.
- You know which half of the frame is the donor channel. Ask whoever set up the emission
  path, or check an earlier analysis of the same setup.
- Optional: a `.tmap` from a calibration of the same microscope.
- Optional: a `.tdat` from a Deep-LASI analysis whose detection you want to match.

The examples below use a movie called `movie.tif` and run in the folder that holds it.

## 1. Name the donor half

Tether assumes the donor is the **left** half. If yours is on the right, you will add
`--donor-side right` to the command in step 4
([`donor_side`](../reference/parameters.md#param-donor-side)).

Getting this backwards raises no error. It swaps the channels and inverts every FRET
efficiency. When the movie has enough bleach events, Tether checks the side after the
extraction and warns you. Step 5 shows what to look for. It also shows how to check a trace
yourself when the check cannot decide.

## 2. Choose how molecules are found

Pick one of these:

- **Most movies:** add nothing. Tether uses its `wavelet` detector, which copes with an
  uneven background.
- **To match a Deep-LASI analysis of the same movies:** add `--tdat run.tdat`. Tether reads
  the detection method and threshold from that file. You cannot combine `--tdat` with the
  two options in the next item.
- **To set the detector yourself:** add `--detection-mode intensity`,
  `--detection-mode bandpass` or `--detection-mode zmw`
  ([`detection_mode`](../reference/parameters.md#param-detection-mode)). You can also add
  `--detection-threshold`
  ([`detection_threshold`](../reference/parameters.md#param-detection-threshold)). Give it as
  a fraction of the brightest pixel in the image that the detector searches. The fraction is
  at least 0 and below 1. A lower threshold finds more, dimmer molecules.

If two molecules close together come out as one, lower `--min-separation`
([`min_separation`](../reference/parameters.md#param-min-separation)).

Then choose which donor spots become molecules
([`colocalization`](../reference/parameters.md#param-colocalization)):

- **Most movies, and to match Deep-LASI:** add nothing. Tether keeps a donor spot only
  when it also finds an acceptor spot there (`partner-in-both`).
- **Low-FRET molecules, or a dark acceptor:** add `--colocalization donor-anchored`.
  Tether then keeps every donor spot. It reads the acceptor at the mapped place, found or
  not. The default drops these molecules. A FRET histogram without them leans toward high
  FRET.

!!! note "The default changed"
    Before this version every extraction was `donor-anchored`. A new extraction now keeps
    only molecules with an acceptor spot. It can find fewer molecules than before. A
    project made before the change opens as it was, and reads as `donor-anchored`.

To see the cost before you run, count the molecules under both rules. This writes no
project:

```bash
tether extract movie.tif -o movie.tether --count-only
```

```text
Molecules in movie.tif under each colocalization rule (nothing written):
  partner-in-both: 412 (selected)
  donor-anchored: 455
```

## 3. Choose how the two halves are lined up

- **Native registration (the default):** add nothing. Tether matches spots between the two
  halves of your movie and fits the map from them.
- **An imported map:** add `--tmap beads.tmap` to use a Deep-LASI channel map instead,
  for example one made from a bead slide. The map carries its own channel layout, so
  `--donor-side` is ignored when you pass one. Tether splits each channel with the map's
  rotation, flip and crop, in that order. A movie whose frames cannot hold that geometry stops
  the extraction with an error. An example is a movie turned by 90 degrees against the map.
  The error names the frame size of the movie and the geometry of the map.
- **A saved Tether map:** make a map once from a bead slide with
  `tether map create beads.tif -o beads.npz`. In the application, use **Calibration → Make
  channel map…**. Then add `--map beads.npz` to extract each later movie with it. The map
  keeps its own channel layout. So `--donor-side` is ignored here too.

Pass one map at most. Tether refuses `--tmap` and `--map` together. The
[command reference](../cli.md#registration-one-map-source) gives the full rule.

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
file, add `--overwrite`. Tether then takes the project's writer lock before it replaces the
file.

Close the project in any other Tether application window or process first. If another one has
the project open, the command stops with `is locked` and does not change the file. There is one
exception. Another user may have opened the same file through a different hard-link name. See
[Troubleshooting](../troubleshooting.md) for that case.

## 5. Check that it worked

A successful run prints three lines. The first names the molecule count and the project. The
second names the colocalization rule and the count that the other rule would keep. The third
gives the result of the donor-side check:

```text
Extracted 412 molecule(s) -> movie.tether
  colocalization: partner-in-both kept 412 (donor-anchored would keep 455)
  donor side: 38 of 40 bleach events agree with the donor side as extracted.
```

With `--tmap`, it adds a line starting `registration: imported from`. With `--tdat`, it adds
a line starting `detection: mode`. Each line names what was applied. Then check three things:

1. No line starting `warning: registration RMS` appeared. If one did, the extraction still
   finished. But every molecule is tagged as having a doubtful channel map. See
   [Registration was flagged low-confidence](../troubleshooting.md#registration-was-flagged-low-confidence).
2. The molecule count is roughly what you see in the movie. If it is far off, see
   [Extraction found far fewer, or far more, molecules than
   expected](../troubleshooting.md#extraction-found-far-fewer-or-far-more-molecules-than-expected).
   If the command stopped with `registration failed` instead, see
   [Extraction found no molecules](../troubleshooting.md#extraction-found-no-molecules).
3. The donor half is right. Tether checks this from the molecules whose acceptor bleaches
    before their donor ([how it works](../cli.md#get-donor-side-right-and-read-the-donor-side-check)):

    - `donor side: 38 of 40 bleach events agree with the donor side as extracted.` The
      side is right. Go on.
    - `warning: 38 of 40 bleach events are consistent with swapped donor and acceptor
      halves; extract this movie again with --donor-side right.` The halves are swapped.
      Extract again as the message says, with `--overwrite`, which replaces
      `movie.tether`. With `--tmap`, the message names the channel order of the map instead,
      because `--donor-side` is ignored. Extract again with a correctly oriented map. Or
      extract without `--tmap`, and give the `--donor-side` of the true donor. With a
      channel layout from Python
      ([other layouts](../compatibility.md#acquisition-geometry)), the message names a
      change to the layout. Give the other half as the donor, or exchange `donor_rect` and
      `acceptor_rect`.
    - `donor side: not enough bleach events to check the donor side (…)`, or a result that
      is *not a clear majority either way*. The check cannot decide, which is usual with
      low FRET, with no acceptor, or with few molecules. Check a trace yourself.

    **To check a trace yourself**, open the project with `tether-gui movie.tether`. Find a
    molecule whose acceptor bleaches partway through. When the acceptor goes dark, the
    **donor** trace (green) should step **up**. If it steps down instead, close the project
    in `tether-gui`. Then extract again with the other `--donor-side` and `--overwrite`.

    If you extracted with `--tmap`, extract again with a correctly oriented map instead. Or
    extract without `--tmap`, and give the `--donor-side` of the true donor.

## Extract in the application

The **Prepare** section of the application window does the same extraction, with a preview of
each stage first. Open it with **View → Section → Prepare**, or press Ctrl+1.

1. Choose the movie with **Choose…** beside **Movie**. The dialog lists every movie container
   that extraction reads, and **Preview** reads the movie the same way as **Extract…**. A
   movie that extraction refuses gives the same message on both, under the form.
2. Choose the registration: a native fit, a saved channel map file (`.npz`), or a Deep-LASI
   `.tmap`. A map file or a `.tmap` needs its file in **Map file**. **Preview** and
   **Extract…** run the same [reuse check](../cli.md#reuse-a-map-file) on a map file. A map
   made at another pixel size is refused on both, with the same message under the form. The
   page has no optics field, so the check does not compare the optics. Use
   `tether extract --optics` for that.
3. Set the options. There is one row for each field of an
   [extraction profile](../cli.md#extraction-profiles), with the same name and the same default. A value that
   Tether refuses shows its reason under the form, and nothing starts.
4. Click **Preview**. The stage table gives the outcome of each stage: `ok`, `over-gate`,
   `skipped` or `failed`. A failed stage gives its reason, and the stages after it are
   `not run`. Nothing is written.
5. Look at the movie viewer. **Detected spots** shows the spots of each half (donor green,
   acceptor red). **Control-point residuals** shows the control points in the acceptor half.
   Green is where the fit puts each donor control point. Red is the acceptor spot of its pair.
   The distance between the two is the residual of that pair. A map file or a `.tmap` has no
   fit, so it has no residual view.
6. Click **Extract…** and give a name for the new project. Tether refuses a name that already
   exists. The page then shows the molecule count and the result of the donor-side check. It
   is a warning with the evidence and the remedy when the halves look swapped, and a note
   otherwise. The check never changes the data. Click **Open the new project** to open it.

### Check the apertures before the run

The **Apertures and background** tab of the same page shows the apertures of the movie and the
options on the left before you extract. Nothing is written to a project.

1. Click **Review apertures**. The review runs in the background, and the window stays usable.
2. Look at the frame, the mean of all frames of the movie. Each spot has its signal aperture
   (a solid circle of `disk_radius`) and its background ring (two dotted circles,
   `ring_inner` and `ring_outer`), at the donor spot and at the acceptor position the
   registration maps it to.
3. Read the marks and the counts above the frame. **Orange** is a spot at an edge: its
   `window`-pixel crop leaves the donor or the acceptor channel, so the run skips it.
   **Magenta** is a molecule whose aperture overlaps the aperture of its nearest neighbour:
   their centres are closer than twice `disk_radius`, the rule of the curation overlap view.
   **Grey** is a spot with no acceptor partner, which the `partner-in-both` rule skips. These
   are the rules the run applies; the tab adds none.
4. Select molecules in the list to see their preview traces, donor and acceptor. A preview
   trace is the trace the run gives that molecule.
5. Change an option, for example `disk_radius`, and press Enter. The tab reviews the movie
   again.
6. Click **Save report…** to write the report: a JSON file with the options, the counts, every
   spot and the traces of the selected molecules, and a PNG of the frame with the apertures.

The command line writes the same report with the same functions, from the movie, an extraction
profile, the registration source and the molecules:

```sh
tether apertures movie.tif --json apertures.json --png apertures.png \
    --profile profile.json --molecule 0 --molecule 3
```

`--disk-radius`, `--ring-inner`, `--ring-outer` and `--window` change one option of the
profile. The command refuses to replace an existing report without `--overwrite`.

## Add another movie to a project

A project can hold several movies of one experiment
([`.tether` project](../glossary.md#tether-project)). `tether extract` writes a project of one
movie. To add a second movie, give it to `tether batch --into`. That also corrects and
idealizes the whole project again ([Many movies at once](#many-movies-at-once)):

```bash
tether batch movie_2.tif --into movie.tether
```

To add the movie without correcting and idealizing again, run a short Python script instead.

The project's correction and idealization stay as they were after an append. They no longer
cover every movie, so the next `tether batch --into` run over the project does them again.

Close the project in the desktop application first. The script needs the project's writer lock.

Save this as `add_movie.py` beside the project, with your project and movie names:

```python
from tether.project.extract import add_movie_to_project
from tether.project.lock import publish_lock

with publish_lock("movie.tether") as guard:
    summary = add_movie_to_project("movie.tether", "movie_2.tif", publish_guard=guard)
print(f"Added {summary.n_molecules} molecule(s) as {summary.movie_id}")
```

Run it with the Python that came with Tether, as in [Analyze](analyze.md#before-you-start). It
prints one line:

```text
Added 388 molecule(s) as mov-6f1d0c2e-93a4-4b7e-8d55-2c0e9b7a41f3
```

Then check the project:

```bash
tether info movie.tether
```

It lists each movie and its molecules:

```text
  movies: 2
    movie.tif: 1800 frames, 0.1 s/frame, 412 molecules
      curation: 0 accepted, 0 rejected, 412 undecided
    movie_2.tif: 1800 frames, 0.1 s/frame, 388 molecules
      curation: 0 accepted, 0 rejected, 388 undecided
```

The printed `movie_id` identifies the movie inside the project. Each molecule carries the
`movie_id` of its movie, and so does each row of the
[molecule table](export-results.md#export-the-molecule-table). Two movies can share a file name,
so pick a movie by its `movie_id`.

The script extracts the movie with the same steps as `tether extract`, with the donor on the
left and native registration. To change that, pass these arguments to `add_movie_to_project`:

- `options=ExtractOptions(donor_side="right")`, with `ExtractOptions` imported from
  `tether.project.extract`, for a donor on the right.
- `tmap="beads.tmap"` for an imported channel map.
- `tdat="run.tdat"` to match a Deep-LASI detection.

Each molecule of the new movie gets the provisional condition parsed from the filename of that
movie. So a project of two movies can hold two [conditions](../glossary.md#condition).

The script stops with an error, and leaves the project unchanged, in these cases:

- The movie is already in the project. The error ends `a movie is appended once`.
- The new movie's window, radii or background window differ from those of the first movie.
- The project has no raw traces, for example an analysis-only import or a subset exported
  without them.
- Another Tether application window or process has the project open. The error says
  `is locked`.

## Many movies at once

`tether batch` extracts a whole folder of movies, either into one project each or all into
one project. Then it corrects and idealizes them. Give it the folder:

```bash
tether batch data -d projects
```

The command is the same in PowerShell, cmd and bash. A condition can be in several day
folders. Then give the parent folder with `--recursive`. You can also give a pattern in
quotes. Tether expands the pattern itself in every shell:

```bash
tether batch data --recursive -d projects
tether batch "data/2026-11-*/*.tif" -d projects
```

Check what the run will do first with `--dry-run`. It prints each movie and its output
project. It tells you if a project is new, finished or to be resumed. It writes nothing.

Two movies can have the same file name in different day folders. Each one then gets its
own project, with the folder name in front, such as `day1__movie_010.tether`. [Command
line](../cli.md#output-names-when-two-movies-have-the-same-file-name) states the rule.

To put every movie of the experiment into one project, give `--into` in place of `-d`:

```bash
tether batch data --recursive --into experiment.tether
```

The first movie creates the project if it does not exist, and each other movie is added to
it. A movie that is already in the project is skipped. So you can run the same command again
after the movies of a new day land in the folder. Only the new movies are added. Then the
project is corrected and idealized again, over every movie in it.

`--dry-run` shows which
movies are new to the project. [Command line](../cli.md#every-movie-into-one-project-into)
gives the details.

To add one movie from Python, with extraction only, see
[Add another movie to a project](#add-another-movie-to-a-project).

`tether batch` has no `--donor-side`, `--detection-mode` or `--detection-threshold` option.
So it extracts every movie with the donor on the left and the default detector. A shared
`--tdat` replaces only the detector settings, never the donor side. A shared `--tmap` sets the
channel layout from the map.

So use `tether batch` when your donor is on the left. Also use it when you pass a `--tmap` from
the same microscope. Otherwise, run `tether extract --donor-side right` on each movie.

The batch summary marks a movie whose halves look swapped `halves-swapped?`. It lists that
movie at the end. So a right-side movie that slipped into a batch is named there, if it has
enough bleach events. [Command line](../cli.md#tether-batch) lists the options of
`tether batch`.

`tether batch` corrects and idealizes every molecule before you have curated any. If you then
reject molecules, the stored factors and model still include them. To leave them out,
[export the accepted molecules](apply-corrections.md#leave-out-the-molecules-you-rejected),
correct that project, and
[fit it again](idealize.md#after-you-change-the-project).

## Next

[Curate the traces](curate-traces.md) to set aside the molecules you do not want in your
analysis.
