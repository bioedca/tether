<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0106 — The tMAVEN fits of Campos-Chavez et al. 2024 are an advisory reference for the sidecar fits

- **Status:** proposed
- **Date:** 2026-11-15
- **Deciders:** bioedca (interview of 2026-11-11 on #757: S1 A to G, S2 A, S3 A, S4 B, S5 A, C1 C,
  C2 B, C3 A, C4 B, C5 C; the two-state sub-population is tMAVEN's plain vbGMM, and the
  `vbgmm_vbhmm` key is reported, decision of 2026-11-15; tMAVEN 0.2.0 and 0.2.3 run from GitHub
  in a separate environment, approval of 2026-11-15)
- **PRD anchor:** §7.4 (idealization), §8 NFR-VALID, NFR-FIXTURES, §11.2
- **Milestone:** M19
- **Issue:** [#757](https://github.com/bioedca/tether/issues/757)

## Context and problem statement

Campos-Chavez et al. (*Nat Commun* 15:6592, 2024, doi:10.1038/s41467-024-50885-x) fitted each
smFRET sample with tMAVEN. The Source Data archive (manifest id `campos2024-source-data`,
CC BY-NC-ND 4.0, [ADR-0083](0083-unchanged-copies-of-cc-by-nc-nd-reference-data.md)) holds the
trace matrices, a FRET histogram sheet and a transition density (TDP) sheet for each fit, and the
count matrix tMAVEN wrote for the five global fits (`Kmatrix/*.txt`). #757 asks Tether's sidecar
(tMAVEN `10f4230`) to fit the same traces again and to hold each fit to the published one. Seven
fits are in the article: the consensus vbHMM of five samples (wild type, ΔNCCA, ΔDiscriminator,
Linker and RAG mutants) and the two sub-populations of the ΔNCCA sample (Fig. 4).

Reading the article, the archive and three tMAVEN versions (2026-11-15) showed:

1. **Which frames each fit read.** The histogram sheet of a fit pools the FRET values of the
   frames it read. Each value is in exactly one run of frames of one molecule of the trace matrix
   (a run of one frame is not placed). The windows found this way give 348, 264, 164, 192, 72, 197
   and 197 molecules, the numbers of the article. Each window of a sub-population lies inside
   the window of its molecule in the whole ΔNCCA fit, but the two sub-populations do not split
   that fit: 192 + 72 = 264, yet 26 molecules are in both (the Two-state window is a shorter run
   of the same frames) and 26 are in neither. The fits below reproduce the published values with
   these windows, so the windows are the frames the authors fitted.
2. **The published values.** The state means are the distinct levels of the TDP sheet; the count
   matrix gives the populations (its row sums) and the transitions; the TDP sheet gives the
   transitions between each pair of levels. The sub-populations have no count matrix.
3. **The settings.** The Methods ("Transition rates and lifetime analyses", paragraph 2) give the
   priors of the consensus fits: α 1.0, β 0.25, π 1.0, a 2.5, b 0.01, with b 0.4 for the Linker
   and 0.2 for the RAG mutant. They name the composite HMM with K-means for the One-state
   population and vbGMM for the Two-state population, with no priors; those take the tMAVEN 0.2.3
   defaults (C4: B), which equal those of `10f4230`.
4. **Five fits are reproduced at the pin.** Wild type, ΔNCCA, ΔDiscriminator and the two
   sub-populations agree with the published means to 2.8e-3 or better; the count matrices of the
   three global ones agree to 1.5 % (off-diagonal, L1) or better. The consensus vbHMM starts from
   data percentiles, so at the pin its fit does not depend on the seed: 11 seeds agree to 2e-8 on
   these three fits and to 1.2e-6 on Linker and RAG.
5. **Two fits are not reproduced by `10f4230` or by 0.2.3.** For the Linker and RAG mutants both
   versions converge to another optimum (means up to 0.39 and 0.36 from the published ones); in
   0.2.3 the seeds move those means by up to 0.020 and 0.017.
   tMAVEN 0.2.0 (`8beaeb1`, 2023-08-14) starts the consensus fit from K-means, which depends on the
   seed: 3 of 11 seeds give the published Linker count matrix to 2e-4, and 2 give the RAG means
   to 1.1e-4. 0.2.0 also gives the ΔDiscriminator count matrix to 8e-10 and the wild-type means
   to 1.6e-7. Commit `62422f9` (0.2.3), which S4 names, is dated 2024-10-24, after the article;
   the fits were probably made with 0.2.0 or a build near it.
6. **The `vbgmm_vbhmm` key is not the published Two-state fit.** It selects one state on these
   traces. The published three-state fit is tMAVEN's plain `run_vbgmm`, which the sidecar has no
   key for.

## Decision drivers

- **A reference value comes from the reference**: the published values are read from the archive
  at test time and are never committed (ADR-0083: unchanged copies only).
- **No limit is chosen to make a test pass**; a limit records a measurement, and the maintainer
  accepts it (a new scientific claim, PRD §11.2).
- **The pinned tMAVEN does not change in this issue** (out of scope), so a fit the pin does not
  reproduce cannot be made to pass by a change of tMAVEN.
- **The required matrix makes no network call.**

## Considered options

1. **Gate all seven fits at the pin, each metric at its measured deviation from the published
   values; record the seed and version evidence (chosen).** The test fails when the sidecar fit
   moves further from the published values than its limit; the documentation says which fits
   reproduce the article and which do not.
2. **Gate the five reproduced fits only and report Linker and RAG.** Rejected: a change of the
   sidecar that moves those two fits further from the article would pass silently, and S1 asks
   for all seven.
3. **Fit Linker and RAG with tMAVEN 0.2.0 in the test.** Rejected: it tests an old tMAVEN, not
   Tether's sidecar, and the K-means start makes the result depend on the seed.
4. **Hold the two sub-populations with the sidecar keys `kmeans_vbhmm` and `vbgmm_vbhmm`.** Kept
   for the One-state population (`kmeans_vbhmm` reproduces it); rejected for the Two-state
   population by the maintainer (finding 6).

## Decision outcome

Chosen option 1.

- **The settings** are in `tests/reference_data/campos_chavez_2024_hmm.json`: for each fit the
  sample, the sheets, the model key, the number of states, each prior and setting with its source
  in the article (or the record that the tMAVEN 0.2.3 default applies), and the windows of
  finding 1 (frame indexes only, no value of the archive).
- **The fit.** `tests/_campos_chavez_hmm_child.py` runs Tether's `_sidecar_runner.run` with the
  priors of the settings file set on tMAVEN's `maven_class`, and adds a test-only `vbgmm` key for
  `run_vbgmm`. With tMAVEN 0.2.x it drives the older `run_fret_*` routines with the same SMD,
  priors and seeding.
- **The limits** are in `schema/campos_chavez_hmm_limits.json`, measured over seeds 0 to 9 and 757
  at the pin: the largest deviation of each metric, rounded up at the second significant digit (a
  required-matrix test enforces the rule). Four metrics: the largest state-mean difference, the
  largest population difference and the off-diagonal L1 difference of the count matrix (global
  fits only), and the L1 difference of the transitions between each pair of levels.
- **The tests.** Required matrix (`tests/test_campos_chavez_hmm.py`, unmarked): every setting has
  a source, the windows are well formed, the comparison on built data, the rounding rule, and
  that `docs/validation.md` shows each limit. Weekly `reference-data` job (`reference_download`):
  each window holds the published histogram values, and each fit, seed 757, is inside its limits;
  the `vbgmm_vbhmm` key on the Two-state population is printed, never gated.
- **Marker.** The issue named the `sidecar` and `large` markers. The data are a `download` entry
  and the marker contract keeps `sidecar` for `tests/test_*sidecar*.py`, so the fits run under
  `reference_download` in the weekly job, which restores the sidecar env (as for the measured
  kinSoft sets, [ADR-0082](0082-measured-kinsoft-sets-are-placed-not-scored.md)), and skip
  without `TETHER_SIDECAR_PYTHON`.
- **Stored models** (step 4, `tests/test_campos_chavez_hmm_stored.py`, `workstation`): each
  private trace file is matched to a sample by its donor traces; each stored model and model
  summary is compared with the published values and with the fresh fit (`fresh` in the limits
  file). The result file holds numbers under neutral ids only (S5: A, ADR-0071).
- **#758** reads the count matrices of the fresh fits from `fresh` in the limits file.

### Consequences

- Good: five published fits of measured data are a reference for the sidecar's consensus vbHMM,
  composite K-means vbHMM and vbGMM, read directly from the authors' Source Data.
- Good: a change of the sidecar that moves any of the seven fits further from the article than its
  limit fails the weekly job.
- Bad: the Linker and RAG limits measure a disagreement (up to 0.4 in a mean), so they say nothing
  about agreement with the article. Each limit is an upper bound: a tMAVEN update that moves these
  fits nearer to the published ones passes, and shows only in the printed numbers; the limits then
  need a new measurement and the maintainer's acceptance.
- Bad: each limit is the largest deviation over the seeds, rounded up at the second significant
  digit, so its headroom over the measurement is small (0.08 % to 6.6 % for the global fits); a
  change of the numerical stack can fail them, and the answer is a new measurement, not a wider
  limit (PRD §11.2).
- Neutral: the `kmeans_vbhmm` and `vbgmm_vbhmm` keys stay undocumented as user features; a `vbgmm`
  key for the sidecar is a separate proposal.

## More information

- tMAVEN <https://github.com/GonzalezBiophysicsLab/tmaven>: `10f4230b6d13c6d2ad67b05d801696b4a40eff4a`
  (the pin), `62422f9` (0.2.3) and `8beaeb1` (0.2.0), each run with the trace matrices only, in a
  separate environment. Read, not vendored.
- The measurement per version and seed is in the limits file and in the pull request.
- `ErrorCalculations_CodeAndSourceData/FractionOfClosedState` of the archive gives the error of
  the closed-state fraction (±0.013 to ±0.019) from a double-Gaussian fit of other traces; it is
  context for the population limits, not a gate.
