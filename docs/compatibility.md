# Does Tether fit my data?

**Who this page is for.** Anyone deciding whether to install Tether at all. It describes
what an acquisition has to look like for Tether to read it, what Tether deliberately does
not do, and roughly what a dataset costs in time and disk. Read it before you download an
installer — if your experiment is in the "does not do" list below, nothing later on this
site will change that.

## The short version

Tether reads **uncompressed multi-page TIFF movies from a single camera whose frame is
split into two horizontal halves** — one donor channel, one acceptor channel — recorded
with **one excitation laser** and **two colours**. That is the dual-view TIRF geometry the
whole pipeline is built around.

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
`left`); the split itself is `tether.project.extract._half_split_geometry`.

With `--tmap`, the split instead comes from an imported Deep-LASI map's own per-channel
crops, and `--donor-side` is ignored — donor is the map's reference channel.

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

One thing worth knowing before you plan a round trip: **not every legacy format carries
pixel coordinates.** The Deep-LASI `.tdat` and `.mat` do; the `.txt` intensity export and
the tMAVEN SMD do not — whatever the SMD was exported from. Without coordinates there is
no trace ⇄ movie round trip, because there is nothing tying a trace back to the spot it
came from.

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
- **Two Deep-LASI detection modes are not ported.** An imported `.tdat` saved with
  particle-detection mode 4 (local-variance) or mode 5 (ZMW intensity) is **refused** with
  a `ValueError` from `tether.io.tdat`, rather than being silently re-detected with a
  different method. Tether implements modes 1–3 — `wavelet`, `intensity` and `bandpass`
  (`tether.imaging.detect.ParticleDetectionMode`).
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

The one thing the store does cache from the pixels is a `window × window` temporal-mean
patch per molecule per channel (`/patches/{donor,acceptor}`), so curation and the static
overlap view still work with the movies offline. At the default 21-pixel window that is
about 3.5 kB per molecule, already inside the 5 kB fixed-per-molecule figure above.

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
