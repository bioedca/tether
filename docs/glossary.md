# Glossary

**Who this page is for.** A bench scientist or new contributor reading the rest of this
site and hitting a term — `apparent E`, `NO_STATE`, `ND-Normalized`, a Viterbi path, α/γ/δ,
`TIRFdata` — that the surrounding page assumes you already know. Every entry below defines
the term **as this codebase uses it**, names the module or constant that pins it, and links
to the page that goes deeper. Where Tether's usage differs from generic smFRET usage, the
entry says so explicitly — those divergences are the ones that cost you a dataset.

Terms are grouped by the stage of the pipeline they belong to. If something went wrong
rather than merely reading oddly, start at [Troubleshooting](troubleshooting.md).

---

## FRET efficiency and the correction factors

### proximity ratio

`A / (D + A)` — acceptor intensity over total intensity, computed frame by frame with no
photophysical correction applied. In Tether this is a synonym for [apparent E](#apparent-e);
there is exactly one implementation
([McCann 2010](https://doi.org/10.1016/j.bpj.2010.04.063)).

### apparent E

`tether.fret.efficiency.apparent_fret(donor, acceptor)`. Defined *literally* as
`corrected_fret(..., alpha=0.0, gamma=1.0)` so the two cannot drift apart. Three Tether
specifics worth knowing:

- It is **not clipped to `[0, 1]`** — a corrected value slightly outside that range on a
  noisy frame is a real fluctuation, and hiding it would be a silent distortion.
- A zero denominator yields **NaN**, drawn as a gap, never `0`.
- **Every population plot is always apparent E**, even after a successful correction:
  `tether.analysis.histogram` and `tether.analysis.cloud` call `apparent_fret`
  unconditionally, and the GUI axis label is the literal string `"apparent E"`
  (`tether.gui.histogram_dock`, `tether.gui.trace_dock`). To find out whether corrections
  actually succeeded you must read `/settings/correction`, not the plot — see
  [correction_method](#correction_method).

### corrected E (accurate FRET)

`tether.fret.efficiency.corrected_fret(donor, acceptor, *, alpha, gamma)`, applying
`I_A,corr = I_A − α·I_D`, a **bare** `I_D,corr = I_D`, and
`E = I_A,corr / (I_A,corr + γ·I_D,corr)`
([Hellenkamp 2018](https://doi.org/10.1038/s41592-018-0085-0)).

> **Tether does not scale the donor by `(1 + α)` the way Deep-LASI does.** γ is defined
> consistently with the bare `I_D`, so Tether's γ is systematically ≈`(1 + α)`× Deep-LASI's
> on the same bleach step (≈9 % at α ≈ 0.09). A cross-tool γ comparison must control for
> that (`tether.fret.gamma`; [ADR-0028](adr/0028-gamma-acceptor-bleach-step-estimator.md)).

### correction order

The fixed chain, repeated verbatim in three modules
(`tether.fret.efficiency`, `.leakage`, `.gamma`):
**background → leakage α → direct excitation δ (= 0) → γ**. At the store level the chain is
**photobleach → leakage α → γ → corrected FRET** (`tether.project.correct`) and it is
enforced at runtime — calling the steps out of order raises with the next command named in
the message (see
[a correction step says to run something first](troubleshooting.md#a-correction-step-refuses-run-something-else-first)).

### α (leakage, bleedthrough)

Donor emission detected in the acceptor channel, applied **additively to the acceptor only**
(`tether.fret.leakage`). Tether estimates it from each trace's own **post-acceptor-bleach
tail** as `mean(I_DA) / mean(I_DD)` — a ratio of window means, not a mean of per-frame
ratios. Gates: tail ≥ `DEFAULT_MIN_WINDOW_FRAMES = 20` frames; per-trace α outside
`[0, LEAKAGE_CEILING = 0.3]` dropped; the dataset value is the **median** of qualifying
traces and is **withheld** below `DEFAULT_MIN_QUALIFYING_TRACES = 10` rather than fabricated
([ADR-0027](adr/0027-leakage-alpha-tail-estimator.md)).

**Scope:** α is **store-wide** in Tether. `compute_leakage_alpha` fits one dataset median over
every analysable molecule in the `.tether` — it does **not** partition by
[condition](#condition) — and writes that single value into every one of their
`/molecules.alpha` cells, which is the field `tether.project.correct` reads back as the
applied factor. The estimator's provenance lands on `/settings/leakage`.
`/conditions.leakage_alpha` also exists in the schema (`tether.io.schema`), but it is a
**curator-editable field** whose only writer is `register_condition(..., leakage_alpha=...)`
(the idempotent upsert in `tether.project.conditions` — there is no separate
`update_condition`; the same call inserts a missing row and updates the provided fields of an
existing one). Nothing in the correction chain reads it and the estimator never writes it.
ADR-0027 describes moving the applied α there per condition, but files that under *Deferred*
— it has not happened, so read that ADR's "α is per-condition" as intent, not as current
behaviour.

### γ (gamma, detection-correction)

The detection-efficiency / quantum-yield ratio between the channels
(`tether.fret.gamma`). Estimated across the **acceptor-bleach step** as `ΔI_A / ΔI_D` on
leakage-corrected intensities, with levels averaged over a 3-frame half-window each side.
Same 20-frame / 10-trace gates as α; per-trace γ outside `(0, GAMMA_CEILING = 5.0]` is
dropped ([ADR-0028](adr/0028-gamma-acceptor-bleach-step-estimator.md)).

**Scope:** γ is **per-molecule with a population-median fallback** — a qualifying molecule
keeps its own γ, a non-qualifying one silently takes the dataset median and is counted in
`/settings/gamma.attrs["n_fallback"]`. There is no per-row marker distinguishing the two.

### δ (delta, direct excitation)

Direct excitation of the acceptor by the donor laser. Measuring it requires the
acceptor-under-acceptor-excitation channel that only [ALEX/PIE](#alex-pie) provides, and
Tether is single-laser two-colour by design (non-goal N1) — so δ is carried as an **inert
`0.0`**, not "unset" (`rows["delta"] = 0.0` in `tether.imaging.extract`;
[ADR-0008](adr/0008-correction-factor-remap.md)).

> **The naming inversion trap.** Deep-LASI's stored `Beta` is what Tether calls **α**
> (leakage), and Deep-LASI's stored `Alpha` is direct excitation, which Tether calls **δ**
> and forces to `0`. Reading Deep-LASI `Alpha` as Tether α drops a real leakage correction
> and shifts every efficiency. See
> [Correction-factor remap](io/legacy-import.md#correction-factor-remap).

### correction_method

The per-molecule provenance stamp on `/molecules` telling every reader *how* E was
computed (`tether.project.correct`). Exactly four literal values:

| Value | Meaning |
|---|---|
| `corrected` | estimated α **and** γ were applied |
| `manual` | hand-entered factors were applied |
| `apparent-E (corrections unavailable)` | total correction failure — the *expected* case for a pure-FRET acquisition with no clean acceptor-bleach step |
| `apparent-E (user toggle)` | valid factors exist; the user deliberately chose the apparent-E view |

Paired with `correction_confidence` — `1.0` or `0.0`, explicitly **a provenance flag, not a
statistical interval**.

### withheld factor / NaN sentinel

α or γ arriving as **NaN** means the min-qualifying-traces gate withheld it. The NaN **stays
in `/molecules.alpha` / `.gamma`** — `compute_corrected_fret` never overwrites a withheld
factor, it only stamps `correction_method` / `correction_confidence` — so NaN factors sitting
next to `apparent-E (corrections unavailable)` are the *expected* signature of a withhold, not
corruption (see
[the batch says `correct=done` but no corrections were applied](troubleshooting.md#the-batch-says-correctdone-but-no-corrections-were-applied)).
What the writer never emits is a NaN **corrected E**: a non-finite factor routes the molecule
to apparent E instead ([ADR-0003](adr/0003-apparent-e-never-nan.md)). This NaN is distinct
from the `-1` [undetected sentinel](#photobleach-frames-first-bleach-frame) and from
[`NO_STATE`](#no_state).

---

## Photobleaching, windows and curation

### photobleach frames (first-bleach frame)

`/molecules.bleach_frames` — a `(donor, acceptor)` pair of **absolute** frame indices.
Tether's detector is a headless reimplementation of tMAVEN's Bayesian **single-step
change-point** model (`tether.fret.photobleach`), run **independently per channel** so α
and γ each get the bleach frame they need
([ADR-0026](adr/0026-photobleach-detection-and-window-default.md)).

Two values must not be confused:

- `(-1, -1)` — the **undetected sentinel**: the detector has not run. Downstream steps
  refuse loudly rather than guess.
- `== frame_range[1]` — the detector ran and that channel does not bleach within the trace.

Detection runs on **background-subtracted (`corrected`)** traces by default, because the
model detects a decay to `N(0)`; raw traces keep a large offset and never look bleached.

### analysis window

`/molecules.analysis_window` — an `(start, end)` frame pair, the universal slicing contract
every downstream reader honours (histograms, cross-correlation, features, idealization),
each falling back to `frame_range` when it is unset.

The **auto** default is trace start → first bleach of the **summed** donor+acceptor
intensity — not per-channel ([ADR-0026](adr/0026-photobleach-detection-and-window-default.md)).
**Manual wins**: the auto value is written only where the window still equals the extraction
default, so a curator-narrowed window is never overwritten.

### window (overloaded — four meanings)

1. **analysis window** — frames, above.
2. **aperture window** — the `21 × 21` **pixel** crop side length (`tether.imaging.aperture`,
   `tether.project.extract`); must be a positive **odd** integer.
3. **estimator windows** — the leakage tail window and γ's 3-frame half-window, in frames.
4. **deep-model `window_length`** — 500 frames, the ML classifier's input length
   ([Deep trace classifier](ml/deep-classifier.md)).

### curation label / category / quality class

Three **independent** fields on `/molecules` (PRD §5.1):

- `curation_label` — the accept/reject state. Codec `CurationLabel` in
  `tether.project.labels`: `UNCURATED = 0`, `ACCEPT = +1`, `REJECT = -1`
  ([ADR-0023](adr/0023-curation-label-codec-and-labels-log.md)).
- `category` — an optional value from the editable per-condition list. Assigning one does
  **not** imply accept.
- `quality_class` — read-only ML ranker output, never a user input.

A **reject is a reversible sticky tag, never a deletion**: it persists, carries across files
on [molecule_key](#molecule-vs-trace), is one-click reversible, and is excluded by the
**toggleable** curation filter (`include_rejected=False` by default). Every keystroke also
appends one provenance-stamped row to `/labels/table`.

---

## Imaging, extraction and registration

### TIRF

Total internal reflection fluorescence microscopy — the illumination geometry that confines
excitation to an evanescent field ≈100 nm above the coverslip, giving the
signal-to-background needed for surface-immobilized single molecules
([Axelrod 2003](https://doi.org/10.1016/S0076-6879(03)61003-7)). Tether's input is a
dual-view TIRF movie; it does not itself model the optics.

### ALEX / PIE

Alternating-laser excitation / pulsed interleaved excitation — acquisition schemes that add
an acceptor-under-acceptor-excitation channel, enabling direct-excitation (δ) and
stoichiometry correction ([Lee 2005](https://doi.org/10.1529/biophysj.104.054114)).
**Out of scope for Tether** (non-goal N1): single-laser, two-colour only. This is why
[δ](#delta-direct-excitation) is structurally zero.

### dual-view

Donor and acceptor imaged side by side on one chip through a splitter, so each frame is two
half-width channels that must be registered to each other. `--donor-side` names which
horizontal half is the donor; the [registration](#registration-tmap) map relates the two.

> Getting `--donor-side` wrong is silent and produces a complete, plausible, inverted
> dataset — see
> [every efficiency is mirrored](troubleshooting.md#every-efficiency-is-mirrored-donor-side-was-backwards).

### aperture

The `21 × 21` pixel crop holding a central **PSF disk** (radius 3 px) and a concentric
**background ring** (inner 6, outer 8), with a deliberate dead zone between them so the ring
samples background uncontaminated by the PSF tail (`tether.imaging.aperture`). Integration
is a sum (top-hat): `I = sum(crop · disk) − ring_background · N_psf`.

This is what `corrected` means in `/traces` at extraction time — **background-subtracted,
not photophysically corrected** (`tether.project.trace_layers`).

### donor-anchored colocalization

Tether **deliberately breaks** Deep-LASI's rule here. Deep-LASI's `findColoc` keeps a
molecule only if an independently-detected partner exists within 3 px in *every* channel,
which silently discards the low-FRET and acceptor-dark population — exactly the molecules a
FRET histogram must keep. Tether instead anchors on the donor: **every in-frame donor spot
becomes a molecule**, and acceptor intensity is read at the *mapped* position whether or not
an acceptor was independently detected. The independent-detection test survives only as the
informational `ColocalizedMolecules.acceptor_detected` flag and **never drops a molecule**
(`tether.imaging.coloc`; [ADR-0015](adr/0015-donor-anchored-colocalization.md)).

Two adjacent facts: **the movie is never resampled** — the map is applied to *coordinates*,
never pixels, to avoid interpolation bias in integrated intensities; and a molecule is
extractable only if its full aperture lies inside the frame **in both channels**, so
out-of-frame spots are dropped rather than zero-filled.

### registration / `.tmap`

The donor↔acceptor **coordinate** map for the dual-view split. Two sources, one type
(`RegistrationMap` in `tether.imaging.calibrate`), stamped `source`:

- **native** — a fit from control points paired out of the movie's own detections
  (degree-2 polynomial at ≥ 6 pairs, a 4-DOF similarity below that).
- **imported** — a Deep-LASI [`.tmap`](#tmap-tdat) applied as-is. Its residual is left
  NaN, so an imported map can never trip the RMS gate, and `--donor-side` is **ignored**
  (the split comes from the `.tmap`'s own crop geometry).

### RMS gate / low-confidence-registration

The registration quality number is the **RMS residual in pixels**, gated at
`DEFAULT_RMS_GATE_PX = 0.5` (`tether.imaging.calibrate`; `--rms-gate`). This numeric gate is
Tether's improvement over Deep-LASI's visual-only QA.

Over-gate is **never a silent drop**: the calibration is marked `low_confidence` and every
molecule it produces is tagged `low-confidence-registration` (`LOW_CONFIDENCE_TAG`), then
kept ([ADR-0014](adr/0014-registration-map-rms-gate-and-over-gate.md)). Batch policy is
`warn` (accept-with-flag) by default, `fail` on request.

> The residual only *measures* something when the control points over-determine the fit —
> 2 pairs for the similarity and 6 for the degree-2 polynomial make it exactly zero by
> construction, and an imported `.tmap` leaves it NaN. See
> [a suspiciously perfect residual](troubleshooting.md#registration-passed-the-gate-with-a-suspiciously-perfect-residual).

Nothing downstream currently filters on the tag — see
[registration was flagged low-confidence](troubleshooting.md#registration-was-flagged-low-confidence).

### à trous / starlet wavelet, MAD

The undecimated wavelet transform whose multiscale product is Tether's default spot detector
([Olivo-Marin 2002](https://doi.org/10.1016/S0031-3203%2801%2900127-3)); **MAD** (median
absolute deviation) is its per-scale noise estimate. The alternatives are the `intensity` and
`bandpass` modes ported from Deep-LASI's `findPart`
([ADR-0021](adr/0021-particle-detection-modes.md)).

---

## Idealization and kinetics

### idealization

In Tether this means specifically **a fitted HMM persisted as additive data under
`/idealization/{model_name}`** (`tether.project.idealize`), not merely "fitting an HMM". The
fit itself runs in an isolated [sidecar](#sidecar) interpreter driving tMAVEN headlessly
([Verma 2024](https://doi.org/10.1016/j.bpj.2024.01.022)).

### vbFRET / ebFRET / consensus VB-HMM

The three model families reachable through tMAVEN:

- **vbFRET** — variational-Bayes **per-trace** HMM
  ([Bronson 2009](https://doi.org/10.1016/j.bpj.2009.09.031)).
- **ebFRET** — empirical-Bayes **population** HMM
  ([van de Meent 2014](https://doi.org/10.1016/j.bpj.2013.12.055)).
- **consensus VB-HMM** (`vbconhmm`) — one shared model fitted across the population.
  This is `MODEL_TYPE_DEFAULT` in Tether.

> **Tether always persists a consensus model** — one shared state count, one shared
> transition matrix, one `/idealization/{model}` for the whole population, plus each
> molecule's Viterbi path. It does **not** persist independent per-trace HMMs. Two tMAVEN
> plots that read per-trace fits are therefore *re-derivations, not ports*: B3 becomes the
> empirical per-molecule transition frequency, and C1 becomes the count of distinct states
> each Viterbi path actually occupies — a 3-state consensus model still yields "1 state" for
> a trace that never leaves one level. See the
> [parity gallery](analysis/parity-gallery.md#c1-vbfret-state-number-distribution).

### ELBO

Evidence Lower BOund — the variational objective. Tether uses it for **state-count
selection**: `nstates` is either fixed, or auto-selected as the maximum ELBO over
`NSTATES_GRID_DEFAULT = (1, 2, 3, 4)`, recorded as
`nstates_selected_by ∈ {"max-elbo", "fixed"}`
([ADR-0024](adr/0024-idealization-store-layout-staleness-and-nstates.md)).

### state

An index into the model's shared `means` vector — that state's FRET level. Not a label a
human assigns.

### Viterbi path / state path

`state_paths` — an `int64 (n_molecules, n_frames)` array of state indices, the persisted
primitive every group-B/C analysis re-derives from. Tether obtains it by **nearest-mean
assignment** of the float idealized level (`states_from_idealized` in
`tether.idealize.driver`).

### `NO_STATE`

`NO_STATE = -1` (`tether.idealize.driver`) — the sentinel filling a Viterbi path outside the
analysis window, in interior gaps, and wherever the idealized level is non-finite. A
`NO_STATE` frame is **never itself a dwell and never a transition** — but it does **not split
a run either**. `tether.analysis.dwell.state_dwells` *strips* the `NO_STATE` frames before
splitting on state change (tMAVEN's NaN strip), so an interior gap between two frames of the
same state is stitched over: `0,0,NO_STATE,0,0` is **one 4-frame dwell** of state 0, not two
2-frame dwells. On the [TDP](#tdp-transition-density-plot) side the gap becomes `NaN` and the
non-finite pair is dropped, so a `NO_STATE` border never emits a transition point.

### dwell time / dwell-time survival

A **dwell** is a run of constant state in a Viterbi path (`tether.analysis.dwell`). Faithful
to tMAVEN's `generate_dwells`, the **first and last dwell of every molecule are censored**
(the last always; the first unless `include_first`) — standard right-censoring for a finite
observation window.

**Survival** is the empirical `S(τ) = P(dwell > τ)`, fitted with single / double / triple /
stretched exponentials plus covariance standard errors and Student-*t* confidence intervals.

> **Units caveat.** `DEFAULT_DWELL_DT = 1.0`, so fitted rates are **per frame** unless you
> pass the movie's frame time as `dt`.

### TDP (transition density plot)

A 2-D histogram of **initial vs final** idealized FRET level over every state-change frame
of a population ([McKinney 2006](https://doi.org/10.1529/biophysj.106.082487)). Tether's is
the "real" TDP: built from a persisted model's Viterbi paths, with a point emitted **only at
state-change frames**, initial and final read `DEFAULT_TDP_NSKIP = 2` frames apart
(`tether.analysis.tdp`). The stored array is the exact unsmoothed histogram in raw counts;
log-normalization and smoothing are display-only.

### post-synchronization

The A2b heatmap variant: every selected state jump is aligned to a common column so
asynchronous stochastic transitions add coherently, revealing the population's average
approach to and departure from a transition. Relative time zero sits at
`DEFAULT_SYNC_PREFRAME = 50` and the time edges run negative before it
(`tether.analysis.histogram`; see
[A2](analysis/parity-gallery.md#a2-2-d-time-vs-signal-histogram-synchronized-fret-heatmap)).

### staleness (stale / fresh / live)

A Tether-only concept with no tMAVEN analogue, and it gates most analyses. Every molecule in
a persisted model carries a **composite input-provenance hash** folding the windowed input
trace, the analysis-window bounds, and the molecule's **effective applied α and γ**. If any
of those change, the recomputed hash diverges and the molecule is **stale**;
`live_molecule_keys()` is the complement that TDP / dwell / state-number /
transition-probability keep by default
([ADR-0029](adr/0029-idealization-correction-provenance-hash-and-per-factor-staleness.md)).

Consequence: re-estimating **α** re-stales **every molecule in the project**, because the α
estimator is **store-wide** — one median written to every analysable
molecule's `/molecules.alpha`. A γ-median shift re-stales only the molecules that took the
fallback.

### fresh-idealization gating / curation filter

The two Tether-added, on-by-default invariants on every store-level group-B/C entry point:
stale molecules are excluded unless `include_stale=True`, and rejected molecules are excluded
unless `include_rejected=True`. Both are toggles, not deletions.

### precision@k / never-auto-drop

The quality ranker's metric and its contract: the model **shall only re-order — never
auto-drop** (`tether.ml.ranking`). A ranking is a permutation; a molecule that cannot be
scored gets a NaN score (never a fabricated `0`), is ranked **last**, and is kept.

### kinSoftChallenge

A blind community benchmark of single-molecule kinetics analysis tools
([Götz 2022](https://doi.org/10.1038/s41467-022-33023-3)), used as Tether's advisory kinetics
oracle ([ADR-0048](adr/0048-kinsoft-kinetics-oracle.md)).

---

## Plot vocabulary carried over from tMAVEN

### ND-Normalized / ND-Raw

tMAVEN `plot_mode` presets that switch the **signal axis away from FRET**
(`tmaven/tmaven/controllers/analysis_plots/data_hist1d.py`, `normalized_defaults` /
`raw_defaults`): `ND Normalized` labels the axis *Normalized Intensity* over
`[-0.25, 1.25]`, `ND Raw` labels it *Intensity (A.U.)* over `[-500, 10000]`, against
`smFRET`'s *E_FRET* over `[-0.25, 1.25]`.

**Tether does not port them.** Its signal axis is always [apparent E](#apparent-e);
`intensity_quantity` only selects which `/traces` layer (`corrected` vs `raw`) feeds it.
The terms appear on this site only because the
[parity gallery](analysis/parity-gallery.md) enumerates tMAVEN's variants.

---

## Files and formats

### molecule vs trace

A **molecule** is one row in `/molecules` — one donor spot in one movie — carrying two
identities:

- `molecule_id` — a globally stable UUID, inherited unchanged by any split or subset file.
- `molecule_key` — cross-file *content* identity, `sha256(movie_sha256 | quantized donor_xy)`
  at `MOLECULE_KEY_QUANTUM_PX = 0.1` px. It is the join key for split-file merge-back and is
  **not unique** (quantized coordinates can collide) — use `molecule_id` when uniqueness
  matters.

A **trace** is that molecule's row *slice* in the rectangular `/traces/*` arrays, zero-padded
to the experiment-max frame count because one experiment spans movies of differing length;
`frame_range` delimits the valid native extent inside the pad.

### condition

Not free text. A condition is identified by a **chemistry/optics key** — construct/variant,
dye, ligand + concentration, buffer, temperature, laser power (`tether.io.filename`,
`tether.project.conditions`). `date`, replicate and source file deliberately **vary within**
a condition and are not part of its identity. `condition_id` is a content hash of that exact
key, and validation is **referential**: an id is valid only if it resolves to a
`/conditions` row whose stored fields hash back to it.

**Keep-separate by default** — two near-miss filename parses yield different ids and stay
separate; collapsing them requires an explicit confirmed re-key
([ADR-0033](adr/0033-condition-identity-and-rekey.md)). At extraction the id is
*provisional-from-filename* and is retained forever in `condition_id_provisional`.

### `TIRFdata` / MCOS / `#refs#`

Deep-LASI's custom MATLAB class, stored as MATLAB-Class-Object-System (MCOS) objects inside a
v7.3 (HDF5) `.tdat`. Decoding it requires resolving the `#refs#` / `#subsystem#` blobs —
which is what `tether.io.mcos` does. See
[TIRFdata decode](io/legacy-import.md#tirfdata-decode).

### `.tmap` / `.tdat`

Deep-LASI's registration-**map** file (`.tmap`) versus its full-session **project** file
(`.tdat`). The `.tmap` is a classic MATLAB v5 MAT-file whose transform coefficients live in
the MCOS `__function_workspace__` blob, in MATLAB **1-based** pixel coordinates
(`tether.imaging.register.read_tmap`). The `.tdat` is MATLAB v7.3 and carries coordinates,
correction factors and the particle-detection mode.

### SMD

Single-Molecule Dataset — a generalized HDF5 storage format for single-molecule data
([Greenfeld 2015](https://doi.org/10.1186/s12859-014-0429-4)), and tMAVEN's interchange
container. Tether writes a **superset** SMD carrying coordinates
([ADR-0002](adr/0002-smd-superset-round-trip.md)), because a tMAVEN-written SMD has no
per-molecule metadata slot — so after a standalone-tMAVEN round trip the trace↔movie link
survives only by exact intensity-trace matching. See the
[hand-off page](idealize/standalone-tmaven-handoff.md).

### `.tether` project

One HDF5 file per experiment, with root attribute `format = "tether-project"` and a
monotonic `schema_version` (currently `1`). The **entire group skeleton was forward-declared
at M0**, so later milestones add *data*, never *structure*
([ADR-0005](adr/0005-m0-schema-freeze.md)); a `schema-guard` CI gate enforces it.

A file stamped with a **higher** `schema_version` than the running app is refused outright
rather than partially read.

### analysis-only import

A coordinate-less import — traces without a movie. Every molecule is tagged
`round-trip-unavailable`, `/settings/analysis_only` records the banner
`coordinates and patches absent; movie round-trip and spot/overlap views unavailable`, and
the shell leaves the spot/overlap seam unwired
([ADR-0046](adr/0046-analysis-only-smd-import.md)). Idealization, histograms, TDP and
kinetics all still work. See
[Round-trip vs analysis-only](io/legacy-import.md#round-trip-vs-analysis-only).

---

## Running Tether

### sidecar

The isolated conda environment (`numpy<2` + PyQt5 + tMAVEN) in which idealization runs,
because tMAVEN's pins cannot share a process with Tether's PySide6 / current-numpy base
stack ([ADR-0004](adr/0004-pin-and-hold-dual-lock-isolation.md)). Resolution order for the
interpreter is: explicit argument → `TETHER_SIDECAR_PYTHON` → the installer's sibling
`envs/sidecar` derived from `sys.prefix`
([ADR-0051](adr/0051-installed-app-launch-surface.md)). That last fallback exists because a
menu shortcut, `PATH` shim or `.desktop` launch never runs the conda `activate.d` hook that
would have set the variable.

### transient vs deterministic sidecar failure

`SidecarError.transient` (`tether.idealize.driver`) decides whether the supervisor retries:

- **transient** — a crash or a timeout. Auto-restarted, up to `--max-restarts` (default 3).
- **deterministic** — a fit error the sidecar itself reported, or a missing interpreter.
  Never retried; restarting cannot change the outcome.

### stage status (batch)

The vocabulary `tether batch` prints per movie (`tether.project.batch`):

| Status | Meaning | Counts as failure? |
|---|---|---|
| `done` | the stage ran and succeeded | no |
| `skipped` | a checkpoint found the stage already complete | no |
| `failed` | the stage raised (or an over-gate movie under `--policy fail`) | **yes** |
| `blocked` | an upstream stage failed | no |
| `deferred` | the sidecar was unavailable at startup | no |
| `not-requested` | `--no-idealize` | no |
| `warning` | non-fatal, e.g. provenance stamping failed | no |

Only `failed` makes the run exit non-zero — which is why
[a deferred idealization exits 0](troubleshooting.md#the-batch-finished-ok-but-nothing-was-idealized).

`blocked` is the one row that never shows up on an exit-`0` run in practice: a stage is
blocked only when its upstream stage was not `done`/`skipped`, so a `blocked` always travels
with a `failed` in the same movie and the run exits `1` anyway.

### write lock / stale lock

Single-writer ownership is held by a `<project>.tether.lock` JSON sidecar carrying
`host / user / pid / timestamp` plus a per-acquisition nonce (`tether.project.lock`).
Ownership is the full `(host, user, pid)` triple — `user` is included deliberately so a
recycled PID under a different login is never silently granted write access.

Liveness is judged by a **wall-clock staleness timeout of ≈30 min**, not by probing the PID
(a remote PID cannot be probed and cloud sync is eventually consistent). A stale lock is
reclaimable only by an **explicit steal**, never automatically. The `.lock` sidecar is not
part of the schema.

### schema_version

The monotonic on-disk stamp on a `.tether` file's root, currently `1`
(`tether.io.schema`). A file whose stamp is **higher** than the running app's is refused —
the app upgrades, the file is never downgraded.

---

## Related pages

- [Troubleshooting](troubleshooting.md) — symptom-keyed failure modes, including the silent ones.
- [Seven-plot parity gallery](analysis/parity-gallery.md) — where the plot vocabulary is asserted against tMAVEN.
- [Legacy Deep-LASI import](io/legacy-import.md) — `.tdat` / `.tmap` / `.mat` formats and the correction-factor remap.
- [Standalone-tMAVEN hand-off](idealize/standalone-tmaven-handoff.md) — the sidecar and the SMD round trip.
- [Deep trace classifier](ml/deep-classifier.md) — the optional GPU add-on.
- [Architecture decisions](adr/README.md) — the rationale behind most entries above.
