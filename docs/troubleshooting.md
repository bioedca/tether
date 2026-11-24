---
description: "Find what you saw, such as an error message or an unexpected result, and read what it means and what to do."
---

# Troubleshooting

**Who this page is for.** Anyone whose Tether run did something other than what they expected.
That includes a run that *looked* fine. Use this page to find what went wrong and what to do
next. It assumes you can run a `tether` command and open the application. It does not assume
you know which module of Tether is responsible.

Find your problem in the table below by what you saw. You can also paste your error text into
the find bar of your browser. Each message on this page is copied exactly from the code that
writes it. That is Tether's own `src/` folder, unless the entry says otherwise. The
[Glossary](glossary.md) defines each special term.

Every entry has the same four parts, in this order:

- **What you see.** The exact message, or the sign that you can observe.
- **What it means.** The cause, in one or two sentences.
- **What to do.** Numbered steps, one instruction in each.
- **Why.** A short reason, and a link to the page that explains the subject.

Nothing tells you about a **silent** failure, so the silent failures come first. Each one has a
*Signature* block directly after "What you see". It is a check that you run on your project
file, with the result that confirms the entry.

## Find what you see

| What you see | Entry |
|---|---|
| Every efficiency is the mirror image of what you expect | [Every efficiency is mirrored when donor-side is backwards](#every-efficiency-is-mirrored-when-donor-side-is-backwards) |
| `idealize=deferred` in the batch summary, and exit code `3` | [The batch finished "ok" but nothing was idealized](#the-batch-finished-ok-but-nothing-was-idealized) |
| `correct=done`, but α and γ are NaN on every molecule | [The batch says `correct=done` but no corrections were applied](#the-batch-says-correctdone-but-no-corrections-were-applied) |
| `extract=skipped` after `tether batch --overwrite` | [`tether batch --overwrite` changed nothing](#tether-batch-overwrite-changed-nothing) |
| An RMS residual of about `1e-14` and `low_confidence: False` | [Registration passed the gate with a suspiciously perfect residual](#registration-passed-the-gate-with-a-suspiciously-perfect-residual) |
| `only 0 control-point pair(s) matched` | [Extraction found no molecules](#extraction-found-no-molecules) |
| A molecule count that is far too low or far too high | [Extraction found far fewer, or far more, molecules than expected](#extraction-found-far-fewer-or-far-more-molecules-than-expected) |
| `could not extract from <name>: …` | [The movie will not load](#the-movie-will-not-load) |
| `invalid choice`, or a message that names an option value | [`tether extract` rejects an option value](#tether-extract-rejects-an-option-value) |
| `warning: registration RMS … exceeds the … gate` | [Registration was flagged low-confidence](#registration-was-flagged-low-confidence) |
| `run compute_photobleach() before …`, or a similar `ValueError` | [A correction step refuses: run something else first](#a-correction-step-refuses-run-something-else-first) |
| A `key=` query or overlay that returns no molecules | [A condition query or per-condition overlay returns nothing](#a-condition-query-or-per-condition-overlay-returns-nothing) |
| A plot with fewer molecules than you curated | [A plot shows fewer molecules than you curated](#a-plot-shows-fewer-molecules-than-you-curated) |
| `Open project failed: <msg>` | [The project will not open](#the-project-will-not-open) |
| `tether: command not found`, or *"is not recognized"* | [The terminal does not find `tether`](#the-terminal-does-not-find-tether) |
| `SidecarError: no sidecar interpreter …` | [Idealization cannot find the sidecar interpreter](#idealization-cannot-find-the-sidecar-interpreter) |
| `pkg_resources` in the sidecar error text | [The sidecar starts but tMAVEN cannot import `pkg_resources`](#the-sidecar-starts-but-tmaven-cannot-import-pkg_resources) |
| `sidecar idealization timed out …` or `… failed after 3 restart(s)` | [Idealization times out, or is restarted repeatedly](#idealization-times-out-or-is-restarted-repeatedly) |
| `Deep-LASI ParticleDetectionMode 4 is not supported by Tether` | [A Deep-LASI `.tdat` uses a particle-detection mode Tether does not implement](#a-deep-lasi-tdat-uses-a-particle-detection-mode-tether-does-not-implement) |
| `LockedError: … is locked`, or a read-only banner | [The project is locked by another user or machine](#the-project-is-locked-by-another-user-or-machine) |
| `possible OneDrive/SharePoint conflict copies` | [A warning names a possible OneDrive/SharePoint conflict copy](#a-warning-names-a-possible-onedrivesharepoint-conflict-copy) |
| `coordinates and patches absent; …` | [The spot and overlap views are unavailable](#the-spot-and-overlap-views-are-unavailable) |
| *"Windows protected your PC"*, or *"Not Opened"* on macOS | [The installer is flagged as unsigned](#the-installer-is-flagged-as-unsigned) |
| `torch.cuda.is_available()` is `False` | [The deep classifier does not see the GPU](#the-deep-classifier-does-not-see-the-gpu) |
| A status bar message that went away before you read it | [A status bar message went away before you read it](#a-status-bar-message-went-away-before-you-read-it) |

---

## Data and analysis failures

### Every efficiency is mirrored when donor-side is backwards

**What you see.** The run succeeded, and the molecule counts look right. But the population
histogram is the mirror image of what you expect. A low-FRET construct reads high, and a
high-FRET construct reads low. In every trace, the donor and the acceptor look swapped.

> **Signature.** Read the donor-side check in the project file:
> `/settings/extraction.attrs["profile_json"]` → `donor_side_check` → `verdict`. The value
> `"swapped"` confirms this entry. The check needs at least 15 bleach events. On a movie with
> enough of them, the wrong run also prints this line on standard error:
>
> ```text
>   warning: 27 of 27 bleach events are consistent with swapped donor and acceptor halves; extract this movie again with --donor-side left.
> ```
>
> With fewer bleach events, the check does not warn. `tether extract` prints
> `donor side: not enough bleach events to check the donor side`, and `tether batch` does not
> mark the movie `halves-swapped?`. Then compare two extractions of one movie, one with each
> value. These fields come from a real pair of runs on a **symmetric** 3-spot synthetic movie.
> That is why the two counts match:
>
> | Field | `--donor-side left` | `--donor-side right` |
> |---|---|---|
> | `/settings/extraction.attrs["profile_json"]` → `donor_side` | `"left"` | `"right"` |
> | `/movies/table` → `donor_crop` (1-based `[y1, x1, y2, x2]`) | `[1, 1, 64, 48]` | `[1, 49, 64, 96]` |
> | `/movies/table` → `acceptor_crop` | `[1, 49, 64, 96]` | `[1, 1, 64, 48]` |
> | mean apparent E | `0.1986` | `0.8014` |
>
> Both runs printed the same `Extracted 3 molecule(s) -> …` line, and both exited `0`. Three
> molecules are too few for the donor-side check, so neither run warned.

**What it means.** `--donor-side` named the wrong half of the frame as the donor channel.
Tether accepts both `left` and `right` without an error, so the swapped run completes and every
apparent E becomes `1 − E`.

**What to do.**

1. Read the donor-side check: the `donor side:` or `warning:` line after `tether extract`, or
   the `halves-swapped?` marker in the `tether batch` summary.
2. Check the orientation against the instrument when the check cannot decide. `donor_crop` in
   `/movies/table` is a 1-based `[y1, x1, y2, x2]` box. Its half of the frame must be the half
   that the splitter sends through the donor emission filter.
3. Check the orientation against a trace. Open a molecule that photobleaches. At the
   acceptor-bleach step, the acceptor channel drops to background and the donor channel *rises*.
   If the channel labeled *acceptor* rises, the sides are swapped.
4. Find out whether the run used a `.tmap`. With `--tmap`, Tether ignores `--donor-side` and
   splits the frame at the map's own crop geometry. Tether shows no warning when you pass both
   flags.

    > **Warning.** Write the new extraction to a new output file, or copy the old project file first.
    > `--overwrite` replaces the old project file, with its curation.

5. **A native split.** Extract the movie again with the other `--donor-side` value. With
   `tether batch`, put `donor_side` in an extraction profile and pass `--profile`.
   `tether batch` has no `--donor-side` flag.
6. **A run with `--tmap`.** Check the channel order of the imported map. `--donor-side` cannot
   change it, so extract again with a map whose donor and acceptor crops are in the correct
   order.

**Why.** The crops, the registration map, the molecule keys and the tags all come from the
chosen split, so there is nothing to flip afterward. The molecule count is not a control,
because a swap changes which spots each colocalization rule counts.
[Get `--donor-side` right](cli.md#get-donor-side-right-and-read-the-donor-side-check) explains
the check and the
[donor-anchored rule](glossary.md#donor-anchored-colocalization).

### The batch finished "ok" but nothing was idealized

**What you see.** Exit code `3`, and `/idealization` is empty:

```text
Batch run: 1 movie(s), 1 ok, 0 failed (policy=warn, idealize=on)
  ok    warp_010  extract=done correct=done idealize=deferred
Deferred: idealize in 1 of 1 movie(s); re-run the same command to resume.
Exit code 3: no movie failed, but a requested stage was deferred for at least one movie.
```

> **Signature.** `/settings/batch.attrs["idealize_status"] == "deferred"`, and `/idealization`
> is empty. The JSONL log carries the reason, in this exact on-disk form:
>
> ```text
> {"movie": "(batch)", "stage": "idealize", "status": "deferred", "detail": "sidecar unavailable \u2014 idealization deferred: no sidecar interpreter: pass sidecar_python= or set TETHER_SIDECAR_PYTHON to a Python in an env built from sidecar/conda-lock.yml with tMAVEN installed"}
> ```
>
> Search the log for an ASCII-only part of the line, such as
> `grep '"status": "deferred"' <out-dir>/batch-log.jsonl` or `grep 'idealization deferred' …`.
> Do not search for the em dash. `BatchLog.event` writes each non-ASCII character in escaped
> form, so the em dash is stored as `\u2014`.

**What it means.** `tether batch` found no sidecar interpreter when it started. It recorded the
idealize stage as `deferred`, not `failed`, so no movie failed and the command returned `3`.

**What to do.**

1. Fix the sidecar. See
   [Idealization cannot find the sidecar interpreter](#idealization-cannot-find-the-sidecar-interpreter).
2. Run the same batch command again. Extract and correct report `skipped` from their checkpoints,
   and only idealization runs.
3. Pass `--no-defer` if you want this condition to fail the run. Each movie's idealize stage then
   fails on its own, and the command exits `1`.

With `--no-defer` and `--quiet`, which leaves out the progress lines, the output is:

```text
warp_010 · idealize: failed — no sidecar interpreter: pass sidecar_python= or set TETHER_SIDECAR_PYTHON to a Python in an env built from sidecar/conda-lock.yml with tMAVEN installed
Batch run: 1 movie(s), 0 ok, 1 failed (policy=warn, idealize=on)
  FAIL  warp_010  extract=done correct=done idealize=failed(no sidecar interpreter: pass sidecar_python= or set TETHER_SIDECAR_PYTHON to a Python in an env built from sidecar/conda-lock.yml with tMAVEN installed)
Exit code 1: at least one movie had a failed stage.
```

**Why.** A deferred stage is not a failure, so `n_failed` stays `0` and
`BatchSummary.n_deferred` counts the movie. The reason is in the
`tether batch: idealize deferred - …` progress line before the first movie, unless you passed
`--quiet`. [Progress while the batch runs](cli.md#progress-while-the-batch-runs) shows those
lines.

### The batch says `correct=done` but no corrections were applied

**What you see.** The run reports `correct=done` and exit `0`. But α and γ are NaN on every
molecule, and your efficiencies are not corrected.

> **Signature.** Read three groups of the project file. This dump comes from a real run:
>
> ```text
> /settings/correction : {'apparent_e_only': False, 'n_molecules': 7, 'n_corrected': 0, 'n_manual': 0, 'n_apparent': 7, 'total_failure': True, ...}
> /settings/leakage    : {'alpha': nan, 'withheld': True, 'n_qualifying': 0, 'min_qualifying_traces': 10, ...}
> /settings/gamma      : ABSENT
> ```
>
> `total_failure=True` **with** `apparent_e_only=False` confirms that the correction failed.
> `total_failure=True` with `apparent_e_only=True` is the toggle that a user set on purpose. When
> `/settings/gamma` is absent, no γ pass is current. `/molecules.correction_method` reads
> `apparent-E (corrections unavailable)`, with `correction_confidence = 0.0`. The stage detail
> in `batch-log.jsonl` reads as follows, with the `α` in escaped form:
>
> ```text
> {"movie": "warp16b", "stage": "correct", "status": "done", "detail": "pb 0D/0A; \u03b1 withheld; apparent-E fallback (7 mol)"}
> ```

**What it means.** The movie has no clean acceptor-bleach step, so Tether withheld α and skipped
γ. This is the expected result for such an acquisition, not a fault in Tether.

**What to do.**

1. Accept apparent E. Every population plot shows [apparent E](glossary.md#apparent-e) in any
   case.
2. Or acquire traces that are long enough to show acceptor photobleaching in at least 10
   molecules.

**Why.** `compute_leakage_alpha` withholds α when fewer than
`DEFAULT_MIN_QUALIFYING_TRACES = 10` donor-only tails qualify, and γ cannot run without α. The
overrides `alpha_override` and
`gamma_override` record a factor in the provenance, but plots and exports still show apparent
E, as [Apply the corrections](how-to/apply-corrections.md#3-if-a-factor-was-withheld-or-you-measured-it-elsewhere)
explains. A molecule that fails the gates of γ takes the **dataset median** γ without a
message, as [How Tether estimates gamma](reference/parameters.md#how-tether-estimates-gamma)
says.

### `tether batch --overwrite` changed nothing

**What you see.** You fixed a parameter and ran the batch again with `--overwrite`. The results
are the old ones, reported as `ok`, with exit `3` because idealization was deferred again:

```text
  ok    warp_010  extract=skipped correct=skipped idealize=deferred
```

> **Signature.** The report shows `extract=skipped` and `correct=skipped`.
> `/settings/extraction.attrs["profile_json"]` still holds the old parameters.

**What it means.** The batch runner found completed `/settings/extraction` and
`/settings/correction` checkpoints and skipped both stages. For a completed extraction that
passed the gate, `--overwrite` does not mean "compute it again".

**What to do.**

1. **A project that `--policy fail` rejected.** Run the batch again with
   `--overwrite --policy fail`. The later stages run only if the new extraction passes the gate.
2. **A completed project whose parameters must change.** Move the old project file out of the
   output folder.
3. Extract that movie again with `tether extract --overwrite`. Or run the batch again with an
   extraction profile that holds the new values, and pass `--profile`.

**Why.** Under `--policy fail`, the runner reads the saved `registration_rms_px` and `rms_gate`.
An over-gate result stays `failed` on each resume, unless you pass `--overwrite`. Batch takes the
writer lock of each destination first, as
[How policy `fail` behaves on resume](cli.md#how-policy-fail-behaves-on-resume) explains.

### Registration passed the gate with a suspiciously perfect residual

**What you see.** `/calibration` reports an RMS residual at machine epsilon (`1e-14` or
smaller) and `low_confidence: False`. But you know that the movie is not perfectly registered.

> **Signature.** In `/calibration/<cal-id>.attrs`, check `rms_residual` and
> `n_control_points`. An `rms_residual` of about `1e-14` with `n_control_points` **equal to the
> degrees of freedom of the fit** confirms this entry. That is 2 at `degree: 1`, or 6 at
> `degree: 2`. Both rows below come from synthetic movies whose true donor→acceptor mapping is a
> **cubic** warp, which neither fit can represent. Both ran at the default `--rms-gate`:
>
> ```text
> vacuous : {'rms_residual': 4.24e-14, 'n_control_points': 6, 'gate_px': 0.5, 'degree': 2, 'low_confidence': False, 'source': 'native'}
> genuine : {'rms_residual': 0.502,    'n_control_points': 15, 'gate_px': 0.5, 'degree': 2, 'low_confidence': True,  'source': 'native'}
> ```
>
> The first row is a badly mis-mapped movie that reports a *perfect* fit. `degree: 2` alone
> does **not** clear it.

**What it means.** The gate measures nothing whenever the control points exactly determine the
fit, because the residual of such a fit is zero by construction. That happens at 2 pairs with
`degree: 1` and at 6 pairs with `degree: 2`.

**What to do.**

1. Trust the RMS number only when `n_control_points` is 7 or more. The degree-2 fit is
   over-determined from 7 pairs.
2. Get more pairs. Raise `--pair-tol`, so that Tether keeps the real pairs.
3. Lower `--min-separation`.
4. Try `--detection-mode intensity` with a lower `--detection-threshold`.
5. Do not import a `--tmap` to fix this. With an imported map, Tether skips pairing and leaves
   the residual NaN, so the gate is absent.

**Why.** Tether fits a degree-2 polynomial from 6 pairs and a 4-parameter similarity below 6. At
3 to 5 pairs the residual is real, and a `low_confidence: True` there is a real
[low-confidence registration](glossary.md#rms-gate-low-confidence-registration). Tether drops
the pairs beyond `--pair-tol` without a message, which is how a movie with many spots can end
with 6 pairs.

### Extraction found no molecules

**What you see.** Exit code `1`, and this message:

```text
tether extract: registration failed: only 0 control-point pair(s) matched (need >= 2). Detected 0 donor / 0 acceptor spots; check the channel split, detection sensitivity (--min-separation) or pairing tolerance (--pair-tol).
```

**What it means.** Fewer than 2 control points were left after pairing, so
`tether.project.extract.extract_movie` could not register the two halves. The two counts in the
message tell you which half of the problem you have.

**What to do.**

1. Read the two counts in the message.
2. **`Detected 0 donor / 0 acceptor`.** Check that the movie is a side-by-side dual-channel
   frame and that `--donor-side` matches.
3. Loosen detection. Use `--detection-mode intensity` with a lower `--detection-threshold`, for
   example `0.2`, or a smaller `--min-separation`.
4. **Spots were detected, but no pairs matched.** Raise `--pair-tol`.
5. Or switch to `--prealign similarity`.
6. Or import the real calibration with `--tmap`.

**Why.** `--detection-threshold` is a **fraction of the detection-image maximum**, not an
absolute count, and the `wavelet` mode ignores it. Its default is `0.5` for `intensity` and
`0.98` for `bandpass`. Without `--min-separation`, each mode uses its own default, as
[Detection and extraction](reference/parameters.md#detection-and-extraction) lists.

### Extraction found far fewer, or far more, molecules than expected

**What you see.** `Extracted N molecule(s)`, where *N* is far from what you expect. It is a few
on a crowded field, or thousands on a sparse one. **Tether raises no error**, because it does
not gate the molecule count.

> **Signature.** `/settings/extraction.attrs["profile_json"]` records each tunable exactly:
> `detection_mode`, `detection_threshold`, `min_separation`, `window`, `pair_tol`,
> `coloc_distance`, `colocalization`, `rms_gate` and `donor_side`. Compare it with a run that you
> trust. The parameters differ, not the code.

**What it means.** The detection sensitivity or the colocalization rule does not fit this movie.
Tether also drops each molecule whose full `21 × 21` aperture window would fall outside the
frame in either channel.

**What to do.**

1. Read the `colocalization:` line of `tether extract`. It gives the count for both rules.
2. Run `tether extract --count-only` to print both counts without writing a project.
3. Adjust `--detection-mode`, `--detection-threshold` or `--min-separation`.
4. Extract the movie again.

**Why.** The default rule,
[partner-in-both](glossary.md#partner-in-both-colocalization), keeps a donor spot only when an
acceptor spot is at the mapped position. A movie with many dark or weak acceptors then gives
far fewer molecules. [Donor-anchored](glossary.md#donor-anchored-colocalization) keeps *every*
in-frame donor spot, and detection happens only once, at extraction.

### The movie will not load

**What you see.** One of the messages in the table, after
`could not extract from <name>: …`, with exit `1`. `tether.io.adapters` checks which container
the file is, and `tether.io.movie` reads the TIFF.

| Message | What it means | What to do |
|---|---|---|
| `… is not memory-mappable; the lazy reader needs an uncompressed, contiguous TIFF (the reference movie format, PRD Appendix A).` | A compressed (lossless, for example LZW) or tiled TIFF. This is the most frequent case. | Save it again without compression. In ImageJ, use *Save As → Tiff*. |
| `…: the frames are stored with the JPEG codec, which can be lossy. …` | A codec that can discard intensity information: JPEG, JPEG 2000, JPEG XR, JPEG XL, WebP or LERC. The TIFF tags do not show whether the mode was lossless, so Tether refuses it. | Export the movie again without compression, from the original acquisition. |
| `…: the pixels are palette (color-map) indices, not intensities.` or `…: each pixel has 3 samples (for example RGB); …` | The file holds color images, not one intensity for each pixel. It is often a screenshot or an export for a slide. | Export the raw single-channel stack. |
| `…: expected a 3-D movie (frames, height, width), got shape (64, 64) (axes 'YX').` | A single-page TIFF. It is often a maximum projection that was saved in place of the stack. | Open the stack in Tether. |
| `…: the name says TIFF, but the content is not a TIFF file …` | The file is named `.tif` but is not a TIFF. It was renamed, it is corrupt, or the copy did not finish. | Copy it again from the acquisition computer. |
| `…: Tether does not read this movie container (.nd2). Tether reads: TIFF (.tif, .tiff). …` | A vendor container, such as `.nd2`, `.czi`, `.sif` or `.vsi`. The message names the Bio-Formats route. | Export the movie to an uncompressed TIFF. |
| `movie changed during extraction: <path>` | The size or the modification time of the file changed during the run. Cloud sync, or an acquisition that is still writing, does this. | Copy the movie to a local disk, or wait until the acquisition finishes. |
| `movie too narrow (1 px) to split into two channels` | The frame cannot be cut into two halves. | Open the dual-channel movie, not a single channel. |

**What to do.**

1. Find the inner message in the table.
2. Do the step in its last column.
3. **A `TiffFileError` from `tifffile`.** Copy the file again. A damaged TIFF can fail inside
   `tifffile`, and that message is not Tether's.

**Why.** `extract_movie` wraps each error that is not an `ExtractionError`, and it keeps the
inner text unchanged. A lossy codec cannot be undone, so saving the file again cannot bring the
values back. A movie of odd width drops its last column without a message, so that both halves
are the same size.

### `tether extract` rejects an option value

**What you see.** One line on standard error and exit `1`, with a message from the table. For
`--donor-side`, `--detection-mode` and `--prealign`, you see the usage line,
`invalid choice: '<value>' (choose from …)` and exit `2`, for example:

```text
tether extract: error: argument --donor-side: invalid choice: 'middle' (choose from left, right)
```

| Message | What to do |
|---|---|
| `donor_side must be 'left' or 'right', got 'middle'` | Use `left` or `right`. Only a library call gets this message, because the CLI refuses the value when it reads the command line. |
| `detection_mode must be one of ('wavelet', 'intensity', 'bandpass'), got 'gaussian'` | Use one of the three modes. Only a library call gets this message. |
| `detection_threshold must be in [0, 1) (a fraction of the detection-image max), got 1.5` | Give a fraction, not a count. |
| `min_separation must be > 0, got -1.0` | Leave the option out to get the default of each mode. |
| `window must be a positive odd integer, got 20` | Give an odd aperture window size. The default is 21. |
| `ring (2*8.0) does not fit in a 11px window` | Make `--window` larger. |
| `pair_tol must be > 0, got 0.0` | Give a positive value. The same rule applies to `coloc_distance` and `rms_gate`. |
| `output exists: <path> (use overwrite=True / --overwrite)` | Pass `--overwrite`. The message names the library keyword first. |
| `--detection-mode/--detection-threshold cannot be combined with --tdat (the .tdat supplies the detection mode); pass one or the other` | Remove one of the two. |

**What it means.** `ExtractOptions.__post_init__` in `tether.project.extract` refused a value.
Tether checks the values before it opens a file, so you see a message and not a traceback.

**What to do.**

1. Find the message in the table.
2. Do the step in its last column.
3. Run the command again.

**Why.** Option checks run **before** any check that a file exists, and `movie not found` comes
before `tmap not found` and `tdat not found`. So `tether extract nope.tif -o x --window 20`
reports the aperture window error, not the missing movie.

### Registration was flagged low-confidence

**What you see.** This warning on standard error, while the run succeeds with **exit code `0`**:

```text
  warning: registration RMS 0.368 px exceeds the 0.2 px gate (7 control points); molecules tagged 'low-confidence-registration'.
Extracted 5 molecule(s) -> …\warp2.tether
```

The library form of the same result (`tether.imaging.calibrate`) reads:

```text
registration RMS residual 0.368 px exceeds the 0.200 px gate (channels 0->1, 7 control points, source native): calibration flagged low-confidence; molecules will be tagged 'low-confidence-registration'.
```

> **Signature.** `/calibration/<cal-id>.attrs` holds `low_confidence: True`, with
> `rms_residual`, `n_control_points`, `gate_px`, `degree` and `source`. Each row of
> `/molecules.tags` holds `low-confidence-registration`.
>
> **In `tether batch` the warning does not show.** The end-of-run report prints the stage
> status only. The flag is only in the JSONL `detail` (`"…; low-confidence registration (flagged)"`).

**What it means.** The RMS residual of the fit was larger than `--rms-gate`, which is
`DEFAULT_RMS_GATE_PX = 0.5` px by default. Under the default `warn` policy, Tether tags the
molecules and keeps them.

**What to do.**

1. Look at the registration. Nothing later in Tether filters on the tag.
2. Get more control points, or use `--prealign similarity`.
3. Raise `--pair-tol`, so that Tether keeps the real pairs.
4. Or import a dedicated calibration with `--tmap`.
5. **`tether batch`.** Pass `--policy fail` to make an over-gate movie fail and exit `1`.

**Why.** No banner in the application and no analysis step excludes a tagged molecule. An
imported `.tmap` leaves the residual NaN, so it **can never trip the gate**. On an imported map,
a clean gate means that Tether measured nothing, not that the map is good.

### A correction step refuses: run something else first

**What you see.** One of these `ValueError` messages from `tether.project.gamma` or
`tether.project.leakage`:

```text
/molecules row 0 has no photobleach frames (bleach_frames=(-1, -1), the undetected sentinel); run compute_photobleach() before compute_leakage_alpha()
/molecules row 0 has no photobleach frames (bleach_frames=(-1, -1), the undetected sentinel); run compute_photobleach() before compute_gamma()
/molecules row 0 has no leakage factor (alpha is NaN); run compute_leakage_alpha() before compute_gamma()
```

Two related messages:

- `project has no /traces/<layer>; run extraction first`. This is a `KeyError` with the same
  text in `tether.project.photobleach`, `.leakage` and `.gamma`.
- `intensity_quantity must be one of ['corrected', 'raw'], got 'bogus'`. `tether.project.export`
  gives the same condition as
  `unknown intensity_quantity 'bogus'; expected one of ['corrected', 'raw']`.

**What it means.** You called a step of the correction before the step that it needs. The
message names the step to run first.

**What to do.**

1. **`project has no /traces/<layer>`.** Extract the movie first. Each step of the correction
   needs the traces.
2. **`intensity_quantity`.** Use `corrected` or `raw`. Only those two layers exist.
3. **A message that names a step to run first.** Run `compute_photobleach`.
4. Run `compute_leakage_alpha`.
5. Run `compute_gamma`.

**Why.** `(-1, -1)` means that Tether has not computed photobleaching yet. `alpha` NaN means that
it has not computed leakage yet. `tether batch` runs the steps in order for you, so you see these
messages only from the library, as in
[Run the four steps in order](how-to/apply-corrections.md#1-run-the-four-steps-in-order).

### A condition query or per-condition overlay returns nothing

**What you see.** `query_molecules(project, key={"ligand": "tRNA"})` matches zero molecules. Or
a per-condition histogram overlay **called with that same `key=` filter** gives no curves. The
plain population histogram of the same project file is correct.

> **Signature.** Call the overlay without `key=`. It groups on the raw `condition_id`, so if it
> is also empty, this entry does not apply. On a freshly batch-extracted 7-molecule project file
> (`Tbox_Cy3Cy5_tRNA_100nM_25C.tether`), each probe gave these counts before and after the sync:
>
> ```text
>                                before sync   after sync
> plain population histogram N :      7             7
> query (no filter) matches    :      7             7
> query key={'ligand':'tRNA'}  :      0             7
> per-condition overlay curves :      1             1
> overlay + key={'ligand':...} :      0             1
> sync_conditions -> created_ids=('cond-6115d12da682',), n_unresolved=0
> ```
>
> Only the two rows with a `key` filter changed.

**What it means.** A `key` filter in `tether.analysis.query` sees only the `/conditions` rows that
exist. Until `tether.project.conditions.sync_conditions()` runs, a molecule's provisional
`condition_id` has no row, so the filter excludes the molecule.

**What to do.**

1. Call `sync_conditions(path)` once for each project file, before you use a `key` filter.
2. Call `validate_conditions(path)` to see the IDs that still have no row.

**Why.** `per_condition_apparent_e_histograms` passes `key` to `query_molecules`, so it has the
same rule. `n_unresolved=0` means only that each **non-empty** ID has a row. A condition query
never returns a molecule with an empty `condition_id`, and `sync_conditions` cannot repair one,
as the [condition](glossary.md#condition) entry explains.

<a id="a-plot-shows-fewer-molecules-than-i-curated"></a>

### A plot shows fewer molecules than you curated

**What you see.** A plot of a project shows fewer molecules than you curated.

**What it means.** By default, every group-B and group-C function excludes the **stale**
molecules and the **rejected** molecules. A molecule is stale when its input trace, its analysis
window or its applied α or γ changed after the model fit.

**What to do.**

> **Warning.** Expect every molecule of the project to go stale when you estimate α again. The α
> estimator is project-wide.

1. Pass `include_stale=True` to show the stale molecules.
2. Pass `include_rejected=True` to show the rejected molecules.
3. Or idealize the molecules again, so that each model is fresh.

**Why.** `compute_leakage_alpha` fits one dataset median over every molecule that it can analyze,
with no split by condition. A change of the γ median makes stale only the molecules that took
the fallback. [Staleness](glossary.md#staleness-stale-fresh-live) explains the rules.

### The project will not open

**What you see.** One of these messages from `tether.project.core.Project.open` or
`tether.io.schema`. In the application, the status bar shows `Open project failed: <msg>`, and
the project that was open stays open.

| Message | What it means | What to do |
|---|---|---|
| `no such .tether project: <path>` | The path has a typing error, or the file moved. | Give the correct path. |
| `file schema_version 99 is newer than this app's 1; refusing to open (PRD section 5.4).` | A newer Tether wrote the file. | **Upgrade Tether.** Do not try to edit the file. |
| `<path> is not a .tether project (format marker=None)` | The file is HDF5 but not a project file, for example an SMD, a model or a `.mat`. | Open the correct file. |
| `<path> is not a readable .tether HDF5 project` | The file is not HDF5, or it is truncated. | Restore the file from a backup, and make sure that the copy completed. |
| `<path> is not a complete .tether project; missing: /movies, /molecules, …` | A half-written project file. The message names each missing group. | Extract the movie again. |

**What to do.**

1. Find the message in the table.
2. Do the step in its last column.

**Why.** `tether extract` writes atomically, with a temporary file and a replace, so a
half-written project file comes from an interruption outside Tether. A batch **resume** refuses a
newer-schema output even with `--overwrite`. A foreign or half-written HDF5 file goes through the
normal path, and the batch tries the movie again.

---

## Setup and environment failures

### The terminal does not find `tether`

**What you see.** You type `tether --version` after you install, and the terminal does not know
the command. These messages come from the terminal, not from Tether:

- `tether: command not found` on Linux and macOS,
- *"is not recognized as the name of a cmdlet"* in Windows PowerShell,
- *"is not recognized as an internal or external command"* in a Windows Command Prompt.

**What it means.** The installer does not change your `PATH`, so a new terminal does not know
the folder of Tether's launchers. The installer is not broken.

**What to do.**

1. **Windows, from the next release.** Open the Start menu, and choose **Tether Command Prompt**.
2. Type the command there. That console finds `tether` while it is open, and your `PATH` does
   not change.
3. **Any system.** Run the launcher by its full path, or put the launcher folder on your `PATH`
   once. [Install Tether, step 4](install.md#4-open-a-terminal-where-tether-works) gives both.

**Why.** The launchers are in the install folder.
`packaging/scripts/post_install.sh` or `post_install.bat` writes them, and
[ADR-0051](adr/0051-installed-app-launch-surface.md) records why the installer leaves `PATH`
alone.

### Idealization cannot find the sidecar interpreter

**What you see.**

```text
SidecarError: no sidecar interpreter: pass sidecar_python= or set TETHER_SIDECAR_PYTHON to a Python in an env built from sidecar/conda-lock.yml with tMAVEN installed
```

Or, when the path is set but wrong:

```text
SidecarError: sidecar interpreter does not exist: C:\nope\python.exe
```

**What it means.** `resolve_sidecar_python` in `tether.idealize.driver` found no interpreter. It
tries the `sidecar_python=` argument, then `$TETHER_SIDECAR_PYTHON`, then the installer's
`envs/sidecar` next to `sys.prefix`.

**What to do.**

1. **A source checkout.** Run `python scripts/setup_sidecar.py`. It makes the environment from
   `sidecar/conda-lock.yml`, installs the pinned tMAVEN, runs a liveness probe and prints the
   line that points Tether at the interpreter.
2. Set `TETHER_SIDECAR_PYTHON` to that interpreter, or pass `--sidecar-python` to
   `tether batch`.
3. **An installed application.** Check that the `envs/sidecar` folder is in the install folder.
   If you see this error, that environment is missing or was moved.

**Why.** Both errors are **deterministic** (`transient=False`), so the supervisor stops at once
and keeps its restart budget. The third step works without an activated environment, because a
menu shortcut does not run the conda `activate.d` hook. [Guided sidecar setup](idealize/standalone-tmaven-handoff.md#guided-sidecar-setup)
has the full procedure.

### The sidecar starts but tMAVEN cannot import `pkg_resources`

**What you see.** Idealization fails with an error that the sidecar reports. The stderr tail in
Tether's wrapper message names `pkg_resources`. The wrapper (`tether.idealize.driver`) looks
like this:

```text
sidecar idealization failed (exit 1): <the sidecar's own error>
--- stderr (tail) ---
…
```

**What it means.** The sidecar environment has setuptools 82 or newer, which removed
`pkg_resources`. tMAVEN imports it at run time, in `tmaven/maven.py`, so `maven_class` cannot
start.

**What to do.**

1. **A sidecar from a source checkout.** Run `scripts/setup_sidecar.py` again. It applies the
   `setuptools<81` pin for you.
2. **A sidecar from the installer.** Report the error. The installer applies the pin, so the
   environment changed after installation.
3. **An environment that you made yourself, or an upgraded setuptools in `envs/sidecar`.**
   Install the pin with the sidecar's own interpreter. This needs the network:

    <!-- markdownlint-disable MD046 -->

        # Linux / macOS
        <install-prefix>/envs/sidecar/bin/python -m pip install "setuptools<81"

        # Windows
        <install-prefix>\envs\sidecar\python.exe -m pip install "setuptools<81"

    <!-- markdownlint-enable MD046 -->

4. Run the liveness probe or the idealization again.

**Why.** setuptools **deprecated `pkg_resources` by 80.9.0** and **removed it in 82.0.0**.
`sidecar/conda-lock.yml` resolves setuptools `82.0.1`, and the pin with its SHA-256 is written
only in **`packaging/setuptools-compatibility.txt`**. Tether's base environment does not import
`pkg_resources`, as [Guided sidecar setup](idealize/standalone-tmaven-handoff.md#guided-sidecar-setup)
explains.

### Idealization times out, or is restarted repeatedly

**What you see.** From `tether.idealize.driver`:

```text
sidecar idealization timed out after 1800.0s
--- stderr (tail) ---
```

Or, after the supervisor used all its restarts (`tether.idealize.supervisor`):

```text
sidecar idealization failed after 3 restart(s); last error: …
```

**What it means.** The fit took longer than the timeout of 1800 s, or the sidecar failed again
after 3 restarts. The first `vbconhmm` fit of a session is much slower than the rest, because JIT
compilation takes most of its time.

**What to do.**

1. Raise `--sidecar-timeout`.
2. Raise `--max-restarts` on a computer where the sidecar sometimes stops. `0` turns restarts off.
3. **An error that the sidecar reports.** Look at the data or the model choice. A second run
   gives the same error.

**Why.** Tether retries a timeout or a crash, because it is **transient**. It does not retry an
error that the sidecar reports, because it is **deterministic**.
[Transient vs deterministic sidecar failure](glossary.md#transient-vs-deterministic-sidecar-failure)
explains the difference.

### A Deep-LASI `.tdat` uses a particle-detection mode Tether does not implement

**What you see.**

```text
tether extract: could not use --tdat mode4.tdat: Deep-LASI ParticleDetectionMode 4 is not supported by Tether (only [1, 2, 3] = wavelet/intensity/bandpass; modes 4 'local-variance' and 5 'ZMW intensity' are not ported)
```

A related message says that the file is not a TIRFdata container. It prints the root keys of
the file:

```text
tether extract: could not use --tdat notdat.tdat: 'notdat.tdat' is not a Deep-LASI TIRFdata .tdat (no 'temp' struct; root keys: ['junk'])
```

**What it means.** `tether.io.tdat` read a detection mode that Tether does not implement, or a
file that is not a `.tdat`. `tether.project.extract` wraps the message.

**What to do.**

1. Run the command again **without** `--tdat`.
2. Choose a supported `--detection-mode` yourself.

**Why.** Tether refuses and does not use a different mode, so an import cannot detect with the
wrong method ([ADR-0021](adr/0021-particle-detection-modes.md)). If `tether.io.tdat` cannot
decode the MCOS threshold of a `.tdat`, it returns `None` without an error. The detector then
keeps the threshold from the CLI or the library.

### The project is locked by another user or machine

**What you see.** In the application, the project opens read-only, and the read-only banner of
the Browser names the case. Or one of these messages from `tether.project.lock`:

```text
LockedError: <path>.tether.lock is locked: held by alice@OTHERPC (pid 424242, acquired 2026-07-21T02:33:13.661070+00:00)
LockedError: <path>.tether.lock is locked: held by alice@OTHERPC (pid 424242, acquired 2020-01-01T00:00:00+00:00; stale)
LockedError: <path>.tether.lock is locked: the lock file is corrupt (unknown owner)
CorruptLockError: unparseable lock file: <path>.tether.lock
```

**What it means.** Another writer holds the lock file of the project, or held it and stopped.
The message and the banner tell you which case you have, and the banner shows an action button
only when the action is safe.

**What to do.**

> **Warning.** Do not delete the `.tether.lock` file by hand. Use the action that the banner
> shows.

1. **The lock names this computer, your login and a Tether that no longer runs.** Click
   **Recover**. Tether takes the lock and opens the project again, writable. No other writer
   can lose work here, so Tether asks for no confirmation.
2. **The lock names a Tether that still runs on this computer.** Close the other application
   window. Tether checks that process about once a minute. When it releases the lock, the
   banner says so, and you can open the project again to write. If it stops without releasing
   the lock, the banner changes to **Recover**.
3. **No `; stale` suffix, from another computer or another login.** Close the other Tether
   application window, or ask the user of the named host to close it. The banner says when a
   takeover becomes possible.

    > **Warning.** Make sure that the holder saved their work before you take over. Tether does
    > not merge the holder's unsaved work, so that work can be lost.

4. **`; stale`.** Click **Take over…**, and type the project file name when Tether asks.
5. **`the lock file is corrupt (unknown owner)`.** Wait until the lock file is older than the
   staleness timeout of about 30 minutes. Then click **Take over…**.
6. **You must work while the project is locked.** Open the project read-only, and curate into
   your own split or subset `.tether`. Merge it back later on `molecule_key`.
7. **You want to know later whether someone took the lock.** Open the project writable, and
   call `Project.read_lock_takeovers()`, as in this example:

```python
from tether.project.core import Project

for row in Project.open("exp.tether").read_lock_takeovers():
    print(row["timestamp"], row["action"], row["ousted_user"], row["ousted_host"])
```

Some messages need a step outside the banner:

1. **`PermissionError: ... lock lifecycle guard is not writable by this user`.** Ask the owner
   of `<path>.tether.lock.guard` to open the project in Tether once. That attempts the fix.
2. **The error stays.** The owner runs `chgrp <lab group> <path>.tether.lock.guard`. `<lab group>`
   is the group of the project folder.
3. Then the owner runs `chmod g+rw <path>.tether.lock.guard`.
4. **`cannot create the per-user lock directory`.** Fix the permissions of your per-user lock
   folder, or set `XDG_STATE_HOME` (Linux, macOS) to a folder that you can write. The folder is
   `%LOCALAPPDATA%\tether\locks` on Windows, `~/.local/state/tether/locks` on Linux and
   `~/Library/Caches/tether/locks` on macOS.
5. **`is locked` from `tether extract --overwrite`, `tether batch --overwrite` or the Deep-LASI
   import.** Close the project in each application window, also in the same program. Then run
   the command again.
6. **`... needs its writer lock` from a script.** Hold `tether.project.lock.publish_lock` on
   the destination, and pass the check that it yields as `publish_guard`. `extract_movie` raises
   it as an `ExtractionError`, and `reconstruct_project` and `import_analysis_only_project` as a
   `PublishGuardRequiredError`.
7. **`is locked` on a second name of one file.** Close the first name. A hard link gives one
   project file two names, and for your user Tether treats both names as one file.
   **Recover** and **Take over…** work through either name, and a takeover takes both names.
8. **A script that must act on a lock.** Call `tether.project.lock.lock_recovery` to read the
   case, and `tether.project.lock.recover_lock` to act on it.
9. Then record the takeover with `Project.record_lock_takeover(action, taken, ousted)`, after the
   script has the lock.

> **Warning.** Give a shared project file one name only. Two users who open one file through two
> hard-link names do not stop each other, because each user has their own record of the names.

**Why.** Tether judges a lock from another computer by the clock, because it cannot probe a
remote process ID ([ADR-0096](adr/0096-the-lock-guard-is-shared-with-directory-writers.md)
explains the guard file). Both actions check the lock again just before they take it. Each
successful takeover writes a row to `/settings/lock_takeovers`, which the
[format reference](reference/tether-format.md#settings-provenance-of-each-pipeline-stage)
describes, and a failed write shows `the takeover record was not written`.

### A warning names a possible OneDrive/SharePoint conflict copy

**What you see.** When you open a project, the Browser of the application shows a caution banner.
Or `tether extract`, `tether batch`, `tether info`, `tether metadata` or `tether export` writes a
line with the same text on standard error:

```text
Warning: 2 possible OneDrive/SharePoint conflict copies beside exp.tether: exp (1).tether, exp-DESKTOP-AB12.tether. Tether does not merge or delete them; compare and reconcile by hand.
```

**What it means.** The folder of the project holds a file whose name matches a sync-conflict
copy of the project file. Tether matches by name only, so the list also holds a file that you
named that way on purpose, for example `exp-donoronly.tether`.

**What to do.**

1. Open each file in Tether, or compare their modification times.
2. Find the file that holds the work that you want to keep.

    > **Warning.** Make sure that you keep the file with your work before you delete one. Tether
    > never merges a conflict copy, so a deleted copy is gone.

3. Move or delete the other file yourself, outside Tether.

**Why.** OneDrive writes `<stem>-<COMPUTERNAME>.tether` when a second computer saved a change at
the same time. The form `<stem> (<...>).tether` is the numbered and "conflicted copy" form.
Tether finds these copies, but it does not block the open and does not change a file, as
[Sync-conflict copies beside a project](cli.md#sync-conflict-copies-beside-a-project) explains.

### The spot and overlap views are unavailable

**What you see.** A project opens and you can curate it. The histograms and idealization work.
But the movie panel and the spot and overlap view of each molecule are not available, and the
application shows:

```text
coordinates and patches absent; movie round-trip and spot/overlap views unavailable
```

**What it means.** This is an **analysis-only** import, and not a failure. Tether made the
project file from an SMD or a `…-donc-accc-w.txt`, with no `.tdat` or `.mat` to give pixel
coordinates.

**What to do.**

1. Import the data again with a coordinate source: the movie **and** the `.mat`. The
   reconstruction path needs both.
2. Read the reason if the import wizard refuses, for example
   `'<key>' cannot reconstruct: no movie and no .mat trace source`.

**Why.** The view needs coordinates, so `tether.gui.shell` does not connect it, and every
molecule has the `round-trip-unavailable` tag
([ADR-0046](adr/0046-analysis-only-smd-import.md)). The
[coordinate-availability matrix](io/legacy-import.md#coordinate-availability-matrix) shows
which files give coordinates.

### The installer is flagged as unsigned

**What you see.** Windows SmartScreen or macOS Gatekeeper warns that the installer comes from an
unidentified developer.

**What it means.** **No Tether installer is OS-code-signed**, and for 1.0 none will be. The
warning is expected, and it does not mean that the download failed or that someone changed the
file.

**What to do.**

1. Verify the file against the release's `SHA256SUMS-<platform>.txt`. Use `sha256sum -c` on
   Linux, `shasum -a 256 -c` on macOS or `Get-FileHash` on Windows.
2. Verify the file against the **build-provenance attestation**. This is the stronger check,
   because it binds the file to the workflow, the repository and the tag that built it:

    <!-- markdownlint-disable MD046 -->

        gh attestation verify --repo bioedca/tether Tether-<version>-<platform>.<ext>

    <!-- markdownlint-enable MD046 -->

3. **Windows.** When SmartScreen shows *"Windows protected your PC"*, choose **More info** →
   **Run anyway**.
4. **macOS.** Double-click the `.pkg`. macOS shows *"Tether-….pkg" Not Opened* and *Apple
   could not verify "Tether-….pkg" is free of malware that may harm your Mac or compromise your
   privacy.* (macOS 26.6.2, November 2026).
5. **macOS.** Open **System Settings → Privacy & Security**, and scroll to **Security**.
6. **macOS.** Click **Open Anyway** within about one hour, and authenticate as an administrator.
   The button shows only *after* macOS blocked the file. After that hour, open the file again
   to show the button again.
7. **Linux.** Run the `.sh` installer after you verify it. No OS trust prompt stops it.

**Why.** The OS prompt cannot tell you whether the file is genuine, and the absence of a warning
is not evidence that the file is signed. The Control-click → **Open** shortcut **no longer
works** since macOS Sequoia. [Why the installers are unsigned](install.md#why-the-installers-are-unsigned)
and [ADR-0059](adr/0059-ship-v1-unsigned-with-provenance-as-the-integrity-anchor.md) explain the
decision, and how [sponsoring Tether](https://github.com/sponsors/bioedca) helps pay for signing.

#### If macOS will not open the `.pkg`

**What you see.** The **Open Anyway** button does not show for the installer, or macOS still
refuses the `.pkg`.

**What it means.** Apple documents **Open Anyway** for *applications*, and Tether ships a `.pkg`.
On macOS 26.6.2, the command line installed a downloaded `.pkg` with its quarantine attribute
and asked nothing.

**What to do.**

1. Verify the file with the checksum and the attestation, as in the entry above.
2. Run this command in the folder of the download:

    <!-- markdownlint-disable MD046 -->

        installer -pkg Tether-<version>-MacOSX-<arch>.pkg -target CurrentUserHomeDirectory

    <!-- markdownlint-enable MD046 -->

3. **macOS still refuses.** Remove the quarantine attribute, and then run the command of step 2
   again:

    <!-- markdownlint-disable MD046 -->

        xattr -d com.apple.quarantine Tether-<version>-MacOSX-<arch>.pkg

    <!-- markdownlint-enable MD046 -->

4. **These steps do not help.** [Report what you saw](https://github.com/bioedca/tether/issues),
   with your macOS version.

**Why.** The command installs into `~/Tether` with no administrator password, and Tether's macOS
build check runs it on each packaging build (`macos-latest` and `macos-15-intel`). The checksum
and the attestation show **authenticity**, not safety: provenance "does not assert the software
is safe", as [ADR-0059](adr/0059-ship-v1-unsigned-with-provenance-as-the-integrity-anchor.md)
says. So when you remove the quarantine attribute, you decide to trust the software.

#### If macOS will not open Tether.app

**What you see.** macOS blocks the first start of **Tether** (`Tether.app`), nothing happens
when you open it, or it is not in Launchpad or Spotlight.

**What it means.** From the next release, the macOS installer adds `Tether.app` to
`~/Applications`, or to `/Applications` for an install outside your home folder
([ADR-0091](adr/0091-macos-launcher-bundle.md)). Like the installer, it is not signed.

**What to do.**

1. **macOS blocks the first start.** Open **Tether** once.
2. Open **System Settings → Privacy & Security**, and click **Open Anyway** under **Security**.
   You do this one time, and later starts open directly.
3. **Nothing happens, with no application window and no message.** Make sure that the installer
   matches your Mac. The `osx-arm64` installer is for Apple silicon. The `osx-64` installer is
   for Intel, and on Apple silicon it needs Rosetta 2.
4. **Tether is not in Launchpad or Spotlight.** Wait some minutes. On a test Mac, Spotlight
   listed **Tether** about ten minutes after the install.
5. Meanwhile, open the `Applications` folder of your home folder in Finder.
6. **The application does not open.** Run `tether-gui` in Terminal. It starts the same
   application and shows any error text.

**Why.** The installer writes the application on your Mac, so it does not have the quarantine
attribute of a download. On a test Mac with macOS 26.6.2, it opened at the first start with no
dialog. `Tether.app` asks macOS to start it as the architecture of its installer, and macOS does
not install Rosetta 2 by default.

### The deep classifier does not see the GPU

**What you see.** `torch.cuda.is_available()` is `False`, or the CUDA runtime fails to start, in
the optional `deep/` environment.

**What it means.** The installed PyTorch wheel has no CUDA, or its CUDA channel (`cu124`,
`cu126` or `cu128`) does not match the NVIDIA driver.

**What to do.**

1. Find the channel that matches your driver with the PyTorch *Start Locally* selector.
2. Install the wheel of that channel.

**Why.** The committed `deep/conda-lock.yml` is **CPU-only on purpose**, so that CI stays
reproducible ([ADR-0047](adr/0047-deep-model-optional-stack-and-dataset.md)). The CUDA wheel
contains the CUDA runtime, so you need only an NVIDIA **driver** and no system CUDA toolkit. The
[Deep trace classifier](ml/deep-classifier.md#gpu-cuda-setup) page has the full instructions
and the reference GPU minimum.

<a id="i-missed-a-message-in-the-status-bar"></a>

### A status bar message went away before you read it

**What you see.** The desktop application showed a message in the status bar at the bottom of
the main window. The next action replaced it before you read it.

**What it means.** The status bar shows one message at a time. The **Messages** panel and the
log file `tether.log` keep the earlier ones.

**What to do.**

1. Open the panel with **View → Messages**. It keeps the latest 2,000 messages, with their time
   and their level: information, warning or error.
2. Select the rows that you want, or no row for all of them.
3. Press **Copy**. The messages go to the clipboard as text, ready for a bug report.
4. Or open the folder of the log file with **Help → Open log folder**. The folder is one of
   these:
    - Windows: `%LOCALAPPDATA%\MondragonLab\Tether\logs\`
    - macOS: `~/Library/Application Support/MondragonLab/Tether/logs/`
    - Linux: `~/.local/share/MondragonLab/Tether/logs/`, or under `$XDG_DATA_HOME` when that
      variable is set
5. Set the environment variable `TETHER_LOG_DIR` to move the log folder.
6. Look at the right end of the status bar for a running background task. It shows the name of
   the task, its progress and, when the task can stop, a **Cancel** button.

**Why.** A warning or an error stays in the status bar until you press **Dismiss** beside it, or
until the next warning or error. A later information line, such as `Molecule mol-3 (4/40)`,
does not hide it. The one-click idealize has no **Cancel**, because its sidecar may still write
to the project, and the [privacy page](privacy.md#the-desktop-application) says what the log
file holds.

---

## Exit codes, and where the details go

| Command | `0` | `1` | `2` |
|---|---|---|---|
| `tether extract` | success — **including a low-confidence-registration warning** | the run failed; the message is on stderr as `tether extract: <msg>` | argparse rejected the command line |
| `tether batch` | every requested stage ran for every movie (`not-requested` and `warning` included) | at least one movie had a failed stage | argparse rejected the command line, **or** the run refused to start (a movie argument that gives no movie, two movies no folder name tells apart, a bad supervision argument) |
| `tether export` | every export was written | at least one export failed; each failure is one `tether export <form>: <msg>` line on stderr | argparse rejected the command line, **or** the export refused to start (two projects mapping to one export, `-o` a file where a folder is needed) |
| `tether` (no subcommand) | prints help | — | an unknown subcommand or top-level flag |

`tether batch` has a fourth code, `3`: no movie failed, and at least one requested stage was
`deferred`. `1` takes precedence over `3`. See
[The batch finished "ok" but nothing was idealized](#the-batch-finished-ok-but-nothing-was-idealized).

Two notes on that table:

- **`2` is argparse, not Tether.** A missing or unknown option never reaches Tether's own
  validation: `tether extract movie.tif` without the required `-o/--output` prints a usage
  block and exits `2`, as does any unrecognized flag or unknown subcommand. A *bad value* for
  a known option is different — that is Tether's own `ExtractOptions.__post_init__` check, one
  line on stderr and exit `1`.
- **`blocked` never appears on an exit-`0` run.** A stage is recorded `blocked` only when its
  upstream stage was not `done`/`skipped`, and the only way upstream lands there is `failed`
  (or `blocked`, which itself implies a `failed` further up). Any `blocked` therefore travels
  with a `failed` in the same movie, which flips `MovieResult.ok` and exits `1`.

Two things the console deliberately does not print, and where to find them instead:

- **Stage details.** `tether batch`'s end-of-run report prints `stage=status` and appends the
  error text only for a `failed` stage. Everything else — the over-gate flag, the α-withheld
  fallback, the deferral reason — lives in the **JSONL structured log**, whose path is echoed
  to stderr on every run (`<out-dir>/batch-log.jsonl` by default, or `--log`).
- **Movies with one file name.** Two input movies with the same file name, such as
  `movie_010.tif` in two condition folders, get their parent folder in front of their output
  name: `condA__movie_010.tether` and `condB__movie_010.tether`. The structured log records
  each movie and its output first, and `--dry-run` prints them before anything runs. Only
  two names that no folder tells apart, such as `A.tif` and `a.tif` in one folder, are
  refused up front with exit `2`. See [Output names when two movies have the same file
  name](cli.md#output-names-when-two-movies-have-the-same-file-name).
