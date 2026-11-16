<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0101 — MASH-FRET's stored analyses are an exact histogram reference and a measured state reference

- **Status:** accepted
- **Date:** 2026-11-14
- **Deciders:** bioedca (histogram and populations, state sequences and number of states with a
  measured limit, and transition rates, R3 B C D; a different algorithm gets a measured, frozen
  limit, Q7 A; a reference check becomes an oracle with a limit in a schema file, an ADR and a row
  in `docs/validation.md`, Q36 A — interview of 2026-11-11; the two state limits frozen and
  populations and rates reported, decision of 2026-11-14 on #712)
- **PRD anchor:** §7.7 FR-ANALYZE, §7.4 (idealization), §8 NFR-VALID, NFR-FIXTURES, §11.2
- **Milestone:** M19
- **Issue:** [#712](https://github.com/bioedca/tether/issues/712)

## Context and problem statement

[ADR-0087](0087-mash-fret-is-a-reference-for-corrected-fret.md) made the nine MASH-FRET project
files of Zenodo record 24104528 (CC-BY-4.0) a reference for Tether's leakage correction and
γ-corrected FRET. The same files also keep MASH-FRET's own analyses: for each molecule tag, the FRET
histogram of the Histogram analysis and its Gaussian fit (`prmThm`), the per-trace state sequences
(`FRET_DTA`), and the dwell table of the Transition analysis (`prmTDP`). #712 asks which of these
can test Tether, and how. Reading the eight files of the generation that `tests/_mashfret.py` reads
(2026-11-14) showed:

1. **The histograms are stored with the bins that made them.** `getHist.m` bins the γ-corrected
   FRET of the `denoise` intensities over the included frames with MATLAB's `hist` at the centres
   `low:bin:high` and, with "overflow" off (every stored histogram), removes the first and the last
   bin. The 37 stored FRET histograms are the counts, not a fit.
2. **Every trace was idealized with per-trace vbFRET, 1 to 5 states, 5 restarts**
   (`prmTT{m}{4}`, `getDiscr.m` method 2). Tether's idealization is tMAVEN's consensus VB-HMM, a
   different algorithm, so the state sequences cannot agree exactly.
3. **No file stores a fitted rate.** The kinetic results (`kin_res`) are empty in every file. The
   dwell table of the chosen clustering (`clst_res{1}.clusters{J}`, one row for each dwell that ends
   in a transition) is stored, for five tags in three files.
4. **The populations are Gaussian-fit areas** (`gauss_ana.m`, the mean and SD over 100 bootstrap
   samples), not state occupancies.

## Decision drivers

- **A reference value comes from the reference tool**, never from a re-implementation of it.
- **No limit is chosen to make a test pass** (#712); a limit records a measurement, and the
  maintainer accepts it (a new scientific claim).
- **The required matrix makes no network call**, and a plain fixture is at most 512 KiB.

## Considered options

1. **Exact histogram gate; measured, frozen state gates; populations and rates reported (chosen by
   the maintainer, 2026-11-14).**
2. **Report everything but the histogram.** Safe, but it leaves the state comparison, which is a
   real comparison of two idealizations of the same traces, without teeth.
3. **Also freeze the rate ratio.** Rejected as a proposal: the files hold MASH-FRET's dwell times,
   not its rates, so the "MASH-FRET rate" would be computed here (`1 / mean`), which the first
   driver rules out as a reference value.
4. **Tether's idealization through tMAVEN's per-trace vbHMM** (the test-only child of
   [ADR-0090](0090-measured-vbfret-paths-are-a-dwell-reference.md)), the closer analogue of
   per-trace vbFRET. It agreed better on Path A (0.94 of frames), but it is not the idealization
   Tether runs, so it would test tMAVEN rather than Tether.

## Decision outcome

Chosen option 1; the maintainer signed off the two frozen limits on 2026-11-14.

- **The data.** A second committed extract of the smallest project,
  `tests/fixtures/mashfret_ed135e_30mm_path_a.mat` (204,676 B, `scripts/make_mashfret_fixture.py`,
  values unchanged): the 17 molecules with tag 12 "Path A 0.2 +- 0.3", their `denoise`
  intensities, `FRET_DTA`, `bool_intensities`, `coord_incl`, `molTag` and `prmTT`, and that tag's
  row of `prmThm` and `prmTDP` (the other rows empty, so the indexes are those of the project). A
  `git` entry of the manifest ([ADR-0073](0073-one-manifest-for-reference-data-sets.md)); the weekly
  workstation run makes it again from the archive and compares it byte for byte.
- **Histogram: exact, required matrix.** Tether's `apparent_e_histogram` of Tether's
  `corrected_fret` with MASH-FRET's γ, with the stored bin count over
  `(first centre − bin/2, last centre + bin/2)`, equals MASH-FRET's counts bin for bin
  (`tests/test_mashfret_analysis.py`): on the extract in the required matrix, and on all 37 stored
  FRET histograms of the archive on the workstation. γ = 1 fails it.
- **States: measured gates, sidecar job.** `run_vbfret(model_type="vbconhmm_modelselection",
  seed=712)` on an SMD of `γ·I_D` and `I_A` (so tMAVEN's `A / (A + D)` is MASH-FRET's FRET)
  (`tests/test_mashfret_analysis_sidecar.py`). Two gates, in `schema/mashfret_analysis_limits.json`
  with their measurements and the rule that ties them (rounded outward at the second significant
  digit, enforced by an unmarked test):
  - frame agreement (each frame's Tether level matched to the nearest MASH-FRET state of its
    molecule): measured 0.848, **at least 0.84**;
  - mean absolute difference in the number of states of a trace: measured 0.353 (11 of 17 traces
    equal, 5 one fewer, 1 one more), **at most 0.36**.
- **Populations and rates: printed, not gated.** Tether's state fractions (0.333, 0.384, 0.283)
  against MASH-FRET's Gaussian populations (0.415, 0.246, 0.340, each ±0.11); Tether's exit rates
  (`pooled_exit_rates`) against `1 / mean` of MASH-FRET's stored dwell times for each matched
  transition cluster (ratios 1.04 to 1.77).

### Consequences

- Good: Tether's FRET histogram has an exact external reference on measured data, across eight
  project files and four MASH-FRET builds.
- Good: a change of Tether's idealization that moves its states on these traces fails the sidecar
  job instead of passing silently.
- Bad: the state gates hold only on the pinned sidecar build and seed; a tMAVEN or NumPy update
  that moves them needs a new measurement and the maintainer's acceptance (PRD §11.2), not a wider
  limit.
- Bad: no rate check against MASH-FRET exists, because the record holds no MASH-FRET rate. A MASH-FRET
  kinetic fit would need a run of MASH-FRET on the workstation
  ([ADR-0074](0074-matlab-reference-outputs-come-from-pinned-runs.md)).
- Neutral: Tether's exit rates on Path A are faster than MASH-FRET's dwell times give; tMAVEN's
  consensus model splits some of the dwells that per-trace vbFRET keeps whole.

## More information

- MASH-FRET <https://github.com/RNA-FRETools/MASH-FRET>, GPL-3.0, commit
  `1a1f465983c59cb513459b10c19474fded317af5`: `source/mod_histogram_analysis/getHist.m`,
  `gauss_ana.m`, `ud_thmPlot.m`; `source/mod-trace-processing/dwell-time analysis/getDiscr.m`;
  `source/mod_transition_analysis/clustering/clustTrans.m`, `TDP/getTDPmat.m`. Read, not vendored.
- The data record and its checksums: [ADR-0087](0087-mash-fret-is-a-reference-for-corrected-fret.md).
- The full-archive state measurement (a report, not a gate) is in the pull request.
