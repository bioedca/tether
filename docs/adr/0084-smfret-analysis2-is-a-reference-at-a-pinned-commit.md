<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0084 — smfret-analysis2 at a pinned commit is the reference for the histogram and the cross-correlation

- **Status:** accepted
- **Date:** 2026-11-12
- **Deciders:** bioedca (interview of 2026-11-11, Q10 A, Q11 A, Q12 A and B, Q13 A, Q18 A)
- **PRD anchor:** §1 (the two model-free lenses), §7.7 FR-ANALYZE, §8 NFR-VALID, N4
- **Milestone:** M19

## Context and problem statement

PRD §1 makes two model-free lenses first-class: the apparent-E histogram and the donor–acceptor
cross-correlation. Both come from the laboratory's MATLAB scripts, smfret-analysis2, and before
issue [#709](https://github.com/bioedca/tether/issues/709) no Tether test compared a result with
those scripts. The public repository `vafabakhsh-lab/smfret-analysis2` (GPL-3.0) carries test
data: 76 trace files and the outputs `total_FRET.csv` and `total_XC.csv`. Which version of the
scripts is the reference, where do its files live, and how does a test compare two results whose
conventions differ?

## Decision drivers

- A reference must be one fixed, public version that anyone can fetch and check.
- The comparison must run in the required matrix, on three operating systems, with no MATLAB.
- Tether's own conventions stay: §7.7 specifies the biased Pearson cross-correlation, and the
  histogram code is shared by every other caller.
- A difference of convention must be converted exactly, not hidden in a tolerance.
- The product gets no importer for an old laboratory format (N4).

## Considered options

1. **The laboratory's local 2025 scripts as the reference.** Rejected (Q10): they have no public
   commit; they stay a source of requirements only (#710 uses them through a `files` pin).
2. **Regenerate the outputs with the MATLAB harness of [ADR-0074](0074-matlab-reference-outputs-come-from-pinned-runs.md).**
   Not needed: the pinned commit's own scripts reproduce the committed outputs exactly (below).
3. **The public repository at the commit of `scripts/reference/matlab/pins.toml`, its test files
   committed verbatim, compared through exact conversions.** Chosen.

## Decision outcome

Chosen option 3.

- **The pin.** smfret-analysis2 at `121634e9b533eb3a922fbfe9bc1e7715a9a6a6b4` (2022-05-20), the pin
  that ADR-0074 already set. A test asserts that every fixture entry names that commit, so the two
  cannot drift apart.
- **The files.** 78 files of `test-data/traces/` (the 76 `1 tr<N>.dat` traces, `total_FRET.csv`,
  `total_XC.csv`) are committed verbatim under `tests/fixtures/smfret_analysis2/`, tier `git` of
  [ADR-0073](0073-one-manifest-for-reference-data-sets.md), one manifest entry each with size and
  SHA-256, `-text` in `.gitattributes` and excluded from the whitespace hooks. The largest file is
  242 kB; all 78 are 7.5 MB.
- **The licence.** The repository gives the GPL version 3 text and no "or later" statement, so the
  files are `GPL-3.0-only` (REUSE override, NOTICE, PROVENANCE), not Tether's `-or-later`.
- **Which outputs.** The `test-data/traces/` pair is what this commit's `create_histogram.m` wrote
  from those traces with every trace kept (`skipped-traces.csv` is empty). A transcription of the
  script reproduces both: the histogram to 4.9e-17, the cross-correlation to 3.6e-16. The files in
  `test-data/ground-truth/` (no header line, a four-column cross-correlation, a different `raw`
  column) are not reproduced by this commit's scripts and are not used.
- **A reader in the tests only** (Q18 A): `tests/_sa2_traces.py` reads columns 2 and 3 (donor,
  acceptor) of a trace file and the two outputs, and refuses any other shape.
- **Exact conversion 1, bin edges.** MATLAB `hist(y, centres)` puts its edges at the midpoints and
  closes each bin on the right, `(a, b]`, with open first and last bins. `np.histogram` in
  `apparent_e_histogram` closes each bin on the left. A sample is binned differently only when it
  sits exactly on an edge; the test moves each such sample one bin down and asserts their number
  (one here, at 0.375 in `1 tr207.dat`). The script's per-trace normalization is Tether's
  per-molecule equal weight. Without the edge rule the difference is 7.5e-6, which is the number
  the first check of #709 reported.
- **Exact conversion 2, cross-correlation.** `calculate_cross_corr.m` divides the sum at lag `k` by
  `N - k` and by MATLAB's `std(donor) * std(acceptor)`, and MATLAB's `std` divides by `N - 1`.
  Tether divides by `N` and the population σ (Q13 A keeps it). The exact factor from Tether's value
  to the script's is `(N - 1) / (N - k)`; #709's text named `N / (N - lag)`, which leaves an error
  of 1/N (up to about 1 % for the shortest trace, 94 frames). The script correlates
  `donor(t)` with `acceptor(t + k)`, which is Tether's lag `-k`. A test asserts that the
  `N / (N - k)` factor alone does not reach the file, so the reason stays visible.
- **The bound.** Agreement to 1e-14 absolute: the files write 15 significant digits, and the rest is
  float64 round-off of the FFT and of the sums (measured 4.4e-16 and 3.1e-16 on Linux). One sample
  of one trace moving one bin changes that bin by more than 4e-6.

### Consequences

- Good: both PRD §1 lenses are checked against the laboratory's own scripts in the required
  matrix, with the source commit and the bytes pinned.
- Good: the two differences of convention are written down as exact conversions, so a reader can
  see why Tether's numbers differ from the script's, and a change of either fails a test (the pull
  request records the two mutant demonstrations).
- Bad: 7.5 MB of plain-git data in every clone, and 78 manifest entries, because the manifest has
  one entry per file.
- Neutral: the MATLAB task functions of ADR-0074 for smfret-analysis2 still have no real run;
  #709 did not need one. Their CSV quoting fix (#840) is checked by reading until that run.
- Out of scope: the successive-frame cloud (#710), the smoothed and filtered columns, the heat map,
  trace selection and background subtraction, and the script's second normalization.
