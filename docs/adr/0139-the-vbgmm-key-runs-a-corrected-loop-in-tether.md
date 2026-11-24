<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0139 — The `vbgmm` key runs a corrected vbGMM loop in Tether, and tMAVEN's own loop stays for the published-fit check

- **Status:** accepted
- **Date:** 2026-11-23
- **Deciders:** bioedca (issue #1337 approved 2026-11-23 with "fix it in Tether": a Tether-side fix, not an upstream change and not a new pin; the agent chose how)
- **PRD anchor:** §4.3 (sidecar isolation), §7.4 (idealization), §8 NFR-VALID
- **Milestone:** none
- **Issue:** [#1337](https://github.com/bioedca/tether/issues/1337)

## Context and problem statement

[ADR-0138](0138-an-independent-scikit-learn-fit-is-the-reference-for-the-vbgmm-key.md) found that
the sidecar key `vbgmm` (tMAVEN `10f4230`, `modeler.run_vbgmm` → `gmm_vb.vb_em_gmm`) stops after
two iterations on `smd_4mol.hdf5`. Its lower bound falls at every step, the relative-change test
reads a negative change at its first check, and the returned fit is the k-means start after two
updates: state means up to 0.44 FRET from the independent scikit-learn fit. tMAVEN's own updates,
run to convergence, agree with that fit within the approved tolerance. The updates are sound; the
bound and the test that reads it are not. The maintainer decided the fix belongs in Tether. How,
without forking tMAVEN or changing the sidecar lock?

Reading `gmm_vb.calc_lowerbound` against the bound of a variational Gaussian mixture (Bishop
2006, §10.2.2, equations 10.71 to 10.77, written for one dimension; Blei, Kucukelbir and McAuliffe
2017, §3, for the same model and the monotone coordinate ascent) shows four departures:

1. The expected log likelihood uses `sk - (xbar - m)**2`; 10.71 has `sk + (xbar - m)**2`.
2. The Dirichlet posterior term uses `(a - 1) * E[ln pi]`, `a` being the Gamma shape; it needs
   `(alpha - 1)`.
3. The Normal posterior term uses `log(beta / 2 * pi)`, which is `log(beta pi / 2)`, for
   `log(beta / (2 pi))`. That is a constant offset and changes no step, but it is wrong.
4. The bound is computed after the E step, pairing the new responsibilities with the sufficient
   statistics of the previous ones in the data term and with the new ones in the assignment term.
   No single `q` has that bound.

A Monte Carlo estimate of the bound from `scipy.stats` densities catches each of 1 to 3 when it is
put back into the corrected function (the base test below; checked by mutation on 2026-11-23).

## Decision drivers

- **The sidecar lock does not change** ([ADR-0004](0004-pin-and-hold-dual-lock-isolation.md) isolation); NumPy and SciPy
  are already in it.
- **tMAVEN's start, priors, update equations, result container and serializer stay tMAVEN's**, so
  the fix changes the stop and nothing the issue did not find wrong.
- **Every other sidecar key runs tMAVEN unchanged** (the issue's scope).
- **A check that reproduces a fit published with tMAVEN keeps measuring tMAVEN** (ADR-0106).

## Considered options

1. **Replace `gmm_vb.outer_loop` in the sidecar child for the `vbgmm` key (chosen).**
   `_sidecar_runner.vbgmm_outer_loop` has tMAVEN's signature and return value, the same start
   and the same M step written in NumPy, and computes `vbgmm_lower_bound` after each M step. The
   fit stops when the relative increase falls below `modeler.converge` or at `modeler.maxiters`.
   `vb_em_gmm` looks `outer_loop` up in its module at call time, so `use_corrected_vbgmm`
   replaces that one name in a short-lived child that runs one fit.
2. **Patch only `calc_lowerbound`.** `outer_loop` is Numba-compiled with `cache=True` and binds the
   compiled bound at compile time; replacing the Python name changes nothing, and the pairing of
   departure 4 lives in the loop anyway.
3. **Upstream tMAVEN, or a newer pin.** Not the maintainer's decision for this issue.
4. **Tether's own vbGMM, start included.** More code and a second start to validate, for no gain
   the first option does not give.

## Decision outcome

Option 1. On the fixture (three states, one restart, seed 1076) the corrected fit's bound rises at
every step, from -5681.96 to -5292.80, and the fit runs to tMAVEN's 100-iteration cap
(`modeler.maxiters`; the last relative increase is 3.6e-8, above `modeler.converge` 1e-8). Its
means are -0.7906, 0.0815, 0.5560: 0.0221 from the reference means and 0.0062 from its fractions,
inside the 0.03 and 0.01 of ADR-0138, and 2e-4 from the fit the convergence test ends at
iteration 105 when the cap is raised. The cap is a tMAVEN preference and is left as it is.

`run(..., tmaven_vbgmm_loop=True)` keeps tMAVEN's own loop, early stop included. Only
`tests/_campos_chavez_hmm_child.py` passes it: the Two-state Campos-Chavez limit (ADR-0106)
measures the pinned tMAVEN against a fit published with tMAVEN, and moving that fit would change
what the limit measures. The flag is not reachable from `run_vbfret` or the command line. The
runner's status line names the loop that ran (`vbgmm_loop`: `tether`, `tmaven`, or `null` for
other keys).

### Consequences

- Positive: the `vbgmm` key fits to its convergence test or cap, and the sidecar test against the
  independent reference is a gate rather than a strict expected failure.
- Positive: the bound is checked without tMAVEN in the base matrix: against a Monte Carlo estimate,
  for monotonicity on the fixture, and for the stopping contract
  (`tests/test_vbgmm_corrected_loop.py`). The sidecar tests check both loop choices end to end.
- Negative: Tether now owns a copy of tMAVEN's vbGMM loop for this key. A tMAVEN pin that changes
  `vb_em_gmm`, `outer_loop`'s signature or its start must be read against it.
- Neutral: the `vbgmm` key is test-only (validation page) and the Campos-Chavez check keeps
  tMAVEN's loop, so no documented user result moves. The `vbgmm_vbhmm` key and the other keys are
  untouched.

## References

- Bishop C. M. (2006). *Pattern Recognition and Machine Learning*, §10.2, equations 10.70-10.77.
  Springer. ISBN 978-0-387-31073-2.
- Blei D. M., Kucukelbir A., McAuliffe J. D. (2017). Variational inference: a review for
  statisticians. *J. Am. Stat. Assoc.* 112:859-877. doi:10.1080/01621459.2017.1285773 (§3, the
  Bayesian mixture of Gaussians, and the monotone coordinate ascent).
- Bronson J. E., Fei J., Hofman J. M., Gonzalez R. L., Wiggins C. H. (2009). Learning rates and
  states from biophysical time series. *Biophys. J.* 97(12):3196-3205. doi:10.1016/j.bpj.2009.09.031
  (the vbFRET priors tMAVEN uses).
- Found with Consensus on 2026-11-23 (query "variational Bayesian Gaussian mixture evidence lower
  bound Normal-Gamma prior derivation"; Blei et al. 2017 ranked first). tMAVEN read in the
  installed sidecar package at `10f4230` (`controllers/modeler/gmm_vb.py`, `modeler.py`); SciPy
  1.17.1 in the cloud sidecar environment.
