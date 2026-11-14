<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0092 — Rate constants with Dirichlet bounds come from the count matrix, as in Campos-Chavez et al. 2024

- **Status:** accepted
- **Date:** 2026-11-14
- **Deciders:** bioedca (issue #758 approved on 2026-11-11, interview decisions S1: C and Q7: A;
  the new scientific claim is his to sign off on the pull request)
- **PRD anchor:** §7.7 FR-ANALYZE, §8 NFR-VALID, NFR-REPRO
- **Milestone:** M19

## Context and problem statement

Campos-Chavez et al., *Nature Communications* 15:6592 (2024), doi:10.1038/s41467-024-50885-x,
report rate constants with a lower and an upper bound (Supplementary Table 5). The bounds come
from the un-normalized transition count matrix of the tMAVEN consensus HMM. The Source Data
(committed unchanged by #755, ADR-0083) holds the script, `Kmatrix/CalculateKbounds.py`, five
count matrices (`Kmatrix/*.txt`) and the output that the script printed for each (`Kmatrix/*.out`).
Tether stores the count matrix with each fitted or imported model (`/idealization/{model}/tmatrix`)
and had no function that gives the bounds.

Which method does Tether use, what does it do outside the range of the published files, and how
does the test compare with the published output?

## What was found

- **The method of the script.** Each row of the count matrix `C` is a Dirichlet distribution.
  With `a0` the row sum: `p = C[i, j] / a0`, `var = p (1 − p) / (a0 + 1)`, `k = −ln(1 − p) / dt`,
  and the bounds are `k` at `p + √var` and at `p − √var`. The frame time is `dt = 0.1 s` (the
  script's comment: 10 frames per second). The script also prints the differences of the bounds
  from the rate.
- **The script does not run as committed.** One line (`tm = np.empty_like(counts)`) has one space
  of indent too many, so Python refuses the file (noted by #755). The `.out` files are the output
  of a working copy. The test therefore compares with the `.out` files and never runs the script.
- **The digits agree exactly.** A vectorized implementation with the same floating-point
  expressions prints, for all five files, every entry of all eight printed sections (the input,
  the variance with 12 decimals, the normalized matrix, the rates, the two bounds and the two
  differences with 5 decimals) identically. No tolerance is needed.
- **No published entry reaches a limit.** Over the five files, the smallest `p − √var` is
  8.4e-05 and the largest `p + √var` is 0.99914, so the clipping below changes no published value.
- **The diagonal is not a rate.** The script applies `−ln(1 − p)/dt` to the whole matrix, so it
  also prints the diagonal entries (for example 69.74 s⁻¹ for the first state of `Wt-Wt`). These
  are not rate constants.
- **Evidence for the relation between `p` and `k`.** Consensus search, 2026-11-14, query "rate
  constant from hidden Markov model transition probability k = −ln(1−p)/dt single-molecule FRET
  Dirichlet posterior uncertainty". Götz et al., *Nat Commun* 13:5402 (2022),
  doi:10.1038/s41467-022-33023-3, state that the transition matrix of an HMM "can directly be
  converted to kinetic rate constants". Kilic et al., *Cell Rep Phys Sci* 2:100409 (2021),
  doi:10.1016/j.xcrp.2021.100409, give the exact relation, the matrix exponential of the
  generator, and the entry-wise conversion as its approximation for kinetics slower than the frame
  rate. Bronson et al., *Biophys J* 97:3196 (2009), doi:10.1016/j.bpj.2009.09.031 (vbFRET, the
  base of tMAVEN's VB-HMM), place Dirichlet distributions on the rows of the transition matrix.

## Considered options

1. **The script's method, entry by entry (chosen).** It is what the paper reports, and the
   published files test it exactly.
2. **The matrix logarithm of the normalized matrix**, the exact generator of Kilic et al. Not the
   published method, so it cannot be tested against the published files; and its bounds need a
   different error propagation. Out of scope (#758 scope: no other method).
3. **Bootstrap bounds.** Out of scope (#758 names it as a non-goal).

## Decision outcome

Chosen option: **option 1**.

- **Where.** `tether.analysis.rate_bounds`: `dirichlet_rate_bounds(counts, dt)` is the array core
  and `model_rate_bounds(project, model_name, dt=...)` reads the `tmatrix` of a stored model. Both
  return a `RateBounds` with the full `N × N` matrices, the diagonal kept for the comparison with
  the published output and documented as not a rate.
- **Defined results outside the published files.** A count of zero gives `p = 0` and a rate and
  bounds of `0`. A row with a sum of zero gives a row of `NaN`, because no probability is defined.
  A probability of 1 gives `var = 0` and a rate and bounds of `+inf`. A bound probability is clipped
  to `[0, 1]`: an upper bound at `p + √var ≥ 1` is `+inf`, and a lower bound at `p − √var < 0` is
  `0`, because a rate constant is not negative and the logarithm of a negative value is not
  defined. The script would give `NaN` and a negative rate there.
- **Refusals.** A matrix that is not square, is empty, holds a non-finite or negative value, or is
  normalized (every row sums to 1 within 1e-9) is refused with a `ValueError`; the message for a
  normalized matrix says that the variance needs the counts. A frame time that is not finite and
  positive is refused. A model that stores no count matrix (threshold, k-means) is refused.
- **Provenance.** `model_rate_bounds` stamps the result (`RateBounds.provenance`) with the method,
  the app version, the time, the parameter `dt`, the project file name, and the model's name,
  type, app version and creation time. It only reads the project, so the HDF5 schema is unchanged.
  Writing the result into the project is a separate change: it is not part of #758.
- **The test.** `tests/test_rate_bounds.py` reads the five `.txt` and `.out` files through the
  manifest of ADR-0073 (each SHA-256 checked) and compares the formatted digits of each of the
  eight sections, entry by entry, as strings. It also covers each defined result, each refusal and
  the project entry point.
- **The docs.** PRD §7.7 has the analysis, the PRD References have [CamposChavez2024], and
  `docs/analysis/rate-bounds.md` gives the formula, the cases and the citation.

### Consequences

- Good: a user gets the numbers of the paper for a new data set from Tether, and the agreement is
  exact on every published value.
- Good: the cases that the script leaves undefined now have one result each, with a test.
- Neutral: the entry-wise conversion is an approximation for kinetics that are not slow against the
  frame rate (Kilic et al. 2021). This is a limit of the published method, and the docs page names
  the method as the paper's.
- Neutral: the diagonal entries are returned although they are not rates; the docs say so.

## Links

- Issue [#758](https://github.com/bioedca/tether/issues/758), part of the plan in
  [#695](https://github.com/bioedca/tether/issues/695); the data from #755 (ADR-0083).
- Campos-Chavez E, Paul S, Zhou Z, Alonso D, Verma AR, Fei J, Mondragón A. Translational T-box
  riboswitches bind tRNA by modulating conformational flexibility. *Nature Communications* 15:6592
  (2024). [doi:10.1038/s41467-024-50885-x](https://doi.org/10.1038/s41467-024-50885-x). Source Data
  `41467_2024_50885_MOESM4_ESM.zip`, CC BY-NC-ND 4.0.
- Götz M et al. *Nature Communications* 13:5402 (2022).
  [doi:10.1038/s41467-022-33023-3](https://doi.org/10.1038/s41467-022-33023-3).
- Kilic Z, Sgouralis I, et al. *Cell Reports Physical Science* 2:100409 (2021).
  [doi:10.1016/j.xcrp.2021.100409](https://doi.org/10.1016/j.xcrp.2021.100409).
- Bronson JE, Fei J, Hofman JM, Gonzalez RL Jr, Wiggins CH. *Biophysical Journal* 97:3196 (2009).
  [doi:10.1016/j.bpj.2009.09.031](https://doi.org/10.1016/j.bpj.2009.09.031).
