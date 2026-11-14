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

- **Efficiencies.** The rounding of `D` and `A` to 8 digits moves `E = A / (D + A)` by at most
  `(|D| dA + |A| dD) / (D + A)^2`, where `dX` is half a unit in the 8th digit of `X`
  (`tests/_campos_chavez_2024.py::rounding_bound`). Each published efficiency is held to
  **deviation / bound <= 1**. The limit is the bound itself; the measured largest ratio is 0.91.
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
| TDP counts, bins that differ | 0 | 0 |

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
| Mean dwell time `tau` of a double exponential and its error (the six `.out` files) | Tether has no function for it: #1028 |
| TDP smoothing, spline and normalization (`checkTDP.py` after `histogram2d`) | Tether's TDP keeps the raw counts and the GUI has no smoothing: #1029 |
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
