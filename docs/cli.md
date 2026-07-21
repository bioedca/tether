# Command line

**Who this page is for.** Anyone driving Tether without the window: scripting the
pipeline, processing a night's worth of movies unattended, or reproducing an
extraction from recorded settings. `tether extract` and `tether batch` are the whole
headless surface — there is no third subcommand. If you are curating traces by hand,
picking states by eye, or reading plots, you want the desktop application instead; this
page only tells you how to start it.

Everything below was transcribed from `--help` on an installed build. Where a default is
quoted, it is the string argparse prints, not a value read out of the source.

## Launching Tether after an installer install

The installer deliberately does **not** edit your `PATH` (see
[ADR-0051](adr/0051-installed-app-launch-surface.md)), so out of the box neither command
is a bare word you can type. It writes both launchers into one directory — `bin/` under
the install root you chose during installation:

| Platform | Command-line launcher | Window launcher |
|---|---|---|
| Windows | `<install-root>\bin\tether.bat` | Start Menu → **Tether**, or `<install-root>\bin\tether-gui.bat` |
| Linux | `<install-root>/bin/tether` | Applications menu → **Tether**, or `<install-root>/bin/tether-gui` |
| macOS | `<install-root>/bin/tether` | `<install-root>/bin/tether-gui` |

Add that one directory to your `PATH` and every example on this page becomes literally
copy-pasteable:

```bash
# macOS / Linux — append to ~/.zshrc or ~/.bashrc to make it permanent
export PATH="<install-root>/bin:$PATH"
```

```powershell
# Windows PowerShell — current session only
$env:PATH = "<install-root>\bin;$env:PATH"

# Windows PowerShell — persists for your user account (new sessions only;
# it does not affect the session you run it in)
[Environment]::SetEnvironmentVariable(
    "PATH", "<install-root>\bin;" + [Environment]::GetEnvironmentVariable("PATH", "User"), "User")
```

Without that step, substitute the full launcher path from the table for `tether` in
every command below; nothing else changes. Verify the install with:

```console
$ tether --version
tether 1.0.0
```

`--version` is the one flag that never touches your data, so it is the right smoke test.
The version is derived from git at build time: a source checkout with no git metadata
reports `tether 0.0.0+unknown`, which means you are running an unbuilt tree, not that the
install is broken.

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

### Get `--donor-side` right, because nothing will tell you it is wrong

> **This is the one flag that can silently invert your results.** `left` and `right` name
> which *horizontal half* of the dual-channel frame carries donor emission. The two halves
> are simply swapped, so a backwards value produces a project in which every FRET
> efficiency is inverted — and it raises no error, because both values are perfectly
> legal. `{left,right}` in the help text above is a *metavar*, not an argparse `choices`
> list: a value that is neither, such as `--donor-side sideways`, is not rejected at parse
> time either. It is caught downstream by `ExtractOptions` and reported as a clean error
> with exit code 1, but a valid-but-wrong value is caught by nothing.
>
> **How to check.** Look at a molecule whose acceptor photobleaches mid-trace. When the
> acceptor dies the donor stops transferring energy, so the *donor* trace steps **up** at
> that moment. If instead the trace you have labelled "donor" steps *down* while the other
> steps up, your halves are swapped — re-extract with the other value.
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
> filename is recorded alongside it as `tmap_source`.

`--detection-mode` and `--prealign` are metavars in the same way — an unrecognised value
reaches `ExtractOptions` rather than argparse, and comes back as an error message on
stderr with exit code 1, never a traceback. `--policy` on `tether batch` is the one flag
in either subcommand with a real argparse `choices` list.

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
```

If the registration RMS residual exceeds `--rms-gate`, extraction still succeeds and
still exits 0, but a warning goes to stderr and the affected molecules are tagged
`low-confidence-registration` in the project — a flag you can filter on later rather
than a silent pass.

## `tether batch`

Process many movies unattended (FR-BATCH). Each movie becomes its own
`<out-dir>/<stem>.tether`. Failures are isolated — one bad movie does not stop the run —
and each stage is checkpointed, so re-running the same command resumes only the
incomplete stages instead of redoing finished work.

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
| `--policy {warn,fail}` | `warn` | over-gate registration policy: `warn` keeps an over-gate movie with a flag; `fail` records that movie's extract stage as failed — the project file is still written, and the verdict does not survive a re-run ([see below](#a-policy-fail-rejection-does-not-survive-the-re-run)) |
| `--no-idealize` | off | skip the idealization stage (extract + correct only) |
| `--overwrite` | off | re-extract a movie whose output exists but is not a completed extraction |
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

### A `--policy fail` rejection does not survive the re-run

`fail` is a verdict recorded about a movie, not a veto on its output. Extraction writes
the `.tether` first and the gate is applied afterwards, to the summary that write
returns — so an over-gate movie under `--policy fail` leaves a **complete, checkpointed**
project in `<out-dir>`, with its extract stage recorded `failed` and correct/idealize
`blocked`. That run exits `1`, as documented.

> **The rejection does not survive the re-run.** Resuming is the whole point of the
> checkpoint, and the checkpoint is consulted *before* the policy: on a second run of the
> same command the extract stage is `skipped` ("already extracted"), which counts as
> satisfied, so correction and — unless you passed `--no-idealize` — idealization run to
> completion on the movie the policy rejected. With no failed stage left, that second run
> exits `0`. `--overwrite` does not change this: it only covers an output that is *not* a
> completed extraction, and a completed one is always skipped.
>
> So `--policy fail` is a gate on the *first* run only. If you rely on it, delete (or move
> aside) each rejected `<out-dir>/<stem>.tether` before re-running, and treat the first
> run's JSONL log — not the presence of a project on disk — as the record of what passed.

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

## Exit codes

| Code | `tether extract` | `tether batch` |
|---|---|---|
| `0` | extraction completed | no stage failed — **but this does not guarantee idealization ran**; see below |
| `1` | extraction failed (`ExtractionError`) | at least one movie had a failed stage |
| `2` | bad command-line arguments (argparse) | bad command-line arguments (argparse), **or** a refusal to start: invalid sidecar-supervision values, or two input movies whose basenames collide on one output `.tether` |

Exit code `2` always means nothing was written: either argparse rejected the command line
before `main()` ran, or `tether batch` refused to start. It is safe to fix the arguments
and re-run.

Exit code `1` means the command *ran*. From `tether extract` it is a clean one-line error
on stderr, never a traceback — an unhandled traceback from either subcommand is a bug
worth reporting. From `tether batch` it means the run finished with at least one movie
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
