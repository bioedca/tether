---
description: "Short definitions of the terms that the Tether documentation uses, in an A to Z list, each with a link to the page that explains it."
---

# Glossary

**Who this page is for.** A lab scientist who reads a Tether page and finds a term that the
page does not define. You want the meaning of the term in Tether, in one or two sentences. The
page assumes that you know the basics of single-molecule FRET.

Each entry starts with a definition. Where Tether's meaning is different from the usual smFRET
meaning, the definition says so. The last lines of an entry link to the page that explains the
term, and name the source module or constant for contributors. If something went wrong, start
at [Troubleshooting](troubleshooting.md).

## All terms, A to Z

The list also gives other names of a term. Each link goes to the entry that defines it. After
the list, the entries are grouped by the stage of the pipeline.

- **A:** [à trous / starlet wavelet, MAD](#a-trous-starlet-wavelet-mad) ·
  [accurate FRET](#corrected-e-accurate-fret) · [ALEX / PIE](#alex-pie) ·
  [α, alpha (leakage)](#leakage-bleedthrough) · [analysis window](#analysis-window) ·
  [analysis-only import](#analysis-only-import) · [aperture](#aperture) ·
  [apparent E](#apparent-e)
- **B:** [background-subtracted intensity](#background-subtracted-intensity-corrected) ·
  [bleedthrough](#leakage-bleedthrough)
- **C:** [category](#curation-label-category-quality-class) · [condition](#condition) ·
  [consensus VB-HMM](#vbfret-ebfret-consensus-vb-hmm) · [corrected E](#corrected-e-accurate-fret) ·
  [corrected FRET](#corrected-e-accurate-fret) · [correction order](#correction-order) ·
  [correction_method](#correction_method) · [curation filter](#fresh-idealization-gating-curation-filter) ·
  [curation label](#curation-label-category-quality-class)
- **D:** [δ, delta (direct excitation)](#delta-direct-excitation) ·
  [deterministic sidecar failure](#transient-vs-deterministic-sidecar-failure) ·
  [donor-anchored colocalization](#donor-anchored-colocalization) · [dual-view](#dual-view) ·
  [dwell time / dwell-time survival](#dwell-time-dwell-time-survival)
- **E:** [ebFRET](#vbfret-ebfret-consensus-vb-hmm) · [ELBO](#elbo)
- **F:** [first-bleach frame](#photobleach-frames-first-bleach-frame) ·
  [fresh-idealization gating](#fresh-idealization-gating-curation-filter)
- **G:** [γ, gamma (detection correction)](#gamma-detection-correction)
- **I:** [idealization](#idealization)
- **K:** [kinSoftChallenge](#kinsoftchallenge)
- **L:** [lock file](#write-lock-stale-lock) ·
  [low-confidence-registration](#rms-gate-low-confidence-registration)
- **M:** [MAD](#a-trous-starlet-wavelet-mad) · [MCOS](#tirfdata-mcos-refs) ·
  [molecule vs trace](#molecule-vs-trace)
- **N:** [NaN sentinel](#withheld-factor-nan-sentinel) · [ND-Normalized / ND-Raw](#nd-normalized-nd-raw) ·
  [never-auto-drop](#precisionk-never-auto-drop) · [`NO_STATE`](#no_state)
- **P:** [partner-in-both colocalization](#partner-in-both-colocalization) ·
  [photobleach frames](#photobleach-frames-first-bleach-frame) ·
  [post-synchronization](#post-synchronization) · [precision@k](#precisionk-never-auto-drop) ·
  [project, project file](#tether-project) · [provenance file](#provenance-file) ·
  [proximity ratio](#proximity-ratio)
- **Q:** [quality class](#curation-label-category-quality-class)
- **R:** [registration / `.tmap`](#registration-tmap) ·
  [RMS gate](#rms-gate-low-confidence-registration)
- **S:** [schema_version](#schema_version) · [sidecar](#sidecar) · [SMD](#smd) ·
  [stage status (batch)](#stage-status-batch) · [stale lock](#write-lock-stale-lock) ·
  [staleness (stale / fresh / live)](#staleness-stale-fresh-live) · [state](#state) ·
  [state path](#viterbi-path-state-path) · [store](#tether-project)
- **T:** [`.tdat`](#tmap-tdat) · [TDP (transition density plot)](#tdp-transition-density-plot) · [`.tether` project](#tether-project) · [TIRF](#tirf) ·
  [`TIRFdata` / MCOS / `#refs#`](#tirfdata-mcos-refs) · [`.tmap` / `.tdat`](#tmap-tdat) ·
  [trace generation](#trace-generation) ·
  [transient vs deterministic sidecar failure](#transient-vs-deterministic-sidecar-failure)
- **V:** [vbFRET](#vbfret-ebfret-consensus-vb-hmm) ·
  [Viterbi path / state path](#viterbi-path-state-path)
- **W:** [window (always with a qualifier)](#window-always-with-a-qualifier) ·
  [withheld factor / NaN sentinel](#withheld-factor-nan-sentinel) ·
  [write lock / stale lock](#write-lock-stale-lock)

---

## FRET efficiency and the correction factors

### proximity ratio

The acceptor intensity divided by the sum of the donor and acceptor intensities, frame by frame,
with no photophysical correction. In Tether it is another name for apparent E.

Formula: `A / (D + A)`. Tether has one implementation of it
([McCann 2010](https://doi.org/10.1016/j.bpj.2010.04.063)). See [apparent E](#apparent-e).

### apparent E

The FRET efficiency from the background-subtracted intensities, with no leakage or γ correction.
Every population plot in Tether shows apparent E, also after a correction succeeds.

Tether defines it as corrected E with α = 0 and γ = 1, so the two cannot drift apart. It is
not clipped to `[0, 1]`. A frame with zero total intensity gives NaN, drawn as a gap, never `0`.
The plot does not show whether a correction was applied: read
[correction_method](#correction_method). See
[Proximity ratio versus corrected E](pipeline.md#proximity-ratio-versus-corrected-e). Code:
`tether.fret.efficiency.apparent_fret`.

### corrected E (accurate FRET)

The FRET efficiency after Tether subtracts donor leakage (α) from the acceptor and corrects the
detection imbalance (γ). In prose, "corrected E" and "corrected FRET" mean this value only.

Formula: `I_A,corr = I_A − α·I_D`, a bare `I_D,corr = I_D`, and
`E = I_A,corr / (I_A,corr + γ·I_D,corr)`
([Hellenkamp 2018](https://doi.org/10.1038/s41592-018-0085-0)). Tether does not scale the donor
by `(1 + α)` as Deep-LASI does, so the γ of the two tools is different on the same data
([What per-molecule γ changes](pipeline.md#what-per-molecule-changes-about-a-published-number);
ADR-0028). Code: `tether.fret.efficiency.corrected_fret`.

### correction order

The fixed order of the correction steps. Tether refuses a step that runs out of order, and the
message names the step to run first.

Intensities: background → leakage α → direct excitation δ (= 0) → γ, the same in
`tether.fret.efficiency`, `.leakage` and `.gamma`. Project: photobleach → leakage α → γ →
corrected FRET (`tether.project.correct`). See
[a correction step says to run something first](troubleshooting.md#a-correction-step-refuses-run-something-else-first).

### α (leakage, bleedthrough)

The donor emission that the acceptor channel detects. Tether subtracts it from the acceptor only,
and uses one value for the whole project, not one value for each condition.

Tether estimates α from each trace's post-acceptor-bleach tail as `mean(I_DA) / mean(I_DD)`, a
ratio of means and not a mean of per-frame ratios. A trace qualifies with a tail of at least 20
frames (`DEFAULT_MIN_WINDOW_FRAMES`) and an α in `[0, 0.3]` (`LEAKAGE_CEILING`). The dataset
value is the median of the qualifying traces. Below 10 of them
(`DEFAULT_MIN_QUALIFYING_TRACES`) Tether withholds it rather than fabricate it (ADR-0027). See
[Leakage is one value for the whole project](reference/parameters.md#leakage-is-one-value-for-the-whole-project).
Code: `tether.fret.leakage`.

### γ (gamma, detection-correction)

The ratio of detection efficiency and quantum yield between the two channels. Tether keeps one γ
for each molecule, and a molecule that does not qualify takes the dataset median.

Tether estimates γ across the acceptor-bleach step as `ΔI_A / ΔI_D` on leakage-corrected
intensities, with the levels averaged over 20 frames each side
([ADR-0130](adr/0130-the-gamma-levels-are-averaged-over-20-frames-each-side.md)). The segments
before and after the step must each be longer than 20 frames. A per-trace γ outside
`(0, 5.0]` (`GAMMA_CEILING`) is dropped, and the 10-trace minimum of α applies (ADR-0028). See
[How Tether estimates gamma](reference/parameters.md#how-tether-estimates-gamma). Code:
`tether.fret.gamma`.

### δ (delta, direct excitation)

Direct excitation of the acceptor by the donor laser. Tether cannot measure it, so it stores δ
as a fixed 0.0, which does not mean "unset".

Measuring δ needs the acceptor-under-acceptor-excitation channel that only [ALEX/PIE](#alex-pie)
gives. Tether is single-laser two-color by design (non-goal N1; ADR-0008). Code:
`rows["delta"] = 0.0` in `tether.imaging.extract`. Deep-LASI uses the opposite Greek letters
for leakage and direct excitation: see
[Correction-factor remap](io/legacy-import.md#correction-factor-remap).

### correction_method

The stamp on each molecule that records how Tether computed its E. It has exactly four values.

| Value | Meaning |
|---|---|
| `corrected` | estimated α **and** γ were applied |
| `manual` | hand-entered factors were applied |
| `apparent-E (corrections unavailable)` | total correction failure: the *expected* case for a pure-FRET acquisition with no clean acceptor-bleach step |
| `apparent-E (user toggle)` | valid factors exist, and the user chose the apparent-E view |

Paired with `correction_confidence`, `1.0` or `0.0`: a provenance flag, not a statistical interval (`tether.project.correct`).

### withheld factor / NaN sentinel

A NaN in a molecule's α or γ cell, which means that the estimator withheld the factor because too
few traces qualified. It is the expected sign of a withhold, not damage to the file.

A missing factor never becomes a NaN in E: Tether gives that molecule apparent E (ADR-0003). E
can still be NaN on a frame where its denominator is zero, for example a frame with zero total
intensity. That per-frame NaN is not this sentinel, and neither is the `-1`
[undetected sentinel](#photobleach-frames-first-bleach-frame) or [`NO_STATE`](#no_state). See
[Two sentinels](reference/tether-format.md#two-sentinels-and-two-derivations-worth-reading-twice) and
[the batch says `correct=done` but no corrections were applied](troubleshooting.md#the-batch-says-correctdone-but-no-corrections-were-applied).

---

## Photobleaching, analysis windows and curation

### photobleach frames (first-bleach frame)

The first frame at which each dye bleaches, stored as a donor and acceptor pair of absolute frame
indices. Tether detects each channel on its own, so α and γ each get the frame that they need.

- `(-1, -1)` is the undetected sentinel: the detector has not run, and later steps refuse to guess.
- A value equal to `frame_range[1]` means that the detector ran and the channel does not bleach
  within the trace.

Stored in `/molecules.bleach_frames`. The detector reimplements tMAVEN's Bayesian single-step
change-point model and runs on the background-subtracted layer by default
(`tether.fret.photobleach`; ADR-0026). See [Why bleach frames come first](pipeline.md#why-bleach-frames-come-first-and-why-your-analysis-window-wins).

### analysis window

The start and end frame of the part of a trace that histograms, cross-correlation, features and
idealization read. By default it runs from the trace start to the first bleach of the summed donor
and acceptor intensity.

Each reader uses `frame_range` when it is unset. The α and γ estimators do not use it: they read
the full trace and its bleach frames. A manual analysis window wins: Tether writes the automatic
value only where the analysis window still equals the extraction default (ADR-0026). Stored in
`/molecules.analysis_window`. See
[Why your analysis window wins](pipeline.md#why-bleach-frames-come-first-and-why-your-analysis-window-wins).

### window (always with a qualifier)

A word that these pages never use alone. Each use names one of these six kinds.

1. **analysis window**: frames, see [analysis window](#analysis-window).
2. **aperture window**: the 21 × 21 pixel crop side, a positive odd integer. The CLI flag and
   the stored setting keep the name `window` (`tether.imaging.aperture`, `tether.project.extract`).
3. **estimator windows**: the leakage tail window and γ's 20-frame half-window, in frames.
4. **model window**: the deep model's 500-frame input `window_length` ([Deep trace classifier](ml/deep-classifier.md)).
5. **application window**: the Tether or tMAVEN desktop window, with its main window and splash window.
6. **post-synchronization window**: the frames binned around each transition (`single_dwell`,
   `sync_preframe`; see [post-synchronization](#post-synchronization)).

### curation label / category / quality class

Three independent fields on each molecule: the accept or reject state, an optional category, and
the quality class from the ranker. A category does not imply accept.

- `curation_label`: `UNCURATED = 0`, `ACCEPT = +1`, `REJECT = -1` (codec `CurationLabel` in `tether.project.labels`; ADR-0023).
- `category`: an optional value from the editable list of each condition.
- `quality_class`: read-only ML ranker output, never a user input.

A reject is a reversible tag, never a deletion. The curation filter leaves rejected molecules
out by default (`include_rejected=False`). See [Curate traces](how-to/curate-traces.md) and
[`/labels`](reference/tether-format.md#labels-the-append-only-curation-log).

---

## Imaging, extraction and registration

### TIRF

Total internal reflection fluorescence microscopy, which excites only an evanescent field about
100 nm above the coverslip. It gives the signal-to-background that surface-immobilized single
molecules need.

Tether's input is a dual-view TIRF movie, and Tether does not model the optics
([Axelrod 2003](https://doi.org/10.1016/S0076-6879(03)61003-7)).

### ALEX / PIE

Alternating-laser excitation and pulsed interleaved excitation, two acquisition schemes that add
an acceptor-under-acceptor-excitation channel. Tether does not support them.

The extra channel enables direct-excitation (δ) and stoichiometry correction
([Lee 2005](https://doi.org/10.1529/biophysj.104.054114)). Tether is single-laser two-color only
(non-goal N1), which is why [δ](#delta-direct-excitation) is always zero.

### dual-view

An imaging layout that puts the donor and the acceptor side by side on one camera chip, through
a splitter. Each frame holds two half-width channels that Tether registers to each other.

`--donor-side` names the horizontal half that holds the donor, and the
[registration](#registration-tmap) map relates the two halves. A wrong `--donor-side` raises no
error: see [the donor-side check](cli.md#get-donor-side-right-and-read-the-donor-side-check).

### aperture

The 21 × 21 pixel crop that Tether sums to get a molecule's intensity in one frame. It holds a
central disk for the spot and a concentric ring for the local background.

The PSF disk has a radius of 3 px. The background ring has an inner radius of 6 and an outer
radius of 8, and the gap between them keeps the PSF tail out of the ring. Integration is a sum
(top-hat): `I = sum(crop · disk) − ring_background · N_psf`. The result is the
[background-subtracted](#background-subtracted-intensity-corrected) layer, which is not
photophysically corrected. Code: `tether.imaging.aperture`, `tether.project.trace_layers`.

### background-subtracted intensity (`corrected`)

The trace layer with the local background removed: the aperture sum minus the ring background.
It is not corrected for leakage or γ.

Tether stores this layer as `intensity_quantity="corrected"`. It is the default layer for
photobleach detection, the correction factors, idealization and the analyses. These pages write
"background-subtracted" for it in prose, and write the stored value `corrected` in code
formatting only.

### donor-anchored colocalization

The colocalization rule that keeps every in-frame donor spot as a molecule, with or without a
detected acceptor spot. It is the option to the default partner-in-both rule.

Tether reads the acceptor at the mapped position. The rule keeps the low-FRET and acceptor-dark
molecules that Deep-LASI's `findColoc` discards, because it does not need an
independently-detected partner within 3 px in every channel. Select it with
`--colocalization donor-anchored`. The independent-detection result is still stored in
`/molecules/acceptor_detected` (`tether.imaging.coloc`; ADR-0015, ADR-0127). A project extracted
before the rule could be chosen reads as donor-anchored.

### partner-in-both colocalization

The default colocalization rule, which is also Deep-LASI's rule. A donor spot becomes a molecule
only when an acceptor spot is detected within 3 px of its mapped position.

The acceptor is still read at the mapped donor position, so a molecule that both rules keep has
the same traces under both. The rule drops molecules with a dark or weak acceptor. Use
[donor-anchored](#donor-anchored-colocalization) to keep them (ADR-0127). See
[Colocalize](pipeline.md#colocalize).

### registration / `.tmap`

The map that carries a donor coordinate to the acceptor coordinate in the other half of a
dual-view movie. Tether fits it from the movie, or applies a Deep-LASI map file.

- **native**: a fit from control points paired out of the movie's own detections, a degree-2
  polynomial at 6 pairs or more and a 4-DOF similarity below that.
- **imported**: a Deep-LASI [`.tmap`](#tmap-tdat) applied as-is. Its residual is NaN, so it can
  never trip the RMS gate. `--donor-side` is ignored, because the `.tmap`'s crop geometry gives
  the split.

Both are a `RegistrationMap` (`tether.imaging.calibrate`), stamped with its `source`.

### RMS gate / low-confidence-registration

The quality check on a registration map: Tether compares the RMS residual of the fit, in pixels,
with a gate of 0.5 px. A map over the gate is kept and flagged, never silently dropped.

The calibration is marked `low_confidence`, and every molecule that it gives is tagged
`low-confidence-registration` (`LOW_CONFIDENCE_TAG`; ADR-0014). Batch policy is `warn`
(accept with a flag) by default and `fail` on request. Nothing downstream filters on the tag
yet. Code: `DEFAULT_RMS_GATE_PX` in `tether.imaging.calibrate`, flag `--rms-gate`. See
[registration was flagged low-confidence](troubleshooting.md#registration-was-flagged-low-confidence)
and [a suspiciously perfect residual](troubleshooting.md#registration-passed-the-gate-with-a-suspiciously-perfect-residual).

### à trous / starlet wavelet, MAD

The undecimated wavelet transform whose multiscale product is Tether's default spot detector.
MAD, the median absolute deviation, is its noise estimate at each scale.

([Olivo-Marin 2002](https://doi.org/10.1016/S0031-3203%2801%2900127-3)). The alternatives are
the `intensity` and `bandpass` modes, ported from Deep-LASI's `findPart` (ADR-0021).

---

## Idealization and kinetics

### idealization

A hidden Markov model fitted to the traces and saved as additive data in the project file. In
Tether the word means the saved fit, not only the act of fitting.

Stored under `/idealization/{model_name}` (`tether.project.idealize`). The fit runs in the
isolated [sidecar](#sidecar), which drives tMAVEN headlessly
([Verma 2024](https://doi.org/10.1016/j.bpj.2024.01.022)). See
[Idealize traces into FRET states](how-to/idealize.md).

### vbFRET / ebFRET / consensus VB-HMM

The three hidden Markov model families that Tether reaches through tMAVEN. Tether always saves a
consensus model, one shared model for the population, and never independent per-trace models.

- **vbFRET**: variational-Bayes per-trace HMM ([Bronson 2009](https://doi.org/10.1016/j.bpj.2009.09.031)).
- **ebFRET**: empirical-Bayes population HMM ([van de Meent 2014](https://doi.org/10.1016/j.bpj.2013.12.055)).
- **consensus VB-HMM** (`vbconhmm`): one shared model fitted across the population, the default
  `MODEL_TYPE_DEFAULT`.

Two tMAVEN plots that read per-trace fits are therefore re-derivations, not ports: see the
[parity gallery](analysis/parity-gallery.md#c1-vbfret-state-number-distribution).

### ELBO

The evidence lower bound, the objective of a variational fit. Tether uses it to choose the
number of states.

`nstates` is either fixed or auto-selected as the maximum ELBO over
`NSTATES_GRID_DEFAULT = (1, 2, 3, 4)`, and Tether records which in
`nstates_selected_by ∈ {"max-elbo", "fixed"}` (ADR-0024).

### state

An index into the shared list of FRET levels of a fitted model, so each state is one FRET level.
A state is not a label that a person assigns.

Code: an index into the model's `means` vector.

### Viterbi path / state path

The sequence of state indices, one for each frame, that a fitted model gives a molecule. Every
dwell, transition and state-count analysis reads it.

Stored as `state_paths`, an `int64 (n_molecules, n_frames)` array. Tether gets it by
nearest-mean assignment of the float idealized level (`states_from_idealized` in
`tether.idealize.driver`).

### `NO_STATE`

The value that marks a frame of a state path with no assigned state. Such a frame is never
itself a dwell and never a transition.

`NO_STATE = -1` (`tether.idealize.driver`). It fills a path outside the analysis window, in
interior gaps, and wherever the idealized level is not finite. `state_dwells` strips it, so the
same-state runs on both sides of an interior gap become one dwell. `dwells_from_states` ends a
run at it. See
[How a `NO_STATE` gap changes the dwells](reference/exports.md#how-a-no_state-gap-changes-the-dwells).

### dwell time / dwell-time survival

A dwell is one run of constant state in a Viterbi path. Dwell-time survival is the fraction of
the dwells that last longer than each dwell time.

As in tMAVEN, Tether drops the last dwell of each molecule always and the first unless
`include_first`, because neither is fully observed (`tether.analysis.dwell`). Survival is the empirical
`S(τ) = P(dwell > τ)`, fitted with single, double, triple or stretched exponentials, with covariance standard errors and
Student-*t* confidence intervals. Fitted rates are per frame unless you pass the movie's frame
time as `dt` (`DEFAULT_DWELL_DT = 1.0`). See
[How a `NO_STATE` gap changes the dwells](reference/exports.md#how-a-no_state-gap-changes-the-dwells).

### TDP (transition density plot)

A 2-D histogram of the initial and the final idealized FRET level at each state change of a
population. Tether builds it from a saved model's Viterbi paths only.

([McKinney 2006](https://doi.org/10.1529/biophysj.106.082487)). A point is emitted only at a
state-change frame, with initial and final read `DEFAULT_TDP_NSKIP = 2` frames apart
(`tether.analysis.tdp`). The stored array is the exact unsmoothed histogram in raw counts. Log
normalization and smoothing are display-only. The plot export draws a zero-safe log color scale
by default and records the scale in the provenance file.

### post-synchronization

The heatmap variant that aligns every selected state jump to a common column. Transitions at
different times then add up, and show the population's average approach to and departure from
a transition.

This is plot A2b. Relative time zero sits at `DEFAULT_SYNC_PREFRAME = 50`, and the time edges
before it are negative (`tether.analysis.histogram`; see
[A2](analysis/parity-gallery.md#a2-2-d-time-vs-signal-histogram-synchronized-fret-heatmap)).

### staleness (stale / fresh / live)

The state of a molecule in a saved model whose inputs changed after the fit. It is a Tether-only
concept with no tMAVEN analog, and it gates most analyses.

Each molecule carries a hash of its windowed input trace, its analysis window bounds and its
effective applied α and γ. When one of these changes, the hash diverges and the molecule is
stale. `live_molecule_keys()` gives the rest, which the TDP, dwell, state-number and
transition-probability analyses keep by default (ADR-0029). Because α is project-wide,
re-estimating α makes every molecule stale. A γ-median shift makes stale only the molecules that
took the fallback. See [After you change the project](how-to/idealize.md#after-you-change-the-project).

### fresh-idealization gating / curation filter

The two filters, on by default, of every project-level analysis that reads a saved model (groups
B and C of the parity gallery). They leave out stale molecules and rejected molecules.

Pass `include_stale=True` or `include_rejected=True` to keep them. Both are toggles, not
deletions.

### precision@k / never-auto-drop

The metric of the quality ranker, and its contract: the ranker only re-orders the molecules and
never drops one by itself.

A ranking is a permutation. A molecule that cannot be scored gets a NaN score, never a
fabricated `0`, is ranked last, and is kept (`tether.ml.ranking`).

### kinSoftChallenge

A blind community benchmark of single-molecule kinetics analysis tools. Tether uses it as its
advisory kinetics oracle.

([Götz 2022](https://doi.org/10.1038/s41467-022-33023-3); ADR-0048).

---

## Plot vocabulary carried over from tMAVEN

### ND-Normalized / ND-Raw

Two tMAVEN plot presets that put intensity, not FRET, on the signal axis. Tether does not port
them, because its signal axis is always apparent E.

In tMAVEN, `ND Normalized` labels the axis *Normalized Intensity* over `[-0.25, 1.25]` and
`ND Raw` labels it *Intensity (A.U.)* over `[-500, 10000]`, against `smFRET`'s *E_FRET* over
`[-0.25, 1.25]` (`tmaven/tmaven/controllers/analysis_plots/data_hist1d.py`,
`normalized_defaults` / `raw_defaults`). In Tether, `intensity_quantity` only selects the
`/traces` layer (`corrected` or `raw`) that feeds the axis. The terms appear on this site only
because the [parity gallery](analysis/parity-gallery.md) lists tMAVEN's variants.

---

## Files and formats

### molecule vs trace

A molecule is one donor spot in one movie, one row in the molecule table. A trace is that
molecule's intensities over time, its row in the trace arrays.

- `molecule_id`: a globally stable UUID, kept unchanged by any split or subset file.
- `molecule_key`: the cross-file content identity, `sha256(movie_sha256 | quantized donor_xy)`
  at `MOLECULE_KEY_QUANTUM_PX = 0.1` px. It is the join key for split-file merge-back and it is
  not unique, so use `molecule_id` when uniqueness matters.

The trace arrays are zero-padded to the longest movie of the experiment, and `frame_range`
marks the valid native frames inside the pad.

### condition

The experimental condition of a molecule, identified by its chemistry and optics key and not by
free text. Date, replicate, source file and project file vary within one condition.

The key: construct or variant, dye, ligand and concentration, buffer, temperature and laser
power (`tether.io.filename`, `tether.project.conditions`; ADR-0068). `condition_id` is a content
hash of that key, valid only if it resolves to a `/conditions` row whose fields hash back to it.
A key parsed from a filename is provisional, kept in `condition_id_provisional`, and gives no
buffer, temperature or laser power. Near-miss parses stay separate until an explicit confirmed
re-key (ADR-0033). See [`/conditions`](reference/tether-format.md#conditions-the-structured-experimental-conditions).

### `TIRFdata` / MCOS / `#refs#`

Deep-LASI's custom MATLAB class, stored as MATLAB Class Object System (MCOS) objects inside a
version 7.3 (HDF5) project file. Tether decodes it to import a Deep-LASI session.

Decoding it needs the `#refs#` and `#subsystem#` blobs, which `tether.io.mcos` resolves. See
[TIRFdata decode](io/legacy-import.md#tirfdata-decode).

### `.tmap` / `.tdat`

Deep-LASI's two file types: the registration-map file and the full-session project file. The
map file is a classic MATLAB version 5 MAT-file, and the session file is MATLAB version 7.3.

The `.tmap` keeps its transform coefficients in the MCOS `__function_workspace__` blob, in MATLAB
1-based pixel coordinates (`tether.imaging.register.read_tmap`). The `.tdat` carries
coordinates, correction factors and the particle-detection mode.

### SMD

Single-Molecule Dataset, a general HDF5 storage format for single-molecule data and tMAVEN's
exchange format. Tether writes a superset of it that also carries coordinates.

([Greenfeld 2015](https://doi.org/10.1186/s12859-014-0429-4); ADR-0002). A tMAVEN-written SMD has
no per-molecule metadata slot. So after a standalone-tMAVEN round trip, the link from trace to
movie survives only by exact intensity-trace matching. See the
[hand-off page](idealize/standalone-tmaven-handoff.md).

### `.tether` project

A project is the unit of work that you open and save in Tether. The project file is the
.tether file that holds it, one HDF5 file for each experiment.

An experiment is one day or one session of acquisition. A project can hold several movies and
several conditions, and one condition can span several projects (ADR-0068). `tether extract`
writes a project of one movie, and
[Add another movie to a project](how-to/extract-traces.md#add-another-movie-to-a-project) adds
the others. "Store" names the HDF5 layout inside a project file, and only the
[format reference](reference/tether-format.md) and the contributor pages use it. The root
attribute `format = "tether-project"` and the [schema_version](#schema_version) mark the file.

### trace generation

One extraction of one movie. Each new extraction of the same movie, for example with a new map,
is the next generation.

The first extraction is generation 1. The shared trace arrays hold only the active (newest) generation. Before its rows are replaced, the previous generation is copied unchanged to
`/generations/archive/<movie_id>/<generation>`, and `/generations/table` records one row per generation (`tether.io.generations`). A molecule within 0.5 px (`GENERATION_MATCH_PX`) of its
closest partner keeps its `molecule_key`, label, category and analysis window. Every fit and
correction of the movie becomes stale
([ADR-0136](adr/0136-a-new-extraction-of-a-movie-is-a-new-trace-generation.md),
[`/generations`](reference/tether-format.md#generations-the-trace-generations-of-each-movie)).

### provenance file

The JSON file that every export writes beside its output. It records the Tether version, the
source project and the export's own parameters.

The file name ends in `.provenance.json`. A table or subset export names it after the output file (`molecules.csv.provenance.json` beside
`molecules.csv`). A plot export writes one for all its formats, named after the stem
(`figure.provenance.json` beside `figure.png`, `figure.pdf` and `figure.svg`). It has no
relation to the [sidecar](#sidecar). See [Provenance file](reference/exports.md#provenance-file).

### analysis-only import

An import of traces without a movie, so the project has no coordinates or image patches.
Idealization, histograms, TDP and kinetics still work.

Every molecule is tagged `round-trip-unavailable`, and `/settings/analysis_only` records the
banner `coordinates and patches absent; movie round-trip and spot/overlap views unavailable`.
The shell leaves the spot/overlap seam unwired (ADR-0046). See
[Round-trip vs analysis-only](io/legacy-import.md#round-trip-vs-analysis-only).

---

## Running Tether

### sidecar

The isolated conda environment, with tMAVEN, PyQt5 and a NumPy older than version 2, in which
idealization runs. These pages use "sidecar" for this environment only.

tMAVEN's pins cannot share a process with Tether's PySide6 and current-NumPy stack (ADR-0004).
Tether looks for the interpreter in this order: an explicit argument, `TETHER_SIDECAR_PYTHON`,
then the installer's sibling `envs/sidecar` derived from `sys.prefix` (ADR-0051). The last one
exists because a menu shortcut, `PATH` shim or `.desktop` launch never runs the conda
`activate.d` hook that sets the variable. The JSON file beside an export is the
[provenance file](#provenance-file), and the `.lock` file beside an open project is the
[lock file](#write-lock-stale-lock).

### transient vs deterministic sidecar failure

The two kinds of sidecar failure. Tether retries a transient failure and never retries a
deterministic one.

- **transient**: a crash or a timeout. Restarted up to `--max-restarts` times (default 3).
- **deterministic**: a fit error that the sidecar itself reported, or a missing interpreter. A
  restart cannot change the outcome.

Code: `SidecarError.transient` in `tether.idealize.driver`.

### stage status (batch)

The word that the batch command prints for each stage of each movie. It tells you whether the
stage ran and whether it counts as a failure.

The values are `done`, `skipped`, `failed`, `blocked`, `deferred`, `not-requested` and
`warning` (`tether.project.batch`). Only `failed` makes the run exit `1`. A `deferred` stage in a
run with no `failed` stage makes it exit `3`: see
[a deferred idealization exits 3](troubleshooting.md#the-batch-finished-ok-but-nothing-was-idealized).
The [stage status table](cli.md#stage-status-in-the-batch-summary) gives the meaning of each.

### write lock / stale lock

The lock file beside a project that lets one process at a time write the project. A lock older
than about 30 minutes is stale, and only an explicit steal reclaims it.

The file is `<project>.tether.lock`, in JSON, with `host / user / pid / timestamp` and a nonce for each acquisition
(`tether.project.lock`). Ownership is the full `(host, user, pid)` triple, so a recycled PID
under another login never gets write access. Tether judges liveness by the wall-clock timeout,
not by a PID probe, because a remote PID cannot be probed and cloud sync is eventually
consistent. The lock file is not part of the schema.

### schema_version

The version number on the root of a project file, currently 3. Tether refuses a file whose
number is higher than its own: the app upgrades, and the file is never downgraded.

The number only goes up (`tether.io.schema`). The whole group skeleton was declared when the
schema was frozen (ADR-0005), and the structure changes only in a versioned step with an ADR and
a migration test. Version 2 added the `/generations` record of [trace generations](#trace-generation)
([ADR-0136](adr/0136-a-new-extraction-of-a-movie-is-a-new-trace-generation.md)), and version 3 the
`/sources` registry of the files a project was made from
([ADR-0140](adr/0140-a-source-registry-records-each-source-file-and-what-was-derived-from-it.md)).
A `schema-guard` CI gate refuses a change that removes, renames or retypes a frozen field.

---

## Related pages

- [Troubleshooting](troubleshooting.md): symptom-keyed failure modes, including the silent ones.
- [Seven-plot parity gallery](analysis/parity-gallery.md): where the plot vocabulary is checked against tMAVEN.
- [Legacy Deep-LASI import](io/legacy-import.md): `.tdat` / `.tmap` / `.mat` formats and the correction-factor remap.
- [Standalone-tMAVEN hand-off](idealize/standalone-tmaven-handoff.md): the sidecar and the SMD round trip.
- [Deep trace classifier](ml/deep-classifier.md): the optional GPU add-on.
- [Architecture decisions](adr/README.md): the rationale behind most entries above.
