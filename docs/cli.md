---
description: "Run tether extract and tether batch without the application window: script the pipeline, run movies unattended or repeat an extraction."
---

# Command line

**Who this page is for.** Anyone driving Tether without the application window: scripting the
pipeline, processing a night's worth of movies unattended, reproducing an
extraction from recorded settings, or exporting results from many projects. Today the
command line has nine subcommands: `tether extract` and `tether batch` write projects,
`tether reextract` extracts a movie of a project again as a new trace generation,
`tether idealize` fits the traces of a project that exists, `tether map create` writes a
channel map file for later extractions, `tether info` only reads projects and prints a
summary, `tether status` only reads a project and prints which steps are complete, which
are stale and what to do next, `tether metadata` shows or gives the frame time, pixel size
and channel roles of a project's movies, and `tether export` reads projects and writes the
molecule table, the trace text file or a subset project.

**A new movie starts here, for now.** The desktop application does not extract a movie
yet. It opens a `.tether` project, imports a Deep-LASI acquisition, and is where you curate
traces by hand, pick states by eye and read plots, but only `tether extract` and
`tether batch` make a `.tether` project from a raw movie. So, for a fresh acquisition, run
one of them first, then open its output in the application; this page tells you how to
start the application too. The extraction section of the application, which takes a movie
to a `.tether` project with no terminal, is planned work: see
[its milestone on GitHub](https://github.com/bioedca/tether/milestone/17).

Everything below was transcribed from the live argparse parser in `src/tether/cli.py` —
the same parser `--help` prints from — and a guard test ([`tests/test_cli_reference.py`](https://github.com/bioedca/tether/blob/main/tests/test_cli_reference.py))
fails if that parser gains, loses or renames an option without a matching edit here. Where
a default is quoted, it is the string argparse prints, not a value read out of the source.

The flags on this page are only the part of the pipeline the command line exposes. Most of
what Tether applies to your data — the correction, idealization, analysis and rendering
defaults — has no CLI flag and is reached from Python or the desktop application. Those are
tabled in [Analysis parameters](reference/parameters.md), which also records, for each one,
whether the value you used is written into the `.tether` project.

## Launching Tether after an installer install

The installer deliberately does **not** edit your `PATH` (see
[ADR-0051](adr/0051-installed-app-launch-surface.md)), so out of the box neither command
is a bare word you can type. It writes both launchers into one directory — `bin/` under
the install root you chose during installation:

| Platform | Command-line launcher | Application-window launcher |
|---|---|---|
| Windows | `<install-root>\bin\tether.bat`, or `tether` in Start Menu → **Tether Command Prompt** from the next release | Start Menu → **Tether**, or `<install-root>\bin\tether-gui.bat` in a terminal |
| Linux | `<install-root>/bin/tether` | Applications menu → **Tether**, or `<install-root>/bin/tether-gui` |
| macOS | `<install-root>/bin/tether` | `<install-root>/bin/tether-gui` |

From the next release, the two launchers are in `launchers/` (`launchers\` on Windows) under
the install root instead, a folder that holds nothing else
([#492](https://github.com/bioedca/tether/issues/492)): read `launchers` for `bin` in the
table.

On Windows, from the next release, Start Menu → **Tether Command Prompt** opens a console
that needs no `PATH` change ([#666](https://github.com/bioedca/tether/issues/666)). In it, you
can type `tether` by name. An example written for one shell, such as the Bash or PowerShell
batch example, still needs that shell. On any system, add that one directory to the **end**
of your `PATH`, and `tether` works by name in any terminal.
[Install Tether, step 4](install.md#optional-put-the-launcher-folder-on-your-path)
gives the line for each system. It also tells why the folder goes at the end: in rc3, on Linux
and macOS, the `bin` folder also holds the installer's own `python`, `pip` and `conda`, and
these must not hide your own.

Without either, substitute the full launcher path from the table for `tether` in
every command below; nothing else changes. Verify the install with:

```console
$ tether --version
tether <version>
```

`--version` is the one flag that never touches your data, so it is the right smoke test.
The version string is generated from git at build time, so an installer or CI build — built
from the git checkout — prints a version that identifies the build you installed rather than
a fixed number. Two other outputs are not signs of a broken install: running straight out of
a source tree that was never built prints `tether 0.0.0+unknown`, meaning no build-time
version file was generated; and a build made from a tree with no git metadata, such as a
downloaded source zip, falls back to the fixed `tether 0.0.0`.

Running `tether` with no subcommand prints the top-level help and exits 0.

## `tether extract`

Run the native extraction pipeline — split → detect → register → colocalize → integrate —
on one dual-channel TIFF movie and write a new `.tether` project.

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
               [--map PATH] [--tdat PATH] [--profile PATH]
               movie
```

| Argument | Default | Meaning |
|---|---|---|
| `movie` | *(required)* | path to the dual-channel TIFF movie |
| `-o OUTPUT`, `--output OUTPUT` | *(required)* | path to the `.tether` project to create |
| `--overwrite` | off | overwrite an existing output project; Tether holds the project's writer lock for the run, so it refuses a project that another writer has open (not another user who opened it through a different hard-link name) |
| `--donor-side {left,right}` | `left` | which horizontal half is the donor channel |
| `--detection-mode {wavelet,intensity,bandpass,zmw}` | `wavelet` | particle-detection method (Deep-LASI `findPart` mode). `intensity`/`bandpass`/`zmw` also honor `--detection-threshold`. Mutually exclusive with `--tdat`, which supplies the mode |
| `--detection-threshold FRAC` | each mode's own — intensity `0.5`, bandpass `0.98`, zmw `0.5` | detection threshold as a fraction of the detection-image max, in `[0, 1)` (intensity/bandpass/zmw modes only; ignored by wavelet). Mutually exclusive with `--tdat` |
| `--window WINDOW` | `21` | aperture / crop-box side length in px, odd |
| `--min-separation MIN_SEPARATION` | unset uses each detection mode's faithful default (wavelet 8, intensity/bandpass/zmw 3) | minimum spot separation in px |
| `--detection-block DETECTION_BLOCK` | `50` | moving-average block size for the detection image |
| `--prealign {translation,similarity}` | `translation` | registration prealign degrees of freedom |
| `--pair-tol PAIR_TOL` | `2` | control-point pairing tolerance in px |
| `--coloc-distance COLOC_DISTANCE` | `3` | acceptor colocalization distance in px |
| `--colocalization {partner-in-both,donor-anchored}` | `partner-in-both` | which molecules to keep: `partner-in-both` keeps a donor spot only when an acceptor spot was detected at its mapped position (Deep-LASI's rule); `donor-anchored` keeps every donor spot, for weak-acceptor and low-FRET samples ([see below](#choose-the-colocalization-rule)) |
| `--count-only` | off | run detection, registration and colocalization, print the molecule count of each colocalization rule, and write nothing |
| `--rms-gate RMS_GATE` | `0.5` | registration RMS-residual gate in px |
| `--tmap PATH` | none | apply an imported Deep-LASI `.tmap` instead of a native fit; splits at the `.tmap`'s own channel geometry (`--donor-side` is then ignored). Cannot be combined with `--map` |
| `--map PATH` | none | apply a saved Tether `.npz` map file (from [`tether map create`](#tether-map-create)) instead of a native fit. It splits at the map's own channel geometry (`--donor-side` is then ignored). Cannot be combined with `--tmap`. See [Registration: one map source](#registration-one-map-source) |
| `--tdat PATH` | none | auto-apply the particle-detection mode decoded from a Deep-LASI `.tdat` (`temp/ParticleDetectionMode`), so extraction matches the method the movie was detected with. It sets `detection_mode` and `detection_threshold` and no other option, and it is refused with `--detection-mode`/`--detection-threshold` or with a `--profile` that sets either ([the rule](#detection-settings-pick-one-source)) |
| `--profile PATH` | none | read every extraction option from an [extraction profile](#extraction-profiles). An option the file leaves out keeps its default. It cannot be combined with an option flag (`--donor-side` to `--rms-gate`) |

### Get `--donor-side` right, and read the donor-side check

> **This is the one flag that can silently invert your results.** `left` and `right` name
> which *horizontal half* of the dual-channel frame carries donor emission. The two halves
> are simply swapped, so a backwards value produces a project in which every FRET
> efficiency is inverted — and it raises no error, because both values are perfectly
> legal. A value that is neither, such as `--donor-side sideways`, is a usage error:
> argparse prints the usage line and `invalid choice: 'sideways' (choose from left, right)`
> and exits with code 2 before any file is opened. A valid-but-wrong value is not an error,
> so the extraction itself cannot refuse it.
>
> **The donor-side check.** After every extraction Tether reads the side back out of the
> traces ([ADR-0070](adr/0070-advisory-check-for-swapped-donor-and-acceptor-halves.md)).
> When the acceptor bleaches first the acceptor drops to background and the donor *rises*;
> when the donor bleaches first both drop. A bleach event is a molecule whose dyes bleach
> one after the other, with a clear drop in one channel and a clear rise in the other at the
> first bleach. If the channel labeled donor shows the acceptor's drop in at least 80 % of
> at least 15 bleach events, the command warns on standard error, names the evidence and
> gives the remedy:
>
> ```text
> Extracted 27 molecule(s) -> wrong.tether
>   warning: 27 of 27 bleach events are consistent with swapped donor and acceptor halves; extract this movie again with --donor-side left.
> ```
>
> Otherwise it prints one `donor side:` line on standard output — for example
> `donor side: 32 of 32 bleach events agree with the donor side as extracted.` The check
> is **advisory**: it never changes the project and never changes the exit code. Its result
> and thresholds are stored as `donor_side_check` in `/settings/extraction`, and
> `tether batch` lists the flagged movies in its summary.
>
> **It cannot always decide.** With fewer than 15 bleach events it says `not enough bleach
> events to check the donor side`. Expect that with low FRET (below about E = 0.2), with no
> acceptor, with short movies, and with most movies of fewer than about 35 molecules. A
> verdict of "not a clear majority either way" means the votes split. In both cases, and
> whenever the result matters, check by eye.
>
> **How to check by eye (the fallback).** Look at a molecule whose acceptor photobleaches
> mid-trace. When the acceptor dies the donor stops transferring energy, so the *donor*
> trace steps **up** at that moment. If instead the trace you have labeled "donor" steps
> *down* while the other steps up, your halves are swapped — re-extract with the other
> value.
>
> **How to audit it afterward.** Every `ExtractOptions` field is written verbatim into
> `/settings/extraction` in the project file, so for a **natively registered** project the
> `donor_side` recorded there is the one that was actually applied — recoverable from any
> `.tether` without re-running anything.
>
> `--tmap` overrides this entirely: an imported Deep-LASI map carries its own channel
> geometry, and `--donor-side` is ignored when one is supplied. Note that it is still
> recorded, so on a `--tmap` run `/settings/extraction` preserves the value you *requested*
> and not the effective geometry. Audit the imported map itself in that case; the map's
> filename is recorded alongside it as `tmap_source`. The donor-side check still runs on a
> `--tmap` run, and its warning then points at the map's channel order instead of at
> `--donor-side`.

`--detection-mode` and `--prealign` are refused in the same way: an unrecognized value
gives the usage line, an `invalid choice` message that names the allowed values, and exit
code 2. The three flags take their allowed values from the same constants that
`ExtractOptions` checks (`tether.extract_choices`), so the command line and the library
accept the same values. `--policy` on `tether batch` and `--intensity-quantity` on
`tether export` also have an argparse `choices` list.

### Detection settings: pick one source

`--tdat` and `--detection-mode`/`--detection-threshold` are two ways to set the same
thing, so combining them is refused up front — before any movie is read — rather than
letting one silently win:

```console
$ tether extract movie.tif -o movie.tether --tdat run.tdat --detection-mode wavelet
tether extract: --detection-mode/--detection-threshold cannot be combined with --tdat (the .tdat supplies the detection mode); pass one or the other
```

The rule for `--tdat`, in `tether extract` and `tether batch` alike:

- **A `.tdat` sets `detection_mode` and `detection_threshold`, and no other option.** Every
  other option, from a flag, a profile or its default, keeps its value. A `.tdat` that
  holds no threshold keeps the threshold unset, so each detection mode uses its own.
- **It is refused beside a choice of either field**: a `--detection-mode` or
  `--detection-threshold` flag, or an [extraction profile](#extraction-profiles) in which
  either is not `null`. The refusal comes before any movie is read.
- **The values it applied are recorded.** `/settings/extraction` stores the
  `detection_mode` and `detection_threshold` that ran, and the `.tdat` filename as
  `tdat_source`.

### Registration: one map source

Each run takes its registration map from exactly one source, and this is the order in
which they apply:

1. **Native fit** — the default. Control points are paired from the movie's own
   detections and a degree-2 map is fitted, with `--donor-side` naming the donor half.
2. **`--tmap PATH`** — a Deep-LASI `.tmap` replaces the native fit.
3. **`--map PATH`** — a saved Tether `.npz` map file replaces the native fit. It is
   applied as it was saved: its coefficients, its channel geometry, its RMS residual and
   its low-confidence verdict are not changed. The run records the map's own RMS gate,
   and `--rms-gate` does not change it.

A map file or a `.tmap` replaces the native fit, so `--donor-side` is ignored. The two
files are never combined: neither can be said to win, so passing both is refused before
any movie is read. `--tdat` is a separate rule: it sets **detection**, not registration,
so it composes with any of the three, and it cannot be combined with
`--detection-mode`/`--detection-threshold` (above).

```console
$ tether extract movie.tif -o movie.tether --tmap run.tmap --map beads.npz
tether extract: --tmap and --map cannot be combined (each one supplies the registration map); pass one or the other
```

The project records which source produced the map. In `/settings/extraction`,
`registration_source` is `native`, `imported` (a `.tmap`) or `map-file`, and
`tmap_source` or `map_source` holds the file name. The `/calibration` group of a
map-file run also stores a `map_file` record (the name and the sha256 of the file) in
its provenance, beside the map's own origin.

### Extraction profiles

An *extraction profile* is a JSON file that holds every extraction option, so one
file describes how a movie was extracted. `tether extract --profile` and
`tether batch --profile` read one. Its form is the form of a
[service-command request](#service-commands), with `"extraction-profile"` in place of the
command name:

```json
{"command":"extraction-profile","fields":{"disk_radius":2.5,"ring_inner":5.0,
"window":19},"version":1}
```

- **A field you leave out keeps its default**, so a profile you write by hand names only
  the options it changes. The fields are the option names with `_` for `-`, plus the seven
  options that have no flag: `prealign_upsample`, `prealign_low_sigma`,
  `prealign_high_sigma`, `disk_radius`, `ring_inner`, `ring_outer` and `bg_window`.
  [Analysis parameters](reference/parameters.md) gives each default and what it does.
- **The channel layout is four fields.** `channel_layout` is the kind: `side-by-side`,
  `top-bottom` or `rectangles`, or `null` for the split that `donor_side` describes.
  `channel_layout_donor` is the donor half of a half layout (`left` or `right`, `top` or
  `bottom`, and `null` takes the first). `channel_layout_donor_rect` and
  `channel_layout_acceptor_rect` are the two rectangles of a `rectangles` layout, each
  `[y1, x1, y2, x2]` with 1-based inclusive bounds. A layout is refused beside a
  `donor_side` other than `left` and beside `--tmap` or `--map`. For example, a camera that puts the
  donor in the bottom half:

  ```json
  {"command":"extraction-profile","fields":{"channel_layout":"top-bottom",
  "channel_layout_donor":"bottom"},"version":1}
  ```

- **It is checked before any movie is read.** An unknown field, a value of the wrong type
  and a value extraction refuses (an even `window`, a `ring_outer` that does not fit in the
  window) stop the command with a message that names the file.
- **It replaces the option flags.** `--profile` with any of `--donor-side` to `--rms-gate`
  is refused, even when the flag repeats the profile's value: change the value in the
  file. `--tdat` may be combined with a profile whose `detection_mode` and
  `detection_threshold` are `null` ([the rule above](#detection-settings-pick-one-source)).
- **The values that ran are recorded.** Every option is written into `/settings/extraction`
  as before. The profile's filename is not.

A profile is a file you name, not a setting of the application, so it can be kept with the
data and used on another computer
([ADR-0129](adr/0129-an-extraction-profile-is-a-file-in-the-request-text-form.md)). From
Python, `tether.services.write_profile` writes one and
`tether.services.ExtractionProfile.from_options` makes one from an `ExtractOptions`.

### Choose the colocalization rule

A molecule starts as a donor spot. By default (`--colocalization partner-in-both`) Tether
keeps it only when an acceptor spot was also detected within `--coloc-distance` of its
mapped position, which is the rule Deep-LASI uses. That rule **drops the low-FRET and
acceptor-dark molecules**: their acceptor is too weak to be detected as a spot of its own.
If your sample has a weak acceptor, or you expect a low-FRET population, extract with
`--colocalization donor-anchored`, which keeps every donor spot whose aperture fits in the
frame. Under both rules the acceptor intensity is read at the mapped donor position.

To see what each rule keeps before you commit to a run, add `--count-only`. It writes
nothing (`-o` is still required):

```console
$ tether extract movie.tif -o movie.tether --count-only
Molecules in movie.tif under each colocalization rule (nothing written):
  partner-in-both: 412 (selected)
  donor-anchored: 455
```

A large gap between the two counts means many donor spots have no detected acceptor. The
rule is recorded with each movie in the project, and every molecule records whether an
acceptor spot was detected for it (`/molecules/acceptor_detected`). A project extracted
before the rule could be chosen used the donor-anchored rule, and it reads that way.
Extracting the movie again uses the new default unless you name `donor-anchored`.
`tether batch --colocalization` sets the rule for every movie in a batch. In an
[extraction profile](#extraction-profiles) the rule is the `colocalization` field, and
`--colocalization` is then refused beside `--profile` like the other option flags.

### Example — one movie to one project

```bash
tether extract movie.tif -o movie.tether
```

```console
Extracted 412 molecule(s) -> movie.tether
  colocalization: partner-in-both kept 412 (donor-anchored would keep 455)
  donor side: 38 of 40 bleach events agree with the donor side as extracted.
```

The second line names the [colocalization rule](#choose-the-colocalization-rule) and what
the other rule would have kept. The third is the
[donor-side check](#get-donor-side-right-and-read-the-donor-side-check).
If the registration RMS residual exceeds `--rms-gate`, extraction still succeeds and
still exits 0, but a warning goes to stderr and the affected molecules are tagged
`low-confidence-registration` in the project — a flag you can filter on later rather
than a silent pass.

## `tether reextract`

Extract a movie that a project holds again, as a new *trace generation*
([ADR-0136](adr/0136-a-new-extraction-of-a-movie-is-a-new-trace-generation.md)). Use it
when the map, the channel geometry or an extraction option of a movie you have curated
changes. The earlier extraction is never overwritten: it stays readable in the project, labels
and all.

```text
tether reextract [-h] [--map PATH] [--tmap PATH] [--tdat PATH] [--profile PATH]
                 [--match-px PX]
                 project movie
```

| Argument | Default | Meaning |
|---|---|---|
| `project` | *(required)* | the `.tether` project that holds the movie |
| `movie` | *(required)* | the movie file, with the bytes the project recorded |
| `--map PATH` | none | apply a saved Tether `.npz` map file instead of a native fit, never with `--tmap` |
| `--tmap PATH` | none | apply an imported Deep-LASI `.tmap` instead of a native fit |
| `--tdat PATH` | none | apply the particle-detection mode of a Deep-LASI `.tdat`, as for `tether extract` |
| `--profile PATH` | none | read every extraction option from an [extraction profile](#extraction-profiles). Without one, each option has the default of `tether extract` |
| `--match-px PX` | `None` | the "did not move" distance in pixels. `None` is 0.5 px (`GENERATION_MATCH_PX`) |

**What moves to the new generation.** A molecule of the new extraction within `--match-px`
of a molecule of the previous one (each the other's closest) *did not move*. It keeps that
molecule's key, and its label, category and analysis window move to it with the mark
**carried**. A carried label does not train the ranker until a person confirms it in the
application (Curation → Reconcile trace generation…), or accepts or rejects the molecule
again. Every fit and correction of the movie becomes stale. The aperture options of the
profile (`disk_radius`, `ring_inner`, `ring_outer`, `bg_window`) must equal the project's.

The command holds the project's writer lock for the whole run, so it refuses a project
that the application or another command has open for writing. It prints the counts of
the three lists that the reconciliation page shows:

```console
$ tether reextract experiment.tether movie_010.tif --map beads.npz
Extracted 214 molecule(s) as generation 2 of movie movie_010 -> experiment.tether
  198 carried (198 waiting for confirmation), 16 new, 9 lost. The fits and corrections of the movie are stale
```

A **new** molecule has no partner in the previous generation, so it has no labels. A
**lost** molecule of the previous generation has no partner in the new one. It and its
labels stay readable in the previous generation. `tether status` prints the same counts.
Exit codes: `0` the new generation was written, `1` the run failed and the project is
unchanged (for example, the movie is not in the project, or the project is locked), `2`
bad command-line arguments. A recipe step runs the same command:
`{"command": "reextract", "fields": {"project": ..., "movie": ...}}`.

## `tether batch`

Process many movies unattended (FR-BATCH). Give it a folder of movies, a pattern or the
movie files themselves. With `--out-dir`, each movie becomes its own
`<out-dir>/<stem>.tether`. With `--into PROJECT.tether`, every movie goes into that one
project, which can hold several movies of one experiment
([`.tether` project](glossary.md#tether-project), [see
below](#every-movie-into-one-project-into)). Give one of the two. Failures are isolated — one bad movie
does not stop the run — and each stage is checkpointed, so re-running the same command
resumes only the incomplete stages instead of redoing finished work.

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
| `movies` | *(required, one or more)* | dual-channel TIFF movies: each one a movie file, a folder or a quoted pattern ([see below](#which-movies-a-folder-or-a-pattern-gives)) |
| `--recursive` | off | a folder argument also includes the movies in its sub-folders |
| `--dry-run` | off | print each movie, its output project and what the run would do with it, then exit `0`. Nothing is written, and no movie is read except to hash it under `--into` ([see below](#check-a-batch-before-it-runs-dry-run)) |
| `-d OUT_DIR`, `--out-dir OUT_DIR` | *(this or `--into`)* | directory for the per-movie `.tether` projects (created if absent) |
| `--into PROJECT.tether` | *(this or `--out-dir`)* | append every movie to this one project, which the first movie creates if it is absent. A movie already in it is skipped, and correct and idealize run once over the whole project ([see below](#every-movie-into-one-project-into)) |
| `--tmap PATH` | none | a shared imported Deep-LASI `.tmap` applied to every movie |
| `--tdat PATH` | none | a shared Deep-LASI `.tdat` detection config applied to every movie. It sets `detection_mode` and `detection_threshold` and no other option, and it is refused with a `--profile` that sets either ([the rule](#detection-settings-pick-one-source)) |
| `--profile PATH` | none | extract every movie with the options of an [extraction profile](#extraction-profiles). An option the file leaves out keeps its default, and without a profile every option but `--colocalization` is at its default. It cannot be combined with `--colocalization` |
| `--policy {warn,fail}` | `warn` | over-gate registration policy: `warn` keeps an over-gate movie with a flag; `fail` records that movie's extract stage as failed — the inspectable project file is still written, and the saved residual is re-evaluated on resume ([see below](#how-policy-fail-behaves-on-resume)) |
| `--colocalization {partner-in-both,donor-anchored}` | `partner-in-both` | the colocalization rule of every movie's extraction; see [`tether extract`](#choose-the-colocalization-rule) |
| `--no-idealize` | off | skip the idealization stage (extract + correct only) |
| `--overwrite` | off | re-extract an incomplete output, or a completed extraction that the current `fail` policy rejects; accepted completed checkpoints still skip. Not with `--into` |
| `--log PATH` | `<out-dir>/batch-log.jsonl`, or `<project>-batch-log.jsonl` beside the `--into` project | write a JSONL structured log here |
| `--max-restarts N` | `3` | auto-restart a movie's idealization up to N times on a transient sidecar failure (crash/timeout). `0` disables restarts |
| `--sidecar-timeout SECONDS` | `1800` | per-idealization-call sidecar timeout in seconds |
| `--sidecar-python PATH` | `$TETHER_SIDECAR_PYTHON` | sidecar interpreter for idealization |
| `--no-defer` | off | do not defer idealization when the sidecar is unavailable at startup; let each movie's idealize stage fail in isolation instead |
| `--quiet` | off | print no progress lines on stderr (the summary on stdout is unchanged, [see below](#progress-while-the-batch-runs)) |
| `--verbose` | off | also print every stage event on stderr, as the structured log records it (not with `--quiet`) |

Idealization runs in the isolated tMAVEN sidecar, not in the application environment. If
`--sidecar-python` is not given, Tether uses `$TETHER_SIDECAR_PYTHON`; an installed build
also falls back to the `envs/sidecar` interpreter shipped beside the application, so a
normal install needs neither. When no sidecar can be found at startup the run *defers*
idealization — extraction and correction still happen for every movie — unless you pass
`--no-defer`, which instead lets each movie's idealize stage fail on its own.

A profile changes how a movie is extracted, not which stages count as done: a movie whose
extraction finished in an earlier run is not extracted again with a new profile, not even
with `--overwrite`. Use a new `--out-dir` to extract a set of movies with a changed
profile.

### Stage status in the batch summary

`tether batch` prints one status for each stage of each movie (`tether.project.batch`):

| Status | Meaning | Counts as failure? |
|---|---|---|
| `done` | the stage ran and succeeded | no |
| `skipped` | a checkpoint found the stage already complete | no |
| `failed` | the stage raised (or an over-gate movie under `--policy fail`) | **yes** |
| `blocked` | an upstream stage failed | no |
| `deferred` | the sidecar was unavailable at startup | no |
| `not-requested` | `--no-idealize` | no |
| `warning` | non-fatal, for example provenance stamping failed | no |

Only `failed` makes the run exit `1`. A `deferred` stage in a run with no `failed` stage
makes it exit `3`. See
[a deferred idealization exits 3](troubleshooting.md#the-batch-finished-ok-but-nothing-was-idealized).

`blocked` is the one row that never shows up on an exit-`0` run in practice. A stage is
blocked only when its upstream stage was not `done`/`skipped`, so a `blocked` always travels
with a `failed` in the same movie and the run exits `1` anyway.

### Which movies a folder or a pattern gives

Each `movies` argument is one of three things, and Tether expands it itself, so the same
command gives the same movies in PowerShell, cmd and bash:

- **A folder** gives the movie files directly inside it. A movie file has an extension that
  extraction reads, in any case. Today these are `.tif` and `.tiff`. With `--recursive` it also
  gives the movies in its sub-folders. Tether opens `.czi` and `.nd2` movies too, but extraction
  does not read them yet (#1390, #1388), so a folder or a pattern leaves them out. A `.czi` or
  `.nd2` file that you name on its own fails its extraction stage with a message that says so
  ([Movie containers](reference/movie-containers.md)).
- **A pattern** — an argument with `*`, `?` or `[...]` in it that is not an existing path —
  gives the movie files it matches. `**` matches any number of folders. A folder that the
  pattern matches is expanded as a folder argument. Put a pattern in quotes, so that bash
  passes it to Tether as it is.
- **A file** is used as it is, whatever its extension.

Names that start with `.` are left out of folders and patterns. macOS writes `._name.tif`
files beside movies on some drives. The movies of all the arguments are put in one list,
sorted by path, and a movie that two arguments give is processed once. An argument that
gives no movie stops the run before anything is written, and the message names it:

```console
$ tether batch data/day4 -d out
tether batch: folder 'data/day4' holds no movie file (.tif, .tiff); add --recursive to include its sub-folders
```

A folder that Tether cannot list stops the run in the same way. This includes each
sub-folder that `--recursive` or a pattern looks in, so a batch never runs with one of them
left out.

### Output names when two movies have the same file name

Acquisition software often numbers the files the same way in each day's folder, so
`day1/movie_010.tif` and `day2/movie_010.tif` are common. Two movies with one output name
would share one project, and the checkpoint would treat the second as already done with
the first one's data. So the output name follows one rule:

- An output is named after the movie's file name without its extension, as `movie_010`.
- While two names are the same, each of those movies puts its next parent folder in
  front, joined with `__`: `day1__movie_010` and `day2__movie_010`. A movie whose name is
  not shared keeps its own name, so a batch without equal file names writes the same
  names as before.
- Names are compared without regard to case on every operating system, because
  `Movie_010.tether` and `movie_010.tether` are one file on Windows and macOS.

The folders are those of the movie's full path, so the names do not depend on the folder
you run the command from. The folders are read as you typed them, with one exception:
after a link, `..` goes to the parent of the link's target. Say `work/link` is a link to
`archive/sub`. Then `work/link/../m.tif` is the file `archive/m.tif`. Its folders are read
from where it really is, so its name is `archive__m`. Windows reads `..` before it follows
a link, so there the path is `work/m.tif` and the batch holds that movie once.

If no folder tells two movies apart — `A.tif` and `a.tif` in one folder — the run stops before anything is written. `--dry-run` shows each name before
the run, and the structured log records each movie and its output as an `input` event
with status `mapped`.

An output name depends on the whole list of movies: if you add `day3/movie_010.tif` to a
batch that had only `day1/movie_010.tif`, the day 1 movie's output becomes
`day1__movie_010` and the old `movie_010.tether` is not resumed. Run the batch with
`--dry-run` first when you change what it holds.

### Every movie into one project (`--into`)

`--into PROJECT.tether` writes the movies of one experiment into one project, so the
conditions, the population views and the ranker see them together:

```bash
tether batch data --recursive --into experiment.tether
```

- **Each movie is appended.** The first movie creates the project when it does not exist.
  Each later one is appended under the project's writer lock, so a failed append leaves the
  project as it was and the run goes on with the next movie. Every movie must use the
  window, radii and background window of the project's first movie. A movie extracted
  with other values fails.
- **A movie already in the project is skipped.** A movie is known by its content, not its
  name. So the same command run again appends no movie twice. Two copies of one file go in
  once. Tether hashes a movie to check this only when the project holds a movie of
  the same file size.
- **Correct and idealize run once, over the whole project,** after the last append. The
  dataset α and γ then come from every movie's molecules. A run that appends a movie runs
  both again, over every movie in the project. The idealization model is then fitted
  again in place of the stored one. A run that appends nothing skips them. The exceptions
  are an earlier `--into` run that left them unfinished, for example with idealization
  deferred, and a movie appended since from Python with
  `tether.project.extract.add_movie_to_project`. Every append marks both stages
  unfinished when it adds the movie. A project that has gained no movie keeps its correction and
  model.
- **The summary still has one line for each movie.** A movie's name is the one
  [the output name rule](#output-names-when-two-movies-have-the-same-file-name) would give
  its own project. So movies with one file name stay apart. Each movie in the project shows
  the project's correct and idealize results. A movie that is not in it has them `blocked`.
  The progress lines and the structured log give those two stages once, as `tether batch:`
  run lines and `(batch)` events.
- **`--policy fail` keeps an over-gate movie out.** Its extract stage is `failed` and
  nothing of it is written, so it is not in the project. A later run tries it again.
- `--into` with `--out-dir` is a usage error (exit `2`). So is `--into` with
  `--overwrite`, because a movie in the project is skipped, never replaced.

`--dry-run --into` prints one line for each movie. The line names the project and says
whether the movie is new to it. A `Project:` line for correct and idealize comes last:

```text
Batch plan (dry run): 3 movie(s) into the one project experiment.tether; nothing was written (a movie is read only to hash it, when the project or an earlier movie of the run has its size)
  data/day1/movie_010.tif -> experiment.tether  [already in the project as movie 'a3f1c2d4e5b6': will be skipped]
  data/day1/movie_011.tif -> experiment.tether  [will be appended]
  data/day2/movie_010.tif -> experiment.tether  [will be appended]
Project: correct and idealize will run over every movie in the project after the appends
```

A project that does not exist yet shows `new project: will be created with this movie` on
the first movie's line. A file that holds the same movie as an earlier one of the run shows
`same content as <name>: will be skipped`, since the run skips it once the earlier one is in.

### Check a batch before it runs (`--dry-run`)

`--dry-run` prints the plan and exits `0`. It reads no movie, creates no folder and writes
no file ([with `--into`](#every-movie-into-one-project-into) it may read a movie to hash
it). It prints one line for each movie with its output project and what the run would
do with it, then the settings, the log path and whether a sidecar interpreter was found:

```text
Batch plan (dry run): 3 movie(s) into projects; no movie was read and nothing was written
  data/day1/movie_010.tif -> projects/day1__movie_010.tether  [new]
  data/day1/movie_011.tif -> projects/movie_011.tether  [exists, complete: will be skipped]
  data/day2/movie_010.tif -> projects/day2__movie_010.tether  [exists: will resume at idealize]
Settings: policy=warn, idealize=on, overwrite=off, recursive=on, tmap=none, tdat=none, channel-layout=none, donor-side=left, detection-mode=wavelet, detection-threshold=mode default, max-restarts=3, sidecar-timeout=1800.0s, defer=on
Structured log: projects/batch-log.jsonl
Sidecar: found at /opt/tether/envs/sidecar/bin/python (the run checks that it starts before the first movie)
```

The settings also name how each movie would be extracted. `channel-layout` gives the
split. Without a layout, `donor-side` follows it. A half layout adds its `donor` half, and a
`rectangles` layout adds `donor-rect` and `acceptor-rect`. With `--tmap` the split reads
`from .tmap`, since the `.tmap` holds its own crops.

Then come `detection-mode` and `detection-threshold`. The threshold reads `mode default`
when the profile leaves it `null`. Both read `from .tdat` when `--tdat` supplies them.

What a line says comes from the output project's checkpoint, which is opened read-only:

| The line says | The output project |
|---|---|
| `new` | does not exist yet |
| `exists, complete: will be skipped` | holds every stage the run asks for |
| `exists: will resume at` a stage | holds some stages, and the run does the others |
| `exists, incomplete:` | holds no finished extraction, and only `--overwrite` replaces it |
| `exists, over the gate:` | holds an extraction that `--policy fail` rejects |

A refusal to start is reported as it would be by the run, with exit code `2`. A movie
argument that gives no movie is one. A profile `channel_layout` beside `--tmap` is another,
because the `.tmap` splits each frame at its own crops. The dry run only looks for the sidecar
interpreter. The real run also starts it once before the first movie.

### Progress while the batch runs

A batch over a condition can run for hours, so `tether batch` prints progress to
**stderr** while it works. One line states the setup before the first movie is read. It
gives the number of movies, the output folder, the policy and whether idealization was
asked for. Check that line first: a wrong option shows there, not hours later in the
summary. Then each movie gets a start line and an end line. The end line gives its
position, its name, the result, the time it took and the status of every stage:

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

A line about the whole run, such as the idealization deferred at startup above, starts
with `tether batch:`. The end line names a flagged stage after `flagged:`. A stage is
flagged when it kept the movie with a flag (an over-gate registration under
`--policy warn`) or raised a warning (the [donor-side
check](#the-donor-side-check-in-the-batch-summary)).

There are three levels:

| Level | stderr while the run works |
|---|---|
| `--quiet` | no progress line |
| default | the setup line, the run-level lines, and a start and an end line for each movie |
| `--verbose` | the default lines, and also one line for each stage event, such as `[1/3] cond_a_010 extract: done - 3 molecule(s)` |

The lines come from the same events as the structured log, so the two cannot disagree. They
are plain text with no cursor control, so a redirected stderr or a scheduler's log file
reads the same as a terminal. **stdout does not change at any level**: it holds the
end-of-run summary and its `Exit code` line only, so a script that reads stdout is not
affected. Two lines on stderr do not
depend on the level. One is the `structured log:` path at the end. The other is a failed
stage, which Python's logging prints as `movie · stage: failed — reason`.

### How policy `fail` behaves on resume

`fail` is a verdict recorded about a movie, not a veto on its output. Extraction writes
the `.tether` first and the gate is applied afterwards, to the summary that write
returns — so an over-gate movie under `--policy fail` leaves a **complete, checkpointed**
project in `<out-dir>`, with its extract stage recorded `failed` and correct/idealize
`blocked`. That run exits `1`, as documented.

On a later run with `--policy fail`, Tether re-reads the saved
`registration_rms_px` and effective `rms_gate` before accepting that extraction
checkpoint. If the residual is still over the saved gate, extraction remains `failed`,
the downstream stages remain `blocked`, and the command exits `1` again. The project
stays available for inspection.

Switching that rerun to `--policy warn` accepts the flagged extraction checkpoint, so
extraction is `skipped` and the incomplete downstream stages may continue.
`--overwrite --policy fail` instead re-extracts only a rejected checkpoint and applies
the gate to the new result. An already completed, in-gate extraction remains
checkpointed and is still skipped. Batch claims every `--overwrite --policy fail`
destination lock before testing whether its project file exists or reading a checkpoint;
any existing writer lock, including one held by another thread in this process, fails
that movie as locked without refreshing or releasing the caller's lock. A process-local
per-destination reservation also prevents another local thread from refreshing that
same-identity lock file or writing through it. Both claims remain held while batch creates,
skips, or re-extracts the destination.

### The donor-side check in the batch summary

`tether batch` has no `--donor-side` option, so every movie is extracted with the donor on
the left. After each extraction it reads the stored [donor-side
check](#get-donor-side-right-and-read-the-donor-side-check), also on a resumed run that skips
the extract stage. A movie whose halves look swapped is marked `halves-swapped?` and listed
in a footer; the check never changes a movie's status or the exit code:

```text
Batch run: 2 movie(s), 2 ok, 0 failed (policy=warn, idealize=off)
  ok    fine_012     extract=done correct=done idealize=not-requested
  ok    flipped_011  extract=done correct=done idealize=not-requested halves-swapped?
Donor and acceptor halves look swapped in 1 movie(s) (advisory; nothing was changed):
  flipped_011: 27 of 27 bleach events are consistent with swapped donor and acceptor halves; extract this movie again with --donor-side right
Exit code 0: every requested stage ran for every movie.
```

The structured log carries the same finding as a `donor-side-check` event with status
`warning`. A movie with too few bleach events to check, or with no clear majority, adds
nothing to the summary. To act on a flagged movie, extract it again with `tether extract`
and the remedy the message names.

### Example — a folder of movies, extraction and correction only

```bash
tether batch data -d projects --no-idealize
```

The same command works in PowerShell and cmd. To take the movies of several day folders,
give the parent folder with `--recursive`, or a quoted pattern, which also works in every
shell:

```bash
tether batch data --recursive -d projects --dry-run
tether batch "data/2026-11-*" -d projects --no-idealize
```

The end-of-run summary goes to stdout; the machine-readable JSONL log lands at
`projects/batch-log.jsonl` unless you moved it with `--log`. Re-run the identical command
later without `--no-idealize` and only the idealization stage executes — everything
already checkpointed is skipped.

## `tether map create`

Make a channel map file from a bead-slide video or from one bead image, so that later
movies can be extracted with it (`tether extract --map`) instead of each fitting its own
registration. The beads of both halves are detected, paired and fitted with the same
stages a native extraction runs, and the map is saved as one `.npz` file with its source
id (the sha256 of the bead file), its projection settings and its quality record. The
application's **Calibration → Make channel map…** (a menu of the Prepare section) runs the same command.

```text
tether map create [-h] -o OUTPUT [--overwrite] [--frames START STOP]
                  [--projection {max,mean}] [--donor-side {left,right}]
                  [--rms-gate RMS_GATE] source
```

| Argument | Default | Meaning |
|---|---|---|
| `source` | required | a bead TIFF: one image, or a video of frames |
| `-o`, `--output OUTPUT` | required | the `.npz` map file to write. The name must end in `.npz` |
| `--overwrite` | off | replace an existing map file (it is replaced only by a complete one). Tether holds the map file's writer lock for the replacement, so it refuses while another `tether map create` holds that lock |
| `--frames START STOP` | every frame | for a video: the 0-based half-open frame range to project |
| `--projection {max,mean}` | `max` | for a video: how the frames are reduced to one image |
| `--donor-side {left,right}` | `left` | which half of the frame is the donor channel |
| `--rms-gate RMS_GATE` | `0.5` | registration RMS-residual gate in px |

```console
$ tether map create beads.tif -o beads.npz
Map -> beads.npz: RMS 0.214 px over 38 bead pair(s), 71% of the field covered
$ tether extract movie.tif -o movie.tether --map beads.npz
```

A map over `--rms-gate` is still written and the command exits `0`, with a warning on
stderr: the map is flagged low-confidence, and every molecule extracted with it is tagged
`low-confidence-registration`. A bead file with too few beads in either half, a bad frame
range, an output that exists without `--overwrite`, an output whose writer lock another
writer holds, or an output name without `.npz` exits `1` with one line on stderr. Bad arguments exit `2`.

Without `--overwrite` the map is saved as a hard link to a finished temporary file, which
never replaces a file. On a file system without hard links (a FAT drive, some network
shares) the map is copied instead, under the map file's writer lock, so a copy that fails
removes only its own file. The writer lock, whenever it is taken, leaves a
`<file>.lock.guard` file beside the map, as it does beside a project.

The map file has a `format_version` (now `1`). Tether reads a map file of its own
version or older, and refuses one written by a newer version. The file holds only plain
arrays and text and is read with pickling switched off, so no stored Python object can
run when a map is loaded. [ADR-0128](adr/0128-the-map-file-carries-a-format-version-and-a-reader-refuses-a-newer-one.md)
records this version rule.

## `tether run`

Run a recipe file: a list of service requests, the same requests that `tether extract`,
`tether batch`, `tether idealize` and `tether map create` send
([service commands](#service-commands)). The application writes one with
**File → Save as recipe…**. The [recipe guide](how-to/run-a-recipe.md) shows a full one.

```text
tether run [-h] [--dry-run] [--resume] recipe
```

| Argument | Default | Meaning |
|---|---|---|
| `recipe` | required | the recipe file (`.json`) |
| `--dry-run` | off | print the steps, the inputs of each step and each approval step, and change nothing |
| `--resume` | off | skip the completed steps at the start of the latest run and continue from the first unfinished step |

Each step runs through its service command, in order, as a job stored in the project that
the step writes (the `output` of `extract`, the `project` of `reextract` and of `idealize`,
the `into` of `batch`). The job's one part names the recipe by the SHA-256 of its text and the step by
its number. Tether also records progress in its per-user state for the recipe's path and
contents. This includes successful steps that write no single project, such as `map` or
`batch` without `into`. The run stops at the first failed step.

A count-only extraction records progress in the recipe checkpoint and leaves the named
project untouched.

Running without `--resume` starts a new checkpoint. `--resume` retries the first unfinished
step and runs every step after it again, since those steps can depend on its results.
When no step is complete, as for a changed recipe, `--resume` starts from the first step.

The run stops when a step defers a stage. If the step has a job, Tether stores it as `failed`.
`--resume` runs that step again, so the command's own checkpoint can finish the deferred stage
before the next recipe step starts. Tether reads relative paths from the recipe file's folder.

A recipe is refused before anything runs when it is not valid: an unknown command, field
or version, a value of the wrong type, a policy with a missing value, a **publish step**
(a recipe can make and upload a deposit draft, but it can never publish, card B3), an
`approve` step (a recipe approves only with a policy), or a field that names an account
secret.

For recipe idealization, select the sidecar interpreter locally with `TETHER_SIDECAR_PYTHON`
or use the installed sidecar. A recipe cannot select the interpreter.

At a step that needs a person, a policy that the recipe names for the step decides
(ADR-0102). With no policy and no approval recorded, the run stops with exit code `4` and
prints the command that approves the step and the one that continues:

```text
tether run: stopped at approval step 'gamma.approve' for 'KJG_231027': the recipe has no policy for it.
  evidence: runs/gamma-KJG_231027.json
  approve:  tether approve exp.tether gamma.approve --subject KJG_231027 --report runs/gamma-KJG_231027.json
  continue: tether run recipe.json --resume
```

You can copy each command and run it as printed. A path or a subject that holds a space
is put in quotes: double quotes for the Tether Command Prompt on Windows, and shell quotes
on macOS and Linux, for example `--subject 'condition 1'`. The command that `tether run`
prints when a step fails is quoted in the same way. On Windows, the Tether Command Prompt
still reads a value that holds a `"` together with `&`, `|`, `<`, `>` or `^`, or that holds a
`%NAME%` of the environment, before Tether gets it. Type such a value by hand
([#1354](https://github.com/bioedca/tether/issues/1354)).

No command reaches an approval step yet: the map, correction and fit steps that reach
them come with #918, #930 and #939, so a recipe that names a policy today is refused
with the reason.

Exit codes: `0` every step ran (or `--dry-run` printed its plan), `1` a step failed, `2`
the recipe was refused and nothing ran, `3` no step failed but a stage was deferred (as
for [`tether batch`](#tether-batch)), `4` the run stopped at a step that needs a person.

## `tether approve`

Record that a person approved a step that needs a person, on the evidence of its report
([ADR-0102](adr/0102-a-batch-run-stops-at-an-approval-step-or-applies-a-recipe-policy.md)).

```text
tether approve [-h] --subject SUBJECT --report REPORT [--by NAME]
               project {map.accept,alpha.confirm,gamma.approve,fit.select}
```

| Argument | Default | Meaning |
|---|---|---|
| `project` | required | the `.tether` project to record the approval in |
| `step` | required | the approval step: `map.accept`, `alpha.confirm`, `gamma.approve` or `fit.select` |
| `--subject SUBJECT` | required | what is approved: the map, the condition or the analysis set |
| `--report REPORT` | required | the report that is the evidence: a `.json` file. Each PNG beside it whose name starts with its stem is recorded too |
| `--by NAME` | the curator name of new labels | the name of the person |

The approval is one row of `/settings/approvals`: the step and subject, `person` and the
name, the SHA-256 of each report file, and the time. When `tether run` stopped in this
project, the row also names that recipe, and `tether run RECIPE --resume` then goes on
past the step. Exit codes: `0` recorded, `1` the write failed (for example, the project is
locked), `2` refused before any write (an unknown step, an empty subject, a report that is
not a readable `.json` file, a project that cannot be opened).

## `tether info`

Print a summary of what one or more projects hold, without the application and without a
Python script: the movies, the molecule and curation counts, the corrections, the
idealization models and the lock holder. Use it to check the result of a batch run, or to
put a summary of a project in a support request.

```text
tether info [-h] [--json] [--stale] projects [projects ...]
```

| Argument | Default | Meaning |
|---|---|---|
| `projects` | *(required, one or more)* | one or more `.tether` projects |
| `--json` | off | print one JSON document instead of text; the layout is [below](#json-layout) |
| `--stale` | off | also count the stale molecules of each idealization model; this reads the trace arrays the models were fit on, so it is slower on a large project |

**It does not change the project.** The command opens the project read-only and takes no
lock, so it works on a project that another user holds, and it does not change the project
file or its `.lock` file. It reads only the tables and the attributes of the project. It
does not read the trace arrays (except for `--stale`) and it never opens a movie file, so it
is fast on a large project and it does not download a OneDrive placeholder movie.

For each project the text output gives:

- the schema version, and the Tether version that created the project;
- each movie: its file name, its frame count, its frame time, its molecule count and the
  curation counts of its molecules;
- each condition and its molecule count. A condition that the molecules name but that has no
  `/conditions` row yet shows `(not yet in /conditions)`;
- the molecule count and the curation counts (accepted, rejected, undecided) for all movies;
- the category counts, and how many molecules have no category;
- whether the correction pass ran, and how many molecules have each correction method
  (`none` is a molecule with no method);
- each idealization model: its method, its state count, its molecule count and, with
  `--stale`, its stale molecule count. Without `--stale` the text says `stale not checked`;
- the ranker model reference: the entries under `/models`, or `none`;
- the lock: `not held`, the holder (user, host, process id, the time it was taken, and
  `active` or `stale`), or `present but unreadable` when the `.lock` file cannot be parsed.
  A lock is stale after 30 minutes, the same limit the application uses.

### Example — check the projects of a batch run

```console
$ tether info out/movie_010.tether
out/movie_010.tether
  schema version 3, written by Tether <version>
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

Give several projects to summarize them in one run (on Windows PowerShell, list them, or use
`tether info (Get-ChildItem projects\*.tether)`, because `tether info` does not expand a pattern
itself).
A project that cannot be read gives one line on stderr, `tether info: <path>: <reason>`, and the
others are still printed. The reasons are: the file does not exist, it is not a `.tether`
project, or its schema version is newer than this version of Tether can read.

### JSON layout

`--json` prints one JSON document for all the projects given. The layout has a version,
`format_version`. A later version of Tether can add keys without a change to the version; it
changes the version if it renames or removes a key, or changes the meaning of a value. A
value that the project does not record is `null`. The keys are:

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

Print the readiness of each step of a project: which steps are complete, which are stale and
why, the jobs stored in the project, and the next step. The application reads the same
function to know where a project is, so the command line and the window agree on the next
step. Use it in a script to wait for a stage, or to see where to continue a project.

```text
tether status [-h] [--json] [--require STAGE] project
```

| Argument | Default | Meaning |
|---|---|---|
| `project` | *(required)* | the `.tether` project |
| `--json` | off | print one JSON document instead of text, in the layout [below](#status-json-layout) |
| `--require STAGE` | none | exit `3` unless the stage is ready (or not needed). Give it again for more stages. `STAGE` is one of the keys in the table below |

**It does not change the project.** As for `tether info`, the project is opened read-only
and no lock is taken, so this works on a project that another user holds. To say whether a
fit is stale it reads the trace arrays the fit was made from, as `tether info --stale` does.

The stages, in the order of the workflow, and the section of the application each one is in:

| Stage key | Name | Section | Ready when |
|---|---|---|---|
| `sources` | sources and geometry | Prepare | the project has a movie |
| `map` | map | Prepare | each movie has a map |
| `traces` | trace generation | Prepare | the project has molecules |
| `curation` | curation | Curate and correct | no molecule is undecided, or the initial curation was finished |
| `corrections` | corrections | Curate and correct | the correction state is `ANALYSIS_READY` |
| `analysis_sets` | analysis sets | Analyze | not available in this version ([#934](https://github.com/bioedca/tether/issues/934)) |
| `fits` | fits | Analyze | the project has a fit and no fit is stale |
| `results` | results | Analyze | not available in this version ([#943](https://github.com/bioedca/tether/issues/943)) |
| `deposit` | deposit | Deposit | not available in this version ([#956](https://github.com/bioedca/tether/issues/956)) |

Each stage has one of these states:

- `ready`.
- `not needed`: a project made from imported traces has no movie and no map.
- `in progress` or `not started`.
- `stale`: a record it depends on has changed, and the record has not been made again.
- `blocked`: the correction state is `CALIBRATION_BLOCKED`.
- `running`: a job of the stage is running now.
- `not available in this version`: the feature of the stage has not landed yet.

The corrections stage also names the correction state of the project
([the correction states](reference/tether-format.md#correction-states),
[ADR-0131](adr/0131-the-correction-states-are-a-transition-log-in-the-project.md)), one of
`APPARENT_REVIEW`,
`BLEEDTHROUGH_REQUIRED`, `GAMMA_REVIEW`, `CALIBRATION_BLOCKED`, `CALIBRATION_APPROVED`,
`CORRECTED_REVIEW`, `ANALYSIS_READY` and `CALIBRATION_STALE`.

The next step first retries a failed or interrupted job unless a job of the same command
finished successfully after its latest attempt. Then it looks for a running job to wait for,
the first stale stage, or the first stage that is not ready, in that order. A stage that is
not available is never the next step.

The step for a stale fit is `tether idealize PROJECT --stale-only`, except for a fit imported
from standalone tMAVEN, which that command never refits: for one, the step is to hand its
molecules to tMAVEN again and import the new fit.

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

The exit codes:

- `0`: the project was read, and each `--require` stage is ready.
- `1`: the project could not be read. One line `tether status: <path>: <reason>` goes to stderr.
- `2`: bad command-line arguments.
- `3`: a `--require` stage is not ready. One line for each such stage goes to stderr, and
  the document or the text is printed too.

### Status JSON layout

`--json` prints one JSON document. The layout has a version, `format_version`, with the same
rule as `tether info`: a later version of Tether can add keys without a change to the version,
and changes the version if it renames or removes a key, or changes the meaning of a value.

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

Fit a hidden Markov model to the traces of a project that exists, without a Python script:
a project from `tether extract`, or one you curated or corrected after `tether batch`. The
fit gives a small set of FRET states and the state of each molecule at each frame, and it
is stored in the project as an idealization model. With `--stale-only`, the command refits
each model whose inputs changed since its fit. [Idealize traces into FRET
states](how-to/idealize.md) explains the methods and what to do with the result.

```text
tether idealize [-h] [--method {vbconhmm,ebhmm}] [--nstates N | --nstates-grid LIST]
                [--model-name NAME] [--condition ID] [--include-rejected] [--overwrite]
                [--seed N] [--stale-only] [--max-restarts N]
                [--sidecar-timeout SECONDS] [--sidecar-python PATH]
                project
```

| Argument | Default | Meaning |
|---|---|---|
| `project` | *(required)* | the `.tether` project to fit |
| `--method {vbconhmm,ebhmm}` | `vbconhmm` | the fit: `vbconhmm` is the consensus VB-HMM, one shared model across all molecules, and `ebhmm` is ebFRET |
| `--nstates N` | automatic | fit exactly N states. Without it, the command fits each state count of `--nstates-grid` and keeps the one with the largest ELBO |
| `--nstates-grid LIST` | `1,2,3,4` | the state counts the automatic choice tries, comma-separated, for example `2,3,4,5`. Do not give it with `--nstates` |
| `--model-name NAME` | the method | store the fit as `/idealization/NAME`. NAME is one name, with no `/`. With `--stale-only`, refit only this model |
| `--condition ID` | all molecules | fit only the molecules of this condition ID (`tether info` lists them). Give it again for more conditions |
| `--include-rejected` | off | also fit the molecules marked rejected. Without it they are left out |
| `--overwrite` | off | replace a model of the same name. Without it the command refuses and fits nothing |
| `--seed N` | unseeded | seed every fit, so the same project, options and seed give the same model on the same build. The seed is not stored in the project |
| `--stale-only` | off | refit each model that has a stale molecule, with the settings stored in the model, and leave the others as they are. It takes none of the fit options above except `--model-name` |
| `--max-restarts N` | `3` | restart a fit up to N times on a sidecar crash or timeout. `0` disables restarts |
| `--sidecar-timeout SECONDS` | `1800` | per-fit sidecar timeout in seconds |
| `--sidecar-python PATH` | `$TETHER_SIDECAR_PYTHON` | sidecar interpreter for the fit |

The command fits the molecules that are not rejected, with the consensus VB-HMM and an
automatic state count, and stores the model under the name of the method, so the default
fit is the model `vbconhmm`. This is the same fit as `idealize_molecules` in Python with the
same options. The fit runs in the tMAVEN sidecar, found as for `tether batch`. Before it
writes anything the command checks that the sidecar can start. When the sidecar cannot
start, the command says why, exits `3` and does not change the project.

**The command holds the project's lock while it fits.** Close the project in the desktop
application first. When another user or the application holds the lock, the command exits
`1` and fits nothing. The molecules of each `--condition` are read again once the command
holds the lock. A fit can take minutes. The lock is released when the command ends.

**`--stale-only` refits a whole model.** A molecule is *stale* in a model when its trace,
its analysis window or its correction factors changed after the fit. A consensus VB-HMM or
an ebFRET model is one fit over all its molecules, so the command refits every molecule of
a model that has a stale molecule, not only the stale ones. A model with no stale molecule
is not refitted, and when no model has one the command needs no sidecar. The command counts
the stale molecules again once it holds the lock, so a change made by another writer before
then is refitted too.

**After a new trace generation, a refit finds each molecule by its key.** A new extraction of
a movie gives each of its molecules a new `molecule_id`. The refit finds a molecule of the
model by its `molecule_id`, or else by its `molecule_key`, which a molecule that did not move
keeps. A molecule that moved has a new key, so the refit leaves it out and the command names
it on a line `N moved in a new trace generation, not refitted: <keys>`. When no molecule of a
model is left, the refit of that model is refused.

**`--stale-only` never refits a model imported from standalone tMAVEN.** Such a model stores
no fit settings that Tether can repeat. The command reports it as `imported from tMAVEN, not
refitted` and leaves it as it is, stale or not. Naming one with `--model-name` is refused.
To refresh it, hand its molecules to tMAVEN again and import the new fit.

The command stores the fit, and it does not select an *active fit*. Comparing fits and
selecting one is planned work ([#939](https://github.com/bioedca/tether/issues/939)).

| Code | `tether idealize` |
|---|---|
| `0` | the fit is stored, or `--stale-only` found nothing stale |
| `1` | the fit failed, or the lock could not be taken |
| `2` | bad command-line arguments, or a refusal to start: an unknown condition, a model that exists without `--overwrite`, a project that cannot be opened, fit options with `--stale-only`, or an imported model named with `--stale-only` |
| `3` | the sidecar was not available, and nothing was written |

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

Show, or give, the metadata of the movies in a project: the frame time, the pixel size and
the channel roles (which half is the donor). Each value has an **origin**: `file` when
Tether read it from the movie (the ImageJ `finterval` and the ImageJ pixel size in µm, or
the channel crops of an imported `.tmap`), `user` when a person gave it, and `unknown`
when there is no value. A native extraction records the channel roles as `user`, because
`--donor-side` names the donor half.

A frame time is **verified** only when it is from the file, or when you confirm it in a
second call. A value you type stays not verified until you confirm it. Tether stores
this flag now. Rate calculations do not check it yet: that is planned work
([#941](https://github.com/bioedca/tether/issues/941)).

```text
tether metadata [-h] [--movie MOVIE_ID] [--frame-time SECONDS] [--pixel-size UM]
                [--confirm-frame-time] [--json]
                project
```

| Argument | Default | Meaning |
|---|---|---|
| `project` | *(required)* | path to the `.tether` project |
| `--movie MOVIE_ID` | the only movie | the movie to change; required when the project has more than one movie |
| `--frame-time SECONDS` | none | give the frame time (origin `user`, not verified) |
| `--pixel-size UM` | none | give the pixel size in µm per pixel (origin `user`) |
| `--confirm-frame-time` | off | mark the movie's frame time as verified; refused in the same call as `--frame-time` |
| `--json` | off | print the records as JSON instead of text |

With no option the command changes nothing and prints each movie. A value of `0` means
unknown. The values are kept beside the `/movies` table, and `/movies.frame_time` keeps
the value from the file ([`.tether` file format](reference/tether-format.md#movies-one-row-per-source-movie)).

```console
$ tether metadata run.tether --frame-time 0.1
mov-…:
  frame time: 0.1 s (user, not verified)
  pixel size: 0 um (unknown)
  channel roles: user
$ tether metadata run.tether --confirm-frame-time
```

## `tether export`

Write the results of one or more `.tether` projects to files that another tool can read.
The command has three forms. Each form calls one function of `tether.project.export`, so
the file is the same file that the function writes for the same settings. The
[Exports reference](reference/exports.md) tells what each file contains.

| Form | Writes | Function |
|---|---|---|
| `tether export table` | one CSV row per molecule | `export_molecule_table_csv` |
| `tether export traces` | the Deep-LASI donor and acceptor trace matrix | `export_deeplasi_txt` |
| `tether export subset` | a smaller `.tether` without the movie | `export_subset_tether` |

These rules apply to all three forms:

- **The project is only read.** The export takes no lock and does not change the project or
  its `<file>.lock` file. You can export a project that another user has open.
- **Every export writes a provenance file.** It is named after the export with
  `.provenance.json` added, and it records the Tether version, the source project and the
  settings ([what the provenance file records](reference/exports.md#provenance-file)).
- **An existing file is kept.** If the export or its provenance file exists, the export
  stops with exit code `1` and tells you to give `--overwrite`.
- **An input project is never a destination.** An output path that is the project
  itself, or a hard link to it, stops the export with exit code `1`:
  `subset out_path movie.tether is the source project; refusing to overwrite`. With several
  projects, every export and provenance file is checked against every project you gave, before
  the first export starts. A match stops the command with exit code `1` and nothing written.
  So `movie.tether` and `movie-subset.tether` exported into one folder cannot replace the
  second project with the subset of the first, even with `--overwrite`. The `<file>.lock` file
  of a project you gave is refused in the same way.
- **Two exports never share a file.** Two outputs or provenance files that are hard links to
  one existing file stop the command with exit code `1` before the first export, even with
  `--overwrite`: otherwise the second export would replace the first one's file. `subset`
  replaces its `.tether` file with a new one, which breaks the link, so for `subset` only the
  provenance files are compared.
- **Several projects go into a folder.** Give several projects, and `-o` names a folder. Tether
  makes the folder if necessary and names each export after its project: `<project>.csv`,
  `<project>-donc-accc-w.txt` or `<project>-subset.tether`. Two projects with the same file
  name would write the same export (names that differ only in upper and lower case count as the
  same), so that command stops with exit code `2` before it writes a file. A project that fails does not stop the others, and the command then exits `1`.
- **Each export prints one line,** the number of molecules and the path:
  `Exported 412 molecule(s) -> molecules.csv`.

### Example — the molecule table of every project after a batch run

Make the folder first: if the pattern matches only one project, `-o` names the file to
write unless it is an existing folder.

```bash
mkdir tables
tether export table projects/*.tether -o tables
```

```powershell
# Windows PowerShell has no shell glob expansion for native commands
mkdir tables
tether export table (Get-ChildItem projects\*.tether) -o tables
```

## `tether export table`

Write one CSV row per molecule: the stored per-molecule fields and the apparent-E summary
([Molecule-table CSV](reference/exports.md#1-molecule-table-csv)). Rejected molecules are
included, with `reject` in the `curation_label` column.

```text
tether export table [-h] -o OUTPUT [--overwrite]
                    [--intensity-quantity {corrected,raw}]
                    [--exclude-rejected]
                    projects [projects ...]
```

| Argument | Default | Meaning |
|---|---|---|
| `projects` | *(required, one or more)* | one or more `.tether` projects to read |
| `-o OUTPUT`, `--output OUTPUT` | *(required)* | the `.csv` file to write; with several projects, or when it is an existing folder, the folder (created if absent) that gets one `<project>.csv` per project |
| `--overwrite` | off | replace an existing output file and its provenance file |
| `--intensity-quantity {corrected,raw}` | `corrected` | which trace layer to read (`intensity_quantity`) |
| `--exclude-rejected` | off | leave rejected molecules out of the table (`include_rejected=False`) |

```bash
tether export table movie.tether -o molecules.csv
```

## `tether export traces`

Write the per-frame donor and acceptor traces as a Deep-LASI trace matrix
([Deep-LASI trace matrix](reference/exports.md#2-deep-lasi-trace-matrix-txt)). `-o` always
names a folder, and the file in it is named `<project>-donc-accc-w.txt`. An `-o` that ends in
`.txt` and does not exist yet is refused with exit code `2`, because it is almost certainly a
file name. Rejected molecules are left out.

```text
tether export traces [-h] -o OUTPUT [--overwrite]
                     [--intensity-quantity {corrected,raw}]
                     [--include-rejected] [--molecule-key KEY]
                     projects [projects ...]
```

| Argument | Default | Meaning |
|---|---|---|
| `projects` | *(required, one or more)* | one or more `.tether` projects to read |
| `-o OUTPUT`, `--output OUTPUT` | *(required)* | the folder (created if absent) to write one `<project>-donc-accc-w.txt` in per project |
| `--overwrite` | off | replace an existing output file and its provenance file |
| `--intensity-quantity {corrected,raw}` | `corrected` | which trace layer to read (`intensity_quantity`) |
| `--include-rejected` | off | keep rejected molecules (`include_rejected=True`) |
| `--molecule-key KEY` | every molecule | export only this molecule; give the flag again for each further molecule (`molecule_keys`) |

All molecules in one file share one frame axis. For a project that combines movies of
different lengths, export one movie at a time with its `--molecule-key` values. Otherwise the
export stops with exit code `1` and names the frame ranges.

```bash
tether export traces movie.tether -o traces
```

## `tether export subset`

Write a self-contained `.tether` of the selected molecules, without the movie
([Subset `.tether`](reference/exports.md#3-subset-tether)). Rejected molecules and the raw
traces are left out.

```text
tether export subset [-h] -o OUTPUT [--overwrite] [--include-rejected]
                     [--include-raw] [--molecule-key KEY]
                     [--curator-alias PREFIX]
                     projects [projects ...]
```

| Argument | Default | Meaning |
|---|---|---|
| `projects` | *(required, one or more)* | one or more `.tether` projects to read |
| `-o OUTPUT`, `--output OUTPUT` | *(required)* | the `.tether` file to write; with several projects, or when it is an existing folder, the folder (created if absent) that gets one `<project>-subset.tether` per project |
| `--overwrite` | off | replace an existing output file and its provenance file |
| `--include-rejected` | off | keep rejected molecules (`include_rejected=True`) |
| `--include-raw` | off | also embed the raw and background traces (`include_raw=True`) |
| `--molecule-key KEY` | every molecule | export only this molecule; give the flag again for each further molecule (`molecule_keys`) |
| `--curator-alias PREFIX` | names are kept | replace each stored user name with `PREFIX-1`, `PREFIX-2` and so on (`curator_alias`); with several projects, each copy numbers its own names |

```bash
tether export subset movie.tether -o accepted.tether
```

To share a copy without your user name, give a prefix. A blank prefix, or a prefix that
contains a user name stored in a project, fails the export of that project: no file is written
for it, the other projects are still exported, and the exit code is `1`:

```bash
tether export subset movie.tether -o shared.tether --curator-alias curator
```

## Sync-conflict copies beside a project

OneDrive and SharePoint can leave a second copy of a file when two machines save it at the
same time. Tether does not prevent these copies. It finds them and tells you.
After `tether extract` writes its project, after `tether batch` finishes, and when
`tether info`, `tether metadata` or `tether export` reads a project, each project the command
opened is checked for siblings in the two documented naming forms:
`<stem>-<COMPUTERNAME>.tether` and `<stem> (<...>).tether`. A hit adds one warning line to
stderr:

```text
  warning: 1 possible OneDrive/SharePoint conflict copy beside movie.tether: movie-DESKTOP-AB12.tether. Tether does not merge or delete it; compare and reconcile by hand.
```

The warning is advisory. It does not change the exit code, and Tether does not open, merge,
rename or delete the copy. It goes to stderr, so the `tether info --json` document on stdout
does not change. The match is by file name only, so a project you named
`movie-donoronly.tether` on purpose is also listed, and you can ignore the line for it. The GUI
shows the same text in the Browser when you open the project.

## Service commands

`tether extract`, `tether reextract`, `tether batch`, `tether idealize`, `tether map create`
and `tether approve` are each the command-line route of one *service command*
([ADR-0100](adr/0100-service-commands-are-the-one-way-to-run-an-action.md)). A service
command is the one implementation of an action. The command line uses it now. The
desktop application and recipe files will use the same commands as their sections are
moved onto them, so the three cannot do different things for the same input. The command
line only reads your options into the command's *request*, runs the command, and prints
the result.

The table comes from the registry in `tether.services.registry`, and a test fails if the
two differ:

<!-- service-commands:begin (made from tether.services.registry; do not edit) -->
| Command | Request | Result | What it does |
|---|---|---|---|
| `tether extract` | `tether.services.extract.ExtractRequest` (version 1) | `tether.services.extract.ExtractResult` | extract traces from a dual-channel movie into a .tether project |
| `tether reextract` | `tether.services.reextract.ReextractRequest` (version 1) | `tether.services.reextract.ReextractResult` | extract a movie of a .tether project again, as a new trace generation |
| `tether batch` | `tether.services.batch.BatchRequest` (version 1) | `tether.services.batch.BatchResult` | run the extract -> correct -> idealize pipeline over many movies |
| `tether idealize` | `tether.services.idealize.IdealizeRequest` (version 1) | `tether.services.idealize.IdealizeResult` | fit the molecules of a .tether project, or refit its stale models |
| `tether map` | `tether.services.maps.MapCreateRequest` (version 1) | `tether.services.maps.MapCreateResult` | make a channel map file (.npz) from a bead video or image |
| `tether approve` | `tether.services.approve.ApproveRequest` (version 1) | `tether.services.approve.ApproveResult` | record that a person approved a step that needs a person |
<!-- service-commands:end -->

A request is plain data with one text form: a JSON object with the command name, the
version of the request, and every field. For example, `tether extract movie.tif -o
movie.tether` sends this request. The text form is one line. It is broken here to fit
the page, and a break between two parts of JSON does not change it:

```json
{"command":"extract","fields":{"bg_window":10,"channel_layout":null,
"channel_layout_acceptor_rect":null,"channel_layout_donor":null,
"channel_layout_donor_rect":null,"coloc_distance":3.0,"colocalization":"partner-in-both",
"count_only":false,"detection_block":50,"detection_mode":null,"detection_threshold":null,
"disk_radius":3.0,"donor_side":"left","map_path":null,
"min_separation":null,"movie":"movie.tif","output":"movie.tether","overwrite":false,
"pair_tol":2.0,"prealign":"translation","prealign_high_sigma":20.0,
"prealign_low_sigma":1.0,"prealign_upsample":10,"ring_inner":6.0,"ring_outer":8.0,
"rms_gate":0.5,"tdat":null,"tmap":null,"window":21},"version":1}
```

The field names are the option names with `_` for `-`. Both requests also hold the
fields of an [extraction profile](#extraction-profiles), and `--profile` sets
them: the file itself is not part of the request, so a request holds every value it ran
with. Two `tether batch` flags are positive fields: `--no-idealize` is
`"idealize": false` and `--no-defer` is `"defer_if_unavailable": false`. A recipe file, which
[`tether run`](#tether-run) reads, is a list of these requests.

## Exit codes

| Code | `tether extract` | `tether batch` | `tether info` | `tether metadata` | `tether export` |
|---|---|---|---|---|---|
| `0` | extraction completed | every requested stage ran for every movie, or `--dry-run` printed its plan | every project was read | the records were printed, after any change | every export was written |
| `1` | extraction failed (`ExtractionError`), or the `--profile` could not be used | at least one movie had a failed stage (whether or not a stage was also deferred), or the output directory or the log could not be written | at least one project could not be read | a refused change or an unreadable project: no movie to choose, an unknown movie, a value that is not finite and positive, nothing to confirm, or a locked file | at least one export failed: the project could not be read, the output exists and `--overwrite` was not given, an output is an input project or its lock file, two outputs or provenance files are one file (hard links), or the function refused the selection; or the output path could not be checked or its folder made |
| `2` | bad command-line arguments (argparse) | bad command-line arguments (argparse), **or** a refusal to start: invalid sidecar-supervision values or extraction options, a `--profile` that could not be used, `--tdat` beside a detection choice, `--tmap` beside a profile `channel_layout`, a movie argument that gives no movie, or two input movies that no folder name tells apart | bad command-line arguments (argparse) | bad command-line arguments (argparse), **or** `--frame-time` and `--confirm-frame-time` in one call | bad command-line arguments (argparse), **or** a refusal to start: two projects that map to one export, `-o` a file where a folder is needed, or a `.txt` `-o` for `traces` |
| `3` | never returned | no movie failed, and at least one requested stage was deferred for at least one movie | never returned | never returned | never returned |

`tether status` has a code `3` of its own, for a `--require` stage that is not ready. Its
codes are in [its section](#tether-status).
`tether idealize` has a code `3` of its own, for a sidecar that cannot start. Its codes are
in [its section](#tether-idealize).

Exit code `2` means no movie was processed: either argparse rejected the command line
before any subcommand handler ran, or `tether batch` or `tether export` refused to start. Nothing is
read and no project or export is written on either path, so it is safe to fix the arguments and re-run.

Exit code `1` means the command *ran*. From `tether extract` it is a clean one-line error
on stderr, never a traceback — an unhandled traceback from any subcommand is a bug
worth reporting. From `tether export` it is one such line per failed project, and the other
projects are still exported. From `tether batch` it means the run finished with at least one movie
having a failed stage; fix those and re-run the same command to resume the rest. It also
means `tether batch` could not create `--out-dir` or write the log. It then prints one line
on stderr that names the path.

> **`3` from `tether batch` means the projects are not finished.** If the sidecar is
> unavailable when the run starts, the default behavior is to *defer* idealization: the
> stage is recorded as `deferred`, which is not `failed`, and the run exits `3` having done
> extraction and correction only. The report prints a `Deferred:` line with the stage and
> the number of movies. Re-run the same command once the sidecar is available and the
> per-stage checkpoint resumes just the idealization, or pass `--no-defer` to make an
> unavailable sidecar a per-movie failure (exit `1`) instead.
>
> `1` takes precedence over `3`: a run with a failed movie exits `1` even when another
> movie's idealization was deferred. A stage you did not ask for is not deferred, so a run
> with `--no-idealize` exits `0` when every movie extracted and corrected. The last line of
> the report names the exit code and its meaning, and `tether batch --help` lists the four
> codes.

**Where to look for what failed.** `tether batch` prints its end-of-run summary — the
per-movie, per-stage detail — to **stdout**, together with the JSONL structured log it
writes to `<out-dir>/batch-log.jsonl`. stderr carries the
[progress lines](#progress-while-the-batch-runs) (none with `--quiet`), the log's path and
startup refusals. If you are capturing streams separately, the complete per-movie failure
detail is in stdout and the log. The progress lines on stderr are for watching the run.

### Exit code `4`: a person must approve a step

Code `4` has one meaning in every `tether` command: the run stopped before a step that
needs a person, because nothing approved that step. [`tether run`](#tether-run) returns it.
`tether batch` never returns it, because it applies its `--policy` rule to the
map and accepts the estimated correction factors, as it does now
([ADR-0102](adr/0102-a-batch-run-stops-at-an-approval-step-or-applies-a-recipe-policy.md)).

The codes that have one meaning in every command:

- `0`: the command completed.
- `1`: the command ran, and at least one item failed.
- `2`: bad command-line arguments, or a refusal to start.
- `4`: the run stopped at an approval step, and nothing failed.

The approval steps are: accept a map, confirm the bleedthrough factor α, approve γ, and
select the active fit. A command that stops with `4` writes one message to stderr with the
step, its subject, the report that is the evidence, the command that approves the step and
the command that continues the run. The work done before the stop is kept, and the
continued run does not do it again. A run that has a failed item and also stopped exits
`1`. A code that this list does not name has the meaning that the table of its command
gives.
