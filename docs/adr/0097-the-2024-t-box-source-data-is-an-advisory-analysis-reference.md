<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0097 — The Source Data of the 2024 T-box paper is an advisory reference for the analysis functions

- **Status:** accepted
- **Date:** 2026-11-14
- **Deciders:** bioedca (interview of 2026-11-11, recorded on issue #756: note of batch 1, S1 A,
  Q7 A, S3 A, S4 B, S5 A)
- **PRD anchor:** §8 (NFR-VALID), §7.4 (apparent E), §7.7 (histograms, dwell times, the TDP)
- **Milestone:** M19

## Context and problem statement

ADR-0083 put the Source Data of Campos-Chavez et al., *Nat Commun* 15:6592 (2024),
doi:10.1038/s41467-024-50885-x, in the reference-data manifest. The paper's histograms, survival
fits and transition density plots (TDPs) were made with tMAVEN 0.2.3 (commit 62422f9) and the
authors' scripts. Tether has functions for each of these steps. Issue #756 asks that Tether give the
published numbers, to numerical precision where a step has no random part, and within a measured,
frozen limit where a step is a fit.

The archive has two properties that decide how a comparison can work:

1. Each trace matrix of `smFRET_SourceData/` writes each intensity with 8 significant digits
   (`%.7e`). The workbook `MainFigures-AllCombined.xlsx` writes each published efficiency with 15.
   The published efficiencies were calculated from intensities with more digits than the archive
   holds, so an efficiency calculated from the archive is not bit-identical to the published one.
2. The workbook gives, for each histogram, the pooled efficiencies, but not which frames of which
   molecule they are (tMAVEN's analysis windows). It gives each survival curve and its fitted
   model, but not the dwells. It gives each TDP's transitions, but not the smoothed array.

What is compared, with which limit, and where does it run?

## Decision drivers

- **A limit comes from a measurement or from a derivation, never from a choice** (#756, Q7 A).
- **The licence forbids a changed copy** (ADR-0083). Anything a test derives from the archive (a
  window list, a dwell list) is derived when the test runs and is not committed.
- **The required matrix makes no network call** (ADR-0073). The committed extract holds only the
  double-exponential parameter files, `checkTDP.py` and one intraFRET trace matrix.
- **This is the maintainer's own study.** A reference that the author of the code under test also
  wrote is advisory, as the kinSoftChallenge oracle (c) is.

## Considered options

1. **A fixed relative limit on each efficiency** (for example `1e-6`). Rejected: near `D + A = 0`
   the rounding of the intensities moves the efficiency by up to `1e-4` relative, and far from it by
   `1e-9`. One number is either loose for most frames or fails for a few.
2. **Commit the recovered windows and dwell lists.** Rejected: they are derived from CC BY-NC-ND
   material (ADR-0083) and they can be derived again in a second.
3. **Compare with a bound derived from the rounding, and recover the windows and dwells when the
   test runs (chosen).**

## Decision outcome

### The method

- **Efficiencies.** Each intensity of the archive is its unrounded value to within `dX`, half a
  unit in the 8th digit of `X`, so the unrounded pair lies in the box `[D - dD, D + dD] x
  [A - dA, A + dA]`. While `D + A` keeps its sign on the box, `E = A / (D + A)` is monotonic in
  each intensity, and the bound is the largest change of `E` at the four corners; both the rounded
  and the unrounded denominator enter it (`tests/_campos_chavez_2024.py::rounding_bound`). Where the
  interval of `D + A` contains zero, no finite bound exists: the bound is infinite and that frame
  is not checked. Each published efficiency is held to **deviation / bound <= 1** where the bound
  is finite. The limit is the bound itself; the measured largest ratio is 0.91, and no published
  value falls on a frame without a finite bound.
- **Histogram windows.** Each published value is located in the trace matrix within the bound, and a
  run of consecutive matching frames of one molecule is one window (`find_windows`). Tether's
  `apparent_fret` over the recovered windows, binned by `apparent_e_histogram`, must give the same
  counts in every bin as the published values binned the same way.
- **Survival curves.** The least event count `N` for which `N * survival` is a whole number gives
  back the dwell lengths (`dwells_from_survival`). Tether's `survival_curve` of those dwells must
  give the published curve, and `fit_survival` with the published model (single or double
  exponential) must give the published `Model` and `Residual` columns.
- **TDPs.** Tether's `transition_density` with `checkTDP.py`'s grid (50 bins, -0.25 to 1.25) must
  give `numpy.histogram2d`'s counts exactly. That is the array before `checkTDP.py`'s smoothing.

### The limits (measured on Linux, macOS and Windows)

| Comparison | Measured largest | Frozen limit |
| --- | --- | --- |
| Representative traces (Figs. 2b, 3g, 4a, 4e), relative | 9.8e-15 | 2e-14 |
| Histogram and intraFRET efficiencies, ratio to the rounding bound | 0.91 | 1.0 (the bound) |
| Histogram bin counts, bins that differ | 0 | 0 |
| Survival curve, absolute | 1.0e-15 | 1e-14 |
| Survival time axis, relative to the last time | 2.2e-16 | 1e-15 |
| Fitted model and residual, relative to the model's first point | 5.9e-6 | 2e-5 |
| Double-exponential parameters vs the parameter files, relative | 1.3e-5 | 5e-5 |
| Their errors vs the parameter files (re-paired, below), relative | 3.2e-3 | 1e-2 |
| Mean dwell time `tau` of the fit vs the parameter files' `tau`, relative (#1129) | 9.3e-6 | 5e-5 |
| That `tau` vs the `.out` file, absolute (s) | 2.5e-3 | 5e-3 (half a unit of the second decimal) |
| Its error vs the parameter files' error, re-paired, relative (#1129) | 1.8e-3 | 1e-2 |
| TDP counts, bins that differ | 0 | 0 |
| Smoothed TDP array (800 x 800, peak 1), absolute; Amendments below | 0.0 | 1e-12 |

Each value is the largest of the three operating systems (run 43610165074 of a temporary
workflow on this branch). The efficiencies, bin counts, survival curves and TDP counts were the same
on the three, to the last digit of the ratio. The fitted values differ between them from the seventh
digit (macOS gave the largest parameter deviation, 1.3e-5); those are the deviations of a fit and
its optimizer. The error deviation is the rounding of the published errors to three digits.

### Where the tests run

The comparisons need the full archive, so they carry `reference_download` and run in the weekly
`reference-data` job, which downloads and verifies the archive (ADR-0073). Issue #756 says `large`;
`large` is the Git-LFS job, which has no network, and the marker contract keeps a download test out
of it. The required matrix runs the method on data built in the test: the bound, the window search,
the dwell inversion and the column reader.

### What the archive says that the paper does not

- **tMAVEN pairs the double-exponential errors with the wrong parameters.** tMAVEN's
  `optimize_double_surv` (0.2.3 at 62422f9, and 0.3.0 at 10f4230 in the sidecar lock) sorts the two
  rates and the two amplitudes, but not their errors. For the three double-exponential panels whose
  curve is in the workbook (Fig. 2e left, 3e, 7d), Tether's standard error of each parameter is the
  published error of the other parameter (to 3e-3), and it is not the published error of the same
  parameter (off by a factor of 19 to 60). Tether's own errors are paired correctly. The test
  records this; it does not adopt tMAVEN's order.
- **Three of the six parameter files have no curve in the workbook** (`Double-Wt+Wt-0`,
  `Double-RAG-0`, `Double-Wt+NCCA-2State-0`). Their pairing cannot be checked from the archive.
- **The archive has no trace matrix for 5 uM WT tRNA** (Fig. 5b). `intraFRET_WT+WT_5uM.txt` is a
  byte copy of the 13 uM file, and the 5 uM column has 163 molecules, not 140.
- **`WT+NCCA_tRNA.txt` has 1499 frames**; the other matrices have 1500. A histogram window that
  reaches the end of a molecule has one published value more than the matrix (22 values in Fig. 3b).
- **The intraFRET notebook reads each movie's file with `skiprows=1`.** The archive's matrix joins
  those files, so for each molecule the first 400 frames start at frame 0 (the first files) or at
  frame 1 (the later ones).

### Steps that get no test, and why

| Step | Why there is no test |
| --- | --- |
| Population fits of the histograms (Figs. 2c, 3b, ...) | HMM fits, #757 |
| Rates and their Dirichlet bounds (Supplementary Table 5) | #758 |
| Two-Gaussian fit and closed-state fraction (Fig. 5c) | Tether has no histogram Gaussian fit: #1030 |
| Fig. 5b, 5 uM WT tRNA column | no trace matrix in the archive |
| Transition-synchronized histograms (Supp. Figs. S3-S6) | they need the Viterbi paths, which the archive does not hold (#757) |
| Representative traces of Supp. Figs. S5a and S6a | same check as Figs. 2b-4e; reading the 30 MB second workbook adds time and no new case |
| BLI and SEC data (Supp. Figs. S1, S2) | not smFRET; Tether has no function for them |

## Consequences

- Tether's efficiency, histogram, survival curve, exponential fit and TDP counts have an external
  reference on measured data: 35 published panels and columns, about 1.3 million published values.
- A deliberate error in each of the four groups makes a weekly test fail (#756 records the runs).
- The reference is advisory. A failure opens the weekly job's issue (#697); it does not gate a
  merge or a release.
- The tMAVEN error pairing is reported to the maintainer, who is the paper's author. Tether does
  not change; a correction of the published errors is a decision outside this repository.

## Amendment, 2026-11-17 — the mean dwell time of the double exponential (#1129)

\#1028 added `mean_dwell_time` and `DwellFit.mean_dwell_time`, so the step "Mean dwell time `tau`
of a double exponential and its error (the six `.out` files)" leaves the table of steps with no
test. Two tests cover it:

- **Required matrix.** `tests/test_analysis_dwell.py::test_mean_dwell_time_reproduces_the_campos_chavez_out_file`
  (from #1028): for each of the six committed `.txt` and `.out` pairs, `mean_dwell_time` of the
  parameters, with the errors in the order the file gives them, prints the `.out`'s `tau` and its
  error to the two decimals the file keeps. It checks the formula, not the pairing.
- **Weekly (`reference_download`).**
  `tests/test_campos_chavez_2024.py::test_mean_dwell_time_of_the_fitted_panel_is_the_published_tau`:
  for the three panels whose curve is in the workbook (Fig. 2e left, 3e, 7d), Tether fits the
  published survival curve and takes `DwellFit.mean_dwell_time` of its own fit. Its `tau` must
  equal the `tau` of the parameter file and the `.out` value within the limits in the table above.
  The references come from the parameter file through the script's formula, written out in the test
  helper, not from Tether's function, so an error in the function cannot cancel out.

**Which error is checked.** The test checks Tether's error against the script's formula with each
error paired with its own parameter: the re-pairing of the first item of "What the archive says
that the paper does not". The published `.out` error uses tMAVEN's order, so it pairs each error
with the other parameter: it is reported beside Tether's (3.08 s against 0.077 s, 1.37 s against 0.088 s, 2.33 s against 0.053 s), the
test asserts that it is more than ten times Tether's, and that the script's formula with the
errors as labelled gives it to the `.out`'s two decimals. Its `tau` does not depend on the errors
and is unaffected.

**Measured** on Linux, macOS and Windows (run 44164888842 of a temporary workflow on the branch,
removed before review). The largest of the three is in the table above: `tau` 9.3e-6 relative
(macOS), 2.5e-3 s from the `.out` (Linux), and the error 1.8e-3 relative (Linux); the three agree
to the sixth digit. The error limit is the one of the parameter errors, whose published values
keep three digits. A deliberate error makes the weekly test fail in all three panels: `tau` times
1.0001, and the error times 1.02.

## Amendment, 2026-11-17 — the smoothed transition density (#1029, #1130)

Issue #1029 added `smooth_transition_density`, the array `checkTDP.py` computes after `histogram2d`: a
Gaussian filter of 1 bin, a bicubic spline through the bin centres onto 800 x 800 points from -0.25
to 1.25 with negative values set to 0, and a division by the maximum. It is compared one step past
the TDP counts above, with the same rule.

- **Weekly** (`reference_download`): for each of the seven TDP sheets, Tether's view of Tether's
  counts beside the array of the committed `checkTDP.py`, run on the sheet's two columns from its
  first line to its `# Plot` comment. Only the CSV read and the two imports it needs for the CSV and
  the plot are left out, and each of those lines must still be in the script. Same shape, same grid,
  largest absolute difference at most the limit.
- **Required matrix**: the same comparison on 606 transitions built in the test (three states, plus
  pairs on both edges of the range, on interior bin edges and outside it), so the method is checked
  on every push without the archive.
- **Deliberate errors**: a filter of 1.1 bins on both axes, on the initial or the final axis alone,
  and a view left unnormalized each exceed the limit by more than 10^6 (a required-matrix test).
  With Tether's default filter set to 1.1 bins, the built-counts test and all seven weekly tests
  fail.

**Measured** (run 44163991160 of a temporary workflow on the #1130 branch; NumPy 2.1.3 and SciPy
1.18.0 on all three): the largest absolute difference is 0.0 on Linux, macOS and Windows, for each of
the seven sheets and for the built counts. The smallest deliberate error measured 0.029 on the built
counts and 0.067 on the sheets, the same on the three systems to the seventh digit.

**The limit is not derived from the measurement.** Both sides make the same SciPy calls on the same
counts, so they agree to the bit wherever the counts are equal. The limit of 1e-12 that #1029 set is
kept: about 4,500 times the spacing of doubles at the peak value 1, room for a change in the order of
the operations, and more than 10^10 below the smallest error measured. The maintainer chose to keep
it on the #1130 pull request (2026-11-17); a later change of it is again the maintainer's decision
on its pull request (M19).
