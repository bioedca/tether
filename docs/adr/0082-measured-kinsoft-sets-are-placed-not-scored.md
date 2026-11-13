<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0082 — The measured kinSoft sets are placed among the published results, not scored

- **Status:** accepted
- **Date:** 2026-11-12
- **Deciders:** bioedca (data sets R4: C, D; advisory Q24: A; reproduce Q22: C; transcription B5: A;
  all recorded on issue #718)
- **PRD anchor:** §8 NFR-VALID(c), §9 M8, §11.2
- **Milestone:** M19

## Context and problem statement

[ADR-0048](0048-kinsoft-kinetics-oracle.md) compares Tether with the kinSoftChallenge on one
*simulated* level, where a ground truth exists. The same Zenodo record also holds five *measured*
sets: the two of the paper's Fig. 2 (ACTR binding NCBD, confocal, 19 traces, binned at 10 ms and at
1 ms) and the three of its Fig. 5 (SSB binding a DNA hairpin, TIRF, 100 ms frames, 134, 163 and 118
traces). The issue body counts 268, 326 and 236 and 40 traces; those counts include the macOS
`__MACOSX/._*` copies inside the archives. For measured data no ground truth exists. The paper
instead reports what each analysis of the challenge inferred.

How is Tether's result on these sets compared with the published results, and where do those
numbers come from?

## Decision drivers

- **No ground truth, so no verdict.** A spread of published results is not a tolerance: an
  analysis can be far from the others and still be the most correct one (Q24: A).
- **Every number has a source.** Each published number is traceable to a file of the paper by
  name and checksum, and the maintainer signs them off on the pull request (B5: A).
- **Compare like with like.** The analyses report models of different sizes, some with hidden
  states. Tether fits two states.
- **Tether's own drift is visible.** A change to Tether that moves its result must show.

## Considered options

1. **Read the numbers off the figures.** Rejected: Fig. 2 and Fig. 5 are plots; reading a plot
   loses digits and cannot be checked.
2. **Copy the kinetic models from the paper's Supplementary Data (chosen).** Supplementary Data 6,
   7, 10, 11 and 12 are spreadsheets with one sheet for each submitted model: FRET efficiencies, the
   rate matrix (row = start state, column = end state, s⁻¹; Supplementary Data 4) and the
   uncertainties. Each file is byte-identical to the matching table of the paper's Source Data.
3. **Compare each rate constant one to one.** Rejected for Fig. 5: in each set, three of the
   two-FRET-state models carry hidden states, so their rate constants do not match a 2-state fit.
4. **Compare the exit rate of each FRET class (chosen).** For every model, the rate of leaving the
   low-FRET class and the high-FRET class, `1 / mean residence time`. The paper compares the Fig. 5
   models through the residence times too (cumulative dwell-time distributions).

## Decision outcome

Chosen options: **2 and 4**.

- **Published side.** `tests/reference_data/kinsoft_published_results.json` copies the models
  unchanged: every sheet for Fig. 2, and for Fig. 5 the two-FRET-state sheets (`_FS_2`), the models
  the paper's Fig. 5 compares. Two models are kept in the file but left out of the comparison, with
  the reason: one reported no FRET efficiencies, one no rate matrix. The five spreadsheets are
  `download` entries of the manifest ([ADR-0073](0073-one-manifest-for-reference-data-sets.md)), so
  the weekly job verifies their checksums against the publisher's copy and then compares each
  copied number with its cell.
- **Class exit rate.** The states of a model are put in two classes at the largest gap in FRET E.
  With the generator `Q` of the model and its stationary distribution `π`, the class `A` is entered
  with the flux `e_j ∝ Σ_{i∉A} π_i Q_ij`, and its mean residence time is `e · (−Q_AA)⁻¹ · 1`. For two
  states this is `1/k12` and `1/k21`.
- **Tether side.** The tMAVEN sidecar fits a 2-state consensus VB-HMM with seed 0
  ([ADR-0075](0075-seeded-ebfret-parity-gate.md) makes a seeded fit repeatable), and the exit rate of
  each class is `1/⟨τ⟩` of the pooled Viterbi dwells, the estimator of ADR-0048. The result is
  frozen in the same file as `tether_measured`.
- **Report, never a verdict.** The weekly `reference-data` job downloads each set, fits it, and
  prints Tether's rate beside the published minimum, median and maximum. No assertion is made on
  that position. The job does fail when Tether's fit moves from its frozen result (relative 1e-4,
  dwell counts exact): that is drift of Tether, which the M19 suite exists to show, and the job
  itself never gates a merge. To fit, the job now restores the sidecar environment beside the base
  one.

### Consequences

- Good: each published number is a copy of a named sheet of a checksummed paper file, and a test
  derives the compared quantity from it, so the copy and the derivation can each be checked. The
  weekly job reads the downloaded spreadsheets with the standard library and compares every copied
  number with its cell.
- Good: `docs/validation.md` shows the ten rates beside the published ranges, and a required test
  keeps the table equal to the file.
- Bad: the weekly job takes longer, because it restores a second environment.
- Bad: the comparison uses two-state models only. The paper's models with three or more FRET states
  (Supplementary Tables 2–4, Supplementary Figs. 5 and 6) are not compared.
- Neutral: on 2026-11-12 Tether was inside the published range for nine of the ten rates; the high-
  FRET class of Fig. 5 set 2 was above it. That is a measurement, not a failure.

## References

- Götz M, Barth A, Bohr SS-R, *et al.* A blind benchmark of analysis tools to infer kinetic rate
  constants from single-molecule FRET trajectories. *Nat Commun* **13**:5402 (2022),
  [doi:10.1038/s41467-022-33023-3](https://doi.org/10.1038/s41467-022-33023-3) (CC-BY-4.0),
  PMC9474500; Supplementary Data 4, 6, 7, 10, 11, 12 and the Source Data archive, retrieved
  2026-11-12.
- kinSoftChallenge data: Götz M & Schmid S, Zenodo
  [doi:10.5281/zenodo.5701310](https://doi.org/10.5281/zenodo.5701310) (CC-BY-4.0); the measured
  data of Fig. 2 from B. Schuler (Zosel F *et al.*, *PNAS* **117**:13480, 2020) and of Fig. 5 from
  M. Schlierf.
- Issue [#718](https://github.com/bioedca/tether/issues/718), part of
  [#695](https://github.com/bioedca/tether/issues/695).
