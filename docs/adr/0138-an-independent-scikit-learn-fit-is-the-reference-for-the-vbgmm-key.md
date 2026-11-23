<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0138 — An independent scikit-learn fit is the reference for the `vbgmm` key, and the pinned tMAVEN fails it

- **Status:** proposed (the maintainer signs off on the comparator and the tolerance in the pull request)
- **Date:** 2026-11-23
- **Deciders:** bioedca (issue #1302 approved 2026-11-23; the agent proposes the comparator and the tolerance, the maintainer signs off before merge)
- **PRD anchor:** §7.4 (idealization), §8 NFR-VALID, NFR-FIXTURES, §11.2
- **Milestone:** none
- **Issue:** [#1302](https://github.com/bioedca/tether/issues/1302)

## Context and problem statement

The sidecar key `vbgmm` (#1076, [ADR-0106](0106-campos-chavez-tmaven-fits-are-an-advisory-reference.md))
runs tMAVEN's plain variational Bayesian Gaussian mixture, `modeler.run_vbgmm`, on the pooled FRET
of every molecule. The live test on the committed four-molecule fixture `smd_4mol.hdf5` checks the
dispatch, the state count, the model type, the shapes, the paths and that one seed gives one fit.
Every one of those passes for a wrong fit: both fits go through `run_vbfret`, and the path check
uses the driver's own conversion. What independent numbers should the fit agree with, and how
closely?

Fitting the fixture (three states, one restart, seed 1076, tMAVEN `10f4230`) showed:

1. **The fit stops after two iterations.** `vb_em_gmm` stops when the relative change of its lower
   bound is below `modeler.converge` (1e-8). The bound it computes goes down at every step
   (-8462.9, -8548.2, -8581.5), so the change is negative at the first test and the loop ends. A
   coordinate-ascent variational update cannot lower a correct evidence lower bound (Blei,
   Kucukelbir and McAuliffe 2017, §2.4 and §3, doi:10.1080/01621459.2017.1285773), so the bound,
   not the fit, is at fault; the returned model is the k-means start after two updates.
2. **Run to convergence, tMAVEN's own updates find another fit.** With the convergence test off,
   `vb_em_gmm` reaches a fixed point (20 000 and 60 000 iterations agree to every printed digit):
   means -0.791, 0.081, 0.556 against the stopped -0.555, 0.185, 1.007.
3. **An independent implementation agrees with the converged fit.** scikit-learn 1.9.0 (the base
   lock) `BayesianGaussianMixture`, with a finite Dirichlet weight prior and tMAVEN's vbGMM priors
   mapped onto its Normal-Wishart prior (`beta` 0.25 → `mean_precision_prior`; Gamma(`a` 0.1, `b`
   0.01) → `degrees_of_freedom_prior` 2a = 0.2 and `covariance_prior` 2b = 0.02; `pi` 1 →
   `weight_concentration_prior`), on the same pooled and clipped FRET, gives means -0.768, 0.082,
   0.563. Nine different starts (k-means, k-means++, random points; three seeds each; ten starts
   each) reach the same optimum. Its means are within 0.0225 of tMAVEN's converged fit and its
   state fractions within 0.0065.
4. **That residual is the mean prior.** tMAVEN centres each state's mean prior on that state's
   k-means start; scikit-learn centres every state's on the data mean. The same updates with the
   shared data-mean prior, started from tMAVEN's converged point, reach the scikit-learn values
   within 5e-5.

The data preparation is rebuilt from the SMD file without tMAVEN: FRET `A / (D + A)` of the raw
intensities in each molecule's `pre_list:post_list` window, then tMAVEN's clip (values outside
`[-1, 2]` or NaN replaced by uniform draws from a generator seeded with 666). Its output is the
data tMAVEN fits: with it, tMAVEN's own `vb_em_gmm` gives the sidecar result to every digit.

## Decision drivers

- **Independent means no shared code**: the comparator is maintained apart from tMAVEN and fits
  the same model with the same priors.
- **The sidecar lock does not grow** (ADR-0003 isolation): scikit-learn is in the base lock only.
- **No limit is chosen to make a test pass**; a limit records a measurement, and the maintainer
  accepts it (§11.2).
- **The pinned tMAVEN does not change in this issue** (out of scope).

## Considered options

1. **scikit-learn on the base side, committed numbers, a strict expected failure on the sidecar
   (proposed).** `tests/_vbgmm_reference.py` fits; `tests/reference_data/smd_4mol_vbgmm_sklearn.json`
   keeps the numbers, their provenance and the tolerance; `tests/test_vbgmm_reference.py` (base
   matrix) fits again and holds the committed numbers to 1e-6; the sidecar test compares the live
   fit and is marked `xfail(strict=True, raises=VbgmmDisagreement)`. Only a deviation over the
   tolerance is expected; any other failure fails, and a fit inside the tolerance (a fixed tMAVEN)
   is an XPASS that fails the job until the marker is removed.
2. **The same, as a hard gate.** Honest, but `sidecar / parity` is a required check and would stay
   red until a tMAVEN change that this repository does not control.
3. **A NumPy copy of tMAVEN's two updates.** It would agree to rounding, but it checks the
   defect, not a correct fit, and it is not independent.
4. **Hold the issue until tMAVEN is fixed.** Leaves the key with no numeric check at all.

## Decision outcome

Option 1, subject to the maintainer's sign-off. The tolerance: the largest absolute difference of
the state means (states sorted by mean) at most **0.03 FRET**, and of the state fractions (tMAVEN's
`frac`, the share of the responsibilities) at most **0.01**. Both are above the converged residual
(0.0225 and 0.0065, finding 4) and far below the stopped fit's deviation (0.44 and 0.38). Variances
are recorded and not gated, because their difference is the mean-prior convention of finding 4.

### Consequences

- Positive: the `vbgmm` key has an independent numeric check, and its present failure is stated
  in the test, the reference file and this record instead of being hidden by a self-comparison.
- Positive: a tMAVEN pin that converges turns the expected failure into a failing XPASS, so the
  marker cannot outlive the defect.
- Negative: the sidecar test records a disagreement rather than gating agreement until then.
- Neutral: the agreement of the Two-state Campos-Chavez fit with its published values (ADR-0106)
  is agreement between two runs of the same stopping rule; whether the published fit also stopped
  early is not established here.

## References

- Blei D. M., Kucukelbir A., McAuliffe J. D. (2017). Variational inference: a review for
  statisticians. *J. Am. Stat. Assoc.* 112:859-877. doi:10.1080/01621459.2017.1285773.
- Bronson J. E., Fei J., Hofman J. M., Gonzalez R. L., Wiggins C. H. (2009). Learning rates and
  states from biophysical time series. *Biophys. J.* 97(12):3196-3205. doi:10.1016/j.bpj.2009.09.031
  (the vbFRET priors tMAVEN uses).
- Verma A. R. et al. (2024). Increasing the accuracy of single-molecule data analysis using tMAVEN.
  *Biophys. J.* 123(17):2765-2780. doi:10.1016/j.bpj.2024.01.022.
- Found with Consensus on 2026-11-23 (queries "variational Bayesian Gaussian mixture model
  single-molecule FRET state identification vbFRET", "coordinate ascent variational inference
  evidence lower bound monotonically increases Gaussian mixture", "tMAVEN single-molecule FRET
  time series analysis software modeling"); scikit-learn behaviour read with Context7
  (`/scikit-learn/scikit-learn/1.9.0`) and in the installed 1.9.0 source
  (`_estimate_wishart_full`).
