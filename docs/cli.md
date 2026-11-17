---
description: "Run tether extract and tether batch without the application window: script the pipeline, run movies unattended or repeat an extraction."
---

# Command line

**Who this page is for.** Anyone driving Tether without the application window: scripting the
pipeline, processing a night's worth of movies unattended, or reproducing an
extraction from recorded settings. Today the command line has four subcommands:
`tether extract` and `tether batch` write projects, `tether info` only reads them and
prints a summary, and `tether metadata` shows or gives the frame time, pixel size and
channel roles of a project's movies.

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
               [--detection-mode {wavelet,intensity,bandpass}]
               [--detection-threshold FRAC] [--window WINDOW]
               [--min-separation MIN_SEPARATION]
               [--detection-block DETECTION_BLOCK]
               [--prealign {translation,similarity}] [--pair-tol PAIR_TOL]
               [--coloc-distance COLOC_DISTANCE] [--rms-gate RMS_GATE]
               [--tmap PATH] [--tdat PATH]
               movie
```

| Argument | Default | Meaning |
|---|---|---|
| `movie` | *(required)* | path to the dual-channel TIFF movie |
| `-o OUTPUT`, `--output OUTPUT` | *(required)* | path to the `.tether` project to create |
| `--overwrite` | off | overwrite an existing output project; Tether holds the project's writer lock for the run, so it refuses a project that another writer has open (not another user who opened it through a different hard-link name) |
| `--donor-side {left,right}` | `left` | which horizontal half is the donor channel |
| `--detection-mode {wavelet,intensity,bandpass}` | `wavelet` | particle-detection method (Deep-LASI `findPart` mode). `intensity`/`bandpass` also honor `--detection-threshold`. Mutually exclusive with `--tdat`, which supplies the mode |
| `--detection-threshold FRAC` | each mode's own — intensity `0.5`, bandpass `0.98` | detection threshold as a fraction of the detection-image max, in `[0, 1)` (intensity/bandpass modes only; ignored by wavelet). Mutually exclusive with `--tdat` |
| `--window WINDOW` | `21` | aperture / crop-box side length in px, odd |
| `--min-separation MIN_SEPARATION` | unset uses each detection mode's faithful default (wavelet 8, intensity/bandpass 3) | minimum spot separation in px |
| `--detection-block DETECTION_BLOCK` | `50` | moving-average block size for the detection image |
| `--prealign {translation,similarity}` | `translation` | registration prealign degrees of freedom |
| `--pair-tol PAIR_TOL` | `2` | control-point pairing tolerance in px |
| `--coloc-distance COLOC_DISTANCE` | `3` | acceptor colocalization distance in px |
| `--rms-gate RMS_GATE` | `0.5` | registration RMS-residual gate in px |
| `--tmap PATH` | none | apply an imported Deep-LASI `.tmap` instead of a native fit; splits at the `.tmap`'s own channel geometry (`--donor-side` is then ignored) |
| `--tdat PATH` | none | auto-apply the particle-detection mode decoded from a Deep-LASI `.tdat` (`temp/ParticleDetectionMode`), so extraction matches the method the movie was detected with; mutually exclusive with `--detection-mode`/`--detection-threshold` |

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
accept the same values.

### Detection settings: pick one source

`--tdat` and `--detection-mode`/`--detection-threshold` are two ways to set the same
thing, so combining them is refused up front — before any movie is read — rather than
letting one silently win:

```console
$ tether extract movie.tif -o movie.tether --tdat run.tdat --detection-mode wavelet
tether extract: --detection-mode/--detection-threshold cannot be combined with --tdat (the .tdat supplies the detection mode); pass one or the other
```

### Example — one movie to one project

```bash
tether extract movie.tif -o movie.tether
```

```console
Extracted 412 molecule(s) -> movie.tether
  donor side: 38 of 40 bleach events agree with the donor side as extracted.
```

The second line is the [donor-side check](#get-donor-side-right-and-read-the-donor-side-check).
If the registration RMS residual exceeds `--rms-gate`, extraction still succeeds and
still exits 0, but a warning goes to stderr and the affected molecules are tagged
`low-confidence-registration` in the project — a flag you can filter on later rather
than a silent pass.

## `tether batch`

Process many movies unattended (FR-BATCH). Each movie becomes its own
`<out-dir>/<stem>.tether`. That is how the command writes, not a limit of the project
file, which can hold several movies of one experiment
([`.tether` project](glossary.md#tether-project)). Failures are isolated — one bad movie
does not stop the run — and each stage is checkpointed, so re-running the same command
resumes only the incomplete stages instead of redoing finished work.

```text
tether batch [-h] -d OUT_DIR [--tmap PATH] [--tdat PATH] [--policy {warn,fail}]
             [--no-idealize] [--overwrite] [--log PATH] [--max-restarts N]
             [--sidecar-timeout SECONDS] [--sidecar-python PATH] [--no-defer]
             movies [movies ...]
```

| Argument | Default | Meaning |
|---|---|---|
| `movies` | *(required, one or more)* | one or more dual-channel TIFF movies |
| `-d OUT_DIR`, `--out-dir OUT_DIR` | *(required)* | directory for the per-movie `.tether` projects (created if absent) |
| `--tmap PATH` | none | a shared imported Deep-LASI `.tmap` applied to every movie |
| `--tdat PATH` | none | a shared Deep-LASI `.tdat` detection config applied to every movie |
| `--policy {warn,fail}` | `warn` | over-gate registration policy: `warn` keeps an over-gate movie with a flag; `fail` records that movie's extract stage as failed — the inspectable project file is still written, and the saved residual is re-evaluated on resume ([see below](#how-policy-fail-behaves-on-resume)) |
| `--no-idealize` | off | skip the idealization stage (extract + correct only) |
| `--overwrite` | off | re-extract an incomplete output, or a completed extraction that the current `fail` policy rejects; accepted completed checkpoints still skip |
| `--log PATH` | `<out-dir>/batch-log.jsonl` | write a JSONL structured log here |
| `--max-restarts N` | `3` | auto-restart a movie's idealization up to N times on a transient sidecar failure (crash/timeout). `0` disables restarts |
| `--sidecar-timeout SECONDS` | `1800` | per-idealization-call sidecar timeout in seconds |
| `--sidecar-python PATH` | `$TETHER_SIDECAR_PYTHON` | sidecar interpreter for idealization |
| `--no-defer` | off | do not defer idealization when the sidecar is unavailable at startup; let each movie's idealize stage fail in isolation instead |

Idealization runs in the isolated tMAVEN sidecar, not in the application environment. If
`--sidecar-python` is not given, Tether uses `$TETHER_SIDECAR_PYTHON`; an installed build
also falls back to the `envs/sidecar` interpreter shipped beside the application, so a
normal install needs neither. When no sidecar can be found at startup the run *defers*
idealization — extraction and correction still happen for every movie — unless you pass
`--no-defer`, which instead lets each movie's idealize stage fail on its own.

Two movies whose filenames share a basename would both map to the same output project,
and the checkpoint would treat the second as already done using the first's data. That is
rejected before any work starts:

```console
$ tether batch a/movie_010.tif b/movie_010.tif -d out
tether batch: movies 'a/movie_010.tif' and 'b/movie_010.tif' both map to 'out/movie_010.tether'; rename one or use a separate --out-dir
```

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
```

The structured log carries the same finding as a `donor-side-check` event with status
`warning`. A movie with too few bleach events to check, or with no clear majority, adds
nothing to the summary. To act on a flagged movie, extract it again with `tether extract`
and the remedy the message names.

### Example — a folder of movies, extraction and correction only

```bash
tether batch data/*.tif -d projects --no-idealize
```

```powershell
# Windows PowerShell has no shell glob expansion for native commands
tether batch (Get-ChildItem data\*.tif) -d projects --no-idealize
```

The end-of-run summary goes to stdout; the machine-readable JSONL log lands at
`projects/batch-log.jsonl` unless you moved it with `--log`. Re-run the identical command
later without `--no-idealize` and only the idealization stage executes — everything
already checkpointed is skipped.

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
  schema version 1, written by Tether <version>
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
`Get-ChildItem` as in the [batch example](#example-a-folder-of-movies-extraction-and-correction-only)).
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

## Service commands

`tether extract` and `tether batch` are each the command-line route of one *service command*
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
| `tether batch` | `tether.services.batch.BatchRequest` (version 1) | `tether.services.batch.BatchResult` | run the extract -> correct -> idealize pipeline over many movies |
<!-- service-commands:end -->

A request is plain data with one text form: a JSON object with the command name, the
version of the request, and every field. For example, `tether extract movie.tif -o
movie.tether` sends this request. The text form is one line. It is broken here to fit
the page, and a break between two parts of JSON does not change it:

```json
{"command":"extract","fields":{"coloc_distance":3.0,"detection_block":50,
"detection_mode":null,"detection_threshold":null,"donor_side":"left",
"min_separation":null,"movie":"movie.tif","output":"movie.tether","overwrite":false,
"pair_tol":2.0,"prealign":"translation","rms_gate":0.5,"tdat":null,"tmap":null,
"window":21},"version":1}
```

The field names are the option names with `_` for `-`. Two `tether batch` flags are
positive fields: `--no-idealize` is `"idealize": false` and `--no-defer` is
`"defer_if_unavailable": false`. A recipe file, which `tether run` will read, is a list of
these requests ([#899](https://github.com/bioedca/tether/issues/899)).

## Exit codes

| Code | `tether extract` | `tether batch` | `tether info` | `tether metadata` |
|---|---|---|---|---|
| `0` | extraction completed | no stage failed — **but this does not guarantee idealization ran**; see below | every project was read | the records were printed, after any change |
| `1` | extraction failed (`ExtractionError`) | at least one movie had a failed stage, or the output directory or the log could not be written | at least one project could not be read | a refused change or an unreadable project: no movie to choose, an unknown movie, a value that is not finite and positive, nothing to confirm, or a locked file |
| `2` | bad command-line arguments (argparse) | bad command-line arguments (argparse), **or** a refusal to start: invalid sidecar-supervision values, or two input movies whose basenames collide on one output `.tether` | bad command-line arguments (argparse) | bad command-line arguments (argparse), **or** `--frame-time` and `--confirm-frame-time` in one call |

Exit code `2` means no movie was processed: either argparse rejected the command line
before any subcommand handler ran, or `tether batch` refused to start. Nothing is read and no project is
written on either path, so it is safe to fix the arguments and re-run — with one wrinkle,
`tether batch` creates `--out-dir` before it checks for colliding basenames, so that
particular refusal can leave an empty output directory behind.

Exit code `1` means the command *ran*. From `tether extract` it is a clean one-line error
on stderr, never a traceback — an unhandled traceback from either subcommand is a bug
worth reporting. From `tether batch` it means the run finished with at least one movie
having a failed stage; fix those and re-run the same command to resume the rest. It also
means `tether batch` could not create `--out-dir` or write the log. It then prints one line
on stderr that names the path.

> **`0` from `tether batch` does not mean every stage ran.** If the sidecar is unavailable
> when the run starts, the default behavior is to *defer* idealization: the stage is
> recorded as `deferred`, which is not `failed`, so the run exits `0` having done
> extraction and correction only. A script that treats `0` as "these projects are fully
> idealized" will consume them too early. Re-run the same command once the sidecar is
> available and the per-stage checkpoint resumes just the idealization, or pass
> `--no-defer` to make an unavailable sidecar a per-movie failure (exit `1`) instead.

**Where to look for what failed.** `tether batch` prints its end-of-run summary — the
per-movie, per-stage detail — to **stdout**, together with the JSONL structured log it
writes to `<out-dir>/batch-log.jsonl`. Only the log's path, and startup refusals, go to
stderr. If you are capturing streams separately, per-movie failure detail is in stdout and
the log, not in stderr.

### Exit code `4`: a person must approve a step

Code `4` is reserved, and it has one meaning in every `tether` command: the run stopped
before a step that needs a person, because nothing approved that step. No command returns
it today. `tether batch` never returns it, because it applies its `--policy` rule to the
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
