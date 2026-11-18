<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0108 — MASH-FRET's stored bootstrap is the reference for Tether's histogram bootstrap

- **Status:** accepted
- **Date:** 2026-11-15
- **Deciders:** bioedca (the bootstrap errors of the histogram populations are a MASH-FRET
  reference, Q15 B; saved files first and a MATLAB run only when no saved file has the result,
  R16 C — interview of 2026-11-11; a reference check becomes an oracle with a limit in a schema
  file, an ADR and a row in `docs/validation.md`, Q36 A; the limit |z| ≤ 3 as a gate, decision of
  2026-11-15 on #713)
- **PRD anchor:** §7.7 FR-ANALYZE (FRET histogram with bootstrap CI), §8 NFR-VALID, §11.2
  ("FRET-histogram bootstrap" row)
- **Milestone:** M19
- **Issue:** [#713](https://github.com/bioedca/tether/issues/713)

## Context and problem statement

Tether's FRET histogram bootstrap (`bootstrap_histogram_ci`) follows BOBA-FRET [König2013]:
resample the molecules with replacement and rebuild the histogram. MASH-FRET implements the same
method in its Histogram analysis (`gauss_ana.m`). No test compared the two. #713 asks first whether
the public MASH-FRET project files (Zenodo 24104528, [ADR-0087](0087-mash-fret-is-a-reference-for-corrected-fret.md))
store bootstrap results, so that no MATLAB run ([ADR-0074](0074-matlab-reference-outputs-come-from-pinned-runs.md))
is needed.

They do. The committed Path A extract of [ADR-0101](0101-mash-fret-analyses-are-a-histogram-and-state-reference.md)
keeps the tag's `prmThm` cell whole: `thm_start{1} = [1 1 17 100 1]` (bootstrap on, 17 molecules
drawn for each sample from the tag's 17, 100 samples, each molecule's histogram weighted to sum 1),
the fit start and bounds of three Gaussians (`thm_start{3}`), each sample's histogram
(`thm_res{2,3}`, 49 bins × 100) and each sample's fitted `[A, μ, FWHM, population]`
(`thm_res{2,2}`). `thm_res{2,1}`, the populations ± SD that #712 reports, is their mean and SD.

A bootstrap is random, so Tether and MASH-FRET cannot give equal numbers, and MASH-FRET's numbers
are one run whose seed is not recorded.

## Decision drivers

- **A reference value comes from the reference tool**: MASH-FRET's own samples and fits, not a
  re-implementation of them.
- **The limit comes from the measured seed-to-seed spread** (#713), not from a wish.
- **Tether's source is not changed** for the test (#713: only for a defect, in its own issue).
- **The required matrix makes no network call**: the extract is already committed.

## Considered options

1. **z-scores against MASH-FRET's stored samples, limit 3 (chosen).** Tether's bootstrap, for ten
   fixed seeds with MASH-FRET's sample count and weights; each compared value is a z-score whose
   denominator is the measured seed-to-seed SD times `sqrt(1 + 1/10)` (MASH-FRET's value is one run
   with that spread).
2. **A MATLAB run with a recorded seed.** Not needed: the saved file has the result (R16 C).
3. **Freeze each measured difference, rounded outward, as in ADR-0101.** Rejected: a bootstrap
   difference is a draw, and a limit at the draw would fail at the next draw of the same, correct
   code. A z-limit states what a correct bootstrap is expected to give.
4. **Compare only the per-bin SD.** It is Tether's own output and needs no fit, but the issue asks
   for the populations, and the frame bootstrap barely moves their means.

## Decision outcome

Chosen option 1; the maintainer signed off the limit as a gate on 2026-11-15. `tests/test_mashfret_bootstrap.py`, required matrix, about 10 s; the limit and the
measurement are in `schema/mashfret_bootstrap_limits.json`.

- **Per-bin SD.** Tether's `std` output (density × bin width = each bin's share, as MASH-FRET
  normalizes) against the SD of MASH-FRET's 100 sample histograms, on the 26 bins whose MASH-FRET
  SD is at least a tenth of the largest. Measured largest |z| 1.11.
- **Gaussian populations, mean and SD.** Tether's samples, one `n_resamples=1` call each (the
  interval of one sample is that sample), fitted with MASH-FRET's model — the sum of
  `A·exp(-(x-μ)²/2σ²)` by bounded least squares from MASH-FRET's stored start and bounds, a
  population being the Gaussian's share of the summed curve over the bin centres. Measured largest
  |z| 1.17 (mean) and 2.20 (SD).
- **Limit |z| ≤ 3** for every compared value. Six other seed sets gave a largest |z| of at most
  2.69, so the limit is not tuned to the chosen seeds.
- **Teeth.** A bootstrap that resamples the pooled frames gives |z| = 130 on the per-bin SD
  (molecules differ, frames of one molecule do not); a test keeps this case.

### Consequences

- Good: Tether's bootstrap has an external reference on measured data, and a change of the
  resampled unit fails the required matrix.
- Bad: the population comparison includes the difference between SciPy's and MATLAB's fitting
  code. Fitted to MASH-FRET's own sample histograms, SciPy's fit moves the population means by up
  to 0.010 and the SDs by up to 0.008 — part of each population z-score.
- Bad: one tag of one file is compared. MASH-FRET always draws as many molecules as the tag has
  in this file; a file that draws fewer would need Tether to do the same, which it cannot.
- Neutral: the population fit is test code; Tether ships no histogram Gaussian fit (its overlay
  draws the idealized model, ADR-0101).

## More information

- MASH-FRET <https://github.com/RNA-FRETools/MASH-FRET>, GPL-3.0, commit
  `1a1f465983c59cb513459b10c19474fded317af5`: `source/mod_histogram_analysis/gauss_ana.m`,
  `getHist.m`, `getEqGauss.m`. Read, not vendored.
- [König2013] König et al., "BOBA FRET: bootstrap-based analysis of single-molecule FRET data",
  *PLoS ONE* 8(12):e84157 (2013), doi `10.1371/journal.pone.0084157` — already cited in
  `src/tether/analysis/histogram.py` and PRD §11.2.
- The frame-bootstrap demonstration on Tether's source and the seed sweep are in the pull request.
