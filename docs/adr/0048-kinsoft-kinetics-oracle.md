# 0048 — kinSoftChallenge kinetics oracle: base-env HMM, 2-state scope, within-spread band

- **Status:** accepted
- **Date:** 2026-07-13
- **Deciders:** bioedca
- **PRD anchor:** §8 NFR-VALID(c), §9 M8, §11.2
- **Milestone:** M8

## Context and problem statement

M8's kinetics-validation oracle (PRD §8 NFR-VALID(c)) must check that Tether infers kinetic rate
constants consistent with the community. The reference is the **kinSoftChallenge** blind benchmark
[Götz2022]: eleven tools (14 analyses) inferred rates from three simulated datasets of increasing
complexity (Fig. 2 = 2-state, Fig. 3 = 3-state non-equilibrium, Fig. 4 = 4-state kinetic
heterogeneity). PR-3a landed the data foundation (reader + the gated LFS `kinsoft_sim.hdf5` +
CC-BY-4.0 plumbing). This PR (PR-3b) builds the fit-and-compare oracle. Three things are undecided:

1. **What idealizes the FRET traces?** Every existing Tether idealizer routes through the isolated
   tMAVEN **sidecar** (`run_vbfret`/`run_vbconhmm`, PyQt5 + `numpy<2`), which needs a second Python
   interpreter and is JIT-dominated (cold `vbconhmm` > 4 min, [[tether-sidecar-test-constraint]]).
2. **Which levels can a FRET-only idealizer honestly handle?** Levels differ fundamentally.
3. **What is the acceptance band**, and how is it made defensible rather than fabricated?

## Decision drivers

- The oracle is **advisory** (never gates `main`) and lives in the gated `large` tier (§9 M8).
- Never fabricate reference data: the ground truth and the band must be sourced + citable (PLAN §1.3).
- Reuse existing machinery (the M6 dwell analysis) rather than re-deriving it.
- Measure-then-freeze, mirroring the M0.5 parity-tolerance freeze (ADR-0009).

## Considered options

- **Idealizer.** (A) A self-contained **base-env Gaussian HMM** (Baum-Welch + Viterbi, numpy only).
  (B) Drive the tMAVEN sidecar. (C) Add an HMM dependency (`hmmlearn`).
- **Scope.** (A) All three levels. (B) The 2-state level only, deferring 3/4-state.
- **Band.** (A) The paper's **reported inter-tool spread** (relative-to-ground-truth). (B) A
  self-consistency band with no ground truth. (C) A hand-picked tolerance.

## Decision outcome

**Idealizer → A (base-env Gaussian HMM).** It keeps the advisory oracle in the base 3-OS test image
(the gated `large` leg) with no sidecar interpreter and no new dependency, and it matches the
benchmark's own recipe — an HMM idealizes the state sequence, from which dwell-time distributions
are compiled and rate constants inferred [Götz2022, Rabiner1989, Bilmes1998]. Baum-Welch/Viterbi are
settled textbook algorithms (no science-gate needed); the init is deterministic (state means at
pooled-signal quantiles), so the fit is reproducible across platforms without a seed. The sidecar
route (B) is rejected as heavyweight for an advisory check and unavailable in the base image; a new
HMM dependency (C) is rejected against the ~100-line self-contained fit.

**Rate estimator → pooled dwell-time MLE** `k = 1/⟨τ⟩` — the maximum-likelihood exit-rate estimator
the benchmark itself uses [Götz2022] — reusing `tether.analysis.dwell.state_dwells` so the
first/last-dwell censoring is identical to the M6 dwell analysis. (It also empirically beat the
transition-matrix conversions: 2.3 %/3.1 % vs 5–8 % deviation on the level-1 data.)

**Scope → B (2-state level 1 active; levels 2 & 3 deferred).** Only the archetypal 2-state system
(Fig. 2) has well-separated FRET states a FRET-only idealizer can faithfully resolve. Level 3
(Fig. 4) is **kinetic heterogeneity**: states 1,2 share the low-FRET level and 3,4 share the high —
a FRET-only idealizer sees two states and *cannot* recover the 4-state kinetics; the benchmark
itself compares this case via cumulative dwell-time distributions, not direct rates. Level 2
(Fig. 3) is a directional non-equilibrium 3-state system with per-trace intensity variability where
even the best tools reach only 9–14 % average deviation. Forcing an oracle on 2/3 would fabricate a
passing (or failing) result; their ground truth is **recorded** in the frozen reference for a future
multi-state / dwell-CDF extension.

**Band → A (the reported inter-tool spread).** For the 2-state dataset the 14 analyses inferred rate
constants within a **maximum of 12 % of ground truth** (5 % average), above a ≥ 3 % (1 σ)
finite-dataset floor. The advisory band is that reported 12 % maximum relative deviation, per rate
constant. Ground truth (k₁₂ = 0.15, k₂₁ = 0.22 s⁻¹) and the spread are frozen in
`schema/kinsoft_reference.json` (single source of truth, mirroring `schema/parity_tolerance.json`)
with full provenance. **Measure-then-freeze:** Tether's own base-env fit was run first and lands at
k₁₂ = 0.1465 (2.3 %) / k₂₁ = 0.2131 (3.1 %) — ~4× inside the band — so the frozen band is calibrated
to a real, passing measurement, not a guess.

### Consequences

- **Good:** the oracle runs in the base image (no sidecar), is deterministic and dependency-free,
  reuses the M6 dwell censoring, and rests on a citable, measure-then-frozen band; schema-guard is
  green (read-only, no `.tether` schema write).
- **Bad / trade-off:** only the 2-state level is validated for now; the base-env HMM is a
  general-purpose idealizer, not the vbFRET/consensus path (fine for an advisory kinetics check).
  The 3/4-state ground truth is recorded but not yet exercised.
- **Follow-up:** a future PR can add a 3-state (level 2) oracle with net-flow handling and a
  dwell-CDF comparison for the 4-state (level 3) case, reusing the same base-env HMM at higher
  `nstates`.

## More information

- **New §11.2 tunable:** the "kinSoftChallenge parity band (M8)" row now carries the frozen band
  (≤ 12 % relative deviation) + ground truth; evidence in `schema/kinsoft_reference.json`.
- Code: `src/tether/analysis/kinetics.py` (`fit_gaussian_hmm`, `viterbi_paths`, `pooled_exit_rates`,
  `two_state_rate_constants`, `load_kinsoft_reference`, `evaluate_kinsoft_level`).
- Tests: `tests/test_kinsoft_kinetics.py` (pure primitives + synthetic recovery + the gated
  `@pytest.mark.large` level-1 within-spread check).
- Data: `tests/fixtures/large/kinsoft_sim.hdf5` (PR-3a, CC-BY-4.0), read via `tether.io.kinsoft`.
- Related: [ADR-0009](0009-parity-metrics-and-freeze.md) (measure-then-freeze a validation
  tolerance), [ADR-0047](0047-deep-model-optional-stack-and-dataset.md) (the M8 deep add-on),
  [Götz2022] the benchmark. Provenance: `tests/fixtures/PROVENANCE.md`.
