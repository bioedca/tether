<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0110 — Idealization parity: per-trace vbFRET, the transition-matrix term, the re-freeze on the pinned build, and a lock on the frozen bounds

- **Status:** accepted
- **Date:** 2026-11-15
- **Deciders:** bioedca (the acceptance criteria of #545, approved 2026-11-15; item 1 option (a),
  2026-11-14; "Seeded check, no frozen row" for item 1 after the measurement below, 2026-11-15;
  "Split into three PRs", 2026-11-15)
- **PRD anchor:** §7.4, §9.2 (the idealization-parity gate), §9.6, §11.2, §8 NFR-VALID (b)
- **Milestone:** M18
- **Issue:** [#545](https://github.com/bioedca/tether/issues/545)
- **Builds on:** [ADR-0009](0009-parity-metrics-and-freeze.md) (the metrics and the freeze rule),
  [ADR-0043](0043-per-method-parity-tolerance.md) (per-method rows),
  [ADR-0075](0075-seeded-ebfret-parity-gate.md) (seeded fits),
  [ADR-0090](0090-measured-vbfret-paths-are-a-dwell-reference.md) (no per-trace vbFRET model type
  in the pinned tMAVEN)

## Context and problem statement

PRD §9.6 carried four idealization-parity criteria that the code did not meet:

1. **Per-trace vbFRET (M2).** The frozen default row was measured on consensus `vbconhmm` and is
   `applied_to` per-trace vbFRET, but no test fitted per-trace vbFRET. The pinned tMAVEN
   `10f4230b` has no modeler method for it: its stand-alone `run_vbhmm_modelselection` is
   commented out in that build. Its per-trace step, `tracelevel_vbhmm_modelselection`, is the
   first stage of its "vb GMM + vb HMM" and "kmeans + vb HMM" models.
2. **Transition matrix (M6).** The M6 bar names state levels, transition matrix and ELBO for
   consensus VB-HMM and ebFRET on the ≥ 50-molecule SMD. `parity.py` had no transition-matrix
   term.
3. **The default row on the pinned build (M0.5).** `$.tolerance` was measured on tMAVEN
   `71cfa1af` under CPython 3.9.23, not on the pinned `10f4230b` under CPython 3.12.13 that the
   sidecar, CI and the installers run.
4. **A loosened bound.** The artifact self-checks assert *recorded value within bound*, so a bound
   widened to any other finite value failed nothing.

## Decision drivers

- **No frozen tolerance becomes wider.** A re-measure may keep a bound or tighten it.
- **A frozen row must mean what it measured.** A row whose floors are 0 and whose ceiling is
  most of the FRET range catches nothing, and freezing it would look like evidence while being
  none (the reasoning of ADR-0043, which refused to loosen the shared row for ebFRET).
- **The four-bound rows keep their shape.** Every consumer of `load_frozen_tolerance`
  (`sidecar / parity`, the RF2-ArfA check of ADR-0079, the docs guard) reads four bounds.
- **A re-freeze is a visible, reviewed edit**, never a one-number change in one file.

## Considered options

The record names the other options for each item it decides.

- Item 1, per-trace vbFRET, after the measurement in its section below:
  - **A seeded check and no frozen row** (chosen, 2026-11-15).
  - **Freeze a row from the measured spread.** Set aside: the ADR-0009 rule applied to it freezes
    floors of 0.0 and an unbounded ΔE ceiling, a row that catches nothing.
  - **Best-of-N restarts per state count in Tether, then freeze.** Set aside here: it is a new
    fitting procedure, not tMAVEN's per-trace step, so it is new scope (carried by #949).
- Item 2, the transition-matrix term:
  - **Its own block, `$.transition_matrix`, apart from the four-bound rows** (chosen).
  - **A fifth bound in each `$.tolerance_by_method` row.** Set aside: every reader of
    `load_frozen_tolerance` would change shape, and the rows' recorded evidence holds no
    transition-matrix measurement.
- Item 3, the default row:
  - **Re-measure on the pinned build and apply the freeze rule** (chosen; every bound stays).
  - **Keep the 2026-06-26 measurement on tMAVEN `71cfa1af` under CPython 3.9.23.** Set aside: it
    is not the build that the sidecar, CI and the installers run.
- Item 4, the frozen bounds:
  - **A literal copy of every frozen bound in the tests, checked against the file** (chosen).
  - **Keep only the self-checks that each recorded value is within its bound.** Set aside: a
    bound widened to any other finite value fails nothing.

## Decision

Issue #545 lands in three pull requests, one after the other (maintainer decision of 2026-11-15, for the
issue's size budget). Items 3 and 4 landed first; the second pull request added the
transition-matrix term (item 2); the third adds per-trace vbFRET (item 1).

### 1. Per-trace vbFRET is a sidecar model type with a seeded check, and no frozen row

The sidecar runner gains `model_type="vbfret"` (`run_vbfret_per_trace`). It calls tMAVEN's own
`tracelevel_vbhmm_modelselection` with tMAVEN's priors and model settings, for 1 to `nstates`
states per trace (the `{1, 2, 3, 4}` of the §11.2 auto selection), and keeps each trace's
highest-evidence model — vbFRET's procedure [Bronson2009]. It writes the model the way the
commented-out `run_vbhmm_modelselection` of that build writes it: type
`"vb HMM_model selection"`, ensemble `mean` NaN, one `trace_level/<i>` entry per trace.
`read_model` now reads that subgroup (`StateModel.trace_level`), and `compare_models` compares
two per-trace models with `compare_trace_models`: the same four quantities, per trace, on the
states each idealized path **occupies** (variational Bayes can keep a fitted state that no frame
uses; it is not a state of the idealization).
`run_vbfret` builds each trace's state path from that trace's own means. The project store does
not take the model: `/idealization` keeps one set of state means per model, so
`idealize_molecules` refuses `model_type="vbfret"` and neither it nor the hand-off import
persists a model with no finite state mean.

**Measured on the pinned build, it does not reproduce across seeds.** `scripts/measure_parity.py
--n-runs 20 --model-type vbfret` (2026-11-15; tMAVEN `10f4230b`, CPython 3.12.13; 19 comparisons
per fixture, anchored on run 00):

| Fixture | state count, worst / mean | Viterbi, worst / mean | per-state ΔE, worst | relative ELBO, worst / mean |
| --- | --- | --- | --- | --- |
| `smd_2mol` | 0.0 / 0.684 | 0.0 / 0.684 | `inf` (some pairs match on no trace) | 0.876 / 0.068 |
| `smd_4mol` | 0.0 / 0.276 | 0.0 / 0.274 | `inf` | 0.0471 / 0.0175 |

On the 281-molecule SMD (10 fits, 9 comparisons) the worst cases were 0.630 (state count), 0.526
(Viterbi), 0.961 FRET (per-state ΔE) and 0.0054 (relative ELBO). Keeping, for each trace, the
highest-evidence result of ten independent per-trace fits did not make it converge either: five
repetitions of that on `smd_2mol`, and three on `smd_4mol`, still occupied different states. The
ADR-0009 rule applied to this spread freezes floors of 0.0 and an unbounded ΔE ceiling. The spread
is a draw from tMAVEN's unseeded starts, so the same command gives other values of the same
order.

Why: tMAVEN's per-trace step makes **one** random start per state count (`vb_em_hmm_parallel`
ignores `nrestarts` in this build), and on short, noisy traces the evidence surface has many
local optima. vbFRET's own analyses keep the best of many restarts per state count — 25 in
[Bronson2010] — and a random start is known to change the log-likelihood reached and the
partition found [Maruotti2021].

So, by the maintainer's decision of 2026-11-15: **no per-trace row is frozen.** The `sidecar`
test `test_4mol_per_trace_vbfret_same_seed_gives_the_same_model` checks that the fit carries a
per-trace model for every molecule and that one seed gives one model value for value (the guard
of ADR-0075), and writes the cross-seed agreement of seeds 1 and 2 to the job log without
asserting it. `"vbFRET (per-trace, M2)"` stays in `$.coverage.applied_to` (decision of
2026-11-14); the coverage note says no test asserts a per-trace fit against that row. The §9.6
row for per-trace vbFRET parity stays open, restated with this finding, and points to
[#949](https://github.com/bioedca/tether/issues/949), the parity evidence for the per-trace HMMs.

### 2. The transition-matrix term is a fifth quantity with its own block

`transition_matrix_abs_delta(ref, test)` is the largest entry-wise `|Δ|` between the two
row-normalized transition matrices (`norm_tmatrix`, the per-frame transition probabilities),
both in canonical mean-sorted order; `inf` when the state counts differ or a matrix is missing,
malformed or non-finite. `ParityMetrics` carries it (default `inf`, "not computed").

It is frozen apart from the four-bound rows, under `$.transition_matrix`, with
`load_transition_matrix_tolerance(path, method)` and `within_transition_matrix_tolerance`. The
§11.2 row gives no provisional default for it, so the freeze is the ADR-0009 rule with the
default absent: the measured worst case × 1.5, **rounded up** to the freeze resolution of the
other bounds (four decimal places) and never below one step of it, `1e-4`
(`freeze_transition_matrix`). Rounding up keeps the ceiling above the widened worst case; the
one-step floor keeps an all-but-zero spread on one machine from freezing a ceiling another
machine's rounding noise breaks.

| Method | Evidence | Pooled worst | Frozen ceiling |
| --- | --- | --- | --- |
| `vbconhmm` | the item-3 runs: 19 cross-seed on `smd_4mol`, 20 against `model_281mol.hdf5` | 3.92e-09 | 0.0001 |
| `ebhmm` | 20 unseeded fits on `smd_281mol`, 19 cross-seed comparisons | 0.0294 | 0.0441 |

The ebFRET four-bound row of ADR-0043 is **not** re-measured: the transition-matrix term's
ebFRET evidence is a separate run, and the 2026-07-08 evidence behind the four bounds is
unchanged. That run's own four metrics stayed inside the frozen ebFRET row (worst state count
0.690, ΔE 0.0061, Viterbi 0.965, relative ELBO 7.9e-4). They are recorded here only, not in
the artifact. The live `sidecar / parity` arms assert the term on all three population
comparisons (the 281-molecule fit against the reference, the 4-molecule cross-seed pair, and the
ebFRET seed-1/seed-2 pair).

### 3. The default row is re-frozen on the pinned build, unchanged

`scripts/measure_parity.py --n-runs 20` (no `--cross-seed`: `smd_281mol` anchored on the
committed `model_281mol.hdf5`, as the live gate asserts) on tMAVEN `10f4230b` / CPython 3.12.13,
2026-11-15, recorded by the script's own build probe. Pooled worst cases: state-count and Viterbi
agreement 1.0; per-state ΔE 6.81e-09; relative ELBO 5.88e-10. The freeze rule again returns
`$.provisional`, so **every bound stays**. The artifact's `$.method`, `$.spread_by_fixture` and
`$.pooled_worst` hold the new measurement; `$.refreeze.previous` keeps the build, date and pooled
worst cases of the 2026-06-26 measurement it replaces.

### 4. The frozen bounds are pinned outside the file

`tests/test_parity.py::_ACCEPTED_BOUNDS` is a literal copy of every frozen bound: `$.tolerance`,
each `$.tolerance_by_method` row and each `$.transition_matrix.tolerance_by_method` row.
`test_frozen_bounds_match_the_accepted_copy` fails on any difference, and names a widened bound
as `WIDENED`; `test_widening_any_one_frozen_bound_fails` widens each bound alone and checks it is
reported. A deliberate re-freeze changes both copies in one pull request, under an ADR.

## Consequences

- Good: three of the four §9.6 rows close (transition matrix, re-freeze, loosened bound). Every
  frozen number is now measured on the build CI runs, and none can move silently.
- Good: per-trace vbFRET runs headless from Tether, and a change in it shows as a failure of the
  seeded check.
- Bad: per-trace vbFRET parity is still not met, and the reason is now a measured fact about the
  pinned tMAVEN's per-trace step, not a missing test. A row for it needs a fit that reproduces —
  for example best-of-N restarts per state count — which is new scope, carried by #949.
- Neutral: the transition-matrix ceiling of `vbconhmm` (1e-4) is far above its measured spread
  (≈ 4e-9) because of the resolution floor; the drift limit of #737 stays the tighter regression
  check of the four bounds and does not cover this term.

## More information

Evidence search (AGENTS.md §Evidence), 2026-11-15, Consensus, query *"variational Bayes hidden
Markov model single-molecule FRET per-trace model selection local optima random
initialization"*:

- [Bronson2009] Bronson J. E., Fei J., Hofman J. M., Gonzalez R. L. Jr., Wiggins C. H. *Learning
  rates and states from biophysical time series: a Bayesian approach to model selection and
  single-molecule FRET data.* Biophys. J. 97 (2009). doi:10.1016/j.bpj.2009.09.031 — maximum-
  evidence state-count selection per trace.
- [Bronson2010] Bronson J. E., Hofman J. M., Fei J., Gonzalez R. L. Jr., Wiggins C. H. *Graphical
  models for inferring single molecule dynamics.* BMC Bioinformatics 11 Suppl 8:S2 (2010).
  doi:10.1186/1471-2105-11-S8-S2 — vbFRET run with 25 random restarts per K, the
  highest-evidence restart kept.
- [Maruotti2021] Maruotti A., Punzo A. *Initialization of hidden Markov and semi-Markov models: a
  critical evaluation of several strategies.* Int. Stat. Rev. 89 (2021).
  doi:10.1111/insr.12436 — different starts reach different log-likelihoods and partitions
  (second source).

The three citations were signed off by the maintainer on 2026-11-16.

Code: `schema/parity_tolerance.json` (`$.refreeze`, `$.transition_matrix`),
`src/tether/idealize/parity.py` (`transition_matrix_abs_delta`, `freeze_transition_matrix`,
`load_transition_matrix_tolerance`, `compare_trace_models`),
`src/tether/idealize/_sidecar_runner.py` (`run_vbfret_per_trace`),
`src/tether/idealize/driver.py` (`read_model`, `StateModel.trace_level`),
`scripts/measure_parity.py`, `tests/test_parity.py`
(`_ACCEPTED_BOUNDS`), `tests/test_parity_sidecar.py`, `docs/validation.md` §(b),
`tests/test_docs_validation_numbers.py`.
