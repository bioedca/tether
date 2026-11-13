<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0081 — kinSoft levels 2 and 3 are reported against the simulator settings, never band-checked

- **Status:** accepted; amends [ADR-0048](0048-kinsoft-kinetics-oracle.md) on the deferred levels
- **Date:** 2026-11-12
- **Deciders:** bioedca
- **PRD anchor:** §8 NFR-VALID(c), §9 M8, §11.2 (the kinSoftChallenge parity band row), N2
- **Milestone:** M19
- **Issue:** [#717](https://github.com/bioedca/tether/issues/717)

## Context and problem statement

[ADR-0048](0048-kinsoft-kinetics-oracle.md) made oracle (c) a band check on the 2-state level 1 of
the kinSoftChallenge [Götz2022] and deferred levels 2 and 3. It recorded their ground truth by
hand from the paper's supplement: level 3's full matrix from Suppl. Table 1, and only two of level
2's six rates, read from a diagram. #717 asks for both levels, with the truth taken from the
simulator's own settings files, and the maintainer chose (interview of 2026-11-11, Q24: A, R4: A,
B, E) to add level 2, to add level 3 through dwell-time distributions, to compare with the truth,
and to keep every leg advisory. Four things are undecided: where the truth lives, what level 2
measures, what level 3 compares, and what "advisory" means for a level the method cannot pass.

## Decision drivers

- No truth value typed by hand (#717's first acceptance criterion); a hand transcription had
  already gone wrong once (below).
- PRD N2: Tether ships no data simulator.
- No new idealization method (#717, out of scope): measure the present one.
- Never weaken or invent a band to fit a result (AGENTS.md).

## Decision outcome

**Truth → the simulator settings files, committed verbatim.** The record's `MATLAB_simulator.zip`
holds `Config_simulation_round_1_level_N.cfg` for each level: the rate matrix, the frame rate, the
bleach rate, the blinking rates, the map of states to degenerate states and the mean intensities.
The three files (9 kB together) are committed byte for byte under
`tests/fixtures/kinsoft_simulator/` with manifest rows (ADR-0073), a REUSE annotation, NOTICE and
PROVENANCE entries; the MATLAB code is not (N2). `tests/_kinsoft_config.py` parses them, and
required-matrix tests hold every ground-truth value in `schema/kinsoft_reference.json` to the
parsed file. The JSON keeps a copy because the docs page and its guard read it; the test is what
stops the copy from drifting. Level 1's file gives level 1's reference unchanged.

The files corrected one recorded value. The deferred level-2 entry read `k12_low_mid = 0.05` and
`k21_mid_low = 0.30` from the diagram; the settings file has 0.30 (low→mid) and 0.05 (mid→low), and
Tether's fit agrees with the file on the faster direction of every pair. Level 3's Suppl. Table 1
matrix equals the file's under a renumbering of the states, recorded as `settings_state_of`, so
its values and the paper's state numbers stay.

**Level 2 → a 3-state fit and the six rates `k_ij = n_ij / T_i`.** The existing base-env Gaussian
HMM at `nstates = 3`, Viterbi, then the pooled transition counts over the dwells
`pooled_exit_rates` counts, so each row sums to that function's exit rate. This is the
maximum-likelihood rate of a continuous-time chain whose every transition is seen. The estimator
lives in the test helper, not in `src/`: it is part of the check, and #717 adds no product method.
The result: the three FRET levels are found, and the rates are 28–375 % off. The cause is measured:
a low↔high jump inside a 0.1 s frame averages to a mid-FRET frame, read as a one-frame mid dwell
(689 of 1638 counted mid dwells). What is missing is a correction for transitions inside a frame;
that is a method change and a separate issue.

**Level 3 → each FRET level's dwells against the closed-form aggregated-Markov distribution.** A
2-state fit sees the two FRET levels. For a class `A` of states that share a FRET value, a dwell
starts with the entry probabilities `φ_A ∝ π_B Q_BA` at steady state and survives past `t` with
probability `φ_A exp(Q_AA t) 1` (Colquhoun & Hawkes 1981, doi:10.1098/rspb.1981.0003; 1982,
doi:10.1098/rstb.1982.0156). The pooled Viterbi dwells of each level are compared with it by the
Kolmogorov–Smirnov distance and by the relative error of the mean. The benchmark compares this
level through cumulative dwell-time distributions too, though it simulates them from each submitted
model [Götz2022]; the closed form is that simulation's limit and needs no simulator (N2). The
observed dwells come out about 30 % short, mostly because the traces are short against the dwells
and a dwell a trace end cuts is never counted.

**Advisory → printed and frozen, never band-checked.** Each leg prints its errors and pins them, at
the file's decimals, in `tether_measured`, so a change of the method fails the `large` tier the way
level 1's pins do. No band is applied, because none is sourced for these levels and inventing one
to fit the result is what the contract forbids. `oracle` reads `"report"` for both levels (it read
`"deferred"`), `evaluate_kinsoft_level` still refuses them, and `schema_version` goes from 1 to 2
because keys were removed and level 2's values changed meaning. The maintainer decides later
whether a leg becomes a gate (#717 item 4).

### Consequences

- **Good:** the truth of all three levels is read from the files the data were simulated from;
  both hard levels run beside level 1 and say what they measure; the diagram error is gone.
- **Bad / trade-off:** level 2's rates show that the present method cannot recover them, and level
  3's distance mixes the fit with the dataset's windowing; neither number is a claim of accuracy.
  The settings files are copied from a zip whose `license.txt` grants MIT terms to the simulator
  code; Tether takes the record's CC-BY-4.0 for the settings files and copies no code.
- **Follow-up:** a correction for transitions inside a frame (level 2), and a windowing-aware
  prediction for level 3, would each be a method change with its own issue.

## More information

- Code: `tests/_kinsoft_config.py` (parser, the aggregated-Markov prediction, `pooled_rate_matrix`,
  `ks_distance`).
- Tests: `tests/test_kinsoft_levels.py` (required: truth against the files, parser, prediction and
  estimator; `large`: the level-2 and level-3 legs).
- Data: `tests/fixtures/kinsoft_simulator/*.cfg` (CC-BY-4.0, Zenodo 10.5281/zenodo.5701310);
  manifest ids `kinsoft-simulator` and `kinsoft-settings-level1` to `kinsoft-settings-level3`.
- Related: [ADR-0048](0048-kinsoft-kinetics-oracle.md) (oracle (c)),
  [ADR-0073](0073-one-manifest-for-reference-data-sets.md) (the manifest), #718 (the measured
  sets).
