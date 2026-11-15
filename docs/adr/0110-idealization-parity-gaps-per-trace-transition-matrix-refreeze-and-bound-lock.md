<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0110 — Idealization parity: per-trace vbFRET, the transition-matrix term, the re-freeze on the pinned build, and a lock on the frozen bounds

- **Status:** accepted
- **Date:** 2026-11-15
- **Deciders:** bioedca (the acceptance criteria of #545, approved 2026-11-15; "Split into three
  PRs", 2026-11-15)
- **PRD anchor:** §7.4, §9.2 (the idealization-parity gate), §9.6, §11.2, §8 NFR-VALID (b)
- **Milestone:** M18
- **Issue:** [#545](https://github.com/bioedca/tether/issues/545)
- **Builds on:** [ADR-0009](0009-parity-metrics-and-freeze.md) (the metrics and the freeze rule),
  [ADR-0043](0043-per-method-parity-tolerance.md) (per-method rows),
  [ADR-0075](0075-seeded-ebfret-parity-gate.md) (seeded fits),
  [ADR-0090](0090-measured-vbfret-paths-are-a-dwell-reference.md) (no per-trace vbFRET model type
  in the pinned tMAVEN)

## Context and problem statement

PRD §9.6 carried four idealization-parity criteria that the code did not meet:

1. **Per-trace vbFRET (M2).** The frozen default row was measured on consensus `vbconhmm` and is
   `applied_to` per-trace vbFRET, but no test fitted per-trace vbFRET. The pinned tMAVEN
   `10f4230b` has no modeler method for it: its stand-alone `run_vbhmm_modelselection` is
   commented out in that build. Its per-trace step, `tracelevel_vbhmm_modelselection`, is the
   first stage of its "vb GMM + vb HMM" and "kmeans + vb HMM" models.
2. **Transition matrix (M6).** The M6 bar names state levels, transition matrix and ELBO for
   consensus VB-HMM and ebFRET on the ≥ 50-molecule SMD. `parity.py` had no transition-matrix
   term.
3. **The default row on the pinned build (M0.5).** `$.tolerance` was measured on tMAVEN
   `71cfa1af` under CPython 3.9.23, not on the pinned `10f4230b` under CPython 3.12.13 that the
   sidecar, CI and the installers run.
4. **A loosened bound.** The artifact self-checks assert *recorded value within bound*, so a bound
   widened to any other finite value failed nothing.

## Decision drivers

- **No frozen tolerance becomes wider.** A re-measure may keep a bound or tighten it.
- **A frozen row must mean what it measured.** A row whose floors are 0 and whose ceiling is
  most of the FRET range catches nothing, and freezing it would look like evidence while being
  none (the reasoning of ADR-0043, which refused to loosen the shared row for ebFRET).
- **The four-bound rows keep their shape.** Every consumer of `load_frozen_tolerance`
  (`sidecar / parity`, the RF2-ArfA check of ADR-0079, the docs guard) reads four bounds.
- **A re-freeze is a visible, reviewed edit**, never a one-number change in one file.

## Decision

#545 lands in three pull requests, one after the other (maintainer decision of 2026-11-15, for the
issue's size budget). This record carries items 3 and 4, which land first; the transition-matrix
term (item 2) and per-trace vbFRET (item 1) add their own sections here in the pull requests
that implement them.

### 3. The default row is re-frozen on the pinned build, unchanged

`scripts/measure_parity.py --n-runs 20` (no `--cross-seed`: `smd_281mol` anchored on the
committed `model_281mol.hdf5`, as the live gate asserts) on tMAVEN `10f4230b` / CPython 3.12.13,
2026-11-15, recorded by the script's own build probe. Pooled worst cases: state-count and Viterbi
agreement 1.0; per-state ΔE 6.81e-09; relative ELBO 5.88e-10. The freeze rule again returns
`$.provisional`, so **every bound stays**. The artifact's `$.method`, `$.spread_by_fixture` and
`$.pooled_worst` hold the new measurement; `$.refreeze.previous` keeps the build, date and pooled
worst cases of the 2026-06-26 measurement it replaces.

### 4. The frozen bounds are pinned outside the file

`tests/test_parity.py::_ACCEPTED_BOUNDS` is a literal copy of every frozen bound: `$.tolerance`
and each `$.tolerance_by_method` row.
`test_frozen_bounds_match_the_accepted_copy` fails on any difference, and names a widened bound
as `WIDENED`; `test_widening_any_one_frozen_bound_fails` widens each bound alone and checks it is
reported. A deliberate re-freeze changes both copies in one pull request, under an ADR.

## Consequences

- Good: two of the four §9.6 rows close (the re-freeze and the loosened bound). Every frozen number
  of `$.tolerance` is now measured on the build CI runs, and no frozen bound can move silently.
- Neutral: the per-trace vbFRET and transition-matrix rows of §9.6 stay open until their pull
  requests land.

## More information

Code: `schema/parity_tolerance.json` (`$.refreeze`), `tests/test_parity.py` (`_ACCEPTED_BOUNDS`),
`docs/validation.md` §(b), `tests/test_docs_validation_numbers.py`.
