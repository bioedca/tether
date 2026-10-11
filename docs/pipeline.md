# How Tether works

**Who this page is for.** You are about to analyze single-molecule FRET data with Tether and
want to know what it does to a movie, in what order, and why some of its choices change what
you can say in a paper. You do not need to be a contributor, and nothing here asks you to run
anything. This page is explanation only: for *how* to do something, follow the links to
[Install Tether](install.md), the [Command line](cli.md), the [Exports](reference/exports.md)
and [Analysis parameters](reference/parameters.md) references, and the
[project store](reference/tether-format.md) reference.

## The pipeline at a glance

This is the road a movie takes when Tether extracts it natively. A legacy Deep-LASI
acquisition joins it later: its traces were already integrated, so it is imported straight
into a project and skips the split-to-extract stages
([Legacy Deep-LASI import](io/legacy-import.md)). The stages that keep a result keep it in one
[`.tether` project file](reference/tether-format.md), which refers to your movie rather than
copying its pixels ([the store references your movies](compatibility.md#the-store-references-your-movies-it-does-not-copy-them));
some stages write nothing, and exports are new files beside the project:

```text
movie → split → detect → register → colocalize → extract → curate → photobleach
      → α / γ corrections → idealize → analyze → export
```

The order binds where one stage needs another's output. Everything downstream reads the
extracted traces, so extraction comes first, and the correction steps depend on one another.
After extraction the road branches rather than running in single file: you can curate, plot
apparent E or export traces as soon as a movie is extracted. Correction and idealization add
to what those later steps can use, the correction factors and the fitted states that the dwell
and transition analyses read; they do not have to come first. The headless batch runner
groups the automatic part of that road into three stages, `extract → correct → idealize`,
declared in dependency order as
[`STAGES` in `tether.project.batch`](https://github.com/bioedca/tether/blob/main/src/tether/project/batch.py),
and when a stage's predecessor did not complete it records the later stage as **blocked**
rather than running it on missing inputs
([stage status](glossary.md#stage-status-batch);
[ADR-0030](adr/0030-headless-batch-runner-isolation-and-checkpoint.md)). The steps inside the
correction stage run in a fixed order there. Called directly, the leakage and γ estimators
refuse to run before the steps they depend on; the final step that stamps how E is computed
does not refuse, and falls back to apparent E when the factors are missing
([correction order](glossary.md#correction-order);
[`tether.project.correct`](https://github.com/bioedca/tether/blob/main/src/tether/project/correct.py)).

The batch runner stops at idealization: curation, analysis and export are not part of it.
Curation in particular is your judgement, made in the app, and it can happen any time after
extraction.

## The stages

Each stage below names the module that does the work, what it leaves in the project file, and
the page that covers it in depth where one exists.

### Movie

The acquisition is a dual-view TIRF movie: one camera frame carries the donor and the acceptor
side by side ([dual-view](glossary.md#dual-view)). `tether.io.movie` opens it as a memory map
and reads frames on demand, so browsing a movie does not load the file. Spot detection does
build a floating-point copy of each channel's frames, which is larger than the file itself, so
plan memory for extraction by that rather than by the file size
([`tether.imaging.detect`](https://github.com/bioedca/tether/blob/main/src/tether/imaging/detect.py)).

- **Writes:** nothing yet. The movie's own record, including a SHA-256 of the file, is written
  when it is extracted.
- **Read more:** [Does Tether fit my data?](compatibility.md)

### Split

`tether.imaging.split` cuts each frame into its donor and acceptor halves.

- **Writes:** nothing on its own.

### Detect

`tether.imaging.detect` finds candidate spots in both halves. It ports Deep-LASI's detection
methods 1 to 3, so a movie can be extracted with the method and threshold its `.tdat`
recorded; methods 4 (local variance) and 5 (ZMW intensity) are not ported, and a `.tdat` that
names one is refused rather than run with a substitute
([ADR-0021](adr/0021-particle-detection-modes.md);
[Does Tether fit my data?](compatibility.md#legacy-project-files)).

- **Writes:** nothing on its own; the spots feed registration and colocalization.
- **Read more:** [Analysis parameters](reference/parameters.md#detection-and-extraction)

### Register

`tether.imaging.register` fits the map that carries a position in one half onto the other,
from donor spots paired with acceptor spots, and `tether.imaging.calibrate` stores it. A
native fit is gated on its residual, and a map that misses the gate is flagged as low
confidence rather than silently trusted
([RMS gate](glossary.md#rms-gate-low-confidence-registration);
[ADR-0014](adr/0014-registration-map-rms-gate-and-over-gate.md)). If you supply Deep-LASI's own
`.tmap` instead, Tether uses that map as given: there are no paired spots to measure it
against, so its residual is recorded as unknown and the gate is not applied
([`tether.project.extract`](https://github.com/bioedca/tether/blob/main/src/tether/project/extract.py)).

- **Writes:** the registration map under `/calibration`, with its residual and source.
- **Read more:** [registration](glossary.md#registration-tmap),
  [Analysis parameters](reference/parameters.md#registration-and-colocalization)

### Colocalize

`tether.imaging.coloc` pairs each donor spot with the acceptor position the registration map
predicts for it. The donor is the anchor: every donor spot whose extraction window fits
inside both channels becomes a molecule, whether or not a separate acceptor spot was detected
there; the ADR records why Tether chose the donor as the anchor
([donor-anchored colocalization](glossary.md#donor-anchored-colocalization);
[ADR-0015](adr/0015-donor-anchored-colocalization.md)).

- **Writes:** nothing on its own; the pairs are what extraction integrates.

### Extract

`tether.imaging.extract` integrates each molecule's donor and acceptor intensity in every
frame and subtracts the local background. From here on a molecule is a row in the project
file, and row *i* of every trace array is the same molecule
([ADR-0016](adr/0016-extraction-trace-store-layout.md)).

- **Writes:** a `/movies` row, the `/molecules` rows with their coordinates, the intensity
  traces under `/traces`, image crops under `/patches`, and the extraction settings under
  `/settings/extraction`.
- **Read more:** [Command line](cli.md#tether-extract),
  [Legacy Deep-LASI import](io/legacy-import.md) for bringing in traces that were extracted
  elsewhere.

### Curate

`tether.project.labels` records each accept or reject you make. The current verdict sits on
the molecule, and every decision is also appended to a log, with who made it and when.
The decision rows are never rewritten; only each row's training weight is recomputed when
the quality ranker retrains
([`tether.project.weighting`](https://github.com/bioedca/tether/blob/main/src/tether/project/weighting.py);
[curation label](glossary.md#curation-label-category-quality-class);
[ADR-0023](adr/0023-curation-label-codec-and-labels-log.md)).

- **Writes:** `curation_label` on the molecule and a row in `/labels`.
- **Read more:** [Deep trace classifier](ml/deep-classifier.md), an optional add-on that
  learns from those labels.

### Photobleach

`tether.fret.photobleach` finds the frame where each dye bleaches, separately for donor and
acceptor, and `tether.project.photobleach` writes the result and proposes the analysis window
([photobleach frames](glossary.md#photobleach-frames-first-bleach-frame);
[ADR-0026](adr/0026-photobleach-detection-and-window-default.md)).

- **Writes:** `bleach_frames` and, where you have not narrowed it, `analysis_window` on each
  molecule.
- **Read more:** [Analysis parameters](reference/parameters.md#photobleaching-and-the-analysis-window)

### α / γ corrections

`tether.fret.leakage` and `tether.fret.gamma` estimate the leakage factor α and the
detection factor γ, and `tether.project.correct` decides, molecule by molecule, whether a
corrected efficiency can be formed and records how
([correction order](glossary.md#correction-order)).

- **Writes:** `alpha`, `gamma`, `correction_method` and `correction_confidence` on each
  molecule, and the provenance of each pass under `/settings/leakage`, `/settings/gamma` and
  `/settings/correction`.
- **Read more:** [Analysis parameters](reference/parameters.md#correction-factors),
  [Validation & benchmarks](validation.md#e-estimator-edge-cases)

### Idealize

`tether.idealize.driver` hands the traces to tMAVEN in its own isolated environment and reads
the fitted state model back, and `tether.project.idealize` stores it with a fingerprint of
its inputs
([idealization](glossary.md#idealization);
[ADR-0024](adr/0024-idealization-store-layout-staleness-and-nstates.md)).

- **Writes:** one subgroup per fitted model under `/idealization`.
- **Read more:** [Standalone-tMAVEN hand-off](idealize/standalone-tmaven-handoff.md)

### Analyze

The `tether.analysis` modules build the histograms, transition-density plots, dwell-time and
kinetics analyses and the other population views. They read the project file and do not
change it. The analyses that read traces leave out molecules you rejected by default. Four
analyses built on an idealization (transition-density plots, dwell times, state number and
transition probabilities) also leave out molecules whose idealization is stale; the
raw-signal views, such as the E histogram and the FRET cloud, do not, so the two kinds can be
drawn from different sets of molecules
([fresh-idealization gating](glossary.md#fresh-idealization-gating-curation-filter)). Two
idealization-based views are exceptions: the post-synchronized transition heatmap applies the
curation filter but not the staleness one, and the fitted-model overlay on the E histogram
reads the model as it was fitted, so it reflects later curation only after a re-fit
([`tether.analysis.histogram`](https://github.com/bioedca/tether/blob/main/src/tether/analysis/histogram.py)).

- **Writes:** nothing to the project file.
- **Read more:** [Seven-plot parity gallery](analysis/parity-gallery.md)

### Export

`tether.project.export` writes a molecule table, a Deep-LASI trace matrix, or a smaller
self-contained `.tether`, and `tether.analysis.plot_export` writes figures. Each one carries a provenance file
beside it ([provenance sidecar](reference/exports.md#provenance-sidecar)).

- **Writes:** new files next to your project; the source project is never modified.
- **Read more:** [Exports](reference/exports.md)

## Why it works this way

Four decisions change what a number from Tether means. Each is stated here with the record
that decided it, so you can check the reasoning rather than take it on trust.

### Proximity ratio versus corrected E

The proximity ratio, `A / (D + A)`, is what you get from the background-subtracted donor and
acceptor intensities before any leakage or γ correction. That is the layer Tether's plots and
E summary read by default; an analysis can be pointed at the `raw` layer, which still carries
the background, but then its ratio is a different number. It is internally
consistent only while the dyes and the instrument stay the same
([McCann 2010](https://doi.org/10.1016/j.bpj.2010.04.063)). The γ-corrected efficiency
removes the leakage and the dye and detection imbalance, and is an absolute energy-transfer
efficiency ([Hellenkamp 2018](https://doi.org/10.1038/s41592-018-0085-0);
[`tether.fret.efficiency`](https://github.com/bioedca/tether/blob/main/src/tether/fret/efficiency.py)).
Tether calls the proximity ratio **apparent E**, and computes it as the corrected formula with
the correction factors set to do nothing, so the two can never be defined differently
([apparent E](glossary.md#apparent-e)).

Two things follow for the plot in front of you. First, every population plot and every
exported E summary is apparent E, even after the corrections succeeded
([apparent E](glossary.md#apparent-e); [Exports](reference/exports.md)). Second, when α or γ
cannot be estimated, for example because too few molecules qualify, Tether does not let the
missing factor reach any number. The factor's cell holds a `NaN` sentinel meaning "no factor
applied", and the molecule is stamped `apparent-E (corrections unavailable)`, which tells
every reader to use apparent E instead
([ADR-0003](adr/0003-apparent-e-never-nan.md);
[project store](reference/tether-format.md#molecules-one-row-per-molecule)). The plot looks the same either way, so the
plot cannot tell you whether a correction was applied; the molecule's `correction_method` and
the `/settings/correction` record can ([correction_method](glossary.md#correction_method)).

### What per-molecule γ changes about a published number

γ evens out the difference between the two dyes in brightness and detection. Tether measures
it on each molecule, from the jump in donor and acceptor intensity at the frame the acceptor
bleaches
([`tether.fret.gamma`](https://github.com/bioedca/tether/blob/main/src/tether/fret/gamma.py);
[ADR-0028](adr/0028-gamma-acceptor-bleach-step-estimator.md)). A molecule whose bleach step
does not pass the quality gates takes the median γ of the molecules that did, provided
enough of them passed to form a dataset γ at all (`min_qualifying_traces`, ten by default).
When too few pass, γ is withheld for every molecule and the whole set falls back to apparent
E ([ADR-0028](adr/0028-gamma-acceptor-bleach-step-estimator.md);
[`tether.project.gamma`](https://github.com/bioedca/tether/blob/main/src/tether/project/gamma.py)).

If you compare γ values with Deep-LASI, expect Tether's to run about `(1 + α)` higher on the
same bleach step, roughly 9 % at a typical α. That is a difference in convention, not an
error: Deep-LASI scales the donor by `(1 + α)` and Tether does not, and each tool defines γ to
match its own donor, so the factor cancels in E itself. It does not cancel in γ, so a
cross-tool γ comparison has to divide it out
([`tether.fret.gamma`](https://github.com/bioedca/tether/blob/main/src/tether/fret/gamma.py);
[ADR-0028](adr/0028-gamma-acceptor-bleach-step-estimator.md)).

### Why bleach frames come first, and why your window wins

Both correction factors are read off photobleaching. α comes from the stretch after the
acceptor has bleached but the donor has not, when any signal in the acceptor channel can only
be donor light leaking through
([`tether.fret.leakage`](https://github.com/bioedca/tether/blob/main/src/tether/fret/leakage.py)),
and γ comes from the acceptor's bleach step itself (above). So the bleach frames have to exist
before either factor does, and a molecule whose bleach frames were never detected is refused
rather than guessed at ([photobleach frames](glossary.md#photobleach-frames-first-bleach-frame)).

The analysis window is the stretch of frames that idealization, the analyses and the
molecule-table export read. The α and γ estimators do not use it: they work from each
molecule's full trace and its bleach frames
([`tether.project.leakage`](https://github.com/bioedca/tether/blob/main/src/tether/project/leakage.py),
[`tether.project.gamma`](https://github.com/bioedca/tether/blob/main/src/tether/project/gamma.py)). Photobleach detection
proposes one, from the start of the trace to the first bleach of the summed donor and acceptor
signal, but it writes that proposal only where the window is still the untouched extraction
default. Once you have narrowed a window by hand, a later detection pass leaves it alone:
manual bounds win over the automatic one
([`tether.project.photobleach`](https://github.com/bioedca/tether/blob/main/src/tether/project/photobleach.py);
[ADR-0026](adr/0026-photobleach-detection-and-window-default.md)). The bleach frames
themselves are re-detected each time the correction stage runs. Changing a window is still a
change to the inputs, so any idealization fitted on the old window becomes stale (next
section).

### What provenance-first storage lets you claim

Tether keeps everything in one file that records where each value came from: the molecule's
coordinates and its content-derived identity, the correction factors and the method that
produced them, and the parameters and Tether version of the extraction, leakage, γ and
correction passes ([ADR-0001](adr/0001-provenance-first-data-model.md);
[`/settings`](reference/tether-format.md#settings-provenance-of-each-pipeline-stage)).
Photobleach detection is the exception: it records the bleach frames and window it found, but
not the detector settings it used
([`tether.project.photobleach`](https://github.com/bioedca/tether/blob/main/src/tether/project/photobleach.py)). A flat CSV of corrected traces carries
none of that, so from the CSV alone nobody can tell which window, which α, which γ or which
version produced a number.

Idealization is where this pays off most. By default the model is fitted on the
background-subtracted donor and acceptor intensities, with no α or γ applied; a fit can be
asked to use the `raw` layer instead, and the stored model records which in its
`intensity_quantity`
([`tether.project.idealize`](https://github.com/bioedca/tether/blob/main/src/tether/project/idealize.py)), but each fitted molecule carries a
fingerprint of that trace, its window, and the α and γ in force for it when it was fitted.
Change any of those and the fingerprint no longer matches, the molecule is marked stale, and
the four staleness-gated analyses above leave it out until it is re-fitted. A change in α marks
every molecule whose E uses the correction stale, because one α applies to all of them; a
molecule on apparent E is fingerprinted with no factors applied, so a new α leaves it fresh;
re-running the estimate and getting the same α changes nothing. A shift in the median γ marks only the corrected molecules that
took that median
([ADR-0029](adr/0029-idealization-correction-provenance-hash-and-per-factor-staleness.md);
[staleness](glossary.md#staleness-stale-fresh-live)). That is what lets you say that a dwell
time or a transition rate came from exactly the trace, window and correction state
recorded with it, and not from an older version of them. When the result leaves Tether, the
[provenance sidecar](reference/exports.md#provenance-sidecar) beside each export carries the
version and parameters with it.
