<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0072 — The ebFRET parity gate runs on two fixed seeds

- **Status:** accepted
- **Date:** 2026-11-11
- **Deciders:** bioedca
- **PRD anchor:** §7.4 (parity definition), §11.2 (the ebFRET tolerance row), §12.6 (`sidecar/parity`), §9 M6
- **Milestone:** M18
- **Issue:** [#311](https://github.com/bioedca/tether/issues/311)

## Context and problem statement

`tests/test_parity_sidecar.py::test_281mol_ebfret_cross_seed_matches_within_frozen_tolerance`
fits the 281-molecule SMD twice with ebFRET and asserts that the two fits agree within the band
[ADR-0043](0043-per-method-parity-tolerance.md) froze. On #306 it failed on one run and passed on a
re-run of the same commit. The failing run missed all three gated metrics by a wide margin
(state-count agreement 0.4235 against a 0.5249 floor, per-state |ΔE| 0.1612 against a 0.0518
ceiling, Viterbi agreement 0.7568 against a 0.9034 floor). `sidecar / parity` is a required check,
so it had become a check whose failures are re-run until green, which is how a real regression gets
through.

[ADR-0007](0007-parity-is-statistical.md) defined parity as statistical because "tMAVEN
self-reseeds", and it rejected pinning tMAVEN's seed as brittle. #311 asks where the variance comes
from before anything changes.

## Where the variance comes from

The pinned tMAVEN (`10f4230b`, the build every live sidecar assertion runs) has no seed setting.
Its ebFRET path (`run_ebhmm` → `hmm_eb.eb_em_hmm`) draws from **two** random streams, and the caller
seeds neither:

1. **Numba's own generator.** `hmm_eb.eb_outer_loop` is compiled in Numba's `nopython` mode and
   picks each of the 5 restarts' starting means with `np.random.rand`. In compiled code that call
   uses Numba's internal generator. Numba seeds it from operating-system entropy, and a
   `np.random.seed` call made from ordinary Python does not reach it. These draws decide which
   population optimum the empirical-Bayes fit converges to.
2. **NumPy's global generator, re-seeded from entropy per molecule.** After the population fit,
   `eb_em_hmm` runs one per-trace `vb_em_hmm` per molecule. Each one calls
   `fxns.initializations.initialize_gmm`, which calls `np.random.seed()` **with no argument** (a
   fresh draw from operating-system entropy) and then samples the trace's starting means from a
   Gaussian KDE. These draws decide each trace's state selection, which is what
   `state_count_fraction` scores.

`modeler.clip_traces` also calls `np.random.seed(666)` and then re-seeds from the wall clock, but
no draw falls between that re-seed and the first per-trace re-seed in the ebFRET path, so it adds
no variance (the bit-identical runs below confirm it).

So the gate compared two **random** fits. Its result was a draw from the cross-seed distribution of
the metrics, and the band was frozen from 19 such draws. The variance is **seeding**: the run was
never seeded. It is not the environment (the same commit gave both answers on the same runner
image) and not thread scheduling or BLAS order (fitting is single-process, `ncpu = 1`, and
`eb_outer_loop` is compiled with `parallel=False`).

## Decision

1. **The sidecar can seed a fit.** `_sidecar_runner.py` takes an optional leading `--seed N`.
   `seed_random_streams(N)` seeds Numba's generator from compiled code, seeds NumPy's global
   generator, and replaces `np.random.seed` in the child process so that a call with no argument
   re-seeds from the next child of `numpy.random.SeedSequence(N)` instead of from entropy. Calls
   with an explicit seed (`clip_traces`'s `666`) pass through unchanged. tMAVEN's code is not
   patched or copied. The child process runs one fit and exits, so the replacement reaches no other
   code. `run_vbfret(..., seed=N)` (and so `run_vbconhmm` and `run_ebhmm`) passes it through.
   **`seed=None` is the default and keeps tMAVEN's own unseeded behaviour**, which is what the
   standalone GUI does.
2. **The ebFRET gate uses two fixed seeds, 1 and 2** (`EBFRET_SEEDS` in the test module). They were
   chosen before any seeded fit was scored and are never re-chosen to make the gate pass. The test
   still compares two *different* starting points (it is still a cross-seed test), but the
   comparison is now the same on every run of a commit.
3. **The frozen ebFRET band is unchanged.** No bound in `schema/parity_tolerance.json` moves, so
   nothing here needs a re-freeze.
4. **A guard keeps the seeding honest.** `test_4mol_ebfret_same_seed_gives_the_same_model` fits the
   4-molecule fixture twice with one seed and asserts the two model files are equal value for value.
   If a tMAVEN bump adds a random source the sidecar does not seed, that test fails on every run,
   instead of the 281-mol gate quietly going back to a draw. This answers ADR-0007's "brittle"
   objection: the brittleness now fails loudly.

## Evidence

All runs in the cloud `tether-sidecar` environment built from `sidecar/conda-lock.yml` (CPython
3.12.13, NumPy 1.26.4, Numba 0.60.0, tMAVEN `10f4230b`), on one commit of this change.

EVIDENCE_PLACEHOLDER

## Consequences

- `sidecar / parity` gives one answer per commit. A red ebFRET gate is now a change in the fit, not
  a draw, so it must be read, not re-run.
- The gate tests one pair of starting points, not a fresh pair per run. A regression that only
  shows on some seeds can pass it; the old gate could catch such a regression only by chance, and
  its failures were already being re-run away. The cross-seed *distribution* is still what the band
  was frozen from, and re-measuring it stays the `sidecar-measure.yml` recipe of ADR-0043.
- Seeded fits are reproducible on one build and one machine type. A different CPU or a different
  Numba or NumPy build may give different numbers for the same seed; the 4-mol guard runs on CI's
  own runner, so a runner where the seeding does not hold fails there.
- ebFRET's own tail (a rare restart set that converges to a poorer optimum, the cause of the
  observed 0.7568) is a property of tMAVEN with its default 5 restarts. It is recorded here, not
  hidden: unseeded fits, including every fit a user runs from the GUI, still show it.
- This **amends** [ADR-0007](0007-parity-is-statistical.md) on one point: Tether can now pin the
  seed from outside tMAVEN, and the ebFRET gate does. Parity stays statistical (a band, not bit
  equality), and the vbconhmm and vbFRET tests are unchanged.

## Alternatives considered

- **Re-derive the band from more runs.** Rejected as the fix. It would widen the band to cover the
  tail, which blinds the gate to the regressions it exists to catch, and the gate would stay a draw.
  #311 forbids widening the band to make the gate pass.
- **Raise the restart count for the gate.** Rejected. More restarts make a poor optimum rarer but
  not impossible, so the gate stays a draw; the fit would also no longer use tMAVEN's default
  settings, which the band was measured with.
- **Seed only NumPy (`np.random.seed(N)` before the fit).** Rejected: it reaches neither stream.
  Numba's generator is separate, and `initialize_gmm` re-seeds NumPy from entropy at the first
  molecule.
- **Patch tMAVEN.** Rejected: the pinned GPL core stays as upstream ships it (PRD §7.4).
