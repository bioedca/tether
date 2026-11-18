<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0120 — A sum-of-Gaussians fit of a FRET histogram, its two area definitions and its errors

- **Status:** proposed
- **Date:** 2026-11-16
- **Deciders:** bioedca
- **PRD anchor:** §7.7 FR-ANALYZE (FRET histograms), §8 NFR-VALID
- **Milestone:** M17
- **Issue:** [#1030](https://github.com/bioedca/tether/issues/1030); the reference is that of
  [ADR-0097](0097-the-2024-t-box-source-data-is-an-advisory-analysis-reference.md) (#756)

## Context and problem statement

Campos-Chavez et al., *Nat Commun* 15:6592 (2024), doi:10.1038/s41467-024-50885-x, report the
fraction of the closed state of the T-box riboswitch in Fig. 5c. The Methods ("IntraFRET data
analysis") fit two Gaussians to the FRET histogram of the first 400 frames of each molecule and
take the area of each Gaussian as the population of its state. ADR-0097 showed that Tether gives the
Fig. 5b efficiencies, but Tether had no fit, so Fig. 5c had no test.

A fit of Gaussians to a FRET histogram is a general analysis, not one paper's: the histogram of a
molecule with a few states is approximated by a sum of Gaussians, one for each state
[Gopich2010], and a Gaussian fit of the histogram is the standard method that likelihood methods
for populations are compared with [Gopich2024]. What does Tether's fit compute, which area does it
report, and how are its errors propagated?

The archive answers the reference side. Its notebook
`ErrorCalculations_CodeAndSourceData/FractionOfClosedState/ErrorFractionOfClosedState.ipynb` bins
the efficiencies with `np.linspace(-0.2, 1.2, 200)` (199 bins, density), fits
`A1 exp(-(x - mu1)^2 / 2 s1^2) + A2 exp(-(x - mu2)^2 / 2 s2^2)` with `scipy.optimize.curve_fit` from
`p0 = [max/2, 0, 0.1, max/2, 0.33, 0.1]`, and then:

- takes each **area** as the sum of the Gaussian at the 199 bin centres times the bin width, while
  the Methods give `A sqrt(2 pi) sigma`;
- takes each **area error** as `sqrt(2 pi) sqrt((A dsigma)^2 + (sigma dA)^2)` and the error of
  `R = A2 / (A1 + A2)` as `R sqrt((dA2 / A2)^2 + (dT / T)^2)` with `dT = sqrt(dA1^2 + dA2^2)`. No
  covariance enters, and `A2` and the total `T`, which contains it, are treated as independent.

## Decision drivers

- **Issue #1030, criterion 2:** the pull request says which area formula it reproduces.
- **A limit comes from a measurement or from a derivation** (ADR-0097). The published fractions
  and errors have four decimals.
- **Tether's own errors follow the method that is correct for correlated parameters**: the
  first-order propagation `var(g) = J C J^T` with the full covariance `C` [Tellinghuisen2001]. In a
  Gaussian fit, a peak's height and width are strongly anti-correlated.
- **Reproducing a published method is not adopting its errors** — the rule ADR-0097 applied to
  tMAVEN's error pairing.

## Considered options

1. **Only the Methods' area `A sqrt(2 pi) sigma`.** Rejected: it does not reproduce Fig. 5c. For
   13 uM WT tRNA it gives 0.4158, not 0.4166 (table below).
2. **Only the notebook's bin-sum area.** Rejected as the only one: it is the area inside the
   histogram range, so it depends on the range, and it is not the area of the Gaussian.
3. **Both, chosen by an argument, `"integral"` the default (chosen).**
4. **Reproduce the notebook's error formula in Tether.** Rejected: it leaves out the covariances
   and treats a part of a sum as independent of the sum. The test computes it, from Tether's
   parameter errors, to compare with the published errors; Tether does not return it.

## Decision outcome

Chosen option: **3**. `tether.analysis.gaussian_fit.fit_gaussians(histogram, initial_means, *,
initial_width=0.1, area="integral")` fits one Gaussian for each initial mean to a `Histogram1D`:

- **The fit.** Unweighted least squares on the bin centres and heights by `curve_fit`'s default
  Levenberg-Marquardt, with no bounds, from the peak height `max / n`, the given means and the width
  0.1 — with two Gaussians, the notebook's starting point. The parameter errors are
  `sqrt(diag(pcov))` with `pcov` scaled by the residual variance (`curve_fit`'s default,
  `absolute_sigma=False`; scipy 1.18.0 of the base lock). A negative width is reported as its
  absolute value, the covariance sign following, and the Gaussians are returned in ascending order
  of mean. A fit that does not converge or has an infinite covariance returns `success = False`
  and `NaN`.
- **The areas.** `area="integral"` is `A sqrt(2 pi) sigma`, the area over the whole axis.
  `area="bin_sum"` is the notebook's sum over the bin centres. Fractions are `areas / sum(areas)`.
- **The errors of the areas and the fractions** use the full covariance and the analytic
  derivative of each area definition. A test compares them with a numerical derivative (to `1e-5`)
  and with the spread of 300 fits of noisy histograms (to 15 %).

### What reproduces Fig. 5c

The weekly `reference_download` test fits Tether's histogram of Tether's efficiencies of each
column's 400-frame windows (the windows ADR-0097 recovers). For 5 uM WT tRNA the archive has no
trace matrix, so that column fits the published efficiencies. Tether's histograms equal the
notebook's in every bin. With `area="bin_sum"` and the notebook's error propagation applied to
Tether's parameter errors, Tether gives what the notebook printed:

| Fig. 5b column | Tether | Notebook printed | Fig. 5c | Tether's own error | `integral` |
| --- | --- | --- | --- | --- | --- |
| No tRNA | 0.309460 ± 0.012753 | 0.3095 ± 0.0128 | 0.3095 ± 0.0128 | 0.006172 | 0.309446 |
| 5 uM WT tRNA | 0.395645 ± 0.010610 | 0.3956 ± 0.0106 | **0.3961** ± 0.0106 | 0.005019 | 0.395269 |
| 13 uM WT tRNA | 0.416600 ± 0.014674 | 0.4166 ± 0.0147 | 0.4166 ± 0.0147 | 0.006783 | 0.415838 |
| 5 uM tRNA-NCCA | 0.362293 ± 0.018916 | 0.3623 ± 0.0189 | **0.3630** ± 0.0189 | 0.009559 | 0.362106 |
| 13 uM tRNA-NCCA | 0.385275 ± 0.015356 | 0.3853 ± 0.0154 | 0.3853 ± 0.0154 | 0.006871 | 0.385230 |

- **The limit is `5e-5`, half a unit in the fourth decimal**: a value equal to the unrounded one
  is within it. It is derived from the printing, not chosen. The largest measured deviation from the
  notebook's printed numbers is 4.7e-5 (the error of the No-tRNA column), on Linux (this
  container, Python 3.12, scipy 1.18.0, NumPy 2.1.3).
- **The published fractions use the bin-sum area** (criterion 2). With the Methods' area, 13 uM WT
  tRNA is 0.4158; the test holds that apart from Fig. 5c by more than ten times the limit.
- **Two Fig. 5c fractions are not the notebook's.** For 5 uM WT tRNA the notebook printed 0.3956
  and Fig. 5c (the workbook's `Fig5c` sheet and the notebook's plotting cell) has 0.3961; for 5 uM
  tRNA-NCCA, 0.3623 and 0.3630. Their errors are the notebook's. No window choice, area definition
  or frame range that the archive allows gives 0.3630 (frames 0-399 of every molecule give 0.3621,
  frames 1-400 give 0.3627). The test records the two as columns where Fig. 5c differs from the
  notebook, and holds Tether to the notebook there. Tether's limit is not widened to reach them.
- **Tether's own error is about half the published one** in every column, for two reasons of
  about equal size. In the No-tRNA column, the correct first-order formula without the covariances
  gives 0.0088 instead of the notebook's 0.0128 (treating `A2` and `T` as independent adds the
  rest), and the covariances bring it to 0.0062: the height and the width of each Gaussian have a
  correlation of -0.58. Both are errors of the least-squares fit
  of the bins, which treats each bin as independent; the 400 frames of a molecule are not
  independent, so neither is a molecule-level uncertainty. The molecule bootstrap of the histogram
  (`bootstrap_histogram_ci`, [König2013]) is the route to that, outside this decision.

### Consequences

- Good: Tether has a Gaussian fit of a histogram for `n` states, with areas, fractions and errors
  that keep the covariances. Fig. 5c has a weekly test in each of its five columns.
- Good: criterion 2 is answered by measurement: the published numbers are the bin-sum areas.
- Trade-off: two definitions of an area. The default is the area of the Gaussian; the bin sum is
  there to reproduce, and is named for what it is.
- Follow-up: the two 5 uM differences in Fig. 5c and the error propagation of the notebook are
  reported to the maintainer, who is the paper's first author. Tether does not change for them; a
  correction of the published figure is a decision outside this repository.
- Follow-up: the reference stays advisory (ADR-0097); a weekly failure opens the job's issue
  (#697). The GUI has no control for the fit; this decision adds the API only.

## More information

- Code: `src/tether/analysis/gaussian_fit.py`; tests: `tests/test_analysis_gaussian_fit.py`
  (required matrix), `tests/test_campos_chavez_2024.py` and `tests/_campos_chavez_2024.py`
  (weekly, `reference_download`). The archive is `campos2024-source-data` of the manifest
  (SHA-256 `83141ce0…9376c71`, CC BY-NC-ND 4.0, ADR-0083); the notebook and the `Fig5c` sheet are
  read from it when the test runs and nothing derived from them is committed.
- Evidence searched with Consensus on 2026-11-16 ("fitting Gaussian functions to single-molecule
  FRET efficiency histograms to determine state populations"; "propagation of uncertainty with
  covariance matrix of least-squares parameters for derived quantities") and checked with Scite
  (`verify_papers`: all four verified, no editorial notice):
  - [Gopich2010] Gopich & Szabo, J. Phys. Chem. B 114:15221 (2010), doi:10.1021/jp105359z.
  - [Gopich2024] Gopich & Chung, J. Phys. Chem. B 128 (2024), doi:10.1021/acs.jpcb.4c01178.
  - [Tellinghuisen2001] Tellinghuisen, "Statistical error propagation", J. Phys. Chem. A 105
    (2001), doi:10.1021/jp003484u.
  - [CamposChavez2024] doi:10.1038/s41467-024-50885-x.
  - [König2013] König et al., PLoS ONE 8:e84157 (2013), the molecule bootstrap already cited by
    `tether.analysis.histogram`.
- Interface: `scipy.optimize.curve_fit` of scipy 1.18.0 (base `conda-lock.yml`): the default method
  is `lm` without bounds, and `absolute_sigma=False` scales `pcov` by `chisq / (M - N)` (its
  installed docstring; Context7 `/scipy/scipy` v1.18.1 had no more detail).
