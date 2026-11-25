---
description: "How Tether takes a movie through extraction, curation, corrections, idealization, analysis and export, and why each choice matters in a paper."
---

# How Tether works

**Who this page is for.** You are about to analyze single-molecule FRET data with Tether and
want to know what it does to a movie, and in what order. You also want to know why some of
its choices change what you can say in a paper. You do not need to be a contributor, and nothing here asks you to run
anything. This page is explanation only. For *how* to do something, follow the links to
[Install Tether](install.md), the [Command line](cli.md), the [Exports](reference/exports.md)
and [Analysis parameters](reference/parameters.md) references, and the
[project file](reference/tether-format.md) reference.

## The pipeline at a glance

This is the path that a movie follows when Tether extracts it natively. A legacy Deep-LASI
acquisition joins the path later. Its traces are already integrated, so Tether imports them
straight into a project, and they skip the stages from split to extract
([Legacy Deep-LASI import](io/legacy-import.md)).

The stages that keep a result keep it in one
[`.tether` project file](reference/tether-format.md). That file refers to your movie and does
not copy its pixels
([the project file references your movies](compatibility.md#the-project-file-references-your-movies-it-does-not-copy-them)).
Some stages write nothing. Exports are new files, written where you direct them:

```text
movie → split → detect → register → colocalize → extract → curate → photobleach
      → α / γ corrections → idealize → analyze → export
```

The order is fixed where one stage needs the output of another. Everything downstream reads the
extracted traces, so extraction comes first. The correction steps depend on one another. After
extraction, the path branches. You can curate, plot apparent E or export traces as soon as a
movie is extracted.

Correction adds the factors, which the molecule table records. Idealization adds the fitted
states that the dwell and transition analyses read. Neither of the two has to come first.

The headless batch runner groups the automatic part of that path into three stages:
`extract → correct → idealize`.
[`STAGES` in `tether.project.batch`](https://github.com/bioedca/tether/blob/main/src/tether/project/batch.py)
declares them in dependency order. When the predecessor of a stage did not complete, the runner
records the later stage as **blocked**. It does not run that stage on missing inputs
([stage status](glossary.md#stage-status-batch);
[ADR-0030](adr/0030-headless-batch-runner-isolation-and-checkpoint.md)).

The steps inside the correction stage run in a fixed order there. When you call them directly,
the leakage and γ estimators refuse to run before the steps they depend on. The final step
stamps how E is computed. It does not refuse, and it falls back to apparent E when the factors
are missing
([correction order](glossary.md#correction-order);
[`tether.project.correct`](https://github.com/bioedca/tether/blob/main/src/tether/project/correct.py)).

The batch runner stops at idealization. Curation, analysis and export are not part of it.
Curation in particular is your judgment, made in the app, and it can happen any time after
extraction.

## The stages

Each stage below names the module that does the work and what it leaves in the project file.
Where a page covers the stage in depth, the stage links to it.

### Movie

The acquisition is a dual-view TIRF movie: one camera frame carries the donor and the acceptor
side by side ([dual-view](glossary.md#dual-view)). `tether.io.movie` opens it as a memory map
and reads frames on demand, so browsing a movie does not load the file. Spot detection does
build a floating-point copy of the frames of each channel. That copy is larger than the file
itself. So plan memory for extraction by that copy, not by the file size
([`tether.imaging.detect`](https://github.com/bioedca/tether/blob/main/src/tether/imaging/detect.py)).

- **Writes:** nothing yet. Tether writes the movie's own record, with a SHA-256 of the file,
  when it extracts the movie.
- **Read more:** [Does Tether fit my data?](compatibility.md)

### Split

`tether.imaging.split` cuts each frame into its donor and acceptor channels. The movie's channel
layout (`tether.imaging.layout`) says where each channel is. It is side by side (the default),
top and bottom, or one free rectangle for each channel. Each channel can also have a recipe
(`tether.imaging.recipe`, #910): rotate, flip, translate and crop steps, in order, with no
resampling. The cut and the recipe become one rotate, flip and crop geometry, which the
movie's row stores.

- **Writes:** the layout record `/movies/layouts/<movie_id>`, with the movie's other records,
  when the movie is extracted.

### Detect

`tether.imaging.detect` finds candidate spots in both halves. It ports the Deep-LASI detection
methods 1, 2, 3 and 5 (ZMW intensity). So Tether can extract a movie with the method and
threshold that its `.tdat` recorded. Method 4 (local variance) is not ported, because the
Deep-LASI method 4 itself cannot run. Tether refuses a `.tdat` that names method 4, and it does
not run a substitute ([ADR-0021](adr/0021-particle-detection-modes.md);
[ADR-0143](adr/0143-deep-lasi-detection-mode-5-is-ported-and-mode-4-stays-refused.md),
[Does Tether fit my data?](compatibility.md#legacy-project-files)).

- **Writes:** nothing on its own. The spots feed registration and colocalization.
- **Read more:** [Analysis parameters](reference/parameters.md#detection-and-extraction)

### Register

`tether.imaging.register` fits the map that carries a position in one half onto the other half.
It fits the map from donor spots paired with acceptor spots, and `tether.imaging.calibrate`
stores it. A native fit is gated on its residual. A map that misses the gate is flagged as low
confidence, and Tether does not trust it silently
([RMS gate](glossary.md#rms-gate-low-confidence-registration);
[ADR-0014](adr/0014-registration-map-rms-gate-and-over-gate.md)).

You can supply the Deep-LASI `.tmap` instead. If its channels need only cropping, Tether uses
that map as given. There are no paired spots to measure it against, so Tether records its
residual as unknown and does not apply the gate. A `.tmap` that also rotates or flips a channel
is applied in the Deep-LASI order: rotate, flip, then crop (#559). Tether refuses a movie whose
frames cannot hold the channel geometry of the map, and it names both
([acquisition geometry](compatibility.md#acquisition-geometry);
[`tether.project.extract`](https://github.com/bioedca/tether/blob/main/src/tether/project/extract.py)).

Deep-LASI offers only a visual check of the registration. This numeric gate is the improvement
that Tether makes on it.

A map can also come from a bead slide. `tether.imaging.beadmap` takes one bead image, or a bead
video reduced to one image. The reduction is a `max` or `mean` projection over the frames that
you choose. Then `tether.imaging.beadmap` runs the same detection, prealign, pairing and fit on
that image. With one image, it gives the same map as the steps above. It stores the source id,
the frame range and the projection with the map.

It also stores a quality record with four values. These are the RMS residual, the number of
pairs, the residual of each pair and the field fraction that the pairs cover. Tether refuses a
source with fewer than six bead spots in either half
([`min_bead_spots`](reference/parameters.md#registration-and-colocalization)). This is a
Python entry point only for now. `tether extract` has no option to use such a map yet.

The map file is an asset of its own ([`tether.imaging.map_asset`](api/imaging.md#tether.imaging.map_asset),
[ADR-0146](adr/0146-the-map-file-is-a-separate-asset-and-reuse-checks-optics-not-the-date.md)).
Besides the transform, it holds a map id, its origin, the source ids and the channel layout.
It also holds the quality record, an optics text, the pixel size, the time and the Tether
version. A project
records the id and the checksum of each map file it used. Before a map file registers a movie,
a reuse check compares the pixel size and the optics text, never the date
([reuse a map file](cli.md#reuse-a-map-file)).

- **Writes:** the registration map under `/calibration`, with its residual and source.
- **Read more:** [registration](glossary.md#registration-tmap),
  [Analysis parameters](reference/parameters.md#registration-and-colocalization)

### Colocalize

`tether.imaging.coloc` pairs each donor spot with the acceptor position that the registration
map predicts for it. It keeps the donor spots whose aperture window fits inside both channels.
By default, it keeps only those with an acceptor spot detected near that position
([partner-in-both](glossary.md#partner-in-both-colocalization)). With
`--colocalization donor-anchored`, it keeps them all
([donor-anchored](glossary.md#donor-anchored-colocalization)). Either way, Tether reads the
acceptor at the mapped position and reports the molecule count of each rule
([ADR-0127](adr/0127-colocalization-policy-partner-in-both-by-default-or-donor-anchored.md)).

- **Writes:** nothing on its own. The pairs are what extraction integrates. Tether stores with
  each molecule whether its acceptor was detected (`/molecules/acceptor_detected`).

### Extract

`tether.imaging.extract` integrates the donor and acceptor intensity of each molecule in every
frame and subtracts the local background. From here on, a molecule is a row in the project
file, and row *i* of every trace array is the same molecule
([ADR-0016](adr/0016-extraction-trace-store-layout.md)).

Tether never resamples the movie. It applies the map to coordinates, never to pixels, to avoid
interpolation bias in the integrated intensities. A molecule is extractable only if its full
aperture lies inside the frame in both channels. So Tether drops the spots that are out of the
frame, and it does not fill them with zeros.

- **Writes:** a `/movies` row, the `/molecules` rows with their coordinates, the intensity
  traces under `/traces`, image crops under `/patches`, and the extraction settings under
  `/settings/extraction`.
- **Read more:** [Command line](cli.md#tether-extract),
  [Legacy Deep-LASI import](io/legacy-import.md) for bringing in traces that were extracted
  elsewhere.

### Preview the stages before a run

`tether.project.preview` runs split, detect, register, colocalize and extract on a movie that is
already in memory. It uses the same order and the same settings as a real extraction, and it
stops before it writes anything. It gives one result for each stage: what the stage produced,
the settings it used and an outcome.

A stage that cannot finish reports `failed` with the reason. A native fit that misses the RMS
gate reports `over-gate`.

The molecules and traces that the preview gives are the same, bit for bit, as the ones a real
run makes. This is true when both use the same channel geometry and the same registration
source. A different geometry can give a different result
([`tether.project.preview`](https://github.com/bioedca/tether/blob/main/src/tether/project/preview.py)).

- **Writes:** nothing.

### Curate

`tether.project.labels` records each accept or reject that you make. The current verdict sits
on the molecule. Tether also appends every decision to a log, with who made it and when.
Tether never rewrites the decision rows. It recomputes only the stored training weight of each
row, on a legacy import or when it merges back the decisions of several curators. The quality
ranker applies the same weighting in memory when it trains
([`tether.project.weighting`](https://github.com/bioedca/tether/blob/main/src/tether/project/weighting.py);
[curation label](glossary.md#curation-label-category-quality-class);
[ADR-0023](adr/0023-curation-label-codec-and-labels-log.md)).

- **Writes:** `curation_label` on the molecule and a row in `/labels`.
- **Read more:** [Deep trace classifier](ml/deep-classifier.md), an optional add-on that
  learns from those labels.

### Photobleach

`tether.fret.photobleach` finds the frame where each dye bleaches, separately for donor and
acceptor. `tether.project.photobleach` writes the result and proposes the analysis window
([photobleach frames](glossary.md#photobleach-frames-first-bleach-frame);
[ADR-0026](adr/0026-photobleach-detection-and-window-default.md)).

- **Writes:** `bleach_frames` on each molecule. Where the analysis window of a molecule is still
  the extraction default, it also writes `analysis_window`.
- **Read more:** [Analysis parameters](reference/parameters.md#photobleaching-and-the-analysis-window)

### α / γ corrections

`tether.fret.leakage` and `tether.fret.gamma` estimate the leakage factor α and the detection
factor γ. `tether.project.correct` decides, molecule by molecule, whether a corrected
efficiency can be formed, and it records how
([correction order](glossary.md#correction-order)).

- **Writes:** `alpha`, `gamma`, `correction_method` and `correction_confidence` on each
  molecule. It also writes the provenance of each pass under `/settings/leakage` and
  `/settings/correction`. It writes `/settings/gamma` only when α was estimated, because the γ
  step is skipped when α is withheld.
- **Read more:** [Analysis parameters](reference/parameters.md#correction-factors),
  [Validation & benchmarks](validation.md#e-estimator-edge-cases)

### Idealize

`tether.idealize.driver` hands the traces to tMAVEN in its own isolated environment and reads
the fitted state model back. `tether.project.idealize` stores the model with a fingerprint of
its inputs
([idealization](glossary.md#idealization);
[ADR-0024](adr/0024-idealization-store-layout-staleness-and-nstates.md)).

- **Writes:** one subgroup for each fitted model under `/idealization`.
- **Read more:** [Standalone-tMAVEN hand-off](idealize/standalone-tmaven-handoff.md)

### Analyze

The `tether.analysis` modules build the histograms, the transition-density plots, the
dwell-time and kinetics analyses and the other population views. They read the project file and
do not change it. By default, the analyses that read traces leave out the molecules that you
rejected.

Four analyses built on an idealization also leave out the molecules whose idealization is
stale. These four are transition-density plots, dwell times, state number and transition
probabilities. The raw-signal views, such as the E histogram and the FRET cloud, do not leave
them out. So the two kinds can be drawn from different sets of molecules
([fresh-idealization gating](glossary.md#fresh-idealization-gating-curation-filter)).

Two views built on an idealization are exceptions. The post-synchronized transition heatmap
applies the curation filter but not the staleness filter. The fitted-model overlay on the E
histogram reads the model as it was fitted. So the molecules that you rejected since then stay
in it until a fresh fit over a new selection. Re-fitting a stale model keeps its original
molecules
([`tether.analysis.histogram`](https://github.com/bioedca/tether/blob/main/src/tether/analysis/histogram.py)).

- **Writes:** nothing to the project file.
- **Read more:** [Seven-plot parity gallery](analysis/parity-gallery.md)

### Export

`tether.project.export` writes a molecule table, a Deep-LASI trace matrix or a smaller
self-contained `.tether`. `tether.analysis.plot_export` writes figures. Each one carries a
provenance file beside it ([provenance file](reference/exports.md#provenance-file)).

- **Writes:** new files where you direct them. Tether never modifies the source project.
- **Read more:** [Exports](reference/exports.md)

## Why it works this way

Four decisions change what a number from Tether means. This section states each one with the
record that decided it, so that you can check the reasoning yourself.

### Proximity ratio versus corrected E

The proximity ratio, `A / (D + A)`, is what you get from the background-subtracted donor and
acceptor intensities before any leakage or γ correction. Tether's plots and E summary read that
layer by default. You can point an analysis at the `raw` layer, which still carries the
background, but then its ratio is a different number. The proximity ratio is internally
consistent only while the dyes and the instrument stay the same
([McCann 2010](https://doi.org/10.1016/j.bpj.2010.04.063)).

The γ-corrected efficiency removes the leakage and the imbalance in dye and detection. Tether
assumes no direct acceptor excitation (δ = 0, single-laser data). Under that assumption, the
γ-corrected efficiency is an absolute energy-transfer efficiency
([Hellenkamp 2018](https://doi.org/10.1038/s41592-018-0085-0);
[`tether.fret.efficiency`](https://github.com/bioedca/tether/blob/main/src/tether/fret/efficiency.py)).
Tether calls the proximity ratio **apparent E**. It computes apparent E as the corrected formula
with the correction factors set to do nothing. So the two can never be defined differently
([apparent E](glossary.md#apparent-e)).

Two things follow for the plot in front of you. First, every population plot that shows E is
apparent E, even after the corrections succeeded. So is the E summary in the molecule-table
export ([apparent E](glossary.md#apparent-e); [Exports](reference/exports.md)). Second, α or γ
can fail to be estimated, for example because too few molecules qualify. Then Tether does not
let the missing factor reach any number.

The cell of the factor holds a `NaN` sentinel that means "no factor applied". Tether stamps the
molecule `apparent-E (corrections unavailable)`, which tells every reader to use apparent E
instead ([ADR-0003](adr/0003-apparent-e-never-nan.md);
[project file](reference/tether-format.md#molecules-one-row-per-molecule)). The plot looks the
same either way, so the plot cannot tell you whether a correction was applied. The
`correction_method` of the molecule and the `/settings/correction` record can tell you
([correction_method](glossary.md#correction_method)).

Tether does not clip apparent E to `[0, 1]`. A corrected value slightly outside that range on a
noisy frame is a real fluctuation. Hiding it would be a silent distortion. The histogram and
cloud analyses call `apparent_fret` unconditionally (`tether.analysis.histogram`,
`tether.analysis.cloud`). The axis label of the trace dock is the literal string
`"apparent E"` (`tether.gui.trace_dock`). The plots of the Analyze section are the export
figures of `tether.analysis.plot_export`.

### What per-molecule γ changes about a published number

The factor γ corrects for the difference in brightness and detection between the two dyes. Tether measures
it on each molecule, from the jump in donor and acceptor intensity at the frame where the
acceptor bleaches
([`tether.fret.gamma`](https://github.com/bioedca/tether/blob/main/src/tether/fret/gamma.py);
[ADR-0028](adr/0028-gamma-acceptor-bleach-step-estimator.md)).

Some molecules have a bleach step that does not pass the quality gates. Each of those takes the
median γ of the molecules that did pass. This needs enough passing molecules to form a dataset
γ at all (`min_qualifying_traces`, ten by default). When too few pass, Tether withholds γ for
every molecule, and the whole set falls back to apparent E
([ADR-0028](adr/0028-gamma-acceptor-bleach-step-estimator.md);
[`tether.project.gamma`](https://github.com/bioedca/tether/blob/main/src/tether/project/gamma.py)).

If you compare γ values with Deep-LASI, expect a higher γ from Tether, by a factor of about
`(1 + α)` on the same bleach step. Your own α sets that factor. In the one Deep-LASI export that
ADR-0028 examined, the median α was 0.09, which makes the gap about 9 %. That gap is a
difference in convention, not an error. Deep-LASI scales the donor by `(1 + α)` and Tether does
not. Each tool defines γ to match its own donor, so the factor cancels in E itself.

The factor does not cancel in γ. So a comparison of γ across the two tools has to divide it out
([`tether.fret.gamma`](https://github.com/bioedca/tether/blob/main/src/tether/fret/gamma.py);
[ADR-0028](adr/0028-gamma-acceptor-bleach-step-estimator.md)).

### Why bleach frames come first, and why your analysis window wins

Tether reads both correction factors from photobleaching. The factor α comes from the stretch after the
acceptor has bleached but the donor has not. The estimator assumes that the acceptor-channel
signal left in that stretch is donor light that leaks through
([`tether.fret.leakage`](https://github.com/bioedca/tether/blob/main/src/tether/fret/leakage.py)).
The factor γ comes from the bleach step of the acceptor itself (above). So photobleach detection has to run
before Tether estimates either factor. If any molecule has not been through detection yet,
Tether refuses the whole estimate and does not guess it.

A molecule where detection ran and found no bleach in a channel is a different case. Tether
records that result as the end of the trace. It does not refuse the molecule, and the molecule
can still take the factor of the dataset
([photobleach frames](glossary.md#photobleach-frames-first-bleach-frame)).

Detection runs on the background-subtracted traces by default, because the model detects a
decay to `N(0)`. Raw traces keep a large offset and never look bleached.

The analysis window is the stretch of frames that idealization, the analyses and the
molecule-table export read. The α and γ estimators do not use it. They work from the full trace
of each molecule and its bleach frames
([`tether.project.leakage`](https://github.com/bioedca/tether/blob/main/src/tether/project/leakage.py),
[`tether.project.gamma`](https://github.com/bioedca/tether/blob/main/src/tether/project/gamma.py)).

Photobleach detection proposes an analysis window, from the start of the trace to the first
bleach of the summed donor and acceptor signal. It writes that proposal only where the analysis
window is still the untouched extraction default. A later detection pass leaves any other
analysis window alone. This is true whether you narrowed it by hand or an earlier pass proposed
it. Manual bounds win over the automatic one, and re-detected bleach frames do not move an
analysis window that is already set
([`tether.project.photobleach`](https://github.com/bioedca/tether/blob/main/src/tether/project/photobleach.py);
[ADR-0026](adr/0026-photobleach-detection-and-window-default.md)).

Tether re-detects the bleach frames themselves each time the correction stage runs. A change to
an analysis window is still a change to the inputs. So any idealization fitted on the old
analysis window becomes stale (next section).

### What provenance-first storage lets you claim

Tether keeps everything in one file that records where each value came from. The file holds
the coordinates of each molecule and its content-derived identity. It holds the correction
factors and the method that produced them. It also holds the parameters and the Tether version
of the extraction, leakage, γ and correction passes
([ADR-0001](adr/0001-provenance-first-data-model.md);
[`/settings`](reference/tether-format.md#settings-provenance-of-each-pipeline-stage)).

Photobleach detection is the exception. It records the bleach frames and the analysis window
that it found, but not the detector settings that it used
([`tether.project.photobleach`](https://github.com/bioedca/tether/blob/main/src/tether/project/photobleach.py)).

The two table exports carry only part of this record. The Deep-LASI `.txt` trace matrix holds
intensities alone. The molecule-table CSV adds the analysis window, α, γ and correction method
of each molecule. The provenance file beside each export records the parameters of the export,
the Tether version that wrote it and the project it came from. It does not record the
parameters of the passes that produced the factors. So only a `.tether` file answers all of
those questions: the project, or a subset exported from it, which copies `/settings`
([provenance file](reference/exports.md#provenance-file)).

Idealization gains the most from this record. By default, the model is fitted on the
background-subtracted donor and acceptor intensities, with no α or γ applied. You can ask a fit
to use the `raw` layer instead. The stored model records which layer it used in its
`intensity_quantity`
([`tether.project.idealize`](https://github.com/bioedca/tether/blob/main/src/tether/project/idealize.py)).
Each fitted molecule carries a fingerprint of that trace, its analysis window, and the α and γ
in force for it when it was fitted.

Change any of those, and the fingerprint no longer matches. Tether then marks the molecule
stale. By default, the four staleness-gated analyses above leave it out until it is re-fitted.
A change in α marks stale every molecule whose E uses the correction, because one α applies to
all of them.

A molecule on apparent E is fingerprinted with no factors applied. So a new α leaves it fresh
as long as it stays on apparent E. The new α marks it stale if it moves the molecule onto
corrected E. Re-running the estimate and getting the same α changes nothing. A shift in the
median γ marks stale only the corrected molecules that took that median
([ADR-0029](adr/0029-idealization-correction-provenance-hash-and-per-factor-staleness.md);
[staleness](glossary.md#staleness-stale-fresh-live)).

You can then show that a dwell time or transition rate came from the exact trace, analysis
window and correction state stored with it. It did not come from an older version of them.
When the result leaves Tether, the [provenance file](reference/exports.md#provenance-file)
beside each export names the project it came from. That project is where the rest of that
record stays.
