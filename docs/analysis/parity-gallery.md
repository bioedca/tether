# Seven-plot parity gallery

Tether reproduces **exactly seven tMAVEN plot types** natively (PRD Appendix C; the
*bounded plot parity* non-goal, N3). Any other tMAVEN plot stays reachable through the
standalone-tMAVEN hand-off — it is **not** reimplemented. This gallery is the single
cross-reference that ties each native plot to its tMAVEN counterpart, its Tether
implementation, and the committed test that establishes the correspondence.

The seven types are enumerated from tMAVEN source at its pinned commit: six
`controller_base_analysisplot` subclasses registered in
`tmaven/tmaven/controllers/analysis_plots/analysisplots.py`, plus the per-trace viewer in
`tmaven/tmaven/trace_plot/`. The smFRET / ND-Normalized / ND-Raw signal modes and
post-synchronization are *variants* of a type, not separate types. Groups **B** and **C**
are idealization-gated, as is A2's post-synchronized variant (**A2b**, which needs a model's
Viterbi paths); **A1**, **raw A2**, and **D1** render without a model.

> **Where parity actually lives.** The **numeric and visual parity is asserted by each
> plot's own committed tests** (the "Parity evidence" rows below), which the 3-OS CI `test`
> matrix runs headlessly. Several of those tests embed the tMAVEN reference routine
> *verbatim* as an in-test oracle and assert array-equality against it. This page
> **consolidates** those correspondences into one reference; it does not replace the tests.
> Where Tether persists a single shared consensus model instead of tMAVEN's independent
> per-trace fits, the plot is re-derived from the stored Viterbi paths rather than ported
> line-for-line — those cases are called out per plot as *re-derivations*, not *ports*.

## At a glance

| # | Plot | tMAVEN source (`tmaven/tmaven/…`) | Tether implementation | Parity evidence |
|---|------|-----------------------------------|-----------------------|-----------------|
| **A1** | 1-D population histogram (+ model Gaussian/GMM overlay) | `controllers/analysis_plots/data_hist1d.py` | `tether.analysis.histogram` | `tests/test_analysis_histogram.py`, `tests/test_analysis_histogram_overlay.py` |
| **A2** | 2-D time-vs-signal histogram (raw + post-sync heatmap) | `controllers/analysis_plots/data_hist2d.py` | `tether.analysis.histogram` | `tests/test_analysis_histogram2d.py`, `tests/test_analysis_histogram_postsync.py` |
| **B1** | Transition density plot (TDP) | `controllers/analysis_plots/data_tdp.py` | `tether.analysis.tdp` | `tests/test_analysis_tdp.py` |
| **B2** | Survival / dwell-time distribution (+ fits, residuals) | `controllers/analysis_plots/survival_dwell.py` | `tether.analysis.dwell` | `tests/test_analysis_dwell.py` |
| **B3** | Transition-probability histogram (+ KDE) | `controllers/analysis_plots/tm_hist.py` | `tether.analysis.transition_prob` | `tests/test_analysis_transition_prob.py` |
| **C1** | vbFRET state-number distribution | `controllers/analysis_plots/model_vbstates.py` | `tether.analysis.state_number` | `tests/test_analysis_state_number.py` |
| **D1** | Per-trace viewer (Tether curation trace dock) | `trace_plot/multi_plot.py` | `tether.gui.trace_dock` | `tests/test_trace_dock.py` |

Every native analysis carries provenance and parameters into the `.tether` store
(NFR-REPRO), and every plot exports as vector PDF/SVG **and** PNG via
`tether.analysis.plot_export` (FR-EXPORT), stamped with those parameters.

Two invariants recur on the group-B/C **store-level** entry points and have **no tMAVEN
analogue** — they are Tether additions, toggleable but on by default:

- **Fresh-idealization gating** — molecules whose idealization is *stale* relative to the
  current trace are excluded (PRD §5.1) unless `include_stale=True`.
- **Curation filter** — rejected molecules are excluded (PRD §7.5) unless
  `include_rejected=True`.

---

## A1 — 1-D population histogram

**What it is.** The population histogram of per-frame apparent FRET efficiency (the
proximity ratio `A/(D+A)`) pooled over each selected molecule's analysis window and
density-normalized in 151 bins over `[-0.25, 1.25]`, optionally overlaid with the idealized
state model drawn as a sum of per-state Gaussians (dashed components + solid combined
mixture) and annotated with the molecule count *N* ([McCann 2010](#mccann2010);
[Verma 2024](#verma2024)).

**tMAVEN counterpart.** `controller_data_hist1d` — `ax.hist(bins=signal_nbins,
range=(signal_min, signal_max), density=True, log=hist_log)` with `signal_nbins=151`,
`signal_min=-0.25`, `signal_max=1.25`; the `model_on` overlay loop
`yi = frac[i]·(1/√(2π·var[i]))·exp(-½·(x-mean[i])²/var[i])` on `linspace(min, max, 1001)`;
and `garnish` for the `N = %d` annotation.

**Tether reimplementation** — `tether.analysis.histogram`:

- `apparent_e_histogram(values, *, bins=DEFAULT_NBINS, value_range=DEFAULT_RANGE, density=True, weights=None) -> Histogram1D` — the pure-array core. `DEFAULT_NBINS = 151` and `DEFAULT_RANGE = (-0.25, 1.25)` are named constants copied from `data_hist1d.py`. Density over a **fixed** range keeps uncorrected proximity-ratio excursions beyond `[0, 1]` visible; non-finite (`D+A == 0`) samples are dropped, never fabricated or clipped.
- `population_apparent_e_histogram(project, *, molecule_keys=None, intensity_quantity="corrected", …) -> Histogram1D` — the `.tether`-store entry point: pool each accepted molecule's windowed apparent E and bin it. This is the headless source of truth the GUI renders (PRD §9 M2 "reproduce the MVP histogram from the API").
- `model_gaussian_overlay(means, variances, frac, *, value_range=DEFAULT_RANGE, n_points=DEFAULT_OVERLAY_POINTS) -> ModelGaussianOverlay` — a **verbatim port** of the tMAVEN `model_on` loop (`DEFAULT_OVERLAY_POINTS = 1001`): dashed per-state components + a solid summed mixture, drawn from the *idealized model's own* persisted emissions, **not** a fresh GMM re-fit of the pooled E. `population_model_gaussian_overlay(project, model_name, …)` reads a persisted `/idealization` model; a threshold/k-means model that carries no per-state spread **raises** rather than fabricating an overlay.

**Tether extensions beyond tMAVEN.** Molecule-level (BOBA-FRET) bootstrap confidence band
on the same pooled histogram (`population_apparent_e_histogram_ci`, PRD §7.7,
[König 2013](#konig2013)); a per-condition shared-axis overlay
(`per_condition_apparent_e_histograms`, the M6 FR-ANALYZE §7.7 clause); and a per-molecule
equal-weight toggle over tMAVEN's frame-weighted default.

**Variants.** tMAVEN's three signal modes (`E_FRET` vs ND-Normalized vs ND-Raw intensity,
each with distinct `signal_min/max`); linear vs `log10` y-axis; density vs raw counts.

```python
from tether.analysis.histogram import (
    population_apparent_e_histogram,
    population_model_gaussian_overlay,
)

hist = population_apparent_e_histogram(project)          # 151 bins over [-0.25, 1.25]
overlay = population_model_gaussian_overlay(project, "vbconhmm")   # dashed states + solid total
```

**Parity evidence.**

- `tests/test_analysis_histogram.py` — `test_default_binning_matches_tmaven_a1` asserts `nbins == 151`, 152 edges, range `(-0.25, 1.25)`, density integrates to 1; `test_population_reproduces_mvp_histogram` checks the store-level result equals an independent `np.histogram(…, bins=151, range=(-0.25, 1.25), density=True)` oracle over the pooled windowed E.
- `tests/test_analysis_histogram_overlay.py` — `test_overlay_matches_tmaven_formula_exactly` reproduces the `data_hist1d.py` `model_on` per-state Gaussian loop **bit-for-bit** (the file's `_tmaven_reference()` is the tMAVEN loop copied verbatim as the oracle); `test_overlay_component_is_scaled_gaussian` cross-checks each component against `frac[i] · scipy.stats.norm.pdf`; `test_population_overlay_reads_persisted_model` reads a persisted model, and `test_model_without_population_members_is_withheld` / `test_model_missing_only_frac_is_withheld` lock the withhold-not-fabricate behavior.

---

## A2 — 2-D time-vs-signal histogram (synchronized FRET heatmap)

**What it is.** A 2-D occupancy heatmap of per-frame apparent FRET efficiency (*y*) versus
time (*x*, colour = frame density) showing how a population's FRET distribution evolves over
the analysis window — either **raw / start-synchronized** or **transition-synchronized**
(post-sync, "A2b"), where every selected state jump is aligned to a common column so
asynchronous stochastic transitions add coherently and reveal the population's average
approach-to and departure-from a transition ([Verma 2024](#verma2024); [McCann 2010](#mccann2010)).

**tMAVEN counterpart.** `controller_data_hist2d` — `get_data`, `interpolate_histogram`,
`histogram_raw`, `sync_start`, `histogram_sync_list`, `gen_sync_list_single`,
`gen_sync_list_fixed`, `gen_jumplist`.

**Tether reimplementation** — `tether.analysis.histogram` (two pure cores + two store
wrappers):

- **Raw / start-sync.** `time_signal_histogram2d(signal_chunks, *, time_bins=DEFAULT_TIME_BINS, signal_bins=DEFAULT_SIGNAL_BINS, signal_range=DEFAULT_SIGNAL_RANGE, time_dt=DEFAULT_TIME_DT, …) -> Histogram2D`, and its store entry `population_time_signal_histogram2d(project, …)`. Faithful to `histogram_raw` after `sync_start`: the frame index drives the time column, and NaN / out-of-range / beyond-`time_bins` frames **drop without shifting** later frames.
- **Post-sync / A2b.** `transition_sync_histogram2d(trace_pairs, *, from_state=-1, to_state=-1, single_dwell=True, sync_preframe=DEFAULT_SYNC_PREFRAME, …) -> TransitionSyncHistogram2D`, and its store entry `population_transition_sync_histogram2d(project, model_name, …)`. Faithful to `gen_sync_list_single` / `gen_sync_list_fixed` + `histogram_sync_list`, adapted from tMAVEN's rectangular NaN-padded arrays to Tether's ragged per-molecule `(state_path, signal)` pairs. The heatmap has `time_bins + 1` columns with the jump at column `sync_preframe` (relative-time zero); `time_edges` run negative before the transition.

Constants ported verbatim: `DEFAULT_TIME_BINS = 100`, `DEFAULT_SIGNAL_BINS = 61` over
`[-0.2, 1.2]`, `DEFAULT_TIME_DT = 1`, `DEFAULT_SYNC_PREFRAME = 50`. Note A2's **61** signal
bins deliberately differ from A1's 151.

**Deliberate departure.** Tether's *y*-axis is always apparent E (unclipped); tMAVEN's
`plot_mode` raw/normalized-intensity *y*-axis presets are not ported —
`intensity_quantity` only selects which `/traces` layer (`"corrected"` vs `"raw"`) feeds
apparent E. Out-of-trace frame indices are dropped, never wrapped.

**Variants.** Raw/start-sync vs post-sync; within post-sync, `single_dwell=True`
(single dwell before/after) vs `False` (fixed ± window), and `from_state`/`to_state`
transition selection (`-1` = any).

```python
from tether.analysis.histogram import (
    population_time_signal_histogram2d,
    population_transition_sync_histogram2d,
)

raw = population_time_signal_histogram2d(project)                       # 100 × 61 over [-0.2, 1.2]
sync = population_transition_sync_histogram2d(project, "vbconhmm",      # any → any transition
                                              from_state=-1, to_state=-1)
```

**Parity evidence.**

- `tests/test_analysis_histogram_postsync.py` — the definitive A2b parity test embeds `_tmaven_postsync` (`gen_sync_list_single` / `gen_sync_list_fixed` + `histogram_sync_list` ported verbatim) and asserts `np.testing.assert_array_equal(h.counts, ref)` plus `n_molecules == nmol` and `n_transitions == npoints` across `single_dwell ∈ {True, False}` and five `(from, to)` state pairs; also checks `xbins+1` columns with the transition at `sync_preframe`, right-open signal interval, out-of-range-still-counts, `NO_STATE` borders are not jumps, and density integrates to 1.
- `tests/test_analysis_histogram2d.py` — A2 raw-mode faithfulness to `histogram_raw`-after-`sync_start`; `test_population_defaults_match_tmaven_a2` pins the 100×61 grid over `[-0.2, 1.2]` with `time_dt=1`, and `test_population_start_synchronization_via_analysis_window` confirms per-molecule start-synchronization.

---

## B1 — Transition density plot (TDP)

**What it is.** A 2-D histogram of **initial vs final** idealized FRET efficiency over every
state-change frame of a molecule population, showing which conformational transitions the
system makes and how frequently — off-diagonal mass is transitions, the diagonal is no net
change ([McKinney 2006](#mckinney2006)).

**tMAVEN counterpart.** `controller_data_tdp` — `get_neighbor_data`, `gen_histogram`,
`plot`.

**Tether reimplementation** — `tether.analysis.tdp`:

- `transition_density(idealized_chunks, *, nskip=DEFAULT_TDP_NSKIP, signal_bins=DEFAULT_TDP_SIGNAL_BINS, signal_range=DEFAULT_TDP_SIGNAL_RANGE, density=False) -> TransitionDensityPlot` — a **verbatim port** of `get_neighbor_data`'s idealized-levels sub-branch (`hist_rawsignal=False`) followed by `gen_histogram`'s `histogram2d` step: per-molecule neighbour pairs `d1 = v[:-nskip]`, `d2 = v[nskip:]`, restricted to state-change frames (`|v[t+1]-v[t]| > 0`), finite-only, then `np.histogram2d` over a square E×E grid. Tether feeds the idealized levels `v` (`levels = means[state]`), so it reproduces the *idealized-vs-idealized* TDP; note tMAVEN's shipped default is `hist_rawsignal=True`, which instead plots the **raw** signal at the transition frames located from `v`.
- `population_transition_density(project, model_name, *, nskip=2, signal_bins=101, signal_range=(-0.25, 1.25), …) -> TransitionDensityPlot` — the store entry: reconstructs each accepted **and fresh** molecule's idealized-level trace from `/idealization/{model_name}` Viterbi paths (`levels = means[state]`).

Rendering constants reproduced exactly as module defaults: `DEFAULT_TDP_NSKIP = 2`,
`DEFAULT_TDP_SIGNAL_BINS = 101`, `DEFAULT_TDP_SIGNAL_RANGE = (-0.25, 1.25)`. The module
stores the **raw, unsmoothed** histogram — tMAVEN's `gaussian_filter` smoothing and
`LogNorm` log-normalization are display-only, applied at render time.

**Variants.** `density=True` (guarded against divide-by-zero all-NaN when no in-range mass)
vs raw counts; `include_stale` / `include_rejected` on the store path.

```python
from tether.analysis.tdp import population_transition_density

tdp = population_transition_density(project, "vbconhmm")   # fresh idealizations only, nskip=2
```

**Parity evidence.** `tests/test_analysis_tdp.py` — `test_parity_with_tmaven_oracle`
(parametrized `nskip = 1, 2, 3`) asserts `transition_density(...).counts` is
`assert_array_equal` to the in-test **verbatim tMAVEN oracle** `_tmaven_tdp`
(`get_neighbor_data` + `gen_histogram`); `test_population_matches_tmaven_oracle_via_store`
runs the full store path and asserts the same; `test_defaults_match_tmaven` pins the three
ported constants; plus hand-checked semantics (off-diagonal landing, only-state-change
frames contribute, histogram2d edge/range behaviour, NaN gaps are not transitions,
ragged↔padded equivalence).

---

## B2 — Survival / dwell-time distribution

**What it is.** For each idealized FRET state, the dwell-time survival function
`S(τ) = P(dwell > τ)` — the fraction of visits to that state lasting longer than τ — whose
exponential decay constants *k* are the state's exit (transition) rates, the kinetic
fingerprint of the Markov model. Dwell-time distributions routinely require single- or
multi-exponential (and stretched) forms ([Lee 2012](#lee2012)), and survival analysis yields
the exponentially-distributed lifetimes directly ([Schrangl 2024](#schrangl2024)).

**tMAVEN counterpart.** `controller_survival_dwell` (the matplotlib plotter — survival
markers OR density histogram, model overlay, residual subplot via `make_axes_locatable`,
`N`/`A`/`k`/`β` textbox); `generate_dwells`, `calculate_dwells`, `survival` in
`modeler/dwells.py`; `optimize_{single,double,triple,stretch}_surv`; and the survival forms
`{single,double,triple,stretched}_exp_surv` in `modeler/fxns/exponentials.py`.

**Tether reimplementation** — `tether.analysis.dwell` (the three-stage pipeline ported as
headless array code):

- `state_dwells(state_chunks, *, no_state=None, include_first=False) -> StateDwells` — reimplements `generate_dwells` (`np.split` on `np.diff != 0`; drop the last run always as right-censored, and the first unless `include_first`).
- `survival_curve(dwell_lengths) -> (tau, survival)` — reimplements `survival()` with the same `S(0)=1` normalization and identical degenerate empty return.
- `fit_survival(tau, survival, *, model="single", ci_level=…) -> DwellFit` — reimplements `optimize_*_surv`'s fit: the same `scipy.optimize.curve_fit` (`trf`, bounded), `sqrt(diag(pcov))` standard errors, and R². On top of the ported SE it adds two-sided Student-*t* CIs and a residual array (tMAVEN forms residuals later, in the plotter, not in `optimize_*_surv`). It **grace-fails** (`success=False`, NaN params) instead of raising.
- `single_exp_survival` / `double_exp_survival` / `triple_exp_survival` / `stretched_exp_survival` — **verbatim copies** of `exponentials.py`.

Constants carried over verbatim: `DEFAULT_DWELL_NBINS = 51`, `DEFAULT_DWELL_DT = 1.0`,
`DWELL_MODEL_NPARAMS = {single: 2, double: 4, triple: 6, stretched: 3}`.

**Variants.** Empirical survival curve (default) OR density-normalized dwell-time histogram,
each with a residuals subplot; single/double/triple/stretched fits (tMAVEN also overlays a
transition-matrix-derived decay in the plotter); first-dwell censoring toggle
(`include_first`); the last dwell is always censored.

```python
from tether.analysis.dwell import population_dwell_times

fits = population_dwell_times(project, "vbconhmm", model="double")   # per-state DwellTimeAnalysis
```

**Parity evidence.** `tests/test_analysis_dwell.py` — `test_state_dwells_matches_tmaven_oracle`
asserts `state_dwells` equals a verbatim port of `generate_dwells` (`_tmaven_dwells`);
`test_survival_matches_tmaven_oracle` asserts `survival_curve` equals a verbatim port of
`survival()` (`_tmaven_survival`), with the exact empty-case degenerate return locked;
`test_fit_recovers_known_rate` / `_double_sorted_by_rate` / `_stretched_reports_beta`
recover known synthetic `k`, `A`, `β`; `test_fit_ci_and_residuals` locks
`CI == Student-t · SE` and `residuals == survival − model_survival`; store-level tests tie
`population_dwell_times` to the pure core and lock the fresh + §7.5 gating.

---

## B3 — Transition-probability histogram

**What it is.** A 1-D histogram (with an optional Gaussian-KDE overlay) of per-molecule HMM
one-step transition probabilities `P(init → fin)` for a chosen ordered state pair, pooled
across a population — exposing how the transition rate for that pair is **distributed across
molecules** rather than as a single consensus number ([McKinney 2006](#mckinney2006);
[van de Meent 2014](#vandemeent2014)).

**tMAVEN counterpart.** `controller_tm_hist` — `get_composite_tm`, `plot`, `garnish`;
reads each trace-level VB model's own `norm_tmatrix[init_vb, fin_vb]` (row/col matched to the
composite model by Gaussian nearest-mean) and overlays `scipy.stats.gaussian_kde`.

**Tether reimplementation** — `tether.analysis.transition_prob`. This is a **re-derivation,
not a verbatim port**: Tether persists a single shared consensus `norm_tmatrix`, not
tMAVEN's independent per-trace fits, so B3 becomes the empirical analogue (mirroring how B1's
real TDP rebuilds from Viterbi paths):

- `empirical_transition_probability(state_path, init_state, final_state) -> float | None` — the per-molecule maximum-likelihood one-step `P(init → fin)` from a Viterbi path: numerator = frames in `init_state` whose successor is `final_state`; denominator = frames in `init_state` with an *observed* successor (gap successors `NO_STATE` excluded). Returns `None` when `init_state` is never occupied with an observed successor — the analogue of tMAVEN's `isfinite` filter, never `0/0`.
- `transition_prob_histogram(state_chunks, *, init_state, final_state, prob_bins=DEFAULT_TPROB_NBINS, prob_range=DEFAULT_TPROB_RANGE, density=True, kde=True, …) -> TransitionProbHistogram` — pools defined per-molecule probabilities, histograms them, and attaches a Gaussian-KDE curve when ≥ 2 probabilities are available (the KDE `try/except` returns `None` on singular covariance rather than crashing).
- `population_transition_prob_histogram(project, model_name, init_state, final_state, …)` — the store entry, validating the pair against the model's `nstates` and applying the shared fresh + curation contract.

Rendering constants ported verbatim: `DEFAULT_TPROB_NBINS = 25`,
`DEFAULT_TPROB_RANGE = (-0.05, 1.05)`, `DEFAULT_TPROB_KDE_BANDWIDTH = 0.25`,
`DEFAULT_TPROB_KDE_POINTS = 100`.

**Variants.** Density vs raw counts; KDE overlay on/off (also absent when < 2 or all-identical
probabilities); any ordered state pair including self-pairs (the diagonal).

```python
from tether.analysis.transition_prob import population_transition_prob_histogram

tph = population_transition_prob_histogram(project, "vbconhmm", init_state=0, final_state=1)
```

**Parity evidence.** `tests/test_analysis_transition_prob.py` — `test_defaults_match_tmaven`
pins the four ported constants; hand-checked `empirical_transition_probability` cases
(`P(0→1) = 1/3`, self-pair `2/3`, gap-successor exclusion, undefined → `None`); density
integrates to 1 and empty/out-of-range paths stay all-zeros, never NaN; KDE present with
≥ 2 distinct probabilities and `None` on identical values; the store path enforces fresh-only
+ rejected-exclusion + `molecule_keys ∩ fresh` and reproduces the pure-core counts.
`tests/test_plot_export.py::test_render_transition_prob` exercises the render + vector/PNG
export round-trip.

---

## C1 — vbFRET state-number distribution

**What it is.** Over the curated population, how many **distinct** conformational (FRET)
states each molecule occupies — a bar chart with *x* = number of states and *y* = number of
trajectories — answering how heterogeneous the population is in state count
([Bronson 2009](#bronson2009), the vbFRET variational-Bayes model-selection method;
[van de Meent 2014](#vandemeent2014)).

**tMAVEN counterpart.** `controller_model_vbstates` — `plot` histograms an *independent
per-trace* vbFRET fit: `nstates = [r.mu.size for r in maven.modeler.model.hmms]`, then
`y = [sum(nstates == i) for i in arange(states_low=1, states_high=10)]`, gated on a `vb`
model type.

**Tether reimplementation** — `tether.analysis.state_number`. A **re-derivation**: Tether
fits one consensus/global model and stores each molecule's Viterbi path, so C1 counts the
distinct states a path actually visits:

- `occupied_state_count(state_path) -> int` — the number of distinct non-`NO_STATE` states one molecule's Viterbi path visits (0 for an all-gap path) — the per-molecule analogue of tMAVEN's `hmm.mu.size`.
- `state_number_counts(state_chunks, *, states_low=DEFAULT_STATE_NUMBER_LOW, states_high=None) -> StateNumberCounts` — histograms per-molecule distinct-state counts into bars `[states_low, states_high]`; drops all-gap (zero-state) molecules (the analogue of tMAVEN counting only traces that carry a model).
- `population_state_number(project, model_name, …) -> StateNumberCounts` — the store entry, with honest `n_out_of_range` accounting so a clipped axis never silently hides molecules.

tMAVEN's `states_low = 1` survives as `DEFAULT_STATE_NUMBER_LOW = 1`; its fixed
`states_high = 10` becomes an auto-derived top bar (`states_high=None`).

```python
from tether.analysis.state_number import population_state_number

counts = population_state_number(project, "vbconhmm")   # bars over occupied-state counts
```

**Parity evidence.** `tests/test_analysis_state_number.py` — the pure core matches the
tMAVEN counting semantics (distinct non-`NO_STATE` states; revisits don't double-count;
all-gap → 0), and `state_number_counts` histograms per-molecule distinct-state counts,
dropping zero-state molecules; `test_population_matches_core` reproduces the pure core over a
seeded store; the Tether-specific fresh/curation selection, honest out-of-range accounting,
and misuse guards are locked.

---

## D1 — Per-trace viewer (Tether curation trace dock)

**What it is.** One molecule's donor/acceptor (and total) intensity plus its per-frame FRET
efficiency time-series, with right-column marginal probability histograms and the
idealized/Viterbi state path overlaid on the FRET panel — Tether's per-trace **curation
surface** (FR-ROUNDTRIP). At the MVP the FRET axis reads *apparent E* (the uncorrected
proximity ratio `A/(D+A)`), switching to corrected E at M3
([McCann 2010](#mccann2010); [Hellenkamp 2018](#hellenkamp2018)).

> **D1 closes the §9 M6 "seven plots" clause across M2 + M6.** Unlike A1–C1, D1 is not a
> population aggregate and has no dedicated numeric tMAVEN oracle — it *is* the curation
> trace dock built at M2 S1, whose tests assert axis/label **conventions** rather than
> tMAVEN visual parity. Its correspondence to tMAVEN's per-trace viewer is therefore
> **structural and conventional** — the shared 2×2 layout, the donor/acceptor/FRET channel
> and colour conventions, and the idealization-path overlay — **not** pixel-identical: the
> **Deliberate MVP divergences** listed below (apparent-vs-corrected E, marginal binning,
> the alpha-graded photobleach segments, the signal-mode switch) are tracked and converge in
> later milestones (photobleach segmentation at M3). Recording that convention-level
> correspondence **here**, alongside A1/A2/B1/B2/B3/C1, is what closes the acceptance clause
> for the seventh plot type across the two milestones — in the sense the plan designates for
> D1, whose parity is conventional by construction rather than a numeric-oracle match.

**tMAVEN counterpart.** `multi_canvas` (a 2×2 matplotlib grid, `width_ratios=[6,1]`);
`calc_trajectory`, `calc_histograms`, `calc_model_traj` / `draw_model`, `draw_traj` (three
segments per channel split at pre-truncation + photobleach), `set_linestyles` (alpha-graded
`[0.25, 0.9, 0.25]`), `draw_hist` / `draw_fret_hist`; donor-green / acceptor-red / FRET-blue
colour convention.

**Tether reimplementation** — `tether.gui.trace_dock` (a **reimplementation**, not a port:
matplotlib/PyQt5 → pyqtgraph):

- `TraceView(donor, acceptor, frame_time=None, name=None, molecule_key=None)` — a Qt-free immutable value object that derives `.total` (`D+A`), `.apparent_e` (`A/(D+A)` via `tether.fret.efficiency.apparent_fret`, NaN where `total == 0`), and `.time_axis(mode)` (frames or seconds).
- `TraceDock(*, seconds_by_default=True)` — the pyqtgraph 2×2 viewer widget: intensity time-series (top-left) over apparent-E FRET (bottom-left, x-linked), each with its marginal histogram in the y-linked right column, plus the idealization step overlay; driven by `set_trace` / `set_idealization` / `set_time_mode` / `clear`.
- `set_idealization(idealized)` draws the per-frame Viterbi level as a `stepMode="center"` overlay (the analogue of tMAVEN `draw_model`), reset to `None` on every `set_trace` so a stale path never bleeds to the next molecule.
- `trace_from_smd(raw, index, …) -> TraceView` — the store/hand-off → dock bridge.

**Deliberate MVP divergences.** The FRET panel plots apparent E instead of tMAVEN's
`calc_relative`; marginals use a fixed 40-bin count instead of `int(√(pbtime−pretime))`;
tMAVEN's three alpha-graded pre/window/post segments and its Relative/Normalized/ND-Raw mode
switch are **not yet** in the M2 dock (photobleach segmentation lands at M3). Tether adds a
total-intensity curve and a seconds/frames x-axis toggle.

**Parity evidence.** `tests/test_trace_dock.py` — the D1 display **contract** that mirrors
`multi_plot` conventions (not a numeric oracle): 2×2 grid with intensity + FRET time-series
left and marginals right; donor green, acceptor red, FRET blue, total neutral grey; the FRET
axis labelled "apparent E" and pinned to 0–1; the FRET curve `== A/(D+A)` on finite frames
with a `connect="finite"` gap at zero-total frames; the idealization overlay is
`stepMode="center"`, drawn only over the finite window, re-laid on the seconds/frames toggle,
hidden before `set_idealization`, and cleared when the trace changes.
`tests/test_fret_efficiency.py` locks the FRET-panel value function
(`apparent_fret == corrected_fret` at `α=0, γ=1`).

---

## References

Verified via [Consensus](https://consensus.app) during authoring:

- <a id="mckinney2006"></a>**[McKinney 2006]** McKinney, Joo & Ha. [*Analysis of single-molecule FRET trajectories using hidden Markov modeling.*](https://consensus.app/papers/details/3b073d160e935835b598eb6ac72bda7b/?utm_source=claude_code) Biophysical Journal. The paper that introduced the transition density plot (B1) alongside HMM idealization and BIC state counting.
- <a id="bronson2009"></a>**[Bronson 2009]** Bronson, Fei, Hofman, Gonzalez & Wiggins. [*Learning rates and states from biophysical time series: a Bayesian approach to model selection and single-molecule FRET data.*](https://consensus.app/papers/details/677d6975832a53f79a756d5511808c50/?utm_source=claude_code) Biophysical Journal. vbFRET — variational-Bayes model selection for the number of conformational states (C1).
- <a id="vandemeent2014"></a>**[van de Meent 2014]** van de Meent, Bronson, Wiggins & Gonzalez. [*Empirical Bayes methods enable advanced population-level analyses of single-molecule FRET experiments.*](https://consensus.app/papers/details/94863e6d77fa5458a6c4d0f9b231781f/?utm_source=claude_code) Biophysical Journal. ebFRET — the empirical-Bayes consensus population model underlying the shared transition matrix (B3, C1).
- <a id="lee2012"></a>**[Lee 2012]** Lee et al. [*Kinetics of the triplex-duplex transition in DNA.*](https://consensus.app/papers/details/a63aced3819b56c9a1a94b7a537deee5/?utm_source=claude_code) Biophysical Journal. smFRET dwell-time distributions fit with single- and double-exponential functions (B2).
- <a id="schrangl2024"></a>**[Schrangl 2024]** Schrangl et al. [*Advanced Quantification of Receptor–Ligand Interaction Lifetimes via Single-Molecule FRET Microscopy.*](https://consensus.app/papers/details/8e9bd690a9c85179b36f4936cd631a13/?utm_source=claude_code) Biomolecules. Survival analysis for exponentially-distributed interaction lifetimes (B2).

- <a id="mccann2010"></a>**[McCann 2010]** McCann JJ, Choi UB, Zheng L, Weninger K, Bowen ME. [*Optimizing methods to recover absolute FRET efficiency from immobilized single molecules.*](https://doi.org/10.1016/j.bpj.2010.04.063) *Biophysical Journal* 99(3):961–970 (2010). The accurate-FRET reference for the apparent-FRET / proximity-ratio distinction (A1, D1) and for the population-median γ aggregation window.
- <a id="verma2024"></a>**[Verma 2024]** Verma AR, Ray KK, Bodick M, Kinz-Thompson CD, Gonzalez RL Jr. [*Increasing the accuracy of single-molecule data analysis using tMAVEN.*](https://doi.org/10.1016/j.bpj.2024.01.022) *Biophysical Journal* 123(17):2765–2780 (2024). tMAVEN — the embedded idealization sidecar whose plot definitions this gallery is asserted against.
- <a id="konig2013"></a>**[König 2013]** König SLB, Hadzic MCAS, Fiorini E, Börner R, Kowerko D, Blanckenhorn WU, Sigel RKO. [*BOBA FRET: bootstrap-based analysis of single-molecule FRET data.*](https://doi.org/10.1371/journal.pone.0084157) *PLoS ONE* 8(12):e84157 (2013). BOBA-FRET — the molecule-level bootstrap confidence band on the pooled histogram (A1, PRD §7.7).
- <a id="hellenkamp2018"></a>**[Hellenkamp 2018]** Hellenkamp B, Schmid S, Doroshenko O, et al. [*Precision and accuracy of single-molecule FRET measurements — a multi-laboratory benchmark study.*](https://doi.org/10.1038/s41592-018-0085-0) *Nature Methods* 15(9):669–676 (2018). The multi-laboratory accurate-FRET benchmark behind the α/δ/γ correction convention (D1, M3).
