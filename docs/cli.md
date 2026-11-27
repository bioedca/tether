---
description: "Run tether extract and tether batch without the application window: script the pipeline, run movies unattended or repeat an extraction."
---

# Command line

**Who this page is for.** You run Tether without the application window. You script the
pipeline, process many movies unattended, extract a movie again from recorded settings or export
the results of many projects. The page assumes that you can type a command in a terminal. It does
not assume that you know Python.

The command line has these subcommands:

| Command | What it does |
|---|---|
| [`tether extract`](#tether-extract) | Makes a `.tether` project from one movie. |
| [`tether reextract`](#tether-reextract) | Extracts a movie of a project again, as a new trace generation. |
| [`tether batch`](#tether-batch) | Makes projects from many movies, and corrects and idealizes them. |
| [`tether map create`](#tether-map-create) | Writes a channel map file for later extractions. |
| [`tether run`](#tether-run) | Runs the steps of a recipe file. |
| [`tether approve`](#tether-approve) | Records that a person approved a step. |
| [`tether save-composition`](#tether-save-composition) | Saves a plot composition of the plot builder to a file. |
| [`tether open-composition`](#tether-open-composition) | Reads a saved plot composition and names each series that cannot be found. |
| [`tether info`](#tether-info) | Reads projects and prints a summary. It changes nothing. |
| [`tether status`](#tether-status) | Reads a project and prints which steps are complete, which are stale and what to do next. |
| [`tether idealize`](#tether-idealize) | Fits the traces of a project that exists. |
| [`tether metadata`](#tether-metadata) | Shows or gives the frame time, the pixel size and the channel roles of the movies of a project. |
| [`tether export`](#tether-export) | Writes the molecule table, the trace text file or a subset project. |

**Start a new movie here.** The desktop application does not extract a movie yet. It opens a
`.tether` project and imports a Deep-LASI acquisition. In it, you curate traces by hand, select
states by looking at the traces and read plots.

Only `tether extract` and `tether batch` make a `.tether` project from a raw movie. So, for a new
acquisition, run one of them first. Then open
its output in the application. The next section tells you how to start the application. The
extraction section of the application is planned work: see
[its milestone on GitHub](https://github.com/bioedca/tether/milestone/17).

## How to read this page

Each command has one entry, with these parts in this order:

1. **What the command does**, in one or two sentences.
2. **Synopsis**: the usage line that `--help` prints for the command. Square brackets mark an
   optional argument. Braces, as in `{left,right}`, give the allowed values. A word in capitals,
   such as `PATH` or `N`, is a value that you give.
3. **Warnings**, before the table, when a value can change a result and give no error.
4. **Options table**, with three columns:
    - **Argument**: the option name and its value, as the synopsis gives them.
    - **Default**: the value that the command uses when you do not give the option. A number is
      the string that `--help` prints.
    - **Meaning**: what the option does, in at most 25 words, with the unit of a number. A longer
      note is in the numbered notes below the table.
5. **Exit codes**: what each exit code of the command means.
6. **Example**: one command line and its output.

Distances are in pixels, times are in seconds and frame counts are in frames. A JSON layout
table gives each key of the document and its meaning. The [Background](#background) section at
the end gives the reasons.

The tables come from the argparse parser in `src/tether/cli.py`. `--help` prints from the same
parser. A guard test,
[`tests/test_cli_reference.py`](https://github.com/bioedca/tether/blob/main/tests/test_cli_reference.py),
fails when that parser gains, loses or renames an option and this page does not change with it.

This page gives only the options that the command line has. Most of the values that Tether
applies to your data have no flag. These are the correction, idealization, analysis and
rendering defaults. You set them from Python or in the desktop application.
[Analysis parameters](reference/parameters.md) gives each one. It also tells whether the
`.tether` project records the value that you used.

## Launching Tether after an installer install

The installer does **not** edit your `PATH`
([ADR-0051](adr/0051-installed-app-launch-surface.md)). So, after an install, you cannot type
either command by its name alone. The installer writes both launchers into one directory:
`bin/` under the install root that you chose during installation.

| Platform | Command-line launcher | Application-window launcher |
|---|---|---|
| Windows | `<install-root>\bin\tether.bat`, or `tether` in Start Menu → **Tether Command Prompt** from the next release | Start Menu → **Tether**, or `<install-root>\bin\tether-gui.bat` in a terminal |
| Linux | `<install-root>/bin/tether` | Applications menu → **Tether**, or `<install-root>/bin/tether-gui` |
| macOS | `<install-root>/bin/tether` | `<install-root>/bin/tether-gui` |

From the next release, the two launchers are in `launchers/` (`launchers\` on Windows) under
the install root. That folder holds nothing else. Read `launchers` for `bin` in the table.

On Windows, from the next release, Start Menu → **Tether Command Prompt** opens a console
that needs no `PATH` change. In it, you
can type `tether` by name. An example written for one shell, such as the Bash or PowerShell
batch example, still needs that shell.

On any system, add that one directory to the **end**
of your `PATH`, and `tether` works by name in any terminal.
[Install Tether, step 4](install.md#optional-put-the-launcher-folder-on-your-path)
gives the line for each system. It also tells why the folder goes at the end. In rc3, on Linux
and macOS, the `bin` folder also holds the installer's own `python`, `pip` and `conda`. These
must not hide your own.

Without either, write the full launcher path from the table in place of `tether` in each
command below. Nothing else changes. To check the install, run this command:

```console
$ tether --version
tether <version>
```

`--version` is the one flag that never reads or writes your data, so it is the right first
check. The build makes the version string from git. So an installer or CI build, made from the
git checkout, prints a version that identifies the build that you installed. Two other outputs
do not mean that the install is broken:

- A source tree that was never built prints `tether 0.0.0+unknown`. No build made the version
  file.
- A build from a tree with no git metadata, such as a downloaded source zip, prints the fixed
  `tether 0.0.0`.

Running `tether` with no subcommand prints the top-level help and exits 0.

## `tether extract`

Run the native extraction pipeline on one dual-channel movie, a TIFF, CZI, ND2 or cellSens
`.vsi` file, and write a new `.tether` project. The stages are split, detect, register, colocalize
and integrate.

```text
tether extract [-h] -o OUTPUT [--overwrite] [--donor-side {left,right}]
               [--detection-mode {wavelet,intensity,bandpass,zmw}]
               [--detection-threshold FRAC] [--window WINDOW]
               [--min-separation MIN_SEPARATION]
               [--detection-block DETECTION_BLOCK]
               [--prealign {translation,similarity}] [--pair-tol PAIR_TOL]
               [--coloc-distance COLOC_DISTANCE]
               [--colocalization {partner-in-both,donor-anchored}]
               [--count-only] [--rms-gate RMS_GATE] [--tmap PATH]
               [--map PATH] [--optics TEXT] [--confirm-optics]
               [--tdat PATH] [--profile PATH]
               movie
```

> **Check `--donor-side` before you extract.** A wrong value inverts every FRET efficiency in
> the project, and it raises no error. Read
> [Get `--donor-side` right](#get-donor-side-right-and-read-the-donor-side-check).

| Argument | Default | Meaning |
|---|---|---|
| `movie` | *(required)* | Path to the dual-channel movie, a TIFF, CZI, ND2 or cellSens `.vsi` file. |
| `-o OUTPUT`, `--output OUTPUT` | *(required)* | Path to the `.tether` project to create. |
| `--overwrite` | off | Replace an existing output project. Tether refuses a project that another writer has open (note 1). |
| `--donor-side {left,right}` | `left` | Which horizontal half of the frame is the donor channel. |
| `--detection-mode {wavelet,intensity,bandpass,zmw}` | `wavelet` | The particle-detection method, which is the Deep-LASI `findPart` mode. You cannot give it with `--tdat` (note 2). |
| `--detection-threshold FRAC` | each mode's own — intensity `0.5`, bandpass `0.98`, zmw `0.5` | The detection threshold, as a fraction of the detection-image maximum, in `[0, 1)`. You cannot give it with `--tdat` (note 2). |
| `--window WINDOW` | `21` | The side length of the aperture window (the crop box), in pixels. It must be odd. |
| `--min-separation MIN_SEPARATION` | unset uses each detection mode's faithful default (wavelet 8, intensity/bandpass/zmw 3) | The minimum distance between two spots, in pixels. |
| `--detection-block DETECTION_BLOCK` | `50` | The block size of the moving average that makes the detection image, in frames. |
| `--prealign {translation,similarity}` | `translation` | The degrees of freedom of the registration prealignment. |
| `--pair-tol PAIR_TOL` | `2` | The pairing tolerance of the control points, in pixels. |
| `--coloc-distance COLOC_DISTANCE` | `3` | The acceptor colocalization distance, in pixels. |
| `--colocalization {partner-in-both,donor-anchored}` | `partner-in-both` | Which molecules to keep. `partner-in-both` is the Deep-LASI rule, and `donor-anchored` keeps every donor spot (note 3). |
| `--count-only` | off | Run detection, registration and colocalization, print the molecule count of each colocalization rule, and write nothing. |
| `--rms-gate RMS_GATE` | `0.5` | The gate on the RMS residual of the registration, in pixels. |
| `--tmap PATH` | none | Apply an imported Deep-LASI `.tmap` in place of a native fit. You cannot give it with `--map` (note 4). |
| `--map PATH` | none | Apply a saved Tether `.npz` map file from [`tether map create`](#tether-map-create) in place of a native fit. You cannot give it with `--tmap` (note 4). |
| `--optics TEXT` | none | With `--map`: the optics text of this movie, such as the objective, the filters and the camera (note 7). |
| `--confirm-optics` | off | With `--map`: use the map although its optics text differs from `--optics`. The project records the confirmation. |
| `--tdat PATH` | none | Apply the particle-detection mode decoded from a Deep-LASI `.tdat` (`temp/ParticleDetectionMode`). It sets only the detection mode and the detection threshold (note 5). |
| `--profile PATH` | none | Read every extraction option from an [extraction profile](#extraction-profiles). An option that the file leaves out keeps its default (note 6). |

**Notes**

1. Tether holds the writer lock of the project for the run. The lock does not see another user
   who opened the project through a different hard-link name.
2. The `intensity`, `bandpass` and `zmw` modes use `--detection-threshold`. The `wavelet` mode
   ignores it. A `.tdat` supplies the mode, so Tether refuses either flag beside `--tdat`.
3. `partner-in-both` keeps a donor spot only when an acceptor spot was detected at its mapped
   position. `donor-anchored` keeps every donor spot, for weak-acceptor and low-FRET samples. See
   [Choose the colocalization rule](#choose-the-colocalization-rule).
4. A `.tmap` or a map file splits each frame at its own channel geometry, and Tether then ignores
   `--donor-side`. See [Registration: one map source](#registration-one-map-source).
5. Use `--tdat` so that the extraction uses the detection method of the movie. It sets
   `detection_mode` and `detection_threshold`. Tether refuses it beside `--detection-mode`,
   `--detection-threshold` or a `--profile` that sets either. See
   [the rule](#detection-settings-pick-one-source).
6. You cannot give `--profile` with an option flag, from `--donor-side` to `--rms-gate`.
7. Tether refuses a map whose own optics text differs, unless you give `--confirm-optics`. See
   [Reuse a map file](#reuse-a-map-file).

### Get `--donor-side` right, and read the donor-side check

> **Give the `--donor-side` value of the half that carries donor emission.** This is the one
> flag that can invert your results with no message. `left` and `right` name the horizontal
> halves of the dual-channel frame. A wrong value swaps the two halves. Every FRET efficiency in
> the project is then inverted, and it raises no error, because both values are legal.

A value that is neither, such as `--donor-side sideways`, is a usage error. argparse prints the
usage line and `invalid choice: 'sideways' (choose from left, right)`. It then exits with code 2
before it opens a file. A valid but wrong value is not an error, so the extraction cannot refuse
it.

**The donor-side check.** After each extraction, Tether reads the donor side back from the
traces ([ADR-0070](adr/0070-advisory-check-for-swapped-donor-and-acceptor-halves.md)). When the
acceptor bleaches first, the acceptor drops to background and the donor *rises*. When the donor
bleaches first, both drop. A bleach event is a molecule whose dyes bleach one after the other.
It has a clear drop in one channel and a clear rise in the other at the first bleach.

The check counts at least 15 bleach events. If the channel labeled donor shows the acceptor's
drop in at least 80 % of them, the command writes a warning on standard error. The warning gives
the evidence and the remedy:

```text
Extracted 27 molecule(s) -> wrong.tether
  warning: 27 of 27 bleach events are consistent with swapped donor and acceptor halves; extract this movie again with --donor-side left.
```

Otherwise the command prints one `donor side:` line on standard output, for example
`donor side: 32 of 32 bleach events agree with the donor side as extracted.` The check is
**advisory**. It never changes the project or the exit code. Tether stores its result and its
thresholds as `donor_side_check` in `/settings/extraction`. `tether batch` lists the flagged
movies in its summary.

**The check cannot always decide.** With fewer than 15 bleach events, it prints
`not enough bleach events to check the donor side`. Expect that with low FRET (below about
E = 0.2), with no acceptor, with short movies and with most movies of fewer than about 35
molecules. A verdict of "not a clear majority either way" means that the votes split. In both
cases, and whenever the result is important, look at the traces yourself.

**Check the traces yourself (the fallback).** Find a molecule whose acceptor photobleaches in the
middle of the trace:

1. Find the frame where the acceptor bleaches. After it, the donor no longer transfers energy,
   so the *donor* trace steps **up** at that frame.
2. If the trace that you labeled "donor" steps *down* while the other steps up, your halves are
   swapped. Extract the movie again with the other value.

**Audit the value afterward.** Tether writes each `ExtractOptions` field, unchanged, into
`/settings/extraction` in the project file. For a **natively registered** project, the
`donor_side` there is the value that the extraction applied. You can read it from any `.tether`
project without running the extraction again.

**A `--tmap` run.** An imported Deep-LASI map carries its own channel geometry, so Tether ignores
`--donor-side` when you give `--tmap`. Tether still records the value. So on a `--tmap` run,
`/settings/extraction` holds the value that you *requested*, not the geometry that the run used.
Audit the imported map itself in that case. Tether records its file name beside it, as
`tmap_source`.

The donor-side check still runs on a `--tmap` run. Its warning then points at the
channel order of the map, not at `--donor-side`.

**Other values that argparse refuses.** Tether refuses an unrecognized `--detection-mode` or
`--prealign` value in the same way. You get the usage line, an `invalid choice` message that
names the allowed values, and exit code 2. The three flags read their allowed values from the
constants that `ExtractOptions` checks (`tether.extract_choices`). So the command line and the
library accept the same values. `--policy` on `tether batch` and `--intensity-quantity` on
`tether export` also have an argparse `choices` list.

### Detection settings: pick one source

`--tdat` and `--detection-mode`/`--detection-threshold` set the same values. So Tether refuses
the combination before it reads a movie. It does not let one of them win with no message:

```console
$ tether extract movie.tif -o movie.tether --tdat run.tdat --detection-mode wavelet
tether extract: --detection-mode/--detection-threshold cannot be combined with --tdat (the .tdat supplies the detection mode); pass one or the other
```

The rule for `--tdat`, in `tether extract` and `tether batch` alike:

- **A `.tdat` sets `detection_mode` and `detection_threshold`, and no other option.** Every
  other option keeps its value from a flag, a profile or its default. A `.tdat` that holds no
  threshold keeps the threshold unset, so each detection mode uses its own.
- **It is refused beside a choice of either field**: a `--detection-mode` or
  `--detection-threshold` flag, or an [extraction profile](#extraction-profiles) in which
  either is not `null`. The refusal comes before any movie is read.
- **The values it applied are recorded.** `/settings/extraction` stores the
  `detection_mode` and `detection_threshold` that ran, and the `.tdat` file name as
  `tdat_source`.

### Registration: one map source

Each run takes its registration map from exactly one source. These are the sources, in the order
in which they apply:

1. **Native fit**, the default. Tether pairs control points from the detections of the movie
   and fits a degree-2 map. `--donor-side` names the donor half.
2. **`--tmap PATH`**: a Deep-LASI `.tmap` replaces the native fit.
3. **`--map PATH`**: a saved Tether `.npz` map file replaces the native fit. Tether applies it as
   it was saved. Its coefficients, its channel geometry, its RMS residual and its low-confidence
   verdict do not change. The run records the RMS gate of the map, and `--rms-gate` does not
   change it.

A map file or a `.tmap` replaces the native fit, so Tether ignores `--donor-side`. Tether never
combines the two files. Neither one can win, so Tether refuses both together before it reads a
movie. `--tdat` is a separate rule. It sets **detection**, not registration. So you can give it
with any of the three sources, but not with `--detection-mode`/`--detection-threshold` (above).

```console
$ tether extract movie.tif -o movie.tether --tmap run.tmap --map beads.npz
tether extract: --tmap and --map cannot be combined (each one supplies the registration map); pass one or the other
```

The project records which source made the map. In `/settings/extraction`,
`registration_source` is `native`, `imported` (a `.tmap`) or `map-file`. `tmap_source` or
`map_source` holds the file name. The `/calibration` group of a map-file run also stores a
`map_file` record in its provenance, beside the origin of the map. The record holds the name and
the sha256 of the file, the map id and the record of the map. It also holds the result of the
reuse check.

### Reuse a map file

A map file is an asset of its own. One bead map can register the movies of many projects. Each
project records the **map id** and the **sha256** of the map file that it used. So two projects
that used the same map name the same file.

Before a map registers a movie, Tether checks that the map fits the movie. The check compares the
optics, never the date. An old map with the same optics is as good as a new one
([ADR-0146](adr/0146-the-map-file-is-a-separate-asset-and-reuse-checks-optics-not-the-date.md)).

| The map and the movie differ in | Result |
|---|---|
| The pixel size: the map's `--pixel-size`, and the movie's from its file. | Refused. The message gives both values. |
| The optics text: the map's `--optics`, and the movie's `--optics`. | Refused, unless you confirm with `--confirm-optics`. |
| A value that one side does not know. | Not compared. The project records it as *not compared*. |

A count with `--count-only` runs the same check, so it refuses what the extraction would refuse.

```console
$ tether map create beads.tif -o beads.npz --optics "60x TIRF, Cy3/Cy5, EMCCD 1x1" --pixel-size 0.267
$ tether extract movie.tif -o movie.tether --map beads.npz --optics "100x TIRF, Cy3/Cy5"
tether extract: movie.tif: could not use --map beads.npz: the map's optics differ from the movie's (optics: the map says '60x TIRF, Cy3/Cy5, EMCCD 1x1'; the movie says '100x TIRF, Cy3/Cy5'); confirm that the map applies to use it (--confirm-optics, or confirm_optics=True)
```

The library check is
[`check_map_reuse`](api/imaging.md#tether.imaging.map_asset.check_map_reuse). It also compares
the channel layout and the channel geometry when the caller gives those of the movie, and it
refuses a difference in either.
[`save_project_map`](api/imaging.md#tether.imaging.map_asset.save_project_map) saves the map
that a project holds as a separate map file.
[`map_asset_from_tmap`](api/imaging.md#tether.imaging.map_asset.map_asset_from_tmap) makes a map
file from a `.tmap`, with the origin `tmap` and no quality record.

### Extraction profiles

An *extraction profile* is a JSON file that holds every extraction option. So one file describes
how a movie was extracted. `tether extract --profile` and `tether batch --profile` read one. Its
form is the form of a [service-command request](#service-commands), with
`"extraction-profile"` in place of the command name:

```json
{"command":"extraction-profile","fields":{"disk_radius":2.5,"ring_inner":5.0,
"window":19},"version":1}
```

- **A field that you leave out keeps its default.** So a profile that you write by hand names
  only the options that it changes. The fields are the option names with `_` for `-`. Seven more
  options have no flag: `prealign_upsample`, `prealign_low_sigma`, `prealign_high_sigma`,
  `disk_radius`, `ring_inner`, `ring_outer` and `bg_window`.
  [Analysis parameters](reference/parameters.md) gives the default and the effect of each one.
- **The channel layout is four fields.** `channel_layout` is the kind: `side-by-side`,
  `top-bottom` or `rectangles`, or `null` for the split that `donor_side` gives.
  `channel_layout_donor` is the donor half of a half layout: `left` or `right`, or `top` or
  `bottom`. `null` takes the first. `channel_layout_donor_rect` and
  `channel_layout_acceptor_rect` are the two rectangles of a `rectangles` layout. Each is
  `[y1, x1, y2, x2]`, with 1-based inclusive bounds.

  Tether refuses a layout beside a `donor_side` other than `left`, and beside `--tmap` or
  `--map`. An example of a layout follows this list.
- **Tether checks the profile before it reads a movie.** The command stops when the profile has
  an unknown field, a value of the wrong type or a value that extraction refuses. Its message
  names the file. An even `window` and a `ring_outer` that does not fit in the aperture
  window are values that extraction refuses.
- **The profile replaces the option flags.** Tether refuses `--profile` with any flag from
  `--donor-side` to `--rms-gate`, also when the flag repeats the value of the profile. Change the
  value in the file instead. You can give `--tdat` with a profile whose `detection_mode` and
  `detection_threshold` are `null` ([the rule above](#detection-settings-pick-one-source)).
- **The values that ran are recorded.** Tether writes every option into `/settings/extraction`,
  as for a run with no profile. It does not record the file name of the profile.

For example, this profile is for a camera that puts the donor in the bottom half:

```json
{"command":"extraction-profile","fields":{"channel_layout":"top-bottom",
"channel_layout_donor":"bottom"},"version":1}
```

A profile is a file that you name, not a setting of the application. So you can keep it with the
data and use it on another computer
([ADR-0129](adr/0129-an-extraction-profile-is-a-file-in-the-request-text-form.md)). From
Python, `tether.services.write_profile` writes a profile, and
`tether.services.ExtractionProfile.from_options` makes one from an `ExtractOptions`.

### Choose the colocalization rule

A molecule starts as a donor spot. The default rule is `--colocalization partner-in-both`, which
is the rule that Deep-LASI uses. It keeps a donor spot only when Tether also detected an acceptor
spot within `--coloc-distance` of its mapped position.

> **Extract with `--colocalization donor-anchored` when your sample has a weak acceptor or a
> low-FRET population.** The default rule **drops the low-FRET and acceptor-dark molecules**.
> Their acceptor is too weak to be detected as a spot of its own.

`donor-anchored` keeps every donor spot whose aperture fits in the frame. Under both rules,
Tether reads the acceptor intensity at the mapped donor position.

To see what each rule keeps before you start a run, add `--count-only`. It writes nothing, but
`-o` is still required:

```console
$ tether extract movie.tif -o movie.tether --count-only
Molecules in movie.tif under each colocalization rule (nothing written):
  partner-in-both: 412 (selected)
  donor-anchored: 455
```

A large difference between the two counts means that many donor spots have no detected
acceptor. The project records the rule of each movie. Each molecule records whether an acceptor
spot was detected for it (`/molecules/acceptor_detected`).

- A project that was extracted before the rule could be chosen used the donor-anchored rule, and
  Tether reads it that way.
- When you extract the movie again, the new default applies, unless you name `donor-anchored`.
- `tether batch --colocalization` sets the rule of every movie in a batch.
- In an [extraction profile](#extraction-profiles), the rule is the `colocalization` field.
  Tether then refuses `--colocalization` beside `--profile`, as it does the other option flags.

**Exit codes**: see the `tether extract` column of the [exit-code table](#exit-codes).

### Example — one movie to one project

```bash
tether extract movie.tif -o movie.tether
```

```console
Extracted 412 molecule(s) -> movie.tether
  colocalization: partner-in-both kept 412 (donor-anchored would keep 455)
  donor side: 38 of 40 bleach events agree with the donor side as extracted.
```

The second line names the [colocalization rule](#choose-the-colocalization-rule) and what the
other rule would keep. The third line is the
[donor-side check](#get-donor-side-right-and-read-the-donor-side-check).

When the RMS residual of the registration is over `--rms-gate`, the extraction still succeeds
and exits 0. A warning goes to stderr. Tether tags the affected molecules
`low-confidence-registration` in the project. You can filter on that tag later, so the result is
not a pass with no message.

## `tether reextract`

Extract a movie of a project again, as a new *trace generation*
([ADR-0136](adr/0136-a-new-extraction-of-a-movie-is-a-new-trace-generation.md)). Use it when the
map, the channel geometry or an extraction option of a movie that you curated changes. Tether
never overwrites the earlier extraction. It stays readable in the project, with its labels.

```text
tether reextract [-h] [--map PATH] [--tmap PATH] [--tdat PATH] [--profile PATH]
                 [--match-px PX]
                 project movie
```

| Argument | Default | Meaning |
|---|---|---|
| `project` | *(required)* | The `.tether` project that holds the movie. |
| `movie` | *(required)* | The movie file, with the bytes that the project recorded. |
| `--map PATH` | none | Apply a saved Tether `.npz` map file in place of a native fit. You cannot give it with `--tmap`. |
| `--tmap PATH` | none | Apply an imported Deep-LASI `.tmap` in place of a native fit. |
| `--tdat PATH` | none | Apply the particle-detection mode of a Deep-LASI `.tdat`, as for `tether extract`. |
| `--profile PATH` | none | Read every extraction option from an [extraction profile](#extraction-profiles). Without one, each option has the default of `tether extract`. |
| `--match-px PX` | `None` | The "did not move" distance, in pixels. `None` means 0.5 pixels (`GENERATION_MATCH_PX`). |

**What moves to the new generation.** A molecule of the new extraction *did not move* when it is
within `--match-px` of a molecule of the previous extraction. Each must also be the closest to
the other. It then keeps the key of that molecule. Its label, its category and its analysis window
move to it, with the mark **carried**.

- A carried label does not train the ranker until a person confirms it in the application
  (Curation → Reconcile trace generation…). Accepting or rejecting the molecule again also
  confirms it.
- Every fit and correction of the movie becomes stale.
- The aperture options of the profile (`disk_radius`, `ring_inner`, `ring_outer`, `bg_window`)
  must equal the aperture options of the project.

The command holds the writer lock of the project for the whole run. So it refuses a project that
the application or another command has open for writing. It prints the counts of the three lists
that the reconciliation page shows:

```console
$ tether reextract experiment.tether movie_010.tif --map beads.npz
Extracted 214 molecule(s) as generation 2 of movie movie_010 -> experiment.tether
  198 carried (198 waiting for confirmation), 16 new, 9 lost. The fits and corrections of the movie are stale
```

A **new** molecule has no partner in the previous generation, so it has no labels. A **lost**
molecule of the previous generation has no partner in the new one. It and its labels stay
readable in the previous generation. `tether status` prints the same counts.

**Exit codes:**

- `0`: Tether wrote the new generation.
- `1`: the run failed, and the project did not change. For example, the movie is not in the
  project, or the project is locked.
- `2`: bad command-line arguments.

A recipe step runs the same command:
`{"command": "reextract", "fields": {"project": ..., "movie": ...}}`.

## `tether batch`

Process many movies unattended. Give it a folder of movies, a pattern or the movie files
themselves. With `--out-dir`, each movie becomes its own `<out-dir>/<stem>.tether`. With
`--into PROJECT.tether`, every movie goes into that one project, which can hold several movies of
one experiment ([`.tether` project](glossary.md#tether-project),
[see below](#every-movie-into-one-project-into)). Give one of the two.

One bad movie does not stop the run. Tether records a checkpoint after each stage. So when you
run the same command again, it does only the stages that are not complete.

```text
tether batch [-h] [--recursive] [--dry-run] (-d OUT_DIR | --into PROJECT.tether)
             [--tmap PATH] [--tdat PATH] [--profile PATH] [--policy {warn,fail}]
             [--colocalization {partner-in-both,donor-anchored}] [--no-idealize]
             [--overwrite] [--log PATH] [--max-restarts N] [--sidecar-timeout SECONDS]
             [--sidecar-python PATH] [--no-defer] [--quiet | --verbose]
             movies [movies ...]
```

| Argument | Default | Meaning |
|---|---|---|
| `movies` | *(required, one or more)* | Dual-channel movies, TIFF, CZI, ND2 or cellSens `.vsi`. Each is a movie file, a folder or a quoted pattern ([see below](#which-movies-a-folder-or-a-pattern-gives)). |
| `--recursive` | off | A folder argument also gives the movies in its sub-folders. |
| `--dry-run` | off | Print each movie, its output project and what the run would do with it, then exit `0` (note 1). |
| `-d OUT_DIR`, `--out-dir OUT_DIR` | *(this or `--into`)* | The directory for the per-movie `.tether` projects. Tether creates it if it is absent. |
| `--into PROJECT.tether` | *(this or `--out-dir`)* | Append every movie to this one project. The first movie creates the project if it is absent (note 2). |
| `--tmap PATH` | none | A shared imported Deep-LASI `.tmap`, applied to every movie. |
| `--tdat PATH` | none | A shared Deep-LASI `.tdat` detection configuration, applied to every movie. It sets only the detection mode and the detection threshold (note 3). |
| `--profile PATH` | none | Extract every movie with the options of an [extraction profile](#extraction-profiles). You cannot give it with `--colocalization` (note 4). |
| `--policy {warn,fail}` | `warn` | The over-gate registration policy. `warn` keeps an over-gate movie with a flag, and `fail` records its extract stage as failed (note 5). |
| `--colocalization {partner-in-both,donor-anchored}` | `partner-in-both` | The colocalization rule of the extraction of each movie. See [`tether extract`](#choose-the-colocalization-rule). |
| `--no-idealize` | off | Skip the idealization stage, so the run only extracts and corrects. |
| `--overwrite` | off | Extract an incomplete output again, or a completed extraction that the current `fail` policy rejects. You cannot give it with `--into` (note 6). |
| `--log PATH` | `<out-dir>/batch-log.jsonl`, or `<project>-batch-log.jsonl` beside the `--into` project | Write a JSONL structured log to this path. |
| `--max-restarts N` | `3` | Restart the idealization of a movie up to N times after a transient sidecar failure, a crash or a time-out. `0` disables restarts. |
| `--sidecar-timeout SECONDS` | `1800` | The sidecar time-out of each idealization call, in seconds. |
| `--sidecar-python PATH` | `$TETHER_SIDECAR_PYTHON` | The sidecar interpreter for idealization (note 7). |
| `--no-defer` | off | Do not defer idealization when the sidecar is unavailable at startup. The idealize stage of each movie then fails on its own. |
| `--quiet` | off | Print no progress lines on stderr. The summary on stdout does not change ([see below](#progress-while-the-batch-runs)). |
| `--verbose` | off | Also print each stage event on stderr, as the structured log records it. You cannot give it with `--quiet`. |

**Notes**

1. A dry run writes nothing. It reads no movie, except to hash it under `--into`. See
   [Check a batch before it runs](#check-a-batch-before-it-runs-dry-run).
2. Tether skips a movie that is already in the project. Correct and idealize run once, over the
   whole project. See [Every movie into one project](#every-movie-into-one-project-into).
3. A `.tdat` sets `detection_mode` and `detection_threshold`. Tether refuses it with a
   `--profile` that sets either. See [the rule](#detection-settings-pick-one-source).
4. An option that the file leaves out keeps its default. Without a profile, every option except
   `--colocalization` is at its default.
5. Under `fail`, Tether still writes the project file, so you can inspect it. On a resumed run,
   Tether evaluates the saved residual again. See
   [How policy `fail` behaves on resume](#how-policy-fail-behaves-on-resume).
6. An accepted completed checkpoint is still skipped.
7. Idealization runs in the isolated tMAVEN sidecar, not in the application environment. Without
   `--sidecar-python`, Tether uses `$TETHER_SIDECAR_PYTHON`. An installed build also falls back
   to the `envs/sidecar` interpreter that comes with the application. So a normal install needs
   neither.

**When no sidecar is found at startup, the run *defers* idealization.** Extraction and correction
still happen for every movie. With `--no-defer`, the idealize stage of each movie fails on its
own instead.

> **Use a new `--out-dir` to extract a set of movies with a changed profile.** A profile changes
> how a movie is extracted, not which stages count as done. A movie whose extraction finished in
> an earlier run is not extracted again with a new profile, not even with `--overwrite`.

### Stage status in the batch summary

`tether batch` prints one status for each stage of each movie (`tether.project.batch`):

| Status | Meaning | Counts as failure? |
|---|---|---|
| `done` | the stage ran and succeeded | no |
| `skipped` | a checkpoint found the stage already complete | no |
| `failed` | the stage raised (or an over-gate movie under `--policy fail`) | **yes** |
| `blocked` | an upstream stage did not complete, or (with `--into`) the movie left the project | no |
| `deferred` | the sidecar was unavailable at startup | no |
| `not-requested` | `--no-idealize` | no |

A `warning` is not a stage status. It is a log event for a problem that is not fatal. Examples
are a failed provenance stamp and a write lock that was not released. It goes to the structured log at
the WARNING level. Unless you give `--quiet`, the progress output on stderr shows it too. It does
not change the exit code.

In a finished run, only `failed` makes the run exit `1`. A `deferred` stage in a run with no
`failed` stage makes it exit `3`. See
[a deferred idealization exits 3](troubleshooting.md#the-batch-finished-ok-but-nothing-was-idealized).
The command also exits `1`, with no summary, when it cannot create the output directory or write
the batch log (`tether.services.batch`).

A stage is usually blocked because its upstream stage was not `done`/`skipped`. So a `blocked`
usually travels with a `failed` in the same movie, and the run exits `1` anyway. There is one
exception. With `--into`, a movie that another writer removed from the project after its
extract stage gets `correct=blocked`, with the detail `no longer in <project>`. Its extract
stage is `done` or `skipped`, so that run can exit `0`.

### Which movies a folder or a pattern gives

Each `movies` argument is one of three things. Tether expands it itself, so the same command
gives the same movies in PowerShell, cmd and bash:

- **A folder** gives the movie files directly in it. A movie file has an extension that
  extraction reads, in uppercase or lowercase. Today these are `.tif`, `.tiff`, `.czi`, `.nd2`
  and `.vsi`. With `--recursive`, a folder also gives the movies in its sub-folders.
    - Tether opens `.sif` and `.sira` movies too, but extraction does not read them yet. So a
      folder or a pattern leaves them out. A `.sif` or `.sira` file that you name on its own
      fails its extraction stage, with a message that says so
      ([Movie containers](reference/movie-containers.md)).
    - An original beside its TIFF export in one folder (`x.nd2` and `x.tif`, `x.czi` and `x.tif`,
      or `x.vsi` and `x.tif`) is one movie. So the run stops before it writes anything, and the
      message names both files. Move or rename one.
    - A `.vsi` movie needs the optional Bio-Formats stack. Without it, its stage fails with the
      install message.
- **A pattern** is an argument with `*`, `?` or `[...]` in it that is not an existing path. It
  gives the movie files that it matches. `**` matches any number of folders. Tether expands a
  folder that the pattern matches as a folder argument. Put a pattern in quotes, so that bash
  gives it to Tether as it is.
- **A file** is used as it is, whatever its extension.

Folders and patterns leave out names that start with `.`. On some drives, macOS writes
`._name.tif` files beside movies. Tether puts the movies of all the arguments in one list,
sorted by path. It processes a movie that two arguments give once. An argument that gives no
movie stops the run before anything is written, and the message names the argument:

```console
$ tether batch data/day4 -d out
tether batch: folder 'data/day4' holds no movie file (.tif, .tiff, .czi, .nd2, .vsi); add --recursive to include its sub-folders
```

A folder that Tether cannot list stops the run in the same way. This includes each sub-folder
that `--recursive` or a pattern looks in. So a batch never runs with one of them left out.

### Output names when two movies have the same file name

Acquisition software often numbers the files in the same way in the folder of each day. So
`day1/movie_010.tif` and `day2/movie_010.tif` are common. Two movies with one output name would
share one project. The checkpoint would then take the second movie as done, with the data of the
first. So the output name follows one rule:

- Tether names an output after the file name of the movie without its extension, as
  `movie_010`.
- While two names are the same, each of those movies adds its next parent folder in front,
  joined with `__`: `day1__movie_010` and `day2__movie_010`. A movie whose name is not shared
  keeps its own name. So a batch without equal file names writes the same names as before.
- Tether compares names without regard to case on every operating system.
  `Movie_010.tether` and `movie_010.tether` are one file on Windows and macOS.

The folders are those of the full path of the movie. So the names do not depend on the folder
that you run the command from. Tether reads the folders as you typed them, with one exception:
after a link, `..` goes to the parent of the target of the link.

For example, `work/link` is a
link to `archive/sub`. Then `work/link/../m.tif` is the file `archive/m.tif`. Tether reads its
folders from where it really is, so its name is `archive__m`. Windows reads `..` before it
follows a link. So on Windows the path is `work/m.tif`, and the batch holds that movie once.

If no folder tells two movies apart, as for `A.tif` and `a.tif` in one folder, the run stops
before it writes anything. `--dry-run` shows each name before the run. The structured log
records each movie and its output as an `input` event with status `mapped`.

> **Run the batch with `--dry-run` first when you change the movies that it holds.** An output
> name depends on the whole list of movies. For example, you add `day3/movie_010.tif` to a batch
> that had only `day1/movie_010.tif`. The output of the day 1 movie then becomes
> `day1__movie_010`, and the run does not resume the old `movie_010.tether`.

### Every movie into one project (`--into`)

`--into PROJECT.tether` writes the movies of one experiment into one project. So the conditions,
the population views and the ranker see them together:

```bash
tether batch data --recursive --into experiment.tether
```

- **Each movie is appended.** The first movie creates the project when it does not exist. Tether
  appends each later movie under the writer lock of the project. So a failed append leaves the
  project as it was, and the run goes on with the next movie. Every movie must use the aperture
  window, the radii and the background window of the first movie of the project. A movie
  extracted with other values fails.
- **A movie already in the project is skipped.** Tether knows a movie by its content, not by its
  name. So the same command run again appends no movie twice. Two copies of one file go in once.
  Tether hashes a movie to check this only when the project holds a movie of the same file size.
- **Correct and idealize run once, over the whole project,** after the last append. The dataset
  α and γ then come from the molecules of every movie.
    - A run that appends a movie runs both again, over every movie in the project. Tether then
      fits the idealization model again, in place of the stored one.
    - A run that appends nothing skips them. There are two exceptions. One is an earlier `--into`
      run that left them unfinished, for example with idealization deferred. The other is a
      movie appended since then from Python with `tether.project.extract.add_movie_to_project`.
      Each append marks both stages unfinished when it adds the movie.
    - A project that has gained no movie keeps its correction and its model.
- **The summary still has one line for each movie.** The name of a movie is the name that
  [the output name rule](#output-names-when-two-movies-have-the-same-file-name) would give its
  own project. So movies with one file name stay apart. Each movie in the project shows the
  correct and idealize results of the project. A movie that is not in it has them `blocked`.
  The progress lines and the structured log give those two stages once, as `tether batch:` run
  lines and `(batch)` events.
- **`--policy fail` keeps an over-gate movie out.** Its extract stage is `failed`, and Tether
  writes nothing of it, so it is not in the project. A later run tries it again.
- `--into` with `--out-dir` is a usage error (exit `2`). So is `--into` with `--overwrite`,
  because Tether skips a movie that is in the project and never replaces it.

`--dry-run --into` prints one line for each movie. The line names the project and says whether
the movie is new to it. A `Project:` line for correct and idealize comes last:

```text
Batch plan (dry run): 3 movie(s) into the one project experiment.tether; nothing was written (a movie is read only to hash it, when the project or an earlier movie of the run has its size)
  data/day1/movie_010.tif -> experiment.tether  [already in the project as movie 'a3f1c2d4e5b6': will be skipped]
  data/day1/movie_011.tif -> experiment.tether  [will be appended]
  data/day2/movie_010.tif -> experiment.tether  [will be appended]
Project: correct and idealize will run over every movie in the project after the appends
```

When the project does not exist yet, the line of the first movie shows
`new project: will be created with this movie`. A file that holds the same movie as an earlier
file of the run shows `same content as <name>: will be skipped`. The run skips it after the
earlier file is in.

### Check a batch before it runs (`--dry-run`)

`--dry-run` prints the plan and exits `0`. It reads no movie, creates no folder and writes no
file. [With `--into`](#every-movie-into-one-project-into), it can read a movie to hash it. It
prints these lines:

- one line for each movie, with its output project and what the run would do with it,
- the settings,
- the path of the structured log,
- whether a sidecar interpreter was found.

```text
Batch plan (dry run): 3 movie(s) into projects; no movie was read and nothing was written
  data/day1/movie_010.tif -> projects/day1__movie_010.tether  [new]
  data/day1/movie_011.tif -> projects/movie_011.tether  [exists, complete: will be skipped]
  data/day2/movie_010.tif -> projects/day2__movie_010.tether  [exists: will resume at idealize]
Settings: policy=warn, idealize=on, overwrite=off, recursive=on, tmap=none, tdat=none, channel-layout=none, donor-side=left, detection-mode=wavelet, detection-threshold=mode default, max-restarts=3, sidecar-timeout=1800.0s, defer=on
Structured log: projects/batch-log.jsonl
Sidecar: found at /opt/tether/envs/sidecar/bin/python (the run checks that it starts before the first movie)
```

The settings also say how Tether would extract each movie:

- `channel-layout` gives the split. Without a layout, `donor-side` follows it. A half layout
  adds its `donor` half, and a `rectangles` layout adds `donor-rect` and `acceptor-rect`. With
  `--tmap`, the split reads `from .tmap`, because the `.tmap` holds its own crops.
- `detection-mode` and `detection-threshold` follow. The threshold reads `mode default` when the
  profile leaves it `null`. Both read `from .tdat` when `--tdat` supplies them.

The text of a line comes from the checkpoint of the output project, which Tether opens
read-only:

| The line says | The output project |
|---|---|
| `new` | does not exist yet |
| `exists, complete: will be skipped` | holds every stage the run asks for |
| `exists: will resume at` a stage | holds some stages, and the run does the others |
| `exists, incomplete:` | holds no finished extraction, and only `--overwrite` replaces it |
| `exists, over the gate:` | holds an extraction that `--policy fail` rejects |

The dry run reports a refusal to start as the run would, with exit code `2`. A movie argument
that gives no movie is one refusal. A profile `channel_layout` beside `--tmap` is another,
because the `.tmap` splits each frame at its own crops. The dry run only looks for the sidecar
interpreter. The real run also starts it once before the first movie.

### Progress while the batch runs

A batch over a condition can run for hours. So `tether batch` prints progress to **stderr** while
it works:

1. One setup line comes before the first movie is read. It gives the number of movies, the
   output folder, the policy and whether you asked for idealization. Check that line first. A
   wrong option shows there, not hours later in the summary.
2. Each movie then gets a start line and an end line. The end line gives its position, its name,
   the result, the time that it took and the status of every stage:

```text
tether batch: 3 movie(s) into projects (policy=warn, idealize=on)
tether batch: idealize deferred - sidecar unavailable — idealization deferred: no sidecar interpreter: pass sidecar_python= or set TETHER_SIDECAR_PYTHON to a Python in an env built from sidecar/conda-lock.yml with tMAVEN installed
[1/3] cond_a_010: started
[1/3] cond_a_010: ok in 4m12s (extract=done correct=done idealize=deferred)
[2/3] cond_a_011: started
[2/3] cond_a_011: ok in 3m58s (extract=done correct=done idealize=deferred; flagged: extract)
[3/3] cond_a_012: started
[3/3] cond_a_012: FAIL in 0.1s (extract=failed(could not extract from cond_a_012.tif: not a TIFF file: header=b'not ') correct=blocked idealize=blocked)
```

A line about the whole run starts with `tether batch:`, as the deferred idealization above does.
The end line names a flagged stage after `flagged:`. A stage is flagged when it kept the movie
with a flag, as an over-gate registration under `--policy warn` does. A stage is also flagged
when it raised a warning, as the
[donor-side check](#the-donor-side-check-in-the-batch-summary) does.

There are three levels:

| Level | stderr while the run works |
|---|---|
| `--quiet` | no progress line |
| default | the setup line, the run-level lines, and a start and an end line for each movie |
| `--verbose` | the default lines, and also one line for each stage event, such as `[1/3] cond_a_010 extract: done - 3 molecule(s)` |

The lines come from the same events as the structured log, so the two cannot disagree. They are
plain text with no cursor control. So a redirected stderr or the log file of a scheduler reads
the same as a terminal.

**stdout does not change at any level.** It holds only the end-of-run summary and its
`Exit code` line. So a script that reads stdout is not affected. Two lines on stderr do not
depend on the level:

- the `structured log:` path at the end,
- a failed stage, which Python's logging prints as `movie · stage: failed — reason`.

### How policy `fail` behaves on resume

`fail` is a verdict that Tether records about a movie. It does not stop the output. Extraction
writes the `.tether` project first. Tether then applies the gate to the summary that the write
returns.

So an over-gate movie under `--policy fail` leaves a **complete, checkpointed** project
in `<out-dir>`. Its extract stage is recorded `failed`, and correct and idealize are `blocked`.
That run exits `1`, as documented.

On a later run with `--policy fail`, Tether reads the saved `registration_rms_px` and the
effective `rms_gate` again. It does this before it accepts that extraction checkpoint. If the
residual is still over the saved gate, extraction stays `failed` and the downstream stages stay
`blocked`. The command exits `1` again. The project stays available for inspection.

- **A rerun with `--policy warn`** accepts the flagged extraction checkpoint. Extraction is then
  `skipped`, and the incomplete downstream stages can continue.
- **`--overwrite --policy fail`** extracts again only a rejected checkpoint, and applies the gate
  to the new result. A completed extraction within the gate stays checkpointed and is still
  skipped.

**The locks of `--overwrite --policy fail`.** Batch claims the lock of each destination first.
It does this before it tests whether the project file exists and before it reads a checkpoint.

An existing writer lock fails that movie as locked. This includes a lock that another thread of
the same process holds. Batch does not refresh or release that existing lock. A
reservation for each destination, inside the process, also stops another local thread from
refreshing that lock file or writing through it. Batch holds both claims while it creates,
skips or extracts the destination again.

### The donor-side check in the batch summary

`tether batch` has no `--donor-side` option, so Tether extracts every movie with the donor on the
left. After each extraction, batch reads the stored
[donor-side check](#get-donor-side-right-and-read-the-donor-side-check). It also does this on a
resumed run that skips the extract stage. A movie whose halves look swapped is marked
`halves-swapped?` and listed in a footer. The check never changes the status of a movie or the
exit code:

```text
Batch run: 2 movie(s), 2 ok, 0 failed (policy=warn, idealize=off)
  ok    fine_012     extract=done correct=done idealize=not-requested
  ok    flipped_011  extract=done correct=done idealize=not-requested halves-swapped?
Donor and acceptor halves look swapped in 1 movie(s) (advisory; nothing was changed):
  flipped_011: 27 of 27 bleach events are consistent with swapped donor and acceptor halves; extract this movie again with --donor-side right
Exit code 0: every requested stage ran for every movie.
```

The structured log carries the same finding, as a `donor-side-check` event with status
`warning`. A movie with too few bleach events to check, or with no clear majority, adds nothing
to the summary. To act on a flagged movie, extract it again with `tether extract` and the remedy
that the message names.

**Exit codes**: see the `tether batch` column of the [exit-code table](#exit-codes).

### Example — a folder of movies, extraction and correction only

```bash
tether batch data -d projects --no-idealize
```

When `data` holds three movies, the summary on stdout reads like this:

```text
Batch run: 3 movie(s), 3 ok, 0 failed (policy=warn, idealize=off)
  ok    movie_010  extract=done correct=done idealize=not-requested
  ok    movie_011  extract=done correct=done idealize=not-requested
  ok    movie_012  extract=done correct=done idealize=not-requested
Exit code 0: every requested stage ran for every movie.
```

The same command works in PowerShell and cmd. To take the movies of several day folders, give
the parent folder with `--recursive`, or a quoted pattern. A quoted pattern also works in every
shell:

```bash
tether batch data --recursive -d projects --dry-run
tether batch "data/2026-11-*" -d projects --no-idealize
```

The end-of-run summary goes to stdout. The JSONL log goes to `projects/batch-log.jsonl`, unless
you give another path with `--log`. Later, run the same command without `--no-idealize`. Only the
idealization stage runs, and Tether skips everything that already has a checkpoint.

## `tether map create`

Make a channel map file from a bead-slide video or from one bead image. Later movies can then be
extracted with it (`tether extract --map`), and each one does not fit its own registration.

Tether detects, pairs and fits the beads of both halves with the stages of a native extraction.
It saves the map as one `.npz` file with three records:

- its source identifier, the sha256 of the bead file,
- its projection settings,
- its quality record.

The **Calibration → Make channel map…** menu of the Prepare section in the application runs the
same command.

```text
tether map create [-h] -o OUTPUT [--overwrite] [--frames START STOP]
                  [--projection {max,mean}] [--donor-side {left,right}]
                  [--rms-gate RMS_GATE] [--optics TEXT] [--pixel-size UM]
                  source
```

| Argument | Default | Meaning |
|---|---|---|
| `source` | required | A bead TIFF: one image, or a video of frames. |
| `-o`, `--output OUTPUT` | required | The `.npz` map file to write. The name must end in `.npz`. |
| `--overwrite` | off | Replace an existing map file, only with a complete one. Tether refuses while another `tether map create` holds the writer lock of the map file. |
| `--frames START STOP` | every frame | For a video: the 0-based, half-open range of frames to project. |
| `--projection {max,mean}` | `max` | For a video: how Tether reduces the frames to one image. |
| `--donor-side {left,right}` | `left` | Which half of the frame is the donor channel. |
| `--rms-gate RMS_GATE` | `0.5` | The gate on the RMS residual of the registration, in pixels. |
| `--optics TEXT` | none | A text for the optics, such as the objective, the filters, the camera and the binning. The map keeps it, and [the reuse check](#reuse-a-map-file) compares it with the `--optics` of a later movie. |
| `--pixel-size UM` | not known | The camera pixel size in micrometres. The map keeps it, and Tether refuses a later movie with another pixel size. |

```console
$ tether map create beads.tif -o beads.npz
Map -> beads.npz: RMS 0.214 px over 38 bead pair(s), 71% of the field covered
  map id: map:3f2a9c0d6e1b47a8b5c4d3e2f1a0b9c8
$ tether extract movie.tif -o movie.tether --map beads.npz
```

A map over `--rms-gate` is still written, and the command exits `0` with a warning on stderr.
Tether flags the map low-confidence. It tags every molecule extracted with the map
`low-confidence-registration`.

**Exit codes:**

- `0`: Tether wrote the map, also when it is over the gate.
- `1`: one line on stderr gives the reason. These are the reasons:
    - a bead file with too few beads in either half,
    - a bad frame range,
    - an output that exists, without `--overwrite`,
    - an output whose writer lock another writer holds,
    - an output name without `.npz`.
- `2`: bad command-line arguments.

Without `--overwrite`, Tether saves the map as a hard link to a finished temporary file. A hard
link never replaces a file. Some file systems have no hard links, such as a FAT drive or some
network shares. There, Tether copies the map instead, under the writer lock of the map file. So a copy that fails removes
only its own file. Whenever Tether takes the writer lock, it leaves a `<file>.lock.guard` file
beside the map, as it does beside a project.

The map file has a `format_version`, now `2`. Tether reads a map file of its own version or
older, and refuses one that a newer version wrote.

Version 2 makes the map a separate asset
([ADR-0146](adr/0146-the-map-file-is-a-separate-asset-and-reuse-checks-optics-not-the-date.md)).
Besides the transform and the channel geometry, it holds the map id and its origin: `beads`,
`tmap` or `native-fit`. It also holds the source ids of the files that made it and the channel
layout. Then come the quality record, the optics text, the pixel size, the time of the save and
the Tether version. A file of version 0 or 1 still reads. Its id is the sha256 of its bytes, and
it has no layout, optics text, pixel size or time.

The file holds only plain arrays and text.
Tether reads it with pickling switched off, so no stored Python object can run when a map loads.
[ADR-0128](adr/0128-the-map-file-carries-a-format-version-and-a-reader-refuses-a-newer-one.md)
records this version rule.

## `tether run`

Run a recipe file. A recipe is a list of service requests, the same requests that
`tether extract`, `tether batch`, `tether idealize` and `tether map create` send
([service commands](#service-commands)). The application writes one with
**File → Save as recipe…**. The [recipe guide](how-to/run-a-recipe.md) shows a full recipe.

```text
tether run [-h] [--dry-run] [--resume] recipe
```

| Argument | Default | Meaning |
|---|---|---|
| `recipe` | required | The recipe file (`.json`). |
| `--dry-run` | off | Print the steps, the inputs of each step and each approval step, and change nothing. |
| `--resume` | off | Skip the completed steps at the start of the latest run, and continue from the first unfinished step. |

**How a run records its steps.** Each step runs through its service command, in order. It runs
as a job stored in the project that the step writes:

- the `output` of `extract`,
- the `project` of `reextract` and of `idealize`,
- the `into` of `batch`.

The one part of the job names the recipe by the SHA-256 of its text, and the step by its number.
Tether also records progress in its per-user state, for the path and the contents of the recipe.
This includes the successful steps that write no single project, such as `map`, or `batch`
without `into`. The run stops at the first failed step. A count-only extraction records progress
in the recipe checkpoint, and does not change the named project.

**How `--resume` continues.** A run without `--resume` starts a new checkpoint. `--resume` tries
the first unfinished step again. It then runs every later step again, because those steps can
depend on its results. When no step is complete, as for a changed recipe, `--resume` starts from
the first step.

The run stops when a step defers a stage. If the step has a job, Tether stores the job as
`failed`. `--resume` runs that step again. So the checkpoint of the command can finish the
deferred stage before the next recipe step starts. Tether reads relative paths from the folder of
the recipe file.

**Refused recipes.** Tether refuses a recipe before anything runs when it is not valid. These
make a recipe not valid:

- an unknown command, field or version,
- a value of the wrong type,
- a policy with a missing value,
- a **publish step**. A recipe can make and upload a deposit draft, but it can never publish.
- an `approve` step. A recipe approves only with a policy.
- a field that names an account secret.

For a recipe that idealizes, select the sidecar interpreter on your computer with
`TETHER_SIDECAR_PYTHON`, or use the installed sidecar. A recipe cannot select the interpreter.

**Approval steps.** At a step that needs a person, a policy that the recipe names for the step
decides (ADR-0102). With no policy and no recorded approval, the run stops with exit code `4`. It
prints the command that approves the step and the command that continues the run. No command
reaches an approval step yet. So today Tether refuses a recipe that names a policy, and it gives
the reason.

**Exit codes:**

- `0`: every step ran, or `--dry-run` printed its plan.
- `1`: a step failed.
- `2`: Tether refused the recipe, and nothing ran.
- `3`: no step failed, but a stage was deferred, as for [`tether batch`](#tether-batch).
- `4`: the run stopped at a step that needs a person.

### Example — a run that stops at an approval step

```text
tether run: stopped at approval step 'gamma.approve' for 'KJG_231027': the recipe has no policy for it.
  evidence: runs/gamma-KJG_231027.json
  approve:  tether approve exp.tether gamma.approve --subject KJG_231027 --report runs/gamma-KJG_231027.json
  continue: tether run recipe.json --resume
```

You can copy each command and run it as printed. Tether puts a path or a subject that holds a
space in quotes. It uses double quotes for the Tether Command Prompt on Windows, and shell quotes
on macOS and Linux, for example `--subject 'condition 1'`. The command that `tether run` prints
when a step fails has the same quotes.

> **On Windows, run the printed commands in the Tether Command Prompt.** PowerShell reads quotes
> in a different way.

On Windows, Tether writes a `"` in a value as `""`, and a `%` as `^%` outside the quotes. For
example, it writes the subject `%USERNAME%` as `--subject ""^%"USERNAME"^%""`. The Tether Command
Prompt then gives Tether the value as it is, also when the value holds `&`, `|`, `<`, `>` or `^`.
It does not expand a `%NAME%` of the environment in the value.

## `tether approve`

Record that a person approved a step that needs a person, on the evidence of its report
([ADR-0102](adr/0102-a-batch-run-stops-at-an-approval-step-or-applies-a-recipe-policy.md)).

```text
tether approve [-h] --subject SUBJECT --report REPORT [--by NAME]
               project {map.accept,alpha.confirm,gamma.approve,fit.select}
```

| Argument | Default | Meaning |
|---|---|---|
| `project` | required | The `.tether` project to record the approval in. |
| `step` | required | The approval step: `map.accept`, `alpha.confirm`, `gamma.approve` or `fit.select`. |
| `--subject SUBJECT` | required | What is approved: the map, the condition or the analysis set. |
| `--report REPORT` | required | The report that is the evidence: a `.json` file. Tether also records each PNG beside it whose name starts with its stem. |
| `--by NAME` | the curator name of new labels | The name of the person. |

The approval is one row of `/settings/approvals`. The row holds the step and the subject,
`person` and the name, the SHA-256 of each report file, and the time. When `tether run` stopped
in this project, the row also names that recipe. `tether run RECIPE --resume` then goes on past
the step.

**Exit codes:**

- `0`: Tether recorded the approval.
- `1`: the write failed, for example because the project is locked.
- `2`: Tether refused the approval before any write. The reasons are an unknown step, an empty
  subject, a report that is not a readable `.json` file and a project that cannot be opened.

### Example — approve γ for one condition

```console
$ tether approve exp.tether gamma.approve --subject KJG_231027 --report runs/gamma-KJG_231027.json --by "A. Curator"
exp.tether: gamma.approve for 'KJG_231027' approved by A. Curator
```

When `tether run` stopped at this step, a second line follows. It names the first 12 characters
of the SHA-256 of the recipe and tells you to continue with `--resume`.

## `tether save-composition`

Write a plot composition to a file: the plot, the project it reads, the series in drawing order
and the plot parameters. The file holds no trace data. *Save composition…* in the Analyze section
of the application runs the same service command. [Read a plot](how-to/analyze.md) describes the
plot builder.

```text
tether save-composition [-h] --project PROJECT --plot PLOT --series SPEC [--model MODEL]
                        [--state NAME=K] [--force]
                        path
```

| Argument | Default | Meaning |
|---|---|---|
| `path` | required | The composition file to write, a `.json` file. |
| `--project PROJECT` | required | The `.tether` project that the plot reads. |
| `--plot PLOT` | required | The plot: the name of a population function, such as `population_dwell_times`. |
| `--series SPEC` | required | A series. Give it once for each series, in drawing order (note 1). |
| `--model MODEL` | none | The idealization model that a model-derived plot reads. |
| `--state NAME=K` | none | A state option of the plot: `state`, `init_state` or `final_state`. Give it once for each option. |
| `--force` | off | Replace a file that exists. |

1. A `SPEC` is `all`, for every molecule of the project, or a preset: `condition=VALUE`,
   `movie=VALUE` or `source_file=VALUE`. Tether freezes the molecules of a preset when the command
   runs. A later change of the project does not change which molecules the file names.

**Exit codes:**

- `0`: Tether wrote the file.
- `1`: the write failed.
- `2`: Tether refused before any write. The file exists and `--force` is not given, or the
  project cannot be found. Or the plot, a preset value or a state option is unknown to the plot.
  Or two series have one name. Tether never replaces the project or a file that is not a plot
  composition, also with `--force`.

### Example — save a dwell-time plot of every molecule

```console
$ tether save-composition dwell.json --project exp.tether --plot population_dwell_times --series all --model vbconhmm --state state=1
dwell.json: population_dwell_times over All molecules
```

## `tether open-composition`

Read a plot composition file. Print its plot, its project, its parameters and its layer list, and
name each series whose project or molecules cannot be found. *Open composition…* in the
application runs the same service command.

```text
tether open-composition [-h] path
```

| Argument | Default | Meaning |
|---|---|---|
| `path` | required | The composition file to read. |

Tether checks each series against the project. A series is missing when its project cannot be
found or when a molecule that it names is not in the project. The application leaves a missing
series out of the layer list and never draws it.

**Exit codes:**

- `0`: Tether read the file, and every series can be drawn.
- `1`: Tether read the file, and at least one series cannot be drawn. stderr names each one and
  what is missing.
- `2`: Tether refused the file. It cannot be read, it is not a plot composition, or it is of a
  format version that this Tether does not read.

## `tether info`

Print a summary of what one or more projects hold, without the application and without a Python
script. The summary gives the movies, the molecule and curation counts, the corrections, the
idealization models and the lock holder. Use it to check the result of a batch run, or to put a
summary of a project in a support request.

```text
tether info [-h] [--json] [--stale] projects [projects ...]
```

| Argument | Default | Meaning |
|---|---|---|
| `projects` | *(required, one or more)* | One or more `.tether` projects. |
| `--json` | off | Print one JSON document in place of text. The layout is [below](#json-layout). |
| `--stale` | off | Also count the stale molecules of each idealization model. This reads the trace arrays of the fits, so it is slower on a large project. |

**It does not change the project.** The command opens the project read-only and takes no lock. So
it works on a project that another user holds. It does not change the project file or its
`.lock` file.

It reads only the tables and the attributes of the project. It does not read the
trace arrays, except for `--stale`, and it never opens a movie file. So it is fast on a large
project, and it does not download a OneDrive placeholder movie.

For each project, the text output gives these items:

- The schema version, and the Tether version that created the project.
- Each movie: its file name, its frame count, its frame time, its molecule count and the
  curation counts of its molecules.
- Each condition and its molecule count. A condition that the molecules name but that has no
  `/conditions` row yet shows `(not yet in /conditions)`.
- The molecule count and the curation counts (accepted, rejected, undecided) for all movies.
- The category counts, and how many molecules have no category.
- Whether the correction pass ran, and how many molecules have each correction method
  (`none` is a molecule with no method).
- Each idealization model: its method, its state count, its molecule count and, with
  `--stale`, its stale molecule count. Without `--stale` the text says `stale not checked`.
- The ranker model reference: the entries under `/models`, or `none`.
- The lock: `not held`, the holder, or `present but unreadable` when Tether cannot parse the
  `.lock` file. The holder is the user, the host, the process identifier, the time it took the
  lock, and `active` or `stale`. A lock is stale after 30 minutes, the same limit that the
  application uses.

**Exit codes**: see the `tether info` column of the [exit-code table](#exit-codes).

### Example — check the projects of a batch run

```console
$ tether info out/movie_010.tether
out/movie_010.tether
  schema version 4, written by Tether <version>
  movies: 1
    movie_010.tif: 1200 frames, 0.1 s/frame, 214 molecules
      curation: 120 accepted, 30 rejected, 64 undecided
  conditions: 1
    cond-353fd5a76531: 214 molecules
  molecules: 214
  curation: 120 accepted, 30 rejected, 64 undecided
  categories: none set (214 uncategorized)
  corrections: ran (corrected 190, apparent-E (corrections unavailable) 24)
  idealizations: 1
    vbconhmm: method vbconhmm, 3 states, 184 molecules, stale not checked (use --stale)
  ranker model: none
  lock: not held
```

Give several projects to summarize them in one run. `tether info` does not expand a pattern
itself. So in Windows PowerShell, list the projects, or use
`tether info (Get-ChildItem projects\*.tether)`.

A project that Tether cannot read gives one line on stderr, `tether info: <path>: <reason>`. The
other projects are still printed. These are the reasons:

- the file does not exist,
- it is not a `.tether` project,
- its schema version is newer than this version of Tether can read.

### JSON layout

`--json` prints one JSON document for all the projects that you give. The layout has a version,
`format_version`. A later version of Tether can add keys and keep the version. It changes the
version when it renames or removes a key, or changes the meaning of a value. A value that the
project does not record is `null`. These are the keys:

| Key | Meaning |
|---|---|
| `format` | always `"tether-info"` |
| `format_version` | the version of this layout (now `1`) |
| `tether_version` | the version of Tether that printed the document |
| `projects` | one entry for each project argument, in the order given |
| `projects[].path` | the project path as given |
| `projects[].error` | only in the entry of a project that could not be read: the reason. Such an entry has no other keys |
| `projects[].schema_version` | the schema version of the project |
| `projects[].written_by` | the Tether version that created the project, or `null` |
| `projects[].n_molecules` | the molecule count |
| `projects[].movies` | one entry for each movie |
| `projects[].movies[].movie_id` | the movie identifier in the project |
| `projects[].movies[].name` | the file name of the movie |
| `projects[].movies[].uri` | the movie path recorded at extraction (the file is not opened) |
| `projects[].movies[].n_frames` | the frame count |
| `projects[].movies[].frame_time_s` | seconds for each frame, or `null` when the project does not record it |
| `projects[].movies[].n_molecules` | the molecule count of the movie |
| `projects[].movies[].curation` | the curation counts of the molecules of the movie |
| `projects[].movies[].curation.accepted` | accepted molecules |
| `projects[].movies[].curation.rejected` | rejected molecules |
| `projects[].movies[].curation.undecided` | molecules not accepted and not rejected |
| `projects[].conditions` | one entry for each condition |
| `projects[].conditions[].condition_id` | the condition identifier |
| `projects[].conditions[].materialized` | `true` when the condition has a `/conditions` row |
| `projects[].conditions[].n_molecules` | the molecule count of the condition |
| `projects[].conditions[].key` | the identity fields of the `/conditions` row: `construct_variant`, `dye`, `ligand`, `ligand_concentration`, `ligand_concentration_unit`, `buffer`, `temperature_c`, `laser_power`. Empty when `materialized` is `false` |
| `projects[].curation` | the curation counts of all molecules |
| `projects[].curation.accepted` | accepted molecules |
| `projects[].curation.rejected` | rejected molecules |
| `projects[].curation.undecided` | molecules not accepted and not rejected |
| `projects[].categories` | a map from each category name to its molecule count |
| `projects[].uncategorized` | molecules with no category |
| `projects[].corrections` | the correction state |
| `projects[].corrections.ran` | `true` when the correction pass ran |
| `projects[].corrections.methods` | a map from each correction method to its molecule count; the key `""` is a molecule with no method |
| `projects[].idealizations` | one entry for each idealization model |
| `projects[].idealizations[].name` | the model name |
| `projects[].idealizations[].method` | the method that made the model, for example `vbconhmm` |
| `projects[].idealizations[].n_states` | the state count |
| `projects[].idealizations[].n_molecules` | the molecule count of the model |
| `projects[].idealizations[].stale` | the stale molecule count with `--stale`, else `null` (not checked) |
| `projects[].models` | the entries under `/models` (the ranker model reference); empty when there is none |
| `projects[].lock` | the `.lock` file as read |
| `projects[].lock.held` | `true` when a `.lock` file is present |
| `projects[].lock.corrupt` | `true` when the `.lock` file cannot be parsed; the owner fields are then `null` |
| `projects[].lock.host` | the computer of the holder |
| `projects[].lock.user` | the user name of the holder |
| `projects[].lock.pid` | the process identifier of the holder |
| `projects[].lock.acquired_utc` | the time the lock was taken (ISO 8601, UTC) |
| `projects[].lock.age_s` | the age of the lock in seconds |
| `projects[].lock.stale` | `true` when the lock is older than 30 minutes |

## `tether status`

Print the readiness of each step of a project. The output gives the steps that are complete, the
steps that are stale and why, the jobs stored in the project, and the next step. The application
reads the same function to know where a project is. So the command line and the application
agree on the next step. Use the command in a script to wait for a stage, or to see where to
continue a project.

```text
tether status [-h] [--json] [--require STAGE] project
```

| Argument | Default | Meaning |
|---|---|---|
| `project` | *(required)* | The `.tether` project. |
| `--json` | off | Print one JSON document in place of text, in the layout [below](#status-json-layout). |
| `--require STAGE` | none | Exit `3` unless the stage is ready or not needed. Give it again for more stages (note 1). |

**Notes**

1. `STAGE` is one of the stage keys in the table below.

**It does not change the project.** As for `tether info`, Tether opens the project read-only and
takes no lock. So the command works on a project that another user holds. To say whether a fit is
stale, it reads the trace arrays of the fit, as `tether info --stale` does.

The stages, in the order of the workflow, and the section of the application that holds each one:

| Stage key | Name | Section | Ready when |
|---|---|---|---|
| `sources` | sources and geometry | Prepare | the project has a movie |
| `map` | map | Prepare | each movie has a map |
| `traces` | trace generation | Prepare | the project has molecules |
| `curation` | curation | Curate and correct | no molecule is undecided, or the initial curation was finished |
| `corrections` | corrections | Curate and correct | the correction state is `ANALYSIS_READY` |
| `analysis_sets` | analysis sets | Analyze | not available in this version |
| `fits` | fits | Analyze | the project has a fit and no fit is stale |
| `results` | results | Analyze | not available in this version |
| `deposit` | deposit | Deposit | not available in this version |

Each stage has one of these states:

- `ready`.
- `not needed`: a project made from imported traces has no movie and no map.
- `in progress` or `not started`.
- `stale`: a record it depends on has changed, and the record has not been made again.
- `blocked`: the correction state is `CALIBRATION_BLOCKED`.
- `running`: a job of the stage is running now.
- `not available in this version`: the feature of the stage is not in this version yet.

The corrections stage also names the correction state of the project
([the correction states](reference/tether-format.md#correction-states),
[ADR-0131](adr/0131-the-correction-states-are-a-transition-log-in-the-project.md)). It is one of
`APPARENT_REVIEW`, `BLEEDTHROUGH_REQUIRED`, `GAMMA_REVIEW`, `CALIBRATION_BLOCKED`,
`CALIBRATION_APPROVED`, `CORRECTED_REVIEW`, `ANALYSIS_READY` and `CALIBRATION_STALE`.

**How the next step is chosen.** Tether looks for these, in this order:

1. a failed or interrupted job to try again, unless a job of the same command finished
   successfully after its latest attempt,
2. a running job to wait for,
3. the first stale stage,
4. the first stage that is not ready.

A stage that is not available is never the next step. The step for a stale fit is
`tether idealize PROJECT --stale-only`. A fit imported from standalone tMAVEN is the exception,
because that command never refits it. For such a fit, the step is to give its molecules to tMAVEN
again and import the new fit.

**Exit codes:**

- `0`: Tether read the project, and each `--require` stage is ready.
- `1`: Tether could not read the project. One line `tether status: <path>: <reason>` goes to
  stderr.
- `2`: bad command-line arguments.
- `3`: a `--require` stage is not ready. One line for each such stage goes to stderr, and the
  document or the text is printed too.

### Example — the status of a project with a stale fit

```console
$ tether status out/movie_010.tether
out/movie_010.tether
  Prepare
    sources and geometry: ready (1 movie)
    map: ready (1 movie with a map)
    trace generation: ready (214 molecules)
  Curate and correct
    curation: in progress (120 accepted, 30 rejected, 64 undecided)
    corrections: not started (correction state APPARENT_REVIEW)
  Analyze
    analysis sets: not available in this version (#934)
    fits: stale (1 model)
    results: not available in this version (#943)
  Deposit
    deposit: not available in this version (#956)
  new trace generations: 1 movie(s), 198 carried (12 waiting for confirmation), 16 new, 9 lost
  stale: 1
    fit vbconhmm: 3 of 184 molecules changed since the fit (their traces, window or correction factors)
  jobs: none
  next step: refit the stale models (tether idealize PROJECT --stale-only)
```

### Status JSON layout

`--json` prints one JSON document. The layout has a version, `format_version`, with the same rule
as `tether info`. A later version of Tether can add keys and keep the version. It changes the
version when it renames or removes a key, or changes the meaning of a value.

| Key | Meaning |
|---|---|
| `format` | always `"tether-status"` |
| `format_version` | the version of this layout (now `1`) |
| `tether_version` | the version of Tether that printed the document |
| `path` | the project path as given |
| `stages` | one entry for each stage, in workflow order |
| `stages[].key` | the stage key, as `--require` takes it |
| `stages[].name` | the name the text output prints |
| `stages[].section` | `prepare`, `curate`, `analyze` or `deposit` |
| `stages[].state` | `ready`, `not_needed`, `in_progress`, `not_started`, `stale`, `blocked`, `running` or `not_available` |
| `stages[].detail` | a short detail, for example the counts |
| `stages[].action` | what to do when the stage is not done, or `""` when it is done or not available |
| `stages[].issue` | for a stage that is not available, the issue that adds it, else `null` |
| `correction_state` | the correction state, for example `"GAMMA_REVIEW"` |
| `stale` | one entry for each stale record |
| `stale[].stage` | the stage key of the record |
| `stale[].item` | the record |
| `stale[].reason` | why it is stale |
| `jobs` | one entry for each job stored in the project, in the order they were made |
| `jobs[].job_id` | the job identifier (the text output prints its first 8 characters) |
| `jobs[].command` | the command the job runs, for example `extract` |
| `jobs[].stage` | the stage key the job works on, or `null` |
| `jobs[].state` | `queued`, `running`, `done`, `failed`, `cancelled` or `interrupted` |
| `jobs[].n_parts` | how many parts the job has |
| `jobs[].n_completed` | how many parts are complete |
| `jobs[].error` | the error of a failed job, else `""` |
| `next_step` | the next step |
| `next_step.stage` | its stage key, or `null` when every available stage is ready |
| `next_step.section` | its section, or `null` |
| `next_step.action` | what to do |
| `reconciliation` | the molecules of the movies extracted again ([`tether reextract`](#tether-reextract)). The text output prints this line only when `movies` is not `0` |
| `reconciliation.movies` | how many movies have more than one trace generation |
| `reconciliation.carried` | the molecules whose labels moved from the previous generation |
| `reconciliation.new` | the molecules of the active generation with no partner in the previous one |
| `reconciliation.lost` | the molecules of the previous generation with no partner in the active one |
| `reconciliation.waiting` | the carried molecules that wait for a person to confirm them |

## `tether idealize`

Fit a hidden Markov model to the traces of a project that exists, without a Python script. The
project can come from `tether extract`, or be one that you curated or corrected after
`tether batch`. The fit gives a small set of FRET states and the state of each molecule at each
frame. Tether stores it in the project as an idealization model. With `--stale-only`, the command
refits each model whose inputs changed after its fit. [Idealize traces into FRET
states](how-to/idealize.md) explains the methods and what to do with the result.

```text
tether idealize [-h] [--method {vbconhmm,ebhmm}] [--nstates N | --nstates-grid LIST]
                [--model-name NAME] [--condition ID] [--include-rejected] [--overwrite]
                [--seed N] [--stale-only] [--max-restarts N]
                [--sidecar-timeout SECONDS] [--sidecar-python PATH]
                project
```

> **Close the project in the desktop application before you run the command.** The command holds
> the lock of the project while it fits. When another user or the application holds the lock,
> the command exits `1` and fits nothing.

| Argument | Default | Meaning |
|---|---|---|
| `project` | *(required)* | The `.tether` project to fit. |
| `--method {vbconhmm,ebhmm}` | `vbconhmm` | The fit. `vbconhmm` is the consensus VB-HMM, one shared model across all molecules, and `ebhmm` is ebFRET. |
| `--nstates N` | automatic | Fit exactly N states. Without it, the command fits each state count of `--nstates-grid` and keeps the one with the largest ELBO. |
| `--nstates-grid LIST` | `1,2,3,4` | The state counts that the automatic choice tries, comma-separated, for example `2,3,4,5`. Do not give it with `--nstates`. |
| `--model-name NAME` | the method | Store the fit as `/idealization/NAME`, where NAME is one name with no `/`. With `--stale-only`, refit only this model. |
| `--condition ID` | all molecules | Fit only the molecules of this condition ID, which `tether info` lists. Give it again for more conditions. |
| `--include-rejected` | off | Also fit the molecules marked rejected. Without it, the command leaves them out. |
| `--overwrite` | off | Replace a model of the same name. Without it, the command refuses and fits nothing. |
| `--seed N` | unseeded | Seed every fit, so the same project, options and seed give the same model on the same build. The project does not store the seed. |
| `--stale-only` | off | Refit each model that has a stale molecule, with the settings stored in the model. Leave the other models as they are (note 1). |
| `--max-restarts N` | `3` | Restart a fit up to N times after a sidecar crash or time-out. `0` disables restarts. |
| `--sidecar-timeout SECONDS` | `1800` | The sidecar time-out of each fit, in seconds. |
| `--sidecar-python PATH` | `$TETHER_SIDECAR_PYTHON` | The sidecar interpreter for the fit. |

**Notes**

1. `--stale-only` takes none of the fit options above except `--model-name`.

**The default fit.** With no option, the command fits the molecules that are not rejected. It
uses the consensus VB-HMM and an automatic state count. It stores the model under the name of the
method, so the default fit is the model `vbconhmm`. This is the same fit as
`idealize_molecules` in Python with the same options.

**The sidecar.** The fit runs in the tMAVEN sidecar, which the command finds as `tether batch`
does. Before it writes anything, the command checks that the sidecar can start. When the sidecar
cannot start, the command says why, exits `3` and does not change the project.

**The lock.** The command reads the molecules of each `--condition` again after it takes the
lock. A fit can take minutes. The command releases the lock when it ends.

**`--stale-only` refits a whole model.** A molecule is *stale* in a model when its trace, its
analysis window or its correction factors changed after the fit. The command refits every
molecule of a model that has a stale molecule, not only the stale ones
([why](#why-stale-only-refits-a-whole-model)).

It does not refit a model with no stale molecule.
When no model has one, the command needs no sidecar. The command counts the stale molecules again
after it takes the lock. So it also refits a change that another writer made before then.

**After a new trace generation, a refit finds each molecule by its key.** A new extraction of a
movie gives each of its molecules a new `molecule_id`. The refit finds a molecule of the model by
its `molecule_id`, or else by its `molecule_key`. A molecule that did not move keeps its key.

A molecule that moved has a new key. So the refit leaves it out, and the command names it on a
line `N moved in a new trace generation, not refitted: <keys>`. When no molecule of a model is
left, Tether refuses the refit of that model.

**`--stale-only` never refits a model imported from standalone tMAVEN.** Such a model stores no
fit settings that Tether can repeat. The command reports it as `imported from tMAVEN, not
refitted`, and leaves it as it is, stale or not. Tether refuses such a model named with
`--model-name`. To refresh it, give its molecules to tMAVEN again and import the new fit.

The command stores the fit. It does not select an *active fit*. Comparing fits and selecting
one is planned work.

**Exit codes:**

- `0`: Tether stored the fit, or `--stale-only` found nothing stale.
- `1`: the fit failed, or the command could not take the lock.
- `2`: bad command-line arguments, or a refusal to start. These are the refusals:
    - an unknown condition,
    - a model that exists, without `--overwrite`,
    - a project that cannot be opened,
    - fit options with `--stale-only`,
    - an imported model named with `--stale-only`.
- `3`: the sidecar was not available, and the command wrote nothing.

### Example — fit a project, then refit it after a correction

```console
$ tether idealize movie_010.tether --seed 1
movie_010.tether
  model: vbconhmm
  method: vbconhmm
  states: 3 (largest ELBO)
  ELBO by state count:
    1: -1520.4
    2: -1302.7
    3: -1288.1
    4: -1291.6
  molecules fitted: 184
$ tether idealize movie_010.tether --stale-only
movie_010.tether
  vbconhmm: 12 stale, 172 current; refitted
```

The numbers above are an example, not a reference result.

## `tether metadata`

Show, or give, the metadata of the movies of a project. The metadata are the frame time, the
pixel size and the channel roles (which half is the donor). Each value has an **origin**:

- `file`: Tether read it from the movie. The sources are the ImageJ `finterval`, the ImageJ
  pixel size in µm and the channel crops of an imported `.tmap`.
- `user`: a person gave it. A native extraction records the channel roles as `user`, because
  `--donor-side` names the donor half.
- `unknown`: there is no value.

A frame time is **verified** only when it comes from the file, or when you confirm it in a second
call. A value that you type stays not verified until you confirm it. Tether stores this flag now.
Rate calculations do not check it yet. That is planned work.

```text
tether metadata [-h] [--movie MOVIE_ID] [--frame-time SECONDS] [--pixel-size UM]
                [--confirm-frame-time] [--json]
                project
```

| Argument | Default | Meaning |
|---|---|---|
| `project` | *(required)* | Path to the `.tether` project. |
| `--movie MOVIE_ID` | the only movie | The movie to change. It is required when the project has more than one movie. |
| `--frame-time SECONDS` | none | Give the frame time, in seconds (origin `user`, not verified). |
| `--pixel-size UM` | none | Give the pixel size, in µm per pixel (origin `user`). |
| `--confirm-frame-time` | off | Mark the frame time of the movie as verified. Tether refuses it in the same call as `--frame-time`. |
| `--json` | off | Print the records as JSON in place of text. |

With no option, the command changes nothing and prints each movie. A value of `0` means unknown.
Tether keeps the values beside the `/movies` table. `/movies.frame_time` keeps the value from
the file ([`.tether` file format](reference/tether-format.md#movies-one-row-per-source-movie)).

**Exit codes**: see the `tether metadata` column of the [exit-code table](#exit-codes).

### Example — give a frame time, then confirm it

```console
$ tether metadata run.tether --frame-time 0.1
mov-…:
  frame time: 0.1 s (user, not verified)
  pixel size: 0 um (unknown)
  channel roles: user
$ tether metadata run.tether --confirm-frame-time
```

## `tether export`

Write the results of one or more `.tether` projects to files that another tool can read. The
command has three forms. Each form calls one function of `tether.project.export`. So the file is
the same file that the function writes for the same settings. The
[Exports reference](reference/exports.md) tells what each file contains.

| Form | Writes | Function |
|---|---|---|
| `tether export table` | one CSV row per molecule | `export_molecule_table_csv` |
| `tether export traces` | the Deep-LASI donor and acceptor trace matrix | `export_deeplasi_txt` |
| `tether export subset` | a smaller `.tether` without the movie | `export_subset_tether` |

These rules apply to all three forms:

- **The project is only read.** The export takes no lock. It does not change the project or its
  `<file>.lock` file. You can export a project that another user has open.
- **Every export writes a provenance file.** Its name is the name of the export with
  `.provenance.json` added. It records the Tether version, the source project and the settings
  ([what the provenance file records](reference/exports.md#provenance-file)).
- **An existing file is kept.** If the export or its provenance file exists, the export stops
  with exit code `1`. The message tells you to give `--overwrite`.
- **An input project is never a destination.** An output path that is the project itself, or a
  hard link to it, stops the export with exit code `1`:
  `subset out_path movie.tether is the source project; refusing to overwrite`.
    - With several projects, Tether checks every export and provenance file against every
      project that you gave, before the first export starts. A match stops the command with exit
      code `1`, and nothing is written.
    - So `movie.tether` and `movie-subset.tether` exported into one folder cannot replace the
      second project with the subset of the first, not even with `--overwrite`.
    - Tether refuses the `<file>.lock` file of a project that you gave in the same way.
- **Two exports never share a file.** Two outputs or provenance files that are hard links to one
  existing file stop the command with exit code `1` before the first export. This is also true
  with `--overwrite`, because the second export would replace the file of the first. `subset`
  replaces its `.tether` file with a new one, which breaks the link. So for `subset`, Tether
  compares only the provenance files.
- **Several projects go into a folder.** Give several projects, and `-o` names a folder. Tether
  makes the folder if necessary, and names each export after its project: `<project>.csv`,
  `<project>-donc-accc-w.txt` or `<project>-subset.tether`.
    - Two projects with the same file name would write the same export. Names that differ only in
      uppercase and lowercase count as the same. That command stops with exit code `2` before it
      writes a file.
    - A project that fails does not stop the others. The command then exits `1`.
- **Each export prints one line,** the number of molecules and the path:
  `Exported 412 molecule(s) -> molecules.csv`.

**Exit codes**: see the `tether export` column of the [exit-code table](#exit-codes). The three
forms share one set of exit codes.

### Example — the molecule table of every project after a batch run

> **Make the folder first.** If the pattern matches only one project, `-o` names the file to
> write, unless it is an existing folder.

```bash
mkdir tables
tether export table projects/*.tether -o tables
```

```powershell
# Windows PowerShell has no shell glob expansion for native commands
mkdir tables
tether export table (Get-ChildItem projects\*.tether) -o tables
```

With two projects, the output is one line for each project:

```console
Exported 412 molecule(s) -> tables/movie_010.csv
Exported 455 molecule(s) -> tables/movie_011.csv
```

## `tether export table`

Write one CSV row per molecule: the stored per-molecule fields and the apparent-E summary
([Molecule-table CSV](reference/exports.md#1-molecule-table-csv)). The table includes rejected
molecules, with `reject` in the `curation_label` column.

```text
tether export table [-h] -o OUTPUT [--overwrite]
                    [--intensity-quantity {corrected,raw}]
                    [--exclude-rejected]
                    projects [projects ...]
```

| Argument | Default | Meaning |
|---|---|---|
| `projects` | *(required, one or more)* | One or more `.tether` projects to read. |
| `-o OUTPUT`, `--output OUTPUT` | *(required)* | The `.csv` file to write. With several projects, or an existing folder, it is a folder with one `<project>.csv` per project (note 1). |
| `--overwrite` | off | Replace an existing output file and its provenance file. |
| `--intensity-quantity {corrected,raw}` | `corrected` | Which trace layer to read (`intensity_quantity`). |
| `--exclude-rejected` | off | Leave rejected molecules out of the table (`include_rejected=False`). |

**Notes**

1. Tether creates the folder if it is absent.

```console
$ tether export table movie.tether -o molecules.csv
Exported 412 molecule(s) -> molecules.csv
```

## `tether export traces`

Write the per-frame donor and acceptor traces as a Deep-LASI trace matrix
([Deep-LASI trace matrix](reference/exports.md#2-deep-lasi-trace-matrix-txt)). `-o` always names
a folder. The file in it is named `<project>-donc-accc-w.txt`. Tether refuses an `-o` that ends
in `.txt` and does not exist yet, with exit code `2`, because it is almost certainly a file name.
The matrix leaves out rejected molecules.

```text
tether export traces [-h] -o OUTPUT [--overwrite]
                     [--intensity-quantity {corrected,raw}]
                     [--include-rejected] [--molecule-key KEY]
                     projects [projects ...]
```

| Argument | Default | Meaning |
|---|---|---|
| `projects` | *(required, one or more)* | One or more `.tether` projects to read. |
| `-o OUTPUT`, `--output OUTPUT` | *(required)* | The folder to write one `<project>-donc-accc-w.txt` in for each project. Tether creates it if it is absent. |
| `--overwrite` | off | Replace an existing output file and its provenance file. |
| `--intensity-quantity {corrected,raw}` | `corrected` | Which trace layer to read (`intensity_quantity`). |
| `--include-rejected` | off | Keep rejected molecules (`include_rejected=True`). |
| `--molecule-key KEY` | every molecule | Export only this molecule. Give the flag again for each further molecule (`molecule_keys`). |

> **Export one movie at a time when the movies of a project have different lengths.** All
> molecules in one file share one frame axis. So give the `--molecule-key` values of one movie.
> Otherwise the export stops with exit code `1` and names the frame ranges.

```console
$ tether export traces movie.tether -o traces
Exported 382 molecule(s) -> traces/movie-donc-accc-w.txt
```

## `tether export subset`

Write a self-contained `.tether` project of the selected molecules, without the movie
([Subset `.tether`](reference/exports.md#3-subset-tether)). It leaves out rejected molecules and
the raw traces.

```text
tether export subset [-h] -o OUTPUT [--overwrite] [--include-rejected]
                     [--include-raw] [--molecule-key KEY]
                     [--curator-alias PREFIX]
                     projects [projects ...]
```

| Argument | Default | Meaning |
|---|---|---|
| `projects` | *(required, one or more)* | One or more `.tether` projects to read. |
| `-o OUTPUT`, `--output OUTPUT` | *(required)* | The `.tether` file to write. With several projects, or an existing folder, it is a folder with one `<project>-subset.tether` per project (note 1). |
| `--overwrite` | off | Replace an existing output file and its provenance file. |
| `--include-rejected` | off | Keep rejected molecules (`include_rejected=True`). |
| `--include-raw` | off | Also embed the raw and background traces (`include_raw=True`). |
| `--molecule-key KEY` | every molecule | Export only this molecule. Give the flag again for each further molecule (`molecule_keys`). |
| `--curator-alias PREFIX` | names are kept | Replace each stored user name with `PREFIX-1`, `PREFIX-2` and so on (`curator_alias`). With several projects, each copy numbers its own names. |

**Notes**

1. Tether creates the folder if it is absent.

```console
$ tether export subset movie.tether -o accepted.tether
Exported 382 molecule(s) -> accepted.tether
```

To share a copy without your user name, give a prefix:

```bash
tether export subset movie.tether -o shared.tether --curator-alias curator
```

A blank prefix fails the export of that project. So does a prefix that contains a user name
stored in the project. Tether then writes no file for that project, still exports the other
projects and exits `1`.

## `tether apertures`

Write the aperture and background report of one movie before the run: the report that the
**Apertures and background** tab of the Prepare page saves
([Check the apertures before the run](how-to/extract-traces.md#check-the-apertures-before-the-run)).
The command previews the movie as the extraction would run it and writes nothing to a project.
It calls the same two functions as the page, so for the same movie, options, registration and
molecules the two write the same files.

```sh
tether apertures movie.tif --json apertures.json --png apertures.png \
    --profile profile.json --molecule 0 --molecule 3
```

| Option | Meaning |
| --- | --- |
| `--json FILE` | The JSON report to write (required): the options, the aperture geometry, the counts, every spot with its mark, and the preview traces of each `--molecule`. |
| `--png FILE` | A PNG of the mean frame with the aperture and the background ring of every spot, coloured by mark. |
| `--profile FILE` | An [extraction profile](#extraction-profiles) with the options. Without one, the default options. |
| `--map FILE`, `--tmap FILE` | The registration: a channel map file or a Deep-LASI `.tmap`. Without one, a native fit. |
| `--window`, `--disk-radius`, `--ring-inner`, `--ring-outer` | Change one aperture option of the profile. |
| `--molecule N` | A molecule whose preview traces the report carries. Repeat it for more. |
| `--overwrite` | Replace an existing report. Without it the command refuses. |

A spot is marked `at-edge` when its crop leaves the donor or the acceptor channel, so the run
skips it, `overlap` when its aperture overlaps the aperture of its nearest molecule, and
`no-partner` when the `partner-in-both` rule skips it for want of an acceptor spot. The command
prints the counts. It exits `0` when it wrote the report, `1` when the review failed (for
example, the preview stopped before the traces), and `2` when it refused the options or an
existing output.

## Sync-conflict copies beside a project

OneDrive and SharePoint can leave a second copy of a file when two computers save it at the same
time. Tether does not prevent these copies. It finds them and tells you. Tether checks each
project that a command opened at these times:

- after `tether extract` writes its project,
- after `tether batch` finishes,
- when `tether info`, `tether metadata` or `tether export` reads a project.

It looks for files beside the project in the two documented naming forms:
`<stem>-<COMPUTERNAME>.tether` and `<stem> (<...>).tether`. A match adds one warning line to
stderr:

```text
  warning: 1 possible OneDrive/SharePoint conflict copy beside movie.tether: movie-DESKTOP-AB12.tether. Tether does not merge or delete it; compare and reconcile by hand.
```

The warning is advisory. It does not change the exit code. Tether does not open, merge, rename or
delete the copy. The warning goes to stderr, so the `tether info --json` document on stdout does
not change.

The match is by file name only. So Tether also lists a project that you named
`movie-donoronly.tether` on purpose, and you can ignore the line for it. The application shows
the same text in the Browser when you open the project.

## Service commands

`tether extract`, `tether reextract`, `tether batch`, `tether idealize`, `tether map create`,
`tether approve`, `tether save-composition` and `tether open-composition` are each the
command-line route of one *service command*
([ADR-0100](adr/0100-service-commands-are-the-one-way-to-run-an-action.md)). A service command
is the one implementation of an action ([why](#why-the-command-line-runs-service-commands)). The
command line only reads your options into the *request* of the command, runs the command and
prints the result.

The table comes from the registry in `tether.services.registry`. A test fails if the two differ:

<!-- service-commands:begin (made from tether.services.registry; do not edit) -->
| Command | Request | Result | What it does |
|---|---|---|---|
| `tether extract` | `tether.services.extract.ExtractRequest` (version 1) | `tether.services.extract.ExtractResult` | extract traces from a dual-channel movie into a .tether project |
| `tether reextract` | `tether.services.reextract.ReextractRequest` (version 1) | `tether.services.reextract.ReextractResult` | extract a movie of a .tether project again, as a new trace generation |
| `tether batch` | `tether.services.batch.BatchRequest` (version 1) | `tether.services.batch.BatchResult` | run the extract -> correct -> idealize pipeline over many movies |
| `tether idealize` | `tether.services.idealize.IdealizeRequest` (version 1) | `tether.services.idealize.IdealizeResult` | fit the molecules of a .tether project, or refit its stale models |
| `tether map` | `tether.services.maps.MapCreateRequest` (version 1) | `tether.services.maps.MapCreateResult` | make a channel map file (.npz) from a bead video or image |
| `tether approve` | `tether.services.approve.ApproveRequest` (version 1) | `tether.services.approve.ApproveResult` | record that a person approved a step that needs a person |
| `tether save-composition` | `tether.services.compositions.SaveCompositionRequest` (version 1) | `tether.services.compositions.SaveCompositionResult` | save a plot composition of the plot builder to a file (no trace data) |
| `tether open-composition` | `tether.services.compositions.OpenCompositionRequest` (version 1) | `tether.services.compositions.OpenCompositionResult` | open a saved plot composition and name each series that cannot be found |
<!-- service-commands:end -->

A request is plain data with one text form. The text form is a JSON object with the command
name, the version of the request and every field. For example, `tether extract movie.tif -o
movie.tether` sends this request. The text form is one line. It is broken here to fit the page.
A break between two parts of JSON does not change it:

```json
{"command":"extract","fields":{"bg_window":10,"channel_layout":null,
"channel_layout_acceptor_rect":null,"channel_layout_donor":null,
"channel_layout_donor_rect":null,"coloc_distance":3.0,"colocalization":"partner-in-both",
"confirm_optics":false,"count_only":false,"detection_block":50,"detection_mode":null,"detection_threshold":null,
"disk_radius":3.0,"donor_side":"left","map_optics":null,"map_path":null,
"min_separation":null,"movie":"movie.tif","output":"movie.tether","overwrite":false,
"pair_tol":2.0,"prealign":"translation","prealign_high_sigma":20.0,
"prealign_low_sigma":1.0,"prealign_upsample":10,"ring_inner":6.0,"ring_outer":8.0,
"rms_gate":0.5,"tdat":null,"tmap":null,"window":21},"version":1}
```

The field names are the option names with `_` for `-`. Both requests also hold the fields of an
[extraction profile](#extraction-profiles), and `--profile` sets them. The file itself is not
part of the request. So a request holds every value that it ran with. Two `tether batch` flags
are positive fields: `--no-idealize` is `"idealize": false`, and `--no-defer` is
`"defer_if_unavailable": false`. A recipe file, which [`tether run`](#tether-run) reads, is a
list of these requests.

## Exit codes

| Code | `tether extract` | `tether batch` | `tether info` | `tether metadata` | `tether export` |
|---|---|---|---|---|---|
| `0` | extraction completed | every requested stage ran for every movie, or `--dry-run` printed its plan | every project was read | the records were printed, after any change | every export was written |
| `1` | extraction failed (`ExtractionError`), or the `--profile` could not be used | at least one movie had a failed stage, or the output directory or the log could not be written (note 1) | at least one project could not be read | a refused change or an unreadable project (note 2) | at least one export failed, or the output path could not be checked or its folder made (note 3) |
| `2` | bad command-line arguments (argparse) | bad command-line arguments (argparse), **or** a refusal to start (note 4) | bad command-line arguments (argparse) | bad command-line arguments (argparse), **or** `--frame-time` and `--confirm-frame-time` in one call | bad command-line arguments (argparse), **or** a refusal to start (note 5) |
| `3` | never returned | no movie failed, and at least one requested stage was deferred for at least one movie | never returned | never returned | never returned |

**Notes**

1. A failed stage gives `1` whether or not a stage was also deferred.
2. The refused changes are these:
    - no movie to choose,
    - an unknown movie,
    - a value that is not finite and positive,
    - nothing to confirm,
    - a locked file.
3. An export fails for these reasons:
    - the project could not be read,
    - the output exists, and `--overwrite` was not given,
    - an output is an input project or its lock file,
    - two outputs or provenance files are one file (hard links),
    - the function refused the selection.
4. `tether batch` refuses to start for these reasons:
    - invalid sidecar-supervision values or extraction options,
    - a `--profile` that could not be used,
    - `--tdat` beside a detection choice,
    - `--tmap` beside a profile `channel_layout`,
    - a movie argument that gives no movie,
    - two input movies that no folder name tells apart.
5. `tether export` refuses to start for these reasons:
    - two projects that map to one export,
    - `-o` a file where a folder is needed,
    - a `.txt` `-o` for `traces`.

`tether status` has a code `3` of its own, for a `--require` stage that is not ready. Its codes
are in [its section](#tether-status). `tether idealize` has a code `3` of its own, for a sidecar
that cannot start. Its codes are in [its section](#tether-idealize).

**Exit code `2` means that no movie was processed.** Either argparse refused the command line
before any subcommand handler ran, or `tether batch` or `tether export` refused to start. On both
paths, Tether reads nothing and writes no project or export. So it is safe to fix the arguments
and run the command again.

**Exit code `1` means that the command *ran*.**

- From `tether extract`, it is a clean one-line error on stderr, never a traceback. An unhandled
  traceback from any subcommand is a bug. Report it.
- From `tether export`, it is one such line for each failed project. Tether still exports the
  other projects.
- From `tether batch`, it means that the run finished and at least one movie has a failed stage.
  Fix those movies and run the same command again to resume the rest. It also means that
  `tether batch` could not create `--out-dir` or write the log. It then prints one line on stderr
  that names the path.

> **Run the batch again when `tether batch` exits `3`.** The projects are not finished. When the
> sidecar is unavailable at the start of the run, the default is to *defer* idealization. The run
> records the stage as `deferred`, which is not `failed`. It exits `3` after extraction and
> correction only. The report prints a `Deferred:` line with the stage and the number of movies.
>
> Run the same command again when the sidecar is available. The checkpoint of each stage then
> resumes only the idealization. Or give `--no-defer`, so that an unavailable sidecar fails each
> movie (exit `1`).
>
> `1` comes before `3`: a run with a failed movie exits `1`, also when the idealization of another
> movie was deferred. A stage that you did not ask for is not deferred. So a run with
> `--no-idealize` exits `0` when every movie was extracted and corrected. The last line of the
> report names the exit code and its meaning. `tether batch --help` lists the four codes.

**Where to look for what failed.** `tether batch` prints its end-of-run summary to **stdout**.
The summary gives the detail for each movie and each stage. The JSONL structured log that the run
writes to `<out-dir>/batch-log.jsonl` holds the same detail. stderr carries these lines:

- the [progress lines](#progress-while-the-batch-runs), none with `--quiet`,
- the path of the log,
- startup refusals.

If you capture the streams separately, the complete failure detail of each movie is in stdout and
in the log. The progress lines on stderr are for watching the run.

### Exit code `4`: a person must approve a step

Code `4` has one meaning in every `tether` command: the run stopped before a step that needs a
person, because nothing approved that step. [`tether run`](#tether-run) returns it.
`tether batch` never returns it. It applies its `--policy` rule to the map and accepts the
estimated correction factors, as it does now
([ADR-0102](adr/0102-a-batch-run-stops-at-an-approval-step-or-applies-a-recipe-policy.md)).

The codes that have one meaning in every command:

- `0`: the command completed.
- `1`: the command ran, and at least one item failed.
- `2`: bad command-line arguments, or a refusal to start.
- `4`: the run stopped at an approval step, and nothing failed.

The approval steps are: accept a map, confirm the bleedthrough factor α, approve γ and select the
active fit. A command that stops with `4` writes one message to stderr. The message gives these
items:

- the step and its subject,
- the report that is the evidence,
- the command that approves the step,
- the command that continues the run.

Tether keeps the work done before the stop, and the continued run does not do it again. A run
that has a failed item and also stopped exits `1`. A code that this list does not name has the
meaning that the table of its command gives.

## Background

### Why `--stale-only` refits a whole model

A consensus VB-HMM or an ebFRET model is one fit over all its molecules. So
`tether idealize --stale-only` refits every molecule of a model that has a stale molecule, not
only the stale ones.

### Why the command line runs service commands

A service command is the one implementation of an action. The command line uses it now. The
desktop application and recipe files will use the same commands as their sections move onto
them. So the command line, the application and a recipe cannot do different things for the same
input.

### Why a `.tdat` and a detection flag are refused together

`--tdat` and `--detection-mode`/`--detection-threshold` set the same two values. If Tether took
both, one of them would win with no message. So Tether refuses the combination before it reads a
movie.
