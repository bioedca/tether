# Command line

**Who this page is for.** Anyone driving Tether without the window: scripting the
pipeline, processing a night's worth of movies unattended, reproducing an extraction
from recorded settings, or exporting results from many projects. `tether extract`,
`tether batch` and `tether export` are the whole headless surface. If you are curating
traces by hand,
picking states by eye, or reading plots, you want the desktop application instead; this
page only tells you how to start it.

Everything below was transcribed from the live argparse parser in `src/tether/cli.py` —
the same parser `--help` prints from — and a guard test (`tests/test_cli_reference.py`)
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

| Platform | Command-line launcher | Window launcher |
|---|---|---|
| Windows | `<install-root>\bin\tether.bat` | Start Menu → **Tether**, or `<install-root>\bin\tether-gui.bat` in a terminal |
| Linux | `<install-root>/bin/tether` | Applications menu → **Tether**, or `<install-root>/bin/tether-gui` |
| macOS | `<install-root>/bin/tether` | `<install-root>/bin/tether-gui` |

Add that one directory to the **end** of your `PATH`, and you can copy every example on
this page as it is. [Install Tether, step 4](install.md#4-put-tethers-launcher-folder-on-your-path)
gives the line for each system. It also tells why the folder goes at the end: on Linux and
macOS the folder also holds the installer's own `python`, `pip` and `conda`, and these must
not hide your own.

Without that step, substitute the full launcher path from the table for `tether` in
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
| `--overwrite` | off | overwrite an existing output project |
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
> legal. `{left,right}` in the help text above is a *metavar*, not an argparse `choices`
> list: a value that is neither, such as `--donor-side sideways`, is not rejected at parse
> time either. It is caught downstream by `ExtractOptions` and reported as a clean error
> with exit code 1. A valid-but-wrong value is not an error, so the extraction itself
> cannot refuse it.
>
> **The donor-side check.** After every extraction Tether reads the side back out of the
> traces ([ADR-0070](adr/0070-advisory-check-for-swapped-donor-and-acceptor-halves.md)).
> When the acceptor bleaches first the acceptor drops to background and the donor *rises*;
> when the donor bleaches first both drop. A bleach event is a molecule whose dyes bleach
> one after the other, with a clear drop in one channel and a clear rise in the other at the
> first bleach. If the channel labelled donor shows the acceptor's drop in at least 80 % of
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
> trace steps **up** at that moment. If instead the trace you have labelled "donor" steps
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

`--detection-mode` and `--prealign` are metavars in the same way — an unrecognised value
reaches `ExtractOptions` rather than argparse, and comes back as an error message on
stderr with exit code 1, never a traceback. `--policy` on `tether batch` is the one flag
of those two subcommands with a real argparse `choices` list; `--intensity-quantity` on
`tether export` has one too.

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
same-identity sidecar or writing through it. Both claims remain held while batch creates,
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
  settings ([Provenance sidecar](reference/exports.md#provenance-sidecar)).
- **An existing file is kept.** If the export or its provenance file exists, the export
  stops with exit code `1` and tells you to give `--overwrite`.
- **The source project is never the destination.** An output path that is the project
  itself, or a hard link to it, stops the export with exit code `1`:
  `subset out_path movie.tether is the source project; refusing to overwrite`.
- **Several projects go into a folder.** Give several projects, and `-o` names a folder. Tether
  makes the folder if necessary and names each export after its project: `<project>.csv`,
  `<project>-donc-accc-w.txt` or `<project>-subset.tether`. Two projects with the same file
  name would write the same export, so that command stops with exit code `2` before it writes
  a file. A project that fails does not stop the others; the command then exits `1`.
- **Each export prints one line,** the number of molecules and the path:
  `Exported 412 molecule(s) -> molecules.csv`.

### Example — the molecule table of every project after a batch run

```bash
tether export table projects/*.tether -o tables
```

```powershell
# Windows PowerShell has no shell glob expansion for native commands
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
different lengths, export one movie at a time with its `--molecule-key` values; otherwise the
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

```bash
tether export subset movie.tether -o accepted.tether
```

## Exit codes

| Code | `tether extract` | `tether batch` | `tether export` |
|---|---|---|---|
| `0` | extraction completed | no stage failed — **but this does not guarantee idealization ran**; see below | every export was written |
| `1` | extraction failed (`ExtractionError`) | at least one movie had a failed stage | at least one export failed: the project could not be read, the output exists and `--overwrite` was not given, the output is the source project, or the function refused the selection |
| `2` | bad command-line arguments (argparse) | bad command-line arguments (argparse), **or** a refusal to start: invalid sidecar-supervision values, or two input movies whose basenames collide on one output `.tether` | bad command-line arguments (argparse), **or** a refusal to start: two projects that map to one export, `-o` a file where a folder is needed, or a `.txt` `-o` for `traces` |

Exit code `2` means no movie was processed: either argparse rejected the command line
before any subcommand handler ran, or `tether batch` or `tether export` refused to start. Nothing is
read and no project or export is written on either path, so it is safe to fix the arguments
and re-run — with one wrinkle,
`tether batch` creates `--out-dir` before it checks for colliding basenames, so that
particular refusal can leave an empty output directory behind.

Exit code `1` means the command *ran*. From `tether extract` it is a clean one-line error
on stderr, never a traceback — an unhandled traceback from any subcommand is a bug
worth reporting. From `tether export` it is one such line per failed project, and the other
projects are still exported. From `tether batch` it means the run finished with at least one movie
having a failed stage; fix those and re-run the same command to resume the rest.

> **`0` from `tether batch` does not mean every stage ran.** If the sidecar is unavailable
> when the run starts, the default behaviour is to *defer* idealization: the stage is
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
