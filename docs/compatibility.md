# Does Tether fit my data?

**Who this page is for.** Anyone deciding whether to install Tether at all. It describes
what an acquisition has to look like for Tether to read it, what Tether deliberately does
not do, and roughly what a dataset costs in time and disk. Read it before you download an
installer — if your experiment is in the "does not do" list below, nothing later on this
site will change that.

## The short version

Tether reads **uncompressed multi-page TIFF movies from a single camera whose frame is
split side by side, down the vertical midline, into a left and a right half** — one donor
channel, one acceptor channel — recorded with **one excitation laser** and **two colours**.
That is the dual-view TIRF geometry the whole pipeline is built around. A top/bottom
(stacked) dual-view export does not fit native extraction: the built-in splitter cuts on
width only, so it would slice each channel in two rather than separating them. (If you have
a Deep-LASI `.tmap` whose per-channel crops describe the stacked tiles, extracting with
`--tmap` splits at those crops instead — but that combination is untested, so treat it as
unsupported.)

If that describes your setup, Tether fits. If any of *ALEX/PIE*, *stoichiometry*,
*three-colour*, or *confocal point detectors* describes your setup, it does not — see
[What Tether does not do](#what-tether-does-not-do). If your movies are in a vendor
container (`.nd2`, `.czi`, `.sif`), export them to uncompressed TIFF first and Tether will
read them.

## Movie files

| Requirement | Detail |
|---|---|
| Container | multi-page TIFF |
| Compression | **none** — the movie must be uncompressed and contiguous |
| Dimensions | exactly 3-D: `(frames, height, width)` |
| Pixel type | not constrained by the reader; the reference acquisition is 16-bit unsigned big-endian, and byte order is preserved end to end |
| Geometry | one frame containing both channels side by side, split down the vertical midline |

The reader is `tether.io.movie.MovieReader`, and it refuses rather than guesses. It raises
`ValueError` when the TIFF series is not 3-D, when the file is not memory-mappable — which
is what a compressed or non-contiguous TIFF looks like from here — and when the memory-map
geometry disagrees with the geometry the TIFF directory reported. That last one is
deliberate belt-and-braces: serving frames from a mismatched map would return wrong data
quietly, which is worse than failing.

**The extension is not what decides.** On the native path (`tether extract`, `tether
batch`) the filename suffix is never checked; the file is handed to `tifffile`, so a real
`.nd2` fails with a parse error ("not a TIFF file") rather than being politely classified.
Extension matching exists only in the Deep-LASI folder-intake wizard, where
`tether.io.intake.classify_file` maps `.tif`/`.tiff` to the movie role, `.tdat`, `.tmap`,
`.mat`, `.txt` and `.hdf5`/`.h5` to their own legacy roles, and anything it does not
recognise to `unknown` — collected into the discovery result's `ignored` list rather than
silently dropped.

**Frame time.** Tether reads seconds-per-frame from the TIFF only when the file carries an
ImageJ `finterval` tag; a non-finite or non-positive value is rejected at that boundary
rather than propagated as a poisoned time axis. The reference big-endian acquisitions
carry no such tag, so a natively extracted project records the frame time as *unknown*
(`0.0`) and traces are plotted against a frame index rather than seconds. Nothing is
guessed.

The reference acquisition the performance envelope is written against is a
512 × 512 × 1700 big-endian `uint16` movie — about **0.83 GiB** per movie, uncompressed.

## Acquisition geometry

Tether splits each frame into a left half and a right half and treats one as donor and the
other as acceptor. Which half is which is yours to declare, with `--donor-side` (default
`left`); the split itself is `tether.project.extract._half_split_geometry`, which crops
both halves from the frame *width* — there is no top/bottom split.

**`--donor-side` is a `tether extract` flag only.** The headless batch runner (`tether
batch`) does not expose it: `_run_batch` builds its `MovieJob`s without an `ExtractOptions`,
so every movie in a batch run uses the default **left** donor. If your acquisition puts the
donor on the right, run it through `tether extract` per movie, supply a `.tmap` (whose
reference channel then defines donor), or drive `tether.project.batch.run_batch` from Python
with `extract_options=ExtractOptions(donor_side="right")`.

With `--tmap`, the split instead comes from an imported Deep-LASI map's own per-channel
crops, and `--donor-side` is ignored — donor is the map's reference channel. **Crop is the
only geometry imported.** Deep-LASI's `processImage` rotates and flips *before* cropping,
and the imported path does not apply either yet, so
`tether.project.extract._imported_registration_map` refuses a `.tmap` whose channels carry
a non-identity `Rotation` or `Flip` rather than splitting at the wrong frame — the
`ExtractionError` names the channels and says the imported path "does not yet apply (only
crop geometry is honored); re-run without `--tmap` to use a native fit"
(`tests/test_extract_cli.py::test_extract_imported_tmap_nonidentity_geometry_refused`
covers both axes). Empty or all-zero `Rotation`/`Flip` — what the reference UCKOPSB map
stores — is the supported case (`RegistrationChannel.has_simple_geometry` in
`tether.imaging.register`).

> ### Getting `--donor-side` backwards is silent, and relabelling afterwards will not fix it
>
> **A wrong `--donor-side` raises no error during extraction.** Both values are legal, so
> extraction succeeds and every trace looks plausible.
>
> It is worse than a mislabelling. Spot detection is **donor-anchored**: Tether finds spots
> in the half you called donor and colocalizes the other channel against them. Get it
> backwards and you have anchored on the acceptor half, so the molecule *set* itself is
> different. You cannot repair the project by swapping the column names afterwards, and you
> cannot repair the efficiencies by taking `1 − E` — that is only a clean mirror for the
> uncorrected proximity ratio, and once leakage α and γ are applied it is not even that.
> Re-extract.
>
> **How to tell.** Find a molecule whose acceptor photobleaches partway through. When the
> acceptor dies, energy transfer stops and the donor de-quenches, so the **donor trace
> steps up** at that instant. If the trace you have labelled "donor" steps *down* while the
> other steps up, your halves are swapped.
>
> This is a correctness risk, not a preference — which is why it is stated here, on a page
> you read before installing.

## Legacy project files

Alongside native extraction, Tether imports existing Deep-LASI and tMAVEN projects —
`.tdat`, `.tmap`, `.txt` and `.mat` from Deep-LASI, and SMD `.hdf5`/`.h5` from tMAVEN. See
the [legacy import page](io/legacy-import.md) for what each format carries.

Two things are worth knowing before you plan a round trip.

**Not every legacy format carries pixel coordinates.** The Deep-LASI `.tdat` and `.mat`
do; the `.txt` intensity export and the tMAVEN SMD do not — whatever the SMD was exported
from. Without coordinates there is nothing tying a trace back to the spot it came from.

**Coordinates alone are not enough — the `.mat` is mandatory.** Rebuilding a project from
a legacy import happens *without* re-extraction, so it needs the pre-integrated per-molecule
traces, and those live only in the `.mat`. The `.tdat` carries coordinates and correction
factors but **no traces**. A movie + `.tdat` set therefore has coordinates and still cannot
be reconstructed: the import wizard classifies it as skipped, naming the missing `.mat`
(`_can_reconstruct` in `tether.gui.deeplasi_wizard`, locked by
`tests/test_deeplasi_wizard.py::test_movie_and_tdat_without_mat_cannot_reconstruct`). If
you have movies and `.tdat` files but no `.mat` export, plan on native re-extraction rather
than reconstruction.

## What Tether does not do

These are deliberate scope boundaries, not gaps waiting to be filled.

- **No ALEX or PIE, no stoichiometry, no three-colour.** Tether is two-colour,
  single-laser only. A direct consequence: the direct-excitation correction factor δ is
  structurally inert (it is zero), because estimating it needs the
  acceptor-under-acceptor-excitation channel that only ALEX provides.
- **No confocal or point-detector data.** This follows from the above rather than being a
  separate decision: the pipeline needs a camera frame it can split into two spatial halves
  and detect spots in, and a photon-arrival stream from a point detector has no such frame.
- **No compressed or tiled TIFFs.** The lazy reader memory-maps the file for O(1) frame
  access; a compressed TIFF cannot be mapped, and is refused rather than silently loaded
  whole. Vendor containers are not read either — convert to uncompressed TIFF first.
- **Two Deep-LASI detection modes are not ported, so those `.tdat` files cannot be
  decoded.** A `.tdat` saved with particle-detection mode 4 (local-variance) or mode 5 (ZMW
  intensity) is **refused** with a `ValueError` from `tether.io.tdat` — "modes 4
  'local-variance' and 5 'ZMW intensity' are not ported" — rather than being silently
  re-detected with a different method. Tether implements modes 1–3 — `wavelet`, `intensity`
  and `bandpass` (`tether.imaging.detect.ParticleDetectionMode`). The refusal is on
  *decoding* that file: `tether extract --tdat` re-extraction and the `.tdat`'s correction
  factors are unavailable. A bundle that also has the `.mat` still reconstructs — the
  import wizard's `_read_tdat_best_effort` (`tether.gui.deeplasi_executor`) catches the
  failure and rebuilds from the `.mat` without corrections, degraded to apparent-E and
  carrying a warning that "the .tdat could not be decoded"
  (`tests/test_deeplasi_executor.py::test_undecodable_tdat_degrades_to_mat_apparent_e`).
- **Older `.dat` and vbFRET `.mat` formats are out of scope.**
- **No data simulator.** Validation runs against real labelled traces and a published
  benchmark dataset, not synthesised data.
- **No central server.** Tether is a desktop application; each person runs it on their own
  machine against their own storage.

## Scale and performance

Every figure below is a named constant in `tether.project.perf`, decided in
[ADR-0032](adr/0032-nfr-perf-budget-verification.md). Read them as **engineering budgets
and derived envelopes, not measurements or a service guarantee**:
`tests/test_project_perf.py` runs a real extraction and holds the projections under them,
which is what keeps them honest.

| Quantity | Value | Constant |
|---|---|---|
| Reference movie | 512 × 512, 1700 frames | `REFERENCE_MOVIE_*` |
| Movies per condition | 100 | `REFERENCE_CONDITION_MOVIES` |
| Molecules per movie | 250 | `REFERENCE_MOLECULES_PER_MOVIE` |
| Unattended batch window | 12 h ("overnight") | `OVERNIGHT_WINDOW_HOURS` |
| Per-trace UI latency budget | 100 ms | `PER_TRACE_LATENCY_BUDGET_S` |
| Trace bytes per molecule per frame | 24 B (six `float32` layers) | `TRACE_BYTES_PER_MOLECULE_FRAME` |
| Fixed bytes per molecule | 5 kB | `FIXED_STORAGE_BYTES_PER_MOLECULE` |
| Per-condition projection ceiling | 5 GiB | `MAX_CONDITION_BYTES` |

`perf.estimate_condition_bytes()` projects a full reference condition — 100 movies × 250
molecules × 1700 frames — at about **1.07 GiB** of `.tether` store, against roughly 90 GB
of raw movies at the reference geometry. One molecule projects to about **46 kB**.

Both are deliberately *pessimistic*: the model costs the six trace layers uncompressed,
while the store writes them gzip-chunked, so real projects come in comfortably under it.
The 5 GiB ceiling is a design self-check that the test suite holds the projection beneath
— it is **not enforced at runtime**, and nothing stops or warns you if a real condition
exceeds it.

The 100 ms per-trace budget is what keeps curation at a 1–2 second-per-trace cadence; it
covers drawing one molecule's donor/acceptor and FRET curves and stepping to the next.

### The store references your movies, it does not copy them

This is the most useful thing to know about disk planning. A `.tether` project records,
per movie, the path you gave it, a `sha256`, and a metadata-only signature — file size,
mtime and an offline-availability flag (see `MOVIES_DTYPE` in `tether.io.schema`). The
movie pixels stay where they are.

Reopening a project does not read your movie files, so it can never force a cloud-storage
placeholder to hydrate just to check whether something changed.

The one thing a **natively extracted** store caches from the pixels is a `window × window`
temporal-mean patch per molecule per channel (`/patches/{donor,acceptor}`), so curation and
the static overlap view still work with the movies offline. At the default 21-pixel window
that is about 3.5 kB per molecule, already inside the 5 kB fixed-per-molecule figure above.
A project reconstructed from a Deep-LASI export is the exception: that path never reads the
movie pixels, so its patches are written zero-filled. Curation, the overlap distance and the
overlap flag still work, but the patch image itself renders blank rather than reporting a
missing patch.

Practical consequence: the traces, patches and all analysis survive moving or renaming
your movies. What you lose is the trip back to the pixels — re-extraction, and inspecting a
molecule against its frames.

## Disk footprint of the application

The installer lays down two complete, isolated environments under the install root —
`envs/tether` for the application and `envs/sidecar` for the tMAVEN idealization backend,
which needs an older, incompatible numeric stack of its own. Two full environments plus a
bundled Python make the install considerably larger than a single-environment desktop
utility.

No figure is quoted here on purpose: the first signed release has not been cut, so any
number would be a guess. The [installers page](packaging.md) is where the measured
per-platform artifact sizes will live once they exist.

## Still not sure?

If your setup is close to but not exactly the geometry above, the fastest check is to run
`tether extract` on a single movie and look at what comes out — `tether extract --help`
lists the options. Extraction either produces a project with a sensible molecule count or
fails with a message naming what it could not read.
